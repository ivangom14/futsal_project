# Fase 22 — Frontend web MVP del agente RFFM

## Auditoría (resumen, comprobado en el código)
- No existía frontend. El agente solo se usaba por CLI (`python -m futsal.agent "pregunta"`) y por el evaluador.
- No existía endpoint de conversación, streaming ni historial. `Agent.run(question)` es **sin memoria**: cada pregunta
  construye `messages = [user]` desde cero (solo conserva el contexto precargado de competiciones/grupos).
- `AgentResult` + `RunRecord` (`agent/trace.py`) ya dan: respuesta, `guardrail`, `error`, duración, llamadas LLM/tools,
  tokens, argumentos/duración/error de cada tool. Las fuentes RAG salen de los resultados de `search_rules`
  (`document`, `rule`, `pages`, `text`…); las tablas oficiales, de `AgentResult.official`.
- El agente solo habla con MCP→API (nunca con PostgreSQL). `Agent` guarda estado por pregunta → una a la vez.

## Decisiones técnicas
- **Servicio nuevo `futsal.agent.web` (puerto 8001)**, separado de la API de datos (8000). Poner el agente dentro de la API
  de datos la haría depender del agente (que a su vez la llama por MCP): dependencia circular. No toca BD ni migraciones.
- El agente corre en un **hilo con su propio bucle** (la llamada al LLM es síncrona): `/chat/health` no se bloquea durante
  una pregunta. Las preguntas se serializan con un `asyncio.Lock`.
- **Sin memoria conversacional**: no se inventa. El frontend guarda las conversaciones en `localStorage` y envía **solo la
  pregunta actual**; la UI lo indica. Mejora futura: `Agent.run(question, history=…)` (ver abajo).
- Sin traza JSONL en disco en este servicio; los resultados de tools se guardan solo en memoria para extraer fuentes.
- CORS solo para `http://localhost:5173` y `http://127.0.0.1:5173` (`CHAT_CORS_ORIGINS`, separados por comas).
- Las «fuentes» son lo **recuperado** en esa ejecución (no se afirma que cada una esté citada en el texto).

## Estructura
```
src/futsal/agent/web.py        servicio FastAPI del chat (contrato, runner, fuentes, diagnóstico)
tests/test_chat_web.py         backend (agente real + LLM guionizado + MCP simulado)
tests/fixtures/chat_response.json   contrato real, compartido con el frontend (lo verifica el test del backend)
scripts/e2e_chat.py            validación E2E real (consume cuota)
frontend/                      React 18 + TypeScript + Vite 5 + Tailwind 3
  src/{api,types,conversations,App}.ts(x), src/components/*, tests *.test.ts(x)
```

## Contrato
**`GET /chat/health`** → 200
```json
{"status":"ok|degraded","agent":{"ready":true,"error":null},
 "llm":{"provider":"gemini","key_configured":true},"data_api":{"reachable":true,"detail":null}}
```
`degraded` si falla cualquiera de las tres partes. Nunca devuelve claves.

**`POST /chat`** `{"question": "…"}` (1–2000 caracteres tras `strip`) → 200
```json
{"answer":"markdown","guardrail":null,"error":null,
 "sources":[{"kind":"rag|official_table","title":"…","detail":"…","pages":"31","text":"…","meta":{}}],
 "diagnostics":{"run_id":"…","provider":"…","model":"…","duration_ms":0,"llm_calls":2,"tool_calls":1,
   "input_tokens":0,"output_tokens":0,"token_source":"provider|estimated|…",
   "tools":[{"name":"…","arguments":{},"duration_ms":0,"is_error":false,"refused":false,"summary":"…"}],"errors":[]}}
```
- `error` no vacío con `answer` seguro = ejecución incompleta (p. ej. límite de herramientas): se muestra como aviso.
- 400 entrada inválida · 502 fallo del agente/LLM sin respuesta (cuota, red…) · 503 agente no disponible (sin clave, MCP caído).
  El frontend muestra 502/503/red como **error con «Reintentar»**, nunca como respuesta del agente.

## Dependencias incorporadas
Backend: ninguna nueva (FastAPI/uvicorn/httpx/mcp ya estaban). Frontend: `react`, `react-dom`, `react-markdown`,
`remark-gfm`; dev: `vite`, `@vitejs/plugin-react`, `typescript`, `tailwindcss@3`, `postcss`, `autoprefixer`,
`@tailwindcss/typography`, `vitest`, `jsdom`, Testing Library. Requiere **Node ≥ 18** (probado con 22).

## Instalación (Windows CMD, desde la carpeta del proyecto)
```
.venv\Scripts\activate.bat
pip install -e ".[dev]"
cd frontend
npm install
cd ..
```
La clave del modelo va en `.env` (`LLM_PROVIDER=gemini`, `GEMINI_API_KEY=…`), como en `docs/phase15-agent.md`.

## Arrancar (tres ventanas CMD, todas en la carpeta del proyecto)
Ventana 1 — base de datos y API de datos:
```
docker compose up -d --wait db
.venv\Scripts\activate.bat
uvicorn futsal.api.app:app_factory --factory --port 8000
```
Ventana 2 — servicio de chat (agente real):
```
.venv\Scripts\activate.bat
uvicorn futsal.agent.web:app_factory --factory --port 8001
```
Ventana 3 — frontend:
```
cd frontend
npm run dev
```
Abre http://127.0.0.1:5173. La insignia superior muestra «Conectado», «Servicio degradado» (pasa el ratón: dice qué falla) o «Sin conexión». `VITE_CHAT_API_URL` (frontend/.env) cambia la URL del chat.

## Pruebas
```
python -m pytest tests/test_chat_web.py -q     # backend, sin LLM ni PostgreSQL
cd frontend && npm test                         # frontend (Vitest; fetch simulado solo en el límite HTTP)
cd frontend && npm run build                    # typecheck + build
python -m pytest -q && ruff check . && python -m mypy      # batería completa
```
**E2E real (consume cuota de Gemini; solo si tú lo ejecutas)**, con las ventanas 1 y 2 arrancadas:
```
python scripts\e2e_chat.py          # solo health y plan, sin LLM
python scripts\e2e_chat.py --yes    # 3 preguntas reales (datos, goleador, normativa con fuentes RAG)
```
Comprobación manual en el navegador: pregunta normativa → debe aparecer «Fuentes consultadas» con documento y página;
para el reglamento, los documentos deben estar indexados (`docs/phase15-*.md`).

## Limitaciones conocidas
- Sin contexto entre mensajes (el agente no lo soporta): «¿y el siguiente?» no funciona; hay que repetir el contexto.
- Sin streaming: se espera la respuesta completa (suele tardar varios segundos). Una pregunta a la vez en el servidor.
- Historial solo en el `localStorage` del navegador (se pierde al borrarlo; sin sincronización).
- Fuentes = recuperadas, no necesariamente citadas; las tablas oficiales solo indican competición, grupo y captura.
- Sin autenticación (fuera de alcance); solo para uso local. No verificado con Gemini real ni en Windows desde este entorno.
- Frescura de datos (Fase 21) no se muestra en la UI aún.

## Mejoras futuras
- `Agent.run(question, history=…)` con las últimas N parejas pregunta/respuesta (hay que revisar la validación de IDs de
  equipo/jornada, que hoy solo admite IDs mostrados en la pregunta actual) y evaluarlo con el evaluador.
- Streaming (SSE) de trazas, historial en backend, mostrar `get_data_freshness`, exportar conversación.
