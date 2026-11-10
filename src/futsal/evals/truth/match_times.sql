-- Hora de cada partido (hora local de Europe/Madrid, tal como la publica la federación), franja del día y si el acta está cerrada.
-- Franjas: manana < 14:00; tarde >= 16:00; mediodia = el resto (las preguntas de franja evitan los días con partidos a mediodía).
SELECT m.id AS match_id, m.scheduled_time AS kickoff_time,
       (m.scheduled_date + m.scheduled_time) AT TIME ZONE 'Europe/Madrid' AS kickoff_utc,
       CASE WHEN m.scheduled_time IS NULL THEN 'sin_hora' WHEN m.scheduled_time < TIME '14:00' THEN 'manana'
            WHEN m.scheduled_time >= TIME '16:00' THEN 'tarde' ELSE 'mediodia' END AS slot,
       (SELECT bool_and(COALESCE(ob.closed, false)) FROM match_reports r JOIN match_report_observations ob ON ob.report_id = r.id
         WHERE r.match_id = m.id AND ob.id = (SELECT max(o2.id) FROM match_report_observations o2 WHERE o2.report_id = r.id)) AS report_closed
FROM matches m
ORDER BY m.id
