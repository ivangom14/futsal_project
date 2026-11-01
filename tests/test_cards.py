"""Tarjetas del acta: el significado de los códigos RFFM 100/101 (confirmado) y el trato de los códigos desconocidos."""

import pytest

from futsal.domain.cards import CARDS_NOTE, KINDS, LABELS, RED, SECOND_YELLOW, UNKNOWN, YELLOW, card_kind, summarize


@pytest.mark.parametrize("code,flag,expected", [
    ("100", "0", YELLOW), ("100", 0, YELLOW), ("100", None, YELLOW), ("100", "1", SECOND_YELLOW), ("100", 1, SECOND_YELLOW),
    ("101", "0", RED), ("101", "1", RED), (" 101 ", None, RED),
    ("102", "0", UNKNOWN), ("999", "1", UNKNOWN), ("", None, UNKNOWN), (None, None, UNKNOWN), ("abc", "1", UNKNOWN)])
def test_card_kind_mapping(code: str | None, flag: object, expected: str) -> None:
    assert card_kind(code, flag) == expected


def test_unknown_codes_never_get_a_color_and_every_kind_has_a_label() -> None:
    assert card_kind("7") == UNKNOWN and set(LABELS) == set(KINDS) and "sin interpretar" in LABELS[UNKNOWN]
    assert "expulsión" in LABELS[SECOND_YELLOW] and LABELS[RED] == "Roja directa" and LABELS[YELLOW] == "Amarilla"
    assert "confirmado" in CARDS_NOTE and "100" in CARDS_NOTE and "101" in CARDS_NOTE


def test_summary_counts_each_kind() -> None:
    assert summarize([YELLOW, YELLOW, SECOND_YELLOW, RED, UNKNOWN]) == {YELLOW: 2, SECOND_YELLOW: 1, RED: 1, UNKNOWN: 1}
    assert summarize([]) == dict.fromkeys(KINDS, 0)
