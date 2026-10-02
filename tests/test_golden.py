"""Golden tests: the official rule reproduces every published certified figure, from the
recorded totalSupply reads (data/phase0/supply_raw.json) and the table a person confirmed
(data/golden/certifications.json). Offline."""

import json

from cierre import DATA_DIR
from cierre.golden import OFFICIAL_RULE, compare, load_confirmed


def _rows():
    chains = json.loads((DATA_DIR / "phase0" / "supply_raw.json").read_text(encoding="utf-8"))
    return [r for chain in chains for r in chain["rows"]]


def test_official_rule_matches_every_certificate():
    certs = load_confirmed()
    rows = compare(certs, _rows(), **OFFICIAL_RULE)
    assert len(rows) == len(certs) == 9
    assert [f"{r['token']} {r['cutoff']}" for r in rows if not r["match"]] == []
    assert all(not r["networks_with_errors"] for r in rows)
    assert all(r["adjustments"] == [] for r in rows)  # no undocumented exclusions


def test_a_missing_network_breaks_the_match():
    certs = load_confirmed()
    rows = [r for r in _rows() if not (r["chain"] == "base" and r["token"] == "wARS")]
    out = {(r["token"], r["cutoff"]): r["match"] for r in compare(certs, rows, **OFFICIAL_RULE)}
    assert out[("wARS", "2026-06-30")] is False
