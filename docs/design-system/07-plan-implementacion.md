# 7. Plan de implementación por fases

Principio: **cambiar la piel sin tocar los contratos**. Las fases 0–3 son solo frontend y no alteran `ChatResponse`,
`api.ts`, `conversations.ts` ni el backend. Cada fase termina con la batería Vitest completa, `npm run build`,
`scripts/check-layout.mjs` a 320/360/768/1280 px y la revisión de contraste.

| Fase | Contenido | Toca | Prioridad | Esfuerzo |
|---|---|---|---|---|
| 0 | Correcciones de accesibilidad y estados (sin rediseño) — **implementada salvo fuentes, detalles técnicos y borrado (→ P1)** | frontend | **P0** | S |
| 1 | Tokens, tema claro/oscuro, fuente, iconos — **implementada** (doc 8) | frontend | **P0** | M |
| 2 | Componentes base (`ui/`) y turnos del chat — **turnos, loader, Composer, diagnóstico y fuentes implementados** (doc 9) | frontend | **P1** | M |
| 3 | Visuales deportivos con el sistema + `TeamCrest` (con fallback) + loader | frontend | **P1** | M |
| 4 | Escudos por API y campos aditivos (`clarification`, `promotions`) | ingesta, API, `presentation.py` | **P2** | M |
| 5 | Streaming de pasos reales del agente (SSE) | `agent/web.py`, frontend | **P3** | L |

## Fase 0 — Correcciones rápidas (P0) · implementada (detalle y pendientes en doc 8)

- Traducir `MatchStatusChip` (`postponed` → «Aplazado», etc.; doc 3 §F.4). **Bug visible hoy.**
- Sustituir `text-slate-400` por un gris con ≥ 4,5:1 y subtítulos de visual (A1–A3).
- Foco visible en todos los botones; cajón móvil con `role="dialog"`, `Escape`, foco atrapado y devuelto (A5, A6).
- `motion-safe:animate-pulse` y scroll `auto` con movimiento reducido (A7).
- Confirmación o «deshacer» (5 s) al borrar conversación (A8).
- Restaurar un **pie de fuentes plegado** con `sources[]` (X1) — el dato ya llega.
- «Detalles técnicos» y `context_mode` solo con `import.meta.env.DEV` o `VITE_SHOW_DIAGNOSTICS=1` (X4).

Aceptación: tests nuevos para la traducción de estados, el cajón (`Escape`, foco) y el pie de fuentes; los existentes en verde.

## Fase 1 — Fundamentos (P0) · implementada (doc 8)

1. `src/ui/tokens.css` con las variables del doc 3 §B.3; `tailwind.config.js` mapea colores semánticos a variables;
   `brand`/`bear` quedan como alias temporales.
2. Tema: `prefers-color-scheme` por defecto + selector «Sistema/Claro/Oscuro» guardado en `localStorage` (try/catch).
3. Archivo autoalojado (`public/fonts/` + `OFL.txt`, `preload`, `font-display: swap`, `size-adjust` de respaldo).
4. `lucide-react` + `src/ui/icons.tsx` (4 propios). Retirar emoji de la UI (`☰ ✕ 🗑 👍 👎 ⚽`).
5. Sustituir hex de los SVG por `var(--fs-…)` / `currentColor`.

Medir antes/después: tamaño gz de JS y CSS, número de peticiones, LCP y CLS en Lighthouse móvil (perfil por defecto)
sobre `vite preview`. **No se promete ninguna mejora**: se publica la medida y se acepta si se cumple el presupuesto
del doc 3 §H.

## Fase 2 — Componentes base y chat (P1)

`Button`, `IconButton`, `Chip` (`PromptChip`/`FilterChip`/`ContextChip`), `Badge`, `InlineNotice`, `EmptyState`,
`Drawer`, `VisualFrame`. Turnos: `UserMessage` (inverso), `AgentTurn` (ancho completo), `ErrorTurn`,
`ClarificationTurn` (lee `diagnostics.context` — compromiso documentado en doc 2), `AssumptionNote`, `SourceFooter`.
Composer v2 (auto-altura, botón Detener, `ContextChip`, `safe-area`).

Estrategia de migración: **un componente por PR**, conservando `data-testid` existentes (`msg-user`, `msg-agent`,
`msg-error`, `visual`, `match-row`, `match-when`, `welcome`, `context-hint`, `slow-hint`, `context-mode`) para que los
69 tests sigan siendo la red de seguridad.

## Fase 3 — Visuales deportivos (P1)

`MatchRow`/`Scoreline`/`ScoreHero`, `StandingsTable` (columnas por contenedor, columna fija), `ScorersList`, forma,
comparativa, y repaso de `Lineup`/`Cards`/`Officials`/`Timeline` con tokens. `TeamCrest` con fallback de monograma
(funciona sin URLs). `FutsitoLoader` + `QuintetoDots` sustituyen el texto pulsante en `App.tsx` sin cambiar la lógica
de `loadingId`/`slow`/`abortRef`.

Fixtures: los `tests/fixtures/chat_response*.json` reales alimentan una página de catálogo de componentes en
desarrollo (`/dev/catalog`, excluida del build) o la demo estática — sin añadir Storybook (dependencia pesada no necesaria
para 25 componentes).

## Fase 4 — Datos para el diseño (P2, backend aditivo)

- Escudos: pasos 1–3 del doc 4 detrás de `CRESTS_MODE` (por defecto `off`). Sin migraciones (`teams.crest_url` existe).
- `clarification {question, options, ask_about}` y `assumption` de primer nivel en `ChatResponse` (opcionales).
- `promotions` en el visual `table` (ya está en la instantánea de clasificación).
- Visual `team` (ficha de equipo, doc 3 §F.9).
- Paso 4 de escudos (proxy normalizado) **solo tras autorización** de la RFFM.

Aceptación: campos opcionales; tests del backend existentes sin cambios; tests nuevos para los campos; frontend
tolera su ausencia.

## Fase 5 — Progreso real del agente (P3)

`POST /chat` con `Accept: text/event-stream` (manteniendo la respuesta JSON actual como predeterminada): eventos
`tool_start`/`tool_end` con nombres de herramienta traducidos («Buscando partidos», «Leyendo el acta») y `final` con el
`ChatResponse` completo. Solo entonces el loader muestra pasos, porque serán reales. Requiere diseño propio en el
backend; fuera del alcance de esta propuesta.

## Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| Regresiones visuales en móvil | `check-layout.mjs` en cada fase (ya mide desbordes a 320 px) + capturas a 2× |
| Romper tests por cambio de DOM | Mantener `data-testid`; cambiar estructura solo dentro de cada componente |
| Peso | Presupuesto del doc 3 §H medido en cada PR |
| Legal (escudos) | `CRESTS_MODE=off` por defecto; autorización antes del proxy |
| Confusión marca/error | Burbuja de usuario en inverso; errores con icono y título (doc 3 §B.1) |
