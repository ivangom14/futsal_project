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
*Interpretación:* «Entrega 4» no estaba definida en el repositorio; se ha tomado como «medir el agente sobre reglamento y dejar el criterio de decisión A (léxica + reformulación) frente a B (pgvector)».
**Decisión vigente: se mantiene A; no se añade pgvector.** Sin ejecuciones online hasta autorización expresa.

### Casos (`src/futsal/agent/rule_cases.py`, suite `all`; no están en `smoke`)
Preguntas y evidencia de `tests/fixtures/knowledge_eval_fifa.json` (verificadas sobre el PDF de la FIFA 2025-26).
| caso | tipo | debe |
|---|---|---|
| `regla_duracion_mitad` | respondible (literal falla, reformulada funciona) | 20 minutos; cita regla 7 / p. 60 |
| `regla_tiempo_muerto` | respondible | un minuto; regla 7 / p. 61 |
| `regla_sexta_falta` | respondible, coloquial | sexta/seis faltas; regla 13 / p. 99-100 |
| `regla_roja_mano` | respondible, coloquial | expulsión por evitar gol/ocasión manifiesta con la mano; regla 12 / p. 85-86 |
| `regla_sin_evidencia_sancion_rffm` | sin evidencia | decir que no consta; sin cifras ni sanciones |
| `regla_sin_evidencia_salario` | sin evidencia | decir que no consta; sin importes |

### Cómo se distingue cada tipo de fallo (el `detail` del informe lo etiqueta)
- **Recuperación** (`recuperación: search_rules no devolvió el fragmento esperado`): ningún fragmento devuelto tiene la regla, la página y el texto esperados. Es el fallo que alimenta la decisión A/B.
- **Herramientas**: no llamó a `search_rules`, la repitió o excedió 3 llamadas.
- **Respuesta** (`respuesta: no da la cifra correcta` / `no cita la regla N ni la página P`): contenido y cita obligatoria.
- **Citas y cifras** (`citas y cifras:`): cita una regla o página que la herramienta no devolvió; da una cifra que no está en los fragmentos ni en la pregunta.
- **Sin evidencia** (comprobación automática, también en live): la respuesta debe contener una fórmula de «no consta/no recoge»; **cualquier** cifra (aunque aparezca en un fragmento tangencial) y cualquier importe o duración de sanción («dos partidos», «60 euros», «€») se marcan como invención.
- **No evaluable ≠ éxito**: si no hay reglamento indexado en la base (`Truth.rules_indexed` es falso: tablas vacías o migración 0004 sin aplicar) los 6 casos quedan `not_evaluable` **antes** de llamar a nada (no consume cuota). Si durante la ejecución la búsqueda devuelve 0 resultados, el caso respondible también queda no evaluable. Un resumen con casos no evaluables nunca es «SUPERADA» por ellos (`overall_status`).

### Límites del evaluador offline (qué queda pendiente de un LLM real)
- Offline el LLM es guionizado y ya reformula: se comprueban API, MCP, criterios y `evidence_ok`, **no** que Gemini llame a `search_rules`, reformule ni cite bien. Las capas de respuesta/citas/cifras solo se ejecutan en live (se prueban con respuestas de ejemplo en `tests/test_agent_rule_cases.py`, que valida los criterios, no al modelo).
- Los 2 casos sin evidencia son `not_evaluable` offline. Lo comprobable automáticamente en live: fórmula de «no consta», ausencia de cifras e importes/sanciones, citas respaldadas. **Queda pendiente (revisión humana del informe)**: que la respuesta no presente como respuesta un fragmento tangencial (p. ej. la regla 12 como si fijara la sanción de la RFFM) o no sugiera un dato sin cifras; los criterios léxicos no detectan paráfrasis engañosas ni afirmaciones sin cifra.
- Los criterios de respuesta son léxicos (cifra y cita); una paráfrasis correcta sin la cifra literal se marcaría como fallo (falso negativo posible; se revisa en el informe).
- `--plan` los lista en «No evaluables en esta base (no consumen cuota)», pero en live **sí se ejecutan y consumen cuota**: solo estima los 4 casos con ruta guionizada (8 llamadas, ≈ 29.300 tokens de entrada); los 2 sin evidencia no se cuentan (≈ +2-4 llamadas, +≈ 8.000-15.000 tokens).

### Coste estimado
- Por caso respondible: 2 llamadas LLM y ≈ 7.200 tokens de entrada (5 fragmentos de hasta 1.200 caracteres en el resultado).
- Ejecución online de los 6 casos: ≈ 10-12 llamadas LLM, ≈ 40.000-45.000 tokens de entrada, ≈ 200-300 de salida más el razonamiento; presupuesto duro de 40 peticiones HTTP; 4 s entre peticiones (≈ 1-2 min). Estimado localmente; Gemini informará los reales. No se calculan costes monetarios.
- **`search_rules` mantiene `limit=5` por defecto.** No se optimiza el número de fragmentos sin una comparación de calidad (recuperación y respuesta con 3 frente a 5) que lo justifique.

### Criterio de decisión A frente a B (tras la ejecución online)
Se mantiene A si los 4 respondibles pasan y los 2 sin evidencia dicen «no consta». Se plantea B si fallan por **recuperación** en ≥ 2 de los 4 (el agente reformuló y aun así no llegó el fragmento). Si fallan por respuesta, cita o herramientas, se corrige el prompt o la descripción de la herramienta, no la recuperación.

### Procedimiento de ejecución online (NO ejecutado; requiere autorización expresa, consume cuota)
Windows CMD, API en marcha (`uvicorn futsal.api.app:create_app --factory --port 8000`) sobre la misma base, con el reglamento ya ingerido y las variables `LLM_PROVIDER=gemini`, `GEMINI_MODEL` y `GEMINI_API_KEY` (no se guardan en informes):
1. Comprobar la base sin gastar cuota (los casos deben salir «evaluables: 4» y la base «misma que la API»); si salen no evaluables, falta ingerir el reglamento:
```
python -m futsal.agent.evaluate --mode live --suite all --plan --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
```
2. Ejecución online (única orden que consume cuota):
```
python -m futsal.agent.evaluate --mode live --confirm-live --suite all --max-requests 40 --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
```
3. Revisar `report.md` (compartible; las trazas saneadas están en `traces.sanitized.jsonl`): estado global, capa que falla en cada caso y las 2 respuestas sin evidencia a mano.
Como `suite_version` cambia, comparar con líneas base anteriores marcará los casos como nuevos (esperado); esta ejecución no es comparable con la línea base de las Fases 11/13.
