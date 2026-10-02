"""Run log: every run of the agent appends one line to data/runs.jsonl.

Each line holds the run kind, its start, duration and result, and the ordered steps with
their own start, duration, status and counts. The page builds the timeline "Lo que hizo
el agente" from the last close run, so a step only says what its counts prove.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from cierre import DATA_DIR

RUNS_PATH = DATA_DIR / "runs.jsonl"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Run:
    def __init__(self, kind: str, **params) -> None:
        self.record: dict = {
            "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{kind}",
            "kind": kind,
            "params": params,
            # schedule, workflow_dispatch or push in GitHub Actions, local otherwise
            "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
            "started_at": _now(),
            "steps": [],
        }
        self._t0 = time.monotonic()

    @contextmanager
    def step(self, key: str):
        """Times one step. The caller fills `counts` (and `status` for a non failure
        outcome such as a dry run). An exception marks the step failed and propagates."""
        rec: dict = {"key": key, "started_at": _now(), "status": "ok", "counts": {}}
        self.record["steps"].append(rec)
        t0 = time.monotonic()
        try:
            yield rec
        except Exception as exc:
            rec["status"] = "failed"
            rec["error"] = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            rec["duration_s"] = round(time.monotonic() - t0, 1)

    def finish(self, status: str) -> dict:
        self.record["status"] = status
        self.record["finished_at"] = _now()
        self.record["duration_s"] = round(time.monotonic() - self._t0, 1)
        RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNS_PATH, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(self.record, ensure_ascii=False) + "\n")
        return self.record


def read_runs() -> list[dict]:
    if not RUNS_PATH.exists():
        return []
    lines = RUNS_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
