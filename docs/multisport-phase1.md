# Multideporte · Fase 1: base multideporte y blindaje de fútbol sala

Estado: implementada, sin commit. No se ha hecho ninguna petición a la RFFM ni ninguna llamada a un LLM de pago. La ingesta de
fútbol 11 está declarada pero **deshabilitada**.

## 1. Decisiones

| Tema | Decisión |
|---|---|
| Dimensión deporte | `competitions.modality` es el deporte (`futsal`, `football11`…). Es obligatorio (CHECK), no tiene valor por defecto y **solo** lo fija el tipo de juego oficial (`game_type_code`, el `tipojuego` de la RFFM: 1 = fútbol 11, 3 = fútbol sala) mediante `config/sources/rffm.json`. Ni el nombre ni un override pueden cambiarlo. Grupos, jornadas, equipos, partidos y actas heredan el deporte por su competición; no hay columna duplicada. |
| Contrato | `sport` es **obligatorio** en todos los endpoints de datos (v1, v2, `/knowledge/search`, `/crests`), en todas las herramientas MCP y en `/chat`, `/chat/feedback` y `/chat/crests`. Sin él: error de validación que nombra `sport` (400 en v1 y en el chat, 422 en v2). El valor por defecto vive **solo en el frontend** (fútbol sala). No hay capa de compatibilidad transitoria: todos los clientes del repositorio se migraron en el mismo cambio. |
| `all` | Ámbito explícito para consultas combinadas intencionadas (API y MCP), nunca un valor por defecto. En ese ámbito cada fila lleva `sport`. En un solo deporte, las respuestas tienen exactamente el formato anterior. `/chat` solo admite `futsal` o `football11`. |
| ID de otro deporte | `GET /groups/{id}/…`, `/matches/{id}`, `/competitions/{id}/…` con un ID de otro deporte → 404 «… no encontrado en fútbol sala». |
| Identificadores | Están verificados como disjuntos los códigos de competición y los `CodigoCategoria` (catálogos T22: 43 de fútbol 11 frente a 50 de fútbol sala, 0 comunes; los de grupo pertenecen a la misma secuencia). No están verificados `CodEquipo` ni `codacta`. Por eso: (a) la identidad de equipo pasa a ser `(source, sport, CodEquipo)`, y un mismo código en dos deportes son dos entidades; (b) el importador nunca mueve una competición ni un grupo a otro deporte (`sport_conflict`: no se aplica nada y queda la incidencia en `data_quality_issues`); (c) un `codacta` repetido sigue protegido por el `identity_conflict` existente. Los jugadores, las personas y los clubes pueden ser compartidos legítimamente (son la misma persona o institución); las consultas los filtran por deporte a través de partido → grupo → competición. |
| Normas | `kb_documents.sports` (text[]). Los tres documentos actuales son de `futsal`. `search_rules` filtra por deporte: una pregunta de fútbol 11 no recibe normas de fútbol sala (hoy no hay documentos de fútbol 11, Fase 5). |
| Agente | `SportScopedMcp` inyecta el deporte de la petición en **cada** herramienta que lo declara (también el contexto precargado y el prefetch) y lo **oculta** al LLM. El modelo no puede elegirlo ni equivocarse, y los esquemas que ve son idénticos a los anteriores, sin coste de tokens. El catálogo, el contexto, el motor de comprensión y el catálogo de equipos se guardan **por deporte**. El prompt de fútbol sala no cambia (byte a byte); el de fútbol 11 solo cambia la presentación (el resto, en la Fase 2). |
| Memoria | El servidor fija el deporte de cada conversación (`ConversationMemory.bind_sport`). Si una conversación conocida llega con otro deporte, se descartan su historial, foco y estado, y no se acepta la siembra del navegador en esa petición. En el frontend, cambiar de deporte con mensajes abre una conversación nueva. Las conversaciones guardadas sin deporte se cargan como fútbol sala. |

## 2. Migración 0009 (`alembic/versions/0009_deporte_obligatorio.py`)

- Rellena `modality` desde `game_type_code`. Las filas sin `game_type_code` (las de `import-league` anteriores a la 0007) pasan a `futsal`, porque toda la ingesta previa usó tipojuego=3; queda constancia en `classification.modality.source = migration_0009`. Un `game_type_code` sin correspondencia **aborta** la migración.
- Quita el valor por defecto `'unknown'` y añade `ck_competitions_modality_valid`.
- `team_entities.sport` (NOT NULL y CHECK) y unicidad `(source, sport, external_code)`. Las entidades sin equipos se marcan `futsal` y no se borra nada.
- `kb_documents.sports` con `ck_kb_documents_sports_valid`.
- El downgrade está verificado (0009 ↔ 0008 en `test_multisport_pg.py`, y también 0006 → head en `test_catalog_identity_pg.py`).

## 3. Alcance declarativo de ingesta (`config/ingest_scope.json`)

Tiene tres niveles: **descubierta** (está en el catálogo oficial y aparece en el informe de `discover_catalog`, con `scope_status` por competición), **en alcance** (casa con una entrada por `CodigoCategoria` + nombre normalizado sin el rótulo de temporada, o el deporte declara `competitions: "all"`) y **habilitada** (deporte `enabled` y entrada `enabled`).

- Fútbol sala: `all`, habilitado. Es lo mismo que antes.
- Fútbol 11: las 12 competiciones declaradas, el deporte y todas las entradas **deshabilitados**. Las otras 31 del catálogo quedan fuera de alcance.
- Hay tres barreras independientes: `discover_catalog` solo encola grupos de competiciones habilitadas; `gather_state` (planificador) solo planifica grupos habilitados; y el trabajador rechaza **sin red** y sin reintentos cualquier trabajo fuera de alcance (`fuera del alcance de ingesta: …`).
- `python -m futsal.cli ingest scope` muestra el estado (sin red ni BD).
- Añadir una competición: añadir una entrada; no hay que tocar código.

## 4. Mismo patrón de actualización para los dos deportes

Programación real (no se ha modificado):

- `deploy/systemd/futsito-update-weekday.timer`: de lunes a viernes, 03:00 (Europe/Madrid).
- `futsito-update-weekend.timer`: sábado y domingo, cada hora en punto.
- `futsito-update.service` ejecuta `scripts/update_rffm.sh 900`, que hace `recover` → `tick --skip-catalog` → `work --until-empty --max-requests 900`, con `flock`.
- La cadencia fina (partidos en juego a +75/110/180 min, barridos del fin de semana cada 2 h, actas, tablas cada 24 h con actividad…) la decide `config/ingest_schedule.json` **por grupo**, no por deporte.

Por eso no hace falta ningún script, temporizador ni cola nuevos. Un grupo de fútbol 11 habilitado entra en el mismo `gather_state` → `plan` → `ingest_jobs` → trabajador, con las mismas prioridades, reintentos, `RateLimiter` (3 s entre peticiones), cortacircuitos, instantáneas, controles de calidad e idempotencia. Habilitarlo es solo un cambio en `ingest_scope.json`. El descubrimiento del catálogo de fútbol 11 se lanza una vez con `ingest tick --game-type 1` (el trabajador lo bloquea mientras esté deshabilitado).

**Presupuesto** (estimación; las cifras reales se miden en la Fase 2):

- Fútbol sala medido en P0: 89 grupos (71 activos).
- Fútbol 11, 12 competiciones: entre 45 y 70 grupos [E], de 16–18 equipos (unas 8–9 actas por grupo y jornada [E]).
- Régimen semanal: entre +60 % y +100 % de las peticiones actuales [E]. Las actas son la mayor parte.
- El fin de semana sobra capacidad (24 ejecuciones de hasta 900 peticiones, unas 1.200/h a 3 s). El punto crítico es la ejecución nocturna del lunes, que recoge las actas atrasadas de los dos deportes y podría superar 900 [E].
- Alternativas, a decidir en la Fase 3 con medidas y **sin cambiarlas ahora**:
  1. subir el tope del lunes a 1.500;
  2. una segunda ejecución de madrugada;
  3. espaciar la revisión de actas ya cerradas.

  Las tres mantienen la misma frecuencia para los dos deportes.
- Carga inicial de fútbol 11 [E]: unas 650 peticiones sin actas y unas 4.300 actas de las jornadas ya jugadas. Se haría en tandas: el planificador ya prioriza lo reciente.

## 5. Clientes migrados

Frontend (`api.ts`: `askAgent`, `sendFeedback`, `getCrests`), servicio de chat (`web.py`: frescura y escudos hacia la API con `sport`), agente (`Agent.run(sport=…)`, CLI `python -m futsal.agent --sport futsal "…"`), MCP (22 herramientas), preflight, evaluadores (`EVAL_SPORT = "futsal"`: todas las suites actuales son de fútbol sala) y pruebas. Las pruebas antiguas de fútbol sala usan `tests/sport_client.FutsalClient`, que añade `sport=futsal` explícito; las de validación usan el cliente normal.

**Despliegue:** API, chat y frontend deben publicarse juntos. Una pestaña abierta con el JavaScript anterior recibirá un error de validación hasta recargar.

## 6. Riesgos y pendientes

- `CodEquipo` y `codacta` entre deportes, sin verificar: se comprueba con datos reales en la Fase 2. Si coinciden, las guardas lo registran y no se fusiona nada.
- El prompt de fútbol 11 (aviso de fuera de ámbito, módulo de reglas) y los alias y vocabulario del resolutor siguen siendo de fútbol sala: es la Fase 2/4. Hoy no hay datos de fútbol 11, así que no hay riesgo.
- `Lineup.tsx` dibuja una pista de 5 (Fase 4).
- Una conversación guardada con un deporte desconocido (de una versión futura) no se carga en esta versión.
