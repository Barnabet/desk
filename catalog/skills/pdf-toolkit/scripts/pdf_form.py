#!/usr/bin/env python3
"""List and fill PDF form fields (AcroForm) with pypdf.

  python3 scripts/pdf_form.py list form.pdf                        # JSON: name, type, value, options
  python3 scripts/pdf_form.py fill form.pdf filled.pdf --values values.json
  python3 scripts/pdf_form.py fill form.pdf filled.pdf --values values.json --flatten   # no longer editable

values.json maps field names (exactly as listed) to values: text as strings, checkboxes as their "on" state
(listed in "options", often "/Yes") or "/Off", choices as one of their options.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

TYPES = {"/Tx": "text", "/Btn": "checkbox", "/Ch": "choice", "/Sig": "signature"}


def field_type(f) -> str:
    kind = TYPES.get(f.get("/FT"), str(f.get("/FT")))
    flags = int(f.get("/Ff", 0))
    if kind == "checkbox" and flags & (1 << 16):
        return "pushbutton"
    if kind == "checkbox" and flags & (1 << 15):
        return "radio"
    return kind


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("input")
    fill = sub.add_parser("fill")
    fill.add_argument("input")
    fill.add_argument("out")
    fill.add_argument("--values", required=True, help="JSON file mapping field names to values")
    fill.add_argument("--flatten", action="store_true", help="Burn the values into the pages")
    a = ap.parse_args()

    reader = PdfReader(a.input)
    fields = reader.get_fields() or {}
    if a.cmd == "list":
        out = []
        for name, f in fields.items():
            states = f.get("/_States_", [])
            opts = f.get("/Opt", [])
            out.append({
                "name": name,
                "type": field_type(f),
                "value": str(f.get("/V")) if f.get("/V") is not None else None,
                "options": [str(o) for o in (states or opts)],
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    values = json.loads(Path(a.values).read_text(encoding="utf-8"))
    unknown = [k for k in values if k not in fields]
    if unknown:
        print(json.dumps({"error": "unknown fields", "fields": unknown, "known": sorted(fields)}))
        return 1
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False, flatten=a.flatten)
    if not a.flatten:
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "wb") as f:
        writer.write(f)
    print(json.dumps({"output": a.out, "filled": sorted(values), "flattened": a.flatten}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
