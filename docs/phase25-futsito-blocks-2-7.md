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

## Alineaciones (visual `lineup`)
Pregunta por la alineación → quinteto inicial sobre una pista de fútbol sala (SVG propio) y suplentes a la derecha (apilados en móvil).
- Datos: `get_match_report` con `sections=["players"]` (el prompt lo indica). El acta trae titulares/suplentes, dorsal, capitán y portero
  (en las 13 actas locales: 1 portero + 4 jugadores titulares por equipo). **No trae posiciones** (cierre, alas, pívot): la colocación en la pista
  es orientativa, sin etiquetas de posición, y el propio visual lo avisa. Solo se distingue al portero.
- Cambio mínimo en la API (solo lectura): `/matches/{id}/report` añade `"goalkeeper": true` a los porteros en `players` (se omite en el resto).
- `presentation.lineup_visuals`: equipo(s) = el mencionado en la pregunta, si no el del foco, si no ambos; portero primero y luego por dorsal;
  suplentes con los porteros primero; «sin titularidad indicada» aparte. Con ≠ 5 titulares o sin exactamente un portero (`court: false`) no se dibuja la
  pista: se listan los titulares y se avisa. Sin titulares, sin acta o sin la sección de jugadores no hay visual. Fuente: «Acta del partido · RFFM»
  (sin `group_id` no se conoce su frescura: se declara).
- Contrato real compartido: `tests/fixtures/chat_response_lineup.json`. Sugeridas tras un acta: goles, tarjetas y árbitros de ese partido.

## Visuales en lugar de texto (Fase 25.3)
- **Árbitros** (`officials`): un monigote vestido de árbitro por persona (negro con cuello amarillo y silbato; asistente en amarillo; mesa en azul
  con cronómetro) con su cargo **tal cual figura en el acta**.
- **Goles** (`timeline`): por minuto, local a la izquierda y visitante a la derecha; el gol en propia puerta cuenta para el rival.
- **Tarjetas** (`cards`, Fase 25.4): ver «Tarjetas» más abajo.
- **Partidos** (`matches`): resultados, próximos partidos o jornada (≥ 2 filas, máx. 12, «se muestran N de M»); un programado con fecha pasada avisa de datos
  posiblemente desactualizados. La forma de un equipo (`team_id` + finalizados) sigue siendo el visual `form`; no se duplican.
- Orden en un acta: alineaciones, árbitros, goles. Todo sale de `get_match_report`/`list_matches` con las secciones `players`, `officials` y `goals`.
- **El gráfico sustituye al texto**: la aplicación lo decide en código y se lo dice al LLM dentro del resultado de la herramienta
  (`visual_note`: «ya lo muestro como gráfico: no lo repitas en texto; responde con una o dos frases con lo esencial»). Solo se añade cuando el visual SÍ se va a
  generar (mismas funciones; prueba de propiedad) y nunca cuando no hay datos suficientes, así que no se pierde información. Si el modelo aun así lo repite, no se
  corrige a posteriori: la obediencia del modelo solo se comprueba con Gemini real (guion manual).

## Tarjetas (Fase 25.4)
El acta solo trae `codigo_tipo_amonestacion` y `segunda_amarilla` (la página no etiqueta los códigos). Significado **confirmado por el responsable del proyecto**:
`100` = amarilla · `100` con `segunda_amarilla=1` = «2.ª amarilla (expulsión)» · `101` = roja directa · cualquier otro código = `unknown` (se muestra
como «Código sin interpretar», sin color, y se cuenta aparte). Comprobado contra las 13 actas locales: 64 amarillas, 7 segundas amarillas (las 7 tenían una `100/0` previa del
mismo jugador en el partido) y 4 rojas directas.
- Dominio: `domain/cards.py` (`card_kind`, etiquetas, resumen). API (solo lectura): `card_kind` en `/matches/{id}/report?sections=cards` y en
  `/groups/{id}/match-events?event_type=card`, `cards_summary` por equipo y nota actualizada; `source_code` se conserva.
- Visual `cards`: un acta se dibuja a dos lados (local/visitante) por minuto; varios partidos, como lista con equipo y jornada y aviso si está recortada. Cada tarjeta
  lleva icono de color **y** texto («Amarilla», «2.ª amarilla (expulsión)», «Roja directa») y un resumen («2 amarillas · 1 segunda amarilla (expulsión) · 1 roja directa»).
- Mismo contrato que el resto: `visual_note` indica al LLM que no repita las tarjetas en texto (solo si el visual se genera); las sugeridas ya no proponen lo que se está viendo.
- Evaluador: el color de una tarjeta es legítimo si lo respalda un `card_kind` devuelto (`criteria.unsupported_card_claims`: amarilla ← yellow/second_yellow; roja ← red); antes
  cualquier color era una invención. `acta_tarjetas_codigo` pasa a exigir solo el recuento.
- Pendiente de comprobar con datos reales: que no aparezcan códigos distintos de 100/101 en otras competiciones (saldrían como `unknown`).

## Respuestas lentas o colgadas (Fase 25.2)
Síntoma: «Futsito está consultando los datos…» durante mucho tiempo. Causas posibles (no se pueden distinguir sin ver el desglose): Gemini lento o
limitando peticiones (los reintentos 429/5xx esperan hasta `LLM_RETRY_MAX_WAIT`=20 s cada uno y cada llamada del bucle puede reintentar), muchas
llamadas por pregunta (cada una reenvía prompt + herramientas + contexto) o preguntas encoladas: el servidor atiende **una a la vez** y una pregunta
antigua (p. ej. tras recargar la página) seguía ocupando el turno.
Medido: el flujo real (MCP stdio + agente + chat + cliente HTTP de Gemini simulado) responde en < 1 s; el prompt creció ≈ 100 tokens por llamada desde antes de Futsito
(las alineaciones, ≈ 20), despreciable frente al resto.
Cambios: la llamada al LLM va en un hilo (no bloquea ni impide cancelar); **tiempo máximo por pregunta** (`CHAT_TIMEOUT_SECONDS`, 90 s) → error amigable
504 y turno liberado; `/chat/health` informa `queue` (`busy`, `running_for_s`, `waiting`); logs `futsal.chat`/`futsal.agent` en la ventana del servidor (pregunta
recibida/resuelta con su duración y cada reintento del LLM); `diagnostics` desglosa `llm_ms`, `retry_wait_ms`, `llm_retries`, `tools_ms`, `context_ms`,
`postprocess_ms` e `input_tokens_peak` («Detalles técnicos → Tiempo»); el frontend avisa tras 20 s y permite **Cancelar** (el servidor lo libera por su tiempo máximo).
No se aumentan los reintentos ni sus límites.

## Contrato (todo compatible; campos nuevos)
`POST /chat` → `answer_kind`, `is_analysis`, `sources[]` (`kind` = `official_data|document`, `cited`, `queried_at`, `updated_at`, `freshness`, `updated_note`),
`visuals[]`, `suggestions[]`, `focus`, `diagnostics.resolved_scope`. Petición: `focus` opcional. Nuevo `POST /chat/feedback`. Fixtures reales compartidos con
el frontend: `tests/fixtures/chat_response.json` y `chat_response_visuals.json` (los verifica el backend).

## Pruebas (sin cuota de Gemini)
`tests/test_futsito_presentation.py` (27, incluye alineaciones: foco, fuentes, frescura, citas, visuales, sugeridas, aislamiento), `tests/test_feedback.py` (14), Vitest 48
(visuales con el contrato real, fuentes y frescura, sugeridas, foco, valoración, persistencia, logo). Batería: 639 pass, 2 skipped; ruff y mypy limpios; el
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
8. «¿Cuál fue la alineación del DOSA en su último partido?» → pista con el portero y 4 jugadores, capitán marcado y suplentes a la derecha, con el aviso de que las posiciones son orientativas; «¿y la del rival?» → el otro equipo.
9. «¿Quiénes fueron los árbitros de [partido]?» → monigotes con su cargo; «¿Cuáles son los próximos partidos de [grupo]?» → tarjetas con fecha y estado; «¿Quién marcó en [partido]?» → goles por minuto. En todos, el texto debe limitarse a una o dos frases (si repite la lista, dímelo).
10. Móvil: `F12` → modo dispositivo; menú ☰, sin desplazamiento horizontal.

## Limitaciones
- Las sugeridas son reglas fijas: no cubren todos los tipos de consulta (p. ej. actas, árbitros) y no analizan el texto de la respuesta.
- `cited` y `is_analysis` son heurísticas léxicas; el foco de «máximo goleador» asume que la pregunta lo dice así.
- Los visuales dependen de lo que las tools devolvieron en esa consulta (p. ej. la forma de un equipo, de los partidos listados; sin históricos de posición).
- Frescura: depende de que la API de datos responda `/groups/{id}/freshness`; si no, se declara que no se conoce.
- El logo es provisional. Valoraciones solo en un archivo local (sin panel de análisis). Nada validado con Gemini real ni en Windows desde este entorno.
