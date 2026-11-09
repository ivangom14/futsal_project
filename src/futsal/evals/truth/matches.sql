-- Todos los partidos con jornada, nombres, marcador, estado y si hay acta almacenada.
SELECT m.id AS match_id, m.group_id, rd.visible_number AS round, m.home_team_id, m.away_team_id,
       th.name AS home, ta.name AS away, m.home_score, m.away_score, m.status, m.scheduled_date AS day, m.venue,
       EXISTS (SELECT 1 FROM match_reports r WHERE r.match_id = m.id) AS has_report
FROM matches m
JOIN rounds rd ON rd.id = m.round_id
JOIN teams th ON th.id = m.home_team_id
JOIN teams ta ON ta.id = m.away_team_id
ORDER BY m.group_id, rd.visible_number, m.scheduled_date, m.scheduled_time, m.external_id
