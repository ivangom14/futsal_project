# v2 · Fichas de tarea para Sonnet

Reglas comunes a todas las fichas:
- Leer `CLAUDE.md`, `PROJECT_CONTEXT.md` y el documento v2 de la tarea; inspeccionar solo los archivos citados.
- **Git**: rama local o worktree por tarea (`v2/T0x-…`). Commits pequeños. **Sin push**: Opus revisa e integra en
  `claude/laughing-volta-5ofbhl`, que es la única rama que se publica.
- **Sin red real**: ni RFFM ni LLM. HTTP simulado y fixtures. PostgreSQL solo en BD desechable (`futsal_test_*`).
- Antes de entregar: pruebas específicas y batería offline completa (`pytest -q --ignore=tests/live`), `ruff check .` y
  `mypy` sobre los archivos tocados.
- Entrega: resumen ≤ 10 líneas, lista de archivos, resultado de pruebas y desviaciones del contrato (si las hay, se justifican).
- No crear código ficticio ni ampliar el alcance.

## Propiedad de archivos en la oleada 0 (no se solapan)

| Tarea | Puede modificar | No debe tocar |
|---|---|---|
| T01 | `agent/agent.py` (solo la ejecución de herramientas en `_loop`), `api/app.py` (solo `find_teams`), `tests/test_prompt_compact.py`, pruebas nuevas | prompts, `llm.py`, `domain/`, MCP, evaluador |
| T02 | `src/futsal/evals/**` (nuevo), `evals/**` (nuevo), `tests/evals/**` | `agent/**` (solo importa), `db/**` |
| T03 | `alembic/versions/0007_*`, `db/models.py`, `domain/competitions.py`, `domain/catalog_classify.py` (nuevo), `config/competition_aliases.json`, `config/sources/rffm.json` (nuevo), `importer/catalog_service.py` | `agent/**`, `api/**`, `refresh/**` |
| T04 | `agent/llm.py`, `.env.example`, `config/llm_prices.json` (nuevo), `tests/test_llm*.py` | `agent/agent.py`, prompts |

---

## T01 · Correcciones rápidas (sin cambio de conducta visible)
- **Objetivo**: (a) ejecutar en paralelo las herramientas de una misma respuesta del LLM conservando el orden de los resultados
  y de la traza; (b) `GET /teams?name=` sin distinguir acentos (`kb_unaccent`, que ya existe desde la migración 0004) y con el
  mismo contrato; (c) las 3 pruebas de `test_prompt_compact` pasan en Python 3.13 sin cambiar ni un byte de los textos legacy.
- **Contratos**: forma de `AgentResult`, traza y `ToolCallRecord` idénticos; respuesta de `/teams` idéntica salvo que encuentra
  más coincidencias.
- **Aceptación**: con un MCP simulado que responde con retraso, dos herramientas en la misma respuesta tardan ≈ max y no la
  suma; la salida es idéntica a la secuencial (prueba de propiedad con 50 combinaciones); «Móstoles» encuentra «MOSTOLES»
  (integración PG); la batería pasa en 3.11, 3.12 y 3.13.
- **Riesgo**: `_execute` modifica el estado compartido del agente. La prueba debe cubrir la intercalación de dos llamadas que
  añaden IDs al mismo conjunto.

## T02 · Futsito-Eval v1 (esqueleto completo, sin LLM real)
- **Contexto**: [evaluation.md](evaluation.md) completo.
- **Objetivo**: paquete `futsal.evals`; esquema de caso y cargador YAML; selector determinista de entidades; verdad SQL;
  calificador que reutiliza `criteria.py` más el detector de contaminación; runner sobre `Agent` con `clock` fijo,
  `PacedLLM`, `BudgetedLLM` y parada por cuota; informe (`results.json` y `report.md`) con las métricas del §6; `compare`
  con la regla de decisión del §7; manifiesto de snapshot y comprobación de huella.
- **Dataset**: los 160 casos de los estratos S1–S9 (§4), con el 30 % marcado `holdout: true` y sellado con
  `agent/datasets.py`.
- **Aceptación**: `plan` sin llamadas externas; con un LLM simulado de guion fijo, el runner produce el informe completo; el
  calificador acierta en ≥ 60 respuestas etiquetadas a mano (correctas, parciales, fallos y críticos, incluida la
  contaminación entre homónimos); mismo snapshot y mismo guion → informe idéntico byte a byte salvo marcas de tiempo.
- **No**: modificar el evaluador antiguo (`agent/evaluate.py`), que sigue funcionando.

## T03 · Migración 0007 y clasificador del catálogo
- **Contexto**: [data-model.md](data-model.md).
- **Objetivo**: migración aditiva (columnas de `seasons`, `competitions` y `competition_groups`; tablas `clubs`,
  `catalog_overrides`, `ingest_jobs`, `raw_fetches`; vista `team_lineage`; índices). Clasificador puro
  `domain/catalog_classify.py`, con orden de precedencia `override > campo oficial > regla > unknown`, confianza y `rule_id`
  por atributo. `import-catalog` guarda los campos oficiales que ya se leen (`codigo_tipo_juego`, `NombreCategoria`,
  `CodigoCategoria`, `Activa`, fechas, `nombre_delegacion`) y el resultado de la clasificación. `domain/competitions.classify`
  usa los atributos guardados si existen y, si no, reglas explícitas (`aficionado`, `tercera division` → senior; `juvenil`;
  `cadete`; `infantil`; `alevin`; `benjamin`; `femenin` → female) y **por defecto `unknown`, nunca senior**.
- **Contratos**: las 14 competiciones actuales obtienen exactamente la misma taxonomía que hoy (prueba de oro con sus
  nombres). API, MCP y agente sin cambios.
- **Aceptación**: upgrade y downgrade en PG desechable con los datos de la Fase 14 intactos; tabla de casos del clasificador
  (≥ 40, con adversariales); 0 casos sin señal clasificados como senior.

## T04 · Pasarela de modelos y adaptador OpenAI
- **Contexto**: [agent.md](agent.md) §Pasarela y [models.md](models.md).
- **Objetivo**: `OpenAILLM` (httpx, llamada de funciones, `Usage` con cacheados y razonamiento, errores normalizados con la
  cuota como `LLMQuotaError`); `create_llm(task)` con `LLM_MODEL_<TAREA>`; predeterminado de Anthropic `claude-haiku-5-5`;
  `cache_control` opcional en Anthropic; `thinkingConfig` opcional en Gemini (sin variable, la petición queda idéntica a la de
  hoy); `config/llm_prices.json` con los precios de models.md (fecha y URL); coste por llamada en `LlmCallRecord` (`null` sin
  tarifa); `.env.example` coherente (`LLM_PROVIDER=gemini` como referencia).
- **Aceptación**: pruebas con HTTP simulado para los tres proveedores (texto, llamadas de funciones, varias llamadas
  paralelas, 429, cuota, 5xx y respuesta inválida); sin variables nuevas, el cuerpo de la petición a Gemini es byte a byte
  el actual (prueba de oro).

---

## Oleada 1 (tras A1/A4; las fichas completas se escriben con los resultados del sondeo)

| Tarea | Resumen | Depende de |
|---|---|---|
| T05 Descubrimiento v2 | temporadas, catálogo FS completo sin `CATALOG_SCOPE`, diff de catálogo, fixtures grabadas del sondeo, `catalog-review` | T03, A1 |
| T06 Cola y planificador | `ingest_jobs` con `SKIP LOCKED` y arrendamiento, `ingest tick/work/plan/status`, tabla del planificador por estado de jornada, limitador e interruptor | T05 |
| T07 Clubes y linaje | extracción del código de club del escudo y del sufijo de equipo, `clubs`, `teams.club_id/squad`, uniones manuales | T03, A1 |
| T08 Cobertura | vista `coverage_status`, `GET /coverage`, alertas | T06 |

## Oleadas 2–3 (resumen)

T09 API de dominio (`/v2/resolve`, `team_overview`, `group_overview`, `club_overview`, `matches`, `match_detail`, `scorers`,
`compare`) · T10 registro de herramientas en el mismo proceso más MCP v2 · T11 agente v2 tras `AGENT_ARCH=v2` (resolutor, router
T0/T1/T2, validador, `RunState`) · T12 comparativa de modelos · T13 retirada R1–R5 · T14 documentos RFFM y búsqueda híbrida.
Opus diseña el router y el validador (T11) antes de delegar.

## Revisión de Opus (cada entrega)

1. Diff completo frente a la propiedad de archivos. 2. Contratos: pruebas de oro y esquemas. 3. Ejecución de pruebas en
local. 4. Criterios de aceptación uno a uno. 5. Actualizar `PROJECT_CONTEXT.md` e integrar en la rama publicada.
