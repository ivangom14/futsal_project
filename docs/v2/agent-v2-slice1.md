# v2 · Agente, entrega 1: consultas amplias (diseño de Opus, 2026-11-09)

Motivo: pruebas reales del responsable (2026-11-09) con 6 fallos de la misma causa: las herramientas v1 exigen un grupo y el
agente pide aclaración ante cualquier «todos/todas». Ver la tabla del diagnóstico en `docs/v2/baseline-a3.md` y el estrato S10
de Futsito-Eval.

## Decisión de arquitectura

Primera porción de la arquitectura v2, **sin sustituir** el flujo actual:

- `AGENT_ARCH=v2` (por defecto `legacy`, que no cambia ni un byte: protegido por `prompt_legacy_golden.json`).
- Con `v2`, el mismo bucle de herramientas recibe un **registro en el mismo proceso** (`agent/v2/registry.py`) con las
  herramientas v2 de abajo, que llaman a `repositories/v2_queries.py` directamente (sin stdio ni HTTP). El servidor MCP publica
  el mismo registro (`mcp_server/v2.py`) para clientes externos. Las herramientas v1 siguen disponibles en v2 durante la transición.
- El resolutor obligatorio y el router T0/T1/T2 llegan en la entrega 2; esta entrega no los necesita.

## Herramientas v2 (entrega 1)

| Herramienta | Entrada | Llama a | Para |
|---|---|---|---|
| `find_player` | `name`, `team?` | `/v2/players` | jugador en todos los grupos; sugerencias marcadas como tales |
| `find_official` | `name`, `include_matches?` | `/v2/officials` | árbitros en todas las competiciones |
| `officials_ranking` | `role?`, `category?` | `/v2/officials/ranking` | «qué árbitro ha pitado más» |
| `matches_by_date` | `date_from`, `date_to?`, `time_from?`, `time_to?`, `category?`, `gender?`, `competition?`, `venue?` | `/v2/matches` | partidos de un día o franja sin grupo |
| `club_overview` | `name`, `results?`, `category?` | `/v2/clubs/search|results` | todos los equipos de un club |
| `scorers_ranking` | `category?`, `gender?`, `limit?` | `/v2/rankings/scorers` | máximo goleador de varias ligas |
| `teams_ranking` | `metric`, `category?`, `gender?` | `/v2/rankings/teams` | equipos más goleadores, menos goleados… |

Todas devuelven `count`, `shown`, `notes` y `freshness`; las notas obligatorias (comparación entre grupos distintos, agrupación
de club por nombre, sugerencias no confirmadas) se deben trasladar a la respuesta: lo comprueba el evaluador.

## Reglas deterministas nuevas (código, no prompt)

1. **Fechas y franjas** (`domain/dates.py`, puro): «sábado 7 de noviembre», «este fin de semana», «mañana», «hoy», «por la
   tarde» (15:00–23:59), «por la mañana» (08:00–14:59), «por la noche» (20:00–23:59), en Europe/Madrid con el reloj inyectado.
   El agente recibe la interpretación como nota de la aplicación y los argumentos de `matches_by_date` se validan contra ella.
2. **Alcance amplio explícito**: si la pregunta contiene un cuantificador de alcance («todos», «todas», «cualquier»,
   «en todas las competiciones», «de todas») o el usuario responde así a una aclaración, la política de ambigüedad pasa a
   **actuar** con las herramientas v2 en lugar de preguntar. Pedir aclaración solo cuando la entidad es ambigua (dos clubes
   distintos con el mismo nombre base, varios jugadores) y nunca dos veces seguidas por lo mismo.
3. **Premisas del usuario**: «creo que juega en X», «¿no es verdad que…?» se verifican con herramientas; la respuesta dice si
   la evidencia lo confirma o no. Confirmar sin evidencia = error crítico (detector en el evaluador y en el validador de la
   entrega 2).
4. **Nombres no encontrados**: si `find_player`/`find_official` devuelven solo `suggestions`, la respuesta dice que el nombre
   no figura y ofrece las sugerencias como posibles, sin atribuirles datos del nombre pedido.

## Delegación (tras aceptar la API v2)

- **T10a (Sonnet)**: `agent/v2/registry.py`, esquemas, `mcp_server/v2.py`, `AGENT_ARCH` en el agente (solo selección del
  registro y del bloque de prompt v2), pruebas con LLM simulado; prueba de oro: `legacy` idéntico.
- **T10b (Sonnet)**: `domain/dates.py` y la regla de alcance amplio en `agent/context.py` solo con `v2`.
- **Aceptación** (Futsito-Eval sobre `ref20261109`, Gemini gratuito): S10 dev ≥ 80 % con 0 críticos; suite `regression` sin
  empeoramientos estables respecto a C1/C2; tokens por pregunta ≤ C × 1,1.

## Pendiente

- Hecho (tarea 4): respaldo determinista ante aclaración insistente (`agent/v2/fallback.py`; traza `context.v2.fallback` / `fallback_skipped` / `answer_source`)
  y «no consta en el acta» (`agent/v2/not_in_source.py`, fórmula `NOT_IN_REPORT`). El respaldo solo actúa tras los 2 empujones (que requieren alcance amplio o
  aclaración previa) y con un plan inequívoco: fechas, máximo goleador + categoría, «todos los equipos del/de <club>» o «qué partidos ha pitado <nombre>».
- Falta: el resolutor obligatorio y el router T0/T1/T2 (entrega 2); ampliar los empujones a una pista de fechas sin «todos».
