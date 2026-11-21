"""Base del marcador de un partido: si un resultado se DISPUTÓ o no (puro; común a fútbol sala y fútbol 11).

El estado (`matches.status`) dice en qué punto está el partido; `result_basis` dice EN QUÉ SE APOYA su marcador cuando no es el de un
partido disputado con acta cerrada. Nada de esto se deduce: sale de indicadores de la RFFM que se guardan tal cual (Fase 3E).

| result_basis      | Indicador de la RFFM                                   | ¿Disputado? |
|-------------------|--------------------------------------------------------|-------------|
| None              | acta cerrada sin indicadores (o sin marcador)          | sí si `finished`; si no, aún no |
| withdrawal_home   | `Retirado_local = 1`: el local se ha retirado          | no: resultado administrativo |
| withdrawal_away   | `Retirado_visitante = 1`                               | no: resultado administrativo |
| resolution        | `motivo_estado` con «resolución»                       | no consta: decisión federativa |
| provisional       | `partido_en_juego = 1` o `ResultadoProvisional = 1`    | en juego o provisional |
| unconfirmed       | marcador publicado con el acta SIN cerrar              | no confirmado por la fuente |
"""

import unicodedata

WITHDRAWAL_HOME, WITHDRAWAL_AWAY, RESOLUTION, PROVISIONAL, UNCONFIRMED = (
    "withdrawal_home", "withdrawal_away", "resolution", "provisional", "unconfirmed")
RESULT_BASES = (WITHDRAWAL_HOME, WITHDRAWAL_AWAY, RESOLUTION, PROVISIONAL, UNCONFIRMED)
ADMINISTRATIVE = (WITHDRAWAL_HOME, WITHDRAWAL_AWAY, RESOLUTION)
NOT_PLAYED_STATUSES = ("postponed", "suspended", "cancelled")


def fold(text: str | None) -> str:
    """Minúsculas sin tildes («Suspensión» → «suspension»): las palabras clave de la RFFM llegan con variantes."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def is_administrative(basis: str | None) -> bool:
    return basis in ADMINISTRATIVE


def played(status: str, basis: str | None) -> bool | None:
    """¿Consta que se disputó? True solo con acta cerrada y sin indicador administrativo; False si consta que no; None si no se sabe."""
    if basis in ADMINISTRATIVE or status in NOT_PLAYED_STATUSES:
        return False
    if status == "finished" and basis is None:
        return True
    return None


def result_note(status: str, basis: str | None, home_team: str | None = None, away_team: str | None = None) -> str | None:
    """Texto para quien presenta el partido (API, agente, visual). None = partido normal: no hace falta nota."""
    definitive = status == "finished"
    if basis == WITHDRAWAL_HOME or basis == WITHDRAWAL_AWAY:
        who = (home_team if basis == WITHDRAWAL_HOME else away_team) or ("el local" if basis == WITHDRAWAL_HOME else "el visitante")
        return (f"Resultado administrativo: {who} se ha retirado de la competición; el partido no se disputa"
                + (" (resultado oficial definitivo)." if definitive else " (acta sin cerrar)."))
    if basis == RESOLUTION:
        return "Resultado por resolución de la RFFM: no consta que el partido se disputara" + (
            " (resultado oficial definitivo)." if definitive else " (acta sin cerrar).")
    if basis == PROVISIONAL:
        return "Marcador provisional (partido en juego o resultado provisional de la RFFM): no es el resultado final."
    if basis == UNCONFIRMED:
        return "Marcador publicado con el acta sin cerrar: la RFFM aún no lo ha confirmado."
    if status in NOT_PLAYED_STATUSES:
        return {"postponed": "Partido aplazado.", "suspended": "Partido suspendido: el marcador, si lo hay, no es un resultado final.",
                "cancelled": "Partido anulado."}[status]
    return None


def effective_basis(status: str, basis: str | None, has_score: bool) -> str | None:
    """Base guardada o, si falta (datos anteriores a la Fase 3E sin instantánea), `unconfirmed` para un marcador con estado `unknown`."""
    if basis is None and status == "unknown" and has_score:
        return UNCONFIRMED
    return basis


def annotation(status: str, basis: str | None, has_score: bool, home_team: str | None = None, away_team: str | None = None,
               *, compact: bool = False) -> dict[str, object]:
    """Campos que se AÑADEN a un partido especial (vacío para uno normal: su respuesta no cambia ni en un byte).

    Completo: `result_basis`, `played` (False = no se disputó; None = no consta) y `result_note`. Compacto: `basis` (y
    `played: false` si consta que no se disputó). Un marcador de un partido aplazado/suspendido/anulado también se anota."""
    eff = effective_basis(status, basis, has_score)
    if eff is None and not (status in NOT_PLAYED_STATUSES and has_score):
        return {}
    was_played = played(status, eff)
    if compact:
        out: dict[str, object] = {"basis": eff or status}
        if was_played is False:
            out["played"] = False
        return out
    return {"result_basis": eff, "played": was_played, "result_note": result_note(status, eff, home_team, away_team)}


WITHDRAWALS = (WITHDRAWAL_HOME, WITHDRAWAL_AWAY)
LISTING_NOTE = ("basis/result_basis = base del marcador: withdrawal_home/withdrawal_away (equipo retirado: resultado administrativo, "
                "NO se disputó), resolution (resolución de la RFFM), provisional (en juego o provisional), unconfirmed (acta sin "
                "cerrar). Con played=false o sin played=true, no lo presentes como partido disputado ni como resultado final.")
