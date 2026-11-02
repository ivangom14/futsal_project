"""Resumen de una comparación controlada `legacy` / `compact` con varias repeticiones por modo (Fase 1). Sin LLM, sin red, sin base de datos.

Lee los `results.json` que deja `scripts/ab_prompt_modes.py` (`<modo>-r<n>.json`) y NO mezcla repeticiones: conserva por caso y por repetición el
resultado, las herramientas, las llamadas LLM y MCP, los tokens y el motivo del fallo, para poder comparar la VARIABILIDAD de cada modo y no solo
el agregado. Los tokens son los que informa el proveedor cuando `token_source` es `provider`; si no, se marcan como estimados.

Uso: `python -m futsal.agent.ab_summary data\\agent\\ab [--json salida.json] [--md salida.md]`
"""

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

from futsal.agent.prompts import COMPACT, LEGACY, MODES

FILE = re.compile(r"^(legacy|compact)-r(\d+)\.json$")
LAYERS = (("evidence_ok", "datos"), ("tools_ok", "herramientas"), ("answer_ok", "respuesta"), ("invention_ok", "invención"),
          ("warning_ok", "advertencia"), ("provenance_ok", "procedencia"))
DECISIVE = ("pass", "fail")  # no_evaluable y not_run no cuentan ni como acierto ni como fallo
MIN_GAP = 0.5  # diferencia mínima de tasa de acierto entre modos para llamarla regresión o mejora consistente (con 3 repeticiones: ≥2 de 3 frente a ≤1 de 3)


def _reasons(case: dict[str, Any]) -> list[str]:
    out = [f"capa {name}" for key, name in LAYERS if case.get(key) is False]
    if case.get("detail"):
        out.append(str(case["detail"])[:160])
    out += [str(i)[:120] for i in case.get("tool_issues") or []]
    out += [f"herramienta no esperada {t}" for t in case.get("unexpected_tools") or []]
    out += [str(c)[:120] for c in case.get("claims") or []]
    return list(dict.fromkeys(out))


def load_runs(folder: Path) -> dict[str, dict[int, dict[str, Any]]]:
    runs: dict[str, dict[int, dict[str, Any]]] = {m: {} for m in MODES}
    for path in sorted(folder.iterdir()):
        m = FILE.match(path.name)
        if m:
            runs[m.group(1)][int(m.group(2))] = json.loads(path.read_text(encoding="utf-8"))
    return runs


def check_mode(mode: str, doc: dict[str, Any], system_len: dict[str, int]) -> str | None:
    """Garantiza que la ejecución se hizo realmente en ese modo (la longitud del system prompt registrada debe coincidir)."""
    seen = (doc.get("summary", {}).get("prefix_chars") or {}).get("system_prompt")
    expected = system_len[mode]
    return None if seen in (None, expected) else f"modo {mode}: system prompt de {seen} car. (esperado {expected}); ¿AGENT_PROMPT_MODE aplicado?"


def case_record(case: dict[str, Any]) -> dict[str, Any]:
    return {"status": case.get("status"), "tools": list(case.get("tools_used") or []), "llm_calls": case.get("llm_calls") or 0,
            "mcp_calls": case.get("tool_calls") or 0, "input_tokens": case.get("input_tokens"), "output_tokens": case.get("output_tokens"),
            "cached_tokens": case.get("cached_tokens"), "token_source": case.get("token_source"), "reasons": _reasons(case) if case.get("status") == "fail" else []}


def summarize(runs: dict[str, dict[int, dict[str, Any]]], system_len: dict[str, int] | None = None) -> dict[str, Any]:
    if system_len is None:
        from futsal.agent.agent import PROMPT_SETS
        system_len = {m: len(PROMPT_SETS[m].system) for m in MODES}
    warnings = [w for m in MODES for doc in runs[m].values() if (w := check_mode(m, doc, system_len))]
    order: list[str] = []
    per: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    for m in MODES:
        for rep, doc in sorted(runs[m].items()):
            for c in doc.get("cases", []):
                if c["case_id"] not in per:
                    order.append(c["case_id"])
                per.setdefault(c["case_id"], {x: {} for x in MODES})[m][rep] = case_record(c)
    cases = []
    for cid in order:
        entry: dict[str, Any] = {"case": cid, "modes": {}}
        for m in MODES:
            reps = per[cid][m]
            dec = [r["status"] for r in reps.values() if r["status"] in DECISIVE]
            passes = dec.count("pass")
            entry["modes"][m] = {
                "reps": {str(k): v for k, v in sorted(reps.items())}, "decisive": len(dec), "passes": passes,
                "rate": passes / len(dec) if dec else None, "flaky": 0 < passes < len(dec),
                "tool_sets": sorted({",".join(sorted(set(r["tools"]))) for r in reps.values()}),
                "reasons": sorted({x for r in reps.values() for x in r["reasons"]})}
        lg, cp = entry["modes"][LEGACY], entry["modes"][COMPACT]
        if lg["rate"] is None or cp["rate"] is None:
            entry["verdict"] = "sin datos"
        elif cp["rate"] - lg["rate"] <= -MIN_GAP:
            entry["verdict"] = "regresión consistente"
        elif cp["rate"] - lg["rate"] >= MIN_GAP:
            entry["verdict"] = "mejora consistente"
        elif lg["flaky"] or cp["flaky"]:
            entry["verdict"] = "variable (ruido)"
        else:
            entry["verdict"] = "igual"
        cases.append(entry)
    totals: dict[str, Any] = {}
    for m in MODES:
        by_rep: dict[str, Any] = {}
        for rep, doc in sorted(runs[m].items()):
            recs = [case_record(c) for c in doc.get("cases", [])]
            dec = [r for r in recs if r["status"] in DECISIVE]
            real = [r for r in dec if r["token_source"] == "provider"]
            calls = sum(r["llm_calls"] for r in dec)
            by_rep[str(rep)] = {"decisive": len(dec), "pass": sum(r["status"] == "pass" for r in dec), "llm_calls": calls,
                                "mcp_calls": sum(r["mcp_calls"] for r in dec), "input_tokens": sum(r["input_tokens"] or 0 for r in real),
                                "output_tokens": sum(r["output_tokens"] or 0 for r in real), "tokens_all_from_provider": len(real) == len(dec),
                                "input_per_llm_call": round(sum(r["input_tokens"] or 0 for r in real) / calls, 1) if calls else None}
        flaky = sum(1 for c in cases if c["modes"][m]["flaky"])
        totals[m] = {"runs": by_rep, "flaky_cases": flaky, "mean_pass": round(mean([r["pass"] for r in by_rep.values()]), 2) if by_rep else None,
                     "mean_input_per_llm_call": round(mean([r["input_per_llm_call"] for r in by_rep.values() if r["input_per_llm_call"]]), 1)
                     if any(r["input_per_llm_call"] for r in by_rep.values()) else None}
    meta = {m: {str(rep): {"run_id": d.get("meta", {}).get("run_id"), "commit": d.get("meta", {}).get("commit"),
                           "model": d.get("meta", {}).get("model")} for rep, d in sorted(runs[m].items())} for m in MODES}
    return {"cases": cases, "totals": totals, "warnings": warnings, "meta": meta,
            "verdicts": {v: sum(c["verdict"] == v for c in cases) for v in ("regresión consistente", "mejora consistente", "variable (ruido)", "igual", "sin datos")}}


def render(s: dict[str, Any]) -> str:
    lines = ["# Comparación controlada legacy / compact (repeticiones por separado)", ""]
    lines += [f"AVISO: {w}" for w in s["warnings"]]
    lines += ["", "## Por modo y repetición (casos decisivos = pass/fail)", "| modo | rep | casos | pass | llamadas LLM | llamadas MCP | tokens entrada | tokens salida | entrada/llamada |",
              "|---|---|---|---|---|---|---|---|---|"]
    for m in MODES:
        for rep, r in s["totals"][m]["runs"].items():
            real = "" if r["tokens_all_from_provider"] else " (¡estimados!)"
            lines.append(f"| {m} | {rep} | {r['decisive']} | {r['pass']} | {r['llm_calls']} | {r['mcp_calls']} | {r['input_tokens']}{real} | "
                         f"{r['output_tokens']} | {r['input_per_llm_call']} |")
    lines += ["", "Variabilidad: casos con resultado distinto entre repeticiones del MISMO modo — " +
              ", ".join(f"{m}: {s['totals'][m]['flaky_cases']}" for m in MODES) + " · media de aciertos por repetición — " +
              ", ".join(f"{m}: {s['totals'][m]['mean_pass']}" for m in MODES), "Veredictos: " + ", ".join(f"{v}: {n}" for v, n in s["verdicts"].items()),
              "", "## Por caso (P = pass, F = fail, · = no decisivo)", "| caso | legacy | compact | veredicto |", "|---|---|---|---|"]
    for c in s["cases"]:
        def cell(m: str) -> str:
            d = c["modes"][m]
            seq = " ".join({"pass": "P", "fail": "F"}.get(r["status"], "·") for r in d["reps"].values())
            return f"{seq} ({d['passes']}/{d['decisive']})" if d["decisive"] else seq
        lines.append(f"| `{c['case']}` | {cell(LEGACY)} | {cell(COMPACT)} | {c['verdict']} |")
    lines += ["", "## Detalle por caso y repetición (herramientas · llamadas LLM/MCP · tokens reales entrada/salida · motivo del fallo)"]
    for c in s["cases"]:
        lines.append(f"\n### `{c['case']}` — {c['verdict']}")
        for m in MODES:
            for rep, r in c["modes"][m]["reps"].items():
                tok = f"{r['input_tokens']}/{r['output_tokens']}" + ("" if r["token_source"] == "provider" else " (estimado)")
                why = f" · {'; '.join(r['reasons'])}" if r["reasons"] else ""
                lines.append(f"- {m} r{rep}: {r['status']} · {', '.join(r['tools']) or '—'} · LLM {r['llm_calls']} / MCP {r['mcp_calls']} · {tok}{why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resume una comparación controlada legacy/compact (sin LLM).")
    ap.add_argument("folder", help="carpeta con legacy-r1.json, compact-r1.json…")
    ap.add_argument("--json", help="guarda el resumen completo")
    ap.add_argument("--md", help="guarda el informe en Markdown")
    args = ap.parse_args(argv)
    summary = summarize(load_runs(Path(args.folder)))
    text = render(summary)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
