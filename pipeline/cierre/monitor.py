"""Daily monitor over the written candidate list.

1. supply: totalSupply of each token on each network in scope, at the current head
2. deployments: whether each token has code on every candidate network, two endpoints
   agreeing, compared with config/tokens.yaml
3. golden_live: the certified cutoffs recomputed from the network (cache skipped for the
   supply reads) with the official rule, against the confirmed table
4. ci: conclusion of the last CI run on main (GitHub API, only when a token is present)

Writes data/monitor/latest.json and appends data/monitor/history.jsonl. Slack only when
something changed against the previous run or a check failed. A failed read is recorded
as a failure, never as a zero.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
from datetime import UTC, datetime

import httpx

from cierre import DATA_DIR
from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.discover import has_code
from cierre.rpc import RpcClient
from cierre.supply import SEL_TOTAL_SUPPLY

MONITOR_DIR = DATA_DIR / "monitor"
LATEST = MONITOR_DIR / "latest.json"
HISTORY = MONITOR_DIR / "history.jsonl"


def read_supply(chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache) -> dict:
    def one(chain: Chain) -> tuple[str, dict]:
        rpc = RpcClient(chain, cache)
        try:
            head = int(rpc.call("eth_blockNumber", []), 16)
            out = {}
            for sym, tok in tokens.items():
                if tok.on(chain.key) is None:
                    continue
                raw = rpc.call(
                    "eth_call", [{"to": tok.address, "data": SEL_TOTAL_SUPPLY}, hex(head)]
                )
                if not isinstance(raw, str) or raw in ("0x", ""):
                    raise ValueError(f"empty totalSupply for {sym}")
                out[sym] = str(int(raw, 16))
            return chain.key, {"block": head, "tokens": out}
        except Exception as exc:  # recorded per network, never a zero
            return chain.key, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            rpc.close()

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        return dict(ex.map(one, chains.values()))


def check_deployments(
    chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache
) -> dict[str, dict[str, str]]:
    """{chain: {token: 'deployed' | 'absent' | 'error: ...'}} for every candidate network."""

    def one(chain: Chain) -> tuple[str, dict]:
        rpc = RpcClient(chain, cache)
        out = {}
        try:
            for sym, tok in tokens.items():
                try:
                    deployed, _ = has_code(rpc, tok.address)
                    out[sym] = "deployed" if deployed else "absent"
                except Exception as exc:
                    out[sym] = f"error: {type(exc).__name__}: {str(exc)[:160]}"
        finally:
            rpc.close()
        return chain.key, out

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(one, chains.values()))


def golden_live(chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache) -> dict:
    from cierre.golden import OFFICIAL_RULE, compare, load_confirmed
    from cierre.supply import read_chain

    certs = load_confirmed()
    days = sorted({c["cutoff"] for c in certs})
    conv = (OFFICIAL_RULE["convention"],)

    def one(chain: Chain) -> list[dict]:
        return read_chain(chain, tokens, days, cache, conventions=conv, live=True)["rows"]

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        rows = [r for chain_rows in ex.map(one, chains.values()) for r in chain_rows]
    result = compare(certs, rows, **OFFICIAL_RULE)
    errors = sorted({n for r in result for n in r["networks_with_errors"]})
    # One failed row per network, with what each endpoint answered, to diagnose.
    detail = {}
    for r in rows:
        if r["status"] == "error" and r["chain"] not in detail:
            detail[r["chain"]] = {
                k: (v[:200] if isinstance(v, str) else v)
                for k, v in (r.get("reads") or {"error": r.get("error")}).items()
            }
    return {
        "matched": sum(r["match"] for r in result),
        "total": len(result),
        "read_errors": errors,
        "read_error_detail": detail,
        # A mismatch with every read fine could mean a published figure is wrong: rule 10.
        "mismatch_without_errors": [
            f"{r['token']} {r['cutoff']}"
            for r in result
            if not r["match"] and not r["networks_with_errors"]
        ],
    }


def ci_status() -> dict | None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/actions/workflows/ci.yml/runs",
        params={"branch": "main", "per_page": 5},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    resp.raise_for_status()
    done = [r for r in resp.json()["workflow_runs"] if r["status"] == "completed"]
    if not done:
        return {"conclusion": None, "at": None, "sha": None}
    r = done[0]
    return {"conclusion": r["conclusion"], "at": r["updated_at"], "sha": r["head_sha"][:7]}


def diff(previous: dict | None, current: dict, names: dict[str, str]) -> list[str]:
    """What changed since the last run, in Spanish, for Slack. Daily supply movements are
    normal activity and are not listed: they stay in the history file."""
    changes = []
    old = (previous or {}).get("deployments", {})
    for chain, toks in current["deployments"].items():
        for sym, state in toks.items():
            before = old.get(chain, {}).get(sym)
            if before is None or before.startswith("error") or state.startswith("error"):
                continue
            if before != state:
                verb = "apareció" if state == "deployed" else "desapareció"
                changes.append(f"El contrato de {sym} {verb} en {names[chain]}.")
    prev_g, g = (previous or {}).get("golden_live"), current["golden_live"]
    if prev_g and (prev_g["matched"], prev_g["total"]) != (g["matched"], g["total"]):
        changes.append(
            f"Chequeo contra las certificaciones: antes {prev_g['matched']} de "
            f"{prev_g['total']}, ahora {g['matched']} de {g['total']}."
        )
    if previous and previous.get("problems") and not current["problems"]:
        changes.append("Los chequeos que fallaban en la corrida anterior volvieron a pasar.")
    return changes


def problems_of(current: dict, tokens: dict[str, Token], names: dict[str, str]) -> list[str]:
    out = []
    for chain, s in current["supply"].items():
        if "error" in s:
            out.append(f"No se pudo leer la cantidad de tokens en {names[chain]}.")
    for chain, toks in current["deployments"].items():
        errs = [sym for sym, st in toks.items() if st.startswith("error")]
        if errs:
            out.append(f"No se pudo revisar {', '.join(errs)} en {names[chain]}.")
        for sym, st in toks.items():
            expected = tokens[sym].on(chain) is not None
            if st == "deployed" and not expected:
                out.append(
                    f"{sym} tiene contrato en {names[chain]} y esa red no está en la lista del "
                    f"cierre. Hay que sumarla antes del próximo cierre."
                )
            if st == "absent" and expected:
                out.append(f"No se encontró el contrato de {sym} en {names[chain]}.")
    g = current["golden_live"]
    if g["read_errors"]:
        out.append(
            "El chequeo contra las certificaciones no pudo leer "
            + ", ".join(names[c] for c in g["read_errors"])
            + "."
        )
    return out


def totals(supply: dict) -> dict[str, str]:
    acc: dict[str, int] = {}
    for s in supply.values():
        for sym, raw in s.get("tokens", {}).items():
            acc[sym] = acc.get(sym, 0) + int(raw)
    return {k: str(v) for k, v in sorted(acc.items())}


def write(current: dict) -> None:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    line = {
        "checked_at": current["checked_at"],
        "status": current["status"],
        "totals": current["totals"],
        "golden_live": f"{current['golden_live']['matched']}/{current['golden_live']['total']}",
        "changes": current["changes"],
        "problems": current["problems"],
    }
    with open(HISTORY, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_previous() -> dict | None:
    return json.loads(LATEST.read_text(encoding="utf-8")) if LATEST.exists() else None


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
