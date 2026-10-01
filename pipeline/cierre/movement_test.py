"""Phase 0 movement history test: can the free sources build the package?

For one token on one network: opening supply + every mint - every burn must equal the
closing supply, exactly, in base units. Supplies come from totalSupply() on archive nodes
(path A). Mints and burns come from logs (path B). When two log sources exist, they must
also agree with each other, event by event.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace

from cierre import REPO_ROOT
from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.explorer import ExplorerClient, ExplorerError
from cierre.ledger import (
    TRANSFER_TOPIC,
    Movement,
    mints_and_burns_explorer,
    mints_and_burns_rpc,
    parse_log,
)
from cierre.rpc import RpcClient, RpcError


def _bscscan_movements(rpc: RpcClient, chain: Chain, token: Token, lo: int, hi: int):
    listing = json.loads((REPO_ROOT / chain.logs_bscscan_list).read_text(encoding="utf-8"))
    txs = listing["tokens"][token.symbol]["transactions"]
    out: list[Movement] = []
    for tx in txs:
        if not lo <= tx["block"] <= hi:
            continue
        receipt = rpc.call("eth_getTransactionReceipt", [tx["tx_hash"]], require_result=True)
        if int(receipt["status"], 16) != 1:
            continue
        for log in receipt["logs"]:
            if log["address"].lower() != token.address.lower():
                continue
            if not log["topics"] or log["topics"][0].lower() != TRANSFER_TOPIC:
                continue
            m = parse_log(chain.key, log)
            if m is not None:
                out.append(m)
    return sorted(set(out), key=lambda m: (m.block, m.log_index))


def collect_sources(
    chain: Chain,
    token: Token,
    lo: int,
    hi: int,
    cache: DiskCache,
    failed: dict[str, str],
    progress=None,
) -> dict[str, list[Movement]]:
    """Every available log source for mints and burns in [lo, hi]. A source that fails is
    recorded in `failed` and left out, it never counts as "no movements"."""
    sources: dict[str, list[Movement]] = {}
    if chain.logs_rpc_urls:
        name = "rpc:" + ",".join(u.split("//")[1] for u in chain.logs_rpc_urls)
        rpc = RpcClient(replace(chain, rpc_urls=chain.logs_rpc_urls), cache)
        try:
            sources[name] = mints_and_burns_rpc(
                rpc,
                [token.address],
                lo,
                hi,
                chain.logs_grid,
                progress,
                all_transfers=chain.logs_all_transfers,
                workers_per_endpoint=chain.logs_workers_per_endpoint,
            )
        except RpcError as exc:
            failed[name] = str(exc)[:300]
        rpc.close()
    for api in chain.explorer_api:
        if api.kind == "etherscan" and not os.environ.get("ETHERSCAN_API_KEY"):
            continue
        ex = ExplorerClient(chain, api, cache)
        try:
            sources[ex.label] = mints_and_burns_explorer(ex, token.address, lo, hi)
        except ExplorerError as exc:
            failed[ex.label] = str(exc)[:300]
    if chain.logs_bscscan_list:
        rpc = RpcClient(chain, cache)
        sources["bscscan list + RPC receipts"] = _bscscan_movements(rpc, chain, token, lo, hi)
        rpc.close()
    return sources


def compare_sources(sources: dict[str, list[Movement]]) -> dict:
    keys = {name: {(m.key, m.kind, m.amount) for m in ms} for name, ms in sources.items()}
    names = list(keys)
    if len(names) < 2:
        return {"compared": False, "agree": None}
    base = keys[names[0]]
    diffs = {}
    for n in names[1:]:
        if keys[n] != base:
            diffs[n] = {
                f"only_in_{names[0]}": sorted(str(x) for x in base - keys[n])[:20],
                f"only_in_{n}": sorted(str(x) for x in keys[n] - base)[:20],
            }
    return {"compared": True, "agree": not diffs, "diffs": diffs}


def reconcile(opening: int, closing: int, movements: list[Movement], lo: int, hi: int) -> dict:
    inside = [m for m in movements if lo <= m.block <= hi]
    mints = sum(m.amount for m in inside if m.kind == "mint")
    burns = sum(m.amount for m in inside if m.kind == "burn")
    expected = opening + mints - burns
    return {
        "opening": str(opening),
        "mints": str(mints),
        "burns": str(burns),
        "n_mints": sum(1 for m in inside if m.kind == "mint"),
        "n_burns": sum(1 for m in inside if m.kind == "burn"),
        "opening_plus_mints_minus_burns": str(expected),
        "closing": str(closing),
        "difference": str(closing - expected),
        "passed": closing == expected,
    }
