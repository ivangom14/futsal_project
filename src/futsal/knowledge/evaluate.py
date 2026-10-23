"""Evaluación offline de la recuperación: preguntas con evidencia verificada contra el PDF."""
from __future__ import annotations

import json
import re
import statistics
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from futsal.knowledge.search import KnowledgeSearch, SearchHit

KS = (1, 3, 5)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", t)).lower()


@dataclass
class QuestionResult:
    id: str
    category: str
    expect: str
    rank_text: int | None      # primer puesto cuyo texto contiene la evidencia esperada
    rank_page: int | None      # primer puesto cuyo rango de páginas cubre una página esperada
    top_score: float
    method: str
    millis: float
    refs: list[str] = field(default_factory=list)


def _covers(hit: SearchHit, ev: dict[str, Any]) -> tuple[bool, bool]:
    if ev.get("doc_key") and hit.doc_key != ev["doc_key"]:  # evidencia de un documento concreto (varios indexados)
        return False, False
    page = any(hit.page_start <= p <= hit.page_end for p in ev["pages"])
    return page, _norm(ev["contains"]) in _norm(hit.text)


def evaluate(search: KnowledgeSearch, questions: list[dict[str, Any]], *, limit: int = 5,
             doc_key: str | None = None, reformulated: bool = False) -> list[QuestionResult]:
    """`reformulated=True` usa `q["reformulated"]` (si existe) en vez de la pregunta literal."""
    out: list[QuestionResult] = []
    for q in questions:
        t0 = time.perf_counter()
        query: str = (q.get("reformulated") if reformulated else None) or q["question"]
        res = search.search(query, limit=limit, doc_key=doc_key)
        ms = (time.perf_counter() - t0) * 1000
        rank_text = rank_page = None
        for i, hit in enumerate(res.hits, 1):
            for ev in q["evidence"]:
                page, txt = _covers(hit, ev)
                if txt and rank_text is None:
                    rank_text = i
                if page and rank_page is None:
                    rank_page = i
        out.append(QuestionResult(
            q["id"], q["category"], q["expect"], rank_text, rank_page,
            res.hits[0].score if res.hits else 0.0, res.method, ms,
            [f"R{h.rule_number} §{h.section_number} p{h.page_start}-{h.page_end}"
             for h in res.hits[:3]]))
    return out


def summarize(results: list[QuestionResult]) -> dict[str, Any]:
    ans = [r for r in results if r.expect == "answerable"]
    no = [r for r in results if r.expect != "answerable"]

    def hit_at(rs: list[QuestionResult], k: int, attr: str) -> float:
        return round(sum(1 for r in rs if (getattr(r, attr) or 99) <= k) / max(len(rs), 1), 3)

    cats: dict[str, dict[str, float]] = {}
    for c in sorted({r.category for r in ans}):
        rs = [r for r in ans if r.category == c]
        cats[c] = {"n": len(rs), "text@5": hit_at(rs, 5, "rank_text")}
    return {
        "answerable": len(ans), "no_evidence": len(no),
        **{f"text@{k}": hit_at(ans, k, "rank_text") for k in KS},
        **{f"page@{k}": hit_at(ans, k, "rank_page") for k in KS},
        "mrr_text": round(sum(1 / r.rank_text for r in ans if r.rank_text) / max(len(ans), 1), 3),
        "by_category": cats,
        "top_score_answerable_median": round(statistics.median(
            [r.top_score for r in ans] or [0.0]), 3),
        "top_score_no_evidence_max": round(max([r.top_score for r in no] or [0.0]), 3),
        "latency_ms_median": round(statistics.median([r.millis for r in results] or [0.0]), 1),
        "latency_ms_max": round(max([r.millis for r in results] or [0.0]), 1),
    }


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["questions"])
