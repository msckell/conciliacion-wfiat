"""GitHub issues for what needs a person: one per movement the exception agent could not
prove, labeled with the close. Runs again without duplicates (a marker in the body).

Uses GITHUB_TOKEN and GITHUB_REPOSITORY (set by GitHub Actions). Without them it is a
dry run: the issues it would open are written to tasks.json and nothing is sent.
"""

from __future__ import annotations

import os

import httpx

from cierre.agent.memo import fmt
from cierre.package import units

API = "https://api.github.com"
REVIEW_LABEL = ("revisar", "d93f0b", "Un movimiento que el agente no pudo probar")


def _d(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def marker(cutoff: str, r: dict) -> str:
    return f"<!-- cierre-task {cutoff} {r['chain']} {r['tx_hash']} {r['log_index']} -->"


def issue_for(cutoff: str, r: dict, chain_names: dict[str, str]) -> dict:
    kind = "emisión" if r["kind"] == "mint" else "quema"
    amount = f"{fmt(units(r['amount'], 18))} {r['token']}"
    chain = chain_names[r["chain"]]
    body = "\n".join(
        [
            marker(cutoff, r),
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
        "title": f"Revisar {kind} de {amount} en {chain} (cierre {_d(cutoff)})",
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
            (label, "5319e7", f"Tareas del cierre al {_d(cutoff)}"),
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
        existing = {
            m: i for i in resp.json() for m in [_marker_of(i.get("body") or "")] if m is not None
        }
        out = []
        for r, issue in wanted:
            found = existing.get(marker(cutoff, r))
            if found:
                out.append(_entry(r, issue, found, "existing"))
                continue
            resp = gh.post(f"/repos/{repo}/issues", json=issue)
            resp.raise_for_status()
            out.append(_entry(r, issue, resp.json(), "opened"))
    return {"status": "ok", "issues": out}


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
