# Descubrimiento RFFM (Fase 1)

Verificado el 2026-10-04 con una única petición HTTP a la página pública de resultados.

## Método
- `GET https://www.rffm.es/competicion/resultados-y-jornadas?temporada=22&competicion=26738243&grupo=26738245&jornada=2&tipojuego=3`
  → HTTP 200, HTML (~290 KB).
- La web es Next.js (SSR). Los partidos llegan **en el HTML inicial**, dentro de
  `<script id="__NEXT_DATA__">` → `props.pageProps.results.partidos`. No hay tablas HTML,
  ni XHR necesario, ni JavaScript requerido. Sin Playwright.
- Dominio único necesario: `www.rffm.es`. (`appweb.rffm.es` aparece como `host` de las actas;
  aún no se visita.)

## Campos de `results` usados
`codigo_competicion`, `nombre_competicion`, `codigo_grupo`, `nombre_grupo`, `jornada`,
`nombre_jornada`, `fecha_jornada` (dd/mm/aaaa), `partidos[]`. Temporada: `season.cod_temporada`.

## Campos de cada partido (30 claves; usadas)
`codacta` (id partido/acta), `CodEquipo_local|visitante`, `Nombre_equipo_*`,
`Goles_casa`, `Goles_visitante` (strings; vacío si no hay), `fecha` (dd/mm/aaaa), `hora` (HH:MM),
`campojuego`, `codigo_campo`, `estado`, `situacion_juego`, `motivo_estado`, `acta_cerrada`.
Excluido a propósito: `arbitro` (dato personal, no necesario).

## Supuestos y huecos
- Solo se observó `estado=1`, `situacion_juego=1`, `acta_cerrada=1` (todos finalizados).
  Los códigos de aplazado/suspendido/anulado no se han observado: se clasifican por texto de
  `motivo_estado` y, si no hay evidencia, `unknown`. Se conserva el valor original.
- La página no incluye URL de acta ni de comparador: `match_report_url` y `comparison_url`
  son `null`. Las URLs relativas que aparezcan se normalizan a absolutas.
- Zona horaria asumida: Europe/Madrid.
- `external_round_id` = `jornada` (no hay id de jornada propio en la página).

# Descubrimiento de jornadas (Fase 2)

- Lista real en `pageProps.rounds.jornadas` (idéntica a `results.listado_jornadas[0].jornadas`),
  presente en cualquier página de jornada: `codjornada`, `nombre`, `fecha_jornada` (dd-mm-aaaa).
  Se obtiene de la página semilla (jornada 2), que se guarda también como su snapshot.
- **ID técnico** (`codjornada`, parámetro `jornada` de la URL) ≠ **número/etiqueta visible**
  (`nombre`) ≠ posición en la lista. Hoy coinciden (1..26) pero se guardan por separado y nunca
  se asume el rango.
- Resultado verificado el 2026-10-04: 26 jornadas, 7 partidos por jornada = 182 partidos
  (13 finalizados, 169 programados, 0 aplazados/suspendidos/anulados/desconocidos).
  La jornada 1 contiene un partido con `estado=0`, sin marcador y fecha 2026-12-05
  (reprogramado): se conserva como `scheduled`.
- Incidencias de calidad observadas: ninguna. 26 peticiones reales en total, secuenciales, pausa de 2 s.
- Validación: cada respuesta debe coincidir en temporada, competición, grupo, tipojuego y jornada
  solicitada; si no, no se guarda y se registra `round_failed`.
- Pendiente (fuera de alcance): actas, base de datos, API, MCP, knowledge base.
