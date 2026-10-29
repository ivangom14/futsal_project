"""Barrera de calidad previa a la persistencia: ninguna respuesta vacía o incompleta sustituye datos válidos ni da por fresco un dato.

Funciones puras (sin red ni base de datos). Las importaciones nunca borran filas; la barrera decide si lo recibido puede aplicarse,
si se aplica pero NO cuenta como actualización válida (`partial`) o si se rechaza sin tocar nada.
"""

from dataclasses import dataclass, field

# estado de la ejecución de un tipo de dato en un grupo
SUCCESS, NO_CHANGES, PARTIAL, FAILED, REJECTED, NO_DATA, SKIPPED = (
    "success", "no_changes", "partial", "failed", "rejected", "no_data", "skipped")
VALID_STATUSES = (SUCCESS, NO_CHANGES)  # los únicos que avanzan la fecha de «última actualización válida»


@dataclass(frozen=True)
class GateResult:
    decision: str  # apply | apply_partial | reject
    status: str  # estado a registrar si no se aplica / punto de partida si se aplica
    reasons: list[str] = field(default_factory=list)


def assess_matches(*, download_status: str, download_error: str | None, expected_ids: tuple[str, str, str],
                   got_ids: tuple[str, str, str] | None, plan_matches: int, plan_rounds: int, stored_matches: int,
                   stored_rounds: int) -> GateResult:
    """Valida la descarga de un grupo (partidos y jornadas) antes de importarla."""
    if download_status == "no_rounds":  # la web no publica jornadas: aún no publicado, no es un error
        return GateResult("reject", NO_DATA, [download_error or "la web no publica jornadas para el grupo"])
    if download_status == "failed" or got_ids is None:
        return GateResult("reject", FAILED, [download_error or "descarga fallida"])
    if got_ids != expected_ids:
        return GateResult("reject", REJECTED, [f"identidad distinta: fichero={got_ids} esperado={expected_ids}"])
    if plan_matches == 0:
        if stored_matches:
            return GateResult("reject", REJECTED, [f"respuesta sin partidos y hay {stored_matches} almacenados: se conservan"])
        return GateResult("reject", NO_DATA, ["la fuente no publica partidos para el grupo"])
    reasons: list[str] = []
    if download_status != "downloaded":
        reasons.append(f"descarga incompleta ('{download_status}')")
    if plan_matches < stored_matches:
        reasons.append(f"la fuente trae {plan_matches} partidos y hay {stored_matches} almacenados (no se borra ninguno)")
    if plan_rounds < stored_rounds:
        reasons.append(f"la fuente trae {plan_rounds} jornadas y hay {stored_rounds} almacenadas")
    return GateResult("apply_partial" if reasons else "apply", PARTIAL if reasons else SUCCESS, reasons)


def assess_report(*, new_players: int, new_closed: bool, stored_players: int | None, stored_closed: bool | None) -> GateResult:
    """Un acta ya almacenada no se sustituye por una versión estructuralmente degradada (sin jugadores, o cerrada → abierta)."""
    if stored_players is None:
        return GateResult("apply", SUCCESS)
    reasons: list[str] = []
    if new_players == 0 and stored_players > 0:
        reasons.append(f"la nueva versión no trae jugadores y la almacenada tiene {stored_players}")
    if stored_closed and not new_closed:
        reasons.append("la nueva versión figura como no cerrada y la almacenada está cerrada")
    return GateResult("reject", REJECTED, reasons) if reasons else GateResult("apply", SUCCESS)
