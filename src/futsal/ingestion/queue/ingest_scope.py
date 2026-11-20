"""Alcance declarativo de la ingesta por deporte y competición (`config/ingest_scope.json`). Lógica pura salvo `gate_target`.

Niveles: descubierta (catálogo oficial) → en alcance (casa con una entrada o el deporte es `all`) → habilitada (deporte y
entrada `enabled`). Lo usan el descubrimiento (`discover_catalog` solo encola grupos de competiciones habilitadas), el
planificador (`gather_state` solo planifica grupos habilitados) y el trabajador (rechaza sin red un trabajo fuera de alcance).
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from futsal.domain.names import norm_name

SCOPE_PATH = Path(__file__).parents[2] / "config" / "ingest_scope.json"
_SEASON_LABEL = re.compile(r"\b\d{4}\s*/\s*\d{2,4}\b|\b\d{4}\s*-\s*\d{2,4}\b")

OUT_OF_SCOPE = "out_of_scope"
SPORT_DISABLED = "sport_disabled"
IN_SCOPE_DISABLED = "in_scope_disabled"
ENABLED = "enabled"


def scope_name(name: str | None) -> str:
    """Nombre comparable: sin acentos, signos ni rótulo de temporada («COPA … 2026/2027» = «COPA …» de otra temporada)."""
    return norm_name(_SEASON_LABEL.sub(" ", name or ""))


@dataclass(frozen=True)
class Entry:
    name: str
    category_code: str | None
    enabled: bool

    def matches(self, name: str | None, category_code: str | None) -> bool:
        if self.category_code is not None and (category_code or "").strip() != self.category_code:
            return False
        return scope_name(name) == scope_name(self.name)


@dataclass(frozen=True)
class SportScope:
    sport: str
    game_type: str
    enabled: bool
    all_competitions: bool
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class IngestScope:
    source: str
    sports: Mapping[str, SportScope]

    def sport_of_game_type(self, game_type: str | None) -> str | None:
        return next((s.sport for s in self.sports.values() if s.game_type == str(game_type or "").strip()), None)

    def status(self, sport: str | None, name: str | None, category_code: str | None) -> str:
        cfg = self.sports.get(sport or "")
        if cfg is None:
            return OUT_OF_SCOPE
        if cfg.all_competitions:
            return ENABLED if cfg.enabled else SPORT_DISABLED
        entry = next((e for e in cfg.entries if e.matches(name, category_code)), None)
        if entry is None:
            return OUT_OF_SCOPE
        if not cfg.enabled:
            return SPORT_DISABLED
        return ENABLED if entry.enabled else IN_SCOPE_DISABLED

    def allows(self, sport: str | None, name: str | None, category_code: str | None) -> bool:
        return self.status(sport, name, category_code) == ENABLED

    def sport_enabled(self, sport: str | None) -> bool:
        cfg = self.sports.get(sport or "")
        return bool(cfg and cfg.enabled)

    def enabled_game_types(self) -> list[str]:
        return [s.game_type for s in self.sports.values() if s.enabled]


def parse_scope(raw: Mapping[str, Any]) -> IngestScope:
    sports: dict[str, SportScope] = {}
    for sport, cfg in (raw.get("sports") or {}).items():
        comps = cfg.get("competitions")
        if comps != "all" and not isinstance(comps, list):
            raise ValueError(f"ingest_scope: '{sport}.competitions' debe ser 'all' o una lista")
        entries = tuple(Entry(str(e["name"]), str(e["category_code"]) if e.get("category_code") else None, bool(e.get("enabled")))
                        for e in (comps if isinstance(comps, list) else []))
        if not cfg.get("game_type"):
            raise ValueError(f"ingest_scope: '{sport}' sin game_type")
        sports[sport] = SportScope(sport, str(cfg["game_type"]), bool(cfg.get("enabled")), comps == "all", entries)
    if len({s.game_type for s in sports.values()}) != len(sports):
        raise ValueError("ingest_scope: dos deportes con el mismo game_type")
    return IngestScope(str(raw.get("source") or "rffm"), sports)


@lru_cache(maxsize=4)
def load_scope(path: Path = SCOPE_PATH) -> IngestScope:
    return parse_scope(json.loads(path.read_text(encoding="utf-8")))


def competition_meta_fields(meta: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """(nombre, CodigoCategoria) de los metadatos de una competición (crudos de /api/competitions o normalizados)."""
    meta = meta or {}
    name = meta.get("nombre") or meta.get("name") or meta.get("competition_name")
    code = meta.get("CodigoCategoria") or meta.get("category_code") or meta.get("category_id")
    return (str(name) if name else None, str(code).strip() if code else None)


def gate_target(session: Session, scope: IngestScope, target: Mapping[str, Any], source: str = "rffm") -> str | None:
    """None si el trabajo está habilitado; si no, el motivo. Sin red: solo lee la competición de la BD si hace falta."""
    from futsal.db.models import Competition  # import local: el resto del módulo es puro

    game_type = target.get("game_type")
    sport = scope.sport_of_game_type(str(game_type) if game_type is not None else None)
    if sport is None:
        return f"tipo de juego {game_type!r} fuera del alcance de ingesta"
    if not scope.sport_enabled(sport):
        return f"deporte {sport} deshabilitado en el alcance de ingesta"
    cid = target.get("competition")
    if cid is None or scope.sports[sport].all_competitions:
        return None
    name, code = competition_meta_fields(target.get("meta") if isinstance(target.get("meta"), Mapping) else None)
    if name is None:
        row = session.execute(select(Competition.name, Competition.category_code)
                              .where(Competition.source == source, Competition.external_id == str(cid))).one_or_none()
        if row is None:
            return f"competición {cid} desconocida: fuera del alcance de ingesta"
        name, code = row
    status = scope.status(sport, name, code)
    return None if status == ENABLED else f"competición {cid} ({name}) {status}"
