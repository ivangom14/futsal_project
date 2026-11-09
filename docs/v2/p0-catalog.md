# v2 · P0: catálogo real de la temporada 2026‑27 (2026-11-09)

Autorización: P0, máximo 51 GET de solo lectura, sin partidos ni actas. **Realizadas: 51/51**: 1 a `/api/competitions?temporada=22&tipojuego=3`
y 50 a `/api/groups?competicion=C`. Todas devolvieron HTTP 200; mínimo de 5,0 s entre peticiones; 46 KB en total; latencia máxima 1,3 s;
20:33–20:37 UTC. No hubo 429, cuerpos `null`, redirecciones ni otras señales de limitación. El script se habría detenido ante cualquiera de ellas.
Registro: `data/raw/rffm/p0/requests.jsonl`; resumen por competición: `data/raw/rffm/p0/catalog_summary.json` (ambos fuera de Git;
solo metadatos públicos de competiciones y grupos).

## Catálogo

| Medida | Valor |
|---|---|
| Competiciones FS | **50** (34 activas, 16 inactivas), las mismas 50 que en el sondeo A1 de esta mañana |
| Grupos | **89** (71 de competiciones activas); ninguna competición sin grupos |
| Grupos por competición | 1 → 31 competiciones · 2 → 12 · 3 → 1 · 4 → 3 · 5 → 2 · 9 → 1 (Alevín 1ª fase) |
| Plazas de equipo declaradas (`total_equipos`) | 1.777 en total: 709 en ligas, 1.021 en copas (eliminatorias con muchos inscritos) y 47 en trofeos |
| Jornadas declaradas (suma por grupo) | 1.244 (1.179 en grupos activos) |
| Delegación | las 89 en `CENTRAL R.F.F.M.` |
| Clasificación oficial publicada | 89/89 grupos (`ver_clasificacion`); goleadores 84/89 |
| Frente a la Fase 14 | antes 14 competiciones y 25 grupos (senior y juvenil masculinos); ahora 50 y 89 (×3,6 grupos) |

Distribución según el clasificador de T03 (competiciones, grupos, plazas):

| Categoría | Masculino | Femenino |
|---|---|---|
| senior | 9 · 15 · 332 | 3 · 3 · 40 |
| juvenil | 6 · 11 · 246 | 2 · 3 · 21 |
| cadete | 5 · 9 · 248 | 2 · 3 · 46 |
| infantil | 5 · 9 · 258 | 3 · 5 · 93 |
| alevín | 3 · 11 · 270 | 2 · 3 · 40 |
| benjamín | 4 · 8 · 113 | 2 · 3 · 17 |
| universitario | 1 · 2 · 12 | 1 · 1 · 10 |
| sin edad (`unknown`) | — | 2 · 3 · 31 |

Tipo: 23 ligas (59 grupos), 22 copas (24 grupos), 5 trofeos (6 grupos). Clasificación: **48/50 `auto`, 2 `needs_review`**:
«PRIMERA DIVISIÓN AUTONOMICA FEMENINO FS» y «PREFERENTE FEMENINO 1ª Fase FS», sin edad en el nombre ni en `NombreCategoria`.
Son casi seguro senior femenino, pero **no se asume**: se confirman en `catalog-review` o con una tabla por `CodigoCategoria` (100005 y 110150).

**Cobertura**: el 100 % del catálogo publicado queda inventariado y clasificado o en revisión. El 0 % de sus partidos está ingerido en este
entorno: P0 no descarga partidos.

## Plan de carga recalculado con G medido (sustituye a la estimación con G = 120 de ingestion.md)

| Paso | Cálculo | Peticiones | Duración a 1 cada 3 s |
|---|---|---|---|
| P1 Temporada actual sin actas | 3·89 (calendario, clasificación, goleadores) + ~7 jornadas jugadas · 71 grupos activos (`/api/results`) | **≈ 765** | ≈ 40 min |
| P2 Actas, solo senior (A9) | ~7 partidos · ~7 jornadas · 18 grupos senior | **≈ 880** | ≈ 45 min |
| P3 Clubes (`fichaequipo`) | equipos distintos de las ligas ≤ ~800 [E] | **≈ 800** | ≈ 40 min |
| Fin de semana | ≈ 29 · 71 grupos activos | **≈ 2.060** | repartidas en 3 días |
| Entre semana | 1 calendario por grupo activo al día | **≈ 71/día** | |

Conclusiones para la fase siguiente:
1. El volumen cabe con holgura en 1 petición cada 3 s y sin concurrencia; no hace falta acelerar.
2. P1 es viable en una ejecución de < 1 h. Requiere antes T05 (descubrimiento con los metadatos oficiales) y T06 (cola con checkpoints) probados offline.
3. Las copas declaran muchos «equipos» en un único grupo «Eliminatorias»: su ingesta va por jornadas o rondas y no por clasificación.
4. Todo el catálogo es de la delegación central; el filtro `delegacion` no aporta nada en esta temporada.
5. Antes de P2 hay que decidir A9 para las actas de categorías inferiores, que ahora son la mayoría (71 de 89 grupos no son senior confirmado: juvenil e inferiores, universitarios y los 3 femeninos sin edad).
