"""Genera las fixtures HTML de clasificación y goleadores (estructura verificada en la web real de la RFFM, datos SINTÉTICOS).

Los nombres de equipos y jugadores son inventados: no se guardan datos personales reales. Ejecutar desde la raíz:
`python tests/fixtures/rffm_tables/build_fixtures.py`. Los tests leen los ficheros generados.
"""
import copy
import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent
COMP, GROUP = "90000001", "90000002"
TEAMS = [("91000001", "C.D. EJEMPLO UNO"), ("91000002", "F.S. EJEMPLO DOS"), ("91000003", "A.D. EJEMPLO TRES"),
         ("91000004", "C.D.E. EJEMPLO CUATRO"), ("91000005", "ESC. EJEMPLO CINCO"), ("91000006", "C.D. EJEMPLO SEIS 'B'")]


def page(props: dict[str, Any]) -> str:
    data = {"props": {"pageProps": props}, "page": "/competicion/x", "buildId": "fixture"}
    return ('<!DOCTYPE html><html><head><title>fixture</title></head><body><div id="__next"></div>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></body></html>')


def row(pos: int, code: str, name: str, pj: int, g: int, e: int, p: int, gf: int, gc: int, pts: int, **extra: Any) -> dict[str, Any]:
    base = {"color": "#41FF1A" if pos == 1 else "", "posicion": str(pos), "url_img": "/img/x.png", "codequipo": code, "nombre": name,
            "jugados": str(pj), "ganados": str(g), "perdidos": str(p), "empatados": str(e), "penaltis": "0",
            "goles_a_favor": str(gf), "goles_en_contra": str(gc), "jugados_casa": str(pj // 2), "ganados_casa": "1",
            "empatados_casa": "0", "perdidos_casa": "0", "jugados_fuera": str(pj - pj // 2), "ganados_fuera": "1",
            "empatados_fuera": "0", "perdidos_fuera": "0", "puntos": str(pts), "puntos_sancion": "0", "puntos_local": "3",
            "puntos_visitante": "3", "mostrar_coeficiente": "0", "coeficiente": "",
            "racha_partidos": [{"tipo": "G", "color": "#04B431"}, {"tipo": "E", "color": "#D7DF01"}]}
    base.update({k: str(v) for k, v in extra.items()})
    return base


def standings(rnd: int, rows: list[dict[str, Any]], date: str, current: int = 4, **over: Any) -> dict[str, Any]:
    st = {"estado": "1", "sesion_ok": "1", "competicion": "LIGA EJEMPLO FS", "codigo_competicion": COMP, "grupo": "Grupo 1",
          "codigo_grupo": GROUP, "tipo_competicion": "2", "jornada": str(rnd), "fecha_jornada": date, "clasificacion": rows,
          "promociones": [{"orden": "1", "nombre_promocion": "ASCENSO", "color_promocion": "#41FF1A"},
                          {"orden": "2", "nombre_promocion": "DESCENSO", "color_promocion": "#FF491C"}], "host": "https://appweb.rffm.es/"}
    st.update(over)
    return {"standings": st, "rounds": {"estado": "1", "currentRound": str(current),
                                         "jornadas": [{"codjornada": str(i), "nombre": str(i)} for i in range(1, 11)]},
            "season": {"cod_temporada": "99", "nombre": "2099-2100"}, "idGameType": 3}


FULL_ROWS = [row(1, *TEAMS[0], 4, 3, 1, 0, 19, 11, 10), row(2, *TEAMS[1], 3, 3, 0, 0, 12, 4, 9), row(3, *TEAMS[2], 4, 2, 3, 0, 14, 8, 9),
             row(4, *TEAMS[3], 4, 1, 1, 2, 9, 12, 4), row(5, *TEAMS[4], 4, 1, 0, 3, 6, 15, 3), row(6, *TEAMS[5], 4, 0, 1, 3, 5, 15, 1)]
R1_ROWS = [row(1, *TEAMS[2], 1, 1, 0, 0, 5, 1, 3), row(2, *TEAMS[0], 1, 1, 0, 0, 4, 2, 3), row(3, *TEAMS[1], 1, 0, 1, 0, 2, 2, 1),
           row(4, *TEAMS[3], 1, 0, 1, 0, 2, 2, 1), row(5, *TEAMS[4], 1, 0, 0, 1, 2, 4, 0), row(6, *TEAMS[5], 1, 0, 0, 1, 1, 5, 0)]


def scorer(code: str, name: str, team: tuple[str, str], pj: int, goals: int, pen: int = 0) -> dict[str, str]:
    return {"codigo_jugador": code, "foto": "", "jugador": name, "escudo_equipo": "/img/y.png", "nombre_equipo": team[1],
            "codigo_equipo": team[0], "partidos_jugados": str(pj), "goles": str(goals), "goles_penalti": str(pen),
            "goles_por_partidos": f"{goals / pj:.2f}"}


def scorers(rows: list[dict[str, str]], **over: Any) -> dict[str, Any]:
    group = {"codigo": GROUP, "nombre": "Grupo 1", "total_jornadas": "10", "total_equipos": "6", "clasificacion_porteros": "0",
             "ver_clasificacion": "1", "clasificacion_goleadores": "1"}
    props = {"scorers": {"estado": "1", "sesion_ok": "1", "competicion": "LIGA EJEMPLO FS", "grupo": "Grupo 1", "goles": rows},
             "group": group, "competition": {"codigo": COMP, "nombre": "LIGA EJEMPLO FS"}, "season": {"cod_temporada": "99"}, "idGameType": 3}
    props.update(over)
    return props


# empate a 4 goles entre tres jugadores; dos jugadores con el MISMO nombre y códigos distintos; un jugador con el mismo nombre en dos equipos
SCORER_ROWS = [scorer("7000001", "GARCIA LOPEZ, ANA", TEAMS[0], 4, 6), scorer("7000002", "MARTIN RUIZ, LUIS", TEAMS[1], 3, 4),
               scorer("7000003", "PEREZ SANZ, CARLA", TEAMS[2], 4, 4, 1), scorer("7000004", "GARCIA LOPEZ, ANA", TEAMS[3], 4, 4),
               scorer("7000005", "SOTO VEGA, IVAN", TEAMS[0], 4, 2), scorer("7000006", "NIETO GIL, PAU", TEAMS[4], 2, 1)]


def main() -> None:
    files: dict[str, str] = {
        "standings_round4_full.html": page(standings(4, FULL_ROWS, "17-10-2099")),
        "standings_round1.html": page(standings(1, R1_ROWS, "26-09-2099")),
        "standings_empty.html": page(standings(1, [], "26-09-2099", current=1)),
        "standings_null.html": page({"standings": None, "rounds": None}),
        "standings_cup.html": page({"standings": {"estado": "1", "sesion_ok": "1", "competicion": "COPA EJEMPLO FS", "codigo_competicion": COMP,
                                                  "grupo": "Eliminatorias", "codigo_grupo": GROUP, "tipo_competicion": "1",
                                                  "fecha_jornada": "13-03-2099", "nombre_eliminatoria": "Ronda",
                                                  "eliminatoria": [{"nombre": "Cuartos"}], "ganadores": []},
                                    "rounds": {"estado": "1", "currentRound": "1"}}),
        "scorers_full.html": page(scorers(SCORER_ROWS)),
        "scorers_empty.html": page(scorers([])),
        "scorers_null.html": page({"scorers": None, "group": None, "competition": None}),
    }
    bad = copy.deepcopy(standings(4, FULL_ROWS, "17-10-2099"))
    del bad["standings"]["clasificacion"][2]["puntos"]
    files["standings_structure_changed.html"] = page(bad)
    wrong = standings(4, FULL_ROWS, "17-10-2099", codigo_grupo="90000999")
    files["standings_other_group.html"] = page(wrong)
    partial = copy.deepcopy(standings(4, FULL_ROWS, "17-10-2099"))
    partial["standings"]["clasificacion"][4]["jugados"] = "x"
    files["standings_incomplete_row.html"] = page(partial)
    sbad = scorers(copy.deepcopy(SCORER_ROWS))
    del sbad["scorers"]["goles"][1]["goles"]
    files["scorers_structure_changed.html"] = page(sbad)
    sinc = scorers(copy.deepcopy(SCORER_ROWS))
    sinc["scorers"]["goles"][3]["jugador"] = ""
    files["scorers_incomplete_row.html"] = page(sinc)
    for name, html in files.items():
        (OUT / name).write_text(html, encoding="utf-8")
    print(f"{len(files)} fixtures en {OUT}")


if __name__ == "__main__":
    main()
