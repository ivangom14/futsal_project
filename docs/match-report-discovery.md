# Descubrimiento del acta (Fase 4)

Verificado el 2026-10-05 con un único partido: `5575697` (jornada 1, PARQUE NORTE F.S. - RODILLITO 'B'
3-4 C.D. LOPE DE VEGA 'B', 2026-09-26). Peticiones reales: 2 (un chunk JS estático y el acta).

## Cómo se localiza el acta
- El HTML de la jornada no trae enlaces. El chunk `/_next/static/chunks/pages/competicion/resultados-y-jornadas-*.js`
  construye el enlace "VER ACTA" así: `/acta-partido/<codacta>?temporada=<t>&competicion=<c>&grupo=<g>`
  (`delegacion=` opcional, no necesario aquí).
- `codacta` es el `external_match_id` ya almacenado en PostgreSQL. No existe un ID de acta distinto
  (`report_external_id == match_external_id`).
- URL usada: `https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245`
  → HTTP 200, 0 redirecciones, dominio `www.rffm.es`, ~260 KB, HTML (Next.js SSR). Sin XHR ni endpoint.

## Estructura
`<script id="__NEXT_DATA__">` → `props.pageProps.game` (59 claves); el `query` de nivel superior da
temporada/competición/grupo. `host` (`https://appweb.rffm.es/`) solo sirve de base de los escudos relativos.

## Campos presentes
- **Identidad/partido**: `codacta`, `jornada`, `fecha` (dd-mm-aaaa), `hora`, `campo`, `codigo_campo`,
  `acta_cerrada`, `suspendido`, equipos (`codigo_equipo_*`, `equipo_*`), `goles_*`, `esquema_*` (táctica).
- **Jugadores** (`jugadores_equipo_local|visitante`): `codjugador`, `nombre_jugador`, `dorsal`, `titular`,
  `suplente`, `capitan`, `portero`. Siempre presentes en el acta real: id y dorsal.
- **Cuerpo técnico**: entrenador (nombre + código), segundo entrenador, delegado local/visitante/de campo
  (solo nombre).
- **Oficiales** (`arbitros_partido`): `cod_arbitro`, `nombre_arbitro`, `tipo_arbitro`.
- **Eventos**: `goles_equipo_*` (`codjugador`, nombre, `minuto`, `tipo_gol`) y `tarjetas_equipo_*`
  (`codjugador`, nombre, `minuto`, `codigo_tipo_amonestacion`, `segunda_amarilla`).

## Ausente o no interpretado
- Sin ID de evento, periodo, tiempo añadido, marcador tras el evento ni descripción: no se inventan.
- Sin URL de ficha de jugador (solo `codjugador`); sin `participation_status`.
- Listas vacías en este acta: `goles_penalti`, `sustituciones_*`, `otras_tarjetas`, `otros_tecnicos_*`,
  `tarjetas_equipo_local`. Si llegan con filas, su estructura no está observada: se registran como
  `validation.unparsed_sections` y no se importan.
- Significado de `tipo_gol=100` y `codigo_tipo_amonestacion=100`: no verificado; se conserva el código.
- Excluido a propósito: fotos (`foto*`), `sexo`, `ver_estadisiticas_jugador`; no se visitan fichas.

## Hallazgo corregido
El acta usa fechas `dd-mm-aaaa` (el listado usa `dd/mm/aaaa`); detectado al comparar con `matches`.

## Pendiente para el procesamiento masivo
Descarga de las actas restantes (con `PoliteFetcher`), manejo de actas no cerradas/suspendidas,
estructura de sustituciones y penaltis cuando aparezcan, y las fichas de jugadores.
