"""Mints and burns from ERC20 Transfer logs (from or to the zero address).

Two independent sources: chunked eth_getLogs on RPC, and an explorer logs API. Callers pass
only final block ranges (cutoff blocks are at least 24 h old), so results are cached forever.
Classification (primary issuance, bridge, unclassified) is added in Phase 1.
"""

from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass

from cierre.cache import DiskCache
from cierre.explorer import ExplorerClient
from cierre.rpc import RangeTooLarge, RpcClient, RpcError

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

    def __init__(self, rpc: RpcClient, grid: int = 10_000, workers_per_endpoint: int = 1) -> None:
        self.rpc = rpc
        self.grid = grid
        self.workers_per_endpoint = workers_per_endpoint
        self.calls = 0

    def _cell(self, flt: dict, lo: int, hi: int, pin: str | None = None) -> list[dict]:
        params = [flt | {"fromBlock": hex(lo), "toBlock": hex(hi)}]
        try:
            self.calls += 1
            out = self.rpc.call("eth_getLogs", params, cache=True, only_url=pin)
        except RangeTooLarge:
            if lo == hi:
                raise
            mid = (lo + hi) // 2
            return self._cell(flt, lo, mid, pin) + self._cell(flt, mid + 1, hi, pin)
        if not isinstance(out, list):
            raise TypeError(f"{self.rpc.chain.key}: eth_getLogs returned {type(out).__name__}")
        return out

    def cells(self, from_block: int, to_block: int) -> list[tuple[int, int]]:
        out = []
        start = from_block
        while start <= to_block:
            end = min((start // self.grid + 1) * self.grid - 1, to_block)
            out.append((start, end))
            start = end + 1
        return out

    def fetch(self, flt: dict, from_block: int, to_block: int, progress=None) -> list[dict]:
        """Cells run in parallel, each pinned to one endpoint in turn. Requests to the same
        endpoint still start at least min_interval apart, so its rate limit holds even with
        several in flight. A cell that fails on its endpoint is retried on any endpoint.
        Order of the result does not matter, callers sort."""
        cells = self.cells(from_block, to_block)
        urls = [ep.url for ep in self.rpc.endpoints]

        def work(i_cell: tuple[int, tuple[int, int]]) -> list[dict]:
            i, (lo, hi) = i_cell
            try:
                return self._cell(flt, lo, hi, pin=urls[i % len(urls)])
            except RangeTooLarge:
                raise
            except RpcError:
                return self._cell(flt, lo, hi, pin=None)

        logs: list[dict] = []
        workers = len(urls) * self.workers_per_endpoint
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for n, part in enumerate(ex.map(work, enumerate(cells)), 1):
                logs.extend(part)
                if progress and n % 200 == 0:
                    progress(n, len(cells))
        return logs


def mints_and_burns_rpc(
    rpc: RpcClient,
    addresses: list[str],
    from_block: int,
    to_block: int,
    grid: int,
    progress=None,
    all_transfers: bool = False,
    workers_per_endpoint: int = 1,
) -> list[Movement]:
    """all_transfers: one query per cell for every Transfer, filtered here, instead of one
    query for mints and one for burns. Halves the calls where cells are small."""
    fetcher = RpcLogFetcher(rpc, grid, workers_per_endpoint)
    addrs = [a.lower() for a in addresses]
    out: list[Movement] = []
    filters = (
        ([TRANSFER_TOPIC],)
        if all_transfers
        else ([TRANSFER_TOPIC, ZERO_TOPIC], [TRANSFER_TOPIC, None, ZERO_TOPIC])
    )
    for topics in filters:
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
