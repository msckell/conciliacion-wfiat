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
    if args.rerender:
        from cierre.agent.memo import rerender

        old = json.loads((out_dir / "memo.json").read_text(encoding="utf-8"))
        _dump(out_dir / "memo.json", rerender(pkg, old))
        return 0
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
    pkg = build_package(cutoff, out_dir, engine_path)
    return 0 if pkg["all_reconciled"] else 1


def build_package(cutoff: str, out_dir, engine_path, use_overrides: bool = True) -> dict:
    """Classification, bridge pairing and the package, from the engine output. Without
    overrides, the movements the exception agent proved go back to review."""
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
        if use_overrides and exc_path.exists()
        else None,
    )
    _dump(out_dir / "package.json", pkg)
    write_excel(pkg, chains, out_dir / f"paquete_cierre_{cutoff}.xlsx")
    print(
        f"package: all_reconciled={pkg['all_reconciled']} movements={len(pkg['movements'])} "
        f"review={len(pkg['review'])} new_networks={pkg['new_networks']}",
        flush=True,
    )
    return pkg


def cmd_run_close(args: argparse.Namespace) -> int:
    """The whole close, in the order of decision 12, with every step in data/runs.jsonl:
    detect, engine, package, verify, exception agent, memo, publish, tasks, notify.

    With --push the publish step commits the package, waits until the site serves that
    same Excel byte for byte, and only then the tasks are opened and Slack gets the link.
    If any step fails, Slack gets an alert with no link and only the run log is pushed."""
    import os
    from datetime import date

    from cierre.agent.exceptions import run as run_exceptions
    from cierre.agent.llm import ask_claude, model_name
    from cierre.agent.memo import build_memo
    from cierre.agent.tools import Tools
    from cierre.close import last_quarter_end, previous_quarter_end, run_close
    from cierre.golden import OFFICIAL_RULE, compare, load_confirmed
    from cierre.runlog import Run
    from cierre.slack import alert_message, close_message, send
    from cierre.tasks import open_issues

    cutoff = last_quarter_end(date.today()) if args.cutoff == "latest" else args.cutoff
    out_dir = DATA_DIR / "closes" / cutoff
    engine_path = out_dir / "engine.json"
    excel_name = f"paquete_cierre_{cutoff}.xlsx"
    site_url = (args.site_url or os.environ.get("SITE_URL") or "").rstrip("/")
    excel_url = f"{site_url}/data/{excel_name}" if site_url else None
    all_chains = load_chains()
    chains = {k: c for k, c in all_chains.items() if c.in_scope}
    tokens = load_tokens()
    names = {k: c.name for k, c in all_chains.items()}
    run = Run("close", cutoff=cutoff, model=model_name())
    out_dir.mkdir(parents=True, exist_ok=True)
    current = "detect"
    pushed = False
    try:
        with run.step("detect") as s:
            s["counts"] = {
                "cutoff": cutoff,
                "previous_cutoff": previous_quarter_end(cutoff),
                "convention": OFFICIAL_RULE["convention"],
            }
        current = "engine"
        with run.step("engine") as s:
            results = run_close(chains, tokens, cutoff, OFFICIAL_RULE["convention"], DiskCache())
            errors = [r["chain"] for r in results if "error" in r]
            s["counts"] = {"networks_read": len(results), "networks_failed": len(errors)}
            if errors:
                # Kept apart, so a failed run never replaces the last good engine output.
                _dump(out_dir / "engine_failed.json", results)
                raise RuntimeError(f"engine failed on {', '.join(errors)}")
            _dump(engine_path, results)
        current = "package"
        with run.step("package") as s:
            pkg = build_package(cutoff, out_dir, engine_path, use_overrides=False)
            rows = pkg["by_network"]
            s["counts"] = {
                "networks_checked": len(pkg["networks_checked"]),
                "networks_with_contracts": len(pkg["networks"]),
                "movements": len(pkg["movements"]),
                "transactions": len({(m["chain"], m["tx_hash"]) for m in pkg["movements"]}),
                "reconciled": sum(r["passed"] for r in rows),
                "reconciliations": len(rows),
                "bridge_pairs": pkg["bridge"]["pairs_matched"],
                "review": len(pkg["review"]),
                "new_networks": pkg["new_networks"],
                "tokens": len(pkg["tokens"]),
            }
            if not pkg["all_reconciled"]:
                raise RuntimeError("a network does not reconcile")
        current = "verify"
        with run.step("verify") as s:
            supply_rows = [
                r
                for chain in json.loads(
                    (DATA_DIR / "phase0" / "supply_raw.json").read_text("utf-8")
                )
                for r in chain["rows"]
            ]
            rows = compare(load_confirmed(), supply_rows, **OFFICIAL_RULE)
            s["counts"] = {"matched": sum(r["match"] for r in rows), "total": len(rows)}
        current = "exceptions"
        with run.step("exceptions") as s:
            tools = Tools(chains, pkg, DiskCache())
            try:
                exc = run_exceptions(pkg, tools, ask_claude, out_dir)
            finally:
                tools.close()
            _dump(out_dir / "exceptions.json", exc)
            pkg = build_package(cutoff, out_dir, engine_path)
            s["counts"] = {
                "investigated": exc["investigated"],
                "resolved": exc["resolved"],
                "tasks": exc["tasks"],
            }
        current = "memo"
        with run.step("memo") as s:
            memo = build_memo(pkg, ask_claude, out_dir)
            _dump(out_dir / "memo.json", memo)
            expl = [t["explanation"] for t in memo["tokens"].values()]
            s["counts"] = {
                "tokens": len(expl),
                "accepted_first_try": sum(
                    e["attempts"] == 1 and e["source"] == "llm" for e in expl
                ),
                "attempts": sum(e["attempts"] for e in expl),
                "fallbacks": len(memo["fallbacks"]),
            }
        current = "publish"
        with run.step("publish") as s:
            if excel_url is None:
                s["status"] = "skipped"
                s["counts"] = {"reason": "no SITE_URL yet"}
            else:
                if args.push:
                    from cierre.gitops import commit_and_push

                    cmd_site(argparse.Namespace(cutoff=cutoff))
                    commit = commit_and_push(
                        [f"data/closes/{cutoff}", "data/site/site.json"],
                        f"close {cutoff}: package",
                    )
                    s["counts"]["commit"] = commit[:7] if commit else "unchanged"
                    pushed = True
                check = wait_for_file(
                    excel_url, out_dir / excel_name, args.deploy_timeout if args.push else 0
                )
                s["counts"] |= check
                if check["http_status"] != 200:
                    raise RuntimeError(f"Excel not reachable: HTTP {check['http_status']}")
                if args.push and not check["same_file"]:
                    raise RuntimeError(
                        f"the site still serves another Excel after {check['waited_s']} s"
                    )
        current = "tasks"
        with run.step("tasks") as s:
            tasks = open_issues(cutoff, exc, names)
            _dump(out_dir / "tasks.json", tasks)
            s["status"] = "dry_run" if tasks["status"] == "dry_run" else "ok"
            s["counts"] = {
                "tasks": len(tasks["issues"]),
                "opened": sum(i["status"] == "opened" for i in tasks["issues"]),
                "existing": sum(i["status"] == "existing" for i in tasks["issues"]),
            }
        current = "notify"
        with run.step("notify") as s:
            payload = close_message(
                pkg,
                memo,
                exc,
                {"matched": run.record["steps"][3]["counts"]["matched"], "total": len(rows)},
                excel_url or "https://LINK-AL-EXCEL-SE-COMPLETA-AL-PUBLICAR",
                names,
                tasks,
            )
            result = send(payload, out_dir / "slack_payload.json")
            s["status"] = "dry_run" if result.startswith("dry run") else "ok"
            s["counts"] = {"tasks_listed": exc["tasks"]}
    except Exception as exc_:
        record = run.finish("failed")
        published = current in ("tasks", "notify")
        state = "published" if published else "pushed" if pushed else "none"
        send(
            alert_message(current, str(exc_), package=state),
            out_dir / "slack_alert_payload.json",
        )
        _mark_alert_sent()
        print(f"close run failed at {current}: {exc_}", flush=True)
        if args.push:
            _set_aside_failed(cutoff, record["id"], keep_package=pushed)
            _push_run_log(f"close {cutoff}: failed run")
        else:
            cmd_site(argparse.Namespace(cutoff=None))
        return 1
    record = run.finish("ok")
    print(f"close run ok in {record['duration_s']} s", flush=True)
    cmd_site(argparse.Namespace(cutoff=cutoff))
    if args.push:
        from cierre.gitops import commit_and_push

        commit_and_push(
            ["data/runs.jsonl", "data/site/site.json", f"data/closes/{cutoff}"],
            f"close {cutoff}: run log",
        )
    return 0


def wait_for_file(url: str, local, timeout_s: int, every_s: int = 20) -> dict:
    """GET the published file until it is byte for byte the local one, or time runs out.
    With timeout 0 it looks once (local runs that do not push)."""
    import hashlib
    import time

    import httpx

    want = hashlib.sha256(local.read_bytes()).hexdigest()
    t0 = time.monotonic()
    while True:
        try:
            resp = httpx.get(url, timeout=60, follow_redirects=True)
            status = resp.status_code
            same = status == 200 and hashlib.sha256(resp.content).hexdigest() == want
        except httpx.HTTPError:
            status, same = None, False
        waited = round(time.monotonic() - t0)
        if same or waited >= timeout_s:
            return {"url": url, "http_status": status, "same_file": same, "waited_s": waited}
        time.sleep(every_s)


def _set_aside_failed(cutoff: str, run_id: str, keep_package: bool) -> None:
    """Keep what a failed run wrote (in .cache/failed, uploaded by the workflow). Unless the
    package was already published, put the close folder back to its last committed state,
    so nothing half done gets published."""
    import shutil

    from cierre import CACHE_DIR, REPO_ROOT
    from cierre.gitops import is_tracked, restore

    out_dir = DATA_DIR / "closes" / cutoff
    if not out_dir.exists():
        return
    shutil.copytree(out_dir, CACHE_DIR / "failed" / run_id, dirs_exist_ok=True)
    if keep_package:
        return
    for f in out_dir.iterdir():
        rel = f.relative_to(REPO_ROOT).as_posix()
        if is_tracked(rel):
            restore([rel])
        else:
            f.unlink()
    if not any(out_dir.iterdir()):
        out_dir.rmdir()


def cmd_monitor(args: argparse.Namespace) -> int:
    """Daily monitor: supply now, deployments on every candidate network, live golden check
    and CI status. Slack only on a change or a failure. With --push, commits the data."""
    from cierre import monitor
    from cierre.runlog import Run
    from cierre.slack import alert_message, monitor_message, send

    all_chains = load_chains()
    in_scope = {k: c for k, c in all_chains.items() if c.in_scope}
    names = {k: c.name for k, c in all_chains.items()}
    tokens = load_tokens()
    cache = DiskCache()
    previous = monitor.read_previous()
    run = Run("monitor")
    current = {"checked_at": monitor.now(), "networks_checked": list(all_chains)}
    out_dir = DATA_DIR / "monitor"
    out_dir.mkdir(parents=True, exist_ok=True)
    step = "supply"
    try:
        with run.step("supply") as s:
            current["supply"] = monitor.read_supply(in_scope, tokens, cache)
            current["totals"] = monitor.totals(current["supply"])
            s["counts"] = {
                "networks": len(in_scope),
                "networks_failed": sum("error" in v for v in current["supply"].values()),
            }
        step = "deployments"
        with run.step("deployments") as s:
            current["deployments"] = monitor.check_deployments(all_chains, tokens, cache)
            states = [st for t in current["deployments"].values() for st in t.values()]
            s["counts"] = {
                "networks_checked": len(all_chains),
                "deployed": states.count("deployed"),
                "errors": sum(st.startswith("error") for st in states),
            }
        step = "golden_live"
        with run.step("golden_live") as s:
            current["golden_live"] = monitor.golden_live(in_scope, tokens, cache)
            g = current["golden_live"]
            if g["mismatch_without_errors"]:
                # Rule 10: a figure that may contradict a published certificate is never
                # published. Maxi gets a private alert, nothing is committed.
                s["counts"] = {"result": "held for review"}
                raise RuntimeError(
                    "live check differs from a certified figure with every read fine: "
                    + ", ".join(g["mismatch_without_errors"])
                )
            s["counts"] = {"matched": g["matched"], "total": g["total"]}
        step = "ci"
        with run.step("ci") as s:
            import httpx

            try:
                current["ci"] = monitor.ci_status()
            except httpx.HTTPError as exc:
                current["ci"] = {"error": f"{type(exc).__name__}"}
            s["status"] = "ok" if current["ci"] is not None else "skipped"
            s["counts"] = {"conclusion": (current["ci"] or {}).get("conclusion")}
        current["problems"] = monitor.problems_of(current, tokens, names)
        current["changes"] = monitor.diff(previous, current, names)
        current["status"] = "failed" if current["problems"] else "ok"
        step = "notify"
        with run.step("notify") as s:
            if current["changes"] or current["problems"]:
                msg = monitor_message(current["changes"], current["problems"], len(all_chains))
                result = send(msg, out_dir / "slack_payload.json")
                s["status"] = "dry_run" if result.startswith("dry run") else "ok"
                if current["problems"]:
                    _mark_alert_sent()
            else:
                s["status"] = "skipped"
            s["counts"] = {
                "changes": len(current["changes"]),
                "problems": len(current["problems"]),
            }
        current["notified"] = run.record["steps"][-1]["status"]
        monitor.write(current)
    except Exception as exc:
        run.finish("failed")
        send(alert_message(step, str(exc), "El monitor"), out_dir / "slack_alert_payload.json")
        _mark_alert_sent()
        print(f"monitor failed at {step}: {exc}", flush=True)
        if args.push and step != "golden_live":
            _push_run_log("monitor: failed run")
        return 1
    run.finish(current["status"])
    print(
        f"monitor {current['status']}: {len(current['changes'])} changes, "
        f"{len(current['problems'])} problems",
        flush=True,
    )
    if args.push:
        cmd_site(argparse.Namespace(cutoff=None))
        from cierre.gitops import commit_and_push

        commit_and_push(
            ["data/monitor", "data/runs.jsonl", "data/site/site.json"],
            f"monitor: {current['status']} {current['checked_at'][:10]}",
        )
    return 0 if current["status"] == "ok" else 1


def _mark_alert_sent() -> None:
    """The workflow sends its own alert when a step fails, unless the CLI already did."""
    from cierre import CACHE_DIR

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "alert_sent").write_text("1", encoding="utf-8")


def _push_run_log(message: str) -> None:
    """After a failed run: publish only the run log, so the page shows the failure."""
    from cierre.gitops import commit_and_push

    cmd_site(argparse.Namespace(cutoff=None))
    commit_and_push(["data/runs.jsonl", "data/site/site.json"], message)


def cmd_alert(args: argparse.Namespace) -> int:
    """Alert from the workflow when a step failed outside the CLI (setup, git, deploy)."""
    from cierre import CACHE_DIR
    from cierre.slack import alert_message, send

    if (CACHE_DIR / "alert_sent").exists():
        print("alert already sent by the CLI")
        return 0
    out = CACHE_DIR / "workflow_alert_payload.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(send(alert_message(args.step, args.message, args.what), out))
    return 0


def published_cutoff() -> str:
    """The newest close with a package, which the page shows."""
    closes = sorted(p.parent.name for p in (DATA_DIR / "closes").glob("*/package.json"))
    return closes[-1]


def cmd_site(args: argparse.Namespace) -> int:
    """Light JSON for the page, from the close files, the golden checks and the run log."""
    from cierre.site import build_site

    cutoff = getattr(args, "cutoff", None) or published_cutoff()
    _dump(DATA_DIR / "site" / "site.json", build_site(cutoff))
    print(f"site data: {DATA_DIR / 'site' / 'site.json'}")
    return 0


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
    p_memo.add_argument(
        "--rerender", action="store_true", help="only reformat the figures, no LLM call"
    )
    p_close = sub.add_parser("close", help="quarter close package for a cutoff")
    p_close.add_argument("--cutoff", required=True)
    p_close.add_argument("--chains", nargs="+", help="rerun the engine only for these")
    p_close.add_argument("--refresh", action="store_true", help="rerun the engine")
    p_run = sub.add_parser("run-close", help="the whole close, logged step by step")
    p_run.add_argument("--cutoff", required=True, help="YYYY-MM-DD, or latest")
    p_run.add_argument("--site-url", help="published site, to check the Excel link")
    p_run.add_argument(
        "--push", action="store_true", help="commit and push the package, wait for the deploy"
    )
    p_run.add_argument("--deploy-timeout", type=int, default=900, help="seconds")
    p_mon = sub.add_parser("monitor", help="daily monitor (Slack on change or failure)")
    p_mon.add_argument("--push", action="store_true", help="commit and push the data")
    p_alert = sub.add_parser("alert", help="Slack alert for a failed workflow step")
    p_alert.add_argument("--step", required=True)
    p_alert.add_argument("--message", required=True)
    p_alert.add_argument("--what", default="El cierre")
    p_site = sub.add_parser("site", help="light JSON for the page")
    p_site.add_argument("--cutoff", help="default: the newest close with a package")
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
        "run-close": cmd_run_close,
        "monitor": cmd_monitor,
        "alert": cmd_alert,
        "site": cmd_site,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
