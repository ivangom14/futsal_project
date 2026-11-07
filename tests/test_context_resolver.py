"""Resolutor por dimensiones, intención y estado conversacional (puros; catálogo real de 14 competiciones del evaluador)."""

import pytest

from futsal.agent.convstate import ConversationState, is_current, turn_hash
from futsal.agent.intent import classify
from futsal.agent.memory import Turn
from futsal.agent.understanding_eval import (
    CID,
    CJF,
    CJP,
    CS1F,
    CS1P,
    GID,
    PAA,
    PAJ,
    PREF_A,
    PREF_J,
    PRI_A,
    PRI_J,
    REAL,
    TER,
    TROF,
)
from futsal.config.competition_aliases import load_aliases, load_dimensions
from futsal.domain.competitions import Competition
from futsal.domain.resolver import Resolver, SeasonScope

COMPS = [Competition(CID[n], n, tuple((GID[(n, g)], g) for g in gs)) for n, gs in REAL.items()]
R = Resolver(COMPS, load_aliases(), load_dimensions())


def names(text: str) -> list[str]:
    return [c.name for c in R.match(R.parse(text))]


@pytest.mark.parametrize("text,expected", [
    ("clasificación de la preferente juvenil", [PREF_J]), ("preferente senior", [PREF_A]), ("preferente aficionado", [PREF_A]),  # intersección, no unión
    ("la preferente", [PREF_A, PREF_J]), ("primera juvenil", [PRI_J]), ("primera autonómica", [PAA, PAJ]), ("tercera", [TER]), ("tercera división grupo 3", [TER]),
    ("regional", [PRI_A]), ("regional juvenil", [PRI_J]), ("copa juvenil", [CJF, CJP]), ("copa senior I", [CS1F, CS1P]), ("trofeo comunidad", [TROF]),
    ("honor juvenil", [TROF]), ("aficionados", [PAA, PREF_A, PRI_A]), ("prefernte juvenil", [PREF_J]), ("PRIMERA DIVISION AUTONOMICA AFICIONADO FS", [PAA])])
def test_candidates_are_the_intersection_of_the_stated_dimensions(text: str, expected: list[str]) -> None:
    assert names(text) == expected


def test_bare_primera_is_never_confused_with_autonomica_and_tercero_is_not_tercera() -> None:
    assert names("primera") == [PRI_A, PRI_J]
    assert R.parse("¿quién va tercero?").level == frozenset()  # una posición, no la Tercera División


def test_missing_terms_and_seasons_are_detected_not_guessed() -> None:
    assert R.parse("la segunda división").absent == ("segunda division",)
    assert R.parse("división de honor").absent and "honor" not in R.parse("división de honor").level
    assert R.parse("la tercera b").absent == ("tercera b",)
    for text in ("el año pasado", "temporada 2023/24", "hace dos años", "la temporada anterior"):
        assert R.parse(text).season == "previous"
    assert not SeasonScope("previous").satisfiable and SeasonScope().satisfiable
    assert SeasonScope("2024", ("current", "2024")).satisfiable  # arquitectura lista para históricos


def test_families_siblings_and_other_dimensions() -> None:
    assert [len(f) for f in R.families(R.match(R.parse("copa")))] == [2, 2, 2]  # I, II y juvenil; cada una con sus fases
    assert [c.name for c in R.siblings([COMPS[10]], "senior")] == [PREF_A]  # Preferente Juvenil → Preferente Aficionado
    assert R.parse("grupo 2").group == "grupo 2" and R.parse("la otra liga").other and R.parse("ese equipo").pronoun and R.parse("su partido").possessive


@pytest.mark.parametrize("text,intent", [
    ("¿Cómo va la liga?", "standings"), ("¿Quién está arriba?", "standings"), ("¿Quién va último?", "standings"), ("¿Cómo quedó el otro día?", "last_result"),
    ("¿Cuándo vuelve a jugar?", "next_match"), ("¿Quién lleva más goles?", "scorers"), ("¿Cuántos puntos tiene DOSA?", "standings"), ("¿Quién ha ganado más partidos?", "stats"),
    ("¿Y los partidos de este fin de semana?", "matches"), ("Equipos de la copa", "teams_list"), ("¿Cuánto dura cada mitad?", "rules"), ("¿Quién manda en la tercera?", "standings")])
def test_intent_is_scored_from_weighted_features(text: str, intent: str) -> None:
    assert classify(text).intent == intent


def test_only_an_elliptical_follow_up_inherits_the_previous_intent() -> None:
    assert classify("¿Y la copa?", "scorers") == classify("¿Y la copa?", "scorers") and classify("¿Y la copa?", "scorers").inherited
    assert classify("Cuéntame un chiste", "scorers").intent is None  # un cambio de tema no hereda
    assert classify("¿Quién va último?").sub == "bottom" and classify("¿Quién está arriba?").sub == "top"


def test_state_roundtrip_and_coherence_with_history() -> None:
    s = ConversationState(competitions=[PREF_J], groups=["Grupo 2"], age="juvenil", intent="standings")
    s.remember_team("C.D. DOSA")
    t = Turn("¿Cómo va la preferente juvenil?", "Respuesta")
    s.last_turn = turn_hash(t.question, t.answer)
    assert ConversationState.from_dict(s.to_dict()) == s
    assert is_current(s, [t]) and not is_current(s, [Turn("otra", "cosa")]) and not is_current(s, [])  # sin historial no hay conversación
    assert ConversationState.from_dict({"version": 99}) is None and ConversationState.from_dict("x") is None
