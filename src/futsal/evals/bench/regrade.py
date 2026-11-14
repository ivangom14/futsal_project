"""Recalificación de una ejecución del benchmark con el calificador ACTUAL, sin llamar a ningún LLM.

Usa lo que guarda cada turno (respuesta, herramientas usadas y evidencia devuelta por ellas) y la expectativa congelada en
`results.json`. Necesita la BD de la ejecución (el snapshot o, si fue `--live`, la BD actual) solo para el catálogo de nombres con el
que se detectan nombres inventados. Nunca sobrescribe: escribe `<run>-regraded/` con los mismos ficheros y un `verdict-diff.json`.
Los turnos de ejecuciones anteriores a que se guardara la evidencia no se pueden recalificar: se conservan y se cuentan aparte.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from futsal.evals import report as reporting
from futsal.evals.bench.runner import BENCH_FILE, load_run
from futsal.evals.catalog import Catalog
from futsal.evals.grading import grade_turn
from futsal.evals.runner import (
    LIVE,
    RESULTS,
    RunConfig,
    live_manifest,
    prepare_database,
    write_outputs,
)
from futsal.evals.selector import ResolvedTurn

GRADED = ("correct", "partial", "fail", "critical", "review")


def regrade_results(results: dict[str, Any], players: list[str], names: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """(resultados nuevos, cambios de veredicto, turnos sin evidencia)."""
    new = json.loads(json.dumps(results))
    diff: list[dict[str, Any]] = []
    missing = 0
    for r in new["records"]:
        if r["verdict"] not in GRADED:
            continue
        if "evidence" not in r:
            missing += 1
            continue
        expected = new["cases"][r["case_id"]]["expected"][r["turn"]]
        turn = ResolvedTurn(**expected)
        g = grade_turn(turn, r["answer"], r["tools_used"], r["evidence"], r["metrics"]["llm_calls"], players, names)
        if g.verdict != r["verdict"]:
            diff.append({"case": r["case_id"], "turn": r["turn"] + 1, "rep": r["rep"] + 1, "before": r["verdict"], "after": g.verdict,
                         "reason_before": str(r["reason"])[:300], "reason_after": "; ".join(g.reasons)[:300]})
        r.update(verdict=g.verdict, reason="; ".join(g.reasons), error_class=g.error_class, grade=g.to_dict())
    new["summary"] = reporting.summarize(new)
    new["meta"]["regraded"] = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "changed": len(diff),
                               "without_evidence": missing, "llm_calls": 0}
    return new, diff, missing


def regrade_run(run_dir: Path, db_url: str | None, admin_url: str | None, root: Path = Path("."),
                output: Path | None = None) -> dict[str, Any]:
    meta, results = load_run(run_dir)
    if not results:
        raise ValueError(f"{run_dir} no tiene resultados de ningún modelo")
    out = output or run_dir.with_name(run_dir.name + "-regraded")
    if out.exists():
        raise ValueError(f"{out} ya existe: regrade nunca sobrescribe")
    snap = meta["snapshot"]
    live = snap.get("live") or snap["id"] == LIVE
    url = db_url or admin_url
    live_m = live_manifest(str(url), snap["as_of"]) if live and url else None
    url, manifest = prepare_database(RunConfig(suite=meta["suite"]["name"], snapshot=LIVE if live else snap["id"], output=out, root=root,
                                               db_url=db_url, admin_url=admin_url, live_manifest=live_m))
    engine = create_engine(url)
    try:
        cat = Catalog.load(engine, seed=manifest["fingerprint"]["sha256"], as_of=datetime.fromisoformat(manifest["as_of"]))
        players, names = cat.player_names, cat.known_names()
    finally:
        engine.dispose()
    out.mkdir(parents=True)
    summary: dict[str, Any] = {}
    for mid, res in results.items():
        new, diff, missing = regrade_results(res, players, names)
        write_outputs(out / mid, new)
        (out / mid / "verdict-diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        g0, g1 = res["summary"]["global"], new["summary"]["global"]
        summary[mid] = {"changed": len(diff), "without_evidence": missing,
                        "accuracy": [g0["accuracy"]["k"], g1["accuracy"]["k"], g1["accuracy"]["n"]],
                        "critical": [g0["critical"]["k"], g1["critical"]["k"]]}
    new_meta = {**meta, "run_id": out.name, "regraded_from": meta["run_id"]}
    (out / BENCH_FILE).write_text(json.dumps(new_meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    blind = run_dir / "blind"
    if blind.exists():  # la hoja del juez se conserva (no depende del calificador)
        shutil.copytree(blind, out / "blind")
    return {"output": str(out), "models": summary}


__all__ = ["RESULTS", "regrade_results", "regrade_run"]
