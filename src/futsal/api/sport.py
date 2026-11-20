"""Deporte obligatorio en la API (Fase 1 multideporte).

Todo endpoint de datos deportivos exige `sport=futsal|football11|all`; sin él, FastAPI responde con un error de validación
(400 en v1, 422 en v2) que nombra `query.sport`. `all` es un ámbito explícito (consultas combinadas intencionadas), nunca un
valor por defecto. Un recurso por identificador de otro deporte responde 404 «no encontrado en <deporte>»: una llamada de
fútbol sala jamás devuelve datos de fútbol 11 aunque el ID exista.
"""

from typing import Annotated, Literal

from fastapi import HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup, Match, Team
from futsal.domain.sports import LABELS, parse_scope

SportParam = Annotated[Literal["futsal", "football11", "all"], Query(
    description="Deporte (obligatorio): futsal (fútbol sala), football11 (fútbol) o all (ambos, solo si se piden los dos)")]


def sports(sport: str) -> tuple[str, ...]:
    return parse_scope(sport)


def label(sport: str) -> str:
    return LABELS.get(sport, "los deportes pedidos")


def _missing(what: str, ident: int, sport: str) -> HTTPException:
    return HTTPException(404, f"{what} {ident} no encontrado en {label(sport)}")


def require_competition(db: Session, competition_id: int, sport: str) -> Competition:
    comp = db.get(Competition, competition_id)
    if comp is None or comp.modality not in sports(sport):
        raise _missing("competición", competition_id, sport)
    return comp


def require_group(db: Session, group_id: int, sport: str) -> CompetitionGroup:
    row = db.execute(select(CompetitionGroup, Competition.modality)
                     .join(Competition, Competition.id == CompetitionGroup.competition_id)
                     .where(CompetitionGroup.id == group_id)).one_or_none()
    if row is None or row[1] not in sports(sport):
        raise _missing("grupo", group_id, sport)
    return row[0]


def require_match(db: Session, match_id: int, sport: str) -> None:
    modality = db.scalar(select(Competition.modality).select_from(Match)
                         .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
                         .join(Competition, Competition.id == CompetitionGroup.competition_id).where(Match.id == match_id))
    if modality is None or modality not in sports(sport):
        raise _missing("partido", match_id, sport)


def team_ids_of(db: Session, sport: str) -> Select[int]:
    """Subconsulta de IDs de equipo del deporte (para filtrar listados por equipo, p. ej. escudos)."""
    return (select(Team.id).join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
            .join(Competition, Competition.id == CompetitionGroup.competition_id)
            .where(Competition.modality.in_(sports(sport))))
