"""Preguntas de reglamento y circulares: se reconocen como del ámbito y se consulta search_rules antes de aceptar un rechazo o un
«no dispongo» sin herramientas. LLM guionizado, sin red. Las protecciones de alcance e invención se mantienen."""
import asyncio
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import OFF_TOPIC_MESSAGE, RULES_RECONSIDER_MESSAGE, SYSTEM_PROMPT, Agent, AgentResult
from futsal.agent.criteria import mentions_competition, mentions_rules, unsupported_digits, unsupported_rule_citations
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server

CIRCULAR_9 = "¿Qué establecía la Circular 9 de 17 de agosto de 2026 de la RFEF?"
SANCION = "¿Qué sanción económica impone la RFFM por protestar al árbitro?"
LIMITATION = "No dispongo de ese dato en mis herramientas."
HIT = {"hits": [{"text": "Anula y sustituye a la Circular N.º 9.", "pages": "1", "document": "Circular n.º 10", "doc_key": "rfef-circular-10", "rule": None}],
       "count": 1}
searches: list[str] = []


def _api(hits: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/competitions":
            comps = [{"id": i, "name": f"LIGA {i} FS", "groups": [{"id": i * 10 + 1, "name": "Grupo 1"}, {"id": i * 10 + 2, "name": "Grupo 2"}]}
                     for i in (1, 2)]
            return httpx.Response(200, json={"items": comps, "competitions": comps, "count": 2})
        if path == "/knowledge/search":
            searches.append(request.url.params.get("q", ""))
            return httpx.Response(200, json=hits)
        return httpx.Response(200, json={"matches": [], "count": 0})
    return handler


class Script:
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script, self.calls, self.seen = list(script), 0, []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        self.seen.append([m.text for m in messages if m.role == "user"])
        return self.script.pop(0) if self.script else LLMResponse("fin", [])


def _run(llm: Script, question: str, hits: dict[str, Any] | None = None) -> AgentResult:
    searches.clear()
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api(hits if hits is not None else {"hits": [], "count": 0}))))

    async def go() -> AgentResult:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), 5).run(question)  # type: ignore[arg-type]

    return asyncio.run(go())


def _search(query: str) -> LLMResponse:
    return LLMResponse("", [ToolCall("s", "search_rules", {"query": query})])


def test_vocabulary_separates_rules_questions_from_competition_questions() -> None:
    assert mentions_rules(CIRCULAR_9) and mentions_rules(SANCION) and mentions_rules("¿Qué dice el reglamento sobre el saque de meta?")
    assert not mentions_rules("¿Quién ganó el Mundial de la FIFA 2022?")             # «FIFA» o «RFEF» solos no bastan
    assert not mentions_rules("¿Quién lidera la clasificación del grupo?")
    assert mentions_competition("¿Quién lidera la clasificación del grupo?") and not mentions_competition("¿Cuánto dura la mitad según las reglas?")


def test_legitimate_circular_question_rejected_as_off_topic_is_reconsidered_with_search_rules() -> None:
    llm = Script([LLMResponse(OFF_TOPIC_MESSAGE, []), _search("Circular 9 anulada"),
                  LLMResponse("La Circular 10 anula y sustituye a la Circular 9 (Circular n.º 10, p. 1); el contenido de la Circular 9 no figura en lo recuperado.", [])])
    res = _run(llm, CIRCULAR_9, HIT)
    assert RULES_RECONSIDER_MESSAGE in llm.seen[1] and searches == ["Circular 9 anulada"]
    assert res.tool_calls == 1 and res.guardrail is None and res.answer != OFF_TOPIC_MESSAGE and "no figura" in res.answer


def test_sanction_question_answered_with_no_data_but_without_tools_gets_one_search_first() -> None:
    llm = Script([LLMResponse(LIMITATION, []), _search("sancion protestar arbitro"),
                  LLMResponse("Los fragmentos recuperados no recogen ninguna sanción económica de la RFFM.", [])])
    res = _run(llm, SANCION)
    assert searches == ["sancion protestar arbitro"] and res.tool_calls == 1 and res.guardrail is None
    assert "no recogen" in res.answer


def test_if_the_model_insists_without_tools_after_the_reconsideration_the_honest_limitation_is_kept_and_asked_only_once() -> None:
    llm = Script([LLMResponse(LIMITATION, []), LLMResponse(LIMITATION, [])])
    res = _run(llm, SANCION)
    assert llm.calls == 2 and res.tool_calls == 0 and res.answer == LIMITATION and res.guardrail == "limitacion_sin_tools"


def test_search_without_evidence_does_not_license_invented_content() -> None:
    # el agente no inventa por sí solo; el evaluador marca cifras y citas sin respaldo en los fragmentos recuperados
    empty = [{"hits": [], "count": 0}]
    assert unsupported_digits("La Circular imponía 3 puntos de sanción.", empty, SANCION) == ["3"]
    assert unsupported_rule_citations("Regla 12, página 85.", empty) == ["Regla 12", "página 85"]
    llm = Script([_search("sancion"), LLMResponse("Los fragmentos recuperados no recogen esa sanción.", [])])
    assert _run(llm, SANCION).answer == "Los fragmentos recuperados no recogen esa sanción."


def test_answer_with_pertinent_evidence_is_accepted_unchanged() -> None:
    text = "Según la Circular n.º 10 (página 1), anula y sustituye a la Circular N.º 9."
    res = _run(Script([_search("Circular 9"), LLMResponse(text, [])]), CIRCULAR_9, HIT)
    assert res.answer == text and res.guardrail is None and res.error is None
    assert unsupported_rule_citations(text, [HIT]) == [] and unsupported_digits(text, [HIT], CIRCULAR_9) == []


def test_questions_outside_the_functional_scope_are_still_rejected() -> None:
    for q in ("Cuéntame un chiste", "¿Quién ganó el Mundial de la FIFA 2022?"):
        llm = Script([LLMResponse(OFF_TOPIC_MESSAGE, [])])
        res = _run(llm, q)
        assert res.answer == OFF_TOPIC_MESSAGE and llm.calls == 1 and res.tool_calls == 0, q   # sin reconsulta forzada
    joke = Script([LLMResponse("Un chiste sobre circulares: ¡qué vueltas!", []), LLMResponse("Otro chiste.", [])])
    res = _run(joke, "Cuéntame un chiste sobre una circular")   # la palabra «circular» solo pide UNA búsqueda: sin datos de tools no se acepta
    assert res.answer == OFF_TOPIC_MESSAGE and res.guardrail == "sin_datos_de_tools"


def test_rules_questions_do_not_ask_for_a_group_but_competition_questions_still_do() -> None:
    ask = "Necesito que me indiques a qué competición y grupo te refieres."
    rules = _run(Script([LLMResponse(ask, []), _search("duracion"), LLMResponse("La mitad dura 20 minutos (Regla 7).", [])]),
                 "¿Cuánto dura la mitad según las reglas?")
    assert rules.guardrail is None and rules.tool_calls == 1                                          # no es una aclaración de ámbito
    comp = _run(Script([LLMResponse(ask, [])]), "¿Quién lidera la clasificación del grupo?")
    assert comp.answer == ask and comp.guardrail == "aclaracion_ambito"                               # el comportamiento de clasificaciones no cambia


def test_prompt_names_circulars_as_in_scope_and_asks_to_attribute_each_fact_to_its_document() -> None:
    assert "reglas del juego y circulares" in SYSTEM_PROMPT and "cita documento, regla y página" in SYSTEM_PROMPT
    assert "sin atribuir a un organismo (FIFA, RFEF) lo que dice otro" in SYSTEM_PROMPT


# --- Fase 17.7: criterios de cifras con metadatos del fragmento y caso de la Circular 9 --------------------------------
SECTION_HIT = [{"hits": [{"text": "Los árbitros confirmarán que han transcurrido los 20 minutos de juego.", "pages": "60", "rule": 7,
                          "document": "Reglas de Juego del Futsal 2025/26", "section": "2 FINALIZACIÓN DE LOS PERIODOS", "rule_title": "La duración del partido"}]}]
C10_HIT = [{"hits": [{"text": "Anula y sustituye a la Circular N.º 9.", "pages": "1", "document": "Circular n.º 10 (rectificativa) de la CTAFS",
                      "doc_key": "rfef-circular-10-rectificativa-2026-27"}]}]


def test_digits_from_hit_metadata_are_supported_but_invented_ones_are_not() -> None:
    cited = "Regla 7 «La duración del partido», sección «2 FINALIZACIÓN DE LOS PERIODOS», página 60: 20 minutos."
    assert unsupported_digits(cited, SECTION_HIT) == []
    assert unsupported_digits(cited + " Con 3 periodos de prórroga.", SECTION_HIT) == ["3"]


def test_without_evidence_only_the_documents_own_name_supports_figures_not_the_fragment_text() -> None:
    assert unsupported_digits("Según la Circular n.º 10, la Circular 9 queda sin efecto.", C10_HIT, "¿Qué establecía la Circular 9?", metadata_only=True) == []
    # el texto tangencial de un fragmento no respalda cifras en un caso sin evidencia: «20 minutos» sigue siendo invención aquí
    assert unsupported_digits("Son 20 minutos.", SECTION_HIT, "¿Cuánto cuesta?", metadata_only=True) == ["20"]


def test_circular_9_case_accepts_admitting_the_content_is_missing_and_rejects_answering_as_if_it_were_known() -> None:
    from futsal.agent.evaluate import Fact, Truth, answer_ok, describe, invention_claims
    from futsal.agent.rule_cases import rule_cases

    case = next(c for c in rule_cases() if c.id == "circular10_sin_evidencia_circular9")
    fact = case.facts[0]  # type: ignore[index]
    assert isinstance(fact, Fact) and "puede añadir que la Circular 10 la anula" in describe(case.facts)
    ideal = "Solo consta que la Circular n.º 10 (página 1) anula y sustituye a la Circular 9; su contenido no figura en lo recuperado."
    assert answer_ok(fact, ideal) and invention_claims(case, ideal, Truth(1, {}, {}, []), C10_HIT) == []
    observed = "La Circular n.º 10 establece que la Circular n.º 9, de 17 de agosto de 2026, queda sin efecto, anulada y sustituida por la Circular n.º 10."
    assert not answer_ok(fact, observed)                       # responde como si lo supiera: sigue fallando por la respuesta…
    assert invention_claims(case, observed, Truth(1, {}, {}, []), C10_HIT) == []   # …pero ya no como invención crítica
    invented = ideal + " Imponía 3 puntos de sanción."
    assert any("3" in c for c in invention_claims(case, invented, Truth(1, {}, {}, []), C10_HIT))


# --- Fase 20: retribución del arbitraje, un par (árbitro + cobro) que debe comprobarse en el reglamento antes de decir «no consta» -----
SALARIO = "¿Cuánto cobra un árbitro de futsal por partido?"


def test_referee_pay_is_a_rules_question_only_as_a_pair_of_words() -> None:
    assert mentions_rules(SALARIO) and mentions_rules("¿Cuál es el sueldo de un colegiado?")
    assert not mentions_rules("¿Cuánto cobra Messi por partido?")            # cobro sin árbitro: ajeno
    assert not mentions_rules("¿Quién arbitró el partido de la jornada 2?")  # árbitro sin cobro: dato de acta
    assert not mentions_rules("¿Qué dice la FIFA sobre el fútbol?")          # «FIFA» sola no convierte nada en reglamento


def test_referee_pay_is_searched_before_accepting_no_consta() -> None:
    llm = Script([LLMResponse(LIMITATION, []), _search("tarifa arbitral retribución árbitro"),
                  LLMResponse("En los documentos consultados no consta cuánto cobra un árbitro.", [])])
    res = _run(llm, SALARIO)
    assert searches == ["tarifa arbitral retribución árbitro"] and res.tool_calls == 1 and res.guardrail is None
    assert "no consta" in res.answer and RULES_RECONSIDER_MESSAGE in llm.seen[1]


def test_pay_of_a_non_futsal_figure_keeps_the_scope_rejection() -> None:
    llm = Script([LLMResponse(OFF_TOPIC_MESSAGE, []), LLMResponse(OFF_TOPIC_MESSAGE, [])])   # «partido» activa la reconsulta de siempre
    res = _run(llm, "¿Cuánto cobra Messi por partido?")
    assert res.answer == OFF_TOPIC_MESSAGE and res.tool_calls == 0 and searches == []
    assert not any(RULES_RECONSIDER_MESSAGE in turn for turn in llm.seen)                       # no se le manda a search_rules
