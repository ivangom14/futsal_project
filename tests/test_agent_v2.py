"""AGENT_ARCH=v2: reglas deterministas, registro en proceso y legacy intacto. LLM guionizado, registro simulado (sin red ni BD)."""

import asyncio
import json
import threading
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent, resolve_arch
from futsal.agent.criteria import figures_without_table
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import Turn
from futsal.agent.messages import NOT_IN_REPORT
from futsal.agent.prompts import MODULE_V2
from futsal.agent.tokens import est_tokens
from futsal.agent.understanding_eval import FakeMcp
from futsal.agent.v2.registry import V2Registry
from futsal.agent.v2.rules import BROAD_ACT_MESSAGE, SUGGESTION_NOTE
from futsal.mcp_server.v2 import create_server
from futsal.repositories import v2_queries as q

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


class Stub(V2Registry):
    """Registro con respuestas guionizadas por herramienta; guarda (nombre, argumentos) de cada llamada."""

    def __init__(self, replies: dict[str, dict[str, Any]] | None = None) -> None:
        super().__init__(lambda: None)  # type: ignore[arg-type, return-value]
        self.replies, self.calls = replies or {}, []

    def _run_sync(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, dict(args)))
        return json.dumps(self.replies.get(name, EMPTY), ensure_ascii=False)


def call(tool: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall("c", tool, args)])


def ask(llm: Rec, question: str, history: list[Turn] | None = None, reg: V2Registry | None = None, arch: str | None = "v2", **kw: Any) -> Any:
    agent = Agent(llm, FakeMcp(), 8, router_shadow=False, arch=arch, v2_registry=reg or Stub(), **kw)
    agent._clock = lambda: NOW
    return asyncio.run(agent.run(question, history or []))


def last_user(llm: Rec, turn: int = 0) -> str:
    return next(m.text for m in reversed(llm.seen[turn]) if m.role == "user")


# --- 1. AGENT_ARCH y legacy idéntico ----------------------------------------------------------------------------------------------
def test_arch_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ARCH", raising=False)
    assert resolve_arch() == "legacy" and resolve_arch("V2") == "v2" and resolve_arch("v3") == "legacy" and resolve_arch("") == "legacy"
    monkeypatch.setenv("AGENT_ARCH", "v2")
    assert resolve_arch() == "v2"
    monkeypatch.setenv("AGENT_ARCH", "otra")
    assert resolve_arch() == "legacy"


def test_legacy_is_byte_identical_default_explicit_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ARCH", raising=False)
    runs = []
    for arch in (None, "legacy", "bogus"):
        llm = Rec([call("get_standings", group_id=1101), LLMResponse("Líder X.", [])])
        agent = Agent(llm, FakeMcp(), 8, router_shadow=False, arch=arch)
        res = asyncio.run(agent.run("Clasificación de la preferente juvenil todos los equipos creo que sí"))
        runs.append((llm.systems, llm.tools, [[(m.role, m.text) for m in ms] for ms in llm.seen], res.answer, res.run.arch if res.run else None,
                     res.run.context if res.run else None))
        assert not {t["name"] for t in llm.tools[0]} & V2_TOOLS and "Herramientas v2" not in llm.systems[0]
        assert llm.systems[0].startswith(GOLDEN["system_prompt"]) and "[Nota de la aplicación" in last_user(llm) and "Alcance amplio" not in last_user(llm)
        assert [t["name"] for t in llm.tools[0]] == ["list_competitions", "find_teams", "list_teams", "list_rounds", "list_matches", "get_standings",
                                                      "get_scorer_table", "search_rules"]
    assert runs[0] == runs[1] == runs[2] and runs[0][4] == "legacy" and runs[0][5] is None


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
    assert reg.calls and reg.calls[0][0] == "matches_by_date" and len(sent["items"]) == 40 and sent["truncated"]["total"] == 60 and sent["count"] == 60
    assert res.tool_calls == 1 and res.answer == "ok"


def test_v1_id_validation_is_kept_for_v1_tools() -> None:
    llm = Rec([call("list_matches", group_id=1101, team_id=999999), LLMResponse("ok", [])])
    ask(llm, "Último partido del Dosa")
    assert "no procede de ninguna herramienta" in next(m for m in llm.seen[1] if m.role == "tool").tool_results[0].content


def test_two_v2_tools_in_one_answer_run_in_parallel() -> None:
    barrier = threading.Barrier(2, timeout=3)

    class Parallel(Stub):
        def _run_sync(self, name: str, args: dict[str, Any]) -> str:
            barrier.wait()  # solo pasa si las dos herramientas están en curso a la vez
            return super()._run_sync(name, args)

    reg = Parallel()
    llm = Rec([LLMResponse("", [ToolCall("a", "find_player", {"name": "Pablo Quintanar"}), ToolCall("b", "find_official", {"name": "Ramos"})]),
               LLMResponse("ok", [])])
    res = ask(llm, "¿Dónde juega Pablo Quintanar y quién es el árbitro Ramos?", reg=reg)
    results = next(m for m in llm.seen[1] if m.role == "tool").tool_results
    assert [r.is_error for r in results] == [False, False] and {c[0] for c in reg.calls} == {"find_player", "find_official"} and res.tool_calls == 2


def test_invalid_v2_arguments_return_a_validation_error_without_crashing() -> None:
    llm = Rec([call("teams_ranking", metric="nada"), call("matches_by_date", date_from="mañana"), LLMResponse("ok", [])])

    class Real(V2Registry):
        pass

    reg = Real(lambda: None)  # type: ignore[arg-type, return-value]
    res = ask(llm, "Ranking de equipos", reg=reg)
    errors = [r.content for m in llm.seen[2] if m.role == "tool" for r in m.tool_results]
    assert len(errors) == 2 and all(e.startswith("error_validacion") for e in errors) and res.answer == "ok"


# --- 3. fechas ---------------------------------------------------------------------------------------------------------------------
def test_date_note_and_deterministic_repair_of_matches_by_date() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-11-14", date_to="2026-11-14", time_from="00:00"), LLMResponse("ok", [])])
    res = ask(llm, "Dime los partidos del sábado 7 de noviembre por la tarde", reg=reg)
    note = last_user(llm)
    assert "2026-11-07" in note and "15:00" in note and "23:59" in note and "Nota de la aplicación" in note
    assert reg.calls == [("matches_by_date", {"date_from": "2026-11-07", "date_to": "2026-11-07", "time_from": "15:00", "time_to": "23:59"})]
    rec = res.run.tool_calls[0] if res.run else None
    assert rec is not None and rec.repaired is not None and rec.repaired["source"] == "dates"
    assert rec.original_arguments == {"date_from": "2026-11-14", "date_to": "2026-11-14", "time_from": "00:00"}


def test_matching_arguments_are_not_repaired() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-11-07", time_from="15:00", time_to="23:59"), LLMResponse("ok", [])])
    res = ask(llm, "Dime los partidos del sábado 7 de noviembre por la tarde", reg=reg)
    assert res.run is not None and res.run.tool_calls[0].repaired is None and reg.calls[0][1]["date_from"] == "2026-11-07"


def test_no_date_hint_leaves_arguments_alone() -> None:
    reg = Stub()
    llm = Rec([call("matches_by_date", date_from="2026-12-01"), LLMResponse("ok", [])])
    ask(llm, "Partidos del primero de diciembre", reg=reg)
    assert reg.calls == [("matches_by_date", {"date_from": "2026-12-01"})]


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
    assert last_user(llm, 1) == BROAD_ACT_MESSAGE and res.tool_calls == 1 and res.answer == "Asumo todas las ligas: X."
    legacy = Rec([LLMResponse(CLARIFY, [])])
    kept = ask(legacy, "pues no sé", history, arch="legacy")
    assert kept.answer == CLARIFY and kept.guardrail == "aclaracion_ambito"  # legacy: sigue como siempre


def test_real_entity_ambiguity_is_still_asked_even_with_broad_scope() -> None:
    cands = {"items": [{"base_name": "pinto", "ambiguous": True, "teams": []}, {"base_name": "atletico pinto", "ambiguous": True, "teams": []}],
             "count": 2, "shown": 2, "notes": ["varios clubes con nombre distinto"], "freshness": None}
    question = "¿A qué club te refieres: «Pinto» o «Atlético Pinto»?"
    llm = Rec([call("club_overview", name="pinto", results=True), LLMResponse(question, [])])
    res = ask(llm, "Resultados de todos los equipos del Pinto", reg=Stub({"club_overview": cands}))
    assert res.answer == question and res.tool_calls == 1 and res.guardrail is None


def test_deterministic_context_turns_ask_into_act_only_with_v2_and_broad_scope() -> None:
    q = "¿Cómo va el DOSA en todas las competiciones?"
    base = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])])
    legacy = ask(base, q, [Turn("¿Cómo va la tercera división?", "…")], arch="legacy", context_mode="deterministic")
    assert legacy.run is not None and legacy.run.context["status"] == "ask"
    llm = Rec([call("club_overview", name="DOSA", results=True), LLMResponse("ok", [])])
    res = ask(llm, q, [Turn("¿Cómo va la tercera división?", "…")], context_mode="deterministic")
    assert res.run is not None and res.run.context["status"] == "act" and res.run.context["reason"] == "alcance amplio explícito"
    assert "Alcance amplio" in last_user(llm) and res.tool_calls == 1 and res.answer == "ok"


def test_deterministic_context_second_scope_question_assumes_and_declares() -> None:
    q = "¿Cómo va el DOSA?"
    first = ask(Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])]), q, [Turn("¿Cómo va la tercera división?", "…")], context_mode="deterministic")
    assert first.run is not None and first.run.context["status"] == "ask" and first.state is not None
    llm = Rec([call("club_overview", name="DOSA", results=True), LLMResponse("Va bien.", [])])
    agent = Agent(llm, FakeMcp(), 8, router_shadow=False, arch="v2", v2_registry=Stub(), context_mode="deterministic")
    hist = [Turn("¿Cómo va la tercera división?", "…"), Turn(q, first.answer or "¿A cuál de estas competiciones te refieres?")]
    res = asyncio.run(agent.run("el dosa, no sé", hist, state=first.state))
    assert res.run is not None and res.run.context["status"] in ("assume", "act")


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
def test_mcp_v2_server_lists_the_registry_and_runs_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {"items": [{"rank": 1, "team": "X"}], "count": 1, "shown": 1, "notes": [], "freshness": None}
    monkeypatch.setattr(q, "build_scope", lambda *a, **k: None)
    monkeypatch.setattr(q, "teams_ranking", lambda db, scope, metric, order, min_played, limit: {**canned, "metric": metric})
    server = create_server(V2Registry(lambda: nullcontext()))  # type: ignore[arg-type, return-value]
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == V2_TOOLS
    assert all(t.description and t.inputSchema.get("properties") is not None for t in tools)
    out = asyncio.run(server.call_tool("teams_ranking", {"metric": "goals_for"}))
    payload = out[1] if isinstance(out, tuple) else out
    assert isinstance(payload, dict) and payload["items"][0]["rank"] == 1 and payload["metric"] == "goals_for"


def test_insistent_clarification_is_counted_in_the_trace() -> None:
    llm = Rec([LLMResponse(CLARIFY, [])] * 6)
    res = ask(llm, "Dime todos los partidos del sábado 14 de noviembre")
    assert res.run is not None and res.run.context["v2"]["clarification_blocked"] == 2 and res.run.context["v2"]["clarify_insisted"] >= 1


def test_every_list_tool_accepts_limit() -> None:
    for t in Stub().tool_defs():
        assert t["input_schema"]["properties"]["limit"]["maximum"] == 100, t["name"]


# --- 7. respaldo determinista ante una aclaración insistente -----------------------------------------------------------------------
REPEATED = [Turn("¿Quién es el máximo goleador del grupo?", CLARIFY)]  # aclaración ya hecha: se activan los empujones sin «todos»
DAY_Q = "Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde"


def _match(i: int) -> dict[str, Any]:
    return {"ref": f"match:{i}", "date": "2026-11-07", "time": f"{15 + i % 8:02d}:00", "home": {"ref": f"team:{i}", "name": f"LOCAL {i}"},
            "away": {"ref": f"team:{i + 100}", "name": f"VISITANTE {i}"}, "score": None, "competition": {"ref": "comp:1", "name": "TERCERA"},
            "group": {"ref": "group:1", "name": "Grupo 1"}}


def _fallback_run(question: str, replies: dict[str, dict[str, Any]], history: list[Turn] | None = None, arch: str | None = "v2") -> tuple[Any, Stub, Rec]:
    reg, llm = Stub(replies), Rec([LLMResponse(CLARIFY, [])] * 6)
    return ask(llm, question, history, reg=reg, arch=arch), reg, llm


def test_fallback_matches_by_date_composes_total_list_and_all_notes() -> None:
    body = {"items": [_match(i) for i in range(30)], "count": 48, "shown": 30, "notes": ["filtro horario: solo partidos con hora conocida", "otra nota"], "freshness": None}
    res, reg, llm = _fallback_run(DAY_Q, {"matches_by_date": body})
    assert len(llm.seen) == 3 and res.tool_calls == 1  # 2 empujones y la tercera vuelta insistente; sin más llamadas al LLM
    assert reg.calls == [("matches_by_date", {"date_from": "2026-11-07", "date_to": "2026-11-07", "time_from": "15:00", "time_to": "23:59", "limit": 100})]
    lines = res.answer.splitlines()
    assert lines[0] == "48 partidos el sábado 7 de noviembre de 2026 entre las 15:00 y las 23:59:"
    assert sum(x.startswith("- 2026-") for x in lines) == 20 and "- y 28 más" in lines
    assert all(n in res.answer for n in body["notes"]) and "LOCAL 0 - VISITANTE 0" in res.answer and CLARIFY not in res.answer
    v2 = res.run.context["v2"] if res.run else {}
    assert v2["fallback"] == {"plan": "matches_by_date", "tool": "matches_by_date", "args": reg.calls[0][1]} and v2["answer_source"] == "deterministic_fallback"
    assert figures_without_table(res.answer, [body], DAY_Q + " (franja interpretada 15:00-23:59)") == []  # ninguna cifra ajena a la herramienta, la pregunta y la franja interpretada


def test_fallback_top_scorer_of_all_senior_leagues() -> None:
    q = "Cuál es el máximo goleador de todas las ligas senior?"
    body = {"items": [{"rank": 1, "player": {"name": "GOLEADOR, A"}, "team": {"name": "EQ"}, "competition": {"name": "TERCERA"}, "group": {"name": "G1"}, "goals": 9, "matches": 7}],
            "count": 1, "shown": 1, "notes": ["compara grupos distintos"], "freshness": None}
    res, reg, _ = _fallback_run(q, {"scorers_ranking": body})
    assert reg.calls == [("scorers_ranking", {"category": "senior"})]
    assert "GOLEADOR, A" in res.answer and "9 goles" in res.answer and "compara grupos distintos" in res.answer
    assert res.run is not None and res.run.context["v2"]["fallback"]["plan"] == "scorers_ranking"
    assert figures_without_table(res.answer, [body], q) == []


def _club(name: str, ambiguous: bool) -> dict[str, Any]:
    return {"club": {"name": name, "ambiguous": ambiguous}, "team": {"name": f"{name} A"}, "competition": {"name": "C"}, "group": {"name": "G"},
            "summary": {"played": 3, "won": 2, "drawn": 1, "lost": 0, "goals_for": 9, "goals_against": 4}}


def test_fallback_club_unique_and_ambiguous() -> None:
    ok = {"items": [_club("C.D. ALAMO", False)], "count": 1, "shown": 1, "notes": ["agrupado por nombre"], "freshness": None}
    res, reg, _ = _fallback_run("Resultados de todos los equipos del álamo?", {"club_overview": ok})
    assert reg.calls == [("club_overview", {"name": "álamo", "results": True})]
    assert res.answer.startswith("1 equipo de C.D. ALAMO:") and "3 jugados, 2 ganados" in res.answer and "agrupado por nombre" in res.answer
    amb = {"items": [_club("PINTO", True), _club("ATLETICO PINTO", True)], "count": 2, "shown": 2, "notes": ["varios clubes"], "freshness": None}
    res2, _, _ = _fallback_run("Resultados de todos los equipos del pinto", {"club_overview": amb})
    assert res2.answer.startswith(CLARIFY) and "PINTO; ATLETICO PINTO" in res2.answer
    assert res2.run is not None and res2.run.context["v2"]["fallback_skipped"] == "ambiguous" and "fallback" not in res2.run.context["v2"]


def test_fallback_official_matches_and_not_found_with_suggestions() -> None:
    q = "Qué partidos ha pitado el árbitro Álvaro Bermejo?"
    listed = {"date": "2026-11-07", "time": "16:00", "home": "A", "away": "B", "score": {"home": 2, "away": 1}, "competition": {"name": "TERCERA"}}
    body = {"items": [{"name": "BERMEJO, ALVARO", "matches": 1, "matches_list": [listed]}], "count": 1, "shown": 1, "notes": ["cobertura"], "freshness": None}
    res, reg, _ = _fallback_run(q, {"find_official": body}, REPEATED)
    assert reg.calls == [("find_official", {"name": "Álvaro Bermejo", "include_matches": True})]
    assert "BERMEJO, ALVARO figura en 1 partido:" in res.answer and "A - B (2-1)" in res.answer and "cobertura" in res.answer
    none = {"items": [], "count": 0, "shown": 0, "notes": ["no figura; nombres parecidos: X"], "suggestions": [{"name": "BERMEJO, ALBERTO"}], "freshness": None}
    res2, _, _ = _fallback_run(q, {"find_official": none}, REPEATED)
    assert "no figura como árbitro" in res2.answer and "posibles" in res2.answer and "BERMEJO, ALBERTO" in res2.answer and "nombres parecidos: X" in res2.answer


def test_fallback_is_skipped_without_plan_or_with_empty_result_and_keeps_the_clarification() -> None:
    history = [Turn("¿Quién es el máximo goleador del grupo?", CLARIFY)]
    res, reg, _ = _fallback_run("¿y eso qué tal?", {}, history)
    assert res.answer == CLARIFY and reg.calls == [] and res.run is not None and res.run.context["v2"]["fallback_skipped"] == "sin_plan"
    res2, reg2, _ = _fallback_run(DAY_Q, {"matches_by_date": EMPTY})  # count=0
    assert res2.answer == CLARIFY and len(reg2.calls) == 1 and res2.run is not None
    assert res2.run.context["v2"]["fallback_skipped"] == "count_0" and "fallback" not in res2.run.context["v2"]
    res3, _, _ = _fallback_run("Dime todos los partidos del sábado 7 de noviembre y los máximos goleadores senior", {"matches_by_date": EMPTY})
    assert res3.answer == CLARIFY  # sin plan inequívoco: pista de fechas y otra pregunta a la vez no se resuelve


def test_fallback_legacy_is_unchanged() -> None:
    res, reg, _ = _fallback_run(DAY_Q, {"matches_by_date": {"items": [_match(1)], "count": 1, "shown": 1, "notes": [], "freshness": None}}, arch="legacy")
    assert reg.calls == [] and res.tool_calls == 0 and res.run is not None and res.run.context is None and res.answer != ""


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


def test_not_in_report_legacy_is_unchanged() -> None:
    llm = Rec([LLMResponse(OFF_TOPIC_MESSAGE, [])] * 3)
    res = ask(llm, "¿En qué minuto entraron los suplentes del partido X - Y?", arch="legacy")
    assert llm.seen and "no recoge" not in last_user(llm) and "no recoge" not in res.answer
