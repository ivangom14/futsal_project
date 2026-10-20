# Fase 15 (RAG) — Entrega 1: extracción del PDF de FIFA

Fuente: `FUTSAL-Laws-of-the-Game-2025-2026_ES.pdf` (Reglas de Juego del Futsal 2025/26), 197 páginas,
4.808.756 bytes, sha256 `8216deb12e7b29a2458dbdcec5852aa1759d63088f0c57b7b62965b8ae714323`.
Descargado en `data/knowledge/fifa-futsal-laws-2025-26/` (ignorado por Git) con `source.json` (URL, hash, fecha de descarga).

Metadatos que constan en el PDF: creador Adobe InDesign 20.4, creado el 2025-09-05. En la página 3 figura
«Entrada en vigor: 5 de septiembre de 2025» (no es fecha de publicación) y la temporada 2025/26 figura en el título.
No consta versión ni fecha de publicación explícitas: no se inventan.

## Método
`pypdf` (texto por página y fuente de cada tramo) → `structure.py` con un `StructureProfile` por tipo de documento
(`profiles.py`): elimina cabeceras/pies, detecta portadillas de regla, apartados numerados correlativos,
subtítulos (fuente FIFASans-Medium) y agrupa párrafos conservando la página de cada uno.
Comandos: `python -m futsal.cli knowledge-fetch ...`, `knowledge-inspect --file ... --profile fifa-futsal-laws`.

## Resultados medidos
- Páginas del PDF = números impresos en las 197 páginas (referencia de página verificable).
- Texto: 196/197 con texto; sin texto solo la **página 197** (contraportada, una imagen JPEG). 0 caracteres ilegibles. No hace falta OCR.
- Tipos: 173 contenido, 19 portadillas, 5 de índice (4-8, excluidas del índice de búsqueda).
- Estructura: 17 reglas detectadas, con la página de inicio coincidente con el índice del propio documento (17/17).
  Apartados por regla: R1 15, R2 6, R3 12, R4 7, R5 7, R6 4, R7 5, R8 2, R9 3, R10 4, R11 0 (no existe fuera de juego en futsal), R12 4, R13 6, R14 3, R15 2, R16 2, R17 2.
  Partes no numeradas: Introducción (1-13), Protocolo VAR (122-128), Señales, Posicionamiento, Interpretación de las reglas, Términos.
- Contraste con una segunda extracción independiente (`pdftotext`): 195/197 páginas con solapamiento de palabras ≥ 0,90
  (p. 17: 0,86 por etiquetas de figura; p. 197 vacía). 1.618 de 1.641 párrafos (98,6 %) empiezan en la página que se les asigna;
  los 23 restantes son etiquetas de diagramas, números de medidas y la p. 55 (texto en árabe en una figura).

## Incidencias
- Tablas (p. 22, 89-90, 105, 111, 134-135, 160, 186, 195) y figuras con pies de foto: el texto se extrae en orden de lectura
  aproximado; las celdas de la tabla de medidas disciplinarias (p. 89-90) quedan linealizadas. Marcadas `posible_tabla_o_figura`.
- Páginas 130-141 (señales arbitrales): casi solo ilustraciones; se conserva el texto de los pies de figura.
- Apartado 3 de la Regla 12 (p. 82-91) y varios bloques de «Interpretación» superan 6.000 caracteres: el fragmentador de la
  Entrega 2 debe dividirlos por párrafos.
- Título de la Regla 7 en la cabecera: «La duracióndel partido» (falta un espacio en el PDF); se usa el de la portadilla.
- fontTools no está instalado: pypdf avisa de codificaciones CFF; se silencia y se verifica que no hay texto corrupto.
