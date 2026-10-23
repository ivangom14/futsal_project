"""El conjunto de evaluación debe estar respaldado por el PDF original, no por memoria."""
import re
import unicodedata
from pathlib import Path

import pytest

from futsal.knowledge.evaluate import load_questions
from futsal.knowledge.extract import PdfExtractor

FIXTURE = Path("tests/fixtures/knowledge_eval_fifa.json")
PDF = Path("data/knowledge/fifa-futsal-laws-2025-26/original.pdf")


def _n(t: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", t)).lower()


def test_fixture_shape() -> None:
    qs = load_questions(FIXTURE)
    assert len({q["id"] for q in qs}) == len(qs)
    cats = {q["category"] for q in qs}
    assert {"literal", "colloquial", "multi_chunk", "exception", "distinguish",
            "unanswerable", "out_of_scope"} <= cats
    for q in qs:
        assert (q["expect"] == "answerable") == bool(q["evidence"])
        for ev in q["evidence"]:
            assert ev["pages"] and ev["contains"]


@pytest.mark.skipif(not PDF.exists(), reason="PDF de FIFA no descargado")
def test_every_expected_evidence_is_in_the_pdf_pages() -> None:
    pages = {p.number: _n(p.text) for p in PdfExtractor().extract(PDF).pages}
    for q in load_questions(FIXTURE):
        for ev in q["evidence"]:
            assert any(_n(ev["contains"]) in pages[p] for p in ev["pages"]), q["id"]


C27_FIXTURE = Path("tests/fixtures/knowledge_eval_circular27.json")
C27_PDF = Path("data/knowledge/rfef-circular-27-2025-26/original.pdf")
C27_KEY, FIFA_KEY = "rfef-circular-27-2025-26", "fifa-futsal-laws-2025-26"


def test_circular27_fixture_shape() -> None:
    qs = load_questions(C27_FIXTURE)
    assert len({q["id"] for q in qs}) == len(qs)
    assert {q["expect"] for q in qs} == {"answerable", "no_evidence"}
    for q in qs:
        assert q["expected_answer"]                                    # la respuesta esperada queda documentada
        assert (q["expect"] == "answerable") == bool(q["evidence"])
        for ev in q["evidence"]:
            assert ev["pages"] and ev["contains"] and ev["doc_key"] in (C27_KEY, FIFA_KEY)


@pytest.mark.skipif(not (C27_PDF.exists() and PDF.exists()), reason="PDF de la Circular 27 o de FIFA no descargado")
def test_circular27_evidence_is_in_the_original_pages() -> None:
    sources = {C27_KEY: C27_PDF, FIFA_KEY: PDF}
    pages = {k: {p.number: _n(p.text) for p in PdfExtractor().extract(v).pages} for k, v in sources.items()}
    for q in load_questions(C27_FIXTURE):
        for ev in q["evidence"]:
            assert any(_n(ev["contains"]) in pages[ev["doc_key"]][p] for p in ev["pages"]), (q["id"], ev["doc_key"])


@pytest.mark.skipif(not (C27_PDF.exists() and PDF.exists()), reason="PDF de la Circular 27 o de FIFA no descargado")
def test_circular27_annex_is_page_by_page_identical_to_the_fifa_document() -> None:
    """Justifica no indexar las p. 24-220: su texto coincide con p. 1-197 del PDF de FIFA."""
    fifa = {p.number: p.text for p in PdfExtractor().extract(PDF).pages}
    c27 = {p.number: p.text for p in PdfExtractor().extract(C27_PDF).pages}
    assert len(fifa) == 197 and len(c27) == 220
    assert all(fifa[k] == c27[k + 23] for k in fifa)
