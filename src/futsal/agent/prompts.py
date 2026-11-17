"""Textos del agente: el conjunto de prompts (`PromptSet`) y el módulo de las herramientas sin grupo (`MODULE_V2`), que se añade al final
del prompt de sistema. El prompt de sistema base vive en `agent.py` (`SYSTEM_PROMPT`). Un único conjunto: sin modos alternativos.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSet:
    mode: str
    system: str
    history_rule: str
    context_header: str
    catalog_header: str


MODULE_V2 = (  # herramientas sin grupo; se añade al final del prompt de sistema
    "\n\nHerramientas v2 (todas las competiciones de la temporada; por nombre y filtros, sin IDs): find_player(name, team?) jugador y su equipo; "
    "find_official(name, include_matches?) árbitro y sus partidos; officials_ranking árbitro con más partidos; matches_by_date partidos de un día o franja; "
    "club_overview(name, results?) todos los equipos de un club y sus resultados; scorers_ranking(category?) máximo goleador de varias ligas; "
    "teams_ranking(metric) equipos más/menos goleadores. Si piden «todos/todas/cualquier» (o lo responden a una aclaración), NO pidas competición, grupo ni "
    "categoría: usa estas herramientas y di qué has asumido; pregunta solo si hay varios clubes o jugadores homónimos. Traslada a la respuesta las `notes` "
    "(grupos distintos, club agrupado por nombre, cobertura parcial). Si find_player/find_official solo dan `suggestions`, di que no figura y ofrécelas como "
    "posibles sin atribuirles datos. Usa las fechas de la nota de la aplicación. Una afirmación del usuario («creo que…») se comprueba con herramientas: "
    "di si se confirma. Si count > shown y piden «todos», repite con `limit` mayor (hasta 100) o di cuántos faltan. Esto prevalece sobre pedir aclaración del contexto.")
