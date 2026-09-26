"""An iCalendar rebuilt from an Outlook appointment or meeting .msg (MS-OXOCAL). Outlook keeps such items in MAPI
properties, not in a text/calendar part: start and end (UTC), the time zone definition (a Windows zone name), the
recurrence blob (PidLidAppointmentRecur: pattern, end, deleted and changed instances), busy status, reminder,
sequence and the meeting's global object id (the UID other clients know it by).

The result is ordinary .ics bytes, so ics_tool, mail_extract and --to-eml treat it like any invite.
"""

from __future__ import annotations

import struct
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

_MIN_EPOCH = datetime(1601, 1, 1)
_DAYS = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
_GREGORIAN = {0, 1, 2, 9, 10, 11, 12}  # CAL_DEFAULT and the Gregorian variants


class RecurError(ValueError):
    pass


def minutes_dt(v: int) -> datetime:
    """A naive local datetime from minutes since 1601-01-01 (how the recurrence blob stores times)."""
    return _MIN_EPOCH + timedelta(minutes=v)


def tz_keyname(blob: Any) -> str | None:
    """The Windows zone name inside a TZDEFINITION blob (PidLidAppointmentTimeZoneDefinition*)."""
    if not isinstance(blob, bytes) or len(blob) < 8:
        return None
    major, _minor, _cb, _flags, cch = struct.unpack_from("<BBHHH", blob, 0)
    if major != 2 or not cch or 8 + cch * 2 > len(blob):
        return None
    return blob[8 : 8 + cch * 2].decode("utf-16-le", "replace").strip("\x00").strip() or None


def parse_recurrence(b: bytes, codec: str | None = None) -> dict[str, Any]:
    """The AppointmentRecurrencePattern structure (MS-OXOCAL 2.2.1.44) as a dict; RecurError when it is not one."""
    o = 0

    def take(fmt: str) -> int:
        nonlocal o
        size = struct.calcsize(fmt)
        if o + size > len(b):
            raise RecurError("the recurrence blob is truncated")
        (v,) = struct.unpack_from(fmt, b, o)
        o += size
        return v

    def text8(n: int) -> str:
        nonlocal o
        if o + n > len(b):
            raise RecurError("the recurrence blob is truncated")
        raw = b[o : o + n]
        o += n
        return raw.decode(codec or "cp1252", "replace")

    if take("<H") != 0x3004 or take("<H") != 0x3004:
        raise RecurError("not a recurrence pattern (unknown version)")
    r: dict[str, Any] = {"freq": take("<H"), "ptype": take("<H"), "calendar": take("<H"), "first": take("<I"), "period": take("<I"), "sliding": take("<I")}
    pt = r["ptype"]
    if pt == 1:
        r["days"] = take("<I")
    elif pt in (2, 4, 0xA, 0xC):
        r["day"] = take("<I")
    elif pt in (3, 0xB):
        r["days"], r["n"] = take("<I"), take("<I")
    elif pt != 0:
        raise RecurError(f"unknown recurrence pattern type {pt:#x}")
    r["end_type"], r["count"], r["first_dow"] = take("<I"), take("<I"), take("<I")
    n = take("<I")
    if n > 50000:
        raise RecurError("implausible deleted-instance count")
    r["deleted"] = [take("<I") for _ in range(n)]
    n = take("<I")
    if n > 50000:
        raise RecurError("implausible modified-instance count")
    r["modified"] = [take("<I") for _ in range(n)]
    r["start_date"], r["end_date"] = take("<I"), take("<I")
    r["reader2"], r["writer2"] = take("<I"), take("<I")
    r["start_offset"], r["end_offset"] = take("<I"), take("<I")
    n = take("<H")
    exceptions = []
    for _ in range(n):
        ex: dict[str, Any] = {"start": take("<I"), "end": take("<I"), "orig": take("<I")}
        flags = take("<H")
        ex["flags"] = flags
        if flags & 0x0001:
            take("<H")
            ex["subject"] = text8(take("<H"))
        if flags & 0x0002:
            take("<I")
        if flags & 0x0004:
            ex["reminder_delta"] = take("<I")
        if flags & 0x0008:
            ex["reminder_set"] = take("<I")
        if flags & 0x0010:
            take("<H")
            ex["location"] = text8(take("<H"))
        if flags & 0x0020:
            ex["busy"] = take("<I")
        if flags & 0x0040:
            take("<I")
        if flags & 0x0080:
            ex["all_day"] = bool(take("<I"))
        if flags & 0x0100:
            take("<I")
        exceptions.append(ex)
    r["exceptions"] = exceptions
    try:  # Unicode subjects and locations of changed instances (ExtendedException), when present
        skip = take("<I")
        o += skip
        for ex in exceptions:
            if r["writer2"] >= 0x3009:
                size = take("<I")  # ChangeHighlight: its size, then that many bytes
                o += size
            size = take("<I")  # ReservedBlockEE1 (never `o += take(…)`: take moves o itself)
            o += size
            if ex["flags"] & 0x0011:
                for _ in range(3):
                    take("<I")
                if ex["flags"] & 0x0001:
                    ln = take("<H")
                    ex["subject"] = b[o : o + ln * 2].decode("utf-16-le", "replace")
                    o += ln * 2
                if ex["flags"] & 0x0010:
                    ln = take("<H")
                    ex["location"] = b[o : o + ln * 2].decode("utf-16-le", "replace")
                    o += ln * 2
            size = take("<I")  # ReservedBlockEE2
            o += size
    except RecurError:
        pass
    return r


def recurrence_rule(r: dict[str, Any], dtstart: datetime | date, until_at: Any = None) -> str:
    """An RRULE for a parsed pattern (Gregorian calendars; others raise RecurError). `until_at(last_date)` gives the
    aware start of the last occurrence for an end date (UNTIL is written in UTC)."""
    if r["calendar"] not in _GREGORIAN or r["ptype"] >= 0xA:
        raise RecurError("a non-Gregorian (Hijri or Hebrew) recurrence")
    pt, period = r["ptype"], r["period"]
    if pt == 0:
        if period <= 0 or period % 1440:
            raise RecurError(f"a daily period of {period} minutes")
        parts = ["FREQ=DAILY", f"INTERVAL={period // 1440}"]
    elif pt == 1:
        days = [_DAYS[i] for i in range(7) if r["days"] & (1 << i)]
        if not days or period <= 0:
            raise RecurError("a weekly pattern without days")
        parts = ["FREQ=WEEKLY", f"INTERVAL={period}", "BYDAY=" + ",".join(days), f"WKST={_DAYS[r['first_dow'] % 7]}"]
    elif pt in (2, 3, 4):
        if period <= 0:
            raise RecurError("a monthly pattern with no period")
        yearly = r["freq"] == 0x200D or period % 12 == 0 and r["freq"] != 0x200C
        parts = ["FREQ=YEARLY", f"INTERVAL={max(1, period // 12)}", f"BYMONTH={dtstart.month}"] if yearly else ["FREQ=MONTHLY", f"INTERVAL={period}"]
        if pt == 2:
            d = r["day"]
            if not 1 <= d <= 31:
                raise RecurError(f"day {d} of the month")
            # Outlook's "day 30" means the 30th, or the month's last day when it is shorter.
            parts += [f"BYMONTHDAY={d}"] if d <= 28 else ["BYMONTHDAY=" + ",".join(str(x) for x in range(28, d + 1)), "BYSETPOS=-1"]
        elif pt == 4:
            parts.append("BYMONTHDAY=-1")
        else:
            days = [_DAYS[i] for i in range(7) if r["days"] & (1 << i)]
            if not days or not 1 <= r["n"] <= 5:
                raise RecurError("a 'Nth weekday' pattern without days")
            parts += ["BYDAY=" + ",".join(days), f"BYSETPOS={r['n'] if r['n'] < 5 else -1}"]
    else:
        raise RecurError(f"pattern type {pt}")
    if r["end_type"] == 0x2022 and r["count"]:
        parts.append(f"COUNT={r['count']}")
    elif r["end_type"] == 0x2021:
        last = minutes_dt(r["end_date"]).date()
        if isinstance(dtstart, datetime) and until_at is not None:
            parts.append("UNTIL=" + until_at(last).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        else:
            parts.append("UNTIL=" + last.strftime("%Y%m%d"))
    return ";".join(parts)


def _uid(goid: Any) -> str | None:
    """The iCalendar UID for a PidLidCleanGlobalObjectId / PidLidGlobalObjectId, as Outlook exports it: the embedded
    vCal UID when the item came from iCalendar, else the id in upper-case hex."""
    if not isinstance(goid, bytes) or len(goid) < 20:
        return None
    if len(goid) > 52 and goid[40:48] == b"vCal-Uid":
        return goid[52:].split(b"\x00", 1)[0].decode("utf-8", "replace") or goid.hex().upper()
    return goid.hex().upper()


def outlook_calendar(p: Any, model: dict[str, Any]) -> tuple[bytes, list[str]] | None:
    """(.ics bytes, warnings) for an Outlook appointment or meeting item, or None when it has no start."""
    from _ics import get_zone, windows_zones
    from _icsbuild import make_calendar, make_event
    from _msg import PSETID_APPOINTMENT, PSETID_COMMON, PSETID_MEETING

    warnings: list[str] = []
    mclass = (model.get("message_class") or "").lower()
    ap = lambda pid, default=None: p.named(PSETID_APPOINTMENT, pid, default)  # noqa: E731
    start_utc, end_utc = ap(0x820D), ap(0x820E)
    if not isinstance(start_utc, datetime):
        return None
    if not isinstance(end_utc, datetime) or end_utc < start_utc:
        end_utc = start_utc
    all_day = bool(ap(0x8215, False))
    zone = None
    key = tz_keyname(ap(0x8260)) or tz_keyname(ap(0x825E))
    if key:
        try:
            zone = get_zone(windows_zones().get(key, key))
        except Exception:  # noqa: BLE001
            warnings.append(f"the time zone '{key}' is unknown; times are written in UTC")
    method = "REQUEST" if "meeting.request" in mclass else "CANCEL" if "canceled" in mclass else "REPLY" if "meeting.resp" in mclass else "PUBLISH"
    spec: dict[str, Any] = {"summary": model.get("subject") or "(no title)", "location": ap(0x8208) or None, "uid": _uid(p.named(PSETID_MEETING, 0x23)) or _uid(p.named(PSETID_MEETING, 0x3))}
    text = (model.get("text") or "").strip()
    if text:
        spec["description"] = text[:20000]
    seq = ap(0x8201)
    if isinstance(seq, int) and seq >= 0:
        spec["sequence"] = seq
    busy = ap(0x8205)
    if isinstance(busy, int):
        spec["transp"] = "TRANSPARENT" if busy == 0 else "OPAQUE"
        if busy == 1:
            spec["status"] = "TENTATIVE"
    if method == "CANCEL":
        spec["status"] = "CANCELLED"
    if p.named(PSETID_COMMON, 0x8503, False):
        delta = p.named(PSETID_COMMON, 0x8501)
        if isinstance(delta, int) and 0 <= delta < 60 * 24 * 60:
            spec["alarms"] = [f"{delta}m"]
    # Organizer and attendees: a meeting (not a plain appointment) has them.
    sender = (model.get("from") or [{}])[0]
    state = ap(0x8217, 0)
    is_meeting = "schedule.meeting" in mclass or (isinstance(state, int) and state & 1)
    if method == "REPLY":
        status = "ACCEPTED" if "resp.pos" in mclass else "DECLINED" if "resp.neg" in mclass else "TENTATIVE"
        org = next((a for a in model.get("to") or [] if "@" in (a.get("email") or "")), None)
        if org:
            spec["organizer"] = {"email": org["email"], "name": org.get("name") or None}
        if "@" in (sender.get("email") or ""):
            spec["attendees"] = [{"email": sender["email"], "name": sender.get("name") or None, "partstat": status, "rsvp": False}]
    elif is_meeting:
        if "@" in (sender.get("email") or ""):
            spec["organizer"] = {"email": sender["email"], "name": sender.get("name") or None}
        else:
            warnings.append("the organizer has no SMTP address in this .msg; ORGANIZER is left out")
        seen = {(sender.get("email") or "").lower()}
        atts = []
        for role, lst in (("required", model.get("to")), ("optional", model.get("cc")), ("fyi", model.get("bcc"))):
            for a in lst or []:
                em = (a.get("email") or "").strip()
                if "@" not in em or em.lower() in seen:
                    continue
                seen.add(em.lower())
                atts.append({"email": em, "name": a.get("name") or None, "role": role, **({"cutype": "RESOURCE"} if role == "fyi" else {})})
        spec["attendees"] = atts
    spec = {k: v for k, v in spec.items() if v not in (None, "", [])}
    recur = ap(0x8216)
    r = None
    if ap(0x8223, False) and isinstance(recur, bytes):
        try:
            r = parse_recurrence(recur, getattr(p, "codec", None))
        except RecurError as e:
            warnings.append(f"the recurrence could not be read ({e}); only the first occurrence is written")
    offset = timedelta(0)
    if r is not None and zone is None and not all_day:
        # No usable zone: the first occurrence's own UTC offset (right until the next daylight-saving change).
        offset = minutes_dt(r["start_date"] + r["start_offset"]) - start_utc.replace(tzinfo=None)
        warnings.append("the recurrence's time zone is unknown; times are written in UTC with the first occurrence's offset")

    def at(local: datetime) -> datetime:
        """An aware time for a local wall time of the item."""
        return local.replace(tzinfo=zone) if zone is not None else (local - offset).replace(tzinfo=timezone.utc)

    first: datetime | date
    if r is not None:
        day0 = minutes_dt(r["start_date"])
        if all_day:
            first = day0.date()
            days = max(1, (r["end_offset"] - r["start_offset"]) // 1440)
            spec.update(start=first, end=first + timedelta(days=days), all_day=True)
        else:
            first = at(day0 + timedelta(minutes=r["start_offset"]))
            spec.update(start=first, duration=f"{max(0, r['end_offset'] - r['start_offset'])}m")
        try:
            spec["rrule"] = recurrence_rule(r, first, lambda d: at(datetime.combine(d, time(0)) + timedelta(minutes=r["start_offset"])))
        except RecurError as e:
            warnings.append(f"the recurrence could not be converted ({e}); only the first occurrence is written")
            r = None
            spec.pop("duration", None)
    if r is None:
        z = zone or timezone.utc
        if all_day:
            d0, d1 = start_utc.astimezone(z).date(), end_utc.astimezone(z).date()
            spec.update(start=d0, end=max(d1, d0 + timedelta(days=1)), all_day=True)
        else:
            spec.update(start=start_utc.astimezone(z), duration=f"{int((end_utc - start_utc).total_seconds() // 60)}m")
            spec.pop("end", None)
    master = make_event(spec, zone or timezone.utc)
    events = [master]
    if r is not None:
        changed_days = {minutes_dt(ex["orig"]).date() for ex in r["exceptions"]}
        ex_dates = sorted({minutes_dt(v).date() for v in r["deleted"]} - changed_days)
        if ex_dates:
            if all_day:
                master.add("exdate", ex_dates)
            else:
                master.add("exdate", [at(datetime.combine(d, time(0)) + timedelta(minutes=r["start_offset"])) for d in ex_dates])
        for ex in r["exceptions"]:
            s, e, orig = minutes_dt(ex["start"]), minutes_dt(ex["end"]), minutes_dt(ex["orig"])
            ov = {k: v for k, v in spec.items() if k not in ("rrule", "start", "end", "all_day", "duration", "alarms")}
            if ex.get("subject"):
                ov["summary"] = ex["subject"]
            if ex.get("location"):
                ov["location"] = ex["location"]
            if ex.get("busy") is not None:
                ov["transp"] = "TRANSPARENT" if ex["busy"] == 0 else "OPAQUE"
            if ex.get("all_day", all_day):
                ov.update(start=s.date(), end=max(e.date(), s.date() + timedelta(days=1)), all_day=True)
            else:
                ov.update(start=at(s), duration=f"{max(0, int((e - s).total_seconds() // 60))}m")
            ev = make_event(ov, zone or timezone.utc)
            ev.add("recurrence-id", orig.date() if all_day else at(orig))
            events.append(ev)
    cal = make_calendar(events, None, method)
    return cal.to_ical(), warnings
