"""Classify each mint and burn from the events in its own transaction receipt.

- bridge_out: a burn with a BridgeDepositInitiated event in the same receipt (same token,
  sender and amount), emitted by a BridgeDeposit contract listed in config/contracts.yaml.
- bridge_in: a mint with a BridgeMintFulfilled event (same token, recipient and amount),
  emitted by a listed BridgeDeposit contract.
- primary: a mint with a LimitedMinter Minted event (same token, destination and amount),
  emitted by a listed LimitedMinter, and no bridge event for it.
- redemption: a burn of the sender's own tokens (no bridge event) where the sender holds
  MINTER_ROLE on a listed LimitedMinter of that network at that block. Checked onchain by
  mark_redemptions(), after classify_movements().
- unclassified: anything else. Nothing is guessed.

Each event is used for one movement only, so a transaction with several movements cannot
classify two of them with the same event. Classification never changes the reconciliation,
which counts every movement that changes supply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

from cierre import CONFIG_DIR
from cierre.abi import BridgeIn, BridgeOut, PrimaryMint, decode, keccak256, selector
from cierre.cache import DiskCache
from cierre.config import Chain
from cierre.rpc import RpcClient

MINTER_ROLE = "0x" + keccak256(b"MINTER_ROLE").hex()
SEL_HAS_ROLE = selector("hasRole(bytes32,address)")


@dataclass(frozen=True)
class KnownContract:
    chain: str
    role: str  # bridge_deposit, limited_minter_bridge or limited_minter
    address: str
    code_hash: str
    evidence: str


def load_contracts(path: Path | None = None) -> list[KnownContract]:
    raw = yaml.safe_load((path or CONFIG_DIR / "contracts.yaml").read_text(encoding="utf-8"))
    out = []
    for role, entries in raw["contracts"].items():
        for e in entries:
            out.append(
                KnownContract(
                    e["chain"], role, e["address"].lower(), e["code_hash"].lower(), e["evidence"]
                )
            )
    return out


def receipt_client(chain: Chain, cache: DiskCache) -> RpcClient:
    """Receipts from the public endpoints first, then the logs endpoints. Some public nodes
    answer null for old receipts (seen on Celo), which moves on to the next endpoint."""
    return RpcClient(replace(chain, rpc_urls=chain.rpc_urls + chain.logs_rpc_urls), cache)


def classify_movements(
    movements: list[dict],
    receipts: dict[str, dict],
    known: list[KnownContract],
    chain: str,
    token_address: str,
) -> None:
    """Adds category and evidence to each movement dict, in place. `receipts` maps tx hash
    to the receipt of that transaction on this chain."""
    bridges = {k.address for k in known if k.chain == chain and k.role == "bridge_deposit"}
    minters = {k.address for k in known if k.chain == chain and k.role == "limited_minter"}
    token = token_address.lower()
    used: set[tuple[str, int]] = set()

    def events(tx: str):
        for lg in receipts[tx]["logs"]:
            ev = decode(lg)
            if ev is not None:
                yield (tx, int(lg["logIndex"], 16)), ev

    for m in sorted(movements, key=lambda m: (m["block"], m["log_index"])):
        tx = m["tx_hash"]
        amount = int(m["amount"])
        who = m["counterparty"].lower()
        found = None
        for key, ev in events(tx):
            if key in used or ev.token != token or ev.amount != amount:
                continue
            if ev.emitter not in bridges:
                continue
            if m["kind"] == "burn" and isinstance(ev, BridgeOut) and ev.sender == who:
                found = ("bridge_out", key, ev)
                break
            if m["kind"] == "mint" and isinstance(ev, BridgeIn) and ev.to == who:
                found = ("bridge_in", key, ev)
                break
        if found is None and m["kind"] == "mint":
            for key, ev in events(tx):
                if key in used or ev.token != token or ev.amount != amount:
                    continue
                if isinstance(ev, PrimaryMint) and ev.emitter in minters and ev.destination == who:
                    found = ("primary", key, ev)
                    break
        if found is None:
            m["category"] = "unclassified"
            m["evidence"] = None
            continue
        category, key, ev = found
        used.add(key)
        m["category"] = category
        m["evidence"] = {"event": type(ev).__name__, "log_index": key[1], **_jsonable(ev)}


def _jsonable(ev: BridgeIn | BridgeOut | PrimaryMint) -> dict:
    return {k: (str(v) if isinstance(v, int) else v) for k, v in asdict(ev).items()}


def mark_redemptions(
    movements: list[dict], rpc: RpcClient, known: list[KnownContract], chain: str
) -> None:
    """Unclassified burns of the sender's own tokens become redemption when the sender holds
    MINTER_ROLE on a listed LimitedMinter of this network, read at the burn's block.
    `rpc` must have passed qualify_history(), since the role is read from archive state."""
    minters = [k.address for k in known if k.chain == chain and k.role == "limited_minter"]
    for m in movements:
        if m.get("category") != "unclassified" or m["kind"] != "burn":
            continue
        burner = m["counterparty"].lower()
        if m.get("tx_from", "").lower() != burner:
            continue
        for lm in minters:
            data = SEL_HAS_ROLE + MINTER_ROLE[2:] + "0" * 24 + burner[2:]
            out = rpc.call(
                "eth_call",
                [{"to": lm, "data": data}, hex(m["block"])],
                cache=True,
                historical=True,
            )
            if int(out, 16) == 1:
                m["category"] = "redemption"
                m["evidence"] = {
                    "check": "LimitedMinter.hasRole(MINTER_ROLE, burner) at the burn block",
                    "limited_minter": lm,
                    "burner": burner,
                    "block": m["block"],
                }
                break
