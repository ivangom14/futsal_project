# Multideporte · Fase 3E: activación real del fútbol 11 de Madrid (2026-11-21)

Estado: **carga básica real hecha y alcance operativo activado; cobertura NO completa al 100 %** (ver §3). 1.293/1.293 peticiones
autorizadas consumidas, **0 actas**, 0 llamadas a LLM de pago, sin commit.

## 0. Entorno (importante)

- La sesión corre en un contenedor en la nube **sin acceso a tu servidor** (ni systemd ni la BD real de producción).
- «Entorno operativo» de esta fase = PostgreSQL 16 local de la sesión (`futsal`), creado con la referencia de fútbol sala
  `data/eval-snapshots/futsal-ref.dump` (14 competiciones, 25 grupos, 3.445 partidos, 571 actas), migrado 0006 → **0010**,
  y cargado con datos REALES de F11 de la RFFM.
- Los cambios operativos (alcance y temporizador) están en el repositorio; **se aplican en tu servidor al desplegar**
  (`git pull` + `systemctl daemon-reload`).
- Las instantáneas descargadas (`data/raw/rffm/queue/2026-11-21`, 85 MB) y esta BD son efímeras: no están en Git.
- Al desplegar en producción, tu cola hará su propia carga (≈1.300 GET en 2–3 noches, con la programación compartida).

## 1. Cambios

| Ámbito | Cambio |
|---|---|
| `config/ingest_scope.json` | F11 `enabled: true`; las 12 entradas `enabled: true` y `reports: false`; juveniles además `scorers: false` |
| `ingest_scope.py` | Política `scorers` por entrada (`allows_scorers`, `restricts_scorers`); `gate_target(..., kind=)` rechaza goleadores sin red |
| `planner.py` | `has_scorers=False` si la política lo prohíbe; **defecto corregido**: el tipo de juego de cada grupo sale de su competición o deporte, nunca del argumento (`tick --game-type 1` etiquetaba como F11 los grupos antiguos de FS sin código) |
| `handlers.py` | `round_results` no encola goleadores prohibidos |
| `jobs.py`, `worker.py` | `ingest work --competition` (lote por competición; el resto sigue `pending`) |
| `repositories/table_queries.py`, `v2_queries.py` | Goleadores de menores: estado `restricted` sin nombres (defensa en profundidad) y excluidos de los rankings v2 |
| `ingest_commands.py` | `ingest coverage` añade jornadas (grupo × jornada) |
| `deploy/systemd/futsito-update-weekday.timer` | 2.ª pasada L-V a las **05:00** (mismo servicio, script, tope 900 y 58 min) |
| Pruebas | Nuevas: `tests/integration/test_football11_activation_pg.py` (6) y `tests/test_shared_schedule.py` (3) |
| Pruebas adaptadas al nuevo alcance | Ver detalle abajo (incluye `test_multisport_unit.py`: las 12 pasan de deshabilitadas a habilitadas) |

Pruebas adaptadas al nuevo alcance (explicado en cada una):
- `test_football11_pilot_pg.py`: el piloto declara sus actas autorizadas; la prueba «F11 deshabilitado» pasa a «fuera de
  alcance rechazado».
- `test_football11_coverage_pg.py`: simula la futura autorización de actas de aficionados.
- `test_multisport_pg.py`: el mensaje de rechazo ya no nombra el deporte.

Sin migraciones nuevas (0010 era la última).

**Reversión:**
- Alcance: `enabled: false` en el deporte o en la entrada. Estado anterior: las 12 `enabled: false`, juveniles `reports: false`,
  sin `scorers`.
- Temporizador: borrar la línea `OnCalendar=Mon..Fri *-*-* 05:00:00 Europe/Madrid` y `systemctl daemon-reload`.
- `ingest pause` detiene la fuente al instante.

## 2. Consumo de la RFFM

| Bloque | GET | Detalle |
|---|---|---|
| Comprobación de red | 1 | HEAD a la portada (no es dato; contado igualmente) |
| A · descubrimiento | 13 | 1 catálogo + 12 listas de grupos |
| B · aficionados y nacional (7) | 544 | Calendarios 41, jornadas, tablas, goleadores; tope del bloque alcanzado |
| C · calendarios juveniles | 52 | |
| B + C combinado (tope global) | 683 | Jornadas y clasificaciones por prioridad del planificador, sin goleadores |
| **Total** | **1.293** | 93,7 MB descargados |

Por tipo de dato:

| Tipo | GET |
|---|---|
| `/api/results` (jornadas) | 1.022 |
| Clasificaciones | 126 |
| Calendarios | 93 |
| Goleadores | 38 |
| Grupos | 12 |
| Catálogo | 1 |
| Actas | **0** |

**Reintentos:** 108 respuestas `null` de `/api/results` (HTTP 200 con cuerpo `null`, intermitente: ≈10 %, en 12 grupos;
Preferente Aficionado grupo 3, 8 de 12). Todas contadas en el presupuesto. Cada 2 nulos seguidos el interruptor corta la pasada
(`source_unavailable`, sin pausar) y el bucle reanudaba; se hizo además una pausa manual de 10 min por prudencia.

**Ineficiencia observada:** cada jornada importada con partidos nuevos vuelve a encolar las tablas de su grupo. En la carga
inicial se pidieron 126 clasificaciones para 89 grupos (≈34 de más).

**Pendiente en la cola** (`pending`/`failed`; lo tomará la programación):
- 46 jornadas, casi todas antiguas (1–9), sobre todo juveniles.
- 62 clasificaciones (refrescos).
- 40 tablas de goleadores de aficionados.
- Calendarios diarios.

Sin descargas de actas:
- `--max-reports 0` y `--exclude-kinds match_report` en todas las ejecuciones.
- `reports=false` en el alcance.
- 0 URLs de acta en `raw_fetches`.

## 3. Cobertura real (`ingest coverage --sport football11`, 21-11-2026 12:40)

| Competición | Grupos | Con partidos | Equipos/plazas | Jornadas (g×j) | Partidos | Finalizados | Clasif. ok | Goleadores ok | Estado |
|---|---|---|---|---|---|---|---|---|---|
| Tercera Federación | 1 | 1 | 18/18 | 14 | 126 | 99 | 1 | 1 | **completa** |
| PDA Aficionado | 2 | 2 | 36/36 | 24 | 216 | 179 | 2 | 2 | **completa** |
| Preferente Aficionado | 4 | 4 | 72/72 | 47 | 423 | 351 | 4 | 4 | casi (9 partidos pasados pendientes, 1 grupo) |
| Primera Aficionado | 8 | 8 | 144/144 | 95 | 855 | 704 | 8 | 8 | casi (9 pend., 1 grupo) |
| Segunda Aficionado | 22 | 22 | 348/342¹ | 214 | 1.696 | 1.274 | 22 | 22 | casi (16 pend., 2 grupos) |
| Nacional Juvenil | 1 | 1 | 18/18 | 10 | 90 | 71 | 1 | — (política) | parcial (18 pend.) |
| PDA Juvenil | 4 | 4 | 72/72 | 46 | 414 | 341 | 4 | — | parcial (18 pend., 2 grupos) |
| Preferente Juvenil | 8 | 8 | 144/143¹ | 90 | 810 | 653 | 8 | — | parcial (53 pend., 4 grupos) |
| Primera Juvenil | 17 | 17 | 306/303¹ | 190 | 1.710 | 1.370 | 17 | — | parcial (121 pend., 10 grupos) |
| Segunda Juvenil | 22 | 22 | 330/313¹ | 184 | 1.398 | 943 | 22 | — | parcial (119 pend., 5 grupos) |
| Copa RFEF Fase Aut. (inactiva) | 3 | 0 | 0/10 | 0 | 0 | 0 | — | — | **sin resultados**² |
| Copa de Aficionados (inactiva, eliminatoria) | 1 | 0 | 0/156 | 0 | 0 | 0 | n/a | — | **sin resultados**² |
| **Total** | **93** | **89** | 1.488 | 914 | 7.738 | 5.985 | **89/89 ligas** | 37/37 aficionados | |

¹ Las plazas de más son pseudoequipos «Descansa» (IDs negativos, jornada de descanso en grupos impares), igual que en fútbol sala.

² El catálogo oficial marca las dos copas como inactivas. Su calendario sí se descargó (13 y 78 partidos), pero el planificador,
por diseño, **nunca pide resultados de competiciones inactivas** (solo calendario y tablas finales). Requiere decisión (§7).

Partidos pasados del calendario oficial que faltan en la BD:
- Ligas: 363 de ≈6.545 (5,5 %), en 26 de 89 grupos, todos de las 46 jornadas pendientes.
- Copas: 91.

**Integridad (BD):**
- 0 `codacta` duplicados.
- 0 partidos con equipos de otro grupo.
- 0 jornadas cruzadas.
- 0 IDs compartidos entre deportes.
- 0 incidencias de calidad nuevas.
- 0 finalizados sin marcador.

10 partidos de Segunda Aficionado con resultado administrativo 3-0/0-3 (algunos con fecha futura): se guardan tal cual, con estado
`unknown`.

**Fútbol sala:** huella (recuentos y md5 de competiciones, grupos, equipos, partidos, actas, eventos, tablas) **idéntica**
antes y después.

## 4. Datos de menores (juveniles de F11)

| Dato | Política |
|---|---|
| Grupos, equipos, calendario, resultados, clasificación oficial | Sí |
| Tabla oficial de goleadores (nombres) | `scorers=false` |
| Actas | Bloqueadas (`reports=false`) |

`scorers=false` se aplica en cuatro puntos:
- No se planifica: verificado con datos reales, el `tick` de los juveniles planificó 52 clasificaciones y 0 goleadores.
- No se encola desde `round_results`.
- El trabajador lo rechaza sin red.
- La API responde `restricted`, sin nombres, aunque existiera tabla guardada; además, queda fuera de `/v2/rankings/scorers`.

Agregado disponible: goles a favor y en contra por equipo en la clasificación oficial.

Fútbol sala juvenil, sin cambios: `futsal` es `all` y no tiene la política.

## 5. Actualizaciones: medición y temporizador compartido

**Medición real:** 3,22 s por petición (ritmo de 3 s más una latencia media de 1,7 s solapada). En 58 min caben ≈1.080 peticiones,
así que manda el tope de 900. La Fase 3 suponía ~4 s.

**Simulación** con el planificador real (`plan`/`collapse`):
- F11 con su estado REAL: calendarios y horarios recién descargados, sin actas por política.
- FS modelado: 25 grupos reales de la referencia, desplazados +14 días y replicados hasta sus 85 grupos.
- Dos semanas de calentamiento y la semana del 23 al 29-11 medida.

| Escenario | GET/semana (FS + F11) | Máx. por ejecución | Atraso L-V |
|---|---|---|---|
| Solo FS (4,0 s) | 3.485 | 447 | 0 |
| FS+F11, solo 03:00 (3,22 s) | 8.202 (3.416 + 4.786) | 900 | **135 (lun), 73 (mié)** |
| FS+F11, solo 03:00 (4,0 s) | 8.189 | 870 (58 min) | 170 / 73 |
| **FS+F11 + 05:00 (3,22 s)** | 9.266 (3.803 + 5.463) | 890 | **0** |
| FS+F11 + 05:00 (4,0 s) | 9.301 | 870 | 30 a las 03:00, 0 tras las 05:00 |

**Decisión aplicada:** 2.ª ejecución L-V a las 05:00.
- La medición confirma la simulación de la Fase 3: con un solo pase hay atraso; con la 2.ª pasada, ninguno.
- Mismo servicio y script, sin servicio de F11.
- Tope de 900 y `TimeoutStartSec=58min` sin cambios.
- La pasada de las 03:00 dura como mucho 58 min, así que no se solapa con la de las 05:00 (además, `flock` y el candado de PG).

**Fines de semana:** nunca saturan.

**Impacto en FS:** sus trabajos se atienden por prioridad junto a los de F11, sin perder pasadas. Con la 05:00, FS hace más
revisiones (3.803 frente a 3.416).

**Límites del modelo:**
- FS no es el real de producción.
- Sin actas de F11.
- Partidos que terminan a su hora.

## 6. API, MCP, agente y frontend (sin LLM de pago)

Circuito real:

> PostgreSQL de la sesión → FastAPI (uvicorn) → MCP stdio → Agente

- La comprensión es determinista.
- El modelo es simulado y obediente (`understanding_eval.SimLLM`).
- Script: `circuit_check.py` del scratchpad.

| Consulta | Resultado |
|---|---|
| ¿Cómo va Preferente Aficionado de fútbol? | Pregunta el grupo (4 opciones oficiales); no elige |
| ¿Qué grupos tiene Segunda Aficionado? | Lista los 22 grupos, pero como pregunta de aclaración (mejorable: debería responder directamente) |
| ¿Quién lidera Primera Juvenil? | Pregunta el grupo (17) |
| ¿Qué partidos de fútbol hay este fin de semana? | `matches_by_date` solo F11: 741 partidos, paginados de 10 en 10 (`next_offset`) |
| Resultados / clasificación de Tercera Federación | `list_matches` y `get_standings` del grupo 7 |
| Próximo partido del líder · ¿Y su último resultado? | Contexto conservado (mismo grupo) |
| ¿Qué equipos participan en Nacional Juvenil? | `list_teams` |
| Máximos goleadores de Preferente Aficionado G1 | Tabla oficial |
| Máximos goleadores de Nacional Juvenil | API `restricted`, sin nombres (el modelo simulado no cubre ese estado; con un LLM real, pendiente) |
| ¿Cómo va Preferente Aficionado de fútbol sala? (con Fútbol) | `otro_deporte`: cambio de deporte, sin datos |
| Lo mismo con Fútbol sala | «PREFERENTE AFICIONADO FS», sus 2 grupos |
| ¿Qué partidos de fútbol hay este fin de semana? (con Fútbol sala) | `otro_deporte` |
| ¿Cómo va la Copa de Aficionados? | `not_ingested`: no inventa, pero no hay datos (§3 ²) |

**Comprensión de FS** (`understanding_eval`, 259 turnos): **idéntica** al commit `88ef142` (100 %, 0 errores críticos,
4.067 tokens estimados por turno).

**Frontend:** sin cambios; Vitest 156/156. El selector y el historial ya filtran por deporte. Con `restricted`, la lista de
goleadores llega vacía y no se pinta ninguna tarjeta.

**Pendiente:** respuestas con un LLM real (Haiku) y la batería del navegador.

## 7. Siguientes pasos y autorizaciones necesarias

| # | Paso | Coste estimado | Autorización |
|---|---|---|---|
| 1 | Completar la carga: 46 jornadas, 62 clasificaciones y 40 goleadores. Lo hace sola la programación compartida al desplegar | ≈150–170 GET (con ≈10 % de nulos) | Ya cubierto por la programación, al desplegar en tu servidor |
| 2 | Resultados de las 2 copas inactivas | ≈8 GET | Sí: cambio del planificador («inactiva = cada jornada una vez»), que también afecta a FS |
| 3 | Actas de F11 de aficionados (solo `acta_cerrada=1`) | ≈3.000 la primera vez, +≈320 por semana | Sí, con la retención (punto 4) |
| 4 | Almacenamiento: solo `__NEXT_DATA__` comprimido + hash, sin `raw.html` duplicado, 30 días de bruto (Fase 3 §8) | 0 GET | Sí: borra o comprime instantáneas |
| 5 | Mejora del agente: «¿qué grupos tiene X?» como respuesta directa | 0 GET | No (código) |
| 6 | Mejora de ingesta: diferir las tablas al final de una carga inicial | ≈−30 GET por carga | No (código) |
| 7 | Validación con LLM real y en el navegador (`docs/multisport-phase2-preguntas.md` + §6) | Coste de LLM | Sí |

Actas históricas: solo las cerradas, en tandas nocturnas con `--max-reports`, después de aplicar el almacenamiento comprimido;
nunca las juveniles.
