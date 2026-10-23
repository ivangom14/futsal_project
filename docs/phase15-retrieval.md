# Fase 15 (RAG) — Entrega 2: persistencia, indexación y recuperación

## Modelo de datos (migración `0004`, sin tocar las tablas deportivas)
| Tabla | Contenido |
|---|---|
| `kb_documents` | `doc_key` estable, título, URL, tipo, idioma, emisor, `publication_date`, `effective_date`, versión, temporada, perfil de estructura. Lo que no consta queda `NULL`. |
| `kb_document_versions` | una fila por contenido (`UNIQUE(document_id, content_hash)`): ruta del original, bytes, páginas, estados de extracción (`ok/partial/failed`) e indexación (`indexed/failed`), `is_current` (índice único parcial: una sola vigente), configuración de fragmentación e informe de calidad (JSONB). |
| `kb_chunks` | texto, regla/apartado/subtítulo, `page_start/page_end`, `search_vector` (columna generada, GIN) |
| `kb_ingestion_runs` | cada ejecución: resultado (`new/unchanged/updated/restored/reindexed/failed`), versión, avisos, error |

Extensiones: `unaccent` y `pg_trgm` (ambas incluidas en PostgreSQL 16, imagen `postgres:16.4` del Compose). Función `kb_unaccent` (IMMUTABLE)
para usarla en la columna generada y en el índice trigrama. El archivo original NO va a PostgreSQL: se copia a
`data/knowledge/<doc_key>/<hash16>.pdf` y la BD guarda la ruta y el hash.

## Versiones e idempotencia
- Mismo hash → `unchanged` (no extrae ni escribe fragmentos; solo actualiza metadatos y registra la ejecución).
- Hash nuevo → se extrae, fragmenta e indexa en **una transacción**; solo entonces releva a la versión vigente. Si algo falla, la vigente no se toca
  y la ejecución queda `failed` con el error.
- Las versiones anteriores **conservan sus fragmentos** (historial auditable); la búsqueda usa solo la vigente salvo `include_history`.
  Nunca se mezclan fragmentos de versiones distintas en una consulta por defecto. Volver a un contenido antiguo → `restored`.
- `--force` o una versión marcada `failed` → `reindexed` (se reconstruyen los fragmentos de esa versión).

## Fragmentación (`chunking.py`, configurable)
Unidad = apartado numerado (o parte sin numerar); los subtítulos se conservan como línea. Objetivo 1.200 caracteres, máximo 1.800,
mínimo 500 para cortar en un subtítulo, solape de 250 con **párrafos completos**; un párrafo solo se divide (por frases) si él solo supera el máximo.
Un apartado corto es un único fragmento, de modo que regla y excepción contiguas no se separan. PDF de FIFA: 267 fragmentos
(mediana 1.095, máx. 1.788 caracteres); todos los párrafos quedan íntegros en algún fragmento.

## Recuperación (`search.py`)
`to_tsvector('spanish', unaccent(...))` con título/apartado ponderados (A) sobre el texto (B); consulta OR ordenada por `ts_rank_cd`
+ 1,0 si el fragmento contiene todos los términos; respaldo por trigramas si no hay coincidencias. Valida la consulta (2–500 caracteres,
`limit` 1–10), filtra por documento, versión, idioma y regla, limita el texto por resultado (1.200) y el total (6.000) e indica `truncated`.
Devuelve documento, versión, regla, apartado, subtítulo, páginas, puntuación y método. Consulta parametrizada (sin SQL dinámico con la entrada del usuario).

## Evaluación preliminar (offline, 25 preguntas con evidencia + 6 sin evidencia)
Evidencia de cada pregunta verificada contra el texto del PDF original (pdftotext) y por prueba automática. Medido en una BD temporal
(no la real), sin LLM:

| Consulta | text@1 | text@3 | text@5 | MRR | page@5 |
|---|---|---|---|---|---|
| Pregunta literal del usuario | 0,32 | 0,56 | 0,60 | 0,44 | 0,76 |
| Reformulada con el vocabulario del reglamento* | 0,52 | 0,76 | 0,88 | 0,66 | 0,92 |

Por categoría (text@5, literal): literales 0,89 · coloquiales 0,40 · distinguir 0,50 · excepciones 0,00 · multi-fragmento 1,00.
Latencia mediana 6,5 ms, máx. 70 ms. *La reformulación es manual: simula lo que haría el agente al llamar a la herramienta; no es una medida del agente.

### Fallos detectados
1. **Vocabulario coloquial** («córner», «pista», «roja», «cambios», «dura») no coincide con el reglamento («saque de esquina», «terreno de juego»,
   «expulsión», «sustituciones», «duración»). Es el principal fallo; el léxico no tiene sinónimos ni semántica.
2. **Palabras genéricas**: `ts_rank_cd` no pondera por frecuencia inversa; «jugador», «partido», «balón» dominan.
3. **Sin umbral de evidencia**: la puntuación máxima de las preguntas sin respuesta (0,91) iguala a la de las que sí la tienen (mediana 0,90);
   no se puede decidir «no hay evidencia» por puntuación. La ausencia debe decidirla el agente leyendo los fragmentos (y las bases de competición no indexadas).
4. Fragmentos cortos exactos (R11: «No existe la infracción de fuera de juego…») pierden frente a fragmentos largos con las palabras dispersas.

### Alternativas probadas (no adoptadas) y por qué
- Expansión de sinónimos añadida a la consulta: sin mejora (0,60 → 0,60; MRR baja), añade ruido.
- Ponderación IDF por cobertura de términos: peor (text@5 0,48).
- Tamaño de fragmento 400/600/800 caracteres: no mejora (0,52–0,56 frente a 0,60).
- Normalización de `ts_rank_cd` y peso del bonus: diferencias de ±0,05 sobre 25 preguntas, dentro del ruido; no se ajustan para no sobreajustar la evaluación.

### Siguientes opciones (decisión pendiente, según resultados de la Entrega 3/4)
A. Mantener la búsqueda léxica y que el agente reformule con el vocabulario del reglamento (la descripción de la herramienta lo pedirá). Coste: ninguno; riesgo: depende del modelo; se mide offline con la columna «reformulada» y online solo con tu autorización.
B. Híbrido con `pgvector`: requiere imagen Docker con la extensión (p. ej. `pgvector/pgvector:pg16`) y un modelo de embeddings (local ~cientos de MB o API con cuota).
   Solo se justificaría si A no basta; la columna de embeddings se añadiría en una migración 0005 sin tocar lo existente.

Segunda fuente (Circular 27 de la RFEF, sin duplicar el anexo idéntico al reglamento FIFA): `docs/phase15-circular27.md`.
Criterio de decisión A/B y comandos de la medición online: `docs/phase15-agent.md` (Entrega 4). Sin ejecutar todavía.
