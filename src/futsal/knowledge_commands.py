"""Comandos CLI de la base de conocimiento (Fase 15): knowledge-fetch, knowledge-inspect."""

import argparse
import json
from pathlib import Path

from futsal.knowledge.extract import ExtractionError, get_extractor
from futsal.knowledge.fetch import FetchError, fetch_document
from futsal.knowledge.profiles import PROFILES
from futsal.knowledge.quality import assess
from futsal.knowledge.structure import build_structure

KNOWLEDGE_DIR = Path("data/knowledge")
COMMANDS = ("knowledge-fetch", "knowledge-inspect")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    f = sub.add_parser("knowledge-fetch", help="descarga un documento público y registra su procedencia")
    f.add_argument("--url", required=True)
    f.add_argument("--doc-id", required=True, help="identificador estable (carpeta bajo data/knowledge)")
    f.add_argument("--output", type=Path, default=KNOWLEDGE_DIR)
    f.add_argument("--refresh", action="store_true")
    i = sub.add_parser("knowledge-inspect", help="extrae un documento y muestra la calidad (sin BD)")
    i.add_argument("--file", type=Path, required=True)
    i.add_argument("--profile", choices=sorted(PROFILES), default="plain")
    i.add_argument("--json", type=Path, default=None, help="guarda el informe completo en JSON")
    i.add_argument("--page", type=int, default=None, help="muestra los segmentos de una página")


def run(args: argparse.Namespace) -> int:
    if args.command == "knowledge-fetch":
        try:
            info = fetch_document(args.url, args.output / args.doc_id, refresh=args.refresh)
        except FetchError as exc:
            print(f"Error: {exc}")
            return 2
        print(f"{info['file']} sha256={str(info['sha256'])[:12]}… bytes={info['bytes']}"
              f"{' (reutilizado)' if info.get('reused') else ''}")
        return 0
    profile = PROFILES[args.profile]
    try:
        doc = get_extractor(args.file).extract(args.file)
    except (ExtractionError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 2
    structured = build_structure(doc, profile)
    report = assess(doc, structured, profile)
    print(report.render())
    if args.page:
        for seg in structured.segments:
            if seg.page_start <= args.page <= seg.page_end:
                print(f"--- {seg.part} §{seg.section_number} {seg.section_title} "
                      f"[{seg.subsection_title}] p{seg.page_start}-{seg.page_end}")
                print(seg.text[:600])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return 0
