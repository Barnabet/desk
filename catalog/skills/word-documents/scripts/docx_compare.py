#!/usr/bin/env python3
"""Compare two Word documents: a paragraph- and word-level change report, and optionally a redline .docx with
real tracked changes (w:ins/w:del with author and date) that Word shows as revisions and can accept or reject.

The report lists added, deleted and changed paragraphs and table rows with the edits inline as CriticMarkup
({--old--}{++new++}), style changes, and header/footer changes. Documents that already contain tracked changes are
compared as if those were accepted. The redline starts from the NEW document (its formatting, styles and
layout) and marks what the old one had differently.

Examples:
  python3 scripts/docx_compare.py contract-v1.docx contract-v2.docx
  python3 scripts/docx_compare.py v1.docx v2.docx --redline v1-to-v2-redline.docx --author "Legal review"
  python3 scripts/docx_compare.py v1.docx v2.docx --format json
  python3 scripts/docx_compare.py v1.docx v2.docx --offset 400        # the next changes of a long report

A long report is cut at --max-chars (or --max-changes) and ends with the exact command for the next part.
"""

from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, add_format, emit, parser, run_main

# Numbers stay whole ('€4,668.50', '12.5%'): a changed amount reads as one replacement, not four.
TOKEN = re.compile(r"[$€£¥]?\d(?:[\d,.\u00a0\u202f]*\d)?%?|\w+|\s+|[^\w\s]", re.UNICODE)
WORD = re.compile(r"\w+", re.UNICODE)


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("The report") :])
    ap.add_argument("old", help="the original .docx (or .odt/.rtf/.doc)")
    ap.add_argument("new", help="the revised .docx")
    ap.add_argument("--redline", metavar="OUT.docx", help="write a copy of NEW with the differences as tracked changes")
    ap.add_argument("--author", default="Desk", help="author of the tracked changes in the redline (default Desk)")
    ap.add_argument("--date", help="revision date (ISO, default now)")
    ap.add_argument("--context", type=int, default=0, help="unchanged paragraphs to show around each change (default 0)")
    ap.add_argument("--max-changes", type=int, default=400, help="cap on listed changes (default 400)")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N changes (to read a long report in parts)")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"cap on printed characters (default {DEFAULT_MAX_CHARS}; 0 = no cap)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing redline file")
    add_format(ap)
    args = ap.parse_args()

    from _common import output_path
    from _docx import load

    old = load(args.old)
    new = load(args.new)
    if args.redline:
        output_path(args.redline, [args.old, args.new], force=args.force)
    cmp = Comparison(old, new)
    report = cmp.report(args.context, args.max_changes, max(0, args.offset))
    if args.redline:
        red = load(args.new)
        stats = Redliner(old, red, cmp, args.author, args.date).run()
        warns = red.save(Path(args.redline), inputs=(Path(args.old), Path(args.new)), force=args.force)
        report["redline"] = {"output": args.redline, **stats}
        if warns:
            report["warnings"] = warns
    fit(report, args.format, args.max_chars)
    emit(report, args.format, render, max_chars=0)
    return 0


_VALUED = {"--redline", "--author", "--date", "--context", "--max-changes", "--offset", "--max-chars", "--format"}


def fit(report: dict[str, Any], fmt: str, max_chars: int) -> None:
    """Cuts the change list to --max-chars (keeping the summary) and adds the exact command for the next part."""
    import json

    from _docx import rerun

    changes = report["changes"]
    if max_chars and changes:
        def size(c: dict[str, Any]) -> int:
            if fmt != "json":
                return len(_change_lines(c)) + 1
            text = json.dumps(c, ensure_ascii=False, indent=2)
            return len(text) + 4 * (text.count("\n") + 1) + 2

        budget = max_chars - 2500
        used, keep = 0, 0
        for c in changes:
            used += size(c)
            if keep and used > budget:
                break
            keep += 1
        if keep < len(changes):
            report["more_changes"] += len(changes) - keep
            report["changes"] = changes[:keep]
    shown_to = report["offset"] + len(report["changes"])
    if report["more_changes"]:
        report["next"] = rerun("docx_compare.py", ["--redline", "--force", "--author", "--date", "--offset"], ["--offset", str(shown_to)], _VALUED)


def tokens(s: str) -> list[str]:
    return TOKEN.findall(s)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class Unit:
    __slots__ = ("kind", "el", "text", "style", "index", "fmt")

    def __init__(self, kind: str, el: Any, text: str, style: str, index: int, fmt: tuple = ()) -> None:
        self.kind = kind
        self.el = el
        self.text = text
        self.style = style
        self.index = index
        self.fmt = fmt

    @property
    def key(self) -> str:
        return f"{self.kind}\x00{norm(self.text)}"


def units_of(doc: Any) -> list[Unit]:
    """The blocks to compare, as the document reads with its tracked changes accepted: a paragraph whose text and
    mark are both deleted is gone (it is not an empty paragraph)."""
    from _docx import DEL, P, PPR, RPR, block_text, iter_blocks, style_id

    out = []
    for i, el in enumerate(iter_blocks(doc.body)):
        if el.tag == P:
            text = _para_text(el)
            ppr = el.find(PPR)
            mark = ppr.find(RPR) if ppr is not None else None
            if not text and mark is not None and mark.find(DEL) is not None:
                continue
            out.append(Unit("p", el, text, doc.styles.name(style_id(el)), i, _fmt_sig(el, doc)))
        else:
            out.append(Unit("t", el, block_text(el), "", i))
    return out


def _header_row(tbl: Any) -> tuple[int, str]:
    from _docx import TR, cell_text, row_cells

    tr = tbl.find(TR)
    if tr is None:
        return 0, ""
    cells = row_cells(tr)
    return len(cells), " | ".join(norm(cell_text(tc)) for tc in cells)


def versions(a: Unit, b: Unit) -> bool:
    """Whether two blocks are versions of each other: similar paragraphs, or tables with the same columns (the
    same header row, or similar content), so that a table whose rows changed is compared row by row."""
    if a.kind != b.kind:
        return False
    if a.kind == "p":
        return similarity(a.text, b.text) >= 0.45
    ha, hb = _header_row(a.el), _header_row(b.el)
    if ha[0] != hb[0]:
        return similarity(a.text, b.text) >= 0.45
    return ha[1] == hb[1] or similarity(a.text, b.text) >= 0.2


def _para_text(p: Any) -> str:
    from _runs import CharMap

    return CharMap(p).text


def _fmt_sig(p: Any, doc: Any) -> tuple:
    """Direct character formatting as (text, bold, italic, underline, strike) pieces, merged."""
    from _docx import RPR, run_text
    from _runs import paragraph_runs
    from _styles import flag, flatten

    out: list[list[Any]] = []
    for r in paragraph_runs(p):
        t = run_text(r)
        if not t:
            continue
        pr = flatten(r.find(RPR))
        key = (flag(pr, "b"), flag(pr, "i"), (pr.get("u") or {}).get("val") not in (None, "none"), flag(pr, "strike"))
        if out and out[-1][1] == key:
            out[-1][0] += t
        else:
            out.append([t, key])
    return tuple((t, k) for t, k in out if any(k))


def similarity(a: str, b: str) -> float:
    """How much two texts share, 0-1: the Dice coefficient of their word multisets (fast, order-blind; only used to
    decide which old and new paragraphs are versions of each other)."""
    if a == b:
        return 1.0
    from collections import Counter

    wa, wb = Counter(WORD.findall(a.lower())), Counter(WORD.findall(b.lower()))
    total = sum(wa.values()) + sum(wb.values())
    if not total:
        return 1.0 if a.strip() == b.strip() else 0.0
    return 2 * sum((wa & wb).values()) / total


def token_opcodes(ta: list[str], tb: list[str]) -> list[tuple[str, int, int, int, int]]:
    """difflib opcodes for two token lists, fast for local edits: the common head and tail are matched first."""
    n = min(len(ta), len(tb))
    head = 0
    while head < n and ta[head] == tb[head]:
        head += 1
    tail = 0
    while tail < n - head and ta[len(ta) - 1 - tail] == tb[len(tb) - 1 - tail]:
        tail += 1
    ops: list[tuple[str, int, int, int, int]] = []
    if head:
        ops.append(("equal", 0, head, 0, head))
    mid_a, mid_b = ta[head : len(ta) - tail], tb[head : len(tb) - tail]
    if mid_a or mid_b:
        sm = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False)
        ops += [(t, i1 + head, i2 + head, j1 + head, j2 + head) for t, i1, i2, j1, j2 in sm.get_opcodes()]
    if tail:
        ops.append(("equal", len(ta) - tail, len(ta), len(tb) - tail, len(tb)))
    return ops


def _row_label(key: str) -> str:
    """A row's first cell with letters in it ('Total', 'Travel expenses'): rows keep their label when edited."""
    for cell in key.split(" | "):
        if re.search(r"[^\W\d_]", cell):
            return norm(cell).lower()
    return ""


def rows_similar(a: str, b: str, ca: int, cb: int) -> bool:
    if ca != cb:
        return False
    if similarity(a, b) >= 0.4:
        return True
    la, lb = _row_label(a), _row_label(b)
    return bool(la) and la == lb


def pair_rows(ka: list[str], kb: list[str], ca: list[int], cb: list[int]) -> list[tuple[str, int | None, int | None]]:
    """Aligns table rows: equal, change (the same row edited: similar text, or the same label cell, with the same
    number of cells), delete, insert. In a changed stretch, deletions come before insertions."""
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    out: list[tuple[str, int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(("equal", i1 + k, j1 + k) for k in range(i2 - i1))
            continue
        nxt = i1  # the next old row not yet placed
        pending: list[int] = []  # new rows waiting to be reported as insertions
        for j in range(j1, j2):
            m = next((i for i in range(nxt, i2) if rows_similar(ka[i], kb[j], ca[i], cb[j])), None)
            if m is None:
                pending.append(j)
                continue
            out.extend(("delete", i, None) for i in range(nxt, m))
            out.extend(("insert", None, x) for x in pending)
            pending = []
            out.append(("change", m, j))
            nxt = m + 1
        out.extend(("delete", i, None) for i in range(nxt, i2))
        out.extend(("insert", None, x) for x in pending)
    return out


class Comparison:
    def __init__(self, old: Any, new: Any) -> None:
        self.old_doc, self.new_doc = old, new
        self.old = units_of(old)
        self.new = units_of(new)
        self.ops = self._align()

    def _align(self) -> list[tuple[str, Unit | None, Unit | None]]:
        """('equal'|'insert'|'delete'|'change', old unit, new unit) in document order."""
        sm = difflib.SequenceMatcher(None, [u.key for u in self.old], [u.key for u in self.new], autojunk=False)
        out: list[tuple[str, Unit | None, Unit | None]] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                out.extend(("equal", self.old[i1 + k], self.new[j1 + k]) for k in range(i2 - i1))
            elif tag == "delete":
                out.extend(("delete", u, None) for u in self.old[i1:i2])
            elif tag == "insert":
                out.extend(("insert", None, u) for u in self.new[j1:j2])
            else:
                out.extend(self._pair(self.old[i1:i2], self.new[j1:j2]))
        return out

    def _pair(self, a: list[Unit], b: list[Unit]) -> list[tuple[str, Unit | None, Unit | None]]:
        out: list[tuple[str, Unit | None, Unit | None]] = []
        i = j = 0
        while i < len(a) and j < len(b):
            if versions(a[i], b[j]):
                out.append(("change", a[i], b[j]))
                i += 1
                j += 1
                continue
            skip_new = j + 1 < len(b) and versions(a[i], b[j + 1])
            skip_old = i + 1 < len(a) and versions(a[i + 1], b[j])
            if skip_new and not skip_old:
                out.append(("insert", None, b[j]))
                j += 1
            elif skip_old and not skip_new:
                out.append(("delete", a[i], None))
                i += 1
            else:
                out.append(("delete", a[i], None))
                out.append(("insert", None, b[j]))
                i += 1
                j += 1
        out.extend(("delete", u, None) for u in a[i:])
        out.extend(("insert", None, u) for u in b[j:])
        return out

    # ── report ──────────────────────────────────────────────────────────
    def report(self, context: int = 0, max_changes: int = 400, offset: int = 0) -> dict[str, Any]:
        changes: list[dict[str, Any]] = []
        stats = {"paragraphs_added": 0, "paragraphs_deleted": 0, "paragraphs_changed": 0, "tables_added": 0, "tables_deleted": 0, "tables_changed": 0, "style_changes": 0, "formatting_changes": 0, "words_added": 0, "words_deleted": 0}
        for kind, o, n in self.ops:
            if kind == "equal":
                if o.kind == "p" and o.style != n.style:
                    stats["style_changes"] += 1
                    changes.append({"type": "style", "old_index": o.index, "new_index": n.index, "text": n.text[:120], "old_style": o.style, "new_style": n.style})
                elif o.kind == "p" and o.fmt != n.fmt:
                    stats["formatting_changes"] += 1
                    changes.append({"type": "formatting", "old_index": o.index, "new_index": n.index, "text": n.text[:120]})
                continue
            if kind == "insert":
                stats["tables_added" if n.kind == "t" else "paragraphs_added"] += 1
                stats["words_added"] += len(re.findall(r"\w+", n.text))
                changes.append({"type": "added", "what": "table" if n.kind == "t" else "paragraph", "new_index": n.index, "text": n.text})
            elif kind == "delete":
                stats["tables_deleted" if o.kind == "t" else "paragraphs_deleted"] += 1
                stats["words_deleted"] += len(re.findall(r"\w+", o.text))
                changes.append({"type": "deleted", "what": "table" if o.kind == "t" else "paragraph", "old_index": o.index, "text": o.text})
            else:
                if o.kind == "t":
                    stats["tables_changed"] += 1
                    rows = self._table_rows_diff(o.el, n.el)
                    changes.append({"type": "changed", "what": "table", "old_index": o.index, "new_index": n.index, "rows": rows})
                    for r in rows:
                        stats["words_added"] += r.get("words_added", 0)
                        stats["words_deleted"] += r.get("words_deleted", 0)
                else:
                    stats["paragraphs_changed"] += 1
                    markup, add, rem = word_diff(o.text, n.text)
                    stats["words_added"] += add
                    stats["words_deleted"] += rem
                    ch = {"type": "changed", "what": "paragraph", "old_index": o.index, "new_index": n.index, "diff": markup}
                    if o.style != n.style:
                        ch["style"] = f"{o.style} → {n.style}"
                    changes.append(ch)
        hf = self._headers_footers()
        total = len(changes)
        return {
            "old": str(self.old_doc.path),
            "new": str(self.new_doc.path),
            "identical": total == 0 and not hf,
            "summary": stats,
            "total_changes": total,
            "offset": offset,
            "changes": changes[offset : offset + max_changes],
            "more_changes": max(0, total - offset - max_changes),
            "headers_footers": hf,
            "context": self._context(context) if context else None,
        }

    def _context(self, n: int) -> list[str]:
        return []

    def _table_rows_diff(self, a: Any, b: Any) -> list[dict[str, Any]]:
        from _docx import TR, cell_text, row_cells

        ra = [[cell_text(tc) for tc in row_cells(tr)] for tr in a.findall(TR)]
        rb = [[cell_text(tc) for tc in row_cells(tr)] for tr in b.findall(TR)]
        out: list[dict[str, Any]] = []
        for kind, i, j in pair_rows([" | ".join(r) for r in ra], [" | ".join(r) for r in rb], [len(r) for r in ra], [len(r) for r in rb]):
            if kind == "equal":
                continue
            if kind == "change":
                cells = []
                add = rem = 0
                for c in range(max(len(ra[i]), len(rb[j]))):
                    x = ra[i][c] if c < len(ra[i]) else ""
                    y = rb[j][c] if c < len(rb[j]) else ""
                    if x != y:
                        m, a_, r_ = word_diff(x, y)
                        cells.append({"col": c, "diff": m})
                        add += a_
                        rem += r_
                out.append({"type": "changed", "old_row": i, "new_row": j, "cells": cells, "words_added": add, "words_deleted": rem})
            elif kind == "delete":
                out.append({"type": "deleted", "old_row": i, "text": " | ".join(ra[i]), "words_deleted": len(re.findall(r"\w+", " ".join(ra[i])))})
            else:
                out.append({"type": "added", "new_row": j, "text": " | ".join(rb[j]), "words_added": len(re.findall(r"\w+", " ".join(rb[j])))})
        return out

    def _headers_footers(self) -> list[dict[str, Any]]:
        from _reader import Reader

        def hf(doc: Any) -> dict[str, str]:
            r = Reader(doc, comments="none", plain=True)
            heads, foots = r._headers_footers()
            out = {}
            for kind, items in (("header", heads), ("footer", foots)):
                for h in items:
                    out[f"{kind} {h['type']}"] = h["text"]
            return out

        a, b = hf(self.old_doc), hf(self.new_doc)
        out = []
        for k in sorted(set(a) | set(b)):
            if a.get(k, "") != b.get(k, ""):
                out.append({"where": k, "diff": word_diff(a.get(k, ""), b.get(k, ""))[0]})
        return out


def word_diff(a: str, b: str) -> tuple[str, int, int]:
    """CriticMarkup of the word-level difference, words added, words deleted."""
    ta, tb = tokens(a), tokens(b)
    out = []
    add = rem = 0
    ops = token_opcodes(ta, tb)
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            out.append(_context("".join(ta[i1:i2]), first=k == 0, last=k == len(ops) - 1))
            continue
        if tag in ("delete", "replace"):
            seg = "".join(ta[i1:i2])
            rem += len(re.findall(r"\w+", seg))
            out.append("{--" + seg + "--}")
        if tag in ("insert", "replace"):
            seg = "".join(tb[j1:j2])
            add += len(re.findall(r"\w+", seg))
            out.append("{++" + seg + "++}")
    return "".join(out), add, rem


def _context(text: str, first: bool, last: bool, keep: int = 8) -> str:
    """Unchanged text between edits, shortened to a few words on each side in long paragraphs."""
    words = re.split(r"(\s+)", text)
    n = len([w for w in words if w.strip()])
    if n <= 2 * keep + 4:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    head = "".join(words[: 2 * keep]).strip()
    tail = "".join(words[-2 * keep :]).strip()
    if first and last:
        return text
    if first:
        return "… " + tail + trail
    if last:
        return lead + head + " …"
    return lead + head + " … " + tail + trail


def render(r: dict[str, Any]) -> str:
    s = r["summary"]
    L = [f"# {Path(r['old']).name} → {Path(r['new']).name}"]
    if r["identical"]:
        L.append("The documents have the same text, styles and headers/footers.")
        return "\n".join(L)
    L.append(
        f"{s['paragraphs_changed']} paragraphs changed, {s['paragraphs_added']} added, {s['paragraphs_deleted']} deleted; "
        f"{s['tables_changed']} tables changed, {s['tables_added']} added, {s['tables_deleted']} deleted; "
        f"{s['style_changes']} style changes, {s['formatting_changes']} formatting changes; "
        f"+{s['words_added']} / -{s['words_deleted']} words"
    )
    if r.get("redline"):
        rd = r["redline"]
        L.append(f"Redline: {rd['output']} ({rd['insertions']} insertions, {rd['deletions']} deletions as tracked changes by {rd['author']}). Render it with docx_render.py to see the markup.")
    L.append("")
    if r.get("offset"):
        L.append(f"(changes {r['offset'] + 1}-{r['offset'] + len(r['changes'])} of {r['total_changes']})")
    for c in r["changes"]:
        L.append(_change_lines(c))
    for h in r["headers_footers"]:
        L.append(f"- {h['where']}: {h['diff']}")
    if r.get("more_changes"):
        L.append(f"[… {r['more_changes']} more changes not shown. Continue with: {r['next']}]")
    return "\n".join(L)


def _change_lines(c: dict[str, Any]) -> str:
    where = f"[{c.get('old_index', '·')} → {c.get('new_index', '·')}]"
    if c["type"] == "added":
        return f"- {where} added {c['what']}: {{++{_short(c['text'])}++}}"
    if c["type"] == "deleted":
        return f"- {where} deleted {c['what']}: {{--{_short(c['text'])}--}}"
    if c["type"] == "style":
        return f"- {where} style {c['old_style']} → {c['new_style']}: {_short(c['text'], 80)}"
    if c["type"] == "formatting":
        return f"- {where} formatting changed (bold/italic/underline/strike): {_short(c['text'], 80)}"
    if c["what"] == "table":
        out = [f"- {where} table changed:"]
        for row in c["rows"]:
            if row["type"] == "changed":
                out.append(f"  - row {row['old_row']} → {row['new_row']}: " + "; ".join(f"col {x['col']}: {x['diff']}" for x in row["cells"]))
            elif row["type"] == "added":
                out.append(f"  - row {row['new_row']} added: {{++{row['text']}++}}")
            else:
                out.append(f"  - row {row['old_row']} deleted: {{--{row['text']}--}}")
        return "\n".join(out)
    return f"- {where} {c['diff']}" + (f"  (style {c['style']})" if c.get("style") else "")


def _short(s: str, n: int = 300) -> str:
    s = s.replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ── redline ─────────────────────────────────────────────────────────────


class Redliner:
    """Writes the differences into (a fresh copy of) the new document as tracked changes."""

    def __init__(self, old: Any, red: Any, cmp: Comparison, author: str, date: str | None) -> None:
        from _tracked import Tracker

        self.old = old
        self.red = red
        self.cmp = cmp
        self.tr = Tracker(red, author, date)
        self.author = author
        self.ins = 0
        self.dels = 0
        self.red_blocks = red.blocks()

    def _mine(self, u: Unit | None) -> Any:
        return self.red_blocks[u.index] if u is not None else None

    def run(self) -> dict[str, Any]:

        last_new: Any = None
        pending_deleted: list[Any] = []
        for kind, o, n in self.cmp.ops:
            if kind == "delete":
                el = self._old_copy(o.el)
                if last_new is not None:
                    anchor = pending_deleted[-1] if pending_deleted else last_new
                    anchor.addnext(el)
                else:
                    first = self.red_blocks[0] if self.red_blocks else None
                    if pending_deleted:
                        pending_deleted[-1].addnext(el)
                    elif first is not None:
                        first.addprevious(el)
                    else:
                        self.red.body.insert(0, el)
                self._mark_deleted(el)
                pending_deleted.append(el)
                continue
            mine = self._mine(n)
            pending_deleted = []
            if kind == "insert":
                self._mark_inserted(mine)
            elif kind == "change":
                if n.kind == "p":
                    self._paragraph(o, n, mine)
                else:
                    self._table(o.el, mine)
            elif kind == "equal" and n.kind == "p" and o.style != n.style:
                self._style_change(o, mine)
            last_new = mine
        return {"insertions": self.ins, "deletions": self.dels, "author": self.author}

    def _mark_inserted(self, el: Any) -> None:
        from _docx import P, TR

        if el.tag == P:
            self.tr.insert_paragraph(el)
        else:
            for tr in el.findall(TR):
                self.tr.insert_row(tr)
        self.ins += 1

    def _mark_deleted(self, el: Any) -> None:
        from _docx import P, TR

        if el.tag == P:
            self.tr.delete_paragraph(el)
        else:
            for tr in el.findall(TR):
                self.tr.delete_row(tr)
        self.dels += 1

    def _old_copy(self, el: Any) -> Any:
        """A copy of an old block that is safe inside the new document (no dangling references)."""
        from _docx import NS, qn

        el = copy.deepcopy(el)
        r_ns = "{" + NS["r"] + "}"
        styles = self.red.styles
        numbering = self.red.numbering
        for x in list(el.iter()):
            if not isinstance(x.tag, str) or x.getparent() is None:
                continue
            tag = x.tag
            if tag in (qn("w:drawing"), qn("w:pict"), qn("w:object"), qn("w:footnoteReference"), qn("w:endnoteReference"), qn("w:commentReference"), qn("w:commentRangeStart"), qn("w:commentRangeEnd"), qn("w:bookmarkStart"), qn("w:bookmarkEnd")):
                x.getparent().remove(x)
            elif tag == qn("w:hyperlink"):
                parent = x.getparent()
                idx = parent.index(x)
                for c in list(x):
                    parent.insert(idx, c)
                    idx += 1
                parent.remove(x)
            elif tag in (qn("w:pStyle"), qn("w:rStyle"), qn("w:tblStyle")) and styles.get(x.get(qn("w:val"))) is None:
                x.getparent().remove(x)
            elif tag == qn("w:numPr"):
                nid = x.find(qn("w:numId"))
                if nid is None or nid.get(qn("w:val")) not in numbering.nums:
                    x.getparent().remove(x)
            elif tag == qn("w:sectPr"):
                x.getparent().remove(x)
            else:
                for k in list(x.attrib):
                    if k.startswith(r_ns):
                        del x.attrib[k]
        return el

    def _paragraph(self, o: Unit, n: Unit, p: Any) -> None:
        """Word-level tracked changes inside a changed paragraph (new text kept, old text as deletions)."""
        from _docx import new_el
        from _runs import CharMap, isolate

        ta, tb = tokens(o.text), tokens(n.text)
        # char offsets
        oa = [0]
        for t in ta:
            oa.append(oa[-1] + len(t))
        ob = [0]
        for t in tb:
            ob.append(ob[-1] + len(t))
        old_cm = CharMap(o.el)
        edits = [op for op in token_opcodes(ta, tb) if op[0] != "equal"]
        for tag, i1, i2, j1, j2 in reversed(edits):
            ns, ne = ob[j1], ob[j2]
            if tag in ("insert", "replace") and ne > ns:
                runs = isolate(p, ns, ne)
                self._wrap_ins(runs)
                self.ins += 1
            if tag in ("delete", "replace"):
                text = o.text[oa[i1] : oa[i2]]
                if not text:
                    continue
                rpr = self._old_rpr(old_cm, oa[i1])
                r = new_el("w:r")
                if rpr is not None:
                    r.append(rpr)
                for piece in re.split(r"(\t|\n)", text):
                    if piece == "\t":
                        r.append(new_el("w:tab"))
                    elif piece == "\n":
                        r.append(new_el("w:br"))
                    elif piece:
                        dt = new_el("w:delText")
                        dt.text = piece
                        dt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                        r.append(dt)
                d = self.tr.el("w:del")
                d.append(r)
                self._insert_at(p, ns, d)
                self.dels += 1
        if o.style != n.style:
            self._style_change(o, p)

    def _wrap_ins(self, runs: list[Any]) -> None:
        group: list[Any] = []

        def flush() -> None:
            if group:
                ins = self.tr.el("w:ins")
                group[0].addprevious(ins)
                for r in group:
                    ins.append(r)
                group.clear()

        for r in runs:
            if group and group[-1].getnext() is not r:
                flush()
            group.append(r)
        flush()

    def _old_rpr(self, cm: Any, pos: int) -> Any:
        from _docx import RPR

        for a in cm.atoms:
            if a.start <= pos < a.start + a.length:
                rpr = a.run.find(RPR)
                if rpr is None:
                    return None
                rpr = copy.deepcopy(rpr)
                for x in list(rpr):
                    if x.tag.split("}")[-1] in ("rPrChange", "ins", "del"):
                        rpr.remove(x)
                sid = rpr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rStyle")
                if sid is not None and self.red.styles.get(sid.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")) is None:
                    rpr.remove(sid)
                return rpr
        return None

    def _insert_at(self, p: Any, pos: int, el: Any) -> None:
        """Inserts an inline element at a character offset of the paragraph's visible text."""
        from _docx import PPR
        from _runs import CharMap, isolate

        cm = CharMap(p)
        if pos >= len(cm.text):
            last = cm.atoms[-1].run if cm.atoms else None
            if last is not None:
                anchor = last
                # after the run's enclosing ins (if the last text was just inserted)
                if anchor.getparent() is not None and anchor.getparent().tag.endswith("}ins") and anchor.getparent().getparent() is p:
                    anchor = anchor.getparent()
                anchor.addnext(el)
            else:
                ppr = p.find(PPR)
                (ppr.addnext(el) if ppr is not None else p.insert(0, el))
            return
        runs = isolate(p, pos, pos + 1)
        target = runs[0]
        if target.getparent() is not None and target.getparent().tag.endswith("}ins"):
            # before an insertion wrapper that starts here
            if target.getparent().index(target) == 0:
                target = target.getparent()
        target.addprevious(el)

    def _style_change(self, o: Unit, p: Any) -> None:
        from _docx import PPR, qn

        ppr = p.find(PPR)
        if ppr is None:
            return
        ch = self.tr.el("w:pPrChange")
        old_ppr = copy.deepcopy(o.el.find(PPR)) if o.el.find(PPR) is not None else None
        from _docx import new_el

        prev = new_el("w:pPr")
        if old_ppr is not None:
            for x in old_ppr:
                if x.tag in (qn("w:pStyle"), qn("w:jc"), qn("w:ind"), qn("w:spacing"), qn("w:numPr")) and not (x.tag == qn("w:pStyle") and self.red.styles.get(x.get(qn("w:val"))) is None):
                    prev.append(copy.deepcopy(x))
        ch.append(prev)
        for existing in ppr.findall(qn("w:pPrChange")):
            ppr.remove(existing)
        ppr.append(ch)

    def _table(self, a: Any, b: Any) -> None:
        """Row-level redline of a changed table; changed rows with the same cells get word-level changes."""
        from _docx import P, TR, row_cells

        ra = a.findall(TR)
        rb = b.findall(TR)
        ka = [" | ".join(_cell_texts(tr)) for tr in ra]
        kb = [" | ".join(_cell_texts(tr)) for tr in rb]
        anchor = None
        for kind, i, j in pair_rows(ka, kb, [len(row_cells(tr)) for tr in ra], [len(row_cells(tr)) for tr in rb]):
            if kind == "equal":
                anchor = rb[j]
            elif kind == "change":
                for tca, tcb in zip(row_cells(ra[i]), row_cells(rb[j])):
                    pa = [p for p in tca.iter(P)]
                    pb = [p for p in tcb.iter(P)]
                    if len(pa) == len(pb):
                        for x, y in zip(pa, pb):
                            ta, tb = _para_text(x), _para_text(y)
                            if ta != tb:
                                self._paragraph(Unit("p", x, ta, "", 0), Unit("p", y, tb, "", 0), y)
                    else:
                        for y in pb:
                            self.tr.insert_paragraph(y)
                            self.ins += 1
                        for x in pa:
                            cp = self._old_copy(x)
                            if pb:
                                pb[-1].addnext(cp)
                            else:
                                tcb.append(cp)
                            self.tr.delete_paragraph(cp)
                            self.dels += 1
                anchor = rb[j]
            elif kind == "insert":
                self.tr.insert_row(rb[j])
                self.ins += 1
                anchor = rb[j]
            else:
                cp = self._old_copy(ra[i])
                if anchor is not None:
                    anchor.addnext(cp)
                elif rb:
                    rb[0].addprevious(cp)
                else:
                    b.append(cp)
                anchor = cp
                self.tr.delete_row(cp)
                self.dels += 1


def _cell_texts(tr: Any) -> list[str]:
    from _docx import cell_text, row_cells

    return [cell_text(tc) for tc in row_cells(tr)]


if __name__ == "__main__":
    run_main(main)
