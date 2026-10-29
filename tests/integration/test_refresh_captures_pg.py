"""Fase 21 (R3): la consulta sirve la última captura `ok`; `incomplete`/`empty` posteriores se conservan pero no la sustituyen."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup, Season
from futsal.db.table_models import ScorerRow, ScorerSnapshot, StandingsRow, StandingsSnapshot
from futsal.repositories import table_queries as tq

pytestmark = pytest.mark.integration
T0 = datetime(2099, 10, 1, 12, 0, tzinfo=UTC)


def _group(s: Session) -> int:
    season = Season(external_id="99", name="s")
    s.add(season)
    s.flush()
    comp = Competition(external_id="c1", season_id=season.id, name="C")
    s.add(comp)
    s.flush()
    g = CompetitionGroup(external_id="g1", competition_id=comp.id, name="G")
    s.add(g)
    s.flush()
    return g.id


def _standings(s: Session, gid: int, rnd: int, status: str, at: datetime, teams: int, tag: str) -> StandingsSnapshot:
    snap = StandingsSnapshot(group_id=gid, round_number=rnd, status=status, content_hash=tag, row_count=teams, source_url="u",
                             fetched_at=at, first_seen_at=at, last_seen_at=at, meta={})
    s.add(snap)
    s.flush()
    s.add_all(StandingsRow(snapshot_id=snap.id, position=i + 1, team_external_id=f"t{i}", team_name=f"T{i}", played=rnd, won=0,
                           drawn=0, lost=0, goals_for=1, goals_against=1, points=i) for i in range(teams))
    return snap


def _scorers(s: Session, gid: int, status: str, at: datetime, goals: list[int], tag: str) -> ScorerSnapshot:
    snap = ScorerSnapshot(group_id=gid, status=status, content_hash=tag, row_count=len(goals), goals_total=sum(goals), source_url="u",
                          fetched_at=at, first_seen_at=at, last_seen_at=at, meta={})
    s.add(snap)
    s.flush()
    s.add_all(ScorerRow(snapshot_id=snap.id, row_order=i + 1, player_external_id=f"p{i}", player_name=f"P{i}", team_external_id="t",
                        team_name="T", goals=g) for i, g in enumerate(goals))
    return snap


def test_incomplete_after_ok_does_not_replace_it(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _standings(s, gid, 5, "ok", T0, 6, "a")
        _standings(s, gid, 5, "incomplete", T0 + timedelta(days=1), 4, "b")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None
    assert out["status"] == "ok" and len(out["standings"]) == 6
    assert out["capture"]["latest_status"] == "incomplete" and out["capture"]["last_valid_at"] == T0.isoformat()
    assert "parcial" in out["warning"] and out["source"]["captured_at"] == T0.isoformat()


def test_later_round_incomplete_does_not_hide_previous_ok_round(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _standings(s, gid, 5, "ok", T0, 6, "a")
        _standings(s, gid, 6, "incomplete", T0 + timedelta(days=1), 3, "b")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None and out["source"]["round"] == 5 and out["status"] == "ok"
    assert "jornada 6" in out["warning"]


def test_newer_ok_wins_and_no_warning(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _standings(s, gid, 5, "ok", T0, 6, "a")
        _standings(s, gid, 6, "ok", T0 + timedelta(days=1), 6, "b")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None and out["source"]["round"] == 6 and "warning" not in out


def test_empty_is_used_only_when_there_was_never_an_ok(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _standings(s, gid, 1, "empty", T0, 0, "a")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None and out["status"] == "empty"
    with Session(engine) as s, s.begin():
        _standings(s, gid, 1, "ok", T0 + timedelta(days=1), 6, "b")
        _standings(s, gid, 1, "empty", T0 + timedelta(days=2), 0, "c")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None and out["status"] == "ok" and len(out["standings"]) == 6 and "vacío" in out["warning"]


def test_only_incomplete_is_served_and_flagged(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _standings(s, gid, 2, "incomplete", T0, 3, "a")
    with Session(engine) as s:
        out = tq.standings(s, gid)
    assert out is not None and out["status"] == "incomplete" and out["coverage"]["complete"] is False


def test_scorers_incomplete_and_empty_do_not_replace_ok(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _scorers(s, gid, "ok", T0, [9, 7, 7], "a")
        _scorers(s, gid, "incomplete", T0 + timedelta(days=1), [9], "b")
        _scorers(s, gid, "empty", T0 + timedelta(days=2), [], "c")
    with Session(engine) as s:
        out = tq.scorer_table(s, gid)
    assert out is not None and out["status"] == "ok" and [r["goals"] for r in out["scorers"]] == [9, 7, 7]
    assert out["capture"]["latest_status"] == "empty" and out["capture"]["last_valid_at"] == T0.isoformat()
    assert "vacío" in out["warning"] and out["source"]["captured_at"] == T0.isoformat()


def test_scorers_empty_is_official_when_no_ok_ever(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        _scorers(s, gid, "empty", T0, [], "a")
    with Session(engine) as s:
        out = tq.scorer_table(s, gid)
    assert out is not None and out["status"] == "empty" and out["scorers"] == []


def test_reobserving_an_old_ok_after_incomplete_makes_it_current_again(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        gid = _group(s)
        ok = _scorers(s, gid, "ok", T0, [3], "a")
        _scorers(s, gid, "incomplete", T0 + timedelta(days=1), [2], "b")
        ok.last_seen_at = T0 + timedelta(days=2)  # la fuente vuelve a publicar la misma tabla válida
    with Session(engine) as s:
        out = tq.scorer_table(s, gid)
    assert out is not None and out["status"] == "ok" and "warning" not in out
