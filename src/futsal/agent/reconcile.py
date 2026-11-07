"""Conciliación de la respuesta VISIBLE con los visuales (Futsito): no se repite en texto lo que ya está dibujado.

Contrato de seguridad (prioridad: no perder información):
- Solo se evalúan ELEMENTOS DE LISTA y FILAS DE TABLA del texto, uno a uno e individualmente. Los párrafos (introducción, conclusiones, explicaciones) no se tocan nunca.
- Un elemento se omite únicamente si TODO lo que dice está respaldado por un registro estructurado del visual: las entidades (equipos, jugadores…) coinciden por completo
  con las de un registro y cada dato (marcador, jornada, fecha, hora, posición, puntos, goles, minuto…) está canonicalizado y es igual al del registro. Cualquier palabra,
  número, fecha u hora que sobre —o que no esté respaldado— hace que el elemento se CONSERVE (aunque quede alguna redundancia).
- Solo modifica la respuesta que se muestra. El texto original (`Reconciliation.original`) no se altera: la memoria, el historial del LLM, el foco, el tipo de respuesta
  y las fuentes siguen usando el original.
- Puro y determinista: sin LLM, sin red, sin dependencias. Idempotente. Horas y fechas en Europe/Madrid (reglas UE calculadas aquí: no depende de `tzdata`).
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

MAX_SNIPPET = 80  # lo que se registra de un elemento omitido (no se guardan respuestas completas)
MAX_AUDIT_ITEMS = 12

FILLER = frozenset({
    "vs", "v", "contra", "frente", "a", "al", "las", "los", "la", "el", "lo", "de", "del", "en", "y", "e", "o", "u", "con", "por", "para", "que", "se", "su", "sus",
    "es", "son", "fue", "fueron", "hay", "un", "una", "unos", "unas", "este", "esta", "hora", "horas", "h", "dia", "dias"})
WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
MONTHS = {m: i for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}


# --- Europe/Madrid sin tzdata -------------------------------------------------------------------------------------------------------
def _last_sunday(year: int, month: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def madrid(moment: datetime) -> datetime:
    """Instante con zona -> hora local de Madrid (CET UTC+1; CEST UTC+2 entre el último domingo de marzo y el de octubre, 01:00 UTC)."""
    utc = moment.astimezone(UTC)
    start = datetime.combine(_last_sunday(utc.year, 3), time(1), tzinfo=UTC)
    end = datetime.combine(_last_sunday(utc.year, 10), time(1), tzinfo=UTC)
    return (utc + timedelta(hours=2 if start <= utc < end else 1)).replace(tzinfo=None)


def kickoff(iso: str | None) -> tuple[date, time | None] | None:
    """(fecha local, hora local o None) de un `date` del API. Con hora y zona se convierte a Madrid; con hora sin zona se toma como hora local; solo fecha = sin hora."""
    if not isinstance(iso, str) or not iso.strip():
        return None
    text = iso.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text), None
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    local = madrid(moment) if moment.tzinfo is not None else moment
    return local.date(), local.time().replace(second=0, microsecond=0)


# --- tokens canónicos ----------------------------------------------------------------------------------------------------------------
def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[’‘´`]", "'", t)
    # «'A'» (letra suelta entre comillas) es la etiqueta de un equipo («C.D. DOSA 'A'»): se vuelve la palabra `taga`, que NO es relleno (la preposición «a» sí lo es)
    return re.sub(r"(?<![a-z0-9])'([a-z])'(?![a-z0-9])", r" tag\1 ", t)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def _date_token(day: int, month: int, year: int | None) -> str | None:
    try:
        if year is None:
            date(2000, month, day)  # valida día/mes (2000 es bisiesto)
            return f"qdm{month:02d}{day:02d}"
        return f"qd{date(year, month, day):%Y%m%d}"
    except ValueError:
        return None


_PATTERNS: list[tuple[re.Pattern[str], Any]] = []


def _add(pattern: str, make: Any) -> None:
    _PATTERNS.append((re.compile(pattern), make))


_add(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b", lambda m: _date_token(int(m[1]), int(m[2]), int(m[3]) + (2000 if len(m[3]) == 2 else 0)))
_add(r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")(?:\s+de\s+(\d{4}))?\b", lambda m: _date_token(int(m[1]), MONTHS[m[2]], int(m[3]) if m[3] else None))
_add(r"\b(\d{1,2})/(\d{1,2})\b", lambda m: _date_token(int(m[1]), int(m[2]), None))
_add(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"qt{int(m[1]):02d}{m[2]}" if int(m[1]) < 24 and int(m[2]) < 60 else None)
_add(r"\b(\d{1,2})\s*h\b", lambda m: f"qt{int(m[1]):02d}00" if int(m[1]) < 24 else None)
_add(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b", lambda m: f"qs{int(m[1])}x{int(m[2])}")
_add(r"\bjornada\s+(\d{1,2})\b", lambda m: f"qr{int(m[1])}")
_add(r"\bgrupo\s+(\d{1,2})\b", lambda m: f"qgr{int(m[1])}")
_add(r"\bmin(?:uto)?s?\.?\s*(\d{1,3})\b", lambda m: f"qm{int(m[1])}")
_add(r"\b(\d{1,3})['’′](?![a-z0-9])", lambda m: f"qm{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:puntos?|pts?)\b", lambda m: f"qp{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:partidos?\s+jugados?|jugados?|pj)\b", lambda m: f"qpj{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:ganados?|victorias?)\b", lambda m: f"qwon{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:empatados?|empates?)\b", lambda m: f"qdrawn{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:perdidos?|derrotas?)\b", lambda m: f"qlost{int(m[1])}")
_add(r"\b(\d{1,3})\s*goles?\s+a\s+favor\b", lambda m: f"qgf{int(m[1])}")
_add(r"\b(\d{1,3})\s*goles?\s+en\s+contra\b", lambda m: f"qga{int(m[1])}")
_add(r"\b(\d{1,3})\s*(?:goles?)\b", lambda m: f"qg{int(m[1])}")
_add(r"\b(?:posicion|puesto|lugar)\s+(\d{1,2})\b", lambda m: f"qo{int(m[1])}")
_add(r"\b(\d{1,2})\s*(?:º|ª|o)\b(?![a-z])", lambda m: f"qo{int(m[1])}")
_add(r"\b(" + "|".join(WEEKDAYS) + r")\b", lambda m: f"qwd{WEEKDAYS[m[1]]}")


def extract(text: str) -> tuple[Counter[str], Counter[str]]:
    """(palabras, datos canónicos). Los datos (fechas, horas, marcadores, jornadas, minutos, puntos, goles…) salen del texto antes de partirlo en palabras."""
    _, w, sp = extract_full(text)
    return w, sp


def extract_full(text: str) -> tuple[list[str], Counter[str], Counter[str]]:
    """(palabras EN ORDEN, palabras contadas, datos canónicos)."""
    body = fold(text)
    specials: Counter[str] = Counter()
    for pattern, make in _PATTERNS:
        def sub(m: re.Match[str], make: Any = make) -> str:
            token = make(m)
            if token:
                specials[token] += 1
            return " " if token else m.group(0)
        body = pattern.sub(sub, body)
    seq = re.findall(r"[a-z0-9]+", body)
    return seq, Counter(seq), specials


# --- registros estructurados por tipo de visual -----------------------------------------------------------------------------------
@dataclass
class Record:
    key: str  # solo para el registro de auditoría
    entities: Counter[str]  # TODOS sus tokens deben estar en el elemento (varios nombres = suma)
    attrs: set[str] = field(default_factory=set)  # datos canónicos que respalda el visual
    extra: set[str] = field(default_factory=set)  # palabras neutras propias del registro (estado, cargo…)
    order: tuple[list[str], list[str]] | None = None  # (local, visitante): en el texto deben aparecer completos y en ese orden (quién juega en casa es un dato)


@dataclass
class VisualFacts:
    type: str
    records: list[Record]
    context_words: set[str]  # lo que muestra su cabecera (título/subtítulo)
    context_specials: set[str]


def _tokens(*names: Any) -> Counter[str]:
    out: Counter[str] = Counter()
    for n in names:
        if isinstance(n, str):
            out.update(words(n))
    return out


def _date_attrs(iso: str | None) -> set[str]:
    k = kickoff(iso)
    if k is None:
        return set()
    d, t = k
    attrs = {f"qd{d:%Y%m%d}", f"qdm{d:%m%d}", f"qwd{d.weekday()}"}
    if t is not None:
        attrs.add(f"qt{t:%H%M}")
    return attrs


def _score_attr(score: Any) -> set[str]:
    m = re.fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*", score) if isinstance(score, str) else None
    return {f"qs{int(m[1])}x{int(m[2])}"} if m else set()


def _header(v: dict[str, Any], *extra: Any) -> tuple[set[str], set[str]]:
    w: Counter[str] = Counter()
    s: Counter[str] = Counter()
    for text in (v.get("title"), v.get("subtitle"), *extra):
        if isinstance(text, str):
            cw, cs = extract(text)
            w.update(cw)
            s.update(cs)
    return {x for x in w if not x.isdigit()}, set(s)  # un número suelto del título no respalda ningún número del texto


COLUMN_PREFIX = {"played": "qpj", "won": "qwon", "drawn": "qdrawn", "lost": "qlost", "goals_for": "qgf", "goals_against": "qga", "goal_difference": "qgd"}
HEADER_PREFIX = {"pj": "qpj", "partidos": "qpj", "jugados": "qpj", "g": "qwon", "ganados": "qwon", "e": "qdrawn", "empatados": "qdrawn", "p": "qlost", "perdidos": "qlost",
                 "gf": "qgf", "gc": "qga", "dg": "qgd", "pts": "qp", "puntos": "qp", "pos": "qo", "posicion": "qo", "puesto": "qo", "#": "qo"}
CARD_WORDS = {"yellow": {"amarilla", "amonestado", "amonestacion"}, "second_yellow": {"doble", "amarilla", "segunda", "roja", "expulsado"},
              "red": {"roja", "expulsado", "expulsion", "directa"}, "unknown": {"tarjeta"}}
STATUS_WORDS = {"finished": {"finalizado", "jugado", "resultado"}, "scheduled": {"programado", "pendiente"}}
OFFICIAL_WORDS = {"referee": {"arbitro", "arbitros", "principal"}, "assistant": {"asistente", "asistentes", "auxiliar"}, "table": {"mesa", "cronometrador", "anotador"}}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def facts_of(v: dict[str, Any]) -> VisualFacts | None:
    """Registros de un visual, solo con lo que el visual dibuja. Tipos sin registros fiables (comparación…) devuelven None: nunca se concilian."""
    kind, records = v.get("type"), []
    rows = _dicts(v.get("rows"))
    ctx_extra: list[Any] = []
    if kind == "matches":
        for r in rows:
            if isinstance(r.get("home"), str) and isinstance(r.get("away"), str):
                attrs = _date_attrs(r.get("date")) | _score_attr(r.get("score")) | ({f"qr{r['round']}"} if isinstance(r.get("round"), int) else set())
                records.append(Record(f"{r['home']} vs {r['away']}", _tokens(r["home"], r["away"]), attrs,
                                      {"partido", "partidos", "jornada", "juega", "juegan", "enfrenta", "enfrentan", *STATUS_WORDS.get(str(r.get("status")), set())},
                                      order=(words(r["home"]), words(r["away"]))))
    elif kind == "table":
        for r in rows:
            if isinstance(r.get("team"), str):
                attrs = {f"qp{r['points']}"} if isinstance(r.get("points"), int) else set()
                if isinstance(r.get("position"), int):
                    attrs |= {f"qo{r['position']}"}
                for key, prefix in COLUMN_PREFIX.items():
                    if isinstance(r.get(key), int):
                        attrs |= {f"{prefix}{r[key]}"}
                records.append(Record(r["team"], _tokens(r["team"]), attrs, {"puntos", "pts", "posicion", "puesto", "equipo", "pj", "partidos", "jugados", "ganados", "empatados", "perdidos",
                                                                              "goles", "favor", "contra", "diferencia"}))
    elif kind == "bars":
        for r in rows:
            if isinstance(r.get("label"), str):
                attrs = ({f"qg{r['value']}"} if isinstance(r.get("value"), int) else set()) | ({f"qo{r['rank']}"} if isinstance(r.get("rank"), int) else set())
                records.append(Record(r["label"], _tokens(r["label"]), attrs, {"goles", "gol", "goleador", "goleadores", *words(str(r.get("sublabel") or ""))}))
    elif kind == "form":
        for r in rows:
            if isinstance(r.get("rival"), str):
                attrs = _score_attr(r.get("score")) | ({f"qr{r['round']}"} if isinstance(r.get("round"), int) else set()) | ({f"qp{r['points']}"} if isinstance(r.get("points"), int) else set())
                records.append(Record(r["rival"], _tokens(r["rival"]), attrs, {"contra", "jornada"}))
    elif kind == "lineup":
        ctx_extra.append(v.get("team"))
        for group in ("starters", "substitutes", "unknown"):
            for p in _dicts(v.get(group)):
                if isinstance(p.get("name"), str):
                    shirt = {str(p["shirt"])} if isinstance(p.get("shirt"), int) else set()
                    records.append(Record(p["name"], _tokens(p["name"]), set(), {"p", "c", "portero", "capitan", "titular", "titulares", "suplente", "suplentes", *shirt}))
    elif kind == "officials":
        for p in _dicts(v.get("people")):
            if isinstance(p.get("name"), str):
                records.append(Record(p["name"], _tokens(p["name"]), set(), {*words(str(p.get("role") or "")), *OFFICIAL_WORDS.get(str(p.get("kind")), set())}))
    elif kind == "timeline":
        ctx_extra += [v.get("home"), v.get("away")]
        for e in _dicts(v.get("events")):
            if isinstance(e.get("player"), str):
                records.append(Record(e["player"], _tokens(e["player"]), {f"qm{e['minute']}"} if isinstance(e.get("minute"), int) else set(),
                                      {"gol", "goles", "minuto", "min", *({"propia", "puerta", "pp"} if e.get("own_goal") else set())}))
    elif kind == "cards":
        ctx_extra += [v.get("home"), v.get("away")]
        for e in _dicts(v.get("events")):
            if isinstance(e.get("player"), str):
                attrs = ({f"qm{e['minute']}"} if isinstance(e.get("minute"), int) else set()) | ({f"qr{e['round']}"} if isinstance(e.get("round"), int) else set())
                records.append(Record(e["player"], _tokens(e["player"]), attrs, {*CARD_WORDS.get(str(e.get("kind")), set()), "tarjeta", "tarjetas", "minuto", *words(str(e.get("team") or ""))}))
    else:
        return None
    if not records:
        return None
    ctx_w, ctx_s = _header(v, *ctx_extra)
    return VisualFacts(str(kind), records, ctx_w, ctx_s)


def _find(seq: list[str], part: list[str]) -> list[int]:
    return [i for i in range(len(seq) - len(part) + 1) if seq[i:i + len(part)] == part] if part else []


def _in_order(seq: list[str], home: list[str], away: list[str]) -> bool:
    """¿Aparece el local completo y, DESPUÉS, el visitante completo (sin solaparse)?"""
    starts_h, starts_a = _find(seq, home), _find(seq, away)
    return any(i + len(home) <= j for i in starts_h for j in starts_a)


def _covers(need: Counter[str], have: Counter[str]) -> bool:
    return bool(need) and all(have[t] >= n for t, n in need.items())


def evaluate_item(text: str, ordinal: int | None, facts: list[VisualFacts], cells: Counter[str] | None = None) -> tuple[bool, str, list[str]]:
    """(¿redundante?, motivo, sobrantes). Redundante solo si registros de UN visual lo respaldan por completo.
    `cells`: datos canónicos de las columnas de una tabla markdown con cabecera reconocida (p. ej. «Pts» -> qp19)."""
    seq, base_w, base_s = extract_full(text)
    base_s.update(cells or {})
    best: tuple[str, list[str]] = ("sin registro del visual que lo respalde", [])
    for vf in facts:
        rem_w, rem_s = Counter(base_w), Counter(base_s)
        matched: list[Record] = []
        cands = sorted((r for r in vf.records if _covers(r.entities, rem_w) and (r.order is None or _in_order(seq, *r.order))),
                       key=lambda r: (sum(r.entities.values()), len(r.attrs & set(rem_s))), reverse=True)
        for r in cands:
            if not _covers(r.entities, rem_w):
                continue  # sus tokens ya los consumió otro registro
            rem_w.subtract(r.entities)
            for a in r.attrs & set(rem_s):
                rem_s.pop(a)
            matched.append(r)
        if not matched:
            continue
        allowed_w = FILLER | vf.context_words | {w for r in matched for w in r.extra}
        left_w = sorted(w for w, n in rem_w.items() if n > 0 and w not in allowed_w)
        # la jornada del subtítulo del visual solo respalda una «jornada N» del texto si ningún registro coincidente trae su propia jornada (si la trae, debe ser esa)
        has_round = any(a.startswith("qr") for r in matched for a in r.attrs)
        context_specials = {c for c in vf.context_specials if not (has_round and c.startswith("qr"))}
        left_s = sorted(s for s in rem_s if s not in context_specials)
        # la numeración de la lista es sintaxis, salvo que el visual tenga posiciones/rangos: entonces debe coincidir con el del registro
        if ordinal is not None and any(a.startswith("qo") for r in matched for a in r.attrs) and not any(f"qo{ordinal}" in r.attrs for r in matched):
            left_s.append(f"numeración {ordinal}")
        if not left_w and not left_s:
            return True, f"{vf.type}: lo respaldan {len(matched)} registro(s) del visual ({'; '.join(r.key for r in matched[:3])})", []
        if len(left_w) + len(left_s) < len(best[1]) or not best[1]:
            best = (f"{vf.type}: contiene datos que el visual no tiene", (left_w + left_s)[:6])
    return False, best[0], best[1]


# --- análisis del markdown ---------------------------------------------------------------------------------------------------------------
LIST_ITEM = re.compile(r"^(\s*)([-*+]|(\d{1,3})[.)])\s+(.*)$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


@dataclass
class Reconciliation:
    text: str  # lo que se muestra
    original: str  # intacto: lo que usan la memoria, el historial del LLM, las fuentes, el tipo de respuesta…
    considered: int = 0
    removed: list[dict[str, str]] = field(default_factory=list)
    kept_uncertain: list[dict[str, Any]] = field(default_factory=list)
    pointer_added: bool = False

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def audit(self) -> dict[str, Any]:
        """Qué se omitió y por qué, SIN guardar respuestas completas (solo fragmentos cortos)."""
        return {"considered": self.considered, "removed": len(self.removed), "kept": self.considered - len(self.removed), "pointer_added": self.pointer_added,
                "removed_items": self.removed[:MAX_AUDIT_ITEMS], "kept_uncertain": self.kept_uncertain[:MAX_AUDIT_ITEMS]}


def _snippet(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= MAX_SNIPPET else t[:MAX_SNIPPET] + "…"


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`~]|\[([^\]]*)\]\([^)]*\)", lambda m: m[1] or "", text)


def _table_row(line: str, headers: list[str]) -> tuple[str, int | None, Counter[str]]:
    """(texto, ordinal, datos por columna) de una fila markdown. Una celda numérica bajo una cabecera conocida (Pos, PJ, G, E, P, GF, GC, DG, Pts) se convierte en
    el dato canónico de esa columna; el resto de celdas se compara como texto. Sin cabecera reconocida, los números quedan sueltos y la fila se conserva."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    texts: list[str] = []
    data: Counter[str] = Counter()
    ordinal: int | None = None
    for idx, cell in enumerate(cells):
        prefix = HEADER_PREFIX.get(headers[idx]) if idx < len(headers) else None
        number = re.fullmatch(r"[+\-−]?\d{1,3}", cell)
        if prefix and number:
            value = int(cell.replace("−", "-"))
            if prefix == "qo":
                ordinal = value
            else:
                data[f"{prefix}{value}"] += 1
        else:
            texts.append(cell)
    return " ".join(texts), ordinal, data


def _safe_facts(v: dict[str, Any]) -> VisualFacts | None:
    try:
        return facts_of(v)
    except Exception:  # noqa: BLE001 - un visual raro nunca debe romper la respuesta: simplemente no se concilia contra él
        return None


def reconcile_answer(answer: str, visuals: list[dict[str, Any]]) -> Reconciliation:
    """Quita del texto visible SOLO los elementos de lista y filas de tabla que los visuales ya muestran por completo."""
    result = Reconciliation(answer, answer)
    facts = [f for f in (_safe_facts(v) for v in visuals or [] if isinstance(v, dict)) if f is not None]
    if not answer.strip() or not facts:
        return result
    lines = answer.split("\n")
    drop: set[int] = set()
    in_code = False
    i = 0

    def judge(idx: int, kind: str, text: str, ordinal: int | None, cells: Counter[str] | None = None) -> bool:
        result.considered += 1
        redundant, reason, left = evaluate_item(_strip_md(text), ordinal, facts, cells)
        if redundant:
            result.removed.append({"kind": kind, "reason": reason, "snippet": _snippet(text)})
        elif left:
            result.kept_uncertain.append({"kind": kind, "reason": reason, "left": left, "snippet": _snippet(text)})
        return redundant

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
        elif not in_code:
            if line.strip().startswith("|") and i + 1 < len(lines) and TABLE_SEPARATOR.match(lines[i + 1]):
                j = i + 2
                rows = []
                while j < len(lines) and lines[j].strip().startswith("|"):
                    rows.append(j)
                    j += 1
                headers = [(words(c) or [""])[0] for c in lines[i].strip().strip("|").split("|")]
                gone = [r for r in rows if judge(r, "table_row", *_table_row(lines[r], headers))]
                drop.update(gone)
                if rows and len(gone) == len(rows):
                    drop.update({i, i + 1})  # sin filas, la cabecera y la separación no tienen sentido
                i = j
                continue
            m = LIST_ITEM.match(line)
            if m:
                text, j = m[4], i + 1
                ordinal = int(m[3]) if m[3] else None
                nested = re.match(r"(\d{1,3})[.)]\s+(.*)", text)
                if nested and ordinal is None:  # «- 1. EQUIPO»: la numeración también es sintaxis de lista
                    ordinal, text = int(nested[1]), nested[2]
                while j < len(lines) and lines[j].strip() and not LIST_ITEM.match(lines[j]) and len(lines[j]) - len(lines[j].lstrip()) > len(m[1]):
                    text += " " + lines[j].strip()  # línea de continuación del mismo elemento
                    j += 1
                if judge(i, "list_item", text, ordinal):
                    drop.update(range(i, j))
                i = j
                continue
        i += 1
    if not drop:
        return result
    kept = [ln for k, ln in enumerate(lines) if k not in drop]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if not out:  # no queda texto: una frase que remite al visual (título del primero), nunca una respuesta vacía
        title = next((str(v["title"]) for v in visuals if isinstance(v.get("title"), str)), "")
        out = f"Te lo muestro en la tarjeta «{title}»." if title else "Te lo muestro a continuación."
        result.pointer_added = True
    result.text = out
    return result
