import json
from copy import deepcopy
from datetime import date, time
from pathlib import Path
from typing import Any

from futsal.ingestion.rffm.calendar import CalendarMatch
from futsal.ingestion.rffm.results_api import (
    Rejected,
    ResultMatch,
    RoundResult,
    parse_results_api,
    reconcile_calendar_results,
)

FX = Path(__file__).parents[1] / "fixtures" / "rffm_v2"
PAYLOAD: dict[str, Any] = json.loads((FX / "api_results_g24270141_r26.json").read_text(encoding="utf-8"))


def ok() -> RoundResult:
    res = parse_results_api(deepcopy(PAYLOAD), "24270141", 26)
    assert isinstance(res, RoundResult)
    return res


def test_round_26_parses_with_state_and_report() -> None:
    res = ok()
    assert (res.group_id, res.round_id, res.competition_id) == ("24270141", "26", "24038111")
    assert len(res.matches) == 7 and res.issues == []
    first = res.matches[0]
    assert first.match.external_match_id == "5338384"
    assert (first.match.home_score, first.match.away_score, first.match.status) == (2, 6, "finished")
    assert first.has_report and first.report_closed and not first.in_play
    assert first.match.status_raw == "estado=1;situacion_juego=1;motivo_estado="
    assert (first.match.scheduled_date, first.match.scheduled_time) == (date(2026, 5, 24), time(13, 15))


def test_accepts_json_text_and_reuses_page_status_logic() -> None:
    res = parse_results_api(json.dumps(PAYLOAD), "24270141", "26")
    assert isinstance(res, RoundResult) and len(res.matches) == 7
    p = deepcopy(PAYLOAD)
    p["partidos"][1].update(motivo_estado="Aplazado por inclemencias", Goles_casa="", Goles_visitante="")
    r = parse_results_api(p, "24270141", 26)
    assert isinstance(r, RoundResult) and r.matches[1].match.status == "postponed"


def test_null_and_empty_are_transient_rejections() -> None:
    for payload in ((FX / "api_results_null.json").read_text(encoding="utf-8"), None, "", "  \n"):
        r = parse_results_api(payload, "24270141", 26)
        assert isinstance(r, Rejected) and r.reason == "null" and r.transient


def test_other_group_or_round_is_rejected() -> None:
    other = deepcopy(PAYLOAD)
    other["codigo_grupo"] = "24270140"
    r = parse_results_api(other, "24270141", 26)
    assert isinstance(r, Rejected) and r.reason == "group_mismatch" and not r.transient
    r2 = parse_results_api(PAYLOAD, "24270141", 25)
    assert isinstance(r2, Rejected) and r2.reason == "round_mismatch"


def test_garbage_is_rejected_without_exception() -> None:
    for bad in ("{no json", [], 3):
        assert isinstance(parse_results_api(bad, "1", 1), Rejected)
    p = deepcopy(PAYLOAD)
    p["partidos"] = "x"
    r = parse_results_api(p, "24270141", 26)
    assert isinstance(r, Rejected) and r.reason == "no_matches"


def test_match_without_codacta_is_an_issue() -> None:
    p = deepcopy(PAYLOAD)
    p["partidos"][0]["codacta"] = ""
    r = parse_results_api(p, "24270141", 26)
    assert isinstance(r, RoundResult) and len(r.matches) == 6 and r.issues == ["partido sin codacta"]


def cal_for(rm: ResultMatch, **over: Any) -> CalendarMatch:
    m = rm.match
    base: dict[str, Any] = {
        "codacta": m.external_match_id, "round_code": "26", "home_team_id": m.home_team_external_id,
        "home_team_name": m.home_team_name, "away_team_id": m.away_team_external_id,
        "away_team_name": m.away_team_name, "home_score": m.home_score, "away_score": m.away_score,
        "scheduled_date": m.scheduled_date, "scheduled_time": m.scheduled_time, "time_known": True,
        "kickoff": None, "venue": None, "venue_id": None}
    base.update(over)
    return CalendarMatch(**base)


def test_reconcile_consistent_and_discrepancies() -> None:
    rm = ok().matches[0]
    good = reconcile_calendar_results(cal_for(rm), rm)
    assert good.consistent and good.discrepancies == [] and good.preferred == "results"
    bad = reconcile_calendar_results(
        cal_for(rm, home_score=3, scheduled_date=date(2026, 5, 25), scheduled_time=time(12, 0)), rm)
    assert not bad.consistent
    assert {d.field for d in bad.discrepancies} == {"home_score", "scheduled_date", "scheduled_time"}
    d = next(d for d in bad.discrepancies if d.field == "home_score")
    assert (d.calendar, d.results) == (3, 2)


def test_reconcile_ignores_unknown_time_and_accepts_plain_match() -> None:
    rm = ok().matches[0]
    unknown = cal_for(rm, scheduled_time=None, time_known=False)
    assert reconcile_calendar_results(unknown, rm.match).consistent
    pending = cal_for(rm, home_score=None, away_score=None)
    assert {d.field for d in reconcile_calendar_results(pending, rm).discrepancies} == {"home_score", "away_score"}
