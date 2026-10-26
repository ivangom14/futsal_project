# Fase 17 — Evaluación integral, robustez y regresión del agente

Objetivo: poder decir, con datos reproducibles, **cuándo el agente acierta, cuándo usa una fuente incorrecta, cuándo carece de evidencia y cuánto cuesta una consulta**, sin duplicar el evaluador de las fases 8-16 y sin llamar a ningún LLM.
Todo lo de esta página se ejecutó **offline** (LLM guionizado / sin LLM). **No se ha evaluado la calidad real de Gemini ni de Claude**: los casos que dependen del texto del modelo se juzgan solo en `--mode live`, que no se ha ejecutado.

## Auditoría del estado de partida
Rama `claude/tender-rubin-rsz9uv`, HEAD `9d3b853` (Fase 16). Evaluador existente: `agent/evaluate.py` (46 casos, verdad = SELECT a PostgreSQL), criterios anti-invención (`criteria.py`), informe/regresión (`report.py`, `regression.py`), modos `offline`/`live` con `--plan`, `--max-requests`, `--confirm-live`. Línea base medida antes de tocar nada: 332 tests pasan y 118 se omiten (sin PostgreSQL), Ruff y mypy limpios; con un PostgreSQL local temporal pasan 448 y se omiten 2 (los `live`).

Huecos verificados:
1. **El grupo evaluado dependía del orden de importación**: `load_truth` tomaba el grupo de menor `id`. Con el catálogo de 25 grupos (Fase 14) podía no ser el del proyecto (`config/targets.py`): en la base reconstruida para esta fase eso dejaba **0 actas** y un fallo espurio (`acta_jugador_ambiguo`). Ahora se elige por código externo del objetivo, con el de menor `id` como respaldo.
2. Los casos de tablas solo cubrían **un grupo**, no había comparación entre equipos, otro grupo/competición, tablas vacías, grupos de eliminatorias, no ingeridos, con fallo o parciales, ni consultas que **combinen** clasificación/goleadores con partidos.
3. El informe no distinguía «sin datos», «no ingerido» y «error», ni separaba los **fallos críticos** (invención, fuente oficial sustituida) de la media, ni daba llamadas medias/máximas, cobertura por fuente o causas de fallo.
4. Un error de tool convertido en cifras de una tabla no se detectaba salvo que además fueran incorrectas respecto a la verdad.
5. Los casos de equipo asumían un equipo llamado «DOSA»; sin él desaparecían (ahora hay un respaldo derivado de los datos).
6. Sin tests de robustez de `get_standings`/`get_scorer_table` en el MCP (404, 5xx, timeout, contrato de parámetros).

## Qué se añadió
| Pieza | Contenido |
|---|---|
| `agent/audit.py` | Taxonomía de situaciones (correcto y completo / parcial / sin datos / no ingerido / error de ingesta o consulta / fuente no verificable / no evaluable), categorías A-G, **errores críticos**, resumen (aprobación sobre evaluables, llamadas medias/máximas, repetidas, sobre el límite, con error, fuente no permitida, latencia, tokens, coste, RAG, causas de fallo) y recomendaciones priorizadas deterministas |
| `agent/audit_cases.py` | 18 casos nuevos (ver abajo). Identificadores siempre derivados de la base; sin precondición el caso es **no evaluable** con el motivo |
| `agent/evaluate.py` | Grupo objetivo por código externo; `TableGroup` (estado de ambas tablas en cada grupo, con SQL propio e independiente de la API); hechos `table_status` y `team:position`; `EvalCase.expect`; `CaseResult.expect/critical`; `--price-in/--price-out` |
| `agent/criteria.py` | `claims_position` (el puesto, no cualquier número) y `figures_without_table` (cifras de una tabla que ninguna tool devolvió) |
| `agent/report.py` | Sección «Auditoría de fiabilidad (Fase 17)» en `report.md` y clave `audit` en `results.json` |

Casos nuevos (categoría → letra del plan):
- **A**: `resultados_otro_grupo` (último partido de un equipo de OTRA competición: detecta confundir grupos).
- **B**: `tabla_clasificacion_posicion_equipo`, `_comparacion`, `_otro_grupo`; estados: `_eliminatorias` (`not_applicable`), `_no_ingerida`, `_fallo_ingesta`, `_incompleta`, `_jornada_no_ingerida`.
- **C**: `tabla_goleadores_top_n`, `_otro_grupo`, `_nombre_repetido`, `_vacia`, `_no_ingerida`, y `_por_jornada_no_verificable` (la RFFM no publica histórico de goleadores: «fuente no verificable»).
- **F**: `combinada_lider_ultimo_resultado`, `combinada_lider_proximo_partido`, `combinada_goleador_y_puesto_de_su_equipo` (enlaza goleadores y clasificación por `team_id`, porque el nombre del equipo se escribe distinto en ambas tablas).

Las categorías **D** (actas) y **E** (reglamento/RAG) ya estaban cubiertas por las fases 9-15 (14 y 20 casos; incluyen sin evidencia, circulares RFEF y distinción FIFA/RFEF); no se duplicaron. No hay caso de «pregunta normativa ambigua que exige precisar el ámbito» (pendiente: necesita un criterio de aclaración para reglamento).

### Qué es un fallo crítico
Un caso `fail` que además (a) da afirmaciones sin respaldo (marcadores, jugadores, citas, cifras: `invention_ok = False`) o (b) usa una herramienta no permitida en una pregunta de **tabla oficial** (reconstruir la clasificación o los goleadores con resultados o actas). Se listan aparte y encabezan el informe y las recomendaciones; no se compensan con la media. Un error de MCP/API seguido de cifras de una tabla no vista es invención crítica.

## Verdad de referencia
| Pregunta | Fuente de verdad | Comparación |
|---|---|---|
| Resultados y calendario | `matches`/`rounds` | marcador, equipos, fecha, recuentos |
| Clasificación | última captura de `standings_snapshots/rows` por jornada | equipo, puesto, puntos, jornada |
| Goleadores | última captura de `scorer_snapshots/rows` | jugador, goles, empates completos |
| Actas | última observación de cada acta | goles, tarjetas (código), árbitros |
| Normativa | fragmentos recuperados por `search_rules` | regla/página/texto esperados |
| Estados (vacía, no ingerida, error…) | SQL propio sobre capturas e intentos | la tool debe declarar el estado del grupo correcto (por competición y nombre de grupo) |

Casos sin verdad fiable → `facts=None`, estado `not_evaluable` y motivo. La respuesta del agente nunca es su propia referencia.

## Resultados medidos (offline, 2026-10-25)
**Entorno**: base PostgreSQL local temporal **reconstruida desde las cachés HTML ya descargadas** (catálogo de 25 grupos, 13 actas del grupo inicial, clasificaciones y goleadores de la caché, 3 documentos RAG indexados con la CLI). **No es tu base** y no contiene resultados posteriores a los snapshots. Evaluación offline = rutas de referencia guionizadas por el MCP/API reales (tokens **estimados**).

| | Antes (HEAD `9d3b853`, misma base) | Después |
|---|---|---|
| Casos generados | 46 | 72 (18 nuevos + 8 que antes no se generaban porque el grupo evaluado era otro) |
| Pass / fail / no evaluables | 29 / 1 espurio (grupo equivocado) / 16 | **55 / 0 / 17** |
| Errores críticos | n/d | 0 |
| Llamadas a tools por consulta (evaluables) | n/d | media 1,2 · máx. 2 · 0 repetidas · 0 errores |
| Tokens de entrada (estimados) | 238.588 | 451.272 (más casos) |

Por categoría (aprobados/evaluables): A 12/12 · B 7/7 · C 6/6 · D 11/11 · E 15/15 · F 3/3 · G 1/1. **100 % no significa calidad del modelo**: en offline solo demuestra que las tools devuelven la evidencia correcta y que la ruta de referencia basta. Recuperación RAG: 15/15 casos con el fragmento esperado; validez de citas: **no medida** (necesita texto real de un LLM).

No evaluables (17): 10 requieren LLM real (`equipo_inexistente`, `jornada_inexistente`, `acta_dato_no_almacenado`, 5 «sin evidencia» de reglamento, `tabla_goleador_no_figura`, `tabla_goleadores_por_jornada_no_verificable`), 7 por precondiciones ausentes en esta base (`tabla_*_no_ingerida/fallo_ingesta/incompleta`, `equipo_ambiguo`, `acta_discrepancia_resultado`, `acta_goleadores_ultimo`; los estados se cubren con la BD sembrada de los tests de integración).
Latencia por consulta (local, sin LLM): media 36 ms, máxima 56 ms. Coste en dinero: **no medido** (sin LLM real; con `--price-in/--price-out` y tokens de proveedor se calcula, nunca con tokens estimados).

### Hallazgos de datos (no del agente)
- El nombre de equipo difiere entre la tabla de goleadores y la de clasificación (`RODILLITO B` / `RODILLITO 'B'`): hay que enlazar por `team_id`, nunca por nombre.
- Empate triple en el máximo goleador del grupo inicial (5 goles): el agente debe dar todos (el caso lo exige).
- Los grupos de eliminatorias devuelven `not_applicable` en clasificación; Copa fase final tiene goleadores `empty`.

## Qué cubren los tests nuevos (sin LLM ni red)
- `tests/test_agent_audit.py` (unitarios): letras, expectativas, críticos, resumen y tasa sobre evaluables, métricas de herramientas, coste, aviso offline, recomendaciones, `claims_position`, hecho `table_status`.
- `tests/test_robustness_tables.py`: errores de API (404/500/503/timeout/conexión) → error controlado, nunca datos; contrato y reenvío de parámetros de `get_standings`/`get_scorer_table`; `figures_without_table`.
- `tests/integration/test_agent_audit_pg.py` (PostgreSQL temporal sembrado con todos los estados): los 17 casos pasan offline; precondiciones ausentes → no evaluable; grupo objetivo por código externo; reconstruir con resultados = crítico; cifras inventadas para un grupo de eliminatorias = crítico y respuesta honesta = pass; puesto vs número suelto; error de API convertido en cifras = crítico; llamadas repetidas y bucle; el informe y `results.json` llevan la auditoría.

## Comandos (Windows CMD, desde la raíz del repositorio)
```bat
.venv\Scripts\activate.bat
python -m pytest -q
ruff check .
python -m mypy
python -m pytest tests\test_agent_audit.py tests\test_robustness_tables.py -q
python -m pytest tests\integration\test_agent_audit_pg.py -q
```
Los tests de integración necesitan PostgreSQL (`docker compose up -d --wait db`); sin él se **omiten** (no fallan). Evaluación offline sobre tu base (API en otra ventana: `uvicorn futsal.api.app:app_factory --factory --port 8000`):
```bat
python -m futsal.agent.evaluate --output data\agent\eval-fase17-offline.json
```
Usa siempre `--output` con un nombre propio: **por defecto sobrescribe `data\agent\eval-last.json`**. El informe (`report.md`, con la sección de auditoría) queda en `data\agent\eval\<run>\`.

### Evaluación live (NO ejecutada; consume cuota; la lanzas tú)
Primero la previsión, que no llama a ningún LLM (el plan no cuenta como «evaluables» los casos que solo se juzgan con LLM real, así que la cuota real será algo mayor):
```bat
python -m futsal.agent.evaluate --plan --only tabla_clasificacion_posicion_equipo,tabla_clasificacion_otro_grupo,tabla_clasificacion_eliminatorias,tabla_goleadores_top_n,tabla_goleadores_vacia,combinada_lider_ultimo_resultado,combinada_goleador_y_puesto_de_su_equipo,resultados_otro_grupo --max-requests 40
```
(En la base reconstruida: 8 casos, 18 llamadas previstas, ≈ 63.500 tokens de entrada estimados.) Ejecución acotada, tras revisar el plan y con tu autorización:
```bat
python -m futsal.agent.evaluate --mode live --confirm-live --only <ids> --max-requests 40 --price-in <USD por Mtok> --price-out <USD por Mtok> --output data\agent\eval-fase17-live.json
```
Los precios los pones tú (no se suponen). Compara con una ejecución live anterior solo si es del mismo proveedor/modelo (`--compare`); las mediciones live de las fases 10-11 (Gemini, 29 casos) **no son comparables** con esta suite de 72 casos ni con tokens estimados.

## Limitaciones y pendiente
- Sin evaluación live: calidad del texto, elección de herramientas, citas RAG y los 10 casos «solo live» siguen sin medir.
- Los criterios de respuesta son léxicos deterministas (acotan la invención, no la descartan). `figures_without_table` solo actúa cuando no hay filas de tabla; con filas, cifras derivadas incorrectas solo las atrapa la comparación con la verdad.
- `acta_dato_no_almacenado` y otros casos de la Fase 10 siguen siendo `not_evaluable` offline; el plan subestima la cuota de esos casos.
- Faltan casos de jugador que cambia de equipo (no observado en los datos) y de paginación de goleadores (no observada) y de pregunta normativa ambigua.
- Se sobrescribió `data/agent/eval-last.json` (fichero local ignorado por Git, de una ejecución previa) al evaluar con la salida por defecto; si lo necesitabas como referencia histórica no es recuperable desde el repositorio.

## Corrección del agente: aclaraciones de ámbito (tras la primera evaluación live)
**Causa raíz.** Con más de un grupo importado el contexto del agente es un catálogo y su prompt le dice que pida aclaración si la pregunta no indica competición y grupo. Gemini lo hacía bien («¿a qué competición y grupo te refieres?»), pero el bucle (`agent/agent.py`) trataba cualquier respuesta sin tools como un dato sin respaldo: primero forzaba una reconsulta (`RECONSIDER_MESSAGE`, que le empuja a consultar) y, si el modelo repetía la aclaración, la sustituía por el aviso de alcance (`guardrail = sin_datos_de_tools`). Solo `states_tool_limit` (limitación de herramientas) estaba exento; una aclaración no.
**Comportamiento anterior → nuevo.** Antes: aclaración → aviso de «solo puedo responder…». Ahora la respuesta se conserva (`guardrail = aclaracion_ambito`, sin reconsulta forzada) únicamente si se cumplen TODAS:
1. la pregunta usa vocabulario de la competición (`mentions_domain`): una pregunta ajena sigue rechazándose;
2. hay más de un grupo en el catálogo y la pregunta no nombra competición y grupo (`scope_identified`): si ya los nombra, se sigue el flujo normal y la petición innecesaria no se acepta;
3. no se ha llamado a ninguna herramienta (tras tools, el flujo no cambia);
4. el texto es una petición de ámbito (`asks_scope_clarification`): ≤ 600 caracteres, vocabulario de competición/grupo/temporada, una fórmula de petición, **sin cifras ni marcadores** (salvo los números de los nombres del catálogo, como «Grupo 2»). Un signo de interrogación por sí solo no basta.
**Pruebas** (`tests/test_agent_scope_clarification.py`, 9, LLM guionizado): goleadores sin grupo y goleador+posición sin grupo conservan la aclaración; temporada y opciones del catálogo; pregunta con competición y grupo → flujo normal; pregunta ajena con respuesta «en forma de aclaración» → rechazada; cifras/marcadores con pregunta → rechazados; preguntas desnudas → no son aclaración; error de tool → sigue siendo error, sin cifras; limitaciones legítimas → aceptadas. Con el `agent.py` anterior fallan 3 de 9 (las de aclaración); las otras 6 protecciones pasan antes y después.
**Limitaciones.** El texto de una aclaración sin cifras podría nombrar datos sin números (p. ej. un nombre de jugador) y no se verifica en tiempo de ejecución; en la evaluación los criterios anti-invención (`unsupported_players`, etc.) siguen aplicándose sobre la respuesta final. No hay aún un caso del evaluador que exija la aclaración ante una pregunta ambigua. Los casos de la Fase 16 que dicen «del grupo» siguen siendo ambiguos en una base con 25 grupos.

## Corrección del agente: respuesta segura al agotar el límite de herramientas (Fase 17.2)
**Causa raíz.** En `Agent._loop`, si el modelo pedía más herramientas de las permitidas (`MAX_TOOL_CALLS`, 5), el bucle fijaba `result.error = "límite de N tool calls alcanzado"` y salía sin tocar `result.answer`, que seguía siendo `""`. Es lo que vio la ejecución live de `combinada_lider_ultimo_resultado`: el modelo ya tenía el partido correcto y siguió consultando, y el usuario recibió una respuesta vacía.
**Comportamiento anterior → nuevo.** Antes: `answer = ""`, `error` fijado. Ahora: `answer = LIMIT_REACHED_MESSAGE` («No he podido completar la consulta: he alcanzado el límite… Prueba a concretar la pregunta»), `guardrail = "limite_herramientas"` y `error` **se conserva** (código de salida de la CLI, fallo del caso en el evaluador y traza no cambian). El mensaje no contiene cifras ni afirma que el dato no exista. El texto del modelo que acompaña a las herramientas pendientes **no se muestra** (no está verificado) y no se presenta información parcial. Sin cambios: respuestas finales antes del límite, aclaraciones de ámbito (`7510d00`), rechazo de preguntas ajenas, guardarraíl de datos sin tools, errores del proveedor LLM (`answer` vacío y `error` con el diagnóstico, no se tragan) y errores de herramienta, que siguen llegando al modelo.
**Archivos.** `src/futsal/agent/agent.py` (constante y 2 líneas en el bucle), `tests/test_agent.py` (el test del bucle infinito ya no exige `answer == ""`), `tests/test_agent_tool_limit.py` (8 tests nuevos).
**Pruebas** (LLM guionizado, sin red): límite sin respuesta → mensaje no vacío, sin cifras y con diagnóstico; respuesta final previa al límite intacta; un lote que excede el límite no se ejecuta ni se muestra su texto («Quedó 3-1»); errores de herramienta repetidos hasta el límite no producen cifras; error seguido de respuesta honesta no se toca; aclaraciones legítimas; rechazo de preguntas ajenas; fallo del proveedor no convertido en el mensaje del límite.
**Limitaciones.** El caso `combinada_lider_ultimo_resultado` seguirá fallando en live si el modelo vuelve a gastar las 5 llamadas (ahora con una respuesta honesta, pero sin resolver): la eficiencia del modelo no se ha tocado (no se modificó el prompt). En el evaluador live la respuesta del mensaje también se puntúa en las capas de respuesta; el estado del caso sigue siendo `fail` por el error del límite. No validado con Gemini real.

## Progreso durante la ejecución (Fase 17.3)
`evaluate` imprime ahora, mientras corre, una línea por caso al empezar (`[n/total] id (peticiones al LLM usadas: x/tope · segundos)`) y otra al terminar (`-> PASS|FAIL|NOT_EVALUABLE|NOT_RUN`, llamadas LLM, tools, tokens de entrada, segundos y el motivo si no pasa; `CRÍTICO` si procede). Solo es salida por pantalla (`on_progress`): no cambia resultados, informes ni cuota.
