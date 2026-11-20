# Fase 2 · Batería de preguntas para probar desde el navegador

Requisito: datos de **Preferente Aficionado · Grupo 1** en la BD que use tu API (ver `docs/multisport-phase2.md` §8) y la Fase 2 desplegada a la vez en API, chat y frontend. Comprueba en «Detalles técnicos» (`?debug`) que cada herramienta lleva el deporte esperado.

## A. Con «Fútbol» seleccionado (football11)

| # | Pregunta | Esperado |
|---|---|---|
| A1 | ¿Qué equipos hay en Preferente Aficionado, grupo 1? | 18 equipos de fútbol 11, ninguno de fútbol sala |
| A2 | Muéstrame la clasificación de Preferente Aficionado, grupo 1 | Tarjeta de clasificación oficial (jornada 10, 18 equipos), sin repetirla en texto |
| A3 | ¿Quién es el líder? | Líder de la tabla anterior (contexto conservado) |
| A4 | ¿Cuál fue su último partido? | Último partido finalizado del líder (jornada 10 si no hay datos más recientes) |
| A5 | ¿Contra quién juega el próximo? | Próximo partido programado del mismo equipo |
| A6 | ¿Quiénes son los máximos goleadores? | Tabla oficial de goleadores del grupo |
| A7 | ¿Qué partidos de Preferente Aficionado hay este fin de semana? | Tarjetas de partidos de la jornada del fin de semana |
| A8 | ¿Cuál es el resultado del último partido? | Marcador del último partido finalizado |
| A9 | ¿Quién jugó de titular en ese partido? (sobre un partido con acta) | **Lista** de 11 titulares y suplentes por equipo, esquema declarado del acta y sin pista de fútbol sala |
| A10 | ¿Qué cambios hubo en ese partido? | «El acta incluye las sustituciones, pero todavía no las he incorporado…» (nunca «no consta») |
| A11 | Dime la clasificación de Preferente Aficionado de fútbol | Igual que A2: «de fútbol» no altera la competición |
| A12 | ¿Cómo va la Segunda Aficionado? | Existe en el catálogo F11 (no dice «no existe»); si no hay datos cargados, lo indica sin inventar |

## B. Cambio de deporte y menciones explícitas

| # | Selección | Pregunta | Esperado |
|---|---|---|---|
| B1 | Fútbol | ¿Cómo va Preferente Aficionado de fútbol sala? | Sin consultar datos: «Tu pregunta es de fútbol sala…» y botón «Cambiar a Fútbol sala y preguntar» |
| B2 | (pulsar B1) | — | Conversación nueva de Fútbol sala con la misma pregunta: «PREFERENTE AFICIONADO FS» |
| B3 | Fútbol sala | ¿Qué partidos de fútbol hay este sábado? | Botón «Cambiar a Fútbol y preguntar»; no se mezclan partidos de fútbol sala |
| B4 | Fútbol | ¿Y en futsal? (tras A3) | Propuesta de cambio a Fútbol sala |
| B5 | Fútbol sala | ¿Quién ganó el último Mundial de fútbol 11? | Aviso de fuera de ámbito (no propone cambiar de deporte) |
| B6 | Fútbol | Cuéntame un chiste | Aviso de fuera de ámbito **de fútbol (fútbol 11)** |
| B7 | Fútbol sala | (con mensajes) pulsar «Fútbol» en el selector | Conversación nueva vacía en Fútbol; la anterior sigue en el historial como fútbol sala |
| B8 | — | Abrir del historial una conversación antigua | El selector muestra su deporte (las anteriores a la Fase 1 son de fútbol sala) |

## C. Fútbol sala (regresión: nada debe cambiar)

| # | Pregunta | Esperado |
|---|---|---|
| C1 | ¿Cómo va el DOSA en fútbol sala? | Como antes de la Fase 1 |
| C2 | ¿Quién jugó de titular en el último partido del DOSA? | Pista con el quinteto inicial (vista gráfica por defecto) |
| C3 | ¿Cuántos cambios se pueden hacer en fútbol sala? | Reglamento de fútbol sala (search_rules) |
| C4 | ¿Qué sustituciones hubo en el partido? | «El acta que publica la RFFM no recoge las sustituciones…» (como siempre) |
