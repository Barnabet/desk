#!/usr/bin/env python3
"""Extract text (and optionally tables) from a PDF with pdfplumber.

Examples:
  python3 scripts/pdf_text.py paper.pdf                         # all pages, plain text with page markers
  python3 scripts/pdf_text.py paper.pdf --pages 2-4 --layout    # keep the visual layout (columns, alignment)
  python3 scripts/pdf_text.py invoice.pdf --tables              # tables as Markdown
  python3 scripts/pdf_text.py invoice.pdf --tables --format json

--format json prints [{"page": n, "text": ..., "tables": [[[cell, ...], ...]]}]. Pages without a text layer come out
empty: the PDF is probably scanned and needs OCR.
"""
from __future__ import annotations

import argparse
import json
import sys

import pdfplumber

from _pages import parse_pages


def md_table(rows: list[list[str | None]]) -> str:
    rows = [[(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in r] for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width] + ["| " + " | ".join(r) + " |" for r in rows[1:]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--pages", help='1-based pages, e.g. "1-3,7" (default: all)')
    ap.add_argument("--layout", action="store_true", help="Preserve layout (columns, spacing)")
    ap.add_argument("--tables", action="store_true", help="Also extract tables")
    ap.add_argument("--format", "-f", choices=["text", "json"], default="text")
    ap.add_argument("--password")
    args = ap.parse_args()
    results = []
    with pdfplumber.open(args.path, password=args.password or "") as pdf:
        for i in parse_pages(args.pages, len(pdf.pages)):
            page = pdf.pages[i]
            text = page.extract_text(layout=args.layout) or ""
            tables = page.extract_tables() if args.tables else []
            results.append({"page": i + 1, "text": text, "tables": tables})
    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for r in results:
        print(f"--- page {r['page']} ---")
        print(r["text"])
        for t, table in enumerate(r["tables"], 1):
            print(f"\n[table {t}]\n{md_table(table)}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
