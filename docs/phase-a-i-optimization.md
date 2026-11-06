# Fases A–I: estabilización, optimización y preparación para producción

> **Actualizado por la fase de corrección de auditoría** (`docs/phase-audit-fixes.md`): la reparación de `group_id` ahora va tras `AGENT_GROUP_ID_REPAIR` (apagada por defecto), el Tier 0 usa lista blanca de plantillas con frescura y ámbito, la ampliación de herramientas usa señales concretas (no errores de herramienta) y el presupuesto de `ab_arms.py` es global y real. Donde este documento diga lo contrario, manda el nuevo.

Rama `claude/tender-rubin-rsz9uv`. Sin commit. **Todo lo nuevo está APAGADO por defecto** (`legacy` + prefetch off + tools full + tier0 off + telemetría off). Nada se ha validado en vivo: lo que sigue distingue **MEDIDO** (A/B real previo), **MEDIDO OFFLINE** (sobre datasets/mocks, sin LLM) y **ESTIMADO**.

## Resumen por fase

| Fase | Estado | Qué se hizo |
|---|---|---|
| A group_id | Hecha (sin la traza r4) | Causa raíz y reparación determinista (abajo) |
| B prefetch | Hecha | Exclusión conservadora de clasificación con ámbito; catálogo revisado |
| C datasets/holdouts | Verificada, sin cambios | Suite de integridad/sellado/solapes en verde; B no se tocó; A solo se midió (sin ajustar) |
| D brazos | Hecha | Banderas `AGENT_TOOLS`, `AGENT_TIER0`; brazos `dynamic_tools`, `tier0`, `prefetch_dynamic` (+ `compact_prefetch` ya existía) |
| E selección dinámica | Hecha (opt-in) | `tool_select.py`, `AGENT_TOOLS=dynamic` |
| F operaciones de alto nivel | **Omitida** | Ver justificación |
| G Tier 0 | Hecha (opt-in, alcance mínimo) | `tier0.py`, `AGENT_TIER0=on`: posición/puntos de UN equipo |
| H plan declarativo | **Omitida** | Ver justificación |
| I prompts/contexto/memoria | Solo auditoría | Sin cambios (ver abajo) |

## A. group_id omitido

**Causa raíz (leída en el código, no en la traza `prefetch-r4`, que no se aportó):** `compact_result` elimina `group_id` de lo que ve el LLM (`DROP_KEYS`). El modelo debe deducirlo del contexto de sistema (grupo → id) y a veces lo omite. `list_matches` lo exige (esquema MCP) y devuelve `error_validacion`, el modelo reintenta: +1 llamada LLM y +~4k tokens. No es del API, de FastAPI ni de PostgreSQL. Afecta a legacy y prefetch por igual.

**Corrección** (`Agent._repair_group`): si falta `group_id` en `list_matches`/`list_match_events`/`find_players` y el `team_id` fue mostrado por `find_teams` en UN solo grupo (id→grupo del resultado original, nunca por similitud) y no es ambiguo, se completa; se registra en `ToolCallRecord.repaired` (`{field, value, source, team_id}`) y en `mcp_calls.repaired`. Con varios grupos o equipo no mostrado: no se toca (se mantiene el rechazo previo). El contrato público de API/MCP no cambia. **(Actualizado)** Va detrás de `AGENT_GROUP_ID_REPAIR=on|off` (**off por defecto**); con la bandera apagada el comportamiento es idéntico al anterior (el test `test_case2_liga_senior…` vuelve a su expectativa original). Se conservan los argumentos originales y los efectivos por separado.

Pruebas: `tests/test_group_repair.py`. No se afirma que el fallo `prefetch-r4` quede resuelto: no se tiene su traza. Pendiente de validar en vivo: que el reintento desaparezca (ver comandos).

Nota: se probó y **se revirtió** pedir al modelo «usa el group_id del resultado» en la nota del prefetch, porque el resultado no lo contiene.

## B. Prefetch

- Exclusión `standings_only` (clasificación pura **con** competición/grupo en la pregunta): vuelve al flujo de siempre y ni carga el catálogo. Va **fuera de `decide()`** para no alterar las métricas selladas del Holdout A. Sin ámbito sigue haciendo prefetch (`find_teams` da el grupo). Dev: 22 preguntas marcadas, ninguna necesitaba herramientas ajenas a la tabla (la única con `list_matches` es un seguimiento, que el agente descarta antes por contexto). Beneficio esperado (ESTIMADO): evitar el +3 % observado en `tabla_clasificacion_posicion_equipo`.
- Catálogo: 24 `list_teams` por Agent y 6 h de caché, secuenciales (coste solo en la primera pregunta de cada proceso). Un fallo parcial deja el catálogo en `None` → sin prefetch (seguro). No se paralelizó (un solo cliente MCP stdio): decisión documentada, sin medir latencia real.

## E. Selección dinámica de herramientas (`AGENT_TOOLS=full|dynamic`)

Selector léxico cerrado, determinista, de **recall** (ante duda, las 15). `search_rules` se mantiene salvo ancla de datos fuerte; seguimientos y preguntas sin señal → completo; unión ≥12 → completo. En ejecución se **amplía al conjunto completo** tras un error de herramienta o una reconsulta de guardarraíl (`rec.tool_select.widened`).

| Medido OFFLINE (etiquetas `needed_tools`; estimación de esquema por caracteres) | Dev (255) | Holdout A (1010, medido una vez, sin ajustar) |
|---|---|---|
| Preguntas con subconjunto | 71,4 % | 72,9 % |
| Herramienta necesaria omitida | 0 / 182 | 0 / 736 (IC95 de fallo ≤ 0,41 %) |
| Ahorro de esquema (todas las preguntas) | 31,3 % | 33,8 % |

Las etiquetas las escribí yo y A es sintético: **no es evidencia de rendimiento real**. Ahorro de tokens ESTIMADO: ~33 % del esquema (~1.590 tokens reales ⇒ ~520/llamada ⇒ ~12 % de la entrada por llamada). Riesgo conocido: variar las herramientas cambia el prefijo y puede reducir el caché implícito del proveedor (medir `cached_tokens`).

## G. Tier 0 (`AGENT_TIER0=on`)

Responde SIN LLM «posición y/o puntos de UN equipo»: `find_teams` + `get_standings` vía `_execute` (validación de IDs, trazas, foco), texto plantilla con cifras copiadas y procedencia (jornada, captura). Se abstiene (y el estado queda como antes) ante: seguimiento, extractor no confiado (homónimo, histórico, multi-equipo…), ≠1 fila/grupo, `status≠ok`, `warning`, cobertura parcial, jornada atrasada respecto a la RFFM, puntos de sanción, equipo ausente o repetido, o cualquier error. Elegibilidad: solo palabras de posición/puntos; cualquier señal de partidos, líder, tabla, goles o comparación → LLM.

Puerta (elegible + extractor), medida offline: dev 22/22 correctas; A 53/53 correctas (5 % de las preguntas). **No basta** para sostener ≥95 % de precisión (hacen falta ≥72 decisiones sin error) y no mide la corrección del texto. Riesgo de producto: el estilo de la respuesta no es el del LLM; no se ha evaluado con el evaluador live. Se añadió `team_id` al foco del Tier 0 (`presentation.compute_focus`) para no romper «¿y su próximo partido?».

## Omitidas

- **F (team_snapshot, compare_recent_form, scorers_query):** su ahorro adicional depende de qué preguntas siguen necesitando varias herramientas tras prefetch + reparación + selección; sin telemetría real ni A/B de esas tres no se puede dimensionar, y `compare_recent_form`/histórico de goleadores introducen reglas de negocio (qué es «forma reciente», no mezclar tabla y actas) que merecen decisión tuya. Diseño sugerido: DTO compactos sobre `McpClient`, `asyncio.gather` acotado, errores parciales explícitos, sin servidor MCP nuevo.
- **H (plan declarativo JSON):** condicionada a F y a que queden casos compuestos que justifiquen el riesgo. Sin Code Mode.
- **I (prompts/contexto/memoria):** auditoría con datos medidos previos: entrada ≈4.4k tokens/llamada, prefijo ≈61 % (sistema ≈880, herramientas ≈1.590, contexto ≈370 reales). `compact` solo dio −3,2 % tras mantener los criterios. Mayor palanca restante = herramientas (E); el historial no se midió. Sin cambios para no arriesgar citas ni guardarraíles.

## Banderas y producción

| Variable | Valores | Defecto | Notas |
|---|---|---|---|
| `AGENT_PROMPT_MODE` | legacy / compact | legacy | |
| `AGENT_PREFETCH` | off / on | off | |
| `AGENT_TOOLS` | full / dynamic | full | |
| `AGENT_TIER0` | off / on | off | requiere catálogo/competiciones cargados |
| `AGENT_ROUTER` | shadow / off | shadow | solo observa |
| `AGENT_TELEMETRY` | off / on | off | ver `docs/telemetry.md` (saneado, retención 90 d, sin envío externo) |

Rollback: poner la variable en su valor por defecto y reiniciar el agente; no hay migraciones. Diagnóstico: `rec.prefetch`, `rec.tool_select`, `rec.tier0`, `mcp_calls.{prefetch,tier0,repaired}` en trazas/telemetría. Configuración recomendada hoy: **todo por defecto**; activar en este orden tras validar en vivo: reparación (ya activa) → `prefetch` → `dynamic` → `tier0`, de uno en uno.

## Comandos live (consumen cuota; sin `--yes` solo muestran el plan)

```bash
git pull origin claude/tender-rubin-rsz9uv
# plan y presupuesto, sin llamadas
python scripts/ab_arms.py --arms legacy,prefetch,dynamic_tools,tier0 --cases prefetch_probe --reps 5 --out data/agent/ab-fase-e --min-interval 8
# ejecutar (consume cuota)
python scripts/ab_arms.py --arms legacy,prefetch,dynamic_tools,tier0 --cases prefetch_probe --reps 5 --out data/agent/ab-fase-e --min-interval 8 --yes
# completar not_run por 429
python scripts/ab_arms.py --arms legacy,prefetch,dynamic_tools,tier0 --cases prefetch_probe --reps 5 --out data/agent/ab-fase-e --min-interval 8 --fill-missing --yes
```
Cada brazo se atribuye por separado; `prefetch_dynamic` combina dos y solo debe usarse después. Casos que ejercitan cada cosa: `tabla_clasificacion_posicion_equipo` (Tier 0 y exclusión), `acta_goleadores_ultimo`/`equipo_proximo_partido` (group_id), el resto (selección dinámica).
