"""Light JSON for the page (data/site/site.json).

The page only displays this file. Every figure is formatted here from the close files with
Decimal, and every sentence with a number in it is a template over them. The only model
text on the page is the memo explanation, already checked by the verifier. The quoted text
of the certificates and the notes of the extraction eval are not exported.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from cierre import DATA_DIR, REPO_ROOT
from cierre.agent.memo import fmt, fmt_date
from cierre.agent.verifier_cases import REJECT_CASES
from cierre.config import load_chains, load_tokens
from cierre.gitops import commit_and_push
from cierre.jsonio import dump_json, load_json, load_json_if_exists
from cierre.package import ART, VERIFIED_LINE, source_check_line, units
from cierre.runlog import RUNS_PATH, read_runs

SLACK_SCREENSHOT = "slack_captura.png"
RECENT_RUNS = 10  # runs listed on the page

TRIGGERS = {
    "schedule": "programada",
    "workflow_dispatch": "manual en GitHub",
    "push": "por un cambio",
    "local": "local",
}

TOOL_LABELS = {
    "get_transaction": "Leyó la transacción y sus eventos",
    "contract_info": "Miró el contrato",
    "has_role": "Consultó permisos de emisión",
    "find_mints_to": "Buscó emisiones hacia la cuenta",
    "find_bridge_fulfillment": "Buscó la emisión del puente en la red de destino",
    "propose": "Propuso una clasificación",
}

LABELS = {"documented": "Documentado", "inferred": "Inferido", "hypothesis": "Hipótesis"}


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


def _tokens(pkg: dict, names: dict[str, str], labels: dict[str, str]) -> list[dict]:
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
                "name": labels.get(sym, t["name"]),
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
            title = f"Detectó el cierre del {fmt_date(c['cutoff'])}"
            detail = (
                f"Corte a las 23:59:59 hora de Buenos Aires. Cierre anterior: "
                f"{fmt_date(c['previous_cutoff'])}."
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
            elif c.get("same_file"):
                title = "Publicó el Excel y comprobó que el link sirve ese mismo archivo"
                detail = c.get("url", "")
            else:
                title = "Publicó el Excel y comprobó que el link responde"
                detail = c.get("url", "")
        elif k == "tasks":
            n = c["tasks"]
            title = f"Abrió {_plural(n, 'tarea', 'tareas')} para revisar"
            if s["status"] == "dry_run":
                title += " (modo de prueba)"
            detail = (
                "Una por cada movimiento que el agente no pudo probar, con su evidencia y el "
                "link de la transacción."
            )
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


MONTHS = {"1": "enero", "4": "abril", "7": "julio", "10": "octubre"}
ART_UTC_OFFSET_HOURS = -3  # Argentina has no daylight saving time


def _cron_text(cron: str) -> str:
    """The two schedules the workflows use (daily, and quarterly on day 1), in Spanish."""
    minute, hour, dom, month, dow = cron.split()
    at = f"{(int(hour) + ART_UTC_OFFSET_HOURS) % 24:02d}:{int(minute):02d} de Buenos Aires"
    if (dom, month, dow) == ("*", "*", "*"):
        return f"Todos los días a las {at}"
    if (dom, month, dow) == ("1", "1,4,7,10", "*"):
        months = [MONTHS[m] for m in month.split(",")]
        listed = ", ".join(months[:-1]) + f" y {months[-1]}"
        return f"El 1 de {listed} a las {at}, el día siguiente a cada fin de trimestre"
    return f"cron {cron} (UTC)"


def _schedule(workflow: str) -> str | None:
    path = REPO_ROOT / ".github" / "workflows" / workflow
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = raw.get("on", raw.get(True)) or {}  # PyYAML reads the key `on` as True
    crons = [c["cron"] for c in on.get("schedule") or []]
    return _cron_text(crons[0]) if crons else None


def _production(runs: list[dict]) -> dict:
    """What the page shows under "En producción": schedules, CI and the daily monitor."""
    mon = load_json_if_exists(DATA_DIR / "monitor" / "latest.json")
    monitor_runs = [r for r in runs if r["kind"] == "monitor"]
    last = monitor_runs[-1] if monitor_runs else None
    last_ok = next((r for r in reversed(monitor_runs) if r["status"] == "ok"), None)
    notify_states = [st["status"] for r in runs for st in r["steps"] if st["key"] == "notify"]
    ci = (mon or {}).get("ci")
    g = (mon or {}).get("golden_live")
    return {
        "schedule": {"monitor": _schedule("daily.yml"), "close": _schedule("close.yml")},
        "ci": None
        if not ci or ci.get("error") or not ci.get("at")
        else {
            "conclusion": ci["conclusion"],
            "at": _local(ci["at"].replace("Z", "+00:00")),
            "sha": ci["sha"],
        },
        "monitor": None
        if last is None or mon is None
        else {
            "last_run_at_iso": last["started_at"],
            "last_run_status": last["status"],
            "last_ok_at_iso": last_ok["started_at"] if last_ok else None,
            "networks_checked": len(mon["networks_checked"]),
            "golden_live_all_match": bool(g) and g["matched"] == g["total"],
            "golden_live_total": g["total"] if g else None,
            "changes": mon.get("changes", []),
            "problems": mon.get("problems", []),
        },
        "slack_live": "ok" in notify_states,
    }


def _verification(names: dict[str, str], decimals: dict[str, int]) -> dict:
    ver = load_json(DATA_DIR / "golden" / "verification.json")
    certs = {
        (c["token"], c["cutoff"]): c
        for c in load_json(DATA_DIR / "golden" / "certifications.json")["certifications"]
    }
    rows = []
    for r in ver["rows"]:
        cert = certs[(r["token"], r["cutoff"])]
        rows.append(
            {
                "token": r["token"],
                "cutoff": fmt_date(r["cutoff"]),
                # As printed in the certificate (2 decimals or whole units).
                "certified": fmt(r["certified"], r["printed_decimals"]),
                "raw_sum": fmt(r["computed"])
                if not r["adjustments"]
                else fmt(units(r["raw_sum_base_units"], decimals[r["token"]])),
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
                "signed": fmt_date(cert["signed_date"]),
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


def _exceptions(
    exc: dict | None, pkg: dict, log_path: Path, names: dict[str, str], decimals: dict[str, int]
) -> dict | None:
    if exc is None:
        return None
    # Keyed without log_index: HyperEVM endpoints do not agree on log numbering.
    movement_kind = {(m["chain"], m["tx_hash"], m["amount"]): m["kind"] for m in pkg["movements"]}
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
        key = (r["chain"], r["tx_hash"], r["log_index"])
        mint = movement_kind[(r["chain"], r["tx_hash"], r["amount"])] == "mint"
        kind = "emisión" if mint else "quema"
        amount = fmt(units(r["amount"], decimals[r["token"]]))
        items.append(
            {
                "outcome": r["outcome"],
                "chain": names[r["chain"]],
                "token": r["token"],
                "movement": f"{kind} de {amount} {r['token']}",
                "reason": r["reason"],
                "summary": r["summary"],
                "checks": r.get("checks") or [],
                "explorer_url": r["explorer_url"],
                "steps": tools.get(key, []),
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
    pkg = load_json(close_dir / "package.json")
    memo = load_json_if_exists(close_dir / "memo.json")
    exc = load_json_if_exists(close_dir / "exceptions.json")
    slack = load_json_if_exists(close_dir / "slack_payload.json")
    chains = load_chains()
    names = {k: c.name for k, c in chains.items()}
    labels = {s: t.name_es or t.name for s, t in load_tokens(include_unconfirmed=True).items()}
    decimals = {s: t["decimals"] for s, t in pkg["tokens"].items()}
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
                    # An attempt the model never answered carries "error" and no problems
                    "problems": [
                        p.get("code", str(p)) if isinstance(p, dict) else str(p)
                        for p in a.get("problems", [])
                    ]
                    or (["sin respuesta del modelo"] if a.get("error") else []),
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
    extraction = load_json_if_exists(DATA_DIR / "golden" / "extraction_eval.json")

    return {
        "close": {
            "cutoff": fmt_date(pkg["cutoff"]),
            "previous_cutoff": fmt_date(pkg["previous_cutoff"]),
            "cutoff_instant": f"{fmt_date(pkg['cutoff'])} 23:59:59, hora de Buenos Aires",
            "generated_at": _local(pkg["generated_at"]),
            "tokens": _tokens(pkg, names, labels),
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
            # Text from the package, so the page, the Excel and Slack say the same thing.
            "verified_note": " ".join(
                ([VERIFIED_LINE] if pkg["all_reconciled"] else [])
                + (
                    [source_check_line(pkg["source_check"], len(rows))]
                    if pkg.get("source_check")
                    else []
                )
            ),
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
        "verification": _verification(names, decimals),
        "methodology": [
            {
                "decision": m["decision"],
                "label": LABELS[m["label"]],
                "label_key": m["label"],
                "source": m["source"],
            }
            for m in pkg["methodology"]
        ],
        "exceptions": _exceptions(exc, pkg, close_dir / "exceptions_log.jsonl", names, decimals),
        "verifier": {
            "model": (memo or {}).get("model"),
            "attempts": attempts,
            "cases": [{"text": t, "code": c, "problem": p} for t, c, p in REJECT_CASES],
        },
        "extraction_eval": None
        if extraction is None
        else {
            "documents": extraction["documents"],
            "all_fields_ok": extraction["documents_all_fields_ok"],
            "per_field_ok": extraction["per_field_ok"],
        },
        "slack": {
            # A dry run unless the last close run's notify step really posted the message
            "dry_run": not any(
                st["key"] == "notify" and st["status"] == "ok"
                for st in (last_ok or {}).get("steps", [])
            ),
            "payload": slack,
            # Screenshot of the message in the channel, added by hand after a live send
            "screenshot": (SLACK_SCREENSHOT if (close_dir / SLACK_SCREENSHOT).exists() else None),
        },
        "runs": [
            {
                "kind": r["kind"],
                "trigger": TRIGGERS.get(r.get("trigger", "local"), r.get("trigger")),
                "started_at": _local(r["started_at"]),
                "duration": _duration(r["duration_s"]),
                "status": r["status"],
            }
            for r in reversed(runs[-RECENT_RUNS:])
        ],
        "production": _production(runs),
    }


def latest_close() -> str:
    """Cutoff of the newest close that has a package, which is the one the page shows."""
    return max(p.parent.name for p in (DATA_DIR / "closes").glob("*/package.json"))


def write_site(cutoff: str | None = None) -> Path:
    """Rebuild data/site/site.json for `cutoff` (default: the newest close)."""
    path = DATA_DIR / "site" / "site.json"
    dump_json(path, build_site(cutoff or latest_close()))
    return path


def publish_run_log(message: str) -> None:
    """After a failed run: push only the run log and the page data, so the page shows it."""
    site_json = write_site()
    commit_and_push([RUNS_PATH, site_json], message)
