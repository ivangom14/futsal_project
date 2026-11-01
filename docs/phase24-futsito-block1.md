# Futsito — Fase 24: interpretación y precisión de consultas (Bloque 1)

Primer bloque de la evolución «Futsito». Alcance de esta entrega: **3.1–3.6** (ligas/copas, categorías, alias, resolución previa,
mensajes sin información, errores amigables). Bloques 2 (memoria mejorada), 3 (fuentes), 4–7 (sugeridas, visuales, valoración,
rediseño/logo) **no están hechos**. El archivo de logo no se ha recibido (la imagen adjunta era un cartel de sorteo, no un logo).

## Auditoría
- BD: `competitions` solo guarda `name` (sin categoría ni tipo); la `category` del catálogo (`inventory.json`) no llega a la base.
- Los nombres oficiales bastan para clasificar: `COPA …`/`TROFEO …` = copa/torneo, `JUVENIL` = juvenil, el resto = liga sénior.
  «Senior» solo aparece en copas; las ligas sénior son `AFICIONADO` (Autonómica, Preferente, Primera) y `TERCERA DIVISIÓN`.
- La aclaración sobre el DOSA venía de que `find_teams` devuelve todas sus competiciones y el LLM las listaba sin filtrar.
- Sin migraciones, sin cambios en MCP, ingesta, Fase 21 ni datos.

## Diseño
| Pieza | Dónde | Qué hace |
|---|---|---|
| Taxonomía y alias | `domain/competitions.py` (puro) | `classify` (tipo y categoría por reglas), `extract_hints` (tipo, categoría, alias/nombre oficial, grupo), `merge_hints` (lo nuevo manda), `candidates`, `row_matches`, `scope_note` |
| Configuración | `config/competition_aliases.json` + `config/competition_aliases.py` | alias coloquiales → **nombres oficiales**, palabras de tipo/categoría, reglas por nombre, excepciones (`overrides`) |
| Integración | `agent/agent.py` | `_resolve_scope` (pistas del mensaje y de los mensajes previos del usuario), `_scope_filter` (filtra `find_teams` antes de que lo vea el LLM), nota de la aplicación en el mensaje |
| Mensajes | `agent/messages.py` | fórmulas por motivo y `classify_error` |
| Errores | `agent/web.py` | mensaje llano + `code` + `technical` (sin secretos) y log `futsal.chat` |

**Alias**: se editan en el JSON, no en el prompt. Cada alias apunta a nombres oficiales y se comprueba contra el catálogo real recibido
(`list_competitions`): un destino inexistente se descarta y no se inventa ningún ID (si todos faltan, no hay candidatas). Una frase que
encaja en varias entradas se resuelve con la categoría dicha; si no hay, gana la entrada `default`; si no hay ninguna, **se pide aclaración**.
La frase más larga gana («regional juvenil» sobre «regional»).

**Resolución antes de preguntar** (en código, antes del LLM):
1. Pistas del mensaje actual y de las preguntas previas del usuario (nunca del texto del asistente): tipo (liga/copa/torneo), categoría
   (senior/juvenil), alias o nombre oficial, grupo. Lo más reciente manda; un cambio de competición/tipo/categoría no arrastra pistas
   incompatibles. «Senior» tras «DOSA en liga» se une a la pregunta original (`resolve_pending`, Fase 23).
2. `find_teams` devuelve solo las competiciones del equipo que cumplen las pistas (+ `scope_filter.excluded`); si ninguna cumple, `note`
   con en cuáles sí figura. «DOSA en liga» ofrece solo las dos ligas; «en liga senior» queda en una → resolución directa.
3. El mensaje del usuario recibe una nota (solo si hay pistas) con lo resuelto: competición e IDs reales, grupos, «falta el grupo» o
   «competiciones posibles». Sin pistas no cambia nada (ni nota ni filtro, sin coste de tokens).
4. Competición y grupo resueltos → no se acepta una petición de aclaración del ámbito ni se rechaza el equipo por ambiguo. Las validaciones de IDs no cambian.

**Mensajes sin información** (por motivo; el LLM los usa según el caso, el prompt los enumera):
dato no encontrado «No he encontrado ese dato en las fuentes que tengo disponibles.» · normativa/fuente sin información «No tengo información suficiente para
responder a esa pregunta.» · dato parcial «No puedo confirmar esa información con los datos actuales.» · consulta no soportada «Todavía no puedo consultar
ese tipo de información, pero sí puedo ayudarte con…» · ambigua «¿Puedes concretarme un poco más qué competición o equipo quieres consultar?».
Todas cuentan como limitación honesta (`states_tool_limit`) y no como fuga del prompt. Coste: +≈60 tokens/llamada en el prompt.

**Errores técnicos** (`POST /chat`, antes un 502 con el texto del LLM): `{"detail": mensaje llano, "code", "technical"}`.
429 → «demasiadas consultas…» (HTTP 429) · conexión → «No he podido conectar…» (502) · timeout → «tardando más de lo esperado…» (504) ·
interno → «Ha ocurrido un problema al procesar tu consulta.» (500) · agente sin arrancar → 503. El `technical` va solo a un bloque plegado
«Detalle técnico» y al log; no se tocan los reintentos del cliente LLM ni sus límites.

## Contrato (cambios; todo compatible)
`diagnostics.resolved_scope`: `{filters, inherited, resolved, competitions[≤8], total}` o `null`. Errores: `detail` (amigable), `code`, `technical`.

## Pruebas (sin cuota de Gemini)
`tests/test_competition_resolution.py` (36): taxonomía del catálogo real (14 competiciones), alias, ambigüedad, casos 1-6 de la especificación,
cadena DOSA liga → «Senior» → «¿Y el próximo partido?», cambio de categoría. `tests/test_friendly_messages.py` (21): 429/conexión/timeout/MCP/interno,
detalle en logs, sin secretos. Frontend (25): mensaje amigable con detalle plegado, `ApiError` con `code`/`technical`.
Batería: 568 pass, 2 skipped; el único error es `test_refresh_runs_pg` (necesita PostgreSQL local; ya fallaba antes).

## Validación manual con Gemini real (Windows CMD, mismo arranque que la Fase 22)
```
git pull origin claude/tender-rubin-rsz9uv
python -m pytest tests\test_competition_resolution.py tests\test_friendly_messages.py -q
cd frontend && npm test && cd ..
```
Reinicia el servicio de chat y recarga con `Ctrl+F5`. En conversaciones nuevas:
1. «¿Cómo ha quedado el DOSA en liga?» → solo debe ofrecer las 2 ligas (sin copas). Luego «Senior» → resultados de la liga sénior.
2. «¿Cómo ha quedado el DOSA en liga senior?» y «…en liga juvenil?» → directos. Luego «¿Y el próximo partido?» sin repetir nada.
3. «¿Cómo va el regional?» → pide el grupo (1-4) sin volver a preguntar la competición; «regional juvenil» → grupos 1-5.
4. «¿Cómo va la autonómica?» → pregunta entre Aficionado y Juvenil. «Liga de aficionados» → ofrece 3 opciones.
5. «¿Cómo va el DOSA en copa?» → solo copas. «DOSA en la preferente» (no juega) → lo dice con naturalidad.
6. Un dato inexistente → debe usar una de las fórmulas nuevas, no «No dispongo…». Provoca un 429 (varias preguntas seguidas) → mensaje llano y «Detalle técnico» plegado.
En «Detalles técnicos» se ve el «Ámbito interpretado».

## Limitaciones
- Clasificación por nombre: una competición nueva con nombres atípicos puede requerir una entrada en `overrides` o en `aliases`.
- Un equipo con «juvenil», «copa» o «liga» en su nombre puede activar un filtro sin querer.
- «Senior» incluye Aficionado, Preferente y Tercera: dentro de la categoría aún pregunta por la división si es ambiguo.
- Las pistas se recalculan desde los últimos 6 turnos del usuario (límite de memoria de la Fase 23).
- Los mensajes por motivo dependen de que el modelo elija la fórmula correcta; no verificado con Gemini real.
- No se ha comprobado con la base real del usuario (catálogo de otra temporada o nombres distintos).
