"""Frescura de los datos por tipo (Fase 21). SOLO LECTURA: informa de cuándo se actualizó cada dato; no lanza ninguna actualización."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from futsal.api.sport import SportParam, require_competition, require_group
from futsal.refresh.freshness import competition_freshness, group_freshness


def freshness_router(get_session: Callable[[], Iterator[Session]]) -> APIRouter:
    router = APIRouter()
    Db = Annotated[Session, Depends(get_session)]

    @router.get("/groups/{group_id}/freshness")
    def group(group_id: int, db: Db, sport: SportParam) -> dict[str, Any]:
        """Última actualización válida de partidos, actas, clasificación y goleadores del grupo, con avisos de parcialidad."""
        require_group(db, group_id, sport)
        found = group_freshness(db, group_id)
        if found is None:
            raise HTTPException(404, f"grupo {group_id} no encontrado")
        return found

    @router.get("/competitions/{competition_id}/freshness")
    def competition(competition_id: int, db: Db, sport: SportParam) -> dict[str, Any]:
        """Resumen de frescura de todos los grupos de la competición."""
        require_competition(db, competition_id, sport)
        found = competition_freshness(db, competition_id)
        if found is None:
            raise HTTPException(404, f"competición {competition_id} no encontrada")
        return found

    return router
