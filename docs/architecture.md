# Arquitectura

Monolito modular en Python: un único repositorio y un único proceso desplegable,
con módulos de dependencias unidireccionales. Sin microservicios ni colas.

## Módulos (`src/futsal/`)

| Módulo | Responsabilidad |
|---|---|
| `domain` | Entidades y reglas puras, sin dependencias de infraestructura |
| `ingestion` | Adaptadores por fuente (RFFM, …) que producen entidades de dominio |
| `repositories` | Persistencia en PostgreSQL (SQLAlchemy 2, Alembic) |
| `api` | API REST (FastAPI); única puerta de acceso a los datos |
| `mcp_server` | Herramientas MCP de solo lectura, cliente de la API |
| `knowledge` | Procesado y búsqueda de documentos (pgvector) |
| `rules` | Clasificaciones y desempates deterministas |
| `agent` | Orquestación: decide qué herramientas invocar |
| `config` | Configuración (variables de entorno) |

Dependencias permitidas: `domain` no depende de nadie; `rules` solo de `domain`;
`ingestion` y `repositories` dependen de `domain`; `api` de `repositories` y `rules`;
`mcp_server` y `agent` solo hablan con la API/herramientas, nunca con la BD.

## Flujo de ingesta

Fuente oficial → adaptador (`ingestion`) → normalización a entidades de `domain`
→ `repositories` → PostgreSQL. La ingesta es determinista e idempotente: misma
entrada, mismo resultado; reejecutarla no duplica datos. Descargas en bruto se
guardan como snapshots para reutilizarlas y auditar el parseo.

## Datos transaccionales vs. conocimiento documental

- **Transaccional** (equipos, partidos, resultados, sanciones): cambia a menudo,
  es estructurado y se consulta por API.
- **Documental** (reglamentos, bases, circulares): texto largo y de cambio lento,
  se trocea, se indexa con embeddings en pgvector y se busca semánticamente.

Ambos viven en PostgreSQL pero con esquemas y ciclos de vida separados.

## API como capa de acceso

FastAPI expone lectura de datos y resultados del motor de reglas. Es el contrato
estable: validación con Pydantic, versionado de rutas y único punto de control
de acceso. Ningún otro componente lee las tablas directamente.

## MCP sobre la API

El servidor MCP traduce endpoints de lectura a herramientas para el agente. Es de
solo lectura y no contiene lógica de negocio: reutiliza la API.

## Knowledge base

Herramienta independiente (ingesta documental + búsqueda). Devuelve fragmentos con
cita (documento, versión, página/artículo) para que el agente pueda fundamentar
respuestas normativas.

## Motor de reglas

`rules` calcula clasificaciones y desempates con funciones puras y deterministas,
parametrizadas por las bases de cada competición. Nunca se delega al LLM un
cálculo que pueda hacerse de forma determinista.

## Agente orquestador

Recibe la pregunta y elige entre: datos (MCP), normativa (knowledge), cálculo
(rules) o una combinación. Cada respuesta debe indicar de qué fuente procede cada
afirmación y, ante falta de datos, decirlo en lugar de inventar.

## Procedencia, versionado y frescura

- **Procedencia**: cada registro guarda fuente, URL y referencia al snapshot.
- **Versionado**: los documentos conservan versiones (fecha de vigencia); los
  cambios en datos transaccionales se registran sin perder el valor anterior
  cuando afecte a resultados o clasificaciones.
- **Frescura**: cada fuente registra `fetched_at`; la API y el agente exponen la
  antigüedad del dato para que pueda advertirse al usuario si está desactualizado.
