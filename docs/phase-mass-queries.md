# Consultas masivas, agregaciones y paginación conversacional

Fase del 2026-11-16. Afecta al agente (desde el 2026-11-17, arquitectura única: herramientas v2 y comprensión determinista).

## 1. Por qué consumían tantos tokens

En la ejecución real `bench127-haiku-v3` (Haiku 5.5 con caché), las 28 preguntas masivas costaban 3,8 veces más que el resto:

| | Masivas (28) | Resto (114) |
|---|---|---|
| Tokens de entrada por pregunta | 47.240 | 23.908 |
| Tokens de salida por pregunta | 2.054 | 690 |
| Llamadas LLM / MCP | 2,71 / 1,71 | 2,33 / 1,56 |
| Coste por pregunta | 0,00369 $ | 0,00097 $ |
| Latencia p50 / p95 | 5,8 s / 26,2 s | 3,9 s / 8,6 s |
| Exactitud | 16/28 | 96/114 |

Causas, medidas en el código:

1. **Filas completas.** Cada partido llegaba al modelo con `ref`, objetos `{ref, name}` de competición, grupo y equipos, la fecha local duplicada, `has_report` y `venue`. Son unos 355 caracteres, unos 100 tokens, por partido. Un sábado con 48 partidos son 4.869 tokens por llamada.
2. **El modelo paginaba.** `matches_by_date` devolvía 20 filas sin `offset`, así que el modelo repetía la consulta por categoría o por competición para completar la lista. Cada resultado se reenviaba en las llamadas siguientes. En una sola pregunta se llegó a 218.000 tokens de entrada.
3. **Respuestas largas.** El modelo transcribía las listas enteras: más de 2.000 tokens de salida.
4. **«Los siguientes» volvía a empezar.** El modelo repetía la consulta con todo el historial, y el estado de la conversación no guardaba qué listado se había mostrado.

## 2. Qué hace ahora el backend (PostgreSQL)

| Operación | Dónde |
|---|---|
| Página real con `OFFSET/LIMIT` sobre un orden total | `v2_queries.page_rows` |
| Total exacto del filtro con `COUNT() OVER()`, calculado antes de paginar | `v2_queries.page_rows` |
| Total también en una página vacía, pasado el final, con un `COUNT` aparte | `v2_queries.page_rows` |
| `offset` y `next_offset` en la respuesta (`next_offset` es `null` si no hay más) | `v2_queries.envelope` |
| Filas compactas (`compact=true`): solo lo que necesita un listado | `matches`, `scorers_ranking`, `teams_ranking` |
| Partidos de **un** árbitro, agrupados por partido, ordenados y paginados en SQL | `v2_queries.official_matches` (`/v2/officials/matches`) |
| Filtros por día, rango, franja horaria (Europe/Madrid), categoría, género, competición, pista y estado | ya existían en `/v2/matches` |

Ordenaciones deterministas, con desempate:
- **Partidos:** fecha local, hora (los que no tienen hora van al final) e id.
- **Goleadores:** goles, nombre normalizado, equipo e id.
- **Equipos:** valor, partidos jugados, nombre, id y grupo.
- **Árbitros:** número de partidos, nombre y nombre normalizado.
- **Partidos de un árbitro:** fecha, hora y partido.

La fila compacta de partido es `{id, date, time, comp: "COMPETICIÓN · Grupo", home, away, score: "3-1", status, venue}`:
- `score` solo aparece si hay marcador;
- `status` solo si no es «programado»;
- `venue` solo si se filtró por pista.

Ocupa unos 140 caracteres, frente a los 355 de antes. No lleva actas, alineaciones, árbitros, eventos ni referencias.

**Exactitud:**
- Una cifra global usa `count`, el total exacto del filtro, nunca el número de filas mostradas.
- El ranking de goleadores no suma goles entre grupos: cada fila es un jugador en un equipo y un grupo, y lo dice una nota.
- Los partidos de un árbitro nunca mezclan personas: si varios árbitros coinciden con el nombre, se devuelven en `people` sin sus partidos.
- La nota de cobertura de actas se mantiene. Lo mostrado son los resultados disponibles, no todos los que existen.

No hacen falta índices nuevos. Los filtros usan las mismas columnas y uniones que antes, y el total sale de la misma consulta.

## 3. Herramientas MCP

No hay herramientas nuevas: las siete v2 se reutilizan y los parámetros nuevos son opcionales.
- `matches_by_date`, `scorers_ranking`, `teams_ranking` y `officials_ranking`: añaden `offset`, por defecto devuelven **10** filas y piden filas compactas.
- `find_official(include_matches=true)`: usa `/v2/officials/matches` (paginado en la base de datos) con `offset`.

La API conserva los contratos anteriores: sin `compact` ni `offset` responde como siempre, y `/v2/officials?include=matches` sigue existiendo.

## 4. Paginación

- Primera página de 10, con `count` (total exacto) y `next_offset`.
- El agente añade al resultado una `page_note` para el LLM. Le indica que diga «Mostrando 1–10 de 48», que termine con «¿Quieres que te muestre los siguientes 10?», que no pida más páginas y que use `count` para las cifras.
- Tamaño explícito («muéstrame 25», «dame 20 más») hasta 50 filas. «El resto» muestra lo que queda, también con un tope de 50.
- Garantías:
  - con datos estables, las páginas no se solapan ni dejan huecos, porque el orden es total (está probado recorriendo 48 filas página a página);
  - si una actualización cambia los datos entre dos páginas, una fila puede desplazarse;
  - el total se recalcula en cada página y se informa tal cual.
- Se usa desplazamiento y no cursor porque los listados son pequeños (cientos de filas como mucho) y el orden es estable. Además, el desplazamiento permite «los anteriores» y saltar a una página.

## 5. Continuidad conversacional

`ConversationState.listing` guarda **solo la consulta**: herramienta, filtros, `offset`, `limit`, `count` y `next_offset`, nunca las filas. Si una pregunta ejecuta herramientas y no produce ningún listado, el listado guardado se retira.

`agent/v2/paging.parse_followup` reconoce, sin LLM, los seguimientos cortos de hasta 10 palabras que empiezan como un seguimiento:
- «los siguientes», «dime los siguientes», «muéstrame 10 más», «continúa», «¿y el resto?», «los anteriores»;
- «muéstrame 25»;
- «ordénalos por hora»;
- «solo los juveniles», «solo los femeninos»;
- «ahora solo los de Preferente»: una palabra que distinga competiciones en el catálogo;
- «¿y el domingo?», «¿y el sábado 7 de noviembre?»;
- «solo a partir de las 18».

Con un cambio de filtro se mantienen los demás y se vuelve a la página 1. Al cambiar de día no se arrastra la franja horaria, salvo que se diga otra.

Una pregunta nueva sigue el flujo normal y no se confunde con un seguimiento, por ejemplo «¿quién ganó el domingo?» o «¿cuántos puntos tiene…?».

Un seguimiento reconocido hace **una** llamada MCP, compone la página con las filas devueltas y no llama al LLM. Si la herramienta falla, lo dice y no inventa la página. Si el estado se perdió (historial reconstruido), «los siguientes» lo resuelve el LLM con el historial, ya que la herramienta admite `offset`.

**Franjas horarias:** «por la mañana» es 08:00–14:59, «por la tarde» 15:00–23:59 y «por la noche» 20:00–23:59. Se pueden configurar con `AGENT_TIME_BANDS="tarde=16:00-23:59"`. El usuario las precisa con horas explícitas («a partir de las 18», «antes de las 12», «entre las 16 y las 20»), que mandan sobre la franja (`domain/dates.parse_hours`).

## 6. Presentación

Se reutilizan los componentes existentes del frontend:
- **Partidos** (partidos de un día o de un árbitro): componente `matches`. Muestra la fecha y hora local con «AAAA-MM-DDTHH:MM» y, como subtítulo, «Mostrando 11–20 de 48». El único cambio en el frontend es un campo opcional `detail` con la competición, que aparece junto a la hora.
- **Rankings** (goleadores, equipos y árbitros): componente `bars`.
- **«Ver más»:** cuando hay `next_offset`, la sugerencia pulsable es «Muéstrame los siguientes 10». Va por la paginación sin LLM.

El texto de la página incluye las filas, que es lo exacto para el historial y la evaluación. La conciliación de texto con los visuales ya existente (`reconcile_answer`) quita del texto visible lo que repite la tabla.

## 7. Medición antes y después

Sin conexión: el mismo mundo sembrado en PostgreSQL y el recorrido real agente → MCP → API → BD, con un LLM simulado. Prueba: `tests/integration/test_mass_queries_pg.py`.

| Consulta | Antes: filas / tokens de herramienta | Ahora: filas / tokens | Reducción |
|---|---|---|---|
| Día con 48 partidos | 48 / 4.869 | 10 (de 48) / 463 | −90 % |
| Fin de semana | 48 / 4.869 | 10 (de 48) / 463 | −90 % |
| Goleadores de todas las ligas | 8 / 734 | 8 / 549 | −25 % |
| Equipos con más goles | 20 / 1.788 | 10 (de 32) / 758 | −58 % |
| Partidos de un árbitro | 1 persona / 439 | 3 partidos / 280 | −36 % |

| Seguimiento («los siguientes», «solo los juveniles», «¿y el sábado 7?») | Antes (flujo LLM) | Ahora |
|---|---|---|
| Llamadas LLM | ≥ 2, reenviando el historial | **0** |
| Llamadas MCP | ≥ 1, a menudo varias | 1 |
| Coste | como una pregunta masiva (≈0,0037 $) | **0 $** |
| Latencia | p50 5,8 s / p95 26 s | ~18 ms (BD local de pruebas) |

Coste por pregunta: con las tarifas registradas de Haiku 5.5 (entrada 0,10, caché 0,01 y salida 0,50 USD por millón, consultadas el 2026-11-09), ahorrar 4.400 tokens de herramienta sin caché son unos 0,00044 $ por llamada. Antes había hasta 5 llamadas por pregunta de fecha, y cada resultado se reenviaba.

Esto es una **proyección**: la exactitud, los tokens y el coste reales se confirman con la suite `bench-masivas`, que es de pago y requiere tu autorización:

```bash
python -m futsal.evals.bench run --models claude-haiku-cache --suite bench-masivas --live --run-id masivas-despues --confirm-live --budget-usd 0.2
```

La suite `bench-masivas` (30 casos, 37 turnos) incluye:
- 16 casos de `bench-127` con referencia real anterior;
- 10 casos nuevos en `masivas_extra.dev.yaml`: primera página, paginación encadenada, fin de semana, cambio de categoría y de día, goleadores paginados, todos los partidos de un árbitro, equipos con más goles, cifra global y día sin partidos;
- 3 preguntas sencillas de control.

## 8. Limitaciones

- La paginación sin LLM necesita el estado conversacional del servidor. Si se perdió, «los siguientes» lo resuelve el LLM con `offset`.
- «Los de Preferente» se reconoce si la palabra distingue competiciones. Palabras como «Primera» o «Segunda» aparecen en muchas y van al LLM.
- `club_overview` y `find_player` no se paginan: sus listas son cortas.
- La primera página la redacta el LLM. Su exactitud depende de que respete la `page_note`; se mide con `bench-masivas`.
- Tres casos nuevos necesitan días con más de 10 o 20 partidos: en el conjunto sintético quedan «no evaluables» y se miden con datos reales.
- `/v2/officials?include=matches` (contrato anterior) sigue recortando en Python. El agente ya no lo usa.
