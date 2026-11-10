# Línea base A3: restaurar tu BD y medir la deriva frente a 53/65

Procedimiento para el responsable. Objetivo: con **tu** BD real (la de la ejecución de referencia `20261026-212906-live-all`,
53 aprobados de 65 evaluables, ver [phase17-evaluation.md](../phase17-evaluation.md)) comprobar si el código antiguo, con el mismo
modelo, sigue dando ≈ 53/65 (**brazo H**) y medir después el código actual (**brazo C**). Nada de esto se ha ejecutado aún.

## 1. Generar el volcado (en tu máquina)

```
pg_dump -Fc --no-owner -f futsal-ref.dump "<URL de tu base de datos>"
```

La URL de conexión se pasa como argumento a `pg_dump` en tu máquina; el fichero resultante no contiene credenciales. No envíes la URL
ni la contraseña. Deja el fichero en `data/eval-snapshots/futsal-ref.dump` (`data/` está ignorado por Git; no se sube). Se admiten
también formato directory/tar y SQL plano, pero se recomienda `-Fc`.

## 2. Importar (BD nueva, aislada) y verificar

```
python -m futsal.evals snapshot import --file data/eval-snapshots/futsal-ref.dump --id ref-5365 --as-of 2026-10-26T21:00:00 --upgrade-copy
python -m futsal.evals snapshot verify --id ref-5365 --db postgresql+psycopg://USUARIO:CLAVE@HOST:5432/futsal_eval_ref-5365
python -m futsal.evals snapshot verify --id ref-5365 --db postgresql+psycopg://USUARIO:CLAVE@HOST:5432/futsal_eval_ref-5365_head
```

(`--as-of` es el instante de Europe/Madrid usado como «ahora»; pon el del día de la referencia. El servidor sale de `DATABASE_URL`
o de `--db`; el usuario necesita `CREATEDB`.)

- Crea `futsal_eval_<id>` (copia **original**) y, con `--upgrade-copy`, `futsal_eval_<id>_head`: el mismo fichero restaurado otra vez
  y migrado con `alembic upgrade head` **solo ahí**. La original no se migra nunca.
- Si alguna de las dos ya existe se niega (código 3) sin tocarla; no hay `--force`. Si el fichero está corrupto, la revisión de
  Alembic es desconocida o la restauración/migración falla, sale con código distinto de 0 y elimina únicamente las BD que acaba de
  crear. Solo toca otras BD el `CREATE DATABASE` y ese borrado.
- Escribe `evals/snapshots/<id>/manifest.json` (`source: external_dump`, `input_sha256`, `input_bytes`, `restored_db`, revisión
  de Alembic, tablas presentes/ausentes frente a la revisión, recuentos por tabla, extensiones `unaccent`/`pg_trgm`/`vector`,
  documentos de la KB, recuentos de temporadas/competiciones/grupos/equipos/partidos/actas, presencia del grupo 26738245, huella de
  contenido) y comprueba que tras la subida las tablas previas conservan recuentos y md5 (en sus columnas previas).
- **Revisa el manifiesto antes de gastar cuota**: la revisión esperada de la referencia es `0005`; `tables_missing` debe estar
  vacío, `target_group.present` en `true`, y los 182 partidos y 18 actas de la referencia deben coincidir con `counts`.
  Si `alembic_revision` es otra o falta algo, para y consúltalo.
- `verify` compara la huella de contenido: la original contra la del manifiesto y la copia `_head` contra su propia huella tras migrar.
  Vuelve a lanzarlo justo antes de cada brazo.

## 3. Brazo H: código antiguo (`ac48edc`) sobre la BD original

Mide la **deriva del modelo y el ruido** frente a 53/65. Mismo código, misma base, mismo modelo (`gemini-3.5-flash-lite`).

1. Worktree aparte con el código de la referencia (migración en 0005):
   `git worktree add ../futsal-ac48edc ac48edc`
2. En ese worktree, con `DATABASE_URL` apuntando a la BD **original** restaurada (`.../futsal_eval_ref-5365`) y tu `GEMINI_API_KEY`
   del plan gratuito, arranca la API (`uvicorn futsal.api.app:app_factory --factory --port 8000`) en otra ventana: el evaluador
   antiguo exige demostrar que API y evaluación usan la misma base (no uses `--allow-unverified-db`).
3. Primero, sin llamar a nada: `python -m futsal.agent.evaluate --plan --suite all --max-requests 250`
4. Ejecución, con **exactamente** los flags de la referencia (`--mode live --confirm-live --suite all --price-in 0.30
   --price-out 2.50`; el resto, valores por defecto de aquel commit: `--max-tool-calls 5`, `--max-rows 40`) más el tope y el ritmo:
   ```
   python -m futsal.agent.evaluate --mode live --confirm-live --suite all --price-in 0.30 --price-out 2.50 --max-requests 250 --min-interval 4 --output data/agent/eval-a3-h1.json
   ```
   **Antes de ejecutar (obligatorio, Opus 2026-11-09)**: el evaluador de `ac48edc` guarda los argumentos exactos en
   `meta.args` de su resultado (`src/futsal/agent/report.py`, `build_meta`). Aporta también el `results.json` de la referencia
   (`20261026-212906-live-all`; en tu máquina, el `--output` de aquella ejecución, p. ej. `data/agent/eval-fase17-live-all.json`) y
   usa **sus** `meta.args`, no los de arriba, que son una deducción a partir de phase17-evaluation.md. Si `meta.args` difiere de esta
   línea, prevalece `meta.args`; si el fichero no existe, la comparación con 53/65 se marca como «flags no verificados».
   Si el informe indica «con cambios sin commit» (`commit_dirty`), la referencia se midió con el árbol sucio: anótalo.
5. Si se agota la cuota (se detiene limpiamente), repite al día siguiente con `--only-failed` solo para los `not_run`, no para fallos.
6. Compara con la referencia: aprobados/65, por categoría y los 12 fallos de la tabla de causas. Para medir el **ruido**, una segunda
   pasada idéntica (`eval-a3-h2.json`) y compara H1 con H2 y con 53/65. Referencias previas del propio histórico: 53/65 (26-oct) y
   48/65 (27-oct, otro commit) ya muestran una variabilidad de al menos ±5 casos.

## 4. Brazo C: código actual sobre la copia `_head`

Solo después de que el brazo H pase la regla de parada. Desde el checkout actual:

```
python -m futsal.evals run --suite regression --snapshot ref-5365 --db postgresql+psycopg://USUARIO:CLAVE@HOST:5432/futsal_eval_ref-5365_head --max-requests 250 --min-interval 4 --output data/evals/a3-c1 --confirm-live
```

Calcula antes el coste y las llamadas con `python -m futsal.evals plan --suite regression --db <URL _head> --max-requests 250`. La
huella de la copia `_head` se acepta porque el import registró la suya tras migrar. Cada ejecución del `run` comprueba esa huella y
se niega (código 3) si la BD cambió.

## 5. Regla de parada

Si el brazo H **no reproduce la referencia dentro del ruido** (53/65 ± la diferencia entre H1 y H2, y sin que cambien los fallos
estructurales: capa de datos, herramientas), **se detiene todo**: no se ejecuta el brazo C y se explican las diferencias
(modelo, cuota, BD, commit sucio, línea de comandos reconstruida) antes de seguir. Las conclusiones del brazo C solo valen si H
confirma que la base y el evaluador antiguo reproducen la medición.

## 6. Coste

Plan gratuito de Gemini: **0 USD**. Las ≈ 172 llamadas por pasada del brazo H (≈ 0,23 USD a precios de pago, solo informativo) y las
de la suite de regresión del brazo C caben en el plan gratuito repartidas en días; los límites por minuto y día se ven en AI Studio
(`aistudio.google.com/rate-limit`). Recuerda que en el plan gratuito Google puede usar los datos para mejorar sus productos (salvo en
el EEE, Suiza y Reino Unido); la batería solo contiene datos deportivos públicos.

## Verificación del dump recibido (2026-11-09) — RESULTADO: NO reproduce la referencia 53/65

Ficheros recibidos (en `data/eval-snapshots/`, fuera de Git):
- `futsal-ref.dump`: sha256 `a8c51500…bb59`, 985.045 bytes, pg_dump 16.4 custom, creado el 2026-11-09 21:11 UTC;
- `ref-20261026-212906-results.json`: sha256 `2145b717…a94a`.

Importación aislada: `futsal_eval_ref20261109` (revisión 0006, huella `999047cd1b9f`) y la copia `_head` (0007, huella
`17304bed58d7`, tablas previas idénticas). No se tocó ninguna otra BD. Snapshot `ref20261109`, `as_of` = momento del dump.

| Comprobación | Referencia 53/65 (26-oct) | Dump (9-nov) | ¿Igual? |
|---|---|---|---|
| Código | `ac48edc` **con cambios sin confirmar** (`commit_dirty=true`) | — | no reconstruible exactamente |
| Argumentos | `max_requests 200`, `min_interval 4`, `max_tool_calls 5`, `max_rows 40`, `llm_max_retries 2`, precio 0,30/2,50 | disponibles | sí |
| Datos del grupo objetivo (26738245) | 182 partidos, **23** finalizados, **18** actas, huella `75a2c6ab117fc8e2` | 182 partidos, **39** finalizados, **39** actas | **no** |
| Antigüedad de los datos | BD del 26-oct | partidos creados el 2-nov y actualizados hasta el 5-nov: la BD **se reconstruyó** después de la referencia | **no** |
| Conjunto de casos (`suite_version`) | `all-72-b32e31cb2b` | `all-72-b32e31cb2b` | sí (mismos 72 ids) |
| Preguntas | — | 61/72 idénticas; 11 distintas (otras entidades); `acta_finalizado_sin_acta` ya no tiene partido que la cumpla | parcial |
| Fallos de la referencia | 12 | las 12 preguntas siguen idénticas | sí, pero con otra verdad |

Conclusión: la BD que produjo 53/65 ya no existe en ese estado; incluso las 61 preguntas idénticas tienen otra respuesta
correcta (más jornadas jugadas, otras clasificaciones y goleadores). **Una ejecución sobre este dump no es equivalente a
53/65 y no se presentará como tal.** No se ha ejecutado ningún brazo ni consumido LLM.

Opciones (decisión del responsable):
- **A (recomendada)**: fijar `ref20261109` como nueva referencia congelada. Brazo H = `ac48edc` limpio (los cambios sin
  confirmar de entonces no se conocen) y brazo C = código actual, ambos sobre este mismo snapshot, con 2 pasadas de H para
  medir el ruido. 53/65 queda como dato histórico orientativo (comparación solo de tendencias en las 61 preguntas idénticas, marcada «no equivalente»).
  Coste: plan gratuito de Gemini (≈ 170–200 llamadas por pasada).
- **B**: buscar una copia de seguridad de la BD anterior al 2-nov (si existe) y repetir la importación.

## Resultados de la opción A (2026-11-09) — nueva referencia `ref20261109`

Mismo snapshot (`ref20261109`, huella `999047cd1b9f`; C sobre la copia `_head` con las tablas previas idénticas), mismo modelo
(`gemini-3.5-flash-lite`) y argumentos de la referencia histórica (`max_requests 200`, `min_interval 4`, `max_tool_calls 5`,
`max_rows 40`, `llm_max_retries 2`). Resultados en `data/` (fuera de Git): H1 `20261109-212504-live-all`, H2 `eval-a3-h2.json`,
C `data/eval-runs/a3-c1`.

| Brazo | Código | Aprobados / evaluables | Críticos | Sin ejecutar (429 de ritmo) | Llamadas LLM | Tokens de entrada |
|---|---|---|---|---|---|---|
| H1 | `ac48edc` limpio, evaluador antiguo | 43/54 = 79,6 % | 0 | 1 | 142 | 577.599 |
| H2 | ídem (repetición) | 44/54 = 81,5 % | 0 | 1 | 144 | 579.013 |
| C | `2c7c7b2`, `futsal.evals --suite regression` | 46/53 = 86,8 % (IC95 75–93 %) | 2 **falsos positivos** del calificador | 2 | ~146 | — |

- **Ruido del código antiguo**: 5 de 53 casos cambian de veredicto entre H1 y H2 (9 %): `equipo_resultados`,
  `acta_goleadores_contra_equipo`, `regla_duracion_mitad`, `regla_roja_mano` y `circular27_modificaciones`.
- **Mejoras estables** (fallan en H1 y H2 y pasan en C), 7: `equipo_proximo_partido`, `equipo_recuento`, `proximo_campo`,
  `partido_mas_goles`, `regla_sin_evidencia_salario`, `tabla_goleadores_vacia` y `resultados_otro_grupo`.
- **Empeoramientos estables** (pasan en H1 y H2 y fallan en C), 3:
  - `circular27_comunica`: **falso positivo** del detector de marcadores, que toma «Circular n.º 27, páginas 1-2» por un resultado 1-2;
  - `recuento_global` y `acta_sustituciones_minuto`: «evidencia incompleta (¿resultado recortado?)», pendientes de análisis de traza.
- Los 2 «críticos» de C son el mismo falso positivo (`circular27_comunica` y `circular27_modificaciones`). El agente no inventó nada.
  Hay que corregir el detector (rangos de páginas, artículos y reglas no son marcadores) y recalificar C sin volver a llamar al LLM.
- **Coste**: ≈ 0,19 USD por pasada si la clave fuera de pago (en el plan gratuito, 0). Acumulado de A3: ≤ 0,57 USD sobre los 35 USD de A5.

Conclusión provisional: con una pasada de C, el código actual mejora de forma estable en 7 casos y empeora en 2 reales (más 1
falso positivo). Antes de fijarlo como referencia v2 hay que: (1) corregir el falso positivo y recalificar; (2) analizar las
2 regresiones con sus trazas; (3) una segunda pasada de C para medir su propio ruido. **53/65 queda como dato histórico no equivalente.**

### Pasos 1–3 (2026-11-09, noche)

1. **Falso positivo corregido**: `criteria.DOC_REF_RANGE_RE` descarta los «N-M» que forman parte de una referencia documental
   (páginas, artículos, reglas, apartados, circulares…); los marcadores reales («ganó 1-2», «(1-2)», «la página web… acabó 1-2»)
   se siguen detectando (7 + 7 pruebas). Solo lo usa el evaluador: la conducta del agente no cambia. **Recalificación de C1
   sin LLM** (`python -m futsal.evals regrade`; salida `data/eval-runs/a3-c1-regraded`, original intacto): `circular27_comunica`
   y `circular27_modificaciones` pasan de crítico a correcto → **C1 = 48/53 = 90,6 %, 0 críticos**. Recalificación PARCIAL:
   C1 no guardó la evidencia de las herramientas; desde ahora la suite `regression` la guarda en cada registro.
   Pendiente: `evals/grading.py` (suite futsito-v1) tiene su propio `SCORE_RE` sin esta corrección.
2. **Las 2 regresiones restantes de C1**:
   - `recuento_global`: respuesta **correcta** («39 jugados, 143 por disputar, 182 en total»). Falso negativo del calificador: el
     hecho «pendientes» exige que una herramienta devuelva 143, pero el agente aplica la regla del prompt de la Fase 17.8
     (total − jugados), que solo se acepta en hechos marcados `pending`. No se modifica el caso (batería histórica).
   - `acta_sustituciones_minuto`: cambio real menor. H responde «No dispongo de ese dato»; C consulta `find_teams` y responde
     con la fórmula genérica «Todavía no puedo consultar ese tipo de información…». No inventa, pero es menos precisa.
3. **C2**: se detuvo limpiamente tras 8 casos por **cuota diaria gratuita agotada** (Gemini pide ~10 h): 6 correctos y 1 fallo
   (`proximo_campo`, que pasó en C1: ruido). Se reanuda con `--fill-missing` el 2026-11-10 a las 09:15 (Madrid). La cuota
   gratuita confirma **coste 0 USD**.

### Resultado final de la baseline `ref20261109` (2026-11-10)

C2 completado con `--fill-missing` (118 llamadas, sin cuota agotada). Incidente previo corregido: la copia `_head` se había
modificado al probar la API v2 (`catalog-backfill`) y el runner se negó a ejecutar por huella distinta; se restauraron ambas
BD desde el mismo dump con huellas idénticas a las del manifiesto (sin cambios en Git) y las pruebas exploratorias usan ahora
la copia aparte `futsal_play_ref20261109`. C1 se ejecutó antes de la modificación.

| Pasada | Código | Aprobados / evaluables | Críticos reales |
|---|---|---|---|
| H1 | `ac48edc` | 43/54 = 79,6 % | 0 |
| H2 | `ac48edc` | 44/54 = 81,5 % | 0 |
| C1 (recalificado) | `2c7c7b2` | 48/53 = 90,6 % | 0 |
| C2 | `e75e783`+ (agente legacy idéntico) | 46/55 = 83,6 % | 0 (1 marcado: falso positivo, ver abajo) |

- **Ruido** (51 casos evaluables en las 4 pasadas): 5 cambios entre H1 y H2 y 5 entre C1 y C2 (≈ 10 %).
- **Mejoras estables** (fallan en H1 y H2, pasan en C1 y C2): `equipo_proximo_partido`, `equipo_recuento`,
  `partido_mas_goles`, `regla_sin_evidencia_salario` y `tabla_goleadores_vacia`.
- **Empeoramientos estables**: `recuento_global` (falso negativo del calificador: respuesta correcta por total − jugados) y
  `acta_sustituciones_minuto` (fórmula genérica de limitación en lugar de «no consta en el acta»; menor, sin invención).
- **Crítico marcado en C2** `circular27_sin_evidencia_rffm`: el agente pidió aclarar «Grupo 3 o Grupo 4» en lugar de decir
  que la sanción no consta en los documentos (fallo de interpretación real); el detector `unsupported_digits` tomó el «3» del
  nombre del grupo como cifra inventada. Pendiente: no contar cifras que forman parte de nombres del catálogo.
- **Decisión**: la referencia v1 sobre `ref20261109` queda fijada en **H ≈ 80–81 %** (código antiguo) y **C ≈ 84–91 %**
  (código actual, media ≈ 87 %), 0 críticos reales. Toda comparación futura del agente v2 se hace contra C1/C2 en este snapshot,
  con 2 pasadas y el ruido medido (±5 casos de 51).
