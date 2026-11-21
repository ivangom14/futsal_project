"""Fase 3E · base del resultado y estados especiales (sin BD): parser de la RFFM, dominio y hash estable.

Las filas imitan el JSON real de `/api/results` (mismas claves; nombres anonimizados). Casos: los cuatro detectados en los datos
reales de fútbol 11 (equipo retirado, marcador con acta sin cerrar, «RESOLUCIÓN RFFM», «Suspensión Masiva») y partidos normales
(finalizado y programado), que deben quedar exactamente como antes. El parser es el mismo para fútbol sala y fútbol 11."""

from datetime import date
from typing import Any

import pytest

from futsal.domain import match_result as mr
from futsal.importer.transform import content_hash
from futsal.ingestion.rffm.parser import _match as parse_match
from futsal.ingestion.rffm.results_api import parse_results_api


def raw(**over: Any) -> dict[str, Any]:
    base = {"codacta": "1000001", "hay_actas": "1", "acta_cerrada": "1", "situacion_juego": "1", "observaciones": "",
            "fecha": "15/11/2026", "hora": "12:00", "campojuego": "CAMPO A", "codigo_campo": "1", "estado": "1", "motivo_estado": "",
            "partido_en_juego": "0", "ResultadoProvisional": "0", "CodEquipo_local": "11", "Nombre_equipo_local": "EQUIPO LOCAL",
            "Retirado_local": "0", "Goles_casa": "2", "CodEquipo_visitante": "22", "Nombre_equipo_visitante": "EQUIPO VISITANTE",
            "Retirado_visitante": "0", "Goles_visitante": "1"}
    return {**base, **over}


def parsed(**over: Any) -> tuple[str, str | None, int | None, int | None]:
    m = parse_match(raw(**over), "https://www.rffm.es/api/results?idGroup=1&round=1")
    return m.status, m.result_basis, m.home_score, m.away_score


def test_normal_finished_and_scheduled_matches_are_unchanged() -> None:
    assert parsed() == ("finished", None, 2, 1)
    assert parsed(acta_cerrada="0", estado="0", Goles_casa="", Goles_visitante="") == ("scheduled", None, None, None)


def test_withdrawn_team_is_an_administrative_result_never_a_played_match() -> None:
    # caso real: 0-3 asignado a un partido del 22-11 (fecha futura), acta sin cerrar, local retirado
    status, basis, h, a = parsed(Retirado_local="1", acta_cerrada="0", Goles_casa="0", Goles_visitante="3", fecha="22/11/2026")
    assert (status, basis, h, a) == ("unknown", mr.WITHDRAWAL_HOME, 0, 3)  # el marcador oficial se conserva tal cual
    assert parsed(Retirado_visitante="1", acta_cerrada="0", Goles_casa="3", Goles_visitante="0")[:2] == ("unknown", mr.WITHDRAWAL_AWAY)
    assert parsed(Retirado_local="1", acta_cerrada="0", estado="0", Goles_casa="", Goles_visitante="")[:2] == ("scheduled", mr.WITHDRAWAL_HOME)
    # resultado administrativo definitivo (acta cerrada): finalizado oficialmente, pero NO disputado
    status, basis, *_ = parsed(Retirado_visitante="1", acta_cerrada="1", Goles_casa="3", Goles_visitante="0")
    assert (status, basis) == ("finished", mr.WITHDRAWAL_AWAY) and mr.played(status, basis) is False


def test_score_with_open_report_is_unconfirmed() -> None:
    status, basis, *_ = parsed(acta_cerrada="0")
    assert (status, basis) == ("unknown", mr.UNCONFIRMED) and mr.played(status, basis) is None


def test_federation_resolution() -> None:
    status, basis, *_ = parsed(acta_cerrada="0", motivo_estado="RESOLUCIÓN RFFM", Goles_casa="0", Goles_visitante="3")
    assert (status, basis) == ("unknown", mr.RESOLUTION) and mr.played(status, basis) is False


@pytest.mark.parametrize("reason", ["Suspensión Masiva", "SUSPENSIÓN", "suspension", "Suspendido", "SUSPENDIDO POR LLUVIA"])
def test_suspension_is_detected_with_accents_and_case_variants(reason: str) -> None:
    status, basis, h, _ = parsed(acta_cerrada="0", motivo_estado=reason)
    assert status == "suspended" and basis is None and h == 2  # el marcador (si lo hay) se conserva, pero no es un resultado
    assert mr.played(status, basis) is False


@pytest.mark.parametrize(("reason", "expected"), [("Aplazado", "postponed"), ("APLAZAMIENTO", "postponed"), ("Anulado", "cancelled"),
                                                  ("Partido cancelado", "cancelled")])
def test_other_status_keywords_keep_working(reason: str, expected: str) -> None:
    assert parsed(acta_cerrada="0", Goles_casa="", Goles_visitante="", motivo_estado=reason)[0] == expected


def test_live_or_provisional_score() -> None:
    assert parsed(acta_cerrada="0", partido_en_juego="1")[:2] == ("unknown", mr.PROVISIONAL)
    assert parsed(acta_cerrada="0", ResultadoProvisional="1")[:2] == ("unknown", mr.PROVISIONAL)


def test_results_api_carries_the_basis() -> None:
    payload = {"codigo_grupo": "1", "jornada": "1", "codigo_competicion": "9", "nombre_jornada": "Jornada 1",
               "partidos": [raw(), raw(codacta="1000002", Retirado_local="1", acta_cerrada="0", Goles_casa="0", Goles_visitante="3")]}
    res = parse_results_api(payload, "1", "1")
    assert [m.match.result_basis for m in res.matches] == [None, mr.WITHDRAWAL_HOME]  # type: ignore[union-attr]


def test_hash_of_a_normal_match_is_the_same_as_before_phase_3e() -> None:
    # valor calculado con la fórmula anterior (sin result_basis): reimportar un partido normal no crea observaciones
    import hashlib
    import json
    old = hashlib.sha256(json.dumps({"status": "finished", "date": "2026-11-15", "time": None, "home_score": 2, "away_score": 1},
                                    sort_keys=True).encode()).hexdigest()
    assert content_hash("finished", date(2026, 11, 15), None, 2, 1) == old
    assert content_hash("finished", date(2026, 11, 15), None, 2, 1, None) == old
    assert content_hash("unknown", date(2026, 11, 15), None, 0, 3, mr.WITHDRAWAL_HOME) != content_hash(
        "unknown", date(2026, 11, 15), None, 0, 3)


def test_annotation_is_empty_for_normal_matches_and_explicit_for_special_ones() -> None:
    assert mr.annotation("finished", None, True) == {} and mr.annotation("scheduled", None, False) == {}
    assert mr.annotation("postponed", None, False) == {}  # sin marcador, el estado ya lo dice todo
    full = mr.annotation("unknown", mr.WITHDRAWAL_HOME, True, "EQUIPO LOCAL", "EQUIPO VISITANTE")
    assert full["played"] is False and "EQUIPO LOCAL se ha retirado" in str(full["result_note"]) and "no se disputa" in str(full["result_note"])
    assert mr.annotation("unknown", mr.WITHDRAWAL_HOME, True, compact=True) == {"basis": mr.WITHDRAWAL_HOME, "played": False}
    assert mr.annotation("unknown", None, True)["result_basis"] == mr.UNCONFIRMED  # datos antiguos sin base: nunca «disputado»
    assert mr.annotation("suspended", None, True, compact=True) == {"basis": "suspended", "played": False}
    definitive = mr.annotation("finished", mr.WITHDRAWAL_AWAY, True, "A", "B")
    assert definitive["played"] is False and "definitivo" in str(definitive["result_note"])


def test_fold_removes_accents_and_case() -> None:
    assert mr.fold("Suspensión MASIVA") == "suspension masiva" and mr.fold(None) == ""


def test_visual_rows_carry_the_basis_and_form_only_counts_played_matches() -> None:
    from futsal.agent.presentation import _form, matches_rows, v2_list_visual

    payload = {"matches": [
        {"round_number": 1, "date": "2026-11-15", "home_team": "A", "away_team": "B", "status": "finished", "home_score": 2, "away_score": 1},
        {"round_number": 2, "date": "2026-11-29", "home_team": "A", "away_team": "C", "status": "unknown", "home_score": 0, "away_score": 3,
         "result_basis": "withdrawal_home", "played": False}], "count": 2}
    rows, _ = matches_rows(payload)
    assert "basis" not in rows[0] and rows[1]["basis"] == "withdrawal_home"
    body = {"items": [{"id": 1, "date": "2026-11-29", "time": None, "comp": "X · Grupo 1", "home": "A", "away": "C", "score": "0-3",
                       "status": "unknown", "basis": "withdrawal_home", "played": False},
                      {"id": 2, "date": "2026-11-29", "time": "10:00", "comp": "X · Grupo 1", "home": "D", "away": "E"}],
            "count": 2, "offset": 0}
    v = v2_list_visual("matches_by_date", {"date_from": "2026-11-29"}, body)
    assert v is not None and [r.get("basis") for r in v["rows"]] == ["withdrawal_home", None]
    assert len(_form("A", [m for m in payload["matches"] if not m.get("result_basis")])["rows"]) == 1
