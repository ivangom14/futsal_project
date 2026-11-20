"""actas de fútbol 11 (Fase 3C): sustituciones como eventos y «otros técnicos» con su cargo oficial

Mínima y aditiva:
- `match_events.event_type` admite `substitution`. El jugador del evento (`player_*`) es el que ENTRA y `related_player_*` el que
  SALE (con el minuto). Las sustituciones no son goles: no cuentan en marcadores ni en goleadores.
- `match_staff.role_label`: cargo tal como figura en el acta (p. ej. «ENCARGADO DE MATERIAL») para los «otros técnicos»
  (`role = other_staff`). Nulo en el resto (entrenadores y delegados conservan su rol normalizado de siempre).
No toca filas existentes. `downgrade` falla si ya hay sustituciones guardadas (no se pueden representar sin perder datos).

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | Sequence[str] | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = op.f('ck_match_events_type_valid')


def upgrade() -> None:
    op.drop_constraint(NAME, 'match_events', type_='check')
    op.create_check_constraint(NAME, 'match_events', "event_type IN ('goal','own_goal','card','substitution')")
    op.add_column('match_events', sa.Column('related_player_id', sa.BigInteger, sa.ForeignKey('players.id'), nullable=True))
    op.add_column('match_events', sa.Column('related_player_external_id', sa.String(64), nullable=True))
    op.add_column('match_events', sa.Column('related_player_name', sa.String(256), nullable=True))
    op.add_column('match_staff', sa.Column('role_label', sa.Text, nullable=True))


def downgrade() -> None:
    n = op.get_bind().execute(sa.text("SELECT count(*) FROM match_events WHERE event_type = 'substitution'")).scalar_one()
    if n:
        raise RuntimeError(f"0010: hay {n} sustituciones guardadas; bajar las borraría")
    op.drop_column('match_staff', 'role_label')
    op.drop_column('match_events', 'related_player_name')
    op.drop_column('match_events', 'related_player_external_id')
    op.drop_column('match_events', 'related_player_id')
    op.drop_constraint(NAME, 'match_events', type_='check')
    op.create_check_constraint(NAME, 'match_events', "event_type IN ('goal','own_goal','card')")
