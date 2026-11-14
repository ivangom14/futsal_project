# Benchmark multimodelo de Futsito

Compara modelos LLM con el agente Futsito **completo** (mismas herramientas MCP, mismas reglas, mismo snapshot congelado y mismo
reloj) y mide calidad, fiabilidad, coste y latencia. No es un framework nuevo: es una capa (`src/futsal/evals/bench/`) sobre
Futsito-Eval v1 (`docs/v2/evaluation.md`), que ya aporta el snapshot con huella, la verdad por SQL, el calificador determinista y el
runner sobre el agente real.

## Piezas

| Pieza | Qué es |
|---|---|
| `evals/bench/models.yaml` | registro de modelos: proveedor, modelo, adaptador, variable de la credencial, tarifas con fuente y fecha, límites, parámetros y `enabled` |
| `evals/bench/suites/bench-v1.json` | 39 casos (50 turnos) de `futsito-v1` dev con categoría y complejidad; 6 conversaciones encadenadas |
| `futsal.evals.bench.registry` | carga y valida el YAML, construye el cliente con el adaptador existente y calcula el coste de cada llamada |
| `futsal.evals.bench.runner` | ejecuta cada modelo con `run_eval`, presupuesto global en USD, reanudación y aislamiento de fallos |
| `futsal.evals.bench.analysis` / `report` | clasificación, estadísticas, rankings A-D, Pareto, router híbrido, incertidumbre → `summary.json` y `report.md` |
| `futsal.evals.bench.blind` | paquete ciego para Opus y revelado posterior |

**Agente evaluado: v2 por defecto** (`--agent v2`, el del frontal con `AGENT_ARCH=v2`; `--agent legacy` para comparar). Los dos
siguen el camino agente → MCP → API → BD: el benchmark monta la API y el MCP reales sobre el snapshot (con «hoy» = día del as_of), así
que ninguna herramienta, v1 o v2, puede consultar `DATABASE_URL`. Los casos que declaran qué haría una arquitectura capaz
(`expect.v2`, verdad SQL) se califican con esa expectativa cuando el agente es v2: debe responder donde v1 se abstenía. El modo de comprensión conversacional se elige con
`--context legacy|deterministic|llm`; por defecto se toma `AGENT_CONTEXT` del entorno (el mismo `.env` que el frontal). Arquitectura y
modo quedan registrados en cada ejecución y `--resume` se niega a mezclarlos. En modo `llm` el intérprete usa el mismo modelo
evaluado, así que sus llamadas también cuentan en coste y presupuesto.

Cambios en código existente (aditivos, sin cambiar conducta): `evals/runner.py` guarda el detalle por llamada (tokens, caché y
reintentos; los tiempos en `timing`), admite un subconjunto de casos y fija explícitamente arquitectura y modo de contexto (con v2,
expectativa v2; antes `AGENT_ARCH=v2` en el entorno hacía que los casos se calificaran con la expectativa de v1); `evals/datasets.py` reconoce las suites de `evals/bench/suites/`; `report_batch`,
agente, API y MCP no se tocan.

## Modelos soportados

Adaptadores existentes en `agent/llm.py`: **anthropic** (Messages API), **gemini** (generateContent) y **openai** (Responses API; vale
también para un endpoint compatible indicando `base_url`). Activados por defecto: `gemini-flash-lite` (gemini-3.5-flash-lite, el
actual) y `claude-haiku` (claude-haiku-5-5). Plantillas desactivadas: `claude-sonnet`, `gemini-flash`, `openai-luna`.

**Ollama u otro proveedor sin adaptador**: hace falta (1) una clase en `agent/llm.py` con `complete(system, messages, tools)` que
devuelva `LLMResponse` con `Usage` (tokens del proveedor) y (2) una línea en `ADAPTERS` de `bench/registry.py` (constructor,
parámetros admitidos, URL por defecto y si la entrada informada incluye la cacheada). No se ha implementado porque no hay modelo
local que medir todavía. Para un modelo local, pon tarifas `0` y anota el coste de máquina en `pricing.other`.

## Añadir un modelo (proveedor con adaptador)

Copia una entrada en `models.yaml`, cambia `id`, `model`, `version` y tarifas, y pon `enabled: true` (o pásalo con `--models`).
Comprueba con `python -m futsal.evals.bench models`. Nada más: el código no cambia.

## Configurar precios

En `pricing`, USD por 1M de tokens: `input`, `output` (incluye el razonamiento), `cached_input`, `cache_write`, `long_context`
(`above_input_tokens`, `input`, `output`, `cached_input`), `other` (otros conceptos, como notas) y obligatoriamente `source` y
`checked_at`. Lo desconocido va como `null`: el coste que dependa de ello queda **no disponible**, nunca cero. Con `free_tier: true`
el coste se calcula a tarifa de pago y se etiqueta como equivalente (lo facturado es 0). Cada ejecución congela la tarifa en
`bench.json`: cambiar el YAML después no altera informes anteriores.

## Introducir preguntas

Las respuestas esperadas **no se escriben a mano**: salen de la verdad SQL del snapshot. Para una pregunta nueva:
1. añade el caso a un YAML de `evals/datasets/futsito-v1/*.dev.yaml` (formato en `docs/v2/evaluation.md` §3: plantilla con
   entidades elegidas por criterio, conducta esperada `answer|clarify|abstain|offtopic`, hechos de la verdad, herramientas);
2. añade su `id` con `category` y `complexity` a `evals/bench/suites/bench-v1.json` (o crea otra suite, p. ej. `bench-v2.json`,
   y súbele la `version`); los informes registran nombre y versión de la suite.

## Ejecutar una comparación

Dos fuentes de datos:
- **Snapshot** (`--snapshot ID`, recomendado): copia congelada; todos los modelos y ejecuciones ven lo mismo y los resultados son
  comparables en el tiempo. Se crea con `python -m futsal.evals snapshot create --id ID --as-of AAAA-MM-DDTHH:MM` (necesita `pg_dump`).
- **Datos vivos** (`--live [--as-of …]`): tu BD actual en solo lectura, sin copia. El «hoy» es el mismo para todos los modelos y la
  huella de los datos se mide al empezar y al terminar: si el cron (u otra cosa) cambió algo, el informe lo avisa con las tablas
  afectadas. Para comparar legacy con v2, usa el mismo `--as-of` en las dos ejecuciones y lánzalas fuera de las horas del cron
  (entre semana solo corre a las 03:00).

```bash
python -m futsal.evals.bench models
```

```bash
python -m futsal.evals.bench plan --models gemini-flash-lite,claude-haiku
```

```bash
python -m futsal.evals.bench run --models gemini-flash-lite,claude-haiku --snapshot ref20261109 --dry-run --limit 5
```

```bash
python -m futsal.evals.bench run --models gemini-flash-lite,claude-haiku --snapshot ref20261109 --agent v2 --budget-usd 0.50 --confirm-live
```

```bash
python -m futsal.evals.bench run --models gemini-flash-lite,claude-haiku --live --as-of 2026-11-14T18:00 --agent v2 --budget-usd 0.50 --confirm-live
```

Opciones: `--agent v2|legacy` (v2 por defecto), `--context`, `--limit N` (los N primeros casos, intercalados por categoría), `--reps R` (repeticiones en orden rotado), `--suite`,
`--seed`, `--arm` (banderas del agente, iguales para todos), `--db` (BD ya restaurada), `--run-id`/`--resume` (completa lo
pendiente sin repetir), `--output-root` (por defecto `data/bench/`, fuera de Git). `report`, `blind`, `reveal` y `history`
regeneran informes, preparan la evaluación ciega y listan ejecuciones.

Resultado en `data/bench/<run_id>/`: `bench.json` (configuración, orden, tarifas, estado y gasto), `<modelo>/results.json` y
`report.md` (el informe de Futsito-Eval de cada modelo), `summary.json` y `report.md` (la comparación).

## Limitar el gasto

- `run` real exige `--confirm-live` **y** `--budget-usd`; sin claves o sin tarifas no arranca.
- Presupuesto global en USD: antes de cada llamada se comprueba que su peor caso razonable cabe (1,5 × la mayor entrada vista, mínimo
  8.000 tokens, más la salida máxima). Al agotarse, lo pendiente queda `not_run` y se completa con `--resume`.
- Tope duro de peticiones HTTP por modelo (`limits.max_requests`) y separación mínima entre peticiones (`limits.min_interval_s`).
- Sin concurrencia: un modelo tras otro. Reintentos acotados del adaptador (`LLM_MAX_RETRIES`); un 429 por cuota detiene ese modelo.
- `plan` estima peticiones, minutos y coste (normal y pesimista con salida × 5) sin llamar a nada; `--calibrate` usa lo medido antes.

## Qué mide

- **Calidad**: clases `correcta`, `parcial`, `incorrecta`, `alucinacion`, `error_tecnico`, `no_evaluable` (más `no_ejecutada`), con
  las reglas de `bench/analysis.py`; exactitud y alucinaciones con IC de Wilson; por categoría y complejidad; conversaciones
  encadenadas completas; uso de herramientas; diferencias pareadas entre modelos con bootstrap (semilla fija).
- **Coste** (calculado, no facturado): por llamada, por pregunta (media, mediana, p95), por pregunta correcta, por conversación y por
  conversación encadenada, por categoría, total, total sin caché, proyección a 100, 1.000 y 10.000 preguntas con su rango y supuestos;
  base de cada cifra (`provider`, `estimated`, `unavailable`).
- **Latencia**: p50/p95/media por pregunta sin las pausas del benchmark; llamadas LLM y MCP por pregunta; reintentos y errores.
- **Rankings**: A calidad, B coste por correcta, C velocidad, D calidad/precio con frontera de Pareto; recomendaciones conservadoras
  (mejor general, más económico aceptable, mejor en complejas y en sencillas) y router híbrido (cota de ahorro); avisos de muestra
  pequeña, costes incompletos, ejecuciones paradas, dry-run o snapshot sintético.

## Equidad

Mismo snapshot, reloj y herramientas; mismo brazo del agente y mismo prompt para todos; caché de prompts de Anthropic desactivada
(la caché implícita de Gemini se mide y se informa también el coste sin caché); orden de modelos barajado con semilla; casos en
orden rotado por repetición; mismo `max_output_tokens` (1024, el de producción). Diferencias por proveedor y no por modelo
(formato de la API, razonamiento contado dentro del límite de salida en Anthropic, 429) aparecen como errores técnicos, reintentos o
tokens de razonamiento: revísalas antes de atribuirlas a la calidad.

## Evaluación con Opus (sin API)

```bash
python -m futsal.evals.bench blind data/bench/<run_id>
```

Genera `blind/PROMPT.md`, `items.md`, `scoresheet.json` y `key.json` (letras barajadas por conversación con semilla). En Claude Code:

> Lee `data/bench/<run_id>/blind/PROMPT.md` y sigue sus dos fases. En la primera, valora a ciegas `items.md` y rellena
> `scoresheet.json` sin abrir `key.json`. En la segunda, ejecuta `python -m futsal.evals.bench reveal data/bench/<run_id>`, lee
> `blind/reveal.md` y `report.md`, y recomienda el modelo con mejor relación calidad/precio: calidad y fiabilidad primero, después
> coste y latencia, sin decidir por diferencias que los intervalos no sostienen.

`reveal` mide el acuerdo del juez con el calificador determinista y lista las discrepancias; el juicio de Opus complementa, no
sustituye, los resultados deterministas.

## Pruebas offline

`tests/evals/test_bench_unit.py` (sin BD) y `tests/evals/test_bench_run.py` (snapshot sintético, dos modelos de guion fijo por el
agente y el MCP reales): registro y validación, coste por llamada (caché, razonamiento, tramo largo, tarifas desconocidas),
presupuesto, clasificación, estadísticas, empates, Pareto, router, informe, evaluación ciega y revelado, parada por presupuesto y
reanudación sin repetir, fallo aislado de un modelo con la clave ocultada, dry-run sin claves y confirmación obligatoria.
