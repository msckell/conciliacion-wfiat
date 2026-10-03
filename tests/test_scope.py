"""Expected scope of a close, and a package that cannot pass without every applicable pair.

The February 2026 cases replay records of the historical evaluation (tests/fixtures/feb2026):
the old package said all_reconciled with only BNB, and the old engine passed six HyperEVM
contracts that did not exist yet.
"""

import json
from pathlib import Path

import pytest

from cierre import close, supply
from cierre.config import Chain, Deployment, Token, in_scope, load_chains, load_tokens
from cierre.ledger import override_for
from cierre.package import build
from cierre.scope import build_manifest, manifest_from_engine, pair_scope
from cierre.tasks import _existing_issue, marker

FEB = Path(__file__).parent / "fixtures" / "feb2026"
OPEN, CLOSE = "2026-01-31", "2026-02-28"


def _blocks(opening: int | None, closing: int | None) -> list[dict]:
    def b(day, n):
        e = {"cutoff": day, "convention": "ART", "unix": 1, "block": n}
        return e | ({"block_1_timestamp": 99} if n is None else {})

    return [b(OPEN, opening), b(CLOSE, closing)]


def _token(creation: int | None, chain: str = "base", deployed: bool = True) -> Token:
    deps = (Deployment(chain, "0xabc", creation, None),) if deployed else ()
    return Token("wARS", "Peso", 18, None, "confirmed", "0xabc", deps)


def _scope(creation, opening, closing, **kw):
    return pair_scope(
        "wARS", _token(creation, **kw), "base", _blocks(opening, closing), OPEN, CLOSE, "ART"
    )


def test_contract_before_the_opening_is_existing():
    assert _scope(50, 100, 900)["applicability"] == "existing"


def test_contract_created_in_the_interval_starts_at_creation():
    p = _scope(500, 100, 900)
    assert (p["applicability"], p["reason"]) == ("created_in_period", "created_after_opening")
    assert p["evidence"] == {"creation_block": 500, "opening_block": 100, "closing_block": 900}


def test_contract_created_after_the_cutoff_is_not_applicable_with_evidence():
    p = _scope(1000, 100, 900)
    assert (p["applicability"], p["reason"]) == ("not_applicable", "created_after_cutoff")
    assert p["evidence"] == {"creation_block": 1000, "closing_block": 900}


def test_unknown_creation_is_unknown_never_zero():
    assert _scope(None, 100, 900)["applicability"] == "unknown"


def test_network_with_no_block_at_the_cutoff_is_not_applicable():
    p = _scope(5, None, None)
    assert (p["applicability"], p["reason"]) == ("not_applicable", "network_not_live")


def test_network_born_inside_the_interval_is_created_in_period():
    p = _scope(5, None, 900)
    assert (p["applicability"], p["reason"]) == ("created_in_period", "network_not_live_at_opening")


def test_new_token_on_an_old_network():
    # Same network, an old token is existing and a new one is created in the period.
    assert _scope(50, 100, 900)["applicability"] == "existing"
    assert _scope(800, 100, 900)["applicability"] == "created_in_period"


def test_not_deployed_needs_the_discovery_probe_as_evidence():
    tok = _token(None, deployed=False)
    blocks = _blocks(100, 900)
    assert pair_scope("wARS", tok, "base", blocks, OPEN, CLOSE, "ART")["applicability"] == (
        "unknown"
    )
    probe = {"probes": [{"chain": "base", "token": "wARS", "deployed": False, "error": None}]}
    p = pair_scope("wARS", tok, "base", blocks, OPEN, CLOSE, "ART", probe)
    assert (p["applicability"], p["reason"]) == ("not_applicable", "not_deployed")


def test_unread_blocks_make_the_pair_unknown():
    tok = _token(50)
    p = pair_scope("wARS", tok, "base", "ConnectError: boom", OPEN, CLOSE, "ART")
    assert p["applicability"] == "unknown"
    m = build_manifest({"base": None}, {"wARS": tok}, OPEN, CLOSE, "ART", {})
    assert m["counts"] == {"unknown": 1} and m["expected"] == 0


# February 2026, from the recorded evaluation inputs.


def _feb_manifest() -> dict:
    blocks = json.loads((FEB / "cutoff_blocks.json").read_text(encoding="utf-8"))
    return build_manifest(
        in_scope(load_chains()), load_tokens(), OPEN, CLOSE, "ART", blocks, blocks_source="fixture"
    )


def _engine(*chains: str) -> list[dict]:
    return [json.loads((FEB / f"engine-{c}.json").read_text(encoding="utf-8")) for c in chains]


def test_february_scope_has_36_applicable_pairs_from_the_manifest():
    m = _feb_manifest()
    assert m["expected"] == 36
    applicable = {p["chain"] for p in m["pairs"] if p["applicability"] != "not_applicable"}
    assert applicable == {"ethereum", "base", "polygon", "gnosis", "bsc", "worldchain"}
    reasons = {
        p["chain"]: p["reason"] for p in m["pairs"] if p["applicability"] == "not_applicable"
    }
    assert reasons == {
        "celo": "created_after_cutoff",
        "hyperevm": "created_after_cutoff",
        "arc": "network_not_live",
    }


def _package(engine: list[dict], manifest: dict | None) -> dict:
    chains = in_scope(load_chains())
    return build(
        engine,
        chains,
        load_tokens(),
        {"networks_checked": list(chains)},
        {},
        [{"cutoff": "2026-06-30", "network_keys": ["base"]}],
        {},
        [],
        "ART",
        manifest=manifest,
    )


def test_h01_only_bnb_supplied_is_not_a_complete_close():
    # The old package said all_reconciled=true for this input (diagnostic-partial-package.json).
    pkg = _package(_engine("bsc"), _feb_manifest())
    assert pkg["all_reconciled"] is False and pkg["data_complete"] is False
    assert pkg["reconciliation_passed"] is True
    assert pkg["scope"]["counts"] == {"missing": 30, "reconciled": 6, "not_applicable": 18}
    assert all(t["partial"] for t in pkg["tokens"].values())


def test_h01_without_a_saved_scope_missing_networks_are_unknown():
    pkg = _package(_engine("bsc"), None)
    assert pkg["all_reconciled"] is False
    assert pkg["scope"]["counts"]["unknown"] == 48


def test_h02_old_hyperevm_passes_are_ignored_and_not_counted():
    pkg = _package(_engine("hyperevm", "arc", "gnosis", "bsc"), _feb_manifest())
    hyper = [r for r in pkg["scope"]["pairs"] if r["chain"] == "hyperevm"]
    assert {r["status"] for r in hyper} == {"not_applicable"}
    assert all("ignored" in r["detail"] for r in hyper)
    assert not any(r["chain"] in ("hyperevm", "arc") for r in pkg["by_network"])
    excluded = {n["chain"]: n["reasons"] for n in pkg["scope"]["excluded_networks"]}
    assert excluded == {
        "celo": ["created_after_cutoff"],
        "hyperevm": ["created_after_cutoff"],
        "arc": ["network_not_live"],
    }
    # Arc's "network not live" is not an engine error, the four missing networks are.
    assert {e["chain"] for e in pkg["engine_errors"]} == {
        "ethereum",
        "base",
        "polygon",
        "worldchain",
    }


def test_complete_input_passes_and_not_applicable_does_not_block():
    m = _feb_manifest()
    applicable = {p["chain"] for p in m["pairs"] if p["applicability"] != "not_applicable"}
    m["pairs"] = [p for p in m["pairs"] if p["chain"] in ("gnosis", "arc", "hyperevm")]
    m["chains"] = ["gnosis", "arc", "hyperevm"]
    assert applicable >= {"gnosis"}
    pkg = _package(_engine("gnosis", "arc", "hyperevm"), m)
    assert pkg["all_reconciled"] is True
    assert pkg["scope"]["counts"] == {"reconciled": 6, "not_applicable": 12}


def test_error_per_network_removed_token_and_empty_list_never_pass():
    m = _feb_manifest()
    gnosis = _engine("gnosis")[0]
    no_token = json.loads(json.dumps(gnosis))
    del no_token["tokens"]["wARS"]
    cases = {
        "error": [{"chain": "gnosis", "error": "boom"}],
        "removed token": [no_token],
        "empty": [],
    }
    m["pairs"] = [p for p in m["pairs"] if p["chain"] == "gnosis"]
    for name, engine in cases.items():
        pkg = _package(engine, m)
        assert pkg["all_reconciled"] is False, name
        assert pkg["data_complete"] is False, name


def test_h02_engine_does_not_read_logs_for_contracts_created_after_the_cutoff(monkeypatch):
    blocks = json.loads((FEB / "engine-hyperevm.json").read_text(encoding="utf-8"))
    tokens = load_tokens()

    def fake_read_chain(chain, toks, days, cache):
        rows = [
            {
                "token": sym,
                "cutoff": b["cutoff"],
                "convention": b["convention"],
                "block": b["block"],
                "status": "not_created",
                "raw": "0",
            }
            for sym in toks
            for b in blocks["cutoff_blocks"]
        ]
        return {"history": {}, "cutoff_blocks": blocks["cutoff_blocks"], "rows": rows}

    def no_logs(*args, **kwargs):
        raise AssertionError("logs must not be read for pairs out of scope")

    monkeypatch.setattr(supply, "read_chain", fake_read_chain)
    monkeypatch.setattr(close, "collect_sources_multi", no_logs)
    chain = load_chains()["hyperevm"]
    res = close.run_chain(
        chain, tokens, CLOSE, "ART", None, log=lambda s: None, previous_cutoff=OPEN
    )
    assert res["status"] == "not_applicable" and "error" not in res
    assert {t["status"] for t in res["tokens"].values()} == {"not_applicable"}
    assert not any(t["passed"] for t in res["tokens"].values())


def test_engine_rejects_a_window_that_runs_backwards(monkeypatch):
    chain = Chain("base", "Base", 8453, True, ("https://x",), "https://basescan.org")
    tokens = {"wARS": _token(10)}
    cb = [{"cutoff": OPEN, "convention": c, "block": 900, "unix": 1} for c in ("ART", "UTC")] + [
        {"cutoff": CLOSE, "convention": c, "block": 800, "unix": 2} for c in ("ART", "UTC")
    ]
    rows = [
        {
            "token": "wARS",
            "cutoff": b["cutoff"],
            "convention": b["convention"],
            "block": b["block"],
            "status": "ok",
            "raw": "5",
        }
        for b in cb
    ]
    monkeypatch.setattr(
        supply,
        "read_chain",
        lambda *a: {"history": {}, "cutoff_blocks": cb, "rows": rows},
    )
    scope = {"wARS": {"applicability": "existing"}}
    res = close.run_chain(
        chain, tokens, CLOSE, "ART", None, log=lambda s: None, scope=scope, previous_cutoff=OPEN
    )
    assert res["tokens"]["wARS"]["status"] == "error"
    assert "backwards" in res["tokens"]["wARS"]["error"]


# HyperEVM log index: the same movement numbered differently by two nodes.


def _mv(log_index: int, amount: str = "5") -> dict:
    return {
        "chain": "hyperevm",
        "tx_hash": "0xAB",
        "token": "wARS",
        "amount": amount,
        "log_index": log_index,
    }


def test_override_matches_across_log_index():
    fix = {"category": "primary"}
    o = [_mv(12) | {"fix": fix}]
    assert override_for(_mv(13), o, [_mv(13)]) == fix


def test_override_with_twin_movements_needs_the_exact_log_index():
    fix = {"category": "primary"}
    o = [_mv(12) | {"fix": fix}]
    assert override_for(_mv(13), o, [_mv(13), _mv(14)]) is None
    assert override_for(_mv(12), o, [_mv(12), _mv(14)]) == fix


def test_existing_issue_found_with_another_log_index():
    old = _mv(12) | {"tx_hash": "0xab"}
    issue = {"number": 5}
    existing = {marker("2026-09-30", old): issue}
    new = _mv(13) | {"tx_hash": "0xab"}
    assert _existing_issue("2026-09-30", new, existing, [new]) == issue
    twin = _mv(14) | {"tx_hash": "0xab"}
    assert _existing_issue("2026-09-30", new, existing, [new, twin]) is None


@pytest.mark.parametrize("chain", ["gnosis", "bsc"])
def test_old_native_results_keep_their_amounts(chain):
    # Same totals as the old engine output for the networks that did reconcile.
    eng = _engine(chain)[0]
    m = _feb_manifest()
    m["pairs"] = [p for p in m["pairs"] if p["chain"] == chain]
    pkg = _package([eng], m)
    for r in pkg["by_network"]:
        rec = eng["tokens"][r["token"]]["conventions"]["ART"]["by_source"][r["source_used"]]
        assert (r["opening"], r["closing"], r["difference"]) == (
            rec["opening"],
            rec["closing"],
            "0",
        )


def test_manifest_from_engine_needs_a_network_with_cutoffs():
    with pytest.raises(ValueError):
        manifest_from_engine([], in_scope(load_chains()), load_tokens(), "ART")
