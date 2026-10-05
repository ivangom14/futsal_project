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
- `src/futsal/cli.py`: CLI
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
- Se excluye el nombre del árbitro (dato personal innecesario).

## Objetivo vigente (confirmado)
temporada=22, competicion=26738243, grupo=26738245, tipojuego=3, jornada inicial=2.
Definido en `src/futsal/config/targets.py` (valores por defecto de la CLI).
Referencia histórica: ninguna distinta registrada en el repo; el "26" inicial era
un error por la jornada (la correcta es 2).

## Fases completadas
Fase 1: corte vertical RFFM jornada 2 (7 partidos finalizados).
Fase 2: descarga de las 26 jornadas (`src/futsal/ingestion/rffm/league.py`, `PoliteFetcher` en
`client.py`): 182 partidos (13 finalizados, 169 programados), 0 duplicados, 0 incidencias,
26 peticiones reales; segunda ejecución 100% caché. Jornadas descubiertas en
`pageProps.rounds.jornadas`; ID técnico (`codjornada`) separado de la etiqueta visible.
Salidas en `data/rffm/` (ignorado); resumen versionado en `examples/league-summary.example.json`.

Fase 3: PostgreSQL (`docker-compose.yml`, `.env.example`, Alembic `alembic/`, migración 0001), modelo en
`src/futsal/db/models.py`, importador en `src/futsal/importer/` (schema→transform→service) y
`repositories/league.py`. CLI: `preview-import`, `import-league [--dry-run] [--fail-on-quality-issues]`,
`db-summary`. Importación completa verificada: 1/1/1 temporada/competición/grupo, 26 jornadas, 14 equipos,
182 partidos, 182 observaciones; reimportación 0 cambios. Detalle: `docs/data-model.md`.
Ejemplos: `examples/import-preview.txt`, `examples/db-summary.example.json`.
Tests de integración usan una BD temporal `futsal_test_*` en el PostgreSQL de Compose (se omiten sin él).

## Comandos esenciales
- `pip install -e '.[dev]'`
- `python3 -m futsal.cli inspect-rffm --round 2`
- `python3 -m futsal.cli scrape-round --round 2 --output data/round-2.json` (`--refresh` redescarga)
- `python3 -m futsal.cli list-rounds` / `scrape-league [--resume|--refresh|--max-rounds N]`
- `python3 -m pytest -q`, `ruff check .`, `python3 -m mypy`
- `docker compose config -q`; `docker compose up -d --wait db && alembic upgrade head`
- `python3 -m futsal.cli preview-import|import-league|db-summary` (ver README)

## Limitaciones conocidas
- La página no da URL de acta ni de comparador: `match_report_url`/`comparison_url` = null.
- Estado `postponed/suspended/cancelled` sin observar aún; la jornada 1 tiene un partido con `estado=0`.
- Sin API, MCP, KB, agente, actas, jugadores ni clasificaciones. Ningún enlace de acta visitado.
- La CLI real es `python -m futsal.cli` (el paquete es `futsal`, no `src`).

## Próxima fase (recomendada)
Acceso controlado a actas (`appweb.rffm.es`, dominio aún no autorizado) o API REST de solo lectura.
