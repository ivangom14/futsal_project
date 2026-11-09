# v2 · Modelo de datos

## Revisión A2 (2026-11-09) — prevalece sobre las secciones siguientes donde se contradigan

Base: sondeo A1 ([probe-a1.md](probe-a1.md)) y el esquema real en `alembic head` (0006), comprobado en un PG desechable.

**Esquema real que condiciona la migración [C]**: `teams` es una **participación** (fila por grupo, única por
`(group_id, external_id)`); `matches` referencia `(home_team_id, group_id)` con FK compuesta. Cambiar esa semántica
sería destructivo, así que la identidad entre temporadas va en una tabla nueva y `teams` solo gana una FK opcional.

**Cambios frente a la propuesta del 2026-11-08 y evidencia**

| Propuesta anterior | Decisión A2 | Evidencia |
|---|---|---|
| Club = número del escudo [H] | **Club = `codigo_club` oficial** de `/fichaequipo/<CodEquipo>`; sin ficha, `club_id` nulo | El número del escudo cambia dentro de la misma temporada (hallazgo 9) |
| Linaje = `(club, categoría, género, sufijo)` | **Linaje = `CodEquipo`** (`team_entities`), que sobrevive a ascensos y cambios de nombre | Hallazgo 8 |
| `squad` del sufijo `'A'/'B'` como clave | `squad` solo informativo (nulo si no hay sufijo) | El juvenil del club no lleva sufijo (hallazgo 10) |
| Misma competición entre temporadas: no previsto | `competition_links` **propuestos** por `CodigoCategoria` + nombre normalizado, con confianza y estado | Códigos de competición cambian; `CodigoCategoria` estable (hallazgo 7) |
| `kind` desde el nombre | `format_code` = `tipo_competicion` oficial (1 eliminatoria, 2 liga) **y** `kind` (liga/copa/trofeo) del nombre | Hallazgo 4 |
| Categorías senior…veteranos | se añaden `universitario` y el ámbito `scope` (`federated`, `school`, `other`) desde `nombre_grupo_categoria` | Hallazgo 4–5 |
| `ingest_jobs`, `raw_fetches` en 0007 | **pasan a 0008** con la cola (T06): 0007 solo catálogo e identidad | Un dueño por migración y fase |

**Migración 0007 (aditiva; nada se borra ni se renombra)**

- `seasons` + `label`, `start_date`, `end_date`, `is_current` (índice único parcial `(source) WHERE is_current`).
- `competitions` + oficiales: `game_type_code`, `category_code`, `category_name`, `category_group_code`,
  `category_group_name`, `format_code`, `active` (bool nulo), `start_date`, `end_date`, `source_meta` jsonb (puntos,
  minutos, partes, flags de goleadores/clasificación); normalizados: `modality`, `scope`, `category`, `gender`, `kind`,
  `phase`, `division` (todos `text NOT NULL DEFAULT 'unknown'`), `classification` jsonb, `review_status`
  (`auto|needs_review|confirmed`, def. `needs_review`), `removed_at`. Índices `(season_id, modality, category, gender)`,
  `(review_status)`, `(source, category_code)`.
- `competition_groups` + `expected_rounds`, `expected_teams`, `delegation`, `has_standings`, `has_scorers` (bool nulo),
  `removed_at`.
- `clubs` (nueva): `id`, `source`, `external_code` (único con `source`), `name`, `normalized_name`, `crest_url`,
  `first_seen_at`, `last_seen_at`.
- `team_entities` (nueva): `id`, `source`, `external_code` (= `CodEquipo`, único con `source`), `club_id` FK nula,
  `club_link_status` (`official|override|unknown`, def. `unknown`), `name` (último visto), `first_seen_at`, `last_seen_at`.
- `teams` + `team_entity_id` FK nula, `squad` text nulo, `crest_url` text nulo. Índices `(team_entity_id)`.
- `competition_links` (nueva): `id`, `from_competition_id`, `to_competition_id` (FK, `from` temporada anterior),
  `basis` (`category_code+name|override`), `confidence` numeric(3,2), `status` (`proposed|confirmed|rejected`); único
  `(from, to)`. Solo `confirmed` se usa para responder «la misma competición el año pasado».
- `catalog_overrides` (nueva): como en la propuesta, con `kind` ∈ `competition|club_link|competition_link`.
- Vista `team_lineage`: una fila por participación (`teams`) con `team_entity_id`, `club_id`, temporada, competición,
  categoría, género y grupo.

**Política de metadatos corregidos a mano**: la precedencia es `override > campo oficial > regla > unknown`. Ninguna
ejecución automática (importación, backfill, descubrimiento) modifica un atributo cuyo `classification.<attr>.source`
sea `override`, ni una competición con `review_status='confirmed'` salvo con `--reclassify-confirmed` explícito, que
registra el valor anterior en `classification.<attr>.previous`.

**Backfill idempotente** (`catalog-backfill`, sin red): (1) `team_entities` desde `DISTINCT (teams.source,
teams.external_id)` y enlace `teams.team_entity_id` (el `CodEquipo` oficial es estable [C]); (2) metadatos y
clasificación de competiciones desde el inventario de catálogo en disco si existe y, si no, solo por nombre (con
confianza menor y `needs_review` donde corresponda); (3) `seasons.label/fechas` si el inventario las trae. Dos
ejecuciones seguidas: la segunda informa 0 cambios. Los clubes (`fichaequipo`) se rellenan en la oleada 1 con red.

**Rollback**: `downgrade` de 0007 elimina solo lo añadido (vista, tablas nuevas, columnas, índices); las tablas y datos
previos quedan intactos (prueba: recuentos y sumas de verificación por tabla antes/después de upgrade→downgrade→upgrade).

**Privacidad (A9, pendiente del permiso de la federación)**: el catálogo y los resultados de todas las categorías sí;
datos personales de jugadores de categorías menores **no se exponen** mientras Futsito sea privado y no haya permiso;
nunca se ingieren datos de contacto de fichas de club/equipo (hallazgo 14).

## Hallazgos que condicionan el diseño (comprobados en el código y en las fixtures)

- La RFFM ya entrega campos estructurados que hoy se leen pero **no se guardan**: por competición `codigo_tipo_juego`,
  `NombreCategoria`, `CodigoCategoria`, `Activa`, `FechaInicio`, `FechaFin` (`ingestion/rffm/catalog.py: parse_competitions`);
  por grupo `total_jornadas`, `total_equipos` y `nombre_delegacion`; por temporada `cod_temporada`, `nombre`, `fecha_inicio`, `fecha_fin`.
- En PostgreSQL `competitions` solo guarda `name` y `season_id`. La categoría se deduce del nombre en cada consulta y por
  defecto es `senior` (`domain/competitions.classify`, `default_age="senior"`). **Error demostrado**: una competición
  «CADETE …» se clasificaría como senior.
- El escudo de cada equipo lleva un código con aspecto de identificador de club: `/pnfg/pimg/Clubes/00100_0000002531_…`.
  Los nombres terminan en sufijo de equipo: `PARQUE NORTE F.S. - RODILLITO 'B'`. **[H]** El código del escudo es estable
  entre categorías y temporadas; se comprueba en el sondeo A1.
- `teams` es único por `(group_id, external_id)`; ya hay 95 nombres repetidos en más de un grupo, sobre 225 **[M]**.

## Principios

1. **El origen manda**: un atributo se toma del campo oficial cuando existe; las reglas por nombre solo lo completan.
2. **Desconocido antes que inventado**: sin señal suficiente, `unknown` y `review_status='needs_review'`. Ningún valor por defecto.
3. **Aditivo**: la migración 0007 solo añade columnas y tablas con valores nulos o por defecto seguros. API v1, MCP v1 y el
   agente actual siguen funcionando sin cambios.
4. **Mínimo necesario**: dos tablas nuevas para identidad (`clubs` y columnas en `teams`) y tres para operación (`ingest_jobs`,
   `raw_fetches`, `catalog_overrides`). El linaje de equipo entre temporadas es una **vista**, no una tabla.

## Migración 0007 (propuesta, pendiente de aprobación A2)

### `seasons` (+columnas)
| Columna | Tipo | Notas |
|---|---|---|
| `label` | text | p. ej. `2026-2027`, desde `season.nombre` |
| `start_date`, `end_date` | date | desde la fuente |
| `is_current` | bool | calculado por el descubrimiento (fecha de hoy dentro del rango); una sola por `source` |

### `competitions` (+columnas)
| Columna | Tipo | Valores | Origen |
|---|---|---|---|
| `game_type_code` | text | código de la fuente (`3`) | `codigo_tipo_juego` |
| `modality` | text | `futsal`, `football11`, `football7`, `unknown` | tabla de correspondencias por fuente |
| `category_code`, `category_name` | text | tal cual la fuente | `CodigoCategoria`, `NombreCategoria` |
| `category` | text | `senior`, `juvenil`, `cadete`, `infantil`, `alevin`, `benjamin`, `prebenjamin`, `veteranos`, `unknown` | regla sobre `category_name`, después sobre `name` |
| `gender` | text | `male`, `female`, `mixed`, `unknown` | idem; en la fuente el masculino suele ser implícito, así que `male` solo se asigna con regla explícita aprobada (ver más abajo) |
| `kind` | text | `league`, `cup`, `tournament`, `playoff`, `unknown` | nombre más formato de grupos (un único grupo «Eliminatorias» indica eliminatoria) |
| `phase` | text | `regular`, `preliminary`, `final`, `unknown` | nombre (`fase previa`, `fase final`) |
| `division` | text | nombre de división normalizado (`tercera`, `primera_autonomica`, `preferente`, `primera`, `segunda`…) | regla; `unknown` si no encaja |
| `active` | bool | | `Activa` |
| `start_date`, `end_date` | date | | fuente |
| `classification` | jsonb | `{campo: {value, source: "source_field|rule|override", rule_id, confidence}}` | trazabilidad por atributo |
| `review_status` | text | `auto`, `needs_review`, `confirmed` | |
| `removed_at` | timestamptz | la competición dejó de aparecer en el catálogo | descubrimiento |

Índices: `(season_id, modality, category, gender)`, `(review_status)`.

**Regla de confianza**: un atributo es `auto` si procede de un campo oficial con correspondencia conocida, o de una regla con
coincidencia explícita y sin conflicto entre fuentes. Si hay conflicto (por ejemplo, la categoría oficial dice «Juvenil» y el
nombre dice «Cadete») o no hay señal, se guarda `unknown` y la competición queda en `needs_review`. Una competición con
cualquier atributo clave (`modality`, `category`, `gender`, `kind`) en `unknown` **no se expone al resolutor como candidata
automática**: el agente la nombra solo si el usuario la cita literalmente, y avisa de que no está clasificada.

**Género**: si en la muestra real del sondeo A1 las competiciones femeninas llevan una marca explícita («FEMENINO», «FEM») en
`NombreCategoria` o en el nombre, se aprueba una regla `implicit_male` por fuente y modalidad: sin marca femenina y con
categoría oficial conocida se clasifica como `male`. Hasta aprobar esa regla, `gender=unknown` en lo no marcado. Así no se
inventa, pero tampoco se bloquea el catálogo entero.

### `competition_groups` (+columnas)
`expected_rounds int`, `expected_teams int`, `delegation text`, `format text` (`league`, `knockout`, `unknown`), `removed_at`.

### `clubs` (nueva)
| Columna | Tipo | Notas |
|---|---|---|
| `id` | bigint PK | |
| `source` | text | `rffm` |
| `external_code` | text | código del escudo (`0000002531`) **[H, A1]**; único por `(source, external_code)` |
| `name`, `normalized_name` | text | nombre más frecuente sin sufijo de equipo |
| `crest_url` | text | |
| `first_seen_at`, `last_seen_at` | timestamptz | |

### `teams` (+columnas)
`club_id bigint null FK clubs`, `squad text null` (`A`, `B`, `C`… extraído del sufijo `'X'`), `base_name text` (nombre sin
sufijo), `crest_url text`.

### Vista `team_lineage`
`lineage_key = (club_id, category, gender, coalesce(squad,'A'))` sobre `teams ⨝ competition_groups ⨝ competitions ⨝ seasons`.
Responde «cómo quedó el Parque Norte B juvenil en cada temporada» sin tabla nueva. Si un club cambia de código o de nombre,
una fila en `catalog_overrides` (`kind='club_merge'`) lo une; nada se adivina.

### `catalog_overrides` (nueva)
Correcciones manuales auditables: `id, source, kind ('competition'|'club_merge'|'team_club'), target_external_id, fields jsonb,
reason text, created_at, created_by`. Prevalecen sobre las reglas. La CLI `catalog-review` las lista y las aplica.

### `ingest_jobs` y `raw_fetches` (nuevas)
Están en [ingestion.md](ingestion.md). `raw_fetches` sustituye al mtime del fichero como estado: URL, `fetched_at`, estado
HTTP, `content_hash`, ruta del snapshot y `parse_status`.

## Multi-federación (futuro, sin código ahora)

- Toda entidad ya lleva `source`; los IDs externos son únicos por `(source, external_id)`. Otra federación es un nuevo `source`
  y un nuevo adaptador en `ingestion/<fuente>/`, que produce los mismos modelos normalizados (temporada, competición, grupo,
  jornada, partido, acta, tablas).
- Las correspondencias de `game_type_code`, `category_code` → valores normalizados se guardan por `source`
  (`config/sources/<fuente>.json`).
- **[H]** El escudo usa la ruta `/pnfg/…` y el prefijo `00100`, que apuntan a una plataforma federativa compartida por varias
  federaciones territoriales. Si se confirma, el adaptador RFFM se podría reutilizar con otra URL base y otro prefijo. No se
  construye nada hasta que se pida.
- El resolutor y las herramientas filtran por `source` (federación) además de por temporada; con una sola fuente el filtro es
  implícito.

## Volumen y rendimiento

Volumen esperado con todo el FS de la RFFM: decenas de miles de partidos por temporada [E]; con varias temporadas, cientos de
miles. Índices nuevos: `matches(group_id, status, scheduled_at)`, `matches(scheduled_at)` (planificador),
`teams(lower(kb_unaccent(name)))` o índice trigram (`pg_trgm`) para la búsqueda de equipos y clubes, `teams(club_id)`.
No hace falta particionar.

## Pruebas obligatorias de la migración

- `alembic upgrade head` y `downgrade -1` en una BD desechable; datos de la Fase 14 intactos (recuentos iguales antes y después).
- Clasificador: tabla de casos con nombres reales del sondeo A1 más casos adversariales («COPA JUVENIL FEMENINA – fase final»,
  «CADETE PREFERENTE FS», nombres sin categoría). **Ningún caso sin señal sale `senior`.**
- Extracción de club y equipo: escudos y nombres reales (con y sin sufijo, con comillas tipográficas).
