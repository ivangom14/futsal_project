-- Equipos con el nombre base de su club (sin el sufijo A/B/C/II/2; mismo criterio que `catalog.base_name`).
SELECT t.id AS team_id, t.name, t.group_id,
       COALESCE(NULLIF(trim(regexp_replace(t.name, '[\s-]*[(''"]?\s*\m(A|B|C|II|2)\M\s*[)''"]?\s*$', '', 'i')), ''), trim(t.name)) AS club_base
FROM teams t
ORDER BY t.group_id, t.name, t.id
