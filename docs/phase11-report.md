# Fase 11 — Optimización de tokens del agente: informe comparativo

Rama `claude/tender-rubin-rsz9uv`. **Ningún ahorro está medido en Gemini**: lo medido con Gemini es solo la línea base
(15/10/2026); el resto son estimaciones offline y una proyección. La validación online queda PENDIENTE.

## 1. Línea base real (commit `aeb29e4`, `gemini-3.5-flash-lite`, informe + traza saneada comprobados)
| Dato | Valor |
|---|---|
| Casos | 29: 26 pass, 1 fail, 2 `not_evaluable`, 0 sin ejecutar |
| Tokens de entrada / salida (reales) | 172.078 / 3.650 |
| Llamadas al LLM / llamadas a herramientas MCP | 58 / 32 |
| Reintentos, razonamiento, caché | 0, 0, 0 |
| Latencia total | 228,4 s, **incluye ~160 s de pausa artificial** (`--min-interval 4`): mediana por llamada 3,86 s con pausa vs 1,07 s sin ella (smoke) |
| Tiempo de herramientas | 2,1 s |

## 2. Diagnóstico (datos observados en la traza)
1. **Prefijo fijo repetido en cada llamada: 83 % del total.** Primera llamada de cada caso: 2.461-2.493 tokens; ×58 = 143.366.
   Desglose estimado por caracteres: herramientas ~60 %, instrucciones ~28 %, contexto ~12 %.
2. **Resultados de herramientas e historial: 17 %** (28.712 tokens). Mayores: recuento global (+5,7k, 40 filas de
   `list_matches`), próximo partido/campo y recuento de equipo (+4,4k a +4,8k).
3. **Llamadas evitables:** `list_teams` en tarjetas (el contexto no decía que los IDs de equipo ya figuraban),
   la reconsulta en `jornada_inexistente` y `get_match` en `proximo_campo`.
4. **Caché:** 2.006/2.012/4.018 tokens en algunas ejecuciones y 0 en otras (caché implícita probabilística). La documentación
   consultada indica caché implícita desde la serie 2.5 y mínimo de 4.096 tokens para los Flash 3.x; **no se pudo
   confirmar el de Flash-Lite**. Con ~2k de prefijo la caché explícita no es viable: no se añade.
5. **Suelo de llamadas:** cada caso con herramientas necesita ≥2 llamadas (elegir herramienta y responder): ~55 en total.

## 3. Cambios
- **Tarjetas:** el encabezado del contexto ahora dice que los IDs de grupo, jornada y equipo ya figuran y que `list_teams`/`find_players`
  son solo para comprobar que algo no figura. La lista de herramientas permitidas del caso **no se ha relajado**.
- **Prefijo:** prompt de sistema 2.468 → 2.102 caracteres (todas las reglas conservadas); descripciones de herramientas más
  breves; `slim_tools` (`anyOf[X,null]` → X, sin sangrías); contexto con rango de jornadas cuando `round_id` = número.
- **Resultados:** `compact_result` descarta `round_id` y valores nulos (los `false`/0 se conservan).
- **`list_matches(limit=N)`** (API y MCP, retrocompatible, sin cambios de esquema): `count` = total de coincidencias, `shown` = filas devueltas.
  Para contar basta `limit=1`.
- **Instrumentación:** la pausa de ritmo se registra aparte (`paced_ms`) y se resta de la latencia; la traza guarda
  `prefix_chars` (sistema/herramientas/contexto) y el informe lo muestra.
- **Criterios nuevos (10.4):** cifras «X de Y partidos» deben cuadrar con `coverage`.
- **Descartado:** formato tabular de filas (≈ −5 % del total, requiere reescribir evaluador y riesgo de interpretación),
  retirar `list_competitions`/`list_groups` del esquema (rompería preguntas legítimas), caché explícita.

## 4. Comparación offline (misma réplica local: 182 partidos, 14 equipos, 13 actas; tokens ESTIMADOS ~3,5 car./token, LLM guionizado)
| | Antes (`aeb29e4`) | Después |
|---|---|---|
| Casos | 23 pass, 0 fail, 6 `not_evaluable` | 23 pass, 0 fail, 6 `not_evaluable` |
| Tokens de entrada (est.) | 161.756 | **135.162 (−26.594; −16,4 %)** |
| Prefijo de una llamada (est.) | 2.508 | 2.128 (−15 %) |
| Llamadas LLM / herramientas | 56 / 32 | 57 / 33 (+1: el recuento de equipo ahora usa 2 consultas con `limit=1`, como hace el modelo real) |
| Regresiones | — | ninguna |

Mayor ahorro por caso: recuento global −5.726; próximo campo −1.931; actas −1.1k a −1.2k; el resto ~−760.
**Proyección sobre Gemini** (si el comportamiento del modelo no cambia): 172.078 × 0,836 ≈ **~144k de entrada (≈ −16 %)**.
No es una medición.

## 5. Objetivo propuesto
El objetivo orientativo de 70-90k **no es viable sin perder calidad**: con ≥55 llamadas y un prefijo mínimo razonable de
~2k tokens, el prefijo por sí solo suma ~110k. Objetivo realista y justificado: **≤145k de entrada (−16 %)**, con 135-145k
esperable; bajar de ahí exigiría quitar herramientas del esquema o reglas del prompt, con riesgo de precisión.
Llamadas al LLM: 55-58 (el suelo es 55). Latencia: se comparará sin pausas (`latency_ms`).

## 6. Evolución de `acta_tarjetas_codigo`
Baseline: pass en respuesta/datos/invención, **fail en herramientas** por `list_teams`. Causa probable (hipótesis):
el contexto no incluía `list_teams` entre las llamadas innecesarias. Corregido sin tocar la lista permitida; test de
regresión que mantiene `list_teams` como «herramienta no esperada». Offline pasa; online PENDIENTE.

## 7. Limitaciones y siguientes pasos
- Tokens/llamadas offline estimados; el comportamiento de Gemini (uso de `limit=1`, número de llamadas) no está medido.
- El uso de `limit` depende de que el modelo lo elija: si no, solo se pierde el ahorro del recuento (~5k), no la precisión.
- Zona horaria de las fechas (`+00:00`) sin confirmar; fuera del alcance de esta fase.
- Siguiente paso: smoke online con presupuesto, luego la suite completa, y `--compare` contra `20261015-205505-live-all`/la ejecución de `aeb29e4`.

## 8. Primera medición online de la Fase 11 (commit `b1d62d3`, 15/10/2026 21:34, `gemini-3.5-flash-lite`)
| | Línea base `aeb29e4` | `b1d62d3` | Δ |
|---|---|---|---|
| Entrada (real) | 172.078 | 145.622 | **−26.456 (−15,4 %)** |
| Salida (real) | 3.650 | 3.252 | −398 |
| Llamadas LLM / herramientas MCP | 58 / 32 | 54 / 28 | −4 / −4 |
| Caché | 0 | 4.012 | |
| Casos | 26 pass, 1 fail, 2 n.e. | 23 pass, 4 fail, 2 n.e. | **4 regresiones, 1 corrección** |
| Latencia sin pausas | no comparable | 58,4 s (herramientas 1,9 s) | |

La proyección offline (−16 %) se cumplió (−15,4 %), **pero 8.077 de esos tokens vienen de tres casos que dejaron de consultar herramientas**
y fallaron; el ahorro «limpio» en casos que se comportan igual es ≈ −18,4k (−10,7 %). Resumen de causas:
- `equipo_inexistente`, `jornada_inexistente`: la cabecera de contexto de la 10.4 («no llames a … list_teams») hizo que el modelo contestara sin
  consultar (los nombres ya estaban en el contexto) y el guardarraíl sustituyó su respuesta por el aviso de alcance. Se corrige con una
  cabecera condicional (si NO aparece en la lista, comprobarlo) y con la reconsulta también ante respuestas sin herramientas.
- `jornada_2_resultados`: el modelo pidió 7 herramientas a la vez y saltó el límite de 5. Hipótesis: al reescribir la descripción de
  `list_matches` se perdió «(acta disponible)» junto a `has_report`; restaurada. Causa no confirmada; sin traza de los argumentos pedidos.
- `acta_partido_sin_acta`: respuesta correcta («por jugar, sin marcador ni acta») rechazada por criterio léxico; ahora vale «no se ha jugado»
  sin marcador para esa verdad.
- `acta_tarjetas_codigo`: **corregido** (ya no llama a `list_teams`).
- `acta_goleadores_contra_equipo` usa `top_scorers(against_team_id)`: −4.127 tokens y una llamada menos.

## 9. Segunda medición online (commit `27a6596`, 15/10/2026 21:46)
| | Línea base `aeb29e4` | `b1d62d3` | `27a6596` |
|---|---|---|---|
| Entrada (real) | 172.078 | 145.622 | **170.732** |
| Llamadas LLM / herramientas | 58 / 32 | 54 / 28 | 61 / 36 |
| Casos | 26 pass, 1 fail | 23 pass, 4 fail | 25 pass, 2 fail |
| Primera llamada (prefijo, real) | 2.463 | ~2.071 | ~2.102 (**−14,7 %**) |
| Caché | 0 | 4.012 | 8.218 |

**Conclusión honesta:** el ahorro del prefijo es real y estable (−14,7 % por llamada), pero el total depende del número de llamadas y de las filas
devueltas, que varían entre ejecuciones del mismo código (54 → 61 llamadas; 145k → 171k). Con una ejecución por caso **no se puede resolver
un efecto menor de ~10 %**.
- **`limit` no funcionó en ninguna de las dos mediciones:** `list_matches(limit=1)` devolvió «19 matches» y 7.403 caracteres, es decir, la API
  en marcha era anterior al cambio (no se reinició tras `git pull`). El modelo repitió las llamadas y `recuento_global` pasó de 9k a 21k tokens.
  Corregido: el MCP recorta él mismo cuando la API ignora `limit`, y el preflight avisa («la API en marcha ignora `limit`: reiníciala»).
- `acta_sustituciones_minuto`: el modelo respondió con una frase de limitación no reconocida («no tengo esa información…») y el guardarraíl la
  sustituyó. Ampliadas las frases reconocidas y la traza guarda ahora `raw_answer` (texto previo a la sustitución).
- Variaciones de conducta sin fallo: `acta_jugador_ambiguo` dio los goles de los dos candidatos (una llamada más) y
  `acta_goleadores_contra_equipo` verificó el equipo con `find_players` y `list_teams` (dos llamadas más).

## 10. Tercera medición online (commit `07006ca`, 16/10/2026 21:13; API reiniciada)
| | Línea base `aeb29e4` | `27a6596` | `07006ca` |
|---|---|---|---|
| Entrada (real) | 172.078 | 170.732 | **157.967 (−8,2 % vs base)** |
| Llamadas LLM / herramientas | 58 / 32 | 61 / 36 | 58 / 32 |
| Casos | 26 pass, 1 fail | 25 pass, 2 fail | 24 pass, 3 fail |
| Capa herramientas | 26/27 | 25/27 | **27/27** |
| Primera llamada (prefijo) | 2.463 | ~2.102 | 2.100-2.132 (−13,6 %) |
| Caché | 0 | 8.218 | 4.012 |

- El prefijo fijo sigue siendo el 77,5 % de los tokens. No hay fallos de herramientas, datos, invención ni advertencias.
- **Las 3 regresiones tienen la misma causa y es mía:** el prompt de la 11.4 decía «que no dispones de esa información» y el modelo repitió
  esa frase en segunda persona («No dispones de esa información…»), que es falsa y el criterio léxico rechaza con razón. Afecta a
  `acta_dato_no_almacenado`, `jornada_inexistente` y `acta_sustituciones_minuto`; la traza confirma `limitacion_sin_tools` en los tres.
  Corregido con una frase de ejemplo en primera persona; «no dispone(s)» ya no cuenta como limitación de las herramientas.
- `limit` no se usó en `recuento_global` (el modelo no lo eligió); sin `limit` el recuento cuesta ~9k tokens.
- Llamadas de más por conducta del modelo: `enfrentamiento_ganador` (+`get_match`), `acta_finalizado_sin_acta` (+`get_match_report`),
  `acta_goleadores_contra_equipo` (+`find_players`, `list_teams`).

## 11. Repetición de los 3 casos de limitación (commit `9e6214f`, 16/10/2026 21:23)
`jornada_inexistente` pasa (consulta `list_rounds`, «La jornada 99 no figura en el grupo»). `acta_dato_no_almacenado` y
`acta_sustituciones_minuto` responden bien, en primera persona («No dispongo de ese dato en mis herramientas.»), y pasan datos,
herramientas y respuesta, pero **fallan la capa de invención**: el detector de fugas del prompt marca esa frase porque el propio
prompt la da como ejemplo. Es un falso positivo mío (la frase es la fórmula pedida, no una cita accidental). Corregido: `LIMITATION_REPLY`
es una constante compartida y `leaks_prompt` la descuenta, igual que el aviso de alcance; cualquier otra copia de las instrucciones se sigue marcando.

## 12. Confirmación de las limitaciones (commit `73e03b6`, 16/10/2026 21:26)
`acta_dato_no_almacenado` y `acta_sustituciones_minuto`: **2 pass, 0 fail** (datos, herramientas, respuesta, invención, advertencia y
presentación en ✔), 1 llamada cada una, 2.145 tokens de entrada por llamada (línea base: 2.463). Junto con `jornada_inexistente`
(pass en `9e6214f`), las tres regresiones de la 11.4 quedan resueltas **en ejecuciones parciales**. Falta una ejecución completa con el
commit final para dar la fase por validada de extremo a extremo.
