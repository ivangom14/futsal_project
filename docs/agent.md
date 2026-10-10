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
