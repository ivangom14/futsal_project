"""Auditoría de fiabilidad (Fase 17): taxonomía de resultados, errores críticos, cobertura por fuente y recomendaciones.

Todo se calcula sobre los `CaseResult` ya producidos (sin red, sin BD, sin LLM). Una media buena nunca oculta un fallo crítico:
los críticos se listan aparte y encabezan el resumen.
"""

from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from futsal.agent.evaluate import CaseResult, EvalCase

# Situación que el caso comprueba (qué debe hacer bien el agente), en el vocabulario de la fase.
EXPECT_LABEL = {
    "complete": "Correcto y completo", "partial": "Correcto pero parcial", "no_data": "Sin datos",
    "not_ingested": "No ingerido", "query_error": "Error de ingesta o consulta", "unverifiable": "Fuente no verificable"}
NO_DATA_KINDS = frozenset({"limitation", "no_report", "no_player", "empty_scope", "not_played", "no_sub_minutes", "no_evidence_rules"})
TABLE_STATUS_EXPECT = {"not_applicable": "no_data", "empty": "no_data", "not_ingested": "not_ingested",
                       "round_not_ingested": "not_ingested", "failed": "query_error", "incomplete": "partial"}

# Categorías funcionales del plan de evaluación (A-F) + una para el rechazo de preguntas ajenas.
LETTERS = {"A": "Resultados y calendario", "B": "Clasificaciones oficiales", "C": "Goleadores oficiales",
           "D": "Actas y eventos", "E": "Reglamento y RAG", "F": "Consultas combinadas", "G": "Fuera de alcance"}
SOURCES = {"A": "resultados", "B": "clasificaciones", "C": "goleadores", "D": "actas", "E": "rag", "F": "combinadas", "G": "alcance"}
FORCED_A = frozenset({"partido_mas_goles", "equipo_jornada_5", "proximo_campo"})  # etiquetadas «combinada» en la Fase 8 pero de una sola fuente
OFFICIAL_PREFIXES = ("tablas", "combinada_fuentes")  # categorías donde la fuente de verdad es la tabla oficial
# herramientas con las que se podría RECONSTRUIR una tabla oficial (resultados y actas); descubrir grupos o equipos no es reconstruir
RECONSTRUCTION_TOOLS = frozenset({"list_matches", "get_match", "get_match_report", "list_match_events", "top_scorers"})


def letter(case_id: str, category: str) -> str:
    """Categoría A-G de un caso. Se decide por categoría e id para no renombrar casos anteriores."""
    if category == "combinada_fuentes":
        return "F"
    if category.startswith("tablas"):
        return "C" if "goleador" in case_id else "B"
    if category.startswith("reglamento"):
        return "E"
    if category == "alcance":
        return "G"
    if case_id.startswith("acta_") or category == "actas":
        return "D"
    return "A"


def expectation(case: "EvalCase") -> str:
    """Situación que comprueba un caso: explícita (`case.expect`) o deducida de sus hechos."""
    if case.expect:
        return case.expect
    kinds = {f.kind for f in case.facts or []}
    for f in case.facts or []:
        if f.kind == "table_status":
            return TABLE_STATUS_EXPECT.get(f.status or "", "no_data")
    if "no_report_finished" in kinds:
        return "not_ingested"
    if "partial_coverage" in kinds:
        return "partial"
    if kinds & NO_DATA_KINDS:
        return "no_data"
    return "complete"


def critical_reasons(category: str, status: str, unexpected_tools: list[str], claims: list[str],
                     invention_ok: bool | None) -> list[str]:
    """Fallos que no se compensan con ninguna media: invención (cifras, citas, marcadores) o fuente oficial sustituida."""
    if status != "fail":
        return []
    reasons: list[str] = []
    if invention_ok is False:
        reasons.append("invención: " + ("; ".join(claims) or "afirmaciones sin respaldo en la evidencia"))
    rebuilt = sorted(set(unexpected_tools) & RECONSTRUCTION_TOOLS)
    if rebuilt and category.startswith(OFFICIAL_PREFIXES):
        reasons.append("fuente incorrecta: usó " + ", ".join(rebuilt) + " en una pregunta de tabla oficial (no se reconstruye)")
    return reasons


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def _counts(rows: "Iterable[CaseResult]") -> dict[str, Any]:
    rs = list(rows)
    p, f = sum(r.status == "pass" for r in rs), sum(r.status == "fail" for r in rs)
    return {"cases": len(rs), "pass": p, "fail": f, "not_evaluable": sum(r.status == "not_evaluable" for r in rs),
            "not_run": sum(r.status == "not_run" for r in rs), "pass_rate_evaluable_pct": _pct(p, p + f)}


def _cause(detail: str) -> str:
    return detail.split(":")[0].strip() or "sin detalle"


def audit_summary(results: "list[CaseResult]", prices: tuple[float, float] | None = None) -> dict[str, Any]:
    """Resumen de fiabilidad. `prices` = (USD por millón de tokens de entrada, de salida); sin precios no se estima coste."""
    ev = [r for r in results if r.status in ("pass", "fail")]
    by_letter = {k: _counts(r for r in results if letter(r.case_id, r.category) == k) | {"name": v} for k, v in LETTERS.items()}
    by_letter = {k: v for k, v in by_letter.items() if v["cases"]}
    situations: dict[str, dict[str, int]] = {k: {"pass": 0, "fail": 0} for k in EXPECT_LABEL}
    for r in ev:
        if r.expect in situations:
            situations[r.expect][r.status] += 1
    by_source = {SOURCES[k]: v for k, v in by_letter.items()}
    calls = [r.tool_calls for r in ev]
    critical = [{"case": r.case_id, "category": r.category, "reasons": r.critical} for r in results if r.critical]
    causes = Counter(_cause(m) for r in results if r.status == "fail" for m in (r.detail.split(" | ") if r.detail else ["sin detalle"]))
    rag = [r for r in ev if letter(r.case_id, r.category) == "E"]
    tin, tout = sum(r.input_tokens for r in ev), sum(r.output_tokens + r.thinking_tokens for r in ev)
    cost = None if prices is None or any(r.tokens_estimated for r in ev) else round(tin / 1e6 * prices[0] + tout / 1e6 * prices[1], 6)
    out: dict[str, Any] = {
        "by_category": by_letter, "by_source": by_source, "situations": situations, "critical": critical,
        "critical_count": len(critical), "failure_causes": dict(causes.most_common()),
        "tools": {"cases": len(ev), "calls_total": sum(calls), "calls_avg": round(sum(calls) / len(calls), 2) if calls else None,
                  "calls_max": max(calls, default=None),
                  "repeated_calls": sum(any("llamada repetida" in i for i in r.tool_issues) for r in ev),
                  "over_limit": sum(any("llamadas (máximo" in i for i in r.tool_issues) for r in ev),
                  "errors": sum(any("devolvió error" in i for i in r.tool_issues) for r in ev),
                  "wrong_source": sum(bool(r.unexpected_tools) for r in ev)},
        "latency_ms": {"avg": round(sum(r.latency_ms for r in ev) / len(ev), 1) if ev else None,
                       "max": max((r.latency_ms for r in ev), default=None)},
        "tokens": {"input": tin, "output_incl_thinking": tout, "estimated": any(r.tokens_estimated for r in ev),
                   "cost_usd": cost, "cost_note": ("sin precios configurados (--price-in/--price-out)" if prices is None
                                                  else "no se estima coste con tokens estimados" if cost is None else "precios aportados por el usuario")},
        "rag": {"cases": len(rag), "retrieval_ok": sum(bool(r.evidence_ok) for r in rag),
                "citations_checked": sum(r.invention_ok is not None for r in rag),
                "citations_valid": sum(r.invention_ok is True for r in rag)}}
    out["recommendations"] = recommendations(results, out)
    return out


def recommendations(results: "list[CaseResult]", a: dict[str, Any]) -> list[str]:
    """Recomendaciones priorizadas y deterministas a partir de lo medido (no opinión libre)."""
    recs: list[str] = []
    if a["critical"]:
        recs.append(f"CRÍTICO: {len(a['critical'])} fallo(s) de invención o de fuente incorrecta ({', '.join(c['case'] for c in a['critical'])}): "
                    "corregir antes que cualquier otra mejora.")
    if a["tools"]["wrong_source"]:
        recs.append(f"{a['tools']['wrong_source']} caso(s) usaron herramientas fuera de las permitidas: revisar la selección de fuente en el prompt/descripciones MCP.")
    if a["tools"]["errors"]:
        recs.append(f"{a['tools']['errors']} caso(s) con errores de herramienta: comprobar que no se convierten en afirmaciones.")
    reasons = Counter(r.detail for r in results if r.status == "not_evaluable")
    pre = sum(n for d, n in reasons.items() if "requiere LLM real" not in d)
    live = sum(n for d, n in reasons.items() if "requiere LLM real" in d)
    if pre:
        recs.append(f"{pre} caso(s) no evaluables por precondiciones ausentes en la base (ingestas/documentos): completarlas para ampliar la cobertura.")
    if live:
        recs.append(f"{live} caso(s) solo se juzgan con LLM real: ejecutar `--mode live` con presupuesto acotado (requiere autorización).")
    if any(r.status in ("pass", "fail") and r.token_source == "estimated" for r in results):
        recs.append("Los tokens son estimaciones locales: el coste real solo se conoce con una ejecución live.")
    return recs or ["Sin hallazgos que priorizar en esta ejecución."]


def render_audit(a: dict[str, Any], mode: str = "live") -> list[str]:
    """Sección Markdown del informe."""
    c = a["critical"]
    out = ["## Auditoría de fiabilidad (Fase 17)", ""]
    if mode == "offline":
        out += ["> **Modo offline:** comprueba que las herramientas MCP/API devuelven la evidencia correcta y que la ruta de referencia basta para "
                "responder. NO evalúa el texto ni la elección de herramientas de un LLM real: la aprobación no es calidad del modelo.", ""]
    out += [
           f"- **Errores críticos (invención o fuente incorrecta): {len(c)}**" + (" — ninguno" if not c else ""),
           *[f"  - `{x['case']}`: {'; '.join(x['reasons'])}" for x in c], "",
           "### Por categoría", "", "| Categoría | Casos | Pass | Fail | No evaluables | Sin ejecutar | Aprobación (evaluables) |", "|---|---|---|---|---|---|---|"]
    for k, v in a["by_category"].items():
        rate = "—" if v["pass_rate_evaluable_pct"] is None else f"{v['pass_rate_evaluable_pct']} %"
        out.append(f"| {k}. {v['name']} | {v['cases']} | {v['pass']} | {v['fail']} | {v['not_evaluable']} | {v['not_run']} | {rate} |")
    out += ["", "### Situación comprobada (casos evaluables)", "", "| Situación | Pass | Fail |", "|---|---|---|"]
    out += [f"| {EXPECT_LABEL[k]} | {v['pass']} | {v['fail']} |" for k, v in a["situations"].items()]
    out += ["| No evaluable | " + str(sum(v["not_evaluable"] for v in a["by_category"].values())) + " | — |", "",
            "«Sin datos», «No ingerido» y «Error» se aprueban solo si el agente lo reconoce sin inventar ni reconstruir la tabla oficial.", ""]
    t, lat, tok, rag = a["tools"], a["latency_ms"], a["tokens"], a["rag"]
    out += ["### Herramientas, latencia y coste", "",
            f"- Llamadas a tools por consulta: media {t['calls_avg'] if t['calls_avg'] is not None else 'n/d'}, máximo {t['calls_max'] if t['calls_max'] is not None else 'n/d'} "
            f"({t['calls_total']} en {t['cases']} casos) · repetidas: {t['repeated_calls']} · sobre el límite: {t['over_limit']} · con error: {t['errors']} · fuente no permitida: {t['wrong_source']}",
            f"- Latencia por consulta (sin pausas de ritmo): media {lat['avg'] if lat['avg'] is not None else 'n/d'} ms, máxima {lat['max'] if lat['max'] is not None else 'n/d'} ms",
            f"- Tokens: entrada {tok['input']} · salida {tok['output_incl_thinking']} ({'ESTIMADOS' if tok['estimated'] else 'del proveedor'}) · coste: "
            f"{tok['cost_usd'] if tok['cost_usd'] is not None else 'n/d'} ({tok['cost_note']})",
            f"- RAG: recuperación correcta {rag['retrieval_ok']}/{rag['cases']} · citas validadas {rag['citations_valid']}/{rag['citations_checked']}"
            + (" (la validez de citas solo se juzga con texto real del modelo: live)" if rag["citations_checked"] == 0 and rag["cases"] else ""), ""]
    if a["failure_causes"]:
        out += ["### Causas de fallo", "", *[f"- {k}: {n}" for k, n in a["failure_causes"].items()], ""]
    out += ["### Recomendaciones (por prioridad)", "", *[f"{i}. {r}" for i, r in enumerate(a["recommendations"], 1)], ""]
    return out
