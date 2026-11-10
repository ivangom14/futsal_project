-- Ranking de goleadores OFICIALES por categoría y género: la última tabla oficial de cada grupo, agrupada por la categoría que
-- se deduce del NOMBRE de la competición (el mismo clasificador que `catalog.categorize`; los snapshots sin las columnas de la
-- migración 0007 las tienen como «unknown»). Los grupos distintos NO son comparables entre sí: son tablas separadas.
WITH latest AS (
    SELECT DISTINCT ON (group_id) id, group_id FROM scorer_snapshots WHERE status = 'ok'
    ORDER BY group_id, last_seen_at DESC, id DESC),
cls AS (
    SELECT g.id AS group_id, c.name AS competition, g.name AS group_name,
           CASE WHEN translate(lower(c.name), 'áéíóú', 'aeiou') LIKE '%femenin%' THEN 'femenino'
                WHEN translate(lower(c.name), 'áéíóú', 'aeiou') LIKE '%cadete%' THEN 'cadete'
                WHEN translate(lower(c.name), 'áéíóú', 'aeiou') LIKE '%juvenil%' THEN 'juvenil'
                ELSE 'senior' END AS category
    FROM competition_groups g JOIN competitions c ON c.id = g.competition_id),
scored AS (
    SELECT cls.category, cls.group_id, cls.competition, cls.group_name, r.player_name, r.team_name, r.goals
    FROM latest JOIN scorer_rows r ON r.snapshot_id = latest.id JOIN cls ON cls.group_id = latest.group_id)
SELECT category, CASE WHEN category = 'femenino' THEN 'femenino' ELSE 'masculino' END AS gender,
       group_id, competition, group_name, player_name, team_name, goals,
       rank() OVER (PARTITION BY category ORDER BY goals DESC) AS category_rank
FROM scored
ORDER BY category, category_rank, player_name
