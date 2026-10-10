"""Pruebas unitarias del arnés de evaluación (sin red, sin PostgreSQL, sin LLM real)."""

from datetime import date

from futsal.agent.agent import OFF_TOPIC_MESSAGE
from futsal.agent.evaluate import (
    CaseResult,
    Fact,
    M,
    PolicyLLM,
    answer_ok,
    evidence_ok,
    format_table,
    has_date,
    has_score,
    key,
    presentation_ok,
    summarize_results,
)
from futsal.agent.llm import Message, ToolCall, ToolResult

DOSA = M(1, 1, 11, 12, "C.D. DOSA", "SAN FELIPE NERI - ELIPA DRAGONS 017", 6, 1, "finished",
         date(2026, 9, 26), "COL. X")
NEXT = M(2, 3, 13, 11, "TORREJON SALA FIVE PLAY 'B'", "C.D. DOSA", None, None, "scheduled",
         date(2026, 10, 10), "P.M. Y")


def test_key_picks_distinctive_token() -> None:
    assert key("C.D. DOSA") == "dosa"
    assert key("SAN FELIPE NERI - ELIPA DRAGONS 017") == "dragons"
    assert key("ESC. DEP. DE FUTBOL DE TORRES") == "torres"


def test_score_variants_and_no_false_positives() -> None:
    for text in ("Ganó 6-1", "terminó 6 - 1", "6–1", "por 6 a 1", "resultado: 6:1"):
        assert has_score(text, 6, 1), text
    assert not has_score("hubo 16-1", 6, 1) and not has_score("6-12", 6, 1)


def test_date_variants() -> None:
    d = date(2026, 10, 10)
    for text in ("2026-10-10", "10/10/2026", "10-10-2026", "el 10 de octubre", "10 de octubre de 2026"):
        assert has_date(text, d), text
    assert not has_date("el 11 de octubre", d)


def test_match_fact_answer_needs_teams_and_score() -> None:
    f = Fact("match", "m", (DOSA,), need_score=True)
    assert answer_ok(f, "El C.D. DOSA ganó 6-1 al San Felipe Neri Elipa Dragons.")
    assert not answer_ok(f, "El DOSA ganó 5-1 al Dragons.")  # marcador erróneo
    assert not answer_ok(f, "Ganó 6-1.")  # sin equipos


def test_scheduled_fact_needs_date() -> None:
    f = Fact("match", "m", (NEXT,), need_date=True, exclude="C.D. DOSA")
    assert answer_ok(f, "Juega contra Torrejón Five Play el 10 de octubre.")  # basta con nombrar al rival
    assert not answer_ok(f, "Juega contra Torrejón Five Play el 17 de octubre.")
    assert not answer_ok(f, "Juega el 10 de octubre.")  # sin rival


def test_count_and_limitation_and_refusal() -> None:
    assert answer_ok(Fact("count", "n", number=13), "Se han jugado 13 partidos")
    assert not answer_ok(Fact("count", "n", number=13), "Se han jugado 130 partidos")
    lim = Fact("limitation", "x")
    assert answer_ok(lim, "Mis herramientas no incluyen los goleadores.")
    assert not answer_ok(lim, "Marcó Pablo Pérez con 3 goles.")  # inventa el dato
    assert answer_ok(Fact("refusal", "x"), OFF_TOPIC_MESSAGE)
    assert not answer_ok(Fact("refusal", "x"), "Un chiste: ...")


def test_evidence_detects_truncated_results() -> None:
    f = Fact("match", "m", (DOSA, NEXT), need_score=True)
    full = {"matches": [{"id": 1, "home_score": 6, "away_score": 1, "date": "2026-09-26"},
                        {"id": 2, "home_score": None, "away_score": None, "date": "2026-10-10"}], "count": 2}
    cut = {"matches": [{"id": 1, "home_score": 6, "away_score": 1}], "count": 2, "truncated": {"total": 2}}
    assert evidence_ok(f, [full]) and not evidence_ok(f, [cut])
    wrong = {"matches": [{"id": 1, "home_score": 5, "away_score": 1}], "count": 1}
    assert not evidence_ok(Fact("match", "m", (DOSA,), need_score=True), [wrong])


def test_count_evidence_from_rows_or_count_field() -> None:
    rows = {"matches": [{"status": "finished"}, {"status": "scheduled"}, {"status": "scheduled"}], "count": 3}
    assert evidence_ok(Fact("count", "j", number=1, status="finished"), [rows])
    assert evidence_ok(Fact("count", "p", number=2, status="pending"), [rows])
    assert evidence_ok(Fact("count", "g", number=169), [{"matches": [], "count": 169}])
    assert not evidence_ok(Fact("count", "x", number=5, status="finished"), [rows])


def test_presentation_is_judged_separately() -> None:
    assert presentation_ok("El DOSA ganó 6-1.")
    assert not presentation_ok('{"matches": []}') and not presentation_ok("home_team_id=3")
    assert not presentation_ok("") and not presentation_ok("x" * 2000)


def test_policy_llm_follows_plan_strips_args_and_estimates_tokens() -> None:
    def plan(step: int, payloads: list[dict[str, object]]) -> list[ToolCall] | None:
        return [ToolCall("a", "list_matches", {"group_id": 1, "team_id": 11})] if step == 0 else None

    llm = PolicyLLM(plan, "fin", frozenset({"team_id"}))
    first = llm.complete("sys", [Message("user", "q")], [])
    assert first.tool_calls[0].arguments == {"group_id": 1}  # línea base sin team_id
    assert first.usage is not None and first.usage.estimated and first.usage.input_tokens > 0
    done = llm.complete("sys", [Message("user", "q"), Message("assistant", "", first.tool_calls),
                                Message("tool", tool_results=[ToolResult("a", "{}")])], [])
    assert done.text == "fin" and not done.tool_calls


def test_summary_and_table_exclude_not_evaluable_from_tokens() -> None:
    rs = [CaseResult("a", "x", "q", "pass", llm_calls=2, input_tokens=100, output_tokens=5, tokens_estimated=True),
          CaseResult("b", "x", "q", "fail", detail="evidencia incompleta", input_tokens=50),
          CaseResult("c", "x", "q", "not_evaluable", input_tokens=999)]
    s = summarize_results(rs)
    assert (s["pass"], s["fail"], s["not_evaluable"], s["input_tokens"]) == (1, 1, 1, 150)
    assert "TOTAL: 1 pass, 1 fail, 1 no evaluables" in format_table(rs)
