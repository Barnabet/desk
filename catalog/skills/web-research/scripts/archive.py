#!/usr/bin/env python3
"""Web archives: the Wayback Machine (and Common Crawl) for pages that changed, vanished or block reading.

  nearest  the Wayback snapshot nearest a date (default: the latest)
  list     the captures of a URL (Wayback CDX, or Common Crawl's index), newest first
  read     an archived copy as Markdown, read like fetch.py (--outline, --section, --grep, --offset …)
  save     ask the Wayback Machine to capture a page. This publishes the page in a public archive, so it runs only
           with --yes, and only when the user asked for it

The CDX index is sometimes slow; when it times out, `nearest` still works. Archived text is framed as untrusted.

Examples:
  python3 scripts/archive.py nearest https://example.com/pricing --date 2023-06-01
  python3 scripts/archive.py list https://example.com/pricing --from 2022 --to 2024 --limit 20
  python3 scripts/archive.py list example.com/blog/ --match prefix --limit 50
  python3 scripts/archive.py read https://example.com/pricing --date 2023-06-01 --grep "per seat"
  python3 scripts/archive.py read https://example.com/pricing --source commoncrawl
  python3 scripts/archive.py save https://example.com/announcement --yes    # only when the user asked: it publishes
"""

from __future__ import annotations

import gzip
import json
import re
from urllib.parse import urlencode

from _common import SkillError, UsageError, add_format, parser, run_main


def _ts(date: str | None, end: bool = False) -> str | None:
    """'2023', '2023-06', '2023-06-01' → a Wayback timestamp prefix."""
    if not date:
        return None
    d = re.sub(r"\D", "", date)
    if len(d) not in (4, 6, 8, 10, 12, 14):
        raise UsageError(f"dates look like 2023, 2023-06 or 2023-06-01 (got {date!r})")
    return d


def _when(ts: str) -> str:
    ts = re.sub(r"\D", "", ts)
    if len(ts) >= 12:
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
    import _net

    return _net.ts_date(ts)


# ── Wayback ─────────────────────────────────────────────────────────────


def cmd_nearest(args) -> int:
    import _net

    url = _net.normalize_input_url(args.url)
    snap = _net.wayback_nearest(url, _ts(args.date))
    if not snap:
        print(f"The Wayback Machine has no capture of {url}." if args.format == "md" else json.dumps({"url": url, "snapshot": None}))
        return 1
    if args.format == "json":
        print(json.dumps({"url": url, **snap, "raw": _net.wayback_raw(snap["url"])}, indent=2))
    else:
        print(f"Nearest capture of {url}{' to ' + args.date if args.date else ''}: {_when(snap['timestamp'])} (HTTP {snap.get('status') or '?'})")
        print(f"Snapshot: {snap['url']}")
        print(f"Read it: python3 scripts/archive.py read {url}" + (f" --date {args.date}" if args.date else ""))
    return 0


def wayback_list(url: str, args) -> list[dict]:
    import _net

    q = {"url": url, "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest,length"}
    if args.match != "exact":
        q["matchType"] = args.match
    if args.collapse:
        q["collapse"] = "digest"
    f, t = _ts(args.from_), _ts(args.to)
    if f:
        q["from"] = f
    if t:
        q["to"] = t
    if args.status:
        q["filter"] = f"statuscode:{args.status}"
    q["limit"] = str(args.limit if args.oldest else -args.limit)
    api = "https://web.archive.org/cdx/search/cdx?" + urlencode(q)
    try:
        r = _net.request(api, kind="api", ttl=6 * 3600, timeout=args.timeout, retries=1, max_bytes=20 * 1024 * 1024)
    except _net.NetError as e:
        raise SkillError(f"the Wayback CDX index did not answer ({e.reason}); it is often slow. `archive.py nearest {url} --date …` still works") from None
    if not r.ok:
        raise SkillError(f"the Wayback CDX index answered HTTP {r.status}; try `archive.py nearest {url}` instead")
    text = r.text.strip()
    if not text:
        return []
    rows = json.loads(text)
    if not rows:
        return []
    head, *data = rows
    out = []
    for row in data:
        d = dict(zip(head, row))
        ts = d.get("timestamp", "")
        out.append({
            "timestamp": ts,
            "when": _when(ts),
            "url": d.get("original", ""),
            "status": d.get("statuscode", ""),
            "mime": d.get("mimetype", ""),
            "bytes": int(d["length"]) if str(d.get("length", "")).isdigit() else None,
            "snapshot": f"https://web.archive.org/web/{ts}/{d.get('original', '')}",
        })
    out.sort(key=lambda x: x["timestamp"], reverse=not args.oldest)
    return out


# ── Common Crawl ────────────────────────────────────────────────────────


def cc_indexes(n: int) -> list[dict]:
    import _net

    data = _net.get_json("https://index.commoncrawl.org/collinfo.json", ttl=24 * 3600, timeout=20)
    if not isinstance(data, list) or not data:
        raise SkillError("Common Crawl's index list is unavailable right now")
    return data[: max(1, n)]


def cc_list(url: str, args) -> list[dict]:
    import _net

    out = []
    for idx in cc_indexes(args.crawls):
        q = {"url": url, "output": "json", "limit": str(args.limit)}
        if args.match != "exact":
            q["matchType"] = args.match
        if args.status:
            q["filter"] = f"status:{args.status}"
        try:
            r = _net.request(idx["cdx-api"] + "?" + urlencode(q), kind="api", ttl=24 * 3600, timeout=args.timeout, retries=1)
        except _net.NetError as e:
            raise SkillError(f"Common Crawl's index did not answer ({e.reason})") from None
        if r.status == 404:
            continue
        if not r.ok:
            raise SkillError(f"Common Crawl's index answered HTTP {r.status}")
        for line in r.text.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append({"timestamp": d.get("timestamp", ""), "when": _when(d.get("timestamp", "")), "url": d.get("url", ""), "status": d.get("status", ""), "mime": d.get("mime", ""), "bytes": int(d["length"]) if str(d.get("length", "")).isdigit() else None, "crawl": idx.get("id", ""), "filename": d.get("filename"), "offset": d.get("offset"), "length": d.get("length")})
    out.sort(key=lambda x: x["timestamp"], reverse=True)
    return out[: args.limit]


def cc_fetch(rec: dict) -> tuple[bytes, str, str]:
    """(body, content type, target URL) of one Common Crawl WARC record, read with a byte-range request."""
    import _net

    start = int(rec["offset"])
    end = start + int(rec["length"]) - 1
    r = _net.request(f"https://data.commoncrawl.org/{rec['filename']}", kind="api", headers={"Range": f"bytes={start}-{end}"}, ttl=7 * 86400, timeout=40, max_bytes=50 * 1024 * 1024)
    if r.status not in (200, 206):
        raise SkillError(f"Common Crawl's data server answered HTTP {r.status}")
    try:
        raw = gzip.decompress(r.body)
    except (OSError, EOFError) as e:
        raise SkillError(f"the Common Crawl record is not readable ({e})") from None
    warc_head, _, rest = raw.partition(b"\r\n\r\n")
    target = ""
    m = re.search(rb"WARC-Target-URI:\s*(\S+)", warc_head, re.I)
    if m:
        target = m.group(1).decode("utf-8", "replace")
    http_head, _, body = rest.partition(b"\r\n\r\n")
    ctype = ""
    m = re.search(rb"(?im)^content-type:\s*([^\r\n]+)", http_head)
    if m:
        ctype = m.group(1).decode("latin-1").strip()
    if re.search(rb"(?im)^transfer-encoding:\s*chunked", http_head):
        body = _dechunk(body)
    if re.search(rb"(?im)^content-encoding:\s*gzip", http_head):
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            pass
    return body, ctype, target


def _dechunk(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        j = data.find(b"\r\n", i)
        if j < 0:
            break
        try:
            n = int(data[i:j].split(b";")[0], 16)
        except ValueError:
            return data
        if n == 0:
            break
        out += data[j + 2 : j + 2 + n]
        i = j + 2 + n + 2
    return bytes(out)


# ── commands ────────────────────────────────────────────────────────────


def cmd_list(args) -> int:
    import _net

    url = args.url if args.match != "exact" else _net.normalize_input_url(args.url)
    rows = cc_list(url, args) if args.source == "commoncrawl" else wayback_list(url, args)
    if args.format == "json":
        print(json.dumps({"url": url, "source": args.source, "count": len(rows), "captures": rows}, indent=2))
        return 0 if rows else 1
    if not rows:
        print(f"No captures of {url} in {args.source} with these filters.")
        return 1
    print(f"{len(rows)} captures of {url} ({args.source}, {'oldest' if args.oldest else 'newest'} first):")
    for r in rows:
        size = f" · {r['bytes'] / 1024:.0f} KB" if r.get("bytes") else ""
        where = r.get("snapshot") or f"crawl {r.get('crawl')}"
        print(f"- {r['when']} · {r['status'] or '-'} · {r['mime'] or '-'}{size} · {where}")
    print(f"Read one: python3 scripts/archive.py read {url if args.match == 'exact' else rows[0]['url']} --date {rows[0]['timestamp'][:8]}" + (" --source commoncrawl" if args.source == "commoncrawl" else ""))
    return 0


def cmd_read(args) -> int:
    import _doc
    import _net

    url = _net.normalize_input_url(args.url)
    if args.source == "commoncrawl":
        args.match, args.status, args.limit = "exact", "200", 5
        rows = cc_list(url, args)
        if args.date:
            want = _ts(args.date) or ""
            rows.sort(key=lambda r: abs(int((r["timestamp"] + "0" * 14)[:14]) - int((want + "0" * 14)[:14])))
        if not rows:
            raise SkillError(f"Common Crawl has no capture of {url} in its newest {args.crawls} crawl(s); try --crawls 3 or the Wayback Machine")
        rec = rows[0]
        body, ctype, target = cc_fetch(rec)
        doc = _doc.extract(body, ctype, url, target or url, full=args.full, inline_links=args.inline_links)
        date = _net.ts_date(rec["timestamp"])
        doc.archived = {"source": "commoncrawl", "date": date, "timestamp": rec["timestamp"], "snapshot": f"Common Crawl {rec['crawl']} ({rec['filename']})", "reason": ""}
        doc.notes.insert(0, f"Archived copy from {date} (Common Crawl {rec['crawl']}).")
        doc.fetched = _doc.iso()
        loaded = _doc.Loaded(doc, body, ctype)
    else:
        loaded = _doc.load_archived(url, when=_ts(args.date), full=args.full, inline_links=args.inline_links)
        if loaded is None:
            raise SkillError(f"the Wayback Machine has no capture of {url}" + (f" near {args.date}" if args.date else ""))
        if args.date and loaded.doc.archived:
            asked = _ts(args.date) or ""
            got = loaded.doc.archived["timestamp"]
            if got[: len(asked)] != asked:
                loaded.doc.notes.append(f"The nearest capture to {args.date} is from {_net.ts_date(got)}.")
    if args.save:
        from _common import output_dir

        files = _doc.save(loaded.doc, loaded.body, output_dir(args.save))
        print("Saved: " + ", ".join(str(f) for f in files))
    print(_doc.render(loaded.doc, args, args.format))
    return 0


def cmd_save(args) -> int:
    import _net

    url = _net.normalize_input_url(args.url)
    if not args.yes:
        raise UsageError("saving publishes the page in the Wayback Machine's public archive. Run with --yes only when the user asked for it; `archive.py nearest URL` shows existing captures")
    try:
        r = _net.request(f"https://web.archive.org/save/{url}", kind="api", cache=False, timeout=120, retries=0, interval=10)
    except _net.NetError as e:
        raise SkillError(f"the Wayback Machine did not answer ({e.reason})") from None
    if r.status == 429:
        raise SkillError("the Wayback Machine is rate-limiting captures right now (429); try again in a few minutes")
    if not r.ok:
        raise SkillError(f"the Wayback Machine refused the capture (HTTP {r.status})")
    snap = ""
    loc = r.headers.get("content-location") or ""
    if loc.startswith("/web/"):
        snap = "https://web.archive.org" + loc
    elif "/web/" in r.final_url:
        snap = r.final_url
    if not snap:
        m = re.search(r"/web/(\d{14})/", r.text[:20000])
        snap = f"https://web.archive.org/web/{m.group(1)}/{url}" if m else ""
    print(json.dumps({"url": url, "snapshot": snap or None}, indent=2) if args.format == "json" else (f"Captured: {snap}" if snap else "Capture requested; the Wayback Machine did not return the snapshot address yet: check `archive.py nearest` in a minute."))
    return 0


def build_parser():
    import _doc

    p = parser(__doc__.split("\n\n")[0], epilog=__doc__[__doc__.index("  nearest") :])
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nearest", help="the snapshot nearest a date")
    n.add_argument("url")
    n.add_argument("--date", help="2023, 2023-06 or 2023-06-01 (default: the latest)")
    add_format(n)
    ls = sub.add_parser("list", help="captures of a URL")
    ls.add_argument("url")
    ls.add_argument("--source", choices=["wayback", "commoncrawl"], default="wayback")
    ls.add_argument("--from", dest="from_", metavar="DATE", help="captures since this date")
    ls.add_argument("--to", metavar="DATE", help="captures until this date")
    ls.add_argument("--limit", type=int, default=30, help="at most this many (default 30)")
    ls.add_argument("--oldest", action="store_true", help="oldest first (default newest)")
    ls.add_argument("--match", choices=["exact", "prefix", "host", "domain"], default="exact", help="exact URL (default), or everything under a prefix, host or domain")
    ls.add_argument("--status", help="only this HTTP status (e.g. 200)")
    ls.add_argument("--collapse", action="store_true", help="Wayback: one row per distinct content (skip identical captures)")
    ls.add_argument("--crawls", type=int, default=1, help="Common Crawl: how many of the newest crawls to search (default 1)")
    ls.add_argument("--timeout", type=float, default=30, help="seconds to wait for the index (default 30)")
    add_format(ls)
    rd = sub.add_parser("read", help="an archived copy as Markdown")
    rd.add_argument("url")
    rd.add_argument("--date", help="the capture nearest this date (default: the latest)")
    rd.add_argument("--source", choices=["wayback", "commoncrawl"], default="wayback")
    rd.add_argument("--crawls", type=int, default=1, help="Common Crawl: how many of the newest crawls to search")
    rd.add_argument("--timeout", type=float, default=30)
    rd.add_argument("--save", metavar="DIR", help="also save the archived response and Markdown into DIR")
    _doc.add_read_args(rd)
    add_format(rd)
    sv = sub.add_parser("save", help="capture a page in the Wayback Machine (publishes it; needs --yes)")
    sv.add_argument("url")
    sv.add_argument("--yes", action="store_true", help="confirm: the user asked to publish this page to the archive")
    add_format(sv)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "limit", 1) < 1:
        raise UsageError("--limit must be ≥ 1")
    return {"nearest": cmd_nearest, "list": cmd_list, "read": cmd_read, "save": cmd_save}[args.cmd](args)


if __name__ == "__main__":
    run_main(main)
