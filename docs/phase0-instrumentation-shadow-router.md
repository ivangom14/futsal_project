# Optimización de tokens — Fase 0: instrumentación y router en sombra

Alcance: **medir** y **observar**. El agente ejecuta exactamente el mismo flujo de siempre (`arch=legacy`, `exec_tier=tier2`). No hay
prompt nuevo, ni selección de herramientas, ni operaciones de alto nivel, ni Tier 0 activo, ni plan declarativo. Sin LLM real, sin red,
sin migraciones, sin cambios en PostgreSQL, MCP, frontend ni contratos de la API.

## 1. Qué se midió y con qué naturaleza de dato

| Naturaleza | Qué es | Dónde |
|---|---|---|
| `provider` | Lo que informa el proveedor: entrada total, salida, razonamiento, caché. **Lo único facturable.** | `LlmCallRecord.input_tokens…`, `totals()` |
| `estimated` | Reparto de la entrada por origen, estimado en local (`caracteres / 3,5`). El proveedor no da este desglose. | `LlmCallRecord.input_breakdown.est_tokens` |
| `chars` | Caracteres exactos de cada parte (base de la estimación). | `input_breakdown.chars` |

Orígenes: `system_prompt`, `history_rule`, `context` (catálogo), `tools` (esquemas), `history`, `question`, `scope_notes` (nota de ámbito/foco que
añade la aplicación), `assistant_turns`, `tool_results` (lo que realmente llega al LLM) y `other` (reconsultas del guardarraíl).
`calibration` = tokens reales / estimados de esa llamada, solo si el proveedor informó el total. Los esquemas se miden en formato canónico
(el cable de Gemini/Anthropic ocupa algo más). Cuando `Usage.estimated` es verdadero (LLM simulado) `provider_input` queda en `null`.

Además `totals()` añade `arch`, `exec_tier`, `mcp_calls` (`requested_by_llm`, `context_preload`, `refused`, `reused`, `errors`) y `token_breakdown`
(suma por origen). Latencia por llamada, total y reintentos ya existían. `RunRecord` gana `conversation_id`, `router_*` y `router_ms`.

**Precios:** `tokens.PRICES` está vacío a propósito. `estimate_cost` devuelve `None` sin un `Price` con `source` y `verified_on`, y nunca costea tokens
estimados.

## 2. Router en sombra (`agent/router.py`)

Función pura (`route`) sin E/S, sin LLM y sin herramientas; ~0,4 ms por pregunta. Reutiliza `extract_hints`/`merge_hints`/`candidates`/`scope_resolved`,
`criteria.mentions_rules/mentions_domain/norm` y el foco verificado. Registra intención (`standings, matches, scorers, teams_players, rules,
composite, ambiguous, unrecognized`), subintención, entidades (nombres, jornadas, referencias, erratas corregidas, ámbito), nivel que habría elegido,
confianza, motivo, aptitud Tier 0 y si habría hecho falta el sistema completo. **No resuelve equipos** (`find_teams` es E/S): los marca
`pending_find_teams`. Un fallo del router se aísla (`rec.router={"error":…}`); `AGENT_ROUTER=off` lo desactiva.

## 3. Evaluación independiente (`python -m futsal.agent.router_eval [--errors]`)

| | Dataset previo (`design_set.json`) | Dataset nuevo (`validation.json`) |
|---|---|---|
| Origen | 72 preguntas del evaluador (Fase 17); el router se diseñó mirándolas | 119 preguntas escritas después del diseño y no usadas para ajustarlo |
| Etiquetas | Derivadas de las herramientas esperadas (más débiles) | Por política documentada en el propio fichero |
| Evaluables | 56 | 115 (4 «no evaluables»: nombre de equipo ambiguo, solo verificable con `find_teams`) |
| Intención correcta | 98,2 % | 81,7 % |
| Nivel correcto | 94,6 % | 77,4 % |
| Ruta (intención y nivel) | 94,6 % | **74,8 %** |
| Alta confianza (n · precisión de ruta) | 41 · 95,1 % | 65 · **80,0 %** |
| Tier 0 propuesto (cobertura) | 66,1 % | **41,7 %** |
| Tier 0 correcto / precisión / exhaustividad | 35 · 94,6 % · 97,2 % | 39 · **81,2 %** · 78,0 % |
| Falsos positivos / falsos negativos de Tier 0 | 2 / 1 | 9 / 11 |
| Tier 0 propuesto donde debía ser Tier 2 | 0 | 0 |

El dataset previo está contaminado por diseño: sus cifras son un suelo de regresión, no evidencia. **La evidencia es la columna derecha, primera ejecución
sin ajustar** (`tests/fixtures/router/validation_first_run_result.json`).

**Criterios de aceptación del diseño (informados, no forzados):** precisión en alta confianza ≥ 95 % → **NO se alcanza** (80,0 %); cobertura de Tier 0 ≥ 40 % →
sí (41,7 %), pero con precisión de Tier 0 del 81,2 %: el 40 % se logra a costa de enviar a Tier 0 lo que no debía.

### Causas de los fallos en el dataset nuevo (a corregir antes de activar Tier 0)
1. **Subintenciones con filtro tratadas como simples** (FP): goleadores de un equipo, con umbral o por jornada; «a qué hora … el sábado»; «próximo partido y quién lo arbitra» (el verbo «arbitra» no es término de acta).
2. **Reglamento + dato de equipo** clasificado como solo reglamento: la regla «`jornada`/`partido` es un filtro» se aplica también cuando hay un equipo nombrado (`regl_16`, `comp_11`).
3. **Ausencia de vocabulario = «no reconocida» con confianza alta** (`regl_03`, `regl_11`): la falta de señales no es prueba de que sea ajena; debería ser confianza baja y Tier 2.
4. **Ámbito**: «Primera Autonómica Aficionado» no resuelve (la unión de alias de `extract_hints` da 3 competiciones: el agente real tiene el mismo comportamiento); equipo en minúsculas («dosa») no se detecta como entidad.
5. **Señales débiles** mal niveladas (`clas_03` «puntos», `clas_05` «va el último en la tabla»); «va primero» exige patrón exacto.
6. **Seguimiento**: la intención no se hereda del turno anterior (`seg_04`, `seg_08`); «su máximo goleador» no cuenta como referencia.
7. **Equipos/jugadores**: «juegan/juega» dispara partidos y compone con listados (`eq_01`, `eq_03`); consultas de jugador concreto caen en no reconocida.
8. Tres discrepancias son solo de taxonomía (`ambiguous` vs `unrecognized`) con nivel correcto (Tier 2).

## 4. Ejemplo de traza antes / después (mismo guion; `tests/fixtures/trace_before_phase0.jsonl` es la «antes»)

Campos nuevos en `RunRecord`: `arch="legacy"`, `exec_tier="tier2"`, `conversation_id`, `router_intent="standings"`, `router_tier="tier0"`,
`router_confidence="high"`, `router_reason="single_intent:standings/table;…"`, `router_ms`, `router{…}`. En cada `llm_calls[i]`: `input_breakdown`
(`chars`, `est_tokens`, `est_total`, `provider_input`, `calibration`). En `totals`: `mcp_calls`, `token_breakdown`. Respuesta, guardarraíl, error y todos los
totales previos: idénticos. `read_jsonl` lee trazas antiguas rellenando estos campos por defecto.

## 5. Pruebas
`tests/test_token_instrumentation.py` (13) y `tests/test_router_shadow.py` (27). Cinco escenarios del agente real (flujo con herramientas, reglamento, guardarraíl
de ajeno, reconsulta, seguimiento con historial) se ejecutan con el router activado y desactivado y comparan system, mensajes, esquemas, llamadas MCP,
respuesta, guardarraíl y traza de texto: idénticos, sin una llamada más al LLM ni a las herramientas.

## 6. Limitaciones
- Tokens por origen estimados (caracteres/3,5); sin tokenizador del proveedor ni `countTokens` (consumiría cuota). La calibración real/estimado solo existe con tokens del proveedor.
- Router sin `find_teams`: no puede saber si un nombre es ambiguo entre competiciones; Tier 0 para consultas de equipo exige que el ejecutor lo gestione.
- Etiquetas del dataset nuevo escritas por el mismo autor del router (correlación de criterio) y sin respuestas deportivas (solo enrutamiento).
- El dataset previo coincidía en 1 pregunta con el nuevo; se reformuló (sin cambiar decisiones).
- Sin PostgreSQL aquí: las pruebas de `tests/integration` no se han ejecutado.
