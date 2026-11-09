"""Informe de una ejecución: `results.json` (resumen) y `report.md`, por estrato y global (docs/v2/evaluation.md §6)."""

from collections import Counter, defaultdict
from typing import Any

from futsal.agent.audit import LETTERS
from futsal.evals.model import STRATA
from futsal.evals.stats import mean, percentile, wilson

GRADED = ("correct", "partial", "fail", "critical")
NAMES = {**STRATA, **LETTERS}


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 6) if n else None, "wilson95": [lo, hi]}


def group_stats(records: list[dict[str, Any]], expected: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    verdicts = Counter(r["verdict"] for r in records)
    graded = [r for r in records if r["verdict"] in GRADED]
    n = len(graded)
    used = [r for r in records if r["verdict"] in GRADED or r["metrics"]["llm_calls"]]  # el consumo incluye lo abortado
    abstain = [r for r in graded if expected.get(r["case_id"], [{}] * (r["turn"] + 1))[r["turn"]].get("behavior") == "abstain"]
    needed = [r for r in graded if (r.get("grade") or {}).get("tools_missing") is not None
              and expected[r["case_id"]][r["turn"]].get("tools_needed")]
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"questions": 0, "input": 0, "cached": 0, "output": 0, "thinking": 0})
    for r in used:
        m = r["metrics"]
        s = by_source[m["token_source"]]
        s["questions"] += 1
        s["input"] += m["input_tokens"]
        s["cached"] += m["cached_tokens"]
        s["output"] += m["output_tokens"]
        s["thinking"] += m["thinking_tokens"]
    llm_ms = [x for r in used for x in r["timing"]["llm_call_ms"]]
    tool_ms = [x for r in used for x in r["timing"]["tool_call_ms"]]
    costs = [r["metrics"]["cost_usd"] for r in used if r["metrics"]["cost_usd"] is not None]
    avg_cost = mean(costs)
    return {
        "turns": len(records), "verdicts": {v: verdicts.get(v, 0) for v in (*GRADED, "review", "not_evaluable", "not_run")},
        "evaluable": n, "accuracy": _rate(verdicts.get("correct", 0), n),
        "critical": {**_rate(verdicts.get("critical", 0), n), "cases": sorted({f"{r['case_id']}#{r['turn'] + 1}" for r in graded if r["verdict"] == "critical"})},
        "interpretation": verdicts.get("fail", 0), "presentation": verdicts.get("partial", 0),
        "error_classes": dict(Counter(r["error_class"] for r in graded if r["error_class"])),
        "abstention_correct": _rate(sum(r["verdict"] == "correct" for r in abstain), len(abstain)),
        "tools": {"needed_covered": _rate(sum(not r["grade"]["tools_missing"] for r in needed), len(needed)),
                  "unexpected_calls": sum(len(r["grade"]["tools_unexpected"]) for r in graded if r.get("grade"))},
        "per_question": {"llm_calls": mean([r["metrics"]["llm_calls"] for r in used]), "mcp_calls": mean([r["metrics"]["mcp_calls"] for r in used]),
                         "questions": len(used)},
        "tokens_by_source": dict(sorted(by_source.items())),
        "latency": {"llm_call_ms": {"p50": percentile(llm_ms, 50), "p95": percentile(llm_ms, 95), "calls": len(llm_ms)},
                    "tool_call_ms": {"p50": percentile(tool_ms, 50), "p95": percentile(tool_ms, 95), "calls": len(tool_ms)}},
        "cost": {"priced_questions": len(costs), "questions": len(used), "usd_per_question": avg_cost,
                 "usd_per_1000": round(avg_cost * 1000, 4) if avg_cost is not None else None},
        "retries": sum(r["metrics"]["retries"] for r in used), "tool_errors": sum(r["metrics"]["errors"] for r in used),
        "over_llm_budget": sum(bool((r.get("grade") or {}).get("over_budget")) for r in graded)}


def consistency(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Entre repeticiones: proporción de turnos con el mismo veredicto en todas y con los mismos hechos."""
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r["verdict"] in GRADED:
            groups[(r["case_id"], r["turn"])].append(r)
    multi = [g for g in groups.values() if len(g) >= 2]
    same_verdict = sum(len({r["verdict"] for r in g}) == 1 for g in multi)
    same_facts = sum(len({tuple(sorted((r.get("grade") or {}).get("facts_present", []))) for r in g}) == 1 for g in multi)
    return {"turns_with_repetitions": len(multi), "same_verdict": _rate(same_verdict, len(multi)),
            "same_facts": _rate(same_facts, len(multi))}


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    records = results["records"]
    expected = {cid: c["expected"] for cid, c in results["cases"].items()}
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        strata[r["stratum"]].append(r)
    review = [{"case": r["case_id"], "turn": r["turn"] + 1, "rep": r["rep"] + 1, "question": r["question"], "reason": r["reason"]}
              for r in records if r["verdict"] == "review"]
    critical = [{"case": r["case_id"], "turn": r["turn"] + 1, "rep": r["rep"] + 1, "question": r["question"], "reason": r["reason"]}
                for r in records if r["verdict"] == "critical"]
    reasons = Counter(str(r["reason"]).split(":")[0][:80] for r in records if r["verdict"] == "not_evaluable")
    return {"global": group_stats(records, expected), "strata": {s: group_stats(rs, expected) for s, rs in sorted(strata.items())},
            "critical_list": critical, "review_list": review, "not_evaluable_reasons": dict(reasons.most_common()), "consistency": consistency(records)}


# ----------------------------------------------------------------------------- Markdown
def _pct(rate: dict[str, Any]) -> str:
    if rate["rate"] is None:
        return "—"
    return f"{rate['rate'] * 100:.1f} % [{rate['wilson95'][0] * 100:.1f}–{rate['wilson95'][1] * 100:.1f}]"


def _num(v: Any, digits: int = 1) -> str:
    return "n/d" if v is None else f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def render_markdown(results: dict[str, Any]) -> str:
    meta, s = results["meta"], results["summary"]
    snap = meta["snapshot"]
    lines = [f"# Informe Futsito-Eval v1 · {meta['suite']} ({meta['split']})", "",
             f"- Snapshot `{snap['id']}` · as_of {snap['as_of']} · huella `{snap['fingerprint'][:12]}`"
             + (" · **SINTÉTICO (solo desarrollo, no es referencia)**" if snap["synthetic"] else ""),
             (f"- Modelo: `{meta['model']['provider']}/{meta['model']['model']}` · brazo `{meta['arm']}` · commit `{meta['commit'][:12]}` "
              f"· repeticiones {meta['reps']} · {meta['generated_at']}"),
             f"- Peticiones al LLM: {meta['budget']['used']} de un tope de {meta['budget']['max_requests']}"
             + (f" · **PARADA: {meta['stopped']}**" if meta["stopped"] else ""),
             f"- Banderas del agente: {', '.join(f'{k}={v}' for k, v in meta['agent_flags'].items())}",
             f"- Temperatura: {meta['temperature']}", ""]
    g = s["global"]
    lines += ["## Resumen global", "",
              (f"- Turnos: {g['turns']} · evaluables {g['evaluable']} · no evaluables {g['verdicts']['not_evaluable']} · "
               f"sin ejecutar {g['verdicts']['not_run']} · pendientes de revisión humana {g['verdicts']['review']} (no cuentan como acierto)"),
              f"- **Exactitud: {_pct(g['accuracy'])}** (correctas {g['verdicts']['correct']}; las parciales cuentan como fallo)",
              (f"- **Errores críticos: {g['verdicts']['critical']}** ({_pct(g['critical'])}) · interpretación: {g['interpretation']} · "
               f"presentación: {g['presentation']}"),
              (f"- Abstención correcta: {_pct(g['abstention_correct'])} · cobertura de herramientas necesarias: "
               f"{_pct(g['tools']['needed_covered'])} · llamadas a herramientas no esperadas: {g['tools']['unexpected_calls']}"), ""]
    lines += ["## Por estrato", "",
              "| Estrato | Evaluables | Correctas | Exactitud (Wilson 95 %) | Presentación | Interpretación | Críticos | No evaluables | Sin ejecutar |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, st in s["strata"].items():
        v = st["verdicts"]
        lines.append(f"| {name} {NAMES.get(name, '')} | {st['evaluable']} | {v['correct']} | {_pct(st['accuracy'])} | {v['partial']} | "
                     f"{v['fail']} | {v['critical']} | {v['not_evaluable']} | {v['not_run']} |")
    lines += ["", "## Errores críticos", ""]
    lines += ([f"- `{c['case']}` (turno {c['turn']}, rep {c['rep']}): {c['reason']}" for c in s["critical_list"]]
              or ["Ninguno."])
    if s["review_list"]:
        lines += ["", "## Pendientes de revisión humana (needs_human_review)", ""]
        lines += [f"- `{c['case']}` (turno {c['turn']}, rep {c['rep']}): {c['reason']}" for c in s["review_list"]]
    c = s["consistency"]
    lines += ["", "## Consistencia entre repeticiones", "",
              (f"- Turnos con ≥ 2 repeticiones: {c['turns_with_repetitions']} · mismo veredicto en todas: {_pct(c['same_verdict'])} "
               f"· mismos hechos: {_pct(c['same_facts'])}"), "", "## Consumo y latencia", "",
              "| Estrato | LLM/preg. | MCP/preg. | LLM p50 / p95 (ms) | Tools p50 / p95 (ms) | Coste/preg. (USD) | Coste/1.000 (USD) | Reintentos | Errores tool |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, st in {"GLOBAL": g, **s["strata"]}.items():
        lat, cost, pq = st["latency"], st["cost"], st["per_question"]
        row = (f"| {name} | {_num(pq['llm_calls'], 2)} | {_num(pq['mcp_calls'], 2)} | "
               f"{_num(lat['llm_call_ms']['p50'], 0)} / {_num(lat['llm_call_ms']['p95'], 0)} | "
               f"{_num(lat['tool_call_ms']['p50'], 0)} / {_num(lat['tool_call_ms']['p95'], 0)} | "
               f"{_num(cost['usd_per_question'], 6) if cost['usd_per_question'] is not None else 'null'} | "
               f"{_num(cost['usd_per_1000'], 3) if cost['usd_per_1000'] is not None else 'null'} | {st['retries']} | {st['tool_errors']} |")
        lines.append(row)
    lines += ["", "Tokens por origen (`provider` = informado por el proveedor; `estimated` = estimado; `measured_zero` = sin llamadas al LLM):", "",
              "| Origen | Preguntas | Entrada | Cacheados | Salida | Razonamiento |", "|---|---|---|---|---|---|"]
    for src, t in g["tokens_by_source"].items():
        lines.append(f"| {src} | {t['questions']} | {t['input']} | {t['cached']} | {t['output']} | {t['thinking']} |")
    pr = meta["prices"]
    tarifa = (f"{pr['model']}: entrada {pr['input']} · cacheada {pr['cached_input']} · salida {pr['output']} USD/1M; fuente {pr['source']}; "
              f"verified_at {pr['verified_at']}" if "source" in pr else f"{pr['model']}: {pr['note']}")
    lines += ["", f"Coste: {g['cost']['priced_questions']} de {g['cost']['questions']} preguntas con tarifa. Tarifa usada ({tarifa}).", ""]
    if meta["suite"] == "regression":
        d = meta["dataset"]
        lines += [(f"Nota: las cifras 72 casos / 65 evaluables de la batería de regresión están DECLARADAS (docs/v2/evaluation.md) hasta medirlas "
                  f"sobre el snapshot real. En este snapshot se miden {d['cases']} casos / {d['evaluable']} evaluables."), ""]
    if s["not_evaluable_reasons"]:
        lines += ["## No evaluables (aparte; nunca cuentan como acierto)", ""]
        lines += [f"- {n} × {why}" for why, n in s["not_evaluable_reasons"].items()]
        lines.append("")
    lines += ["## Reloj de pared y reproducibilidad", ""]
    lines += [f"- {d}" for d in meta["clock"]["dependencies"]]
    lines.append("")
    return "\n".join(lines)
