"""Ruta de lectura del reglamento (Fase 15, Entrega 3): búsqueda léxica sobre los fragmentos indexados. Solo lectura."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from futsal.knowledge.search import KnowledgeSearch, SearchError, SearchResult

NOTE = ("Fragmentos literales de los documentos indexados (campo document: reglas FIFA o circulares de la RFEF). La puntuación "
        "NO mide relevancia (no hay umbral): lee el texto y comprueba que responde; si no, dilo. Cita documento, regla y página. "
        "Una circular solo rige lo que su texto dice (p. ej. competiciones de la RFEF): no es normativa de la RFFM ni de otra "
        "competición salvo que lo indique. Si dos documentos difieren, preséntalos por separado sin decidir cuál prevalece, "
        "salvo que el texto lo diga.")
NOT_INDEXED = ("La consulta cita {names}, que no está en la base: ningún fragmento procede de ella. No afirmes su contenido; "
               "lo que otro fragmento diga sobre ella (p. ej. que la anula) cítalo con su documento y página.")
NO_HITS = "Sin resultados: reformula con el vocabulario del reglamento (otras palabras) o dilo si sigue sin aparecer."


def _hit(h: Any, with_history: bool) -> dict[str, Any]:
    section = " ".join(x for x in (h.section_number, h.section_title) if x) or None
    pages = str(h.page_start) if h.page_start == h.page_end else f"{h.page_start}-{h.page_end}"
    out: dict[str, Any] = {
        "document": h.document_title, "doc_key": h.doc_key, "version": h.version_label, "rule": h.rule_number,
        "type": h.doc_type, "rule_title": h.rule_title, "section": section, "subsection": h.subsection_title, "pages": pages,
        "score": h.score, "text": h.text, "truncated": h.truncated or None}
    if h.part and h.part != f"Regla {h.rule_number}":
        out["part"] = h.part  # procedencia dentro del documento (p. ej. «Circular FIFA n.º 1941»); la regla ya figura en `rule`
    if with_history:
        out["is_current"] = h.is_current
    return {k: v for k, v in out.items() if v is not None}  # sin nulos: menos tokens para el LLM


def payload(result: SearchResult, with_history: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"query": result.query, "method": result.method, "count": len(result.hits),
                           "hits": [_hit(h, with_history) for h in result.hits], "note": NOTE if result.hits else NO_HITS}
    missing = [f"la Circular {r['circular']}" for r in result.references if r["doc_key"] is None]
    if result.references:
        out["cited_circulars"] = [{"circular": r["circular"], "indexed": r["doc_key"] is not None} for r in result.references]
    if missing:
        out["note"] += " " + NOT_INDEXED.format(names=" y ".join(missing))
    return out


def knowledge_router(get_engine: Callable[[], Engine]) -> APIRouter:
    router = APIRouter()

    @router.get("/knowledge/search")
    def search_rules(q: Annotated[str, Query(min_length=2, max_length=500)],
                     limit: Annotated[int, Query(ge=1, le=10)] = 5,
                     rule: Annotated[int | None, Query(ge=1)] = None,
                     doc_key: str | None = None, include_history: bool = False) -> dict[str, Any]:
        try:
            result = KnowledgeSearch(get_engine()).search(q, limit=limit, rule=rule, doc_key=doc_key,
                                                          include_history=include_history)
        except SearchError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SQLAlchemyError as exc:  # sin migración 0004 o sin documento indexado
            raise HTTPException(503, "base de conocimiento no disponible (alembic upgrade head y knowledge-ingest)") from exc
        return payload(result, include_history)

    return router
