# Futsito — Fase 25: bloques 2 a 7

Continuación de `docs/phase24-futsito-block1.md`. Sin migraciones; MCP, ingesta y Fase 21 intactos; sin LLM en las pruebas.

## Bloque 2 — Memoria contextual (foco de la conversación)
- Sobre la memoria de la Fase 23 (turnos recortados, `resolve_pending`, pistas de ámbito de la Fase 24) se añade un **foco** por conversación:
  equipo, jugador, competición, grupo y jornada de la última consulta **verificada**.
- Se calcula en `agent/presentation.py: compute_focus` solo con llamadas ejecutadas y sin error (argumentos `team_id`, `player_id`, `round_id`,
  `group_id` traducidos a **nombres** con lo que mostraron las tools). «Máximo goleador» fija jugador y equipo desde la tabla oficial.
  Una consulta fallida, incompleta o con guardarraíl no mueve el foco.
- `merge_focus`: lo nuevo manda; cambiar de competición/grupo sin nombrar equipo descarta equipo, jugador y jornada; cambiar de equipo descarta el jugador.
- Se entrega al LLM como nota con **nombres** (nunca IDs) y los otros grupos de la competición («el otro grupo»); los IDs se vuelven a obtener por
  nombre y se validan como siempre. Si el usuario nombra otra cosa, la nota dice que se ignore.
- Servidor autoritativo por `conversation_id` (`ConversationMemory.get_focus/put_focus`); el foco que manda el navegador solo siembra conversaciones
  desconocidas y se sanea (claves conocidas, ≤ 80 caracteres). El frontend lo guarda con cada respuesta (localStorage) y lo reenvía tras recargar.

## Bloque 3 — Fuentes, trazabilidad y fiabilidad
- `answer_kind`: `official` (datos RFFM vía tools), `documental` (RAG), `mixed`, `none`; `is_analysis` marca análisis o recomendaciones del modelo
  (palabras de la pregunta o de la respuesta) → la interfaz muestra «Análisis o recomendación · no es información oficial».
- Fuentes **solo de llamadas ejecutadas** y sin error (las rechazadas por el guardarraíl no cuentan):
  - `official_data`: título por conjunto de datos, competición y grupo (catálogo), `queried_at` y fecha de actualización: la **captura** de la tabla
    oficial o la **frescura de la Fase 21** (`GET /groups/{id}/freshness`, solo lectura, caché 60 s). Sin fecha → `updated_at: null` y
    «No se conoce la fecha de actualización de estos datos.» Si la Fase 21 dice `stale` → «Datos posiblemente desactualizados».
  - `document` (RAG): documento, regla/sección, página, fragmento y `cited`: **true** si la respuesta menciona su regla o página, o copia ≥ 4 palabras
    seguidas del fragmento. Los no citados salen aparte y plegados («Otros fragmentos consultados, no citados en la respuesta»).
- Límite: `cited` es una heurística de correspondencia, no una prueba de que el modelo se apoyara en ese fragmento.

## Bloque 4 — Preguntas sugeridas
Sin llamada extra al LLM: reglas en `presentation.build_suggestions` según la consulta realizada y el foco. Solo propone lo que las tools resuelven
(próximos rivales, últimos partidos, goleadores, clasificación, resumen de una regla citada, circulares). 2-3 distintas, sin repetir lo ya preguntado y
ninguna tras un error, un límite, una limitación o una respuesta sin herramientas. Al pulsar una se envía como mensaje normal del usuario
(solo la última respuesta las ofrece).

## Bloque 5 — Respuestas visuales
Contrato estructurado `visuals[]` construido **solo con datos de las tools** (`result.official` y `list_matches`), nunca interpretando texto:
`table` (clasificación: Pos, Equipo, PJ, G, E, P, GF, GC, DG, Pts; resalta el equipo mencionado), `bars` (goleadores), `comparison` (dos equipos de la
clasificación mencionados en la pregunta), `form` (últimos resultados V/E/D y puntos acumulados **de los partidos mostrados**). Sin datos
suficientes (estado no `ok/incomplete`, <2 partidos, error o límite) no se genera nada; `partial` avisa de datos incompletos. El frontend dibuja con
HTML/SVG propios (sin librería de gráficos) y como texto (sin HTML inyectable); un tipo desconocido se ignora y el texto sigue siendo la alternativa.
No hay evolución de posición por jornada: solo existe la clasificación actual (no se inventa un histórico).

## Bloque 6 — Valoración
`POST /chat/feedback` `{conversation_id, message_id, rating: up|down, reason?, comment?, run_id?}`; motivos: `wrong_data`, `lost_context`, `incomplete`,
`bad_source`, `misunderstood`, `other` (solo con 👎, igual que el comentario ≤ 1000, con secretos enmascarados). Persistencia: **JSONL local**
`data/feedback/feedback.jsonl` (`FEEDBACK_FILE`; `data/` está ignorado por Git; sin base de datos nueva). Cada línea: `ts`, ids, valoración, motivo,
comentario, `run_id` y un resumen de diagnóstico **tomado del servidor** (modelo, herramientas, guardarraíl, turnos de contexto, tipo de respuesta; un
`run_id` desconocido no se acepta). No guarda el texto de la respuesta ni claves. No altera al agente. Análisis rápido (Windows):
`python -c "import json;[print(json.loads(l)) for l in open('data/feedback/feedback.jsonl',encoding='utf-8')]"`.
La valoración se guarda con el mensaje en el navegador y sobrevive a recargas; un fallo al guardar no la da por enviada.

## Bloque 7 — Futsito (identidad y diseño)
- **Logo provisional propio** (`frontend/public/futsito-logo.svg` y `futsito-mark.svg`, mascota osito con camiseta roja y balón de fútbol sala,
  tipografía gruesa con contorno): inspirado en el estilo de la imagen aportada (rojo y marrón cálidos), **sin** reutilizar el escudo de la RFFM, el
  logo de Ibercaja ni su camiseta. Para usar el definitivo basta sustituir esos dos archivos (mismas proporciones: 420×128 y 128×128).
- Paleta `brand` (rojos) y `bear` (marrones/crema) en `tailwind.config.js`; cabecera con el nombre, logo en la barra lateral y en cada respuesta,
  pantalla de bienvenida con ejemplos, estado de conexión discreto, fuentes y detalles técnicos plegados, preguntas sugeridas y valoración bajo cada respuesta.
- Accesibilidad: botones con nombre, grupos y regiones con `aria-label`, `:focus-visible`, tablas con `scope`, gráficos con `role=img` y etiqueta.
- Comprobado en Chromium real (1280×900 y 390×844): sin errores de consola, sin desbordamiento horizontal y logos cargados.

## Contrato (todo compatible; campos nuevos)
`POST /chat` → `answer_kind`, `is_analysis`, `sources[]` (`kind` = `official_data|document`, `cited`, `queried_at`, `updated_at`, `freshness`, `updated_note`),
`visuals[]`, `suggestions[]`, `focus`, `diagnostics.resolved_scope`. Petición: `focus` opcional. Nuevo `POST /chat/feedback`. Fixtures reales compartidos con
el frontend: `tests/fixtures/chat_response.json` y `chat_response_visuals.json` (los verifica el backend).

## Pruebas (sin cuota de Gemini)
`tests/test_futsito_presentation.py` (21: foco, fuentes, frescura, citas, visuales, sugeridas, aislamiento), `tests/test_feedback.py` (14), Vitest 37
(visuales con el contrato real, fuentes y frescura, sugeridas, foco, valoración, persistencia, logo). Batería: 603 pass, 2 skipped; ruff y mypy limpios; el
único error de la suite completa es `test_refresh_runs_pg` (necesita PostgreSQL local; ya fallaba).

## Validación manual con Gemini real (Windows CMD)
```
git pull origin claude/tender-rubin-rsz9uv
python -m pytest tests\test_futsito_presentation.py tests\test_feedback.py -q
cd frontend && npm test && cd ..
```
Reinicia el servicio de chat y recarga con `Ctrl+F5`. Conversaciones nuevas:
1. «¿Quién es el máximo goleador de [competición y grupo]?» → «¿En qué equipo juega?» → «¿Cuántos goles lleva su equipo?» (foco: jugador y equipo).
2. «¿Cómo va el DOSA en liga senior?» → «¿Y cómo va en la clasificación?» → «¿Quién es su máximo goleador?» → «¿Cuándo juega el siguiente?».
   Debe aparecer la tabla de clasificación con el DOSA resaltado, el ranking de goleadores y «Datos actualizados el …» en las fuentes.
3. «Compara el DOSA y el [rival]» → tabla + comparativa. «¿Cómo han quedado los últimos partidos del DOSA?» → resultados V/E/D y puntos acumulados.
4. «¿Cuánto dura cada mitad?» → «Normativa citada» con documento y página; los fragmentos no citados, plegados aparte.
5. «Analiza el rendimiento del DOSA» → insignia «Análisis o recomendación · no es información oficial».
6. Pulsa una pregunta sugerida → se envía como mensaje normal. Cambia a «¿Y el otro grupo?» y a otro equipo: el foco debe cambiar.
7. 👍 y 👎 (con motivo y comentario) → comprueba `data\feedback\feedback.jsonl`. Recarga: la valoración sigue marcada.
8. Móvil: `F12` → modo dispositivo; menú ☰, sin desplazamiento horizontal.

## Limitaciones
- Las sugeridas son reglas fijas: no cubren todos los tipos de consulta (p. ej. actas, árbitros) y no analizan el texto de la respuesta.
- `cited` y `is_analysis` son heurísticas léxicas; el foco de «máximo goleador» asume que la pregunta lo dice así.
- Los visuales dependen de lo que las tools devolvieron en esa consulta (p. ej. la forma de un equipo, de los partidos listados; sin históricos de posición).
- Frescura: depende de que la API de datos responda `/groups/{id}/freshness`; si no, se declara que no se conoce.
- El logo es provisional. Valoraciones solo en un archivo local (sin panel de análisis). Nada validado con Gemini real ni en Windows desde este entorno.
