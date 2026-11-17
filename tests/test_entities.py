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


def test_common_question_words_are_never_corrected_into_a_team_name() -> None:
    """«¿cuántos puntos tiene?» no es una errata de «UNION TRES CANTOS»; «Valdemoru» sí lo es de «Valdemoro»."""
    from futsal.agent.context import ContextEngine
    from futsal.agent.convstate import ConversationState
    from futsal.config.competition_aliases import load_dimensions
    from futsal.domain.competitions import Competition

    comp = Competition(1, "PRIMERA AFICIONADO FS", ((10, "Grupo 1"),))
    cat = TeamCatalog([TeamEntry(n, comp.name, "Grupo 1", "10") for n in
                       ("UNION TRES CANTOS FUTBOL SALA", "UNION TRES CANTOS FUTBOL SALA 'B'", "C.D. VALDEMORO", "SALA NORTE")])
    eng = ContextEngine([comp], load_aliases(), load_dimensions(), cat)
    state = ConversationState()
    for q in ("¿cuántos puntos tiene?", "¿Cuántos goles ha marcado Hugo Ferrándiz Olmedilla?", "¿y cuántos ha encajado?"):
        hit = eng.team_hit(q, state)
        assert not hit.names and not hit.ambiguous, (q, hit)
    assert eng.team_hit("¿cómo va el valdemoru?", state).names == ["C.D. VALDEMORO"]


def test_v2_does_not_ask_again_for_the_competition_of_a_team_whose_scope_was_already_resolved() -> None:
    """«¿En qué posición está X?» → aclaración → «El juvenil» (acota a dos: se pregunta entre ellas) → «cualquiera» (se asume «todas»)
    → «¿cuántos puntos tiene?» no vuelve a preguntar."""
    from futsal.agent.context import ContextEngine
    from futsal.agent.convstate import ConversationState
    from futsal.agent.v2.rules import wants_broad_scope
    from futsal.config.competition_aliases import load_dimensions
    from futsal.domain.competitions import Competition

    comps = [Competition(1, "PREFERENTE JUVENIL FS", ((11, "Grupo 1"),)), Competition(2, "COPA JUVENIL - fase final- FS", ((12, "Eliminatorias"),)),
             Competition(3, "PRIMERA DIVISION AUTONOMICA AFICIONADO FS", ((13, "Grupo 1"),))]
    team = "CDE ATHLETIC MOSTOLES 'B'"
    cat = TeamCatalog([TeamEntry(team, c.name, c.groups[0][1], str(c.groups[0][0])) for c in comps])
    eng = ContextEngine(comps, load_aliases(), load_dimensions(), cat)

    def talk(turns: list[str]) -> list[tuple[str, list[str], list[str]]]:
        state, seen = ConversationState(), []
        for q in turns:
            u = eng.understand(q, q, state, has_history=state.turns > 0, broad_scope=wants_broad_scope(q))  # como el agente
            seen.append((u.status, list(u.options), list(u.competitions)))
            state = eng.next_state(state, u, q, "…")
        return seen

    # posición: la copa (eliminatorias) no tiene clasificación, no se ofrece; «el juvenil» deja solo la liga juvenil
    seen = talk(["¿En qué posición está CDE ATHLETIC MOSTOLES 'B'?", "El juvenil", "¿cuántos puntos tiene?"])
    assert seen[0][0] == "ask" and set(seen[0][1]) == {"PREFERENTE JUVENIL FS", "PRIMERA DIVISION AUTONOMICA AFICIONADO FS"}
    assert seen[1][0] in ("act", "assume") and seen[1][2] == ["PREFERENTE JUVENIL FS"] and seen[2][0] != "ask", seen
    # «¿cuándo juega?» vale también para la copa: «el juvenil» acota a dos, «cualquiera» se resuelve como «todas» y no se vuelve a preguntar
    seen = talk(["¿Cuándo juega CDE ATHLETIC MOSTOLES 'B'?", "El juvenil", "no sé, cualquiera", "¿y el siguiente?"])
    assert seen[0][0] == "ask" and len(seen[0][1]) == 3
    assert seen[1][0] == "ask" and set(seen[1][1]) == {"COPA JUVENIL - fase final- FS", "PREFERENTE JUVENIL FS"}
    assert seen[2][0] in ("assume", "act") and seen[3][0] != "ask", seen


def test_naming_two_teams_is_never_a_which_team_question() -> None:
    """«Compara A (grupo) con B (grupo)» y «el partido A - B»: dos equipos nombrados no son una ambigüedad (bench-127 v4: s7, s5)."""
    from futsal.agent.context import ContextEngine
    from futsal.agent.convstate import ConversationState
    from futsal.config.competition_aliases import load_dimensions
    from futsal.domain.competitions import Competition

    comps = [Competition(1, "PREFERENTE JUVENIL FS", ((11, "Grupo 2"),)), Competition(2, "PRIMERA DIVISION AUTONOMICA AFICIONADO FS", ((21, "Grupo 2"),))]
    cat = TeamCatalog([TeamEntry("TORREJON SALA FIVE PLAY", comps[0].name, "Grupo 2", "11"),
                       TeamEntry("A. D. OBISPO PERELLO 'B'", comps[1].name, "Grupo 2", "21"),
                       TeamEntry("CLUB CORAZONISTAS", comps[1].name, "Grupo 2", "21")])
    eng = ContextEngine(comps, load_aliases(), load_dimensions(), cat)
    q = ("Compara a TORREJON SALA FIVE PLAY (PREFERENTE JUVENIL FS Grupo 2) con A. D. OBISPO PERELLO 'B' "
         "(PRIMERA DIVISION AUTONOMICA AFICIONADO FS Grupo 2): ¿cuántos puntos tiene cada uno?")
    u = eng.understand(q, q, ConversationState())
    assert not (u.status == "ask" and u.ask_about == "team"), u.to_dict()
    q = "¿Quién marcó en el partido CLUB CORAZONISTAS - A. D. OBISPO PERELLO 'B' de la jornada 5?"
    u = eng.understand(q, q, ConversationState())
    assert u.status in ("act", "assume") and u.competitions == [comps[1].name], u.to_dict()


def test_rules_concepts_without_the_word_rule_are_rules_questions() -> None:
    from futsal.agent.criteria import mentions_rules
    from futsal.agent.intent import classify

    for q in ("¿Cuántos tiempos muertos tiene cada equipo?", "¿Cuántos jugadores puede inscribir un club en el acta de la RFFM?",
              "¿Cuándo está en fuera de juego un jugador?"):
        assert classify(q).intent == "rules" and mentions_rules(q), q
    assert classify("¿Cuántos puntos tiene el Club Arganda?").intent == "standings"


def test_the_common_name_of_a_club_is_a_club_query_not_a_which_team_question() -> None:
    """«¿Cómo le ha ido al CLUB ARGANDA FUTSAL en juvenil?» (bench-127 v5): consulta de club; «el Pinto» suelto sigue preguntando."""
    from futsal.agent.context import ContextEngine
    from futsal.agent.convstate import ConversationState
    from futsal.config.competition_aliases import load_dimensions
    from futsal.domain.competitions import Competition

    comps = [Competition(1, "PRIMERA JUVENIL FS", ((11, "Grupo 1"),)), Competition(2, "PRIMERA AFICIONADO FS", ((21, "Grupo 1"),))]
    teams = ["CLUB ARGANDA FUTSAL 'A'", "CLUB ARGANDA FUTSAL 'B'", "CLUB ARGANDA FUTSAL - ALBERO-MUDANZAS SEGOVIANA 'A'", "FUTSAL PINTO", "C.F.S. PINTO"]
    cat = TeamCatalog([TeamEntry(t, comps[i % 2].name, "Grupo 1", str(11 + 10 * (i % 2))) for i, t in enumerate(teams)])
    eng = ContextEngine(comps, load_aliases(), load_dimensions(), cat)
    q = "¿Cómo le ha ido al CLUB ARGANDA FUTSAL en juvenil?"
    u = eng.understand(q, q, ConversationState())
    assert u.status == "act" and u.provenance.get("scope") == "broad" and not u.options, u.to_dict()
    q = "¿Cómo va el Pinto?"
    u = eng.understand(q, q, ConversationState())
    assert u.status == "ask" and u.ask_about == "team", u.to_dict()
