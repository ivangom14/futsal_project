"""Estructura documental sobre texto sintético (sin PDF ni BD)."""
import re

from futsal.knowledge.models import ExtractedDocument, PageText
from futsal.knowledge.quality import assess
from futsal.knowledge.structure import StructureProfile, build_structure

PROFILE = StructureProfile(
    running_headers=(re.compile(r"Reglas de Juego 2025"),),
    rule_header=re.compile(r"Regla (\d+)\s+•\s+(.+)"),
    rule_divider=re.compile(r"Regla (\d+)"),
    part_dividers=((re.compile(r"^Protocolo VAR$"), "Protocolo VAR"),),
    section_heading=re.compile(r"(\d{1,2})\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9 ,/\-.()]{2,})"),
    toc_rule_entry=re.compile(r"REGLA (\d+)\.[^\d]*?\s(\d{1,3})(?=\s|$)"),
)


def _doc(*pages: tuple[str, tuple[str, ...]]) -> ExtractedDocument:
    return ExtractedDocument([PageText(i, t, str(i), 0, e) for i, (t, e) in enumerate(pages, 1)])


LONG = "Este es un párrafo normativo largo que ocupa todo el ancho de la línea del documento."
SHORT = "Fin del párrafo."


def _body(*lines: str) -> str:
    return "\n".join(lines)


def test_headers_removed_rule_carried_over_and_pages_kept() -> None:
    doc = _doc(
        ("Índice\n1\nREGLA 1. EL TERRENO 3\n" + "\n".join(["REGLA 9. X 30"] * 8), ()),
        ("El terreno\nRegla 1", ()),
        (_body("Reglas de Juego 2025", "3", "Regla 1   •   El terreno", "1 TERRENO DE JUEGO",
               LONG, SHORT, "2 MARCACIÓN", LONG, SHORT), ()),
        (_body("Reglas de Juego 2025", "4", LONG, SHORT), ()),
    )
    s = build_structure(doc, PROFILE)
    kinds = {p.number: p.kind for p in s.pages}
    assert kinds == {1: "toc", 2: "divider", 3: "content", 4: "content"}
    secs = [(g.rule_number, g.section_number, g.section_title, g.page_start, g.page_end)
            for g in s.segments]
    assert secs == [(1, "1", "TERRENO DE JUEGO", 3, 3), (1, "2", "MARCACIÓN", 3, 4)]
    assert all("Reglas de Juego" not in g.text for g in s.segments)
    assert s.segments[0].rule_title == "El terreno"


def test_section_numbers_must_be_consecutive() -> None:
    doc = _doc(("Regla 2\nEl balón", ()),
               (_body("Regla 2   •   El balón", "1 UNO", LONG, SHORT, "7 SALTO", LONG, SHORT), ()))
    s = build_structure(doc, PROFILE)
    assert [g.section_number for g in s.segments] == ["1"]
    assert "7 SALTO" in s.segments[0].text  # no es un apartado: se queda en el texto


def test_wrapped_section_title_and_emphasized_subsection() -> None:
    doc = _doc(("Regla 3\nJugadores", ()),
               (_body("Regla 3   •   Jugadores",
                      "1 PUBLICIDAD, LOGOTIPOS Y EMBLEMAS SOBRE EL TERRENO DE", "JUEGO",
                      LONG, SHORT, "Excepción", LONG, SHORT),
                ("Excepción",)))
    s = build_structure(doc, PROFILE)
    assert s.segments[0].section_title == "PUBLICIDAD, LOGOTIPOS Y EMBLEMAS SOBRE EL TERRENO DE JUEGO"
    assert (s.segments[1].subsection_title, s.segments[1].section_number) == ("Excepción", "1")


def test_paragraph_grouping_and_hyphen_join() -> None:
    doc = _doc(("Regla 4\nT", ()),
               (_body("Regla 4   •   T", "1 UNO", LONG, LONG[:-8] + "condi-", "ción corta.", LONG, SHORT), ()))
    texts = [p.text for p in build_structure(doc, PROFILE).segments[0].paragraphs]
    assert texts[0].endswith("condición corta.")
    assert len(texts) == 2


def test_figure_only_page_keeps_its_text_and_part_divider() -> None:
    doc = _doc(("Protocolo VAR", ()), ("Reglas de Juego 2025\n2\nSaque (opción 1)", ("Saque (opción 1)",)))
    s = build_structure(doc, PROFILE)
    assert s.segments[0].part == "Protocolo VAR"
    assert "Saque (opción 1)" in s.segments[0].text and s.segments[0].page_start == 2


def test_quality_report_flags_empty_and_garbled_and_toc_mismatch() -> None:
    toc = "ÍNDICE\n" + "\n".join(f"REGLA {n}. TITULO {'X' * n} {n + 9}" for n in range(1, 3)) + "\n" * 0
    toc += "\n" + "\n".join(["REGLA 9. RELLENO 99"] * 8)
    doc = _doc((toc, ()), ("Regla 1\nA", ()), ("", ()),
               (_body("Regla 1   •   A", "1 UNO", "texto � roto"), ()))
    s = build_structure(doc, PROFILE)
    q = assess(doc, s, PROFILE)
    assert q.garbled_chars == 1 and 4 in q.flagged["texto_ilegible"]
    assert q.flagged["sin_texto"] == [3]
    assert q.rules_start_mismatch[1] == (10, 2) and not q.ok
