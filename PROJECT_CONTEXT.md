# PROJECT_CONTEXT

## Objetivo general
Asistente agéntico sobre competiciones deportivas (inicio: futsal, fuente RFFM).
Proyecto personal con vocación de evolucionar a producto.

## Arquitectura acordada
Monolito modular. Flujo: ingesta determinista → PostgreSQL → API REST (FastAPI)
→ MCP de solo lectura sobre la API. Aparte: knowledge base (pgvector), motor
determinista de reglas y agente orquestador. Detalle: `docs/architecture.md`.

## Tecnologías
Python 3.11+ (entorno actual 3.11), FastAPI, PostgreSQL + pgvector, SQLAlchemy 2,
Alembic, Pydantic, httpx, pytest, ruff, mypy, Docker Compose.
Activos hoy: httpx, pydantic, SQLAlchemy 2 (síncrono), Alembic, psycopg 3, PostgreSQL 16.4 (Compose).

## Estructura del repositorio
- `src/futsal/{domain,ingestion,repositories,api,mcp_server,knowledge,rules,agent,config}`
- `src/futsal/ingestion/rffm/`: `client.py` (HTTP, solo RFFM), `parser.py` (puro),
  `models.py` (pydantic), `export.py` (JSON)
- `ingestion/rffm/report_*.py` (modelos, parser, comparación, TXT), `repositories/match_reports.py`,
  `importer/report_service.py`; CLI en `cli.py` + `report_commands.py`
- `tests/ingestion/`, `tests/fixtures/rffm_round_2.html`
- `docs/architecture.md`, `docs/discovery.md` (método RFFM)
- `data/` (ignorado por Git): snapshots en `data/raw/rffm/`, salidas JSON

## Decisiones tomadas
- Un solo repo y proceso; sin microservicios, Kubernetes ni Kafka.
- `mcp_server` y `agent` acceden a datos solo vía API; cálculos en `rules`.
- RFFM: los datos vienen en el HTML inicial (Next.js `__NEXT_DATA__`); httpx + json,
  sin Playwright ni bs4.
- Snapshot local reutilizable: no se vuelve a descargar salvo `--refresh`.
- `external_match_id` = `codacta`; `external_round_id` = número de jornada.
- Estado: `finished` si hay marcador y acta cerrada; `scheduled` si no hay marcador;
  `postponed/suspended/cancelled` por texto de `motivo_estado`; si no, `unknown`.
  El original se guarda en `status_raw`.
- El listado de jornadas no trae árbitro. El acta sí (`match_officials`, nombre y rol) y se expone tal cual figura en el acta (Fase 9).

## Objetivo vigente (confirmado)
temporada=22, competicion=26738243, grupo=26738245, tipojuego=3, jornada inicial=2.
Definido en `src/futsal/config/targets.py` (valores por defecto de la CLI).
Referencia histórica: ninguna distinta registrada en el repo; el "26" inicial era
un error por la jornada (la correcta es 2).

## Fases completadas
Fase 1: corte vertical RFFM jornada 2 (7 partidos finalizados).
Fase 2: descarga de las 26 jornadas (`src/futsal/ingestion/rffm/league.py`, `PoliteFetcher` en
`client.py`): 182 partidos (13 finalizados, 169 programados), 0 duplicados, 0 incidencias,
26 peticiones reales; 2ª ejecución 100% caché. Jornadas en `pageProps.rounds.jornadas` (ID técnico ≠ etiqueta).
Salidas en `data/rffm/` (ignorado); resumen en `examples/league-summary.example.json`.
Fase 3: PostgreSQL (`docker-compose.yml`, `.env.example`, Alembic `alembic/`, migración 0001), modelo en
`src/futsal/db/models.py`, importador en `src/futsal/importer/` (schema→transform→service) y
`repositories/league.py`. CLI: `preview-import`, `import-league [--dry-run] [--fail-on-quality-issues]`,
`db-summary`. Importación completa verificada: 1/1/1 temporada/competición/grupo, 26 jornadas, 14 equipos,
182 partidos, 182 observaciones; reimportación 0 cambios. Detalle: `docs/data-model.md`.
Tests de integración: BD temporal `futsal_test_*` (se omiten sin PostgreSQL; el rol necesita CREATEDB).

Fase 4: acta piloto `5575697` (`www.rffm.es/acta-partido/<codacta>?temporada&competicion&grupo`, `__NEXT_DATA__`→`pageProps.game`).
Migración 0002 (8 tablas, observaciones por hash): 79 filas; reimportación 0 nuevas.
Fase 5: `process-match-reports` (`report_batch.py`): 12 actas más (12 peticiones, 0 fallos) → 13 actas, 203 jugadores,
172 eventos; reejecución 0 candidatos. Migración 0003: `own_goal` (`tipo_gol`=102, confirmado). Política: acta cerrada
prevalece en el marcador (5575707: 7-3→7-4, anterior en `match_observations`); `import-league` no lo revierte. Docs: `docs/match-report-discovery.md`,
`docs/data-model.md`; ejemplos `examples/match-report*`.
Fase 5 (API): FastAPI de solo lectura en `src/futsal/api/app.py` (`/health`, `/competitions`, `/competitions/{id}/groups`,
`/groups/{id}/{rounds,teams,matches}`, `/matches/{id}`); tests `tests/integration/test_api_pg.py`. Detalle: `docs/api.md`.
Fase 6 (MCP): `src/futsal/mcp_server/server.py` (SDK `mcp`/FastMCP, stdio, cliente httpx sobre la API; `API_BASE_URL`).
Tools: `list_competitions|groups|rounds|teams|matches`, `get_match`; errores controlados. Tests `tests/test_mcp_server.py`. Detalle: `docs/mcp.md`.
Fase 7 (agente): `src/futsal/agent/` (`llm.py` Protocol+adaptador Anthropic/httpx, `mcp_client.py`, `agent.py` bucle con
`MAX_TOOL_CALLS`=5 y traza, `python -m futsal.agent "pregunta"`). Tests `tests/test_agent.py` (LLM simulado). Detalle: `docs/agent.md`.
Gemini: `GeminiLLM` (REST httpx) + `create_llm()` según `LLM_PROVIDER`; `GEMINI_API_KEY`/`GEMINI_MODEL` solo por entorno/.env; tests con HTTP simulado.
Fase 8: evaluación (`agent/evaluate.py`, 15 casos, verdad de PostgreSQL solo lectura), traza JSONL (`agent/trace.py`), tokens por llamada
(`Usage`), `team_id` en `list_matches`, `date` con fallback a `scheduled_date`. Detalle y medidas en `docs/agent.md`.
Fase 9 (actas por API/MCP/agente): `api/reports.py` + `repositories/report_queries.py` (solo lectura, última observación del acta).
Endpoints `/matches/{id}/report`, `/groups/{id}/{match-events,scorers,players}`, `has_report` en partidos; MCP `get_match_report`,
`list_match_events`, `top_scorers`, `find_players`. `coverage` explícito (finalizados con/sin acta), `consistency` marcador vs acta.
Sin migraciones. Eval: 9 casos de actas; retirados `goleadores_/arbitro_no_disponible` (premisa obsoleta). Detalle: `docs/api.md`, `docs/agent.md`.
(Nota: fases de actas anteriores se numeraron 4/5 en esta lista; la API es la siguiente fase.)
## Comandos esenciales
- `pip install -e '.[dev]'`
- `python3 -m futsal.cli scrape-round --round 2 --output data/round-2.json` (`--refresh` redescarga)
- `python3 -m futsal.cli list-rounds` / `scrape-league [--resume|--refresh|--max-rounds N]`
- `python3 -m pytest -q`, `ruff check .`, `python3 -m mypy`
- `uvicorn futsal.api.app:app_factory --factory --port 8000`
- `python -m futsal.mcp_server.server` (MCP stdio); `python -m futsal.agent "pregunta"` (requiere `ANTHROPIC_API_KEY`)
- `docker compose config -q`; `docker compose up -d --wait db && alembic upgrade head`
- `python3 -m futsal.cli preview-import|import-league|db-summary` y `*-match-report` (ver README)

## Limitaciones conocidas
- El listado no da URL de acta (`match_report_url` = null); el patrón se descubrió en Fase 4.
- Estado `postponed/suspended/cancelled` sin observar aún; la jornada 1 tiene un partido con `estado=0`.
- Sin KB ni clasificaciones; agente solo experimental; sin fichas de jugadores. Significado de tarjetas 100/101 sin verificar.
- Sustituciones/penaltis/otros técnicos/periodos: no figuran en el acta observada (no se importan ni se exponen; `not_in_source`).
- Cobertura de actas medida solo sobre snapshots locales (13/13 finalizados); no se ha comprobado la BD real del usuario ni Gemini en vivo.
- La CLI real es `python -m futsal.cli` (el paquete es `futsal`, no `src`).

## Próxima fase (recomendada)
Clasificaciones (derivables de `matches`) y procesamiento incremental de actas al jugarse más partidos; verificar el significado de las tarjetas 100/101.

Fase 10 (fiabilidad): reintentos acotados en el cliente LLM (`LLM_MAX_RETRIES`=2, 429/5xx/red), traza con `token_source`
(proveedor/estimado/no informado), caché y reintentos; evaluación en capas (datos, herramientas, respuesta, invención,
advertencia) con 29 casos y criterios anti-invención (`agent/criteria.py`); `agent/preflight.py` (misma base API=evaluación,
bloquea el modo online si no se demuestra); `evaluate --plan/--suite smoke/--report/--compare`. Offline completado; **validación
online con Gemini PENDIENTE** (la ejecuta el usuario: `docs/evaluation-online.md`). Detalle: `docs/agent.md`.

Fase 10.1 (tras la primera evaluación online, smoke 5/5 y suite `all` con 13 casos cortados por 429): un 429 abortado es `not_run`
(infraestructura, ninguna capa se juzga, el consumo sí se cuenta); capas sin respuesta = `—`; `--min-interval` (4 s por defecto en
live, `PacedLLM`) separa las peticiones; herramientas equivalentes de solo lectura permitidas (`EXTRA_ALLOWED`); el aviso de alcance
ya no vale como limitación y el agente conserva una limitación declarada sin tools (`limitacion_sin_tools`); el prompt prohíbe
mostrar IDs internos. Pendiente: repetir online los casos `not_run` y revisar `acta_partido_sin_acta` con su traza.

Fase 10.2 (segunda evaluación online `all`: 22 pass, 5 fail, 0 reintentos): nuevos criterios de invención verificables en
`agent/criteria.py` (`unsupported_scores`: todo marcador de la respuesta debe figurar en la evidencia; `leaks_prompt`: no copiar
las instrucciones del sistema); evidencia equivalente vía `list_matches` para casos sin acta y limitación sin tools estructural;
el agente pide UNA reconsulta de tools si responde con el aviso de alcance a una pregunta con vocabulario de la competición
(`RECONSIDER_MESSAGE`); el prompt pide copiar marcadores tal cual y no citar instrucciones. Offline completado (246 tests);
online pendiente de repetir con estos cambios.

Fase 10.3 (tercera evaluación online: 25 pass, 2 fail, 0 reintentos): el prompt ya no contiene la frase «Antes de decir que … no
existe» (el modelo la repetía en sus respuestas), pide «no figura en el grupo» y «mis herramientas…»; `find_players` permitido en
`equipo_inexistente`. Pendiente: repetir online `equipo_inexistente` y `acta_jugador_inexistente` y medir estabilidad con
`--compare` entre ejecuciones completas.

Fase 11 (optimización de tokens; ver `docs/phase11-report.md`): línea base real `aeb29e4` = 172.078 tokens de entrada, 58 llamadas;
83 % es prefijo fijo repetido. Prompt/esquemas/contexto más cortos (`slim_tools`), resultados sin nulos ni `round_id`,
`list_matches(limit)` con `count` total y `shown`, pausa de ritmo fuera de la latencia y `prefix_chars` en la traza.
Offline (estimado): −16,4 % de entrada, sin regresiones. Objetivo realista ≤145k; el 70-90k no es viable sin perder calidad.
Validación online PENDIENTE (preflight → smoke con `--max-requests` → suite completa).

Fase 11.4 (primera medición online de la Fase 11, `b1d62d3`): entrada real 145.622 (−15,4 %), 54 llamadas, pero 4 regresiones; ahorro
limpio ≈ −10,7 %. Corregido: cabecera de contexto condicional, reconsulta ante respuestas sin tools a preguntas de la competición,
«(acta disponible)» restaurado, primera persona en las limitaciones y criterio léxico de `no_report`. Pendiente repetir online.
