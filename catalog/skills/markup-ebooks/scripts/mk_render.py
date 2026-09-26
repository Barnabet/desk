#!/usr/bin/env python3
"""Render any markup document or e-book to PNG pages through pandoc and Typst, to look at the typeset result.

Takes the same inputs and layout options as mk_convert.py's PDF output (Markdown, HTML, reStructuredText, LaTeX,
Org, AsciiDoc, Typst, DocBook, JATS, EPUB, FB2, notebooks, RTF, DOCX, … and PDF itself), so what you see is what
`mk_convert.py … out.pdf` produces with the same flags. PNGs are sized for vision (long edge 1568 px); pick pages
with --pages, by heading with --section, or where a phrase appears with --find. --sheet adds a contact sheet of
the pages. The PDF of a big document is cached, so rendering other pages later is instant.
Long documents (over about 600 KB of text: a book, a 5 MB web page) are not typeset whole unless you ask for
--pages or --full: the default shows the beginning, --section typesets that section alone (an address from
`mk_read.py --outline`, a heading, or tK / "ch N" for an EPUB) and --find the section where the text appears.
HTML is typeset as a document here, not drawn like a browser would (the web-research skill has a browser).

Examples:
  python3 scripts/mk_render.py report.md                          # first pages + page count
  python3 scripts/mk_render.py report.md --template report --toc --pages 1-3
  python3 scripts/mk_render.py book.epub --sheet --pages 1-24
  python3 scripts/mk_render.py thesis.tex --section "Results"
  python3 scripts/mk_render.py book.epub --section t12             # one TOC entry of a long book, typeset alone
  python3 scripts/mk_render.py huge.md --full --pages 400-402        # the whole document (slow once, then cached)
  python3 scripts/mk_render.py paper.md --find "Table 2" --bibliography refs.bib
  python3 scripts/mk_render.py poster.typ --dpi 200
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, input_file, output_dir, parse_ranges, parser, run_main
from _mk import add_doc_args, detect_reader, main_content_inputs, make_pdf, warning_lines

DEFAULT_PAGES = 8


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="+", help="document(s); several are joined like mk_convert does")
    p.add_argument("--pages", help=f"pages like 1-3,7,last (default: all up to {DEFAULT_PAGES}, else the first {DEFAULT_PAGES})")
    p.add_argument("--section", action="append", help="render the pages of the section whose heading contains this text; an outline address (1.2) or, for an EPUB, tK / 'ch N' typesets that part alone (repeatable)")
    p.add_argument("--find", help="render the pages where this text appears (up to 8)")
    p.add_argument("--full", action="store_true", help="typeset a long document whole (default for long ones: the beginning, or the --section/--find part)")
    p.add_argument("--sheet", action="store_true", help="also a contact sheet of the rendered pages")
    p.add_argument("--dpi", type=float, help="resolution (default: fit --max-edge)")
    p.add_argument("--max-edge", type=int, default=1568, help="long edge in pixels (default 1568; 0 = no cap)")
    p.add_argument("--out", help="folder for the PNGs (default: <name>-pages in the current folder)")
    p.add_argument("--pdf", metavar="PATH", help="also keep the PDF here")
    p.add_argument("--no-cache", action="store_true", help="typeset again instead of reusing the cached PDF")
    p.add_argument("--force", action="store_true", help="replace --pdf if it exists")
    add_doc_args(p)
    add_format(p)
    return p


def pages_for_section(pdf: Any, text: str) -> list[int]:
    """Pages from the PDF outline (Typst writes one bookmark per heading) for a heading containing text."""
    from _mk import pdf_outline

    toc = pdf_outline(pdf)
    low = text.lower()
    for i, (level, title, index, _) in enumerate(toc):
        if low in title.lower() and index is not None:
            start = index + 1
            end = len(pdf)
            for nlevel, _, nindex, ntop in toc[i + 1 :]:
                if nlevel <= level and nindex is not None:
                    # The section runs onto the next heading's page unless that heading opens the page.
                    at_top = ntop is not None and ntop < 0.25
                    end = max(start, nindex if at_top else nindex + 1)
                    break
            return list(range(start, min(end, start + 11) + 1))
    titles = ", ".join(repr(t) for _, t, _, _ in toc[:12])
    raise UsageError(f"no heading contains '{text}'" + (f"; headings: {titles}" if titles else " (the PDF has no outline)"))


def pages_for_text(pdf: Any, text: str, limit: int = 8) -> list[int]:
    found = []
    for i in range(len(pdf)):
        page = pdf[i]
        tp = page.get_textpage()
        try:
            searcher = tp.search(text, match_case=False)
            if searcher.get_next() is not None:
                found.append(i + 1)
        finally:
            tp.close()
            page.close()
        if len(found) >= limit:
            break
    if not found:
        raise SkillError(f"'{text}' does not appear in the typeset document")
    return found


_PART_SPEC = re.compile(r"\s*(?:\d+(?:\.\d+)*|t\d+|ch(?:apter)?\s*\d+)\s*", re.I)


def pick_part(inputs: list[Path], reader: str, a: Any) -> tuple[list[Path], str, str | None]:
    """For a long document (or a --section given as an address), the part to typeset alone: (inputs, reader, label).
    Other documents are typeset whole: (inputs, reader, None)."""
    from _bigdoc import EXCERPT_FROM_CHARS, PARTABLE, doc_chars, section_input

    base = reader.split("+")[0].split("-")[0]
    if len(inputs) != 1 or base not in PARTABLE or a.full:
        return inputs, reader, None
    addressed = bool(a.section) and all(_PART_SPEC.fullmatch(x) for x in a.section)
    if not addressed and (a.pages or doc_chars(inputs[0], reader, a) < EXCERPT_FROM_CHARS):
        return inputs, reader, None
    src, label = section_input(inputs[0], reader, a, specs=a.section, find=a.find if not a.section else None)
    return [src], "markdown", label


def main() -> int:
    a = build_parser().parse_args()
    inputs = [input_file(x) for x in a.inputs]
    first = inputs[0]
    stem = first.stem
    tmpdir = Path(tempfile.mkdtemp(prefix="desk-mk-render-"))
    warnings: list[str] = []
    cached = False
    try:
        excerpt = None  # (what part of a long document was typeset alone)
        if first.suffix.lower() == ".pdf":
            if len(inputs) > 1:
                raise UsageError("render one PDF at a time")
            if a.full:
                raise UsageError("--full is for documents to typeset; a PDF is rendered as it is")
            pdf_path = first
            engine = "pdf"
        else:
            reader = detect_reader(first, a.from_format)
            if a.main:
                if reader != "html":
                    raise UsageError("--main is for HTML input (it keeps the page's main content)")
                inputs, reader = main_content_inputs(inputs, reader, a)
            src_reader = reader
            inputs, reader, excerpt = pick_part(inputs, reader, a)
            res = make_pdf(inputs, reader, a)
            pdf_path = tmpdir / f"{stem}.pdf"
            pdf_path.write_bytes(res["pdf"])
            warnings = res["warnings"]
            cached = res.get("cached", False)
            engine = "typst" if reader == "typst" else f"pandoc ({src_reader}) + typst"
            if excerpt:
                engine += f"; {excerpt} typeset alone"
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            count = len(pdf)
            note = None
            if a.pages:
                pages = parse_ranges(a.pages, count)
            elif a.section and not excerpt:
                pages = sorted({n for x in a.section for n in pages_for_section(pdf, x)})
            elif a.find:
                pages = pages_for_text(pdf, a.find)
            else:
                pages = list(range(1, min(count, DEFAULT_PAGES) + 1))
                if count > DEFAULT_PAGES:
                    note = f"{'The excerpt' if excerpt else 'The document'} has {count} pages; rendered 1-{DEFAULT_PAGES}. More: --pages {DEFAULT_PAGES + 1}-{min(count, 2 * DEFAULT_PAGES)} (or --sheet --pages 1-{min(count, 24)} for an overview)."
            if excerpt and not a.section and not a.find:
                where = "a TOC entry tK or a spine document 'ch N' from mk_read.py FILE" if src_reader.split("+")[0] == "epub" else "an address from mk_read.py FILE --outline"
                note = (note + " " if note else "") + f"This long document was not typeset whole: pick a part with --section ({where}) or --find, or pass --full."
        finally:
            pdf.close()
        from _render import contact_sheet, pdf_to_pngs

        folder = output_dir(a.out or f"{stem}-pages")
        pngs = pdf_to_pngs(pdf_path, folder, pages=pages, dpi=a.dpi, max_edge=a.max_edge, prefix=stem)
        sheet = None
        if a.sheet and len(pngs) > 1:
            sheet = contact_sheet(pngs, folder / f"{stem}-sheet.png", labels=[f"p. {n}" for n in pages], title=f"{first.name} · {count} pages")
        kept = None
        if a.pdf:
            from _common import output_path

            dest = output_path(a.pdf, inputs, a.force)
            dest.write_bytes(pdf_path.read_bytes())
            kept = str(dest)
        info: dict[str, Any] = {"input": str(first), "engine": engine, "pages": count, "rendered": [str(p) for p in pngs], "page_numbers": pages, "cached": cached}
        if excerpt:
            info["excerpt"] = excerpt
        if sheet:
            info["sheet"] = str(sheet)
        if kept:
            info["pdf"] = kept
        if warnings:
            info["warnings"] = warnings
        if a.format == "json":
            from PIL import Image

            sizes = []
            for p in pngs:
                with Image.open(p) as im:
                    sizes.append(list(im.size))
            info["sizes"] = sizes
            emit(info, "json", max_chars=None)
            return 0
        print(f"{first.name}: {count} page{'s' if count != 1 else ''} ({engine}{', cached PDF' if cached else ''}); rendered page{'s' if len(pages) != 1 else ''} {', '.join(map(str, pages))}")
        for line in warning_lines(warnings):
            print(line)
        if kept:
            print(f"PDF: {kept}")
        from _render import announce

        announce([*(str(p) for p in pngs), *([str(sheet)] if sheet else [])], note)
        return 0
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    run_main(main)
