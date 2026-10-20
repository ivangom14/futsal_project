"""base de conocimiento documental (Fase 15): documentos, versiones, fragmentos y ejecuciones

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # unaccent no es IMMUTABLE; el envoltorio permite usarlo en columnas generadas e índices.
    op.execute("""CREATE OR REPLACE FUNCTION kb_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$""")

    op.create_table(
        'kb_documents',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_key', sa.Text, nullable=False),
        sa.Column('title', sa.Text, nullable=False),
        sa.Column('source_url', sa.Text),
        sa.Column('doc_type', sa.Text, nullable=False),
        sa.Column('language', sa.Text, nullable=False),
        sa.Column('issuer', sa.Text),
        sa.Column('publication_date', sa.Date),
        sa.Column('effective_date', sa.Date),
        sa.Column('version_label', sa.Text),
        sa.Column('season_label', sa.Text),
        sa.Column('structure_profile', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('doc_key', name=op.f('uq_kb_documents_doc_key')),
    )
    op.create_table(
        'kb_document_versions',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('document_id', sa.BigInteger,
                  sa.ForeignKey('kb_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('content_hash', sa.Text, nullable=False),
        sa.Column('file_path', sa.Text, nullable=False),
        sa.Column('file_bytes', sa.BigInteger, nullable=False),
        sa.Column('page_count', sa.Integer),
        sa.Column('version_label', sa.Text),
        sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('extraction_status', sa.Text, nullable=False, server_default='pending'),
        sa.Column('index_status', sa.Text, nullable=False, server_default='pending'),
        sa.Column('is_current', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('chunk_config', pg.JSONB),
        sa.Column('quality', pg.JSONB),
        sa.UniqueConstraint('document_id', 'content_hash',
                            name=op.f('uq_kb_document_versions_document_id_content_hash')),
        sa.CheckConstraint("extraction_status IN ('pending','ok','partial','failed')",
                           name=op.f('ck_kb_document_versions_extraction_valid')),
        sa.CheckConstraint("index_status IN ('pending','indexed','failed')",
                           name=op.f('ck_kb_document_versions_index_valid')),
    )
    op.create_index('uq_kb_document_versions_current', 'kb_document_versions', ['document_id'],
                    unique=True, postgresql_where=sa.text('is_current'))
    op.create_table(
        'kb_chunks',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('version_id', sa.BigInteger,
                  sa.ForeignKey('kb_document_versions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer, nullable=False),
        sa.Column('part', sa.Text),
        sa.Column('rule_number', sa.Integer),
        sa.Column('rule_title', sa.Text),
        sa.Column('section_number', sa.Text),
        sa.Column('section_title', sa.Text),
        sa.Column('subsection_title', sa.Text),
        sa.Column('page_start', sa.Integer, nullable=False),
        sa.Column('page_end', sa.Integer, nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('char_count', sa.Integer, nullable=False),
        sa.Column('search_vector', pg.TSVECTOR, sa.Computed(
            "setweight(to_tsvector('spanish', kb_unaccent(coalesce(rule_title,'') || ' ' || "
            "coalesce(section_title,'') || ' ' || coalesce(subsection_title,''))), 'A') || "
            "setweight(to_tsvector('spanish', kb_unaccent(text)), 'B')", persisted=True)),
        sa.UniqueConstraint('version_id', 'ordinal', name=op.f('uq_kb_chunks_version_id_ordinal')),
    )
    op.create_index('ix_kb_chunks_search_vector', 'kb_chunks', ['search_vector'],
                    postgresql_using='gin')
    op.execute("CREATE INDEX ix_kb_chunks_text_trgm ON kb_chunks "
               "USING gin (kb_unaccent(lower(text)) gin_trgm_ops)")
    op.create_table(
        'kb_ingestion_runs',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_key', sa.Text, nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('outcome', sa.Text, nullable=False, server_default='running'),
        sa.Column('version_id', sa.BigInteger,
                  sa.ForeignKey('kb_document_versions.id', ondelete='SET NULL')),
        sa.Column('stats', pg.JSONB),
        sa.Column('warnings', pg.JSONB),
        sa.Column('error', sa.Text),
        sa.CheckConstraint("outcome IN ('running','new','unchanged','updated','restored','reindexed','failed')",
                           name=op.f('ck_kb_ingestion_runs_outcome_valid')),
    )


def downgrade() -> None:
    op.drop_table('kb_ingestion_runs')
    op.drop_table('kb_chunks')
    op.drop_table('kb_document_versions')
    op.drop_table('kb_documents')
    op.execute("DROP FUNCTION IF EXISTS kb_unaccent(text)")
