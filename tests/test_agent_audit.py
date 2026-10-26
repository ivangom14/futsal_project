"""Fase 17: taxonomía, errores críticos, resumen de auditoría y criterios nuevos. Funciones puras: sin BD, red ni LLM."""
from futsal.agent.audit import (
    audit_summary,
    critical_reasons,
    expectation,
    letter,
    recommendations,
    render_audit,
)
from futsal.agent.criteria import claims_position
from futsal.agent.evaluate import CaseResult, EvalCase, Fact, answer_ok, evidence_ok


def _r(case_id: str = "x", status: str = "pass", category: str = "resultados", **kw: object) -> CaseResult:
    return CaseResult(case_id, category, "¿?", status, expect=str(kw.pop("expect", "complete")), **kw)  # type: ignore[arg-type]


def test_letters_follow_the_plan_categories_without_renaming_old_cases() -> None:
    assert letter("equipo_resultados", "resultados") == "A" and letter("partido_mas_goles", "combinada") == "A"
    assert letter("tabla_clasificacion_lider", "tablas") == "B" and letter("tabla_goleadores_maximo", "tablas") == "C"
    assert letter("tabla_goleadores_top_n", "tablas_multigrupo") == "C" and letter("tabla_clasificacion_otro_grupo", "tablas_multigrupo") == "B"
    assert letter("acta_arbitro", "actas") == "D" and letter("acta_finalizado_sin_acta", "negativo") == "D"
    assert letter("regla_sexta_falta", "reglamento") == "E" and letter("circular10_x", "reglamento_sin_evidencia") == "E"
    assert letter("combinada_lider_ultimo_resultado", "combinada_fuentes") == "F" and letter("fuera_de_alcance", "alcance") == "G"


def test_expectation_is_deduced_from_the_facts_and_can_be_forced() -> None:
    def case(kind: str, status: str | None = None, expect: str = "") -> EvalCase:
        return EvalCase("c", "tablas", "q", [Fact(kind, "l", status=status)], expect=expect)

    assert expectation(case("table_row")) == "complete"
    assert expectation(case("table_status", "not_applicable")) == "no_data"
    assert expectation(case("table_status", "not_ingested")) == "not_ingested"
    assert expectation(case("table_status", "round_not_ingested")) == "not_ingested"
    assert expectation(case("table_status", "failed")) == "query_error"
    assert expectation(case("table_status", "incomplete")) == "partial"
    assert expectation(case("no_report_finished")) == "not_ingested" and expectation(case("partial_coverage")) == "partial"
    assert expectation(case("no_evidence_rules")) == "no_data" and expectation(case("limitation", expect="unverifiable")) == "unverifiable"


def test_critical_only_for_failures_of_invention_or_wrong_official_source() -> None:
    assert critical_reasons("tablas", "pass", ["list_matches"], [], True) == []                    # un acierto nunca es crítico
    assert "fuente incorrecta" in critical_reasons("tablas_multigrupo", "fail", ["list_matches"], [], None)[0]
    assert "fuente incorrecta" in critical_reasons("combinada_fuentes", "fail", ["top_scorers"], [], None)[0]
    assert critical_reasons("tablas", "fail", ["find_teams", "list_competitions"], [], None) == []        # descubrir grupos/equipos no es reconstruir
    assert critical_reasons("resultados", "fail", ["get_match"], [], None) == []                    # herramienta extra de solo lectura en resultados: no crítico
    assert "invención" in critical_reasons("reglamento", "fail", [], ["cita «regla 99»"], False)[0]
    assert critical_reasons("tablas", "not_run", ["x"], ["y"], False) == []                        # infraestructura: no se juzga


def test_summary_pass_rate_uses_evaluable_cases_and_critical_failures_are_listed_apart() -> None:
    results = [_r("a", "pass"), _r("b", "pass", category="tablas", expect="no_data"),
               _r("tabla_x", "fail", category="tablas", critical=["invención: da un marcador"], detail="invención: da un marcador | respuesta incorrecta"),
               _r("n", "not_evaluable", detail="requiere LLM real (x)"), _r("r", "not_run")]
    a = audit_summary(results)
    assert a["by_category"]["A"]["pass_rate_evaluable_pct"] == 100.0
    assert a["by_category"]["B"] == {**a["by_category"]["B"], "pass": 1, "fail": 1, "pass_rate_evaluable_pct": 50.0}
    assert a["critical_count"] == 1 and a["critical"][0]["case"] == "tabla_x"
    assert a["situations"]["no_data"] == {"pass": 1, "fail": 0} and a["situations"]["complete"] == {"pass": 1, "fail": 1}
    assert a["failure_causes"] == {"invención": 1, "respuesta incorrecta": 1}
    assert a["recommendations"][0].startswith("CRÍTICO") and "tabla_x" in a["recommendations"][0]
    assert "Errores críticos (invención o fuente incorrecta): 1" in "\n".join(render_audit(a, "live"))


def test_tool_metrics_cover_loops_errors_wrong_source_and_calls() -> None:
    results = [_r("a", tool_calls=2, tool_issues=["llamada repetida list_matches({})"]),
               _r("b", tool_calls=5, tool_issues=["7 llamadas (máximo razonable 4)", "la herramienta get_standings devolvió error"],
                  unexpected_tools=["list_matches"]),
               _r("c", "not_evaluable", tool_calls=9)]
    t = audit_summary(results)["tools"]
    assert (t["calls_avg"], t["calls_max"], t["repeated_calls"], t["over_limit"], t["errors"], t["wrong_source"]) == (3.5, 5, 1, 1, 1, 1)


def test_cost_is_never_estimated_without_prices_or_from_estimated_tokens() -> None:
    real = [_r("a", input_tokens=2_000_000, output_tokens=1_000_000, token_source="provider")]
    assert audit_summary(real)["tokens"]["cost_usd"] is None
    assert audit_summary(real, (1.0, 4.0))["tokens"]["cost_usd"] == 6.0
    estimated = [_r("a", input_tokens=10, tokens_estimated=True, token_source="estimated")]
    t = audit_summary(estimated, (1.0, 4.0))["tokens"]
    assert t["cost_usd"] is None and "estimados" in t["cost_note"]


def test_offline_reports_say_they_do_not_judge_a_real_llm() -> None:
    text = "\n".join(render_audit(audit_summary([_r("a")]), "offline"))
    assert "no evalúa" in text.lower() and "llm real" in text.lower()
    assert "no evalúa" not in "\n".join(render_audit(audit_summary([_r("a")]), "live")).lower()


def test_recommendations_distinguish_missing_preconditions_from_live_only_cases() -> None:
    recs = recommendations([_r("a", "not_evaluable", detail="no hay clasificación oficial ingerida"),
                            _r("b", "not_evaluable", detail="requiere LLM real (x)")], audit_summary([]))
    assert any("precondiciones" in r for r in recs) and any("--mode live" in r for r in recs)
    assert recommendations([], audit_summary([])) == ["Sin hallazgos que priorizar en esta ejecución."]


def test_claims_position_needs_the_rank_not_any_number() -> None:
    assert claims_position("Está en el puesto 3 con 9 puntos", 3) and claims_position("Es el 2.º clasificado", 2)
    assert claims_position("Es segundo", 2) and claims_position("Lidera la tabla", 1) and claims_position("Ocupa la posición 7", 7)
    assert claims_position("ocupa la **8ª posición** con 6 puntos", 8) and claims_position("es el 8º", 8)
    assert not claims_position("Tiene 3 puntos", 3) and not claims_position("Lleva 5 victorias", 5)
    assert not claims_position("Está en el puesto 13", 3)


def test_table_status_fact_checks_the_declared_state_of_that_group_and_that_the_answer_admits_it() -> None:
    f = Fact("table_status", "grupo de eliminatorias", names=("COPA X", "Eliminatorias"), status="not_applicable")
    ok = {"status": "not_applicable", "competition": "COPA X", "group": "Eliminatorias"}
    assert evidence_ok(f, [ok]) and not evidence_ok(f, [{**ok, "group": "Grupo 1"}]) and not evidence_ok(f, [{**ok, "status": "ok"}])
    assert answer_ok(f, "El grupo no dispone de tabla de clasificación: la RFFM no publica clasificación.")
    assert answer_ok(f, "En eliminatorias no consta clasificación.") and not answer_ok(f, "Lidera el Club A con 9 puntos.")
    rnd = Fact("table_status", "jornada", names=("COPA X", "Eliminatorias"), status="round_not_ingested")
    assert evidence_ok(rnd, [{**ok, "status": "failed"}]) and evidence_ok(rnd, [{**ok, "status": "round_not_ingested"}])
    inc = Fact("table_status", "parcial", names=("COPA X", "Eliminatorias"), status="incomplete")
    assert answer_ok(inc, "La clasificación es parcial: faltan equipos.") and not answer_ok(inc, "Lidera el Club A.")


# --- Fase 17.4: correcciones del evaluador tras la primera pasada live completa -------------------------------------
def test_page_lists_are_validated_page_by_page_and_the_circular_number_comes_from_the_document() -> None:
    from futsal.agent.criteria import page_numbers, unsupported_digits, unsupported_rule_citations

    hits = [{"hits": [{"text": "mano", "pages": "85-86", "rule": 12, "document": "Circular n.º 27 de la RFEF", "doc_key": "rfef-circular-27-2025-26"},
                      {"text": "mas", "pages": "90", "rule": 12}]}]
    ok = "Regla 12, páginas 85, 90 y 86; Circular n.º 27 de la RFEF."
    assert page_numbers("pág. 60; páginas 85, 90 y 177-178; p. 9-10") == {60, 85, 90, 177, 178, 9, 10}
    assert unsupported_digits(ok, hits) == [] and unsupported_rule_citations(ok, hits) == []
    bad = "Regla 12, páginas 85, 90 y 177-178."                  # 177-178 no se recuperó: ahora SÍ se detecta como cita sin respaldo
    assert unsupported_rule_citations(bad, hits) == ["páginas 85, 90 y 177-178"]
    assert unsupported_digits("Circular n.º 99 de la RFEF.", hits) == ["99"]  # un número de circular que ningún fragmento respalda sigue siendo invención


def test_a_scope_clarification_may_repeat_the_numbers_of_the_question_but_not_add_new_ones() -> None:
    from futsal.agent.criteria import asks_scope_clarification

    catalog = [("LIGA", ["Grupo 1", "Grupo 2"])]
    text = "Necesito que me indiques a qué competición y grupo te refieres para la jornada 2."
    assert asks_scope_clarification(text, catalog, "¿Cómo quedaron los partidos de la jornada 2?")
    assert not asks_scope_clarification(text, catalog, "¿Cómo quedaron los partidos?")                     # el 2 no venía de la pregunta
    assert not asks_scope_clarification(text + " Van 12 goles.", catalog, "¿Cómo quedaron los partidos de la jornada 2?")


def test_failed_case_ids_reads_fail_and_not_run_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    from futsal.agent.evaluate import failed_case_ids

    path = tmp_path / "r.json"
    path.write_text(json.dumps({"cases": [{"case_id": "a", "status": "pass"}, {"case_id": "b", "status": "fail"},
                                          {"case_id": "c", "status": "not_run"}, {"case_id": "d", "status": "not_evaluable"}]}), encoding="utf-8")
    assert failed_case_ids(path) == {"b", "c"}


def test_scoped_questions_and_discovery_tools_for_old_cases_with_several_groups() -> None:
    from futsal.agent.evaluate import DISCOVERY_TOOLS, Truth, build_cases, scoped_question

    t = Truth(1, {}, {}, [], competition="LIGA UNO FS", group_name="Grupo 2", groups_total=25)
    assert scoped_question(t, "¿Qué resultados ha tenido el C.D. DOSA?") == "En LIGA UNO FS (Grupo 2), ¿qué resultados ha tenido el C.D. DOSA?"
    assert scoped_question(t, "") == ""
    cases = {c.id: c for c in build_cases(t)}
    assert DISCOVERY_TOOLS <= cases["tabla_clasificacion_lider"].allowed_tools
    assert not DISCOVERY_TOOLS & cases["fuera_de_alcance"].allowed_tools if "fuera_de_alcance" in cases else True
    rule = next(c for c in cases.values() if c.category.startswith("reglamento"))
    assert not DISCOVERY_TOOLS & rule.allowed_tools
    # con un solo grupo las preguntas históricas no cambian
    single = Truth(1, {}, {}, [], competition="LIGA UNO FS", group_name="Grupo 2", groups_total=1)
    assert next(c for c in build_cases(single) if c.id == "tabla_clasificacion_lider").question == "¿Quién lidera la clasificación del grupo y con cuántos puntos?"


def test_a_literal_quote_of_a_retrieved_fragment_is_not_a_citation_of_the_rules_it_mentions() -> None:
    from futsal.agent.criteria import unsupported_rule_citations

    hit = [{"hits": [{"text": "Son aquellas sancionadas con un libre directo según lo estipulado en la Regla 3, la Regla 4 y la Regla 12.",
                      "pages": "99", "rule": 13}]}]
    literal = "Regla 13, página 99: «son aquellas sancionadas con un libre directo según lo estipulado en la Regla 3, la Regla 4 y la Regla 12»."
    assert unsupported_rule_citations(literal, hit) == []
    altered = "Regla 13, página 99: «son aquellas sancionadas con un libre directo y lo estipulado en la Regla 7 del reglamento»."
    assert unsupported_rule_citations(altered, hit) == ["Regla 7"]                       # una cita que no está en el fragmento se sigue detectando
    assert unsupported_rule_citations(literal + " Además la Regla 12 lo prohíbe.", hit) == ["Regla 12"]   # fuera de la cita literal, también


def test_partial_coverage_case_asks_about_the_actas_and_the_player_ambiguity_case_accepts_the_official_table() -> None:
    from futsal.agent.evaluate import EXTRA_ALLOWED

    assert "get_scorer_table" in EXTRA_ALLOWED["acta_jugador_ambiguo"]
    assert "get_scorer_table" not in EXTRA_ALLOWED.get("cobertura_parcial_advertencia", frozenset())  # esa vía sigue siendo la de las actas
