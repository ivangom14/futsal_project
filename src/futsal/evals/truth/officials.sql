-- Árbitros y demás oficiales de la última observación del acta de cada partido (solo los partidos con acta almacenada).
SELECT m.id AS match_id, m.group_id, o.display_name AS official, o.role, COALESCE(o.external_id, '') AS external_id
FROM (SELECT DISTINCT ON (r.id) r.match_id, ob.id AS obs_id
      FROM match_reports r JOIN match_report_observations ob ON ob.report_id = r.id
      ORDER BY r.id, ob.observed_at DESC, ob.id DESC) l
JOIN matches m ON m.id = l.match_id
JOIN match_officials o ON o.observation_id = l.obs_id
ORDER BY m.id, o.sequence
