"""Suite `regression`: las preguntas ACTUALES (`build_cases` = fases 8-17 + `audit_cases` + `rule_cases`) ejecutadas con el
`evaluate` existente, sin reescribirlas, y adaptadas al formato de resultados de Eval v1 (veredicto, motivo, métricas).

Veredictos: `pass` -> `correct`; `fail` -> `fail` (clase `interpretation` si falla la respuesta o las herramientas, `presentation` si solo
falla una advertencia o la evidencia), o `critical` si el evaluador antiguo marca un error crítico (invención o fuente sustituida);
`not_evaluable` y `not_run` se conservan. Un `not_run` por infraestructura distinta de cuota/presupuesto pasa a `not_evaluable`.
"""

import asyncio
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from futsal.agent.audit import NO_DATA_KINDS, letter
from futsal.agent.criteria import _parse_scores
from futsal.agent.evaluate import (
    CaseResult,
    EvalCase,
    build_cases,
    evaluate,
    invention_claims,
    load_truth,
)
from futsal.agent.llm import BudgetedLLM, LLMClient, PacedLLM
from futsal.agent.trace import read_jsonl
from futsal.evals import report as reporting
from futsal.evals.prices import cost_usd
from futsal.evals.runner import (
    INFRA,
    RunConfig,
    _empty_usage,
    _load_previous,
    _pending_pairs,
    _record,
    _stop_reason,
    arm_environment,
    build_meta,
    mounted,
    prepare_database,
    rotated,
    write_outputs,
)

STATUS_TO_VERDICT = {"pass": "correct", "fail": "fail", "not_evaluable": "not_evaluable", "not_run": "not_run"}


def behavior_of(case: EvalCase) -> str:
    if not case.facts:
        return "answer"
    kind = case.facts[0]
    if kind.kind == "refusal":
        return "offtopic"
    if kind.kind in ("ambiguous", "team_ambiguous"):
        return "clarify"
    return "abstain" if kind.kind in NO_DATA_KINDS or kind.kind in ("table_status", "no_evidence_rules") else "answer"


def adapt(r: CaseResult, case: EvalCase, rep: int, price: tuple[str | None, str | None],
          evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resultado del evaluador antiguo -> registro de Eval v1 (el veredicto conserva el del evaluador)."""
    verdict = STATUS_TO_VERDICT[r.status]
    error_class: str | None = None
    reason = r.detail
    if r.status == "not_run" and not _stop_reason(r.detail):
        verdict, reason = "not_evaluable", f"{INFRA} {r.detail}"
    if r.status == "fail":
        if r.critical:
            verdict, error_class, reason = "critical", "critical", "; ".join(r.critical)
        else:
            error_class = "interpretation" if (r.answer_ok is False or r.tools_ok is False) else "presentation"
    totals = {"token_source": r.token_source, "input_tokens": r.input_tokens, "cached_input_tokens": r.cached_tokens,
              "billed_output_tokens": r.output_tokens + r.thinking_tokens, "output_tokens": r.output_tokens,
              "thinking_tokens": r.thinking_tokens}
    usage = _empty_usage()
    usage["metrics"].update({
        "llm_calls": r.llm_calls, "tool_calls": r.tool_calls, "mcp_calls": r.tool_calls, "input_tokens": r.input_tokens,
        "cached_tokens": r.cached_tokens, "output_tokens": r.output_tokens, "thinking_tokens": r.thinking_tokens,
        "billed_output_tokens": r.output_tokens + r.thinking_tokens, "token_source": r.token_source, "retries": r.llm_retries,
        "errors": sum("devolvió error" in i for i in r.tool_issues), "exec_tier": r.exec_tier, "cost_usd": cost_usd(price[0], price[1], totals)})
    llm_ms = max(r.latency_ms - r.tool_ms, 0.0)
    usage["timing"] = {"llm_call_ms": [llm_ms] if r.llm_calls else [], "tool_call_ms": [r.tool_ms] if r.tool_calls else [],
                       "latency_ms": r.latency_ms, "basis": "question"}  # el evaluador antiguo solo guarda sumas por pregunta
    grade = {"tools_missing": [i for i in r.tool_issues if i.startswith("falta la herramienta")], "tools_unexpected": r.unexpected_tools,
             "facts_present": ["answer_ok"] if r.answer_ok else [], "over_budget": False, "legacy": {
                 "status": r.status, "evidence_ok": r.evidence_ok, "tools_ok": r.tools_ok, "answer_ok": r.answer_ok,
                 "invention_ok": r.invention_ok, "warning_ok": r.warning_ok, "presentation_ok": r.presentation_ok}}
    return _record(r.case_id, letter(r.case_id, r.category), False, rep, 0, r.question, verdict, reason, error_class=error_class,
                   answer=r.answer, tools_used=r.tools_used, grade=grade, run_id=r.run_id, evidence=evidence or [], **usage)


def _trace_evidence(trace: Path) -> list[dict[str, Any]]:
    """Payloads de las tools tal y como los ve el evaluador (resultado enviado al LLM, ya recortado), de la traza de una pregunta."""
    out: list[dict[str, Any]] = []
    if not trace.exists():
        return out
    for row in read_jsonl(trace):
        for tc in row.get("tool_calls", []):
            if tc.get("is_error") or not tc.get("result"):
                continue
            try:
                data = json.loads(tc["result"])
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def run_regression(cfg: RunConfig, llm_factory: Any) -> dict[str, Any]:
    url, manifest = prepare_database(cfg)
    engine = create_engine(url)
    try:
        truth = load_truth(engine)
        if truth is None:
            raise RuntimeError("el snapshot no tiene ningún grupo: no se puede construir la suite de regresión")
        cases = build_cases(truth)
        by_id = {c.id: c for c in cases}
        order = [c.id for c in cases]
        pairs = [(cid, r) for r in range(cfg.reps) for cid in order]
        previous = _load_previous(cfg, manifest)
        todo = _pending_pairs(previous, pairs)
        base: LLMClient = llm_factory()
        budget = BudgetedLLM(PacedLLM(base, cfg.min_interval), cfg.max_requests)
        price = (getattr(base, "provider", None), getattr(base, "model", None))
        as_of = datetime.fromisoformat(manifest["as_of"])

        async def go() -> tuple[list[dict[str, Any]], str | None]:
            out: list[dict[str, Any]] = []
            stopped: str | None = None
            async with mounted(engine, as_of) as mcp:
                for rep in range(cfg.reps):
                    for cid in rotated(order, rep, cfg.reps):
                        if (cid, rep) not in todo:
                            continue
                        case = by_id[cid]
                        if stopped:
                            out.append(_record(cid, letter(cid, case.category), False, rep, 0, case.question, "not_run", stopped))
                            continue
                        trace = Path(tmp) / f"{cid}-{rep}.jsonl"
                        res = (await evaluate(truth, mcp, "live", budget, only={cid}, trace_path=trace))[0]
                        out.append(adapt(res, case, rep, price, _trace_evidence(trace)))
                        stopped = _stop_reason(res.detail) if res.status == "not_run" else None
            return out, stopped

        with arm_environment(cfg.arm) as flags, tempfile.TemporaryDirectory() as tmp:
            new_records, stopped = asyncio.run(go())
        kept = [r for r in (previous["records"] if previous else []) if (r["case_id"], r["rep"]) not in todo]
        records = sorted(kept + new_records, key=lambda r: (r["rep"], r["case_id"], r["turn"]))
        meta = build_meta(cfg, manifest, base, flags, budget.used, stopped, price)
        meta["dataset"] = {"cases": len(cases), "evaluable": sum(c.facts is not None for c in cases),
                           "note": "preguntas actuales de agent/ (build_cases + audit_cases + rule_cases), sin reescribir"}
        cases_info: dict[str, Any] = {cid: {"stratum": letter(cid, by_id[cid].category), "holdout": False, "capability": "v1",
                            "status": "ok" if by_id[cid].facts is not None else "not_evaluable", "reason": by_id[cid].reason,
                            "bindings": {}, "expected": [{"user": by_id[cid].question, "behavior": behavior_of(by_id[cid]),
                                                          "tools_needed": sorted(by_id[cid].required)}]} for cid in order}
        results = {"schema": 1, "meta": meta, "cases": cases_info, "records": records}
        results["summary"] = reporting.summarize(results)
        write_outputs(cfg.output, results)
        return results
    finally:
        engine.dispose()


def legacy_status(results: dict[str, Any]) -> dict[str, str]:
    """Estado del evaluador antiguo por caso, leído de un results.json de regresión (para comparar con una ejecución directa)."""
    return {r["case_id"]: r["grade"]["legacy"]["status"] for r in results["records"] if r.get("grade") and "legacy" in r["grade"]}


# ----------------------------------------------------------------------------- recalificación sin LLM
_INVENTION = "invención: "
_SOURCE = "; fuente incorrecta"
_SCORE_CLAIM = re.compile(r"da el marcador (\d+)-(\d+),")
_LAYER_NAMES = (("evidence_ok", "datos"), ("tools_ok", "herramientas"), ("answer_ok", "respuesta"), ("warning_ok", "advertencia"))


def _split_reason(reason: str) -> tuple[list[str], str]:
    """(afirmaciones de invención, resto del motivo crítico) de un motivo `invención: a; b; fuente incorrecta: …`."""
    if not reason.startswith(_INVENTION):
        return [], reason
    head, sep, tail = reason[len(_INVENTION):].partition(_SOURCE)
    return [c for c in head.split("; ") if c], (_SOURCE[2:] + tail if sep else "")


def _rebuild(rec: dict[str, Any], claims: list[str], rest: str, why: str) -> None:
    """Recompone veredicto y motivo con las afirmaciones de invención que quedan; el resto de capas se conserva."""
    legacy = rec["grade"]["legacy"]
    legacy["invention_ok"] = not claims
    failing = [name for key, name in _LAYER_NAMES if legacy.get(key) is False]
    reasons = ([_INVENTION + "; ".join(claims)] if claims else []) + ([rest] if rest else [])
    if claims or rest:
        rec["verdict"], rec["error_class"], rec["reason"] = "critical", "critical", "; ".join(reasons)
        legacy["status"] = "fail"
    elif failing:
        legacy["status"] = "fail"
        rec["verdict"] = "fail"
        rec["error_class"] = "interpretation" if legacy.get("answer_ok") is False or legacy.get("tools_ok") is False else "presentation"
        rec["reason"] = "(recalificado) capas que ya fallaban en la ejecución original: " + ", ".join(failing)
    else:
        legacy["status"] = "pass"
        rec["verdict"], rec["error_class"], rec["reason"] = "correct", None, ""
    rec["regrade"] = why


def regrade_record(rec: dict[str, Any], case: EvalCase | None, truth: Any) -> dict[str, Any]:
    """Recalifica un registro sin LLM. Con evidencia guardada (y la BD del snapshot) rehace la capa de invención completa; sin ella,
    solo descarta los marcadores inventados que el criterio actual ya no detecta en la respuesta (nunca añade afirmaciones)."""
    out: dict[str, Any] = json.loads(json.dumps(rec))
    if not out.get("grade") or "legacy" not in out["grade"] or not out.get("answer") or out["verdict"] not in ("correct", "fail", "critical"):
        return out
    old, rest = _split_reason(out["reason"]) if out["verdict"] == "critical" else ([], "")
    evidence = out.get("evidence")
    if evidence and case is not None and truth is not None:
        new = invention_claims(case, out["answer"], truth, evidence)
        if new != old:
            _rebuild(out, new, rest, "completo: invención recalculada con la evidencia guardada")
        return out
    still = _parse_scores(out["answer"])
    new = [c for c in old if not (m := _SCORE_CLAIM.search(c)) or (int(m.group(1)), int(m.group(2))) in still]
    if new != old:
        _rebuild(out, new, rest, "parcial")
        out["regraded_partial"] = True
        out["regraded_partial_reason"] = ("sin evidencia de las herramientas en la ejecución: solo se recalifica la detección de marcadores "
                                          "inventados sobre la respuesta; el resto de veredictos se conserva")
    return out


def regrade_results(results: dict[str, Any], cases: dict[str, EvalCase], truth: Any, source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Nuevo results.json (sin tocar el original) y diff de veredictos antes/después."""
    records = [regrade_record(r, cases.get(r["case_id"]), truth) for r in results["records"]]
    diff = [{"case": a["case_id"], "rep": a["rep"], "turn": a["turn"], "before": a["verdict"], "after": b["verdict"],
             "reason_before": a["reason"], "reason_after": b["reason"], "mode": b["regrade"]}
            for a, b in zip(results["records"], records, strict=True) if a["verdict"] != b["verdict"] or a["reason"] != b["reason"]]
    new = json.loads(json.dumps(results))
    new["records"] = records
    new["meta"]["regrade"] = {"source": source, "changed": len(diff), "llm_calls": 0,
                              "partial": sum(bool(r.get("regraded_partial")) for r in records),
                              "full": sum(str(r.get("regrade", "")).startswith("completo") for r in records)}
    new["summary"] = reporting.summarize(new)
    return new, diff
