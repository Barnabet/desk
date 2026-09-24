#!/usr/bin/env python3
"""Page operations with pypdf. Each command writes a new file and prints a JSON summary.

  python3 scripts/pdf_pages.py merge out.pdf a.pdf b.pdf c.pdf
  python3 scripts/pdf_pages.py extract in.pdf out.pdf --pages 1-3,8
  python3 scripts/pdf_pages.py delete in.pdf out.pdf --pages 2,5
  python3 scripts/pdf_pages.py rotate in.pdf out.pdf --degrees 90 [--pages 1-2]
  python3 scripts/pdf_pages.py split in.pdf outdir/ [--every 1]          # one file per N pages
  python3 scripts/pdf_pages.py encrypt in.pdf out.pdf --password secret
  python3 scripts/pdf_pages.py decrypt in.pdf out.pdf --password secret
  python3 scripts/pdf_pages.py metadata in.pdf out.pdf --title "..." --author "..." --subject "..."

Pages are 1-based; ranges like "3-" run to the end.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from _pages import parse_pages


def open_reader(path: str, password: str | None = None) -> PdfReader:
    r = PdfReader(path)
    if r.is_encrypted:
        if not password or not r.decrypt(password):
            raise SystemExit(f"{path} is encrypted; pass --password")
    return r


def save(writer: PdfWriter, out: str) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("out")
    m.add_argument("inputs", nargs="+")
    for name in ("extract", "delete"):
        p = sub.add_parser(name)
        p.add_argument("input")
        p.add_argument("out")
        p.add_argument("--pages", required=True)
    r = sub.add_parser("rotate")
    r.add_argument("input")
    r.add_argument("out")
    r.add_argument("--degrees", type=int, choices=[90, 180, 270], required=True)
    r.add_argument("--pages")
    s = sub.add_parser("split")
    s.add_argument("input")
    s.add_argument("outdir")
    s.add_argument("--every", type=int, default=1)
    for name in ("encrypt", "decrypt"):
        p = sub.add_parser(name)
        p.add_argument("input")
        p.add_argument("out")
        p.add_argument("--password", required=True)
    md = sub.add_parser("metadata")
    md.add_argument("input")
    md.add_argument("out")
    for key in ("title", "author", "subject", "keywords"):
        md.add_argument(f"--{key}")
    for p in sub.choices.values():
        if "--password" not in p._option_string_actions:
            p.add_argument("--password", help="Password of an encrypted input")
    a = ap.parse_args()

    if a.cmd == "merge":
        w = PdfWriter()
        for path in a.inputs:
            w.append(open_reader(path, a.password))
        save(w, a.out)
        print(json.dumps({"output": a.out, "pages": len(w.pages), "inputs": len(a.inputs)}))
    elif a.cmd in ("extract", "delete"):
        reader = open_reader(a.input, a.password)
        chosen = parse_pages(a.pages, len(reader.pages))
        keep = chosen if a.cmd == "extract" else [i for i in range(len(reader.pages)) if i not in set(chosen)]
        w = PdfWriter()
        for i in keep:
            w.add_page(reader.pages[i])
        save(w, a.out)
        print(json.dumps({"output": a.out, "pages": len(keep)}))
    elif a.cmd == "rotate":
        reader = open_reader(a.input, a.password)
        targets = set(parse_pages(a.pages, len(reader.pages)))
        w = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i in targets:
                page.rotate(a.degrees)
            w.add_page(page)
        save(w, a.out)
        print(json.dumps({"output": a.out, "rotated": len(targets)}))
    elif a.cmd == "split":
        reader = open_reader(a.input, a.password)
        Path(a.outdir).mkdir(parents=True, exist_ok=True)
        stem = Path(a.input).stem
        outputs = []
        for start in range(0, len(reader.pages), a.every):
            w = PdfWriter()
            for i in range(start, min(start + a.every, len(reader.pages))):
                w.add_page(reader.pages[i])
            out = str(Path(a.outdir) / f"{stem}-{start + 1:03d}.pdf")
            save(w, out)
            outputs.append(out)
        print(json.dumps({"outputs": outputs}))
    elif a.cmd == "encrypt":
        w = PdfWriter(clone_from=open_reader(a.input))
        w.encrypt(user_password=a.password, algorithm="AES-256")
        save(w, a.out)
        print(json.dumps({"output": a.out, "encrypted": True}))
    elif a.cmd == "decrypt":
        w = PdfWriter(clone_from=open_reader(a.input, a.password))
        save(w, a.out)
        print(json.dumps({"output": a.out, "encrypted": False}))
    elif a.cmd == "metadata":
        w = PdfWriter(clone_from=open_reader(a.input, a.password))
        meta = {f"/{k.capitalize()}": getattr(a, k) for k in ("title", "author", "subject", "keywords") if getattr(a, k)}
        w.add_metadata(meta)
        save(w, a.out)
        print(json.dumps({"output": a.out, "set": sorted(k.lstrip("/").lower() for k in meta)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
