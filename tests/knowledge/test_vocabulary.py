"""Fase 20 (sin base de datos): reglas de vocabulario, referencias a circulares y aviso de circular no indexada."""
import pytest

from futsal.api.knowledge import NOT_INDEXED, payload
from futsal.knowledge.search import SearchResult
from futsal.knowledge.vocabulary import EXPANSIONS, ascii_words, cited_circulars, triggered


def _fired(query: str) -> list[str]:
    return ["+".join(e.trigger) for e, _ in triggered(query)]


def test_the_table_is_small_explicit_and_every_rule_says_why() -> None:
    assert 0 < len(EXPANSIONS) <= 6                                  # sin listas enormes de términos genéricos
    assert all(e.why and e.trigger and e.add for e in EXPANSIONS)
    assert all(w == w.lower() and w.isascii() for e in EXPANSIONS for t in e.trigger for w in t.split("|"))   # disparadores normalizados


def test_triggers_match_whole_words_only() -> None:
    assert _fired("¿Puede el portero sacar de meta?") == ["portero|porteros|portera|porteras"]
    assert _fired("saque de esquina portería del ejecutor") == []   # «portería» no es «portero»: el prefijo daba un falso disparo medido
    assert _fired("¿Cuántos penaltis hay en la tanda?") == ["penalti|penaltis"]
    assert _fired("el penal se tira desde la marca") == []          # ya está en el vocabulario del reglamento


def test_multiword_rules_need_every_word() -> None:
    assert _fired("más allá de la línea de medio campo") == ["medio+campo"]
    assert _fired("balón en medio de la pista") == []
    assert _fired("reglas del campo de juego") == []
    assert _fired("pasa la línea central") == ["linea+central"]


def test_a_question_without_trigger_words_is_not_expanded() -> None:
    for q in ("¿Cuánto dura cada mitad del partido?", "tiempo muerto duración", "sexta falta acumulada tiro libre directo barrera",
              "¿Qué modificaciones se han aprobado en las Reglas de Juego del Futsal 2025/2026?", "cambios en las reglas de juego 2025/2026"):
        assert triggered(q) == [], q


def test_accents_and_case_do_not_hide_a_trigger() -> None:
    assert ascii_words("PENALTI Línea") == ["penalti", "linea"]
    assert _fired("PENALTI y LÍNEA CENTRAL") == ["penalti|penaltis", "linea+central"]


@pytest.mark.parametrize(("query", "expected"), [
    ("Circular 10", [10]), ("¿qué dice la circular n.º 10?", [10]), ("circular nº 27", [27]), ("Circular número 9 de 17 de agosto", [9]),
    ("circular 9 y circular n.º 10", [9, 10]), ("circular 10 y la 10 otra vez circular 10", [10]),
    ("circulares de la RFEF", []), ("¿Qué circular anula a la anterior?", []), ("Circular 2026", []), ("regla 10 de la FIFA", []),
])
def test_explicit_circular_references(query: str, expected: list[int]) -> None:
    assert cited_circulars(query) == expected


def test_payload_warns_when_a_cited_circular_is_not_in_the_base_and_not_otherwise() -> None:
    missing = SearchResult("circular 9", "fts", [], [{"circular": 9, "doc_key": None}])
    body = payload(missing)
    assert body["cited_circulars"] == [{"circular": 9, "indexed": False}]
    assert NOT_INDEXED.format(names="la Circular 9") in body["note"]
    mixed = payload(SearchResult("circulares 9 y 10", "fts", [], [{"circular": 9, "doc_key": None}, {"circular": 10, "doc_key": "c10"}]))
    assert "la Circular 9" in mixed["note"] and "Circular 10" not in mixed["note"]
    present = payload(SearchResult("circular 10", "fts", [], [{"circular": 10, "doc_key": "c10"}]))
    assert present["cited_circulars"] == [{"circular": 10, "indexed": True}] and "no está en la base" not in present["note"]
    assert "cited_circulars" not in payload(SearchResult("saque", "fts"))
