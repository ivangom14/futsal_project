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
