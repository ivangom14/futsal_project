# Fase 19 — Resolución de equipos y eficiencia de consultas del agente

Solo offline: **sin llamadas LLM, sin scraping, sin cambios en datos, migraciones ni RAG**. La medición live de referencia
(`docs/phase17-evaluation.md`, 53/65) no se modifica.

## Auditoría (estado real antes de editar)
HEAD `56f5e3c` = remoto, árbol limpio; `827ddb6`, `102db50`, `c6720ff` y `ac48edc` están en HEAD. Línea base: 404 pass / 133 skipped (PostgreSQL).

| Problema observado en live | Causa raíz (verificada en el código) |
|---|---|
| `team_id` adivinado (`equipo_proximo_partido`, `acta_goleadores_ultimo`) | El agente enviaba a las tools cualquier `team_id`/`round_id`. Si el ID existía en el grupo, la API devolvía en silencio los datos de **otro** equipo (solo un ID inexistente daba 404). En modo catálogo (>3 grupos) el contexto no lista equipos, y `home_team_id`/`away_team_id`/`group_id`/`round_id` se descartan del resultado antes de llegar al modelo (`DROP_KEYS`) |
| Muchas llamadas para el último partido | `GET /groups/{id}/matches` solo ordena ascendente por jornada y `limit` toma las primeras filas: no había forma de pedir «el último» con `limit=1`, así que el modelo recorría jornadas y las inventaba |
| Próximo partido «equivocado» | Un partido `scheduled` con fecha ya pasada era indistinguible de uno futuro (dato desactualizado en la base) |
| Llamadas idénticas repetidas | Sin reutilización de resultados dentro de la pregunta |

## Cambios
- **API** `GET /groups/{id}/matches`: parámetro `order=asc|desc` (por defecto `asc`, comportamiento anterior intacto; `desc` = fecha publicada más reciente primero, jornada e id como desempate, sin fecha al final) y `past_due: true` en partidos aún `scheduled` con fecha anterior a hoy (no cambia su estado ni los interpreta como jugados). `create_app(..., today=)` inyecta «hoy» en tests. Funciones puras `order_matches` / `mark_past_due`.
- **MCP** `list_matches`: parámetro `order`; la descripción explica `past_due`.
- **Agente** (`agent/agent.py`):
  - `team_id` / `against_team_id` / `round_id` solo se ejecutan si una tool mostró ese ID al modelo en la pregunta (`find_teams`, `list_teams`, `list_rounds`, `team_id` de resultados) o figura en el contexto precargado. Si no, la llamada **no se ejecuta**, el modelo recibe un `error_validacion` con la herramienta que debe usar y **cuenta contra el límite** (el límite global no cambia).
  - Nombre ambiguo: si `find_teams` devolvió el mismo nombre en varios grupos y la pregunta no nombra competición y grupo (`scope_identified`), usar uno de esos IDs se rechaza con la orden de preguntar cuál.
  - Una llamada idéntica (herramienta + argumentos) dentro de la misma pregunta reutiliza el resultado sin ir a la API; sigue contando para el límite.
  - Prompt (+≈90 tokens por llamada): último partido = `list_matches(team_id, status=finished, order=desc, limit=1)` + `get_match_report`; próximo = `status=scheduled`; `past_due=true` ⇒ fecha pasada, datos posiblemente desactualizados; IDs solo de las tools.
- Sin caché persistente, sin cambios en el límite (`MAX_TOOL_CALLS`=5), en la guardia de alcance ni en el RAG.

## Pruebas
`tests/test_agent_team_resolution.py` (14, LLM guionizado + API simulada que usa las funciones reales de orden y `past_due`): equipo por `find_teams`; ID inventado (que existe en la BD) rechazado sin llegar a la API; `against_team_id`; ambiguo no elegido / permitido si la pregunta da competición y grupo / inequívoco sin aclaración; orden `desc`; `past_due`; el próximo partido llega al modelo con la marca; prompt y descripción; jornadas solo de `list_rounds`; llamada repetida reutilizada; el límite conserva la clasificación oficial ya obtenida y no inventa el resto; tools sin IDs (actas, clasificación, resultados) intactas. Además `tests/test_mcp_server.py` (reenvío de `order`) y `tests/integration/test_api_pg.py` (orden y `past_due` sobre PostgreSQL temporal).

## Resultados de validación
- Pruebas específicas: `tests/test_agent_team_resolution.py` 14/14; `tests/test_mcp_server.py` completo.
- Suite completa con un PostgreSQL desechable local (fuera del repo, sin tocar datos del usuario): **551 passed, 2 skipped** (los 2 son live). Sin PostgreSQL: 419 passed, 134 skipped (integración).
- `ruff check .` limpio; `mypy` sin incidencias (81 ficheros).
- Efecto en pruebas existentes: 3 tests de `tests/integration/test_agent_audit_pg.py` y el plan offline de `resultados_otro_grupo` (`agent/audit_cases.py`) usaban un `team_id`/`round_id` de otro grupo sin haberlo descubierto (y en el mismo lote que el descubrimiento). Ahora descubren antes (`list_teams` / `list_rounds`); ninguna expectativa se relajó (siguen exigiendo los mismos límites, fuentes y marcadores).
- RAG sin cambios: ningún fichero de `knowledge/`, `api/knowledge.py`, `rule_cases.py` ni de la herramienta `search_rules` se modificó.

## Limitaciones
- Los IDs de la pregunta del usuario no cuentan como fuente (se exige una tool). Si el contexto precargado se recorta (>30 equipos por grupo) hay que usar `find_teams`.
- La ambigüedad se detecta por `find_teams` + alcance de la pregunta; si el modelo no llama a `find_teams` pero acierta un ID mostrado en el contexto, no hay comprobación adicional.
- `order=desc` usa la fecha publicada; un partido sin fecha queda al final. Con una API sin reiniciar (anterior a esta fase) `order` se ignora.
- Los datos verificados que se conservan al agotar el límite siguen siendo las tablas oficiales (Fase 17.8); los resultados de `list_matches` no se resumen en el mensaje de límite.
- No validado con un LLM real. Repetir en live (con autorización): `equipo_proximo_partido`, `acta_goleadores_ultimo`, `combinada_lider_ultimo_resultado`, `combinada_lider_proximo_partido`, `equipo_ambiguo` y controles `resultados_otro_grupo`, `tabla_clasificacion_lider`.
