# 10. P1, segundo bloque — componentes base, escudos, clasificación, aclaraciones, contexto y deshacer

Estado: **implementado** en el frontend. Sin cambios en backend, MCP, agente ni en el contrato de `/chat`. Con esto P1 queda
completa salvo `ScoreHero` (§10.4).

## 10.1 Qué cambia

| Cambio | Detalle | Archivos |
|---|---|---|
| **Componentes base** | `Button` (primary/secondary/ghost, 40 px), `Notice` (info/warning/danger con icono), `Chip` (suggest/option). Sustituyen clases repetidas en estado de carga, error, valoración, barra lateral, sugeridas y fallo de render | `src/ui/Button.tsx`, `Notice.tsx`, `Chip.tsx` y componentes |
| **Escudo con monograma** | Escudo genérico con 2 iniciales del club (antes de « - », sin formas jurídicas, «FUTSAL», «SALA»…; una sola palabra → 2 letras: «DO», «AR»), tono de 8 (texto blanco ≥ 5,7:1) derivado del club sin sufijo de categoría: el 'A' y el 'B' coinciden. Decorativo (`aria-hidden`). En partidos (columna propia de la rejilla, sin alterar la medida del nombre), clasificación y goleadores | `src/ui/TeamCrest.tsx`, `Matches.tsx`, `Visuals.tsx`, `index.css` |
| **Clasificación** | Se activa cuando la tabla tiene `position` y `team`. «Pos» y «Equipo» fijas al desplazar; G/E/P se pliegan con tarjeta < 22 rem y GF/GC con < 34 rem (consulta de contenedor; siguen en el DOM); «Ver todas las columnas» (`aria-pressed`) las muestra con desplazamiento; abreviaturas con `<abbr title>`; DG con signo; Pts en negrita; fila destacada con barra roja. Otras tablas, igual que antes | `Visuals.tsx`, `index.css` |
| **Goleadores** | Rango en bloque (oro para el 1.º), jugador, equipo con escudo de 16 px, goles en Archivo condensada, barra fina | `Visuals.tsx` |
| **Aclaraciones pulsables** | Con `diagnostics.context.status` `ask` (o `missing` con opciones), la última respuesta ofrece las opciones como chips (en lugar de las sugeridas) que envían la opción; turno con borde azul pista e insignia «Necesito un detalle» | `MessageBubble.tsx` |
| **Supuestos** | Si el backend antepuso el supuesto (`assumption_prepended`) y el texto empieza **exactamente** por él, se muestra como nota informativa y se quita del texto (no se duplica); si no, no se toca nada | `MessageBubble.tsx` |
| **Chip de contexto** | Sobre la caja de entrada: «Hablando de **C.D. DOSA**» (equipo o jugador; si no, competición · grupo) con «×» que empieza una conversación nueva. Sin foco, se mantiene el aviso anterior | `Composer.tsx`, `App.tsx` |
| **Deshacer al borrar** | Aviso 6 s «Conversación «…» borrada · Deshacer». El `DELETE` al servidor sigue saliendo en el acto; «Deshacer» restaura la conversación en su sitio, con sus mensajes, y la siguiente pregunta reenvía el historial (el servidor lo acepta si no la conoce). Fuera de `<main>` para poder usarse con el cajón móvil abierto | `UndoToast.tsx`, `conversations.ts` (`restoreConversation`), `App.tsx` |
| **Corrección de contraste (oscuro)** | `--fs-gold-text` era amarillo claro en oscuro, pero solo se usa **sobre** relleno oro (que no cambia): 1,26:1 en el rango n.º 1 y en la «C» de capitán (defecto de P0). Ahora `#5a3e00` en ambos modos: 5,25:1 | `styles/tokens.css` |
| **Script de maquetación** | Detecta «apilado» por las áreas de la rejilla, no por el número de columnas (la columna del escudo lo falseaba) | `scripts/check-layout.mjs` |

## 10.2 Pruebas

| Prueba | Resultado |
|---|---|
| Vitest | **114/114** (94 previas sin cambios + 20 nuevas en `src/design-p1b.test.tsx`). Avisos `act(...)`: los 6 de siempre |
| `tsc --noEmit` | sin errores |
| `check-layout.mjs` | sin scroll ni desbordes; marcador centrado (≤ 0,8 px). Coste del escudo a 320 px: columna del nombre 212 → 182 px, alto por partido 112 → 120 px (antes de P1: 145) |
| Chromium, 320/768/1280, claro y oscuro (partidos, clasificación, comparativa, goleadores, forma, aclaración) | 0 textos bajo AA tras la corrección del oro; sin scroll horizontal; columnas visibles: 320 → Pos Equipo PJ DG Pts; 768 → + G E P; 1280 → todas (sin botón); al pulsar «Ver todas» a 320 px, 10 columnas con desplazamiento interno y Pos/Equipo fijas; chip «Hablando de C.D. DOSA»; borrar → aviso → «Deshacer» restaura las 3 respuestas |

## 10.3 Peso (gzip de Vite)

| | Tras P1-bloque 1 | Tras P1-bloque 2 | Diferencia | Acumulado desde antes de P0 |
|---|---|---|---|---|
| JS | 116,42 KB | 119,17 KB | +2,8 KB | +9,2 KB (110,01 → 119,17) |
| CSS | 7,27 KB | 7,63 KB | +0,4 KB | +1,0 KB (6,63 → 7,63) |

El acumulado de JS (+9,2 KB gzip, iconos incluidos) queda dentro del presupuesto conjunto de la guía (§H: iconos ≤ 6 KB + JS del sistema ≤ 8 KB).

## 10.4 Pendiente

- **`ScoreHero`** (cabecera común de un partido): no se implementa porque los visuales de acta solo traen el marcador dentro
  del texto del subtítulo; extraerlo con expresiones regulares sería frágil. Requiere un campo estructurado (P2).
- **P2**: URLs de escudos oficiales (con `CRESTS_MODE` y autorización), `clarification`/`assumption` como campos de primer
  nivel en la respuesta, zonas de ascenso/descenso en la clasificación, visual de ficha de equipo.
- **P3**: progreso real del agente por streaming.
