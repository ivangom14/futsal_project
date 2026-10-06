# Fase 6 — MCP Server

## Objetivo
Exponer las consultas de la API como *tools* MCP de solo lectura para un agente o cliente MCP.
Capa fina: valida parámetros, llama a la API por HTTP y devuelve JSON. Sin SQL ni lógica de negocio.

## Arquitectura
```
Cliente MCP → list_matches → MCP Server → GET /groups/1/matches?status=finished → FastAPI → PostgreSQL
```
Código: `src/futsal/mcp_server/server.py`. No importa `db`/`repositories`; solo `httpx`.

## Tools
Los listados devuelven `{<clave>: [...], "count": N}`. IDs = IDs internos de BD.

| Tool | Parámetros | Endpoint |
|---|---|---|
| `list_competitions` | `season_id` (opc.) | `GET /competitions` |
| `list_groups` | `competition_id` | `GET /competitions/{id}/groups` |
| `list_rounds` | `group_id` | `GET /groups/{id}/rounds` |
| `list_teams` | `group_id` | `GET /groups/{id}/teams` |
| `list_matches` | `group_id`, `round_id` (opc., ID interno), `status` (opc.: scheduled, finished, postponed, suspended, cancelled, unknown) | `GET /groups/{id}/matches` |
| `get_match` | `match_id` | `GET /matches/{id}` (incluye `observations`) |

Ejemplo: `list_matches(group_id=1, status="finished")` →
`{"matches":[{"id":2,"round_number":1,"home_team":"PARQUE NORTE F.S. - RODILLITO 'B'","home_score":3,
"away_team":"C.D. LOPE DE VEGA 'B'","away_score":4,"status":"finished",...}],"count":13}`

## Errores
Error MCP (`isError`) con prefijo, sin trazas: `error_no_encontrado` (404), `error_validacion`
(400/422 de la API o tipos/`status` inválidos rechazados por el esquema), `error_dependencia`
(API caída o 5xx), `error_timeout` (10 s).

## Arranque
```
docker compose up -d --wait db && alembic upgrade head
uvicorn futsal.api.app:app_factory --factory --port 8000
python -m futsal.mcp_server.server        # stdio; también: futsal-mcp
```
Configuración: `API_BASE_URL` (entorno o `.env`; por defecto `http://127.0.0.1:8000`).
Claude Desktop: `{"mcpServers":{"futsal":{"command":"python","args":["-m","futsal.mcp_server.server"],
"env":{"API_BASE_URL":"http://127.0.0.1:8000"}}}}`.

## Tests
`python -m pytest tests/test_mcp_server.py -q` (API simulada con `httpx.MockTransport`; sin PostgreSQL).

## Prueba real (stdio, cliente `mcp`)
Descubre las 6 tools, `list_matches(group_id=1, status="finished")` → 13 partidos reales,
`get_match(2)` → detalle, `get_match(999999)` → `error_no_encontrado`, `status="bad"` → error de validación.

## Decisiones
- SDK oficial `mcp` (FastMCP), ya instalado; transporte stdio (el más simple para uso local).
- Cliente `httpx` síncrono inyectable (tests); dependencia `mcp` añadida a `pyproject.toml`.
- API sin cambios.

## Limitaciones
- Solo stdio; sin autenticación, paginación ni caché.
- Los IDs son internos de BD: hay que navegar competiciones → grupos → jornadas/partidos.
- La API no expone clasificaciones, jugadores ni eventos de acta.
