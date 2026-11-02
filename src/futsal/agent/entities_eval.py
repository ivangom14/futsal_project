"""Evaluación OFFLINE del extractor de equipos (Fase 3A). Sin LLM, sin red, sin base de datos.

Separa tres cosas que no deben confundirse:
- PRECISIÓN de las activaciones: de las veces que el extractor dice «hay un equipo», cuántas acierta (con intervalo exacto del 95 %).
- TASA DE FALSOS POSITIVOS: de las preguntas donde NO debía activarse (sin equipo, homónimos, varios equipos, reglamento…), cuántas activó (intervalo del 95 %).
- COBERTURA: de las preguntas donde sí debía activarse, cuántas activó bien. Es solo ahorro potencial; no afecta a la seguridad.
El objetivo de seguridad (falsos positivos ≤ 1 %) solo se da por cumplido si el LÍMITE SUPERIOR del intervalo lo respalda con ese tamaño de muestra. Si no, el
veredicto es «no concluyente» y el prefetch sigue desactivado.

Uso: `python -m futsal.agent.entities_eval --set dev|a|b [--errors] [--json salida.json]` (b exige --use-holdout-b-final y queda anotado).
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from futsal.agent import datasets
from futsal.agent.entities import TeamCatalog, decide
from futsal.agent.router import resolve_scope
from futsal.agent.stats import cp_interval, min_n_for_upper_bound, verdict_upper
from futsal.config.competition_aliases import load_aliases
from futsal.domain.competitions import Competition

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
CATALOG = FIXTURES / "entities" / "team_catalog.json"
SETS = {"dev": FIXTURES / "entities" / "entities_dev.json", "a": FIXTURES / "router" / "holdout_a.json"}
TARGET_FALSE_POSITIVE = 0.01


def competitions(catalog: TeamCatalog) -> list[Competition]:
    groups: dict[str, dict[str, str]] = defaultdict(dict)
    for e in catalog.entries:
        groups[e.competition][e.group_ref] = e.group
    return [Competition(i, name, tuple((j, gn) for j, (_, gn) in enumerate(sorted(g.items()), 1))) for i, (name, g) in enumerate(sorted(groups.items()), 1)]


def run_cases(doc: dict[str, Any], catalog: TeamCatalog) -> list[dict[str, Any]]:
    comps, aliases, rows = competitions(catalog), load_aliases(), []
    for c in doc["cases"]:
        hints, _, _ = resolve_scope(comps, aliases, c.get("prev_questions") or [], c["question"])
        d = decide(c["question"], catalog, hints, aliases, has_context=bool(c.get("prev_questions") or c.get("focus")))
        gold_fire = c["prefetch_expected"] == "fire"
        rows.append({"id": c["id"], "category": c["category"], "form": c.get("form"), "gold": c["prefetch_entity"] if gold_fire else None,
                     "gold_abstain_reason": c["abstain_reason"], "fired": d.fired, "team": d.team, "reason": d.reason, "question": c["question"],
                     "correct": d.fired and gold_fire and d.team == c["prefetch_entity"], "wrong_team": d.fired and gold_fire and d.team != c["prefetch_entity"],
                     "false_positive": d.fired and not gold_fire, "missed": gold_fire and not d.fired})
    return rows


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if r["gold"]]
    neg = [r for r in rows if not r["gold"]]
    fires = [r for r in rows if r["fired"]]
    tp = sum(r["correct"] for r in rows)
    wrong = sum(r["wrong_team"] for r in rows)
    fp = sum(r["false_positive"] for r in neg)

    def rate(k: int, n: int) -> dict[str, Any]:
        lo, hi = cp_interval(k, n)
        return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": [lo, hi]}

    by_reason: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "false_positives": 0})
    for r in neg:
        by_reason[r["gold_abstain_reason"] or "?"]["n"] += 1
        by_reason[r["gold_abstain_reason"] or "?"]["false_positives"] += r["false_positive"]
    by_form: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "correct": 0, "missed": 0})
    for r in pos:
        by_form[r["form"] or "?"]["n"] += 1
        by_form[r["form"] or "?"]["correct"] += r["correct"]
        by_form[r["form"] or "?"]["missed"] += r["missed"]
    wrong_all = wrong + fp
    return {
        "cases": len(rows), "positives": len(pos), "negatives": len(neg), "fires": len(fires),
        "precision": rate(tp, len(fires)),  # activaciones correctas / activaciones
        "false_positive_rate": rate(fp, len(neg)),  # activaciones donde NO debía activarse / preguntas donde no debía
        "wrong_team_rate_among_fires": rate(wrong_all, len(fires)),  # activaciones que apuntan a un equipo que no es / activaciones
        "coverage": rate(tp, len(pos)),  # solo ahorro potencial
        "verdict_false_positive_le_1pct": verdict_upper(fp, len(neg), TARGET_FALSE_POSITIVE),
        "verdict_wrong_team_le_1pct": verdict_upper(wrong_all, len(fires), TARGET_FALSE_POSITIVE),
        "negatives_needed_for_1pct": {"with_0_fp": min_n_for_upper_bound(TARGET_FALSE_POSITIVE, 0), "with_1_fp": min_n_for_upper_bound(TARGET_FALSE_POSITIVE, 1),
                                      "with_2_fp": min_n_for_upper_bound(TARGET_FALSE_POSITIVE, 2)},
        "fires_needed_for_1pct_wrong": min_n_for_upper_bound(TARGET_FALSE_POSITIVE, 0),
        "false_positives_by_reason": dict(by_reason), "positives_by_form": dict(by_form),
        "prefetch_ready": fp == 0 and wrong_all == 0 and verdict_upper(fp, len(neg), TARGET_FALSE_POSITIVE) == "cumple"
        and verdict_upper(wrong_all, len(fires), TARGET_FALSE_POSITIVE) == "cumple",
        "abstain_reasons": dict(Counter(r["reason"] for r in rows if not r["fired"]))}


def render(name: str, m: dict[str, Any], rows: list[dict[str, Any]], errors: bool) -> str:
    def f(x: dict[str, Any]) -> str:
        return "n/d" if x["rate"] is None else f"{x['rate'] * 100:.2f}% ({x['k']}/{x['n']}; IC95 {x['ci95'][0] * 100:.2f}–{x['ci95'][1] * 100:.2f}%)"
    lines = [f"== {name}: {m['cases']} preguntas ({m['positives']} con equipo inequívoco, {m['negatives']} donde NO debe activarse) ==",
             f"Precisión de las activaciones: {f(m['precision'])}", f"Tasa de falsos positivos (sobre negativos): {f(m['false_positive_rate'])}",
             f"Activaciones con equipo equivocado o sin equipo: {f(m['wrong_team_rate_among_fires'])}", f"Cobertura (solo ahorro potencial): {f(m['coverage'])}",
             f"Objetivo falsos positivos ≤ 1 %: {m['verdict_false_positive_le_1pct'].upper()} — hacen falta ≥ {m['negatives_needed_for_1pct']['with_0_fp']} negativos con 0 fallos "
             f"({m['negatives_needed_for_1pct']['with_1_fp']} con 1; {m['negatives_needed_for_1pct']['with_2_fp']} con 2); hay {m['negatives']}",
             f"Objetivo activaciones erróneas ≤ 1 %: {m['verdict_wrong_team_le_1pct'].upper()} — hacen falta ≥ {m['fires_needed_for_1pct_wrong']} activaciones sin error; hay {m['fires']}",
             f"Criterios estadísticos de seguridad cumplidos CON ESTE DATASET: {'SÍ' if m['prefetch_ready'] else 'NO (el prefetch sigue desactivado)'}"
             + (" — OJO: el dataset es sintético y lo redactó el autor del extractor; no sustituye a preguntas reales ni al Holdout B, y activar el prefetch sigue siendo una decisión aparte" if m["prefetch_ready"] else ""),
             "Falsos positivos por motivo (n | FP): " + ", ".join(f"{k} {v['n']}|{v['false_positives']}" for k, v in sorted(m["false_positives_by_reason"].items())),
             "Positivos por forma de mención (n | acertados | no detectados): " + ", ".join(f"{k} {v['n']}|{v['correct']}|{v['missed']}" for k, v in sorted(m["positives_by_form"].items()))]
    if errors:
        lines.append("Fallos:")
        for r in rows:
            if r["false_positive"] or r["wrong_team"]:
                lines.append(f"  [ACTIVACIÓN ERRÓNEA] {r['id']}: {r['question'][:90]} → {r['team']} ({r['reason']}; esperado: {r['gold'] or 'abstenerse: ' + str(r['gold_abstain_reason'])})")
        for r in [r for r in rows if r["missed"]][:25]:
            lines.append(f"  [no detectado] {r['id']} ({r['form']}): {r['question'][:90]} → {r['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa el extractor de equipos del prefetch (offline).")
    ap.add_argument("--set", choices=("dev", "a", "b"), default="dev")
    ap.add_argument("--errors", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--use-holdout-b-final", action="store_true", help="solo para la evaluación FINAL: queda anotado en data/holdout/b_access.log")
    args = ap.parse_args(argv)
    catalog = TeamCatalog.from_json(CATALOG)
    if args.set == "b":
        doc = datasets.load_holdout_b(acknowledge=args.use_holdout_b_final, purpose="entities_eval final")
    else:
        doc = datasets.load(SETS[args.set])
    rows = run_cases(doc, catalog)
    m = metrics(rows)
    print(render(args.set, m, rows, args.errors))
    if args.json:
        Path(args.json).write_text(json.dumps({"set": args.set, "metrics": m, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
