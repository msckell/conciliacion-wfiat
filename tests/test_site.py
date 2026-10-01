"""The page data: timeline sentences come only from each step's counts, and the prose
follows the house style (no dashes, no semicolons)."""

from cierre.site import _duration, _timeline

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
    assert _duration(1) == "1 segundo"
    assert _duration(42.4) == "42 segundos"
    assert _duration(61) == "1 minuto"
    assert _duration(245) == "4 minutos"
