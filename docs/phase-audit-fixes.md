# Fase de corrección de auditoría (antes de la validación en vivo)

Rama `claude/tender-rubin-rsz9uv`. Sin commit. Nada se ha ejecutado contra Gemini ni Anthropic. Todo lo experimental sigue **apagado por defecto**.

## Entorno de las pruebas (reproducibilidad)

| Entorno | Python | mcp | Suite offline (sin `tests/integration`) |
|---|---|---|---|
| A | 3.12.3 | 1.30.0 | **1073 pasan, 0 fallos** |
| B | 3.11.18 | 1.30.0 | **1073 pasan, 0 fallos** |
| C | 3.13.18 | 1.30.0 | 1070 pasan, **3 fallan** (`test_prompt_compact.py`) |

Otras versiones relevantes: pydantic 2.14.3, httpx 0.28.1, pytest 9.2.0, sqlalchemy 2.1.8. `tests/integration` no se ha ejecutado (requiere PostgreSQL; no se instala).
**Causa de los 3 fallos de Python 3.13 (demostrada):** el compilador de 3.13 elimina la sangría de los docstrings y `mcp` los usa como descripción de herramienta; el fixture dorado guarda la sangría original. No es una regresión ni depende de la versión de `mcp`. No se han tocado fixtures ni dependencias.
`pyproject.toml` declara `requires-python >=3.11` y `mcp>=1.2,<2` **sin lockfile**. Un lockfile (p. ej. `uv.lock`) daría reproducibilidad, pero fija versiones para todos los entornos y habría que mantenerlo: se propone, **no se introduce** sin tu decisión. Entorno recomendado para la suite: Python 3.11 o 3.12 con `mcp` 1.30.0.

## Cambios por hallazgo

### C1 — Tier 0: lista blanca cerrada (`tier0.py`, `Agent._tier0`)
- Se sustituye la lista negra por **plantillas cerradas**: la pregunta, con el equipo y el ámbito sustituidos por marcadores, debe encajar ENTERA en una plantilla (posición, puntos, o ambas) con artículos, saludos y adverbios de actualidad admitidos (`actualmente`, `ahora`, `hoy`, `esta temporada`). Cualquier palabra no prevista ⇒ flujo normal con LLM. `candidate()` es solo un prefiltro barato previo al catálogo.
- Se mantiene el extractor (`decide`: homónimos, histórico, multi-equipo, contexto) y todas las comprobaciones de datos.
- **Dataset adversarial** `tests/fixtures/tier0/adversarial.json`: 134 preguntas redactadas a mano (25 admitidas, 109 que deben abstenerse) en 11 categorías: objetivos futuros, ventaja/diferencia, históricos, predicciones, local/visitante, sanciones/fair play/no disponible, comparaciones, seguimiento, ámbito ambiguo o distinto y formas no previstas. Se prueba de extremo a extremo con el agente: **0 activaciones incorrectas y las 25 admitidas se responden**.
- Honestidad del dataset: lo redacté yo a partir de los fallos de la auditoría; **no es evidencia de rendimiento real**. Dos ajustes durante la implementación, ambos sobre las ADMITIDAS (artículos «el/la», contracción «del» y «cuál es el puesto»); ningún negativo se modificó. Un caso se movió de abstener a admitir («Dime cuántos puntos tiene X»: pregunta directa y actual).
- Coste de cobertura (medido offline sobre los datasets del extractor, no sobre tráfico real): la puerta del Tier 0 dispara 22/255 en desarrollo (0 incorrectas) y **20/1010 en Holdout A** (0 incorrectas); con la lista negra eran 53. Es el precio de abstenerse ante lo no previsto.

### Frescura y competición (Tier 0)
- Frescura real: la captura debe ser ≤ `AGENT_TIER0_MAX_AGE_HOURS` (**72 h por defecto**, configurable; valores no numéricos o ≤0 vuelven a 72). Sin `captured_at` con zona horaria, ilegible o fechado en el futuro ⇒ abstención. Además se sigue exigiendo jornada al día respecto a la RFFM.
- Competición pedida: si el usuario menciona un ámbito reconocible, los datos recuperados deben cumplirlo (`row_matches` con las mismas pistas que `find_teams`); un alias que apunta a una competición inexistente (`hints.missing`) o un ámbito no reconocido ⇒ abstención; datos de otra competición ⇒ `scope_mismatch`. No se crean equivalencias.
- La procedencia (herramienta, grupo, jornada, captura ISO y antigüedad en horas) queda en `rec.tier0.provenance` y el texto cita jornada y fecha.

### I1 — Reparación de `group_id` (`AGENT_GROUP_ID_REPAIR=off|on`, off por defecto)
- Con `off` el comportamiento es idéntico al anterior a la reparación (el test que había alterado vuelve a su expectativa original; `git diff` de `test_competition_resolution.py` vacío).
- Con `on`, mismas condiciones conservadoras (un solo grupo para el `team_id` en un `find_teams` de esta pregunta; nada por similitud; ni equipos ambiguos ni ids no mostrados; nunca se sobrescribe un `group_id` explícito).
- Trazabilidad: `ToolCallRecord.arguments` = argumentos **efectivos**; `original_arguments` = los del modelo (siempre); `repaired` = `{field, value, source, team_id, reason}`; `mcp_calls.repaired` y `CaseResult.repaired_calls`. El veredicto de herramientas del evaluador juzga los argumentos originales.
- Brazo `group_repair` para medirla sola. No se afirma que el fallo `prefetch-r4` quede resuelto (no hay traza).

### I2 — Dynamic Tools: ampliación por señales concretas
- **Ya no** se amplía por un error de herramienta (un error de datos o validación no es una herramienta ausente).
- Señales que amplían al conjunto completo (**una sola vez por pregunta**; después `_subset_active()` es falso): (a) el modelo pide una herramienta real que no se le declaró; (b) el modelo declara una limitación de herramientas sin cifras (`states_tool_limit`) mientras solo tiene un subconjunto (se añade un mensaje «ahora dispones de todas las herramientas» y se conservan todos los mensajes y resultados previos); (c) las tres reconsultas de guardarraíl que ya existían.
- Selección: «faltas/falta/amonestaciones» añaden las herramientas de actas y de partidos además de `search_rules`; los seguimientos, el foco y cualquier cambio de ámbito en conversación van con las 15. La política de goleadores no cambia: tabla oficial y actas se ofrecen juntas; la diferencia vive en el prompt y en las herramientas.
- Presets del evaluador: `dynamic_general`, `dynamic_actas`, `dynamic_rules`, `dynamic_scorers`, `dynamic_ambiguous` y `tier0_probe` (ids reales, comprobados estáticamente). **No hay caso de seguimiento en el evaluador** (todos son de una pregunta): ese comportamiento solo se comprueba offline.
- Coste de la ampliación (límite superior): una llamada LLM extra por pregunta que la dispare; solo con `AGENT_TOOLS=dynamic`.

### I3 — Telemetría de Tier 0
- `exec_tier=tier0` y `token_source=measured_zero`: **0 tokens MEDIDOS** (ni estimados ni «no informados»). `estimate_cost` da 0 USD si hay tarifa verificada.
- `arms_summary`: cuenta como medidos `provider` y `measured_zero`; añade la columna «origen de los tokens» (`n provider, n measured_zero, n estimated, n unreported`) y etiqueta cada caso (`0 medido: sin LLM`, `estimado`, `no informado`). `ab_summary.py` (Fase 1) no se ha tocado. `report.py` etiqueta el nuevo origen.

### Presupuesto global de `ab_arms.py`
- `--max-total-requests` (por defecto tandas × `--max-requests`): tope duro **global** de la ejecución (todos los brazos y tandas, reintentos incluidos y `--fill-missing`). Cada tanda recibe `min(--max-requests, lo que quede)`; se descuenta lo realmente usado (llamadas + reintentos de su `results.json`); si no hay resultados legibles se cobra TODO lo concedido; agotado el global no se lanza ninguna tanda más y el proceso termina con código 3 (y resumen).
- El tope por tanda es **duro** en el propio cliente (`BudgetedLLM`, en la ruta live del evaluador): antes de cada llamada lógica comprueba que, en el peor caso (1 + reintentos permitidos), no se superaría; si no, no envía nada. Ya no puede pasarse por la mitad de un caso. Los casos rechazados quedan `not_run` con «presupuesto de peticiones agotado» y `arms_summary` los distingue de los 429.
- El plan (`--plan`/sin `--yes`) no llama a nada y muestra: presupuesto global máximo aplicado, tandas totales y pendientes, tope por tanda, consumo ESTIMADO por tanda y por brazo (marcado como estimación, no tope), tokens ESTIMADOS y aviso si la estimación supera el global.
- Límites: la unidad es la petición HTTP del proveedor; el contador del cliente solo cubre llamadas hechas por el agente del evaluador (es la única vía de consumo que existe en `evaluate.py`). Una ejecución nueva de `ab_arms.py` tiene su propio presupuesto (el `manifest.json` registra lo gastado en ella).

## Brazos y presets nuevos
`group_repair`, `prefetch_dynamic_tier0` (la combinación de la etapa 6); ya existían `dynamic_tools`, `tier0`, `prefetch_dynamic`, `compact_prefetch`. Todos fijan **todas** las banderas (incluida `AGENT_GROUP_ID_REPAIR=off` salvo en `group_repair`), de modo que no se contaminan.

## Banderas (todas apagadas por defecto)
`AGENT_PROMPT_MODE=legacy`, `AGENT_PREFETCH=off`, `AGENT_TOOLS=full`, `AGENT_TIER0=off`, `AGENT_GROUP_ID_REPAIR=off`, `AGENT_ROUTER=shadow`, `AGENT_TELEMETRY=off`, `AGENT_TIER0_MAX_AGE_HOURS=72` (solo con Tier 0).

## Riesgos que permanecen
- Nada de lo nuevo se ha probado con Gemini: comportamiento real del modelo ante subconjuntos de herramientas (peticiones a herramientas no declaradas, coste de la ampliación), caché implícita (en el A/B real fue ≈9 % de la entrada), estilo y exhaustividad del texto del Tier 0.
- El dataset adversarial es sintético y de redacción propia: cubre las formas de la auditoría, no el lenguaje real de los usuarios. Con tráfico real habrá que ampliar la lista blanca con datos (telemetría saneada), no al revés.
- La cobertura real del Tier 0 será baja (20/1010 en el dataset de A) y depende de la frecuencia de ingesta frente a las 72 h.
- La frescura usa la hora del proceso del agente; si el reloj del servidor no es fiable el Tier 0 se abstiene por «captura en el futuro» solo con >1 h de desfase.
- El presupuesto global es por ejecución del script; no hay un contador persistente entre ejecuciones.
- `tests/integration` (PostgreSQL) sin ejecutar. 3 tests de `test_prompt_compact.py` fallan en Python 3.13 (entorno, no código).

## Ajustes tras la etapa 2 (cuota diaria agotada)
La etapa 2 se cortó con 67 errores 429 que pedían esperar ≈19,5 h: era la **cuota diaria** del proveedor, no el ritmo. Cambios (no alteran el comportamiento del agente; solo el manejo de ese error y los informes):
- `llm.py`: una espera pedida ≥ 600 s (`QUOTA_WAIT_THRESHOLD_S`) lanza `LLMQuotaError` (mensaje con `QUOTA_MARKER` = «cuota del proveedor agotada»), con independencia de los reintentos que queden. Esperas menores siguen igual que antes.
- `evaluate.py`: ante un caso `not_run` por cuota, **se detiene** y marca el resto como `not_run` («no se gastan más peticiones»); sale con el código `QUOTA_EXIT_CODE` = 4 (en `arms.py`) con el informe y `results.json` ya escritos.
- `scripts/ab_arms.py`: con código 4 cobra lo realmente usado, **no lanza más tandas ni relleno**, conserva lo completado (también en `--fill-missing`), registra `quota_exhausted` en el `manifest.json` con cuándo vuelve la cuota (según lo que pidió el servidor) y regenera el resumen. Termina con código 4.
- `arms_summary.py`: distingue **cuota agotada** (`[cuota×n]`, también en resultados antiguos por el texto «esperar Ns» ≥ 600 s) de un 429 por minuto (`[429×n]`); las medias por brazo (aciertos, llamadas, tokens por llamada) y las por caso **solo cuentan repeticiones completas/decisivas**; las filas incompletas se marcan «INCOMPLETA», se muestra la columna «sin ejecutar» y «—» en lugar de `None`.
- Comprobado sobre la carpeta real de la etapa 2: los 67 se clasifican como cuota y las medias salen de las 2 repeticiones completas (legacy 38 llamadas; prefetch 30).
- Tests: `tests/test_quota.py` (12). Suite offline en Python 3.12 y 3.11: 1085 pasan.
