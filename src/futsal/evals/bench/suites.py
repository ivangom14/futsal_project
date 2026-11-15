"""Suites del benchmark desde el CSV de revisión humana (`evals/bench/review/<suite>-revision.csv`).

Se incluye cada caso cuya primera fila tiene `incluir` = si/sí/x/1 (las filas siguientes del mismo caso son sus turnos). Las
respuestas esperadas NO están en el CSV: salen de la verdad SQL de cada caso de `evals/datasets/futsito-v1`. Una fila con pregunta y
`incluir` = si pero sin `id` es una pregunta nueva: se guarda aparte para definir su caso (conducta y hechos esperados) antes de usarla.
"""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from futsal.evals.datasets import BENCH_SUITES_DIR, load_cases

YES = {"si", "sí", "x", "1", "yes", "s"}
STRATUM_CATEGORY = {"S1": "clasificacion", "S2": "equipos_ambiguos", "S3": "encadenadas", "S4": "resultados", "S5": "sin_datos",
                    "S6": "adversariales", "S7": "competiciones_grupos", "S8": "estadisticas", "S9": "reglamento", "S10": "competiciones_grupos"}


def read_review(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """(ids incluidos en orden, preguntas nuevas). Admite `;` o `,` como separador y el BOM de Excel."""
    text = path.read_text(encoding="utf-8-sig")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    if not rows or "incluir" not in rows[0] or "id" not in rows[0]:
        raise ValueError(f"{path}: faltan las columnas «incluir» e «id» (usa el CSV de revisión como plantilla)")
    ids: list[str] = []
    new: list[dict[str, str]] = []
    for r in rows:
        keep = (r.get("incluir") or "").strip().lower() in YES
        cid = (r.get("id") or "").strip()
        if keep and cid and cid not in ids:
            ids.append(cid)
        elif keep and not cid and (r.get("pregunta") or "").strip():
            new.append({k: (v or "").strip() for k, v in r.items() if k})
    return ids, new


def import_suite(csv_path: Path, name: str, version: str | None = None, root: Path = Path(".")) -> dict[str, Any]:
    ids, new = read_review(csv_path)
    if not ids:
        raise ValueError("ningún caso con incluir = si")
    dev = {c.id: c for c in load_cases(root, "dev")}
    unknown = [i for i in ids if i not in dev]
    if unknown:
        raise ValueError(f"ids que no existen en evals/datasets/futsito-v1 (dev): {unknown[:10]}")
    path = root / BENCH_SUITES_DIR / f"{name}.json"
    old: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    known = {c["id"]: c for c in old.get("cases", [])}
    if version is None:
        major, _, minor = str(old.get("version", "0.9")).partition(".")
        version = f"{major}.{int(minor or 0) + 1}" if old else "1.0"
    cases = []
    for cid in ids:
        c = dev[cid]
        base = known.get(cid, {})
        cases.append({"id": cid, "category": base.get("category") or ("encadenadas" if len(c.turns) > 1 else STRATUM_CATEGORY[c.stratum]),
                      "complexity": "simple" if c.stratum == "S1" and len(c.turns) == 1 else "complex", "stratum": c.stratum,
                      "turns": len(c.turns)})
    suite = {**{k: v for k, v in old.items() if k not in ("cases", "version")}, "name": name, "version": version,
             "source_dataset": "evals/datasets/futsito-v1 (dev)", "reviewed_from": str(csv_path),
             "categories": list(dict.fromkeys(c["category"] for c in cases)), "cases": cases}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    new_file = None
    if new:
        new_file = csv_path.with_name(f"{name}-nuevas.json")
        new_file.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"name": name, "version": version, "path": str(path), "cases": len(cases), "turns": sum(c["turns"] for c in cases),
            "by_category": dict(Counter(c["category"] for c in cases)), "new_questions": new, "new_file": str(new_file) if new_file else None}
