#!/usr/bin/env python3
"""Render Word document pages to PNG images sized for vision, so you can look at them with view_image.

Engines: LibreOffice when installed (exact: Word-compatible layout), otherwise the built-in renderer (Typst:
close, but line and page breaks can differ). The output says which engine drew the pages. Also renders .odt,
.rtf and .doc (the last needs LibreOffice).

The document is laid out once and the layout is cached: rendering other pages later is instant. Without --pages,
at most the first 20 pages are drawn; on a long document, pick pages with --pages, or let --find / --block pick
the pages where a phrase or a docx_read block address appears.

Examples:
  python3 scripts/docx_render.py report.docx                         # pages -> report_pages/page-01.png …
  python3 scripts/docx_render.py report.docx --pages 1-3 --out renders/
  python3 scripts/docx_render.py report.docx --sheet                 # plus one contact sheet of the pages
  python3 scripts/docx_render.py thesis.docx --find "Table 4.2"      # the pages where the phrase is
  python3 scripts/docx_render.py thesis.docx --block 5201            # the page holding docx_read's block 5201
  python3 scripts/docx_render.py redline.docx --changes accept       # the document as if changes were accepted
  python3 scripts/docx_render.py report.docx --engine builtin --dpi 110 --pdf report-preview.pdf
"""

from __future__ import annotations

import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, parser, run_main

DEFAULT_PAGES = 20
FIND_PAGES = 8


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Engines:") :])
    ap.add_argument("input", help=".docx/.docm/.dotx/.dotm (or .odt/.rtf/.doc)")
    ap.add_argument("--out", help="folder for the PNGs (default: <name>_pages in the current folder)")
    ap.add_argument("--pages", help=f"pages to render: 1-3,7,10- or last (default: all, at most the first {DEFAULT_PAGES})")
    ap.add_argument("--find", metavar="TEXT", help=f"render the pages whose text contains TEXT (case-insensitive; up to {FIND_PAGES})")
    ap.add_argument("--block", type=int, metavar="N", help="render the page where block N (a docx_read index) starts")
    ap.add_argument("--dpi", type=float, help="resolution (default: fit the long edge to 1568 px, about 135 dpi for A4)")
    ap.add_argument("--max-edge", type=int, default=None, help="cap on the long edge in pixels (default 1568; 0 = no cap)")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet of the rendered pages")
    ap.add_argument("--engine", choices=["auto", "libreoffice", "builtin"], default="auto")
    ap.add_argument("--changes", choices=["markup", "accept", "reject"], default="markup", help="tracked changes: shown as markup (default), accepted or rejected")
    ap.add_argument("--pdf", metavar="PATH", help="also keep the intermediate PDF here")
    ap.add_argument("--force", action="store_true", help="overwrite --pdf if it exists")
    ap.add_argument("--no-cache", action="store_true", help="lay the document out again instead of using the cached layout")
    add_format(ap)
    args = ap.parse_args()
    if sum(x is not None for x in (args.pages, args.find, args.block)) > 1:
        raise UsageError("use one of --pages, --find and --block")

    import os

    from _common import input_file, output_dir, output_path

    if args.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    src = input_file(args.input)
    out_dir = output_dir(args.out or (Path.cwd() / f"{src.stem}_pages"))
    pdf_keep = output_path(args.pdf, [src], force=args.force) if args.pdf else None
    target = None
    if args.find is not None:
        target = ("find", args.find)
    elif args.block is not None:
        target = ("block", args.block)
    result = render(src, out_dir, pages=args.pages, dpi=args.dpi, max_edge=args.max_edge, engine=args.engine, changes=args.changes, sheet=args.sheet, pdf_keep=pdf_keep, target=target)
    if args.format == "json":
        emit(result, "json")
    else:
        from _render import announce

        note = f"engine: {result['engine']}" + (f" · {'; '.join(result['notes'])}" if result["notes"] else "")
        print(f"{result['rendered']} of {result['page_count']} pages rendered from {src.name} ({result['seconds']}s)" + (f": pages {result['pages_label']}" if result.get("pages_label") else ""))
        announce(result["images"] + ([result["sheet"]] if result.get("sheet") else []), note)
    return 0


def render(src: Path, out_dir: Path, *, pages: str | None = None, dpi: float | None = None, max_edge: int | None = None, engine: str = "auto", changes: str = "markup",
           sheet: bool = False, pdf_keep: Path | None = None, target: tuple[str, Any] | None = None) -> dict:
    from _cache import release
    from _docx import document_pdf
    from _render import VISION_EDGE, contact_sheet, pdf_page_count, pdf_to_pngs

    t0 = time.time()
    notes: list[str] = []
    pdf, label, more, entry = document_pdf(src, engine, changes)
    layout_seconds = round(time.time() - t0, 2)
    notes += more
    try:
        count = pdf_page_count(pdf)
        edge = VISION_EDGE if max_edge is None else max_edge
        found: dict[str, Any] | None = None
        if target is not None:
            numbers, found = locate(src, pdf, count, target, changes)
            if len(numbers) > FIND_PAGES:
                notes.append(f"{len(numbers)} pages match; rendered the first {FIND_PAGES} (all: {_ranges(numbers)})")
                numbers = numbers[:FIND_PAGES]
        else:
            numbers, skipped = page_numbers(pages, count)
            if skipped:
                notes.append(f"skipped {', '.join(skipped)}: the document has {count} page{'s' if count != 1 else ''}")
            if pages is None and count > DEFAULT_PAGES:
                numbers = numbers[:DEFAULT_PAGES]
                notes.append(f"a long document: rendered pages 1-{DEFAULT_PAGES} of {count}; choose others with --pages {DEFAULT_PAGES + 1}-{min(count, 2 * DEFAULT_PAGES)}, or --find TEXT / --block N")
        pngs = pdf_to_pngs(pdf, out_dir, pages=numbers, dpi=dpi, max_edge=edge)
        result: dict[str, Any] = {"input": str(src), "engine": label, "page_count": count, "rendered": len(pngs), "pages": numbers, "pages_label": _ranges(numbers),
                                  "images": [str(p) for p in pngs], "notes": notes}
        if found is not None:
            result["located"] = found
        if pdf_keep is not None:
            shutil.copyfile(pdf, pdf_keep)
            result["pdf"] = str(pdf_keep)
        if sheet and pngs:
            sheet_path = out_dir / "sheet.png"
            contact_sheet(pngs, sheet_path, labels=[f"page {n}" for n in numbers[: len(pngs)]], title=f"{src.name} · {count} pages")
            result["sheet"] = str(sheet_path)
        result["seconds"] = round(time.time() - t0, 2)
        result["layout_seconds"] = layout_seconds
        return result
    finally:
        release(entry)


def _ranges(nums: list[int]) -> str:
    """[1, 2, 3, 7] -> '1-3, 7'."""
    out: list[str] = []
    start = prev = None
    for n in list(nums) + [None]:
        if start is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    return ", ".join(out)


# ── locating pages by text ──────────────────────────────────────────────


def _norm(s: str) -> str:
    """Text for matching across engines: compatibility forms (ligatures), no hyphenation at line ends, single spaces."""
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "").replace("\u200b", "")
    s = re.sub(r"-\s*[\r\n]+\s*", "", s)
    s = re.sub(r"[‐‑‒–—]", "-", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(s.split()).lower()


def page_texts(pdf: Path) -> list[str]:
    """Normalised text of every page of the laid-out PDF, cached by the PDF's content."""
    from _cache import cached_json

    def compute() -> list[str]:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf))
        try:
            out = []
            for i in range(len(doc)):
                page = doc[i]
                tp = page.get_textpage()
                try:
                    out.append(_norm(tp.get_text_range()))
                finally:
                    tp.close()
                    page.close()
            return out
        finally:
            doc.close()

    return cached_json(pdf, "docx-page-text", {}, "1", compute)


def locate(src: Path, pdf: Path, count: int, target: tuple[str, Any], changes: str) -> tuple[list[int], dict[str, Any]]:
    """The pages matching --find TEXT or --block N, and what was looked for."""
    texts = page_texts(pdf)
    kind, value = target
    if kind == "find":
        needle = _norm(str(value))
        if not needle:
            raise UsageError("--find needs some text")
        hits = [i + 1 for i, t in enumerate(texts) if needle in t]
        if not hits:
            raise SkillError(f"{value!r} is not on any of the {count} pages (as laid out). Check the wording with docx_read.py --find, which also searches notes and comments")
        return hits, {"find": value, "pages": hits}
    # --block N: the block's first words, found in page order (the first page where they appear after the previous
    # block's page would be exact; the first occurrence is right unless the words repeat earlier).
    from docx_read import read_document

    data = read_document(src, changes=changes)  # docx_read's default read: usually cached already
    blocks = data["result"]["blocks"]
    n = int(value)
    if n < 0 or n >= data["total"]:
        raise SkillError(f"block {n} does not exist (the document has blocks 0-{data['total'] - 1})")
    # The nearest block at or after N with enough text to recognise.
    probe = None
    for b in blocks[n : n + 20]:
        words = _norm(b.get("text", "").replace("\n", " ").replace(" | ", " ")).split()
        if len(" ".join(words)) >= 12:
            probe = (b["index"], " ".join(words[:10]))
            break
    if probe is None:
        raise SkillError(f"block {n} and the blocks after it have no text to look for; use --pages")
    idx, phrase = probe
    # Earlier text narrows ambiguity: count how often the phrase occurs in the blocks before it.
    before = sum(_norm(b.get("text", "")).count(phrase) for b in blocks[:idx])
    seen = 0
    for i, t in enumerate(texts):
        c = t.count(phrase)
        if seen + c > before:
            return [i + 1], {"block": n, "matched_block": idx, "phrase": phrase, "pages": [i + 1]}
        seen += c
    # Lines can break inside the phrase differently: retry with fewer words.
    short = " ".join(phrase.split()[:4])
    hits = [i + 1 for i, t in enumerate(texts) if short in t]
    if hits:
        return hits[:1], {"block": n, "matched_block": idx, "phrase": short, "pages": hits[:1], "approximate": True}
    raise SkillError(f"could not find block {n}'s text on the rendered pages; use docx_read.py --blocks {n} and --pages")


def page_numbers(spec: str | None, count: int) -> tuple[list[int], list[str]]:
    """The requested pages that exist (1-based), and the parts of the request past the end of the document."""
    from _common import parse_ranges

    if spec is None:
        return parse_ranges(None, count), []
    nums: list[int] = []
    skipped: list[str] = []
    for part in spec.split(","):
        p = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", p)
        if m and int(m.group(1)) <= count < int(m.group(2)):
            p = f"{m.group(1)}-{count}"
        try:
            nums += [n for n in parse_ranges(p, count) if n not in nums]
        except UsageError as e:
            if "out of range" not in str(e):
                raise
            skipped.append(f"page {part.strip()}")
    if not nums:
        raise SkillError(f"no such page: {spec} (the document has {count} page{'s' if count != 1 else ''})")
    return nums, skipped


if __name__ == "__main__":
    run_main(main)
