"""Planificador de la ingesta (ingestion.md §3): qué trabajos toca encolar y cuándo (lo que vence dentro del horizonte). `plan` es puro: reloj inyectado, umbrales en
`ScheduleConfig` y estado de entrada (`PlannerState`); no toca red ni base de datos. `gather_state` lee ese estado de PostgreSQL.

Un trabajo vivo es único por `(kind, target_key)`: `plan` devuelve TODAS las franjas pendientes y `collapse` deja una por objetivo
(la más temprana, con la mayor prioridad), que es lo que `enqueue` conservaría. Cuando un trabajo termina, el siguiente `tick`
vuelve a emitir las franjas que aún no se han cubierto (cubierta = hay una consulta terminada posterior a la franja).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    IngestJob,
    Match,
    MatchReport,
    MatchReportObservation,
    Round,
    Season,
)
from futsal.ingestion.queue import jobs
from futsal.ingestion.queue.jobs import canonical_key
from futsal.ingestion.queue.schedule import ScheduleConfig

CALENDAR, ROUND_RESULTS, STANDINGS, SCORERS, DISCOVER_CATALOG = "calendar", "round_results", "standings", "scorers", "discover_catalog"
MATCH_REPORT = "match_report"
REPORT_NONE, REPORT_OPEN, REPORT_CLOSED = "none", "open", "closed"  # estado del acta almacenada (None = no se sabe)
UNRESOLVED = (None, "scheduled", "unknown")  # sin resultado ni explicación
CLOSED_STATUSES = ("finished", "cancelled")


def catalog_target(season: str, game_type: str) -> dict[str, Any]:
    return {"season": season, "game_type": game_type}


def group_target(season: str, competition: str, group: str, game_type: str) -> dict[str, Any]:
    return {"season": season, "competition": competition, "group": group, "game_type": game_type}


def round_target(group: Mapping[str, Any], round_code: str) -> dict[str, Any]:
    return {**{k: group[k] for k in ("season", "competition", "group", "game_type")}, "round": round_code}


def report_target(group: Mapping[str, Any], codacta: str) -> dict[str, Any]:
    return {**{k: group[k] for k in ("season", "competition", "group", "game_type")}, "match": codacta}


@dataclass(frozen=True)
class JobSpec:
    kind: str
    target: dict[str, Any]
    priority: int
    not_before: datetime
    reason: str

    @property
    def target_key(self) -> str:
        return canonical_key(self.target)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "priority": self.priority,
                "not_before": self.not_before.isoformat(), "reason": self.reason}


@dataclass(frozen=True)
class MatchState:
    codacta: str
    round_code: str
    status: str | None  # None: solo se conoce por el calendario
    kickoff: datetime | None  # aware; None si la hora no es conocida
    day: date | None
    report: str | None = None  # acta almacenada: REPORT_NONE | REPORT_OPEN | REPORT_CLOSED; None = no se sabe (calendario)


@dataclass(frozen=True)
class CalendarRound:
    code: str
    number: int | None
    matches: tuple[MatchState, ...]


@dataclass(frozen=True)
class GroupState:
    season: str
    competition: str
    group: str
    game_type: str
    active: bool
    has_standings: bool | None = None
    has_scorers: bool | None = None
    db_matches: tuple[MatchState, ...] = ()
    calendar: tuple[CalendarRound, ...] = ()  # resumen del último calendario (checkpoint)
    last_known_round: int | None = None  # última jornada con algún partido finalizado
    # Última lectura de /api/results por partido: codacta -> (hay_actas, acta_cerrada). Sin entrada = no se sabe.
    report_hints: Mapping[str, tuple[bool, bool]] = field(default_factory=dict)

    @property
    def target(self) -> dict[str, Any]:
        return group_target(self.season, self.competition, self.group, self.game_type)


@dataclass(frozen=True)
class PlannerState:
    season: str
    game_type: str
    groups: tuple[GroupState, ...] = ()
    last_done: Mapping[tuple[str, str], datetime] = field(default_factory=dict)  # (kind, target_key) -> última vez terminado


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _fmt(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d-%m %H:%M")


def _sort_code(code: str) -> tuple[int, str]:
    return (int(code), code) if code.isdigit() else (10**9, code)


def merge_rounds(group: GroupState) -> dict[str, tuple[bool, list[MatchState]]]:
    """Partidos por jornada: el calendario aporta lo que no está en la BD; la BD manda en estado y, si la tiene, en la hora."""
    by_code: dict[str, dict[str, MatchState]] = {}
    for rnd in group.calendar:
        by_code.setdefault(rnd.code, {}).update({m.codacta: m for m in rnd.matches})
    stored: set[str] = set()
    moved: dict[str, list[MatchState]] = {}
    for m in group.db_matches:
        stored.add(m.round_code)
        cal = by_code.get(m.round_code, {}).get(m.codacta)
        by_code.setdefault(m.round_code, {})[m.codacta] = MatchState(
            m.codacta, m.round_code, m.status, m.kickoff or (cal.kickoff if cal else None), m.day or (cal.day if cal else None),
            m.report)
        # Reprogramado: el calendario (diario) trae otra fecha que la BD para un partido sin resultado. No se sabe cuál es la
        # más reciente, así que se planifica con las DOS (coste: alguna consulta de más; nunca se pierde el partido).
        if cal is not None and m.status in UNRESOLVED and cal.day is not None and m.day is not None and cal.day != m.day:
            moved.setdefault(m.round_code, []).append(MatchState(m.codacta, m.round_code, m.status, cal.kickoff, cal.day, m.report))
    return {code: (code in stored, sorted(by_code[code].values(), key=lambda x: x.codacta) + moved.get(code, []))
            for code in sorted(by_code, key=_sort_code)}


def _local_day(m: MatchState, tz: ZoneInfo) -> date | None:
    return m.kickoff.astimezone(tz).date() if m.kickoff else m.day


def _weekend(now: datetime, cfg: ScheduleConfig, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """Ventana del fin de semana activo que contiene `now` (viernes 16:00 -> lunes 12:00 por defecto), o None."""
    local = now.astimezone(tz)
    start_day = local.date()
    while start_day.weekday() != cfg.weekend_start_weekday:
        start_day -= timedelta(days=1)
    start = datetime.combine(start_day, cfg.weekend_start, tzinfo=tz)
    if start > local:
        start -= timedelta(days=7)
    end_day = start.date() + timedelta(days=1)
    while end_day.weekday() != cfg.weekend_end_weekday:
        end_day += timedelta(days=1)
    end = datetime.combine(end_day, cfg.weekend_end, tzinfo=tz)
    return (start, end) if start <= local <= end else None


def _next_sweep(base: datetime, cfg: ScheduleConfig, tz: ZoneInfo) -> datetime:
    """Primer instante >= `base` dentro de la ventana diaria de barridos [sweep_from, sweep_until]."""
    local = base.astimezone(tz)
    if local.time() < cfg.sweep_from:
        return datetime.combine(local.date(), cfg.sweep_from, tzinfo=tz)
    if local.time() > cfg.sweep_until:
        return datetime.combine(local.date() + timedelta(days=1), cfg.sweep_from, tzinfo=tz)
    return base


def _due(last: datetime | None, every: timedelta, now: datetime) -> datetime:
    return now if last is None else max(last + every, now)


def _hours(value: float) -> timedelta:
    return timedelta(hours=value)


def _days(value: float) -> timedelta:
    return timedelta(days=value)


def _round_specs(g: GroupState, code: str, stored: bool, matches: Sequence[MatchState], last_done: Mapping[tuple[str, str], datetime],
                 cfg: ScheduleConfig, now: datetime, tz: ZoneInfo) -> list[JobSpec]:
    target = round_target(g.target, code)
    last = last_done.get((ROUND_RESULTS, canonical_key(target)))
    pri = cfg.priorities
    today = now.astimezone(tz).date()
    out: list[JobSpec] = []

    def add(priority: int, when: datetime, reason: str) -> None:
        out.append(JobSpec(ROUND_RESULTS, target, priority, _utc(when), reason))

    unresolved = [m for m in matches if m.status in UNRESOLVED]
    # 1. En juego: franjas de inicio distintas con hora conocida (las que no tienen hora solo entran en el barrido).
    window = timedelta(minutes=cfg.in_play_window_min)
    franjas = sorted({m.kickoff for m in unresolved if m.kickoff is not None
                      and now - window <= m.kickoff <= now + _hours(cfg.horizon_hours)})
    slots: dict[datetime, str] = {}
    for k in franjas:
        for off in cfg.in_play_offsets_min:
            t = k + timedelta(minutes=off)
            if last is None or last < t:
                slots.setdefault(max(t, now), f"en juego: inicio {_fmt(k, tz)} +{off} min")
    for t, reason in sorted(slots.items()):
        add(pri.in_play, t, reason)
    # 2. Fin de semana activo: barrido cada `sweep_every_hours` dentro de [sweep_from, sweep_until].
    wk = _weekend(now, cfg, tz)
    if wk is not None and any(d is not None and wk[0].date() <= d <= wk[1].date() for d in (_local_day(m, tz) for m in matches)):
        sweep = _next_sweep(_due(last, _hours(cfg.sweep_every_hours), now), cfg, tz)
        if sweep <= wk[1]:
            add(pri.in_play, sweep, "fin de semana activo: barrido")
    # 3. Asentándose: finalizados hace menos de `settling_hours`.
    def settled_ref(m: MatchState) -> datetime:
        return m.kickoff + window if m.kickoff else datetime.combine(m.day or today, time(23, 59), tzinfo=tz)

    if any(m.status == "finished" and now - settled_ref(m) < _hours(cfg.settling_hours) for m in matches):
        add(pri.rest, _due(last, _hours(cfg.settling_every_hours), now), "asentándose")
    # 4. Próxima jornada: partidos abiertos en los próximos `next_round_days`; sirve para obtener la HORA de /api/results.
    open_days = [d for d in (_local_day(m, tz) for m in matches if m.status not in CLOSED_STATUSES) if d is not None]
    if any(today <= d <= today + _days(cfg.next_round_days) for d in open_days):
        add(pri.next_round, _due(last, _hours(cfg.next_round_every_hours), now), "próxima jornada")
    # 5. Pasada atrasada: fecha pasada sin resultado (si nunca se consultó es la carga inicial).
    overdue = sorted(d for d in (_local_day(m, tz) for m in unresolved) if d is not None and d < today)
    if overdue:
        age = today - overdue[-1]
        every = _hours(cfg.past_due_every_hours if age <= _days(cfg.past_due_first_days) else cfg.past_due_then_every_hours)
        initial = last is None and not stored
        recent = age <= _days(cfg.initial_recent_days)
        add(pri.next_round if initial and recent else pri.rest, _due(last, every, now),
            ("carga inicial reciente" if recent else "carga inicial atrasada") if initial else "pasada atrasada (past_due)")
    # 6. Cerrada (semanal 30 días, luego mensual) o lejana (semanal): solo jornadas ya conocidas en la BD.
    if stored and not out:
        if matches and all(m.status in CLOSED_STATUSES for m in matches):
            days = sorted(d for d in (_local_day(m, tz) for m in matches) if d is not None)
            age = today - days[-1] if days else _days(cfg.closed_first_days + 1)
            every = _days(cfg.closed_every_days if age <= _days(cfg.closed_first_days) else cfg.closed_then_every_days)
            add(pri.rest, _due(last, every, now), "cerrada")
        elif any(m.status not in UNRESOLVED or (d := _local_day(m, tz)) is not None and d < today for m in matches):
            add(pri.rest, _due(last, _days(cfg.far_every_days), now), "jugada sin finalizar del todo")
        # Sin partidos jugados ni vencidos y todos a > `next_round_days` o sin fecha: NO hay round_results; la cubre `calendar`.
    return out


def _report_specs(g: GroupState, last_done: Mapping[tuple[str, str], datetime], cfg: ScheduleConfig, now: datetime,
                  tz: ZoneInfo) -> list[JobSpec]:
    """Actas de partidos finalizados: las que faltan y las abiertas recientes. Sin descargar el histórico en cada pasada.

    - `/api/results` (pista de `round_results`) dice si hay acta y si está cerrada: «no hay» -> no se pide (la jornada se vuelve a
      mirar con su propia cadencia); «hay» y no la tenemos, o «cerrada» y la nuestra está abierta -> se pide en cuanto toca.
    - Sin pista (datos anteriores a la cola): espera creciente según la antigüedad del partido y abandono tras `reports.max_days`.
    - Un acta almacenada y cerrada no se vuelve a pedir nunca desde aquí (la revisión completa es `refresh --mode full`).
    """
    rc, pri = cfg.reports, cfg.priorities
    out: list[JobSpec] = []
    for m in g.db_matches:
        if m.status != "finished" or m.report not in (REPORT_NONE, REPORT_OPEN):
            continue
        ref = m.kickoff or (datetime.combine(m.day, time(12, 0), tzinfo=tz) if m.day else None)
        age = now - ref if ref is not None else _days(rc.max_days + 1)
        hint = g.report_hints.get(m.codacta)
        target = report_target(g.target, m.codacta)
        last = last_done.get((MATCH_REPORT, canonical_key(target)))
        if m.report == REPORT_NONE:
            if hint is not None and not hint[0]:
                continue  # la RFFM aún no tiene acta: no se gasta una petición
            if hint is not None:  # publicada según /api/results: sin abandono por antigüedad, pero la espera crece (404 repetidos)
                every = _hours(rc.available_every_hours if age <= _days(rc.first_days) else
                               rc.mid_every_hours if age <= _days(rc.mid_days) else rc.late_every_hours)
                reason = "acta publicada y pendiente"
            elif age > _days(rc.max_days):
                continue  # demasiado antigua y sin pista: solo la recupera una revisión manual
            elif age <= _days(rc.first_days):
                every, reason = _hours(rc.first_every_hours), "acta pendiente (reciente)"
            elif age <= _days(rc.mid_days):
                every, reason = _hours(rc.mid_every_hours), "acta pendiente"
            else:
                every, reason = _hours(rc.late_every_hours), "acta pendiente (atrasada)"
        else:
            if age > _days(rc.open_days):
                continue
            closed_now = hint is not None and hint[1]
            every = _hours(rc.available_every_hours if closed_now else rc.open_every_hours)
            reason = "acta cerrada en la fuente; la almacenada está abierta" if closed_now else "acta abierta: revisión"
        priority = pri.reports if age <= _days(rc.first_days) else pri.rest
        out.append(JobSpec(MATCH_REPORT, target, priority, _utc(_due(last, every, now)), reason))
    return out


def calendar_covered_rounds(state: PlannerState, cfg: ScheduleConfig, now: datetime) -> int:
    """Jornadas de grupos activos que no generan `round_results` porque las cubre el calendario diario (lejanas o sin fecha)."""
    tz, now = ZoneInfo(cfg.timezone), _utc(now)
    n = 0
    for g in state.groups:
        if g.active:
            for code, (_, matches) in merge_rounds(g).items():
                specs = _round_specs(g, code, True, matches, {}, cfg, now, tz)
                if not any(s.target.get("round") == code for s in specs) and all(m.status in UNRESOLVED for m in matches):
                    n += 1
    return n


def _group_specs(g: GroupState, last_done: Mapping[tuple[str, str], datetime], cfg: ScheduleConfig, now: datetime,
                 tz: ZoneInfo) -> list[JobSpec]:
    pri = cfg.priorities
    key = canonical_key(g.target)
    out: list[JobSpec] = []
    today = now.astimezone(tz).date()
    if not g.active:  # competiciones inactivas: solo calendario y tablas finales, con la prioridad más baja
        every = _days(cfg.inactive_every_days)
        out.append(JobSpec(CALENDAR, g.target, pri.inactive, _utc(_due(last_done.get((CALENDAR, key)), every, now)),
                           "competición inactiva: calendario"))
        for kind, flag in ((STANDINGS, g.has_standings), (SCORERS, g.has_scorers)):
            if flag is not False:
                out.append(JobSpec(kind, g.target, pri.inactive, _utc(_due(last_done.get((kind, key)), every, now)),
                                   f"competición inactiva: {kind} finales"))
        return out
    out.append(JobSpec(CALENDAR, g.target, pri.rest, _utc(_due(last_done.get((CALENDAR, key)), _hours(cfg.calendar_every_hours), now)),
                       "calendario diario"))
    window = _days(cfg.tables_activity_days)
    days = [d for m in merge_rounds(g).values() for x in m[1] for d in [_local_day(x, tz)] if d is not None]
    active = any(abs(d - today) <= window for d in days)
    for kind, flag in ((STANDINGS, g.has_standings), (SCORERS, g.has_scorers)):
        if flag is False:
            continue
        last = last_done.get((kind, key))
        if last is None:
            out.append(JobSpec(kind, g.target, pri.tables, _utc(now), "tablas: primera captura"))
        elif active:
            out.append(JobSpec(kind, g.target, pri.tables, _utc(_due(last, _hours(cfg.tables_every_hours), now)),
                               "tablas: al menos una vez al día en un grupo con actividad"))
    return out


def plan_group(g: GroupState, last_done: Mapping[tuple[str, str], datetime], cfg: ScheduleConfig, now: datetime,
               *, only_kinds: Sequence[str] | None = None) -> list[JobSpec]:
    tz = ZoneInfo(cfg.timezone)
    now = _utc(now)
    specs = _group_specs(g, last_done, cfg, now, tz)
    if g.active:
        for code, (stored, matches) in merge_rounds(g).items():
            specs += _round_specs(g, code, stored, matches, last_done, cfg, now, tz)
        specs += _report_specs(g, last_done, cfg, now, tz)
    limit = now + _hours(cfg.horizon_hours)  # lo que vence más allá del horizonte lo emitirá un `tick` posterior
    return [s for s in specs if (only_kinds is None or s.kind in only_kinds) and s.not_before < limit]


def plan(state: PlannerState, cfg: ScheduleConfig, now: datetime) -> list[JobSpec]:
    """Todas las franjas pendientes en `now`, ordenadas por prioridad, hora y (a igualdad) jornada más reciente primero."""
    now = _utc(now)
    specs: list[JobSpec] = []
    cat_last = state.last_done.get((DISCOVER_CATALOG, canonical_key(catalog_target(state.season, state.game_type))))
    daily = now.astimezone(ZoneInfo(cfg.timezone)).month in cfg.catalog_daily_months
    every = _days(1 if daily else cfg.catalog_every_days)
    specs.append(JobSpec(DISCOVER_CATALOG, catalog_target(state.season, state.game_type), cfg.priorities.rest,
                         _utc(_due(cat_last, every, now)), "catálogo diario (junio-septiembre)" if daily else "catálogo semanal"))
    specs = [s for s in specs if s.not_before < now + _hours(cfg.horizon_hours)]
    for g in state.groups:
        specs += plan_group(g, state.last_done, cfg, now)
    return sorted(specs, key=lambda s: (-s.priority, s.not_before, s.kind, s.target.get("group", ""),
                                        -_sort_code(str(s.target.get("round", "0")))[0]))


def collapse(specs: Sequence[JobSpec]) -> list[JobSpec]:
    """Una por `(kind, target)`: la franja más temprana con la mayor prioridad y los motivos reunidos (lo que `enqueue` conserva)."""
    best: dict[tuple[str, str], JobSpec] = {}
    reasons: dict[tuple[str, str], list[str]] = {}
    for s in specs:
        k = (s.kind, s.target_key)
        reasons.setdefault(k, [])
        if s.reason not in reasons[k]:
            reasons[k].append(s.reason)
        cur = best.get(k)
        if cur is None:
            best[k] = s
        else:
            best[k] = JobSpec(s.kind, s.target, max(cur.priority, s.priority), min(cur.not_before, s.not_before), cur.reason)
    out = [JobSpec(s.kind, s.target, s.priority, s.not_before, "; ".join(reasons[k])) for k, s in best.items()]
    return sorted(out, key=lambda s: (-s.priority, s.not_before, s.kind, s.target.get("group", ""),
                                      -_sort_code(str(s.target.get("round", "0")))[0]))


# ---------------------------------------------------------------------------------------------------------------- lectura de la BD


def calendar_summary_rounds(summary: Mapping[str, Any], tz: ZoneInfo) -> tuple[CalendarRound, ...]:
    """Resumen guardado en el checkpoint de `calendar` -> estado de jornadas (partidos sin estado)."""
    out: list[CalendarRound] = []
    for r in summary.get("rounds", []):
        ms: list[MatchState] = []
        for m in r.get("matches", []):
            day = date.fromisoformat(m["date"]) if m.get("date") else None
            kick = datetime.combine(day, time.fromisoformat(m["time"]), tzinfo=tz) if day and m.get("time") else None
            ms.append(MatchState(str(m["codacta"]), str(r["code"]), None, kick, day))
        out.append(CalendarRound(str(r["code"]), r.get("number"), tuple(ms)))
    return tuple(out)


def gather_state(session: Session, source: str, season: str, game_type: str, cfg: ScheduleConfig,
                 *, group_ids: Sequence[str] | None = None) -> PlannerState:
    """Lee de PostgreSQL grupos, partidos, resúmenes de calendario y últimas consultas terminadas. Solo lectura."""
    tz = ZoneInfo(cfg.timezone)
    stmt = (select(CompetitionGroup, Competition).join(Competition, Competition.id == CompetitionGroup.competition_id)
            .join(Season, Season.id == Competition.season_id)
            .where(Season.source == source, Season.external_id == season, CompetitionGroup.removed_at.is_(None))
            .order_by(CompetitionGroup.external_id))
    if group_ids is not None:
        stmt = stmt.where(CompetitionGroup.external_id.in_(list(group_ids)))
    rows = session.execute(stmt).all()
    by_group: dict[int, list[MatchState]] = {}
    last_round: dict[int, int] = {}
    ids = [grp.id for grp, _ in rows]
    reports: dict[int, str] = {
        mid: REPORT_CLOSED if closed else REPORT_OPEN for mid, closed in session.execute(
            select(MatchReport.match_id, MatchReportObservation.closed)
            .join(MatchReportObservation, MatchReportObservation.report_id == MatchReport.id)
            .join(Match, Match.id == MatchReport.match_id).where(Match.group_id.in_(ids))
            .ext(distinct_on(MatchReport.match_id))
            .order_by(MatchReport.match_id, MatchReportObservation.observed_at.desc(), MatchReportObservation.id.desc()))}
    for gid, mid, codacta, rcode, status, at, day, number in session.execute(
            select(Match.group_id, Match.id, Match.external_id, Round.external_id, Match.status, Match.scheduled_at,
                   Match.scheduled_date, Round.visible_number).join(Round, Round.id == Match.round_id).where(Match.group_id.in_(ids))):
        by_group.setdefault(gid, []).append(MatchState(codacta, rcode, status, at, day, reports.get(mid, REPORT_NONE)))
        if status == "finished" and number is not None:
            last_round[gid] = max(last_round.get(gid, 0), number)
    cal_jobs = session.execute(
        select(IngestJob.target_key, IngestJob.checkpoint).where(IngestJob.source == source, IngestJob.kind == CALENDAR,
                                                                 IngestJob.status == "done", IngestJob.checkpoint.is_not(None))
        .ext(distinct_on(IngestJob.target_key)).order_by(IngestJob.target_key, IngestJob.id.desc())).all()
    calendars = {key: cp for key, cp in cal_jobs}
    hints: dict[str, dict[str, tuple[bool, bool]]] = {}
    for target, cp in session.execute(
            select(IngestJob.target, IngestJob.checkpoint).where(IngestJob.source == source, IngestJob.kind == ROUND_RESULTS,
                                                                 IngestJob.status == "done", IngestJob.checkpoint.is_not(None))
            .ext(distinct_on(IngestJob.target_key)).order_by(IngestJob.target_key, IngestJob.id.desc())):
        for codacta, flags in ((cp or {}).get("reports") or {}).items():
            hints.setdefault(str(target.get("group")), {})[str(codacta)] = (bool(flags[0]), bool(flags[1]))
    done = {(kind, key): at for kind, key, at in session.execute(
        select(IngestJob.kind, IngestJob.target_key, func.max(IngestJob.finished_at))
        .where(IngestJob.source == source, IngestJob.status == "done").group_by(IngestJob.kind, IngestJob.target_key))
        if at is not None}
    groups: list[GroupState] = []
    for grp, comp in rows:
        base = group_target(season, comp.external_id, grp.external_id, comp.game_type_code or game_type)
        cp = calendars.get(canonical_key(base))
        groups.append(GroupState(
            season=season, competition=comp.external_id, group=grp.external_id, game_type=base["game_type"],
            active=comp.active is not False, has_standings=grp.has_standings, has_scorers=grp.has_scorers,
            db_matches=tuple(by_group.get(grp.id, ())), calendar=calendar_summary_rounds(cp, tz) if cp else (),
            last_known_round=last_round.get(grp.id), report_hints=hints.get(grp.external_id, {})))
    return PlannerState(season, game_type, tuple(groups), done)


@dataclass(frozen=True)
class TickResult:
    specs: list[JobSpec]  # lo que se encolaría (colapsado por objetivo)
    created: int  # trabajos nuevos
    existing: int  # ya había un trabajo vivo con la misma clave (se adelanta o se sube su prioridad, nunca se duplica)


def tick(session: Session, source: str, season: str, game_type: str, cfg: ScheduleConfig, now: datetime,
         *, max_attempts: int = 4, group_ids: Sequence[str] | None = None,
         exclude_kinds: Sequence[str] = ()) -> TickResult:
    """Encola de forma idempotente lo que `plan` considera pendiente. Solo hace `flush`: confirma quien llama.

    `group_ids` limita los grupos (None = todos los de la temporada); `exclude_kinds` descarta tipos (p. ej. el catálogo)."""
    specs = [s for s in collapse(plan(gather_state(session, source, season, game_type, cfg, group_ids=group_ids), cfg, now))
             if s.kind not in exclude_kinds]
    live = set(session.execute(select(IngestJob.kind, IngestJob.target_key)
                               .where(IngestJob.source == source, IngestJob.status.in_(("pending", "running", "failed")))).all())
    created = 0
    for spec in specs:
        if (spec.kind, spec.target_key) not in live:
            created += 1
        jobs.enqueue(session, source, spec.kind, spec.target, priority=spec.priority, not_before=spec.not_before,
                     max_attempts=max_attempts, now=_utc(now))
    return TickResult(specs, created, len(specs) - created)
