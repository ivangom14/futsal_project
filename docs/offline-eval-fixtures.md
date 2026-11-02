# Estudio: evaluación sin depender de una PostgreSQL externa

**Problema.** El evaluador real (`agent/evaluate.py`) construye su verdad (`Truth`) desde PostgreSQL (`load_truth`); en el entorno cloud no hay servidor (solo el cliente `psql`), así que las 72 pruebas
de casos solo se ejecutan en tu máquina. **No se ha instalado nada** (decisión tuya); esto es solo el análisis.

| Opción | Qué da | Coste / riesgo | Veredicto |
|---|---|---|---|
| 1. Reconstruir PostgreSQL desde `data/rffm` con los CLI existentes | Evaluador completo reproducible: `import-catalog` → `import-league` → `process-match-reports` → `ingest-standings --offline` / `ingest-scorers --offline` → `knowledge-ingest` (PDF en `data/knowledge`). Todos leen caché local; no hay scraping | `data/rffm` (162 MB) y `data/knowledge` (21 MB) no están en Git; la verdad depende de la fecha de las capturas (tu base actual tiene 182+ partidos y 33 actas; las capturas locales pueden diferir); exige Docker/PostgreSQL con pgvector | **Recomendada en tu máquina o CI propia**; huella de partidos (`preflight`) para comprobar que es la misma base |
| 2. Instalar PostgreSQL en la sesión cloud | Lo mismo que 1 dentro de la sesión | No autorizado; la red del entorno puede bloquear la instalación; el contenedor es efímero (habría que ponerlo en el script de configuración del entorno) | Descartada por ahora |
| 3. Fixture mínimo versionado + API simulada (`httpx.MockTransport`) con el servidor MCP real | Pruebas de integración agente ↔ MCP ↔ forma real de las respuestas, sin base de datos. **Ya se usa**: `tests/test_prefetch_mcp.py` (catálogo real de 363 entradas) | No reproduce `Truth` ni las 72 verdades; hay que mantener la simulación al día con la API | **Adoptada** para pruebas de flujo (prefetch, selección de herramientas, Tier 0) |
| 4. SQLite | — | Los modelos usan tipos de PostgreSQL (JSONB, tsvector, pgvector): no viable | Descartada |

**Qué corre hoy sin base de datos:** todas las pruebas de agente con LLM y MCP simulados (859), la evaluación del router y del extractor de entidades, los resúmenes de comparaciones, `prompt_compare` y la telemetría.
**Qué necesita PostgreSQL real:** `evaluate.py` (las 72 verdades), `tests/integration`, y las comparaciones live.

**Siguiente paso si lo apruebas:** un script `scripts/build_eval_db.py` que ejecute la opción 1 sobre una base desechable (Docker Compose existente) y verifique la huella de la base resultante.
