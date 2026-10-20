"""Fragmentación: tamaños, solape, subtítulos y párrafos completos (sin BD)."""
import pytest

from futsal.knowledge.chunking import ChunkConfig, chunk_segments
from futsal.knowledge.models import Paragraph, Segment


def seg(paras: list[tuple[int, str]], *, sub: str | None = None, section: str | None = "1",
        rule: int | None = 5) -> Segment:
    return Segment("Regla 5", rule, "Los árbitros", section, "AUTORIDAD", sub,
                   [Paragraph(p, t) for p, t in paras])


def words(n: int, tag: str = "palabra") -> str:
    return " ".join(f"{tag}{i}" for i in range(n)) + "."


CFG = ChunkConfig(target_chars=300, max_chars=500, min_chars=150, overlap_chars=100)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        ChunkConfig(target_chars=100, max_chars=50)
    with pytest.raises(ValueError):
        ChunkConfig(overlap_chars=5000)


def test_small_section_is_one_chunk_with_rule_and_page_range() -> None:
    chunks = chunk_segments([seg([(10, "Regla general."), (11, "Excepción: salvo que...")])], CFG)
    assert len(chunks) == 1
    c = chunks[0]
    assert (c.rule_number, c.section_number, c.page_start, c.page_end) == (5, "1", 10, 11)
    assert "Regla general." in c.text and "Excepción" in c.text


def test_never_splits_a_paragraph_and_covers_everything() -> None:
    paras = [(i + 1, f"P{i} " + words(20, f"w{i}_")) for i in range(12)]
    chunks = chunk_segments([seg(paras)], CFG)
    assert len(chunks) > 2
    assert all(len(c.text) <= CFG.max_chars for c in chunks)
    for _, text in paras:
        assert any(text in c.text for c in chunks)  # cada párrafo íntegro en algún fragmento
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_overlap_repeats_whole_paragraph_at_start_of_next_chunk() -> None:
    paras = [(i + 1, f"P{i} " + words(12, f"w{i}_")) for i in range(10)]
    chunks = chunk_segments([seg(paras)], CFG)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        last_para = prev.text.split("\n")[-1]
        assert nxt.text.split("\n")[0] == last_para
    no_overlap = chunk_segments([seg(paras)], ChunkConfig(300, 500, 150, 0))
    assert len(no_overlap) <= len(chunks)
    assert no_overlap[1].text.split("\n")[0] != no_overlap[0].text.split("\n")[-1]


def test_long_paragraph_is_split_on_sentences() -> None:
    sentence = "Esta frase describe una condición del reglamento con bastante detalle. "
    chunks = chunk_segments([seg([(3, sentence * 20)])], CFG)
    assert len(chunks) > 1 and all(len(c.text) <= CFG.max_chars for c in chunks)
    assert all(c.text.rstrip().endswith(".") for c in chunks)


def test_subsection_starts_new_chunk_only_when_current_is_big_enough() -> None:
    a = seg([(1, words(40, "a"))], sub=None)            # > min_chars
    b = seg([(2, words(8, "b"))], sub="Juego peligroso")
    c = seg([(3, "Breve.")], sub="Otra excepción")
    chunks = chunk_segments([a, b, c], CFG)
    assert chunks[0].subsection_title is None and "Juego peligroso" not in chunks[0].text
    assert chunks[1].subsection_title == "Juego peligroso"
    assert "Otra excepción\nBreve." in chunks[1].text  # el fragmento corto no se corta por el subtítulo


def test_different_sections_never_share_a_chunk() -> None:
    chunks = chunk_segments([seg([(1, "Uno.")], section="1"), seg([(1, "Dos.")], section="2")], CFG)
    assert [(c.section_number, c.text) for c in chunks] == [("1", "Uno."), ("2", "Dos.")]
