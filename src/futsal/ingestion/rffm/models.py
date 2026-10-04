"""Modelos normalizados de la jornada y los partidos de RFFM."""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel

MatchStatus = Literal["scheduled", "finished", "postponed", "suspended", "cancelled", "unknown"]


class Match(BaseModel):
    external_match_id: str | None
    home_team_name: str | None
    home_team_external_id: str | None
    away_team_name: str | None
    away_team_external_id: str | None
    home_score: int | None
    away_score: int | None
    status: MatchStatus
    status_raw: str | None
    scheduled_date: date | None
    scheduled_time: time | None
    timezone: str
    venue: str | None
    venue_external_id: str | None
    match_report_url: str | None
    comparison_url: str | None
    source_url: str


class RoundData(BaseModel):
    external_round_id: str
    round_number_or_label: str
    round_date: date | None
    season_external_id: str
    competition_external_id: str
    competition_name: str | None
    group_external_id: str
    group_name: str | None
    source_url: str
    observed_at: datetime
    matches: list[Match]
