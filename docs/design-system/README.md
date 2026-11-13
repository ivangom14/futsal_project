# Sistema de diseño de Futsito — «Pista»

Sistema de diseño del frontend de Futsito. **Estado: P0 implementada** (tokens, claro/oscuro, Archivo, Lucide, contraste AA,
estados de partido, menú móvil accesible; ver [08-p0-implementacion.md](08-p0-implementacion.md)). P1 implementada ([09-p1-bloque1.md](09-p1-bloque1.md), [10-p1-bloque2.md](10-p1-bloque2.md)); solo queda `ScoreHero`, que depende de P2. P2–P3 siguen en propuesta.
Backend, MCP y agente no se han modificado.

| # | Documento | Contenido |
|---|-----------|-----------|
| 1 | [01-auditoria-frontend.md](01-auditoria-frontend.md) | Estado real del frontend, problemas medidos y priorizados |
| 2 | [02-inventario-componentes-estados.md](02-inventario-componentes-estados.md) | Componentes, contratos visuales y máquinas de estado implícitas (reales vs. propuestas) |
| 3 | [03-guia-estilos.md](03-guia-estilos.md) | Identidad, direcciones comparadas, tokens, color, tipografía, iconos, movimiento, componentes, responsive, accesibilidad |
| 4 | [04-escudos-equipos.md](04-escudos-equipos.md) | Escudos oficiales: dónde están en la RFFM, medidas, legalidad, arquitectura y fallback |
| 5 | [05-loader-futsito.md](05-loader-futsito.md) | Animación «Futsito dando toques»: comparativa de técnicas y especificación |
| 6 | [06-recursos-visuales.md](06-recursos-visuales.md) | Catálogo de fuentes, iconos y runtimes con licencia y peso verificados |
| 7 | [07-plan-implementacion.md](07-plan-implementacion.md) | Fases, prioridades, criterios de aceptación y cómo no romper nada |
| 8 | [08-p0-implementacion.md](08-p0-implementacion.md) | Qué se implementó en P0, validación y medidas antes/después |
| 9 | [09-p1-bloque1.md](09-p1-bloque1.md) | P1, primer bloque: chat a ancho completo, loader, caja de entrada, diagnóstico oculto y fuentes |
| 10 | [10-p1-bloque2.md](10-p1-bloque2.md) | P1, segundo bloque: componentes base, escudos con monograma, clasificación, goleadores, aclaraciones, contexto y deshacer |
| — | [demo/futsito-design-demo.html](demo/futsito-design-demo.html) | Demo estática aislada (abrir en el navegador): tokens, claro/oscuro, componentes, loader |

## Decisiones en una línea

- **Dirección**: «Pista» — identidad de fútbol sala (líneas de pista, marcador, dorsales, mascota) sobre una base de chat sobria.
- **Color**: rojo camiseta `#C0281E` (marca y acción principal) + neutros cálidos «tiza/parqué» + azul pista `#1B5FA6` (información, foco, serie 2) + oro marcador `#F2B21B` (destacados). Claro y oscuro, todos los pares de texto ≥ 4,5:1 (medidos).
- **Tipografía**: **Archivo Variable** (OFL, ejes `wght` 100–900 y `wdth` 62–125, 90 KB woff2 latín) para marca, títulos, marcadores y tablas; **pila del sistema** para la conversación (0 KB).
- **Iconos**: **Lucide** (ISC; 20 iconos = 3,9 KB gz medidos) + 4 iconos propios en la misma rejilla (balón de fútbol sala, pista, tarjeta, escudo genérico).
- **Loader**: SVG en línea + CSS (`transform`), Futsito dando toques; 2,2 KB medidos (0,8 KB gzip), sin dependencias, estático con `prefers-reduced-motion`.
- **Escudos**: `crest-backfill` rellena `teams.crest_url` desde las instantáneas ya descargadas; con `CRESTS_MODE=link` la interfaz enlaza las imágenes oficiales (solo pruebas privadas; doc 4 §4.8). Con autorización, copias normalizadas (611 KB → 48,5 KB medidos).

## Medidas de referencia (2026-11-12)

| Medida | Valor | Cómo |
|---|---|---|
| Bundle JS actual | 354,2 KB (110,0 KB gzip) | `vite build` en el scratchpad |
| CSS actual | 38,3 KB (6,6 KB gzip) | ídem |
| Fuentes web actuales | 0 (pila del sistema) | `index.css`, `tailwind.config.js` |
| 14 escudos del grupo objetivo | 611,7 KB originales; 48,5 KB en WebP 96 px | descarga única a `appweb.rffm.es` con 1 s de pausa; Pillow en venv temporal |
