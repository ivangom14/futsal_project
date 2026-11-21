"""Base del resultado y estado de partidos YA guardados, desde las instantáneas de la RFFM ya descargadas, sin red (Fase 3E).

Recorre las instantáneas `.json` (`/api/results`) y `.html` (jornada con `__NEXT_DATA__`) bajo `--dir`, interpreta cada partido con
el MISMO parser de la ingesta (`parser._match`: estado y `result_basis`) y, para cada `codacta`, usa la instantánea más reciente.

Solo se aplica si esa instantánea describe el MISMO estado que la BD (marcador y fecha iguales): una instantánea antigua nunca
pisa un dato más nuevo. Cambia:
- `matches.result_basis`.
- `matches.status` solo de `unknown` a `postponed/suspended/cancelled`: el parser antiguo no reconocía «Suspensión» con tilde.
- En la última observación del partido, `result_basis` y su `content_hash`, con la fórmula actual, para que la siguiente
  importación no cree una observación duplicada.

Idempotente: la segunda ejecución da 0 cambios.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.db.models import Match, MatchObservation
from futsal.importer.crest_backfill import _load
from futsal.importer.transform import content_hash
from futsal.ingestion.rffm.parser import _match as parse_match

STATUS_FIXES = ("postponed", "suspended", "cancelled")  # solo desde `unknown`: lo único que cambió en la interpretación del estado


def _raw_matches(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("codacta") not in (None, "") and "Goles_casa" in node:
                out.append(node)
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def scan(root: Path) -> tuple[dict[str, Any], int]:
    """{codacta: partido interpretado de su instantánea más reciente}, archivos leídos."""
    latest: dict[str, Any] = {}
    read = 0
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".html", ".json")]
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        data = _load(path)
        if data is None:
            continue
        read += 1
        for raw in _raw_matches(data):
            m = parse_match(raw, str(path))
            if m.external_match_id:
                latest[m.external_match_id] = m
    return latest, read


def run_result_basis_backfill(engine: Engine, root: Path, *, dry_run: bool = False) -> dict[str, Any]:
    latest, read = scan(root)
    stats: dict[str, Any] = {"files": read, "snapshot_matches": len(latest), "matched": 0, "stale_skipped": 0,
                             "basis_set": {}, "status_fixed": {}, "observations_updated": 0, "dry_run": dry_run}
    with Session(engine) as s, s.begin():
        rows = s.scalars(select(Match).where(Match.source == "rffm", Match.external_id.in_(list(latest)))).all()
        for row in rows:
            m = latest[row.external_id]
            stats["matched"] += 1
            if (m.home_score, m.away_score, m.scheduled_date) != (row.home_score, row.away_score, row.scheduled_date):
                stats["stale_skipped"] += 1  # la BD tiene un estado distinto (más nuevo o de otra fuente): no se toca
                continue
            status = m.status if row.status == "unknown" and m.status in STATUS_FIXES else row.status
            if (row.result_basis, row.status) == (m.result_basis, status):
                continue
            if m.result_basis != row.result_basis:
                key = m.result_basis or "none"
                stats["basis_set"][key] = stats["basis_set"].get(key, 0) + 1
            if status != row.status:
                stats["status_fixed"][f"{row.status}->{status}"] = stats["status_fixed"].get(f"{row.status}->{status}", 0) + 1
            if dry_run:
                continue
            row.result_basis, row.status = m.result_basis, status
            obs = s.scalars(select(MatchObservation).where(MatchObservation.match_id == row.id)
                            .order_by(MatchObservation.observed_at.desc(), MatchObservation.id.desc()).limit(1)).one_or_none()
            if obs is not None and (obs.home_score, obs.away_score) == (row.home_score, row.away_score):
                obs.result_basis, obs.status = m.result_basis, status
                obs.content_hash = content_hash(status, obs.scheduled_date, obs.scheduled_time, obs.home_score, obs.away_score,
                                                m.result_basis)
                stats["observations_updated"] += 1
        if dry_run:
            s.rollback()
    return stats
