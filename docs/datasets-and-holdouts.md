# Datasets, holdouts y estadística (Fase 2A)

| Conjunto | Fichero | Uso | Estado |
|---|---|---|---|
| Ajuste (router) | `tests/fixtures/router/design_set.json`, `validation.json` | Depurar libremente. **Contaminados**: no son evidencia. | Sin cambios |
| Ajuste (extractor) | `tests/fixtures/entities/entities_dev.json` (255) | Depurar el extractor de equipos | Nuevo |
| **Holdout A** | `tests/fixtures/router/holdout_a.json` (1.010: 430 con equipo, 580 negativos) | Validación. Sellado con huella; **no se usa para ajustar** | Nuevo, ejecutado una vez |
| **Holdout B** | `data/holdout/holdout_b.json` (1.009: 430 + 579), **ignorado por Git** | Evaluación final. Manifiesto con huella en `tests/fixtures/router/holdout_b.manifest.json` | Nuevo, **sin ejecutar nunca** |

## Cómo se construyeron
Redactados antes de ejecutar el extractor sobre ellos, con **etiquetas por construcción y por el catálogo real** (`tests/fixtures/entities/team_catalog.json`: 24 grupos, 363 entradas, 225 nombres,
95 presentes en más de un grupo, extraído en solo lectura de `data/rffm/catalog`). Cada conjunto usa equipos distintos (reparto por huella del nombre), plantillas distintas y semillas distintas; se
eliminaron las preguntas con ≥ 85 % de palabras en común con cualquier conjunto de ajuste o entre holdouts. El generador **no está en el repositorio**, a propósito: quien lo tenga a mano vería las
plantillas del Holdout B.

Cada pregunta lleva: intención, **`needed_tools`** (mínimo para responder bien), **`plausible_tools`** (superconjunto aceptable, para medir recall de un selector de herramientas), `prefetch_expected`
(`fire`/`abstain`), `prefetch_entity` (nombre oficial) o `abstain_reason`, ambigüedad de equipo, ámbito, seguimiento (`prev_questions`, `focus`), `tier0_eligible`, temporada (`current`/`historical`;
las históricas quedan fuera de alcance) y `form` (cómo se escribió el equipo).

## Limitaciones que no se pueden ocultar
1. **Son sintéticos.** Plantillas combinadas con equipos reales; no sustituyen a preguntas de usuarios. Preguntas reales anonimizadas: **0 por ahora**. Se incorporan con la telemetría
   (`telemetry export-candidates`) y tu etiquetado (`datasets add`). Objetivo declarado: ≥ 20 % reales antes de la evaluación final.
2. **El autor del extractor y de los datasets es el mismo.** «A ciegas» significa que nunca se ejecutó el extractor sobre A ni B antes de congelarlo, no independencia de criterio.
   La mitigación real es que **aportes tú preguntas al Holdout B** (`python -m futsal.agent.datasets add --into data\holdout\holdout_b.json --manifest tests\fixtures\router\holdout_b.manifest.json --from TUS.json`).
3. Las formas «núcleo» (el equipo sin su forma jurídica) se etiquetan **por una regla** parecida a la del extractor: miden consistencia, no semántica. Se informan aparte (`form=core`).
4. Un fichero de `data/holdout/` solo existe en la máquina donde se generó: ver «Entrega del Holdout B» en `docs/phase2a-e0-3a.md`.

## Estadística (`agent/stats.py`)
Intervalos exactos de Clopper-Pearson bilaterales del 95 %. Muestra mínima para sostener un objetivo: falsos positivos ≤ 1 % → **368** negativos con 0 fallos, 555 con 1, 720 con 2;
aciertos ≥ 95 % → 72 / 110 / 142 decisiones con 0 / 1 / 2 errores. Un objetivo solo se da por cumplido si el LÍMITE SUPERIOR del intervalo lo respalda; si no, el veredicto es «no concluyente».

## Guardia del Holdout B
`datasets.load_holdout_b` exige reconocimiento expreso (`--use-holdout-b-final` en `entities_eval`) y anota cada lectura (fecha, finalidad, huella) en `data/holdout/b_access.log`.
No es una barrera técnica (el fichero es legible): es disciplina de proceso, y deja rastro.
