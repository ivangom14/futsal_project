# PROJECT_CONTEXT

## Objetivo general
Asistente agéntico sobre competiciones deportivas (inicio: futsal, fuente RFFM).
Proyecto personal con vocación de evolucionar a producto.

## Arquitectura acordada
Monolito modular. Flujo: ingesta determinista → PostgreSQL → API REST (FastAPI)
→ MCP de solo lectura sobre la API. Aparte: knowledge base (pgvector), motor
determinista de reglas y agente orquestador. Detalle: `docs/architecture.md`.

## Tecnologías
Python 3.12+, FastAPI, PostgreSQL + pgvector, SQLAlchemy 2, Alembic, Pydantic,
httpx, pytest, Docker Compose. (Solo Postgres está activo en Compose por ahora.)

## Estructura del repositorio
- `src/futsal/{domain,ingestion,repositories,api,mcp_server,knowledge,rules,agent,config}`
  (paquetes vacíos, solo `__init__.py`)
- `tests/`, `docs/architecture.md`
- `docker-compose.yml` (servicio `db`), `.env.example`, `pyproject.toml`
- `CLAUDE.md` (reglas de trabajo), este archivo

## Decisiones tomadas
- Un solo repo y proceso; sin microservicios, Kubernetes ni Kafka.
- Layout `src/`; paquete `futsal`.
- Imagen `pgvector/pgvector:pg16`; credenciales vía `.env` (no versionado).
- `mcp_server` y `agent` nunca acceden a la BD directamente.
- Dependencias de Python se añadirán en cada fase cuando se usen.

## Fase completada
Fase 0: andamiaje (estructura, Compose, .env.example, docs, CLAUDE.md).

## Comandos esenciales
- `cp .env.example .env`
- `docker compose up -d db`
- `docker compose config -q` (validar Compose)
- `pytest` (aún sin tests)

## Limitaciones conocidas
- Sin código funcional, modelos, migraciones, API, MCP, KB ni agente.
- `pyproject.toml` sin dependencias todavía.

## Próxima fase
Fase 1: modelo de dominio mínimo (competición, equipo, partido) y esquema inicial
de BD con Alembic; sin scraping todavía.
