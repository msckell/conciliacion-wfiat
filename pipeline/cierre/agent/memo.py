"""Closing memo: templates render every figure and status, the LLM writes only the
explanation, one token at a time, citing facts by ID.

Per token: build its facts, ask for an explanation, check it with the verifier, retry with
feedback that names each problem, then fall back to the template only version. Every
attempt is appended to attempts.jsonl.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from cierre.agent import verifier
from cierre.agent.llm import Ask, LlmError, model_name
from cierre.cutoffs import period

MAX_RETRIES = 2

SYSTEM = (Path(__file__).parent / "prompts" / "memo_system.md").read_text(encoding="utf-8")

SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}

FACT_MEANING = {
    "outstanding": "tokens en circulación al cierre de esa fecha, suma de las redes revisadas",
    "change": "variación de la circulación en el trimestre",
    "mints": "total emitido en el trimestre (incluye puente)",
    "burns": "total quemado en el trimestre (incluye puente)",
    "primary_mints": "emisión primaria: tokens nuevos emitidos por el emisor",
    "redemptions": "rescate primario: tokens que el emisor quemó",
    "bridge_in": "tokens que entraron a una red por el puente",
    "bridge_out": "tokens que salieron de una red por el puente",
    "networks_with_balance": "cantidad de redes con saldo al cierre",
    "review_items": "movimientos que quedan para que una persona revise",
}

COUNT_FACTS = {"networks_with_balance", "review_items"}


def period_of(pkg: dict) -> dict:
    """The period of the package: a quarter (id 2026Q3) or an explicit interval."""
    return pkg.get("period") or period(pkg["previous_cutoff"], pkg["cutoff"])


def fmt(value: Decimal | str | int, places: int = 2) -> str:
    """Spanish format with a fixed number of decimals: 1.234.567,89 and 132.950,00.
    Counts use places=0."""
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    whole, _, frac = f"{abs(d):f}".partition(".")
    whole = f"{int(whole):,}".replace(",", ".")
    return sign + whole + ("," + frac if frac else "")


def fmt_date(iso: str) -> str:
    """2026-09-30 -> 30/09/2026."""
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def token_facts(pkg: dict, sym: str) -> dict[str, dict]:
    """Every fact of one token: id -> {value (formatted), meaning}."""
    t = pkg["tokens"][sym]
    cut, prev, q = pkg["cutoff"], pkg["previous_cutoff"], period_of(pkg)["id"]
    cat = t["by_category"]
    raw = {
        f"{sym}.outstanding.{prev}": (t["opening"], "outstanding"),
        f"{sym}.outstanding.{cut}": (t["closing"], "outstanding"),
        f"{sym}.change.{q}": (t["change"], "change"),
        f"{sym}.mints.{q}": (t["mints"], "mints"),
        f"{sym}.burns.{q}": (t["burns"], "burns"),
        f"{sym}.primary_mints.{q}": (cat["primary"]["amount"], "primary_mints"),
        f"{sym}.redemptions.{q}": (cat["redemption"]["amount"], "redemptions"),
        f"{sym}.bridge_in.{q}": (cat["bridge_in"]["amount"], "bridge_in"),
        f"{sym}.bridge_out.{q}": (cat["bridge_out"]["amount"], "bridge_out"),
        f"{sym}.networks_with_balance.{cut}": (
            len(t["networks_with_balance"]),
            "networks_with_balance",
        ),
        f"{sym}.review_items.{q}": (
            sum(1 for r in pkg["review"] if r["token"] == sym),
            "review_items",
        ),
    }
    # Zero flows are left out, so the text cannot dwell on them. Balances stay.
    return {
        fid: {
            "value": fmt(v, 0 if kind in COUNT_FACTS else 2),
            "meaning": FACT_MEANING[kind].replace("trimestre", period_of(pkg)["word"]),
        }
        for fid, (v, kind) in raw.items()
        if kind == "outstanding" or Decimal(str(v)) != 0
    }


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def template(pkg: dict, sym: str) -> dict:
    """Figures and statuses, rendered by code only."""
    t = pkg["tokens"][sym]
    rows = [r for r in pkg["by_network"] if r["token"] == sym]
    ok = all(r["passed"] and r["difference"] == "0" for r in rows)
    n_review = sum(1 for r in pkg["review"] if r["token"] == sym)
    change = Decimal(t["change"])
    sign = "+" if change >= 0 else ""
    return {
        "headline": (
            f"{sym}: {fmt(t['closing'])} al {fmt_date(pkg['cutoff'])} "
            f"({sign}{fmt(change)} vs {fmt_date(pkg['previous_cutoff'])})"
        ),
        "status": (
            f"Conciliado en {_plural(len(rows), 'red', 'redes')}, diferencia cero."
            if ok
            else "No concilia en alguna red. Revisá la hoja Conciliación."
        ),
        "reconciled": ok,
        "review": (
            f"{_plural(n_review, 'movimiento', 'movimientos')} para revisar."
            if n_review
            else "Nada para revisar."
        ),
        "networks_with_balance": len(t["networks_with_balance"]),
    }


def _prompt(sym: str, facts: dict[str, dict], pkg: dict) -> str:
    lines = [
        f"Token: {sym}. {period_of(pkg)['word'].capitalize()} cerrado al "
        f"{fmt_date(pkg['cutoff'])}.",
        "",
        "Datos disponibles:",
    ]
    for fid, f in facts.items():
        lines.append(f"- {{{fid}}}: {f['meaning']} (valor {f['value']})")
    with_balance = set(pkg["tokens"][sym]["networks_with_balance"])
    for n in pkg["networks"]:
        if n["is_new"] and sym in n["new_tokens"]:
            has = "tiene saldo" if n["chain"] in with_balance else "no tiene saldo"
            lines += [
                "",
                f"Contexto: {n['name']} es una red nueva desde el cierre anterior. "
                f"El contrato de {sym} existe ahí y al cierre {has} en esa red.",
            ]
    lines += ["", "Escribí la explicación siguiendo las reglas."]
    return "\n".join(lines)


def explain_token(pkg: dict, sym: str, ask: Ask, attempts_path: Path | None = None) -> dict:
    """The verified LLM explanation of one token, or the fallback marker when every attempt
    failed. A failed call (LlmError) asks again with the prompt of the previous attempt."""
    facts = token_facts(pkg, sym)
    values = {fid: f["value"] for fid, f in facts.items()}
    periods = {pkg["cutoff"], pkg["previous_cutoff"], period_of(pkg)["id"]}
    base_prompt = prompt = _prompt(sym, facts, pkg)
    attempts = MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        record = {"token": sym, "attempt": attempt, "at": datetime.now(UTC).isoformat()}
        try:
            reply = ask(SYSTEM, prompt, SCHEMA)
            text = reply.data.get("explanation", "")
            problems = verifier.check(text, sym, values, periods)
            record |= {
                "model": reply.model,
                "duration_s": reply.duration_s,
                "text": text,
                "problems": [asdict(p) for p in problems],
                "accepted": not problems,
            }
        except LlmError as exc:
            problems = []
            record |= {"error": str(exc)[:300], "accepted": False}
        if attempts_path is not None:
            with open(attempts_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if record["accepted"]:
            return {
                "source": "llm",
                "text_with_ids": text,
                "text": verifier.render(text, values),
                "attempts": attempt,
            }
        if problems:
            prompt = base_prompt + "\n\n" + verifier.feedback(problems)
    return {"source": "fallback", "text_with_ids": None, "text": None, "attempts": attempts}


def build_memo(pkg: dict, ask: Ask, out_dir: Path) -> dict:
    attempts = out_dir / "attempts.jsonl"
    attempts.unlink(missing_ok=True)
    tokens = {}
    for sym in pkg["tokens"]:
        tokens[sym] = template(pkg, sym) | {"explanation": explain_token(pkg, sym, ask, attempts)}
    return {
        "cutoff": pkg["cutoff"],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model_name(),
        "tokens": tokens,
        "fallbacks": [s for s, t in tokens.items() if t["explanation"]["source"] == "fallback"],
    }


def rerender(pkg: dict, memo: dict) -> dict:
    """Templates and fact values again, over the explanations already verified. No LLM
    call: only the formatting of the figures changes."""
    tokens = {}
    for sym, old in memo["tokens"].items():
        expl = dict(old["explanation"])
        if expl["source"] == "llm":
            values = {fid: f["value"] for fid, f in token_facts(pkg, sym).items()}
            expl["text"] = verifier.render(expl["text_with_ids"], values)
        tokens[sym] = template(pkg, sym) | {"explanation": expl}
    return memo | {"tokens": tokens}
