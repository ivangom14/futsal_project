"""Modelo de la base de conocimiento documental (Fase 15). Independiente del modelo deportivo."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from futsal.db.models import Base


class KbDocument(Base):
    __tablename__ = "kb_documents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_key: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    version_label: Mapped[str | None] = mapped_column(Text)
    season_label: Mapped[str | None] = mapped_column(Text)
    structure_profile: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KbDocumentVersion(Base):
    __tablename__ = "kb_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "content_hash"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text)
    file_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    version_label: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    extraction_status: Mapped[str] = mapped_column(Text, default="pending")
    index_status: Mapped[str] = mapped_column(Text, default="pending")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    chunk_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (UniqueConstraint("version_id", "ordinal"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("kb_document_versions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    part: Mapped[str | None] = mapped_column(Text)
    rule_number: Mapped[int | None] = mapped_column(Integer)
    rule_title: Mapped[str | None] = mapped_column(Text)
    section_number: Mapped[str | None] = mapped_column(Text)
    section_title: Mapped[str | None] = mapped_column(Text)
    subsection_title: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "setweight(to_tsvector('spanish', kb_unaccent(coalesce(rule_title,'') || ' ' || "
        "coalesce(section_title,'') || ' ' || coalesce(subsection_title,''))), 'A') || "
        "setweight(to_tsvector('spanish', kb_unaccent(text)), 'B')", persisted=True))


class KbIngestionRun(Base):
    __tablename__ = "kb_ingestion_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_key: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(Text, default="running")
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("kb_document_versions.id", ondelete="SET NULL"))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
