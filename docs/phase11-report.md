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
