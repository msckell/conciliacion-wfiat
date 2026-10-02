"""Odd answers from public nodes count as a failed endpoint, not as a crash."""

import httpx

from cierre.config import load_chains
from cierre.rpc import RpcClient


def test_error_sent_as_plain_text_moves_to_the_next_endpoint():
    chain = load_chains()["base"]
    first, second = chain.rpc_urls[0], chain.rpc_urls[1]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).rstrip("/") == first.rstrip("/"):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": "upstream down"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x10"})

    rpc = RpcClient(chain, cache=None, min_interval=0, retries=1)
    rpc.http = httpx.Client(transport=httpx.MockTransport(handler))
    assert rpc.call("eth_blockNumber", []) == "0x10"
    assert rpc.last_url == second
