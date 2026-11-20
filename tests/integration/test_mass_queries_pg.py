"""Consultas masivas a través del recorrido real (agente → MCP → API → PostgreSQL, sin LLM): tokens de herramienta que llegan al modelo,
paginación y totales exactos. Imprime la medición con `-s` (referencia antes/después en docs/phase-mass-queries.md)."""

import json
from typing import Any

from tests.integration.test_agent_v2_pg import (  # noqa: F401 - fixtures
    Rec,
    call,
    converse,
    seeded,
    world,
)

from futsal.agent.llm import LLMResponse
from futsal.agent.tokens import est_tokens

MASS = [  # (etiqueta, pregunta, herramienta, argumentos con los valores por defecto actuales: primera página de 10)
    ("día con 48 partidos", "¿Qué partidos hay el sábado 14 de noviembre?", "matches_by_date", {"date_from": "2026-11-14"}),
    ("fin de semana", "¿Qué partidos hay este fin de semana?", "matches_by_date", {"date_from": "2026-11-14", "date_to": "2026-11-15"}),
    ("goleadores de todas las ligas", "Dime el ranking de goleadores de todas las ligas", "scorers_ranking", {}),
    ("equipos con más goles", "¿Qué equipos llevan más goles esta temporada?", "teams_ranking", {"metric": "goals_for"}),
    ("partidos de un árbitro", "Dime todos los partidos arbitrados por Antonio Ramos Perez", "find_official",
     {"name": "Antonio Ramos Perez", "include_matches": True}),
    ("resultados de un club", "Muéstrame los resultados del Alamo", "club_overview", {"name": "alamo", "results": True}),
]
# Referencia ANTES de esta fase (mismo mundo sembrado; argumentos que pedía el modelo: limit=100 en partidos, 20 en el resto; filas completas).
BEFORE_TOKENS = {"día con 48 partidos": 4869, "fin de semana": 4869, "goleadores de todas las ligas": 734, "equipos con más goles": 1788,
                 "partidos de un árbitro": 439, "resultados de un club": 443}


def measure(w: dict[str, Any], question: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    llm = Rec([call(tool, **args), LLMResponse("ok", [])])
    res = converse(w, llm, question)
    sent = [r.content for m in llm.seen[1] if m.role == "tool" for r in m.tool_results]
    body = json.loads(sent[0]) if sent else {}
    return {"chars": sum(map(len, sent)), "tokens": est_tokens(sum(map(len, sent))), "rows": len(body.get("items") or []),
            "count": body.get("count"), "llm_calls": len(llm.seen), "tool_calls": res.tool_calls}


def test_mass_query_tool_payloads(seeded: dict[str, Any]) -> None:  # noqa: F811
    print("\nconsulta | filas enviadas | total | caracteres | tokens est. | antes")
    for label, q, tool, args in MASS:
        m = measure(seeded, q, tool, args)
        print(f"{label} | {m['rows']} | {m['count']} | {m['chars']} | {m['tokens']} | {BEFORE_TOKENS[label]}")
        assert m["tool_calls"] == 1 and m["tokens"] <= BEFORE_TOKENS[label]  # nunca más tokens de herramienta que antes
        assert m["rows"] <= 10 or tool == "club_overview"


def test_pages_cover_every_row_once_in_a_stable_order(seeded: dict[str, Any]) -> None:  # noqa: F811
    """48 partidos del sábado en páginas de 10: sin duplicados ni huecos, mismo orden que una sola página grande y total exacto."""
    from tests.integration.test_agent_v2_pg import v2_call

    def page(offset: int, limit: int = 10) -> dict[str, Any]:
        return json.loads(v2_call(seeded, "matches_by_date", {"date_from": "2026-11-14", "limit": limit, "offset": offset})[1])

    ids: list[int] = []
    offset: int | None = 0
    while offset is not None:
        body = page(offset)
        assert body["count"] == 48 and body["offset"] == offset
        ids += [it["id"] for it in body["items"]]
        offset = body["next_offset"]
    assert len(ids) == 48 and len(set(ids)) == 48 and ids == [it["id"] for it in page(0, 100)["items"]]
    past = page(60)
    assert past["items"] == [] and past["count"] == 48 and past["next_offset"] is None  # total exacto también pasada la última página
    first = page(0)["items"][0]
    assert set(first) <= {"id", "date", "time", "comp", "home", "away", "score", "status", "venue"}  # fila compacta: sin actas ni refs


def test_official_matches_are_paged_in_the_database(seeded: dict[str, Any]) -> None:  # noqa: F811
    from tests.integration.test_agent_v2_pg import v2_call

    body = json.loads(v2_call(seeded, "find_official", {"name": "Antonio Ramos Perez", "include_matches": True, "limit": 1})[1])
    assert body["official"] == "RAMOS PEREZ, ANTONIO" and body["count"] >= 1 and len(body["items"]) == 1 and body["offset"] == 0
    assert {"date", "home", "away", "role"} <= set(body["items"][0])
    missing = json.loads(v2_call(seeded, "find_official", {"name": "Antonio Ramos Pereira", "include_matches": True})[1])
    assert missing["items"] == [] and missing["suggestions"] and missing["people"] == []  # no figura: sugerencias, nunca sus partidos


def chat(w: dict[str, Any], steps: list[tuple[str, Rec]]) -> list[Any]:
    """Una conversación v2 con estado (como el servicio web): cada pregunta recibe el historial y el estado de la anterior."""
    from tests.integration.test_agent_v2_pg import NOW, HybridMcp, with_mcp

    from futsal.agent.agent import Agent
    from futsal.agent.memory import Turn

    async def run(mcp: HybridMcp) -> list[Any]:
        out, history, state = [], [], None
        for question, llm in steps:
            agent = Agent(llm, mcp, 8, trace_results=True)  # como el servicio web
            agent._clock = lambda: NOW
            res = await agent.run(question, list(history), state=state, sport="futsal")
            out.append(res)
            history.append(Turn(question, res.answer))
            state = res.state
        return out
    return with_mcp(w, run)


def test_conversation_pages_filters_and_changes_day_without_extra_llm_calls(seeded: dict[str, Any]) -> None:  # noqa: F811
    first = Rec([call("matches_by_date", date_from="2026-11-14"), LLMResponse("Hay 48 partidos el sábado 14; mostrando 1–10 de 48. ¿Quieres los siguientes 10?", [])])
    nothing = Rec([])  # si el agente llamara al LLM en un seguimiento, fallaría (lista vacía)
    res = chat(seeded, [("¿Qué partidos hay el sábado 14 de noviembre?", first), ("Dime los siguientes", nothing),
                        ("Muéstrame 15 más", Rec([])), ("¿Y el resto?", Rec([])), ("Los siguientes", Rec([])),
                        ("¿y el sábado 7 de noviembre?", Rec([])), ("solo a partir de las 18", Rec([]))])
    tool_body = json.loads(next(r.content for m in first.seen[1] if m.role == "tool" for r in m.tool_results))
    assert tool_body["count"] == 48 and len(tool_body["items"]) == 10 and "Mostrando 1–10 de 48" in tool_body["page_note"]
    assert res[0].state["listing"]["next_offset"] == 10 and "items" not in json.dumps(res[0].state)  # el estado guarda la consulta, no filas
    p2, p3, p4, p5, sat7, late = (r.answer for r in res[1:])
    assert "mostrando 11–20 de 48" in p2 and p2.count("\n- ") == 10 and "siguientes 10" in p2
    assert "mostrando 21–35 de 48" in p3
    assert "mostrando 36–48 de 48" in p4 and "No hay más resultados" in p4
    assert "ya te he mostrado los 48 resultados" in p5
    assert "Partidos del sábado 7 de noviembre" in sat7 and "3 resultados" in sat7  # otro día: primera página de esa consulta
    assert "entre las 18:00 y las 23:59" in late and "19:30" in late and "16:00" not in late
    for r in res[1:]:
        assert r.run.context["pagination"]["tool"] == "matches_by_date" and r.tool_calls == 1 and not r.run.llm_calls


def test_a_new_question_after_a_listing_is_not_hijacked(seeded: dict[str, Any]) -> None:  # noqa: F811
    first = Rec([call("matches_by_date", date_from="2026-11-14"), LLMResponse("Mostrando 1–10 de 48.", [])])
    other = Rec([call("scorers_ranking"), LLMResponse("El máximo goleador es PRIMERO, GOLEADOR.", [])])
    res = chat(seeded, [("¿Qué partidos hay el sábado 14 de noviembre?", first), ("¿Quién es el máximo goleador de todas las ligas?", other)])
    assert "pagination" not in (res[1].run.context or {}) and other.seen  # pregunta nueva: flujo normal con el LLM
    assert res[1].state["listing"]["tool"] == "scorers_ranking"  # y su listado pasa a ser el último


def test_pages_come_with_the_existing_visuals_and_a_see_more_chip(seeded: dict[str, Any]) -> None:  # noqa: F811
    from futsal.agent import presentation as pres

    first = Rec([call("matches_by_date", date_from="2026-11-14"), LLMResponse("Mostrando 1–10 de 48.", [])])
    res = chat(seeded, [("¿Qué partidos hay el sábado 14 de noviembre?", first), ("Dime los siguientes", Rec([]))])
    for r, label in ((res[0], "Mostrando 1–10 de 48"), (res[1], "Mostrando 11–20 de 48")):
        visuals = pres.build_visuals("q", r.run, [], r.names)
        matches = [v for v in visuals if v["type"] == "matches"]
        assert len(matches) == 1 and matches[0]["subtitle"] == label and matches[0]["total"] == 48 and len(matches[0]["rows"]) == 10
        assert matches[0]["rows"][0]["date"].startswith("2026-11-14T") and matches[0]["rows"][0]["detail"]
        assert pres.build_suggestions("q", r.answer, r.run, {}, [], [], visuals) == ["Muéstrame los siguientes 10"]
    scorers = Rec([call("scorers_ranking", limit=3), LLMResponse("Mostrando 1–3 de 8.", [])])
    out = chat(seeded, [("Dime los goleadores de todas las ligas", scorers)])[0]
    bars = [v for v in pres.build_visuals("q", out.run, [], out.names) if v["type"] == "bars"]
    assert bars and bars[0]["subtitle"] == "Mostrando 1–3 de 8" and bars[0]["rows"][0]["value"] >= bars[0]["rows"][-1]["value"]
