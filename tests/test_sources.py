"""Log sources: time budget (H04), proven coverage of manual exports (H05), arithmetic kept
apart from an independent check (H06), and keys that never leave the environment.

Every case is synthetic and offline. The clock is fake, so no test waits for real.
"""

import json

import httpx
import pytest

from cierre import ledger
from cierre.config import Chain, ExplorerApi, Token, load_chains, load_tokens, redact
from cierre.explorer import Budget, BudgetExhausted, ExplorerClient, ExplorerError
from cierre.ledger import CoverageError, Movement, check_listing, collect_sources_multi
from cierre.movement_test import compare_sources, reconcile
from cierre.package import _verification, verification_text
from cierre.rpc import RpcClient, RpcError

CHAIN = Chain("base", "Base", 8453, True, ("https://x",), "https://basescan.org")
API = ExplorerApi("blockscout", "https://base.blockscout.com/api")
TOPICS = ["0xddf2", "0x" + "0" * 64]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _client(handler, seconds=30, cache=None) -> tuple[ExplorerClient, Clock]:
    clock = Clock()
    budget = Budget(seconds, clock, clock.sleep)
    client = ExplorerClient(
        CHAIN, API, cache, min_interval=0, budget=budget, transport=httpx.MockTransport(handler)
    )
    return client, clock


def test_reset_header_longer_than_the_budget_stops_at_once():
    def h(req):
        return httpx.Response(429, headers={"x-ratelimit-reset": "600000"})

    client, clock = _client(h)
    with pytest.raises(BudgetExhausted) as err:
        client.get_logs("0xa", TOPICS, 1, 100)
    assert clock.t < 30 and client.budget.requests == 1
    assert "budget 30 s" in str(err.value)


@pytest.mark.parametrize("headers", [{}, {"x-ratelimit-reset": "soon"}])
def test_missing_or_malformed_header_backs_off_within_the_budget(headers):
    client, clock = _client(lambda req: httpx.Response(429, headers=headers))
    with pytest.raises(BudgetExhausted):
        client.get_logs("0xa", TOPICS, 1, 100)
    assert clock.t <= 30 and client.budget.requests >= 2


def test_repeated_timeouts_end_within_the_budget():
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)

    client, clock = _client(h)
    with pytest.raises(BudgetExhausted):
        client.get_logs("0xa", TOPICS, 1, 100)
    assert clock.t <= 30


class MemCache:
    def __init__(self) -> None:
        self.d: dict = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v


def test_answered_ranges_stay_cached_after_a_later_failure():
    cache = MemCache()
    ok = {"status": "1", "message": "OK", "result": []}
    calls = []

    def h(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=ok)

    client, _ = _client(h, cache=cache)
    assert client.get_logs("0xa", TOPICS, 1, 100) == []
    # A source that now only answers 429 still has the answered range.
    blocked, _ = _client(lambda req: httpx.Response(429), cache=cache)
    assert blocked.get_logs("0xa", TOPICS, 1, 100) == []
    assert blocked.budget.requests == 0


def test_an_exhausted_second_source_leaves_the_first_one(monkeypatch):
    chain = Chain(
        "base",
        "Base",
        8453,
        True,
        ("https://x",),
        "https://basescan.org",
        explorer_api=(API,),
        logs_rpc_urls=("https://mainnet.base.org",),
    )

    class Exhausted:
        label = "blockscout:https://base.blockscout.com/api"

        def __init__(self, *a, **k):
            pass

    def exhausted(*a, **k):
        raise BudgetExhausted("time budget exhausted: budget 300 s, 12 requests, 290 s waiting")

    monkeypatch.setattr(ledger, "ExplorerClient", Exhausted)
    monkeypatch.setattr(ledger, "mints_and_burns_explorer", exhausted)
    monkeypatch.setattr(ledger, "mints_and_burns_rpc", lambda *a, **k: [])
    tok = load_tokens()["wARS"]
    failed: dict = {}
    sources = collect_sources_multi(chain, [tok], 1, 100, None, failed)
    assert list(sources) == ["rpc:mainnet.base.org"]
    assert "budget 300 s" in failed["blockscout:https://base.blockscout.com/api"]


# H05: a manual export counts only with proven coverage.


def _tok(sym: str) -> Token:
    return Token(sym, sym, 18, None, "confirmed", "0x" + sym, ())


def _listing(**over) -> dict:
    base = {
        "covers_from_block": 0,
        "covers_to_block": 1000,
        "tokens": {
            "wARS": {
                "total_shown": 2,
                "transactions": [{"tx_hash": "0xA", "block": 10}, {"tx_hash": "0xB", "block": 20}],
            },
        },
    }
    return base | over


def test_listing_inside_its_coverage_is_accepted():
    assert len(check_listing(_listing(), [_tok("wARS")], 5, 900)) == 2


@pytest.mark.parametrize(
    "listing, why",
    [
        (_listing(covers_to_block=500), "another period"),
        ({"tokens": _listing()["tokens"]}, "no coverage stated"),
        (
            _listing(
                tokens={
                    "wARS": {"total_shown": 3, "transactions": [{"tx_hash": "0xA", "block": 10}]}
                }
            ),
            "truncated pages",
        ),
        (_listing(tokens={}), "token missing"),
        (
            _listing(
                tokens={
                    "wARS": {
                        "total_shown": 2,
                        "transactions": [
                            {"tx_hash": "0xA", "block": 10},
                            {"tx_hash": "0xa", "block": 11},
                        ],
                    }
                }
            ),
            "conflicting duplicate",
        ),
        ({"tokens": {"wARS": {"total_shown": 0, "transactions": []}}}, "empty, no coverage"),
    ],
)
def test_listing_without_proof_is_refused(listing, why):
    with pytest.raises(CoverageError):
        check_listing(listing, [_tok("wARS")], 5, 900)


def test_identical_duplicate_counts_once_and_an_empty_covered_list_is_valid():
    dup = _listing(
        tokens={
            "wARS": {
                "total_shown": 2,
                "transactions": [{"tx_hash": "0xA", "block": 10}, {"tx_hash": "0xa", "block": 10}],
            }
        }
    )
    assert len(check_listing(dup, [_tok("wARS")], 5, 900)) == 1
    empty = _listing(tokens={"wARS": {"total_shown": 0, "transactions": []}})
    assert check_listing(empty, [_tok("wARS")], 5, 900) == []


def test_the_recorded_bscscan_list_covers_september_and_february():
    from cierre import REPO_ROOT

    chain = load_chains()["bsc"]
    listing = json.loads((REPO_ROOT / chain.logs_bscscan_list).read_text(encoding="utf-8"))
    tokens = list(load_tokens().values())
    assert len(check_listing(listing, tokens, 107345137, 125024742)) == 72
    assert check_listing(listing, tokens, 82186272, 83973244)  # covers February too


def test_missing_receipt_fails_the_source(monkeypatch, tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps(_listing()), encoding="utf-8")
    chain = Chain(
        "bsc", "BNB", 56, True, ("https://x",), "https://bscscan.com", logs_bscscan_list=str(path)
    )

    class NoReceipts:
        def __init__(self, *a, **k):
            pass

        def call(self, *a, **k):
            raise RpcError("bsc", "eth_getTransactionReceipt", ["https://x: null result"])

        def close(self):
            pass

    monkeypatch.setattr(ledger, "RpcClient", NoReceipts)
    failed: dict = {}
    assert collect_sources_multi(chain, [_tok("wARS")], 5, 900, None, failed) == {}
    assert "null result" in failed["bscscan list + RPC receipts"]


# H06: arithmetic is not the same as a complete history.


def _m(tx: str, kind: str, amount: int, log_index: int = 0) -> Movement:
    return Movement("base", "0xa", kind, 50, tx, log_index, amount, "0xb")


def test_missing_mint_and_burn_of_the_same_amount_still_reconcile_but_disagree():
    # Synthetic adversarial case: source B misses a mint and a burn that cancel out.
    full = [_m("0x1", "mint", 7), _m("0x2", "burn", 7), _m("0x3", "mint", 5)]
    partial = [_m("0x3", "mint", 5)]
    assert reconcile(10, 15, full, 1, 100)["passed"]
    assert reconcile(10, 15, partial, 1, 100)["passed"]
    cmp = compare_sources({"rpc:a": full, "blockscout:https://b/api": partial})
    assert cmp["agree"] is False
    t = {
        "source_comparison": cmp,
        "sources_complete": ["blockscout:https://b/api", "rpc:a"],
        "sources_incomplete": [],
    }
    v = _verification(t, {})
    assert v["status"] == "discrepancy"
    assert verification_text(v) == "Dos fuentes concilian pero no listan los mismos movimientos."


def test_a_second_source_that_does_not_reconcile_is_incomplete_not_a_discrepancy():
    # Base, 30/09: Blockscout returned no movements, so its list does not reconcile. It
    # cannot confirm or contradict the RPC logs, which reconcile by two paths.
    t = {
        "source_comparison": compare_sources({"rpc:a": [_m("0x1", "mint", 7)]}),
        "source_comparison_all": {"compared": True, "agree": False, "diffs": {"x": {}}},
        "sources_complete": ["rpc:a"],
        "sources_incomplete": ["blockscout:https://base.blockscout.com/api"],
    }
    v = _verification(t, {})
    assert v["status"] == "limited"
    assert verification_text(v) == (
        "Una sola fuente: base.blockscout.com devolvió una lista incompleta."
    )


def test_log_index_does_not_count_as_a_difference_but_the_amount_does():
    a = [_m("0x1", "mint", 7, log_index=12)]
    assert compare_sources({"a": a, "b": [_m("0x1", "mint", 7, log_index=13)]})["agree"]
    assert not compare_sources({"a": a, "b": [_m("0x1", "mint", 8, log_index=12)]})["agree"]


def test_one_source_is_limited_and_says_which_one_failed():
    t = {
        "source_comparison": {"compared": False},
        "sources_complete": ["rpc:a"],
        "sources_incomplete": [],
    }
    v = _verification(
        t, {"sources_failed": {"blockscout:https://gnosis.blockscout.com/api": "301"}}
    )
    assert v["status"] == "limited"
    assert verification_text(v) == "Una sola fuente: no respondió gnosis.blockscout.com."
    assert _verification(t, {})["status"] == "limited"


# Keys from the environment.


def test_key_placeholder_is_filled_from_env_and_never_printed(monkeypatch, tmp_path):
    yaml_text = """chains:
  bsc:
    name: BNB
    chain_id: 56
    in_scope: true
    rpc_urls: [https://bsc-dataseed.bnbchain.org]
    logs_rpc: {urls: ["https://bsc-mainnet.nodereal.io/v1/${NODEREAL_API_KEY}"], grid: 50000}
    explorer: https://bscscan.com
"""
    cfg = tmp_path / "chains.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")
    monkeypatch.delenv("NODEREAL_API_KEY", raising=False)
    assert load_chains(cfg)["bsc"].logs_rpc_urls == ()
    monkeypatch.setenv("NODEREAL_API_KEY", "s3cr3tkey123")
    chain = load_chains(cfg)["bsc"]
    assert chain.logs_rpc_urls == ("https://bsc-mainnet.nodereal.io/v1/s3cr3tkey123",)
    assert "s3cr3tkey123" not in redact(chain.logs_rpc_urls[0])

    from dataclasses import replace

    rpc = RpcClient(replace(chain, rpc_urls=chain.logs_rpc_urls), retries=1)

    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(rpc.http, "post", boom)
    with pytest.raises(RpcError) as err:
        rpc.call("eth_blockNumber", [])
    assert "s3cr3tkey123" not in str(err.value) and "***" in str(err.value)


def test_a_redirect_fails_at_once_and_is_not_followed():
    def h(req):
        return httpx.Response(301, headers={"location": "https://gnosisscan.io/api?x=1"})

    client, clock = _client(h)
    with pytest.raises(ExplorerError) as err:
        client.get_logs("0xa", TOPICS, 1, 100)
    assert "redirect to gnosisscan.io" in str(err.value)
    assert client.budget.requests == 1 and clock.t == 0


def test_a_second_source_without_its_free_key_says_so(monkeypatch):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("NODEREAL_API_KEY", raising=False)
    monkeypatch.setattr(ledger, "_bscscan_movements", lambda *a: [])
    failed: dict = {}
    collect_sources_multi(load_chains()["bsc"], [_tok("wARS")], 1, 2, None, failed)
    assert failed == {
        "rpc:bsc-mainnet.nodereal.io/v1/***": "not configured: NODEREAL_API_KEY is not set"
    }
    t = {
        "source_comparison": {"compared": False},
        "sources_complete": ["bscscan list"],
        "sources_incomplete": [],
    }
    text = verification_text(_verification(t, {"sources_failed": failed}))
    assert text == (
        "Una sola fuente: la segunda (bsc-mainnet.nodereal.io) necesita una key gratuita "
        "que no está cargada."
    )


def test_an_empty_range_is_empty_in_both_explorer_dialects_but_other_errors_fail():
    # Etherscan answers a range with no events as status 0, "No records found", [].
    for message in ("No records found", "No logs found"):
        client, _ = _client(
            lambda req, m=message: httpx.Response(
                200, json={"status": "0", "message": m, "result": []}
            )
        )
        assert client.get_logs("0xa", TOPICS, 1, 100) == []
    client, _ = _client(
        lambda req: httpx.Response(
            200, json={"status": "0", "message": "NOTOK", "result": "Invalid API Key"}
        )
    )
    with pytest.raises(ExplorerError):
        client.get_logs("0xa", TOPICS, 1, 100)
