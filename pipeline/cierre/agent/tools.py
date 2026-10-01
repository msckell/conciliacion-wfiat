"""Read only tools for the exception agent. Each returns plain JSON, small enough to put
back in the prompt. None of them writes anything or changes any state."""

from __future__ import annotations

import httpx

from cierre.abi import keccak256, selector, topic
from cierre.blocks import get_block
from cierre.bridge import find_fulfillment_after
from cierre.cache import DiskCache
from cierre.classify import load_contracts, receipt_client
from cierre.config import Chain
from cierre.ledger import TRANSFER_TOPIC, ZERO_TOPIC, RpcLogFetcher
from cierre.rpc import USER_AGENT, RpcClient

FUNCTIONS = {
    selector(s): s
    for s in (
        "mint(address,uint256)",
        "burn(uint256)",
        "burnFrom(address,uint256)",
        "transfer(address,uint256)",
        "depositForBridge(address,uint256,uint256,address,bytes32)",
        "fulfillBridgeMint(address,address,uint256,uint256,bytes32,uint256)",
        "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
        "handleOps((address,uint256,bytes,bytes,bytes32,uint256,bytes32,bytes,bytes)[],address)",
        "multicall(bytes[])",
    )
}
EVENTS = {
    topic(s): s.split("(")[0]
    for s in (
        "Transfer(address,address,uint256)",
        "BridgeDepositInitiated(uint256,address,address,uint256,uint256,uint256,address,bytes32)",
        "BridgeMintFulfilled(address,address,uint256,uint256,bytes32,uint256)",
        "Minted(address,address,address,uint256)",
        "ExecutionSuccess(bytes32,uint256)",
        "UserOperationEvent(bytes32,address,address,uint256,bool,uint256,uint256)",
        "RoleGranted(bytes32,address,address)",
        "Approval(address,address,uint256)",
    )
}
BLOCKSCOUT_V2 = {
    "ethereum": "https://eth.blockscout.com/api/v2",
    "base": "https://base.blockscout.com/api/v2",
    "polygon": "https://polygon.blockscout.com/api/v2",
    "celo": "https://celo.blockscout.com/api/v2",
    "worldchain": "https://worldchain-mainnet.explorer.alchemy.com/api/v2",
}


def _addr(t: str) -> str:
    return "0x" + t[-40:].lower()


class Tools:
    def __init__(self, chains: dict[str, Chain], pkg: dict, cache: DiskCache) -> None:
        self.chains = chains
        self.pkg = pkg
        self.cache = cache
        self.known = load_contracts()
        self.tokens = {}  # address -> symbol, from the package movements and config
        from cierre.config import load_tokens

        for sym, t in load_tokens().items():
            self.tokens[t.address.lower()] = sym
        self.by_symbol = {v: k for k, v in self.tokens.items()}
        self._state: dict[str, RpcClient] = {}

    # -- helpers ------------------------------------------------------------------

    def _rpc(self, chain: str) -> RpcClient:
        return receipt_client(self.chains[chain], self.cache)

    def _state_rpc(self, chain: str) -> RpcClient:
        if chain not in self._state:
            rpc = RpcClient(self.chains[chain], self.cache)
            rpc.qualify_history(self.by_symbol["wARS"])
            self._state[chain] = rpc
        return self._state[chain]

    def close(self) -> None:
        for rpc in self._state.values():
            rpc.close()

    # -- tools --------------------------------------------------------------------

    def get_transaction(self, chain: str, tx_hash: str) -> dict:
        rpc = self._rpc(chain)
        try:
            tx = rpc.call("eth_getTransactionByHash", [tx_hash], cache=True, require_result=True)
            r = rpc.call("eth_getTransactionReceipt", [tx_hash], cache=True, require_result=True)
            block = int(r["blockNumber"], 16)
            ts = get_block(rpc, block).timestamp
        finally:
            rpc.close()
        sel = (tx.get("input") or "0x")[:10]
        logs = []
        for lg in r["logs"]:
            t = [x.lower() for x in lg["topics"]]
            name = EVENTS.get(t[0], "unknown") if t else "anonymous"
            entry = {"emitter": lg["address"].lower(), "event": name}
            if name == "Transfer" and len(t) == 3:
                entry |= {
                    "token": self.tokens.get(lg["address"].lower(), "otro"),
                    "from": _addr(t[1]),
                    "to": _addr(t[2]),
                    "amount_base_units": str(int(lg["data"], 16)),
                }
            else:
                entry |= {"topics": t[:4], "data_prefix": lg["data"][:130]}
            logs.append(entry)
        return {
            "chain": chain,
            "tx_hash": tx_hash.lower(),
            "status": "ok" if int(r["status"], 16) == 1 else "reverted",
            "block": block,
            "timestamp": ts,
            "from": tx["from"].lower(),
            "to": (tx.get("to") or "").lower(),
            "function": FUNCTIONS.get(sel, f"desconocida ({sel})"),
            "logs": logs[:25],
        }

    def contract_info(self, chain: str, address: str) -> dict:
        address = address.lower()
        rpc = self._rpc(chain)
        try:
            code = rpc.call("eth_getCode", [address, "latest"])
        finally:
            rpc.close()
        out: dict = {"chain": chain, "address": address, "is_contract": len(code or "") > 2}
        if out["is_contract"]:
            out["code_hash"] = "0x" + keccak256(bytes.fromhex(code[2:])).hex()
            out["known_as"] = [
                {"role": k.role, "evidence": k.evidence}
                for k in self.known
                if k.chain == chain and k.address == address
            ]
            out["is_wfiat_token"] = self.tokens.get(address)
            base = BLOCKSCOUT_V2.get(chain)
            if base:
                try:
                    resp = httpx.get(
                        f"{base}/smart-contracts/{address}",
                        headers={"User-Agent": USER_AGENT},
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        d = resp.json()
                        out["explorer_name"] = d.get("name")
                        out["explorer_verified"] = bool(d.get("is_verified"))
                    else:
                        out["explorer_name"] = f"sin dato (HTTP {resp.status_code})"
                except httpx.HTTPError as exc:
                    out["explorer_name"] = f"sin dato ({type(exc).__name__})"
        return out

    def has_role(self, chain: str, contract: str, role: str, account: str, block: int) -> dict:
        role_hash = "0x" + keccak256(role.encode()).hex()
        data = selector("hasRole(bytes32,address)") + role_hash[2:] + "0" * 24 + account[2:]
        out = self._state_rpc(chain).call(
            "eth_call",
            [{"to": contract.lower(), "data": data.lower()}, hex(int(block))],
            cache=True,
            historical=True,
        )
        return {
            "chain": chain,
            "contract": contract.lower(),
            "role": role,
            "account": account.lower(),
            "block": int(block),
            "has_role": int(out, 16) == 1,
        }

    def find_mints_to(
        self, chain: str, token: str, to: str, from_block: int, amount_base_units: str = ""
    ) -> dict:
        """Mints of a token to an address from a block on: the quarter from the closing
        package (complete), plus the blocks after the cutoff read from the network."""
        to = to.lower()
        hits = [
            {"tx_hash": m["tx_hash"], "block": m["block"], "amount_base_units": m["amount"]}
            for m in self.pkg["movements"]
            if m["chain"] == chain
            and m["token"] == token
            and m["kind"] == "mint"
            and m["counterparty"].lower() == to
            and m["block"] >= int(from_block)
        ]
        net = next(n for n in self.pkg["networks"] if n["chain"] == chain)
        chain_cfg = self.chains[chain]
        after = net["cutoff_block"] + 1
        searched_after = None
        if chain_cfg.logs_rpc_urls:
            from dataclasses import replace

            lrpc = RpcClient(replace(chain_cfg, rpc_urls=chain_cfg.logs_rpc_urls), None)
            try:
                head = int(lrpc.call("eth_blockNumber", []), 16)
                fetcher = RpcLogFetcher(lrpc, chain_cfg.logs_grid)
                flt = {
                    "address": [self.by_symbol[token]],
                    "topics": [TRANSFER_TOPIC, ZERO_TOPIC, "0x" + "0" * 24 + to[2:]],
                }
                for lg in fetcher.fetch(flt, max(after, int(from_block)), head):
                    hits.append(
                        {
                            "tx_hash": lg["transactionHash"].lower(),
                            "block": int(lg["blockNumber"], 16),
                            "amount_base_units": str(int(lg["data"], 16)),
                        }
                    )
                searched_after = head
            finally:
                lrpc.close()
        if amount_base_units:
            hits = [h for h in hits if h["amount_base_units"] == str(amount_base_units)]
        return {
            "chain": chain,
            "token": token,
            "to": to,
            "from_block": int(from_block),
            "quarter_checked_to_block": net["cutoff_block"],
            "after_cutoff_checked_to_block": searched_after,
            "mints": hits[:20],
        }

    def find_bridge_fulfillment(
        self, dest_chain: str, token: str, source_chain: str, source_tx: str, deposit_id: int
    ) -> dict:
        bridge = next(
            k.address for k in self.known if k.chain == dest_chain and k.role == "bridge_deposit"
        )
        # Inside the quarter, every bridge mint is already in the package. After the
        # cutoff, the destination network is read.
        src_id = str(self.chains[source_chain].chain_id)
        for m in self.pkg["movements"]:
            ev = m.get("evidence") or {}
            if (
                m["chain"] == dest_chain
                and m.get("category") == "bridge_in"
                and ev.get("source_chain_id") == src_id
                and ev.get("source_tx_hash", "")[-64:] == source_tx.lower()[-64:]
                and ev.get("source_deposit_id") == str(deposit_id)
            ):
                return {"status": "found", "tx_hash": m["tx_hash"], "block": m["block"]}
        net = next(n for n in self.pkg["networks"] if n["chain"] == dest_chain)
        return find_fulfillment_after(
            self.chains[dest_chain],
            bridge,
            self.by_symbol[token],
            self.chains[source_chain].chain_id,
            source_tx,
            int(deposit_id),
            net["cutoff_block"] + 1,
            self.cache,
        ) | {"quarter_checked": True}


TOOL_SPECS = {
    "get_transaction": "args {chain, tx_hash}: la transacción, su función y sus eventos "
    "decodificados",
    "contract_info": "args {chain, address}: si es contrato, si figura en la lista de contratos "
    "conocidos y el nombre verificado en el explorador si hay uno",
    "has_role": "args {chain, contract, role, account, block}: si la cuenta tenía ese rol "
    "(por ejemplo MINTER_ROLE) en ese contrato, en ese bloque",
    "find_mints_to": "args {chain, token, to, from_block, amount_base_units opcional}: emisiones "
    "de un token hacia una dirección desde un bloque, en el trimestre y después del corte",
    "find_bridge_fulfillment": "args {dest_chain, token, source_chain, source_tx, deposit_id}: "
    "busca la emisión del puente para ese depósito en la red de destino, en el trimestre y "
    "después del corte",
}
