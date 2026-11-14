"""Análisis comparativo de una ejecución del benchmark: calidad, coste, latencia, rankings, Pareto y router híbrido.

Clasificación de cada turno (a partir del calificador determinista de Futsito-Eval, sin LLM):
- correcta: conducta esperada, hechos requeridos presentes, nada sin respaldo (veredicto `correct`);
- parcial: conducta correcta con algún hecho requerido ausente o mal presentado (`partial`);
- incorrecta: conducta equivocada, p. ej. responde cuando debía aclarar o abstenerse, o no responde a lo pedido (`fail`);
- alucinacion: cifra, marcador, nombre o regla sin respaldo en la evidencia, o datos de otra competición/grupo/temporada (`critical`);
- error_tecnico: el turno no tuvo respuesta por un fallo de infraestructura (red, 5xx, 429 no recuperado) (`not_evaluable` + `infra:`);
- no_evaluable: falta una capacidad de datos en el snapshot o el calificador pide revisión humana (`not_evaluable` / `review`);
- no_ejecutada: no se llegó a ejecutar (presupuesto o cuota agotados) (`not_run`).
La exactitud principal es correctas / (correctas + parciales + incorrectas + alucinaciones), con IC de Wilson al 95 %.

Coste: CALCULADO con la tarifa del registro y los tokens de cada llamada. Base `provider` (tokens medidos por el proveedor),
`estimated` (estimados por el agente) o `unavailable` (sin tarifa o sin tokens: no se suma ni se trata como cero). Nunca es un
importe facturado.
"""

import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from futsal.evals.bench.registry import ModelSpec, call_cost, parse_model
from futsal.evals.stats import BOOTSTRAP_SEED, bootstrap_ci, mean, percentile, wilson

CLASSES = ("correcta", "parcial", "incorrecta", "alucinacion", "error_tecnico", "no_evaluable", "no_ejecutada")
GRADED = ("correcta", "parcial", "incorrecta", "alucinacion")
PROJECTIONS = (100, 1000, 10000)
SMALL_SAMPLE = 30
DEFAULT_GAP = 0.05  # diferencia de exactitud que se acepta como «calidad comparable» (y solo si el IC pareado no la contradice)


def classify(record: dict[str, Any]) -> str:
    v = record["verdict"]
    if v == "correct":
        return "correcta"
    if v == "partial":
        return "parcial"
    if v == "fail":
        return "incorrecta"
    if v == "critical":
        return "alucinacion"
    if v == "not_run":
        return "no_ejecutada"
    if v == "not_evaluable" and str(record.get("reason", "")).startswith("infra:"):
        return "error_tecnico"
    return "no_evaluable"


def exact_percentile(values: Sequence[float], q: float) -> float | None:
    """Percentil sin redondeo (los costes por pregunta son del orden de 1e-4 $: el percentil compartido redondea a 3 decimales)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def exact_mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 6) if n else None, "wilson95": [lo, hi]}


# ----------------------------------------------------------------------------- filas por turno
def turn_rows(results: dict[str, Any], spec: ModelSpec, cases: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in results["records"]:
        calls = r.get("llm_calls_detail") or []
        costs = [call_cost(spec, c) for c in calls]
        bases = {c.basis for c in costs}
        if not calls:
            usd, basis, usd_nc = (0.0, "none", 0.0) if r["metrics"]["llm_calls"] == 0 else (None, "unavailable", None)
        elif "unavailable" in bases:
            usd, basis, usd_nc = None, "unavailable", None
        else:
            usd = round(sum(c.usd or 0.0 for c in costs), 10)
            usd_nc = round(sum(c.usd_no_cache or 0.0 for c in costs), 10)
            basis = "estimated" if "estimated" in bases else "provider"
        timing = r.get("timing", {})
        paced = sum(float(x or 0.0) for x in timing.get("llm_paced_ms", []))
        call_ms = list(timing.get("llm_call_ms", []))
        info = cases.get(r["case_id"], {})
        rows.append({
            "case_id": r["case_id"], "turn": r["turn"], "rep": r["rep"], "question": r["question"], "answer": r.get("answer", ""),
            "class": classify(r), "verdict": r["verdict"], "reason": r.get("reason", ""), "category": info.get("category", "?"),
            "complexity": info.get("complexity", "?"), "turns_in_case": info.get("turns", 1), "tools_used": r.get("tools_used", []),
            "llm_calls": r["metrics"]["llm_calls"], "mcp_calls": r["metrics"]["mcp_calls"], "retries": r["metrics"]["retries"],
            "tool_errors": r["metrics"]["errors"], "input_tokens": r["metrics"]["input_tokens"], "cached_tokens": r["metrics"]["cached_tokens"],
            "output_tokens": r["metrics"]["output_tokens"], "thinking_tokens": r["metrics"]["thinking_tokens"],
            "token_source": r["metrics"]["token_source"], "cost_usd": usd, "cost_usd_no_cache": usd_nc, "cost_basis": basis,
            "calls": [{"index": c.get("index"), "input": c.get("input"), "cached": c.get("cached"), "output": c.get("output"),
                       "thinking": c.get("thinking"), "token_source": c.get("token_source"), "cost_usd": k.usd, "basis": k.basis,
                       "latency_ms": call_ms[i] if i < len(call_ms) else None, "retries": c.get("retries"), "error": c.get("error")}
                      for i, (c, k) in enumerate(zip(calls, costs, strict=True))],
            "latency_ms": round(max(float(r["timing"]["latency_ms"]) - paced, 0.0), 1),
            "executed": r["verdict"] != "not_run" and (r["metrics"]["llm_calls"] > 0 or r["verdict"] in ("correct", "partial", "fail", "critical")),
            "grade": r.get("grade")})
    return rows


# ----------------------------------------------------------------------------- estadísticas de un modelo
def _quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["class"] for r in rows)
    graded = [r for r in rows if r["class"] in GRADED]
    n = len(graded)
    return {"turns": len(rows), "classes": {c: counts.get(c, 0) for c in CLASSES}, "evaluable": n,
            "accuracy": _rate(counts.get("correcta", 0), n), "acceptable": _rate(counts.get("correcta", 0) + counts.get("parcial", 0), n),
            "hallucination": _rate(counts.get("alucinacion", 0), n), "small_sample": n < SMALL_SAMPLE}


def _bootstrap_mean(values: Sequence[float], seed: int = BOOTSTRAP_SEED, rounds: int = 2000) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    return round(means[int(rounds * 0.025)], 8), round(means[int(rounds * 0.975) - 1], 8)


def _cost(rows: list[dict[str, Any]], free_tier: bool) -> dict[str, Any]:
    executed = [r for r in rows if r["executed"]]
    priced = [r for r in executed if r["cost_usd"] is not None]
    values = [float(r["cost_usd"]) for r in priced]
    total = round(sum(values), 8)
    correct = sum(r["class"] == "correcta" for r in executed)
    conv: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in executed:
        conv[(r["case_id"], r["rep"])].append(r)
    conv_costs = [sum(float(x["cost_usd"]) for x in v) for v in conv.values() if all(x["cost_usd"] is not None for x in v)]
    chained = [sum(float(x["cost_usd"]) for x in v) for v in conv.values()
               if v[0]["turns_in_case"] > 1 and all(x["cost_usd"] is not None for x in v)]
    by_cat: dict[str, list[float]] = defaultdict(list)
    for r in priced:
        by_cat[r["category"]].append(float(r["cost_usd"]))
    avg = exact_mean(values)
    ci = _bootstrap_mean(values)
    complete = len(priced) == len(executed) and bool(executed)
    return {
        "questions_executed": len(executed), "questions_priced": len(priced), "complete": complete,
        "basis": dict(Counter(r["cost_basis"] for r in executed)),
        "free_tier": free_tier, "label": ("equivalente a tarifa de pago (plan gratuito: facturado 0)" if free_tier
                                          else "calculado con tarifas y tokens; no es el importe facturado"),
        "total_usd": total, "total_usd_no_cache": round(sum(float(r["cost_usd_no_cache"] or 0.0) for r in priced), 8),
        "per_question": {"mean": avg, "median": exact_percentile(values, 50), "p95": exact_percentile(values, 95), "mean_ci95": list(ci) if ci else None},
        "per_correct": round(total / correct, 8) if correct and complete else None,
        "per_conversation": {"mean": exact_mean(conv_costs), "n": len(conv_costs)},
        "per_chained_conversation": {"mean": exact_mean(chained), "n": len(chained)},
        "by_category": {c: {"mean": exact_mean(v), "n": len(v)} for c, v in sorted(by_cat.items())},
        "projections": {str(n): {"usd": round(avg * n, 4) if avg is not None else None,
                                 "range95": [round(ci[0] * n, 4), round(ci[1] * n, 4)] if ci else None} for n in PROJECTIONS},
        "projection_assumptions": ["misma mezcla de categorías y conversaciones que la suite", "mismo prompt, herramientas y modelo",
                                   "sin caché de prompts", "tarifas del registro en su fecha de consulta",
                                   "media por pregunta medida en esta ejecución; rango = IC 95 % bootstrap de la media"]}


def _latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [float(r["latency_ms"]) for r in rows if r["executed"]]
    return {"p50_ms": percentile(vals, 50), "p95_ms": percentile(vals, 95), "mean_ms": mean(vals), "n": len(vals),
            "note": "reloj de pared de la pregunta completa (LLM + MCP) sin las pausas de ritmo del benchmark"}


def model_stats(rows: list[dict[str, Any]], spec: ModelSpec, summary: dict[str, Any] | None) -> dict[str, Any]:
    executed = [r for r in rows if r["executed"]]
    by_cat = {c: _quality([r for r in rows if r["category"] == c]) for c in sorted({r["category"] for r in rows})}
    by_cx = {c: _quality([r for r in rows if r["complexity"] == c]) for c in sorted({r["complexity"] for r in rows})}
    cx_cost = {c: exact_mean([float(r["cost_usd"]) for r in executed if r["complexity"] == c and r["cost_usd"] is not None])
               for c in by_cx}
    conv: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["turns_in_case"] > 1:
            conv[(r["case_id"], r["rep"])].append(r)
    chained_ok = [all(x["class"] == "correcta" for x in v) for v in conv.values() if all(x["class"] in GRADED for x in v)]
    g = (summary or {}).get("global", {})
    return {
        "model": spec.as_dict(), "quality": _quality(rows), "by_category": by_cat, "by_complexity": by_cx,
        "cost_by_complexity": cx_cost, "chained_conversations": _rate(sum(chained_ok), len(chained_ok)),
        "tools": g.get("tools"), "abstention_correct": g.get("abstention_correct"),
        "usage": {"llm_calls_per_question": mean([r["llm_calls"] for r in executed]), "mcp_calls_per_question": mean([r["mcp_calls"] for r in executed]),
                  "retries": sum(r["retries"] for r in executed), "tool_errors": sum(r["tool_errors"] for r in executed),
                  "tokens": {"input": sum(r["input_tokens"] for r in executed), "cached": sum(r["cached_tokens"] for r in executed),
                             "output": sum(r["output_tokens"] for r in executed), "thinking": sum(r["thinking_tokens"] for r in executed),
                             "sources": dict(Counter(r["token_source"] for r in executed))}},
        "cost": _cost(rows, spec.free_tier), "latency": _latency(rows)}


# ----------------------------------------------------------------------------- comparación entre modelos
def paired(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Diferencia de exactitud A − B sobre los mismos turnos evaluables en ambos (bootstrap pareado por turno, semilla fija)."""
    ka = {(r["case_id"], r["turn"], r["rep"]): r["class"] for r in a if r["class"] in GRADED}
    kb = {(r["case_id"], r["turn"], r["rep"]): r["class"] for r in b if r["class"] in GRADED}
    common = sorted(set(ka) & set(kb))
    diffs = [float(ka[k] == "correcta") - float(kb[k] == "correcta") for k in common]
    ci = bootstrap_ci(diffs)
    d = mean(diffs)
    verdict = "sin datos" if ci is None else ("A mejor" if ci[0] > 0 else "B mejor" if ci[1] < 0 else "sin diferencia demostrada")
    return {"n": len(common), "diff": d, "ci95": list(ci) if ci else None, "verdict": verdict}


def pareto(stats: dict[str, dict[str, Any]]) -> list[str]:
    """Modelos no dominados en (exactitud ↑, coste medio por pregunta ↓, latencia p50 ↓). Solo con las tres métricas disponibles."""
    pts = {m: (s["quality"]["accuracy"]["rate"], s["cost"]["per_question"]["mean"], s["latency"]["p50_ms"]) for m, s in stats.items()}
    pts = {m: p for m, p in pts.items() if None not in p}
    front = []
    for m, (acc, cost, lat) in pts.items():
        dominated = any(o != m and oa >= acc and oc <= cost and ol <= lat and (oa > acc or oc < cost or ol < lat)
                        for o, (oa, oc, ol) in pts.items())
        if not dominated:
            front.append(m)
    return sorted(front)


def _comparable(a: str, b: str, stats: dict[str, Any], pairs: dict[tuple[str, str], Any], gap: float, key: str = "quality") -> bool:
    """¿La calidad de `b` es comparable a la de `a`? Diferencia ≤ gap y el IC pareado no demuestra que `a` sea mejor."""
    qa = stats[a][key]["accuracy"]["rate"] if key == "quality" else stats[a][key]
    qb = stats[b][key]["accuracy"]["rate"] if key == "quality" else stats[b][key]
    if qa is None or qb is None:
        return False
    pair = pairs.get((a, b))
    proven_worse = bool(pair and pair["ci95"] and pair["ci95"][0] > 0)
    return qa - qb <= gap and not proven_worse


def compare(stats: dict[str, dict[str, Any]], rows: dict[str, list[dict[str, Any]]], gap: float = DEFAULT_GAP) -> dict[str, Any]:
    models = [m for m in stats if stats[m]["quality"]["evaluable"]]
    pairs = {(a, b): paired(rows[a], rows[b]) for a in models for b in models if a != b}

    def acc(m: str) -> float:
        return float(stats[m]["quality"]["accuracy"]["rate"] or 0.0)

    def hall(m: str) -> float:
        return float(stats[m]["quality"]["hallucination"]["rate"] or 0.0)

    def cpq(m: str) -> float:
        v = stats[m]["cost"]["per_question"]["mean"]
        return float("inf") if v is None else float(v)

    def cpc(m: str) -> float:
        v = stats[m]["cost"]["per_correct"]
        return float("inf") if v is None else float(v)

    quality_rank = sorted(models, key=lambda m: (-acc(m), hall(m)))
    economic_rank = sorted(models, key=cpc)
    speed_rank = sorted(models, key=lambda m: (stats[m]["latency"]["p50_ms"] or float("inf"), stats[m]["latency"]["p95_ms"] or float("inf")))
    recs: dict[str, Any] = {}
    if quality_rank:
        top = quality_rank[0]
        tied = [m for m in quality_rank if m == top or _comparable(top, m, stats, pairs, 0.0)]
        best = min(tied, key=lambda m: (hall(m), cpq(m)))
        recs["best_overall"] = {"model": best, "tied_with": [m for m in tied if m != best],
                                "evidence": "IC pareado: sin diferencia demostrada con los empatados" if len(tied) > 1 else
                                "mayor exactitud con diferencia pareada demostrada" if len(models) > 1 else "único modelo"}
        acceptable = [m for m in models if _comparable(best, m, stats, pairs, gap) and hall(m) <= hall(best) + gap]
        recs["cheapest_acceptable"] = {"model": min(acceptable, key=cpq) if acceptable else None, "criterion":
                                       f"exactitud ≤ {gap * 100:.0f} puntos por debajo de {best}, sin peor resultado demostrado y "
                                       f"alucinaciones ≤ {gap * 100:.0f} puntos por encima; el más barato por pregunta"}
        for cx, key in (("complex", "best_complex"), ("simple", "best_simple")):
            cand = [m for m in models if stats[m]["by_complexity"].get(cx, {}).get("evaluable")]
            if cand:
                m = max(cand, key=lambda x: (stats[x]["by_complexity"][cx]["accuracy"]["rate"] or 0.0, -cpq(x)))
                q = stats[m]["by_complexity"][cx]
                recs[key] = {"model": m, "accuracy": q["accuracy"], "small_sample": q["small_sample"]}
    recs["router"] = router(stats, models, gap)
    return {"pairs": {f"{a}|{b}": v for (a, b), v in pairs.items()}, "rank_quality": quality_rank, "rank_economic": economic_rank,
            "rank_speed": speed_rank, "pareto": pareto({m: stats[m] for m in models}), "recommendations": recs, "gap": gap}


def router(stats: dict[str, dict[str, Any]], models: list[str], gap: float) -> dict[str, Any]:
    """Router ORÁCULO por complejidad (sencilla/compleja según la suite): para cada clase, el modelo más barato con exactitud
    comparable al mejor en esa clase. Es una COTA del ahorro: supone clasificar sin error ni coste; un router real ahorra menos."""
    if len(models) < 2:
        return {"applicable": False, "reason": "hace falta más de un modelo"}
    classes = sorted({cx for m in models for cx in stats[m]["by_complexity"]})
    shares: dict[str, int] = {cx: max(stats[m]["by_complexity"].get(cx, {}).get("evaluable", 0) for m in models) for cx in classes}
    total = sum(shares.values())
    if not total:
        return {"applicable": False, "reason": "sin turnos evaluables"}
    choice: dict[str, Any] = {}
    for cx in classes:
        def q(m: str, _cx: str = cx) -> float | None:
            rate = stats[m]["by_complexity"].get(_cx, {}).get("accuracy", {}).get("rate")
            return None if rate is None else float(rate)
        cand = [m for m in models if q(m) is not None and stats[m]["cost_by_complexity"].get(cx) is not None]
        if not cand:
            return {"applicable": False, "reason": f"sin coste o calidad medidos en «{cx}»"}
        best_q = max(q(m) or 0.0 for m in cand)
        ok = [m for m in cand if best_q - (q(m) or 0.0) <= gap]
        pick = min(ok, key=lambda m: float(stats[m]["cost_by_complexity"][cx]))
        choice[cx] = {"model": pick, "accuracy": q(pick), "cost_per_question": stats[pick]["cost_by_complexity"][cx], "share": shares[cx] / total}
    mix_cost = sum(c["share"] * float(c["cost_per_question"]) for c in choice.values())
    mix_acc = sum(c["share"] * float(c["accuracy"] or 0.0) for c in choice.values())
    single = max(models, key=lambda m: (stats[m]["quality"]["accuracy"]["rate"] or 0.0))
    single_cost = stats[single]["cost"]["per_question"]["mean"]
    saving = None if not single_cost else round(1 - mix_cost / float(single_cost), 4)
    return {"applicable": True, "choice": choice, "estimated_accuracy": round(mix_acc, 4), "estimated_cost_per_question": round(mix_cost, 8),
            "reference_model": single, "reference_cost_per_question": single_cost, "saving_vs_reference": saving,
            "worth_it": bool(saving and saving > 0.2 and len({c["model"] for c in choice.values()}) > 1),
            "caveats": ["cota superior: router perfecto y gratuito", "clases de la suite (S1 de un turno = sencilla); en producción la mezcla real puede ser otra",
                        "con muestras pequeñas por clase la elección puede cambiar entre ejecuciones"]}


def analyze(meta: dict[str, Any], results: dict[str, dict[str, Any]], gap: float = DEFAULT_GAP) -> dict[str, Any]:
    specs = {mid: parse_model(meta["models"][mid]) for mid in results}  # la tarifa CONGELADA en bench.json, no la del YAML actual
    rows = {mid: turn_rows(res, specs[mid], meta["cases"]) for mid, res in results.items()}
    stats = {mid: model_stats(rows[mid], specs[mid], results[mid].get("summary")) for mid in results}
    warnings = []
    for mid, s in stats.items():
        q = s["quality"]
        if q["small_sample"]:
            warnings.append(f"{mid}: solo {q['evaluable']} turnos evaluables: los intervalos son anchos; no decidas por diferencias pequeñas")
        if not s["cost"]["complete"]:
            warnings.append(f"{mid}: {s['cost']['questions_executed'] - s['cost']['questions_priced']} preguntas sin coste calculable "
                            f"(bases {s['cost']['basis']})")
        st = meta.get("models_status", {}).get(mid, {})
        if st.get("status") != "complete":
            warnings.append(f"{mid}: ejecución {st.get('status')} ({st.get('detail')}): resultados incompletos")
    for mid, st in meta.get("models_status", {}).items():
        if mid not in results:
            warnings.append(f"{mid}: sin resultados ({st.get('status')}: {st.get('detail')})")
    if meta.get("kind") == "dry_run":
        warnings.insert(0, "EJECUCIÓN DE PRUEBA (dry-run): modelo simulado; los veredictos y costes NO miden ningún modelo")
    if meta["snapshot"].get("synthetic"):
        warnings.insert(0, "snapshot SINTÉTICO: solo desarrollo; no sirve para decidir")
    snap = meta["snapshot"]
    if snap.get("live"):
        if snap.get("data_changed"):
            warnings.insert(0, "DATOS VIVOS (sin snapshot) y CAMBIARON durante la ejecución (tablas con distinto número de filas: "
                               f"{', '.join(snap.get('changed_tables') or []) or 'mismo número de filas, distinto contenido'}): "
                               "algún veredicto puede deberse al cambio y no al modelo")
        elif "data_changed" in snap:
            warnings.insert(0, "datos vivos (sin snapshot): no cambiaron durante la ejecución (misma huella al empezar y al terminar)")
        else:
            warnings.insert(0, "datos vivos (sin snapshot): ejecución sin terminar; no se ha comprobado si los datos cambiaron")
    return {"meta": meta, "stats": stats, "comparison": compare(stats, rows, gap), "warnings": warnings, "rows": rows}
