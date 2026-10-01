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


def task_line(r: dict, chain_names: dict[str, str]) -> str:
    amount = fmt(units(r["amount"], 18))
    kind = "emisión" if r["kind"] == "mint" else "quema"
    return (
        f"• {chain_names[r['chain']]}, {kind} de {amount} {r['token']}: {r['reason']} "
        f"<{r['explorer_url']}|ver transacción>"
    )


def close_message(
    pkg: dict,
    memo: dict,
    exceptions: dict | None,
    verification: dict,
    excel_url: str,
    chain_names: dict[str, str],
) -> dict:
    cut = _d(pkg["cutoff"])
    lines = [f"• *{t['headline']}*. {t['status']}" for t in memo["tokens"].values()]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Cierre wFIAT al {cut}"}},
        _section("El agente armó el paquete onchain del cierre. Está listo para que lo revisés."),
        _section("\n".join(lines)),
    ]
    alerts = []
    for n in pkg["networks"]:
        if n["is_new"]:
            blk = next(iter(n["new_tokens"].values()))["creation_block"]
            alerts.append(
                f":warning: Red nueva desde el último cierre: *{n['name']}* "
                f"(contratos creados desde el bloque {blk})."
            )
    if not pkg["all_reconciled"]:
        alerts.append(":rotating_light: Hay redes que no concilian. Revisá la hoja Conciliación.")
    if alerts:
        blocks.append(_section("\n".join(alerts)))
    if exceptions:
        tasks = [r for r in exceptions["results"] if r["outcome"] == "task"]
        head = (
            f"*Qué te toca revisar* ({len(tasks)})\n"
            f"El agente investigó {exceptions['investigated']} movimientos, resolvió "
            f"{exceptions['resolved']} con evidencia y te deja estos:"
        )
        body = "\n".join(task_line(r, chain_names) for r in tasks) or "Nada pendiente."
        blocks.append(_section(head + "\n" + body))
    blocks.append(
        _section(
            f"*Verificación:* el método coincide con la cantidad certificada por el contador "
            f"en {verification['matched']} de las {verification['total']} certificaciones "
            f"publicadas. Redes revisadas: {len(pkg['networks_checked'])}."
        )
    )
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


def alert_message(step: str, error: str) -> dict:
    text = f":rotating_light: El cierre se detuvo en el paso *{step}*. No hay paquete publicado."
    return {
        "text": f"Cierre wFIAT detenido en {step}",
        "blocks": [_section(text), _section(f"```{error[:500]}```")],
    }


def send(payload: dict, dry_run_path: Path) -> str:
    """Post to the webhook, or write the payload to a file when there is none."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        dry_run_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return f"dry run: {dry_run_path}"
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return "sent"
