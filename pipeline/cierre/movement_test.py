"""Movement history check: opening supply + every mint - every burn == closing supply.

Exact, in base units, for one token on one network. Supplies come from totalSupply() on
archive nodes, mints and burns from logs. When two log sources exist, they must also agree
with each other, event by event.
"""

from __future__ import annotations

from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.ledger import Movement, collect_sources_multi


def collect_sources(
    chain: Chain,
    token: Token,
    lo: int,
    hi: int,
    cache: DiskCache,
    failed: dict[str, str],
    progress=None,
) -> dict[str, list[Movement]]:
    return collect_sources_multi(chain, [token], lo, hi, cache, failed, progress)


def compare_sources(sources: dict[str, list[Movement]]) -> dict:
    """Do all log sources hold the same movements? Differences are listed against the first."""
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
    """Reconcile `closing` against `opening` plus the movements in blocks [lo, hi]."""
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
