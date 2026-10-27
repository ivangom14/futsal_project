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

## Correcciones del evaluador tras la primera pasada live completa (Fase 17.4)
La pasada live de los 72 casos (Gemini `gemini-3.5-flash-lite`, 21 pass / 44 fail / 7 no evaluables) mezclaba fallos del agente con defectos del evaluador y de la formulación de los casos. Se corrigieron solo estos últimos; **ningún criterio se relajó para ocultar un fallo del agente**.
| Defecto | Corrección |
|---|---|
| `asks_scope_clarification` rechazaba una aclaración válida que repetía «jornada 2» de la pregunta (el guardarraíl la sustituía por el aviso de alcance) | Se permiten los números que ya figuran en la pregunta; cualquier otra cifra sigue rechazándose |
| Los 3 «críticos» de reglamento: el criterio de cifras no entendía listas de páginas («p. 85, 90 y 177-178») ni el número de la circular | Las páginas de cada lista se validan una a una contra lo recuperado (`unsupported_rule_citations`, ahora más estricto: una página no recuperada **sí** se señala) y el título/clave del documento cuenta como evidencia del número de circular (`unsupported_digits`) |
| Respuestas correctas que fallaban por usar `find_teams`, `list_competitions`, `list_groups` o `list_rounds` (de solo lectura; necesarias para localizar un equipo entre 25 grupos) | Permitidas en todos los casos salvo rechazos de alcance y reglamento (`DISCOVERY_TOOLS`); siguen sin poder reconstruir tablas |
| `combinada_lider_proximo_partido` exigía los puntos del líder, que la pregunta no pide | Solo se exige el partido (y la herramienta de clasificación sigue siendo obligatoria) |
| Preguntas anteriores a la Fase 14 sin competición ni grupo («el grupo», «el C.D. DOSA»), ambiguas con 25 grupos: el agente pedía aclaración con razón | Con más de un grupo importado se antepone «En <competición> (<grupo>), » (`SCOPED_IDS`, 31 casos); verdad y criterios no cambian |
Además: `--only` y `--only-failed` implican `--suite all` si no se indica otra (antes, en live, la suite `smoke` por defecto dejaba la selección vacía), y `--only-failed RESULTS.json` repite solo los casos `fail` o `not_run` de una ejecución anterior.
**Siguen sin tocar (fallos reales del agente observados):** no consulta `search_rules` antes de declarar «no consta» (4 casos), afirma «no figura en el grupo» tras un error de herramienta, usa `find_teams` con un nombre de jugador, 3 fallos de recuperación del RAG y `combinada_lider_ultimo_resultado` agota el límite.
Repetir solo lo fallido: `python -m futsal.agent.evaluate --mode live --confirm-live --only-failed data\agent\eval-fase17-live-all.json --max-requests 100 --price-in 0.30 --price-out 2.50 --output data\agent\eval-fase17-live-2.json`. Los resultados de la primera pasada **no son comparables caso a caso** con la repetición (cambian preguntas y criterios en los casos corregidos).

## Tercera tanda de correcciones del evaluador (Fase 17.5)
Tras la repetición de los casos fallidos (22 pass / 11 fail / 11 sin ejecutar: el presupuesto de 100 peticiones fue insuficiente, error de planificación mío; para 44 casos hacen falta unas 130):
- **Cita literal con otras reglas dentro** (`circular10_penal_no_acumulable`): «según lo estipulado en la Regla 3, la Regla 4 y la Regla 12» es texto del fragmento de la Regla 13, no una cita del modelo. `unsupported_rule_citations` ignora una cita entrecomillada solo si está literalmente en los fragmentos recuperados (≥ 20 caracteres); una cita alterada o las reglas fuera de las comillas se siguen señalando. La respuesta de ese caso sigue siendo incorrecta (fallo de recuperación), pero ya no se marca como invención crítica.
- **`cobertura_parcial_advertencia`** estaba obsoleto: «¿máximos goleadores del grupo?» se responde ahora con la tabla oficial (Fase 16). La pregunta pide «según las actas importadas», que es lo que comprueba el caso (advertir de cobertura parcial de actas).
- **`acta_jugador_ambiguo`** acepta `get_scorer_table` (los goles de un jugador también están en la tabla oficial).
Sin tocar: fallos reales del agente (IDs de equipo adivinados, no consulta `search_rules` antes de «no consta», preguntas de circulares tratadas como ajenas, fallos de recuperación del RAG).

## Mejora del agente: consultas de reglamento y circulares (Fase 17.6)
Casos live observados (`gemini-3.5-flash-lite`): `circular10_sin_evidencia_circular9` y `circular27_fechas_rfef_fifa` se contestaban con el aviso de alcance; `circular10_sin_evidencia_sancion_rffm` (y `circular27_sin_evidencia_rffm`) con «No dispongo…» sin llamar a `search_rules`; `circular10_distingue_fifa` omitía la matización FIFA. **Tres causas distintas:**
| Síntoma | Causa raíz | ¿Tratado aquí? |
|---|---|---|
| Pregunta legítima rechazada como ajena | Ninguna de sus palabras («circular», «RFEF», «FIFA»…) estaba en `DOMAIN_WORDS`, así que la reconsulta forzada del bucle no se activaba y el modelo se quedaba con su propio aviso de alcance (el prompt tampoco nombraba las circulares) | Sí |
| «No dispongo» sin buscar | La pregunta sí era de dominio, pero la fórmula de limitación sin herramientas está exenta de reconsulta a propósito (Fase 10.1: periodos, sustituciones…). Para reglamento esa exención da por «no consta» algo que nunca se buscó | Sí |
| Respuesta correcta pero incompleta (FIFA p. 12) | Recuperación léxica: con una sola búsqueda («saque de meta guardameta medio campo») salen la Circular 10 y la Regla 16 pero no la introducción FIFA de la p. 12; el modelo tampoco hace una segunda búsqueda. Medido en la base reconstruida, sin LLM | No (RAG léxico, fuera de alcance) |
**Cambio** (`agent/agent.py`, `agent/criteria.py`): `mentions_rules` (regla/s, reglamento, circular/es, normativa, sanción/es: «FIFA» o «RFEF» solos **no** cuentan). Si la pregunta es de reglamento y el modelo responde sin herramientas (aviso de alcance, limitación o cualquier otra cosa), se le pide **una sola vez** consultar `search_rules` (`RULES_RECONSIDER_MESSAGE`) y responder solo con lo recuperado, citando documento, regla y página. Esa petición no valida nada: si insiste sin herramientas, una limitación honesta se conserva y cualquier otra respuesta sin datos de tools se sustituye como antes; las cifras y citas sin respaldo siguen marcándose en la evaluación. Además: las preguntas de reglamento ya no se tratan como «falta grupo» (`mentions_competition`, sin las palabras de reglamento) y el prompt (+81 caracteres, ≈ +23 tokens por llamada) nombra las circulares como ámbito y pide citar el documento sin atribuir a un organismo lo que dice otro. Descripción MCP de `search_rules`, ingestas, RAG y evaluador sin cambios.
**Pruebas** (`tests/test_agent_rules_scope.py`, 9, LLM guionizado): con el código anterior, ambos casos terminaban con 1 llamada y 0 herramientas (reproducido); ahora se pide la consulta. Cubren la Circular 9, la sanción de la Circular 10, rechazo de lo ajeno (incluida «FIFA» sola y una broma sobre una circular), búsqueda sin evidencia, respuesta con evidencia aceptada sin cambios, protecciones de citas y cifras, y que clasificaciones/goleadores mantienen sus aclaraciones.
**Limitaciones:** la recuperación léxica sigue perdiendo fragmentos (`circular10_saque_meta_coloquial`, `circular10_penal_no_acumulable`, `circular10_distingue_fifa`, `circular27_modificaciones`); «¿Cuánto cobra un árbitro…?» no usa vocabulario de reglamento y no se reconsulta; si el modelo ignora la petición el usuario sigue viendo el aviso. No validado con Gemini real.
**Repetición live mínima recomendada (7 casos, ≈ 18 llamadas):** `circular10_sin_evidencia_circular9`, `circular27_fechas_rfef_fifa`, `circular10_sin_evidencia_sancion_rffm`, `circular27_sin_evidencia_rffm` (los cuatro afectados) y, como control de regresión, `fuera_de_alcance`, `regla_duracion_mitad` y `tabla_clasificacion_lider`.

## Criterio de cifras con metadatos y caso de la Circular 9 (Fase 17.7)
La repetición live de la Fase 17.6 (5 pass / 2 fail, 2 «críticos») mostró que el efecto de la mejora era el esperado y que los dos críticos eran falsos positivos de `unsupported_digits`:
- `regla_duracion_mitad`: la cifra «2» era el número de sección («2 FINALIZACIÓN DE LOS PERIODOS»), un metadato del fragmento que el criterio no contaba como evidencia.
- `circular10_sin_evidencia_circular9`: la cifra «10» era el nombre del documento («Circular n.º 10»); en los casos sin evidencia el criterio ignoraba por completo lo recuperado.
**Corrección** (`agent/criteria.py`, `agent/evaluate.py`): título y clave del documento, versión, sección, subsección, título de la regla y procedencia (`HIT_METADATA`) respaldan las cifras que se citan desde ellos. En los casos sin evidencia (`metadata_only`) solo cuentan esos metadatos: el TEXTO de un fragmento tangencial sigue sin respaldar ninguna cifra, y las cantidades inventadas («dos partidos», «€») se siguen detectando aparte. Residual conocido: un número inventado que coincida con uno de esos metadatos quedaría respaldado.
**Caso `circular10_sin_evidencia_circular9`:** acepta reconocer que el contenido de la Circular 9 no consta, y que se añada que la Circular 10 la anula y sustituye (el informe lo describe así). La respuesta observada («la Circular 10 establece que la Circular 9 queda sin efecto», sin decir que su contenido no consta) **sigue fallando por la capa de respuesta**, pero ya no se marca como invención crítica.
**Sin cambios:** el fallo de recuperación de `regla_duracion_mitad` (las consultas del modelo no devolvieron el fragmento esperado) es el RAG léxico, fuera de alcance. Tests: 3 nuevos en `tests/test_agent_rules_scope.py`.

## Medición final de la Fase 17 (live, 2026-10-26) — referencia de cierre
**Procedencia.** Cifras tomadas del `report.md` de la ejecución (generado en el equipo del usuario y adjuntado a la sesión; no está en el repositorio porque `data/` se ignora en Git) y del encargo de cierre. Esta es la única referencia de la medición final: **no se mezcla con las pasadas parciales anteriores**, que quedan arriba como histórico de diagnóstico.

### Identificación
| | |
|---|---|
| Ejecución | `20261026-212906-live-all`, 2026-10-26 (informe generado a las 21:40 UTC; unos 11 minutos) |
| Commit | `ac48edc` (Fase 17.7); el informe lo marca «con cambios sin commit» en la máquina del usuario, cuyo estado exacto no se puede verificar desde el repositorio (aquí el árbol está limpio en `ac48edc`) |
| Suite | `all-72-b32e31cb2b`: 72 casos, 65 evaluables, 7 no evaluables |
| Proveedor / modelo | gemini / `gemini-3.5-flash-lite` |
| Base | misma que la API (demostrado): 182 partidos, 18 actas, huella `75a2c6ab117fc8e2` |
| Prefijo fijo por llamada | sistema 2.800 · herramientas 6.462 · contexto 1.426 caracteres (10.688) |
| Línea de comandos | no consta en el informe (tope de peticiones y salida no verificables) |

### Resultados
**53 aprobados de 65 evaluables (81,5 %) · 12 fallidos · 7 no evaluables · 0 sin ejecutar.**
| Categoría | Casos | Pass | Fail | No evaluables | Aprobación (evaluables) |
|---|---|---|---|---|---|
| A. Resultados y calendario | 15 | 12 | 2 | 1 | 12/14 |
| B. Clasificaciones oficiales | 10 | 7 | 0 | 3 | 7/7 |
| C. Goleadores oficiales | 9 | 6 | 1 | 2 | 6/7 |
| D. Actas y eventos | 14 | 13 | 0 | 1 | 13/13 |
| E. Reglamento y RAG | 20 | 13 | 7 | 0 | 13/20 |
| F. Consultas combinadas | 3 | 1 | 2 | 0 | 1/3 |
| G. Fuera de alcance | 1 | 1 | 0 | 0 | 1/1 |
Capas (OK/fallo): datos 60/5 · herramientas 60/5 · respuesta 57/8 · invención 65/0 · advertencia 64/1 · presentación 65/0. RAG: recuperación correcta 16/20, citas validadas 20/20. Herramientas: 106 llamadas en 65 casos (media 1,63, máximo 5), 0 repetidas, 0 con error, 3 casos sobre el límite, 2 con herramienta no permitida. Errores críticos marcados por el evaluador: **1** (`tabla_goleadores_por_jornada_no_verificable`, ver abajo).

### Consumo, coste y duración
172 llamadas al LLM (0 reintentos) · 701.998 tokens de entrada reales (94.522 servidos desde caché) · 8.831 de salida · latencia media 2.964 ms por caso (máxima 9.167 ms, sin pausas de ritmo; pausas de ritmo 495,6 s). **Coste reportado: 0,232677 USD**, calculado con 0,30 USD y 2,50 USD por millón de tokens de entrada y salida, **precios aportados por el usuario a partir de fuentes de terceros y NO verificados en la página oficial de Google** (no se pudo abrir desde el entorno de desarrollo); no es una tarifa oficial. El cálculo tampoco descuenta los tokens en caché.

### Los 12 fallos, por causa
| Causa | Caso(s) | Evidencia de esta ejecución |
|---|---|---|
| Recuento incorrecto presentado con seguridad | `recuento_global` | Respondió «22 jugados y 160 pendientes» (verdad: 23 y 159) tras una sola llamada a `list_matches` sin filtrar. El evaluador no lo marca crítico (no valida recuentos derivados); falla por datos y respuesta |
| Intento de reconstrucción desde actas y límite agotado | `tabla_goleadores_por_jornada_no_verificable` | Tras `get_scorer_table` llamó tres veces a `top_scorers` (actas) por jornadas y agotó las 5 llamadas; marcado **crítico** por fuente incorrecta. El usuario solo recibió el mensaje de límite, sin datos |
| Exceso de llamadas y límite agotado | `combinada_lider_ultimo_resultado` | 5 llamadas (clasificación, `find_teams` con el nombre de la competición, tres `list_matches`) y mensaje de límite |
| Partido desactualizado en la base | `combinada_lider_proximo_partido` | Respondió la jornada 6 (1 de noviembre) en lugar de la jornada 5 (24 de octubre): ese partido sigue «programado» en la base aunque su fecha ya pasó, así que la verdad depende de datos sin actualizar |
| Discrepancia del evaluador | `resultados_otro_grupo` | Respuesta correcta (3-1) con una llamada adicional a `get_match_report`; el criterio actual permite `list_matches`, `get_match`, `list_teams` y las herramientas de descubrimiento, pero no `get_match_report` |
| Recuperación incompleta o fuente inadecuada del RAG | `regla_roja_mano`, `circular10_saque_meta_coloquial`, `circular10_video_solicitudes`, `circular10_penal_no_acumulable`, `circular10_distingue_fifa` | La búsqueda no devolvió el fragmento esperado (regla, página y texto); `circular10_video_solicitudes` respondió con el texto FIFA en lugar de la Circular 10 |
| Respuesta sin reconocer que el contenido no consta | `circular10_sin_evidencia_circular9` | Afirmó que la Circular 9 «ha quedado sin efecto» (anulada por la Circular 10). El evaluador no la marca como invención (invención 65/0, cita de la página 1 validada): la vigencia sale del fragmento recuperado; lo que falla es que no reconoce que el contenido de la Circular 9 no consta |
| Falta de consulta antes de «no consta» | `regla_sin_evidencia_salario` | Respondió «No dispongo de ese dato» sin llamar a `search_rules` (la pregunta, «¿cuánto cobra un árbitro…?», no usa vocabulario de reglamento que active la reconsulta) |

### No evaluables (7)
`acta_discrepancia_resultado`, `equipo_ambiguo`, `tabla_clasificacion_jornada_pasada`, `tabla_clasificacion_no_ingerida`, `tabla_clasificacion_incompleta`, `tabla_goleadores_no_ingerida`, `tabla_goleadores_nombre_repetido`: precondiciones ausentes en la base del usuario (sin discrepancias, sin nombres repetidos, una sola jornada de clasificación ingerida, ningún grupo en esos estados). Los estados se cubren con la base sembrada de los tests de integración, no con el modelo real.

### Limitaciones de la medición
- **Una sola ejecución:** cada caso se ejecutó una vez, sin repeticiones ni estimación de varianza. No se calcula ni se presenta ningún intervalo de confianza.
- **Variabilidad entre ejecuciones:** en las pasadas parciales previas, casos concretos cambiaron de resultado sin tocar el código (p. ej. `regla_duracion_mitad` falló o pasó según las consultas que formuló el modelo). La cifra global debe leerse como la de esta ejecución, no como una tasa estable del agente.
- **Siete casos no evaluables** que solo se cubren con fixtures; los casos que dependen de datos de competición cambian de verdad cuando se actualiza la base.
- Coste con tarifa no verificada oficialmente; commit marcado «con cambios sin commit» en el informe; criterios de respuesta léxicos que acotan pero no descartan la invención; un solo modelo (`gemini-3.5-flash-lite`).

### Siguientes pasos recomendados (ordenados e independientes; ninguno iniciado)
1. **Criterio del evaluador para `resultados_otro_grupo`.** El criterio actual **no** permite `get_match_report`; valorar permitirla como lectura de solo lectura equivalente (comprobar el acta), con test. Solo evaluador.
2. **Recuentos y reconstrucción desde actas (agente).** Contar con `list_matches` filtrado y `count` (no con una lista sin filtrar) y no reconstruir con actas un dato oficial que la tabla no ofrece (goleadores por jornada). Es lo único de esta lista con cifras erróneas o intentos de reconstrucción.
3. **Descubrimiento de equipos y eficiencia de consultas (agente).** No adivinar IDs de equipo (usar `find_teams`), elegir el último partido de una lista ya filtrada y vocabulario de reglamento más amplio para la reconsulta de `search_rules`.
4. **Fase independiente para la recuperación léxica del RAG** (5 fallos de recuperación; la de mayor coste y riesgo de regresión).
5. **Actualización controlada de los datos de competición** (resultados jugados desde la última ingesta; requiere peticiones web y decisión del usuario), seguida de una nueva evaluación cuando proceda: cambiará la verdad de los casos de próximo y último partido.

## Integridad de recuentos y datos oficiales (Fase 17.8)
Cambio posterior a la medición final (no la modifica ni la repite: no se ha repetido la evaluación live desde `102db50`). Atiende dos fallos de comportamiento observados en ella.
| Problema | Causa raíz (comprobada en la traza de la medición) | Cambio |
|---|---|---|
| `recuento_global`: «22 jugados y 160 pendientes» (verdad: 23 y 159) | Una sola llamada `list_matches(group_id)` sin filtrar. La API no pagina y devolvió los 182 partidos (70 KB); el agente recorta las listas a 40 filas (`truncated`, total 182) y el modelo contó los finalizados entre esas 40 (22) y extrapoló. `list_matches` ya admitía `status` y `limit`, y `count` es el total exacto de coincidencias (con `limit=1` basta), así que no se añade herramienta | La pista de lista recortada dice ahora «lista INCOMPLETA: no cuentes sus filas ni deduzcas totales… para contar usa `limit=1` (`count` es el total exacto)»; el prompt añade «Para contar usa `limit=1` y lee `count` (total exacto; jugados = `status finished`, por jugar = total sin filtro − jugados): nunca cuentes las filas de una lista recortada» (+167 caracteres, ≈ 48 tokens por llamada) |
| `tabla_goleadores_por_jornada_no_verificable`: tras la tabla oficial llamó 3 veces a `top_scorers` (actas) por jornadas y agotó el límite; el usuario no recibió ni el dato oficial | Nada en el contrato decía que la tabla oficial es solo la actual (la RFFM no publica histórico ni desglose por jornada); y el mensaje de límite descartaba lo ya obtenido | `SCORERS_NOTE` (va en cada resultado de `get_scorer_table`) y la descripción de la tool declaran «tabla ACTUAL, sin desglose por jornada; no la reconstruyas con `top_scorers`; si piden una jornada pasada di que no consta y ofrece esta tabla con su captura». Al agotar el límite, el mensaje conserva, copiado de lo que devolvieron `get_scorer_table` / `get_standings` (nunca del texto del modelo), el máximo goleador con sus empates y el líder, con fecha de captura, y dice que el resto no se ha podido verificar. Sin datos oficiales, el mensaje es el de siempre (sin cifras) |
**Qué no cambia:** `top_scorers` (actas) sigue disponible sin restricciones; el fallo del límite se sigue registrando (`error`, evaluación) y el caso seguirá fallando si agota el límite; evaluador, RAG, ingestas, PostgreSQL y datos intactos. Archivos: `agent/agent.py`, `repositories/table_queries.py` (solo el texto de la nota), `mcp_server/server.py` (solo la descripción de `get_scorer_table`), `tests/test_agent_counts_official.py` (9 tests).
**Limitaciones:** la corrección de ambos casos depende de que el modelo siga la pista; el código solo garantiza que la pista está donde el modelo la ve y que el límite no pierde lo oficial ya obtenido. No hay un tope de llamadas repetidas a `top_scorers` (eficiencia general, pendiente). El agente no verifica los totales que da: si ignora `count`, un recuento erróneo sigue siendo posible. Pendiente de validar con una ejecución live acotada: `recuento_global` y `tabla_goleadores_por_jornada_no_verificable` (≈ 8 llamadas).
