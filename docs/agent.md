# Fase 7 — Agente controlado con MCP (experimental)

## Objetivo
Demostrar el ciclo de *tool calling*: pregunta → LLM elige tool y argumentos → MCP → API → PostgreSQL
→ resultado al LLM → respuesta. No es un agente de producción (sin memoria, RAG ni planificación).

## Arquitectura
```
Agent (src/futsal/agent/agent.py)
 ├── LLMClient  (llm.py: Protocol + tipos neutrales; adaptadores AnthropicLLM y GeminiLLM sobre httpx;
 │               `create_llm()` elige según `LLM_PROVIDER`)
 └── McpClient  (mcp_client.py: sesión MCP; descubre y ejecuta tools)
```
LLM y MCP están desacoplados: cambiar de proveedor = nuevo adaptador de `LLMClient`.
Las 6 tools se descubren desde MCP (`list_tools`); el agente no las duplica.

## Ciclo
1. `list_tools` (MCP) → se pasan al LLM como definiciones (`name`, `description`, `input_schema`).
2. Se envía la pregunta con un system prompt corto (`SYSTEM_PROMPT`).
3. Si el LLM devuelve tool calls → se ejecutan por MCP y los resultados (también errores, `is_error`)
   vuelven al LLM; se repite.
4. Sin tool calls → respuesta final.
`MAX_TOOL_CALLS` (def. 5): si la siguiente ronda lo superaría, se detiene sin ejecutar más tools y
`AgentResult.error` indica el límite.

## Arranque
```
docker compose up -d --wait db && alembic upgrade head
uvicorn futsal.api.app:app_factory --factory --port 8000
export ANTHROPIC_API_KEY=...            # o en .env (ignorado por Git)
python -m futsal.agent "¿Cómo quedó el partido 2?"
```
El agente lanza el MCP (`python -m futsal.mcp_server.server`, stdio) como subproceso.

## Configuración (entorno o `.env`)
`LLM_PROVIDER` (`anthropic` por defecto | `gemini`), `ANTHROPIC_API_KEY` (si anthropic), `LLM_MODEL` (def. `claude-haiku-4-5-20251001`, barato),
`ANTHROPIC_BASE_URL`, `MAX_TOOL_CALLS`, `API_BASE_URL`. La clave nunca se imprime ni se traza.

### Gemini
```
LLM_PROVIDER=gemini
GEMINI_API_KEY=<configurar localmente>   # en el entorno o en .env (ignorado por Git)
GEMINI_MODEL=<modelo>                    # def. gemini-3.5-flash-lite (económico, con function calling)
```
`GeminiLLM` usa la REST `generateContent` (httpx, sin SDK nuevo); la clave viaja solo en la cabecera
`x-goog-api-key` (nunca en URL) y se oculta en mensajes de error. Convierte `input_schema` a
`functionDeclarations` (quita claves no soportadas), tool calls a `functionCall` y resultados a
`functionResponse`; Gemini no da ids de llamada, se generan como `<nombre>:<n>`.

## Preguntas de prueba
"¿Cómo quedó el partido 2?" · "¿Qué partidos terminados hay en el grupo 1?" ·
"¿Qué equipos hay en el grupo 1?" · "¿Qué partidos juega PARQUE NORTE F.S.?" ·
"¿Qué partidos hay en la jornada 2 del grupo 1?" · "¿Qué puedes hacer?" · "¿Cómo quedó el partido 999999?"

## Ejemplo de traza (formato real del agente; resultados MCP reales, LLM simulado)
```
USER
¿Qué partidos terminados hay en el grupo 1?

LLM
→ tool: list_matches
→ arguments: {"group_id": 1, "status": "finished"}

MCP
→ 13 matches

LLM
→ final answer
```
Error: `MCP → ERROR Error executing tool get_match: error_no_encontrado: partido 999999 no existe`.

## Tests
`python -m pytest tests/test_agent.py -q`: LLM simulado (guion) + servidor MCP real en memoria con API
simulada. Incluye `GeminiLLM` con HTTP simulado (config, clave ausente, no filtrar clave, tools, tool call, resultado, respuesta final). Cubre descubrimiento, ejecución, retorno al LLM, varias tools, fin sin tools, límite, error
MCP y formato del adaptador Anthropic.

## Limitaciones
- Prueba real con LLM **no ejecutada** en la Fase 7 por falta de clave (ver `PROJECT_CONTEXT.md`).
- Dos adaptadores (Anthropic, Gemini; Gemini sin prueba real aún); sin streaming, memoria ni conversación multi-turno.
- Los IDs son internos: preguntas por nombre requieren que el modelo navegue las tools.
- Tool calls de una ronda se ejecutan en serie; sin reintentos ante fallos del LLM.

## Gemini 3 y `thoughtSignature`
Los modelos Gemini 3 devuelven un `thoughtSignature` junto a cada `functionCall`; hay que reenviarlo tal cual en el
mensaje del modelo del turno siguiente o la API responde HTTP 400. `ToolCall.signature` lo conserva y
`_gemini_contents` lo devuelve. Anthropic lo ignora.

## Guardarraíles de alcance y ahorro de tokens
**Alcance.** El prompt limita al agente a la competición (competiciones, grupos, jornadas, equipos, partidos, resultados),
le ordena ignorar peticiones de "olvidar instrucciones" y le da un mensaje fijo (`OFF_TOPIC_MESSAGE`) para todo lo demás.
Además hay un control determinista en `Agent.run`: si el modelo contesta **sin haber llamado a ninguna tool** y su texto
no es ese mensaje, la respuesta se sustituye por `OFF_TOPIC_MESSAGE` y se marca `AgentResult.guardrail`
(`sin_datos_de_tools`). Así un chiste o un "1+1" no pasan aunque el modelo obedezca al usuario. Límite: si el modelo
llama a una tool y luego desvía la respuesta, solo lo frena el prompt.
Si lo pedido no lo cubren las tools (p. ej. goleadores), debe decirlo en vez de afirmar que el dato no existe.

**Tokens de entrada.** Se reenvían en cada vuelta el prompt, las 6 definiciones de tools (~800 tokens) y todos los
resultados anteriores; lo que más pesa son los resultados. Medidas (estimación ~3,5 car./token, API real):

| Escenario | Antes | Ahora |
|---|---|---|
| "resultados de la jornada 2" (descubrir IDs + `list_matches`) | ~9.000 | ~2.900 |
| `list_matches` sin filtro (182 partidos) | ~22.800 | ~4.800 |

Cambios: (1) `compact_result`: JSON compacto, sin `group_id`/`home_team_id`/`away_team_id`/`external_id`
y listas recortadas a `MAX_RESULT_ROWS` (def. 40) con `truncated` y el `count` total; (2) contexto previo: al empezar,
el agente consulta por MCP (sin LLM) competición, grupo y `jornada=round_id` y lo añade al system prompt, de modo que
no gasta 3 vueltas en descubrir IDs (esas consultas no cuentan para `MAX_TOOL_CALLS`); (3) el prompt pide filtros.

## Fase 8 — Evaluación, trazabilidad y tokens

### Cómo se contaban los tokens (y por qué 19.883 no era una llamada)
`GeminiLLM.usage` acumulaba con `+=` el `promptTokenCount` de **todas** las llamadas (y de todas las preguntas del
proceso) y la CLI lo imprimía una sola vez al final. Además el agente reenvía en cada vuelta prompt, tools y todo el
historial. Medido con la API real (estimación ~3,5 car./token, sin tokenizer; el LLM va guionizado):
`list_matches` sin filtro devolvía 64.180 caracteres (182 partidos) ≈ 18-21k tokens; la pregunta "resultados y próximos
partidos del DOSA" con el código de antes (`67ff2f0`) son 2 llamadas de ~0,9k + ~21,5k ≈ **22.400** estimados.
Compatible con los 19.883 observados, pero **hipótesis**: aquella ejecución no dejó traza. Demostrado por código:
la cifra era acumulada, el historial se reenvía, no existía filtro por equipo. No medible sin clave: tokens reales de
Gemini y `thoughtsTokenCount` (razonamiento, que no se leía y se factura como salida).

### Traza (`src/futsal/agent/trace.py`)
Cada ejecución añade una línea JSON a `data/agent/traces.jsonl` (`AGENT_TRACE_FILE`, `--trace-file`; vacío la desactiva):
`run_id`, pregunta, proveedor/modelo, `llm_calls[]` (tokens in/out/thinking **de esa llamada**, latencia, tools pedidas,
error), `tool_calls[]` (nombre, argumentos, duración, `is_error`, caracteres devueltos/enviados al LLM, resumen,
`truncated`), `context_ms`, `latency_ms`, respuesta, `guardrail`, `error` y `totals`. Los totales suman cada llamada
**una vez** (el `input` de la llamada N ya incluye el historial reenviado; por eso crece) e incluyen
`peak_input_tokens`. Si el proveedor no informa uso se anota `calls_without_usage`; no se inventa. Los resultados de las
tools solo se guardan con `--trace-results`. Los secretos del entorno se enmascaran. No hay reintentos de LLM ni de MCP.
La CLI imprime ahora tokens por llamada y el total de la pregunta; el acumulado de la sesión sigue visible, rotulado.

### Optimizaciones (justificadas por la medición)
1. `list_matches` admite `team_id` (parámetro del tool existente, no una tool nueva): sin él el agente debía traer los
   182 partidos y, desde el recorte a 40 filas, perdía los de las últimas jornadas. Los equipos (`team_id=nombre`) van en
   el contexto previo (~350 tokens) y evitan `list_teams`.
2. La API devolvía `date: null` para 152 de 182 partidos (sin hora publicada); ahora cae a `scheduled_date`. Lo detectó
   la evaluación: sin fecha el agente no podía decir cuándo se juega un partido.
3. El prompt manda filtrar siempre por `team_id`/`round_id`/`status`.

### Evaluación (`python -m futsal.agent.evaluate`)
22 casos (15 de la Fase 8, menos 2 retirados, más 9 de actas) con verdad calculada en el momento por consultas **solo lectura** a PostgreSQL (nada hardcodeado). Aserciones
estructuradas (marcador, fecha en varios formatos, recuentos, rival, campo, rechazo); la presentación se puntúa aparte.
| Caso | Qué comprueba | Tools razonables |
|---|---|---|
| equipo_resultados | resultados finalizados del DOSA | list_matches(team_id, finished) |
| equipo_proximo_partido | rival y fecha del próximo | list_matches(team_id, scheduled) |
| equipo_recuento | jugados y pendientes del DOSA | list_matches(team_id) |
| equipo_jornada_5 | rival y fecha en la jornada 5 | list_matches(team_id, round_id) |
| enfrentamiento_ganador | marcador de un partido concreto | list_matches(team_id, finished) |
| proximo_campo | campo del próximo (2 tools) | list_matches + get_match |
| jornada_2_resultados | todos los marcadores de la jornada 2 | list_matches(round_id) |
| partido_mas_goles | agregación sobre finalizados | list_matches(finished) |
| recuento_global | jugados / pendientes del grupo | list_matches ×2 |
| equipos_del_grupo | nº y nombres de los equipos | list_teams |
| acta_goleadores_ultimo | goleadores del DOSA en su último partido | list_matches → get_match_report |
| acta_partido_sin_acta | partido no jugado: debe decir que no hay acta | list_matches → get_match_report |
| acta_arbitro | árbitros de un partido con acta | list_matches → get_match_report(officials) |
| acta_jugador_inexistente | jugador que no consta: no inventar | find_players |
| acta_jornada_sin_resultados | jornada sin partidos jugados: cobertura vacía | top_scorers(round_id) |
| acta_goles_agrupados | jugadores con 2+ goles en una jornada (agrupado en SQL) | top_scorers(round_id, min_goals=2) |
| acta_goleadores_contra_equipo | quién marcó contra el DOSA | top_scorers(against_team_id) |
| acta_discrepancia_resultado | marcador previo del listado vs acta (solo si hay datos reales/fixture) | list_matches → get_match_report |
| acta_dato_no_almacenado | resultado por periodos: no figura en la fuente | — (live) |
| equipo_inexistente, jornada_inexistente | debe decir que no existe, sin inventar | — (live) |
| fuera_de_alcance | chiste: respuesta = aviso de alcance | — (guardarraíl) |
**Offline** (por defecto, sin LLM): un LLM guionizado recorre la ruta de tools razonable por el MCP/API reales y se
comprueba que la **evidencia** que recibiría el modelo (tras compactar/recortar) contiene las cifras correctas; tokens
estimados. No evalúa texto del modelo: esos casos salen `not_evaluable`, nunca aprobados. **Live** (`--mode live
--confirm-live`): LLM real, puntúa respuestas y exige evidencia. Un caso sin datos suficientes se marca `not_evaluable`.
Resultado guardado en `data/agent/eval-last.json`.


### Fase 9 — actas (goleadores, árbitros, eventos)
Nuevas tools MCP (ver `docs/mcp.md`): `get_match_report`, `list_match_events`, `top_scorers`, `find_players` y el filtro
`has_report` de `list_matches`. Cálculos (agrupar goles, cobertura, coherencia con el marcador) en SQL/Python
deterministas; el LLM solo elige tool y redacta. El prompt le exige avisar si no hay acta o `coverage.complete=false`.
**Cambios en la evaluación (declarados, no maquillaje):** `goleadores_no_disponible` y `arbitro_no_disponible` se retiran
porque su premisa ("ninguna tool expone goleadores/oficiales") ya no es cierta; ambos eran `not_evaluable` offline, así que
no afectan a ningún pass/fail. Se sustituyen por casos con verdad en BD (`acta_goleadores_ultimo`, `acta_arbitro`,
`acta_goles_agrupados`...) y por `acta_dato_no_almacenado` (periodos). Los otros 13 casos no se han tocado.
La verdad de actas sale de SQL propio de solo lectura en `evaluate.py` (independiente de `report_queries.py`).
**Coste (offline, tokens estimados, mismos 11 casos comunes con la BD de snapshots):** 45 719 → 67 396 tokens de entrada
(+47 %, ≈ +890 por llamada al LLM): el coste fijo son los esquemas de las 4 tools nuevas y 4 frases de prompt. Mitigado quitando
los `title` autogenerados del JSON Schema (`strip_titles`, −1 000 caracteres). Número de llamadas LLM y de tools de esos casos
sin cambios (23 llamadas LLM y 12 de tools en ambas mediciones). Con Gemini real no se ha medido (sin clave en el entorno de desarrollo).
Pruebas: unitarias `tests/test_agent_eval.py`, `tests/test_mcp_server.py`; integración `tests/integration/test_report_queries_pg.py`,
`test_agent_eval_pg.py` (BD temporal con el acta fixture, sin LLM); en vivo `tests/live/test_gemini_live.py`
(`RUN_LIVE_LLM=1`, comprueba exactitud, tools invocadas y evidencia).

## Fase 10 — Fiabilidad, evaluación real y observabilidad
Procedimiento para ejecutar la validación con Gemini desde tu equipo: `docs/evaluation-online.md`. Estado: implementación y
validación **offline** completadas; validación **online pendiente** (requiere tu clave, tu PostgreSQL y compartir el informe).

### Reintentos controlados (`llm.py`)
`RetryPolicy` + `post_with_retries` (Gemini y Anthropic). Solo errores **transitorios**: 429, 5xx y fallos de red/timeout.
Nunca 400/401/403/404 ni respuestas inválidas (200 con JSON roto → `LLMError`, 1 intento). Máximo `LLM_MAX_RETRIES` (por
defecto **2**, tope 5; `0` desactiva; construir el cliente a mano = sin reintentos). Espera exponencial 1 s, 2 s…, o `Retry-After`
(cabecera, o `retryDelay` del cuerpo de error de Gemini), siempre ≤ `LLM_RETRY_MAX_WAIT` (20 s): si el servidor pide más, **no se
reintenta**. Cada intento queda en `LlmCallRecord.attempts` (`number`, `status`, `error`, `waited_s`) junto con `retries` y
`retry_wait_ms`; `totals` suma `llm_retries`. Una llamada lógica = 1 + reintentos peticiones HTTP: así se mide su impacto en cuota.

### Trazas: real, estimado o no informado
Cada llamada lleva `token_source`: `provider` (lo informa Gemini: `promptTokenCount`, `candidatesTokenCount`,
`thoughtsTokenCount`, `cachedContentTokenCount`), `estimated` (LLM guionizado del modo offline, ~3,5 car./token sobre el
**formato real** que recibe Gemini) o `unreported` (el proveedor no informó: aparece como "n/d", no como 0). `totals` suma cada
llamada una vez y añade `token_source` (`provider|estimated|mixed|unreported`), `cached_input_tokens` y `llm_retries`.

### Evaluación en tres capas + invención (`evaluate.py`, `criteria.py`)
29 casos (los 22 de las fases 8-9 intactos + 7 nuevos). Veredictos **independientes** por caso:
1. **Datos**: las tools devolvieron la cifra correcta (verdad = `SELECT` de solo lectura a PostgreSQL).
2. **Herramientas**: obligatorias usadas, ninguna ajena, sin llamadas repetidas ni errores, dentro del máximo; un caso de datos
   cuyo texto fue sustituido por el guardarraíl cuenta como fallo; fuera de alcance no debe usar tools.
3. **Respuesta** (solo con texto real del modelo o rechazo determinista): hechos esperados, y por separado
   **invención** (nombra un jugador de la base que las tools no devolvieron; da marcador de un partido no jugado o inexistente;
   afirma colores de tarjeta; da minutos de cambios), **advertencia** (si la evidencia es parcial, el acta falta o los marcadores
   se contradicen, la respuesta debe avisar *de ese motivo concreto*) y **presentación** (informativa).
Un caso sin datos suficientes en la base se marca `not_evaluable` (con el motivo) y nunca pasa por defecto. Casos nuevos:
`partido_futuro_resultado`, `acta_finalizado_sin_acta` (≠ no jugado), `acta_jugador_ambiguo`, `equipo_ambiguo`,
`acta_tarjetas_codigo`, `cobertura_parcial_advertencia`, `acta_sustituciones_minuto`. Suite `smoke` (5 casos) para la primera prueba.
Los criterios son léxicos y deterministas: acotan la invención, no la eliminan; por eso el informe incluye cada respuesta.

### Seguridad de la evaluación online
`preflight.py` demuestra por huella (partidos: id, estado, marcador; y nº de actas) que la API y la evaluación leen la misma base;
si no se demuestra, `evaluate --mode live` **se bloquea** (`--allow-unverified-db` lo fuerza, no recomendado). `--plan` nunca
construye un cliente LLM real (prueba automática). `--max-requests` es un tope duro de peticiones (llamadas + reintentos).

### Línea base offline (réplica del contenedor de desarrollo, NO tu base)
Mismo método (LLM guionizado, tokens estimados con el esquema MCP crudo), 19 casos comunes evaluables: Fase 9 → Fase 10 pasa
de 2.788 a 2.916 tokens de entrada por llamada (+4,6 %) por las reglas añadidas al prompt, con 19/19 casos en ambas y las mismas
43 llamadas. Coste fijo por llamada medido: prompt ~565 + contexto ~262 + 10 tools ~1.550 ≈ 2.380 tokens (~80 % de la entrada).
No se recortaron herramientas ni descripciones sin una prueba online que confirme que Gemini mantiene la calidad.

## Fase 11: consumo de tokens
Resumen y comparativa en `docs/phase11-report.md`. Cambios que afectan al uso: `list_matches` admite `limit` (devuelve `count` = total y
`shown` = filas devueltas; para contar basta `limit=1`); los resultados enviados al LLM no incluyen `round_id` ni valores nulos;
el esquema de herramientas se envía sin uniones `null` ni sangrías (`slim_tools`); la traza y el informe incluyen el tamaño del
prefijo fijo por llamada y la latencia se mide sin las pausas de `--min-interval`.

## Fase 13: evaluación continua y observabilidad (todo offline por defecto)
Reutiliza el evaluador, `results.json`/`report.md` y las trazas existentes; añade identidad de ejecución, estado global, comparación con
veredicto y listado de ejecuciones. **Nada de esto llama a ningún LLM**: solo lee `results.json`.

**Identidad de cada ejecución** (`meta` de `results.json` y cabecera de `report.md`): `run_id`, fecha, commit (+ `commit_dirty` si hay cambios
sin commit), `suite_version` (nombre, nº de casos y huella de sus ids), modo, proveedor/modelo (sin credenciales), casos
pass/fail/no evaluables/sin ejecutar y **estado global**: `SUPERADA`, `CON FALLOS`, `INCOMPLETA` (hay casos sin ejecutar: no es concluyente) o
`SIN CASOS EVALUABLES`. Los informes anteriores a la Fase 13 se leen igual; sus campos nuevos aparecen como «n/d».

**Cómo se agregan las métricas.** Los tokens son los de cada llamada al LLM (no acumulados); el caso suma sus llamadas y la suite suma los casos
evaluados y los cortados por el proveedor, cada llamada una sola vez. Las pausas de `--min-interval` no cuentan como latencia. Los tokens
estimados (offline) y los del proveedor no se mezclan ni se comparan. **No disponibles:** tokens de caché de escritura, coste monetario y
códigos de error por llamada (solo reintentos y casos cortados por el proveedor).

**Comparar con una referencia** (CMD):
```
python -m futsal.agent.evaluate --list-runs
python -m futsal.agent.evaluate --compare data\agent\eval\<ANTES>\results.json data\agent\eval\<DESPUES>\results.json
python -m futsal.agent.evaluate --mode offline --suite all --baseline data\agent\eval\<ANTES>\results.json
```
La comparación mantiene la tabla por caso y añade un **veredicto**: `SIN_REGRESIONES`, `ATENCION`, `REGRESION` o `NO_COMPARABLE`
(distinto modo, proveedor o modelo, o sin casos comunes: no se emite decisión automática), y «LISTA / NO LISTA PARA REVISIÓN». La calidad
es una condición independiente: **menos tokens nunca compensa un fallo**. `--baseline` guarda además `comparison.txt` en la carpeta de la ejecución.
Por defecto solo informa (código de salida 0); con `--fail-on-regression` sale con 1 si hay regresiones bloqueantes.

| Señal | Tipo | Umbral por defecto y justificación |
|---|---|---|
| pass → fail; caso evaluado → no evaluable/sin ejecutar; menos casos evaluables | bloqueante | ninguno: es pérdida de calidad o de cobertura |
| capa datos/herramientas/respuesta/invención/advertencia ✔ → ✘ (aunque el caso ya fallara) | bloqueante | ninguno; invención y advertencias son salvaguardas |
| errores de herramientas MCP nuevos | bloqueante | ninguno |
| tokens de entrada, llamadas LLM, llamadas MCP (casos comunes evaluados) | bloqueante | +15 %, +10 %, +15 %: es el ruido real entre ejecuciones del mismo código en la Fase 11 (54-61 llamadas, 145k-171k tokens). `--max-tokens-pct`, `--max-calls-pct` (negativo = solo informativo) |
| latencia | aviso | desactivado: varía con el proveedor (58-105 s con el mismo código). `--max-latency-pct` |
| reintentos adicionales; ejecuciones cortadas por el proveedor | aviso | +3 (`--max-retries-increase`) |
| capa presentación ✔ → ✘ | aviso | informativa |
| casos nuevos o ausentes, suite distinta | info/aviso | se comparan solo los casos comunes |

**Limitaciones:** la comparación de consumo solo es fiable entre ejecuciones del mismo modo, proveedor, modelo y origen de tokens; con una
ejecución por caso no se distinguen diferencias menores que el ruido anterior; la validación con un proveedor real depende de ejecuciones
`--mode live` que lanza el usuario.
