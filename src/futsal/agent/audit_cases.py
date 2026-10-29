"""Casos de la Fase 17: más grupos y competiciones, estados de las tablas oficiales y consultas que combinan fuentes.

Se suman a los de las fases 8-16 sin duplicarlos. Los identificadores (grupos, equipos, jugadores) salen de la verdad cargada de
PostgreSQL (`Truth`), nunca de constantes. Sin la precondición un caso queda no evaluable con el motivo: no se aprueba por defecto.
"""

from futsal.agent.evaluate import EvalCase, Fact, M, Plan, TableGroup, Truth, _call, key, norm
from futsal.agent.llm import ToolCall

STD, SCO = frozenset({"get_standings"}), frozenset({"get_scorer_table"})
DISCOVERY = frozenset({"list_competitions", "list_groups", "find_teams"})  # descubrir el grupo sin usar resultados ni actas
LM = frozenset({"list_matches"})


def _where(tg: TableGroup) -> str:
    return f"{tg.competition} ({tg.name})"


def _leader_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [r for r in rows if r["goals"] == rows[0]["goals"]][:4] if rows else []


def _table_cases(t: Truth) -> list[EvalCase]:  # noqa: C901 - catálogo declarativo
    main = next((g for g in t.table_groups if g.group_id == t.group_id), None)
    ok_main = main is not None and main.standings_status == "ok" and bool(main.standings)
    last = max(main.standings) if ok_main and main else None
    rows = main.standings[last] if ok_main and main and last is not None else []
    g = t.group_id
    cases: list[EvalCase] = []

    def uniq(row: dict[str, object]) -> bool:
        return sum(key(str(r["team"])) == key(str(row["team"])) for r in rows) == 1

    # --- B. clasificación: un equipo, comparación y otro grupo/competición
    mid = rows[len(rows) // 2] if len(rows) >= 3 and uniq(rows[len(rows) // 2]) else None
    cases.append(EvalCase(
        "tabla_clasificacion_posicion_equipo", "tablas_multigrupo",
        f"En {_where(main)}, ¿en qué posición de la clasificación está {mid['team']} y cuántos puntos tiene?" if mid and main else "",
        [Fact("table_row", "puesto oficial", names=(str(mid["team"]),), number=int(str(mid["position"])), status="team:position"),
         Fact("table_row", "puntos oficiales", names=(str(mid["team"]),), number=int(str(mid["points"])), status="team:points")] if mid else None,
        STD | DISCOVERY, lambda s, p: _call("get_standings", group_id=g) if s == 0 else None,
        "" if mid else "no hay clasificación oficial ingerida con equipos distinguibles", required=STD, max_calls=2))
    pair = (rows[1], rows[-1]) if len(rows) >= 4 and uniq(rows[1]) and uniq(rows[-1]) else None
    cases.append(EvalCase(
        "tabla_clasificacion_comparacion", "tablas_multigrupo",
        f"En {_where(main)}, compara en la clasificación a {pair[0]['team']} y {pair[1]['team']}: ¿qué puesto y cuántos puntos tiene cada uno?" if pair and main else "",
        [f for r in pair for f in (Fact("table_row", "puesto", names=(str(r["team"]),), number=int(str(r["position"])), status="team:position"),
                                    Fact("table_row", "puntos", names=(str(r["team"]),), number=int(str(r["points"])), status="team:points"))]
        if pair else None,
        STD | DISCOVERY, lambda s, p: _call("get_standings", group_id=g) if s == 0 else None,
        "" if pair else "no hay clasificación oficial ingerida con al menos 4 equipos", required=STD, max_calls=2))
    other = next((x for x in sorted(t.table_groups, key=lambda x: x.competition == (main.competition if main else ""))
                  if x.group_id != g and x.standings_status == "ok" and x.standings), None)
    o_rows = other.standings[max(other.standings)] if other else []
    cases.append(EvalCase(
        "tabla_clasificacion_otro_grupo", "tablas_multigrupo",
        f"En {_where(other)}, ¿quién lidera la clasificación y con cuántos puntos?" if other else "",
        [Fact("table_row", "líder oficial del otro grupo", names=(str(o_rows[0]["team"]),), number=int(str(o_rows[0]["points"])), status="team:points"),
         Fact("table_round", "jornada de esa tabla", number=max(other.standings))] if other and o_rows else None,
        STD | DISCOVERY, lambda s, p: _call("get_standings", group_id=other.group_id) if s == 0 and other else None,
        "" if other else "solo hay un grupo con clasificación ingerida", required=STD, max_calls=4))

    # --- B/C. estados de las tablas: sin datos, no ingerido, error, parcial (el agente no debe reconstruir nada)
    def status_case(case_id: str, kind: str, status: str, label: str, question: str, tool: str, allowed: frozenset[str]) -> EvalCase:
        tg = next((x for x in t.table_groups if getattr(x, f"{kind}_status") == status), None)
        return EvalCase(
            case_id, "tablas_estados", question.format(w=_where(tg)) if tg else "",
            [Fact("table_status", label, names=(tg.competition, tg.name), status=status)] if tg else None,
            allowed | DISCOVERY, (lambda s, p: _call(tool, group_id=tg.group_id) if s == 0 else None) if tg else None,
            "" if tg else f"ningún grupo de esta base tiene {kind} en estado «{status}» (se cubre con fixtures)",
            required=frozenset({tool}), max_calls=3, forbid_scores=True)

    cases += [
        status_case("tabla_clasificacion_eliminatorias", "standings", "not_applicable", "grupo de eliminatorias: no hay clasificación",
                    "¿Cómo va la clasificación de {w}?", "get_standings", STD),
        status_case("tabla_clasificacion_no_ingerida", "standings", "not_ingested", "clasificación aún no ingerida",
                    "¿Quién lidera la clasificación de {w}?", "get_standings", STD),
        status_case("tabla_clasificacion_fallo_ingesta", "standings", "failed", "la última ingesta falló y no hay captura",
                    "¿Quién lidera la clasificación de {w}?", "get_standings", STD),
        status_case("tabla_clasificacion_incompleta", "standings", "incomplete", "clasificación con equipos de menos",
                    "¿Cuántos puntos tiene el líder de {w}?", "get_standings", STD),
        status_case("tabla_goleadores_vacia", "scorers", "empty", "tabla válida sin goleadores",
                    "¿Quién es el máximo goleador de {w}?", "get_scorer_table", SCO),
        status_case("tabla_goleadores_no_ingerida", "scorers", "not_ingested", "goleadores aún no ingeridos",
                    "¿Quién es el máximo goleador de {w}?", "get_scorer_table", SCO)]
    missing_round = next((n for n in sorted(t.rounds, reverse=True) if main and n not in main.standings), None) if ok_main else None
    cases.append(EvalCase(
        "tabla_clasificacion_jornada_no_ingerida", "tablas_estados",
        f"¿Cómo estaba la clasificación tras la jornada {missing_round}? ¿Quién lideraba?" if missing_round else "",
        [Fact("table_status", "jornada sin clasificación ingerida", names=(t.competition, t.group_name), status="round_not_ingested")] if missing_round else None,
        STD | DISCOVERY, (lambda s, p: _call("get_standings", group_id=g, round=missing_round) if s == 0 else None) if missing_round else None,
        "" if missing_round else "todas las jornadas del grupo tienen clasificación ingerida", required=STD, max_calls=3, forbid_scores=True))

    # --- C. goleadores: top N, otro grupo, nombres repetidos y lo que la RFFM no publica
    sc = t.official_scorers
    n_top = next((n for n in (5, 4, 3) if len(sc) > n and sc[n - 1]["goals"] > sc[n]["goals"] or len(sc) == n), None) if sc else None
    cases.append(EvalCase(
        "tabla_goleadores_top_n", "tablas_multigrupo",
        f"En {_where(main)}, ¿quiénes son los {n_top} máximos goleadores y cuántos goles lleva cada uno?" if n_top and main else "",
        [Fact("table_row", "goleador oficial", names=(str(r["player"]),), number=int(str(r["goals"])), status="player:goals") for r in sc[:n_top]]
        if n_top else None,
        SCO | DISCOVERY, lambda s, p: _call("get_scorer_table", group_id=g, limit=n_top) if s == 0 and n_top else None,
        "" if n_top else "no hay tabla oficial de goleadores con un corte limpio (sin empate en el último puesto)", required=SCO, max_calls=2))
    o_sc = next((x for x in sorted(t.table_groups, key=lambda x: x.competition == (main.competition if main else ""))
                 if x.group_id != g and x.scorers_status == "ok" and x.scorers), None)
    lead = _leader_rows(o_sc.scorers) if o_sc else []
    cases.append(EvalCase(
        "tabla_goleadores_otro_grupo", "tablas_multigrupo",
        f"¿Quién es el máximo goleador de {_where(o_sc)} y cuántos goles lleva?" if o_sc else "",
        [Fact("table_row", "máximo goleador oficial del otro grupo", names=(str(r["player"]),), number=int(str(r["goals"])), status="player:goals")
         for r in lead] if o_sc and lead else None,
        SCO | DISCOVERY, lambda s, p: _call("get_scorer_table", group_id=o_sc.group_id, limit=10) if s == 0 and o_sc else None,
        "" if o_sc else "solo hay un grupo con goleadores ingeridos", required=SCO, max_calls=4))
    dup = None
    for tg in t.table_groups:
        names = [norm(str(r["player"])) for r in tg.scorers]
        again = next((n for n in names if names.count(n) > 1), None)
        if again:
            dup = (tg, [r for r in tg.scorers if norm(str(r["player"])) == again])
            break
    cases.append(EvalCase(
        "tabla_goleadores_nombre_repetido", "tablas_multigrupo",
        f"¿Cuántos goles lleva {dup[1][0]['player']} según la tabla de goleadores de {_where(dup[0])}?" if dup else "",
        [Fact("table_row", "una de las filas con ese nombre", names=(str(r["player"]),), number=int(str(r["goals"])), status="player:goals")
         for r in dup[1]] if dup else None,
        SCO | DISCOVERY, (lambda s, p: _call("get_scorer_table", group_id=dup[0].group_id, name=str(dup[1][0]["player"])) if s == 0 else None) if dup else None,
        "" if dup else "ninguna tabla de goleadores ingerida repite un nombre", required=SCO, max_calls=3))
    cases.append(EvalCase(
        "tabla_goleadores_por_jornada_no_verificable", "tablas_estados",
        "¿Quién era el máximo goleador del grupo tras la jornada 2?",
        [Fact("limitation", "la RFFM solo publica la tabla de goleadores actual, sin histórico por jornada")] if sc else None,
        SCO | DISCOVERY | frozenset({"list_rounds"}), None,
        "no hay tabla oficial de goleadores ingerida" if not sc else "la tabla de goleadores no tiene histórico por jornada: no se puede verificar",
        max_calls=3, forbid_scores=True, expect="unverifiable"))

    # --- F. consultas que combinan fuentes oficiales y partidos
    lead_row = rows[0] if rows else None
    lid = lead_row.get("team_id") if lead_row else None
    fin = sorted((m for m in t.of_team(int(str(lid)), "finished") if m.hs is not None), key=lambda m: (m.round_number, m.id)) if lid in t.teams else []
    pend = sorted((m for m in t.of_team(int(str(lid)), "pending") if m.day), key=lambda m: (m.day, m.id)) if lid in t.teams else []

    def two_steps(second: ToolCall | None) -> Plan:
        return lambda s, p: _call("get_standings", group_id=g) if s == 0 else (
            [second] if s == 1 and second is not None else None)

    cases.append(EvalCase(
        "combinada_lider_ultimo_resultado", "combinada_fuentes", f"En {_where(main)}, ¿quién lidera el grupo y cuál fue su último resultado?" if main else "",
        # la pregunta no pide los puntos del líder (como en el próximo partido): basta el partido; el líder se comprueba con la tool obligatoria
        [Fact("match", "último partido del líder", (fin[-1],), need_score=True, exclude=str(lead_row["team"]))] if fin and lead_row else None,
        STD | LM | DISCOVERY | frozenset({"get_match", "list_teams"}),
        two_steps(ToolCall("c-list_matches", "list_matches", {"group_id": g, "team_id": lid, "status": "finished"})) if fin else None,
        "" if fin else "no hay clasificación oficial ingerida o el líder no tiene partidos finalizados", required=STD | LM, max_calls=3))
    cases.append(EvalCase(
        "combinada_lider_proximo_partido", "combinada_fuentes", f"En {_where(main)}, ¿cuál es el próximo partido del líder del grupo?" if main else "",
        # la pregunta no pide los puntos del líder: basta que el partido nombre a su equipo (el líder se comprueba con la tool obligatoria)
        [Fact("match", "próximo partido del líder", (pend[0],), need_date=True)] if pend and lead_row else None,
        STD | LM | DISCOVERY | frozenset({"get_match", "list_teams"}),
        two_steps(ToolCall("c-list_matches", "list_matches", {"group_id": g, "team_id": lid, "status": "scheduled"})) if pend else None,
        "" if pend else "no hay clasificación oficial ingerida o el líder no tiene partidos programados con fecha", required=STD | LM, max_calls=3))
    # el nombre del equipo se escribe distinto en ambas tablas («RODILLITO B» / «RODILLITO 'B'»): se enlaza por team_id
    main_sc = next((x.scorers for x in t.table_groups if x.group_id == g), [])
    tops = _leader_rows(main_sc)
    by_id = {r["team_id"]: r for r in rows if r.get("team_id") is not None}
    team_rows = {r["team_id"]: by_id[r["team_id"]] for r in tops if r.get("team_id") in by_id}
    linked = bool(tops) and len(team_rows) == len({r.get("team_id") for r in tops})
    cases.append(EvalCase(
        "combinada_goleador_y_puesto_de_su_equipo", "combinada_fuentes",
        f"En {_where(main)}, ¿quién es el máximo goleador del grupo y en qué puesto de la clasificación está su equipo?" if main else "",
        [Fact("table_row", "máximo goleador oficial", names=(str(r["player"]),), number=int(str(r["goals"])), status="player:goals") for r in tops]
        + [Fact("table_row", "puesto oficial de su equipo", names=(str(v["team"]),), number=int(str(v["position"])), status="team:position")
           for v in team_rows.values()] if linked else None,
        STD | SCO | DISCOVERY, lambda s, p: (_call("get_scorer_table", group_id=g, limit=5) if s == 0 else
                                 _call("get_standings", group_id=g) if s == 1 else None),
        "" if linked else "hace falta clasificación y goleadores oficiales ingeridos y enlazados por equipo",
        required=STD | SCO, max_calls=3))

    # --- A. confusión de grupos: resultados de un equipo de OTRA competición
    o = t.other
    team = next((i for i in sorted(o.teams) if any(m.status == "finished" and i in (m.home_id, m.away_id) for m in o.matches)), None) if o else None
    last_m: M | None = max((m for m in o.of_team(team, "finished") if m.hs is not None), key=lambda m: (m.round_number, m.id), default=None) \
        if o and team is not None else None
    cases.append(EvalCase(
        "resultados_otro_grupo", "resultados",
        f"¿Cómo terminó el último partido jugado de {o.teams[team]} en {o.competition} ({o.group_name})?" if o and team is not None and last_m else "",
        [Fact("match", "último partido jugado en otro grupo", (last_m,), need_score=True, exclude=o.teams[team])] if o and team is not None and last_m else None,
        # get_match_report solo en ESTE caso: el prompt del agente le pide, para el último partido de un equipo, comprobar el acta con su id
        # (marcador vs acta); es la misma lectura que ya permite `enfrentamiento_ganador`. El resto de límites (4 llamadas) se mantiene.
        LM | DISCOVERY | frozenset({"get_match", "get_match_report", "list_teams"}),
        # el equipo se descubre antes (list_teams): un team_id solo se acepta si una tool lo mostró
        (lambda s, p: _call("list_teams", group_id=o.group_id) if s == 0 else
         _call("list_matches", group_id=o.group_id, team_id=team, status="finished") if s == 1 else None) if o and team is not None else None,
        "" if last_m else "no hay un segundo grupo con partidos finalizados (no se puede detectar la confusión de grupos)", required=LM, max_calls=4))
    return cases


def audit_cases(t: Truth) -> list[EvalCase]:
    return _table_cases(t)
