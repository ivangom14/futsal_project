# 5. Loader de Futsito: «toques»

## 5.1 Objetivo

Mostrar, mientras el agente procesa (`loadingId === conversación activa`), a Futsito dando toques al balón: identidad de
marca, feedback de «estoy trabajando» y nada más. Sin pasos falsos (el backend no transmite progreso).

## 5.2 Técnicas comparadas (medido donde se indica)

| Técnica | Peso | Nitidez | Modo oscuro | Movimiento reducido | Dependencias | Veredicto |
|---|---|---|---|---|---|---|
| GIF optimizado | 20–150 KB típico (no medido; depende de fotogramas) | Raster; borroso a 2×/3× | Transparencia de 1 bit → halo sobre fondo oscuro | No se puede pausar: hay que cambiar a una imagen estática aparte | 0 | ✗ |
| Sprite sheet (WebP/PNG + `steps()`) | 15–40 KB (no medido) | Raster; hace falta versión 2× | Bien (alfa) | Bien (fotograma fijo) | 0 | Posible, pero rígido: cada cambio de dibujo exige re-renderizar |
| **SVG en línea + CSS** | **2,2 KB (0,8 KB gzip)** medidos en el borrador de la demo (`demo/futsito-design-demo.html`: SVG 1 252 B + CSS 992 B) | Vectorial | Colores por tokens | `@media (prefers-reduced-motion)` | 0 | **✓ Recomendado** |
| SVG con SMIL | ~2,5 KB | Vectorial | Bien | **No respeta** la media query sin JS | 0 | ✗ |
| Lottie (`lottie-web` light) | runtime **46,4 KB gz** (medido, `lottie_light.min.js` 5.13.0) + JSON | Vectorial | Colores fijos en el JSON | Con JS | +1 | ✗ para un loader |
| dotLottie (`@lottiefiles/dotlottie-web`) | wasm **492 KB gz** (medido, 0.83.2) | Vectorial | — | Con JS | +1 | ✗ |
| Rive (`@rive-app/canvas`) | wasm **941 KB gz** (medido, 2.50.0) | Vectorial, interactivo | — | Con JS | +1 | ✗ (justificable solo para una mascota interactiva completa) |

Justificación: el loader se ve en **todas** las consultas; su coste debe ser cercano a cero. SVG+CSS anima solo
`transform`, se compone en GPU, hereda los tokens de color y degrada de forma nativa con movimiento reducido.

## 5.3 Especificación

**Dibujo** (`viewBox="0 0 120 120"`, derivado de `futsito-mark.svg`): cabeza y orejas del osito, camiseta roja,
dos piernas (la derecha articulada en la cadera), balón de fútbol sala de 9 px de radio y su sombra elíptica.

**Ciclo** de 900 ms (≈ ritmo real de toques suaves), en bucle solo mientras dura la consulta:

| Tiempo | Balón | Pierna derecha | Sombra |
|---|---|---|---|
| 0 % | Contacto con el pie, aplastado `scale(1.08, .92)` | Levantada −18° | 100 % |
| 0→50 % | Sube 38 px, `--fs-ease-out` (desacelera), gira 180° | Baja a 0° (0–20 %) | Se reduce a 60 %, opacidad .5 |
| 50→100 % | Cae, `--fs-ease-in` (acelera) | Vuelve a −18° al final (80–100 %) | Crece |

```css
.fl-ball   { animation: fl-bounce .9s infinite; transform-box: fill-box; transform-origin: center; }
.fl-leg    { animation: fl-kick .9s infinite;   transform-box: fill-box; transform-origin: 30% 0; }
.fl-shadow { animation: fl-shadow .9s infinite; transform-box: fill-box; transform-origin: center; }
@keyframes fl-bounce {
  0%   { transform: translateY(0) scale(1.08,.92); animation-timing-function: var(--fs-ease-out); }
  50%  { transform: translateY(-38px) rotate(180deg); animation-timing-function: var(--fs-ease-in); }
  100% { transform: translateY(0) rotate(360deg) scale(1.08,.92); }
}
@keyframes fl-kick   { 0%,100% { transform: rotate(-18deg); } 20%,80% { transform: rotate(0); } }
@keyframes fl-shadow { 0%,100% { transform: scale(1); opacity: .9; } 50% { transform: scale(.6); opacity: .45; } }
@media (prefers-reduced-motion: reduce) {
  .fl-ball, .fl-leg, .fl-shadow { animation: none; }
  .fl-ball { transform: translateY(-20px); }   /* pose fija: balón en el aire */
}
```

**Comportamiento**

| Regla | Valor | Motivo |
|---|---|---|
| Retardo de aparición | 300 ms | Evita el parpadeo en respuestas rápidas |
| Tamaño | 56 px (móvil) / 64 px; alto reservado | Sin salto de maquetación |
| Texto | «Futsito está consultando los datos oficiales…» | Honesto, sin pasos inventados |
| Tiempo transcurrido | desde 5 s: «12 s» (`tabular-nums`) | Feedback real |
| Lento (≥ `SLOW_MS` = 20 s) | `InlineNotice warning` con la explicación actual | Ya existe la lógica |
| Cancelar | Botón `secondary` 40 px junto al texto **y** el botón del composer pasa a «Detener» | Objetivo táctil |
| Accesibilidad | contenedor `role="status"` `aria-live="polite"`; SVG `aria-hidden`; solo el texto se anuncia, una vez | No saturar el lector |
| Fin | Fundido 120 ms; no hay «celebración» | Sobriedad |
| Variante compacta | `QuintetoDots` (5 puntos) para botones o espacios < 32 px | Mismo lenguaje |

## 5.4 Cómo validarlo antes de producción

1. Medir el SVG final minificado y su CSS (borrador actual: 2,2 KB, 0,8 KB gzip; objetivo ≤ 3 KB).
2. Chrome DevTools → Performance: 10 s de loader en un móvil de gama media emulado (CPU 4×), comprobar que no hay
   *layout* ni *paint* por fotograma (solo composición).
3. Activar «Reducir movimiento» en el sistema y comprobar la pose fija.
4. Revisar con lector de pantalla (VoiceOver/TalkBack) que se anuncia una sola vez.
