"""Resumen de una comparación controlada entre N brazos (Fase E0). Sin LLM, sin red, sin base de datos.

Generaliza `ab_summary` (que compara solo legacy/compact y NO se toca: la comparación de la Fase 1 depende de él) reutilizando sus piezas.
Lee `<brazo>-r<n>.json` (los que deja `scripts/ab_arms.py`), conserva cada repetición por separado y compara cada brazo con el de referencia.

Naturaleza de los datos en el informe: todo lo que sale de los `results.json` es MEDIDO (resultado de cada caso, llamadas LLM y MCP, tokens del
proveedor cuando `token_source` es `provider`); cuando los tokens son estimados se marcan como tales. Aquí no hay simulaciones.

Uso: `python -m futsal.agent.arms_summary data\\agent\\ab-arms [--baseline legacy] [--json salida.json] [--md salida.md]`
"""

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

from futsal.agent.ab_summary import DECISIVE, MIN_GAP, case_record
from futsal.agent.arms import ARMS, BASELINE, arm_env

FILE = re.compile(r"^([a-z][a-z0-9_]*)-r(\d+)\.json$")


def load_arms(folder: Path) -> dict[str, dict[int, dict[str, Any]]]:
    arms: dict[str, dict[int, dict[str, Any]]] = {}
    for path in sorted(folder.iterdir()):
        m = FILE.match(path.name)
        if m:
            arms.setdefault(m.group(1), {})[int(m.group(2))] = json.loads(path.read_text(encoding="utf-8"))
    return arms


def check_flags(arm: str, doc: dict[str, Any]) -> str | None:
    """Comprueba que la tanda se hizo con las banderas de su brazo (`meta.agent_flags`). Sin ese dato (ejecuciones anteriores a E0) no se puede
    comprobar y se avisa; no se da por buena."""
    flags = (doc.get("meta") or {}).get("agent_flags")
    if flags is None:
        return f"brazo {arm}: la ejecución no registra agent_flags (anterior a E0): no se puede comprobar su configuración"
    if arm not in ARMS:
        return None
    expected = arm_env(arm)
    diff = {k: (flags.get(k), v) for k, v in expected.items() if flags.get(k) != v}
    return f"brazo {arm}: banderas distintas de las esperadas (registrada, esperada): {diff}" if diff else None


def summarize(arms: dict[str, dict[int, dict[str, Any]]], baseline: str | None = None) -> dict[str, Any]:
    names = sorted(arms, key=lambda a: (a != BASELINE, a))
    base = baseline or (BASELINE if BASELINE in arms else (names[0] if names else ""))
    warnings = [w for a in names for doc in arms[a].values() if (w := check_flags(a, doc))]
    warnings = list(dict.fromkeys(warnings))
    order: list[str] = []
    per: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    for a in names:
        for rep, doc in sorted(arms[a].items()):
            for c in doc.get("cases", []):
                if c["case_id"] not in per:
                    order.append(c["case_id"])
                per.setdefault(c["case_id"], {x: {} for x in names})[a][rep] = case_record(c)
    cases = []
    for cid in order:
        entry: dict[str, Any] = {"case": cid, "arms": {}, "verdicts": {}}
        for a in names:
            reps = per[cid][a]
            dec = [r["status"] for r in reps.values() if r["status"] in DECISIVE]
            passes = dec.count("pass")
            entry["arms"][a] = {
                "reps": {str(k): v for k, v in sorted(reps.items())}, "decisive": len(dec), "passes": passes,
                "rate": passes / len(dec) if dec else None, "flaky": 0 < passes < len(dec),
                "tool_sets": sorted({",".join(sorted(set(r["tools"]))) for r in reps.values()}),
                "reasons": sorted({x for r in reps.values() for x in r["reasons"]}),
                "mean_llm_calls": round(mean([r["llm_calls"] for r in reps.values()]), 2) if reps else None,
                "mean_input_tokens": round(mean([r["input_tokens"] or 0 for r in reps.values()]), 1) if reps else None}
        for a in names:
            if a == base:
                continue
            b, x = entry["arms"].get(base), entry["arms"][a]
            if not b or b["rate"] is None or x["rate"] is None:
                entry["verdicts"][a] = "sin datos"
            elif x["rate"] - b["rate"] <= -MIN_GAP:
                entry["verdicts"][a] = "regresión consistente"
            elif x["rate"] - b["rate"] >= MIN_GAP:
                entry["verdicts"][a] = "mejora consistente"
            elif b["flaky"] or x["flaky"]:
                entry["verdicts"][a] = "variable (ruido)"
            else:
                entry["verdicts"][a] = "igual"
        cases.append(entry)
    totals: dict[str, Any] = {}
    for a in names:
        by_rep: dict[str, Any] = {}
        for rep, doc in sorted(arms[a].items()):
            recs = [case_record(c) for c in doc.get("cases", [])]
            dec = [r for r in recs if r["status"] in DECISIVE]
            real = [r for r in dec if r["token_source"] == "provider"]
            calls = sum(r["llm_calls"] for r in dec)
            by_rep[str(rep)] = {"decisive": len(dec), "pass": sum(r["status"] == "pass" for r in dec), "llm_calls": calls,
                                "mcp_calls": sum(r["mcp_calls"] for r in dec), "input_tokens": sum(r["input_tokens"] or 0 for r in real),
                                "output_tokens": sum(r["output_tokens"] or 0 for r in real), "tokens_all_from_provider": len(real) == len(dec),
                                "input_per_llm_call": round(sum(r["input_tokens"] or 0 for r in real) / calls, 1) if calls else None}
        totals[a] = {"runs": by_rep, "flaky_cases": sum(1 for c in cases if c["arms"][a]["flaky"]),
                     "mean_pass": round(mean([r["pass"] for r in by_rep.values()]), 2) if by_rep else None,
                     "mean_llm_calls": round(mean([r["llm_calls"] for r in by_rep.values()]), 1) if by_rep else None,
                     "mean_input_per_llm_call": round(mean([r["input_per_llm_call"] for r in by_rep.values() if r["input_per_llm_call"]]), 1)
                     if any(r["input_per_llm_call"] for r in by_rep.values()) else None}
    verdict_names = ("regresión consistente", "mejora consistente", "variable (ruido)", "igual", "sin datos")
    return {"baseline": base, "arms": names, "cases": cases, "totals": totals, "warnings": warnings,
            "verdicts": {a: {v: sum(c["verdicts"].get(a) == v for c in cases) for v in verdict_names} for a in names if a != base},
            "nature": {"medido": "resultado de cada caso, llamadas LLM y MCP, tokens informados por el proveedor",
                       "estimado": "tokens marcados como estimados (token_source distinto de provider)", "simulado": "nada: este informe no simula"}}


def render(s: dict[str, Any]) -> str:
    arms = s["arms"]
    lines = [f"# Comparación controlada entre brazos: {', '.join(arms)} (referencia: {s['baseline']})", "",
             "Naturaleza de los datos: **MEDIDO** (resultados, llamadas LLM y MCP, tokens del proveedor). Los tokens que no vengan del proveedor se marcan "
             "«(¡estimados!)». Nada de este informe es simulado.", ""]
    lines += [f"AVISO: {w}" for w in s["warnings"]]
    lines += ["", "## Por brazo y repetición (casos decisivos = pass/fail)", "| brazo | rep | casos | pass | llamadas LLM | llamadas MCP | tokens entrada | tokens salida | entrada/llamada |",
              "|---|---|---|---|---|---|---|---|---|"]
    for a in arms:
        for rep, r in s["totals"][a]["runs"].items():
            real = "" if r["tokens_all_from_provider"] else " (¡estimados!)"
            lines.append(f"| {a} | {rep} | {r['decisive']} | {r['pass']} | {r['llm_calls']} | {r['mcp_calls']} | {r['input_tokens']}{real} | {r['output_tokens']} | {r['input_per_llm_call']} |")
    lines += ["", "Variabilidad (casos con resultado distinto entre repeticiones del MISMO brazo) · media de aciertos · media de llamadas LLM por repetición:"]
    for a in arms:
        t = s["totals"][a]
        lines.append(f"- {a}: {t['flaky_cases']} variables · {t['mean_pass']} aciertos · {t['mean_llm_calls']} llamadas LLM · {t['mean_input_per_llm_call']} tokens de entrada por llamada")
    for a, v in s["verdicts"].items():
        lines.append(f"Veredictos de {a} frente a {s['baseline']}: " + ", ".join(f"{k}: {n}" for k, n in v.items()))
    lines += ["", "## Por caso (P = pass, F = fail, · = no decisivo)", "| caso | " + " | ".join(arms) + " | veredictos |", "|---|" + "---|" * (len(arms) + 1)]
    for c in s["cases"]:
        def cell(a: str, c: dict[str, Any] = c) -> str:
            d = c["arms"][a]
            seq = " ".join({"pass": "P", "fail": "F"}.get(r["status"], "·") for r in d["reps"].values())
            return f"{seq} ({d['passes']}/{d['decisive']})" if d["decisive"] else seq
        verdicts = "; ".join(f"{a}: {v}" for a, v in c["verdicts"].items())
        lines.append(f"| `{c['case']}` | " + " | ".join(cell(a) for a in arms) + f" | {verdicts} |")
    lines += ["", "## Detalle por caso y repetición (herramientas · llamadas LLM/MCP · tokens entrada/salida · motivo del fallo)"]
    for c in s["cases"]:
        lines.append(f"\n### `{c['case']}` — " + "; ".join(f"{a}: {v}" for a, v in c["verdicts"].items()))
        for a in arms:
            for rep, r in c["arms"][a]["reps"].items():
                tok = f"{r['input_tokens']}/{r['output_tokens']}" + ("" if r["token_source"] == "provider" else " (estimado)")
                why = f" · {'; '.join(r['reasons'])}" if r["reasons"] else ""
                lines.append(f"- {a} r{rep}: {r['status']} · {', '.join(r['tools']) or '—'} · LLM {r['llm_calls']} / MCP {r['mcp_calls']} · {tok}{why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resume una comparación controlada entre N brazos (sin LLM).")
    ap.add_argument("folder")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--json")
    ap.add_argument("--md")
    args = ap.parse_args(argv)
    summary = summarize(load_arms(Path(args.folder)), args.baseline)
    text = render(summary)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
