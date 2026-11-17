"""Hoja Excel de revisión humana de una ejecución: una fila por turno con la pregunta real (ya con el equipo, jugador… de la BD), la
respuesta EXACTA del modelo, lo que se esperaba, el veredicto del calificador automático y dos columnas vacías para el revisor
(`humano`, con desplegable bien/parcial/mal, y `comentario`). Una pestaña por modelo; no llama al LLM ni a la BD."""

from pathlib import Path
from typing import Any

from futsal.evals.bench.runner import load_run
from futsal.evals.runner import expected_for
from futsal.evals.selector import ResolvedTurn

AUTO = {"correct": "bien", "partial": "parcial (falta un dato)", "fail": "mal", "critical": "mal grave (inventa o contradice)",
        "review": "revisar", "not_evaluable": "no evaluable", "not_run": "no ejecutado"}
FILL = {"correct": "C6EFCE", "partial": "FFEB9C", "fail": "FFC7CE", "critical": "FF8F8F"}
BEHAVIOR = {"answer": "responder con los datos", "clarify": "pedir aclaración", "abstain": "decir que no consta (no inventar)",
            "offtopic": "rechazarlo (fuera de ámbito)"}
HUMAN = ("bien", "parcial", "mal")
COLUMNS = [("caso", 26), ("categoría", 18), ("turno", 7), ("rep", 5), ("pregunta", 45), ("respuesta", 80), ("se esperaba", 45),
           ("automático", 16), ("motivo automático", 40), ("herramientas", 24), ("humano", 10), ("comentario", 40)]


def _expected(exp: dict[str, Any]) -> str:
    try:
        turn = expected_for(ResolvedTurn(**exp))
    except TypeError:  # resultado sin la expectativa completa (caso no evaluable)
        return ""
    parts = [BEHAVIOR.get(turn.behavior, turn.behavior)]
    parts += [f"{f.get('label') or f.get('kind')} = {f.get('value')}" for f in turn.facts]
    parts += [f"NO: {f.get('label') or f.get('kind')} = {f.get('value')}" for f in turn.forbid_facts]
    if turn.clarify_mentions:
        parts.append("opciones: " + ", ".join(turn.clarify_mentions))
    if turn.forbid_any_score:
        parts.append("sin marcadores")
    return "\n".join(parts)


def review_rows(meta: dict[str, Any], results: dict[str, Any]) -> list[list[Any]]:
    order = {cid: i for i, cid in enumerate(meta.get("case_ids") or [])}
    cats = meta.get("cases") or {}
    rows = []
    for r in sorted(results["records"], key=lambda r: (order.get(r["case_id"], 10**6), r["rep"], r["turn"])):
        exps = (results.get("cases", {}).get(r["case_id"]) or {}).get("expected") or []
        exp = exps[r["turn"]] if r["turn"] < len(exps) else {}
        n = len(exps) or 1
        rows.append([r["case_id"], (cats.get(r["case_id"]) or {}).get("category", ""), f"{r['turn'] + 1}/{n}", r["rep"] + 1,
                     r["question"], r.get("answer") or "", _expected(exp), AUTO.get(r["verdict"], r["verdict"]),
                     r.get("reason") or "", ", ".join(r.get("tools_used") or []), "", ""])
    return rows


def export_review(run_dir: Path, output: Path | None = None) -> dict[str, Any]:
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
        from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
        from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError("falta openpyxl: pip install -e '.[bench]'") from exc
    meta, by_model = load_run(run_dir)
    if not by_model:
        raise ValueError(f"{run_dir} no tiene resultados de ningún modelo")
    wb = Workbook()
    wb.remove(wb.active)
    counts: dict[str, int] = {}
    inv = {v: k for k, v in AUTO.items()}
    for mid, results in by_model.items():
        ws = wb.create_sheet(mid[:31])
        ws.append([c for c, _ in COLUMNS])
        for i, (_, width) in enumerate(COLUMNS, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = width
            ws.cell(1, i).font = Font(bold=True)
        rows = review_rows(meta, results)
        for row in rows:
            ws.append(row)
        wrap = Alignment(wrap_text=True, vertical="top")
        for line in ws.iter_rows(min_row=2):
            for cell in line:
                cell.alignment = wrap
            fill = FILL.get(inv.get(line[7].value, ""))
            if fill:
                line[7].fill = PatternFill("solid", start_color=fill)
        dv = DataValidation(type="list", formula1='"' + ",".join(HUMAN) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        if rows:
            dv.add(f"K2:K{len(rows) + 1}")
        ws.freeze_panes = "F2"
        ws.auto_filter.ref = ws.dimensions
        counts[mid] = len(rows)
    out = output or run_dir / "respuestas.xlsx"
    wb.save(out)
    return {"path": str(out), "rows": counts}
