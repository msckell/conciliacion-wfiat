"""Expected scope of a close: every token and network pair the close must account for.

The pairs come from the configuration (confirmed tokens times networks in scope), never from
the results that arrived, so a missing network or token cannot disappear from the package.
Each pair gets its applicability from the cutoff blocks and the contract creation block,
before any log is read:

- existing            the contract existed at the opening cutoff
- created_in_period   created after the opening cutoff block and at or before the closing one,
                      so its opening supply is zero and its history starts at creation
- not_applicable      created after the closing cutoff, or the network had no block yet at
                      the cutoff, or the contract is not deployed there, always with evidence
- unknown             the creation block or a cutoff block is not known; never zero, never
                      not_applicable
"""

from __future__ import annotations

import concurrent.futures as cf

from cierre.cache import DiskCache
from cierre.config import Chain, Token
from cierre.cutoffs import CONVENTIONS, cutoff_block
from cierre.rpc import RpcClient

APPLICABLE = ("existing", "created_in_period")


def read_cutoff_blocks(
    chains: dict[str, Chain], days: list[str], cache: DiskCache
) -> dict[str, list[dict] | str]:
    """Cutoff blocks per network for both conventions, or an error string for a network
    whose blocks could not be read."""

    def one(chain: Chain) -> list[dict] | str:
        rpc = RpcClient(chain, cache)
        try:
            return [cutoff_block(rpc, d, conv) for d in days for conv in CONVENTIONS]
        except Exception as exc:  # recorded per network, the others still get their scope
            return f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            rpc.close()

    with cf.ThreadPoolExecutor(max_workers=max(1, len(chains))) as ex:
        return dict(zip(chains, ex.map(one, chains.values()), strict=True))


def _probe(discovery: dict | None, chain: str, symbol: str) -> dict | None:
    for p in (discovery or {}).get("probes", []):
        if p.get("chain") == chain and p.get("token") == symbol:
            return p
    return None


def pair_scope(
    symbol: str,
    token: Token,
    chain: str,
    blocks: list[dict] | str,
    previous_cutoff: str,
    cutoff: str,
    convention: str,
    discovery: dict | None = None,
) -> dict:
    """Applicability of one token on one network, with the evidence behind it."""
    row: dict = {"token": symbol, "chain": chain}
    if isinstance(blocks, str):
        return row | {"applicability": "unknown", "reason": f"cutoff blocks not read: {blocks}"}
    by = {(b["cutoff"], b["convention"]): b for b in blocks}
    close_b, open_b = by.get((cutoff, convention)), by.get((previous_cutoff, convention))
    if close_b is None:
        return row | {"applicability": "unknown", "reason": "closing cutoff block missing"}
    if close_b["block"] is None:
        return row | {
            "applicability": "not_applicable",
            "reason": "network_not_live",
            "evidence": {
                "cutoff_unix": close_b["unix"],
                "block_1_timestamp": close_b["block_1_timestamp"],
            },
        }
    dep = token.on(chain)
    if dep is None:
        probe = _probe(discovery, chain, symbol)
        if probe and probe.get("deployed") is False and not probe.get("error"):
            return row | {
                "applicability": "not_applicable",
                "reason": "not_deployed",
                "evidence": {"probe": "no code at the token address in cierre discover"},
            }
        return row | {"applicability": "unknown", "reason": "no deployment on record"}
    if dep.creation_block is None:
        return row | {"applicability": "unknown", "reason": "no creation block on record"}
    if dep.creation_block > close_b["block"]:
        return row | {
            "applicability": "not_applicable",
            "reason": "created_after_cutoff",
            "evidence": {"creation_block": dep.creation_block, "closing_block": close_b["block"]},
        }
    if open_b is None:
        return row | {"applicability": "unknown", "reason": "opening cutoff block missing"}
    evidence = {
        "creation_block": dep.creation_block,
        "opening_block": open_b["block"],
        "closing_block": close_b["block"],
    }
    if open_b["block"] is None or dep.creation_block > open_b["block"]:
        return row | {
            "applicability": "created_in_period",
            "reason": "network_not_live_at_opening"
            if open_b["block"] is None
            else "created_after_opening",
            "evidence": evidence,
        }
    return row | {"applicability": "existing", "evidence": evidence}


def build_manifest(
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    previous_cutoff: str,
    cutoff: str,
    convention: str,
    blocks: dict[str, list[dict] | str],
    discovery: dict | None = None,
    blocks_source: str = "rpc",
) -> dict:
    """The expected scope. A network in `chains` with no entry in `blocks` gets unknown
    pairs, so the close cannot pass without it."""
    pairs = [
        pair_scope(
            sym,
            tok,
            key,
            blocks.get(key, "not read"),
            previous_cutoff,
            cutoff,
            convention,
            discovery,
        )
        for key in chains
        for sym, tok in tokens.items()
    ]
    counts: dict[str, int] = {}
    for p in pairs:
        counts[p["applicability"]] = counts.get(p["applicability"], 0) + 1
    return {
        "previous_cutoff": previous_cutoff,
        "cutoff": cutoff,
        "convention": convention,
        "blocks_source": blocks_source,
        "chains": list(chains),
        "tokens": list(tokens),
        "pairs": pairs,
        "counts": counts,
        "expected": sum(p["applicability"] in APPLICABLE for p in pairs),
    }


def manifest_from_engine(
    engine: list[dict],
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    convention: str,
    discovery: dict | None = None,
) -> dict:
    """The scope for an engine output saved without one. The pairs still come from the
    configuration: a network missing from the engine output gets unknown pairs."""
    first = next((c for c in engine if "previous_cutoff" in c), None)
    if first is None:
        raise ValueError("engine output has no network with cutoffs, scope unknown")
    blocks = {c["chain"]: c["cutoff_blocks"] for c in engine if c.get("cutoff_blocks")}
    return build_manifest(
        chains,
        tokens,
        first["previous_cutoff"],
        first["cutoff"],
        convention,
        blocks,
        discovery,
        blocks_source="engine.json",
    )


def pairs_for(manifest: dict, chain: str) -> dict[str, dict]:
    return {p["token"]: p for p in manifest["pairs"] if p["chain"] == chain}
