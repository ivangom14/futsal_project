"""Preflight seguro del agente: configuración sin secretos, servicios y prueba de que evaluación y API usan la MISMA base.

SOLO LECTURA: las consultas a PostgreSQL van en transacción de solo lectura y a la API solo se hacen GET.
Nunca se imprime una clave, una contraseña ni el contenido del `.env`; de cada variable secreta solo se indica si
está definida. Uso: `python -m futsal.agent.preflight [--for-live]`.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from futsal.config.settings import api_base_url, database_url, env_value

EXPECTED_TOOLS = frozenset({
    "list_competitions", "list_groups", "list_rounds", "list_teams", "list_matches", "get_match",
    "get_match_report", "list_match_events", "top_scorers", "find_players"})
MARKER = Path("data/agent/preflight.json")
Status = Literal["ok", "warn", "fail", "info"]


def quiet_logs() -> None:
    import logging

    logging.getLogger().setLevel(logging.WARNING)
    for name in ("httpx", "mcp", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@dataclass
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class SameDb:
    """`same`: huellas idénticas. `different`: demostrado que difieren. `unproven`: no se pudo comparar."""

    verdict: Literal["same", "different", "unproven"]
    detail: str
    db: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- huellas
def fingerprint(rows: list[tuple[Any, ...]]) -> str:
    """Huella estable de filas (id, estado, marcador). `None` se serializa igual en la base y en la API."""
    body = ";".join(":".join("-" if v is None else str(v) for v in r) for r in sorted(rows, key=lambda r: r[0]))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def db_snapshot(engine: Engine) -> dict[str, Any]:
    """Recuentos y huella desde PostgreSQL en transacción de SOLO LECTURA (rechaza cualquier escritura)."""
    with engine.connect().execution_options(postgresql_readonly=True) as c:
        group = c.execute(text("SELECT id, name FROM competition_groups ORDER BY id LIMIT 1")).one_or_none()
        if group is None:
            return {"empty": True}
        gid = group[0]
        rows = [tuple(r) for r in c.execute(text(
            "SELECT id, status, home_score, away_score FROM matches WHERE group_id = :g"), {"g": gid})]

        def one(sql: str) -> int:
            return int(c.execute(text(sql), {"g": gid}).scalar() or 0)

        latest = ("(SELECT DISTINCT ON (report_id) report_id, closed, home_score, away_score FROM "
                  "match_report_observations ORDER BY report_id, observed_at DESC, id DESC)")
        return {
            "group_id": gid, "group_name": group[1], "fingerprint": fingerprint(rows),
            "matches": len(rows), "finished": sum(1 for r in rows if r[1] == "finished"),
            "with_report": one("SELECT count(*) FROM match_reports r JOIN matches m ON m.id = r.match_id "
                               "WHERE m.group_id = :g"),
            "finished_without_report": one(
                "SELECT count(*) FROM matches m WHERE m.group_id = :g AND m.status = 'finished' AND NOT EXISTS "
                "(SELECT 1 FROM match_reports r WHERE r.match_id = m.id)"),
            "reports_not_closed": one(f"SELECT count(*) FROM match_reports r JOIN matches m ON m.id = r.match_id "
                                      f"JOIN {latest} o ON o.report_id = r.id WHERE m.group_id = :g "
                                      f"AND o.closed IS NOT TRUE"),
            "score_discrepancies": one(f"SELECT count(*) FROM match_reports r JOIN matches m ON m.id = r.match_id "
                                       f"JOIN {latest} o ON o.report_id = r.id WHERE m.group_id = :g AND "
                                       f"(o.home_score, o.away_score) IS DISTINCT FROM (m.home_score, m.away_score)"),
            "players": one("SELECT count(*) FROM players"),
            "homonym_names": one("SELECT count(*) FROM (SELECT display_name FROM players GROUP BY 1 "
                                 "HAVING count(*) > 1) x"),
            "events": one("SELECT count(*) FROM match_events"),
            "own_goals": one("SELECT count(*) FROM match_events WHERE event_type = 'own_goal'")}


def api_snapshot(client: httpx.Client) -> dict[str, Any]:
    """Lo mismo, pero visto a través de la API (solo GET)."""
    comps = client.get("/competitions").json()["items"]
    if not comps:
        return {"empty": True}
    groups = client.get(f"/competitions/{comps[0]['id']}/groups").json()["items"]
    if not groups:
        return {"empty": True}
    gid = groups[0]["id"]
    matches = client.get(f"/groups/{gid}/matches").json()["items"]
    cov = client.get(f"/groups/{gid}/scorers", params={"limit": 1}).json().get("coverage", {})
    rows = [(m["id"], m["status"], m["home_score"], m["away_score"]) for m in matches]
    return {"group_id": gid, "group_name": groups[0]["name"], "fingerprint": fingerprint(rows),
            "matches": len(rows), "finished": sum(1 for r in rows if r[1] == "finished"),
            "with_report": cov.get("with_report")}


def verify_same_database(engine: Engine, client: httpx.Client) -> SameDb:
    """¿La base que lee la evaluación es la que sirve la API? Compara huellas de partidos y cobertura de actas."""
    try:
        db = db_snapshot(engine)
    except Exception as exc:  # noqa: BLE001
        return SameDb("unproven", f"no se pudo leer PostgreSQL ({type(exc).__name__})")
    try:
        api = api_snapshot(client)
    except Exception as exc:  # noqa: BLE001
        return SameDb("unproven", f"no se pudo consultar la API ({type(exc).__name__})", db=db)
    if db.get("empty") or api.get("empty"):
        return SameDb("unproven", "la base o la API no devuelven grupos: no hay nada que comparar", db, api)
    diffs = [f"{k}: base={db.get(k)} API={api.get(k)}"
             for k in ("group_id", "fingerprint", "matches", "finished", "with_report") if db.get(k) != api.get(k)]
    if diffs:
        return SameDb("different", "la API y la evaluación NO ven los mismos datos (" + "; ".join(diffs) + ")",
                      db, api)
    return SameDb("same", f"huella de partidos idéntica ({db['fingerprint']}), {db['matches']} partidos, "
                          f"{db['with_report']} actas", db, api)


# ----------------------------------------------------------------------------- comprobaciones
def env_checks(for_live: bool) -> list[Check]:
    """Estado de la configuración. De las variables secretas solo se informa si están definidas."""
    out = [Check(".env", "info", "presente" if Path(".env").is_file() else "no existe (se usa solo el entorno)")]
    provider = env_value("LLM_PROVIDER", "anthropic").lower()
    out.append(Check("LLM_PROVIDER", "ok" if provider in ("gemini", "anthropic") else "fail", provider))
    key_name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
    defined = bool(env_value(key_name))
    out.append(Check(key_name, "ok" if defined else ("fail" if for_live else "warn"),
                     "definida" if defined else "NO definida" + (" (obligatoria para la evaluación online)"
                                                                 if for_live else "")))
    model = env_value("GEMINI_MODEL", "(por defecto)") if provider == "gemini" else env_value("LLM_MODEL", "(por defecto)")
    out.append(Check("modelo", "info", model))
    try:
        url = make_url(database_url())
        host = f"{url.host}:{url.port}/{url.database}"
        local = url.host in ("localhost", "127.0.0.1", "::1")
        out.append(Check("DATABASE_URL", "ok" if local else "warn",
                         f"host={host}, contraseña={'definida' if url.password else 'NO definida'}"
                         + ("" if local else " (base NO local: comprueba que es la que quieres)")))
    except Exception:  # noqa: BLE001
        out.append(Check("DATABASE_URL", "fail", "no definida o inválida"))
    out.append(Check("API_BASE_URL", "info", api_base_url()))
    out.append(Check("límites", "info", f"MAX_TOOL_CALLS={env_value('MAX_TOOL_CALLS', '5')}, "
                     f"LLM_MAX_RETRIES={env_value('LLM_MAX_RETRIES', '2')}, "
                     f"LLM_RETRY_MAX_WAIT={env_value('LLM_RETRY_MAX_WAIT', '20')}s"))
    return out


def docker_check() -> Check:
    if shutil.which("docker") is None:
        return Check("Docker", "warn", "no está en el PATH (no hace falta si PostgreSQL corre sin Docker)")
    try:
        done = subprocess.run(["docker", "info"], capture_output=True, timeout=8, check=False)  # noqa: S603, S607
    except (subprocess.TimeoutExpired, OSError):
        return Check("Docker", "warn", "no responde (¿Docker Desktop está arrancado?)")
    return Check("Docker", "ok" if done.returncode == 0 else "warn",
                 "responde" if done.returncode == 0 else "instalado, pero el servicio no responde")


def tools_check() -> Check:
    from futsal.mcp_server.server import create_server

    names = {t.name for t in asyncio.run(create_server().list_tools())}
    quiet_logs()  # FastMCP activa el nivel INFO global al crearse
    missing, extra = sorted(EXPECTED_TOOLS - names), sorted(names - EXPECTED_TOOLS)
    if missing:
        return Check("tools MCP", "fail", f"faltan {missing}: código desactualizado (git pull + pip install -e .)")
    return Check("tools MCP", "ok", f"{len(names)} tools" + (f" (nuevas no esperadas: {extra})" if extra else ""))


def run_preflight(engine: Engine | None, client: httpx.Client | None, *, for_live: bool = False,
                  check_docker: bool = True) -> tuple[list[Check], SameDb]:
    checks = env_checks(for_live)
    if check_docker:
        checks.append(docker_check())
    checks.append(tools_check())
    if engine is None or client is None:
        return checks, SameDb("unproven", "sin motor de base de datos o cliente de API")
    health: Check
    try:
        r = client.get("/health")
        health = Check("API", "ok" if r.status_code == 200 else "fail", f"/health -> HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        health = Check("API", "fail", f"no responde ({type(exc).__name__}); arranca: uvicorn futsal.api.app:app_factory"
                                      " --factory --port 8000")
    checks.append(health)
    same = verify_same_database(engine, client) if health.status == "ok" else SameDb(
        "unproven", "la API no responde: no se puede demostrar que usa la misma base")
    db = same.db
    if db and not db.get("empty"):
        checks.append(Check("PostgreSQL", "ok", f"lectura correcta; grupo '{db['group_name']}', "
                                                f"{db['matches']} partidos ({db['finished']} finalizados)"))
        checks.append(Check("auditoría de datos", "info",
                            f"actas={db['with_report']}, finalizados sin acta={db['finished_without_report']}, "
                            f"actas sin cerrar={db['reports_not_closed']}, marcador de actas ≠ competición="
                            f"{db['score_discrepancies']}, jugadores={db['players']} (nombres repetidos="
                            f"{db['homonym_names']}), eventos={db['events']} (en propia={db['own_goals']})"))
    else:
        checks.append(Check("PostgreSQL", "fail", same.detail))
    prefix = {"same": "DEMOSTRADO: ", "different": "DIFERENTES: ", "unproven": "NO DEMOSTRADO: "}[same.verdict]
    checks.append(Check("misma base (API = evaluación)", "ok" if same.verdict == "same" else "fail",
                        prefix + same.detail))
    return checks, same


def blocking(checks: list[Check], same: SameDb) -> bool:
    """La evaluación online se bloquea si no está DEMOSTRADO que API y evaluación usan la misma base, o hay un fallo."""
    return same.verdict != "same" or any(c.status == "fail" for c in checks)


def render(checks: list[Check], same: SameDb) -> str:
    tag = {"ok": "[OK]    ", "warn": "[AVISO] ", "fail": "[FALLO] ", "info": "[INFO]  "}
    lines = [f"{tag[c.status]}{c.name}: {c.detail}" for c in checks]
    if blocking(checks, same):
        lines.append("\nRESULTADO: BLOQUEADA la evaluación online (corrige los [FALLO]; la base de la evaluación y la "
                     "API deben ser la misma).")
    else:
        lines.append("\nRESULTADO: correcto. Puedes lanzar la evaluación online (sigue el procedimiento).")
    return "\n".join(lines)


def write_marker(checks: list[Check], same: SameDb, path: Path = MARKER) -> None:
    """Resumen SIN secretos de la última comprobación (lo lee el informe de evaluación)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "checked_at": datetime.now(UTC).isoformat(), "blocking": blocking(checks, same),
        "same_database": same.verdict, "detail": same.detail,
        "db": {k: v for k, v in same.db.items() if k != "empty"}, "checks": [asdict(c) for c in checks]},
        ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(prog="futsal.agent.preflight")
    p.add_argument("--for-live", action="store_true", help="exige la clave del LLM (evaluación online)")
    p.add_argument("--no-docker", action="store_true")
    args = p.parse_args()
    from futsal.db.session import make_engine

    try:
        engine: Engine | None = make_engine()
    except Exception:  # noqa: BLE001
        engine = None
    with httpx.Client(base_url=api_base_url(), timeout=10) as client:
        checks, same = run_preflight(engine, client, for_live=args.for_live, check_docker=not args.no_docker)
    print(render(checks, same))
    write_marker(checks, same)
    raise SystemExit(2 if blocking(checks, same) else 1 if any(c.status == "warn" for c in checks) else 0)


if __name__ == "__main__":
    main()
