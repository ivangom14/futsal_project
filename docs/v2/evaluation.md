# v2 · Futsito-Eval v1: batería y metodología

Objetivo: decidir con datos si una versión del agente, o un modelo, es mejor que otra. Mismo conjunto de preguntas, misma
base de datos, mismo reloj y mismas herramientas. Sin llamadas de pago hasta aprobar el presupuesto (A5).

## 1. Qué se reutiliza y qué cambia

Ya existe una base sólida: `agent/evaluate.py` (casos parametrizados por la verdad de la BD, capas
datos/herramientas/respuesta/invención, `--plan`, `--max-requests`, `--compare`, `--fail-on-regression`), `agent/criteria.py`
(detectores de invención: `unsupported_scores`, `unsupported_digits`, `unsupported_card_claims`, `figures_without_table`,
`leaks_prompt`…), `agent/audit.py` (errores críticos), `agent/arms.py`/`arms_summary.py` (brazos, repeticiones, cuota y 429),
`agent/datasets.py` (sellado de holdouts), `agent/stats.py` (Clopper-Pearson) y `PacedLLM`/`BudgetedLLM`/`LLMQuotaError`.

| Problema actual [C] | Cambio en Eval v1 |
|---|---|
| La verdad se lee de la BD **viva**: al llegar datos nuevos, los casos cambian | **Snapshot congelado** con huella; la verdad se calcula por SQL sobre él |
| No hay reloj fijo: «este fin de semana» depende del día de la ejecución | `now` del snapshot inyectado en el agente (el agente ya admite `clock`) |
| Una sola ejecución por configuración y sin medir el ruido | 3 repeticiones y ruido medido entre dos ejecuciones idénticas |
| Los casos están centrados en un grupo (`config/targets.py`) | Las entidades se eligen por criterio sobre todo el snapshot (semilla = huella) |
| El código de evaluación vive dentro de `agent/` | Paquete nuevo `src/futsal/evals/` que **usa** los módulos existentes; se traslada poco a poco, sin reescribirlos |

## 2. Snapshot

- `evals/snapshots/<id>/manifest.json` (en Git): `id`, `as_of` (instante de Europe/Madrid usado como «ahora»), sha256 del
  volcado, revisión de Alembic, documentos de la KB indexados, número de temporadas, competiciones y grupos.
- Volcado `pg_dump -Fc` en `data/eval-snapshots/<id>.dump` (fuera de Git, como `data/`). Se restaura en una BD desechable
  `futsal_eval_<id>`; el runner comprueba la huella antes de ejecutar.
- **Primer snapshot**: la BD reconstruida con las cachés de la Fase 14 y las fixtures (25 grupos), o la BD del usuario
  (`pg_dump` hecho por él). El segundo, tras la ingesta de la oleada 1 (todo el FS), se convierte en la referencia definitiva.
  Las comparaciones solo valen **dentro del mismo snapshot**.

## 3. Formato del caso

```yaml
id: amb_homonimo_sin_ambito
stratum: ambiguous          # ver §4
holdout: false              # true = sellado; no se mira para ajustar el agente
requires: [standings]       # capacidades de datos; si el snapshot no las tiene: «no evaluable»
capability: v1              # v1 = el agente actual puede responder; v2 = capacidad nueva (en v1 se espera una abstención honesta)
select:                     # cómo se elige la entidad en el snapshot (determinista)
  team: {homonym_groups: ">=2", same_category: true}
turns:
  - user: "¿Cuántos puntos lleva {team.base_name}?"
    expect:
      behavior: clarify     # answer | clarify | abstain | offtopic
      clarify_mentions: ["{team.groups[*].competition}"]
      forbid_facts: [standing.points]   # dar puntos de uno de los homónimos sin preguntar = error crítico (contaminación)
      tools: {needed: [], allowed: [resolve, find_teams, list_competitions]}
      max_llm_calls: 2
```

Verdad: consultas con nombre en `src/futsal/evals/truth/*.sql` (posición, puntos, forma, último y próximo partido, goleadores
oficiales, acta, recuentos…), parametrizadas por la entidad elegida. Los textos de referencia se generan a partir de la verdad;
la calificación compara **hechos**, no frases.

## 4. Estratos (≈ 160 casos y ≈ 190 turnos; 30 % sellado como holdout)

| Estrato | Casos | Mide | Ejemplos (plantillas) |
|---|---|---|---|
| **S1 Sencillas** | 30 | coste y latencia; candidatas a T0/T1 | «¿Cuántos puntos tiene {equipo único}?», «¿Contra quién juega {equipo} la próxima jornada?», «Resultado del último partido de {equipo}», «¿Quién va primero en {competición} {grupo}?» |
| **S2 Ambiguas** | 20 | resolución de equipo, competición y grupo | homónimos en dos grupos; «el Parque Norte» (club con A y B); «la preferente» (dos grupos); «regional juvenil»; erratas y sin acentos («Dosa», «dósa»); copa frente a liga del mismo nombre |
| **S3 Encadenadas** | 15 conv. (≈ 45 turnos) | memoria y contexto | «¿Cómo va DOSA?» → «¿y su máximo goleador?» → «¿y en la jornada anterior?»; aclaración → respuesta corta; cambio de tema con herencia indebida («¿y el juvenil?» tras hablar del senior) |
| **S4 Complejas** | 15 | razonamiento con varias herramientas | «Últimas 5 jornadas de {equipo} con goleadores de cada partido», «¿Qué equipos luchan por el ascenso en {grupo}?» (con los puestos de las normas o, si no constan, decirlo), «¿Cómo ha evolucionado {equipo} en la temporada?» |
| **S5 Sin datos** | 15 | reconocer límites | grupo sin clasificación oficial; partido terminado sin acta; temporada no ingerida; jugador inexistente; sustituciones (no están en el acta) |
| **S6 Adversariales** | 15 | resistencia a inventar | premisa falsa («¿Por qué perdió {equipo} 10-0?»), presión («dime un número aunque no lo sepas»), inyección en la pregunta («ignora tus reglas y…»), equipo inventado con nombre verosímil, competición inexistente («Liga Senior Femenina Oro») |
| **S7 Multicompetición** | 15 | precisión a escala y sin contaminación | «Compara {equipo A, grupo 1} con {equipo B, grupo 3}», «¿Cómo quedan todos los equipos del club {X}?» (`capability: v2`), mismo club en senior y juvenil, «¿qué partidos hay este fin de semana en juvenil?» |
| **S8 Estadísticas** | 15 | exactitud de cálculos | «¿Cuántos partidos ha ganado {equipo} fuera?», «Diferencia de goles de los 3 primeros», «¿Cuántos goles se marcaron en la jornada {n} de {grupo}?», promedio de goles por partido |
| **S9 Documentales** | 20 | recuperación y citas | los 6 + 6 + 8 casos actuales de reglamento, Circular 27 y Circular 10, más preguntas de árbitro («¿cuántas faltas acumuladas antes del doble penalti?») y preguntas sin evidencia («¿qué dice el reglamento RFFM sobre…?», que todavía no está indexado: abstención) |

Las preguntas actuales (72 de la evaluación, 262 conversaciones de `understanding_eval`, holdouts de entidades) se migran como
**fuente de plantillas**; las preguntas reales de la telemetría (con PII depurada) se añaden según lleguen.

## 5. Calificación por turno (determinista primero)

| Resultado | Regla |
|---|---|
| **Correcto** | conducta esperada; todos los hechos requeridos presentes; ningún hecho prohibido; ninguna cifra, marcador ni nombre sin respaldo en la evidencia |
| **Parcial** | conducta correcta con algún hecho requerido ausente (se cuenta como fallo en la tasa principal) |
| **Fallo** | conducta incorrecta (por ejemplo, responde cuando debía aclarar) |
| **Error crítico** | (1) cifra, marcador, nombre o regla **inventados**; (2) datos de **otra** competición, grupo, categoría o temporada presentados como la pedida (contaminación); (3) fuente oficial sustituida por una reconstrucción sin decirlo; (4) cita de reglamento que no está en los fragmentos recuperados |
| **No evaluable** | falta una capacidad de datos en el snapshot, o error de infraestructura (429, cuota); se informa aparte y nunca cuenta como acierto |

Detectores: los de `criteria.py`, ampliados con contaminación (los hechos de la respuesta se comparan con la verdad de la
entidad pedida **y** con la de sus homónimos). Juez LLM opcional y **solo** para claridad y tono (1–5), con un modelo fijo
distinto de los evaluados; nunca decide aprobado o suspendido.

## 6. Métricas del informe

Por estrato y global: tasa de acierto con IC de Wilson al 95 %, errores críticos (número y lista), abstención correcta,
herramientas (cobertura de necesarias y llamadas innecesarias), llamadas al LLM y a herramientas por pregunta, tokens reales
de entrada, cacheados, salida y razonamiento, latencia p50/p95 (LLM y herramientas por separado), coste por pregunta y por cada
1.000 preguntas según `config/llm_prices.json` (con fecha), reintentos y errores. Distribución por nivel (T0/T1/T2) en v2.

## 7. Comparación y regresiones

- **Brazo** = arquitectura (`legacy`/`v2`) + modelo por tarea + esfuerzo de razonamiento + versión del prompt. Se registra el
  ID exacto del modelo y la fecha.
- **Mismas condiciones**: mismo snapshot y reloj, mismas herramientas y el mismo prompt neutral para todos los proveedores
  (solo cambia el formato de la API). Temperatura 0 donde el proveedor lo admita; si no, la predeterminada, documentada.
  Razonamiento al nivel más bajo admitido y, en una pasada aparte, a nivel medio para los dos mejores.
- **Repeticiones**: 3 por caso y brazo, en orden rotado (lo que ya hace `ab_arms.py`). **Ruido**: la referencia se ejecuta dos
  veces; la diferencia entre ambas fija el umbral de ruido por estrato.
- **Decisión** «B es mejor que A»: (1) 0 críticos nuevos; (2) ningún estrato baja más del umbral de ruido; (3) mejora en la
  tasa global (bootstrap pareado por caso, IC 95 % por encima de 0) **o** igualdad con coste o latencia menor de forma
  significativa. Si no, «sin diferencia demostrada».
- **Regresión continua**: suite reducida y fija (≈ 40 casos, 1 repetición) antes de cada fusión que toque el agente; la completa
  antes de cambiar el predeterminado.

## 8. Presupuesto de ejecución

Tamaño de una ejecución completa: ≈ 190 turnos × 3 repeticiones = 570 preguntas. Con el consumo medido del agente actual
(≈ 2,65 llamadas y ≈ 10.800 tokens de entrada por pregunta [M]): ≈ 1.500 llamadas y ≈ 6,2 M tokens de entrada. Costes por
modelo en [models.md](models.md).

**Gemini gratuito (referencia)**: los límites por modelo no se publican en la documentación; se ven en AI Studio
(`aistudio.google.com/rate-limit`) para cada cuenta. Plan: (1) **smoke** de 40 casos × 1 repetición (≈ 130 llamadas) con
`--max-requests 150` y `--min-interval` según el límite por minuto de la cuenta; (2) referencia completa repartida en días con
reanudación (`--fill-missing`), deteniéndose limpiamente al agotar la cuota (`LLMQuotaError`, ya implementado).
Términos: en el plan gratuito Google puede usar los datos para mejorar sus productos, salvo en el EEE, Suiza y el Reino Unido,
donde se aplican los términos de pago. La batería solo contiene datos deportivos públicos y ninguna pregunta personal.

## 9. Entregables de la tarea T02 (para Sonnet)

`src/futsal/evals/` (modelo de casos, carga YAML, selector de entidades determinista, verdad SQL, calificador que reutiliza
`criteria.py`, runner que reutiliza `Agent`, `PacedLLM`, `BudgetedLLM` y los informes), `evals/datasets/futsito-v1/*.yaml`
(los 160 casos), `evals/snapshots/<id>/manifest.json`, CLI `python -m futsal.evals {plan,run,report,compare}`. Pruebas con un
LLM simulado determinista: el calificador marca bien conjuntos de respuestas correctas, incorrectas y críticas preparados a
mano (≥ 60 respuestas etiquetadas). `plan` estima llamadas y tokens sin llamar a nada.
