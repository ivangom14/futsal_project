# Fase 21 — Actualización y consistencia de datos

Objetivo: actualizar partidos, actas, clasificaciones y goleadores **sin borrar y reimportar**, con integridad, trazabilidad y recuperación.
La actualización es una operación **administrativa de la CLI**: ni la API ni el MCP descargan ni escriben (solo consultan la frescura).
Sin LLM, sin cambios en el RAG ni en el modelo de agente. Migración **0006** aditiva (ver «Cambios de esquema»).

## Diagnóstico previo (lo que ya existía y lo que faltaba)
| Tema | Antes de la fase |
|---|---|
| Ejecución | Pipeline manual de varios comandos (`scrape-catalog`, `import-catalog`, `process-match-reports`, `ingest-standings|scorers`). |
| Incremental | Solo en la **descarga** (`scrape-catalog --update`, `update_policy.py`). La importación reimportaba todo (idempotente, pero sin filtro). |
| Idempotencia / claves | Resuelta: upsert por `(source, external_id)`, unicidades por grupo, FK compuestas `(round_id, group_id)` / `(team_id, group_id)`, actas y capturas por `content_hash`. **No se reimplementa.** |
| Borrados | Nunca se borra nada: una fuente vacía no destruye histórico. Lo que no existía era **detectar** lo que desaparece. |
| Fallo a mitad | Cada importación de grupo es una transacción; pero un `IngestionRun` quedaba `running` para siempre si el proceso moría. |
| Frescura | Solo `/tables/coverage` (tablas). Nada para partidos ni actas. |

Riesgos encontrados y tratados (aprobados antes de implementar):
- **R1** un partido (`(source, external_id)` global) podía **cambiar de grupo** en silencio porque el upsert actualizaba `group_id`.
- **R2** un listado que devolvía `scheduled` sin marcador para un partido `finished` **sobrescribía** estado y marcador.
- **R3** las consultas servían la captura más reciente aunque fuese `incomplete`/`empty`, sustituyendo una `ok`.
- **R4** estados de ejecución pobres (`running|success|failed`) y ejecuciones abandonadas sin recuperación.
- **R5/R6** sin detección de lo que la fuente deja de devolver y sin frescura por tipo de dato.
- **R7 (nuevo, mismo principio)** un acta degradada (sin jugadores, o cerrada → abierta) pasaba a ser la versión vigente. Tratado en `refresh` con una barrera previa (no modifica `import-match-report`).

## Qué actualiza y de qué fuente
| Tipo | Fuente | Mecanismo | Atomicidad |
|---|---|---|---|
| `matches` (partidos, jornadas, equipos) | listado de jornadas RFFM (`league.json` por grupo) | `scrape_group` (política `--update`) → barrera → `run_import` | una transacción por grupo |
| `reports` (actas, eventos, oficiales) | `acta-partido/<codacta>` | `process_reports` con barrera de calidad; incremental = partidos finalizados sin acta; `full` = además revisa las ya incorporadas | una transacción por acta |
| `standings` | `clasificaciones` oficial | `ingest_tables` (captura con hash) | una transacción por captura |
| `scorers` | `goleadores` oficial | `ingest_tables` | una transacción por captura |

Clasificaciones y goleadores **siguen siendo las tablas oficiales**: nunca se reconstruyen desde partidos o actas.

## Reglas de protección (6.4 y aprobaciones R1–R3)
**Identidad (R1).** Un partido que ya existe en otro `group_id` no se mueve ni se modifica: incidencia `identity_conflict` con `details`
(`stored_group_external_id`, `incoming_group_external_id`, competiciones, `match_external_id`). La ejecución del grupo queda `partial`.

**Regresión de un finalizado (R2).** Se considera sospechosa si el partido almacenado es `finished` y la fuente lo devuelve `scheduled`/`unknown`, o
`finished` sin marcador. Entonces: se conserva lo almacenado, no se crea observación, incidencia `finished_regression_held` con lo almacenado, lo
devuelto, su hash y si hay acta cerrada. **No** son regresiones (se aplican, con `match_observations` como traza): la corrección de un marcador
(`finished` → `finished` con otro marcador), un aplazamiento/suspensión/cancelación explícitos (`motivo_estado`) y los cambios de fecha, hora, campo o jornada.
*Evidencia real de la fuente:* el listado solo publica estado y marcador, sin fecha de publicación ni indicador de corrección; el único dato
verificable es la **persistencia**. Regla documentada: la regresión se **confirma** (se aplica, incidencia `finished_regression_confirmed`) solo si
(a) el **mismo contenido** (hash) ya fue devuelto y retenido en una ejecución anterior, (b) con al menos `--regression-confirm-hours` (24) de diferencia
y (c) **no existe acta cerrada** que acredite que se jugó. Si la fuente vuelve a publicar el partido finalizado, se cierra la revisión
(`finished_regression_resolved`). Los pendientes se ven en la frescura (`held_for_review`). El acta cerrada sigue prevaleciendo sobre el listado (Fase 5).

**Capturas oficiales (R3).** Se sirve la última captura `ok`. Una `incomplete` o `empty` posterior se conserva pero no la sustituye; solo si **nunca** hubo `ok` se sirve la
más reciente (`empty` = ausencia oficial; `incomplete` = lo mejor disponible, marcada). La respuesta incluye `capture` (`served_status`, `last_valid_at`,
`latest_status`, `latest_captured_at`) y un aviso si el último intento fue parcial o vacío. Aplica a clasificación (por jornada), goleadores, cobertura y frescura.

**Barrera de descarga (`refresh/gates.py`).** Antes de importar un grupo: identidad del fichero = alcance pedido (si no, `rejected`); sin jornadas = `no_data` (aún no
publicado, no es error); descarga fallida = `failed`; sin partidos con histórico = `rejected` (se conserva); menos jornadas que las almacenadas, descarga incompleta o alguna jornada con menos partidos de los que declara = `partial` (se importa lo recibido —nunca se borra—, pero **no** avanza la última actualización válida); menos partidos que
los almacenados con una descarga **completa y coherente** = `success`/`no_changes` con una nota (son históricos, se ven como `historical` en la reconciliación). *Fase 21.1:* antes esto último también era `partial` y dejaba el grupo así indefinidamente.
**Actas:** una versión sin jugadores (con jugadores almacenados) o cerrada → abierta se rechaza; un acta con HTTP 404/410 es «aún no publicada» (ausencia temporal, no fallo);
403/429/5xx, red, tiempo de espera o página ilegible son fallo. Se corta tras 3 fallos de descarga seguidos (no los 404/410).

**Reintentos.** Sin cambios: `PoliteFetcher` solo reintenta transitorios (429, 5xx, red) con espera acotada; 4xx no se reintenta; las tablas `null` tienen
`--null-retries`. Un error de validación, identidad o persistencia nunca se reintenta: se registra.

## Ejecuciones y recuperación
`ingestion_runs` (ya existente) gana `kind` (`import` histórico, `matches`, `report`, `refresh`) y `stats` JSONB, y los estados `partial`, `no_changes`,
`cancelled`, `abandoned`. Una ejecución `refresh` guarda en `stats` el alcance, el resultado por grupo y tipo, contadores, errores (con `error_kind`:
`network|parse|validation|persistence|unexpected|source_or_validation`), `child_run_id` de la importación y `http_requests`.

Estado global: `failed` si todo lo relevante falló/se rechazó; `partial` si algo falló, quedó parcial o se rechazó; `no_changes` si todo está al día sin cambios;
`success` si hubo cambios sin incidencias; `cancelled` si se interrumpe (Ctrl+C). Un error inesperado nunca cierra la ejecución como correcta.

**Abandonadas.** `python -m futsal.cli refresh-recover` marca `abandoned` las ejecuciones `running` iniciadas hace más de N horas (por defecto 6;
`--max-age-hours` o variable `FUTSAL_RUN_ABANDON_HOURS`). Solo toca `running`, se puede repetir (idempotente), tiene `--dry-run` y deja la traza en
`error_summary` y `stats.recovered`. La migración **no** cierra ninguna `running`. Una ejecución real que dure más del umbral también se marcaría: por eso es configurable.
Una ejecución ya recuperada no se sobrescribe si termina tarde.

## Frescura por tipo de dato
Tabla `dataset_refreshes` (partidos y actas): por grupo, último intento (fecha, estado, mensaje, ejecución) y **última actualización válida** (`success`/`no_changes`)
con `source_fetched_at` (momento de la descarga). Clasificación y goleadores se derivan de sus capturas e intentos existentes. Para datos anteriores a la fase
(sin registro) la frescura se deduce de los registros (`basis: records`). No se mezclan fecha de ejecución, de consulta a la fuente, última válida y modificación
del registro. **La RFFM no publica fecha de publicación: no se inventa** (el campo `note` lo dice). Umbrales por defecto (`StalePolicy`): partidos 72 h, actas 168 h,
clasificación 96 h, goleadores 168 h; estados: `fresh`, `stale`, `never`, `partial`, `empty`, `not_applicable`.

- API: `GET /groups/{id}/freshness`, `GET /competitions/{id}/freshness` (solo lectura).
- MCP: `get_data_freshness(group_id | competition_id)` (solo lectura; el LLM no puede iniciar actualizaciones). El prompt del agente añade una frase: usarla si
  preguntan si los datos están al día o cuándo se actualizaron.

## Reconciliación (solo lectura)
`refresh-reconcile` compara el snapshot local de la fuente (`league.json`, actas normalizadas) con PostgreSQL, **sin red y con `SET TRANSACTION READ ONLY`** (lo impone la base
de datos). Clasifica cada hallazgo:
| Categoría | Ejemplos |
|---|---|
| `confirmed_difference` | partido en la fuente y no en PostgreSQL; fecha/hora/campo/jornada/equipo/estado/marcador distintos; acta con versión distinta; grupo no importado |
| `possible_inconsistency` | regresión de un finalizado; listado frente a acta cerrada; partido en otro grupo; tabla más antigua que los últimos resultados o por detrás de la jornada actual |
| `temporary_absence` | jornada no descargada; actas pendientes; última captura de tabla parcial/vacía; sin snapshot local |
| `ingest_error` | jornada con descarga fallida; partido inválido; fichero ilegible; identidad del fichero distinta |
| `historical` | partido que ya no figura en una jornada descargada completa: se conserva |
No corrige nada: la corrección es `refresh`. Con `--fail-on-differences` devuelve 1 si hay diferencias, inconsistencias o errores de ingesta.

## Comandos (CLI real)
```
python -m futsal.cli refresh --group ID [--competition ID] [--all-groups]
        [--only matches,reports,standings,scorers] [--mode incremental|full] [--dry-run] [--offline] [--plan]
        [--regression-confirm-hours 24] [--table-max-age-hours 12] [--report-limit N] [--output informe.json]
        [--request-delay 2] [--timeout 30] [--horizon-days --settle-days --max-age-days --min-age-hours]
python -m futsal.cli refresh-status [--limit 5] [--group ID | --competition ID] [--abandon-hours H]
python -m futsal.cli refresh-reconcile (--group ID | --competition ID | --all-groups) [--json fichero] [--fail-on-differences]
python -m futsal.cli refresh-recover [--max-age-hours H] [--dry-run]
```
Valores seguros: alcance obligatorio (sin él, error), identificadores desconocidos = error, `incremental` por defecto, `--dry-run` sin escrituras (no descarga actas),
`--plan` sin red ni base de datos. Códigos de salida: 0 (`success`/`no_changes`), 1 (`partial`/`failed`), 2 (uso o entorno). Los directorios por defecto son los de las fases
anteriores (`data/rffm/catalog`, `data/rffm/tables`, `data/rffm/match-reports`); requiere `discover-catalog` previo.
Automatización futura: es un comando con código de salida; basta programarlo (p. ej. `refresh --all-groups --mode incremental`) y `refresh-recover`.

## Cambios de esquema (migración 0006, aditiva y reversible)
- `ingestion_runs`: `kind` (NOT NULL, defecto `import`), `stats` JSONB (NULL), y el CHECK de estado pasa a un **superconjunto** (`+ partial, no_changes, cancelled, abandoned`).
- `data_quality_issues`: `details` JSONB (NULL).
- Tabla nueva `dataset_refreshes` (`group_id`+`dataset` únicos; `dataset` ∈ `matches|reports`).
No transforma ni borra filas; las ejecuciones históricas quedan como `import`; `downgrade` es posible (los estados nuevos se reasignan a `success`/`failed`).

## Qué se conserva ante fallos
Nada se borra por ausencia en la fuente. Una descarga vacía, parcial o fallida deja intactos partidos, actas y capturas válidas; solo registra el intento. Una transacción
fallida (inserción de partidos, actualización de resultados, eventos de un acta, sustitución de una tabla) se revierte entera. **Recuperar una actualización fallida:**
`refresh-status` (qué falló y por qué) → corregir la causa → repetir `refresh` con el mismo alcance (idempotente) → `refresh-recover` si quedó una `running` colgada.

## Pruebas
Fixtures y PostgreSQL desechable, sin red: `tests/test_refresh_gates.py` (sin PostgreSQL), `tests/integration/test_refresh_{guards,captures,service,runs,reconcile,cli}_pg.py`
(identidad, regresión y corrección, capturas oficiales, idempotencia, respuestas vacías/parciales/HTTP/timeout/HTML cambiado, aislamiento de grupos y temporadas,
alcance selectivo, dry-run, fallos de persistencia de partidos/actas/clasificación/goleadores, actas degradadas, recuperación, migración desde 0005, reconciliación
sin escrituras, API y MCP de frescura, CLI).

## Validación con la fuente real (base desechable, no la habitual)
Un grupo (temporada 22, competición 26738243, grupo 26738245) en una base PostgreSQL desechable, con el cliente cortés existente (31 peticiones, 1 min 45 s):
1.ª ejecución `partial`: la web devolvió para la jornada 25 una página con otra identidad (la comprobación existente la rechazó), así que se importaron 175 partidos,
`matches` quedó `partial` **sin avanzar la última actualización válida**; actas (3), clasificación y goleadores `success`. 2.ª ejecución incremental: `success`,
4 peticiones, recupera la jornada 25 (+7 partidos, 182 en total, 0 duplicados). 3.ª: `no_changes`, 0 peticiones. `refresh-reconcile`: 0 diferencias confirmadas, 27 actas
pendientes. `refresh-status` mostró `fresh` para los cuatro tipos. **No** se ha ejecutado nada contra la base habitual del usuario ni masivamente (un solo grupo).
`--mode full`, los 25 grupos y la regresión de un partido finalizado solo se han probado con fixtures.

## Limitaciones
- La frecuencia de la fuente no se conoce: los umbrales de `stale` son configurables pero orientativos.
- Una regresión legítima de la fuente (p. ej. resultado anulado) tarda al menos `--regression-confirm-hours` en aplicarse y requiere dos ejecuciones separadas.
- Un acta no publicada que la web sirve con HTTP 200 pero sin datos se ve como página ilegible (fallo), no como «aún no publicada».
- `full` repite todas las peticiones del alcance (puede ser mucho): usar `--plan` antes. `--dry-run` descarga partidos y tablas pero no actas.
- La reconciliación compara con snapshots locales: para contrastar con la web hay que descargar antes (`refresh --dry-run` o `scrape-catalog --update`).
- La frescura de clasificación/goleadores se basa en la captura; no hay fecha de publicación oficial. Un partido desaparecido de la fuente se marca `historical`, nunca se borra.
- La frase del prompt y la nueva herramienta añaden unos tokens por llamada; no se ha medido con un LLM real (no se ejecutó evaluación live).
- `get_data_freshness` no está en las listas de herramientas permitidas de los casos de evaluación históricos (no se modificaron); si un modelo la usara en uno, figuraría como no esperada.
