"""Suite `regression`: las preguntas actuales ejecutadas con el `evaluate` existente y adaptadas al formato de Eval v1."""

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from futsal.agent.agent import OFF_TOPIC_MESSAGE
from futsal.agent.evaluate import EvalCase, build_cases, evaluate, load_truth
from futsal.agent.llm import LLMResponse, Message, ToolCall, ToolResult, Usage  # noqa: F401
from futsal.evals.regression import legacy_status
from futsal.evals.runner import RunConfig, mounted, run_eval
from futsal.evals.snapshot import parse_as_of

NO_DATA = "No he encontrado ese dato en las fuentes que tengo disponibles."


class LegacyScript:
    """Guion fijo para las preguntas actuales: sigue la ruta de referencia de cada caso y responde con los hechos que puede (o se abstiene).
    Sin estado entre casos: el mismo guion sirve para el evaluador antiguo y para el runner de Eval v1."""

    provider, model = "scripted", "legacy-1"

    def __init__(self, cases: list[EvalCase]) -> None:
        self.cases = cases

    def _case(self, messages: list[Message]) -> EvalCase:
        first = next(m.text for m in messages if m.role == "user")
        return next(c for c in self.cases if first.startswith(c.question))

    def _text(self, case: EvalCase) -> str:
        facts = case.facts or []
        if facts and facts[0].kind == "refusal":
            return OFF_TOPIC_MESSAGE
        lines = [f"{m.home} {m.hs}-{m.aws} {m.away}." for f in facts if f.kind in ("match", "match_any") and f.matches
                 for m in f.matches if m.hs is not None]
        if lines:
            return " ".join(lines)
        return NO_DATA if facts and facts[0].kind in ("limitation", "no_report", "no_player", "empty_scope", "not_played") else "Dato no localizado."

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        case = self._case(messages)
        step = sum(1 for m in messages if m.role == "tool")
        import json
        payloads = [json.loads(r.content) for m in messages for r in m.tool_results if not r.is_error]
        calls = case.plan(step, payloads) if case.plan and case.facts and case.facts[0].kind != "refusal" else None
        if calls:
            return LLMResponse("", list(calls), Usage(500, 20, 0, None))
        return LLMResponse(self._text(case), [], Usage(900, 60, 0, None))


def test_regression_reproduces_the_legacy_evaluator_verdicts(synthetic: Any, tmp_path: Path) -> None:
    engine = create_engine(synthetic.url)
    truth = load_truth(engine)
    assert truth is not None
    cases = build_cases(truth)
    as_of = parse_as_of("2026-10-20T12:00:00")

    async def direct() -> list[Any]:
        async with mounted(engine, as_of) as mcp:
            return await evaluate(truth, mcp, "live", LegacyScript(cases))

    legacy = {r.case_id: r for r in asyncio.run(direct())}
    engine.dispose()
    cfg = RunConfig(suite="regression", snapshot="synthetic-dev", output=tmp_path / "reg", root=synthetic.root, db_url=synthetic.url,
                    max_requests=5000, reps=1)
    results = run_eval(cfg, lambda: LegacyScript(cases))
    assert len(legacy) == len(cases) == len(results["records"])
    # mismos veredictos que el evaluador antiguo con el mismo guion
    assert legacy_status(results) == {cid: r.status for cid, r in legacy.items()}
    verdict = {"pass": "correct", "fail": "fail", "not_evaluable": "not_evaluable", "not_run": "not_run"}
    for rec in results["records"]:
        old = legacy[rec["case_id"]]
        expected = "critical" if old.status == "fail" and old.critical else verdict[old.status]
        assert rec["verdict"] == expected, (rec["case_id"], rec["verdict"], old.status)
        assert rec["question"] == old.question and rec["answer"] == old.answer
        assert rec["metrics"]["llm_calls"] == old.llm_calls and rec["metrics"]["input_tokens"] == old.input_tokens
        if rec["verdict"] == "fail":
            assert rec["error_class"] in ("interpretation", "presentation") and rec["reason"] == old.detail
    statuses = {old.status for old in legacy.values()}
    assert {"pass", "fail", "not_evaluable"} <= statuses  # el guion produce veredictos de todo tipo
    meta = results["meta"]
    assert meta["suite"] == "regression" and meta["dataset"]["cases"] == len(cases)
    assert set(results["summary"]["strata"]) <= set("ABCDEFG")
    report = (tmp_path / "reg" / "report.md").read_text(encoding="utf-8")
    assert "regression" in report and "Wilson" in report
