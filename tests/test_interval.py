"""Explicit close intervals next to the default quarter (H03 of the February evaluation)."""

import pytest

from cierre import cli, close_run
from cierre.agent.memo import token_facts
from cierre.cutoffs import is_quarter, opening_for, period
from cierre.ledger import Movement
from cierre.movement_test import reconcile


def test_default_opening_is_the_previous_quarter_end():
    assert opening_for("2026-09-30") == "2026-06-30"
    assert opening_for("2026-03-31") == "2025-12-31"
    with pytest.raises(ValueError):
        opening_for("2026-02-28")  # not a quarter end and no opening given


def test_explicit_interval_any_dates_in_order():
    assert opening_for("2026-02-28", "2026-01-31") == "2026-01-31"
    assert opening_for("2026-02-15", "2026-02-01") == "2026-02-01"  # not month ends
    for bad in ("2026-02-28", "2026-03-01"):
        with pytest.raises(ValueError):
            opening_for("2026-02-28", bad)


def test_cli_rejects_a_reversed_interval_before_any_network_call(monkeypatch):
    monkeypatch.setattr(close_run, "refresh_engine", lambda *a: pytest.fail("ran the engine"))
    with pytest.raises(ValueError):
        cli.main(["close", "--cutoff", "2026-02-28", "--opening", "2026-03-01"])


def test_period_names_the_quarter_or_the_interval():
    assert is_quarter("2026-06-30", "2026-09-30")
    assert not is_quarter("2026-01-31", "2026-02-28")
    assert period("2026-06-30", "2026-09-30") == {
        "kind": "quarter",
        "id": "2026Q3",
        "word": "trimestre",
    }
    assert period("2026-01-31", "2026-02-28")["id"] == "2026-01-31_2026-02-28"


def test_close_dir_keeps_the_quarter_path_and_separates_intervals():
    assert close_run.close_dir("2026-09-30").name == "2026-09-30"
    assert close_run.close_dir("2026-09-30", "2026-06-30").name == "2026-09-30"
    assert close_run.close_dir("2026-03-31", "2026-02-28").name == "2026-02-28_2026-03-31"


def test_window_excludes_the_opening_block_and_includes_the_closing_one():
    def mv(block: int, amount: int) -> Movement:
        return Movement("base", "0xa", "mint", block, f"0x{block}", 0, amount, "0xb")

    # Opening supply at block 100 already holds the mint of block 100.
    r = reconcile(5, 12, [mv(100, 5), mv(101, 3), mv(200, 4), mv(201, 9)], 101, 200)
    assert (r["mints"], r["difference"], r["passed"]) == ("7", "0", True)


def test_memo_facts_name_the_interval_and_never_say_quarter():
    cat = {
        k: {"amount": "0", "count": 0}
        for k in ("primary", "redemption", "bridge_in", "bridge_out", "unclassified")
    }
    pkg = {
        "cutoff": "2026-02-28",
        "previous_cutoff": "2026-01-31",
        "period": period("2026-01-31", "2026-02-28"),
        "review": [],
        "tokens": {
            "wARS": {
                "opening": "1",
                "closing": "2",
                "change": "1",
                "mints": "1",
                "burns": "0",
                "by_category": cat,
                "networks_with_balance": ["base"],
            }
        },
    }
    facts = token_facts(pkg, "wARS")
    assert "wARS.mints.2026-01-31_2026-02-28" in facts
    assert not any("trimestre" in f["meaning"] for f in facts.values())
