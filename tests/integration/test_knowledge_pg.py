"""Ingesta y búsqueda documental contra PostgreSQL temporal (se omite sin servidor)."""
from collections.abc import Callable
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from futsal.knowledge import ingest as ingest_mod
from futsal.knowledge.ingest import DocumentSpec, ingest_document
from futsal.knowledge.models import ExtractedDocument, PageText
from futsal.knowledge.profiles import PROFILES
from futsal.knowledge.search import KnowledgeSearch, SearchError

SPEC = DocumentSpec(doc_key="doc-a", title="Reglamento de prueba", doc_type="reglamento",
                    language="es", effective_date=date(2025, 9, 5), version_label="v1")

PdfMaker = Callable[[list[str]], bytes]


@pytest.fixture()
def kb(pg_engine: Engine) -> Iterator[Engine]:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, "
                       "kb_ingestion_runs RESTART IDENTITY CASCADE"))
    yield pg_engine


def _pdf(tmp_path: Path, make_pdf: PdfMaker, name: str, pages: list[str]) -> Path:
    path = tmp_path / name
    path.write_bytes(make_pdf(pages))
    return path


def _counts(engine: Engine) -> tuple[int, int, int, int]:
    with engine.connect() as c:
        return tuple(c.execute(text(  # type: ignore[return-value]
            "SELECT (SELECT count(*) FROM kb_documents), (SELECT count(*) FROM kb_document_versions),"
            " (SELECT count(*) FROM kb_chunks), (SELECT count(*) FROM kb_ingestion_runs)")).one())


def test_ingest_is_idempotent_and_records_runs(kb: Engine, tmp_path: Path,
                                               make_pdf: PdfMaker) -> None:
    f = _pdf(tmp_path, make_pdf, "a.pdf", ["El guardameta no puede tocar el balon con la mano",
                                            "Un tiro penal se ejecuta desde la marca"])
    storage = tmp_path / "store"
    r1 = ingest_document(kb, SPEC, f, storage=storage)
    assert (r1.outcome, r1.pages) == ("new", 2) and r1.chunks >= 1
    before = _counts(kb)
    r2 = ingest_document(kb, SPEC, f, storage=storage)
    assert r2.outcome == "unchanged" and r2.version_id == r1.version_id
    after = _counts(kb)
    assert after[:3] == before[:3] and after[3] == before[3] + 1  # solo una ejecución más
    with kb.connect() as c:
        row = c.execute(text("SELECT file_path, content_hash, is_current, extraction_status, "
                             "index_status FROM kb_document_versions")).one()
        eff = c.execute(text("SELECT effective_date, publication_date FROM kb_documents")).one()
    assert Path(row[0]).is_file() and row[1][:16] in row[0]
    assert row[2:] == (True, "ok", "indexed")
    assert eff == (date(2025, 9, 5), None)  # la fecha de publicación no se inventa


def test_new_version_keeps_history_and_search_uses_only_current(
        kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    storage = tmp_path / "store"
    v1 = _pdf(tmp_path, make_pdf, "v1.pdf", ["La barrera estara a cinco metros"])
    v2 = _pdf(tmp_path, make_pdf, "v2.pdf", ["La barrera estara a seis metros"])
    ingest_document(kb, SPEC, v1, storage=storage)
    r2 = ingest_document(kb, SPEC, v2, storage=storage)
    assert r2.outcome == "updated"
    s = KnowledgeSearch(kb)
    hits = s.search("barrera metros").hits
    assert len(hits) == 1 and "seis" in hits[0].text and hits[0].is_current
    hist = s.search("barrera metros", include_history=True).hits
    assert {("seis" in h.text, h.is_current) for h in hist} == {(True, True), (False, False)}
    with kb.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_document_versions WHERE is_current")
                         ).scalar() == 1
    r3 = ingest_document(kb, SPEC, v1, storage=storage)  # volver al contenido anterior
    assert r3.outcome == "restored" and "cinco" in s.search("barrera metros").hits[0].text
    assert _counts(kb)[:2] == (1, 2)


def test_failures_do_not_touch_current_version(kb: Engine, tmp_path: Path,
                                               make_pdf: PdfMaker) -> None:
    storage = tmp_path / "store"
    good = _pdf(tmp_path, make_pdf, "g.pdf", ["Texto vigente sobre el saque de banda"])
    ingest_document(kb, SPEC, good, storage=storage)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"no es un pdf")
    res = ingest_document(kb, SPEC, bad, storage=storage)
    missing = ingest_document(kb, SPEC, tmp_path / "nada.pdf", storage=storage)
    assert res.outcome == missing.outcome == "failed" and res.error and missing.error
    assert KnowledgeSearch(kb).search("saque banda").hits
    with kb.connect() as c:
        outcomes = [r[0] for r in c.execute(text("SELECT outcome FROM kb_ingestion_runs ORDER BY id"))]
    assert outcomes == ["new", "failed", "failed"]


def test_partial_extraction_is_recorded(kb: Engine, tmp_path: Path, make_pdf: PdfMaker,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    class Partial:
        suffixes = (".pdf",)

        def extract(self, path: Path) -> ExtractedDocument:
            return ExtractedDocument([PageText(1, "Texto util sobre sustituciones ilimitadas")],
                                     warnings=["página 2: error de extracción (ValueError)"])

    monkeypatch.setattr(ingest_mod, "get_extractor", lambda p: Partial())
    res = ingest_document(kb, SPEC, _pdf(tmp_path, make_pdf, "p.pdf", ["x"]), storage=tmp_path / "s")
    assert res.outcome == "new" and res.warnings
    with kb.connect() as c:
        assert c.execute(text("SELECT extraction_status FROM kb_document_versions")).scalar() == "partial"
        assert c.execute(text("SELECT warnings->>0 FROM kb_ingestion_runs")).scalar()


def test_reindex_after_failed_indexing_replaces_chunks(kb: Engine, tmp_path: Path,
                                                       make_pdf: PdfMaker) -> None:
    f = _pdf(tmp_path, make_pdf, "a.pdf", ["Contenido sobre tiempos muertos"])
    r1 = ingest_document(kb, SPEC, f, storage=tmp_path / "s")
    with kb.begin() as c:
        c.execute(text("UPDATE kb_document_versions SET index_status='failed'"))
    r2 = ingest_document(kb, SPEC, f, storage=tmp_path / "s")
    assert r2.version_id == r1.version_id and _counts(kb)[2] == r1.chunks
    r3 = ingest_document(kb, SPEC, f, storage=tmp_path / "s", force=True)
    assert r3.outcome == "reindexed" and _counts(kb)[:3] == (1, 1, r1.chunks)


def test_search_ranking_filters_validation_and_typos(kb: Engine, tmp_path: Path,
                                                     make_pdf: PdfMaker) -> None:
    other = DocumentSpec(doc_key="doc-b", title="Otro documento", doc_type="circular",
                         language="es", version_label="v9")
    ingest_document(kb, SPEC, _pdf(tmp_path, make_pdf, "a.pdf", [
        "El GUARDAMETA no puede tocar el balón con la mano fuera de su área",
        "El tiro penal se ejecuta desde la marca de diez metros"]), storage=tmp_path / "s")
    ingest_document(kb, other, _pdf(tmp_path, make_pdf, "b.pdf", [
        "Circular sobre horarios de partidos de guardameta juvenil"]), storage=tmp_path / "s")
    s = KnowledgeSearch(kb)
    r = s.search("guardameta mano")  # sin acentos ni mayúsculas
    assert r.method == "fts" and "mano" in r.hits[0].text and r.hits[0].doc_key == "doc-a"
    assert {h.doc_key for h in s.search("guardameta").hits} == {"doc-a", "doc-b"}
    assert {h.doc_key for h in s.search("guardameta", doc_key="doc-b").hits} == {"doc-b"}
    assert s.search("guardameta", version_label="v1").hits[0].doc_key == "doc-a"
    assert s.search("guardameta", language="en").hits == []
    typo = s.search("guardamta")  # errata: respaldo por trigramas
    assert typo.method == "trigram" and typo.hits
    assert s.search("zzzqqq inexistente").hits == [] and s.search("zzzqqq").method == "none"
    h = s.search("guardameta", limit=1).hits
    assert len(h) == 1 and h[0].version_label in ("v1", "v9") and (h[0].page_start, h[0].page_end) == (1, 2)
    for bad in ("", " ", "x", "a" * 501):
        with pytest.raises(SearchError):
            s.search(bad)
    for lim in (0, 11):
        with pytest.raises(SearchError):
            s.search("guardameta", limit=lim)
    assert s.search("guardameta'); DROP TABLE kb_chunks;--").method in ("fts", "trigram", "none")
    assert _counts(kb)[2] == 2  # la tabla sigue intacta


def test_hit_and_total_size_limits(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    long_text = "El árbitro sanciona la falta " + "con una descripción muy larga " * 40
    ingest_document(kb, SPEC, _pdf(tmp_path, make_pdf, "l.pdf", [long_text, long_text + "otra"]),
                    storage=tmp_path / "s")
    s = KnowledgeSearch(kb)
    hit = s.search("árbitro falta", max_hit_chars=200).hits[0]
    assert len(hit.text) <= 201 and hit.truncated
    capped = s.search("árbitro falta", max_hit_chars=500, max_total_chars=600).hits
    assert sum(len(x.text) for x in capped) <= 601


def test_rule_filter_and_citation_fields(kb: Engine, tmp_path: Path, make_pdf: PdfMaker,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.knowledge.structure import StructureProfile
    import re
    monkeypatch.setitem(PROFILES, "t", StructureProfile(
        rule_header=re.compile(r"Regla (\d+)\s+•\s+(.+)"), rule_divider=re.compile(r"Regla (\d+)")))
    pages = [PageText(1, "Regla 3\nLos jugadores"),
             PageText(2, "Regla 3   •   Los jugadores\nCada equipo tendrá cinco jugadores en pista."),
             PageText(3, "Regla 4\nEl equipamiento"),
             PageText(4, "Regla 4   •   El equipamiento\nLos jugadores usarán espinilleras.")]

    class Fake:
        suffixes = (".pdf",)

        def extract(self, path: Path) -> ExtractedDocument:
            return ExtractedDocument(pages)

    monkeypatch.setattr(ingest_mod, "get_extractor", lambda p: Fake())
    spec = DocumentSpec(doc_key="r", title="R", doc_type="reglamento", language="es",
                        structure_profile="t")
    ingest_document(kb, spec, _pdf(tmp_path, make_pdf, "x.pdf", ["x"]), storage=tmp_path / "s")
    s = KnowledgeSearch(kb)
    assert {h.rule_number for h in s.search("jugadores").hits} == {3, 4}
    only4 = s.search("jugadores", rule=4).hits
    assert [(h.rule_number, h.rule_title, h.page_start) for h in only4] == [(4, "El equipamiento", 4)]


REAL_PDF = Path("data/knowledge/fifa-futsal-laws-2025-26/original.pdf")
FIXTURE = Path("tests/fixtures/knowledge_eval_fifa.json")


@pytest.mark.skipif(not REAL_PDF.exists(), reason="PDF de FIFA no descargado")
def test_real_fifa_pdf_ingest_and_retrieval_floor(kb: Engine, tmp_path: Path) -> None:
    from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
    from futsal.knowledge.evaluate import evaluate, load_questions, summarize
    spec = KNOWLEDGE_SOURCES["fifa-futsal-laws-2025-26"].spec
    first = ingest_document(kb, spec, REAL_PDF, storage=tmp_path)
    assert first.outcome == "new" and first.pages == 197 and 200 <= first.chunks <= 400
    assert ingest_document(kb, spec, REAL_PDF, storage=tmp_path).outcome == "unchanged"
    assert _counts(kb)[:3] == (1, 1, first.chunks)
    with kb.connect() as c:
        rules = c.execute(text("SELECT count(DISTINCT rule_number) FROM kb_chunks")).scalar()
        eff = c.execute(text("SELECT effective_date FROM kb_documents")).scalar()
    assert rules == 17 and eff == date(2025, 9, 5)
    # Suelo de regresión (no objetivo de calidad): medido 0,60 / 0,88 en la Entrega 2.
    qs = load_questions(FIXTURE)
    literal = summarize(evaluate(KnowledgeSearch(kb), qs))
    reform = summarize(evaluate(KnowledgeSearch(kb), qs, reformulated=True))
    assert literal["text@5"] >= 0.5 and reform["text@5"] >= 0.8
    hits = KnowledgeSearch(kb).search("saque de esquina cuadrante", limit=3).hits
    assert any(h.rule_number == 17 and h.page_start <= 119 <= h.page_end for h in hits)
