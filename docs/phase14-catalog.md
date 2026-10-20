# Fase 14 — Ingesta de competiciones senior y juvenil de fútbol sala

Temporada 22 (2026-2027), tipo de juego 3 (Fútbol Sala). Inventario verificado contra la web de la RFFM el 2026-10-17
(15 peticiones HTTP). Los IDs son los devueltos por la RFFM; no se deducen ni se fijan en el código.

## Método de descubrimiento
1. **Competiciones**: `pageProps.competitions` de la página de resultados de un grupo futsal conocido (el grupo de
   referencia de `config/targets.py`). Sin competición/grupo futsal la web devuelve el catálogo de Fútbol-11.
2. **Grupos**: `GET https://www.rffm.es/api/groups?competicion=<id>` (el JSON que consume la propia web; mismo host).
   Cada grupo trae `total_jornadas` y `total_equipos`, usados como totales esperados.
3. **Jornada semilla**: la página del grupo **sin** `jornada` devuelve la jornada actual; de ahí se descubre la lista
   real de jornadas (`pageProps.rounds.jornadas`) sin suponer IDs.
4. **Identificación**: igualdad exacta del nombre normalizado (sin acentos, mayúsculas, espacios ni guiones) y tipo de
   juego 3. Una coincidencia = `confirmed`; varias = `ambiguous`; ninguna = `not_found`. Nunca por prefijo ni parecido.

## Inventario (resultado del descubrimiento)
| # | Nivel | Denominación pedida | Nombre RFFM | competición | Estado | Grupos (id · nombre · jornadas · equipos) |
|---|---|---|---|---|---|---|
| 1 | senior | Tercera División FS | TERCERA DIVISIÓN FS | 26738221 | confirmed | 26771028 · Grupo 3 · 30 · 16<br>26771029 · Grupo 4 · 30 · 16 |
| 2 | senior | Copa Senior I - fase final - FS | COPA SENIOR I - fase final- FS | 28403858 | confirmed | 28410667 · Eliminatorias · 4 · 60 |
| 3 | senior | Copa Senior I - fase previa - FS | COPA SENIOR I - fase previa- FS | 28403847 | confirmed | 28410203 · Eliminatorias · 1 · 24 |
| 4 | senior | Primera División Autonómica Aficionado FS | PRIMERA DIVISION AUTONOMICA AFICIONADO FS | 26738243 | confirmed | 26738244 · Grupo 1 · 26 · 14<br>26738245 · Grupo 2 · 26 · 14 |
| 5 | senior | Preferente Aficionado FS | PREFERENTE AFICIONADO FS | 26738246 | confirmed | 26738247 · Grupo 1 · 26 · 14<br>26738248 · Grupo 2 · 26 · 14 |
| 6 | senior | Copa Senior II - fase final - FS | COPA SENIOR II - fase final- FS | 28403930 | confirmed | 28411968 · Eliminatorias · 5 · 78 |
| 7 | senior | Copa Senior II - fase previa - FS | COPA SENIOR II - fase previa- FS | 28403871 | confirmed | 28411587 · Eliminatorias · 1 · 22 |
| 8 | senior | Primera Aficionado FS | PRIMERA AFICIONADO FS | 27776951 | confirmed | 27776954 · Grupo 1 · 26 · 13<br>28153964 · Grupo 2 · 22 · 12<br>28153967 · Grupo 3 · 22 · 12<br>28153975 · Grupo 4 · 26 · 13 |
| 9 | juvenil | Trofeo Comunidad División Honor Juvenil FS | TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS | 26843411 | confirmed | 26843429 · Eliminatorias · 3 · 8 |
| 10 | juvenil | Primera División Autonómica Juvenil FS | PRIMERA DIVISION AUTONOMICA JUVENIL FS | 26749998 | confirmed | 26814437 · Grupo Unico · 30 · 16 |
| 11 | juvenil | Preferente Juvenil FS | PREFERENTE JUVENIL FS | 26738220 | confirmed | 26878003 · Grupo 1 · 26 · 14<br>26878012 · Grupo 2 · 26 · 14 |
| 12 | juvenil | Copa Juvenil - fase final - FS | COPA JUVENIL - fase final- FS | 28404058 | confirmed | 28446703 · Eliminatorias · 6 · 112 |
| 13 | juvenil | Copa Juvenil - fase previa - FS | COPA JUVENIL - fase previa- FS | 28404003 | confirmed | 28413770 · Eliminatorias · 1 · 14 |
| 14 | juvenil | Primera Juvenil FS | PRIMERA JUVENIL FS | 26738228 | confirmed | 28011290 · Grupo 1 · 26 · 13<br>28122269 · Grupo 2 · 26 · 13<br>28122301 · Grupo 3 · 26 · 14<br>28123098 · Grupo 4 · 26 · 14<br>28123215 · Grupo 5 · 26 · 14 |

Totales: 14/14 confirmadas, 0 ambiguas, 0 no localizadas, **25 grupos**, 0 competiciones sin grupos, 0 fallos de descubrimiento.
Las copas y el Trofeo Comunidad usan un único grupo «Eliminatorias».
El catálogo actual tiene 50 competiciones futsal; las 36 restantes quedan fuera del alcance.

## Diseño (sin migraciones)
- Mismo esquema: `seasons → competitions → competition_groups → rounds/teams → matches`. Equipos únicos por
  (grupo, id externo): clubes con nombre igual en grupos distintos son filas distintas. Partidos únicos por `codacta`.
- `catalog.py` (descubrimiento, sin red propia), `catalog_ingest.py` (reutiliza `scrape_league` por grupo, aislado),
  `importer/catalog_service.py` (reutiliza `run_import` por grupo + verificación de solo lectura), `catalog_commands.py` (CLI).
- Trabajo en `data/rffm/catalog/s22_t3/` (ignorado por Git): `inventory.json`, `catalog-scrape-report.json`,
  `c<competición>/g<grupo>/{seed.html,rounds/,league.json}`, `catalog-import-report.json`, `catalog-verify-report.json`.
- Estados de grupo: `downloaded`, `partial` (jornadas fallidas), `no_rounds` (la web no publica jornadas), `failed`.
  Una competición solo se considera completa si todos sus grupos confirmados están `downloaded`.
- Trazabilidad en PostgreSQL: una `ingestion_runs` por grupo importado, con sus `data_quality_issues`
  (`round_not_ok` si hubo jornadas fallidas).

## Comandos (Windows CMD, desde la raíz del repositorio con el entorno activado)
```
python -m futsal.cli discover-catalog --refresh
python -m futsal.cli scrape-catalog --plan
python -m futsal.cli scrape-catalog --competition 28403847
python -m futsal.cli scrape-catalog --resume
python -m futsal.cli import-catalog --dry-run
python -m futsal.cli import-catalog
python -m futsal.cli verify-catalog
```
- Una sola competición: `--competition <ID real>` (repetible) en `scrape-catalog`, `import-catalog`.
- Todo el alcance: sin `--competition`. `--plan` solo estima peticiones (aproximación: jornadas esperadas menos snapshots).
- Reanudar: repetir `scrape-catalog --resume` (los snapshots por jornada se reutilizan; solo se piden las que faltan).
  `--refresh` fuerza la redescarga (necesario para ver resultados nuevos). `import-catalog` es idempotente.
- `import-catalog` necesita PostgreSQL (`docker compose up -d --wait db` y `alembic upgrade head`) y `DATABASE_URL`.
- Códigos de salida: 0 todo correcto; 1 algún grupo no `downloaded`/importado; 2 error de entorno.

## Consultas por grupo (API y MCP)
- `GET /competitions` devuelve cada competición con sus `groups` (`id`, `name`); `GET /groups/{id}/...` fija el grupo.
- `GET /teams?name=` busca equipos en todos los grupos (`group_id`, `group`, `competition_id`, `competition`);
  MCP `find_teams`. Los `id` son internos de la BD, distintos de los IDs externos de la RFFM.
- Con más de 3 grupos el agente recibe en su contexto solo el catálogo competición→grupos (determinista, sin LLM) y debe
  pedir aclaración si la pregunta no fija competición y grupo. Con ≤3 grupos el contexto es el de siempre.

## Límites y problemas conocidos
- El número esperado de **partidos** no lo publica la web; solo jornadas y equipos. No se inventa: se informa.
- Las actas (`process-match-reports`) siguen limitadas al grupo inicial: fuera de alcance de esta fase.
- `find_teams` no ignora acentos (usa `ILIKE`).
- La evaluación online del agente y el preflight usan el grupo de menor `id` (el inicial). Con el contexto de catálogo
  cambia el comportamiento del agente: hay que recalibrar la línea base online (`--baseline`) antes de comparar.
- Resultado de la descarga real (3 pasadas): 12 grupos completos, 12 parciales (61 jornadas fallidas) y 1 sin jornadas; 2.982 partidos. La web devuelve a veces `results` vacío para una jornada (intermitente, cambia entre pasadas): repetir `scrape-catalog --resume --request-delay 5`. Informe por grupo: `docs/phase14-report.md`.
- Un fallo temporal de la web se registra por grupo/jornada; no se cachea y se reintenta en la siguiente ejecución.

## Contador de progreso y qué se vuelve a descargar
`scrape-catalog` muestra en pantalla (stderr, una línea que se reescribe) el grupo actual, la jornada, las peticiones hechas frente a las
previstas, el tiempo transcurrido y el restante, p. ej.:
`[grupo 7/25] 26738246/26738247 PREFERENTE... | jornada 12/26 | peticiones 143/~437 (32 %) | transcurrido 06:21 | restante ~13:20`.
El total es una estimación (jornadas esperadas menos snapshots locales); los reintentos y las jornadas vacías añaden peticiones, y el restante se calcula con
el ritmo real medido, así que es orientativo. Si el terminal está redirigido a un fichero solo escribe una línea por grupo.

Qué se descarga en cada ejecución: sin `--refresh`, **las jornadas que ya tienen snapshot no se piden otra vez** (se vuelven a leer del disco);
solo se piden las que faltan o fallaron. `--resume` además se salta su reanálisis. `--refresh` vuelve a pedir TODAS las jornadas
(necesario hoy para ver resultados nuevos).


## Actualización incremental: `scrape-catalog --update`
Vuelve a pedir **solo** las jornadas ya descargadas que pueden haber cambiado y omite las cerradas. Se decide con los ficheros locales (sin red), por jornada:

| Motivo | Cuándo se vuelve a pedir |
|---|---|
| `anomalia:<código>` | la primera pasada dejó un marcador a medias o no numérico, un acta cerrada sin marcador, un partido de fecha pasada sin marcador, estado desconocido, equipo vacío… (`check_quality`) |
| `aplazado_o_suspendido` | algún partido está aplazado o suspendido, sea cual sea su fecha |
| `pendiente_proximo` | tiene partidos por jugar y el primero cae en los próximos `--horizon-days` (14) |
| `cerrada_reciente` | todo cerrado, pero el snapshot se tomó menos de `--settle-days` (3) después del último partido (correcciones de resultado) |
| `lejano_caducado` / `sin_fecha` | partidos lejanos o sin fecha: como mucho cada `--max-age-days` (30), para detectar cambios de calendario |
| (sin snapshot) | las jornadas que faltan o fallaron se piden siempre |

Una jornada cuyo snapshot tiene menos de `--min-age-hours` (12) no se repite, para no insistir a la web. La semilla de cada grupo se renueva (1 petición por grupo)
para detectar jornadas recién publicadas, salvo que sea muy reciente. Si la nueva petición **falla**, se conserva el snapshot anterior (no se pierde la
jornada) y se anota `refetch_failed_kept_snapshot`. `--update` y `--refresh` son incompatibles. La edad del snapshot es la fecha de modificación del fichero HTML.

```
python -m futsal.cli scrape-catalog --update --plan      # qué revisaría y por qué, sin tocar la web
python -m futsal.cli scrape-catalog --update             # lo ejecuta, con contador de progreso
python -m futsal.cli import-catalog                       # idempotente: actualiza resultados en PostgreSQL
python -m futsal.cli verify-catalog
```
Cada línea de grupo añade `revisadas=N omitidas=M` (y `conservadas_sin_actualizar=K` si alguna petición falló).

## Actas de varios grupos: `process-match-reports`
Por defecto sigue procesando **solo el grupo inicial** (`26738245`). Para las demás ligas:
```
python -m futsal.cli process-match-reports --all-groups --plan          # candidatos por grupo y peticiones previstas; no descarga
python -m futsal.cli process-match-reports --group 26738244 --limit 5   # prueba pequeña en un grupo (ID externo de la RFFM, repetible)
python -m futsal.cli process-match-reports --all-groups --request-delay 5
```
Candidatos = partidos `finished` con marcador y sin acta en PostgreSQL (hay que haber ejecutado `import-catalog`). Una petición por acta; las que ya tienen
snapshot en `data/rffm/match-reports/<id>/raw.html` no se piden. Muestra un contador (`[lote 1/1] actas | acta 12/235 | peticiones 12/~235 | transcurrido… | restante ~…`),
se detiene tras 3 fallos de descarga seguidos y al final indica candidatos y fallos por grupo. La identidad (partido, competición y grupo) se valida antes de guardar
cada acta. Es idempotente: repetir el comando solo procesa lo que sigue sin acta.
