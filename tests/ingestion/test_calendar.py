import json
import re
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from futsal.ingestion.rffm.calendar import parse_calendar
from futsal.ingestion.rffm.parser import RffmParseError

FX = Path(__file__).parents[1] / "fixtures" / "rffm_v2"
S22 = (FX / "calendario_s22_c26738243_g26738245.html").read_text(encoding="utf-8")
S10 = (FX / "calendario_s10_c18329896_g18298068.html").read_text(encoding="utf-8")
REQ22 = {"season": "22", "game_type": "3", "competition": "26738243", "group": "26738245"}
REQ10 = {"season": "10", "game_type": "3", "competition": "18329896", "group": "18298068"}


def with_calendar(html: str, mutate: Callable[[dict[str, Any]], object]) -> str:
    m = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', html, re.DOTALL)
    assert m
    data = json.loads(m.group(2))
    mutate(data["props"]["pageProps"]["calendar"])
    return html[: m.start()] + m.group(1) + json.dumps(data) + m.group(3) + html[m.end():]


def test_s22_league_calendar() -> None:
    page = parse_calendar(S22, REQ22)
    assert page.scope.ok and page.issues == []
    assert len(page.rounds) == 26 and len(page.matches) == 182
    assert sum(m.home_score is not None for m in page.matches) == 45
    assert page.competition_format == "2" and page.group_name == "Grupo 2" and page.current_round == "7"
    assert len({m.codacta for m in page.matches}) == 182
    first = page.rounds[0]
    assert (first.code, first.number, first.label) == ("1", 1, "1 (26-09-2026)")
    m = first.matches[0]
    assert (m.codacta, m.home_team_id, m.away_team_id) == ("5575697", "19068192", "18730340")
    assert (m.home_score, m.away_score, m.scheduled_date, m.scheduled_time) == (3, 4, date(2026, 9, 26), time(16, 0))
    assert m.kickoff == datetime(2026, 9, 26, 16, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    assert m.kickoff is not None and m.kickoff.utcoffset().total_seconds() == 7200  # type: ignore[union-attr]
    assert m.venue_id == "20481040" and m.venue and "NUEVA CASTILLA" in m.venue


def test_empty_hour_means_date_only() -> None:
    page = parse_calendar(S22, REQ22)
    unknown = [m for m in page.matches if not m.time_known]
    assert len(unknown) == 116
    assert all(m.scheduled_time is None and m.kickoff is None and m.scheduled_date for m in unknown)
    assert all(m.scheduled_time is not None for m in page.matches if m.time_known)


def test_midnight_is_unknown_time_and_empty_score_is_none() -> None:
    def mut(cal: dict[str, Any]) -> None:
        cal["rounds"][0]["equipos"][0].update(hora="00:00", goles_casa="", goles_visitante="")

    m = parse_calendar(with_calendar(S22, mut), REQ22).rounds[0].matches[0]
    assert not m.time_known and m.kickoff is None and m.home_score is None and m.away_score is None


def test_s10_cup_with_eliminatorias_group() -> None:
    page = parse_calendar(S10, REQ10)
    assert page.scope.ok
    assert page.group_name == "Eliminatorias" and page.competition_format == "1" and page.season_name == "2014-2015"
    assert [len(r.matches) for r in page.rounds] == [16, 8, 4, 2, 1]
    assert len(page.matches) == 31 and sum(m.home_score is not None for m in page.matches) == 26
    assert page.rounds[0].label == "Dieciseisavos, 30-05-2015 (Ida)"
    assert {i.code for i in page.issues} == {"missing_date"}  # 2 partidos sin fecha
    assert sum(m.scheduled_date is None for m in page.matches) == 2


def test_rest_matches_and_empty_codes_are_issues_not_exceptions() -> None:
    def mut(cal: dict[str, Any]) -> None:
        eq = cal["rounds"][0]["equipos"]
        eq[0].update(codigo_equipo_visitante="", equipo_visitante="")  # descanso
        eq[1].update(codigo_equipo_local="")
        eq[2].update(codacta="")
        eq[3]["goles_casa"] = "x"

    page = parse_calendar(with_calendar(S22, mut), REQ22)
    assert len(page.matches) == 182 - 3
    codes = sorted(i.code for i in page.issues)
    assert codes == ["match_without_codacta", "match_without_team", "match_without_team", "non_numeric_score"]


def test_duplicate_codacta_is_an_issue() -> None:
    def mut(cal: dict[str, Any]) -> None:
        cal["rounds"][1]["equipos"][0]["codacta"] = cal["rounds"][0]["equipos"][0]["codacta"]

    page = parse_calendar(with_calendar(S22, mut), REQ22)
    assert len(page.matches) == 181 and [i.code for i in page.issues] == ["duplicate_codacta"]


def test_scope_mismatch_returns_no_rounds() -> None:
    page = parse_calendar(S22, {**REQ22, "season": "21"})
    assert not page.scope.ok and page.rounds == [] and page.issues[0].code == "scope_mismatch"
    other = parse_calendar((FX / "resultados_scope_mismatch_pedido_s21.html").read_text(encoding="utf-8"),
                           {"season": "21", "game_type": "3"})
    assert not other.scope.ok and other.matches == []


def test_missing_calendar_structure_raises() -> None:
    html = with_calendar(S22, lambda cal: cal.pop("rounds"))
    with pytest.raises(RffmParseError):
        parse_calendar(html, REQ22)
