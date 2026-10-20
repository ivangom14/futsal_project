"""Comandos CLI de la base de conocimiento (Fase 15)."""

import argparse
import json
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
from futsal.db.session import make_engine
from futsal.knowledge.chunking import ChunkConfig, chunk_segments
from futsal.knowledge.ingest import DocumentSpec, ingest_document
from futsal.knowledge.search import KnowledgeSearch, SearchError
from futsal.knowledge.extract import ExtractionError, get_extractor
from futsal.knowledge.fetch import FetchError, fetch_document
from futsal.knowledge.profiles import PROFILES
from futsal.knowledge.quality import assess
from futsal.knowledge.structure import build_structure

KNOWLEDGE_DIR = Path("data/knowledge")
COMMANDS = ("knowledge-fetch", "knowledge-inspect", "knowledge-ingest", "knowledge-search",
            "knowledge-status")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    f = sub.add_parser("knowledge-fetch", help="descarga un documento público y registra su procedencia")
    f.add_argument("--url", required=True)
    f.add_argument("--doc-id", required=True, help="identificador estable (carpeta bajo data/knowledge)")
    f.add_argument("--output", type=Path, default=KNOWLEDGE_DIR)
    f.add_argument("--refresh", action="store_true")
    g = sub.add_parser("knowledge-ingest", help="indexa un documento en PostgreSQL (idempotente)")
    g.add_argument("--source", choices=sorted(KNOWLEDGE_SOURCES), help="documento del manifiesto")
    g.add_argument("--file", type=Path, help="archivo propio (requiere --doc-id y --title)")
    g.add_argument("--doc-id")
    g.add_argument("--title")
    g.add_argument("--doc-type", default="reglamento")
    g.add_argument("--language", default="es")
    g.add_argument("--profile", choices=sorted(PROFILES), default="plain")
    g.add_argument("--url")
    g.add_argument("--issuer")
    g.add_argument("--version-label")
    g.add_argument("--season")
    g.add_argument("--publication-date", type=date.fromisoformat)
    g.add_argument("--effective-date", type=date.fromisoformat)
    g.add_argument("--target-chars", type=int, default=ChunkConfig.target_chars)
    g.add_argument("--max-chars", type=int, default=ChunkConfig.max_chars)
    g.add_argument("--min-chars", type=int, default=ChunkConfig.min_chars)
    g.add_argument("--overlap-chars", type=int, default=ChunkConfig.overlap_chars)
    g.add_argument("--storage", type=Path, default=KNOWLEDGE_DIR)
    g.add_argument("--dry-run", action="store_true", help="extrae y fragmenta sin tocar la BD")
    g.add_argument("--force", action="store_true", help="reindexa aunque el hash no haya cambiado")
    q = sub.add_parser("knowledge-search", help="consulta la base documental (solo lectura)")
    q.add_argument("query")
    q.add_argument("--limit", type=int, default=5)
    q.add_argument("--doc-id")
    q.add_argument("--version-label")
    q.add_argument("--rule", type=int)
    q.add_argument("--history", action="store_true", help="incluye versiones anteriores")
    sub.add_parser("knowledge-status", help="documentos, versiones y últimas ejecuciones")
    i = sub.add_parser("knowledge-inspect", help="extrae un documento y muestra la calidad (sin BD)")
    i.add_argument("--file", type=Path, required=True)
    i.add_argument("--profile", choices=sorted(PROFILES), default="plain")
    i.add_argument("--json", type=Path, default=None, help="guarda el informe completo en JSON")
    i.add_argument("--page", type=int, default=None, help="muestra los segmentos de una página")


def _spec(args: argparse.Namespace) -> tuple[DocumentSpec, Path] | str:
    if args.source:
        src = KNOWLEDGE_SOURCES[args.source]
        return src.spec, args.storage / src.spec.doc_key / src.file_name
    if not (args.file and args.doc_id and args.title):
        return "indica --source, o bien --file con --doc-id y --title"
    return DocumentSpec(
        doc_key=args.doc_id, title=args.title, doc_type=args.doc_type, language=args.language,
        structure_profile=args.profile, source_url=args.url, issuer=args.issuer,
        publication_date=args.publication_date, effective_date=args.effective_date,
        version_label=args.version_label, season_label=args.season), args.file


def _db(args: argparse.Namespace) -> int:
    try:
        engine = make_engine()
        if args.command == "knowledge-ingest":
            parsed = _spec(args)
            if isinstance(parsed, str):
                print(f"Error: {parsed}")
                return 2
            spec, path = parsed
            cfg = ChunkConfig(args.target_chars, args.max_chars, args.min_chars, args.overlap_chars)
            if args.dry_run:
                doc = get_extractor(path).extract(path)
                st = build_structure(doc, PROFILES[spec.structure_profile])
                chunks = chunk_segments(st.segments, cfg)
                print(f"[dry-run] {spec.doc_key}: {len(doc.pages)} páginas, {len(chunks)} fragmentos")
                return 0
            res = ingest_document(engine, spec, path, chunk_config=cfg, storage=args.storage,
                                  force=args.force)
            print(f"{res.doc_key}: {res.outcome} versión={res.version_id} fragmentos={res.chunks} "
                  f"páginas={res.pages}")
            for w in res.warnings:
                print(f"  aviso: {w}")
            if res.error:
                print(f"  error: {res.error}")
            return 1 if res.outcome == "failed" else 0
        if args.command == "knowledge-search":
            found = KnowledgeSearch(engine).search(
                args.query, limit=args.limit, doc_key=args.doc_id,
                version_label=args.version_label, rule=args.rule, include_history=args.history)
            print(f"método={found.method} resultados={len(found.hits)}")
            for h in found.hits:
                ref = f"Regla {h.rule_number} §{h.section_number}" if h.rule_number else h.part
                print(f"[{h.score}] {h.document_title} ({h.version_label}) {ref} "
                      f"{h.section_title or ''} p.{h.page_start}-{h.page_end}")
                print("   " + h.text[:200].replace("\n", " "))
            return 0
        with engine.connect() as conn:
            for r in conn.execute(text(
                    "SELECT d.doc_key, v.id, v.is_current, v.index_status, v.extraction_status, "
                    "v.page_count, (SELECT count(*) FROM kb_chunks c WHERE c.version_id = v.id) "
                    "FROM kb_documents d JOIN kb_document_versions v ON v.document_id = d.id "
                    "ORDER BY d.doc_key, v.id")):
                print(f"{r[0]} v{r[1]} {'ACTUAL' if r[2] else 'anterior'} índice={r[3]} "
                      f"extracción={r[4]} páginas={r[5]} fragmentos={r[6]}")
            for r in conn.execute(text(
                    "SELECT id, doc_key, outcome, started_at, error FROM kb_ingestion_runs "
                    "ORDER BY id DESC LIMIT 5")):
                print(f"ejecución {r[0]} {r[1]} {r[2]} {r[3]:%Y-%m-%d %H:%M}"
                      f"{' error=' + str(r[4]) if r[4] else ''}")
        return 0
    except SearchError as exc:
        print(f"Error: {exc}")
        return 2
    except (SQLAlchemyError, RuntimeError, ExtractionError, FileNotFoundError) as exc:
        print(f"Error: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return 2


def run(args: argparse.Namespace) -> int:
    if args.command in ("knowledge-ingest", "knowledge-search", "knowledge-status"):
        return _db(args)
    if args.command == "knowledge-fetch":
        try:
            info = fetch_document(args.url, args.output / args.doc_id, refresh=args.refresh)
        except FetchError as exc:
            print(f"Error: {exc}")
            return 2
        print(f"{info['file']} sha256={str(info['sha256'])[:12]}… bytes={info['bytes']}"
              f"{' (reutilizado)' if info.get('reused') else ''}")
        return 0
    profile = PROFILES[args.profile]
    try:
        doc = get_extractor(args.file).extract(args.file)
    except (ExtractionError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 2
    structured = build_structure(doc, profile)
    report = assess(doc, structured, profile)
    print(report.render())
    if args.page:
        for seg in structured.segments:
            if seg.page_start <= args.page <= seg.page_end:
                print(f"--- {seg.part} §{seg.section_number} {seg.section_title} "
                      f"[{seg.subsection_title}] p{seg.page_start}-{seg.page_end}")
                print(seg.text[:600])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return 0
