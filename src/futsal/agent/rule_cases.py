"""Casos de reglamento del evaluador (Fase 15): se comprueba la recuperación vía `search_rules` y la respuesta citada.

Las preguntas proceden de `tests/fixtures/knowledge_eval_fifa.json` y `tests/fixtures/knowledge_eval_circular27.json`
(evidencia verificada sobre los PDF originales). Los casos sin evidencia deben terminar en «no consta», sin cifras ni sanciones.
Cada caso declara los documentos que deben estar indexados; si faltan, queda no evaluable (nunca aprobado).
"""
from __future__ import annotations

from typing import Any

from futsal.agent.evaluate import EvalCase, Fact, _call
from futsal.agent.llm import ToolCall

_TOOLS = frozenset({"search_rules"})
FIFA = frozenset({"fifa-futsal-laws-2025-26"})
C27 = frozenset({"rfef-circular-27-2025-26"})


def _plan(query: str) -> Any:
    def plan(step: int, _p: list[dict[str, Any]]) -> list[ToolCall] | None:
        return _call("search_rules", query=query, limit=5) if step == 0 else None
    return plan


def _answerable(case_id: str, question: str, query: str, facts: Fact | list[Fact], docs: frozenset[str] = FIFA) -> EvalCase:
    return EvalCase(case_id, "reglamento", question, facts if isinstance(facts, list) else [facts], _TOOLS, _plan(query),
                    "el agente debe reformular y citar", required=_TOOLS, max_calls=3, kb_docs=docs)


def _no_evidence(case_id: str, question: str, docs: frozenset[str] = FIFA) -> EvalCase:
    return EvalCase(case_id, "reglamento_sin_evidencia", question, [Fact("no_evidence_rules", "sin evidencia")], _TOOLS,
                    None, "los documentos indexados no contienen el dato", required=_TOOLS, max_calls=3, forbid_scores=True,
                    kb_docs=docs)


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
        *_circular27_cases(),
    ]


def _circular27_cases() -> list[EvalCase]:
    """Circular 27 de la RFEF (PDF de 220 p.; se indexan p. 1-23). Páginas físicas del PDF, no la numeración impresa."""
    both = FIFA | C27
    return [
        _answerable("circular27_comunica", "¿Qué comunica la Circular 27 de la RFEF y a qué temporada corresponde?",
                    "Circular 27 RFEF comunica afiliados temporada",
                    Fact("rule_cited", "circular FIFA 1941, temporada 2025/2026", number=2025, pages=(1,),
                         contains="la RFEF desea comunicar a todos sus afiliados el contenido de la Circular"), C27),
        _answerable("circular27_modificaciones", "¿Qué modificaciones se han aprobado en las Reglas de Juego del Futsal 2025/2026?",
                    "modificaciones Reglas de Juego Futsal 2025/2026 Bureau del Consejo",
                    Fact("rule_cited", "el Bureau del Consejo de la FIFA aprobó modificaciones", names=("bureau",), pages=(2, 3),
                         contains="serie de modificaciones"), C27),
        _answerable("circular27_cesped", "Según el resumen de modificaciones, ¿cuándo se permiten las superficies de césped artificial?",
                    "césped artificial competiciones nacionales",
                    Fact("rule_cited", "césped artificial: excepcional, solo competiciones nacionales", names=("nacional",), rule=1,
                         pages=(3,), contains="se permitirán las superficies de césped artificial"), C27),
        _answerable("circular27_cesped_coloquial", "¿Se puede jugar un partido de futsal en una pista de césped sintético?",
                    "superficies de césped artificial terreno de juego competiciones nacionales",
                    Fact("rule_cited", "césped artificial: excepcional, solo competiciones nacionales", names=("nacional",), rule=1,
                         pages=(3, 15), contains="se permitirán las superficies de césped artificial"), both),
        _answerable("circular27_fechas_rfef_fifa", "¿En qué fecha comunicó la RFEF la circular y en qué fecha la emitió la FIFA?",
                    "fecha circular RFEF Las Rozas septiembre circular FIFA Zúrich",
                    [Fact("rule_cited", "la RFEF comunicó el 8 de septiembre de 2025", all_of=("8 de septiembre",), pages=(1,),
                          contains="Las Rozas de Madrid, a 8 de septiembre de 2025"),
                     Fact("rule_cited", "la FIFA emitió la 1941 el 5 de septiembre de 2025", all_of=("5 de septiembre",), pages=(2,),
                          contains="Zúrich, 5 de septiembre de 2025")], C27),
        _no_evidence("circular27_sin_evidencia_rffm",
                     "¿Qué sanción impone la RFFM a un club que alinee a un jugador sin licencia en Tercera División Futsal?", C27),
    ]
