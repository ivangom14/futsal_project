"""Detección de regresiones entre dos `results.json` y estado de «listo para revisión». Sin red, sin BD, sin LLM.

Principios: la CALIDAD es una condición independiente de los tokens (menos tokens nunca compensa un fallo); las métricas de
consumo solo se comparan entre ejecuciones comparables (mismo modo, proveedor y modelo, y mismo origen de tokens); lo que no se
puede medir o comparar se dice, no se inventa.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from futsal.agent.trace import sanitize_text

EVALUATED = ("pass", "fail")
# capas cuyo empeoramiento bloquea; «presentación» es solo informativa (ver docs/agent.md)
BLOCKING_LAYERS = (("evidence_ok", "datos"), ("tools_ok", "herramientas"), ("answer_ok", "respuesta"),
                   ("invention_ok", "invención"), ("warning_ok", "advertencia"))
INFO_LAYERS = (("presentation_ok", "presentación"),)
UNAVAILABLE_METRICS = ("tokens de caché de escritura (el proveedor no los informa)",
                       "coste monetario (no se calcula: no hay tarifas verificadas)",
                       "códigos de error del proveedor por llamada (solo reintentos y casos `not_run` por infraestructura)")


@dataclass(frozen=True)
class Thresholds:
    """Umbrales configurables. Defecto = el ruido observado entre ejecuciones reales del MISMO código (Fase 11: 54-61 llamadas,
    145k-171k tokens de entrada, es decir ±8-12 %); un umbral por debajo daría falsas alarmas. `None` = solo informativo."""

    max_input_tokens_pct: float | None = 15.0
    max_llm_calls_pct: float | None = 10.0
    max_tool_calls_pct: float | None = 15.0
    max_latency_pct: float | None = None  # la latencia depende del proveedor (58-105 s con el mismo código): no bloquea
    max_retries_increase: int = 3
    allow_fewer_evaluable: bool = False


@dataclass
class Finding:
    severity: str  # bloqueante | aviso | info
    code: str
    message: str


@dataclass
class Verdict:
    status: str = "SIN_REGRESIONES"  # SIN_REGRESIONES | ATENCION | REGRESION | NO_COMPARABLE
    ready: bool = False
    findings: list[Finding] = field(default_factory=list)
    deltas: dict[str, Any] = field(default_factory=dict)
    preexisting_fails: list[str] = field(default_factory=list)
    new_fails: list[str] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str) -> None:
        self.findings.append(Finding(severity, code, sanitize_text(message)))

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "bloqueante"]


# ----------------------------------------------------------------------------- una sola ejecución
def overall_status(summary: dict[str, Any]) -> tuple[str, str]:
    """Estado global de UNA ejecución. Una ejecución parcial nunca se presenta como superada."""
    p, f, ne, nr = summary["pass"], summary["fail"], summary["not_evaluable"], summary["not_run"]
    if p + f == 0:
        return "SIN CASOS EVALUABLES", f"0 casos evaluados ({ne} no evaluables, {nr} sin ejecutar)"
    if nr:
        return "INCOMPLETA", f"{nr} caso(s) sin ejecutar (presupuesto agotado o error del proveedor): el resultado no es concluyente"
    if f:
        return "CON FALLOS", f"{f} caso(s) fallido(s) de {p + f} evaluados"
    return "SUPERADA", f"{p} de {p} casos evaluados superados ({ne} no evaluables no cuentan como superados)"


def tool_errors(cases: list[dict[str, Any]]) -> int:
    return sum(1 for c in cases for i in c.get("tool_issues") or [] if "devolvió error" in i)


def infra_errors(cases: list[dict[str, Any]]) -> int:
    return sum(1 for c in cases if c["status"] == "not_run" and "infraestructura" in (c.get("detail") or ""))


# ----------------------------------------------------------------------------- comparación
def _pct(before: float, after: float) -> float | None:
    return None if before <= 0 else (after - before) / before * 100


def _sum(cases: list[dict[str, Any]], key: str) -> float:
    return sum(c.get(key) or 0 for c in cases)


def _comparable(ma: dict[str, Any], mb: dict[str, Any]) -> list[str]:
    why = []
    for key, label in (("mode", "modo"), ("provider", "proveedor"), ("model", "modelo")):
        if ma.get(key) != mb.get(key):
            why.append(f"{label}: {ma.get(key)} ≠ {mb.get(key)}")
    return why


def compare(before: dict[str, Any], after: dict[str, Any], th: Thresholds | None = None) -> Verdict:  # noqa: C901
    th = th or Thresholds()
    v = Verdict()
    a = {c["case_id"]: c for c in before["cases"]}
    b = {c["case_id"]: c for c in after["cases"]}
    ma, mb = before.get("meta", {}), after.get("meta", {})
    sa, sb = before["summary"], after["summary"]
    common = [k for k in a if k in b]
    if not common:
        v.status = "NO_COMPARABLE"
        v.add("aviso", "sin_casos_comunes", "las dos ejecuciones no comparten ningún caso")
        return _finish(v, after)
    why = _comparable(ma, mb)
    quality_comparable = not why
    if why:
        v.status = "NO_COMPARABLE"
        v.add("aviso", "no_comparable", "no son comparables (" + "; ".join(why) + "): ni la calidad ni los tokens se contrastan; "
              "las estimaciones locales y los tokens reales nunca se mezclan")
    for cid in b:
        if cid not in a:
            v.add("info", "caso_nuevo", f"caso nuevo respecto a la referencia: {cid}")
    for cid in a:
        if cid not in b:
            v.add("aviso", "caso_ausente", f"caso de la referencia ausente ahora: {cid} (otra suite, --only o --limit)")
    if ma.get("suite_version") and mb.get("suite_version") and ma["suite_version"] != mb["suite_version"]:
        v.add("aviso", "suite_distinta", f"versión de suite distinta ({ma['suite_version']} → {mb['suite_version']}): "
              "se comparan solo los casos comunes")
    # --- calidad (condición independiente de los tokens) ---
    v.preexisting_fails = sorted(k for k in common if a[k]["status"] == "fail" and b[k]["status"] == "fail")
    v.new_fails = sorted(k for k in b if b[k]["status"] == "fail" and (k not in a or a[k]["status"] != "fail"))
    if quality_comparable:
        for k in common:
            x, y = a[k], b[k]
            if x["status"] == "pass" and y["status"] == "fail":
                v.add("bloqueante", "regresion_funcional", f"{k}: pass → fail ({(y.get('detail') or '')[:120]})")
            elif x["status"] in EVALUATED and y["status"] in ("not_evaluable", "not_run"):
                v.add("bloqueante", "perdida_cobertura", f"{k}: {x['status']} → {y['status']} ({(y.get('detail') or '')[:100]})")
            elif x["status"] == "not_evaluable" and y["status"] in EVALUATED:
                v.add("info", "ahora_evaluable", f"{k}: no evaluable → {y['status']}")
            for attr, label in BLOCKING_LAYERS:
                if x["status"] in EVALUATED and y["status"] in EVALUATED and x.get(attr) is True and y.get(attr) is False \
                        and not (x["status"] == "pass" and y["status"] == "fail"):
                    v.add("bloqueante", f"degradacion_{label}", f"{k}: la capa {label} pasa de ✔ a ✘")
            for attr, label in INFO_LAYERS:
                if x.get(attr) is True and y.get(attr) is False:
                    v.add("aviso", f"degradacion_{label}", f"{k}: la capa {label} (informativa) pasa de ✔ a ✘")
        evaluable_a, evaluable_b = sa["pass"] + sa["fail"], sb["pass"] + sb["fail"]
        if evaluable_b < evaluable_a and not th.allow_fewer_evaluable and len(a) == len(b) and set(a) == set(b):
            v.add("bloqueante", "menos_evaluables", f"casos evaluables {evaluable_a} → {evaluable_b}")
        elif evaluable_b != evaluable_a:
            v.add("info", "evaluables_distintos", f"casos evaluables {evaluable_a} → {evaluable_b}")
        # errores de herramientas y del proveedor
        ta, tb = tool_errors(list(a.values())), tool_errors(list(b.values()))
        if tb > ta:
            v.add("bloqueante", "errores_herramientas", f"errores de herramientas MCP {ta} → {tb}")
        ia, ib = infra_errors(list(a.values())), infra_errors(list(b.values()))
        if ib > ia:
            v.add("aviso", "errores_proveedor", f"ejecuciones cortadas por el proveedor {ia} → {ib}: el resultado es parcial")
        ra, rb = sa["llm_retries"], sb["llm_retries"]
        if rb - ra > th.max_retries_increase:
            v.add("aviso", "mas_reintentos", f"reintentos {ra} → {rb} (umbral +{th.max_retries_increase})")
    # --- consumo y latencia (solo entre casos comunes evaluados en ambas y ejecuciones comparables) ---
    both = [k for k in common if a[k]["status"] in EVALUATED and b[k]["status"] in EVALUATED]
    sources = {x["token_source"] for k in both for x in (a[k], b[k]) if x.get("llm_calls")}
    if quality_comparable and both:
        mixed = len(sources) > 1 or "mixed" in sources
        if mixed:
            v.add("aviso", "tokens_no_comparables", f"origen de tokens distinto o mezclado ({sorted(sources)}): "
                  "los tokens no se comparan")
        va = [a[k] for k in both]
        vb = [b[k] for k in both]
        for key, label, limit, block in (("input_tokens", "tokens de entrada", th.max_input_tokens_pct, True),
                                         ("llm_calls", "llamadas LLM", th.max_llm_calls_pct, True),
                                         ("tool_calls", "llamadas MCP", th.max_tool_calls_pct, True),
                                         ("latency_ms", "latencia (sin pausas)", th.max_latency_pct, False)):
            if key == "input_tokens" and mixed:
                continue
            x, y = _sum(va, key), _sum(vb, key)
            pct = _pct(x, y)
            v.deltas[key] = {"before": x, "after": y, "pct": None if pct is None else round(pct, 1)}
            if pct is not None and limit is not None and pct > limit:
                v.add("bloqueante" if block else "aviso", f"incremento_{key}",
                      f"{label}: {x:.0f} → {y:.0f} ({pct:+.1f} % > umbral {limit:.0f} %)")
            elif pct is not None and limit is None and pct > 25:
                v.add("info", f"incremento_{key}", f"{label}: {x:.0f} → {y:.0f} ({pct:+.1f} %; sin umbral configurado)")
        v.deltas["cached_tokens"] = {"before": _sum(va, "cached_tokens"), "after": _sum(vb, "cached_tokens")}
        v.deltas["cases_compared"] = len(both)
    elif quality_comparable:
        v.add("aviso", "sin_casos_evaluados_comunes", "ningún caso evaluado en ambas ejecuciones: no hay consumo que comparar")
    return _finish(v, after)


def _finish(v: Verdict, after: dict[str, Any]) -> Verdict:
    if v.status != "NO_COMPARABLE":
        v.status = "REGRESION" if v.blocking() else ("ATENCION" if any(f.severity == "aviso" for f in v.findings)
                                                      else "SIN_REGRESIONES")
    s = after["summary"]
    state, _ = overall_status(s)
    # sin referencia comparable no se afirma «lista»: la decisión es manual (la calidad absoluta se informa aparte)
    v.ready = state in ("SUPERADA", "CON FALLOS") and v.status != "NO_COMPARABLE" and not v.blocking() and not v.new_fails
    return v


def render_verdict(v: Verdict, after: dict[str, Any], before_ref: str = "") -> str:
    state, why = overall_status(after["summary"])
    ready_txt = ("SIN REFERENCIA COMPARABLE: no se emite decisión automática" if v.status == "NO_COMPARABLE"
                 else "LISTA PARA REVISIÓN" if v.ready else "NO LISTA PARA REVISIÓN")
    lines = [f"Referencia: {before_ref or 'n/d'}",
             f"Calidad de esta ejecución: {state} — {why}",
             f"Veredicto de la comparación: {v.status} · {ready_txt}"]
    if v.preexisting_fails:
        lines.append("Fallos que ya existían en la referencia (no son regresión): " + ", ".join(v.preexisting_fails))
    if v.new_fails:
        lines.append("Fallos NUEVOS: " + ", ".join(v.new_fails))
    for sev, title in (("bloqueante", "REGRESIONES / BLOQUEOS"), ("aviso", "AVISOS"), ("info", "NOTAS")):
        items = [f for f in v.findings if f.severity == sev]
        if items:
            lines.append(f"{title}:")
            lines += [f"  - [{f.code}] {f.message}" for f in items]
    if v.deltas.get("cases_compared"):
        d = v.deltas
        parts = [f"{k}: {d[k]['before']:.0f} → {d[k]['after']:.0f}" + (f" ({d[k]['pct']:+.1f} %)" if d[k]["pct"] is not None else "")
                 for k in ("input_tokens", "llm_calls", "tool_calls", "latency_ms") if k in d]
        lines.append(f"Consumo en los {d['cases_compared']} casos comunes evaluados: " + " · ".join(parts))
    lines.append("Condición independiente: menos tokens NO compensa un fallo; la mejora global solo se afirma con calidad sin regresiones.")
    lines.append("No disponible: " + "; ".join(UNAVAILABLE_METRICS) + ".")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- historial
def list_runs(report_dir: Path) -> list[dict[str, Any]]:
    """Resumen de las ejecuciones guardadas (una por subcarpeta con results.json). Ignora las ilegibles."""
    rows: list[dict[str, Any]] = []
    for f in sorted(report_dir.glob("*/results.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            m, s = d.get("meta", {}), d["summary"]
        except (OSError, ValueError, KeyError):
            continue
        state, _ = overall_status(s)
        rows.append({"dir": f.parent.name, "date": str(m.get("generated_at", ""))[:19], "commit": m.get("commit", "n/d"),
                     "dirty": m.get("commit_dirty"), "mode": d.get("mode", "?"), "suite": m.get("suite", "?"),
                     "model": m.get("model", "?"), "state": state, "pass": s["pass"], "fail": s["fail"],
                     "ne": s["not_evaluable"], "not_run": s["not_run"], "in": s["input_tokens"], "calls": s["llm_calls"],
                     "source": ",".join(s.get("token_sources", [])) or "n/d"})
    return rows


def render_runs(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No hay ejecuciones guardadas."
    head = f"{'carpeta':36}{'commit':11}{'modo':8}{'suite':7}{'estado':20}{'ok/fail/ne/nr':16}{'in':>9}{'LLM':>5}  origen tokens"
    out = [head]
    for r in rows:
        commit = f"{r['commit']}{'*' if r['dirty'] else ''}"
        out.append(f"{r['dir']:36}{commit:11}{r['mode']:8}{r['suite']:7}{r['state']:20}"
                   f"{r['pass']}/{r['fail']}/{r['ne']}/{r['not_run']:<9}{r['in']:>9}{r['calls']:>5}  {r['source']}")
    out.append("(* = árbol de trabajo con cambios sin commit; origen de tokens: provider = reales, estimated = estimados)")
    return "\n".join(out)
