import json
import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props, parse_round

FIXTURE = Path(__file__).parent.parent / "fixtures" / "rffm_round_2.html"
URL = "https://www.rffm.es/competicion/resultados-y-jornadas?jornada=2"
OBSERVED = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_round_recognised(html: str) -> None:
    r = parse_round(html, URL, OBSERVED)
    assert (r.external_round_id, r.round_number_or_label) == ("2", "2")
    assert (r.season_external_id, r.competition_external_id, r.group_external_id) == (
        "22", "26738243", "26738245")


def test_match_count_matches_fixture(html: str) -> None:
    # 7 verificado contra el contenido real del fixture (pageProps.results.partidos)
    assert len(parse_round(html, URL, OBSERVED).matches) == 7


def test_teams_and_ids_present(html: str) -> None:
    for m in parse_round(html, URL, OBSERVED).matches:
        assert m.home_team_name and m.away_team_name
        assert m.home_team_external_id and m.away_team_external_id
        assert m.home_team_name != m.home_team_external_id


def test_finished_scores_are_int(html: str) -> None:
    ms = parse_round(html, URL, OBSERVED).matches
    assert all(m.status == "finished" for m in ms)
    assert all(isinstance(m.home_score, int) and isinstance(m.away_score, int) for m in ms)


def _mutated(html: str, **changes: str) -> str:
    m = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', html, re.S)
    assert m
    data = json.loads(m.group(2))
    data["props"]["pageProps"]["results"]["partidos"][0].update(changes)
    return html.replace(m.group(2), json.dumps(data))


def test_missing_score_is_null_and_scheduled(html: str) -> None:
    h = _mutated(html, Goles_casa="", Goles_visitante="", acta_cerrada="0")
    m = parse_round(h, URL, OBSERVED).matches[0]
    assert m.home_score is None and m.away_score is None
    assert m.status == "scheduled"


def test_postponed_detected_from_reason(html: str) -> None:
    h = _mutated(html, Goles_casa="", Goles_visitante="", motivo_estado="Aplazado")
    m = parse_round(h, URL, OBSERVED).matches[0]
    assert m.status == "postponed" and "motivo_estado=Aplazado" in (m.status_raw or "")


def test_report_urls_null_or_absolute(html: str) -> None:
    for m in parse_round(html, URL, OBSERVED).matches:
        for u in (m.match_report_url, m.comparison_url):
            assert u is None or u.startswith("https://")


def test_match_id_from_codacta(html: str) -> None:
    ids = [m.external_match_id for m in parse_round(html, URL, OBSERVED).matches]
    assert all(i and i.isdigit() for i in ids) and len(set(ids)) == len(ids)


def test_date_time_parsed(html: str) -> None:
    m = parse_round(html, URL, OBSERVED).matches[0]
    assert m.scheduled_date is not None and m.scheduled_time is not None
    assert m.timezone == "Europe/Madrid"


@pytest.mark.parametrize("bad", ["<html></html>", '<script id="__NEXT_DATA__">{}</script>',
    '<script id="__NEXT_DATA__">{"props":{"pageProps":{"results":{}}}}</script>'])
def test_unexpected_structure_raises_clear_error(bad: str) -> None:
    with pytest.raises(RffmParseError):
        parse_round(bad, URL)


def test_extract_props_invalid_json() -> None:
    with pytest.raises(RffmParseError, match="pageProps"):
        extract_page_props('<script id="__NEXT_DATA__">not json</script>')


def test_parser_works_offline(html: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_net(*a: object, **k: object) -> None:
        raise AssertionError("acceso a red")

    monkeypatch.setattr(socket, "socket", no_net)
    assert parse_round(html, URL, OBSERVED).matches


def test_deterministic_except_observed_at(html: str) -> None:
    a = parse_round(html, URL).model_dump(exclude={"observed_at"})
    b = parse_round(html, URL).model_dump(exclude={"observed_at"})
    assert a == b
