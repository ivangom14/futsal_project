"""Planificador (puro): reglas de ingestion.md §3 con reloj fijo y umbrales leídos del JSON. Sin red ni base de datos."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from futsal.ingestion.queue.controls import CONFIG_PATH
from futsal.ingestion.queue.jobs import canonical_key
from futsal.ingestion.queue.planner import (
    CALENDAR,
    DISCOVER_CATALOG,
    ROUND_RESULTS,
    SCORERS,
    STANDINGS,
    CalendarRound,
    GroupState,
    JobSpec,
    MatchState,
    PlannerState,
    calendar_covered_rounds,
    catalog_target,
    collapse,
    group_target,
    plan,
    round_target,
)
from futsal.ingestion.queue.schedule import load_schedule

MAD = ZoneInfo("Europe/Madrid")
SEASON, COMP, GROUP, GT = "22", "100", "200", "3"
CFG = load_schedule()
G_TARGET = group_target(SEASON, COMP, GROUP, GT)


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=MAD).astimezone(UTC)


def match(n: int, rnd: str, day: str, hm: str | None, status: str | None = "scheduled") -> MatchState:
    return MatchState(f"m{n}", rnd, status, at(day, hm) if hm else None, date.fromisoformat(day))


def group(*matches: MatchState, active: bool = True, calendar: tuple[CalendarRound, ...] = ()) -> GroupState:
    return GroupState(SEASON, COMP, GROUP, GT, active, True, True, db_matches=matches, calendar=calendar)


def calendar_round(code: str, *matches: MatchState) -> CalendarRound:
    return CalendarRound(code, int(code), matches)


def settled(now: datetime) -> dict[tuple[str, str], datetime]:
    """Catálogo, calendario y tablas consultados hace poco: aísla las reglas de jornada."""
    recent = now
    return {(DISCOVER_CATALOG, canonical_key(catalog_target(SEASON, GT))): recent,
            (CALENDAR, canonical_key(G_TARGET)): recent, (STANDINGS, canonical_key(G_TARGET)): recent,
            (SCORERS, canonical_key(G_TARGET)): recent}


WIDE = replace(CFG, horizon_hours=24 * 60)  # para ver también lo que vence a semanas vista


def run(g: GroupState, now: datetime, last: dict[tuple[str, str], datetime] | None = None, cfg=CFG) -> list[JobSpec]:  # type: ignore[no-untyped-def]
    return plan(PlannerState(SEASON, GT, (g,), settled(now) if last is None else last), cfg, now)


def local(specs: list[JobSpec], prefix: str) -> list[str]:
    return sorted(s.not_before.astimezone(MAD).strftime("%H:%M") for s in specs if s.reason.startswith(prefix))


SAT_NOON = at("2026-11-07", "12:00")  # sábado 7-11-2026 (CET)


def test_saturday_noon_with_two_kickoffs_gives_the_in_play_slots() -> None:
    g = group(match(1, "5", "2026-11-07", "16:00"), match(2, "5", "2026-11-07", "18:30"))
    specs = run(g, SAT_NOON)
    rr = [s for s in specs if s.kind == ROUND_RESULTS]
    assert local(rr, "en juego") == ["17:15", "17:50", "19:00", "19:45", "20:20", "21:30"]
    assert {s.priority for s in rr if s.reason.startswith("en juego")} == {CFG.priorities.in_play}
    assert all(s.target == round_target(G_TARGET, "5") for s in rr)
    sweep = [s for s in rr if "barrido" in s.reason]
    assert [s.not_before for s in sweep] == [SAT_NOON] and sweep[0].priority == CFG.priorities.in_play
    assert not [s for s in specs if s.kind != ROUND_RESULTS]  # calendario, catálogo y tablas ya al día
    one = collapse(specs)
    assert len(one) == 1 and one[0].not_before == SAT_NOON  # un trabajo vivo por (tipo, objetivo): la franja más temprana


def test_a_slot_already_covered_by_a_later_check_is_not_emitted_again() -> None:
    g = group(match(1, "5", "2026-11-07", "16:00"))
    last = settled(SAT_NOON)
    last[(ROUND_RESULTS, canonical_key(round_target(G_TARGET, "5")))] = at("2026-11-07", "17:20")
    rr = [s for s in run(g, at("2026-11-07", "17:30"), last) if s.kind == ROUND_RESULTS]
    assert local(rr, "en juego") == ["17:50", "19:00"]  # la de +75 min ya está cubierta


def test_monday_only_settling_and_calendar() -> None:
    monday = at("2026-11-09", "12:30")
    played = tuple(match(n, "5", "2026-11-07", hm, "finished") for n, hm in ((1, "16:00"), (2, "18:30")))
    g = group(*played, calendar=(calendar_round("6", match(3, "6", "2026-11-28", None, None)),))  # siguiente jornada tras el parón
    last = settled(monday)
    last[(CALENDAR, canonical_key(G_TARGET))] = monday - timedelta(hours=25)  # el calendario diario ya toca
    last[(ROUND_RESULTS, canonical_key(round_target(G_TARGET, "5")))] = monday - timedelta(hours=7)
    specs = run(g, monday, last)
    assert sorted((s.kind, s.reason) for s in specs) == [(CALENDAR, "calendario diario"), (ROUND_RESULTS, "asentándose")]
    assert not [s for s in specs if s.reason.startswith(("en juego", "fin de semana"))]
    assert all(s.not_before == monday for s in specs)


def test_monday_with_the_next_round_inside_14_days_adds_the_daily_next_round_check() -> None:
    monday = at("2026-11-09", "12:30")
    g = group(*(match(n, "5", "2026-11-07", hm, "finished") for n, hm in ((1, "16:00"), (2, "18:30"))),
              match(3, "6", "2026-11-14", None, "scheduled"))
    last = settled(monday)
    last[(ROUND_RESULTS, canonical_key(round_target(G_TARGET, "6")))] = monday - timedelta(hours=5)
    nxt = [s for s in run(g, monday, last) if s.target.get("round") == "6"]
    assert [(s.reason, s.priority, s.not_before) for s in nxt] == [
        ("próxima jornada", CFG.priorities.next_round, monday + timedelta(hours=19))]


def test_group_without_known_hours_only_gets_sweeps_and_the_daily_hour_lookup() -> None:
    g = group(match(1, "5", "2026-11-07", None), match(2, "5", "2026-11-07", None))
    specs = run(g, SAT_NOON)
    assert not [s for s in specs if s.reason.startswith("en juego")]
    assert sorted(s.reason for s in specs) == ["fin de semana activo: barrido", "próxima jornada"]  # la hora sale de /api/results
    assert {s.not_before for s in specs} == {SAT_NOON}


def test_weekend_sweep_cadence_is_clipped_to_the_daily_window_and_ends_with_the_weekend() -> None:
    g = group(match(1, "5", "2026-11-07", None), match(2, "5", "2026-11-08", None))
    key = (ROUND_RESULTS, canonical_key(round_target(G_TARGET, "5")))

    def sweep_after(checked: datetime, now: datetime) -> datetime | None:
        last = settled(now)
        last[key] = checked
        found = [s for s in run(g, now, last) if "barrido" in s.reason]
        return found[0].not_before if found else None

    assert sweep_after(at("2026-11-07", "13:00"), at("2026-11-07", "13:30")) == at("2026-11-07", "15:00")  # cada 2 h
    assert sweep_after(at("2026-11-07", "22:00"), at("2026-11-07", "22:30")) == at("2026-11-08", "09:00")  # fuera de 09:00-23:30
    assert sweep_after(at("2026-11-07", "23:00"), at("2026-11-07", "23:10")) == at("2026-11-08", "09:00")
    assert sweep_after(at("2026-11-09", "07:00"), at("2026-11-09", "08:00")) == at("2026-11-09", "09:00")  # lunes antes de las 12:00
    assert not [s for s in run(g, at("2026-11-09", "12:30")) if "barrido" in s.reason]  # pasado el lunes 12:00


def test_past_due_every_12h_for_7_days_then_daily() -> None:
    key = (ROUND_RESULTS, canonical_key(round_target(G_TARGET, "4")))
    now = at("2026-11-12", "12:00")

    def due(match_day: str, checked_hours_ago: float) -> datetime:
        last = settled(now)
        last[key] = now - timedelta(hours=checked_hours_ago)
        specs = run(group(match(1, "4", match_day, "16:00", "scheduled")), now, last)
        (spec,) = [s for s in specs if s.kind == ROUND_RESULTS]
        assert spec.reason == "pasada atrasada (past_due)" and spec.priority == CFG.priorities.rest
        return spec.not_before

    assert due("2026-11-09", 13) == now  # han pasado 12 h: toca ya
    assert due("2026-11-09", 5) == now + timedelta(hours=7)
    assert due("2026-10-20", 5) == now + timedelta(hours=19)  # más de 7 días: diaria


def test_closed_rounds_weekly_for_30_days_then_monthly_and_played_unfinished_rounds_weekly() -> None:
    key = (ROUND_RESULTS, canonical_key(round_target(G_TARGET, "1")))
    now = at("2026-11-20", "12:00")

    def next_check(day: str, status: str, last_days: int = 1) -> tuple[str, datetime] | None:
        last = settled(now)
        last[key] = now - timedelta(days=last_days)
        specs = run(group(match(1, "1", day, "16:00", status), match(2, "1", day, "18:30", status)), now, last, WIDE)
        found = [s for s in specs if s.kind == ROUND_RESULTS]
        return (found[0].reason, found[0].not_before) if found else None

    assert next_check("2026-11-01", "finished") == ("cerrada", now + timedelta(days=6))  # semanal
    assert next_check("2026-09-01", "finished") == ("cerrada", now + timedelta(days=29))  # mensual
    assert next_check("2027-01-30", "scheduled") is None  # lejana sin jugar: solo el calendario (antes: semanal)


def test_initial_load_prioritises_recent_rounds_and_lists_the_most_recent_first() -> None:
    now = at("2026-11-10", "12:00")
    cal = tuple(calendar_round(str(n), match(n, str(n), d, None, None)) for n, d in
                ((1, "2026-10-03"), (2, "2026-11-07"), (3, "2026-11-21"), (4, "2027-02-20")))
    specs = run(group(calendar=cal), now)
    rr = [(s.target["round"], s.reason, s.priority) for s in specs if s.kind == ROUND_RESULTS]
    assert rr == [("3", "próxima jornada", 80), ("2", "carga inicial reciente", 80), ("1", "carga inicial atrasada", 40)]
    assert "4" not in [r[0] for r in rr]  # una jornada lejana sin guardar no se pide


def test_group_level_rules_priorities_and_inactive_competitions() -> None:
    now = at("2026-11-10", "12:00")
    active = group(match(1, "1", "2026-11-09", "16:00", "finished"))
    specs = collapse(run(active, now, {}))
    pri = CFG.priorities
    assert sorted((s.kind, s.priority) for s in specs if s.kind != ROUND_RESULTS) == sorted([
        (STANDINGS, pri.tables), (SCORERS, pri.tables), (DISCOVER_CATALOG, pri.rest), (CALENDAR, pri.rest)])
    assert [s.priority for s in specs] == sorted((s.priority for s in specs), reverse=True)
    inactive = collapse(run(group(match(1, "1", "2026-11-09", "16:00", "finished"), active=False), now, {}))
    inactive = [s for s in inactive if s.kind != DISCOVER_CATALOG]
    assert {(s.kind, s.priority) for s in inactive} == {(CALENDAR, pri.inactive), (STANDINGS, pri.inactive), (SCORERS, pri.inactive)}
    assert pri.in_play > pri.next_round > pri.tables > pri.rest > pri.inactive


def test_tables_daily_only_in_groups_with_activity_and_catalog_weekly_or_daily_in_summer() -> None:
    now = at("2026-11-10", "12:00")
    last = settled(now)
    last[(STANDINGS, canonical_key(G_TARGET))] = now - timedelta(hours=30)
    busy = [s for s in run(group(match(1, "1", "2026-11-09", "16:00", "finished")), now, last) if s.kind == STANDINGS]
    assert [(s.not_before, s.priority) for s in busy] == [(now, CFG.priorities.tables)]
    idle = [s for s in run(group(match(1, "1", "2026-09-09", "16:00", "finished")), now, last) if s.kind == STANDINGS]
    assert idle == []  # sin actividad cercana no hay consulta diaria
    cat = (DISCOVER_CATALOG, canonical_key(catalog_target(SEASON, GT)))
    winter = [s for s in run(group(), now, {cat: now - timedelta(days=2)}, WIDE) if s.kind == DISCOVER_CATALOG]
    assert winter[0].not_before == now + timedelta(days=5) and winter[0].reason == "catálogo semanal"
    july = at("2026-07-15", "12:00")
    summer = [s for s in run(group(), july, {cat: july - timedelta(hours=5)}) if s.kind == DISCOVER_CATALOG]
    assert summer[0].not_before == july + timedelta(hours=19) and "diario" in summer[0].reason


def test_thresholds_come_from_the_json(tmp_path: Path) -> None:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["planner"]["in_play"]["offsets_min"] = [10, 20]
    raw["planner"]["weekend"]["sweep_every_hours"] = 4
    raw["planner"]["priorities"]["in_play"] = 999
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load_schedule(path)
    g = group(match(1, "5", "2026-11-07", "16:00"))
    specs = run(g, SAT_NOON, cfg=cfg)
    assert local(specs, "en juego") == ["16:10", "16:20"] and {s.priority for s in specs if s.reason.startswith("en juego")} == {999}
    last = settled(SAT_NOON)
    last[(ROUND_RESULTS, canonical_key(round_target(G_TARGET, "5")))] = at("2026-11-07", "11:00")
    assert [s.not_before for s in run(g, SAT_NOON, last, cfg) if "barrido" in s.reason] == [at("2026-11-07", "15:00")]


def test_shipped_planner_section_matches_the_contract() -> None:
    c = load_schedule()
    assert c.in_play_offsets_min == (75, 110, 180) and c.sweep_every_hours == 2 and (c.sweep_from.hour, c.sweep_until.minute) == (9, 30)
    assert (c.settling_hours, c.settling_every_hours, c.next_round_days, c.past_due_every_hours, c.past_due_first_days) == (72, 6, 14, 12, 7)
    assert (c.closed_every_days, c.closed_first_days, c.catalog_every_days, c.tables_group_minutes) == (7, 30, 7, 30)
    assert c.catalog_daily_months == (6, 7, 8, 9) and c.weekend_start_weekday == 4 and c.weekend_end_weekday == 0


def test_26_round_group_with_7_played_only_gets_played_rounds_plus_the_next_one() -> None:
    now = at("2026-11-10", "12:00")
    cal = tuple(calendar_round(str(n), match(n, str(n), (date(2026, 9, 26) + timedelta(days=7 * (n - 1))).isoformat(), None, None))
                for n in range(1, 27))
    played = tuple(match(100 + n, str(n), (date(2026, 9, 26) + timedelta(days=7 * (n - 1))).isoformat(), "16:00", "finished")
                   for n in range(1, 8))
    specs = [s for s in run(group(*played, calendar=cal), now) if s.kind == ROUND_RESULTS]
    rounds = {s.target["round"] for s in specs}
    assert rounds <= {str(n) for n in range(1, 10)} and {"8", "9"} <= rounds  # 7 jugadas (como mucho) + las ≤ 14 días (8: 14-11, 9: 21-11)
    assert not rounds & {str(n) for n in range(10, 27)}  # ninguna lejana
    wide = plan(PlannerState(SEASON, GT, (group(*played, calendar=cal),), settled(now)), WIDE, now)
    assert not {s.target["round"] for s in wide if s.kind == ROUND_RESULTS} & {str(n) for n in range(10, 27)}
    assert calendar_covered_rounds(PlannerState(SEASON, GT, (group(*played, calendar=cal),)), CFG, now) == 17


def test_group_with_undated_rounds_only_gets_the_calendar() -> None:
    now = at("2026-11-10", "12:00")
    undated = tuple(calendar_round(str(n), MatchState(f"u{n}", str(n), None, None, None)) for n in (1, 2, 3))
    specs = run(group(calendar=undated), now, {})
    assert not [s for s in specs if s.kind == ROUND_RESULTS]
    assert CALENDAR in {s.kind for s in specs}
