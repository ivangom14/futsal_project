"""Catálogo RFFM: competiciones de un tipo de juego y sus grupos (sin red propia).

El listado de competiciones va en `pageProps.competitions` de cualquier página con una
competición+grupo de ese tipo de juego; los grupos de cada competición, en `/api/groups`
(el mismo JSON que consume la web). Los IDs son siempre los devueltos por la RFFM.
"""

import json
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.ingestion.rffm.client import FetchError, group_url, groups_url
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props

Fetch = Callable[[str], str]
CONFIRMED, AMBIGUOUS, NOT_FOUND = "confirmed", "ambiguous", "not_found"
GROUPS_OK, NO_GROUPS, GROUPS_FAILED, GROUPS_SKIPPED = "ok", "no_groups", "fetch_failed", "not_requested"


def normalize_name(value: str) -> str:
    """Minúsculas, sin acentos ni puntuación y con espacios colapsados (`fase final- FS` == `fase final - FS`)."""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _s(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def parse_competitions(html: str) -> tuple[dict[str, str | None], list[dict[str, str | None]]]:
    """(temporada, competiciones) del catálogo de una página de la web."""
    props = extract_page_props(html)
    raw = props.get("competitions")
    if not isinstance(raw, list) or not raw:
        raise RffmParseError("La página no incluye pageProps.competitions")
    season = props.get("season") or {}
    comps = [
        {"external_id": _s(c.get("codigo")), "name": _s(c.get("nombre")),
         "game_type": _s(c.get("codigo_tipo_juego")), "category": _s(c.get("NombreCategoria")),
         "category_id": _s(c.get("CodigoCategoria")), "active": _s(c.get("Activa")),
         "start_date": _s(c.get("FechaInicio")), "end_date": _s(c.get("FechaFin"))}
        for c in raw if isinstance(c, dict) and _s(c.get("codigo")) and _s(c.get("nombre"))
    ]
    return ({"external_id": _s(season.get("cod_temporada")), "name": _s(season.get("nombre"))}, comps)


def resolve_scope(competitions: Sequence[dict[str, str | None]], scope: Sequence[tuple[str, str]],
                  game_type: str) -> list[dict[str, Any]]:
    """Una entrada por elemento del alcance: confirmada (1 coincidencia exacta), ambigua (>1) o no localizada.

    Solo se compara el nombre normalizado completo, nunca por prefijo: `Copa Juvenil - fase final`
    no se confunde con `Copa Juvenil Femenino - Fase final`.
    """
    entries: list[dict[str, Any]] = []
    for level, label in scope:
        key = normalize_name(label)
        found = [c for c in competitions
                 if c["game_type"] == game_type and normalize_name(c["name"] or "") == key]
        ids = sorted({c["external_id"] or "" for c in found})
        entry: dict[str, Any] = {"key": key, "requested_label": label, "level": level,
                                 "identification": NOT_FOUND, "competition": None, "candidates": []}
        if len(ids) == 1:
            entry.update(identification=CONFIRMED, competition=found[0])
        elif ids:
            entry.update(identification=AMBIGUOUS, candidates=found)
        entries.append(entry)
    return entries


def parse_groups(payload: Any) -> list[dict[str, Any]]:
    """Grupos del JSON de `/api/groups`; ignora duplicados y entradas sin código."""
    if not isinstance(payload, list):
        raise RffmParseError("la respuesta de grupos no es una lista")
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for g in payload:
        gid = _s(g.get("codigo")) if isinstance(g, dict) else None
        if not gid or gid in seen:
            continue
        seen.add(gid)
        total_rounds, total_teams = _s(g.get("total_jornadas")), _s(g.get("total_equipos"))
        groups.append({
            "external_id": gid, "name": _s(g.get("nombre")),
            "expected_rounds": int(total_rounds) if total_rounds and total_rounds.isdigit() else None,
            "expected_teams": int(total_teams) if total_teams and total_teams.isdigit() else None,
            "delegation": _s(g.get("nombre_delegacion")),
        })
    return groups


def _cached(path: Path, fetch: Fetch, url: str, refresh: bool) -> tuple[str, bool]:
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8"), True
    text = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text, False


def discover_catalog(reference: Target, scope: Sequence[tuple[str, str]], fetch: Fetch, out_dir: Path,
                     *, refresh: bool = False,
                     now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Inventario del alcance: identifica cada competición y descubre todos sus grupos.

    `reference` es un grupo futsal conocido: su página trae el catálogo completo del tipo de juego.
    Los grupos descargados se guardan por competición y se reutilizan; los fallos no se cachean.
    """
    url = group_url(reference.season, reference.competition, reference.group, reference.game_type)
    try:
        html, _ = _cached(out_dir / "catalog.html", fetch, url, refresh)
        season, competitions = parse_competitions(html)
    except (FetchError, RffmParseError) as exc:
        raise RuntimeError(f"No se pudo obtener el catálogo de competiciones: {exc}") from exc
    entries = resolve_scope(competitions, scope, reference.game_type)
    groups_from_cache = 0
    for entry in entries:
        entry.update(groups=[], groups_status=GROUPS_SKIPPED, groups_error=None)
        comp = entry["competition"]
        if comp is None:
            continue
        gurl = groups_url(comp["external_id"])
        try:
            text, from_cache = _cached(out_dir / "groups" / f"{comp['external_id']}.json", fetch, gurl, refresh)
            try:
                groups = parse_groups(json.loads(text))
            except (json.JSONDecodeError, RffmParseError):
                (out_dir / "groups" / f"{comp['external_id']}.json").unlink(missing_ok=True)
                raise
        except (FetchError, RffmParseError, json.JSONDecodeError) as exc:
            entry.update(groups_status=GROUPS_FAILED, groups_error=str(exc)[:200])
            continue
        groups_from_cache += from_cache
        entry.update(groups=groups, groups_status=GROUPS_OK if groups else NO_GROUPS)
    statuses = [e["identification"] for e in entries]
    return {
        "season": season, "game_type": reference.game_type, "source_url": url,
        "generated_at": now().isoformat(), "entries": entries,
        "summary": {
            "requested": len(entries), "confirmed": statuses.count(CONFIRMED),
            "ambiguous": statuses.count(AMBIGUOUS), "not_found": statuses.count(NOT_FOUND),
            "groups_total": sum(len(e["groups"]) for e in entries),
            "competitions_without_groups": sum(e["groups_status"] == NO_GROUPS for e in entries),
            "group_requests_failed": sum(e["groups_status"] == GROUPS_FAILED for e in entries),
            "catalog_competitions": len(competitions), "groups_from_cache": groups_from_cache,
        },
    }
