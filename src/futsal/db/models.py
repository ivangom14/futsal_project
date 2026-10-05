"""Modelo relacional (SQLAlchemy 2). Claves internas + identificadores externos de RFFM."""

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Tracked:
    """Campos comunes de entidades importadas."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="rffm")
    external_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    source_url: Mapped[str | None] = mapped_column(Text)


class Season(Tracked, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    name: Mapped[str | None] = mapped_column(String(128))


class Competition(Tracked, Base):
    __tablename__ = "competitions"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(256))


class CompetitionGroup(Tracked, Base):
    __tablename__ = "competition_groups"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(256))


class Round(Tracked, Base):
    __tablename__ = "rounds"
    __table_args__ = (
        UniqueConstraint("group_id", "external_id"),
        UniqueConstraint("id", "group_id"),
    )
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"), index=True)
    visible_label: Mapped[str] = mapped_column(String(64))
    visible_number: Mapped[int | None] = mapped_column(Integer)
    scheduled_date: Mapped[date | None] = mapped_column(Date)


class Team(Tracked, Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("group_id", "external_id"),
        UniqueConstraint("id", "group_id"),
    )
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(256))


class Match(Tracked, Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        ForeignKeyConstraint(["round_id", "group_id"], ["rounds.id", "rounds.group_id"]),
        ForeignKeyConstraint(["home_team_id", "group_id"], ["teams.id", "teams.group_id"]),
        ForeignKeyConstraint(["away_team_id", "group_id"], ["teams.id", "teams.group_id"]),
        CheckConstraint("home_team_id <> away_team_id", name="home_away_differ"),
        CheckConstraint("(home_score IS NULL) = (away_score IS NULL)", name="score_pair"),
        CheckConstraint("home_score >= 0 AND away_score >= 0", name="score_non_negative"),
        CheckConstraint(
            "status IN ('scheduled','finished','postponed','suspended','cancelled','unknown')",
            name="status_valid"),
        Index("ix_matches_round_id", "round_id"),
        Index("ix_matches_home_team_id", "home_team_id"),
        Index("ix_matches_away_team_id", "away_team_id"),
        Index("ix_matches_status", "status"),
    )
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"), index=True)
    round_id: Mapped[int] = mapped_column(BigInteger)
    home_team_id: Mapped[int] = mapped_column(BigInteger)
    away_team_id: Mapped[int] = mapped_column(BigInteger)
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    scheduled_time: Mapped[time | None] = mapped_column(Time)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64))
    venue: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','success','failed')", name="status_valid"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), default="rffm")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    input_path: Mapped[str] = mapped_column(Text)
    rounds_processed: Mapped[int] = mapped_column(Integer, default=0)
    matches_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    observations_created: Mapped[int] = mapped_column(Integer, default=0)
    issues_created: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class MatchObservation(Base):
    __tablename__ = "match_observations"
    __table_args__ = (
        CheckConstraint("(home_score IS NULL) = (away_score IS NULL)", name="score_pair"),
        Index("ix_match_observations_match_id_observed_at", "match_id", "observed_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    scheduled_time: Mapped[time | None] = mapped_column(Time)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class MatchReport(Tracked, Base):
    """Acta de un partido (una por partido). El contenido vive en las observaciones."""

    __tablename__ = "match_reports"
    __table_args__ = (UniqueConstraint("source", "external_id"), UniqueConstraint("match_id"))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))


class MatchReportObservation(Base):
    """Versión observada de un acta, identificada por el hash de su contenido normalizado."""

    __tablename__ = "match_report_observations"
    __table_args__ = (
        UniqueConstraint("report_id", "content_hash"),
        CheckConstraint("(home_score IS NULL) = (away_score IS NULL)", name="score_pair"),
        Index("ix_match_report_observations_report_id_observed_at", "report_id", "observed_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("match_reports.id"))
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    closed: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16))
    round_external_id: Mapped[str] = mapped_column(String(64))
    home_team_external_id: Mapped[str | None] = mapped_column(String(64))
    away_team_external_id: Mapped[str | None] = mapped_column(String(64))
    home_formation: Mapped[str | None] = mapped_column(String(32))
    away_formation: Mapped[str | None] = mapped_column(String(32))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    scheduled_time: Mapped[time | None] = mapped_column(Time)
    venue: Mapped[str | None] = mapped_column(String(256))


class Player(Tracked, Base):
    """Jugador con identificador externo RFFM (`codjugador`). Solo identidad; sin datos personales."""

    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    display_name: Mapped[str] = mapped_column(String(256))


class PlayerTeamMembership(Base):
    """Equipo con el que se ha visto jugar a un jugador en una temporada (observado, no permanente)."""

    __tablename__ = "player_team_memberships"
    __table_args__ = (UniqueConstraint("player_id", "team_id", "season_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()


class MatchPlayer(Base):
    """Participación de una persona en el acta. `player_id` nulo si la fuente no da identificador."""

    __tablename__ = "match_players"
    __table_args__ = (
        UniqueConstraint("observation_id", "team_id", "sequence"),
        Index("uq_match_players_observation_id_team_id_player_external_id", "observation_id",
              "team_id", "player_external_id", unique=True,
              postgresql_where=text("player_external_id IS NOT NULL")),
        CheckConstraint("lineup_status IN ('starter','substitute','unknown')", name="lineup_valid"),
        CheckConstraint("shirt_number IS NULL OR shirt_number >= 0", name="shirt_non_negative"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("match_report_observations.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    player_external_id: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(256))
    shirt_number: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    lineup_status: Mapped[str] = mapped_column(String(16))
    is_captain: Mapped[bool] = mapped_column(Boolean)
    sequence: Mapped[int] = mapped_column(Integer)


class MatchStaff(Base):
    __tablename__ = "match_staff"
    __table_args__ = (UniqueConstraint("observation_id", "sequence"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("match_report_observations.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)


class MatchOfficial(Base):
    __tablename__ = "match_officials"
    __table_args__ = (UniqueConstraint("observation_id", "sequence"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("match_report_observations.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (
        UniqueConstraint("observation_id", "sequence"),
        CheckConstraint("event_type IN ('goal','card')", name="type_valid"),
        CheckConstraint("minute IS NULL OR minute >= 0", name="minute_non_negative"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("match_report_observations.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(16))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    player_external_id: Mapped[str | None] = mapped_column(String(64))
    player_name: Mapped[str | None] = mapped_column(String(256))
    minute: Mapped[int | None] = mapped_column(Integer)
    source_code: Mapped[str | None] = mapped_column(String(32))
    source_detail: Mapped[dict[str, str] | None] = mapped_column(JSONB)
