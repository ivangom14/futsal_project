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
RAG = {"hits": [
    {"document": "Reglas de Juego FIFA", "doc_key": "fifa", "rule": 7, "rule_title": "Duración", "pages": "31",
     "text": "El partido durará dos tiempos iguales de 20 minutos cada uno."},
    {"document": "Reglas de Juego FIFA", "doc_key": "fifa", "rule": 12, "rule_title": "Faltas", "pages": "55",
     "text": "Las faltas se sancionan con tiro libre directo o indirecto."}]}


def _pl(team: str, name: str, shirt: int, lineup: str, gk: bool = False, captain: bool = False) -> dict[str, Any]:
    return {"team": team, "player_id": shirt, "name": name, "shirt": shirt, "lineup": lineup, "captain": captain, **({"goalkeeper": True} if gk else {})}


REPORT = {"report_available": True, "match": {"match_id": 9, "round_number": 4, "home_team": "C.D. DOSA", "away_team": "FS ALCALA",
                                              "status": "finished", "home_score": 3, "away_score": 1},
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
                "list_matches": MATCHES, "search_rules": RAG, "get_match_report": REPORTS["current"]}.get(name, {})
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
    body["diagnostics"].update(run_id="run", duration_ms=0)
    for t in body["diagnostics"]["tools"]:
        t["duration_ms"] = 0
    for src in body["sources"]:
        src["queried_at"] = "2026-10-30T10:00:00+00:00"
    assert {v["type"] for v in body["visuals"]} == {"table", "comparison", "bars", "form"} and body["suggestions"]
    if not VISUALS_FIXTURE.exists():
        VISUALS_FIXTURE.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(VISUALS_FIXTURE.read_text(encoding="utf-8")) == body


# --- Alineaciones: quinteto inicial sobre la pista y suplentes ----------------------------------------------------------------------------
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
    assert lineup("alineación", {k: v for k, v in REPORT.items() if k != "players"}).visuals == []  # sin la sección de jugadores


def test_lineup_report_is_listed_as_official_source_with_unknown_date_and_suggestions() -> None:
    r = lineup("¿Cuál fue la alineación del DOSA?")
    src = next(s for s in r.sources if s.title == "Acta del partido · RFFM")
    assert src.detail == "C.D. DOSA - FS ALCALA · Jornada 4" and src.updated_at is None and "No se conoce la fecha" in src.updated_note
    assert r.suggestions == ["¿Quién marcó los goles de ese partido?", "¿Qué tarjetas hubo en ese partido?", "¿Quiénes fueron los árbitros de ese partido?"]


def test_prompt_points_lineups_to_the_report_players_section() -> None:
    from futsal.agent.agent import SYSTEM_PROMPT

    assert "alineaciones" in SYSTEM_PROMPT and 'sections=["players"]' in SYSTEM_PROMPT


LINEUP_FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "chat_response_lineup.json"


def test_lineup_contract_fixture_matches_backend() -> None:
    c, _ = web([call("get_match_report", match_id=9, sections=["players"]), LLMResponse("Así formó el DOSA.", [])])
    with c:
        body = c.post("/chat", json={"question": "¿Cuál fue la alineación del DOSA?", "conversation_id": "ln"}).json()
    body["diagnostics"].update(run_id="run", duration_ms=0)
    for t in body["diagnostics"]["tools"]:
        t["duration_ms"] = 0
    for src in body["sources"]:
        src["queried_at"] = "2026-10-30T10:00:00+00:00"
    assert [v["type"] for v in body["visuals"]] == ["lineup"]
    if not LINEUP_FIXTURE.exists():
        LINEUP_FIXTURE.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(LINEUP_FIXTURE.read_text(encoding="utf-8")) == body
