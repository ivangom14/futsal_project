# Fase 14 — Informe de ingesta por competición y grupo

Medición del 2026-10-17: descubrimiento y descarga contra la RFFM real; importación y verificación en un PostgreSQL **temporal** (no la BD del usuario).
Tres pasadas de `scrape-catalog --resume` (la 2ª y 3ª con `--request-delay 5`). Los fallos de jornada son páginas con `results` vacío devueltas por la web de forma intermitente (cambian de una pasada a otra); se reintentan con `--resume`.

| Competición (ID) | Grupo (ID) | Descubrim. | Descarga | Jornadas desc./esp. | Jornadas fallidas | Equipos desc./esp. | Partidos desc. | Persistido (BD) | Pendiente de verificar |
|---|---|---|---|---|---|---|---|---|---|
| TERCERA DIVISIÓN FS (26738221) | Grupo 3 (26771028) | confirmed | partial | 30/30 | 3 | 16/16 | 216 | sí: 30 jornadas, 16 equipos, 216 partidos {'finished': 22, 'scheduled': 194} | jornadas 3, 6, 14 |
| TERCERA DIVISIÓN FS (26738221) | Grupo 4 (26771029) | confirmed | partial | 30/30 | 5 | 16/16 | 200 | sí: 30 jornadas, 16 equipos, 200 partidos {'finished': 32, 'scheduled': 168} | jornadas 6, 9, 12, 15, 18 |
| COPA SENIOR I - fase final- FS (28403858) | Eliminatorias (28410667) | confirmed | downloaded | 4/4 | 0 | 16/60 | 8 | sí: 4 jornadas, 16 equipos, 8 partidos {'scheduled': 8} |  |
| COPA SENIOR I - fase previa- FS (28403847) | Eliminatorias (28410203) | confirmed | downloaded | 1/1 | 0 | 24/24 | 12 | sí: 1 jornadas, 24 equipos, 12 partidos {'finished': 7, 'scheduled': 5} |  |
| PRIMERA DIVISION AUTONOMICA AFICIONADO FS (26738243) | Grupo 1 (26738244) | confirmed | downloaded | 26/26 | 0 | 14/14 | 182 | sí: 26 jornadas, 14 equipos, 182 partidos {'finished': 19, 'scheduled': 163} |  |
| PRIMERA DIVISION AUTONOMICA AFICIONADO FS (26738243) | Grupo 2 (26738245) | confirmed | downloaded | 26/26 | 0 | 14/14 | 182 | sí: 26 jornadas, 14 equipos, 182 partidos {'finished': 19, 'scheduled': 163} |  |
| PREFERENTE AFICIONADO FS (26738246) | Grupo 1 (26738247) | confirmed | downloaded | 26/26 | 0 | 14/14 | 182 | sí: 26 jornadas, 14 equipos, 182 partidos {'finished': 14, 'scheduled': 168} |  |
| PREFERENTE AFICIONADO FS (26738246) | Grupo 2 (26738248) | confirmed | partial | 26/26 | 7 | 14/14 | 133 | sí: 26 jornadas, 14 equipos, 133 partidos {'finished': 14, 'scheduled': 119} | jornadas 10, 11, 13, 14, 19, 20, 25 |
| COPA SENIOR II - fase final- FS (28403930) | Eliminatorias (28411968) | confirmed | partial | 5/5 | 1 | 32/78 | 16 | sí: 5 jornadas, 32 equipos, 16 partidos {'scheduled': 16} | jornadas 5 |
| COPA SENIOR II - fase previa- FS (28403871) | Eliminatorias (28411587) | confirmed | downloaded | 1/1 | 0 | 22/22 | 11 | sí: 1 jornadas, 22 equipos, 11 partidos {'finished': 6, 'scheduled': 3, 'unknown': 2} |  |
| PRIMERA AFICIONADO FS (27776951) | Grupo 1 (27776954) | confirmed | partial | 26/26 | 1 | 14/13 | 175 | sí: 26 jornadas, 14 equipos, 175 partidos {'finished': 6, 'scheduled': 169} | jornadas 19 |
| PRIMERA AFICIONADO FS (27776951) | Grupo 2 (28153964) | confirmed | downloaded | 22/22 | 0 | 12/12 | 132 | sí: 22 jornadas, 12 equipos, 132 partidos {'finished': 4, 'scheduled': 128} |  |
| PRIMERA AFICIONADO FS (27776951) | Grupo 3 (28153967) | confirmed | downloaded | 22/22 | 0 | 12/12 | 132 | sí: 22 jornadas, 12 equipos, 132 partidos {'finished': 5, 'scheduled': 127} |  |
| PRIMERA AFICIONADO FS (27776951) | Grupo 4 (28153975) | confirmed | partial | 26/26 | 1 | 14/13 | 175 | sí: 26 jornadas, 14 equipos, 175 partidos {'finished': 6, 'scheduled': 169} | jornadas 6 |
| TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS (26843411) | Eliminatorias (26843429) | confirmed | downloaded | 3/3 | 0 | 8/8 | 7 | sí: 3 jornadas, 8 equipos, 7 partidos {'finished': 7} |  |
| PRIMERA DIVISION AUTONOMICA JUVENIL FS (26749998) | Grupo Unico (26814437) | confirmed | partial | 30/30 | 1 | 16/16 | 232 | sí: 30 jornadas, 16 equipos, 232 partidos {'finished': 31, 'scheduled': 201} | jornadas 6 |
| PREFERENTE JUVENIL FS (26738220) | Grupo 1 (26878003) | confirmed | partial | 26/26 | 1 | 14/14 | 175 | sí: 26 jornadas, 14 equipos, 175 partidos {'finished': 14, 'scheduled': 161} | jornadas 4 |
| PREFERENTE JUVENIL FS (26738220) | Grupo 2 (26878012) | confirmed | partial | 26/26 | 10 | 14/14 | 112 | sí: 26 jornadas, 14 equipos, 112 partidos {'finished': 14, 'scheduled': 98} | jornadas 9, 10, 11, 12, 13, 14, 15, 16, 17, 18 |
| COPA JUVENIL - fase final- FS (28404058) | Eliminatorias (28446703) | confirmed | no_rounds | -/6 | 0 | -/112 | - | no | sin jornadas publicadas por la web |
| COPA JUVENIL - fase previa- FS (28404003) | Eliminatorias (28413770) | confirmed | downloaded | 1/1 | 0 | 14/14 | 7 | sí: 1 jornadas, 14 equipos, 7 partidos {'finished': 6, 'scheduled': 1} |  |
| PRIMERA JUVENIL FS (26738228) | Grupo 1 (28011290) | confirmed | partial | 26/26 | 16 | 14/13 | 70 | sí: 26 jornadas, 14 equipos, 70 partidos {'finished': 1, 'scheduled': 69} | jornadas 1, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 23, 24 |
| PRIMERA JUVENIL FS (26738228) | Grupo 2 (28122269) | confirmed | partial | 26/26 | 1 | 14/13 | 175 | sí: 26 jornadas, 14 equipos, 175 partidos {'finished': 6, 'scheduled': 169} | jornadas 15 |
| PRIMERA JUVENIL FS (26738228) | Grupo 3 (28122301) | confirmed | partial | 26/26 | 14 | 14/14 | 84 | sí: 26 jornadas, 14 equipos, 84 partidos {'finished': 7, 'scheduled': 77} | jornadas 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25 |
| PRIMERA JUVENIL FS (26738228) | Grupo 4 (28123098) | confirmed | downloaded | 26/26 | 0 | 14/14 | 182 | sí: 26 jornadas, 14 equipos, 182 partidos {'finished': 7, 'scheduled': 175} |  |
| PRIMERA JUVENIL FS (26738228) | Grupo 5 (28123215) | confirmed | downloaded | 26/26 | 0 | 14/14 | 182 | sí: 26 jornadas, 14 equipos, 182 partidos {'finished': 7, 'scheduled': 175} |  |

Grupos: {'partial': 12, 'downloaded': 12, 'no_rounds': 1}. Partidos descargados: 2982. Jornadas fallidas: 61.

## Estado por competición
| Competición | Estado |
|---|---|
| TERCERA DIVISIÓN FS | parcial (0/2 grupos completos) |
| COPA SENIOR I - fase final- FS | completa (1/1 grupos completos) |
| COPA SENIOR I - fase previa- FS | completa (1/1 grupos completos) |
| PRIMERA DIVISION AUTONOMICA AFICIONADO FS | completa (2/2 grupos completos) |
| PREFERENTE AFICIONADO FS | parcial (1/2 grupos completos) |
| COPA SENIOR II - fase final- FS | parcial (0/1 grupos completos) |
| COPA SENIOR II - fase previa- FS | completa (1/1 grupos completos) |
| PRIMERA AFICIONADO FS | parcial (2/4 grupos completos) |
| TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS | completa (1/1 grupos completos) |
| PRIMERA DIVISION AUTONOMICA JUVENIL FS | parcial (0/1 grupos completos) |
| PREFERENTE JUVENIL FS | parcial (0/2 grupos completos) |
| COPA JUVENIL - fase final- FS | no descargada (0/1 grupos completos) |
| COPA JUVENIL - fase previa- FS | completa (1/1 grupos completos) |
| PRIMERA JUVENIL FS | parcial (2/5 grupos completos) |

## Verificación en PostgreSQL (solo lectura)
Duplicados de partidos/equipos, partidos huérfanos, grupos huérfanos y competiciones huérfanas: **0**. Competición inicial 26738243 conservada (grupo 26738245: 182 partidos, id de BD sin cambios).
Cada grupo importado tiene su `ingestion_runs` y sus `data_quality_issues` (`round_not_ok` para las jornadas fallidas).
El total esperado de **partidos** no lo publica la RFFM; solo se compararon jornadas y equipos. 'Partidos persistidos' coincide con 'descargados' en todos los grupos importados.

## Pendiente
- 12 grupos parciales y 1 sin jornadas (Copa Juvenil fase final, `28446703`): repetir `scrape-catalog --resume --request-delay 5` hasta que no queden jornadas fallidas.
- Ninguna competición con más de un grupo puede darse por completa mientras algún grupo esté parcial.
- Ejecutar `import-catalog` y `verify-catalog` en la BD real del usuario (aquí solo se validó en una temporal).
