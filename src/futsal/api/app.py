"""API REST de consulta (solo lectura) sobre el modelo persistido en PostgreSQL."""

from collections.abc import Callable, Iterator
from datetime import date, datetime, time
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, aliased

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    Match,
    MatchObservation,
    MatchReport,
    Round,
    Season,
    Team,
)
from futsal.api.freshness import freshness_router
from futsal.api.knowledge import knowledge_router
from futsal.api.reports import reports_router
from futsal.api.tables import tables_router
from futsal.db.session import make_engine

STATUSES = ("scheduled", "finished", "postponed", "suspended", "cancelled", "unknown")


def _items(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": rows, "count": len(rows)}


def _iso(value: date | time | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def match_day(m: dict[str, Any]) -> str:
    """Fecha ISO (aaaa-mm-dd) del partido tal como se expone, o '' si no consta."""
    return str(m.get("date") or "")[:10]


def order_matches(rows: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    """`asc`: orden de jornada (el de siempre). `desc`: lo más reciente primero por la fecha publicada
    (jornada e id como desempate; sin fecha, al final), para pedir el último partido con limit=1.
    `date_asc`: lo más próximo primero por la fecha publicada (jornada e id como desempate; sin fecha, al final), para pedir el
    PRÓXIMO partido con limit=1 aunque haya partidos reprogramados dentro de una jornada anterior."""
    if order == "desc":
        return sorted(rows, key=lambda m: (match_day(m), m.get("round_number") or 0, m["id"]), reverse=True)
    if order == "date_asc":
        return sorted(rows, key=lambda m: (not match_day(m), match_day(m), m.get("round_number") or 0, m["id"]))
    return rows


def mark_past_due(rows: list[dict[str, Any]], today: date) -> None:
    """`past_due=true` en un partido aún `scheduled` cuya fecha ya pasó: los datos pueden estar desactualizados
    (no se interpreta como jugado ni se cambia su estado)."""
    for m in rows:
        if m["status"] == "scheduled" and match_day(m) and match_day(m) < today.isoformat():
            m["past_due"] = True


def _match_dict(m: Match, home: str | None, away: str | None, number: int | None,
                has_report: bool | None = None) -> dict[str, Any]:
    data = {"id": m.id, "group_id": m.group_id, "round_id": m.round_id, "round_number": number,
            "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
            "home_team": home, "away_team": away, "date": _iso(m.scheduled_at) or _iso(m.scheduled_date),
            "status": m.status, "home_score": m.home_score, "away_score": m.away_score}
    if has_report is not None:
        data["has_report"] = has_report
    return data


def create_app(engine: Engine | None = None, today: Callable[[], date] = date.today) -> FastAPI:
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
        by_comp: dict[int, list[dict[str, Any]]] = {}
        for g in db.scalars(select(CompetitionGroup).order_by(CompetitionGroup.id)):
            by_comp.setdefault(g.competition_id, []).append({"id": g.id, "name": g.name})
        return _items([
            {"id": c.id, "external_id": c.external_id, "name": c.name,
             "season_id": c.season_id, "season": season, "groups": by_comp.get(c.id, [])}
            for c, season in db.execute(stmt)])

    @app.get("/teams")
    def find_teams(db: Db, name: Annotated[str, Query(min_length=2, max_length=80)],
                   limit: Annotated[int, Query(ge=1, le=100)] = 30) -> dict[str, Any]:
        """Equipos por nombre en todos los grupos: permite resolver el grupo cuando la pregunta no lo indica."""
        like = "%" + name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = [{"id": t.id, "name": t.name, "group_id": g.id, "group": g.name,
                 "competition_id": c.id, "competition": c.name}
                for t, g, c in db.execute(
                    select(Team, CompetitionGroup, Competition)
                    .join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
                    .join(Competition, Competition.id == CompetitionGroup.competition_id)
                    .where(Team.name.ilike(like, escape="\\"))
                    .order_by(Competition.id, CompetitionGroup.id, Team.name))]
        if len(rows) > limit:
            return {"items": rows[:limit], "count": len(rows), "shown": limit}
        return _items(rows)

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
                status: str | None = None, team_id: int | None = None,
                has_report: bool | None = None,
                order: Annotated[str, Query(pattern="^(asc|desc|date_asc)$")] = "asc",
                limit: Annotated[int | None, Query(ge=1, le=200)] = None) -> dict[str, Any]:
        _group(db, group_id)
        if team_id is not None and db.scalar(
                select(Team.id).where(Team.id == team_id, Team.group_id == group_id)) is None:
            raise HTTPException(404, f"equipo {team_id} no encontrado en el grupo {group_id}")
        if status is not None and status not in STATUSES:
            raise HTTPException(400, f"status inválido; valores: {', '.join(STATUSES)}")
        home, away = aliased(Team), aliased(Team)
        report_id = select(MatchReport.id).where(MatchReport.match_id == Match.id).exists()
        stmt = (select(Match, home.name, away.name, Round.visible_number, report_id)
                .join(home, home.id == Match.home_team_id)
                .join(away, away.id == Match.away_team_id)
                .join(Round, Round.id == Match.round_id)
                .where(Match.group_id == group_id)
                .order_by(Round.visible_number, Match.scheduled_at, Match.id))
        if round_id is not None:
            stmt = stmt.where(Match.round_id == round_id)
        if status is not None:
            stmt = stmt.where(Match.status == status)
        if team_id is not None:
            stmt = stmt.where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        if has_report is not None:
            stmt = stmt.where(report_id if has_report else ~report_id)
        rows = order_matches([_match_dict(m, h, a, n, r) for m, h, a, n, r in db.execute(stmt)], order)
        mark_past_due(rows, today())
        if limit is not None and len(rows) > limit:  # count = total de coincidencias; shown = filas devueltas
            return {"items": rows[:limit], "count": len(rows), "shown": limit}
        return _items(rows)

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
                         "date": _iso(o.scheduled_at) or _iso(o.scheduled_date)} for o in obs])
        return data

    app.include_router(reports_router(get_session))
    app.include_router(tables_router(get_session))
    app.include_router(freshness_router(get_session))
    app.include_router(knowledge_router(get_engine))
    return app


def app_factory() -> FastAPI:
    """Para `uvicorn futsal.api.app:app_factory --factory`."""
    return create_app()
