"""Log summaries for text_tool.py log: format, time span, level counts, per-period activity, top message templates.

Works on bytes, streams, and splits big files across processes; the summary is cached by content fingerprint, so
asking again (or with a chart) is instant. Templates mask numbers, ids, hex, quoted strings and timestamps, so
"user 42 timed out after 3000ms" and "user 7 timed out after 12ms" count as one message.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

from _textenc import Encoding

VERSION = "8"
KEEP_PER_LEVEL = 300  # templates kept per level in the cached summary, so a rare FATAL is never crowded out by common ERRORs
SEVERITY = {"FATAL": 3, "CRITICAL": 2, "ERROR": 1}
MAX_TEMPLATES = 150_000
TEMPLATE_LEN = 160

_MONTHS = {m: i for i, m in enumerate((b"Jan", b"Feb", b"Mar", b"Apr", b"May", b"Jun", b"Jul", b"Aug", b"Sep", b"Oct", b"Nov", b"Dec"), 1)}
# ALERT and EMERG count only in brackets or as a "alert:" prefix: as bare words they are message text ("logrotate:
# ALERT exited abnormally")
_LEVEL = re.compile(rb"\b(TRACE|DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|ERR|CRITICAL|CRIT|FATAL|PANIC|SEVERE)\b")
_BRACKET_LEVEL = re.compile(rb"\[(?:\w+:)?(emerg|alert|crit|error|warn|notice|info|debug|trace\d?)\]|\blevel=\"?(\w+)")
_ANDROID_LEVEL = re.compile(rb"^\S+ \S+\s+\d+\s+\d+ ([VDIWEFA]) ")
_LETTER = {b"V": "TRACE", b"D": "DEBUG", b"I": "INFO", b"W": "WARN", b"E": "ERROR", b"F": "FATAL", b"A": "FATAL"}
# syslog-style programs put the level as a lowercase prefix: "sshd[24200]: error: Received disconnect", "fatal: …"
_PREFIX_LEVEL = re.compile(rb"(?:^|[\]:>)]\s|\s-\s)(error|warning|warn|fatal|critical|crit|panic|emerg|alert|notice|debug)(?=:\s)", re.I)
_JSON_LEVEL = re.compile(rb'"(?:level|lvl|severity|log\.level|levelname)"\s*:\s*"(\w+)"', re.I)
_STATUS = re.compile(rb'" (\d{3}) ')
# Masked as "#": UUIDs, 0x… numbers, hex ids (a hex word holding a digit and a letter in any order, or any hex word of
# 8+ characters such as "deadbeef"), numbers with their separators, and quoted text.
_MASK = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|0x[0-9a-fA-F]+|\b(?=[0-9a-fA-F]*[a-fA-F])(?:(?=[0-9a-fA-F]*\d)[0-9a-fA-F]+|[0-9a-fA-F]{8,})\b|\d+(?:[.:,/_-]\d+)*|\"[^\"\n]{0,300}\"|'[^'\n]{0,300}'")
_SPACES = re.compile(rb"\s+")
_NORM = {b"WARNING": "WARN", b"ERR": "ERROR", b"CRIT": "CRITICAL", b"SEVERE": "ERROR", b"PANIC": "FATAL", b"EMERG": "FATAL", b"ALERT": "FATAL", b"FATAL": "FATAL", b"TRACE": "TRACE", b"DEBUG": "DEBUG", b"INFO": "INFO", b"WARN": "WARN", b"ERROR": "ERROR", b"NOTICE": "NOTICE", b"CRITICAL": "CRITICAL"}
ERRORS = {"ERROR", "CRITICAL", "FATAL"}

_TS = {
    "iso": re.compile(rb"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})"),
    "apache": re.compile(rb"\[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2})"),
    "syslog": re.compile(rb"^(\w{3}) ([ \d]\d) (\d{2}):(\d{2})"),
    "slash": re.compile(rb"(\d{4})/(\d{2})/(\d{2})[ T](\d{2}):(\d{2})"),
    "us": re.compile(rb"(\d{1,2})/(\d{1,2})/(\d{4}),? (\d{1,2}):(\d{2})"),
    "glog": re.compile(rb"^[IWEF](\d{2})(\d{2}) (\d{2}):(\d{2})"),
    "tod": re.compile(rb"(\d{2}):(\d{2}):\d{2}"),
    "epoch": re.compile(rb"\b(1\d{9})(?:\d{3})?(?:\.\d+)?\b"),
    "bgl": re.compile(rb"^\S+ (\d{9,10}) \d{4}[.-]\d{2}[.-]\d{2}"),
    "apache-error": re.compile(rb"^\[\w{3} (\w{3}) ([ \d]\d) (\d{2}):(\d{2}):\d{2}(?:\.\d+)? (\d{4})\]"),
    "android": re.compile(rb"^(\d{2})-(\d{2}) (\d{2}):(\d{2})"),
    "compact": re.compile(rb"^(\d{2})(\d{2})(\d{2}) (\d{2})(\d{2})"),
    "compact-dash": re.compile(rb"^(\d{4})(\d{2})(\d{2})-(\d{1,2}):(\d{1,2})"),
    "bracket-md": re.compile(rb"^\[(\d{2})\.(\d{2}) (\d{2}):(\d{2}):\d{2}\]"),
}
_ANCHORED = ("syslog", "glog", "apache-error", "android", "compact", "compact-dash", "bracket-md", "bgl")
YEARLESS = ("syslog", "glog", "android", "bracket-md")  # dates without a year: a jump back of over 30 days is a new year
_ROLLBACK = 30 * 1440
_FORMAT_TS = {
    "Apache/nginx access log": "apache", "syslog": "syslog", "ISO timestamps": "iso", "slash dates": "slash", "US dates": "us",
    "glog": "glog", "time of day": "tod", "epoch seconds": "epoch", "level first": "iso", "JSON lines": "iso",
    "Apache error log": "apache-error", "Android logcat": "android", "compact dates": "compact",
    "compact dash dates": "compact-dash", "bracketed month.day": "bracket-md", "BGL/Thunderbird (epoch and date)": "bgl",
}


_MK_MEMO: dict[tuple[str, bytes], int | None] = {}


def _minute_key(kind: str, line: bytes) -> int | None:
    """Minutes since 2000-01-01 (yearless formats use 2000), or None. Memoized on the matched text: consecutive
    lines share their minute, so the date arithmetic runs once per minute, not once per line."""
    m = _TS[kind].search(line, 0, 120) if kind not in _ANCHORED else _TS[kind].match(line)
    if not m:
        if kind != "iso":
            m2 = _TS["iso"].search(line, 0, 120)
            if m2:
                return _minute_key("iso", line)
        return None
    memo_key = (kind, m.group(0))
    if memo_key in _MK_MEMO:
        return _MK_MEMO[memo_key]
    if len(_MK_MEMO) > 200_000:
        _MK_MEMO.clear()
    _MK_MEMO[memo_key] = v = _minute_value(kind, m)
    return v


def _minute_value(kind: str, m: re.Match[bytes]) -> int | None:
    g = m.groups()
    try:
        if kind in ("iso", "slash", "compact-dash"):
            y, mo, d, h, mi = (int(x) for x in g)
        elif kind == "apache":
            d, mo, y, h, mi = int(g[0]), _MONTHS.get(g[1], 0), int(g[2]), int(g[3]), int(g[4])
        elif kind == "syslog":
            y, mo, d, h, mi = 2000, _MONTHS.get(g[0], 0), int(g[1].strip() or 0), int(g[2]), int(g[3])
        elif kind == "us":
            mo, d, y, h, mi = (int(x) for x in g)
        elif kind in ("glog", "android", "bracket-md"):
            y, mo, d, h, mi = 2000, int(g[0]), int(g[1]), int(g[2]), int(g[3])
        elif kind == "apache-error":
            mo, d, h, mi, y = _MONTHS.get(g[0], 0), int(g[1].strip() or 0), int(g[2]), int(g[3]), int(g[4])
        elif kind == "compact":
            y, mo, d, h, mi = 2000 + int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4])
        elif kind == "tod":
            y, mo, d, h, mi = 2000, 1, 1, int(g[0]), int(g[1])
        else:  # epoch, bgl
            return int(g[0]) // 60 - 15778080  # minutes since 2000-01-01
        return (_dt.date(y, mo, d).toordinal() - 730120) * 1440 + h * 60 + mi
    except (ValueError, TypeError):
        return None


def shift_years(k: int, n: int) -> int:
    """Key `k` moved `n` calendar years later (yearless logs that run past New Year)."""
    if not n:
        return k
    dt = _dt.datetime(2000, 1, 1) + _dt.timedelta(minutes=k)
    try:
        dt = dt.replace(year=dt.year + n)
    except ValueError:  # 29 February
        dt = dt.replace(year=dt.year + n, day=28)
    return int((dt - _dt.datetime(2000, 1, 1)).total_seconds() // 60)


def minute_label(k: int, kind: str, bucket: int = 1) -> str:
    """The label of minute key `k`; with a bucket of a day or more, the date alone."""
    dt = _dt.datetime(2000, 1, 1) + _dt.timedelta(minutes=k)
    daily = bucket >= 1440
    if kind in YEARLESS:
        return dt.strftime("%b %d" if daily else "%b %d %H:%M") + (f" (+{dt.year - 2000}y)" if dt.year > 2000 else "")
    if kind == "tod":
        return dt.strftime("%H:%M") if k < 1440 else f"day {k // 1440 + 1}" + ("" if daily else f" {dt.strftime('%H:%M')}")
    return dt.strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M")


def _level(line: bytes, fmt: str) -> str | None:
    if fmt == "JSON lines":
        m = _JSON_LEVEL.search(line)
        if m:
            v = m.group(1).upper()
            return _NORM.get(v, v.decode("ascii", "replace"))
        return None
    if fmt == "Apache/nginx access log":
        m = _STATUS.search(line)
        if m:
            c = m.group(1)[:1]
            return "ERROR" if c == b"5" else "WARN" if c == b"4" else "INFO"
        return None
    if fmt == "glog" and line[:1] in (b"I", b"W", b"E", b"F"):
        return {b"I": "INFO", b"W": "WARN", b"E": "ERROR", b"F": "FATAL"}[line[:1]]
    if fmt == "Android logcat":
        m = _ANDROID_LEVEL.match(line)
        return _LETTER[m.group(1)] if m else None
    m = _LEVEL.search(line, 0, 200)
    if not m:
        m2 = _BRACKET_LEVEL.search(line, 0, 200)
        if not m2:
            m3 = _PREFIX_LEVEL.search(line, 0, 240)
            if not m3:
                return None
            v = m3.group(1).upper()
            return _NORM.get(v, v.decode("ascii", "replace"))
        v = (m2.group(1) or m2.group(2)).upper().rstrip(b"0123456789")
        return _NORM.get(v, v.decode("ascii", "replace"))
    v = m.group(1)
    return _NORM.get(v, v.decode("ascii"))


def template(line: bytes, fmt: str) -> bytes:
    if fmt == "JSON lines":
        m = re.search(rb'"(?:msg|message|event)"\s*:\s*"((?:[^"\\]|\\.){0,400})"', line)
        if m:
            line = m.group(1)
    elif fmt == "Apache/nginx access log":
        m = re.search(rb'"(\w+) (\S+)[^"]*" (\d{3})', line)
        if m:
            path = m.group(2).split(b"?")[0]
            return m.group(1) + b" " + _MASK.sub(b"#", path)[:120] + b" -> " + m.group(3)
    t = _MASK.sub(b"#", line[:600])
    t = _SPACES.sub(b" ", t).strip()
    return _strip_time(t)[:TEMPLATE_LEN]


_DATEWORD = re.compile(rb"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_DATEWORD_MID = re.compile(rb"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b(?=[ ,]+(?:#|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b))")
_ISO_JOIN = re.compile(rb"#T#Z?|#Z\b")
_LEAD_TIME = re.compile(rb"^(?:[\s\[\]\-:.,/+()]*#)+[\s\]\-:.,/+()]*")


def _strip_time(t: bytes) -> bytes:
    """Drops the masked timestamp (and pid/tid columns) a line starts with, so one message is one template whatever its date."""
    t = _DATEWORD_MID.sub(b"#", t)
    t = _ISO_JOIN.sub(b"# #", _DATEWORD.sub(b"#", t[:48])) + t[48:]
    m = _LEAD_TIME.match(t)
    if m and m.group(0).count(b"#") >= 2 and m.end() < len(t):
        return t[m.end() :]
    return t


def _scan(job: tuple[str, int, int, str, str, int]) -> dict[str, Any]:
    path, start, end, fmt, tskind, sample_every = job
    from _textops import chunks

    levels: dict[str, int] = {}
    per_min: dict[int, int] = {}
    per_min_err: dict[int, int] = {}
    per_min_warn: dict[int, int] = {}
    per_min_fatal: dict[int, int] = {}
    tmpl: dict[bytes, list] = {}
    lines = 0
    no_ts = 0
    first_k = last_k = None
    last_key = None
    yearless = tskind in YEARLESS
    yoff = 0
    for _off, buf in chunks(path, start, end):
        parts = buf.split(b"\n")
        if buf.endswith(b"\n"):
            parts.pop()
        for line in parts:
            lines += 1
            if not line.strip():
                continue
            lv = _level(line, fmt)
            if lv:
                levels[lv] = levels.get(lv, 0) + 1
            else:
                levels["(none)"] = levels.get("(none)", 0) + 1
            k = _minute_key(tskind, line)
            if k is not None and yearless:
                if yoff:
                    k = shift_years(k, yoff)
                if last_key is not None and k < last_key - _ROLLBACK:
                    yoff += 1
                    k = shift_years(k, 1)
            if k is None:
                no_ts += 1
                k = last_key
            else:
                if first_k is None:
                    first_k = k
                last_k = k
                last_key = k
            if k is not None:
                per_min[k] = per_min.get(k, 0) + 1
                if lv in ERRORS:
                    per_min_err[k] = per_min_err.get(k, 0) + 1
                    if lv != "ERROR":
                        per_min_fatal[k] = per_min_fatal.get(k, 0) + 1
                elif lv == "WARN":
                    per_min_warn[k] = per_min_warn.get(k, 0) + 1
            important = lv in ERRORS or lv == "WARN"
            if important or sample_every <= 1 or lines % sample_every == 0:
                t = template(line.rstrip(b"\r"), fmt)
                e = tmpl.get(t)
                if e is None:
                    if len(tmpl) >= MAX_TEMPLATES:
                        for kk in [kk for kk, vv in tmpl.items() if vv[0] == 1 and not vv[3]][: MAX_TEMPLATES // 4]:
                            del tmpl[kk]
                    tmpl[t] = [1, lines, lv or "", important, line[:300].rstrip(b"\r")]
                else:
                    e[0] += 1
    return {
        "start": start, "lines": lines, "levels": levels, "per_min": per_min, "per_min_err": per_min_err, "per_min_warn": per_min_warn, "per_min_fatal": per_min_fatal,
        "templates": [(k, v) for k, v in tmpl.items()], "no_ts": no_ts, "first_k": first_k, "last_k": last_k,
    }


def detect_format(path: str) -> str:
    from _textsniff import log_format

    with open(path, "rb") as f:
        head = f.read(65536)
    text = head.decode("utf-8", "replace")
    lines = [l for l in text.splitlines()[:300] if l.strip()]
    if lines and lines[0].lstrip().startswith("{"):
        ok = 0
        for l in lines[:20]:
            try:
                json.loads(l)
                ok += 1
            except ValueError:
                pass
        if ok >= min(len(lines), 20) * 0.8:
            return "JSON lines"
    fmt = log_format(lines)
    if fmt and fmt[1] >= 0.2:
        return fmt[0]
    return "plain lines"


def summarize_log(path: str, enc: Encoding, top: int = 15, use_cache: bool = True, workers: int | None = None, level: str | None = None) -> dict[str, Any]:
    from _textops import ascii_compatible

    if not ascii_compatible(enc):
        from _common import SkillError

        raise SkillError(f"{enc.name} logs: convert to UTF-8 first (text_tool.py convert FILE OUT --to utf-8)")

    def compute() -> dict[str, Any]:
        return _summarize(path, workers)

    size = os.path.getsize(path)
    if use_cache and size >= 8 * 1024 * 1024:
        from _cache import cached_json

        full = cached_json(path, "fi-logsummary", {}, VERSION, compute)
    else:
        full = compute()
    return _present(full, top, level)


def _summarize(path: str, workers: int | None) -> dict[str, Any]:
    from _common import pool_map, workers_for
    from _textops import PARALLEL_MIN, aligned_ranges

    size = os.path.getsize(path)
    fmt = detect_format(path)
    tskind = _FORMAT_TS.get(fmt, "iso")
    with open(path, "rb") as f:
        head = f.read(1 << 20)
    avg = max(20, len(head) / max(1, head.count(b"\n")))
    est_lines = size / avg
    sample_every = max(1, int(est_lines // 500_000))  # ~500k templated lines rank the top messages; errors and warnings are always exact
    parts = workers_for(8, workers) if size >= PARALLEL_MIN else 1
    outs = pool_map(_scan, [(path, s, e, fmt, tskind, sample_every) for s, e in aligned_ranges(path, parts)], workers=parts)
    outs.sort(key=lambda r: r["start"])
    levels: dict[str, int] = {}
    per_min: dict[int, int] = {}
    per_err: dict[int, int] = {}
    per_warn: dict[int, int] = {}
    per_fatal: dict[int, int] = {}
    tmpl: dict[bytes, list] = {}
    base = 0
    lines = no_ts = 0
    first_k = last_k = None
    for r in outs:
        for k, v in r["levels"].items():
            levels[k] = levels.get(k, 0) + v
        ny = 0  # a yearless log that passed New Year in an earlier part: this part's dates are in a later year
        if tskind in YEARLESS and last_k is not None and r["first_k"] is not None:
            while ny < 50 and shift_years(r["first_k"], ny) < last_k - _ROLLBACK:
                ny += 1
            if ny:
                r["first_k"] = shift_years(r["first_k"], ny)
                r["last_k"] = shift_years(r["last_k"], ny) if r["last_k"] is not None else None
        for src, dst in ((r["per_min"], per_min), (r["per_min_err"], per_err), (r["per_min_warn"], per_warn), (r["per_min_fatal"], per_fatal)):
            for k, v in src.items():
                k = shift_years(k, ny)
                dst[k] = dst.get(k, 0) + v
        for t, (cnt, first, lv, imp, ex) in r["templates"]:
            e = tmpl.get(t)
            if e is None:
                tmpl[t] = [cnt, first + base, lv, imp, ex]
            else:
                e[0] += cnt
        base += r["lines"]
        lines += r["lines"]
        no_ts += r["no_ts"]
        if first_k is None and r["first_k"] is not None:
            first_k = r["first_k"]
        if r["last_k"] is not None:
            last_k = r["last_k"]
    ranked = sorted(tmpl.items(), key=lambda kv: -kv[1][0])
    kept: dict[bytes, list] = dict(ranked[:KEEP_PER_LEVEL])
    per_level: dict[str, int] = {}
    for k, v in ranked:  # the top templates of every level, however rare the level
        n = per_level.get(v[2], 0)
        if n < KEEP_PER_LEVEL:
            per_level[v[2]] = n + 1
            kept.setdefault(k, v)
    keep = list(kept.items())
    minutes = sorted(per_min)
    lo, hi = (minutes[0], minutes[-1]) if minutes else (None, None)
    return {
        "format": fmt, "timestamp_kind": tskind, "lines": lines, "size": size, "levels": levels, "no_timestamp": no_ts,
        # the span is the earliest and latest timestamp: logs merged from several sources are not in time order
        "first": minute_label(lo, tskind) if lo is not None else None, "last": minute_label(hi, tskind) if hi is not None else None,
        "in_order": first_k == lo and last_k == hi, "file_starts": minute_label(first_k, tskind) if first_k is not None else None,
        "file_ends": minute_label(last_k, tskind) if last_k is not None else None,
        "min_key": lo, "max_key": hi,
        "per_min": [[k, per_min[k], per_err.get(k, 0), per_warn.get(k, 0), per_fatal.get(k, 0)] for k in minutes],
        "templates": [[k.decode("utf-8", "replace"), v[0], v[1], v[2], bool(v[3]), v[4].decode("utf-8", "replace")] for k, v in keep],
        "distinct_templates": len(tmpl), "sampled_every": sample_every,
    }


def buckets(full: dict[str, Any], max_buckets: int = 48) -> tuple[int, list[list[int]]]:
    rows = full["per_min"]
    if not rows:
        return 0, []
    span = rows[-1][0] - rows[0][0] + 1
    for size in (1, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080, 43200):
        if span / size <= max_buckets:
            break
    out: dict[int, list[int]] = {}
    for row in rows:
        k, n, e, w = row[:4]
        f = row[4] if len(row) > 4 else 0
        b = k - (k % size)
        cur = out.setdefault(b, [b, 0, 0, 0, 0])
        cur[1] += n
        cur[2] += e
        cur[3] += w
        cur[4] += f
    first = rows[0][0] - rows[0][0] % size
    last = rows[-1][0] - rows[-1][0] % size
    series = [out.get(b, [b, 0, 0, 0, 0]) for b in range(first, last + size, size)]
    return size, series


def _present(full: dict[str, Any], top: int, level: str | None) -> dict[str, Any]:
    tskind = full["timestamp_kind"]
    size, series = buckets(full)
    busiest = sorted([r for r in series if r[1]], key=lambda r: (-r[1], -r[2]))[:8]
    error_peaks = sorted([r for r in series if r[2]], key=lambda r: (-r[2], -r[1]))[:5]
    tm = full["templates"]
    approx = full["sampled_every"] > 1

    def pack(rows: list) -> list[dict[str, Any]]:
        return [{"count": r[1], "approx": approx and not r[4], "level": r[3], "first_line": r[2], "template": r[0], "example": r[5]} for r in rows]

    top_all = sorted(tm, key=lambda r: -r[1])[:top]
    top_err = sorted([r for r in tm if r[3] in ERRORS], key=lambda r: (-SEVERITY.get(r[3], 0), -r[1]))[:top]  # most severe first
    top_warn = sorted([r for r in tm if r[3] == "WARN"], key=lambda r: -r[1])[: max(5, top // 2)]
    out: dict[str, Any] = {
        "format": full["format"], "lines": full["lines"], "size": full["size"], "first": full["first"], "last": full["last"],
        "in_order": full.get("in_order", True), "file_starts": full.get("file_starts"), "file_ends": full.get("file_ends"),
        "levels": dict(sorted(full["levels"].items(), key=lambda kv: -kv[1])), "no_timestamp": full["no_timestamp"],
        "bucket_minutes": size,
        "busiest": [{"period": minute_label(r[0], tskind, size), "lines": r[1], "errors": r[2], "warnings": r[3]} for r in busiest],
        "error_peaks": [{"period": minute_label(r[0], tskind, size), "lines": r[1], "errors": r[2], "warnings": r[3]} for r in error_peaks],
        "series": [{"period": minute_label(r[0], tskind, size), "lines": r[1], "errors": r[2], "warnings": r[3], **({"fatal": r[4]} if r[4] else {})} for r in series],
        "top_errors": pack(top_err), "top_warnings": pack(top_warn), "top_messages": pack(top_all),
        "distinct_templates": full["distinct_templates"], "sampled_every": full["sampled_every"],
    }
    if level:
        lv = _NORM.get(level.upper().encode(), level.upper())
        out["top_level"] = {"level": lv, "templates": pack(sorted([r for r in tm if r[3] == lv], key=lambda r: -r[1])[:top])}
    return out


def bucket_unit(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 1440 or minutes % 1440:
        return f"{minutes // 60} h"
    return "day" if minutes == 1440 else "week" if minutes == 10080 else f"{minutes // 1440} days"


def render_md(d: dict[str, Any], path: str) -> str:
    from _common import human_size, md_escape_cell

    lv = " · ".join(f"{k} {v:,}" for k, v in d["levels"].items())
    out = [f"## {path}: {d['format']}, {d['lines']:,} lines, {human_size(d['size'])}", ""]
    if d["first"]:
        order = "" if d.get("in_order", True) else f"; the lines are not in time order: the file starts at {d['file_starts']} and ends at {d['file_ends']}"
        out.append(f"- **Time span:** {d['first']} → {d['last']}" + (f" ({d['no_timestamp']:,} lines without a timestamp)" if d["no_timestamp"] else "") + order)
    out.append(f"- **Levels:** {lv}")
    out.append(f"- **Distinct message templates:** {d['distinct_templates']:,}" + (f" (non-error lines sampled 1 in {d['sampled_every']}: their counts are estimates, marked ~)" if d["sampled_every"] > 1 else ""))
    if d["busiest"]:
        unit = bucket_unit(d["bucket_minutes"])
        out.append(f"\n### Busiest periods (per {unit}, most lines first)\n")
        out.append("| period | lines | errors | warnings |\n|---|---|---|---|")
        for b in d["busiest"]:
            out.append(f"| {b['period']} | {b['lines']:,} | {b['errors']:,} | {b['warnings']:,} |")
        peaks = d.get("error_peaks", [])
        if peaks and peaks[0]["period"] not in {x["period"] for x in d["busiest"][:3]}:  # errors peak elsewhere
            out.append(f"\n### Periods with the most errors (per {unit})\n")
            out.append("| period | errors | warnings | lines |\n|---|---|---|---|")
            for b in d["error_peaks"]:
                out.append(f"| {b['period']} | {b['errors']:,} | {b['warnings']:,} | {b['lines']:,} |")

    def table(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        out.append(f"\n### {title}\n")
        out.append("| count | level | first line | message (numbers, ids and quoted text masked as #) |\n|---|---|---|---|")
        for r in rows:
            c = f"~{r['count'] * d['sampled_every']:,}" if r["approx"] else f"{r['count']:,}"
            out.append(f"| {c} | {r['level']} | {r['first_line']:,} | {md_escape_cell(r['template'])} |")

    table("Top errors (most severe level first, then by count)", d["top_errors"])
    table("Top warnings", d["top_warnings"])
    if d.get("top_level"):
        table(f"Top {d['top_level']['level']} messages", d["top_level"]["templates"])
    table("Most frequent messages", d["top_messages"])
    if d["top_errors"]:
        ex = d["top_errors"][0]
        out.append(f"\nSee the first occurrence: `python3 scripts/text_tool.py slice {path} --lines {max(1, ex['first_line'] - 5)}-{ex['first_line'] + 20}`")
    return "\n".join(out)


def nice_ticks(peak: float, target: int = 4) -> tuple[float, list[float]]:
    """A round axis maximum and its ticks (steps of 1, 2, 2.5 or 5 times a power of ten) covering `peak`."""
    import math

    peak = max(1.0, float(peak))
    raw = peak / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    if step < 1:
        step = 1.0
    top = math.ceil(peak / step) * step
    return top, [i * step for i in range(int(round(top / step)) + 1)]


def _tick_label(v: float) -> str:
    return f"{int(v):,}" if v == int(v) else f"{v:,.1f}"


def render_chart(d: dict[str, Any], out: Any, name: str = "") -> Any:
    from PIL import Image, ImageDraw

    from _render import _font

    series = d["series"]
    W, H = 1500, 640
    L, R, T, B = 90, 30, 70, 110
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    title = f"{name + ': ' if name else ''}{d['format']}, {d['lines']:,} lines" + (f", {d['first']} to {d['last']}" if d["first"] else "")
    dr.text((L, 18), title[:140], fill="#111111", font=_font(22, title[:140]))
    if not series:
        dr.text((L, H // 2), "no timestamps found: nothing to chart", fill="#aa0000", font=_font(20))
        img.save(out)
        return out
    peak, ticks = nice_ticks(max(r["lines"] for r in series) or 1)
    fatal_seen = False
    n = len(series)
    pw = (W - L - R) / n
    ph = H - T - B
    f13 = _font(13)
    for v in ticks:
        y = T + ph - ph * v / peak
        dr.line([(L, y), (W - R, y)], fill="#e6e6e6")
        lab = _tick_label(v)
        dr.text((L - 8 - dr.textlength(lab, font=f13), y - 8), lab, fill="#555555", font=f13)
    for i, r in enumerate(series):
        x0 = L + i * pw + 1
        x1 = L + (i + 1) * pw - 1
        h_all = ph * r["lines"] / peak
        dr.rectangle([x0, T + ph - h_all, x1, T + ph], fill="#b9c7d8")
        h_w = ph * (r["errors"] + r["warnings"]) / peak
        if r["warnings"]:
            dr.rectangle([x0, T + ph - h_w, x1, T + ph], fill="#f0b429")
        if r["errors"]:
            h_e = max(2.0, ph * r["errors"] / peak)
            dr.rectangle([x0, T + ph - h_e, x1, T + ph], fill="#d64545")
        if r.get("fatal"):  # rare but severe: a marker above the bar, so 3 FATAL lines among millions stay visible
            cx = (x0 + x1) / 2
            ty = T + ph - h_all - 6
            dr.polygon([(cx - 7, ty - 12), (cx + 7, ty - 12), (cx, ty)], fill="#7a0010")
            fatal_seen = True
    step = max(1, n // 8)
    right = -1.0
    for i in range(0, n, step):
        x = L + i * pw
        lab = series[i]["period"]
        tw = dr.textlength(lab, font=f13)
        tx = min(x + 2, W - 6 - tw)  # keep the last label inside the image
        if tx < right + 12:  # would overlap the previous label
            continue
        dr.line([(x, T + ph), (x, T + ph + 6)], fill="#333333")
        dr.text((tx, T + ph + 10), lab, fill="#333333", font=f13)
        right = tx + tw
    unit = bucket_unit(d["bucket_minutes"])
    lx = L
    for color, label in (("#b9c7d8", f"lines per {unit}"), ("#f0b429", "warnings"), ("#d64545", "errors")):
        dr.rectangle([lx, H - 40, lx + 18, H - 24], fill=color)
        dr.text((lx + 24, H - 42), label, fill="#333333", font=_font(15))
        lx += 190
    if fatal_seen:
        dr.polygon([(lx + 2, H - 40), (lx + 16, H - 40), (lx + 9, H - 26)], fill="#7a0010")
        dr.text((lx + 24, H - 42), "FATAL or CRITICAL lines in that period", fill="#333333", font=_font(15))
    img.save(out)
    return out
