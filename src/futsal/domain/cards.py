"""Tarjetas del acta de la RFFM (puro, sin E/S).

El acta solo trae `codigo_tipo_amonestacion` y `segunda_amarilla`; la RFFM no etiqueta los códigos. El significado lo ha CONFIRMADO el
responsable del proyecto (no figura en la fuente) y se comprobó contra 13 actas reales (75 tarjetas): 100 = amarilla; 100 con
`segunda_amarilla=1` = segunda amarilla del mismo jugador (7/7 tenían una amarilla anterior en el partido), que supone expulsión;
101 = roja directa. Cualquier otro código es DESCONOCIDO: se informa sin color y nunca se interpreta.
"""

YELLOW = "yellow"
SECOND_YELLOW = "second_yellow"
RED = "red"
UNKNOWN = "unknown"
KINDS = (YELLOW, SECOND_YELLOW, RED, UNKNOWN)

LABELS = {YELLOW: "Amarilla", SECOND_YELLOW: "2.ª amarilla (expulsión)", RED: "Roja directa", UNKNOWN: "Tarjeta (código sin interpretar)"}

CARDS_NOTE = ("card_kind: yellow = amarilla; second_yellow = segunda amarilla del jugador, supone expulsión; red = roja directa; unknown = "
              "código sin interpretar (sin color). Significado de los códigos RFFM 100/101 confirmado por el responsable del proyecto "
              "(el acta no lo etiqueta); source_code conserva el original.")


def card_kind(source_code: str | None, second_yellow: object = None) -> str:
    code = str(source_code).strip() if source_code is not None else ""
    flag = str(second_yellow).strip().lower() in ("1", "true")
    if code == "100":
        return SECOND_YELLOW if flag else YELLOW
    if code == "101":
        return RED
    return UNKNOWN


def summarize(kinds: list[str]) -> dict[str, int]:
    return {k: sum(1 for x in kinds if x == k) for k in KINDS}
