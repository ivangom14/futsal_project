"""domain/dates.py: fechas y franjas en Europe/Madrid con reloj inyectado (hoy de referencia: lunes 2026-11-09)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from futsal.domain.dates import DateHint, describe, parse_date_hint

MADRID = ZoneInfo("Europe/Madrid")
MON = datetime(2026, 11, 9, 10, 0, tzinfo=MADRID)
MORNING, AFTERNOON, NIGHT = (time(8, 0), time(14, 59)), (time(15, 0), time(23, 59)), (time(20, 0), time(23, 59))
NO = (None, None)
D = date


def hint(text: str, now: datetime = MON) -> DateHint | None:
    return parse_date_hint(text, lambda: now)


CASES: list[tuple[str, datetime, tuple[date, date] | None, tuple[time | None, time | None]]] = [
    # --- relativos
    ("hoy", MON, (D(2026, 11, 9), D(2026, 11, 9)), NO),
    ("mañana", MON, (D(2026, 11, 10), D(2026, 11, 10)), NO),
    ("ayer", MON, (D(2026, 11, 8), D(2026, 11, 8)), NO),
    ("pasado mañana", MON, (D(2026, 11, 11), D(2026, 11, 11)), NO),
    ("anteayer", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("hoy y mañana", MON, (D(2026, 11, 9), D(2026, 11, 10)), NO),
    # --- fin de semana
    ("este fin de semana", MON, (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("la jornada de este fin de semana", MON, (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("el próximo fin de semana", MON, (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("el fin de semana pasado", MON, (D(2026, 11, 7), D(2026, 11, 8)), NO),
    ("este fin de semana", datetime(2026, 11, 13, 9, tzinfo=MADRID), (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("este fin de semana", datetime(2026, 11, 14, 9, tzinfo=MADRID), (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("este fin de semana", datetime(2026, 11, 15, 9, tzinfo=MADRID), (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("el próximo fin de semana", datetime(2026, 11, 14, 9, tzinfo=MADRID), (D(2026, 11, 21), D(2026, 11, 22)), NO),
    ("este fin de semana por la tarde", MON, (D(2026, 11, 14), D(2026, 11, 15)), AFTERNOON),
    # --- días de la semana
    ("el sábado", MON, (D(2026, 11, 14), D(2026, 11, 14)), NO),
    ("este sábado", MON, (D(2026, 11, 14), D(2026, 11, 14)), NO),
    ("el próximo domingo", MON, (D(2026, 11, 15), D(2026, 11, 15)), NO),
    ("el lunes", MON, (D(2026, 11, 9), D(2026, 11, 9)), NO),
    ("el próximo lunes", MON, (D(2026, 11, 16), D(2026, 11, 16)), NO),
    ("el lunes pasado", MON, (D(2026, 11, 2), D(2026, 11, 2)), NO),
    ("el sábado y el domingo", MON, (D(2026, 11, 14), D(2026, 11, 15)), NO),
    ("el sábado", datetime(2027, 1, 2, 10, tzinfo=MADRID), (D(2027, 1, 2), D(2027, 1, 2)), NO),
    ("el próximo sábado", datetime(2027, 1, 2, 10, tzinfo=MADRID), (D(2027, 1, 9), D(2027, 1, 9)), NO),
    # --- fechas explícitas
    ("sábado 7 de noviembre", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("Sábado 14 de Noviembre", MON, (D(2026, 11, 14), D(2026, 11, 14)), NO),
    ("7 de noviembre de 2026", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("7/11", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("el 14/11", MON, (D(2026, 11, 14), D(2026, 11, 14)), NO),
    ("7-11-2026", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("7/11/26", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("2026-11-07", MON, (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("sábado 7 de noviembre", datetime(2026, 1, 10, 9, tzinfo=MADRID), (D(2026, 11, 7), D(2026, 11, 7)), NO),
    # --- franjas y la ambigüedad de «mañana»
    ("mañana por la tarde", MON, (D(2026, 11, 10), D(2026, 11, 10)), AFTERNOON),
    ("mañana por la mañana", MON, (D(2026, 11, 10), D(2026, 11, 10)), MORNING),
    ("mañana por la noche", MON, (D(2026, 11, 10), D(2026, 11, 10)), NIGHT),
    ("hoy por la tarde", MON, (D(2026, 11, 9), D(2026, 11, 9)), AFTERNOON),
    ("esta tarde", MON, (D(2026, 11, 9), D(2026, 11, 9)), AFTERNOON),
    ("esta noche", MON, (D(2026, 11, 9), D(2026, 11, 9)), NIGHT),
    ("esta mañana", MON, (D(2026, 11, 9), D(2026, 11, 9)), MORNING),
    ("mañana por la tarde o por la noche", MON, (D(2026, 11, 10), D(2026, 11, 10)), AFTERNOON),
    ("mañana a mediodía", MON, (D(2026, 11, 10), D(2026, 11, 10)), NO),
    ("Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde", MON, (D(2026, 11, 7), D(2026, 11, 7)), AFTERNOON),
    ("7/11 por la mañana", MON, (D(2026, 11, 7), D(2026, 11, 7)), MORNING),
    # --- sin día: no se inventa nada
    ("a mediodía", MON, None, NO),
    ("por la tarde", MON, None, NO),
    ("clasificación de la liga", MON, None, NO),
    ("31/4", MON, None, NO),
    # --- cambio de año
    ("mañana", datetime(2026, 12, 31, 20, tzinfo=MADRID), (D(2027, 1, 1), D(2027, 1, 1)), NO),
    ("ayer", datetime(2027, 1, 1, 9, tzinfo=MADRID), (D(2026, 12, 31), D(2026, 12, 31)), NO),
    ("1/1", datetime(2026, 12, 31, 20, tzinfo=MADRID), (D(2027, 1, 1), D(2027, 1, 1)), NO),
    ("30/12", datetime(2027, 1, 5, 9, tzinfo=MADRID), (D(2026, 12, 30), D(2026, 12, 30)), NO),
    ("7/11", datetime(2027, 1, 5, 9, tzinfo=MADRID), (D(2026, 11, 7), D(2026, 11, 7)), NO),
    ("este fin de semana", datetime(2026, 12, 31, 20, tzinfo=MADRID), (D(2027, 1, 2), D(2027, 1, 3)), NO),
    ("sábado 2 de enero", datetime(2026, 12, 28, 9, tzinfo=MADRID), (D(2027, 1, 2), D(2027, 1, 2)), NO),
    # --- fin de mes
    ("mañana", datetime(2026, 2, 28, 9, tzinfo=MADRID), (D(2026, 3, 1), D(2026, 3, 1)), NO),
    ("mañana", datetime(2028, 2, 28, 9, tzinfo=MADRID), (D(2028, 2, 29), D(2028, 2, 29)), NO),
    ("mañana", datetime(2026, 4, 30, 9, tzinfo=MADRID), (D(2026, 5, 1), D(2026, 5, 1)), NO),
    ("29/2", datetime(2028, 2, 28, 9, tzinfo=MADRID), (D(2028, 2, 29), D(2028, 2, 29)), NO),
    # --- horario de verano/invierno: el reloj en UTC se pasa a la fecha local de Madrid
    ("hoy", datetime(2026, 3, 28, 22, 30, tzinfo=UTC), (D(2026, 3, 28), D(2026, 3, 28)), NO),   # 23:30 CET
    ("hoy", datetime(2026, 3, 28, 23, 30, tzinfo=UTC), (D(2026, 3, 29), D(2026, 3, 29)), NO),   # 00:30 CET (cambio a verano ese día)
    ("mañana", datetime(2026, 3, 29, 21, 30, tzinfo=UTC), (D(2026, 3, 30), D(2026, 3, 30)), NO),  # 23:30 CEST
    ("hoy", datetime(2026, 3, 29, 22, 30, tzinfo=UTC), (D(2026, 3, 30), D(2026, 3, 30)), NO),   # 00:30 CEST
    ("hoy", datetime(2026, 10, 24, 22, 30, tzinfo=UTC), (D(2026, 10, 25), D(2026, 10, 25)), NO),  # 00:30 CEST
    ("hoy", datetime(2026, 10, 25, 22, 30, tzinfo=UTC), (D(2026, 10, 25), D(2026, 10, 25)), NO),  # 23:30 CET (cambio a invierno ese día)
    ("hoy", datetime(2026, 10, 25, 23, 30, tzinfo=UTC), (D(2026, 10, 26), D(2026, 10, 26)), NO),
    ("mañana por la tarde", datetime(2026, 10, 24, 12, tzinfo=UTC), (D(2026, 10, 25), D(2026, 10, 25)), AFTERNOON),
]


@pytest.mark.parametrize(("text", "now", "days", "times"), CASES, ids=[f"{i:02d}-{c[0][:30]}" for i, c in enumerate(CASES)])
def test_date_hint_table(text: str, now: datetime, days: tuple[date, date] | None, times: tuple[time | None, time | None]) -> None:
    got = hint(text, now)
    if days is None:
        assert got is None
        return
    assert got is not None
    assert (got.date_from, got.date_to) == days and (got.time_from, got.time_to) == times and got.phrase


def test_table_has_at_least_forty_cases() -> None:
    assert len(CASES) >= 40


def test_phrase_keeps_the_original_text_and_describe_is_readable() -> None:
    got = hint("Quiero ver el Sábado 7 de Noviembre por la tarde, por favor")
    assert got is not None and got.phrase == "Sábado 7 de Noviembre por la tarde"
    assert describe(got) == "«Sábado 7 de Noviembre por la tarde» = sábado 2026-11-07, de 15:00 a 23:59 (Europe/Madrid)"


def test_default_clock_is_usable() -> None:
    assert parse_date_hint("hoy") is not None
