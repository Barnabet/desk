# Calendars with ics_tool.py

## Inputs

`.ics`, `.ical`, `.ifb` (free/busy), several `VCALENDAR`s in one file, UTF-8 with or without BOM, UTF-16, and
emails (`.eml`, `.msg`) that carry an invite: the calendar part is found automatically. Items are numbered `#1 …`
across the files given, in file order; `read --range`, `edit` ops (`"item": N`) and the agenda refer to them.

Outlook meetings, meeting requests, responses, cancellations and appointments saved as `.msg` have no calendar
part: Outlook keeps them in MAPI properties (MS-OXOCAL). The skill rebuilds an iCalendar from them:

| .msg property | becomes |
|---|---|
| start and end (PidLidAppointmentStartWhole/EndWhole, UTC), all-day flag | `DTSTART`/`DTEND` in the item's zone (dates when all-day) |
| time zone definition (PidLidAppointmentTimeZoneDefinitionRecur/StartDisplay) | its Windows zone name mapped to IANA (`W. Europe Standard Time` → `Europe/Berlin`) |
| recurrence blob (PidLidAppointmentRecur) | `RRULE` (daily, weekly, monthly, Nth weekday, month end, yearly; COUNT or UNTIL), `EXDATE` for deleted occurrences, a `RECURRENCE-ID` override for each moved or renamed one |
| clean global object id | `UID` (as Outlook exports it), so replies match the organizer's copy |
| sender, To/Cc/Bcc recipients (meetings only) | `ORGANIZER`, required / optional / resource `ATTENDEE`s (recipients without an SMTP address are left out) |
| busy status, reminder, sequence, location, body | `TRANSP`, `VALARM`, `SEQUENCE`, `LOCATION`, `DESCRIPTION` |
| message class | `METHOD`: REQUEST (meeting request), CANCEL, REPLY (responses, with the sender's PARTSTAT), PUBLISH |

`mail_read` lists it as `<subject>.ics (built from the Outlook meeting properties)`, `mail_extract` saves it, and
`--to-eml` carries it as a `text/calendar` alternative, the way Outlook sends invitations. A recurrence in a
Hijri or Hebrew calendar, or a blob that does not parse, gives the first occurrence only, with a warning.

## Time zones

- A `TZID` resolves in this order: an IANA name (`Europe/Paris`), a Windows name (`Romance Standard Time`,
  mapped with the Unicode CLDR table), the calendar's own `VTIMEZONE` definition (exact rules, DST included).
- UTC times (`…Z`) are instants. Floating times (no zone) are wall-clock times: they are shown as they are, and
  taken in the display zone for conflict and free-slot maths. Google's `X-WR-TIMEZONE` applies to floating times.
- `--tz` sets the display zone for `agenda`, `conflicts`, `free` and `render` (default: the machine's zone, from
  `TZ`, `/etc/localtime` or the Windows zone name). `read` shows each event in its own zone.
- Recurrences are expanded in the event's own zone, so a 09:00 weekly meeting stays at 09:00 across daylight
  saving changes (and moves in UTC).

## Recurrence

- `RRULE` (all frequencies and BYxxx parts, COUNT, UNTIL in any of the forms clients write), `RDATE` (dates, times,
  periods), `EXDATE` (a DATE exdate removes that day's occurrence of a timed series).
- Overrides: a `VEVENT` with the same `UID` and a `RECURRENCE-ID` replaces that occurrence (moved, retitled);
  with `STATUS:CANCELLED` it removes it. A cancelled master event is skipped unless `--include-cancelled`.
- At most 20 000 occurrences per event per window (runaway `FREQ=MINUTELY` rules stay bounded). When a series
  reaches the cap, `agenda`, `conflicts`, `free` and `render` print `warning: item #N (…) repeats more than 20,000
  times in this window; only occurrences up to … are included` on stderr (and above the agenda), and JSON has it
  in `notes`. Narrow `--from`/`--to` to see the rest.
- An item whose end falls past the year 9999, or that ends before it starts, is read as a zero-length item and
  marked damaged; an item that cannot be expanded at all is left out with a note.
- A series that started years ago is not walked from its first occurrence: its start is moved forward by whole
  intervals to just before the window, which gives the same occurrences (rules with COUNT are expanded from the
  start). A 20-year-old hourly series costs the same as a new one.
- Rules as Exchange writes them (`BYDAY=MO, TU, WE`, folded mid-rule) are cleaned up before expansion.
- `read` describes rules in English: "weekly on Mon, Wed until 2026-12-31", "monthly on the 2nd Tue".

## Damaged calendars

Real calendars carry damage: an unreadable date, an impossible duration, a property written twice, a component
with a bad name. Each item is read as far as it goes: a bad property is skipped and recorded, and `read` shows
"damaged: …" for it (`problems` in JSON); a doubled property keeps its first value. A component that cannot be
parsed at all is skipped with a warning, and the rest of the file still reads. `merge` and `edit` copy damaged
items as they are and say so; they never refuse a file because of them.

## Busy, conflicts, free slots

- Busy: events that are not cancelled, not `TRANSP:TRANSPARENT` (unless `--include-free`), not all-day (unless
  `--all-day-busy`), plus `FREEBUSY` periods of `.ifb` files except `FBTYPE=FREE`.
- `conflicts` reports every overlapping pair with the overlap in minutes, across all files given; with many pairs
  it lists the busiest days first, and the output is cut at `--max-chars` with the command for the next part.
- `free` walks each working day (`--weekdays mon-fri`, `--hours 09:00-17:00`, in `--tz`), subtracts busy time
  (widened by `--buffer`), and keeps gaps of at least `--min`. `--first N` stops early.

## create --spec

One event object, a list of them, or `{"calendar": {"name", "method", "tz"}, "events": [...]}`. Event keys:

| key | meaning |
|---|---|
| `summary` | title |
| `start` | `2026-10-05T09:30` (in `tz`), with an offset, or `2026-10-05` for all-day |
| `end` / `duration` / `days` | end time, or a duration like `45m`, `1h30m`, `PT2H`; all-day events end the next day by default |
| `tz` | IANA zone for this event (else the calendar's, else local) |
| `all_day` | true for date-only events (shown as free unless `busy: true`) |
| `location`, `description`, `url`, `categories` | text fields (`categories` a list or comma text) |
| `status` | `TENTATIVE`, `CONFIRMED`, `CANCELLED` |
| `busy` / `transp` | `busy: false` shows as free (`TRANSP:TRANSPARENT`) |
| `organizer` | `"Name <addr>"` or `{email, name}`; required for `METHOD:REQUEST` |
| `attendees` | list of `"Name <addr>"` or `{email, name, role: required|optional|chair|fyi, partstat, rsvp}` |
| `rrule` | `"FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"` or `{"FREQ": "WEEKLY", "BYDAY": ["MO"]}` |
| `exdate`, `rdate` | dates or date-times (a list) |
| `alarms` | `["15m", "1d"]` or `[{"before": "10m", "action": "DISPLAY", "description": "…"}]` |
| `uid`, `sequence`, `priority`, `class` | as in RFC 5545 |

The written calendar has `PRODID`, `VERSION`, `CALSCALE`, the needed `VTIMEZONE` blocks and a fresh `DTSTAMP`, and
is read back before the script reports success.

## edit --ops

A JSON list; each op names its target with `uid`, `item` (the `#N` from `read`) or `summary_match` (regex). Every
change bumps `SEQUENCE` and `LAST-MODIFIED`. An op that matches nothing fails the whole edit (nothing is written)
unless it has `"optional": true`.

| op | fields | effect |
|---|---|---|
| `set` | `field`, `value` | set or clear a text field (summary, location, description, status, url, transp, class, priority) |
| `move` | `start`, optional `end`, `tz` | new start; the duration is kept when `end` is omitted |
| `shift` | `by` (`1h`, `-30m`, `2d`) | move start and end |
| `cancel` | `date` or `at` | cancel one occurrence of a series (adds EXDATE); without a date, cancels the event |
| `delete` | | remove the event and its overrides |
| `add_attendee` / `remove_attendee` | `email`, `name`, `role` | |
| `set_rrule` | `rrule` | replace (or with an empty value remove) the repeat rule |
| `end_series` | `until` | stop a series after that date |
| `add_alarm` / `remove_alarms` | `before` | |

## reply

`reply INVITE --attendee ADDR --status accepted|declined|tentative [--comment …] --out reply.ics` writes a
`METHOD:REPLY` calendar with the invitation's UID, SEQUENCE, times and organizer. Put it in a draft to the
organizer with `mail_create.py --calendar reply.ics` (the script prints the command). Nothing is sent.

## render

`--view week` (time grid 08:00-18:00 widened to fit, all-day strip, overlapping events side by side, tentative
dashed, conflicts outlined in red, one colour per input file) or `--view month` (up to what fits per day, then
"+N more"). The pictures always show whole periods: `--view week` starts on the Monday of `--from` and, without
`--to`, draws that one week; `--view month` draws the whole month of `--from` (and each month up to `--to`),
including the neighbouring days its grid shows, so an empty cell really is free. At most 8 PNGs; the output
says when more were asked for. Look at them with view_image.

## Output formats

- `agenda` Markdown: one heading per day, `start–end **summary** · location (file #item, flags)`; flags are
  repeats, moved/changed, tentative, cancelled, free, floating time. Summaries longer than 200 characters are cut
  with a marker giving their full length (`… [5,000 characters]`).
- `agenda --format csv` (and `--out x.csv`): `date, start, end, all_day, summary, location, status, calendar,
  item, uid, recurring`, with `start` and `end` as `YYYY-MM-DD HH:MM` in `--tz` (all-day: the first and last day).
- `agenda --format json` (and `--out x.json`): `{tz, from, to, notes, occurrences}`; each occurrence has `date`,
  `start`/`end` in ISO 8601 with the UTC offset (`2026-10-13T15:00+02:00`; floating times have none and
  `floating: true`; all-day items give dates, `end` exclusive and `last_day` inclusive), `all_day`, `summary`,
  `location`, `status`, `busy`, `calendar`, `item`, `uid`, `recurring`, `changed`.
- `read --full` shows alarms in words (`display 15 min before the start`), skipped dates, "Shows as: busy/free",
  the sequence and attendees with role and answer.
