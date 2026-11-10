"""Consultas de SOLO LECTURA de la API v2 (T09, «consultas sin grupo»): todos los grupos de una temporada a la vez.

Referencias opacas (`team:`, `group:`, `comp:`, `match:`, `player:`, `club:`, `season:`) con los IDs internos; nunca IDs de la RFFM. Todas las
respuestas llevan `items`, `count` (total de coincidencias), `shown`, `notes` y `freshness` (máxima fecha de captura válida implicada).
Los nombres se comparan normalizados (sin acentos ni signos, minúsculas) y por palabras completas; si no hay coincidencia exacta NUNCA se
devuelve un parecido como si fuera lo pedido: solo `suggestions`. Los cálculos (rankings, resúmenes) son deterministas y viven aquí.
"""

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, Numeric, Time, and_, case, cast, func, or_, select, union_all
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from futsal.config.competition_aliases import load_aliases
from futsal.db.models import (
    Club,
    Competition,
    CompetitionGroup,
    DatasetRefresh,
    Match,
    MatchEvent,
    MatchObservation,
    MatchOfficial,
    MatchPlayer,
    MatchReport,
    MatchReportObservation,
    Player,
    PlayerTeamMembership,
    Round,
    Season,
    Team,
    TeamEntity,
)
from futsal.db.table_models import ScorerRow, ScorerSnapshot, StandingsRow, StandingsSnapshot
from futsal.domain.competitions import classify
from futsal.domain.names import club_base_tokens, norm_name, squad_of, tokens_of
from futsal.repositories.report_queries import latest

MADRID = ZoneInfo("Europe/Madrid")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
CATEGORIES = ("senior", "juvenil", "cadete", "infantil", "alevin", "benjamin", "prebenjamin", "veteranos", "universitario")
GENDERS = ("male", "female", "mixed")
GENDER_ALIASES = {"masculino": "male", "femenino": "female", "mixto": "mixed"}
KINDS = ("league", "cup", "tournament", "playoff")
STATUSES = ("scheduled", "finished", "postponed", "suspended", "cancelled", "unknown")
TEAM_METRICS = ("goals_for", "goals_against", "wins", "points_per_game")
SIMILARITY_MIN = 0.3
MAX_SUGGESTIONS = 5
MAX_OFFICIAL_MATCHES = 100
MAX_TEAM_RESULTS = 50
DIFFERENT_ROUNDS_NOTE = "Ranking que compara grupos distintos con diferente número de jornadas jugadas."
CLUB_LINK_NOTE = "agrupado por nombre; pendiente de confirmar con el código oficial de club"


class InvalidParam(ValueError):
    """Parámetro inválido: la API responde 422."""


@dataclass(frozen=True)
class Filters:
    season: str | None = None
    category: str | None = None
    gender: str | None = None
    competition: str | None = None


@dataclass(frozen=True)
class CompInfo:
    id: int
    name: str
    category: str
    gender: str
    kind: str
    source: str  # catalog (clasificada auto|confirmed en 0007) | name (deducida del nombre)


@dataclass
class Scope:
    season: Season | None
    comps: dict[int, CompInfo]
    notes: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return list(self.comps)


# ----------------------------------------------------------------------------- utilidades
def ref(kind: str, ident: int | None) -> str | None:
    return f"{kind}:{ident}" if ident is not None else None


def ref_obj(kind: str, ident: int | None, name: str | None) -> dict[str, Any]:
    return {"ref": ref(kind, ident), "name": name}


def envelope(items: list[dict[str, Any]], count: int, notes: list[str], freshness: datetime | None, scope: Scope | None = None,
             **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"items": items, "count": count, "shown": len(items), "notes": notes,
                            "freshness": freshness.isoformat() if freshness else None}
    if scope is not None:
        body["season"] = ({"ref": ref("season", scope.season.id), "label": season_label(scope.season)} if scope.season else None)
    body.update(extra)
    return body


def season_label(season: Season) -> str:
    return season.label or season.name or season.external_id


def _max(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def sql_norm(col: Any) -> ColumnElement[str]:
    """Misma normalización que `names.norm_name`, en SQL: sin acentos, minúsculas, solo [a-z0-9] y espacios simples."""
    return func.trim(func.regexp_replace(func.kb_unaccent(func.lower(col)), "[^a-z0-9]+", " ", "g"))


def words_match(norm_col: Any, tokens: Sequence[str]) -> ColumnElement[bool]:
    """Todas las palabras (completas, en cualquier orden) están en la columna ya normalizada."""
    padded = func.concat(" ", norm_col, " ")
    return and_(*[padded.like(f"% {t} %") for t in tokens])


def parse_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidParam(f"limit debe estar entre 1 y {MAX_LIMIT}")
    return limit


def _needed_tokens(name: str) -> list[str]:
    tokens = tokens_of(name)
    if not tokens:
        raise InvalidParam("name debe contener letras o números")
    return tokens


# ----------------------------------------------------------------------------- ámbito: temporada + clasificación de competiciones
def _default_season(db: Session, today: date) -> tuple[Season | None, str]:
    current = db.scalar(select(Season).where(Season.is_current).order_by(Season.id.desc()).limit(1))
    if current is not None:
        return current, "marcada como vigente (is_current)"
    day = func.coalesce(Match.scheduled_date, cast(Match.scheduled_at, Date))
    recent = db.execute(
        select(Competition.season_id, func.count(Match.id).label("n"))
        .select_from(Match).join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .where(day.between(today - timedelta(days=60), today + timedelta(days=30)))
        .group_by(Competition.season_id).order_by(func.count(Match.id).desc(), Competition.season_id.desc()).limit(1)).first()
    if recent is not None:
        return db.get(Season, recent[0]), "la que tiene más partidos recientes"
    anyone = db.execute(
        select(Competition.season_id).select_from(Match).join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .group_by(Competition.season_id).order_by(func.count(Match.id).desc(), Competition.season_id.desc()).limit(1)).first()
    if anyone is not None:
        return db.get(Season, anyone[0]), "la que tiene más partidos (ninguna tiene partidos recientes)"
    return db.scalar(select(Season).order_by(Season.id.desc()).limit(1)), "la más reciente cargada"


def _season_from_ref(db: Session, value: str) -> Season | None:
    text = value.strip()
    m = re.fullmatch(r"(?:season:)?(\d+)", text.lower())
    if m:
        return db.get(Season, int(m.group(1)))
    wanted = norm_name(text)
    if not wanted:
        raise InvalidParam("season inválida; usa season:<id> o la etiqueta (p. ej. 2026-2027)")
    for s in db.scalars(select(Season)):
        if wanted in (norm_name(s.label or ""), norm_name(s.name or ""), norm_name(s.external_id)):
            return s
    return None


_FEMALE = re.compile(r"\b(femenin\w*|fem)\b")
_MALE = re.compile(r"\bmasculin\w*\b")


def comp_info(cid: int, name: str | None, category: str, gender: str, kind: str, review_status: str) -> CompInfo:
    """Atributos de 0007 si la competición está clasificada (`auto|confirmed`); si no, deducidos del nombre (aproximados)."""
    if review_status in ("auto", "confirmed"):
        return CompInfo(cid, name or "", category, gender, kind, "catalog")
    folded = norm_name(name or "")
    tax = classify(name or "", load_aliases())
    gen = "female" if _FEMALE.search(folded) else ("male" if _MALE.search(folded) else "unknown")
    return CompInfo(cid, name or "", tax.age, gen, tax.kind, "name")


def build_scope(db: Session, today: date, f: Filters, *, kind: str | None = None, drop_unknown_category: bool = False) -> Scope:
    category = _valid(f.category, CATEGORIES, "category")
    gender = _valid(GENDER_ALIASES.get((f.gender or "").strip().lower(), f.gender), GENDERS, "gender")
    notes: list[str] = []
    if f.season is not None:
        season = _season_from_ref(db, f.season)
        if season is None:
            return Scope(None, {}, [f"temporada «{f.season}» no encontrada"])
    else:
        season, why = _default_season(db, today)
        if season is None:
            return Scope(None, {}, ["no hay temporadas cargadas"])
        notes.append(f"temporada por defecto: {season_label(season)} ({why})")
    rows = db.execute(select(Competition.id, Competition.name, Competition.category, Competition.gender, Competition.kind,
                             Competition.review_status).where(Competition.season_id == season.id).order_by(Competition.id)).all()
    comps = {r[0]: comp_info(*r) for r in rows}
    if f.competition:
        m = re.fullmatch(r"(?:comp:)?(\d+)", f.competition.strip().lower())
        if m:
            comps = {k: v for k, v in comps.items() if k == int(m.group(1))}
        else:
            words = tokens_of(f.competition)
            if not words:
                raise InvalidParam("competition inválida; usa comp:<id> o parte del nombre")
            comps = {k: v for k, v in comps.items() if all(w in norm_name(v.name).split() for w in words)}
        if not comps:
            notes.append(f"competición «{f.competition}» no encontrada en la temporada")
    by_name = sum(1 for c in comps.values() if c.source == "name")
    filtering = bool(category or gender or kind or drop_unknown_category)
    if category:
        unknown = [c for c in comps.values() if c.category == "unknown"]
        comps = {k: v for k, v in comps.items() if v.category == category}
        if unknown:
            notes.append(f"{len(unknown)} competiciones sin categoría clasificada no se incluyen en el filtro category={category}")
    elif drop_unknown_category:
        unknown = [c for c in comps.values() if c.category == "unknown"]
        comps = {k: v for k, v in comps.items() if v.category != "unknown"}
        if unknown:
            listed = ", ".join(c.name for c in unknown[:5]) + ("…" if len(unknown) > 5 else "")
            notes.append(f"excluidas {len(unknown)} competiciones con categoría unknown (sin clasificar): {listed}")
    if gender:
        unknown_g = sum(1 for c in comps.values() if c.gender == "unknown")
        comps = {k: v for k, v in comps.items() if v.gender == gender}
        if unknown_g:
            notes.append(f"{unknown_g} competiciones sin género clasificado no se incluyen en el filtro gender={gender}")
    if kind:
        comps = {k: v for k, v in comps.items() if v.kind == kind}
    if filtering and by_name:
        notes.append(f"{by_name} competiciones de la temporada se han clasificado por su nombre (no están clasificadas en el catálogo): "
                     "categoría, género y tipo son aproximados")
    return Scope(season, comps, notes)


def _valid(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value is None or value == "":
        return None
    v = value.strip().lower()
    if v not in allowed:
        raise InvalidParam(f"{name} inválido; valores: {', '.join(allowed)}")
    return v


# ----------------------------------------------------------------------------- frescura y cobertura
def matches_freshness(db: Session, *, group_ids: Collection[int] = (), match_ids: Collection[int] = ()) -> datetime | None:
    """Última captura válida de partidos: `dataset_refreshes` de los grupos y observaciones de los partidos indicados."""
    refreshed = obs = None
    if group_ids:
        refreshed = db.scalar(select(func.max(DatasetRefresh.last_valid_at)).where(
            DatasetRefresh.dataset == "matches", DatasetRefresh.group_id.in_(list(group_ids))))
    if match_ids:
        obs = db.scalar(select(func.max(MatchObservation.observed_at)).where(MatchObservation.match_id.in_(list(match_ids))))
    elif group_ids:
        obs = db.scalar(select(func.max(MatchObservation.observed_at)).join(Match, Match.id == MatchObservation.match_id)
                        .where(Match.group_id.in_(list(group_ids))))
    return _max(refreshed, obs)


def report_coverage_note(db: Session, comp_ids: Sequence[int]) -> str:
    done, with_report = db.execute(
        select(func.count(Match.id), func.count(MatchReport.id)).select_from(Match)
        .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
        .outerjoin(MatchReport, MatchReport.match_id == Match.id)
        .where(CompetitionGroup.competition_id.in_(list(comp_ids)), Match.status == "finished")).one()
    return (f"datos de actas almacenadas: {with_report} de {done} partidos finalizados del ámbito tienen acta; los recuentos son "
            "parciales y que algo no figure no prueba que no ocurriera")


def _latest_snapshots(db: Session, model: Any, comp_ids: Sequence[int], order: list[Any]) -> dict[int, tuple[int, datetime]]:
    """Última captura `ok` de cada grupo de las competiciones: {group_id: (snapshot_id, fetched_at)}."""
    ranked = (select(model.group_id.label("group_id"), model.id.label("id"), model.fetched_at.label("fetched_at"),
                     func.row_number().over(partition_by=model.group_id, order_by=order).label("rn"))
              .join(CompetitionGroup, CompetitionGroup.id == model.group_id)
              .where(model.status == "ok", CompetitionGroup.competition_id.in_(list(comp_ids))).subquery())
    stmt = select(ranked.c.group_id, ranked.c.id, ranked.c.fetched_at).where(ranked.c.rn == 1)
    return {gid: (sid, at) for gid, sid, at in db.execute(stmt)}


def _scorer_snapshots(db: Session, comp_ids: Sequence[int]) -> dict[int, tuple[int, datetime]]:
    return _latest_snapshots(db, ScorerSnapshot, comp_ids, [ScorerSnapshot.last_seen_at.desc(), ScorerSnapshot.id.desc()])


def _standings_snapshots(db: Session, comp_ids: Sequence[int]) -> dict[int, tuple[int, datetime]]:
    return _latest_snapshots(db, StandingsSnapshot, comp_ids, [
        StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(), StandingsSnapshot.id.desc()])


def _empty(scope: Scope, **extra: Any) -> dict[str, Any]:
    return envelope([], 0, scope.notes, None, scope, **extra)


# ----------------------------------------------------------------------------- 1. jugadores
def players(db: Session, scope: Scope, name: str, limit: int) -> dict[str, Any]:
    tokens = _needed_tokens(name)
    if not scope.comps:
        return _empty(scope, suggestions=[])
    ids = scope.ids
    n = sql_norm(Player.display_name)
    joined = (select(Player.id, Player.display_name, Player.external_id, Team.id.label("team_id"), Team.name.label("team_name"),
                     CompetitionGroup.id.label("group_id"), CompetitionGroup.name.label("group_name"),
                     Competition.id.label("comp_id"), Competition.name.label("comp_name"))
              .select_from(Player).join(PlayerTeamMembership, PlayerTeamMembership.player_id == Player.id)
              .join(Team, Team.id == PlayerTeamMembership.team_id).join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
              .join(Competition, Competition.id == CompetitionGroup.competition_id).where(Competition.id.in_(ids)))
    rows = db.execute(joined.add_columns(func.count().over().label("total")).where(words_match(n, tokens))
                      .order_by(Competition.id, CompetitionGroup.id, Team.name, Player.display_name, Player.id).limit(limit)).all()
    notes = list(scope.notes)
    if not rows:
        suggestions = _suggest_players(db, ids, name)
        if suggestions:
            listed = ", ".join(s["name"] for s in suggestions)
            notes.append(f"«{name}» no figura; nombres parecidos: {listed}. Son sugerencias por similitud, no el jugador pedido: "
                         "confirma cuál antes de usar sus datos")
        else:
            notes.append(f"«{name}» no figura en las actas almacenadas de la temporada y no hay nombres parecidos "
                         "(que no aparezca no prueba que no exista)")
        return envelope([], 0, notes, None, scope, suggestions=suggestions)
    total = int(rows[0].total)
    pids = {r.id for r in rows}
    exts = {r.external_id for r in rows}
    obs = aliased(MatchReportObservation)
    apps: dict[tuple[int, int], int] = {
        (pid, tid): cnt for pid, tid, cnt in db.execute(
            select(MatchPlayer.player_id, MatchPlayer.team_id, func.count(MatchPlayer.id)).join(obs, obs.id == MatchPlayer.observation_id)
            .where(latest(obs), MatchPlayer.player_id.in_(pids)).group_by(MatchPlayer.player_id, MatchPlayer.team_id))
        if pid is not None}
    goals: dict[tuple[int, int], int] = {
        (pid, tid): cnt for pid, tid, cnt in db.execute(
            select(MatchEvent.player_id, MatchEvent.team_id, func.count(MatchEvent.id)).join(obs, obs.id == MatchEvent.observation_id)
            .where(latest(obs), MatchEvent.event_type == "goal", MatchEvent.player_id.in_(pids))
            .group_by(MatchEvent.player_id, MatchEvent.team_id)) if pid is not None}
    group_ids = {r.group_id for r in rows}
    snaps = _scorer_snapshots(db, list({r.comp_id for r in rows}))
    official: dict[tuple[int, int | None, str], Any] = {}
    snap_group = {sid: gid for gid, (sid, _) in snaps.items()}
    if snap_group:
        for r in db.execute(select(ScorerRow.snapshot_id, ScorerRow.player_id, ScorerRow.player_external_id, ScorerRow.goals,
                                   ScorerRow.matches_played).where(ScorerRow.snapshot_id.in_(list(snap_group)),
                                                                   or_(ScorerRow.player_id.in_(pids), ScorerRow.player_external_id.in_(exts)))):
            official[(snap_group[r.snapshot_id], r.player_id, r.player_external_id)] = r
    items = []
    missing_official = False
    for r in rows:
        status, off_goals, off_matches = "no_table", None, None
        if r.group_id in snaps:
            hit = official.get((r.group_id, r.id, r.external_id)) or next(
                (v for (g, p, e), v in official.items() if g == r.group_id and (p == r.id or e == r.external_id)), None)
            status = "listed" if hit is not None else "not_listed"
            off_goals, off_matches = (hit.goals, hit.matches_played) if hit is not None else (None, None)
        missing_official = missing_official or status != "listed"
        items.append({
            "ref": ref("player", r.id), "name": r.display_name, "team": ref_obj("team", r.team_id, r.team_name),
            "competition": ref_obj("comp", r.comp_id, r.comp_name), "group": ref_obj("group", r.group_id, r.group_name),
            "matches_in_reports": apps.get((r.id, r.team_id), 0), "goals_in_reports": goals.get((r.id, r.team_id), 0),
            "official": {"status": status, "goals": off_goals, "matches": off_matches}})
    notes.append(report_coverage_note(db, ids))
    if missing_official:
        notes.append("official.status: listed = figura en la tabla oficial de goleadores; not_listed = no figura (solo se publican "
                     "jugadores con goles); no_table = el grupo no tiene tabla oficial válida ingerida")
    fresh = _max(db.scalar(select(func.max(MatchReportObservation.observed_at)).join(MatchReport, MatchReport.id == MatchReportObservation.report_id)
                           .join(Match, Match.id == MatchReport.match_id).where(Match.group_id.in_(list(group_ids)))),
                 *[snaps[g][1] for g in group_ids if g in snaps])
    return envelope(items, total, notes, fresh, scope)


def _suggest_players(db: Session, comp_ids: list[int], name: str) -> list[dict[str, Any]]:
    query = norm_name(name)
    sim = func.similarity(sql_norm(Player.display_name), query)
    rows = db.execute(
        select(Player.id, Player.display_name, func.max(sim).label("sim"), func.min(Team.name).label("team"))
        .select_from(Player).join(PlayerTeamMembership, PlayerTeamMembership.player_id == Player.id)
        .join(Team, Team.id == PlayerTeamMembership.team_id).join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
        .where(CompetitionGroup.competition_id.in_(comp_ids), sim >= SIMILARITY_MIN)
        .group_by(Player.id, Player.display_name).order_by(func.max(sim).desc(), Player.display_name, Player.id).limit(MAX_SUGGESTIONS)).all()
    return [{"ref": ref("player", r.id), "name": r.display_name, "team": r.team, "similarity": round(float(r.sim), 3)} for r in rows]


# ----------------------------------------------------------------------------- 2. árbitros
def _official_base(scope: Scope, role: str | None = None) -> Any:
    obs = aliased(MatchReportObservation)
    home, away = aliased(Team), aliased(Team)
    n = sql_norm(MatchOfficial.display_name)
    stmt = (select(n.label("n"), MatchOfficial.display_name, MatchOfficial.role, Match.id.label("match_id"), Match.scheduled_at,
                   Match.scheduled_date, Match.home_score, Match.away_score, Match.status, home.name.label("home"),
                   away.name.label("away"), Competition.id.label("comp_id"), Competition.name.label("comp_name"),
                   CompetitionGroup.id.label("group_id"), CompetitionGroup.name.label("group_name"), obs.observed_at)
            .select_from(MatchOfficial).join(obs, obs.id == MatchOfficial.observation_id)
            .join(MatchReport, MatchReport.id == obs.report_id).join(Match, Match.id == MatchReport.match_id)
            .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
            .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
            .join(Competition, Competition.id == CompetitionGroup.competition_id)
            .where(latest(obs), Competition.id.in_(scope.ids)))
    if role is not None:
        stmt = stmt.where(sql_norm(MatchOfficial.role) == norm_name(role))
    return stmt, n


def _local_parts(at: datetime | None, day: date | None) -> tuple[str | None, str | None, str | None]:
    """(fecha, hora HH:MM, instante local con desfase) en Europe/Madrid; sin hora conocida, solo la fecha."""
    if at is not None:
        local = at.astimezone(MADRID)
        return local.date().isoformat(), local.strftime("%H:%M"), local.isoformat(timespec="minutes")
    return (day.isoformat() if day else None), None, None


def officials(db: Session, scope: Scope, name: str, include_matches: bool, limit: int) -> dict[str, Any]:
    tokens = _needed_tokens(name)
    if not scope.comps:
        return _empty(scope, suggestions=[])
    stmt, n = _official_base(scope)
    rows = db.execute(stmt.where(words_match(n, tokens))).all()
    notes = list(scope.notes)
    if not rows:
        query = norm_name(name)
        base, _ = _official_base(scope)
        sub = base.with_only_columns(n.label("n"), MatchOfficial.display_name.label("display_name")).subquery()
        sugg = db.execute(select(func.max(sub.c.display_name).label("name"), func.max(func.similarity(sub.c.n, query)).label("sim"))
                          .where(func.similarity(sub.c.n, query) >= SIMILARITY_MIN).group_by(sub.c.n)
                          .order_by(func.max(func.similarity(sub.c.n, query)).desc(), func.max(sub.c.display_name)).limit(MAX_SUGGESTIONS)).all()
        suggestions = [{"name": s.name, "similarity": round(float(s.sim), 3)} for s in sugg]
        if suggestions:
            notes.append(f"«{name}» no figura; nombres parecidos: {', '.join(s['name'] for s in suggestions)}. Son sugerencias por "
                         "similitud, no el árbitro pedido: confirma cuál antes de usar sus datos")
        else:
            notes.append(f"«{name}» no figura como árbitro en las actas almacenadas de la temporada y no hay nombres parecidos")
        return envelope([], 0, notes, None, scope, suggestions=suggestions)
    by_person: dict[str, list[Any]] = {}
    for r in rows:
        by_person.setdefault(r.n, []).append(r)
    ordered = sorted(by_person.values(), key=lambda rs: (-len({r.match_id for r in rs}), norm_name(rs[0].display_name)))
    items = []
    fresh = None
    for rs in ordered[:limit]:
        fresh = _max(fresh, *[r.observed_at for r in rs])
        match_ids = {r.match_id for r in rs}
        item: dict[str, Any] = {
            "name": Counter(r.display_name for r in rs).most_common(1)[0][0], "matches": len(match_ids),
            "roles": dict(sorted(Counter(r.role for r in rs).items())),
            "competitions": _tally(rs, "comp_id", "comp_name", "comp"), "groups": _tally(rs, "group_id", "group_name", "group")}
        if include_matches:
            listed = sorted({r.match_id: r for r in rs}.values(), key=lambda r: (_local_parts(r.scheduled_at, r.scheduled_date)[0] or "",
                                                                              _local_parts(r.scheduled_at, r.scheduled_date)[1] or "", r.match_id))
            item["matches_list"] = [_official_match(r) for r in listed[:MAX_OFFICIAL_MATCHES]]
            if len(listed) > MAX_OFFICIAL_MATCHES:
                item["matches_list_truncated"] = True
        items.append(item)
    notes.append(report_coverage_note(db, scope.ids))
    return envelope(items, len(ordered), notes, fresh, scope, suggestions=[])


def _tally(rows: list[Any], id_attr: str, name_attr: str, kind: str) -> list[dict[str, Any]]:
    counts: dict[int, set[int]] = {}
    names: dict[int, str] = {}
    for r in rows:
        counts.setdefault(getattr(r, id_attr), set()).add(r.match_id)
        names[getattr(r, id_attr)] = getattr(r, name_attr)
    return [{**ref_obj(kind, i, names[i]), "matches": len(m)} for i, m in sorted(counts.items())]


def _official_match(r: Any) -> dict[str, Any]:
    day, hour, _ = _local_parts(r.scheduled_at, r.scheduled_date)
    return {"ref": ref("match", r.match_id), "date": day, "time": hour, "home": r.home, "away": r.away,
            "score": {"home": r.home_score, "away": r.away_score} if r.home_score is not None else None, "status": r.status,
            "role": r.role, "competition": ref_obj("comp", r.comp_id, r.comp_name), "group": ref_obj("group", r.group_id, r.group_name)}


def officials_ranking(db: Session, scope: Scope, role: str, limit: int) -> dict[str, Any]:
    if not norm_name(role):
        raise InvalidParam("role debe contener letras")
    if not scope.comps:
        return _empty(scope)
    base, _ = _official_base(scope, role)
    sub = base.subquery()
    matches = func.count(func.distinct(sub.c.match_id))
    stmt = (select(func.max(sub.c.display_name).label("name"), matches.label("matches"), func.array_agg(func.distinct(sub.c.role)).label("roles"),
                   func.count(func.distinct(sub.c.comp_id)).label("competitions"), func.max(sub.c.observed_at).label("fresh"),
                   func.count().over().label("total"))
            .group_by(sub.c.n).order_by(matches.desc(), func.max(sub.c.display_name)).limit(limit))
    rows = db.execute(stmt).all()
    items = [{"rank": i, "name": r.name, "matches": r.matches, "roles": sorted(r.roles), "competitions": r.competitions}
             for i, r in enumerate(rows, 1)]
    notes = [*scope.notes, report_coverage_note(db, scope.ids),
             f"ranking por partidos con ese rol («{role}») en actas almacenadas, no por designaciones oficiales"]
    return envelope(items, int(rows[0].total) if rows else 0, notes, _max(*[r.fresh for r in rows]), scope)


# ----------------------------------------------------------------------------- 3. partidos de todos los grupos
def matches(db: Session, scope: Scope, *, date_from: date | None, date_to: date | None, time_from: time | None, time_to: time | None,
            venue: str | None, status: str | None, order: str, limit: int) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise InvalidParam("date_from no puede ser posterior a date_to")
    if time_from and time_to and time_from > time_to:
        raise InvalidParam("time_from no puede ser posterior a time_to")
    if status is not None and status not in STATUSES:
        raise InvalidParam(f"status inválido; valores: {', '.join(STATUSES)}")
    if order not in ("asc", "desc"):
        raise InvalidParam("order debe ser asc o desc")
    if not scope.comps:
        return _empty(scope)
    local = func.timezone("Europe/Madrid", Match.scheduled_at)
    local_date = func.coalesce(cast(local, Date), Match.scheduled_date)
    home, away = aliased(Team), aliased(Team)
    has_report = select(MatchReport.id).where(MatchReport.match_id == Match.id).exists()
    stmt = (select(Match.id, Match.scheduled_at, Match.scheduled_date, Match.venue, Match.status, Match.home_score, Match.away_score,
                   Match.home_team_id, Match.away_team_id, home.name.label("home"), away.name.label("away"), Round.visible_number,
                   Competition.id.label("comp_id"), Competition.name.label("comp_name"), CompetitionGroup.id.label("group_id"),
                   CompetitionGroup.name.label("group_name"), has_report.label("has_report"), func.count().over().label("total"))
            .select_from(Match).join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
            .join(Competition, Competition.id == CompetitionGroup.competition_id).join(Round, Round.id == Match.round_id)
            .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
            .where(Competition.id.in_(scope.ids)))
    notes = list(scope.notes)
    if date_from:
        stmt = stmt.where(local_date >= date_from)
    if date_to:
        stmt = stmt.where(local_date <= date_to)
    if time_from or time_to:
        stmt = stmt.where(Match.scheduled_at.is_not(None))
        if time_from:
            stmt = stmt.where(cast(local, Time) >= time_from)
        if time_to:
            stmt = stmt.where(cast(local, Time) <= time_to)
        notes.append("filtro horario: solo partidos con hora conocida (hora de Europe/Madrid)")
    if venue:
        words = tokens_of(venue)
        if not words:
            raise InvalidParam("venue debe contener letras o números")
        stmt = stmt.where(and_(*[sql_norm(Match.venue).like(f"%{w}%") for w in words]))
    if status:
        stmt = stmt.where(Match.status == status)
    asc = order == "asc"
    stmt = stmt.order_by(local_date.asc() if asc else local_date.desc(), Match.scheduled_at.asc().nulls_last() if asc else Match.scheduled_at.desc().nulls_last(),
                         Match.id)
    rows = db.execute(stmt.limit(limit)).all()
    items = []
    for r in rows:
        day, hour, iso = _local_parts(r.scheduled_at, r.scheduled_date)
        items.append({
            "ref": ref("match", r.id), "date": day, "time": hour, "local_datetime": iso, "round": r.visible_number,
            "competition": ref_obj("comp", r.comp_id, r.comp_name), "group": ref_obj("group", r.group_id, r.group_name),
            "home": ref_obj("team", r.home_team_id, r.home), "away": ref_obj("team", r.away_team_id, r.away),
            "score": {"home": r.home_score, "away": r.away_score} if r.home_score is not None else None,
            "status": r.status, "has_report": bool(r.has_report), "venue": r.venue})
    fresh = matches_freshness(db, match_ids=[r.id for r in rows], group_ids=list({r.group_id for r in rows}))
    return envelope(items, int(rows[0].total) if rows else 0, notes, fresh, scope)


# ----------------------------------------------------------------------------- 4. clubes por nombre
@dataclass
class ClubTeam:
    id: int
    name: str
    squad: str | None
    group_id: int
    group_name: str
    comp: CompInfo
    club_id: int | None
    club_name: str | None


@dataclass
class ClubGroup:
    base: str
    official: bool
    club_id: int | None
    display: str
    teams: list[ClubTeam] = field(default_factory=list)
    exact: bool = False


def _club_groups(db: Session, scope: Scope, name: str) -> list[ClubGroup]:
    """Equipos de la temporada cuyo nombre base contiene todas las palabras de `name`, agrupados por club."""
    query_tokens = club_base_tokens(name) or _needed_tokens(name)
    if not tokens_of(name):
        raise InvalidParam("name debe contener letras o números")
    if not scope.comps:
        return []
    rows = db.execute(
        select(Team.id, Team.name, Team.squad, CompetitionGroup.id, CompetitionGroup.name, CompetitionGroup.competition_id,
               TeamEntity.club_id, Club.name)
        .select_from(Team).join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
        .outerjoin(TeamEntity, TeamEntity.id == Team.team_entity_id).outerjoin(Club, Club.id == TeamEntity.club_id)
        .where(CompetitionGroup.competition_id.in_(scope.ids), _loose(query_tokens))
        .order_by(CompetitionGroup.competition_id, CompetitionGroup.id, Team.name, Team.id)).all()
    groups: dict[tuple[str, Any], ClubGroup] = {}
    for tid, tname, squad, gid, gname, cid, club_id, club_name in rows:
        base_tokens = club_base_tokens(tname or "")
        if not all(t in base_tokens for t in query_tokens):
            continue
        base = " ".join(base_tokens)
        key = ("club", club_id) if club_id is not None else ("name", base)
        group = groups.setdefault(key, ClubGroup(base, club_id is not None, club_id, (club_name or base).upper() if club_id else base.upper()))
        group.teams.append(ClubTeam(tid, tname or "", squad or squad_of(tname or ""), gid, gname or "", scope.comps[cid], club_id, club_name))
    result = list(groups.values())
    wanted = " ".join(query_tokens)
    for g in result:
        g.exact = g.base == wanted
    return sorted(result, key=lambda g: (not g.exact, g.base, g.club_id or 0))


def _loose(tokens: Sequence[str]) -> ColumnElement[bool]:
    """Prefiltro SQL laxo: cada palabra aparece como subcadena del nombre normalizado (la verificación fina se hace sobre el nombre base)."""
    col = func.trim(func.regexp_replace(func.regexp_replace(func.kb_unaccent(func.lower(Team.name)), r"\.", "", "g"), "[^a-z0-9]+", " ", "g"))
    return and_(*[col.like(f"%{t}%") for t in tokens])


def _standing_map(db: Session, teams: list[ClubTeam]) -> tuple[dict[int, dict[str, Any]], datetime | None]:
    comp_ids = list({t.comp.id for t in teams})
    snaps = _standings_snapshots(db, comp_ids)
    by_snap = {sid: gid for gid, (sid, _) in snaps.items()}
    out: dict[int, dict[str, Any]] = {}
    if by_snap:
        wanted = {t.id for t in teams}
        for r in db.execute(select(StandingsRow.snapshot_id, StandingsRow.team_id, StandingsRow.position, StandingsRow.points,
                                   StandingsRow.played).where(StandingsRow.snapshot_id.in_(list(by_snap)), StandingsRow.team_id.in_(wanted))):
            out[r.team_id] = {"position": r.position, "points": r.points, "played": r.played, "official": True}
    return out, _max(*[at for _, at in snaps.values()])


def _team_item(t: ClubTeam, standing: dict[str, Any] | None) -> dict[str, Any]:
    return {"ref": ref("team", t.id), "name": t.name, "squad": t.squad, "competition": ref_obj("comp", t.comp.id, t.comp.name),
            "group": ref_obj("group", t.group_id, t.group_name), "category": t.comp.category, "gender": t.comp.gender,
            "standing": standing}


def _club_header(g: ClubGroup, ambiguous: bool) -> dict[str, Any]:
    return {"name": g.display, "base_name": g.base, "club_ref": ref("club", g.club_id),
            "club_link": "official" if g.official else "name_match", "exact": g.exact, "ambiguous": ambiguous}


def _club_notes(groups: list[ClubGroup], notes: list[str]) -> list[str]:
    out = list(notes)
    if any(not g.official for g in groups):
        out.append(CLUB_LINK_NOTE)
    if len(groups) > 1:
        names = ", ".join(g.display for g in groups[:5])
        out.append(f"el nombre coincide con {len(groups)} clubes de nombre distinto ({names}); son candidatos, "
                   "pide confirmación antes de asumir cuál es el pedido")
    return out


def clubs_search(db: Session, scope: Scope, name: str, limit: int) -> dict[str, Any]:
    groups = _club_groups(db, scope, name)
    notes = _club_notes(groups, scope.notes)
    if not groups:
        notes.append(f"«{name}» no coincide con ningún equipo de la temporada")
        return envelope([], 0, notes, None, scope)
    shown = groups[:limit]
    standings, fresh = _standing_map(db, [t for g in shown for t in g.teams])
    items = [{**_club_header(g, len(groups) > 1), "teams_count": len(g.teams),
              "teams": [_team_item(t, standings.get(t.id)) for t in g.teams]} for g in shown]
    if any(t["standing"] is None for it in items for t in it["teams"]):
        notes.append("standing: posición y puntos de la clasificación OFICIAL (última captura válida); null si el grupo no tiene clasificación ingerida")
    return envelope(items, len(groups), notes, _max(fresh, matches_freshness(db, group_ids=list({t.group_id for g in shown for t in g.teams}))), scope)


# ----------------------------------------------------------------------------- 5. resultados por club
def clubs_results(db: Session, scope: Scope, name: str, limit: int) -> dict[str, Any]:
    groups = _club_groups(db, scope, name)
    notes = _club_notes(groups, scope.notes)
    if not groups:
        notes.append(f"«{name}» no coincide con ningún equipo de la temporada")
        return envelope([], 0, notes, None, scope)
    flat = [(g, t) for g in groups for t in sorted(g.teams, key=lambda t: (t.comp.id, t.group_id, t.name, t.id))]
    page = flat[:limit]
    ids = {t.id for _, t in page}
    home, away = aliased(Team), aliased(Team)
    rows = db.execute(
        select(Match.id, Match.scheduled_at, Match.scheduled_date, Match.home_team_id, Match.away_team_id, Match.home_score,
               Match.away_score, home.name.label("home"), away.name.label("away"), Round.visible_number)
        .select_from(Match).join(Round, Round.id == Match.round_id).join(home, home.id == Match.home_team_id)
        .join(away, away.id == Match.away_team_id)
        .where(Match.status == "finished", Match.home_score.is_not(None), or_(Match.home_team_id.in_(ids), Match.away_team_id.in_(ids)))
        .order_by(func.coalesce(Match.scheduled_date, cast(Match.scheduled_at, Date)).desc(), Match.scheduled_at.desc().nulls_last(),
                  Match.id.desc())).all()
    items: list[dict[str, Any]] = []
    truncated = False
    for g, t in page:
        mine = [r for r in rows if t.id in (r.home_team_id, r.away_team_id)]
        summary = {"played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0}
        results = []
        for r in mine:
            is_home = r.home_team_id == t.id
            gf, ga = (r.home_score, r.away_score) if is_home else (r.away_score, r.home_score)
            summary["played"] += 1
            summary["goals_for"] += gf
            summary["goals_against"] += ga
            outcome = "won" if gf > ga else ("drawn" if gf == ga else "lost")
            summary[outcome] += 1
            day, hour, _ = _local_parts(r.scheduled_at, r.scheduled_date)
            results.append({"match": ref("match", r.id), "date": day, "time": hour, "round": r.visible_number, "home": r.home, "away": r.away,
                            "score": {"home": r.home_score, "away": r.away_score}, "venue_side": "home" if is_home else "away",
                            "result": {"won": "G", "drawn": "E", "lost": "P"}[outcome]})
        truncated = truncated or len(results) > MAX_TEAM_RESULTS
        items.append({"club": _club_header(g, len(groups) > 1), "team": ref_obj("team", t.id, t.name),
                      "competition": ref_obj("comp", t.comp.id, t.comp.name), "group": ref_obj("group", t.group_id, t.group_name),
                      "category": t.comp.category, "gender": t.comp.gender, "summary": summary, "results_count": len(results),
                      "results_shown": min(len(results), MAX_TEAM_RESULTS), "results": results[:MAX_TEAM_RESULTS]})
    if truncated:
        notes.append(f"results: solo los {MAX_TEAM_RESULTS} más recientes por equipo; summary cuenta todos los finalizados")
    notes.append("resumen calculado sobre partidos finalizados con marcador (G ganados, E empatados, P perdidos); no incluye sanciones oficiales")
    fresh = matches_freshness(db, group_ids=list({t.group_id for _, t in page}))
    return envelope(items, len(flat), notes, fresh, scope)


# ----------------------------------------------------------------------------- 6. ranking de goleadores
def scorers_ranking(db: Session, scope: Scope, limit: int) -> dict[str, Any]:
    notes = [*scope.notes, DIFFERENT_ROUNDS_NOTE, "goles de la tabla OFICIAL de goleadores (última captura válida de cada grupo)"]
    if not scope.comps:
        return envelope([], 0, notes, None, scope)
    snaps = _scorer_snapshots(db, scope.ids)
    total_groups = db.scalar(select(func.count(CompetitionGroup.id)).where(CompetitionGroup.competition_id.in_(scope.ids))) or 0
    notes.append(f"{len(snaps)} de {total_groups} grupos del ámbito tienen tabla oficial de goleadores válida; el resto no cuenta")
    if not snaps:
        return envelope([], 0, notes, None, scope)
    rows = db.execute(
        select(ScorerRow.player_id, ScorerRow.player_name, ScorerRow.team_id, ScorerRow.team_name, ScorerRow.goals, ScorerRow.matches_played,
               CompetitionGroup.id.label("group_id"), CompetitionGroup.name.label("group_name"), Competition.id.label("comp_id"),
               Competition.name.label("comp_name"), func.count().over().label("total"))
        .select_from(ScorerRow).join(ScorerSnapshot, ScorerSnapshot.id == ScorerRow.snapshot_id)
        .join(CompetitionGroup, CompetitionGroup.id == ScorerSnapshot.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .where(ScorerRow.snapshot_id.in_([s for s, _ in snaps.values()]))
        .order_by(ScorerRow.goals.desc(), sql_norm(ScorerRow.player_name), ScorerRow.team_name, ScorerRow.id).limit(limit)).all()
    items = [{"rank": i, "player": {"ref": ref("player", r.player_id), "name": r.player_name}, "team": ref_obj("team", r.team_id, r.team_name),
              "competition": ref_obj("comp", r.comp_id, r.comp_name), "group": ref_obj("group", r.group_id, r.group_name),
              "goals": r.goals, "matches": r.matches_played} for i, r in enumerate(rows, 1)]
    return envelope(items, int(rows[0].total) if rows else 0, notes, _max(*[at for _, at in snaps.values()]), scope)


# ----------------------------------------------------------------------------- 7. ranking de equipos
def teams_ranking(db: Session, scope: Scope, metric: str, order: str | None, min_played: int, limit: int) -> dict[str, Any]:
    if metric not in TEAM_METRICS:
        raise InvalidParam(f"metric inválida; valores: {', '.join(TEAM_METRICS)}")
    if order not in (None, "asc", "desc"):
        raise InvalidParam("order debe ser asc o desc")
    direction = order or ("asc" if metric == "goals_against" else "desc")
    notes = [*scope.notes, DIFFERENT_ROUNDS_NOTE,
             f"calculado sobre partidos finalizados con marcador; orden {direction} por {metric}" +
             (" (menos goles encajados primero)" if metric == "goals_against" and direction == "asc" else ""),
             ]
    if metric == "points_per_game":
        notes.append(f"puntos = 3 por victoria y 1 por empate sobre partidos jugados (mínimo {min_played}); la clasificación oficial puede "
                     "diferir por sanciones")
    if not scope.comps:
        return envelope([], 0, notes, None, scope)
    base = (select(Match.group_id, Match.home_team_id.label("team_id"), Match.home_score.label("gf"), Match.away_score.label("ga"))
            .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
            .where(Match.status == "finished", Match.home_score.is_not(None), CompetitionGroup.competition_id.in_(scope.ids)))
    other = (select(Match.group_id, Match.away_team_id.label("team_id"), Match.away_score.label("gf"), Match.home_score.label("ga"))
             .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
             .where(Match.status == "finished", Match.away_score.is_not(None), CompetitionGroup.competition_id.in_(scope.ids)))
    sides = union_all(base, other).subquery()
    won = func.sum(case((sides.c.gf > sides.c.ga, 1), else_=0))
    drawn = func.sum(case((sides.c.gf == sides.c.ga, 1), else_=0))
    played = func.count()
    gf, ga = func.sum(sides.c.gf), func.sum(sides.c.ga)
    agg = (select(sides.c.team_id, sides.c.group_id, played.label("pj"), won.label("w"), drawn.label("d"), gf.label("gf"), ga.label("ga"))
           .group_by(sides.c.team_id, sides.c.group_id).having(played >= min_played).subquery())
    ppg = (3 * agg.c.w + agg.c.d) * 1.0 / cast(agg.c.pj, Numeric)
    value = {"goals_for": agg.c.gf, "goals_against": agg.c.ga, "wins": agg.c.w, "points_per_game": ppg}[metric]
    rows = db.execute(
        select(agg, Team.name.label("team_name"), CompetitionGroup.name.label("group_name"), Competition.id.label("comp_id"),
               Competition.name.label("comp_name"), func.count().over().label("total"))
        .select_from(agg).join(Team, Team.id == agg.c.team_id).join(CompetitionGroup, CompetitionGroup.id == agg.c.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .order_by(value.asc() if direction == "asc" else value.desc(), agg.c.pj.desc(), Team.name, Team.id).limit(limit)).all()
    items = []
    for i, r in enumerate(rows, 1):
        items.append({"rank": i, "team": ref_obj("team", r.team_id, r.team_name), "competition": ref_obj("comp", r.comp_id, r.comp_name),
                      "group": ref_obj("group", r.group_id, r.group_name), "played": r.pj, "won": int(r.w), "drawn": int(r.d),
                      "lost": r.pj - int(r.w) - int(r.d), "goals_for": int(r.gf), "goals_against": int(r.ga),
                      "points_per_game": round((3 * int(r.w) + int(r.d)) / r.pj, 2)})
    fresh = matches_freshness(db, group_ids=list({r.group_id for r in rows})) if rows else None
    return envelope(items, int(rows[0].total) if rows else 0, notes, fresh, scope, metric=metric, order=direction)
