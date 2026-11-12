# 4. Escudos oficiales de los equipos

## 4.1 ¿Están ya en el flujo de datos?

**Sí en la fuente, no en la aplicación.** La RFFM incluye la ruta del escudo en las cuatro páginas que el proyecto ya
descarga, pero la ingesta la descarta:

| Página RFFM (ya en uso) | Campo | Ejemplo | Qué hace hoy el código |
|---|---|---|---|
| Resultados y jornadas | `url_img_local`, `url_img_visitante` | `/pnfg/pimg/Clubes/00100_0012168000_ESCUDO_LOPE.png` | `ingestion/rffm/league.py`/`parser.py` no lo leen |
| Clasificación | `url_img` | ídem | `ingestion/rffm/tables.py:37` lo **excluye explícitamente** (`_STANDINGS_SKIP`) |
| Calendario | `escudo_equipo_local`, `escudo_equipo_visitante` | ídem | no se ingiere hoy |
| Acta | `escudo_local`, `escudo_visitante` | `…_download.jpg?nova=1` | `report_parser.py:192` lo parsea a `ReportTeam.crest_url`, pero `repositories/match_reports.py` **no lo persiste** |

En base de datos ya existen **`teams.crest_url`** y **`clubs.crest_url`** (migración `0007_catalogo_e_identidad`), sin
ningún escritor. La API, el MCP y `presentation.py` no exponen escudos.

Identidad: el número del escudo **no** identifica al club (cambia dentro de la temporada; `docs/v2/probe-a1.md`, hallazgo 9).
El escudo es un **atributo** del equipo en una fecha, nunca una clave.

## 4.2 Medidas reales (14 escudos del grupo objetivo, 2026-11-12)

Descarga única desde `https://appweb.rffm.es` (host base que ya usa `report_parser`), 1 s entre peticiones, a un
directorio temporal fuera del repositorio.

| Medida | Resultado |
|---|---|
| Cabeceras | `200`, `Cache-Control: max-age=15552000, public` (180 días), `ETag`, `Last-Modified`; **sin** `Access-Control-Allow-Origin` |
| Formatos | 8 JPEG (sin transparencia, fondo blanco o negro) y 6 PNG RGBA |
| `Content-Type` | **no fiable**: un archivo `.png` servido como `image/png` es un JPEG (detectar por bytes mágicos) |
| Dimensiones | 12 de 14 ≈ 100 px de lado; uno 264×238; uno **1268×1796** |
| Proporción | de 1,52:1 (100×66) a 0,71:1 (1268×1796) |
| Peso | 3,2 KB a **464 KB**; total **611,7 KB** |
| Normalizado a 96×96 WebP q82 (lienzo transparente, `contain`) | 2,3 a 5,3 KB; total **48,5 KB (−92 %)** |
| Normalizado a 96×96 PNG | total 241,2 KB (WebP es claramente mejor) |

Conclusión: enlazar el original funciona y cachea bien, pero un solo escudo puede costar 464 KB y los JPEG traen
cajas de fondo. Solo una copia normalizada da un resultado homogéneo y ligero.

## 4.3 Condiciones de uso (leído, no es asesoramiento jurídico)

El aviso legal de `www.rffm.es/aviso-legal` (consultado 2026-11-12) indica que los contenidos, **incluidas imágenes**,
pertenecen al titular del sitio o a terceros, y prohíbe «reproducir, copiar, distribuir, poner a disposición […],
transformar o modificar los contenidos, a menos que se cuente con la autorización del titular de los correspondientes
derechos o ello resulte legalmente permitido». Además, cada escudo es signo distintivo de su club.

Consecuencias para el diseño técnico:

| Opción | Implica | Riesgo | Requisito |
|---|---|---|---|
| **A. Solo URL + enlace directo** (el navegador carga de `appweb.rffm.es`) | No se copia ni transforma nada; Futsito guarda una URL, igual que guarda otros datos públicos | Bajo en propiedad intelectual; **privacidad**: la RFFM recibe la IP del usuario; dependencia de su disponibilidad | `referrerpolicy="no-referrer"`; mencionarlo en la política de privacidad; bandera para apagarlo |
| **B. Proxy con caché y normalización** | Reproducción y transformación | Requiere autorización | Permiso escrito de la RFFM (y, si procede, de los clubes) |
| **C. Sin escudos** | Fallback para todos | Ninguno | — |

**Recomendación**: implementar A detrás de una bandera (`CRESTS_MODE=off|link`, por defecto `off` en producción
hasta revisar privacidad) y **pedir autorización** a la RFFM para B. El diseño de componentes es idéntico en A y B;
solo cambia la URL que entrega la API.

## 4.4 Arquitectura propuesta (incremental, sin romper contratos)

### Paso 1 — Ingesta (sin migraciones, sin peticiones extra)
- `league.py`/`parser.py`: leer `url_img_local`/`url_img_visitante` de las jornadas **ya descargadas** y guardar la ruta
  relativa normalizada (sin `?nova=1`) en `teams.crest_url`. La URL absoluta se compone en la API con una constante
  `RFFM_ASSET_HOST = "https://appweb.rffm.es"` (si cambia el host, no hay que reingestar).
- Regla de actualización: la última jornada observada manda; el cambio se registra como cualquier otra observación.
- `tables.py`: quitar `url_img` de `_STANDINGS_SKIP` solo si se necesita para equipos sin partido en jornadas (no debería).

### Paso 2 — API (aditivo)
- `crest_url: str | null` en `/groups/{id}/teams`, `/teams`, partidos (`home_crest_url`, `away_crest_url`) y filas de
  clasificación. Campos nuevos opcionales: los clientes actuales los ignoran.
- Con `CRESTS_MODE=off` el campo es `null` siempre.

### Paso 3 — Visuales (aditivo)
- `presentation.py`: `home_crest`/`away_crest` en filas de `matches`, `crest` en filas de `table`, `bars` (por
  `sublabel` → equipo) y cabeceras `lineup`/`timeline`/`cards`. El MCP no cambia: el visual se compone en el servicio web,
  que ya conoce los equipos por API.

### Paso 4 — Solo con autorización: `/assets/crests/{team_id}.webp`
- Endpoint en `futsal.api` que sirve una copia normalizada (96×96 WebP, lienzo transparente, `contain`).
- Generación **en la ingesta** (no al vuelo): descarga con `PoliteFetcher`, validación por bytes mágicos y tamaño
  máximo (2 MB), conversión con Pillow (dependencia nueva justificada solo en este paso), almacenamiento en
  `data/crests/<sha256>.webp` (ignorado por Git) y en BD solo el hash.
- Respuesta con `Cache-Control: public, max-age=31536000, immutable` y URL con hash → invalidación por cambio de URL.
- Coste medido de referencia: 48,5 KB para un grupo de 14 equipos.

## 4.5 Componente `TeamCrest`

```tsx
<TeamCrest name="LOPE DE VEGA FS" src={row.home_crest} size={24} />
```

- Contenedor cuadrado `size × size`, fondo **`--fs-crest-tile` (blanco) en claro y oscuro**, borde 1 px `border`,
  radio `size/4`, relleno `max(2px, size/12)`. El fondo blanco común neutraliza las cajas blancas/negras de los JPEG y
  evita escudos oscuros invisibles en modo oscuro.
- `<img width height loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="">` con `object-fit: contain`.
  `alt=""` porque el nombre del equipo siempre está al lado (evita leerlo dos veces).
- Error de carga o `src` nulo → **fallback** en el mismo contenedor (sin salto de tamaño).
- Tamaños: 16 (goleadores, texto), 20 (partidos a 320 px), 24 (partidos, clasificación), 32 (partido ancho), 48 (`ScoreHero`), 56 (`TeamCard`).

### Fallback: monograma en escudo genérico

- Forma: icono propio `crest` (escudo de punta redondeada) relleno con un tono derivado de forma determinista del
  nombre del equipo sin sufijos de categoría (hash → 1 de 8 tonos de la paleta con texto blanco ≥ 4,5:1).
- Texto: 1–2 iniciales significativas en Archivo 750 `wdth` 87.5 (se ignoran «FS», «F.S.», «CD», «CF», «AD», «CDE», «A/B/C» y artículos):
  «LOPE DE VEGA FS» → «LV»; «CARDENAL SPÍNOLA CHAMARTÍN» → «CS».
- Nunca imita un escudo real (sin colores del club: no se conocen y se inventarían).

## 4.6 Impacto responsive

| Contexto | 320 px | ≥ 600 px |
|---|---|---|
| Fila de partido apilada | escudo 20 + 8 px de hueco: −28 px al nombre (las 4 líneas de `line-clamp` siguen bastando; verificar con `scripts/check-layout.mjs`) | 24–32 px |
| Clasificación | escudo 20 dentro de la columna fija Equipo | 24 |
| Goleadores | 16 junto al nombre del equipo | 16 |
| Texto del chat | sin escudos (no se interpreta texto libre) | — |

## 4.7 Prioridad

1. `TeamCrest` con fallback (solo frontend, útil desde el primer día aunque no haya URLs).
2. Pasos 1–3 con `CRESTS_MODE` (backend aditivo, sin migraciones).
3. Solicitud de autorización a la RFFM → paso 4.
