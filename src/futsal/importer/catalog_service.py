"""Importación por lotes de los grupos descargados por `scrape-catalog` y verificación de solo lectura."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup, Match, Round, Season, Team
from futsal.importer.schema import InputError
from futsal.importer.service import run_import
from futsal.ingestion.rffm.catalog_ingest import DOWNLOADED, PARTIAL

IMPORTABLE = (DOWNLOADED, PARTIAL)


def import_catalog(engine: Engine, report: dict[str, Any], *, dry_run: bool = False,
                   only: set[str] | None = None) -> list[dict[str, Any]]:
    """Importa cada grupo con `league.json`; un error en un grupo no impide importar los demás."""
    results: list[dict[str, Any]] = []
    for g in report["groups"]:
        if only and g["competition_id"] not in only:
            continue
        row = {"competition_id": g["competition_id"], "group_id": g["group_id"], "download_status": g["status"]}
        if g["status"] not in IMPORTABLE:
            results.append({**row, "import_status": "skipped", "error": f"descarga '{g['status']}'"})
            continue
        try:
            rep = run_import(engine, Path(g["league_path"]), dry_run=dry_run)
        except (InputError, OSError) as exc:
            results.append({**row, "import_status": "failed", "error": str(exc)[:200]})
            continue
        results.append({**row, "import_status": rep.status, "error": rep.error, "issues": rep.issues,
                        "rounds": rep.rounds_processed, "matches": rep.matches_processed,
                        "inserted": rep.total("inserted"), "updated": rep.total("updated"),
                        "unchanged": rep.total("unchanged")})
    return results


def verify_catalog(engine: Engine, report: dict[str, Any]) -> dict[str, Any]:
    """Compara lo descargado (informe) con lo persistido (PostgreSQL), solo lectura."""
    groups: list[dict[str, Any]] = []
    with Session(engine) as s:
        for g in report["groups"]:
            row = s.execute(
                select(CompetitionGroup.id, Competition.id).join(Competition, Competition.id == CompetitionGroup.competition_id)
                .where(CompetitionGroup.external_id == g["group_id"], Competition.external_id == g["competition_id"])
            ).one_or_none()
            item: dict[str, Any] = {"competition_id": g["competition_id"], "group_id": g["group_id"],
                                    "download_status": g["status"], "persisted": row is not None}
            if row is not None:
                gid = row[0]
                by_status = dict(s.execute(select(Match.status, func.count()).where(Match.group_id == gid)
                                           .group_by(Match.status)).all())
                db = {"rounds": s.scalar(select(func.count()).select_from(Round).where(Round.group_id == gid)) or 0,
                      "teams": s.scalar(select(func.count()).select_from(Team).where(Team.group_id == gid)) or 0,
                      "matches": sum(by_status.values()), "matches_by_status": by_status}
                item["db"] = db
                downloaded = g.get("matches_total")
                item["matches_match_download"] = downloaded == db["matches"] if downloaded is not None else None
            groups.append(item)
        return {"groups": groups, "integrity": _integrity(s)}


def _integrity(s: Session) -> dict[str, int]:
    """Conteos que deben ser 0 (el esquema ya lo impone; se comprueba igualmente)."""
    def count(stmt: Any) -> int:
        return int(s.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    dup_matches = select(Match.external_id).group_by(Match.external_id).having(func.count() > 1)
    dup_teams = select(Team.group_id, Team.external_id).group_by(Team.group_id, Team.external_id).having(func.count() > 1)
    orphan_matches = select(Match.id).outerjoin(Round, Round.id == Match.round_id).where(
        (Round.id.is_(None)) | (Round.group_id != Match.group_id))
    orphan_groups = select(CompetitionGroup.id).outerjoin(Competition, Competition.id == CompetitionGroup.competition_id).where(
        Competition.id.is_(None))
    orphan_comps = select(Competition.id).outerjoin(Season, Season.id == Competition.season_id).where(Season.id.is_(None))
    return {"duplicate_matches": count(dup_matches), "duplicate_teams": count(dup_teams),
            "orphan_matches": count(orphan_matches), "orphan_groups": count(orphan_groups),
            "orphan_competitions": count(orphan_comps)}
