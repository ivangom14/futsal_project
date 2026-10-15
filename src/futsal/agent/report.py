"""Informe de evaluación (Markdown + JSON), estimación de cuota y comparación entre ejecuciones. Sin red."""

import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.agent.evaluate import CaseResult, summarize_results

LAYERS = [("datos", "evidence_ok"), ("herramientas", "tools_ok"), ("respuesta", "answer_ok"),
          ("invención", "invention_ok"), ("advertencia", "warning_ok"), ("presentación", "presentation_ok")]
SOURCE_LABEL = {"provider": "REALES (informados por el proveedor)", "estimated": "ESTIMADOS localmente (~3,5 car./token)",
                "mixed": "mezcla de reales y estimados", "unreported": "NO informados por el proveedor"}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,  # noqa: S603, S607
                              timeout=5, check=False).stdout.strip() or "n/d"
    except (OSError, subprocess.TimeoutExpired):
        return "n/d"


def build_meta(*, mode: str, suite: str, provider: str, model: str, args: dict[str, Any],
               preflight: dict[str, Any] | None) -> dict[str, Any]:
    """Metadatos de la ejecución. Nunca incluye claves ni cadenas de conexión."""
    return {"generated_at": datetime.now(UTC).isoformat(), "mode": mode, "suite": suite, "provider": provider,
            "model": model, "commit": git_commit(), "args": args, "preflight": preflight}


def plan_estimate(offline: list[CaseResult], *, selected: int, max_tool_calls: int, retries: int,
                  max_requests: int | None) -> dict[str, Any]:
    """Cuota prevista para una ejecución online. Todo salvo el recuento de casos es ESTIMACIÓN local."""
    ev = [r for r in offline if r.status in ("pass", "fail")]
    typical = sum(r.llm_calls for r in ev)
    worst_calls = len(ev) * (max_tool_calls + 1)
    return {
        "cases_selected": selected, "cases_evaluable": len(ev),
        "not_evaluable": [f"{r.case_id}: {r.detail}" for r in offline if r.status == "not_evaluable"],
        "typical_llm_calls": typical, "max_llm_calls": worst_calls,
        "retries_per_call": retries, "typical_http_requests_no_failures": typical,
        "max_http_requests_worst_case": worst_calls * (1 + retries),
        "request_budget": max_requests,
        "estimated_input_tokens": sum(r.input_tokens for r in ev), "estimated_output_tokens": sum(r.output_tokens for r in ev),
        "tool_ms_local": round(sum(r.tool_ms for r in ev), 1)}


def render_plan(p: dict[str, Any]) -> str:
    lines = [
        f"Casos seleccionados: {p['cases_selected']} (evaluables: {p['cases_evaluable']})",
        f"Llamadas al LLM previstas (ruta de referencia, sin fallos):  {p['typical_llm_calls']}   [estimación: depende de lo que decida el modelo]",
        f"Máximo teórico de llamadas al LLM:                          {p['max_llm_calls']}   [= casos × (MAX_TOOL_CALLS+1); garantizado por el límite del agente]",
        f"Reintentos permitidos por llamada (solo 429/5xx/red):        {p['retries_per_call']}",
        f"Peticiones HTTP máximas en el PEOR caso:                     {p['max_http_requests_worst_case']}   [solo si el proveedor falla siempre]",
        f"Presupuesto duro de peticiones (--max-requests):             {p['request_budget'] if p['request_budget'] is not None else 'sin límite'}"
        "   [la evaluación se detiene al alcanzarlo]",
        f"Tokens previstos: entrada ~{p['estimated_input_tokens']}, salida ~{p['estimated_output_tokens']}   "
        "[ESTIMADOS localmente; Gemini informará los reales, y el razonamiento (thinking) se añade a la salida]",
        "Qué es estimación y qué no: el nº de casos y los límites son exactos; llamadas y tokens son estimaciones."]
    if p["not_evaluable"]:
        lines.append("No evaluables en esta base (no consumen cuota): " + "; ".join(p["not_evaluable"]))
    return "\n".join(lines)


def _yn(v: bool | None) -> str:
    return {True: "✔", False: "✘", None: "—"}[v]


def _prefix_line(p: dict[str, int]) -> str:
    if not p:
        return "n/d"
    return (f"sistema {p.get('system_prompt', 0)} · herramientas {p.get('tools', 0)} · contexto {p.get('context', 0)} "
            f"(total {sum(p.values())})")


def render_markdown(meta: dict[str, Any], results: list[CaseResult]) -> str:
    s = summarize_results(results)
    pre = meta.get("preflight") or {}
    out = [f"# Informe de evaluación del agente ({meta['mode']}, suite {meta['suite']})", "",
           f"- Fecha: {meta['generated_at']}  ·  commit: `{meta['commit']}`",
           f"- Proveedor/modelo: **{meta['provider']} / {meta['model']}**",
           f"- Misma base API = evaluación: **{pre.get('same_database', 'no comprobado')}** — {pre.get('detail', '')}",
           f"- Resultado: **{s['pass']} pass · {s['fail']} fail · {s['not_evaluable']} no evaluables · {s['not_run']} sin ejecutar**",
           f"- Llamadas al LLM: {s['llm_calls']} · reintentos: {s['llm_retries']} · llamadas a tools: {s['tool_calls']}",
           f"- Tokens: entrada {s['input_tokens']} · salida {s['output_tokens']} · razonamiento {s['thinking_tokens']} "
           f"· de la entrada, servidos desde caché {s['cached_tokens']} — origen: {', '.join(SOURCE_LABEL[x] for x in s['token_sources']) or 'n/d'}",
           f"- Latencia (sin pausas de ritmo): total {s['latency_ms']} ms (herramientas {s['tool_ms']} ms) · pausas de ritmo {s.get('paced_ms', 0)} ms",
           f"- Prefijo fijo por llamada (caracteres): {_prefix_line(s.get('prefix_chars') or {})}", "",
           "## Veredictos por capa", "", "| Capa | OK | Fallo |", "|---|---|---|"]
    label = {"invencion": "invención", "presentacion": "presentación"}
    out += [f"| {label.get(name, name)} | {v['pass']} | {v['fail']} |" for name, v in s["layers"].items()]
    out += ["", "Las capas son independientes: **datos** (¿devolvieron las tools la cifra correcta?), **herramientas** "
            "(obligatorias, permitidas, límite, repeticiones), **respuesta** (hechos), **invención** (afirmaciones sin "
            "respaldo), **advertencia** (parcialidad/contradicción) y **presentación** (informativa).", "", "## Casos", "",
            "| Caso | Estado | " + " | ".join(n for n, _ in LAYERS) + " | LLM | in/out |", "|---|---|"
            + "---|" * len(LAYERS) + "---|---|"]
    for r in results:
        tok = f"{r.input_tokens}/{r.output_tokens}" if r.llm_calls else "—"
        out.append(f"| `{r.case_id}` | {r.status} | " + " | ".join(_yn(getattr(r, a)) for _, a in LAYERS)
                   + f" | {r.llm_calls} | {tok} |")
    out += ["", "## Detalle para revisión humana", ""]
    for r in results:
        out += [f"### `{r.case_id}` — {r.status.upper()} ({r.category})", "", f"**Pregunta:** {r.question or '—'}", "",
                f"**Criterio de corrección:** {r.criteria}", ""]
        if r.status in ("not_evaluable", "not_run"):
            out += [f"**Motivo:** {r.detail}", ""]
            continue
        out += [f"**Herramientas:** {', '.join(r.tool_trace) or 'ninguna'}", ""]
        if r.answer:
            out += ["**Respuesta del agente:**", "", *[f"> {ln}" for ln in r.answer.splitlines()], ""]
        issues = r.tool_issues + r.claims + [f"falta advertir: {m}" for m in r.missing_warnings]
        if r.detail:
            out += [f"**Problemas detectados:** {r.detail}", ""]
        elif issues:
            out += ["**Observaciones:** " + "; ".join(issues), ""]
        out += [f"**Consumo:** {r.llm_calls} llamadas LLM ({r.llm_retries} reintentos) · tokens in {r.input_tokens} / out "
                f"{r.output_tokens} / razonamiento {r.thinking_tokens} · caché {r.cached_tokens} · origen: {SOURCE_LABEL.get(r.token_source, r.token_source)}"
                f" · latencia {r.latency_ms} ms (tools {r.tool_ms} ms) · run `{r.run_id}`", ""]
    out += ["## Cómo interpretar este informe", "",
            "- `✔` cumple, `✘` falla, `—` no aplica o no se pudo evaluar de forma fiable (nunca se aprueba por defecto).",
            "- Las comprobaciones de respuesta son criterios léxicos deterministas: acotan la invención pero no sustituyen "
            "la lectura de las respuestas anteriores.",
            "- No contiene claves ni cadenas de conexión. Comparte este informe y `traces.sanitized.jsonl`."]
    return "\n".join(out) + "\n"


def results_payload(meta: dict[str, Any], results: list[CaseResult]) -> dict[str, Any]:
    return {"meta": meta, "mode": meta["mode"], "summary": summarize_results(results),
            "cases": [asdict(r) for r in results]}


def write_report(run_dir: Path, meta: dict[str, Any], results: list[CaseResult]) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {"results": run_dir / "results.json", "report": run_dir / "report.md"}
    paths["results"].write_text(json.dumps(results_payload(meta, results), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["report"].write_text(render_markdown(meta, results), encoding="utf-8")
    return paths


def compare_runs(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Compara dos `results.json` (antes/después): cambios de estado por caso, tokens, llamadas y latencia."""
    a = {c["case_id"]: c for c in before["cases"]}
    b = {c["case_id"]: c for c in after["cases"]}
    lines = [f"{'caso':34}{'antes':15}{'después':15}{'Δ in':>8}{'Δ out':>7}{'Δ LLM':>7}{'Δ ms':>8}"]
    regress = []
    for cid in [*a, *[k for k in b if k not in a]]:
        x, y = a.get(cid), b.get(cid)
        if x is None or y is None:
            lines.append(f"{cid:34}{(x or {}).get('status', '(nuevo)'):15}{(y or {}).get('status', '(ya no existe)'):15}")
            continue
        both = x["status"] in ("pass", "fail") and y["status"] in ("pass", "fail")
        d = (lambda k: y[k] - x[k]) if both else (lambda k: 0)
        lines.append(f"{cid:34}{x['status']:15}{y['status']:15}{d('input_tokens'):>+8}{d('output_tokens'):>+7}"
                     f"{d('llm_calls'):>+7}{d('latency_ms'):>+8.0f}")
        if x["status"] == "pass" and y["status"] == "fail":
            regress.append(cid)
    sa, sb = before["summary"], after["summary"]
    lines.append(f"TOTAL antes: {sa['pass']} pass/{sa['fail']} fail · después: {sb['pass']} pass/{sb['fail']} fail | "
                 f"Δ tokens in {sb['input_tokens'] - sa['input_tokens']:+}, out {sb['output_tokens'] - sa['output_tokens']:+}, "
                 f"Δ llamadas LLM {sb['llm_calls'] - sa['llm_calls']:+}")
    ma, mb = before.get("meta", {}), after.get("meta", {})
    if (ma.get("mode"), ma.get("model")) != (mb.get("mode"), mb.get("model")):
        lines.append(f"AVISO: no son comparables en tokens (antes {ma.get('mode')}/{ma.get('model')}, "
                     f"después {mb.get('mode')}/{mb.get('model')}); las estimaciones locales y los tokens reales no se mezclan.")
    lines.append("REGRESIONES (pass -> fail): " + (", ".join(regress) if regress else "ninguna"))
    return "\n".join(lines)
