# 1. Auditoría del frontend actual

Fuente: código de `frontend/` en el commit actual, build medido y contratos de `src/futsal/agent/presentation.py` y `web.py`.
Severidad: **A** (bloquea calidad o accesibilidad), **M** (degrada la experiencia), **B** (pulido).

## 1.1 Arquitectura y dependencias

| Aspecto | Estado real |
|---|---|
| Stack | React 18 + TypeScript + Vite 5 + Tailwind 3 (`@tailwindcss/typography`) |
| Dependencias de ejecución | `react`, `react-dom`, `react-markdown`, `remark-gfm` (4). Sin librería de iconos, fuentes, animación ni estado |
| Tamaño | ~1 500 líneas de código (sin tests) en 16 componentes; 69 tests Vitest |
| Estado | `useState` en `App.tsx`; conversaciones en `localStorage` (`conversations.ts`, funciones puras) |
| Red | `api.ts`: `POST /chat` (no streaming, timeout 120 s, cancelable), `GET /chat/health` cada 15 s, `POST /chat/feedback`, `DELETE /chat/conversations/{id}` |
| Respuesta del agente | `ChatResponse`: `answer` (Markdown), `answer_kind`, `is_analysis`, `visuals[]` tipados (10 tipos), `suggestions[]`, `sources[]`, `diagnostics` |
| Build | JS 354,2 KB / 110,0 KB gz (reparto por módulo no medido); CSS 38,3 KB / 6,6 KB gz |

Valoración DX: la base es buena (pocas dependencias, contratos tipados, render de visuales por `switch` sobre un tipo
discriminado, tests). Lo que falta es una **capa de diseño**: no hay tokens semánticos, cada componente decide colores
Tailwind sueltos y los SVG llevan hex fijos.

## 1.2 Sistema de estilos

| # | Hallazgo | Evidencia | Sev. |
|---|---|---|---|
| E1 | Solo dos escalas de color de marca (`brand`, `bear`) y el resto `slate`/`amber`/`red`/`green` de Tailwind mezclados sin semántica | `tailwind.config.js`; `bg-amber-100`, `text-red-800`, `bg-green-100`… en 9 componentes | M |
| E2 | Hex fijos dentro de SVG (no siguen tema ni modo oscuro) | `Lineup.tsx` (`#c0281e`, `#3b2416`, `#f5b301`), `Visuals.tsx` (`stroke="#c0281e"`), `Cards.tsx`, `Officials.tsx` | M |
| E3 | Sin modo oscuro | ninguna variante `dark:`; `body` con `bg-bear-50` fijo | M |
| E4 | `Frame` de visual duplicado | `Visuals.tsx:Frame` y la cabecera copiada en `Matches.tsx` | B |
| E5 | Sin escala tipográfica propia; tamaños mágicos | `font-size: 0.84375rem` en `.match-team`, `text-[10px]` en `ConnectionBadge` | B |
| E6 | Iconografía con emoji y glifos de texto | `☰`, `✕`, `🗑`, `👍/👎`, `⚽`: render distinto por sistema, no escalan con el texto, lectores de pantalla leen el emoji | M |

## 1.3 Accesibilidad (contrastes medidos con la fórmula WCAG 2.x)

| # | Hallazgo | Medida | Sev. |
|---|---|---|---|
| A1 | `text-slate-400` sobre blanco (pista de contexto, rango en barras, «J3» en forma, motivo de la conexión) | **2,56:1** (AA exige 4,5:1) | A |
| A2 | `text-slate-500` (subtítulos `text-xs`) sobre `bear-50` en la cabecera de cada visual | **4,36:1** | A |
| A3 | Píldora «vs» `slate-500` sobre `bear-100` | **3,77:1** | A |
| A4 | Barra secundaria `bear-500` sobre pista `bear-100` (gráfico comparativo) | 3,10:1 (justo por encima del 3:1 de no-texto) | B |
| A5 | Menú lateral móvil sin fondo modal, sin `Escape`, sin devolver el foco ni atraparlo | `Sidebar.tsx` (solo alterna `hidden`) | A |
| A6 | Botones sin foco visible: «Enviar», «Nueva conversación», borrar conversación, cerrar menú | `Composer.tsx`, `Sidebar.tsx` | A |
| A7 | `animate-pulse` y `scrollIntoView({behavior:"smooth"})` ignoran `prefers-reduced-motion` | `App.tsx` | M |
| A8 | Borrar conversación es inmediato, sin confirmación ni deshacer | `Sidebar.tsx` → `deleteConversation` | M |
| A9 | Objetivos táctiles < 24×24 px (WCAG 2.2 2.5.8): papelera `px-2` sin alto, «Cancelar» y «Cambiar» como texto subrayado de 12 px | `Sidebar.tsx`, `App.tsx`, `FeedbackBar.tsx` | M |
| A10 | Buen punto de partida: `role="status"`/`alert`, `aria-label` en SVG de alineación/tarjetas/árbitros, `lang="es"`, entrada a 16 px en móvil (sin zoom iOS) | — | ✓ |

## 1.4 Responsive

- Correcto: maquetación de partidos por **consulta de contenedor** (`.match-list`), nombres completos con `line-clamp: 4`,
  `overflow-x: hidden` global, script `scripts/check-layout.mjs` que mide desbordes a 320 px.
- **R1 (M)**: la respuesta del agente vive en una burbuja `max-w-[92%]` con borde, relleno `px-4` y avatar de 32 px al lado:
  a 320 px el visual dispone de ≈ 230 px útiles. Es la principal pérdida de espacio para tarjetas deportivas.
- **R2 (M)**: la clasificación (10 columnas) depende de `overflow-x-auto` sin columna fija: al desplazar se pierde el nombre del equipo.
- **R3 (B)**: un único punto de ruptura (`md:`) para la barra lateral; en tablet vertical (768 px) la barra de 288 px deja 480 px al chat.

## 1.5 Estados de carga, error y vacío

| # | Hallazgo | Sev. |
|---|---|---|
| S1 | La carga es un texto con `animate-pulse` («Futsito está consultando los datos…»); sin identidad, sin tiempo transcurrido | M |
| S2 | Aviso de lentitud a los 20 s (`SLOW_MS`) y cancelación: bien resuelto funcionalmente, mal jerarquizado (texto de 12 px subrayado) | M |
| S3 | **Estado de partido sin traducir**: `Matches.tsx` muestra `m.status` tal cual para todo lo que no sea `finished`/`scheduled` → el usuario ve `postponed`, `suspended`, `cancelled` o `unknown` | A |
| S4 | No hay estados vacíos propios por visual: si el backend no envía visual, solo queda el texto (correcto como contrato, pero sin guía visual) | B |
| S5 | El estado de la cola (`health.queue.busy/waiting`) llega pero no se muestra | B |
| S6 | El error de respuesta es una caja roja coherente y con reintento; usa el mismo rojo que la marca (confusión marca/error) | M |

## 1.6 Presentación de respuestas del agente (AX)

| # | Hallazgo | Sev. |
|---|---|---|
| X1 | **Las fuentes se quitaron de la UI** (commit `c80095a`) aunque el backend sigue enviando `sources[]` con `updated_at`/`freshness`. Solo queda la insignia `answer_kind`. Para un asistente de datos oficiales es la pérdida de confianza más importante | A |
| X2 | Las **aclaraciones** (`diagnostics.context.status = "ask"` con `options[]`, solo con `AGENT_CONTEXT=deterministic|llm`) se pintan como una respuesta normal; las opciones solo aparecen en «Detalles técnicos» y no se pueden pulsar | M |
| X3 | Los **supuestos** (`status = "assume"`, `assumption`) se anteponen al texto sin distinción visual | M |
| X4 | «Detalles técnicos» (tokens, modelo, herramientas) y el modo de contexto en la cabecera se muestran a todos los usuarios: útil para desarrollo, ruido para el usuario final | M |
| X5 | Duplicidad texto/tarjeta: ya mitigada en backend (`reconcile.py` omite filas respaldadas por un visual). El frontend no necesita lógica extra; sí un orden visual estable (texto breve → visual → acciones) | ✓ |
| X6 | Insignia de análisis (`is_analysis`) en ámbar: correcto en concepto; texto largo en móvil | B |
| X7 | El orden actual en la tarjeta es: insignias → Markdown → aviso de error parcial → visuales → sugeridas → valoración → detalles. Las sugeridas quedan entre el visual y la valoración; en móvil la acción principal (seguir preguntando) se aleja del campo de entrada | B |

## 1.7 Navegación y conversaciones

- Real: lista de conversaciones con título derivado de la primera pregunta, nueva conversación, borrar, persistencia local
  y olvido en servidor al borrar. La pregunta en curso se ata a su conversación (`loadingId`), pero **se bloquea el envío en
  todas** mientras hay una en vuelo (`loading = loadingId !== null`).
- Falta: fecha/agrupación («Hoy», «Esta semana»), indicador de conversación con consulta en curso en la barra lateral, renombrar.

## 1.8 Rendimiento

- Sin fuentes web ni imágenes pesadas: el primer render es rápido. Los dos SVG del logo pesan 1,4 y 1,7 KB.
- `react-dom` y `react-markdown` + `remark-gfm` concentran previsiblemente el bundle (reparto no medido); se justifican (las respuestas son Markdown con tablas).
  No se propone sustituirlos sin medir antes (fuera de alcance).
- Riesgo futuro real: los escudos (ver doc 4: un escudo de 464 KB en un grupo de 14 equipos) y una librería de animación
  pesada (ver doc 5: Rive 941 KB gz de wasm, dotLottie 492 KB gz).

## 1.9 Resumen priorizado

1. **A**: contraste (A1–A3), foco visible y menú modal accesible (A5, A6), estados de partido sin traducir (S3), fuentes fuera de la UI (X1).
2. **M**: tokens y modo oscuro (E1–E3), espacio útil en móvil (R1), clasificación con columna fija (R2), loader con identidad (S1–S2), aclaraciones y supuestos (X2–X3), detalles técnicos solo en modo desarrollo (X4), iconos (E6), confirmación al borrar (A8).
3. **B**: el resto.
