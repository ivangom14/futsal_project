"""Selección dinámica de herramientas (Fase E, `AGENT_TOOLS=dynamic`; por defecto `full` = las 15 de siempre).

Decide QUÉ herramientas recibe el LLM en una pregunta; no decide si se puede responder sin LLM (eso sería un Tier 0, otra decisión distinta con otro criterio).
Prioridad: RECALL. Ante cualquier duda devuelve `None` (= conjunto completo) y es preferible incluir una herramienta de más que omitir una necesaria.
Solo mira la pregunta actual con señales léxicas cerradas: sin LLM, sin red, determinista. Seguimientos (historial o foco) y preguntas sin señal clara: conjunto completo.

Sin ejecución de nada generado por el modelo. El agente ensancha al conjunto completo UNA vez si hay una señal concreta de herramienta ausente (ver `Agent._widen_tools`); un error de datos no ensancha.
"""

from dataclasses import dataclass

from futsal.agent.criteria import mentions_rules
from futsal.agent.entities import normalize

ALL_TOOLS = frozenset({
    "list_competitions", "list_groups", "list_rounds", "list_teams", "list_matches", "get_match", "get_match_report", "list_match_events",
    "top_scorers", "find_players", "find_teams", "search_rules", "get_standings", "get_scorer_table", "get_data_freshness"})

BASE = frozenset({"find_teams", "list_matches", "get_standings"})  # nunca se omiten en una pregunta de datos: son la puerta de entrada a casi todo
GROUPS: dict[str, frozenset[str]] = {
    "standings": frozenset({"get_standings", "find_teams", "list_groups", "list_competitions", "list_teams"}),
    "matches": frozenset({"find_teams", "list_matches", "get_match", "get_match_report", "list_rounds", "list_match_events", "list_teams", "find_players"}),
    "scorers": frozenset({"get_scorer_table", "top_scorers", "find_players", "list_match_events", "get_match_report", "find_teams", "list_matches", "list_groups"}),
    "catalog": frozenset({"list_competitions", "list_groups", "list_teams", "list_rounds", "find_teams"}),
    "freshness": frozenset({"get_data_freshness", "list_groups", "list_competitions"}),
    "rules": frozenset({"search_rules"}),
}
SIGNALS: dict[str, frozenset[str]] = {
    "standings": frozenset({"clasificacion", "posicion", "puesto", "puntos", "tabla", "lider", "clasificado", "clasificada", "descenso", "ascenso", "playoff", "puntua",
                            "puntuacion", "diferencia"}),
    "matches": frozenset({"resultado", "resultados", "partido", "partidos", "jugo", "juega", "jugara", "jugado", "jugados", "gano", "ganado", "perdio", "perdido",
                          "empato", "empates", "victoria", "victorias", "derrota", "derrotas", "proximo", "proxima", "ultimo", "ultima", "ultimos", "ultimas",
                          "jornada", "jornadas", "acta", "actas", "contra", "rival", "enfrentamiento", "campo", "pista", "horario", "hora", "fecha", "cuando", "donde",
                          "arbitro", "arbitros", "tarjeta", "tarjetas", "racha", "forma", "marcador", "quedo", "quedaron", "siguiente", "anterior", "goles", "gol",
                          "jugaron", "juegan", "disputado", "disputados", "disputa", "recuento", "balance", "falta", "faltas", "amonestaciones"}),
    "scorers": frozenset({"goleador", "goleadores", "goles", "gol", "marco", "marcado", "anotador", "anotadores", "pichichi", "maximo", "maxima", "jugador", "jugadores",
                          "anoto", "marcaron", "dorsal", "tarjetas", "amarillas", "rojas", "sancionado", "sancionados"}),
    "catalog": frozenset({"competiciones", "competicion", "grupos", "equipos", "categorias", "categoria", "ligas", "temporada"}),
    "freshness": frozenset({"actualizado", "actualizados", "actualizacion", "actualizada", "desactualizado", "reciente", "datos", "fresco", "ingesta"}),
}
RULE_HINTS = frozenset({"dura", "duracion", "mitad", "mitades", "tiempo", "tiempos", "reglamento", "permitido", "permitida", "prohibido", "prohibida", "norma", "normas",
                        "regla", "reglas", "sancion", "sanciones", "falta", "faltas", "penalti", "doble", "saque", "banda", "cambio", "cambios", "tamano", "balon", "circular"})
STRONG_DATA = frozenset({"resultado", "resultados", "proximo", "proxima", "ultimo", "ultima", "jornada", "jornadas", "acta", "actas", "contra", "rival", "enfrentamiento",
                         "campo", "pista", "horario", "marcador", "quedo", "quedaron", "clasificacion", "posicion", "puesto", "puntos", "tabla", "lider", "goleador",
                         "goleadores", "gano", "perdio", "empato", "siguiente"})  # sin una de estas, la pregunta pudo ser normativa: search_rules se mantiene
MAX_SELECTED = 12  # si la unión llega a casi todo, no compensa: conjunto completo


@dataclass(frozen=True)
class Selection:
    tools: frozenset[str] | None  # None = conjunto completo
    reason: str
    groups: tuple[str, ...] = ()


def select_tools(question: str, *, has_context: bool = False) -> Selection:
    if has_context:
        return Selection(None, "context")
    words = set(normalize(question).split())
    found = [g for g, sig in SIGNALS.items() if words & sig]
    strict_rules = mentions_rules(question)
    if strict_rules or words & RULE_HINTS or (found and not words & STRONG_DATA):
        found.append("rules")  # posible pregunta normativa (o sin ancla de datos fuerte): search_rules siempre disponible
    if not found:
        return Selection(None, "no_signal")
    chosen: set[str] = set()
    for g in found:
        chosen |= GROUPS[g]
    if found != ["rules"] or not strict_rules:  # solo con vocabulario normativo inequívoco se ofrece search_rules sola; con una pista débil, también las de datos
        chosen |= BASE
    chosen &= ALL_TOOLS
    if len(chosen) >= MAX_SELECTED:
        return Selection(None, "wide", tuple(found))
    return Selection(frozenset(chosen), "selected", tuple(found))


def filter_tools(tools: list[dict], selection: Selection) -> list[dict]:
    """Esquemas a enviar; si falta alguna herramienta esperada (servidor distinto) o no hay selección, todas."""
    if selection.tools is None:
        return tools
    kept = [t for t in tools if t.get("name") in selection.tools]
    return kept if kept and len(kept) == len(selection.tools & {t.get("name") for t in tools}) else tools
