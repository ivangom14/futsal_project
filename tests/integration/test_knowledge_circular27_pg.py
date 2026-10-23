"""Fase 15: Circular 27 de la RFEF como documento independiente junto al reglamento FIFA. PostgreSQL temporal; sin LLM."""
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
from futsal.knowledge.evaluate import evaluate, load_questions, summarize
from futsal.knowledge.ingest import DocumentSpec, ingest_document
from futsal.knowledge.models import PageSection
from futsal.knowledge.search import KnowledgeSearch

pytestmark = pytest.mark.integration
PdfMaker = Callable[[list[str]], bytes]
LAW = "El guardameta no puede tocar el balon con la mano tras un pase intencionado de un companero de equipo"
BASE = DocumentSpec(doc_key="reglas", title="Reglas de prueba", doc_type="reglamento", language="es", version_label="v1")
COMPOSITE = DocumentSpec(
    doc_key="circular", title="Circular de prueba", doc_type="circular", language="es", issuer="RFEF",
    structure_profile="rfef-circular", version_label="C-1",
    page_sections=(PageSection(1, 1, "Comunicación de la RFEF"), PageSection(2, 2, "Resumen de cambios"),
                   PageSection(3, 4, "Reglas completas (anexo)", skip_reason="idéntico a reglas")))
PAGES = ["La RFEF comunica a sus afiliados el contenido de la circular de la federacion internacional",
         "Regla 12. Faltas y conducta incorrecta\nSe anade una excepcion para el cambio de portero tras una lesion", LAW,
         "Una expulsion implica que el jugador abandona el terreno de juego y no puede ser sustituido"]


@pytest.fixture()
def kb(pg_engine: Engine) -> Iterator[Engine]:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
    yield pg_engine


def _both(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> tuple[Path, Path]:
    base, comp = tmp_path / "base.pdf", tmp_path / "comp.pdf"
    base.write_bytes(make_pdf(PAGES[2:]))
    comp.write_bytes(make_pdf(PAGES))
    ingest_document(kb, BASE, base, storage=tmp_path / "store")
    return base, comp


def test_skipped_annex_is_not_indexed_and_does_not_duplicate_results(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    _, comp = _both(kb, tmp_path, make_pdf)
    res = ingest_document(kb, COMPOSITE, comp, storage=tmp_path / "store")
    assert res.outcome == "new" and res.pages == 4 and any("3-4 no indexadas" in w for w in res.warnings)
    with kb.connect() as c:
        pages = c.execute(text("SELECT page_start, page_end, part FROM kb_chunks c JOIN kb_document_versions v ON v.id = c.version_id "
                               "JOIN kb_documents d ON d.id = v.document_id WHERE d.doc_key = 'circular' ORDER BY c.id")).all()
        quality = c.execute(text("SELECT quality->'page_sections' FROM kb_document_versions WHERE content_hash IS NOT NULL "
                                 "AND document_id = (SELECT id FROM kb_documents WHERE doc_key = 'circular')")).scalar()
    assert {p[2] for p in pages} == {"Comunicación de la RFEF", "Resumen de cambios"} and max(p[1] for p in pages) == 2
    assert quality[2]["skip_reason"] == "idéntico a reglas"
    docs = [h.doc_key for h in KnowledgeSearch(kb).search("guardameta pase intencionado mano", limit=5).hits]
    assert docs == ["reglas"]                                              # el anexo duplicado no compite con el original


def test_ingest_is_idempotent_and_does_not_touch_the_existing_document(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    _, comp = _both(kb, tmp_path, make_pdf)

    def snapshot() -> tuple[object, ...]:
        with kb.connect() as c:
            return tuple(c.execute(text("SELECT v.id, v.content_hash, v.is_current, (SELECT count(*) FROM kb_chunks k WHERE k.version_id = v.id) "
                                        "FROM kb_document_versions v JOIN kb_documents d ON d.id = v.document_id WHERE d.doc_key = 'reglas'")).one())
    before = snapshot()
    first = ingest_document(kb, COMPOSITE, comp, storage=tmp_path / "store")
    again = ingest_document(kb, COMPOSITE, comp, storage=tmp_path / "store")
    assert (first.outcome, again.outcome) == ("new", "unchanged") and again.version_id == first.version_id
    assert snapshot() == before
    with kb.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_documents")).scalar() == 2
        assert c.execute(text("SELECT count(*) FROM kb_document_versions")).scalar() == 2


def test_invalid_sections_fail_the_run_without_creating_the_document(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    comp = tmp_path / "c.pdf"
    comp.write_bytes(make_pdf(PAGES))
    bad = DocumentSpec(doc_key="mala", title="Mala", doc_type="circular", language="es", structure_profile="rfef-circular",
                       page_sections=(PageSection(1, 9, "fuera"),))
    res = ingest_document(kb, bad, comp, storage=tmp_path / "store")
    assert res.outcome == "failed" and "fuera del documento" in (res.error or "")
    with kb.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_documents WHERE doc_key = 'mala'")).scalar() == 0


def test_api_exposes_part_and_document_for_circular_hits_only(kb: Engine, tmp_path: Path, make_pdf: PdfMaker) -> None:
    _, comp = _both(kb, tmp_path, make_pdf)
    ingest_document(kb, COMPOSITE, comp, storage=tmp_path / "store")
    client = TestClient(create_app(kb))
    hits = client.get("/knowledge/search", params={"q": "RFEF comunica afiliados circular", "limit": 3}).json()["hits"]
    top = hits[0]
    assert top["doc_key"] == "circular" and top["document"] == "Circular de prueba" and top["part"] == "Comunicación de la RFEF"
    rule = client.get("/knowledge/search", params={"q": "excepcion cambio de portero lesion", "limit": 1}).json()["hits"][0]
    assert rule["doc_key"] == "circular" and rule["rule"] == 12 and rule["part"] == "Resumen de cambios"
    base = client.get("/knowledge/search", params={"q": "guardameta pase intencionado", "limit": 1}).json()["hits"][0]
    assert base["doc_key"] == "reglas"
    note = client.get("/knowledge/search", params={"q": "RFEF"}).json()["note"]
    assert "RFFM" in note and "circulares de la RFEF" in note and "sin decidir cuál prevalece" in note  # alcance de la evidencia


C27 = Path("data/knowledge/rfef-circular-27-2025-26/original.pdf")
FIFA = Path("data/knowledge/fifa-futsal-laws-2025-26/original.pdf")


@pytest.mark.skipif(not (C27.exists() and FIFA.exists()), reason="PDF de la Circular 27 o de FIFA no descargado")
def test_real_circular27_is_added_without_harming_the_fifa_retrieval(kb: Engine, tmp_path: Path) -> None:
    fifa_spec = KNOWLEDGE_SOURCES["fifa-futsal-laws-2025-26"].spec
    c27_spec = KNOWLEDGE_SOURCES["rfef-circular-27-2025-26"].spec
    fifa = ingest_document(kb, fifa_spec, FIFA, storage=tmp_path)
    fifa_questions = load_questions(Path("tests/fixtures/knowledge_eval_fifa.json"))
    alone = (summarize(evaluate(KnowledgeSearch(kb), fifa_questions)), summarize(evaluate(KnowledgeSearch(kb), fifa_questions, reformulated=True)))
    c27 = ingest_document(kb, c27_spec, C27, storage=tmp_path)
    assert c27.outcome == "new" and c27.pages == 220 and 35 <= c27.chunks <= 60
    assert ingest_document(kb, c27_spec, C27, storage=tmp_path).outcome == "unchanged"
    with kb.connect() as c:
        last_page = c.execute(text("SELECT max(page_end) FROM kb_chunks k JOIN kb_document_versions v ON v.id = k.version_id "
                                   "JOIN kb_documents d ON d.id = v.document_id WHERE d.doc_key = :k"), {"k": c27_spec.doc_key}).scalar()
        fifa_row = c.execute(text("SELECT v.id, v.is_current, (SELECT count(*) FROM kb_chunks k WHERE k.version_id = v.id) "
                                  "FROM kb_document_versions v JOIN kb_documents d ON d.id = v.document_id WHERE d.doc_key = :k"),
                             {"k": fifa_spec.doc_key}).one()
    assert last_page == 23                                                  # solo comunicación, circular FIFA y resumen de cambios
    assert tuple(fifa_row) == (fifa.version_id, True, fifa.chunks)          # el reglamento FIFA no cambia
    # No empeora la recuperación del reglamento FIFA: mismas métricas @5 que con el documento solo (MRR puede variar por citas legítimas).
    both = (summarize(evaluate(KnowledgeSearch(kb), fifa_questions)), summarize(evaluate(KnowledgeSearch(kb), fifa_questions, reformulated=True)))
    for before, after in zip(alone, both, strict=True):
        assert after["text@5"] >= before["text@5"] and after["page@5"] >= before["page@5"]
    # Circular 27: suelo de regresión medido (literal 4/6, reformulada 6/6).
    c27_questions = load_questions(Path("tests/fixtures/knowledge_eval_circular27.json"))
    literal = summarize(evaluate(KnowledgeSearch(kb), c27_questions))
    reform = summarize(evaluate(KnowledgeSearch(kb), c27_questions, reformulated=True))
    assert literal["text@5"] >= 0.66 and reform["text@5"] == 1.0
    # El reglamento FIFA sigue siendo la fuente de las preguntas generales.
    top = KnowledgeSearch(kb).search("saque de esquina cuadrante", limit=3).hits
    assert any(h.doc_key == fifa_spec.doc_key and h.rule_number == 17 for h in top)
