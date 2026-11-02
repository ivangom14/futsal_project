"""Brazos de evaluación (Fase E0): configuraciones con nombre = combinación de banderas `AGENT_*`.

Un brazo fija TODAS las banderas conocidas (no hereda nada del entorno del usuario) para que dos tandas del mismo brazo sean comparables, y
fuerza `AGENT_TELEMETRY=off`: una evaluación nunca escribe en la telemetría de uso real. Sin LLM, sin red.

Naturaleza de los datos que producen las evaluaciones (se etiqueta siempre así en los informes):
- MEDIDO: lo que informa el proveedor (tokens), el resultado de cada caso y las llamadas LLM/MCP de las trazas.
- SIMULADO: reproducciones sobre trazas guardadas (`prompt_compare`): cambian el prefijo, no ejecutan nada.
- ESTIMADO: cualquier cifra calculada antes de ejecutar (`--plan`) o por caracteres.
"""

from futsal.config.settings import env_value

FLAGS: dict[str, tuple[str, ...]] = {
    "AGENT_PROMPT_MODE": ("legacy", "compact"),
    "AGENT_PREFETCH": ("off", "on"),
    "AGENT_TELEMETRY": ("off", "on"),
    "AGENT_ROUTER": ("shadow", "off"),
}
DEFAULTS = {"AGENT_PROMPT_MODE": "legacy", "AGENT_PREFETCH": "off", "AGENT_TELEMETRY": "off", "AGENT_ROUTER": "shadow"}

# brazo -> solo lo que se aparta de DEFAULTS (la telemetría siempre apagada)
ARMS: dict[str, dict[str, str]] = {
    "legacy": {},
    "compact": {"AGENT_PROMPT_MODE": "compact"},
    "prefetch": {"AGENT_PREFETCH": "on"},
    "compact_prefetch": {"AGENT_PROMPT_MODE": "compact", "AGENT_PREFETCH": "on"},
}
BASELINE = "legacy"

# Casos del evaluador (ids de `evaluate.build_cases`). `suite:all` / `suite:smoke` usan --suite en lugar de --only.
PRESETS: dict[str, list[str]] = {
    # Fase 1: los 6 que empeoraron + los 10 que mejoraron en la primera comparación
    "phase1": ["acta_jugador_inexistente", "circular27_comunica", "circular10_anula", "circular10_saque_meta_coloquial",
               "tabla_goleadores_nombre_repetido", "tabla_goleadores_por_jornada_no_verificable", "equipo_proximo_partido",
               "acta_goleadores_ultimo", "acta_arbitro", "acta_jornada_sin_resultados", "acta_tarjetas_codigo", "acta_sustituciones_minuto",
               "regla_duracion_mitad", "circular27_fechas_rfef_fifa", "circular10_penal_no_acumulable", "combinada_lider_ultimo_resultado"],
    # Fase 3A: 8 casos con UN equipo (el prefetch debería activarse) + 4 controles donde debe abstenerse (dos equipos, inexistente, ajeno)
    "prefetch_probe": ["equipo_resultados", "equipo_proximo_partido", "equipo_recuento", "equipo_jornada_5", "proximo_campo",
                       "acta_goleadores_ultimo", "tabla_clasificacion_posicion_equipo", "resultados_otro_grupo",
                       "equipo_inexistente", "enfrentamiento_ganador", "tabla_clasificacion_comparacion", "fuera_de_alcance"],
}


def arm_env(name: str) -> dict[str, str]:
    """Entorno COMPLETO del brazo: todas las banderas conocidas, con su valor por defecto salvo las del brazo."""
    if name not in ARMS:
        raise ValueError(f"brazo desconocido {name!r}; disponibles: {', '.join(ARMS)}")
    env = {**DEFAULTS, **ARMS[name], "AGENT_TELEMETRY": "off"}
    validate_flags(env)
    return env


def validate_flags(env: dict[str, str]) -> None:
    for key, value in env.items():
        if key not in FLAGS:
            raise ValueError(f"bandera desconocida {key!r}")
        if value not in FLAGS[key]:
            raise ValueError(f"{key}={value!r} no válido; valores: {', '.join(FLAGS[key])}")


def current_flags() -> dict[str, str]:
    """Valor EFECTIVO de las banderas ahora (entorno o .env; los valores no válidos cuentan como el predeterminado, igual que en el agente)."""
    out = {}
    for key, allowed in FLAGS.items():
        value = (env_value(key, DEFAULTS[key]) or DEFAULTS[key]).strip().lower()
        out[key] = value if value in allowed else DEFAULTS[key]
    return out
