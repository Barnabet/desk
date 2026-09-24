#!/usr/bin/env python3
"""Summarise a PDF as JSON: pages, page size, encryption, metadata, outline, form fields, and whether it has a text layer.

Example: python3 scripts/pdf_info.py report.pdf
A PDF with "text_layer": false is probably scanned; its text needs OCR, which this skill does not include.
"""
from __future__ import annotations

import argparse
import json
import sys

import pdfplumber
from pypdf import PdfReader


def outline_titles(reader: PdfReader, items=None, depth=0, out=None):
    out = [] if out is None else out
    for item in reader.outline if items is None else items:
        if isinstance(item, list):
            outline_titles(reader, item, depth + 1, out)
        else:
            out.append({"title": item.title, "depth": depth})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--password", help="Password for an encrypted PDF")
    args = ap.parse_args()
    reader = PdfReader(args.path)
    encrypted = reader.is_encrypted
    if encrypted:
        if not args.password or not reader.decrypt(args.password):
            print(json.dumps({"path": args.path, "encrypted": True, "error": "password required"}))
            return 1
    first = reader.pages[0] if reader.pages else None
    meta = {k.lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()}
    fields = reader.get_fields() or {}
    chars = 0
    with pdfplumber.open(args.path, password=args.password or "") as pdf:
        for page in pdf.pages[:3]:
            chars += len(page.chars)
    info = {
        "path": args.path,
        "pages": len(reader.pages),
        "page_size_pt": [float(first.mediabox.width), float(first.mediabox.height)] if first else None,
        "encrypted": encrypted,
        "metadata": meta,
        "outline": outline_titles(reader)[:200],
        "form_fields": len(fields),
        "text_layer": chars > 0,
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
