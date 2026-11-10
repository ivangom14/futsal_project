"""Agente v2 de extremo a extremo: LLM guionizado + registro real sobre PG desechable con datos sembrados (las 6 preguntas reales del responsable)."""

import asyncio
import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session
from tests.integration.test_api_v2_pg import Seed, at, utc, world  # noqa: F401 - `world` es un fixture
from tests.test_agent_v2 import NOW, Rec, call, last_user

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse
from futsal.agent.memory import Turn
from futsal.agent.understanding_eval import FakeMcp
from futsal.agent.v2.registry import V2Registry
from futsal.agent.v2.rules import SUGGESTION_NOTE
from futsal.db.models import Competition

pytestmark = pytest.mark.integration


@pytest.fixture()
def seeded(world: dict[str, Any]) -> Iterator[dict[str, Any]]:  # noqa: F811
    with Session(world["engine"]) as s, s.begin():
        sd = Seed(s, world["season"])
        comp = s.get(Competition, world["c_s2"])
        assert comp is not None
        g, t = sd.group(comp, "Grupo 2", ["C.D. ALAMO A", "C.D. ALAMO B", "RIVAL FS", "OTRO FS"])
        a, b, rival, other = t["C.D. ALAMO A"], t["C.D. ALAMO B"], t["RIVAL FS"], t["OTRO FS"]
        sd.match(g, a, rival, at(2026, 11, 7, 16), None)           # sábado por la tarde
        sd.match(g, other, b, at(2026, 11, 7, 19, 30), None)      # sábado por la tarde
        sd.match(g, rival, other, at(2026, 11, 7, 11), None)      # sábado por la mañana: queda fuera de la franja
        sd.match(g, a, other, at(2026, 10, 31, 17), (4, 1))
        sd.match(g, rival, b, at(2026, 10, 24, 17), (2, 2))
        for i in range(47):                                         # sábado 14: con el partido ya sembrado del mundo, 48 en total
            sd.match(g, a, other, at(2026, 11, 14, 8 + i % 14, 15 * (i % 4)), None)
    yield world


def registry(w: dict[str, Any]) -> V2Registry:
    return V2Registry(lambda: Session(w["engine"]), lambda: date(2026, 11, 9))


def converse(w: dict[str, Any], llm: Rec, question: str, history: list[Turn] | None = None) -> Any:
    agent = Agent(llm, FakeMcp(), 8, router_shadow=False, arch="v2", v2_registry=registry(w))
    agent._clock = lambda: NOW
    return asyncio.run(agent.run(question, history or []))


def tool_json(llm: Rec, turn: int = 1) -> list[dict[str, Any]]:
    return [json.loads(r.content) for m in llm.seen[turn] if m.role == "tool" for r in m.tool_results]


def test_q1_player_team(seeded: dict[str, Any]) -> None:
    llm = Rec([call("find_player", name="Pablo quintanar"), LLMResponse("Pablo Quintanar juega en MOSTOLES FS.", [])])
    res = converse(seeded, llm, "Sabes en qué equipo juega Pablo quintanar ?")
    body = tool_json(llm)[0]
    assert body["items"][0]["team"]["name"] == "MOSTOLES FS" and "MOSTOLES FS" in res.answer and res.tool_calls == 1 and res.guardrail is None


def test_q2_official_not_found_with_suggestions(seeded: dict[str, Any]) -> None:
    llm = Rec([call("find_official", name="Antonio Ramos Pereira"), LLMResponse("Ese árbitro no figura; quizá te refieras a RAMOS PEREZ, ANTONIO.", [])])
    res = converse(seeded, llm, "Sabes que partidos ha pitado el árbitro Antonio Ramos Pereira ?")
    body = tool_json(llm)[0]
    assert body["items"] == [] and body["suggestions"][0]["name"] == "RAMOS PEREZ, ANTONIO" and SUGGESTION_NOTE in body["notes"]
    assert "no figura" in res.answer


def test_q3_matches_saturday_afternoon_without_clarification(seeded: dict[str, Any]) -> None:
    llm = Rec([LLMResponse("¿De qué competición o grupo quieres los partidos?", []),
               call("matches_by_date", date_from="2026-11-14", time_from="00:00"),   # fechas equivocadas: se corrigen
               LLMResponse("El sábado 7 hay 2 partidos por la tarde.", [])])
    res = converse(seeded, llm, "Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde")
    body = tool_json(llm, 2)[0]
    assert body["count"] == 2 and sorted(m["time"] for m in body["items"]) == ["16:00", "19:30"]
    assert res.run is not None and res.run.tool_calls[0].repaired is not None
    assert res.run.tool_calls[0].arguments == {"date_from": "2026-11-07", "date_to": "2026-11-07", "time_from": "15:00", "time_to": "23:59"}
    assert res.guardrail is None and "2 partidos" in res.answer


def test_q4_club_overview_with_results(seeded: dict[str, Any]) -> None:
    llm = Rec([call("club_overview", name="alamo", results=True), LLMResponse("El Álamo A ganó 4-1; el B empató 2-2.", [])])
    res = converse(seeded, llm, "Como han sido los resultados de todos los equipos del álamo?")
    body = tool_json(llm)[0]
    assert body["count"] == 2 and {i["team"]["name"]: i["summary"]["played"] for i in body["items"]} == {"C.D. ALAMO A": 1, "C.D. ALAMO B": 1}
    assert "álamo" in last_user(llm) and "Alcance amplio" in last_user(llm) and res.guardrail is None


def test_q5_top_scorer_of_all_senior_leagues(seeded: dict[str, Any]) -> None:
    llm = Rec([call("scorers_ranking", category="senior"), LLMResponse("PRIMERO, GOLEADOR con 9 goles.", [])])
    res = converse(seeded, llm, "Cuál es el máximo goleador de todas las ligas senior?")
    body = tool_json(llm)[0]
    assert body["items"][0]["player"]["name"] == "PRIMERO, GOLEADOR" and body["items"][0]["goals"] == 9
    assert any("compara grupos distintos" in n for n in body["notes"]) and res.tool_calls == 1


def test_q6_premise_after_the_first_question(seeded: dict[str, Any]) -> None:
    first = Rec([call("find_player", name="Pablo quintanar"), LLMResponse("Pablo Quintanar juega en MOSTOLES FS.", [])])
    r1 = converse(seeded, first, "Sabes en qué equipo juega Pablo quintanar ?")
    second = Rec([call("find_player", name="Pablo quintanar", team="san Felipe Neri"), LLMResponse("No consta: figura en MOSTOLES FS, no en San Felipe Neri.", [])])
    r2 = converse(seeded, second, "Creo que juega en el san Felipe Neri", [Turn("Sabes en qué equipo juega Pablo quintanar ?", r1.answer)])
    note = last_user(second)
    assert "El usuario afirma" in note and "Creo que juega en el san Felipe Neri" in note and "no lo confirma" in note
    body = tool_json(second)[0]
    assert body["items"] == [] and any("ninguno en un equipo que coincida" in n and "MOSTOLES FS" in n for n in body["notes"]) and "no consta" in r2.answer.lower()


def test_club_homonyms_are_flagged_so_the_agent_asks(seeded: dict[str, Any]) -> None:
    llm = Rec([call("club_overview", name="pinto"), LLMResponse("¿Te refieres a «Pinto» o a «Atlético Pinto»?", [])])
    res = converse(seeded, llm, "Resultados de todos los equipos del pinto")
    body = tool_json(llm)[0]
    assert body["count"] == 2 and all(i["ambiguous"] for i in body["items"]) and res.answer.startswith("¿Te refieres") and res.guardrail is None


def test_invalid_arguments_and_unknown_player_do_not_break(seeded: dict[str, Any]) -> None:
    reg = registry(seeded)
    is_error, text = asyncio.run(reg.call("find_player", {"name": "x"}))
    assert is_error and text.startswith("error_validacion")
    is_error, text = asyncio.run(reg.call("matches_by_date", {"date_from": "2026-11-09", "time_from": "20:00", "time_to": "10:00"}))
    assert is_error and text.startswith("error_validacion")
    ok, body = asyncio.run(reg.call("find_player", {"name": "Zzyzx Qwerty"}))
    assert not ok and json.loads(body)["items"] == [] and utc(2026, 1, 1).year == 2026


def test_all_matches_of_a_saturday_with_48_games_uses_a_bigger_limit(seeded: dict[str, Any]) -> None:
    reg = registry(seeded)
    default = json.loads(asyncio.run(reg.call("matches_by_date", {"date_from": "2026-11-14"}))[1])
    assert default["count"] == 48 and default["shown"] == 20  # por defecto 20: el LLM ve que faltan
    llm = Rec([call("matches_by_date", date_from="2026-11-14", limit=60), LLMResponse("Hay 48 partidos el sábado.", [])])
    agent = Agent(llm, FakeMcp(), 8, router_shadow=False, arch="v2", v2_registry=reg, max_rows=100)
    agent._clock = lambda: NOW
    res = asyncio.run(agent.run("Dime todos los partidos del sábado 14 de noviembre"))
    body = tool_json(llm)[0]
    assert body["count"] == 48 and body["shown"] == 48 and "truncated" not in body and "48" in res.answer
    for tool, args in (("find_official", {"name": "ramos", "limit": 1}), ("officials_ranking", {"limit": 2}), ("club_overview", {"name": "alamo", "limit": 1}),
                       ("teams_ranking", {"metric": "goals_for", "limit": 1}), ("find_player", {"name": "juan garcia lopez", "limit": 1})):
        out = json.loads(asyncio.run(reg.call(tool, args))[1])
        assert out["shown"] <= args["limit"] and len(out["items"]) <= args["limit"], tool


# --- respaldo determinista: el LLM simulado SIEMPRE pide aclaración ----------------------------------------------------------------------
def _always_clarifies() -> Rec:
    return Rec([LLMResponse("¿De qué competición o grupo quieres consultarlo?", [])] * 6)


def test_fallback_saturday_afternoon_total_with_seeded_data(seeded: dict[str, Any]) -> None:
    q = "Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde"
    llm = _always_clarifies()
    res = converse(seeded, llm, q)
    assert res.answer.splitlines()[0] == "2 partidos el sábado 7 de noviembre de 2026 entre las 15:00 y las 23:59:"
    assert "16:00" in res.answer and "19:30" in res.answer and "11:00" not in res.answer and res.tool_calls == 1
    assert res.run is not None and res.run.context["v2"]["answer_source"] == "deterministic_fallback"


def test_fallback_top_scorer_senior_with_seeded_data(seeded: dict[str, Any]) -> None:
    res = converse(seeded, _always_clarifies(), "Cuál es el máximo goleador de todas las ligas senior?")
    assert "PRIMERO, GOLEADOR" in res.answer and "9 goles" in res.answer and "compara grupos distintos" in res.answer
    assert res.run is not None and res.run.context["v2"]["fallback"]["tool"] == "scorers_ranking"
