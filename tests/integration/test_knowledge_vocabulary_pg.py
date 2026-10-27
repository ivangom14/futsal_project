"""Fase 20: vocabulario coloquial, circular citada y tipo de documento en la recuperación. Corpus sintético: un fragmento por página con
extractos literales de los documentos reales, insertado directamente en las tablas (el fragmentado de PDF ya está probado aparte).
PostgreSQL temporal, sin LLM. Cada expansión se justifica comparando la misma consulta con y sin ella."""
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.knowledge import vocabulary
from futsal.knowledge.search import AND_BONUS, KnowledgeSearch, _expand, _lexemes

pytestmark = pytest.mark.integration
FIFA = ("fifa-futsal-laws-2025-26", "Reglas de Juego del Futsal 2025/26", "reglamento")
C10 = ("rfef-circular-10-rectificativa-2026-27", "Circular n.º 10 (rectificativa) de la CTAFS de la RFEF", "circular")
FIFA_KEY, C10_KEY = FIFA[0], C10[0]
FIFA_PAGES = [  # (página, regla, texto)
    (116, 16, "El guardameta del equipo defensor lanzará o dejará caer el balón con las manos desde dentro del área. Se concederá saque de meta "
              "cuando el balón haya atravesado completamente la línea de fondo, por el suelo o por el aire."),
    (12, None, "Cuando las reglas nacionales del futsal juvenil, de veteranos, de personas con discapacidad y futsal base prohíban que el "
               "guardameta lance el balón más allá de la línea central, se concederá un libre directo al equipo adversario, que se lanzará "
               "desde el lugar donde el balón cruzó la línea central."),
    (99, 13, "FALTAS ACUMULADAS Son aquellas sancionadas con un libre directo según lo estipulado en la Regla 3, la Regla 4 y la Regla 12. "
             "En el acta del partido se registrarán las faltas acumuladas de los dos equipos en cada uno de los periodos."),
    (111, 14, "El penalti se lanzará desde la marca del penalti. El segundo punto de penalti se marca a diez metros. Se concederá un penalti "
              "cuando un jugador cometa una falta sancionable con un libre directo dentro de su propia área."),
]
C10_PAGES = [
    (1, None, "Restricción en saque de meta. En la ejecución del saque de meta, el/la guardameta, no podrá pasar con el balón directamente la "
              "línea de medio campo sin que, previamente, haya sido tocado por otro/a jugador/a. Se concederá un tiro libre indirecto al "
              "equipo adversario."),
    (2, None, "Faltas sancionables con penal no acumulables. Todas las infracciones sancionadas con tiro libre directo cuentan como falta "
              "acumulable; sin embargo, cuando se conceda un tiro penal, la falta no se registrará como acumulada."),
]


@pytest.fixture()
def kb(pg_engine: Engine) -> Iterator[Engine]:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
        for (key, title, kind), pages in ((FIFA, FIFA_PAGES), (C10, C10_PAGES)):
            doc = c.execute(text("INSERT INTO kb_documents (doc_key, title, doc_type, language, structure_profile) "
                                 "VALUES (:k, :t, :d, 'es', 'plain') RETURNING id"), {"k": key, "t": title, "d": kind}).scalar()
            ver = c.execute(text("INSERT INTO kb_document_versions (document_id, content_hash, file_path, file_bytes, extraction_status,"
                                 " index_status, is_current) VALUES (:d, :h, 'x.pdf', 1, 'ok', 'indexed', true) RETURNING id"),
                            {"d": doc, "h": key}).scalar()
            for i, (page, rule, body) in enumerate(pages):
                c.execute(text("INSERT INTO kb_chunks (version_id, ordinal, rule_number, page_start, page_end, text, char_count) "
                               "VALUES (:v, :o, :r, :p, :p, :t, :n)"),
                          {"v": ver, "o": i, "r": rule, "p": page, "t": body, "n": len(body)})
    yield pg_engine


def _docs(kb: Engine, query: str, limit: int = 5) -> list[tuple[str, int]]:
    return [("circular" if h.doc_key == C10_KEY else "fifa", h.page_start) for h in KnowledgeSearch(kb).search(query, limit=limit).hits]


def _score_of(kb: Engine, query: str, doc_key: str, page: int) -> float | None:
    hits = KnowledgeSearch(kb).search(query, limit=10).hits
    return next((h.score for h in hits if h.doc_key == doc_key and h.page_start == page), None)


def _without_expansions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vocabulary, "EXPANSIONS", ())


# --- una consulta sin disparador no cambia ----------------------------------------------------------------------------------------
def test_a_query_without_trigger_words_is_not_altered(kb: Engine) -> None:
    with kb.connect() as conn:
        lexemes = _lexemes(conn, "tiempo muerto duracion de cada mitad")
        assert _expand(conn, "tiempo muerto duracion de cada mitad", lexemes) == (lexemes, lexemes)


def test_a_trigger_adds_alternatives_without_removing_the_original_terms(kb: Engine) -> None:
    with kb.connect() as conn:
        lexemes = _lexemes(conn, "portero saque de meta")
        or_terms, and_groups = _expand(conn, "portero saque de meta", lexemes)
    assert set(lexemes) <= set(or_terms) and "guardamet" in or_terms
    assert any(g.startswith("(") and "guardamet" in g for g in and_groups) and len(and_groups) == len(lexemes)


# --- por qué mejora cada expansión: el fragmento específico sube cuando se activa -----------------------------------------------
def test_portero_finds_the_guardameta_provision_of_the_circular(kb: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "portero saque de meta medio campo"
    with_rule = _score_of(kb, query, C10_KEY, 1)
    _without_expansions(monkeypatch)
    without = _score_of(kb, query, C10_KEY, 1)
    # la circular solo dice «guardameta»: con la regla cumple «todos los términos» (bonus), sin ella no
    assert with_rule is not None and without is not None and with_rule >= AND_BONUS > without


def test_penalti_finds_the_tiro_penal_provision_of_the_circular(kb: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "falta penalti acumulada"
    with_rule = _score_of(kb, query, C10_KEY, 2)
    _without_expansions(monkeypatch)
    without = _score_of(kb, query, C10_KEY, 2)
    # el stemmer separa «penalt» de «penal»: sin la regla la circular nunca contiene «penalti»
    assert with_rule is not None and without is not None and with_rule >= AND_BONUS > without


def test_medio_campo_finds_the_linea_central_provision_of_the_fifa_rules(kb: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "guardameta lanza el balón más allá de la línea de medio campo reglas nacionales"
    with_rule = _score_of(kb, query, FIFA_KEY, 12)
    _without_expansions(monkeypatch)
    without = _score_of(kb, query, FIFA_KEY, 12)
    assert with_rule is not None and without is not None and with_rule > without       # «línea central» de las Reglas FIFA suma


def test_the_reverse_rule_finds_the_linea_de_medio_campo_provision_when_the_question_says_linea_central(
        kb: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    query = "guardameta saque de meta línea central indirecto"
    with_rule = _score_of(kb, query, C10_KEY, 1)
    _without_expansions(monkeypatch)
    without = _score_of(kb, query, C10_KEY, 1)
    assert with_rule is not None and without is not None and with_rule > without


# --- circular citada ---------------------------------------------------------------------------------------------------------------
def test_a_cited_circular_goes_before_the_general_rules(kb: Engine) -> None:
    plain = _docs(kb, "saque de meta línea de fondo balón manos", limit=5)
    cited = KnowledgeSearch(kb).search("saque de meta línea de fondo balón manos según la circular 10", limit=5)
    assert plain[0][0] == "fifa" and (cited.hits[0].doc_key, cited.hits[0].page_start) == (C10_KEY, 1)
    assert cited.references == [{"circular": 10, "doc_key": C10_KEY}]
    assert any(h.doc_key == FIFA_KEY for h in cited.hits)        # la regla general sigue disponible tras la específica


def test_an_unindexed_circular_is_reported_and_no_hit_pretends_to_come_from_it(kb: Engine) -> None:
    client = TestClient(create_app(kb))
    body = client.get("/knowledge/search", params={"q": "que establecia la circular 9 de la RFEF sobre el saque de meta"}).json()
    assert body["cited_circulars"] == [{"circular": 9, "indexed": False}]
    assert "Circular 9" in body["note"] and "no está en la base" in body["note"]
    assert all("9" not in h["document"] for h in body["hits"])
    ok = client.get("/knowledge/search", params={"q": "circular n.º 10 saque de meta"}).json()
    assert ok["cited_circulars"] == [{"circular": 10, "indexed": True}] and "no está en la base" not in ok["note"]


def test_hits_say_whether_they_are_general_rules_or_a_circular(kb: Engine) -> None:
    body = TestClient(create_app(kb)).get("/knowledge/search", params={"q": "guardameta saque de meta linea de medio campo"}).json()
    kinds = {h["doc_key"]: h["type"] for h in body["hits"]}
    assert kinds == {FIFA_KEY: "reglamento", C10_KEY: "circular"}
    assert all(h["pages"] and h["document"] and h["doc_key"] for h in body["hits"])      # documento y página se conservan


# --- sin evidencia ----------------------------------------------------------------------------------------------------------------
def test_a_question_without_evidence_gets_no_fragment_that_answers_it(kb: Engine) -> None:
    body = TestClient(create_app(kb)).get("/knowledge/search", params={"q": "cuanto cobra un arbitro de futsal por partido"}).json()
    assert not any("cobra" in h["text"].lower() or "euros" in h["text"].lower() for h in body["hits"])
    assert "cited_circulars" not in body                       # sin circular citada no hay referencias ni avisos inventados
