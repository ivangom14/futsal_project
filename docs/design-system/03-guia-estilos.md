# 3. Guía de estilos — «Pista»

## A. Identidad visual

### A.1 Tres direcciones evaluadas

| | 1 · «Marcador» (deportivo profesional) | 2 · «Conversa» (IA minimalista) | 3 · **«Pista» (fútbol sala propio)** |
|---|---|---|---|
| Idea | Fondo oscuro azul noche, tipografía condensada, densidad de datos tipo app de resultados | Grises neutros, un acento, mucho aire; el contenido es el protagonista | Base de chat sobria + lenguaje gráfico de la pista de fútbol sala (líneas, área de 6 m, marcador, dorsales) + mascota |
| Ventajas | Credibilidad inmediata; muy bueno para tablas | Lo más legible y rápido de construir; envejece bien | Reconocible, coherente con la mascota y el logo actuales (rojo camiseta + marrón osito), diferencia frente a apps genéricas |
| Inconvenientes | Genérico (parece cualquier app de resultados); frío frente a una mascota cálida; oscuro por defecto penaliza lectura larga | Sin identidad deportiva; la mascota queda fuera de lugar; Futsito parecería «otro chatbot» | Riesgo de exceso decorativo: exige reglas estrictas de uso de motivos |
| Complejidad | Media (dos modos muy distintos) | Baja | Media (4 iconos y 3 ilustraciones propias, tokens) |
| Encaje | 2/5 | 3/5 | **5/5** |

**Recomendación: «Pista».** Mantiene el activo de marca existente (mascota, rojo, marrones), añade lo que hace único al
fútbol sala y se apoya en la sobriedad de la dirección 2 para la conversación. No es una mezcla: la estructura es
siempre la de un chat limpio y **los motivos de pista solo aparecen en tres lugares** (marcadores, divisores/estados
vacíos y la mascota). Esa regla es lo que evita el exceso decorativo.

### A.2 Personalidad

Futsito es **el delegado de campo que se sabe todos los datos**: cercano, preciso, nunca fanfarrón.

| Es | No es |
|---|---|
| Directo: primero el dato, luego el contexto | Locuaz ni grandilocuente |
| Honesto: dice de dónde sale cada dato y cuándo no lo sabe | Ambiguo sobre la procedencia |
| Cercano: tutea, usa vocabulario de fútbol sala con naturalidad | Infantil (la mascota es amable, el texto es adulto) |
| Deportivo en los detalles | Ruidoso: sin confeti, sin sonidos, sin animaciones continuas |

### A.3 Principios de diseño

1. **El dato manda.** Números grandes y tabulares; decoración nunca compite con un marcador.
2. **Procedencia visible.** Toda respuesta con datos dice su fuente y su fecha.
3. **Una cosa, un sitio.** Texto breve + visual; nunca el mismo dato dos veces.
4. **Pulgar primero.** Diseñado para 360 px y una mano; el escritorio es una ampliación.
5. **Movimiento con propósito.** Solo para feedback y espera; todo respeta movimiento reducido.
6. **Ligero por defecto.** Cada recurso nuevo justifica su peso con una medida.

### A.4 Elementos diferenciadores del fútbol sala

| Motivo | Origen real | Uso permitido |
|---|---|---|
| **Líneas de pista** (2 px, esquinas redondeadas, línea media) | Pista 40×20 m | Fondo de la pista de alineación; ilustraciones; divisor de sección en bienvenida |
| **Área de 6 m** (arco en «D») | Área de portería de fútbol sala | Forma de los estados vacíos y del marco del loader |
| **Marcador** (numerales condensados, tabulares) | Marcadores de pabellón | `Scoreline`, posiciones y puntos de la clasificación |
| **Dorsal** (número en bloque) | Camisetas | Jugadores en alineación, goleadores, tarjetas |
| **Quinteto** (5 puntos) | 5 jugadores en pista; también las 5 faltas acumuladas | Indicador de carga compacto (5 puntos que se encienden) |
| **Parqué / tiza** | Suelo de pabellón | Neutros cálidos de fondo (no texturas) |

## B. Color

### B.1 Paleta base

| Rol | Nombre | Claro | Oscuro | Uso |
|---|---|---|---|---|
| Marca / acción | Camiseta | `#C0281E` | `#C0281E` (relleno) / `#FF8A7E` (texto y gráfico) | Botón principal, logo, serie 1, fila destacada |
| Marca hover | Camiseta oscura | `#A21F17` | `#D63A2F` | Hover/pressed del primario |
| Secundario | Azul pista | `#1B5FA6` | `#7DB3F0` | Enlaces, foco, información, aclaraciones, serie 2 |
| Acento | Oro marcador | `#F2B21B` (texto encima `#5A3E00`) | `#F2B21B` / texto `#F7CF6A` | Capitán, líder, análisis, «nuevo» |
| Fondo | Tiza | `#F8F4EE` | `#14100D` | Lienzo de la app |
| Superficie | Blanco | `#FFFFFF` | `#1E1813` | Tarjetas, composer |
| Superficie 2 | Parqué claro | `#F1EAE0` | `#29211A` | Cabecera de visual, pista de barras, hover |
| Borde | | `#E2D6C6` | `#3B3027` | Bordes decorativos |
| Borde fuerte | | `#8C7A68` | `#9C8A78` | Bordes de controles (≥ 3:1) |
| Texto | Tinta | `#221812` | `#F4ECE3` | Texto principal |
| Texto 2 | | `#6A5646` | `#BCAA98` | Secundario, subtítulos, metadatos |
| Inverso | | `#2A1E16` | `#F4ECE3` | Burbuja del usuario, marcador final |

Semánticos (resultado deportivo y estado del sistema comparten tono **pero no forma**: V/E/D llevan letra; los
mensajes de sistema llevan icono y título):

| Rol | Claro (texto / fondo) | Oscuro (texto / fondo) | Deportivo | Sistema |
|---|---|---|---|---|
| success | `#1C7444` / `#E5F4EA` | `#5CC98A` / `#14301F` | Victoria | Valoración guardada |
| warning | `#94560A` / `#FBF0DD` | `#F0B45A` / `#33240E` | Empate, aplazado | Respuesta parcial, lento, desactualizado |
| danger | `#B3261E` / `#FBE9E7` | `#FF8A80` / `#3A1714` | Derrota, roja, suspendido | Error, sin conexión |
| info | `#1B5FA6` / `#E8F0FA` | `#7DB3F0` / `#16263A` | — | Aclaración, supuesto |

**Decisión marca vs. error.** El rojo de marca y el `danger` son casi el mismo tono. Para que un error no parezca «de
marca» y viceversa: (1) la burbuja del usuario pasa de rojo a **inverso** (marrón tinta), reduciendo la superficie roja;
(2) el rojo de marca solo aparece en relleno (botón, logo, fila destacada); (3) el error siempre lleva icono
`triangle-alert`, título y fondo `danger-soft`. Compromiso: se pierde algo de «calor» en el chat a cambio de claridad.

### B.2 Contrastes medidos (WCAG 2.x, fórmula de luminancia relativa)

| Par | Claro | Oscuro |
|---|---|---|
| Texto / fondo | 15,87 | 16,18 |
| Texto 2 / superficie | 6,93 | 7,82 |
| Texto 2 / superficie 2 | 5,80 | 7,04 |
| Marca-texto / superficie | 7,65 | 7,69 |
| Blanco / Camiseta (botón) | 5,89 | 5,89 |
| Blanco / Camiseta hover | 7,65 | 4,66 |
| Azul pista / superficie | 6,49 | 8,01 |
| success / success-soft | 5,08 | 6,91 |
| warning / warning-soft | 5,18 | 8,13 |
| danger / danger-soft | 5,58 | 7,01 |
| Texto oro / oro | 5,25 | — |
| Borde fuerte / superficie (no texto, ≥ 3) | 4,12 | 5,29 |
| Foco / fondo (no texto, ≥ 3) | 5,93 | 8,63 |
| Camiseta como gráfico / superficie | 5,89 | **2,98 ✗** → en oscuro, gráficos con `#FF8A7E` (7,69) |

### B.3 Tokens (CSS custom properties)

Tres capas: **primitivos** (paleta), **semánticos** (rol) y **de componente** (solo donde aporten). Los componentes
consumen solo semánticos.

```css
:root {
  /* semánticos — claro */
  --fs-bg: #F8F4EE;          --fs-surface: #FFFFFF;     --fs-surface-2: #F1EAE0;
  --fs-border: #E2D6C6;      --fs-border-strong: #8C7A68;
  --fs-text: #221812;        --fs-text-2: #6A5646;      --fs-inverse: #2A1E16;  --fs-on-inverse: #FFFFFF;
  --fs-brand: #C0281E;       --fs-brand-hover: #A21F17; --fs-brand-text: #A21F17; --fs-brand-soft: #FCEDEA; --fs-on-brand: #FFFFFF;
  --fs-accent: #1B5FA6;      --fs-accent-soft: #E8F0FA;
  --fs-gold: #F2B21B;        --fs-gold-text: #5A3E00;
  --fs-success: #1C7444;     --fs-success-soft: #E5F4EA;
  --fs-warning: #94560A;     --fs-warning-soft: #FBF0DD;
  --fs-danger: #B3261E;      --fs-danger-soft: #FBE9E7;
  --fs-focus: #1B5FA6;
  --fs-chart-1: var(--fs-brand); --fs-chart-2: var(--fs-accent); --fs-chart-track: var(--fs-surface-2);
  --fs-crest-tile: #FFFFFF;  /* los escudos siempre sobre blanco, también en oscuro */

  /* forma y espacio */
  --fs-radius-sm: 6px; --fs-radius-md: 10px; --fs-radius-lg: 16px; --fs-radius-pill: 999px;
  --fs-space-1: 4px; --fs-space-2: 8px; --fs-space-3: 12px; --fs-space-4: 16px; --fs-space-5: 24px; --fs-space-6: 32px;
  --fs-shadow-1: 0 1px 2px rgb(34 24 18 / .06), 0 1px 1px rgb(34 24 18 / .04);
  --fs-shadow-2: 0 6px 20px rgb(34 24 18 / .10);

  /* movimiento */
  --fs-dur-1: 120ms; --fs-dur-2: 200ms; --fs-dur-3: 320ms;
  --fs-ease-out: cubic-bezier(.2,.7,.3,1); --fs-ease-in: cubic-bezier(.6,0,.8,.3); --fs-ease-io: cubic-bezier(.4,0,.2,1);

  /* tipografía */
  --fs-font-display: "Archivo Variable", "Archivo", system-ui, sans-serif;
  --fs-font-text: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
:root[data-theme="dark"] { /* y @media (prefers-color-scheme: dark) sin data-theme explícito */
  --fs-bg: #14100D; --fs-surface: #1E1813; --fs-surface-2: #29211A; --fs-border: #3B3027; --fs-border-strong: #9C8A78;
  --fs-text: #F4ECE3; --fs-text-2: #BCAA98; --fs-inverse: #F4ECE3; --fs-on-inverse: #1E1813;
  --fs-brand-hover: #D63A2F; --fs-brand-text: #FF8A7E; --fs-brand-soft: #3A1A15;
  --fs-accent: #7DB3F0; --fs-accent-soft: #16263A; --fs-gold-text: #F7CF6A;
  --fs-success: #5CC98A; --fs-success-soft: #14301F; --fs-warning: #F0B45A; --fs-warning-soft: #33240E;
  --fs-danger: #FF8A80; --fs-danger-soft: #3A1714; --fs-focus: #7DB3F0;
  --fs-chart-1: #FF8A7E;
}
```

> **Implementado (P0)** en `frontend/src/styles/tokens.css` con nombres ajustados a Tailwind (`canvas`, `line`, `ink`, `tile`…): tabla de equivalencias en [08-p0-implementacion.md](08-p0-implementacion.md).

Integración con Tailwind 3 (sin dependencias nuevas): `theme.extend.colors` apunta a las variables
(`surface: "var(--fs-surface)"`, …) y `darkMode: ["class", '[data-theme="dark"]']`. Así el código sigue usando
utilidades (`bg-surface text-text-2`) y el modo oscuro no necesita variantes `dark:` en cada componente.
Las paletas `brand`/`bear` se mantienen como alias durante la migración (fase 1 del plan) y se retiran al final.

## C. Tipografía

### C.1 Alternativas comparadas (pesos medidos: woff2, subconjunto latín de Fontsource 5.3.0)

| Familia | Licencia | Ejes / pesos | Peso | Cifras tabulares | Personalidad | Veredicto |
|---|---|---|---|---|---|---|
| **Archivo Variable** | OFL-1.1 | `wght` 100–900, `wdth` 62–125 | **90,1 KB** (ambos ejes) · 34,9 KB solo `wght` | sí (`tnum`) | Grotesca deportiva con anchura variable: condensada para marcadores, expandida para la marca | **Elegida** para display, marcadores y tablas |
| Barlow Condensed | OFL-1.1 | estáticos | 21–22 KB **por peso** (4 pesos ≈ 89 KB) | sí | Muy «rótulo deportivo», sin cuerpo de texto | Descartada: mismo peso que Archivo con menos flexibilidad |
| Bricolage Grotesque | OFL-1.1 | `opsz`, `wght`, `wdth` 75–100 | 131,5 KB (todos los ejes) · 41,3 KB `wght` | sí | Expresiva, editorial | Descartada: más pesada y menos «marcador» |
| Space Grotesk | OFL-1.1 | `wght` | 22,3 KB | sí | Tecnológica | Descartada: se asocia a «IA genérica» |
| Inter Variable | OFL-1.1 | `wght` (+`opsz`) | 48,3 KB · 72,9 KB con `opsz` | sí | Neutra, excelente en pantalla | Alternativa para el texto si se quiere uniformidad entre SO (+48 KB) |
| **Pila del sistema** | — | — | **0 KB** | sí (SF, Segoe, Roboto) | Nativa, máxima legibilidad | **Elegida** para la conversación |

Todas verifican `ñ Ñ ¿ ¡ á é í ó ú ü € º` en el subconjunto latín (comprobado con fontTools).

**Decisión.** Una sola fuente web (Archivo, 90 KB, `font-display: swap`, precargada) para todo lo que es marca o dato;
el texto conversacional (lo más abundante) en la fuente del sistema. Justificación: UX (lectura nativa en cada
dispositivo, sin parpadeo en el texto largo), DX (un archivo, un `@font-face`), rendimiento (el texto del chat no
depende de la descarga). El eje de anchura permite que la clasificación de 10 columnas quepa a 320 px sin bajar del
tamaño mínimo de 13 px.

Autoalojada con `@fontsource-variable/archivo` (dependencia de build de 1 MB desempaquetada que solo emite el woff2
usado) o copiando el woff2 a `public/fonts/` con su `OFL.txt`. Se prefiere autoalojar frente a Google Fonts: sin
petición a terceros (privacidad) y caché propia.

### C.2 Escala

| Token | Familia | Tamaño / interlínea | Peso | `wdth` | Uso |
|---|---|---|---|---|---|
| `brand` | Archivo | 28/32 (móvil) · 32/36 | 850 | 112 | Nombre «Futsito» (si no se usa el SVG) |
| `h1` | Archivo | 22/28 · 24/30 | 750 | 100 | Título de bienvenida |
| `h2` | Archivo | 17/22 | 700 | 100 | Título de visual |
| `overline` | Archivo | 12/16, `letter-spacing: .06em`, mayúsculas | 650 | 100 | «Jornada 12 · sáb 07/11» |
| `body` | Sistema | **16/24** | 400 | — | Mensajes (también en escritorio: no bajar a 14) |
| `body-sm` | Sistema | 14/20 | 400 | — | Metadatos, sugeridas |
| `caption` | Sistema | 13/18 | 400 | — | Pies de visual, fuentes. **Mínimo absoluto** (se elimina `text-[10px]`) |
| `score-lg` | Archivo `tnum` | 28/28 | 800 | 75 | Marcador de partido destacado |
| `score` | Archivo `tnum` | 18/20 | 800 | 75 | Marcador en lista |
| `table` | Archivo `tnum` | 14/20 | 500 (Pts 750) | 87.5 | Clasificación, goleadores |
| `dorsal` | Archivo `tnum` | 15/16 | 800 | 75 | Dorsales |

Reglas: cifras siempre `font-variant-numeric: tabular-nums`; nombres de equipo en el texto del sistema (no se
condensan: legibilidad de nombres largos); nunca mayúsculas en nombres de equipo (la RFFM ya los da en mayúsculas;
se muestran tal cual, sin transformar).

## D. Iconografía

### D.1 Librerías evaluadas (medido: 20 iconos equivalentes, esbuild `--minify`, React externo)

| Librería | Versión | Licencia | 20 iconos | Deportivos disponibles | Veredicto |
|---|---|---|---|---|---|
| **Lucide** (`lucide-react`) | 1.72.0 | ISC | **16,0 KB / 3,9 KB gz** | `goal`, `whistle`, `trophy`, `medal`, `podium`, `shirt`, `timer`, `calendar-days`, `list-ordered`, `shield` (sin balón de fútbol) | **Elegida** |
| Phosphor (`@phosphor-icons/react`) | 2.1.10 | MIT | 55,8 KB / 14,5 KB gz | `SoccerBall`, `Trophy`, `TShirt`, `Ranking` (sin silbato) | Descartada: 3,7× más peso por el sistema de pesos |
| Tabler (`@tabler/icons-react`) | 3.51.0 | MIT | no medido | amplio | Equivalente a Lucide; Lucide encaja mejor con el trazo redondeado de la mascota |

### D.2 Reglas

- Rejilla 24×24, trazo **2 px** a 20–24 px y **1,75 px** a 16 px (`absoluteStrokeWidth` desactivado), extremos redondeados.
- Tamaños: `sm` 16 (en línea con `caption`/`body-sm`), `md` 20 (botones, chips), `lg` 24 (navegación). Ilustración: aparte.
- Color: `currentColor` siempre; un icono nunca es el único portador de significado (texto o `aria-label`).
- Decorativos con `aria-hidden="true"`; botones solo-icono con `aria-label` y objetivo ≥ 40×40 px (mínimo WCAG 24).
- Importación por nombre (`import { Trophy } from "lucide-react"`) para que el *tree-shaking* elimine el resto.

### D.3 Mapa de iconos

| Concepto | Icono | Fuente |
|---|---|---|
| Partido / resultado | **`ball`** (balón de fútbol sala, paneles simplificados) | Propio |
| Próximo partido / calendario | `calendar-days` · hora `clock` | Lucide |
| Competición | `trophy` | Lucide |
| Clasificación | `list-ordered` (lista) · `podium` (podio) | Lucide |
| Goleadores / gol | `goal` | Lucide |
| Equipo | **`crest`** (escudo genérico, también fallback) | Propio |
| Jugadores / plantilla | `shirt` · `users` | Lucide |
| Alineación | **`court`** (pista con línea media) | Propio |
| Árbitros | `whistle` | Lucide |
| Tarjetas | **`card`** (rectángulo vertical, relleno por tipo) | Propio (ya existe dibujo en `Cards.tsx`) |
| Estadísticas / comparar | `chart-column` | Lucide |
| Normativa | `book-open` / `scale` | Lucide |
| Enviar · cancelar · reintentar | `send-horizontal` · `square` (stop) · `rotate-ccw` | Lucide |
| Nueva conv. · borrar · menú · cerrar | `plus` · `trash-2` · `menu` · `x` | Lucide |
| Valorar | `thumbs-up` · `thumbs-down` | Lucide |
| Aviso · info · fuente · actualizado | `triangle-alert` · `info` · `database` · `refresh-cw` | Lucide |
| Análisis del modelo | `sparkles` | Lucide |

Los 4 iconos propios viven en `src/ui/icons.tsx` con la misma API que Lucide (`size`, `strokeWidth`, `className`).

## E. Movimiento, ilustración y microinteracciones

### E.1 Tokens y reglas

- Duraciones: `--fs-dur-1` 120 ms (hover, press), `--fs-dur-2` 200 ms (aparición de mensaje, chips), `--fs-dur-3` 320 ms (cajón lateral).
- Solo `transform` y `opacity` (composición en GPU; sin animar `height`, `width`, `top`).
- `@media (prefers-reduced-motion: reduce)`: duraciones a 0, sin bucles; el scroll al final pasa a `behavior: "auto"`.
- Nada en bucle salvo el loader, y solo mientras hay una consulta en curso.

### E.2 Inventario

| Momento | Recurso | Técnica | Peso estimado |
|---|---|---|---|
| Bienvenida | Futsito apoyado en el balón sobre líneas de pista; saludo con la pata **una vez** (600 ms) | SVG en línea + CSS | ~3 KB |
| Procesando | `FutsitoLoader` dando toques (doc 5) | SVG + CSS | 2,2 KB (medido) |
| Procesando (compacto) | «Quinteto»: 5 puntos que se encienden en secuencia | CSS | < 0,5 KB |
| Búsqueda / foco | `ContextChip` entra con fundido + desplazamiento 4 px | CSS | 0 |
| Estado vacío | Área de 6 m vacía con el balón en el punto de penalti: «No hay partidos con acta en esta jornada» | SVG estático | ~1,5 KB |
| Error | «Balón fuera»: balón cruzando la línea de banda (estático) | SVG estático | ~1,5 KB |
| Confirmación | Check dentro del balón al guardar la valoración (escala 0.8→1, 200 ms) | CSS | 0 |
| Mensaje nuevo | Fundido + 6 px hacia arriba, 200 ms | CSS | 0 |

GIF, Lottie y Rive no aportan nada que SVG+CSS no resuelva aquí (comparativa medida en doc 5).
Ilustraciones: propias, derivadas de las formas del logo actual (círculos y trazos redondeados, paleta de tokens).
No se descargan ilustraciones de terceros.

## F. Componentes

Convención: componentes de sistema en `frontend/src/ui/` (genéricos) y deportivos en `frontend/src/components/sport/`.
Todas las variantes con `data-variant` o props tipadas; nada de clases de color sueltas en los componentes de dominio.

### F.1 Turnos del chat

| Turno | Forma | Detalle |
|---|---|---|
| `UserMessage` | Burbuja a la derecha, `inverse`, radio 16 con esquina inferior derecha 4, máx. 85 % | `body`, `whitespace-pre-wrap` |
| `AgentTurn` | **Sin burbuja**, ancho completo; cabecera de 24 px: avatar 24 + «Futsito» + `ProvenanceBadge` | Libera ~70 px a 320 px para los visuales |
| `ClarificationTurn` | `AgentTurn` con borde izquierdo 3 px `accent` y opciones como `PromptChip` | Solo si `context.status === "ask"` y hay `options` |
| `ErrorTurn` | Caja `danger-soft`, icono, título «No he podido responder», texto y botón secundario «Reintentar» | `cancelled` usa tono neutro: «Consulta cancelada» |
| `AgentStatus` | `FutsitoLoader` + «Consultando datos oficiales…» + tiempo (desde 5 s) + «Cancelar» | Aparece a los 300 ms (evita parpadeo en respuestas rápidas) |

Orden dentro de `AgentTurn` (fijo): cabecera → `AssumptionNote` → texto → visuales → `SourceFooter` →
acciones (valorar · copiar) → sugeridas (solo la última, **justo encima del composer** en móvil).

### F.2 Composer

- Textarea con auto-altura (1→6 líneas), 16 px, borde `border-strong`, radio 16, foco con anillo 2 px `focus`.
- Botón primario circular 40 px (`send-horizontal`); durante la consulta se convierte en **Detener** (`square`), que cancela.
- `ContextChip` encima cuando hay foco: «Hablando de **Lope de Vega** · ×».
- Contador solo desde 1 800/2 000. Ayuda «Enter envía · Shift+Enter nueva línea» solo en escritorio (`pointer: fine`).
- Estados: vacío (botón deshabilitado, contraste 3:1 mantenido), con texto, enviando (stop), sin conexión (deshabilitado + motivo).

### F.3 Botones

| Variante | Fondo / texto | Uso |
|---|---|---|
| `primary` | `brand` / `on-brand`; hover `brand-hover` | Una por vista: enviar, nueva conversación |
| `secondary` | `surface` + borde `border-strong` / `text` | Reintentar, cancelar |
| `ghost` | transparente / `text-2`; hover `surface-2` | Acciones de mensaje, cerrar |
| `danger` | `danger-soft` / `danger` | Confirmar borrado |
| `icon` | `ghost` 40×40 | Menú, borrar, valorar |

Alturas 40 (por defecto) y 32 (compacto en escritorio, nunca en táctil). Foco: `outline: 2px solid var(--fs-focus); outline-offset: 2px`.
Deshabilitado: `opacity .5` + `cursor: not-allowed`; nunca solo por color.

### F.4 Tarjeta de partido (`MatchRow` + `Scoreline`)

```
┌───────────────────────────────────────────────┐
│ JORNADA 12 · SÁB 07/11 · 11:00     [Finalizado]│  overline + MatchStatusChip
│ [escudo] LOPE DE VEGA FS              ┌─────┐ │
│                                       │ 4–2 │ │  Scoreline: Archivo wdth 75, inverse
│ [escudo] CARDENAL SPÍNOLA CHAMARTÍN   └─────┘ │
└───────────────────────────────────────────────┘
≥ 30 rem de contenedor:   [esc] LOCAL   4 – 2   VISITANTE [esc]
```

- Se mantiene la lógica de **consulta de contenedor** actual (`.match-list`): ya está probada a 320 px.
- Escudo 24 px (lista) / 32 px (≥ 30 rem). Ganador en peso 650, perdedor 400 (solo si `finished`).
- `MatchStatusChip` (traducción obligatoria, corrige S3):

| `status` | Etiqueta | Tono |
|---|---|---|
| `finished` | Finalizado | neutro (`surface-2` / `text-2`) |
| `scheduled` | Programado | `accent-soft` / `accent` |
| `postponed` | Aplazado | `warning` |
| `suspended` | Suspendido | `danger` |
| `cancelled` | Cancelado | `danger` |
| `unknown` / otro | Sin confirmar | neutro + `title` con el valor original |

- Sin marcador: «vs» en `surface-2`/`text-2` (5,80:1, corrige A3). `past_due`: `StaleNote` bajo la fila.

### F.5 Marcador destacado (`ScoreHero`, para un único partido)

Escudos 48 px, nombres en dos líneas máx., marcador `score-lg` sobre `inverse` con radio 10, debajo «Final · Jornada 12 ·
Pabellón …». Variante «programado»: hora en lugar del marcador. Se usa cuando la respuesta trata de un solo partido
(acta, timeline, alineación) como cabecera común — hoy cada visual repite el subtítulo `_match_line`.

### F.6 Clasificación (`StandingsTable`)

- Columnas visibles por ancho de contenedor: < 22 rem → `Pos · Equipo · PJ · DG · Pts`; 22–34 rem → + `G E P`; ≥ 34 rem → todas.
  Las ocultas siguen disponibles con «Ver tabla completa» (desplazamiento horizontal) — el dato nunca se pierde.
- **Pos + escudo + equipo fijos** (`position: sticky; left: 0`) al desplazar.
- Números en `table` (Archivo `wdth` 87.5, tabular); `Pts` en 750.
- Fila destacada (`highlight`): fondo `brand-soft`, barra izquierda 3 px `brand`.
- Zonas de ascenso/descenso: el backend guarda `promotions` (color, nombre) en la instantánea pero **no las envía** en
  el visual → propuesta de campo aditivo (fase 4).
- Cabecera con `<abbr title="Partidos jugados">PJ</abbr>`.

### F.7 Goleadores (`ScorersList`)

Fila: rango (dorsal 15 px, oro para el 1.º) · nombre · equipo con escudo 16 px · goles (`score`) · barra 6 px
(`chart-1` sobre `chart-track`). Empates de rango con el mismo número (`rank` del backend).

### F.8 Forma, comparativa, alineación, árbitros, tarjetas, timeline

Mantener los dibujos actuales y sustituir hex por `var(--fs-…)`. Forma: chips V/E/D de 24 px con letra (no solo color).
Comparativa: serie 1 `chart-1`, serie 2 `chart-2` (rojo vs. azul: distinguibles también con daltonismo rojo-verde).

### F.9 Ficha de equipo (`TeamCard`) — **nuevo, requiere visual `team` en backend**

Escudo 56 px · nombre · competición/grupo · posición y puntos · forma (5 chips) · próximo partido. En móvil, una
columna; ≥ 34 rem, escudo a la izquierda y datos en rejilla 2×2. Fuente única: datos ya disponibles por API
(`teams`, `standings`, `matches`), compuestos en `presentation.py`.

### F.10 Chips

| Chip | Uso | Estilo |
|---|---|---|
| `PromptChip` | Ejemplos de bienvenida y sugeridas | `surface` + borde, radio pill, 40 px de alto, texto `body-sm`, icono opcional |
| `FilterChip` | Opciones de aclaración (equipo/competición/grupo) | Igual + `aria-pressed` |
| `ContextChip` | Foco activo | `accent-soft` / `accent` + `x` |
| `ProvenanceBadge` | `answer_kind` | `surface-2` + icono `database` («Datos oficiales RFFM») |
| `AnalysisBadge` | `is_analysis` | `gold` / `gold-text` + `sparkles`, texto corto «Análisis» + `title` con la explicación |

### F.11 Carga, errores, vacíos

| Pieza | Contenido |
|---|---|
| `FutsitoLoader` | Doc 5 |
| `QuintetoDots` | 5 puntos 6 px, encendido secuencial 1 s; reducido: 5 puntos estáticos |
| `InlineNotice` | Icono + texto, tonos `info/warning/danger`, para parciales, lentitud, desactualizado |
| `EmptyState` | Ilustración 96 px + título + 1 frase + acción opcional |
| `ErrorTurn` | §F.1 |
| `SourceFooter` | Plegado: «Datos oficiales RFFM · actualizado 09/11/2026» → desplegado: lista de `sources` con `freshness` |

## G. Responsive (mobile-first)

| Rango | Anchura | Navegación | Chat | Visuales |
|---|---|---|---|---|
| Móvil pequeño | 320–359 | Cajón modal 85 vw | Gutter 12 px; `AgentTurn` a ancho completo | Partidos apilados; clasificación 5 columnas; escudo 20 px |
| Móvil grande | 360–599 | Cajón modal 320 px | Gutter 16 px | Ídem; escudo 24 px |
| Tablet | 600–1023 | Cajón modal (vertical) · barra fija de 256 px desde 900 px | Columna máx. 680 px | Partidos en 3 columnas cuando el contenedor ≥ 30 rem |
| Escritorio | ≥ 1024 | Barra fija 280 px | Columna máx. 760 px, centrada | Clasificación completa |

Reglas: los visuales responden al **contenedor** (container queries), no a la pantalla; la página nunca desplaza en
horizontal (solo dentro de la tabla); `100dvh` para el alto y `env(safe-area-inset-bottom)` en el composer;
objetivos táctiles ≥ 40 px.

## H. Accesibilidad y rendimiento

- **WCAG 2.2 AA**: contrastes de §B.2; foco visible en todo control (2.4.7, 2.4.11 no oculto por el composer fijo:
  `scroll-padding-bottom` igual a su altura); objetivos ≥ 24 px (2.5.8; se usan 40); cajón con `role="dialog"`,
  `aria-modal`, foco atrapado, `Escape` y retorno del foco; `aria-live="polite"` solo en `AgentStatus` y en el
  último turno; tablas con `scope` y `abbr`; idioma `es`.
- **Movimiento reducido**: §E.1; el loader se queda en pose estática con el texto.
- **Presupuesto de peso** (incremento sobre lo medido hoy):

| Recurso | Presupuesto |
|---|---|
| Fuente | ≤ 90 KB (1 woff2), precargada |
| Iconos | ≤ 6 KB gz |
| Ilustraciones + loader | ≤ 10 KB en total, en línea |
| Escudos | ≤ 4 KB por escudo servido (medido: 2,3–5,3 KB en WebP 96 px), `loading="lazy"` |
| JS del sistema de diseño | ≤ 8 KB gz |

- **Sin saltos visuales (CLS)**: `width`/`height` en todos los `img` (escudos 1:1 con `object-fit: contain`), métricas de
  respaldo de la fuente con `size-adjust` para Archivo frente a la pila del sistema, reserva de alto del loader.
- **Dependencias nuevas justificadas**: solo `lucide-react` (iconos) y, opcionalmente, `@fontsource-variable/archivo`
  (empaquetado de la fuente). Ninguna de estado ni animación.
