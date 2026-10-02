"""Offline unit tests for the Phase 0 building blocks."""

from decimal import Decimal

import pytest

from cierre.golden import at_printed_precision, to_units
from cierre.ledger import TRANSFER_TOPIC, ZERO_TOPIC, Movement, parse_log
from cierre.movement_test import compare_sources, reconcile
from cierre.references import network_keys, parse_printed_number

ADDR = "0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d"
HOLDER = "0x" + "0" * 24 + "ab" * 20


@pytest.mark.parametrize(
    "printed, value, decimals, ambiguous",
    [
        ("973,000,960.19", "973000960.19", 2, False),
        ("7,619,996,077", "7619996077", 0, False),
        ("2.586.148", "2586148", 0, False),
        ("2.594.240,11", "2594240.11", 2, False),
        ("719,448", "719448", 0, True),  # resolved later with the document's own convention
        ("104,898,654", "104898654", 0, False),
    ],
)
def test_parse_printed_number(printed, value, decimals, ambiguous):
    n = parse_printed_number(printed)
    assert (n.value, n.decimals, n.ambiguous) == (value, decimals, ambiguous)


def test_network_names_map_to_config_keys():
    names = ["Ethereum", "World Chain", "BNB Smart Chain", "Binance Smart Chain", "CeloScan"]
    assert network_keys(names) == ["ethereum", "worldchain", "bsc", "bsc", "celo"]
    with pytest.raises(ValueError):
        network_keys(["Solana"])


def _log(frm: str, to: str, amount: int, block: int = 10, idx: int = 0) -> dict:
    return {
        "address": ADDR,
        "topics": [TRANSFER_TOPIC, frm, to],
        "data": hex(amount),
        "blockNumber": hex(block),
        "transactionHash": "0x" + "11" * 32,
        "logIndex": hex(idx),
    }


def test_parse_log_mint_burn_and_plain_transfer():
    mint = parse_log("base", _log(ZERO_TOPIC, HOLDER, 5))
    burn = parse_log("base", _log(HOLDER, ZERO_TOPIC, 3, idx=1))
    assert (mint.kind, mint.signed, burn.kind, burn.signed) == ("mint", 5, "burn", -3)
    assert parse_log("base", _log(HOLDER, HOLDER, 7)) is None


def test_reconcile_is_exact_in_base_units():
    ms = [
        Movement("base", ADDR, "mint", 11, "0xa", 0, 10**18 + 1, HOLDER),
        Movement("base", ADDR, "burn", 12, "0xb", 0, 1, HOLDER),
        Movement("base", ADDR, "mint", 99, "0xc", 0, 5, HOLDER),  # outside the window
    ]
    ok = reconcile(100, 100 + 10**18, ms, 11, 20)
    assert ok["passed"] and ok["difference"] == "0"
    off_by_one = reconcile(100, 100 + 10**18 + 1, ms, 11, 20)
    assert not off_by_one["passed"] and off_by_one["difference"] == "1"


def test_compare_sources_flags_a_missing_event():
    a = [Movement("base", ADDR, "mint", 11, "0xa", 0, 5, HOLDER)]
    assert compare_sources({"x": a, "y": list(a)})["agree"] is True
    assert compare_sources({"x": a, "y": []})["agree"] is False
    assert compare_sources({"x": a})["compared"] is False


def test_precision_rules_differ_only_where_they_should():
    v = to_units(973_000_960_195_000_000_000_000_000, 18)  # 973,000,960.195
    assert at_printed_precision(v, 2, "round_half_up") == Decimal("973000960.20")
    assert at_printed_precision(v, 2, "truncate") == Decimal("973000960.19")


def test_whole_units_rule_compares_both_sides_at_zero_places():
    from cierre.golden import compare

    cert = {
        "token": "wARS",
        "cutoff": "2026-03-31",
        "figure": "973000960.19",
        "printed_decimals": 2,
        "network_keys": ["base"],
    }
    raw = str(973000960_2006421207200328 * 10**2)  # 973000960.2006... with 18 decimals
    row = {
        "token": "wARS",
        "cutoff": "2026-03-31",
        "convention": "ART",
        "chain": "base",
        "status": "ok",
        "raw": raw,
    }
    printed = compare([cert], [row], "ART", "all_checked", "round_half_up", "printed")[0]
    whole = compare([cert], [row], "ART", "all_checked", "round_half_up", "whole_units")[0]
    assert (printed["match"], whole["match"], whole["compared_places"]) == (False, True, 0)
