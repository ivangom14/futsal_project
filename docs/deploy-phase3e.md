# Despliegue de la Fase 3E en el servidor real (fútbol 11 + temporizador compartido)

Procedimiento para desplegar el commit `74c88a5` (rama `claude/laughing-volta-5ofbhl`) en el servidor que ya ejecuta Futsito.

## Supuestos sobre el entorno

Salen del repositorio; ajústalos donde no coincidan.

| Elemento | Valor según el repo | Dónde |
|---|---|---|
| Sistema | Ubuntu con systemd | `deploy/systemd/` |
| Repositorio | `/opt/futsito`, usuario y grupo `futsito` | `futsito-update.service` |
| Variables de la actualización | `/etc/futsito/update.env` | Ídem |
| PostgreSQL | 16.4 en Docker Compose (`db`, volumen `pgdata`) en `127.0.0.1:5432` | `docker-compose.yml` |
| Python | `.venv/bin/python` del repo | `scripts/update_rffm.sh` |
| API de datos | `uvicorn futsal.api.app:app_factory --factory --port 8000` | README |
| Chat (agente + MCP) | `uvicorn futsal.agent.web:app_factory --factory --port 8001`; arranca el servidor MCP por stdio y este llama a la API (`API_BASE_URL`) | `agent/web.py` |
| Frontend «Pista» | `npm run build` → `frontend/dist`; apunta al chat con `VITE_CHAT_API_URL` (por defecto `http://127.0.0.1:8001`) | `frontend/src/api.ts` |

**Sin unidades en el repo.** No hay unidades systemd para la API ni para el chat. Donde pone `<reiniciar API>`,
`<reiniciar chat>` y `<publicar frontend>`, usa tu método actual (systemd propio, tmux, supervisor…).

**Sin copia de seguridad.** La excluiste de este procedimiento. Consecuencia: una vez cargado fútbol 11, **no se puede bajar de la
migración 0009** (ver §10). La reversión es siempre de código y de alcance, nunca de esquema.

## 0. Ventana y línea base (5 min)

**Cuándo desplegar:** de lunes a jueves, por la mañana, por ejemplo el **lunes 23-11 a partir de las 09:00**.
- La carga inicial de fútbol 11 (≈1.300 GET + ≈10 % de nulos) debe terminar antes del fin de semana (viernes 16:00), cuando
  fútbol sala tiene partidos en juego y la máxima prioridad.
- Así no se pisa con la pasada de las 03:00 ni con la de las 05:00.

Con el usuario `futsito` en `/opt/futsito`:

```bash
cd /opt/futsito && set -a && . /etc/futsito/update.env && set +a
```

```bash
mkdir -p data/deploy-3e && git rev-parse HEAD > data/deploy-3e/commit_anterior.txt
```

```bash
.venv/bin/python -m alembic current | tee data/deploy-3e/alembic_antes.txt
```

```bash
.venv/bin/python -m futsal.cli ingest status > data/deploy-3e/status_antes.json
```

```bash
.venv/bin/python -m futsal.cli ingest coverage --sport futsal --json > data/deploy-3e/coverage_fs_antes.json
```

Huella de fútbol sala (recuentos y md5 por tabla): la misma consulta se repite en §8 y debe coincidir, salvo lo que la propia
programación actualice entre medias.

```bash
psql "${DATABASE_URL/+psycopg/}" -At -f - > data/deploy-3e/huella_fs_antes.txt <<'SQL'
select 'competitions', count(*) from competitions where modality='futsal';
select 'groups', count(*) from competition_groups g join competitions c on c.id=g.competition_id where c.modality='futsal';
select 'teams', count(*) from teams t join competition_groups g on g.id=t.group_id join competitions c on c.id=g.competition_id where c.modality='futsal';
select 'matches', count(*), count(*) filter (where m.status='finished') from matches m join competition_groups g on g.id=m.group_id join competitions c on c.id=g.competition_id where c.modality='futsal';
select 'reports', count(*) from match_reports r join matches m on m.id=r.match_id join competition_groups g on g.id=m.group_id join competitions c on c.id=g.competition_id where c.modality='futsal';
SQL
```

## 1. Parar la programación durante el despliegue

```bash
sudo systemctl stop futsito-update-weekday.timer futsito-update-weekend.timer
```

```bash
systemctl is-active futsito-update.service
```

Si sale `active`, espera a que termine: SIGTERM no corta transacciones; o `sudo systemctl stop futsito-update.service`.

## 2. Código y dependencias

```bash
git fetch origin claude/laughing-volta-5ofbhl && git checkout claude/laughing-volta-5ofbhl && git pull --ff-only
```

```bash
git log --oneline -1
```

Debe mostrar `74c88a5 Multideporte Fase 3E…`.

```bash
.venv/bin/pip install -e .
```

## 3. Migraciones hasta 0010

```bash
.venv/bin/python -m alembic upgrade head
```

```bash
.venv/bin/python -m alembic current
```

Debe mostrar `0010 (head)`.

- **0009** (deporte obligatorio): las competiciones sin código de tipo de juego pasan a `futsal` y queda constancia.
  Comprobación: `select modality, count(*) from competitions group by 1;` solo debe dar `futsal` antes de cargar fútbol 11.
- **0010** (sustituciones y otros técnicos): aditiva; no toca filas existentes.

## 4. Variables de entorno y alcance deportivo

| Fichero o servicio | Debe tener | NO debe tener |
|---|---|---|
| `/etc/futsito/update.env` | `DATABASE_URL`, `FUTSAL_RFFM_NETWORK_AUTHORIZED=1` | `FUTSAL_INGEST_SCOPE`: sustituiría el alcance operativo |
| API (8000) | `DATABASE_URL` | `FUTSAL_INGEST_SCOPE` |
| Chat (8001) | `API_BASE_URL=http://127.0.0.1:8000`, `LLM_PROVIDER`/`LLM_MODEL` y la clave del LLM, `CHAT_CORS_ORIGINS` con el origen del frontend | `FUTSAL_INGEST_SCOPE` |
| Frontend (compilación) | `VITE_CHAT_API_URL` = URL pública del chat | — |

```bash
grep -c FUTSAL_INGEST_SCOPE /etc/futsito/update.env
```

Debe dar 0.

```bash
.venv/bin/python -m futsal.cli ingest scope
```

Debe dar:
- `futsal … HABILITADO · todas las del catálogo`
- `football11 … HABILITADO · 12 declaradas`, cada una `enabled`

```bash
.venv/bin/python -c "from futsal.ingestion.queue.ingest_scope import load_scope as l; s=l(); f=s.sports['football11']; print(all(not e.reports for e in f.entries), sorted(e.name for e in f.entries if not e.scorers))"
```

Debe dar `True` y las 5 juveniles.

## 5. Actualización coordinada de backend, MCP, agente y frontend

Orden: API → chat (arranca su MCP) → frontend.

1. `<reiniciar API>`. Comprobación:

   ```bash
   curl -s "http://127.0.0.1:8000/competitions?sport=futsal" | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])"
   ```

   Debe salir el mismo número que antes.
2. `<reiniciar chat>`. Comprobación:

   ```bash
   curl -s http://127.0.0.1:8001/chat/health
   ```

   El MCP es un subproceso del chat: reiniciar el chat reinicia el MCP y el agente.
3. Frontend:

   ```bash
   cd frontend && npm ci && VITE_CHAT_API_URL=<url del chat> npm run build && cd ..
   ```

   Después, `<publicar frontend>` (copiar `frontend/dist`).
4. Prueba de humo en el navegador:
   - El selector «Fútbol sala | Fútbol» sale con fútbol sala por defecto.
   - Una pregunta de fútbol sala responde como siempre.

Con fútbol 11 aún sin descubrir, «Fútbol» no tiene datos: es lo esperado hasta el paso 6.

## 6. Activación de las 12 competiciones de fútbol 11

**Por qué es manual.** El alcance ya está habilitado por el commit, pero la programación usa `ingest tick --skip-catalog`: **nunca
descubre catálogos nuevos**. Sin este paso, fútbol 11 no se carga nunca. Es el punto de control de la activación.

1. Descubrimiento: solo el catálogo de fútbol 11, 13 GET.

   ```bash
   .venv/bin/python -m futsal.cli ingest tick --season 22 --game-type 1 --group __ninguno__
   ```

   ```bash
   .venv/bin/python -m futsal.cli ingest work --allow-network --until-empty --kinds discover_catalog,discover_groups --max-requests 13 --max-reports 0
   ```

   Esperado: `requests: 13`, `done: 13`, `pending_by_kind: {'calendar': 93}`.

   ```bash
   .venv/bin/python -m futsal.cli ingest coverage --sport football11
   ```

   Debe dar 12 competiciones, 93 grupos y 1.627 plazas (`teams_expected`).
2. Carga inicial. Elige una opción:
   - **a) Dejarla a la programación.** Reactiva los temporizadores (paso 7). Con 03:00 + 05:00 entre semana, ≈1–2 noches.
   - **b) En tandas manuales**, de día y fuera de las 03:00–06:00, como en la sesión, con un tope por tanda:

     ```bash
     .venv/bin/python -m futsal.cli ingest tick --season 22 --skip-catalog
     ```

     ```bash
     .venv/bin/python -m futsal.cli ingest work --allow-network --until-empty --max-requests 450 --max-reports 0 --exclude-kinds match_report
     ```

     Repite hasta vaciar la cola. Los cortes `source_unavailable` por nulos de la RFFM son normales (≈10 %): vuelve a lanzar.
     Una ejecución que choque con el temporizador sale con `locked` y 0 peticiones.

## 7. Temporizador compartido (03:00 y 05:00)

```bash
sudo install -m 0644 deploy/systemd/futsito-update-weekday.timer deploy/systemd/futsito-update.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl start futsito-update-weekday.timer futsito-update-weekend.timer
```

```bash
systemctl list-timers 'futsito-*' --all
```

El de entre semana debe mostrar la siguiente a las 03:00 o a las 05:00.

```bash
systemctl cat futsito-update-weekday.timer | grep OnCalendar
```

Deben salir dos líneas: `03:00:00` y `05:00:00`.

```bash
systemctl show futsito-update.service -p TimeoutStartUSec -p ExecStart | cat
```

Debe dar 58 min y `update_rffm.sh 900`.

**Tras la primera noche:**

```bash
journalctl -u futsito-update --since "today 02:55" | grep -E "inicio|fin|stop_reason|locked"
```

- La de las 03:00 termina antes de las 04:00.
- La de las 05:00 arranca sin `locked`.

## 8. Control de peticiones, trabajos pendientes y fútbol sala

**Peticiones por día, deporte y tipo**, con nulos y errores:

```bash
psql "${DATABASE_URL/+psycopg/}" -c "
select (r.requested_at at time zone 'Europe/Madrid')::date dia,
       case j.target->>'game_type' when '1' then 'football11' when '3' then 'futsal' else 'otro' end deporte, j.kind,
       count(*) peticiones, count(*) filter (where r.parse_status<>'ok') no_ok
from raw_fetches r left join ingest_jobs j on j.id=r.job_id
where r.requested_at > now() - interval '3 days' group by 1,2,3 order by 1,2,3;"
```

**Por ejecución:**

```bash
psql "${DATABASE_URL/+psycopg/}" -c "
select id, started_at, status, stats->>'stop_reason' parada, stats->'requests_by_kind' peticiones, stats->'pending_by_kind' pendientes
from ingestion_runs where kind='ingest_worker' order by id desc limit 12;"
```

```bash
.venv/bin/python -m futsal.cli ingest status
```

**Garantías que hay que comprobar** (todas deben dar 0):

```bash
psql "${DATABASE_URL/+psycopg/}" -At -c "
select 'actas_f11_en_cola', count(*) from ingest_jobs where kind='match_report' and target->>'game_type'='1';
select 'goleadores_juveniles', count(*) from ingest_jobs where kind='scorers' and target->>'competition' in ('26738130','26737715','26737718','26737724','26738323');
select 'ejecuciones_sobre_900', count(*) from ingestion_runs where kind='ingest_worker' and (stats->>'requests')::int > 900;"
```

| Señal | Normal | Actuar |
|---|---|---|
| `stop_reason` | `empty`, `max_requests`, `source_unavailable` | `paused`: revisar el motivo y `ingest resume`; `breaker`: revisar |
| Nulos de `/api/results` | ≈10 % | >30 % varias pasadas seguidas: `ingest pause` y esperar |
| Atraso a las 06:00 L-V | 0 | >0 tres días seguidos: revisar capacidad |

**Fútbol sala sigue igual:**
- Repite la huella de §0 en `data/deploy-3e/huella_fs_despues.txt` y compárala con `diff`. Solo cambia lo que la programación
  haya actualizado de forma normal (más finalizados, más actas); competiciones, grupos y equipos deben coincidir.
- `ingest coverage --sport futsal --json` frente a `coverage_fs_antes.json`.
- `curl -s "http://127.0.0.1:8000/groups/<id de un grupo F11>/teams?sport=futsal"` debe dar 404, «no encontrado en fútbol sala».
- Sin coste:

  ```bash
  .venv/bin/python -m futsal.agent.understanding_eval --out data/deploy-3e/und.json
  ```

  Debe dar `pass_rate 1.0` y `critical_wrong_competition 0`.
- En el navegador, con «Fútbol sala»: «¿Cómo va Preferente Aficionado de fútbol sala?» (sus grupos FS) y «¿Qué partidos de fútbol
  hay este fin de semana?» (propone cambiar a Fútbol y no mezcla).

## 9. Partidos de liga pendientes y las dos copas

En la sesión quedaron 363 partidos de liga sin cargar porque se acabó el presupuesto. En producción la carga es nueva: esta
consulta mide lo que falte, calendario oficial frente a la BD.

```bash
psql "${DATABASE_URL/+psycopg/}" -c "
with cal as (select j.target->>'competition' comp, j.target->>'group' grp, m->>'codacta' codacta, (m->>'date')::date d
             from ingest_jobs j, jsonb_array_elements(j.checkpoint->'rounds') r, jsonb_array_elements(r->'matches') m
             where j.kind='calendar' and j.status='done' and j.target->>'game_type'='1'),
     db as (select m.external_id from matches m join competition_groups g on g.id=m.group_id join competitions c on c.id=g.competition_id
            where c.modality='football11')
select c.name, count(*) filter (where cal.d <= current_date) pasados_calendario,
       count(*) filter (where cal.d <= current_date and db.external_id is null) pasados_sin_cargar,
       count(distinct cal.grp) filter (where cal.d <= current_date and db.external_id is null) grupos
from cal join competitions c on c.external_id=cal.comp left join db on db.external_id=cal.codacta group by 1 order by 1;"
```

**Ligas:** `pasados_sin_cargar` debe tender a 0 tras la carga inicial. Si quedan:
- Revisa `select target, attempts, last_error from ingest_jobs where kind='round_results' and status in ('failed','dead');`.
- Si son nulos, se reintentan solos, hasta 4 intentos con espera creciente.
- Un trabajo `dead` lo vuelve a proponer el planificador en la cadencia de jornadas cerradas.

**Copas** (Copa RFEF Fase Autonómica y Copa de Aficionados): el catálogo oficial las marca **inactivas**.
- Lo esperado **con el código actual**: calendario descargado, 0 partidos en la BD (13 y 78 en el calendario) y clasificación
  `not_ingested` o `not_applicable`.
- El planificador no pide resultados de competiciones inactivas.
- Cargarlas requiere decidir el cambio propuesto en `docs/multisport-phase3e.md` §7 (≈8 GET); afecta también a las inactivas de
  fútbol sala.

## 10. Reversión

De menor a mayor alcance. Ninguna borra datos.

| Problema | Acción | Efecto |
|---|---|---|
| Algo raro en la ingesta | `.venv/bin/python -m futsal.cli ingest pause --reason "revisión 3E"`; después `ingest resume` | Para la fuente al instante; nada se pierde |
| Fútbol 11 molesta a fútbol sala o consume de más | En `src/futsal/config/ingest_scope.json`, `football11.enabled: false` (cambio local o commit de reversión). Opcional: vaciar su cola con `update ingest_jobs set status='cancelled' where target->>'game_type'='1' and status in ('pending','failed');` | El planificador deja de proponer F11; el trabajador rechaza sin red lo encolado; FS sigue igual; los datos de F11 se quedan, aunque la API los sigue sirviendo con `sport=football11` |
| Sobra la pasada de las 05:00 | Borrar la línea `OnCalendar=Mon..Fri *-*-* 05:00:00 Europe/Madrid` de `/etc/systemd/system/futsito-update-weekday.timer`, luego `sudo systemctl daemon-reload && sudo systemctl restart futsito-update-weekday.timer` | Vuelve a solo 03:00 (con atraso entre semana si F11 sigue activo) |
| Fallo de código en la API, el chat o el frontend | Volver al commit de `data/deploy-3e/commit_anterior.txt` si es ≥ `a8f3162`, o a `88ef142` (Fase 3; mismo esquema 0010 y F11 deshabilitado), reinstalar y reiniciar API, chat y frontend | Código anterior con el esquema actual |
| Esquema | **No bajar migraciones** una vez cargado F11 | El `downgrade` de 0009 recrea la unicidad de equipos sin deporte y fallaría o mezclaría datos si hay el mismo código de equipo en los dos deportes; el de 0010 se niega si hay sustituciones. Sin copia previa no hay reversión de esquema segura |

Borrar los datos de F11 cargados no forma parte de la reversión: requiere tu autorización expresa.

## 11. Revisión: resultados administrativos (estado `unknown` con marcador)

**Corrección de cifras.** En el informe de la 3E hablé de «10 resultados administrativos». Ese dato era de Segunda Aficionado a
mitad de carga. Con la carga completa (BD de la sesión), hay **73 partidos de F11 en `unknown` con marcador** (y 3 en fútbol sala).
Clasificados con las instantáneas de la RFFM:

| Grupo | Partidos | Lo que dice la fuente |
|---|---|---|
| Equipo retirado | **48** | `Retirado_local`/`Retirado_visitante = "1"`, `acta_cerrada = "0"`, resultado asignado (3-0/0-3; en juveniles a veces 13-0 o 5-0). **Los 12 con fecha futura** (22-11 y 29-11) son todos de este grupo |
| Marcador sin acta cerrada | 23 | Sin retirada: probablemente jugados y pendientes de cerrar el acta |
| «RESOLUCIÓN RFFM» | 1 | Resultado por resolución federativa |
| «Suspensión Masiva» con marcador | 1 | Debería ser `suspended`: el parser busca `"suspend"` y la RFFM escribe «Suspensión» (`suspens…`). **Defecto**, también en fútbol sala |

**Cómo los interpreta hoy el agente** (código y API revisados):

| Consulta | Comportamiento | Valoración |
|---|---|---|
| «Próximo partido» | `list_matches status=scheduled`: no incluye estos partidos | Correcto en lo esencial (un partido con resultado asignado no se juega), pero no dice por qué; un equipo retirado sale sin próximo partido |
| «Último resultado» | `status=finished`: no los incluye | Incompleto: el 0-3 del domingo pasado de un rival de un retirado no aparece como último resultado, aunque cuenta en la clasificación oficial |
| «Partidos del fin de semana» y listados | `score "0-3"` + `status "unknown"`, sin explicación (`/v2/matches` compacto; `/matches/{id}` igual) | **Riesgo**: el LLM puede presentar «0-3» como resultado de un partido del domingo que no se ha jugado ni se jugará. Ni el prompt ni las descripciones MCP explican `unknown` |
| Clasificación | Tabla oficial de la RFFM | Correcto (no se recalcula) |

**Propuesta de corrección** (no aplicada; requiere tu visto bueno porque cambia la ingesta y afecta también a fútbol sala):
1. Parser: leer `Retirado_local`/`Retirado_visitante` y guardar el motivo administrativo. Migración aditiva **0011**:
   `matches.result_note` (texto), con valores `withdrawal_home`, `withdrawal_away`, `federation_resolution`, `null`. El estado sigue
   `unknown`, sin inventar `finished`.
2. Parser: la palabra clave `"suspend"` pasa a `"suspen"` («suspendido» y «Suspensión»).
3. API y MCP:
   - En listados y en `/matches/{id}`, añadir `result_note` y una nota «resultado administrativo: el equipo X se ha retirado
     (RFFM); el partido no se disputa».
   - En filas compactas, `status: "administrative"` en lugar de `unknown`, solo en la presentación.
4. Agente:
   - Una línea en la descripción de `list_matches` y del prompt: `unknown` con marcador es un resultado no confirmado o
     administrativo; nunca se presenta como jugado.
   - «Último resultado» incluye los administrativos ya vencidos, marcados como tales.
5. Pruebas:
   - Fixture con `Retirado_local=1` y fecha futura.
   - Casos de comprensión «¿próximo partido del Mar Abierto?», «¿partidos del domingo en Segunda Aficionado G5?».
   - Regresión FS con sus 3 `unknown`.

**Comprobación en producción tras desplegar**, sin cambiar nada:

```bash
psql "${DATABASE_URL/+psycopg/}" -c "
select c.modality, count(*) unknown_con_marcador, count(*) filter (where m.scheduled_date > current_date) futuros
from matches m join competition_groups g on g.id=m.group_id join competitions c on c.id=g.competition_id
where m.status='unknown' and m.home_score is not null group by 1;"
```

Y en el navegador («Fútbol»):
- «¿Cuál es el próximo partido del C.D. MAR ABIERTO 'A'?»
- «¿Qué partidos hay el domingo en Segunda Aficionado grupo 5?»

Si el agente da un 0-3 de un partido futuro como jugado, es el riesgo descrito: aplicar la corrección.

## 12. Revisión: «¿Qué grupos tiene X?» pide aclaración

**Reproducción** (API → MCP → agente reales, modelo simulado, BD de la sesión):

| Pregunta | Resultado |
|---|---|
| «¿Qué grupos tiene Segunda Aficionado?» (22 grupos) | `aclaracion_ambito`: «¿De qué grupo quieres consultarlo: «Grupo 1»; …?». Sin herramientas; queda una aclaración pendiente, así que la siguiente frase se interpreta como elección de grupo |
| «¿Cuántos grupos tiene Tercera Federación?» (1 grupo) | Consulta la **clasificación** (`get_standings`) y 3 llamadas al LLM |
| «¿Qué grupos tiene Tercera División?» (fútbol sala, 2 grupos) | 4 `get_standings` y 3 llamadas al LLM |

**Causa:**
1. `agent/intent.py` no tiene intención de «lista de grupos» (`INTENTS = standings, last_result, next_match, matches, scorers,
   teams_list, stats, rules`): `classify("¿Qué grupos tiene Segunda Aficionado?")` devuelve `intent=None`.
2. Sin intención, `ContextEngine._targets` (`agent/context.py`) aplica la política de ámbito:
   - Con más de `FANOUT_MAX_GROUPS = 3` grupos y ninguno indicado, pregunta el grupo.
   - Con 3 o menos, consulta todos, y la consulta por defecto acaba siendo la clasificación.

   La pregunta es sobre el catálogo, no sobre un grupo, pero la política no lo distingue.

**Corrección propuesta** (no aplicada):
1. `intent.py`: nueva intención `groups_list`, con patrones como `\b(que|cuales|cuantos)\s+grupos\b`,
   `\bgrupos\s+(tiene|hay|forman|componen|juegan)\b` y `\blista(do)?\s+de\s+grupos\b`, y un peso que gane a «standings».
2. `context.py`: con `groups_list` y la competición resuelta → `status=act` **sin** pasar por `_targets`.
   - Ni aclaración de grupo ni reparto.
   - Estado conversacional: competición fijada, `groups=[]` y sin `pending`, para que «¿y quién lidera el grupo 3?» funcione
     después.
   - Si la competición es ambigua (varias familias), se sigue preguntando la competición, como hoy.
3. Respuesta determinista, como la paginación:
   - Componerla con el catálogo que el agente ya tiene (nombres de grupo) más una llamada MCP `list_groups(competition_id)` para
     el número de equipos.
   - **0 llamadas al LLM** (hoy, de 1 a 3) y un visual de chips con los grupos, que llevan a «clasificación del grupo N».
4. Pruebas:
   - `understanding_cases`: F11 con 22 grupos, F11 con 1, FS con 2, el seguimiento «¿y el grupo 3?» y «¿cuántos grupos hay en
     Primera Juvenil?».
   - `understanding_eval` de fútbol sala al 100 %.
   - Prueba con MCP simulado de que no se llama a `get_standings`.
5. Impacto en fútbol sala: la misma pregunta pasa de consultar clasificaciones a listar grupos (mejora). El resto de casos no
   cambia porque la intención nueva solo salta con estas frases.
