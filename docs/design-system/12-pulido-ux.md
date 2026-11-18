# 12 · Pulido UX: respuestas sin duplicidad, aclaraciones, clasificación móvil (2026-11-17, sin commit)

## Causa real de la duplicidad
1. **Origen**: `presentation.visual_note` pedía al LLM «una o dos frases con lo esencial» → el modelo narraba 1.º y 2.º. Los listados v2
   (`matches_by_date`, `scorers_ranking`, `teams_ranking`…) no recibían aviso alguno.
2. **Conciliación** (`reconcile.py`): solo evaluaba elementos de lista y filas de tabla; los párrafos nunca se tocaban y, si no quedaba nada,
   añadía «Te lo muestro en la tarjeta…».
3. **Aclaraciones**: el texto (del LLM o `clarification_text`) enumeraba las opciones y el frontend las repetía como botones.

## Estrategia (contrato agente → frontend en un solo punto)
- `reconcile.present_answer(answer, visuals, context, clarification)` decide el texto **visible**; `answer` original intacto en
  `answer_original` (memoria, historial del LLM, fuentes, calificador del benchmark).
  - Aclaración con ≥ 2 opciones reales que el texto enumera → solo la pregunta breve («¿A qué competición te refieres?», con ««X» juega en
    varias competiciones.» si hay equipo). Si el texto dice otra cosa, se conserva.
  - Con visuales → listas, filas y ahora **frases de párrafo** que un registro del visual respalda por completo (entidades completas + cifras
    canónicas iguales; vocabulario narrativo neutro por tipo, `PROSE_WORDS`), e introducciones/remisiones sin datos propios (`INTRO_WORDS`;
    «…:» solo si la lista que presenta se omitió entera). Una frase con cualquier dato, palabra o cifra no respaldada se conserva (avisos,
    análisis, incertidumbre). Solo se quita un prefijo/sufijo del párrafo, nunca dejando una frase anafórica («Le saca…») huérfana.
  - Sin nada que mostrar → texto vacío: el visual es la respuesta (sin frase puntero).
- Origen: `VISUAL_NOTE` única («no lo repitas… UNA frase breve con el dato clave, sin introducción; solo lo que la tarjeta no muestra»),
  también para los listados v2. Regla de formato breve en `SYSTEM_PROMPT` (móvil, sin introducción, negrita solo en el dato clave).
- 0 llamadas LLM nuevas; ~0,7 ms por respuesta.

## Frontend
- Clasificación: tabla de anchos fijos; prioridad Pos · escudo+Equipo · PJ · Pts; DG < 24 rem, G/E/P < 30 rem, GF/GC < 38 rem plegadas;
  «Ver todas las columnas» = desplazamiento interno con Pos/Equipo fijos (equipo 11,5 rem). Nombre sin cortes de palabra, 2 líneas,
  toque = nombre completo; la letra de categoría (`'A'`) en distintivo propio (nunca se recorta). Escudos: mecanismo existente
  (`/chat/crests`, `CRESTS_MODE`) y monograma como fallback.
- Aclaraciones: sin rótulo «Necesito un detalle»; botones apilados a todo el ancho en móvil, justo bajo la pregunta; en mensajes antiguos
  quedan inactivos; guarda síncrona contra doble envío.
- Alineación: pista por defecto cuando `court` (5 titulares con portero), suplentes debajo, «Ver lista»/«Ver en pista»; nombres de la
  pista ≤ 13 caracteres (sin solapes); sin `court`, lista con aviso.
- Historial: papelera directa (sin menú «⋯»), no abre la conversación, «Deshacer» conservado.
- `historyFor` reenvía `answer_original` aunque el texto visible esté vacío.
- Goleadores oficiales: hasta 30 filas (10 visibles + «Ver N más») y `total`; antes se recortaban a 10 sin avisar.

## Validación
- Backend 1448 pass (49 skip; `tests/integration` sin PostgreSQL); nuevos en `tests/test_answer_presentation.py`.
- Vitest 140/140 (`ux-polish.test.tsx` + actualizados). `tsc` y `vite build` OK; JS −0,2 KB gz, CSS +0,2 KB gz.
- Chromium (iPhone 13, 320 px, 1280 px; claro/oscuro), escenarios con nombres reales más largos del catálogo: 32/32 vistas sin scroll
  horizontal; nombre de equipo 4 → 2 líneas (7 → 2 a 320 px), fila media 62 → 46 px; «todas las columnas» 608 px dentro de 356.

## Coste
- Entrada estimada (car./3,5): +54 tokens por llamada (prompt de sistema, cacheable) y +14 por resultado con visual.
- Salida: NO medida (sin LLM). Esperable menos tokens al no enumerar filas; confirmar con `bench-127` antes de dar cifras.

## Pendiente
- Marcador estructurado de un único partido (no hay visual de resultado individual; el texto sigue siendo la respuesta).
- Frases con cifras sin unidad («…con 24») o nombres abreviados («el DOSA») se conservan por prudencia.
- Relanzar `bench-127` para medir tokens de salida y comprobar que la calificación no varía.
