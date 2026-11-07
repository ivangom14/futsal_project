"""Conciliación de la respuesta visible con los visuales: elemento a elemento, con datos estructurados y prefiriendo conservar el texto ante la duda.
Sin LLM real, red ni base de datos."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent import reconcile
from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import ConversationMemory
from futsal.agent.reconcile import MAX_SNIPPET, kickoff, madrid, reconcile_answer
from futsal.agent.web import AgentRunner, create_chat_app

NEXT = {"type": "matches", "title": "Próximos partidos · PRIMERA DIVISION AUTONOMICA AFICIONADO FS · Grupo 2", "subtitle": None, "total": 4, "partial": False, "rows": [
    {"round": 7, "date": "2026-11-07T10:00:00+00:00", "home": "PARQUE NORTE F.S. - RODILLITO 'B'", "away": "C.D. DOSA", "score": None, "status": "scheduled", "past_due": False},
    {"round": 7, "date": "2026-11-08T10:00:00+00:00", "home": "AGRUPACION COLEGIO CALASANCIO 'A'", "away": "SAN FELIPE NERI - ELIPA DRAGONS 017", "score": None, "status": "scheduled", "past_due": False},
    {"round": 7, "date": "2026-11-08T14:30:00+00:00", "home": "AGRUPACION AUTO-ESCUELA SIMANCAS 'B'", "away": "A. D. OBISPO PERELLO 'B'", "score": None, "status": "scheduled", "past_due": False},
    {"round": 7, "date": "2026-11-08T16:45:00+00:00", "home": "C.D. LOPE DE VEGA 'B'", "away": "ESC. DEP. DE FUTBOL DE TORRES", "score": None, "status": "scheduled", "past_due": False}]}
LISTING = """Estos son los próximos partidos del grupo:

- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00)
- AGRUPACION COLEGIO CALASANCIO 'A' vs SAN FELIPE NERI - ELIPA DRAGONS 017 (08/11/2026 a las 11:00)
- AGRUPACION AUTO-ESCUELA SIMANCAS 'B' vs A. D. OBISPO PERELLO 'B' (08/11/2026 a las 15:30)
- C.D. LOPE DE VEGA 'B' vs ESC. DEP. DE FUTBOL DE TORRES (08/11/2026 a las 17:45)

¡Disfruta de la jornada!"""


def lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


# --- Europe/Madrid ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("iso", "expected"), [
    ("2026-11-08T10:00:00+00:00", "2026-11-08 11:00"),   # invierno: UTC+1
    ("2026-07-15T10:00:00+00:00", "2026-07-15 12:00"),   # verano: UTC+2
    ("2026-03-29T00:59:00+00:00", "2026-03-29 01:59"),   # justo antes del cambio de marzo
    ("2026-03-29T01:00:00+00:00", "2026-03-29 03:00"),   # cambio a horario de verano (01:00 UTC)
    ("2026-10-25T00:59:00+00:00", "2026-10-25 02:59"),   # justo antes de volver al invierno
    ("2026-10-25T01:00:00+00:00", "2026-10-25 02:00"),   # vuelta al invierno
    ("2026-11-08T11:00:00+01:00", "2026-11-08 11:00"),   # ya viene en hora de Madrid
    ("2026-11-08T23:30:00+00:00", "2026-11-09 00:30"),   # cruza la medianoche
])
def test_kickoff_is_converted_to_europe_madrid(iso: str, expected: str) -> None:
    d, t = kickoff(iso)  # type: ignore[misc]
    assert f"{d} {t:%H:%M}" == expected


def test_date_only_has_no_time_and_naive_datetimes_are_local_and_garbage_is_none() -> None:
    assert kickoff("2026-11-08") == (date(2026, 11, 8), None)
    d, t = kickoff("2026-11-08T11:00:00")  # type: ignore[misc]
    assert (str(d), f"{t:%H:%M}") == ("2026-11-08", "11:00")
    assert kickoff(None) is None and kickoff("") is None and kickoff("no es una fecha") is None
    assert madrid(datetime(2026, 11, 8, 10, 0, tzinfo=UTC)).hour == 11


# --- lista totalmente redundante ----------------------------------------------------------------------------------------------------------
def test_a_fully_redundant_list_is_removed_but_intro_and_conclusion_are_kept() -> None:
    r = reconcile_answer(LISTING, [NEXT])
    assert lines(r.text) == ["Estos son los próximos partidos del grupo:", "¡Disfruta de la jornada!"]
    assert r.original == LISTING and r.changed and (r.considered, len(r.removed)) == (4, 4)


def test_the_time_in_text_must_match_the_madrid_time_of_the_visual() -> None:
    utc_hour = LISTING.replace("a las 11:00)\n- AGRUP", "a las 10:00)\n- AGRUP", 1)  # la hora UTC en vez de la de Madrid: dato distinto => se conserva
    r = reconcile_answer(utc_hour, [NEXT])
    assert any("10:00" in ln for ln in lines(r.text)) and len(r.removed) == 3


# --- A: elemento por elemento, también con listas parcialmente redundantes -----------------------------------------------------------------
def test_partial_lists_keep_exactly_the_items_the_visual_does_not_back() -> None:
    visual = {**NEXT, "partial": True, "rows": NEXT["rows"][:2]}  # el visual solo muestra 2 de los 4 partidos
    r = reconcile_answer(LISTING, [visual])
    kept = [ln for ln in lines(r.text) if ln.startswith("- ")]
    assert len(kept) == 2 and "SIMANCAS" in kept[0] and "LOPE DE VEGA" in kept[1] and len(r.removed) == 2


def test_one_item_with_extra_information_is_kept_while_the_rest_goes() -> None:
    text = LISTING.replace("(08/11/2026 a las 15:30)", "(08/11/2026 a las 15:30, pista cubierta)")
    r = reconcile_answer(text, [NEXT])
    kept = [ln for ln in lines(r.text) if ln.startswith("- ")]
    assert len(kept) == 1 and "pista cubierta" in kept[0] and len(r.removed) == 3
    assert r.kept_uncertain and "pista" in r.kept_uncertain[0]["left"]


def test_a_match_the_visual_does_not_have_is_kept_even_if_all_the_others_are_redundant() -> None:
    text = LISTING.replace("C.D. LOPE DE VEGA 'B' vs ESC. DEP. DE FUTBOL DE TORRES", "C.D. OTRO EQUIPO vs ESC. DEP. DE FUTBOL DE TORRES")
    r = reconcile_answer(text, [NEXT])
    assert [ln for ln in lines(r.text) if ln.startswith("- ")] == ["- C.D. OTRO EQUIPO vs ESC. DEP. DE FUTBOL DE TORRES (08/11/2026 a las 17:45)"]


# --- B: seguridad frente a concisión ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00) y el DOSA llega en mal momento",   # opinión añadida
    "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (08/11/2026 a las 11:00)",                                 # fecha equivocada
    "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 12:00)",                                 # hora equivocada
    "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00) resultado 3-1",                   # marcador que el visual no tiene
    "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA, jornada 8",                                               # jornada distinta
    "- PARQUE NORTE vs DOSA (07/11/2026 a las 11:00)",                                                           # nombres abreviados: no hay certeza
    "- El DOSA visita al PARQUE NORTE F.S. - RODILLITO 'B' el sábado",                                          # frase libre con verbo
])
def test_when_there_is_not_full_certainty_the_text_is_kept(bad: str) -> None:
    r = reconcile_answer(f"Intro.\n\n{bad}\n", [NEXT])
    assert bad in r.text and not r.removed


def test_a_time_in_the_text_is_kept_when_the_visual_has_no_time() -> None:
    visual = {**NEXT, "rows": [{**NEXT["rows"][0], "date": "2026-11-07"}]}  # solo fecha: la tarjeta no puede mostrar hora
    r = reconcile_answer("Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00)\n", [visual])
    assert "11:00" in r.text and not r.removed
    r2 = reconcile_answer("Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026)\n", [visual])
    assert not r2.removed or "11:00" not in r2.text


@pytest.mark.parametrize("variant", ["(07/11/2026 a las 11:00)", "el 7 de noviembre de 2026 a las 11:00", "sábado 07/11 a las 11:00", "(07/11/26 11:00)", "(sábado, 07/11/2026 - 11:00 h)"])
def test_dates_are_compared_canonically_not_as_strings(variant: str) -> None:
    r = reconcile_answer(f"Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA {variant}\n", [NEXT])
    assert len(r.removed) == 1, variant


# --- los distintos tipos de visual ------------------------------------------------------------------------------------------------------------
RESULTS = {"type": "matches", "title": "Resultados · Grupo 2", "subtitle": None, "total": 2, "partial": False, "rows": [
    {"round": 6, "date": "2026-11-01T11:00:00+00:00", "home": "C.D. DOSA", "away": "F.S. DIAGONAL 'A'", "score": "7-1", "status": "finished", "past_due": False},
    {"round": 6, "date": "2026-11-01T12:30:00+00:00", "home": "CLUB X", "away": "CLUB Y", "score": "0-0", "status": "finished", "past_due": False}]}
TABLE = {"type": "table", "title": "Clasificación · Grupo 2", "subtitle": "Jornada 7", "partial": False, "columns": [], "rows": [
    {"position": 1, "team": "PARQUE NORTE F.S. - RODILLITO 'B'", "played": 7, "won": 6, "drawn": 1, "lost": 0, "goals_for": 30, "goals_against": 10, "goal_difference": 20, "points": 19},
    {"position": 2, "team": "C.D. DOSA", "played": 7, "won": 5, "drawn": 1, "lost": 1, "goals_for": 28, "goals_against": 12, "goal_difference": 16, "points": 16}]}
BARS = {"type": "bars", "title": "Goleadores · Grupo 2", "subtitle": None, "partial": False, "rows": [
    {"label": "ZAPATA RUDAS, SANTIAGO", "sublabel": "C.D. DOSA", "value": 12, "rank": 1}, {"label": "PEREZ LOPEZ, ANA", "sublabel": "CLUB X", "value": 9, "rank": 2}]}
FORM = {"type": "form", "title": "Forma · C.D. DOSA", "subtitle": None, "rows": [
    {"round": 5, "rival": "CLUB CORAZONISTAS 'A'", "score": "1-7", "result": "D", "points": 0, "home": False}, {"round": 6, "rival": "F.S. DIAGONAL 'A'", "score": "7-1", "result": "V", "points": 3, "home": True}]}
LINEUP = {"type": "lineup", "title": "Alineación · C.D. DOSA", "subtitle": None, "team": "C.D. DOSA", "court": True, "note": "", "unknown": [],
          "starters": [{"name": "Santiago Zapata Rudas", "short": "S. Zapata", "shirt": 22, "captain": False, "goalkeeper": True},
                       {"name": "Alvaro Dieguez Sanchez", "short": "A. Dieguez", "shirt": 7, "captain": False, "goalkeeper": False}],
          "substitutes": [{"name": "Hader Ibarra De La Torre", "short": "H. Ibarra", "shirt": 3, "captain": False, "goalkeeper": False}]}
OFFICIALS = {"type": "officials", "title": "Árbitros", "subtitle": None, "people": [{"name": "GARCIA RUIZ, LUIS", "role": "Árbitro principal", "kind": "referee"},
                                                                                       {"name": "MORA DIAZ, SARA", "role": "Cronometrador", "kind": "table"}]}
TIMELINE = {"type": "timeline", "title": "Goles", "subtitle": None, "home": "C.D. DOSA", "away": "CLUB X", "partial": False, "events": [
    {"minute": 12, "player": "ZAPATA RUDAS, SANTIAGO", "side": "home", "own_goal": False}, {"minute": 30, "player": "PEREZ LOPEZ, ANA", "side": "away", "own_goal": False}]}
CARDS = {"type": "cards", "title": "Tarjetas", "subtitle": None, "home": "C.D. DOSA", "away": "CLUB X", "partial": False, "summary": {}, "events": [
    {"minute": 20, "player": "ZAPATA RUDAS, SANTIAGO", "team": "C.D. DOSA", "side": "home", "kind": "yellow", "round": None}]}


@pytest.mark.parametrize(("visual", "redundant", "kept"), [
    (RESULTS, ["C.D. DOSA 7-1 F.S. DIAGONAL 'A' (01/11/2026)", "CLUB X 0-0 CLUB Y (jornada 6)"], ["C.D. DOSA 7-1 F.S. DIAGONAL 'A' (01/11/2026), ganó con claridad", "C.D. DOSA 6-1 F.S. DIAGONAL 'A'"]),
    (TABLE, ["1. PARQUE NORTE F.S. - RODILLITO 'B' - 19 puntos", "C.D. DOSA (16 pts)", "2º C.D. DOSA, 16 puntos"], ["1. PARQUE NORTE F.S. - RODILLITO 'B' - 20 puntos", "1. C.D. DOSA - 16 puntos", "PARQUE NORTE F.S. - RODILLITO 'B' es el líder"]),
    (BARS, ["1. ZAPATA RUDAS, SANTIAGO (C.D. DOSA) - 12 goles", "PEREZ LOPEZ, ANA - 9 goles"], ["ZAPATA RUDAS, SANTIAGO - 13 goles", "1. ZAPATA RUDAS, SANTIAGO (CLUB X) - 12 goles"]),
    (FORM, ["Jornada 6: F.S. DIAGONAL 'A' 7-1", "CLUB CORAZONISTAS 'A' (1-7)"], ["Jornada 6: F.S. DIAGONAL 'A' 1-7", "F.S. DIAGONAL 'A' 7-1 y 3 puntos de 3 posibles"]),
    (LINEUP, ["C.D. DOSA: ZAPATA RUDAS, SANTIAGO (P), DIEGUEZ SANCHEZ, ALVARO (Titulares); IBARRA DE LA TORRE, HADER (Suplentes)."],
     ["C.D. DOSA: ZAPATA RUDAS, SANTIAGO (P), GOMEZ CARRETERO, IVAN (Titulares)", "C.D. DOSA: ZAPATA RUDAS, SANTIAGO (P) lesionado"]),
    (OFFICIALS, ["Árbitro principal: GARCIA RUIZ, LUIS", "Cronometrador: MORA DIAZ, SARA"], ["Árbitro principal: GARCIA RUIZ, LUIS (colegio madrileño)", "Árbitro: PEREZ LOPEZ, ANA"]),
    (TIMELINE, ["Min. 12: ZAPATA RUDAS, SANTIAGO", "30' PEREZ LOPEZ, ANA (gol)"], ["Min. 13: ZAPATA RUDAS, SANTIAGO", "Min. 12: ZAPATA RUDAS, SANTIAGO de penalti"]),
    (CARDS, ["Tarjeta amarilla a ZAPATA RUDAS, SANTIAGO (C.D. DOSA) en el minuto 20"], ["Tarjeta roja a ZAPATA RUDAS, SANTIAGO (C.D. DOSA) en el minuto 20", "Tarjeta amarilla a ZAPATA RUDAS, SANTIAGO en el minuto 21"]),
], ids=["resultados", "clasificacion", "goleadores", "forma", "alineacion", "arbitros", "goles", "tarjetas"])
def test_every_visual_type_removes_what_it_shows_and_keeps_what_it_does_not(visual: dict[str, Any], redundant: list[str], kept: list[str]) -> None:
    for item in redundant:
        r = reconcile_answer(f"Intro.\n\n- {item}\n\nConclusión.", [visual])
        assert item not in r.text and "Intro." in r.text and "Conclusión." in r.text, f"debería omitirse: {item}"
    for item in kept:
        r = reconcile_answer(f"Intro.\n\n- {item}\n\nConclusión.", [visual])
        assert item in r.text, f"debería conservarse: {item}"


def test_markdown_tables_are_judged_row_by_row_and_header_goes_only_when_all_rows_do() -> None:
    full = "Clasificación:\n\n| Pos | Equipo | Pts |\n|---|---|---|\n| 1 | PARQUE NORTE F.S. - RODILLITO 'B' | 19 puntos |\n| 2 | C.D. DOSA | 16 puntos |\n\nGran liga."
    r = reconcile_answer(full, [TABLE])
    assert "|" not in r.text and "Gran liga." in r.text
    mixed = full.replace("| 2 | C.D. DOSA | 16 puntos |", "| 2 | C.D. DOSA | 16 puntos y racha de 5 victorias |")
    r = reconcile_answer(mixed, [TABLE])
    assert "| Pos | Equipo | Pts |" in r.text and "racha de 5 victorias" in r.text and "PARQUE NORTE" not in r.text


def test_ordered_list_numbers_are_syntax_unless_the_visual_has_positions() -> None:
    r = reconcile_answer("Intro.\n\n1. C.D. DOSA - 16 puntos\n2. PARQUE NORTE F.S. - RODILLITO 'B' - 19 puntos\n", [TABLE])
    assert "C.D. DOSA" in r.text and "PARQUE NORTE" in r.text  # la numeración (1., 2.) contradice las posiciones de la tabla: se conserva
    m = reconcile_answer("Intro.\n\n1. PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00)\n", [NEXT])
    assert len(m.removed) == 1  # en una lista de partidos la numeración no es un dato


# --- límites y seguridad ----------------------------------------------------------------------------------------------------------------------
def test_without_visuals_or_with_unreconcilable_ones_the_text_is_untouched() -> None:
    assert reconcile_answer(LISTING, []).text == LISTING
    comparison = {"type": "comparison", "title": "A vs B", "subtitle": None, "series": ["A", "B"], "rows": [{"label": "Puntos", "values": [1, 2]}]}
    assert reconcile_answer(LISTING, [comparison]).text == LISTING
    assert reconcile_answer("", [NEXT]).text == "" and reconcile_answer("Sin listas, solo prosa.", [NEXT]).text == "Sin listas, solo prosa."


def test_prose_is_never_removed_even_if_it_repeats_a_row() -> None:
    prose = "El PARQUE NORTE F.S. - RODILLITO 'B' juega contra C.D. DOSA el 07/11/2026 a las 11:00."
    assert reconcile_answer(prose, [NEXT]).text == prose


def test_if_nothing_is_left_a_short_pointer_to_the_visual_replaces_the_text() -> None:
    only_list = "\n".join(ln for ln in LISTING.splitlines() if ln.startswith("- "))
    r = reconcile_answer(only_list, [NEXT])
    assert r.pointer_added and r.text.startswith("Te lo muestro en la tarjeta «Próximos partidos") and r.audit()["pointer_added"] is True


def test_code_blocks_are_not_touched() -> None:
    text = "Intro.\n\n```\n- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (07/11/2026 a las 11:00)\n```\n"
    assert reconcile_answer(text, [NEXT]).text == text


def test_markdown_formatting_does_not_hide_a_redundant_item() -> None:
    r = reconcile_answer("Intro.\n\n- **PARQUE NORTE F.S. - RODILLITO 'B'** vs *C.D. DOSA* (07/11/2026 a las 11:00)\n", [NEXT])
    assert len(r.removed) == 1


def test_wrapped_list_items_are_judged_as_one_item() -> None:
    r = reconcile_answer("Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' vs\n  C.D. DOSA (07/11/2026 a las 11:00)\n- Nota: llevar balón\n", [NEXT])
    assert "PARQUE" not in r.text and "llevar balón" in r.text


def test_reconciliation_is_idempotent() -> None:
    cases = [(LISTING, [NEXT]), ("\n".join(ln for ln in LISTING.splitlines() if ln.startswith("- ")), [NEXT]),
             ("Intro.\n\n| Pos | Equipo | Pts |\n|---|---|---|\n| 1 | C.D. DOSA | 16 puntos |\n\nFin.", [TABLE]),
             ("x\n\n- C.D. DOSA: ZAPATA RUDAS, SANTIAGO (P) lesionado\n- IBARRA DE LA TORRE, HADER\n", [LINEUP])]
    for text, visuals in cases:
        once = reconcile_answer(text, visuals).text
        assert reconcile_answer(once, visuals).text == once


def test_the_audit_records_counts_and_reasons_without_storing_full_answers() -> None:
    long_item = "- " + "AGRUPACION COLEGIO CALASANCIO 'A' vs SAN FELIPE NERI - ELIPA DRAGONS 017 (08/11/2026 a las 11:00) " + "y mucho texto de opinión " * 20
    r = reconcile_answer(f"Intro.\n\n{long_item}\n{LISTING.splitlines()[2]}\n", [NEXT])
    audit = r.audit()
    assert audit["considered"] == 2 and audit["removed"] == 1 and audit["kept"] == 1
    assert all(len(i["snippet"]) <= MAX_SNIPPET + 1 and i["reason"] and i["kind"] == "list_item" for i in audit["removed_items"] + audit["kept_uncertain"])
    assert json.dumps(audit).count("opinión") < 2 and "y mucho texto de opinión " * 5 not in json.dumps(audit)  # nunca la respuesta completa


def test_several_visuals_are_considered_and_an_item_is_removed_if_any_backs_it_completely() -> None:
    r = reconcile_answer("Intro.\n\n- C.D. DOSA 7-1 F.S. DIAGONAL 'A'\n1. PARQUE NORTE F.S. - RODILLITO 'B' - 19 puntos\n", [RESULTS, TABLE])
    assert "DIAGONAL" not in r.text and "PARQUE NORTE" not in r.text


# --- separación entre respuesta original y visible (web) --------------------------------------------------------------------------------
class FakeMcp:
    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("list_competitions", "list_matches")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name == "list_competitions":
            return False, '{"competitions":[],"count":0}'
        matches = [{"id": i, "home_team": r["home"], "away_team": r["away"], "date": r["date"], "round_number": 7, "status": "scheduled"} for i, r in enumerate(NEXT["rows"])]
        return False, json.dumps({"matches": matches, "count": 4})


class Script:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps = list(steps)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        return self.steps.pop(0)


def web_client(memory: ConversationMemory) -> TestClient:
    steps = [LLMResponse("", [ToolCall("c1", "list_matches", {"group_id": 1, "status": "scheduled"})]), LLMResponse(LISTING, [])]

    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Script(steps), FakeMcp(), 3, trace_results=True)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}, memory=memory))


def test_only_the_visible_answer_changes_and_memory_keeps_the_original() -> None:
    memory = ConversationMemory()
    with web_client(memory) as c:
        body = c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?", "conversation_id": "conv-reconcile-1"}).json()
    assert [v["type"] for v in body["visuals"]] == ["matches"]
    assert "AGRUPACION" not in body["answer"] and "Estos son los próximos partidos" in body["answer"] and "¡Disfruta de la jornada!" in body["answer"]
    stored = memory.get("conv-reconcile-1")
    assert stored and stored[-1].answer == LISTING  # el historial que verá el LLM en la siguiente pregunta es el original completo
    rec = body["diagnostics"]["reconciliation"]
    assert rec["considered"] == 4 and rec["removed"] == 4 and rec["removed_items"][0]["reason"].startswith("matches:")
    assert "text" not in json.dumps(rec).lower().replace("texto", "")  # sin campo con la respuesta completa


def test_reconciliation_is_skipped_without_visuals_and_leaves_no_audit() -> None:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Script([LLMResponse(LISTING, [])]), FakeMcp(), 3)

    with TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None})) as c:
        body = c.post("/chat", json={"question": "hola"}).json()
    assert body["visuals"] == [] and body["diagnostics"]["reconciliation"] is None


def test_source_and_kind_decisions_use_the_original_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.agent import presentation

    seen: dict[str, str] = {}
    real_sources, real_kind = presentation.build_sources, presentation.answer_kind
    monkeypatch.setattr(presentation, "build_sources", lambda q, answer, *a, **k: (seen.__setitem__("sources", answer), real_sources(q, answer, *a, **k))[1])
    monkeypatch.setattr(presentation, "answer_kind", lambda q, answer, *a, **k: (seen.__setitem__("kind", answer), real_kind(q, answer, *a, **k))[1])
    with web_client(ConversationMemory()) as c:
        c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?"})
    assert seen["sources"] == LISTING and seen["kind"] == LISTING


def test_the_module_does_not_depend_on_the_agent_or_the_llm() -> None:
    assert "futsal.agent.agent" not in reconcile.__dict__.get("__doc__", "") and not hasattr(reconcile, "Agent")


def test_a_full_stats_markdown_table_is_compared_cell_by_cell_with_its_header() -> None:
    head = "| Pos | Equipo | PJ | G | E | P | GF | GC | DG | Pts |\n|---|---|---|---|---|---|---|---|---|---|\n"
    ok = "| 1 | PARQUE NORTE F.S. - RODILLITO 'B' | 7 | 6 | 1 | 0 | 30 | 10 | +20 | 19 |\n| 2 | C.D. DOSA | 7 | 5 | 1 | 1 | 28 | 12 | +16 | 16 |"
    assert "|" not in reconcile_answer(f"Tabla:\n\n{head}{ok}\n\nFin.", [TABLE]).text.replace("Tabla:", "")
    wrong = ok.replace("| 28 | 12 |", "| 29 | 12 |")  # un solo GF distinto: esa fila se conserva, la otra no
    r = reconcile_answer(f"Tabla:\n\n{head}{wrong}\n\nFin.", [TABLE])
    assert "C.D. DOSA" in r.text and "PARQUE NORTE" not in r.text and head.strip().splitlines()[0] in r.text
    no_header = "| 1 | PARQUE NORTE F.S. - RODILLITO 'B' | 7 | 6 | 1 | 0 | 30 | 10 | +20 | 19 |"
    assert "PARQUE NORTE" in reconcile_answer(f"Tabla:\n\n| a | b |\n|---|---|\n{no_header}\n", [TABLE]).text  # sin cabecera reconocida no hay certeza


def test_visuals_with_limited_data_only_back_what_they_actually_contain() -> None:
    no_score = {**RESULTS, "rows": [{**RESULTS["rows"][0], "score": None}]}
    assert "7-1" in reconcile_answer("Intro.\n\n- C.D. DOSA 7-1 F.S. DIAGONAL 'A'\n", [no_score]).text  # el visual no trae marcador
    only_starters = {**LINEUP, "substitutes": []}
    r = reconcile_answer("Intro.\n\n- C.D. DOSA: ZAPATA RUDAS, SANTIAGO (P), IBARRA DE LA TORRE, HADER (Suplentes)\n", [only_starters])
    assert "IBARRA" in r.text  # un suplente que el visual no tiene
    assert "ZAPATA" not in reconcile_answer("Intro.\n\n- ZAPATA RUDAS, SANTIAGO (P)\n", [only_starters]).text
    empty = {**NEXT, "rows": []}
    assert reconcile_answer(LISTING, [empty]).text == LISTING


# --- correcciones de la revisión final: orden, etiqueta 'A', jornada del subtítulo -------------------------------------------------------------
def test_home_and_away_order_matters_so_an_inverted_item_is_kept() -> None:
    inverted = "- C.D. DOSA vs PARQUE NORTE F.S. - RODILLITO 'B' (07/11/2026 a las 11:00)"  # el visual dice que juega en casa el PARQUE NORTE
    r = reconcile_answer(f"Intro.\n\n{inverted}\n", [NEXT])
    assert inverted in r.text and not r.removed
    ida_vuelta = {**NEXT, "rows": [NEXT["rows"][0], {**NEXT["rows"][0], "round": 12, "date": "2026-12-12T10:00:00+00:00", "home": "C.D. DOSA", "away": "PARQUE NORTE F.S. - RODILLITO 'B'"}]}
    assert not reconcile_answer(f"Intro.\n\n{inverted}\n", [ida_vuelta]).removed  # la ida del 07/11 era al revés
    ok = "- C.D. DOSA vs PARQUE NORTE F.S. - RODILLITO 'B' (12/12/2026 a las 11:00)"
    assert len(reconcile_answer(f"Intro.\n\n{ok}\n", [ida_vuelta]).removed) == 1  # la vuelta, tal cual, sí está en el visual


def test_the_team_label_between_quotes_is_part_of_the_name_not_the_preposition_a() -> None:
    plain = {**NEXT, "rows": [{**NEXT["rows"][0], "home": "C.D. DOSA", "away": "CLUB X"}]}
    for label in ("'A'", "'B'", "’A’"):
        text = f"- C.D. DOSA {label} vs CLUB X (07/11/2026 a las 11:00)"
        assert text in reconcile_answer(f"Intro.\n\n{text}\n", [plain]).text, label  # visual sin etiqueta: puede ser otro equipo
    labelled = {**NEXT, "rows": [{**NEXT["rows"][0], "home": "C.D. DOSA 'A'", "away": "CLUB X"}]}
    assert len(reconcile_answer("Intro.\n\n- C.D. DOSA 'A' vs CLUB X (07/11/2026 a las 11:00)\n", [labelled]).removed) == 1
    assert not reconcile_answer("Intro.\n\n- C.D. DOSA vs CLUB X (07/11/2026 a las 11:00)\n", [labelled]).removed  # y sin etiqueta en el texto, tampoco hay certeza
    assert len(reconcile_answer("Intro.\n\n- C.D. DOSA ’A’ vs CLUB X (07/11/2026 a las 11:00)\n", [labelled]).removed) == 1  # comillas tipográficas


def test_a_round_from_the_visual_header_never_backs_a_round_that_differs_from_the_matched_row() -> None:
    visual = {**NEXT, "subtitle": "Jornada 7", "rows": [{**NEXT["rows"][0], "round": 8}]}
    wrong = "- PARQUE NORTE F.S. - RODILLITO 'B' vs C.D. DOSA (jornada 7, 07/11/2026 a las 11:00)"
    assert wrong in reconcile_answer(f"Intro.\n\n{wrong}\n", [visual]).text
    right = wrong.replace("jornada 7", "jornada 8")
    assert len(reconcile_answer(f"Intro.\n\n{right}\n", [visual]).removed) == 1
    table = {**TABLE, "subtitle": "Jornada 7"}  # sin registros con jornada propia, el encabezado sí respalda «jornada 7»
    assert len(reconcile_answer("Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' - 19 puntos (jornada 7)\n", [table]).removed) == 1
    assert not reconcile_answer("Intro.\n\n- PARQUE NORTE F.S. - RODILLITO 'B' - 19 puntos (jornada 6)\n", [table]).removed


def test_malformed_visuals_never_raise() -> None:
    bad: list[Any] = [None, 3, "x", [], {}, {"type": "matches"}, {"type": "matches", "rows": 5}, {"type": "matches", "rows": [1, None, {"home": 3, "away": None}]},
                      {"type": "table", "rows": True, "columns": 2.5}, {"type": "lineup", "starters": "no", "substitutes": [None], "team": 7},
                      {"type": "timeline", "events": {"a": 1}}, {"type": "cards", "events": [{"player": 5, "minute": "x"}]}, {"type": "officials", "people": 0}]
    for v in bad:
        assert reconcile_answer(LISTING, [v]).text == LISTING
    assert reconcile_answer(LISTING, bad).text == LISTING


# --- historial: el navegador reenvía el original cuando el servidor no conoce la conversación ---------------------------------------------------
class SpyScript(Script):
    def __init__(self, steps: list[LLMResponse], seen: list[list[Message]]) -> None:
        super().__init__(steps)
        self.seen = seen

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(list(messages))
        return super().complete(system, messages, tools)


def spy_client(steps: list[LLMResponse], memory: ConversationMemory, seen: list[list[Message]], mcp: Any = None) -> TestClient:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(SpyScript(steps, seen), mcp or FakeMcp(), 3, trace_results=True)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}, memory=memory))


def first_turn() -> dict[str, Any]:
    steps = [LLMResponse("", [ToolCall("c1", "list_matches", {"group_id": 1, "status": "scheduled"})]), LLMResponse(LISTING, [])]
    with spy_client(steps, ConversationMemory(), []) as c:
        return c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?", "conversation_id": "conv-restart"}).json()


def test_the_response_carries_the_original_text_only_when_something_was_omitted() -> None:
    body = first_turn()
    assert body["answer_original"] == LISTING and body["answer"] != LISTING and "AGRUPACION" not in body["answer"]

    steps = [LLMResponse("", [ToolCall("c1", "list_matches", {"group_id": 1, "status": "scheduled"})]), LLMResponse("Solo prosa, sin listas.", [])]
    with spy_client(steps, ConversationMemory(), []) as c:
        plain = c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?"}).json()
    assert plain["answer_original"] is None and plain["answer"] == "Solo prosa, sin listas." and plain["visuals"]


def test_after_a_server_restart_the_followup_keeps_the_data_if_the_browser_sends_the_original() -> None:
    body = first_turn()
    q1 = "¿Cuáles son los próximos partidos?"
    follow = [LLMResponse("El segundo es AGRUPACION COLEGIO CALASANCIO 'A'.", []), LLMResponse("ok", []), LLMResponse("ok", [])]
    # lo que hacía antes: el navegador reenvía lo VISIBLE y el servidor (sin memoria) se lo da al LLM sin la lista
    seen_before: list[list[Message]] = []
    with spy_client(list(follow), ConversationMemory(), seen_before) as c:
        c.post("/chat", json={"question": "¿A qué hora juega el segundo?", "conversation_id": "conv-restart",
                              "history": [{"role": "user", "content": q1}, {"role": "assistant", "content": body["answer"]}], "focus": body["focus"]})
    assert not any("CALASANCIO" in m.text for m in seen_before[0][:-1])
    # ahora: el navegador reenvía `answer_original` y el LLM ve los partidos
    seen_after: list[list[Message]] = []
    with spy_client(list(follow), ConversationMemory(), seen_after) as c:
        r = c.post("/chat", json={"question": "¿A qué hora juega el segundo?", "conversation_id": "conv-restart",
                                  "history": [{"role": "user", "content": q1}, {"role": "assistant", "content": body["answer_original"]}], "focus": body["focus"]})
    assert r.status_code == 200
    history = [m for m in seen_after[0][:-1] if m.role == "assistant"]
    assert history and "AGRUPACION COLEGIO CALASANCIO 'A' vs SAN FELIPE NERI" in history[0].text and "08/11/2026 a las 11:00" in history[0].text


# --- red de seguridad e interruptor ------------------------------------------------------------------------------------------------------------
def test_a_reconciliation_failure_never_breaks_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.agent import web

    def boom(answer: str, visuals: list[dict[str, Any]]) -> Any:
        raise TypeError("visual inesperado")

    monkeypatch.setattr(web, "reconcile_answer", boom)
    steps = [LLMResponse("", [ToolCall("c1", "list_matches", {"group_id": 1, "status": "scheduled"})]), LLMResponse(LISTING, [])]
    with spy_client(steps, ConversationMemory(), []) as c:
        res = c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?"})
    body = res.json()
    assert res.status_code == 200 and body["answer"] == LISTING and body["answer_original"] is None and [v["type"] for v in body["visuals"]] == ["matches"]
    assert body["diagnostics"]["reconciliation"]["error"] == "TypeError"


@pytest.mark.parametrize(("value", "active"), [("off", False), ("OFF", False), (" off ", False), ("on", True), ("", True), ("quizás", True)])
def test_the_reconcile_switch_only_turns_off_with_off(monkeypatch: pytest.MonkeyPatch, value: str, active: bool) -> None:
    monkeypatch.setenv("CHAT_RECONCILE", value)
    steps = [LLMResponse("", [ToolCall("c1", "list_matches", {"group_id": 1, "status": "scheduled"})]), LLMResponse(LISTING, [])]
    with spy_client(steps, ConversationMemory(), []) as c:
        body = c.post("/chat", json={"question": "¿Cuáles son los próximos partidos?"}).json()
    assert (body["answer"] != LISTING) is active and (body["answer_original"] is not None) is active
    assert (body["diagnostics"]["reconciliation"] is not None) is active
    assert [v["type"] for v in body["visuals"]] == ["matches"]  # los visuales no dependen del interruptor


def test_the_switch_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.agent.web import reconcile_enabled

    monkeypatch.delenv("CHAT_RECONCILE", raising=False)
    assert reconcile_enabled() is True
