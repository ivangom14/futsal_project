"""Servicio de recuperación documental (independiente del agente y de la capa HTTP).

Búsqueda textual de PostgreSQL: `tsvector` en español sin acentos, consulta OR ordenada con
`ts_rank_cd` (las coincidencias que contienen todos los términos puntúan más) y, si no hay
ningún resultado, respaldo por similitud de trigramas para erratas.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import Engine, text

MAX_QUERY_CHARS = 500
MAX_LIMIT = 10
DEFAULT_LIMIT = 5
DEFAULT_HIT_CHARS = 1200
DEFAULT_TOTAL_CHARS = 6000
TRIGRAM_MIN = 0.4
RANK_NORM = 32  # normalización de ts_rank_cd (bits de PostgreSQL)
AND_BONUS = 1.0  # peso extra si el fragmento contiene todos los términos de la consulta
_LEXEME = re.compile(r"[a-z0-9ñ]+")


class SearchError(ValueError):
    """Consulta o filtros no válidos."""


@dataclass
class SearchHit:
    chunk_id: int
    doc_key: str
    document_title: str
    version_id: int
    version_label: str | None
    is_current: bool
    part: str | None
    rule_number: int | None
    rule_title: str | None
    section_number: str | None
    section_title: str | None
    subsection_title: str | None
    page_start: int
    page_end: int
    score: float
    text: str
    truncated: bool = False


@dataclass
class SearchResult:
    query: str
    method: str  # fts | trigram | none
    hits: list[SearchHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    cut = value.rfind(" ", 0, limit)
    return value[: cut if cut > limit // 2 else limit].rstrip() + "…", True


_SELECT = """
SELECT c.id, d.doc_key, d.title, v.id AS version_id, coalesce(v.version_label, d.version_label)
         AS version_label, v.is_current, c.part, c.rule_number, c.rule_title, c.section_number,
       c.section_title, c.subsection_title, c.page_start, c.page_end, c.text, {score} AS score
FROM kb_chunks c
JOIN kb_document_versions v ON v.id = c.version_id AND v.index_status = 'indexed'
JOIN kb_documents d ON d.id = v.document_id
WHERE {match}
  AND (:include_history OR v.is_current)
  AND (CAST(:doc_key AS text) IS NULL OR d.doc_key = :doc_key)
  AND (CAST(:version_label AS text) IS NULL
       OR coalesce(v.version_label, d.version_label) = :version_label)
  AND (CAST(:language AS text) IS NULL OR d.language = :language)
  AND (CAST(:rule AS integer) IS NULL OR c.rule_number = :rule)
ORDER BY score DESC, c.id
LIMIT :limit
"""
_FTS = _SELECT.format(
    score="(CASE WHEN c.search_vector @@ to_tsquery('simple', :andq) THEN :and_bonus ELSE 0.0 END"
          " + ts_rank_cd(c.search_vector, to_tsquery('simple', :orq), :rank_norm))",
    match="c.search_vector @@ to_tsquery('simple', :orq)")
_TRIGRAM = _SELECT.format(
    score="word_similarity(kb_unaccent(lower(:q)), kb_unaccent(lower(c.text)))",
    match="word_similarity(kb_unaccent(lower(:q)), kb_unaccent(lower(c.text))) >= :tmin")


class KnowledgeSearch:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def search(self, query: str, *, limit: int = DEFAULT_LIMIT, doc_key: str | None = None,
               version_label: str | None = None, language: str | None = None,
               rule: int | None = None, include_history: bool = False,
               max_hit_chars: int = DEFAULT_HIT_CHARS,
               max_total_chars: int = DEFAULT_TOTAL_CHARS) -> SearchResult:
        q = " ".join(query.split())
        if len(q) < 2:
            raise SearchError("la consulta está vacía o es demasiado corta")
        if len(q) > MAX_QUERY_CHARS:
            raise SearchError(f"la consulta supera {MAX_QUERY_CHARS} caracteres")
        if not 1 <= limit <= MAX_LIMIT:
            raise SearchError(f"limit debe estar entre 1 y {MAX_LIMIT}")
        if rule is not None and rule < 1:
            raise SearchError("rule debe ser un número de regla positivo")
        params: dict[str, Any] = {
            "q": q, "limit": limit, "doc_key": doc_key, "version_label": version_label,
            "language": language, "rule": rule, "include_history": include_history,
            "tmin": TRIGRAM_MIN, "and_bonus": AND_BONUS, "rank_norm": RANK_NORM}
        with self._engine.connect() as conn:
            raw = conn.execute(text("SELECT tsvector_to_array(to_tsvector('spanish', "
                                    "kb_unaccent(:q)))"), {"q": q}).scalar() or []
            lexemes = [m for lx in raw for m in _LEXEME.findall(str(lx))]
            rows: list[Any] = []
            method = "none"
            if lexemes:
                params["orq"] = " | ".join(lexemes)
                params["andq"] = " & ".join(lexemes)
                rows = list(conn.execute(text(_FTS), params).mappings())
                method = "fts"
            if not rows:
                rows = list(conn.execute(text(_TRIGRAM), params).mappings())
                method = "trigram" if rows else "none"
        hits: list[SearchHit] = []
        budget = max_total_chars
        for r in rows:
            if budget <= 0:
                break
            body, cut = _truncate(r["text"], min(max_hit_chars, budget))
            budget -= len(body)
            hits.append(SearchHit(
                chunk_id=r["id"], doc_key=r["doc_key"], document_title=r["title"],
                version_id=r["version_id"], version_label=r["version_label"],
                is_current=r["is_current"], part=r["part"], rule_number=r["rule_number"],
                rule_title=r["rule_title"], section_number=r["section_number"],
                section_title=r["section_title"], subsection_title=r["subsection_title"],
                page_start=r["page_start"], page_end=r["page_end"],
                score=round(float(r["score"]), 4), text=body, truncated=cut))
        return SearchResult(q, method, hits)
