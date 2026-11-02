# Telemetría local de uso (Fase 2A)

**Apagada por defecto.** `AGENT_TELEMETRY=on` añade una línea por pregunta a `data/telemetry/questions.jsonl` (carpeta ignorada por Git). Nada sale de la máquina: el módulo
`agent/telemetry.py` no importa ninguna librería de red (lo comprueba una prueba) y no hay envío a proveedores. Las evaluaciones (`scripts/ab_arms.py`) fuerzan
`AGENT_TELEMETRY=off`: nunca escriben en la telemetría de uso real.

## Qué se registra, exactamente
| Campo | Contenido |
|---|---|
| `ts`, `run_id`, `schema` | Fecha (UTC, segundos), identificador de la ejecución, versión del formato |
| `conversation` | Hash SHA-256 (16 car.) del id de conversación con una **sal local** (`data/telemetry/.salt`); el id real no se guarda |
| `question` | La pregunta **saneada**: correos, URLs, IBAN, DNI/NIE, teléfonos, @usuarios y secuencias numéricas largas sustituidos por `[email]`, `[url]`, `[iban]`, `[dni]`, `[tel]`, `[usuario]`, `[num]`; secretos enmascarados (mismo saneado que las trazas); 300 caracteres como máximo |
| `pii_flag` | `true` si se sustituyó algo, hay autoidentificación («me llamo…», «mi hijo…», «vivo en…»), aparece un posible nombre completo de persona, o la pregunta era demasiado larga |
| `history_turns` | Solo el **número** de turnos de historial enviados |
| `tools_used`, `tools_refused` | Nombres y recuentos de las herramientas que pidió el LLM (sin argumentos); nº de llamadas rechazadas por los guardarraíles |
| `prefetch` | `attempted`, `fired`, `reason`, `rows`, `injected`, `catalog_calls` (sin el nombre del equipo) |
| `llm_calls`, `mcp_calls` | Recuentos (MCP separado en pedidas por el LLM, prefetch, precarga de contexto, rechazadas, reutilizadas, con error) |
| `tokens` | Entrada, salida, razonamiento y caché **del proveedor**, y su origen (`provider`/`estimated`) |
| `latency_ms`, `prompt_mode`, `flags` | Latencia, modo de prompt y banderas `AGENT_*` vigentes |
| `router` | Intención, nivel, confianza, motivo y aptitud Tier 0 del router en sombra (**sin** las entidades detectadas) |
| `guardrail`, `error_kind` | Guardarraíl que actuó y clase de error (`none`, `tool_limit`, `llm`, `other`) |

## Qué NO se registra nunca
La pregunta sin sanear · el historial (ni preguntas ni respuestas anteriores) · las respuestas del asistente · los argumentos y resultados de las herramientas ·
identificadores internos (equipo, jugador, partido) · claves o cadenas de conexión · el nombre del equipo detectado por el prefetch.

## Retención y borrado
- `AGENT_TELEMETRY_RETENTION_DAYS` (por defecto **90**; un valor inválido o menor que 1 vuelve a 90, nunca «sin límite»): al escribir, y como mucho una vez al día, se borra lo más antiguo.
- `python -m futsal.agent.telemetry stats` · `purge --before AAAA-MM-DD` · `purge --all` (borra también el fichero) · `export-candidates`.
- Borrar `data/telemetry/` entero también es válido; la sal se regenera (los hashes antiguos dejan de coincidir).

## Datos personales
Ante la duda se marca `pii_flag` y ese registro **nunca** se exporta como candidato a dataset. `export-candidates` además deduplica, excluye preguntas con marcadores de sustitución o de
menos de 8 caracteres y no incluye `run_id`, fecha ni conversación; deja las etiquetas (`labels`) en `null` para que las rellenes tú.
La detección de nombres de persona es heurística (dos palabras seguidas con inicial mayúscula fuera del vocabulario de la competición): puede marcar de más y puede no detectar todo.
Los nombres de jugadores son datos públicos de la RFFM, pero incluyen menores en categorías juveniles; por eso se tratan con duda.
