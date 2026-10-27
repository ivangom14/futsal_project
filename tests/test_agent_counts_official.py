"""Recuentos exactos y datos oficiales: filtros y `count` de list_matches, lista recortada marcada como incompleta, y la fuente oficial de
goleadores frente a las actas (sin reconstruir por jornada; lo oficial ya obtenido se conserva al agotar el límite). LLM guionizado, sin red."""
import asyncio
import json
import re
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import (
    DEFAULT_MAX_ROWS,
    LIMIT_REACHED_MESSAGE,
    SYSTEM_PROMPT,
    Agent,
    AgentResult,
    compact_result,
    limit_message,
)
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server
from futsal.repositories.table_queries import SCORERS_NOTE

# 182 partidos: 23 finalizados (22 entre los 40 primeros) y 159 programados, como en la base real del informe
MATCHES = [{"id": i, "round_number": 1 + i // 7, "home_team": f"L{i}", "away_team": f"V{i}", "status": "finished" if (i < 22 or i == 60) else "scheduled",
            "home_score": 1, "away_score": 0} for i in range(182)]
SCORER_TABLE = {"group_id": 1, "group": "Grupo 2", "competition": "LIGA UNO FS", "status": "ok", "source": {"captured_at": "2026-10-25T10:00:00+00:00"},
                "scorers": [{"rank": 1, "tied_with": 1, "player": "RODRIGUES, MARCOS", "team": "CLUB A", "goals": 7},
                            {"rank": 1, "tied_with": 1, "player": "DIAZ, MIGUEL", "team": "CLUB B", "goals": 7},
                            {"rank": 3, "player": "SANZ, PAU", "team": "CLUB C", "goals": 5}]}
STANDINGS = {"group_id": 1, "group": "Grupo 2", "competition": "LIGA UNO FS", "status": "ok", "source": {"round": 5, "captured_at": "2026-10-24T09:00:00+00:00"},
             "standings": [{"position": 1, "team": "CLUB A", "points": 12}, {"position": 2, "team": "CLUB B", "points": 10}]}
ACTAS = {"scorers": [{"player": "ACTAS, PEPE", "goals": 99}], "coverage": {"complete": False}}
requests: list[httpx.Request] = []


def _api(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    path, q = request.url.path, request.url.params
    if path == "/groups/1/matches":
        rows = [m for m in MATCHES if not q.get("status") or m["status"] == q["status"]]
        limit = int(q["limit"]) if q.get("limit") else None
        body: dict[str, Any] = {"items": rows[:limit] if limit else rows, "count": len(rows)}
        if limit:
            body["shown"] = min(limit, len(rows))
        return httpx.Response(200, json=body)
    routes = {"/groups/1/scorer-table": SCORER_TABLE, "/groups/1/standings": STANDINGS, "/groups/1/scorers": ACTAS}
    if path in routes:
        return httpx.Response(200, json=routes[path])
    if path == "/groups/500/matches":
        return httpx.Response(500, json={"detail": "caída"})
    if path == "/competitions":
        return httpx.Response(200, json={"items": [], "competitions": [], "count": 0})
    return httpx.Response(404, json={"detail": "no encontrado"})


class Script:
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script, self.messages = list(script), []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.messages = messages
        return self.script.pop(0) if self.script else LLMResponse("fin", [])


def _call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall(name, name, args)])


def _run(llm: Script, question: str = "pregunta", max_calls: int = 5) -> AgentResult:
    requests.clear()
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api)))

    async def go() -> AgentResult:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), max_calls).run(question)  # type: ignore[arg-type]

    return asyncio.run(go())


def _seen(llm: Script) -> list[str]:
    return [r.content for m in llm.messages for r in m.tool_results]


# --- A. recuentos --------------------------------------------------------------------------------------------------------------
def test_filtered_counts_are_exact_and_reach_the_model_as_count_not_as_rows() -> None:
    llm = Script([_call("list_matches", group_id=1, status="finished", limit=1), _call("list_matches", group_id=1, limit=1),
                  LLMResponse("Se han jugado 23 partidos y quedan 159 (de 182).", [])])
    res = _run(llm)
    assert res.answer == "Se han jugado 23 partidos y quedan 159 (de 182)." and res.tool_calls == 2 and res.guardrail is None
    sent = [(dict(r.url.params)) for r in requests if r.url.path == "/groups/1/matches"]
    assert sent == [{"status": "finished", "limit": "1"}, {"limit": "1"}]                       # filtros y límite llegan a la API
    seen = _seen(llm)
    assert '"count":23' in seen[0] and '"shown":1' in seen[0] and '"count":182' in seen[1]
    assert 182 - 23 == 159 == len([m for m in MATCHES if m["status"] == "scheduled"])


def test_an_unfiltered_list_is_marked_incomplete_with_the_total_and_counting_its_rows_would_be_wrong() -> None:
    llm = Script([_call("list_matches", group_id=1), LLMResponse("fin", [])])
    _run(llm)
    data = json.loads(_seen(llm)[0])
    assert data["count"] == 182 and data["truncated"]["shown"] == DEFAULT_MAX_ROWS and data["truncated"]["total"] == 182
    assert "INCOMPLETA" in data["truncated"]["hint"] and "limit=1" in data["truncated"]["hint"] and "count" in data["truncated"]["hint"]
    visible_played = sum(m["status"] == "finished" for m in data["matches"])
    assert visible_played == 22 != 23                                                            # el error observado: contar las filas visibles
    cut = json.loads(compact_result(json.dumps({"items": list(range(100)), "count": 100}), 40))
    assert cut["truncated"]["total"] == 100 and "INCOMPLETA" in cut["truncated"]["hint"]


def test_prompt_asks_to_count_with_limit_1_and_not_with_a_truncated_list() -> None:
    assert "Para contar usa limit=1 y lee count" in SYSTEM_PROMPT and "nunca cuentes las filas de una lista recortada" in SYSTEM_PROMPT


def test_without_reliable_totals_no_figures_are_invented() -> None:
    errored = Script([_call("list_matches", group_id=500), LLMResponse("No he podido consultar los partidos.", [])])
    res = _run(errored)
    assert res.answer == "No he podido consultar los partidos." and "error_dependencia" in _seen(errored)[0]
    looping = Script([_call("list_matches", group_id=1) for _ in range(8)])                       # solo listas recortadas y sin respuesta
    res2 = _run(looping)
    assert res2.answer == LIMIT_REACHED_MESSAGE and not re.search(r"\d", res2.answer) and res2.official == []


# --- B. goleadores oficiales vs actas ----------------------------------------------------------------------------------------------
def test_official_scorer_source_states_it_is_the_current_table_without_round_breakdown() -> None:
    assert "ACTUAL" in SCORERS_NOTE and "desglose por jornada" in SCORERS_NOTE and "top_scorers" in SCORERS_NOTE
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    assert "ACTUAL" in (tools["get_scorer_table"].description or "") and "desglose por jornada" in (tools["get_scorer_table"].description or "")
    assert "No es la tabla oficial" in (tools["top_scorers"].description or "")                  # la fuente de actas sigue declarada como tal


def test_official_question_queries_the_official_source_and_keeps_its_answer() -> None:
    llm = Script([_call("get_scorer_table", group_id=1, limit=3), LLMResponse("Máximos goleadores: RODRIGUES y DIAZ con 7 (tabla oficial).", [])])
    res = _run(llm, "¿Quiénes son los máximos goleadores oficiales?")
    assert res.answer.startswith("Máximos goleadores") and [r.url.path for r in requests if "scorer" in r.url.path] == ["/groups/1/scorer-table"]
    assert len(res.official) == 1 and res.guardrail is None


def test_when_the_limit_is_hit_the_official_data_already_retrieved_is_kept_without_mixing_in_actas() -> None:
    llm = Script([_call("get_scorer_table", group_id=1), _call("list_rounds", group_id=1), _call("top_scorers", group_id=1, round_id=2),
                  _call("top_scorers", group_id=1, round_id=1), _call("top_scorers", group_id=1), _call("top_scorers", group_id=1, min_goals=2)])
    res = _run(llm, "¿Quién era el máximo goleador tras la jornada 2?")
    assert res.guardrail == "limite_herramientas" and res.error == "límite de 5 tool calls alcanzado"   # el fallo sigue visible
    assert res.answer.startswith(LIMIT_REACHED_MESSAGE)
    assert "RODRIGUES, MARCOS (CLUB A) y DIAZ, MIGUEL (CLUB B) con 7 goles" in res.answer           # empate completo, copiado de la tabla oficial
    assert "tabla actual con captura 2026-10-25" in res.answer and "no se ha podido verificar" in res.answer
    assert "ACTAS, PEPE" not in res.answer and "99" not in res.answer                              # nada de las actas como si fuera oficial


def test_limit_message_also_keeps_the_official_leader_of_the_standings_and_skips_non_valid_tables() -> None:
    assert "líder CLUB A con 12 puntos" in limit_message([STANDINGS]) and "jornada 5 con captura 2026-10-24" in limit_message([STANDINGS])
    assert limit_message([{**SCORER_TABLE, "status": "empty", "scorers": []}]) == LIMIT_REACHED_MESSAGE
    only_rank_3 = {**SCORER_TABLE, "scorers": SCORER_TABLE["scorers"][2:]}                          # búsqueda por nombre sin el líder: no se inventa uno
    assert limit_message([only_rank_3]) == LIMIT_REACHED_MESSAGE
    twice = limit_message([SCORER_TABLE, SCORER_TABLE])
    assert twice.count("máximo goleador") == 1                                                        # sin duplicados


def test_legitimate_actas_queries_are_not_restricted_and_normal_flows_are_unchanged() -> None:
    actas = Script([_call("top_scorers", group_id=1, min_goals=2), LLMResponse("Según las actas (cobertura parcial): ACTAS, PEPE 99.", [])])
    res = _run(actas, "¿Quién marcó más en las actas?")
    assert res.tool_calls == 1 and res.answer.startswith("Según las actas") and res.official == []
    std = Script([_call("get_standings", group_id=1), LLMResponse("Lidera CLUB A con 12 puntos (jornada 5).", [])])
    res2 = _run(std, "¿Quién lidera la clasificación?")
    assert res2.answer.startswith("Lidera CLUB A") and res2.guardrail is None and res2.error is None and len(res2.official) == 1
