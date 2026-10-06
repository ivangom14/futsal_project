"""permitir event_type own_goal (tipo_gol 102 = gol en propia meta)

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAME = op.f('ck_match_events_type_valid')


def upgrade() -> None:
    op.drop_constraint(NAME, 'match_events', type_='check')
    op.create_check_constraint(NAME, 'match_events', "event_type IN ('goal','own_goal','card')")


def downgrade() -> None:
    op.drop_constraint(NAME, 'match_events', type_='check')
    op.create_check_constraint(NAME, 'match_events', "event_type IN ('goal','card')")
