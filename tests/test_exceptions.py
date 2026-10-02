"""Exception agent: code accepts only proposals it can check, offline with fake tools."""

from types import SimpleNamespace

import pytest

from cierre.abi import BridgeIn
from cierre.agent.exceptions import investigate, verify
from cierre.agent.llm import Reply
from cierre.rpc import RpcError

TOKEN = "0x0dc4f92879b7670e5f4e4e6e3c801d229129d90d"
SAFE = "0x" + "2b" * 20
USER = "0x" + "ab" * 20
ZERO = "0x" + "0" * 40


class FakeTools:
    def __init__(self, role=True, refund_amount="10"):
        self.chains = {
            "arc": SimpleNamespace(chain_id=5042),
            "ethereum": SimpleNamespace(chain_id=1),
        }
        self.by_symbol = {"wARS": TOKEN}
        self.known = []
        self.role = role
        self.refund_amount = refund_amount

    def receipt(self, chain, tx_hash):
        return {"logs": [{}]}

    def get_transaction(self, chain, tx_hash):
        if tx_hash == "0xrefund":
            log = {
                "event": "Transfer",
                "from": ZERO,
                "to": USER,
                "token": "wARS",
                "amount_base_units": self.refund_amount,
            }
            return {"status": "ok", "block": 20, "from": USER, "to": TOKEN, "logs": [log]}
        return {"status": "ok", "block": 10, "from": "0x" + "11" * 20, "to": SAFE, "logs": []}

    def has_role(self, chain, contract, role, account, block):
        return {"has_role": self.role}


MINT = {
    "chain": "arc",
    "token": "wARS",
    "kind": "mint",
    "amount": "10",
    "block": 10,
    "tx_hash": "0xmint",
    "counterparty": SAFE,
    "category": "unclassified",
}
BURN = {
    "chain": "arc",
    "token": "wARS",
    "kind": "burn",
    "amount": "10",
    "block": 10,
    "tx_hash": "0xburn",
    "counterparty": USER,
    "category": "bridge_out",
    "evidence": {"deposit_id": "1", "dest_chain_id": "1", "dest_recipient": USER},
}


def test_primary_by_minter_needs_the_role_and_the_right_account():
    p = {"kind": "primary_by_minter", "account": SAFE, "contract": TOKEN}
    assert verify(p, MINT, FakeTools(role=True))[0] is True
    assert verify(p, MINT, FakeTools(role=False))[0] is False
    other = p | {"account": "0x" + "99" * 20}
    assert verify(other, MINT, FakeTools(role=True))[0] is False


def test_refund_needs_same_amount_same_account_and_later_block():
    p = {"kind": "bridge_refund", "refund_tx": "0xrefund"}
    assert verify(p, BURN, FakeTools())[0] is True
    assert verify(p, BURN, FakeTools(refund_amount="9"))[0] is False


def _fulfillment(**changes) -> BridgeIn:
    """The destination mint that answers BURN, with some fields changed."""
    fields = {
        "emitter": "0x" + "b0" * 20,
        "token": TOKEN,
        "to": USER,
        "amount": 10,
        "source_chain_id": 5042,
        "source_tx_hash": "0xburn",
        "source_deposit_id": 1,
    }
    return BridgeIn(**(fields | changes))


@pytest.mark.parametrize(
    "changes, accepted",
    [
        ({}, True),
        ({"amount": 9}, False),
        ({"source_deposit_id": 2}, False),
        ({"source_tx_hash": "0xother"}, False),
        ({"source_chain_id": 1}, False),
    ],
)
def test_late_fulfillment_needs_the_same_deposit_hash_chain_and_amount(
    monkeypatch, changes, accepted
):
    monkeypatch.setattr("cierre.agent.exceptions.decode", lambda lg: _fulfillment(**changes))
    p = {"kind": "bridge_late_fulfillment", "dest_chain": "ethereum", "dest_tx": "0xdest"}
    assert verify(p, BURN, FakeTools())[0] is accepted


def test_late_fulfillment_needs_the_destination_of_the_deposit_and_a_bridge_out():
    p = {"kind": "bridge_late_fulfillment", "dest_chain": "arc", "dest_tx": "0xdest"}
    assert verify(p, BURN, FakeTools())[0] is False
    assert verify(p | {"dest_chain": "ethereum"}, MINT, FakeTools())[0] is False


def test_loop_calls_a_tool_then_proposes_and_logs_each_step():
    replies = [
        {
            "thought": "miro la tx",
            "action": "get_transaction",
            "args": {"chain": "arc", "tx_hash": "0xmint"},
        },
        {
            "thought": "el Safe tiene el rol",
            "action": "propose",
            "args": {
                "kind": "primary_by_minter",
                "account": SAFE,
                "contract": TOKEN,
                "summary": "Emitió el Safe.",
            },
        },
    ]
    seen, logged = [], []

    def ask(system, prompt, schema):
        seen.append(prompt)
        return Reply(replies[len(seen) - 1], "fake", 0.0)

    tools = FakeTools()
    res = investigate(MINT, "sin clasificar", tools, ask, logged.append)
    assert (res["outcome"], res["kind"], res["steps"]) == ("resolved", "primary_by_minter", 2)
    assert "Resultado" in seen[1] and len(logged) == 2
    assert "Redes válidas: arc (chain id 5042), ethereum (chain id 1).\nTokens: wARS." in seen[0]


def test_needs_person_becomes_a_task():
    def ask(system, prompt, schema):
        return Reply(
            {
                "thought": "no sé",
                "action": "propose",
                "args": {"kind": "needs_person", "summary": "Mirá el contrato."},
            },
            "fake",
            0.0,
        )

    res = investigate(MINT, "sin clasificar", FakeTools(), ask, lambda e: None)
    assert res["outcome"] == "task"


def _scripted(replies):
    def ask(system, prompt, schema):
        return Reply(replies.pop(0), "fake", 0.0)

    return ask


def test_a_proposal_the_chain_cannot_confirm_becomes_a_task():
    class Unreadable(FakeTools):
        def receipt(self, chain, tx_hash):
            raise RpcError(chain, "eth_getTransactionReceipt", ["not found"])

    proposal = {
        "kind": "bridge_late_fulfillment",
        "dest_chain": "ethereum",
        "dest_tx": "0xinvented",
        "summary": "Se completó del otro lado.",
    }
    ask = _scripted([{"thought": "ya está", "action": "propose", "args": proposal}])
    res = investigate(BURN, "puente sin par", Unreadable(), ask, lambda e: None)
    assert res["outcome"] == "task"
    assert res["checks"] == ["no se pudo comprobar la propuesta (RpcError)"]


def test_the_agent_can_only_call_the_listed_tools():
    calls = []

    class Spy(FakeTools):
        def close(self):
            calls.append("close")

    replies = [
        {"thought": "cierro", "action": "close", "args": {}},
        {
            "thought": "no sé",
            "action": "propose",
            "args": {"kind": "needs_person", "summary": "Mirá el contrato."},
        },
    ]
    logged = []
    res = investigate(MINT, "sin clasificar", Spy(), _scripted(replies), logged.append)
    assert calls == [] and res["outcome"] == "task"
    assert logged[0]["result_preview"] == "error: unknown tool 'close'"
