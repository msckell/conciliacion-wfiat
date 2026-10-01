"""Classification from receipt events, offline with hand built receipts."""

from cierre.abi import BRIDGE_DEPOSIT_INITIATED, BRIDGE_MINT_FULFILLED, LIMITED_MINTER_MINTED
from cierre.classify import KnownContract, classify_movements

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
