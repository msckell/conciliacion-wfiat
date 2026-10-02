"""Reference data under data/ and the commands that regenerate it: network discovery, raw
supply at each cutoff, the golden comparison grid, the movement history test and the
CoinGecko platforms."""

from __future__ import annotations

import concurrent.futures as cf
import os
import time

from cierre import DATA_DIR, supply
from cierre.cache import DiskCache
from cierre.config import in_scope, load_chains, load_tokens
from cierre.cutoffs import CONVENTIONS
from cierre.discover import Probe, as_dict, coingecko_platforms, cross_check_creation, probe
from cierre.golden import OFFICIAL_RULE, PLACES_RULES, PRECISION_RULES, compare, load_confirmed
from cierre.jsonio import dump_json, load_json
from cierre.movement_test import collect_sources, compare_sources, reconcile
from cierre.rpc import RpcClient
from cierre.runlog import now
from cierre.supply import HISTORY_PROBE_SYMBOL

# Raw supply, the golden grid and the movement tests. The folder name predates this layout.
REFERENCE_DIR = DATA_DIR / "phase0"
SUPPLY_RAW = REFERENCE_DIR / "supply_raw.json"
GOLDEN_GRID = REFERENCE_DIR / "golden_grid.json"
DISCOVERY = DATA_DIR / "discovery.json"
COINGECKO = DATA_DIR / "sources" / "coingecko_platforms.json"
VERIFICATION = DATA_DIR / "golden" / "verification.json"

DISCOVERY_WORKERS = 8  # networks probed in parallel
COINGECKO_PAUSE_S = 3  # between requests, to stay under the free tier rate limit


def default_cutoffs() -> list[str]:
    """Every certified cutoff plus every close that has a package."""
    certified = {c["cutoff"] for c in load_confirmed()}
    closes = {p.parent.name for p in (DATA_DIR / "closes").glob("*/package.json")}
    return sorted(certified | closes)


def read_supply_rows() -> list[dict]:
    """Every row of the raw supply file, across networks."""
    return [r for chain in load_json(SUPPLY_RAW) for r in chain["rows"]]


def recheck_creation() -> None:
    """Rerun only the explorer cross check of creation blocks on data/discovery.json."""
    chains = load_chains()
    disc = load_json(DISCOVERY)
    cache = DiskCache()
    clients: dict[str, RpcClient] = {}
    fields = set(Probe.__dataclass_fields__)
    for row in disc["probes"]:
        if not row["in_scope"] or not row["deployed"] or row.get("creation_check") == "match":
            continue
        chain = chains[row["chain"]]
        rpc = clients.setdefault(chain.key, RpcClient(chain, cache))
        p = Probe(**{k: v for k, v in row.items() if k in fields})
        cross_check_creation(rpc, chain, p)
        row.update(
            {
                "explorer_creation_block": p.explorer_creation_block,
                "explorer_creation_tx": p.explorer_creation_tx,
                "creation_check": p.creation_check,
            }
        )
        print(f"{row['chain']:10} {row['token']:5} {p.creation_check}", flush=True)
    dump_json(DISCOVERY, disc)


def discover(only: list[str] | None) -> None:
    """Probe every candidate network for every token. With `only`, the rows of those
    networks replace the ones in the existing file."""
    chains = load_chains()
    tokens = load_tokens(include_unconfirmed=True)
    cache = DiskCache()

    def run_chain(key: str) -> list[dict]:
        chain = chains[key]
        rpc = RpcClient(chain, cache)
        out = []
        history = {}
        if chain.in_scope:
            history = rpc.qualify_history(tokens[HISTORY_PROBE_SYMBOL].address)
            for url, note in history.items():
                print(f"{key:11} history  {url}: {note}", flush=True)
        for sym, tok in tokens.items():
            p = probe(rpc, chain, tok.address, find_creation=chain.in_scope)
            row = as_dict(p) | {"token": sym, "in_scope": chain.in_scope}
            out.append(row)
            print(
                f"{key:11} {sym:5} deployed={p.deployed} agree={p.endpoints_agreeing} "
                f"{p.symbol or ''} {p.decimals or ''} creation={p.creation_block} "
                f"check={p.creation_check} "
                f"{('ERROR ' + p.error) if p.error else ''}",
                flush=True,
            )
        rpc.close()
        for row in out:
            row["rpc_history"] = history
        return out

    selected = only or list(chains)
    with cf.ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as ex:
        results = [r for rows in ex.map(run_chain, selected) for r in rows]
    if only and DISCOVERY.exists():
        old = load_json(DISCOVERY)["probes"]
        results = [r for r in old if r["chain"] not in selected] + results
    dump_json(DISCOVERY, {"networks_checked": list(chains), "probes": results})


def raw_supply(cutoffs: list[str]) -> None:
    """Raw totalSupply per token, network, cutoff and time convention. No comparison here."""
    chains = in_scope(load_chains())
    tokens = load_tokens()
    cache = DiskCache()
    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        results = list(
            ex.map(
                lambda c: supply.read_chain(c, tokens, cutoffs, cache, log=True), chains.values()
            )
        )
    dump_json(SUPPLY_RAW, results)


def _test_quarter(index: dict, symbol: str, chain: str, cutoffs: list[str]) -> tuple[str, str]:
    """(opening cutoff, closing cutoff) of the first quarter in which the token exists on
    the network. A network launched after the first cutoff is tested from its own launch."""
    quarters = list(zip(cutoffs, cutoffs[1:], strict=False))
    for opening, closing in quarters:
        if index[(symbol, chain, closing, "ART")]["status"] not in (
            "not_created",
            "network_not_live",
        ):
            return opening, closing
    return quarters[-1]


def movement_test(symbol: str, only: list[str] | None) -> None:
    """Movement history test: opening + mints - burns == closing, per network, exact."""
    chains = in_scope(load_chains())
    if only:
        chains = {k: chains[k] for k in only}
    token = load_tokens()[symbol]
    supply_rows = read_supply_rows()
    index = {(r["token"], r["chain"], r["cutoff"], r["convention"]): r for r in supply_rows}
    cutoffs = sorted({r["cutoff"] for r in supply_rows})
    cache = DiskCache()

    def run_chain(key: str) -> dict:
        chain = chains[key]
        creation = token.on(key).creation_block
        open_day, close_day = _test_quarter(index, token.symbol, key, cutoffs)
        windows = {}
        for conv in CONVENTIONS:
            o = index[(token.symbol, key, open_day, conv)]
            c = index[(token.symbol, key, close_day, conv)]
            if c["status"] != "ok":
                return {"chain": key, "passed": False, "error": f"closing supply {c['status']}"}
            if o["status"] == "ok":
                open_block, opening, basis = o["block"], int(o["raw"]), "totalSupply at cutoff"
            elif o["status"] in ("not_created", "network_not_live"):
                open_block, opening = creation - 1, 0
                basis = f"contract created at block {creation}, after the opening cutoff"
            else:
                return {"chain": key, "passed": False, "error": f"opening supply {o['status']}"}
            windows[conv] = (open_block, opening, basis, c["block"], int(c["raw"]))
        lo = min(w[0] for w in windows.values()) + 1
        hi = max(w[3] for w in windows.values())
        print(f"{key:10} logs {lo}..{hi} ({hi - lo + 1} blocks)", flush=True)
        failed: dict[str, str] = {}
        sources = collect_sources(
            chain,
            token,
            lo,
            hi,
            cache,
            failed,
            progress=lambda n, t: print(f"{key:10} {n}/{t} cells", flush=True),
        )
        result = {
            "chain": key,
            "token": token.symbol,
            "range": [lo, hi],
            "sources": {n: len(ms) for n, ms in sources.items()},
            "sources_failed": failed,
            "source_comparison": compare_sources(sources),
            "conventions": {},
        }
        if not sources:
            result |= {"passed": False, "error": "no log source"}
            return result
        # The reconciliation decides which source is complete: a source passes only if it
        # reconciles exactly under every convention. The network passes if one source does.
        complete = set(sources)
        for conv, (ob, opening, basis, cb, closing) in windows.items():
            per_source = {
                n: reconcile(opening, closing, ms, ob + 1, cb) for n, ms in sources.items()
            }
            complete &= {n for n, r in per_source.items() if r["passed"]}
            result["conventions"][conv] = {
                "opening_block": ob,
                "opening_basis": basis,
                "closing_block": cb,
                "by_source": per_source,
            }
        result["sources_complete"] = sorted(complete)
        result["sources_incomplete"] = sorted(set(sources) - complete)
        result["passed"] = bool(complete)
        best = sorted(complete)[0] if complete else next(iter(sources))
        first = result["conventions"]["ART"]["by_source"][best]
        print(
            f"{key:10} {'PASSED' if result['passed'] else 'FAILED'} sources={result['sources']} "
            f"incomplete={result['sources_incomplete']} "
            f"mints={first['n_mints']} burns={first['n_burns']} diff={first['difference']}",
            flush=True,
        )
        return result

    def safe(key: str) -> dict:
        try:
            return run_chain(key)
        except Exception as exc:  # one network failing must not hide the others
            print(f"{key:10} ERROR {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            return {"chain": key, "passed": False, "error": f"{type(exc).__name__}: {exc}"}

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        results = list(ex.map(safe, list(chains)))
    path = REFERENCE_DIR / f"movement_test_{token.symbol}.json"
    if only and path.exists():
        results = [r for r in load_json(path) if r["chain"] not in chains] + results
    dump_json(path, results)


def golden_check() -> None:
    """Computed vs certified under every rule, and the official rule's result. Refuses to
    run on figures a person has not confirmed."""
    certs = load_confirmed()
    supply_rows = read_supply_rows()
    grid = {}
    for conv in CONVENTIONS:
        for nets in ("all_checked", "listed_in_certificate"):
            for prec in PRECISION_RULES:
                for places in PLACES_RULES:
                    rows = compare(certs, supply_rows, conv, nets, prec, places)
                    grid[f"{conv}|{nets}|{prec}|{places}"] = rows
                    n = sum(r["match"] for r in rows)
                    print(f"{conv} {nets:22} {prec:14} {places:11} -> {n} of {len(rows)} match")
    dump_json(GOLDEN_GRID, grid)
    official = compare(certs, supply_rows, **OFFICIAL_RULE)
    n_ok = sum(r["match"] for r in official)
    dump_json(
        VERIFICATION,
        {"rule": OFFICIAL_RULE, "matched": n_ok, "total": len(official), "rows": official},
    )
    print(f"official rule {OFFICIAL_RULE} -> {n_ok} of {len(official)}")


def coingecko() -> None:
    """Networks CoinGecko lists for each confirmed token, for the discovery cross check."""
    out = {"retrieved_at": now(), "tokens": {}}
    for sym, tok in load_tokens().items():
        if tok.coingecko_id:
            out["tokens"][sym] = coingecko_platforms(
                tok.coingecko_id, os.environ.get("COINGECKO_API_KEY")
            )
            print(sym, sorted(out["tokens"][sym]["platforms"]), flush=True)
            time.sleep(COINGECKO_PAUSE_S)
    dump_json(COINGECKO, out)
