-- Última clasificación oficial publicada de cada grupo (captura correcta de la jornada más reciente).
SELECT x.group_id, x.round_number, r.position, r.team_id, r.team_name, r.played, r.won, r.drawn, r.lost,
       r.goals_for, r.goals_against, r.points
FROM (SELECT DISTINCT ON (group_id) id, group_id, round_number
      FROM standings_snapshots WHERE status = 'ok'
      ORDER BY group_id, round_number DESC, last_seen_at DESC, id DESC) x
JOIN standings_rows r ON r.snapshot_id = x.id
ORDER BY x.group_id, r.position
