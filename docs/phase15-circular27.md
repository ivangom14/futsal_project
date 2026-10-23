# Fase 15 — Circular 27 de la RFEF (Reglas de Juego del Futsal 2025/2026) en la base de conocimiento

Ampliación de la base documental existente (mismo `knowledge-ingest`, mismas tablas `kb_*`, misma API `GET /knowledge/search` y misma herramienta MCP `search_rules`). Sin pgvector, sin embeddings, sin herramienta nueva, sin migración.
Verificado en el entorno de Claude (Linux) sobre una **copia** de la base réplica; nada se ha ejecutado en tu base local ni en tu Windows ni con Gemini.

## Fuente y metadatos (`src/futsal/config/knowledge_sources.py`, clave `rfef-circular-27-2025-26`)
| campo | valor | origen |
|---|---|---|
| Emisor | Real Federación Española de Fútbol (RFEF) | p. 1 |
| Documento | Circular n.º 27, temporada 2025/2026: Reglas de Juego del Futsal 2025/2026 (Circular FIFA n.º 1941) | p. 1 |
| URL | `https://rfef.es/sites/default/files/pdf/circulares/Circular_27_con_anexos.pdf` | indicada por el usuario |
| Fecha de publicación | 2025-09-08 (carta de la RFEF, «Las Rozas de Madrid, a 8 de septiembre de 2025»). La circular FIFA es del 5-sep-2025 (p. 2) | p. 1-2 |
| Entrada en vigor | no consta en la circular (se deja vacío) | — |
| Tipo / idioma / perfil | `circular` / `es` / `rfef-circular` | — |
| Versión | «Circular 27 · 2025/26» (la versión técnica es el hash del archivo) | — |
| Hash SHA-256 (descarga del 2026-10-23) | `b05def07112306d3fb35c867fd5aa857689dd92d98c472d1965bf6d78cd6dc55`, 5.326.803 bytes, 220 páginas | `source.json` / BD |
| Estado | `extracción=ok`, `índice=indexed`, 44 fragmentos (p. 1-23) | `knowledge-status` |

Queda como **documento independiente**: no se toca el PDF de FIFA (`fifa-futsal-laws-2025-26`), ni su versión, hash, fragmentos ni historial (lo comprueba un test).

## Estructura del PDF (páginas **físicas**, 1-based)
| páginas | contenido | tratamiento |
|---|---|---|
| 1 | Carta de la RFEF (Circular 27, 8-sep-2025, Secretario General) | indexada; parte «Comunicación de la RFEF (Circular 27)» |
| 2 | Circular FIFA n.º 1941 (Zúrich, 5-sep-2025) | indexada; parte «Circular FIFA n.º 1941» |
| 3-23 | Resumen de los principales cambios (FIFA): fragmentos de las reglas con los cambios, protocolo VAR, directrices y posicionamiento, interpretación | indexada; parte «Resumen de cambios 2025/26 (FIFA; sin cursiva ni tachado)» (+ subparte); reglas 1-17 detectadas por los encabezados «Regla N.» |
| 24-220 | Reglas de Juego del Futsal 2025/26 completas «con cambios resaltados» | **no indexada** (ver más abajo) |

### Solapamiento y decisión de no indexar las p. 24-220
- Comprobado: el texto extraído de **las 197 páginas** del reglamento anexo (p. 24-220) es **idéntico carácter a carácter** al de las p. 1-197 del PDF de FIFA ya indexado (desfase constante de 23 páginas). Lo verifica `test_circular27_annex_is_page_by_page_identical_to_the_fifa_document`.
- Consecuencia de indexarlas: cada pregunta de reglamento devolvería dos fragmentos equivalentes (uno por documento) y gastaría hasta la mitad de los 5 resultados con la misma evidencia. No se descarta contenido: el texto está en `fifa-futsal-laws-2025-26`; el archivo original se conserva en `data/knowledge/…/<hash16>.pdf` y el tramo omitido, con su motivo, queda en `kb_document_versions.quality.page_sections` y se avisa en la ingesta («páginas 24-220 no indexadas: …»).
- Limitación: el PDF anexo **resalta** los cambios (color/marcas gráficas); ese resaltado no está en el texto extraído de ninguna de las dos copias. Si se quisiera responder «qué texto cambió exactamente en la regla N» desde las reglas completas, haría falta extraer el formato (mejora futura).
- Alcance de la decisión: si en el futuro RFEF publica otra versión con reglas **distintas**, el hash cambia y el tramo se reevaluará (la exclusión está declarada en el manifiesto, no es automática).

## Cambios de código (mínimos, sobre la arquitectura existente)
- `knowledge/models.py`: `PageSection(first, last, part, skip_reason)`; `DocumentSpec.page_sections` (PDF compuesto: partes por tramo de páginas y tramos excluidos con motivo). Validación de tramos (solapes y fuera de rango → la ingesta falla sin crear el documento).
- `knowledge/structure.py` / `profiles.py`: perfil `rfef-circular` (sin cabecera repetida de la FIFA, número de página impreso descartado, reglas por «Regla N.» en el resumen, subapartados «Protocolo…», «Directrices…», «Interpretación…» abren su propia parte para no heredar la regla anterior).
- `knowledge/ingest.py`: aplica los tramos y guarda `quality.page_sections`. Idempotencia sin cambios (hash).
- `api/knowledge.py`: el hit incluye `part` solo cuando aporta (no es «Regla N»); la `note` indica que una circular solo rige lo que su texto dice (no es normativa de la RFFM ni de otra competición salvo que lo indique) y que, si dos documentos difieren, se presentan por separado (actualizada al añadir la Circular 10 de 2026/27). `mcp_server/server.py`: la descripción de `search_rules` nombra ambos documentos. Sin palabras clave rígidas ni filtros por documento obligatorios.
- `knowledge/evaluate.py`: la evidencia de una pregunta puede declarar `doc_key`.
- `agent/evaluate.py` + `agent/rule_cases.py`: `Truth.kb_docs` (documentos indexados); cada caso declara los que necesita y, si falta alguno, queda **no evaluable** (no se gasta cuota ni se aprueba).

## Citas: página física frente a número impreso
Las citas usan siempre la **página física del PDF** (`pages`: «3», «1-2»). La numeración impresa difiere (resumen: p. 4 ↔ «2»; reglas anexas: p. 83 ↔ «60»), por eso el informe de calidad marca `numero_impreso_distinto` en p. 4-23: es esperado y no se usa para citar. En la circular el «apartado» es la parte (campo `part`) y, en el resumen, la regla (`rule`).

## Resultados de extracción (p. 1-23 indexadas; 220 páginas procesadas)
- 220/220 páginas extraídas; 0 caracteres ilegibles; 0 avisos de extracción; **1 página sin texto: la 220** (última, también vacía en el PDF de FIFA; está en el tramo omitido). Las p. 1-23 no tienen páginas con poco texto ni con posibles tablas.
- 44 fragmentos (media ≈ 1.040 caracteres; 2 fragmentos < 200: un «(…)» y el resto de un título) y 24 segmentos; reglas detectadas en el resumen: 1-10, 12, 13, 15-17.
- **Tablas/listas**: el cuadro de medidas del terreno (p. 3-4) conserva el texto pero no su maquetación de columnas. **Cursiva y tachado** del resumen (nuevo / eliminado) no se conservan: en las frases modificadas figuran juntos el texto viejo y el nuevo («preferentemente preferiblemente», «superior a más larga que»). El agente no debe afirmar qué es nuevo o eliminado salvo que la frase lo permita; la parte del documento lo indica («sin cursiva ni tachado»). En el PDF la cursiva marca lo nuevo (comprobado con la fuente `OpenSans-Italic`) y es una mejora futura recuperarla.

## Evaluación de recuperación (sin LLM; `tests/fixtures/knowledge_eval_circular27.json`)
Evidencia verificada contra el PDF (test `test_circular27_evidence_is_in_the_original_pages`). Con ambos documentos en la misma base:

| pregunta | respuesta esperada | evidencia (p. física, parte/regla) | literal | reformulada |
|---|---|---|---|---|
| Qué comunica la Circular 27 y a qué temporada | Comunica a los afiliados la Circular FIFA 1941 (nueva edición de las Reglas de Juego del Futsal); temporada 2025/2026 | p. 1, carta de la RFEF | rango 1 | 1 |
| Qué modificaciones se han aprobado | El Bureau del Consejo de la FIFA aprobó una serie de modificaciones (unas específicas del futsal, otras por cambios del fútbol); se adjunta un resumen | p. 2 (circular FIFA) y p. 3 (resumen) | 2 | 2 |
| Cuándo se permite el césped artificial (según el resumen de modificaciones) | De forma excepcional, solo en competiciones nacionales y si lo especifica el reglamento de la competición (Regla 1 §1) | p. 3, regla 1 (la misma frase está en FIFA p. 15) | **no está en el top 5** | 1 |
| ¿Se puede jugar en pista de césped sintético? (coloquial) | Igual; evidencia válida en cualquiera de los dos documentos | FIFA p. 15 o circular p. 3, regla 1 | **no está en el top 5** | 1 |
| Fechas de la RFEF y de la FIFA | RFEF 8-sep-2025 (Las Rozas); FIFA 5-sep-2025 (Zúrich) | p. 1 y p. 2 | 1 | 1 |
| Quién firma la Circular 27 | Álvaro de Miguel Casanueva, Secretario General | p. 1 | 1 | 1 |
| Sanción de la RFFM por alinear a un jugador sin licencia (sin evidencia) | **No consta**: ni la circular ni las Reglas FIFA regulan sanciones de la RFFM | — | — | — |
| Sanciones de la Circular 27 a los clubes (sin evidencia) | **No consta**: solo comunica las Reglas de Juego | — | — | — |

Literal text@5 = 4/6 (0,67); reformulada 6/6 (1,00). Fallo conocido: con la pregunta literal sobre el césped el fragmento del reglamento FIFA ocupa el primer puesto y el de la circular queda fuera; con vocabulario del reglamento («césped artificial competiciones nacionales») la circular es la primera. Es un límite de la búsqueda léxica, no de la ingesta.

### No se perjudica al reglamento FIFA (25 preguntas respondibles + 6 sin evidencia, antes y después)
| | text@1 | text@3 | text@5 | page@5 | MRR |
|---|---|---|---|---|---|
| FIFA solo, literal | 0,32 | 0,56 | 0,60 | 0,76 | 0,437 |
| FIFA + circular, literal | 0,28 | 0,56 | 0,60 | 0,76 | 0,417 |
| FIFA solo, reformulada | 0,52 | 0,76 | 0,88 | 0,92 | 0,655 |
| FIFA + circular, reformulada | 0,48 | 0,76 | 0,88 | 0,92 | 0,635 |

Misma cobertura en los 5 primeros; cae un puesto la pregunta de amonestación/expulsión porque el resumen de cambios de la regla 12 (texto legítimo y relevante) compite en primer lugar (−0,02 de MRR). Un test impide que text@5 y page@5 de FIFA bajen. Se mantiene `limit=5`.

### Evaluación del agente (casos de `agent/rule_cases.py`, offline con LLM guionizado; **no es una evaluación del LLM**)
Seis casos nuevos `circular27_*` (comunica, modificaciones, césped, césped coloquial, fechas RFEF/FIFA, y uno sin evidencia sobre la RFFM) que comprueban por capas: **recuperación** (¿algún fragmento trae página, regla y texto esperados?), **respuesta** (dato correcto y cita de página/regla), **citas** (regla/página no devueltas), **invención** (cifras y colores no respaldados, importes y sanciones en los casos sin evidencia) y **comportamiento sin evidencia** («no consta»). Suite offline completa con ambos documentos indexados (41 casos): **32 pass, 0 fail, 9 no evaluables** (65 → 75 llamadas guionizadas). Solo los 12 casos de reglamento/circular, con ambos documentos indexados, offline: 9 pass, 0 fail, 3 no evaluables (los 3 sin evidencia, que requieren LLM real). Con solo FIFA indexada: los 6 casos de la circular quedan **no evaluables** (4 pass FIFA, 8 no evaluables); nunca aprobados.
Pendiente de una evaluación real (con tu autorización y cuota): que Gemini busque, distinga la circular del reglamento FIFA y reconozca que no consta una sanción de la RFFM. Límites léxicos conocidos: no detecta fechas intercambiadas (RFEF↔FIFA) si ambas figuran en la respuesta, ni paráfrasis engañosas en los casos sin evidencia.

## Alcance normativo (qué puede afirmar el agente)
La `note` de cada respuesta de `search_rules` recuerda: cita **documento**, regla y página; la circular que comunica las reglas no es normativa de competición ni de la RFFM. Para la circular, el agente debe poder decir qué comunica, qué fecha lleva cada documento (RFEF 8-sep, FIFA 5-sep) y qué modificaciones resume; para sanciones, obligaciones administrativas o reglamentos de competición de la RFEF/RFFM **solo hay evidencia si existe un documento propio indexado**; si no, debe indicar el alcance de la evidencia y qué falta. No se fija jerarquía normativa.

## Coste en tokens
La descripción de `search_rules` crece ≈ 40 caracteres (≈ +12 tokens por llamada) y la `note` ≈ 170 caracteres por respuesta (≈ +45 tokens, una vez por búsqueda): ≈ +60-70 tokens por pregunta de reglamento (el caso medio offline pasa de ≈ 7.200 a ≈ 7.270 tokens de entrada). No se ha cambiado el límite de 5 fragmentos.

## Procedimiento en tu Windows CMD (NO ejecutado por Claude; comandos comprobados contra el código en Linux)
1. **Carpeta, rama y código** (ventana 1, desde la carpeta del proyecto, entorno activado):
```bat
cd ruta\a\futsal_project
dir pyproject.toml
.venv\Scripts\activate.bat
git branch --show-current
git status -sb
git pull origin claude/tender-rubin-rsz9uv
pip install -e ".[dev]"
```
La rama debe ser `claude/tender-rubin-rsz9uv`. Si la API (`uvicorn`) estaba en marcha antes del `git pull`, páralo (Ctrl+C) y arráncalo de nuevo al final: el código de `search_rules` ha cambiado.
2. **Estado de la base** (no modifica nada):
```bat
docker compose up -d --wait db
alembic upgrade head
python -m futsal.cli knowledge-status
```
Debes ver `fifa-futsal-laws-2025-26 v1 ACTUAL índice=indexed extracción=ok páginas=197 fragmentos=267`. Apunta el número de versión y de fragmentos: no deben cambiar con la ingesta de la circular.
3. **Descargar el PDF** (escribe en `data\knowledge\rfef-circular-27-2025-26\`, ignorado por Git; reutiliza el archivo si ya existe; `--refresh` fuerza la descarga):
```bat
python -m futsal.cli knowledge-fetch --url https://rfef.es/sites/default/files/pdf/circulares/Circular_27_con_anexos.pdf --doc-id rfef-circular-27-2025-26
certutil -hashfile data\knowledge\rfef-circular-27-2025-26\original.pdf SHA256
```
Debe imprimir `original.pdf sha256=b05def071123… bytes=5326803`. Si el hash o el tamaño difieren, la RFEF ha reemplazado el archivo: la ingesta lo tratará como **versión nueva** (la anterior se conserva) y las páginas citadas deberán revisarse.
4. **Prueba en seco** (extrae y fragmenta, no toca la base):
```bat
python -m futsal.cli knowledge-ingest --source rfef-circular-27-2025-26 --dry-run
```
Debe imprimir `[dry-run] rfef-circular-27-2025-26: 220 páginas, 44 fragmentos`.
5. **Ingesta real** (única orden que escribe, solo en las tablas `kb_*` de tu base; idempotente):
```bat
python -m futsal.cli knowledge-ingest --source rfef-circular-27-2025-26
```
Primera vez: `rfef-circular-27-2025-26: new versión=N fragmentos=44 páginas=220` y el aviso `páginas 24-220 no indexadas: texto idéntico al documento fifa-futsal-laws-2025-26 (p. 24-220 = p. 1-197)`. Si la repites: `unchanged` (no duplica nada). `--force` reconstruye los fragmentos de la misma versión.
6. **Verificar la indexación y que el reglamento FIFA no cambió**:
```bat
python -m futsal.cli knowledge-status
```
Esperado: la línea de FIFA idéntica a la del paso 2 y `rfef-circular-27-2025-26 vN ACTUAL índice=indexed extracción=ok páginas=220 fragmentos=44`, más una ejecución `new` (y `unchanged` si repetiste).
7. **Consultas de ejemplo** (sin LLM ni cuota):
```bat
python -m futsal.cli knowledge-search "qué comunica la circular 27 de la RFEF" --limit 3
python -m futsal.cli knowledge-search "césped artificial competiciones nacionales" --limit 3
python -m futsal.cli knowledge-search "saque de esquina cuadrante" --limit 3
python -m futsal.cli knowledge-search "fecha circular Las Rozas Zúrich" --doc-id rfef-circular-27-2025-26 --limit 3
```
Esperado: la 1.ª y la 4.ª devuelven primero `Circular n.º 27 de la RFEF…` (p. 1 y p. 2); la 2.ª devuelve la circular (p. 3, regla 1) y el reglamento FIFA (p. 15, regla 1); la 3.ª, `Reglas de Juego del Futsal 2025/26` regla 17. Cada línea muestra documento, versión, regla/parte y página.
8. **Plan del evaluador** (sin LLM ni cuota; reinicia antes la API en la ventana 2 con `uvicorn futsal.api.app:app_factory --factory --port 8000`):
```bat
python -m futsal.agent.evaluate --mode live --suite all --plan --only circular27_comunica,circular27_modificaciones,circular27_cesped,circular27_cesped_coloquial,circular27_fechas_rfef_fifa,circular27_sin_evidencia_rffm
```
Debe imprimir `Base de conocimiento: fifa-futsal-laws-2025-26, rfef-circular-27-2025-26`, 6 casos (5 evaluables en el plan; el sin evidencia necesita LLM real y **sí consume cuota** en live), ≈ 10 llamadas previstas y ≈ 36.600 tokens de entrada estimados (en mi entorno). La ejecución `--mode live` no forma parte de esta tarea: requiere tu autorización expresa.

### Si algo falla
- **Descarga** (`Error: descarga fallida: …` / `el contenido descargado no es un PDF`): sin conexión, proxy o la RFEF devuelve una página HTML; abre la URL en el navegador, repite con `--refresh`, o descarga el PDF a `data\knowledge\rfef-circular-27-2025-26\original.pdf` a mano y repite desde el paso 3 (omite `knowledge-fetch`).
- **Extracción** (`Error: ExtractionError: PDF cifrado` / `no se pudo leer el PDF`): archivo corrupto o incompleto: compara bytes y hash con el paso 3 y vuelve a descargarlo. `--dry-run` reproduce el error sin escribir.
- **Indexación** (`failed` en la salida, `índice=failed` en `knowledge-status`, `error=` en la última ejecución): la versión no queda como actual y el documento anterior no se toca; corrige y repite el comando (reindexa). `Error: OperationalError`: PostgreSQL parado (`docker compose up -d --wait db`, revisa `DATABASE_URL` en `.env`). `relation "kb_…" does not exist`: falta `alembic upgrade head`. `tramos de páginas solapados/fuera del documento`: el manifiesto no coincide con el PDF (¿cambió el número de páginas?).
- **No aparece en las búsquedas**: `knowledge-status` debe mostrar `ACTUAL` e `indexed`; prueba con `--doc-id rfef-circular-27-2025-26`; recuerda que la búsqueda es léxica (usa palabras del texto: «circular», «RFEF», «Bureau», «césped artificial»).

## Limitaciones y mejoras futuras
- Cursiva/tachado y resaltado de cambios no se conservan (ver arriba); recuperar la cursiva permitiría marcar «nuevo/modificado».
- Búsqueda léxica: una pregunta literal puede no priorizar la circular frente al reglamento cuando ambos contienen la frase (césped). Mejoras ya identificadas en la Fase 15 (fusionar fragmentos cortos, ajuste de ranking), no aplicadas aquí.
- No hay normativa propia de la RFFM/RFEF (disciplina, licencias, competición) indexada: las preguntas de ese tipo deben terminar en «no consta» salvo que se incorpore una fuente.
- Un fragmento de 3 caracteres («(…)») y otro de 34 son ruido sin efecto en la búsqueda.
