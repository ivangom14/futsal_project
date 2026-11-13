"""Umbrales del planificador (sección `planner` de `config/ingest_schedule.json`). Nada fijo en código: todo sale del JSON."""

import json
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from futsal.ingestion.queue.controls import CONFIG_PATH


def _hm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class Priorities:
    in_play: int = 100
    next_round: int = 80
    tables: int = 60
    rest: int = 40
    inactive: int = 10
    reports: int = 70


@dataclass(frozen=True)
class ReportPolicy:
    """Cadencia de las actas (`planner.reports`): horas entre intentos según la antigüedad del partido."""
    available_every_hours: float = 6.0  # /api/results dice que hay acta (o que ya está cerrada) y aquí falta
    first_days: float = 3.0
    first_every_hours: float = 12.0
    mid_days: float = 14.0
    mid_every_hours: float = 48.0
    max_days: float = 60.0
    late_every_hours: float = 168.0
    open_days: float = 14.0  # un acta abierta se revisa mientras el partido tenga menos de esto
    open_every_hours: float = 24.0


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str = "Europe/Madrid"
    horizon_hours: float = 24.0  # hasta dónde se anticipan las franjas de partidos
    in_play_offsets_min: tuple[int, ...] = (75, 110, 180)
    in_play_window_min: int = 180  # un partido sin resultado cuenta «en juego» hasta inicio + esto
    weekend_start_weekday: int = 4  # 0 = lunes
    weekend_start: time = time(16, 0)
    weekend_end_weekday: int = 0
    weekend_end: time = time(12, 0)
    sweep_every_hours: float = 2.0
    sweep_from: time = time(9, 0)
    sweep_until: time = time(23, 30)
    settling_hours: float = 72.0
    settling_every_hours: float = 6.0
    next_round_days: float = 14.0
    next_round_every_hours: float = 24.0
    past_due_first_days: float = 7.0
    past_due_every_hours: float = 12.0
    past_due_then_every_hours: float = 24.0
    closed_first_days: float = 30.0
    closed_every_days: float = 7.0
    closed_then_every_days: float = 30.0
    far_every_days: float = 7.0
    calendar_every_hours: float = 24.0
    catalog_every_days: float = 7.0
    catalog_daily_months: tuple[int, ...] = (6, 7, 8, 9)
    tables_every_hours: float = 24.0
    tables_activity_days: float = 3.0
    tables_group_minutes: float = 30.0
    initial_recent_days: float = 7.0
    inactive_every_days: float = 30.0
    priorities: Priorities = field(default_factory=Priorities)
    reports: ReportPolicy = field(default_factory=ReportPolicy)


def load_schedule(path: Path | None = None) -> ScheduleConfig:
    raw: dict[str, Any] = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8")).get("planner", {})
    d, dp = ScheduleConfig(), Priorities()

    def sec(name: str) -> dict[str, Any]:
        value = raw.get(name, {})
        return value if isinstance(value, dict) else {}

    ip, wk, st, nr, pd, cl = sec("in_play"), sec("weekend"), sec("settling"), sec("next_round"), sec("past_due"), sec("closed")
    cat, tb, pr, rp = sec("catalog"), sec("tables"), sec("priorities"), sec("reports")
    dr = ReportPolicy()
    return ScheduleConfig(
        timezone=str(raw.get("timezone", d.timezone)),
        horizon_hours=float(raw.get("horizon_hours", d.horizon_hours)),
        in_play_offsets_min=tuple(int(x) for x in ip.get("offsets_min", d.in_play_offsets_min)),
        in_play_window_min=int(ip.get("window_min", d.in_play_window_min)),
        weekend_start_weekday=int(wk.get("start_weekday", d.weekend_start_weekday)),
        weekend_start=_hm(wk["start_time"]) if "start_time" in wk else d.weekend_start,
        weekend_end_weekday=int(wk.get("end_weekday", d.weekend_end_weekday)),
        weekend_end=_hm(wk["end_time"]) if "end_time" in wk else d.weekend_end,
        sweep_every_hours=float(wk.get("sweep_every_hours", d.sweep_every_hours)),
        sweep_from=_hm(wk["sweep_from"]) if "sweep_from" in wk else d.sweep_from,
        sweep_until=_hm(wk["sweep_until"]) if "sweep_until" in wk else d.sweep_until,
        settling_hours=float(st.get("hours", d.settling_hours)),
        settling_every_hours=float(st.get("every_hours", d.settling_every_hours)),
        next_round_days=float(nr.get("within_days", d.next_round_days)),
        next_round_every_hours=float(nr.get("every_hours", d.next_round_every_hours)),
        past_due_first_days=float(pd.get("first_days", d.past_due_first_days)),
        past_due_every_hours=float(pd.get("every_hours", d.past_due_every_hours)),
        past_due_then_every_hours=float(pd.get("then_every_hours", d.past_due_then_every_hours)),
        closed_first_days=float(cl.get("first_days", d.closed_first_days)),
        closed_every_days=float(cl.get("every_days", d.closed_every_days)),
        closed_then_every_days=float(cl.get("then_every_days", d.closed_then_every_days)),
        far_every_days=float(sec("far").get("every_days", d.far_every_days)),
        calendar_every_hours=float(sec("calendar").get("every_hours", d.calendar_every_hours)),
        catalog_every_days=float(cat.get("every_days", d.catalog_every_days)),
        catalog_daily_months=tuple(int(x) for x in cat.get("daily_months", d.catalog_daily_months)),
        tables_every_hours=float(tb.get("every_hours", d.tables_every_hours)),
        tables_activity_days=float(tb.get("activity_days", d.tables_activity_days)),
        tables_group_minutes=float(tb.get("group_minutes", d.tables_group_minutes)),
        initial_recent_days=float(sec("initial").get("recent_days", d.initial_recent_days)),
        inactive_every_days=float(sec("inactive").get("every_days", d.inactive_every_days)),
        priorities=Priorities(in_play=int(pr.get("in_play", dp.in_play)), next_round=int(pr.get("next_round", dp.next_round)),
                              tables=int(pr.get("tables", dp.tables)), rest=int(pr.get("rest", dp.rest)),
                              inactive=int(pr.get("inactive", dp.inactive)), reports=int(pr.get("reports", dp.reports))),
        reports=ReportPolicy(**{k: float(rp.get(k, getattr(dr, k))) for k in ReportPolicy.__dataclass_fields__}))
