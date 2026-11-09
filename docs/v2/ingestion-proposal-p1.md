# v2 · Propuesta P1: ingesta completa de la temporada 2026‑27 sin actas (NO ejecutada; pendiente de autorización)

Base: P0 ([p0-catalog.md](p0-catalog.md)): 50 competiciones (34 activas) y 89 grupos (71 activos), todos de la delegación
central; endpoints y tamaños verificados en A1 ([probe-a1.md](probe-a1.md)). Las 51 peticiones de P0 **no autorizan** P1.

## 1. Alcance

Las 50 competiciones y los 89 grupos de T22: partidos (fecha, hora, campo, marcador, estado, acta cerrada sí/no),
clasificación oficial y goleadores oficiales. **Fuera de alcance**: actas (P2, pendiente de A9 para las categorías
inferiores), fichas de club/equipo (P3) e histórico.

## 2. Peticiones desglosadas

| Bloque | Endpoint | Cálculo | Peticiones |
|---|---|---|---|
| D Catálogo (detectar cambios desde P0) | `/api/competitions` + `/api/groups` | 1 + 50 | **51** |
| C Calendario de cada grupo | `/competicion/calendario` | 89 | **89** |
| S Clasificación oficial | `/competicion/clasificaciones` | solo grupos de liga (`format_code=2`); los de eliminatoria quedan `not_applicable` como en la Fase 16 | **≈ 59–65** |
| G Goleadores oficiales | `/competicion/goleadores` | grupos con `clasificacion_goleadores=1` | **≤ 84** |
| R Estado de las jornadas jugadas | `/api/results?idGroup&round` | (grupo, jornada) con algún partido de fecha pasada según el calendario; ~7 por grupo activo | **≈ 500** (cota: 71 × 9) |
| **Total** | | | **≈ 790** (rango 690–850) |

`ingest plan` calculará la lista **exacta** sin red a partir de los calendarios. Se presentará antes de pedir la
autorización con su número cerrado. Tamaño estimado de las descargas: ≈ 90 MB (las páginas HTML pesan ~300 KB; el JSON de jornada, ~9 KB).

## 3. Frecuencia, concurrencia y paradas

- **1 petición cada 3 s** por host, **1 trabajador** y **sin concurrencia**: ≈ 40 min para P1 completa.
- Tope duro por ejecución (`--max-requests`, igual al número autorizado) y registro de **todas** las peticiones (`raw_fetches`:
  URL, hora, estado HTTP, bytes, sha256, ruta del snapshot y resultado del análisis).
- Ventana preferente: de lunes a jueves, fuera de las franjas de partidos.
- **Parada inmediata** (interruptor del `source`): cualquier 429 o 403; 3 errores 5xx o de red seguidos; 2 cuerpos `null` o vacíos
  seguidos en URLs distintas; más del 20 % de páginas de un tick sin la forma esperada de `__NEXT_DATA__`; cualquier página
  cuya temporada o tipo de juego no coincida con lo pedido (la RFFM responde 200 con otro ámbito, hallazgo A1‑3).

## 4. Reintentos y recuperación

- Reintentos solo de errores transitorios (5xx, red, `null`): 3 intentos con espera 10 s → 30 s → 90 s. Después, el trabajo pasa
  a `dead` y genera una alerta. Un 429 nunca se reintenta: abre el interruptor.
- Cola `ingest_jobs` en PostgreSQL (migración 0008, T06): **un trabajo por (grupo, endpoint, jornada)**, idempotente por
  `(kind, target_key)` con índice único parcial, arrendamiento (`lease_until`) y `SELECT … FOR UPDATE SKIP LOCKED`.
- Si el proceso se interrumpe, los arrendamientos vencidos vuelven a `pending`: se pierde como mucho una petición. `ingest work --resume`
  continúa donde se quedó y nunca repite un trabajo `done` salvo con `--refresh`.
- Cada ejecución queda en `ingestion_runs` con el recuento por estado, el número de peticiones y el motivo de la parada.

## 5. Actualización incremental y prioridad

Orden de la carga inicial (prioridad descendente):
1. Jornadas con partidos en los últimos 7 días y la jornada del próximo fin de semana de los grupos activos.
2. Clasificación y goleadores de los grupos activos.
3. Resto de jornadas jugadas de los grupos activos, de la más reciente a la más antigua.
4. Las 16 competiciones inactivas (18 grupos): solo calendario y tablas finales.

Mantenimiento posterior, con el planificador de ingestion.md §3 y umbrales en `config/ingest_schedule.json`; nada fijo en código:
- calendario diario por grupo activo (≈ 71/día), para detectar cambios de fecha, hora o campo y partidos aplazados;
- jornada del fin de semana: consultas a +75, +110 y +180 min del inicio de cada franja y barrido cada 2 h (≈ 2.060 peticiones por fin de semana);
- clasificación y goleadores solo cuando cambia un resultado del grupo (agrupando 30 min);
- jornadas `past_due` (fecha pasada sin resultado) cada 12 h durante 7 días.

## 6. Duplicados y regresiones

- Identidad por códigos oficiales: `codacta` (partido), `CodEquipo` (equipo, global) y códigos de grupo y competición. Las
  restricciones únicas actuales y `team_entities` impiden duplicar.
- Puertas de la Fase 21 sin cambios:
  - `identity_conflict`: un partido no cambia de grupo;
  - `finished_regression_held`: un resultado final no se pierde por una lectura posterior incompleta;
  - las capturas `ok` no se sustituyen por `empty` o `incomplete`.
- Coherencia entre fuentes: el marcador del calendario y el de `/api/results` deben coincidir; si discrepan → `data_quality_issues`,
  y prevalece `/api/results` (trae el estado y el acta).
- Cambios de horario: se guardan como observación (`match_observations`) y nunca sobrescriben un partido ya finalizado.

## 7. Métricas de cobertura y calidad (criterios de aceptación de P1)

| Métrica | Objetivo |
|---|---|
| Grupos con calendario ingerido | 89/89 |
| Jornadas presentes frente a `expected_rounds` (ligas) | 100 % |
| Partidos de fecha pasada con marcador o estado explicado (aplazado, suspendido, `past_due`) | ≥ 98 % |
| Clasificación oficial `ok` en grupos de liga activos | ≥ 95 % (el resto con motivo) |
| Goleadores `ok` o `empty` válido | ≥ 95 % |
| Duplicados, huérfanos, `identity_conflict` | 0 |
| Páginas con ámbito distinto del pedido | 0 aceptadas |
| Peticiones realizadas frente a autorizadas | ≤ 100 % (tope duro) |
| Incidencias de `data_quality_issues` | listadas y clasificadas, ninguna sin revisar |

Salida: `ingest status` y `GET /coverage` (T08) con estas cifras por grupo, más un informe `docs/v2/p1-report.md`.

## 8. Requisitos previos (offline, delegables a Sonnet; sin red)

| Tarea | Contenido | Archivos (dueño único) |
|---|---|---|
| T05 Descubrimiento v2 | `/api/competitions` + `/api/groups` → informe con metadatos oficiales → `import_catalog` (ya los persiste) | `ingestion/rffm/catalog*.py`, `catalog_commands.py` |
| T05b Parsers nuevos | `calendario` (HTML) y `/api/results` (JSON), con fixtures grabadas en A1 (datos públicos de partidos) y validación de ámbito | `ingestion/rffm/calendar.py` (nuevo), `results_api.py` (nuevo) |
| T06 Cola y trabajador | migración 0008 (`ingest_jobs`, `raw_fetches`), `ingest plan/tick/work/status`, limitador, interruptor y reintentos | `ingestion/queue/**` (nuevo), `alembic/0008_*` |
| T08 Cobertura | vista `coverage_status`, `GET /coverage`, métricas del §7 | `api/coverage.py` (nuevo), vista en 0008 |

Aceptación offline antes de pedir P1:
- matar el trabajador a mitad de una ejecución → al reanudar no hay duplicados ni pérdidas (prueba PG);
- `ingest plan` con un reloj fijo produce la lista exacta de peticiones;
- todas las pruebas de la Fase 21 pasan sin cambios;
- un servidor HTTP simulado que devuelve 429, `null` o otro ámbito abre el interruptor.

## 9. Lo que se pedirá autorizar

«P1: hasta N peticiones GET (N = la salida de `ingest plan`, ≈ 790), 1 cada 3 s, sin actas ni fichas, con parada ante cualquier
señal de limitación». Las actas (P2), los clubes (P3), el mantenimiento automático y el histórico se pedirán por separado.
