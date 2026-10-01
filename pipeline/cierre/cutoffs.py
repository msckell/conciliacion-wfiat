"""Cutoff instants and the block that represents each one on each network.

The cutoff block is the last block whose timestamp is at or before 23:59:59 of the cutoff
date. Which time zone the certificates use is not documented, so both are computed:
ART (America/Argentina/Buenos_Aires, UTC-3) and UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from cierre.blocks import block_at_or_before, get_block
from cierre.rpc import RpcClient

CONVENTIONS = {
    "ART": ZoneInfo("America/Argentina/Buenos_Aires"),
    "UTC": UTC,
}


def cutoff_instant(day: str, convention: str) -> datetime:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=CONVENTIONS[convention])


def cutoff_block(rpc: RpcClient, day: str, convention: str) -> dict:
    """The cutoff block, or block None with the genesis timestamp as evidence when the
    network did not exist yet at the cutoff."""
    instant = cutoff_instant(day, convention)
    ts = int(instant.timestamp())
    genesis = get_block(rpc, 0)
    first = get_block(rpc, 1)
    if first.timestamp > ts:
        return {
            "cutoff": day,
            "convention": convention,
            "instant_utc": instant.astimezone(UTC).isoformat(),
            "unix": ts,
            "block": None,
            "network_not_live": True,
            "genesis_timestamp": genesis.timestamp,
            "block_1_timestamp": first.timestamp,
        }
    blk = block_at_or_before(rpc, ts)
    nxt = get_block(rpc, blk.number + 1)
    if not (blk.timestamp <= ts < nxt.timestamp):
        raise AssertionError(f"{rpc.chain.key}: block {blk.number} does not bracket {ts}")
    return {
        "cutoff": day,
        "convention": convention,
        "instant_utc": instant.astimezone(UTC).isoformat(),
        "unix": ts,
        "block": blk.number,
        "block_timestamp": blk.timestamp,
        "block_hash": blk.hash,
        "next_block_timestamp": nxt.timestamp,
    }
