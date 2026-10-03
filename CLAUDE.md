# Reglas de trabajo para Claude

Proyecto: asistente agéntico de competiciones deportivas. Monolito modular Python.

## Antes de empezar
- Leer primero `PROJECT_CONTEXT.md`.
- Inspeccionar solo los archivos relacionados con la fase actual.
- Usar búsquedas dirigidas con `rg` antes de abrir archivos completos.
- No recorrer todo el repositorio salvo que sea imprescindible.
- No volver a leer archivos sin cambios.

## Gestión del contexto
- No introducir HTML, JSON, documentos o logs completos en el contexto.
- Descargar y procesar contenidos grandes mediante scripts.
- Examinar únicamente fragmentos representativos.
- Reutilizar snapshots y caché existentes.

## Alcance
- No usar subagentes salvo petición explícita.
- No ampliar el alcance de una fase.
- No modificar componentes no relacionados.
- No crear código ficticio, clases vacías ni archivos aún innecesarios.

## Pruebas
- Ejecutar pruebas específicas durante el desarrollo.
- Reservar la batería completa para la fase final.

## Cierre de fase
- No mostrar código ni archivos completos en la respuesta.
- Actualizar `PROJECT_CONTEXT.md` al terminar.
- Responder con un resumen de máximo diez líneas.

## Arquitectura (resumen; detalle en `docs/architecture.md`)
- Código en `src/futsal/<módulo>`; `domain` no depende de infraestructura.
- `mcp_server` y `agent` acceden a datos solo vía API.
- Cálculos deterministas en `rules`, nunca en el LLM.
