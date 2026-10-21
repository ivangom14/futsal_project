"""Casos de reglamento del evaluador (Fase 15): se comprueba la recuperación vía `search_rules` y la respuesta citada.

Las preguntas proceden de `tests/fixtures/knowledge_eval_fifa.json` (evidencia ya verificada sobre el PDF de la FIFA 2025-26).
Dos casos no tienen evidencia en el documento: el agente debe decir que no consta, sin cifras ni sanciones.
"""
from __future__ import annotations

from typing import Any

from futsal.agent.evaluate import EvalCase, Fact, _call
from futsal.agent.llm import ToolCall

_TOOLS = frozenset({"search_rules"})


def _plan(query: str) -> Any:
    def plan(step: int, _p: list[dict[str, Any]]) -> list[ToolCall] | None:
        return _call("search_rules", query=query, limit=5) if step == 0 else None
    return plan


def _answerable(case_id: str, question: str, query: str, fact: Fact) -> EvalCase:
    return EvalCase(case_id, "reglamento", question, [fact], _TOOLS, _plan(query),
                    "el agente debe reformular y citar", required=_TOOLS, max_calls=3)


def _no_evidence(case_id: str, question: str) -> EvalCase:
    return EvalCase(case_id, "reglamento_sin_evidencia", question, [Fact("no_evidence_rules", "sin evidencia")], _TOOLS,
                    None, "el reglamento FIFA no contiene el dato", required=_TOOLS, max_calls=3, forbid_scores=True)


def rule_cases() -> list[EvalCase]:
    return [
        _answerable("regla_duracion_mitad", "¿Cuánto dura cada mitad del partido?", "periodos de juego duración mitades",
                    Fact("rule_cited", "mitades de 20 minutos", number=20, rule=7, pages=(60,),
                         contains="dos mitades iguales de 20 minutos")),
        _answerable("regla_tiempo_muerto", "¿Cuánto dura el tiempo muerto?", "tiempo muerto duración",
                    Fact("rule_cited", "tiempo muerto de un minuto", number=1, names=("un minuto",), rule=7, pages=(61,),
                         contains="un minuto de tiempo muerto")),
        _answerable("regla_sexta_falta", "¿a las cuántas faltas del equipo te hacen tiro libre sin barrera?",
                    "sexta falta acumulada tiro libre directo barrera",
                    Fact("rule_cited", "libre directo desde la sexta falta", number=6, names=("sexta", "seis"), rule=13,
                         pages=(99, 100), contains="faltas acumuladas")),
        _answerable("regla_roja_mano", "¿cuándo te sacan tarjeta roja por una mano?",
                    "expulsión infracción por mano ocasión manifiesta de gol",
                    Fact("rule_cited", "expulsión por evitar gol con la mano", names=("ocasion manifiesta", "evit"), rule=12,
                         pages=(85, 86), contains="ocasión manifiesta de gol")),
        _no_evidence("regla_sin_evidencia_sancion_rffm",
                     "¿Cuántos partidos de sanción corresponden a una tarjeta roja directa en Tercera División Futsal de la RFFM?"),
        _no_evidence("regla_sin_evidencia_salario", "¿Cuánto cobra un árbitro de futsal por partido?"),
    ]
