"""The page data: timeline sentences come only from each step's counts, and the prose has no
dashes or semicolons."""

import json
import shutil

import cierre.site as site
from cierre import DATA_DIR
from cierre.site import _duration, _exceptions, _timeline

RUN = {
    "id": "20261001T000000Z-close",
    "kind": "close",
    "started_at": "2026-10-01T12:00:00+00:00",
    "duration_s": 245.0,
    "status": "ok",
    "steps": [
        {
            "key": "detect",
            "status": "ok",
            "started_at": "2026-10-01T12:00:00+00:00",
            "duration_s": 0.0,
            "counts": {
                "cutoff": "2026-09-30",
                "previous_cutoff": "2026-06-30",
                "convention": "ART",
            },
        },
        {
            "key": "engine",
            "status": "ok",
            "started_at": "2026-10-01T12:00:00+00:00",
            "duration_s": 90.0,
            "counts": {"networks_read": 9, "networks_failed": 0},
        },
        {
            "key": "package",
            "status": "ok",
            "started_at": "2026-10-01T12:01:30+00:00",
            "duration_s": 20.0,
            "counts": {
                "reconciled": 54,
                "reconciliations": 54,
                "movements": 628,
                "bridge_pairs": 255,
                "review": 1,
                "new_networks": ["arc"],
            },
        },
        {
            "key": "verify",
            "status": "ok",
            "started_at": "2026-10-01T12:02:00+00:00",
            "duration_s": 0.1,
            "counts": {"matched": 9, "total": 9},
        },
        {
            "key": "exceptions",
            "status": "ok",
            "started_at": "2026-10-01T12:02:00+00:00",
            "duration_s": 100.0,
            "counts": {"investigated": 9, "resolved": 4, "tasks": 5},
        },
        {
            "key": "memo",
            "status": "ok",
            "started_at": "2026-10-01T12:03:40+00:00",
            "duration_s": 50.0,
            "counts": {"tokens": 6, "accepted_first_try": 6, "attempts": 6, "fallbacks": 0},
        },
        {
            "key": "publish",
            "status": "skipped",
            "started_at": "2026-10-01T12:04:30+00:00",
            "duration_s": 0.0,
            "counts": {"reason": "no SITE_URL yet"},
        },
        {
            "key": "notify",
            "status": "dry_run",
            "started_at": "2026-10-01T12:04:30+00:00",
            "duration_s": 0.0,
            "counts": {"tasks_listed": 5},
        },
    ],
}
NAMES = {"arc": "Arc"}


def test_timeline_uses_the_counts():
    t = _timeline(RUN, NAMES)
    titles = [s["title"] for s in t["steps"]]
    assert titles[0] == "Detectó el cierre del 30/09/2026"
    assert "54 de 54" in titles[2]
    assert "coincide en 9" in titles[3]
    assert "resolvió 4" in titles[4] and "5 tareas" in titles[4]
    assert "Red nueva: Arc" in t["steps"][2]["detail"]
    assert "1 movimiento para revisar" in t["steps"][2]["detail"]
    assert titles[6].endswith("pendiente")
    assert titles[7].endswith("(modo de prueba)")
    assert t["started_at"] == "01/10/2026 09:00"
    assert t["duration"] == "4 minutos"


def test_timeline_prose_style():
    for s in _timeline(RUN, NAMES)["steps"]:
        for text in (s["title"], s["detail"]):
            assert ";" not in text and "—" not in text and " - " not in text


def test_no_run_means_no_timeline():
    assert _timeline(None, NAMES) is None


def test_duration():
    assert _duration(0.2) == "menos de 1 segundo"
    assert _duration(1) == "1 segundo"
    assert _duration(42.4) == "42 segundos"
    assert _duration(61) == "1 minuto"
    assert _duration(245) == "4 minutos"


def test_site_survives_an_attempt_the_model_never_answered(tmp_path, monkeypatch):
    for part in ("closes/2026-09-30", "golden", "monitor"):
        shutil.copytree(DATA_DIR / part, tmp_path / part)
    attempts = tmp_path / "closes/2026-09-30/attempts.jsonl"
    failed = {"token": "wARS", "attempt": 1, "error": "claude error: Invalid auth token"}
    attempts.write_text(json.dumps(failed | {"accepted": False}) + "\n", encoding="utf-8")
    monkeypatch.setattr(site, "DATA_DIR", tmp_path)

    out = site.build_site("2026-09-30")
    assert out["verifier"]["attempts"][0]["problems"] == ["sin respuesta del modelo"]


def test_exception_items_take_the_movement_kind_from_the_package(tmp_path):
    # Files written before proposal_kind existed have the proposal's kind under "kind".
    result = {
        "chain": "arc",
        "token": "wARS",
        "tx_hash": "0xmint",
        "log_index": 3,
        "kind": "needs_person",
        "amount": "10000000000000000000",
        "outcome": "task",
        "reason": "sin clasificar",
        "summary": "Mirá el contrato.",
        "explorer_url": "https://example.org/tx/0xmint",
    }
    exc = {"investigated": 1, "resolved": 0, "tasks": 1, "results": [result]}
    pkg = {"movements": [{k: result[k] for k in ("chain", "tx_hash", "amount")} | {"kind": "mint"}]}
    out = _exceptions(exc, pkg, tmp_path / "missing.jsonl", {"arc": "Arc"}, {"wARS": 18})
    assert out["items"][0]["movement"] == "emisión de 10,00 wARS"
