"""Memo flow with a fake LLM: retry with feedback, then fallback."""

import json

from cierre.agent.llm import Reply
from cierre.agent.memo import build_memo, fmt

PKG = {
    "cutoff": "2026-09-30",
    "previous_cutoff": "2026-06-30",
    "networks": [],
    "review": [],
    "by_network": [{"token": "wARS", "passed": True, "difference": "0"}],
    "tokens": {
        "wARS": {
            "opening": "100",
            "closing": "150.5",
            "change": "50.5",
            "mints": "60.5",
            "burns": "10",
            "networks_with_balance": ["base"],
            "by_category": {
                k: {"amount": "0", "count": 0}
                for k in ("primary", "redemption", "bridge_in", "bridge_out", "unclassified")
            },
        }
    },
}


def fake(replies):
    seen = []

    def ask(system, prompt, schema):
        seen.append(prompt)
        return Reply({"explanation": replies[len(seen) - 1]}, "fake", 0.0, None)

    return ask, seen


def test_retry_with_feedback_then_accept(tmp_path):
    ask, seen = fake(["Subió a 150 tokens.", "Cerró en {wARS.outstanding.2026-09-30}."])
    memo = build_memo(PKG, ask, tmp_path)
    e = memo["tokens"]["wARS"]["explanation"]
    assert (e["source"], e["attempts"], e["text"]) == ("llm", 2, "Cerró en 150,5.")
    assert "rechazado" in seen[1]
    lines = (tmp_path / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["accepted"] for x in lines] == [False, True]


def test_fallback_after_three_bad_attempts(tmp_path):
    ask, _ = fake(["Hay 3.", "Hay {wBRL.outstanding.2026-09-30}.", "Todo concilia."])
    memo = build_memo(PKG, ask, tmp_path)
    assert memo["tokens"]["wARS"]["explanation"]["source"] == "fallback"
    assert memo["fallbacks"] == ["wARS"]
    assert memo["tokens"]["wARS"]["status"] == "Conciliado en 1 redes, diferencia cero."


def test_fmt_spanish():
    assert (fmt("14931194588.926548"), fmt("-0.5"), fmt("7619996077")) == (
        "14.931.194.588,93",
        "-0,5",
        "7.619.996.077",
    )
