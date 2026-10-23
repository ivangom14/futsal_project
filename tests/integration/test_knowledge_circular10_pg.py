"""Fase 15: Circular 10 rectificativa (CTAFS, RFEF 2026/27) como tercer documento, sin perjudicar a los anteriores. PostgreSQL temporal; sin LLM."""
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
from futsal.knowledge.evaluate import evaluate, load_questions, summarize
from futsal.knowledge.ingest import ingest_document
from futsal.knowledge.search import KnowledgeSearch

pytestmark = pytest.mark.integration
FIFA, C27, C10 = (Path(f"data/knowledge/{k}/original.pdf") for k in (
    "fifa-futsal-laws-2025-26", "rfef-circular-27-2025-26", "rfef-circular-10-rectificativa-2026-27"))
FIXTURES = {"fifa-futsal-laws-2025-26": "knowledge_eval_fifa.json", "rfef-circular-27-2025-26": "knowledge_eval_circular27.json",
            "rfef-circular-10-rectificativa-2026-27": "knowledge_eval_circular10.json"}


@pytest.fixture()
def kb(pg_engine: Engine) -> Iterator[Engine]:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
    yield pg_engine


def _metrics(kb: Engine, key: str) -> tuple[dict[str, object], dict[str, object]]:
    qs = load_questions(Path("tests/fixtures") / FIXTURES[key])
    return (summarize(evaluate(KnowledgeSearch(kb), qs)), summarize(evaluate(KnowledgeSearch(kb), qs, reformulated=True)))


@pytest.mark.skipif(not (FIFA.exists() and C27.exists() and C10.exists()), reason="faltan PDF descargados de FIFA, Circular 27 o Circular 10")
def test_real_circular10_is_added_without_harming_fifa_or_circular27(kb: Engine, tmp_path: Path) -> None:
    specs = {k: KNOWLEDGE_SOURCES[k].spec for k in FIXTURES}
    fifa = ingest_document(kb, specs["fifa-futsal-laws-2025-26"], FIFA, storage=tmp_path)
    c27 = ingest_document(kb, specs["rfef-circular-27-2025-26"], C27, storage=tmp_path)
    before = {k: _metrics(kb, k) for k in ("fifa-futsal-laws-2025-26", "rfef-circular-27-2025-26")}
    spec10 = specs["rfef-circular-10-rectificativa-2026-27"]
    c10 = ingest_document(kb, spec10, C10, storage=tmp_path)
    assert c10.outcome == "new" and c10.pages == 12 and 20 <= c10.chunks <= 40
    assert ingest_document(kb, spec10, C10, storage=tmp_path).outcome == "unchanged"
    with kb.connect() as c:
        rows = {r[0]: tuple(r[1:]) for r in c.execute(text(
            "SELECT d.doc_key, v.id, v.is_current, (SELECT count(*) FROM kb_chunks k WHERE k.version_id = v.id) "
            "FROM kb_document_versions v JOIN kb_documents d ON d.id = v.document_id"))}
        last_page = c.execute(text("SELECT max(page_end) FROM kb_chunks k JOIN kb_document_versions v ON v.id = k.version_id "
                                   "JOIN kb_documents d ON d.id = v.document_id WHERE d.doc_key = :k"), {"k": spec10.doc_key}).scalar()
    assert rows["fifa-futsal-laws-2025-26"] == (fifa.version_id, True, fifa.chunks)               # sin cambios en los anteriores
    assert rows["rfef-circular-27-2025-26"] == (c27.version_id, True, c27.chunks) and last_page == 12
    for key, (lit_before, ref_before) in before.items():                                          # recuperación @5 no empeora
        lit, ref = _metrics(kb, key)
        assert lit["text@5"] >= lit_before["text@5"] and ref["text@5"] >= ref_before["text@5"], key  # type: ignore[operator]
        assert lit["page@5"] >= lit_before["page@5"] and ref["page@5"] >= ref_before["page@5"], key  # type: ignore[operator]
    lit10, ref10 = _metrics(kb, "rfef-circular-10-rectificativa-2026-27")                          # suelo medido: literal 4/8, reformulada 8/8
    assert lit10["text@5"] >= 0.5 and ref10["text@5"] == 1.0
    hit = TestClient(create_app(kb)).get("/knowledge/search", params={
        "q": "saque de meta guardameta línea de medio campo tiro libre indirecto", "limit": 1}).json()["hits"][0]
    assert hit["doc_key"] == spec10.doc_key and hit["pages"] == "1" and hit["part"].startswith("I. MODIFICACIONES")
    assert hit["section"].startswith("1 Restricci")
