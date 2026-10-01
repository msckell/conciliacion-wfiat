"""Command line entry point: cierre <command>."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys

from cierre import DATA_DIR
from cierre.cache import DiskCache
from cierre.config import load_chains, load_tokens
from cierre.rpc import RpcClient


def _dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def cmd_recheck_creation(args: argparse.Namespace) -> int:
    """Rerun only the explorer cross check of creation blocks on data/discovery.json."""
    from cierre.discover import Probe, _cross_check_creation

    chains = load_chains()
    path = DATA_DIR / "discovery.json"
    disc = json.loads(path.read_text(encoding="utf-8"))
    cache = DiskCache()
    clients: dict[str, RpcClient] = {}
    fields = set(Probe.__dataclass_fields__)
    for row in disc["probes"]:
        if not row["in_scope"] or not row["deployed"] or row.get("creation_check") == "match":
            continue
        chain = chains[row["chain"]]
        rpc = clients.setdefault(chain.key, RpcClient(chain, cache))
        p = Probe(**{k: v for k, v in row.items() if k in fields})
        _cross_check_creation(rpc, chain, p)
        row.update(
            {
                "explorer_creation_block": p.explorer_creation_block,
                "explorer_creation_tx": p.explorer_creation_tx,
                "creation_check": p.creation_check,
            }
        )
        print(f"{row['chain']:10} {row['token']:5} {p.creation_check}", flush=True)
    _dump(path, disc)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from cierre.discover import as_dict, probe

    chains = load_chains()
    tokens = load_tokens(include_unconfirmed=True)
    cache = DiskCache()

    def run_chain(key: str) -> list[dict]:
        chain = chains[key]
        rpc = RpcClient(chain, cache)
        out = []
        history = {}
        if chain.in_scope:
            history = rpc.qualify_history(tokens["wARS"].address)
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

    selected = args.chains or list(chains)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = [r for rows in ex.map(run_chain, selected) for r in rows]
    path = DATA_DIR / "discovery.json"
    if args.chains and path.exists():
        # Partial rerun: replace only the rows of the chains that were probed again.
        old = json.loads(path.read_text(encoding="utf-8"))["probes"]
        results = [r for r in old if r["chain"] not in selected] + results
    _dump(path, {"networks_checked": list(chains), "probes": results})
    return 0


CUTOFFS = ["2026-03-31", "2026-06-30", "2026-09-30"]


def cmd_supply(args: argparse.Namespace) -> int:
    """Raw totalSupply per token, network, cutoff and time convention. No comparison here."""
    from cierre import supply
    from cierre.cutoffs import CONVENTIONS, cutoff_block

    chains = {k: c for k, c in load_chains().items() if c.in_scope}
    tokens = load_tokens()
    cache = DiskCache()

    def run_chain(key: str) -> dict:
        chain = chains[key]
        rpc = RpcClient(chain, cache)
        history = rpc.qualify_history(tokens["wARS"].address)
        good = [ep.url for ep in rpc.endpoints if ep.history_ok]
        blocks, rows = [], []
        for day in args.cutoffs:
            for conv in CONVENTIONS:
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
                    if dep is None or dep.creation_block is None:
                        row |= {"status": "error", "error": "no creation block on record"}
                    elif dep.creation_block > cb["block"]:
                        row |= {
                            "status": "not_created",
                            "raw": "0",
                            "creation_block": dep.creation_block,
                        }
                    else:
                        reads = {}
                        for url in good[:2]:
                            try:
                                reads[url] = str(
                                    int(
                                        rpc.call(
                                            "eth_call",
                                            [
                                                {
                                                    "to": tok.address,
                                                    "data": supply.SEL_TOTAL_SUPPLY,
                                                },
                                                hex(cb["block"]),
                                            ],
                                            only_url=url,
                                            historical=True,
                                        ),
                                        16,
                                    )
                                )
                            except Exception as exc:  # recorded, never turned into a zero
                                reads[url] = f"error: {type(exc).__name__}: {str(exc)[:160]}"
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
                    print(
                        f"{key:10} {day} {conv} blk={cb['block']} {sym:5} "
                        f"{row['status']:11} {row.get('raw', row.get('error'))}",
                        flush=True,
                    )
        rpc.close()
        return {"chain": key, "history": history, "cutoff_blocks": blocks, "rows": rows}

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        results = list(ex.map(run_chain, list(chains)))
    _dump(DATA_DIR / "phase0" / "supply_raw.json", results)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cierre")
    sub = parser.add_subparsers(dest="command", required=True)
    p_disc = sub.add_parser("discover", help="probe the candidate networks for each token")
    p_disc.add_argument("--chains", nargs="+", help="only these chains, merged into the file")
    sub.add_parser("recheck-creation", help="redo the explorer check of creation blocks")
    p_supply = sub.add_parser("supply", help="raw totalSupply at each cutoff block")
    p_supply.add_argument("--cutoffs", nargs="+", default=CUTOFFS)
    args = parser.parse_args(argv)
    commands = {
        "discover": cmd_discover,
        "recheck-creation": cmd_recheck_creation,
        "supply": cmd_supply,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
