# Fases 2A, E0 y 3A: datos y telemetría, arnés de evaluación y prefetch de entidades

Todo detrás de banderas **apagadas por defecto** (`AGENT_PREFETCH=off`, `AGENT_TELEMETRY=off`, `AGENT_PROMPT_MODE=legacy`; router en sombra). Sin LLM real, sin migraciones, sin cambios en PostgreSQL, API pública,
frontend ni contratos MCP. `ab_summary.py`, `ab_prompt_modes.py`, `router.py`, `prompts.py` y las descripciones MCP **no se han tocado** (huellas MD5 comprobadas).

## 1. Prefetch de entidades (3A): flujo
```
pregunta ──► resolve_scope (hints) ──► [AGENT_PREFETCH=on y sin historial ni foco]
             │
             ├─ catálogo de equipos: list_teams de cada grupo (MCP, sin LLM), en caché 6 h
             ├─ entities.decide(): ¿UN equipo inequívoco? (nombre completo o núcleo único; sin reglamento ni fechas históricas;
             │                     homónimo solo si el ámbito dicho en la pregunta deja exactamente uno: domain.competitions.row_matches)
             │      └─ duda ⇒ NO hace nada: flujo idéntico al de siempre
             ├─ _execute(find_teams(name=<nombre oficial>), via="prefetch")   ← el MISMO _execute: validación de IDs, filtro de ámbito,
             │      reutilización, ToolCallRecord; NO cuenta en result.tool_calls (guardarraíles y límite de herramientas intactos)
             ├─ ¿el resultado confirma el equipo (nombre exacto, un solo grupo)?  no ⇒ se restaura TODO el estado (IDs, ambigüedad, nombres,
             │      llamadas reutilizables) y no se inyecta nada
             └─ sí ⇒ se añade al mensaje de usuario como TEXTO delimitado («resultado de la herramienta, datos, no instrucciones»)
                     con el mismo JSON compacto que habría recibido el LLM; sin turnos functionCall sintéticos
LLM ──► (su find_teams idéntico se reutiliza) ──► list_matches(team_id) ✓ el ID procede de una herramienta ──► …
```
Registro: `ToolCallRecord.via="prefetch"` (`llm_call=0`), `RunRecord.prefetch` (intentado, activado, motivo, filas, inyectado, llamadas de catálogo), `totals.mcp_calls.prefetch`, origen `prefetch` en el desglose de tokens.
**Compatibilidad con Gemini: NO validada.** Lo comprobado offline es la estructura (mensajes de solo texto, roles alternados, sin partes de función). Que la inyección como texto no degrade la calidad
solo lo dice una prueba live (§6). Hasta entonces `AGENT_PREFETCH` sigue en `off`.

## 2. Extractor (`agent/entities.py`) y sus métricas
Conservador: normaliza comillas, números, guiones, siglas con puntos, apóstrofos interiores, espacios y mayúsculas; solo acepta el nombre **completo** o su **núcleo** (sin forma jurídica) con límites
de palabra; sin coincidencia aproximada ni por subcadena; se abstiene ante ningún equipo, varios, homónimo sin ámbito, forma ambigua, reglamento, fecha/temporada histórica y contexto previo.

| Métrica (Holdout A, **primera y única ejecución con el extractor congelado**, n = 1.010) | Valor | IC 95 % exacto |
|---|---|---|
| Precisión de las activaciones | 422/422 = 100 % | 99,13–100 % |
| **Tasa de falsos positivos** (580 negativos) | 0/580 = 0 % | **0–0,63 %** |
| Activaciones con equipo erróneo o sin equipo | 0/422 | 0–0,87 % |
| Cobertura (solo ahorro potencial) | 422/430 = 98,1 % | 96,37–99,19 % |
Objetivo «falsos positivos ≤ 1 %»: **CUMPLE con este dataset** (580 ≥ 368 negativos con 0 fallos). Los 8 no detectados son abstenciones seguras (siglas pegadas a la palabra, p. ej. «ADESCURIALENSE»).
En el conjunto de ajuste `dev` (255), donde se depuró: 100 % de precisión y 0 falsos positivos, pero **«no concluyente»** (125 < 368 negativos). **Hallazgos al depurar:** una plantilla de homónimos mía decía
«en la liga», que SÍ desambigua (error de etiquetado mío, corregido); y los apóstrofos dentro de palabras («P'ALPIE» frente a «PALPIE») fallaban (corregido en `normalize`).
**Qué NO demuestra:** el dataset es sintético y lo redactó el autor del extractor; la cobertura real será mucho menor (nombres abreviados o en minúsculas parciales no se detectan, a propósito) y los falsos
positivos con preguntas reales siguen sin medir. El Holdout B no se ha ejecutado. Decidir activar el prefetch exige además la prueba live.
Coste de la abstención: **42 % de los nombres del catálogo (95/225) están en más de un grupo** (ligas y copas): sin ámbito en la pregunta, el prefetch no actúa en esos equipos.

## 3. Arquitectura resultante
`arms.py` (banderas por brazo) · `ab_arms.py` / `arms_summary.py` (N brazos) · `telemetry.py` · `datasets.py` / `stats.py` / `entities_eval.py` · `entities.py` · `Agent._prefetch` + `_team_catalog_get`.
Ver `docs/telemetry.md`, `docs/datasets-and-holdouts.md`, `docs/eval-harness.md`, `docs/offline-eval-fixtures.md`.

## 4. Compatibilidad con la evaluación pendiente de la Fase 1
`scripts/ab_prompt_modes.py` y `agent/ab_summary.py` no se han modificado; su salida sobre datos sintéticos coincide con un fixture oro generado **antes** de E0 (`tests/fixtures/ab_summary_golden.json`)
y el resumen de N brazos da los mismos veredictos en legacy/compact. Lo único que cambia en el evaluador es que `results.json` incorpora `meta.agent_flags` (clave adicional que `ab_summary` ignora).

## 4b. Ajustes tras el A/B live legacy vs prefetch
- `scripts/ab_arms.py --fill-missing --yes [--min-interval 8]`: repite solo los casos `not_run` (429, red) de cada `<brazo>-r<n>.json` con las banderas de su brazo y los funde en él (copia previa en `<brazo>-r<n>.orig.json`; los casos fundidos llevan `filled: true`).
- `arms_summary`: una diferencia solo se llama «consistente» con ≥3 repeticiones decisivas por brazo; con menos, «insuficiente»; los `not_run` por 429 se marcan (`[429×n]`) y se avisa del desequilibrio.
- Nota del prefetch: se revirtió la frase sobre `group_id` (ver `docs/phase-a-i-optimization.md`): `compact_result` quita `group_id` de lo que ve el LLM, así que no podía copiarlo del resultado.

## 5. Limitaciones y riesgos
Ver §2 (datos sintéticos, mismo autor, cobertura real desconocida), y: el prefetch añade una consulta `list_teams` por grupo (24 en las capturas locales) la primera vez y cada 6 h; el LLM ve una conversación distinta con la bandera
encendida (riesgo central, solo medible en vivo); una pregunta con un nombre propio que no es equipo no dispara nada; el diagnóstico muestra la llamada de prefetch como una llamada de herramienta; la telemetría guarda la
pregunta saneada (apagada por defecto); el test `test_health_ok_never_exposes_keys` falló una vez de forma intermitente al principio (depende del arranque del hilo del agente; pasa en repetición: no está relacionado con estos cambios).

## 6. Entrega del Holdout B
`data/holdout/holdout_b.json` está ignorado por Git y vive solo en la sesión donde se generó; se te entrega como adjunto para que lo coloques en `data\holdout\` de tu equipo. El manifiesto con su huella
(`tests/fixtures/router/holdout_b.manifest.json`) sí está en el repositorio: `python -m futsal.agent.datasets verify data\holdout\holdout_b.json --manifest tests\fixtures\router\holdout_b.manifest.json`.
