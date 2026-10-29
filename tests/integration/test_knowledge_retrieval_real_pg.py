"""Fase 20 sobre los PDF reales (se omite si no están descargados): recuperación, citas y datos sin evidencia de los casos de reglamento
del evaluador, medidos POR SEPARADO y sin LLM. «Recuperación» = el fragmento con la evidencia verificada está en los 5 primeros;
«citas» = documento y página de ese fragmento son los esperados. La respuesta final solo se puede medir con un LLM real (no se ejecuta)."""
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from futsal.agent.evaluate import EvalCase, Fact
from futsal.agent.rule_cases import rule_cases
from futsal.api.knowledge import payload
from futsal.config.knowledge_sources import KNOWLEDGE_SOURCES
from futsal.knowledge.evaluate import evaluate, load_questions, summarize
from futsal.knowledge.ingest import ingest_document
from futsal.knowledge.search import KnowledgeSearch

pytestmark = pytest.mark.integration
KEYS = ("fifa-futsal-laws-2025-26", "rfef-circular-27-2025-26", "rfef-circular-10-rectificativa-2026-27")
PDFS = {k: Path(f"data/knowledge/{k}/original.pdf") for k in KEYS}
FIXTURES = dict(zip(KEYS, ("knowledge_eval_fifa.json", "knowledge_eval_circular27.json", "knowledge_eval_circular10.json"), strict=True))
ANSWERABLE = ("regla_roja_mano", "circular10_saque_meta_coloquial", "circular10_video_solicitudes", "circular10_penal_no_acumulable",
              "circular10_distingue_fifa", "circular27_modificaciones")
NO_EVIDENCE = ("regla_sin_evidencia_salario", "circular10_sin_evidencia_circular9")
# variantes coloquiales de las mismas preguntas que ANTES de la Fase 20 no recuperaban la evidencia en el top 5 y ahora sí
FIXED_VARIANTS = (
    ("circular10_penal_no_acumulable", "¿Una falta que se castiga con penalti cuenta como falta acumulada?"),
    ("circular10_penal_no_acumulable", "falta penalti acumulada"),
    ("circular10_penal_no_acumulable", "penalti falta acumulada cuenta"),
    ("circular10_penal_no_acumulable", "falta acumulable penalti"),
    ("circular10_saque_meta_coloquial", "portero saque de meta pasar el medio campo"),
    ("circular10_saque_meta_coloquial", "saque de meta portero línea central"),
    ("circular10_distingue_fifa", "portero lanza balón más allá del medio campo saque de meta"),
)
# variantes que ya se recuperaban y no deben perderse
KEPT_VARIANTS = (
    ("regla_roja_mano", "tarjeta roja por mano"), ("regla_roja_mano", "mano deliberada tarjeta roja"),
    ("circular10_video_solicitudes", "VAR cuántas solicitudes tiene cada equipo"),
    ("circular10_video_solicitudes", "solicitudes de vídeo por periodo"),
    ("circular10_saque_meta_coloquial", "saque de meta guardameta línea de medio campo"),
    ("circular10_penal_no_acumulable", "tiro penal faltas acumuladas"),
    ("circular27_modificaciones", "novedades del reglamento futsal 2025/26"), ("circular27_modificaciones", "circular 27 modificaciones"),
)


def _n(t: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", t)).lower()


@pytest.fixture(scope="module")
def real(pg_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    missing = [k for k, p in PDFS.items() if not p.exists()]
    if missing:
        pytest.skip(f"faltan PDF descargados: {missing}")
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE kb_chunks, kb_document_versions, kb_documents, kb_ingestion_runs RESTART IDENTITY CASCADE"))
    store = tmp_path_factory.mktemp("kb")
    for k in KEYS:
        ingest_document(pg_engine, KNOWLEDGE_SOURCES[k].spec, PDFS[k], storage=store)
    yield pg_engine


def _case(case_id: str) -> EvalCase:
    return next(c for c in rule_cases() if c.id == case_id)


def _queries(case: EvalCase) -> list[str]:
    """Las consultas de la ruta de referencia del caso (una por búsqueda)."""
    out: list[str] = []
    while case.plan is not None and (calls := case.plan(len(out), [])):
        out.append(str(calls[0].arguments["query"]))
    return out


def _evidence(search: KnowledgeSearch, case: EvalCase, queries: list[str]) -> dict[str, list[dict[str, object]]]:
    """Para cada hecho con texto esperado: todos los fragmentos de los 5 primeros (de cualquier consulta) que lo contienen, en orden."""
    found: dict[str, list[dict[str, object]]] = {}
    for fact in case.facts or []:
        assert isinstance(fact, Fact) and fact.contains
        found[fact.contains] = []
        for q in queries:
            res = search.search(q, limit=5)
            for item, hit in zip(payload(res)["hits"], res.hits, strict=True):
                if _n(fact.contains) in _n(hit.text):
                    found[fact.contains].append({**item, "_hit": hit})
    return found


def _cited_correctly(case: EvalCase, fact: Fact, items: list[dict[str, object]]) -> bool:
    """¿Algún fragmento con la evidencia es del documento esperado y su rango de páginas cubre una página esperada?"""
    for item in items:
        hit = item["_hit"]
        pages = set(range(hit.page_start, hit.page_end + 1))  # type: ignore[attr-defined]
        if hit.doc_key in case.kb_docs and set(fact.pages) & pages:  # type: ignore[attr-defined]
            return True
    return False


def test_recovery_of_the_reference_queries_is_complete(real: Engine) -> None:
    search = KnowledgeSearch(real)
    failed = {cid: [k for k, v in _evidence(search, _case(cid), _queries(_case(cid))).items() if not v] for cid in ANSWERABLE}
    assert {k: v for k, v in failed.items() if v} == {}, failed


def test_citations_point_to_the_document_and_page_that_hold_the_evidence(real: Engine) -> None:
    search = KnowledgeSearch(real)
    wrong: list[str] = []
    for cid in ANSWERABLE:
        case = _case(cid)
        found = _evidence(search, case, _queries(case))
        for fact in case.facts or []:
            items = found[fact.contains or ""]
            if not _cited_correctly(case, fact, items):
                wrong.append(f"{cid}: esperado {sorted(case.kb_docs)} p{fact.pages}")  # type: ignore[attr-defined]
            for item in items:  # los metadatos que recibe el agente salen del propio fragmento
                hit = item["_hit"]
                assert item["doc_key"] == hit.doc_key and item["document"] == hit.document_title and item["type"] == hit.doc_type  # type: ignore[attr-defined]
                assert item["pages"] == (str(hit.page_start) if hit.page_start == hit.page_end else f"{hit.page_start}-{hit.page_end}")  # type: ignore[attr-defined]
    assert wrong == []


def test_the_specific_circular_provision_is_not_replaced_by_the_general_rule(real: Engine) -> None:
    search = KnowledgeSearch(real)
    for cid in ("circular10_saque_meta_coloquial", "circular10_penal_no_acumulable", "circular10_video_solicitudes"):
        found = _evidence(search, _case(cid), _queries(_case(cid)))
        assert all(v and v[0]["type"] == "circular" for v in found.values()), cid
    both = _evidence(search, _case("circular10_distingue_fifa"), _queries(_case("circular10_distingue_fifa")))
    assert {v[0]["type"] for v in both.values() if v} == {"circular", "reglamento"}      # circular y regla general, por separado


@pytest.mark.parametrize(("cid", "query"), FIXED_VARIANTS + KEPT_VARIANTS)
def test_colloquial_variants_reach_the_same_evidence(real: Engine, cid: str, query: str) -> None:
    case = _case(cid)
    found = _evidence(KnowledgeSearch(real), case, [query])
    assert any(found.values()), (cid, query)


def test_a_question_citing_circular_10_returns_its_provisions_first(real: Engine) -> None:
    res = KnowledgeSearch(real).search("¿Qué dice la Circular 10 sobre el saque de meta del portero?", limit=5)
    assert res.references == [{"circular": 10, "doc_key": KEYS[2]}]
    assert [h.doc_key for h in res.hits[:2]] == [KEYS[2]] * 2 and res.hits[0].doc_type == "circular"


def test_no_evidence_questions_return_nothing_that_answers_them(real: Engine) -> None:
    search = KnowledgeSearch(real)
    salary = payload(search.search(_case("regla_sin_evidencia_salario").question, limit=5))
    assert not re.search(r"\b(cobra|cobran|honorarios|euros|salario|tarifa)\b", " ".join(h["text"] for h in salary["hits"]), re.I)
    c9 = payload(search.search(_case("circular10_sin_evidencia_circular9").question, limit=5))
    assert c9["cited_circulars"] == [{"circular": 9, "indexed": False}] and "no está en la base" in c9["note"]
    # el único fragmento que habla de la Circular 9 es la Circular 10 (que la anula): es lo que se puede citar, no su contenido
    assert all(h["doc_key"] != "rfef-circular-9" for h in c9["hits"])


def test_scope_text_of_other_documents_is_not_mixed_into_the_circular(real: Engine) -> None:
    res = KnowledgeSearch(real).search("saque de meta guardameta línea de medio campo tiro libre indirecto", limit=5)
    c10 = [h for h in res.hits if h.doc_key == KEYS[2]]
    assert c10 and all(h.doc_type == "circular" for h in c10) and all(h.doc_type == "reglamento" for h in res.hits if h.doc_key == KEYS[0])


def test_fixture_metrics_do_not_regress(real: Engine) -> None:
    floors = {  # (literal text@5, reformulada text@5, literal page@5) medidos antes de la Fase 20 como suelo; circular 10 literal mejora
        KEYS[0]: (0.6, 0.88, 0.76), KEYS[1]: (0.667, 1.0, 0.667), KEYS[2]: (0.75, 1.0, 0.75)}
    for key, (lit5, ref5, page5) in floors.items():
        qs = load_questions(Path("tests/fixtures") / FIXTURES[key])
        lit = summarize(evaluate(KnowledgeSearch(real), qs))
        ref = summarize(evaluate(KnowledgeSearch(real), qs, reformulated=True))
        assert lit["text@5"] >= lit5 and ref["text@5"] >= ref5 and lit["page@5"] >= page5, (key, lit, ref)


@pytest.mark.parametrize(("cid", "query", "rule"), [
    ("circular10_saque_meta_coloquial", "saque de meta portero pasar la linea de medio campo", 12),     # consultas reales de la ejecución live
    ("circular10_saque_meta_coloquial", "guardameta saque de meta mas alla de la linea de medio campo", 16),
    ("circular10_penal_no_acumulable", "falta acumulada penalti", 13),
])
def test_a_rule_filter_chosen_by_the_model_no_longer_hides_the_circular(real: Engine, cid: str, query: str, rule: int) -> None:
    case = _case(cid)
    fact = (case.facts or [])[0]
    assert fact.contains
    res = KnowledgeSearch(real).search(query, limit=5, rule=rule)
    assert any(_n(fact.contains) in _n(h.text) and h.doc_key == KEYS[2] for h in res.hits), (query, rule)
