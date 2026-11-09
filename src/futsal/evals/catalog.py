"""Catálogo de entidades del snapshot: toda la verdad sale de consultas SQL con nombre (`truth/*.sql`), nunca de un LLM.

Una conexión de solo lectura carga el catálogo completo; las entidades (equipo, grupo, competición, club, partido) se
construyen a partir de él como diccionarios JSON-serializables que las plantillas de los casos referencian (`{team.points}`).
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from futsal.agent.evaluate import norm

TRUTH_DIR = Path(__file__).parent / "truth"
_SUFFIX = re.compile(r"[\s\-]*[('\"]?\s*\b(?:[ABC]|II|2)\b\s*[)'\"]?\s*$", re.IGNORECASE)


def sql_text(name: str) -> str:
    """SQL con nombre. Solo SELECT/WITH: el catálogo nunca escribe."""
    body = (TRUTH_DIR / f"{name}.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("--")).strip().lower()
    if not code.startswith(("select", "with")):
        raise ValueError(f"truth/{name}.sql no es una consulta de solo lectura")
    return body


def named_query(conn: Any, name: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql_text(name)), params).mappings()]


def categorize(competition: str) -> str:
    """Categoría deportiva a partir del NOMBRE de la competición (regla fija y documentada)."""
    n = norm(competition)
    for word, label in (("femenin", "femenino"), ("cadete", "cadete"), ("juvenil", "juvenil")):
        if word in n:
            return label
    return "senior"


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm(name))


def base_name(name: str) -> str:
    """Nombre sin el sufijo de equipo B/A/II (el «club»): «PARQUE NORTE 'B'» -> «PARQUE NORTE»."""
    out = _SUFFIX.sub("", name.strip())
    return out or name.strip()


def _iso(day: Any) -> str | None:
    return day.isoformat() if isinstance(day, date) else None


@dataclass
class Catalog:
    groups: list[dict[str, Any]]
    teams: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    standings: dict[int, list[dict[str, Any]]]
    scorers: dict[int, list[dict[str, Any]]]
    goals: dict[int, list[dict[str, Any]]]
    kb_docs: frozenset[str]
    player_names: list[str]
    seed: str = ""
    as_of: datetime | None = None
    _g: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _by_team: dict[int, list[dict[str, Any]]] = field(default_factory=dict, repr=False)
    _by_key: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._g = {g["group_id"]: g for g in self.groups}
        for g in self.groups:
            g["category"] = categorize(g["competition"])
        self._by_team = defaultdict(list)
        for m in self.matches:
            m["day"] = _iso(m["day"])
            self._by_team[m["home_team_id"]].append(m)
            self._by_team[m["away_team_id"]].append(m)
        self._by_key = defaultdict(list)
        for t in self.teams:
            t["key"], t["base_key"] = name_key(t["name"]), name_key(base_name(t["name"]))
            self._by_key[t["key"]].append(t)

    # ------------------------------------------------------------------ carga
    @classmethod
    def load(cls, engine: Engine, seed: str = "", as_of: datetime | None = None) -> "Catalog":
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            groups = named_query(conn, "groups")
            teams = named_query(conn, "teams")
            matches = named_query(conn, "matches")
            standings: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for r in named_query(conn, "standings_latest"):
                standings[r["group_id"]].append(r)
            scorers: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for r in named_query(conn, "scorers_latest"):
                scorers[r["group_id"]].append(r)
            goals: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for r in named_query(conn, "report_goals"):
                goals[r["match_id"]].append(r)
            try:
                kb = frozenset(str(r["doc_key"]) for r in named_query(conn, "kb_docs"))
            except SQLAlchemyError:
                conn.rollback()
                kb = frozenset()
            players = [str(r["display_name"]) for r in named_query(conn, "players")]
        return cls(groups, teams, matches, dict(standings), dict(scorers), dict(goals), kb, players, seed, as_of)

    # ------------------------------------------------------------------ capacidades
    def capabilities(self) -> set[str]:
        caps: set[str] = set()
        if any(g["has_standings"] for g in self.groups):
            caps.add("standings")
        if any(g["has_scorers"] for g in self.groups):
            caps.add("scorers")
        if any(g["reports"] for g in self.groups):
            caps.add("reports")
        if any(g["finished"] for g in self.groups):
            caps.add("results")
        if any(g["pending"] for g in self.groups):
            caps.add("calendar")
        if len({g["season"] for g in self.groups}) >= 2:
            caps.add("multi_season")
        if len({g["category"] for g in self.groups}) >= 2:
            caps.add("multi_category")
        if len(self.groups) >= 2:
            caps.add("multi_group")
        if any(len(v) >= 2 for v in self._by_key.values()):
            caps.add("homonyms")
        if any(m["status"] == "finished" and not m["has_report"] for m in self.matches):
            caps.add("finished_without_report")
        if any(not g["has_standings"] and g["teams"] for g in self.groups):
            caps.add("group_without_standings")
        if self.kb_docs:
            caps.add("kb")
            caps |= {f"kb:{d}" for d in self.kb_docs}
        return caps

    def known_names(self) -> list[str]:
        """Nombres propios del snapshot (equipos, competiciones, grupos y jugadores): el catálogo con el que se reconocen nombres en las respuestas."""
        names = {t["name"] for t in self.teams} | {g["competition"] for g in self.groups} | {g["group_name"] for g in self.groups}
        return sorted(names | set(self.player_names))

    def seasons(self) -> list[str]:
        return sorted({g["season"] for g in self.groups})

    # ------------------------------------------------------------------ vistas
    def group(self, group_id: int) -> dict[str, Any]:
        return self._g[group_id]

    def team_matches(self, team_id: int) -> list[dict[str, Any]]:
        return self._by_team.get(team_id, [])

    def standing_row(self, team: dict[str, Any]) -> dict[str, Any] | None:
        return next((r for r in self.standings.get(team["group_id"], []) if r["team_id"] == team["team_id"]), None)

    def homonym_rows(self, team: dict[str, Any]) -> list[dict[str, Any]]:
        return [t for t in self._by_key.get(team["key"], []) if t["team_id"] != team["team_id"] and t["group_id"] != team["group_id"]]

    def club_mate_rows(self, team: dict[str, Any]) -> list[dict[str, Any]]:
        g = self.group(team["group_id"])
        return [t for t in self.teams if t["base_key"] == team["base_key"] and t["key"] != team["key"]
                and self.group(t["group_id"])["competition_id"] == g["competition_id"]]

    def has_flags(self, team: dict[str, Any]) -> set[str]:
        """Propiedades de un equipo que usan los filtros `with` de los casos."""
        flags: set[str] = set()
        ms = self.team_matches(team["team_id"])
        fin = [m for m in ms if m["status"] == "finished"]
        if self.standing_row(team):
            flags.add("standings")
        if fin:
            flags.add("finished")
        if any(m["status"] != "finished" for m in ms):
            flags.add("pending")
        if any(m["away_team_id"] == team["team_id"] for m in fin):
            flags.add("away_played")
        if any(m["has_report"] for m in fin):
            flags.add("reports")
        if len(fin) >= 2:
            flags.add("finished2")
        if len(fin) >= 3:
            flags.add("finished3")
        if len(fin) >= 5:
            flags.add("finished5")
        if any(s["team_id"] == team["team_id"] for s in self.scorers.get(team["group_id"], [])):
            flags.add("scorers")
        return flags

    @staticmethod
    def match_view(m: dict[str, Any], team_id: int | None, names: dict[int, str] | None = None) -> dict[str, Any]:
        home = team_id is None or m["home_team_id"] == team_id
        rival = m["away"] if home else m["home"]
        hs, aws = m["home_score"], m["away_score"]
        view: dict[str, Any] = {"match_id": m["match_id"], "round": m["round"], "date": m["day"], "venue": m["venue"],
                                "home": m["home"], "away": m["away"], "rival": rival, "is_home": home,
                                "status": m["status"], "has_report": m["has_report"]}
        if hs is not None and aws is not None:
            gf, ga = (hs, aws) if home else (aws, hs)
            view.update({"score": f"{hs}-{aws}", "gf": gf, "ga": ga,
                         "result": "ganó" if gf > ga else "perdió" if gf < ga else "empató"})
        return view

    def team_entity(self, team: dict[str, Any], decoys: bool = True) -> dict[str, Any]:
        g = self.group(team["group_id"])
        tid = team["team_id"]
        ent: dict[str, Any] = {
            "kind": "team", "id": tid, "name": team["name"], "base_name": base_name(team["name"]), "group_id": g["group_id"],
            "group": g["group_name"], "competition": g["competition"], "category": g["category"], "season": g["season"]}
        st = self.standing_row(team)
        if st:
            ent.update({"points": st["points"], "position": st["position"], "played": st["played"], "won": st["won"],
                        "drawn": st["drawn"], "lost": st["lost"], "gf": st["goals_for"], "ga": st["goals_against"],
                        "gd": st["goals_for"] - st["goals_against"], "standings_round": st["round_number"]})
        ms = self.team_matches(tid)
        fin = sorted((m for m in ms if m["status"] == "finished"), key=lambda m: (m["round"], m["day"] or "", m["match_id"]))
        pend = sorted((m for m in ms if m["status"] != "finished"), key=lambda m: (m["round"], m["day"] or "", m["match_id"]))
        views = [self.match_view(m, tid) for m in fin]
        ent["finished"], ent["pending"] = len(fin), len(pend)
        if views:
            ent["last_match"] = views[-1]
            if len(views) >= 2:
                ent["prev_match"] = views[-2]
            ent["recent"] = views[-5:]
            ent["home_wins"] = sum(v["is_home"] and v["result"] == "ganó" for v in views)
            ent["away_wins"] = sum((not v["is_home"]) and v["result"] == "ganó" for v in views)
            ent["goals_for"] = sum(v["gf"] for v in views)
            ent["goals_against"] = sum(v["ga"] for v in views)
            ent["goals_per_match"] = f"{ent['goals_for'] / len(views):.2f}".replace(".", ",")
            ent["form"] = "".join({"ganó": "G", "empató": "E", "perdió": "P"}[v["result"]] for v in views[-5:])
        if pend:
            ent["next_match"] = self.match_view(pend[0], tid)
        sc = [s for s in self.scorers.get(g["group_id"], []) if s["team_id"] == tid]
        if sc:
            ent["top_scorer"] = {"name": sc[0]["player_name"], "goals": sc[0]["goals"]}
        for v in reversed(views):
            if v["has_report"]:
                ent["reported_match"] = {**v, "goals": [{"player": e["player_name"], "minute": e["minute"]}
                                                       for e in self.goals.get(v["match_id"], []) if e["event_type"] == "goal"]}
                break
        if decoys:
            hom = [self.team_entity(t, decoys=False) for t in self.homonym_rows(team)]
            mates = [self.team_entity(t, decoys=False) for t in self.club_mate_rows(team)]
            ent["homonyms"], ent["club_mates"], ent["decoys"] = hom, mates, [*hom, *mates]
        return ent

    def group_entity(self, group: dict[str, Any], decoys: bool = True) -> dict[str, Any]:
        gid = group["group_id"]
        ent: dict[str, Any] = {"kind": "group", "id": gid, "group_id": gid, "name": group["group_name"], "group": group["group_name"],
                               "competition": group["competition"], "category": group["category"], "season": group["season"],
                               "teams": group["teams"], "finished": group["finished"], "pending": group["pending"]}
        rows = self.standings.get(gid, [])
        if rows:
            ent["standings_round"] = rows[0]["round_number"]
            ent["leader"] = {"name": rows[0]["team_name"], "points": rows[0]["points"], "played": rows[0]["played"]}
            if len(rows) >= 3:
                ent["top3"] = [r["team_name"] for r in rows[:3]]
                ent["top3_gd"] = sum(r["goals_for"] - r["goals_against"] for r in rows[:3])
            ent["last_place"] = {"name": rows[-1]["team_name"], "points": rows[-1]["points"]}
            ent["gap_first_last"] = rows[0]["points"] - rows[-1]["points"]
            if len(rows) >= 2:
                ent["second"] = {"name": rows[1]["team_name"], "points": rows[1]["points"]}
        sc = self.scorers.get(gid, [])
        if sc:
            ent["top_scorer"] = {"name": sc[0]["player_name"], "team": sc[0]["team_name"], "goals": sc[0]["goals"]}
        ms = [m for m in self.matches if m["group_id"] == gid]
        fin = [m for m in ms if m["status"] == "finished"]
        if fin:
            last_round = max(m["round"] for m in fin)
            ent["last_round"] = last_round
            ent["round_goals"] = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in fin if m["round"] == last_round)
            ent["round_matches"] = sum(m["round"] == last_round for m in fin)
            ent["goals_total"] = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in fin)
            ent["avg_goals"] = f"{ent['goals_total'] / len(fin):.2f}".replace(".", ",")
        weekend = self.weekend_matches(gid)
        if weekend:
            ent["weekend"] = weekend
        if decoys:
            others = [g for g in self.groups if g["group_id"] != gid and (
                g["competition_id"] == group["competition_id"] or g["group_name"] == group["group_name"])]
            ent["decoys"] = [self.group_entity(g, decoys=False) for g in others[:6]]
        return ent

    def weekend_matches(self, group_id: int) -> list[dict[str, Any]]:
        """Partidos aún no jugados del próximo fin de semana respecto al `as_of` del snapshot (sábado y domingo)."""
        if self.as_of is None:
            return []
        today = self.as_of.date()
        saturday = today - timedelta(days=1) if today.weekday() == 6 else today + timedelta(days=(5 - today.weekday()) % 7)
        days = {saturday.isoformat(), (saturday + timedelta(days=1)).isoformat()}
        return [self.match_view(m, None) for m in self.matches
                if m["group_id"] == group_id and m["status"] != "finished" and m["day"] in days]

    def match_entity(self, m: dict[str, Any]) -> dict[str, Any]:
        g = self.group(m["group_id"])
        ent = {"kind": "match", **self.match_view(m, None), "group": g["group_name"], "competition": g["competition"],
               "category": g["category"], "season": g["season"], "group_id": g["group_id"]}
        ent["goals"] = [{"player": e["player_name"], "minute": e["minute"]} for e in self.goals.get(m["match_id"], [])]
        return ent
