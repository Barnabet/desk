"""A map of a big text file, for text_tool.py map: its outline with stable addresses, never a dump.

The outline is whatever structure the file has: Markdown/Org/AsciiDoc/LaTeX headings, SQL statements (CREATE, INSERT,
COPY, ALTER) grouped by table, top-level definitions in source code, chapters in books, or any --pattern. Repeated
markers in a row (a million INSERTs into one table) collapse into one run. Every item carries its line numbers and
byte offset, which `slice --lines` and `slice --bytes` read directly. A file without structure (a log, a CSV) gets
equal sections whose first lines show where each one starts (for a log: its timestamps).

The scan streams the file in newline-aligned chunks, splits big files across processes, and is cached by content
fingerprint. Bytes only, so ASCII-compatible encodings (UTF-8, Latin-1, cp125x...). Standard library only.
"""

from __future__ import annotations

import os
import re
from typing import Any

from _textenc import Encoding

VERSION = "2"
MAX_MARKERS = 200_000  # markers kept per scan range; runs collapse them, and the total is always counted
PREVIEW = 160

_CODE_TOP = (
    rb"^(?:async[ \t]+)?(?:def|class)[ \t]+\w+"
    rb"|^(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?function[ \t]*\*?[ \t]*\w+"
    rb"|^(?:export[ \t]+)?(?:default[ \t]+)?(?:abstract[ \t]+)?class[ \t]+\w+"
    rb"|^(?:export[ \t]+)?(?:interface|type|enum)[ \t]+\w+"
    rb"|^func[ \t]+(?:\([^)\n]*\)[ \t]*)?\w+"
    rb"|^(?:pub(?:\([\w:]+\))?[ \t]+)?(?:async[ \t]+)?(?:fn|struct|enum|trait|impl|mod)\b[^\n{;]*"
    rb"|^(?:public|private|protected|internal|static)[ \t][^\n;=]*\("
    rb"|^(?:module|package|namespace)[ \t]+[\w.:]+"
)

#: kind -> (description, pattern). Group 0 is the marker line (or its start); labels are derived in _label().
KINDS: dict[str, tuple[str, bytes]] = {
    "markdown": ("Markdown headings", rb"^(?:#{1,6}[ \t]+\S[^\n]*|```|~~~)"),
    "org": ("Org headings", rb"^\*{1,6}[ \t]+\S[^\n]*"),
    "asciidoc": ("AsciiDoc headings", rb"^={1,6}[ \t]+\S[^\n]*"),
    "latex": ("LaTeX sections", rb"^[ \t]*\\(?:part|chapter|section|subsection|subsubsection)\*?\{[^\n]*"),
    "sql": ("SQL statements", rb"(?i)^[ \t]*(?:CREATE[ \t]+(?:OR[ \t]+REPLACE[ \t]+)?(?:TEMP(?:ORARY)?[ \t]+)?(?:TABLE|VIEW|INDEX|UNIQUE[ \t]+INDEX|FUNCTION|PROCEDURE|TRIGGER|SCHEMA|DATABASE|SEQUENCE|TYPE)|INSERT[ \t]+(?:IGNORE[ \t]+)?INTO|REPLACE[ \t]+INTO|COPY|ALTER[ \t]+TABLE|DROP[ \t]+(?:TABLE|VIEW|INDEX|DATABASE)|UPDATE|DELETE[ \t]+FROM|USE)\b[^\n]*"),
    "code": ("top-level definitions", _CODE_TOP),
    "chapters": ("chapters and parts", b""),  # built below: _chapter_pattern()
}


def _chapter_pattern() -> bytes:
    """Chapter, part, book, act and letter headings in English, French, Spanish, Italian, Portuguese, German, Dutch,
    Polish and Russian, followed by a number, a Roman numeral, a capitalised word or an ordinal ("Chapitre premier").
    Each word is matched as UTF-8 and in its legacy code page (cp1252, or cp1251 for Russian)."""
    words = ("Chapter Part Book Section Act Letter Volume Chapitre Partie Livre Tome Acte Lettre Cap\u00edtulo Parte Libro "
             "Capitolo Kapitel Teil Buch Akt Hoofdstuk Deel Boek Rozdzia\u0142 Cz\u0119\u015b\u0107 \u0413\u043b\u0430\u0432\u0430 "
             "\u0427\u0430\u0441\u0442\u044c \u041a\u043d\u0438\u0433\u0430").split()
    alts: list[bytes] = []
    for w in words:
        for form in (w, w.upper()):
            for enc in ("utf-8", "cp1251" if "\u0400" <= w[0] <= "\u04ff" else "cp1252", "cp1250"):
                try:
                    b = re.escape(form.encode(enc))
                except UnicodeEncodeError:
                    continue
                if b not in alts:
                    alts.append(b)
    number = rb"(?:[0-9]+|[IVXLCDM]+\b|[A-Z][a-z]+|[A-Z]{2,}|premi\S*|first|second|third|erste\S*|primer\S*|prim[oa]\b|pierwsz\S*|\xd0\xbf\xd0\xb5\xd1\x80\xd0\xb2\S*)"
    return rb"^[ \t]*(?:" + b"|".join(alts) + rb")[ \t]+" + number + rb"[^\n]{0,120}$"


KINDS["chapters"] = (KINDS["chapters"][0], _chapter_pattern())

_SQL_NAME = re.compile(rb"(?i)^[ \t]*(CREATE[ \t]+(?:OR[ \t]+REPLACE[ \t]+)?(?:TEMP(?:ORARY)?[ \t]+)?(?:UNIQUE[ \t]+)?(\w+)(?:[ \t]+IF[ \t]+NOT[ \t]+EXISTS)?|INSERT[ \t]+(?:IGNORE[ \t]+)?INTO|REPLACE[ \t]+INTO|COPY|ALTER[ \t]+TABLE(?:[ \t]+ONLY)?|DROP[ \t]+(\w+)(?:[ \t]+IF[ \t]+EXISTS)?|UPDATE|DELETE[ \t]+FROM|USE)[ \t]+[`\"\[]?([\w.$-]+)")


def pick_kind(path: str, head: bytes) -> str:
    """The outline kind for a file, from its extension and first bytes; 'sections' when it has no structure."""
    from _types import CODE_EXTS, ext_of

    ext = ext_of(os.path.basename(path))
    if ext in (".md", ".markdown", ".mdown", ".mkd", ".mdx"):
        return "markdown"
    if ext == ".org":
        return "org"
    if ext in (".adoc", ".asciidoc"):
        return "asciidoc"
    if ext in (".tex", ".ltx", ".latex"):
        return "latex"
    if ext == ".sql" or re.search(rb"(?im)^(?:CREATE TABLE|INSERT INTO|COPY \w+ .*FROM stdin)", head[:65536]):
        return "sql"
    if ext in CODE_EXTS:
        return "code"
    text_like = ext in ("", ".txt", ".text")
    if text_like and len(re.findall(KINDS["chapters"][1], head, re.M)) >= 2:
        return "chapters"
    if text_like and len(re.findall(rb"^#{1,3}[ \t]+\S", head, re.M)) >= 3:
        return "markdown"
    return "sections"


def _scan(job: tuple[str, int, int, bytes, int]) -> tuple[int, int, list[tuple[int, int, bytes]], int]:
    """(range start, newlines in range, [(offset, line within range (1-based), marker bytes)], markers found)."""
    from _textops import chunks

    path, start, end, pattern, flags = job
    rx = re.compile(pattern, flags)
    lines_before = 0
    found: list[tuple[int, int, bytes]] = []
    total = 0
    for off, buf in chunks(path, start, end):
        last = 0
        count = lines_before
        for m in rx.finditer(buf):
            total += 1
            if len(found) >= MAX_MARKERS:
                continue
            count += buf.count(b"\n", last, m.start())
            last = m.start()
            eol = buf.find(b"\n", m.start(), m.start() + 400)
            found.append((off + m.start(), count + 1, buf[m.start() : eol if eol >= 0 else m.start() + 400].rstrip(b"\r")))
        lines_before += buf.count(b"\n")
    return start, lines_before, found, total


def _label(kind: str, raw: bytes, enc: Encoding) -> tuple[str, str, int]:
    """(group key, label, level) for a marker line; consecutive markers with the same group key become one run."""
    text = raw.decode(enc.name if enc.name != "ascii" else "utf-8", "replace").strip()
    if kind == "markdown":
        if text.startswith(("```", "~~~")):
            return "fence", text, 0
        level = len(text) - len(text.lstrip("#"))
        return f"h:{text}", text[:PREVIEW], level  # the label keeps its #s: they show the level in a flat table
    if kind in ("org", "asciidoc"):
        ch = "*" if kind == "org" else "="
        level = len(text) - len(text.lstrip(ch))
        return f"h:{text}", text[:PREVIEW], level
    if kind == "latex":
        m = re.match(r"\\(\w+)\*?\{(.*)", text)
        levels = {"part": 1, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}
        return f"h:{text}", text[:PREVIEW], levels.get(m.group(1), 2) if m else 2
    if kind == "sql":
        m = _SQL_NAME.match(raw)
        if m:
            verb = re.sub(r"\s+", " ", m.group(1).decode("ascii", "replace").upper())
            verb = re.sub(r" IF (NOT )?EXISTS$", "", verb)
            name = m.group(4).decode("utf-8", "replace").strip("`\"[]")
            statement = verb.split(" (")[0]
            if statement.startswith(("INSERT", "REPLACE", "COPY")):
                return f"{statement}:{name}", f"{statement} {name}", 2
            return f"{text[:80]}", f"{statement} {name}", 1
        return text[:80], text[:PREVIEW], 1
    return f"{text[:200]}", text[:PREVIEW], 1


def build_map(path: str, enc: Encoding, kind: str, pattern: str | None, ignore_case: bool, workers: int | None, use_cache: bool) -> dict[str, Any]:
    """The outline runs of a file (cached for files over 8 MB)."""

    def compute() -> dict[str, Any]:
        return _build(path, enc, kind, pattern, ignore_case, workers)

    size = os.path.getsize(path)
    if use_cache and size >= 8 * 1024 * 1024:
        from _cache import cached_json

        params = {"kind": kind, "pattern": pattern, "i": ignore_case, "enc": enc.name}
        return cached_json(path, "fi-textmap", params, VERSION, compute)
    return compute()


def _build(path: str, enc: Encoding, kind: str, pattern: str | None, ignore_case: bool, workers: int | None) -> dict[str, Any]:
    from _common import UsageError, pool_map, workers_for
    from _textops import PARALLEL_MIN, aligned_ranges

    if pattern:
        try:
            pat = pattern.encode(enc.name if enc.name != "ascii" else "utf-8")
            re.compile(pat)
        except (re.error, UnicodeEncodeError, LookupError) as e:
            raise UsageError(f"bad --pattern: {e}") from e
        # a pattern matches anywhere in a line; the marker is the whole line
        pat = rb"^[^\n]*?(?:" + pat + rb")[^\n]*"
        desc = f"lines matching {pattern}"
    else:
        desc, pat = KINDS[kind]
    flags = re.M | (re.I if ignore_case else 0)
    size = os.path.getsize(path)
    parts = workers_for(8, workers) if size >= PARALLEL_MIN else 1
    outs = pool_map(_scan, [(path, s, e, pat, flags) for s, e in aligned_ranges(path, parts)], workers=parts)
    outs.sort(key=lambda r: r[0])
    markers: list[tuple[int, int, bytes]] = []
    base = 0
    total = 0
    for _start, newlines, found, n in outs:
        markers.extend((off, base + line, raw) for off, line, raw in found)
        base += newlines
        total += n
    in_fence = False
    runs: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for off, line, raw in markers:
        key, label, level = _label("pattern" if pattern else kind, raw, enc)
        if key == "fence":
            in_fence = not in_fence
            continue
        if in_fence:
            continue  # "# comment" lines inside a Markdown code block are not headings
        if kind == "sql" and not pattern:
            statement = label.rsplit(" ", 1)[0]
            counts[statement] = counts.get(statement, 0) + 1
        prev = runs[-1] if runs else None
        if prev is not None and prev["key"] == key and kind == "sql" and not pattern and key.startswith(("INSERT", "REPLACE", "COPY")):
            prev["end_line"] = line
            prev["count"] += 1
            continue
        runs.append({"key": key, "line": line, "end_line": line, "offset": off, "label": label, "level": level, "count": 1})
    for r in runs:
        r.pop("key")
    return {"kind": "pattern" if pattern else kind, "description": desc, "markers": total, "kept": len(markers), "runs": runs, "statements": counts}


def sections(path: str, idx: dict[str, Any], n: int, enc: Encoding) -> list[dict[str, Any]]:
    """n equal line ranges with the byte offset and first line of each (seeks through the line index: instant)."""
    from _textops import seek_line

    total = idx["lines"]
    if total <= 0:
        return []
    n = max(1, min(n, total))
    step = -(-total // n)
    out = []
    with open(path, "rb") as f:
        for a in range(1, total + 1, step):
            b = min(total, a + step - 1)
            off = seek_line(path, a, idx)
            f.seek(off)
            first = f.readline(PREVIEW * 4).rstrip(b"\r\n")
            out.append({"lines": [a, b], "offset": off, "first_line": first.decode(enc.name if enc.name != "ascii" else "utf-8", "replace")[:PREVIEW]})
    size = os.path.getsize(path)
    for i, s in enumerate(out):
        s["bytes"] = (out[i + 1]["offset"] if i + 1 < len(out) else size) - s["offset"]
    return out
