"""Evaluación OFFLINE del agente sobre una BD temporal sembrada con fixtures (API y MCP reales en memoria)."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine

from futsal.agent.evaluate import CaseResult, evaluate, load_truth
from futsal.agent.mcp_client import SessionMcpClient
from futsal.api.app import create_app
from futsal.importer.service import run_import
from futsal.mcp_server.server import create_server

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"


def _evaluate(engine: Engine, **kw: object) -> list[CaseResult]:
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "offline", **kw)  # type: ignore[arg-type]

    return asyncio.run(go())


def test_offline_evaluation_passes_with_complete_evidence(engine: Engine) -> None:
    results = {r.case_id: r for r in _evaluate(engine)}
    assert not [r for r in results.values() if r.status == "fail"], {
        r.case_id: r.detail for r in results.values() if r.status == "fail"}
    assert results["equipo_resultados"].status == "pass"
    assert results["jornada_2_resultados"].status == "pass"
    assert results["recuento_global"].status == "pass" and results["fuera_de_alcance"].status == "pass"
    assert results["fuera_de_alcance"].answer_ok is True  # guardarraíl determinista
    # casos que dependen del texto del modelo: no se inventan resultados offline
    assert results["goleadores_no_disponible"].status == "not_evaluable"
    assert all(r.tokens_estimated for r in results.values() if r.status != "not_evaluable" and r.llm_calls)


def test_offline_evaluation_is_read_only(engine: Engine) -> None:
    from sqlalchemy import text

    run_import(engine, FIXTURE)
    q = "SELECT (SELECT count(*) FROM matches), (SELECT count(*) FROM match_observations), " \
        "(SELECT count(*) FROM ingestion_runs), (SELECT sum(coalesce(home_score,0)+coalesce(away_score,0)) FROM matches)"
    with engine.connect() as c:
        before = tuple(c.execute(text(q)).one())
    _evaluate(engine)  # vuelve a importar el mismo fixture (idempotente) y evalúa
    with engine.connect() as c:
        after = tuple(c.execute(text(q)).one())
    assert before[0] == after[0] and before[1] == after[1] and before[3] == after[3]


def test_truncated_results_are_detected_as_incomplete_evidence(engine: Engine) -> None:
    results = {r.case_id: r for r in _evaluate(engine, max_rows=1)}  # recorte agresivo
    failed = [r for r in results.values() if r.status == "fail"]
    assert failed and all("evidencia incompleta" in r.detail for r in failed)


class _FakeLive:
    """Simula un LLM real: pide una tool y responde con el texto dado. Sin red."""

    provider, model = "fake", "fake"

    def __init__(self, group_id: int, team_id: int, text: str) -> None:
        self._args = {"group_id": group_id, "team_id": team_id, "status": "finished"}
        self._text, self._called = text, False

    def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        from futsal.agent.llm import LLMResponse, ToolCall, Usage

        if not self._called:
            self._called = True
            return LLMResponse("", [ToolCall("x", "list_matches", self._args)], Usage(500, 20, 0, None))
        return LLMResponse(self._text, [], Usage(900, 60, 0, None))


def _live(engine: Engine, text_for: object) -> CaseResult:
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    dosa = truth.team_id("DOSA")
    assert dosa is not None
    text = text_for(truth, dosa) if callable(text_for) else str(text_for)  # type: ignore[operator]
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "live", _FakeLive(truth.group_id, dosa, text),
                                  only={"equipo_resultados"})

    return asyncio.run(go())[0]


def _correct(truth, dosa):  # type: ignore[no-untyped-def]
    return " ".join(f"{m.home} {m.hs}-{m.aws} {m.away}." for m in truth.of_team(dosa, "finished"))


def test_live_mode_scores_correct_wrong_and_presentation_separately(engine: Engine) -> None:
    ok = _live(engine, _correct)
    assert ok.status == "pass" and ok.answer_ok and ok.presentation_ok and ok.input_tokens == 1400
    assert not ok.tokens_estimated  # tokens informados por el proveedor, no estimados
    wrong = _live(engine, lambda t, d: _correct(t, d).replace("-", "-9"))
    assert wrong.status == "fail" and wrong.answer_ok is False and "respuesta incorrecta" in wrong.detail
    ugly = _live(engine, lambda t, d: _correct(t, d) + ' {"home_team_id": 3}')
    assert ugly.status == "pass" and ugly.answer_ok and ugly.presentation_ok is False  # presentación aparte
