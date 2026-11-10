"""CLI de la cola de ingesta: `ingest {status,pause,resume,recover,cancel}` (T06a) y `ingest {plan,tick,work}` (T06b).

`plan` y `tick` no usan la red; `work` se niega a usarla salvo con `--allow-network` Y `FUTSAL_RFFM_NETWORK_AUTHORIZED=1`.
"""

import argparse
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_TARGET
from futsal.db.session import make_engine
from futsal.ingestion.queue import controls, jobs
from futsal.ingestion.queue.controls import load_config
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
        if name == "plan":
            p.add_argument("--json", action="store_true", help="salida en JSON")
    work = isub.add_parser("work", help="trabajador de la cola (red real solo con --allow-network Y FUTSAL_RFFM_NETWORK_AUTHORIZED=1)")
    add_work_arguments(work)


def _at(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    at = datetime.fromisoformat(value)
    return (at if at.tzinfo else at.replace(tzinfo=ZoneInfo(load_schedule().timezone))).astimezone(UTC)


def run(args: argparse.Namespace) -> int:
    cmd = args.ingest_command
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
    with Session(engine) as session, session.begin():
        if cmd == "tick":
            result = tick(session, args.source, args.season, args.game_type, load_schedule(), _at(args.at),
                          max_attempts=load_config().max_attempts)
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


def _plan(engine: Engine, args: argparse.Namespace) -> int:
    """Solo lectura: la sesión se descarta sin confirmar."""
    at, cfg = _at(args.at), load_schedule()
    with Session(engine) as session:
        state = gather_state(session, args.source, args.season, args.game_type, cfg)
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
