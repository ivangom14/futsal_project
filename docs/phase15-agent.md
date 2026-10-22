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
python -m futsal.cli knowledge-ingest --source fifa-futsal-laws-2025-26   # ingesta (Entrega 2); también knowledge-status / knowledge-search
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

### Procedimiento de ejecución online en Windows CMD (NO ejecutado; consume cuota solo el paso 7)
Decisiones vigentes: `search_rules` con `limit=5` por defecto y coste de tokens aceptado; la herramienta se ofrece siempre (sin filtrar por palabras clave de reglamento).
Los comandos están comprobados contra el código (nombres de módulos, opciones y variables); **no se han probado en tu Windows**, solo la lógica equivalente en el entorno de Claude (Linux).
Claude no tiene ni ha pedido tu `GEMINI_API_KEY`. Usa **dos ventanas CMD**, ambas en la carpeta del proyecto y con el entorno virtual activado.

**Paso 0 · Carpeta, rama y entorno (ventana 1)**
```bat
cd ruta\a\futsal_project
dir pyproject.toml
.venv\Scripts\activate.bat
where python
git branch --show-current
git fetch origin claude/tender-rubin-rsz9uv
git status -sb
git log -1 --oneline
```
Debe verse `pyproject.toml`; `where python` debe listar primero `...\futsal_project\.venv\Scripts\python.exe`; la rama, `claude/tender-rubin-rsz9uv`; `git status -sb` sin cambios locales ni «behind». Si estás por detrás: `git pull origin claude/tender-rubin-rsz9uv` y `pip install -e ".[dev]"`. No hagas merge a `main`.

**Paso 1 · Base de datos y reglamento indexado (ventana 1)**
```bat
docker compose up -d --wait db
alembic upgrade head
python -m futsal.cli knowledge-status
```
`knowledge-status` debe mostrar `fifa-futsal-laws-2025-26 ... índice=indexed ... fragmentos=267`. Si no aparece (o falla por tablas inexistentes), `alembic upgrade head` aplica la migración 0004 y la ingesta es idempotente:
```bat
python -m futsal.cli knowledge-ingest --source fifa-futsal-laws-2025-26
```
(Necesita el PDF en `data\knowledge\fifa-futsal-laws-2025-26\original.pdf`, ignorado por Git; si falta, `python -m futsal.cli knowledge-fetch --help`.) Esto escribe solo en las tablas `kb_*`, nunca en las deportivas.

**Paso 2 · Variables de entorno sin revelar la clave (ventana 1)**
Las lee `futsal.config.settings.env_value`: primero el entorno de la sesión y, si falta, `.env`. Comprobación sin imprimir la clave (solo «definida»/«NO definida»):
```bat
python -m futsal.agent.preflight --for-live --no-docker
```
Muestra `LLM_PROVIDER`, `GEMINI_API_KEY` (definida/NO), el modelo, el host de `DATABASE_URL` sin contraseña y si la API responde (esta última fallará hasta el paso 3; es normal). Alternativa solo de sesión: `if defined GEMINI_API_KEY (echo definida) else (echo NO definida)`. **No uses** `echo %GEMINI_API_KEY%`, `set GEMINI_API_KEY` sin argumentos, `set` a secas, `type .env` ni `findstr GEMINI .env`: mostrarían la clave.

Variables que falten, solo para esta ventana (se pierden al cerrarla):
```bat
set LLM_PROVIDER=gemini
set GEMINI_MODEL=gemini-3.5-flash-lite
```
(`gemini-3.5-flash-lite` es el modelo por defecto del código; puedes omitir `GEMINI_MODEL`.) La clave, por orden de preferencia:
1. **`.env` local** (método ya usado en la Fase 10; `.env` está en `.gitignore` y no está versionado): `notepad .env` y añade la línea `GEMINI_API_KEY=...`. Nada queda en el historial de CMD ni se versiona.
2. **Solo la sesión**: `set /p GEMINI_API_KEY=Clave: `. No queda en el comando, pero lo que escribes **se ve en pantalla** mientras lo tecleas, y no he verificado si CMD lo conserva en el historial de la ventana (F7): al terminar, pulsa **Alt+F7** para borrarlo y cierra la ventana. No pongas la clave en `set GEMINI_API_KEY=valor`, scripts, `.bat` ni archivos del repositorio.

**Paso 3 · Arrancar la API (ventana 2, mismo directorio y entorno)**
```bat
cd ruta\a\futsal_project
.venv\Scripts\activate.bat
uvicorn futsal.api.app:app_factory --factory --port 8000
```
Debe decir `Uvicorn running on http://127.0.0.1:8000`. La API usa tu `DATABASE_URL` (la misma que la ventana 1); no necesita la clave de Gemini. Verificación desde la ventana 1:
```bat
curl.exe -s "http://127.0.0.1:8000/knowledge/search?q=tiempo+muerto&limit=1"
```
Debe devolver JSON con `"count":1` y la regla 7. Si la API estaba arrancada antes de hacer `git pull`, páprala (Ctrl+C) y reiníciala.

**Paso 4 · Plan de cuota (ventana 1; no llama al LLM ni consume cuota)**
```bat
python -m futsal.agent.evaluate --mode live --suite all --plan --max-requests 40 --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
```
Verificado en el código: con `--plan` el programa imprime el plan y termina antes de construir ningún cliente de LLM (`create_llm` solo se llama sin `--plan`), por lo que no necesita la clave. Comprueba en la salida:
- `Base de datos: misma que la API (demostrado)` (si dice «NO demostrada», la ejecución live queda bloqueada: reinicia la API y revisa `DATABASE_URL`).
- `Reglamento: indexado en la base de conocimiento` (si dice NO indexado, los 6 casos quedan no evaluables: vuelve al paso 1).
- `Casos seleccionados: 6 (evaluables: 4)`; `Llamadas al LLM previstas: 8`; `Presupuesto duro de peticiones (--max-requests): 40`; tokens de entrada ≈ 29.300 (estimación).
- Los 2 casos sin evidencia aparecen en «No evaluables en esta base (no consumen cuota)»: **en live sí se ejecutan y consumen cuota** (≈ +2-4 llamadas). Presupuesto realista total: ≈ 10-12 llamadas, ≈ 40.000-45.000 tokens de entrada; con 4 s entre peticiones, 1-2 minutos.

**Paso 5 · (opcional, sin cuota) Preflight completo**
```bat
python -m futsal.agent.preflight --for-live
```
Debe indicar `GEMINI_API_KEY definida`, la API respondiendo y las 12 herramientas MCP esperadas, incluida `search_rules`. Sale con código 2 si algo bloquea.

**Paso 6 · Antes de gastar cuota** (checklist): rama correcta y sin cambios locales; `knowledge-status` indexado; API en marcha y con el código actual; plan con base «misma que la API», reglamento indexado y 6 casos; clave definida (preflight); aceptas ≈ 10-12 llamadas. El comando de abajo es el único que consume cuota.

**Paso 7 · Ejecución online (SOLO TÚ, ventana 1; Claude no la ejecuta)**
```bat
python -m futsal.agent.evaluate --mode live --confirm-live --suite all --max-requests 40 --only regla_duracion_mitad,regla_tiempo_muerto,regla_sexta_falta,regla_roja_mano,regla_sin_evidencia_sancion_rffm,regla_sin_evidencia_salario
```
Sin `--confirm-live` el programa se niega a llamar al LLM. Usa `--min-interval 4` por defecto entre peticiones, 2 reintentos por llamada solo en 429/5xx/red y se detiene al llegar a 40 peticiones. Un 429 deja el caso como `not_run` (no `fail`).

**Paso 8 · Interpretar el informe**
- La salida imprime la tabla por caso y al final la ruta `data\agent\eval\<fecha>-live-all\`. Contiene `report.md` (léelo), `results.json`, `traces.sanitized.jsonl` (compartible) y `traces.raw.jsonl` (**no lo compartas**).
- En `report.md`: estado global (SUPERADA / CON FALLOS / INCOMPLETA / SIN CASOS EVALUABLES; los no evaluables nunca cuentan como superados), tabla por capa y, en «Detalle para revisión humana», la pregunta, la ruta de herramientas, el criterio y **la respuesta literal del agente** de cada caso. Para ir a un caso: `findstr /n "regla_sin_evidencia" data\agent\eval\<carpeta>\report.md`.
- Dónde falla cada caso (etiqueta en `detail`): `recuperación:` (el fragmento esperado no llegó; lo que alimenta A/B), `herramientas:`, `respuesta:` (cifra o cita ausente), `citas y cifras:` (regla/página/cifra no respaldadas).
- **Revisión manual de los 2 casos sin evidencia** (lo que el evaluador no puede juzgar): lee la respuesta literal de `regla_sin_evidencia_sancion_rffm` y `regla_sin_evidencia_salario` y comprueba que (a) dice claramente que el reglamento no lo recoge, (b) no da cifras, importes ni sanciones, (c) no presenta un fragmento tangencial (p. ej. la regla 12 sobre tarjetas) como si respondiera a la pregunta, y (d) no sugiere un dato sin cifra («suele ser de varios partidos»). Si la respuesta es correcta pero el evaluador la marcó (o al revés), anótalo: los criterios son léxicos.
- Un caso `not_run` por 429 o error de red es de infraestructura: repite solo ese caso con `--only` tras esperar.
- Para decidir A/B aplica el criterio de arriba. Comparar con líneas base de fases anteriores avisará «suite distinta» (esperado).

**Paso 9 · Detener y limpiar**
Ventana 2: **Ctrl+C** para parar `uvicorn`. Si la clave estaba solo en la sesión, cierra ambas ventanas (o `set GEMINI_API_KEY=` para vaciarla). Para parar la base: `docker compose stop db` (opcional).

**Si algo falla**
- *Faltan credenciales* (`GEMINI_API_KEY no definida`, `No se puede iniciar el LLM`): no se ha llamado al LLM; define la clave (paso 2) y repite el paso 7. `LLM_PROVIDER` debe ser `gemini`.
- *Falla la conexión a PostgreSQL* («No se puede conectar a PostgreSQL»): Docker Desktop abierto y `docker compose up -d --wait db`; revisa `DATABASE_URL` en `.env`. No se consumió cuota.
- *La API no responde* (`ConnectError`, «NO demostrada igual a la API»): ventana 2 sin arrancar o en otro puerto/base; reinicia `uvicorn` y vuelve al paso 4. Con base no demostrada la ejecución live se bloquea por diseño (`--allow-unverified-db` existe pero no se recomienda).
- *El reglamento no está indexado*: el plan lo indica; repite el paso 1 (`knowledge-ingest`). Los casos de reglamento saldrían `not_evaluable`, nunca aprobados.
- *Tablas `kb_*` inexistentes*: `alembic upgrade head`.
