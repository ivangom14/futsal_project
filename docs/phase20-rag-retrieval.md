# Fase 20 — Recuperación de reglamentos y circulares (RAG)

Solo offline: **sin LLM, sin scraping, sin cambios en datos ni migraciones**; el RAG sigue siendo léxico (PostgreSQL FTS + trigramas), sin embeddings ni
servicios externos. La medición live de referencia (`docs/phase17-evaluation.md`, 53/65) no se modifica.

## Auditoría del flujo (dónde se perdía la evidencia)
Índice reconstruido en una BD temporal con los tres PDF (FIFA, Circular 27, Circular 10) y medido sin LLM, con la pregunta literal, con la consulta de
referencia de cada caso y con 39 variantes coloquiales plausibles (`FIXED_VARIANTS`/`KEPT_VARIANTS` en los tests).

| Caso (live fallido) | ¿Con la consulta de referencia? | Causa en el flujo | Tratado |
|---|---|---|---|
| `regla_roja_mano` | evidencia en el puesto 2 (FIFA p. 85) | recuperación: la consulta literal también acierta; el fallo live dependió de la consulta del modelo. La Circular 27 (p. 9-10, resumen de cambios) cita el mismo texto y puede salir antes | sin cambio (ya recuperable); se prueba la cita |
| `circular10_saque_meta_coloquial` | puesto 1 | vocabulario: «portero» ≠ «guardameta» (la circular solo dice «guardameta»); la pregunta literal no la recupera (hueco semántico «de un lado al otro del campo» ↔ «línea de medio campo») | parcial: «portero» sí |
| `circular10_video_solicitudes` | puesto 2 | ranking: los fragmentos genéricos FIFA sobre revisión en vídeo desplazan a la disposición de la circular cuando la consulta es «revisión de vídeo solicitudes por equipo» | **no resuelto** (ver límites) |
| `circular10_penal_no_acumulable` | puesto 3 | vocabulario: el stemmer reduce «penalti» a «penalt» y la circular dice «tiro penal»/«penal» | sí |
| `circular10_distingue_fifa` | circular puesto 1 + FIFA p. 12 con la 2.ª búsqueda | vocabulario («portero», «línea de medio campo» frente a «línea central» en FIFA p. 12) y necesita dos documentos | parcial |
| `circular10_sin_evidencia_circular9` | n/a | respuesta: la Circular 9 no está indexada y nada lo decía en el resultado; el modelo afirmaba su vigencia | sí: aviso explícito en el resultado |
| `regla_sin_evidencia_salario` | n/a | alcance: «¿cuánto cobra un árbitro…?» no usa vocabulario de reglamento y el agente respondía «no dispongo» sin buscar | sí: reconsulta única |
| `circular27_modificaciones` | puesto 2 | ninguna (se recupera; se mantiene como control) | control |

Eslabones auditados: la detección de ámbito (`mentions_rules`), el vocabulario/expansión (nuevo), la búsqueda FTS (`search.py`), el ranking, la fragmentación
y los metadatos (sin cambios: los fragmentos con la evidencia existen y llevan documento, regla y páginas correctos) y el contexto que recibe el agente
(`payload`). La fragmentación e indexación **no** eran la causa en ningún caso.

## Cambios
- `knowledge/vocabulary.py` (nuevo): tabla explícita de 4 reglas, cada una con su motivo: `portero→guardameta`, `penalti→penal`, `medio campo→central` y `línea central→medio campo`.
  Disparadores por **palabra exacta** (un primer intento con prefijos disparaba «portería» y empeoraba un caso del conjunto de evaluación). Probadas y **descartadas** por no aportar o
  empeorar: `cambios→sustituciones` (rompía «cambios en las reglas»), `pista→terreno` y `córner→esquina` (sin mejora medida).
- `knowledge/search.py`: la expansión solo AÑADE términos: los de la pregunta se conservan y, sin disparador, la consulta es idéntica. Las reglas de una palabra entran como alternativa
  en la condición «contiene todos los términos» (el fragmento con «guardameta» cumple una pregunta con «portero»); las de varias palabras solo amplían la búsqueda OR.
  Circular citada (`circular 10`, `circular n.º 10`…): sus fragmentos puntúan +2 (por encima del bonus de «todos los términos») para que la disposición específica vaya antes que la regla general,
  sin excluir esta. Cada resultado incluye el tipo de documento (`type`: `reglamento`/`circular`).
- `api/knowledge.py`: `type` por fragmento y `cited_circulars` (`indexed` sí/no); si se cita una circular que no está en la base, la nota dice que ningún fragmento procede de ella y que no se afirme su
  contenido (lo que otro fragmento diga de ella —p. ej. que la anula— se cita con su documento y página).
- `agent/criteria.py`: `mentions_rules` también reconoce el **par** árbitro + cobro («cuánto cobra un árbitro», «sueldo de un colegiado») para pedir UNA consulta a `search_rules` antes de aceptar
  «no dispongo». Solo el par: «cuánto cobra Messi» sigue ajeno, «quién arbitró el partido» sigue siendo un dato de acta y «FIFA» o «circular» sueltas no convierten nada en reglamentaria.
- Prompt del agente, límite de herramientas y guardias de alcance: sin cambios.

## Resultados (sin LLM)
Evaluación separada por capa. **Recuperación** = la evidencia verificada está en los 5 primeros; **citas** = documento y página del fragmento son los esperados; **respuesta** = no medible sin LLM.

| Medida | Antes | Después |
|---|---|---|
| Casos de reglamento respondibles recuperados con la consulta de referencia (6) | 6/6 | 6/6 |
| Citas correctas (documento y página esperados) en esos 6 | 6/6 | 6/6 |
| Variantes coloquiales con la evidencia en el top 5 (39 variantes de 6 casos) | 18/39 | 26/39 |
| Pregunta literal de `circular10_penal_no_acumulable` | no | **sí** |
| Circular 10, literal text@5 / page@5 (8 preguntas) | 0,50 / 0,625 | **0,75 / 0,75** |
| FIFA, literal text@5 · reformulada text@5 | 0,60 · 0,88 | 0,60 · 0,88 (sin cambio) |
| Circular 27 (literal · reformulada text@5) | 0,667 · 1,0 | 0,667 · 1,0 (sin cambio) |
| FIFA text@1 / MRR literal | 0,28 / 0,417 | 0,24 / 0,397 (−1 pregunta: `segundo_punto` baja del 1.º al 2.º puesto por `penalti→penal`) |
| Sin evidencia: `salario` sin ningún fragmento con la respuesta; Circular 9 marcada «no indexada» | — | sí |
| Respuesta final de los 8 casos | no medible offline | **no medida** (requiere LLM; recomendación abajo) |

## Pruebas
- `tests/knowledge/test_vocabulary.py` (sin BD): tabla pequeña y justificada, disparadores por palabra exacta (incluye el falso disparo «portería»), reglas multipalabra, preguntas sin disparador intactas, referencias a circulares y aviso de circular no indexada.
- `tests/integration/test_knowledge_vocabulary_pg.py` (PostgreSQL temporal, fragmentos literales): consulta sin disparador idéntica; cada expansión comparada con y sin ella (la disposición específica pasa a cumplir «todos los términos»); circular citada antes que la regla general
  y esta sigue disponible; circular 9 no indexada avisada; `type` reglamento/circular; pregunta sin evidencia sin fragmento que la responda.
- `tests/integration/test_knowledge_retrieval_real_pg.py` (PDF reales; se omite si faltan): recuperación y citas de los 6 casos respondibles, disposición de la circular no sustituida por la regla general, 7 variantes que ahora se recuperan (fallan sin la Fase 20),
  8 que ya se recuperaban y no se pierden, sin evidencia (salario, Circular 9) y suelos de las tres métricas del conjunto de evaluación.
- `tests/test_agent_rules_scope.py` (+3): el par árbitro + cobro se consulta antes de «no consta»; «cuánto cobra Messi» sigue rechazado sin ir a `search_rules`; ninguna otra palabra suelta activa la consulta.
- Comprobado que los tests de variantes y de expansión **fallan** con la tabla vacía (13 fallos) y pasan con ella.

## Validación
Suite completa con un PostgreSQL desechable local (fuera del repo) y los PDF reales: **602 passed, 2 skipped** (live). Sin PostgreSQL: 438 passed, 166 skipped. `ruff check .` limpio; `mypy` sin incidencias (82 ficheros). Fase 19 intacta (sin cambios en `api/app.py`, MCP, `agent/agent.py`, migraciones ni datos).

## Límites y casos pendientes
- La disposición de la circular puede seguir perdiendo frente a fragmentos genéricos FIFA cuando la consulta no usa su vocabulario (`circular10_video_solicitudes` con «revisión de vídeo solicitudes por equipo»; la pregunta literal de `circular10_saque_meta_coloquial`; la
  pregunta literal de `circular10_distingue_fifa`, que además exige dos documentos). Un ranking que lo arreglara sería sobreajuste con 25+14 preguntas (ya descartado en la Entrega 2); lo siguiente, si hace falta, es la opción B de `docs/phase15-retrieval.md` (híbrido con pgvector), no
  más reglas léxicas.
- `regla_roja_mano`: la Circular 27 (resumen de cambios) repite el texto de la regla 12 y a veces sale antes que la FIFA; ambas citas son legítimas pero distintas (la respuesta debe atribuirlas a su documento).
- La expansión no distingue sentidos: `penalti→penal` añade fragmentos sobre tiro penal a toda consulta con «penalti» (coste medido: −1 puesto en una pregunta).
- Las reglas se validaron con PDF y fixtures de este repositorio; otros documentos pueden necesitar las suyas.
- La vigencia de una circular solo se afirma si un fragmento lo dice; no se deduce.
