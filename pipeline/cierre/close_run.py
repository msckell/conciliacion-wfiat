"""The quarter close from start to finish, with every step in the run log.

Steps, in order: detect, engine, package, verify, exceptions, memo, publish, tasks, notify.
The Excel is committed and served before anyone is told about it, and a failure at any step
sends an alert that carries no link.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from datetime import date
from pathlib import Path

import httpx
import yaml

from cierre import CACHE_DIR, CONFIG_DIR, DATA_DIR
from cierre.agent import exceptions as exception_agent
from cierre.agent.llm import ask_claude, model_name
from cierre.agent.memo import build_memo
from cierre.agent.tools import Tools
from cierre.alerts import send_failure
from cierre.bridge import match
from cierre.cache import DiskCache
from cierre.close import classify_all, last_quarter_end, previous_quarter_end, run_close
from cierre.config import in_scope, load_chains, load_tokens
from cierre.gitops import commit_and_push, is_tracked, restore
from cierre.golden import OFFICIAL_RULE, compare, load_confirmed
from cierre.jsonio import dump_json, load_json
from cierre.package import build, write_excel
from cierre.refresh import COINGECKO, DISCOVERY, read_supply_rows
from cierre.runlog import RUNS_PATH, Run
from cierre.site import publish_run_log, write_site
from cierre.slack import alert_message, close_message, send
from cierre.tasks import open_issues

# Stands in for the Excel link in the dry run Slack payload when no site is configured. The
# page swaps any link that starts with https://LINK for its own Excel (site/src/Slack.tsx).
EXCEL_URL_PLACEHOLDER = "https://LINK-AL-EXCEL-SE-COMPLETA-AL-PUBLICAR"


def close_dir(cutoff: str) -> Path:
    return DATA_DIR / "closes" / cutoff


def refresh_engine(cutoff: str, only: list[str] | None = None) -> None:
    """Run the engine and save engine.json. With `only`, just those networks are rerun and
    merged into the existing file."""
    engine_path = close_dir(cutoff) / "engine.json"
    chains = in_scope(load_chains())
    if only:
        chains = {k: chains[k] for k in only}
    results = run_close(chains, load_tokens(), cutoff, OFFICIAL_RULE["convention"], DiskCache())
    if only and engine_path.exists():
        results = [r for r in load_json(engine_path) if r["chain"] not in chains] + results
    dump_json(engine_path, results)


def build_package(cutoff: str, use_overrides: bool = True) -> dict:
    """Classification, bridge pairing and the package, from the engine output. Without
    overrides, the movements the exception agent proved go back to review."""
    out_dir = close_dir(cutoff)
    exceptions_path = out_dir / "exceptions.json"
    engine = load_json(out_dir / "engine.json")
    all_chains = load_chains()
    chains = in_scope(all_chains)
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
        load_json(DISCOVERY),
        load_json(COINGECKO),
        load_confirmed(),
        bridge_summary,
        methodology,
        OFFICIAL_RULE["convention"],
        all_chains=all_chains,
        overrides=exception_agent.overrides(load_json(exceptions_path))
        if use_overrides and exceptions_path.exists()
        else None,
    )
    dump_json(out_dir / "package.json", pkg)
    write_excel(pkg, chains, out_dir / f"paquete_cierre_{cutoff}.xlsx")
    print(
        f"package: all_reconciled={pkg['all_reconciled']} movements={len(pkg['movements'])} "
        f"review={len(pkg['review'])} new_networks={pkg['new_networks']}",
        flush=True,
    )
    return pkg


def investigate(pkg: dict, out_dir: Path) -> dict:
    """The exception agent over the review items of the package. Saves exceptions.json."""
    chains = in_scope(load_chains())
    tools = Tools(chains, pkg, DiskCache())
    try:
        result = exception_agent.run(pkg, tools, ask_claude, out_dir)
    finally:
        tools.close()
    dump_json(out_dir / "exceptions.json", result)
    return result


def write_memo(pkg: dict, out_dir: Path) -> dict:
    """The closing memo, with the LLM explanations checked. Saves memo.json."""
    memo = build_memo(pkg, ask_claude, out_dir)
    dump_json(out_dir / "memo.json", memo)
    return memo


def verify_certificates() -> dict:
    """The official rule against the confirmed figure of every published certificate."""
    rows = compare(load_confirmed(), read_supply_rows(), **OFFICIAL_RULE)
    return {"matched": sum(r["match"] for r in rows), "total": len(rows)}


def wait_for_file(url: str, local: Path, timeout_s: int, every_s: int = 20) -> dict:
    """GET the published file until it is byte for byte the local one, or time runs out.
    With timeout 0 it looks once (local runs that do not push)."""
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


def set_aside_failed(cutoff: str, run_id: str, keep_package: bool) -> None:
    """Keep what a failed run wrote (in .cache/failed, uploaded by the workflow). Unless the
    package was already published, put the close folder back to its last committed state,
    so nothing half done gets published."""
    out_dir = close_dir(cutoff)
    if not out_dir.exists():
        return
    shutil.copytree(out_dir, CACHE_DIR / "failed" / run_id, dirs_exist_ok=True)
    if keep_package:
        return
    for f in out_dir.iterdir():
        if is_tracked(f):
            restore([f])
        else:
            f.unlink()
    if not any(out_dir.iterdir()):
        out_dir.rmdir()


def _package_counts(pkg: dict) -> dict:
    rows = pkg["by_network"]
    return {
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


def _memo_counts(memo: dict) -> dict:
    explanations = [t["explanation"] for t in memo["tokens"].values()]
    return {
        "tokens": len(explanations),
        "accepted_first_try": sum(
            e["attempts"] == 1 and e["source"] == "llm" for e in explanations
        ),
        "attempts": sum(e["attempts"] for e in explanations),
        "fallbacks": len(memo["fallbacks"]),
    }


def resolve_cutoff(cutoff: str) -> str:
    """A cutoff date as given, or the last quarter end for "latest"."""
    return last_quarter_end(date.today()) if cutoff == "latest" else cutoff


def run(cutoff: str, site_url: str | None, push: bool, deploy_timeout: int) -> int:
    """The whole close for `cutoff` (a date, or "latest" for the last quarter end). Returns
    the exit code.

    With `push` the publish step commits the package, waits until the site serves that same
    Excel byte for byte, and only then the tasks are opened and Slack gets the link. If any
    step fails, Slack gets an alert with no link and only the run log is pushed."""
    cutoff = resolve_cutoff(cutoff)
    out_dir = close_dir(cutoff)
    excel_name = f"paquete_cierre_{cutoff}.xlsx"
    site_url = (site_url or os.environ.get("SITE_URL") or "").rstrip("/")
    excel_url = f"{site_url}/data/{excel_name}" if site_url else None
    all_chains = load_chains()
    chains = in_scope(all_chains)
    names = {k: c.name for k, c in all_chains.items()}
    run_log = Run("close", cutoff=cutoff, model=model_name())
    out_dir.mkdir(parents=True, exist_ok=True)
    pushed = published = False
    try:
        with run_log.step("detect") as s:
            s["counts"] = {
                "cutoff": cutoff,
                "previous_cutoff": previous_quarter_end(cutoff),
                "convention": OFFICIAL_RULE["convention"],
            }
        with run_log.step("engine") as s:
            results = run_close(
                chains, load_tokens(), cutoff, OFFICIAL_RULE["convention"], DiskCache()
            )
            errors = [r["chain"] for r in results if "error" in r]
            s["counts"] = {"networks_read": len(results), "networks_failed": len(errors)}
            if errors:
                # Kept apart, so a failed run does not replace the last good engine output.
                dump_json(out_dir / "engine_failed.json", results)
                raise RuntimeError(f"engine failed on {', '.join(errors)}")
            dump_json(out_dir / "engine.json", results)
        with run_log.step("package") as s:
            pkg = build_package(cutoff, use_overrides=False)
            s["counts"] = _package_counts(pkg)
            if not pkg["all_reconciled"]:
                raise RuntimeError("a network does not reconcile")
        with run_log.step("verify") as s:
            verification = verify_certificates()
            s["counts"] = verification
        with run_log.step("exceptions") as s:
            exceptions = investigate(pkg, out_dir)
            pkg = build_package(cutoff)
            s["counts"] = {
                "investigated": exceptions["investigated"],
                "resolved": exceptions["resolved"],
                "tasks": exceptions["tasks"],
            }
        with run_log.step("memo") as s:
            memo = write_memo(pkg, out_dir)
            s["counts"] = _memo_counts(memo)
        with run_log.step("publish") as s:
            if excel_url is None:
                s["status"] = "skipped"
                s["counts"] = {"reason": "no SITE_URL yet"}
            else:
                if push:
                    site_json = write_site(cutoff)
                    commit = commit_and_push([out_dir, site_json], f"close {cutoff}: package")
                    s["counts"]["commit"] = commit[:7] if commit else "unchanged"
                    pushed = True
                check = wait_for_file(
                    excel_url, out_dir / excel_name, deploy_timeout if push else 0
                )
                s["counts"] |= check
                if check["http_status"] != 200:
                    raise RuntimeError(f"Excel not reachable: HTTP {check['http_status']}")
                if push and not check["same_file"]:
                    raise RuntimeError(
                        f"the site still serves another Excel after {check['waited_s']} s"
                    )
                # Only a pushed package that the site now serves counts as published.
                published = push
        with run_log.step("tasks") as s:
            tasks = open_issues(cutoff, exceptions, names)
            dump_json(out_dir / "tasks.json", tasks)
            s["status"] = "dry_run" if tasks["status"] == "dry_run" else "ok"
            s["counts"] = {
                "tasks": len(tasks["issues"]),
                "opened": sum(i["status"] == "opened" for i in tasks["issues"]),
                "existing": sum(i["status"] == "existing" for i in tasks["issues"]),
            }
        with run_log.step("notify") as s:
            payload = close_message(
                pkg,
                memo,
                exceptions,
                verification,
                excel_url or EXCEL_URL_PLACEHOLDER,
                names,
                tasks,
            )
            result = send(payload, out_dir / "slack_payload.json")
            s["status"] = "dry_run" if result.startswith("dry run") else "ok"
            s["counts"] = {"tasks_listed": exceptions["tasks"]}
    except Exception as error:
        record = run_log.finish("failed")
        failed = run_log.last_step
        state = "published" if published else "pushed" if pushed else "none"
        send_failure(
            alert_message(failed, str(error), package=state),
            out_dir / "slack_alert_payload.json",
        )
        print(f"close run failed at {failed}: {error}", flush=True)
        if push:
            set_aside_failed(cutoff, record["id"], keep_package=pushed)
            publish_run_log(f"close {cutoff}: failed run")
        else:
            write_site()
        return 1
    record = run_log.finish("ok")
    print(f"close run ok in {record['duration_s']} s", flush=True)
    site_json = write_site(cutoff)
    if push:
        commit_and_push([RUNS_PATH, site_json, out_dir], f"close {cutoff}: run log")
    return 0
