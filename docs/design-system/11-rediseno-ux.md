# 11. Rediseño UX/UI integral (Pista v2)

Fecha: 2026-11-17 · Alcance: solo `frontend/` (más este documento). Sin cambios de backend, API, MCP, agente ni contratos.
Sin commit (pendiente de autorización del responsable).

Las cuatro capturas de iPhone mencionadas en el encargo **no llegaron a esta sesión**: la auditoría se hizo sobre la app real
en marcha (Vite + Playwright, `/chat` simulado con el contrato real) en 5 tamaños de pantalla.

Referencias de criterio (conocimiento previo, **no consultadas en línea en esta sesión**): WCAG 2.2 (1.4.3 contraste 4,5:1,
1.4.11 contraste de componentes 3:1, 2.4.7/2.4.11 foco, 2.5.8 tamaño mínimo 24 px AA y 2.5.5 44 px AAA), Apple HIG (objetivos de
44 pt, áreas seguras), Material (48 dp), NN/g (revelación progresiva, reconocimiento antes que recuerdo, minimalismo), web.dev
(`dvh`/`svh`, `visualViewport`, `interactive-widget`, LCP/INP/CLS).

## A. Auditoría priorizada

Gravedad: 1 crítica · 2 alta · 3 media · 4 baja. Esfuerzo: S/M/L. Tipo: V visual · I interacción · A agente/API (no se arregla en la presentación).

| # | Problema observado | Tipo | Grav. | Impacto | Esf. | Solución aplicada |
|---|---|---|---|---|---|---|
| 1 | Listados: ~110–120 px por partido; fecha, competición y «Programado» repetidos en cada fila; 3 líneas de metadatos antes de los equipos | V | 2 | Leer 10 partidos exigía 4 pantallas | M | Fila deportiva compacta: hora a la izquierda, equipos apilados con escudo, goles alineados a la derecha (ganador en negrita); agrupación por día y competición; lo común (jornada, competición, «Todos programados») una vez en la cabecera; estado solo si es excepcional (Aplazado, Suspendido…) |
| 2 | Paginación: el texto repetía «Mostrando 1–10 de 48» y «¿Quieres que te muestre los siguientes 10?», y el «ver más» era un chip suelto bajo el pie | V/I | 2 | Duplicidad y acción poco visible | S | `tidyAnswer` quita SOLO esas dos frases cuando hay visual paginado; el chip del backend se convierte en «Ver 10 partidos más · quedan 38» dentro de la tarjeta (misma petición, 0 LLM) |
| 3 | El auto-scroll llevaba al FINAL de cada respuesta: en respuestas largas había que subir para leer desde el principio | I | 2 | Fricción en cada consulta | S | Al llegar la respuesta, la pregunta queda arriba; al preguntar, al final; posición de lectura recordada por conversación; botón «Ir al final» |
| 4 | Teclado iOS: la app usaba `height:100%` sin `viewport-fit`, sin `dvh` ni `visualViewport` (el compositor podía quedar bajo el teclado; `env(safe-area-*)` valía 0) | I | 2 | Uso con una mano en iPhone | M | `100dvh` + `--app-h` desde `visualViewport` solo con un campo enfocado; `viewport-fit=cover`, `interactive-widget=resizes-content`; áreas seguras en cabecera, cajón y compositor |
| 5 | Cabecera de 3 elementos por cada respuesta (avatar + «Futsito» + insignia «Datos oficiales RFFM») | V | 3 | Ruido repetido | S | Respuesta sin cabecera; procedencia y **fecha de actualización** en el pie («Fuente: … · act. 16/11»), valoración en la misma línea |
| 6 | Árbitros dibujados como monigotes de 52×84 px | V | 3 | 260 px para 3 nombres | S | Lista compacta cargo + nombre |
| 7 | Alineación dibujada por defecto en una pista con posiciones **inventadas** (el acta no las trae) | V | 2 | Sugiere un dato que no existe | S | Lista de titulares/suplentes por defecto; «Ver en pista» opcional con el aviso de colocación orientativa |
| 8 | Historial: papelera permanente en cada fila, sin agrupar ni buscar; la conversación vacía aparecía en la lista | V/I | 3 | Borrados accidentales, difícil encontrar | M | Grupos Hoy/Ayer/7/30 días/Anteriores, búsqueda (≥ 6 conversaciones, sin tildes, también en las preguntas), selección con barra de marca, menú «⋯» con Borrar (+ «Deshacer» existente) |
| 9 | «Conectado» permanente; aviso «Futsito tiene en cuenta los mensajes anteriores…» ocupando una línea; ayuda «Enter envía» visible | V | 3 | ~40 px de ruido fijo | S | Estado solo si hay problema; contexto solo cuando hay foco («Sobre C.D. DOSA ×»); ayuda de teclado para lectores de pantalla |
| 10 | Estado inicial: logo de 96 px, saludo y dos párrafos; 2 de 4 ejemplos ambiguos (provocaban aclaración) | V/I | 3 | Primer uso lento | S | Una línea de capacidades + 4 consultas reales por capacidad verificada (fin de semana, tabla, goleadores, reglamento), pegadas al compositor |
| 11 | Clasificación en móvil con G/E/P visibles: nombres partidos en 4 líneas | V | 3 | Tabla difícil de escanear | S | En tarjetas < 26 rem quedan Pos·Equipo·PJ·DG·Pts; «Ver todas las columnas» conserva el resto |
| 12 | Paleta crema/marrón; cabeceras de tarjeta con banda de color; burbuja del usuario casi negra | V | 4 | Aspecto «rústico», poco neutro | S | Neutros cálidos casi grises, rojo del logo solo en marca/acentos, una sola tarjeta con borde fino |
| 13 | Tablas Markdown del agente (comparativas) sin estilo ni contenedor desplazable | V | 3 | Desbordes posibles | S | Contenedor con scroll propio enfocable (axe), estilo de tabla deportiva |
| 14 | Respuestas muy largas sin revelación progresiva | I | 4 | Scroll largo | S | > 1.400 caracteres: pliegue con «Mostrar respuesta completa» (texto completo en el DOM) |
| 15 | 320 px: los nombres reales más largos (51–60 car.) se recortaban a 4 líneas | V | 3 | Pérdida de información | S | En tarjeta < 18,5 rem se oculta el escudo decorativo y se reduce el relleno: 0 recortes |
| A1 | «¿Qué juveniles del club han jugado con el sénior?» | A | 2 | No hay herramienta que cruce actas por categoría (solo `find_player` por nombre y equipos de un club) | L | **Pendiente de API/agente**; el frontend ya pinta la tabla Markdown que devuelva |
| A2 | Último resultado de un equipo sin tarjeta de marcador | A | 3 | `presentation.py` solo emite `matches` con ≥ 2 filas y la forma reciente va aparte | S | **Pendiente**: emitir un `matches` de 1 fila (o un `score` estructurado) haría posible un «ScoreHero» |
| A3 | Un foco previo no debe restringir una pregunta explícita más amplia | A | 2 | Es resolución de consultas (v2 `multi_scope_kind`), no de presentación | — | Fuera de alcance; la UI solo muestra el foco y permite quitarlo |
| A4 | Quitar el contexto = conversación nueva | A | 4 | El foco vive en el servidor por conversación; no hay endpoint para vaciarlo | S | Pendiente: `DELETE /chat/conversations/{id}/focus` permitiría quitarlo sin cambiar de conversación |
| A5 | El texto del agente a veces narra lo que ya muestra la tarjeta | A | 3 | Lo resuelve en parte `reconcile.py`; el resto es del prompt | — | En frontend solo se quitan las frases de paginación deterministas |

## B. Sistema visual (tokens en `src/styles/tokens.css`, mapeados en `tailwind.config.js`)

**Color** (todos verificados; texto normal ≥ 4,5:1, controles ≥ 3:1):

| Token | Claro | Oscuro | Uso | Contraste medido |
|---|---|---|---|---|
| canvas / surface / surface-2 | #f5f5f3 / #fff / #efeeeb | #0e0e10 / #18181b / #222226 | fondo, tarjetas, cabeceras de grupo | — |
| ink / ink-2 | #17171a / #5c5a55 | #f2f1ee / #a9a8a3 | texto / secundario | ink-2: 6,3 (claro) · 8,1 (oscuro) sobre canvas |
| line / line-strong | #e3e1dc / #8a8781 | #2e2e33 / #8b8a92 | separadores / borde de controles | line-strong 3,6 · 5,2 |
| brand / brand-text | #c0281e / #a21f17 | #c0281e / #ff8a7e | rojo del logo: botón principal, selección, acentos | blanco sobre brand 5,9 |
| accent | #1f5fae | #86b6f0 | enlaces, acciones de tarjeta, aclaraciones | 5,8 · 9,2 |
| success / warning / danger | #17703f / #8a5300 / #b3261e | #5cc98a / #f0b45a / #ff8a80 | V/E/D, avisos, errores (siempre con icono o letra) | ≥ 5,5 |
| bubble | #e9e8e4 | #2a2a2f | pregunta del usuario | — |

**Tipografía**: pila del sistema para la conversación (16 px: evita el zoom de iOS); Archivo Variable (ya autoalojada) solo para
marca, títulos de tarjeta y cifras (`stretch-score` 75 %, `stretch-table` 87,5 %). Escala: 11 (antetítulos en versalitas), 12, 13, 15, 16, 18, 24.

**Espaciado** base 4 px; relleno de fila 8×12; separación entre turnos 24. **Radios**: 6 / 10 / 14 px (sin pastillas gigantes
salvo chips y compositor). **Sombras**: solo `shadow-pop` en elementos flotantes (menú, cajón, «Ir al final»). **Alturas táctiles**:
44 px en puntero grueso (`[@media(pointer:coarse)]`), 36–40 px con ratón. **Movimiento**: 160 ms, sin animaciones con `prefers-reduced-motion`.

**Componentes**: `ui/Panel` (contenedor único de visuales: antetítulo de competición + titular + subtítulo, acción de pie
`PanelAction`), `Chip` (sugerencia / opción de aclaración), `Button`, `Notice`, `TeamCrest`. Criterios: una tarjeta por dato,
nunca tarjetas dentro de tarjetas; lo común se dice una vez; estados con texto, no solo color; nombres completos, nunca «…».

## C. Implementación (archivos)

- `index.html`: `viewport-fit=cover`, `interactive-widget=resizes-content`, `theme-color` claro/oscuro.
- `src/styles/tokens.css`, `tailwind.config.js`, `src/index.css`: tokens v2; alto `100dvh`/`--app-h`; filas de partido, tabla, ranking, Markdown, pliegue.
- `src/viewport.ts` (nuevo): alto visual con teclado abierto (iOS).
- `src/App.tsx`: cabecera de 52 px (menú · marca · nueva consulta; en escritorio, título de la conversación), desplazamiento por turnos y por conversación, «Ir al final», mensajes memorizados con callbacks estables, salud sondeada solo con la pestaña visible.
- `src/components/Composer.tsx`: una fila, `enterkeyhint=send`, contexto «Sobre … ×» solo con foco, contador solo cerca del límite.
- `src/components/Sidebar.tsx`: grupos temporales, búsqueda, menú de acciones accesible, selección marcada, vacía no listada.
- `src/components/MessageBubble.tsx`: sin cabecera repetida, etiqueta de análisis, `tidyAnswer`, «ver más» en tarjeta, tablas Markdown, pliegue de respuestas largas, `memo`.
- `src/components/Matches.tsx` (reescrito), `Visuals.tsx` (clasificación, ranking con revelación, forma con racha), `Lineup.tsx` (lista + pista opcional), `Officials.tsx` (lista), `Timeline.tsx`, `Cards.tsx` (sobre `Panel`), `Welcome.tsx`, `Suggestions.tsx`, `SourceFooter.tsx` (fecha visible), `FeedbackBar.tsx` (en línea), `ConnectionBadge.tsx` (solo problemas), `AgentStatus.tsx` (compacto).
- `src/ui/Panel.tsx` (nuevo), `src/ui/Chip.tsx`, `src/format.ts` (`dayHeading`, `parseScore`).
- Pruebas: `src/redesign.test.tsx` (nuevo, 11) y adaptación de las existentes a la nueva estructura; `scripts/check-layout.mjs` mide la nueva maqueta.

Persistencia intacta: misma clave `rffm-chat-v1` y mismo formato; ninguna conversación se migra ni se borra.

## D. Validación

- **Vitest**: 135/135 (121 previas, adaptadas donde comprobaban la maqueta antigua, + 11 nuevas + 3 reescritas de partidos). `tsc` sin errores. 0 llamadas a LLM.
- **Capturas reales** (Playwright, app en Vite, `/chat` simulado con el contrato real): iPhone SE 375×667, iPhone 393×852, Android 412×915, tablet 820×1180, escritorio 1440×900; claro y oscuro; estados de carga, error de red, teclado (viewport reducido), cajón abierto. Escenarios: último resultado, próximos partidos, fin de semana sénior paginado (48), clasificación (14 equipos), goleadores (15 con empates), acta (alineación, árbitros, goles, tarjetas), comparación juvenil/sénior (tabla Markdown), nombres de 100+ caracteres, marcador no numérico, fecha pasada, estado vacío, lista vacía, aclaración con opciones, análisis no oficial, respuesta extensa.
- **axe-core 4.10 (WCAG 2.0/2.1/2.2 A y AA)**: 0 infracciones en 18 combinaciones (9 escenarios × claro/oscuro) tras corregir la única hallada (tabla desplazable no enfocable). 0 controles < 24 px. 0 desbordamientos horizontales.
- **`check-layout.mjs`** (nombres más largos del catálogo real, 6 anchos 320–1280): 0 desbordes, 0 nombres reales recortados (solo el nombre absurdo de 107 car.), goles alineados con su equipo (0 px de error).
- **Rendimiento** (build de producción, CPU ×4, conversación con 4 turnos y 3 visuales): LCP móvil 456 ms (antes 732), escritorio 550–640 ms (antes 550–690); CLS 0,000 (igual); interacción máx. 40–96 ms (antes 48–96, diferencia dentro del ruido). Son tiempos de frontend: la latencia del agente LLM no se midió ni cambia.
- **Peso**: JS 125,0 KB gz (+5,4: ~16 iconos Lucide y el historial), CSS 9,0 KB gz (+1,3). Sin dependencias nuevas.

**Limitaciones**: sin dispositivo iOS real (teclado emulado reduciendo el viewport; el comportamiento de `visualViewport` en Safari debe comprobarse en un iPhone); sin lector de pantalla real (VoiceOver/TalkBack): solo axe y pruebas de roles; INP aproximado con Event Timing en una sola interacción; capturas con datos simulados realistas (nombres reales del catálogo), no con el agente vivo.

## E. Antes / después

- **Densidad**: el listado del fin de semana pasa de unos 3 a unos 5 partidos visibles por pantalla en iPhone (79 px de media por partido con nombres reales largos); la información repetida en cada fila (fecha, competición, estado) se dice una vez.
- **Usabilidad**: «ver más» dentro de la tarjeta; la respuesta se lee desde su inicio; historial con búsqueda y grupos; nueva consulta a un toque desde la cabecera.
- **Navegación**: cabecera estable de 52 px; en escritorio, el título de la conversación orienta; selección marcada con barra de marca y `aria-current`.
- **Accesibilidad**: objetivos de 44 px en táctil, frase completa por partido para lectores de pantalla, estados con texto, regiones desplazables enfocables, foco devuelto al cerrar menús.
- **Rendimiento**: menos DOM por respuesta (sin monigotes ni pista por defecto), mensajes memorizados, sin sondeo en segundo plano.

Capturas y láminas comparativas: carpeta de trabajo de la sesión (no versionadas).

## F. Pendientes

Agente/API/datos (no se resuelven en la presentación): A1 participaciones cruzadas por categoría; A2 marcador estructurado de un único partido; A3 alcance amplio frente a foco previo (resolución de consultas); A4 vaciar el foco sin cambiar de conversación; A5 narración redundante del LLM.
Frontend: verificar en iPhone real (teclado, áreas seguras) y con VoiceOver; valorar `content-visibility` si las conversaciones superan ~50 turnos; escudos oficiales solo cuando la RFFM autorice su uso público (`CRESTS_MODE`).
