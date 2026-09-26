"""Output helpers for the email-calendar scripts: paging with exact continuation commands, tables as Markdown,
CSV or JSON, and the "stable address" format. Standard library only.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

from _common import DEFAULT_MAX_CHARS, _json_default, md_table


def script_cmd(extra: dict[str, str | None] | None = None, drop: Sequence[str] = ()) -> str:
    """The command line that ran this script, with some options replaced — to tell the agent how to continue."""
    argv = list(sys.argv[1:])
    extra = extra or {}
    out: list[str] = []
    skip = False
    names = set(extra) | set(drop)
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        key = a.split("=", 1)[0]
        if key in names:
            if "=" not in a and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                skip = True
            continue
        out.append(a)
    for k, v in extra.items():
        if v is None:
            continue
        out += [k, v] if v != "" else [k]
    script = "scripts/" + Path(sys.argv[0]).name
    quote = (lambda s: s if s and all(c.isalnum() or c in "-_./:#=,+@" for c in s) else '"' + s.replace('"', '\\"') + '"') if os.name == "nt" else shlex.quote
    return "python3 " + " ".join(quote(x) for x in [script, *out])


def page_text(text: str, offset: int, max_chars: int, what: str = "characters", header_lines: int = 0) -> str:
    """A window of `text` starting at offset; says what was left out and how to read the next part.

    `header_lines` (for CSV) repeats the first lines of the text at the top of every later part."""
    total = len(text)
    if offset >= total and total:
        return f"[nothing more: the output has {total} {what}]"
    end = min(total, offset + max_chars) if max_chars else total
    # Cut at a line break when one is near, so tables and paragraphs stay whole.
    if end < total:
        nl = text.rfind("\n", offset + max_chars // 2, end)
        if nl > offset:
            end = nl + 1
    body = text[offset:end]
    notes = []
    if offset:
        notes.append(f"[… skipped the first {offset} {what}]")
    if end < total:
        notes.append(f"[… {total - end} more {what} not shown. Next part: {script_cmd({'--offset': str(end)})}]")
    if offset and header_lines:
        head = "\n".join(text.split("\n", header_lines)[:header_lines])
        if not body.startswith(head):
            body = head + "\n" + body
    return ("\n".join(notes[:1]) + "\n" if offset else "") + body + ("\n" + notes[-1] if end < total else "")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], fmt: str) -> str:
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(headers)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
        return buf.getvalue()
    if fmt == "json":
        return json.dumps([dict(zip(headers, r)) for r in rows], ensure_ascii=False, indent=2, default=_json_default)
    return md_table(headers, rows)


def auto_format(fmt: str, n_rows: int, small: int = 40) -> str:
    """'auto' → Markdown for small extracts, CSV for medium and large ones (far fewer tokens)."""
    if fmt != "auto":
        return fmt
    return "md" if n_rows <= small else "csv"


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)


def trunc(s: Any, n: int) -> str:
    s = "" if s is None else str(s).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


MAX_CHARS = DEFAULT_MAX_CHARS
