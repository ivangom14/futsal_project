"""Compara el prefijo del modo `legacy` y del `compact` (Fase 1 de la optimización de tokens). Sin LLM, sin red y sin base de datos.

Mide los textos reales (prompt, historial, cabeceras de contexto, descripciones MCP) con el mismo estimador de la Fase 0 (caracteres / 3,5:
ESTIMACIÓN, no tokens del proveedor) y, opcionalmente, reproduce los casos de una ejecución guardada del evaluador (`--cases`) con cada
prefijo. La reproducción mantiene tal cual las llamadas al LLM y a las herramientas del flujo guionizado y solo cambia el prefijo: sirve
para ver cuánto pesa el cambio por caso, NO para decir si las respuestas siguen siendo correctas (eso exige un LLM real:
`AGENT_PROMPT_MODE=legacy|compact python -m futsal.agent.evaluate --mode live --confirm-live` y `--compare`).

Uso: `python -m futsal.agent.prompt_compare [--cases data/agent/eval-last.json] [--header context|catalog] [--json salida.json]`
"""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from futsal.agent.agent import PROMPT_SETS, slim_tools
from futsal.agent.llm import _gemini_tools
from futsal.agent.mcp_client import strip_titles
from futsal.agent.prompts import COMPACT, LEGACY
from futsal.agent.tokens import BASIS, est_tokens
from futsal.mcp_server.server import create_server


async def _tools(mode: str) -> list[dict[str, Any]]:
    listed = await create_server(descriptions=mode).list_tools()
    return slim_tools([{"name": t.name, "description": t.description or "", "input_schema": strip_titles(t.inputSchema)} for t in listed])


def _json_chars(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def measure(mode: str) -> dict[str, Any]:
    tools = asyncio.run(_tools(mode))
    p = PROMPT_SETS[mode]
    per_tool = {t["name"]: _json_chars(t) for t in tools}
    return {"mode": mode, "system": len(p.system), "history_rule": len(p.history_rule), "context_header": len(p.context_header),
            "catalog_header": len(p.catalog_header), "tools_canonical": _json_chars(tools), "tools_gemini_wire": _json_chars(_gemini_tools(tools)),
            "per_tool": per_tool}


def prefix_tokens(m: dict[str, Any], context_chars: int, wire: bool = False) -> int:
    """Prefijo ESTIMADO de una llamada: prompt + esquemas + contexto (el contexto incluye su cabecera)."""
    return est_tokens(m["system"]) + est_tokens(m["tools_gemini_wire" if wire else "tools_canonical"]) + est_tokens(context_chars)


def replay(path: Path, header: str, legacy: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any]:
    """Cada caso de la ejecución guardada con el prefijo de cada modo. Entrada = llamadas × prefijo + parte variable (pregunta, resultados)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    key = "context_header" if header == "context" else "catalog_header"
    rows = []
    for c in doc["cases"]:
        pf, calls, inp = c.get("prefix_chars") or {}, c.get("llm_calls") or 0, c.get("input_tokens") or 0
        if not inp or not pf:
            rows.append({"id": c["case_id"], "category": c["category"], "status": c["status"], "evaluable": False})
            continue
        recorded = est_tokens(pf["system_prompt"]) + est_tokens(pf["tools"]) + est_tokens(pf["context"])
        variable = max(inp - calls * recorded, 0)  # lo que no es prefijo: se conserva igual en ambos modos
        ctx_data = pf["context"] - legacy[key]  # el contexto de datos (grupos, jornadas, equipos) no cambia
        before = calls * prefix_tokens(legacy, pf["context"]) + variable
        after = calls * prefix_tokens(compact, ctx_data + compact[key]) + variable
        rows.append({"id": c["case_id"], "category": c["category"], "status": c["status"], "evaluable": True, "llm_calls": calls,
                     "tool_calls": c.get("tool_calls"), "tokens_before": before, "tokens_after": after, "saved": before - after})
    return {"source": str(path), "run": doc.get("meta", {}).get("run_id"), "commit": doc.get("meta", {}).get("commit"), "rows": rows}


def render(legacy: dict[str, Any], compact: dict[str, Any], rep: dict[str, Any] | None, header: str) -> str:
    key = "context_header" if header == "context" else "catalog_header"
    lines = [f"Estimador: {BASIS} (tokens ESTIMADOS; el proveedor no desglosa el prefijo).",
             f"{'parte':22s}{'legacy':>9s}{'compact':>9s}{'Δ car.':>8s}{'Δ %':>8s}{'tok L':>7s}{'tok C':>7s}"]
    for name, label in (("system", "system prompt"), ("history_rule", "regla de historial"), ("context_header", "cabecera contexto"),
                        ("catalog_header", "cabecera catálogo"), ("tools_canonical", "herramientas (canónico)"),
                        ("tools_gemini_wire", "herramientas (cable Gemini)")):
        a, b = legacy[name], compact[name]
        lines.append(f"{label:22s}{a:9d}{b:9d}{b - a:8d}{(b / a - 1) * 100:7.1f}%{est_tokens(a):7d}{est_tokens(b):7d}")
    ctx = 1638  # contexto de la ejecución offline de referencia (1 grupo); la cabecera cambia, los datos no
    for wire in (False, True):
        a = prefix_tokens(legacy, ctx, wire)
        b = prefix_tokens(compact, ctx - legacy[key] + compact[key], wire)
        lines.append(f"Prefijo por llamada ({'cable Gemini' if wire else 'canónico'}, con contexto de {ctx} car.): {a} → {b} tokens "
                     f"({b - a:+d}; {(b / a - 1) * 100:+.1f}%)")
    lines.append("Por herramienta (car. legacy → compact): " + ", ".join(
        f"{n} {legacy['per_tool'][n]}→{compact['per_tool'][n]}" for n in legacy["per_tool"] if legacy["per_tool"][n] != compact["per_tool"][n]))
    if rep:
        ev = [r for r in rep["rows"] if r["evaluable"]]
        before, after = sum(r["tokens_before"] for r in ev), sum(r["tokens_after"] for r in ev)
        lines.append(f"Reproducción de {len(ev)} casos de {rep['run']} ({rep['commit']}), flujo guionizado idéntico (mismas llamadas LLM y MCP): "
                     f"{before} → {after} tokens de entrada ESTIMADOS ({after - before:+d}; {(after / before - 1) * 100:+.1f}%)")
        by: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for r in ev:
            by[r["category"]][0] += 1
            by[r["category"]][1] += r["tokens_before"]
            by[r["category"]][2] += r["tokens_after"]
        lines.append("Por categoría (casos | antes → después | Δ%):")
        for cat, (n, a, b) in sorted(by.items()):
            lines.append(f"  {cat:20s}{n:3d} | {a:7d} → {b:7d} | {(b / a - 1) * 100:+.1f}%")
        lines.append(f"Casos sin tokens en la traza (no evaluables en esa ejecución): {len(rep['rows']) - len(ev)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compara el prefijo legacy/compact (estimado; sin LLM).")
    ap.add_argument("--cases", help="results.json de una ejecución del evaluador (p. ej. data/agent/eval-last.json)")
    ap.add_argument("--header", choices=("context", "catalog"), default="context", help="cabecera de contexto de esa ejecución")
    ap.add_argument("--json", help="guarda el resultado completo")
    args = ap.parse_args(argv)
    legacy, compact = measure(LEGACY), measure(COMPACT)
    rep = replay(Path(args.cases), args.header, legacy, compact) if args.cases else None
    print(render(legacy, compact, rep, args.header))
    if args.json:
        Path(args.json).write_text(json.dumps({"legacy": legacy, "compact": compact, "replay": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
