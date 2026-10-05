"""Modelos normalizados del acta de un partido (solo campos observados en la fuente)."""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel

Side = Literal["home", "away"]
LineupStatus = Literal["starter", "substitute", "unknown"]


class ReportInfo(BaseModel):
    report_external_id: str
    match_external_id: str
    season_external_id: str
    competition_external_id: str
    group_external_id: str
    round_external_id: str
    closed: bool | None
    content_hash: str


class ReportMatch(BaseModel):
    home_team_name: str | None
    home_team_external_id: str | None
    away_team_name: str | None
    away_team_external_id: str | None
    scheduled_date: date | None
    scheduled_time: time | None
    timezone: str
    venue: str | None
    venue_external_id: str | None
    home_score: int | None
    away_score: int | None
    status: str


class ReportTeam(BaseModel):
    side: Side
    external_id: str | None
    name: str | None
    formation: str | None
    crest_url: str | None


class ReportPlayer(BaseModel):
    team_side: Side
    team_external_id: str | None
    sequence: int
    player_external_id: str | None
    display_name: str
    shirt_number: int | None
    role: str
    lineup_status: LineupStatus
    captain: bool


class ReportStaff(BaseModel):
    team_side: Side | None
    team_external_id: str | None
    sequence: int
    display_name: str
    role: str
    external_id: str | None


class ReportOfficial(BaseModel):
    sequence: int
    display_name: str
    role: str
    external_id: str | None


class ReportEvent(BaseModel):
    sequence: int
    event_type: Literal["goal", "card"]
    team_side: Side
    team_external_id: str | None
    player_external_id: str | None
    player_name: str | None
    minute: int | None
    source_code: str | None
    source_detail: dict[str, str] | None


class ComparisonRow(BaseModel):
    field: str
    matches_value: str | None
    report_value: str | None
    equal: bool


class Validation(BaseModel):
    empty_sections: list[str]
    unparsed_sections: list[str]
    comparison: list[ComparisonRow] | None = None


class ReportSource(BaseModel):
    url: str
    system: str
    technical_source: str
    observed_at: datetime


class MatchReport(BaseModel):
    match: ReportMatch
    report: ReportInfo
    teams: list[ReportTeam]
    players: list[ReportPlayer]
    staff: list[ReportStaff]
    officials: list[ReportOfficial]
    events: list[ReportEvent]
    validation: Validation
    source: ReportSource
