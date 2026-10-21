"""Fase 15, Entrega 3 (opción A): API, herramienta MCP `search_rules` y agente sobre el reglamento. PostgreSQL temporal; sin LLM real."""

import asyncio
import json
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.api.app import create_app
from futsal.knowledge.ingest import DocumentSpec, ingest_document
from futsal.knowledge.search import KnowledgeSearch
from futsal.mcp_server.server import create_server

pytestmark = pytest.mark.integration
PdfMaker = Callable[[list[str]], bytes]
SPEC = DocumentSpec(doc_key="doc-a", title="Reglamento de prueba", doc_type="reglamento", language="es",
                    effective_date=date(2025, 9, 5), version_label="v1")
PAGES = ["El saque de esquina se ejecuta desde el cuadrante mas cercano al punto donde el balon salio",
         "El guardameta no puede tocar el balon con la mano tras un pase intencionado de un companero",
         "Una expulsion implica que el jugador abandona el terreno de juego y no puede ser sustituido de inmediato"]


@pytest.fixture()
def kb(pg_engine: Engine, tmp_path: Path, make_pdf: PdfMaker) -> Iterator[Engine]:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_pdf(PAGES))
    ingest_document(pg_engine, SPEC, pdf, storage=tmp_path / "store")
    yield pg_engine


def test_api_returns_compact_cited_fragments(kb: Engine) -> None:
    r = TestClient(create_app(kb)).get("/knowledge/search", params={"q": "saque de esquina", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "fts" and body["count"] == len(body["hits"]) >= 1 and "NO mide relevancia" in body["note"]
    top = body["hits"][0]
    assert "cuadrante" in top["text"] and top["pages"] == "1-3" and top["document"] == "Reglamento de prueba"
    assert top["doc_key"] == "doc-a" and top["version"] == "v1"
    assert all(v is not None for h in body["hits"] for v in h.values())                  # sin nulos: menos tokens
    assert all("is_current" not in h for h in body["hits"])


def test_api_filters_limits_and_reports_no_hits(kb: Engine) -> None:
    c = TestClient(create_app(kb))
    assert len(c.get("/knowledge/search", params={"q": "balon", "limit": 1}).json()["hits"]) == 1
    nothing = c.get("/knowledge/search", params={"q": "zzzzzz qqqqqq"}).json()
    assert nothing["count"] == 0 and nothing["hits"] == [] and "reformula" in nothing["note"]
    with_history = c.get("/knowledge/search", params={"q": "balon", "include_history": True}).json()["hits"][0]
    assert with_history["is_current"] is True


@pytest.mark.parametrize("params", [{"q": "x"}, {"q": "balon", "limit": 0}, {"q": "balon", "limit": 11},
                                    {"q": "balon", "rule": 0}, {"q": "a" * 501}])
def test_api_validates_parameters(kb: Engine, params: dict[str, Any]) -> None:
    assert TestClient(create_app(kb)).get("/knowledge/search", params=params).status_code in (400, 422)


def test_api_is_safe_against_sql_in_the_query_and_reports_unavailable_kb(kb: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    c = TestClient(create_app(kb))
    assert c.get("/knowledge/search", params={"q": "'; DROP TABLE kb_chunks; --"}).status_code == 200
    with kb.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kb_chunks")).scalar() >= 1             # la tabla sigue ahí

    def boom(*a: object, **k: object) -> None:
        raise OperationalError("select", {}, Exception("no existe kb_chunks"))

    monkeypatch.setattr(KnowledgeSearch, "search", boom)
    r = c.get("/knowledge/search", params={"q": "balon"})
    assert r.status_code == 503 and "no disponible" in r.json()["detail"] and "kb_chunks" not in r.json()["detail"]


def test_mcp_tool_end_to_end_goes_through_the_api(kb: Engine) -> None:
    server = create_server(TestClient(create_app(kb)))

    async def go() -> tuple[bool, str]:
        async with connect(server._mcp_server) as session:
            res = await session.call_tool("search_rules", {"query": "guardameta balon mano", "limit": 2})
            return res.isError, res.content[0].text  # type: ignore[union-attr]

    is_error, body = asyncio.run(go())
    data = json.loads(body)
    assert not is_error and data["count"] >= 1 and "guardameta" in data["hits"][0]["text"] and data["hits"][0]["pages"] == "1-3"


class _Rules:
    """LLM guionizado: reformula la pregunta coloquial con el vocabulario del reglamento y responde citando la página."""

    provider, model = "fake", "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_tool_result = ""

    def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            assert "search_rules" in {t["name"] for t in tools} and "search_rules" in system
            return LLMResponse("", [ToolCall("t1", "search_rules", {"query": "saque de esquina cuadrante"})])
        self.seen_tool_result = messages[-1].tool_results[0].content
        return LLMResponse("El saque de esquina se ejecuta desde el cuadrante (página 1 del reglamento).", [])


def test_agent_uses_search_rules_and_receives_a_compact_result(kb: Engine) -> None:
    server = create_server(TestClient(create_app(kb)))
    llm = _Rules()

    async def go() -> Any:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), 5, preload_context=False).run("¿Cómo se saca un córner?")

    res = asyncio.run(go())
    assert res.tool_calls == 1 and res.guardrail is None and "página 1" in res.answer
    sent = json.loads(llm.seen_tool_result)
    assert sent["hits"][0]["pages"] == "1-3" and "cuadrante" in sent["hits"][0]["text"] and "note" in sent
    assert "null" not in llm.seen_tool_result                                              # sin nulos hacia el modelo


REAL_PDF = Path("data/knowledge/fifa-futsal-laws-2025-26/original.pdf")
FIXTURE = Path("tests/fixtures/knowledge_eval_fifa.json")


@pytest.mark.skipif(not REAL_PDF.exists(), reason="PDF de FIFA no descargado")
def test_api_returns_exactly_what_the_retrieval_layer_returns_for_the_evaluation_questions(
        pg_engine: Engine, tmp_path: Path) -> None:
    from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
    from futsal.knowledge.evaluate import load_questions

    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
    ingest_document(pg_engine, KNOWLEDGE_SOURCES["fifa-futsal-laws-2025-26"].spec, REAL_PDF, storage=tmp_path)
    client, direct = TestClient(create_app(pg_engine)), KnowledgeSearch(pg_engine)
    checked = 0
    for q in load_questions(FIXTURE):
        for query in {q["question"], q.get("reformulated") or q["question"]}:
            api = client.get("/knowledge/search", params={"q": query, "limit": 5}).json()
            want = direct.search(query, limit=5)
            assert api["method"] == want.method and api["count"] == len(want.hits)
            got = [(h.get("rule"), h["pages"], h["text"][:60]) for h in api["hits"]]
            exp = [(h.rule_number, str(h.page_start) if h.page_start == h.page_end else f"{h.page_start}-{h.page_end}", h.text[:60])
                   for h in want.hits]
            assert got == exp, query                                                           # misma ordenación y contenido
            checked += 1
    assert checked >= 25
    size = len(json.dumps(client.get("/knowledge/search", params={"q": "saque de esquina", "limit": 5}).json(), ensure_ascii=False))
    assert size <= 7000                                                                         # respuesta acotada (≈ 2 k tokens)
