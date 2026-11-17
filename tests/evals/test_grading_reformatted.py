"""Calificador ante respuestas reales de bench-127 v4 (2026-11-17): siglas, abreviaturas, nombres unidos con «y», apellidos que parecen
colores de tarjeta, listados paginados y victorias contadas con los partidos. Lo inventado se sigue detectando."""

from futsal.agent.criteria import card_color_claims, unsupported_card_claims
from futsal.agent.evaluate import presentation_ok
from futsal.evals.grading import grade_turn, unknown_names
from futsal.evals.selector import ResolvedTurn

ROWS = [{"items": [{"comp": "TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS · Eliminatorias", "home": "AGRUPACION DEPORTIVA NAVALAFUENTE",
                    "away": "CLUB ESCUELA DEPORTIVA MANILA-ESTRELLAS 'A'", "score": "1-3"}]}]


def crit(answer: str, payloads: list = ROWS) -> list[str]:  # type: ignore[type-arg]
    return unknown_names(ResolvedTurn("¿Qué partidos ha arbitrado?", "answer"), answer, payloads, [])[0]


def test_initials_abbreviations_and_truncated_words_are_the_same_name() -> None:
    assert crit("1. **Trofeo Comunidad Div. Honor Juvenil FS** (Eliminatorias)") == []
    assert crit("Pitó el AD Navalafuente – Club Escuela Deportiva Manila-Est.") == []


def test_invented_names_are_still_critical() -> None:
    assert crit("Pitó el AD Torrelodones – Club Escuela Deportiva Manila-Estrellas.")  # Torrelodones no está en la evidencia
    scorers = [{"rows": [{"player": "BOULBARS, EL HASSAN"}, {"player": "BEN CHAKHTIR, AYMAN"}]}]
    assert crit("Le siguen El Hassan Boulbars y Ayman Ben Chakhtir, con 4.", scorers) == []
    assert crit("Le siguen Raúl Boulbars y Ayman Ben Chakhtir, con 4.", scorers)  # «Raúl» no es su nombre


def test_a_capitalised_surname_is_not_a_card_colour() -> None:
    assert card_color_claims("- Marcos Rojas Sánchez, min. 12") == [] and unsupported_card_claims("- Marcos Rojas Sánchez, min. 12", []) == []
    assert card_color_claims("Vio la tarjeta roja en el 30.") and card_color_claims("Roja directa para Pablo.")


def test_a_page_of_ten_rows_has_room_but_long_prose_does_not() -> None:
    page = "Mostrando 1–10 de 48:\n" + "\n".join(f"{i}. **18:30** · PRIMERA AFICIONADO FS, Grupo 2: Agrupación Deportiva Local Número {i} – Club Deportivo Elemental Visitante {i}. Finalizado, 3-1; acta disponible."
                                               for i in range(1, 11))
    assert len(page) > 1500 and presentation_ok(page)
    assert not presentation_ok("Texto largo sin filas. " * 80)


def test_wins_counted_from_the_matches_are_correct_when_they_match_the_official_table() -> None:
    turn = ResolvedTurn("¿Cuántos partidos ha ganado RACING LA PALOMA 'B'?", "answer", facts=[{"kind": "number", "value": 3, "label": "ganados"}],
                        tools_needed=["get_standings"])
    ok = grade_turn(turn, "RACING LA PALOMA 'B' ha ganado 3 partidos.", ["list_teams", "list_matches"], [], 2, [])
    assert ok.verdict == "correct" and ok.tools_missing == []
    wrong = grade_turn(turn, "RACING LA PALOMA 'B' ha ganado 2 partidos.", ["list_teams", "list_matches"], [], 2, [])
    assert wrong.verdict != "correct" and wrong.tools_missing == ["get_standings"]


def test_v6_wording_spelled_numbers_away_side_last_place_and_typos() -> None:
    """bench-127 v6: «hay dos partidos», «victoria por 7-0… es de visitante», «es último», «YouFirst Smart Finance» (fuente «FINACE»)."""
    two = ResolvedTurn("¿Qué partidos hay el sábado por la tarde?", "answer", facts=[{"kind": "number", "value": 2, "label": "partidos"}])
    assert grade_turn(two, "El sábado hay dos partidos por la tarde.", ["matches_by_date"], [], 2, []).verdict == "correct"
    away = ResolvedTurn("¿Último resultado de COLEGIO CLARET?", "answer", facts=[{"kind": "score", "value": "0-7"}])
    ok = "Fue una victoria por 7-0 frente a CDE AMET. Fue en la jornada 4 y el partido es de visitante."
    match = [{"matches": [{"home": "CDE AMET - AGUIRRE", "away": "COLEGIO CLARET (FS)", "score": "0-7"}]}]
    assert grade_turn(away, ok, ["list_matches"], match, 2, []).verdict == "correct"
    assert grade_turn(away, "Fue una derrota por 7-0 frente a CDE AMET, como visitante.", ["list_matches"], match, 2, []).verdict != "correct"
    team = "C.D. EUROPEO 'B'"
    last = ResolvedTurn(f"¿Cómo va {team}?", "answer", facts=[{"kind": "position", "value": 13, "team": team, "label": "posición"}])
    table = [{"rows": [{"position": p, "team": f"EQUIPO {p}"} for p in range(1, 14)]}]
    assert grade_turn(last, f"{team} es último del grupo, con 0 puntos.", ["get_standings"], table, 2, []).verdict == "correct"
    assert grade_turn(last, f"{team} es último del grupo.", ["get_standings"], table[:1] and [{"rows": table[0]["rows"] + [{"position": 14}]}], 2,
                      []).verdict != "correct"  # si hay un puesto 14, «último» no es el 13
    rows = [{"items": [{"home": "CD RIVAS FUTSAL - YOUFIRST SMART FINACE", "away": "CLUB DEPORCOSLADA F.S. 'B'"}]}]
    assert crit("Juegan CD Rivas Futsal - YouFirst Smart Finance y Club Deportcoslada F.S.", rows) == []
