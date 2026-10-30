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

Fase 11.6 (tercera medición online, `07006ca`): entrada real 157.967 (−8,2 % vs 172.078), 58 llamadas, herramientas 27/27; 3 fallos por mi
frase de limitación en segunda persona («No dispones…»), corregida con ejemplo en primera persona. Prefijo −13,6 % por llamada (estable);
el total depende de la conducta del modelo (54-61 llamadas). Pendiente: repetir online y medir con varias ejecuciones.

Fase 11.7: repetición de las 3 limitaciones (`9e6214f`): respuestas correctas en primera persona, pero el detector de fugas marcaba la fórmula
`LIMITATION_REPLY` que el propio prompt pide; ahora se descuenta (como el aviso de alcance). Pendiente: confirmar online.

Fase 11.8: las 2 limitaciones pasan online con `73e03b6` (2/2); `jornada_inexistente` pasó con `9e6214f`. Pendiente solo una suite completa
con el commit final para cerrar la fase.

Fase 11 cerrada (suite completa con `83d1472`): entrada real 158.715 (−7,8 % vs 172.078), 60 llamadas, 26 pass / 1 fail / 2 n.e.; único fallo =
`list_teams` de más en `acta_arbitro` (variación del modelo). Prefijo −13 %/llamada, `limit` operativo, 0 reintentos. Informe: `docs/phase11-report.md`.

Fase 13 (evaluación continua, solo offline): `agent/regression.py` (umbrales configurables, veredicto, `overall_status`, historial), `results.json`
con `run_id`/`suite_version`/`commit_dirty`, estado global en el informe (`INCOMPLETA` si hay casos sin ejecutar), flags `--baseline`,
`--fail-on-regression`, `--max-*-pct`, `--list-runs`; `--compare` conserva su salida y añade el veredicto. Detalle en `docs/agent.md`. Sin llamadas
online; Fase 12 (llamadas redundantes) no implementada.

Fase 14 (ingesta de 14 competiciones senior/juvenil FS, temporada 22; detalle e inventario en `docs/phase14-catalog.md`): catálogo desde
`pageProps.competitions` y grupos desde `/api/groups?competicion=` (14/14 confirmadas, 25 grupos, sin migraciones). CLI `discover-catalog`,
`scrape-catalog [--plan|--resume|--competition ID]`, `import-catalog`, `verify-catalog`; trabajo en `data/rffm/catalog/s22_t3/`. API: `groups` en
`/competitions` y `GET /teams?name=`; MCP `find_teams`; el contexto del agente pasa a catálogo (sin equipos) con >3 grupos. Descubrimiento real
hecho. Descarga real autorizada (3 pasadas): 12/25 grupos completos, 12 parciales, 1 sin jornadas, 2.982 partidos (`docs/phase14-report.md`); importada y verificada solo en una BD temporal (0 duplicados/huérfanos). **Pendiente: reanudar los grupos parciales e importar en la BD real del usuario.**
Sin llamadas LLM. Pendiente: recalibrar la línea base online del agente tras la ingesta; actas solo del grupo inicial.

Fase 15 Entrega 3 (opción A, léxica; `docs/phase15-agent.md`): `GET /knowledge/search`, tool MCP `search_rules` (descripción con glosario de reformulación) y prompt del agente
con reglas del juego; preflight espera `find_teams`/`search_rules`. Offline: API = capa de recuperación en las 25 preguntas (PDF real); 368 tests. **No validado con LLM real**. Coste: ≈ +300 tokens/llamada (~+18 % offline sobre el conjunto de 29 casos; hay que recalibrar la línea base online). Decisión pendiente: aceptar/acortar/exponer condicionalmente.

Fase 15 Entrega 4 (interpretada como: casos de reglamento + criterio A/B; `docs/phase15-agent.md`): `agent/rule_cases.py` (4 respondibles + 2 sin evidencia), hechos `rule_cited`/`no_evidence_rules`
y criterios `unsupported_rule_citations`/`unsupported_digits`. Offline: suite completa 27 pass, 0 fail, 8 no evaluables. **Pendiente (requiere autorización)**: ejecución `--mode live` de los 6 casos (comandos en el doc) y decidir A/B,
más el coste de `search_rules` (≈ +300 tokens/llamada; ≈ 7.200 tokens de entrada por caso de reglamento).

Fase 15 cierre offline: decisiones del usuario: `search_rules` con `limit=5` y coste de tokens aceptado, siempre disponible (sin filtro por palabras clave); se mantiene la opción A. Suite offline completa: 27 pass, 0 fail, 8 no evaluables;
`--plan` de los 6 casos de reglamento (8 llamadas, ≈ 29.300 tokens de entrada; ≈ 10-12 llamadas reales) sin LLM. El evaluador imprime «Reglamento: indexado / NO indexado». Procedimiento Windows CMD (dos ventanas) en
`docs/phase15-agent.md`; **la ejecución `--mode live` la hace el usuario en su equipo** (no verificado en Windows ni con Gemini).

Fase 15, primera medición online (usuario, 2026-10-23, Gemini): 3 pass / 3 fail; 12 llamadas, 45.115 tokens de entrada (reales). Un fallo de recuperación real (`regla_duracion_mitad`: fragmento de una frase no recuperado), uno por evidencia esperada estrecha
y criterio de color de tarjeta mal aplicado a reglamento (corregido), y uno de comportamiento (`regla_sin_evidencia_salario`: no busca). Detalle y opciones en `docs/phase15-agent.md`. Pendiente de decisión del usuario: ensanchar la evidencia de `regla_roja_mano`,
fusionar fragmentos cortos / ajustar ranking (reindexado), reforzar el prompt para que consulte antes de declarar «no consta». Opción A se mantiene; sin pgvector.

Fase 15, Circular 27 de la RFEF (`docs/phase15-circular27.md`): segundo documento de la base de conocimiento (`rfef-circular-27-2025-26`, PDF de 220 p.; se indexan p. 1-23 = carta RFEF, circular FIFA 1941 y resumen de cambios, 44 fragmentos;
p. 24-220 idénticas página a página al PDF FIFA ya indexado: no se indexan, motivo guardado en `quality.page_sections`). Mecanismo mínimo: `DocumentSpec.page_sections`, perfil `rfef-circular`, `part` en el hit de la API, `note` con alcance
(circular ≠ normativa RFFM). Retrieval sin perjuicio al FIFA (text@5 0,60/0,88 iguales; MRR −0,02) y circular 4/6 literal, 6/6 reformulada. Evaluador: 6 casos `circular27_*`, no evaluables sin el documento. **Pendiente del usuario**: ingestar en su base local
(comandos en el doc), y cualquier evaluación live del agente (no ejecutada). Limitaciones: cursiva/tachado no conservados, ranking léxico.

Fase 15, Circular 10 rectificativa CTAFS-RFEF 2026/27 (`docs/phase15-circular10.md`): tercer documento (`rfef-circular-10-rectificativa-2026-27`, 12 p., 28 fragmentos, texto propio sin anexos ni solapamiento relevante: 12 % en 6-gramas; se indexa entero). Anula la Circular 9 (no está en la base) y se declara
aplicable a competiciones de la RFEF (no dice nada de la RFFM). Cambio mínimo: `part_heading_line` y perfil `rfef-ctafs-circular` (partes I-VIII y apartados numerados); `note`/descripción genéricas («circulares de la RFEF», sin jerarquía normativa). Retrieval: FIFA y Circular 27 idénticos
antes/después; circular 10 literal 4/8, reformulada 8/8. Evaluador: 8 casos `circular10_*` (suite offline 38 pass, 0 fail, 11 no evaluables con los tres documentos). **Pendiente del usuario**: ingestar en su base local (comandos en el doc) y cualquier evaluación live.

Fase 16, clasificaciones y goleadores oficiales (`docs/phase16-tables.md`): se ingieren las tablas que publica la RFFM (no se calculan). Ambas páginas llevan los datos en `__NEXT_DATA__` (`standings`, `scorers`); la
clasificación admite `jornada=N` (una jornada futura/inexistente devuelve la actual en silencio: se valida), los goleadores solo la tabla actual (solo jugadores con goles, sin posición, sin paginación observada). La web devuelve a veces `null`
(se reintenta; nunca es «vacía»). Migración 0005 (`standings_snapshots/rows`, `scorer_snapshots/rows`, `table_ingest_attempts`), CLI `ingest-standings`, `ingest-scorers`, `tables-report`, API `/groups/{id}/standings|scorer-table`, `/competitions/{id}/scorer-table`,
`/tables/coverage`, MCP `get_standings` y `get_scorer_table` (+≈320 tokens por llamada), 5 casos `tabla_*` del evaluador. Cobertura real (copia de la base): 18/25 clasificaciones y 22/25 goleadores `ok`; 6 grupos de eliminatorias `not_applicable`, 2 goleadores `empty`
(válidos), 1 grupo sin jornadas fuera de la base (`no_group_in_db`). **Pendiente del usuario**: `alembic upgrade head` e ingestar en su base local (comandos en el doc); evaluación live del agente con las tools nuevas (no ejecutada).

Fase 17, evaluación integral y robustez (`docs/phase17-evaluation.md`; solo offline, **sin llamadas LLM**): `agent/audit.py` (taxonomía sin datos / no ingerido / error / parcial / no verificable, categorías A-G, **errores críticos** = invención o fuente oficial sustituida, resumen con llamadas medias/máx., latencia, tokens, coste solo con `--price-in/--price-out` y tokens de proveedor, cobertura por fuente, causas de fallo y recomendaciones) integrado en `report.md` y `results.json` (`audit`).
`agent/audit_cases.py`: 18 casos nuevos (otros grupos/competiciones, estados `not_applicable/empty/not_ingested/failed/incomplete/round_not_ingested`, top N, nombres repetidos, consultas combinadas, confusión de grupos). Corregido: `load_truth` tomaba el grupo de menor id (con 25 grupos dependía del orden de importación); ahora el objetivo de `config/targets.py`. Nuevo criterio `figures_without_table` (error de tool o grupo sin tabla convertido en cifras).
Offline sobre una base reconstruida desde las cachés (no la del usuario): 72 casos, 55 pass, 0 fail, 17 no evaluables (10 solo con LLM real), 0 críticos; suite completa 481 pass / 2 skipped (live). **Pendiente (requiere autorización)**: ejecución `--mode live` acotada (comandos y plan en el doc) y repetir la evaluación offline sobre tu base.
Aviso: `evaluate` sobrescribe por defecto `data/agent/eval-last.json`; usa `--output` propio.
Fase 17.1 (corrección del agente): las aclaraciones legítimas de ámbito (competición/grupo/temporada) se conservan (`guardrail = aclaracion_ambito`) en lugar de sustituirse por el aviso de alcance; solo si la pregunta es de la competición, hay >1 grupo y no los nombra, no hubo tools y el texto es una petición breve sin cifras (`criteria.asks_scope_clarification`, `scope_identified`). 9 tests nuevos; detalle en `docs/phase17-evaluation.md`. Sin llamadas LLM.
Fase 17.2: al agotar `MAX_TOOL_CALLS` sin respuesta final el agente devuelve `LIMIT_REACHED_MESSAGE` (`guardrail = limite_herramientas`) en lugar de `""`; `error` se conserva y no se muestra texto no verificado del modelo. 8 tests nuevos (`tests/test_agent_tool_limit.py`); sin cambios de prompt ni de criterios de evaluación. Detalle en `docs/phase17-evaluation.md`.
Fase 17.4: correcciones del evaluador tras la primera pasada live completa (21 pass / 44 fail; la mayoría por casos ambiguos con 25 grupos y criterios demasiado estrictos): aclaraciones que repiten números de la pregunta, páginas en lista validadas una a una y número de circular respaldado por el documento, herramientas de descubrimiento permitidas, 31 preguntas antiguas con competición y grupo, `--only-failed`. Fallos reales del agente sin tocar (detalle en `docs/phase17-evaluation.md`). Sin llamadas LLM.
Fase 17.5: tercera tanda de correcciones del evaluador (cita literal con reglas dentro ya no es invención, `cobertura_parcial_advertencia` pregunta por las actas, `acta_jugador_ambiguo` acepta la tabla oficial). Segunda pasada live parcial (22 pass / 11 fail / 11 sin ejecutar por presupuesto insuficiente); pendientes 11 casos y los fallos reales del agente. Sin llamadas LLM.
Fase 17.6: consultas de reglamento y circulares. Causas separadas (palabras de circular/RFEF/FIFA fuera del vocabulario de dominio; fórmula de limitación sin herramientas exenta de reconsulta; recuperación léxica del RAG, no tratada). Cambio: `mentions_rules` pide UNA consulta a `search_rules` antes de aceptar un rechazo o «no dispongo» sin herramientas (no valida ninguna respuesta), las preguntas de reglamento no piden grupo y el prompt nombra circulares y documentos (+≈23 tokens/llamada). 9 tests nuevos (`tests/test_agent_rules_scope.py`). Pendiente: repetir en live 7 casos (lista en `docs/phase17-evaluation.md`); RAG léxico, IDs de equipo y eficiencia de búsqueda de partidos sin abordar.
Fase 17.7: verificación live de la 17.6 (5 pass / 2 fail; las 4 consultas de reglamento afectadas ya llaman a `search_rules`; 0 regresiones en los controles). Los 2 «críticos» eran falsos positivos de cifras (número de sección y nombre del documento): los metadatos del fragmento respaldan las cifras citadas desde ellos y, sin evidencia, solo ellos. `circular10_sin_evidencia_circular9` acepta «su contenido no consta» más la mención de que la Circular 10 la anula. Pendiente: RAG léxico (`regla_duracion_mitad` y otros), IDs de equipo, eficiencia de búsqueda de partidos.
Fase 17, medición final (live, `docs/phase17-evaluation.md`): ejecución `20261026-212906-live-all` con `ac48edc`, suite `all-72-b32e31cb2b`, `gemini-3.5-flash-lite`: **53 pass / 12 fail / 7 no evaluables (53/65 = 81,5 %)**; actas 13/13, clasificaciones 7/7, resultados 12/14, goleadores 6/7, reglamento 13/20, combinadas 1/3, alcance 1/1; 172 llamadas, 701.998 tokens de entrada, 0,23 USD (tarifa NO verificada oficialmente), ~11 min. Un solo crítico marcado (intento de reconstruir goleadores desde actas). Es la referencia de cierre; una sola ejecución, con variabilidad por caso entre ejecuciones (limitación, sin intervalo calculado). Pendientes, en orden: criterio del evaluador para `resultados_otro_grupo` (no permite `get_match_report`); recuentos erróneos y reconstrucción desde actas; IDs de equipo y eficiencia de consulta; fase independiente de RAG léxico; actualización controlada de datos y nueva evaluación.
Fase 17.8 (integridad de recuentos y datos oficiales; `docs/phase17-evaluation.md`, sin repetir la evaluación live): `recuento_global` fallaba porque el modelo contaba las 40 filas visibles de una lista recortada (la API no pagina; `count` con `limit=1` es el total exacto): pista de lista INCOMPLETA y regla de recuento en el prompt. `tabla_goleadores_por_jornada_no_verificable`: la nota de `get_scorer_table` y su descripción declaran que es la tabla actual sin desglose por jornada, y el mensaje de límite conserva el máximo goleador/líder oficial ya obtenido (copiado de la tool). 9 tests (`tests/test_agent_counts_official.py`). Pendiente: validar ambos casos en live; IDs de equipo, eficiencia y RAG léxico sin abordar.
Fase 17.9: segunda ejecución live completa con `c6720ff` (48 pass / 17 fail / 7 n.e. = 48/65; no sustituye a la de cierre 53/65; una sola pasada, variabilidad sin medir). `tabla_goleadores_por_jornada_no_verificable` pasa y `recuento_global` da cifras correctas. Ajustes del evaluador (sin tocar agente): pendientes derivados como total − jugados con ambos `count` de las tools, `get_match` en `acta_finalizado_sin_acta`, reglas copiadas con su contexto de un fragmento no son invención. Principal fuente de fallos ahora: IDs de equipo inventados que agotan el límite (agente, pendiente), RAG léxico y datos de competición desactualizados.
Fase 19 (resolución de equipos y eficiencia; `docs/phase19-agent-efficiency.md`, solo offline, sin LLM ni RAG): `list_matches` admite `order=asc|desc` (último partido = `status=finished, order=desc, limit=1`) y marca `past_due` en partidos programados con fecha pasada; el agente solo ejecuta `team_id`/`against_team_id`/`round_id` que una tool mostró en la pregunta (si no, `error_validacion` que cuenta contra el límite), no elige entre equipos homónimos de varios grupos sin competición/grupo en la pregunta y reutiliza llamadas idénticas. 14 tests nuevos; suite 551 pass / 2 skipped con PostgreSQL desechable; Ruff y mypy limpios. Pendiente: validar en live (casos en el doc).
Fase 20 (recuperación de reglamentos y circulares; `docs/phase20-rag-retrieval.md`, solo offline, sin LLM; RAG léxico sin cambios de arquitectura): tabla explícita de 4 equivalencias (`portero→guardameta`, `penalti→penal`, `medio campo↔línea central`; disparadores por palabra exacta), prioridad de la circular citada por número, `type` por fragmento y aviso `cited_circulars`/«no indexada» (Circular 9), y reconsulta única a `search_rules` para el par árbitro + cobro. Variantes coloquiales con evidencia en el top 5: 18/39 → 26/39; Circular 10 literal text@5 0,50 → 0,75; FIFA y Circular 27 sin pérdida de text@5. Pendiente: `circular10_video_solicitudes` y las preguntas literales de saque de meta/distingue FIFA siguen limitadas por el ranking léxico (siguiente paso: híbrido pgvector, no más reglas); la respuesta final de los 8 casos solo se mide con LLM real (no ejecutado).
Fase 20.1 (ajustes tras la ejecución live con `c0ca314`: 48/65, no sustituye a 53/65; `docs/phase20-rag-retrieval.md`): una llamada rechazada por el guardarraíl (`refused`) cuenta como llamada pero no como error de herramienta y solo sugiere `find_teams`; `search_rules(rule=N)` ya no oculta las circulares; `combinada_lider_ultimo_resultado` no exige los puntos del líder. Pendientes: proximo_partido del líder (dato desactualizado), tabla_goleadores_vacia (frase), recuento_global, ranking léxico. Repetir en live los casos afectados.
Fase 21 (actualización y consistencia de datos; `docs/phase21-data-refresh-consistency.md`; sin LLM, sin cambios en el RAG; migración **0006** aditiva): protección de identidad (`identity_conflict`: un partido no cambia de grupo), de partidos finalizados (`finished_regression_held`; se confirma solo si el mismo contenido se repite en otra ejecución ≥24 h después y no hay acta cerrada; las correcciones de marcador y aplazamientos explícitos se aplican) y de capturas oficiales (se sirve la última `ok`; `incomplete`/`empty` posteriores se conservan y se avisan). Módulo `src/futsal/refresh/` (`gates`, `runs`, `freshness`, `reconcile`, `service`) y CLI `refresh` (incremental/full, `--only`, `--group/--competition`, `--dry-run`, `--offline`, `--plan`), `refresh-status`, `refresh-reconcile` (solo lectura, `SET TRANSACTION READ ONLY`), `refresh-recover` (ejecuciones `running` antiguas → `abandoned`, umbral 6 h configurable). Frescura por tipo de dato (`dataset_refreshes` + capturas): API `GET /groups|competitions/{id}/freshness` y tool MCP de solo lectura `get_data_freshness` (el LLM no puede iniciar actualizaciones). Validado con fixtures y con la fuente real en una BD desechable (un grupo). Pendiente: ejecutar `refresh` en tu BD habitual (`alembic upgrade head` antes) y medir en live el agente con la nueva herramienta.
Fase 22 (frontend web MVP; `docs/phase22-frontend.md`; sin LLM en las pruebas, sin migraciones, Fase 21 intacta): servicio `futsal.agent.web` (FastAPI, puerto 8001, agente real sobre MCP; `GET /chat/health`, `POST /chat` con respuesta, fuentes RAG/tablas oficiales y diagnóstico) y `frontend/` (React+TS+Vite+Tailwind; conversaciones en `localStorage`, Markdown, fuentes, detalles técnicos, estado de conexión, reintento). El agente NO tiene memoria conversacional: cada pregunta es independiente (limitación documentada, sin inventarla). Pruebas: `tests/test_chat_web.py` (13), `frontend` Vitest (15), fixture de contrato compartido `tests/fixtures/chat_response.json`; E2E real con `scripts/e2e_chat.py --yes` (lo ejecuta el usuario). Pendiente: validar en Windows con Gemini; contexto conversacional (`history`), streaming.
Fase 23 (memoria conversacional; `docs/phase23-conversational-memory.md`; sin LLM en las pruebas, sin migraciones, MCP/ingesta/Fase 21 intactos): `Agent.run(question, history=())` + `agent/memory.py` (`select_turns`: 6 turnos / 6000 car. / 1200 por mensaje; `ConversationMemory` en memoria, LRU 200). `POST /chat` acepta `conversation_id` e `history` opcionales (el servidor manda; el navegador solo siembra ids desconocidos) y `DELETE /chat/conversations/{id}`; `diagnostics.history_turns`. Seguridad: el historial es solo contexto lingüístico (`HISTORY_RULE`); los IDs siguen validándose contra lo que las tools muestran en la pregunta actual, el guardarraíl «datos sin tools» sigue activo y el ámbito solo se toma de las preguntas previas del usuario. Solo se memorizan respuestas sin error ni guardarraíl (salvo `aclaracion_ambito`). Pruebas: `tests/test_chat_memory.py` (21) y Vitest (24). Pendiente: validación manual con Gemini real y en Windows (guion en el doc); coste de tokens real sin medir; sin persistencia en backend.
Fase 23.1: tras el primer uso real (aclaración «grupo 1 autonomica senior» ignorada; no existe «Autonómica Senior» en el catálogo, solo Aficionado/Juvenil/Tercera…), `HISTORY_RULE` indica que la respuesta a una aclaración continúa la pregunta pendiente y el agente hace UNA reconsulta (`SCOPE_REUSE_MESSAGE`, `criteria.user_gave_scope`) si vuelve a pedir un ámbito que el usuario ya dio. 4 tests nuevos; sin verificar con Gemini real.
Fase 23.2: unión determinista de la pregunta pendiente (`memory.resolve_pending`): si el asistente terminó pidiendo un dato y el usuario responde en ≤ 12 palabras sin `?`, la pregunta efectiva = original + «Precisiones del usuario: …» (solo palabras del usuario); `diagnostics.resolved_question` y «Interpretada como» en la UI. Motivo: `flash-lite` olvidaba la pregunta pendiente (captura del usuario). Sin verificar con Gemini real.
