# Optimización de tokens — Fase 1: prompt y descripciones MCP compactos

Alcance: reducir el prefijo que se reenvía en cada llamada al LLM (system prompt, regla de historial, cabeceras de contexto y descripciones de
las 15 herramientas) **sin quitar reglas**, detrás de una bandera. No hay selección dinámica de herramientas, operaciones de alto nivel, `run_plan`,
Tier 0 activo ni cambios en el router en sombra. Sin LLM real, sin migraciones, sin cambios en PostgreSQL, frontend, contratos MCP ni API.

## 1. Bandera y rollback
`AGENT_PROMPT_MODE=legacy|compact` (en el entorno o `.env`). **Por defecto `legacy`**: con la bandera ausente, vacía o con un valor desconocido se envían
exactamente los textos de antes (`tests/fixtures/prompt_legacy_golden.json`, comprobado byte a byte). La leen el agente (system, historial, cabeceras) y el
servidor MCP (descripciones; el subproceso hereda el entorno). Rollback = quitar la variable. `RunRecord.prompt_mode` y `totals.prompt_mode` registran el modo.
No se ha activado `compact` por defecto porque su efecto sobre las respuestas de Gemini no se puede comprobar sin una ejecución live (§6).

## 2. Qué se ha cambiado
- `agent/prompts.py` (nuevo): prompt dividido en **núcleo** (identidad, alcance, ajeno, seguridad, errores, cinco fórmulas «sin información») y cuatro
  **módulos** (`tables`, `matches`, `entities`, `rules`); el seguimiento conversacional sigue siendo `HISTORY_RULE` (ya condicional). **Los módulos se incluyen
  siempre**: no se puede garantizar equivalencia con inclusión condicional mientras el router tenga un 80 % de precisión y no haya LLM con el que comprobarlo.
  `compact_system(modules=…)` deja la estructura lista para la Fase 2.
- `mcp_server/descriptions.py` (nuevo) + `server.py`: `create_server(descriptions=…)`; las descripciones compactas solo sustituyen **texto** (10 claves). Los
  esquemas (nombres, tipos, enum, límites, `required`) son idénticos (prueba).
- `agent.py`: `PROMPT_SETS`, `resolve_prompt_mode`, `Agent(prompt_mode=…)`; `trace.py`: `prompt_mode`; `evaluate.py`: la detección de «repite las instrucciones»
  vigila ambos prompts.
- `agent/prompt_compare.py` (nuevo): mide ambos modos y reproduce los casos de una ejecución guardada con cada prefijo.

Recortes (equivalente conservado; inventario de reglas en `tests/test_prompt_compact.py`): lista de ámbito del núcleo (ya la enumera el aviso de ajeno),
«hay partidos reprogramados» y el aviso de `past_due` (queda en la herramienta y en una cláusula del prompt), receta de último/próximo duplicada en el parámetro
`order`, «cítalas / dilo» duplicado en `search_rules`, repeticiones en `HISTORY_RULE` y en las cabeceras de contexto.

**Criterio definitivo de goleadores** (decidido por el responsable tras la primera comparación live; reemplaza al borrador anterior):
- Ranking o «máximos goleadores» de un grupo o competición → `get_scorer_table` (tabla oficial RFFM).
- Goles de un jugador concreto → `find_players` y actas.
- Goles, árbitros, tarjetas y alineaciones de un partido, de un rival o de los partidos de una jornada concreta (lo ocurrido en esos partidos) → actas.
- Máximo goleador o clasificación acumulada «tras la jornada N» → NO se reconstruye con actas: la tabla oficial refleja solo la situación actual y no permite verificar ese histórico.
- Nunca se mezclan tabla oficial y actas en un ranking sin decirlo.
Las descripciones de `get_scorer_table` (OFICIAL, tabla ACTUAL, sin desglose por jornada), `top_scorers` (ACTAS, «No es la tabla oficial») y `get_standings` no cambian de
significado, y ningún caso del evaluador se ha modificado (`acta_jugador_inexistente` y `acta_jugador_ambiguo` incluidos). Solo se aplica en `compact`; `legacy` no recibe estas reglas.

## 3. Medición (ESTIMADA: caracteres/3,5; el proveedor no desglosa el prefijo)
Tras la corrección del criterio de goleadores y de la instrucción de reformular las consultas de reglamento:

| Parte | legacy | compact | Δ |
|---|---|---|---|
| System prompt | 4.124 car. · 1.179 tok | 4.170 · 1.192 | **+1,1 %** (el prompt ya NO ahorra) |
| Regla de historial (solo con historial) | 863 · 247 | 730 · 209 | −15,4 % |
| Cabecera de contexto / de catálogo | 405 · 116 / 362 · 104 | 308 · 88 / 345 · 99 | −24,0 % / −4,7 % |
| Herramientas (canónico) | 7.416 · 2.119 | 7.046 · 2.014 | −5,0 % |
| Herramientas (cable Gemini) | 6.945 · 1.985 | 6.575 · 1.879 | −5,3 % |
| **Prefijo por llamada** (con 1.638 car. de contexto) canónico / cable Gemini | 3.766 / 3.632 tok | 3.647 / 3.512 | **−119 / −120 tok (−3,2 % / −3,3 %)** |

Antes de la corrección el prefijo bajaba −217 tokens estimados (−5,8 %) con un system prompt de 3.829 caracteres; las reglas nuevas (+341 car.) consumen casi todo el ahorro del
prompt, y el que queda viene de las descripciones de herramientas, el historial y las cabeceras. Reproducción de los 56 casos con tokens de la ejecución offline `20261025-145322`
(commit `9d3b853`; 124 llamadas LLM y 68 MCP, **idénticas**): 534.848 → 520.092 tokens estimados (**−2,8 %**). `python -m futsal.agent.prompt_compare --cases data/agent/eval-last.json`.

**Calibración con tokens REALES** (primera comparación live del usuario, `gemini-3.5-flash-lite`, 63 casos comunes): con el flujo idéntico, `compact` ahorró exactamente **163 tokens
por llamada** (versión anterior a la corrección), es decir, ~75 % de los 217 estimados: Gemini cuenta ≈ 4,7 caracteres por token, no 3,5. El porcentaje sí se confirma (~5,8 % de un
prefijo real de ~2.800 tokens). Como el prefijo es ~60 % de lo que se envía en cada llamada, el ahorro determinista fue ≈ 3,5 % del total de entrada de la suite; el −6,8 % observado
incluía variación de flujos (p. ej. `regla_duracion_mitad`, −36.579 tokens y −3 llamadas). Con la corrección, el ahorro por llamada esperado baja a ≈ 90 tokens reales (estimación).

## 4. Objetivo del 15 %: NO se alcanza
El texto aporta −3,2 % tras la corrección (−5,8 % antes de ella). Razones medidas: el núcleo son ~1.700 car., de ellos 661 son literales obligatorios (aviso de ajeno y cinco fórmulas) que los
guardarraíles y las pruebas exigen; el prompt anterior ya venía comprimido por la Fase 11; la mayor parte de las herramientas (≈4.500 car.) es estructura de esquema que no se puede
tocar sin cambiar contratos; y `"default": null` (≈470 car.) ya lo descarta el adaptador de Gemini. Para llegar al 15 % haría falta quitar reglas o **incluir módulos y herramientas
solo cuando hagan falta** (Fase 2). No se ha intentado por debajo de eso porque habría sacrificado reglas (precisión, IDs, fuentes) sin poder comprobarlo.

## 5. Primera comparación live y corrección
Primera comparación (una ejecución por modo, 63 casos comunes): 46 → 50 pass, 10 casos mejoran y 6 empeoran; tokens reales de entrada −6,8 %, llamadas LLM 161 → 160. Veredicto del
evaluador: `REGRESION`. **No concluyente**: con una sola ejecución por modo no se separa el efecto del prompt del ruido entre ejecuciones (variación conocida de ±5 casos).
Causas probables (hipótesis, sin trazas): `tabla_goleadores_por_jornada_no_verificable` (reconstruyó con actas por la redacción «jornada» del criterio) y `acta_jugador_inexistente` (usó
la tabla oficial para un jugador concreto) → corregidas con el criterio definitivo de §2; `circular27_comunica`, `circular10_anula` y `circular10_saque_meta_coloquial` (RAG) → se restaura
«usa search_rules, reformulando con el vocabulario del reglamento» en el módulo de reglamento (se había quitado del prompt); `tabla_goleadores_nombre_repetido` → sin causa clara.

## 6. Comparación controlada con repeticiones (la ejecuta el usuario)
Los 6 casos que empeoraron y los 10 que mejoraron, 3 repeticiones por modo, mismo modelo y condiciones, tandas alternadas (L C · C L · L C) y resultados separados por modo y repetición:
`python scripts/ab_prompt_modes.py` (solo muestra comandos y coste), `--plan` (plan del evaluador, sin LLM) y `--yes` (ejecuta; consume cuota). Coste estimado: 96 ejecuciones de caso,
~307 llamadas al LLM (rango 192-480), tope duro 600 (100 por tanda), ~27 min con la pausa de 4 s. Resultado en `data/agent/ab/`: `<modo>-r<n>.json`, una carpeta por tanda (informe y
trazas crudas y saneadas), `manifest.json`, `summary.md` y `summary.json` (`python -m futsal.agent.ab_summary data\agent\ab`). Por caso y repetición conserva: pass/fail, herramientas,
llamadas LLM y MCP, tokens reales de entrada y salida (marca los estimados), motivo del fallo; por modo, los casos con resultado variable entre repeticiones; y comprueba que cada tanda se
hizo realmente en su modo (longitud del system prompt registrada). Veredicto por caso: regresión consistente (compact ≥ 0,5 por debajo de legacy), mejora consistente, variable (ruido) o igual.
Criterio para considerar `compact`: ninguna regresión consistente, llamadas LLM sin subir y tokens reales de entrada por debajo de `legacy`.

## 7. Pruebas
`tests/test_prompt_compact.py` (67): legacy byte a byte contra el fixture oro, bandera por defecto/desconocida/entorno, contratos de esquema idénticos, inventario de reglas (una
prueba por regla, incluido el criterio definitivo de goleadores y la instrucción de reformular), salvaguardas de `HISTORY_RULE` y de las cabeceras, descripciones que distinguen
herramientas parecidas, mismo flujo LLM/MCP en ambos modos, router en sombra y `exec_tier=tier2` sin cambios. `tests/test_ab_summary.py` (12): orquestador (nada sin `--yes`,
modo solo por entorno, alternancia, reanudación, parada ante error) y resumen (repeticiones separadas, variabilidad, tokens reales frente a estimados, modo incorrecto). Suite: 758 pasan.

## 8. Limitaciones
Estimaciones por caracteres (el real es ~25 % menor en tokens absolutos). Reproducción basada en una ejecución offline de otro commit. Precisión de Gemini con `compact` aún sin
demostrar: la comparación controlada está preparada, no ejecutada. `compact` sigue desactivado por defecto. Las cifras de `legacy` usan el contexto de 1 grupo; con ~25 grupos el contexto
es mayor y el porcentaje, algo menor.
