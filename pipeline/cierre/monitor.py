"""Daily monitor over the written candidate list of networks.

Each run reads the supply of every token on every network in scope, checks which candidate
networks hold a deployment, recomputes the certified cutoffs from the network (the live
golden check), warms the log cache for the running quarter and reads the CI status. It
writes data/monitor and the run log. Slack gets a message only when something changed or a
check failed. A failed read is recorded as a failure, not as a zero.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
from dataclasses import replace
from datetime import date

import httpx

from cierre import DATA_DIR
from cierre.alerts import mark_sent, send_failure
from cierre.cache import DiskCache
from cierre.close import last_quarter_end
from cierre.config import Chain, Token, load_chains, load_tokens
from cierre.cutoffs import cutoff_block
from cierre.discover import has_code
from cierre.gitops import commit_and_push
from cierre.golden import OFFICIAL_RULE, compare, load_confirmed
from cierre.jsonio import dump_json, load_json_if_exists
from cierre.ledger import mints_and_burns_rpc
from cierre.rpc import RpcClient
from cierre.runlog import RUNS_PATH, Run, now
from cierre.site import publish_run_log, write_site
from cierre.slack import alert_message, monitor_message, send
from cierre.supply import SEL_TOTAL_SUPPLY, read_chain

MONITOR_DIR = DATA_DIR / "monitor"
LATEST = MONITOR_DIR / "latest.json"
HISTORY = MONITOR_DIR / "history.jsonl"
PROBE_WORKERS = 8  # networks probed in parallel for deployments


def read_supply(chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache) -> dict:
    """totalSupply of each token on each network at its current head, in base units."""

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
        except Exception as exc:  # recorded per network, not as a zero
            return chain.key, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            rpc.close()

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        return dict(ex.map(one, chains.values()))


def check_deployments(
    chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache
) -> dict[str, dict[str, str]]:
    """{chain: {token: 'deployed' | 'absent' | 'error: ...'}} for every candidate network.
    Two endpoints must agree before a contract counts as deployed or absent."""

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

    with cf.ThreadPoolExecutor(max_workers=PROBE_WORKERS) as ex:
        return dict(ex.map(one, chains.values()))


def golden_live(chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache) -> dict:
    """The certified cutoffs recomputed from the network with the official rule. The supply
    reads skip the cache, so a changed archive answer shows up."""
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
        # A mismatch while every read worked could mean a published figure is wrong.
        "mismatch_without_errors": [
            f"{r['token']} {r['cutoff']}"
            for r in result
            if not r["match"] and not r["networks_with_errors"]
        ],
    }


def prefetch_logs(
    chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache, today: date
) -> dict:
    """Read the mint and burn logs of the running quarter into the disk cache, so the close
    does not have to ask free nodes for millions of blocks in one go (a GitHub runner
    stalled partway through HyperEVM's range).

    Same filter and grid as the close engine, so its cells hit the same cache keys. Only
    whole cells that end well behind the head are read: a cached answer lives forever, and
    blocks near the head can still be reorganized. Nothing here is used as data: the
    close reads and reconciles everything again."""
    start_day = last_quarter_end(today)

    def one(chain: Chain) -> tuple[str, dict]:
        if not chain.logs_rpc_urls:
            return chain.key, {"skipped": "no logs RPC (explorer or list source)"}
        live = [t for t in tokens.values() if t.on(chain.key) is not None]
        rpc = RpcClient(chain, cache)
        try:
            head = int(rpc.call("eth_blockNumber", []), 16)
            lo = cutoff_block(rpc, start_day, "UTC")["block"]  # the earlier convention
        except Exception as exc:
            return chain.key, {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        finally:
            rpc.close()
        grid = chain.logs_grid
        hi = ((head - 2 * grid) // grid) * grid - 1
        if lo is None or hi <= lo:
            return chain.key, {"cells": 0}
        lrpc = RpcClient(replace(chain, rpc_urls=chain.logs_rpc_urls), cache)
        try:
            mints_and_burns_rpc(
                lrpc,
                [t.address for t in live],
                lo,
                hi,
                grid,
                None,
                all_transfers=chain.logs_all_transfers,
                workers_per_endpoint=chain.logs_workers_per_endpoint,
            )
            return chain.key, {"from": lo, "to": hi, "cells": (hi - lo) // grid + 1}
        except Exception as exc:
            return chain.key, {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        finally:
            lrpc.close()

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        return dict(ex.map(one, chains.values()))


def ci_status() -> dict | None:
    """Conclusion of the latest finished CI run on main. None without GITHUB_TOKEN and
    GITHUB_REPOSITORY, which is the case in local runs."""
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
    """Supply per token summed over the networks that were read, in base units."""
    acc: dict[str, int] = {}
    for s in supply.values():
        for sym, raw in s.get("tokens", {}).items():
            acc[sym] = acc.get(sym, 0) + int(raw)
    return {k: str(v) for k, v in sorted(acc.items())}


def write(current: dict) -> None:
    """Save the run as latest.json and append a short line to the history."""
    dump_json(LATEST, current)
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
    return load_json_if_exists(LATEST)


def run(push: bool) -> int:
    """One monitor run, logged step by step. Returns the exit code: 0 when every check
    passed. With `push`, commits the data (and, after a failure, only the run log)."""
    all_chains = load_chains()
    in_scope = {k: c for k, c in all_chains.items() if c.in_scope}
    names = {k: c.name for k, c in all_chains.items()}
    tokens = load_tokens()
    cache = DiskCache()
    previous = read_previous()
    run_log = Run("monitor")
    current = {"checked_at": now(), "networks_checked": list(all_chains)}
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with run_log.step("supply") as s:
            current["supply"] = read_supply(in_scope, tokens, cache)
            current["totals"] = totals(current["supply"])
            s["counts"] = {
                "networks": len(in_scope),
                "networks_failed": sum("error" in v for v in current["supply"].values()),
            }
        with run_log.step("deployments") as s:
            current["deployments"] = check_deployments(all_chains, tokens, cache)
            states = [st for t in current["deployments"].values() for st in t.values()]
            s["counts"] = {
                "networks_checked": len(all_chains),
                "deployed": states.count("deployed"),
                "errors": sum(st.startswith("error") for st in states),
            }
        with run_log.step("golden_live") as s:
            current["golden_live"] = golden_live(in_scope, tokens, cache)
            g = current["golden_live"]
            if g["mismatch_without_errors"]:
                # A figure that may contradict a published certificate is not published:
                # alert privately and commit nothing.
                s["counts"] = {"result": "held for review"}
                raise RuntimeError(
                    "live check differs from a certified figure with every read fine: "
                    + ", ".join(g["mismatch_without_errors"])
                )
            s["counts"] = {"matched": g["matched"], "total": g["total"]}
        with run_log.step("prefetch") as s:
            # A warm up for the close: a failure here is recorded, not alerted.
            current["prefetch"] = prefetch_logs(in_scope, tokens, cache, date.today())
            s["counts"] = {
                "cells": sum(v.get("cells", 0) for v in current["prefetch"].values()),
                "networks_failed": [k for k, v in current["prefetch"].items() if "error" in v],
            }
        with run_log.step("ci") as s:
            try:
                current["ci"] = ci_status()
            except httpx.HTTPError as exc:
                current["ci"] = {"error": f"{type(exc).__name__}"}
            s["status"] = "ok" if current["ci"] is not None else "skipped"
            s["counts"] = {"conclusion": (current["ci"] or {}).get("conclusion")}
        current["problems"] = problems_of(current, tokens, names)
        current["changes"] = diff(previous, current, names)
        current["status"] = "failed" if current["problems"] else "ok"
        with run_log.step("notify") as s:
            if current["changes"] or current["problems"]:
                msg = monitor_message(current["changes"], current["problems"], len(all_chains))
                result = send(msg, MONITOR_DIR / "slack_payload.json")
                s["status"] = "dry_run" if result.startswith("dry run") else "ok"
                if current["problems"]:
                    mark_sent()
            else:
                s["status"] = "skipped"
            s["counts"] = {
                "changes": len(current["changes"]),
                "problems": len(current["problems"]),
            }
            current["notified"] = s["status"]
        write(current)
    except Exception as exc:
        run_log.finish("failed")
        failed = run_log.last_step
        send_failure(
            alert_message(failed, str(exc), "El monitor"),
            MONITOR_DIR / "slack_alert_payload.json",
        )
        print(f"monitor failed at {failed}: {exc}", flush=True)
        # A failed live check may mean a published figure is wrong: publish nothing then.
        if push and failed != "golden_live":
            publish_run_log("monitor: failed run")
        return 1
    run_log.finish(current["status"])
    print(
        f"monitor {current['status']}: {len(current['changes'])} changes, "
        f"{len(current['problems'])} problems",
        flush=True,
    )
    if push:
        site_json = write_site()
        commit_and_push(
            [MONITOR_DIR, RUNS_PATH, site_json],
            f"monitor: {current['status']} {current['checked_at'][:10]}",
        )
    return 0 if current["status"] == "ok" else 1
