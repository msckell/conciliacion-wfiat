"""Load config/chains.yaml and config/tokens.yaml into plain dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cierre import CONFIG_DIR


@dataclass(frozen=True)
class ExplorerApi:
    kind: str  # "blockscout" or "etherscan"
    url: str


@dataclass(frozen=True)
class Chain:
    key: str
    name: str
    chain_id: int
    in_scope: bool
    rpc_urls: tuple[str, ...]
    explorer: str
    explorer_api: tuple[ExplorerApi, ...] = field(default_factory=tuple)
    no_history: tuple[str, ...] = field(default_factory=tuple)

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.explorer}/tx/{tx_hash}"

    def block_url(self, number: int) -> str:
        return f"{self.explorer}/block/{number}"

    def address_url(self, address: str) -> str:
        return f"{self.explorer}/address/{address}"


def load_chains(path: Path | None = None) -> dict[str, Chain]:
    raw = yaml.safe_load((path or CONFIG_DIR / "chains.yaml").read_text(encoding="utf-8"))
    chains: dict[str, Chain] = {}
    for key, c in raw["chains"].items():
        urls = list(c["rpc_urls"])
        override = os.environ.get(f"RPC_URL_{key.upper()}")
        if override:
            urls.insert(0, override)
        chains[key] = Chain(
            key=key,
            name=c["name"],
            chain_id=int(c["chain_id"]),
            in_scope=bool(c["in_scope"]),
            rpc_urls=tuple(urls),
            explorer=c["explorer"].rstrip("/"),
            explorer_api=tuple(ExplorerApi(**e) for e in c.get("explorer_api") or []),
            no_history=tuple(c.get("no_history") or []),
        )
    return chains


@dataclass(frozen=True)
class Deployment:
    chain: str
    address: str
    creation_block: int | None
    creation_tx: str | None


@dataclass(frozen=True)
class Token:
    symbol: str
    name: str
    decimals: int
    coingecko_id: str | None
    status: str
    address: str
    deployments: tuple[Deployment, ...]

    def on(self, chain: str) -> Deployment | None:
        return next((d for d in self.deployments if d.chain == chain), None)


def load_tokens(path: Path | None = None, include_unconfirmed: bool = False) -> dict[str, Token]:
    raw = yaml.safe_load((path or CONFIG_DIR / "tokens.yaml").read_text(encoding="utf-8"))
    tokens: dict[str, Token] = {}
    for symbol, t in raw["tokens"].items():
        if t["status"] != "confirmed" and not include_unconfirmed:
            continue
        deps = tuple(
            Deployment(
                chain=chain,
                address=t["address"],
                creation_block=d.get("creation_block"),
                creation_tx=d.get("creation_tx"),
            )
            for chain, d in (t.get("deployments") or {}).items()
        )
        tokens[symbol] = Token(
            symbol=symbol,
            name=t["name"],
            decimals=int(t["decimals"]),
            coingecko_id=t.get("coingecko_id"),
            status=t["status"],
            address=t["address"],
            deployments=deps,
        )
    return tokens
