"""Backfill idempotente del catálogo (revisión A2), sin red.

1. `team_entities` desde `DISTINCT (teams.source, teams.external_id)` y enlace `teams.team_entity_id`.
2. Metadatos oficiales y clasificación de competiciones: desde el inventario en disco si existe y, si no, solo por nombre y
   campos ya guardados (con menos confianza y `needs_review` donde corresponda). Grupos: campos oficiales del inventario.
3. `seasons.label` y fechas si el inventario las trae.

Dos ejecuciones seguidas: la segunda informa `changes == 0`. Con `dry_run` se calcula todo y se deshace.
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup, Season, Team, TeamEntity
from futsal.importer.catalog_service import (
    apply_competition_meta,
    apply_group_meta,
    clean_text,
    load_source_config,
    parse_date,
    table_overrides,
)


def find_inventories(path: Path | None, default_dir: Path) -> list[Path]:
    """`path` puede ser un `inventory.json` o un directorio; sin `path`, los inventarios bajo `default_dir` (si existen)."""
    base = path or default_dir
    if base.is_file():
        return [base]
    return sorted(base.rglob("inventory.json")) if base.is_dir() else []


def _load_inventories(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(competiciones por ID, grupos por ID, temporadas por ID) de los inventarios; solo entradas confirmadas."""
    comps: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    seasons: dict[str, dict[str, Any]] = {}
    for p in paths:
        inv = json.loads(p.read_text(encoding="utf-8"))
        season = inv.get("season") or {}
        if season.get("external_id"):
            seasons[str(season["external_id"])] = season
        for entry in inv.get("entries", []):
            comp = entry.get("competition")
            if not comp or not comp.get("external_id"):
                continue
            comps[str(comp["external_id"])] = comp
            for g in entry.get("groups") or []:
                if g.get("external_id"):
                    groups[str(g["external_id"])] = g
    return comps, groups, seasons


def _entities(s: Session) -> dict[str, int]:
    """Paso 1: crea las entidades que faltan, refresca el último nombre visto y enlaza los equipos sin entidad."""
    counts = {"entities_created": 0, "entities_renamed": 0, "teams_linked": 0}
    existing = {(e.source, e.external_code): e for e in s.scalars(select(TeamEntity))}
    latest: dict[tuple[str, str], tuple[Any, int, str | None]] = {}
    first: dict[tuple[str, str], Any] = {}
    for t in s.scalars(select(Team)):
        key = (t.source, t.external_id)
        stamp = (t.last_seen_at, t.id, t.name)
        if key not in latest or stamp[:2] > latest[key][:2]:
            latest[key] = stamp
        if key not in first or t.first_seen_at < first[key]:
            first[key] = t.first_seen_at
    for key in sorted(latest):
        entity = existing.get(key)
        if entity is None:
            entity = existing[key] = TeamEntity(source=key[0], external_code=key[1], name=latest[key][2],
                                                first_seen_at=first[key], last_seen_at=latest[key][0])
            s.add(entity)
            counts["entities_created"] += 1
        elif latest[key][2] and entity.name != latest[key][2]:
            entity.name = latest[key][2]
            counts["entities_renamed"] += 1
    s.flush()
    ids = {k: e.id for k, e in existing.items()}
    for t in s.scalars(select(Team).where(Team.team_entity_id.is_(None))):
        t.team_entity_id = ids[(t.source, t.external_id)]
        counts["teams_linked"] += 1
    s.flush()
    return counts


def _competitions(s: Session, comps: dict[str, dict[str, Any]], groups: dict[str, dict[str, Any]], source: str,
                  reclassify_confirmed: bool) -> dict[str, int]:
    cfg = load_source_config(source)
    by_comp = table_overrides(s, source)
    counts = {"competitions_seen": 0, "competitions_changed": 0, "competitions_skipped_confirmed": 0,
              "competitions_with_override": 0, "groups_changed": 0}
    for comp in s.scalars(select(Competition).where(Competition.source == source).order_by(Competition.id)):
        counts["competitions_seen"] += 1
        if any(isinstance(v, dict) and v.get("source") == "override" for v in (comp.classification or {}).values()) \
                or comp.external_id in by_comp:
            counts["competitions_with_override"] += 1
        if comp.review_status == "confirmed" and not reclassify_confirmed:
            counts["competitions_skipped_confirmed"] += 1
            continue
        if apply_competition_meta(comp, comps.get(comp.external_id), cfg, by_comp.get(comp.external_id),
                                  reclassify_confirmed=reclassify_confirmed):
            counts["competitions_changed"] += 1
    for grp in s.scalars(select(CompetitionGroup).where(CompetitionGroup.source == source).order_by(CompetitionGroup.id)):
        if grp.external_id in groups and apply_group_meta(grp, groups[grp.external_id]):
            counts["groups_changed"] += 1
    s.flush()
    return counts


def _seasons(s: Session, seasons: dict[str, dict[str, Any]], source: str) -> dict[str, int]:
    changed = 0
    for season in s.scalars(select(Season).where(Season.source == source).order_by(Season.id)):
        inv = seasons.get(season.external_id)
        if not inv:
            continue
        new = {"label": clean_text(inv.get("label") or inv.get("name")), "start_date": parse_date(inv.get("start_date")),
               "end_date": parse_date(inv.get("end_date"))}
        diff = {k: v for k, v in new.items() if v is not None and getattr(season, k) != v}
        for k, v in diff.items():
            setattr(season, k, v)
        changed += bool(diff)
    s.flush()
    return {"seasons_changed": changed}


def run_backfill(engine: Engine, *, inventories: list[Path], dry_run: bool = False, reclassify_confirmed: bool = False,
                 source: str | None = None) -> dict[str, Any]:
    """Ejecuta los tres pasos en una transacción; `changes` es el total de filas modificadas o creadas (0 = nada que hacer)."""
    source = source or load_source_config()["source"]
    comps, groups, seasons = _load_inventories(inventories)
    with Session(engine) as s:
        summary: dict[str, Any] = {"dry_run": dry_run, "inventories": len(inventories), "inventory_competitions": len(comps)}
        summary.update(_entities(s))
        summary.update(_competitions(s, comps, groups, source, reclassify_confirmed))
        summary.update(_seasons(s, seasons, source))
        summary["changes"] = sum(summary[k] for k in ("entities_created", "entities_renamed", "teams_linked",
                                                      "competitions_changed", "groups_changed", "seasons_changed"))
        summary["team_entities_total"] = s.scalar(select(func.count()).select_from(TeamEntity)) or 0
        summary["teams_unlinked"] = s.scalar(select(func.count()).select_from(Team).where(Team.team_entity_id.is_(None))) or 0
        summary["review_status"] = dict(s.execute(select(Competition.review_status, func.count())
                                                  .group_by(Competition.review_status)).all())
        if dry_run:
            s.rollback()
        else:
            s.commit()
    return summary
