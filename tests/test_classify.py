"""Classification from receipt events, offline with hand built receipts."""

from cierre.abi import BRIDGE_DEPOSIT_INITIATED, BRIDGE_MINT_FULFILLED, LIMITED_MINTER_MINTED
from cierre.classify import MINTER_ROLE, KnownContract, classify_movements, mark_direct_mints

TOKEN = "0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d"
BRIDGE = "0x465e642387d3d73a57cdc1368ffa53a800ba5d47"
MINTER = "0xd168cfbbe260d48cd119497a9a2ee8482080c5e7"
USER = "0x" + "ab" * 20
KNOWN = [
    KnownContract("base", "bridge_deposit", BRIDGE, "0x0", "test"),
    KnownContract("base", "limited_minter", MINTER, "0x0", "test"),
]


def _t(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _u(n: int) -> str:
    return format(n, "064x")


def _log(emitter: str, topics: list[str], words: list[int], idx: int) -> dict:
    return {
        "address": emitter,
        "topics": topics,
        "data": "0x" + "".join(_u(w) for w in words),
        "logIndex": hex(idx),
    }


def _bridge_out(amount: int, sender: str = USER, emitter: str = BRIDGE, idx: int = 5) -> dict:
    topics = [BRIDGE_DEPOSIT_INITIATED, "0x" + _u(7), _t(TOKEN), _t(sender)]
    return _log(emitter, topics, [amount, 0, 480, int(USER, 16), 0], idx)


def _bridge_in(amount: int, to: str = USER, idx: int = 6) -> dict:
    topics = [BRIDGE_MINT_FULFILLED, _t(TOKEN), _t(to), "0x" + _u(9)]
    return _log(BRIDGE, topics, [amount, 1, 0xBEEF], idx)


def _minted(amount: int, dest: str = USER, emitter: str = MINTER, idx: int = 4) -> dict:
    topics = [LIMITED_MINTER_MINTED, _t(TOKEN), _t("0x" + "11" * 20), _t(dest)]
    return _log(emitter, topics, [amount], idx)


def _mv(kind: str, amount: int, tx: str = "0xaa", idx: int = 3) -> dict:
    return {
        "kind": kind,
        "amount": str(amount),
        "counterparty": USER,
        "tx_hash": tx,
        "block": 1,
        "log_index": idx,
    }


def _run(movements: list[dict], logs: list[dict]) -> list[str]:
    classify_movements(movements, {"0xaa": {"logs": logs}}, KNOWN, "base", TOKEN)
    return [m["category"] for m in movements]


def test_bridge_out_and_in():
    assert _run([_mv("burn", 10)], [_bridge_out(10)]) == ["bridge_out"]
    assert _run([_mv("mint", 10)], [_minted(10), _bridge_in(10)]) == ["bridge_in"]


def test_primary_mint_needs_known_minter():
    assert _run([_mv("mint", 10)], [_minted(10)]) == ["primary"]
    other = "0x" + "22" * 20
    assert _run([_mv("mint", 10)], [_minted(10, emitter=other)]) == ["unclassified"]


def test_unknown_bridge_emitter_or_other_amount_stays_unclassified():
    other = "0x" + "33" * 20
    assert _run([_mv("burn", 10)], [_bridge_out(10, emitter=other)]) == ["unclassified"]
    assert _run([_mv("burn", 10)], [_bridge_out(11)]) == ["unclassified"]
    assert _run([_mv("burn", 10)], []) == ["unclassified"]


def test_one_event_classifies_one_movement_only():
    ms = [_mv("burn", 10, idx=1), _mv("burn", 10, idx=2)]
    assert _run(ms, [_bridge_out(10)]) == ["bridge_out", "unclassified"]


# Direct mints (H08): the six February 2026 mints of 1,000 wBRL, wMXN and wCOP on Base and
# Ethereum were sent straight to the token by 0x5ca3...f20f, which held MINTER_ROLE on the
# token at each block (read onchain on 2026-10-03).

WCOP = "0x8a1d45e102e886510e891d2ec656a708991e2d76"
MINTER = "0x5ca3f8eeba12d83408fc097c2dad79212456f20f"
SAFE = "0x2b839174fe62466067c22e2a4c8054071f9d8d68"


class RoleRpc:
    """hasRole(MINTER_ROLE, account) answers from a fixed set, and records each read."""

    def __init__(self, holders: set[str]) -> None:
        self.holders = holders
        self.calls: list[tuple[str, str, str]] = []

    def call(self, method, params, cache=False, historical=False):
        req, block = params
        assert method == "eth_call" and historical
        assert req["data"][10:74] == MINTER_ROLE[2:]
        account = "0x" + req["data"][-40:]
        self.calls.append((req["to"], account, block))
        return "0x" + ("1" if account in self.holders else "0").rjust(64, "0")


def _mint(tx_from: str, tx_to: str, kind: str = "mint", category: str = "unclassified") -> dict:
    return {
        "kind": kind,
        "category": category,
        "block": 24435347,
        "tx_from": tx_from,
        "tx_to": tx_to,
        "amount": "1000000000000000000000",
    }


def test_a_direct_mint_by_a_token_minter_is_primary_with_its_evidence():
    m = _mint(MINTER, WCOP)
    rpc = RoleRpc({MINTER})
    mark_direct_mints([m], rpc, WCOP.upper().replace("0X", "0x"))
    assert m["category"] == "primary"
    assert m["evidence"]["minter"] == MINTER and m["evidence"]["block"] == 24435347
    assert rpc.calls == [(WCOP, MINTER, hex(24435347))]


def test_a_direct_mint_without_the_role_stays_unclassified():
    m = _mint(MINTER, WCOP)
    mark_direct_mints([m], RoleRpc(set()), WCOP)
    assert m["category"] == "unclassified" and "evidence" not in m


def test_a_mint_through_a_wallet_is_not_proven_even_if_the_wallet_has_the_role():
    # Arc, 30/09: a Safe with MINTER_ROLE minted through execTransaction. The caller of mint
    # cannot be proven from the transaction, so the rule leaves it to the exception agent.
    m = _mint("0x00000000000000000000000000000000000000aa", SAFE)
    rpc = RoleRpc({SAFE})
    mark_direct_mints([m], rpc, WCOP)
    assert m["category"] == "unclassified" and rpc.calls == []


def test_burns_and_classified_mints_are_left_alone():
    burn = _mint(MINTER, WCOP, kind="burn")
    bridged = _mint(MINTER, WCOP, category="bridge_in")
    rpc = RoleRpc({MINTER})
    mark_direct_mints([burn, bridged], rpc, WCOP)
    assert burn["category"] == "unclassified" and bridged["category"] == "bridge_in"
    assert rpc.calls == []
