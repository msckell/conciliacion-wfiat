"""JSON-RPC client: several endpoints per chain, retries, rate limit, disk cache.

A failed query raises. It never turns into a default value such as zero or an empty list.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from cierre.cache import DiskCache
from cierre.config import Chain

USER_AGENT = "cierre-wfiat-demo/0.1 (+https://github.com/msckell)"


class RpcError(Exception):
    """Every endpoint failed. Carries the per endpoint errors."""

    def __init__(self, chain: str, method: str, errors: list[str]) -> None:
        self.chain = chain
        self.method = method
        self.errors = errors
        super().__init__(f"{chain} {method}: all endpoints failed: " + " | ".join(errors))


class RangeTooLarge(RpcError):
    """The endpoint refused an eth_getLogs range or result size. The caller must split."""


class MissingState(RpcError):
    """No endpoint could serve historical state (no archive node available)."""


class CallReverted(Exception):
    """eth_call reverted. Deterministic, so no point trying another endpoint."""


_RANGE_PATTERNS = re.compile(
    r"block range (is )?(too|exceed)|max(imum)? block range|block range limit|range is too|"
    r"range too|exceed.*range|too many (blocks|results|logs)|"
    r"more than \d+ (results|logs)|response size|max results|retry with|query timeout|"
    r"maximum (block )?range|max(imum)? (is|of) \d+|eth_getLogs.*(range|limited)|"
    r"ranges? over|-32005|-32012|log response size|too large|exceeds the range",
    re.IGNORECASE,
)
# The node does not have that range (a gap or a lagging backend). Not a size problem, so
# the range is not split: another endpoint is asked instead.
_INVALID_RANGE_PATTERNS = re.compile(r"invalid block range", re.IGNORECASE)
_RATE_PATTERNS = re.compile(
    r"rate.?limit|too many requests|throttl|capacity|request timeout|timed out|temporarily",
    re.IGNORECASE,
)
_STATE_PATTERNS = re.compile(
    r"missing trie node|header not found|state.*not available|historical state|pruned|"
    r"state histories|old data not available|not an archive|no state available|"
    r"required historical|state is not|unknown (block|state)|block not found|"
    r"archive|not supported for blocks|beyond.*history",
    re.IGNORECASE,
)


@dataclass
class _Endpoint:
    url: str
    min_interval: float
    last_call: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    # None until qualify_history() runs. Historical state is read only where this is True.
    history_ok: bool | None = None
    history_note: str = ""

    def wait_turn(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = self.last_call + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self.last_call = time.monotonic()


class RpcClient:
    def __init__(
        self,
        chain: Chain,
        cache: DiskCache | None = None,
        min_interval: float = 0.2,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.chain = chain
        self.cache = cache
        self.retries = retries
        self.endpoints = [_Endpoint(u, min_interval) for u in chain.rpc_urls]
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
        self.last_url: str | None = None
        self._id = 0

    def call(
        self,
        method: str,
        params: list[Any],
        *,
        cache: bool = False,
        historical: bool = False,
        only_url: str | None = None,
        require_result: bool = False,
        cache_per_url: bool = False,
    ) -> Any:
        """Call `method` on the first endpoint that answers.

        cache: the answer is immutable (an explicit old block), so store it on disk forever.
        historical: needs archive state. Only endpoints that passed qualify_history are used.
        only_url: pin one endpoint, for source comparison.
        require_result: a null result (for example a receipt a pruned node no longer has)
            moves on to the next endpoint instead of being returned.
        cache_per_url: with only_url, keep a separate cache entry per endpoint, so a check
            that compares two endpoints never reads one endpoint's answer for the other.
        """
        key = DiskCache.key(self.chain.chain_id, method, params)
        if cache_per_url:
            if only_url is None:
                raise ValueError("cache_per_url needs only_url")
            key = DiskCache.key(self.chain.chain_id, method, params, only_url)
        if cache and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.last_url = "cache"
                return hit

        errors: list[str] = []
        range_errors = 0
        state_errors = 0
        endpoints = [e for e in self.endpoints if only_url is None or e.url == only_url]
        if historical:
            if all(e.history_ok is None for e in self.endpoints):
                raise RuntimeError(f"{self.chain.key}: call qualify_history() before state reads")
            endpoints = [e for e in endpoints if e.history_ok]
            if not endpoints:
                raise MissingState(self.chain.key, method, ["no endpoint passed qualify_history"])
        for ep in endpoints:
            outcome = self._try_endpoint(ep, method, params, errors)
            if outcome == "range":
                range_errors += 1
                continue
            if outcome == "state":
                state_errors += 1
                continue
            if outcome == "failed":
                continue
            result = outcome[1]
            if result is None and require_result:
                errors.append(f"{ep.url}: null result")
                continue
            # "0x" can mean "no contract" or a lagging node, so it is never cached.
            if cache and self.cache is not None and result not in (None, "0x"):
                self.cache.put(key, result)
            self.last_url = ep.url
            return result

        if range_errors:
            raise RangeTooLarge(self.chain.key, method, errors)
        if state_errors and state_errors >= len(endpoints):
            raise MissingState(self.chain.key, method, errors)
        raise RpcError(self.chain.key, method, errors)

    def qualify_history(self, probe_address: str, rounds: int = 3) -> dict[str, str]:
        """Decide, per endpoint, whether it really serves historical state.

        `probe_address` must be a contract created long after block 1. A good archive node
        answers "0x" for its code at block 1. Some public nodes ignore the block parameter and
        answer with today's code: they would also return today's totalSupply for any old block,
        so they are excluded from every state read. Nodes that refuse old state are excluded
        too. A node that answers "0x" for pruned state passes this test, which is why creation
        blocks are cross checked against an explorer and supply is reconciled against logs.

        Load balanced endpoints can mix good and bad backends, so the question is asked
        `rounds` times and every answer must be "0x".
        """
        for ep in self.endpoints:
            if ep.url in self.chain.no_history:
                ep.history_ok, ep.history_note = False, "excluded in config (no_history)"
                continue
            ep.history_ok, ep.history_note = True, "serves historical state"
            for _ in range(rounds):
                try:
                    code = self.call("eth_getCode", [probe_address, "0x1"], only_url=ep.url)
                except MissingState as exc:
                    ep.history_ok = False
                    ep.history_note = f"no archive state: {exc.errors[-1][:120]}"
                    break
                except RpcError as exc:
                    ep.history_ok, ep.history_note = False, f"unreachable: {exc.errors[-1][:120]}"
                    break
                if code not in ("0x", "", None):
                    ep.history_ok, ep.history_note = False, "ignores the block parameter"
                    break
        return {ep.url: ep.history_note for ep in self.endpoints}

    def _try_endpoint(
        self, ep: _Endpoint, method: str, params: list[Any], errors: list[str]
    ) -> str | tuple[str, Any]:
        for attempt in range(self.retries):
            ep.wait_turn()
            self._id += 1
            body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            try:
                resp = self.http.post(ep.url, json=body)
            except httpx.HTTPError as exc:
                errors.append(f"{ep.url}: {type(exc).__name__}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                errors.append(f"{ep.url}: HTTP {resp.status_code}")
                time.sleep(2.0 * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                errors.append(f"{ep.url}: HTTP {resp.status_code} non JSON body")
                text = resp.text[:300]
                if _RANGE_PATTERNS.search(text):
                    return "range"
                return "failed"
            if isinstance(data, dict) and "error" in data and data["error"] is not None:
                err = data["error"]
                msg = f"{err.get('code')} {err.get('message')} {err.get('data') or ''}".strip()
                errors.append(f"{ep.url}: {msg[:200]}")
                if method == "eth_call" and "revert" in msg.lower():
                    raise CallReverted(msg)
                if _RATE_PATTERNS.search(msg) or err.get("code") == 429:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if _INVALID_RANGE_PATTERNS.search(msg):
                    return "failed"
                if method == "eth_getLogs" and _RANGE_PATTERNS.search(msg):
                    return "range"
                if _STATE_PATTERNS.search(msg):
                    return "state"
                return "failed"
            if resp.status_code != 200 or not isinstance(data, dict) or "result" not in data:
                errors.append(f"{ep.url}: HTTP {resp.status_code} unexpected body")
                return "failed"
            return ("ok", data["result"])
        return "failed"

    def close(self) -> None:
        self.http.close()
