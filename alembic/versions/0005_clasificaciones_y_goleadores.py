"""clasificaciones y tablas de goleadores oficiales de la RFFM (Fase 16): capturas, filas e intentos de ingesta

Solo crea tablas nuevas; no modifica ni borra datos de resultados, actas ni documentos.

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.func.now() if default else None)


def upgrade() -> None:
    op.create_table(
        'standings_snapshots',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.BigInteger, sa.ForeignKey('competition_groups.id'), nullable=False),
        sa.Column('round_number', sa.Integer, nullable=False),
        sa.Column('round_date', sa.Date),
        sa.Column('current_round', sa.Integer),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('row_count', sa.Integer, nullable=False),
        sa.Column('expected_teams', sa.Integer),
        sa.Column('source_url', sa.Text, nullable=False),
        _ts('fetched_at'),
        _ts('first_seen_at', default=True),
        _ts('last_seen_at', default=True),
        sa.Column('meta', pg.JSONB),
        sa.UniqueConstraint('group_id', 'round_number', 'content_hash',
                            name=op.f('uq_standings_snapshots_group_id_round_number_content_hash')),
        sa.CheckConstraint("status IN ('ok','empty','incomplete')", name=op.f('ck_standings_snapshots_status_valid')),
    )
    op.create_index(op.f('ix_standings_snapshots_group_id'), 'standings_snapshots', ['group_id'])
    op.create_index('ix_standings_snapshots_group_round_seen', 'standings_snapshots', ['group_id', 'round_number', 'last_seen_at'])
    op.create_table(
        'standings_rows',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('snapshot_id', sa.BigInteger, sa.ForeignKey('standings_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer, nullable=False),
        sa.Column('team_external_id', sa.String(64), nullable=False),
        sa.Column('team_id', sa.BigInteger, sa.ForeignKey('teams.id')),
        sa.Column('team_name', sa.String(256), nullable=False),
        sa.Column('played', sa.Integer, nullable=False),
        sa.Column('won', sa.Integer, nullable=False),
        sa.Column('drawn', sa.Integer, nullable=False),
        sa.Column('lost', sa.Integer, nullable=False),
        sa.Column('goals_for', sa.Integer, nullable=False),
        sa.Column('goals_against', sa.Integer, nullable=False),
        sa.Column('points', sa.Integer, nullable=False),
        sa.Column('penalty_points', sa.Integer),
        sa.Column('details', pg.JSONB),
        sa.UniqueConstraint('snapshot_id', 'team_external_id', name=op.f('uq_standings_rows_snapshot_id_team_external_id')),
    )
    op.create_index(op.f('ix_standings_rows_snapshot_id'), 'standings_rows', ['snapshot_id'])
    op.create_index(op.f('ix_standings_rows_team_id'), 'standings_rows', ['team_id'])

    op.create_table(
        'scorer_snapshots',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.BigInteger, sa.ForeignKey('competition_groups.id'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('row_count', sa.Integer, nullable=False),
        sa.Column('goals_total', sa.Integer, nullable=False),
        sa.Column('source_url', sa.Text, nullable=False),
        _ts('fetched_at'),
        _ts('first_seen_at', default=True),
        _ts('last_seen_at', default=True),
        sa.Column('meta', pg.JSONB),
        sa.UniqueConstraint('group_id', 'content_hash', name=op.f('uq_scorer_snapshots_group_id_content_hash')),
        sa.CheckConstraint("status IN ('ok','empty','incomplete')", name=op.f('ck_scorer_snapshots_status_valid')),
    )
    op.create_index(op.f('ix_scorer_snapshots_group_id'), 'scorer_snapshots', ['group_id'])
    op.create_index('ix_scorer_snapshots_group_seen', 'scorer_snapshots', ['group_id', 'last_seen_at'])
    op.create_table(
        'scorer_rows',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('snapshot_id', sa.BigInteger, sa.ForeignKey('scorer_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('row_order', sa.Integer, nullable=False),
        sa.Column('player_external_id', sa.String(64), nullable=False),
        sa.Column('player_id', sa.BigInteger, sa.ForeignKey('players.id')),
        sa.Column('player_name', sa.String(256), nullable=False),
        sa.Column('team_external_id', sa.String(64), nullable=False),
        sa.Column('team_id', sa.BigInteger, sa.ForeignKey('teams.id')),
        sa.Column('team_name', sa.String(256), nullable=False),
        sa.Column('matches_played', sa.Integer),
        sa.Column('goals', sa.Integer, nullable=False),
        sa.Column('penalty_goals', sa.Integer),
        sa.Column('goals_per_match', sa.Numeric(6, 2)),
        sa.Column('details', pg.JSONB),
        sa.UniqueConstraint('snapshot_id', 'row_order', name=op.f('uq_scorer_rows_snapshot_id_row_order')),
    )
    op.create_index('ix_scorer_rows_snapshot_player', 'scorer_rows', ['snapshot_id', 'player_external_id'])
    op.create_index(op.f('ix_scorer_rows_player_id'), 'scorer_rows', ['player_id'])
    op.create_index(op.f('ix_scorer_rows_team_id'), 'scorer_rows', ['team_id'])

    op.create_table(
        'table_ingest_attempts',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.BigInteger, sa.ForeignKey('competition_groups.id'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('requested_round', sa.Integer),
        sa.Column('outcome', sa.String(24), nullable=False),
        sa.Column('change', sa.String(16)),
        sa.Column('error_code', sa.String(32)),
        sa.Column('error_message', sa.Text),
        sa.Column('snapshot_id', sa.BigInteger),
        sa.Column('returned_round', sa.Integer),
        sa.Column('http_requests', sa.Integer, nullable=False, server_default='0'),
        sa.Column('warnings', pg.JSONB),
        _ts('attempted_at', default=True),
        sa.CheckConstraint("kind IN ('standings','scorers')", name=op.f('ck_table_ingest_attempts_kind_valid')),
        sa.CheckConstraint("outcome IN ('ok','empty','incomplete','not_applicable','failed','round_unavailable')",
                           name=op.f('ck_table_ingest_attempts_outcome_valid')),
    )
    op.create_index('ix_table_ingest_attempts_group_kind_at', 'table_ingest_attempts', ['group_id', 'kind', 'attempted_at'])


def downgrade() -> None:
    for table in ('table_ingest_attempts', 'scorer_rows', 'scorer_snapshots', 'standings_rows', 'standings_snapshots'):
        op.drop_table(table)
