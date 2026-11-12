# 8. P0 implementada — fundamentos «Pista»

Estado: **implementada en el frontend, sin commit** (pendiente de revisión). Sin cambios en backend, MCP, agente, `api.ts`,
`types.ts`, `conversations.ts` ni contratos. Se conservan todos los `data-testid`.

## 8.1 Qué incluye

| Bloque | Implementación | Archivos |
|---|---|---|
| Tokens | Variables `--fs-*` claro/oscuro (§B.3 de la guía) y colores **semánticos** de Tailwind que apuntan a ellas; se retiran las escalas `brand-50…900` y `bear-*` y los grises/ámbar/rojo/verde por defecto de los componentes | `src/styles/tokens.css`, `tailwind.config.js`, `src/index.css` |
| Modo oscuro | Sigue a `prefers-color-scheme`; selector Sistema/Claro/Oscuro en la barra lateral (`data-theme` en `<html>`, `localStorage` `futsito.theme` con try/catch); script en `index.html` que aplica el tema antes del primer pintado; `color-scheme` para controles nativos; Markdown (`prose`) mapeado a tokens | `src/theme.ts`, `src/components/ThemeSwitch.tsx`, `index.html` |
| Tipografía | Archivo Variable autoalojada (1 woff2 latín, ejes `wght`+`wdth`, `font-display: swap`, `preload`, licencia OFL junto al archivo). Marca, títulos de visual, bienvenida, marcadores (`stretch-score`), cifras de tabla (`stretch-table`), minutos y dorsales. La conversación sigue en la fuente del sistema | `public/fonts/`, `tokens.css`, `index.css` (`.stretch-*`) |
| Iconos | `lucide-react` (Menu, X, Trash2, ThumbsUp/Down, TriangleAlert, Hand, Monitor/Sun/Moon) + `FutsalBall` propio. Sustituye ☰ ✕ 🗑 👍 👎 ⚽ 🧤 🟨🟥 | `src/components/icons.tsx`, componentes |
| Contraste AA | Todo texto secundario en `ink-2` (≥ 5,80:1 en cualquier superficie); «vs» 5,80:1; errores con texto `danger` sobre `danger-soft`; botón «Reintentar» secundario (antes texto blanco sobre rojo, que en oscuro no valdría) | componentes |
| Estados de partido | `matchStatus()`: Finalizado, Programado, Aplazado, Suspendido, Cancelado; cualquier otro → «Sin confirmar» con el valor original en `title`. Chip con tono semántico | `src/format.ts`, `Matches.tsx` |
| Menú móvil | Diálogo modal (`role="dialog"`, `aria-modal`), foco al primer control, Tab/Shift+Tab atrapados, Escape y fondo cierran, foco devuelto al botón (`aria-expanded`, `aria-haspopup`), chat `inert` mientras está abierto, se cierra al pasar a escritorio. Ancho `min(18rem, 85vw)` | `Sidebar.tsx`, `App.tsx` |
| Foco y objetivos | `:focus-visible` global (2 px `--fs-focus`, desplazamiento 2 px); botones de icono 40×40; enlaces de texto («Cancelar», «Cambiar») y motivos de valoración ≥ 24 px de alto | `index.css`, componentes |
| Movimiento reducido | `motion-safe:animate-pulse`, scroll al final sin animación y anulación global de animaciones/transiciones con `prefers-reduced-motion: reduce` | `App.tsx`, `index.css` |
| Ilustraciones | Monigotes de árbitros sobre fondo `tile` fijo (blanco) para que el uniforme negro se vea en oscuro; forma reciente con `stroke/fill-chart-1`; pista de alineación sin cambios (fondo propio) | `Officials.tsx`, `Visuals.tsx` |
| Corrección adicional | Píldoras de minuto de Goles/Tarjetas ya no se estiran en vertical cuando el nombre ocupa varias líneas (`self-center`) — defecto previo detectado en la revisión visual | `Timeline.tsx`, `Cards.tsx` |

Nombres de token implementados (difieren de los del borrador de la guía para no chocar con utilidades de Tailwind):
`canvas` (= `--fs-bg` del borrador), `surface`, `surface-2`, `line` (= `--fs-border`), `line-strong`, `ink` (= `--fs-text`),
`ink-2`, `inverse`, `inverse-on`, `brand`, `brand-hover`, `brand-text`, `brand-soft`, `brand-on`, `accent`, `accent-soft`,
`gold`, `gold-text`, `success|warning|danger` (+ `-soft`), `focus`, `chart-1`, `chart-2`, `tile` (= `--fs-crest-tile`), `scrim`.

## 8.2 Validación

| Prueba | Resultado |
|---|---|
| Vitest | **83/83** (69 existentes sin modificar + 14 nuevas en `src/design-p0.test.tsx`: estados, cajón, tema, iconos). Los 6 avisos `act(...)` ya existían en `HEAD` |
| `tsc --noEmit` | sin errores |
| `scripts/check-layout.mjs` (tarjeta de partidos, 320–1280 px) | igual que antes: sin scroll horizontal, 0 desbordes, nombres completos salvo el absurdo de 107 caracteres |
| Escaneo de contraste de todo el texto visible (Chromium, respuestas simuladas con los fixtures reales: partidos, clasificación, comparativa, goleadores, acta, árbitros, goles, tarjetas, error) | `HEAD`: **16–17** textos por debajo de AA en cada vista. P0: **0** en 320/768/1280 px, claro y oscuro (~200 textos por vista) |
| Teclado | Cajón: foco inicial en «+ Nueva conversación», Tab atrapado, anillo de 2 px visible (azul pista claro/oscuro), Escape devuelve el foco a «Abrir conversaciones» |
| Fuente | `document.fonts.check('800 16px "Archivo Variable"')` verdadero en todas las vistas |
| Estados | Programado / Aplazado / Suspendido visibles en español (fixture con estados alterados en la prueba) |

## 8.3 Peso (Vite build de producción; gzip de Vite)

| | `HEAD` | P0 | Diferencia |
|---|---|---|---|
| JS | 354,17 KB · 110,01 KB gz | 367,79 KB · 114,20 KB gz | +13,6 KB · **+4,2 KB gz** |
| CSS | 38,30 KB · 6,63 KB gz | 39,17 KB · 7,30 KB gz | +0,9 KB · **+0,7 KB gz** |
| HTML | 0,53 KB | 0,94 KB | +0,4 KB (preload + script de tema) |
| Fuente | — | 90,1 KB (woff2, ya comprimido; precargado; caché del navegador) | +90,1 KB en la primera visita |

Dentro del presupuesto de la guía (§H: fuente ≤ 90 KB, iconos ≤ 6 KB gz, JS del sistema ≤ 8 KB gz). No se ha medido LCP/CLS
con Lighthouse; el texto de la conversación no depende de la fuente web (pila del sistema) y Archivo usa `swap`.

## 8.4 Cómo comprobarlo

```bash
cd frontend && npm install && npm run dev
```

Abrir http://127.0.0.1:5173 (con el backend `futsal.agent.web` en el puerto 8001 para respuestas reales). Probar:
tema en la parte inferior de la barra lateral; a < 768 px, menú ☰ → Tab/Escape; respuestas con partidos aplazados.

## 8.5 Pendiente (P1)

- Del bloque «correcciones rápidas» del plan, aplazado porque cambia comportamiento: pie de **fuentes** plegado (`sources[]`),
  **detalles técnicos** solo en desarrollo, **confirmación/deshacer** al borrar conversación.
- Componentes base (`ui/`) y turnos del chat: `AgentTurn` a ancho completo (resuelve el corte de nombres en Tarjetas a 320 px),
  burbuja de usuario en `inverse`, `ErrorTurn`, `ClarificationTurn`, `AssumptionNote`, Composer v2, chips unificados.
- Visuales deportivos con el sistema: clasificación con columna fija y columnas por contenedor, `ScoreHero`, goleadores, `TeamCrest`.
- `FutsitoLoader` (doc 5) en lugar del texto pulsante.
- Fallback métrico de la fuente (`size-adjust`) y medición Lighthouse (LCP/CLS).
