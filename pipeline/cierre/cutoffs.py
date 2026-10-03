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


def opening_for(cutoff: str, opening: str | None = None) -> str:
    """The opening cutoff of a close: the one given, or the previous quarter end. An explicit
    opening can be any date before the cutoff (a month, a week), the scheduled close keeps
    the quarter."""
    if opening is None:
        return previous_quarter_end(cutoff)
    if date.fromisoformat(opening) >= date.fromisoformat(cutoff):
        raise ValueError(f"opening {opening} is not before cutoff {cutoff}")
    return opening


def is_quarter(opening: str, cutoff: str) -> bool:
    """Whether opening and cutoff are two consecutive quarter ends."""
    try:
        return previous_quarter_end(cutoff) == opening
    except ValueError:
        return False


def period(opening: str, cutoff: str) -> dict:
    """How a close names its interval: a quarter (2026Q3) or an explicit interval."""
    if is_quarter(opening, cutoff):
        y, m, _ = cutoff.split("-")
        return {"kind": "quarter", "id": f"{y}Q{(int(m) - 1) // 3 + 1}", "word": "trimestre"}
    return {"kind": "interval", "id": f"{opening}_{cutoff}", "word": "período"}
