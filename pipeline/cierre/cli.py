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

    chains = {k: c for k, c in load_chains().items() if c.in_scope}
    tokens = load_tokens()
    cache = DiskCache()

    def run_chain(key: str) -> dict:
        return supply.read_chain(chains[key], tokens, args.cutoffs, cache, log=True)

    with cf.ThreadPoolExecutor(max_workers=len(chains)) as ex:
        results = list(ex.map(run_chain, list(chains)))
    _dump(DATA_DIR / "phase0" / "supply_raw.json", results)
    return 0


def cmd_movements(args: argparse.Namespace) -> int:
    """Phase 0 movement test: opening + mints - burns == closing, per network, exact."""
    from cierre.movement_test import collect_sources, compare_sources, reconcile

    chains = {k: c for k, c in load_chains().items() if c.in_scope}
    if args.chains:
        chains = {k: chains[k] for k in args.chains}
    token = load_tokens()[args.token]
    supply_rows = json.loads((DATA_DIR / "phase0" / "supply_raw.json").read_text("utf-8"))
    index = {
        (r["token"], r["chain"], r["cutoff"], r["convention"]): r
        for chain in supply_rows
        for r in chain["rows"]
    }
    cache = DiskCache()

    def run_chain(key: str) -> dict:
        chain = chains[key]
        creation = token.on(key).creation_block
        open_day, close_day = (
            ("2026-06-30", "2026-09-30") if key == "arc" else ("2026-03-31", "2026-06-30")
        )
        windows = {}
        for conv in ("ART", "UTC"):
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
    path = DATA_DIR / "phase0" / f"movement_test_{token.symbol}.json"
    if args.chains and path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        results = [r for r in old if r["chain"] not in chains] + results
    _dump(path, results)
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    """Computed vs certified. Refuses to run on figures a person has not confirmed."""
    from cierre.golden import OFFICIAL_RULE, PLACES_RULES, PRECISION_RULES, compare, load_confirmed

    certs = load_confirmed()
    supply_rows = [
        r
        for chain in json.loads((DATA_DIR / "phase0" / "supply_raw.json").read_text("utf-8"))
        for r in chain["rows"]
    ]
    grid = {}
    for conv in ("ART", "UTC"):
        for nets in ("all_checked", "listed_in_certificate"):
            for prec in PRECISION_RULES:
                for places in PLACES_RULES:
                    rows = compare(certs, supply_rows, conv, nets, prec, places)
                    grid[f"{conv}|{nets}|{prec}|{places}"] = rows
                    n = sum(r["match"] for r in rows)
                    print(f"{conv} {nets:22} {prec:14} {places:11} -> {n} of {len(rows)} match")
    _dump(DATA_DIR / "phase0" / "golden_grid.json", grid)
    official = compare(certs, supply_rows, **OFFICIAL_RULE)
    _dump(
        DATA_DIR / "golden" / "verification.json",
        {
            "rule": OFFICIAL_RULE,
            "matched": sum(r["match"] for r in official),
            "total": len(official),
            "rows": official,
        },
    )
    n_ok = sum(r["match"] for r in official)
    print(f"official rule {OFFICIAL_RULE} -> {n_ok} of {len(official)}")
    return 0


def cmd_coingecko(args: argparse.Namespace) -> int:
    """Networks CoinGecko lists for each confirmed token, for the discovery cross check."""
    import os
    import time
    from datetime import UTC, datetime

    from cierre.discover import coingecko_platforms

    out = {"retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "tokens": {}}
    for sym, tok in load_tokens().items():
        if tok.coingecko_id:
            out["tokens"][sym] = coingecko_platforms(
                tok.coingecko_id, os.environ.get("COINGECKO_API_KEY")
            )
            print(sym, sorted(out["tokens"][sym]["platforms"]), flush=True)
            time.sleep(3)
    _dump(DATA_DIR / "sources" / "coingecko_platforms.json", out)
    return 0


def cmd_memo(args: argparse.Namespace) -> int:
    """Closing memo for a close that already has its package."""
    from cierre.agent.llm import ask_claude
    from cierre.agent.memo import build_memo

    out_dir = DATA_DIR / "closes" / args.cutoff
    pkg = json.loads((out_dir / "package.json").read_text(encoding="utf-8"))
    memo = build_memo(pkg, ask_claude, out_dir)
    _dump(out_dir / "memo.json", memo)
    for sym, t in memo["tokens"].items():
        e = t["explanation"]
        print(f"{sym}: {e['source']} after {e['attempts']} attempt(s)", flush=True)
    return 0


def cmd_extract_eval(args: argparse.Namespace) -> int:
    """LLM extraction of every certificate, scored against the confirmed table."""
    from cierre.agent.extract import run_eval
    from cierre.agent.llm import ask_claude
    from cierre.golden import load_confirmed

    result = run_eval(load_confirmed(), ask_claude)
    _dump(DATA_DIR / "golden" / "extraction_eval.json", result)
    print(f"{result['documents_all_fields_ok']} of {result['documents']} documents fully right")
    print(result["per_field_ok"])
    return 0


def cmd_exceptions(args: argparse.Namespace) -> int:
    """Exception agent over the review items of the engine package. The package and the
    Excel are rebuilt afterwards, with the movements it proved."""
    from cierre.agent.exceptions import run
    from cierre.agent.llm import ask_claude
    from cierre.agent.tools import Tools

    out_dir = DATA_DIR / "closes" / args.cutoff
    pkg = json.loads((out_dir / "package.json").read_text(encoding="utf-8"))
    chains = {k: c for k, c in load_chains().items() if c.in_scope}
    tools = Tools(chains, pkg, DiskCache())
    try:
        result = run(pkg, tools, ask_claude, out_dir)
    finally:
        tools.close()
    _dump(out_dir / "exceptions.json", result)
    print(f"resolved {result['resolved']} of {result['investigated']}, tasks {result['tasks']}")
    return _package(args.cutoff, out_dir, out_dir / "engine.json")


def cmd_slack(args: argparse.Namespace) -> int:
    """Slack message for a close. Dry run (payload to a file) without SLACK_WEBHOOK_URL."""
    from cierre.slack import close_message, send

    out_dir = DATA_DIR / "closes" / args.cutoff

    def read(name: str):
        path = out_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    payload = close_message(
        read("package.json"),
        read("memo.json"),
        read("exceptions.json"),
        json.loads((DATA_DIR / "golden" / "verification.json").read_text(encoding="utf-8")),
        args.excel_url,
        {k: c.name for k, c in load_chains().items()},
    )
    print(send(payload, out_dir / "slack_payload.json"))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    """Quarter close: engine (supply, movements, reconciliation), then the package."""
    from cierre.close import run_close
    from cierre.golden import OFFICIAL_RULE

    out_dir = DATA_DIR / "closes" / args.cutoff
    engine_path = out_dir / "engine.json"
    if args.refresh or not engine_path.exists():
        chains = {k: c for k, c in load_chains().items() if c.in_scope}
        if args.chains:
            chains = {k: chains[k] for k in args.chains}
        results = run_close(
            chains, load_tokens(), args.cutoff, OFFICIAL_RULE["convention"], DiskCache()
        )
        if args.chains and engine_path.exists():
            old = json.loads(engine_path.read_text(encoding="utf-8"))
            results = [r for r in old if r["chain"] not in chains] + results
        _dump(engine_path, results)
    return _package(args.cutoff, out_dir, engine_path)


def _package(cutoff: str, out_dir, engine_path) -> int:
    """Classification, bridge pairing and the package, from the engine output."""
    import yaml

    from cierre import CONFIG_DIR
    from cierre.agent.exceptions import overrides
    from cierre.bridge import match
    from cierre.close import classify_all
    from cierre.golden import OFFICIAL_RULE, load_confirmed
    from cierre.package import build, write_excel

    exc_path = out_dir / "exceptions.json"
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    chains = {k: c for k, c in load_chains().items() if c.in_scope}
    tokens = load_tokens()
    cache = DiskCache()
    report = classify_all(engine, chains, tokens, cache)
    bridge_summary = match(engine, chains, report.pop("known"), cache)
    bridge_summary["contracts"] = report
    methodology = yaml.safe_load((CONFIG_DIR / "methodology.yaml").read_text(encoding="utf-8"))[
        "methodology"
    ]
    pkg = build(
        engine,
        chains,
        tokens,
        json.loads((DATA_DIR / "discovery.json").read_text(encoding="utf-8")),
        json.loads((DATA_DIR / "sources" / "coingecko_platforms.json").read_text(encoding="utf-8")),
        load_confirmed(),
        bridge_summary,
        methodology,
        OFFICIAL_RULE["convention"],
        all_chains=load_chains(),
        overrides=overrides(json.loads(exc_path.read_text(encoding="utf-8")))
        if exc_path.exists()
        else None,
    )
    _dump(out_dir / "package.json", pkg)
    write_excel(pkg, chains, out_dir / f"paquete_cierre_{cutoff}.xlsx")
    print(
        f"package: all_reconciled={pkg['all_reconciled']} movements={len(pkg['movements'])} "
        f"review={len(pkg['review'])} new_networks={pkg['new_networks']}",
        flush=True,
    )
    return 0 if pkg["all_reconciled"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cierre")
    sub = parser.add_subparsers(dest="command", required=True)
    p_disc = sub.add_parser("discover", help="probe the candidate networks for each token")
    p_disc.add_argument("--chains", nargs="+", help="only these chains, merged into the file")
    sub.add_parser("recheck-creation", help="redo the explorer check of creation blocks")
    p_supply = sub.add_parser("supply", help="raw totalSupply at each cutoff block")
    p_supply.add_argument("--cutoffs", nargs="+", default=CUTOFFS)
    sub.add_parser("golden", help="computed vs confirmed certified figures, every rule")
    sub.add_parser("coingecko", help="networks CoinGecko lists for each token")
    p_exc = sub.add_parser("exceptions", help="exception agent over the review items")
    p_exc.add_argument("--cutoff", required=True)
    p_slack = sub.add_parser("slack", help="Slack message for a close (dry run without webhook)")
    p_slack.add_argument("--cutoff", required=True)
    p_slack.add_argument("--excel-url", required=True)
    sub.add_parser("extract-eval", help="LLM certificate extraction vs confirmed table")
    p_memo = sub.add_parser("memo", help="closing memo (LLM explanations, verified)")
    p_memo.add_argument("--cutoff", required=True)
    p_close = sub.add_parser("close", help="quarter close package for a cutoff")
    p_close.add_argument("--cutoff", required=True)
    p_close.add_argument("--chains", nargs="+", help="rerun the engine only for these")
    p_close.add_argument("--refresh", action="store_true", help="rerun the engine")
    p_mov = sub.add_parser("movements", help="phase 0 movement history test")
    p_mov.add_argument("--token", default="wARS")
    p_mov.add_argument("--chains", nargs="+")
    args = parser.parse_args(argv)
    commands = {
        "discover": cmd_discover,
        "recheck-creation": cmd_recheck_creation,
        "supply": cmd_supply,
        "movements": cmd_movements,
        "golden": cmd_golden,
        "close": cmd_close,
        "coingecko": cmd_coingecko,
        "memo": cmd_memo,
        "extract-eval": cmd_extract_eval,
        "exceptions": cmd_exceptions,
        "slack": cmd_slack,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
