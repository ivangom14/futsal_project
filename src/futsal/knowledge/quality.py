"""Informe de calidad de la extracción: páginas vacías, tablas, texto ilegible y estructura."""
from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from futsal.knowledge.models import ExtractedDocument
from futsal.knowledge.structure import StructuredDocument, StructureProfile

_GARBLED = re.compile(r"[\ufffd\ue000-\uf8ff\x00-\x08\x0b\x0c\x0e-\x1f]|\(cid:\d+\)")
LOW_TEXT_CHARS = 200
TABLE_MIN_LINES = 10
TABLE_MAX_MEDIAN_LINE = 28


@dataclass
class PageQuality:
    number: int
    kind: str
    raw_chars: int
    cleaned_chars: int
    images: int
    printed_page: int | None
    flags: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    pages: list[PageQuality]
    total_pages: int
    kinds: dict[str, int]
    flagged: dict[str, list[int]]
    printed_page_mismatches: list[int]
    rules_detected: list[int]
    rules_declared: dict[int, int]  # regla -> página según el índice del documento
    rules_start_mismatch: dict[int, tuple[int, int | None]]
    sections_per_rule: dict[int, int]
    segments: int
    segment_chars_max: int
    pages_without_segment: list[int]
    garbled_chars: int
    extraction_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return (not self.printed_page_mismatches and not self.rules_start_mismatch
                and self.garbled_chars == 0 and not self.extraction_warnings)

    def render(self) -> str:
        lines = [f"páginas: {self.total_pages}  tipos: {self.kinds}",
                 f"segmentos: {self.segments} (máx {self.segment_chars_max} caracteres)",
                 f"caracteres ilegibles: {self.garbled_chars}",
                 f"páginas con número impreso distinto: {self.printed_page_mismatches or 'ninguna'}",
                 f"reglas detectadas: {self.rules_detected}"]
        if self.rules_declared:
            lines.append(f"reglas del índice: {sorted(self.rules_declared)}; "
                         f"discrepancias de página: {self.rules_start_mismatch or 'ninguna'}")
        for flag, pages in sorted(self.flagged.items()):
            lines.append(f"{flag}: {pages}")
        lines.append(f"páginas de contenido sin segmento: {self.pages_without_segment or 'ninguna'}")
        lines += self.extraction_warnings
        return "\n".join(lines)


def assess(doc: ExtractedDocument, structured: StructuredDocument,
           profile: StructureProfile) -> QualityReport:
    info = {p.number: p for p in structured.pages}
    with_segment = {para.page for seg in structured.segments for para in seg.paragraphs}
    pages: list[PageQuality] = []
    flagged: dict[str, list[int]] = {}
    garbled = 0
    for page in doc.pages:
        pi = info[page.number]
        pq = PageQuality(page.number, pi.kind, len(page.text.strip()), pi.cleaned_chars,
                         page.image_count, pi.printed_page)
        bad = len(_GARBLED.findall(page.text))
        garbled += bad
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        if bad:
            pq.flags.append("texto_ilegible")
        if pq.raw_chars == 0:
            pq.flags.append("sin_texto")
        elif pi.kind == "content" and pi.cleaned_chars < LOW_TEXT_CHARS:
            pq.flags.append("poco_texto_con_imagenes" if page.image_count else "poco_texto")
        if (len(lines) >= TABLE_MIN_LINES
                and statistics.median(len(ln) for ln in lines) < TABLE_MAX_MEDIAN_LINE):
            pq.flags.append("posible_tabla_o_figura")
        if pi.printed_page is not None and pi.printed_page != page.number:
            pq.flags.append("numero_impreso_distinto")
        for f in pq.flags:
            flagged.setdefault(f, []).append(page.number)
        pages.append(pq)

    kinds: dict[str, int] = {}
    for p in structured.pages:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1

    first_page: dict[int, int] = {}
    for p in structured.pages:
        if p.rule_number is not None:
            first_page.setdefault(p.rule_number, p.number)
    declared: dict[int, int] = {}
    if profile.toc_rule_entry:
        toc_text = " ".join(re.sub(r"\s+", " ", pg.text) for pg in doc.pages
                            if info[pg.number].kind == "toc")
        declared = {int(n): int(pg) for n, pg in profile.toc_rule_entry.findall(toc_text)}
    mismatch = {r: (pg, first_page.get(r)) for r, pg in declared.items()
                if first_page.get(r) != pg}
    sections: dict[int, int] = {}
    for seg in structured.segments:
        if seg.rule_number is not None and seg.section_number:
            sections[seg.rule_number] = max(sections.get(seg.rule_number, 0),
                                            int(seg.section_number))
    return QualityReport(
        pages=pages, total_pages=len(doc.pages), kinds=kinds,
        flagged=flagged,
        printed_page_mismatches=flagged.get("numero_impreso_distinto", []),
        rules_detected=sorted(first_page), rules_declared=declared,
        rules_start_mismatch=mismatch, sections_per_rule=sections,
        segments=len(structured.segments),
        segment_chars_max=max((len(s.text) for s in structured.segments), default=0),
        pages_without_segment=[p.number for p in structured.pages
                               if p.kind == "content" and p.number not in with_segment],
        garbled_chars=garbled, extraction_warnings=list(doc.warnings))
