# v2 · Sondeo A1 de la RFFM (2026-11-09)

Autorización A1: solo GET, ≤ 20 peticiones. **Realizadas: 20/20**, todas HTTP 200, ≥ 5,0 s entre peticiones,
2,48 MB en total, latencia máxima 2,8 s, 08:22–08:30 UTC. Script: `scripts/rffm_probe_a1.py` (tope duro y registro).
Registro completo: `data/raw/rffm/probe-a1/requests.jsonl` (fuera de Git, junto a los cuerpos). **Los cuerpos 12–14
contienen datos personales** (dirección, teléfono, correo, presidente, plantillas): no salen de `data/` ni van a fixtures.

| # | Petición | Para qué | Resultado |
|---|---|---|---|
| 1 | `resultados-y-jornadas` T22, grupo conocido | semilla | `seasons` (20), `gameTypes` (5), 50 competiciones FS con metadatos |
| 2 | ídem T21 sin competición | ¿catálogo sin semilla? | **ignora los parámetros**: devuelve T22 y Fútbol‑11 |
| 3 | bundle JS de la página | rutas internas | `/api/results?idGroup&round` |
| 4 | T21 con ID de competición de T22 y sin grupo | ¿ID estable? | igual que 2: sin grupo la página cae al predeterminado |
| 5–6 | `/api/competitions?temporada=21|22&tipojuego=3` | catálogo por temporada | **JSON directo**: T21 = 103 (63 FS federadas + 35 deporte escolar + 5 otras); T22 = 50, idéntico a la página |
| 7 | `/api/groups` de 1ª Aut. Aficionado T21 | equipos T21 | 2 grupos |
| 8 | `/api/results` con parámetros de página | | `null` (parámetros equivocados) |
| 9 | `/api/results?idGroup=…&round=26` | resultados ligeros | **8,9 KB** frente a ~300 KB de la página; mismos campos que `results` |
| 10–11 | `_app` y `_buildManifest.js` | mapa de páginas | `fichaequipo`, `fichaclub`, `fichajugador`, `calendario`, `clubes`, `tabla-cruzada`, `comparativa-equipos` |
| 12–13 | `/fichaequipo/<cod>` (Parque Norte 'B' y 'A') | identidad | `codigo_club` oficial, `nombre_club`, categoría y código de categoría |
| 14 | `/fichaclub/<cod>` | equipos del club | `equipos_club` (3 equipos FS de la temporada actual), delegación |
| 15 | `/competicion/calendario` T22 grupo 2 | carga | **26 jornadas y 182 partidos en una página**, coherente con las jornadas |
| 16 | `/api/competitions` T10 (2014‑15) | histórico | 42 competiciones FS |
| 17–18 | `/api/groups` Alevín y Benjamín 1ª fase T22 | tamaño de base | 9 y 5 grupos (90 y 49 equipos, 9 jornadas) |
| 19–20 | grupos y calendario de una competición de 2014‑15 | histórico | 1 grupo «Eliminatorias», 31 partidos, 26 con resultado |

## Hallazgos (C = comprobado en este sondeo)

1. **Temporadas [C]**: `pageProps.seasons` lista 20 (`cod_temporada` 3 = 2007‑08 … 22 = 2026‑27, con fechas). La actual
   es la 22 (2026‑07‑01 → 2027‑09‑01). No hace falta sondear `N+1`.
2. **Catálogo sin semilla [C]**: `GET /api/competitions?temporada=N&tipojuego=3` devuelve el catálogo completo en JSON.
   Elimina el «problema del arranque» de `ingestion.md` y la dependencia de `config/targets.py`.
3. **La página HTML ignora `temporada`/`competicion` si falta `grupo` [C]** y devuelve T22 y Fútbol‑11 con `200`: una
   respuesta válida pero de **otro ámbito**. El parser debe validar `season.cod_temporada` e `idGameType` contra lo pedido.
4. **Metadatos oficiales [C]**: `NombreCategoria`, `CodigoCategoria`, `cod_grupo_categoria`/`nombre_grupo_categoria`
   (`FUTBOL SALA`, `DEPORTE ESCOLAR FÚTBOL SALA`, `OTRAS CATEGORÍAS COMPETICIONALES`), `tipo_competicion`
   (1 en copas, trofeos y fases finales; 2 en ligas y en «fase de grupos» [C en la muestra]: **formato**, no tipo),
   `Activa` (en T21 todas `0`), fechas, puntos por resultado, minutos y partes.
5. **Categoría [C]**: `NombreCategoria` es la **división de la que depende** (la Copa Juvenil lleva «PRIMERA JUVENIL
   SALA»; el Trofeo Comunidad Senior Masculino, «SEGUNDA DIVISIÓN B»). La edad sale de ahí casi siempre, pero hay casos
   sin edad («Liga Universitaria Futbol Sala», «FASE ZONAL SALA» del deporte escolar, «SEGUNDA DIVISIÓN B DE FÚTBOL
   SALA», «TERCERA DIVISIÓN DE FÚTBOL SALA»): esos necesitan una tabla de correspondencias por `CodigoCategoria` o
   quedan `unknown`.
6. **Género [C]**: en T22 todas las femeninas llevan «FEMENINO» en `NombreCategoria`, **salvo** las universitarias,
   donde la marca está solo en el nombre («CAMPEONATO UNIVERSITARIO FEMENINO/MASCULINO»). La regla `implicit_male` es
   viable solo si se exige ausencia de marca femenina en `NombreCategoria` **y** en el nombre.
7. **Códigos de competición y grupo cambian cada temporada [C]** (0 de 35 nombres comunes T21/T22 con el mismo código).
   **`CodigoCategoria` es estable** (34/35 entre T21 y T22; 40/42 de 2014‑15 siguen existiendo en T22): es la clave
   para relacionar «la misma competición» entre temporadas.
8. **`CodEquipo` es estable entre temporadas [C]**: los 8 equipos de la muestra que repiten conservan el código; uno
   cambió de nombre por patrocinio con el mismo código (`19359818`), y Parque Norte 'A' (`17145330`) conserva el suyo
   **tras ascender** de 1ª Autonómica a Tercera. Cero casos de mismo nombre con distinto código en la muestra.
9. **El número del escudo NO identifica al club [C — refuta la hipótesis H de data-model.md]**: Lope de Vega pasó de
   `00100_0012168000` a `00100_0014267064` y Parque Norte de `0000002531` a `0014265582` dentro de la misma temporada.
   Es el ID del fichero de imagen.
10. **Club oficial [C]**: `fichaequipo` da `codigo_club` y `nombre_club` («PARQUE NORTE F.S. (FS)»: el club se registra
    por modalidad); 'A' y 'B' comparten `16660312`; `fichaclub` lista los equipos actuales del club. El sufijo `'A'/'B'`
    del nombre no es fiable (el juvenil se llama «PARQUE NORTE F.S. » sin sufijo).
11. **Calendario completo en una petición [C]**: `/competicion/calendario` trae todas las jornadas con fecha, hora,
    campo, marcador y códigos de equipo, pero **sin** `estado`, `acta_cerrada` ni `motivo_estado`. Detecta cambios de
    fecha (un partido de la jornada 7 aparece movido al 16‑12‑2026). Las jornadas en curso siguen necesitando
    `/api/results` (que sí trae estado y acta).
12. **Histórico [C]**: hay catálogo y partidos con resultado al menos desde 2014‑15 (T10); no se ha comprobado si hay
    actas, clasificaciones y goleadores históricos.
13. **Delegaciones [C]**: los grupos muestreados son todos de `CENTRAL R.F.F.M.`; el club tiene su propia delegación
    («DELEGACIÓN COTORRUELO»). La URL admite `delegacion=`; no se ha visto un grupo de otra delegación.
14. **Datos personales [C]**: `fichaequipo`, `fichaclub` y `fichajugador` exponen datos de contacto, presidente,
    plantillas y técnicos. **Nunca se ingieren**: de las fichas solo se guardan códigos, nombres de club/equipo,
    categoría y escudo.

## Consecuencias para el diseño (aplicadas en data-model.md e ingestion.md)

- Identidad de equipo = `(source, CodEquipo)` **global**, no por grupo; el linaje entre temporadas usa ese código.
- Club = `codigo_club` de `fichaequipo` (una petición por equipo nuevo; ~unos cientos por temporada [E]); sin ficha, el
  equipo queda sin club, nunca agrupado por nombre ni por escudo.
- Competición entre temporadas = `(source, CodigoCategoria, nombre normalizado)` como relación **propuesta** con
  confianza, no como identidad.
- Descubrimiento por `/api/competitions` y `/api/groups` (JSON); barrido por `calendario`; jornadas activas por
  `/api/results`. Estimación de carga: ver ingestion.md §Presupuesto (revisado).

## Limitaciones del sondeo

Muestra pequeña (2 grupos para la estabilidad de códigos, 2 fichas de equipo); no se ha medido el número total de
grupos de T22 (exigiría 50 peticiones `groups`), ni la tolerancia de ritmo de la RFFM, ni clasificaciones/goleadores
por API JSON, ni actas históricas, ni grupos de otras delegaciones.
