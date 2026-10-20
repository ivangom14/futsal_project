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

PROFILES: dict[str, StructureProfile] = {
    "fifa-futsal-laws": FIFA_FUTSAL_LAWS,
    "plain": StructureProfile(),
}
