"""Descripciones compactas de las herramientas MCP (Fase 1 de la optimización de tokens).

Solo textos: no cambian nombres, parámetros, tipos, restricciones ni respuestas. Con `AGENT_PROMPT_MODE=compact` el servidor usa estas
descripciones; sin la bandera (o `legacy`) usa las de siempre (los docstrings y `Field(description=…)` de `server.py`). Lo que se ha quitado
está dicho ya en el prompt del agente (la receta de último/próximo partido, citar y no inventar al consultar el reglamento…); lo que
distingue herramientas parecidas (tabla oficial frente a actas) y las reglas contra IDs inventados se conserva.

Las claves son `herramienta` o `herramienta.parametro`.
"""

from futsal.config.settings import env_value

COMPACT: dict[str, str] = {
    "list_competitions": "Competiciones con sus grupos; el id del grupo es el group_id de las demás tools.",
    "list_groups": "Grupos de una competición; su id es el group_id.",
    "find_teams": "Busca equipos por nombre en todos los grupos (team_id, group_id, grupo y competición). Si coinciden varios grupos y la "
                  "pregunta no lo precisa, pregunta cuál.",
    "list_rounds": "Jornadas de un grupo por número; su id es el round_id de list_matches.",
    "list_teams": "Equipos de un grupo.",
    "list_matches": "Partidos de un grupo: equipos, fecha, estado, marcador y has_report. count = total de coincidencias (shown = filas "
                    "devueltas con limit). past_due=true: programado con fecha ya pasada (datos posiblemente desactualizados; no jugado).",
    "list_matches.order": "asc (jornada, por defecto), desc (más reciente primero) o date_asc (más próximo primero)",
    "get_data_freshness": "Cuándo se actualizó cada dato (partidos, actas, clasificación, goleadores) de un grupo o una competición, con "
                          "avisos de datos desactualizados o parciales. Solo lectura; para «¿están al día los datos?».",
    "search_rules": "Reglas del juego (reglamento FIFA de fútbol sala y circulares de la RFEF sobre reglas y criterios arbitrales), texto "
                    "literal. Búsqueda por palabras: usa el vocabulario del reglamento (córner→saque de esquina, pista→terreno de juego, "
                    "roja→expulsión, cambios→sustituciones, portero→guardameta) y prueba otra si no aparece. Devuelve fragmentos con regla "
                    "y página.",
    "search_rules.rule": "Solo esa regla FIFA (las circulares se incluyen siempre); solo si el usuario la nombra",
}


def tools_mode(explicit: str | None = None) -> str:
    """`compact` o `legacy` (predeterminado). Misma bandera que el prompt del agente: `AGENT_PROMPT_MODE`."""
    mode = (explicit or env_value("AGENT_PROMPT_MODE", "legacy")).strip().lower()
    return mode if mode in ("legacy", "compact") else "legacy"


def overrides(mode: str) -> dict[str, str]:
    return COMPACT if mode == "compact" else {}
