"""API REST de consulta (solo lectura) sobre el modelo persistido en PostgreSQL."""

from collections.abc import Iterator
from datetime import date, datetime, time
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, aliased

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    Match,
    MatchObservation,
    Round,
    Season,
    Team,
)
from futsal.db.session import make_engine

STATUSES = ("scheduled", "finished", "postponed", "suspended", "cancelled", "unknown")


def _items(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": rows, "count": len(rows)}


def _iso(value: date | time | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _match_dict(m: Match, home: str | None, away: str | None, number: int | None) -> dict[str, Any]:
    return {"id": m.id, "group_id": m.group_id, "round_id": m.round_id, "round_number": number,
            "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
            "home_team": home, "away_team": away, "date": _iso(m.scheduled_at),
            "status": m.status, "home_score": m.home_score, "away_score": m.away_score}


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Futsal API", version="0.1.0")
    state: dict[str, Engine] = {}

    def get_engine() -> Engine:
        if "engine" not in state:
            state["engine"] = engine or make_engine()
        return state["engine"]

    def get_session() -> Iterator[Session]:
        with Session(get_engine()) as session:
            yield session

    Db = Annotated[Session, Depends(get_session)]

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"detail": f"parámetros inválidos: {detail}"}, status_code=400)

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

    @app.get("/competitions")
    def competitions(db: Db, season_id: int | None = None) -> dict[str, Any]:
        stmt = (select(Competition, Season.name).join(Season, Season.id == Competition.season_id)
                .order_by(Competition.id))
        if season_id is not None:
            stmt = stmt.where(Competition.season_id == season_id)
        return _items([
            {"id": c.id, "external_id": c.external_id, "name": c.name,
             "season_id": c.season_id, "season": season}
            for c, season in db.execute(stmt)])

    @app.get("/competitions/{competition_id}/groups")
    def groups(competition_id: int, db: Db) -> dict[str, Any]:
        if db.get(Competition, competition_id) is None:
            raise HTTPException(404, f"competición {competition_id} no encontrada")
        rows = db.scalars(select(CompetitionGroup)
                          .where(CompetitionGroup.competition_id == competition_id)
                          .order_by(CompetitionGroup.id))
        return _items([{"id": g.id, "external_id": g.external_id, "name": g.name,
                        "competition_id": g.competition_id} for g in rows])

    def _group(db: Session, group_id: int) -> CompetitionGroup:
        group = db.get(CompetitionGroup, group_id)
        if group is None:
            raise HTTPException(404, f"grupo {group_id} no encontrado")
        return group

    @app.get("/groups/{group_id}/rounds")
    def rounds(group_id: int, db: Db) -> dict[str, Any]:
        _group(db, group_id)
        rows = db.scalars(select(Round).where(Round.group_id == group_id)
                          .order_by(Round.visible_number, Round.id))
        return _items([{"id": r.id, "external_id": r.external_id, "label": r.visible_label,
                        "number": r.visible_number, "date": _iso(r.scheduled_date)}
                       for r in rows])

    @app.get("/groups/{group_id}/teams")
    def teams(group_id: int, db: Db) -> dict[str, Any]:
        _group(db, group_id)
        rows = db.scalars(select(Team).where(Team.group_id == group_id).order_by(Team.name))
        return _items([{"id": t.id, "external_id": t.external_id, "name": t.name}
                       for t in rows])

    @app.get("/groups/{group_id}/matches")
    def matches(group_id: int, db: Db, round_id: int | None = None,
                status: str | None = None) -> dict[str, Any]:
        _group(db, group_id)
        if status is not None and status not in STATUSES:
            raise HTTPException(400, f"status inválido; valores: {', '.join(STATUSES)}")
        home, away = aliased(Team), aliased(Team)
        stmt = (select(Match, home.name, away.name, Round.visible_number)
                .join(home, home.id == Match.home_team_id)
                .join(away, away.id == Match.away_team_id)
                .join(Round, Round.id == Match.round_id)
                .where(Match.group_id == group_id)
                .order_by(Round.visible_number, Match.scheduled_at, Match.id))
        if round_id is not None:
            stmt = stmt.where(Match.round_id == round_id)
        if status is not None:
            stmt = stmt.where(Match.status == status)
        return _items([_match_dict(m, h, a, n) for m, h, a, n in db.execute(stmt)])

    @app.get("/matches/{match_id}")
    def match_detail(match_id: int, db: Db) -> dict[str, Any]:
        home, away = aliased(Team), aliased(Team)
        row = db.execute(
            select(Match, home.name, away.name, Round.visible_number)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .join(Round, Round.id == Match.round_id)
            .where(Match.id == match_id)).one_or_none()
        if row is None:
            raise HTTPException(404, f"partido {match_id} no encontrado")
        m, h, a, n = row
        obs = db.scalars(select(MatchObservation).where(MatchObservation.match_id == match_id)
                         .order_by(MatchObservation.observed_at, MatchObservation.id))
        data = _match_dict(m, h, a, n)
        data.update(venue=m.venue, timezone=m.timezone, external_id=m.external_id,
                    observations=[
                        {"observed_at": _iso(o.observed_at), "status": o.status,
                         "home_score": o.home_score, "away_score": o.away_score,
                         "date": _iso(o.scheduled_at)} for o in obs])
        return data

    return app


def app_factory() -> FastAPI:
    """Para `uvicorn futsal.api.app:app_factory --factory`."""
    return create_app()
