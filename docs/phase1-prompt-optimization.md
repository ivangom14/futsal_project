# Optimización de tokens — Fase 1: prompt y descripciones MCP compactos

Alcance: reducir el prefijo que se reenvía en cada llamada al LLM (system prompt, regla de historial, cabeceras de contexto y descripciones de
las 15 herramientas) **sin quitar reglas**, detrás de una bandera. No hay selección dinámica de herramientas, operaciones de alto nivel, `run_plan`,
Tier 0 activo ni cambios en el router en sombra. Sin LLM real, sin migraciones, sin cambios en PostgreSQL, frontend, contratos MCP ni API.

## 1. Bandera y rollback
`AGENT_PROMPT_MODE=legacy|compact` (en el entorno o `.env`). **Por defecto `legacy`**: con la bandera ausente, vacía o con un valor desconocido se envían
exactamente los textos de antes (`tests/fixtures/prompt_legacy_golden.json`, comprobado byte a byte). La leen el agente (system, historial, cabeceras) y el
servidor MCP (descripciones; el subproceso hereda el entorno). Rollback = quitar la variable. `RunRecord.prompt_mode` y `totals.prompt_mode` registran el modo.
No se ha activado `compact` por defecto porque su efecto sobre las respuestas de Gemini no se puede comprobar sin una ejecución live (§5).

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

Recortes (todos con equivalente conservado; inventario de 25 reglas en `tests/test_prompt_compact.py`): lista de ámbito del núcleo (ya la enumera el aviso de ajeno),
«hay partidos reprogramados» y el aviso de `past_due` (queda en la herramienta y en una cláusula del prompt), «reformulando con el vocabulario…» (queda en
`search_rules`), receta de último/próximo duplicada en el parámetro `order`, «cítalas / dilo» duplicado en `search_rules`, repeticiones en `HISTORY_RULE`
y las cabeceras de contexto. **Criterio de goleadores** (decidido por el responsable) escrito explícitamente: «máximos goleadores» de un grupo o competición →
`get_scorer_table` (oficial); goles, árbitros, tarjetas y alineaciones de un partido, jornada, rival o jugador concreto → actas; no mezclar ambas sin decirlo.
Las descripciones de `get_scorer_table` (OFICIAL, tabla ACTUAL, sin desglose por jornada), `top_scorers` (ACTAS, «No es la tabla oficial») y `get_standings`
no cambian de significado. El caso del evaluador `cobertura_parcial_advertencia` ya decía «Según las actas importadas»: ningún caso ni dataset se ha modificado.

## 3. Medición (ESTIMADA: caracteres/3,5; el proveedor no desglosa el prefijo)
| Parte | legacy | compact | Δ |
|---|---|---|---|
| System prompt | 4.124 car. · 1.179 tok | 3.829 · 1.094 | −7,2 % |
| Regla de historial (solo con historial) | 863 · 247 | 730 · 209 | −15,4 % |
| Cabecera de contexto / de catálogo | 405 · 116 / 362 · 104 | 308 · 88 / 345 · 99 | −24,0 % / −4,7 % |
| Herramientas (canónico) | 7.416 · 2.119 | 7.046 · 2.014 | −5,0 % |
| Herramientas (cable Gemini) | 6.945 · 1.985 | 6.575 · 1.879 | −5,3 % |
| **Prefijo por llamada** (con 1.638 car. de contexto) canónico / cable Gemini | 3.766 / 3.632 tok | 3.549 / 3.414 | **−217 / −218 tok (−5,8 % / −6,0 %)** |

Con historial se ahorran ~38 tokens más por llamada. Reproducción de los 56 casos con tokens de la ejecución offline `20261025-145322` (commit `9d3b853`, base del
usuario; 124 llamadas LLM y 68 MCP, **idénticas**): 534.848 → 507.940 tokens de entrada estimados (**−5,0 %**; −217 a −651 por caso; por categoría entre −4,4 %
`reglamento` y −5,8 % `alcance`). Resultados individuales: `python -m futsal.agent.prompt_compare --cases data/agent/eval-last.json --json data/agent/phase1-compare.json`.

## 4. Objetivo del 15 %: NO se alcanza
El texto aporta −5,8 % (−217 tokens). Razones medidas: el núcleo son 1.701 car., de ellos 661 son literales obligatorios (aviso de ajeno y cinco fórmulas) que los
guardarraíles y las pruebas exigen; el prompt anterior ya venía comprimido por la Fase 11; la mayor parte de las herramientas (≈4.500 car.) es estructura de
esquema que no se puede tocar sin cambiar contratos; y `"default": null` (≈470 car.) ya lo descarta el adaptador de Gemini. Para llegar al 15 % (≈565 tokens) haría falta
quitar reglas o **incluir módulos y herramientas solo cuando hagan falta** (Fase 2): los módulos `tables`/`matches`/`rules` suman 1.761 car. (~500 tokens) y las herramientas de
un solo dominio, más. No se ha intentado por debajo de eso porque habría sacrificado reglas (precisión, IDs, fuentes) sin poder comprobarlo.

## 5. Lo que NO se ha podido verificar (y cómo hacerlo)
- **Precisión de Gemini con el prompt compacto**: sin LLM real no se puede medir. No hay datos de aciertos de los 65 casos antes/después. Los flujos guionizados
  (LLM simulado) no dependen del texto del prompt, así que «0 regresiones» en offline no demostraría nada; por eso no se presenta como tal.
- **El evaluador offline real** necesita PostgreSQL (`load_truth`), que no hay en este entorno.
- Procedimiento (lo ejecutas tú, con presupuesto): misma base y mismo suite en ambos modos, tomando `--max-requests` del plan que imprime `--plan`:
  `set AGENT_PROMPT_MODE=legacy` → `python -m futsal.agent.evaluate --mode live --confirm-live --suite all --output data\agent\eval-legacy.json`;
  `set AGENT_PROMPT_MODE=compact` → mismo comando con `--output data\agent\eval-compact.json`;
  `python -m futsal.agent.evaluate --compare data\agent\eval-legacy.json data\agent\eval-compact.json --fail-on-regression`. Aceptar `compact` solo si no hay
  regresiones por caso (hay variabilidad entre ejecuciones: repetir los fallos), las llamadas LLM no suben y los tokens reales del proveedor bajan.

## 6. Pruebas
`tests/test_prompt_compact.py` (62): legacy byte a byte contra el fixture oro (prompt, historial, cabeceras, 15 herramientas), bandera por defecto/desconocida/entorno, contratos
de esquema idénticos, cada parte más pequeña, inventario de reglas (una prueba por regla), salvaguardas de `HISTORY_RULE` y de las cabeceras, descripciones que distinguen
herramientas parecidas, coherencia oficial/actas, mismo flujo LLM/MCP en ambos modos, router en sombra y `exec_tier=tier2` sin cambios, y la reproducción. Suite: 741 pasan.

## 7. Limitaciones
Estimaciones por caracteres; el cable real de Gemini/Anthropic puede diferir. Reproducción basada en una ejecución offline de otro commit (se rebasa el prefijo, no se vuelven a ejecutar
los casos). `compact` queda desactivado por defecto hasta validarlo con LLM real. Las cifras de `legacy` (3.766) usan el contexto de 1 grupo de esa ejecución; con catálogo de ~25 grupos el
contexto es mayor y el porcentaje, algo menor.
