-- Última tabla oficial de goleadores de cada grupo, en el orden publicado.
SELECT x.group_id, r.row_order, r.player_name, r.team_id, r.team_name, r.matches_played, r.goals
FROM (SELECT DISTINCT ON (group_id) id, group_id
      FROM scorer_snapshots WHERE status = 'ok'
      ORDER BY group_id, last_seen_at DESC, id DESC) x
JOIN scorer_rows r ON r.snapshot_id = x.id
ORDER BY x.group_id, r.row_order
