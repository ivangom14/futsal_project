# v2 · Descubrimiento, ingesta y mantenimiento

Hay tres procesos separados que se coordinan a través de PostgreSQL:
**descubrimiento** (qué existe) → **ingesta y mantenimiento** (obtener y actualizar) → **consulta** (el agente, solo lectura).

## Qué se conserva de lo actual

`PoliteFetcher` (ritmo, reintentos, `Retry-After`), los parsers puros de `__NEXT_DATA__`, `scrape_group`/`run_import`
idempotentes, las puertas de la Fase 21 (`identity_conflict`, `finished_regression_held`, capturas `ok` frente a
`incomplete`/`empty`), `ingestion_runs`, `data_quality_issues`, `dataset_refreshes` y los snapshots en disco para auditar.

Lo que cambia es el **estado operativo**: pasa de ficheros (inventario JSON, mtime del HTML) a tablas, y la ejecución pasa de
una CLI lanzada a mano a una cola con un planificador.

## 1. Descubrimiento automático

| Paso | Fuente | Frecuencia | Detecta |
|---|---|---|---|
| Temporadas | **[H, A1]** selector de temporadas de la web; si no existe un listado, la `season` de cualquier página futsal más el sondeo de `temporada=N+1` | semanal; diario entre junio y septiembre | temporada nueva, `is_current` |
| Competiciones | `pageProps.competitions` de una página futsal de esa temporada (la semilla se obtiene del propio catálogo de la temporada, no de `config/targets.py`) | diario | nuevas, retiradas (`removed_at`), cambio de nombre o de `Activa` |
| Grupos | `/api/groups?competicion=` | diario y cuando aparece una competición | nuevos o retirados; `total_jornadas`, `total_equipos` |
| Jornadas | página del grupo sin `jornada` → `rounds.jornadas` | cuando aparece el grupo y en cada barrido semanal | jornadas añadidas (copas por fases) |

- **Alcance por datos, no por lista**: `source=rffm`, `modality=futsal` y la temporada actual; las exclusiones son filas de
  `catalog_overrides`. `CATALOG_SCOPE` deja de usarse (se conserva hasta que la v2 alcance paridad).
- **Problema del arranque**: hoy hace falta un grupo futsal conocido para ver el catálogo futsal. En v2 la semilla de cada
  temporada es cualquier grupo futsal ya descubierto de esa temporada. Para una temporada nueva, la primera semilla sale del
  sondeo con `tipojuego=3` sin grupo, o de una competición que se repite entre temporadas **[H, A1]**.
- **Diff del catálogo**: cada ejecución guarda el conjunto descubierto y lo compara con la BD. Las altas generan trabajos; las
  bajas solo marcan `removed_at` (nunca se borra); los cambios de nombre se registran en `classification`.
- **Clasificación** ([data-model.md](data-model.md)): automática con confianza; lo dudoso va a `needs_review`. **Los datos se
  ingieren igual**: clasificar no bloquea la ingesta, solo la exposición automática al resolutor.

### Temporada nueva: intervención manual mínima
1. El descubrimiento detecta `cod_temporada` nuevo → crea `seasons` y encola el catálogo.
2. Las competiciones se clasifican; las que tienen todo `auto` quedan disponibles.
3. Se encolan grupos, jornadas, clasificaciones y goleadores; el planificador las prioriza por fecha.
4. **Única acción humana**: revisar `catalog-review` (competiciones en `needs_review`) y, si los hubiera, los clubes sin código.
5. El agente pasa a «temporada actual» cuando `is_current` cambia; las preguntas sin temporada usan la actual.

## 2. Cola de trabajos (`ingest_jobs`)

| Columna | Notas |
|---|---|
| `id`, `source` | |
| `kind` | `discover_seasons`, `discover_catalog`, `discover_groups`, `group_seed`, `round`, `standings`, `scorers`, `match_report` |
| `target` jsonb y `target_key` text | p. ej. `{"season":"22","competition":"…","group":"…","round":"7"}`; `target_key` canónico |
| `priority` int, `not_before` timestamptz | los asigna el planificador |
| `status` | `pending`, `running`, `done`, `failed`, `dead` |
| `attempts`, `max_attempts`, `last_error`, `last_http_status` | reintento con espera exponencial; `dead` tras agotarlos (genera alerta) |
| `lease_until`, `worker` | arrendamiento; un trabajo con arrendamiento vencido vuelve a `pending` (recuperación tras caída) |
| `checkpoint` jsonb | progreso dentro del trabajo (p. ej. en un descubrimiento de grupos, último índice procesado) |
| `run_id` FK `ingestion_runs` | trazabilidad |

- Índice único parcial `(kind, target_key) WHERE status IN ('pending','running')`: **no se pueden duplicar trabajos**.
- Toma de trabajos con `SELECT … FOR UPDATE SKIP LOCKED`: varios procesos son posibles, aunque por defecto hay uno.
- **La unidad natural de checkpoint es `(grupo, jornada)`**: una página contiene la jornada entera. Si el proceso muere a mitad
  de una competición, se pierde como mucho una página.
- La escritura en BD pasa por las puertas de la Fase 21 sin cambios: una respuesta vacía o incompleta nunca pisa datos buenos.

## 3. Planificador según el calendario

El planificador (`ingest tick`, idempotente) calcula cada X minutos qué toca y encola. La frecuencia **se ancla a los horarios
reales de los partidos**, no a un intervalo fijo. Así se consigue mucha frecuencia en la jornada del fin de semana sin
consultar lo que no cambia.

| Estado de la jornada (por grupo) | Condición (hora de Europe/Madrid) | Cuándo se consulta |
|---|---|---|
| **En juego** | algún partido con `scheduled_at` ∈ [ahora − 3 h, ahora] sin resultado final | a `inicio + 75 min`, `+ 110 min`, `+ 180 min` por franja de inicio distinta, y cada 30 min mientras quede alguno pendiente en la franja |
| **Fin de semana activo** | la jornada del fin de semana en curso (viernes 16:00 → lunes 12:00) | además, barrido cada 2 h de 09:00 a 23:30 (detecta aplazamientos y cambios de horario) |
| **Asentándose** | partidos terminados hace < 72 h, o acta sin cerrar | cada 6 h (correcciones de marcador y cierre de actas) |
| **Próxima** | primer partido en ≤ 14 días | diario (cambios de calendario) |
| **Pasada atrasada** (`past_due`) | fecha pasada sin resultado | cada 12 h durante 7 días; después diario |
| **Cerrada** | todo terminado y asentado | semanal durante 30 días; después mensual |
| **Lejana / sin fecha** | | semanal |

Derivados:
- **Clasificación y goleadores**: se encolan al detectar un cambio de resultados en el grupo (con 30 min de agrupación), y
  como mínimo una vez al día en un grupo con actividad.
- **Actas**: se encola `match_report` cuando un partido pasa a `finished` con acta cerrada. Si el acta no está cerrada, se
  reintenta con el estado «asentándose».
- Todos los umbrales viven en configuración (`config/ingest_schedule.json`), no en código, y se ajustan con las mediciones.

### Presupuesto de peticiones [E — se medirá]
- Con G grupos activos y unas 7 franjas de inicio distintas por jornada y grupo [H], una jornada de fin de semana cuesta
  ≈ G × (7 × 3 + 8 barridos) ≈ **29·G peticiones**: unas 5.800 con G = 200. Entre semana ≈ 2·G al día.
- Un limitador global (por host, ~1 petición cada 3 s por defecto, con espera adaptativa ante errores o `results` vacíos)
  procesa ~1.200 peticiones por hora. La jornada del fin de semana cabe con holgura. Si la cola se atrasa, se sirve primero
  la prioridad alta (en juego) y se aplazan los barridos.
- **Experimentos necesarios antes de producción** (requieren A4): ritmo que tolera la RFFM sin `results` vacíos, latencia de
  publicación del resultado tras el final (fija los `+75/+110/+180`) y valor real de G y de las franjas.

## 4. Fallos y cambios en la web

- **Errores transitorios** (red, 5xx, `results` vacío o `null`): reintento con espera exponencial; sin pisar datos.
- **Cambio de estructura**: cada página se valida contra un esquema mínimo de `__NEXT_DATA__` (claves y tipos esperados). Si
  más del 20 % de las páginas de un tick fallan la validación, un **interruptor** detiene la cola de ese `source` y genera una
  alerta crítica. Así no se llena la BD de incidencias ni se martillea la web.
- **Canario**: un grupo de referencia se compara a diario con su fixture de forma (no de contenido) para detectar cambios.

## 5. Cobertura, frescura y alertas

Vista `coverage_status` (por grupo y conjunto de datos: partidos, actas, clasificación, goleadores):
`expected` (jornadas y equipos declarados), `present`, `finished_with_score`, `reports_present`, `last_success_at`,
`last_attempt_at`, `consecutive_failures`, `freshness` (`fresh`, `stale`, `failing`) según un SLA por estado de jornada (por
ejemplo, en juego: resultado < 3 h tras el final previsto).

- CLI `ingest status` (resumen y lista de lo que incumple) y endpoint de solo lectura `GET /coverage` (para el panel y para
  que el agente avise de datos atrasados).
- **Alertas** (fichero de log estructurado y, en producción, un webhook configurable): interruptor abierto, trabajo `dead`,
  grupo `failing` en fin de semana, catálogo con altas o bajas, competiciones nuevas en `needs_review`, cola atrasada más de 1 h.

## 6. CLI (nueva, junto a la actual)

`ingest discover [--season]` · `ingest plan` (qué se encolaría y por qué, sin red) · `ingest tick` (planifica) ·
`ingest work [--max-requests N] [--until-empty]` · `ingest status` · `catalog-review [--apply]`.
Despliegue: `tick` y `work` lanzados por cron o systemd (o por el Programador de tareas de Windows en el PC). No hace falta
broker ni demonio propio. Pendiente de A6.

## 7. Aceptación de la oleada 1

- Descubrimiento con fixtures grabadas: 100 % de las competiciones FS de la muestra clasificadas o en `needs_review`, 0 senior
  por defecto.
- Matar el proceso a mitad de un trabajo → al reanudar no hay duplicados ni pérdidas (prueba de integración con PG).
- `ingest plan` con un reloj simulado de sábado a las 12:00 produce las consultas previstas en la tabla (prueba unitaria del
  planificador con fechas fijas).
- Puertas de la Fase 21: todas sus pruebas siguen pasando sin modificarlas.
