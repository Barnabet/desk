#!/usr/bin/env python3
"""Outline, read and search the pages Desk's web tools saved: web_fetch with save: true writes web/pages/*.md, and
web_crawl writes a folder under web/crawls/ with one .md per page and an index.json. Standard library only.

Examples:
  python3 scripts/pages.py outline web/pages/docs-site-org-guide-1a2b3c4d.md
  python3 scripts/pages.py outline web/crawls/docs.site.org-20261028-120000
  python3 scripts/pages.py section web/pages/docs-site-org-guide-1a2b3c4d.md "installation"
  python3 scripts/pages.py grep "rate limit" web/crawls/docs.site.org-20261028-120000 --context 2
  python3 scripts/pages.py list web/crawls/docs.site.org-20261028-120000
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, parser, run_main
from _paging import add_paging, check_paging, page_text

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def md_files(path: str) -> tuple[Path, list[Path]]:
    """The .md files at `path` (one file, or every .md under a folder) and the folder names are shown relative to."""
    p = Path(path)
    if p.is_file():
        return p.parent, [p]
    if p.is_dir():
        files = sorted(f for f in p.rglob("*.md") if f.is_file())
        if not files:
            raise SkillError(f"no .md pages under {path}")
        return p, files
    raise SkillError(f"no such file or folder: {path}")


def read(f: Path) -> str:
    return f.read_text(encoding="utf-8", errors="replace")


def headings(text: str) -> list[tuple[int, int, str]]:
    """(line number, level, title) of each Markdown heading, skipping fenced code."""
    out: list[tuple[int, int, str]] = []
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else HEADING.match(line)
        if m:
            out.append((n, len(m.group(1)), m.group(2)))
    return out


def title_of(text: str) -> str:
    """The `Title:` line the web tools write, else the first heading."""
    for line in text.splitlines()[:5]:
        if line.startswith("Title: "):
            return line[len("Title: "):].strip()
    hs = headings(text)
    return hs[0][2] if hs else ""


def cmd_outline(args) -> int:
    base, files = md_files(args.path)
    if len(files) == 1 and Path(args.path).is_file():
        text = read(files[0])
        rows = [f"{'  ' * (level - 1)}{'#' * level} {title}  (line {n})" for n, level, title in headings(text)]
        head = f"{files[0].name}: {title_of(text) or '(untitled)'} · {len(text):,} characters · {len(rows)} headings"
        what = "headings"
    else:
        rows = []
        for f in files:
            text = read(f)
            rows.append(f"{f.relative_to(base).as_posix()}: {title_of(text) or '(untitled)'} · {len(headings(text))} headings · {len(text):,} characters")
        head = f"{len(files)} pages in {args.path}"
        what = "pages"
    if not rows:
        print(f"{head}\n(no headings: read it with section or grep)")
        return 0
    print(page_text(head, rows[args.offset:][: args.limit], args.offset, len(rows), args.max_chars, what))
    return 0


def cmd_section(args) -> int:
    _, files = md_files(args.file)
    text = read(files[0])
    lines = text.splitlines()
    hs = headings(text)
    want = args.heading.casefold()
    match = next((h for h in hs if want in h[2].casefold()), None)
    if not match:
        known = "; ".join(h[2] for h in hs[:20]) or "none"
        raise SkillError(f"no heading contains {args.heading!r}. Headings: {known}")
    start, level, _ = match
    end = next((n for n, lv, _ in hs if n > start and lv <= level), len(lines) + 1)
    print(cap("\n".join(lines[start - 1 : end - 1]).rstrip(), args.max_chars, "Narrow it with grep, or read a subsection."))
    return 0


def cmd_grep(args) -> int:
    base, files = md_files(args.path)
    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(args.pattern if args.regex else re.escape(args.pattern), flags)
    except re.error as e:
        raise UsageError(f"bad regex: {e}") from e
    blocks: list[str] = []
    for f in files:
        lines = read(f).splitlines()
        name = f.relative_to(base).as_posix()
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            lo, hi = max(0, i - args.context), min(len(lines), i + args.context + 1)
            blocks.append("\n".join(f"{name}:{j + 1}{':' if j == i else '-'} {lines[j]}" for j in range(lo, hi)))
    if not blocks:
        print(f"No matches for {args.pattern!r} in {args.path}")
        return 1
    head = f"{len(blocks)} matches for {args.pattern!r}"
    print(page_text(head, blocks[args.offset:][: args.limit], args.offset, len(blocks), args.max_chars, "matches"))
    return 0


def cmd_list(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        raise SkillError(f"no such folder: {args.folder}")
    index = folder / "index.json"
    rows: list[str] = []
    if index.is_file():
        try:
            entries = json.loads(index.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SkillError(f"unreadable index.json: {e}") from e
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict) and e.get("file"):
                rows.append(f"{e['file']} · {e.get('url', '')} · {e.get('title') or '(untitled)'} · {e.get('chars', '?')} chars")
    else:
        _, files = md_files(args.folder)
        rows = [f"{f.relative_to(folder).as_posix()} · {title_of(read(f)) or '(untitled)'}" for f in files]
    if not rows:
        raise SkillError(f"no pages listed in {args.folder}")
    print(page_text(f"{len(rows)} pages in {args.folder}", rows[args.offset:][: args.limit], args.offset, len(rows), args.max_chars, "pages"))
    return 0


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Examples:"):])
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("outline", help="headings of a page, or each page of a folder")
    o.add_argument("path")
    add_paging(o, "headings")
    s = sub.add_parser("section", help="one section of a page, by (part of) its heading")
    s.add_argument("file")
    s.add_argument("heading")
    s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    g = sub.add_parser("grep", help="lines matching a phrase (or --regex) in a page or every page of a folder")
    g.add_argument("pattern")
    g.add_argument("path")
    g.add_argument("--context", type=int, default=0, help="lines of context around each match")
    g.add_argument("--regex", action="store_true", help="treat the pattern as a regular expression")
    g.add_argument("--case-sensitive", action="store_true")
    add_paging(g, "matches")
    ls = sub.add_parser("list", help="the pages of a crawl folder, from its index.json")
    ls.add_argument("folder")
    add_paging(ls, "pages")
    args = p.parse_args()
    if args.cmd != "section":
        check_paging(args)
    if args.cmd == "grep" and args.context < 0:
        raise UsageError("--context is 0 or more")
    return {"outline": cmd_outline, "section": cmd_section, "grep": cmd_grep, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    run_main(main)
