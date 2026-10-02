"""Find the last block at or before a timestamp (binary search, cached)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from cierre.cache import DiskCache
from cierre.rpc import RpcClient, block_tag

# A block older than this is final, so its header is cached forever. Every chain in scope
# finalizes in well under an hour, so 6 h leaves a wide margin.
FINALITY_SECONDS = 6 * 3600


@dataclass(frozen=True)
class Block:
    number: int
    timestamp: int
    hash: str


def get_block(rpc: RpcClient, number: int | str) -> Block:
    """Header of a block, by number or by tag ("latest"). Only numbers are cached."""
    by_number = isinstance(number, int)
    key = DiskCache.key(rpc.chain.chain_id, "block_header", number)
    if by_number and rpc.cache is not None:
        hit = rpc.cache.get(key)
        if hit is not None:
            return Block(**hit)
    raw = rpc.call("eth_getBlockByNumber", [block_tag(number), False], require_result=by_number)
    if raw is None:
        raise LookupError(f"{rpc.chain.key}: block {number} not found")
    block = Block(int(raw["number"], 16), int(raw["timestamp"], 16), raw["hash"])
    if by_number and rpc.cache is not None and block.timestamp < time.time() - FINALITY_SECONDS:
        rpc.cache.put(key, asdict(block))
    return block


def block_at_or_before(rpc: RpcClient, ts: int) -> Block:
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
        hours = FINALITY_SECONDS // 3600
        raise ValueError(f"{rpc.chain.key}: cutoff {ts} is less than {hours} h old, not final")

    lo = get_block(rpc, 1)
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
