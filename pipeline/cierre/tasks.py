"""GitHub issues for what needs a person: one per movement the exception agent could not
prove, labeled with the close. Runs again without duplicates (a marker in the body).

Uses GITHUB_TOKEN and GITHUB_REPOSITORY (set by GitHub Actions). Without them it is a
dry run: the issues it would open are written to tasks.json and nothing is sent.
"""

from __future__ import annotations

import os

import httpx

from cierre.agent.memo import fmt, fmt_date
from cierre.config import load_tokens
from cierre.package import units

API = "https://api.github.com"
CLOSE_LABEL_COLOR = "5319e7"
REVIEW_LABEL = ("revisar", "d93f0b", "Un movimiento que el agente no pudo probar")
DEMO_NOTE = (
    "> **Demo independiente.** Este issue lo abrió de forma automática el agente de una demo "
    "independiente de Máximo Sckell, hecha solo con datos públicos (las blockchains y las "
    "certificaciones que publica Ripio). No es una herramienta oficial de Ripio y no afirma "
    "ningún error de Ripio. Marca un movimiento que el agente no pudo clasificar con "
    "información pública y que en un cierre real revisaría una persona de Finanzas."
)


def describe_movement(r: dict) -> tuple[str, str]:
    """Kind and amount of a movement row, in Spanish: ('quema', '985,00 wARS')."""
    decimals = load_tokens()[r["token"]].decimals
    kind = "emisión" if r["kind"] == "mint" else "quema"
    return kind, f"{fmt(units(r['amount'], decimals))} {r['token']}"


def marker(cutoff: str, r: dict) -> str:
    return f"<!-- cierre-task {cutoff} {r['chain']} {r['tx_hash']} {r['log_index']} -->"


def issue_for(cutoff: str, r: dict, chain_names: dict[str, str]) -> dict:
    kind, amount = describe_movement(r)
    chain = chain_names[r["chain"]]
    body = "\n".join(
        [
            marker(cutoff, r),
            DEMO_NOTE,
            "",
            f"**Por qué quedó para revisar:** {r['reason']}",
            "",
            f"**Lo que encontró el agente:** {r['summary']}",
            "",
            "**Movimiento**",
            f"- Red: {chain}",
            f"- Moneda: {r['token']}",
            f"- Tipo: {kind}",
            f"- Monto: {amount}",
            f"- Transacción: [ver en el explorador]({r['explorer_url']})",
            "",
            "**Qué te toca:** revisá el movimiento con el link de la transacción y cerrá "
            "este issue con lo que decidas. El agente no lo pudo probar con evidencia, así "
            "que no lo clasificó.",
        ]
    )
    return {
        "title": f"Revisar {kind} de {amount} en {chain} (cierre {fmt_date(cutoff)})",
        "body": body,
        "labels": [f"cierre-{cutoff}", REVIEW_LABEL[0]],
    }


def open_issues(
    cutoff: str,
    exceptions: dict,
    chain_names: dict[str, str],
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Open one issue per task, or find the one a previous run opened. `transport` lets
    the tests answer for GitHub."""
    tasks = [r for r in exceptions["results"] if r["outcome"] == "task"]
    wanted = [(r, issue_for(cutoff, r, chain_names)) for r in tasks]
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return {
            "status": "dry_run",
            "issues": [_entry(r, i, None, "dry_run") for r, i in wanted],
        }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(base_url=API, headers=headers, timeout=30, transport=transport) as gh:
        label = f"cierre-{cutoff}"
        for name, color, desc in (
            (label, CLOSE_LABEL_COLOR, f"Tareas del cierre al {fmt_date(cutoff)}"),
            REVIEW_LABEL,
        ):
            resp = gh.post(
                f"/repos/{repo}/labels", json={"name": name, "color": color, "description": desc}
            )
            if resp.status_code not in (201, 422):  # 422: the label already exists
                resp.raise_for_status()
        resp = gh.get(
            f"/repos/{repo}/issues", params={"labels": label, "state": "all", "per_page": 100}
        )
        resp.raise_for_status()
        existing = {m: i for i in resp.json() if (m := _marker_of(i.get("body") or "")) is not None}
        out = []
        for r, issue in wanted:
            found = _existing_issue(cutoff, r, existing, tasks)
            if found:
                out.append(_entry(r, issue, found, "existing"))
                continue
            resp = gh.post(f"/repos/{repo}/issues", json=issue)
            resp.raise_for_status()
            out.append(_entry(r, issue, resp.json(), "opened"))
    return {"status": "ok", "issues": out}


def _existing_issue(
    cutoff: str, r: dict, existing: dict[str, dict], tasks: list[dict]
) -> dict | None:
    """The issue a previous run opened for this task. The marker holds the log index, which
    HyperEVM nodes do not agree on, so a marker for the same transaction also counts when
    only one task and one issue share that transaction."""
    exact = existing.get(marker(cutoff, r))
    if exact:
        return exact
    prefix = f"<!-- cierre-task {cutoff} {r['chain']} {r['tx_hash']} "
    same_tx = [i for m, i in existing.items() if m.startswith(prefix)]
    siblings = [t for t in tasks if (t["chain"], t["tx_hash"]) == (r["chain"], r["tx_hash"])]
    return same_tx[0] if len(same_tx) == 1 and len(siblings) == 1 else None


def _marker_of(body: str) -> str | None:
    first = body.splitlines()[0] if body else ""
    return first if first.startswith("<!-- cierre-task ") else None


def _entry(r: dict, issue: dict, gh_issue: dict | None, status: str) -> dict:
    return {
        "chain": r["chain"],
        "tx_hash": r["tx_hash"],
        "log_index": r["log_index"],
        "title": issue["title"],
        "status": status,
        "number": gh_issue["number"] if gh_issue else None,
        "url": gh_issue["html_url"] if gh_issue else None,
        "state": gh_issue.get("state") if gh_issue else None,
    }
