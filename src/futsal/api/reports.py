"""Rutas de lectura de actas (eventos, goleadores, árbitros). Sin descarga ni importación."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from futsal.db.models import CompetitionGroup
from futsal.repositories import report_queries as rq

MAX_LIMIT = 200


def reports_router(get_session: Callable[[], Iterator[Session]]) -> APIRouter:
    router = APIRouter()
    Db = Annotated[Session, Depends(get_session)]
    Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]

    def _group(db: Session, group_id: int) -> None:
        if db.scalar(select(CompetitionGroup.id).where(CompetitionGroup.id == group_id)) is None:
            raise HTTPException(404, f"grupo {group_id} no encontrado")

    def _run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except rq.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/matches/{match_id}/report")
    def match_report(match_id: int, db: Db, sections: str = ",".join(rq.DEFAULT_SECTIONS)) -> dict[str, Any]:
        wanted = tuple(s.strip() for s in sections.split(",") if s.strip())
        bad = [s for s in wanted if s not in rq.SECTIONS]
        if bad or not wanted:
            raise HTTPException(400, f"sections inválido {bad}; valores: {', '.join(rq.SECTIONS)}")
        return _run(lambda: rq.match_report(db, match_id, wanted))

    @router.get("/groups/{group_id}/match-events")
    def match_events(group_id: int, db: Db, event_type: rq.EventType | None = None,
                     team_id: int | None = None, against_team_id: int | None = None,
                     round_id: int | None = None, match_id: int | None = None,
                     player_id: int | None = None, limit: Limit = 40) -> dict[str, Any]:
        _group(db, group_id)
        return _run(lambda: rq.list_events(
            db, group_id, event_type=event_type, team_id=team_id, against_team_id=against_team_id,
            round_id=round_id, match_id=match_id, player_id=player_id, limit=limit))

    @router.get("/groups/{group_id}/scorers")
    def scorers(group_id: int, db: Db, team_id: int | None = None, against_team_id: int | None = None,
                round_id: int | None = None, match_id: int | None = None, player_id: int | None = None,
                min_goals: Annotated[int, Query(ge=1, le=100)] = 1, limit: Limit = 20) -> dict[str, Any]:
        _group(db, group_id)
        return _run(lambda: rq.scorers(
            db, group_id, team_id=team_id, against_team_id=against_team_id, round_id=round_id,
            match_id=match_id, player_id=player_id, min_goals=min_goals, limit=limit))

    @router.get("/groups/{group_id}/players")
    def players(group_id: int, db: Db, name: Annotated[str, Query(min_length=2, max_length=80)],
                team_id: int | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> dict[str, Any]:
        _group(db, group_id)
        return _run(lambda: rq.find_players(db, group_id, name, team_id=team_id, limit=limit))

    return router
