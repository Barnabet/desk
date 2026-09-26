#!/usr/bin/env python3
"""Render slides to PNG images sized for vision (one per slide), plus optional contact sheets."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, output_dir, parse_ranges, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_render.py deck.pptx --out-dir renders            # every slide → renders/deck-01.png …
  python3 scripts/pptx_render.py deck.pptx --slides 2,5 --out-dir renders --force
  python3 scripts/pptx_render.py deck.pptx --sheet-only --out-dir renders # contact sheets: 20 slides per image

Engines: LibreOffice when installed (exact: what PowerPoint shows, give or take fonts), otherwise the built-in
renderer (a close approximation drawn with Typst). --engine picks one. The output names the engine used.
Images are 1568 px on the long edge by default (--width to change). Contact sheets hold --per-sheet slides each
(default 20, readable thumbnails), so a big deck gives several sheets. Renders are cached per file content: a
second render of the same slides is instant (--no-cache to bypass). Then look at them with view_image.
"""

RENDER_VERSION = "2"
THUMB_EDGE = 360  # --sheet-only renders slides at the size the sheet shows them (its 360 px cells): no resampling


def main() -> int:
    ap = parser("Render .pptx/.pptm/.potx/.ppsx (and .ppt/.odp with LibreOffice) slides to PNG.", EPILOG)
    ap.add_argument("file")
    ap.add_argument("--out-dir", help="folder for the PNGs (default: <name>-slides in the current folder)")
    ap.add_argument("--slides", help="slide numbers or ranges, like 1-3,7 (default all)")
    ap.add_argument("--engine", choices=["auto", "libreoffice", "builtin"], default="auto")
    ap.add_argument("--width", type=int, default=0, help="long edge in pixels (default 1568)")
    ap.add_argument("--sheet", action="store_true", help="also write contact sheets of the rendered slides")
    ap.add_argument("--sheet-only", action="store_true", help="write only the contact sheets (fast: small renders)")
    ap.add_argument("--per-sheet", type=int, default=20, help="slides per contact sheet (default 20)")
    ap.add_argument("--skip-hidden", action="store_true", help="leave out hidden slides")
    ap.add_argument("--force", action="store_true", help="overwrite existing PNGs")
    ap.add_argument("--no-cache", action="store_true", help="do not use or fill the file cache")
    add_format(ap)
    a = ap.parse_args()
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    from _render import VISION_EDGE

    edge = a.width or VISION_EDGE
    if edge < 64 or edge > 8000:
        raise UsageError("--width must be between 64 and 8000 pixels")
    if a.per_sheet < 1 or a.per_sheet > 200:
        raise UsageError("--per-sheet must be between 1 and 200")
    src = Path(a.file)
    out = output_dir(a.out_dir or f"{src.stem}-slides")
    result = render(src, out, a.slides, a.engine, edge, sheet=a.sheet or a.sheet_only, sheet_only=a.sheet_only, skip_hidden=a.skip_hidden, force=a.force, per_sheet=a.per_sheet)
    if a.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        from _render import announce

        head = f"Rendered {len(result['slides'])} slide(s) with {result['engine_label']}."
        if result.get("cached"):
            head += f" {result['cached']} came from the cache."
        print(head)
        paths = [s["png"] for s in result["slides"] if s.get("png")] + result.get("sheets", [])
        note = None
        if result.get("not_drawn"):
            note = "Drawn as grey boxes: " + ", ".join(result["not_drawn"]) + "."
        announce(paths, note)
    return 0


def render(src: Path, out: Path, slides: str | None, engine: str, edge: int, sheet: bool = False, sheet_only: bool = False, skip_hidden: bool = False, force: bool = False, prefix: str | None = None, per_sheet: int = 20) -> dict[str, Any]:
    from _deck import ooxml_source
    from _fastdeck import deck_map
    from _render import find_soffice

    src = Path(src)
    path = ooxml_source(src)
    m = deck_map(path)
    count = m["slide_count"]
    if not count:
        raise SkillError(f"{src.name} has no slides")
    numbers = parse_ranges(slides, count)
    info = {s["number"]: s for s in m["slides"]}
    if skip_hidden:
        numbers = [n for n in numbers if not info[n]["hidden"]]
    if not numbers:
        raise SkillError("no slides to render")
    soffice = find_soffice() if engine in ("auto", "libreoffice") else None
    if engine == "libreoffice" and not soffice:
        raise SkillError("LibreOffice is not installed; use --engine builtin (or auto)")
    prefix = prefix or src.stem
    width = max(len(str(count)), 2)
    targets = {n: out / f"{prefix}-{n:0{width}d}.png" for n in numbers}
    n_sheets = -(-len(numbers) // per_sheet) if sheet else 0
    sheet_paths = [out / f"{prefix}-sheet.png"] if n_sheets == 1 else [out / f"{prefix}-sheet-{i + 1}.png" for i in range(n_sheets)]
    if not force:
        clash = [p for p in ([] if sheet_only else list(targets.values())) + sheet_paths if p.exists()]
        if clash:
            raise SkillError(f"{clash[0]} already exists; pass --force to overwrite, or choose another --out-dir")
    eng = "libreoffice" if soffice else "builtin"
    render_edge = min(edge, THUMB_EDGE) if sheet_only else edge
    pngs, notes, cached, transient_dirs = slide_pngs(path, numbers, eng, render_edge, count, info)
    if eng == "libreoffice":
        label = "LibreOffice (exact)"
    elif find_soffice():
        label = "the built-in renderer (approximate; LibreOffice is installed: --engine libreoffice renders exactly)"
    else:
        label = "the built-in renderer (approximate; install LibreOffice for exact rendering)"
    result: dict[str, Any] = {"engine": eng, "engine_label": label, "slides": [], "cached": cached}
    produced: list[Path] = []
    try:
        from PIL import Image

        for n in numbers:
            p = pngs.get(n)
            if p is None:
                continue
            if sheet_only:
                produced.append(p)
                result["slides"].append({"number": n})
                continue
            dest = targets[n]
            shutil.copyfile(p, dest)
            with Image.open(dest) as im:
                size = im.size
            produced.append(dest)
            result["slides"].append({"number": n, "png": str(dest), "width": size[0], "height": size[1]})
        if sheet:
            from _render import contact_sheet

            done = [n for n in numbers if pngs.get(n)]
            sheets = []
            for i, sp in enumerate(sheet_paths):
                part = done[i * per_sheet:(i + 1) * per_sheet]
                if not part:
                    continue
                imgs = produced[i * per_sheet:(i + 1) * per_sheet]
                labels = [f"{n}. {info[n]['title'][:40]}" + (" (hidden)" if info[n]["hidden"] else "") for n in part]
                rng = f"slides {part[0]}-{part[-1]}" if len(part) > 1 else f"slide {part[0]}"
                title = f"{src.name}: {rng} of {count}" + (f" (sheet {i + 1}/{len(sheet_paths)})" if len(sheet_paths) > 1 else "")
                contact_sheet(imgs, sp, labels=labels, title=title, cols=_cols(len(part)))
                sheets.append(str(sp))
            result["sheets"] = sheets
            result["sheet"] = sheets[0] if sheets else None
    finally:
        for d in transient_dirs:
            shutil.rmtree(d, ignore_errors=True)
    drawn: list[str] = []
    for n in numbers:
        for w in notes.get(n, []):
            if w not in drawn:
                drawn.append(w)
    if drawn:
        result["not_drawn"] = drawn
    return result


def _cols(n: int) -> int:
    return 4 if n > 9 else 3 if n > 4 else 2 if n > 1 else 1


def slide_pngs(path: Path, numbers: list[int], eng: str, edge: int, count: int, info: dict[int, Any]) -> tuple[dict[int, Path], dict[int, list[str]], int, list[Path]]:
    """PNG files for the slides, from the cache where possible: ({n: png}, {n: notes}, cached count, temp dirs)."""
    from _cache import cached_dir, lookup, transient

    extra: dict[str, Any] = {}
    if eng == "builtin":
        from _fonts import font_signature

        extra["fonts"] = font_signature()

    def params(n: int) -> dict[str, Any]:
        return {"engine": eng, "edge": edge, "n": n, **extra}

    have: dict[int, Path] = {}
    notes: dict[int, list[str]] = {}
    missing: list[int] = []
    for n in numbers:
        d = lookup(path, "pptx-png", params(n), RENDER_VERSION)
        if d is not None and (d / "slide.png").exists():
            have[n] = d / "slide.png"
            try:
                notes[n] = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("not_drawn", [])
            except (OSError, ValueError):
                notes[n] = []
        else:
            missing.append(n)
    cached = len(have)
    temps: list[Path] = []
    if missing:
        data, drawn = _render_missing(path, missing, eng, edge, count, info)
        for n in missing:
            if n not in data:
                continue

            def build(tmp: Path, n: int = n) -> None:
                (tmp / "slide.png").write_bytes(data[n])
                (tmp / "meta.json").write_text(json.dumps({"not_drawn": drawn.get(n, [])}), encoding="utf-8")

            d = cached_dir(path, "pptx-png", params(n), RENDER_VERSION, build)
            if transient(d):
                temps.append(d)
            have[n] = d / "slide.png"
            notes[n] = drawn.get(n, [])
    return have, notes, cached, temps


def _render_missing(path: Path, numbers: list[int], eng: str, edge: int, count: int, info: dict[int, Any]) -> tuple[dict[int, bytes], dict[int, list[str]]]:
    if eng == "builtin":
        from _deck import open_deck
        from _typrender import render_pngs

        from _ooxml import EMU_PER_IN

        prs = open_deck(path, checked=True).prs
        w_in = (prs.slide_width or 9144000) / EMU_PER_IN
        h_in = (prs.slide_height or 6858000) / EMU_PER_IN
        ppi = edge / max(w_in, h_in)
        pages, drawn = render_pngs(prs, str(path), numbers, ppi)
        return dict(zip(numbers, pages)), drawn
    import tempfile

    from _render import pdf_page_count, pdf_to_pngs

    pdf = lo_pdf(path)
    tmpdir = Path(tempfile.mkdtemp(prefix="desk-pptx-lo-png-"))
    try:
        pages_in_pdf = pdf_page_count(pdf)
        if pages_in_pdf == count:
            page_of = {n: n for n in numbers}
        else:
            # Older LibreOffice skips hidden slides: map the visible ones in order.
            visible = [n for n in range(1, count + 1) if not info[n]["hidden"]]
            page_of = {n: visible.index(n) + 1 for n in numbers if n in visible and visible.index(n) < pages_in_pdf}
        pages = sorted(set(page_of.values()))
        if not pages:
            return {}, {}
        pngs = pdf_to_pngs(pdf, tmpdir, pages=pages, max_edge=edge, prefix="p")
        by_page = {pg: p.read_bytes() for pg, p in zip(pages, pngs)}
        return {n: by_page[pg] for n, pg in page_of.items()}, {}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        from _cache import release

        release(pdf.parent)


def lo_pdf(path: Path) -> Path:
    """The whole deck as a PDF made by LibreOffice (hidden slides included), cached per file content."""
    from _cache import cached_file

    from _render import office_convert

    fmt = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"},"ExportNotesPages":{"type":"boolean","value":"false"}}'

    def build(target: Path) -> None:
        pdf = office_convert(path, fmt)
        try:
            shutil.move(str(pdf), str(target))
        finally:
            shutil.rmtree(pdf.parent, ignore_errors=True)

    return cached_file(path, "pptx-lo-pdf", {"hidden": True}, "1", build, name="deck.pdf")


if __name__ == "__main__":
    run_main(main)
