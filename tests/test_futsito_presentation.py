"""Futsito, bloques 2-5: foco de la conversación, fuentes con frescura y correspondencia, visuales estructurados y preguntas sugeridas.
Agente real + LLM guionizado + MCP simulado (sin cuota de Gemini ni PostgreSQL)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from futsal.agent import presentation as pres
from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import Turn
from futsal.agent.web import AgentRunner, create_chat_app

CATALOG = {"competitions": [
    {"id": 1, "name": "PRIMERA AFICIONADO FS", "groups": [{"id": 11, "name": "Grupo 1"}, {"id": 12, "name": "Grupo 2"}]},
    {"id": 2, "name": "PRIMERA JUVENIL FS", "groups": [{"id": 21, "name": "Grupo 1"}]}]}
STANDINGS = {"status": "ok", "competition": "PRIMERA AFICIONADO FS", "group": "Grupo 2", "group_id": 12,
             "source": {"round": 5, "captured_at": "2026-10-28T09:00:00+00:00"},
             "standings": [
                 {"position": 1, "team_id": 5, "team": "C.D. DOSA", "played": 5, "won": 4, "drawn": 1, "lost": 0, "goals_for": 20,
                  "goals_against": 8, "goal_difference": 12, "points": 13},
                 {"position": 2, "team_id": 6, "team": "FS ALCALA", "played": 5, "won": 3, "drawn": 1, "lost": 1, "goals_for": 15,
                  "goals_against": 10, "goal_difference": 5, "points": 10}]}
SCORERS = {"status": "ok", "competition": "PRIMERA AFICIONADO FS", "group": "Grupo 2", "group_id": 12,
           "source": {"captured_at": "2026-10-28T09:00:00+00:00"},
           "scorers": [{"rank": 1, "player": "Juan Pérez", "player_id": 7, "team": "C.D. DOSA", "goals": 9},
                       {"rank": 2, "player": "Luis Gil", "player_id": 8, "team": "FS ALCALA", "goals": 7}]}
TEAMS = {"teams": [{"id": 5, "name": "C.D. DOSA", "group_id": 12, "group": "Grupo 2", "competition": "PRIMERA AFICIONADO FS"}]}
MATCHES = {"matches": [
    {"id": 1, "round_number": 1, "home_team": "C.D. DOSA", "away_team": "FS ALCALA", "date": "2026-10-03", "status": "finished",
     "home_score": 3, "away_score": 1},
    {"id": 2, "round_number": 2, "home_team": "X", "away_team": "C.D. DOSA", "date": "2026-10-10", "status": "finished",
     "home_score": 2, "away_score": 2},
    {"id": 3, "round_number": 3, "home_team": "C.D. DOSA", "away_team": "Y", "date": "2026-10-17", "status": "finished",
     "home_score": 0, "away_score": 1}], "count": 3}
NEXT = {"matches": [
    {"id": 4, "round_number": 6, "home_team": "C.D. DOSA", "away_team": "Z", "date": "2026-11-01", "status": "scheduled"},
    {"id": 5, "round_number": 7, "home_team": "W", "away_team": "C.D. DOSA", "date": "2026-11-08", "status": "scheduled", "past_due": True},
    {"id": 6, "round_number": 8, "home_team": "C.D. DOSA", "away_team": "V", "date": None, "status": "scheduled"}], "count": 30}
RAG = {"hits": [
    {"document": "Reglas de Juego FIFA", "doc_key": "fifa", "rule": 7, "rule_title": "Duración", "pages": "31",
     "text": "El partido durará dos tiempos iguales de 20 minutos cada uno."},
    {"document": "Reglas de Juego FIFA", "doc_key": "fifa", "rule": 12, "rule_title": "Faltas", "pages": "55",
     "text": "Las faltas se sancionan con tiro libre directo o indirecto."}]}


def _pl(team: str, name: str, shirt: int, lineup: str, gk: bool = False, captain: bool = False) -> dict[str, Any]:
    return {"team": team, "player_id": shirt, "name": name, "shirt": shirt, "lineup": lineup, "captain": captain, **({"goalkeeper": True} if gk else {})}


def _card(team: str, name: str, minute: int, code: str, second: str) -> dict[str, Any]:
    from futsal.domain.cards import card_kind  # el significado lo calcula la API; aquí se reproduce su salida

    return {"type": "card", "team": team, "player_id": minute, "player": name, "minute": minute, "source_code": code, "second_yellow": second,
            "card_kind": card_kind(code, second)}


REPORT = {"report_available": True, "match": {"match_id": 9, "round_number": 4, "home_team": "C.D. DOSA", "away_team": "FS ALCALA",
                                              "status": "finished", "home_score": 3, "away_score": 1},
          "officials": [{"name": "INIESTA DEL AMO, EDUARDO HUGO", "role": "Árbitro"}, {"name": "LARRUBIA PAJARES, JAIME", "role": "Árbitro"},
                        {"name": "ORTIZ PINILLOS, ARANTXA", "role": "Árbitro asistente"}],
          "goals": [{"type": "goal", "team": "C.D. DOSA", "player": "PEREZ LOPEZ, JUAN", "minute": 3},
                    {"type": "goal", "team": "FS ALCALA", "player": "MARTIN CANO, LEO", "minute": 12},
                    {"type": "own_goal", "team": "FS ALCALA", "player": "OTRO UNO, A", "minute": 20},
                    {"type": "goal", "team": "C.D. DOSA", "player": "RUIZ SOTO, ANA", "minute": 31}],
          "cards": [_card("C.D. DOSA", "GIL MORA, PEDRO", 35, "177", "0"), _card("FS ALCALA", "OTRO DOS, B", 30, "100", "1"),
                    _card("C.D. DOSA", "PEREZ LOPEZ, JUAN", 14, "100", "0"), _card("FS ALCALA", "OTRO DOS, B", 9, "100", "0"),
                    _card("C.D. DOSA", "RUIZ SOTO, ANA", 24, "101", "0")],
          "players": [_pl("C.D. DOSA", "SUPLENTE GARCIA, LUIS", 20, "substitute"), _pl("C.D. DOSA", "PEREZ LOPEZ, JUAN", 10, "starter", captain=True),
                      _pl("C.D. DOSA", "APARICIO JIMENEZ, JAVIER", 1, "starter", gk=True), _pl("C.D. DOSA", "RUIZ SOTO, ANA", 7, "starter"),
                      _pl("C.D. DOSA", "GIL MORA, PEDRO", 5, "starter"), _pl("C.D. DOSA", "NUÑEZ DIAZ, IVAN", 9, "starter"),
                      _pl("C.D. DOSA", "REYES VEGA, SARA", 13, "substitute", gk=True),
                      _pl("FS ALCALA", "MARTIN CANO, LEO", 1, "starter", gk=True), _pl("FS ALCALA", "OTRO UNO, A", 2, "starter"),
                      _pl("FS ALCALA", "OTRO DOS, B", 3, "starter"), _pl("FS ALCALA", "OTRO TRES, C", 4, "starter"),
                      _pl("FS ALCALA", "OTRO CUATRO, D", 5, "starter"), _pl("FS ALCALA", "BANQUILLO UNO, E", 6, "substitute")]}


REPORTS: dict[str, dict[str, Any]] = {"current": REPORT}  # acta que devuelve el MCP simulado (los tests la sustituyen)


class Mcp:
    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "find_teams", "get_standings", "get_scorer_table", "list_matches", "search_rules", "get_match_report")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        data = {"list_competitions": CATALOG, "find_teams": TEAMS, "get_standings": STANDINGS, "get_scorer_table": SCORERS,
                "list_matches": NEXT if arguments.get("status") == "scheduled" else MATCHES, "search_rules": RAG,
                "get_match_report": REPORTS["current"]}.get(name, {})
        return False, json.dumps(data)


class Script:
    provider, model = "fake", "fake"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps, self.seen = list(steps), []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(list(messages))
        return self.steps.pop(0)


def call(tool: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall(f"c-{tool}", tool, args)])


def run(question: str, steps: list[LLMResponse], history: list[Turn] | None = None, focus: dict[str, Any] | None = None) -> tuple[Any, Script]:
    llm = Script(steps)
    return asyncio.run(Agent(llm, Mcp(), 5, trace_results=True).run(question, history or [], focus)), llm


def present(question: str, steps: list[LLMResponse], freshness: dict[int, Any] | None = None, asked: list[str] | None = None,
            prev: dict[str, str] | None = None) -> Any:
    from futsal.agent.web import build_response

    res, _ = run(question, steps)
    return build_response(res, 0, question, prev or {}, asked or [], freshness or {})


# --- Bloque 2: foco ----------------------------------------------------------------------------------------------------------------
def test_focus_comes_from_verified_tool_calls_not_from_text() -> None:
    steps = [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5, status="finished"), LLMResponse("Resultados.", [])]
    r = present("¿Cómo ha quedado el DOSA?", steps)
    assert r.focus == {"team": "C.D. DOSA", "competition": "PRIMERA AFICIONADO FS", "group": "Grupo 2"}


def test_top_scorer_question_sets_player_and_team_focus() -> None:
    r = present("¿Quién es el máximo goleador?", [call("get_scorer_table", group_id=12), LLMResponse("Juan Pérez, 9 goles.", [])])
    assert r.focus["player"] == "Juan Pérez" and r.focus["team"] == "C.D. DOSA"


def test_focus_reaches_the_llm_as_names_and_others_groups_for_el_otro_grupo() -> None:
    focus = {"team": "C.D. DOSA", "competition": "PRIMERA AFICIONADO FS", "group": "Grupo 2"}
    _, llm = run("¿Y el siguiente partido?", [LLMResponse("ok", [])] * 4, focus=focus)
    note = llm.seen[0][-1].text
    assert "equipo «C.D. DOSA»" in note and "grupo «Grupo 2»" in note and "Otros grupos de esa competición: «Grupo 1»" in note
    assert "team_id" not in note.split("Foco")[1].split("Los datos")[0]  # nombres, nunca IDs


def test_focus_ids_are_not_trusted_the_tool_validation_still_applies() -> None:
    res, _ = run("¿Y su próximo partido?", [call("list_matches", group_id=12, team_id=5), LLMResponse("No dispongo.", [])],
                 focus={"team": "C.D. DOSA"})
    assert res.run is not None and res.run.tool_calls[0].refused  # team_id=5 nunca lo mostró una tool en esta pregunta


def test_merge_focus_changes_team_competition_and_does_not_drag_stale_context() -> None:
    prev = {"team": "C.D. DOSA", "player": "Juan Pérez", "competition": "A", "group": "Grupo 2", "round": "3"}
    assert pres.merge_focus(prev, {"team": "FS ALCALA", "competition": "A", "group": "Grupo 2"}) == {
        "team": "FS ALCALA", "competition": "A", "group": "Grupo 2", "round": "3"}  # otro equipo: el jugador anterior ya no vale
    moved = pres.merge_focus(prev, {"competition": "B", "group": "Grupo 1"})
    assert moved == {"competition": "B", "group": "Grupo 1"}  # otra competición sin equipo: se descarta equipo, jugador y jornada
    assert pres.sanitize_focus({"team": "x" * 200, "evil": "1", "group": 3})["team"] == "x" * 80 and "evil" not in pres.sanitize_focus({"evil": "1"})


def web(steps: list[LLMResponse], freshness: Any = None) -> tuple[TestClient, Script]:
    llm = Script(steps)

    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(llm, Mcp(), 5, trace_results=True)

    app = create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None},
                          freshness=freshness or (lambda ids: {}))
    return TestClient(app), llm


def test_focus_is_kept_per_conversation_isolated_and_seeded_only_for_unknown_ids() -> None:
    steps = [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5), *[LLMResponse(f"r{i}", []) for i in range(14)]]
    c, llm = web(steps)
    with c:
        first = c.post("/chat", json={"question": "¿Cómo va el DOSA?", "conversation_id": "a"}).json()
        assert first["focus"]["team"] == "C.D. DOSA"
        c.post("/chat", json={"question": "¿Y el siguiente?", "conversation_id": "a"})
        c.post("/chat", json={"question": "¿Y el siguiente?", "conversation_id": "b"})  # otra conversación: sin foco
        seeded = c.post("/chat", json={"question": "¿Y el siguiente?", "conversation_id": "c",
                                       "focus": {"team": "FS ALCALA", "x": "y"}})
        forged = c.post("/chat", json={"question": "¿Y el siguiente?", "conversation_id": "a", "focus": {"team": "FALSO"}})
    notes = [m[-1].text for m in llm.seen if "Foco" in m[-1].text or m[-1].text.startswith("¿Y el")]
    assert any("equipo «C.D. DOSA»" in n for n in notes)
    assert "FS ALCALA" in [m[-1].text for m in llm.seen][-2] and seeded.status_code == 200 and forged.status_code == 200
    assert not any("FALSO" in m[-1].text for m in llm.seen)  # conversación conocida: manda el servidor


# --- Bloque 3: fuentes, frescura y correspondencia -------------------------------------------------------------------------------------
def test_official_sources_carry_dates_and_unknown_dates_are_declared_not_invented() -> None:
    r = present("¿Cómo va la clasificación?", [call("get_standings", group_id=12), LLMResponse("Líder DOSA.", [])])
    src = r.sources[0]
    assert (src.kind, src.title, src.updated_at, src.freshness) == ("official_data", "Clasificación oficial · RFFM",
                                                                    "2026-10-28T09:00:00+00:00", "known")
    assert src.detail == "PRIMERA AFICIONADO FS · Grupo 2" and src.queried_at
    r = present("¿Resultados del DOSA?", [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5), LLMResponse("ok", [])])
    unknown = next(s for s in r.sources if s.title.startswith("Partidos"))
    assert unknown.updated_at is None and unknown.freshness == "unknown" and "No se conoce la fecha" in unknown.updated_note


def test_freshness_from_phase21_gives_the_matches_date_and_flags_stale_data() -> None:
    fresh = {12: {"datasets": {"matches": {"status": "stale", "last_valid_at": "2026-10-01T08:00:00+00:00"}}}}
    r = present("¿Resultados del DOSA?", [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5), LLMResponse("ok", [])],
                freshness=fresh)
    src = next(s for s in r.sources if s.title.startswith("Partidos"))
    assert src.updated_at == "2026-10-01T08:00:00+00:00" and src.freshness == "stale" and "desactualizados" in src.updated_note


def test_only_hits_the_answer_relies_on_are_marked_cited() -> None:
    r = present("¿Cuánto dura cada mitad?", [call("search_rules", query="duración"), LLMResponse("Cada mitad dura 20 minutos (Regla 7, p. 31).", [])])
    assert [(s.pages, s.cited) for s in r.sources] == [("31", True), ("55", False)] and r.answer_kind == "documental"
    copied = present("¿Qué pasa con las faltas?", [call("search_rules", query="faltas"),
                                                  LLMResponse("Se sancionan con tiro libre directo o indirecto según el caso.", [])])
    assert [s.cited for s in copied.sources] == [False, True]  # por texto copiado, sin citar la regla


def test_answer_kind_and_analysis_flag() -> None:
    assert present("¿Cómo va?", [call("get_standings", group_id=12), LLMResponse("ok", [])]).answer_kind == "official"
    mixed = present("¿Cómo va y cuánto dura la mitad?", [call("get_standings", group_id=12), call("search_rules", query="x"), LLMResponse("ok", [])])
    assert mixed.answer_kind == "mixed"
    analysis = present("Analiza el rendimiento del DOSA", [call("get_standings", group_id=12), LLMResponse("Tiene buen ataque.", [])])
    assert analysis.is_analysis is True and analysis.answer_kind == "official"
    assert present("¿Cómo va?", [call("get_standings", group_id=12), LLMResponse("ok", [])]).is_analysis is False
    none = present("Hola", [LLMResponse("Solo puedo responder preguntas sobre la competición.", [])])
    assert none.answer_kind == "none" and none.sources == []


def test_refused_or_failed_calls_are_never_sources() -> None:
    r = present("¿Y su próximo partido?", [call("list_matches", group_id=12, team_id=5), LLMResponse("No he podido.", [])])
    assert r.sources == [] and r.visuals == []


# --- Bloque 5: visuales -----------------------------------------------------------------------------------------------------------------
def test_standings_table_and_scorers_ranking_use_only_tool_data() -> None:
    r = present("¿Cómo va el DOSA en la clasificación?", [call("get_standings", group_id=12), call("get_scorer_table", group_id=12),
                                                         LLMResponse("ok", [])])
    table = next(v for v in r.visuals if v["type"] == "table")
    assert [c["label"] for c in table["columns"]] == ["Pos", "Equipo", "PJ", "G", "E", "P", "GF", "GC", "DG", "Pts"]
    assert [row["team"] for row in table["rows"]] == ["C.D. DOSA", "FS ALCALA"] and table["rows"][0]["highlight"] is True
    assert table["subtitle"] == "Jornada 5" and table["rows"][1]["highlight"] is False
    bars = next(v for v in r.visuals if v["type"] == "bars")
    assert [(b["label"], b["value"]) for b in bars["rows"]] == [("Juan Pérez", 9), ("Luis Gil", 7)]


def test_comparison_of_two_teams_named_in_the_question() -> None:
    r = present("Compara el DOSA y el Alcalá", [call("get_standings", group_id=12), LLMResponse("ok", [])])
    cmp = next(v for v in r.visuals if v["type"] == "comparison")
    assert cmp["series"] == ["C.D. DOSA", "FS ALCALA"] and {"label": "Puntos", "values": [13, 10]} in cmp["rows"]
    assert not any(v["type"] == "comparison" for v in present("¿Cómo va el DOSA?", [call("get_standings", group_id=12), LLMResponse("ok", [])]).visuals)


def test_form_chart_computes_cumulative_points_from_the_matches_listed() -> None:
    r = present("¿Cómo han quedado los últimos partidos del DOSA?", [
        call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5, status="finished"), LLMResponse("ok", [])])
    form = next(v for v in r.visuals if v["type"] == "form")
    assert [(x["result"], x["points"], x["rival"]) for x in form["rows"]] == [("V", 3, "FS ALCALA"), ("E", 4, "X"), ("D", 4, "Y")]


def test_no_visuals_without_enough_data_or_after_incomplete_runs() -> None:
    empty = {**STANDINGS, "status": "empty", "standings": []}
    rec = run("q", [LLMResponse("hola", [])])[0].run
    assert rec is not None and pres.build_visuals("q", rec, [empty], {"teams": {}, "players": {}, "rounds": {}}) == []
    limit = present("¿Cómo va?", [call("get_standings", group_id=12)] * 6)  # se agota el límite de herramientas
    assert limit.visuals == [] and limit.error


# --- Bloque 4: preguntas sugeridas ----------------------------------------------------------------------------------------------------------
def test_suggestions_follow_the_query_use_the_focus_and_are_resolvable() -> None:
    r = present("¿Cómo va el DOSA en la clasificación?", [call("find_teams", name="DOSA"), call("get_standings", group_id=12, team_id=5),
                                                         LLMResponse("Va primero.", [])])
    assert r.suggestions == ["¿Quiénes son los próximos rivales de C.D. DOSA?", "¿Cómo han quedado los últimos partidos de C.D. DOSA?",
                             "¿Quién es el máximo goleador de C.D. DOSA?"]


def test_suggestions_skip_already_asked_questions_and_are_distinct() -> None:
    r = present("¿Cómo va el DOSA en la clasificación?", [call("find_teams", name="DOSA"), call("get_standings", group_id=12, team_id=5),
                                                         LLMResponse("ok", [])], asked=["¿Quién es el máximo goleador de C.D. DOSA?"])
    assert len(r.suggestions) == 2 and len(set(r.suggestions)) == 2 and all("máximo goleador" not in s for s in r.suggestions)


def test_no_suggestions_for_errors_limits_or_answers_without_tools() -> None:
    assert present("Hola", [LLMResponse("Solo puedo responder preguntas sobre la competición.", [])]).suggestions == []
    assert present("¿Cómo va?", [call("get_standings", group_id=12), LLMResponse("No he encontrado ese dato en las fuentes que tengo disponibles.", [])]
                   ).suggestions == []
    assert present("¿Cómo va?", [call("get_standings", group_id=12)] * 6).suggestions == []


def test_rules_answers_suggest_only_what_the_rag_can_answer() -> None:
    r = present("¿Cuánto dura cada mitad?", [call("search_rules", query="duración"), LLMResponse("Dura 20 minutos (Regla 7, p. 31).", [])])
    assert r.suggestions == ["Resúmeme la Regla 7 del reglamento", "¿Qué circulares hay sobre este tema?"]


def test_clicked_suggestions_are_plain_user_messages_with_the_same_contract() -> None:
    c, _ = web([call("get_standings", group_id=12), LLMResponse("Líder DOSA.", [])])
    with c:
        body = c.post("/chat", json={"question": "¿Cómo va la clasificación?", "conversation_id": "s"}).json()
    assert body["suggestions"] and all(isinstance(s, str) and s.endswith(("?", "reglamento")) for s in body["suggestions"])
    assert datetime.fromisoformat(body["sources"][0]["queried_at"]).tzinfo is UTC or body["sources"][0]["queried_at"]


VISUALS_FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "chat_response_visuals.json"


def test_visual_contract_fixture_matches_backend() -> None:
    """El frontend renderiza este JSON REAL (tabla, comparativa, ranking y forma): si cambia el contrato, hay que regenerarlo."""
    steps = [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5, status="finished"),
             call("get_standings", group_id=12), call("get_scorer_table", group_id=12), LLMResponse("El DOSA lidera con 13 puntos.", [])]
    c, _ = web(steps, freshness=lambda ids: {12: {"datasets": {"matches": {"status": "fresh", "last_valid_at": "2026-10-29T08:00:00+00:00"}}}})
    with c:
        body = c.post("/chat", json={"question": "Compara el DOSA y el Alcalá en la clasificación", "conversation_id": "fx"}).json()
    body["diagnostics"].update(run_id="run", duration_ms=0, llm_ms=0, retry_wait_ms=0, tools_ms=0, context_ms=0, postprocess_ms=0)
    for t in body["diagnostics"]["tools"]:
        t["duration_ms"] = 0
    for src in body["sources"]:
        src["queried_at"] = "2026-10-30T10:00:00+00:00"
    assert {v["type"] for v in body["visuals"]} == {"table", "comparison", "bars", "form"} and body["suggestions"]
    if not VISUALS_FIXTURE.exists():
        VISUALS_FIXTURE.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(VISUALS_FIXTURE.read_text(encoding="utf-8")) == body


# --- Alineaciones: quinteto inicial sobre la pista y suplentes (y árbitros, goles y partidos como visuales) ----------------------------------------------------------------------------
def lineup(question: str, report: dict[str, Any] | None = None) -> Any:
    steps = [call("get_match_report", match_id=9, sections=["players"]), LLMResponse("Alineación del partido.", [])]
    REPORTS["current"] = report if report is not None else REPORT
    try:
        return present(question, steps)
    finally:
        REPORTS["current"] = REPORT


def test_lineup_shows_the_starting_five_with_goalkeeper_first_and_the_substitutes() -> None:
    r = lineup("¿Cuál fue la alineación del DOSA?")
    (v,) = [v for v in r.visuals if v["type"] == "lineup"]
    assert v["title"] == "Alineación · C.D. DOSA" and v["subtitle"] == "C.D. DOSA 3-1 FS ALCALA · Jornada 4" and v["court"] is True
    assert [(p["shirt"], p["short"], p["goalkeeper"]) for p in v["starters"]] == [
        (1, "J. Aparicio", True), (5, "P. Gil", False), (7, "A. Ruiz", False), (9, "I. Nuñez", False), (10, "J. Perez", False)]
    assert [p["name"] for p in v["starters"]][0] == "Javier Aparicio Jimenez" and v["starters"][4]["captain"] is True
    assert [(p["shirt"], p["goalkeeper"]) for p in v["substitutes"]] == [(13, True), (20, False)]  # portero suplente primero
    assert "no la posición" in v["note"] and "orientativa" in v["note"]  # el acta no da posiciones: se avisa, no se inventan


def test_lineup_without_naming_a_team_shows_both_and_naming_one_shows_only_it() -> None:
    both = [v["team"] for v in lineup("¿Qué alineaciones hubo en ese partido?").visuals if v["type"] == "lineup"]
    assert both == ["C.D. DOSA", "FS ALCALA"]
    only = [v["team"] for v in lineup("¿Cuál fue la alineación del Alcalá?").visuals if v["type"] == "lineup"]
    assert only == ["FS ALCALA"]


def test_lineup_needs_real_data_and_degrades_without_the_court_when_incomplete() -> None:
    four = {**REPORT, "players": [p for p in REPORT["players"] if not (p["team"] == "FS ALCALA" and p["shirt"] == 5 and p["lineup"] == "starter")]}
    alcala = next(v for v in lineup("alineación del Alcalá", four).visuals if v["type"] == "lineup")
    assert alcala["court"] is False and len(alcala["starters"]) == 4  # sin 5 titulares no se dibuja la pista; se listan los que constan
    assert not [v for v in lineup("alineación", {**REPORT, "players": [p for p in REPORT["players"] if p["lineup"] == "substitute"]}).visuals
                if v["type"] == "lineup"]  # sin titulares, nada
    assert lineup("alineación", {"report_available": False, "reason": "sin acta", "match": REPORT["match"]}).visuals == []
    sin_jugadores = lineup("alineación", {k: v for k, v in REPORT.items() if k != "players"})
    assert [v["type"] for v in sin_jugadores.visuals] == ["officials", "timeline", "cards"]  # sin la sección de jugadores no hay alineación, pero sí lo demás


def test_lineup_report_is_listed_as_official_source_with_unknown_date_and_suggestions() -> None:
    r = lineup("¿Cuál fue la alineación del DOSA?")
    src = next(s for s in r.sources if s.title == "Acta del partido · RFFM")
    assert src.detail == "C.D. DOSA - FS ALCALA · Jornada 4" and src.updated_at is None and "No se conoce la fecha" in src.updated_note
    # árbitros, goles y tarjetas ya se ven en gráficos: no se vuelven a sugerir
    assert r.suggestions == ["¿Cómo va la clasificación del grupo?", "¿Cuáles son los próximos partidos del grupo?"]
    only_players = lineup("alineación", {k: v for k, v in REPORT.items() if k not in ("officials", "goals", "cards")})
    assert only_players.suggestions[:3] == ["¿Quién marcó los goles de ese partido?", "¿Qué tarjetas hubo en ese partido?",
                                            "¿Quiénes fueron los árbitros de ese partido?"]


def test_prompt_points_lineups_to_the_report_players_section() -> None:
    from futsal.agent.agent import SYSTEM_PROMPT

    assert "alineaciones" in SYSTEM_PROMPT and 'sections=["players"]' in SYSTEM_PROMPT


# --- Árbitros, goles, partidos y «no repetir en texto» ---------------------------------------------------------------------------------------
def test_officials_are_people_with_their_role_and_kind_of_figure() -> None:
    r = lineup("¿Quiénes fueron los árbitros del partido?")
    (v,) = [v for v in r.visuals if v["type"] == "officials"]
    assert [(p["name"], p["role"], p["kind"]) for p in v["people"]] == [
        ("Eduardo Hugo Iniesta del Amo", "Árbitro", "referee"), ("Jaime Larrubia Pajares", "Árbitro", "referee"),
        ("Arantxa Ortiz Pinillos", "Árbitro asistente", "assistant")]  # partículas en minúscula, cargo tal cual figura en el acta
    assert v["subtitle"] == "C.D. DOSA 3-1 FS ALCALA · Jornada 4"


def test_goal_timeline_puts_each_goal_on_the_side_that_scores_and_never_draws_cards() -> None:
    r = lineup("¿Quién marcó?")
    (v,) = [v for v in r.visuals if v["type"] == "timeline"]
    assert [(e["minute"], e["player"], e["side"], e["own_goal"]) for e in v["events"]] == [
        (3, "Juan Perez Lopez", "home", False), (12, "Leo Martin Cano", "away", False),
        (20, "A Otro Uno", "home", True),  # gol en propia puerta: cuenta para el rival (DOSA)
        (31, "Ana Ruiz Soto", "home", False)]
    assert (v["home"], v["away"]) == ("C.D. DOSA", "FS ALCALA") and "tarjeta" not in json.dumps(v).lower()
    sin_goles = {**REPORT, "goals": []}
    assert not [v for v in lineup("alineación", sin_goles).visuals if v["type"] == "timeline"]


def test_report_visuals_come_in_a_stable_order() -> None:
    assert [v["type"] for v in lineup("¿Qué alineaciones hubo en ese partido?").visuals] == ["lineup", "lineup", "officials", "timeline", "cards"]
    sin_acta = {"report_available": False, "reason": "sin acta", "match": REPORT["match"], "officials": REPORT["officials"]}
    assert lineup("árbitros", sin_acta).visuals == []  # sin acta no se dibuja nada


def test_matches_card_for_upcoming_results_with_total_and_past_due() -> None:
    r = present("¿Cuáles son los próximos partidos?", [call("list_matches", group_id=12, status="scheduled", limit=3), LLMResponse("Ahí van.", [])])
    (v,) = [v for v in r.visuals if v["type"] == "matches"]
    assert v["title"] == "Próximos partidos · PRIMERA AFICIONADO FS · Grupo 2" and v["total"] == 30 and v["partial"] is True
    assert [(x["round"], x["home"], x["score"], x["past_due"]) for x in v["rows"]] == [(6, "C.D. DOSA", None, False), (7, "W", None, True), (8, "C.D. DOSA", None, False)]


def test_team_form_and_matches_card_do_not_duplicate_each_other() -> None:
    form = present("últimos partidos del DOSA", [call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5, status="finished"),
                                                 LLMResponse("ok", [])])
    assert [v["type"] for v in form.visuals] == ["form"]
    results = present("resultados", [call("list_matches", group_id=12, status="finished"), LLMResponse("ok", [])])
    assert [v["type"] for v in results.visuals] == ["matches"] and results.visuals[0]["title"] == "Resultados · PRIMERA AFICIONADO FS · Grupo 2"
    one = present("próximo", [call("list_matches", group_id=12, status="scheduled", limit=1), LLMResponse("ok", [])])
    assert [v["type"] for v in one.visuals] == ["matches"]  # el simulado devuelve 3 filas; con 1 sola fila real no se dibuja


def seen_tool_result(steps: list[LLMResponse], question: str) -> dict[str, Any]:
    _, llm = run(question, steps)
    msg = next(m for m in llm.seen[1] if m.role == "tool")
    return dict(json.loads(msg.tool_results[0].content))


def test_the_model_is_told_not_to_repeat_what_the_app_already_draws() -> None:
    data = seen_tool_result([call("get_match_report", match_id=9, sections=["officials", "players", "goals"]), LLMResponse("ok", [])],
                            "¿Cuál fue la alineación del DOSA y quién arbitró?")
    note = data["visual_note"]
    assert "los árbitros" in note and "las alineaciones" in note and "los goles por minuto" in note and "no lo repitas en texto" in note
    assert "una o dos frases" in note  # el texto sigue aportando lo esencial
    assert "visual_note" in seen_tool_result([call("get_standings", group_id=12), LLMResponse("ok", [])], "clasificación")
    assert "los partidos en una tabla" in seen_tool_result([call("list_matches", group_id=12, status="scheduled"), LLMResponse("ok", [])], "próximos")["visual_note"]


def test_no_note_when_nothing_will_be_drawn_so_no_information_is_lost() -> None:
    only_sub = {**REPORT, "officials": [], "goals": [], "cards": [], "players": [p for p in REPORT["players"] if p["lineup"] == "substitute"]}
    REPORTS["current"] = only_sub
    try:
        assert "visual_note" not in seen_tool_result([call("get_match_report", match_id=9), LLMResponse("ok", [])], "¿qué pasó?")
    finally:
        REPORTS["current"] = REPORT
    assert "visual_note" not in seen_tool_result([call("find_teams", name="DOSA"), LLMResponse("ok", [])], "equipo")  # solo las tools con visual
    one_team_results = seen_tool_result([call("find_teams", name="DOSA"), call("list_matches", group_id=12, team_id=5, status="finished"), LLMResponse("ok", [])],
                                        "últimos partidos del DOSA")
    assert "visual_note" not in one_team_results  # sus últimos resultados los dibuja la «forma» del equipo y no hay aviso de tabla


def test_note_and_visual_always_agree() -> None:
    """Propiedad: si la tool avisa de un visual, build_visuals lo genera (y a la inversa para los tipos avisables)."""
    cases = [([call("get_match_report", match_id=9, sections=["players"]), LLMResponse("ok", [])], {"lineup"}),
             ([call("get_standings", group_id=12), LLMResponse("ok", [])], {"table"}),
             ([call("get_scorer_table", group_id=12), LLMResponse("ok", [])], {"bars"}),
             ([call("list_matches", group_id=12, status="scheduled"), LLMResponse("ok", [])], {"matches"})]
    for steps, expected in cases:
        noted = "visual_note" in seen_tool_result(list(steps), "¿qué alineación hubo, clasificación y partidos?")
        drawn = {v["type"] for v in present("¿qué alineación hubo, clasificación y partidos?", list(steps)).visuals}
        assert noted and expected <= drawn, (steps[0].tool_calls[0].name, noted, drawn)


MATCH_FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "chat_response_match.json"
MATCHES_FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "chat_response_matches.json"


def _fixture(steps: list[LLMResponse], question: str, path: Any, expected: list[str]) -> None:
    c, _ = web(steps)
    with c:
        body = c.post("/chat", json={"question": question, "conversation_id": "fx"}).json()
    body["diagnostics"].update(run_id="run", duration_ms=0, llm_ms=0, retry_wait_ms=0, tools_ms=0, context_ms=0, postprocess_ms=0)
    for t in body["diagnostics"]["tools"]:
        t["duration_ms"] = 0
    for src in body["sources"]:
        src["queried_at"] = "2026-10-30T10:00:00+00:00"
    assert [v["type"] for v in body["visuals"]] == expected
    if not path.exists():
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == body


def test_match_and_matches_contract_fixtures_match_backend() -> None:
    _fixture([call("get_match_report", match_id=9, sections=["officials", "players", "goals"]), LLMResponse("Ganó el DOSA 3-1.", [])],
             "¿Cuál fue la alineación del DOSA y quién arbitró?", MATCH_FIXTURE, ["lineup", "officials", "timeline", "cards"])
    _fixture([call("list_matches", group_id=12, status="scheduled", limit=3), LLMResponse("Estos son los próximos partidos.", [])],
             "¿Cuáles son los próximos partidos?", MATCHES_FIXTURE, ["matches"])


# --- Tarjetas (significado de los códigos confirmado: 100 amarilla, 100+segunda=2.ª amarilla/expulsión, 101 roja directa) --------------------------
def test_cards_visual_orders_by_minute_assigns_sides_and_counts_each_kind() -> None:
    (v,) = [v for v in lineup("¿Qué tarjetas hubo?").visuals if v["type"] == "cards"]
    assert [(e["minute"], e["player"], e["side"], e["kind"]) for e in v["events"]] == [
        (9, "B Otro Dos", "away", "yellow"), (14, "Juan Perez Lopez", "home", "yellow"), (24, "Ana Ruiz Soto", "home", "red"),
        (30, "B Otro Dos", "away", "second_yellow"), (35, "Pedro Gil Mora", "home", "unknown")]  # el 177 queda sin color
    assert v["summary"] == {"yellow": 2, "second_yellow": 1, "red": 1, "unknown": 1} and (v["home"], v["away"]) == ("C.D. DOSA", "FS ALCALA")
    assert v["subtitle"] == "C.D. DOSA 3-1 FS ALCALA · Jornada 4" and v["partial"] is False


def test_no_cards_no_visual_and_no_note() -> None:
    sin = {**REPORT, "cards": []}
    assert not [v for v in lineup("tarjetas", sin).visuals if v["type"] == "cards"]
    REPORTS["current"] = {**sin, "officials": [], "goals": [], "players": []}
    try:
        assert "visual_note" not in seen_tool_result([call("get_match_report", match_id=9, sections=["cards"]), LLMResponse("ok", [])], "tarjetas")
    finally:
        REPORTS["current"] = REPORT


def test_the_model_is_told_the_cards_are_drawn_and_the_tool_gives_the_verified_kind() -> None:
    data = seen_tool_result([call("get_match_report", match_id=9, sections=["cards"]), LLMResponse("ok", [])], "¿Qué tarjetas hubo?")
    assert "las tarjetas" in data["visual_note"] and all("card_kind" in c for c in data["cards"])


EVENTS = {"events": [{"match_id": 9, "round_number": 4, "type": "card", "team": "C.D. DOSA", "opponent": "FS ALCALA", "player": "RUIZ SOTO, ANA",
                      "minute": 24, "source_code": "101", "card_kind": "red"},
                     {"match_id": 7, "round_number": 2, "type": "card", "team": "FS ALCALA", "opponent": "X", "player": "OTRO DOS, B",
                      "minute": 9, "source_code": "100", "card_kind": "yellow"}], "count": 2, "total": 5, "truncated": True}


def test_cards_from_several_matches_are_a_list_marked_partial_when_truncated() -> None:
    v = pres.cards_from_events(EVENTS)
    assert v is not None and v["type"] == "cards" and v["partial"] is True and v["home"] is None
    assert [(e["round"], e["minute"], e["team"], e["kind"]) for e in v["events"]] == [(2, 9, "FS ALCALA", "yellow"), (4, 24, "C.D. DOSA", "red")]
    assert pres.cards_from_events({"events": [{"type": "goal", "player": "X", "card_kind": "yellow"}]}) is None
    assert pres.cards_from_events({"events": [{"type": "card", "player": "X", "source_code": "100"}]}) is None  # sin card_kind no se inventa
    assert "las tarjetas" in (pres.visual_note("list_match_events", {}, EVENTS, "tarjetas", {}) or "")
