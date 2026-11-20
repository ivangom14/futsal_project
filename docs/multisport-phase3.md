# Multideporte · Fase 3: ampliación completa del fútbol 11 (desarrollo; activación pendiente)

Estado: **desarrollo terminado, sin activación real.** 0 peticiones a la RFFM, 0 llamadas a LLM de pago, sin commit. Fase 1 = `a8f3162`; la Fase 2 sigue sin commit (instantánea separable de la Fase 2: diff e inventario de ficheros nuevos guardados antes de empezar). El alcance operativo de fútbol 11 sigue **deshabilitado**; la programación (`deploy/systemd`, `scripts/update_rffm.sh`) y los límites no han cambiado.

## 1. Estado por subfase

| Subfase | Estado | Validación |
|---|---|---|
| 3A Presupuestos y concurrencia | **Hecha** | fixtures y PostgreSQL aislado (`test_ingest_budget_pg.py`) |
| 3B Cobertura básica de las 12 | **Preparada** (código, lotes, inventario y comprobación de cobertura); la carga real de los 93 grupos está **pendiente de autorización** | fixtures reales de catálogo y grupos (`test_football11_coverage_pg.py`), volumen sintético para SQL |
| 3C Actas de fútbol 11 | **Hecha**: migración 0010, parser, importación, API y agente | fixtures anonimizadas del piloto; hash de las actas de FS sin cambios |
| 3D Activación gradual | **Preparada** (plan, validación, reversión, simulación de carga); activación **pendiente de autorización** | simulación con el planificador real |

## 2. 3A · Presupuestos estrictos (incidente de las 5 actas)

**Causa:** el tope global (`--max-requests`) sí se respetó; faltaba un tope **por tipo**. La cola reclama siempre por prioridad, y las actas que encola cada jornada (prioridad alta) adelantaban a las jornadas pendientes.

**Solución** (misma cola, mismo planificador y mismo trabajador):
- `ingest work --max-reports N`: tope ESTRICTO de actas por ejecución. Agotado, `claim` excluye `match_report`. Las actas restantes siguen `pending`, con su prioridad y sin duplicarse, y la siguiente ejecución las toma sin repetir descargas.
- `--kinds` / `--exclude-kinds`: lotes por tipo (p. ej. la carga básica sin actas).
- **Política de actas en el alcance** (`reports` por deporte o competición): control **antes de encolar** (el manejador de jornadas), **antes de planificar** (`GroupState.reports_allowed`) y **antes de ejecutar** (el trabajador rechaza sin red). Los juveniles de fútbol 11 tienen `reports: false`.
- **Un solo trabajador por fuente:** candado de PostgreSQL (`pg_try_advisory_lock`). Un segundo trabajador termina con `stop_reason=locked` y 0 peticiones. Complementa al `flock` del script.
- **Registro:** `ingestion_runs.stats` guarda `requests_by_kind` (peticiones reales, reintentos y errores incluidos), el presupuesto usado, los tipos aplazados y los pendientes por tipo al terminar.

**Pruebas:** el incidente se reproduce sin tope (más de 3 actas con 25 peticiones) y con `max_reports=3` se descargan exactamente 3. Se prueban también los aplazados sin pérdida ni duplicados, la reanudación sin repetir descargas, `--max-reports 0` y `--exclude-kinds`, la política que bloquea antes de encolar, el candado y el tope global contando los 404.

## 3. 3B · Cobertura básica de las 12 competiciones

- **Inventario oficial** (catálogo y grupos del 20-11-2026, Fase 2): 12 competiciones, 93 grupos y 1.627 plazas. Las respuestas de grupos son metadatos públicos y se guardan como fixtures para probar sin red.
- **Estado oficial respetado:**
  - COPA RFEF FASE AUTONÓMICA inactiva (`active=false`): su calendario se pide con la prioridad baja de las inactivas y su cadencia es la de las inactivas.
  - COPA DE AFICIONADOS: eliminatoria (`format_code=1`, `kind=cup`).
  - Nunca se calculan clasificaciones propias: solo se guardan las tablas oficiales, y los rankings excluyen las copas por defecto.
- **Lotes reanudables:**
  - `ingest tick --competition <código>` / `--group <código>` (repetibles) planifican solo ese lote; el alcance sigue mandando.
  - `ingest work --max-requests N --exclude-kinds match_report` descarga el lote sin actas.
  - La cola es persistente e idempotente: reiniciar no repite lo ya hecho.
- **Comprobación de cobertura:** `ingest coverage --sport football11` (solo lectura) muestra, por competición:
  - grupos con partidos frente a grupos oficiales y equipos frente a plazas;
  - partidos y finalizados;
  - clasificación y goleadores `ok`;
  - actas, inactiva y eliminatoria.

  Nunca da por completa una competición sin datos.
- **SQL:** con un volumen sintético de unas 2,5 veces la temporada (43.432 partidos: 27.234 de F11 y 16.198 de FS, en 178 grupos), los endpoints principales de los dos deportes responden con medianas de 9–45 ms y máximos de ~110 ms. El filtro por deporte por fecha tarda 8 ms en SQL. **No se añade ningún índice**: las medidas no lo justifican.
- **Pendiente:** la carga real (ver §9).

## 4. 3C · Actas de fútbol 11

- **Migración 0010** (aditiva y reversible):
  - `match_events.event_type` admite `substitution`; `player_*` es el jugador que **entra** y `related_player_*` (id, código y nombre) el que **sale**, con el minuto y los dorsales tal cual.
  - `match_staff.role_label` guarda el cargo oficial de los «otros técnicos» (`role=other_staff`, p. ej. «ENCARGADO DE MATERIAL»).
  - El `downgrade` se niega si ya hay sustituciones guardadas.
- **Parser:**
  - Interpreta `sustituciones_equipo_*` y `otros_tecnicos_*`.
  - Una fila malformada nunca se convierte en un evento vacío: se omite y se avisa como «filas no interpretables».
  - El esquema declarado (p. ej. 1-4-3-3) se conserva; **no se atribuyen posiciones** (la fuente las da vacías).
- **Hash estable:** las claves nuevas vacías no entran en el hash. Las actas de fútbol sala (sin esas secciones) conservan exactamente su hash anterior (`27c92f3e…`, comprobado contra el código de la Fase 1), así que reimportarlas no crea observaciones nuevas.
- **Las sustituciones no son goles:** se revisaron todos los usos de «lo que no es tarjeta». Marcador derivado, consistencia del acta y goleadores cuentan solo `goal` y `own_goal`.
- **API:** sección `substitutions`; en F11 va siempre junto con `players`, sin cambiar el esquema MCP que ve el LLM. En `staff` se añade `role_label`, y F11 ya no tiene `not_ingested`.
- **Agente:** en F11, «¿qué cambios hubo?» se responde con datos (acta completa con sustituciones); en FS sigue «el acta no recoge las sustituciones». El prompt de F11 ya no da las sustituciones como no cubiertas.
- **Estados del dato:**

  | Estado | Cómo se indica |
  |---|---|
  | Inexistente en la fuente | `not_in_source` (por deporte) |
  | Disponible pero no procesado | `not_ingested`; hoy vacío, el mecanismo se mantiene |
  | Ilegible | `game=null` → `unreadable`, sin inventar |
  | Pendiente de actualización | acta abierta o aún no descargada, con el motivo en la respuesta |

## 5. Política propuesta para actas juveniles (decisión A9)

**Hoy:**
- La decisión A9 propone `aggregate_only` para juvenil e inferiores (resultados y clasificaciones sí; datos personales de jugadores no), pero **no está implementada**.
- Las actas de fútbol sala juvenil sí se descargan y se exponen, con Futsito en uso privado.
- Las fichas con datos de contacto nunca se ingieren.

**Propuesta para fútbol 11 juvenil (aplicada en el alcance; sin descargas):**
1. **Sí:** grupos, equipos (clubes), calendarios, resultados y clasificaciones oficiales.
2. **Tabla oficial de goleadores:** publica nombres de menores. Se propone guardar solo lo necesario para «máximos goleadores» (nombre tal como lo publica la RFFM, equipo y goles) y **revisar**, con la decisión A9, si se expone por nombre o solo en agregado. Pendiente de tu decisión.
3. **No:** actas juveniles (`reports: false`): ni alineaciones, ni tarjetas nominales, ni técnicos, ni árbitros de esos partidos. Se bloquea al encolar, al planificar y al ejecutar.
4. **Retención:** las instantáneas en bruto de páginas juveniles (que incluyen nombres y fotos de la web) no deberían conservarse más de lo necesario para reprocesar. Propuesta: 30 días; después se borra el fichero y se guarda solo el hash y el estado en `raw_fetches`. Se aplicaría con un comando de mantenimiento, **solo con tu autorización**.
5. **Coherencia:** se recomienda revisar también la política vigente de las actas de fútbol sala juvenil (hoy se descargan) cuando se decida A9.

## 6. Simulación conjunta de carga (planificador real)

**Método:**
- `plan()` y `collapse()` reales con `config/ingest_schedule.json`.
- Programación real: lunes a viernes a las 03:00, fines de semana cada hora, `update_rffm.sh 900` y **`TimeoutStartSec=58min`** de systemd.
- Inventario real: F11 de la Fase 2 y FS del P0.
- Semana del 23 al 29-11-2026 tras dos semanas de calentamiento, a ~4 s por petición (3 s de ritmo y ~1 s de latencia medida).
- Partidos sintéticos: horarios repartidos el sábado 09:00–20:30 y el domingo 09:00–13:30.
- Resultados deterministas: el escenario A se repitió con resultado idéntico.
- La herramienta queda en el scratchpad de la sesión; puede incorporarse al repositorio si se quiere.

| Escenario | Pet./semana FS | F11 | Total | Máx. por ejecución | Más larga | Atraso entre semana | Resultados FS p50/p90 | F11 p50/p90 | Clasif. FS p50 |
|---|---|---|---|---|---|---|---|---|---|
| A · solo FS (hoy) | 4.513 | — | 4.513 | 529 | 35 min | 0 | 30 / 31 min | — | 79 min |
| B · FS + F11, todo igual | 4.399 | 6.223 | 10.622 | 870 | 58 min (corte) | **133–297 trabajos vencidos/día** (calendarios y revisiones) | 32 / 35 min | 30 / 33 min | 85 min |
| C · B + tope 1.200 y timeout 85 min el fin de semana | 4.434 | 6.267 | 10.701 | 900 | 60 min | igual que B | 32 / 35 | 30 / 33 | 85 |
| D · B sin actas F11 de aficionados ni juveniles | 4.437 | 5.656 | 10.093 | 870 | 58 min | igual que B | 32 / 35 | 30 / 33 | 84 |
| **E · B + 2.ª ejecución L-V a las 05:00** | 4.704 | 6.613 | 11.317 | 870 | 58 min | **0 al terminar la de las 05:00** | 32 / 35 | 30 / 33 | — |

**Conclusiones:**
- **El límite efectivo es el tiempo** (58 min × ~15 peticiones/min ≈ 870), no el tope de 900.
- El fin de semana no satura en ningún escenario: el máximo por ejecución horaria es de ~870 en picos puntuales, sin pendientes al cerrar el domingo.
- **Entre semana, B satura**: la única ejecución de las 03:00 no termina y arrastra calendarios y revisiones de partidos vencidos al día siguiente.
- **Fútbol sala pierde algo de frescura** en B: resultados del fin de semana +2 min (p50) y +4 min (p90), clasificaciones +6 min, y calendarios o revisiones de entre semana con hasta un día de retraso.
- El reparto entre deportes es por prioridad y por grupo, sin discriminación: en B, F11 recibe el 59 % (más grupos y más equipos por grupo).
- **Actas:** 0 re-descargas de actas cerradas (verificado en el código y en la simulación). Cada acta se pide de media **dos veces** (al publicarse abierta y al cerrarse): FS 716 y F11 621 peticiones/semana.

## 7. Recomendación de presupuesto (para tu aprobación; nada aplicado)

1. **Mantener el tope de 900 y el timeout de 58 min.**
2. **Añadir una segunda ejecución de lunes a viernes a las 05:00** (mismo servicio y script: una línea más en `futsito-update-weekday.timer`). Escenario E: elimina el atraso de entre semana sin tocar los límites. Con ella, los dos deportes recuperan la frescura de entre semana de hoy.
3. Optimizaciones que reducen peticiones sin perder frescura (cada una necesita tu aprobación porque cambia el comportamiento de FS):
   - Pedir el acta **solo cuando `acta_cerrada=1`** (salvo la primera vez en las 3 h siguientes al partido): ≈ −45 % de peticiones de actas, unas −600/semana entre los dos deportes.
   - Calendario cada 48 h en lugar de cada 24 h para grupos sin partidos esa semana: ≈ −40 % de calendarios.
4. **Carga inicial** fuera de la programación, en tandas nocturnas acotadas (§9).

## 8. Almacenamiento y costes

| Concepto | Hoy | Propuesta | Ahorro |
|---|---|---|---|
| Acta (cada descarga) | instantánea en bruto 264 KB + caché `raw.html` 264 KB + `normalized.json` 18 KB ≈ **546 KB** | JSON útil comprimido 2,4 KB + normalizado comprimido ~3 KB | **−99 %** |
| Calendario / clasificación / goleadores | 418 / 292 / 327 KB por descarga | 6,3 / 2,5 / 6,8 KB | −98 % |
| Disco semanal (instantáneas en bruto) | FS hoy ~885 MB/semana (~45 GB/año); FS+F11 ~1,9 GB/semana (~95 GB/año) | ~36 MB/semana | −98 % |
| Base de datos | ~125 filas por acta; 4 actas F11 ≈ 0,7 MB con índices | sin cambios | — |
| Tráfico | ~1,5 GB/semana (FS+F11); la RFFM sirve HTML, no se puede reducir el tamaño | actas solo cerradas y calendario cada 48 h: −15–20 % de peticiones | — |

**Propuesta** (sin infraestructura nueva):
- Instantánea = solo `__NEXT_DATA__` útil comprimido, con su hash.
- Eliminar la caché duplicada `raw.html`.
- Retención de las instantáneas en bruto existentes: 30 días; se conservan siempre `raw_fetches` (url, hash, estado y tamaño), las observaciones versionadas y el normalizado, que permite reprocesar cuando se amplía el parser.

**No se ha borrado ni comprimido nada:** requiere tu autorización.

## 9. Activación gradual (preparada, no ejecutada)

**Cada bloque:**
1. Habilitar las entradas en `config/ingest_scope.json` (cambio versionado).
2. Descubrimiento del bloque: `ingest tick --season 22 --game-type 1` y `ingest work --allow-network --kinds discover_catalog,discover_groups`.
3. Carga básica en tandas: `ingest tick --competition … --skip-catalog` y `ingest work --allow-network --exclude-kinds match_report --max-requests 300`.
4. Validación: `ingest coverage --sport football11` (equipos = plazas, grupos con partidos, clasificación y goleadores `ok`, incidencias), la comprobación de clasificación recalculada desde el calendario de la Fase 2 y la batería de preguntas.
5. Los grupos habilitados entran solos en la programación existente, con la misma cola, el mismo planificador y el mismo patrón temporal.

**Reversión:** `enabled: false` en la entrada. El planificador deja de proponer, el trabajador rechaza lo encolado sin red y `ingest pause` detiene la fuente al instante. Los datos ya cargados se quedan; borrarlos requiere autorización.

| Bloque | Competiciones | Grupos | GET estimados (carga básica, sin actas) |
|---|---|---|---|
| 0 · descubrimiento | catálogo + 12 listas de grupos | 93 | 13 |
| 1 · primeros aficionados | Tercera, PDA Aficionado, Preferente Aficionado | 7 | ≈ 7 × (3 + ~11) ≈ 100 |
| 2 · resto de aficionados | Primera y Segunda Aficionado, Copa RFEF (inactiva), Copa de Aficionados | 34 | ≈ 34 × 3 + 30 × ~11 ≈ 435 |
| 3 · juveniles, datos básicos | Nacional, PDA, Preferente, Primera y Segunda Juvenil (`reports: false`) | 52 | ≈ 52 × (3 + ~11) ≈ 730 |
| 4 · actas | aficionados, según la política autorizada | — | ≈ 3.030 actas jugadas hasta la jornada 10 (tandas con `--max-reports`) |

Total de la carga básica ≈ **1.280 GET**: unos 85 min, en 2–3 noches con tope de 300–600. Las actas de aficionados van aparte (≈ 3.030 + ~320 por semana); las juveniles están bloqueadas.

**Optimización posible, no implementada:** las jornadas pasadas podrían importarse del calendario, que trae marcadores y ya se descarga, en lugar de una petición `/api/results` por jornada. Ahorraría unos 890 GET (−70 % de la carga básica), pero el calendario no trae estado ni marca de acta. Es una decisión para tu revisión.
