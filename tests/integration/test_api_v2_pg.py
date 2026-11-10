"""API v2 (consultas sin grupo) sobre PG desechable: liga importada + grupos sembrados con SQLAlchemy (varias competiciones y categorías)."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from itertools import count
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from futsal.api.app import create_app
from futsal.db.models import (
    Club,
    Competition,
    CompetitionGroup,
    DatasetRefresh,
    IngestionRun,
    Match,
    MatchEvent,
    MatchOfficial,
    MatchPlayer,
    MatchReport,
    MatchReportObservation,
    Player,
    PlayerTeamMembership,
    Round,
    Team,
    TeamEntity,
)
from futsal.db.table_models import ScorerRow, ScorerSnapshot, StandingsRow, StandingsSnapshot
from futsal.importer.report_service import run_report_import
from futsal.importer.service import run_import
from futsal.ingestion.rffm.report_parser import parse_match_report

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"
URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"
MADRID = ZoneInfo("Europe/Madrid")
TODAY = date(2026, 11, 9)
_ids = count(1000)


def at(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """Hora local de Madrid (la BD la guarda en UTC)."""
    return datetime(y, m, d, h, mi, tzinfo=MADRID)


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _ext() -> str:
    return f"T{next(_ids)}"


class Seed:
    """Construye grupos, partidos, actas y tablas con SQLAlchemy dentro de la temporada importada."""

    def __init__(self, s: Session, season_id: int) -> None:
        self.s, self.season_id = s, season_id
        self.run_id = s.scalars(select(IngestionRun.id).limit(1)).one()
        self.rounds: dict[int, Round] = {}

    def comp(self, name: str, category: str = "unknown", gender: str = "unknown", kind: str = "unknown",
             review: str = "needs_review") -> Competition:
        c = Competition(source="rffm", season_id=self.season_id, external_id=_ext(), name=name, category=category, gender=gender,
                        kind=kind, review_status=review)
        self.s.add(c)
        self.s.flush()
        return c

    def group(self, comp: Competition, name: str, teams: list[str]) -> tuple[CompetitionGroup, dict[str, Team]]:
        g = CompetitionGroup(source="rffm", external_id=_ext(), competition_id=comp.id, name=name)
        self.s.add(g)
        self.s.flush()
        self.rounds[g.id] = Round(source="rffm", external_id="1", group_id=g.id, visible_label="1", visible_number=1)
        self.s.add(self.rounds[g.id])
        made = {n: Team(source="rffm", external_id=_ext(), group_id=g.id, name=n) for n in teams}
        self.s.add_all(made.values())
        self.s.flush()
        return g, made

    def match(self, g: CompetitionGroup, home: Team, away: Team, when: datetime | None, score: tuple[int, int] | None = None,
              venue: str | None = None, status: str | None = None) -> Match:
        m = Match(source="rffm", external_id=_ext(), group_id=g.id, round_id=self.rounds[g.id].id, home_team_id=home.id,
                  away_team_id=away.id, scheduled_at=when, scheduled_date=when.astimezone(MADRID).date() if when else None,
                  timezone="Europe/Madrid", venue=venue, status=status or ("finished" if score else "scheduled"),
                  home_score=score[0] if score else None, away_score=score[1] if score else None)
        self.s.add(m)
        self.s.flush()
        return m

    def player(self, name: str, ext: str, team: Team) -> Player:
        p = Player(source="rffm", external_id=ext, display_name=name)
        self.s.add(p)
        self.s.flush()
        self.s.add(PlayerTeamMembership(player_id=p.id, team_id=team.id, season_id=self.season_id))
        return p

    def report(self, m: Match, observed: datetime, officials: list[tuple[str, str]], players: list[tuple[Player, Team]],
               goals: list[tuple[Player, Team]]) -> None:
        r = MatchReport(source="rffm", external_id=_ext(), match_id=m.id)
        self.s.add(r)
        self.s.flush()
        o = MatchReportObservation(report_id=r.id, ingestion_run_id=self.run_id, observed_at=observed, content_hash=_ext(), source_url="x",
                                   closed=True, status="finished", round_external_id="1", home_score=m.home_score, away_score=m.away_score)
        self.s.add(o)
        self.s.flush()
        for i, (name, role) in enumerate(officials):
            self.s.add(MatchOfficial(observation_id=o.id, display_name=name, role=role, sequence=i))
        for i, (p, t) in enumerate(players):
            self.s.add(MatchPlayer(observation_id=o.id, team_id=t.id, player_id=p.id, player_external_id=p.external_id,
                                   display_name=p.display_name, role="player", lineup_status="starter", is_captain=False, sequence=i))
        for i, (p, t) in enumerate(goals):
            self.s.add(MatchEvent(observation_id=o.id, sequence=i, event_type="goal", team_id=t.id, player_id=p.id,
                                  player_external_id=p.external_id, player_name=p.display_name, minute=5 + i))

    def scorers(self, g: CompetitionGroup, status: str, fetched: datetime, rows: list[tuple[str, str, Team | None, int, int | None]],
                seen: datetime | None = None, players: dict[str, Player] | None = None) -> None:
        snap = ScorerSnapshot(group_id=g.id, status=status, content_hash=_ext(), row_count=len(rows), goals_total=sum(r[3] for r in rows),
                              source_url="x", fetched_at=fetched, last_seen_at=seen or fetched)
        self.s.add(snap)
        self.s.flush()
        for i, (name, ext, team, goals, played) in enumerate(rows):
            p = (players or {}).get(ext)
            self.s.add(ScorerRow(snapshot_id=snap.id, row_order=i, player_external_id=ext, player_id=p.id if p else None, player_name=name,
                                 team_external_id=team.external_id if team else "0", team_id=team.id if team else None,
                                 team_name=team.name if team else "EQUIPO SIN ENLACE", matches_played=played, goals=goals))


def _ids_of(c: dict[str, Any]) -> list[str]:
    return [i["ref"] for i in c["items"]]


@pytest.fixture()
def world(engine: Engine, tmp_path: Path) -> Iterator[dict[str, Any]]:
    with engine.begin() as c:
        c.execute(text("TRUNCATE clubs, team_entities CASCADE"))
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    path = tmp_path / "acta.json"
    path.write_text(parse_match_report(html, URL).model_dump_json(), encoding="utf-8")
    assert run_report_import(engine, path).status == "success"
    w: dict[str, Any] = {}
    with Session(engine) as s, s.begin():
        season_id = s.scalars(select(Competition.season_id).limit(1)).one()
        base = s.scalars(select(Competition)).one()
        base.category, base.gender, base.kind, base.review_status = "senior", "male", "league", "auto"
        base_group = s.scalars(select(CompetitionGroup)).one()
        sd = Seed(s, season_id)
        # --- senior: Segunda autonómica (4 equipos, A/B del mismo club)
        c_s2 = sd.comp("SEGUNDA AUTONOMICA AFICIONADO FS", "senior", "male", "league", "auto")
        g_s2, t = sd.group(c_s2, "Grupo 1", ["C.F.S. PINTO A", "C.F.S. PINTO B", "MOSTOLES FS", "PARLA FS"])
        pa, pb, mo, pl = t["C.F.S. PINTO A"], t["C.F.S. PINTO B"], t["MOSTOLES FS"], t["PARLA FS"]
        m1 = sd.match(g_s2, mo, pl, at(2026, 10, 24, 18), (3, 2), venue="POLIDEPORTIVO LA ESTACIÓN")
        m2 = sd.match(g_s2, pa, mo, at(2026, 10, 24, 12, 30), (1, 1))
        sd.match(g_s2, pl, pb, at(2026, 10, 25, 10), (0, 4))
        sd.match(g_s2, pb, pa, at(2026, 10, 24, 10), (2, 5))
        sd.match(g_s2, pa, pl, at(2026, 11, 14, 17))
        # --- juvenil: Pinto sin sufijo + otro club distinto cuyo nombre base contiene «pinto»
        c_j = sd.comp("LIGA NACIONAL JUVENIL FS", "juvenil", "male", "league", "auto")
        g_j, t = sd.group(c_j, "Grupo 1", ["PINTO C.F.S.", "ATLETICO PINTO F.S.", "ALCORCON JUVENIL", "GETAFE JUVENIL"])
        pj, ap, al, ge = t["PINTO C.F.S."], t["ATLETICO PINTO F.S."], t["ALCORCON JUVENIL"], t["GETAFE JUVENIL"]
        j1 = sd.match(g_j, pj, ap, at(2026, 10, 24, 12, 30), (2, 2))
        j2 = sd.match(g_j, al, ge, at(2026, 10, 25, 0, 30), (6, 0))
        sd.match(g_j, ge, pj, at(2026, 10, 24, 23, 30), (1, 3))
        # --- sin clasificar (categoría unknown) y clasificada solo por nombre
        c_u = sd.comp("TORNEO SIN CLASIFICAR FS")
        g_u, t = sd.group(c_u, "Grupo 1", ["UNKNOWN FS"])
        c_n = sd.comp("LIGA INFANTIL SIN CLASIFICAR FS")
        g_n, t_n = sd.group(c_n, "Grupo 1", ["INFANTIL FS"])
        # --- tres competiciones cadete con partidos el mismo día (cambio de hora de octubre y de marzo)
        cad: list[CompetitionGroup] = []
        for i, (h, mi) in enumerate([(10, 0), (12, 30), (18, 0)], 1):
            c = sd.comp(f"LIGA CADETE {i} FS", "cadete", "male", "league", "auto")
            g, t = sd.group(c, "Grupo 1", [f"CADETE {i} LOCAL", f"CADETE {i} VISITANTE"])
            cad.append(g)
            sd.match(g, *t.values(), at(2026, 10, 24, h, mi), (1, 0))
            if i == 1:
                sd.match(g, *t.values(), at(2026, 10, 25, 10), (2, 0))      # día del cambio de hora (+01:00)
                sd.match(g, *t.values(), at(2027, 3, 28, 18), None)          # día del cambio de marzo (+02:00)
        # --- jugadores y actas
        quintanar = sd.player("QUINTANAR PEREZ, PABLO", "P-1", mo)
        munoz = sd.player("MUÑOZ GÓMEZ, ÁLVARO", "P-2", pl)
        juan_a = sd.player("GARCIA LOPEZ, JUAN", "P-3", mo)
        juan_b = sd.player("GARCIA LOPEZ, JUAN", "P-4", al)
        sergio = sd.player("JIMENEZ RUIZ, SERGIO", "P-5", al)
        sd.report(m1, utc(2026, 11, 1, 10), [("RAMOS PEREZ, ANTONIO", "ARBITRO"), ("LOPEZ DIAZ, MARTA", "ARBITRO ASISTENTE")],
                  [(quintanar, mo), (juan_a, mo), (munoz, pl)], [(quintanar, mo)])
        sd.report(m2, utc(2026, 11, 2, 10), [("RAMOS PEREZ, ANTONIO", "ARBITRO")], [(quintanar, mo), (juan_a, mo)], [(quintanar, mo)])
        sd.report(j1, utc(2026, 11, 3, 10), [("RAMOS PEREZ, ANTONIO", "ARBITRO")], [], [])
        sd.report(j2, utc(2026, 11, 4, 10), [("SANZ MORA, LUIS", "ARBITRO")], [(juan_b, al), (sergio, al)], [(sergio, al), (sergio, al)])
        # --- tablas oficiales de goleadores
        sd.scorers(g_s2, "ok", utc(2026, 11, 7, 10), [("QUINTANAR PEREZ, PABLO", "P-1", mo, 3, 4)])             # antigua: no cuenta
        sd.scorers(g_s2, "ok", utc(2026, 11, 8, 10), [("ZAMORA VEGA, IVAN", "Z-1", pl, 7, 6), ("QUINTANAR PEREZ, PABLO", "P-1", mo, 5, 6),
                                                      ("ABAD RUIZ, LUIS", "A-1", pa, 5, 6)], players={"P-1": quintanar})
        sd.scorers(g_s2, "incomplete", utc(2026, 11, 9, 8), [("FALSO, JUGADOR", "F-1", pa, 99, 1)])           # incompleta posterior: no cuenta
        sd.scorers(base_group, "ok", utc(2026, 11, 7, 9), [("PRIMERO, GOLEADOR", "B-1", None, 9, 10), ("SEGUNDO, GOLEADOR", "B-2", None, 4, 9)])
        sd.scorers(g_j, "ok", utc(2026, 11, 8, 12), [("JIMENEZ RUIZ, SERGIO", "P-5", al, 12, 8), ("OTRO, JUGADOR", "J-2", ge, 3, 8)],
                   players={"P-5": sergio})
        sd.scorers(g_u, "ok", utc(2026, 11, 5, 9), [("SIN CATEGORIA, JUGADOR", "U-1", None, 50, 5)])
        sd.scorers(g_n, "ok", utc(2026, 11, 6, 9), [("INFANTIL, NINO", "N-1", t_n["INFANTIL FS"], 8, 5)])
        # --- clasificación oficial del grupo senior
        snap = StandingsSnapshot(group_id=g_s2.id, round_number=3, status="ok", content_hash=_ext(), row_count=4, source_url="x",
                                 fetched_at=utc(2026, 11, 8, 11))
        s.add(snap)
        s.flush()
        for pos, (team, pts) in enumerate([(pb, 7), (pa, 4), (mo, 4), (pl, 0)], 1):
            s.add(StandingsRow(snapshot_id=snap.id, position=pos, team_external_id=team.external_id, team_id=team.id, team_name=team.name,
                               played=3, won=0, drawn=0, lost=0, goals_for=0, goals_against=0, points=pts))
        s.add(DatasetRefresh(group_id=g_s2.id, dataset="matches", last_attempt_at=utc(2026, 11, 8, 6), last_attempt_status="ok",
                             last_valid_at=utc(2026, 11, 8, 6)))
        # --- un club con código oficial (Getafe juvenil)
        club = Club(source="rffm", external_code="C-1", name="GETAFE JUVENIL CLUB", normalized_name="getafe juvenil club")
        s.add(club)
        s.flush()
        ent = TeamEntity(source="rffm", external_code="E-1", club_id=club.id, club_link_status="official", name=ge.name)
        s.add(ent)
        s.flush()
        ge.team_entity_id = ent.id
        s.flush()
        w.update(season=season_id, c_s2=c_s2.id, c_j=c_j.id, c_u=c_u.id, c_n=c_n.id, g_s2=g_s2.id, g_j=g_j.id, base=base.id,
                 cad=[g.id for g in cad], teams={"pa": pa.id, "pb": pb.id, "mo": mo.id, "pl": pl.id, "pj": pj.id, "ap": ap.id, "ge": ge.id},
                 players={"quintanar": quintanar.id, "munoz": munoz.id, "juan_a": juan_a.id, "juan_b": juan_b.id, "sergio": sergio.id},
                 m1=m1.id, club=club.id)
    w["client"] = TestClient(create_app(engine, today=lambda: TODAY))
    w["engine"] = engine
    yield w


def get(w: dict[str, Any], url: str, **params: Any) -> dict[str, Any]:
    r = w["client"].get(url, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert {"items", "count", "shown", "notes", "freshness"} <= set(body)
    assert body["shown"] == len(body["items"]) and isinstance(body["notes"], list)
    return body


# ----------------------------------------------------------------------------- 1. jugadores
def test_player_found_in_reverse_order_without_accents_with_stats(world: dict[str, Any]) -> None:
    body = get(world, "/v2/players", name="Pablo quintanar")
    assert body["count"] == 1 and body["shown"] == 1
    item = body["items"][0]
    assert item["ref"] == f"player:{world['players']['quintanar']}" and item["name"] == "QUINTANAR PEREZ, PABLO"
    assert item["team"]["name"] == "MOSTOLES FS" and item["competition"]["ref"] == f"comp:{world['c_s2']}"
    assert item["group"]["ref"] == f"group:{world['g_s2']}"
    assert item["matches_in_reports"] == 2 and item["goals_in_reports"] == 2
    assert item["official"] == {"status": "listed", "goals": 5, "matches": 6}  # la tabla ANTIGUA (3 goles) y la incompleta no cuentan
    assert body["freshness"] == "2026-11-08T10:00:00+00:00"
    assert body["season"]["ref"] == f"season:{world['season']}"
    accents = get(world, "/v2/players", name="alvaro MUNOZ")["items"]
    assert [i["name"] for i in accents] == ["MUÑOZ GÓMEZ, ÁLVARO"] and accents[0]["official"]["status"] == "not_listed"
    assert get(world, "/v2/players", name="gómez  muñoz")["count"] == 1


def test_unknown_player_gets_suggestions_never_as_match(world: dict[str, Any]) -> None:
    body = get(world, "/v2/players", name="Pablo Kintanar")
    assert body["items"] == [] and body["count"] == 0
    assert body["suggestions"][0]["name"] == "QUINTANAR PEREZ, PABLO" and 0.3 <= body["suggestions"][0]["similarity"] <= 1
    assert body["suggestions"][0]["ref"].startswith("player:") and len(body["suggestions"]) <= 5
    assert any("no figura; nombres parecidos: QUINTANAR PEREZ, PABLO" in n for n in body["notes"])
    far = get(world, "/v2/players", name="Zzyzx Qwerty")
    assert far["items"] == [] and far["suggestions"] == [] and any("no figura" in n for n in far["notes"])
    partial = get(world, "/v2/players", name="Pablo Perez Martinez")  # un token de más: no es coincidencia exacta
    assert partial["items"] == [] and partial["suggestions"]


def test_homonyms_in_two_teams_and_limit(world: dict[str, Any]) -> None:
    body = get(world, "/v2/players", name="juan garcia lopez")
    assert body["count"] == 2 and {i["ref"] for i in body["items"]} == {f"player:{world['players']['juan_a']}", f"player:{world['players']['juan_b']}"}
    assert {i["team"]["name"] for i in body["items"]} == {"MOSTOLES FS", "ALCORCON JUVENIL"}
    assert {i["competition"]["name"] for i in body["items"]} == {"SEGUNDA AUTONOMICA AFICIONADO FS", "LIGA NACIONAL JUVENIL FS"}
    one = get(world, "/v2/players", name="juan garcia lopez", limit=1)
    assert one["count"] == 2 and one["shown"] == 1
    assert get(world, "/v2/players", name="juan garcia lopez", category="juvenil")["count"] == 1
    assert get(world, "/v2/players", name="juan garcia lopez", competition=f"comp:{world['c_s2']}")["count"] == 1
    assert get(world, "/v2/players", name="juan garcia lopez", competition="segunda autonomica")["count"] == 1


# ----------------------------------------------------------------------------- 2. árbitros
def test_official_with_matches_in_two_competitions(world: dict[str, Any]) -> None:
    body = get(world, "/v2/officials", name="antonio RAMOS")
    assert body["count"] == 1
    it = body["items"][0]
    assert it["name"] == "RAMOS PEREZ, ANTONIO" and it["matches"] == 3 and it["roles"] == {"ARBITRO": 3}
    assert {c["name"]: c["matches"] for c in it["competitions"]} == {"SEGUNDA AUTONOMICA AFICIONADO FS": 2, "LIGA NACIONAL JUVENIL FS": 1}
    assert len(it["groups"]) == 2 and "matches_list" not in it
    detail = get(world, "/v2/officials", name="antonio ramos", include="matches")["items"][0]["matches_list"]
    assert [(m["date"], m["time"], m["home"], m["away"]) for m in detail] == [
        ("2026-10-24", "12:30", "C.F.S. PINTO A", "MOSTOLES FS"), ("2026-10-24", "12:30", "PINTO C.F.S.", "ATLETICO PINTO F.S."),
        ("2026-10-24", "18:00", "MOSTOLES FS", "PARLA FS")]
    assert {m["score"]["home"] for m in detail} == {1, 2, 3} and all(m["ref"].startswith("match:") and m["competition"]["ref"] for m in detail)
    assert get(world, "/v2/officials", name="lopez marta")["items"][0]["roles"] == {"ARBITRO ASISTENTE": 1}
    imported = get(world, "/v2/officials", name="Álvaro garcia lasa")["items"][0]  # acta importada de la fixture
    assert imported["matches"] == 1 and imported["roles"] == {"ARBITRO": 1}
    assert body["freshness"] == "2026-11-03T10:00:00+00:00"


def test_unknown_official_is_not_returned_as_match(world: dict[str, Any]) -> None:
    body = get(world, "/v2/officials", name="Antonio Ramos Pereira")
    assert body["items"] == [] and body["count"] == 0
    assert body["suggestions"][0]["name"] == "RAMOS PEREZ, ANTONIO" and body["suggestions"][0]["similarity"] >= 0.3
    assert any("no figura; nombres parecidos: RAMOS PEREZ, ANTONIO" in n for n in body["notes"])
    assert get(world, "/v2/officials", name="Qwxz Plmk")["suggestions"] == []


def test_officials_ranking(world: dict[str, Any]) -> None:
    body = get(world, "/v2/officials/ranking")
    assert [(i["rank"], i["name"], i["matches"]) for i in body["items"]][:4] == [
        (1, "RAMOS PEREZ, ANTONIO", 3), (2, "GARCIA LASA, ALVARO", 1), (3, "GARVALENA CRESPO, MARTA", 1), (4, "SANZ MORA, LUIS", 1)]
    assert body["count"] == 4 and all(i["roles"] == ["ARBITRO"] for i in body["items"])
    juvenil = get(world, "/v2/officials/ranking", category="juvenil", limit=1)
    assert juvenil["count"] == 2 and juvenil["shown"] == 1 and juvenil["items"][0]["name"] == "RAMOS PEREZ, ANTONIO"
    assist = get(world, "/v2/officials/ranking", role="arbitro asistente")
    assert {i["name"] for i in assist["items"]} == {"LOPEZ DIAZ, MARTA", "ARJONA ESCALONA, MANUEL JOSE"}


# ----------------------------------------------------------------------------- 3. partidos
def test_matches_same_day_in_three_groups_with_time_band_and_dst(world: dict[str, Any]) -> None:
    day = get(world, "/v2/matches", category="cadete", date_from="2026-10-24", date_to="2026-10-24")
    assert day["count"] == 3 and [m["time"] for m in day["items"]] == ["10:00", "12:30", "18:00"]
    assert len({m["group"]["ref"] for m in day["items"]}) == 3 and len({m["competition"]["ref"] for m in day["items"]}) == 3
    assert {m["local_datetime"][-6:] for m in day["items"]} == {"+02:00"}
    assert all(set(m) >= {"ref", "date", "time", "competition", "group", "home", "away", "score", "status", "has_report"} for m in day["items"])
    band = get(world, "/v2/matches", category="cadete", date_from="2026-10-24", date_to="2026-10-25", time_from="11:00", time_to="13:00")
    assert [m["time"] for m in band["items"]] == ["12:30"] and any("hora conocida" in n for n in band["notes"])
    after_dst = get(world, "/v2/matches", category="cadete", date_from="2026-10-25", date_to="2026-10-25")
    assert [(m["time"], m["local_datetime"][-6:]) for m in after_dst["items"]] == [("10:00", "+01:00")]
    spring = get(world, "/v2/matches", category="cadete", date_from="2027-03-28", date_to="2027-03-28")
    assert [(m["time"], m["local_datetime"][-6:], m["status"], m["score"]) for m in spring["items"]] == [("18:00", "+02:00", "scheduled", None)]
    with world["engine"].connect() as c:  # en BD está en UTC: 10:00 local de octubre = 08:00Z, de la semana del cambio = 09:00Z, marzo 18:00 = 16:00Z
        stored = sorted(r[0] for r in c.execute(text(
            "SELECT to_char(scheduled_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') FROM matches WHERE group_id = ANY(:g) ORDER BY 1"),
            {"g": world["cad"]}))
    assert "2026-10-24 08:00" in stored and "2026-10-25 09:00" in stored and "2027-03-28 16:00" in stored


def test_matches_midnight_boundary_uses_madrid_dates(world: dict[str, Any]) -> None:
    # 23:30 del 24 (21:30Z) y 00:30 del 25 (22:30Z del 24) son días locales distintos aunque ambos sean 24 en UTC
    on24 = get(world, "/v2/matches", category="juvenil", date_from="2026-10-24", date_to="2026-10-24")
    on25 = get(world, "/v2/matches", category="juvenil", date_from="2026-10-25", date_to="2026-10-25")
    assert [m["time"] for m in on24["items"]] == ["12:30", "23:30"] and [m["time"] for m in on25["items"]] == ["00:30"]
    late = get(world, "/v2/matches", category="juvenil", date_from="2026-10-24", date_to="2026-10-25", time_from="23:00", order="desc")
    assert [m["time"] for m in late["items"]] == ["23:30"]
    desc = get(world, "/v2/matches", category="juvenil", date_from="2026-10-24", date_to="2026-10-25", order="desc")
    assert [m["time"] for m in desc["items"]] == ["00:30", "23:30", "12:30"]


def test_matches_filters_status_venue_report_and_freshness(world: dict[str, Any]) -> None:
    s2 = f"comp:{world['c_s2']}"
    allm = get(world, "/v2/matches", competition=s2)
    assert allm["count"] == 5 and allm["freshness"] == "2026-11-08T06:00:00+00:00"
    assert get(world, "/v2/matches", competition=s2, status="scheduled")["count"] == 1
    assert get(world, "/v2/matches", competition=s2, status="finished", limit=2)["shown"] == 2
    venue = get(world, "/v2/matches", competition=s2, venue="estacion")
    assert venue["count"] == 1 and venue["items"][0]["has_report"] is True and venue["items"][0]["score"] == {"home": 3, "away": 2}
    assert sum(m["has_report"] for m in allm["items"]) == 2
    assert get(world, "/v2/matches", competition=s2, status="finished", date_from="2026-10-25")["count"] == 1
    # competición por nombre y por ámbito de temporada
    assert get(world, "/v2/matches", competition="segunda autonomica")["count"] == 5
    assert get(world, "/v2/matches", season=f"season:{world['season']}", competition=s2)["count"] == 5
    none = get(world, "/v2/matches", season="season:99999")
    assert none["items"] == [] and any("no encontrada" in n for n in none["notes"])
    default = get(world, "/v2/matches", competition=s2)
    assert any("temporada por defecto" in n for n in default["notes"])


# ----------------------------------------------------------------------------- 4 y 5. clubes
def test_club_search_groups_squads_and_flags_name_match(world: dict[str, Any]) -> None:
    body = get(world, "/v2/clubs/search", name="Club Pinto")
    assert body["count"] == 2 and [i["base_name"] for i in body["items"]] == ["pinto", "atletico pinto"]
    pinto, atletico = body["items"]
    assert pinto["exact"] is True and pinto["club_link"] == "name_match" and pinto["club_ref"] is None
    assert pinto["ambiguous"] is True and atletico["ambiguous"] is True
    assert {(t["name"], t["squad"], t["category"]) for t in pinto["teams"]} == {
        ("C.F.S. PINTO A", "A", "senior"), ("C.F.S. PINTO B", "B", "senior"), ("PINTO C.F.S.", None, "juvenil")}
    by_name = {t["name"]: t for t in pinto["teams"]}
    assert by_name["C.F.S. PINTO B"]["standing"] == {"position": 1, "points": 7, "played": 3, "official": True}
    assert by_name["C.F.S. PINTO A"]["standing"]["position"] == 2 and by_name["PINTO C.F.S."]["standing"] is None
    assert len({t["competition"]["ref"] for t in pinto["teams"]}) == 2
    assert any("agrupado por nombre; pendiente de confirmar con el código oficial de club" in n for n in body["notes"])
    assert any("nombre distinto" in n for n in body["notes"])
    assert body["freshness"] == "2026-11-08T11:00:00+00:00"
    single = get(world, "/v2/clubs/search", name="atletico pinto")
    assert single["count"] == 1 and single["items"][0]["ambiguous"] is False and single["items"][0]["club_link"] == "name_match"
    seniors = get(world, "/v2/clubs/search", name="pinto", category="senior")
    assert seniors["count"] == 1 and seniors["items"][0]["teams_count"] == 2
    assert get(world, "/v2/clubs/search", name="pinto", limit=1)["shown"] == 1
    assert get(world, "/v2/clubs/search", name="nadaquever")["items"] == []


def test_club_with_official_code_is_marked_official(world: dict[str, Any]) -> None:
    body = get(world, "/v2/clubs/search", name="getafe")
    assert body["count"] == 1 and body["items"][0]["club_link"] == "official" and body["items"][0]["club_ref"] == f"club:{world['club']}"
    assert body["items"][0]["ambiguous"] is False and not any("pendiente de confirmar" in n for n in body["notes"])


def test_club_results_by_team_with_summary(world: dict[str, Any]) -> None:
    body = get(world, "/v2/clubs/results", name="pinto")
    assert body["count"] == 4
    teams = {i["team"]["name"]: i for i in body["items"]}
    assert teams["C.F.S. PINTO A"]["summary"] == {"played": 2, "won": 1, "drawn": 1, "lost": 0, "goals_for": 6, "goals_against": 3}
    assert teams["C.F.S. PINTO B"]["summary"] == {"played": 2, "won": 1, "drawn": 0, "lost": 1, "goals_for": 6, "goals_against": 5}
    assert teams["PINTO C.F.S."]["summary"] == {"played": 2, "won": 1, "drawn": 1, "lost": 0, "goals_for": 5, "goals_against": 3}
    assert teams["ATLETICO PINTO F.S."]["summary"]["played"] == 1
    a = teams["C.F.S. PINTO A"]
    assert a["competition"]["name"] == "SEGUNDA AUTONOMICA AFICIONADO FS" and a["club"]["club_link"] == "name_match"
    assert [(r["date"], r["result"], r["venue_side"]) for r in a["results"]] == [("2026-10-24", "E", "home"), ("2026-10-24", "G", "away")]
    assert a["results_count"] == a["results_shown"] == 2 and any("pendiente de confirmar" in n for n in body["notes"])
    assert get(world, "/v2/clubs/results", name="pinto", category="juvenil")["count"] == 2
    assert get(world, "/v2/clubs/results", name="pinto", limit=3)["shown"] == 3


# ----------------------------------------------------------------------------- 6. goleadores
def test_scorers_ranking_senior_across_groups_uses_latest_ok_table(world: dict[str, Any]) -> None:
    body = get(world, "/v2/rankings/scorers", category="senior")
    assert [(i["rank"], i["player"]["name"], i["goals"]) for i in body["items"]] == [
        (1, "PRIMERO, GOLEADOR", 9), (2, "ZAMORA VEGA, IVAN", 7), (3, "ABAD RUIZ, LUIS", 5), (4, "QUINTANAR PEREZ, PABLO", 5), (5, "SEGUNDO, GOLEADOR", 4)]
    assert body["count"] == 5 and {i["competition"]["name"] for i in body["items"]} == {
        "PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "SEGUNDA AUTONOMICA AFICIONADO FS"}
    quintanar = body["items"][3]
    assert quintanar["player"]["ref"] == f"player:{world['players']['quintanar']}" and quintanar["team"]["ref"] == f"team:{world['teams']['mo']}"
    assert quintanar["group"]["ref"] == f"group:{world['g_s2']}" and quintanar["matches"] == 6
    assert any("compara grupos distintos con diferente número de jornadas jugadas" in n for n in body["notes"])
    assert body["freshness"] == "2026-11-08T10:00:00+00:00"
    assert all(i["player"]["name"] != "FALSO, JUGADOR" for i in body["items"])


def test_scorers_ranking_juvenil_filter_and_unknown_category_excluded(world: dict[str, Any]) -> None:
    juvenil = get(world, "/v2/rankings/scorers", category="juvenil")
    assert [i["player"]["name"] for i in juvenil["items"]] == ["JIMENEZ RUIZ, SERGIO", "OTRO, JUGADOR"] and juvenil["freshness"] == "2026-11-08T12:00:00+00:00"
    everything = get(world, "/v2/rankings/scorers", limit=100)
    names = [i["player"]["name"] for i in everything["items"]]
    assert "SIN CATEGORIA, JUGADOR" not in names and names[0] == "JIMENEZ RUIZ, SERGIO" and "INFANTIL, NINO" in names
    assert any("categoría unknown" in n and "TORNEO SIN CLASIFICAR FS" in n for n in everything["notes"])
    assert any("clasificado" in n and "por su nombre" in n for n in everything["notes"])
    infantil = get(world, "/v2/rankings/scorers", category="infantil")
    assert [i["player"]["name"] for i in infantil["items"]] == ["INFANTIL, NINO"] and any("por su nombre" in n for n in infantil["notes"])
    assert get(world, "/v2/rankings/scorers", category="senior", gender="female")["items"] == []
    assert get(world, "/v2/rankings/scorers", category="juvenil", gender="masculino")["count"] == 2
    assert get(world, "/v2/rankings/scorers", competition_kind="cup")["items"] == []
    limited = get(world, "/v2/rankings/scorers", category="senior", limit=2)
    assert limited["count"] == 5 and limited["shown"] == 2
    assert any("grupos del ámbito tienen tabla oficial" in n for n in limited["notes"])


# ----------------------------------------------------------------------------- 7. equipos
def test_team_rankings_deterministic(world: dict[str, Any]) -> None:
    comp = f"comp:{world['c_s2']}"

    def rank(metric: str, **extra: Any) -> list[str]:
        return [i["team"]["name"] for i in get(world, "/v2/rankings/teams", metric=metric, competition=comp, **extra)["items"]]

    assert rank("goals_for") == ["C.F.S. PINTO A", "C.F.S. PINTO B", "MOSTOLES FS", "PARLA FS"]
    assert rank("goals_against") == ["C.F.S. PINTO A", "MOSTOLES FS", "C.F.S. PINTO B", "PARLA FS"]
    assert rank("goals_against", order="desc") == ["PARLA FS", "C.F.S. PINTO B", "C.F.S. PINTO A", "MOSTOLES FS"]
    assert rank("wins") == ["C.F.S. PINTO A", "C.F.S. PINTO B", "MOSTOLES FS", "PARLA FS"]
    assert rank("points_per_game") == ["C.F.S. PINTO A", "MOSTOLES FS", "C.F.S. PINTO B", "PARLA FS"]
    body = get(world, "/v2/rankings/teams", metric="points_per_game", competition=comp)
    first = body["items"][0]
    assert first["rank"] == 1 and first["points_per_game"] == 2.0 and (first["played"], first["won"], first["drawn"], first["lost"]) == (2, 1, 1, 0)
    assert (first["goals_for"], first["goals_against"]) == (6, 3) and first["group"]["ref"] == f"group:{world['g_s2']}"
    assert any("compara grupos distintos con diferente número de jornadas jugadas" in n for n in body["notes"])
    assert body["metric"] == "points_per_game" and body["freshness"] == "2026-11-08T06:00:00+00:00" and body["count"] == 4
    assert get(world, "/v2/rankings/teams", metric="wins", competition=comp, min_played=3)["items"] == []
    top = get(world, "/v2/rankings/teams", metric="goals_for", category="senior", limit=3)
    assert top["shown"] == 3 and top["count"] > 4 and top["items"][0]["goals_for"] >= top["items"][2]["goals_for"]
    every = get(world, "/v2/rankings/teams", metric="wins", limit=100)
    assert any("categoría unknown" in n for n in every["notes"]) and all(i["competition"]["name"] != "TORNEO SIN CLASIFICAR FS" for i in every["items"])


# ----------------------------------------------------------------------------- límites, 422 y nombres raros
@pytest.mark.parametrize("url", [
    "/v2/players?name=ab&limit=0", "/v2/players?name=ab&limit=101", "/v2/players?name=x", "/v2/players?name=@@@",
    "/v2/players?name=ab&category=bogus", "/v2/players?name=ab&gender=bogus", "/v2/officials?name=%40%40", "/v2/officials?name=ab&include=x",
    "/v2/matches?date_from=2026-10-25&date_to=2026-10-24", "/v2/matches?time_from=18:00&time_to=10:00", "/v2/matches?status=bogus",
    "/v2/matches?order=sideways", "/v2/matches?date_from=nope", "/v2/matches?venue=%2F%2F", "/v2/clubs/search?name=%25%25",
    "/v2/rankings/teams?metric=bogus", "/v2/rankings/teams?metric=wins&order=up", "/v2/rankings/scorers?competition_kind=bogus",
    "/v2/rankings/scorers?category=bogus", "/v2/officials/ranking?role=%25%25", "/v2/players?name=ab&competition=%25%25",
])
def test_invalid_parameters_are_422(world: dict[str, Any], url: str) -> None:
    r = world["client"].get(url)
    assert r.status_code == 422, (url, r.text)


@pytest.mark.parametrize("name", [
    "'; DROP TABLE players;--", "%_\\", "Ünï\"çödé 'ñ'", "O'Brien-Núñez", "a b c d e f g h i j", "x" * 80, "漢字 kanji", "NULL", "\t\n tab", "\x00\x01 ab",
    "Ωmega Ø", "p%ablo", "quintanar\\", "😀 pablo",
])
def test_strange_names_never_500(world: dict[str, Any], name: str) -> None:
    for path in ("/v2/players", "/v2/officials", "/v2/clubs/search", "/v2/clubs/results"):
        r = world["client"].get(path, params={"name": name})
        assert r.status_code in (200, 422), (path, name, r.status_code, r.text)
        if r.status_code == 200:
            assert r.json()["count"] >= 0
    assert world["client"].get("/health").status_code == 200  # la tabla sigue ahí
    assert get(world, "/v2/players", name="Pablo quintanar")["count"] == 1


def test_v1_untouched_and_v2_empty_database_scope(world: dict[str, Any]) -> None:
    assert world["client"].get("/competitions").status_code == 200
    assert world["client"].get("/groups/999999/matches").status_code == 404
    assert world["client"].get("/groups/1/matches?status=bogus").status_code == 400  # v1 sigue devolviendo 400
    ping = get(world, "/v2/officials/ranking", competition="no-existe-ninguna")
    assert ping["items"] == [] and any("no encontrada" in n for n in ping["notes"])


def test_json_roundtrip_and_no_external_ids(world: dict[str, Any]) -> None:
    """Las respuestas solo llevan referencias opacas: ningún campo `external_id` ni IDs de la RFFM."""
    for url, params in [("/v2/players", {"name": "pablo quintanar"}), ("/v2/matches", {"limit": 5}), ("/v2/clubs/search", {"name": "pinto"}),
                        ("/v2/rankings/scorers", {}), ("/v2/rankings/teams", {"metric": "wins"})]:
        text_body = json.dumps(get(world, url, **params))
        assert "external" not in text_body and "26738243" not in text_body and "5575697" not in text_body
