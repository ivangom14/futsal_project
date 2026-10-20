"""Documentos conocidos de la base de conocimiento (añadir una entrada = nuevo documento)."""

from dataclasses import dataclass
from datetime import date

from futsal.knowledge.ingest import DocumentSpec


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
}
