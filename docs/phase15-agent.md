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
  Requiere una evaluación online que no se ha lanzado (cuota). Los casos de reglamento ya existen en el evaluador (Entrega 4) pero solo se han ejecutado offline.
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

## Entrega 4: casos de reglamento en el evaluador y decisión A/B
*Interpretación:* «Entrega 4» no estaba definida en el repositorio; se ha tomado como «medir el agente sobre reglamento y dejar el criterio de decisión A (léxica + reformulación) frente a B (pgvector)». Sin ejecuciones online.

**Casos** (`src/futsal/agent/rule_cases.py`, suite `all`; preguntas y evidencia de `tests/fixtures/knowledge_eval_fifa.json`):
- `regla_duracion_mitad` (literal fallida, solo funciona reformulada), `regla_tiempo_muerto`, `regla_sexta_falta` (coloquial), `regla_roja_mano` (coloquial): deben usar `search_rules`, dar la cifra correcta y citar regla o página.
- `regla_sin_evidencia_sancion_rffm`, `regla_sin_evidencia_salario`: deben decir que el reglamento no lo recoge, sin cifras ni sanciones.
- Capas nuevas: datos (algún fragmento trae la regla, página y texto esperados), respuesta (`rule_cited`, `no_evidence_rules`) e invención (`unsupported_rule_citations`: regla/página que no devolvió la herramienta; `unsupported_digits`: cifras ausentes de los fragmentos y de la pregunta).
- Offline: los 4 respondibles pasan con un LLM guionizado que ya reformula (miden API+MCP+criterios, no a Gemini); los 2 sin evidencia son «no evaluables» (requieren LLM real). Suite completa offline: 27 pass, 0 fail, 8 no evaluables.
- Si el documento no está ingerido (la búsqueda devuelve 0 resultados), los respondibles quedan «no evaluables» en vez de fallar.
- Hallazgo: cada caso respondible cuesta ≈ 7.200 tokens de entrada (2 llamadas): 5 fragmentos de hasta 1.200 caracteres en el resultado. Candidato a ajustar (`limit` por defecto 3) tras la medición online.

**Criterio de decisión A frente a B** (tras la ejecución online de los 6 casos): se mantiene A si los 4 respondibles pasan y los 2 sin evidencia dicen «no consta»; se considera B si fallan por recuperación
(capa de datos, el agente reformuló y aun así no llegó el fragmento) en ≥ 2 de los 4. Si fallan por respuesta/cita, se corrige el prompt, no la recuperación.

**Ejecución online (NO lanzada; requiere tu autorización expresa, consume cuota)**, Windows CMD, con `GEMINI_API_KEY`, `LLM_PROVIDER=gemini` y la API en marcha:
```
python -m futsal.agent.evaluate --mode live --suite all --plan --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
python -m futsal.agent.evaluate --mode live --confirm-live --suite all --max-requests 40 --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
```
El primero no llama al LLM (estimación: ≈ 8 llamadas y ≈ 29.000 tokens de entrada; subestima los 2 sin evidencia, que `--plan` no cuenta al no tener ruta guionizada, +≈ 4 llamadas). Como `suite_version` cambia, la comparación con líneas base anteriores marcará casos nuevos (esperado).
