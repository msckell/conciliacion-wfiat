"""Checks an LLM explanation before any of it reaches Finance.

The LLM does not type figures. It cites facts by ID, like {wARS.outstanding.2026-09-30},
and code replaces each ID with the formatted value. This module rejects:

- raw_figure: any digit, or a number written in words, outside a fact ID
- unknown_fact: a fact ID that does not exist at all
- wrong_token: a fact ID of another token
- wrong_period: a fact ID of this token but a period outside this close
- banned_claim: words that state a conclusion only the templates may state
  (reconciliation, coverage, audit, "todas las redes")
- style: dashes or semicolons, which the house style for Spanish copy does not use
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

FACT_ID = re.compile(r"\{([A-Za-z]+)\.([a-z_]+)\.([0-9A-Za-z-]+)\}")

NUMBER_WORDS = re.compile(
    r"\b(cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|veinte|"
    r"treinta|cien|ciento|cientos|mil|miles|millon|millones|billon|billones|"
    r"docena|decena|centena|mitad|doble|triple)\b"
)

BANNED = {
    "todas las redes": r"\btodas las redes\b",
    "auditado": r"\baudit\w*",
    "respaldo": r"\brespald\w*",
    "cobertura": r"\bcobertura\b|\bcubiert\w*",
    "garantiza": r"\bgarantiz\w*",
    "certifica": r"\bcertific\w*",
    "concilia": r"\bconcilia\w*",
    "coincide": r"\bcoincid\w*",
    "verificado": r"\bverificad\w*",
    "sin errores": r"\bsin error\w*",
    "correcto": r"\bcorrect\w*",
}

DASH_OR_SEMICOLON = re.compile(r";|\s[-–—]\s|[–—]")


@dataclass(frozen=True)
class Problem:
    code: str
    detail: str


def _plain(text: str) -> str:
    """Lowercase without accents, so 'Millón' and 'millon' match the same rule."""
    norm = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


def _style_problems(rest: str, digits_detail: str) -> list[Problem]:
    """Figures, banned claims and style, over text already stripped of fact IDs."""
    problems: list[Problem] = []
    if re.search(r"\d", rest):
        problems.append(Problem("raw_figure", digits_detail))
    elif word := NUMBER_WORDS.search(rest):
        problems.append(
            Problem("raw_figure", f"hay un número escrito en palabras: '{word.group(0)}'")
        )
    for label, pattern in BANNED.items():
        if re.search(pattern, rest):
            problems.append(Problem("banned_claim", f"no podés afirmar '{label}'"))
    if DASH_OR_SEMICOLON.search(rest):
        problems.append(Problem("style", "no uses guiones ni punto y coma"))
    return problems


def check(text: str, token: str, facts: dict[str, str], periods: set[str]) -> list[Problem]:
    """facts: fact ID to formatted value. periods: the periods this close may cite."""
    problems: list[Problem] = []
    for m in FACT_ID.finditer(text):
        fid = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        if m.group(1) != token:
            problems.append(Problem("wrong_token", f"{{{fid}}} es de {m.group(1)}, no de {token}"))
        elif m.group(3) not in periods:
            problems.append(Problem("wrong_period", f"{{{fid}}} es de otro período"))
        elif fid not in facts:
            problems.append(Problem("unknown_fact", f"{{{fid}}} no existe"))
    rest = _plain(FACT_ID.sub(" ", text))
    problems += _style_problems(rest, "hay números escritos fuera de un ID de dato")
    if "{" in rest or "}" in rest:
        problems.append(Problem("unknown_fact", "hay una llave suelta que no es un ID válido"))
    return problems


def render(text: str, facts: dict[str, str]) -> str:
    """Replace each fact ID with its formatted value. Call only after check() passed."""
    return FACT_ID.sub(lambda m: facts[f"{m.group(1)}.{m.group(2)}.{m.group(3)}"], text)


def feedback(problems: list[Problem]) -> str:
    lines = [f"- {p.detail}" for p in problems]
    return "Tu texto anterior fue rechazado por estos motivos:\n" + "\n".join(lines)


# Identifiers an agent summary may quote: hex addresses and hashes, and standard names
# like ERC1967 or ERC-4337. Anything else with a digit is a figure.
IDENTIFIER = re.compile(r"\b0x[0-9a-fA-F]+(\.{3}[0-9a-fA-F]*)?|\bERC-?\d+\w*", re.IGNORECASE)


def check_free_text(text: str) -> list[Problem]:
    """For texts without fact IDs, such as the exception agent's summary: no figures and
    no banned claims, but addresses and hashes may be quoted."""
    rest = _plain(IDENTIFIER.sub(" ", text))
    return _style_problems(
        rest, "hay números (montos, bloques o ids) fuera de una dirección o hash"
    )
