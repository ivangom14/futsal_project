-- Equipos por grupo.
SELECT t.id AS team_id, t.name, t.group_id
FROM teams t
ORDER BY t.group_id, t.name, t.external_id
