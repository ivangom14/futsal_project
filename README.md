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
