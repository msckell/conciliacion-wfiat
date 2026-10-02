"""Pair bridge burns with bridge mints across networks, exactly, through the bridge events.

BridgeMintFulfilled carries (sourceChainId, sourceTxHash, sourceDepositId), so each mint
points at one burn. Nothing here uses time windows or amounts to guess a pair: amounts
and recipients are only checked after the pair is found by its key.

Results per bridge movement:
- matched: both sides inside the quarter.
- in_flight_at_cutoff: burned before the cutoff, minted after it (or not yet). The mint
  side is searched after the cutoff on the destination network.
- in_flight_at_previous_cutoff: minted in the quarter for a burn before the previous
  cutoff. The burn is read from its own transaction on the source network.
"""

from __future__ import annotations

from dataclasses import replace

from cierre.abi import BRIDGE_MINT_FULFILLED, BridgeIn, BridgeOut, decode
from cierre.cache import DiskCache
from cierre.classify import KnownContract, receipt_client
from cierre.config import Chain
from cierre.explorer import ExplorerClient, ExplorerError
from cierre.ledger import RpcLogFetcher
from cierre.rpc import RpcClient, RpcError


def _topic_uint(n: int) -> str:
    return "0x" + format(n, "064x")


def _topic_addr(a: str) -> str:
    return "0x" + "0" * 24 + a.lower()[2:]


def _bridge_address(known: list[KnownContract], chain: str) -> str | None:
    hits = [k.address for k in known if k.chain == chain and k.role == "bridge_deposit"]
    return hits[0] if len(hits) == 1 else None


def _side(chain_result: dict, movement: dict) -> dict:
    return {
        "chain": chain_result["chain"],
        "tx_hash": movement["tx_hash"],
        "block": movement["block"],
    }


def find_fulfillment_after(
    chain: Chain,
    bridge_address: str,
    token: str,
    source_chain_id: int,
    source_tx: str,
    deposit_id: int,
    from_block: int,
    cache: DiskCache,
) -> dict:
    """Search the destination network after its cutoff for the mint of one deposit.
    Returns status found, not_found (searched up to a block) or not_searched (no source)."""
    topics = [BRIDGE_MINT_FULFILLED, _topic_addr(token), None, _topic_uint(deposit_id)]
    rpc = RpcClient(chain, cache)
    try:
        head = int(rpc.call("eth_blockNumber", []), 16)
    finally:
        rpc.close()
    logs = None
    errors = []
    if chain.logs_rpc_urls:
        lrpc = RpcClient(replace(chain, rpc_urls=chain.logs_rpc_urls), None)
        try:
            fetcher = RpcLogFetcher(lrpc, chain.logs_grid, chain.logs_workers_per_endpoint)
            flt = {"address": [bridge_address], "topics": topics}
            logs = fetcher.fetch(flt, from_block, head)
        except RpcError as exc:
            errors.append(str(exc)[:200])
        finally:
            lrpc.close()
    if logs is None:
        for api in chain.explorer_api:
            if api.kind != "blockscout":
                continue
            try:
                ex = ExplorerClient(chain, api, None)
                logs = ex.get_logs(bridge_address, topics, from_block, head)
                break
            except ExplorerError as exc:
                errors.append(str(exc)[:200])
    if logs is None:
        return {"status": "not_searched", "searched_to": None, "errors": errors}
    for lg in logs:
        ev = decode(lg)
        if (
            isinstance(ev, BridgeIn)
            and ev.source_chain_id == source_chain_id
            and ev.source_tx_hash.lower()[-64:] == source_tx.lower()[-64:]
            and ev.source_deposit_id == deposit_id
        ):
            return {
                "status": "found",
                "tx_hash": lg["transactionHash"].lower(),
                "block": int(lg["blockNumber"], 16),
                "amount": str(ev.amount),
                "explorer_url": chain.tx_url(lg["transactionHash"].lower()),
                "searched_to": head,
            }
    return {"status": "not_found", "searched_to": head}


def find_source_burn(chain: Chain, source_tx: str, deposit_id: int, cache: DiskCache) -> dict:
    """Read the source transaction of a bridge mint and return its BridgeDepositInitiated."""
    rpc = receipt_client(chain, cache)
    try:
        r = rpc.call("eth_getTransactionReceipt", [source_tx], cache=True, require_result=True)
    except RpcError as exc:
        return {"status": "not_read", "error": str(exc)[:200]}
    finally:
        rpc.close()
    for lg in r["logs"]:
        ev = decode(lg)
        if isinstance(ev, BridgeOut) and ev.deposit_id == deposit_id:
            return {
                "status": "found",
                "block": int(r["blockNumber"], 16),
                "amount": str(ev.amount),
                "sender": ev.sender,
                "explorer_url": chain.tx_url(source_tx),
            }
    return {"status": "no_deposit_event"}


def match(
    engine: list[dict],
    chains: dict[str, Chain],
    known: list[KnownContract],
    cache: DiskCache,
) -> dict:
    """Adds a `bridge` dict to every bridge movement (in place) and returns the summary."""
    by_id = {c.chain_id: k for k, c in chains.items()}
    cut = {}
    for c in engine:
        for b in c.get("cutoff_blocks", []):
            if b["convention"] == c.get("convention"):
                cut[(c["chain"], b["cutoff"])] = b["block"]
    outs, ins = {}, {}
    for c in engine:
        for t in c.get("tokens", {}).values():
            for m in t.get("movements", []):
                ev = m.get("evidence") or {}
                if m.get("category") == "bridge_out":
                    outs[(c["chain"], m["tx_hash"], int(ev["deposit_id"]))] = (c, m)
                elif m.get("category") == "bridge_in":
                    src = by_id.get(int(ev["source_chain_id"]))
                    key = (src, "0x" + ev["source_tx_hash"][-64:], int(ev["source_deposit_id"]))
                    ins[key] = (c, m)

    delays = []
    for key, (c, m) in outs.items():
        ev = m["evidence"]
        hit = ins.get(key)
        if hit is not None:
            c2, m2 = hit
            ok = m2["amount"] == m["amount"] and m2["counterparty"] == ev["dest_recipient"]
            status = "matched" if ok else "mismatch"
            delay = m2["timestamp"] - m["timestamp"]
            delays.append(delay)
            m["bridge"] = {"status": status, "other_side": _side(c2, m2), "delay_seconds": delay}
            m2["bridge"] = {"status": status, "other_side": _side(c, m), "delay_seconds": delay}
            continue
        dest = by_id.get(int(ev["dest_chain_id"]))
        if dest is None:
            m["bridge"] = {"status": "dest_not_checked", "dest_chain_id": ev["dest_chain_id"]}
            continue
        bridge_addr = _bridge_address(known, dest)
        if bridge_addr is None:
            m["bridge"] = {"status": "dest_bridge_unknown", "dest_chain": dest}
            continue
        after = find_fulfillment_after(
            chains[dest],
            bridge_addr,
            ev["token"],
            chains[c["chain"]].chain_id,
            m["tx_hash"],
            int(ev["deposit_id"]),
            cut[(dest, c["cutoff"])] + 1,
            cache,
        )
        m["bridge"] = {"status": "in_flight_at_cutoff", "dest_chain": dest, "after": after}

    for key, (c, m) in ins.items():
        if "bridge" in m:
            continue
        src, src_tx, dep = key
        if src is None:
            ev = m["evidence"]
            m["bridge"] = {"status": "source_not_checked", "source_chain_id": ev["source_chain_id"]}
            continue
        burn = find_source_burn(chains[src], src_tx, dep, cache)
        prev_block = cut.get((src, c["previous_cutoff"]))
        before_prev = (
            burn.get("status") == "found" and prev_block is not None and burn["block"] <= prev_block
        )
        m["bridge"] = {
            "status": "in_flight_at_previous_cutoff" if before_prev else "source_unexplained",
            "source_chain": src,
            "source_tx_hash": src_tx,
            "source": burn,
            "source_previous_cutoff_block": prev_block,
        }
    return {
        "pairs_matched": len(delays),
        "delay_seconds_max": max(delays) if delays else None,
        "delay_seconds_median": sorted(delays)[len(delays) // 2] if delays else None,
    }
