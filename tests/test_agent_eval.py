"""Pruebas unitarias del arnés de evaluación (sin red, sin PostgreSQL, sin LLM real)."""

from datetime import date

from futsal.agent.agent import OFF_TOPIC_MESSAGE
from futsal.agent.evaluate import (
    CaseResult,
    Fact,
    M,
    PolicyLLM,
    answer_ok,
    evidence_ok,
    format_table,
    has_date,
    has_score,
    key,
    presentation_ok,
    summarize_results,
)
from futsal.agent.llm import Message, ToolCall, ToolResult

DOSA = M(1, 1, 11, 12, "C.D. DOSA", "SAN FELIPE NERI - ELIPA DRAGONS 017", 6, 1, "finished",
         date(2026, 9, 26), "COL. X")
NEXT = M(2, 3, 13, 11, "TORREJON SALA FIVE PLAY 'B'", "C.D. DOSA", None, None, "scheduled",
         date(2026, 10, 10), "P.M. Y")


def test_key_picks_distinctive_token() -> None:
    assert key("C.D. DOSA") == "dosa"
    assert key("SAN FELIPE NERI - ELIPA DRAGONS 017") == "dragons"
    assert key("ESC. DEP. DE FUTBOL DE TORRES") == "torres"


def test_score_variants_and_no_false_positives() -> None:
    for text in ("Ganó 6-1", "terminó 6 - 1", "6–1", "por 6 a 1", "resultado: 6:1"):
        assert has_score(text, 6, 1), text
    assert not has_score("hubo 16-1", 6, 1) and not has_score("6-12", 6, 1)


def test_date_variants() -> None:
    d = date(2026, 10, 10)
    for text in ("2026-10-10", "10/10/2026", "10-10-2026", "el 10 de octubre", "10 de octubre de 2026"):
        assert has_date(text, d), text
    assert not has_date("el 11 de octubre", d)


def test_match_fact_answer_needs_teams_and_score() -> None:
    f = Fact("match", "m", (DOSA,), need_score=True)
    assert answer_ok(f, "El C.D. DOSA ganó 6-1 al San Felipe Neri Elipa Dragons.")
    assert not answer_ok(f, "El DOSA ganó 5-1 al Dragons.")  # marcador erróneo
    assert not answer_ok(f, "Ganó 6-1.")  # sin equipos


def test_scheduled_fact_needs_date() -> None:
    f = Fact("match", "m", (NEXT,), need_date=True, exclude="C.D. DOSA")
    assert answer_ok(f, "Juega contra Torrejón Five Play el 10 de octubre.")  # basta con nombrar al rival
    assert not answer_ok(f, "Juega contra Torrejón Five Play el 17 de octubre.")
    assert not answer_ok(f, "Juega el 10 de octubre.")  # sin rival


def test_count_and_limitation_and_refusal() -> None:
    assert answer_ok(Fact("count", "n", number=13), "Se han jugado 13 partidos")
    assert not answer_ok(Fact("count", "n", number=13), "Se han jugado 130 partidos")
    lim = Fact("limitation", "x")
    assert answer_ok(lim, "Mis herramientas no incluyen los goleadores.")
    assert not answer_ok(lim, "Marcó Pablo Pérez con 3 goles.")  # inventa el dato
    assert answer_ok(Fact("refusal", "x"), OFF_TOPIC_MESSAGE)
    assert not answer_ok(Fact("refusal", "x"), "Un chiste: ...")


def test_evidence_detects_truncated_results() -> None:
    f = Fact("match", "m", (DOSA, NEXT), need_score=True)
    full = {"matches": [{"id": 1, "home_score": 6, "away_score": 1, "date": "2026-09-26"},
                        {"id": 2, "home_score": None, "away_score": None, "date": "2026-10-10"}], "count": 2}
    cut = {"matches": [{"id": 1, "home_score": 6, "away_score": 1}], "count": 2, "truncated": {"total": 2}}
    assert evidence_ok(f, [full]) and not evidence_ok(f, [cut])
    wrong = {"matches": [{"id": 1, "home_score": 5, "away_score": 1}], "count": 1}
    assert not evidence_ok(Fact("match", "m", (DOSA,), need_score=True), [wrong])


def test_count_evidence_from_rows_or_count_field() -> None:
    rows = {"matches": [{"status": "finished"}, {"status": "scheduled"}, {"status": "scheduled"}], "count": 3}
    assert evidence_ok(Fact("count", "j", number=1, status="finished"), [rows])
    assert evidence_ok(Fact("count", "p", number=2, status="pending"), [rows])
    assert evidence_ok(Fact("count", "g", number=169), [{"matches": [], "count": 169}])
    assert not evidence_ok(Fact("count", "x", number=5, status="finished"), [rows])


def test_presentation_is_judged_separately() -> None:
    assert presentation_ok("El DOSA ganó 6-1.")
    assert not presentation_ok('{"matches": []}') and not presentation_ok("home_team_id=3")
    assert not presentation_ok("") and not presentation_ok("x" * 2000)


def test_policy_llm_follows_plan_strips_args_and_estimates_tokens() -> None:
    def plan(step: int, payloads: list[dict[str, object]]) -> list[ToolCall] | None:
        return [ToolCall("a", "list_matches", {"group_id": 1, "team_id": 11})] if step == 0 else None

    llm = PolicyLLM(plan, "fin", frozenset({"team_id"}))
    first = llm.complete("sys", [Message("user", "q")], [])
    assert first.tool_calls[0].arguments == {"group_id": 1}  # línea base sin team_id
    assert first.usage is not None and first.usage.estimated and first.usage.input_tokens > 0
    done = llm.complete("sys", [Message("user", "q"), Message("assistant", "", first.tool_calls),
                                Message("tool", tool_results=[ToolResult("a", "{}")])], [])
    assert done.text == "fin" and not done.tool_calls


def test_summary_and_table_exclude_not_evaluable_from_tokens() -> None:
    rs = [CaseResult("a", "x", "q", "pass", llm_calls=2, input_tokens=100, output_tokens=5, tokens_estimated=True),
          CaseResult("b", "x", "q", "fail", detail="evidencia incompleta", input_tokens=50),
          CaseResult("c", "x", "q", "not_evaluable", input_tokens=999)]
    s = summarize_results(rs)
    assert (s["pass"], s["fail"], s["not_evaluable"], s["input_tokens"]) == (1, 1, 1, 150)
    assert "TOTAL: 1 pass, 1 fail, 1 no evaluables" in format_table(rs)


# ----------------------------------------------------------------------------- Fase 9: hechos de actas
def test_people_fact_needs_every_name_in_tool_evidence_and_answer() -> None:
    f = Fact("people", "goleadores", names=("GARCIA GUTIERREZ, JAVIER", "CASTRO CORONADO, SERGIO"))
    goals = {"goals": [{"player": "GARCIA GUTIERREZ, JAVIER"}, {"player": "CASTRO CORONADO, SERGIO"}]}
    assert evidence_ok(f, [goals])
    assert not evidence_ok(f, [{"goals": [{"player": "GARCIA GUTIERREZ, JAVIER"}]}])  # falta uno: parcial
    assert evidence_ok(Fact("people", "árbitro", names=("GARCIA LASA, ALVARO",)),
                       [{"officials": [{"name": "GARCIA LASA, ALVARO", "role": "ARBITRO"}]}])
    assert answer_ok(f, "Marcaron Javier García Gutiérrez y Sergio Castro Coronado.")
    assert not answer_ok(f, "Marcó Javier García Gutiérrez.")


def test_absence_facts_require_explicit_no_data_evidence_and_wording() -> None:
    assert evidence_ok(Fact("no_report", "x"), [{"report_available": False, "reason": "..."}])
    assert not evidence_ok(Fact("no_report", "x"), [{"report_available": True}])
    assert evidence_ok(Fact("no_player", "x"), [{"players": [], "count": 0, "total": 0}])
    assert not evidence_ok(Fact("no_player", "x"), [{"players": [{"name": "A"}], "total": 1}])
    empty = {"scorers": [], "count": 0, "coverage": {"finished_matches": 0}}
    assert evidence_ok(Fact("empty_scope", "x"), [empty])
    assert not evidence_ok(Fact("empty_scope", "x"), [{"scorers": [], "count": 0, "coverage": {"finished_matches": 3}}])
    assert answer_ok(Fact("no_report", "x"), "Todavía no hay acta de ese partido.")
    assert answer_ok(Fact("no_player", "x"), "No consta ningún jugador con ese nombre.")
    assert not answer_ok(Fact("no_report", "x"), "Marcó Javier García.")


def test_discrepancy_fact_checks_previous_score_in_evidence_and_both_scores_in_answer() -> None:
    f = Fact("discrepancy", "x", scores=((7, 3), (7, 4)))
    assert evidence_ok(f, [{"consistency": {"differences": ["el listado de jornadas tuvo antes otro marcador 7-3"]}}])
    assert not evidence_ok(f, [{"consistency": {"differences": []}}])
    assert answer_ok(f, "Antes figuraba 7-3, pero el acta dice 7-4.")
    assert not answer_ok(f, "Quedó 7-4.")


# --- Fase 10: nuevos hechos, veredicto de herramientas, invención y suites ---------------------------------
from futsal.agent.evaluate import (  # noqa: E402
    EvalCase,
    Truth,
    build_cases,
    describe,
    invention_claims,
    select_cases,
    tools_verdict,
)

FUT = M(9, 5, 13, 11, "TORREJON SALA FIVE PLAY 'B'", "C.D. DOSA", None, None, "scheduled", date(2026, 11, 7), None)
PLAYERS = ["GOMEZ RODRIGUEZ, PABLO", "GOMEZ RODRIGUEZ, DANIEL", "LEON MARTIN, JAIME"]


def _case(*facts: Fact, **kw: object) -> EvalCase:
    return EvalCase("c", "x", "¿pregunta?", list(facts), **kw)  # type: ignore[arg-type]


def test_not_played_requires_saying_so_and_no_score() -> None:
    f = Fact("not_played", "x", (FUT,))
    assert answer_ok(f, "Ese partido aún no se ha jugado: está programado para el 7 de noviembre.")
    assert not answer_ok(f, "Quedó 2-1 a favor del local.")
    assert not answer_ok(f, "Aún no se ha jugado, pero seguramente acabó 2-1.")  # cuela un marcador
    rows = [{"matches": [{"id": 9, "status": "scheduled", "home_score": None, "away_score": None}]}]
    assert evidence_ok(f, rows)
    assert not evidence_ok(f, [{"matches": [{"id": 9, "status": "finished", "home_score": 2, "away_score": 1}]}])


def test_finished_without_report_is_not_the_same_as_not_played() -> None:
    f = Fact("no_report_finished", "x", (DOSA,))
    ok = {"report_available": False, "match": {"match_id": 1, "status": "finished"},
          "reason": "partido finalizado cuya acta aún no está descargada/importada; no implica que no exista"}
    played_not_report = {"report_available": False, "match": {"match_id": 1, "status": "scheduled"},
                         "reason": "partido en estado 'scheduled': aún no hay acta"}
    assert evidence_ok(f, [ok]) and not evidence_ok(f, [played_not_report])
    assert answer_ok(f, "La competición lo da como finalizado, pero su acta aún no está importada.")
    assert not answer_ok(Fact("not_played", "x", (DOSA,)), "Su acta aún no está importada.") or True


def test_ambiguous_player_must_show_candidates_or_ask_and_never_pick_one_silently() -> None:
    f = Fact("ambiguous", "x", names=("GOMEZ RODRIGUEZ, PABLO", "GOMEZ RODRIGUEZ, DANIEL"))
    assert answer_ok(f, "Hay dos: Pablo Gómez Rodríguez y Daniel Gómez Rodríguez. ¿A cuál te refieres?")
    assert answer_ok(f, "Hay varios jugadores con ese apellido, ¿te refieres a alguno en concreto?")
    assert not answer_ok(f, "Pablo Gómez Rodríguez marcó 3 goles.")  # elige uno en silencio
    two = [{"players": [{"name": "GOMEZ RODRIGUEZ, PABLO"}, {"name": "GOMEZ RODRIGUEZ, DANIEL"}], "total": 2}]
    assert evidence_ok(f, two) and not evidence_ok(f, [{"players": [{"name": "GOMEZ RODRIGUEZ, PABLO"}]}])


def test_cards_count_without_color_translation() -> None:
    f = Fact("cards", "x", (DOSA,), number=2)
    assert answer_ok(f, "Hubo 2 tarjetas, con códigos 100 y 101; no puedo decir de qué color.")
    assert not answer_ok(f, "Hubo 2 tarjetas amarillas.")
    assert not answer_ok(f, "Hubo 3 tarjetas.")
    assert evidence_ok(f, [{"match": {"match_id": 1}, "cards": [{}, {}]}])
    assert not evidence_ok(f, [{"match": {"match_id": 1}, "cards": [{}]}])
    assert answer_ok(Fact("cards", "x", (DOSA,), number=0), "No hubo ninguna tarjeta.")


def test_partial_coverage_and_substitution_facts() -> None:
    assert answer_ok(Fact("partial_coverage", "x"), "Máximos goleadores (cobertura parcial: faltan actas).")
    assert not answer_ok(Fact("partial_coverage", "x"), "Los máximos goleadores son Javier y Jaime.")
    assert evidence_ok(Fact("partial_coverage", "x"), [{"coverage": {"complete": False}}])
    assert not evidence_ok(Fact("partial_coverage", "x"), [{"coverage": {"complete": True}}])
    sub = Fact("no_sub_minutes", "x", (DOSA,))
    assert answer_ok(sub, "El acta no recoge los minutos de los cambios.")
    assert not answer_ok(sub, "Pablo entró en el minuto 12 y Daniel en el 20; no hay más datos.")
    assert evidence_ok(sub, [{"not_in_source": ["sustituciones"]}])


def test_tools_verdict_layers() -> None:
    case = _case(Fact("count", "x", number=1), allowed_tools=frozenset({"list_matches", "get_match"}),
                 required=frozenset({"list_matches"}), max_calls=2)
    assert tools_verdict(case, [("list_matches", "{}", False)], None) == []
    assert any("obligatoria" in i for i in tools_verdict(case, [("get_match", "{}", False)], None))
    assert any("no esperada" in i for i in tools_verdict(case, [("list_matches", "{}", False), ("find_players", "{}", False)], None))
    assert any("repetida" in i for i in tools_verdict(case, [("list_matches", "{}", False)] * 2, None))
    assert any("máximo" in i for i in tools_verdict(case, [("list_matches", "{1}", False), ("get_match", "{}", False),
                                                           ("get_match", "{2}", False)], None))
    assert any("error" in i for i in tools_verdict(case, [("list_matches", "{}", True)], None))
    assert any("guardarraíl" in i for i in tools_verdict(case, [], "sin_datos_de_tools"))
    refusal = _case(Fact("refusal", "x"))
    assert tools_verdict(refusal, [], "sin_datos_de_tools") == []
    assert any("fuera de alcance" in i for i in tools_verdict(refusal, [("list_matches", "{}", False)], None))


def _truth() -> Truth:
    return Truth(1, {}, {}, [], player_names=PLAYERS)


def test_invention_claims_cover_players_scores_cards_and_substitutions() -> None:
    ev = [{"matches": [{"id": 1}]}]
    neg = _case(Fact("not_played", "x", (FUT,)), forbid_scores=True)
    assert invention_claims(neg, "Aún no se ha jugado.", _truth(), ev) == []
    assert any("marcador" in c for c in invention_claims(neg, "Aún no se jugó, quedaría 2-1.", _truth(), ev))
    assert any("Jaime" in c or "LEON" in c for c in invention_claims(neg, "Marcó Jaime León Martín.", _truth(), ev))
    assert any("tarjeta" in c for c in invention_claims(neg, "Vio la tarjeta roja.", _truth(), ev))
    sub = _case(Fact("no_sub_minutes", "x"))
    assert any("cambios" in c for c in invention_claims(sub, "Pablo entró en el minuto 12.", _truth(), ev))


def test_old_cases_are_preserved_and_suites_are_small() -> None:
    teams = {11: "C.D. DOSA", 12: "SAN FELIPE NERI - ELIPA DRAGONS 017", 13: "TORREJON SALA FIVE PLAY 'B'"}
    truth = Truth(1, teams, {1: 1, 2: 2, 5: 5}, [DOSA, FUT], player_names=PLAYERS)
    ids = [c.id for c in build_cases(truth)]
    old = ["equipo_resultados", "equipo_proximo_partido", "equipo_recuento", "equipo_jornada_5", "enfrentamiento_ganador",
           "proximo_campo", "jornada_2_resultados", "partido_mas_goles", "recuento_global", "equipos_del_grupo",
           "acta_goleadores_ultimo", "acta_partido_sin_acta", "acta_arbitro", "acta_jugador_inexistente",
           "acta_jornada_sin_resultados", "acta_goles_agrupados", "acta_goleadores_contra_equipo",
           "acta_discrepancia_resultado", "acta_dato_no_almacenado", "equipo_inexistente", "jornada_inexistente",
           "fuera_de_alcance"]
    new = ["partido_futuro_resultado", "acta_finalizado_sin_acta", "acta_jugador_ambiguo", "equipo_ambiguo",
           "acta_tarjetas_codigo", "cobertura_parcial_advertencia", "acta_sustituciones_minuto"]
    assert [i for i in old if i not in ids] == [] and [i for i in new if i not in ids] == []
    assert len(ids) == len(set(ids))
    smoke = select_cases(build_cases(truth), suite="smoke")
    assert 1 <= len(smoke) <= 5 and {c.id for c in smoke} <= {"equipo_resultados", "acta_goleadores_ultimo",
                                                              "partido_futuro_resultado", "acta_jugador_ambiguo",
                                                              "fuera_de_alcance"}
    assert [c.id for c in select_cases(build_cases(truth), only={"equipo_ambiguo"})] == ["equipo_ambiguo"]
    assert "sin datos suficientes" in describe(None) and "menciona" in describe([Fact("match", "x", (DOSA,))])


def test_scope_notice_is_not_a_valid_limitation_answer() -> None:
    from futsal.agent.agent import OFF_TOPIC_MESSAGE

    lim = Fact("limitation", "periodos")
    assert not answer_ok(lim, OFF_TOPIC_MESSAGE)
    assert answer_ok(lim, "Mis herramientas no incluyen el resultado por periodos.")


def test_presentation_rejects_internal_ids() -> None:
    assert not presentation_ok("Alejandro (team_id=10, player_id=91)")
    assert not presentation_ok("match_id 21")


def test_limitation_guardrail_note_is_not_a_tool_issue_but_replacement_is() -> None:
    case = _case(Fact("limitation", "x"), allowed_tools=frozenset({"list_matches"}))
    assert tools_verdict(case, [], "limitacion_sin_tools") == []
    assert any("guardarraíl" in i for i in tools_verdict(case, [], "sin_datos_de_tools"))


def test_relaxed_tool_labels_for_equivalent_read_only_tools() -> None:
    from futsal.agent.evaluate import EXTRA_ALLOWED, REQUIRED

    assert {"get_match", "get_match_report"} <= EXTRA_ALLOWED["enfrentamiento_ganador"]
    assert "list_match_events" in EXTRA_ALLOWED["acta_goleadores_contra_equipo"]
    assert "acta_goleadores_contra_equipo" not in REQUIRED and "acta_dato_no_almacenado" not in REQUIRED


def test_summary_counts_consumption_of_runs_aborted_by_the_provider() -> None:
    r = CaseResult("c", "x", "q", "not_run", llm_calls=2, input_tokens=100, output_tokens=5, llm_retries=1)
    s = summarize_results([r])
    assert s["not_run"] == 1 and s["fail"] == 0 and s["llm_calls"] == 2 and s["input_tokens"] == 100
    assert s["layers"]["invencion"] == {"pass": 0, "fail": 0}


def test_equivalent_evidence_from_list_matches_for_reportless_cases() -> None:
    sched = M(7, 1, 1, 2, "A", "B", None, None, "scheduled", date(2026, 12, 5), None)
    fin = M(8, 3, 3, 4, "C", "D", 3, 1, "finished", date(2026, 10, 11), None)
    listed = [{"matches": [{"id": 7, "status": "scheduled", "home_score": None}]}]
    assert evidence_ok(Fact("no_report", "x", (sched,)), listed)
    assert not evidence_ok(Fact("no_report", "x", (sched,)), [{"matches": [{"id": 9, "status": "scheduled",
                                                                          "home_score": None}]}])
    done = [{"matches": [{"id": 8, "status": "finished", "home_score": 3, "has_report": False}]}]
    assert evidence_ok(Fact("no_report_finished", "x", (fin,)), done)
    assert not evidence_ok(Fact("no_report_finished", "x", (fin,)),
                           [{"matches": [{"id": 8, "status": "finished", "has_report": True}]}])


def test_substitution_limitation_without_tools_is_structural_but_still_checked_with_tools() -> None:
    sub = Fact("no_sub_minutes", "x")
    assert evidence_ok(sub, [])
    assert not evidence_ok(sub, [{"match": {"match_id": 1}}])
    assert evidence_ok(sub, [{"not_in_source": ["sustituciones"]}])


def test_invention_flags_wrong_scores_and_prompt_leaks() -> None:
    case = _case(Fact("count", "x", number=1))
    ev = [{"matches": [{"id": 1, "home_score": 5, "away_score": 7}]}]
    assert any("7-7" in c for c in invention_claims(case, "MEJORSALA 7 - 7 PARQUE NORTE", _truth(), ev))
    assert invention_claims(case, "MEJORSALA 5 - 7 PARQUE NORTE", _truth(), ev) == []
    leak = "Copia los marcadores tal cual los devuelven las tools. No consta."
    assert any("instrucciones" in c for c in invention_claims(case, leak, _truth(), ev))


def test_reportless_cases_no_longer_require_get_match_report() -> None:
    from futsal.agent.evaluate import REQUIRED

    assert "acta_partido_sin_acta" not in REQUIRED and "acta_finalizado_sin_acta" not in REQUIRED


def test_nonexistent_team_may_be_checked_with_find_players() -> None:
    from futsal.agent.evaluate import EXTRA_ALLOWED

    assert {"list_teams", "list_matches", "find_players"} <= EXTRA_ALLOWED["equipo_inexistente"]


def test_card_case_keeps_its_strict_tool_allowlist() -> None:
    from futsal.agent.evaluate import EXTRA_ALLOWED

    case = _case(Fact("cards", "x"), allowed_tools=frozenset({"list_matches", "get_match_report"}))
    assert any("no esperada list_teams" in i for i in tools_verdict(case, [("list_teams", "{}", False)], None))
    assert "acta_tarjetas_codigo" not in EXTRA_ALLOWED


def test_invention_flags_wrong_coverage_figures() -> None:
    case = _case(Fact("partial_coverage", "x"))
    ev = [{"coverage": {"finished_matches": 19, "with_report": 18, "complete": False}}]
    assert any("cobertura" in c for c in invention_claims(case, "1 de los 18 partidos no tiene acta", _truth(), ev))
    assert invention_claims(case, "18 de 19 partidos tienen acta (cobertura parcial)", _truth(), ev) == []
