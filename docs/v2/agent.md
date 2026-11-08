# v2 · Agente, herramientas MCP y pasarela de modelos

## Objetivo

Que una pregunta típica se responda con **0 o 1 llamada al LLM**, que el ámbito (temporada, competición, grupo, equipo) lo
fije **código determinista**, y que añadir cientos de grupos no cambie ni el prompt ni las herramientas.

## Flujo v2 (tras `AGENT_ARCH=v2`; `legacy` sigue siendo el predeterminado hasta la aceptación)

```
pregunta + estado conversacional
   │
   ▼  resolve (determinista, API)            ← sustituye al catálogo en el prompt
   ├── necesita aclaración → pregunta con candidatos (sin LLM)
   ▼  router (intención por rasgos; LLM barato solo si la confianza es baja)
   ├── T0 plantilla de la lista blanca → respuesta compuesta en código (sin LLM)
   ├── T1 la app precarga 1–2 herramientas de alto nivel → 1 llamada (modelo `simple`)
   └── T2 bucle de herramientas v2 (≤ 4 llamadas, modelo `complex`)
   ▼  validador de respuesta (cifras, marcadores y nombres respaldados por la evidencia)
   ├── falla en T1 → UNA escalada a T2
   ▼  presentación (visuales) + conciliación → respuesta + estado actualizado
```

- **T0** reutiliza `agent/tier0.py` y la lista blanca adversarial (0 activaciones incorrectas en 134 casos [M]).
- **El resolutor** reutiliza `domain/resolver.py`, `agent/convstate.py`, `agent/intent.py` y `agent/context.py` (offline: 99–100 %
  en el banco de comprensión [M]). Pasa a ser obligatorio y a ejecutarse en la API (`POST /v2/resolve`), de modo que el MCP y
  otros clientes lo compartan.
- **Se conserva la regla anti-invención**: el agente solo ejecuta referencias (`ref`) que el resolutor o una herramienta le
  han mostrado en la conversación. Esto sustituye a la validación actual por IDs y hace innecesaria la reparación de `group_id`.
- El **validador de respuesta** es código de ejecución (`agent/validate.py`). La evaluación lo importa, no al revés:
  `criteria.py` deja de ser una dependencia del agente.

## Contratos de herramientas v2

Referencias opacas: `season:22`, `comp:7`, `group:45`, `team:123`, `club:9`, `match:555`, `player:77`. Se validan en el
servidor; nunca son IDs de la RFFM. Toda respuesta lleva `freshness` (fecha de la última captura válida) y `notes` (avisos:
dato parcial, no clasificado, tabla oficial frente a actas). Las listas llevan siempre `count` y `shown`.

| Herramienta | Entrada | Salida (resumen) | Sustituye a |
|---|---|---|---|
| `resolve` | `text`, `context{season?, focus_refs[]}` | `season`, `entities[{type, mention, status: resolved/ambiguous/not_found/unclassified, ref?, candidates≤5}]`, `scope`, `needs_clarification`, `clarification` | catálogo en el prompt, `find_teams`, `list_competitions`, `list_groups` |
| `team_overview` | `team`, `last_n=5` | equipo (club, categoría, género, competición, grupo, temporada), posición oficial, forma, último y próximo partido, goleadores del equipo en la tabla oficial | `get_standings` + `list_matches` + `get_scorer_table` |
| `group_overview` | `group`, `top?` | clasificación oficial, líder, jornada actual, próximos partidos, top 5 goleadores | idem |
| `club_overview` | `club`, `season?` | todos los equipos del club (A/B/C, categorías, género) con competición, grupo, posición y puntos | nuevo |
| `matches` | `scope`, `team?`, `opponent?`, `round?`, `date_from?`, `date_to?`, `status?`, `order`, `limit=10` | partidos con local/visitante, hora de Madrid, resultado, `has_report` | `list_matches`, `get_match`, `list_rounds` |
| `match_detail` | `match`, `include[score, events, lineups, officials, cards]` | acta normalizada | `get_match_report`, `list_match_events` |
| `scorers` | `scope`, `team?`, `player?`, `limit=10` | tabla oficial (y, si se pide un jugador, sus goles por partido desde las actas, marcado como tal) | `get_scorer_table`, `top_scorers`, `find_players` |
| `compare` | `teams[2..4]` | posición, forma y enfrentamientos directos | nuevo |
| `search_rules` | `query`, `issuer?`, `season?`, `limit=5` | fragmentos con cita (documento, versión, página y artículo) | `search_rules` |

Son 9 herramientas frente a las 15 actuales. Presupuesto de respuesta: ≤ 1.500 tokens en el caso típico. Esquemas sin
`title`, con descripciones de una línea. La v1 (15 herramientas) se mantiene hasta retirarla (paso R5).

**En el mismo proceso**: el agente v2 llama a un registro de herramientas compartido en el mismo proceso. El servidor MCP
publica ese mismo registro para clientes externos. Se elimina el salto stdio y el HTTP intermedio en el camino caliente; el
contrato es el mismo. Las herramientas de una misma respuesta se ejecutan en paralelo.

**Code Mode**: descartado por ahora. Para estadísticas libres (oleada posterior), `stats_query` con un DSL validado (filtros,
agregaciones y orden sobre vistas de solo lectura), nunca SQL ni código arbitrario.

## Pasarela de modelos (`agent/llm.py`, ampliado; sin dependencias nuevas)

| Tarea | Uso | Modelo inicial |
|---|---|---|
| `router` | intención cuando la confianza determinista es baja | el de `simple` |
| `interpreter` | reformulación con validación contra el catálogo (`interpreter.py`) | el de `simple` |
| `simple` | T1 | `gemini-3.5-flash-lite` (referencia) |
| `complex` | T2 y escalada | `gemini-3.5-flash-lite` hasta la comparativa |
| `judge` | solo evaluación (redacción), fijo entre comparaciones | a decidir en A5 |

- Configuración: `LLM_MODEL_<TAREA>=proveedor:modelo` (p. ej. `LLM_MODEL_SIMPLE=anthropic:claude-haiku-5-5`); sin variables, todo
  usa `LLM_PROVIDER`/`GEMINI_MODEL` como hoy.
- Adaptadores: Gemini (el actual, más `thinkingConfig` y caché explícita opcional), Anthropic (predeterminado
  `claude-haiku-5-5`, `cache_control` en system y herramientas, parámetro `effort`), **OpenAI nuevo** (llamada de funciones
  sobre HTTP con httpx, como los demás).
- Todos devuelven `Usage` normalizado (entrada, cacheados, salida y razonamiento) y los mismos errores (`LLMError`,
  `LLMQuotaError`).
- **Coste por llamada** en la traza a partir de `config/llm_prices.json` (precio, fecha y URL de la fuente). Sin tarifa
  registrada, el coste queda `null`; nunca se inventa.

## Concurrencia y estado

- Un `RunState` por petición sustituye a los atributos `self._teams`, `self._seen`… del `Agent` compartido. Se retira el
  `asyncio.Lock` global: varias preguntas a la vez, con un límite configurable y una cola.
- Estado conversacional (foco: temporada, competición, grupo, equipo y club; preguntas pendientes) persistido en PG
  (`conversations`, `conversation_turns`) en la oleada 4. El historial de texto solo aporta lenguaje; el ámbito siempre sale
  del estado validado. El servidor deja de aceptar el historial del navegador como verdad.

## Retirada de banderas y heurísticas (cada paso, en una entrega aparte y con la evaluación como puerta)

| Paso | Qué se retira | Sustituto | Puerta de aceptación |
|---|---|---|---|
| R1 | `AGENT_GROUP_ID_REPAIR` | referencias opacas | v2 sin errores de ámbito en los estratos «ambiguas» y «multicompetición» |
| R2 | `AGENT_TOOLS=dynamic`, `AGENT_PREFETCH` | precarga de T1 por intención | tokens por pregunta ≤ referencia × 0,6 y misma precisión |
| R3 | `AGENT_TIER0`, `AGENT_ROUTER` (sombra) | T0 y router reales en v2 | 0 críticos en T0; precisión de ruta ≥ 95 % en el holdout nuevo |
| R4 | `AGENT_CONTEXT`, `RECONSIDER_MESSAGE`, `RULES_RECONSIDER_MESSAGE`, `SCOPE_REUSE_MESSAGE`, `TOOLS_WIDENED_MESSAGE`, `DOMAIN_WORDS` en el runtime | resolutor y router (el dominio lo decide el catálogo, no palabras sueltas) | estratos «sin datos» y «adversariales» ≥ referencia |
| R5 | `AGENT_PROMPT_MODE`, prompt legacy, MCP v1, catálogo en el prompt, `AGENT_ARCH=legacy` | v2 | v2 ≥ referencia en **todos** los estratos (dentro del ruido medido), 0 críticos nuevos y coste ≤ referencia; después, borrado del código legacy en un PR aparte |

Se mantienen: `AGENT_TELEMETRY` (privacidad), `CHAT_RECONCILE` (interruptor de seguridad) y `CHAT_TIMEOUT_SECONDS`.
Durante la transición, la huella del prompt legacy (`prompt_legacy_golden.json`) sigue protegiendo la ruta legacy.

## Aceptación de la oleada 2

- Contratos v2 con pruebas de integración en PG desechable (homónimos, equipos A/B, competición sin clasificar, grupo sin
  clasificación oficial, temporada no actual).
- Agente v2 frente a la referencia en Eval v1 (mismo modelo): precisión ≥ en cada estrato, 0 críticos nuevos, ≥ 40 % menos
  tokens de entrada por pregunta y ≥ 30 % menos llamadas al LLM [objetivos, a medir].
- Prueba de concurrencia: 10 preguntas simultáneas con LLM simulado, sin mezcla de estado entre ellas.
