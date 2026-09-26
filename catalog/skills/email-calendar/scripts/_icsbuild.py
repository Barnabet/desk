"""Building and changing iCalendar objects (icalendar): events from JSON-like specs, calendars with the
VTIMEZONE components they need, merge/dedupe, edit operations and invitation replies.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from _common import SkillError, UsageError
from _ics import get_zone, parse_duration

PRODID = "-//Desk//email-calendar skill//EN"
_ROLES = {"required": "REQ-PARTICIPANT", "req": "REQ-PARTICIPANT", "optional": "OPT-PARTICIPANT", "opt": "OPT-PARTICIPANT", "chair": "CHAIR", "fyi": "NON-PARTICIPANT", "non": "NON-PARTICIPANT"}


def _when(v: Any, tz: Any, all_day: bool) -> date | datetime:
    if isinstance(v, (date, datetime)):
        return v
    s = str(v).strip()
    if all_day or re.fullmatch(r"\d{4}-\d\d-\d\d", s):
        return date.fromisoformat(s[:10])
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise UsageError(f"bad date/time {v!r} (ISO like 2026-10-01T14:00, or 2026-10-01 for all-day)") from e
    return d if d.tzinfo else d.replace(tzinfo=tz)


def _cal_address(v: Any, default_role: str | None = None) -> Any:
    from icalendar import vCalAddress

    if isinstance(v, str):
        import email.utils

        name, addr = email.utils.parseaddr(v)
        v = {"email": addr or v, "name": name}
    addr = str(v.get("email") or "").strip()
    if not addr:
        raise UsageError(f"attendee without an email: {v!r}")
    ca = vCalAddress(addr if addr.lower().startswith("mailto:") else f"mailto:{addr}")
    if v.get("name"):
        ca.params["CN"] = str(v["name"])
    role = v.get("role", default_role)
    if role:
        ca.params["ROLE"] = _ROLES.get(str(role).lower(), str(role).upper())
    if default_role is not None:
        ca.params["PARTSTAT"] = str(v.get("partstat") or v.get("status") or "NEEDS-ACTION").upper()
        if v.get("rsvp", True):
            ca.params["RSVP"] = "TRUE"
        if v.get("cutype"):
            ca.params["CUTYPE"] = str(v["cutype"]).upper()
    return ca


def make_event(spec: dict[str, Any], default_tz: Any) -> Any:
    """An icalendar Event from a spec: summary, start, end|duration, tz, all_day, location, description,
    attendees, organizer, rrule, exdate, rdate, alarms, status, transp, categories, url, uid, sequence, priority, class."""
    from icalendar import Alarm, Event, vRecur

    tz = get_zone(spec["tz"]) if spec.get("tz") else default_tz
    all_day = bool(spec.get("all_day"))
    if not spec.get("start"):
        raise UsageError(f"event {spec.get('summary')!r} has no start")
    start = _when(spec["start"], tz, all_day)
    if isinstance(start, date) and not isinstance(start, datetime):
        all_day = True
    ev = Event()
    ev.add("uid", str(spec.get("uid") or f"{uuid.uuid4()}@desk.local"))
    ev.add("dtstamp", datetime.now(timezone.utc).replace(microsecond=0))
    ev.add("summary", str(spec.get("summary") or "(no title)"))
    ev.add("dtstart", start)
    if spec.get("end"):
        end = _when(spec["end"], tz, all_day)
        if all_day and isinstance(end, date) and not isinstance(end, datetime) and end <= start:
            end = start + timedelta(days=1)  # an inclusive last day given as the end
        if not all_day and end <= start:
            raise UsageError(f"event {spec.get('summary')!r} ends before it starts")
        ev.add("dtend", end)
    elif spec.get("duration"):
        d = parse_duration(str(spec["duration"]))
        ev.add("dtend", start + d)
    elif all_day:
        ev.add("dtend", start + timedelta(days=int(spec.get("days", 1))))
    else:
        ev.add("dtend", start + timedelta(hours=1))
    for key in ("location", "description", "url"):
        if spec.get(key):
            ev.add(key, str(spec[key]))
    if spec.get("status"):
        ev.add("status", str(spec["status"]).upper())
    if spec.get("transp") or spec.get("busy") is not None:
        ev.add("transp", str(spec.get("transp") or ("OPAQUE" if spec.get("busy") else "TRANSPARENT")).upper())
    elif all_day:
        ev.add("transp", "TRANSPARENT")
    if spec.get("class"):
        ev.add("class", str(spec["class"]).upper())
    for key in ("sequence", "priority"):
        if spec.get(key) is not None:
            ev.add(key, int(spec[key]))
    if spec.get("categories"):
        cats = spec["categories"]
        ev.add("categories", cats if isinstance(cats, list) else [c.strip() for c in str(cats).split(",")])
    if spec.get("organizer"):
        ev.add("organizer", _cal_address(spec["organizer"]))
    for att in spec.get("attendees") or []:
        ev.add("attendee", _cal_address(att, "REQ-PARTICIPANT"))
    if spec.get("rrule"):
        rr = spec["rrule"]
        rule = vRecur.from_ical(rr) if isinstance(rr, str) else vRecur({k.upper(): v for k, v in rr.items()})
        ev.add("rrule", rule)
    for key in ("exdate", "rdate"):
        vals = spec.get(key)
        if vals:
            vals = vals if isinstance(vals, list) else [vals]
            conv = []
            for v in vals:
                d = _when(v, tz, all_day)
                if not all_day and not isinstance(d, datetime):
                    d = datetime.combine(d, start.timetz() if isinstance(start, datetime) else time(0))
                conv.append(d)
            ev.add(key, conv)
    for al in spec.get("alarms") or []:
        a = Alarm()
        if isinstance(al, str):
            al = {"before": al}
        a.add("action", str(al.get("action", "DISPLAY")).upper())
        before = parse_duration(str(al.get("before") or al.get("trigger") or "15m").lstrip("-"))
        a.add("trigger", -before if before is not None else timedelta(minutes=-15))
        a.add("description", str(al.get("description") or spec.get("summary") or "Reminder"))
        ev.add_component(a)
    return ev


def make_calendar(events: list[Any], name: str | None = None, method: str | None = None, extra: list[Any] = ()) -> Any:  # type: ignore[assignment]
    from icalendar import Calendar

    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    if method:
        cal.add("method", method.upper())
    if name:
        cal.add("x-wr-calname", name)
    for c in extra:
        cal.add_component(c)
    for ev in events:
        cal.add_component(ev)
    add_timezones(cal)
    return cal


def add_timezones(cal: Any) -> list[str]:
    """Adds the VTIMEZONEs the calendar's TZIDs need (so every client reads the times right); returns missing ones."""
    try:
        years = []
        for comp in cal.walk():
            for key in ("dtstart", "dtend"):
                v = comp.get(key)
                if v is not None and hasattr(v, "dt"):
                    years.append(v.dt.year if hasattr(v.dt, "year") else 2026)
        first = date(max(1970, (min(years) if years else 2020) - 1), 1, 1)
        last = date(min(2100, (max(years) if years else 2030) + 10), 1, 1)
        cal.add_missing_timezones(first_date=first, last_date=last)
    except Exception:  # noqa: BLE001
        cal.add_missing_timezones()
    return sorted(cal.get_missing_tzids())


def ics_bytes(cal: Any) -> bytes:
    return cal.to_ical()


# ── merge ───────────────────────────────────────────────────────────────


def _rank(comp: Any) -> tuple:
    seq = int(comp.get("sequence", 0) or 0)
    lm = comp.get("last-modified") or comp.get("dtstamp")
    stamp = lm.dt.timestamp() if lm is not None and isinstance(getattr(lm, "dt", None), datetime) else 0.0
    return (seq, stamp)


def _content_key(comp: Any) -> tuple:
    summ = re.sub(r"\s+", " ", str(comp.get("summary") or "")).strip().casefold()

    def inst(key: str) -> str:
        v = comp.get(key)
        if v is None:
            return ""
        d = v.dt
        if isinstance(d, datetime):
            return d.astimezone(timezone.utc).isoformat() if d.tzinfo else d.isoformat()
        return d.isoformat()

    return (comp.name, summ, inst("dtstart"), inst("dtend"), str(comp.get("rrule").to_ical() if comp.get("rrule") is not None else ""))


def merge_calendars(cals: list[tuple[str, Any]], dedupe: str = "uid", name: str | None = None) -> tuple[Any, dict[str, int]]:
    """One calendar from many. dedupe 'uid' keeps the newest version of each UID + RECURRENCE-ID (by SEQUENCE,
    then LAST-MODIFIED); 'content' also merges same title + start + end under different UIDs; 'none' keeps all."""
    from icalendar import Calendar

    out = Calendar()
    out.add("prodid", PRODID)
    out.add("version", "2.0")
    tzs: dict[str, Any] = {}
    chosen: dict[tuple, Any] = {}
    order: list[tuple] = []
    stats = {"input": 0, "kept": 0, "duplicates": 0, "timezones": 0}
    content_seen: dict[tuple, tuple] = {}
    for src, cal in cals:
        if name is None and cal.get("x-wr-calname"):
            name = str(cal.get("x-wr-calname"))
        for comp in cal.subcomponents:
            if comp.name == "VTIMEZONE":
                tzid = str(comp.get("tzid"))
                tzs.setdefault(tzid, comp)
                continue
            stats["input"] += 1
            uid = str(comp.get("uid") or "")
            rid = comp.get("recurrence-id")
            rid_s = rid.to_ical().decode() if rid is not None else ""
            key: tuple = (comp.name, uid, rid_s) if uid and dedupe != "none" else (comp.name, f"#{src}#{stats['input']}", "")
            if dedupe == "content" and not rid_s:
                ck = _content_key(comp)
                if ck in content_seen and content_seen[ck] != key:
                    key = content_seen[ck]
                else:
                    content_seen[ck] = key
            if key in chosen:
                stats["duplicates"] += 1
                if _rank(comp) > _rank(chosen[key]):
                    chosen[key] = comp
            else:
                chosen[key] = comp
                order.append(key)
    if name:
        out.add("x-wr-calname", name)
    for tz in tzs.values():
        out.add_component(tz)
    for key in order:
        out.add_component(chosen[key])
    stats["kept"] = len(order)
    stats["timezones"] = len(tzs)
    add_timezones(out)
    return out, stats


# ── edits ───────────────────────────────────────────────────────────────


def _targets(cal: Any, op: dict[str, Any], items_by_i: dict[int, tuple[str, str]]) -> list[Any]:
    uid = op.get("uid")
    if uid is None and op.get("item") is not None:
        ref = items_by_i.get(int(op["item"]))
        if ref is None:
            raise UsageError(f"no item #{op['item']}")
        uid = ref[0]
    if uid is None and op.get("summary_match"):
        rx = re.compile(op["summary_match"], re.I)
        return [c for c in cal.subcomponents if c.name in ("VEVENT", "VTODO") and rx.search(str(c.get("summary") or ""))]
    if uid is None:
        raise UsageError(f"operation {op.get('op')!r} needs uid, item or summary_match")
    comps = [c for c in cal.subcomponents if c.name in ("VEVENT", "VTODO") and str(c.get("uid") or "") == str(uid)]
    if op.get("op") not in ("delete",):
        masters = [c for c in comps if c.get("recurrence-id") is None]
        return masters or comps
    return comps


def _touch(comp: Any) -> None:
    seq = int(comp.get("sequence", 0) or 0) + 1
    for key in ("sequence", "dtstamp", "last-modified"):
        if key in comp:
            del comp[key]
    comp.add("sequence", seq)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    comp.add("dtstamp", now)
    comp.add("last-modified", now)


def apply_ops(cal: Any, ops: list[dict[str, Any]], default_tz: Any, items_by_i: dict[int, tuple[str, str]]) -> list[str]:
    """Applies edit operations in order; returns one report line per operation. Unmatched ops raise unless optional."""
    report = []
    for n, op in enumerate(ops, 1):
        kind = op.get("op")
        targets = _targets(cal, op, items_by_i)
        if not targets:
            if op.get("optional"):
                report.append(f"{n}. {kind}: no match (optional)")
                continue
            raise SkillError(f"operation {n} ({kind}) matches no event; nothing was written")
        for comp in targets:
            if kind == "set":
                field = str(op["field"]).lower()
                if field in ("dtstart", "dtend", "start", "end", "rrule", "uid"):
                    raise UsageError(f"use move/shift/set_rrule for {field}")
                if field in comp:
                    del comp[field]
                if op.get("value") not in (None, ""):
                    comp.add(field, op["value"] if field != "status" else str(op["value"]).upper())
            elif kind in ("move", "shift"):
                s = comp.get("dtstart").dt
                e = comp.get("dtend").dt if comp.get("dtend") is not None else None
                dur = (e - s) if e is not None else None
                if kind == "shift":
                    by = parse_duration(str(op["by"]))
                    ns = s + by
                    ne = e + by if e is not None else None
                else:
                    tz = get_zone(op["tz"]) if op.get("tz") else (s.tzinfo if isinstance(s, datetime) and s.tzinfo else default_tz)
                    ns = _when(op["start"], tz, not isinstance(s, datetime))
                    ne = _when(op["end"], tz, not isinstance(s, datetime)) if op.get("end") else (ns + dur if dur is not None else None)
                del comp["dtstart"]
                comp.add("dtstart", ns)
                if "dtend" in comp:
                    del comp["dtend"]
                if ne is not None and "duration" not in comp:
                    comp.add("dtend", ne)
            elif kind == "cancel":
                if op.get("date") or op.get("at"):
                    s = comp.get("dtstart").dt
                    when = str(op.get("at") or op.get("date"))
                    if isinstance(s, datetime):
                        d = _when(when, s.tzinfo or default_tz, False)
                        if not isinstance(d, datetime):
                            d = datetime.combine(d, s.timetz() if s.tzinfo else s.time())
                            d = d if d.tzinfo or not s.tzinfo else d.replace(tzinfo=s.tzinfo)
                    else:
                        d = _when(when, None, True)
                    comp.add("exdate", [d])
                else:
                    if "status" in comp:
                        del comp["status"]
                    comp.add("status", "CANCELLED")
            elif kind == "delete":
                cal.subcomponents.remove(comp)
                continue
            elif kind == "add_attendee":
                comp.add("attendee", _cal_address({k: v for k, v in op.items() if k in ("email", "name", "role", "partstat", "rsvp")}, "REQ-PARTICIPANT"))
            elif kind == "remove_attendee":
                email_l = str(op["email"]).lower()
                atts = comp.get("attendee")
                atts = atts if isinstance(atts, list) else ([atts] if atts is not None else [])
                keep = [x for x in atts if str(x).lower().replace("mailto:", "") != email_l]
                if len(keep) == len(atts):
                    raise SkillError(f"{op['email']} is not an attendee of {comp.get('summary')}")
                del comp["attendee"]
                for x in keep:
                    comp.add("attendee", x)
            elif kind == "set_rrule":
                from icalendar import vRecur

                if "rrule" in comp:
                    del comp["rrule"]
                if op.get("rrule"):
                    comp.add("rrule", vRecur.from_ical(op["rrule"]) if isinstance(op["rrule"], str) else vRecur({k.upper(): v for k, v in op["rrule"].items()}))
            elif kind == "end_series":
                from icalendar import vRecur

                rr = comp.get("rrule")
                if rr is None:
                    raise SkillError(f"{comp.get('summary')} does not repeat")
                rule = dict(rr)
                rule.pop("COUNT", None)
                s = comp.get("dtstart").dt
                u = _when(op["until"], s.tzinfo if isinstance(s, datetime) else None, not isinstance(s, datetime))
                if isinstance(s, datetime) and not isinstance(u, datetime):
                    u = datetime.combine(u, time(23, 59, 59), tzinfo=s.tzinfo)
                if isinstance(u, datetime) and u.tzinfo is not None:
                    u = u.astimezone(timezone.utc)
                rule["UNTIL"] = [u]
                del comp["rrule"]
                comp.add("rrule", vRecur(rule))
            elif kind == "add_alarm":
                from icalendar import Alarm

                al = Alarm()
                al.add("action", "DISPLAY")
                al.add("trigger", -parse_duration(str(op.get("before", "15m")).lstrip("-")))
                al.add("description", str(comp.get("summary") or "Reminder"))
                comp.add_component(al)
            elif kind == "remove_alarms":
                comp.subcomponents = [s for s in comp.subcomponents if s.name != "VALARM"]
            else:
                raise UsageError(f"unknown operation {kind!r} (set, move, shift, cancel, delete, add_attendee, remove_attendee, set_rrule, end_series, add_alarm, remove_alarms)")
            _touch(comp)
        report.append(f"{n}. {kind}: {len(targets)} item(s) ({', '.join(str(t.get('summary') or t.get('uid')) for t in targets[:3])})")
    add_timezones(cal)
    return report


def make_reply(cal: Any, attendee: str, status: str, comment: str | None = None, uid: str | None = None) -> Any:
    """A METHOD:REPLY calendar answering an invitation for one attendee (ACCEPTED, DECLINED, TENTATIVE)."""
    from icalendar import Event

    status = {"accept": "ACCEPTED", "accepted": "ACCEPTED", "yes": "ACCEPTED", "decline": "DECLINED", "declined": "DECLINED", "no": "DECLINED", "tentative": "TENTATIVE", "maybe": "TENTATIVE"}.get(status.lower(), status.upper())
    events = [c for c in cal.walk("VEVENT") if uid is None or str(c.get("uid")) == uid]
    if not events:
        raise SkillError("the invitation holds no event" + (f" with UID {uid}" if uid else ""))
    ev = events[0]
    rep = Event()
    for key in ("uid", "sequence", "recurrence-id", "dtstart", "dtend", "duration", "summary", "organizer", "location"):
        if ev.get(key) is not None:
            rep.add(key, ev.get(key))
    rep.add("dtstamp", datetime.now(timezone.utc).replace(microsecond=0))
    me = attendee.lower().replace("mailto:", "")
    orig = ev.get("attendee")
    orig = orig if isinstance(orig, list) else ([orig] if orig is not None else [])
    match = next((a for a in orig if str(a).lower().replace("mailto:", "") == me), None)
    ca = _cal_address({"email": attendee, "name": str(match.params.get("CN")) if match is not None and match.params.get("CN") else None, "partstat": status, "rsvp": False})
    ca.params["PARTSTAT"] = status
    rep.add("attendee", ca)
    if comment:
        rep.add("comment", comment)
    out = make_calendar([rep], method="REPLY")
    return out, status, match is not None
