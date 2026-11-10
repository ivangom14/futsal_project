# Futsito · Handoff operativo (estado al 2026-11-10)

Referencia para retomar el proyecto en una conversación nueva. Lo que aquí se afirma está comprobado en el código y en las
pruebas; el detalle está en los documentos enlazados. Leer también `CLAUDE.md` y `PROJECT_CONTEXT.md`.

## 1. Qué es y cómo está montado

Asistente sobre el fútbol sala de la RFFM. Monolito modular Python 3.11+ (venv probado en 3.12), PostgreSQL 16, FastAPI, MCP.

```
RFFM ──(ingesta: cola + planificador, proceso aparte)──► PostgreSQL ◄──(solo lectura)── API v1 (:8000) + API v2 (/v2)
                                                                                ▲
                                     agente (web :8001) ── herramientas v1 por MCP stdio · herramientas v2 en proceso
```

| Módulo | Qué hace |
|---|---|
| `ingestion/rffm/` | clientes y lectores de la RFFM; v2: `discovery_v2`, `calendar`, `results_api`, `scope` |
| `ingestion/queue/` | cola `ingest_jobs` (0008), limitador, interruptor, `Fetcher`, planificador y trabajador (T06) |
| `importer/`, `refresh/` | importación idempotente y puertas de la Fase 21 (no degradar finalizados ni capturas buenas) |
| `domain/` | reglas puras: `catalog_classify` (nunca senior por defecto), `dates` (fechas y franjas), `names`, `cards` |
| `api/` (v1) y `api/v2/` | lectura; v2 = consultas sin grupo (jugadores, árbitros, partidos por fecha, clubes, rankings) |
| `agent/` | agente; `AGENT_ARCH=legacy` (por defecto, protegido byte a byte) o `v2` (`agent/v2/`) |
| `evals/` | Futsito-Eval: snapshot con huella, verdad SQL, suites `regression`, `futsito-v1`, `smoke40`; `regrade` sin LLM |

Migraciones: `alembic head = 0008` (verificada ida/vuelta/ida sobre una copia con datos reales). 0007 = catálogo oficial e identidad (clubs, team_entities…); 0008 = cola de ingesta.

## 2. Funcionalidades completadas (commits en `claude/laughing-volta-5ofbhl`)

- **Datos**: sondeo A1 (20 GET) y P0 (51 GET): T22 = 50 competiciones, 89 grupos ([probe-a1.md](probe-a1.md),
  [p0-catalog.md](p0-catalog.md)). Catálogo clasificado con confianza ([data-model.md](data-model.md), Revisión A2).
- **Ingesta preparada, nunca ejecutada**: descubrimiento y lectores (T05/T05b), cola y planificador (T06). Plan de carga P1 ≈ 790
  peticiones ([ingestion-proposal-p1.md](ingestion-proposal-p1.md)).
- **API v2 sin grupo** ([../api.md](../api.md) §v2) y **agente v2, entrega 1** ([agent-v2-slice1.md](agent-v2-slice1.md)):
  7 herramientas, fechas y franjas, «todos/todas» sin repreguntar, premisas verificadas, respaldo determinista ante aclaración
  insistente y «no consta en el acta».
- **Modelos**: pasarela por tarea y adaptador OpenAI (Responses API); precios verificados el 2026-11-09 en
  `config/llm_prices.json`; coste por llamada en la traza ([models.md](models.md)).
- **Evaluación**: ≈ 260 casos (S1–S10, holdout sellado 30 %, 10/10 sellos), regresión histórica de 72 casos
  ([evaluation.md](evaluation.md), [baseline-a3.md](baseline-a3.md)).

## 3. Decisiones técnicas que no hay que reabrir sin evidencia

- Identidad: equipo = `CodEquipo` (estable entre temporadas); club = `codigo_club` de `/fichaequipo`; **el número del escudo no
  identifica al club**. Competición entre temporadas por `CodigoCategoria` (los códigos de competición cambian cada año).
- La RFFM responde 200 con **otra temporada o modalidad** si faltan parámetros: toda página se valida por códigos.
- El calendario no trae estado ni acta ni hora de jornadas futuras: **solo planifica**; los partidos se escriben desde
  `/api/results` a través de `run_import` y las puertas de la Fase 21.
- Cálculos (rankings, recuentos, fechas) en código, nunca en el LLM. Sugerencias de nombres nunca se presentan como coincidencia.
- Fichas de club o equipo con datos personales: **nunca se ingieren**. Datos de menores: no se exponen hasta el permiso de la
  federación (A9). Datos de prueba sin nombres reales (solo las frases literales del responsable).
- Evaluación: verdad solo por SQL; comparaciones solo dentro del mismo snapshot y con 2 pasadas; el LLM nunca juzga la verdad.

## 4. Pruebas y referencias

- Batería offline (pytest, PostgreSQL desechable, LLM y HTTP simulados): **2069 pass, 0 fallos, 35 omitidas** (las omitidas
  necesitan los PDF de reglamento descargados o `RUN_LIVE_LLM=1`). Sin claves de LLM ni de red en el entorno.
- **Baseline real** (snapshot `ref20261109`, dump del responsable, Gemini 3.5 Flash-Lite gratuito, agente `legacy`):
  H (código `ac48edc`) 79,6 % / 81,5 %; C (actual) 90,6 % / 83,6 % → **47/55 tras recalificar C2** con las correcciones del calificador; 0 críticos reales; ruido
  ≈ ±5 casos de 51. 53/65 (26-oct) es histórico **no equivalente**.
- El snapshot y su copia `_head` son de solo lectura (protección por huella). Pruebas exploratorias: `futsal_play_ref20261109`.
- Gasto LLM acumulado: **0 USD** (A5: tope 35 USD).

## 5. Entorno de trabajo (sesión en la nube)

- PostgreSQL desechable: `su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/tmp/futsal_pg -l /var/tmp/futsal_pg/log -o '-p 5432 -k /tmp' start"`.
  **No** usar `service postgresql start` (otro clúster vacío en el mismo puerto).
- `DATABASE_URL=postgresql+psycopg://futsal:change-me@localhost:5432/futsal`; venv `.venv` (3.12).
- Ficheros fuera de Git en `data/`: `eval-snapshots/futsal-ref.dump`, `ref-20261026-212906-results.json`, `eval-runs/a3-*`,
  `raw/rffm/{probe-a1,p0}`. El worktree `/home/user/futsal-ac48edc` reproduce el código histórico.

## 6. Pendiente, por prioridad

1. **Siguiente fase propuesta por el responsable: registro de interacciones del agente** (telemetría/feedback de uso real).
2. Evaluación del agente v2 con Gemini real (gratuito): las 6 preguntas reales y S10 ×2 frente a C1/C2. Criterio: S10 ≥ 80 %,
   0 críticos, sin empeoramientos estables en `regression`.
3. Ingesta: P1 (≈ 790 GET, ~40 min) → requiere autorización; después P2 actas senior, P3 clubes, histórico (cada uno aparte).
   Cola y planificador listos (T06): `ingest plan|tick|work|status|pause|resume|recover|cancel`. Red real solo con
   `--allow-network` **y** `FUTSAL_RFFM_NETWORK_AUTHORIZED=1`. Plan real sobre 25 grupos: 235 trabajos (≈ 770 peticiones
   escalado a 89 grupos). Pendientes de T06: reconciliación calendario/results como incidencia (P1 §6), regla «acta sin
   cerrar» (no se guarda `acta_cerrada`), rachas del interruptor solo en memoria, alerta al pasar a `dead`.
4. Agente v2 entrega 2: resolutor y router (T0/T1/T2), validador de respuestas, estado conversacional en PG.
5. A8 reglamento: embeddings locales + búsqueda híbrida y documentos RFFM (caso «saque del portero» sin resolver).
6. A5 comparativa de modelos (≈ 23 USD previstos). A6 despliegue VPS. A7 límites y privacidad antes de abrir al público.
7. Retirada del agente legacy y sus flags solo cuando v2 ≥ legacy en todos los estratos.

## 7. Autorizaciones

| Sin pedir permiso (offline) | Requiere autorización expresa del responsable |
|---|---|
| código y pruebas offline, revisión, documentación | commit y push (siempre se presenta antes) |
| evaluaciones sin LLM (`plan`, `regrade`) | cualquier petición real a la RFFM (P1, P2, P3, histórico) |
| PostgreSQL desechable y copias exploratorias | llamadas LLM (incluso gratuitas: se anuncian); cualquier gasto |
| | merge a `main`, despliegue, publicación, contratación |
| | retirar legacy o flags; datos personales o de menores |

## 8. Próximo paso recomendado

Iniciar la fase de **registro de interacciones** sobre el agente v2 (el responsable ya lo está probando en su front con
`AGENT_ARCH=v2`), y en paralelo lanzar la evaluación v2 con Gemini gratuito para tener la medida antes de cambiar nada más.

## 9. Cierre de la fase «tareas pendientes» (2026-11-10)

- **Tarea 3 (calificador)**: cifras dentro de denominaciones del catálogo («Grupo 3», «… ZONA 3») no cuentan como invención si
  la denominación existe; «Grupo 7» inexistente sí. `recuento_global` acepta total − jugados solo si la diferencia de dos
  `count` coincide y la respuesta da el número correcto. `regrade` por capas (`layers`) y completo (`full`, ejecuciones nuevas).
  Recalificación de C2 sin LLM: 2 cambios esperados, 46/55 → 47/55, equivalencia 48/48 con las correcciones desactivadas.
- **Tarea 4 (agente v2)**: respaldo determinista conservador tras 2 empujones (fechas, goleadores por categoría, club no
  ambiguo, partidos de un árbitro; respuesta solo con datos de la herramienta) y `NOT_IN_REPORT` para sustituciones, descanso,
  tiempo añadido y marcador tras cada gol. Legacy intacto. Pendiente: los empujones solo se activan con «todos» o aclaración previa.
- **T06 (cola y planificador)**: migración 0008, cola idempotente con arrendamiento y reanudación, limitador 1 petición/3 s,
  interruptor (429/403, 5xx×3, null×2, un `scope_mismatch` por códigos; nombres distintos = incidencia de calidad), planificador
  por calendario configurable (`config/ingest_schedule.json`), horas desde `/api/results`, escritura vía `run_import` con las
  puertas de la Fase 21. Agente y cola no se importan entre sí.
- **Incidente detectado en la revisión**: la puerta de red aceptaba una sola llave; corregida por Opus (dos llaves) antes de
  cualquier petición (0 peticiones reales comprobadas).
