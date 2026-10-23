"""PDF compuesto (circular + anexos): partes por tramo de páginas, reglas del resumen y exclusión de duplicados."""
import pytest

from futsal.knowledge.ingest import apply_sections
from futsal.knowledge.models import ExtractedDocument, PageSection, PageText
from futsal.knowledge.profiles import PROFILES
from futsal.knowledge.structure import build_structure

PROFILE = PROFILES["rfef-circular"]
LONG = "Este es un párrafo normativo largo que ocupa todo el ancho de la línea del documento original."
SECTIONS = (PageSection(1, 1, "Comunicación"), PageSection(2, 3, "Resumen de cambios"),
            PageSection(4, 5, "Reglas completas", skip_reason="idéntico a otro documento"))


def _doc(*texts: str) -> ExtractedDocument:
    return ExtractedDocument([PageText(i, t, None, 0, ()) for i, t in enumerate(texts, 1)])


def _body(*lines: str) -> str:
    return "\n".join(lines)


def test_parts_follow_page_ranges_and_summary_rules_do_not_leak_across_subsections() -> None:
    doc = _doc(
        _body("Circular n.º 27", LONG, "Fin."),
        _body("Fédération Internationale de Football Association", "FIFA-Strasse 20  C. P. 8044", "2",
              "Regla 12. Faltas y", "conducta incorrecta", LONG, "Fin de infracciones."),
        _body("3", LONG, "Directrices", "prácticas para árbitros", LONG, "Fin."),
    )
    st = build_structure(doc, PROFILE, SECTIONS)
    by_page = {s.page_start: s for s in st.segments}
    assert by_page[1].part == "Comunicación" and by_page[1].rule_number is None
    rule12 = by_page[2]
    assert rule12.part == "Resumen de cambios" and rule12.rule_number == 12
    assert "Regla 12. Faltas y" in rule12.text                       # el encabezado se conserva en el texto
    assert "Fédération" not in rule12.text and "FIFA-Strasse" not in rule12.text and "\n2\n" not in f"\n{rule12.text}\n"
    guide = by_page[3]
    assert guide.part.endswith("· Directrices prácticas para árbitros")
    assert guide.rule_number is None                                 # no hereda la regla 12 del subapartado anterior


def test_new_range_resets_rule_and_part_even_without_headings() -> None:
    doc = _doc(_body("Regla 3. Los", "jugadores", LONG, "Fin."), _body(LONG, "Fin."))
    st = build_structure(doc, PROFILE, (PageSection(1, 1, "A"), PageSection(2, 2, "B")))
    assert [(s.part, s.rule_number) for s in st.segments] == [("A", 3), ("B", None)]


def test_apply_sections_drops_skipped_pages_but_keeps_numbering() -> None:
    doc = _doc("uno", "dos", "tres", "cuatro", "cinco")
    kept = apply_sections(doc, SECTIONS)
    assert [p.number for p in kept.pages] == [1, 2, 3]
    assert apply_sections(doc, (PageSection(1, 5, "todo"),)) is doc  # sin exclusiones no se toca nada


@pytest.mark.parametrize("sections", [
    (PageSection(1, 3, "A"), PageSection(3, 4, "B")),                  # solapados
    (PageSection(1, 9, "A"),),                                        # fuera del documento
    (PageSection(0, 2, "A"),),
    (PageSection(4, 2, "A"),)])
def test_apply_sections_rejects_invalid_ranges(sections: tuple[PageSection, ...]) -> None:
    with pytest.raises(ValueError):
        apply_sections(_doc("1", "2", "3", "4", "5"), sections)
