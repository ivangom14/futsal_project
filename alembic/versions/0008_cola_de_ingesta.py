"""cola de ingesta (T06a): trabajos, registro de peticiones e interruptor por fuente

Solo añade tablas nuevas e índices; no toca filas ni columnas existentes. `downgrade` elimina únicamente lo añadido.

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0008'
down_revision: str | Sequence[str] | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text('now()')


def upgrade() -> None:
    op.create_table(
        'ingest_jobs',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('target', pg.JSONB, nullable=False),
        sa.Column('target_key', sa.Text, nullable=False),
        sa.Column('priority', sa.Integer, nullable=False, server_default=sa.text('0')),
        sa.Column('not_before', sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column('status', sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('attempts', sa.Integer, nullable=False, server_default=sa.text('0')),
        sa.Column('max_attempts', sa.Integer, nullable=False, server_default=sa.text('4')),
        sa.Column('last_error', sa.Text),
        sa.Column('last_http_status', sa.Integer),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('worker', sa.Text),
        sa.Column('checkpoint', pg.JSONB),
        sa.Column('run_id', sa.BigInteger,
                  sa.ForeignKey('ingestion_runs.id', name=op.f('fk_ingest_jobs_run_id_ingestion_runs'))),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending','running','done','failed','dead','cancelled')",
                           name=op.f('ck_ingest_jobs_status_valid')),
    )
    op.create_index('uq_ingest_jobs_live_key', 'ingest_jobs', ['source', 'kind', 'target_key'], unique=True,
                    postgresql_where=sa.text("status IN ('pending','running','failed')"))
    op.create_index('ix_ingest_jobs_status_priority_not_before', 'ingest_jobs',
                    ['status', sa.text('priority DESC'), 'not_before'])
    op.create_index('ix_ingest_jobs_run_id', 'ingest_jobs', ['run_id'])

    op.create_table(
        'raw_fetches',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('job_id', sa.BigInteger,
                  sa.ForeignKey('ingest_jobs.id', name=op.f('fk_raw_fetches_job_id_ingest_jobs'))),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('url', sa.Text, nullable=False),
        sa.Column('method', sa.String(8), nullable=False, server_default=sa.text("'GET'")),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status_code', sa.Integer),
        sa.Column('bytes', sa.Integer),
        sa.Column('sha256', sa.String(64)),
        sa.Column('content_type', sa.Text),
        sa.Column('snapshot_path', sa.Text),
        sa.Column('elapsed_ms', sa.Integer),
        sa.Column('parse_status', sa.String(16), nullable=False),
        sa.Column('error', sa.Text),
        sa.CheckConstraint(
            "parse_status IN ('ok','null_body','scope_mismatch','schema_error','http_error','network_error')",
            name=op.f('ck_raw_fetches_parse_status_valid')),
    )
    op.create_index('ix_raw_fetches_job_id', 'raw_fetches', ['job_id'])
    op.create_index('ix_raw_fetches_source_requested_at', 'raw_fetches', ['source', 'requested_at'])

    op.create_table(
        'ingest_controls',
        sa.Column('source', sa.String(32), primary_key=True),
        sa.Column('paused', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('reason', sa.Text),
        sa.Column('opened_by', sa.String(16)),
        sa.Column('opened_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("opened_by IS NULL OR opened_by IN ('manual','breaker')",
                           name=op.f('ck_ingest_controls_opened_by_valid')),
    )


def downgrade() -> None:
    op.drop_table('ingest_controls')
    op.drop_index('ix_raw_fetches_source_requested_at', table_name='raw_fetches')
    op.drop_index('ix_raw_fetches_job_id', table_name='raw_fetches')
    op.drop_table('raw_fetches')
    op.drop_index('ix_ingest_jobs_run_id', table_name='ingest_jobs')
    op.drop_index('ix_ingest_jobs_status_priority_not_before', table_name='ingest_jobs')
    op.drop_index('uq_ingest_jobs_live_key', table_name='ingest_jobs')
    op.drop_table('ingest_jobs')
