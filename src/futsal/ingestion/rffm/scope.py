"""Validación de ámbito de las páginas de la RFFM (puro, sin red).

La web responde HTTP 200 con OTRO ámbito (temporada actual y Fútbol-11) si faltan parámetros
(sondeo A1-3). Toda página con `pageProps` debe pasar por `validate_scope` antes de usarse.

Fuentes de cada dato en `pageProps` (se comparan todas las presentes):
- temporada: `season.cod_temporada`; en el calendario, `calendar.temporada` («2026-2027»); `query.temporada`;
- tipo de juego: `idGameType`; `query.tipojuego`;
- competición y grupo: `results.codigo_competicion` / `results.codigo_grupo`; `query.competicion` / `query.grupo`.

`query` es el eco de lo pedido, no prueba de lo servido: por sí solo no demuestra nada, pero sí delata un eco distinto.
El calendario NO trae códigos de competición ni de grupo; solo nombres (`calendar.competicion`, `calendar.grupo`),
que se comparan si el llamador los indica (`competition_name`, `group_name`).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from futsal.ingestion.rffm.catalog import normalize_name

_SEASON_NAME = re.compile(r"^\s*(\d{4})\s*-\s*(\d{4})\s*$")


@dataclass(frozen=True)
class ScopeCheck:
    ok: bool
    mismatches: list[str] = field(default_factory=list)


def _s(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _sub(props: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = props.get(key)
    return value if isinstance(value, Mapping) else {}


def season_code_from_name(name: str, seasons: Any = None) -> str | None:
    """Código de temporada a partir de su nombre («2026-2027» -> «22»).

    Usa `pageProps.seasons` si se facilita; si no, la correspondencia lineal observada en A1
    (`cod_temporada` 3 = 2007-2008 ... 22 = 2026-2027), es decir, año inicial - 2004.
    """
    if isinstance(seasons, list):
        for s in seasons:
            if isinstance(s, dict) and _s(s.get("nombre")) == name.strip() and _s(s.get("cod_temporada")):
                return _s(s.get("cod_temporada"))
    m = _SEASON_NAME.match(name)
    if m and int(m.group(2)) == int(m.group(1)) + 1:
        return str(int(m.group(1)) - 2004)
    return None


def validate_scope(page_props: Mapping[str, Any], requested: Mapping[str, Any]) -> ScopeCheck:
    """Compara el ámbito servido con el pedido: `season`, `game_type` y, si se piden, `competition`, `group`.

    Opcionales `competition_name` y `group_name` se comparan con el calendario (que no trae códigos).
    Un dato pedido que la página no permite verificar cuenta como discrepancia.
    """
    query, season = _sub(page_props, "query"), _sub(page_props, "season")
    results, calendar = _sub(page_props, "results"), _sub(page_props, "calendar")
    cal_name = _s(calendar.get("temporada"))
    cal_season = season_code_from_name(cal_name, page_props.get("seasons")) if cal_name else None
    if cal_name and cal_season is None:
        cal_season = f"?{cal_name}"  # nombre no interpretable: nunca coincide con lo pedido

    checks: list[tuple[str, str, list[tuple[str, str | None]]]] = [
        ("season", "temporada", [("season.cod_temporada", _s(season.get("cod_temporada"))),
                                 ("calendar.temporada", cal_season),
                                 ("query.temporada", _s(query.get("temporada")))]),
        ("game_type", "tipo de juego", [("idGameType", _s(page_props.get("idGameType"))),
                                        ("query.tipojuego", _s(query.get("tipojuego")))]),
        ("competition", "competición", [("results.codigo_competicion", _s(results.get("codigo_competicion"))),
                                        ("query.competicion", _s(query.get("competicion")))]),
        ("group", "grupo", [("results.codigo_grupo", _s(results.get("codigo_grupo"))),
                            ("query.grupo", _s(query.get("grupo")))]),
    ]
    mismatches: list[str] = []
    for key, label, sources in checks:
        want = _s(requested.get(key))
        if want is None:
            if key in ("season", "game_type"):
                mismatches.append(f"{label}: no se indicó en lo pedido")
            continue
        seen = [(src, val) for src, val in sources if val is not None]
        if not seen:
            mismatches.append(f"{label}: la página no permite verificar {want!r}")
        mismatches += [f"{label}: {src}={val!r} != {want!r}" for src, val in seen if val != want]
    for key, label, got in (("competition_name", "nombre de competición", calendar.get("competicion")),
                            ("group_name", "nombre de grupo", calendar.get("grupo"))):
        want = _s(requested.get(key))
        if want is not None and normalize_name(_s(got) or "") != normalize_name(want):
            mismatches.append(f"{label}: {_s(got)!r} != {want!r}")
    return ScopeCheck(ok=not mismatches, mismatches=mismatches)
