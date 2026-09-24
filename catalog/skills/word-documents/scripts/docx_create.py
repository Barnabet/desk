#!/usr/bin/env python3
"""Create a .docx from Markdown-lite text (see references/markdown.md for the supported subset).

Examples:
  python3 scripts/docx_create.py report.docx --input report.md --title "Q3 report"
  echo "# Hello\n\nSome **bold** text." | python3 scripts/docx_create.py hello.docx
  python3 scripts/docx_create.py memo.docx --input memo.md --template letterhead.docx

Prints a JSON summary. With --template, the new document starts from that file (its styles, headers and footers)
with its body cleared.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document

from _docxbuild import add_blocks
from _mdlite import parse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output", help="Path of the .docx to write")
    ap.add_argument("--input", "-i", default="-", help="Markdown-lite file, or - for stdin (default)")
    ap.add_argument("--template", help="A .docx whose styles, headers and footers to reuse")
    ap.add_argument("--title", help="Document title (core properties)")
    ap.add_argument("--author", help="Author (core properties)")
    args = ap.parse_args()

    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    doc = Document(args.template) if args.template else Document()
    if args.template:
        body = doc.element.body
        for child in list(body):
            if not child.tag.endswith("}sectPr"):
                body.remove(child)
    added = add_blocks(doc, parse(text))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    doc.core_properties.created = now
    doc.core_properties.modified = now
    doc.core_properties.revision = 1
    if args.title:
        doc.core_properties.title = args.title
    if args.author:
        doc.core_properties.author = args.author
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.output)
    tables = sum(1 for el in added if el.tag.endswith("}tbl"))
    print(json.dumps({"output": args.output, "paragraphs": len(added) - tables, "tables": tables}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
