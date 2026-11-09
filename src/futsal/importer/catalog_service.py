"""Importación por lotes de los grupos descargados por `scrape-catalog` y verificación de solo lectura.

También persiste los metadatos oficiales y la clasificación de competiciones (revisión A2) con la política de overrides:
`override > campo oficial > regla > unknown`; un atributo con origen `override` y una competición `confirmed` no se
modifican salvo `reclassify_confirmed=True` (y, aun así, nunca un override).
"""

import json
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from futsal.db.models import (
    CatalogOverride,
    Competition,
    CompetitionGroup,
    Match,
    Round,
    Season,
    Team,
)
from futsal.domain.catalog_classify import ATTRS, OVERRIDE, CompetitionMeta, classify_competition
from futsal.importer.schema import InputError
from futsal.importer.service import run_import
from futsal.ingestion.rffm.catalog_ingest import DOWNLOADED, PARTIAL

IMPORTABLE = (DOWNLOADED, PARTIAL)
SOURCES_DIR = Path(__file__).parents[1] / "config" / "sources"

# Campo normalizado -> nombres aceptados en el informe/inventario (los del parser de la web y los crudos de /api/competitions).
_SOURCE_KEYS = {
    "game_type_code": ("game_type_code", "game_type", "codigo_tipo_juego"),
    "category_code": ("category_code", "category_id", "CodigoCategoria"),
    "category_name": ("category_name", "category", "NombreCategoria"),
    "category_group_code": ("category_group_code", "cod_grupo_categoria"),
    "category_group_name": ("category_group_name", "nombre_grupo_categoria"),
    "format_code": ("format_code", "tipo_competicion"),
    "active": ("active", "Activa"),
    "start_date": ("start_date", "FechaInicio"),
    "end_date": ("end_date", "FechaFin"),
}
_SOURCE_META_KEYS = ("ptos_ganado", "ptos_empatado", "ptos_perdido", "minutos_juego", "numero_partes", "goleadores",
                     "ver_estadisticas_jugador", "visible_clasificacion")
_GROUP_KEYS = ("expected_rounds", "expected_teams", "delegation", "has_standings", "has_scorers")
_TEXT_FIELDS = ("game_type_code", "category_code", "category_name", "category_group_code", "category_group_name", "format_code")


@lru_cache(maxsize=4)
def load_source_config(source: str = "rffm") -> dict[str, Any]:
    """Correspondencias y reglas de clasificación de una fuente (`config/sources/<fuente>.json`)."""
    data: dict[str, Any] = json.loads((SOURCES_DIR / f"{source}.json").read_text(encoding="utf-8"))
    return data


def clean_text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return {"1": True, "0": False, "true": True, "false": False}.get(str(value).strip().lower()) if value is not None else None


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def normalize_source_meta(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Campos oficiales de una competición con los nombres de columna; solo los presentes (nunca borra con `None`)."""
    out: dict[str, Any] = {}
    for field, keys in _SOURCE_KEYS.items():
        value = next((raw[k] for k in keys if k in raw and raw[k] is not None), None)
        parsed = _bool(value) if field == "active" else parse_date(value) if field.endswith("_date") else clean_text(value)
        if parsed is not None:
            out[field] = parsed
    extra = raw.get("source_meta") if isinstance(raw.get("source_meta"), Mapping) else {
        k: raw[k] for k in _SOURCE_META_KEYS if k in raw}
    if extra:
        out["source_meta"] = dict(extra)
    return out


def table_overrides(s: Session, source: str) -> dict[str, dict[str, str]]:
    """Overrides de `catalog_overrides` (kind=competition) por ID externo de competición; el más reciente manda."""
    out: dict[str, dict[str, str]] = {}
    for row in s.scalars(select(CatalogOverride).where(CatalogOverride.source == source, CatalogOverride.kind == "competition")
                         .order_by(CatalogOverride.id)):
        out.setdefault(row.target_external_id, {}).update(
            {a: str(v) for a, v in row.fields.items() if a in ATTRS and v})
    return out


def apply_competition_meta(comp: Competition, incoming: Mapping[str, Any] | None, cfg: Mapping[str, Any],
                           extra_overrides: Mapping[str, str] | None = None, *, reclassify_confirmed: bool = False) -> list[str]:
    """Persiste campos oficiales y clasificación; devuelve los campos modificados (vacío = sin cambios).

    Una competición `confirmed` no se toca salvo `reclassify_confirmed`; entonces el valor anterior de cada atributo que cambia
    queda en `classification.<attr>.previous` y, si cambia algún valor, la competición vuelve al estado calculado.
    """
    confirmed = comp.review_status == "confirmed"
    if confirmed and not reclassify_confirmed:
        return []
    old = comp.classification or {}
    overrides = {a: str(v["value"]) for a, v in old.items() if isinstance(v, dict) and v.get("source") == OVERRIDE}
    overrides.update(extra_overrides or {})
    official = normalize_source_meta(incoming or {})
    changes: dict[str, Any] = {f: v for f, v in official.items() if getattr(comp, f) != v}
    merged = {f: official.get(f, getattr(comp, f)) for f in (*_TEXT_FIELDS, "active")}
    result = classify_competition(CompetitionMeta(name=comp.name or "", **merged), overrides, cfg)
    new_json = result.as_json()
    value_changed = False
    for attr in ATTRS:
        before = old.get(attr) if isinstance(old.get(attr), dict) else None
        if before and before.get("previous") is not None and before.get("value") == new_json[attr]["value"]:
            new_json[attr]["previous"] = before["previous"]  # se conserva (idempotencia)
        if getattr(comp, attr) != result.value(attr):
            value_changed = True
            if confirmed and getattr(comp, attr) != "unknown":
                new_json[attr]["previous"] = getattr(comp, attr)
            changes[attr] = result.value(attr)
    if old != new_json:
        changes["classification"] = new_json
    status = "confirmed" if confirmed and not value_changed else result.review_status
    if comp.review_status != status:
        changes["review_status"] = status
    for field, value in changes.items():
        setattr(comp, field, value)
    return sorted(changes)


def apply_group_meta(group: CompetitionGroup, incoming: Mapping[str, Any]) -> list[str]:
    """Campos de grupo oficiales (solo los presentes)."""
    changes: dict[str, Any] = {}
    for key in _GROUP_KEYS:
        value = incoming.get(key)
        if value is None:
            continue
        value = _bool(value) if key.startswith("has_") else int(value) if key.startswith("expected_") else clean_text(value)
        if value is not None and getattr(group, key) != value:
            changes[key] = value
    for field, value in changes.items():
        setattr(group, field, value)
    return sorted(changes)


def _persist_catalog_meta(engine: Engine, g: Mapping[str, Any], source: str) -> list[str]:
    meta, group_meta = g.get("competition_meta"), {k: g[k] for k in _GROUP_KEYS if g.get(k) is not None}
    if not meta and not group_meta:
        return []
    changed: list[str] = []
    with Session(engine) as s:
        comp = s.scalars(select(Competition).where(Competition.source == source,
                                                   Competition.external_id == g["competition_id"])).one_or_none()
        if comp is None:
            return []
        if meta:
            changed += apply_competition_meta(comp, meta, load_source_config(source),
                                              table_overrides(s, source).get(comp.external_id))
        grp = s.scalars(select(CompetitionGroup).where(CompetitionGroup.source == source,
                                                       CompetitionGroup.external_id == g["group_id"])).one_or_none()
        if grp is not None and group_meta:
            changed += apply_group_meta(grp, group_meta)
        s.commit()
    return changed


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
        result = {**row, "import_status": rep.status, "error": rep.error, "issues": rep.issues,
                  "rounds": rep.rounds_processed, "matches": rep.matches_processed,
                  "inserted": rep.total("inserted"), "updated": rep.total("updated"),
                  "unchanged": rep.total("unchanged")}
        if not dry_run and rep.status != "failed":
            try:
                changed = _persist_catalog_meta(engine, g, load_source_config()["source"])
                if changed:
                    result["catalog_meta_changes"] = changed
            except (SQLAlchemyError, ValueError) as exc:
                result["catalog_meta_error"] = str(exc)[:200]
        results.append(result)
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
