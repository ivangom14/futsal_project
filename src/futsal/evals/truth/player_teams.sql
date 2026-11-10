-- Jugadores con los equipos en los que se les ha visto jugar (actas) o que figuran en una tabla oficial de goleadores.
SELECT DISTINCT p.id AS player_id, p.display_name, t.id AS team_id, t.name AS team_name, t.group_id
FROM players p
JOIN (SELECT player_id, team_id FROM player_team_memberships
      UNION
      SELECT r.player_id, r.team_id FROM scorer_rows r WHERE r.player_id IS NOT NULL AND r.team_id IS NOT NULL) x ON x.player_id = p.id
JOIN teams t ON t.id = x.team_id
ORDER BY p.display_name, p.id, t.id
