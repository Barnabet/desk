#!/usr/bin/env python3
"""The research ledger: every source you use, its saved copy, quotes checked against that copy, and the citations.
The ledger is sources.json in the current folder (or --ledger). Standard library only; it never goes online: save
pages with web_fetch (save: true) and pass their path with --saved.

Examples:
  python3 scripts/sources.py add --url https://example.org/report --title "Annual report" --site "Example" \\
      --author "Doe, J." --published 2026-03-02 --saved web/pages/example-org-report-1a2b3c4d.md
  python3 scripts/sources.py check-quote 1 "revenue grew by 12 percent"
  python3 scripts/sources.py list
  python3 scripts/sources.py cite --style apa
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, atomic_write, parser, run_main

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MLA_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
# Curly quotes, dashes and Markdown emphasis differ between a page and a quote of it; neither changes the words.
FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " ", "*": None, "_": None, "`": None})


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SkillError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise SkillError(f"{path} is not a sources list")
    return data


def save(path: Path, sources: list[dict[str, Any]]) -> None:
    atomic_write(path, (json.dumps(sources, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def find(sources: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    for s in sources:
        if str(s.get("id")) == ref.lstrip("[").rstrip("]"):
            return s
    raise SkillError(f"no source [{ref}] in the ledger (see: sources.py list)")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(FOLD)).strip()


def parse_date(value: str | None) -> tuple[int, int | None, int | None] | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", value.strip())
    if not m:
        raise UsageError(f"dates are YYYY, YYYY-MM or YYYY-MM-DD, not {value!r}")
    y, mo, d = (int(g) if g else None for g in m.groups())
    if mo is not None and not 1 <= mo <= 12:
        raise UsageError(f"bad month in {value!r}")
    return y, mo, d  # type: ignore[return-value]


def apa_date(value: str | None) -> str:
    p = parse_date(value)
    if not p:
        return "n.d."
    y, mo, d = p
    return f"{y}, {MONTHS[mo - 1]}{f' {d}' if d else ''}" if mo else str(y)


def mla_date(value: str | None) -> str:
    p = parse_date(value)
    if not p:
        return ""
    y, mo, d = p
    return " ".join(x for x in [str(d) if d else "", MLA_MONTHS[mo - 1] if mo else "", str(y)] if x)


def with_period(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else f"{text}."


def cite(s: dict[str, Any], style: str) -> str:
    title, url, site, author = s["title"], s["url"], s.get("site") or "", s.get("author") or ""
    if style == "apa":
        parts = [f"{with_period(author)} ({apa_date(s.get('published'))}).", with_period(title)] if author else [with_period(title), f"({apa_date(s.get('published'))})."]
        if site:
            parts.append(with_period(site))
        return " ".join([*parts, url])
    if style == "mla":
        pieces = [x for x in [site, mla_date(s.get("published")), url] if x]
        accessed = mla_date(s.get("accessed"))
        head = f"{with_period(author)} " if author else ""
        return f'{head}"{with_period(title)}" {", ".join(pieces)}.{f" Accessed {accessed}." if accessed else ""}'
    accessed = s.get("accessed")
    site_part = f" {with_period(site)}" if site else ""
    accessed_part = f" (accessed {accessed})" if accessed else ""
    return f"[{s['id']}] {with_period(title)}{site_part} {url}{accessed_part}."


def cmd_add(args, path: Path) -> int:
    sources = load(path)
    if args.saved and not Path(args.saved).is_file():
        raise SkillError(f"no saved copy at {args.saved}")
    parse_date(args.published)
    parse_date(args.accessed)
    entry: dict[str, Any] = {
        "url": args.url,
        "title": args.title,
        "site": args.site,
        "author": args.author,
        "published": args.published,
        "accessed": args.accessed or dt.date.today().isoformat(),
        "saved": args.saved,
    }
    entry = {k: v for k, v in entry.items() if v}
    existing = next((s for s in sources if s.get("url") == args.url), None)
    if existing:
        existing.update(entry)
        save(path, sources)
        print(f"Updated [{existing['id']}] {existing['title']}")
        return 0
    entry = {"id": max((int(s.get("id", 0)) for s in sources), default=0) + 1, **entry}
    sources.append(entry)
    save(path, sources)
    print(f"Added [{entry['id']}] {entry['title']}{'' if args.saved else ' (no saved copy: quotes cannot be checked)'}")
    return 0


def cmd_check(args, path: Path) -> int:
    s = find(load(path), args.id)
    saved = s.get("saved")
    if not saved or not Path(saved).is_file():
        raise SkillError(f"[{s['id']}] has no saved copy; fetch it with web_fetch save: true, then sources.py add --url {s['url']} --title ... --saved PATH")
    text = Path(saved).read_text(encoding="utf-8", errors="replace")
    quote = norm(args.quote)
    if len(quote) < 8:
        raise UsageError("quote at least a few words")
    lines = text.splitlines()
    if quote in norm(text):
        line = next((i + 1 for i, ln in enumerate(lines) if quote[:40] in norm(ln)), None)
        print(f"Found in [{s['id']}] {s['title']}{f' (line {line})' if line else ''}.")
        return 0
    best = max(lines, key=lambda ln: difflib.SequenceMatcher(None, quote, norm(ln)).ratio(), default="")
    print(f"Not found in [{s['id']}] {s['title']} ({saved}).\nClosest line: {norm(best)[:300]}")
    return 1


def cmd_list(_args, path: Path) -> int:
    sources = load(path)
    if not sources:
        print(f"No sources yet in {path}.")
        return 0
    for s in sources:
        saved = f" · saved {s['saved']}" if s.get("saved") else ""
        print(f"[{s['id']}] {s['title']} · {s['url']}{saved}")
    return 0


def cmd_cite(args, path: Path) -> int:
    sources = load(path)
    chosen = [find(sources, r) for r in args.ids] if args.ids else sources
    if not chosen:
        raise SkillError(f"no sources in {path}")
    for s in chosen:
        print(cite(s, args.style))
    return 0


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Examples:"):])
    p.add_argument("--ledger", default="sources.json", help="the ledger file (default ./sources.json)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="record a source (the same URL again updates it)")
    a.add_argument("--url", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--site", help="the site or publisher, e.g. 'Reuters'")
    a.add_argument("--author", help="'Last, F.' or an organisation")
    a.add_argument("--published", help="YYYY, YYYY-MM or YYYY-MM-DD")
    a.add_argument("--accessed", help="YYYY-MM-DD (default today)")
    a.add_argument("--saved", help="the saved copy (web_fetch save: true) to check quotes against")
    c = sub.add_parser("check-quote", help="check that a quote appears in a source's saved copy (exit 1 if not)")
    c.add_argument("id")
    c.add_argument("quote")
    sub.add_parser("list", help="every source in the ledger")
    ci = sub.add_parser("cite", help="citations for all sources, or the given ids")
    ci.add_argument("ids", nargs="*")
    ci.add_argument("--style", choices=["numbered", "apa", "mla"], default="numbered")
    args = p.parse_args()
    path = Path(args.ledger)
    return {"add": cmd_add, "check-quote": cmd_check, "list": cmd_list, "cite": cmd_cite}[args.cmd](args, path)


if __name__ == "__main__":
    run_main(main)
