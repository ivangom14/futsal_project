"""Documentos conocidos de la base de conocimiento (añadir una entrada = nuevo documento)."""

from dataclasses import dataclass
from datetime import date

from futsal.knowledge.ingest import DocumentSpec
from futsal.knowledge.models import PageSection


@dataclass(frozen=True)
class KnowledgeSource:
    spec: DocumentSpec
    file_name: str = "original.pdf"


KNOWLEDGE_SOURCES: dict[str, KnowledgeSource] = {
    "fifa-futsal-laws-2025-26": KnowledgeSource(DocumentSpec(
        doc_key="fifa-futsal-laws-2025-26",
        title="Reglas de Juego del Futsal 2025/26",
        doc_type="reglamento",
        language="es",
        structure_profile="fifa-futsal-laws",
        source_url="https://digitalhub.fifa.com/m/7f361c82a13cd623/original/"
                   "FUTSAL-Laws-of-the-Game-2025-2026_ES.pdf",
        issuer="FIFA",
        publication_date=None,  # no consta en el documento
        effective_date=date(2025, 9, 5),  # «Entrada en vigor» (p. 3)
        version_label="2025/26",
        season_label="2025/26",
    )),
    # PDF de 220 páginas: carta de la RFEF (p. 1), Circular FIFA 1941 (p. 2), resumen de cambios (p. 3-23) y las Reglas de
    # Juego completas (p. 24-220). Estas últimas son idénticas, página a página (p. 24-220 ↔ p. 1-197), al documento
    # `fifa-futsal-laws-2025-26`: no se indexan para no duplicar fragmentos. Ver docs/phase15-circular27.md.
    "rfef-circular-27-2025-26": KnowledgeSource(DocumentSpec(
        doc_key="rfef-circular-27-2025-26",
        title="Circular n.º 27 de la RFEF: Reglas de Juego del Futsal 2025/2026 (Circular FIFA n.º 1941)",
        doc_type="circular",
        language="es",
        structure_profile="rfef-circular",
        source_url="https://rfef.es/sites/default/files/pdf/circulares/Circular_27_con_anexos.pdf",
        issuer="Real Federación Española de Fútbol (RFEF)",
        publication_date=date(2025, 9, 8),  # carta de la RFEF (p. 1); la circular FIFA es del 5-sep-2025 (p. 2)
        effective_date=None,  # la circular no fija fecha de entrada en vigor
        version_label="Circular 27 · 2025/26",
        season_label="2025/26",
        page_sections=(
            PageSection(1, 1, "Comunicación de la RFEF (Circular 27)"),
            PageSection(2, 2, "Circular FIFA n.º 1941"),
            PageSection(3, 23, "Resumen de cambios de las Reglas de Juego 2025/26 (FIFA; sin cursiva ni tachado)"),
            PageSection(24, 220, "Reglas de Juego del Futsal 2025/26 con cambios resaltados (anexo)",
                        skip_reason="texto idéntico al documento fifa-futsal-laws-2025-26 (p. 24-220 = p. 1-197)"),
        ),
    )),
}
