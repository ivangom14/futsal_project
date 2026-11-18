"""Texto VISIBLE de las respuestas (`reconcile.present_answer`): sin duplicar lo que dibuja un visual ni enumerar las opciones de una aclaración,
conservando avisos, análisis y todo lo que el visual no muestra. Puro y determinista: sin LLM."""

from futsal.agent import presentation as pres
from futsal.agent.reconcile import clarification_question, present_answer, sentences

A = "CDB SPORTING LAS AGUILAS DE MADRID - MERCADO LAS AGUILAS 'B'"
B = "C.D.B. MORALZARZAL FUTSAL - TALLERES LOMILLOS BERROCOSA 'A'"
SCORERS = {"type": "bars", "title": "Goleadores · PRIMERA DIVISION AUTONOMICA AFICIONADO FS · Grupo 2", "subtitle": "Tabla actual · captura 2026-11-16",
           "partial": False, "rows": [{"label": "Daniel Martín Ruiz", "sublabel": A, "value": 14, "rank": 1},
                                      {"label": "Álvaro Sanz Gil", "sublabel": B, "value": 12, "rank": 2}]}
COLS = [{"key": k, "label": k, "align": "right"} for k in ("position", "team", "played", "points")]
TABLE = {"type": "table", "title": "Clasificación · PRIMERA DIVISION AUTONOMICA AFICIONADO FS · Grupo 2", "subtitle": "Jornada 10", "partial": False, "columns": COLS,
         "rows": [{"position": 1, "team": B, "played": 10, "points": 25}, {"position": 2, "team": A, "played": 10, "points": 24},
                  {"position": 3, "team": "C.D. DOSA", "played": 10, "points": 23}]}
LINEUP = {"type": "lineup", "title": "Alineación · C.D. DOSA", "subtitle": "C.D. DOSA 3-1 FS ALCALA · Jornada 4", "team": "C.D. DOSA", "court": True,
          "starters": [{"name": "Javier Aparicio Jimenez", "short": "J. Aparicio", "shirt": 1, "captain": False, "goalkeeper": True},
                       {"name": "Pedro Gil Mora", "short": "P. Gil", "shirt": 5, "captain": False, "goalkeeper": False}],
          "substitutes": [{"name": "Sara Reyes Vega", "short": "S. Reyes", "shirt": 13, "captain": False, "goalkeeper": True}], "unknown": [], "note": "…"}
OPTIONS = ["PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "COPA DE LA COMUNIDAD SENIOR FS"]


def test_scorers_paragraph_repeating_the_ranking_is_not_shown() -> None:
    raw = (f"El máximo goleador del Grupo 2 es **Daniel Martín Ruiz** ({A}) con **14 goles**, seguido de **Álvaro Sanz Gil** ({B}) con **12 goles**. "
           "Te muestro el ranking completo a continuación.")
    r = present_answer(raw, [SCORERS])
    assert r.text == "" and r.original == raw and {x["kind"] for x in r.removed} == {"sentence", "intro"}


def test_standings_markdown_table_and_leader_sentence_are_not_shown_twice() -> None:
    raw = ("Esta es la clasificación del Grupo 2 tras la jornada 10:\n\n| Pos | Equipo | PJ | Pts |\n|---|---|---|---|\n"
           + "\n".join(f"| {r['position']} | {r['team']} | 10 | {r['points']} |" for r in TABLE["rows"]) + f"\n\nEl líder es **{B}** con 25 puntos.")
    assert present_answer(raw, [TABLE]).text == ""


def test_warnings_uncertainty_and_analysis_are_kept() -> None:
    raw = (f"El líder es **{B}** con 25 puntos.\n\nOjo: la tabla oficial es de la jornada 10 y aún no incluye los partidos aplazados.\n\n"
           "En mi opinión, el DOSA tiene el calendario más favorable.")
    text = present_answer(raw, [TABLE]).text
    assert "Ojo: la tabla oficial" in text and "En mi opinión" in text and "El líder es" not in text


def test_a_wrong_figure_is_never_hidden() -> None:
    raw = f"El líder es {B} con 27 puntos."  # el visual dice 25: no lo respalda, se conserva
    assert present_answer(raw, [TABLE]).text == raw


def test_a_sentence_depending_on_a_removed_one_keeps_the_paragraph() -> None:
    raw = f"El líder es {B} con 25 puntos. Le saca un punto al segundo tras ganar el derbi."
    assert present_answer(raw, [TABLE]).text == raw


def test_lineup_enumeration_and_its_headings_are_not_shown() -> None:
    raw = ("Alineación del **C.D. DOSA** en la jornada 4:\n\n**Titulares:**\n- 1 Javier Aparicio Jimenez (portero)\n- 5 Pedro Gil Mora\n\n"
           "**Suplentes:**\n- 13 Sara Reyes Vega")
    assert present_answer(raw, [LINEUP]).text == ""


def test_sentence_split_respects_club_abbreviations() -> None:
    assert sentences("El líder es el C.D.B. MORALZARZAL FUTSAL. Le sigue el A.D. ALCORCON F.S. 'C'.") == [
        "El líder es el C.D.B. MORALZARZAL FUTSAL.", "Le sigue el A.D. ALCORCON F.S. 'C'."]


def test_clarification_shows_a_short_question_and_keeps_the_enumeration_in_the_original() -> None:
    raw = "Hay varias competiciones que encajan. ¿A cuál te refieres?\n\n" + "\n".join(f"- {o}" for o in OPTIONS)
    ctx = {"status": "ask", "option_kind": "competition", "options": OPTIONS}
    r = present_answer(raw, [], ctx, clarification=True)
    assert r.text == "¿A qué competición te refieres?" and r.original == raw and r.changed
    team = present_answer(raw, [], {**ctx, "team": "C.D. DOSA"}, clarification=True).text
    assert team == "«C.D. DOSA» juega en varias competiciones. ¿A qué competición te refieres?"


def test_clarification_text_is_kept_when_it_says_something_else_or_is_not_an_ask() -> None:
    ctx = {"status": "ask", "option_kind": "competition", "options": OPTIONS}
    other = "¿Te refieres a la liga o a la copa?"  # no enumera ESAS opciones: no es redundante
    assert present_answer(other, [], ctx, clarification=True).text == other
    assert present_answer("- " + "\n- ".join(OPTIONS), [], ctx, clarification=False).text.startswith("- ")  # sin guardarraíl de aclaración
    assert clarification_question({"status": "ask", "options": OPTIONS[:1]}) is None  # una sola opción no son botones
    assert clarification_question({"status": "act", "options": OPTIONS}) is None
    assert clarification_question({"status": "ask", "option_kind": "group", "options": ["Grupo 1", "Grupo 2"]}) == "¿Qué grupo quieres consultar?"


def test_without_visuals_or_clarification_the_text_is_untouched() -> None:
    raw = "El C.D. DOSA ganó 3-1 al FS ALCALA en la jornada 10."
    assert present_answer(raw, []).text == raw and not present_answer(raw, []).changed


def test_v2_rankings_also_tell_the_model_not_to_repeat_the_card() -> None:
    body = {"items": [{"player": "Daniel Martín Ruiz", "team": A, "goals": 14, "rank": 1}, {"player": "Álvaro Sanz Gil", "team": B, "goals": 12, "rank": 2}],
            "count": 2, "offset": 0}
    note = pres.visual_note("scorers_ranking", {}, body, "máximos goleadores", {})
    assert note and "el ranking de goleadores" in note and "no lo repitas en texto" in note
    assert pres.visual_note("scorers_ranking", {}, {"items": [], "count": 0, "offset": 0}, "x", {}) is None  # sin tarjeta, sin aviso


def test_official_scorers_beyond_ten_travel_to_the_card_with_their_total() -> None:
    from futsal.agent.trace import RunRecord

    scorers = [{"player": f"Jugador {i}", "team": "C.D. DOSA", "goals": 30 - i, "rank": i + 1} for i in range(25)]
    visuals = pres.build_visuals("goleadores", RunRecord(), [{"competition": "X", "group": "Grupo 1", "status": "ok", "scorers": scorers, "source": {}}],
                                 {"teams": {}, "rounds": {}})
    bars = next(v for v in visuals if v["type"] == "bars")
    assert len(bars["rows"]) == 25 and bars["total"] == 25  # antes: 10 filas sin avisar de que había más
