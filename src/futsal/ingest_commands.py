"""CLI de la cola de ingesta: `ingest {status,pause,resume,recover,cancel}` (T06a) y `ingest {plan,tick,work}` (T06b).

`plan` y `tick` no usan la red; `work` se niega a usarla salvo con `--allow-network` Y `FUTSAL_RFFM_NETWORK_AUTHORIZED=1`.
"""

import argparse
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_TARGET
from futsal.db.models import Competition, CompetitionGroup
from futsal.db.session import make_engine
from futsal.ingestion.queue import controls, jobs
from futsal.ingestion.queue.controls import load_config
from futsal.ingestion.queue.ingest_scope import load_scope
from futsal.ingestion.queue.planner import (
    calendar_covered_rounds,
    collapse,
    gather_state,
    plan,
    tick,
)
from futsal.ingestion.queue.schedule import load_schedule
from futsal.ingestion.queue.worker import (
    MigrationMissing,
    add_work_arguments,
    ensure_schema,
    network_authorized,
    run_cli,
)

COMMANDS = ("ingest",)


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    ingest = sub.add_parser("ingest", help="cola de ingesta: estado, pausa, recuperación y cancelación")
    isub = ingest.add_subparsers(dest="ingest_command", required=True)
    for name, help_ in (("status", "progreso de la cola y estado del source"), ("pause", "pausa el source"),
                        ("resume", "reanuda el source"), ("recover", "devuelve a pending los arrendamientos vencidos")):
        p = isub.add_parser(name, help=help_)
        p.add_argument("--source", default="rffm")
        if name == "pause":
            p.add_argument("--reason", default="pausa manual")
    cancel = isub.add_parser("cancel", help="cancela un trabajo vivo")
    cancel.add_argument("--id", type=int, required=True)
    for name, help_ in (("plan", "lo que se encolaría y por qué (sin red y sin escribir)"),
                        ("tick", "encola de forma idempotente lo que toca según el calendario")):
        p = isub.add_parser(name, help=help_)
        p.add_argument("--source", default="rffm")
        p.add_argument("--season", default=RFFM_TARGET["season"])
        p.add_argument("--game-type", default=RFFM_TARGET["game_type"])
        p.add_argument("--at", default=None, help="instante ISO 8601 (sin zona = Europe/Madrid); por defecto, ahora")
        p.add_argument("--competition", action="append", default=[],
                       help="lote: solo los grupos de esta competición (código oficial; repetible). El alcance sigue mandando")
        p.add_argument("--group", action="append", default=[], help="lote: solo este grupo (código oficial; repetible)")
        if name == "plan":
            p.add_argument("--json", action="store_true", help="salida en JSON")
        else:
            p.add_argument("--skip-catalog", action="store_true",
                           help="no descubre competiciones nuevas: solo mantiene los grupos ya importados (evita la carga masiva)")
    isub.add_parser("scope", help="alcance declarativo de ingesta por deporte (config/ingest_scope.json); sin red ni BD")
    cov = isub.add_parser("coverage", help="cobertura REAL por competición frente al catálogo oficial (solo lectura, sin red)")
    cov.add_argument("--sport", required=True, choices=("futsal", "football11"))
    cov.add_argument("--json", action="store_true", help="salida en JSON")
    work = isub.add_parser("work", help="trabajador de la cola (red real solo con --allow-network Y FUTSAL_RFFM_NETWORK_AUTHORIZED=1)")
    add_work_arguments(work)


def _at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    at = datetime.fromisoformat(value)
    return (at if at.tzinfo else at.replace(tzinfo=ZoneInfo(load_schedule().timezone))).astimezone(UTC)


def run(args: argparse.Namespace) -> int:
    cmd = args.ingest_command
    if cmd == "scope":
        return _scope()
    if cmd == "work" and not network_authorized(args.allow_network):
        return run_cli(args)  # rechaza antes de tocar nada
    engine = make_engine()
    try:
        ensure_schema(engine)
    except MigrationMissing as exc:
        print(f"Error: {exc}")
        return 2
    if cmd == "work":
        return run_cli(args, engine)
    now = datetime.now(UTC)
    if cmd == "plan":
        return _plan(engine, args)
    if cmd == "coverage":
        return _coverage(engine, args)
    with Session(engine) as session, session.begin():
        if cmd == "tick":
            result = tick(session, args.source, args.season, args.game_type, load_schedule(), _at(args.at),
                          max_attempts=load_config().max_attempts, group_ids=_batch_groups(session, args),
                          exclude_kinds=("discover_catalog", "discover_groups") if getattr(args, "skip_catalog", False) else ())
            print(f"tick {_at(args.at).isoformat()}: nuevos={result.created} ya_vivos={result.existing}")
        elif cmd == "status":
            control = controls.get_control(session, args.source)
            state = {"source": args.source, "paused": bool(control and control.paused),
                     "reason": control.reason if control else None, "opened_by": control.opened_by if control else None,
                     **jobs.progress(session, args.source)}
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        elif cmd == "pause":
            controls.pause(session, args.source, args.reason, "manual", now)
            print(f"{args.source}: pausado ({args.reason})")
        elif cmd == "resume":
            print(f"{args.source}: {'reanudado' if controls.resume(session, args.source, now) else 'no estaba pausado'}")
        elif cmd == "recover":
            ids = jobs.recover_expired_leases(session, now)
            print(f"arrendamientos recuperados: {len(ids)}" + (f" (ids {ids})" if ids else ""))
        else:
            ok = jobs.cancel(session, args.id, now)
            print(f"trabajo {args.id}: {'cancelado' if ok else 'no existe o ya terminó'}")
            return 0 if ok else 1
    return 0


def _batch_groups(session: Session, args: argparse.Namespace) -> list[str] | None:
    """Grupos del lote (`--competition` / `--group`); None = todos los de la temporada (lo de siempre)."""
    comps, groups = list(getattr(args, "competition", []) or []), list(getattr(args, "group", []) or [])
    if not comps and not groups:
        return None
    found = set(groups)
    if comps:
        found |= set(session.scalars(select(CompetitionGroup.external_id).join(Competition, Competition.id == CompetitionGroup.competition_id)
                                     .where(Competition.source == args.source, Competition.external_id.in_(comps))))
    return sorted(found)


COVERAGE_SQL = """
SELECT c.external_id, c.name, c.active, c.format_code,
       count(DISTINCT g.id) AS groups, coalesce(sum(DISTINCT g.expected_teams * 1000000 + g.id) / 1000000, 0) AS _unused,
       (SELECT coalesce(sum(expected_teams), 0) FROM competition_groups x WHERE x.competition_id = c.id) AS teams_expected,
       (SELECT count(*) FROM teams t JOIN competition_groups x ON x.id = t.group_id WHERE x.competition_id = c.id) AS teams,
       (SELECT count(DISTINCT m.round_id) FROM matches m JOIN competition_groups x ON x.id = m.group_id
         WHERE x.competition_id = c.id) AS rounds,
       (SELECT count(*) FROM matches m JOIN competition_groups x ON x.id = m.group_id WHERE x.competition_id = c.id) AS matches,
       (SELECT count(*) FROM matches m JOIN competition_groups x ON x.id = m.group_id
         WHERE x.competition_id = c.id AND m.status = 'finished') AS finished,
       (SELECT count(DISTINCT s.group_id) FROM standings_snapshots s JOIN competition_groups x ON x.id = s.group_id
         WHERE x.competition_id = c.id AND s.status = 'ok') AS standings_ok,
       (SELECT count(DISTINCT s.group_id) FROM scorer_snapshots s JOIN competition_groups x ON x.id = s.group_id
         WHERE x.competition_id = c.id AND s.status = 'ok') AS scorers_ok,
       (SELECT count(*) FROM match_reports r JOIN matches m ON m.id = r.match_id JOIN competition_groups x ON x.id = m.group_id
         WHERE x.competition_id = c.id) AS reports,
       (SELECT count(DISTINCT x.id) FROM competition_groups x WHERE x.competition_id = c.id AND EXISTS
         (SELECT 1 FROM matches m WHERE m.group_id = x.id)) AS groups_with_matches
FROM competitions c LEFT JOIN competition_groups g ON g.competition_id = c.id
WHERE c.modality = :sport AND c.removed_at IS NULL
GROUP BY c.id ORDER BY c.external_id"""


def _coverage(engine: Engine, args: argparse.Namespace) -> int:
    """Cobertura real (BD) por competición, para validar cada bloque de activación: nunca se declara completa sin estos datos."""
    with engine.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(text(COVERAGE_SQL), {"sport": args.sport})]
    for r in rows:
        r.pop("_unused", None)
        r["complete_basic"] = bool(r["groups"]) and r["groups_with_matches"] == r["groups"] and r["teams"] >= r["teams_expected"]
    totals = {k: sum(int(r[k] or 0) for r in rows) for k in ("groups", "teams_expected", "teams", "rounds", "matches", "finished",
                                                               "standings_ok", "scorers_ok", "reports", "groups_with_matches")}
    if args.json:
        print(json.dumps({"sport": args.sport, "competitions": rows, "totals": totals}, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"cobertura {args.sport}: {len(rows)} competiciones")
    for r in rows:
        print(f"- {r['external_id']} {r['name']}: grupos {r['groups_with_matches']}/{r['groups']} con partidos · equipos "
              f"{r['teams']}/{r['teams_expected']} · jornadas (grupo×jornada) {r['rounds']} · partidos {r['matches']} ({r['finished']} finalizados) · clasificación ok "
              f"{r['standings_ok']} · goleadores ok {r['scorers_ok']} · actas {r['reports']}"
              + ("" if r["active"] is not False else " · INACTIVA") + (" · eliminatoria" if r["format_code"] == "1" else ""))
    print("totales:", json.dumps(totals, ensure_ascii=False))
    return 0


def _scope() -> int:
    """Qué deportes y competiciones están en el alcance y cuáles habilitadas (lo descubierto se ve en el informe de discover_catalog)."""
    scope = load_scope()
    for sport, cfg in scope.sports.items():
        mode = "todas las del catálogo" if cfg.all_competitions else f"{len(cfg.entries)} declaradas"
        print(f"{sport} (tipojuego={cfg.game_type}): {'HABILITADO' if cfg.enabled else 'deshabilitado'} · {mode}")
        for e in cfg.entries:
            state = scope.status(sport, e.name, e.category_code)
            print(f"  - {e.name} [{e.category_code or '-'}]: {state}")
    return 0


def _plan(engine: Engine, args: argparse.Namespace) -> int:
    """Solo lectura: la sesión se descarta sin confirmar."""
    at, cfg = _at(args.at), load_schedule()
    with Session(engine) as session:
        state = gather_state(session, args.source, args.season, args.game_type, cfg, group_ids=_batch_groups(session, args))
        raw = plan(state, cfg, at)
        session.rollback()
    specs = collapse(raw)
    by_kind: dict[str, int] = {}
    for sp in specs:
        by_kind[sp.kind] = by_kind.get(sp.kind, 0) + 1
    if args.json:
        print(json.dumps({"at": at.isoformat(), "slots": len(raw), "jobs": len(specs), "by_kind": by_kind,
                          "rounds_covered_by_calendar": calendar_covered_rounds(state, cfg, at),
                          "specs": [sp.as_dict() for sp in specs]}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    tz = ZoneInfo(cfg.timezone)
    print(f"plan a {at.astimezone(tz).isoformat()}: {len(specs)} trabajos ({len(raw)} franjas) {by_kind}")
    for sp in specs:
        print(f"{sp.not_before.astimezone(tz):%d-%m %H:%M} p={sp.priority:<3} {sp.kind:<14} "
              f"{json.dumps(sp.target, ensure_ascii=False, sort_keys=True)}  {sp.reason}")
    return 0
