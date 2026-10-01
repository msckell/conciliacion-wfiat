"""Mints and burns from ERC20 Transfer logs (from or to the zero address).

Two independent sources: chunked eth_getLogs on RPC, and an explorer logs API. Callers pass
only final block ranges (cutoff blocks are at least 24 h old), so results are cached forever.
Classification (primary issuance, bridge, unclassified) is added in Phase 1.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
from dataclasses import dataclass, replace

from cierre import REPO_ROOT
from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.explorer import ExplorerClient, ExplorerError
from cierre.rpc import _INVALID_RANGE_PATTERNS as _INVALID_RANGE
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
        except RpcError as exc:
            # An endpoint answered "invalid block range". Seen on HyperEVM (2026-10-01):
            # newer blocks fail at 1000 and pass at 500, so this is a size limit there.
            # A real gap fails down to one block and raises.
            if lo == hi or not any(_INVALID_RANGE.search(e) for e in exc.errors):
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


def _bscscan_movements(
    rpc: RpcClient, chain: Chain, tokens: list[Token], lo: int, hi: int
) -> list[Movement]:
    listing = json.loads((REPO_ROOT / chain.logs_bscscan_list).read_text(encoding="utf-8"))
    addrs = {t.address.lower() for t in tokens}
    txs = [tx for t in tokens for tx in listing["tokens"][t.symbol]["transactions"]]
    out: list[Movement] = []
    for tx in txs:
        if not lo <= tx["block"] <= hi:
            continue
        receipt = rpc.call("eth_getTransactionReceipt", [tx["tx_hash"]], require_result=True)
        if int(receipt["status"], 16) != 1:
            continue
        for log in receipt["logs"]:
            if log["address"].lower() not in addrs:
                continue
            if not log["topics"] or log["topics"][0].lower() != TRANSFER_TOPIC:
                continue
            m = parse_log(chain.key, log)
            if m is not None:
                out.append(m)
    return sorted(set(out), key=lambda m: (m.block, m.log_index))


def collect_sources_multi(
    chain: Chain,
    tokens: list[Token],
    lo: int,
    hi: int,
    cache: DiskCache,
    failed: dict[str, str],
    progress=None,
) -> dict[str, list[Movement]]:
    """Every available log source for mints and burns of `tokens` in [lo, hi]. RPC sources
    ask for every token in one scan. A source that fails is recorded in `failed` and left
    out, it never counts as "no movements". An explorer source counts only if it answered
    for every token."""
    sources: dict[str, list[Movement]] = {}
    if chain.logs_rpc_urls:
        name = "rpc:" + ",".join(u.split("//")[1] for u in chain.logs_rpc_urls)
        rpc = RpcClient(replace(chain, rpc_urls=chain.logs_rpc_urls), cache)
        try:
            sources[name] = mints_and_burns_rpc(
                rpc,
                [t.address for t in tokens],
                lo,
                hi,
                chain.logs_grid,
                progress,
                all_transfers=chain.logs_all_transfers,
                workers_per_endpoint=chain.logs_workers_per_endpoint,
            )
        except RpcError as exc:
            failed[name] = str(exc)[:300]
        rpc.close()
    for api in chain.explorer_api:
        if api.kind == "etherscan" and not os.environ.get("ETHERSCAN_API_KEY"):
            continue
        ex = ExplorerClient(chain, api, cache)
        try:
            sources[ex.label] = _dedupe(
                [m for t in tokens for m in mints_and_burns_explorer(ex, t.address, lo, hi)]
            )
        except ExplorerError as exc:
            failed[ex.label] = str(exc)[:300]
    if chain.logs_bscscan_list:
        rpc = RpcClient(chain, cache)
        sources["bscscan list + RPC receipts"] = _bscscan_movements(rpc, chain, tokens, lo, hi)
        rpc.close()
    return sources


def net(movements: list[Movement], address: str) -> int:
    a = address.lower()
    return sum(m.signed for m in movements if m.token_address == a)


__all__ = [
    "DiskCache",
    "Movement",
    "collect_sources_multi",
    "mints_and_burns_explorer",
    "mints_and_burns_rpc",
    "net",
]
