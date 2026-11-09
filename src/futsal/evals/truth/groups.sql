-- Grupos con su competición, temporada y qué datos oficiales tienen (verdad del snapshot; solo lectura).
SELECT g.id AS group_id, g.name AS group_name, c.id AS competition_id, c.name AS competition,
       s.id AS season_id, COALESCE(s.name, s.external_id) AS season,
       (SELECT count(*) FROM teams t WHERE t.group_id = g.id) AS teams,
       (SELECT count(*) FROM matches m WHERE m.group_id = g.id AND m.status = 'finished') AS finished,
       (SELECT count(*) FROM matches m WHERE m.group_id = g.id AND m.status <> 'finished') AS pending,
       (SELECT count(*) FROM matches m JOIN match_reports r ON r.match_id = m.id WHERE m.group_id = g.id) AS reports,
       EXISTS (SELECT 1 FROM standings_snapshots x WHERE x.group_id = g.id AND x.status = 'ok') AS has_standings,
       EXISTS (SELECT 1 FROM scorer_snapshots x WHERE x.group_id = g.id AND x.status = 'ok') AS has_scorers
FROM competition_groups g
JOIN competitions c ON c.id = g.competition_id
JOIN seasons s ON s.id = c.season_id
ORDER BY COALESCE(s.name, s.external_id), c.name, g.name, g.external_id
