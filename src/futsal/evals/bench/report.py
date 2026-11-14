"""Informe del benchmark: `summary.json` (todo lo calculado) y `report.md` (rankings, Pareto, categorías, fallos, incertidumbre)."""

import json
from pathlib import Path
from typing import Any

from futsal.evals.bench.analysis import CLASSES, DEFAULT_GAP, PROJECTIONS, analyze
from futsal.evals.bench.runner import load_run

SUMMARY, REPORT = "summary.json", "report.md"
FAILURES_SHOWN = 12


def _pct(rate: dict[str, Any] | None) -> str:
    if not rate or rate.get("rate") is None:
        return "—"
    lo, hi = rate["wilson95"]
    return f"{rate['rate'] * 100:.1f} % [{lo * 100:.0f}–{hi * 100:.0f}] (n={rate['n']})"


def _usd(v: Any, digits: int = 4) -> str:
    """Importes en USD legibles también cuando son diminutos (una pregunta puede costar 0,0001 $)."""
    if v is None:
        return "n/d"
    x = float(v)
    if x == 0:
        return "0 $"
    return f"{x:.{digits}f} $" if abs(x) >= 0.01 else f"{x:.3g} $"


def _ms(v: Any) -> str:
    return "n/d" if v is None else f"{float(v) / 1000:.2f} s"


def render(a: dict[str, Any]) -> str:
    meta, stats, cmp_ = a["meta"], a["stats"], a["comparison"]
    snap = meta["snapshot"]
    L = [f"# Benchmark multimodelo · {meta['run_id']}", "",
         (f"- Suite `{meta['suite']['name']}` v{meta['suite']['version']} · {len(meta['case_ids'])} casos · {meta['reps']} repetición(es) · "
          f"agente **{meta.get('agent', {}).get('arch', 'legacy')}** (contexto `{meta.get('agent', {}).get('context_mode', 'legacy')}`, "
          f"banderas `{meta['arm']}`) · semilla {meta['seed']}"),
         f"- Snapshot `{snap['id']}` (as_of {snap['as_of']}, huella `{snap['fingerprint'][:12]}`) · commit `{meta['commit'][:12]}`",
         f"- Inicio {meta['started_at']} · fin {meta.get('finished_at')} · orden de modelos {', '.join(meta['model_order'])}",
         (f"- Gasto calculado {_usd(meta.get('budget', {}).get('spent_usd_calculated'), 4)} de un tope de "
          f"{_usd(meta.get('budget', {}).get('max_usd'), 2)} (calculado con tarifas; no es lo facturado)"), ""]
    if a["warnings"]:
        L += ["## Avisos", ""] + [f"- **{w}**" for w in a["warnings"]] + [""]
    L += ["## Modelos", "", "| id | proveedor/modelo | versión | tarifa entrada/caché/salida (USD/1M) | fuente y fecha | estado |", "|---|---|---|---|---|---|"]
    for mid, m in meta["models"].items():
        p = m["pricing"]
        st = meta.get("models_status", {}).get(mid, {})
        L.append(f"| {mid} | {m['provider']}/{m['model']} | {m['version']} | {p['input']} / {p['cached_input']} / {p['output']}"
                 f"{' · plan gratuito' if m['free_tier'] else ''} | {p['source']} ({p['checked_at'] or 'sin fecha'}) | {st.get('status', 'sin ejecutar')} |")
    L += ["", "## A. Ranking de calidad (exactitud y fiabilidad)", "",
          "Exactitud = correctas / evaluables, IC de Wilson al 95 %. Alucinación = cifra, nombre o dato de otra competición sin respaldo.", "",
          "| # | modelo | exactitud | correctas+parciales | alucinaciones | encadenadas completas | " + " | ".join(CLASSES) + " |",
          "|---|---|---|---|---|---|" + "---|" * len(CLASSES)]
    for i, mid in enumerate(cmp_["rank_quality"], 1):
        q = stats[mid]["quality"]
        L.append(f"| {i} | {mid} | {_pct(q['accuracy'])} | {_pct(q['acceptable'])} | {_pct(q['hallucination'])} | "
                 f"{_pct(stats[mid]['chained_conversations'])} | " + " | ".join(str(q["classes"][c]) for c in CLASSES) + " |")
    if cmp_["pairs"]:
        L += ["", "Diferencias pareadas de exactitud (mismos turnos; bootstrap, IC 95 %):", ""]
        seen: set[frozenset[str]] = set()
        for key, p in cmp_["pairs"].items():
            x, y = key.split("|")
            if frozenset((x, y)) in seen:
                continue
            seen.add(frozenset((x, y)))
            ci = f"[{p['ci95'][0] * 100:+.1f}, {p['ci95'][1] * 100:+.1f}]" if p["ci95"] else "—"
            L.append(f"- {x} − {y}: {(p['diff'] or 0) * 100:+.1f} puntos {ci} sobre {p['n']} turnos → **{p['verdict'].replace('A ', x + ' ').replace('B ', y + ' ')}**")
    L += ["", "## B. Ranking económico (coste por pregunta correctamente resuelta)", "",
          "| # | modelo | por pregunta correcta | media / mediana / p95 por pregunta | por conversación | por conversación encadenada | total ejecución | total sin caché | base |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, mid in enumerate(cmp_["rank_economic"], 1):
        c = stats[mid]["cost"]
        pq = c["per_question"]
        L.append(f"| {i} | {mid} | {_usd(c['per_correct'])} | {_usd(pq['mean'])} / {_usd(pq['median'])} / {_usd(pq['p95'])} | "
                 f"{_usd(c['per_conversation']['mean'])} | {_usd(c['per_chained_conversation']['mean'])} | {_usd(c['total_usd'], 4)} | "
                 f"{_usd(c['total_usd_no_cache'], 4)} | {c['basis']} |")
    L += ["", "Proyección (media medida × N; rango = IC 95 % de la media):", "",
          "| modelo | " + " | ".join(f"{n:,} preguntas".replace(",", ".") for n in PROJECTIONS) + " |", "|---|" + "---|" * len(PROJECTIONS)]
    for mid in cmp_["rank_economic"]:
        pr = stats[mid]["cost"]["projections"]
        L.append(f"| {mid} | " + " | ".join(
            f"{_usd(pr[str(n)]['usd'], 2)}" + (f" [{pr[str(n)]['range95'][0]:.2f}–{pr[str(n)]['range95'][1]:.2f}]" if pr[str(n)]["range95"] else "")
            for n in PROJECTIONS) + " |")
    if stats:
        first = next(iter(stats.values()))["cost"]
        L += ["", "Supuestos: " + "; ".join(first["projection_assumptions"]) + "."]
    L += ["", "## C. Ranking de velocidad", "", "| # | modelo | p50 | p95 | media | llamadas LLM / MCP por pregunta | reintentos | errores de herramienta |",
          "|---|---|---|---|---|---|---|---|"]
    for i, mid in enumerate(cmp_["rank_speed"], 1):
        lt, u = stats[mid]["latency"], stats[mid]["usage"]
        L.append(f"| {i} | {mid} | {_ms(lt['p50_ms'])} | {_ms(lt['p95_ms'])} | {_ms(lt['mean_ms'])} | "
                 f"{u['llm_calls_per_question'] or 0:.2f} / {u['mcp_calls_per_question'] or 0:.2f} | {u['retries']} | {u['tool_errors']} |")
    L += ["", "## D. Calidad / precio", "", "| modelo | exactitud | coste por pregunta | coste por correcta | p50 | en la frontera de Pareto |",
          "|---|---|---|---|---|---|"]
    for mid in cmp_["rank_quality"]:
        s = stats[mid]
        L.append(f"| {mid} | {_pct(s['quality']['accuracy'])} | {_usd(s['cost']['per_question']['mean'])} | {_usd(s['cost']['per_correct'])} | "
                 f"{_ms(s['latency']['p50_ms'])} | {'sí' if mid in cmp_['pareto'] else 'no (dominado)'} |")
    L += ["", (f"Frontera de Pareto (exactitud ↑, coste ↓, latencia ↓): **{', '.join(cmp_['pareto']) or '—'}**. Sin puntuación única: elige "
               "en la frontera según lo que pese más.")]
    L += ["", "## Por categoría", "", "| categoría | " + " | ".join(cmp_["rank_quality"]) + " |", "|---|" + "---|" * len(cmp_["rank_quality"])]
    cats = sorted({c for s in stats.values() for c in s["by_category"]})
    for cat in cats:
        cells = []
        for mid in cmp_["rank_quality"]:
            q = stats[mid]["by_category"].get(cat)
            cost = stats[mid]["cost"]["by_category"].get(cat, {}).get("mean")
            cells.append("—" if not q or q["accuracy"]["rate"] is None else
                         f"{q['accuracy']['k']}/{q['accuracy']['n']} · halluc. {q['classes']['alucinacion']} · {_usd(cost)}")
        L.append(f"| {cat} | " + " | ".join(cells) + " |")
    L += ["", "## Recomendación (determinista, conservadora)", ""]
    recs = cmp_["recommendations"]
    if "best_overall" in recs:
        b = recs["best_overall"]
        L.append(f"- **Mejor modelo general:** {b['model']}" + (f" (empatado con {', '.join(b['tied_with'])})" if b["tied_with"] else "")
                 + f" — {b['evidence']}.")
        ch = recs["cheapest_acceptable"]
        L.append(f"- **Más económico con calidad aceptable:** {ch['model'] or 'ninguno cumple'} — {ch['criterion']}.")
        for key, label in (("best_complex", "preguntas complejas"), ("best_simple", "consultas sencillas")):
            if key in recs:
                r = recs[key]
                L.append(f"- **Mejor para {label}:** {r['model']} ({_pct(r['accuracy'])})" + (" · muestra pequeña" if r["small_sample"] else ""))
    rt = recs.get("router", {})
    if rt.get("applicable"):
        parts = ", ".join(f"{cx} → {c['model']} ({c['share'] * 100:.0f} % del tráfico)" for cx, c in rt["choice"].items())
        saving = "n/d" if rt["saving_vs_reference"] is None else f"{rt['saving_vs_reference'] * 100:.0f} %"
        L += [(f"- **Router híbrido (cota):** {parts}. Exactitud estimada {rt['estimated_accuracy'] * 100:.1f} %, coste "
               f"{_usd(rt['estimated_cost_per_question'])} por pregunta frente a {_usd(rt['reference_cost_per_question'])} de {rt['reference_model']} "
               f"→ ahorro {saving}. "
               f"{'Puede compensar' if rt['worth_it'] else 'No compensa con estos datos'} ({'; '.join(rt['caveats'])}).")]
    else:
        L.append(f"- Router híbrido: no aplicable ({rt.get('reason')}).")
    L += [("- Ninguna recomendación se basa en diferencias que el IC pareado no demuestre; con muestras pequeñas, repite con más "
           "repeticiones antes de cambiar el modelo predeterminado.")]
    L += ["", "## Fallos relevantes", ""]
    for mid in cmp_["rank_quality"]:
        bad = [r for r in a["rows"][mid] if r["class"] in ("alucinacion", "incorrecta", "error_tecnico")]
        bad.sort(key=lambda r: ("alucinacion", "incorrecta", "error_tecnico").index(r["class"]))
        L.append(f"### {mid} ({len(bad)})")
        for r in bad[:FAILURES_SHOWN]:
            L.append(f"- `{r['case_id']}#{r['turn'] + 1}` r{r['rep'] + 1} [{r['class']}] «{r['question'][:90]}» → {str(r['reason'])[:160]}")
        if len(bad) > FAILURES_SHOWN:
            L.append(f"- … y {len(bad) - FAILURES_SHOWN} más en summary.json")
        L.append("")
    L += ["## Cómo leer este informe", "",
          "- Los veredictos salen del calificador determinista (verdad SQL del snapshot); la evaluación ciega de Opus es complementaria.",
          ("- Los costes son calculados con la tarifa congelada en bench.json y los tokens informados por el proveedor (base `provider`); "
           "`estimated` = tokens estimados; `unavailable` = no calculable. No son importes facturados."),
          ("- Las diferencias atribuibles al proveedor (formato de la API, adaptador) y no al modelo se ven como errores técnicos o reintentos; "
           "revisa esas columnas antes de achacarlas a la calidad."), ""]
    return "\n".join(L)


def build_report(run_dir: Path, gap: float = DEFAULT_GAP) -> dict[str, Any]:
    meta, results = load_run(run_dir)
    a = analyze(meta, results, gap)
    summary = {k: v for k, v in a.items() if k != "rows"}
    summary["turns"] = a["rows"]
    (run_dir / SUMMARY).write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    (run_dir / REPORT).write_text(render(a), encoding="utf-8")
    return a
