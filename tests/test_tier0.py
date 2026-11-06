"""Tier 0 (AGENT_TIER0): lista blanca cerrada de plantillas, frescura, ámbito y trazabilidad. Agente REAL con MCP simulado; sin red, cuota ni PostgreSQL.

El dataset adversarial (`tests/fixtures/tier0/adversarial.json`) está redactado a mano a partir de los fallos de la auditoría: NO es evidencia de rendimiento real."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from futsal.agent.agent import Agent, resolve_tier0
from futsal.agent.memory import Turn
from futsal.agent.presentation import compute_focus
from futsal.agent.tier0 import candidate, compose, match, max_age_hours
from test_prefetch import FakeMcp, Script, call, final

ADV = json.loads((Path(__file__).parent / "fixtures" / "tier0" / "adversarial.json").read_text(encoding="utf-8"))["cases"]
Q = "En TERCERA DIVISIÓN FS (Grupo 3), ¿en qué posición está el C.D. DOSA y cuántos puntos tiene?"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def standings(captured: datetime | None = NOW - timedelta(hours=2), **over: Any) -> dict[str, Any]:
    rows = [{"position": 1, "team_id": 12, "team": "PARQUE NORTE F.S. - RODILLITO 'B'", "played": 10, "won": 8, "drawn": 1, "lost": 1, "goals_for": 40, "goals_against": 12,
             "goal_difference": 28, "points": 25},
            {"position": 2, "team_id": 11, "team": "C.D. DOSA", "played": 10, "won": 7, "drawn": 2, "lost": 1, "goals_for": 35, "goals_against": 15, "goal_difference": 20, "points": 23}]
    source: dict[str, Any] = {"name": "RFFM · clasificaciones", "round": 10, "current_round": 10}
    if captured is not None:
        source["captured_at"] = captured.isoformat()
    base = {"group_id": 6, "group": "Grupo 3", "competition": "TERCERA DIVISIÓN FS", "status": "ok", "source": source,
            "coverage": {"teams": 2, "expected_teams": 2, "complete": True}, "standings": rows}
    return {**base, **over}


class StandingsMcp(FakeMcp):
    def __init__(self, data: dict[str, Any] | None = None, error: bool = False) -> None:
        super().__init__()
        self.data, self.error = data, error

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name == "get_standings":
            self.calls.append((name, dict(arguments)))
            return (True, "error_dependencia: API no disponible") if self.error else (False, json.dumps(self.data if self.data is not None else standings()))
        return await super().call_tool(name, arguments)


def run(question: str = Q, mcp: FakeMcp | None = None, steps: list[Any] | None = None, tier0: str | None = "on", history: list[Turn] | None = None,
        clock: datetime = NOW) -> tuple[Any, Script, FakeMcp]:
    llm, fake = Script(steps if steps is not None else []), mcp or StandingsMcp()
    agent = Agent(llm, fake, 5, tier0=tier0)
    agent._clock = lambda: clock
    res = asyncio.run(agent.run(question, history or []))
    return res, llm, fake


LLM = lambda: [call("get_standings", group_id=6), final("respuesta del LLM")] * 4


def test_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TIER0", raising=False)
    assert resolve_tier0() is False and resolve_tier0("on") is True and resolve_tier0("quizás") is False


# --- lista blanca: dataset adversarial de extremo a extremo --------------------------------------------------------------------------
@pytest.mark.parametrize("case", ADV, ids=[c["id"] for c in ADV])
def test_adversarial_dataset_end_to_end(case: dict[str, Any]) -> None:
    history = [Turn(h, "respuesta anterior") for h in case["history"]]
    res, llm, _ = run(case["question"], steps=LLM(), history=history)
    answered = bool(res.run.tier0 and res.run.tier0["answered"])
    if case["expect"] == "answer":
        assert answered and llm.seen == [], (case["question"], res.run.tier0)
    else:
        assert not answered and llm.seen, (case["question"], res.run.tier0)  # cero activaciones incorrectas: sigue el flujo normal con LLM


def test_dataset_covers_every_audited_risk_and_has_enough_cases() -> None:
    cats = {c["category"] for c in ADV}
    assert {"objetivos_futuros", "ventaja_diferencia", "historico", "prediccion_escenario", "local_visitante", "sancion_fairplay_no_disponible", "comparacion",
            "seguimiento", "ambito_ambiguo_o_distinto", "forma_no_prevista", "admitida"} <= cats
    assert sum(c["expect"] == "abstain" for c in ADV) >= 100 and sum(c["expect"] == "answer" for c in ADV) >= 20


def test_a_single_unlisted_word_is_enough_to_abstain() -> None:
    base = "en que posicion esta el dosa"
    assert match(base, "dosa", []) is not None
    for extra in ("hace un mes", "como local", "si gana", "de sancion", "fuera de casa", "sin contar el ultimo partido", "y quien esta por delante"):
        assert match(f"{base} {extra}", "dosa", []) is None


def test_team_must_appear_exactly_once_and_scope_is_masked() -> None:
    assert match("en que posicion esta el dosa y el dosa", "dosa", []) is None
    assert match("en que posicion esta el diagonal", "dosa", []) is None
    assert match("en tercera division fs grupo 3 en que posicion esta el dosa", "dosa", ["tercera division fs", "grupo 3"]) is not None
    assert match("en la liga marciana en que posicion esta el dosa", "dosa", ["tercera division fs"]) is None


def test_candidate_is_only_a_cheap_prefilter_and_never_a_decision() -> None:
    assert candidate("¿En qué posición está el DOSA?") and not candidate("¿Cuándo juega el DOSA?")


# --- respuesta correcta y trazabilidad ----------------------------------------------------------------------------------------------
def test_answers_position_and_points_without_any_llm_call_and_keeps_provenance() -> None:
    res, llm, mcp = run()
    assert llm.seen == [] and res.run.llm_calls == [] and res.tool_calls == 0
    assert "posición 2 de 2 equipos" in res.answer and "23 puntos" in res.answer and "jornada 10" in res.answer and "2026-11-06" in res.answer and "RFFM" in res.answer
    info = res.run.tier0
    assert info["answered"] and info["template"] == "position_points" and info["provenance"]["tool"] == "get_standings" and info["provenance"]["round"] == 10
    assert info["provenance"]["captured"].startswith("2026-11-06") and info["provenance"]["age_hours"] == 2.0
    assert [t.via for t in res.run.tool_calls] == ["tier0", "tier0"] and ("get_standings", {"group_id": 6}) in mcp.calls
    assert compute_focus(res.run, res.names, res.groups_used, res.official, Q).get("team") == "C.D. DOSA"


def test_off_means_the_llm_flow_exactly_as_before() -> None:
    res, llm, _ = run(tier0="off", steps=[call("get_standings", group_id=6), final("ok")])
    assert res.run.tier0 is None and len(llm.seen) == 2 and res.answer == "ok" and res.run.exec_tier == "tier2"


# --- I3: clasificación y tokens ----------------------------------------------------------------------------------------------------
def test_a_tier0_run_is_tier0_with_zero_measured_tokens_not_unreported() -> None:
    res, _, _ = run()
    t = res.run.totals()
    assert t["exec_tier"] == "tier0" and t["token_source"] == "measured_zero" and t["llm_calls"] == 0
    assert t["input_tokens"] == t["output_tokens"] == 0 and t["tokens_estimated"] is False and t["mcp_calls"]["tier0"] == 2


def test_an_llm_run_keeps_its_tier_and_source_and_a_failed_tier0_does_not_change_them() -> None:
    res, _, _ = run(steps=LLM(), question="¿Cuándo juega el C.D. DOSA?")
    t = res.run.totals()
    assert t["exec_tier"] == "tier2" and t["token_source"] == "unreported" and t["llm_calls"] >= 1  # el Script no informa tokens: se queda «no informado»


# --- datos: frescura real, aviso, cobertura, sanciones ------------------------------------------------------------------------------
@pytest.mark.parametrize(("name", "data"), [
    ("captura obsoleta (>72 h)", standings(NOW - timedelta(hours=73))),
    ("sin fecha de captura", standings(None)),
    ("fecha sin zona horaria", standings(None, source={"round": 10, "current_round": 10, "captured_at": "2026-11-06T10:00:00"})),
    ("fecha ilegible", standings(None, source={"round": 10, "current_round": 10, "captured_at": "ayer"})),
    ("captura en el futuro", standings(NOW + timedelta(hours=5))),
    ("aviso en los datos", standings(warning="Última jornada ingerida: 9")),
    ("jornada atrasada", standings(source={"round": 9, "current_round": 10, "captured_at": (NOW - timedelta(hours=1)).isoformat()})),
    ("datos incompletos", standings(status="incomplete")),
    ("cobertura parcial", standings(coverage={"complete": False})),
    ("equipo ausente", standings(standings=[])),
])
def test_any_doubt_in_the_data_falls_back_to_the_llm_with_the_state_untouched(name: str, data: dict[str, Any]) -> None:
    res, llm, _ = run(mcp=StandingsMcp(data), steps=LLM())
    assert llm.seen and not res.run.tier0["answered"] and res.tool_calls >= 1, name
    assert res.run.tier0["reason"] == "data_not_sufficient", name


def test_a_tool_error_and_penalty_points_go_to_the_llm() -> None:
    res, llm, _ = run(mcp=StandingsMcp(error=True), steps=LLM())
    assert llm.seen and not res.run.tier0["answered"]
    data = standings()
    data["standings"][1]["penalty_points"] = 1
    res, llm, _ = run(mcp=StandingsMcp(data), steps=LLM())
    assert llm.seen and not res.run.tier0["answered"]


def test_the_freshness_threshold_is_configurable_and_documented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TIER0_MAX_AGE_HOURS", raising=False)
    assert max_age_hours() == 72.0
    monkeypatch.setenv("AGENT_TIER0_MAX_AGE_HOURS", "6")
    assert max_age_hours() == 6.0 and compose(standings(NOW - timedelta(hours=7)), 11, now=NOW) is None and compose(standings(NOW - timedelta(hours=5)), 11, now=NOW) is not None
    for bad in ("0", "-3", "mucho"):
        monkeypatch.setenv("AGENT_TIER0_MAX_AGE_HOURS", bad)
        assert max_age_hours() == 72.0


def test_compose_requires_a_unique_team_row() -> None:
    assert compose(standings(), 11, now=NOW) is not None and compose(standings(), 99, now=NOW) is None
    dup = standings()
    dup["standings"].append(dict(dup["standings"][1]))
    assert compose(dup, 11, now=NOW) is None


# --- competición pedida ------------------------------------------------------------------------------------------------------------
def test_data_from_another_competition_is_never_used_to_answer() -> None:
    other = standings(competition="COPA SENIOR I - fase final- FS", group="Eliminatorias")
    res, llm, _ = run("En TERCERA DIVISIÓN FS (Grupo 3), ¿en qué posición está el C.D. DOSA?", mcp=StandingsMcp(other), steps=LLM())
    assert llm.seen and not res.run.tier0["answered"] and res.run.tier0["reason"] == "scope_mismatch"


def test_the_answer_names_the_competition_and_group_of_the_data() -> None:
    res, _, _ = run("¿En qué posición está el PARQUE NORTE F.S. - RODILLITO 'B'?")
    assert res.run.tier0["answered"] and "TERCERA DIVISIÓN FS (Grupo 3)" in res.answer and "PARQUE NORTE" in res.answer
