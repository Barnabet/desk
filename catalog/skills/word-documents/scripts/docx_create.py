#!/usr/bin/env python3
"""Create a Word document from Markdown (or a JSON spec for exact control), with professional default styles.

Markdown: headings, paragraphs, **bold**, *italic*, ~~strike~~, `code`, links, images ![caption](chart.png){width=60%},
bullet/numbered/task lists (nested), pipe tables, footnotes [^1], math $E=mc^2$, block quotes, fenced code, and
Desk extras: a line with \\pagebreak starts a new page; a line with [[toc]] places a table of contents there.
Pandoc styles: ::: {custom-style="Quote"} for a paragraph style, [text]{custom-style="Strong"} for a character style.

Layout: --size A4|Letter|21x29.7cm, --landscape, --margins 2cm | 2cm,3cm | top,right,bottom,left, header and
footer text where {page}, {pages}, {date}, {title} become live fields and 'left|center|right' uses three
positions, --toc with real page numbers, --title-page, --style report|classic|modern, or --template to reuse the
styles, page setup, headers and footers of your own .docx/.dotx.

Examples:
  python3 scripts/docx_create.py report.md out/report.docx --title "Q3 Report" --author "Finance" --toc --page-numbers
  python3 scripts/docx_create.py notes.md memo.docx --template letterhead.dotx
  python3 scripts/docx_create.py - brief.docx --style classic --size Letter --footer "Confidential||Page {page} of {pages}" < brief.md
  python3 scripts/docx_create.py --spec spec.json invoice.docx      # JSON spec (see references/json-spec.md)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from _common import SkillError, UsageError, add_format, emit, parser, run_main


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("Markdown:") :])
    ap.add_argument("input", nargs="?", help="Markdown file ('-' for stdin); omit with --spec or --markdown")
    ap.add_argument("output", nargs="?", help="the .docx (or .dotx/.docm) to write")
    ap.add_argument("--markdown", metavar="TEXT", help="Markdown given inline instead of a file")
    ap.add_argument("--spec", metavar="JSON", help="build from a JSON spec (file path or inline JSON) instead of Markdown")
    ap.add_argument("--template", metavar="DOCX", help="reuse the styles, page setup, headers and footers of this .docx/.dotx")
    ap.add_argument("--style", default="report", choices=["report", "classic", "modern"], help="built-in style preset (default report; ignored with --template)")
    ap.add_argument("--font", help="body font (e.g. 'Arial')")
    ap.add_argument("--heading-font", help="heading font")
    ap.add_argument("--font-size", type=float, help="body size in points (e.g. 11)")
    ap.add_argument("--color", metavar="RRGGBB", help="heading colour")
    ap.add_argument("--line-spacing", type=float, help="line spacing multiple (e.g. 1.15)")
    ap.add_argument("--lang", help="document language (e.g. en-GB, fr-FR)")
    ap.add_argument("--title")
    ap.add_argument("--subtitle")
    ap.add_argument("--author")
    ap.add_argument("--date", help="date shown under the title (use 'today' for today's date)")
    ap.add_argument("--subject")
    ap.add_argument("--keywords")
    ap.add_argument("--title-page", action="store_true", help="put the title block on its own cover page")
    ap.add_argument("--toc", action="store_true", help="add a table of contents after the title (or where [[toc]] is)")
    ap.add_argument("--toc-depth", type=int, default=3)
    ap.add_argument("--toc-title", default="Contents")
    ap.add_argument("--no-toc-pages", action="store_true", help="skip the render that fills TOC page numbers (Word updates them on open)")
    ap.add_argument("--number-sections", action="store_true", help="number headings 1, 1.1, 1.1.1")
    ap.add_argument("--size", help="paper: A4 (default with --style), Letter, Legal, A5, A3 or WxH like 21x29.7cm")
    ap.add_argument("--landscape", action="store_true")
    ap.add_argument("--margins", help="2.5cm | 2cm,3cm | top,right,bottom,left")
    ap.add_argument("--header", help="header text; {page} {pages} {date} {title} are fields; 'left|center|right' for three parts")
    ap.add_argument("--footer", help="footer text (same syntax as --header)")
    ap.add_argument("--header-align", default="right", choices=["left", "center", "right"])
    ap.add_argument("--footer-align", default="center", choices=["left", "center", "right"])
    ap.add_argument("--page-numbers", nargs="?", const="{page}", metavar="FORMAT", help="page numbers in the footer (default '{page}'; e.g. 'Page {page} of {pages}')")
    ap.add_argument("--no-smart", action="store_true", help="keep straight quotes and -- as typed")
    ap.add_argument("--resource-path", action="append", default=[], help="folder to find images in (default: the Markdown file's folder and the current folder)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output")
    add_format(ap)
    args = ap.parse_args()

    if (args.spec or args.markdown is not None) and args.input and not args.output:
        args.output, args.input = args.input, None
    if not args.output:
        if args.input and not args.markdown and not args.spec and args.input.lower().endswith((".docx", ".dotx", ".docm")):
            raise UsageError("give the Markdown input and the output path (or --markdown TEXT / --spec JSON)")
        raise UsageError("missing output path")
    out = Path(args.output)
    if out.suffix.lower() not in (".docx", ".dotx", ".docm", ".dotm"):
        raise UsageError("the output must end in .docx (or .dotx/.docm); use docx_convert.py for PDF, ODT or RTF")

    from _common import output_path

    output_path(out, [args.input] if args.input and args.input != "-" else [], force=args.force)
    tmp = Path(tempfile.mkdtemp(prefix="desk-create-"))
    try:
        report = build(args, out, tmp)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    emit(report, args.format, render_report)
    return 0


def build(args, out: Path, tmp: Path) -> dict:
    import datetime as dt

    from _docx import Doc, load
    from _markdown import build_reference, fill_toc, make_title_page, md_to_docx, page_setup, set_header_footer, toc_pages_via_render

    warnings: list[str] = []
    # reference document: a user template, or a preset with overrides
    if args.template:
        tpl = load(args.template)
        ref = tmp / "reference.docx"
        tpl.save(ref, force=True)
        # Pandoc copies the template's body too if we let it: empty it, keep the final section properties.
        rdoc = Doc(ref)
        body = rdoc.body
        from _docx import SECTPR

        for child in list(body):
            if child.tag != SECTPR:
                body.remove(child)
        rdoc.save(ref, force=True)
    else:
        overrides = {"body": args.font, "heading": args.heading_font or args.font, "size": args.font_size, "heading_color": args.color, "line": args.line_spacing, "lang": args.lang, "page": args.size, "margins": args.margins}
        ref = build_reference(tmp / "reference.docx", args.style, overrides)

    date = args.date
    if date and date.lower() == "today":
        today = dt.date.today()
        date = f"{today.day} {today.strftime('%B %Y')}"
    meta = {"title": args.title, "subtitle": args.subtitle, "author": args.author, "date": date, "subject": args.subject, "keywords": args.keywords}
    if args.lang:
        meta["lang"] = args.lang
    if args.toc:
        meta["toc-title"] = args.toc_title
    body_doc = tmp / "body.docx"
    if args.spec:
        from _common import load_json_arg
        from _spec import build_from_spec

        spec = load_json_arg(args.spec)
        inline = args.spec.lstrip().startswith(("{", "[")) or args.spec == "-"
        base = Path(args.spec).parent if not inline and Path(args.spec).exists() else Path.cwd()
        warnings += build_from_spec(spec, ref, body_doc, base_dir=base, metadata=meta)
        source = "JSON spec"
    else:
        if args.markdown is not None:
            md = args.markdown
            res = [Path.cwd()]
        elif args.input == "-":
            md = sys.stdin.read()
            res = [Path.cwd()]
        elif args.input:
            src = Path(args.input)
            if not src.exists():
                raise SkillError(f"{src} does not exist")
            md = src.read_text(encoding="utf-8-sig")
            res = [src.parent.resolve(), Path.cwd()]
        else:
            raise UsageError("give a Markdown file, '-' for stdin, --markdown TEXT or --spec JSON")
        res = [Path(p) for p in args.resource_path] + res
        md_to_docx(md, body_doc, ref, resource_paths=res, smart=not args.no_smart, metadata=meta, toc=args.toc and "[[toc]]" not in md.lower() and "\\toc" not in md, toc_depth=args.toc_depth, number_sections=args.number_sections)
        source = "Markdown"

    doc = Doc(body_doc)
    from _markdown import finish_tables

    if args.template and (args.size or args.margins or args.landscape):
        page_setup(doc, size=args.size, margins=args.margins, orientation="landscape" if args.landscape else None)
    elif args.landscape:
        page_setup(doc, orientation="landscape")
    finish_tables(doc)
    footer = args.footer
    if args.page_numbers:
        if footer:
            warnings.append("--page-numbers ignored because --footer is given; put {page} in the footer text")
        else:
            footer = args.page_numbers
    if args.header:
        set_header_footer(doc, "header", args.header, align=args.header_align)
    if footer:
        set_header_footer(doc, "footer", footer, align=args.footer_align)
    titled = False
    if args.title_page:
        titled = make_title_page(doc)
        if not titled:
            warnings.append("--title-page needs --title (or a title in the Markdown front matter)")
    toc_entries = 0
    if args.toc or any(True for _ in _toc_markers(doc)):
        toc_entries = fill_toc(doc, args.toc_depth, title=args.toc_title)
        if titled and args.toc:
            _page_break_after_toc(doc)
    from _docx import remove_custom_properties

    remove_custom_properties(doc, {"toc-title", "lang", "subtitle", "date"})
    cp = doc.docx.core_properties
    for k in ("title", "author", "subject", "keywords"):
        v = meta.get(k)
        if v:
            setattr(cp, k, v)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)
    cp.created = now  # a new document (the reference or template file's own dates do not carry over)
    cp.modified = now
    cp.revision = 1
    cp.last_modified_by = args.author or cp.last_modified_by or "Desk"
    set_app_properties(doc)
    warnings += doc.save(out, force=True)
    toc_engine = None
    filled = 0
    if toc_entries and not args.no_toc_pages:
        try:
            pages, toc_engine, page_count = toc_pages_via_render(out, args.toc_depth)
        except SkillError as e:
            warnings.append(f"the TOC's page numbers are left for Word to fill on open: laying the document out failed ({e})")
        else:
            doc2 = Doc(out)
            filled = _set_toc_pages(doc2, pages)
            set_app_properties(doc2, pages=page_count)
            doc2.save(out, force=True)
    return summarize(out, source, warnings, toc_entries, toc_engine, filled)


def set_app_properties(doc, pages: int | None = None) -> None:
    """docProps/app.xml says who made the file and how long it is. pandoc's reference file claims Word 12 and
    1 page; file browsers and docx_info would repeat that. Pages are written only when a render counted them."""
    from lxml import etree

    from _docx import NS, T

    ep = NS["ep"]
    words = sum(len((t.text or "").split()) for t in doc.body.iter(T))
    chars = sum(len(t.text or "") for t in doc.body.iter(T))
    for rel in doc.package.rels.values():
        if not rel.reltype.endswith("/extended-properties") or rel.is_external:
            continue
        root = etree.fromstring(rel.target_part.blob)
        for el in list(root):
            if etree.QName(el).localname in ("Pages", "Words", "Characters", "CharactersWithSpaces", "Lines", "Paragraphs", "TotalTime", "Application", "AppVersion", "DocSecurity"):
                root.remove(el)
        values = [("TotalTime", "0")]
        if pages:
            values.append(("Pages", str(pages)))
        values += [("Words", str(words)), ("Characters", str(chars)), ("Application", "Desk word-documents"), ("DocSecurity", "0")]
        for i, (name, val) in enumerate(values):
            el = etree.Element(f"{{{ep}}}{name}")
            el.text = val
            root.insert(i, el)
        rel.target_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _toc_markers(doc):
    from _docx import P, iter_blocks, para_text
    from _markdown import TOC_MARKER

    for el in iter_blocks(doc.body):
        if el.tag == P and para_text(el).strip().startswith(TOC_MARKER):
            yield el


def _page_break_after_toc(doc) -> None:
    """A page break after the last TOC entry, so the body starts on a fresh page."""
    from _docx import FLDCHAR, P, new_el, qn, sub_el, wattr

    ends = [f for f in doc.body.iter(FLDCHAR) if wattr(f, "fldCharType") == "end"]
    for f in ends:
        p = f.getparent().getparent()
        while p is not None and p.tag != P:
            p = p.getparent()
        if p is None:
            continue
        instr = p.getparent()
        # The TOC's end sits in the last TOC paragraph (style TOC n).
        ppr = p.find(qn("w:pPr"))
        ps = ppr.find(qn("w:pStyle")) if ppr is not None else None
        if ps is not None and (wattr(ps, "val") or "").upper().startswith("TOC"):
            br = new_el("w:p")
            sub_el(sub_el(br, "w:r"), "w:br", type="page")
            anchor = p
            parent = p.getparent()
            if parent is not None and parent.tag == qn("w:sdtContent"):
                anchor = parent.getparent()
            anchor.addnext(br)
            return


def _set_toc_pages(doc, pages: dict[str, int]) -> int:
    """Writes page numbers into the cached results of the TOC's PAGEREF fields (by the bookmark each names).
    Returns how many were filled."""
    import re

    from _docx import FLDCHAR, INSTR, T, sub_el, wattr

    n = 0
    for it in list(doc.body.iter(INSTR)):
        m = re.match(r"\s*PAGEREF\s+(\S+)", it.text or "")
        if not m or m.group(1) not in pages:
            continue
        page = pages[m.group(1)]
        # The cached result is the w:t in the run after the 'separate' fldChar.
        nxt = it.getparent().getnext()
        while nxt is not None:
            fc = nxt.find(FLDCHAR)
            if fc is not None and wattr(fc, "fldCharType") == "separate":
                res = nxt.getnext()
                if res is not None and res.find(FLDCHAR) is None:
                    t = res.find(T)
                    if t is None:
                        t = sub_el(res, "w:t")
                    t.text = str(page)
                    n += 1
                break
            nxt = nxt.getnext()
    return n


def summarize(out: Path, source: str, warnings: list[str], toc_entries: int, toc_engine: str | None, filled: int = 0) -> dict:
    from _docx import load
    from _reader import Reader

    doc = load(out)
    rd = Reader(doc, comments="none")
    res = rd.read()
    blocks = res["blocks"]
    words = sum(len(b["text"].split()) for b in blocks)
    report = {
        "output": str(out),
        "source": source,
        "blocks": len(blocks),
        "words": words,
        "headings": sum(1 for b in blocks if b.get("heading")),
        "tables": sum(1 for b in blocks if b["type"] == "table"),
        "images": len(res["images"]),
        "footnotes": len(res["footnotes"]),
        "sections": len(doc.section_elements()),
    }
    if toc_entries:
        report["toc_entries"] = toc_entries
        if toc_engine:
            report["toc_pages"] = f"{filled} of {toc_entries} filled by laying the document out ({toc_engine}) and reading where each heading landed; Word refreshes them on open"
        else:
            report["toc_pages"] = "left for Word to fill on open"
    if warnings:
        report["warnings"] = warnings
    return report


def render_report(r: dict) -> str:
    lines = [f"wrote {r['output']} ({r['source']}): {r['blocks']} blocks, {r['words']} words, {r['headings']} headings, {r['tables']} tables, {r['images']} images, {r['footnotes']} footnotes"]
    if r.get("toc_entries"):
        lines.append(f"table of contents: {r['toc_entries']} entries, page numbers {r['toc_pages']}")
    for w in r.get("warnings", []):
        lines.append(f"warning: {w}")
    lines.append("Check it: python3 scripts/docx_render.py " + r["output"] + "  (then view_image the pages)")
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
