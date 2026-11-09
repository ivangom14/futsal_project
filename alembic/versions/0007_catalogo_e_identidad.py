"""catálogo e identidad (revisión A2): metadatos oficiales y clasificación de competiciones, identidad de equipo y club, enlaces entre temporadas

Solo añade: columnas nulas o con valor por defecto seguro, tablas nuevas y la vista `team_lineage`. No renombra ni borra nada
ni modifica filas existentes. `downgrade` elimina únicamente lo añadido, en orden inverso.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0007'
down_revision: str | Sequence[str] | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNKNOWN = sa.text("'unknown'")
TEXT_ATTRS = ('modality', 'scope', 'category', 'gender', 'kind', 'phase', 'division')
COMPETITION_COLUMNS = (
    'game_type_code', 'category_code', 'category_name', 'category_group_code', 'category_group_name', 'format_code',
    'active', 'start_date', 'end_date', 'source_meta', *TEXT_ATTRS, 'classification', 'review_status', 'removed_at')
GROUP_COLUMNS = ('expected_rounds', 'expected_teams', 'delegation', 'has_standings', 'has_scorers', 'removed_at')

TEAM_LINEAGE = """
CREATE VIEW team_lineage AS
SELECT t.id AS team_id, t.source, t.external_id AS team_external_id, t.name AS team_name,
       t.team_entity_id, te.club_id,
       s.id AS season_id, s.label AS season_label, s.name AS season_name,
       c.id AS competition_id, c.name AS competition_name, c.modality, c.category, c.gender,
       g.id AS group_id, g.name AS group_name
FROM teams t
JOIN competition_groups g ON g.id = t.group_id
JOIN competitions c ON c.id = g.competition_id
JOIN seasons s ON s.id = c.season_id
LEFT JOIN team_entities te ON te.id = t.team_entity_id
"""


def upgrade() -> None:
    op.add_column('seasons', sa.Column('label', sa.Text))
    op.add_column('seasons', sa.Column('start_date', sa.Date))
    op.add_column('seasons', sa.Column('end_date', sa.Date))
    op.add_column('seasons', sa.Column('is_current', sa.Boolean, nullable=False, server_default=sa.text('false')))
    op.create_index('uq_seasons_source_current', 'seasons', ['source'], unique=True, postgresql_where=sa.text('is_current'))

    for name in ('game_type_code', 'category_code', 'category_name', 'category_group_code', 'category_group_name', 'format_code'):
        op.add_column('competitions', sa.Column(name, sa.Text))
    op.add_column('competitions', sa.Column('active', sa.Boolean))
    op.add_column('competitions', sa.Column('start_date', sa.Date))
    op.add_column('competitions', sa.Column('end_date', sa.Date))
    op.add_column('competitions', sa.Column('source_meta', pg.JSONB))
    for name in TEXT_ATTRS:
        op.add_column('competitions', sa.Column(name, sa.Text, nullable=False, server_default=UNKNOWN))
    op.add_column('competitions', sa.Column('classification', pg.JSONB))
    op.add_column('competitions', sa.Column('review_status', sa.Text, nullable=False, server_default=sa.text("'needs_review'")))
    op.add_column('competitions', sa.Column('removed_at', sa.DateTime(timezone=True)))
    op.create_check_constraint(op.f('ck_competitions_review_status_valid'), 'competitions',
                               "review_status IN ('auto','needs_review','confirmed')")
    op.create_index('ix_competitions_season_id_modality_category_gender', 'competitions',
                    ['season_id', 'modality', 'category', 'gender'])
    op.create_index('ix_competitions_review_status', 'competitions', ['review_status'])
    op.create_index('ix_competitions_source_category_code', 'competitions', ['source', 'category_code'])

    op.add_column('competition_groups', sa.Column('expected_rounds', sa.Integer))
    op.add_column('competition_groups', sa.Column('expected_teams', sa.Integer))
    op.add_column('competition_groups', sa.Column('delegation', sa.Text))
    op.add_column('competition_groups', sa.Column('has_standings', sa.Boolean))
    op.add_column('competition_groups', sa.Column('has_scorers', sa.Boolean))
    op.add_column('competition_groups', sa.Column('removed_at', sa.DateTime(timezone=True)))

    op.create_table(
        'clubs',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('external_code', sa.Text, nullable=False),
        sa.Column('name', sa.Text),
        sa.Column('normalized_name', sa.Text),
        sa.Column('crest_url', sa.Text),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('source', 'external_code', name=op.f('uq_clubs_source_external_code')),
    )
    op.create_table(
        'team_entities',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('external_code', sa.Text, nullable=False),
        sa.Column('club_id', sa.BigInteger, sa.ForeignKey('clubs.id')),
        sa.Column('club_link_status', sa.Text, nullable=False, server_default=UNKNOWN),
        sa.Column('name', sa.Text),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('source', 'external_code', name=op.f('uq_team_entities_source_external_code')),
        sa.CheckConstraint("club_link_status IN ('official','override','unknown')",
                           name=op.f('ck_team_entities_club_link_status_valid')),
    )
    op.add_column('teams', sa.Column('team_entity_id', sa.BigInteger, sa.ForeignKey('team_entities.id')))
    op.add_column('teams', sa.Column('squad', sa.Text))
    op.add_column('teams', sa.Column('crest_url', sa.Text))
    op.create_index('ix_teams_team_entity_id', 'teams', ['team_entity_id'])

    op.create_table(
        'competition_links',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('from_competition_id', sa.BigInteger, sa.ForeignKey('competitions.id'), nullable=False),
        sa.Column('to_competition_id', sa.BigInteger, sa.ForeignKey('competitions.id'), nullable=False),
        sa.Column('basis', sa.Text, nullable=False),
        sa.Column('confidence', sa.Numeric(3, 2), nullable=False),
        sa.Column('status', sa.Text, nullable=False, server_default=sa.text("'proposed'")),
        sa.UniqueConstraint('from_competition_id', 'to_competition_id',
                            name=op.f('uq_competition_links_from_competition_id_to_competition_id')),
        sa.CheckConstraint("status IN ('proposed','confirmed','rejected')", name=op.f('ck_competition_links_status_valid')),
        sa.CheckConstraint("basis IN ('category_code+name','override')", name=op.f('ck_competition_links_basis_valid')),
    )
    op.create_table(
        'catalog_overrides',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('kind', sa.Text, nullable=False),
        sa.Column('target_external_id', sa.Text, nullable=False),
        sa.Column('fields', pg.JSONB, nullable=False),
        sa.Column('reason', sa.Text),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', sa.Text),
        sa.CheckConstraint("kind IN ('competition','club_link','competition_link')", name=op.f('ck_catalog_overrides_kind_valid')),
    )
    op.execute(TEAM_LINEAGE)


def downgrade() -> None:
    op.execute('DROP VIEW team_lineage')
    op.drop_table('catalog_overrides')
    op.drop_table('competition_links')
    op.drop_index('ix_teams_team_entity_id', table_name='teams')
    for name in ('crest_url', 'squad', 'team_entity_id'):
        op.drop_column('teams', name)
    op.drop_table('team_entities')
    op.drop_table('clubs')
    for name in reversed(GROUP_COLUMNS):
        op.drop_column('competition_groups', name)
    op.drop_index('ix_competitions_source_category_code', table_name='competitions')
    op.drop_index('ix_competitions_review_status', table_name='competitions')
    op.drop_index('ix_competitions_season_id_modality_category_gender', table_name='competitions')
    op.drop_constraint(op.f('ck_competitions_review_status_valid'), 'competitions', type_='check')
    for name in reversed(COMPETITION_COLUMNS):
        op.drop_column('competitions', name)
    op.drop_index('uq_seasons_source_current', table_name='seasons')
    for name in ('is_current', 'end_date', 'start_date', 'label'):
        op.drop_column('seasons', name)
