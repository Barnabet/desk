#!/usr/bin/env python3
"""Explore mailboxes of any size: mbox files (Gmail Takeout, Thunderbird, Apple Mail export), Maildir and
Maildir++ folders, Apple Mail .mbox bundles, and folders of .eml/.emlx/.msg files.

The first call builds an index in one streaming pass (byte offsets, headers, attachment names per message) and
caches it, so later calls answer in milliseconds even for GB mailboxes. Every message has a stable address #N
(file order) usable with mail_read.py / mail_extract.py --message N.

commands:
  index    map of the mailbox: counts, date range, top senders, attachments, per-year volume, first/last messages
  list     messages #a-#b as a table (Markdown for a few rows, CSV for more)
  search   filter by sender, recipient, subject, body text, dates, attachments, labels; returns addresses
  show     one message in full (like mail_read.py)
  export   matching messages to .eml files, a new mbox, Markdown, CSV or JSON
  threads  conversations rebuilt from References / In-Reply-To (and subjects)
  stats    senders, recipients, domains, months, weekdays, hours, sizes, attachment types
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, emit, human_size, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/mbox_tool.py index archive.mbox
  python3 scripts/mbox_tool.py list archive.mbox --range 1-200
  python3 scripts/mbox_tool.py search archive.mbox --from "@acme\\.com" --since 2025-01-01 --has-attachment
  python3 scripts/mbox_tool.py search archive.mbox --body "invoice\\s+#?\\d+" --limit 20
  python3 scripts/mbox_tool.py show archive.mbox 1234
  python3 scripts/mbox_tool.py export archive.mbox --subject "contract" --as eml --out found/
  python3 scripts/mbox_tool.py export ~/Maildir --messages 1-50 --as mbox --out first50.mbox
  python3 scripts/mbox_tool.py threads archive.mbox --limit 30
  python3 scripts/mbox_tool.py stats archive.mbox --top 15
"""

FIELDS_CSV = ["n", "date", "from", "to", "cc", "subject", "size", "attachments", "labels", "message_id"]


def _add_common(sp) -> None:
    sp.add_argument("mailbox", help="an mbox file, a Maildir, an Apple Mail .mbox bundle or a folder of .eml/.emlx/.msg files")
    sp.add_argument("--no-cache", action="store_true", help="do not read or write the cached index")
    sp.add_argument("--workers", type=int, help="parallel workers for the first indexing pass")
    sp.add_argument("--tz", help="show dates in this zone (Europe/Paris, UTC, local); default: each sender's own time with its UTC offset")


def _add_filters(sp) -> None:
    g = sp.add_argument_group("filters (regular expressions, case-insensitive unless --case)")
    g.add_argument("--from", dest="from_", metavar="RE", help="sender name or address")
    g.add_argument("--to", metavar="RE", help="any To or Cc recipient")
    g.add_argument("--subject", metavar="RE")
    g.add_argument("--body", metavar="RE", help="the message text (decoded; builds a cached text store on first use)")
    g.add_argument("--any", metavar="RE", help="from, to, cc, subject, attachment names or body")
    g.add_argument("--since", metavar="DATE", help="on or after (2025-01-31, 2025-01-31T09:00, -30d)")
    g.add_argument("--until", metavar="DATE", help="before (exclusive)")
    g.add_argument("--has-attachment", action="store_true", help="only messages with attachments (not counting inline images)")
    g.add_argument("--attachment", metavar="GLOB", help='attachment file name glob, like "*.pdf"')
    g.add_argument("--label", metavar="RE", help="Gmail labels / keywords / folder")
    g.add_argument("--list-id", metavar="RE", help="mailing list")
    g.add_argument("--message-id", metavar="ID", help="exact Message-ID")
    g.add_argument("--case", action="store_true", help="case-sensitive matching")


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sp = sub.add_parser("index", help="map of the mailbox (the default first look)")
    _add_common(sp)
    sp.add_argument("--top", type=int, default=10, help="how many top senders to show")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("list", help="messages as a table")
    _add_common(sp)
    sp.add_argument("--range", default=None, help="message numbers like 1-200, 500-, last (default: the first 200)")
    sp.add_argument("--sort", choices=["n", "date", "size", "from", "subject"], default="n")
    sp.add_argument("--desc", action="store_true", help="reverse the sort")
    sp.add_argument("--format", choices=["auto", "md", "csv", "json"], default="auto", help="auto: Markdown up to 40 rows, CSV above")
    sp.add_argument("--max-chars", type=int, default=60000)

    sp = sub.add_parser("search", help="find messages; prints their addresses (#N)")
    _add_common(sp)
    _add_filters(sp)
    sp.add_argument("--limit", type=int, default=100, help="rows to print (default 100)")
    sp.add_argument("--offset", type=int, default=0, help="skip this many matches (paging)")
    sp.add_argument("--sort", choices=["n", "date", "size"], default="n")
    sp.add_argument("--desc", action="store_true")
    sp.add_argument("--count", action="store_true", help="only count the matches")
    sp.add_argument("--format", choices=["auto", "md", "csv", "json"], default="auto")
    sp.add_argument("--max-chars", type=int, default=60000)

    sp = sub.add_parser("show", help="one message in full")
    _add_common(sp)
    sp.add_argument("message", help="number (#N), 'last' or a Message-ID")
    sp.add_argument("--headers", action="store_true")
    sp.add_argument("--prefer", choices=["auto", "text", "html"], default="auto")
    sp.add_argument("--strip-quotes", action="store_true")
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--max-chars", type=int, default=60000)
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("export", help="write matching messages out")
    _add_common(sp)
    _add_filters(sp)
    sp.add_argument("--messages", help="message numbers like 1-10,42 (instead of or on top of the filters)")
    sp.add_argument("--as", dest="as_", choices=["eml", "mbox", "md", "csv", "json"], required=True)
    sp.add_argument("--out", required=True, help="a folder for eml (and md, one file per message), else a file")
    sp.add_argument("--max", type=int, default=100000, help="stop after this many messages")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--format", choices=["md", "json"], default="md")

    sp = sub.add_parser("threads", help="conversations")
    _add_common(sp)
    sp.add_argument("--min", type=int, default=2, help="only threads with at least this many messages (default 2)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--thread", help="show one thread as a tree: its id (T12) or any message number in it")
    sp.add_argument("--sort", choices=["last", "first", "count", "people", "bytes"], default="last", help="order: last (newest activity first, default), first (oldest start first), count (most messages), people (most participants), bytes (largest)")
    sp.add_argument("--no-subject", action="store_true", help="do not join replies that only share a subject")
    sp.add_argument("--format", choices=["auto", "md", "csv", "json"], default="auto")
    sp.add_argument("--max-chars", type=int, default=60000)

    sp = sub.add_parser("stats", help="who, when, how big")
    _add_common(sp)
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--format", choices=["md", "json"], default="md")

    a = p.parse_args()
    from _mbox import open_mailbox

    if a.tz:
        from _ics import get_zone

        _TZ.append(get_zone(a.tz))

    path = Path(a.mailbox).expanduser()
    box = open_mailbox(path, use_cache=not a.no_cache, workers=a.workers)
    try:
        return {"index": cmd_index, "list": cmd_list, "search": cmd_search, "show": cmd_show, "export": cmd_export, "threads": cmd_threads, "stats": cmd_stats}[a.cmd](box, a)
    finally:
        box.close()


# ── helpers ─────────────────────────────────────────────────────────────


def _from(r) -> str:
    if r["from_name"] and r["from_addr"]:
        return f"{r['from_name']} <{r['from_addr']}>"
    return r["from_addr"] or r["from_name"] or ""


_TZ: list = []  # the display zone chosen with --tz (empty: each sender's own time)


def _date(r, full: bool = False) -> str:
    """The message date as 'YYYY-MM-DD HH:MM ±hhmm' (sender's time), or in the --tz zone."""
    import re
    from datetime import datetime, timezone

    d = r["date"]
    if not d:
        return ""
    if _TZ and r["ts"] is not None:
        loc = datetime.fromtimestamp(r["ts"], tz=timezone.utc).astimezone(_TZ[0])
        return loc.strftime("%Y-%m-%d %H:%M:%S" if full else "%Y-%m-%d %H:%M")
    m = re.search(r"([+-])(\d\d):(\d\d)$", d)
    off = f" {m.group(1)}{m.group(2)}{m.group(3)}" if m else ""
    return (d[:19] if full else d[:16]).replace("T", " ") + (" UTC" if off in (" +0000", " -0000") else off)


def _atts(r) -> str:
    import json

    if not r["att"]:
        return ""
    items = json.loads(r["att"])
    names = [x[0] or x[1] for x in items if not x[3]]
    return "; ".join(names)


def _iso(r) -> str:
    """The full ISO date (with offset) for CSV and JSON; in the --tz zone when one was given."""
    if _TZ and r["ts"] is not None:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(r["ts"], tz=timezone.utc).astimezone(_TZ[0]).isoformat()
    return r["date"] or ""


def _row_out(r) -> list:
    return [r["n"], _iso(r), _from(r), r["to_addrs"] or "", r["cc_addrs"] or "", r["subject"] or "", r["size"], _atts(r), r["labels"] or "", r["msgid"] or ""]


def _cache_note(box) -> str:
    if box.built_in is not None:
        return f"(index built in {box.built_in:.1f}s and cached; later calls reuse it)" if box.use_cache else f"(index built in {box.built_in:.1f}s; not cached: --no-cache)"
    return "(index from cache)"


def _print_rows(rows, fmt: str, max_chars: int, compact: bool, total: int, next_cmd: str | None, snippets: dict | None = None, cont=None) -> None:
    """A Markdown or CSV table of index rows within max_chars, cut at a row. `cont(k)` gives the exact command for
    the rows after the first k shown (when the budget cut the table); `next_cmd` is the command after all of them."""
    from _out import auto_format, table, trunc

    fmt = auto_format(fmt, len(rows))
    if fmt == "md" and compact:
        headers = ["#", "date", "from", "subject", "size", "attachments"]
        data = [[f"#{r['n']}", _date(r), trunc(_from(r), 48), trunc(r["subject"], 80), human_size(r["size"] or 0), trunc(_atts(r), 60)] for r in rows]
        if snippets:
            headers.append("match")
            for d, r in zip(data, rows):
                d.append(trunc(snippets.get(r["n"], ""), 120))
    else:
        headers = FIELDS_CSV + (["match"] if snippets else [])
        data = [_row_out(r) + ([snippets.get(r["n"], "")] if snippets else []) for r in rows]
    head = table(headers, [], fmt).rstrip("\n")
    body = [table(headers, [d], fmt).rstrip("\n")[len(head) :].lstrip("\n") for d in data]
    budget = (max_chars or 10**12) - len(head) - 400
    shown, used = 0, 0
    for line in body:
        if used + len(line) + 1 > budget and shown:
            break
        used += len(line) + 1
        shown += 1
    print("\n".join([head] + body[:shown]))
    if shown < len(rows):
        more = cont(shown) if cont else None
        print(f"\n[--max-chars {max_chars} reached after {shown} of {len(rows)} rows. " + (f"Next part: {more}]" if more else "Ask for fewer rows (--range/--limit) or raise --max-chars.]"))
    elif next_cmd:
        print(f"\n[{total} in all. Next: {next_cmd}]")


# ── index (the map) ─────────────────────────────────────────────────────


def cmd_index(box, a) -> int:
    from collections import Counter

    db = box.db()
    meta = box.meta()
    n = box.count()
    rows = db.execute("SELECT MIN(ts) mn, MAX(ts) mx, SUM(size) sz, SUM(CASE WHEN ts IS NULL THEN 1 ELSE 0 END) nodate, SUM(CASE WHEN n_att > 0 THEN 1 ELSE 0 END) withatt, SUM(n_att) natt FROM msgs").fetchone()
    top = db.execute("SELECT from_addr, MAX(from_name) name, COUNT(*) c FROM msgs GROUP BY from_addr ORDER BY c DESC LIMIT ?", (a.top,)).fetchall()
    years = db.execute("SELECT substr(date, 1, 4) y, COUNT(*) c FROM msgs WHERE date IS NOT NULL GROUP BY y ORDER BY y").fetchall()
    pre = meta.get("map") or {}
    att_bytes = pre.get("attachment_bytes", 0)
    exts: Counter[str] = Counter(dict(pre.get("attachment_types") or []))
    labels: Counter[str] = Counter(dict(pre.get("labels") or []))
    folders = db.execute("SELECT folder, COUNT(*) c FROM msgs WHERE folder IS NOT NULL GROUP BY folder ORDER BY c DESC LIMIT 20").fetchall()
    first = box.rows(limit=5)
    last = box.rows(order="n DESC", limit=5)[::-1] if n > 10 else []
    from datetime import datetime, timezone

    def d(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "?"

    data = {
        "mailbox": str(box.path),
        "kind": box.kind,
        "bytes": meta.get("bytes"),
        "messages": n,
        "first_date": d(rows["mn"]),
        "last_date": d(rows["mx"]),
        "without_date": rows["nodate"] or 0,
        "with_attachments": rows["withatt"] or 0,
        "attachments": rows["natt"] or 0,
        "attachment_bytes": att_bytes,
        "attachment_types": exts.most_common(12),
        "top_senders": [{"from": r["from_addr"], "name": r["name"], "count": r["c"]} for r in top],
        "per_year": {r["y"]: r["c"] for r in years},
        "labels": labels.most_common(20),
        "folders": [(r["folder"], r["c"]) for r in folders],
        "cached": box.cached(),
    }
    if a.format == "json":
        emit(data, "json", max_chars=None)
        return 0
    out = [f"# {box.path.name} — {box.kind}, {human_size(meta.get('bytes') or 0)}, {n:,} messages", ""]
    if n:
        out.append(f"- **Dates:** {data['first_date']} → {data['last_date']}" + (f" ({data['without_date']} without a date)" if data["without_date"] else ""))
        out.append(f"- **Attachments:** {data['with_attachments']:,} messages carry {data['attachments']:,} files ({human_size(att_bytes)})" + (": " + ", ".join(f"{k} {v}" for k, v in data["attachment_types"][:8]) if exts else ""))
        if years:
            out.append("- **Per year:** " + ", ".join(f"{r['y']}: {r['c']:,}" for r in years))
        if labels:
            out.append("- **Labels:** " + ", ".join(f"{k} ({v})" for k, v in labels.most_common(15)))
        if folders:
            out.append("- **Folders:** " + ", ".join(f"{f} ({c})" for f, c in folders))
        out += ["", "## Top senders", "", md_table(["from", "name", "messages"], [[r["from_addr"], r["name"] or "", r["c"]] for r in top])]
        out += ["", "## First and last messages", ""]
        from _out import trunc

        sample = first + ([None] if last else []) + last
        out.append(md_table(["#", "date", "from", "subject", "size"], [["…", "", "", "", ""] if r is None else [f"#{r['n']}", _date(r), trunc(_from(r), 40), trunc(r["subject"], 70), human_size(r["size"] or 0)] for r in sample]))
    else:
        out.append("The mailbox is empty.")
    out += ["", "Next: `list --range 1-200`, `search --from/--subject/--body/--since …`, `show N`, `threads`, `stats`; read one message with `mail_read.py MAILBOX --message N`.", _cache_note(box)]
    print("\n".join(out))
    return 0


# ── list ────────────────────────────────────────────────────────────────


def cmd_list(box, a) -> int:
    from _common import parse_ranges
    from _out import script_cmd

    n = box.count()
    if n == 0:
        print("The mailbox is empty.")
        return 0
    spec = a.range or f"1-{min(n, 200)}"
    nums = parse_ranges(spec, n)
    order = {"n": "n", "date": "ts", "size": "size", "from": "from_addr", "subject": "subject"}[a.sort] + (" DESC" if a.desc else "")
    if nums == list(range(nums[0], nums[-1] + 1)):
        rows = box.rows("n BETWEEN ? AND ?", (nums[0], nums[-1]), order=order)
    else:
        rows = []
        for i in range(0, len(nums), 900):
            chunk = nums[i : i + 900]
            rows += box.rows(f"n IN ({','.join('?' * len(chunk))})", chunk, order=order)
    nxt = None
    if nums[-1] < n:
        nxt = script_cmd({"--range": f"{nums[-1] + 1}-{min(n, nums[-1] + len(nums))}"})
    if a.format == "json":
        emit({"messages": n, "range": spec, "rows": [dict(zip(FIELDS_CSV, _row_out(r))) for r in rows], "next": nxt}, "json", max_chars=None)
        return 0
    print(f"{box.path.name}: messages {nums[0]}-{nums[-1]} of {n:,} {_cache_note(box)}")
    cont = None
    if a.sort == "n" and not a.desc and nums == list(range(nums[0], nums[-1] + 1)):
        cont = lambda k: script_cmd({"--range": f"{rows[k - 1]['n'] + 1}-{nums[-1]}"})  # noqa: E731
    _print_rows(rows, a.format, a.max_chars, True, n, nxt, cont=cont)
    return 0


# ── search ──────────────────────────────────────────────────────────────


def _rx(pattern: str | None, case: bool) -> str | None:
    import re

    if pattern is None:
        return None
    try:
        re.compile(pattern)
    except re.error as e:
        raise UsageError(f"bad regular expression {pattern!r}: {e}") from e
    return pattern if case else "(?i)" + pattern


def select(box, a, numbers: list[int] | None = None) -> tuple[list, dict[int, str]]:
    """Rows matching the filters (header filters in SQL, body filters on the cached text store), with snippets."""
    import fnmatch
    import json
    import re
    import zlib

    from _ics import get_zone, parse_when

    where, params = [], []
    case = getattr(a, "case", False)
    for field, cols in (("from_", ["from_name", "from_addr"]), ("to", ["to_addrs", "cc_addrs"]), ("subject", ["subject"]), ("label", ["labels", "folder"]), ("list_id", ["list_id"])):
        pat = _rx(getattr(a, field, None), case)
        if pat:
            where.append("(" + " OR ".join(f"{c} REGEXP ?" for c in cols) + ")")
            params += [pat] * len(cols)
    tz = get_zone("UTC")
    if getattr(a, "since", None):
        where.append("ts >= ?")
        params.append(parse_when(a.since, tz).timestamp())
    if getattr(a, "until", None):
        where.append("ts < ?")
        params.append(parse_when(a.until, tz).timestamp())
    if getattr(a, "has_attachment", False):
        where.append("n_att > 0")
    if getattr(a, "attachment", None):
        where.append("att IS NOT NULL")
    if getattr(a, "message_id", None):
        mid = a.message_id if a.message_id.startswith("<") else f"<{a.message_id}>"
        where.append("msgid = ?")
        params.append(mid)
    if numbers is not None:
        if not numbers:
            return [], {}
        where.append(f"n IN ({','.join(str(int(x)) for x in numbers)})")
    order = {"n": "n", "date": "ts", "size": "size"}.get(getattr(a, "sort", "n") or "n", "n") + (" DESC" if getattr(a, "desc", False) else "")
    body = _rx(getattr(a, "body", None), case)
    anyp = _rx(getattr(a, "any", None), case)
    sql_where = " AND ".join(where)
    if (body or anyp) and not getattr(a, "attachment", None):
        # Body searches: narrow by number first (cheap), read full rows only for the hits.
        cand = [int(r[0]) for r in box.db().execute("SELECT n FROM msgs" + (f" WHERE {sql_where}" if sql_where else "") + f" ORDER BY {order}", tuple(params))]
        snippets: dict[int, str] = {}
        keep: set[int] = set()
        if anyp:
            rx = re.compile(anyp)
            cols = ("from_name", "from_addr", "to_addrs", "cc_addrs", "subject", "att")
            head_q = "SELECT n FROM msgs WHERE (" + " OR ".join(f"{c} REGEXP ?" for c in cols) + ")" + (f" AND {sql_where}" if sql_where else "")
            keep = {int(r[0]) for r in box.db().execute(head_q, tuple([anyp] * len(cols)) + tuple(params))}
            keep |= _body_filter(box, [n for n in cand if n not in keep], rx, snippets)
            cand = [n for n in cand if n in keep]
        if body:
            hits = _body_filter(box, cand, re.compile(body), snippets)
            cand = [n for n in cand if n in hits]
        return _rows_for(box, cand), snippets
    rows = box.rows(sql_where, params, order=order)
    if getattr(a, "attachment", None):
        glob = a.attachment.lower()
        rows = [r for r in rows if any(fnmatch.fnmatch((x[0] or "").lower(), glob) for x in json.loads(r["att"] or "[]"))]
    snippets = {}
    if anyp:
        rx = re.compile(anyp)
        head_hits = {r["n"] for r in rows if any(rx.search(str(r[c] or "")) for c in ("from_name", "from_addr", "to_addrs", "cc_addrs", "subject", "att"))}
        keep = head_hits | _body_filter(box, [r["n"] for r in rows if r["n"] not in head_hits], rx, snippets)
        rows = [r for r in rows if r["n"] in keep]
    if body:
        hits = _body_filter(box, [r["n"] for r in rows], re.compile(body), snippets)
        rows = [r for r in rows if r["n"] in hits]
    return rows, snippets


def _rows_for(box, ns: list[int]) -> list:
    """Index rows for message numbers, in the order given."""
    got = {}
    for i in range(0, len(ns), 900):
        chunk = ns[i : i + 900]
        for r in box.db().execute(f"SELECT * FROM msgs WHERE n IN ({','.join('?' * len(chunk))})", chunk):
            got[int(r["n"])] = r
    return [got[n] for n in ns if n in got]


def _body_filter(box, ns: list[int], rx, snippets: dict[int, str]) -> set[int]:
    """The message numbers (of `ns`) whose decoded text matches rx, with a snippet for each. The cached text store
    is scanned in two threads (SQLite and zlib release the GIL), with a literal prefilter (see scan_text_store)."""
    from concurrent.futures import ThreadPoolExecutor

    from _common import workers_for
    from _mbox import scan_text_store

    if not ns:
        return set()
    wanted = set(ns)
    path = box.text_store()
    lo, hi = min(wanted), max(wanted)
    n_threads = workers_for(2, None) if len(wanted) > 2000 else 1
    step = (hi - lo) // n_threads + 1
    ranges = [(lo + i * step, min(hi, lo + (i + 1) * step - 1)) for i in range(n_threads)]
    only = wanted if len(wanted) < hi - lo + 1 else None
    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        parts = list(pool.map(lambda r: scan_text_store(path, r[0], r[1], only, rx), ranges))
    hits: set[int] = set()
    for part in parts:
        for n, snip in part:
            hits.add(n)
            snippets[n] = snip
    return hits


def cmd_search(box, a) -> int:
    from _out import script_cmd

    rows, snippets = select(box, a)
    total = len(rows)
    if a.count:
        print(total if a.format != "json" else f'{{"matches": {total}}}')
        return 0
    page = rows[a.offset : a.offset + a.limit]
    nxt = script_cmd({"--offset": str(a.offset + a.limit)}) if a.offset + a.limit < total else None
    if a.format == "json":
        emit({"matches": total, "offset": a.offset, "rows": [dict(zip(FIELDS_CSV + ["match"], _row_out(r) + [snippets.get(r["n"], "")])) for r in page], "next": nxt}, "json", max_chars=None)
        return 0
    note = ""
    if box.text_built_in is not None:
        note = f" (text store built in {box.text_built_in:.1f}s" + (" and cached)" if box.use_cache else "; not cached: --no-cache)")
    print(f"{total:,} matching message(s) in {box.path.name}" + (f"; showing {a.offset + 1}-{a.offset + len(page)}" if total > len(page) else "") + note + ".")
    if not page:
        return 0
    _print_rows(page, a.format, a.max_chars, True, total, nxt, snippets or None, cont=lambda k: script_cmd({"--offset": str(a.offset + k)}))
    if total <= 5:
        print(f"\nRead one with: python3 scripts/mail_read.py {box.path} --message {page[0]['n']}")
    return 0


# ── show ────────────────────────────────────────────────────────────────


def cmd_show(box, a) -> int:
    from _mail import body_markdown, build_mail, parse_bytes, public, render_md
    from _out import page_text

    n = box.resolve(a.message)
    mail = build_mail(parse_bytes(box.raw(n)), "mbox" if box.kind == "mbox" else "eml")
    mail["mailbox_address"] = f"{box.path.name}#{n}"
    if a.format == "json":
        data = public(mail)
        data["body"], data["body_source"] = body_markdown(mail, a.prefer, a.strip_quotes)
        data.pop("text", None)
        data.pop("html", None)
        if not a.headers:
            data.pop("headers", None)
        emit(data, "json", max_chars=None)
        return 0
    print(page_text(render_md(mail, {"headers": a.headers, "prefer": a.prefer, "strip_quotes": a.strip_quotes}), a.offset, a.max_chars))
    return 0


# ── export ──────────────────────────────────────────────────────────────


def cmd_export(box, a) -> int:
    import re

    from _common import parse_ranges

    nums = parse_ranges(a.messages, box.count()) if a.messages else None
    has_filter = any(getattr(a, k, None) for k in ("from_", "to", "subject", "body", "any", "since", "until", "attachment", "label", "list_id", "message_id")) or a.has_attachment
    if nums is None and not has_filter:
        raise UsageError("say what to export: --messages 1-100 and/or filters like --from, --subject, --since")
    rows, _ = select(box, a, nums)
    rows = rows[: a.max]
    if not rows:
        print("No message matches; nothing written.")
        return 0
    written: list[str] = []
    if box.kind != "mbox":
        target = Path(a.out).expanduser().resolve()
        if target == box.path.resolve() or box.path.resolve() in target.parents:
            raise SkillError(f"refusing to export into the mailbox folder {box.path} itself; choose a folder outside it")
    if a.as_ in ("eml", "md"):
        out = output_dir(a.out)
        from _mail import safe_name

        for r in rows:
            stem = f"{r['n']:06d}-" + safe_name((r["subject"] or "no subject")[:60], "message").replace(" ", "_")
            dest = out / (stem + (".eml" if a.as_ == "eml" else ".md"))
            if dest.exists() and not a.force:
                raise SkillError(f"{dest} already exists; pass --force or choose another folder")
            raw = box.raw(r["n"])
            if a.as_ == "eml":
                dest.write_bytes(raw)
            else:
                from _mail import build_mail, parse_bytes, render_md

                m = build_mail(parse_bytes(raw))
                m["mailbox_address"] = f"{box.path.name}#{r['n']}"
                dest.write_text(render_md(m, {"depth": 1}), encoding="utf-8")
            written.append(str(dest))
        where = str(out)
    elif a.as_ == "mbox":
        from _mbox import mbox_escape, mbox_from_line

        dest = output_path(a.out, [box.path], a.force)
        with open(dest, "wb") as f:
            for r in rows:
                raw = box.raw(r["n"])
                f.write(mbox_from_line(raw))
                f.write(mbox_escape(raw))
                f.write(b"\n")
        where = str(dest)
    else:
        from _out import table

        dest = output_path(a.out, [box.path], a.force)
        dest.write_text(table(FIELDS_CSV, [_row_out(r) for r in rows], "csv" if a.as_ == "csv" else "json"), encoding="utf-8")
        where = str(dest)
    if a.format == "json":
        emit({"exported": len(rows), "as": a.as_, "out": where, "numbers": [r["n"] for r in rows]}, "json", max_chars=None)
    else:
        nums_txt = ", ".join(f"#{r['n']}" for r in rows[:20]) + (" …" if len(rows) > 20 else "")
        print(f"Exported {len(rows)} message(s) as {a.as_} to {where}: {nums_txt}")
    return 0


# ── threads ─────────────────────────────────────────────────────────────


def build_threads(rows: list, by_subject: bool = True) -> list[dict]:
    """Connected components over Message-ID links (References, In-Reply-To), then same-subject replies."""
    import re

    # "Re: ", "Fwd [2]: " … repeated. One \s* per gap, so a long run of spaces splits one way only (was quadratic).
    reply_prefix = re.compile(r"^\s*(?:(?:re|fwd?|aw|wg|sv|tr|rif|antw)\s*(?:\[\d+\]\s*)?:\s*)+")
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    key_of: dict[int, str] = {}
    for r in rows:
        k = r["msgid"] or f"#n{r['n']}"
        key_of[r["n"]] = k
        parent.setdefault(k, k)
        refs = (r["refs"] or "").split()
        if r["in_reply_to"]:
            refs.append(r["in_reply_to"])
        for ref in refs:
            parent.setdefault(ref, ref)
            union(ref, k)
    if by_subject:
        first_by_subject: dict[str, str] = {}
        for r in sorted(rows, key=lambda r: r["ts"] or 0):
            subj = reply_prefix.sub("", (r["subject"] or "").lower()).strip()
            if not subj or len(subj) < 4:
                continue
            is_reply = subj != (r["subject"] or "").lower().strip()
            if subj in first_by_subject:
                if is_reply:
                    union(first_by_subject[subj], key_of[r["n"]])
            else:
                first_by_subject[subj] = key_of[r["n"]]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(find(key_of[r["n"]]), []).append(r)
    threads = []
    for members in groups.values():
        members.sort(key=lambda r: (r["ts"] or 0, r["n"]))
        people = []
        for r in members:
            f = r["from_addr"] or r["from_name"]
            if f and f not in people:
                people.append(f)
        threads.append({"members": members, "count": len(members), "first": members[0]["date"], "last": members[-1]["date"], "subject": members[0]["subject"] or "", "people": people, "last_ts": members[-1]["ts"] or 0})
    threads.sort(key=lambda t: -t["last_ts"])
    for i, t in enumerate(threads, 1):
        t["id"] = f"T{i}"
    return threads


def cmd_threads(box, a) -> int:
    from _out import auto_format, script_cmd, table, trunc

    rows = box.rows()
    threads = build_threads(rows, not a.no_subject)
    if a.thread:
        t = None
        if a.thread.upper().startswith("T") and a.thread[1:].isdigit():
            t = next((x for x in threads if x["id"] == a.thread.upper()), None)
        elif a.thread.lstrip("#").isdigit():
            n = int(a.thread.lstrip("#"))
            t = next((x for x in threads if any(r["n"] == n for r in x["members"])), None)
        if t is None:
            raise UsageError(f"no thread {a.thread}")
        by_id = {r["msgid"]: r for r in t["members"] if r["msgid"]}
        depth: dict[int, int] = {}
        for r in t["members"]:
            par = by_id.get(r["in_reply_to"]) or next((by_id[x] for x in reversed((r["refs"] or "").split()) if x in by_id), None)
            depth[r["n"]] = depth.get(par["n"], 0) + 1 if par is not None and par["n"] != r["n"] else 0
        if a.format == "json":
            emit({"id": t["id"], "subject": t["subject"], "messages": [{"n": r["n"], "depth": depth[r["n"]], "date": r["date"], "from": _from(r), "subject": r["subject"]} for r in t["members"]]}, "json", max_chars=None)
            return 0
        print(f"# {t['id']}: {t['subject']} — {t['count']} messages, {len(t['people'])} people\n")
        for r in t["members"]:
            print(f"{'  ' * min(depth[r['n']], 12)}- #{r['n']} {_date(r)} {trunc(_from(r), 40)}: {trunc(r['subject'], 70)}")
        print(f"\nRead one with: python3 scripts/mail_read.py {box.path} --message {t['members'][-1]['n']}")
        return 0
    sel = [t for t in threads if t["count"] >= a.min]
    if a.sort != "last":
        key = {"first": lambda t: t["first"] or "", "count": lambda t: -t["count"], "people": lambda t: -len(t["people"]), "bytes": lambda t: -sum(int(r["size"] or 0) for r in t["members"])}[a.sort]
        sel = sorted(sel, key=key)
    page = sel[a.offset : a.offset + a.limit]
    nxt = script_cmd({"--offset": str(a.offset + a.limit)}) if a.offset + a.limit < len(sel) else None
    data_rows = [[t["id"], t["count"], human_size(sum(int(r["size"] or 0) for r in t["members"])), (t["first"] or "")[:10], (t["last"] or "")[:10], trunc(t["subject"], 70), trunc(", ".join(t["people"][:4]) + (f" +{len(t['people']) - 4}" if len(t["people"]) > 4 else ""), 80), ",".join(str(r["n"]) for r in t["members"][:12]) + ("…" if t["count"] > 12 else "")] for t in page]
    if a.format == "json":
        emit({"threads": len(sel), "all_threads": len(threads), "sort": a.sort, "rows": [dict(zip(["id", "count", "size", "first", "last", "subject", "people", "messages"], r)) for r in data_rows], "next": nxt}, "json", max_chars=None)
        return 0
    order = {"last": "newest first", "first": "oldest first", "count": "most messages first", "people": "most people first", "bytes": "largest first"}[a.sort]
    print(f"{len(sel):,} thread(s) with at least {a.min} messages ({len(threads):,} conversations in all, {order}).")
    print(table(["thread", "msgs", "size", "first", "last", "subject", "people", "messages"], data_rows, auto_format(a.format, len(data_rows))).rstrip())
    if nxt:
        print(f"\n[Next: {nxt}]")
    if page:
        print(f"\nShow one as a tree: python3 scripts/mbox_tool.py threads {box.path} --thread {page[0]['id']}")
    return 0


# ── stats ───────────────────────────────────────────────────────────────


def cmd_stats(box, a) -> int:
    import json
    from collections import Counter
    from datetime import datetime

    senders: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    recips: Counter[str] = Counter()
    months: Counter[str] = Counter()
    weekdays: Counter[str] = Counter()
    hours: Counter[int] = Counter()
    types: Counter[str] = Counter()
    sizes = []
    import re

    for r in box.rows():
        if r["from_addr"]:
            senders[r["from_addr"]] += 1
            domains[r["from_addr"].rsplit("@", 1)[-1]] += 1
        for addr in re.findall(r"(?<![\w.+'-])[\w.+'-]+@[\w.-]+", (r["to_addrs"] or "") + "," + (r["cc_addrs"] or "")):  # starts only at a word's start: linear
            recips[addr.lower()] += 1
        if r["date"]:
            try:
                d = datetime.fromisoformat(r["date"])
                months[d.strftime("%Y-%m")] += 1
                weekdays[d.strftime("%a")] += 1
                hours[d.hour] += 1
            except ValueError:
                pass
        sizes.append((r["size"] or 0, r["n"], r["subject"] or ""))
        for name, ctype, size, inline in json.loads(r["att"] or "[]"):
            if not inline:
                types[Path(name).suffix.lower() if name and "." in name else ctype] += 1
    sizes.sort(reverse=True)
    total = sum(s for s, _, _ in sizes)
    data = {
        "messages": len(sizes),
        "total_bytes": total,
        "median_bytes": sorted(s for s, _, _ in sizes)[len(sizes) // 2] if sizes else 0,
        "top_senders": senders.most_common(a.top),
        "top_sender_domains": domains.most_common(a.top),
        "top_recipients": recips.most_common(a.top),
        "per_month": dict(sorted(months.items())),
        "per_weekday": {d: weekdays.get(d, 0) for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")},
        "per_hour_local": {h: hours.get(h, 0) for h in range(24)},
        "largest": [{"n": n, "bytes": s, "subject": subj} for s, n, subj in sizes[: a.top]],
        "attachment_types": types.most_common(a.top),
    }
    if a.format == "json":
        emit(data, "json", max_chars=None)
        return 0
    out = [f"# Statistics for {box.path.name}: {len(sizes):,} messages, {human_size(total)}", ""]
    for title, key, h in (("Top senders", "top_senders", "from"), ("Sender domains", "top_sender_domains", "domain"), ("Top recipients", "top_recipients", "to/cc")):
        out += [f"## {title}", "", md_table([h, "messages"], data[key]), ""]
    months_sorted = sorted(months.items())
    if len(months_sorted) > 36:
        per_year: Counter[str] = Counter()
        for k, v in months_sorted:
            per_year[k[:4]] += v
        out += ["## Per year", "", md_table(["year", "messages"], sorted(per_year.items())), ""]
    else:
        out += ["## Per month", "", md_table(["month", "messages"], months_sorted), ""]
    out += ["## Weekdays (sender's local time)", "", md_table(list(data["per_weekday"]), [list(data["per_weekday"].values())]), ""]
    out += ["## Hours (sender's local time)", "", md_table([f"{h:02d}" for h in range(24)], [[hours.get(h, 0) for h in range(24)]]), ""]
    out += ["## Largest messages", "", md_table(["#", "size", "subject"], [[f"#{n}", human_size(s), subj[:70]] for s, n, subj in sizes[: a.top]]), ""]
    if types:
        out += ["## Attachment types", "", md_table(["type", "files"], types.most_common(a.top))]
    print("\n".join(out).rstrip())
    return 0


if __name__ == "__main__":
    run_main(main)
