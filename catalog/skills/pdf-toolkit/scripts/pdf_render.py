#!/usr/bin/env python3
"""Render PDF pages to PNG images sized for a model to look at (view_image), zoom into a region of a page at a
higher resolution, overlay a coordinate grid, or build contact sheets that show many pages at once.

Region and grid coordinates are points from the top-left of the page as displayed (1 pt = 1/72 in), the same
coordinates pdf_text.py --words/--search report and pdf_redact.py --box / pdf_pages.py crop --box accept.
Values between 0 and 1 are fractions of the page (0.5,0,1,0.5 is the top-right quarter).

Examples:
  python3 scripts/pdf_render.py report.pdf                         # pages 1-20 → report-pages/page-01.png …
  python3 scripts/pdf_render.py report.pdf --pages 3,7 --dpi 150 --out renders/
  python3 scripts/pdf_render.py report.pdf --pages 2 --region 300,400,560,560 --dpi 300   # zoom on a table
  python3 scripts/pdf_render.py report.pdf --pages 1 --grid          # grid every 50 pt, to read coordinates
  python3 scripts/pdf_render.py book.pdf --sheet                     # contact sheets, 12 pages each
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, output_dir, parser, run_main
from _pdfkit import emit, open_pdfium, parse_box, parse_pages, pdf_input, pdfium_source, render_pages
from _render import VISION_EDGE, announce, contact_sheet

DEFAULT_PAGE_LIMIT = 20


def nice_step(span: float) -> float:
    raw = span / 10
    for s in (2, 5, 10, 20, 25, 50, 100, 200, 500):
        if s >= raw:
            return float(s)
    return 1000.0


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("pdf")
    p.add_argument("--pages", help=f"pages like 1-3,7 (default: all, at most the first {DEFAULT_PAGE_LIMIT}; with --sheet all)")
    p.add_argument("--dpi", type=float, help="resolution; still capped by --max-edge unless --max-edge 0 (default: fit --max-edge; 200 for --region)")
    p.add_argument("--max-edge", type=int, default=VISION_EDGE, help=f"longest side in pixels (default {VISION_EDGE}, what vision models use; 0 = no cap)")
    p.add_argument("--region", help="x0,y0,x1,y1 in points from the top-left (or fractions 0-1): render only that part")
    p.add_argument("--grid", nargs="?", const=-1.0, type=float, metavar="STEP", help="overlay a labelled grid every STEP points (default: 50, or a tenth of the region)")
    p.add_argument("--sheet", action="store_true", help="contact sheets: many pages as labelled thumbnails")
    p.add_argument("--per-sheet", type=int, default=12, help="pages per contact sheet (default 12)")
    p.add_argument("--out", help="output folder (default: <name>-pages next to the current directory)")
    p.add_argument("--jpeg", action="store_true", help="write JPEG instead of PNG (smaller for photos and scans)")
    p.add_argument("--no-forms", action="store_true", help="do not draw form field values")
    p.add_argument("--password", help="password of an encrypted PDF")
    p.add_argument("--force", action="store_true", help="replace existing images")
    p.add_argument("--format", choices=["md", "json"], default="md", help="md prints the image paths (default); json adds sizes")
    p.add_argument("--no-cache", action="store_true", help="render again instead of reusing cached renders")
    a = p.parse_args()
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"

    path = pdfium_source(pdf_input(a.pdf), a.password)
    doc = open_pdfium(path, a.password)
    count = len(doc)
    sizes = {}
    try:
        if a.pages:
            numbers = parse_pages(a.pages, count)
        elif a.sheet:
            numbers = list(range(1, min(count, max(1, a.per_sheet) * 30) + 1))
        elif a.region:
            numbers = [1]
        else:
            numbers = list(range(1, min(count, DEFAULT_PAGE_LIMIT) + 1))
        for n in numbers:
            sizes[n] = doc[n - 1].get_size()
    finally:
        doc.close()
    if not numbers:
        raise UsageError("the PDF has no pages")
    out = output_dir(a.out or f"{path.stem}-pages")
    width = max(2, len(str(count)))
    ext = ".jpg" if a.jpeg else ".png"

    if a.sheet:
        return render_sheets(a, path, numbers, count, out, ext)

    jobs: list[dict[str, Any]] = []
    for n in numbers:
        vw, vh = sizes[n]
        region = parse_box(a.region, vw, vh) if a.region else None
        rw, rh = (region[2] - region[0], region[3] - region[1]) if region else (vw, vh)
        grid = None
        if a.grid is not None:
            grid = a.grid if a.grid > 0 else (nice_step(max(rw, rh)) if region else 50.0)
        name = f"page-{n:0{width}d}" + ("-region" if region else "") + ext
        target = out / name
        if target.exists() and not a.force:
            raise SkillError(f"{target} already exists; pass --force or choose another --out")
        dpi = a.dpi or (200.0 if region else None)
        jobs.append({"pdf": str(path), "index": n - 1, "password": a.password, "dpi": dpi, "max_edge": a.max_edge, "region": region, "grid": grid, "out": str(target), "forms": not a.no_forms})
    results = render_pages(jobs)
    for r in results:
        r["dpi"] = round(r["scale"] * 72, 1)
    if a.format == "json":
        emit({"file": str(path), "pages": count, "images": results}, "json")
        return 0
    note_bits = []
    if not a.pages and not a.region and count > len(numbers):
        note_bits.append(f"Rendered pages 1-{len(numbers)} of {count}; use --pages for the others, or --sheet for an overview.")
    dpis = sorted({r["dpi"] for r in results})
    if len(dpis) == 1:
        note_bits.append(f"Scale: {dpis[0]} dpi, so point = pixel × 72 / {dpis[0]}" + (f", plus the region's top-left corner ({jobs[0]['region'][0]:g}, {jobs[0]['region'][1]:g})" if a.region else "") + ".")
    announce([r["path"] for r in results], " ".join(note_bits) or None)
    return 0


def render_sheets(a: Any, path: Path, numbers: list[int], count: int, out: Path, ext: str) -> int:
    per = max(1, min(a.per_sheet, 30))
    cols = 4 if per > 9 else 3 if per > 4 else 2
    rows = (per + cols - 1) // cols
    cell = max(160, min(400, (VISION_EDGE - 12 * (cols + 1)) // cols))
    tmp = Path(tempfile.mkdtemp(prefix="desk-sheet-"))
    try:
        jobs = [{"pdf": str(path), "index": n - 1, "password": a.password, "dpi": None, "max_edge": cell, "out": str(tmp / f"t-{n}.png"), "forms": not a.no_forms} for n in numbers]
        thumbs = render_pages(jobs)
        sheets = []
        for s in range(0, len(thumbs), per):
            group = thumbs[s : s + per]
            first, last = group[0]["page"], group[-1]["page"]
            target = out / f"sheet-{first:0{max(2, len(str(count)))}d}-{last:0{max(2, len(str(count)))}d}{ext}"
            if target.exists() and not a.force:
                raise SkillError(f"{target} already exists; pass --force or choose another --out")
            title = f"{path.name}: pages {first}-{last} of {count}"
            made = contact_sheet([g["path"] for g in group], target if ext == ".png" else tmp / "sheet.png", labels=[f"p. {g['page']}" for g in group], cols=cols, cell=cell, title=title, max_edge=VISION_EDGE)
            if ext != ".png":
                from PIL import Image

                with Image.open(made) as im:
                    im.convert("RGB").save(target, quality=85)
            sheets.append({"path": str(target), "pages": [first, last]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if a.format == "json":
        emit({"file": str(path), "pages": count, "sheets": sheets, "rows": rows, "cols": cols}, "json")
        return 0
    more = f" Pages {numbers[-1] + 1}-{count} are not shown; use --pages." if numbers[-1] < count and not a.pages else ""
    announce([s["path"] for s in sheets], f"{len(sheets)} contact sheet(s), {per} pages each. Zoom in on a page with --pages N.{more}")
    return 0


if __name__ == "__main__":
    run_main(main)
