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
| `GET /groups/{id}/matches` | filtros `round_id`, `team_id` (partidos del equipo, local o visitante; 404 si no es del grupo), `status` (`scheduled`, `finished`, `postponed`, `suspended`, `cancelled`, `unknown`), `order=asc|desc|date_asc` (def. `asc` por jornada; `desc` = fecha publicada más reciente primero, p. ej. `status=finished&order=desc&limit=1` = último partido; `date_asc` = fecha publicada más próxima primero, sin fecha al final, p. ej. `status=scheduled&order=date_asc&limit=1` = próximo partido aunque haya partidos reprogramados dentro de una jornada anterior). Un partido `scheduled` con fecha ya pasada lleva `past_due: true` (datos posiblemente desactualizados; su estado no se modifica) |
| `GET /groups/{id}/matches` (`has_report`) | cada partido trae `has_report`; filtro `has_report=true|false` (p. ej. `status=finished&has_report=false` = finalizados sin acta descargada) |
| `GET /matches/{id}/report` | acta de UN partido; `sections` = `officials,goals` (def.), `cards`, `players`, `staff`. Sin acta: 200 con `report_available=false` y `reason`; partido inexistente: 404. Incluye `consistency` y `not_in_source` |
| `GET /groups/{id}/match-events` | eventos de las actas (def. `goal`,`own_goal`; `event_type=card`). Filtros `team_id`, `against_team_id`, `round_id`, `match_id`, `player_id`, `limit` (1-200, def. 40). `total`/`truncated` y `coverage` |
| `GET /groups/{id}/standings`, `/groups/{id}/scorer-table`, `/competitions/{id}/scorer-table`, `/tables/coverage` | clasificación y goleadores **oficiales** de la RFFM, con estados explícitos (`ok`, `incomplete`, `empty`, `not_ingested`, `failed`, `not_applicable`, `round_not_ingested`); detalle en `docs/phase16-tables.md` |
| `GET /groups/{id}/freshness`, `GET /competitions/{id}/freshness` | **Fase 21**, solo lectura: última actualización VÁLIDA, último intento y estado (`fresh`/`stale`/`never`/`partial`/`empty`/`not_applicable`) de partidos, actas, clasificación y goleadores, con `warnings`; no lanza ninguna actualización. Clasificación y goleadores sirven la última captura `ok` (`capture` indica la última realizada). Detalle: `docs/phase21-data-refresh-consistency.md` |
| `GET /groups/{id}/scorers` | goles agrupados por jugador en SQL (`min_goals`, mismos filtros, `limit` def. 20); `own_goals` aparte; `coverage` |
| `GET /knowledge/search` | fragmentos de los documentos indexados (reglamento FIFA, Circular 27 de la RFEF; `q` 2-500, `limit` 1-10, `rule`, `doc_key`, `include_history`); sin umbral de relevancia; cada fragmento lleva `type` (`reglamento`/`circular`); la pregunta se amplía con 4 equivalencias explícitas (`knowledge/vocabulary.py`) y una `circular N` citada prioriza sus fragmentos y se informa en `cited_circulars` (`indexed`: false = no está en la base); 503 si no hay base de conocimiento. Detalle: `docs/phase20-rag-retrieval.md` |
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
- Tarjetas (Fase 25.4): se devuelven `source_code` (original) y `card_kind` = `yellow` (código 100), `second_yellow` (100 con `segunda_amarilla=1`: segunda amarilla, supone expulsión), `red` (101, roja directa) o `unknown` (cualquier otro código: sin color, no se interpreta). Significado **confirmado por el responsable del proyecto** (la RFFM no etiqueta los códigos) y contrastado con 13 actas reales (75 tarjetas: 64 amarillas, 7 segundas amarillas —las 7 con una amarilla previa del mismo jugador— y 4 rojas). El acta incluye `cards_summary` por equipo.
- Árbitros: `officials` con nombre y rol tal cual figuran en el acta (la exclusión de la Fase 1 afectaba solo al listado de
  jornadas, que no los trae; el acta sí los trae y ya estaban almacenados en `match_officials`).
- Errores: 404 grupo/equipo/jornada/partido/jugador inexistente; 400 parámetros inválidos (`limit`, `event_type`, `sections`, `name`).


## API v2 — consultas sin grupo (T09, primera entrega) — prefijo `/v2`
Código: `src/futsal/api/v2/` (`routes.py`, `names.py`) y `src/futsal/repositories/v2_queries.py`. Solo lectura; la v1 no cambia.
Contrato común de **todas** las respuestas: `items`, `count` (total de coincidencias), `shown` (filas devueltas), `notes` (avisos: lista de
textos) y `freshness` (máxima fecha de captura válida implicada, ISO UTC; `null` si no consta), más `season` (`ref` y `label`).
Referencias opacas con IDs internos, nunca de la RFFM: `team:`, `group:`, `comp:`, `match:`, `player:`, `club:` (solo con código oficial), `season:`.
Filtros comunes (opcionales): `season` (`season:<id>`, `<id>` o etiqueta; por defecto la marcada `is_current` o, si no hay, la que tiene más
partidos recientes, y se dice en `notes`), `category`, `gender` (`male|female|mixed`, también `masculino|femenino|mixto`), `competition`
(`comp:<id>` o palabras del nombre), `limit` (1-100, def. 20). Categoría y género salen de la clasificación del catálogo (0007) cuando la
competición está `auto|confirmed`; si no, se deducen del nombre y `notes` lo avisa. Errores: **422** (también tipo/rango) y nunca 500 por un
nombre raro: los nombres se reducen a palabras `[a-z0-9]` sin acentos; sin ninguna, 422.

| Ruta | Notas |
|---|---|
| `GET /v2/players?name=` | todos los grupos; palabras completas en cualquier orden y sin acentos («Pablo silvosa» = «SILVOSA PEREZ, PABLO»). Un item por jugador y equipo: `matches_in_reports`, `goals_in_reports` y `official` (`listed` con goles/partidos de la última tabla oficial `ok`, `not_listed` o `no_table`). Sin coincidencia exacta: `items: []`, `suggestions` (≤ 5, `similarity` de pg_trgm ≥ 0,3) y nota «no figura; nombres parecidos: …»; una sugerencia nunca se presenta como el jugador pedido |
| `GET /v2/officials?name=` | árbitros por `match_officials.display_name`: `roles` (conteo por rol), `matches`, `competitions`, `groups`; `include=matches` añade `matches_list` (fecha y hora de Europe/Madrid, local, visitante, resultado, competición, grupo; máx. 100). Mismas sugerencias que jugadores |
| `GET /v2/officials/ranking?role=ARBITRO` | árbitros con más partidos con ese rol en actas almacenadas (nota de cobertura de actas) |
| `GET /v2/matches` | `date_from`, `date_to` (AAAA-MM-DD), `time_from`, `time_to` (HH:MM), todo en Europe/Madrid (BD en UTC; respeta el cambio de hora), `venue`, `status`, `order=asc|desc`. Cada item: `date`, `time`, `local_datetime` (con desfase), competición, grupo, local, visitante, `score`, `status`, `has_report`. «Tarde»/«mañana» los interpreta el agente, no esta API |
| `GET /v2/clubs/search?name=` | equipos agrupados por nombre base normalizado (sin A/B/C, «club», «C.F.S.», «F.S.», puntuación) en todas las competiciones, con competición, grupo, categoría, género y `standing` oficial si lo hay. `club_link: "name_match"` + nota «agrupado por nombre; pendiente de confirmar con el código oficial de club»; `"official"` + `club_ref` solo si `team_entities.club_id` existe. Varios nombres base distintos → `ambiguous: true` en cada candidato |
| `GET /v2/clubs/results?name=` | partidos finalizados de todos los equipos de la búsqueda, por equipo (y su competición), con `summary` (played, won, drawn, lost, goals_for, goals_against); `results` máx. 50 por equipo, más recientes primero |
| `GET /v2/rankings/scorers?competition_kind=league` | agrega las últimas tablas oficiales `ok` de todos los grupos del filtro (`competition_kind=all` quita el tipo); orden por goles y, a igualdad, nombre. Excluye competiciones con categoría `unknown` y lo dice en `notes` junto con «compara grupos distintos con diferente número de jornadas jugadas» |
| `GET /v2/rankings/teams?metric=` | `goals_for`, `goals_against` (menos primero por defecto), `wins`, `points_per_game` (3-1-0) desde partidos finalizados; `order=asc|desc` y `min_played` opcionales; misma nota y exclusión |

Pruebas: `tests/test_api_v2_unit.py` (normalización, nombre base de club, validación) y `tests/integration/test_api_v2_pg.py` (PG desechable).
Sin migraciones ni índices nuevos: con 3.640 partidos, 2.400 actas y 58.000 participaciones sintéticas cada ruta respondió en < 110 ms (mediana).
