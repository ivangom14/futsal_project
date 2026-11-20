"""AGENT_ARCH=v2: reglas deterministas, herramientas v2 por el MCP y legacy intacto. LLM guionizado y MCP simulado con los esquemas
REALES de las herramientas v2 del servidor (sin red ni BD)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent
from futsal.agent.criteria import figures_without_table
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient, strip_titles
from futsal.agent.memory import Turn
from futsal.agent.messages import NOT_IN_REPORT
from futsal.agent.prompts import MODULE_V2
from futsal.agent.tokens import est_tokens
from futsal.agent.understanding_eval import FakeMcp
from futsal.agent.v2.rules import BROAD_ACT_MESSAGE, SUGGESTION_NOTE
from futsal.mcp_server.server import create_server
from futsal.mcp_server.v2_tools import V2_TOOL_NAMES

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "prompt_legacy_golden.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
CLARIFY = "¿De qué competición o grupo quieres consultarlo?"
V2_TOOLS = {"find_player", "find_official", "officials_ranking", "matches_by_date", "club_overview", "scorers_ranking", "teams_ranking"}
EMPTY = {"items": [], "count": 0, "shown": 0, "notes": [], "freshness": None}


class Rec:
    """LLM guionizado que además registra lo que recibe (system, herramientas y mensajes de cada vuelta)."""

    provider, model = "fake", "fake"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps, self.systems, self.tools, self.seen = list(steps), [], [], []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.systems.append(system)
        self.tools.append(tools)
        self.seen.append(list(messages))
        return self.steps.pop(0)


def v2_tool_defs() -> list[dict[str, Any]]:
    """Esquemas que publica el servidor MCP real para las herramientas v2 (como los entrega `SessionMcpClient`)."""
    tools = asyncio.run(create_server(httpx.Client(base_url="http://api.invalid")).list_tools())
    return [{"name": t.name, "description": t.description or "", "input_schema": strip_titles(t.inputSchema)} for t in tools
            if t.name in V2_TOOL_NAMES]


V2_DEFS = v2_tool_defs()


class Stub(FakeMcp):
    """MCP simulado: las v1 del `FakeMcp` de siempre y las v2 con los esquemas reales y respuestas guionizadas por herramienta.
    Guarda (nombre, argumentos) de cada llamada v2 en `calls_v2`."""

    def __init__(self, replies: dict[str, dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.replies, self.calls_v2 = replies or {}, []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [*await super().list_tools(), *V2_DEFS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name not in V2_TOOL_NAMES:
            return await super().call_tool(name, arguments)
        assert arguments.get("sport") == "futsal"  # Fase 1: el agente inyecta el deporte de la petición en cada herramienta v2
        self.calls_v2.append((name, {k: v for k, v in arguments.items() if k != "sport"}))
        return False, json.dumps(self.replies.get(name, EMPTY), ensure_ascii=False)


def call(tool: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall("c", tool, args)])


def ask(llm: Rec, question: str, history: list[Turn] | None = None, reg: Stub | None = None, **kw: Any) -> Any:
    agent = Agent(llm, reg or Stub(), 8, **kw)
    agent._clock = lambda: NOW
    return asyncio.run(agent.run(question, history or [], sport="futsal"))


def last_user(llm: Rec, turn: int = 0) -> str:
    return next(m.text for m in reversed(llm.seen[turn]) if m.role == "user")


# --- 1. AGENT_ARCH y legacy idéntico ----------------------------------------------------------------------------------------------
def test_v2_exposes_v1_plus_seven_tools_a_short_prompt_and_traces_arch() -> None:
    llm = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])])
    res = ask(llm, "Cuéntame un chiste")
    names = [t["name"] for t in llm.tools[0]]
    assert names[:8] == ["list_competitions", "find_teams", "list_teams", "list_rounds", "list_matches", "get_standings", "get_scorer_table", "search_rules"]
    assert set(names[8:]) == V2_TOOLS and llm.systems[0].startswith(GOLDEN["system_prompt"] + MODULE_V2)
    assert est_tokens(len(MODULE_V2)) <= 400
    for t in llm.tools[0][8:]:
        assert "title" not in json.dumps(t) and "\n" not in t["description"] and "null" not in json.dumps(t["input_schema"])
    assert res.run is not None and res.run.arch == "v2" and res.run.to_dict()["arch"] == "v2"


# --- 2. ejecución en proceso -------------------------------------------------------------------------------------------------------
def test_v2_tools_run_in_process_and_results_are_compacted() -> None:
    rows = [{"ref": f"match:{i}", "date": "2026-11-07", "time": "16:00", "home": f"L{i}", "away": f"V{i}", "score": None} for i in range(60)]
    reg = Stub({"matches_by_date": {"items": rows, "count": 60, "shown": 60, "notes": ["n"], "freshness": None}})
    llm = Rec([call("matches_by_date", date_from="2026-11-07"), LLMResponse("ok", [])])
    res = ask(llm, "Partidos del 7/11", reg=reg, max_rows=40)
    sent = json.loads(next(m for m in llm.seen[1] if m.role == "tool").tool_results[0].content)
    # un día entero llega sin recortar (hasta 100 filas): así el modelo no pagina con varias llamadas
    assert reg.calls_v2 and reg.calls_v2[0][0] == "matches_by_date" and len(sent["items"]) == 60 and "truncated" not in sent and sent["count"] == 60
    assert res.tool_calls == 1 and res.answer == "ok"
    clubs = Stub({"club_overview": {"items": rows, "count": 60, "shown": 60, "notes": [], "freshness": None}})
    llm2 = Rec([call("club_overview", name="alamo"), LLMResponse("ok", [])])
    ask(llm2, "Equipos del Álamo", reg=clubs, max_rows=40)
    sent2 = json.loads(next(m for m in llm2.seen[1] if m.role == "tool").tool_results[0].content)
    assert len(sent2["items"]) == 40 and sent2["truncated"]["total"] == 60  # el resto de herramientas sigue recortando


def test_v1_id_validation_is_kept_for_v1_tools() -> None:
    llm = Rec([call("list_matches", group_id=1101, team_id=999999), LLMResponse("ok", [])])
    ask(llm, "Último partido del Dosa")
    assert "no procede de ninguna herramienta" in next(m for m in llm.seen[1] if m.role == "tool").tool_results[0].content


def test_two_v2_tools_in_one_answer_run_in_parallel() -> None:
    class Parallel(Stub):
        """Cada llamada v2 espera a que la otra también esté en curso: solo termina si van a la vez."""

        def __init__(self) -> None:
            super().__init__()
            self.in_flight = 0
            self.both: asyncio.Event | None = None

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
            if name not in V2_TOOL_NAMES:
                return await super().call_tool(name, arguments)
            self.both = self.both or asyncio.Event()
            self.in_flight += 1
            if self.in_flight == 2:
                self.both.set()
            await asyncio.wait_for(self.both.wait(), timeout=3)
            return await super().call_tool(name, arguments)

    reg = Parallel()
    llm = Rec([LLMResponse("", [ToolCall("a", "find_player", {"name": "Pablo Quintanar"}), ToolCall("b", "find_official", {"name": "Ramos"})]),
               LLMResponse("ok", [])])
    res = ask(llm, "¿Dónde juega Pablo Quintanar y quién es el árbitro Ramos?", reg=reg)
    results = next(m for m in llm.seen[1] if m.role == "tool").tool_results
    assert [r.is_error for r in results] == [False, False] and {c[0] for c in reg.calls_v2} == {"find_player", "find_official"} and res.tool_calls == 2


def api_stub(seen: list[httpx.Request]) -> httpx.Client:
    """API simulada para el servidor MCP real: 422 en fechas mal formadas (como FastAPI) y un ranking fijo."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v2/matches" and not request.url.params.get("date_from", "").startswith("20"):
            return httpx.Response(422, json={"detail": "parámetros inválidos: query.date_from: Input should be a valid date"})
        if request.url.path == "/v2/rankings/teams":
            return httpx.Response(200, json={"items": [{"rank": 1, "team": "X"}], "count": 1, "shown": 1, "notes": [], "freshness": None,
                                             "metric": request.url.params["metric"]})
        return httpx.Response(404, json={"detail": "no existe"})
    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))


def test_invalid_v2_arguments_return_a_validation_error_without_crashing() -> None:
    llm = Rec([call("teams_ranking", metric="nada"), call("matches_by_date", date_from="mañana"), LLMResponse("ok", [])])
    seen: list[httpx.Request] = []

    async def go() -> Any:
        async with connect(create_server(api_stub(seen))._mcp_server) as session:
            agent = Agent(llm, SessionMcpClient(session), 8)
            agent._clock = lambda: NOW
            return await agent.run("Ranking de equipos", sport="futsal")

    res = asyncio.run(go())
    errors = [r.content for m in llm.seen[2] if m.role == "tool" for r in m.tool_results]
    assert len(errors) == 2 and "metric" in errors[0] and "error_validacion" in errors[1] and res.answer == "ok"
    assert [r.url.path for r in seen if r.url.path.startswith("/v2/")] == ["/v2/matches"]  # la métrica inválida ni llega a la API


# --- 3. fechas ---------------------------------------------------------------------------------------------------------------------
def test_date_note_and_deterministic_repair_of_matches_by_date() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-11-14", date_to="2026-11-14", time_from="00:00"), LLMResponse("ok", [])])
    res = ask(llm, "Dime los partidos del sábado 7 de noviembre por la tarde", reg=reg)
    note = last_user(llm)
    assert "2026-11-07" in note and "15:00" in note and "23:59" in note and "Nota de la aplicación" in note
    assert reg.calls_v2 == [("matches_by_date", {"date_from": "2026-11-07", "date_to": "2026-11-07", "time_from": "15:00", "time_to": "23:59"})]
    rec = res.run.tool_calls[0] if res.run else None
    assert rec is not None and rec.repaired is not None and rec.repaired["source"] == "dates"
    assert rec.original_arguments == {"date_from": "2026-11-14", "date_to": "2026-11-14", "time_from": "00:00"}


def test_matching_arguments_are_not_repaired() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-11-07", time_from="15:00", time_to="23:59"), LLMResponse("ok", [])])
    res = ask(llm, "Dime los partidos del sábado 7 de noviembre por la tarde", reg=reg)
    assert res.run is not None and res.run.tool_calls[0].repaired is None and reg.calls_v2[0][1]["date_from"] == "2026-11-07"


def test_no_date_hint_leaves_arguments_alone() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-12-01"), LLMResponse("ok", [])])
    ask(llm, "Partidos del primero de diciembre", reg=reg)
    assert reg.calls_v2 == [("matches_by_date", {"date_from": "2026-12-01"})]


# --- 4. alcance amplio y aclaración repetida --------------------------------------------------------------------------------------
def test_broad_scope_forbids_the_scope_clarification() -> None:
    llm = Rec([LLMResponse(CLARIFY, []), call("matches_by_date", date_from="2026-11-14"), LLMResponse("Hay 3 partidos.", [])])
    res = ask(llm, "Dime todos los partidos del sábado 14 de noviembre")
    assert "Alcance amplio" in last_user(llm) and last_user(llm, 1) == BROAD_ACT_MESSAGE
    assert res.answer == "Hay 3 partidos." and res.guardrail is None and res.tool_calls == 1
    assert res.run is not None and res.run.context["v2"]["clarification_blocked"] == 1


def test_without_broad_scope_the_first_clarification_is_still_allowed() -> None:
    llm = Rec([LLMResponse(CLARIFY, [])])
    res = ask(llm, "¿Quién es el máximo goleador del grupo?")
    assert res.answer == CLARIFY and res.guardrail == "aclaracion_ambito" and res.tool_calls == 0


def test_second_clarification_in_a_row_is_never_asked_again() -> None:
    history = [Turn("¿Quién es el máximo goleador del grupo?", CLARIFY)]
    llm = Rec([LLMResponse(CLARIFY, []), call("scorers_ranking", category="senior"), LLMResponse("Asumo todas las ligas: X.", [])])
    res = ask(llm, "pues no sé", history)
    assert last_user(llm, 1) == BROAD_ACT_MESSAGE and res.tool_calls == 1 and res.answer.endswith("Asumo todas las ligas: X.")
    assert res.answer.startswith("Entiendo que quieres consultarlo en todas las competiciones")  # la suposición se declara una vez


def test_real_entity_ambiguity_is_still_asked_even_with_broad_scope() -> None:
    cands = {"items": [{"base_name": "pinto", "ambiguous": True, "teams": []}, {"base_name": "atletico pinto", "ambiguous": True, "teams": []}],
             "count": 2, "shown": 2, "notes": ["varios clubes con nombre distinto"], "freshness": None}
    question = "¿A qué club te refieres: «Pinto» o «Atlético Pinto»?"
    llm = Rec([call("club_overview", name="pinto", results=True), LLMResponse(question, [])])
    res = ask(llm, "Resultados de todos los equipos del Pinto", reg=Stub({"club_overview": cands}))
    assert res.answer == question and res.tool_calls == 1 and res.guardrail is None


def test_deterministic_context_turns_ask_into_act_only_with_v2_and_broad_scope() -> None:
    q = "¿Cómo va el DOSA en todas las competiciones?"
    llm = Rec([call("club_overview", name="DOSA", results=True), LLMResponse("ok", [])])
    res = ask(llm, q, [Turn("¿Cómo va la tercera división?", "…")])
    assert res.run is not None and res.run.context["status"] == "act" and res.run.context["reason"] == "alcance amplio explícito"
    assert "Alcance amplio" in last_user(llm) and res.tool_calls == 1 and res.answer == "ok"


def test_deterministic_context_second_scope_question_assumes_and_declares() -> None:
    q = "¿Cómo va el DOSA?"
    first = ask(Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])]), q, [Turn("¿Cómo va la tercera división?", "…")])
    assert first.run is not None and first.run.context["status"] == "ask" and first.state is not None
    llm = Rec([call("club_overview", name="DOSA", results=True), LLMResponse("Va bien.", [])])
    agent = Agent(llm, Stub(), 8)
    hist = [Turn("¿Cómo va la tercera división?", "…"), Turn(q, first.answer or "¿A cuál de estas competiciones te refieres?")]
    res = asyncio.run(agent.run("el dosa, no sé", hist, state=first.state, sport="futsal"))
    assert res.run is not None and res.run.context["status"] in ("assume", "act")


@pytest.mark.parametrize(("q", "tool", "args"), [
    ("¿Qué partidos hay el sábado 14 de noviembre?", "matches_by_date", {"date_from": "2026-11-14"}),
    ("¿Qué partidos ha arbitrado Guillermo Redondo Juarez?", "find_official", {"name": "Guillermo Redondo Juarez"}),
    ("¿Qué equipo senior tiene más victorias?", "teams_ranking", {"category": "senior", "metric": "wins"}),
    ("¿Qué equipos tiene el CLUB PILARISTAS?", "club_overview", {"name": "PILARISTAS"}),
])
def test_queries_that_span_every_competition_never_ask_which_one(q: str, tool: str, args: dict[str, Any]) -> None:
    """Personas, un día, una categoría entera o un club: sin «todos» explícito, tampoco se pregunta la competición (v2)."""
    llm = Rec([LLMResponse(CLARIFY, []), call(tool, **args), LLMResponse("ok", [])])
    res = ask(llm, q)
    assert last_user(llm, 1) == BROAD_ACT_MESSAGE and res.answer == "ok" and res.tool_calls == 1


def test_unknown_proper_name_is_searched_instead_of_asking_the_competition() -> None:
    q = "¿Cuántos puntos tiene Real Sala Aranjuez Norte?"
    llm = Rec([call("club_overview", name="Real Sala Aranjuez Norte"), LLMResponse("No consta ningún equipo con ese nombre.", [])])
    res = ask(llm, q, [])
    assert res.run is not None and res.run.context["status"] == "act" and res.tool_calls == 1


def test_category_ranking_gets_the_not_comparable_note() -> None:
    from futsal.agent.v2.rules import RANKING_NOTE
    llm = Rec([call("teams_ranking", category="senior", metric="goals_against"), LLMResponse("ok", [])])
    ask(llm, "¿Qué equipo senior ha encajado menos goles?")
    assert RANKING_NOTE in llm.seen[0][-1].text
    plain = Rec([call("get_standings", group_id=101), LLMResponse("ok", [])])
    ask(plain, "¿Quién va primero en la PRIMERA AFICIONADO FS Grupo 1?")
    assert RANKING_NOTE not in plain.seen[0][-1].text


# --- 5. premisas y sugerencias -----------------------------------------------------------------------------------------------------
def test_premise_note_forces_verification() -> None:
    llm = Rec([call("find_player", name="Pablo Quintanar"), LLMResponse("No consta.", [])])
    ask(llm, "Creo que juega en el san Felipe Neri", [Turn("¿En qué equipo juega Pablo Quintanar?", "En Móstoles FS.")])
    note = last_user(llm)
    assert "Creo que juega en el san Felipe Neri" in note and "compruébalo con las herramientas" in note and "no lo confirma" in note
    plain = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])] * 3)
    ask(plain, "¿En qué equipo juega Pablo Quintanar?")
    assert "El usuario afirma" not in last_user(plain)


@pytest.mark.parametrize("text", ["Me han dicho que el Parla ganó", "¿No es verdad que el Parla ganó?", "Tengo entendido que Ramos pita en juvenil"])
def test_premise_patterns(text: str) -> None:
    llm = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])] * 3)
    ask(llm, text)
    assert "El usuario afirma" in last_user(llm)


def test_suggestions_without_items_get_the_mandatory_note() -> None:
    body = {"items": [], "count": 0, "shown": 0, "notes": ["no figura; nombres parecidos: X"], "freshness": None,
            "suggestions": [{"ref": "player:1", "name": "X", "similarity": 0.5}]}
    llm = Rec([call("find_official", name="Álvaro Bermejo Suárez"), LLMResponse("No figura.", [])])
    ask(llm, "¿Qué partidos ha pitado el árbitro Álvaro Bermejo Suárez?", reg=Stub({"find_official": body}))
    sent = json.loads(next(m for m in llm.seen[1] if m.role == "tool").tool_results[0].content)
    assert SUGGESTION_NOTE in sent["notes"] and sent["suggestions"][0]["name"] == "X"
    with_items = {**body, "items": [{"name": "Y"}], "count": 1, "shown": 1}
    llm2 = Rec([call("find_official", name="Y"), LLMResponse("ok", [])])
    ask(llm2, "¿Qué partidos ha pitado el árbitro Y?", reg=Stub({"find_official": with_items}))
    assert SUGGESTION_NOTE not in next(m for m in llm2.seen[1] if m.role == "tool").tool_results[0].content


# --- 6. servidor MCP v2 ------------------------------------------------------------------------------------------------------------
def test_single_mcp_server_publishes_v1_and_v2_and_v2_calls_go_through_the_api() -> None:
    seen: list[httpx.Request] = []
    server = create_server(api_stub(seen))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert V2_TOOLS <= names and {"list_matches", "get_standings", "find_teams"} <= names  # un solo servidor: v1 y v2
    assert all(t["description"] and t["input_schema"].get("properties") is not None for t in V2_DEFS)
    out = asyncio.run(server.call_tool("teams_ranking", {"sport": "futsal", "metric": "goals_for"}))
    payload = out[1] if isinstance(out, tuple) else out
    assert isinstance(payload, dict) and payload["items"][0]["rank"] == 1 and payload["metric"] == "goals_for"
    assert seen[0].url.path == "/v2/rankings/teams" and dict(seen[0].url.params) == {"sport": "futsal", "metric": "goals_for", "min_played": "1", "limit": "10",
                                                                                    "offset": "0", "compact": "true", "competition_kind": "league"}


def test_agent_and_mcp_never_touch_the_database() -> None:
    """Arquitectura obligatoria agente → MCP → API → BD: el agente en ejecución y el servidor MCP no importan la capa de datos.
    (`agent/evaluate.py` y `agent/preflight.py` son herramientas de evaluación que leen la verdad de la BD: fuera del camino del chat.)"""
    import re

    root = Path(__file__).parents[1] / "src" / "futsal"
    forbidden = re.compile(r"^\s*(from|import)\s+(futsal\.(db|repositories)\b|sqlalchemy\b)", re.MULTILINE)
    allowed = {"agent/evaluate.py", "agent/preflight.py"}
    offenders = [str(f.relative_to(root)) for pkg in ("agent", "mcp_server") for f in (root / pkg).rglob("*.py")
                 if str(f.relative_to(root)) not in allowed and forbidden.search(f.read_text(encoding="utf-8"))]
    assert offenders == [], f"acceden a la BD sin pasar por la API: {offenders}"


def test_empty_final_answer_after_tools_is_asked_once_for_a_short_answer() -> None:
    from futsal.agent.agent import EMPTY_ANSWER_MESSAGE
    llm = Rec([call("matches_by_date", date_from="2026-11-14"), LLMResponse("", []), LLMResponse("Hay 3 partidos.", [])])
    res = ask(llm, "Dime todos los partidos del sábado 14 de noviembre")
    assert llm.seen[-1][-1].text == EMPTY_ANSWER_MESSAGE and res.answer == "Hay 3 partidos." and res.tool_calls == 1
    twice = ask(Rec([call("matches_by_date", date_from="2026-11-14"), LLMResponse("", []), LLMResponse("", [])]),
                "Dime todos los partidos del sábado 14 de noviembre")
    assert twice.answer == ""  # una sola vez: no hay bucle


def test_v2_tool_limit_with_data_asks_for_a_final_answer_instead_of_the_generic_notice() -> None:
    from futsal.agent.agent import LIMIT_FINAL_MESSAGE
    page = [call("matches_by_date", date_from="2026-11-14", offset=i * 40) for i in range(8)]  # el agente de las pruebas admite 8
    six = LLMResponse("", [ToolCall(f"x{i}", "matches_by_date", {"date_from": "2026-11-14", "offset": i}) for i in range(2)])
    llm = Rec([*page, six, LLMResponse("Hay 120 partidos; estos son los primeros.", [])])
    res = ask(llm, "¿Qué partidos hay el sábado 14 de noviembre?")
    assert llm.seen[-1][-1].text == LIMIT_FINAL_MESSAGE and res.answer.startswith("Hay 120 partidos") and res.tool_calls == 8
    insist = ask(Rec([*page, six, six]), "¿Qué partidos hay el sábado 14 de noviembre?")
    assert insist.guardrail == "limite_herramientas"  # una sola vez


def test_short_follow_up_of_a_date_question_keeps_the_broad_scope_and_the_day() -> None:
    hist = [Turn("¿Qué partidos hay el sábado 14 de noviembre?", "Hay 30 partidos.")]
    llm = Rec([LLMResponse(CLARIFY, []), call("matches_by_date", date_from="2026-11-14", category="juvenil"), LLMResponse("Hay 8.", [])])
    res = ask(llm, "¿y solo los de juvenil?", hist)
    assert "2026-11-14" in llm.seen[0][-1].text and last_user(llm, 1) == BROAD_ACT_MESSAGE and res.answer == "Hay 8."


def test_insistent_clarification_is_counted_in_the_trace() -> None:
    llm = Rec([LLMResponse(CLARIFY, [])] * 6)
    res = ask(llm, "Dime todos los partidos del sábado 14 de noviembre")
    assert res.run is not None and res.run.context["v2"]["clarification_blocked"] == 2 and res.run.context["v2"]["clarify_insisted"] >= 1


def test_every_list_tool_accepts_limit() -> None:
    for t in V2_DEFS:
        assert t["input_schema"]["properties"]["limit"]["maximum"] == 100, t["name"]


# --- 7. respaldo determinista ante una aclaración insistente -----------------------------------------------------------------------
REPEATED = [Turn("¿Quién es el máximo goleador del grupo?", CLARIFY)]  # aclaración ya hecha: se activan los empujones sin «todos»
DAY_Q = "Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde"


def _match(i: int) -> dict[str, Any]:
    return {"ref": f"match:{i}", "date": "2026-11-07", "time": f"{15 + i % 8:02d}:00", "home": {"ref": f"team:{i}", "name": f"LOCAL {i}"},
            "away": {"ref": f"team:{i + 100}", "name": f"VISITANTE {i}"}, "score": None, "competition": {"ref": "comp:1", "name": "TERCERA"},
            "group": {"ref": "group:1", "name": "Grupo 1"}}


def _fallback_run(question: str, replies: dict[str, dict[str, Any]], history: list[Turn] | None = None) -> tuple[Any, Stub, Rec]:
    reg, llm = Stub(replies), Rec([LLMResponse(CLARIFY, [])] * 6)
    return ask(llm, question, history, reg=reg), reg, llm


def test_fallback_matches_by_date_composes_total_list_and_all_notes() -> None:
    body = {"items": [_match(i) for i in range(30)], "count": 48, "shown": 30, "notes": ["filtro horario: solo partidos con hora conocida", "otra nota"], "freshness": None}
    res, reg, llm = _fallback_run(DAY_Q, {"matches_by_date": body})
    assert len(llm.seen) == 3 and res.tool_calls == 1  # 2 empujones y la tercera vuelta insistente; sin más llamadas al LLM
    assert reg.calls_v2 == [("matches_by_date", {"date_from": "2026-11-07", "date_to": "2026-11-07", "time_from": "15:00", "time_to": "23:59"})]  # primera página (10 por defecto) y count = total
    lines = res.answer.splitlines()
    assert lines[0] == "48 partidos el sábado 7 de noviembre de 2026 entre las 15:00 y las 23:59:"
    assert sum(x.startswith("- 2026-") for x in lines) == 20 and "- y 28 más" in lines
    assert all(n in res.answer for n in body["notes"]) and "LOCAL 0 - VISITANTE 0" in res.answer and CLARIFY not in res.answer
    v2 = res.run.context["v2"] if res.run else {}
    assert v2["fallback"] == {"plan": "matches_by_date", "tool": "matches_by_date", "args": reg.calls_v2[0][1]} and v2["answer_source"] == "deterministic_fallback"
    assert figures_without_table(res.answer, [body], DAY_Q + " (franja interpretada 15:00-23:59)") == []  # ninguna cifra ajena a la herramienta, la pregunta y la franja interpretada


def test_fallback_top_scorer_of_all_senior_leagues() -> None:
    q = "Cuál es el máximo goleador de todas las ligas senior?"
    body = {"items": [{"rank": 1, "player": {"name": "GOLEADOR, A"}, "team": {"name": "EQ"}, "competition": {"name": "TERCERA"}, "group": {"name": "G1"}, "goals": 9, "matches": 7}],
            "count": 1, "shown": 1, "notes": ["compara grupos distintos"], "freshness": None}
    res, reg, _ = _fallback_run(q, {"scorers_ranking": body})
    assert reg.calls_v2 == [("scorers_ranking", {"category": "senior"})]
    assert "GOLEADOR, A" in res.answer and "9 goles" in res.answer and "compara grupos distintos" in res.answer
    assert res.run is not None and res.run.context["v2"]["fallback"]["plan"] == "scorers_ranking"
    assert figures_without_table(res.answer, [body], q) == []


def _club(name: str, ambiguous: bool) -> dict[str, Any]:
    return {"club": {"name": name, "ambiguous": ambiguous}, "team": {"name": f"{name} A"}, "competition": {"name": "C"}, "group": {"name": "G"},
            "summary": {"played": 3, "won": 2, "drawn": 1, "lost": 0, "goals_for": 9, "goals_against": 4}}


def test_fallback_club_unique_and_ambiguous() -> None:
    ok = {"items": [_club("C.D. ALAMO", False)], "count": 1, "shown": 1, "notes": ["agrupado por nombre"], "freshness": None}
    res, reg, _ = _fallback_run("Resultados de todos los equipos del álamo?", {"club_overview": ok})
    assert reg.calls_v2 == [("club_overview", {"name": "álamo", "results": True})]
    assert res.answer.startswith("1 equipo de C.D. ALAMO:") and "3 jugados, 2 ganados" in res.answer and "agrupado por nombre" in res.answer
    amb = {"items": [_club("PINTO", True), _club("ATLETICO PINTO", True)], "count": 2, "shown": 2, "notes": ["varios clubes"], "freshness": None}
    res2, _, _ = _fallback_run("Resultados de todos los equipos del pinto", {"club_overview": amb})
    # dos clubes con ese nombre: se pregunta cuál (la comprensión ya lo detecta con el catálogo), sin dar datos de ninguno
    assert "PINTO" in res2.answer and "ATL" in res2.answer and "?" in res2.answer and res2.guardrail == "aclaracion_ambito"


def test_fallback_official_matches_and_not_found_with_suggestions() -> None:
    q = "Qué partidos ha pitado el árbitro Álvaro Bermejo?"
    listed = {"date": "2026-11-07", "time": "16:00", "home": "A", "away": "B", "score": {"home": 2, "away": 1}, "competition": {"name": "TERCERA"}}
    body = {"items": [{"name": "BERMEJO, ALVARO", "matches": 1, "matches_list": [listed]}], "count": 1, "shown": 1, "notes": ["cobertura"], "freshness": None}
    res, reg, _ = _fallback_run(q, {"find_official": body}, REPEATED)
    assert reg.calls_v2 == [("find_official", {"name": "Álvaro Bermejo", "include_matches": True})]
    assert "BERMEJO, ALVARO figura en 1 partido:" in res.answer and "A - B (2-1)" in res.answer and "cobertura" in res.answer
    none = {"items": [], "count": 0, "shown": 0, "notes": ["no figura; nombres parecidos: X"], "suggestions": [{"name": "BERMEJO, ALBERTO"}], "freshness": None}
    res2, _, _ = _fallback_run(q, {"find_official": none}, REPEATED)
    assert "no figura como árbitro" in res2.answer and "posibles" in res2.answer and "BERMEJO, ALBERTO" in res2.answer and "nombres parecidos: X" in res2.answer


def test_fallback_is_skipped_without_plan_or_with_empty_result_and_keeps_the_clarification() -> None:
    history = [Turn("¿Quién es el máximo goleador del grupo?", CLARIFY)]
    res, reg, _ = _fallback_run("¿y eso qué tal?", {}, history)
    assert res.answer == CLARIFY and reg.calls_v2 == [] and res.run is not None and res.run.context["v2"]["fallback_skipped"] == "sin_plan"
    res2, reg2, _ = _fallback_run(DAY_Q, {"matches_by_date": EMPTY})  # count=0
    assert res2.answer == CLARIFY and len(reg2.calls_v2) == 1 and res2.run is not None
    assert res2.run.context["v2"]["fallback_skipped"] == "count_0" and "fallback" not in res2.run.context["v2"]
    res3, _, _ = _fallback_run("Dime todos los partidos del sábado 7 de noviembre y los máximos goleadores senior", {"matches_by_date": EMPTY})
    assert res3.answer == CLARIFY  # sin plan inequívoco: pista de fechas y otra pregunta a la vez no se resuelve


# --- 8. «no consta en el acta» -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q, what", [("¿En qué minuto entraron los suplentes del partido X - Y?", "las sustituciones"),
                                     ("¿cuál fue el resultado al descanso?", "el resultado por periodos"),
                                     ("¿Qué minuto añadido hubo en el partido del sábado?", "el minuto o tiempo añadido"),
                                     ("¿Cuál era el marcador tras cada gol del partido X - Y?", "el marcador tras cada gol")])
def test_not_in_report_answers_without_tools_or_llm(q: str, what: str) -> None:
    llm = Rec([])
    res = ask(llm, q)
    assert res.answer.startswith("Ese dato no consta en el acta del partido: el acta que publica la RFFM no recoge ") and what in res.answer
    assert res.answer == NOT_IN_REPORT.format(qué=res.answer.split("recoge ")[1][:-1])
    assert res.tool_calls == 0 and llm.seen == [] and res.run is not None and "not_in_report" in res.run.context["v2"]


@pytest.mark.parametrize("q", ["¿quién marcó en el partido X - Y?", "¿cuántos cambios se pueden hacer en futsal?", "¿Cuántas sustituciones permite el reglamento?",
                               "¿Cómo quedó el partido X - Y?"])
def test_not_in_report_leaves_other_questions_to_the_normal_flow(q: str) -> None:
    llm = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])] * 3)
    ask(llm, q)
    assert llm.seen and "no recoge" not in last_user(llm)


def test_not_in_report_mixed_question_adds_a_note_and_goes_to_the_llm() -> None:
    llm = Rec([LLMResponse("ok", [])] * 3)
    res = ask(llm, "¿Quién marcó en el partido X - Y y en qué minuto hubo cambios?")
    assert llm.seen and "no recoge" in last_user(llm) and "Nota de la aplicación" in last_user(llm) and res.run is not None
    assert res.run.context["v2"]["not_in_report"] == "substitutions"
