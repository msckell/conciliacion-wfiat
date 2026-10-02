"""Phase 4 pieces, offline: monitor change detection, GitHub tasks without duplicates,
the scheduled cutoff and the schedule text on the page."""

import json
from datetime import date
from types import SimpleNamespace

import httpx

from cierre.close import last_quarter_end
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
    import httpx

    import cierre.slack as slack

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
