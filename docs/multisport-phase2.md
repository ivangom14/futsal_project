# Multideporte · Fase 2: primera integración real de fútbol 11 (piloto)

Estado: implementada, sin commit (Fase 1 = `a8f3162`). Autorizadas hasta 25 GET a la RFFM: **25 hechas, todas con HTTP 200**. 0 llamadas a LLM de pago. Alcance operativo de fútbol 11 **deshabilitado**: no se han tocado la programación ni los límites.

## 1. Las 12 competiciones (catálogo T22 de fútbol 11, `tipojuego=1`, consultado el 2026-11-20)

Todas son `codigo_tipo_juego=1` (football11) y de la delegación CENTRAL R.F.F.M. Los 12 códigos casan con `config/ingest_scope.json` por `CodigoCategoria` y nombre; las otras 31 competiciones del catálogo (43 en total) quedan fuera de alcance.

| # | Código | Competición | Categoría oficial (CodigoCategoria) | Grupo de categoría | Formato | Activa | Grupos | Equipos | Equipos/grupo | Jornadas/grupo |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 26738289 | COPA RFEF FASE AUTONÓMICA | TERCERA FEDERACION (40050) | CATEGORIA NACIONAL | liga / grupos | no | 3 | 10 | 2–4 | 1–3 |
| 2 | 26738226 | TERCERA FEDERACIÓN RFEF | TERCERA FEDERACION (40050) | CATEGORIA NACIONAL | liga / grupos | sí | 1 | 18 | 18 | 34 |
| 3 | 28733581 | COPA DE AFICIONADOS RFFM 2026/2027 | PRIMERA DIVISION AUTONOMICA AFICIONADO (40053) | AFICIONADOS | eliminatoria | sí | 1 | 156 | 156 | 1 |
| 4 | 26737698 | PRIMERA DIVISIÓN AUTONÓMICA AFICIONADO | PRIMERA DIVISION AUTONOMICA AFICIONADO (40053) | AFICIONADOS | liga / grupos | sí | 2 | 36 | 18 | 34 |
| 5 | 26737701 | PREFERENTE AFICIONADO | PREFERENTE AFICIONADO (40054) | AFICIONADOS | liga / grupos | sí | 4 | 72 | 18 | 34 |
| 6 | 26737706 | PRIMERA AFICIONADO | PRIMERA AFICIONADO (40055) | AFICIONADOS | liga / grupos | sí | 8 | 144 | 18 | 34 |
| 7 | 26738300 | SEGUNDA AFICIONADO | SEGUNDA AFICIONADO (40056) | AFICIONADOS | liga / grupos | sí | 22 | 342 | 14–16 | 26–30 |
| 8 | 26738130 | NACIONAL JUVENIL | LIGA NACIONAL JUVENIL (40059) | JUVENILES | liga / grupos | sí | 1 | 18 | 18 | 34 |
| 9 | 26737715 | PRIMERA DIVISIÓN AUTONÓMICA JUVENIL | PRIMERA DIVISION AUTONOMICA JUVENIL (40060) | JUVENILES | liga / grupos | sí | 4 | 72 | 18 | 34 |
| 10 | 26737718 | PREFERENTE JUVENIL | PREFERENTE JUVENIL (40061) | JUVENILES | liga / grupos | sí | 8 | 143 | 17–18 | 34 |
| 11 | 26737724 | PRIMERA JUVENIL | PRIMERA JUVENIL (40062) | JUVENILES | liga / grupos | sí | 17 | 303 | 16–18 | 34 |
| 12 | 26738323 | SEGUNDA JUVENIL | SEGUNDA JUVENIL (40063) | JUVENILES | liga / grupos | sí | 22 | 313 | 12–16 | 22–30 |
| | | **Total** | | | | | **93** | **1627** | | |


Observaciones:
- La COPA RFEF FASE AUTONÓMICA está inactiva (`Activa=0`). Tiene 3 grupos de 2–4 equipos y 1–3 jornadas, y formato oficial 2 («grupos»).
- La Copa de Aficionados es una eliminatoria de 156 inscritos en un solo «grupo».
- Juveniles: 5 competiciones, 52 grupos y 849 equipos (menores; ver §6).

## 2. Peticiones reales (registradas en `raw_fetches` de la BD aislada)

| # | Endpoint | Finalidad | Resultado | Bytes |
|---|---|---|---|---|
| 1 | `/api/competitions?temporada=22&tipojuego=1` | catálogo F11 (job `discover_catalog`) | 200 | 22.417 |
| 2 | `/api/groups?competicion=26737701` | grupos de Preferente Aficionado (job `discover_groups`) | 200 | 881 |
| 3–13 | `/api/groups?competicion=…` (×11) | metadatos de las otras 11 (Fetcher de la cola, sin importar) | 200 | 19.620 |
| 14 | `/competicion/goleadores?…grupo=26737702&tipojuego=1` | tabla oficial de goleadores (job `scorers`) | 200 | 334.716 |
| 15 | `/competicion/clasificaciones?…` | clasificación oficial (job `standings`) | 200 | 299.345 |
| 16 | `/competicion/calendario?…` | calendario completo, 34 jornadas (job `calendar`) | 200 | 427.910 |
| 17–19, 23 | `/api/results?idGroup=26737702&round=9…12` | jornadas 9–12 (job `round_results`) | 200 | ~11.000 c/u |
| 20–22, 24–25 | `/acta-partido/<codacta>` (×5) | actas (job `match_report`) | 200 | ~255.000–271.000 c/u |

Total: 25 GET, 2,49 MB, latencia media 946 ms y máxima 2,5 s, de 16:35:39 a 16:38:12 UTC (2,5 min con el ritmo de 3 s). **Incidencia:** se descargaron 5 actas en lugar del máximo de 3 autorizado. Los resultados de cada jornada encolan sus actas con más prioridad que las jornadas siguientes; el total siguió en 25 GET. Para la Fase 3: excluir `match_report` de forma explícita cuando haya un tope de actas.

## 3. Diferencias verificadas fútbol sala ↔ fútbol 11

| Aspecto | Resultado |
|---|---|
| `/api/results` | **Misma estructura** (57 claves idénticas); el parser actual funciona sin cambios. |
| Calendario, clasificación y goleadores | Mismos parsers; clasificación 18/18 filas y goleadores 135 filas `ok`. |
| Acta | Mismas claves más `foto_*` (técnicos y delegados, no se ingieren). **11 titulares** y 6–7 suplentes por equipo, 1–2 porteros. Árbitro y 2 asistentes (en FS: árbitros y cronometrador). |
| Sustituciones | **Están en el acta de F11** (`sustituciones_equipo_*`: entra, sale, dorsales, minuto), no en la de FS. Aún no se interpretan: queda la incidencia `report_section_not_parsed`, y la API y el agente dicen «figuran en el acta pero aún no incorporadas» (nunca «no consta»). |
| Otros técnicos | `otros_tecnicos_*` (nombre, tipo): no interpretados (incidencia). |
| Esquema táctico | `esquema_local/visitante` **declarado** en el acta (p. ej. 1-4-3-3); la ingesta ya lo guardaba (`home/away_formation`). Las **posiciones individuales llegan vacías** en ambos deportes: no se asignan jugadores a posiciones. |
| Tarjetas | Mismos códigos (100 amarilla, 101 segunda amarilla/roja, según la Fase 25.4). |
| Acta vacía | 5589202: `/api/results` la da por cerrada, pero la página devuelve `game = null` → `unreadable` (no se inventa nada). |
| Identificadores | 0 `CodEquipo` comunes (64 FS frente a 18 F11 en local), 0 `codacta` comunes (238 frente a 306), 0 jugadores comunes. Los códigos de competición y grupo son de una única secuencia. Muestra limitada: la guarda `sport_conflict` y la identidad de equipo por deporte siguen siendo necesarias. |

## 4. Piloto: Preferente Aficionado · Grupo 1 (`26737702`) en la BD aislada `futsito_f2_pilot`

Importado con la cola, el planificador y el worker existentes, con el alcance aislado (`examples/ingest_scope.pilot-preferente-aficionado-g1.json`, vía `FUTSAL_INGEST_SCOPE`):

- 1 competición (football11, tipojuego 1), 4 grupos (metadatos), 18 equipos, 4 jornadas (9–12) y 36 partidos (18 finalizados y 18 programados).
- Clasificación oficial de 18 filas y 135 goleadores.
- 4 actas: 142 jugadores en acta, 47 eventos, 12 árbitros y 24 técnicos.
- 14 incidencias, todas `report_section_not_parsed` (sustituciones y otros técnicos).

**Comprobación frente a la RFFM** (instantáneas oficiales):

- 36/36 marcadores coinciden con el calendario.
- Clasificación oficial: 18/18 filas coinciden con la recalculada desde los 90 resultados del calendario (J, G, E, P, GF, GC y puntos = 3G+E).
- 4/4 actas coinciden con el marcador.
- La tabla de goleadores suma 286 goles frente a 295 marcados. La diferencia (9) coincidiría con goles en propia meta, que no suman a ningún goleador, pero **no está verificada**.

**Idempotencia:** al reimportar las 4 jornadas y las 4 actas hay 0 altas y 0 actualizaciones, el mismo contenido (hash) y ninguna observación nueva.

## 5. Cambios (sobre la Fase 1)

- **Ingesta:** `FUTSAL_INGEST_SCOPE`, que permite un alcance aislado solo para ejecuciones manuales (la programación no la define), y restricción opcional por `groups` en una entrada del alcance, aplicada en `discover_groups`, el planificador y el worker.
- **API:** el acta indica `not_in_source` según el deporte y `not_ingested` (F11: sustituciones).
- **Agente:**
  - Avisos de fuera de ámbito y prompt por deporte (fútbol sala: byte a byte igual).
  - Textos de revisión con el deporte.
  - «No existe en la base» calculado contra el catálogo activo, de modo que «segunda», «nacional» y «liga nacional» existen en F11.
  - Las menciones de deporte («de fútbol», «futsal», «F11») se quitan antes de resolver la competición.
  - Si se menciona otro deporte, respuesta determinista sin LLM con `sport_switch`.
  - Sustituciones de F11: «figuran pero aún no incorporadas».
- **Regresión detectada y corregida:** la propuesta de cambio de deporte respondía también a «¿Quién ganó el último Mundial de fútbol 11?» (caso `s6_fuera_de_alcance_futbol` de Futsito-Eval). Ahora solo se propone si la pregunta trata de competiciones (vocabulario de competición, palabras del catálogo activo, categoría o tipo, o un seguimiento corto en una conversación en marcha). El resto sigue el flujo normal de rechazo.
- **Visual de alineación:** en F11 es una lista con el esquema declarado (`sport: football11`).
- **Frontend:** lista de titulares y suplentes en F11 (nunca la pista de 5), botón «Cambiar a … y preguntar» que repite la pregunta en una conversación del otro deporte, y sugerencias de Preferente Aficionado.
- **Fixtures** `tests/fixtures/rffm_f11/`: anonimizadas (0 de 235 nombres reales, 0 de 113 códigos de jugador y 0 fotos) y recortadas a `__NEXT_DATA__`.

## 6. Menores

- Se mantiene la política de fútbol sala juvenil: las actas guardan nombre y dorsal, sin datos de contacto ni fotos.
- No se ha descargado ninguna acta juvenil; el piloto es de aficionados.
- Las 4.170 actas juveniles jugadas (estimación hasta la jornada 10) requieren tu decisión antes de la Fase 3.

## 7. Carga (medido en el piloto, extrapolado a las 12)

**Medido por grupo:**
- Calendario: 428 KB.
- Clasificación: 299 KB.
- Goleadores: 335 KB.
- Una jornada de `/api/results`: 11 KB.
- Un acta: ~260 KB de HTML, de los que solo ~17 KB son el JSON útil.
- Latencia media: ~1 s; ritmo: 3 s.

**Volumen de temporada (catálogo real):** 89 grupos de liga, 22.872 partidos de liga y 2.798 jornadas, más 4 grupos de copa.

**Carga inicial [E]:**
- 93 × (calendario + 2 tablas + ~12 jornadas) ≈ 1.400 GET.
- Actas jugadas hasta la jornada 10: ≈ 3.030 de aficionados y ≈ 4.170 juveniles.
- Total ≈ 8.600 GET, unas 9–10 h a 3–4 s por petición, en tandas con el tope de 900.
- Instantáneas en bruto ≈ 1,9 GB solo de actas (el 94 % es HTML de la página).

**Régimen semanal [E] por grupo** (cadencias de `config/ingest_schedule.json`):
- Calendario diario: 7.
- Tablas con actividad: ~8.
- Resultados en juego, asentamiento y próxima jornada: ~25–35.
- Actas: ~9.
- Total ≈ 50–60 por grupo, es decir, ≈ 4.600–5.600 por semana para las 12. Similar al volumen de fútbol sala (89 grupos): duplica el total.

**Límite de 900 por ejecución:**
- La capacidad semanal teórica (5 × 900 + 48 × 900) sobra.
- El riesgo está en las **horas punta** del fin de semana (mañanas de domingo, con juveniles de F11 y FS a la vez), donde la demanda estimada (~1.000/h) supera tanto el tope de 900 como el máximo de ~1.200/h que permite el ritmo de 3 s. Lo que sobra pasa a la hora siguiente: pierde frescura, no fiabilidad.
- El lunes a las 03:00 se acumulan las actas de los dos deportes.
- Alternativas, a medir en la Fase 3 antes de tocar nada:
  1. Prioridades: en juego y actas antes que calendarios.
  2. Calendario semanal en lugar de diario para grupos sin cambios.
  3. Tope de 1.500 el lunes.
  4. Guardar solo el JSON de las páginas (~94 % menos disco).

  Ninguna está aplicada.

## 8. Reproducir el piloto (en una BD de pruebas; requiere autorización, ≈45 GET para el grupo completo)

```
export DATABASE_URL=postgresql+psycopg://…/futsito_pruebas FUTSAL_INGEST_SCOPE=examples/ingest_scope.pilot-preferente-aficionado-g1.json
alembic upgrade head
python -m futsal.cli ingest tick --season 22 --game-type 1           # catálogo F11
FUTSAL_RFFM_NETWORK_AUTHORIZED=1 python -m futsal.cli ingest work --allow-network --until-empty --max-requests 2
python -m futsal.cli ingest tick --season 22 --game-type 1 --skip-catalog
FUTSAL_RFFM_NETWORK_AUTHORIZED=1 python -m futsal.cli ingest work --allow-network --until-empty --max-requests 60
```
