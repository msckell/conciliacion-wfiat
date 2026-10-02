"""Probe the written candidate list: is each token deployed, what is it, when was it created.

Every network checked is recorded, deployed or not. Existence needs two endpoints to agree,
because a lagging or rate limited node can answer "0x" for a contract that exists.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx

from cierre import supply
from cierre.blocks import get_block
from cierre.config import Chain
from cierre.rpc import USER_AGENT, MissingState, RpcClient, RpcError

# EIP-1967 storage slot that holds a proxy's implementation address.
ERC1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
COINGECKO_COIN_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}"


@dataclass
class Probe:
    """What was found for one token address on one network."""

    chain: str
    address: str
    deployed: bool
    endpoints_agreeing: int
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    implementation: str | None = None
    creation_block: int | None = None
    creation_timestamp: int | None = None
    creation_method: str | None = None
    explorer_creation_block: int | None = None
    explorer_creation_tx: str | None = None
    creation_check: str | None = None
    error: str | None = None


def has_code(rpc: RpcClient, address: str) -> tuple[bool, int]:
    """(deployed, endpoints that agree). Raises if two endpoints disagree or none answer."""
    answers: list[bool] = []
    for ep in rpc.endpoints:
        try:
            code = rpc.call("eth_getCode", [address, "latest"], only_url=ep.url)
        except RpcError:
            continue
        answers.append(isinstance(code, str) and len(code) > 2)
        if len(answers) == 2:
            break
    if not answers:
        raise RpcError(rpc.chain.key, "eth_getCode", ["no endpoint answered"])
    if len(set(answers)) > 1:
        raise RpcError(rpc.chain.key, "eth_getCode", [f"endpoints disagree: {answers}"])
    return answers[0], len(answers)


def creation_block(rpc: RpcClient, address: str, head: int) -> int:
    """First block with code at `address`, by binary search on archive state."""
    lo, hi = 0, head  # invariant: no code at lo (or lo == 0), code at hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        code = supply.code_at(rpc, address, mid)
        if isinstance(code, str) and len(code) > 2:
            hi = mid
        else:
            lo = mid
    return hi


def probe(rpc: RpcClient, chain: Chain, address: str, find_creation: bool = True) -> Probe:
    """Caller must have run rpc.qualify_history() if find_creation is True."""
    try:
        deployed, agree = has_code(rpc, address)
    except RpcError as exc:
        return Probe(chain.key, address, False, 0, error=str(exc)[:300])
    p = Probe(chain.key, address, deployed, agree)
    if not deployed:
        return p
    p.name = supply.name(rpc, address)
    p.symbol = supply.symbol(rpc, address)
    p.decimals = supply.decimals(rpc, address)
    slot = rpc.call("eth_getStorageAt", [address, ERC1967_IMPL_SLOT, "latest"])
    p.implementation = "0x" + slot[-40:] if slot and int(slot, 16) else None
    if find_creation:
        head = get_block(rpc, "latest").number
        try:
            blk = creation_block(rpc, address, head)
            p.creation_block = blk
            p.creation_timestamp = get_block(rpc, blk).timestamp
            p.creation_method = "binary search on eth_getCode (archive)"
        except MissingState as exc:
            p.error = f"creation block unknown, no archive endpoint: {str(exc)[:200]}"
        except RpcError as exc:
            p.error = f"creation block unknown, query failed: {str(exc)[:200]}"
        cross_check_creation(rpc, chain, p)
    return p


def cross_check_creation(rpc: RpcClient, chain: Chain, p: Probe) -> None:
    """Compare the binary search result with the creation tx an explorer reports."""
    blockscout = next((e for e in chain.explorer_api if e.kind == "blockscout"), None)
    if blockscout is None:
        p.creation_check = "no free explorer API on this network"
        return
    base = blockscout.url.removesuffix("/api")
    try:
        resp = httpx.get(
            f"{base}/api/v2/addresses/{p.address}",
            timeout=30,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        resp.raise_for_status()
        tx = resp.json().get("creation_transaction_hash")
    except (httpx.HTTPError, ValueError) as exc:
        p.creation_check = f"explorer lookup failed: {type(exc).__name__}"
        return
    if not tx:
        p.creation_check = "explorer reports no creation tx"
        return
    try:
        receipt = rpc.call("eth_getTransactionReceipt", [tx], require_result=True)
    except RpcError:
        p.creation_check = f"creation tx {tx} not found on any RPC"
        return
    p.explorer_creation_tx = tx
    p.explorer_creation_block = int(receipt["blockNumber"], 16)
    if p.creation_block is None:
        p.creation_check = "explorer only"
    elif p.creation_block == p.explorer_creation_block:
        p.creation_check = "match"
    else:
        p.creation_check = (
            f"MISMATCH: archive says {p.creation_block}, explorer says {p.explorer_creation_block}"
        )


def as_dict(p: Probe) -> dict:
    return asdict(p)


# CoinGecko platform ids for the candidate networks. A platform not listed here is reported
# under not_in_candidate_list instead of being dropped.
COINGECKO_PLATFORMS = {
    "ethereum": "ethereum",
    "base": "base",
    "polygon-pos": "polygon",
    "xdai": "gnosis",
    "binance-smart-chain": "bsc",
    "world-chain": "worldchain",
    "celo": "celo",
    "hyperevm": "hyperevm",
    "arc": "arc",
    "arbitrum-one": "arbitrum",
    "optimistic-ethereum": "optimism",
    "avalanche": "avalanche",
    "unichain": "unichain",
    "linea": "linea",
    "scroll": "scroll",
    "mantle": "mantle",
    "sonic": "sonic",
    "monad": "monad",
    "plasma": "plasma",
    "ink": "ink",
    "lisk": "lisk",
    "sei-v2": "sei",
    "katana": "katana",
    "rootstock": "rootstock",
}


def coingecko_platforms(coingecko_id: str, api_key: str | None = None) -> dict:
    """Networks CoinGecko lists for a token, mapped to config keys where possible."""
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    url = COINGECKO_COIN_URL.format(coin_id=coingecko_id)
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "false",
        "community_data": "false",
        "developer_data": "false",
    }
    for attempt in range(4):
        resp = httpx.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        resp.raise_for_status()
        break
    else:
        raise RuntimeError(f"coingecko {coingecko_id}: rate limited")
    detail = resp.json().get("detail_platforms") or {}
    out = {"coingecko_id": coingecko_id, "platforms": {}, "not_in_candidate_list": {}}
    for platform, info in detail.items():
        address = (info or {}).get("contract_address") or ""
        if not platform or not address:
            continue
        key = COINGECKO_PLATFORMS.get(platform)
        if key is None:
            out["not_in_candidate_list"][platform] = address.lower()
        else:
            out["platforms"][key] = address.lower()
    return out
