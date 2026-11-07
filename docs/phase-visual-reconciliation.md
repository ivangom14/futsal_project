# Conciliación texto/visuales y tarjetas de partidos en móvil

Sin commit. Sin llamadas a Gemini ni Anthropic. No toca el agente (ni su prompt, ni `visual_note`, ni los tokens): es posproceso de presentación y frontend, así que no afecta a los A/B en curso.

## 1. Dónde se producía la duplicidad
- **El frontend no duplica**: `MessageBubble` pinta el texto del agente una vez y `<Visuals>` una vez.
- **Combinación de dos cosas del backend**: el texto es lo que escribe el LLM y los visuales los construye `presentation.build_visuals` desde los datos de las herramientas, sin mirar el texto. Solo existía una petición al modelo (`visual_note`: «no lo repitas en texto») dentro del resultado de la herramienta; el modelo la ignora a veces. Sin conciliación, nada garantizaba que no se repitiera.
- **Segundo factor**: la tarjeta de partidos no mostraba la hora (el dato `date` sí la trae); el texto la daba. Para poder omitir la lista sin perder información, la tarjeta ahora muestra la hora.

## 2. Conciliación (`src/futsal/agent/reconcile.py`, usada en `web.build_response`)
- Evalúa **cada elemento de lista y cada fila de tabla markdown por separado**. Los párrafos (introducción, conclusiones) nunca se tocan. Bloques de código, tampoco.
- Un elemento se omite solo si **todo** lo que dice lo respalda un registro estructurado del visual: las entidades (equipos, jugadores) coinciden por completo y cada dato está canonicalizado y es igual (marcador, jornada, fecha, **hora en Europe/Madrid**, posición, puntos, goles, minuto, tarjeta…). En tablas, cada celda se compara con su columna según la cabecera (Pos, PJ, G, E, P, GF, GC, DG, Pts). Cualquier palabra, número, fecha u hora que sobre, o nombres abreviados, ⇒ **se conserva**.
- Tipos con registros: partidos/resultados, clasificación, goleadores, forma, alineación, árbitros, goles por minuto y tarjetas. `comparison` y los tipos desconocidos no concilian.
- Si no queda texto, una frase que remite a la tarjeta («Te lo muestro en la tarjeta «…».»).
- **Original intacto**: `result.answer` no se modifica; memoria conversacional, historial del LLM, foco, tipo de respuesta, fuentes y sugeridas siguen usándolo. Solo `ChatResponse.answer` (lo visible) cambia. Hay un test que lo comprueba contra la memoria real y otro contra `build_sources`/`answer_kind`.
- **Trazabilidad**: `diagnostics.reconciliation` = `{considered, removed, kept, pointer_added, removed_items[{kind, reason, snippet}], kept_uncertain[{…, left}]}` (fragmentos de ≤80 caracteres, nunca la respuesta completa) y un `INFO` con los contadores. Se ve en «Detalles técnicos». Contrato nuevo: `diagnostics.reconciliation` (`null` por defecto); se regeneraron los 4 fixtures de contrato (`chat_response*.json`): solo ganan esa clave.
- **Correcciones tras la revisión final** (todas con test):
  - *Historial*: `ChatResponse.answer_original` lleva el texto ORIGINAL del agente solo cuando la conciliación omitió algo (si no, `null`); el navegador lo usa como historial (`historyFor`) y no lo muestra. Así, si el servidor no conoce la conversación (reinicio, desalojo de memoria), las preguntas de seguimiento («¿a qué hora juega el segundo?») conservan los datos que ahora están en el gráfico. Las respuestas antiguas guardadas en el navegador (sin el campo) siguen funcionando. El original no se escribe en logs (solo contadores).
  - *Falsos positivos*: el orden local-visitante es un dato (un partido con los equipos al revés se conserva, también en ida y vuelta); la etiqueta `'A'`/`'B'` forma parte del nombre del equipo y ya no se confunde con la preposición «a» (admite comillas tipográficas); la «jornada N» del subtítulo del visual no respalda una jornada distinta de la de la fila coincidente.
  - *Red de seguridad*: la conciliación falla en abierto (si lanza cualquier error se muestra el texto original y `diagnostics.reconciliation.error` lo indica) y cada visual deforme se ignora individualmente (4.000 visuales deformados generados al azar: 0 excepciones).
  - *Interruptor*: `CHAT_RECONCILE=off` apaga la conciliación (por defecto activa; cualquier otro valor la deja activa). Los visuales no dependen de él.
- Horas: Europe/Madrid con las reglas de la UE calculadas en el propio módulo (no depende de `tzdata`); con zona se convierte; sin zona se toma como local; solo fecha = sin hora.

## 3. Tarjeta de partidos en móvil (`Matches.tsx`, `index.css`, `format.ts`)
- Hora junto a la fecha: «dom 08/11 · 11:00» (Europe/Madrid, con independencia de la zona del navegador; fechas imposibles se rechazan).
- Nombres completos: saltan de línea (hasta 4; solo un nombre absurdo >100 caracteres se recorta y su texto íntegro va en `title`). Nada de `truncate`.
- **Maquetación C** por consulta de contenedor (ancho de la tarjeta, no de la pantalla): estrecha ⇒ equipos apilados con una línea fina entre ellos y VS/marcador a la derecha, centrado en vertical; ancha (≥30 rem) ⇒ tres columnas con VS/marcador centrado. En tarjetas muy estrechas (≈320 px) menos relleno lateral y VS compacto. Tamaño de fuente solo de los nombres: 13,5 px móvil / 14 px escritorio.
- Coherencia: cabeceras de goles y tarjetas, rival de la forma y etiquetas de goleadores envuelven en vez de truncar. Lineup y barra lateral, sin cambios. Clasificación en móvil: sin cambios (decisión).

## 4. Pruebas y resultados
| Prueba | Resultado |
|---|---|
| Backend, Python 3.12 y 3.11 (sin `tests/integration`) | **1151 pasan** (1085 previos + 66 nuevos) |
| `tests/test_reconcile.py` (66) | pasan: 8 tipos de visual, listas totalmente y parcialmente redundantes, elemento a elemento, información adicional conservada, visuales con datos limitados, sin visuales, idempotencia, Madrid (invierno/verano/cambios de hora/medianoche/sin zona/solo fecha), fechas en 5 formatos, tablas con cabecera, auditoría sin respuestas completas, original intacto vía web |
| Frontend `vitest` (zonas UTC, New_York, Auckland, Madrid) | **66 pasan** en las 4 (48 previos + 18 nuevos) |
| `tsc --noEmit`, `vite build` | limpios |
| Ruff (archivos nuevos) | limpio; `web.py` conserva los 3 avisos que ya tenía |
| Navegador real (Chromium, `frontend/scripts/check-layout.mjs`) | ver abajo |

Maquetación medida (22 equipos: cortos, 8 reales, los 8 más largos del catálogo —máx. 60 caracteres— y uno de 107):

| Ancho | Scroll horizontal | Desbordes | Nombres recortados | VS centrado (error máx.) | Alto medio por partido | Modo |
|---|---|---|---|---|---|---|
| 320 | no | 0 | 1 (solo el de 107) | 0 px | 145 px | apilado |
| 360 | no | 0 | 1 (solo el de 107) | 0 px | 136 px | apilado |
| 390 | no | 0 | 1 (solo el de 107) | 0 px | 113 px | apilado |
| 430 | no | 0 | 0 | 0 px | 104 px | apilado |
| 768 | no | 0 | 0 | 0 px | 97 px | apilado |
| 1280 | no | 0 | 0 | 0,01 px | 71 px | 3 columnas |
(Esa muestra es un caso límite: la mitad de los equipos son de los más largos del catálogo.)

## 5. Limitaciones
- Solo se concilian listas y tablas; la prosa que repita datos se conserva. Paráfrasis, nombres abreviados («DOSA») o datos de más ⇒ se conservan (redundancia ocasional a propósito).
- El LLM sigue generando la lista (los tokens de salida no bajan). Mover `visual_note` o endurecer el prompt cambiaría el comportamiento del agente y contaminaría el A/B de la etapa 2: queda pendiente para después.
- Si el texto da la hora en UTC y la tarjeta en Madrid, el elemento se conserva (los datos difieren).
- Las consultas de contenedor requieren Chrome 105+/Safari 16+/Firefox 110+; en navegadores antiguos se ve el diseño apilado (el de móvil).
- La medición de maquetación usa Chromium en Linux con fuentes de sistema; en un iPhone la tipografía (SF) varía algo: conviene una revisión visual allí. El script es opcional y requiere `playwright`.
- Las cabeceras de goles y de tarjetas y las filas de «forma» siguen recortando a 4 líneas los nombres más largos (60 caracteres) en pantallas ≤390 px (la tarjeta de partidos sí los muestra completos desde 320 px). Mejor que el recorte de una línea de antes, pero no del todo «sin puntos suspensivos»; queda pendiente.
- Las horas se muestran tal cual las trae el dato: un `scheduled_at` a medianoche saldría como 00:00/01:00.
