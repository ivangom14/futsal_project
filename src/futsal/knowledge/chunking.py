"""Fragmentación por unidades de significado: nunca parte un párrafo salvo que supere el máximo."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from futsal.knowledge.models import Segment

_SENTENCE = re.compile(r"(?<=[.;:!?])\s+(?=[A-ZÁÉÍÓÚÑ•(«\"])")


@dataclass(frozen=True)
class ChunkConfig:
    target_chars: int = 1200   # tamaño objetivo: se cierra el fragmento al superarlo
    max_chars: int = 1800      # máximo duro (solo se divide un párrafo si él solo lo supera)
    min_chars: int = 500       # no se corta en un subtítulo si el fragmento aún es más corto
    overlap_chars: int = 250   # párrafos completos repetidos al inicio del siguiente fragmento

    def __post_init__(self) -> None:
        if not 0 < self.min_chars <= self.target_chars <= self.max_chars:
            raise ValueError("se requiere 0 < min_chars <= target_chars <= max_chars")
        if not 0 <= self.overlap_chars < self.target_chars:
            raise ValueError("overlap_chars debe ser menor que target_chars")

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class Chunk:
    ordinal: int
    part: str
    rule_number: int | None
    rule_title: str | None
    section_number: str | None
    section_title: str | None
    subsection_title: str | None
    page_start: int
    page_end: int
    text: str


@dataclass
class _Item:
    segment: Segment
    page: int
    text: str


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    cur = ""
    for sentence in _SENTENCE.split(text):
        while len(sentence) > max_chars:  # sin puntuación: corte por palabras
            cut = sentence.rfind(" ", 0, max_chars) or max_chars
            pieces.append((cur + " " + sentence[:cut]).strip() if cur else sentence[:cut])
            cur, sentence = "", sentence[cut:].strip()
        if cur and len(cur) + 1 + len(sentence) > max_chars:
            pieces.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def _unit_key(seg: Segment) -> tuple[str, int | None, str | None]:
    return (seg.part, seg.rule_number, seg.section_number)


def chunk_segments(segments: list[Segment], config: ChunkConfig | None = None) -> list[Chunk]:
    """Une los segmentos de un mismo apartado y los reparte en fragmentos de tamaño razonable.

    Los subtítulos se conservan como línea propia dentro del texto. Un fragmento nuevo solo
    empieza en un subtítulo si el actual ya alcanzó `min_chars`, de modo que una regla general y
    su excepción (normalmente bajo el mismo subtítulo o contiguas) no se separan sin necesidad.
    """
    cfg = config or ChunkConfig()
    chunks: list[Chunk] = []
    units: list[list[Segment]] = []
    for seg in segments:
        if units and _unit_key(units[-1][0]) == _unit_key(seg):
            units[-1].append(seg)
        else:
            units.append([seg])
    for unit in units:
        items: list[_Item] = []
        for seg in unit:
            for para in seg.paragraphs:
                for piece in _split_long(para.text, cfg.max_chars - 80):
                    items.append(_Item(seg, para.page, piece))
        _chunk_unit(items, cfg, chunks)
    return chunks


def _render(items: list[_Item]) -> str:
    lines: list[str] = []
    last_sub: str | None = None
    for it in items:
        sub = it.segment.subsection_title
        if sub and sub != last_sub:
            lines.append(sub)
        last_sub = sub
        lines.append(it.text)
    return "\n".join(lines)


def _chunk_unit(items: list[_Item], cfg: ChunkConfig, out: list[Chunk]) -> None:
    cur: list[_Item] = []
    size = 0
    carried_n = 0  # ítems iniciales de `cur` que son solape del fragmento anterior

    def emit() -> None:
        first = next((i for i in cur if i.segment.subsection_title), cur[0])
        seg = cur[0].segment
        out.append(Chunk(len(out), seg.part, seg.rule_number, seg.rule_title, seg.section_number,
                         seg.section_title, first.segment.subsection_title,
                         min(i.page for i in cur), max(i.page for i in cur), _render(cur)))

    for it in items:
        new_sub = bool(cur) and it.segment is not cur[-1].segment and bool(
            it.segment.subsection_title)
        if len(cur) > carried_n and (size + len(it.text) > cfg.max_chars
                                     or size >= cfg.target_chars
                                     or (new_sub and size >= cfg.min_chars)):
            emit()
            carry: list[_Item] = []
            total = 0
            if not new_sub:  # un subtítulo nuevo no arrastra el tema anterior
                for prev in reversed(cur):  # solape: párrafos completos del final
                    if total + len(prev.text) > cfg.overlap_chars:
                        break
                    carry.insert(0, prev)
                    total += len(prev.text)
            cur, size, carried_n = carry, total, len(carry)
        cur.append(it)
        size += len(it.text)
    if len(cur) > carried_n:
        emit()
