"""Fechas y franjas horarias del lenguaje natural (puro, sin E/S; reloj inyectable; Europe/Madrid).

`parse_date_hint(texto, clock)` devuelve la interpretación de «hoy», «mañana», «ayer», «el sábado», «este fin de semana», «sábado 7 de noviembre», «7/11»,
«7-11-2026»… más la franja («por la mañana» 08:00-14:59, «por la tarde» 15:00-23:59, «por la noche» 20:00-23:59), o None si no hay ningún día.
«A mediodía» no se interpreta. Sin año («7/11», «sábado 7 de noviembre») se toma el año cuya fecha queda más cerca de hoy (y, si se dijo el día de la
semana, el que coincide con él).
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
MORNING = (time(8, 0), time(14, 59))
AFTERNOON = (time(15, 0), time(23, 59))
NIGHT = (time(20, 0), time(23, 59))
BANDS = {"manana": MORNING, "tarde": AFTERNOON, "noche": NIGHT}
WEEKDAYS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
WEEKDAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
          "octubre": 10, "noviembre": 11, "diciembre": 12}
_WD = "|".join(WEEKDAYS)
_MONTH = "|".join(MONTHS)

_ISO = re.compile(r"(?<![\d/-])(\d{4})-(\d{1,2})-(\d{1,2})(?![\d/-])")
_DMY = re.compile(r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/-])")
_DM = re.compile(r"(?<![\d/-])(\d{1,2})/(\d{1,2})(?![\d/-])")
_NAMED = re.compile(rf"(?:\b({_WD})\s+)?(?:\bel\s+)?(?<!\d)(\d{{1,2}})\s+(?:de\s+)?({_MONTH})(?:\s+(?:de|del)\s+(\d{{4}}))?\b")
_BAND = re.compile(r"\b(?:(?:por|de|en|a|durante)\s+la|(esta))\s+(manana|tarde|noche)\b")
_WEEKEND = re.compile(r"\b(?:(?:este|el|del)\s+)?(?:(proximo|pasado|ultimo)\s+)?(?:fin\s+de\s+semana|finde)(?:\s+(pasado|proximo|que\s+viene))?\b")
_WEEKDAY = re.compile(rf"\b(?:(?:el|este|del)\s+)?(?:(proximo|pasado|ultimo)\s+)?({_WD})(?:\s+(que\s+viene|pasado|proximo))?\b")
_RELATIVE = re.compile(r"\b(pasado\s+manana|anteayer|antes\s+de\s+ayer|ayer|hoy|manana)\b")
_OFFSETS = {"pasado manana": 2, "anteayer": -2, "antes de ayer": -2, "ayer": -1, "hoy": 0, "manana": 1}


@dataclass(frozen=True)
class DateHint:
    date_from: date
    date_to: date
    time_from: time | None
    time_to: time | None
    phrase: str


def madrid_now() -> datetime:
    return datetime.now(MADRID)


def _fold(text: str) -> str:
    """Minúsculas y sin acentos, carácter a carácter (misma longitud: las posiciones valen para el texto original)."""
    out = []
    for ch in text.lower():
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base[:1] or " ")
    return "".join(out)


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _year_for(today: date, month: int, day: int, weekday: int | None) -> date | None:
    """Sin año: de los años vecinos, el que coincide con el día de la semana dicho (si lo hay) y queda más cerca de hoy."""
    cands = [d for y in (today.year - 1, today.year, today.year + 1) if (d := _valid(y, month, day)) is not None]
    if weekday is not None and any(d.weekday() == weekday for d in cands):
        cands = [d for d in cands if d.weekday() == weekday]
    return min(cands, key=lambda d: (abs((d - today).days), -d.toordinal())) if cands else None


def _weekend(today: date, which: str) -> tuple[date, date]:
    saturday = today - timedelta(days=today.weekday()) + timedelta(days=5)  # semana ISO en curso (lunes a domingo)
    if which == "next" and today.weekday() >= 5:
        saturday += timedelta(days=7)
    elif which == "prev":
        saturday -= timedelta(days=7)
    return saturday, saturday + timedelta(days=1)


def _weekday(today: date, wd: int, mod: str) -> date:
    delta = (wd - today.weekday()) % 7
    if mod == "next":
        return today + timedelta(days=delta or 7)
    if mod == "prev":
        return today - timedelta(days=(today.weekday() - wd) % 7 or 7)
    return today + timedelta(days=delta)  # «el sábado»: el más próximo desde hoy (hoy incluido)


def parse_date_hint(text: str, clock: Callable[[], datetime] = madrid_now) -> DateHint | None:
    """Día(s) y franja que dice `text`, o None si no nombra ningún día. `clock` devuelve un instante con zona horaria (se pasa a Europe/Madrid)."""
    today = clock().astimezone(MADRID).date()
    low = _fold(text)
    spans: list[tuple[int, int]] = []  # trozos ya consumidos (el texto original se conserva para `phrase`)
    days: list[date] = []

    def free(m: re.Match[str]) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in spans)

    def take(m: re.Match[str]) -> None:
        spans.append((m.start(), m.end()))

    for m in _ISO.finditer(low):
        if free(m) and (d := _valid(int(m[1]), int(m[2]), int(m[3]))):
            days.append(d)
            take(m)
    for m in _DMY.finditer(low):
        year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        if free(m) and (d := _valid(year, int(m[2]), int(m[1]))):
            days.append(d)
            take(m)
    for m in _NAMED.finditer(low):
        wd = WEEKDAYS.get(m[1]) if m[1] else None
        d = _valid(int(m[4]), MONTHS[m[3]], int(m[2])) if m[4] else _year_for(today, MONTHS[m[3]], int(m[2]), wd)
        if free(m) and d:
            days.append(d)
            take(m)
    for m in _DM.finditer(low):
        if free(m) and (d := _year_for(today, int(m[2]), int(m[1]), None)):
            days.append(d)
            take(m)
    bands: list[tuple[time, time]] = []
    esta = False
    for m in _BAND.finditer(low):
        if free(m):
            bands.append(BANDS[m[2]])
            esta = esta or bool(m[1])
            take(m)
    for m in _WEEKEND.finditer(low):
        if not free(m):
            continue
        mods = f"{m[1] or ''} {m[2] or ''}"
        which = "prev" if ("pasado" in mods or "ultimo" in mods) else "next" if ("proximo" in mods or "que" in mods) else "this"
        days.extend(_weekend(today, which))
        take(m)
    for m in _RELATIVE.finditer(low):
        if free(m):
            days.append(today + timedelta(days=_OFFSETS[" ".join(m[1].split())]))
            take(m)
    for m in _WEEKDAY.finditer(low):
        if not free(m):
            continue
        mods = f"{m[1] or ''} {m[3] or ''}"
        mod = "prev" if ("pasado" in mods or "ultimo" in mods) else "next" if ("proximo" in mods or "que" in mods) else "near"
        days.append(_weekday(today, WEEKDAYS[m[2]], mod))
        take(m)
    if not days and esta:
        days.append(today)  # «esta tarde», «esta noche»
    if not days:
        return None
    time_from = min((b[0] for b in bands), default=None)
    time_to = max((b[1] for b in bands), default=None)
    phrase = " ".join(text[a:b].strip() for a, b in sorted(spans))
    return DateHint(min(days), max(days), time_from, time_to, phrase)


def describe(hint: DateHint) -> str:
    """Texto breve y verificable de la interpretación (para la nota de la aplicación)."""
    def day(d: date) -> str:
        return f"{WEEKDAY_NAMES[d.weekday()]} {d.isoformat()}"

    span = day(hint.date_from) if hint.date_from == hint.date_to else f"{day(hint.date_from)} a {day(hint.date_to)}"
    band = f", de {hint.time_from:%H:%M} a {hint.time_to:%H:%M}" if hint.time_from and hint.time_to else ""
    return f"«{hint.phrase}» = {span}{band} (Europe/Madrid)"
