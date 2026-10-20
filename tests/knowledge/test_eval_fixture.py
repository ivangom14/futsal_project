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
