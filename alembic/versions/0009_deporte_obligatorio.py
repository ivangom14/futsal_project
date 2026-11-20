"""deporte obligatorio (Fase 1 multideporte): modality como deporte, identidad de equipo por deporte y normas por deporte

- `competitions.modality` pasa a ser el DEPORTE de la competición, obligatorio y validado (CHECK) y sin valor por defecto: cada
  alta debe declararlo. Se rellena desde el campo oficial `game_type_code` (tipojuego de la RFFM: 1 fútbol 11, 3 fútbol sala…).
  Las filas sin `game_type_code` (importaciones anteriores a la 0007 por `import-league`) se marcan `futsal`: hasta esta migración
  TODA la ingesta usó tipojuego=3 (objetivo fijo y el parser rechaza otro `idGameType`); queda constancia en
  `classification.modality` (`source = migration_0009`). Un `game_type_code` sin correspondencia ABORTA la migración (no se adivina).
- `team_entities.sport`: el `CodEquipo` oficial no se ha verificado exclusivo de una modalidad; la identidad pasa a ser
  `(source, sport, external_code)`, de modo que un mismo código en dos deportes son dos entidades (nunca se fusionan ni se rechazan).
- `kb_documents.sports` (text[]): deportes a los que se aplica cada documento normativo; los actuales son de fútbol sala.

`downgrade` deshace el esquema (no restaura `unknown` en `modality`: el valor rellenado es correcto en ambos sentidos) y falla si ya
hay dos entidades de equipo con el mismo código en deportes distintos (no se pueden volver a unir sin perder datos).

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = '0009'
down_revision: str | Sequence[str] | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GAME_TYPES = {'1': 'football11', '2': 'football7', '3': 'futsal', '4': 'football5', '5': 'beach'}  # config/sources/rffm.json
MODALITIES = ('futsal', 'football11', 'football7', 'football5', 'beach')
KB_SPORTS = ('futsal', 'football11')


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    conn = op.get_bind()
    case = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in GAME_TYPES.items())
    op.execute(f"UPDATE competitions SET modality = CASE btrim(game_type_code) {case} END "
               f"WHERE btrim(game_type_code) IN ({_in(list(GAME_TYPES))})")
    unmapped = conn.execute(sa.text(
        "SELECT external_id, game_type_code FROM competitions "
        f"WHERE game_type_code IS NOT NULL AND btrim(game_type_code) NOT IN ({_in(list(GAME_TYPES))})")).all()
    if unmapped:
        raise RuntimeError(f"0009: competiciones con game_type_code sin correspondencia (no se adivina el deporte): {unmapped[:5]}")
    op.execute("""
        UPDATE competitions SET modality = 'futsal',
          classification = coalesce(classification, '{}'::jsonb) || jsonb_build_object('modality', jsonb_build_object(
            'value', 'futsal', 'source', 'migration_0009', 'detail', 'sin game_type_code: ingesta anterior a 0009 (tipojuego=3)',
            'confidence', 1.0))
        WHERE game_type_code IS NULL OR btrim(game_type_code) = ''""")
    op.alter_column('competitions', 'modality', server_default=None, existing_type=sa.Text, nullable=False)
    op.create_check_constraint(op.f('ck_competitions_modality_valid'), 'competitions', f"modality IN ({_in(MODALITIES)})")

    op.add_column('team_entities', sa.Column('sport', sa.Text, nullable=True))
    mixed = conn.execute(sa.text("""
        SELECT te.external_code FROM team_entities te JOIN teams t ON t.team_entity_id = te.id
        JOIN competition_groups g ON g.id = t.group_id JOIN competitions c ON c.id = g.competition_id
        GROUP BY te.id, te.external_code HAVING count(DISTINCT c.modality) > 1""")).all()
    if mixed:
        raise RuntimeError(f"0009: entidades de equipo enlazadas a varios deportes: {[m[0] for m in mixed[:5]]}")
    op.execute("""
        UPDATE team_entities te SET sport = sub.modality FROM (
          SELECT DISTINCT t.team_entity_id AS id, c.modality FROM teams t
          JOIN competition_groups g ON g.id = t.group_id JOIN competitions c ON c.id = g.competition_id
          WHERE t.team_entity_id IS NOT NULL) sub
        WHERE sub.id = te.id""")
    # entidades sin equipos enlazados (no se borran): anteriores a 0009, solo pudo crearlas la ingesta de fútbol sala
    op.execute("UPDATE team_entities SET sport = 'futsal' WHERE sport IS NULL")
    op.alter_column('team_entities', 'sport', nullable=False)
    op.create_check_constraint(op.f('ck_team_entities_sport_valid'), 'team_entities', f"sport IN ({_in(MODALITIES)})")
    op.drop_constraint(op.f('uq_team_entities_source_external_code'), 'team_entities', type_='unique')
    op.create_unique_constraint(op.f('uq_team_entities_source_sport_external_code'), 'team_entities',
                                ['source', 'sport', 'external_code'])

    op.add_column('kb_documents', sa.Column('sports', pg.ARRAY(sa.Text), nullable=False,
                                            server_default=sa.text("ARRAY['futsal']::text[]")))
    op.alter_column('kb_documents', 'sports', server_default=None)
    op.create_check_constraint(op.f('ck_kb_documents_sports_valid'), 'kb_documents',
                               f"cardinality(sports) > 0 AND sports <@ ARRAY[{_in(KB_SPORTS)}]::text[]")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_kb_documents_sports_valid'), 'kb_documents', type_='check')
    op.drop_column('kb_documents', 'sports')
    op.drop_constraint(op.f('uq_team_entities_source_sport_external_code'), 'team_entities', type_='unique')
    op.create_unique_constraint(op.f('uq_team_entities_source_external_code'), 'team_entities', ['source', 'external_code'])
    op.drop_constraint(op.f('ck_team_entities_sport_valid'), 'team_entities', type_='check')
    op.drop_column('team_entities', 'sport')
    op.drop_constraint(op.f('ck_competitions_modality_valid'), 'competitions', type_='check')
    op.alter_column('competitions', 'modality', server_default=sa.text("'unknown'"), existing_type=sa.Text)
