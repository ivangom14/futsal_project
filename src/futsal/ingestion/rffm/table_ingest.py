"""Descarga con caché de las páginas de clasificación y goleadores, con reintento de los `null` intermitentes (sin base de datos).

La caché guarda SOLO páginas que se han analizado bien (una respuesta `null` o con otra estructura nunca queda cacheada). Un
HTML cacheado se reutiliza mientras tenga menos de `max_age_hours` (`None`: indefinidamente) y no se pida `--refresh`.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.ingestion.rffm.client import FetchError, scorers_url, standings_url
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.tables import (
    IdentityMismatch,
    ScorerTable,
    StandingsTable,
    StructureChanged,
    TransientSource,
    parse_scorers,
    parse_standings,
)

Fetch = Callable[[str], str]
KINDS = ("standings", "scorers")
# códigos de error de una captura fallida (se guardan en la tabla de intentos)
DOWNLOAD, TRANSIENT, STRUCTURE, IDENTITY, ROUND_UNAVAILABLE = "download", "transient", "structure", "identity", "round_unavailable"


@dataclass
class TableFetch:
    kind: str
    url: str
    table: StandingsTable | ScorerTable | None = None
    from_cache: bool = False
    fetched_at: datetime | None = None
    error_code: str | None = None
    error: str | None = None
    requested_round: int | None = None

    @property
    def ok(self) -> bool:
        return self.table is not None and self.error_code is None


def cache_path(work: Path, competition: str, group: str, kind: str, round_number: int | None = None) -> Path:
    name = "scorers.html" if kind == "scorers" else f"standings_j{round_number}.html" if round_number else "standings_current.html"
    return work / f"c{competition}" / f"g{group}" / name


def _parse(kind: str, html: str, target: Target) -> StandingsTable | ScorerTable:
    if kind == "standings":
        return parse_standings(html, competition=target.competition, group=target.group)
    return parse_scorers(html, competition=target.competition, group=target.group)


def _age_hours(path: Path, now: datetime) -> float:
    return (now - datetime.fromtimestamp(path.stat().st_mtime, UTC)).total_seconds() / 3600


def fetch_table(kind: str, target: Target, fetch: Fetch, path: Path, *, round_number: int | None = None, refresh: bool = False,
                max_age_hours: float | None = 12.0, null_retries: int = 2, null_delay: float = 3.0,
                sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                cache_only: bool = False) -> TableFetch:
    """Obtiene y analiza una tabla. Nunca lanza: devuelve `TableFetch` con `error_code` si falla."""
    url = (standings_url(target.season, target.competition, target.group, target.game_type, round_number) if kind == "standings"
           else scorers_url(target.season, target.competition, target.group, target.game_type))
    out = TableFetch(kind, url, requested_round=round_number)
    if path.exists() and not refresh and (cache_only or max_age_hours is None or _age_hours(path, now()) < max_age_hours):
        try:
            out.table = _parse(kind, path.read_text(encoding="utf-8"), target)
            out.from_cache = True
            out.fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except (RffmParseError, OSError):
            out.table = None  # caché ilegible o de otra estructura: se vuelve a pedir
        if out.table is not None:
            return _check_round(out, round_number)
    if cache_only:
        out.error_code, out.error = DOWNLOAD, "sin página en caché (modo --offline)"
        return out
    for attempt in range(null_retries + 1):
        try:
            html = fetch(url)
        except FetchError as exc:
            out.error_code, out.error = DOWNLOAD, str(exc)[:300]
            return out
        try:
            out.table = _parse(kind, html, target)
        except TransientSource as exc:
            out.error_code, out.error = TRANSIENT, str(exc)[:300]
            if attempt < null_retries:
                sleep(null_delay * (attempt + 1))
            continue
        except IdentityMismatch as exc:
            out.error_code, out.error = IDENTITY, str(exc)[:300]
            return out
        except (StructureChanged, RffmParseError) as exc:
            out.error_code, out.error = STRUCTURE, str(exc)[:300]
            return out
        out.error_code = out.error = None
        out.fetched_at = now()
        out = _check_round(out, round_number)
        if out.ok:  # solo se cachea lo que se ha entendido y corresponde a lo pedido
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        return out
    out.table = None
    return out


def _check_round(out: TableFetch, requested: int | None) -> TableFetch:
    """Una jornada futura o inexistente devuelve en silencio la actual: no se puede etiquetar como la pedida."""
    table = out.table
    if isinstance(table, StandingsTable) and requested is not None and table.outcome != "not_applicable" \
            and table.round_number != requested:
        out.error_code = ROUND_UNAVAILABLE
        out.error = f"se pidió la jornada {requested} y la web devolvió la {table.round_number}"
    return out


def standings_rounds(current: int | None, *, round_number: int | None, all_rounds: bool) -> list[int | None]:
    """Jornadas a pedir: `[None]` = la actual (sin parámetro); una concreta; o todas hasta la actual (la actual, sin parámetro, primero)."""
    if round_number is not None:
        return [round_number]
    if not all_rounds:
        return [None]
    return [None] + list(range(1, (current or 1)))


def summary(results: list[TableFetch]) -> dict[str, Any]:
    return {"fetched": sum(1 for r in results if not r.from_cache), "from_cache": sum(1 for r in results if r.from_cache),
            "failed": sum(1 for r in results if r.error_code)}
