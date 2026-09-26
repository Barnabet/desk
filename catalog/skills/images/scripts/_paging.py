"""Output budgets and continuation for long listings: --max-chars, --offset and --limit; print what fits, say what was
left out, and end with the exact command that reads the next part (spec 4b.3).

The source of truth is catalog/shared/_paging.py (first written for the images skill); `pnpm catalog:sync` copies it
into skills that use it (edit it there). Standard library only.

    add_paging(parser, "files")            # --max-chars, --offset, --limit
    check_paging(args)
    rows = all_rows[args.offset:][: args.limit]
    print(page_text(head, rows, args.offset, len(all_rows), args.max_chars, "files"))
    emit_json_page(items, args.offset, total, args.max_chars)   # JSON: never cut mid-item
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from _common import DEFAULT_MAX_CHARS, UsageError


def add_paging(p: Any, what: str = "items", limit_help: str | None = None) -> None:
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"text budget (default {DEFAULT_MAX_CHARS}); a longer listing stops at a row and ends with the command for the next part")
    p.add_argument("--offset", type=int, default=0, help=f"skip the first N {what} (the next-part command sets it)")
    p.add_argument("--limit", type=int, default=None, help=limit_help or f"at most N {what} (default: as many as fit in --max-chars)")


def check_paging(args: Any) -> None:
    if args.offset < 0:
        raise UsageError("--offset is 0 or more")
    if args.limit is not None and args.limit < 1:
        raise UsageError("--limit is 1 or more")
    if args.max_chars is not None and args.max_chars < 2000:
        raise UsageError("--max-chars is at least 2000")


def next_command(offset: int, drop: Sequence[str] = ("--offset",)) -> str:
    """This script's command line with --offset set to `offset` (the command that reads the next part)."""
    argv = sys.argv[1:]
    out: list[str] = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in drop:
            skip = True
            continue
        if any(a.startswith(d + "=") for d in drop):
            continue
        out.append(a)
    parts = ["python3", f"scripts/{Path(sys.argv[0]).name}", *out, "--offset", str(offset)]
    if os.name == "nt":  # cmd/PowerShell quoting; POSIX shells get shlex quoting
        import subprocess

        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def fit_rows(head: str, rows: Sequence[str], budget: int) -> int:
    """How many of `rows` fit after `head` within `budget` characters (at least one)."""
    used = len(head) + 1
    n = 0
    for r in rows:
        used += len(r) + 1
        if used > budget and n:
            break
        n += 1
    return n


def page_text(head: str, rows: Sequence[str], offset: int, total: int, max_chars: int | None, what: str = "rows", tail: str = "") -> str:
    """`head` + as many `rows` as fit + a continuation line; `rows` are the rows from `offset` on (already limited)."""
    budget = (max_chars or 10**12) - 400 - len(tail)
    n = fit_rows(head, rows, budget)
    shown = rows[:n]
    text = head + ("\n" if head and shown else "") + "\n".join(shown)
    end = offset + n
    if end < total:
        text += f"\n\n[{what} {offset + 1}-{end} of {total}; {total - end} more. Next part: {next_command(end)}]"
    elif offset and n:
        text += f"\n\n[{what} {offset + 1}-{end} of {total}: the last part]"
    if tail:
        text += "\n" + tail
    return text


def emit_json_page(items: list[Any], offset: int, total: int, max_chars: int | None, default: Callable[[Any], Any] | None = None) -> None:
    """Prints a JSON list of as many items as fit (never cut mid-item); a stderr note gives the next command."""
    budget = max_chars or 10**12
    n = len(items)
    text = json.dumps(items, ensure_ascii=False, indent=2, default=default or str)
    if len(text) > budget and items:
        lo, hi = 1, len(items)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(json.dumps(items[:mid], ensure_ascii=False, indent=2, default=default or str)) <= budget:
                lo = mid
            else:
                hi = mid - 1
        n = lo
        text = json.dumps(items[:n], ensure_ascii=False, indent=2, default=default or str)
    print(text)
    end = offset + n
    if end < total:
        print(f"[… items {offset + 1}-{end} of {total} (--max-chars {max_chars}). Next part: {next_command(end)}]", file=sys.stderr)
