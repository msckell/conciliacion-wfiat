"""Etherscan style `module=logs&action=getLogs` API (Blockscout and Etherscan V2).

Used as a second, independent source for mint and burn logs. Results are capped at 1000
per call, so a full page means the range is split and asked again.
"""

from __future__ import annotations

import os
import time

import httpx

from cierre.cache import DiskCache
from cierre.config import Chain, ExplorerApi
from cierre.rpc import USER_AGENT

PAGE_CAP = 1000


class ExplorerError(Exception):
    pass


class ExplorerClient:
    def __init__(
        self, chain: Chain, api: ExplorerApi, cache: DiskCache | None, min_interval: float = 0.35
    ) -> None:
        self.chain = chain
        self.api = api
        self.cache = cache
        self.min_interval = min_interval
        self._last = 0.0
        self.http = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
        self.key = os.environ.get("ETHERSCAN_API_KEY") if api.kind == "etherscan" else None
        if api.kind == "etherscan" and not self.key:
            raise ExplorerError("ETHERSCAN_API_KEY is not set")

    @property
    def label(self) -> str:
        return f"{self.api.kind}:{self.api.url}"

    def _get(self, params: dict) -> list[dict]:
        if self.api.kind == "etherscan":
            params = {"chainid": self.chain.chain_id, **params, "apikey": self.key}
        for attempt in range(4):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.http.get(self.api.url, params=params)
            except httpx.HTTPError as exc:
                err = f"{type(exc).__name__}"
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                err = f"HTTP {resp.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                err = f"HTTP {resp.status_code} non JSON body: {resp.text[:80]!r}"
                time.sleep(3 * (attempt + 1))
                continue
            result = data.get("result")
            if data.get("status") == "1" and isinstance(result, list):
                return result
            msg = str(data.get("message", "")) + " " + str(result)[:200]
            if isinstance(result, list) and "no logs found" in msg.lower():
                return []
            if "rate limit" in msg.lower() or "max calls" in msg.lower():
                err = msg
                time.sleep(3 * (attempt + 1))
                continue
            raise ExplorerError(f"{self.label}: {msg}")
        raise ExplorerError(f"{self.label}: gave up after retries: {err}")

    def get_logs(
        self, address: str, topics: list[str | None], from_block: int, to_block: int
    ) -> list[dict]:
        """All logs in [from_block, to_block], splitting the range when a page is full."""
        params: dict = {
            "module": "logs",
            "action": "getLogs",
            "address": address,
            "fromBlock": from_block,
            "toBlock": to_block,
        }
        for i, t in enumerate(topics):
            if t is not None:
                params[f"topic{i}"] = t
        set_topics = [i for i, t in enumerate(topics) if t is not None]
        for a, b in zip(set_topics, set_topics[1:], strict=False):
            params[f"topic{a}_{b}_opr"] = "and"

        key = DiskCache.key("explorer", self.api.url, self.chain.chain_id, params)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        logs = self._get(dict(params))
        if len(logs) >= PAGE_CAP:
            if from_block == to_block:
                raise ExplorerError(f"{self.label}: more than {PAGE_CAP} logs in one block")
            mid = (from_block + to_block) // 2
            logs = self.get_logs(address, topics, from_block, mid) + self.get_logs(
                address, topics, mid + 1, to_block
            )
        if self.cache is not None:
            self.cache.put(key, logs)
        return logs
