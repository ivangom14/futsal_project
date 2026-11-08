# v2 · Funcionalidades futuras (propuestas)

Cada idea indica de qué depende. Ninguna exige rehacer la arquitectura: todas se apoyan en el catálogo estructurado, la
identidad de club y equipo, la API de dominio y el planificador.

## Seguimiento de «mi equipo» (prioridad alta: más valor, menos dependencias)
- **Seguir equipos o clubes** (perfil de usuario): «mi equipo» se resuelve sin preguntar. *Depende de*: usuarios (A7), linaje.
- **Avisos**: resultado final, cambio de horario o aplazamiento, cambio de posición. El planificador ya detecta el cambio; solo
  falta el canal (correo, Telegram o push web). *Depende de*: T06.
- **Resumen semanal**: el viernes, la previa de la jornada (rival, forma y horario); el domingo por la noche, resultados y
  clasificación. Se genera en T0/T1 con coste casi nulo.
- **Calendario ICS** del equipo o del club, que se actualiza solo.
- **«¿Qué necesito para…?»**: escenarios de ascenso, descenso o liderato con los partidos restantes. Exige las normas de cada
  competición (puestos de ascenso y desempates) en `rules/`, un cálculo determinista y nunca el LLM.
- **Historial**: enfrentamientos directos y evolución del equipo entre temporadas (linaje).

## Entrenadores
- **Informe del rival**: forma, local y visitante, goles a favor y en contra por tramos de minutos (actas), tarjetas, máximos
  goleadores y portero titular habitual.
- **Riesgo de sanción** por acumulación de amarillas, si las normas disciplinarias de la competición están en la KB.
- **Uso de la plantilla** desde las actas: convocatorias, titularidades y goles por jugador.

## Árbitros y reglamento
- **Preguntas de reglamento con cita** (ya existe) más los documentos de la RFFM.
- **Qué cambió esta temporada**: diferencias entre circulares y versiones (`kb_document_versions` ya guarda versiones).
- **Modo práctica**: casos de juego y preguntas tipo test generados desde el reglamento, con la respuesta citada.
- **Situaciones**: «saque de banda con el balón en movimiento» → regla y criterio de la circular vigente.

## Clubes y coordinadores
- **Panel del club**: todos sus equipos (A, B, C…) en todas las categorías, con su posición, la agenda del fin de semana y
  los resultados.
- **Agenda de instalaciones**: partidos por pabellón y día (`venue`), para detectar solapes.

## Aficionados y medios
- **Crónica automática de la jornada** con datos verificados (resultados, goleadores, rachas y sorpresas por posición).
- **Récords y rachas**: invictos, goleadas, máximos goleadores históricos (multitemporada).
- **Widgets embebibles** (clasificación o próximo partido) para webs de clubes, servidos por la API de dominio.

## Plataforma
- API pública con clave y cuotas por cliente; otras federaciones (nuevo `source`); exportaciones (CSV).

## Privacidad (decisión necesaria antes de ampliar categorías: A9)
En las categorías inferiores a senior, los jugadores son en su mayoría **menores**. Publicar nombres, goles, tarjetas o
alineaciones de menores mediante un asistente abierto plantea un riesgo legal (RGPD) y reputacional, aunque la federación
los muestre. Propuesta: una política por categoría (`player_data_policy`: `full`, `aggregate_only` u `hidden`) aplicada en la
API de dominio. Por defecto, `aggregate_only` para juvenil e inferiores (resultados y clasificaciones sí; datos personales
de jugadores no), y lo mismo para árbitros y técnicos en las respuestas libres.
