"""What the exception agent sees of a transaction: decoded fields only. The fixtures are the
raw RPC answers of two cases the model refused on 2026-10-01 when it got raw hex."""

import json
import re
from pathlib import Path

from cierre.agent.exceptions import RESULT_CHARS
from cierre.agent.tools import brief_transaction

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "agent_transactions.json").read_text(encoding="utf-8")
)
TOKENS = {
    "0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d": "wARS",
    "0xd76f5faf6888e24d9f04bf92a0c8b921fe4390e0": "wBRL",
}
HEX = re.compile(r"0x[0-9a-fA-F]*")
ZERO = "0x" + "0" * 40


def _brief(name: str) -> dict:
    f = FIXTURES[name]
    return brief_transaction(f["chain"], f["tx"], f["receipt"], f["timestamp"], TOKENS)


def _hex_values(obj, key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _hex_values(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _hex_values(v, key)
    elif isinstance(obj, str) and HEX.fullmatch(obj):
        yield key, obj


def test_no_calldata_topics_or_data_blobs_reach_the_model():
    for name, f in FIXTURES.items():
        b = _brief(name)
        text = json.dumps(b)
        for raw_key in ("input", "topics", "data", "data_prefix"):
            assert f'"{raw_key}"' not in text
        assert f["tx"]["input"][10:74] not in text
        for lg in f["receipt"]["logs"]:
            assert all(t[2:] not in text for t in lg["topics"][:1])
        # Hex is left only as addresses (20 bytes) and transaction hashes (32 bytes).
        for key, value in _hex_values(b):
            assert len(value) == 42 or (len(value) == 66 and key in ("tx_hash", "source_tx_hash"))
        assert len(text) < RESULT_CHARS


def test_handleops_keeps_what_the_agent_and_the_code_use():
    b = _brief("worldchain_handleops")
    assert (b["function"], b["status"], b["block"]) == ("handleOps", "ok", 33467660)
    burn = {
        "emitter": "0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d",
        "event": "Transfer",
        "token": "wARS",
        "from": "0x62af8720d73bdf6801c16826b568ccc9f1d2d835",
        "to": ZERO,
        "amount_base_units": "37112040867826365798",
    }
    assert burn in b["logs"]
    op = next(lg for lg in b["logs"] if lg["event"] == "UserOperationEvent")
    assert (op["sender"], op["success"]) == ("0x34b92f4813f80f2fe8bc739eabb7263e4f6cc9cc", True)
    unknown = [lg for lg in b["logs"] if lg["event"] == "desconocido"]
    assert len(unknown) == 4 and all(set(lg) == {"emitter", "event"} for lg in unknown)


def test_bridge_deposit_is_decoded_instead_of_raw_topics():
    b = _brief("hyperevm_bridge_out")
    assert b["function"] == "depositForBridge"
    dep = next(lg for lg in b["logs"] if lg["event"] == "BridgeDepositInitiated")
    assert dep == {
        "emitter": "0x465e642387d3d73a57cdc1368ffa53a800ba5d47",
        "event": "BridgeDepositInitiated",
        "deposit_id": 19,
        "token": "wBRL",
        "sender": "0xd7700c8fbb6df79ce08d6d1d138547d097a8eb67",
        "amount_base_units": "19965120000000000000000",
        "fee_base_units": "8000000000000000000",
        "dest_chain_id": 1,
        "dest_recipient": "0xd7700c8fbb6df79ce08d6d1d138547d097a8eb67",
    }
