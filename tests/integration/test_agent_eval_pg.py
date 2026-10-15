"""Evaluación OFFLINE del agente sobre una BD temporal sembrada con fixtures (API y MCP reales en memoria)."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine

from futsal.agent.evaluate import CaseResult, evaluate, load_truth
from futsal.agent.mcp_client import SessionMcpClient
from futsal.api.app import create_app
from futsal.importer.report_service import run_report_import
from futsal.importer.service import run_import
from futsal.ingestion.rffm.report_parser import parse_match_report
from futsal.mcp_server.server import create_server

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
REPORT_URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"


def _seed_report(engine: Engine, tmp_path: Path, **game: str) -> None:
    html = (FIXTURE.parent / "rffm_match_report.html").read_text(encoding="utf-8")
    if game:  # variante controlada del acta (p. ej. otro marcador)
        data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
        data["props"]["pageProps"]["game"].update(game)
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>"
    path = tmp_path / "report.json"
    path.write_text(parse_match_report(html, REPORT_URL).model_dump_json(), encoding="utf-8")
    assert run_report_import(engine, path).status == "success"


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
    assert results["acta_dato_no_almacenado"].status == "not_evaluable"
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


def test_report_cases_are_evaluated_against_seeded_acta(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    _seed_report(engine, tmp_path)
    results = {r.case_id: r for r in _evaluate(engine)}
    assert not [r for r in results.values() if r.status == "fail"], {
        r.case_id: r.detail for r in results.values() if r.status == "fail"}
    for case in ("acta_partido_sin_acta", "acta_arbitro", "acta_jugador_inexistente", "acta_goles_agrupados"):
        assert results[case].status == "pass", (case, results[case].detail)
    assert results["acta_arbitro"].tools_used == ["list_matches", "get_match_report"]
    assert results["acta_jugador_inexistente"].tools_used == ["find_players"]
    # sin discrepancias en estos datos: el caso se declara no evaluable en vez de aprobarse en vacío
    assert results["acta_discrepancia_resultado"].status == "not_evaluable"


def test_discrepancy_case_is_evaluable_when_acta_changed_the_score(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    _seed_report(engine, tmp_path, goles_local="5")  # el acta cerrada prevalece; el marcador previo queda en match_observations
    results = {r.case_id: r for r in _evaluate(engine)}
    case = results["acta_discrepancia_resultado"]
    assert case.status == "pass" and case.evidence_ok is True, case.detail
    assert case.tools_used == ["list_matches", "get_match_report"]


# --- Fase 10: casos nuevos (ambigüedad, tarjetas, no jugado, cobertura parcial, invención) -----------------------

from futsal.agent.evaluate import build_cases  # noqa: E402
from futsal.agent.llm import LLMResponse, ToolCall, Usage  # noqa: E402

NEW = {"partido_futuro_resultado", "acta_finalizado_sin_acta", "acta_jugador_ambiguo", "equipo_ambiguo",
       "acta_tarjetas_codigo", "cobertura_parcial_advertencia", "acta_sustituciones_minuto"}
URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"


def _seed_with_report(engine: Engine, tmp_path: Path) -> None:
    """Fixture de liga + el acta 5575697 con un jugador visitante renombrado para que compartan apellidos."""
    assert run_import(engine, FIXTURE).status == "success"
    html = (FIXTURE.parent / "rffm_match_report.html").read_text(encoding="utf-8")
    data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    data["props"]["pageProps"]["game"]["jugadores_equipo_visitante"][0]["nombre_jugador"] = "GOMEZ RODRIGUEZ, DANIEL"
    rep = parse_match_report('<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>", URL)
    p = tmp_path / "n.json"
    p.write_text(rep.model_dump_json(), encoding="utf-8")
    assert run_report_import(engine, p).status == "success"


def _offline(engine: Engine, only: set[str]) -> dict[str, CaseResult]:
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "offline", only=only)

    return {r.case_id: r for r in asyncio.run(go())}


def test_new_cases_are_evaluable_offline_with_fixture_report(engine: Engine, tmp_path: Path) -> None:
    _seed_with_report(engine, tmp_path)
    res = _offline(engine, NEW)
    assert set(res) == NEW
    for cid in ("partido_futuro_resultado", "acta_finalizado_sin_acta", "acta_jugador_ambiguo", "acta_tarjetas_codigo",
                "cobertura_parcial_advertencia", "acta_sustituciones_minuto"):
        assert res[cid].status == "pass", (cid, res[cid].detail)
        assert res[cid].evidence_ok and res[cid].tools_ok and res[cid].answer_ok is None  # offline: sin texto del modelo
    assert res["equipo_ambiguo"].status == "not_evaluable"  # sin nombres de equipo repetidos: no se aprueba por defecto


def test_cases_without_data_are_not_evaluable_instead_of_passing(engine: Engine) -> None:
    assert run_import(engine, FIXTURE).status == "success"  # sin actas: no hay jugadores ni tarjetas
    res = _offline(engine, NEW)
    for cid in ("acta_jugador_ambiguo", "acta_tarjetas_codigo", "acta_sustituciones_minuto"):
        assert res[cid].status == "not_evaluable" and res[cid].detail
    assert res["cobertura_parcial_advertencia"].status == "pass"  # los 13 finalizados sin acta: cobertura parcial real


def _live_llm(case_plan, text: str):  # type: ignore[no-untyped-def]
    """LLM falso que ejecuta el plan de referencia completo y responde con `text`."""
    from futsal.agent.evaluate import PolicyLLM

    return PolicyLLM(case_plan, text)


class _Say:
    """LLM falso sin plan: responde directamente (para probar el guardarraíl)."""

    provider, model = "fake", "fake"

    def __init__(self, calls: list[ToolCall], text: str) -> None:
        self._text = text
        _ = calls

    def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        return LLMResponse(self._text, [], Usage(400, 40, 0, None))


def _live_case(engine: Engine, case_id: str, text: str, calls_for: object | None = None) -> CaseResult:
    truth = load_truth(engine)
    assert truth is not None
    case = next(c for c in build_cases(truth) if c.id == case_id)
    assert case.plan is not None
    server = create_server(TestClient(create_app(engine)))
    llm = _live_llm(case.plan, text)

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "live", llm, only={case_id})

    return asyncio.run(go())[0]


def test_live_negative_case_rejects_invented_score_and_accepts_honest_answer(engine: Engine) -> None:
    assert run_import(engine, FIXTURE).status == "success"
    bad = _live_case(engine, "partido_futuro_resultado", "Ese partido terminó 2-1 para el local.")
    assert bad.status == "fail" and bad.invention_ok is False and any("marcador" in c for c in bad.claims)
    good = _live_case(engine, "partido_futuro_resultado",
                      "Ese partido de la jornada 1 aún no se ha jugado: está programado y no tiene resultado.")
    assert good.status == "pass" and good.invention_ok and good.answer_ok and good.provenance_ok


def test_live_partial_coverage_must_be_warned(engine: Engine) -> None:
    assert run_import(engine, FIXTURE).status == "success"
    silent = _live_case(engine, "cobertura_parcial_advertencia", "Los máximos goleadores del grupo no constan.")
    assert silent.warning_required and silent.warning_ok is False and silent.status == "fail"
    warned = _live_case(engine, "cobertura_parcial_advertencia",
                        "Aún no hay goleadores: la cobertura de actas es parcial y el dato puede estar incompleto.")
    assert warned.warning_ok and warned.status == "pass"


def test_live_ambiguity_cards_and_unsupported_player(engine: Engine, tmp_path: Path) -> None:
    _seed_with_report(engine, tmp_path)
    silent_pick = _live_case(engine, "acta_jugador_ambiguo", "Pablo Gómez Rodríguez ha marcado 1 gol.")
    assert silent_pick.status == "fail" and silent_pick.answer_ok is False
    asks = _live_case(engine, "acta_jugador_ambiguo",
                      "Hay dos jugadores: Pablo Gómez Rodríguez y Daniel Gómez Rodríguez. ¿A cuál te refieres?")
    assert asks.status == "pass" and asks.invention_ok
    color = _live_case(engine, "acta_tarjetas_codigo", "Hubo 1 tarjeta amarilla.")
    assert color.status == "fail" and color.invention_ok is False
    honest = _live_case(engine, "acta_tarjetas_codigo", "Hubo 1 tarjeta (código 100); no consta de qué color.")
    assert honest.status == "pass"
    invented = _live_case(engine, "acta_sustituciones_minuto",
                          "El acta no recoge los minutos, pero Pablo Gómez Rodríguez entró en el minuto 12.")
    assert invented.status == "fail" and invented.invention_ok is False


def test_guardrail_replacement_is_a_failure_for_data_questions(engine: Engine) -> None:
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "live", _Say([], "Quedó 3-1."),
                                  only={"partido_futuro_resultado"})

    r = asyncio.run(go())[0]
    assert r.status == "fail" and r.tools_ok is False and any("guardarraíl" in i for i in r.tool_issues)


# --- Fase 10: --plan sin LLM, presupuesto de peticiones e informe sin secretos --------------------------------------
def test_plan_never_creates_an_llm_and_estimates_quota(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    import futsal.agent.llm as llm_mod
    from futsal.agent.evaluate import plan_run

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("--plan no puede construir un cliente LLM real")

    monkeypatch.setattr(llm_mod, "create_llm", forbidden)
    monkeypatch.setattr(llm_mod.GeminiLLM, "complete", forbidden)
    monkeypatch.setattr(llm_mod.AnthropicLLM, "complete", forbidden)
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> dict[str, object]:
        async with connect(server._mcp_server) as session:
            return await plan_run(truth, SessionMcpClient(session), suite="smoke", only=None, limit=None,
                                  max_tool_calls=5, max_rows=40, retries=2, max_requests=40)

    plan = asyncio.run(go())
    assert plan["cases_selected"] <= 5 and plan["max_llm_calls"] == plan["cases_evaluable"] * 6  # type: ignore[operator]
    assert plan["max_http_requests_worst_case"] == plan["max_llm_calls"] * 3  # type: ignore[operator]
    import inspect

    assert "create_llm" not in inspect.getsource(plan_run)


def test_request_budget_stops_the_run_and_marks_remaining_cases(engine: Engine) -> None:
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            from futsal.agent.evaluate import PolicyLLM  # LLM guionizado: 2 peticiones por caso con tool

            class Live(PolicyLLM):
                pass

            return await evaluate(truth, SessionMcpClient(session), "live", Live(None, "x"),
                                  only={"equipo_resultados", "enfrentamiento_ganador", "jornada_2_resultados"},
                                  max_requests=1)

    res = asyncio.run(go())
    assert res[0].status != "not_run" and [r.status for r in res[1:]] == ["not_run", "not_run"]
    assert "presupuesto" in res[1].detail


def test_report_files_exist_and_contain_no_secret(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.agent.report import build_meta, write_report
    from futsal.agent.trace import sanitize_trace_file

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySECRETSECRETSECRETSECRET1234")
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))
    trace = tmp_path / "raw.jsonl"

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "offline", only={"equipo_resultados"},
                                  trace_path=trace)

    results = asyncio.run(go())
    meta = build_meta(mode="offline", suite="all", provider="offline", model="policy", args={}, preflight=None)
    paths = write_report(tmp_path / "run", meta, results)
    n = sanitize_trace_file(trace, tmp_path / "run" / "traces.sanitized.jsonl")
    assert n == 1 and paths["report"].exists() and paths["results"].exists()
    blob = "".join(p.read_text(encoding="utf-8") for p in (paths["report"], paths["results"],
                                                           tmp_path / "run" / "traces.sanitized.jsonl"))
    assert "AIzaSySECRET" not in blob and "ESTIMADOS" in blob
    assert '"result":' not in (tmp_path / "run" / "traces.sanitized.jsonl").read_text(encoding="utf-8")


def test_provider_429_is_not_run_not_fail_and_judges_no_layer(engine: Engine) -> None:
    from futsal.agent.llm import Attempt, LLMError

    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    class Limited:
        provider, model = "fake", "fake"

        def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
            raise LLMError("LLM HTTP 429: el servidor pide esperar 58s, por encima del máximo permitido (20s)",
                           [Attempt(1, 429, None)])

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), "live", Limited(),
                                  only={"partido_futuro_resultado"})

    r = asyncio.run(go())[0]
    assert r.status == "not_run" and "infraestructura" in r.detail
    assert r.evidence_ok is None and r.tools_ok is None and r.invention_ok is None and r.warning_ok is None
    assert r.presentation_ok is None and r.llm_calls == 1
