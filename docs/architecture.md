# Arquitectura

Monolito modular en Python: un único repositorio y un único proceso desplegable,
con módulos de dependencias unidireccionales. Sin microservicios ni colas.

## Módulos (`src/futsal/`)

| Módulo | Responsabilidad |
|---|---|
| `domain` | Entidades y reglas puras, sin dependencias de infraestructura |
| `ingestion` | Adaptadores por fuente (RFFM, …) que producen entidades de dominio |
| `repositories` | Persistencia en PostgreSQL (SQLAlchemy 2, Alembic) |
| `api` | API REST (FastAPI); única puerta de acceso a los datos |
| `mcp_server` | Herramientas MCP de solo lectura, cliente de la API |
| `knowledge` | Procesado y búsqueda de documentos (pgvector) |
| `rules` | Clasificaciones y desempates deterministas |
| `agent` | Orquestación: decide qué herramientas invocar |
| `config` | Configuración (variables de entorno) |

Dependencias permitidas: `domain` no depende de nadie; `rules` solo de `domain`;
`ingestion` y `repositories` dependen de `domain`; `api` de `repositories` y `rules`;
`mcp_server` y `agent` solo hablan con la API/herramientas, nunca con la BD.

**Camino obligatorio de los datos del chat: agente → MCP → API → BD**, para todas las herramientas. Un único servidor MCP
(`mcp_server/server.py`) publica las herramientas v1 (por grupo) y las v2 (sin grupo, `mcp_server/v2_tools.py`, sobre la API
`/v2`), y el agente ofrece todas al LLM. Lo vigila
`tests/test_agent_v2.py::test_agent_and_mcp_never_touch_the_database` (`agent/evaluate.py` y `agent/preflight.py` son herramientas de
evaluación que leen la verdad de la BD; no forman parte del camino del chat). El registro v2 «en el mismo proceso» que consultaba la
BD directamente se retiró el 2026-11-14 por incumplir esta regla.

## Flujo de ingesta

Fuente oficial → adaptador (`ingestion`) → normalización a entidades de `domain`
→ `repositories` → PostgreSQL. La ingesta es determinista e idempotente: misma
entrada, mismo resultado; reejecutarla no duplica datos. Descargas en bruto se
guardan como snapshots para reutilizarlas y auditar el parseo.

## Datos transaccionales vs. conocimiento documental

- **Transaccional** (equipos, partidos, resultados, sanciones): cambia a menudo,
  es estructurado y se consulta por API.
- **Documental** (reglamentos, bases, circulares): texto largo y de cambio lento,
  se trocea, se indexa con embeddings en pgvector y se busca semánticamente.

Ambos viven en PostgreSQL pero con esquemas y ciclos de vida separados.

## API como capa de acceso

FastAPI expone lectura de datos y resultados del motor de reglas. Es el contrato
estable: validación con Pydantic, versionado de rutas y único punto de control
de acceso. Ningún otro componente lee las tablas directamente.

## MCP sobre la API

El servidor MCP traduce endpoints de lectura a herramientas para el agente. Es de
solo lectura y no contiene lógica de negocio: reutiliza la API.

## Knowledge base

Herramienta independiente (ingesta documental + búsqueda). Devuelve fragmentos con
cita (documento, versión, página/artículo) para que el agente pueda fundamentar
respuestas normativas.

## Motor de reglas

`rules` calcula clasificaciones y desempates con funciones puras y deterministas,
parametrizadas por las bases de cada competición. Nunca se delega al LLM un
cálculo que pueda hacerse de forma determinista.

## Agente orquestador

Recibe la pregunta y elige entre: datos (MCP), normativa (knowledge), cálculo
(rules) o una combinación. Cada respuesta debe indicar de qué fuente procede cada
afirmación y, ante falta de datos, decirlo en lugar de inventar.

## Procedencia, versionado y frescura

- **Procedencia**: cada registro guarda fuente, URL y referencia al snapshot.
- **Versionado**: los documentos conservan versiones (fecha de vigencia); los
  cambios en datos transaccionales se registran sin perder el valor anterior
  cuando afecte a resultados o clasificaciones.
- **Frescura**: cada fuente registra `fetched_at`; la API y el agente exponen la
  antigüedad del dato para que pueda advertirse al usuario si está desactualizado.

## Inventario actual (2026-11-14)

Foto del sistema tal como está en el código. Actualízala con cada cambio de herramientas, endpoints, tablas o interruptores.

### Capas y procesos

```
Frontal (React) → servidor del chat → agente → MCP → API → PostgreSQL ← ingesta (cron)
```

| Capa | Dónde | Qué hace |
|---|---|---|
| Frontal | `frontend/` | Interfaz del chat |
| Servidor del chat | `agent/web.py` (FastAPI) | Recibe preguntas y ejecuta el agente; escudos, valoraciones y borrado de conversaciones |
| Agente | `agent/agent.py` | Decide qué herramientas usar y redacta la respuesta con el LLM |
| MCP | `mcp_server/server.py` + `mcp_server/v2_tools.py` | Traduce la API a herramientas; el chat lo arranca como subproceso (stdio) |
| API | `api/` (uvicorn) | Única puerta a los datos, solo lectura |
| PostgreSQL | Docker Compose | Almacenamiento |
| Ingesta | `ingestion/` + `scripts/update_rffm.sh` | Descarga de la RFFM y guardado en la BD |

### Agente: una única arquitectura

Desde el 2026-11-17 no hay modos ni interruptores de arquitectura: se retiraron el agente legacy, el contexto `legacy`/`llm` (intérprete
LLM), el modo de prompt compacto, el prefetch, el Tier 0, las herramientas dinámicas, la reparación de `group_id` y el router en sombra.
Cada pregunta sigue siempre el mismo camino:

1. **Comprensión determinista** (`agent/context.py`, sin LLM): resolutor de competición, grupo y equipo sobre el catálogo, estado de la
   conversación (`agent/convstate.py`) y política de aclaración. Decide si actuar, asumir (y decirlo), preguntar con opciones reales o
   explicar que el dato no existe.
2. **Seguimientos de listados sin LLM** (`agent/v2/paging.py`): «los siguientes», «solo los juveniles», «¿y el domingo?» repiten la
   consulta guardada con otro `offset` o filtro (una llamada MCP).
3. **LLM con las 22 herramientas** (v1 + v2) y reglas deterministas propias (`agent/v2/rules.py`): fechas, alcance amplio, premisas
   falsas, «no consta en el acta» y respuesta de respaldo sin LLM ante aclaraciones insistentes.
4. **Guardarraíles y recuperación**: una respuesta con datos debe apoyarse en herramientas; si el modelo no consulta, la aplicación le
   entrega la interpretación verificada o ejecuta ella misma las consultas.

Única variable de comportamiento que queda: `AGENT_TELEMETRY` (off/on, registro de uso). `AGENT_TIME_BANDS` ajusta las franjas
horarias (no es un modo). Las mejoras se hacen sobre este camino y se miden con el benchmark (`docs/bench.md`).

### Herramientas del MCP (un único servidor)

**v1 (por grupo)**

| Herramienta | Endpoint |
|---|---|
| `list_competitions` | `GET /competitions` |
| `list_groups` | `GET /competitions/{id}/groups` |
| `find_teams` | `GET /teams` |
| `list_rounds` | `GET /groups/{id}/rounds` |
| `list_teams` | `GET /groups/{id}/teams` |
| `list_matches` | `GET /groups/{id}/matches` |
| `get_match` | `GET /matches/{id}` |
| `get_match_report` | `GET /matches/{id}/report` |
| `list_match_events` | `GET /groups/{id}/match-events` |
| `top_scorers` (goles según actas) | `GET /groups/{id}/scorers` |
| `get_standings` (clasificación oficial) | `GET /groups/{id}/standings` |
| `get_scorer_table` (goleadores oficiales) | `GET /groups/{id}/scorer-table`, `GET /competitions/{id}/scorer-table` |
| `get_data_freshness` | `GET /groups/{id}/freshness`, `GET /competitions/{id}/freshness` |
| `find_players` | `GET /groups/{id}/players` |
| `search_rules` (reglamento) | `GET /knowledge/search` |

**v2 (sin grupo, en todas las competiciones)**

| Herramienta | Endpoint |
|---|---|
| `find_player` | `GET /v2/players` |
| `find_official` | `GET /v2/officials` |
| `officials_ranking` | `GET /v2/officials/ranking` |
| `matches_by_date` | `GET /v2/matches` |
| `club_overview` | `GET /v2/clubs/search` o `GET /v2/clubs/results` |
| `scorers_ranking` | `GET /v2/rankings/scorers` |
| `teams_ranking` | `GET /v2/rankings/teams` |

### Endpoints

- **API de datos**: los de las tablas anteriores, más `GET /health`, `GET /crests` (escudos) y `GET /tables/coverage` (cobertura de
  tablas oficiales).
- **Servidor del chat** (no es la API de datos): `POST /chat`, `GET /chat/health`, `POST /chat/feedback`,
  `DELETE /chat/conversations/{id}`, `GET /chat/crests`, `GET /chat/crests/img/{file}`.

### Tablas de la base de datos (34 tablas y 1 vista)

| Bloque | Tablas |
|---|---|
| Competición y calendario | `seasons`, `competitions`, `competition_groups`, `rounds`, `teams`, `matches`, `match_observations` (historial de cambios de cada partido) |
| Actas | `match_reports`, `match_report_observations` (versiones), `match_events`, `match_officials`, `match_players`, `match_staff`, `players`, `player_team_memberships` |
| Tablas oficiales | `standings_snapshots`, `standings_rows`, `scorer_snapshots`, `scorer_rows`, `table_ingest_attempts` |
| Catálogo e identidad | `clubs`, `team_entities`, `competition_links`, `catalog_overrides`; vista `team_lineage` |
| Ingesta y control | `ingest_jobs` (cola), `ingest_controls` (pausa), `raw_fetches` (registro de descargas), `ingestion_runs`, `dataset_refreshes` (frescura), `data_quality_issues` |
| Reglamento | `kb_documents`, `kb_document_versions`, `kb_chunks`, `kb_ingestion_runs` |
