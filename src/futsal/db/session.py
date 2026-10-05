"""Engine y sesiones síncronas."""

from sqlalchemy import Engine, create_engine

from futsal.config.settings import database_url


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)
