# futsal_project

Asistente agéntico de competiciones deportivas (inicio: futsal, fuente RFFM). Ver `PROJECT_CONTEXT.md`.

## Descarga de la liga (Fase 2)
```
pip install -e '.[dev]'
python3 -m futsal.cli list-rounds                      # jornadas declaradas por la web
python3 -m futsal.cli scrape-league --max-rounds 2     # prueba limitada
python3 -m futsal.cli scrape-league --resume           # completa; omite jornadas ya hechas
```
Opciones: `--output` (def. `data/rffm`), `--refresh`, `--resume`, `--request-delay` (2 s),
`--timeout`, `--max-rounds`. Salidas (ignoradas por Git): `data/rffm/rounds/<id>.{html,json}`,
`league.json`, `scrape-summary.json`. Resumen revisable: `examples/league-summary.example.json`.

Caché: sin `--refresh` se reutiliza todo snapshot existente (0 peticiones). `--resume` además
carga el JSON de las jornadas completadas. Una jornada fallida no afecta al resto. Los snapshots
solo se guardan tras validar temporada, competición, grupo, tipo de juego y jornada.

Pruebas: `python3 -m pytest -q`, `ruff check .`, `python3 -m mypy` (sin acceso a Internet).

## PostgreSQL e importación (Fase 3)
```
cp .env.example .env                 # ajustar contraseña (ignorado por Git)
docker compose up -d --wait db       # PostgreSQL 16.4 con healthcheck y volumen
alembic upgrade head                 # crea tablas
python3 -m futsal.cli preview-import --input data/rffm/league.json --output examples/import-preview.txt
python3 -m futsal.cli import-league --input data/rffm/league.json [--dry-run] [--fail-on-quality-issues]
python3 -m futsal.cli db-summary [--output examples/db-summary.example.json]
```
- **preview-import**: solo lee el JSON, no usa PostgreSQL; escribe un TXT con ejemplos.
- **--dry-run**: ejecuta la importación real en una transacción que se descarta (necesita BD, no persiste nada ni registra ejecución).
- **import-league**: persiste, registra `ingestion_runs` y crea observaciones.
- Destruir y recrear la BD de desarrollo: `docker compose down -v && docker compose up -d --wait db && alembic upgrade head`.
- Pruebas de integración (BD temporal `futsal_test_*` creada y eliminada en el mismo servidor): `python3 -m pytest -q` (se omiten si no hay PostgreSQL).
- Detalle del modelo: `docs/data-model.md`.

## Acta de un partido (Fase 4)
```
python3 -m futsal.cli inspect-match-report [--match-id ID]      # sin ID elige un finalizado sin acta; no descarga
python3 -m futsal.cli scrape-match-report [--match-id ID]       # 1 petición (o caché) -> data/rffm/match-reports/ID/{raw.html,normalized.json}
python3 -m futsal.cli preview-match-report --input data/rffm/match-reports/ID/normalized.json --output examples/match-report-preview.txt [--no-db]
python3 -m futsal.cli import-match-report --input data/rffm/match-reports/ID/normalized.json [--dry-run]
python3 -m futsal.cli match-report-summary [--output examples/match-report-db-summary.example.json]
```
Requiere PostgreSQL con la migración `0002` (`alembic upgrade head`) y las jornadas importadas.
Ejemplos revisables: `examples/match-report.example.json`, `examples/match-report-preview.txt`,
`examples/match-report-db-summary.example.json`. Método y campos: `docs/match-report-discovery.md`.

### Lote de actas
```
python3 -m futsal.cli process-match-reports [--limit N] [--request-delay 2.0] [--no-import] [--output resumen.json]
```
Toma los partidos finalizados sin acta importada, de uno en uno: usa el snapshot si existe; si no, descarga con pausa,
valida que la página sea el partido/competición esperado antes de guardarla, normaliza, compara con `matches` e importa.
Un fallo no detiene el lote; 3 fallos de descarga seguidos lo abortan. Reejecutarlo no repite lo ya importado.
Resumen revisable: `examples/match-report-batch-summary.example.json`.
Si el acta está cerrada y su marcador difiere del listado, prevalece el acta (el valor anterior queda en
`match_observations`); `tipo_gol=102` es gol en propia meta y suma al rival.

## Agente: evaluación y trazas (Fase 8)
```
python -m futsal.agent.evaluate                      # offline: sin LLM, solo lectura, necesita API y PostgreSQL
python -m futsal.agent.evaluate --mode live --confirm-live --limit 3   # LLM real (de pago)
python -m futsal.agent "pregunta"                    # añade una línea a data/agent/traces.jsonl
python -m pytest -q                                  # sin red; la prueba real con Gemini está desactivada
```
Detalle, casos y método de medición de tokens: `docs/agent.md`.
