"""Exception agent: investigates each movement the engine left for review.

The LLM picks one read only tool per step (pipeline/cierre/agent/tools.py) and ends with a
proposal. Code then checks the proposal against the chain: roles, amounts, hashes and
order of blocks. Only a proposal that passes is accepted. Everything else, and anything
the agent could not prove, becomes a task for a person. Every step goes to the run log.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from cierre.abi import BridgeIn, decode
from cierre.agent import verifier
from cierre.agent.llm import Ask, LlmError, Reply
from cierre.agent.tools import TOOL_SPECS, Tools
from cierre.rpc import RpcError

SYSTEM = (Path(__file__).parent / "prompts" / "exceptions_system.md").read_text(encoding="utf-8")
MAX_STEPS = 8
# Used when the agent's last summary still fails the verifier.
NO_SUMMARY = (
    "El resumen del agente no pasó el verificador en el último paso, así que no se publica. "
    "Revisá el movimiento con el link de la transacción."
)
# Longest tool result put back in the prompt.
RESULT_CHARS = 3500

SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": [*TOOL_SPECS, "propose"]},
        "args": {"type": "object"},
    },
    "required": ["thought", "action", "args"],
}

# Category the package gives a movement once the agent proved its proposal.
CATEGORY_FOR = {
    "primary_by_minter": "primary",
    "redemption_by_minter": "redemption",
    "bridge_late_fulfillment": "bridge_out",
    "bridge_refund": "bridge_out",
}


def case_text(m: dict, reason: str) -> str:
    lines = [
        "Movimiento a investigar:",
        f"- red: {m['chain']}",
        f"- token: {m['token']}",
        f"- tipo: {'emisión' if m['kind'] == 'mint' else 'quema'}",
        f"- monto en unidades base: {m['amount']}",
        f"- bloque: {m['block']}",
        f"- transacción: {m['tx_hash']}",
        f"- cuenta que recibe o quema: {m['counterparty']}",
        f"- motivo por el que quedó para revisar: {reason}",
    ]
    ev = m.get("evidence") or {}
    if m.get("category") == "bridge_out":
        lines += [
            f"- depositId: {ev.get('deposit_id')}",
            f"- red de destino (chain id): {ev.get('dest_chain_id')}",
            f"- destinatario en destino: {ev.get('dest_recipient')}",
        ]
    return "\n".join(lines)


def _tools_text(tools: Tools) -> str:
    ids = ", ".join(f"{k} (chain id {c.chain_id})" for k, c in tools.chains.items())
    specs = "\n".join(f"- {name}: {desc}" for name, desc in TOOL_SPECS.items())
    return f"Redes válidas: {ids}.\nTokens: {', '.join(tools.by_symbol)}.\n\n{specs}"


def _verify_minter(kind: str, proposal: dict, m: dict, tools: Tools) -> tuple[bool, list[str]]:
    want = "mint" if kind == "primary_by_minter" else "burn"
    if m["kind"] != want:
        return False, [f"la propuesta es para una {want} y el movimiento no lo es"]
    tx = tools.get_transaction(m["chain"], m["tx_hash"])
    account = str(proposal.get("account", "")).lower()
    contract = str(proposal.get("contract", "")).lower()
    if account not in (tx["from"], tx["to"]):
        return False, ["la cuenta propuesta no es quien envió ni el destino de la transacción"]
    known = {k.address for k in tools.known if k.chain == m["chain"]}
    if contract != tools.by_symbol[m["token"]] and contract not in known:
        return False, ["el contrato no es el token ni un contrato conocido de esa red"]
    if kind == "redemption_by_minter" and m["counterparty"].lower() != account:
        return False, ["quien quema no es la cuenta propuesta"]
    role = tools.has_role(m["chain"], contract, "MINTER_ROLE", account, m["block"])
    return role["has_role"], [
        f"MINTER_ROLE de {account} en {contract} al bloque {m['block']}: "
        f"{'sí' if role['has_role'] else 'no'}"
    ]


def _verify_late_fulfillment(proposal: dict, m: dict, tools: Tools) -> tuple[bool, list[str]]:
    ev = m["evidence"]
    dest = str(proposal.get("dest_chain", ""))
    if dest not in tools.chains or tools.chains[dest].chain_id != int(ev["dest_chain_id"]):
        return False, ["la red de destino no coincide con la del depósito"]
    receipt = tools.receipt(dest, proposal.get("dest_tx"))
    for lg in receipt["logs"]:
        e = decode(lg)
        if (
            isinstance(e, BridgeIn)
            and e.source_deposit_id == int(ev["deposit_id"])
            and e.source_tx_hash[-64:] == m["tx_hash"][-64:]
            and e.source_chain_id == tools.chains[m["chain"]].chain_id
            and e.amount == int(m["amount"])
        ):
            return True, ["la emisión de destino trae el mismo depositId, hash y monto"]
    return False, ["la transacción de destino no tiene una emisión del puente para este depósito"]


def _verify_refund(proposal: dict, m: dict, tools: Tools) -> tuple[bool, list[str]]:
    refund = tools.get_transaction(m["chain"], str(proposal.get("refund_tx", "")))
    ok = (
        refund["status"] == "ok"
        and refund["block"] > m["block"]
        and any(
            lg["event"] == "Transfer"
            and lg.get("from") == "0x" + "0" * 40
            and lg.get("to") == m["counterparty"].lower()
            and lg.get("token") == m["token"]
            and lg.get("amount_base_units") == m["amount"]
            for lg in refund["logs"]
        )
    )
    return ok, [
        "la devolución emite el mismo monto a la misma cuenta, después de la quema"
        if ok
        else "la transacción propuesta no devuelve el mismo monto a la misma cuenta"
    ]


def verify(proposal: dict, m: dict, tools: Tools) -> tuple[bool, list[str]]:
    """Re-check the proposal with code. Returns (accepted, checks done in Spanish)."""
    kind = proposal.get("kind")
    if kind in ("primary_by_minter", "redemption_by_minter"):
        return _verify_minter(kind, proposal, m, tools)
    if kind in ("bridge_late_fulfillment", "bridge_refund"):
        if m.get("category") != "bridge_out":
            return False, ["el movimiento no es una salida del puente"]
        check = _verify_late_fulfillment if kind == "bridge_late_fulfillment" else _verify_refund
        return check(proposal, m, tools)
    return False, ["sin propuesta verificable"]


def _ask_with_retry(ask: Ask, prompt: str, step: int, log: Callable[[dict], None]) -> Reply | None:
    """One retry: a refusal or a timeout can be transient."""
    for attempt in (1, 2):
        try:
            return ask(SYSTEM, prompt, SCHEMA)
        except LlmError as exc:
            log({"step": step, "attempt": attempt, "error": str(exc)[:300]})
    return None


def investigate(m: dict, reason: str, tools: Tools, ask: Ask, log: Callable[[dict], None]) -> dict:
    """Run the agent loop on one movement. The outcome is "resolved" only when code accepted
    the proposal, otherwise "task"."""
    case = case_text(m, reason)
    tools_text = _tools_text(tools)
    history: list[str] = []
    for step in range(1, MAX_STEPS + 1):
        done = (
            "Lo que ya hiciste:\n" + "\n\n".join(history) if history else "Todavía no hiciste nada."
        )
        prompt = f"{tools_text}\n\n{case}\n\n{done}\n\nPaso {step} de {MAX_STEPS}."
        reply = _ask_with_retry(ask, prompt, step, log)
        if reply is None:
            return {
                "outcome": "task",
                "summary": "El modelo no respondió a esta consulta, así que el agente no pudo "
                "analizar el movimiento. Revisalo a mano con el link de la transacción.",
                "steps": step,
            }
        d = reply.data
        action, args = d.get("action"), d.get("args") or {}
        entry = {
            "step": step,
            "model": reply.model,
            "thought": d.get("thought"),
            "action": action,
            "args": args,
        }
        if action == "propose":
            problems = verifier.check_free_text(str(args.get("summary", "")))
            if problems and step < MAX_STEPS:
                log(entry | {"summary_rejected": [asdict(p) for p in problems]})
                history.append(
                    f"Paso {step}: propusiste {json.dumps(args, ensure_ascii=False)}\n"
                    + verifier.feedback(problems)
                    + "\nVolvé a proponer con un summary corregido."
                )
                continue
            if problems:
                args = args | {"summary": NO_SUMMARY}
            if args.get("kind") == "needs_person":
                accepted, checks = False, ["el agente pidió una persona"]
            else:
                # The proposal names hashes and fields the model chose: if the chain cannot
                # confirm them, the movement goes to a person instead of stopping the run.
                try:
                    accepted, checks = verify(args, m, tools)
                except (RpcError, LookupError, TypeError, ValueError) as exc:
                    accepted = False
                    checks = [f"no se pudo comprobar la propuesta ({type(exc).__name__})"]
            log(entry | {"accepted": accepted, "checks": checks})
            return {
                "outcome": "resolved" if accepted else "task",
                "kind": args.get("kind"),
                "proposal": args,
                "checks": checks,
                "summary": args.get("summary", ""),
                "steps": step,
            }
        if action not in TOOL_SPECS:
            text = f"error: unknown tool {action!r}"
        else:
            try:
                result = getattr(tools, action)(**args)
                text = json.dumps(result, ensure_ascii=False)[:RESULT_CHARS]
            except Exception as exc:  # a failed call goes back to the agent as text
                text = f"error: {type(exc).__name__}: {str(exc)[:200]}"
        log(entry | {"result_preview": text[:400]})
        history.append(f"Paso {step}: {action} {json.dumps(args)}\nResultado: {text}")
    return {
        "outcome": "task",
        "summary": "El agente no llegó a una conclusión en ocho pasos.",
        "steps": MAX_STEPS,
    }


def run(pkg: dict, tools: Tools, ask: Ask, out_dir: Path) -> dict:
    log_path = out_dir / "exceptions_log.jsonl"
    log_path.unlink(missing_ok=True)
    by_key = {(m["chain"], m["tx_hash"], m["kind"], m["amount"]): m for m in pkg["movements"]}
    results = []
    for item in pkg["review"]:
        m = by_key[(item["chain"], item["tx_hash"], item["kind"], item["amount"])]
        header = {
            "chain": m["chain"],
            "token": m["token"],
            "tx_hash": m["tx_hash"],
            "log_index": m["log_index"],
            "kind": m["kind"],
        }

        def log(entry, header=header):
            with open(log_path, "a", encoding="utf-8", newline="\n") as f:
                rec = header | entry | {"at": datetime.now(UTC).isoformat(timespec="seconds")}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        res = investigate(m, item["reason"], tools, ask, log)
        # The proposal's kind gets its own key so it does not replace the movement's kind.
        proposal_kind = res.pop("kind", None)
        results.append(
            header
            | {
                "amount": m["amount"],
                "reason": item["reason"],
                "explorer_url": item["explorer_url"],
            }
            | res
            | {"proposal_kind": proposal_kind}
        )
        print(
            f"{m['chain']:10} {m['token']} {m['kind']} -> {res['outcome']} "
            f"({proposal_kind}, {res['steps']} pasos)",
            flush=True,
        )
    return {
        "cutoff": pkg["cutoff"],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "investigated": len(results),
        "resolved": sum(r["outcome"] == "resolved" for r in results),
        "tasks": sum(r["outcome"] == "task" for r in results),
        "results": results,
    }


def overrides(exceptions: dict) -> dict[tuple[str, str, int], dict]:
    """Movements the agent proved, keyed by (chain, tx_hash, log_index), for the package."""
    out = {}
    for r in exceptions.get("results", []):
        # Files written before proposal_kind existed kept the proposal's kind under "kind".
        kind = r.get("proposal_kind", r.get("kind"))
        if r["outcome"] == "resolved" and kind in CATEGORY_FOR:
            out[(r["chain"], r["tx_hash"], r["log_index"])] = {
                "category": CATEGORY_FOR[kind],
                "resolved_by_agent": {
                    "kind": kind,
                    "checks": r["checks"],
                    "summary": r["summary"],
                    "proposal": r["proposal"],
                },
            }
    return out
