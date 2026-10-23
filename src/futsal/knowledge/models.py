"""Modelos de la extracción documental (sin dependencias de infraestructura)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PageText:
    """Texto crudo de una página. `number` es la posición 1-based en el PDF original."""

    number: int
    text: str
    label: str | None = None
    image_count: int = 0
    # Tramos en tipografía destacada (negrita/medium): candidatos a subtítulo.
    emphasized: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageSection:
    """Tramo de páginas físicas de un PDF compuesto (varios documentos en un mismo archivo)."""

    first: int
    last: int
    part: str
    skip_reason: str | None = None  # si consta, el tramo no se indexa (p. ej. texto idéntico al de otro documento)


@dataclass(frozen=True)
class ExtractedDocument:
    pages: list[PageText]
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Paragraph:
    page: int
    text: str


@dataclass
class Segment:
    """Unidad estructural (apartado de una regla o de una parte) con sus párrafos y páginas."""

    part: str
    rule_number: int | None
    rule_title: str | None
    section_number: str | None
    section_title: str | None
    subsection_title: str | None = None
    paragraphs: list[Paragraph] = field(default_factory=list)

    @property
    def page_start(self) -> int:
        return self.paragraphs[0].page

    @property
    def page_end(self) -> int:
        return self.paragraphs[-1].page

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)
