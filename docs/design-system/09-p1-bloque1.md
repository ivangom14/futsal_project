# 9. P1, primer bloque — chat, loader, caja de entrada, diagnóstico y fuentes

Estado: **implementado en el frontend, sin commit**. Sin cambios en backend, MCP, agente ni en el contrato de `/chat`.
Orden acordado: (1) respuesta a ancho completo y burbuja de usuario, (2) loader y caja de entrada, (3) diagnóstico oculto y fuentes.

## 9.1 Qué cambia

| # | Cambio | Detalle | Archivos |
|---|---|---|---|
| 1 | Respuesta de Futsito **sin burbuja y a todo el ancho** | `<article aria-label="Respuesta de Futsito">` con cabecera (avatar 28 px, «Futsito», insignias de procedencia). Orden: texto → aviso parcial → visuales → fuentes → valoración → sugeridas → (detalles técnicos) | `MessageBubble.tsx` |
| 1 | Pregunta del usuario en **burbuja inversa** (marrón tinta / crema en oscuro) | El rojo queda para marca, acción principal y destacados; los errores ya no compiten con él | `MessageBubble.tsx` |
| 1 | Error a todo el ancho; **cancelación en tono neutro** («Consulta cancelada», icono distinto) | Nuevo campo local `Message.failureCode` (estado del navegador, no de la API) | `MessageBubble.tsx`, `App.tsx`, `types.ts` |
| 2 | **Loader de Futsito** dando toques (doc 5) | SVG + CSS, aparece a los 300 ms, pose fija con movimiento reducido; texto «Futsito está consultando los datos oficiales…», segundos transcurridos desde 5 s (escritos en el DOM, sin re-render; `aria-hidden`), aviso de lentitud con icono, «Cancelar» como botón de 40 px | `FutsitoLoader.tsx`, `AgentStatus.tsx`, `index.css` |
| 2 | **Caja de entrada v2** | Crece con el texto (1→6 líneas), botón redondo «Enviar» (icono) que pasa a **«Detener»** durante la consulta de esa conversación; placeholder corto; ayuda de teclado solo con puntero fino; contador desde 1 800/2 000; margen seguro inferior del iPhone | `Composer.tsx`, `App.tsx` |
| 3 | **Diagnóstico oculto** por defecto | «Detalles técnicos» y el modo de contexto de la cabecera solo con `VITE_SHOW_DIAGNOSTICS=1`, `?debug` en la URL o `localStorage futsito.debug=1`. Las pruebas lo activan en `vite.config.ts` | `diagnostics.ts`, `ConnectionBadge.tsx`, `.env.example` |
| 3 | **Pie de fuentes** plegado | Una línea «Fuente: … y N más»; al abrir, cada fuente con detalle, páginas agrupadas y «actualizado dd/mm/aaaa» o «fecha de actualización no disponible»; aviso si `freshness: stale`; no lista fragmentos no citados (`cited: false`) | `SourceFooter.tsx` |

**Cambio de decisión anterior.** El commit `c80095a` había retirado la sección de fuentes de la UI. Se recupera en forma
mínima (una línea plegada) por la auditoría (X1) y con aprobación expresa. El test que exigía su ausencia se ha reescrito:
ahora comprueba que no vuelve la sección grande y que el pie está plegado y agrupa páginas.

## 9.2 Pruebas

| Prueba | Resultado |
|---|---|
| Vitest | **94/94**: 83 previas + 11 nuevas (`src/design-p1.test.tsx`). Dos tests previos adaptados al cambio aprobado: «Enviar» deshabilitado durante la consulta → «Detener»; ausencia de fuentes → pie plegado |
| Avisos `act(...)` | 6, los mismos que antes de P0 |
| `tsc --noEmit` | sin errores |
| `scripts/check-layout.mjs` (actualizado: busca el campo por su etiqueta «Pregunta», no por el placeholder) | sin scroll horizontal ni desbordes; a 320 px la columna de nombres pasa de 152 a **212 px** y cada partido de 145 a **112 px** de alto (−23 %); a 360 px, de 136 a 103 px |
| Chromium, fixtures reales + error + consulta pendiente, 320/768/1280, claro y oscuro | 0 textos bajo AA (≈ 210 por vista), loader animado (`fl-bounce`), «Detener» visible, tiempo «· 6 s» |

## 9.3 Peso (gzip de Vite)

| | Tras P0 | Tras P1-bloque 1 | Diferencia |
|---|---|---|---|
| JS | 114,20 KB | 116,42 KB | +2,2 KB |
| CSS | 7,30 KB | 7,27 KB | −0,03 KB |

## 9.4 Pendiente de P1 (resuelto en el segundo bloque, doc 10, salvo `ScoreHero`)

- Clasificación con columna de equipo fija y columnas según el ancho; `ScoreHero`; goleadores con el estilo de la guía.
- `TeamCrest` con monograma (sin URLs de escudos hasta P2).
- Aclaraciones pulsables (`ClarificationTurn`), supuestos (`AssumptionNote`) y chip de contexto encima de la caja de entrada.
- Confirmación o «deshacer» al borrar una conversación.
- Componentes base en `src/ui/` (botón, chip, aviso) para unificar estilos repetidos.
