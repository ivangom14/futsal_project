"""Clasificaciones y tablas de goleadores OFICIALES de la RFFM (Fase 16). Independiente de partidos y actas.

Cada captura es una fila de `*_snapshots` identificada por el hash de su contenido: repetir la ingesta no duplica y un cambio de
contenido crea otra captura (historial). Los enlaces a `teams`/`players` son opcionales: los identificadores externos se guardan siempre.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from futsal.db.models import Base


class StandingsSnapshot(Base):
    """Clasificación publicada para una jornada concreta, tal como la entregó la RFFM en un momento dado."""

    __tablename__ = "standings_snapshots"
    __table_args__ = (
        UniqueConstraint("group_id", "round_number", "content_hash"),
        CheckConstraint("status IN ('ok','empty','incomplete')", name="status_valid"),
        Index("ix_standings_snapshots_group_round_seen", "group_id", "round_number", "last_seen_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)  # jornada que devuelve la web (no la pedida)
    round_date: Mapped[date | None] = mapped_column(Date)
    current_round: Mapped[int | None] = mapped_column(Integer)  # jornada actual del grupo cuando se capturó
    status: Mapped[str] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    expected_teams: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # promociones, avisos de calidad


class StandingsRow(Base):
    __tablename__ = "standings_rows"
    __table_args__ = (UniqueConstraint("snapshot_id", "team_external_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("standings_snapshots.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # posición publicada por la RFFM
    team_external_id: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    team_name: Mapped[str] = mapped_column(String(256))
    played: Mapped[int] = mapped_column(Integer)
    won: Mapped[int] = mapped_column(Integer)
    drawn: Mapped[int] = mapped_column(Integer)
    lost: Mapped[int] = mapped_column(Integer)
    goals_for: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    penalty_points: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # parciales casa/fuera, racha, color de promoción…


class ScorerSnapshot(Base):
    """Tabla de goleadores publicada (la web solo ofrece la tabla actual del grupo)."""

    __tablename__ = "scorer_snapshots"
    __table_args__ = (
        UniqueConstraint("group_id", "content_hash"),
        CheckConstraint("status IN ('ok','empty','incomplete')", name="status_valid"),
        Index("ix_scorer_snapshots_group_seen", "group_id", "last_seen_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    goals_total: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ScorerRow(Base):
    __tablename__ = "scorer_rows"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "row_order"),
        Index("ix_scorer_rows_snapshot_player", "snapshot_id", "player_external_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("scorer_snapshots.id", ondelete="CASCADE"))
    row_order: Mapped[int] = mapped_column(Integer)  # orden publicado; la RFFM no da posición explícita
    player_external_id: Mapped[str] = mapped_column(String(64))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(256))
    team_external_id: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    team_name: Mapped[str] = mapped_column(String(256))
    matches_played: Mapped[int | None] = mapped_column(Integer)
    goals: Mapped[int] = mapped_column(Integer)
    penalty_goals: Mapped[int | None] = mapped_column(Integer)
    goals_per_match: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class TableIngestAttempt(Base):
    """Registro de cada intento (correcto, vacío, no aplicable o fallido): permite reanudar, informar y no perder los errores."""

    __tablename__ = "table_ingest_attempts"
    __table_args__ = (
        CheckConstraint("kind IN ('standings','scorers')", name="kind_valid"),
        CheckConstraint("outcome IN ('ok','empty','incomplete','not_applicable','failed','round_unavailable')", name="outcome_valid"),
        Index("ix_table_ingest_attempts_group_kind_at", "group_id", "kind", "attempted_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("competition_groups.id"))
    kind: Mapped[str] = mapped_column(String(16))
    requested_round: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24))
    change: Mapped[str | None] = mapped_column(String(16))  # new | unchanged (si hubo captura)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(BigInteger)  # sin FK: apunta a la tabla de capturas según `kind`
    returned_round: Mapped[int | None] = mapped_column(Integer)
    http_requests: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
