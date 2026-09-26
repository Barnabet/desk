"""iCalendar core for the email-calendar skill: parsing (.ics, .ical, .ifb, invites inside emails) into plain,
cacheable event dicts; time zones (IANA, Windows names, custom VTIMEZONE); recurrence expansion with EXDATE,
RDATE and RECURRENCE-ID overrides; busy/free computation; and building calendars.

icalendar and python-dateutil are imported lazily; tzdata provides zoneinfo data on Windows.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable

from _common import SkillError, UsageError

ICS_EXTS = {".ics", ".ical", ".icalendar", ".ifb", ".vcs", ".ifbf"}
PARSE_VERSION = "7"

# ── time zones ──────────────────────────────────────────────────────────

_ZONE_CACHE: dict[str, tzinfo] = {}
_WINDOWS: dict[str, str] | None = None
WARNINGS: list[str] = []


def windows_zones() -> dict[str, str]:
    """icalendar's CLDR table of Windows zone names → IANA, loaded without importing all of icalendar (fast)."""
    global _WINDOWS
    if _WINDOWS is None:
        _WINDOWS = {}
        try:
            import importlib.util

            spec = importlib.util.find_spec("icalendar")
            if spec and spec.origin:
                path = Path(spec.origin).parent / "timezone" / "windows_to_olson.py"
                mspec = importlib.util.spec_from_file_location("_desk_windows_to_olson", path)
                if mspec and mspec.loader:
                    mod = importlib.util.module_from_spec(mspec)
                    mspec.loader.exec_module(mod)
                    _WINDOWS = dict(getattr(mod, "WINDOWS_TO_OLSON", {}))
        except Exception:  # noqa: BLE001
            _WINDOWS = {}
    return _WINDOWS


def local_zone() -> tzinfo:
    """The machine's zone: TZ, /etc/localtime, the Windows zone name, else a fixed offset."""
    from zoneinfo import ZoneInfo

    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        try:
            return ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            pass
    try:
        link = os.path.realpath("/etc/localtime")  # portable-ok: absent on Windows, handled below
        if "zoneinfo/" in link:
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except Exception:  # noqa: BLE001
        pass
    try:
        import time as _time

        name = _time.tzname[0]
        if name in windows_zones():
            return ZoneInfo(windows_zones()[name])
    except Exception:  # noqa: BLE001
        pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def get_zone(name: str | None) -> tzinfo:
    """A tzinfo for an IANA name, a Windows zone name, 'UTC', 'local' or a fixed offset like +02:00."""
    if not name or name.lower() == "local":
        return local_zone()
    if name in _ZONE_CACHE:
        return _ZONE_CACHE[name]
    from zoneinfo import ZoneInfo

    z: tzinfo | None = None
    if name.upper() in ("UTC", "Z", "GMT", "ETC/UTC"):
        z = ZoneInfo("UTC")
    else:
        try:
            z = ZoneInfo(name)
        except Exception:  # noqa: BLE001
            try:
                if name in windows_zones():
                    z = ZoneInfo(windows_zones()[name])
            except Exception:  # noqa: BLE001
                z = None
        if z is None:
            m = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?", name.strip(), re.I)
            if m:
                delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
                z = timezone(delta if m.group(1) == "+" else -delta)
    if z is None:
        raise UsageError(f"unknown time zone '{name}' (use an IANA name like Europe/Paris, or +02:00)")
    _ZONE_CACHE[name] = z
    return z


def zone_name(tz: tzinfo | None) -> str:
    if tz is None:
        return "floating"
    key = getattr(tz, "key", None)
    if key:
        return key
    return str(tz)


def _iana_for(tzid: str) -> str | None:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(tzid)
        return tzid
    except Exception:  # noqa: BLE001
        pass
    return windows_zones().get(tzid)


def resolve_tz(tzid: str | None, vtimezones: dict[str, str]) -> tzinfo | None:
    """tzinfo for a TZID: IANA or Windows names first, then the calendar's own VTIMEZONE definition."""
    if not tzid:
        return None
    key = f"tzid:{tzid}"
    if key in _ZONE_CACHE:
        return _ZONE_CACHE[key]
    z: tzinfo | None = None
    iana = _iana_for(tzid)
    if iana:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(iana)
    elif tzid in vtimezones:
        try:
            from icalendar import Timezone

            z = Timezone.from_ical(vtimezones[tzid]).to_tz(lookup_tzid=False)
        except Exception:  # noqa: BLE001
            z = None
    if z is None and tzid.upper() in ("UTC", "Z", "GMT"):
        z = timezone.utc
    if z is not None:
        _ZONE_CACHE[key] = z
    return z


# ── parsing times and durations from the command line ───────────────────

_DUR = re.compile(r"(?i)^\s*(?:(\d+(?:\.\d+)?)\s*w)?\s*(?:(\d+(?:\.\d+)?)\s*d)?\s*(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?\s*$")


def parse_duration(s: str | None) -> timedelta | None:
    """'90m', '1h30m', '2d', '1w', 'PT1H30M', '-15m' → timedelta."""
    if s is None or str(s).strip() == "":
        return None
    t = str(s).strip()
    neg = t.startswith("-")
    t = t.lstrip("+-")
    if t.upper().startswith("P"):
        from icalendar.prop import vDuration

        try:
            d = vDuration.from_ical(t.upper())
        except Exception as e:  # noqa: BLE001
            raise UsageError(f"bad duration '{s}'") from e
        return -d if neg else d
    m = _DUR.match(re.sub(r"\s+", " ", t))  # one space per gap: _DUR's adjacent \s* runs backtrack on long ones
    if not m or not any(m.groups()):
        raise UsageError(f"bad duration '{s}' (like 30m, 1h30m, 2d, PT45M)")
    w, d, h, mi, se = (float(x) if x else 0.0 for x in m.groups())
    td = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=se)
    return -td if neg else td


def parse_when(s: str, tz: tzinfo, end_of_day: bool = False) -> datetime:
    """An aware datetime from 'now', 'today', 'tomorrow', '+3d', '2026-10-01', '2026-10-01 14:30', ISO with offset."""
    t = s.strip().lower()
    now = datetime.now(tz)
    today = datetime.combine(now.date(), time(0), tzinfo=tz)
    if t == "now":
        return now
    if t in ("today", "tomorrow", "yesterday"):
        d = today + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[t])
        return d + timedelta(days=1) if end_of_day else d
    if re.fullmatch(r"[+-]\d+[dwhm]", t):
        n = int(t[:-1])
        unit = {"d": "days", "w": "weeks", "h": "hours", "m": "minutes"}[t[-1]]
        base = now if t[-1] in "hm" else today
        return base + timedelta(**{unit: n})
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", t):
            d = datetime.combine(date.fromisoformat(t), time(0), tzinfo=tz)
            return d + timedelta(days=1) if end_of_day else d
        if re.fullmatch(r"\d{8}", t):
            d = datetime.combine(datetime.strptime(t, "%Y%m%d").date(), time(0), tzinfo=tz)
            return d + timedelta(days=1) if end_of_day else d
        v = datetime.fromisoformat(s.strip().replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in s else s.strip().replace("Z", "+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=tz)
    except ValueError as e:
        raise UsageError(f"bad date or time '{s}' (use 2026-10-01, 2026-10-01T14:30, today, +7d)") from e


# ── parsing calendars into plain dicts ──────────────────────────────────


def read_calendar_bytes(data: bytes) -> list[Any]:
    """icalendar Calendar objects from raw bytes (several VCALENDARs allowed; BOM, blank lines and bad folding tolerated)."""
    from icalendar import Calendar

    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        data = data.decode("utf-16").encode("utf-8")
    text = data.decode("utf-8", "replace")
    text = re.sub(r"\r?\n[ \t]*\r?\n", "\n", text) if "\n\n" in text.replace("\r", "") else text
    upper = text.upper()
    if "BEGIN:VCALENDAR" not in upper:
        if re.search(r"(?m)^BEGIN:(VEVENT|VTODO|VJOURNAL|VFREEBUSY|VTIMEZONE)\s*$", upper):
            text = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//unknown//EN\r\n" + text.strip() + "\r\nEND:VCALENDAR\r\n"
            WARNINGS.append("the file holds components without a VCALENDAR wrapper; read them anyway")
        else:
            raise SkillError("not an iCalendar file (no BEGIN:VCALENDAR or VEVENT)")
    elif "END:VCALENDAR" not in upper:
        text = text.rstrip() + "\r\nEND:VCALENDAR\r\n"
    try:
        cals = list(Calendar.from_ical(text, multiple=True))
        for c in cals:
            list(c.walk())  # icalendar parses lazily: surface errors here
        return cals
    except Exception as e:  # noqa: BLE001 — salvage what can be read, component by component
        cals, skipped = _salvage(text)
        if not cals:
            raise SkillError(f"the calendar could not be parsed: {e}") from e
        WARNINGS.append(f"the calendar is damaged ({e}); {skipped} component(s) could not be read and were skipped")
        return cals


def _salvage(text: str) -> tuple[list[Any], int]:
    """Parses a damaged calendar one top-level component at a time, dropping malformed lines and components."""
    from icalendar import Calendar, Component

    lines = re.sub(r"\r?\n[ \t]", "", text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    good = [ln for ln in lines if re.match(r"^[A-Za-z0-9-]+(;[^:]*)?:", ln)]
    cal = Calendar()
    depth = 0
    block: list[str] = []
    skipped = 0
    for ln in good:
        up = ln.upper()
        if up.startswith("BEGIN:"):
            depth += 1
            if depth == 1:
                continue
        if up.startswith("END:"):
            depth -= 1
            if depth == 0:
                continue
        if depth == 1 and not block and not up.startswith("BEGIN:"):
            try:
                name, _, value = ln.partition(":")
                cal.add(name.split(";")[0], value)
            except Exception:  # noqa: BLE001
                pass
            continue
        block.append(ln)
        if depth == 1 and up.startswith("END:"):
            try:
                comp = Component.from_ical("\r\n".join(block) + "\r\n")
                list(comp.walk())
                cal.add_component(comp)
            except Exception:  # noqa: BLE001
                skipped += 1
            block = []
    return ([cal] if cal.subcomponents else []), skipped


def _dt_parts(v: Any) -> tuple[str | None, bool, str | None]:
    """(local ISO text, all_day, tzid) for a DATE, floating DATE-TIME, UTC or zoned DATE-TIME value."""
    if v is None:
        return None, False, None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.isoformat(), False, None
        tzid = None
        key = getattr(v.tzinfo, "key", None)
        if key:
            tzid = key
        else:
            try:
                from icalendar.timezone import tzid_from_dt

                tzid = tzid_from_dt(v)
            except Exception:  # noqa: BLE001
                tzid = None
        if v.utcoffset() == timedelta(0) and (tzid in (None, "UTC") or str(v.tzinfo) in ("UTC", "tzutc()")):
            tzid = "UTC"
        return v.replace(tzinfo=None).isoformat(), False, tzid or "UTC"
    if isinstance(v, date):
        return v.isoformat(), True, None
    return None, False, None


def _addr(v: Any) -> dict[str, Any]:
    s = str(v)
    out: dict[str, Any] = {"email": re.sub(r"(?i)^mailto:", "", s).strip()}
    params = getattr(v, "params", {}) or {}
    for k, key in (("CN", "name"), ("ROLE", "role"), ("PARTSTAT", "partstat"), ("RSVP", "rsvp"), ("CUTYPE", "cutype"), ("DELEGATED-FROM", "delegated_from")):
        if params.get(k):
            out[key] = str(params.get(k)).strip('"')
    return out


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
        if v is None:
            return None
    s = str(v)
    return s if s.strip() else None


def _date_list(values: Any) -> list[str]:
    """EXDATE/RDATE values as ISO strings (with offset when zoned), tolerant of lists of lists and periods."""
    out: list[str] = []
    if values is None:
        return out
    items = values if isinstance(values, list) else [values]
    for item in items:
        dts = getattr(item, "dts", None)
        if dts is None:
            continue
        for d in dts:
            val = d.dt
            if isinstance(val, tuple):
                val = val[0]
            if isinstance(val, datetime):
                out.append(val.isoformat())
            elif isinstance(val, date):
                out.append(val.isoformat())
    return out


def _first(v: Any) -> Any:
    """A property that should appear once but appears several times (a damaged file): its first value."""
    return (v[0] if v else None) if isinstance(v, list) else v


def _dt_of(comp: Any, key: str) -> Any:
    v = _first(comp.get(key))
    return v.dt if v is not None else None


def clean_rule(r: Any) -> str:
    """An RRULE as text. A rule icalendar could not parse (like 'BYDAY=MO, TU' from Exchange) stays escaped text:
    undo the escaping and drop the stray whitespace so dateutil can expand it."""
    s = r.to_ical().decode("utf-8", "replace") if hasattr(r, "to_ical") else str(r)
    s = s.replace("\\;", ";").replace("\\,", ",").replace("\\", "")
    return re.sub(r"\s+", "", s).strip(";")


def component_dict(comp: Any, idx: int, source: str, calname: str | None) -> dict[str, Any]:
    name = comp.name.upper()
    kind = {"VEVENT": "event", "VTODO": "todo", "VJOURNAL": "journal", "VFREEBUSY": "freebusy"}.get(name, name.lower())
    problems: list[str] = []

    def safe(fn: Any, default: Any = None) -> Any:
        """A damaged property (bad date, impossible duration) is recorded and skipped; the rest still reads."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            problems.append(str(e).split("\n")[0][:160])
            return default

    start_v, end_v, due_v, dur = (safe(lambda k=k: _dt_of(comp, k)) for k in ("dtstart", "dtend", "due", "duration"))
    if start_v is None and kind == "todo" and due_v is not None:
        start_v = due_v  # a task without a start sits on its due date
    s_iso, all_day, tzid = _dt_parts(start_v)
    e_iso, _, e_tzid = _dt_parts(end_v)
    duration_s: float | None = None
    if start_v is not None and end_v is not None:
        try:
            if isinstance(start_v, datetime) and isinstance(end_v, datetime):
                if (start_v.tzinfo is None) == (end_v.tzinfo is None):
                    duration_s = (end_v - start_v).total_seconds()
            elif not isinstance(start_v, datetime) and not isinstance(end_v, datetime):
                duration_s = (end_v - start_v).total_seconds()
        except Exception:  # noqa: BLE001
            duration_s = None
    if duration_s is None and isinstance(dur, timedelta):
        duration_s = dur.total_seconds()
    if duration_s is None and kind == "event":
        duration_s = 86400.0 if all_day else 0.0
    if duration_s is not None and start_v is not None:
        if duration_s < 0:
            problems.append("it ends before it starts; read as a zero-length item")
            duration_s = 0.0
        try:
            _as_dt(start_v) + timedelta(seconds=duration_s)
        except (OverflowError, ValueError):
            problems.append("its end is out of range (after the year 9999); read as a zero-length item")
            duration_s = 0.0
    d: dict[str, Any] = {
        "i": idx,
        "type": kind,
        "source": source,
        "calendar": calname,
        "uid": _text(comp.get("uid")),
        "summary": _text(comp.get("summary")),
        "location": _text(comp.get("location")),
        "description": _text(comp.get("description")),
        "status": (_text(comp.get("status")) or "").upper() or None,
        "transp": (_text(comp.get("transp")) or "").upper() or None,
        "class": (_text(comp.get("class")) or "").upper() or None,
        "url": _text(comp.get("url")),
        "start": s_iso,
        "tzid": tzid,
        "all_day": all_day,
        "end": e_iso,
        "end_tzid": e_tzid,
        "duration_s": duration_s,
    }
    rid = _first(comp.get("recurrence-id"))
    if rid is not None:
        r_iso, r_all, r_tz = _dt_parts(rid.dt)
        d["recurrence_id"] = {"at": r_iso, "tzid": r_tz, "all_day": r_all, "range": str(rid.params.get("RANGE", "")) or None}
    for key in ("sequence", "priority"):
        v = comp.get(key)
        if v is not None:
            try:
                d[key] = int(v)
            except (TypeError, ValueError):
                pass
    cats = comp.get("categories")
    if cats is not None:
        vals: list[str] = []
        for c in cats if isinstance(cats, list) else [cats]:
            vals.extend(str(x) for x in getattr(c, "cats", [c]))
        d["categories"] = vals
    if comp.get("organizer") is not None:
        d["organizer"] = _addr(comp.get("organizer"))
    att = comp.get("attendee")
    if att is not None:
        d["attendees"] = [_addr(a) for a in (att if isinstance(att, list) else [att])]
    rr = comp.get("rrule")
    if rr is not None:
        rules = rr if isinstance(rr, list) else [rr]
        d["rrule"] = [x for x in (clean_rule(r) for r in rules) if x]
    ex = safe(lambda: _date_list(comp.get("exdate")), [])
    if ex:
        d["exdate"] = ex
    rd = safe(lambda: _date_list(comp.get("rdate")), [])
    if rd:
        d["rdate"] = rd
    for key in ("created", "last-modified", "dtstamp", "completed"):
        v = _first(comp.get(key))
        dt = safe(lambda v=v: v.dt) if v is not None and hasattr(v, "dt") else None
        if dt is not None:
            d[key.replace("-", "_")] = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
    if due_v is not None:
        d["due"], d["due_all_day"], d["due_tzid"] = _dt_parts(due_v)
    if comp.get("percent-complete") is not None:
        d["percent_complete"] = safe(lambda: int(_first(comp.get("percent-complete"))))
    alarms = []
    for sub in comp.subcomponents:
        if sub.name.upper() == "VALARM":
            trig = _first(sub.get("trigger"))
            tv = safe(lambda trig=trig: trig.dt) if trig is not None else None
            related = str(getattr(trig, "params", {}).get("RELATED", "START")).upper() if trig is not None else "START"
            alarms.append({"action": _text(sub.get("action")), "trigger": _trigger_text(tv, related) if tv is not None else None, "trigger_s": tv.total_seconds() if isinstance(tv, timedelta) else None, "description": _text(sub.get("description"))})
    if alarms:
        d["alarms"] = alarms
    if kind == "freebusy":
        periods = []
        fbs = comp.get("freebusy")
        for fb in fbs if isinstance(fbs, list) else ([fbs] if fbs is not None else []):
            fbtype = str(getattr(fb, "params", {}).get("FBTYPE", "BUSY")).upper()
            for p in getattr(fb, "dts", None) or [fb]:
                val = getattr(p, "dt", None)
                if isinstance(val, tuple) and len(val) == 2:
                    a, b = val
                    if isinstance(b, timedelta):
                        b = a + b
                    periods.append([a.isoformat(), b.isoformat(), fbtype])
        if not periods:
            # icalendar may leave FREEBUSY as text; parse "start/end" pairs ourselves.
            for raw in re.findall(r"FREEBUSY[^:]*:([^\r\n]+)", comp.to_ical().decode("utf-8", "replace")):
                for pr in raw.split(","):
                    if "/" in pr:
                        a, b = pr.split("/", 1)
                        try:
                            from icalendar.prop import vDDDTypes

                            av = vDDDTypes.from_ical(a)
                            bv = av + vDDDTypes.from_ical(b) if b.upper().startswith(("P", "-P")) else vDDDTypes.from_ical(b)
                            periods.append([av.isoformat(), bv.isoformat(), "BUSY"])
                        except Exception:  # noqa: BLE001
                            continue
        d["freebusy"] = periods
    if problems:
        d["problems"] = problems
    return {k: v for k, v in d.items() if v is not None and v != [] and v != ""}


def _trigger_text(tv: Any, related: str = "START") -> str:
    """'15 min before the start', '1 day before the start', 'at the end', or the absolute time."""
    if not isinstance(tv, timedelta):
        return tv.isoformat(sep=" ") if hasattr(tv, "isoformat") else str(tv)
    anchor = "the end" if related == "END" else "the start"
    secs = int(abs(tv.total_seconds()))
    if secs == 0:
        return f"at {anchor}"
    parts = []
    for unit, n in (("week", 604800), ("day", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        if secs >= n and (unit != "week" or secs % n == 0):
            k, secs = divmod(secs, n)
            parts.append(f"{k} {unit}{'s' if k > 1 and unit in ('week', 'day') else ''}")
    return " ".join(parts) + (" before " if tv < timedelta(0) else " after ") + anchor


def parse_calendar_file(path: Path) -> dict[str, Any]:
    """{calendars: [{name, method, tz, prodid}], vtimezones: {tzid: ical}, items: [component dicts], warnings}."""
    del WARNINGS[:]
    cals = read_calendar_bytes(path.read_bytes())
    out = parse_calendars(cals, path.name)
    out["warnings"] = list(WARNINGS)
    return out


def parse_calendars(cals: list[Any], source: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    vtz: dict[str, str] = {}
    infos = []
    for cal in cals:
        name = _text(cal.get("x-wr-calname")) or _text(cal.get("name"))
        info = {"name": name, "method": _text(cal.get("method")), "prodid": _text(cal.get("prodid")), "x_wr_timezone": _text(cal.get("x-wr-timezone"))}
        infos.append({k: v for k, v in info.items() if v})
        for comp in cal.walk():
            nm = comp.name.upper()
            if nm == "VTIMEZONE":
                tzid = _text(comp.get("tzid"))
                if tzid and tzid not in vtz:
                    vtz[tzid] = comp.to_ical().decode("utf-8", "replace")
            elif nm in ("VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY"):
                try:
                    d = component_dict(comp, len(items) + 1, source, name)
                except Exception as e:  # noqa: BLE001 — one broken component must not hide the rest
                    d = {"i": len(items) + 1, "type": nm.lower()[1:], "source": source, "summary": _text(comp.get("summary")), "error": str(e)}
                if info.get("x_wr_timezone") and d.get("start") and not d.get("tzid") and not d.get("all_day"):
                    d["floating_zone"] = info["x_wr_timezone"]
                items.append(d)
    return {"calendars": infos, "vtimezones": vtz, "items": items}


def load_items(paths: Iterable[Path], use_cache: bool = True) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Parsed items of several calendar files (cached by content), with their VTIMEZONEs and calendar infos."""
    items: list[dict[str, Any]] = []
    vtz: dict[str, str] = {}
    infos: list[dict[str, Any]] = []
    for p in paths:
        if use_cache:
            from _cache import cached_json

            parsed = cached_json(p, "ics-items", {}, PARSE_VERSION, lambda p=p: parse_calendar_file(p))
        else:
            parsed = parse_calendar_file(p)
        base = len(items)
        for it in parsed["items"]:
            it = dict(it)
            it["i"] = base + it["i"]
            it["source"] = p.name  # the cache is keyed by content: a copy under another name shares the entry
            items.append(it)
        for k, v in parsed["vtimezones"].items():
            vtz.setdefault(k, v)
        for c in parsed["calendars"]:
            infos.append({**c, "source": p.name})
        for w in parsed.get("warnings") or []:
            import sys as _sys

            print(f"warning: {p.name}: {w}", file=_sys.stderr)
    return items, vtz, infos


# ── recurrence ──────────────────────────────────────────────────────────


class Ctx:
    """Display zone plus the VTIMEZONEs needed to rebuild event times."""

    def __init__(self, display: tzinfo, vtz: dict[str, str]) -> None:
        self.display, self.vtz = display, vtz

    def zone(self, tzid: str | None, floating_zone: str | None = None) -> tzinfo | None:
        if tzid:
            z = resolve_tz(tzid, self.vtz)
            return z or timezone.utc
        if floating_zone:
            try:
                return get_zone(floating_zone)
            except UsageError:
                return None
        return None


def item_start(it: dict[str, Any], ctx: Ctx) -> datetime | date | None:
    s = it.get("start")
    if not s:
        return None
    if it.get("all_day"):
        return date.fromisoformat(s[:10])
    d = datetime.fromisoformat(s)
    z = ctx.zone(it.get("tzid"), it.get("floating_zone"))
    return d.replace(tzinfo=z) if z else d


def _fix_until(rule: str, dtstart: datetime | date) -> str:
    """dateutil wants UNTIL in UTC for zoned starts and naive for floating ones; calendars disagree, so normalise."""
    m = re.search(r"UNTIL=([0-9TZ]+)", rule, re.I)
    if not m:
        return rule
    raw = m.group(1).upper()
    try:
        if len(raw) == 8:
            u = datetime.strptime(raw, "%Y%m%d")
            is_date = True
        else:
            u = datetime.strptime(raw.rstrip("Z"), "%Y%m%dT%H%M%S")
            is_date = False
    except ValueError:
        return re.sub(r";?UNTIL=[^;]*", "", rule)
    if isinstance(dtstart, datetime) and dtstart.tzinfo is not None:
        if raw.endswith("Z"):
            new = raw
        else:
            local = datetime.combine(u.date(), time(23, 59, 59)) if is_date else u
            new = local.replace(tzinfo=dtstart.tzinfo).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    else:
        if raw.endswith("Z") and isinstance(dtstart, datetime):
            new = u.strftime("%Y%m%dT%H%M%S")
        elif is_date:
            new = u.strftime("%Y%m%dT235959")
        else:
            new = u.strftime("%Y%m%dT%H%M%S")
    return rule[: m.start(1)] + new + rule[m.end(1) :]


_UNIT_DAYS = {"DAILY": 1, "WEEKLY": 7}
_UNIT_SECONDS = {"HOURLY": 3600, "MINUTELY": 60, "SECONDLY": 1}


def fast_forward(rule: str, start: datetime, target: datetime) -> tuple[str, datetime]:
    """(rule, dtstart) that yield the same occurrences from `target` on, with dtstart moved to the last whole
    interval period before target, so dateutil does not walk a long series from its first occurrence.

    Rules with COUNT are left alone (COUNT counts from the true start). Where dateutil derives a missing
    BYMONTHDAY/BYMONTH from dtstart, the original values are written into the rule first, so moving dtstart to
    the first of a month changes nothing.
    """
    if start >= target:
        return rule, start
    parts = {k.upper(): v for k, _, v in (p.partition("=") for p in rule.split(";")) if k}
    if "COUNT" in parts:
        return rule, start
    freq = parts.get("FREQ", "").upper()
    try:
        interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    except ValueError:
        return rule, start
    naive_target = target.astimezone(start.tzinfo).replace(tzinfo=None) if start.tzinfo and target.tzinfo else target.replace(tzinfo=None)
    s0 = start.replace(tzinfo=None)
    if freq in _UNIT_DAYS:
        period = timedelta(days=_UNIT_DAYS[freq] * interval)
        k = (naive_target - s0) // period - 1
        return (rule, start + k * period) if k > 0 else (rule, start)
    if freq in _UNIT_SECONDS:
        period = timedelta(seconds=_UNIT_SECONDS[freq] * interval)
        k = (naive_target - s0) // period - 1
        if k <= 0:
            return rule, start
        # Hourly and finer steps count real elapsed time in dateutil only on naive wall time: stay on that clock.
        new = (s0 + k * period).replace(tzinfo=start.tzinfo)
        return rule, new
    if freq in ("MONTHLY", "YEARLY"):
        implicit = not any(x in parts for x in ("BYWEEKNO", "BYYEARDAY", "BYMONTHDAY", "BYDAY"))
        step = interval if freq == "MONTHLY" else 12 * interval
        months = (naive_target.year - s0.year) * 12 + (naive_target.month - s0.month)
        k = months // step - 1
        if k <= 0:
            return rule, start
        extra = []
        if implicit:
            extra.append(f"BYMONTHDAY={s0.day}")
            if freq == "YEARLY" and "BYMONTH" not in parts:
                extra.append(f"BYMONTH={s0.month}")
        total = s0.month - 1 + k * step
        new = start.replace(year=s0.year + total // 12, month=total % 12 + 1, day=1)
        return (rule + "".join(";" + e for e in extra)), new
    return rule, start


def _as_dt(v: datetime | date) -> datetime:
    return v if isinstance(v, datetime) else datetime.combine(v, time(0))


def _parse_iso_like(s: str, ref: datetime, all_day: bool) -> datetime:
    if all_day or re.fullmatch(r"\d{4}-\d\d-\d\d", s):
        return datetime.combine(date.fromisoformat(s[:10]), time(0), tzinfo=ref.tzinfo) if ref.tzinfo and not all_day else datetime.combine(date.fromisoformat(s[:10]), time(0))
    d = datetime.fromisoformat(s)
    if ref.tzinfo is None:
        return d.replace(tzinfo=None) if d.tzinfo else d
    return d if d.tzinfo else d.replace(tzinfo=ref.tzinfo)


MAX_OCCURRENCES = 20000


def expand_item(it: dict[str, Any], ctx: Ctx, win_start: datetime, win_end: datetime, skip: set[str] | None = None, capped: list | None = None) -> list[tuple[datetime, datetime]]:
    """Occurrences (start, end) of one item overlapping the window. Naive datetimes for all-day and floating items
    (their wall time is compared with the window in the display zone); aware ones otherwise. When a series passes
    MAX_OCCURRENCES in the window, the expansion stops and the last instant reached is appended to `capped`."""
    s0 = item_start(it, ctx)
    if s0 is None:
        return []
    dur = timedelta(seconds=float(it.get("duration_s") or 0))
    start = _as_dt(s0)
    naive = start.tzinfo is None
    ws, we = win_start, win_end
    if naive:
        ws = win_start.astimezone(ctx.display).replace(tzinfo=None)
        we = win_end.astimezone(ctx.display).replace(tzinfo=None)
    rules = it.get("rrule") or []
    rdates = it.get("rdate") or []
    out: list[tuple[datetime, datetime]] = []
    if not rules and not rdates:
        if start < we and (start + dur > ws or (dur == timedelta(0) and start >= ws)):
            out.append((start, start + dur))
        return out
    from dateutil.rrule import rruleset, rrulestr

    rs = rruleset()
    for r in rules:
        try:
            rule, first = fast_forward(_fix_until(r, start), start, ws - dur)
            rs.rrule(rrulestr(rule, dtstart=first, ignoretz=False))
        except Exception:  # noqa: BLE001 — a broken rule: keep the first occurrence at least
            rs.rdate(start)
    if not rules:
        rs.rdate(start)
    for rd in rdates:
        try:
            rs.rdate(_parse_iso_like(rd, start, bool(it.get("all_day"))))
        except ValueError:
            continue
    for ex in it.get("exdate") or []:
        try:
            exd = _parse_iso_like(ex, start, bool(it.get("all_day")))
            if not naive and exd.tzinfo is None:
                exd = exd.replace(tzinfo=start.tzinfo)
            rs.exdate(exd)
            if it.get("all_day") or len(ex) == 10:
                # A DATE exdate on a timed series removes that day's occurrence.
                rs.exdate(datetime.combine(exd.date(), start.timetz()) if not naive else datetime.combine(exd.date(), start.time()))
        except ValueError:
            continue
    n = 0
    for occ in rs.xafter(ws - dur - timedelta(seconds=1), inc=True):
        if occ >= we:
            break
        key = occ_key(occ)
        if skip and key in skip:
            continue
        if occ + dur > ws or (dur == timedelta(0) and occ >= ws):
            out.append((occ, occ + dur))
        n += 1
        if n >= MAX_OCCURRENCES:
            if capped is not None:
                capped.append(occ)
            break
    return out


def occ_key(d: datetime | date) -> str:
    """A comparable key for an instance (UTC instant for zoned times, wall time otherwise)."""
    if isinstance(d, datetime) and d.tzinfo is not None:
        return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if isinstance(d, datetime):
        return d.strftime("%Y%m%dT%H%M%S")
    return d.strftime("%Y%m%d") + "T000000"


def expand(items: list[dict[str, Any]], ctx: Ctx, win_start: datetime, win_end: datetime, types: tuple[str, ...] = ("event",), include_cancelled: bool = False, notes: list[str] | None = None) -> list[dict[str, Any]]:
    """All occurrences in the window across items, with RECURRENCE-ID overrides and cancelled instances applied.
    `notes` collects what the result leaves out: series cut at MAX_OCCURRENCES, items that could not be expanded."""
    overrides: dict[str, set[str]] = {}
    for it in items:
        rid = it.get("recurrence_id")
        if rid and it.get("uid"):
            if rid.get("all_day"):
                k = rid["at"][:10].replace("-", "") + "T000000"
            else:
                d = datetime.fromisoformat(rid["at"])
                z = ctx.zone(rid.get("tzid"), it.get("floating_zone"))
                k = occ_key(d.replace(tzinfo=z) if z else d)
            overrides.setdefault(it["uid"], set()).add(k)
    occs: list[dict[str, Any]] = []
    for it in items:
        if it.get("type") not in types or "error" in it:
            continue
        if it.get("status") == "CANCELLED" and not include_cancelled and not it.get("recurrence_id"):
            continue
        skip = overrides.get(it.get("uid") or "") if not it.get("recurrence_id") else None
        if it.get("recurrence_id") and it.get("status") == "CANCELLED" and not include_cancelled:
            continue
        capped: list = []
        try:
            got = expand_item(it, ctx, win_start, win_end, skip, capped)
        except (OverflowError, ValueError) as e:
            if notes is not None:
                notes.append(f"item #{it['i']} ({_short(it.get('summary'))}) could not be expanded ({e}); it is left out")
            continue
        if capped and notes is not None:
            last = capped[0]
            notes.append(f"item #{it['i']} ({_short(it.get('summary'))}) repeats more than {MAX_OCCURRENCES:,} times in this window; only occurrences up to {last.isoformat(sep=' ', timespec='minutes') if isinstance(last, datetime) else last} are included (narrow --from/--to to see the rest)")
        for s, e in got:
            occs.append({"item": it, "start": s, "end": e})
    occs.sort(key=lambda o: sort_key(o["start"], ctx))
    return occs


def _short(s: str | None, n: int = 60) -> str:
    s = (s or "(no title)").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def sort_key(d: datetime, ctx: Ctx) -> float:
    if d.tzinfo is None:
        return d.replace(tzinfo=ctx.display).timestamp()
    return d.timestamp()


def to_display(d: datetime, ctx: Ctx) -> datetime:
    return d.astimezone(ctx.display) if d.tzinfo is not None else d


def aware(d: datetime, ctx: Ctx) -> datetime:
    """An instant for busy/free maths: naive (all-day or floating) times are taken in the display zone."""
    return d if d.tzinfo is not None else d.replace(tzinfo=ctx.display)


# ── descriptions ────────────────────────────────────────────────────────

_DAYS = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ordinal(n: int) -> str:
    if n == -1:
        return "last"
    if n < 0:
        return f"{_ordinal(-n)}-to-last"
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe_rrule(rule: str) -> str:
    """Plain English for an RRULE: 'every 2 weeks on Mon, Wed until 2026-12-31' (the rule itself if it is odd)."""
    try:
        return _describe(rule)
    except (ValueError, IndexError, KeyError):
        return rule


def _describe(rule: str) -> str:
    parts = dict(p.split("=", 1) for p in rule.upper().split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    interval = int(parts.get("INTERVAL", "1") or 1)
    unit = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year", "HOURLY": "hour", "MINUTELY": "minute", "SECONDLY": "second"}.get(freq, freq.lower())
    s = f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
    if interval == 1 and freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        s = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly"}[freq]
    if "BYDAY" in parts:
        days = []
        for tok in parts["BYDAY"].split(","):
            m = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", tok)
            if m:
                days.append((f"{_ordinal(int(m.group(1)))} " if m.group(1) else "") + _DAYS.get(m.group(2), m.group(2)))
        if "BYSETPOS" in parts:
            pos = ", ".join(_ordinal(int(x)) for x in parts["BYSETPOS"].split(",") if x.lstrip("+-").isdigit())
            s += f" on the {pos} of {'/'.join(days)}"
        else:
            s += " on " + ", ".join(days)
    if "BYMONTHDAY" in parts:
        s += " on day " + ", ".join(parts["BYMONTHDAY"].split(","))
    if "BYMONTH" in parts:
        s += " in " + ", ".join(_MONTHS[int(x) - 1] for x in parts["BYMONTH"].split(",") if x.isdigit() and 1 <= int(x) <= 12)
    if "BYHOUR" in parts:
        s += " at " + ", ".join(f"{int(h):02d}:{int(parts.get('BYMINUTE', '0').split(',')[0] or 0):02d}" for h in parts["BYHOUR"].split(","))
    if "COUNT" in parts:
        s += f", {parts['COUNT']} times"
    if "UNTIL" in parts:
        u = parts["UNTIL"]
        s += f" until {u[:4]}-{u[4:6]}-{u[6:8]}"
    return s


def fmt_dt(d: datetime | date | None, all_day: bool = False, with_zone: bool = True) -> str:
    if d is None:
        return ""
    if all_day or not isinstance(d, datetime):
        return d.strftime("%a %Y-%m-%d")
    s = d.strftime("%a %Y-%m-%d %H:%M")
    if with_zone and d.tzinfo is not None:
        s += " " + (getattr(d.tzinfo, "key", None) or d.strftime("%z"))
    return s


def when_text(it: dict[str, Any], ctx: Ctx) -> tuple[str, str]:
    """(start, end) as text in the item's own zone (or the display zone when one was asked for)."""
    s = item_start(it, ctx)
    if s is None:
        return "", ""
    if it.get("all_day"):
        days = int(round(float(it.get("duration_s") or 86400) / 86400))
        try:
            end = s + timedelta(days=max(days, 1) - 1) if isinstance(s, date) else s
        except (OverflowError, ValueError):
            end, days = s, 1
        return fmt_dt(s, True), (fmt_dt(end, True) if days > 1 else "")
    try:
        e = s + timedelta(seconds=float(it.get("duration_s") or 0)) if isinstance(s, datetime) else None
    except (OverflowError, ValueError):
        e = None
    return fmt_dt(s), fmt_dt(e) if e else ""


def summarize_invite(data: bytes, method_hint: str | None = None) -> dict[str, Any] | None:
    """A short summary of the first event or task of a calendar part inside an email (for mail_read)."""
    cals = read_calendar_bytes(data)
    parsed = parse_calendars(cals, "invite")
    items = [i for i in parsed["items"] if i.get("type") in ("event", "todo")]
    if not items:
        return None
    it = items[0]
    ctx = Ctx(timezone.utc, parsed["vtimezones"])
    start, end = when_text(it, ctx)
    method = (parsed["calendars"][0].get("method") if parsed["calendars"] else None) or method_hint
    out = {
        "method": (method or "").upper() or None,
        "summary": it.get("summary"),
        "start": start,
        "end": end,
        "location": it.get("location"),
        "organizer": (it.get("organizer") or {}).get("email"),
        "attendees": it.get("attendees", []),
        "status": it.get("status"),
        "uid": it.get("uid"),
        "sequence": it.get("sequence"),
        "description": it.get("description"),
        "recurrence": "; ".join(describe_rrule(r) for r in it.get("rrule", [])) or None,
        "events": len(items),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}
