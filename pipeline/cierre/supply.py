"""Read ERC20 totalSupply, name, symbol and decimals at a given block."""

from __future__ import annotations

from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.cutoffs import CONVENTIONS, cutoff_block
from cierre.rpc import RpcClient, block_tag

# Created long after block 1 on every network, so a node that really serves historical state
# answers "no code" for it at block 1. See RpcClient.qualify_history().
HISTORY_PROBE_SYMBOL = "wARS"

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"
SEL_DECIMALS = "0x313ce567"


class NoContract(Exception):
    """There is no code at the address at that block. This is not a zero supply."""


def _call(rpc: RpcClient, address: str, data: str, block: int | str) -> str:
    # An explicit block never changes, so it is cached and needs archive state.
    by_number = isinstance(block, int)
    out = rpc.call(
        "eth_call",
        [{"to": address, "data": data}, block_tag(block)],
        cache=by_number,
        historical=by_number,
    )
    if not isinstance(out, str) or out in ("0x", ""):
        raise NoContract(f"{rpc.chain.key}: empty eth_call result at {address} block {block}")
    return out


def code_at(rpc: RpcClient, address: str, block: int | str) -> str:
    """Contract code at a block, "0x" when there is none."""
    by_number = isinstance(block, int)
    return rpc.call(
        "eth_getCode", [address, block_tag(block)], cache=by_number, historical=by_number
    )


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


def _two_reads(
    rpc: RpcClient, urls: list[str], address: str, block: int, live: bool = False
) -> dict[str, str]:
    """totalSupply from up to two archive endpoints, moving on when one fails. Each answer
    is cached per endpoint, so the comparison stays between two independent reads. With
    `live`, the cache is skipped and both endpoints are asked again."""
    reads: dict[str, str] = {}
    for url in urls:
        try:
            out = rpc.call(
                "eth_call",
                [{"to": address, "data": SEL_TOTAL_SUPPLY}, hex(block)],
                only_url=url,
                cache=not live,
                cache_per_url=True,
                historical=True,
            )
            reads[url] = str(int(out, 16))
        except Exception as exc:  # the failure is the read's value, not a zero
            reads[url] = f"error: {type(exc).__name__}: {str(exc)[:160]}"
        if sum(not v.startswith("error") for v in reads.values()) == 2:
            break
    return reads


def read_chain(
    chain: Chain,
    tokens: dict[str, Token],
    days: list[str],
    cache: DiskCache,
    log: bool = False,
    conventions: tuple[str, ...] | None = None,
    live: bool = False,
) -> dict:
    """Cutoff blocks (both conventions) and raw totalSupply of every token at each one.

    Two archive endpoints are read when available and must agree. A failed or disputed read
    is recorded as an error, never as a zero. A token whose contract did not exist yet at
    the cutoff block gets status not_created, with its creation block as evidence.

    `live` reads totalSupply from the network again instead of the disk cache. Cutoff blocks
    may still come from the cache: a block number at a past instant never changes."""
    key = chain.key
    rpc = RpcClient(chain, cache)
    history = rpc.qualify_history(tokens[HISTORY_PROBE_SYMBOL].address)
    good = [ep.url for ep in rpc.endpoints if ep.history_ok]
    blocks, rows = [], []
    for day in days:
        for conv in conventions or tuple(CONVENTIONS):
            cb = cutoff_block(rpc, day, conv)
            blocks.append(cb)
            for sym, tok in tokens.items():
                dep = tok.on(key)
                row = {
                    "token": sym,
                    "chain": key,
                    "cutoff": day,
                    "convention": conv,
                    "block": cb["block"],
                }
                if cb["block"] is None:
                    row |= {
                        "status": "network_not_live",
                        "raw": "0",
                        "block_1_timestamp": cb["block_1_timestamp"],
                    }
                elif dep is None or dep.creation_block is None:
                    row |= {"status": "error", "error": "no creation block on record"}
                elif dep.creation_block > cb["block"]:
                    row |= {
                        "status": "not_created",
                        "raw": "0",
                        "creation_block": dep.creation_block,
                    }
                else:
                    reads = _two_reads(rpc, good, tok.address, cb["block"], live)
                    values = {v for v in reads.values() if not v.startswith("error")}
                    if len(values) == 1:
                        row |= {"status": "ok", "raw": values.pop(), "reads": reads}
                    else:
                        row |= {
                            "status": "error",
                            "error": "reads missing or disagree",
                            "reads": reads,
                        }
                rows.append(row)
                if log:
                    print(
                        f"{key:10} {day} {conv} blk={cb['block']} {sym:5} "
                        f"{row['status']:11} {row.get('raw', row.get('error'))}",
                        flush=True,
                    )
    rpc.close()
    return {"chain": key, "history": history, "cutoff_blocks": blocks, "rows": rows}
