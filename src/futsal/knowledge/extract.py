"""Extracción de texto por página. `Extractor` es la interfaz para añadir formatos."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from futsal.knowledge.models import ExtractedDocument, PageText


class ExtractionError(Exception):
    pass


class Extractor(Protocol):
    suffixes: tuple[str, ...]

    def extract(self, path: Path) -> ExtractedDocument: ...


class PdfExtractor:
    suffixes: tuple[str, ...] = (".pdf",)

    def extract(self, path: Path) -> ExtractedDocument:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ExtractionError("PDF cifrado")
            labels = reader.page_labels
            pages: list[PageText] = []
            warnings: list[str] = []
            for i, page in enumerate(reader.pages, start=1):
                runs = _EmphasisCollector()
                try:
                    text = page.extract_text(visitor_text=runs.visit) or ""
                except Exception as exc:  # una página rota no aborta el documento
                    text = ""
                    warnings.append(f"página {i}: error de extracción ({type(exc).__name__})")
                pages.append(PageText(i, text, labels[i - 1] if i - 1 < len(labels) else None,
                                      _image_count(page), runs.result()))
            meta = {k.lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()}
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"no se pudo leer el PDF: {type(exc).__name__}") from exc
        return ExtractedDocument(pages, meta, warnings)


# Subtítulos del PDF de FIFA: FIFASans-Medium (la negrita se usa en cabeceras de tabla).
_HEAVY_FONT = re.compile(r"Medium|Semibold|DemiBold", re.I)


class _EmphasisCollector:
    """Recoge, línea a línea, los tramos escritos con una fuente destacada."""

    def __init__(self) -> None:
        self._runs: list[str] = []
        self._open = False

    def visit(self, text: str, cm: object, tm: object, font: object, size: object) -> None:
        name = str(font.get("/BaseFont", "")) if isinstance(font, dict) else ""
        if not text.strip():
            if "\n" in text:
                self._open = False
            return
        if _HEAVY_FONT.search(name):
            piece = " ".join(text.split())
            if self._open:
                self._runs[-1] = f"{self._runs[-1]} {piece}"
            else:
                self._runs.append(piece)
            self._open = not text.endswith("\n")
        else:
            self._open = False

    def result(self) -> tuple[str, ...]:
        return tuple(self._runs)


def _image_count(page: object) -> int:
    try:
        res = page["/Resources"]  # type: ignore[index]
        xobj = res.get_object().get("/XObject")
        if xobj is None:
            return 0
        return sum(1 for x in xobj.get_object().values()
                   if x.get_object().get("/Subtype") == "/Image")
    except Exception:
        return 0


def get_extractor(path: Path) -> Extractor:
    for ex in (PdfExtractor(),):
        if path.suffix.lower() in ex.suffixes:
            return ex
    raise ExtractionError(f"formato no soportado: {path.suffix}")
