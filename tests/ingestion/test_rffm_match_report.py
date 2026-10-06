import json
import re
import socket
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.report_compare import (
    DbMatchView,
    compare,
    discrepancies,
    events_score,
    material_errors,
)
from futsal.ingestion.rffm.report_parser import absolute_url, parse_match_report
from futsal.ingestion.rffm.report_preview import NA, render_preview

FIXTURE = Path(__file__).parent.parent / "fixtures" / "rffm_match_report.html"
URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"
OBSERVED = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _mut(html: str, fn: Any) -> str:
    m = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', html, re.S)
    assert m
    data = json.loads(m.group(2))
    fn(data["props"]["pageProps"]["game"])
    return html.replace(m.group(2), json.dumps(data))


def _view(**kw: Any) -> DbMatchView:
    base: dict[str, Any] = dict(
        external_id="5575697", competition_external_id="26738243", group_external_id="26738245",
        round_external_id="1", home_team_external_id="19068192",
        home_team_name="PARQUE NORTE F.S. - RODILLITO 'B'", away_team_external_id="18730340",
        away_team_name="C.D. LOPE DE VEGA 'B'", home_score=3, away_score=4,
        scheduled_date=date(2026, 9, 26), scheduled_time=None, venue="COL. NUEVA CASTILLA (futbol sala)")
    base.update(kw)
    return DbMatchView(**base)


def test_identity(html: str) -> None:
    r = parse_match_report(html, URL, OBSERVED).report
    assert (r.report_external_id, r.match_external_id) == ("5575697", "5575697")
    assert (r.season_external_id, r.competition_external_id, r.group_external_id,
            r.round_external_id) == ("22", "26738243", "26738245", "1")
    assert r.closed is True and len(r.content_hash) == 64


def test_teams_and_score(html: str) -> None:
    m = parse_match_report(html, URL, OBSERVED).match
    assert (m.home_team_external_id, m.away_team_external_id) == ("19068192", "18730340")
    assert m.home_team_name and m.away_team_name
    assert (m.home_score, m.away_score, m.status) == (3, 4, "finished")
    assert m.scheduled_date == date(2026, 9, 26)  # el acta usa dd-mm-aaaa


def test_players_by_side(html: str) -> None:
    ps = parse_match_report(html, URL, OBSERVED).players
    assert sum(p.team_side == "home" for p in ps) == 9 and sum(p.team_side == "away" for p in ps) == 12
    assert all(p.team_external_id for p in ps)
    assert len({(p.team_side, p.sequence) for p in ps}) == len(ps)


def test_starters_substitutes_captain_goalkeeper(html: str) -> None:
    ps = parse_match_report(html, URL, OBSERVED).players
    assert {p.lineup_status for p in ps} == {"starter", "substitute"}
    assert sum(p.captain for p in ps) == 2 and any(p.role == "goalkeeper" for p in ps)


def test_optional_shirt_number(html: str) -> None:
    h = _mut(html, lambda g: g["jugadores_equipo_local"][0].update(dorsal=""))
    assert parse_match_report(h, URL, OBSERVED).players[0].shirt_number is None


def test_player_without_external_id(html: str) -> None:
    h = _mut(html, lambda g: g["jugadores_equipo_local"][0].update(codjugador=""))
    p = parse_match_report(h, URL, OBSERVED).players[0]
    assert p.player_external_id is None and p.display_name


def test_absent_sections_are_reported(html: str) -> None:
    v = parse_match_report(html, URL, OBSERVED).validation
    assert "tarjetas_equipo_local" in v.empty_sections and v.unparsed_sections == []
    h = _mut(html, lambda g: g["sustituciones_equipo_local"].append({"x": "1"}))
    assert parse_match_report(h, URL, OBSERVED).validation.unparsed_sections == [
        "sustituciones_equipo_local (1 filas)"]


def test_event_without_minute_goes_last_and_is_null(html: str) -> None:
    h = _mut(html, lambda g: g["goles_equipo_local"][0].update(minuto=""))
    ev = parse_match_report(h, URL, OBSERVED).events
    assert ev[-1].minute is None and ev[-1].event_type == "goal"
    assert [e.sequence for e in ev] == list(range(1, len(ev) + 1))
    assert sum(e.event_type == "goal" for e in ev) == 7 and sum(e.event_type == "card" for e in ev) == 1


def test_urls_are_absolute(html: str) -> None:
    crests = [t.crest_url for t in parse_match_report(html, URL, OBSERVED).teams]
    assert all(c and c.startswith("https://appweb.rffm.es/pnfg/") for c in crests)
    assert absolute_url("/a/b.png", "https://x.es/") == "https://x.es/a/b.png"
    assert absolute_url("", "https://x.es/") is None


@pytest.mark.parametrize("bad", [
    "<html></html>",
    '<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>',
    '<script id="__NEXT_DATA__">{"props":{"pageProps":{"game":{"codacta":"1"}}}}</script>'])
def test_unexpected_structure(bad: str) -> None:
    with pytest.raises(RffmParseError):
        parse_match_report(bad, URL)


def test_hash_deterministic_and_content_sensitive(html: str) -> None:
    a = parse_match_report(html, URL, OBSERVED)
    b = parse_match_report(html, URL, datetime(2030, 1, 1, tzinfo=UTC))
    assert a.report.content_hash == b.report.content_hash
    h = _mut(html, lambda g: g.update(goles_local="9"))
    assert parse_match_report(h, URL, OBSERVED).report.content_hash != a.report.content_hash


def test_comparison_matches(html: str) -> None:
    rows = compare(parse_match_report(html, URL, OBSERVED), _view(scheduled_time=None))
    assert all(r.equal for r in rows if r.field != "hora")


def test_score_discrepancy_detected_not_material(html: str) -> None:
    rows = compare(parse_match_report(html, URL, OBSERVED), _view(home_score=2))
    assert [d.field for d in discrepancies(rows) if "goles" in d.field] == ["goles local"]
    assert material_errors(rows) == []
    other_team = compare(parse_match_report(html, URL, OBSERVED), _view(home_team_external_id="1"))
    assert material_errors(other_team)


def test_txt_preview(html: str) -> None:
    r = parse_match_report(html, URL, OBSERVED)
    txt = render_preview(r, [("match_players", 21, 0, 0)])
    for section in ("ACTA", "ALINEACIÓN LOCAL", "ALINEACIÓN VISITANTE", "CUERPO TÉCNICO",
                    "OFICIALES", "EVENTOS", "COMPARACIÓN", "IMPORTACIÓN PREVISTA"):
        assert section in txt
    assert "Marcador: 3-4" in txt and "match_players | 21 | 0 | 0" in txt
    assert NA in txt  # comparación no disponible sin BD


def test_no_network(html: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_net(*a: object, **k: object) -> None:
        raise AssertionError("acceso a red")

    monkeypatch.setattr(socket, "socket", no_net)
    assert parse_match_report(html, URL, OBSERVED).players


def test_own_goal_code_102_credits_the_rival(html: str) -> None:
    base = parse_match_report(html, URL, OBSERVED)
    assert events_score(base) == (3, 4) and not any(e.event_type == "own_goal" for e in base.events)
    h = _mut(html, lambda g: g["goles_equipo_local"][0].update(tipo_gol="102"))
    r = parse_match_report(h, URL, OBSERVED)
    own = [e for e in r.events if e.event_type == "own_goal"]
    assert len(own) == 1 and own[0].team_side == "home" and own[0].source_code == "102"
    assert events_score(r) == (2, 5)  # la propia del local suma al visitante
    assert "gol en propia meta" in render_preview(r)
