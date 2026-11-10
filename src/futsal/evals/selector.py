"""Selector determinista de entidades y resolución de plantillas (docs/v2/evaluation.md §3).

Dado un caso y el catálogo del snapshot, elige las entidades que cumplen `select` (orden por sha256(semilla|caso|nombre|clave);
semilla = huella del snapshot) y rellena preguntas y hechos esperados. Si falta una capacidad (`requires`) o no hay entidad que
cumpla `select`, el caso queda `not_evaluable` con el motivo. La verdad sale SOLO del catálogo (SQL con nombre).
"""

import hashlib
import operator
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from futsal.agent.evaluate import norm
from futsal.evals.catalog import Catalog, date_es, name_key
from futsal.evals.model import Case, TurnSpec

PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*(?:(?:\.\w+)|(?:\[(?:\d+|\*)\]))*)((?:\|\w+)*)\}")
FICTIONAL_TEAMS = ("C.D. Valdemoro Alta Sala", "Atlético Vallecas Futsal Élite", "Real Sala Aranjuez Norte",
                   "U.D. Pozuelo Dorado", "F.S. Alcorcón Cristal")
FICTIONAL_COMPETITIONS = ("Liga Senior Femenina Oro", "Copa Nacional de Futsal Élite", "Superliga Madrileña de Honor")
FICTIONAL_PLAYERS = ("Marcos Valdivieso Quirós", "Hugo Ferrándiz Olmedilla", "Íker Soriano Bellido")
FIRST_NAMES = ("Álvaro", "Raúl", "Marcos", "Iván", "Hugo", "Sergio", "Daniel", "Adrián")  # nombres de pila para personas inexistentes verosímiles
ABSENT_SEASONS = ("2019-20", "2018-19", "2017-18", "2020-21")
_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}


class Unresolvable(Exception):
    """Un dato que la plantilla necesita no existe para la entidad elegida."""


# ----------------------------------------------------------------------------- plantillas
def _tokens(path: str) -> list[str | int]:
    first = re.match(r"\w+", path)
    assert first is not None
    toks: list[str | int] = [first.group()]
    for m in re.finditer(r"\.(\w+)|\[(\d+|\*)\]", path[first.end():]):
        toks.append(m.group(1) if m.group(1) else ("*" if m.group(2) == "*" else int(m.group(2))))
    return toks


def _walk(obj: Any, toks: list[str | int], path: str) -> Any:
    if not toks:
        return obj
    tok, rest = toks[0], toks[1:]
    if tok == "*":
        if not isinstance(obj, list):
            raise Unresolvable(path)
        out: list[Any] = []
        for item in obj:
            val = _walk(item, rest, path)
            out += val if isinstance(val, list) else [val]
        return out
    if isinstance(tok, int):
        if not isinstance(obj, list) or tok >= len(obj):
            raise Unresolvable(path)
        return _walk(obj[tok], rest, path)
    if not isinstance(obj, dict) or obj.get(tok) is None:
        raise Unresolvable(path)
    return _walk(obj[tok], rest, path)


def lookup(path: str, bindings: dict[str, Any]) -> Any:
    toks = _tokens(path)
    if toks[0] not in bindings:
        raise Unresolvable(path)
    return _walk(bindings[toks[0]], toks[1:], path)


def _unaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _nopunct(text: str) -> str:
    return re.sub(r"[.']", "", text)


def _typo(text: str) -> str:
    """Errata de teclado determinista: intercambia dos letras de la palabra más larga (o quita la última si es corta)."""
    words = text.split()
    if not words:
        return text
    i = max(range(len(words)), key=lambda k: len(words[k]))
    w = words[i]
    words[i] = w[:2] + w[3] + w[2] + w[4:] if len(w) >= 5 else w[:-1] if len(w) >= 3 else w
    return " ".join(words)


PIPES: dict[str, Callable[[str], str]] = {"lower": str.lower, "upper": str.upper, "unaccent": _unaccent, "nopunct": _nopunct, "typo": _typo}


def _piped(value: Any, pipes: str | None) -> Any:
    for pipe in (pipes or "").split("|")[1:]:
        if pipe not in PIPES:
            raise Unresolvable(f"|{pipe}")
        value = PIPES[pipe](str(value))
    return value


def _fmt(value: Any) -> str:
    return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)


def render(value: Any, bindings: dict[str, Any]) -> Any:
    """Sustituye `{entidad.campo}`. Una cadena que es solo un marcador conserva el tipo (número, lista)."""
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value)
        if whole:
            return _piped(lookup(whole.group(1), bindings), whole.group(2))
        return PLACEHOLDER.sub(lambda m: _fmt(_piped(lookup(m.group(1), bindings), m.group(2))), value)
    if isinstance(value, list):
        out: list[Any] = []
        for v in value:
            r = render(v, bindings)
            out += r if isinstance(r, list) and isinstance(v, str) else [r]
        return out
    if isinstance(value, dict):
        return {k: render(v, bindings) for k, v in value.items()}
    return value


def referenced(value: Any) -> set[str]:
    """Nombres de entidad que usa una plantilla."""
    if isinstance(value, str):
        return {m.group(1).split(".")[0].split("[")[0] for m in PLACEHOLDER.finditer(value)}
    if isinstance(value, list):
        return set().union(*(referenced(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(referenced(v) for v in value.values())) if value else set()
    return set()


# ----------------------------------------------------------------------------- selección
def _cmp(count: int, spec: Any) -> bool:
    if spec in ("none", 0, "0"):
        return count == 0
    text = str(spec).strip()
    for sym in (">=", "<=", "==", ">", "<"):
        if text.startswith(sym):
            return bool(_OPS[sym](count, int(text[len(sym):])))
    return count == int(text)


def _rank(seed: str, case_id: str, name: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{case_id}|{name}|{key}".encode()).hexdigest()


def _group_flags(g: dict[str, Any]) -> set[str]:
    flags = set()
    if g["has_standings"]:
        flags.add("standings")
    elif g["teams"]:
        flags.add("no_standings")
    if g["has_scorers"]:
        flags.add("scorers")
    elif g["teams"]:
        flags.add("no_scorers")
    for key, label in (("reports", "reports"), ("finished", "finished"), ("pending", "pending")):
        if g[key]:
            flags.add(label)
    return flags


def _relations_ok(g: dict[str, Any], spec: dict[str, Any], bound: dict[str, Any]) -> bool:
    """Relaciones con entidades ya elegidas: otro grupo, mismo grupo, otra competición."""
    if "other_group_than" in spec and bound[spec["other_group_than"]].get("group_id") == g["group_id"]:
        return False
    if "same_group_as" in spec and bound[spec["same_group_as"]].get("group_id") != g["group_id"]:
        return False
    return not ("other_competition_than" in spec and bound[spec["other_competition_than"]].get("competition") == g["competition"])


def _scope_ok(g: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec.get("category") and g["category"] != spec["category"]:
        return False
    if spec.get("season") and g["season"] != spec["season"]:
        return False
    if spec.get("group_with") and not set(spec["group_with"]) <= _group_flags(g):
        return False
    return not (spec.get("competition_like") and name_key(spec["competition_like"]) not in name_key(g["competition"]))


def _where_ok(entity: dict[str, Any], where: dict[str, Any]) -> bool:
    """Condiciones numéricas sobre los campos de la entidad: `where: {away_wins: ">=1"}`."""
    for path, spec in where.items():
        try:
            value = _walk(entity, [*_tokens("e." + path)[1:]], path)
        except Unresolvable:
            return False
        if not isinstance(value, int | float) or not _cmp(int(value), spec):
            return False
    return True


class _Picker:
    def __init__(self, cat: Catalog, case_id: str) -> None:
        self.cat, self.case_id = cat, case_id

    def pick(self, name: str, spec: dict[str, Any], bound: dict[str, Any]) -> dict[str, Any]:
        kind = spec["kind"]
        cands: list[tuple[str, dict[str, Any]]] = getattr(self, f"_{kind}")(spec, bound)
        if spec.get("where"):
            cands = [kv for kv in cands if _where_ok(kv[1], spec["where"])]
        if not cands:
            raise Unresolvable(f"ninguna entidad cumple select.{name} ({_describe(spec)})")
        cands.sort(key=lambda kv: _rank(self.cat.seed, self.case_id, name, kv[0]))
        return cands[0][1]

    # -- equipos
    def _team(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat, out = self.cat, []
        for t in cat.teams:
            g = cat.group(t["group_id"])
            if not _scope_ok(g, spec) or not _relations_ok(g, spec, bound):
                continue
            if spec.get("with") and not set(spec["with"]) <= cat.has_flags(t):
                continue
            hom = cat.homonym_rows(t)
            if "homonyms" in spec and not _cmp(len(hom), spec["homonyms"]):
                continue
            if "homonym_groups" in spec and not _cmp(len(hom) + 1, spec["homonym_groups"]):
                continue
            mates = cat.club_mate_rows(t)
            if "club_mates" in spec and not _cmp(len(mates), spec["club_mates"]):
                continue
            if spec.get("unique_in_snapshot") and (hom or mates):
                continue
            hom_groups = [cat.group(h["group_id"]) for h in hom]
            if spec.get("same_category") and any(x["category"] != g["category"] for x in hom_groups):
                continue
            if spec.get("diff_category") and not any(x["category"] != g["category"] for x in hom_groups):
                continue
            if spec.get("diff_season") and not any(x["season"] != g["season"] for x in hom_groups):
                continue
            if spec.get("same_competition") and not any(x["competition_id"] == g["competition_id"] for x in hom_groups):
                continue
            out.append((str(t["team_id"]), cat.team_entity(t)))
        return out

    # -- clubes con equipos A y B en el mismo grupo
    def _club(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat, out = self.cat, []
        by_club: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for t in cat.teams:
            by_club.setdefault((t["group_id"], t["base_key"]), []).append(t)
        for (gid, bkey), teams in by_club.items():
            if len({t["key"] for t in teams}) < 2:
                continue
            g = cat.group(gid)
            if not _scope_ok(g, spec) or not _relations_ok(g, spec, bound):
                continue
            if spec.get("with") and not all(set(spec["with"]) <= cat.has_flags(t) for t in teams):
                continue
            ents = [cat.team_entity(t, decoys=False) for t in teams]
            out.append((f"{gid}:{bkey}", {
                "kind": "club", "name": ents[0]["base_name"], "base_name": ents[0]["base_name"], "group_id": gid,
                "group": g["group_name"], "competition": g["competition"], "category": g["category"], "season": g["season"],
                "teams": ents, "team_names": [e["name"] for e in ents], "decoys": ents}))
        return out

    # -- grupos
    def _group(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat, out = self.cat, []
        for g in cat.groups:
            if not _scope_ok(g, {k: v for k, v in spec.items() if k != "group_with"}) or not _relations_ok(g, spec, bound):
                continue
            if spec.get("with") and not set(spec["with"]) <= _group_flags(g):
                continue
            if "teams" in spec and not _cmp(g["teams"], spec["teams"]):
                continue
            if "competition_groups" in spec and not _cmp(
                    sum(x["competition_id"] == g["competition_id"] for x in cat.groups), spec["competition_groups"]):
                continue
            if "category_groups" in spec and not _cmp(
                    sum(x["category"] == g["category"] for x in cat.groups), spec["category_groups"]):
                continue
            if "same_name_groups" in spec and not _cmp(
                    sum(x["group_name"] == g["group_name"] and x["group_id"] != g["group_id"] for x in cat.groups),
                    spec["same_name_groups"]):
                continue
            out.append((str(g["group_id"]), cat.group_entity(g)))
        return out

    # -- competiciones
    def _competition(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat, out = self.cat, []
        seen: set[int] = set()
        for g in cat.groups:
            if g["competition_id"] in seen:
                continue
            seen.add(g["competition_id"])
            mine = [x for x in cat.groups if x["competition_id"] == g["competition_id"]]
            if not _scope_ok(g, {k: v for k, v in spec.items() if k != "group_with"}) or not _relations_ok(g, spec, bound):
                continue
            if "groups" in spec and not _cmp(len(mine), spec["groups"]):
                continue
            if spec.get("with") and not any(set(spec["with"]) <= _group_flags(x) for x in mine):
                continue
            others = [x for x in cat.groups if x["competition_id"] != g["competition_id"] and (
                x["competition"] == g["competition"] or x["season"] == g["season"])]
            out.append((str(g["competition_id"]), {
                "kind": "competition", "id": g["competition_id"], "name": g["competition"], "competition": g["competition"],
                "category": g["category"], "season": g["season"], "groups": len(mine),
                "group_names": [x["group_name"] for x in mine], "group": mine[0]["group_name"],
                "decoys": [{"kind": "competition", "name": x["competition"], "competition": x["competition"],
                            "group": x["group_name"], "category": x["category"], "season": x["season"]} for x in others[:6]]}))
        return out

    # -- partidos
    def _match(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat, out = self.cat, []
        for m in cat.matches:
            g = cat.group(m["group_id"])
            if not _scope_ok(g, spec) or not _relations_ok(g, spec, bound):
                continue
            if spec.get("finished") and m["status"] != "finished":
                continue
            if spec.get("pending") and m["status"] == "finished":
                continue
            if spec.get("status") and m["status"] != spec["status"]:
                continue
            if spec.get("open_report") and cat.times.get(m["match_id"], {}).get("report_closed") is not False:
                continue
            if spec.get("no_report") and m["has_report"]:
                continue
            if spec.get("has_report") and not m["has_report"]:
                continue
            out.append((str(m["match_id"]), cat.match_entity(m)))
        return out

    # -- entidades que NO existen (casos sin datos y adversariales)
    def _season_absent(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        have = {name_key(s) for s in self.cat.seasons()}
        return [(s, {"kind": "season", "name": s}) for s in ABSENT_SEASONS if name_key(s) not in have]

    def _fictional_team(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        have = {t["key"] for t in self.cat.teams}
        return [(n, {"kind": "team", "name": n, "base_name": n}) for n in FICTIONAL_TEAMS if name_key(n) not in have]

    def _fictional_competition(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        have = [name_key(g["competition"]) for g in self.cat.groups]
        return [(n, {"kind": "competition", "name": n, "competition": n}) for n in FICTIONAL_COMPETITIONS
                if not any(name_key(n) in h for h in have)]

    def _category_absent(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        have = {g["category"] for g in self.cat.groups}
        return [(c, {"kind": "category", "name": c}) for c in ("cadete", "infantil", "alevin") if c not in have]

    def _fictional_player(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        have = {name_key(p) for p in self.cat.player_names}
        return [(n, {"kind": "player", "name": n}) for n in FICTIONAL_PLAYERS if name_key(n) not in have]


    # -- personas: jugadores y árbitros (consultas multiámbito: sin grupo ni competición)
    @staticmethod
    def _fragment_of(fragment: str, name: str) -> bool:
        """El fragmento (una o varias palabras, sin orden ni acentos) está contenido en el nombre completo."""
        words = lambda x: set(re.findall(r"[a-z0-9]+", norm(x)))
        return words(fragment) <= words(name)

    def _person_ok(self, e: dict[str, Any], spec: dict[str, Any], bound: dict[str, Any]) -> bool:
        if spec.get("name_like") and not self._fragment_of(spec["name_like"], e["name"]):
            return False
        if spec.get("has_accent") and not e.get("has_accent"):
            return False
        if spec.get("second_surname") and not e.get("second_surname"):
            return False
        if spec.get("category") and e.get("category") != spec["category"]:
            return False
        if "other_group_than" in spec and bound[spec["other_group_than"]].get("group_id") == e.get("group_id"):
            return False
        return not ("same_group_as" in spec and bound[spec["same_group_as"]].get("group_id") != e.get("group_id"))

    def _player(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [(str(e["id"]), e) for e in self.cat.player_entities() if self._person_ok(e, spec, bound)]

    def _referee(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [(e["name"], e) for e in self.cat.referee_entities() if self._person_ok(e, spec, bound)]

    def _lookalike(self, reals: list[dict[str, Any]], spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for real in reals:
            if spec.get("like") and not self._fragment_of(spec["like"], real["name"]):
                continue
            for cand in self.cat.lookalikes(real, FIRST_NAMES):
                if not spec.get("first") or norm_eq(spec["first"], cand["first"]):
                    out.append((f"{real['name']}|{cand['name']}", cand))
        return out

    def _player_lookalike(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return self._lookalike(self.cat.player_entities(), spec)

    def _referee_lookalike(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return self._lookalike(self.cat.referee_entities(), spec)

    # -- fechas, franjas y pabellones
    def _day(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        cat = self.cat
        today = cat.as_of.date().isoformat() if cat.as_of else None
        cands: list[tuple[str, list[str], str]]
        if spec.get("weekend"):
            days = cat.weekend_days()
            cands = [("weekend", days, "este fin de semana")] if days else []
        elif spec.get("date") or spec.get("same_day_as"):
            iso = spec.get("date") or bound[spec["same_day_as"]]["date"]
            cands = [(iso, [iso], date_es(date.fromisoformat(iso)))]
        else:
            alld = sorted({m["day"] for m in cat.matches if m["day"]})
            if today and spec.get("future"):
                alld = [d for d in alld if d > today]
            if today and spec.get("past"):
                alld = [d for d in alld if d <= today]
            cands = [(d, [d], date_es(date.fromisoformat(d))) for d in alld]
        out = []
        for dkey, days, label in cands:
            venues: list[str | None] = [None]
            if spec.get("by_venue"):
                venues = sorted({m["venue"] for m in cat.matches if m["day"] in days and m["venue"]})
            for venue in venues:
                ent = cat.day_entity(days, label, spec.get("slot"), spec.get("category"), venue)
                if spec.get("slot") and spec.get("clean_slot") and ent["midday_n"]:
                    continue  # un partido a mediodía hace discutible «por la mañana / por la tarde»
                if (spec.get("date") or spec.get("same_day_as")) and not ent["count"]:
                    continue
                if spec.get("with_category") and not cat.day_entity(days, label, None, spec["with_category"])["count"]:
                    continue
                out.append((f"{dkey}|{venue}", ent))
        return out

    def _day_absent(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Sábados y domingos SIN partidos en el snapshot: dentro del calendario ingerido o después de su último día."""
        cat = self.cat
        have = sorted({m["day"] for m in cat.matches if m["day"]})
        if not have:
            return []
        first, last = date.fromisoformat(have[0]), date.fromisoformat(have[-1])
        if spec.get("future"):
            start = max(last, cat.as_of.date() if cat.as_of else last) + timedelta(days=14)
            days = [start + timedelta(days=i) for i in range(60)]
        else:
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        return [(d.isoformat(), cat.day_entity([d.isoformat()], date_es(d))) for d in days
                if d.weekday() >= 5 and d.isoformat() not in have]

    # -- clubes con varios equipos o ámbitos, y nombres de club ambiguos
    def _club_multi(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for bkey, teams in self.cat.club_groups().items():
            ent = self.cat.club_entity(teams, spec.get("category"))
            if ent is None or (spec.get("name_like") and not set(norm(spec["name_like"]).split()) <= set(norm(ent["base_name"]).split())):
                continue  # por palabras: «ALAMO» no es «INTERSALA MOSTOLES»
            out.append((bkey, ent))
        return out

    def _club_ambiguous(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [(e["token"], e) for e in self.cat.ambiguous_clubs()]

    # -- rankings entre grupos
    def _scorer_ranking(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        ent = self.cat.scorer_rank_entity(spec["category"])
        return [(spec["category"], ent)] if ent else []

    def _team_ranking(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        ent = self.cat.team_rank_entity(spec["category"], spec["metric"])
        return [(f"{spec['category']}|{spec['metric']}", ent)] if ent else []

    def _category_pair(self, spec: dict[str, Any], bound: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        a, b = self.cat.category_goal_avg(spec["a"]), self.cat.category_goal_avg(spec["b"])
        if not a or not b or f"{a[0]:.2f}" == f"{b[0]:.2f}":
            return []
        hi, lo = (spec["a"], spec["b"]) if a[0] > b[0] else (spec["b"], spec["a"])
        def fmt(v: float) -> str:
            return f"{v:.2f}".replace(".", ",")

        return [(f"{spec['a']}|{spec['b']}", {
            "kind": "ranking", "name": f"{spec['a']} y {spec['b']}", "a": spec["a"], "b": spec["b"], "avg_a": fmt(a[0]), "avg_b": fmt(b[0]),
            "matches_a": a[1], "matches_b": b[1], "higher": hi, "lower": lo, "decoys": []})]


def norm_eq(a: str, b: str) -> bool:
    return name_key(a) == name_key(b)


def _describe(spec: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in spec.items())


# ----------------------------------------------------------------------------- caso resuelto
@dataclass
class ResolvedTurn:
    user: str
    behavior: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    forbid_facts: list[dict[str, Any]] = field(default_factory=list)
    decoy_facts: list[dict[str, Any]] = field(default_factory=list)
    clarify_mentions: list[str] = field(default_factory=list)
    tools_needed: list[str] = field(default_factory=list)
    tools_allowed: list[str] | None = None
    max_llm_calls: int | None = None
    forbid_scores: list[str] = field(default_factory=list)
    forbid_any_score: bool = False
    forbid_phrases: list[str] = field(default_factory=list)
    forbid_tools: list[str] = field(default_factory=list)
    source: str | None = None
    rule_case: str | None = None
    scope_requested: list[str] = field(default_factory=list)
    scope_other: list[str] = field(default_factory=list)
    known_names: list[str] = field(default_factory=list)  # nombres de la verdad del caso (entidad pedida, hechos, opciones de la aclaración)
    entity_keys: list[str] = field(default_factory=list)  # claves de nombre de la entidad pedida (¿la respuesta trata de ella?)
    v2: dict[str, Any] | None = None  # conducta y hechos esperados de una arquitectura que SÍ resuelve la consulta (verdad SQL; no se califica en v1)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ResolvedCase:
    case_id: str
    stratum: str
    holdout: bool
    capability: str
    status: str  # ok | not_evaluable
    reason: str = ""
    bindings: dict[str, Any] = field(default_factory=dict)
    turns: list[ResolvedTurn] = field(default_factory=list)


def _lite(entity: dict[str, Any]) -> dict[str, Any]:
    """Entidad para el informe: sin las listas de señuelos anidadas (se conservan sus nombres)."""
    out = {k: v for k, v in entity.items() if k not in ("decoys", "homonyms", "club_mates", "teams")}
    out["decoys"] = [f"{d.get('name', '')} | {d.get('competition', '')} | {d.get('group', '')} | {d.get('season', '')}"
                     for d in entity.get("decoys", [])]
    return out


def _fact_key(f: dict[str, Any]) -> tuple[str, str, str]:
    return str(f.get("kind")), str(f.get("value", f.get("any"))), str(f.get("label", ""))


def _decoy_facts(specs: list[dict[str, Any]], bindings: dict[str, Any], required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mismos hechos evaluados sobre los señuelos (homónimos, otras categorías o temporadas) y distintos de los pedidos."""
    have = {_fact_key(f) for f in required}
    out: list[dict[str, Any]] = []
    for spec in specs:
        for name in sorted(referenced(spec) & set(bindings)):
            for d in bindings[name].get("decoys", []) if isinstance(bindings[name], dict) else []:
                try:
                    fact = render(spec, {**bindings, name: d})
                except Unresolvable:
                    continue
                for one in _expand([fact]):
                    if _fact_key(one) not in have and one not in out:
                        out.append({**one, "decoy_of": f"{d.get('name', '')} ({d.get('competition', '')} {d.get('group', '')} {d.get('season', '')})"})
    return out


def _scope_names(bindings: dict[str, Any]) -> tuple[list[str], list[str]]:
    primary = next((b for b in bindings.values() if isinstance(b, dict) and b.get("kind") in ("team", "group", "club", "match")), None)
    if not primary:
        return [], []
    mine = {name_key(str(primary.get(k, ""))) for k in ("group", "competition") if primary.get(k)}
    other: list[str] = []
    for d in primary.get("decoys", []):  # solo competiciones: sus nombres son largos y distintivos; «Grupo 2» aparece en cualquier texto
        val = str(d.get("competition", ""))
        if val and name_key(val) not in mine and not any(name_key(val) in m or m in name_key(val) for m in mine) and val not in other:
            other.append(val)
    return [str(primary[k]) for k in ("group", "competition") if primary.get(k)], other


def _expand(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Un hecho cuyo valor es una lista (`{team.recent[*].score}`) se convierte en un hecho por elemento."""
    out: list[dict[str, Any]] = []
    for f in facts:
        v = f.get("value")
        if isinstance(v, list) and f["kind"] in ("number", "score", "name", "date"):
            out += [{**f, "value": x} for x in v]
        else:
            out.append(f)
    return out


def _truth_strings(bindings: dict[str, Any], turn: ResolvedTurn) -> list[str]:
    """Textos que forman parte de la verdad de un turno: entidades pedidas (sin señuelos), valores de los hechos y opciones de la aclaración."""
    out: list[str] = []
    for b in bindings.values():
        if isinstance(b, dict):
            for k in ("name", "base_name", "group", "competition", "home", "away"):
                if isinstance(b.get(k), str):
                    out.append(b[k])
            out += [str(n) for n in b.get("team_names", [])]
    for f in turn.facts:
        v = f.get("value")
        out += [str(x) for x in (v if isinstance(v, list) else [v])] if v is not None else []
    return [*out, *turn.clarify_mentions]


def resolve_turn(spec: TurnSpec, bindings: dict[str, Any]) -> ResolvedTurn:
    e = spec.expect
    facts = _expand(render(e.get("facts") or [], bindings))
    turn = ResolvedTurn(user=str(render(spec.user, bindings)), behavior=e["behavior"], facts=facts)
    turn.forbid_facts = _expand(render(e.get("forbid_facts") or [], bindings))
    for cand in e.get("candidate_facts") or []:  # clarify: los hechos de CUALQUIER candidato no deben darse sin preguntar
        who = cand.get("of") or next(iter(bindings))
        body = {k: v for k, v in cand.items() if k != "of"}
        for ent in [bindings[who], *bindings[who].get("decoys", [])]:
            try:
                turn.forbid_facts += _expand([render(body, {**bindings, who: ent})])
            except Unresolvable:
                continue
    if e["behavior"] in ("answer", "abstain") and not e.get("ignore_decoys"):
        turn.decoy_facts = _decoy_facts(e.get("facts") or [], bindings, facts)
    turn.clarify_mentions = [str(x) for x in render(e.get("clarify_mentions") or [], bindings)]
    tools = e.get("tools") or {}
    turn.tools_needed = [str(t) for t in tools.get("needed", [])]
    turn.tools_allowed = [str(t) for t in tools["allowed"]] if "allowed" in tools else None
    turn.max_llm_calls = e.get("max_llm_calls")
    turn.forbid_scores = [str(x) for x in render(e.get("forbid_scores") or [], bindings)]
    turn.forbid_any_score = bool(e.get("forbid_any_score"))
    turn.forbid_phrases = [str(x) for x in render(e.get("forbid_phrases") or [], bindings)]
    turn.forbid_tools = [str(t) for t in e.get("forbid_tools") or []]
    turn.source, turn.rule_case = e.get("source"), e.get("rule_case")
    if e.get("v2"):
        v2 = render(e["v2"], bindings)
        turn.v2 = {**v2, "facts": _expand(v2.get("facts") or [])}
    turn.scope_requested, turn.scope_other = _scope_names(bindings)
    turn.known_names = _truth_strings(bindings, turn)
    primary = next((b for b in bindings.values() if isinstance(b, dict) and b.get("kind") in ("team", "club", "group")), None)
    if primary and primary.get("name"):
        turn.entity_keys = [name_key(str(primary["name"]))]
    return turn


def resolve_case(case: Case, cat: Catalog, caps: set[str] | None = None) -> ResolvedCase:
    caps = cat.capabilities() if caps is None else caps
    out = ResolvedCase(case.id, case.stratum, case.holdout, case.capability, "ok")
    missing = [r for r in case.requires if r not in caps]
    if missing:
        out.status, out.reason = "not_evaluable", f"capacidad ausente en el snapshot: {', '.join(missing)}"
        return out
    picker = _Picker(cat, case.id)
    bound: dict[str, Any] = {}
    try:
        for name, spec in case.select.items():
            bound[name] = picker.pick(name, spec, bound)
        turns = [resolve_turn(t, bound) for t in case.turns]
    except Unresolvable as exc:
        out.status, out.reason = "not_evaluable", f"sin entidad o dato en el snapshot: {exc}"
        return out
    out.bindings, out.turns = {k: _lite(v) for k, v in bound.items()}, turns
    return out
