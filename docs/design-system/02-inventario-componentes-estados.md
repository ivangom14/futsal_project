# 2. Inventario de componentes y estados

Leyenda de estado: **Real** = existe hoy en el código; **Backend** = el dato llega pero la UI no lo usa;
**Propuesta** = mejora que requiere trabajo (indicado dónde).

## 2.1 Componentes existentes

| Componente | Archivo | Rol | Reutilización / problemas | Destino en el sistema |
|---|---|---|---|---|
| `App` | `App.tsx` | Orquesta estado, salud, envío, reintento, cancelación | Carga inline (texto + pulse) | Extraer `AgentStatus` (loader) |
| `Sidebar` | `Sidebar.tsx` | Conversaciones | No es modal accesible en móvil | `ConversationList` + `Drawer` |
| `Composer` | `Composer.tsx` | Entrada, Enter envía, 2 000 caracteres | Sin auto-altura, sin cancelar dentro | `Composer` v2 |
| `Welcome` | `Welcome.tsx` | Bienvenida y 4 ejemplos | Ejemplos como botones de ancho completo | `Welcome` + `PromptChip` |
| `MessageBubble` | `MessageBubble.tsx` | Usuario / error / agente | Tres variantes en un archivo; burbuja del agente estrecha | `UserMessage`, `AgentTurn`, `ErrorTurn` |
| `Badges` | dentro de `MessageBubble` | `answer_kind` + `is_analysis` | — | `ProvenanceBadge` |
| `Visuals` | `Visuals.tsx` | Despacha por `visual.type`; incluye tabla, barras, comparativa, forma | `Frame` local | `VisualFrame` + un archivo por visual |
| `Matches` | `Matches.tsx` | Lista de partidos (container queries) | Repite la cabecera de `Frame`; estado sin traducir | `MatchList` / `MatchRow` / `Scoreline` |
| `Lineup` | `Lineup.tsx` | Pista SVG + suplentes | Hex fijos | tokens vía `currentColor`/CSS vars |
| `Cards` | `Cards.tsx` | Tarjetas por partido o lista | Hex fijos | ídem + icono `card` propio |
| `Officials` | `Officials.tsx` | Árbitros con monigotes | Hex fijos | ídem |
| `Timeline` | `Timeline.tsx` | Goles por minuto | Emoji `⚽` | icono `ball` propio |
| `Suggestions` | `Suggestions.tsx` | Sugeridas (solo última respuesta) | — | `PromptChip` (misma pieza que Welcome) |
| `FeedbackBar` | `FeedbackBar.tsx` | 👍/👎 + motivo | Emoji, objetivos pequeños | `FeedbackBar` con iconos |
| `TechnicalDetails` | `TechnicalDetails.tsx` | Diagnóstico | Visible a todos | Solo modo desarrollo |
| `ConnectionBadge` | `ConnectionBadge.tsx` | Salud del servicio | Muestra `context_mode` (dev) | `StatusDot` |
| `ErrorBoundary` | `ErrorBoundary.tsx` | Fallo de render | — | Estado vacío ilustrado «Balón fuera» |
| `Logo` | `Logo.tsx` | `img` a SVG en `public/` | — | `Brand` (+ `FutsitoLoader`) |

Visuales que el backend genera hoy (`presentation.py`): `table` (clasificación), `bars` (goleadores), `comparison`
(dos equipos de la clasificación), `form` (últimos resultados), `matches`, `lineup`, `officials`, `timeline`, `cards`.
**No existe** un visual de «ficha de equipo»: es una propuesta (doc 3, §F.9) que requiere un tipo nuevo en el backend.

## 2.2 Máquinas de estado implícitas

### Chat (por conversación) — Real

```
          escribir              Enter / Enviar            respuesta 2xx
 [vacío] ────────▶ [componiendo] ───────────────▶ [esperando] ─────────────▶ [respondido]
    ▲                    ▲                         │  │  │                     │
    │                    └──────── (siguiente) ────┼──┼──┼─────────────────────┘
    │                                   20 s        │  │  └─ abort usuario ─▶ [fallo: "Has cancelado"]
    │                              ┌───────────────┘  │
    │                              ▼                  └─ red / HTTP / 120 s ─▶ [fallo] ──Reintentar──▶ [esperando]
    │                         [esperando·lento]
    └── nueva conversación
```

| Estado | Señal en código | Visual hoy | Visual propuesto |
|---|---|---|---|
| vacío | `messages.length === 0` | `Welcome` | Bienvenida ilustrada + chips |
| componiendo | `text` en `Composer` | botón habilitado si hay texto | contador cerca del límite (≥ 1 800) |
| enviando→esperando | `loadingId === conv.id` | texto pulsante | `FutsitoLoader` tras 300 ms + tiempo transcurrido |
| esperando·lento | `slow` (≥ `SLOW_MS` = 20 s) | texto ámbar | Mismo loader + aviso con jerarquía + botón «Cancelar» de 40 px |
| respondido | `message.response` | burbuja | `AgentTurn` |
| respondido parcial | `response.error` | aviso ámbar | `InlineNotice tone=warning` |
| fallo | `message.failure` | caja roja + Reintentar | `ErrorTurn` (tono `danger`, icono, no rojo de marca) |
| cancelado | `ApiError code=cancelled` | caja roja | `ErrorTurn tone=neutral` (no es un error del sistema) |

No existe un estado «respondiendo» (streaming): `POST /chat` devuelve todo al final. **No se debe simular**
progreso por pasos que no ocurren (ver §2.3).

### Consulta al agente — Real en backend, opaca en la UI

`petición → [contexto/ámbito] → (0..5 llamadas a herramientas MCP) → conciliación texto/visual → respuesta`.
Los pasos quedan en `diagnostics` (`tools[]`, `llm_ms`, `tools_ms`, `context`, `reconciliation`) **después** de responder.
Hoy solo se ven en «Detalles técnicos».

| Paso | Dato disponible | Uso visual propuesto |
|---|---|---|
| Herramientas usadas | `diagnostics.tools[].name/summary/is_error` | Pie «Consultado: partidos · clasificación» (traducción fija por nombre de tool) |
| Ámbito resuelto | `diagnostics.resolved_scope.competitions` | Chip de contexto «Primera Autonómica · Grupo 2» |
| Fuentes | `sources[]` (`title`, `updated_at`, `freshness`) | `SourceFooter` plegado: «Datos oficiales RFFM · actualizado 09/11» |
| Error de tool | `tools[].is_error` | Sin cambio (ya se refleja en `response.error`) |

### Aclaraciones y preguntas encadenadas — Backend (solo con `AGENT_CONTEXT=deterministic|llm`)

`diagnostics.context.status ∈ {act, assume, ask, missing, nodata, none}`; con `ask` llegan `options[]` y `ask_about`
(`team|competition|group|scope`).

| Estado | Hoy | Propuesta (frontend; sin cambio de contrato) | Mejora futura de contrato |
|---|---|---|---|
| `ask` | Texto normal | `ClarificationTurn`: pregunta + opciones como `PromptChip` que envían la opción | Campo de primer nivel `clarification {question, options}` para no leer `diagnostics` |
| `assume` | Texto con el supuesto antepuesto | `AssumptionNote` sobre el texto: «Entiendo que hablas de …» | `assumption` de primer nivel |
| `missing` / `nodata` | Texto | `EmptyState` contextual | — |
| encadenada | `historyFor`/`focusFor` + aviso en Composer | `ContextChip` encima del Composer con el foco (`focus.team`, `focus.competition`) y botón «×» que inicia conversación nueva | — |

Compromiso AX/DX: leer `diagnostics.context` en la UI es frágil (es un campo de diagnóstico). Se acepta en una
primera fase porque no cambia contratos; la fase 4 del plan propone promoverlo a campo explícito.

### Componentes deportivos — Real

Los visuales llegan **ya resueltos** con la respuesta: no cargan por su cuenta. Sus estados reales son:

| Estado | Señal | Visual propuesto |
|---|---|---|
| con datos | `rows.length > 0` | componente normal |
| parciales | `partial: true` (tabla, barras, partidos, timeline, cards) | `PartialNote` en el pie: «Se muestran 8 de 26» / «La fuente está incompleta» |
| sin visual | backend no envía visual | solo texto (contrato vigente: el texto es la alternativa) |
| tipo desconocido | `default: return null` | se mantiene (degradación segura) |
| desactualizado | `past_due` en partidos; `freshness: stale` en fuentes | `StaleNote` con icono reloj |
| estado de partido | `finished`, `scheduled`, `postponed`, `suspended`, `cancelled`, `unknown` | `MatchStatusChip` con traducción (doc 3 §F.4) |

«Cargando» y «error» de un componente deportivo **no existen** hoy y no hacen falta mientras el visual venga en la
respuesta. Aparecerían si en el futuro hay vistas navegables fuera del chat (Propuesta, fuera de alcance).

### Selección contextual (equipo/competición/grupo) — Real en backend

El foco (`Focus`: team, player, competition, group, round) viaja en cada petición (`focusFor`) y vuelve en
`response.focus`. La UI solo lo indica con el texto «Futsito tiene en cuenta los mensajes anteriores…». Propuesta:
`ContextChip` (arriba).

### Conexión — Real

`checking → ok | degraded | offline`, sondeo cada 15 s y al pulsar. `degraded` explica la causa en `title`.
Propuesta: punto de estado sin texto en móvil (texto en escritorio) y banner solo si `offline`/`degraded` impide preguntar.

### Valoración — Real

`sin valorar → (👍 → enviado) | (👎 → pidiendo motivo → enviado | error → reintento) → cambiar`.

### Cola — Backend

`health.queue {busy, running_for_s, waiting}` sin uso. Propuesta: en `esperando`, si `waiting > 0`, «Hay N consultas
antes que la tuya». Limitación: el dato tiene hasta 15 s de antigüedad (sondeo); se mostraría solo como orientación.

## 2.3 Reglas AX derivadas

1. **Un dato, un lugar**: si hay visual, el texto resume y el visual detalla (el backend ya concilia). La UI nunca
   repite filas del visual en texto.
2. **Procedencia siempre visible y discreta**: insignia de tipo + pie de fuentes con fecha de actualización.
3. **Nada de progreso inventado**: sin streaming, el loader comunica «estoy trabajando» y el tiempo transcurrido,
   no pasos ficticios. Con streaming real (fase 5 del plan) se mostrarían las herramientas reales.
4. **Distinguir tipos de turno** por forma, no solo por color: pregunta (burbuja derecha), respuesta (sin burbuja,
   a ancho completo), aclaración (borde azul pista + opciones), análisis (insignia oro), error (icono + borde `danger`).
5. **Tipos nuevos de visual**: un componente por tipo, registrado en un mapa `type → componente` y envuelto en
   `VisualFrame` (título, subtítulo, pie parcial/fuente). Tipo desconocido → `null`.
