"""Closing package: package.json for the site and an Excel workbook for the accountant.

Every figure comes from the engine output in base units. Units are derived with Decimal
and rounded only when shown. Labels are in Spanish, as Finance reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from cierre import REPO_ROOT
from cierre.config import Chain, Token

ART = ZoneInfo("America/Argentina/Buenos_Aires")

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


def units(raw: int | str, decimals: int) -> Decimal:
    return Decimal(int(raw)) / (Decimal(10) ** decimals)


def _iso_art(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).astimezone(ART).isoformat()


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
    overrides: dict[tuple[str, str, int], dict] | None = None,
) -> dict:
    """overrides: movements the exception agent proved, keyed by (chain, tx, log_index)."""
    by_chain = {c["chain"]: c for c in engine}
    first = next(c for c in engine if "previous_cutoff" in c)
    cutoff, prev = first["cutoff"], first["previous_cutoff"]
    engine_errors = [
        {"chain": c["chain"], "error": c["error"]} for c in engine if c.get("error")
    ] + [
        {"chain": c["chain"], "token": sym, "error": t.get("error", "no source reconciles")}
        for c in engine
        for sym, t in c.get("tokens", {}).items()
        if not t.get("passed")
    ]

    networks = []
    for key in chains:
        c = by_chain.get(key)
        if c is None:
            continue
        blk = {(b["cutoff"], b["convention"]): b for b in c.get("cutoff_blocks", [])}
        close_b, open_b = blk.get((cutoff, convention)), blk.get((prev, convention))
        new_tokens = {}
        for sym, t in c.get("tokens", {}).items():
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
                "is_new": bool(new_tokens) and len(new_tokens) == len(c.get("tokens", {})),
                "new_tokens": new_tokens,
                "previous_cutoff_block_note": None
                if open_b and open_b["block"]
                else "la red no existía al corte anterior",
            }
        )

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
            if not t or not t.get("passed"):
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
                fix = (overrides or {}).get((m["chain"], m["tx_hash"], m["log_index"]))
                if fix:
                    m = m | fix
                cat = m.get("category", "unclassified")
                agg["by_category"][cat]["amount"] += int(m["amount"])
                agg["by_category"][cat]["count"] += 1
                movements.append(m)
        token_rows[sym] = {
            "name": tok.name,
            "decimals": d,
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

    movements.sort(key=lambda m: (m["timestamp"], m["chain"], m["log_index"]))
    review = _review_items(movements, chains)
    between = [
        m
        for c in engine
        for t in c.get("tokens", {}).values()
        for m in t.get("movements_between_conventions", [])
    ]
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
        "cutoff": cutoff,
        "previous_cutoff": prev,
        "convention": convention,
        "cutoff_instant": f"{cutoff} 23:59:59 America/Argentina/Buenos_Aires",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "all_reconciled": not engine_errors
        and all(r["passed"] and r["difference"] == "0" for r in network_rows),
        "engine_errors": engine_errors,
        "networks": networks,
        "networks_checked": discovery["networks_checked"],
        "networks_checked_names": [
            (all_chains or chains)[k].name for k in discovery["networks_checked"]
        ],
        "new_networks": [n["chain"] for n in networks if n["is_new"]],
        "tokens": token_rows,
        "by_network": network_rows,
        "movements": movements,
        "review": review,
        "bridge": bridge_summary,
        "convention_check": {
            "movements_between_art_and_utc": len(between),
            "note": "La convención horaria no cambia este cierre."
            if not between
            else "Hay movimientos entre las 23:59:59 UTC y las 23:59:59 de Buenos Aires.",
        },
        "cross_checks": {
            "coingecko_platforms": cg,
            "coingecko_not_found_here": sorted(set(cg_union) - set(found)),
            "found_not_in_coingecko": sorted(set(found) - set(cg_union)),
            "previous_certificates_networks": cert_nets,
            "found_not_in_previous_certificates": sorted(set(found) - set(cert_nets)),
        },
        "methodology": methodology,
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
                "Sin clasificar: no tiene evento del puente ni de LimitedMinter."
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


# ---------------------------------------------------------------------------- Excel


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


def _dt(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, UTC).astimezone(ART).strftime("%d/%m/%Y %H:%M:%S")


def _d(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def write_excel(pkg: dict, chains: dict[str, Chain], path) -> None:
    wb = Workbook()
    names = {k: c.name for k, c in chains.items()}
    cut, prev = _d(pkg["cutoff"]), _d(pkg["previous_cutoff"])

    # Resumen
    ws = wb.active
    ws.title = "Resumen"
    _logo(ws)
    ws["A2"] = f"Cierre trimestral wFIAT al {cut}"
    ws["A2"].font = TITLE
    ws["A3"] = "Demo independiente para Ripio, por Máximo Sckell. No es una herramienta oficial."
    ws["A4"] = (
        f"Corte: {cut} 23:59:59 hora de Buenos Aires. Cierre anterior: {prev}. "
        f"Generado: {pkg['generated_at']} (UTC)."
    )
    ws["A5"] = (
        "Conciliación por red: todas las diferencias son cero."
        if pkg["all_reconciled"]
        else "Atención: hay redes o tokens que no concilian. Revisá la hoja Conciliación."
    )
    ws["A5"].font = Font(bold=True)
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
        vals = [
            sym,
            float(Decimal(t["opening"])),
            float(Decimal(t["mints"])),
            float(Decimal(t["burns"])),
            float(Decimal(t["closing"])),
            float(Decimal(t["change"])),
            float(Decimal(cat["primary"]["amount"])),
            float(Decimal(cat["redemption"]["amount"])),
            float(Decimal(cat["bridge_in"]["amount"])),
            float(Decimal(cat["bridge_out"]["amount"])),
            cat["unclassified"]["count"],
            ", ".join(names[k] for k in t["networks_with_balance"]),
        ]
        for i, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=i, value=v)
            if isinstance(v, float):
                cell.number_format = NUM
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

    # Por red
    ws = wb.create_sheet("Por red")
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
        vals = [
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
        ]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=j, value=v)
            if isinstance(v, float):
                cell.number_format = NUM
        _link(ws.cell(row=i, column=10), n["cutoff_block_url"], str(row["closing_block"]))
        if new_tok:
            _link(
                ws.cell(row=i, column=3),
                new_tok["creation_block_url"],
                f"creado en {new_tok['creation_block']}",
            )
    _widths(ws, [8, 16, 18, 18, 16, 10, 16, 10, 18, 14, 9, 40])

    # Emisiones y quemas
    ws = wb.create_sheet("Emisiones y quemas")
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
        ws.cell(row=i, column=11, value=BRIDGE_ES.get(b.get("status"), "") if b else "")
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

    # Conciliación
    ws = wb.create_sheet("Conciliación")
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
        ],
    )
    for i, row in enumerate(pkg["by_network"], 2):
        vals = [
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
        ]
        for j, v in enumerate(vals, 1):
            ws.cell(row=i, column=j, value=v)
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
    _widths(ws, [8, 14, 32, 30, 30, 32, 32, 11, 13, 40, 30, 30])

    # Metodología
    ws = wb.create_sheet("Metodología")
    _header(ws, 1, ["Decisión", "Etiqueta", "Fuente o evidencia"])
    for i, m in enumerate(pkg["methodology"], 2):
        ws.cell(row=i, column=1, value=m["decision"]).alignment = Alignment(wrap_text=True)
        ws.cell(row=i, column=2, value=LABEL_ES[m["label"]])
        ws.cell(row=i, column=3, value=m["source"]).alignment = Alignment(wrap_text=True)
    _widths(ws, [70, 14, 70])

    for sheet in wb.worksheets:
        sheet.sheet_properties.tabColor = PURPLE
    wb.save(path)
