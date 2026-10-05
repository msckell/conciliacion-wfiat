"""Closing package: package.json for the site and an Excel workbook for the accountant.

Every figure comes from the engine output in base units. Units are derived with Decimal
and rounded only when shown. Labels are in Spanish, as Finance reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from cierre import REPO_ROOT
from cierre.agent.memo import fmt_date
from cierre.config import Chain, Token
from cierre.cutoffs import CONVENTIONS, period
from cierre.ledger import NOT_CONFIGURED, override_for
from cierre.scope import APPLICABLE, manifest_from_engine

ART = CONVENTIONS["ART"]

CATEGORY_ES = {
    "primary": "Emisión primaria",
    "redemption": "Rescate primario",
    "bridge_in": "Puente, entrada",
    "bridge_out": "Puente, salida",
    "unclassified": "Sin clasificar",
}
BRIDGE_ES = {
    "matched": "Emparejado dentro del trimestre",
    "mismatch": "Emparejado, pero monto o destinatario no coinciden",
    "in_flight_at_cutoff": "En tránsito al corte",
    "in_flight_at_previous_cutoff": "Salió antes del corte anterior",
    "source_unexplained": "Origen sin explicar",
    "source_not_checked": "Origen en una red no revisada",
    "dest_not_checked": "Destino en una red no revisada",
    "dest_bridge_unknown": "Contrato puente de destino sin identificar",
}
LABEL_ES = {"documented": "documentada", "inferred": "inferida", "hypothesis": "hipótesis"}
PAIR_ES = {
    "reconciled": "conciliado",
    "not_reconciled": "NO concilia",
    "error": "sin resultado, error",
    "missing": "sin resultado",
    "unknown": "alcance desconocido",
    "not_applicable": "no aplica",
}
REASON_ES = {
    "network_not_live": "la red no tenía bloques al corte",
    "created_after_cutoff": "el contrato se creó después del corte",
    "not_deployed": "el contrato no está desplegado en esta red",
}
# Second source check, an extra layer on top of the two path reconciliation.
VERIFICATION_ES = {
    "verified": "confirmado por una segunda fuente",
    "limited": "una sola fuente",
    "discrepancy": "las fuentes no coinciden",
}
# Pair statuses that leave the close incomplete.
INCOMPLETE = ("not_reconciled", "error", "missing", "unknown")


def units(raw: int | str, decimals: int) -> Decimal:
    return Decimal(int(raw)) / (Decimal(10) ** decimals)


def _dt(ts: int | None) -> str:
    """Unix time to 'dd/mm/yyyy hh:mm:ss' in Buenos Aires time, empty when unknown."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, UTC).astimezone(ART).strftime("%d/%m/%Y %H:%M:%S")


def build(
    engine: list[dict],
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    discovery: dict,
    coingecko: dict,
    certifications: list[dict],
    bridge_summary: dict,
    methodology: list[dict],
    convention: str,
    all_chains: dict[str, Chain] | None = None,
    overrides: list[dict] | None = None,
    manifest: dict | None = None,
) -> dict:
    """The package from the engine output.

    `manifest` is the expected scope (scope.py). Every applicable pair in it needs a
    reconciled result, or the close is incomplete. Without it, the scope is rebuilt from the
    configuration and the cutoff blocks in the engine output. `overrides` holds the
    movements the exception agent proved (see agent.exceptions.overrides)."""
    if manifest is None:
        manifest = manifest_from_engine(engine, chains, tokens, convention, discovery)
    by_chain = {c["chain"]: c for c in engine}
    cutoff, prev = manifest["cutoff"], manifest["previous_cutoff"]
    pairs = _scope_rows(manifest, by_chain)
    reconciled = {(r["token"], r["chain"]) for r in pairs if r["status"] == "reconciled"}
    applicable_chains = {r["chain"] for r in pairs if r["applicability"] in APPLICABLE}
    engine_errors = [
        {"chain": c["chain"], "error": c["error"]}
        for c in engine
        if c.get("error") and c["chain"] in applicable_chains
    ] + [
        {"chain": r["chain"], "token": r["token"], "error": f"{r['status']}: {r['detail']}"}
        for r in pairs
        if r["status"] in INCOMPLETE
    ]

    networks = _networks(by_chain, chains, tokens, cutoff, prev, convention, pairs)
    token_rows, network_rows, movements = _token_rows(
        by_chain, chains, tokens, convention, overrides, reconciled, pairs
    )
    movements.sort(key=lambda m: (m["timestamp"], m["chain"], m["log_index"]))
    between = [
        m
        for c in engine
        for t in c.get("tokens", {}).values()
        for m in t.get("movements_between_conventions", [])
    ]
    return {
        "cutoff": cutoff,
        "previous_cutoff": prev,
        "period": period(prev, cutoff),
        "convention": convention,
        "cutoff_instant": f"{cutoff} 23:59:59 {ART.key}",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **_status(pairs, network_rows),
        "engine_errors": engine_errors,
        "scope": _scope_summary(manifest, pairs, all_chains or chains),
        "networks": networks,
        "networks_checked": discovery["networks_checked"],
        "networks_checked_names": [
            (all_chains or chains)[k].name for k in discovery["networks_checked"]
        ],
        "new_networks": [n["chain"] for n in networks if n["is_new"]],
        "tokens": token_rows,
        "by_network": network_rows,
        "movements": movements,
        "review": _review_items(movements, chains),
        "bridge": bridge_summary,
        "convention_check": {
            "movements_between_art_and_utc": len(between),
            "note": "La convención horaria no cambia este cierre."
            if not between
            else "Hay movimientos entre las 23:59:59 UTC y las 23:59:59 de Buenos Aires.",
        },
        "cross_checks": _cross_checks(network_rows, coingecko, certifications),
        "methodology": methodology,
    }


def _scope_rows(manifest: dict, by_chain: dict[str, dict]) -> list[dict]:
    """One row per pair of the expected scope, with what the engine returned for it."""
    rows = []
    for p in manifest["pairs"]:
        c = by_chain.get(p["chain"])
        t = ((c or {}).get("tokens") or {}).get(p["token"])
        row = {
            "token": p["token"],
            "chain": p["chain"],
            "applicability": p["applicability"],
            "reason": p.get("reason"),
            "evidence": p.get("evidence"),
            "detail": None,
        }
        if p["applicability"] not in APPLICABLE:
            status = p["applicability"]
            if t and t.get("passed"):
                row["detail"] = "the engine returned a result, ignored: the pair is out of scope"
        elif t is None:
            status = "error" if c and c.get("error") else "missing"
            row["detail"] = (c or {}).get("error") or "the engine returned nothing for it"
        elif t.get("passed"):
            status = "reconciled"
            row["verification"] = _verification(t, c)
        elif t.get("status") in ("not_applicable", "unknown"):
            status = "error"
            row["detail"] = f"the engine says {t['status']}, the scope says applicable"
        elif "conventions" in t:
            status = "not_reconciled"
            row["detail"] = "no source reconciles"
        else:
            status = "error"
            row["detail"] = t.get("error") or "no result"
        rows.append(row | {"status": status})
    return rows


def _source_label(name: str) -> str:
    """'blockscout:https://base.blockscout.com/api' -> 'base.blockscout.com'."""
    rest = name.split(":", 1)[-1]
    return rest.split("//", 1)[-1].split("/", 1)[0]


def _verification(t: dict, c: dict) -> dict:
    """Whether a reconciled pair was also confirmed by an independent source. This is an
    extra layer: the pair is already verified against the chain by two paths (totalSupply at
    the cutoff block equals opening plus mints minus burns, exactly).

    verified: two or more reconciling sources hold the same events.
    discrepancy: two reconciling sources hold different events. A person must look.
    limited: only one source reconciles (the others failed, need a key, or returned an
    incomplete list). The only case it cannot see is a mint and a burn of the same amount
    missing together."""
    sc = t.get("source_comparison") or {}
    answered = sorted(set(t.get("sources_complete", [])) | set(t.get("sources_incomplete", [])))
    failures = c.get("sources_failed") or {}
    out = {
        "sources": answered,
        "not_reconciling": t.get("sources_incomplete", []),
        "failed": sorted(n for n, e in failures.items() if not e.startswith(NOT_CONFIGURED)),
        "needs_key": sorted(n for n, e in failures.items() if e.startswith(NOT_CONFIGURED)),
    }
    if sc.get("compared") and sc.get("agree"):
        return out | {"status": "verified"}
    if sc.get("compared"):
        return out | {
            "status": "discrepancy",
            "differences": {
                name: {side: len(v) for side, v in d.items()}
                for name, d in (sc.get("diffs") or {}).items()
            },
        }
    return out | {"status": "limited"}


def verification_text(v: dict) -> str:
    """Spanish detail of a verification status, for the Excel and Slack."""
    if v["status"] == "verified":
        return "Coinciden " + " y ".join(_source_label(n) for n in v["sources"]) + "."
    if v["status"] == "discrepancy":
        return "Dos fuentes concilian pero no listan los mismos movimientos."
    if v.get("not_reconciling"):
        return (
            "Una sola fuente: "
            + ", ".join(_source_label(n) for n in v["not_reconciling"])
            + " devolvió una lista incompleta."
        )
    if v["failed"]:
        return (
            "Una sola fuente: no respondió "
            + ", ".join(_source_label(n) for n in v["failed"])
            + "."
        )
    if v.get("needs_key"):
        hosts = ", ".join(_source_label(n) for n in v["needs_key"])
        return (
            f"Una sola fuente: la segunda ({hosts}) necesita una key gratuita que no está cargada."
        )
    return "Una sola fuente: no hay una segunda fuente gratuita para esta red."


def _status(pairs: list[dict], network_rows: list[dict]) -> dict:
    """Arithmetic and completeness kept apart. all_reconciled needs both."""
    reconciliation_passed = not any(r["status"] == "not_reconciled" for r in pairs) and all(
        r["passed"] and r["difference"] == "0" for r in network_rows
    )
    data_complete = not any(r["status"] in INCOMPLETE for r in pairs)
    ok = reconciliation_passed and data_complete
    checked = [r for r in pairs if r["status"] == "reconciled"]
    counts: dict[str, int] = {}
    for r in checked:
        counts[r["verification"]["status"]] = counts.get(r["verification"]["status"], 0) + 1
    return {
        "all_reconciled": ok,
        "reconciliation_passed": reconciliation_passed,
        "data_complete": data_complete,
        # Verified means verified against the chain by two paths. The second source is an
        # extra check: only a real discrepancy between two reconciling sources goes to a
        # person. A pair checked by one source only stays verified.
        "close_ready": ok,
        "source_check": {
            "counts": counts,
            "complete": ok and counts.get("verified", 0) == len(checked),
            "review": [
                {
                    "token": r["token"],
                    "chain": r["chain"],
                    "status": r["verification"]["status"],
                    "detail": verification_text(r["verification"]),
                }
                for r in checked
                if r["verification"]["status"] == "discrepancy"
            ],
        },
    }


def _scope_summary(manifest: dict, pairs: list[dict], chains: dict[str, Chain]) -> dict:
    counts: dict[str, int] = {}
    for r in pairs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    excluded = []
    for key in manifest["chains"]:
        mine = [r for r in pairs if r["chain"] == key]
        if mine and all(r["applicability"] == "not_applicable" for r in mine):
            excluded.append(
                {
                    "chain": key,
                    "name": chains[key].name if key in chains else key,
                    "reasons": sorted({r["reason"] for r in mine}),
                }
            )
    return {
        "blocks_source": manifest.get("blocks_source"),
        "expected": sum(r["applicability"] in APPLICABLE for r in pairs),
        "counts": counts,
        "excluded_networks": excluded,
        "pairs": pairs,
    }


def _networks(
    by_chain: dict[str, dict],
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    cutoff: str,
    prev: str,
    convention: str,
    pairs: list[dict],
) -> list[dict]:
    """Cutoff blocks per network, and the tokens whose contract was created after the
    previous cutoff (their opening supply is zero, with the creation block as evidence).
    Networks with no applicable pair are left out: the scope lists them as excluded."""
    networks = []
    for key in chains:
        c = by_chain.get(key)
        applicable = {
            r["token"] for r in pairs if r["chain"] == key and r["applicability"] in APPLICABLE
        }
        if c is None or not applicable:
            continue
        blk = {(b["cutoff"], b["convention"]): b for b in c.get("cutoff_blocks", [])}
        close_b, open_b = blk.get((cutoff, convention)), blk.get((prev, convention))
        new_tokens = {}
        for sym, t in c.get("tokens", {}).items():
            if sym not in applicable:
                continue
            conv = (t.get("conventions") or {}).get(convention) or {}
            if conv.get("opening_basis", "").startswith("contract created"):
                dep = tokens[sym].on(key)
                new_tokens[sym] = {
                    "creation_block": dep.creation_block,
                    "creation_tx": dep.creation_tx,
                    "creation_block_url": chains[key].block_url(dep.creation_block),
                    "creation_tx_url": chains[key].tx_url(dep.creation_tx)
                    if dep.creation_tx
                    else None,
                }
        networks.append(
            {
                "chain": key,
                "name": chains[key].name,
                "cutoff_block": close_b and close_b["block"],
                "cutoff_block_timestamp": close_b and close_b.get("block_timestamp"),
                "cutoff_block_url": close_b
                and close_b["block"]
                and chains[key].block_url(close_b["block"]),
                "previous_cutoff_block": open_b and open_b["block"],
                "is_new": bool(new_tokens) and len(new_tokens) == len(applicable),
                "new_tokens": new_tokens,
                "previous_cutoff_block_note": None
                if open_b and open_b["block"]
                else "la red no existía al corte anterior",
            }
        )
    return networks


def _token_rows(
    by_chain: dict[str, dict],
    chains: dict[str, Chain],
    tokens: dict[str, Token],
    convention: str,
    overrides: list[dict] | None,
    reconciled: set[tuple[str, str]],
    pairs: list[dict],
) -> tuple[dict, list[dict], list[dict]]:
    """Per token totals, per token and network reconciliation rows, and every movement.
    Only reconciled pairs of the scope count. A token with any applicable pair left out is
    marked partial: its totals are a subtotal, not the token's total."""
    token_rows, network_rows, movements = {}, [], []
    for sym, tok in tokens.items():
        d = tok.decimals
        agg = {
            "opening": 0,
            "closing": 0,
            "mints": 0,
            "burns": 0,
            "n_mints": 0,
            "n_burns": 0,
            "by_category": {k: {"amount": 0, "count": 0} for k in CATEGORY_ES},
        }
        nets_with_balance = []
        for key in chains:
            c = by_chain.get(key) or {}
            t = (c.get("tokens") or {}).get(sym)
            if (sym, key) not in reconciled or not t:
                continue
            conv = t["conventions"][convention]
            rec = conv["by_source"][t["source_used"]]
            agg["opening"] += int(rec["opening"])
            agg["closing"] += int(rec["closing"])
            agg["mints"] += int(rec["mints"])
            agg["burns"] += int(rec["burns"])
            agg["n_mints"] += rec["n_mints"]
            agg["n_burns"] += rec["n_burns"]
            if int(rec["closing"]) > 0:
                nets_with_balance.append(key)
            other = [n for n in t["sources_complete"] if n != t["source_used"]]
            network_rows.append(
                {
                    "token": sym,
                    "chain": key,
                    "opening_block": conv["opening_block"],
                    "opening_basis": conv["opening_basis"],
                    "closing_block": conv["closing_block"],
                    "opening": rec["opening"],
                    "mints": rec["mints"],
                    "burns": rec["burns"],
                    "n_mints": rec["n_mints"],
                    "n_burns": rec["n_burns"],
                    "expected": rec["opening_plus_mints_minus_burns"],
                    "closing": rec["closing"],
                    "difference": rec["difference"],
                    "passed": rec["passed"],
                    "source_used": t["source_used"],
                    "other_sources_complete": other,
                    "sources_incomplete": t["sources_incomplete"],
                    "closing_reads": conv.get("closing_reads", {}),
                }
            )
            for m in t.get("movements", []):
                fix = override_for(m, overrides or [], t["movements"])
                if fix:
                    m = m | fix
                cat = m.get("category", "unclassified")
                agg["by_category"][cat]["amount"] += int(m["amount"])
                agg["by_category"][cat]["count"] += 1
                movements.append(m)
        mine = [r for r in pairs if r["token"] == sym]
        expected = [r for r in mine if r["applicability"] in APPLICABLE]
        n_ok = sum(r["status"] == "reconciled" for r in expected)
        token_rows[sym] = {
            "name": tok.name,
            "decimals": d,
            "partial": any(r["status"] in INCOMPLETE for r in mine),
            "pairs_expected": len(expected),
            "pairs_reconciled": n_ok,
            "opening": str(units(agg["opening"], d)),
            "closing": str(units(agg["closing"], d)),
            "change": str(units(agg["closing"] - agg["opening"], d)),
            "mints": str(units(agg["mints"], d)),
            "burns": str(units(agg["burns"], d)),
            "n_mints": agg["n_mints"],
            "n_burns": agg["n_burns"],
            "opening_base_units": str(agg["opening"]),
            "closing_base_units": str(agg["closing"]),
            "by_category": {
                k: {"amount": str(units(v["amount"], d)), "count": v["count"]}
                for k, v in agg["by_category"].items()
            },
            "networks_with_balance": nets_with_balance,
            "closing_by_network": {
                r["chain"]: str(units(r["closing"], d)) for r in network_rows if r["token"] == sym
            },
        }
    return token_rows, network_rows, movements


def _cross_checks(network_rows: list[dict], coingecko: dict, certifications: list[dict]) -> dict:
    """The networks found against the ones CoinGecko lists and the latest certificates name."""
    latest_cert = max(certifications, key=lambda x: x["cutoff"])
    cert_nets = sorted(
        {
            k
            for x in certifications
            if x["cutoff"] == latest_cert["cutoff"]
            for k in x["network_keys"]
        }
    )
    found = sorted({r["chain"] for r in network_rows})
    cg = {sym: sorted(v["platforms"]) for sym, v in (coingecko.get("tokens") or {}).items()}
    cg_union = sorted({k for v in cg.values() for k in v})
    return {
        "coingecko_platforms": cg,
        "coingecko_not_found_here": sorted(set(cg_union) - set(found)),
        "found_not_in_coingecko": sorted(set(found) - set(cg_union)),
        "previous_certificates_networks": cert_nets,
        "found_not_in_previous_certificates": sorted(set(found) - set(cert_nets)),
    }


def _review_items(movements: list[dict], chains: dict[str, Chain]) -> list[dict]:
    out = []
    for m in movements:
        reason = None
        b = m.get("bridge") or {}
        if m.get("resolved_by_agent"):
            continue
        if m.get("category") == "unclassified":
            reason = (
                "Sin clasificar: no tiene evento del puente ni de LimitedMinter, y no es una "
                "llamada directa al token de una cuenta con permiso de emisión."
                if m["kind"] == "mint"
                else "Sin clasificar: no tiene evento del puente y quien quema no tiene "
                "permiso de emisión."
            )
        elif b.get("status") == "mismatch":
            reason = "Par del puente encontrado, pero no coinciden el monto o el destinatario."
        elif b.get("status") == "in_flight_at_cutoff":
            after = b.get("after") or {}
            dest = chains[b["dest_chain"]].name
            when = _dt(m.get("timestamp"))[:10]
            if after.get("status") == "not_found":
                reason = (
                    f"Salió por el puente el {when} hacia {dest} y no encontramos la emisión "
                    f"del otro lado (buscada hasta el bloque {after['searched_to']} de {dest})."
                )
            elif after.get("status") != "found":
                reason = (
                    f"Salió por el puente el {when} hacia {dest}. No pudimos buscar la emisión "
                    "del otro lado después del corte."
                )
        elif b.get("status") in (
            "source_unexplained",
            "source_not_checked",
            "dest_not_checked",
            "dest_bridge_unknown",
        ):
            reason = BRIDGE_ES[b["status"]] + "."
        if reason:
            out.append(
                {
                    "chain": m["chain"],
                    "token": m["token"],
                    "kind": m["kind"],
                    "amount": m["amount"],
                    "tx_hash": m["tx_hash"],
                    "explorer_url": m.get("explorer_url")
                    or chains[m["chain"]].tx_url(m["tx_hash"]),
                    "reason": reason,
                }
            )
    return out


# Ripio branding, Excel only. Purple sampled from the wordmark, tint is 10% purple on white.
PURPLE = "7808FE"
TINT = "F2E6FF"
LOGO = REPO_ROOT / "assets" / "branding" / "ripio_wordmark.png"
LOGO_HEIGHT_PX = 40

HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor=PURPLE)
TINT_FILL = PatternFill("solid", fgColor=TINT)
TITLE = Font(bold=True, size=16, color=PURPLE)
NUM = "#,##0.00"


def _header(ws, row: int, labels: list[str]) -> None:
    for i, label in enumerate(labels, 1):
        cell = ws.cell(row=row, column=i, value=label)
        cell.font, cell.fill = HEAD, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _link(cell, url: str | None, text: str) -> None:
    cell.value = text
    if url:
        cell.hyperlink = url
        cell.font = Font(color="1F4E99", underline="single")


def _logo(ws) -> None:
    """Wordmark in A1, row 1 sized to hold it."""
    img = Image(str(LOGO))
    img.width, img.height = round(img.width * LOGO_HEIGHT_PX / img.height), LOGO_HEIGHT_PX
    ws.add_image(img, "A1")
    ws.row_dimensions[1].height = LOGO_HEIGHT_PX * 0.75 + 4  # points


def _num(value: str) -> float:
    """Excel cells hold floats. The exact figures are in base units in the Conciliación sheet."""
    return float(Decimal(value))


def _write_row(ws, row: int, values: list) -> None:
    for col, value in enumerate(values, 1):
        cell = ws.cell(row=row, column=col, value=value)
        if isinstance(value, float):
            cell.number_format = NUM


def write_excel(pkg: dict, chains: dict[str, Chain], path) -> None:
    wb = Workbook()
    names = {k: c.name for k, c in chains.items()}
    summary = wb.active
    summary.title = "Resumen"
    _summary_sheet(summary, pkg, names)
    _networks_sheet(wb.create_sheet("Por red"), pkg, names)
    _movements_sheet(wb.create_sheet("Emisiones y quemas"), pkg, chains, names)
    _reconciliation_sheet(wb.create_sheet("Conciliación"), pkg, names)
    _methodology_sheet(wb.create_sheet("Metodología"), pkg)
    for sheet in wb.worksheets:
        sheet.sheet_properties.tabColor = PURPLE
    wb.save(path)


def _summary_sheet(ws, pkg: dict, names: dict[str, str]) -> None:
    cut, prev = fmt_date(pkg["cutoff"]), fmt_date(pkg["previous_cutoff"])
    _logo(ws)
    ws["A2"] = (
        f"Cierre trimestral wFIAT al {cut}"
        if pkg["period"]["kind"] == "quarter"
        else f"Cierre wFIAT del {prev} al {cut}"
    )
    ws["A2"].font = TITLE
    ws["A3"] = "Demo independiente para Ripio, por Máximo Sckell. No es una herramienta oficial."
    ws["A4"] = (
        f"Corte: {cut} 23:59:59 hora de Buenos Aires. Cierre anterior: {prev}. "
        f"Generado: {pkg['generated_at']} (UTC)."
    )
    ws["A5"] = (
        "Conciliación por red: todas las diferencias son cero."
        if pkg["all_reconciled"]
        else "Atención: el cierre está incompleto. Revisá la hoja Conciliación."
        if pkg["reconciliation_passed"]
        else "Atención: hay redes o tokens que no concilian. Revisá la hoja Conciliación."
    )
    ws["A5"].font = Font(bold=True)
    ws["A6"] = _scope_line(pkg["scope"])
    _header(
        ws,
        7,
        [
            "Token",
            f"Saldo al {prev}",
            "Emisiones",
            "Quemas",
            f"Saldo al {cut}",
            "Variación",
            "Emisión primaria",
            "Rescate primario",
            "Puente, entradas",
            "Puente, salidas",
            "Sin clasificar",
            "Redes con saldo",
        ],
    )
    r = 8
    for sym, t in pkg["tokens"].items():
        cat = t["by_category"]
        _write_row(
            ws,
            r,
            [
                f"{sym} (parcial)" if t["partial"] else sym,
                _num(t["opening"]),
                _num(t["mints"]),
                _num(t["burns"]),
                _num(t["closing"]),
                _num(t["change"]),
                _num(cat["primary"]["amount"]),
                _num(cat["redemption"]["amount"]),
                _num(cat["bridge_in"]["amount"]),
                _num(cat["bridge_out"]["amount"]),
                cat["unclassified"]["count"],
                ", ".join(names[k] for k in t["networks_with_balance"]),
            ],
        )
        r += 1
    r += 1
    for n in pkg["networks"]:
        if n["is_new"]:
            any_tok = next(iter(n["new_tokens"].values()))
            ws.cell(
                row=r, column=1, value=f"Red nueva desde el último cierre: {n['name']}"
            ).font = Font(bold=True)
            _link(
                ws.cell(row=r, column=5),
                any_tok["creation_block_url"],
                f"Bloque de creación {any_tok['creation_block']}",
            )
            r += 1
    ws.cell(
        row=r,
        column=1,
        value="Redes revisadas: " + ", ".join(pkg["networks_checked_names"]),
    )
    r += 1
    ws.cell(
        row=r,
        column=1,
        value=f"Para revisar: {len(pkg['review'])} movimientos (detalle en Emisiones y quemas).",
    )
    r += 1
    ws.cell(row=r, column=1, value=pkg["convention_check"]["note"])
    r += 1
    if pkg["all_reconciled"]:
        ws.cell(row=r, column=1, value=VERIFIED_LINE)
        r += 1
    ws.cell(row=r, column=1, value=source_check_line(pkg["source_check"], len(pkg["by_network"])))
    r += 2
    ws.cell(
        row=r,
        column=1,
        value=(
            "Usa solo datos públicos: las blockchains y las certificaciones publicadas. "
            "No calcula el respaldo bancario."
        ),
    )
    _widths(ws, [10, 18, 16, 16, 18, 16, 16, 16, 16, 16, 12, 60])


VERIFIED_LINE = (
    "Verificado contra la blockchain: en cada red, la cantidad de tokens al corte coincide "
    "exacto, al último decimal, con la apertura más las emisiones menos las quemas."
)


def source_check_line(check: dict, reconciled: int) -> str:
    """One Spanish line: the extra check, how many reconciled pairs a second source confirmed
    movement by movement. Only a real discrepancy asks for a person."""
    n_ok = check["counts"].get("verified", 0)
    line = (
        f"Control extra: una segunda fuente independiente confirmó cada movimiento en {n_ok} "
        f"de {reconciled} pares."
    )
    if n_ok < reconciled:
        line += " En el resto no hay una segunda fuente gratuita disponible y completa."
    disc = check["counts"].get("discrepancy", 0)
    if disc:
        line += (
            f" En {disc} {'par' if disc == 1 else 'pares'} dos fuentes no listan los mismos "
            "movimientos: revisalo en la hoja Conciliación."
        )
    return line


def _scope_line(scope: dict) -> str:
    n_ok = scope["counts"].get("reconciled", 0)
    line = f"Alcance: {n_ok} de {scope['expected']} pares token y red conciliados."
    n_na = scope["counts"].get("not_applicable", 0)
    if n_na:
        line += f" {n_na} pares no aplican a este período (ver hoja Conciliación)."
    return line


def _networks_sheet(ws, pkg: dict, names: dict[str, str]) -> None:
    cut, prev = fmt_date(pkg["cutoff"]), fmt_date(pkg["previous_cutoff"])
    _header(
        ws,
        1,
        [
            "Token",
            "Red",
            f"Bloque corte {prev}",
            f"Saldo al {prev}",
            "Emisiones",
            "Cant. emisiones",
            "Quemas",
            "Cant. quemas",
            f"Saldo al {cut}",
            f"Bloque corte {cut}",
            "Red nueva",
            "Fuente de movimientos",
        ],
    )
    newmap = {n["chain"]: n for n in pkg["networks"]}
    for i, row in enumerate(pkg["by_network"], 2):
        d = pkg["tokens"][row["token"]]["decimals"]
        n = newmap[row["chain"]]
        new_tok = n["new_tokens"].get(row["token"])
        _write_row(
            ws,
            i,
            [
                row["token"],
                names[row["chain"]],
                row["opening_block"] if not new_tok else f"creado en {new_tok['creation_block']}",
                float(units(row["opening"], d)),
                float(units(row["mints"], d)),
                row["n_mints"],
                float(units(row["burns"], d)),
                row["n_burns"],
                float(units(row["closing"], d)),
                row["closing_block"],
                "sí" if new_tok else "no",
                row["source_used"],
            ],
        )
        _link(ws.cell(row=i, column=10), n["cutoff_block_url"], str(row["closing_block"]))
        if new_tok:
            _link(
                ws.cell(row=i, column=3),
                new_tok["creation_block_url"],
                f"creado en {new_tok['creation_block']}",
            )
    _widths(ws, [8, 16, 18, 18, 16, 10, 16, 10, 18, 14, 9, 40])


def _movements_sheet(ws, pkg: dict, chains: dict[str, Chain], names: dict[str, str]) -> None:
    _header(
        ws,
        1,
        [
            "Fecha y hora (Buenos Aires)",
            "Red",
            "Token",
            "Tipo",
            "Clasificación",
            "Monto",
            "Monto en unidades base",
            "Bloque",
            "Transacción",
            "Cuenta",
            "Puente: estado",
            "Puente: otra punta",
            "Para revisar",
        ],
    )
    review_tx = {(x["chain"], x["tx_hash"]): x["reason"] for x in pkg["review"]}
    for i, m in enumerate(pkg["movements"], 2):
        d = pkg["tokens"][m["token"]]["decimals"]
        b = m.get("bridge") or {}
        other = b.get("other_side") or {}
        after = b.get("after") or {}
        ws.cell(row=i, column=1, value=_dt(m.get("timestamp")))
        ws.cell(row=i, column=2, value=names[m["chain"]])
        ws.cell(row=i, column=3, value=m["token"])
        ws.cell(row=i, column=4, value="Emisión" if m["kind"] == "mint" else "Quema")
        ws.cell(row=i, column=5, value=CATEGORY_ES[m.get("category", "unclassified")])
        c = ws.cell(row=i, column=6, value=float(units(m["amount"], d)))
        c.number_format = NUM
        ws.cell(row=i, column=7, value=m["amount"])
        ws.cell(row=i, column=8, value=m["block"])
        _link(ws.cell(row=i, column=9), m.get("explorer_url"), m["tx_hash"])
        ws.cell(row=i, column=10, value=m["counterparty"])
        status = BRIDGE_ES.get(b.get("status"), "") if b else ""
        ws.cell(row=i, column=11, value=status.replace("trimestre", pkg["period"]["word"]))
        if other:
            _link(
                ws.cell(row=i, column=12),
                chains[other["chain"]].tx_url(other["tx_hash"]),
                f"{names[other['chain']]} {other['tx_hash'][:12]}…",
            )
        elif after.get("status") == "found":
            _link(
                ws.cell(row=i, column=12),
                after["explorer_url"],
                f"llegó después del corte, {names[b['dest_chain']]} bloque {after['block']}",
            )
        elif b.get("source", {}).get("status") == "found":
            _link(
                ws.cell(row=i, column=12),
                b["source"]["explorer_url"],
                f"salió de {names[b['source_chain']]} en el bloque {b['source']['block']}",
            )
        reason = review_tx.get((m["chain"], m["tx_hash"]), "")
        agent = m.get("resolved_by_agent")
        if agent and not reason:
            reason = "Resuelto por el agente. Verificado: " + ". ".join(agent["checks"])
        ws.cell(row=i, column=13, value=reason)
        if reason and not agent:
            ws.cell(row=i, column=13).fill = TINT_FILL
    _widths(ws, [20, 14, 7, 8, 18, 16, 30, 11, 30, 44, 30, 40, 50])
    ws.freeze_panes = "A2"


def _reconciliation_sheet(ws, pkg: dict, names: dict[str, str]) -> None:
    _header(
        ws,
        1,
        [
            "Token",
            "Red",
            "Saldo de apertura (unidades base)",
            "+ Emisiones",
            "− Quemas",
            "= Esperado",
            "totalSupply al corte",
            "Diferencia",
            "Resultado",
            "Fuente que concilia",
            "Otras fuentes completas",
            "Fuentes incompletas",
            "Segunda fuente",
            "Detalle de la segunda fuente",
        ],
    )
    verif = {
        (x["token"], x["chain"]): x["verification"]
        for x in pkg["scope"]["pairs"]
        if x.get("verification")
    }
    for i, row in enumerate(pkg["by_network"], 2):
        _write_row(
            ws,
            i,
            [
                row["token"],
                names[row["chain"]],
                row["opening"],
                row["mints"],
                row["burns"],
                row["expected"],
                row["closing"],
                row["difference"],
                "conciliado" if row["passed"] else "NO concilia",
                row["source_used"],
                ", ".join(row["other_sources_complete"]),
                ", ".join(row["sources_incomplete"]),
                VERIFICATION_ES[verif[(row["token"], row["chain"])]["status"]],
                verification_text(verif[(row["token"], row["chain"])]),
            ],
        )
    r = len(pkg["by_network"]) + 3
    ws.cell(
        row=r,
        column=1,
        value=(
            "Montos en unidades base (18 decimales), exactos. El saldo de apertura sale de "
            "totalSupply() en el bloque del corte anterior, o es cero cuando el contrato se creó "
            "después de ese corte (ver hoja Por red)."
        ),
    )
    others = [x for x in pkg["scope"]["pairs"] if x["status"] != "reconciled"]
    if others:
        r += 2
        ws.cell(row=r, column=1, value="Pares sin conciliar o fuera del período").font = Font(
            bold=True
        )
        _header(ws, r + 1, ["Token", "Red", "Estado", "Motivo", "Evidencia"])
        for i, x in enumerate(others, r + 2):
            ev = x.get("evidence") or {}
            _write_row(
                ws,
                i,
                [
                    x["token"],
                    names.get(x["chain"], x["chain"]),
                    PAIR_ES[x["status"]],
                    REASON_ES.get(x["reason"], x["detail"] or x["reason"] or ""),
                    ", ".join(f"{k} {v}" for k, v in ev.items()),
                ],
            )
    _widths(ws, [8, 14, 32, 30, 30, 32, 32, 11, 13, 40, 30, 30, 24, 60])


def _methodology_sheet(ws, pkg: dict) -> None:
    _header(ws, 1, ["Decisión", "Etiqueta", "Fuente o evidencia"])
    for i, m in enumerate(pkg["methodology"], 2):
        ws.cell(row=i, column=1, value=m["decision"]).alignment = Alignment(wrap_text=True)
        ws.cell(row=i, column=2, value=LABEL_ES[m["label"]])
        ws.cell(row=i, column=3, value=m["source"]).alignment = Alignment(wrap_text=True)
    _widths(ws, [70, 14, 70])
