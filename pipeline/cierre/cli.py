"""Command line entry point: cierre <command>. Parses arguments and calls the modules."""

from __future__ import annotations

import argparse
import sys

from cierre import DATA_DIR, alerts, close_run, monitor, refresh
from cierre.agent.extract import run_eval
from cierre.agent.llm import ask_claude
from cierre.agent.memo import rerender
from cierre.config import load_chains
from cierre.golden import load_confirmed
from cierre.jsonio import dump_json, load_json, load_json_if_exists
from cierre.site import write_site
from cierre.slack import close_message, send


def cmd_discover(args: argparse.Namespace) -> int:
    refresh.discover(args.chains)
    return 0


def cmd_recheck_creation(args: argparse.Namespace) -> int:
    refresh.recheck_creation()
    return 0


def cmd_supply(args: argparse.Namespace) -> int:
    refresh.raw_supply(args.cutoffs or refresh.default_cutoffs())
    return 0


def cmd_movements(args: argparse.Namespace) -> int:
    refresh.movement_test(args.token, args.chains)
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    refresh.golden_check()
    return 0


def cmd_coingecko(args: argparse.Namespace) -> int:
    refresh.coingecko()
    return 0


def _build_package(cutoff: str) -> int:
    """Rebuild the package from the engine output. Exit code 1 when a network does not
    reconcile."""
    pkg = close_run.build_package(cutoff)
    return 0 if pkg["all_reconciled"] else 1


def cmd_close(args: argparse.Namespace) -> int:
    """Quarter close: engine (supply, movements, reconciliation), then the package."""
    if args.refresh or not (close_run.close_dir(args.cutoff) / "engine.json").exists():
        close_run.refresh_engine(args.cutoff, args.chains)
    return _build_package(args.cutoff)


def cmd_memo(args: argparse.Namespace) -> int:
    """Closing memo for a close that already has its package."""
    out_dir = close_run.close_dir(args.cutoff)
    pkg = load_json(out_dir / "package.json")
    if args.rerender:
        dump_json(out_dir / "memo.json", rerender(pkg, load_json(out_dir / "memo.json")))
        return 0
    memo = close_run.write_memo(pkg, out_dir)
    for sym, t in memo["tokens"].items():
        e = t["explanation"]
        print(f"{sym}: {e['source']} after {e['attempts']} attempt(s)", flush=True)
    return 0


def cmd_extract_eval(args: argparse.Namespace) -> int:
    """LLM extraction of every certificate, scored against the confirmed table."""
    result = run_eval(load_confirmed(), ask_claude)
    dump_json(DATA_DIR / "golden" / "extraction_eval.json", result)
    print(f"{result['documents_all_fields_ok']} of {result['documents']} documents fully right")
    print(result["per_field_ok"])
    return 0


def cmd_exceptions(args: argparse.Namespace) -> int:
    """Exception agent over the review items of the engine package, then the package and the
    Excel are rebuilt with the movements it proved."""
    out_dir = close_run.close_dir(args.cutoff)
    result = close_run.investigate(load_json(out_dir / "package.json"), out_dir)
    print(f"resolved {result['resolved']} of {result['investigated']}, tasks {result['tasks']}")
    return _build_package(args.cutoff)


def cmd_slack(args: argparse.Namespace) -> int:
    """Slack message for a close. Dry run (payload to a file) without SLACK_WEBHOOK_URL."""
    out_dir = close_run.close_dir(args.cutoff)
    payload = close_message(
        load_json_if_exists(out_dir / "package.json"),
        load_json_if_exists(out_dir / "memo.json"),
        load_json_if_exists(out_dir / "exceptions.json"),
        load_json(refresh.VERIFICATION),
        args.excel_url,
        {k: c.name for k, c in load_chains().items()},
        load_json_if_exists(out_dir / "tasks.json"),  # each task links the issue the close opened
    )
    print(send(payload, out_dir / "slack_payload.json"))
    return 0


def cmd_run_close(args: argparse.Namespace) -> int:
    return close_run.run(args.cutoff, args.site_url, args.push, args.deploy_timeout)


def cmd_monitor(args: argparse.Namespace) -> int:
    return monitor.run(args.push)


def cmd_alert(args: argparse.Namespace) -> int:
    print(alerts.send_workflow_alert(args.step, args.message, args.what))
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    """Light JSON for the page, from the close files, the golden checks and the run log."""
    print(f"site data: {write_site(args.cutoff)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cierre")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, func, summary: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=summary)
        p.set_defaults(func=func)
        return p

    p = command("discover", cmd_discover, "probe the candidate networks for each token")
    p.add_argument("--chains", nargs="+", help="only these chains, merged into the file")
    command("recheck-creation", cmd_recheck_creation, "redo the explorer check of creation blocks")
    p = command("supply", cmd_supply, "raw totalSupply at each cutoff block")
    p.add_argument("--cutoffs", nargs="+", help="default: every certified cutoff and every close")
    command("golden", cmd_golden, "computed vs confirmed certified figures, every rule")
    command("coingecko", cmd_coingecko, "networks CoinGecko lists for each token")
    p = command("exceptions", cmd_exceptions, "exception agent over the review items")
    p.add_argument("--cutoff", required=True)
    p = command("slack", cmd_slack, "Slack message for a close (dry run without webhook)")
    p.add_argument("--cutoff", required=True)
    p.add_argument("--excel-url", required=True)
    command("extract-eval", cmd_extract_eval, "LLM certificate extraction vs confirmed table")
    p = command("memo", cmd_memo, "closing memo (LLM explanations, verified)")
    p.add_argument("--cutoff", required=True)
    p.add_argument("--rerender", action="store_true", help="only reformat the figures, no LLM call")
    p = command("close", cmd_close, "quarter close package for a cutoff")
    p.add_argument("--cutoff", required=True)
    p.add_argument("--chains", nargs="+", help="rerun the engine only for these")
    p.add_argument("--refresh", action="store_true", help="rerun the engine")
    p = command("run-close", cmd_run_close, "the whole close, logged step by step")
    p.add_argument("--cutoff", required=True, help="YYYY-MM-DD, or latest")
    p.add_argument("--site-url", help="published site, to check the Excel link")
    p.add_argument(
        "--push", action="store_true", help="commit and push the package, wait for the deploy"
    )
    p.add_argument("--deploy-timeout", type=int, default=900, help="seconds")
    p = command("monitor", cmd_monitor, "daily monitor (Slack on change or failure)")
    p.add_argument("--push", action="store_true", help="commit and push the data")
    p = command("alert", cmd_alert, "Slack alert for a failed workflow step")
    p.add_argument("--step", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--what", default="El cierre")
    p = command("site", cmd_site, "light JSON for the page")
    p.add_argument("--cutoff", help="default: the newest close with a package")
    p = command("movements", cmd_movements, "movement history test, opening + mints - burns")
    p.add_argument("--token", default="wARS")
    p.add_argument("--chains", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
