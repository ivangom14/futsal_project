-- Goles (y goles en propia) de la última observación del acta de cada partido, con el equipo del jugador.
SELECT l.match_id, e.sequence, e.event_type, e.team_id, COALESCE(e.player_name, '') AS player_name, e.minute
FROM (SELECT DISTINCT ON (r.id) r.match_id, o.id AS obs_id
      FROM match_reports r JOIN match_report_observations o ON o.report_id = r.id
      ORDER BY r.id, o.observed_at DESC, o.id DESC) l
JOIN match_events e ON e.observation_id = l.obs_id
WHERE e.event_type IN ('goal', 'own_goal')
ORDER BY l.match_id, e.sequence
