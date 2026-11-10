"""Catálogo de entidades del snapshot: toda la verdad sale de consultas SQL con nombre (`truth/*.sql`), nunca de un LLM.

Una conexión de solo lectura carga el catálogo completo; las entidades (equipo, grupo, competición, club, partido) se
construyen a partir de él como diccionarios JSON-serializables que las plantillas de los casos referencian (`{team.points}`).
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from futsal.agent.evaluate import MONTHS, key, norm

TRUTH_DIR = Path(__file__).parent / "truth"
WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
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


def date_es(day: date) -> str:
    """«sábado 7 de noviembre»."""
    return f"{WEEKDAYS[day.weekday()]} {day.day} de {MONTHS[day.month - 1]}"


def tokens_key(name: str) -> tuple[str, ...]:
    """Palabras de un nombre sin orden, acentos ni mayúsculas (jugadores y árbitros: «SILVOSA PEREZ, PABLO» = «Pablo Silvosa Perez»)."""
    return tuple(sorted(norm(name).split()))


def person_forms(display: str) -> dict[str, Any] | None:
    """Formas de escribir un nombre «APELLIDOS, NOMBRE» (el formato de las actas). None si no trae la coma (no se puede ordenar)."""
    if "," not in display:
        return None
    surnames, first = (x.strip() for x in display.split(",", 1))
    if not surnames or not first:
        return None
    ft, st = first.title(), surnames.title()
    natural = f"{ft} {st}"
    s_tokens = st.split()
    return {"first": ft, "surnames": st, "surname1": s_tokens[0], "surname2": " ".join(s_tokens[1:]), "natural": natural,
            "natural_lower": natural.lower(), "comma": f"{st}, {ft}", "unaccent": _plain(natural),
            "natural_lower_unaccent": _plain(natural).lower(), "first_surname": f"{ft.split()[0]} {s_tokens[0]}",
            "first_surname_lower": f"{ft.split()[0]} {s_tokens[0]}".lower(), "has_accent": _plain(display) != display,
            "second_surname": len(s_tokens) >= 2}


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


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
    player_rows: list[dict[str, Any]] = field(default_factory=list)  # jugador-equipo (truth/player_teams.sql)
    officials: list[dict[str, Any]] = field(default_factory=list)  # oficiales de actas (truth/officials.sql)
    times: dict[int, dict[str, Any]] = field(default_factory=dict)  # hora y franja por partido (truth/match_times.sql)
    scorer_ranking: list[dict[str, Any]] = field(default_factory=list)  # goleadores por categoría (truth/scorers_ranking.sql)
    club_bases: dict[int, str] = field(default_factory=dict)  # equipo -> nombre base del club (truth/club_teams.sql)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)
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
            player_rows = named_query(conn, "player_teams")
            officials = named_query(conn, "officials")
            times = {int(r["match_id"]): r for r in named_query(conn, "match_times")}
            ranking = named_query(conn, "scorers_ranking")
            clubs = {int(r["team_id"]): str(r["club_base"]) for r in named_query(conn, "club_teams")}
        return cls(groups, teams, matches, dict(standings), dict(scorers), dict(goals), kb, players, seed, as_of,
                   player_rows, officials, times, ranking, clubs)

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
        for flag, present in (("players", bool(self.player_rows)), ("officials", bool(self.officials)),
                              ("match_times", any(t["kickoff_time"] for t in self.times.values())),
                              ("scorer_ranking", bool(self.scorer_ranking)),
                              ("postponed", any(m["status"] == "postponed" for m in self.matches)),
                              ("suspended", any(m["status"] == "suspended" for m in self.matches)),
                              ("open_report", any(t["report_closed"] is False for t in self.times.values()))):
            if present:
                caps.add(flag)
        if self.kb_docs:
            caps.add("kb")
            caps |= {f"kb:{d}" for d in self.kb_docs}
        return caps

    def known_names(self) -> list[str]:
        """Nombres propios del snapshot (equipos, competiciones, grupos y jugadores): el catálogo con el que se reconocen nombres en las respuestas."""
        names = {t["name"] for t in self.teams} | {g["competition"] for g in self.groups} | {g["group_name"] for g in self.groups}
        return sorted(names | set(self.player_names) | {str(o["official"]) for o in self.officials})

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
        fin = sorted((m for m in ms if m["status"] == "finished"), key=lambda m: (m["round"] or 0, m["day"] or "", m["match_id"]))
        pend = sorted((m for m in ms if m["status"] != "finished"), key=lambda m: (m["round"] or 0, m["day"] or "", m["match_id"]))
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
            ent["next_match"].update(self.kickoff_times(pend[0]["match_id"]))
            ent["next_has_time"] = int("time" in ent["next_match"])
            ent["next_has_venue"] = int(bool(ent["next_match"].get("venue")))
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
        numbered = [m["round"] for m in fin if m["round"] is not None]  # copas y grupos reales: jornada nula
        if numbered:
            last_round = max(numbered)
            ent["last_round"] = last_round
            ent["round_goals"] = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in fin if m["round"] == last_round)
            ent["round_matches"] = sum(m["round"] == last_round for m in fin)
        if fin:
            ent["goals_total"] = sum((m["home_score"] or 0) + (m["away_score"] or 0) for m in fin)
            ent["avg_goals"] = f"{ent['goals_total'] / len(fin):.2f}".replace(".", ",")
        rounds: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for m in ms:
            if m["round"] is not None:
                rounds[m["round"]][m["status"] == "finished"] += 1
        open_rounds = sorted(r for r, (pend, fin) in rounds.items() if pend and fin)
        if open_rounds:  # jornada en curso: con partidos jugados y otros todavía sin jugar
            ent["open_round"] = open_rounds[0]
            ent["open_round_pending"], ent["open_round_finished"] = rounds[open_rounds[0]]
        weekend = self.weekend_matches(gid)
        if weekend:
            ent["weekend"] = weekend
        ent["weekend_n"] = len(weekend)
        ent["weekend_first_timed"] = int(bool(weekend) and "time" in weekend[0])
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
        return [{**self.match_view(m, None), **self.kickoff_times(m["match_id"])} for m in self.matches
                if m["group_id"] == group_id and m["status"] != "finished" and m["day"] in days]

    def kickoff_times(self, match_id: int) -> dict[str, str]:
        """Hora local de Madrid («18:30») y la de Canarias (una hora menos); vacío si el partido no tiene hora."""
        t = self.times.get(match_id, {}).get("kickoff_time")
        if t is None:
            return {}
        canarias = (datetime.combine(date.min, t) - timedelta(hours=1)).time()
        return {"time": t.strftime("%H:%M"), "time_canarias": canarias.strftime("%H:%M")}

    def match_entity(self, m: dict[str, Any]) -> dict[str, Any]:
        g = self.group(m["group_id"])
        ent = {"kind": "match", **self.match_view(m, None), "group": g["group_name"], "competition": g["competition"],
               "category": g["category"], "season": g["season"], "group_id": g["group_id"]}
        ent["goals"] = [{"player": e["player_name"], "minute": e["minute"]} for e in self.goals.get(m["match_id"], [])]
        return ent


    # ------------------------------------------------------------------ jugadores y árbitros (consultas multiámbito)
    def player_entities(self) -> list[dict[str, Any]]:
        """Jugadores con nombre «APELLIDOS, NOMBRE» (el de las actas): sus equipos y los homónimos (mismas palabras, otro jugador)."""
        if "players" in self._cache:
            return list(self._cache["players"])
        by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in self.player_rows:
            by_id[int(r["player_id"])].append(r)
        base: dict[int, dict[str, Any]] = {}
        for pid, rows in by_id.items():
            forms = person_forms(str(rows[0]["display_name"]))
            if forms is None:
                continue
            rows = sorted(rows, key=lambda r: r["team_id"])
            g = self.group(rows[0]["group_id"])
            base[pid] = {
                "kind": "player", "id": pid, "name": str(rows[0]["display_name"]), **forms, "team": rows[0]["team_name"],
                "team_id": rows[0]["team_id"], "group_id": g["group_id"], "group": g["group_name"], "competition": g["competition"],
                "category": g["category"], "season": g["season"], "team_count": len({r["team_id"] for r in rows}),
                "team_list": [r["team_name"] for r in rows], "competitions": sorted({self.group(r["group_id"])["competition"] for r in rows}),
                "_tokens": tokens_key(str(rows[0]["display_name"]))}
        by_tokens: dict[tuple[str, ...], list[int]] = defaultdict(list)
        by_short: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for pid, e in base.items():
            by_tokens[e["_tokens"]].append(pid)
            by_short[tuple(norm(e["first_surname"]).split())].append(pid)
        out = []
        for pid, e in base.items():
            same = [{k: v for k, v in base[o].items() if k != "_tokens"} for o in by_tokens[e["_tokens"]] if o != pid]
            ent = {k: v for k, v in e.items() if k != "_tokens"}
            ent["homonym_count"] = len(same)
            ent["first_surname_count"] = len(by_short[tuple(norm(e["first_surname"]).split())])
            ent["decoys"] = same
            out.append(ent)
        self._cache["players"] = out
        return list(out)

    def referee_entities(self) -> list[dict[str, Any]]:
        """Árbitros principales con los partidos que constan en las actas almacenadas (no son todos los partidos del snapshot)."""
        if "referees" in self._cache:
            return list(self._cache["referees"])
        per: dict[tuple[str, ...], dict[str, Any]] = {}
        for o in self.officials:
            role = norm(str(o["role"]))
            if role.startswith("arbitro") and "asistente" not in role:
                per.setdefault(tokens_key(str(o["official"])), {"name": str(o["official"]), "ids": set()})["ids"].add(o["match_id"])
        by_match = {m["match_id"]: m for m in self.matches}
        out: list[dict[str, Any]] = []
        for e in per.values():
            forms = person_forms(e["name"])
            ms = sorted((by_match[i] for i in e["ids"] if i in by_match), key=lambda m: (m["day"] or "", m["match_id"]))
            if forms is None or not ms:
                continue
            groups = [self.group(m["group_id"]) for m in ms]
            out.append({
                "kind": "referee", "name": e["name"], **forms, "matches_n": len(ms), "groups_n": len({g["group_id"] for g in groups}),
                "competitions": sorted({g["competition"] for g in groups}), "competitions_n": len({g["competition_id"] for g in groups}),
                "sample_home": [m["home"] for m in ms[:3]], "sample_away": [m["away"] for m in ms[:3]],
                "first_date": ms[0]["day"], "last_date": ms[-1]["day"], "decoys": [],
                "match_list": [f"{m['home']} - {m['away']}" for m in ms]})
        ranked = sorted((r["matches_n"] for r in out), reverse=True)
        for r in out:
            r["referees_n"] = len(out)
            r["top_unique"] = len(ranked) >= 2 and r["matches_n"] == ranked[0] and ranked[0] > ranked[1]
        self._cache["referees"] = out
        return list(out)

    def person_keys(self) -> set[tuple[str, ...]]:
        """Claves (palabras sin orden) de todo jugador u oficial del snapshot: un nombre inventado no puede coincidir con ninguna."""
        keys = {tokens_key(n) for n in self.player_names}
        return keys | {tokens_key(str(o["official"])) for o in self.officials}

    def lookalikes(self, real: dict[str, Any], pool: tuple[str, ...]) -> list[dict[str, Any]]:
        """Nombres verosímiles que NO existen: los apellidos de una persona real con otro nombre de pila (-> «Álvaro Sánchez Suárez»)."""
        taken, out = self.person_keys(), []
        for first in pool:
            if norm(first) == norm(real["first"]):
                continue
            display = f"{real['surnames'].upper()}, {first.upper()}"
            forms = person_forms(display)
            if forms and tokens_key(display) not in taken:
                out.append({"kind": real["kind"], "name": display, "base_name": real["name"], **forms, "real_name": real["name"],
                            "real_natural": real["natural"], "real_first": real["first"], "team": real.get("team"),
                            "group": real.get("group"), "competition": real.get("competition"), "decoys": []})
        return out

    # ------------------------------------------------------------------ fechas, franjas y pabellones
    def weekend_days(self) -> list[str]:
        if self.as_of is None:
            return []
        today = self.as_of.date()
        saturday = today - timedelta(days=1) if today.weekday() == 6 else today + timedelta(days=(5 - today.weekday()) % 7)
        return [saturday.isoformat(), (saturday + timedelta(days=1)).isoformat()]

    def day_entity(self, days: list[str], label: str, slot: str | None = None, category: str | None = None,
                   venue: str | None = None) -> dict[str, Any]:
        """Partidos de uno o varios días en TODOS los grupos del snapshot (hora local de Europe/Madrid), con filtros opcionales."""
        rows = []
        for m in self.matches:
            g = self.group(m["group_id"])
            if m["day"] not in days or (category and g["category"] != category) or (venue and m["venue"] != venue):
                continue
            rows.append((m, g, self.times.get(m["match_id"], {})))
        mid = sum(t.get("slot") == "mediodia" for _m, _g, t in rows)
        rows = sorted((r for r in rows if not slot or r[2].get("slot") == slot), key=lambda r: r[0]["match_id"])
        return {"kind": "day", "name": label, "date": days[0], "date_es": label, "count": len(rows),
                "groups_n": len({g["group_id"] for _m, g, _t in rows}), "competitions": sorted({g["competition"] for _m, g, _t in rows}),
                "competitions_n": len({g["competition_id"] for _m, g, _t in rows}),
                "categories": sorted({g["category"] for _m, g, _t in rows}),
                "sample_home": [m["home"] for m, _g, _t in rows[:3]], "midday_n": mid, "slot": slot, "category": category,
                "venue": venue, "venues_n": len({m["venue"] for m, _g, _t in rows if m["venue"]}), "weekend": len(days) > 1,
                "decoys": []}

    # ------------------------------------------------------------------ clubes (varios equipos y ámbitos)
    def club_base(self, team: dict[str, Any]) -> str:
        return self.club_bases.get(team["team_id"]) or base_name(team["name"])

    def club_groups(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in self.teams:
            out[name_key(self.club_base(t))].append(t)
        return out

    def club_entity(self, teams: list[dict[str, Any]], category: str | None = None) -> dict[str, Any] | None:
        mine = [t for t in teams if not category or self.group(t["group_id"])["category"] == category]
        if not mine:
            return None
        groups = [self.group(t["group_id"]) for t in mine]
        won = drawn = lost = gf = ga = 0
        for t in mine:
            for v in (self.match_view(m, t["team_id"]) for m in self.team_matches(t["team_id"]) if m["status"] == "finished"):
                if "result" not in v:
                    continue
                won, drawn, lost = won + (v["result"] == "ganó"), drawn + (v["result"] == "empató"), lost + (v["result"] == "perdió")
                gf, ga = gf + v["gf"], ga + v["ga"]
        base = self.club_base(mine[0])
        return {"kind": "club", "name": base, "base_name": base, "category": category, "teams_n": len(mine),
                "team_names": [t["name"] for t in mine], "groups_n": len({g["group_id"] for g in groups}),
                "competitions": sorted({g["competition"] for g in groups}), "categories": sorted({g["category"] for g in groups}),
                "categories_n": len({g["category"] for g in groups}), "suffixes_n": len({t["key"] for t in mine}),
                "played": won + drawn + lost, "won": won, "drawn": drawn, "lost": lost, "gf": gf, "ga": ga, "decoys": []}

    def ambiguous_clubs(self) -> list[dict[str, Any]]:
        """Palabra distintiva compartida por clubes con nombre base distinto: «DOSA» en «C.D. DOSA» y «C.D. DOSA ZONA 3»."""
        by_token: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for k, teams in self.club_groups().items():
            by_token[key(self.club_base(teams[0]))][k] += teams
        out = []
        for token, clubs in by_token.items():
            if len(clubs) < 2 or not token:
                continue
            names = sorted({self.club_base(ts[0]) for ts in clubs.values()})
            out.append({"kind": "club", "name": token.upper(), "token": token.upper(), "base_name": token.upper(),
                        "candidates": names, "candidates_n": len(names),
                        "team_names": [t["name"] for ts in clubs.values() for t in ts], "decoys": []})
        return out

    # ------------------------------------------------------------------ rankings entre grupos (los grupos NO son comparables)
    def scorer_rank_entity(self, category: str) -> dict[str, Any] | None:
        rows = [r for r in self.scorer_ranking if r["category"] == category]
        top = sorted((r for r in rows if r["category_rank"] == 1), key=lambda r: r["player_name"])
        if not top:
            return None
        t = top[0]  # con empate en cabeza, `top` es el primero por nombre y `top_names` los reúne a todos
        rest = sorted((r["goals"] for r in rows if r["category_rank"] != 1), reverse=True)
        return {"kind": "ranking", "name": category, "category": category, "gender": t["gender"],
                "groups_n": len({r["group_id"] for r in rows}), "top_names": [r["player_name"] for r in top],
                "tied_n": len(top), "top": {"name": t["player_name"], "team": t["team_name"], "goals": t["goals"], "competition": t["competition"],
                        "group": t["group_name"]}, "second_goals": rest[0] if rest else None, "decoys": []}

    def team_rank_entity(self, category: str, metric: str) -> dict[str, Any] | None:
        """Mejor equipo de una categoría por goles a favor, victorias, goles en contra (menor) o racha actual de victorias."""
        cands: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for g in self.groups:
            if g["category"] != category:
                continue
            if metric == "win_streak":
                for t in (t for t in self.teams if t["group_id"] == g["group_id"]):
                    streak = 0
                    for m in sorted((m for m in self.team_matches(t["team_id"]) if m["status"] == "finished"),
                                    key=lambda m: (m["round"] or 0, m["day"] or "", m["match_id"]), reverse=True):
                        if self.match_view(m, t["team_id"]).get("result") != "ganó":
                            break
                        streak += 1
                    cands.append((streak, g, {"name": t["name"]}))
            else:
                col = {"goals_for": "goals_for", "wins": "won", "goals_against": "goals_against"}[metric]
                cands += [(int(r[col]), g, {"name": r["team_name"], "played": r["played"]}) for r in self.standings.get(g["group_id"], []) if r["played"]]  # sin partidos jugados no hay mejor ni peor equipo
        if len(cands) < 2:
            return None
        best = min(c[0] for c in cands) if metric == "goals_against" else max(c[0] for c in cands)
        top = sorted((c for c in cands if c[0] == best), key=lambda c: c[2]["name"])
        if not top or (metric == "win_streak" and best < 2):
            return None
        value, g, who = top[0]  # con empate en cabeza, `top` es el primero por nombre y `top_names` los reúne a todos
        return {"kind": "ranking", "name": category, "category": category, "metric": metric,
                "groups_n": len({c[1]["group_id"] for c in cands}), "top_names": [c[2]["name"] for c in top], "tied_n": len(top),
                "top": {**who, "value": value, "group": g["group_name"], "competition": g["competition"]}, "decoys": []}

    def category_goal_avg(self, category: str) -> tuple[float, int] | None:
        ms = [m for m in self.matches if m["status"] == "finished" and m["home_score"] is not None
              and self.group(m["group_id"])["category"] == category]
        return (sum(m["home_score"] + m["away_score"] for m in ms) / len(ms), len(ms)) if ms else None
