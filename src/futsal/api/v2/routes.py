"""Rutas v2 (solo lectura) de consultas sin grupo. Validación: 422 en parámetros inválidos (también los de tipo)."""

from collections.abc import Callable, Coroutine, Iterator
from datetime import date, time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from futsal.repositories import v2_queries as q

Limit = Annotated[int, Query(ge=1, le=q.MAX_LIMIT)]
Name = Annotated[str, Query(min_length=2, max_length=80)]


class V2Route(APIRoute):
    """Los errores de validación de FastAPI (tipo, rango) responden 422 en v2 (la v1 usa 400)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
                return JSONResponse({"detail": f"parámetros inválidos: {detail}"}, status_code=422)

        return handler


def v2_router(get_session: Callable[[], Iterator[Session]], today: Callable[[], date] = date.today) -> APIRouter:
    router = APIRouter(route_class=V2Route)
    Db = Annotated[Session, Depends(get_session)]

    def filters(season: Annotated[str | None, Query(max_length=40)] = None, category: Annotated[str | None, Query(max_length=30)] = None,
                gender: Annotated[str | None, Query(max_length=30)] = None,
                competition: Annotated[str | None, Query(max_length=80)] = None) -> q.Filters:
        return q.Filters(season, category, gender, competition)

    F = Annotated[q.Filters, Depends(filters)]

    def run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except q.InvalidParam as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/players")
    def players(db: Db, f: F, name: Name, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Jugadores de actas en todos los grupos; sin coincidencia exacta solo `suggestions` (nunca como si fuera el pedido)."""
        return run(lambda: q.players(db, q.build_scope(db, today(), f), name, limit))

    @router.get("/officials/ranking")
    def officials_ranking(db: Db, f: F, role: Annotated[str, Query(min_length=2, max_length=40)] = "ARBITRO",
                          limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Árbitros con más partidos en las actas almacenadas."""
        return run(lambda: q.officials_ranking(db, q.build_scope(db, today(), f), role, limit))

    @router.get("/officials")
    def officials(db: Db, f: F, name: Name, include: Annotated[str | None, Query(max_length=20)] = None,
                  limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Árbitros por nombre (rol, partidos, competiciones y grupos); `include=matches` añade la lista de partidos."""
        if include not in (None, "", "matches"):
            raise HTTPException(422, "include inválido; valores: matches")
        return run(lambda: q.officials(db, q.build_scope(db, today(), f), name, include == "matches", limit))

    @router.get("/matches")
    def matches(db: Db, f: F, date_from: date | None = None, date_to: date | None = None, time_from: time | None = None,
                time_to: time | None = None, venue: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
                status: Annotated[str | None, Query(max_length=20)] = None, order: Annotated[str, Query(max_length=4)] = "asc",
                limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Partidos de todos los grupos en un rango de fechas/horas de Europe/Madrid."""
        return run(lambda: q.matches(db, q.build_scope(db, today(), f), date_from=date_from, date_to=date_to, time_from=time_from,
                                     time_to=time_to, venue=venue, status=status, order=order, limit=limit))

    @router.get("/clubs/search")
    def clubs_search(db: Db, f: F, name: Name, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Equipos agrupados por nombre base normalizado (sin A/B/C, «club», «C.F.S.»), con `club_link`."""
        return run(lambda: q.clubs_search(db, q.build_scope(db, today(), f), name, limit))

    @router.get("/clubs/results")
    def clubs_results(db: Db, f: F, name: Name, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Partidos finalizados de todos los equipos de `/clubs/search`, por equipo y competición, con resumen."""
        return run(lambda: q.clubs_results(db, q.build_scope(db, today(), f), name, limit))

    @router.get("/rankings/scorers")
    def scorers(db: Db, f: F, competition_kind: Annotated[str, Query(max_length=20)] = "league",
                limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Goleadores de las últimas tablas oficiales `ok` de todos los grupos del filtro (`competition_kind=all` quita el tipo)."""
        kind = None if competition_kind == "all" else competition_kind
        if kind is not None and kind not in q.KINDS:
            raise HTTPException(422, f"competition_kind inválido; valores: all, {', '.join(q.KINDS)}")
        return run(lambda: q.scorers_ranking(db, q.build_scope(db, today(), f, kind=kind, drop_unknown_category=True), limit))

    @router.get("/rankings/teams")
    def teams(db: Db, f: F, metric: Annotated[str, Query(max_length=20)], order: Annotated[str | None, Query(max_length=4)] = None,
              min_played: Annotated[int, Query(ge=1, le=100)] = 1, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
        """Ranking de equipos desde partidos finalizados (goals_for, goals_against, wins, points_per_game)."""
        return run(lambda: q.teams_ranking(db, q.build_scope(db, today(), f, drop_unknown_category=True), metric, order, min_played, limit))

    return router
