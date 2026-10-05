"""Modelo relacional (SQLAlchemy 2). Claves internas + identificadores externos de RFFM."""

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
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
)
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
