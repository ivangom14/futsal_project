"""`plan`: estima llamadas LLM/MCP, tokens y coste de una ejecución SIN ninguna llamada externa (ni red ni LLM).

Coste medio por pregunta: 2,65 llamadas y 10.800 tokens de entrada MEDIDOS en el agente actual (docs/v2/evaluation.md §8), o los de un
`results.json` anterior (`--calibrate`). Lo que no está medido se declara supuesto: salida por llamada (49 tokens, docs/eval-harness.md),
llamadas MCP por pregunta (llamadas LLM − 1) y margen de reintentos del tope sugerido (15 %).
"""

import json
import math
from pathlib import Path
from typing import Any

from futsal.agent.llm import Usage, estimate_cost
from futsal.evals.datasets import counts, suite_cases
from futsal.evals.prices import price_info

MEASURED = {"llm_calls": 2.65, "input_tokens": 10800.0}
ASSUMED_OUTPUT_PER_CALL = 49.0
RETRY_MARGIN = 1.15
REGRESSION_DECLARED = {"cases": 72, "evaluable": 65}


def per_question(calibrate: Path | None) -> dict[str, Any]:
    if calibrate is None:
        calls = MEASURED["llm_calls"]
        return {"source": "medido en el agente actual (docs/v2/evaluation.md §8)", "llm_calls": calls,
                "mcp_calls": round(calls - 1, 2), "input_tokens": MEASURED["input_tokens"],
                "output_tokens": round(calls * ASSUMED_OUTPUT_PER_CALL, 1),
                "assumed": ["salida: 49 tokens por llamada (docs/eval-harness.md)", "MCP: llamadas LLM − 1 por pregunta"]}
    data = json.loads(calibrate.read_text(encoding="utf-8"))
    g = data["summary"]["global"]
    q = g["per_question"]
    provider = g["tokens_by_source"].get("provider")
    if not provider or not provider["questions"]:
        raise ValueError("el results.json de calibración no tiene tokens del proveedor (origen `provider`)")
    n = provider["questions"]
    return {"source": f"calibrado con {calibrate} ({q['questions']} preguntas)", "llm_calls": q["llm_calls"], "mcp_calls": q["mcp_calls"],
            "input_tokens": round(provider["input"] / n, 1), "output_tokens": round((provider["output"] + provider["thinking"]) / n, 1),
            "assumed": []}


def build_plan(suite: str, split: str, reps: int, root: Path = Path("."), calibrate: Path | None = None,
               provider: str | None = None, model: str | None = None,
               max_requests: int | None = None, regression_cases: dict[str, int] | None = None) -> dict[str, Any]:
    if suite == "regression":
        decl = regression_cases or REGRESSION_DECLARED
        by_stratum: dict[str, Any] = {"A-G": {"cases": decl["cases"], "turns": decl["evaluable"], "holdout": 0}}
        note = ("72 casos / 65 evaluables según la batería actual (declarado; con --db se cuentan en el snapshot)"
                if regression_cases is None else "contados en el snapshot")
    else:
        by_stratum = counts(suite_cases(suite, split, root))
        note = f"{suite} ({split}): turnos del dataset (sin resolver contra ningún snapshot)"
    turns = sum(v["turns"] for v in by_stratum.values())
    pq = per_question(calibrate)
    questions = turns * reps
    llm_calls, mcp_calls = questions * pq["llm_calls"], questions * pq["mcp_calls"]
    tin, tout = questions * pq["input_tokens"], questions * pq["output_tokens"]
    info = price_info(provider, model)
    cost = estimate_cost(provider or "", model or "", Usage(int(tin), int(tout), 0, None, True))
    suggested = math.ceil(llm_calls * RETRY_MARGIN)
    return {
        "suite": suite, "split": split, "reps": reps, "turns_per_repetition": turns, "questions": questions, "by_stratum": by_stratum,
        "note": note, "per_question": pq,
        "estimated": {"llm_calls": round(llm_calls), "mcp_calls": round(mcp_calls), "input_tokens": round(tin),
                      "output_tokens": round(tout)},
        "cost_usd": round(cost, 6) if cost is not None else None,
        "cost_note": (f"tarifa {info['source']} (verificada {info['verified_at']})" if "source" in info
                      else "sin tarifa en config/llm_prices.json para ese modelo: coste = null"),
        "suggested_max_requests": suggested,
        "budget_ok": None if max_requests is None else max_requests >= llm_calls,
        "max_requests": max_requests, "external_calls": 0}


def render(plan: dict[str, Any]) -> str:
    e = plan["estimated"]
    lines = [f"Plan {plan['suite']} ({plan['split']}) × {plan['reps']} repeticiones — SIN llamadas externas", f"  {plan['note']}"]
    lines += [f"  {s}: {v['cases']} casos · {v['turns']} turnos" for s, v in plan["by_stratum"].items()]
    cost = plan["cost_usd"] if plan["cost_usd"] is not None else "null"
    lines += [f"  Preguntas: {plan['questions']} (turnos × repeticiones) · base: {plan['per_question']['source']}",
              (f"  Estimado: {e['llm_calls']} llamadas LLM · {e['mcp_calls']} llamadas MCP · {e['input_tokens']} tokens de entrada · "
               f"{e['output_tokens']} de salida"),
              f"  Coste: {cost} USD ({plan['cost_note']})",
              f"  --max-requests sugerido: {plan['suggested_max_requests']} (llamadas + 15 % de reintentos)"]
    lines += [f"  Supuesto: {a}" for a in plan["per_question"]["assumed"]]
    if plan["budget_ok"] is False:
        lines.append(f"  AVISO: --max-requests {plan['max_requests']} no basta para ejecutarlo entero: quedará parte not_run (reanudable)")
    return "\n".join(lines)
