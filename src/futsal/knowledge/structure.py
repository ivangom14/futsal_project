"""Detección de estructura (partes, reglas, apartados) a partir del texto por página.

La lógica es genérica; lo específico de cada documento vive en un `StructureProfile`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from futsal.knowledge.models import ExtractedDocument, Paragraph, Segment

_BULLET = re.compile(r"^(•|-|–|\d{1,2}[.)]|[a-z]\))\s")


@dataclass(frozen=True)
class StructureProfile:
    """Patrones de un tipo de documento. Un perfil vacío trata el documento como texto plano."""

    # Líneas de cabecera/pie (solo se miran las primeras/últimas de cada página).
    running_headers: tuple[re.Pattern[str], ...] = ()
    # Cabecera con regla: grupos (número, título). Fija la regla vigente.
    rule_header: re.Pattern[str] | None = None
    # Portadilla de regla: línea «Regla N»; el resto de líneas es el título.
    rule_divider: re.Pattern[str] | None = None
    # Portadillas de parte: (patrón sobre el texto de la página, título de la parte).
    part_dividers: tuple[tuple[re.Pattern[str], str], ...] = ()
    # Línea completa que abre una parte: (patrón, título de la parte).
    part_headings: tuple[tuple[re.Pattern[str], str], ...] = ()
    # Apartado numerado: grupos (número, título); el número debe ser correlativo.
    section_heading: re.Pattern[str] | None = None
    # Entrada del índice para verificar la estructura: grupos (número de regla, página).
    toc_rule_entry: re.Pattern[str] | None = None
    header_lines: int = 4
    footer_lines: int = 2
    divider_max_chars: int = 90
    wrap_ratio: float = 0.8


_UPPER_CONT = re.compile(r"[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ ,/\-.()]{1,50}")


def _is_toc(lines: list[str]) -> bool:
    """Página de índice: la mayoría de líneas terminan en un número de página."""
    if len(lines) < 8:
        return False
    hits = sum(1 for ln in lines if re.search(r"\s\d{1,3}\s*$", ln))
    return hits >= 0.5 * len(lines)


@dataclass
class PageInfo:
    number: int
    printed_page: int | None
    cleaned_chars: int
    kind: str  # content | divider | toc
    part: str
    rule_number: int | None


@dataclass
class StructuredDocument:
    segments: list[Segment] = field(default_factory=list)
    pages: list[PageInfo] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _clean_lines(text: str, number: int, profile: StructureProfile
                 ) -> tuple[list[str], int | None, tuple[int, str] | None]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    printed: int | None = None
    header_rule: tuple[int, str] | None = None
    keep: list[str] = []
    n = len(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        edge = i < profile.header_lines or i >= n - profile.footer_lines
        if edge:
            if s.isdigit() and len(s) <= 3:
                printed = int(s)
                if printed == number:
                    continue
            if profile.rule_header and (m := profile.rule_header.match(s)):
                header_rule = (int(m.group(1)), _norm(m.group(2)))
                continue
            if any(p.match(s) for p in profile.running_headers):
                continue
        keep.append(ln)
    return keep, printed, header_rule


def _paragraphs(lines: list[str], profile: StructureProfile) -> list[list[str]]:
    """Agrupa líneas en párrafos: una línea corta cierra el párrafo; viñetas abren uno nuevo."""
    if not lines:
        return []
    lens = sorted(len(ln) for ln in lines)
    width = lens[int(0.9 * (len(lens) - 1))]
    out: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        s = ln.strip()
        if cur and _BULLET.match(s):
            out.append(cur)
            cur = []
        cur.append(s)
        if len(ln) < profile.wrap_ratio * width:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _join(par: list[str]) -> str:
    text = ""
    for ln in par:
        if text.endswith("-") and ln[:1].islower():
            text = text[:-1] + ln
        else:
            text = f"{text} {ln}" if text else ln
    return _norm(text)


def build_structure(doc: ExtractedDocument, profile: StructureProfile) -> StructuredDocument:
    result = StructuredDocument()
    part = "Introducción"
    rule_no: int | None = None
    rule_title: str | None = None
    started = False  # los disparadores de parte solo valen tras la primera portadilla
    section_no: int | None = None
    section_title: str | None = None
    sub_title: str | None = None
    seg: Segment | None = None

    def new_segment() -> Segment:
        nonlocal seg
        seg = Segment(part, rule_no, rule_title, str(section_no) if section_no else None,
                      section_title, sub_title)
        result.segments.append(seg)
        return seg

    for page in doc.pages:
        lines, printed, header_rule = _clean_lines(page.text, page.number, profile)
        flat = _norm(" ".join(lines))
        divider = False
        if len(flat) <= profile.divider_max_chars and lines:
            for pat, title in profile.part_dividers:
                if pat.search(flat):
                    part, rule_no, rule_title, section_no, section_title = title, None, None, None, None
                    sub_title = None
                    divider, started, seg = True, True, None
            if not divider and profile.rule_divider:
                idx = next((i for i, ln in enumerate(lines)
                            if profile.rule_divider.match(ln.strip())), None)
                if idx is not None:
                    m = profile.rule_divider.match(lines[idx].strip())
                    assert m
                    rule_no = int(m.group(1))
                    rule_title = _norm(" ".join(ln for i, ln in enumerate(lines) if i != idx))
                    part, section_no, section_title = f"Regla {rule_no}", None, None
                    divider, started, seg = True, True, None
            if divider:
                sub_title = None
        if not divider and header_rule and header_rule[0] != rule_no:
            rule_no, rule_title = header_rule
            part, section_no, section_title, seg = f"Regla {rule_no}", None, None, None
            sub_title, started = None, True
        elif not divider and header_rule:
            started = True
        if divider:
            result.pages.append(PageInfo(page.number, printed, len(flat), "divider", part, rule_no))
            continue
        if not started and _is_toc(lines):
            result.pages.append(PageInfo(page.number, printed, len(flat), "toc", part, rule_no))
            continue
        emph = {_norm(r) for r in page.emphasized}

        pending: list[str] = []
        added = [0]

        def flush() -> None:
            for par in _paragraphs(pending, profile):
                text = _join(par)
                if text:
                    (seg or new_segment()).paragraphs.append(Paragraph(page.number, text))
                    added[0] += 1
            pending.clear()

        skip = 0
        for idx, ln in enumerate(lines):
            if skip:
                skip -= 1
                continue
            s = ln.strip()
            new_part = next((t for p, t in profile.part_headings if started and p.fullmatch(s)),
                            None)
            sec = profile.section_heading.fullmatch(s) if profile.section_heading else None
            if new_part:
                flush()
                part, rule_no, rule_title, section_no, section_title, seg = (
                    new_part, None, None, None, None, None)
                sub_title = None
            elif sec and int(sec.group(1)) == (section_no or 0) + 1:
                flush()
                title = _norm(sec.group(2))
                nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                if len(s) > 45 and _UPPER_CONT.fullmatch(nxt):
                    title, skip = f"{title} {nxt}", 1
                section_no, section_title, seg, sub_title = int(sec.group(1)), title, None, None
            elif started and len(s) >= 3 and s in emph:
                flush()
                title = s
                for extra in lines[idx + 1: idx + 3]:  # subtítulo partido en varias líneas
                    if extra.strip() in emph and len(extra.strip()) >= 3:
                        title, skip = f"{title} {extra.strip()}", skip + 1
                    else:
                        break
                seg, sub_title = None, title
            else:
                pending.append(ln)
        flush()
        if not added[0] and flat:  # página solo de títulos/pies de figura: se conserva el texto
            (seg or new_segment()).paragraphs.append(
                Paragraph(page.number, _norm(" · ".join(ln.strip() for ln in lines))))
        result.pages.append(PageInfo(page.number, printed, len(flat), "content", part, rule_no))
    return result
