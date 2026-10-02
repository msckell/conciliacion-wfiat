"""Failure alerts raised by the CLI.

A run that fails inside the CLI posts its own alert and leaves a marker file, so the
workflow's catch-all alert step does not post a second one.
"""

from __future__ import annotations

from pathlib import Path

from cierre import CACHE_DIR
from cierre.slack import alert_message, send

ALERT_SENT = CACHE_DIR / "alert_sent"
WORKFLOW_ALERT_RECORD = CACHE_DIR / "workflow_alert_payload.json"


def mark_sent() -> None:
    ALERT_SENT.parent.mkdir(parents=True, exist_ok=True)
    ALERT_SENT.write_text("1", encoding="utf-8")


def send_failure(payload: dict, record_path: Path) -> None:
    """Post an alert for a failed run and note that it went out."""
    send(payload, record_path)
    mark_sent()


def send_workflow_alert(step: str, message: str, what: str) -> str:
    """The workflow's alert, for a step that failed outside the CLI (setup, git, deploy).
    Returns what to print."""
    if ALERT_SENT.exists():
        return "alert already sent by the CLI"
    WORKFLOW_ALERT_RECORD.parent.mkdir(parents=True, exist_ok=True)
    return send(alert_message(step, message, what), WORKFLOW_ALERT_RECORD)
