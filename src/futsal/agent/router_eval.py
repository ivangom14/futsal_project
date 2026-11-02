"""Evaluación OFFLINE del router en sombra (Fase 0): sin LLM, sin red, sin base de datos.

Dos conjuntos que NUNCA se mezclan en las métricas:
- `design_set.json`: el dataset previo (72 preguntas del evaluador de la Fase 17). El router se diseñó mirándolo: no es evidencia independiente.
- `validation.json`: el dataset nuevo, escrito después de diseñar el router y sin usarlo para ajustarlo.

Uso: `python -m futsal.agent.router_eval [--set design|validation|both] [--errors] [--json salida.json]`
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from futsal.agent.router import RouterDecision, resolve_scope, route
from futsal.config.competition_aliases import load_aliases
from futsal.domain.competitions import Competition

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "router"
MIN_HIGH_CONFIDENCE_PRECISION = 0.95  # criterio de aceptación del diseño (no se fuerza: se informa)
MIN_TIER0_COVERAGE = 0.40


def load_catalog(name: str, path: Path | None = None) -> list[Competition]:
    data = json.loads((path or FIXTURES / "catalog.json").read_text(encoding="utf-8"))[name]
    return [Competition(i + 1, comp, tuple((100 * (i + 1) + j, g) for j, g in enumerate(groups, 1))) for i, (comp, groups) in enumerate(data.items())]


def load_cases(name: str, base: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(((base or FIXTURES) / f"{name}.json").read_text(encoding="utf-8"))
    return data


def decide(case: dict[str, Any], catalog: str, aliases: dict[str, Any], base: Path | None = None) -> RouterDecision:
    """La misma entrada que recibe el router dentro del agente: ámbito resuelto con el catálogo, foco verificado e historial."""
    comps = load_catalog(case.get("catalog") or catalog, None if base is None else base / "catalog.json")
    hints, scope_ok, _ = resolve_scope(comps, aliases, case.get("prev_questions") or [], case["question"])
    return route(case["question"], competitions=comps, hints=hints, scope_ok=scope_ok,
                 groups_total=sum(len(c.groups) for c in comps), focus=case.get("focus") or {},
                 has_history=bool(case.get("prev_questions")))


def evaluate_set(name: str, base: Path | None = None) -> dict[str, Any]:
    doc = load_cases(name, base)
    aliases = load_aliases()
    rows: list[dict[str, Any]] = []
    for case in doc["cases"]:
        if not case.get("evaluable", True):
            rows.append({"id": case["id"], "category": case["category"], "evaluable": False, "question": case["question"],
                         "note": case.get("note") or case.get("reason_not_evaluable", "")})
            continue
        d = decide(case, doc.get("catalog", "full"), aliases, base)
        rows.append({"id": case["id"], "category": case["category"], "evaluable": True, "question": case["question"],
                     "exp_intent": case["expected_intent"], "exp_tier": case["expected_tier"], "intent": d.intent, "tier": d.tier,
                     "confidence": d.confidence, "reason": d.reason, "intent_ok": d.intent == case["expected_intent"],
                     "tier_ok": d.tier == case["expected_tier"]})
    return {"set": name, "doc": doc.get("_doc", ""), "metrics": metrics(rows), "rows": rows}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ev = [r for r in rows if r["evaluable"]]
    n = len(ev)
    high = [r for r in ev if r["confidence"] == "high"]
    pred0 = [r for r in ev if r["tier"] == "tier0"]
    exp0 = [r for r in ev if r["exp_tier"] == "tier0"]
    fp = [r for r in pred0 if r["exp_tier"] != "tier0"]  # se habría mandado a Tier 0 lo que no debía
    fn = [r for r in exp0 if r["tier"] != "tier0"]  # se habría perdido un ahorro posible
    unsafe = [r for r in pred0 if r["exp_tier"] == "tier2"]
    correct0 = [r for r in pred0 if r["tier_ok"] and r["intent_ok"]]
    by_cat: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "tier_ok": 0, "intent_ok": 0, "tier0_pred": 0, "fp": 0, "fn": 0})
    for r in ev:
        c = by_cat[r["category"]]
        c["n"] += 1
        c["tier_ok"] += r["tier_ok"]
        c["intent_ok"] += r["intent_ok"]
        c["tier0_pred"] += r["tier"] == "tier0"
        c["fp"] += r in fp
        c["fn"] += r in fn
    return {
        "total": len(rows), "evaluable": n, "not_evaluable": len(rows) - n,
        "intent_accuracy": _ratio(sum(r["intent_ok"] for r in ev), n),
        "tier_accuracy": _ratio(sum(r["tier_ok"] for r in ev), n),
        "route_accuracy": _ratio(sum(r["intent_ok"] and r["tier_ok"] for r in ev), n),
        "high_confidence": {"n": len(high), "precision_route": _ratio(sum(r["intent_ok"] and r["tier_ok"] for r in high), len(high)),
                            "precision_tier": _ratio(sum(r["tier_ok"] for r in high), len(high))},
        "tier0": {"predicted": len(pred0), "coverage_predicted": _ratio(len(pred0), n),
                  "correct": len(correct0), "coverage_correct": _ratio(len(correct0), n),
                  "expected": len(exp0), "ceiling_coverage": _ratio(len(exp0), n),
                  "precision": _ratio(len(correct0), len(pred0)), "recall": _ratio(len(exp0) - len(fn), len(exp0))},
        "false_positives": len(fp), "false_negatives": len(fn), "unsafe_tier0_for_tier2": len(unsafe),
        "tier_confusion": {f"{e}->{p}": c for (e, p), c in sorted(Counter((r["exp_tier"], r["tier"]) for r in ev).items())},
        "by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
        "meets": {"high_confidence_precision>=95%": (None if not high else
                                                      sum(r["intent_ok"] and r["tier_ok"] for r in high) / len(high) >= MIN_HIGH_CONFIDENCE_PRECISION),
                  "tier0_coverage>=40%": (None if not n else len(pred0) / n >= MIN_TIER0_COVERAGE)},
    }


def _pct(v: float | None) -> str:
    return "n/d" if v is None else f"{v * 100:.1f}%"


def render(result: dict[str, Any], errors: bool = False) -> str:
    m = result["metrics"]
    t0, hc = m["tier0"], m["high_confidence"]
    lines = [f"== {result['set']}: {m['total']} preguntas ({m['evaluable']} evaluables, {m['not_evaluable']} no evaluables) ==",
             f"Intención correcta: {_pct(m['intent_accuracy'])} | nivel correcto: {_pct(m['tier_accuracy'])} | ruta (intención y nivel): {_pct(m['route_accuracy'])}",
             f"Alta confianza: {hc['n']} decisiones; precisión de la ruta {_pct(hc['precision_route'])} (solo nivel {_pct(hc['precision_tier'])}) "
             f"→ criterio ≥95%: {'SÍ' if m['meets']['high_confidence_precision>=95%'] else 'NO'}",
             f"Tier 0: {t0['predicted']} propuestas ({_pct(t0['coverage_predicted'])} de las evaluables; techo según etiquetas {_pct(t0['ceiling_coverage'])}); "
             f"correctas {t0['correct']} (cobertura útil {_pct(t0['coverage_correct'])}); precisión {_pct(t0['precision'])}, exhaustividad {_pct(t0['recall'])} "
             f"→ criterio ≥40%: {'SÍ' if m['meets']['tier0_coverage>=40%'] else 'NO'}",
             f"Falsos positivos de Tier 0: {m['false_positives']} (de ellos a ruta que debía ser Tier 2: {m['unsafe_tier0_for_tier2']}) | "
             f"falsos negativos: {m['false_negatives']}",
             "Matriz esperado→previsto: " + ", ".join(f"{k}={v}" for k, v in m["tier_confusion"].items()),
             "Por categoría (n | nivel ok | intención ok | Tier0 propuestos | FP | FN):"]
    for cat, c in m["by_category"].items():
        lines.append(f"  {cat:18s}{c['n']:4d} | {c['tier_ok']:3d} | {c['intent_ok']:3d} | {c['tier0_pred']:3d} | {c['fp']:2d} | {c['fn']:2d}")
    if errors:
        lines.append("Discrepancias (esperado → previsto):")
        for r in result["rows"]:
            if r["evaluable"] and not (r["intent_ok"] and r["tier_ok"]):
                lines.append(f"  {r['id']:10s} {r['exp_intent']}/{r['exp_tier']} → {r['intent']}/{r['tier']} ({r['confidence']}; {r['reason']}) | {r['question'][:70]}")
    skipped = [r for r in result["rows"] if not r["evaluable"]]
    if skipped:
        lines.append(f"No evaluables ({len(skipped)}): " + "; ".join(f"{r['id']}" for r in skipped[:12]) + (" …" if len(skipped) > 12 else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa el router en sombra (offline; sin LLM ni red).")
    ap.add_argument("--set", choices=("design", "validation", "both"), default="both")
    ap.add_argument("--errors", action="store_true", help="lista las discrepancias")
    ap.add_argument("--json", help="guarda el resultado completo en este fichero")
    args = ap.parse_args(argv)
    names = {"both": ("design_set", "validation"), "design": ("design_set",), "validation": ("validation",)}[args.set]
    results = [evaluate_set(n) for n in names]
    for r in results:
        print(render(r, args.errors), end="\n\n")
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
