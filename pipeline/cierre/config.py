"""Load config/chains.yaml and config/tokens.yaml into plain dataclasses."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cierre import CONFIG_DIR

# Blocks per eth_getLogs cell when chains.yaml sets no logs_rpc.grid for a network.
DEFAULT_LOGS_GRID = 10_000


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
    logs_rpc_urls: tuple[str, ...] = field(default_factory=tuple)
    logs_grid: int = DEFAULT_LOGS_GRID
    logs_all_transfers: bool = False
    logs_workers_per_endpoint: int = 1
    logs_bscscan_list: str | None = None
    # (source label, env var) of logs URLs left out because the variable is not set.
    logs_missing_keys: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.explorer}/tx/{tx_hash}"

    def block_url(self, number: int) -> str:
        return f"{self.explorer}/block/{number}"


_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")
# Values taken from the environment into URLs (free API keys). redact() hides them.
_SECRETS: set[str] = set()


def _expand(url: str) -> str | None:
    """A URL with ${VAR} placeholders filled from the environment, or None when a variable
    is not set: that endpoint is then simply not available."""
    names = _VAR.findall(url)
    values = {n: os.environ.get(n) for n in names}
    if not all(values.values()):
        return None
    _SECRETS.update(v for v in values.values() if v)
    return _VAR.sub(lambda m: values[m.group(1)], url)


def redact_placeholders(url: str) -> str:
    """bsc-mainnet.nodereal.io/v1/${NODEREAL_API_KEY} -> bsc-mainnet.nodereal.io/v1/***"""
    return _VAR.sub("***", url)


def redact(text: str) -> str:
    """Text with every key taken from the environment replaced by ***."""
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return text


def load_chains(path: Path | None = None) -> dict[str, Chain]:
    raw = yaml.safe_load((path or CONFIG_DIR / "chains.yaml").read_text(encoding="utf-8"))
    chains: dict[str, Chain] = {}
    for key, c in raw["chains"].items():
        urls = list(c["rpc_urls"])
        override = os.environ.get(f"RPC_URL_{key.upper()}")
        if override:
            urls.insert(0, override)
        logs = c.get("logs_rpc") or {}
        missing = tuple(
            ("rpc:" + redact_placeholders(u.split("//")[1]), _VAR.findall(u)[0])
            for u in logs.get("urls") or []
            if _expand(u) is None
        )
        chains[key] = Chain(
            key=key,
            name=c["name"],
            chain_id=int(c["chain_id"]),
            in_scope=bool(c["in_scope"]),
            rpc_urls=tuple(urls),
            explorer=c["explorer"].rstrip("/"),
            explorer_api=tuple(ExplorerApi(**e) for e in c.get("explorer_api") or []),
            no_history=tuple(c.get("no_history") or []),
            logs_rpc_urls=tuple(u for u in map(_expand, logs.get("urls") or []) if u),
            logs_grid=int(logs.get("grid") or DEFAULT_LOGS_GRID),
            logs_all_transfers=bool(logs.get("all_transfers")),
            logs_workers_per_endpoint=int(logs.get("workers") or 1),
            logs_bscscan_list=c.get("logs_bscscan_list"),
            logs_missing_keys=missing,
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
    name_es: str | None = None  # display name on the page

    def on(self, chain: str) -> Deployment | None:
        return next((d for d in self.deployments if d.chain == chain), None)


def in_scope(chains: dict[str, Chain]) -> dict[str, Chain]:
    """The networks the close reconciles (in_scope in chains.yaml)."""
    return {k: c for k, c in chains.items() if c.in_scope}


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
            name_es=t.get("name_es"),
        )
    return tokens
