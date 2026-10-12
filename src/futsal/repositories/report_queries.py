"""Consultas de SOLO LECTURA sobre actas: disponibilidad, eventos, goleadores y coherencia con `matches`.

Siempre se usa la última observación (por `observed_at`, `id`) de cada acta. Nada se infiere: lo que no
figura en el acta (periodos, sustituciones, penaltis...) no se devuelve.
"""

import re
import unicodedata
from typing import Any, Literal

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session, aliased

from futsal.db.models import (
    Match,
    MatchEvent,
    MatchObservation,
    MatchOfficial,
    MatchPlayer,
    MatchReport,
    MatchReportObservation,
    MatchStaff,
    Player,
    PlayerTeamMembership,
    Round,
    Team,
)

SECTIONS = ("officials", "goals", "cards", "players", "staff")
DEFAULT_SECTIONS = ("officials", "goals")
EventType = Literal["goal", "own_goal", "card"]
MISSING_LISTED = 10
NOT_IN_SOURCE = ["periodos / resultado por periodos", "sustituciones", "penaltis", "minuto añadido",
                 "marcador tras cada evento"]
CARD_NOTE = "source_code es el código RFFM de la tarjeta; su significado (amarilla/roja) no está verificado"


class NotFound(LookupError):
    """Entidad inexistente en el ámbito consultado."""


def norm(text: str) -> str:
    """Minúsculas sin acentos ni signos, para comparar nombres."""
    base = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9ñ ]+", " ", "".join(c for c in base if not unicodedata.combining(c))).strip()


def latest(obs: Any) -> Any:
    """Condición: `obs` es la última observación de su acta."""
    other = aliased(MatchReportObservation)
    newest = (select(other.id).where(other.report_id == obs.report_id)
              .order_by(other.observed_at.desc(), other.id.desc()).limit(1).correlate(obs).scalar_subquery())
    return obs.id == newest


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def event_credit(event_type: str, team_id: int, home_id: int) -> str:
    """Lado al que suma el evento: el gol en propia meta suma al rival."""
    home = team_id == home_id
    return ("home" if home else "away") if event_type == "goal" else ("away" if home else "home")


# ----------------------------------------------------------------------------- cobertura
def coverage(db: Session, group_id: int, *, round_id: int | None = None, team_id: int | None = None,
             match_id: int | None = None) -> dict[str, Any]:
    """Disponibilidad de actas sobre los partidos FINALIZADOS del ámbito. Los no finalizados no cuentan."""
    obs = aliased(MatchReportObservation)
    home, away = aliased(Team), aliased(Team)
    stmt = (select(Match, Round.visible_number, home.name, away.name, MatchReport.id, obs.closed)
            .join(Round, Round.id == Match.round_id)
            .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
            .outerjoin(MatchReport, MatchReport.match_id == Match.id)
            .outerjoin(obs, (obs.report_id == MatchReport.id) & latest(obs))
            .where(Match.group_id == group_id, Match.status == "finished")
            .order_by(Round.visible_number, Match.id))
    if round_id is not None:
        stmt = stmt.where(Match.round_id == round_id)
    if team_id is not None:
        stmt = stmt.where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
    if match_id is not None:
        stmt = stmt.where(Match.id == match_id)
    rows = db.execute(stmt).all()
    missing = [{"match_id": m.id, "round_number": n, "home_team": h, "away_team": a}
               for m, n, h, a, rep, _ in rows if rep is None]
    with_report = len(rows) - len(missing)
    not_closed = sum(1 for _, _, _, _, rep, closed in rows if rep is not None and closed is not True)
    complete = bool(rows) and not missing and not_closed == 0
    if not rows:
        note = "No hay partidos finalizados en este ámbito: no hay actas que consultar."
    elif complete:
        note = f"Cobertura completa: {with_report} de {len(rows)} partidos finalizados tienen acta cerrada."
    else:
        note = (f"Cobertura parcial: {with_report} de {len(rows)} partidos finalizados tienen acta"
                f"{f' ({not_closed} sin cerrar)' if not_closed else ''}; el resultado puede ser incompleto. "
                "La ausencia de un dato en las actas disponibles no prueba que no ocurriera.")
    return {"finished_matches": len(rows), "with_report": with_report, "reports_not_closed": not_closed,
            "without_report_count": len(missing), "without_report": missing[:MISSING_LISTED],
            "complete": complete, "note": note}


# ----------------------------------------------------------------------------- acta de un partido
def _match_header(db: Session, match_id: int) -> tuple[Match, dict[str, Any]]:
    home, away = aliased(Team), aliased(Team)
    row = db.execute(select(Match, home.name, away.name, Round.visible_number)
                     .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
                     .join(Round, Round.id == Match.round_id).where(Match.id == match_id)).one_or_none()
    if row is None:
        raise NotFound(f"partido {match_id} no encontrado")
    m, h, a, n = row
    return m, {"match_id": m.id, "round_number": n, "home_team": h, "away_team": a,
               "status": m.status, "home_score": m.home_score, "away_score": m.away_score}


def match_report(db: Session, match_id: int, sections: tuple[str, ...] = DEFAULT_SECTIONS) -> dict[str, Any]:
    m, head = _match_header(db, match_id)
    obs = db.scalars(select(MatchReportObservation).join(MatchReport, MatchReport.id == MatchReportObservation.report_id)
                     .where(MatchReport.match_id == match_id)
                     .order_by(MatchReportObservation.observed_at.desc(), MatchReportObservation.id.desc())
                     ).first()
    if obs is None:
        if m.status == "finished":
            reason = ("partido finalizado cuya acta aún no está descargada/importada en la base de datos; "
                      "no implica que no exista en la RFFM")
        else:
            reason = f"partido en estado '{m.status}': aún no hay acta"
        return {"report_available": False, "reason": reason, "match": head}
    names = {t.id: t.name for t in db.scalars(select(Team).where(Team.id.in_([m.home_team_id, m.away_team_id])))}
    out: dict[str, Any] = {
        "report_available": True, "match": head,
        "report": {"closed": obs.closed, "observed_at": _iso(obs.observed_at), "source_url": obs.source_url,
                   "venue": obs.venue, "home_formation": obs.home_formation, "away_formation": obs.away_formation,
                   "observations_stored": db.scalar(select(func.count()).select_from(MatchReportObservation)
                                                    .where(MatchReportObservation.report_id == obs.report_id))},
        "not_in_source": NOT_IN_SOURCE}
    events = list(db.scalars(select(MatchEvent).where(MatchEvent.observation_id == obs.id)
                             .order_by(MatchEvent.sequence)))

    def ev(e: MatchEvent) -> dict[str, Any]:
        d = {"type": e.event_type, "team": names.get(e.team_id), "player_id": e.player_id,
             "player": e.player_name, "minute": e.minute}
        if e.event_type == "card":
            d.update(source_code=e.source_code, second_yellow=(e.source_detail or {}).get("segunda_amarilla"))
        return d

    if "officials" in sections:
        out["officials"] = [{"name": o.display_name, "role": o.role} for o in db.scalars(
            select(MatchOfficial).where(MatchOfficial.observation_id == obs.id).order_by(MatchOfficial.sequence))]
    if "goals" in sections:
        out["goals"] = [ev(e) for e in events if e.event_type != "card"]
    if "cards" in sections:
        out["cards"] = [ev(e) for e in events if e.event_type == "card"]
        out["cards_note"] = CARD_NOTE
    if "players" in sections:
        out["players"] = [{"team": names.get(p.team_id), "player_id": p.player_id, "name": p.display_name,
                           "shirt": p.shirt_number, "lineup": p.lineup_status, "captain": p.is_captain}
                          for p in db.scalars(select(MatchPlayer).where(MatchPlayer.observation_id == obs.id)
                                              .order_by(MatchPlayer.team_id, MatchPlayer.sequence))]
    if "staff" in sections:
        out["staff"] = [{"team": names.get(s.team_id) if s.team_id else None, "name": s.display_name,
                         "role": s.role} for s in db.scalars(select(MatchStaff).where(
                             MatchStaff.observation_id == obs.id).order_by(MatchStaff.sequence))]
    out["consistency"] = _consistency(db, m, obs, events)
    return out


def _consistency(db: Session, m: Match, obs: MatchReportObservation, events: list[MatchEvent]
                 ) -> dict[str, Any]:
    ev = [0, 0]
    for e in events:
        if e.event_type != "card":
            ev[0 if event_credit(e.event_type, e.team_id, m.home_team_id) == "home" else 1] += 1
    current = (m.home_score, m.away_score)
    report = (obs.home_score, obs.away_score)
    previous = sorted({(o.home_score, o.away_score) for o in db.scalars(
        select(MatchObservation).where(MatchObservation.match_id == m.id))
        if (o.home_score, o.away_score) != current and o.home_score is not None})
    diffs: list[str] = []
    if report != current:
        diffs.append(f"marcador de competición {current[0]}-{current[1]} ≠ marcador del acta {report[0]}-{report[1]}")
    if report[0] is not None and tuple(ev) != report:
        diffs.append(f"los goles del acta suman {ev[0]}-{ev[1]} (propia meta incluida) ≠ marcador del acta "
                     f"{report[0]}-{report[1]}")
    if previous:
        diffs.append("el listado de jornadas tuvo antes otro marcador "
                     + ", ".join(f"{a}-{b}" for a, b in previous) + "; el acta cerrada prevalece")
    return {"competition_score": list(current), "report_score": list(report), "events_score": ev,
            "consistent": not [d for d in diffs if not d.startswith("el listado")], "differences": diffs}


# ----------------------------------------------------------------------------- eventos y goleadores
def _check_scope(db: Session, group_id: int, *, team_id: int | None, against_team_id: int | None,
                 round_id: int | None, match_id: int | None, player_id: int | None) -> None:
    for tid in (team_id, against_team_id):
        if tid is not None and db.scalar(select(Team.id).where(Team.id == tid, Team.group_id == group_id)) is None:
            raise NotFound(f"equipo {tid} no encontrado en el grupo {group_id}")
    if round_id is not None and db.scalar(
            select(Round.id).where(Round.id == round_id, Round.group_id == group_id)) is None:
        raise NotFound(f"jornada {round_id} no encontrada en el grupo {group_id}")
    if match_id is not None and db.scalar(
            select(Match.id).where(Match.id == match_id, Match.group_id == group_id)) is None:
        raise NotFound(f"partido {match_id} no encontrado en el grupo {group_id}")
    if player_id is not None and db.get(Player, player_id) is None:
        raise NotFound(f"jugador {player_id} no encontrado")


def _scoped(stmt: Select[Any], obs: Any, group_id: int, event_types: tuple[str, ...], *, team_id: int | None,
            against_team_id: int | None, round_id: int | None, match_id: int | None,
            player_id: int | None) -> Select[Any]:
    stmt = stmt.where(Match.group_id == group_id, MatchEvent.event_type.in_(event_types))
    if team_id is not None:
        stmt = stmt.where(MatchEvent.team_id == team_id)
    if against_team_id is not None:  # solo goles a favor de un equipo distinto que enfrentó a `against`
        stmt = stmt.where(MatchEvent.event_type == "goal", MatchEvent.team_id != against_team_id,
                          (Match.home_team_id == against_team_id) | (Match.away_team_id == against_team_id))
    if round_id is not None:
        stmt = stmt.where(Match.round_id == round_id)
    if match_id is not None:
        stmt = stmt.where(Match.id == match_id)
    if player_id is not None:
        stmt = stmt.where(MatchEvent.player_id == player_id)
    return stmt


def _base(*cols: Any) -> Select[Any]:
    return (select(*cols).select_from(MatchEvent)
            .join(MatchReportObservation, MatchReportObservation.id == MatchEvent.observation_id)
            .join(MatchReport, MatchReport.id == MatchReportObservation.report_id)
            .join(Match, Match.id == MatchReport.match_id).join(Round, Round.id == Match.round_id)
            .where(latest(MatchReportObservation)))


def list_events(db: Session, group_id: int, *, event_type: str | None = None, team_id: int | None = None,
                against_team_id: int | None = None, round_id: int | None = None, match_id: int | None = None,
                player_id: int | None = None, limit: int = 40) -> dict[str, Any]:
    _check_scope(db, group_id, team_id=team_id, against_team_id=against_team_id, round_id=round_id,
                 match_id=match_id, player_id=player_id)
    types = (event_type,) if event_type else ("goal", "own_goal")
    flt = {"team_id": team_id, "against_team_id": against_team_id, "round_id": round_id,
           "match_id": match_id, "player_id": player_id}
    team = aliased(Team)
    stmt = _scoped(_base(MatchEvent, Match, Round.visible_number, team.name, Player.display_name)
                   .join(team, team.id == MatchEvent.team_id)
                   .outerjoin(Player, Player.id == MatchEvent.player_id),
                   MatchReportObservation, group_id, types, **flt)
    total = db.scalar(_scoped(_base(func.count()), MatchReportObservation, group_id, types, **flt)) or 0
    rows = db.execute(stmt.order_by(Round.visible_number, Match.id, MatchEvent.sequence).limit(limit)).all()
    teams = {t.id: t.name for t in db.scalars(select(Team).where(Team.group_id == group_id))}
    items = []
    for e, m, n, tname, pname in rows:
        opp = m.away_team_id if e.team_id == m.home_team_id else m.home_team_id
        d = {"match_id": m.id, "round_number": n, "type": e.event_type, "team": tname,
             "opponent": teams.get(opp), "player_id": e.player_id, "player": pname or e.player_name,
             "minute": e.minute}
        if e.event_type == "card":
            d["source_code"] = e.source_code
        items.append(d)
    out: dict[str, Any] = {"events": items, "count": len(items), "total": total, "limit": limit,
                           "truncated": total > len(items), "source": "actas RFFM (última observación)",
                           "coverage": coverage(db, group_id, round_id=round_id, match_id=match_id,
                                                team_id=team_id or against_team_id)}
    if "card" in types:
        out["cards_note"] = CARD_NOTE
    if any(t == "own_goal" for t in types):
        out["own_goal_note"] = "own_goal: gol en propia meta; `team` es el equipo del jugador y suma al rival"
    return out


def scorers(db: Session, group_id: int, *, team_id: int | None = None, against_team_id: int | None = None,
            round_id: int | None = None, match_id: int | None = None, player_id: int | None = None,
            min_goals: int = 1, limit: int = 20) -> dict[str, Any]:
    _check_scope(db, group_id, team_id=team_id, against_team_id=against_team_id, round_id=round_id,
                 match_id=match_id, player_id=player_id)
    flt = {"team_id": team_id, "against_team_id": against_team_id, "round_id": round_id,
           "match_id": match_id, "player_id": player_id}
    team = aliased(Team)
    name = func.coalesce(Player.display_name, MatchEvent.player_name)
    goals = func.count().filter(MatchEvent.event_type == "goal")
    own = func.count().filter(MatchEvent.event_type == "own_goal")
    groups = (MatchEvent.player_id, name, MatchEvent.team_id, team.name)
    inner = (_scoped(_base(MatchEvent.player_id.label("player_id"), name.label("player"), team.name.label("team"),
                           goals.label("goals"), own.label("own_goals"),
                           func.count(distinct(Match.id)).filter(MatchEvent.event_type == "goal")
                           .label("matches_with_goal"))
                     .join(team, team.id == MatchEvent.team_id).outerjoin(Player, Player.id == MatchEvent.player_id),
                     MatchReportObservation, group_id, ("goal", "own_goal"), **flt)
             .group_by(*groups).having(goals >= min_goals))
    sub = inner.subquery()
    total = db.scalar(select(func.count()).select_from(sub)) or 0
    rows = db.execute(select(sub).order_by(sub.c.goals.desc(), sub.c.own_goals, sub.c.player).limit(limit)).all()
    items = [{"player_id": r.player_id, "player": r.player, "team": r.team, "goals": r.goals,
              "own_goals": r.own_goals, "matches_with_goal": r.matches_with_goal} for r in rows]
    return {"scorers": items, "count": len(items), "total": total, "limit": limit, "truncated": total > len(items),
            "min_goals": min_goals, "source": "actas RFFM (última observación); `goals` excluye goles en propia meta",
            "coverage": coverage(db, group_id, round_id=round_id, match_id=match_id,
                                 team_id=team_id or against_team_id)}


# ----------------------------------------------------------------------------- jugadores
def find_players(db: Session, group_id: int, name: str, *, team_id: int | None = None,
                 limit: int = 10) -> dict[str, Any]:
    """Jugadores vistos en actas del grupo cuyo nombre contiene todas las palabras de `name`."""
    _check_scope(db, group_id, team_id=team_id, against_team_id=None, round_id=None, match_id=None,
                 player_id=None)
    tokens = norm(name).split()
    if not tokens:
        raise ValueError("name no puede estar vacío")
    stmt = (select(Player.id, Player.display_name, Team.id, Team.name)
            .join(PlayerTeamMembership, PlayerTeamMembership.player_id == Player.id)
            .join(Team, Team.id == PlayerTeamMembership.team_id).where(Team.group_id == group_id)
            .order_by(Player.display_name))
    if team_id is not None:
        stmt = stmt.where(Team.id == team_id)
    found = [{"player_id": pid, "name": pname, "team_id": tid, "team": tname}
             for pid, pname, tid, tname in db.execute(stmt) if all(t in norm(pname) for t in tokens)]
    return {"players": found[:limit], "count": min(len(found), limit), "total": len(found),
            "note": ("Solo constan jugadores que aparecen en actas ya almacenadas; "
                     "que no aparezca no prueba que no exista." if not found else "")}
