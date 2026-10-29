"""Fase 19: resolución de equipos y eficiencia. Los IDs de equipo y jornada solo valen si una tool los mostró; nombres ambiguos sin
elegir; último partido = finalizado más reciente (order=desc, limit=1); próximo partido con `past_due`; llamadas repetidas
reutilizadas; el límite conserva lo oficial ya obtenido. LLM guionizado y API simulada que aplica las funciones reales de orden y
`past_due`; sin red."""
import asyncio
import json
from datetime import date
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import (
    AMBIGUOUS_TEAM_MESSAGE,
    LIMIT_REACHED_MESSAGE,
    SYSTEM_PROMPT,
    Agent,
    AgentResult,
)
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.api.app import mark_past_due, order_matches
from futsal.mcp_server.server import create_server

TODAY = date(2026, 10, 27)
COMPETITIONS = [
    {"id": 1, "name": "PRIMERA SENIOR", "groups": [{"id": 1, "name": "Grupo 1"}, {"id": 2, "name": "Grupo 2"}]},
    {"id": 2, "name": "JUVENIL", "groups": [{"id": 3, "name": "Grupo 1"}, {"id": 4, "name": "Grupo 2"}]},
]
TEAMS = [  # FS ALCALA existe en dos grupos de competiciones distintas; CD DOSA solo en uno
    {"id": 11, "name": "CD DOSA", "group_id": 1, "group": "Grupo 1", "competition_id": 1, "competition": "PRIMERA SENIOR"},
    {"id": 21, "name": "FS ALCALA", "group_id": 1, "group": "Grupo 1", "competition_id": 1, "competition": "PRIMERA SENIOR"},
    {"id": 31, "name": "FS ALCALA", "group_id": 3, "group": "Grupo 1", "competition_id": 2, "competition": "JUVENIL"},
]


def _m(mid: int, rnd: int, day: str | None, status: str, hs: int | None = None, as_: int | None = None) -> dict[str, Any]:
    return {"id": mid, "group_id": 1, "home_team_id": 11, "away_team_id": 21, "home_team": "CD DOSA", "away_team": "FS ALCALA",
            "round_number": rnd, "date": day, "status": status, "home_score": hs, "away_score": as_}


MATCHES = [_m(101, 1, "2026-10-03T18:00:00", "finished", 3, 1), _m(102, 2, "2026-10-10T18:00:00", "finished", 2, 2),
           _m(103, 5, "2026-10-24T18:00:00", "scheduled"),          # fecha pasada y sigue programado: dato desactualizado
           _m(104, 6, "2026-11-01T18:00:00", "scheduled"), _m(105, 7, None, "scheduled")]
STANDINGS = {"group_id": 1, "group": "Grupo 1", "competition": "PRIMERA SENIOR", "status": "ok",
             "source": {"round": 5, "captured_at": "2026-10-24T09:00:00+00:00"},
             "standings": [{"position": 1, "team": "CD DOSA", "points": 12}]}
requests: list[httpx.Request] = []


def _api(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    path, q = request.url.path, request.url.params
    if path == "/competitions":
        return httpx.Response(200, json={"items": COMPETITIONS, "count": len(COMPETITIONS)})
    if path == "/teams":
        rows = [t for t in TEAMS if q["name"].lower() in t["name"].lower()]
        return httpx.Response(200, json={"items": rows, "count": len(rows)})
    if path == "/groups/1/rounds":
        rows = [{"id": n, "number": n} for n in (1, 2, 5, 6, 7)]
        return httpx.Response(200, json={"items": rows, "count": len(rows)})
    if path == "/groups/1/matches":
        rows = [dict(m) for m in MATCHES if (not q.get("status") or m["status"] == q["status"])
                and (not q.get("team_id") or int(q["team_id"]) in (m["home_team_id"], m["away_team_id"]))]
        rows = order_matches(rows, q.get("order", "asc"))
        mark_past_due(rows, TODAY)
        limit = int(q["limit"]) if q.get("limit") else None
        body: dict[str, Any] = {"items": rows[:limit] if limit else rows, "count": len(rows)}
        if limit:
            body["shown"] = min(limit, len(rows))
        return httpx.Response(200, json=body)
    if path == "/groups/1/standings":
        return httpx.Response(200, json=STANDINGS)
    if path == "/matches/102/report":
        return httpx.Response(200, json={"match_id": 102, "report_available": True, "goals": []})
    return httpx.Response(404, json={"detail": "no encontrado"})


class Script:
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script, self.messages = list(script), []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.messages = messages
        return self.script.pop(0) if self.script else LLMResponse("fin", [])


def _call(tool: str, /, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall(tool, tool, args)])


def _run(llm: Script, question: str, max_calls: int = 5) -> AgentResult:
    requests.clear()
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api)))

    async def go() -> AgentResult:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), max_calls).run(question)  # type: ignore[arg-type]

    return asyncio.run(go())


def _results(llm: Script) -> list[str]:
    return [r.content for m in llm.messages for r in m.tool_results]


def _matches_requests() -> list[dict[str, str]]:
    return [dict(r.url.params) for r in requests if r.url.path == "/groups/1/matches"]


# --- 1. el equipo se resuelve con tools fiables, no con un ID inventado --------------------------------------------------------------
def test_team_is_resolved_with_find_teams_and_then_last_match_is_one_call() -> None:
    llm = Script([_call("find_teams", name="DOSA"),
                  _call("list_matches", group_id=1, team_id=11, status="finished", order="desc", limit=1),
                  LLMResponse("El último partido de CD DOSA fue 2-2 ante FS ALCALA (jornada 2).", [])])
    res = _run(llm, "¿Cuál fue el último partido de CD DOSA?")
    assert res.tool_calls == 2 and res.guardrail is None and res.error is None
    assert _matches_requests() == [{"team_id": "11", "status": "finished", "order": "desc", "limit": "1"}]
    last = json.loads(_results(llm)[1])
    assert last["count"] == 2 and last["shown"] == 1 and last["matches"][0]["home_score"] == 2   # el más reciente, no el primero


def test_a_guessed_team_id_is_refused_without_calling_the_api_even_if_it_exists() -> None:
    llm = Script([_call("list_matches", group_id=1, team_id=11, status="finished"),         # 11 existe, pero nadie lo mostró
                  _call("find_teams", name="DOSA"),
                  _call("list_matches", group_id=1, team_id=11, status="finished", order="desc", limit=1),
                  LLMResponse("Fue el 2-2.", [])])
    res = _run(llm, "¿Cuál fue el último partido de CD DOSA?")
    first = _results(llm)[0]
    assert "team_id=11" in first and "no procede de ninguna herramienta" in first and "find_teams" in first
    assert len(_matches_requests()) == 1 and _matches_requests()[0]["order"] == "desc"       # solo la llamada con ID obtenido de find_teams
    assert res.answer == "Fue el 2-2." and res.tool_calls == 3                              # la llamada rechazada cuenta contra el límite


def test_against_team_id_is_checked_too() -> None:
    llm = Script([_call("find_teams", name="DOSA"), _call("top_scorers", group_id=1, team_id=11, against_team_id=21),
                  LLMResponse("fin", [])])
    _run(llm, "goles de CD DOSA contra el rival")
    assert "against_team_id=21" in _results(llm)[1] and not [r for r in requests if r.url.path.endswith("/scorers")]


# --- 2. nombres ambiguos ----------------------------------------------------------------------------------------------------------
def test_ambiguous_name_is_not_picked_arbitrarily() -> None:
    llm = Script([_call("find_teams", name="ALCALA"), _call("list_matches", group_id=1, team_id=21, status="finished"),
                  LLMResponse("¿Te refieres al FS ALCALA de PRIMERA SENIOR (Grupo 1) o al de JUVENIL (Grupo 1)?", [])])
    res = _run(llm, "¿Cuál fue el último partido del FS ALCALA?")
    assert _results(llm)[1] == AMBIGUOUS_TEAM_MESSAGE and not _matches_requests()
    assert "¿Te refieres" in res.answer


def test_ambiguous_name_is_allowed_once_the_question_names_competition_and_group() -> None:
    llm = Script([_call("find_teams", name="ALCALA"), _call("list_matches", group_id=1, team_id=21, status="finished"),
                  LLMResponse("2-2 en la jornada 2.", [])])
    _run(llm, "¿Último partido del FS ALCALA en PRIMERA SENIOR Grupo 1?")
    assert len(_matches_requests()) == 1 and '"matches"' in _results(llm)[1]


def test_unambiguous_name_needs_no_clarification() -> None:
    llm = Script([_call("find_teams", name="DOSA"), _call("list_matches", group_id=1, team_id=11, limit=1), LLMResponse("ok", [])])
    _run(llm, "partidos del CD DOSA")
    assert len(_matches_requests()) == 1


# --- 3. último y próximo partido -------------------------------------------------------------------------------------------------
def test_order_desc_puts_the_latest_dated_match_first_and_undated_last() -> None:
    rows = [dict(m) for m in MATCHES]
    assert [m["id"] for m in order_matches(rows, "asc")] == [101, 102, 103, 104, 105]       # asc: sin cambios
    assert [m["id"] for m in order_matches(rows, "desc")] == [104, 103, 102, 101, 105]
    finished = [m for m in rows if m["status"] == "finished"]
    assert [m["id"] for m in order_matches(finished, "desc")] == [102, 101]


def test_scheduled_match_with_a_past_date_is_flagged_not_treated_as_played() -> None:
    rows = [dict(m) for m in MATCHES]
    mark_past_due(rows, TODAY)
    flagged = {m["id"] for m in rows if m.get("past_due")}
    assert flagged == {103}                                                                  # 104 es futuro, 105 sin fecha, los finalizados nunca
    assert next(m for m in rows if m["id"] == 103)["status"] == "scheduled" and "home_score" in rows[2]
    assert not any(m.get("past_due") for m in rows if m["status"] == "finished")


def test_next_match_query_shows_the_model_the_stale_date_instead_of_assuming_it_was_played() -> None:
    llm = Script([_call("find_teams", name="DOSA"), _call("list_matches", group_id=1, team_id=11, status="scheduled", limit=3),
                  LLMResponse("Constan como programados: J5 (24/10, fecha ya pasada, datos posiblemente desactualizados) y J6 (1/11).", [])])
    res = _run(llm, "¿Cuál es el próximo partido del CD DOSA?")
    seen = json.loads(_results(llm)[1])
    assert [m["past_due"] if "past_due" in m else None for m in seen["matches"]] == [True, None, None]
    assert _matches_requests()[0] == {"team_id": "11", "status": "scheduled", "limit": "3"} and res.tool_calls == 2


def test_prompt_and_tool_describe_last_next_and_past_due() -> None:
    assert "status=finished, order=desc, limit=1" in SYSTEM_PROMPT and "past_due=true" in SYSTEM_PROMPT
    assert "nunca los supongas" in SYSTEM_PROMPT
    desc = next(t for t in asyncio.run(create_server().list_tools()) if t.name == "list_matches")
    assert "past_due" in (desc.description or "") and "order" in desc.inputSchema["properties"]


# --- 4. nada de jornadas ni IDs inventados ---------------------------------------------------------------------------------------
def test_round_ids_must_come_from_list_rounds() -> None:
    llm = Script([_call("list_matches", group_id=1, round_id=9), _call("list_matches", group_id=1, round_id=10),
                  _call("list_rounds", group_id=1), _call("list_matches", group_id=1, round_id=7), LLMResponse("fin", [])])
    res = _run(llm, "¿qué partidos hay en la jornada 9?", max_calls=6)
    results = _results(llm)
    assert "round_id=9" in results[0] and "round_id=10" in results[1] and "list_rounds" in results[0]
    assert _matches_requests() == [{"round_id": "7"}]                                       # las jornadas supuestas nunca llegan a la API
    assert res.tool_calls == 4


# --- 5. llamadas repetidas ---------------------------------------------------------------------------------------------------------
def test_identical_call_reuses_the_previous_result_but_still_counts() -> None:
    same = dict(group_id=1, status="finished", limit=1)
    llm = Script([_call("list_matches", **same), _call("list_matches", **same), LLMResponse("fin", [])])
    res = _run(llm, "partidos jugados")
    assert len(_matches_requests()) == 1 and res.tool_calls == 2
    assert _results(llm)[0] == _results(llm)[1]


# --- 6. límite: lo verificado se conserva ------------------------------------------------------------------------------------------
def test_limit_keeps_official_data_already_obtained_and_does_not_invent_the_rest() -> None:
    llm = Script([_call("get_standings", group_id=1), _call("list_matches", group_id=1, team_id=98),
                  _call("list_matches", group_id=1, team_id=99), _call("list_matches", group_id=1, team_id=97)])
    res = _run(llm, "¿Quién lidera el grupo y cuál fue su último partido?", max_calls=3)
    assert res.guardrail == "limite_herramientas" and res.error and res.answer.startswith(LIMIT_REACHED_MESSAGE)
    assert "CD DOSA con 12 puntos" in res.answer and "jornada 5" in res.answer              # copiado de la tool
    assert "no se ha podido verificar" in res.answer and not _matches_requests()            # los IDs supuestos no llegaron a la API


# --- 7. sin regresiones ---------------------------------------------------------------------------------------------------------
def test_tools_without_team_or_round_ids_are_untouched() -> None:
    llm = Script([_call("get_standings", group_id=1), _call("get_match_report", match_id=102),
                  _call("list_matches", group_id=1, status="finished"), LLMResponse("Líder CD DOSA; el 2-2 de la J2.", [])])
    res = _run(llm, "clasificación y acta del partido 102")
    assert res.tool_calls == 3 and res.guardrail is None and len(res.official) == 1
    assert not any("error_validacion" in r for r in _results(llm))
    assert [dict(r.url.params) for r in requests if r.url.path == "/groups/1/matches"] == [{"status": "finished"}]


# --- una llamada rechazada no es un fallo de la herramienta, pero sigue contando como llamada ---------------------------------------
def test_refused_calls_are_marked_and_the_hint_only_names_find_teams() -> None:
    from futsal.agent.evaluate import verdict_calls
    llm = Script([_call("list_matches", group_id=1, team_id=11), _call("find_teams", name="DOSA"),
                  _call("list_matches", group_id=1, team_id=11, limit=1), LLMResponse("ok", [])])
    res = _run(llm, "partidos del CD DOSA")
    assert res.run is not None
    flags = [(tc.name, tc.refused, tc.is_error) for tc in res.run.tool_calls]
    assert flags == [("list_matches", True, True), ("find_teams", False, False), ("list_matches", False, False)]
    assert "find_teams" in _results(llm)[0] and "list_teams" not in _results(llm)[0]
    calls = verdict_calls(res.run.tool_calls)
    assert [c[2] for c in calls] == [False, False, False] and len(calls) == 3        # sin «devolvió error», pero cuenta para el máximo


def test_a_real_tool_error_is_still_an_error_in_the_verdict() -> None:
    from futsal.agent.evaluate import verdict_calls
    llm = Script([_call("find_teams", name="DOSA"), _call("list_matches", group_id=999, team_id=11), LLMResponse("no se pudo", [])])
    res = _run(llm, "partidos del CD DOSA")
    assert res.run is not None and [c[2] for c in verdict_calls(res.run.tool_calls)] == [False, True]
