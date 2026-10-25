# Fase 16 — Clasificaciones y goleadores oficiales de la RFFM

El agente consulta las **tablas que publica la RFFM**, no las reconstruye. Flujo: `www.rffm.es` → caché HTML + PostgreSQL (`standings_*`, `scorer_*`) → FastAPI → MCP (`get_standings`, `get_scorer_table`) → agente.
Resultados, actas y RAG no cambian: las tablas oficiales viven en tablas nuevas (migración 0005) y se pueden usar junto a ellos, nunca en su lugar.
Verificado en el entorno de Claude (Linux) contra la web real el 2026-10-24 y sobre una **copia** de la base réplica; nada se ha ejecutado en tu base local ni en tu Windows ni con Gemini.

## Cómo se extraen realmente las dos tablas
Ambas páginas son Next.js: los datos van en el HTML inicial, en `<script id="__NEXT_DATA__">` → `props.pageProps` (mismo mecanismo que resultados y actas; no hay API ni JSON aparte). Sin Playwright ni dependencias nuevas.
| | Clasificación | Goleadores |
|---|---|---|
| URL | `/competicion/clasificaciones?temporada=22&competicion=<c>&grupo=<g>[&jornada=N]&tipojuego=3` | `/competicion/goleadores?temporada=22&competicion=<c>&grupo=<g>&tipojuego=3` |
| Dato | `pageProps.standings` (`jornada`, `fecha_jornada`, `clasificacion[]`, `promociones[]`) + `rounds.currentRound` | `pageProps.scorers.goles[]` + `pageProps.group` |
| Campos por fila | `posicion`, `codequipo`, `nombre`, `jugados`, `ganados`, `empatados`, `perdidos`, `goles_a_favor`, `goles_en_contra`, `puntos`, `puntos_sancion`, parciales casa/fuera, racha, color de promoción | `codigo_jugador`, `jugador`, `codigo_equipo`, `nombre_equipo`, `partidos_jugados`, `goles`, `goles_penalti`, `goles_por_partidos` |
| Jornada | **Sí**: `jornada=N` devuelve la clasificación acumulada tras N (comprobado con 1, 2, 3 y 4) | **No**: solo la tabla actual |
| Identificadores | `codequipo` = `teams.external_id` (14/14 equipos coinciden en la base) | `codigo_jugador` = `players.external_id` (97/102 conocidos por las actas importadas); `codigo_equipo` igual que arriba |
| Paginación / límite | no | **ninguna observada**: 22 grupos con datos, hasta 137 filas, sin tope redondo; los goles de la tabla son siempre ≤ los goles a favor de los equipos (diferencia de 1 a 10 goles = propia meta y goles no atribuidos) en los 18 grupos de liga |
| Fecha de actualización | no hay marca de actualización: solo `fecha_jornada` (fecha de la jornada). La referencia de frescura es **nuestra captura** (`captured_at`) | ídem (solo `captured_at`) |

Comportamientos verificados que condicionan el diseño:
- **Fallo intermitente**: la web devuelve a veces `standings: null` o `scorers: null` / `group: null` con HTTP 200 (reproducido en la jornada 2 de un grupo, en Tercera Grupo 3 y en Copa Senior I previa; al reintentar sale bien). Un `null` **nunca** se trata como tabla vacía: se reintenta (`--null-retries`, 2 por defecto) y, si persiste, queda `failed`/`transient`. Una tabla vacía válida es `estado="1"` con la lista vacía (p. ej. Copa Senior I fase final, aún sin partidos).
- **Jornada inexistente o futura** (26, 99, 0): la web devuelve **la actual sin avisar**. Se compara `standings.jornada` con la pedida: si difiere, el intento se registra como `round_unavailable` y no se guarda nada con esa etiqueta.
- **Eliminatorias** (`tipo_competicion = 1`: copas y trofeo): no hay `clasificacion`, solo el cuadro de cruces (`eliminatoria`). Estado `not_applicable`; el cuadro no se ingiere (los partidos ya están en resultados).
- **Diferencia de goles**: no se publica. La API la calcula (GF − GC) y la declara en `derived`; no se guarda como dato oficial.
- **Posición de goleadores**: no se publica. Orden = goles descendentes y, a igualdad, orden arbitrario de la web. La API deriva `rank` (los empates comparten puesto) y `tied_with`.
- **Solo aparecen jugadores con ≥ 1 gol**: un jugador ausente no figura como goleador (no se afirma que tenga 0 ni que no juegue).
- **Nombres iguales**: hay un caso real de mismo nombre con códigos distintos; las filas se distinguen por `codigo_jugador` + `codigo_equipo`. **Cambios de equipo**: no se ha observado ningún jugador con dos equipos en la misma tabla (0 de 22 tablas); no se sabe cómo los trataría la RFFM, y si apareciera uno se conservan todas sus filas y se avisa.

## Modelo de datos (migración `0005`, solo tablas nuevas)
| tabla | contenido | unicidad |
|---|---|---|
| `standings_snapshots` | una captura de la clasificación de **una jornada** de un grupo: jornada devuelta, fecha de la jornada, jornada actual, estado `ok/empty/incomplete`, hash del contenido, nº de filas, equipos esperados, URL, `fetched_at`, `first_seen_at`, `last_seen_at`, promociones y avisos | `(group_id, round_number, content_hash)` |
| `standings_rows` | fila publicada: posición, equipo (código externo, nombre y `team_id` si existe), PJ/G/E/P, GF/GC, puntos, puntos de sanción, `details` (casa/fuera, racha, color) | `(snapshot_id, team_external_id)` |
| `scorer_snapshots` / `scorer_rows` | captura de la tabla de goleadores y sus filas: orden publicado, jugador (código, nombre, `player_id` si existe), equipo (código, nombre, `team_id`), partidos, goles, goles de penalti, goles/partido | `(group_id, content_hash)` / `(snapshot_id, row_order)` |
| `table_ingest_attempts` | **cada intento**: tipo, jornada pedida y devuelta, resultado (`ok/empty/incomplete/not_applicable/failed/round_unavailable`), código y texto del error, nº de peticiones, avisos, `change` (`new/unchanged`) | índice `(group_id, kind, attempted_at)` |
Reglas: repetir la ingesta con el mismo contenido solo actualiza `last_seen_at` (idempotente); un cambio crea otra captura y conserva la anterior (historial); la captura vigente es la de `last_seen_at` más reciente. Las capturas están ligadas al grupo, que pertenece a una competición y temporada: no se mezclan temporadas (test con dos temporadas con los mismos códigos de equipo). No se borra ni sobrescribe nada de resultados, actas, eventos, oficiales ni documentos RAG. `downgrade` borra solo estas 5 tablas.

## Ingesta
Requisitos: PostgreSQL con `alembic upgrade head` (0005) y el catálogo ya descargado e importado (`discover-catalog`, `import-catalog`: de ahí salen los 14 competiciones y 25 grupos con sus IDs reales).
- Código: `ingestion/rffm/tables.py` (parsers puros), `table_ingest.py` (descarga + caché + reintento de `null`), `importer/table_service.py` (persistencia, orquestación por grupo e informe), `table_commands.py` (CLI), `repositories/table_queries.py` + `api/tables.py` (lectura).
- **Caché**: `data/rffm/tables/s22_t3/c<comp>/g<grupo>/{standings_current,standings_j<N>,scorers}.html`. Solo se guarda una página que se ha entendido y corresponde a lo pedido. Una página reciente (`--max-age-hours`, 12) no se vuelve a pedir; las jornadas históricas no caducan; `--refresh` fuerza la descarga; `--offline` usa solo la caché.
- **Aislamiento**: cada grupo y cada tabla van por separado; un fallo no detiene el resto ni mezcla clasificación con goleadores. Pausa de 2 s entre peticiones (`PoliteFetcher`), reintentos 429/5xx/red y reintentos propios de `null`. El informe JSON se reescribe tras cada grupo.
- **Reanudación**: `--resume` omite (sin red ni intento nuevo) lo ya `ok/empty/not_applicable` dentro de `--max-age-hours`; los fallos se reintentan.
- **Estados por grupo** (informe y salida): `ok`, `empty` (tabla válida sin filas), `incomplete` (filas descartadas o menos equipos de los que declara el grupo), `not_applicable` (eliminatorias), `failed`, `round_unavailable`, `skipped` (resume), `no_group_in_db` (el grupo no está importado). Código de salida 0 solo si todos son `ok/empty/not_applicable/skipped`.

### Comandos (Windows CMD, desde la raíz del repositorio con `.venv\Scripts\activate.bat` activado)
```bat
python -m futsal.cli ingest-standings --plan
python -m futsal.cli ingest-standings --group 26738245
python -m futsal.cli ingest-standings
python -m futsal.cli ingest-scorers
python -m futsal.cli tables-report --json data\rffm\tables\tables-report.json
```
- Un grupo (prueba): `--group <ID real>`; una competición: `--competition <ID real>` (ambos repetibles); los N primeros: `--limit-groups N`. Sin filtros recorre las 14 competiciones y 25 grupos (≈ 25 peticiones por tabla, ~1,5 min con la pausa de 2 s).
- Una jornada concreta: `ingest-standings --round 3` (la web debe devolver esa jornada; si no, `round_unavailable`). Historial completo: `ingest-standings --all-rounds` (una petición por jornada hasta la actual y por grupo; úsalo con `--group`/`--competition`, y con `--resume`).
- Reanudar tras una interrupción o ejecutar solo lo pendiente: repetir el mismo comando con `--resume`. Forzar datos nuevos: `--refresh`. Probar sin tocar la base: `--dry-run`. Ver la previsión sin red: `--plan`.
- Cobertura y errores: `tables-report` (solo lectura; sale con 1 si algún grupo está `failed`, `incomplete`, `not_ingested` o `no_group_in_db`), el informe JSON de la última ingesta (`data\rffm\tables\s22_t3\tables-standings-report.json`, `tables-scorers-report.json`) y la tabla `table_ingest_attempts`.
- Actualización periódica: repetir `ingest-standings` y `ingest-scorers` (la caché de 12 h evita peticiones innecesarias).

## Cobertura comprobada (web real, 2026-10-24, unas 50 peticiones, copia de la base)
| | resultado |
|---|---|
| Competiciones / grupos del catálogo | 14 / 25 |
| **Clasificación** | 18 grupos `ok` (todos con el nº de equipos que declara el grupo), 6 `not_applicable` (eliminatorias), 1 `no_group_in_db` |
| **Goleadores** | 22 grupos `ok`, 2 `empty` (Copa Senior I y II fase final: sin partidos jugados), 1 `no_group_in_db` |
| Errores de descarga / estructura / incompletas | 0 / 0 / 0 (los `null` intermitentes se resolvieron con el reintento) |
| Vínculos | 292/292 filas de clasificación con `team_id`; 1.552/1.552 filas de goleadores con `team_id`; 121/1.552 con `player_id` (solo hay actas del grupo inicial) |
| Última jornada por competición | Tercera y Primera Autonómica Juvenil: 5 · Primera Autonómica Aficionado: 4 · Preferente (aficionado y juvenil): 3 · Primera Aficionado y Primera Juvenil: 2 (y 3 en Primera Juvenil G5) |
El grupo `28446703` (Copa Juvenil fase final) no se ha podido persistir: la web no publica jornadas y `import-catalog` no lo importa (`no_rounds`), así que no existe en `competition_groups`. La web le devuelve tabla de goleadores vacía; queda `no_group_in_db` y se documenta, no se da por cubierto.
Historial comprobado en un grupo (jornadas 1-4 con `--round` y `--all-rounds`, reutilizando la caché de las ya pedidas) y `--round 99` → `round_unavailable`. La pasada completa no se ha repetido sobre tu base local: **depende de que ejecutes los comandos**.

## API (solo lectura; `uvicorn futsal.api.app:app_factory --factory --port 8000`)
| ruta | parámetros | devuelve |
|---|---|---|
| `GET /groups/{id}/standings` | `round` (1-200, opcional) | clasificación oficial de esa jornada o de la última ingerida |
| `GET /groups/{id}/scorer-table` | `name` (2-80, sin acentos), `team_id`, `limit` (1-200, 10) | tabla oficial; con `name`, todos los registros del jugador |
| `GET /competitions/{id}/scorer-table` | `limit` (1-50, 1) | los máximos goleadores de **cada** grupo de la competición |
| `GET /tables/coverage` | `competition_id` | estado y última captura de ambas tablas por grupo |
`id` es el ID interno de la base (el de `/competitions`), no el de la RFFM. Todas incluyen `source` (jornada, fecha de la jornada, `captured_at`, URL), `coverage` (equipos/jugadores, esperados, `complete`, avisos), `derived` (campos calculados) y una `note` corta. Estados (`status`) que no son una tabla vacía engañosa:
`ok`, `incomplete` (disponible pero incompleta), `empty` (tabla válida sin registros), `not_ingested` (nunca ingerida), `failed` (el intento falló y no hay captura), `not_applicable` (eliminatorias), `round_not_ingested` (jornada pedida no capturada; incluye `available_rounds`). Grupo inexistente → 404; parámetros inválidos → 400. Si un intento posterior falla, se sirve la última captura buena con `warning` y `last_attempt`.
```json
GET /groups/1/standings   (extracto real; goal_difference es derivado)
{"status": "ok", "source": {"round": 4, "round_date": "2026-10-17", "current_round": 4, "captured_at": "2026-10-24T13:30:47+00:00", "rounds_available": [1, 2, 3, 4]},
 "coverage": {"teams": 14, "expected_teams": 14, "played_min": 3, "played_max": 4, "complete": true}, "derived": ["goal_difference"],
 "standings": [{"position": 1, "team": "C.D. DOSA", "played": 4, "won": 3, "drawn": 1, "lost": 0, "goals_for": 19, "goals_against": 11, "goal_difference": 8, "points": 10, "zone": "ASCENSO"},
               {"position": 2, "team": "AGRUPACION AUTO-ESCUELA SIMANCAS 'B'", "played": 3, "points": 9, "…": "…"}]}
GET /groups/1/scorer-table?limit=3   (nombres sustituidos)
{"status": "ok", "coverage": {"players_listed": 102, "goals_total": 187, "only_players_with_goals": true, "complete": true}, "matching": 102, "shown": 3,
 "scorers": [{"rank": 1, "tied_with": 3, "player": "JUGADOR A", "team": "PARQUE NORTE F.S. - RODILLITO B", "goals": 5, "matches_played": 2}, "… (empate a 5 goles entre 4 jugadores)"]}
GET /groups/1/standings?round=9  →  {"status": "round_not_ingested", "available_rounds": [1, 2, 3, 4], "message": "La clasificación de la jornada 9 no está ingerida: no la reconstruyas; di que no consta."}
```

## MCP y agente
- Tools nuevas (solo estas dos; sin una por competición): `get_standings(group_id, round?)` y `get_scorer_table(group_id | competition_id, name?, limit)`. `top_scorers` (actas) sigue igual y su descripción dice que no es la tabla oficial.
- El prompt dice que clasificación y goleadores del grupo salen de esas tools, que no se calculan con resultados ni actas, que se indica jornada o captura y que si `status` no es `ok/incomplete` no constan. El mensaje de alcance ahora menciona las clasificaciones.
- Presupuesto de llamadas sin cambios (`MAX_TOOL_CALLS` = 5). Coste: el prefijo fijo crece ≈ +230 caracteres de prompt y ≈ +900 de esquemas de tools (≈ +320 tokens por llamada al LLM, medido localmente con la estimación de 3,5 caracteres/token).
- Preguntas y ruta esperada (las cinco primeras las cubren casos del evaluador offline con LLM guionizado, **no con Gemini**; las dos últimas son la ruta prevista, sin caso): «¿cómo está la clasificación?» → `get_standings`; «tras la jornada 3» → `get_standings(round=3)`; «¿quiénes son los máximos goleadores?» → `get_scorer_table`; «¿cuántos goles lleva X?» → `get_scorer_table(name)`; «el máximo goleador de cada grupo» → `get_scorer_table(competition_id)`; «compara la clasificación con los últimos resultados» → `get_standings` + `list_matches`.
- Evaluador (`agent/evaluate.py`): 5 casos nuevos `tabla_*` (líder y puntos, jornada pasada, máximo goleador con empates completos, goles de un jugador, jugador que no figura). Sin tablas ingeridas quedan **no evaluables**, nunca aprobados. Reconstruir con resultados/actas cuenta como «herramienta no esperada».

## Pruebas (offline, sin red ni LLM)
```bat
python -m pytest tests\ingestion\test_rffm_tables_parser.py tests\ingestion\test_rffm_tables_ingest.py tests\ingestion\test_tables_cli.py tests\test_agent_table_cases.py -q
python -m pytest tests\integration\test_tables_pg.py -q
python -m pytest -q
```
Fixtures sintéticas con la estructura real en `tests\fixtures\rffm_tables\` (se regeneran con `python tests\fixtures\rffm_tables\build_fixtures.py`; no contienen datos personales). Cubren: clasificación completa, de otra jornada, vacía, `null`, eliminatorias, estructura cambiada, grupo distinto y fila inválida; goleadores con empates, nombres iguales, vacía, `null`, estructura cambiada y fila inválida; reintentos de `null` y de HTTP 503, caché y caducidad, jornada no disponible, reanudación, idempotencia, historial, separación de temporadas, grupos sin tabla, los 14×2 grupos de la configuración, API, MCP y agente con LLM guionizado. Las pruebas contra la web real **no están en la suite**: se hicieron a mano (sección de cobertura).

## Limitaciones y trabajo pendiente
- Sin fecha de actualización de la RFFM: la frescura es la de nuestra captura; la clasificación de la jornada actual puede estar a medias (equipos con distinto nº de partidos: `played_min/max`).
- Los goleadores no tienen historial por jornada ni posición oficial; el historial de clasificación existe solo si se ingiere (`--all-rounds`).
- Si la RFFM corrige una clasificación pasada, la siguiente ingesta crea otra captura; la API sirve la más reciente.
- Falta contrastar clasificación y goleadores con resultados y actas (discrepancias): los datos están en la base para hacerlo.
- `no_group_in_db` para grupos sin jornadas (Copa Juvenil fase final) hasta que existan en el catálogo importado.
- No se ha medido el comportamiento de Gemini con las tools nuevas (requiere tu autorización y cuota).

## Si algo falla (ejecución real del usuario, 2026-10-25)
En la primera ejecución sobre la base local: 16 grupos `ok`, 3 grupos de copa `failed` (`standings=null` tras 3 intentos), 5 `no_group_in_db` y 1 `not_applicable`. Cómo interpretarlo y qué hacer:
- **`failed` con «standings=null (fallo intermitente)»** en grupos de *Eliminatorias*: la web devolvió `null` en los tres intentos (con respuestas lentas, ~40 s por grupo). Los grupos de copa no tienen clasificación (serían `not_applicable`), así que no se pierde ningún dato. Repetir solo lo pendiente (los `ok` se omiten y los fallos se reintentan):
```bat
python -m futsal.cli ingest-standings --resume
```
  Si persiste, subir los reintentos: `--null-retries 4`.
- **`no_group_in_db`**: el grupo está en el catálogo pero no en PostgreSQL, y las tablas necesitan el grupo (no se crean grupos vacíos desde esta ingesta). Comprobar y completar con los comandos de la Fase 14, y repetir la ingesta de tablas:
```bat
python -m futsal.cli verify-catalog
python -m futsal.cli scrape-catalog --resume --competition <ID de la competición>
python -m futsal.cli import-catalog --competition <ID de la competición>
python -m futsal.cli ingest-standings --resume
```
  `import-catalog` solo importa grupos descargados (`downloaded` o `partial`) y el informe `data\rffm\catalog\s22_t3\catalog-import-report.json` dice por qué se omitió cada uno.
- **Línea de progreso repetida en CMD**: la línea era más ancha que la ventana y `\r` no la sobrescribía bien; ahora se recorta al ancho de la terminal (solo presentación).
- El código de salida es 1 mientras haya grupos `failed`, `incomplete` o `no_group_in_db`: es intencionado (una ingesta parcial no se marca como completa).
