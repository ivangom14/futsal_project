# Comprensión conversacional de Futsito (`AGENT_CONTEXT=legacy|deterministic|llm`)

Estado: implementado y probado OFFLINE (LLM y MCP simulados). **No validado en vivo.** Por defecto `legacy` = el flujo de siempre, byte a byte (ver «Compatibilidad»).

## Problema (reproducido)
«¿Cómo va la liga preferente?» tras hablar del DOSA juvenil perdía la categoría (al nombrar una competición se descartaba la anterior), los guardarraíles dependían de listas de palabras
(`DOMAIN_WORDS` no tiene «liga», «preferente», «va», «arriba»…), lo heredado se aplicaba como FILTRO duro a `find_teams` («¿Cómo va el DOSA?» tras la Tercera → 0 equipos) y un
rechazo del modelo no tenía recuperación. Causas por capa: extractor/herencia (`merge_hints`), guardarraíles léxicos, estado no estructurado, router solo en sombra.

## Flujo (modos `deterministic` y `llm`)
```
mensaje + historial + estado ──► ContextEngine.understand  (resolver + intent + equipos + política)  ──► Understanding {status, intención, competiciones, grupos, equipo…}
                                   └─ solo modo llm y solo si hay duda: Interpreter (1 llamada LLM sin tools, salida validada contra el catálogo) ─► se vuelve a resolver
Understanding.note() ──► mensaje de usuario del agente (notas verificadas)  ──►  bucle de tools de siempre  ──► _ctx_guard (política y recuperación) ──► _finalize_context (estado)
```
| Pieza | Fichero | Qué hace |
|---|---|---|
| Resolutor por dimensiones | `domain/resolver.py` | Descompone cada competición del catálogo en tipo/nivel/categoría/fase/grupos; candidatas = INTERSECCIÓN de lo dicho; ajuste exacto («primera juvenil» ≠ «primera autonómica juvenil»); erratas; plurales; términos inexistentes y temporadas; `SeasonScope` listo para históricos |
| Vocabulario | `config/competition_dimensions.json` | Palabras vacías, competiciones/categorías que NO existen, referencias (otra/ese/su/ordinales), señales de temporada. Los alias coloquiales siguen en `competition_aliases.json` |
| Intención | `agent/intent.py` | Puntuación por rasgos léxicos con peso (no frases hechas); hereda la intención solo en seguimientos elípticos («¿Y la copa?»); es una PISTA, el LLM puede razonar otra cosa |
| Estado | `agent/convstate.py` | Competiciones, grupos, categoría, equipos, opciones ofrecidas, intención, pregunta pendiente, entidades mencionadas, suposiciones ya declaradas. Caché DERIVABLE: si no coincide con el último turno del historial se reconstruye |
| Motor y política | `agent/context.py` | `act` / `assume` (declara «Entiendo que te refieres a…» una vez) / `ask` (opciones reales, sin consultar) / `missing` / `nodata` / `none`; herencia solo como desempate; ≤3 grupos → todos, >3 → pregunta; ordinales sobre opciones ofrecidas; `rebuild` desde el historial |
| Intérprete | `agent/interpreter.py` | JSON estricto; todo se valida (competición/grupo/equipo inexistente → descartado); solo puede ELEGIR entre candidatas ya dichas; falla en abierto |
| Recuperación | `Agent._ctx_guard/_run_plan` | Rechazo erróneo → (1) revisión con la interpretación verificada y el plan, (2) la aplicación ejecuta las consultas por `_execute` y se las entrega, (3) datos verificados o mensaje honesto; nunca el aviso de alcance como salida por defecto |

Separación memoria / historial / estado: el historial (`memory.Turn`) es la fuente de verdad lingüística; el foco (`presentation.compute_focus`) lo que las tools consultaron; el estado lo identificado. El servidor lo
guarda (`ConversationMemory.put_state`) y, si no lo conoce, se reconstruye con el historial que reenvía el navegador (misma API y contrato de respuesta; el estado no sale al cliente).

## Decisiones aprobadas (cumplidas)
≤3 grupos → se consultan todos y se presentan por grupo; >3 → se pregunta salvo contexto/equipo que lo determine. Suposiciones declaradas una vez. Temporadas anteriores: no se inventan (hoy solo existe la actual).
El contexto nunca bloquea: `find_teams` solo se recorta con el ámbito RESUELTO en esta pregunta.

## Compatibilidad / aislamiento del A/B
Todo el código nuevo cuelga de `self._ctx_on`. En `legacy` la huella de TODO lo que ve el LLM (prompt, mensajes, herramientas) es idéntica a la de la rama antes de este trabajo (c80095a) en 359 turnos y 2 perfiles de
modelo simulado; `tests/test_understanding_eval.py` la comprueba contra `tests/fixtures/understanding/legacy_digests_c80095a.json`. No se ha tocado `arms.py`, `ab_arms.py`, `evaluate.py` ni los prompts. **No exportar
`AGENT_CONTEXT` al lanzar el A/B** (por defecto es `legacy`). Cambios en código compartido: `entities.scan` (bucle de `decide` extraído sin cambios; tests de entidades/holdout A intactos), `AnthropicLLM` omite `tools` si va vacío
(con tools, cuerpo idéntico), `RunRecord.context`, `AgentResult.state`, `ConversationMemory.put_state/get_state`.

## Evaluación (`python -m futsal.agent.understanding_eval --modes legacy,deterministic,llm --set dev|holdout|all --profile strict|capable`)
Dos conjuntos: desarrollo (189 conversaciones/259 turnos) y CIEGO (73/100, redactado después de dejar el primero al 100 %). Cada turno declara intención, competiciones, grupos, equipo y acción; se juzga por las
herramientas MCP realmente ejecutadas y sus argumentos; consultar una competición distinta de la esperada es fallo CRÍTICO. Resultados en `tests/fixtures/understanding/`.
El «LLM» es un modelo obediente simulado (perfil `strict`: solo palabras clave, reproduce el rechazo real; `capable`: entiende lo coloquial y arrastra la intención): mide la aplicación, no a Gemini.
