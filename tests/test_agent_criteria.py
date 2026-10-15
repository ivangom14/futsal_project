"""Criterios anti-invención: deterministas, sin red ni BD."""

from futsal.agent.criteria import (
    states_tool_limit,
    answer_warns,
    card_color_claims,
    clarifies,
    has_any_score,
    mentioned_players,
    needs_warning,
    norm,
    says_not_played,
    substitution_minute_claims,
    unsupported_players,
)

PLAYERS = ["GOMEZ RODRIGUEZ, PABLO", "GOMEZ RODRIGUEZ, DANIEL", "GARCIA GUTIERREZ, JAVIER", "LEON MARTIN, JAIME"]


def test_player_mention_needs_all_name_words_in_one_sentence() -> None:
    assert mentioned_players("Marcó Pablo Gómez Rodríguez al final.", PLAYERS) == ["GOMEZ RODRIGUEZ, PABLO"]
    assert mentioned_players("Gómez Rodríguez, Daniel abrió el marcador", PLAYERS) == ["GOMEZ RODRIGUEZ, DANIEL"]
    assert mentioned_players("Hubo un Gómez. Y un Pablo.", PLAYERS) == []  # palabras en frases distintas
    assert mentioned_players("Javier marcó", PLAYERS) == []  # solo el nombre de pila no identifica


def test_unsupported_player_is_one_not_in_evidence_or_question() -> None:
    evidence = [{"goals": [{"player": "GARCIA GUTIERREZ, JAVIER", "minute": 7}]}]
    ok = unsupported_players("Marcó Javier García Gutiérrez en el minuto 7.", PLAYERS, evidence, "¿quién marcó?")
    assert ok == []
    invented = unsupported_players("Marcó Jaime León Martín.", PLAYERS, evidence, "¿quién marcó?")
    assert invented == ["LEON MARTIN, JAIME"]  # existe en la base pero las tools no lo devolvieron: invención
    asked = unsupported_players("Jaime León Martín no consta.", PLAYERS, [], "¿Cuántos goles marcó Jaime León Martín?")
    assert asked == []  # lo nombró el usuario


def test_scores_are_detected_but_not_dates_or_times() -> None:
    assert has_any_score("Terminó 3-1") and has_any_score("quedó 2 – 0")
    assert not has_any_score("El 10-10-2026 a las 18:30") and not has_any_score("jornada 3 de 26")


def test_card_color_claims_only_when_not_negated() -> None:
    assert card_color_claims("Hubo una tarjeta amarilla para el local.")
    assert not card_color_claims("No puedo decir si fue amarilla o roja: solo consta el código 100.")
    assert not card_color_claims("Hubo 2 tarjetas con código 100 y 1 con código 101.")


def test_substitution_minute_claims() -> None:
    assert substitution_minute_claims("Pablo entró en el minuto 12.")
    assert not substitution_minute_claims("El acta no recoge los minutos de las sustituciones.")
    assert not substitution_minute_claims("Los suplentes fueron Pablo y Daniel.")


def test_not_played_and_clarification_phrases() -> None:
    assert says_not_played("Ese partido aún no se ha jugado, está programado para el 17 de octubre.")
    assert not says_not_played("Ganó el local por goleada.")
    assert clarifies("Hay dos jugadores con ese nombre, ¿te refieres a Pablo o a Daniel?")
    assert not clarifies("Marcó 2 goles.")


def test_warning_is_required_only_when_evidence_is_partial_or_contradictory() -> None:
    assert needs_warning([{"coverage": {"complete": False, "finished_matches": 5}}]) == ["cobertura de actas parcial"]
    assert needs_warning([{"coverage": {"complete": True, "finished_matches": 5}}]) == []
    assert needs_warning([{"report_available": False}]) == ["acta no disponible"]
    assert needs_warning([{"consistency": {"consistent": False, "differences": ["x"]}}]) == [
        "marcador de competición y acta contradictorios"]
    assert needs_warning([{"matches": []}, {"coverage": {"complete": False, "finished_matches": 0}}]) == []


def test_answer_warns_detects_cautions() -> None:
    assert answer_warns("Ojo: la cobertura de actas es parcial, el dato puede estar incompleto.")
    assert answer_warns("El marcador difiere entre la competición y el acta.")
    assert not answer_warns("Marcaron Javier y Jaime.")


def test_norm_strips_accents() -> None:
    assert norm("Gómez, Ñandú!") == "gomez nandu"


def test_negation_in_one_clause_does_not_hide_an_invented_claim_in_another() -> None:
    text = "El acta no recoge los minutos de los cambios, pero Pablo entró en el minuto 12."
    assert substitution_minute_claims(text)
    assert not substitution_minute_claims("El acta no recoge los minutos de los cambios.")
    assert card_color_claims("No consta el color, aunque hubo una tarjeta roja.")


def test_states_tool_limit_needs_a_limitation_without_figures() -> None:
    assert states_tool_limit("Mis herramientas no incluyen el resultado por periodos.")
    assert not states_tool_limit("Las herramientas no incluyen periodos, pero quedó 3-4.")
    assert not states_tool_limit("Aquí tienes un chiste.")
    assert not states_tool_limit("No incluyen el dato; fue en el minuto 12.")
