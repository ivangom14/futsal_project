# v2 · Modelo de datos

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
