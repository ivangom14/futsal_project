# 6. Catálogo de recursos visuales

Criterio: solo recursos con licencia comprobada. «Verificado» = licencia leída en el registro npm (`npm view <pkg> license`)
el 2026-11-12 y/o enlace respondiendo 200. **No se ha descargado ni incorporado ningún recurso al proyecto**: las
mediciones se hicieron en un directorio temporal.

## 6.1 Tipografía

| Recurso | Licencia | Peso medido (latín) | Enlace | Estado |
|---|---|---|---|---|
| **Archivo Variable** (Omnibus-Type) | OFL-1.1 | 90,1 KB (`wght`+`wdth`) | https://fonts.google.com/specimen/Archivo · https://fontsource.org/fonts/archivo | **Recomendado** |
| Inter Variable | OFL-1.1 | 48,3 KB | https://fontsource.org/fonts/inter | Alternativa de texto |
| Barlow Condensed | OFL-1.1 | 21–22 KB por peso | https://fontsource.org/fonts/barlow-condensed | Descartado |
| Bricolage Grotesque | OFL-1.1 | 131,5 KB | https://fontsource.org/fonts/bricolage-grotesque | Descartado |
| Space Grotesk | OFL-1.1 | 22,3 KB | https://fontsource.org/fonts/space-grotesk | Descartado |

OFL-1.1 permite uso comercial, autoalojamiento y subconjuntos; exige conservar la licencia junto a los archivos de la
fuente y no venderla por separado. Al autoalojar, copiar `OFL.txt` junto al woff2.

## 6.2 Iconos

| Recurso | Licencia | Coste medido (20 iconos) | Enlace | Estado |
|---|---|---|---|---|
| **Lucide** `lucide-react` 1.72.0 | ISC | 3,9 KB gz | https://lucide.dev · https://lucide.dev/license | **Recomendado** |
| Phosphor `@phosphor-icons/react` 2.1.10 | MIT | 14,5 KB gz | https://phosphoricons.com | Descartado (peso) |
| Tabler `@tabler/icons-react` 3.51.0 | MIT | no medido | https://tabler.io/icons | Alternativa |

Iconos propios necesarios (no existen en Lucide): `ball` (fútbol sala), `court`, `card`, `crest`. Se dibujan en la
rejilla de Lucide (24 px, trazo 2, extremos redondeados); son originales, sin licencia de terceros.

## 6.3 Animación

| Recurso | Licencia | Coste medido | Enlace | Estado |
|---|---|---|---|---|
| CSS + SVG nativos | — | 0 KB de runtime | https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion | **Recomendado** |
| `lottie-web` 5.13.0 (light) | MIT | 46,4 KB gz | https://github.com/airbnb/lottie-web | No necesario |
| `@lottiefiles/dotlottie-web` 0.83.2 | MIT | wasm 492 KB gz | https://github.com/LottieFiles/dotlottie-web | No necesario |
| `@rive-app/canvas` 2.50.0 | MIT (runtime) | wasm 941 KB gz | https://rive.app | No necesario |

Animaciones de bancos (LottieFiles, GIPHY, Tenor…): **no recomendadas**. Las licencias varían por pieza (algunas exigen
atribución o prohíben uso comercial), las de fútbol suelen mostrar fútbol 11 y no transmiten la identidad propia. Si en
el futuro se usa alguna, registrar en este archivo: autor, URL de la pieza, licencia exacta y fecha de comprobación.

## 6.4 Ilustraciones (propias, a producir)

| Pieza | Uso | Formato | Base |
|---|---|---|---|
| Futsito saludando sobre líneas de pista | Bienvenida | SVG en línea | `frontend/public/futsito-mark.svg` |
| Futsito dando toques | Loader (doc 5) | SVG en línea + CSS | ídem (borrador en la demo) |
| Área de 6 m vacía con balón | Estado vacío | SVG | líneas de pista |
| Balón fuera de banda | Error | SVG | ídem |
| Escudo genérico con monograma | Fallback de escudo | Componente SVG | icono `crest` |

La mascota y el logo actuales son propios del proyecto (`docs/phase25-futsito-blocks-2-7.md`, bloque 7: «sin reutilizar
el escudo de la RFFM, el logo de Ibercaja ni su camiseta»). Se mantiene esa regla.

## 6.5 Escudos de equipos

| Fuente | Condiciones | Enlace |
|---|---|---|
| RFFM (`appweb.rffm.es/pnfg/pimg/Clubes/…`) | Aviso legal: prohibido reproducir/transformar sin autorización del titular | https://www.rffm.es/aviso-legal |

Detalle y propuesta: doc 4.

## 6.6 Referencias de buenas prácticas

| Tema | Enlace |
|---|---|
| WCAG 2.2 | https://www.w3.org/TR/WCAG22/ |
| Movimiento reducido | https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion |
