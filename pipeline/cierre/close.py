"""Close engine: supply at the opening and closing cutoffs (consecutive quarter ends by
default, or an explicit interval), every mint and burn in between, and the exact
reconciliation per token and network.

Two independent paths per token and network: totalSupply() at each cutoff block (archive
state) and the mint and burn logs in between. Every movement that changes supply goes in,
bridge movements included. A source counts as complete only if it reconciles exactly.
"""

from __future__ import annotations

import concurrent.futures as cf

from cierre import supply
from cierre.abi import keccak256
from cierre.blocks import get_block
from cierre.cache import DiskCache
from cierre.classify import classify_movements, load_contracts, mark_redemptions, receipt_client
from cierre.config import Chain, Token, redact
from cierre.cutoffs import CONVENTIONS, opening_for
from cierre.ledger import Movement, collect_sources_multi
from cierre.movement_test import compare_sources, reconcile
from cierre.rpc import RpcClient
from cierre.scope import APPLICABLE, build_manifest, pair_scope, read_cutoff_blocks


def _opening(row: dict, dep_creation: int | None) -> tuple[int, int, str] | str:
    """(block before the window, opening supply, basis) or an error string."""
    if row["status"] == "ok":
        return row["block"], int(row["raw"]), "totalSupply at the previous cutoff block"
    if row["status"] in ("not_created", "network_not_live"):
        if dep_creation is None:
            return "no creation block on record"
        return (
            dep_creation - 1,
            0,
            f"contract created at block {dep_creation}, after the previous cutoff",
        )
    return f"opening supply {row['status']}: {row.get('error', '')}"


def _movement_row(m: Movement, symbol: str) -> dict:
    return {
        "token": symbol,
        "chain": m.chain,
        "kind": m.kind,
        "block": m.block,
        "tx_hash": m.tx_hash,
        "log_index": m.log_index,
        "amount": str(m.amount),
        "counterparty": m.counterparty,
    }


def _skipped(pair: dict) -> dict:
    """Token result for a pair the scope says the close cannot reconcile: not applicable
    (with its evidence) or unknown. Neither counts as reconciled."""
    status = pair["applicability"]
    res = {"passed": False, "status": status, "reason": pair.get("reason")}
    if status == "unknown":
        res["error"] = f"scope unknown: {pair.get('reason')}"
    if pair.get("evidence"):
        res["evidence"] = pair["evidence"]
    return res


def run_chain(
    chain: Chain,
    tokens: dict[str, Token],
    cutoff: str,
    convention: str,
    cache: DiskCache,
    log=lambda s: print(s, flush=True),
    scope: dict[str, dict] | None = None,
    previous_cutoff: str | None = None,
) -> dict:
    """Supplies, movements and reconciliation for every token on one network.

    `scope` maps each token to its pair in the expected scope (see scope.py). Without it the
    applicability is computed here from the same cutoff blocks. Pairs that are not
    applicable or unknown get no log query and never count as reconciled."""
    prev = opening_for(cutoff, previous_cutoff)
    s = supply.read_chain(chain, tokens, [prev, cutoff], cache)
    rows = {(r["token"], r["cutoff"], r["convention"]): r for r in s["rows"]}
    blocks = {(b["cutoff"], b["convention"]): b for b in s["cutoff_blocks"]}
    if scope is None:
        scope = {
            sym: pair_scope(sym, tok, chain.key, s["cutoff_blocks"], prev, cutoff, convention)
            for sym, tok in tokens.items()
        }
    out: dict = {
        "chain": chain.key,
        "name": chain.name,
        "previous_cutoff": prev,
        "cutoff": cutoff,
        "convention": convention,
        "history_endpoints": s["history"],
        "cutoff_blocks": s["cutoff_blocks"],
        "tokens": {},
    }
    applicable = [sym for sym in tokens if scope[sym]["applicability"] in APPLICABLE]
    for sym in tokens:
        if sym not in applicable:
            out["tokens"][sym] = _skipped(scope[sym])
    if not applicable:
        if all(scope[sym]["applicability"] == "not_applicable" for sym in tokens):
            out["status"] = "not_applicable"
        else:
            out["error"] = "no token on this network has a known scope"
        return out
    if blocks[(cutoff, convention)]["block"] is None:
        out["error"] = "network not live at the cutoff, but the scope says applicable"
        return out

    # Window over both conventions, so the close can say whether the convention matters. A
    # convention whose closing block is before the creation block is left out of the
    # comparison: its window would run backwards.
    windows: dict[str, dict[str, tuple]] = {}
    skipped_conventions: dict[str, dict[str, str]] = {}
    errors: dict[str, str] = {}
    for sym in applicable:
        dep = tokens[sym].on(chain.key)
        creation = dep.creation_block if dep else None
        windows[sym] = {}
        for conv in CONVENTIONS:
            c = rows[(sym, cutoff, conv)]
            if c["status"] in ("not_created", "network_not_live"):
                if conv == convention:
                    errors[sym] = f"closing supply {c['status']}, but the scope says applicable"
                    break
                skipped_conventions.setdefault(sym, {})[conv] = (
                    "contract created after this convention's closing block"
                )
                continue
            if c["status"] != "ok":
                errors[sym] = f"closing supply {c['status']}: {c.get('error', '')}"
                break
            o = _opening(rows[(sym, prev, conv)], creation)
            if isinstance(o, str):
                errors[sym] = o
                break
            if o[0] >= c["block"]:
                errors[sym] = f"window runs backwards: opening block {o[0]}, closing {c['block']}"
                break
            windows[sym][conv] = (*o, c["block"], int(c["raw"]), c.get("reads", {}))
    live = {sym: w for sym, w in windows.items() if sym not in errors}
    for sym, err in errors.items():
        out["tokens"][sym] = {"passed": False, "status": "error", "error": err}
    if not live:
        out["error"] = "no token has both supplies"
        out["token_errors"] = errors
        out["tokens"] = {sym: out["tokens"][sym] for sym in tokens}
        return out
    lo = min(w[0] for ws in live.values() for w in ws.values()) + 1
    hi = max(w[3] for ws in live.values() for w in ws.values())
    log(f"{chain.key:10} logs {lo}..{hi} ({hi - lo + 1} blocks)")
    failed: dict[str, str] = {}
    sources = collect_sources_multi(
        chain,
        [tokens[s] for s in live],
        lo,
        hi,
        cache,
        failed,
        progress=lambda n, t: log(f"{chain.key:10} {n}/{t} cells"),
    )
    out |= {
        "range": [lo, hi],
        "sources": {n: len(ms) for n, ms in sources.items()},
        "sources_failed": failed,
    }

    art_block, utc_block = blocks[(cutoff, "ART")]["block"], blocks[(cutoff, "UTC")]["block"]
    for sym in live:
        tok = tokens[sym]
        addr = tok.address.lower()
        mine = {n: [m for m in ms if m.token_address == addr] for n, ms in sources.items()}
        res: dict = {"conventions": {}, "source_comparison": compare_sources(mine)}
        if sym in skipped_conventions:
            res["conventions_skipped"] = skipped_conventions[sym]
        complete = set(mine)
        for conv, (ob, opening, basis, cb, closing, reads) in live[sym].items():
            per_source = {n: reconcile(opening, closing, ms, ob + 1, cb) for n, ms in mine.items()}
            complete &= {n for n, r in per_source.items() if r["passed"]}
            res["conventions"][conv] = {
                "opening_block": ob,
                "opening": str(opening),
                "opening_basis": basis,
                "closing_block": cb,
                "closing": str(closing),
                "closing_reads": reads,
                "by_source": per_source,
            }
        res["sources_complete"] = sorted(complete)
        res["sources_incomplete"] = sorted(set(mine) - complete)
        res["passed"] = bool(complete)
        res["status"] = "reconciled" if complete else "not_reconciled"
        if complete:
            chosen = sorted(complete)[0]
            ob, _, _, cb, _, _ = live[sym][convention]
            res["source_used"] = chosen
            res["movements"] = [
                _movement_row(m, sym) for m in mine[chosen] if ob + 1 <= m.block <= cb
            ]
            # Movements that fall between the two conventions' cutoff blocks of this close.
            res["movements_between_conventions"] = [
                _movement_row(m, sym)
                for m in mine[chosen]
                if min(art_block, utc_block) < m.block <= max(art_block, utc_block)
            ]
        out["tokens"][sym] = res
        first = res["conventions"][convention]["by_source"]
        log(
            f"{chain.key:10} {sym:5} {'PASSED' if res['passed'] else 'FAILED'} "
            f"complete={res['sources_complete']} "
            + " ".join(f"{n.split(':')[0]}:diff={r['difference']}" for n, r in first.items())
        )
    out["tokens"] = {sym: out["tokens"][sym] for sym in tokens}
    for name, err in failed.items():
        log(f"{chain.key:10} source failed {name}: {err}")
    return out


def enrich(chain: Chain, chain_result: dict, cache: DiskCache) -> None:
    """Add timestamp, transaction sender, target and selector to each movement. These are
    the inputs of the classification. Results are cached, the blocks are final."""
    rpc = RpcClient(chain, cache)
    try:
        for tok in chain_result.get("tokens", {}).values():
            for m in tok.get("movements", []) + tok.get("movements_between_conventions", []):
                tx = rpc.call(
                    "eth_getTransactionByHash", [m["tx_hash"]], cache=True, require_result=True
                )
                m["tx_from"] = tx["from"].lower()
                m["tx_to"] = (tx.get("to") or "").lower()
                m["selector"] = (tx.get("input") or "0x")[:10]
                m["timestamp"] = get_block(rpc, m["block"]).timestamp
                m["explorer_url"] = chain.tx_url(m["tx_hash"])
    finally:
        rpc.close()


def expected_scope(
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    cutoff: str,
    convention: str,
    cache: DiskCache,
    discovery: dict | None = None,
    previous_cutoff: str | None = None,
) -> dict:
    """The expected scope of the close, from the configuration and the cutoff blocks, before
    any log is read."""
    prev = opening_for(cutoff, previous_cutoff)
    blocks = read_cutoff_blocks(chains, [prev, cutoff], cache)
    return build_manifest(chains, tokens, prev, cutoff, convention, blocks, discovery)


def run_close(
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    cutoff: str,
    convention: str,
    cache: DiskCache,
    manifest: dict | None = None,
) -> list[dict]:
    def one(key: str) -> dict:
        chain = chains[key]
        scope = prev = None
        if manifest is not None:
            scope = {p["token"]: p for p in manifest["pairs"] if p["chain"] == key}
            prev = manifest["previous_cutoff"]
        try:
            res = run_chain(
                chain, tokens, cutoff, convention, cache, scope=scope, previous_cutoff=prev
            )
            enrich(chain, res, cache)
            return res
        except Exception as exc:  # one network failing must not hide the others
            print(f"{key:10} ERROR {type(exc).__name__}: {redact(str(exc))[:300]}", flush=True)
            return {"chain": key, "error": redact(f"{type(exc).__name__}: {exc}")[:1000]}

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        return list(ex.map(one, list(chains)))


def classify_all(
    engine: list[dict], chains: dict[str, Chain], tokens: dict[str, Token], cache: DiskCache
) -> dict:
    """Receipts for every movement, then the classification. A listed contract whose code
    hash changed is dropped for that network, so its events classify nothing."""
    known = load_contracts()
    report: dict = {"contracts_checked": [], "contracts_dropped": []}
    usable = []
    for chain_key in dict.fromkeys(k.chain for k in known):
        if chain_key not in chains:
            continue
        rpc = RpcClient(chains[chain_key], cache)
        try:
            for k in [k for k in known if k.chain == chain_key]:
                code = rpc.call("eth_getCode", [k.address, "latest"])
                h = "0x" + keccak256(bytes.fromhex(code[2:])).hex() if code else None
                entry = {"chain": chain_key, "role": k.role, "address": k.address}
                if h == k.code_hash:
                    usable.append(k)
                    report["contracts_checked"].append(entry)
                else:
                    report["contracts_dropped"].append(entry | {"code_hash_now": h})
        finally:
            rpc.close()
    for c in engine:
        if "tokens" not in c:
            continue
        rpc = receipt_client(chains[c["chain"]], cache)
        try:
            for sym, t in c["tokens"].items():
                ms = t.get("movements", [])
                receipts = {
                    tx: rpc.call("eth_getTransactionReceipt", [tx], cache=True, require_result=True)
                    for tx in {m["tx_hash"] for m in ms}
                }
                classify_movements(ms, receipts, usable, c["chain"], tokens[sym].address)
        finally:
            rpc.close()
        state = RpcClient(chains[c["chain"]], cache)
        try:
            state.qualify_history(tokens[supply.HISTORY_PROBE_SYMBOL].address)
            for t in c["tokens"].values():
                mark_redemptions(t.get("movements", []), state, usable, c["chain"])
        finally:
            state.close()
    report["known"] = usable
    return report
