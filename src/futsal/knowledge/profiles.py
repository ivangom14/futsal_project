"""Perfiles de estructura por tipo de documento."""
from __future__ import annotations

import re

from futsal.knowledge.structure import StructureProfile

_UP = "A-ZÁÉÍÓÚÑÜ"


def _c(p: str) -> re.Pattern[str]:
    return re.compile(p)


FIFA_FUTSAL_LAWS = StructureProfile(
    running_headers=(
        _c(r"Reglas de Juego del Futsal 2025/26"),
        _c(r"(Observaciones sobre las Reglas de Juego del Futsal|Índice|Posicionamiento"
           r"|Protocolo de sistemas de revisión en vídeo"
           r"|Directrices prácticas para árbitros de futsal y otros"
           r"|(y otros )?miembros del equipo arbitral)"),
    ),
    rule_header=_c(r"Regla (\d+)\s+•\s+(.+)"),
    rule_divider=_c(r"Regla (\d+)"),
    part_dividers=(
        (_c(r"^Protocolo de sistemas de revisión en vídeo$"),
         "Protocolo de sistemas de revisión en vídeo"),
        (_c(r"^Directrices prácticas para árbitros de futsal"),
         "Directrices prácticas para árbitros"),
    ),
    part_headings=(
        (_c(r"SEÑALES"), "Señales"),
        (_c(r"POSICIONAMIENTO"), "Posicionamiento"),
        (_c(r"INTERPRETACIÓN DE LAS REGLAS Y OTRAS RECOMENDACIONES"),
         "Interpretación de las reglas y otras recomendaciones"),
        (_c(r"TÉRMINOS DE FUTSAL"), "Términos de futsal"),
        (_c(r"TÉRMINOS ARBITRALES"), "Términos arbitrales"),
    ),
    toc_rule_entry=_c(r"REGLA (\d+)\.[^\d]*?\s(\d{1,3})(?=\s|$)"),
    section_heading=_c(rf"(\d{{1,2}})\s+([{_UP}][{_UP}0-9 ,/\-.()]{{2,}})"),
)

# Circular de la RFEF que adjunta circular FIFA, resumen de cambios y reglas: los tramos de páginas los declara el
# `DocumentSpec`; aquí solo la cabecera de la FIFA (repetida en cada hoja) y las reglas citadas en el resumen de cambios.
RFEF_CIRCULAR = StructureProfile(
    running_headers=(_c(r"Fédération Internationale de Football Association"), _c(r"FIFA-Strasse 20")),
    rule_inline=_c(r"(?i)Regla\s+(\d{1,2})\.\s+\S"),
    strip_edge_numbers=True,
    # Subapartados del resumen de cambios: abren su propia parte para que no hereden la regla anterior.
    part_headings=(
        (_c(r"Protocolo de"),
         "Resumen de cambios 2025/26 (FIFA; sin cursiva ni tachado) · Protocolo de sistemas de revisión en vídeo"),
        (_c(r"Directrices"),
         "Resumen de cambios 2025/26 (FIFA; sin cursiva ni tachado) · Directrices prácticas para árbitros"),
        (_c(r"Interpretación de"),
         "Resumen de cambios 2025/26 (FIFA; sin cursiva ni tachado) · Interpretación de las reglas y otras recomendaciones"),
    ),
)

PROFILES: dict[str, StructureProfile] = {
    "rfef-circular": RFEF_CIRCULAR,
    "fifa-futsal-laws": FIFA_FUTSAL_LAWS,
    "plain": StructureProfile(),
}
