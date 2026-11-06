"""Fase 3A: extractor conservador de equipos. Catálogo REAL de la temporada 22 (fixture) y datasets etiquetados. Sin red, sin LLM, sin PostgreSQL."""

import json
from pathlib import Path

import pytest

from futsal.agent import datasets
from futsal.agent.entities import Decision, TeamCatalog, TeamEntry, decide, normalize
from futsal.agent.entities_eval import CATALOG, SETS, competitions, metrics, run_cases
from futsal.agent.router import resolve_scope
from futsal.config.competition_aliases import load_aliases
from futsal.domain.competitions import Hints

FIXTURES = Path(__file__).parent / "fixtures"
CAT = TeamCatalog.from_json(CATALOG)
ALIASES = load_aliases()
COMPS = competitions(CAT)


def go(question: str, context: bool = False) -> Decision:
    hints, _, _ = resolve_scope(COMPS, ALIASES, [], question)
    return decide(question, CAT, hints, ALIASES, has_context=context)


# --- normalización --------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ("C.D. DOSA", "cd dosa"), ("A. D. OBISPO PERELLO 'B'", "ad obispo perello b"), ("C.D.E. MEJORSALA", "cde mejorsala"),
    ("F.S. DIAGONAL ’A’", "fs diagonal a"), ("AGRUPACION AUTO-ESCUELA SIMANCAS - DYNAVIN 'A'", "agrupacion auto escuela simancas dynavin a"),
    ("  Móstoles   Ñandú  ", "mostoles nandu"), ("DON P'ALPIE ARANJUEZ", "don palpie aranjuez"), ("DON P’ALPIE", "don palpie"), ("TORREJÓN, SALA; FIVE", "torrejon sala five")])
def test_normalization_unifies_quotes_dots_hyphens_spaces_and_case(a: str, b: str) -> None:
    assert normalize(a) == b


def test_normalization_keeps_numbers_and_separates_single_letter_quotes() -> None:
    assert normalize("FUTBOL SALA LIMONES 5 'B'") == "futbol sala limones 5 b" and normalize("SAN FELIPE NERI - ELIPA DRAGONS 017") == "san felipe neri elipa dragons 017"


# --- decisiones sobre el catálogo real -------------------------------------------------------------------------------------------------
def test_fires_on_one_unambiguous_team_in_any_reasonable_spelling() -> None:
    for q in ("¿Cuándo juega A. D. Obispo Perelló B?", "cuando juega ad obispo perello 'b'", "¿Cuándo juega A.D. OBISPO PERELLO ’B’?"):
        d = go(q)
        assert d.fired and d.team == "A. D. OBISPO PERELLO 'B'" and d.reason == "ok", q


def test_abstains_in_every_doubtful_situation() -> None:
    cases = {"¿Quién lidera el grupo?": "no_match", "¿Cómo va el Real Madrid?": "no_match", "Compara el A. D. Obispo Perelló B con el FUTBOL SALA LIMONES 5 'B'": "multi_team",
             "El A. D. OBISPO PERELLO 'B' según el reglamento, ¿cuántas faltas?": "rules", "Resultados del A. D. OBISPO PERELLO 'B' en 2019": "historical",
             "¿Y el A. D. OBISPO PERELLO 'B'?": "context"}
    for q, reason in cases.items():
        d = go(q, context=reason == "context")
        assert not d.fired and d.reason == reason, (q, d)


def test_homonyms_abstain_unless_the_question_states_a_scope_that_leaves_exactly_one() -> None:
    q = "¿Cuál fue el último resultado del C.D. DOSA?"
    d = go(q)
    assert not d.fired and d.reason == "homonym" and d.team == "C.D. DOSA"
    scoped = go("En PRIMERA DIVISION AUTONOMICA AFICIONADO FS (Grupo 2), ¿cuál fue el último resultado del C.D. DOSA?")
    assert scoped.fired and scoped.scoped and scoped.team == "C.D. DOSA"


def test_there_is_no_approximate_or_substring_matching() -> None:
    for q in ("partido de DYNAVIN", "¿Cómo va Parque Norte?", "resultados de ESCURIALENSE", "¿Hay algún equipo de Móstoles?"):
        assert not go(q).fired, q


def test_a_single_word_core_must_be_unique_and_not_generic() -> None:
    cat = TeamCatalog([TeamEntry("C.D. DOSA", "L", "Grupo 1", "g1"), TeamEntry("C.D. SALA", "L", "Grupo 1", "g1"), TeamEntry("C.D.E. SALA NORTE", "L", "Grupo 1", "g1"),
                       TeamEntry("A.D. REAL", "L", "Grupo 1", "g1")])
    assert decide("¿Cómo va el dosa?", cat).fired                     # núcleo de una palabra, único y no genérico
    assert not decide("¿Cómo va la sala?", cat).fired                  # genérica y presente en dos nombres
    assert not decide("¿Cómo va el real?", cat).fired                  # palabra genérica
    assert decide("¿Cómo va el C.D. SALA?", cat).fired                # el nombre COMPLETO sí vale aunque contenga una palabra genérica


def test_two_different_catalog_names_that_normalise_equal_are_never_picked() -> None:
    cat = TeamCatalog([TeamEntry("A.D. ALCORCON 'A'", "L", "G1", "g1"), TeamEntry("AD ALCORCON A", "L", "G2", "g2")])
    d = decide("Resultado del AD Alcorcón A", cat)
    assert not d.fired and d.reason == "ambiguous_form"


# --- datasets: ajuste y holdout A ---------------------------------------------------------------------------------------------------------
def test_dev_set_is_a_regression_floor_not_independent_evidence() -> None:
    m = metrics(run_cases(datasets.load(SETS["dev"]), CAT))
    assert m["false_positive_rate"]["k"] == 0 and m["wrong_team_rate_among_fires"]["k"] == 0 and m["coverage"]["rate"] >= 0.9
    assert m["verdict_false_positive_le_1pct"] == "no concluyente"  # 125 negativos no bastan para sostener el 1 %: no se presenta como cumplido


def test_holdout_a_result_is_reproducible_and_matches_the_recorded_first_run() -> None:
    """A está sellado y el extractor se congeló antes de ejecutarlo. Si este test falla, el extractor cambió DESPUÉS de validarse en A: A ya no es independiente
    (revalidar en el Holdout B, nunca ajustar sobre A)."""
    first = json.loads((FIXTURES / "router" / "holdout_a_entities_first_run.json").read_text(encoding="utf-8"))["metrics"]
    now = metrics(run_cases(datasets.load(SETS["a"]), CAT))
    for key in ("fires", "precision", "false_positive_rate", "wrong_team_rate_among_fires", "coverage", "verdict_false_positive_le_1pct", "verdict_wrong_team_le_1pct"):
        assert now[key] == first[key], key


def test_holdout_a_supports_the_one_percent_target_statistically() -> None:
    m = json.loads((FIXTURES / "router" / "holdout_a_entities_first_run.json").read_text(encoding="utf-8"))["metrics"]
    assert m["negatives"] >= 555 and m["false_positive_rate"]["k"] == 0 and m["false_positive_rate"]["ci95"][1] <= 0.01
    assert m["verdict_false_positive_le_1pct"] == "cumple" and m["fires"] >= 368 and m["verdict_wrong_team_le_1pct"] == "cumple"


def test_every_negative_reason_is_represented_in_holdout_a() -> None:
    reasons = datasets.stats(datasets.load(SETS["a"]))["abstain_reason"]
    assert set(reasons) == set(datasets.ABSTAIN_REASONS) - set() and all(n >= 25 for n in reasons.values())


def test_catalog_fixture_has_real_homonyms() -> None:
    names = {e.name for e in CAT.entries}
    assert len(names) >= 200 and sum(CAT.homonym(n) for n in names) >= 50 and Hints().empty()


def test_standings_exclusion_never_hits_a_question_that_needs_more_than_the_table() -> None:
    """Capa aparte de `decide` (no cambia las métricas selladas de A). En el set de DESARROLLO ninguna pregunta marcada necesita herramientas ajenas a la clasificación."""
    from futsal.agent.entities import standings_only
    loaded = datasets.load(SETS["dev"])
    cases = loaded["cases"] if isinstance(loaded, dict) else loaded
    # los seguimientos («¿Y en qué puesto está?») ya los descarta el agente por contexto antes de llegar aquí
    flagged = [c for c in cases if standings_only(c["question"]) and not c["question"].startswith("¿Y ")]
    table_tools = {"get_standings", "find_teams", "list_competitions", "list_groups", "list_teams"}  # find_teams: sin ámbito dicho, el grupo sale de ahí (el agente lo comprueba)
    assert flagged and all(set(c["needed_tools"]) <= table_tools for c in flagged)
    assert not [c for c in cases if c["intent"] != "standings" and set(c["needed_tools"]) - {"find_teams"} and standings_only(c["question"])
                     and not c["question"].startswith("¿Y ")]
