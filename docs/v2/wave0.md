# v2 · Oleada 0 — registro de ejecución (2026-11-09)

Autorización del responsable: A1–A9 del 2026-11-09. Opus planifica, define contratos, revisa y acepta; Sonnet ejecuta en
worktrees aislados (uno por tarea, sin solapamiento de archivos). Sin commits ni push hasta la aprobación del responsable.

## Estado inicial comprobado

- Rama `claude/laughing-volta-5ofbhl`, último commit `c50c7a7` (solo documentación v2). Migración en `0006`.
- Entorno: Python 3.12 (venv) y 3.13; PostgreSQL 16 desechable local (sin pgvector instalado); `GEMINI_API_KEY` presente;
  **sin `data/`** (ni cachés de la RFFM ni BD del usuario).
- Batería offline: **1399 pass, 7 fail, 35 skipped**. Fallos: 6 por orden de pruebas (alembic `fileConfig` deshabilita
  los loggers `futsal.*`; parecía la variable `GEMINI_MODEL`) y 1 prueba PG obsoleta (tarjetas, Fase 25.4).

## Contradicciones documento ↔ código/realidad (resueltas)

| # | Documento | Realidad | Resolución |
|---|---|---|---|
| 1 | data-model: club = número del escudo [H] | el número cambia dentro de la temporada (A1) | club = `codigo_club` oficial (Revisión A2) |
| 2 | data-model: linaje = club+categoría+sufijo | `CodEquipo` estable entre temporadas y ascensos (A1) | `team_entities` por `CodEquipo` |
| 3 | ingestion: «problema del arranque» | existe `/api/competitions?temporada&tipojuego` (A1) | semilla por API |
| 4 | ingestion: 26 páginas por grupo | `calendario` trae la temporada en 1 página (A1) | barrido por calendario |
| 5 | delegation T03: `ingest_jobs`/`raw_fetches` en 0007 | son de la cola (oleada 1) | pasan a 0008 |
| 6 | PROJECT_CONTEXT: 3 fallos en 3.13 «de entorno» | fallo de la prueba (sangría de docstrings) | corregida la prueba (T01) |
| 7 | Fase 25.4: pruebas PG actualizadas | una seguía esperando «amarilla = invención» | corregida (T01) |
| 8 | models.md: precios a reverificar | verificados hoy en las páginas oficiales: coinciden | `llm_prices.json` con fecha |
| 9 | evaluation: el agente admite `clock` | solo lo usa la frescura del Tier 0; la fecha que cambia respuestas está en la API (`create_app(today=)`) | Eval inyecta ambos |
| 10 | A9 propuesta `aggregate_only` | el responsable pedirá permiso a la federación | privado; sin datos personales de menores expuestos hasta el permiso |

## Tareas

| Tarea | Resp. | Archivos | Depende | Estado | Resultado medido |
|---|---|---|---|---|---|
| A1 Sondeo RFFM | Opus | `scripts/rffm_probe_a1.py`, `docs/v2/probe-a1.md`, `tests/fixtures/rffm_catalog/` | — | **hecho** | 20/20 GET, todas 200, ≥ 5 s entre peticiones; 14 hallazgos |
| A2 Revisión del modelo | Opus | `docs/v2/data-model.md` | A1 | **hecho** | esquema real inspeccionado en 0006; 7 cambios con evidencia |
| T01 Correcciones rápidas | Sonnet | `agent/agent.py` (lotes), `api/app.py` (`/teams`), `tests/conftest.py`, 3 pruebas | — | **aceptada** | herramientas independientes en paralelo (≈ max en vez de suma, salida idéntica en 60 semillas); `/teams` sin acentos; 7 fallos previos corregidos |
| T04 Pasarela y OpenAI | Sonnet | `agent/llm.py`, `agent/trace.py`, `config/llm_prices.json`, `.env.example`, `tests/test_llm_providers.py` | — | **aceptada** | Responses API (verificada), `create_llm(task)`, Haiku 5.5, caché y razonamiento opcionales; cuerpo Gemini idéntico byte a byte (prueba de oro); 40 pruebas |
| T04b Coste por llamada | Opus | `agent/agent.py` (`_call_cost`), `tests/test_call_cost.py` | T01, T04 | **hecho** | coste solo con tokens del proveedor y tarifa registrada; si no, `null` |
| T03 Migración 0007 y clasificador | Sonnet | `alembic/0007`, `db/models.py`, `domain/catalog_classify.py`, `domain/competitions.py`, `config/…`, `importer/catalog_*`, CLI `catalog-backfill` | A1, A2 | **aceptada** | ida y vuelta 0006↔0007 con recuentos y md5 idénticos; 195 competiciones reales: 149 `auto`, 46 `needs_review`, 0 senior sin señal; oro de las 14 actuales idéntico |
| T02 Futsito-Eval v1 | Sonnet (+ revisión Opus) | `src/futsal/evals/**`, `evals/**`, `tests/evals/**`, `pyproject.toml` (Opus: `pyyaml`, package-data) | T04 | **aceptada tras 1 ronda de correcciones** | 160 casos / 190 turnos (S1–S9), 48 holdout sellados (30 %), `smoke40`; suite `regression` = evaluador actual sin reescribir; verdad solo SQL; calificador 79/80 (98,8 %) en etiquetas del mismo autor; críticos = invención (también sin señuelo) y contaminación de homónimo/categoría/temporada; reloj fijado en agente (`_clock`) y API (`today`); tarifas solo vía `estimate_cost` |
| A3 Baseline Gemini | Opus | `evals/runs/…` | T02 + snapshot | **bloqueada** | falta el snapshot de datos (decisión del responsable) |

Batería integrada final (T01+T02+T03+T04+T04b): **1691 pass, 0 fail, 35 skipped** (Python 3.12, PG 16, con `GEMINI_MODEL` exportado).
Ruff global sigue con ~169 avisos y mypy con 22 errores **anteriores**; ningún archivo nuevo o tocado añade avisos nuevos.

## Estimación de gasto A5 (`python -m futsal.evals plan`, consumo medido del agente actual [M]; razonamiento no incluido)

| Modelo | smoke40 ×1 | batería completa ×3 |
|---|---|---|
| gemini-3.5-flash-lite | cuota gratuita (0,16 si fuera de pago) | cuota gratuita (2,03) |
| claude-haiku-5-5 / gpt-6-luna | 0,05 | 0,65 |
| gpt-6.1-terra | 0,53 | 6,53 |
| claude-sonnet-5-5 | 1,05 | 13,05 |

F‑A + F‑B completas ≈ 21–23 USD + smokes ≈ 2 USD: dentro del tope de 35 USD. No se ha gastado nada.

## Pendientes y riesgos conocidos

- El descubrimiento todavía no rellena los metadatos oficiales (la importación ya los persiste si llegan): T05.
- 7 competiciones femeninas de 1ª/2ª/Preferente quedan `category=unknown` (sin edad en el nombre): tabla por
  `CodigoCategoria` o `catalog-review`.
- `tests/integration/conftest.py` no vacía las tablas nuevas (las pruebas de T03 se limpian solas).
- Paralelismo de herramientas: los lotes con `team_id`/`round_id` siguen en secuencial (sin cambio de conducta).
- El calificador de Eval se ha etiquetado por el mismo autor: mide consistencia, no independencia (revisión humana pendiente).
- El detector de nombres inventados exige ≥ 2 palabras con mayúscula: un nombre de una sola palabra no se detecta.
- La suite `regression` (65/72) solo se ha comprobado con el snapshot sintético; las cifras se miden con el real.

## Decisión

Continuar: la Oleada 0 cumple sus criterios salvo A3, bloqueada por datos. Siguiente: decisión del snapshot y de P0, después T05 (descubrimiento) y T06 (cola) offline.
