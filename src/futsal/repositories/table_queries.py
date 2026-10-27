"""Consultas de SOLO LECTURA sobre las clasificaciones y goleadores OFICIALES de la RFFM (capturas en PostgreSQL).

Nada se recalcula a partir de partidos o actas. Estados que distingue el resultado:
`ok` (obtenida), `incomplete` (disponible pero con filas descartadas o equipos de menos), `empty` (tabla válida sin registros),
`not_ingested` (aún no ingerida), `failed` (el intento falló y no hay captura), `not_applicable` (eliminatorias: no hay clasificación)
y `round_not_ingested` (jornada pedida no capturada). Los campos derivados se declaran en `derived`.
"""

import re
import unicodedata
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup
from futsal.db.table_models import (
    ScorerRow,
    ScorerSnapshot,
    StandingsRow,
    StandingsSnapshot,
    TableIngestAttempt,
)

STANDINGS_NOTE = ("Clasificación OFICIAL publicada por la RFFM tras la jornada indicada (no calculada). Cita la jornada y la fecha de captura; "
                  "los equipos pueden haber jugado distinto número de partidos.")
SCORERS_NOTE = ("Tabla OFICIAL de goleadores de la RFFM: solo figuran jugadores con al menos un gol; la RFFM no publica posición, así que "
                "`rank` se deriva de los goles y los empates comparten puesto. Un jugador que no aparece no tiene goles registrados en la tabla. "
                "Es la tabla ACTUAL: la RFFM no publica histórico ni desglose por jornada; no lo reconstruyas con top_scorers (actas, cobertura parcial). "
                "Si piden una jornada pasada, di que ese desglose no consta y ofrece esta tabla indicando su fecha de captura.")
NOT_INGESTED = "Aún no se han ingerido estos datos del grupo: no los reconstruyas con resultados o actas; di que no constan."


def _norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def group_info(db: Session, group_id: int) -> tuple[CompetitionGroup, Competition] | None:
    group = db.get(CompetitionGroup, group_id)
    if group is None:
        return None
    return group, db.get(Competition, group.competition_id)  # type: ignore[return-value]


def _last_attempt(db: Session, group_id: int, kind: str, requested: int | None = None) -> TableIngestAttempt | None:
    stmt = select(TableIngestAttempt).where(TableIngestAttempt.group_id == group_id, TableIngestAttempt.kind == kind)
    if requested is not None:
        stmt = stmt.where(TableIngestAttempt.requested_round == requested)
    return db.scalar(stmt.order_by(TableIngestAttempt.attempted_at.desc(), TableIngestAttempt.id.desc()).limit(1))


def _attempt_dict(a: TableIngestAttempt) -> dict[str, Any]:
    return _clean({"outcome": a.outcome, "at": _iso(a.attempted_at), "error_code": a.error_code, "error": a.error_message})


def _missing(db: Session, group: CompetitionGroup, competition: Competition, kind: str, requested: int | None) -> dict[str, Any]:
    """Sin captura: distingue no ingerido, ingesta fallida y grupo sin tabla (eliminatorias)."""
    head = {"group_id": group.id, "group": group.name, "competition": competition.name}
    last = _last_attempt(db, group.id, kind) if requested is None else _last_attempt(db, group.id, kind, requested)
    if last is not None and last.outcome == "not_applicable":
        return {**head, "status": "not_applicable", "message": "Grupo de eliminatorias: la RFFM no publica tabla de clasificación."}
    if last is not None and last.outcome in ("failed", "round_unavailable"):
        return {**head, "status": "failed", "last_attempt": _attempt_dict(last),
                "message": "La última ingesta falló y no hay captura anterior: los datos no constan."}
    return {**head, "status": "not_ingested", "message": NOT_INGESTED}


def standings(db: Session, group_id: int, round_number: int | None = None) -> dict[str, Any] | None:
    found = group_info(db, group_id)
    if found is None:
        return None
    group, competition = found
    stmt = select(StandingsSnapshot).where(StandingsSnapshot.group_id == group_id)
    if round_number is not None:
        stmt = stmt.where(StandingsSnapshot.round_number == round_number)
    snap = db.scalar(stmt.order_by(StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(),
                                   StandingsSnapshot.id.desc()).limit(1))
    rounds = sorted(r for (r,) in db.execute(select(StandingsSnapshot.round_number).where(StandingsSnapshot.group_id == group_id).distinct()))
    if snap is None:
        out = _missing(db, group, competition, "standings", round_number)
        if round_number is not None and out["status"] == "not_ingested" and rounds:
            out.update(status="round_not_ingested", requested_round=round_number, available_rounds=rounds,
                       message=f"La clasificación de la jornada {round_number} no está ingerida: no la reconstruyas; di que no consta.")
        return out
    rows = db.scalars(select(StandingsRow).where(StandingsRow.snapshot_id == snap.id).order_by(StandingsRow.position, StandingsRow.id)).all()
    promos = {p.get("color"): p.get("name") for p in (snap.meta or {}).get("promotions", []) if p.get("color")}
    played = [r.played for r in rows]
    out = {
        "group_id": group.id, "group": group.name, "competition": competition.name, "status": snap.status,
        "source": _clean({"name": "RFFM · clasificaciones", "round": snap.round_number, "round_date": _iso(snap.round_date),
                          "current_round": snap.current_round, "captured_at": _iso(snap.fetched_at), "url": snap.source_url,
                          "rounds_available": rounds}),
        "coverage": _clean({"teams": snap.row_count, "expected_teams": snap.expected_teams, "played_min": min(played, default=None),
                            "played_max": max(played, default=None), "complete": snap.status in ("ok", "empty"),
                            "warnings": (snap.meta or {}).get("warnings") or None}),
        "derived": ["goal_difference"], "note": STANDINGS_NOTE,
        "standings": [_clean({
            "position": r.position, "team_id": r.team_id, "team": r.team_name, "played": r.played, "won": r.won, "drawn": r.drawn,
            "lost": r.lost, "goals_for": r.goals_for, "goals_against": r.goals_against,
            "goal_difference": r.goals_for - r.goals_against, "points": r.points,
            "penalty_points": r.penalty_points or None, "zone": promos.get((r.details or {}).get("color"))}) for r in rows]}
    if snap.current_round is not None and snap.round_number < snap.current_round and round_number is None:
        out["warning"] = f"Última jornada ingerida: {snap.round_number}; la RFFM ya iba por la {snap.current_round}."
    last = _last_attempt(db, group_id, "standings", round_number)
    if last is not None and last.outcome in ("failed", "round_unavailable") and last.attempted_at > snap.last_seen_at:
        out["last_attempt"] = _attempt_dict(last)
        out["warning"] = "El último intento de actualización falló: se muestra la última captura buena."
    return out


def _ranked(rows: list[ScorerRow]) -> list[dict[str, Any]]:
    """Posición derivada de los goles (la RFFM no la publica): los empates comparten puesto."""
    counts: dict[int, int] = {}
    for r in rows:
        counts[r.goals] = counts.get(r.goals, 0) + 1
    out: list[dict[str, Any]] = []
    rank = 0
    for index, r in enumerate(rows):
        if index == 0 or rows[index - 1].goals != r.goals:
            rank = index + 1
        out.append(_clean({
            "rank": rank, "tied_with": counts[r.goals] - 1 or None, "player": r.player_name, "player_id": r.player_id,
            "team": r.team_name, "team_id": r.team_id, "goals": r.goals, "matches_played": r.matches_played,
            "penalty_goals": r.penalty_goals, "goals_per_match": float(r.goals_per_match) if r.goals_per_match is not None else None}))
    return out


def scorer_table(db: Session, group_id: int, *, name: str | None = None, team_id: int | None = None,
                 limit: int = 10) -> dict[str, Any] | None:
    found = group_info(db, group_id)
    if found is None:
        return None
    group, competition = found
    snap = db.scalar(select(ScorerSnapshot).where(ScorerSnapshot.group_id == group_id)
                     .order_by(ScorerSnapshot.last_seen_at.desc(), ScorerSnapshot.id.desc()).limit(1))
    if snap is None:
        return _missing(db, group, competition, "scorers", None)
    rows = list(db.scalars(select(ScorerRow).where(ScorerRow.snapshot_id == snap.id).order_by(ScorerRow.row_order)))
    ranked = _ranked(rows)
    selected = list(zip(rows, ranked, strict=True))
    if team_id is not None:
        selected = [(r, d) for r, d in selected if r.team_id == team_id]
    if name:
        wanted = _norm(name)
        selected = [(r, d) for r, d in selected if wanted and wanted in _norm(r.player_name)]
    shown = selected if name else selected[:limit]
    out: dict[str, Any] = {
        "group_id": group.id, "group": group.name, "competition": competition.name, "status": snap.status,
        "source": _clean({"name": "RFFM · goleadores", "captured_at": _iso(snap.fetched_at), "url": snap.source_url}),
        "coverage": _clean({"players_listed": snap.row_count, "goals_total": snap.goals_total, "only_players_with_goals": True,
                            "complete": snap.status in ("ok", "empty"), "warnings": (snap.meta or {}).get("warnings") or None}),
        "derived": ["rank", "tied_with"], "note": SCORERS_NOTE,
        "matching": len(selected), "shown": len(shown), "scorers": [d for _, d in shown]}
    if (name or team_id is not None) and not selected:
        out["message"] = "No figura en la tabla de goleadores (solo se publican jugadores con goles): no consta que haya marcado."
    last = _last_attempt(db, group_id, "scorers")
    if last is not None and last.outcome in ("failed", "round_unavailable") and last.attempted_at > snap.last_seen_at:
        out["last_attempt"] = _attempt_dict(last)
        out["warning"] = "El último intento de actualización falló: se muestra la última captura buena."
    return out


def competition_scorers(db: Session, competition_id: int, limit: int) -> dict[str, Any] | None:
    competition = db.get(Competition, competition_id)
    if competition is None:
        return None
    groups = []
    for g in db.scalars(select(CompetitionGroup).where(CompetitionGroup.competition_id == competition_id).order_by(CompetitionGroup.id)):
        table = scorer_table(db, g.id, limit=limit) or {}
        groups.append(_clean({k: table.get(k) for k in ("group_id", "group", "status", "source", "coverage", "scorers", "message", "warning")}))
    return {"competition_id": competition.id, "competition": competition.name, "derived": ["rank", "tied_with"], "note": SCORERS_NOTE,
            "groups": groups}


def table_coverage(db: Session, competition_id: int | None = None) -> dict[str, Any] | None:
    if competition_id is not None and db.get(Competition, competition_id) is None:
        return None
    stmt = select(CompetitionGroup).order_by(CompetitionGroup.id)
    if competition_id is not None:
        stmt = stmt.where(CompetitionGroup.competition_id == competition_id)
    items: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {"standings": {}, "scorers": {}}
    for g in db.scalars(stmt):
        comp = db.get(Competition, g.competition_id)
        item: dict[str, Any] = {"group_id": g.id, "group": g.name, "competition_id": g.competition_id,
                                "competition": comp.name if comp else None}
        for kind in ("standings", "scorers"):
            table = standings(db, g.id) if kind == "standings" else scorer_table(db, g.id, limit=1)
            table = table or {}
            src = table.get("source") or {}
            item[kind] = _clean({"status": table.get("status"), "round": src.get("round"), "captured_at": src.get("captured_at"),
                                 "rows": (table.get("coverage") or {}).get("teams") or (table.get("coverage") or {}).get("players_listed")})
            totals[kind][table.get("status") or "unknown"] = totals[kind].get(table.get("status") or "unknown", 0) + 1
        items.append(item)
    return {"groups": len(items), "by_status": totals, "items": items}


def snapshot_counts(db: Session) -> dict[str, int]:
    return {"standings_snapshots": int(db.scalar(select(func.count(StandingsSnapshot.id))) or 0),
            "scorer_snapshots": int(db.scalar(select(func.count(ScorerSnapshot.id))) or 0)}
