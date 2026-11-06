"""Tier 0 (`AGENT_TIER0=on`; apagado por defecto): respuesta SIN LLM a «posición y/o puntos ACTUALES de UN equipo».

Diseño de seguridad (auditoría): LISTA BLANCA CERRADA de plantillas. La pregunta, con el equipo y el ámbito sustituidos por marcadores, debe encajar ENTERA en una
plantilla admitida; lo que no encaje (cualquier palabra no prevista) sigue por el flujo normal con LLM. No se amplía ninguna lista negra: ante la duda, se abstiene.

Solo texto construido con cifras copiadas de `get_standings` (clasificación oficial RFFM) con su procedencia (jornada y captura). Además de las comprobaciones de
datos (estado, avisos, cobertura, sanciones, fila única, jornada al día) exige FRESCURA real de la captura (`AGENT_TIER0_MAX_AGE_HOURS`, 72 h por defecto;
sin fecha de captura verificable, se abstiene). Este módulo es puro (sin red ni estado): el agente lo orquesta reutilizando `_execute`.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from futsal.agent.entities import normalize
from futsal.config.settings import env_value

DEFAULT_MAX_AGE_HOURS = 72.0  # captura de la clasificación más antigua que esto => no se responde sin LLM (la ingesta normal es más frecuente que la jornada)
TEAM, SCOPE = "zzteam", "zzscope"

_EQ = rf"(?:(?:el|la) )?{TEAM}"  # el equipo, con artículo opcional
_TABLE = r"(?:la )?(?:clasificacion|tabla)"
_POS_Q = (rf"(?:en que (?:posicion|puesto) (?:de {_TABLE} )?(?:esta|va|se encuentra) {_EQ}(?: en {_TABLE})?"
          rf"|que (?:posicion|puesto) ocupa {_EQ}(?: en {_TABLE})?"
          rf"|cual es (?:la posicion|el puesto) (?:del|de(?: la)?) {TEAM}(?: en {_TABLE})?)")
_PTS_Q = rf"cuantos puntos (?:tiene|lleva|suma) {_EQ}"
_POS_TAIL = r"en que (?:posicion|puesto) esta"
_PTS_TAIL = r"cuantos puntos tiene"
_CORES: dict[str, str] = {
    "position": _POS_Q,
    "points": _PTS_Q,
    "position_points": rf"(?:{_POS_Q} y {_PTS_TAIL}(?: el equipo)?|{_PTS_Q} y {_POS_TAIL}(?: el equipo)?)",
}
_PREFIX = r"(?:(?:oye|hola|buenas|una duda|dime|por favor) )*"
_SCOPE_BEFORE = rf"(?:(?:en|dentro de) {SCOPE} )?"
_SCOPE_AFTER = rf"(?: (?:en|de|del|dentro de) {SCOPE})?"
_SUFFIX = r"(?: (?:actualmente|ahora mismo|ahora|hoy|esta temporada|por favor))*"
_TEMPLATES = {name: re.compile(rf"^{_PREFIX}{_SCOPE_BEFORE}{core}{_SCOPE_AFTER}{_SUFFIX}$") for name, core in _CORES.items()}


def max_age_hours() -> float:
    """`AGENT_TIER0_MAX_AGE_HOURS` (por defecto 72). Un valor no numérico o no positivo cuenta como el predeterminado."""
    try:
        value = float(env_value("AGENT_TIER0_MAX_AGE_HOURS", str(DEFAULT_MAX_AGE_HOURS)))
    except ValueError:
        return DEFAULT_MAX_AGE_HOURS
    return value if value > 0 else DEFAULT_MAX_AGE_HOURS


def candidate(question: str) -> bool:
    """Filtro BARATO previo al catálogo (solo evita cargarlo): hay alguna palabra de posición/puntos. No decide nada: la lista blanca la aplica `match`."""
    return bool(set(normalize(question).split()) & {"posicion", "puesto", "puntos"})


def _bounded(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])")


def mask(question: str, team_form: str, scope_phrases: list[str]) -> tuple[str, list[str]] | None:
    """(pregunta normalizada con `zzteam` y `zzscope`, ámbitos encontrados). None si el equipo no aparece exactamente una vez."""
    text = normalize(question)
    pattern = _bounded(normalize(team_form))
    if len(pattern.findall(text)) != 1:
        return None
    text = pattern.sub(TEAM, text)
    found: list[str] = []
    for phrase in sorted({normalize(p) for p in scope_phrases if normalize(p)}, key=len, reverse=True):
        rx = _bounded(phrase)
        if rx.search(text):
            found.append(phrase)
            text = rx.sub(SCOPE, text)
    return re.sub(rf"(?:{SCOPE} )+{SCOPE}", SCOPE, text), found


def match(question: str, team_form: str, scope_phrases: list[str]) -> tuple[str, list[str]] | None:
    """(plantilla, ámbitos mencionados) si la pregunta ENTERA encaja en una plantilla admitida; None en cualquier otro caso."""
    masked = mask(question, team_form, scope_phrases)
    if masked is None:
        return None
    text, found = masked
    for name, rx in _TEMPLATES.items():
        if rx.match(text):
            return name, found
    return None


def _captured(source: dict[str, Any]) -> datetime | None:
    raw = source.get("captured_at")
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)  # Python >= 3.11 admite la «Z» final
    except ValueError:
        return None
    return when if when.tzinfo is not None else None  # sin zona horaria no se puede comparar con seguridad


def compose(data: Any, team_id: int, *, now: datetime | None = None, max_age: float | None = None) -> tuple[str, dict[str, Any]] | None:
    """(texto, procedencia) o None si los datos no bastan para responder con seguridad (el flujo normal con LLM sigue)."""
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("warning"):
        return None
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    if coverage.get("complete") is False or coverage.get("warnings"):
        return None
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    rows = [r for r in data.get("standings") or [] if isinstance(r, dict)]
    mine = [r for r in rows if r.get("team_id") == team_id]
    if len(mine) != 1 or not rows:
        return None
    row = mine[0]
    needed = ("position", "points", "played", "won", "drawn", "lost", "goals_for", "goals_against", "team")
    if any(row.get(k) is None for k in needed) or row.get("penalty_points"):
        return None
    rnd, current = source.get("round"), source.get("current_round")
    captured = _captured(source)
    if rnd is None or captured is None or (current is not None and rnd < current):  # sin jornada/captura verificable, o atrasada respecto a la RFFM
        return None
    limit = max_age if max_age is not None else max_age_hours()
    age = (now or datetime.now(UTC)) - captured
    if age > timedelta(hours=limit) or age < timedelta(hours=-1):  # captura obsoleta, o fechada en el futuro (reloj o dato incoherente)
        return None
    where = f"{data.get('competition', '')} ({data.get('group', '')})"
    text = (f"{row['team']} ocupa la posición {row['position']} de {len(rows)} equipos en {where}, con {row['points']} puntos "
            f"({row['played']} partidos jugados: {row['won']} ganados, {row['drawn']} empatados y {row['lost']} perdidos; "
            f"goles a favor {row['goals_for']} y en contra {row['goals_against']}).\n\n"
            f"Fuente: clasificación oficial de la RFFM, jornada {rnd}, captura del {captured.date().isoformat()}.")
    return text, {"tool": "get_standings", "group_id": data.get("group_id"), "round": rnd, "captured": captured.isoformat(), "age_hours": round(age.total_seconds() / 3600, 1)}
