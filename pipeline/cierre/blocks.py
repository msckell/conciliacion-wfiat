"""Find the last block at or before a timestamp (binary search, cached)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from cierre.rpc import RpcClient

# A block older than this is treated as final and cached forever. Every chain in scope
# finalizes in well under an hour, so 6 h leaves a wide margin.
FINALITY_SECONDS = 6 * 3600


@dataclass(frozen=True)
class Block:
    number: int
    timestamp: int
    hash: str


def get_block(rpc: RpcClient, number: int | str) -> Block:
    tag = hex(number) if isinstance(number, int) else number
    cacheable = isinstance(number, int)
    if cacheable and rpc.cache is not None:
        hit = rpc.cache.get(rpc.cache.key(rpc.chain.chain_id, "block_header", number))
        if hit is not None:
            return Block(**hit)
    raw = rpc.call("eth_getBlockByNumber", [tag, False], require_result=cacheable)
    if raw is None:
        raise LookupError(f"{rpc.chain.key}: block {number} not found")
    block = Block(int(raw["number"], 16), int(raw["timestamp"], 16), raw["hash"])
    if cacheable and rpc.cache is not None and block.timestamp < time.time() - FINALITY_SECONDS:
        rpc.cache.put(
            rpc.cache.key(rpc.chain.chain_id, "block_header", number),
            {"number": block.number, "timestamp": block.timestamp, "hash": block.hash},
        )
    return block


def block_at_or_before(rpc: RpcClient, ts: int, lo_hint: int = 1) -> Block:
    """Last block whose timestamp is <= ts.

    Interpolation and bisection alternate, so the search converges in log steps even when
    block times are uneven. Timestamps are non decreasing, which is all this needs.
    """
    latest = get_block(rpc, "latest")
    if latest.timestamp <= ts:
        raise ValueError(
            f"{rpc.chain.key}: latest block {latest.number} is not past {ts}, cutoff not final"
        )
    if ts > time.time() - FINALITY_SECONDS:
        raise ValueError(f"{rpc.chain.key}: cutoff {ts} is less than 6 h old, not final")

    lo = get_block(rpc, max(lo_hint, 1))
    if lo.timestamp > ts:
        raise ValueError(f"{rpc.chain.key}: block {lo.number} is already after {ts}")
    hi = get_block(rpc, latest.number)

    step = 0
    while hi.number - lo.number > 1:
        if step % 2 == 0 and hi.timestamp > lo.timestamp:
            frac = (ts - lo.timestamp) / (hi.timestamp - lo.timestamp)
            guess = lo.number + int(frac * (hi.number - lo.number))
        else:
            guess = (lo.number + hi.number) // 2
        guess = min(max(guess, lo.number + 1), hi.number - 1)
        mid = get_block(rpc, guess)
        if mid.timestamp <= ts:
            lo = mid
        else:
            hi = mid
        step += 1
    return lo


def to_unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: the cutoff time zone must be explicit")
    return int(dt.timestamp())
