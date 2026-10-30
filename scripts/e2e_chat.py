"""Validación E2E REAL de la Fase 22 (consume cuota del LLM). Uso (con API 8000 y chat 8001 arrancados):

    python scripts/e2e_chat.py            # solo comprobaciones sin LLM (health) y muestra el plan
    python scripts/e2e_chat.py --yes      # envía 3 preguntas al agente real (≈ 3 ejecuciones del agente)

Solo usa la biblioteca estándar. No lee ni imprime claves.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

QUESTIONS = [
    ("datos", "¿Cuáles son los próximos partidos de Primera Autonómica Senior Grupo 2?", False),
    ("clasificación", "¿Quién es el máximo goleador de Primera Autonómica Senior Grupo 2?", False),
    ("normativa", "¿Cuánto dura cada mitad de un partido de fútbol sala?", True),  # debe traer fuentes del RAG
]


def call(url: str, body: dict[str, str] | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--yes", action="store_true", help="autoriza las llamadas reales al LLM")
    args = parser.parse_args()
    try:
        _, health = call(f"{args.base}/chat/health")
    except OSError as exc:
        print(f"FALLO: el servicio de chat no responde en {args.base} ({exc})")
        return 2
    print("health:", json.dumps(health, ensure_ascii=False))
    if health["status"] != "ok":
        print("FALLO: servicio degradado; corrige antes de gastar cuota (agente, clave del modelo y API de datos).")
        return 2
    if not args.yes:
        print(f"Plan: {len(QUESTIONS)} preguntas reales. Repite con --yes para ejecutarlas.")
        return 0
    failed = 0
    for name, question, needs_sources in QUESTIONS:
        status, body = call(f"{args.base}/chat", {"question": question})
        if status != 200:
            print(f"[{name}] FALLO HTTP {status}: {body.get('detail')}")
            failed += 1
            continue
        d = body["diagnostics"]
        problems = []
        if not body["answer"].strip():
            problems.append("respuesta vacía")
        if body.get("guardrail") == "sin_datos_de_tools":
            problems.append("el agente no consultó herramientas")
        if needs_sources and not body["sources"]:
            problems.append("sin fuentes del RAG (¿documentos indexados?)")
        print(f"[{name}] {'FALLO: ' + ', '.join(problems) if problems else 'OK'} | {d['duration_ms'] / 1000:.1f} s | "
              f"tools={[t['name'] for t in d['tools']]} | fuentes={len(body['sources'])} | guardarraíl={body['guardrail']}")
        print("   ", body["answer"][:300].replace("\n", " "))
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
