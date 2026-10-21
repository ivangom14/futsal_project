# Fase 15 (RAG) — Entrega 3, opción A: reglamento en la API, el MCP y el agente

Se mantiene la búsqueda **léxica** de la Entrega 2 (sin embeddings, sin migraciones) y el agente reformula con el vocabulario del reglamento.
La opción B (`pgvector`) queda abierta y solo se justificaría si A no basta con medición online.

## Qué se ha añadido
| Capa | Cambio |
|---|---|
| API | `GET /knowledge/search?q=&limit=1..10&rule=&doc_key=&include_history=` (solo lectura, parametrizada). 400 si la consulta no es válida; 503 si no hay migración `0004` o documento indexado (sin detalles internos). Respuesta compacta: `query`, `method` (`fts`/`trigram`/`none`), `count`, `hits` (`document`, `doc_key`, `version`, `rule`, `rule_title`, `section`, `subsection`, `pages`, `score`, `text`, `truncated`; sin nulos) y una `note`. |
| MCP | Herramienta `search_rules(query, limit=5, rule?)`. Su descripción pide reformular con el vocabulario del reglamento (córner→saque de esquina, pista→terreno de juego, roja→expulsión, cambios→sustituciones, portero→guardameta), probar otra consulta y citar regla y página. Accede solo vía API. |
| Agente | El prompt incluye las reglas del juego en su ámbito y pide: usar `search_rules` reformulando, citar regla y página, no inventar si los fragmentos no responden y no mezclar reglamento con datos de competición. El aviso de alcance menciona las reglas; el vocabulario de la reconsulta incluye regla/reglamento/falta/penalti. |
| Preflight | `EXPECTED_TOOLS` incluye `find_teams` y `search_rules` (detecta un código desactualizado). |

## Diseño de la respuesta de la herramienta
- **No hay umbral de relevancia**: la puntuación no distingue fragmentos relevantes de irrelevantes (medido en la Entrega 2). Por eso la respuesta lo dice en
  `note` y el agente debe leer el texto y decidir «no hay evidencia»; con cero resultados la nota pide reformular.
- Acotada: ≤ 5 fragmentos por defecto, texto por fragmento ≤ 1.200 caracteres y total ≤ 6.000 (`truncated`), unos 2 k tokens como máximo.
- El agente ya descarta los nulos al compactar los resultados.

## Qué está demostrado (offline, PostgreSQL temporal, sin LLM)
- La API devuelve **exactamente** lo que la capa de recuperación (mismo orden, método, regla, páginas y texto) para las 25 preguntas de evaluación, literales y reformuladas, sobre el PDF real de FIFA.
  Por tanto la medición de la Entrega 2 (text@5: 0,60 literal → 0,88 reformulada manualmente) es la de referencia; **no es una medición del agente**.
- La herramienta MCP y el agente (LLM guionizado) la usan de extremo a extremo; validación de parámetros, 400/503 y consulta con SQL hostil sin efecto.
- 368 tests pasan (2 omitidos); Ruff y mypy limpios.

## Qué NO está demostrado
- Que Gemini reformule bien, llame a `search_rules` cuando debe y no la llame cuando no, ni que cite correctamente o admita «no consta» en las 6 preguntas sin evidencia.
  Requiere una evaluación online que no se ha lanzado (cuota). El evaluador del agente todavía no tiene casos de reglamento.
- La calidad de las respuestas del agente sobre reglas (solo está medida la recuperación).

## Coste en tokens (importante)
La herramienta y las instrucciones se envían en **cada** llamada al LLM. Medido por caracteres (estimación ~3,5 car./token):
`search_rules` ≈ 730 caracteres de esquema (~210 tokens) y el prompt +300 caracteres (~90 tokens): **≈ +300 tokens por llamada (~+14 % del prefijo)**,
a lo que se suma `find_teams` de la Fase 14 (~100 tokens). Evaluación offline estimada del conjunto actual de 29 casos frente a la última ejecución offline
(Fase 13): 129.856 → 153.883 tokens de entrada (+18,5 %), con las mismas llamadas y sin cambios de calidad; el detector de regresiones lo marca como bloqueante
(umbral 15 %), lo cual es correcto: hay que recalibrar la línea base online. Esto reduce el ahorro del prefijo logrado en la Fase 11.

Opciones (decisión pendiente): aceptar el coste; acortar la descripción; o exponer `search_rules` solo cuando haya un documento indexado / solo en preguntas con
vocabulario de reglas (menos coste, pero riesgo de no ofrecerla en preguntas coloquiales).

## Uso
```
python -m futsal.knowledge ...             # ingesta/inspección (Entrega 2)
uvicorn futsal.api.app:app_factory --factory --port 8000
curl "http://localhost:8000/knowledge/search?q=saque%20de%20esquina&limit=3"
```
