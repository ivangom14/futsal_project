"""Ingesta idempotente de documentos: hash → extracción → estructura → fragmentos → PostgreSQL.

Cada versión (hash distinto) tiene sus propios fragmentos; solo una es `is_current` por documento
y la búsqueda usa únicamente esa salvo que se pida el historial. Una versión nueva se indexa
completa en una transacción y solo entonces releva a la anterior, que conserva sus fragmentos.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from futsal.db.kb_models import KbChunk, KbDocument, KbDocumentVersion, KbIngestionRun
from futsal.knowledge.chunking import ChunkConfig, chunk_segments
from futsal.knowledge.extract import ExtractionError, get_extractor
from futsal.knowledge.profiles import PROFILES
from futsal.knowledge.quality import assess
from futsal.knowledge.structure import build_structure

STORAGE_DIR = Path("data/knowledge")


@dataclass(frozen=True)
class DocumentSpec:
    """Metadatos declarados del documento. Lo que no conste se deja en None (no se infiere)."""

    doc_key: str
    title: str
    doc_type: str
    language: str
    structure_profile: str = "plain"
    source_url: str | None = None
    issuer: str | None = None
    publication_date: date | None = None
    effective_date: date | None = None
    version_label: str | None = None
    season_label: str | None = None


@dataclass
class IngestResult:
    doc_key: str
    outcome: str  # new | unchanged | updated | restored | reindexed | failed
    version_id: int | None = None
    chunks: int = 0
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _store(path: Path, doc_key: str, digest: str, storage: Path) -> Path:
    """Copia el original a `<storage>/<doc_key>/<hash16><ext>` (la BD solo guarda la ruta)."""
    dest = storage / doc_key / f"{digest[:16]}{path.suffix.lower()}"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return dest


def ingest_document(engine: Engine, spec: DocumentSpec, path: Path, *,
                    chunk_config: ChunkConfig | None = None, storage: Path = STORAGE_DIR,
                    force: bool = False) -> IngestResult:
    cfg = chunk_config or ChunkConfig()
    with Session(engine) as s:
        run = KbIngestionRun(doc_key=spec.doc_key)
        s.add(run)
        s.commit()
        run_id = run.id
    try:
        result = _ingest(engine, spec, path, cfg, storage, force)
    except Exception as exc:  # noqa: BLE001 - se registra y se informa sin traza ni secretos
        result = IngestResult(spec.doc_key, "failed", error=f"{type(exc).__name__}: {exc}"[:500])
        _mark_failed(engine, spec, path)
    with Session(engine) as s:
        done = s.get(KbIngestionRun, run_id)
        assert done is not None
        done.outcome, done.version_id = result.outcome, result.version_id
        done.finished_at = datetime.now(UTC)
        done.warnings, done.error = result.warnings or None, result.error
        done.stats = {"chunks": result.chunks, "pages": result.pages}
        s.commit()
    return result


def _mark_failed(engine: Engine, spec: DocumentSpec, path: Path) -> None:
    """Si la versión ya se había registrado, queda marcada como fallida (se reintentará)."""
    try:
        digest = file_sha256(path)
    except OSError:
        return
    with Session(engine) as s:
        doc = s.scalar(select(KbDocument).where(KbDocument.doc_key == spec.doc_key))
        if doc is None:
            return
        ver = s.scalar(select(KbDocumentVersion).where(
            KbDocumentVersion.document_id == doc.id, KbDocumentVersion.content_hash == digest))
        if ver is not None and ver.index_status != "indexed":
            ver.index_status = "failed"
            s.commit()


def _apply_metadata(doc: KbDocument, spec: DocumentSpec) -> None:
    doc.title, doc.doc_type, doc.language = spec.title, spec.doc_type, spec.language
    doc.structure_profile, doc.source_url, doc.issuer = (
        spec.structure_profile, spec.source_url, spec.issuer)
    doc.publication_date, doc.effective_date = spec.publication_date, spec.effective_date
    doc.version_label, doc.season_label = spec.version_label, spec.season_label
    doc.updated_at = datetime.now(UTC)


def _ingest(engine: Engine, spec: DocumentSpec, path: Path, cfg: ChunkConfig, storage: Path,
            force: bool) -> IngestResult:
    if spec.structure_profile not in PROFILES:
        raise ValueError(f"perfil de estructura desconocido: {spec.structure_profile}")
    if not path.is_file():
        raise FileNotFoundError(f"no existe el archivo: {path}")
    digest = file_sha256(path)
    with Session(engine) as s:
        doc = s.scalar(select(KbDocument).where(KbDocument.doc_key == spec.doc_key))
        existing = None
        if doc is not None:
            existing = s.scalar(select(KbDocumentVersion).where(
                KbDocumentVersion.document_id == doc.id,
                KbDocumentVersion.content_hash == digest))
        if doc is not None and existing is not None and existing.index_status == "indexed" \
                and not force:
            _apply_metadata(doc, spec)
            outcome = "unchanged"
            if not existing.is_current:  # volver a una versión anterior
                _make_current(s, existing)
                outcome = "restored"
            s.commit()
            return IngestResult(spec.doc_key, outcome, existing.id, _count(s, existing.id),
                                existing.page_count or 0)

    extractor = get_extractor(path)
    extracted = extractor.extract(path)
    profile = PROFILES[spec.structure_profile]
    structured = build_structure(extracted, profile)
    report = assess(extracted, structured, profile)
    chunks = chunk_segments(structured.segments, cfg)
    if not chunks:
        raise ExtractionError("el documento no contiene texto extraíble")
    warnings = list(extracted.warnings)
    if report.flagged.get("sin_texto"):
        warnings.append(f"páginas sin texto: {report.flagged['sin_texto']}")
    if report.rules_start_mismatch:
        warnings.append(f"discrepancias con el índice: {report.rules_start_mismatch}")
    stored = _store(path, spec.doc_key, digest, storage)

    with Session(engine) as s, s.begin():
        doc = s.scalar(select(KbDocument).where(KbDocument.doc_key == spec.doc_key))
        is_new_doc = doc is None
        if doc is None:
            doc = KbDocument(doc_key=spec.doc_key, title=spec.title, doc_type=spec.doc_type,
                             language=spec.language, structure_profile=spec.structure_profile)
            s.add(doc)
        _apply_metadata(doc, spec)
        s.flush()
        had_current = s.scalar(select(KbDocumentVersion.id).where(
            KbDocumentVersion.document_id == doc.id, KbDocumentVersion.is_current)) is not None
        ver = existing = s.scalar(select(KbDocumentVersion).where(
            KbDocumentVersion.document_id == doc.id, KbDocumentVersion.content_hash == digest))
        if ver is None:
            ver = KbDocumentVersion(document_id=doc.id, content_hash=digest)
            s.add(ver)
        else:  # reindexación (versión fallida o --force): se reconstruyen sus fragmentos
            s.execute(delete(KbChunk).where(KbChunk.version_id == ver.id))
        ver.file_path, ver.file_bytes = str(stored), path.stat().st_size
        ver.page_count, ver.version_label = len(extracted.pages), spec.version_label
        ver.extraction_status = "partial" if extracted.warnings else "ok"
        ver.chunk_config, ver.is_current = cfg.as_dict(), False
        ver.quality = {k: v for k, v in report.to_dict().items() if k != "pages"}
        s.flush()
        s.add_all(KbChunk(
            version_id=ver.id, ordinal=c.ordinal, part=c.part, rule_number=c.rule_number,
            rule_title=c.rule_title, section_number=c.section_number,
            section_title=c.section_title, subsection_title=c.subsection_title,
            page_start=c.page_start, page_end=c.page_end, text=c.text, char_count=len(c.text))
            for c in chunks)
        ver.index_status = "indexed"
        s.flush()
        _make_current(s, ver)
        if existing is not None:
            outcome = "reindexed"  # mismo contenido: reconstrucción de fragmentos
        else:
            outcome = "new" if is_new_doc or not had_current else "updated"
        return IngestResult(spec.doc_key, outcome, ver.id, len(chunks), len(extracted.pages),
                            warnings)


def _make_current(s: Session, ver: KbDocumentVersion) -> None:
    s.execute(update(KbDocumentVersion).where(
        KbDocumentVersion.document_id == ver.document_id, KbDocumentVersion.is_current,
        KbDocumentVersion.id != ver.id).values(is_current=False))
    s.flush()
    ver.is_current = True


def _count(s: Session, version_id: int) -> int:
    return int(s.scalar(select(func.count()).select_from(KbChunk).where(
        KbChunk.version_id == version_id)) or 0)
