# Modelo de datos (Fase 3)

Claves internas (`id` bigint) + identificadores externos RFFM (`source`, `external_id`). Nunca se identifica por nombre.

```mermaid
erDiagram
    seasons ||--o{ competitions : tiene
    competitions ||--o{ competition_groups : tiene
    competition_groups ||--o{ rounds : tiene
    competition_groups ||--o{ teams : tiene
    rounds ||--o{ matches : contiene
    teams ||--o{ matches : "local/visitante"
    matches ||--o{ match_observations : historico
    ingestion_runs ||--o{ match_observations : crea
    ingestion_runs ||--o{ data_quality_issues : registra
```

## Tablas
- `seasons`, `competitions`, `competition_groups`: únicas por (`source`, `external_id`).
- `rounds`: única por (`group_id`, `external_id`); `visible_label` separado del ID técnico.
- `teams`: únicos por (`group_id`, `external_id`); `name` es el último nombre visto.
- `matches`: último estado conocido; único por (`source`, `external_id`). `scheduled_date`/`scheduled_time` tal como publica RFFM (la hora puede faltar) y `scheduled_at` (UTC) solo si hay ambas.
- `match_observations`: versiones distintas observadas (estado, fechas, marcador, `content_hash`).
- `ingestion_runs`: una fila por ejecución real (estado, contadores, `error_summary`).
- `data_quality_issues`: incidencias por ejecución (partido inválido omitido, jornada desconocida, etc.).
- Campos comunes de entidades: `source`, `external_id`, `created_at`, `updated_at`, `first_seen_at`, `last_seen_at`, `source_url`.

## Restricciones
- Unicidad de temporada, competición, grupo, jornada por grupo, equipo por grupo y partido.
- CHECK: local ≠ visitante; ambos marcadores o ninguno; marcadores ≥ 0; estado válido.
- FK compuestas `(round_id, group_id)` y `(home/away_team_id, group_id)`: un partido no puede usar jornadas ni equipos de otro grupo.

## Idempotencia
Upsert por identificador externo dentro de una transacción. Se compara campo a campo: igual → `last_seen_at`; distinto → actualiza y `updated_at`. Reimportar los mismos datos: 0 nuevos, 0 actualizados, 0 observaciones.
Un error revierte toda la importación; la ejecución queda como `failed` en `ingestion_runs` (transacción aparte).

## Histórico
`content_hash` = SHA-256 de {estado, fecha, hora, marcador local, marcador visitante}; sin `observed_at`, `updated_at` ni timestamps de ejecución. Se crea una observación si el hash difiere del de la última observación del partido (la primera importación crea una por partido; un retorno a un estado anterior también se registra). Cambios en sede o equipos actualizan `matches` pero no generan observación.

## Preview, dry-run e importación real
| Modo | BD | Escribe | Registra ejecución |
|---|---|---|---|
| `preview-import` | no | solo el TXT | no |
| `import-league --dry-run` | sí (transacción descartada) | nada | no |
| `import-league` | sí | sí | sí |
