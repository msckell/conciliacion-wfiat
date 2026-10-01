"""Mints and burns from ERC20 Transfer logs (from or to the zero address).

Two independent sources: chunked eth_getLogs on RPC, and an explorer logs API. Callers pass
only final block ranges (cutoff blocks are at least 24 h old), so results are cached forever.
Classification (primary issuance, bridge, unclassified) is added in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from cierre.cache import DiskCache
from cierre.explorer import ExplorerClient
from cierre.rpc import RangeTooLarge, RpcClient

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64


@dataclass(frozen=True)
class Movement:
    chain: str
    token_address: str
    kind: str  # "mint" or "burn"
    block: int
    tx_hash: str
    log_index: int
    amount: int  # base units
    counterparty: str  # receiver of a mint, sender of a burn

    @property
    def signed(self) -> int:
        return self.amount if self.kind == "mint" else -self.amount

    @property
    def key(self) -> tuple[str, int]:
        return (self.tx_hash.lower(), self.log_index)


def _hex_or_int(v) -> int:
    if isinstance(v, int):
        return v
    v = str(v)
    if v.startswith("0x"):
        return int(v, 16) if len(v) > 2 else 0
    return int(v)


def parse_log(chain: str, log: dict) -> Movement | None:
    topics = [t.lower() for t in log["topics"] if t]
    if len(topics) != 3 or topics[0] != TRANSFER_TOPIC:
        return None
    frm, to = topics[1], topics[2]
    if frm == ZERO_TOPIC and to == ZERO_TOPIC:
        raise ValueError(f"{chain}: Transfer from zero to zero in {log['transactionHash']}")
    if frm == ZERO_TOPIC:
        kind, other = "mint", to
    elif to == ZERO_TOPIC:
        kind, other = "burn", frm
    else:
        return None
    return Movement(
        chain=chain,
        token_address=log["address"].lower(),
        kind=kind,
        block=_hex_or_int(log["blockNumber"]),
        tx_hash=log["transactionHash"].lower(),
        log_index=_hex_or_int(log["logIndex"]),
        amount=int(log["data"], 16),
        counterparty="0x" + other[-40:],
    )


class RpcLogFetcher:
    """eth_getLogs over a block range, on a fixed grid so reruns hit the cache.

    A cell that an endpoint refuses is split in two, recursively, down to one block.
    """

    def __init__(self, rpc: RpcClient, grid: int = 10_000) -> None:
        self.rpc = rpc
        self.grid = grid
        self.calls = 0

    def _cell(self, flt: dict, lo: int, hi: int) -> list[dict]:
        params = [flt | {"fromBlock": hex(lo), "toBlock": hex(hi)}]
        try:
            self.calls += 1
            out = self.rpc.call("eth_getLogs", params, cache=True)
        except RangeTooLarge:
            if lo == hi:
                raise
            mid = (lo + hi) // 2
            return self._cell(flt, lo, mid) + self._cell(flt, mid + 1, hi)
        if not isinstance(out, list):
            raise TypeError(f"{self.rpc.chain.key}: eth_getLogs returned {type(out).__name__}")
        return out

    def fetch(self, flt: dict, from_block: int, to_block: int, progress=None) -> list[dict]:
        logs: list[dict] = []
        start = from_block
        while start <= to_block:
            cell_end = (start // self.grid + 1) * self.grid - 1
            end = min(cell_end, to_block)
            logs.extend(self._cell(flt, start, end))
            if progress:
                progress(end)
            start = end + 1
        return logs


def mints_and_burns_rpc(
    rpc: RpcClient, addresses: list[str], from_block: int, to_block: int, grid: int, progress=None
) -> list[Movement]:
    fetcher = RpcLogFetcher(rpc, grid)
    addrs = [a.lower() for a in addresses]
    out: list[Movement] = []
    for topics in ([TRANSFER_TOPIC, ZERO_TOPIC], [TRANSFER_TOPIC, None, ZERO_TOPIC]):
        flt = {"address": addrs, "topics": topics}
        for log in fetcher.fetch(flt, from_block, to_block, progress):
            m = parse_log(rpc.chain.key, log)
            if m is not None and m.token_address in addrs:
                out.append(m)
    return _dedupe(out)


def mints_and_burns_explorer(
    ex: ExplorerClient, address: str, from_block: int, to_block: int
) -> list[Movement]:
    out: list[Movement] = []
    for topics in ([TRANSFER_TOPIC, ZERO_TOPIC], [TRANSFER_TOPIC, None, ZERO_TOPIC]):
        for log in ex.get_logs(address, topics, from_block, to_block):
            m = parse_log(ex.chain.key, log)
            if m is not None:
                out.append(m)
    return _dedupe(out)


def _dedupe(movements: list[Movement]) -> list[Movement]:
    seen: dict[tuple[str, int], Movement] = {}
    for m in movements:
        prev = seen.get(m.key)
        if prev is not None and prev != m:
            raise ValueError(f"conflicting copies of log {m.key}")
        seen[m.key] = m
    return sorted(seen.values(), key=lambda m: (m.block, m.log_index))


def net(movements: list[Movement], address: str) -> int:
    a = address.lower()
    return sum(m.signed for m in movements if m.token_address == a)


__all__ = [
    "DiskCache",
    "Movement",
    "mints_and_burns_explorer",
    "mints_and_burns_rpc",
    "net",
]
