"""Quarter close engine: supply at both cutoffs, every mint and burn in between, and the
exact reconciliation per token and network.

Two independent paths per token and network: totalSupply() at each cutoff block (archive
state) and the mint and burn logs in between. Every movement that changes supply goes in,
bridge movements included. A source counts as complete only if it reconciles exactly.
"""

from __future__ import annotations

import concurrent.futures as cf
from datetime import date

from cierre import supply
from cierre.abi import keccak256
from cierre.blocks import get_block
from cierre.cache import DiskCache
from cierre.classify import classify_movements, load_contracts, mark_redemptions, receipt_client
from cierre.config import Chain, Token
from cierre.cutoffs import CONVENTIONS
from cierre.ledger import Movement, collect_sources_multi
from cierre.movement_test import compare_sources, reconcile
from cierre.rpc import RpcClient

QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


def last_quarter_end(today: date) -> str:
    """The most recent quarter end strictly before `today` (the scheduled close runs the
    day after)."""
    for year in (today.year, today.year - 1):
        for m, dd in reversed(QUARTER_ENDS):
            if date(year, m, dd) < today:
                return date(year, m, dd).isoformat()
    raise AssertionError("unreachable")


def previous_quarter_end(day: str) -> str:
    d = date.fromisoformat(day)
    if (d.month, d.day) not in QUARTER_ENDS:
        raise ValueError(f"{day} is not a quarter end")
    i = QUARTER_ENDS.index((d.month, d.day))
    if i == 0:
        return date(d.year - 1, 12, 31).isoformat()
    m, dd = QUARTER_ENDS[i - 1]
    return date(d.year, m, dd).isoformat()


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


def run_chain(
    chain: Chain,
    tokens: dict[str, Token],
    cutoff: str,
    convention: str,
    cache: DiskCache,
    log=lambda s: print(s, flush=True),
) -> dict:
    prev = previous_quarter_end(cutoff)
    s = supply.read_chain(chain, tokens, [prev, cutoff], cache)
    rows = {(r["token"], r["cutoff"], r["convention"]): r for r in s["rows"]}
    blocks = {(b["cutoff"], b["convention"]): b for b in s["cutoff_blocks"]}
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
    if blocks[(cutoff, convention)]["block"] is None:
        out["error"] = "network not live at the cutoff"
        return out

    # Window over both conventions, so the close can say whether the convention matters.
    windows: dict[str, dict[str, tuple]] = {}
    errors: dict[str, str] = {}
    for sym, tok in tokens.items():
        dep = tok.on(chain.key)
        creation = dep.creation_block if dep else None
        windows[sym] = {}
        for conv in CONVENTIONS:
            o = _opening(rows[(sym, prev, conv)], creation)
            c = rows[(sym, cutoff, conv)]
            if isinstance(o, str):
                errors[sym] = o
                break
            if c["status"] == "not_created":
                windows[sym][conv] = (*o, c["block"], 0, c.get("reads", {}))
            elif c["status"] != "ok":
                errors[sym] = f"closing supply {c['status']}: {c.get('error', '')}"
                break
            else:
                windows[sym][conv] = (*o, c["block"], int(c["raw"]), c.get("reads", {}))
    live = {sym: w for sym, w in windows.items() if sym not in errors}
    if not live:
        out["error"] = "no token has both supplies"
        out["token_errors"] = errors
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

    for sym, tok in tokens.items():
        if sym in errors:
            out["tokens"][sym] = {"passed": False, "error": errors[sym]}
            continue
        addr = tok.address.lower()
        mine = {n: [m for m in ms if m.token_address == addr] for n, ms in sources.items()}
        res: dict = {"conventions": {}, "source_comparison": compare_sources(mine)}
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
        if complete:
            chosen = sorted(complete)[0]
            ob, _, _, cb, _, _ = live[sym][convention]
            res["source_used"] = chosen
            res["movements"] = [
                _movement_row(m, sym) for m in mine[chosen] if ob + 1 <= m.block <= cb
            ]
            # Movements that fall between the two conventions' cutoff blocks of this close.
            art, utc = live[sym]["ART"][3], live[sym]["UTC"][3]
            res["movements_between_conventions"] = [
                _movement_row(m, sym)
                for m in mine[chosen]
                if min(art, utc) < m.block <= max(art, utc)
            ]
        out["tokens"][sym] = res
        first = res["conventions"][convention]["by_source"]
        log(
            f"{chain.key:10} {sym:5} {'PASSED' if res['passed'] else 'FAILED'} "
            f"complete={res['sources_complete']} "
            + " ".join(f"{n.split(':')[0]}:diff={r['difference']}" for n, r in first.items())
        )
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


def run_close(
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    cutoff: str,
    convention: str,
    cache: DiskCache,
) -> list[dict]:
    def one(key: str) -> dict:
        chain = chains[key]
        try:
            res = run_chain(chain, tokens, cutoff, convention, cache)
            enrich(chain, res, cache)
            return res
        except Exception as exc:  # one network failing must not hide the others
            print(f"{key:10} ERROR {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            return {"chain": key, "error": f"{type(exc).__name__}: {exc}"[:1000]}

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
    for chain_key in {k.chain for k in known}:
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
