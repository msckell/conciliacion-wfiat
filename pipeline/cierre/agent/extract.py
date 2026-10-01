"""LLM extraction of the published certificates, scored against the confirmed table.

This is an eval, not a source of truth: the golden figures were extracted with pypdf and
regex and confirmed by Maxi (data/golden/certifications.json). Here the LLM reads each
PDF on its own, every number it returns must appear verbatim in the PDF text, and each
field is compared with the confirmed value.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

from cierre.agent.llm import Ask, LlmError, model_name
from cierre.references import fetch_pdf, network_keys, parse_printed_number

SYSTEM = (Path(__file__).parent / "prompts" / "extract_system.md").read_text(encoding="utf-8")

SCHEMA = {
    "type": "object",
    "properties": {
        "token": {"type": "string"},
        "cutoff": {"type": "string"},
        "tokens_outstanding": {"type": "string"},
        "tokens_outstanding_quote": {"type": "string"},
        "collateral": {"type": "string"},
        "collateral_currency": {"type": "string"},
        "signed_date": {"type": "string"},
        "networks_listed": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "token",
        "cutoff",
        "tokens_outstanding",
        "tokens_outstanding_quote",
        "collateral",
        "collateral_currency",
        "signed_date",
        "networks_listed",
        "notes",
    ],
    "additionalProperties": False,
}

FIELDS = ("token", "cutoff", "tokens_outstanding", "collateral", "signed_date", "networks")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def pdf_text(url: str) -> str:
    return _flat(" ".join(p.extract_text() or "" for p in PdfReader(fetch_pdf(url)).pages))


def score(got: dict, cert: dict, text: str) -> dict:
    """Per field: ok, plus a reason when not. Numbers must be verbatim in the PDF."""
    out: dict[str, dict] = {}

    def verbatim(s: str) -> bool:
        return bool(s) and _flat(s) in text

    out["token"] = {"ok": got["token"].replace(" ", "") == cert["token"]}
    out["cutoff"] = {"ok": got["cutoff"] == cert["cutoff"]}
    if not verbatim(got["tokens_outstanding"]):
        out["tokens_outstanding"] = {"ok": False, "reason": "no aparece textual en el PDF"}
    else:
        value = parse_printed_number(got["tokens_outstanding"]).value
        out["tokens_outstanding"] = {"ok": value == cert["figure"], "value": value}
    if not verbatim(got["tokens_outstanding_quote"]):
        out["tokens_outstanding"] |= {"ok": False, "reason": "la cita no aparece textual"}
    collateral = (cert.get("collateral_as_stated") or {}).get("printed")
    if not verbatim(got["collateral"]):
        out["collateral"] = {"ok": False, "reason": "no aparece textual en el PDF"}
    else:
        out["collateral"] = {"ok": got["collateral"] == collateral}
    out["signed_date"] = {"ok": got["signed_date"] == cert.get("signed_date")}
    try:
        keys = sorted(set(network_keys(got["networks_listed"])))
        out["networks"] = {"ok": keys == sorted(set(cert["network_keys"])), "keys": keys}
    except ValueError as exc:
        out["networks"] = {"ok": False, "reason": str(exc)[:120]}
    for f in FIELDS:
        out.setdefault(f, {"ok": False})
    return out


def run_eval(certs: list[dict], ask: Ask) -> dict:
    rows = []
    for cert in certs:
        text = pdf_text(cert["document_url"])
        row = {"token": cert["token"], "cutoff": cert["cutoff"], "url": cert["document_url"]}
        try:
            reply = ask(SYSTEM, "Texto del certificado:\n\n" + text, SCHEMA)
            row |= {
                "extracted": reply.data,
                "fields": score(reply.data, cert, text),
                "duration_s": reply.duration_s,
            }
        except LlmError as exc:
            row |= {"error": str(exc)[:300], "fields": {f: {"ok": False} for f in FIELDS}}
        row["all_ok"] = all(v["ok"] for v in row["fields"].values())
        rows.append(row)
    per_field = {f: sum(r["fields"][f]["ok"] for r in rows) for f in FIELDS}
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model_name(),
        "documents": len(rows),
        "documents_all_fields_ok": sum(r["all_ok"] for r in rows),
        "per_field_ok": per_field,
        "rows": rows,
    }
