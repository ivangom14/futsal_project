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
| `GET /competitions` | filtro opcional `season_id` |
| `GET /competitions/{id}/groups` | 404 si no existe la competición |
| `GET /groups/{id}/rounds` | ordenadas por número |
| `GET /groups/{id}/teams` | ordenados por nombre |
| `GET /groups/{id}/matches` | filtros `round_id`, `status` (`scheduled`, `finished`, `postponed`, `suspended`, `cancelled`, `unknown`) |
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
- `date` = `scheduled_at` ISO 8601 (UTC); `home_score`/`away_score` nulos si no hay marcador.
- Detalle de partido incluye observaciones de listado, no datos de acta (Fase 4).

## Limitaciones
- Sin paginación ni autenticación; sin clasificaciones, jugadores ni eventos de acta.
- Filtro de jornada por `round_id` interno (no por número).
- `season` es null si la temporada no tiene nombre importado.
