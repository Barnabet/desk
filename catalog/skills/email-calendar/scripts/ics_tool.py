#!/usr/bin/env python3
"""Calendars (.ics .ical .ifb, and invites saved from email): read events and tasks, expand recurrences
(RRULE with EXDATE, RDATE and RECURRENCE-ID exceptions) into an agenda in any time zone, find conflicts and free
slots across calendars, create events and invitations, merge and dedupe, edit, answer invitations, export
CSV/JSON/Markdown, and draw week or month views as PNG.

commands:
  read       the calendar's items (a map first for big calendars); --find to search, --full for every field
  agenda     occurrences between --from and --to, recurrences expanded, in --tz
  conflicts  overlapping busy events across one or more calendars
  free       free slots within working hours across calendars (and .ifb free/busy files)
  create     a new .ics from options or a JSON spec (time zones, attendees, alarms, repeats)
  merge      several calendars into one, duplicates removed
  edit       change events by UID or item number with JSON operations
  reply      accept / decline / tentatively accept an invitation (METHOD:REPLY .ics)
  render     week or month view PNGs to look at
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, emit, input_file, load_json_arg, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/ics_tool.py read calendar.ics
  python3 scripts/ics_tool.py read calendar.ics --find "dentist|doctor" --full
  python3 scripts/ics_tool.py agenda work.ics home.ics --from 2026-10-01 --to 2026-11-01 --tz Europe/Paris
  python3 scripts/ics_tool.py conflicts work.ics home.ics --from today --to +30d
  python3 scripts/ics_tool.py free work.ics ana.ics --from 2026-10-05 --to 2026-10-10 --hours 09:00-17:30 --min 45m --tz Europe/Paris
  python3 scripts/ics_tool.py create --out standup.ics --summary "Stand-up" --start 2026-10-05T09:30 --duration 15m \\
      --tz Europe/Paris --rrule "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" --attendee "Bob <bob@example.org>" --alarm 10m --method REQUEST
  python3 scripts/ics_tool.py create --out trip.ics --spec events.json
  python3 scripts/ics_tool.py merge a.ics b.ics --out all.ics --dedupe content
  python3 scripts/ics_tool.py edit team.ics --out team-v2.ics --ops '[{"op":"cancel","uid":"x@y","date":"2026-10-12"}]'
  python3 scripts/ics_tool.py reply invite.ics --attendee me@example.com --status accepted --out reply.ics
  python3 scripts/ics_tool.py render work.ics --from 2026-10-05 --view week --out-dir views/
"""


def _add_inputs(sp, many: bool = True) -> None:
    sp.add_argument("files", nargs="+" if many else 1, help="calendar files (.ics .ical .ifb), or emails (.eml .msg) holding an invite")
    sp.add_argument("--tz", help="show times in this zone (IANA like Europe/Paris, 'local', or +02:00); default: each event's own zone for read, local for the rest")
    sp.add_argument("--no-cache", action="store_true")


def _add_window(sp, default_days: int = 30) -> None:
    sp.add_argument("--from", dest="from_", default="today", help="window start: 2026-10-01, 2026-10-01T09:00, today, -7d (default today)")
    sp.add_argument("--to", help=f"window end (exclusive): a date, +14d … (default --from + {default_days} days)")
    sp.add_argument("--days", type=int, help="window length in days (instead of --to)")


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sp = sub.add_parser("read", help="list the calendar's items")
    _add_inputs(sp)
    sp.add_argument("--find", metavar="RE", help="only items whose summary, description, location, organizer, attendees or UID match")
    sp.add_argument("--type", choices=["event", "todo", "journal", "freebusy", "all"], default="all")
    sp.add_argument("--full", action="store_true", help="every field of each item")
    sp.add_argument("--range", help="item numbers like 1-100 (paging)")
    sp.add_argument("--map", action="store_true", help="the summary map even for a small calendar")
    sp.add_argument("--format", choices=["auto", "md", "csv", "json"], default="auto")
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--offset", type=int, default=0, help="with --full: start the text at this character (the next-part command sets it)")

    sp = sub.add_parser("agenda", help="expanded occurrences in a window")
    _add_inputs(sp)
    _add_window(sp)
    sp.add_argument("--find", metavar="RE")
    sp.add_argument("--include-cancelled", action="store_true")
    sp.add_argument("--todos", action="store_true", help="include tasks with a start or due date")
    sp.add_argument("--out", help="write the agenda to a file (.csv, .json or .md)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "csv", "json"], default="md")
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--offset", type=int, default=0, help="start the text at this character (the next-part command sets it)")

    sp = sub.add_parser("conflicts", help="overlapping busy events")
    _add_inputs(sp)
    _add_window(sp)
    sp.add_argument("--all-day-busy", action="store_true", help="count all-day events as busy (default: they do not block)")
    sp.add_argument("--include-free", action="store_true", help="count events marked free/transparent too")
    sp.add_argument("--format", choices=["md", "json"], default="md")
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--offset", type=int, default=0, help="start the text at this character (the next-part command sets it)")

    sp = sub.add_parser("free", help="free slots within working hours")
    _add_inputs(sp)
    _add_window(sp, 7)
    sp.add_argument("--hours", default="09:00-17:00", help="working hours per day (default 09:00-17:00)")
    sp.add_argument("--weekdays", default="mon-fri", help="working days like mon-fri or mon,wed,fri (default mon-fri)")
    sp.add_argument("--min", default="30m", help="shortest useful slot (default 30m)")
    sp.add_argument("--buffer", default="0m", help="keep this much free before and after each event")
    sp.add_argument("--all-day-busy", action="store_true")
    sp.add_argument("--include-free", action="store_true", help="events marked free/transparent block time too")
    sp.add_argument("--first", type=int, help="only the first N slots")
    sp.add_argument("--format", choices=["md", "json"], default="md")
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--offset", type=int, default=0, help="start the text at this character (the next-part command sets it)")

    sp = sub.add_parser("create", help="write a new .ics")
    sp.add_argument("--out", required=True)
    sp.add_argument("--spec", help="JSON: one event, a list, or {calendar: {name, method, tz}, events: [...]} (inline, file or -)")
    sp.add_argument("--summary")
    sp.add_argument("--start", help="2026-10-05T09:30 (in --tz) or 2026-10-05 for all-day")
    sp.add_argument("--end")
    sp.add_argument("--duration", help="like 45m, 1h30m, 2d")
    sp.add_argument("--all-day", action="store_true")
    sp.add_argument("--tz", help="zone of the times (default: local)")
    sp.add_argument("--location")
    sp.add_argument("--description")
    sp.add_argument("--url")
    sp.add_argument("--organizer", help='"Name <addr>"')
    sp.add_argument("--attendee", action="append", default=[], help='"Name <addr>" (repeatable); append ";optional" for optional')
    sp.add_argument("--rrule", help='repeat rule like "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"')
    sp.add_argument("--exdate", action="append", default=[], help="skip this occurrence (repeatable)")
    sp.add_argument("--alarm", action="append", default=[], help="reminder before the start, like 15m (repeatable)")
    sp.add_argument("--status", choices=["TENTATIVE", "CONFIRMED", "CANCELLED"])
    sp.add_argument("--busy", choices=["busy", "free"], help="show as busy (OPAQUE) or free (TRANSPARENT)")
    sp.add_argument("--categories")
    sp.add_argument("--uid")
    sp.add_argument("--name", help="calendar name (X-WR-CALNAME)")
    sp.add_argument("--method", choices=["PUBLISH", "REQUEST", "CANCEL", "REPLY"], help="REQUEST makes it an invitation (for mail_create.py --calendar)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("merge", help="combine calendars")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--out", required=True)
    sp.add_argument("--dedupe", choices=["uid", "content", "none"], default="uid", help="uid (default): newest version per UID; content: also same title+start+end")
    sp.add_argument("--name", help="calendar name of the result")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("edit", help="change events with JSON operations")
    sp.add_argument("file")
    sp.add_argument("--out", required=True)
    sp.add_argument("--ops", required=True, help="JSON list of operations (inline, file or -); see references/calendar.md")
    sp.add_argument("--tz", help="zone for times given without one (default: the event's own)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("reply", help="answer an invitation")
    sp.add_argument("file", help="the invitation (.ics, or an .eml/.msg carrying it)")
    sp.add_argument("--attendee", required=True, help="your address as invited")
    sp.add_argument("--status", required=True, help="accepted, declined or tentative")
    sp.add_argument("--comment")
    sp.add_argument("--uid", help="which event, when the file holds several")
    sp.add_argument("--out", required=True)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("render", help="week or month view PNGs")
    _add_inputs(sp)
    _add_window(sp, 7)
    sp.add_argument("--view", choices=["week", "month"], default="week", help="week: whole weeks from the Monday of --from (default --to: one week); month: the whole month of --from (to the month of --to when given)")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--all-day-busy", action="store_true")
    sp.add_argument("--force", action="store_true")

    a = p.parse_args()
    return {"read": cmd_read, "agenda": cmd_agenda, "conflicts": cmd_conflicts, "free": cmd_free, "create": cmd_create, "merge": cmd_merge, "edit": cmd_edit, "reply": cmd_reply, "render": cmd_render}[a.cmd](a)


# ── loading ─────────────────────────────────────────────────────────────


def _calendar_bytes_from(path: Path) -> list[bytes]:
    """Calendar parts of an email, or the file itself."""
    from _ics import ICS_EXTS

    if path.suffix.lower() in ICS_EXTS:
        return [path.read_bytes()]
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if b"BEGIN:VCALENDAR" in head.upper():
        return [path.read_bytes()]
    from _mail import iter_attachments, load_mail

    mail = load_mail(path)
    parts = [att["_data"] for _, att in iter_attachments(mail) if att.get("kind") == "calendar" and att.get("_data")]
    if not parts:
        raise SkillError(f"{path.name} holds no calendar (.ics) part")
    return parts


def load(files: list[str], use_cache: bool = True):
    from _ics import ICS_EXTS, load_items, parse_calendars, read_calendar_bytes

    paths = []
    extra_items: list[dict] = []
    extra_vtz: dict[str, str] = {}
    extra_infos: list[dict] = []
    for f in files:
        pth = input_file(f)
        with open(pth, "rb") as fh:
            head = fh.read(4096)  # not read_bytes(): the input may be a huge mailbox
        if pth.suffix.lower() in ICS_EXTS or b"BEGIN:VCALENDAR" in head.upper():
            paths.append(pth)
        else:
            for data in _calendar_bytes_from(pth):
                parsed = parse_calendars(read_calendar_bytes(data), pth.name)
                extra_vtz.update(parsed["vtimezones"])
                extra_infos += [{**c, "source": pth.name} for c in parsed["calendars"]]
                extra_items += parsed["items"]
    items, vtz, infos = load_items(paths, use_cache)
    items += [dict(it) for it in extra_items]
    for i, it in enumerate(items, 1):
        it["i"] = i
    vtz.update({k: v for k, v in extra_vtz.items() if k not in vtz})
    return items, vtz, infos + extra_infos


def _ctx(a, vtz):
    from _ics import Ctx, get_zone, local_zone

    tz = get_zone(a.tz) if getattr(a, "tz", None) else local_zone()
    return Ctx(tz, vtz)


def _window(a, ctx):
    from datetime import timedelta

    from _ics import parse_when

    start = parse_when(a.from_, ctx.display)
    if a.days:
        end = start + timedelta(days=a.days)
    elif a.to:
        end = parse_when(a.to, ctx.display, end_of_day=False)
        if end <= start:
            raise UsageError("--to must be after --from")
    else:
        default = 30 if a.cmd == "agenda" else 7
        end = start + timedelta(days=default)
    return start, end


def _occ_rows(occs, ctx):
    """Occurrence dicts for output: local start/end in the display zone, plus the item's fields."""
    from datetime import datetime, time

    from _ics import to_display

    rows = []
    for o in occs:
        it = o["item"]
        s, e = to_display(o["start"], ctx), to_display(o["end"], ctx)
        rows.append(
            {
                "item": it["i"],
                "start_local": s,
                "end_local": e,
                "all_day": bool(it.get("all_day")),
                "summary": _cut(it.get("summary") or "(no title)", 500),
                "location": _cut(it.get("location") or "", 300),
                "status": it.get("status") or "",
                "transp": it.get("transp") or "",
                "source": it.get("source") or "",
                "uid": it.get("uid") or "",
                "recurring": bool(it.get("rrule") or it.get("rdate")),
                "override": bool(it.get("recurrence_id")),
                "organizer": (it.get("organizer") or {}).get("email", ""),
                "attendees": len(it.get("attendees") or []),
                "type": it.get("type"),
                "floating": not it.get("all_day") and not it.get("tzid") and not it.get("floating_zone"),
            }
        )
    return rows


def _fmt_time(r, full_end: bool = False) -> tuple[str, str]:
    """(start, end) text in the display zone. The end is a bare time when it falls on the start's day, unless
    `full_end` (CSV: always a date and time). All-day items end on their last day (inclusive)."""
    from datetime import timedelta

    s, e = r["start_local"], r["end_local"]
    if r["all_day"]:
        last = e - timedelta(days=1)
        return s.strftime("%Y-%m-%d"), (last.strftime("%Y-%m-%d") if last.date() > s.date() or full_end else "")
    return s.strftime("%Y-%m-%d %H:%M"), (e.strftime("%H:%M") if e.date() == s.date() and not full_end else e.strftime("%Y-%m-%d %H:%M"))


# ── read ────────────────────────────────────────────────────────────────


def _item_row(it, ctx):
    from _ics import describe_rrule, when_text

    s, e = when_text(it, ctx)
    zone = it.get("tzid") or it.get("floating_zone") or ("" if it.get("all_day") or not it.get("start") else "floating")
    rep = "; ".join(describe_rrule(r) for r in it.get("rrule", []))
    if it.get("exdate"):
        k = len(it["exdate"])
        rep += f" ({k} date{'s' if k > 1 else ''} skipped)"
    if it.get("recurrence_id"):
        rep = f"changed occurrence of {it['recurrence_id']['at'][:16]}"
    if it.get("error") or it.get("problems"):
        rep = (rep + "; " if rep else "") + "damaged: " + "; ".join([it["error"]] if it.get("error") else it["problems"])
    return [f"#{it['i']}", it.get("type", ""), _cut(it.get("summary") or "", 500), s, e, zone, rep, _cut(it.get("location") or "", 300), it.get("status") or "", _cut(it.get("uid") or "", 300)]


def cmd_read(a) -> int:
    import re
    from collections import Counter

    from _out import auto_format, script_cmd, table, trunc

    items, vtz, infos = load(a.files, not a.no_cache)
    from _ics import Ctx, get_zone

    ctx = Ctx(get_zone(a.tz) if a.tz else get_zone("UTC"), vtz)
    if a.tz is None:
        ctx.display = get_zone("UTC")
    sel = [it for it in items if a.type == "all" or it.get("type") == a.type]
    if a.find:
        rx = re.compile(a.find, re.I)

        def fields(it):
            parts = [it.get(k) or "" for k in ("summary", "description", "location", "uid")] + list(it.get("categories") or [])
            parts.append((it.get("organizer") or {}).get("email", ""))
            parts += [x.get("email", "") for x in it.get("attendees") or []] + [x.get("name", "") for x in it.get("attendees") or []]
            return [str(p) for p in parts if p]

        sel = [it for it in sel if any(rx.search(f) for f in fields(it))]
    big = len(sel) > 150 and not a.find and not a.range and not a.full
    if a.map or big:
        years: Counter[str] = Counter()
        types: Counter[str] = Counter(it.get("type") for it in sel)
        locs: Counter[str] = Counter(it.get("location") for it in sel if it.get("location"))
        orgs: Counter[str] = Counter((it.get("organizer") or {}).get("email") for it in sel if it.get("organizer"))
        zones: Counter[str] = Counter(it.get("tzid") or ("all-day" if it.get("all_day") else "floating") for it in sel if it.get("start"))
        starts = sorted(it["start"][:10] for it in sel if it.get("start"))
        for s in starts:
            years[s[:4]] += 1
        rec = sum(1 for it in sel if it.get("rrule"))
        data = {"items": len(sel), "types": dict(types), "recurring": rec, "overrides": sum(1 for it in sel if it.get("recurrence_id")), "first": starts[0] if starts else None, "last": starts[-1] if starts else None, "per_year": dict(sorted(years.items())), "zones": zones.most_common(8), "top_locations": locs.most_common(8), "top_organizers": orgs.most_common(8), "calendars": infos}
        if a.format == "json":
            emit(data, "json", max_chars=None)
            return 0
        names = ", ".join(sorted({c.get("name") or c["source"] for c in infos}))
        out = [f"# {names} — {len(sel):,} items (map)", ""]
        out.append("- **Types:** " + ", ".join(f"{k} {v:,}" for k, v in types.items()))
        out.append(f"- **Span:** {data['first']} → {data['last']}; {rec:,} repeating series, {data['overrides']:,} changed occurrences")
        out.append("- **Per year (by first start):** " + ", ".join(f"{k}: {v:,}" for k, v in data["per_year"].items()))
        out.append("- **Time zones:** " + ", ".join(f"{k} ({v})" for k, v in data["zones"]))
        if locs:
            out.append("- **Top locations:** " + ", ".join(f"{k} ({v})" for k, v in data["top_locations"]))
        if orgs:
            out.append("- **Top organizers:** " + ", ".join(f"{k} ({v})" for k, v in data["top_organizers"]))
        out += ["", f"Next: `agenda --from … --to …` for what happens in a window, `read --find RE` to search, `read --range 1-100` to page (items #1-#{len(sel)})."]
        print("\n".join(out))
        return 0
    if a.range:
        from _common import parse_ranges

        want = set(parse_ranges(a.range, len(items)))
        sel = [it for it in sel if it["i"] in want]
    if a.full or a.format == "json":
        if a.format == "json":
            emit({"calendars": infos, "items": sel}, "json", max_chars=None)
            return 0
        out = []
        for it in sel:
            row = _item_row(it, ctx)
            out.append(f"## #{it['i']} {it.get('type')}: {_cut(it.get('summary') or '(no title)', 300)}")
            shows = ("free (transparent)" if it.get("transp") == "TRANSPARENT" else "busy") if it.get("type") == "event" else ""
            for label, v in (("Start", row[3]), ("End", row[4]), ("Zone", row[5]), ("Repeats", row[6].split("damaged: ")[0].rstrip("; ")), ("Location", row[7]), ("Status", row[8]), ("Shows as", shows), ("UID", row[9]), ("Organizer", (it.get("organizer") or {}).get("email")), ("Sequence", it.get("sequence")), ("Categories", ", ".join(it.get("categories") or [])), ("URL", it.get("url")), ("Due", it.get("due"))):
                if v:
                    out.append(f"- **{label}:** {v}")
            if it.get("exdate"):
                out.append(f"- **Except:** {', '.join(x[:16] for x in it['exdate'][:20])}")
            if it.get("attendees"):
                out.append("- **Attendees:** " + "; ".join(f"{x.get('name', '')} <{x['email']}> {x.get('role', '').lower()} {x.get('partstat', '').lower()}".strip() for x in it["attendees"][:40]))
            if it.get("alarms"):
                out.append("- **Alarms:** " + ", ".join(f"{(x.get('action') or 'DISPLAY').lower()} {x.get('trigger') or ''}".strip() for x in it["alarms"]))
            if it.get("freebusy"):
                out.append(f"- **Busy periods:** {len(it['freebusy'])}: " + ", ".join(f"{p[0][:16]}→{p[1][11:16]}" for p in it["freebusy"][:10]))
            for prob in ([it["error"]] if it.get("error") else []) + (it.get("problems") or []):
                out.append(f"- **Damaged:** {prob}")
            if it.get("description"):
                out.append("\n" + it["description"].strip()[:3000])
            out.append("")
        from _out import page_text

        print(page_text("\n".join(out), a.offset, a.max_chars))
        return 0
    fmt = auto_format(a.format, len(sel))
    headers = ["#", "type", "summary", "start", "end", "zone", "repeats", "location", "status", "uid"]
    rows = [_item_row(it, ctx) for it in sel]
    if fmt == "md":
        rows = [[r[0], r[1], trunc(r[2], 60), r[3], r[4], r[5], trunc(r[6], 50), trunc(r[7], 40), r[8], trunc(r[9], 30)] for r in rows]
    names = ", ".join(sorted({c.get("name") or c["source"] for c in infos}))
    print(f"{names}: {len(sel)} item(s)" + (f" matching {a.find!r}" if a.find else "") + ". Times are in each event's own zone.")
    text = table(headers, rows, fmt)
    if len(text) > a.max_chars:
        cut = text.rfind("\n", 0, a.max_chars)
        shown = text[:cut].count("\n") - (1 if fmt == "md" else 0)
        last = sel[max(0, shown - 1)]["i"] if sel else 0
        text = text[:cut] + f"\n[… {len(sel) - shown} more items. Next: {script_cmd({'--range': f'{last + 1}-'})}]"
    print(text.rstrip())
    return 0


# ── agenda ──────────────────────────────────────────────────────────────


def _cut(s: Any, n: int) -> str:
    """A long cell cut with a marker that says how long it was (the original length when cut twice)."""
    import re

    s = "" if s is None else str(s)
    if len(s) <= n:
        return s
    m = re.search(r"… \[([\d,]+) characters\]$", s)
    total = m.group(1) if m else f"{len(s):,}"
    return s[:n].rstrip() + f"… [{total} characters]"


def _print_notes(notes: list[str]) -> None:
    for n in dict.fromkeys(notes):
        print(f"warning: {n}", file=sys.stderr)


def _iso_row(r) -> dict:
    """One occurrence for JSON: ISO 8601 start and end with their UTC offset (dates for all-day items, where `end`
    is exclusive as in iCalendar and `last_day` inclusive)."""
    from datetime import datetime, timedelta

    s, e = r["start_local"], r["end_local"]
    if r["all_day"]:
        sd = s.date() if isinstance(s, datetime) else s
        ed = e.date() if isinstance(e, datetime) else e
        times = {"start": sd.isoformat(), "end": ed.isoformat(), "last_day": max(sd, ed - timedelta(days=1)).isoformat()}
    else:
        times = {"start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes")}
    return {"date": s.strftime("%Y-%m-%d"), **times, "all_day": r["all_day"], "floating": r["floating"], "summary": r["summary"], "location": r["location"], "status": r["status"], "busy": r["transp"] != "TRANSPARENT", "calendar": r["source"], "item": r["item"], "uid": r["uid"], "recurring": r["recurring"], "changed": r["override"]}


def cmd_agenda(a) -> int:
    import re

    from _ics import expand
    from _out import dump_json, page_text, table

    items, vtz, infos = load(a.files, not a.no_cache)
    ctx = _ctx(a, vtz)
    start, end = _window(a, ctx)
    types = ("event", "todo") if a.todos else ("event",)
    notes: list[str] = []
    occs = expand(items, ctx, start, end, types, a.include_cancelled, notes)
    rows = _occ_rows(occs, ctx)
    if a.find:
        rx = re.compile(a.find, re.I)
        rows = [r for r in rows if any(rx.search(str(r[k] or "")) for k in ("summary", "location", "uid"))]
    zname = getattr(ctx.display, "key", None) or str(ctx.display)
    headers = ["date", "start", "end", "all_day", "summary", "location", "status", "calendar", "item", "uid", "recurring"]

    def flat(r):
        s, e = _fmt_time(r, full_end=True)
        return [r["start_local"].strftime("%Y-%m-%d"), s, e, r["all_day"], r["summary"], r["location"], r["status"], r["source"], r["item"], r["uid"], r["recurring"]]

    _print_notes(notes)
    if a.out:
        out = output_path(a.out, [Path(f) for f in a.files], a.force)
        ext = out.suffix.lower()
        if ext == ".json":
            out.write_text(dump_json({"tz": zname, "from": start.isoformat(), "to": end.isoformat(), "occurrences": [_iso_row(r) for r in rows], "notes": notes}), encoding="utf-8")
        elif ext == ".csv":
            out.write_text(table(headers, [flat(r) for r in rows], "csv"), encoding="utf-8")
        else:
            out.write_text(_agenda_md(rows, zname, start, end, notes), encoding="utf-8")
        print(f"Wrote {len(rows)} occurrence(s) from {start.date()} to {(end - _second()).date()} ({zname}) to {out}." + (f" {len(notes)} warning(s) above." if notes else ""))
        return 0
    if a.format == "json":
        emit({"tz": zname, "from": start.isoformat(), "to": end.isoformat(), "notes": notes, "occurrences": [_iso_row(r) for r in rows]}, "json", max_chars=None)
        return 0
    if a.format == "csv":
        print(page_text(table(headers, [flat(r) for r in rows], "csv"), a.offset, a.max_chars, header_lines=1).rstrip())
        return 0
    text = _agenda_md(rows, zname, start, end, notes)
    if not rows:
        spans = sorted(it["start"][:10] for it in items if it.get("start"))
        if spans:
            text += f"\nThe calendar's items start between {spans[0]} and {spans[-1]}; widen --from/--to."
    print(page_text(text, a.offset, a.max_chars))
    return 0


def _second():
    from datetime import timedelta

    return timedelta(seconds=1)


def _agenda_md(rows, zname, start, end, notes=()) -> str:
    out = [f"# Agenda {start.strftime('%Y-%m-%d')} → {(end - _second()).strftime('%Y-%m-%d')} ({zname}): {len(rows)} occurrence(s)", ""]
    for n in notes:
        out.append(f"> warning: {n}")
    day = None
    for r in rows:
        d = r["start_local"].strftime("%a %Y-%m-%d")
        if d != day:
            out += ["", f"## {d}"]
            day = d
        s, e = _fmt_time(r)
        when = "all day" + (f" until {e}" if e else "") if r["all_day"] else f"{s[11:]}–{e}"
        flags = []
        if r["recurring"]:
            flags.append("repeats")
        if r["override"]:
            flags.append("moved/changed")
        if r["status"] in ("TENTATIVE", "CANCELLED"):
            flags.append(r["status"].lower())
        if r["transp"] == "TRANSPARENT":
            flags.append("free")
        if r["floating"]:
            flags.append("floating time")
        extra = f" · {_cut(r['location'], 120)}" if r["location"] else ""
        out.append(f"- {when} **{_cut(r['summary'], 200)}**{extra} ({r['source']} #{r['item']}{', ' + ', '.join(flags) if flags else ''})")
    return "\n".join(out).replace("\n\n\n", "\n\n") + "\n"


# ── busy / free ─────────────────────────────────────────────────────────


def _busy(a, items, ctx, start, end, notes=None):
    from datetime import datetime, timedelta

    from _ics import aware, expand

    occs = expand(items, ctx, start, end, ("event",), notes=notes)
    rows = _occ_rows(occs, ctx)
    busy = []
    for o, r in zip(occs, rows):
        it = o["item"]
        if r["all_day"] and not getattr(a, "all_day_busy", False):
            continue
        if it.get("transp") == "TRANSPARENT" and not getattr(a, "include_free", False):
            continue
        busy.append({"s": aware(o["start"], ctx), "e": aware(o["end"], ctx), "row": r})
    # VFREEBUSY periods
    for it in items:
        for p in it.get("freebusy") or []:
            if p[2] == "FREE":
                continue
            s, e = datetime.fromisoformat(p[0]), datetime.fromisoformat(p[1])
            s, e = aware(s, ctx), aware(e, ctx)
            if s < end and e > start:
                busy.append({"s": s, "e": e, "row": {"summary": f"busy ({p[2].lower()})", "source": it.get("source"), "item": it["i"], "location": "", "all_day": False, "start_local": s.astimezone(ctx.display), "end_local": e.astimezone(ctx.display), "status": "", "uid": it.get("uid", "")}})
    busy.sort(key=lambda b: b["s"])
    return busy


def cmd_conflicts(a) -> int:
    items, vtz, infos = load(a.files, not a.no_cache)
    ctx = _ctx(a, vtz)
    start, end = _window(a, ctx)
    notes: list[str] = []
    busy = _busy(a, items, ctx, start, end, notes)
    _print_notes(notes)
    pairs = []
    active: list[dict] = []
    for b in busy:
        active = [x for x in active if x["e"] > b["s"]]
        for x in active:
            ov = (min(x["e"], b["e"]) - b["s"]).total_seconds() / 60
            if ov > 0:
                pairs.append((x, b, ov))
        active.append(b)
    zname = getattr(ctx.display, "key", None) or str(ctx.display)
    if a.format == "json":
        emit({"tz": zname, "from": start.isoformat(), "to": end.isoformat(), "notes": notes, "busy_events": len(busy), "conflicts": [{"a": _brief(x), "b": _brief(y), "overlap_minutes": round(m)} for x, y, m in pairs]}, "json", max_chars=None)
        return 0
    from collections import Counter

    from _out import page_text

    lines = [f"# Conflicts {start.date()} → {(end - _second()).date()} ({zname}): {len(pairs)} overlap(s) among {len(busy)} busy event(s)", ""]
    lines += [f"> warning: {n}" for n in notes] + ([""] if notes else [])
    if len(pairs) > 20:
        per_day = Counter(y["s"].astimezone(ctx.display).strftime("%a %Y-%m-%d") for _, y, _ in pairs)
        lines += ["Busiest days: " + ", ".join(f"{d} ({n})" for d, n in per_day.most_common(8)), ""]
    for x, y, m in pairs:
        lines.append(f"- **{x['s'].astimezone(ctx.display).strftime('%a %Y-%m-%d %H:%M')}** {_label(x, ctx)} ⟷ {_label(y, ctx)} — overlap {int(round(m))} min")
    if not pairs:
        lines.append("No overlapping busy events." + ("" if a.all_day_busy else " (All-day events do not count; add --all-day-busy to include them.)"))
    print(page_text("\n".join(lines), a.offset, a.max_chars))
    return 0


def _brief(b):
    r = b["row"]
    return {"summary": r["summary"], "start": b["s"].isoformat(), "end": b["e"].isoformat(), "calendar": r["source"], "item": r["item"], "uid": r.get("uid")}


def _label(b, ctx):
    r = b["row"]
    return f"{_cut(r['summary'], 120)} {b['s'].astimezone(ctx.display).strftime('%H:%M')}–{b['e'].astimezone(ctx.display).strftime('%H:%M')} ({r['source']} #{r['item']})"


def cmd_free(a) -> int:
    import re
    from datetime import datetime, time, timedelta

    from _ics import parse_duration

    items, vtz, infos = load(a.files, not a.no_cache)
    ctx = _ctx(a, vtz)
    start, end = _window(a, ctx)
    m = re.fullmatch(r"(\d{1,2})(?::(\d\d))?\s*-\s*(\d{1,2})(?::(\d\d))?", a.hours.strip())
    if not m:
        raise UsageError("--hours like 09:00-17:30")
    h_start = time(int(m.group(1)), int(m.group(2) or 0))
    h_end = time(int(m.group(3)) % 24, int(m.group(4) or 0)) if int(m.group(3)) < 24 else time(23, 59, 59)
    days = _weekdays(a.weekdays)
    min_len = parse_duration(a.min) or timedelta(minutes=30)
    buf = parse_duration(a.buffer) or timedelta(0)
    notes: list[str] = []
    busy = _busy(a, items, ctx, start - timedelta(days=1), end + timedelta(days=1), notes)
    _print_notes(notes)
    intervals = [((b["s"] - buf).astimezone(ctx.display), (b["e"] + buf).astimezone(ctx.display)) for b in busy]
    slots = []
    d = start.astimezone(ctx.display).date()
    last = (end - timedelta(seconds=1)).astimezone(ctx.display).date()
    while d <= last:
        if d.weekday() in days:
            ws = max(datetime.combine(d, h_start, tzinfo=ctx.display), start)
            we = min(datetime.combine(d, h_end, tzinfo=ctx.display), end)
            cur = ws
            for s, e in sorted(i for i in intervals if i[1] > ws and i[0] < we):
                if s > cur and s - cur >= min_len:
                    slots.append((cur, min(s, we)))
                cur = max(cur, e)
            if we > cur and we - cur >= min_len:
                slots.append((cur, we))
        d += timedelta(days=1)
    if a.first:
        slots = slots[: a.first]
    zname = getattr(ctx.display, "key", None) or str(ctx.display)
    if a.format == "json":
        emit({"tz": zname, "from": start.isoformat(), "to": end.isoformat(), "hours": a.hours, "weekdays": a.weekdays, "min": a.min, "notes": notes, "busy_events": len(busy), "slots": [{"start": s.isoformat(), "end": e.isoformat(), "minutes": int((e - s).total_seconds() // 60)} for s, e in slots]}, "json", max_chars=None)
        return 0
    from _out import page_text

    total = sum((e - s for s, e in slots), timedelta())
    lines = [f"# Free slots {start.date()} → {(end - _second()).date()} ({zname}), {a.hours} on {a.weekdays}, at least {a.min}: {len(slots)} slot(s), {total.total_seconds() / 3600:.1f} h in all", ""]
    lines += [f"> warning: {n} (so some busy time may be missing)" for n in notes] + ([""] if notes else [])
    cur_day = None
    for s, e in slots:
        day = s.strftime("%a %Y-%m-%d")
        if day != cur_day:
            lines += ([] if cur_day is None else [""]) + [f"## {day}"]
            cur_day = day
        mins = int((e - s).total_seconds() // 60)
        lines.append(f"- {s.strftime('%H:%M')}–{e.strftime('%H:%M')} ({mins // 60}h{mins % 60:02d})" if mins >= 60 else f"- {s.strftime('%H:%M')}–{e.strftime('%H:%M')} ({mins} min)")
    if not slots:
        lines.append("No free slot matches; widen --hours, --weekdays or the window, or lower --min.")
    lines += ["", f"(based on {len(busy)} busy event(s) from {', '.join(sorted({Path(f).name for f in a.files}))}; all-day and free/transparent events {'count' if a.all_day_busy else 'do not count'})"]
    print(page_text("\n".join(lines), a.offset, a.max_chars))
    return 0


def _weekdays(spec: str) -> set[int]:
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    out: set[int] = set()
    for part in spec.lower().replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            x, y = part.split("-", 1)
            i, j = names.index(x[:3]), names.index(y[:3])
            out |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in ("all", "every"):
            out |= set(range(7))
        else:
            if part[:3] not in names:
                raise UsageError(f"bad weekday {part!r}")
            out.add(names.index(part[:3]))
    return out


# ── create / merge / edit / reply ───────────────────────────────────────


def _verify(out: Path, damaged_before: Any = 0) -> dict:
    """Reads a written calendar back; returns counts, to prove it parses. Items that were already damaged in the
    input (`damaged_before`: a count, or a function computing it only when needed) are reported, not refused.
    The parse goes through the cache, so reading the new file next is instant."""
    from _cache import cached_json
    from _ics import PARSE_VERSION, parse_calendar_file

    parsed = cached_json(out, "ics-items", {}, PARSE_VERSION, lambda: parse_calendar_file(out))
    bad = [it for it in parsed["items"] if "error" in it]
    if callable(damaged_before):
        damaged_before = damaged_before() if bad else 0
    if len(bad) > damaged_before:
        raise SkillError(f"{out} was written but item #{bad[0]['i']} does not read back: {bad[0]['error']}")
    return {"items": len(parsed["items"]), "timezones": sorted(parsed["vtimezones"]), "damaged": [f"#{it['i']} {it.get('summary') or ''}: {it['error']}" for it in bad]}


def _damaged(cals, source: str) -> int:
    from _ics import parse_calendars

    return sum(1 for it in parse_calendars(cals, source)["items"] if "error" in it)


def cmd_create(a) -> int:
    from _ics import get_zone, local_zone
    from _icsbuild import make_calendar, make_event

    spec_file = [Path(a.spec)] if a.spec and a.spec != "-" and Path(a.spec).expanduser().is_file() else []
    out = output_path(a.out, spec_file, a.force)
    cal_opts: dict = {"name": a.name, "method": a.method, "tz": a.tz}
    specs: list[dict] = []
    if a.spec:
        spec = load_json_arg(a.spec)
        if isinstance(spec, dict) and "events" in spec:
            cal_opts.update({k: v for k, v in (spec.get("calendar") or {}).items() if v})
            specs = list(spec["events"])
        elif isinstance(spec, dict):
            specs = [spec]
        elif isinstance(spec, list):
            specs = spec
        else:
            raise UsageError("--spec: an event object, a list of events, or {calendar, events}")
    if a.summary or a.start:
        attendees = []
        for x in a.attendee:
            addr, _, role = x.partition(";")
            attendees.append({"email": addr.strip(), "role": role.strip() or "required"} if "<" not in addr else {**_parse_addr(addr), "role": role.strip() or "required"})
        specs.append({"summary": a.summary, "start": a.start, "end": a.end, "duration": a.duration, "all_day": a.all_day, "location": a.location, "description": a.description, "url": a.url, "organizer": a.organizer, "attendees": attendees, "rrule": a.rrule, "exdate": a.exdate, "alarms": a.alarm, "status": a.status, "busy": (a.busy == "busy") if a.busy else None, "categories": a.categories, "uid": a.uid})
    if not specs:
        raise UsageError("give --summary/--start (and more) or --spec")
    default_tz = get_zone(cal_opts["tz"]) if cal_opts.get("tz") else local_zone()
    events = []
    for s in specs:
        if not isinstance(s, dict):
            raise UsageError(f"--spec: each event must be an object, got {s!r}")
        s = {k: v for k, v in s.items() if v not in (None, [], "")}
        if s.get("attendees"):
            s["attendees"] = [(_parse_addr(x) if isinstance(x, str) else x) for x in s["attendees"]]
        events.append(make_event(s, default_tz))
    if cal_opts.get("method") == "REQUEST":
        for ev in events:
            if ev.get("organizer") is None:
                raise UsageError("an invitation (METHOD:REQUEST) needs an --organizer")
    cal = make_calendar(events, cal_opts.get("name"), cal_opts.get("method"))
    out.write_bytes(cal.to_ical())
    check = _verify(out)
    info = {"out": str(out), "events": len(events), "method": cal_opts.get("method"), "timezones": check["timezones"], "uids": [str(e.get("uid")) for e in events]}
    if a.format == "json":
        emit(info, "json", max_chars=None)
        return 0
    print(f"Wrote {out}: {len(events)} event(s)" + (f", METHOD:{info['method']}" if info["method"] else "") + (f", time zones {', '.join(check['timezones'])}" if check["timezones"] else "") + ". It reads back cleanly.")
    for e in events[:20]:
        s = e.get("dtstart").dt
        print(f"- {e.get('summary')} — {s.isoformat() if hasattr(s, 'isoformat') else s}" + (f" (repeats: {e.get('rrule').to_ical().decode()})" if e.get("rrule") is not None else ""))
    print(f"Check it: python3 scripts/ics_tool.py agenda {out} --from {_first_day(events)} --days 14  (or render --view week)")
    return 0


def _first_day(events) -> str:
    from datetime import datetime

    d = events[0].get("dtstart").dt
    return (d.date() if isinstance(d, datetime) else d).isoformat()


def _parse_addr(s: str) -> dict:
    import email.utils

    name, addr = email.utils.parseaddr(s)
    return {"name": name, "email": addr or s}


def cmd_merge(a) -> int:
    from _ics import read_calendar_bytes
    from _icsbuild import merge_calendars

    out = output_path(a.out, [Path(f) for f in a.files], a.force)
    cals = []
    for f in a.files:
        pth = input_file(f)
        for data in _calendar_bytes_from(pth):
            for cal in read_calendar_bytes(data):
                cals.append((pth.name, cal))
    merged, stats = merge_calendars(cals, a.dedupe, a.name)
    out.write_bytes(merged.to_ical())
    check = _verify(out, lambda: _damaged([merged], out.name))
    if a.format == "json":
        emit({"out": str(out), **stats, "items_written": check["items"], "damaged_in_input": check["damaged"]}, "json", max_chars=None)
        return 0
    print(f"Merged {len(a.files)} file(s) into {out}: {stats['input']} item(s) in, {stats['kept']} kept, {stats['duplicates']} duplicate(s) dropped (dedupe: {a.dedupe}), {stats['timezones']} time zone(s).")
    for d in check["damaged"]:
        print(f"warning: item {d} (already damaged in the input; copied as it was)")
    return 0


def cmd_edit(a) -> int:
    from _ics import get_zone, local_zone, parse_calendars, read_calendar_bytes
    from _icsbuild import apply_ops

    src = input_file(a.file)
    out = output_path(a.out, [src], a.force)
    ops = load_json_arg(a.ops)
    if isinstance(ops, dict):
        ops = [ops]
    cals = read_calendar_bytes(src.read_bytes())
    if len(cals) != 1:
        raise SkillError("edit works on a file with one VCALENDAR; merge first")
    parsed = parse_calendars(cals, src.name)
    damaged = sum(1 for it in parsed["items"] if "error" in it)
    by_i = {it["i"]: (it.get("uid"), it.get("summary")) for it in parsed["items"]}
    report = apply_ops(cals[0], ops, get_zone(a.tz) if a.tz else local_zone(), by_i)
    out.write_bytes(cals[0].to_ical())
    check = _verify(out, damaged)
    if a.format == "json":
        emit({"out": str(out), "operations": report, "items": check["items"]}, "json", max_chars=None)
        return 0
    print(f"Wrote {out} ({check['items']} item(s)); the input is unchanged.")
    for line in report:
        print(f"- {line}")
    return 0


def cmd_reply(a) -> int:
    from _ics import read_calendar_bytes
    from _icsbuild import make_reply

    src = input_file(a.file)
    out = output_path(a.out, [src], a.force)
    parts = _calendar_bytes_from(src)
    cal = read_calendar_bytes(parts[0])[0]
    reply, status, invited = make_reply(cal, a.attendee, a.status, a.comment, a.uid)
    out.write_bytes(reply.to_ical())
    ev = next(iter(reply.walk("VEVENT")))
    org = str(ev.get("organizer") or "").replace("mailto:", "")
    info = {"out": str(out), "status": status, "uid": str(ev.get("uid")), "organizer": org, "summary": str(ev.get("summary") or ""), "attendee_was_invited": invited}
    if a.format == "json":
        emit(info, "json", max_chars=None)
        return 0
    print(f"Wrote {out}: METHOD:REPLY, {a.attendee} {status} “{info['summary']}” (UID {info['uid']}).")
    if not invited:
        print(f"warning: {a.attendee} is not among the invitation's attendees; the organizer's calendar may ignore the reply.")
    if org:
        print(f"To send it: python3 scripts/mail_create.py --out reply.eml --from {a.attendee} --to {org} --subject \"{status.title()}: {info['summary']}\" --calendar {out}  (a draft; nothing is sent)")
    return 0


def _view_window(a, ctx):
    """The window a picture covers, widened to whole pictures so no drawn day is left blank by the window: whole
    weeks (Monday to Sunday) for --view week; for --view month, the whole month(s) of --from (to the month of --to
    when given) including the neighbouring days shown in the grid."""
    from datetime import datetime, time, timedelta

    start, end = _window(a, ctx)
    d0 = start.astimezone(ctx.display).date()
    if a.view == "month":
        m0 = d0.replace(day=1)
        if a.to or a.days:
            last = (end - timedelta(seconds=1)).astimezone(ctx.display).date()
        else:
            last = m0
        m1 = (last.replace(day=28) + timedelta(days=4)).replace(day=1)  # the month after the last one shown
        g0 = m0 - timedelta(days=m0.weekday())
        g1 = m1 + timedelta(days=(7 - m1.weekday()) % 7)
    else:
        last = (end - timedelta(seconds=1)).astimezone(ctx.display).date() if a.to or a.days else d0
        g0 = d0 - timedelta(days=d0.weekday())
        g1 = last + timedelta(days=7 - last.weekday())
    z = ctx.display
    first_pic, end_pic = (m0, m1) if a.view == "month" else (g0, g1)
    return datetime.combine(g0, time(0), tzinfo=z), datetime.combine(g1, time(0), tzinfo=z), first_pic, end_pic


def cmd_render(a) -> int:
    from datetime import timedelta

    from _calrender import render_views
    from _ics import expand
    from _render import announce

    items, vtz, infos = load(a.files, not a.no_cache)
    ctx = _ctx(a, vtz)
    start, end, first_pic, end_pic = _view_window(a, ctx)
    notes: list[str] = []
    occs = expand(items, ctx, start, end, ("event",), notes=notes)
    _print_notes(notes)
    rows = _occ_rows(occs, ctx)
    busy = _busy(a, items, ctx, start, end)
    conflict_keys = set()
    active: list = []
    by_start = {}
    for i, r in enumerate(rows):
        by_start.setdefault((r["item"], r["start_local"].isoformat()), i)
    for b in busy:
        active = [x for x in active if x["e"] > b["s"]]
        for x in active:
            for bb in (x, b):
                k = by_start.get((bb["row"]["item"], bb["row"]["start_local"].isoformat()))
                if k is not None:
                    conflict_keys.add(k)
        active.append(b)
    zname = getattr(ctx.display, "key", None) or str(ctx.display)
    outdir = output_dir(a.out_dir)
    pngs = render_views(rows, a.view, first_pic, end_pic, zname, outdir, conflict_keys, a.force)
    wanted = ((end_pic.year - first_pic.year) * 12 + end_pic.month - first_pic.month) if a.view == "month" else (end_pic - first_pic).days // 7
    span = f"{first_pic.strftime('%B %Y')}" + (f" to {pngs[-1].stem[6:]}" if len(pngs) > 1 else "") if a.view == "month" else f"{first_pic.isoformat()} to {(first_pic + timedelta(days=7 * len(pngs) - 1)).isoformat()}"
    note = f"{len(pngs)} {a.view} view(s) ({span}), {len(rows)} occurrence(s), {len(conflict_keys)} in conflict (outlined in red), times in {zname}."
    if wanted > len(pngs):
        note += f" Only the first {len(pngs)} of {wanted} {a.view}s were drawn; render the rest with a later --from."
    announce(pngs, note)
    return 0


if __name__ == "__main__":
    run_main(main)
