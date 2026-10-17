"""Objetivo vigente de ingesta RFFM (fuente única de estos identificadores)."""

RFFM_TARGET = {
    "season": "22",
    "competition": "26738243",
    "group": "26738245",
    "game_type": "3",
}
RFFM_INITIAL_ROUND = 2

# Fase 14: competiciones senior y juvenil de fútbol sala (denominaciones de la captura del usuario).
# Se resuelven por nombre normalizado contra el catálogo real de la RFFM; los IDs nunca se fijan aquí.
CATALOG_SCOPE: tuple[tuple[str, str], ...] = (
    ("senior", "Tercera División FS"),
    ("senior", "Copa Senior I - fase final - FS"),
    ("senior", "Copa Senior I - fase previa - FS"),
    ("senior", "Primera División Autonómica Aficionado FS"),
    ("senior", "Preferente Aficionado FS"),
    ("senior", "Copa Senior II - fase final - FS"),
    ("senior", "Copa Senior II - fase previa - FS"),
    ("senior", "Primera Aficionado FS"),
    ("juvenil", "Trofeo Comunidad División Honor Juvenil FS"),
    ("juvenil", "Primera División Autonómica Juvenil FS"),
    ("juvenil", "Preferente Juvenil FS"),
    ("juvenil", "Copa Juvenil - fase final - FS"),
    ("juvenil", "Copa Juvenil - fase previa - FS"),
    ("juvenil", "Primera Juvenil FS"),
)
