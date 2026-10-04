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
Activos hoy: httpx, pydantic (+ Postgres en Compose, sin uso aún).

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

## Fase completada
Fase 1: corte vertical RFFM jornada 2 (grupo 2, 1ª Autonómica Aficionado FS 2026-27):
descarga → snapshot → parser → modelo normalizado → JSON. 7 partidos, todos finalizados.
Campos encontrados: ids de partido/equipos/campo, nombres, marcadores, fecha, hora, campo, estado.

## Comandos esenciales
- `pip install -e '.[dev]'`
- `python3 -m futsal.cli inspect-rffm --round 2`
- `python3 -m futsal.cli scrape-round --round 2 --output data/round-2.json` (`--refresh` redescarga)
- `python3 -m pytest -q`, `ruff check .`, `python3 -m mypy`
- `docker compose config -q`

## Limitaciones conocidas
- La página no da URL de acta ni de comparador: `match_report_url`/`comparison_url` = null.
- Solo se observó el estado `1`; códigos de aplazado/suspendido/anulado sin verificar.
- Sin persistencia, API, MCP, KB ni agente. Una sola jornada por ejecución.
- La CLI real es `python -m futsal.cli` (el paquete es `futsal`, no `src`).

## Próxima fase (recomendada)
Fase 2: abrir una acta (`appweb.rffm.es`, dominio aún no autorizado) para obtener URL de acta
y detalle, o recorrer todas las jornadas con snapshots y pausa entre peticiones.
