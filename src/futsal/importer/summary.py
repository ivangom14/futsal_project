"""Consultas agregadas reales sobre PostgreSQL."""

from typing import Any

from sqlalchemy import Engine, func, select

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    DataQualityIssue,
    IngestionRun,
    Match,
    MatchObservation,
    Round,
    Season,
    Team,
)


def db_summary(engine: Engine) -> dict[str, Any]:
    tables = {"temporadas": Season, "competiciones": Competition, "grupos": CompetitionGroup,
              "jornadas": Round, "equipos": Team, "partidos": Match,
              "observaciones": MatchObservation, "ejecuciones_importacion": IngestionRun,
              "incidencias": DataQualityIssue}
    with engine.connect() as conn:
        out: dict[str, Any] = {
            name: conn.scalar(select(func.count()).select_from(model)) or 0
            for name, model in tables.items()
        }
        rows = conn.execute(select(Match.status, func.count()).group_by(Match.status)).all()
        out["partidos_por_estado"] = {status: n for status, n in sorted(rows)}
    return out
