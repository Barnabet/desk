#!/usr/bin/env python3
"""Apply edits to a .docx and write the result (the input is never modified in place unless --out is the same path).

Operations come as a JSON list (--ops file, or stdin):
  {"op": "replace", "find": "ACME Ltd", "replace": "Acme Limited"}        # every occurrence, body, tables, headers, footers
  {"op": "replace", "find": "draft", "replace": "final", "first": true}   # only the first occurrence
  {"op": "append", "markdown": "## Next steps\\n\\n- Sign by Friday"}
  {"op": "insert_after", "index": 3, "markdown": "An inserted paragraph."}  # index from docx_read.py --format json
  {"op": "delete", "index": 7}
  {"op": "properties", "title": "Contract", "author": "Legal", "subject": "...", "keywords": "..."}

Replacement keeps the formatting of the run where each match starts. Prints a JSON summary of what changed.
Example: python3 scripts/docx_edit.py in.docx --out out.docx --ops edits.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph

from _docxbuild import add_blocks
from _mdlite import parse


def replace_in_paragraph(p: Paragraph, find: str, repl: str, limit: int | None) -> int:
    count = 0
    search_from = 0
    while find and (limit is None or count < limit):
        runs = p.runs
        texts = [r.text for r in runs]
        start = "".join(texts).find(find, search_from)
        if start < 0:
            break
        end = start + len(find)
        pos = 0
        first = True
        for run, t in zip(runs, texts):
            a, b = pos, pos + len(t)
            pos = b
            if b <= start or a >= end:
                continue
            keep_before = t[: max(0, start - a)]
            keep_after = t[end - a:] if end < b else ""
            run.text = keep_before + (repl if first else "") + keep_after
            first = False
        count += 1
        search_from = start + len(repl)
    return count


def all_paragraphs(doc):
    yield from doc.paragraphs
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for s in doc.sections:
        for part in (s.header, s.footer):
            yield from part.paragraphs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", "-o", required=True, help="Where to write the edited document")
    ap.add_argument("--ops", help="JSON file with the list of operations (default: stdin)")
    args = ap.parse_args()
    ops = json.loads(Path(args.ops).read_text(encoding="utf-8") if args.ops else sys.stdin.read())
    if isinstance(ops, dict):
        ops = [ops]
    doc = Document(args.input)
    changes = []
    # Index-based operations refer to the document as read before any edit.
    original = list(doc.paragraphs)
    for op in ops:
        kind = op.get("op")
        if kind == "replace":
            limit = 1 if op.get("first") else None
            n = 0
            for p in all_paragraphs(doc):
                n += replace_in_paragraph(p, op["find"], op.get("replace", ""), None if limit is None else limit - n)
                if limit is not None and n >= limit:
                    break
            changes.append({"op": "replace", "find": op["find"], "count": n})
        elif kind == "append":
            added = add_blocks(doc, parse(op["markdown"]))
            changes.append({"op": "append", "elements": len(added)})
        elif kind == "insert_after":
            target = original[op["index"]]._p
            added = add_blocks(doc, parse(op["markdown"]))
            for el in added:
                target.addnext(el)
                target = el
            changes.append({"op": "insert_after", "index": op["index"], "elements": len(added)})
        elif kind == "delete":
            el = original[op["index"]]._p
            el.getparent().remove(el)
            changes.append({"op": "delete", "index": op["index"]})
        elif kind == "properties":
            cp = doc.core_properties
            for key in ("title", "author", "subject", "keywords", "comments", "category"):
                if key in op:
                    setattr(cp, key, op[key])
            changes.append({"op": "properties", "set": [k for k in op if k != "op"]})
        else:
            print(json.dumps({"error": f"unknown op: {kind}"}))
            return 2
    doc.core_properties.modified = datetime.now(timezone.utc).replace(microsecond=0)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.out)
    print(json.dumps({"output": args.out, "changes": changes}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
