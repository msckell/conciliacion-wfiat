"""Light JSON for the page (data/site/site.json).

The page only displays what this file holds. Every figure is formatted here from the
close files with Decimal, and every status and sentence with a number in it is a
template over those files, so the site never computes or types a figure. The LLM text
that reaches the page is the memo explanation, already checked by the verifier.

Not exported on purpose: the notes of the extraction eval (only its scores) and the
quoted text of the certificates.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from cierre import DATA_DIR
from cierre.agent.memo import fmt
from cierre.agent.verifier_cases import REJECT_CASES
from cierre.config import load_chains
from cierre.package import units
from cierre.runlog import read_runs

ART = ZoneInfo("America/Argentina/Buenos_Aires")

TOOL_LABELS = {
    "get_transaction": "Leyó la transacción y sus eventos",
    "contract_info": "Miró el contrato",
    "has_role": "Consultó permisos de emisión",
    "find_mints_to": "Buscó emisiones hacia la cuenta",
    "find_bridge_fulfillment": "Buscó la emisión del puente en la red de destino",
    "propose": "Propuso una clasificación",
}

LABELS = {"documented": "Documentado", "inferred": "Inferido", "hypothesis": "Hipótesis"}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _d(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


def _local(iso: str) -> str:
    """ISO instant to 'dd/mm/aaaa hh:mm' in Buenos Aires time."""
    return datetime.fromisoformat(iso).astimezone(ART).strftime("%d/%m/%Y %H:%M")


def _signed(value: Decimal | str) -> str:
    d = Decimal(str(value))
    return ("+" if d > 0 else "") + fmt(d)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _duration(seconds: float) -> str:
    if seconds < 1:
        return "menos de 1 segundo"
    s = round(seconds)
    if s < 60:
        return _plural(s, "segundo", "segundos")
    m = round(seconds / 60)
    return _plural(m, "minuto", "minutos")


def _tokens(pkg: dict, names: dict[str, str]) -> list[dict]:
    out = []
    for sym, t in pkg["tokens"].items():
        closing = Decimal(t["closing"])
        nets = []
        for key, amount in t["closing_by_network"].items():
            a = Decimal(amount)
            if a == 0:
                continue
            share = (a / closing * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            nets.append(
                {"chain": key, "name": names[key], "amount": fmt(a), "share_pct": float(share)}
            )
        nets.sort(key=lambda n: -Decimal(t["closing_by_network"][n["chain"]]))
        out.append(
            {
                "symbol": sym,
                "name": t["name"],
                "closing": fmt(closing),
                "opening": fmt(t["opening"]),
                "change": _signed(t["change"]),
                "networks_with_balance": len(t["networks_with_balance"]),
                "by_network": nets,
            }
        )
    return out


def _timeline(run: dict | None, names: dict[str, str]) -> dict | None:
    """One sentence per step, from that step's counts only."""
    if run is None:
        return None
    steps = []
    for s in run["steps"]:
        c = s["counts"]
        k = s["key"]
        if k == "detect":
            title = f"Detectó el cierre del {_d(c['cutoff'])}"
            detail = (
                f"Corte a las 23:59:59 hora de Buenos Aires. Cierre anterior: "
                f"{_d(c['previous_cutoff'])}."
            )
        elif k == "engine":
            title = f"Leyó {c['networks_read']} redes"
            detail = (
                "Buscó el bloque del corte en cada red y leyó la cantidad de tokens y cada "
                "emisión y quema del trimestre."
            )
        elif k == "package":
            new = ", ".join(names[n] for n in c["new_networks"])
            title = (
                f"Concilió {c['reconciled']} de {c['reconciliations']} combinaciones de "
                f"moneda y red, con diferencia cero"
            )
            detail = (
                f"Clasificó {c['movements']} emisiones y quemas y emparejó "
                f"{c['bridge_pairs']} pases del puente entre redes. "
                + (f"Red nueva: {new}. " if new else "")
                + f"Encontró {_plural(c['review'], 'movimiento', 'movimientos')} para revisar."
            )
        elif k == "verify":
            title = (
                f"Comprobó el método contra {c['total']} certificaciones publicadas: "
                f"coincide en {c['matched']}"
            )
            detail = "Recalcula la cantidad de tokens de cada corte certificado con la misma regla."
        elif k == "exceptions":
            title = (
                f"Investigó {c['investigated']} movimientos: resolvió {c['resolved']} con "
                f"evidencia y dejó {_plural(c['tasks'], 'tarea', 'tareas')} para una persona"
            )
            detail = (
                "Leyó cada transacción, consultó permisos y buscó la otra punta del puente. "
                "El código comprobó cada propuesta antes de aceptarla."
            )
        elif k == "memo":
            title = f"Escribió el resumen de las {c['tokens']} monedas"
            detail = (
                f"El verificador aceptó {c['accepted_first_try']} de {c['tokens']} textos al "
                f"primer intento. Las cifras las pone el código, no la IA."
            )
        elif k == "publish":
            if s["status"] == "skipped":
                title = "Publicación del Excel: pendiente"
                detail = "El chequeo del link publicado se activa cuando el sitio esté en línea."
            else:
                title = "Publicó el Excel y comprobó que el link responde"
                detail = c.get("url", "")
        elif k == "notify":
            title = "Avisó a Finanzas por Slack"
            if s["status"] == "dry_run":
                title += " (modo de prueba)"
            detail = (
                f"Con el resumen, el link al Excel y "
                f"{_plural(c['tasks_listed'], 'tarea', 'tareas')} para revisar."
            )
        else:
            title, detail = k, ""
        steps.append(
            {
                "key": k,
                "title": title,
                "detail": detail,
                "status": s["status"],
                "at": _local(s["started_at"]),
                "duration": _duration(s.get("duration_s", 0)),
            }
        )
    return {
        "id": run["id"],
        "started_at": _local(run["started_at"]),
        "duration": _duration(run["duration_s"]),
        "status": run["status"],
        "steps": steps,
    }


def _verification(names: dict[str, str]) -> dict:
    ver = _read(DATA_DIR / "golden" / "verification.json")
    certs = {
        (c["token"], c["cutoff"]): c
        for c in _read(DATA_DIR / "golden" / "certifications.json")["certifications"]
    }
    rows = []
    for r in ver["rows"]:
        cert = certs[(r["token"], r["cutoff"])]
        rows.append(
            {
                "token": r["token"],
                "cutoff": _d(r["cutoff"]),
                "certified": fmt(r["certified"]),
                "raw_sum": fmt(r["computed"])
                if not r["adjustments"]
                else fmt(units(r["raw_sum_base_units"], 18)),
                "adjustments": "Ninguno" if not r["adjustments"] else str(len(r["adjustments"])),
                "computed": fmt(r["computed_at_printed_precision"], 0),
                "difference": fmt(
                    Decimal(r["computed_at_printed_precision"])
                    - Decimal(r["certified"]).quantize(Decimal(1), rounding=ROUND_HALF_UP),
                    0,
                ),
                "match": r["match"],
                "networks": [names[n] for n in r["networks_included"]],
                "document_url": cert["document_url"],
                "signed": _d(cert["signed_date"]),
            }
        )
    return {
        "matched": ver["matched"],
        "total": ver["total"],
        "rule": (
            "Suma de totalSupply() en todas las redes revisadas, sin ajustes, al corte de las "
            "23:59:59 hora de Buenos Aires, comparada en tokens enteros."
        ),
        "rows": rows,
    }


def _exceptions(exc: dict | None, log_path, names: dict[str, str]) -> dict | None:
    if exc is None:
        return None
    tools: dict[tuple, list[str]] = {}
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if "action" in e:
                tools.setdefault((e["chain"], e["tx_hash"], e["log_index"]), []).append(
                    TOOL_LABELS.get(e["action"], e["action"])
                )
    items = []
    for r in exc["results"]:
        kind = "emisión" if r.get("kind") == "mint" or "primary" in r.get("kind", "") else "quema"
        items.append(
            {
                "outcome": r["outcome"],
                "chain": names[r["chain"]],
                "token": r["token"],
                "movement": f"{kind} de {fmt(units(r['amount'], 18))} {r['token']}",
                "reason": r["reason"],
                "summary": r["summary"],
                "checks": r.get("checks") or [],
                "explorer_url": r["explorer_url"],
                "steps": tools.get((r["chain"], r["tx_hash"], r["log_index"]), []),
            }
        )
    return {
        "investigated": exc["investigated"],
        "resolved": exc["resolved"],
        "tasks": exc["tasks"],
        "items": items,
    }


def build_site(cutoff: str) -> dict:
    close_dir = DATA_DIR / "closes" / cutoff
    pkg = _read(close_dir / "package.json")
    memo = _read(close_dir / "memo.json")
    exc = _read(close_dir / "exceptions.json")
    slack = _read(close_dir / "slack_payload.json")
    chains = load_chains()
    names = {k: c.name for k, c in chains.items()}
    runs = read_runs()
    close_runs = [r for r in runs if r["kind"] == "close" and r["params"].get("cutoff") == cutoff]
    last_ok = next((r for r in reversed(close_runs) if r["status"] == "ok"), None)

    attempts = []
    att_path = close_dir / "attempts.jsonl"
    if att_path.exists():
        for line in att_path.read_text(encoding="utf-8").splitlines():
            a = json.loads(line)
            attempts.append(
                {
                    "token": a["token"],
                    "attempt": a["attempt"],
                    "accepted": a["accepted"],
                    "problems": [
                        p.get("code", str(p)) if isinstance(p, dict) else str(p)
                        for p in a["problems"]
                    ],
                    "duration": _duration(a.get("duration_s", 0)),
                }
            )

    rows = pkg["by_network"]
    new_networks = []
    for n in pkg["networks"]:
        if n["is_new"]:
            created = sorted(n["new_tokens"].items(), key=lambda kv: kv[1]["creation_block"])
            first = created[0][1]
            new_networks.append(
                {
                    "name": n["name"],
                    "first_creation_block": first["creation_block"],
                    "first_creation_block_url": first["creation_block_url"],
                    "tokens": [
                        {
                            "symbol": s,
                            "creation_block": v["creation_block"],
                            "creation_block_url": v["creation_block_url"],
                        }
                        for s, v in created
                    ],
                }
            )
    package_step = next(
        (s for s in (last_ok or {}).get("steps", []) if s["key"] == "package"), None
    )
    transactions = len({(m["chain"], m["tx_hash"]) for m in pkg["movements"]})
    eval_ = _read(DATA_DIR / "golden" / "extraction_eval.json")

    return {
        "close": {
            "cutoff": _d(pkg["cutoff"]),
            "previous_cutoff": _d(pkg["previous_cutoff"]),
            "cutoff_instant": f"{_d(pkg['cutoff'])} 23:59:59, hora de Buenos Aires",
            "generated_at": _local(pkg["generated_at"]),
            "tokens": _tokens(pkg, names),
            "networks": [
                {
                    "name": n["name"],
                    "cutoff_block": n["cutoff_block"],
                    "cutoff_block_url": n["cutoff_block_url"],
                    "is_new": n["is_new"],
                }
                for n in pkg["networks"]
            ],
            "new_networks": new_networks,
            "networks_checked": pkg["networks_checked_names"],
            "reconciled": sum(r["passed"] for r in rows),
            "reconciliations": len(rows),
            "all_reconciled": pkg["all_reconciled"],
            "movements": len(pkg["movements"]),
            "transactions": transactions,
            "bridge_pairs": pkg["bridge"]["pairs_matched"],
            "convention_note": pkg["convention_check"]["note"],
            "excel_file": f"paquete_cierre_{cutoff}.xlsx",
            "memo": [
                {
                    "symbol": s,
                    "headline": t["headline"],
                    "status": t["status"],
                    "review": t["review"],
                    "explanation": t["explanation"]["text"],
                    "explanation_source": t["explanation"]["source"],
                }
                for s, t in (memo or {"tokens": {}})["tokens"].items()
            ],
        },
        "timeline": _timeline(last_ok, names),
        "agent_vs_manual": None
        if last_ok is None
        else {
            "transactions": package_step["counts"]["transactions"]
            if package_step
            else transactions,
            "networks": len(pkg["networks"]),
            "tokens": len(pkg["tokens"]),
            "duration": _duration(last_ok["duration_s"]),
        },
        "verification": _verification(names),
        "methodology": [
            {
                "decision": m["decision"],
                "label": LABELS[m["label"]],
                "label_key": m["label"],
                "source": m["source"],
            }
            for m in pkg["methodology"]
        ],
        "exceptions": _exceptions(exc, close_dir / "exceptions_log.jsonl", names),
        "verifier": {
            "model": (memo or {}).get("model"),
            "attempts": attempts,
            "cases": [{"text": t, "code": c, "problem": p} for t, c, p in REJECT_CASES],
        },
        "extraction_eval": None
        if eval_ is None
        else {
            "documents": eval_["documents"],
            "all_fields_ok": eval_["documents_all_fields_ok"],
            "per_field_ok": eval_["per_field_ok"],
        },
        "slack": {
            # Sent only when the last close run's notify step really posted it.
            "dry_run": not any(
                st["key"] == "notify" and st["status"] == "ok"
                for st in (last_ok or {}).get("steps", [])
            ),
            "payload": slack,
        },
        "runs": [
            {
                "kind": r["kind"],
                "started_at": _local(r["started_at"]),
                "duration": _duration(r["duration_s"]),
                "status": r["status"],
            }
            for r in reversed(runs[-10:])
        ],
        "monitor": _read(DATA_DIR / "monitor" / "latest.json"),
    }
