"""Production pieces, offline: monitor change detection, GitHub tasks without duplicates,
the scheduled cutoff, the schedule text on the page and the order of the close steps."""

import json
import re
import shlex
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

import cierre.alerts as alerts
import cierre.close_run as close_run
import cierre.gitops as gitops
import cierre.monitor as monitor
import cierre.refresh as refresh
import cierre.runlog as runlog
import cierre.slack as slack
from cierre import REPO_ROOT, cli
from cierre.config import load_chains, load_tokens
from cierre.cutoffs import last_quarter_end
from cierre.monitor import diff, problems_of, totals
from cierre.site import _cron_text
from cierre.slack import close_message, task_line
from cierre.tasks import issue_for, marker, open_issues

NAMES = {"base": "Base", "arc": "Arc", "zksync": "zkSync"}
TOKENS = {"wARS": SimpleNamespace(on=lambda chain: chain in ("base", "arc") or None)}


def _state(deployments, golden=(9, 9), supply=None, problems=None):
    return {
        "supply": supply or {"base": {"block": 1, "tokens": {"wARS": "5"}}},
        "deployments": deployments,
        "golden_live": {"matched": golden[0], "total": golden[1], "read_errors": []},
        "problems": problems or [],
    }


def test_monitor_flags_a_new_deployment_and_a_golden_change():
    before = _state({"zksync": {"wARS": "absent"}})
    now = _state({"zksync": {"wARS": "deployed"}}, golden=(8, 9))
    changes = diff(before, now, NAMES)
    assert changes == [
        "El contrato de wARS apareció en zkSync.",
        "Chequeo contra las certificaciones: antes 9 de 9, ahora 8 de 9.",
    ]
    assert any("no está en la lista del cierre" in p for p in problems_of(now, TOKENS, NAMES))


def test_monitor_quiet_day_and_failed_read_is_never_zero():
    same = _state({"base": {"wARS": "deployed"}})
    assert diff(same, same, NAMES) == []
    assert problems_of(same, TOKENS, NAMES) == []
    failed = _state({"base": {"wARS": "deployed"}}, supply={"base": {"error": "timeout"}})
    assert problems_of(failed, TOKENS, NAMES) == ["No se pudo leer la cantidad de tokens en Base."]
    assert totals(failed["supply"]) == {}


def test_scheduled_close_takes_the_last_quarter_end():
    assert last_quarter_end(date(2026, 10, 1)) == "2026-09-30"
    assert last_quarter_end(date(2027, 1, 1)) == "2026-12-31"
    assert last_quarter_end(date(2026, 9, 30)) == "2026-06-30"


def test_close_and_run_close_both_take_latest(monkeypatch):
    # The engine-check job of close.yml passes "latest" to `cierre close`.
    built = []
    monkeypatch.setattr(cli, "_build_package", lambda cutoff, opening: built.append(cutoff) or 0)
    monkeypatch.setattr(close_run, "close_dir", lambda cutoff, opening=None: Path("/nonexistent"))
    monkeypatch.setattr(close_run, "refresh_engine", lambda cutoff, chains, opening: None)
    assert cli.main(["close", "--cutoff", "latest"]) == 0
    assert built == [last_quarter_end(date.today())]
    assert close_run.resolve_cutoff("2026-09-30") == "2026-09-30"


def test_schedule_text_in_buenos_aires_time():
    assert _cron_text("0 12 * * *") == "Todos los días a las 09:00 de Buenos Aires"
    assert _cron_text("0 12 1 1,4,7,10 *") == (
        "El 1 de enero, abril, julio y octubre a las 09:00 de Buenos Aires, "
        "el día siguiente a cada fin de trimestre"
    )


TASK = {
    "chain": "base",
    "token": "wARS",
    "kind": "burn",
    "amount": "985000000000000000000",
    "tx_hash": "0x" + "ab" * 32,
    "log_index": 7,
    "outcome": "task",
    "reason": "Salió por el puente y no encontramos la emisión del otro lado.",
    "summary": "Revisá con el operador del puente.",
    "explorer_url": "https://basescan.org/tx/0x" + "ab" * 32,
}
EXC = {"investigated": 2, "resolved": 1, "results": [TASK, TASK | {"outcome": "resolved"}]}


def test_issue_text():
    issue = issue_for("2026-09-30", TASK, NAMES)
    assert issue["title"] == "Revisar quema de 985,00 wARS en Base (cierre 30/09/2026)"
    assert issue["body"].splitlines()[0] == marker("2026-09-30", TASK)
    assert issue["labels"] == ["cierre-2026-09-30", "revisar"]
    assert ";" not in issue["body"] and " - " not in issue["body"]


def test_tasks_dry_run_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = open_issues("2026-09-30", EXC, NAMES)
    assert out["status"] == "dry_run" and len(out["issues"]) == 1


def test_tasks_open_once_then_reuse_the_issue(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    store: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/labels"):
            return httpx.Response(422)
        if req.method == "GET":
            return httpx.Response(200, json=store)
        body = json.loads(req.content)
        issue = {"number": len(store) + 1, "html_url": "https://gh/1", "state": "open"} | body
        store.append(issue)
        return httpx.Response(201, json=issue)

    first = open_issues("2026-09-30", EXC, NAMES, httpx.MockTransport(handler))
    second = open_issues("2026-09-30", EXC, NAMES, httpx.MockTransport(handler))
    assert [i["status"] for i in first["issues"]] == ["opened"]
    assert [i["status"] for i in second["issues"]] == ["existing"]
    assert len(store) == 1


def test_slack_task_line_links_the_issue():
    line = task_line(TASK, NAMES, "https://gh/1")
    assert line.endswith("<https://gh/1|tarea>")
    pkg = {"cutoff": "2026-09-30", "networks": [], "all_reconciled": True, "networks_checked": []}
    tasks = {"issues": [{"chain": "base", "tx_hash": TASK["tx_hash"], "log_index": 7, "url": "u"}]}
    msg = close_message(pkg, {"tokens": {}}, EXC, {"matched": 9, "total": 9}, "x", NAMES, tasks)
    assert "<u|tarea>" in json.dumps(msg)


def test_slack_puts_what_needs_review_before_what_is_in_order():
    pkg = {
        "cutoff": "2026-09-30",
        "networks": [],
        "all_reconciled": True,
        "networks_checked": ["base"],
    }
    memo = {"tokens": {"wARS": {"headline": "wARS: 1,00 al 30/09/2026", "status": "Conciliado"}}}
    msg = json.dumps(
        close_message(pkg, memo, EXC, {"matched": 9, "total": 9}, "x", NAMES), ensure_ascii=False
    )
    review = msg.index("Se requiere tu revisión en 1 movimiento*")
    ok = msg.index("Todo lo demás está conciliado y verificado")
    assert review < ok < msg.index("wARS: 1,00")

    nothing = {"investigated": 1, "resolved": 1, "results": []}
    msg = json.dumps(
        close_message(pkg, memo, nothing, {"matched": 9, "total": 9}, "x", NAMES),
        ensure_ascii=False,
    )
    assert "Se requiere tu revisión" not in msg
    assert "Todo está conciliado y verificado" in msg

    msg = json.dumps(
        close_message(pkg, memo, nothing, {"matched": 8, "total": 9}, "x", NAMES),
        ensure_ascii=False,
    )
    assert "conciliado y verificado" not in msg


def test_slack_send_keeps_a_record_of_what_was_sent(tmp_path, monkeypatch):
    posted = []

    def fake_post(url, json, timeout):
        posted.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setattr(slack.httpx, "post", fake_post)
    record = tmp_path / "slack_payload.json"
    assert slack.send({"text": "hola"}, record) == "sent"
    assert posted == [{"text": "hola"}]
    assert json.loads(record.read_text(encoding="utf-8")) == {"text": "hola"}


COMMANDS = [
    "discover",
    "recheck-creation",
    "supply",
    "golden",
    "coingecko",
    "exceptions",
    "slack",
    "extract-eval",
    "memo",
    "close",
    "run-close",
    "monitor",
    "alert",
    "site",
    "movements",
]


def test_every_command_answers_help():
    for name in COMMANDS:
        with pytest.raises(SystemExit) as stop:
            cli.main([name, "--help"])
        assert stop.value.code == 0


def test_workflows_call_commands_and_flags_that_exist():
    parser = cli.build_parser()
    calls = 0
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
        for step in (step for job in jobs.values() for step in job["steps"]):
            for line in step.get("run", "").splitlines():
                if "uv run cierre " in line:
                    command = line.split("uv run cierre ", 1)[1]
                    parser.parse_args(shlex.split(re.sub(r"\$\{\{.*?\}\}", "x", command)))
                    calls += 1
    assert calls >= 5  # run-close, close, monitor and the two alerts


def test_movement_test_starts_at_the_first_quarter_with_a_contract():
    cutoffs = ["2026-03-31", "2026-06-30", "2026-09-30"]
    index = {("wARS", "base", c, "ART"): {"status": "ok"} for c in cutoffs}
    index |= {
        ("wARS", "arc", "2026-03-31", "ART"): {"status": "network_not_live"},
        ("wARS", "arc", "2026-06-30", "ART"): {"status": "not_created"},
        ("wARS", "arc", "2026-09-30", "ART"): {"status": "ok"},
    }
    assert refresh._test_quarter(index, "wARS", "base", cutoffs) == (cutoffs[0], cutoffs[1])
    assert refresh._test_quarter(index, "wARS", "arc", cutoffs) == (cutoffs[1], cutoffs[2])


def test_default_cutoffs_are_the_certified_ones_and_the_closes():
    cutoffs = refresh.default_cutoffs()
    assert cutoffs == sorted(cutoffs)
    assert {"2026-03-31", "2026-06-30"} <= set(cutoffs)


# A package with only what the run log counts and the notification read.
PKG = {
    "all_reconciled": True,
    "reconciliation_passed": True,
    "data_complete": True,
    "scope": {"expected": 1, "counts": {"reconciled": 1}},
    "by_network": [{"passed": True}],
    "networks_checked": ["base"],
    "networks": ["base"],
    "movements": [{"chain": "base", "tx_hash": "0x1"}],
    "bridge": {"pairs_matched": 0},
    "review": [],
    "new_networks": [],
    "tokens": {"wARS": {}},
}
MEMO = {"tokens": {"wARS": {"explanation": {"attempts": 1, "source": "llm"}}}, "fallbacks": []}
NO_TASKS = {"investigated": 1, "resolved": 1, "tasks": 0, "results": []}
STEPS = [
    "detect",
    "engine",
    "package",
    "verify",
    "exceptions",
    "memo",
    "publish",
    "tasks",
    "notify",
]
SERVED = {"url": "u", "http_status": 200, "same_file": True, "waited_s": 0}


def _fake_close(monkeypatch, tmp_path, served=SERVED, engine=None):
    """Replace everything outside the repo (network, git, Claude, GitHub, Slack) and point
    the files at tmp_path. Returns the list that records what happened, in order."""
    events: list[str] = []
    data = tmp_path / "data"
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(close_run, "DATA_DIR", data)
    monkeypatch.setattr(runlog, "RUNS_PATH", data / "runs.jsonl")
    monkeypatch.setattr(alerts, "ALERT_SENT", tmp_path / "alert_sent")
    monkeypatch.setattr(close_run, "DiskCache", lambda: None)
    monkeypatch.setattr(
        close_run, "write_scope", lambda cutoff, cache: {"expected": 1, "counts": {"existing": 1}}
    )
    monkeypatch.setattr(close_run, "run_close", lambda *args: engine or [{"chain": "base"}])
    monkeypatch.setattr(close_run, "build_package", lambda cutoff, use_overrides=True: PKG)
    monkeypatch.setattr(close_run, "verify_certificates", lambda: {"matched": 9, "total": 9})
    monkeypatch.setattr(close_run, "investigate", lambda pkg, out_dir: NO_TASKS)
    monkeypatch.setattr(close_run, "write_memo", lambda pkg, out_dir: MEMO)
    monkeypatch.setattr(close_run, "write_site", lambda cutoff=None: data / "site.json")

    def commit(paths, message):
        events.append(f"commit: {message}")
        return "abcdef1234"

    def wait(url, local, timeout):
        events.append("wait")
        return served

    def open_tasks(cutoff, exceptions, names):
        events.append("issues")
        return {"status": "dry_run", "issues": []}

    def notice(*args):
        events.append("slack")
        return {"text": "cierre"}

    def set_aside(cutoff, run_id, keep_package):
        events.append(f"aside {keep_package}")

    monkeypatch.setattr(close_run, "commit_and_push", commit)
    monkeypatch.setattr(close_run, "wait_for_file", wait)
    monkeypatch.setattr(close_run, "open_issues", open_tasks)
    monkeypatch.setattr(close_run, "close_message", notice)
    monkeypatch.setattr(close_run, "set_aside_failed", set_aside)
    monkeypatch.setattr(close_run, "publish_run_log", lambda m: events.append(f"run log: {m}"))
    return events


def _last_run(tmp_path) -> dict:
    lines = (tmp_path / "data" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


def test_close_publishes_the_excel_before_telling_anyone(monkeypatch, tmp_path):
    events = _fake_close(monkeypatch, tmp_path)
    assert close_run.run("2026-09-30", "https://site.example", push=True, deploy_timeout=5) == 0
    assert events == [
        "commit: close 2026-09-30: package",
        "wait",
        "issues",
        "slack",
        "commit: close 2026-09-30: run log",
    ]
    run = _last_run(tmp_path)
    assert run["status"] == "ok" and [s["key"] for s in run["steps"]] == STEPS
    assert run["steps"][-1]["status"] == "dry_run"
    slack_file = tmp_path / "data" / "closes" / "2026-09-30" / "slack_payload.json"
    assert json.loads(slack_file.read_text(encoding="utf-8")) == {"text": "cierre"}


def test_close_alerts_without_a_link_when_the_site_does_not_serve_the_excel(monkeypatch, tmp_path):
    stale = {"url": "u", "http_status": 404, "same_file": False, "waited_s": 5}
    events = _fake_close(monkeypatch, tmp_path, served=stale)
    assert close_run.run("2026-09-30", "https://site.example", push=True, deploy_timeout=5) == 1
    # The package was pushed but nobody was told: no tasks, no Slack, only the run log.
    assert events == [
        "commit: close 2026-09-30: package",
        "wait",
        "aside True",
        "run log: close 2026-09-30: failed run",
    ]
    run = _last_run(tmp_path)
    assert run["status"] == "failed" and run["steps"][-1]["key"] == "publish"
    alert = tmp_path / "data" / "closes" / "2026-09-30" / "slack_alert_payload.json"
    text = alert.read_text(encoding="utf-8")
    assert "https://" not in text and "El paquete se subió al repo" in text
    assert (tmp_path / "alert_sent").exists()


def test_the_alert_says_published_only_after_a_pushed_package_was_served(monkeypatch, tmp_path):
    def broken_notice(*args):
        raise RuntimeError("slack down")

    alert = tmp_path / "data" / "closes" / "2026-09-30" / "slack_alert_payload.json"
    cases = [("https://site.example", True, "quedó publicado"), (None, False, "No hay paquete")]
    for site_url, push, expected in cases:
        _fake_close(monkeypatch, tmp_path)
        monkeypatch.setattr(close_run, "close_message", broken_notice)
        assert close_run.run("2026-09-30", site_url, push=push, deploy_timeout=5) == 1
        assert expected in alert.read_text(encoding="utf-8")


def test_close_keeps_the_last_good_engine_output_when_a_network_fails(monkeypatch, tmp_path):
    events = _fake_close(monkeypatch, tmp_path, engine=[{"chain": "base", "error": "boom"}])
    assert close_run.run("2026-09-30", "https://site.example", push=True, deploy_timeout=5) == 1
    assert events == ["aside False", "run log: close 2026-09-30: failed run"]
    out_dir = tmp_path / "data" / "closes" / "2026-09-30"
    assert (out_dir / "engine_failed.json").exists() and not (out_dir / "engine.json").exists()
    assert [s["key"] for s in _last_run(tmp_path)["steps"]] == ["detect", "engine"]


GOLDEN_OK = {"matched": 9, "total": 9, "read_errors": [], "mismatch_without_errors": []}


def _fake_monitor(monkeypatch, tmp_path, golden=GOLDEN_OK, supply_error=False):
    events: list[str] = []
    chains, tokens = load_chains(), load_tokens()
    in_scope = [k for k, c in chains.items() if c.in_scope]
    folder = tmp_path / "monitor"
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(monitor, "MONITOR_DIR", folder)
    monkeypatch.setattr(monitor, "LATEST", folder / "latest.json")
    monkeypatch.setattr(monitor, "HISTORY", folder / "history.jsonl")
    monkeypatch.setattr(runlog, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(alerts, "ALERT_SENT", tmp_path / "alert_sent")
    monkeypatch.setattr(monitor, "DiskCache", lambda: None)
    supply = {k: {"block": 1, "tokens": {"wARS": "5"}} for k in in_scope}
    if supply_error:
        supply[in_scope[0]] = {"error": "timeout"}
    deployments = {
        key: {sym: "deployed" if tok.on(key) else "absent" for sym, tok in tokens.items()}
        for key in chains
    }

    def send(payload, path):
        events.append("slack")
        return "dry run"

    def commit(paths, message):
        events.append(f"commit: {message[:10]}")

    monkeypatch.setattr(monitor, "read_supply", lambda *args: supply)
    monkeypatch.setattr(monitor, "check_deployments", lambda *args: deployments)
    monkeypatch.setattr(monitor, "golden_live", lambda *args: golden)
    monkeypatch.setattr(monitor, "prefetch_logs", lambda *args: {})
    monkeypatch.setattr(monitor, "ci_status", lambda: None)
    monkeypatch.setattr(monitor, "write_site", lambda cutoff=None: tmp_path / "site.json")
    monkeypatch.setattr(monitor, "send", send)
    monkeypatch.setattr(monitor, "commit_and_push", commit)
    monkeypatch.setattr(monitor, "publish_run_log", lambda m: events.append("run log"))
    return events, folder


def test_monitor_quiet_day_commits_the_data_and_stays_silent(monkeypatch, tmp_path):
    events, folder = _fake_monitor(monkeypatch, tmp_path)
    assert monitor.run(push=True) == 0
    assert events == ["commit: monitor: o"]
    assert (folder / "latest.json").exists()
    assert len((folder / "history.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_monitor_failed_read_notifies_and_still_commits(monkeypatch, tmp_path):
    events, _ = _fake_monitor(monkeypatch, tmp_path, supply_error=True)
    assert monitor.run(push=True) == 1
    assert events == ["slack", "commit: monitor: f"]
    assert (tmp_path / "alert_sent").exists()


def test_monitor_publishes_nothing_when_a_certified_figure_may_be_wrong(monkeypatch, tmp_path):
    mismatch = GOLDEN_OK | {"matched": 8, "mismatch_without_errors": ["wARS 2026-06-30"]}
    events, folder = _fake_monitor(monkeypatch, tmp_path, golden=mismatch)
    assert monitor.run(push=True) == 1
    assert events == []  # no commit, not even the run log
    assert (folder / "slack_alert_payload.json").exists()
    assert not (folder / "latest.json").exists()


def _git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def test_commit_and_push_and_restore(monkeypatch, tmp_path):
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    repo.mkdir()
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    tracked = repo / "data" / "a.json"
    tracked.parent.mkdir()
    tracked.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "start")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main")
    monkeypatch.setattr(gitops, "REPO_ROOT", repo)

    assert gitops.commit_and_push([tracked], "nothing changed") is None
    tracked.write_text('{"x": 1}\n', encoding="utf-8")
    assert gitops.commit_and_push([tracked], "change") == _git(remote, "rev-parse", "main")

    untracked = repo / "data" / "b.json"
    tracked.write_text("edited\n", encoding="utf-8")
    untracked.write_text("{}\n", encoding="utf-8")
    assert gitops.is_tracked(tracked) and not gitops.is_tracked(untracked)
    gitops.restore([tracked, untracked])
    assert tracked.read_text(encoding="utf-8") == '{"x": 1}\n' and untracked.exists()
