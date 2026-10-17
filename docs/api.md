# Fase 5 — API de consulta (solo lectura)

## Objetivo
API REST pequeña sobre el modelo persistido en PostgreSQL; capa estable para la futura capa MCP.
No conoce el scraper RFFM ni escribe datos.

## Arquitectura resultante
- `src/futsal/api/app.py`: FastAPI con `create_app(engine=None)`; reutiliza `futsal.db.models`,
  `make_engine()` y `DATABASE_URL` (`.env`). Sesión síncrona SQLAlchemy por petición.
- Sin dependencias de `ingestion`/`importer`. Tests: `tests/integration/test_api_pg.py`.

## Endpoints
Todos devuelven JSON; los listados usan `{"items": [...], "count": N}`. IDs = IDs internos de BD.

| Método y ruta | Notas |
|---|---|
| `GET /health` | 200 `{"status":"ok","database":"ok"}`; 503 si falla PostgreSQL |
| `GET /competitions` | filtro opcional `season_id`; cada competición incluye sus `groups` (`id`, `name`) |
| `GET /teams?name=` | equipos de todos los grupos por nombre (`group_id`, `group`, `competition_id`, `competition`); `limit` 1-100 (def. 30), `count`/`shown` |
| `GET /competitions/{id}/groups` | 404 si no existe la competición |
| `GET /groups/{id}/rounds` | ordenadas por número |
| `GET /groups/{id}/teams` | ordenados por nombre |
| `GET /groups/{id}/matches` | filtros `round_id`, `team_id` (partidos del equipo, local o visitante; 404 si no es del grupo), `status` (`scheduled`, `finished`, `postponed`, `suspended`, `cancelled`, `unknown`) |
| `GET /groups/{id}/matches` (`has_report`) | cada partido trae `has_report`; filtro `has_report=true|false` (p. ej. `status=finished&has_report=false` = finalizados sin acta descargada) |
| `GET /matches/{id}/report` | acta de UN partido; `sections` = `officials,goals` (def.), `cards`, `players`, `staff`. Sin acta: 200 con `report_available=false` y `reason`; partido inexistente: 404. Incluye `consistency` y `not_in_source` |
| `GET /groups/{id}/match-events` | eventos de las actas (def. `goal`,`own_goal`; `event_type=card`). Filtros `team_id`, `against_team_id`, `round_id`, `match_id`, `player_id`, `limit` (1-200, def. 40). `total`/`truncated` y `coverage` |
| `GET /groups/{id}/scorers` | goles agrupados por jugador en SQL (`min_goals`, mismos filtros, `limit` def. 20); `own_goals` aparte; `coverage` |
| `GET /groups/{id}/players?name=` | jugadores vistos en actas del grupo (sin acentos/mayúsculas, todas las palabras); devuelve `player_id` |
| `GET /matches/{id}` | partido + `venue`, `timezone`, `observations` (`match_observations`) |

Errores: 400 parámetros inválidos (tipo erróneo, `status` desconocido), 404 recurso inexistente,
500 error interno.

## Ejemplos
```
curl localhost:8000/health
{"status":"ok","database":"ok"}

curl "localhost:8000/groups/1/matches?status=finished"
{"items":[{"id":2,"group_id":1,"round_id":1,"round_number":1,"home_team_id":3,"away_team_id":4,
 "home_team":"PARQUE NORTE F.S. - RODILLITO 'B'","away_team":"C.D. LOPE DE VEGA 'B'",
 "date":"2026-09-26T14:00:00+00:00","status":"finished","home_score":3,"away_score":4}, ...],"count":13}

curl localhost:8000/matches/999999
{"detail":"partido 999999 no encontrado"}   # 404
```

## Ejecutar
```
pip install -e '.[dev]'
docker compose up -d --wait db && alembic upgrade head   # y datos importados (Fases 3-4)
uvicorn futsal.api.app:app_factory --factory --port 8000  # docs interactivas en /docs
```

## Tests
`python -m pytest tests/integration/test_api_pg.py -q` (BD temporal `futsal_test_*`; se omiten sin PostgreSQL).

## Decisiones
- FastAPI (ya previsto en la arquitectura); endpoints síncronos, SQLAlchemy 2 existente.
- Validación de entrada: se remapea el 422 de FastAPI a 400.
- `date` = `scheduled_at` ISO 8601 (UTC) y, si el partido no tiene hora publicada (152 de 182 hoy), `scheduled_date` (`AAAA-MM-DD`); `home_score`/`away_score` nulos si no hay marcador.
- Detalle de partido incluye observaciones de listado, no datos de acta (Fase 4).

## Limitaciones
- Sin paginación ni autenticación; sin clasificaciones, jugadores ni eventos de acta.
- Filtro de jornada por `round_id` interno (no por número).
- `season` es null si la temporada no tiene nombre importado.


## Actas (Fase 9) — lectura sobre `match_reports` / `match_events` / `match_officials`
Código: `src/futsal/api/reports.py` (rutas) y `src/futsal/repositories/report_queries.py` (SQL). Solo lectura: no descargan
ni importan (eso sigue en la CLI). Siempre se usa la **última observación** de cada acta.
- `coverage` (en eventos y goleadores) cuenta solo partidos **finalizados** del ámbito: `finished_matches`, `with_report`,
  `reports_not_closed`, `without_report` (hasta 10 ids), `complete` y una `note`. Si `complete` es false el resultado puede
  ser parcial y la ausencia de un evento no prueba nada. Un ámbito sin partidos finalizados lo dice explícitamente.
- Distinción: partido finalizado sin acta importada ("no implica que no exista en la RFFM") vs. partido no jugado (estado).
- `consistency` (en `/report`): `competition_score`, `report_score`, `events_score` (goles + propia meta al rival) y
  `differences` legibles; incluye el marcador previo del listado cuando el acta cerrada lo sustituyó (política de la Fase 5).
- `own_goal`: `team` = equipo del jugador; suma al rival. `against_team_id` cuenta solo goles (`goal`) de rivales.
- Tarjetas: se devuelve `source_code`; su significado (amarilla/roja) **no está verificado** y no se traduce.
- Árbitros: `officials` con nombre y rol tal cual figuran en el acta (la exclusión de la Fase 1 afectaba solo al listado de
  jornadas, que no los trae; el acta sí los trae y ya estaban almacenados en `match_officials`).
- Errores: 404 grupo/equipo/jornada/partido/jugador inexistente; 400 parámetros inválidos (`limit`, `event_type`, `sections`, `name`).
