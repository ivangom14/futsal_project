# Arnés de evaluación por configuraciones (E0)

Un **brazo** es una combinación de banderas `AGENT_*` con nombre (`agent/arms.py`): `legacy`, `compact`, `prefetch`, `compact_prefetch`. Cada brazo fija **todas** las banderas (no hereda nada de tu
entorno) y apaga la telemetría. `scripts/ab_arms.py` ejecuta N brazos × repeticiones (orden rotado) sobre un preset de casos; `agent/arms_summary.py` resume por brazo, repetición y caso.

**No sustituye** a `scripts/ab_prompt_modes.py` + `agent/ab_summary.py` (legacy frente a compact de la Fase 1), que siguen intactos: una prueba compara su salida con un fixture oro y comprueba que
el resumen de N brazos coincide con él en legacy/compact.

## Naturaleza de los datos
- **MEDIDO:** resultado de cada caso, llamadas LLM y MCP, tokens que informa el proveedor (`token_source=provider`).
- **ESTIMADO:** cualquier cifra del plan antes de ejecutar y los tokens marcados como estimados («¡estimados!»).
- **SIMULADO:** `prompt_compare` (cambia el prefijo sobre trazas guardadas, no ejecuta nada). El resumen de brazos no simula nada.

## Plan y coste (`python scripts\ab_arms.py --arms legacy,prefetch --cases prefetch_probe`)
Muestra, sin llamar a nada: nº de tandas, **máximo global de ejecuciones de caso**, **tope duro** de peticiones (`--max-requests` × tandas, que sí se aplica), llamadas LLM **estimadas** (3,2 por caso) y la
cota teórica de diseño (7 por caso), tokens **estimados** (entrada a 4.626 por llamada y salida a 49, ambos medidos en tus ejecuciones anteriores; se pueden cambiar) y el coste **solo** si aportas
`--price-in`, `--price-out`, `--price-source` y `--price-date` (sin fuente y fecha no es una tarifa verificada). `--plan-evaluator` añade el plan del propio evaluador (sin LLM; necesita API y base de datos).

## Provenance
Cada `results.json` registra ahora `meta.agent_flags` (las banderas vigentes). `arms_summary` avisa si una tanda no se hizo con las banderas de su brazo o si es anterior a E0 y no se puede comprobar.
