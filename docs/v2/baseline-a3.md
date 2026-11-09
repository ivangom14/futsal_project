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
