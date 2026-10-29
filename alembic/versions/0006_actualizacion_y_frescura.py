"""actualización de datos (Fase 21): tipo y estadísticas de ejecución, detalle de incidencias y frescura por grupo y tipo de dato

Solo añade columnas con valor por defecto/NULL, amplía (superconjunto) el CHECK de estado de `ingestion_runs` y crea una tabla
nueva. No modifica ni borra filas existentes: las ejecuciones históricas siguen siendo válidas con `kind='import'`.

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUS = "status IN ('running','success','failed')"
NEW_STATUS = "status IN ('running','success','failed','partial','no_changes','cancelled','abandoned')"


def upgrade() -> None:
    op.add_column('ingestion_runs', sa.Column('kind', sa.String(24), nullable=False, server_default='import'))
    op.add_column('ingestion_runs', sa.Column('stats', pg.JSONB, nullable=True))
    op.drop_constraint(op.f('ck_ingestion_runs_status_valid'), 'ingestion_runs', type_='check')
    op.create_check_constraint(op.f('ck_ingestion_runs_status_valid'), 'ingestion_runs', NEW_STATUS)
    op.add_column('data_quality_issues', sa.Column('details', pg.JSONB, nullable=True))
    op.create_table(
        'dataset_refreshes',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.BigInteger, sa.ForeignKey('competition_groups.id'), nullable=False),
        sa.Column('dataset', sa.String(16), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_attempt_status', sa.String(16), nullable=False),
        sa.Column('last_attempt_run_id', sa.BigInteger, sa.ForeignKey('ingestion_runs.id')),
        sa.Column('last_attempt_message', sa.Text),
        sa.Column('last_valid_at', sa.DateTime(timezone=True)),
        sa.Column('last_valid_run_id', sa.BigInteger, sa.ForeignKey('ingestion_runs.id')),
        sa.Column('source_fetched_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('group_id', 'dataset', name=op.f('uq_dataset_refreshes_group_id_dataset')),
        sa.CheckConstraint("dataset IN ('matches','reports')", name=op.f('ck_dataset_refreshes_dataset_valid')),
    )


def downgrade() -> None:
    op.drop_table('dataset_refreshes')
    op.drop_column('data_quality_issues', 'details')
    op.execute("UPDATE ingestion_runs SET status='success' WHERE status IN ('partial','no_changes')")
    op.execute("UPDATE ingestion_runs SET status='failed' WHERE status IN ('cancelled','abandoned')")
    op.drop_constraint(op.f('ck_ingestion_runs_status_valid'), 'ingestion_runs', type_='check')
    op.create_check_constraint(op.f('ck_ingestion_runs_status_valid'), 'ingestion_runs', OLD_STATUS)
    op.drop_column('ingestion_runs', 'stats')
    op.drop_column('ingestion_runs', 'kind')
