"""Slack message for Finance, as Block Kit, sent through an Incoming Webhook.

Every figure and status comes from templates over the package, the memo and the
verification files. Without SLACK_WEBHOOK_URL (dry run), the payload is written to a file.
A message without a reachable Excel link is an alert, never a close notice (decision 12).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from cierre.agent.memo import fmt
from cierre.package import units


def _d(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def task_line(r: dict, chain_names: dict[str, str], issue_url: str | None = None) -> str:
    amount = fmt(units(r["amount"], 18))
    kind = "emisión" if r["kind"] == "mint" else "quema"
    issue = f" · <{issue_url}|tarea>" if issue_url else ""
    return (
        f"• {chain_names[r['chain']]}, {kind} de {amount} {r['token']}: {r['reason']} "
        f"<{r['explorer_url']}|ver transacción>{issue}"
    )


def close_message(
    pkg: dict,
    memo: dict,
    exceptions: dict | None,
    verification: dict,
    excel_url: str,
    chain_names: dict[str, str],
    tasks: dict | None = None,
) -> dict:
    """`tasks` is the output of tasks.open_issues: each task line links its issue."""
    cut = _d(pkg["cutoff"])
    issue_urls = {
        (i["chain"], i["tx_hash"], i["log_index"]): i["url"]
        for i in (tasks or {}).get("issues", [])
    }
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Cierre wFIAT al {cut}"}},
        _section("El agente armó el paquete onchain del cierre. Está listo para que lo revisés."),
    ]

    # First what needs a person (Maxi's order, 2026-10-02), then everything that is in order.
    review = []
    if not pkg["all_reconciled"]:
        review.append(":rotating_light: Hay redes que no concilian. Revisá la hoja Conciliación.")
    if exceptions:
        pending = [r for r in exceptions["results"] if r["outcome"] == "task"]
        if pending:
            review.append(
                f":mag: *Se requiere tu revisión en {len(pending)} "
                f"{'movimiento' if len(pending) == 1 else 'movimientos'}*\n"
                f"El agente investigó {exceptions['investigated']} movimientos, resolvió "
                f"{exceptions['resolved']} con evidencia y te deja estos:\n"
                + "\n".join(
                    task_line(
                        r, chain_names, issue_urls.get((r["chain"], r["tx_hash"], r["log_index"]))
                    )
                    for r in pending
                )
            )
    if review:
        blocks.append(_section("\n\n".join(review)))

    all_good = pkg["all_reconciled"] and verification["matched"] == verification["total"]
    if all_good and review:
        ok_head = ":white_check_mark: *Todo lo demás está conciliado y verificado*"
    elif all_good:
        ok_head = ":white_check_mark: *Todo está conciliado y verificado*"
    else:
        ok_head = "*Resto del cierre*"
    lines = [f"• *{t['headline']}*. {t['status']}" for t in memo["tokens"].values()]
    notes = []
    for n in pkg["networks"]:
        if n["is_new"]:
            blk = next(iter(n["new_tokens"].values()))["creation_block"]
            notes.append(
                f":new: Red nueva desde el último cierre: *{n['name']}* "
                f"(contratos creados desde el bloque {blk})."
            )
    notes.append(
        f"*Verificación:* el método coincide con la cantidad certificada por el contador "
        f"en {verification['matched']} de las {verification['total']} certificaciones "
        f"publicadas. Redes revisadas: {len(pkg['networks_checked'])}."
    )
    blocks.append(_section(ok_head + "\n" + "\n".join(lines) + "\n\n" + "\n".join(notes)))
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Descargar el Excel"},
                    "url": excel_url,
                    "style": "primary",
                }
            ],
        }
    )
    return {"text": f"Cierre wFIAT al {cut} listo para revisión", "blocks": blocks}


PACKAGE_STATE = {
    "none": " No hay paquete publicado.",
    "pushed": " El paquete se subió al repo, pero el sitio no lo sirvió a tiempo. "
    "No se avisó a Finanzas.",
    "published": " El paquete quedó publicado, pero el aviso no se completó. Revisá la corrida.",
}


def alert_message(step: str, error: str, what: str = "El cierre", package: str = "none") -> dict:
    """An alert never carries the Excel link (decision 12)."""
    text = f":rotating_light: {what} se detuvo en el paso *{step}*."
    if what == "El cierre":
        text += PACKAGE_STATE[package]
    return {
        "text": f"{what} wFIAT detenido en {step}",
        "blocks": [_section(text), _section(f"```{error[:500]}```")],
    }


def monitor_message(changes: list[str], problems: list[str], networks: int) -> dict:
    """Daily monitor: sent only when something changed or a check failed."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Monitor diario wFIAT"}}]
    if problems:
        lines = "\n".join(f"• {p}" for p in problems)
        blocks.append(_section(f":rotating_light: *Falló un chequeo*\n{lines}"))
    if changes:
        lines = "\n".join(f"• {c}" for c in changes)
        blocks.append(_section(f"*Qué cambió*\n{lines}"))
    blocks.append(_section(f"Redes revisadas: {networks}."))
    title = "Monitor wFIAT: falló un chequeo" if problems else "Monitor wFIAT: hay cambios"
    return {"text": title, "blocks": blocks}


def send(payload: dict, record_path: Path) -> str:
    """Write the payload to a file, then post it when there is a webhook. The file is the
    record of what Finance received, so the page shows the message that was really sent."""
    record_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return f"dry run: {record_path}"
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return "sent"
