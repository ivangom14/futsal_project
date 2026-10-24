"""Rutas de lectura de las clasificaciones y goleadores OFICIALES de la RFFM (Fase 16). Sin descarga ni cálculo propio."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from futsal.repositories import table_queries as tq


def tables_router(get_session: Callable[[], Iterator[Session]]) -> APIRouter:
    router = APIRouter()
    Db = Annotated[Session, Depends(get_session)]

    def _found(result: dict[str, Any] | None, what: str, ident: int) -> dict[str, Any]:
        if result is None:
            raise HTTPException(404, f"{what} {ident} no encontrado")
        return result

    @router.get("/groups/{group_id}/standings")
    def standings(group_id: int, db: Db, round: Annotated[int | None, Query(ge=1, le=200)] = None) -> dict[str, Any]:
        """Clasificación oficial de la jornada indicada (por defecto, la última ingerida)."""
        return _found(tq.standings(db, group_id, round), "grupo", group_id)

    @router.get("/groups/{group_id}/scorer-table")
    def scorer_table(group_id: int, db: Db, name: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
                     team_id: int | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 10) -> dict[str, Any]:
        """Tabla oficial de goleadores; `name` busca un jugador (sin acentos) y devuelve todos sus registros."""
        return _found(tq.scorer_table(db, group_id, name=name, team_id=team_id, limit=limit), "grupo", group_id)

    @router.get("/competitions/{competition_id}/scorer-table")
    def competition_scorer_table(competition_id: int, db: Db, limit: Annotated[int, Query(ge=1, le=50)] = 1) -> dict[str, Any]:
        """Máximos goleadores oficiales de cada grupo de la competición."""
        return _found(tq.competition_scorers(db, competition_id, limit), "competición", competition_id)

    @router.get("/tables/coverage")
    def coverage(db: Db, competition_id: int | None = None) -> dict[str, Any]:
        """Estado de ingesta de ambas tablas por grupo (última captura, jornada, filas)."""
        return _found(tq.table_coverage(db, competition_id), "competición", competition_id or 0)

    return router
