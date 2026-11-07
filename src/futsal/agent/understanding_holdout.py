"""Conjunto CIEGO de comprensión (ver `understanding_eval`): redacción distinta a la del conjunto de desarrollo y escrito DESPUÉS de dejar ese conjunto al 100 %.
No se ha usado para ajustar el código: se ejecuta, se registra el resultado y solo entonces se corrigen fallos reales (dejando constancia del antes y el después)."""

from typing import Any

from futsal.agent.understanding_cases import (
    DOSA_COMPS,
    GROUPS,
    MISSING,
    NODATA,
    REFUSE,
    T,
    act,
    ask,
)
from futsal.agent.understanding_eval import (
    CJF,
    CJP,
    CS1F,
    CS1P,
    CS2F,
    CS2P,
    PAA,
    PAJ,
    PREF_A,
    PREF_J,
    PRI_A,
    PRI_J,
    TER,
)

HOLDOUT: list[dict[str, Any]] = []


def S(cat: str, q: str, expect: dict[str, Any], **flags: bool) -> None:
    HOLDOUT.append({"id": f"ho-{cat[:4]}-{len(HOLDOUT) + 1:03d}", "cat": cat, "turns": [T(q, expect, **flags)]})


def M(cat: str, turns: list[dict[str, Any]], history: list[tuple[str, str]] | None = None) -> None:
    HOLDOUT.append({"id": f"ho-{cat[:4]}-{len(HOLDOUT) + 1:03d}", "cat": cat, "turns": turns, **({"history": history} if history else {})})


for q, e in [
    ("¿Qué tal va la preferente de juveniles?", act([PREF_J], "standings")), ("¿Cómo está el panorama en tercera?", act([TER], "standings")),
    ("Dime los goleadores de la liga preferente juvenil", act([PREF_J], "scorers")), ("Quiero ver la tabla de la primera juvenil grupo 4", act([PRI_J], "standings", ["Grupo 4"])),
    ("¿Quién es líder en la autonómica senior?", act([PAA], "standings")), ("Próximos partidos de la regional grupo 3", act([PRI_A], "next_match", ["Grupo 3"])),
    ("¿Cuántos equipos hay en la tercera división?", act([TER], "teams_list")), ("Clasificación copa senior II", act([CS2F, CS2P], "standings")),
    ("¿Quién va el último en la primera aficionado grupo 1?", act([PRI_A], "standings", ["Grupo 1"])), ("Goleador de la copa juvenil fase final", act([CJF], "scorers")),
    ("Equipos de la copa juvenil fase previa", act([CJP], "teams_list")), ("¿Cómo le va al Valdemoro en la liga?", act([PRI_A], "standings", ["Grupo 2"], "C.D. VALDEMORO")),
    ("Resultados recientes del Getafe Sala", act([PREF_J], "last_result", ["Grupo 2"], "GETAFE SALA")), ("¿Cuándo se enfrenta el Móstoles City?", act([PRI_A], "next_match", ["Grupo 4"], "MÓSTOLES CITY")),
    ("¿Cómo va Atlético Pinto?", act([PREF_J], "standings", ["Grupo 1"], "ATLÉTICO PINTO")), ("Puntos del Escuela Leganés", act([PRI_J], "standings", ["Grupo 1"], "ESCUELA LEGANÉS")),
    ("¿Cómo va la regional juvenil grupo 1?", act([PRI_J], "standings", ["Grupo 1"])), ("Goleadores de la copa senior I fase final", act([CS1F], "scorers"))]:
    S("directas", q, e)

for q, ex in [
    ("¿Cómo va el Dosa juvenil en la liga?", ask([PAJ, PREF_J, PRI_J])), ("¿Cómo va el Dosa en la copa senior?", act([CS1P], "standings", ["Eliminatorias"], "C.D. DOSA")),
    ("¿Cómo va el Dosa en preferente?", act([PREF_J], "standings", ["Grupo 1"], "C.D. DOSA")), ("¿Cómo va el Dosa en primera?", act([PRI_J], "standings", ["Grupo 3"], "C.D. DOSA")),
    ("¿Cómo va el Pinto en primera?", act([PRI_J], "standings", ["Grupo 3"], "PINTO C.F.S.")), ("¿Cómo anda el Alcobendas Sala?", act([PREF_A], "standings", ["Grupo 2"], "ALCOBENDAS SALA")),
    ("¿Cómo va el Leganés en la tercera?", act([TER], "standings", ["Grupo 3"], "C.D. LEGANÉS")), ("¿Cómo va el Valdemoro en la preferente juvenil?", None)]:
    if ex is None:
        S("equipos_y_ambito", q, {"action": "missing", "options": [PRI_A]})  # el equipo existe pero no en ese ámbito
    else:
        S("equipos_y_ambito", q, ex)

for q in ["¿Cómo va la liga de veteranos?", "Clasificación de la sub 23", "Partidos de la segunda b", "Goleadores de la liga benjamín", "Clasificación de la liga nacional", "¿Cómo va la tercera b?"]:
    S("inexistentes", q, MISSING)
for q in ["¿Cómo acabó la tercera en 2019?", "Clasificación final de la temporada anterior de la preferente senior", "¿Cómo quedó la autonómica juvenil hace tres años?"]:
    S("temporadas", q, NODATA)
for q in ["¿Qué hora es en Tokio?", "Escribe un poema sobre el mar", "¿Cuál es la capital de Francia?", "¿Quién ganó la Champions League?"]:
    S("fuera_de_tema", q, REFUSE)
for q, e in [
    ("¿Cómo va la primera?", ask([PRI_A, PRI_J])), ("Clasificación de primera división autonómica", ask([PAA, PAJ])), ("Goleadores de la liga", ask()),
    ("Partidos de la copa", ask([CS1F, CS1P, CS2F, CS2P, CJF, CJP])), ("Clasificación regional senior", ask(GROUPS[PRI_A])), ("Partidos de la primera juvenil", ask(GROUPS[PRI_J]))]:
    S("ambiguas", q, e)
for q, e in [("Clasificación de la prefrente senior", act([PREF_A], "standings")), ("Goleadores de terecra", act([TER], "scorers")),
             ("Clasificación de la prefernte juvenill", act([PREF_J], "standings")), ("¿Cómo va el Valdemoru en la lliga?", act([PRI_A], "standings", ["Grupo 2"], "C.D. VALDEMORO"))]:
    S("erratas", q, e)

# ------------------------------------------------------------------------------------------------------------ conversaciones
Q = "Clasificación de la copa senior I"
for turns in [
    [T(Q, act([CS1F, CS1P], "standings")), T("¿Y los goleadores?", act([CS1F, CS1P], "scorers"))],
    [T("Goleadores de la preferente senior", act([PREF_A], "scorers")), T("Y la juvenil", act([PREF_J], "scorers", assume=True))],
    [T("¿Quién va primero en la tercera?", act([TER], "standings")), T("¿Y último?", act([TER], "standings"))],
    [T("Clasificación de la primera juvenil grupo 5", act([PRI_J], "standings", ["Grupo 5"])), T("¿Y el grupo 2?", act([PRI_J], "standings", ["Grupo 2"]))],
    [T("¿Cómo va el Getafe Sala?", act([PREF_J], "standings", ["Grupo 2"], "GETAFE SALA")), T("¿Y el Atlético Pinto?", act([PREF_J], "standings", ["Grupo 1"], "ATLÉTICO PINTO"))],
    [T("¿Cómo va el Getafe Sala?", act([PREF_J], "standings", ["Grupo 2"], "GETAFE SALA")), T("¿Y los goleadores de la preferente juvenil?", act([PREF_J], "scorers"))],
    [T("¿Cómo va el Dosa?", ask(DOSA_COMPS)), T("la copa", ask([CS1P, CJF]))],
    [T("¿Cómo va el Dosa?", ask(DOSA_COMPS)), T("la preferente", act([PREF_J], "standings", ["Grupo 1"], "C.D. DOSA"))],
    [T("¿Cómo va la preferente?", ask([PREF_A, PREF_J])), T("la segunda", act([PREF_J], "standings", assume=True))],
    [T("¿Cómo va la preferente?", ask([PREF_A, PREF_J])), T("la primera", act([PREF_A], "standings", assume=True))],
    [T("Clasificación de la copa juvenil", act([CJF, CJP], "standings")), T("¿Y la senior?", ask([CS1F, CS1P, CS2F, CS2P]))],
    [T("¿Qué equipos hay en la tercera?", act([TER], "teams_list")), T("¿Y quién va primero?", act([TER], "standings"))],
    [T("Goleadores de la regional grupo 4", act([PRI_A], "scorers", ["Grupo 4"])), T("¿Y el máximo goleador del grupo 2?", act([PRI_A], "scorers", ["Grupo 2"]))],
    [T("Clasificación de la tercera grupo 4", act([TER], "standings", ["Grupo 4"])), T("Y la autonómica", act([PAA], "standings", assume=True))],
    [T("¿Cómo va el Pinto?", ask(["ATLÉTICO PINTO", "PINTO C.F.S."])), T("el segundo", act([PRI_J], "standings", ["Grupo 3"], "PINTO C.F.S.", assume=True))],
    [T("Clasificación de primera aficionado", ask(GROUPS[PRI_A])), T("el tercero", act([PRI_A], "standings", ["Grupo 3"], assume=True))],
    [T("Clasificación de la preferente senior", act([PREF_A], "standings")), T("¿Y el goleador?", act([PREF_A], "scorers"), refuse_first=True)],
    [T("Clasificación de la tercera", act([TER], "standings")), T("¿Y en la segunda división?", MISSING), T("¿Y los goleadores de la tercera?", act([TER], "scorers"))],
    [T("¿Cómo va el C.D. Valdemoro?", act([PRI_A], "standings", ["Grupo 2"], "C.D. VALDEMORO")), T("¿Y su próximo partido?", act([PRI_A], "next_match", ["Grupo 2"], "C.D. VALDEMORO")),
     T("¿Cuándo vuelve a jugar?", act([PRI_A], "next_match", ["Grupo 2"], "C.D. VALDEMORO"))],
    [T("Clasificación de la preferente juvenil", act([PREF_J], "standings")), T("¿Cómo quedó el año pasado?", NODATA), T("¿Y los partidos de este fin de semana?", act([PREF_J], "matches"))],
    [T("Goleadores de la copa senior II", act([CS2F, CS2P], "scorers")), T("¿Y de la tercera?", act([TER], "scorers"))],
    [T("¿Cómo va el Getafe Sala?", act([PREF_J], "standings", ["Grupo 2"], "GETAFE SALA")), T("¿Y su último resultado?", act([PREF_J], "last_result", ["Grupo 2"], "GETAFE SALA"), lose_memory=True)],
    [T("Clasificación de la preferente senior", act([PREF_A], "standings")), T("¿Y los partidos de este fin de semana?", act([PREF_A], "matches"), lose_memory=True)],
    [T("Clasificación de la tercera grupo 3", act([TER], "standings", ["Grupo 3"])), T("¿Y la preferente juvenil?", act([PREF_J], "standings"), lose_memory=True)]]:
    M("conversaciones", turns)
