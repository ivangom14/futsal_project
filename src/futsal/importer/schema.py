"""Lectura y validación estructural del JSON de liga (sin red ni base de datos)."""

import json
from datetime import date, time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError


class _Ext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    external_id: str


class NamedExt(_Ext):
    name: str | None = None


class RoundIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    external_round_id: str
    visible_round_label: str
    visible_round_number: int | None = None
    scheduled_date: date | None = None
    matches: int | None = None
    source_url: str
    status: str


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    external_match_id: str | None
    external_round_id: str
    home_team_external_id: str | None
    home_team_name: str | None
    away_team_external_id: str | None
    away_team_name: str | None
    home_score: int | None
    away_score: int | None
    status: str
    scheduled_date: date | None
    scheduled_time: time | None
    timezone: str
    venue: str | None
    source_url: str


class LeagueFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str
    season: NamedExt
    competition: NamedExt
    group: NamedExt
    rounds: list[RoundIn]
    matches: list[MatchIn]


class InputError(ValueError):
    """JSON ilegible o con estructura inválida."""


def read_league(path: Path) -> LeagueFile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"no se puede leer {path}: {exc}") from exc
    try:
        return LeagueFile.model_validate(raw)
    except ValidationError as exc:
        raise InputError(f"estructura inválida en {path}: {exc.error_count()} errores; "
                         f"primero: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}") from exc
