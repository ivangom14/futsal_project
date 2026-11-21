"""base del resultado (Fase 3E): en qué se apoya el marcador de un partido que no se disputó o no está confirmado

Una sola columna nullable, `result_basis`, en `matches` y en `match_observations` (para que el cambio quede versionado):
`withdrawal_home` / `withdrawal_away` (equipo retirado, `Retirado_*` de la RFFM), `resolution` («resolución» en `motivo_estado`),
`provisional` (en juego o provisional) y `unconfirmed` (marcador con el acta sin cerrar). NULL = partido normal (o sin marcador):
las filas existentes no cambian. Valores y significado en `futsal.domain.match_result`.
El relleno de lo ya guardado se hace sin red desde las instantáneas (`python -m futsal.cli backfill-result-basis`).
`downgrade` borra solo esta información (se puede recalcular otra vez desde las instantáneas).

Revision ID: 0011
Revises: 0010
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011'
down_revision: str | Sequence[str] | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VALUES = "('withdrawal_home','withdrawal_away','resolution','provisional','unconfirmed')"


def upgrade() -> None:
    for table in ('matches', 'match_observations'):
        op.add_column(table, sa.Column('result_basis', sa.String(16), nullable=True))
        op.create_check_constraint(op.f(f'ck_{table}_result_basis_valid'), table,
                                   f"result_basis IS NULL OR result_basis IN {VALUES}")


def downgrade() -> None:
    for table in ('match_observations', 'matches'):
        op.drop_constraint(op.f(f'ck_{table}_result_basis_valid'), table, type_='check')
        op.drop_column(table, 'result_basis')
