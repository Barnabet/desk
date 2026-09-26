"""Excel number formats, date serials and text-to-number parsing (standard library only).

format_value(value, code) renders a cell value the way Excel displays it: sections and conditions, colors, digit
placeholders (0 # ?), thousands separators and scaling, percent, scientific, fractions, literals, text (@), dates and
times including elapsed [h]:mm and fractional seconds, AM/PM, and General.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

# ── date serials (1900 system with Lotus' leap-year bug, or the 1904 system) ──

_BASE_1900 = _dt.datetime(1899, 12, 30)
_BASE_1904 = _dt.datetime(1904, 1, 1)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def date_to_serial(d: _dt.date | _dt.datetime | _dt.time, date1904: bool = False) -> float:
    """A Python date, datetime or time as an Excel serial number."""
    if isinstance(d, _dt.time):
        return (d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6) / 86400.0
    if not isinstance(d, _dt.datetime):
        d = _dt.datetime(d.year, d.month, d.day)
    if d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    if date1904:
        delta = d - _BASE_1904
    else:
        delta = d - _BASE_1900
    serial = delta.days + delta.seconds / 86400.0 + delta.microseconds / 86400e6
    if not date1904 and delta.days <= 60:
        serial -= 1  # dates before 1900-03-01 sit one lower because of the phantom 1900-02-29
    return serial


def serial_parts(serial: float, date1904: bool = False) -> tuple[int, int, int, int, int, int, int, int]:
    """(year, month, day, hour, minute, second, millisecond, weekday 0=Sunday) for a serial; handles 1900-02-29 and 1900-01-00."""
    days = math.floor(serial)
    ms = round((serial - days) * 86_400_000)
    if ms >= 86_400_000:
        days += 1
        ms -= 86_400_000
    h, rem = divmod(ms, 3_600_000)
    mi, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    if date1904:
        d = (_BASE_1904 + _dt.timedelta(days=days)).date()
        wd = (d.weekday() + 1) % 7
        return d.year, d.month, d.day, h, mi, s, msec, wd
    if days == 60:
        return 1900, 2, 29, h, mi, s, msec, 3
    if days == 0:
        return 1900, 1, 0, h, mi, s, msec, 6
    if days < 60:
        d = (_dt.datetime(1899, 12, 31) + _dt.timedelta(days=days)).date()
        wd = (days + 6) % 7  # serial 1 (1900-01-01) is a Sunday in Excel's calendar
    else:
        d = (_BASE_1900 + _dt.timedelta(days=days)).date()
        wd = (d.weekday() + 1) % 7
    return d.year, d.month, d.day, h, mi, s, msec, wd


def serial_to_datetime(serial: float, date1904: bool = False) -> _dt.datetime:
    """A serial as a Python datetime (1900-02-29 becomes 1900-02-28; serial 0 becomes 1899-12-31)."""
    y, mo, d, h, mi, s, ms, _ = serial_parts(serial, date1904)
    if mo == 2 and d == 29 and y == 1900:
        d = 28
    if d == 0:
        return _dt.datetime(1899, 12, 31, h, mi, s, ms * 1000)
    return _dt.datetime(y, mo, d, h, mi, s, ms * 1000)


def serial_to_python(serial: float, kind: str, date1904: bool = False) -> _dt.date | _dt.datetime | _dt.time:
    """kind is 'date', 'time' or 'datetime' (from the number format)."""
    dt = serial_to_datetime(serial, date1904)
    if kind == "date":
        return dt.date()
    if kind == "time" and 0 <= serial < 1:
        return dt.time()
    return dt


def ymd_to_serial(y: int, m: int, d: int, date1904: bool = False) -> float:
    """Excel's DATE(): months and days overflow into the next unit; years 0-1899 add 1900."""
    if 0 <= y < 1900:
        y += 1900
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    if y < 1900 or y > 9999:
        raise ValueError("year out of range")
    base = date_to_serial(_dt.date(y, m, 1), date1904)
    serial = base + d - 1
    if serial < 0:
        raise ValueError("date before the epoch")
    return serial


# ── built-in number formats ─────────────────────────────────────────────

BUILTIN_FORMATS = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 5: '"$"#,##0_);("$"#,##0)', 6: '"$"#,##0_);[Red]("$"#,##0)',
    7: '"$"#,##0.00_);("$"#,##0.00)', 8: '"$"#,##0.00_);[Red]("$"#,##0.00)', 9: "0%", 10: "0.00%", 11: "0.00E+00",
    12: "# ?/?", 13: "# ??/??", 14: "m/d/yyyy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy", 18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yyyy h:mm", 27: "yyyy/m/d", 28: "yyyy/m/d", 29: "yyyy/m/d",
    30: "m/d/yy", 31: "yyyy/m/d", 32: "h:mm:ss", 33: "h:mm:ss", 34: "yyyy/m/d h:mm", 35: "yyyy/m/d h:mm", 36: "yyyy/m/d",
    37: "#,##0_);(#,##0)", 38: "#,##0_);[Red](#,##0)", 39: "#,##0.00_);(#,##0.00)", 40: "#,##0.00_);[Red](#,##0.00)",
    41: '_(* #,##0_);_(* \\(#,##0\\);_(* "-"_);_(@_)', 42: '_("$"* #,##0_);_("$"* \\(#,##0\\);_("$"* "-"_);_(@_)',
    43: '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)', 44: '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)',
    45: "mm:ss", 46: "[h]:mm:ss", 47: "mm:ss.0", 48: "##0.0E+0", 49: "@", 50: "yyyy/m/d", 51: "yyyy/m/d", 52: "yyyy/m/d",
    53: "yyyy/m/d", 54: "yyyy/m/d", 55: "yyyy/m/d", 56: "yyyy/m/d", 57: "yyyy/m/d", 58: "yyyy/m/d",
}

_QUOTED = re.compile(r'"[^"]*"|\\.|\[[^\[\]]*\]|_.|\*.')  # a [section] holds no "[": linear on "[[[…"


@lru_cache(maxsize=4096)
def date_kind(code: str | None) -> str | None:
    """'date', 'time' or 'datetime' when the format shows a date/time, else None."""
    if not code or code == "General":
        return None
    first = _split_sections(code)[0]
    stripped = _QUOTED.sub(lambda m: m.group(0) if re.fullmatch(r"\[(h+|m+|s+)\]", m.group(0), re.I) else "", first)
    low = stripped.lower()
    has_date = bool(re.search(r"[yd]|(?<![hs:])m(?!m*\s*[:s])|e", low)) and not re.fullmatch(r"[#0?,.%e+\-\s]*", low)
    has_date = has_date and bool(re.search(r"[ydm]", low))
    has_time = bool(re.search(r"[hs]|am/pm|a/p|\[[hms]", low))
    if not has_date and not has_time:
        return None
    if has_date and has_time:
        return "datetime"
    return "date" if has_date else "time"


# ── General ─────────────────────────────────────────────────────────────


def _trim_zeros(s: str) -> str:
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _sci(x: float, mant_digits: int) -> str:
    s = f"{x:.{max(0, mant_digits)}E}"
    mant, exp = s.split("E")
    mant = _trim_zeros(mant)
    e = int(exp)
    return f"{mant}E{'+' if e >= 0 else '-'}{abs(e):02d}"


def general(x: float, width: int | None = None) -> str:
    """Excel's General format. width=None gives the 15-significant-digit text used by & and TEXT; 11 is a default column."""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if x == 0:
        return "0"
    if isinstance(x, int) and abs(x) < 10**15 and (width is None or len(str(x)) <= width):
        return str(x)
    ax = abs(x)
    exp = math.floor(math.log10(ax))
    if width is None:
        if exp >= 15 or exp < -9:
            return _sci(x, 14)
        s = f"{round(x, max(0, 14 - exp)):.{max(0, 14 - exp)}f}"
        return _trim_zeros(s)
    neg = 1 if x < 0 else 0
    if exp >= width - neg or ax < 1e-9:
        return _sci(x, max(0, width - neg - 6))
    if exp < -4:
        fixed = _trim_zeros(f"{x:.{width - neg - 2}f}")
        sig = len(_trim_zeros(f"{ax:.10E}".split("E")[0]).replace(".", ""))
        if len(fixed) <= width and len(fixed.replace("-", "").replace(".", "").lstrip("0")) >= min(sig, width - neg - 2 + exp):
            return fixed
        return _sci(x, max(0, width - neg - 6))
    int_digits = max(1, exp + 1)
    decimals = max(0, width - neg - int_digits - 1)
    s = _trim_zeros(f"{_round_half_up(x, decimals):.{decimals}f}")
    if len(s) > width:
        return _sci(x, max(0, width - neg - 6))
    return s


def _round_half_up(x: float, places: int) -> float:
    try:
        d = Decimal(repr(x))
        q = Decimal(1).scaleb(-places)
        return float(d.quantize(q, rounding=ROUND_HALF_UP))
    except Exception:  # noqa: BLE001 — inf/nan or absurd precision
        return round(x, places)


def _dec_round(x: float, places: int) -> Decimal:
    """x rounded half away from zero at `places` decimals, working on its 15-significant-digit decimal form."""
    d = Decimal(f"{x:.15g}")
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


# ── format parsing ──────────────────────────────────────────────────────

_COLORS = {
    "black": "#000000", "blue": "#0000FF", "cyan": "#00FFFF", "green": "#00FF00", "magenta": "#FF00FF", "red": "#FF0000",
    "white": "#FFFFFF", "yellow": "#FFFF00",
}


def _split_sections(code: str) -> list[str]:
    out, cur, i, n = [], [], 0, len(code)
    while i < n:
        ch = code[i]
        if ch == '"':
            j = code.find('"', i + 1)
            j = n - 1 if j < 0 else j
            cur.append(code[i : j + 1])
            i = j + 1
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(code[i : i + 2])
            i += 2
            continue
        if ch == "[":
            j = code.find("]", i)
            j = n - 1 if j < 0 else j
            cur.append(code[i : j + 1])
            i = j + 1
            continue
        if ch == ";":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


class _Section:
    __slots__ = ("tokens", "color", "cond", "kind", "percent", "scale", "grouping", "int_ph", "dec_ph", "exp", "frac", "has_text", "elapsed", "ampm", "raw")

    def __init__(self) -> None:
        self.tokens: list[tuple[str, str]] = []
        self.color: str | None = None
        self.cond: tuple[str, float] | None = None
        self.kind = "number"  # number | date | text | general
        self.percent = 0
        self.scale = 0
        self.grouping = False
        self.exp: str | None = None
        self.frac = False
        self.has_text = False
        self.elapsed = False
        self.ampm = False
        self.raw = ""


_DATE_TOKEN = re.compile(r"(yyyy|yyy|yy|y|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|e|am/pm|a/p|0+|\.0+)", re.I)


@lru_cache(maxsize=2048)
def _parse_section(text: str) -> _Section:
    sec = _Section()
    sec.raw = text
    toks: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j
            toks.append(("lit", text[i + 1 : j]))
            i = j + 1
        elif ch == "\\" and i + 1 < n:
            toks.append(("lit", text[i + 1]))
            i += 2
        elif ch == "_" and i + 1 < n:
            toks.append(("lit", " "))
            i += 2
        elif ch == "*" and i + 1 < n:
            i += 2  # fill character: ignored in plain text output
        elif ch == "[":
            j = text.find("]", i)
            j = n - 1 if j < 0 else j
            inner = text[i + 1 : j]
            low = inner.lower()
            m = re.fullmatch(r"\s*(<=|>=|<>|<|>|=)\s*(-?[\d.]+(?:e[+-]?\d+)?)\s*", low)
            if low in _COLORS:
                sec.color = _COLORS[low]
            elif re.fullmatch(r"color\s*\d+", low):
                sec.color = "#000000"
            elif m:
                sec.cond = (m.group(1), float(m.group(2)))
            elif re.fullmatch(r"h+|m+|s+", low):
                toks.append(("elapsed", low))
                sec.elapsed = True
            elif low.startswith("$"):
                sym = inner[1:].split("-", 1)[0]
                if sym:
                    toks.append(("lit", sym))
            i = j + 1
        elif ch == "@":
            toks.append(("text", "@"))
            sec.has_text = True
            i += 1
        elif text[i:].lower().startswith("general"):
            toks.append(("general", ""))
            i += 7
        elif ch in "0#?":
            toks.append(("ph", ch))
            i += 1
        elif ch == ".":
            toks.append(("dot", "."))
            i += 1
        elif ch == ",":
            toks.append(("comma", ","))
            i += 1
        elif ch == "%":
            toks.append(("pct", "%"))
            sec.percent += 1
            i += 1
        elif ch in "Ee" and i + 1 < n and text[i + 1] in "+-":
            toks.append(("exp", text[i : i + 2]))
            i += 2
        elif ch == "/":
            toks.append(("slash", "/"))
            i += 1
        elif ch.lower() in "ymdhs" or text[i : i + 5].lower() == "am/pm" or text[i : i + 3].lower() == "a/p" or (ch.lower() == "e" and not any(t[0] == "ph" for t in toks)):
            m = _DATE_TOKEN.match(text, i)
            tok = m.group(0) if m else ch
            if tok.lower() in ("am/pm", "a/p"):
                sec.ampm = True
                toks.append(("ampm", tok))
            else:
                toks.append(("date", tok.lower()))
            i += len(tok)
        elif ch.isdigit() and toks and toks[-1][0] in ("slash", "fixden"):
            j = i
            while j < n and text[j].isdigit():
                j += 1
            toks.append(("fixden", text[i:j]))
            i = j
        else:
            toks.append(("lit", ch))
            i += 1
    if any(t[0] == "general" for t in toks):
        sec.kind = "general"
    elif any(t[0] in ("date", "elapsed", "ampm") for t in toks):
        sec.kind = "date"
        # a run of 0s after seconds is fractional seconds
        fixed: list[tuple[str, str]] = []
        for k, (t, v) in enumerate(toks):
            if t == "dot" and k + 1 < len(toks) and toks[k + 1][0] == "ph" and toks[k + 1][1] == "0":
                fixed.append(("subsec_dot", "."))
            elif t == "ph" and v == "0" and fixed and fixed[-1][0] in ("subsec_dot", "subsec"):
                fixed.append(("subsec", "0"))
            elif t == "ph":
                fixed.append(("lit", v))
            else:
                fixed.append((t, v))
        toks = _resolve_minutes(fixed)
    elif not any(t[0] == "ph" for t in toks) and sec.has_text:
        sec.kind = "text"
    else:
        sec.kind = "number"
        _analyse_number(sec, toks)
    sec.tokens = toks
    return sec


def _resolve_minutes(toks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """'m'/'mm' means minutes after an hour or before a second, else months."""
    out = list(toks)
    idx = [k for k, t in enumerate(out) if t[0] in ("date", "elapsed")]
    for pos, k in enumerate(idx):
        t, v = out[k]
        if t != "date" or v not in ("m", "mm"):
            continue
        prev = out[idx[pos - 1]][1] if pos > 0 else ""
        nxt = out[idx[pos + 1]][1] if pos + 1 < len(idx) else ""
        if prev.startswith("h") or nxt.startswith("s") or prev in ("[h]", "hh", "h"):
            out[k] = ("minute", v)
    return out


def _analyse_number(sec: _Section, toks: list[tuple[str, str]]) -> None:
    # thousands scaling: commas right after the last digit placeholder (before the dot or at the end)
    last_ph = max((k for k, t in enumerate(toks) if t[0] == "ph"), default=-1)
    dot = next((k for k, t in enumerate(toks) if t[0] == "dot"), None)
    int_end = dot if dot is not None else len(toks)
    k = (dot - 1) if dot is not None else last_ph + 1
    if dot is None:
        k = last_ph + 1
        while k < len(toks) and toks[k][0] == "comma":
            sec.scale += 1
            k += 1
    else:
        k = dot - 1
        while k >= 0 and toks[k][0] == "comma":
            sec.scale += 1
            k -= 1
    first_ph = next((k for k, t in enumerate(toks) if t[0] == "ph"), None)
    if first_ph is not None:
        for k2 in range(first_ph, min(int_end, len(toks))):
            if toks[k2][0] == "comma" and any(t[0] == "ph" for t in toks[k2 + 1 : int_end]):
                sec.grouping = True
    sec.exp = next((v for t, v in toks if t == "exp"), None)
    sec.frac = any(t == "slash" for t, _ in toks) and sec.exp is None


# ── formatting ──────────────────────────────────────────────────────────


def _cond_ok(cond: tuple[str, float], x: float) -> bool:
    op, v = cond
    return {"<": x < v, ">": x > v, "=": x == v, "<=": x <= v, ">=": x >= v, "<>": x != v}[op]


def format_value(value, code: str | None = "General", date1904: bool = False, width: int | None = 11) -> tuple[str, str | None]:
    """(display text, color or None) for a cell value under an Excel number format."""
    if value is None:
        return "", None
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), None
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        if not code or code == "General":
            code = "h:mm:ss" if isinstance(value, _dt.time) else ("yyyy-mm-dd hh:mm:ss" if isinstance(value, _dt.datetime) and (value.hour or value.minute or value.second) else "yyyy-mm-dd")
        value = date_to_serial(value, date1904)
    code = code or "General"
    if isinstance(value, str):
        sections = _split_sections(code)
        if len(sections) >= 4:
            sec = _parse_section(sections[3])
        elif len(sections) == 1 and "@" in sections[0]:
            sec = _parse_section(sections[0])
        else:
            return value, None
        return _render_text(sec, value), sec.color
    if not isinstance(value, (int, float)):
        return str(value), None
    if math.isnan(value) or math.isinf(value):
        return "#NUM!", None
    if code == "General" or code.lower() == "general":
        return general(value, width), None
    sections = _split_sections(code)
    secs = [_parse_section(s) for s in sections]
    sec, neg_handled = _pick_section(secs, value)
    if sec.kind == "general":
        txt = general(abs(value) if neg_handled else value, width)
        return _wrap_general(sec, txt), sec.color
    if sec.kind == "text":
        return _render_text(sec, general(value, width)), sec.color
    if sec.kind == "date":
        if value < 0 and not sec.elapsed:
            return "#" * 8, None
        return _render_date(sec, abs(value) if neg_handled else value, date1904), sec.color
    x = abs(value) if neg_handled else value
    txt = _render_number(sec, x)
    return txt, sec.color


def _pick_section(secs: list[_Section], x: float) -> tuple[_Section, bool]:
    """The section for x and whether the sign is carried by the section (so the value is shown unsigned)."""
    n = len(secs)
    if any(s.cond for s in secs[:2]):
        if secs[0].cond and _cond_ok(secs[0].cond, x):
            return secs[0], False
        if n > 1 and secs[1].cond and _cond_ok(secs[1].cond, x):
            return secs[1], False
        if n > 2:
            return secs[2], False
        if n == 2:
            return secs[1], (x < 0 and not secs[1].cond)
        return secs[0], False
    if x > 0 or n == 1:
        return secs[0], False
    if x < 0:
        return (secs[1], True) if n >= 2 else (secs[0], False)
    return (secs[2] if n >= 3 else secs[0]), False


def _wrap_general(sec: _Section, txt: str) -> str:
    out = []
    for t, v in sec.tokens:
        out.append(txt if t == "general" else v if t == "lit" else "")
    return "".join(out)


def _render_text(sec: _Section, s: str) -> str:
    return "".join(s if t == "text" else v if t == "lit" else "" for t, v in sec.tokens)


def _render_number(sec: _Section, x: float) -> str:
    toks = sec.tokens
    neg = x < 0
    x = abs(x)
    x *= 100**sec.percent
    x /= 1000**sec.scale
    if sec.exp:
        return ("-" if neg else "") + _render_sci(sec, x)
    if sec.frac:
        return _render_fraction(sec, x, neg)
    dot = next((k for k, t in enumerate(toks) if t[0] == "dot"), None)
    int_toks = toks[:dot] if dot is not None else toks
    dec_toks = toks[dot + 1 :] if dot is not None else []
    dec_ph = [v for t, v in dec_toks if t == "ph"]
    places = len(dec_ph)
    d = _dec_round(x, places)
    s = f"{d:f}"
    ip, _, fp = s.partition(".")
    fp = fp.ljust(places, "0")[:places]
    # decimals: trailing optional placeholders drop zeros
    dec_digits = list(fp)
    for k in range(places - 1, -1, -1):
        if dec_digits[k] == "0" and dec_ph[k] in "#?":
            dec_digits[k] = "" if dec_ph[k] == "#" else " "
        else:
            break
    int_ph = [v for t, v in int_toks if t == "ph"]
    if ip == "0" and int_ph and "0" not in int_ph:
        ip = ""
    if sec.grouping and ip:
        ip = f"{int(ip):,}"
    out_int = _fill_integer(int_toks, ip)
    out_dec = []
    di = 0
    for t, v in dec_toks:
        if t == "ph":
            out_dec.append(dec_digits[di])
            di += 1
        elif t in ("lit", "pct"):
            out_dec.append(v)
        elif t == "text":
            out_dec.append("")
    res = out_int + ("." if dot is not None else "") + "".join(out_dec)
    return ("-" if neg else "") + res


def _fill_integer(int_toks: list[tuple[str, str]], digits: str) -> str:
    """Places integer digits into placeholders from the right; extra digits go before the first placeholder."""
    ph_count = sum(1 for t, _ in int_toks if t == "ph")
    if ph_count == 0:
        return "".join(v if t in ("lit", "pct") else "" for t, v in int_toks)
    chars = list(digits)
    out: list[str] = []
    first_ph = next(k for k, t in enumerate(int_toks) if t[0] == "ph")
    for k in range(len(int_toks) - 1, -1, -1):
        t, v = int_toks[k]
        if t == "ph":
            if chars:
                # digits and grouping commas fill placeholders one digit at a time
                ch = chars.pop()
                if ch == ",":
                    out.append(ch)
                    ch = chars.pop() if chars else ""
                out.append(ch)
            else:
                out.append("0" if v == "0" else " " if v == "?" else "")
            if k == first_ph and chars:
                out.append("".join(chars)[::-1])
                chars = []
        elif t in ("lit", "pct"):
            out.append(v[::-1])
    return "".join(out)[::-1]


def _render_sci(sec: _Section, x: float) -> str:
    toks = sec.tokens
    ek = next(k for k, t in enumerate(toks) if t[0] == "exp")
    mant_toks, exp_toks = toks[:ek], toks[ek + 1 :]
    dot = next((k for k, t in enumerate(mant_toks) if t[0] == "dot"), None)
    int_ph = [v for t, v in (mant_toks[:dot] if dot is not None else mant_toks) if t == "ph"]
    dec_ph = [v for t, v in (mant_toks[dot + 1 :] if dot is not None else []) if t == "ph"]
    n_int = max(1, len(int_ph))
    if x == 0:
        e = 0
        m = 0.0
    else:
        e = math.floor(math.log10(x))
        if len(int_ph) > 1:  # engineering style: exponent a multiple of the integer digit count
            e = e - (e % len(int_ph))
        else:
            e = e - (n_int - 1)
        m = x / (10**e)
        md = _dec_round(m, len(dec_ph))
        if md >= 10 ** max(1, len(int_ph) if len(int_ph) > 1 else 1):
            e += len(int_ph) if len(int_ph) > 1 else 1
            m = x / (10**e)
    mant = f"{_dec_round(m, len(dec_ph)):f}"
    ip, _, fp = mant.partition(".")
    fp = fp.ljust(len(dec_ph), "0")
    fp_list = list(fp)
    for k in range(len(dec_ph) - 1, -1, -1):
        if fp_list[k] == "0" and dec_ph[k] == "#":
            fp_list[k] = ""
        else:
            break
    if ip == "0" and int_ph and "0" not in int_ph:
        ip = ""
    int_part = _fill_integer(mant_toks[:dot] if dot is not None else mant_toks, ip)
    exp_digits = sum(1 for t, _ in exp_toks if t == "ph") or 1
    sign = "-" if e < 0 else ("+" if sec.exp.endswith("+") else "")
    exp_str = str(abs(e)).rjust(exp_digits, "0")
    tail = "".join(v for t, v in exp_toks if t == "lit")
    return f"{int_part}{'.' if dot is not None else ''}{''.join(fp_list)}{sec.exp[0]}{sign}{exp_str}{tail}"


def _render_fraction(sec: _Section, x: float, neg: bool) -> str:
    toks = sec.tokens
    sk = next(k for k, t in enumerate(toks) if t[0] == "slash")
    before, after = toks[:sk], toks[sk + 1 :]
    fixden = next((int(v) for t, v in after if t == "fixden"), None)
    den_ph = sum(1 for t, _ in after if t == "ph")
    # split `before` into whole-number placeholders and numerator placeholders (separated by a literal)
    ph_idx = [k for k, t in enumerate(before) if t[0] == "ph"]
    groups: list[list[int]] = []
    for k in ph_idx:
        if groups and k == groups[-1][-1] + 1:
            groups[-1].append(k)
        else:
            groups.append([k])
    has_whole = len(groups) >= 2
    whole = int(x) if has_whole else 0
    frac = x - whole
    if fixden:
        den = fixden
        num = round(frac * den)
    else:
        max_den = 10**max(1, den_ph) - 1
        num, den = _best_fraction(frac, max_den)
    if num == den and has_whole:
        whole += 1
        num = 0
    num_width = len(groups[-1]) if groups else 1
    den_width = max(den_ph, len(str(fixden)) if fixden else 0)
    sign = "-" if neg and (whole or num) else ""
    if has_whole:
        whole_txt = str(whole) if whole else ""
        if num == 0:
            body = (whole_txt or "0") + " " * (num_width + den_width + 2)
            return sign + body.rstrip() if not whole_txt else sign + whole_txt
        lits_before = "".join(v for t, v in before[: groups[0][0]] if t == "lit")
        sep = "".join(v for t, v in before[groups[0][-1] + 1 : groups[-1][0]] if t == "lit") or " "
        return f"{sign}{lits_before}{whole_txt}{sep}{str(num).rjust(num_width)}/{str(den).ljust(den_width)}".rstrip()
    return f"{sign}{str(num + whole * den).rjust(num_width)}/{str(den).ljust(den_width)}".rstrip()


def _best_fraction(x: float, max_den: int) -> tuple[int, int]:
    from fractions import Fraction

    f = Fraction(x).limit_denominator(max_den)
    return f.numerator, f.denominator


def _render_date(sec: _Section, x: float, date1904: bool) -> str:
    toks = sec.tokens
    subsec_digits = sum(1 for t, _ in toks if t == "subsec")
    serial = x
    if subsec_digits == 0:
        # without fractional seconds Excel rounds to the nearest second
        serial = round(x * 86400) / 86400
    y, mo, d, h, mi, s, ms, wd = serial_parts(serial, date1904)
    total_seconds_float = x * 86400
    out: list[str] = []
    hour12 = sec.ampm
    for k, (t, v) in enumerate(toks):
        if t == "date":
            if v in ("yyyy", "yyy", "e"):
                out.append(f"{y:04d}")
            elif v in ("yy", "y"):
                out.append(f"{y % 100:02d}")
            elif v == "mmmmm":
                out.append(MONTHS[mo - 1][0])
            elif v == "mmmm":
                out.append(MONTHS[mo - 1])
            elif v == "mmm":
                out.append(MONTHS[mo - 1][:3])
            elif v == "mm":
                out.append(f"{mo:02d}")
            elif v == "m":
                out.append(str(mo))
            elif v == "dddd":
                out.append(DAYS[(wd - 1) % 7])
            elif v == "ddd":
                out.append(DAYS[(wd - 1) % 7][:3])
            elif v == "dd":
                out.append(f"{d:02d}")
            elif v == "d":
                out.append(str(d))
            elif v in ("hh", "h"):
                hh = h
                if hour12:
                    hh = h % 12 or 12
                out.append(f"{hh:02d}" if v == "hh" else str(hh))
            elif v in ("ss", "s"):
                out.append(f"{s:02d}" if v == "ss" else str(s))
            else:
                out.append(v)
        elif t == "minute":
            out.append(f"{mi:02d}" if v == "mm" else str(mi))
        elif t == "elapsed":
            secs = round(total_seconds_float) if subsec_digits == 0 else total_seconds_float
            if v.startswith("h"):
                val = int(secs // 3600)
            elif v.startswith("m"):
                val = int(secs // 60)
            else:
                val = int(secs)
            out.append(str(val).rjust(len(v), "0"))
        elif t == "ampm":
            am = h < 12
            if v.lower() == "a/p":
                ch = "A" if am else "P"
                out.append(ch if v[0].isupper() else ch.lower())
            else:
                out.append("AM" if am else "PM")
        elif t == "subsec_dot":
            out.append(".")
        elif t == "subsec":
            pass
        elif t == "lit":
            out.append(v)
        elif t in ("dot", "comma", "slash"):
            out.append(v)
    txt = "".join(out)
    if subsec_digits:
        frac = f"{ms / 1000:.{subsec_digits}f}"[2:]
        txt = txt.replace("." , "." + frac, 1) if "." in txt else txt
    return txt


# ── parsing text as numbers and dates (VALUE, DATEVALUE, coercion) ──────

# Its adjacent \s* runs backtrack cubically on long whitespace ("(" + 500 spaces took minutes): parse_number
# collapses every run to one space first, which cannot change what matches.
_NUM_RE = re.compile(r"^\(?([+-]?)\s*([$€£¥]?)\s*([+-]?)(\d{1,3}(?:,\d{3})+|\d*)(\.\d*)?(?:[eE]([+-]?\d+))?\s*(%?)\s*\)?$")
_WS_RUN = re.compile(r"\s+")
_MONTH_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}
for _i, _m in enumerate(MONTHS):
    _MONTH_ABBR[_m.lower()] = _i + 1
_MONTH_ABBR["sept"] = 9


def parse_number(s: str) -> float | None:
    """Excel's text→number coercion: '1,234.5', '$12', '(5)', '12%', '1e3'. None when it is not a number."""
    t = s.strip()
    if not t:
        return None
    m = _NUM_RE.match(_WS_RUN.sub(" ", t))
    if not m or not (m.group(4) or m.group(5)) or m.group(5) == "." and not m.group(4):
        return None
    paren = t.startswith("(") and t.endswith(")")
    if t.startswith("(") != t.endswith(")"):
        return None
    if m.group(1) and m.group(3):
        return None
    try:
        v = float((m.group(4) or "0").replace(",", "") + (m.group(5) or "") + (("e" + m.group(6)) if m.group(6) else ""))
    except ValueError:
        return None
    if m.group(7):
        v /= 100
    if m.group(1) == "-" or m.group(3) == "-" or paren:
        v = -v
    return v


def parse_time(s: str) -> float | None:
    s = s.strip()  # not \s* at both ends: two adjacent optional whitespace runs backtrack quadratically
    m = re.fullmatch(r"(\d{1,4}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?\s*([ap]\.?m?\.?)?", s, re.I)
    if not m:
        m2 = re.fullmatch(r"(\d{1,2})\s*([ap]\.?m?\.?)", s, re.I)
        if not m2:
            return None
        h, mi, sec, ap = int(m2.group(1)), 0, 0.0, m2.group(2)
    else:
        h, mi, sec, ap = int(m.group(1)), int(m.group(2)), float(m.group(3) or 0), m.group(4)
    if ap:
        if h > 12 or h == 0:
            return None
        pm = ap.lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
    if mi >= 60 or sec >= 60:
        return None
    return (h * 3600 + mi * 60 + sec) / 86400.0


def parse_date(s: str, date1904: bool = False) -> float | None:
    """A date and/or time in common formats as a serial: 2024-01-31, 1/31/2024, 31-Jan-2024, Jan 31 2024, 2024-01-31 13:45."""
    t = s.strip()
    if not t:
        return None
    t = t.replace("T", " ") if re.match(r"\d{4}-\d{2}-\d{2}T", t) else t
    time_part = 0.0
    # The time after the first space run that reaches it. Not ^(.*?)[ ]+…$: that lazy prefix against the space run
    # backtracks quadratically on long runs of spaces; a run's first space finds the same split.
    m = re.search(r"(?<! )[ ]+(\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s*[ap]\.?m\.?)?)$", t, re.I)
    date_part = t[: m.start()] if m else ""
    if m and "\n" not in date_part and re.search(r"\d", date_part):
        tp = parse_time(m.group(1))
        if tp is None:
            return None
        time_part = tp
        t = date_part.strip()
    elif re.fullmatch(r"\d{1,4}:\d{1,2}(:\d{1,2}(\.\d+)?)?(\s*[ap]\.?m?\.?)?|\d{1,2}\s*[ap]m", t, re.I):
        return parse_time(t)
    y = mo = d = None
    today = _dt.date.today()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y is None:
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", t)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else today.year
            if mo > 12 and d <= 12:
                mo, d = d, mo
    if y is None:
        m = re.fullmatch(r"(\d{1,2})[-\s./]+([A-Za-z]+)\.?(?:[-\s./,]+(\d{2,4}))?", t)
        if m and m.group(2).lower() in _MONTH_ABBR:
            d, mo = int(m.group(1)), _MONTH_ABBR[m.group(2).lower()]
            y = int(m.group(3)) if m.group(3) else today.year
    if y is None:
        m = re.fullmatch(r"([A-Za-z]+)\.?[-\s./]+(\d{1,2})(?:(?:st|nd|rd|th)?[,\s./-]+(\d{2,4}))?", t)
        if m and m.group(1).lower() in _MONTH_ABBR:
            mo, d = _MONTH_ABBR[m.group(1).lower()], int(m.group(2))
            y = int(m.group(3)) if m.group(3) else today.year
    if y is None:
        m = re.fullmatch(r"([A-Za-z]+)[-\s./,]+(\d{4})", t)
        if m and m.group(1).lower() in _MONTH_ABBR:
            mo, d, y = _MONTH_ABBR[m.group(1).lower()], 1, int(m.group(2))
    if y is None:
        return None
    if y < 100:
        y += 2000 if y < 30 else 1900
    if not (1 <= mo <= 12) or not (1 <= d <= 31):
        return None
    try:
        _dt.date(y, mo, d)
    except ValueError:
        if not (y == 1900 and mo == 2 and d == 29):
            return None
    if y == 1900 and mo == 2 and d == 29 and not date1904:
        return 60.0 + time_part
    if y < 1900 or y > 9999:
        return None
    return date_to_serial(_dt.date(y, mo, d), date1904) + time_part
