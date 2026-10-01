"""Computed vs certified, in three separate layers.

1. raw sum: totalSupply() summed over the networks checked, in base units
2. adjustments: each documented adjustment, with its source (none documented so far)
3. computed: raw sum + adjustments, shown next to the certified figure

One rule for every certificate: same networks rule, same time convention, same precision
rule. Only figures confirmed by a person (data/golden/certifications.json) are compared.

The official rule compares both sides in whole tokens (OFFICIAL_RULE). The grid still
records every other rule, so the choice stays visible.
"""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

from cierre import DATA_DIR

GOLDEN_PATH = DATA_DIR / "golden" / "certifications.json"

# Documented adjustments only. Each entry needs an external source. Empty on purpose.
ADJUSTMENTS: list[dict] = []

PRECISION_RULES = {"round_half_up": ROUND_HALF_UP, "truncate": ROUND_DOWN}

# Places compared: "printed" uses each certificate's printed precision, "whole_units" uses 0.
PLACES_RULES = ("printed", "whole_units")

OFFICIAL_RULE = {
    "convention": "ART",
    "networks_rule": "all_checked",
    "precision_rule": "round_half_up",
    "places_rule": "whole_units",
}


def load_confirmed(path: Path = GOLDEN_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("confirmed_by") or not data.get("confirmed_at"):
        raise ValueError(f"{path}: not confirmed by a person, refusing to compare")
    return data["certifications"]


def to_units(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


def at_printed_precision(value: Decimal, places: int, rule: str) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=PRECISION_RULES[rule])


def compare(
    certs: list[dict],
    supply_rows: list[dict],
    convention: str,
    networks_rule: str,
    precision_rule: str,
    places_rule: str = "printed",
    decimals: int = 18,
) -> list[dict]:
    """networks_rule: 'all_checked' sums every network checked where the token exists.
    'listed_in_certificate' sums only the networks the certificate names."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in supply_rows:
        if r["convention"] == convention:
            by_key.setdefault((r["token"], r["cutoff"]), []).append(r)

    out = []
    for cert in certs:
        rows = by_key.get((cert["token"], cert["cutoff"]), [])
        if networks_rule == "listed_in_certificate":
            rows = [r for r in rows if r["chain"] in cert["network_keys"]]
        bad = [r for r in rows if r["status"] not in ("ok", "not_created", "network_not_live")]
        raw_sum = sum(int(r["raw"]) for r in rows if r["status"] != "error")
        adjustments = [
            a for a in ADJUSTMENTS if a["token"] == cert["token"] and a["cutoff"] == cert["cutoff"]
        ]
        adj_total = sum(int(a["raw"]) for a in adjustments)
        computed_raw = raw_sum + adj_total
        computed = to_units(computed_raw, decimals)
        certified = Decimal(cert["figure"])
        places = cert["printed_decimals"] if places_rule == "printed" else 0
        shown = at_printed_precision(computed, places, precision_rule)
        certified_cmp = at_printed_precision(certified, places, precision_rule)
        out.append(
            {
                "token": cert["token"],
                "cutoff": cert["cutoff"],
                "certified": str(certified),
                "printed_decimals": cert["printed_decimals"],
                "raw_sum_base_units": str(raw_sum),
                "adjustments": adjustments,
                "computed": str(computed),
                "computed_at_printed_precision": str(shown),
                "difference": str(computed - certified),
                "compared_places": places,
                "match": not bad and shown == certified_cmp,
                "networks_included": sorted(
                    r["chain"] for r in rows if r["status"] == "ok" and int(r["raw"]) > 0
                ),
                "networks_with_errors": [r["chain"] for r in bad],
                "convention": convention,
                "networks_rule": networks_rule,
                "precision_rule": precision_rule,
                "places_rule": places_rule,
            }
        )
    return out
