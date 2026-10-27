"""Fase 17: casos de auditoría (grupos, estados de las tablas oficiales y fuentes combinadas) sobre una BD temporal sembrada.

Sin red ni LLM real: el modo offline sigue la ruta de referencia por el MCP/API reales; el «live» usa un LLM guionizado que simula
respuestas buenas y malas para comprobar que el evaluador las distingue (fuente incorrecta, invención) y las marca como críticas.
"""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.agent.evaluate import CaseResult, Truth, build_cases, evaluate, load_truth
from futsal.agent.llm import LLMResponse, ToolCall, Usage
from futsal.agent.mcp_client import SessionMcpClient
from futsal.agent.report import render_markdown, results_payload
from futsal.api.app import create_app
from futsal.db.models import Competition, CompetitionGroup, Match, Round, Team
from futsal.db.table_models import (
    ScorerRow,
    ScorerSnapshot,
    StandingsRow,
    StandingsSnapshot,
    TableIngestAttempt,
)
from futsal.importer.service import run_import
from futsal.mcp_server.server import create_server

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
NOW = datetime(2099, 10, 20, 12, 0, tzinfo=UTC)
NEW_IDS = {
    "tabla_clasificacion_posicion_equipo", "tabla_clasificacion_comparacion", "tabla_clasificacion_otro_grupo",
    "tabla_clasificacion_eliminatorias", "tabla_clasificacion_no_ingerida", "tabla_clasificacion_fallo_ingesta",
    "tabla_clasificacion_incompleta", "tabla_goleadores_vacia", "tabla_goleadores_no_ingerida",
    "tabla_clasificacion_jornada_no_ingerida", "tabla_goleadores_top_n", "tabla_goleadores_otro_grupo",
    "tabla_goleadores_nombre_repetido", "combinada_lider_ultimo_resultado", "combinada_lider_proximo_partido",
    "combinada_goleador_y_puesto_de_su_equipo", "resultados_otro_grupo"}


def _standings(s: Session, group_id: int, rnd: int, teams: list[Team], status: str = "ok") -> None:
    snap = StandingsSnapshot(group_id=group_id, round_number=rnd, current_round=rnd, status=status, content_hash=f"h{group_id}-{rnd}",
                             row_count=len(teams), expected_teams=len(teams), source_url="https://example.invalid/clasificaciones",
                             fetched_at=NOW, meta={})
    s.add(snap)
    s.flush()
    for pos, team in enumerate(teams, 1):
        s.add(StandingsRow(snapshot_id=snap.id, position=pos, team_external_id=team.external_id, team_id=team.id,
                           team_name=team.name or "", played=rnd, won=rnd, drawn=0, lost=0, goals_for=3 * rnd - pos,
                           goals_against=pos, points=3 * rnd - pos + 1))


def _scorers(s: Session, group_id: int, rows: list[tuple[str, Team, int]], status: str = "ok") -> None:
    snap = ScorerSnapshot(group_id=group_id, status=status, content_hash=f"s{group_id}", row_count=len(rows),
                          goals_total=sum(r[2] for r in rows), source_url="https://example.invalid/goleadores", fetched_at=NOW, meta={})
    s.add(snap)
    s.flush()
    for order, (name, team, goals) in enumerate(rows):
        s.add(ScorerRow(snapshot_id=snap.id, row_order=order, player_external_id=f"p{group_id}{order}", player_name=name,
                        team_external_id=team.external_id, team_id=team.id, team_name=team.name or "", goals=goals, matches_played=2))


def _attempt(s: Session, group_id: int, kind: str, outcome: str) -> None:
    s.add(TableIngestAttempt(group_id=group_id, kind=kind, outcome=outcome, http_requests=1, attempted_at=NOW))


def seed(engine: Engine, *, other_competition: bool = True) -> None:
    """Liga objetivo con clasificación (jornadas 1 y 4) y goleadores, y grupos de otra competición en cada estado posible."""
    assert run_import(engine, FIXTURE).status == "success"
    with Session(engine) as s, s.begin():
        main = s.scalars(select(CompetitionGroup).where(CompetitionGroup.external_id == "26738245")).one()
        comp = s.get(Competition, main.competition_id)
        assert comp is not None
        teams = list(s.scalars(select(Team).where(Team.group_id == main.id).order_by(Team.id)))[:6]
        for rnd in (1, 4):  # la jornada 2 existe en el calendario pero su clasificación no se ingirió
            _standings(s, main.id, rnd, teams)
        _scorers(s, main.id, [("AAA UNO, ANA", teams[0], 5), ("BBB DOS, LUIS", teams[1], 4), ("CCC TRES, PAU", teams[2], 3),
                              ("DDD CUATRO, EVA", teams[3], 2), ("EEE CINCO, JOSE", teams[4], 1), ("FFF SEIS, MAR", teams[5], 1),
                              ("AAA UNO, ANA", teams[5], 1)])  # mismo nombre, otro jugador
        if not other_competition:
            return
        other = Competition(external_id="90000020", season_id=comp.season_id, name="LIGA OTRA FS")
        cup = Competition(external_id="90000021", season_id=comp.season_id, name="COPA OTRA FS")
        s.add_all([other, cup])
        s.flush()
        groups = {n: CompetitionGroup(external_id=f"9000003{i}", competition_id=(cup if n == "cup" else other).id,
                                      name="Eliminatorias" if n == "cup" else f"Grupo {n}")
                  for i, n in enumerate(["A", "B", "C", "D", "cup"])}
        s.add_all(groups.values())
        s.flush()
        a, b, c, d, k = (groups[n].id for n in ("A", "B", "C", "D", "cup"))  # D: grupo importado sin ninguna tabla ingerida
        mates = {gid: [Team(external_id=f"9{gid}{i}", group_id=gid, name=f"CLUB {chr(65 + i)} {gid}") for i in range(3)] for gid in (a, b, c, d, k)}
        for ts in mates.values():
            s.add_all(ts)
        s.flush()
        rnd = Round(external_id="1", group_id=a, visible_label="1", visible_number=1, scheduled_date=date(2099, 1, 1))
        s.add(rnd)
        s.flush()
        s.add(Match(external_id="9000001", group_id=a, round_id=rnd.id, home_team_id=mates[a][0].id, away_team_id=mates[a][1].id,
                    scheduled_date=date(2099, 1, 1), timezone="Europe/Madrid", status="finished", home_score=4, away_score=2))
        _standings(s, a, 3, mates[a])
        _scorers(s, a, [("ZZZ ALFA, IVAN", mates[a][0], 6), ("YYY BETA, OLGA", mates[a][1], 2), ("XXX GAMMA, RUT", mates[a][2], 1)])
        _standings(s, b, 3, mates[b][:2], status="incomplete")  # 2 de 3 equipos: tabla parcial
        _attempt(s, b, "scorers", "failed")
        _attempt(s, c, "standings", "failed")  # sin captura previa: error de ingesta
        _attempt(s, k, "standings", "not_applicable")  # eliminatorias
        _scorers(s, k, [], status="empty")  # tabla válida sin goleadores
        s.flush()


def _run(engine: Engine, mode: str, llm: Any = None, **kw: Any) -> tuple[Truth, list[CaseResult]]:
    truth = load_truth(engine)
    assert truth is not None
    server = create_server(TestClient(create_app(engine)))

    async def go() -> list[CaseResult]:
        async with connect(server._mcp_server) as session:
            return await evaluate(truth, SessionMcpClient(session), mode, llm, **kw)  # type: ignore[arg-type]

    return truth, asyncio.run(go())


class _Script:
    """LLM guionizado: ejecuta las llamadas dadas y responde con el texto dado."""

    provider, model = "fake", "fake"

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], text: str) -> None:
        self._calls, self._text, self._done = calls, text, False

    def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        if not self._done and self._calls:
            self._done = True
            return LLMResponse("", [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(self._calls)], Usage(500, 20, 0, None))
        return LLMResponse(self._text, [], Usage(900, 60, 0, None))


def test_new_cases_are_evaluable_and_pass_offline_in_every_table_state(engine: Engine) -> None:
    seed(engine)
    _, results = _run(engine, "offline")
    by_id = {r.case_id: r for r in results}
    assert NEW_IDS <= by_id.keys()
    bad = {i: (by_id[i].status, by_id[i].detail) for i in NEW_IDS if by_id[i].status != "pass"}
    assert not bad, bad
    assert {i: by_id[i].expect for i in ("tabla_clasificacion_eliminatorias", "tabla_clasificacion_no_ingerida", "tabla_clasificacion_fallo_ingesta",
                                         "tabla_clasificacion_incompleta", "tabla_goleadores_vacia", "tabla_clasificacion_jornada_no_ingerida")} == {
        "tabla_clasificacion_eliminatorias": "no_data", "tabla_clasificacion_no_ingerida": "not_ingested",
        "tabla_clasificacion_fallo_ingesta": "query_error", "tabla_clasificacion_incompleta": "partial",
        "tabla_goleadores_vacia": "no_data", "tabla_clasificacion_jornada_no_ingerida": "not_ingested"}
    assert by_id["tabla_goleadores_por_jornada_no_verificable"].expect == "unverifiable"
    assert by_id["tabla_goleadores_por_jornada_no_verificable"].status == "not_evaluable"  # sin LLM real no se inventa el veredicto


def test_cases_need_their_preconditions_and_say_which_is_missing(engine: Engine) -> None:
    seed(engine, other_competition=False)
    _, results = _run(engine, "offline")
    by_id = {r.case_id: r for r in results}
    assert by_id["tabla_clasificacion_eliminatorias"].status == "not_evaluable" and "not_applicable" in by_id["tabla_clasificacion_eliminatorias"].detail
    assert by_id["tabla_clasificacion_otro_grupo"].status == "not_evaluable" and "solo hay un grupo" in by_id["tabla_clasificacion_otro_grupo"].detail
    assert by_id["resultados_otro_grupo"].status == "not_evaluable"
    assert by_id["tabla_clasificacion_posicion_equipo"].status == "pass"  # los casos del grupo principal no dependen del resto


def test_target_group_is_chosen_by_its_external_id_not_by_lowest_id(engine: Engine) -> None:
    from futsal.db.models import Season

    with Session(engine) as s, s.begin():  # un grupo AJENO con id menor, importado antes que el objetivo
        season = Season(external_id="22", name="2026-27")
        s.add(season)
        s.flush()
        comp = Competition(external_id="90000050", season_id=season.id, name="OTRA")
        s.add(comp)
        s.flush()
        s.add(CompetitionGroup(external_id="90000051", competition_id=comp.id, name="Grupo previo"))
    assert run_import(engine, FIXTURE).status == "success"
    truth = load_truth(engine)
    assert truth is not None and truth.group_name == "Grupo 2" and truth.matches


def test_reconstructing_the_table_from_results_is_a_critical_wrong_source_failure(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    answer = "Lidera el primer equipo con 6 puntos según los resultados."
    calls = [("list_matches", {"group_id": truth.group_id, "status": "finished"})]
    _, results = _run(engine, "live", _Script(calls, answer), only={"tabla_clasificacion_posicion_equipo"})
    r = results[0]
    assert r.status == "fail" and r.tools_ok is False and "list_matches" in r.unexpected_tools
    assert r.critical and "fuente incorrecta" in r.critical[0]


def test_invented_standings_for_a_cup_group_are_critical_and_honest_answers_pass(engine: Engine) -> None:
    seed(engine)
    case = next(c for c in build_cases(load_truth(engine)) if c.id == "tabla_clasificacion_eliminatorias")  # type: ignore[arg-type]
    assert case.plan is not None
    calls = [("get_standings", {"group_id": next(g.group_id for g in load_truth(engine).table_groups  # type: ignore[union-attr]
                                                  if g.standings_status == "not_applicable")})]
    invented = _run(engine, "live", _Script(calls, "El líder va primero con 9 puntos y ganó 4-2 al segundo."),
                    only={"tabla_clasificacion_eliminatorias"})[1][0]
    assert invented.status == "fail" and invented.invention_ok is False and invented.critical
    honest = _run(engine, "live", _Script(calls, "Es un grupo de eliminatorias: la RFFM no publica clasificación, así que no consta."),
                  only={"tabla_clasificacion_eliminatorias"})[1][0]
    assert honest.status == "pass" and honest.critical == [] and honest.expect == "no_data"


def test_position_answers_must_state_the_rank_not_just_a_number(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    case = next(c for c in build_cases(truth) if c.id == "tabla_clasificacion_posicion_equipo")
    team = case.facts[0].names[0]  # type: ignore[index]
    pos, pts = case.facts[0].number, case.facts[1].number  # type: ignore[index]
    calls = [("get_standings", {"group_id": truth.group_id})]
    good = _run(engine, "live", _Script(calls, f"{team} ocupa el puesto {pos} con {pts} puntos."), only={case.id})[1][0]
    wrong = _run(engine, "live", _Script(calls, f"{team} tiene {pts} puntos y {pos + 3} victorias."), only={case.id})[1][0]
    assert good.status == "pass" and wrong.status == "fail" and wrong.answer_ok is False


def test_report_and_results_json_carry_the_audit_with_critical_errors_first(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    calls = [("list_matches", {"group_id": truth.group_id, "status": "finished"})]
    _, results = _run(engine, "live", _Script(calls, "Va primero con 6 puntos."), only={"tabla_clasificacion_posicion_equipo"})
    meta = {"mode": "live", "suite": "x", "provider": "fake", "model": "fake", "generated_at": "t", "commit": "c",
            "args": {"price_per_mtok": [1.0, 2.0]}}
    payload = results_payload(meta, results)
    assert payload["audit"]["critical_count"] == 1 and payload["audit"]["by_category"]["B"]["fail"] == 1
    assert payload["audit"]["tokens"]["cost_usd"] is not None and not payload["audit"]["tokens"]["estimated"]
    md = render_markdown(meta, results)
    assert "Errores críticos (invención o fuente incorrecta): 1" in md and "fuente incorrecta" in md
    assert md.index("Errores críticos") < md.index("## Veredictos por capa")


def test_api_error_turned_into_table_figures_is_a_critical_invention(engine: Engine) -> None:
    seed(engine)
    calls = [("get_standings", {"group_id": 987654})]  # grupo inexistente: la API responde 404 y el MCP devuelve un error controlado
    r = _run(engine, "live", _Script(calls, "El líder tiene 12 puntos y el segundo 10."), only={"tabla_clasificacion_lider"})[1][0]
    assert r.status == "fail" and any("devolvió error" in i for i in r.tool_issues)
    assert r.invention_ok is False and r.critical and any("12" in c for c in r.critical[0].split(";"))
    honest = _run(engine, "live", _Script(calls, "No he podido consultar la clasificación: el grupo no consta."),
                  only={"tabla_clasificacion_lider"})[1][0]
    assert honest.invention_ok is True and honest.critical == []  # el error se reconoce; sigue siendo fallo de herramienta, no de invención


def test_repeated_and_looping_tool_calls_are_detected(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    same = ("get_standings", {"group_id": truth.group_id})
    dup = _run(engine, "live", _Script([same, same], "Va primero."), only={"tabla_clasificacion_lider"})[1][0]
    assert dup.status == "fail" and any("llamada repetida" in i for i in dup.tool_issues)
    loop = _run(engine, "live", _Script([same] * 8, "Va primero."), only={"tabla_clasificacion_lider"})[1][0]
    assert loop.status == "fail" and "límite" in loop.detail


def test_progress_lines_report_each_case_and_do_not_change_the_results(engine: Engine) -> None:
    seed(engine)
    lines: list[str] = []
    only = {"tabla_clasificacion_lider", "tabla_clasificacion_eliminatorias"}
    _, with_progress = _run(engine, "offline", only=only, on_progress=lines.append)
    _, silent = _run(engine, "offline", only=only)
    assert [(r.case_id, r.status) for r in with_progress] == [(r.case_id, r.status) for r in silent]
    assert lines[0].startswith("[1/2] ") and lines[2].startswith("[2/2] ") and len(lines) == 4
    assert "-> PASS" in lines[1] and "llamadas LLM" in lines[1] and "peticiones al LLM usadas" in lines[0]


def test_with_several_groups_old_questions_name_the_scope_and_discovery_tools_are_allowed(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None and truth.groups_total > 1
    cases = {c.id: c for c in build_cases(truth)}
    scope = f"En {truth.competition} ({truth.group_name}), "
    assert cases["tabla_clasificacion_lider"].question == scope + "¿quién lidera la clasificación del grupo y con cuántos puntos?"
    assert cases["equipo_resultados"].question.startswith(scope)
    assert "find_teams" in cases["tabla_clasificacion_lider"].allowed_tools and "find_teams" not in cases["fuera_de_alcance"].allowed_tools
    # el caso del próximo partido del líder ya no exige los puntos, que la pregunta no pide
    facts = cases["combinada_lider_proximo_partido"].facts
    assert facts is not None and [f.kind for f in facts] == ["match"]
    _, results = _run(engine, "offline", only={"tabla_clasificacion_lider", "combinada_lider_proximo_partido"})
    assert [r.status for r in results] == ["pass", "pass"]


# --- resultados_otro_grupo: get_match_report permitido SOLO en ese caso (comprobación del acta del último partido) -----------------
def _other_group_call(truth: Truth) -> tuple[Any, list[tuple[str, dict[str, Any]]], Any]:
    case = next(c for c in build_cases(truth) if c.id == "resultados_otro_grupo")
    m = case.facts[0].matches[0]  # type: ignore[index]
    team = m.home_id
    calls = [("list_matches", {"group_id": truth.other.group_id, "team_id": team, "status": "finished"}),  # type: ignore[union-attr]
             ("get_match_report", {"match_id": m.id})]
    return case, calls, m


def test_other_group_case_accepts_the_acta_check_only_when_the_functional_answer_is_right(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None and truth.other is not None
    case, calls, m = _other_group_call(truth)
    assert "get_match_report" in case.allowed_tools and case.max_calls == 4
    warned = f"Terminó {m.home} {m.hs} - {m.aws} {m.away}. El acta aún no está descargada."
    ok = _run(engine, "live", _Script(calls, warned), only={case.id})[1][0]
    assert ok.status == "pass" and ok.tools_ok is True and ok.tool_issues == [], (ok.detail, ok.tool_issues, ok.claims, ok.answer)
    # el control de advertencia sigue activo: con la tool del acta y el acta sin importar, no avisar es un fallo aunque el marcador sea correcto
    silent = _run(engine, "live", _Script(calls, f"Terminó {m.home} {m.hs} - {m.aws} {m.away}."), only={case.id})[1][0]
    assert silent.status == "fail" and silent.answer_ok is True and silent.warning_ok is False and silent.tools_ok is True
    wrong = _run(engine, "live", _Script(calls, f"Terminó {m.home} {m.hs + 3} - {m.aws} {m.away}. El acta aún no está descargada."),
                 only={case.id})[1][0]
    assert wrong.status == "fail" and wrong.tools_ok is True and wrong.answer_ok is False            # la tool permitida no salva una cifra errónea


def test_other_group_case_still_enforces_call_limit_and_forbids_other_sources(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None and truth.other is not None
    case, calls, m = _other_group_call(truth)
    right = f"Terminó {m.home} {m.hs} - {m.aws} {m.away}. El acta aún no está descargada."
    too_many = calls + [("get_match", {"match_id": m.id}), ("list_teams", {"group_id": truth.other.group_id}),
                        ("find_teams", {"name": "CLUB"})]
    r = _run(engine, "live", _Script(too_many, right), only={case.id})[1][0]
    assert r.status == "fail" and any("llamadas (máximo razonable 4)" in i for i in r.tool_issues)
    other_source = calls + [("top_scorers", {"group_id": truth.other.group_id})]                      # no es una lectura equivalente
    r2 = _run(engine, "live", _Script(other_source, right), only={case.id})[1][0]
    assert r2.status == "fail" and "top_scorers" in r2.unexpected_tools
    redundant = calls + [calls[1]]
    r3 = _run(engine, "live", _Script(redundant, right), only={case.id})[1][0]
    assert r3.status == "fail" and any("llamada repetida" in i for i in r3.tool_issues)


def test_get_match_report_was_not_allowed_globally(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    cases = {c.id: c for c in build_cases(truth)}
    for cid in ("equipo_recuento", "partido_mas_goles", "recuento_global", "jornada_2_resultados", "tabla_clasificacion_lider",
                "combinada_lider_ultimo_resultado", "tabla_goleadores_top_n", "partido_futuro_resultado"):
        if cid in cases:
            assert "get_match_report" not in cases[cid].allowed_tools, cid
    assert "get_match_report" in cases["enfrentamiento_ganador"].allowed_tools                         # el precedente que ya existía


def test_finished_without_acta_case_accepts_get_match_but_still_rejects_other_sources_and_wrong_scores(engine: Engine) -> None:
    seed(engine)
    truth = load_truth(engine)
    assert truth is not None
    case = next(c for c in build_cases(truth) if c.id == "acta_finalizado_sin_acta")
    m = case.facts[0].matches[0]  # type: ignore[index]
    calls = [("list_matches", {"group_id": truth.group_id, "round_id": truth.rounds[m.round_number]}), ("get_match", {"match_id": m.id}),
             ("get_match_report", {"match_id": m.id})]
    text = f"{m.home} {m.hs}-{m.aws} {m.away}: el acta aún no está descargada ni importada, por lo que no constan los goleadores."
    ok = _run(engine, "live", _Script(calls, text), only={case.id})[1][0]
    assert ok.status == "pass" and ok.tools_ok is True, (ok.detail, ok.tool_issues)
    other = _run(engine, "live", _Script(calls + [("top_scorers", {"group_id": truth.group_id})], text), only={case.id})[1][0]
    assert other.status == "fail" and "top_scorers" in other.unexpected_tools
