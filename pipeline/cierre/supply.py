"""Read ERC20 totalSupply, name, symbol and decimals at a given block."""

from __future__ import annotations

from cierre.rpc import RpcClient

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"
SEL_DECIMALS = "0x313ce567"


class NoContract(Exception):
    """There is no code at the address at that block. This is not a zero supply."""


def _call(rpc: RpcClient, address: str, data: str, block: int | str) -> str:
    tag = hex(block) if isinstance(block, int) else block
    cacheable = isinstance(block, int)
    out = rpc.call(
        "eth_call",
        [{"to": address, "data": data}, tag],
        cache=cacheable,
        historical=cacheable,
    )
    if not isinstance(out, str) or out in ("0x", ""):
        raise NoContract(f"{rpc.chain.key}: empty eth_call result at {address} block {block}")
    return out


def code_at(rpc: RpcClient, address: str, block: int | str) -> str:
    tag = hex(block) if isinstance(block, int) else block
    cacheable = isinstance(block, int)
    return rpc.call("eth_getCode", [address, tag], cache=cacheable, historical=cacheable)


def total_supply(rpc: RpcClient, address: str, block: int | str) -> int:
    """totalSupply in base units. Raises NoContract instead of returning 0."""
    return int(_call(rpc, address, SEL_TOTAL_SUPPLY, block), 16)


def decimals(rpc: RpcClient, address: str, block: int | str = "latest") -> int:
    return int(_call(rpc, address, SEL_DECIMALS, block), 16)


def _decode_string(hexdata: str) -> str:
    raw = bytes.fromhex(hexdata[2:])
    if len(raw) >= 64:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + length].decode("utf-8", errors="replace")
    return raw.rstrip(b"\x00").decode("utf-8", errors="replace")


def name(rpc: RpcClient, address: str, block: int | str = "latest") -> str:
    return _decode_string(_call(rpc, address, SEL_NAME, block))


def symbol(rpc: RpcClient, address: str, block: int | str = "latest") -> str:
    return _decode_string(_call(rpc, address, SEL_SYMBOL, block))
