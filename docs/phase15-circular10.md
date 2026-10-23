# Fase 15 — Circular 10 (rectificativa) de la CTAFS de la RFEF, temporada 2026/2027

Tercer documento de la base de conocimiento, con los mismos criterios que la Circular 27 (`docs/phase15-circular27.md`): documento independiente, mismo `knowledge-ingest`, misma API y herramienta `search_rules`; sin pgvector, embeddings, herramienta nueva ni migración.
Verificado en el entorno de Claude (Linux) sobre **copias** de la base réplica; nada se ha ejecutado en tu base local, en tu Windows ni con Gemini.

## Fuente y metadatos (`src/futsal/config/knowledge_sources.py`, clave `rfef-circular-10-rectificativa-2026-27`)
| campo | valor | origen |
|---|---|---|
| Emisor | Comité Técnico de Árbitros de Fútbol Sala (CTAFS) de la Real Federación Española de Fútbol (RFEF) | firma «Presidente CTAFS de la RFEF» (p. 12) |
| Documento | Circular n.º 10 (Rectificativa): Reglas de Juego y criterios arbitrales, temporada 2026/2027. **Anula y sustituye a la Circular n.º 9 (17-ago-2026)** | p. 1 |
| URL | la indicada por el usuario (`…modificacion_26-08-2026_1.pdf?cb=d02708b3`; el parámetro `cb` solo evita la caché) | usuario |
| Fecha de publicación | 2026-08-26 («Las Rozas de Madrid, 26 de agosto de 2026»); metadatos del PDF: creado el 25-ago-2026 | p. 12 |
| Entrada en vigor | no consta (solo «aplicables … en la temporada 2026/2027») → vacío | p. 1 |
| Ámbito declarado | «todas las competiciones organizadas por la Real Federación Española de Fútbol». No menciona competiciones de la RFFM ni de otras federaciones territoriales | p. 1 |
| Tipo / idioma / perfil | `circular` / `es` / `rfef-ctafs-circular` | — |
| Versión | «Circular 10 rectificativa · 2026/27» (la versión técnica es el hash) | — |
| Hash SHA-256 (descarga del 2026-10-23) | `f5ebf94cb9ed8c7fad74ed8bc7b82186b3d549c55748e40dee82338ea4d3c134`, 413.938 bytes, 12 páginas | `source.json` / BD |
| Estado | `extracción=ok`, `índice=indexed`, 28 fragmentos | `knowledge-status` |

No se toca ningún documento anterior (FIFA 2025/26 y Circular 27): lo comprueba un test (versión, hash y fragmentos idénticos antes y después).
**La Circular 9 no está en la base** (solo se sabe que esta la anula): el agente no debe inventar su contenido.

## Estructura del PDF (12 páginas, todas indexadas)
Documento único de texto propio, sin anexos: portada y ámbito (p. 1), **I. Modificaciones a las Reglas de Juego** (1. saque de meta, 2. tarjetas por protestas del banquillo = falta acumulable, 3. faltas con penal no acumulables, 4-5. vídeo en segunda amarilla y desde la 5.ª falta; p. 1-2), **II. Medidas para la dinamización del juego** (7 apartados; p. 2-5, algunos solo para Liga Prime Futsal y Primera División), **III. Protocolo de soporte de vídeo** (p. 6-8), **IV. Distancia en saque de banda y esquina** (p. 8), **V. Manos** (p. 8-9), **VI. Evitar un gol u ocasión manifiesta** (p. 10-11), **VII. Uso de brazos** (p. 11), **VIII. Sistemas electrónicos en banquillos** y firma (p. 12).

### Solapamiento
Comprobado frente a los dos documentos ya indexados con 6-gramas de palabras: **12,4 % global** de coincidencia y como máximo 39,5 % en un fragmento (el que cita reglas de la FIFA). Es contenido propio de la RFEF (enmiendas y criterios), no una copia: **se indexa entero** y no hay nada que excluir.

### Cambio de código (mínimo, genérico)
- `StructureProfile.part_heading_line` + perfil `rfef-ctafs-circular`: cada «I. TÍTULO» abre una parte y cada «1. Título» (sin coma ni punto final y con numeración correlativa) un apartado; así los pasos de listas («1. Preguntarán al lesionado…») no se confunden con apartados. Test con texto sintético y comprobación sobre el PDF real.
- La `note` de la API y la descripción de `search_rules` dejan de nombrar documentos concretos («circulares de la RFEF») y añaden: una circular solo rige lo que su texto dice (no es normativa de la RFFM salvo que lo indique) y, si dos documentos difieren, se presentan por separado sin decidir cuál prevalece salvo que el texto lo diga.
- `agent/rule_cases.py`: 8 casos `circular10_*`; el plan de referencia admite varias búsquedas (la comparación entre documentos necesita dos).

## Resultados de extracción
- 12/12 páginas con texto, 0 caracteres ilegibles, 0 avisos, 0 páginas con poco texto o posible tabla, numeración impresa = física (la cita es la página física). 23 segmentos → **28 fragmentos** (media ≈ 745 caracteres; 2 de menos de 200: «(Sólo partidos con SV)» y la falta con penal, 177).
- Cada fragmento conserva parte (I-VIII), apartado (número y título) y página. Apartados detectados: I.1-5, II.1-7, III.1-4 (la circular repite «Aspectos prácticos» como 3 y 4); IV-VIII sin numeración interna. Subapartados «A) MANOS», «B) CON CONTACTO FÍSICO» quedan en el texto, no como campo.
- **Defectos de extracción** (pypdf): espacios espurios en algunas palabras («s aque», «protesta s» en títulos; «conced a», «e starán» en el texto, 2 casos en el cuerpo). Afectan solo a esas apariciones; la búsqueda léxica sigue encontrando el resto.
- Tablas/listas: no hay tablas; las listas de pasos se conservan como texto.

## Evaluación de recuperación (sin LLM; `tests/fixtures/knowledge_eval_circular10.json`; evidencia comprobada contra los PDF por test)
| pregunta (resumen) | respuesta esperada | evidencia | literal | reformulada |
|---|---|---|---|---|
| Circular que anula la 10 | La Circular 9 (17-ago-2026), sin efecto | p. 1 | 1 | 1 |
| Restricción del guardameta en el saque de meta (con «Circular 10») | No pasar el medio campo sin que lo toque otro jugador o el suelo en su mitad; si no, libre indirecto desde la línea central, sin ventaja | p. 1, I.1 | **no** | 1 |
| ¿Puede el portero tirar al otro lado desde el saque de meta? (coloquial) | Igual | p. 1, I.1 | **no** | 1 |
| Protestas del banquillo ¿faltas acumulables? | Sí, acumulan falta | p. 1-2, I.2 | 1 | 1 |
| Solicitudes de vídeo por equipo si se ratifica la decisión | 1 por equipo y período (no acumulable); sin límite si rectifica | p. 7, III.2 | 3 | 2 |
| Falta con penalti ¿acumulada? (fragmento corto, 177 caracteres) | No | p. 2, I.3 | **no** | 3 |
| Ámbito de la circular | Competiciones de la RFEF 2026/27; no dice nada de la RFFM | p. 1 | 1 | 1 |
| Guardameta pasa el medio campo: circular 10 frente a Reglas FIFA | Circular: libre indirecto; FIFA: solo si las reglas nacionales (juvenil, veteranos, discapacidad, base) lo prohíben; citar ambos sin decidir | C10 p. 1 y FIFA p. 12 | **no** | 2 |
| Sanción económica de la RFFM por protestar (sin evidencia) | No consta | — | — | — |
| Contenido de la Circular 9 (sin evidencia) | No consta; solo que está anulada | — | — | — |

Literal text@5 = 4/8 (0,50); reformulada 8/8. Los fallos literales son los de siempre en una búsqueda léxica (vocabulario coloquial y un fragmento de una sola frase). Hallazgos nuevos:
- **Truncado de 1.200 caracteres**: en la Regla 16 del reglamento FIFA (p. 116-117) la frase relevante («prohíban que el guardameta lance el balón más allá de la línea central») queda cortada; solo la ve el fragmento de la p. 12. La comparación necesita **dos búsquedas** (una por documento); con una sola consulta, ninguna trae ambos textos.
- Una discrepancia propia de la FIFA (p. 12 habla de «libre directo» y la Regla 16, p. 116-117, de «libre indirecto») no es de la ingesta; el agente debe citar cada texto con su página.

### Sin perjuicio a los documentos anteriores (mismas preguntas antes y después de añadir la Circular 10)
| | FIFA literal | FIFA reformulada | Circular 27 literal | Circular 27 reformulada |
|---|---|---|---|---|
| text@1 / @3 / @5 | 0,28 / 0,56 / 0,60 | 0,48 / 0,76 / 0,88 | 0,50 / 0,67 / 0,67 | 0,83 / 1,0 / 1,0 |
| page@5 · MRR | 0,76 · 0,417 | 0,92 · 0,635 | 0,67 · 0,583 | 1,0 · 0,917 |

Idénticas antes y después (incluido el MRR). Un test impide que text@5 y page@5 de ambos conjuntos bajen. Se mantiene `limit=5`.

### Evaluación del agente (offline, LLM guionizado; **no evalúa al LLM**)
Ocho casos `circular10_*` por capas (recuperación, respuesta, citas, invención y comportamiento sin evidencia). Con los tres documentos indexados: suite offline completa **38 pass, 0 fail, 11 no evaluables** (49 casos; los 11 no evaluables necesitan LLM real o datos que la base no tiene). Sin la Circular 10 sus 8 casos salen **no evaluables** (nunca aprobados); el de comparación exige además el reglamento FIFA.
Pendiente de evaluación real (con tu autorización y cuota): que Gemini haga las dos búsquedas de la comparación, no dé por aplicable la circular a la RFFM y diga «no consta» para la sanción de la RFFM y el contenido de la Circular 9. Revisión humana: el criterio léxico no detecta que se afirme la aplicación a la RFFM sin decirlo con palabras clave, ni la paráfrasis engañosa en los casos sin evidencia.

## Alcance normativo
La Circular 10 se declara aplicable a las competiciones organizadas por la **RFEF** en 2026/27. Para competiciones de la RFFM (p. ej. Tercera División) el documento no acredita nada: el agente debe decir qué dice el texto y que no consta si les aplica. Frente al reglamento FIFA 2025/26, no se establece jerarquía: se citan por separado salvo que el propio texto indique cuál prevalece. La Circular 9 queda anulada según esta circular, pero su contenido no está en la base.

## Coste en tokens
La `note` crece ≈ 230 caracteres respecto a la versión de la Circular 27 (≈ +60 tokens por respuesta de búsqueda); la descripción de la herramienta ≈ +10 tokens por llamada. Caso medio offline: ≈ 7.250-7.650 tokens de entrada (2 llamadas); la comparación, ≈ 14.100 (3 llamadas, 2 búsquedas). `--plan` de los 8 casos: ≈ 13 llamadas y ≈ 51.000 tokens de entrada estimados (no cuenta los 2 sin evidencia).

## Procedimiento en tu Windows CMD (NO ejecutado por Claude; comandos comprobados contra el código en Linux)
Los pasos 1-2 (carpeta, rama, `git pull`, Docker, `alembic upgrade head`, `knowledge-status`) son los de `docs/phase15-circular27.md`; reinicia `uvicorn` tras el `git pull`.
1. **Descargar** (escribe en `data\knowledge\rfef-circular-10-rectificativa-2026-27\`, ignorado por Git; reutiliza el archivo si existe, `--refresh` fuerza la descarga):
```bat
python -m futsal.cli knowledge-fetch --url "https://rfef.es/sites/default/files/circular_10_rectificativa_ctafs_rfef_2026-2027_reglas_de_juego_y_criterios_arbitrales_modificacion_26-08-2026_1.pdf?cb=d02708b3" --doc-id rfef-circular-10-rectificativa-2026-27
certutil -hashfile data\knowledge\rfef-circular-10-rectificativa-2026-27\original.pdf SHA256
```
Debe imprimir `original.pdf sha256=f5ebf94cb9ed… bytes=413938`. Si difieren, la RFEF ha cambiado el archivo: se ingerirá como versión nueva (la anterior se conserva) y habrá que revisar las citas.
2. **Prueba en seco** (no toca la base):
```bat
python -m futsal.cli knowledge-ingest --source rfef-circular-10-rectificativa-2026-27 --dry-run
```
Esperado: `[dry-run] rfef-circular-10-rectificativa-2026-27: 12 páginas, 28 fragmentos`.
3. **Ingesta real** (única orden que escribe, solo en `kb_*`; idempotente):
```bat
python -m futsal.cli knowledge-ingest --source rfef-circular-10-rectificativa-2026-27
```
Primera vez: `new versión=N fragmentos=28 páginas=12` sin avisos; repetida: `unchanged`. `--force` reconstruye los fragmentos de la misma versión.
4. **Verificar** (los anteriores deben seguir igual que antes de este paso):
```bat
python -m futsal.cli knowledge-status
```
Esperado: `rfef-circular-10-rectificativa-2026-27 vN ACTUAL índice=indexed extracción=ok páginas=12 fragmentos=28`, además de `fifa-futsal-laws-2025-26 … fragmentos=267` y `rfef-circular-27-2025-26 … fragmentos=44` sin cambios.
5. **Consultas de ejemplo** (sin LLM ni cuota):
```bat
python -m futsal.cli knowledge-search "saque de meta guardameta línea de medio campo tiro libre indirecto" --limit 3
python -m futsal.cli knowledge-search "circular 10 rectificativa anula sustituye circular 9" --limit 3
python -m futsal.cli knowledge-search "solicitudes soporte de vídeo por equipo y período" --limit 3
python -m futsal.cli knowledge-search "reglas nacionales futsal juvenil veteranos guardameta línea central" --limit 3
```
Esperado: la 1.ª y la 2.ª devuelven primero `Circular n.º 10 (rectificativa) de la CTAFS de la RFEF…` (p. 1, apartados I.1 y portada); la 3.ª, la misma circular (p. 7, III.2); la 4.ª, el reglamento FIFA (p. 12 y Regla 16).
6. **Plan del evaluador** (sin LLM ni cuota; API reiniciada en la ventana 2):
```bat
python -m futsal.agent.evaluate --mode live --suite all --plan --only circular10_anula,circular10_saque_meta_coloquial,circular10_protestas_banquillo,circular10_video_solicitudes,circular10_penal_no_acumulable,circular10_distingue_fifa,circular10_sin_evidencia_sancion_rffm,circular10_sin_evidencia_circular9
```
Debe imprimir `Base de conocimiento:` con los tres documentos y `Casos seleccionados: 8 (evaluables: 6)`. Los 2 sin evidencia **sí consumirían cuota** en live; `--mode live` no forma parte de esta tarea y requiere tu autorización.

### Si algo falla
- **Descarga** (`descarga fallida`, `el contenido descargado no es un PDF`): conexión, proxy o la RFEF devuelve HTML; abre la URL en el navegador, repite con `--refresh`, o copia el PDF a `data\knowledge\rfef-circular-10-rectificativa-2026-27\original.pdf` y salta el `knowledge-fetch`.
- **Extracción** (`PDF cifrado`, `no se pudo leer el PDF`): archivo corrupto; compara hash y bytes y vuelve a descargarlo. `--dry-run` lo reproduce sin escribir.
- **Indexación** (`failed`, `índice=failed`, `error=` en la última ejecución): la versión no queda actual y los demás documentos no se tocan; corrige y repite. `OperationalError`: PostgreSQL parado (`docker compose up -d --wait db`, `DATABASE_URL` en `.env`). `relation "kb_…" does not exist`: `alembic upgrade head`.
- **No aparece en las búsquedas**: debe figurar `ACTUAL` e `indexed`; prueba con `--doc-id rfef-circular-10-rectificativa-2026-27` y palabras del texto («saque de meta», «soporte de vídeo», «protestas»).

## Limitaciones y mejoras futuras
- Búsqueda léxica: vocabulario coloquial y fragmentos de una sola frase pueden quedar fuera del top 5 (3 de 8 preguntas literales); la comparación entre documentos necesita dos búsquedas. Mejoras ya identificadas: fusionar fragmentos cortos, ajuste del ranking, reformular en el agente.
- Texto visible por fragmento limitado a 1.200 caracteres (la frase relevante puede quedar fuera, como en la Regla 16 de la FIFA); no se ha cambiado el límite.
- Espacios espurios de pypdf en algunas palabras; subapartados «A)/B)» sin campo propio; fragmento de 22 caracteres sin valor.
- Sin la Circular 9 ni normativa propia de la RFFM: preguntas sobre ellas deben terminar en «no consta».
