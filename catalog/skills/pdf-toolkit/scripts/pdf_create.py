#!/usr/bin/env python3
"""Create a PDF from Markdown-lite text with reportlab (headings, paragraphs, lists, quotes, code, tables, page breaks).

Examples:
  python3 scripts/pdf_create.py out/report.pdf --input report.md --title "Quarterly report"
  python3 scripts/pdf_create.py out/letter.pdf --input letter.md --page-size letter --margin 2.5
  echo "# Hello" | python3 scripts/pdf_create.py out/hello.pdf

Text uses a Unicode font found on the system (Arial Unicode on macOS) so accents and symbols render; pass --font
with a .ttf to choose another. Links are kept as "text (url)". Prints a JSON summary.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

from _mdlite import Block, parse, spans

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def register_font(path: str | None) -> str:
    for candidate in ([path] if path else FONT_CANDIDATES):
        if candidate and Path(candidate).exists():
            pdfmetrics.registerFont(TTFont("Body", candidate))
            return "Body"
    if path:
        raise SystemExit(f"font not found: {path}")
    return "Helvetica"


def inline(text: str) -> str:
    out = []
    for chunk, fmt in spans(text):
        s = html.escape(chunk, quote=False)
        if fmt.get("bold"):
            s = f"<b>{s}</b>"
        if fmt.get("italic"):
            s = f"<i>{s}</i>"
        if fmt.get("code"):
            s = f'<font face="Courier">{s}</font>'
        out.append(s)
    return "".join(out)


def build(blocks: list[Block], font: str):
    base = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=base["BodyText"], fontName=font, fontSize=10.5, leading=14.5, alignment=TA_LEFT, spaceAfter=6)
    heads = {n: ParagraphStyle(f"H{n}", parent=base[f"Heading{min(n, 6)}"], fontName=font, spaceBefore=10 if n > 1 else 4, spaceAfter=6) for n in range(1, 7)}
    for n, size in ((1, 20), (2, 15), (3, 12.5)):
        heads[n].fontSize, heads[n].leading = size, size * 1.25
    quote = ParagraphStyle("Quote", parent=body, leftIndent=14, textColor=colors.HexColor("#555555"), borderPadding=(0, 0, 0, 6))
    code = ParagraphStyle("Code", parent=base["Code"], fontSize=8.5, leading=11, backColor=colors.HexColor("#F3F0E8"), borderPadding=6, spaceAfter=8)
    cell = ParagraphStyle("Cell", parent=body, fontSize=9.5, leading=12, spaceAfter=0)

    story = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind in ("bullet", "number"):
            kind = b.kind
            items = []
            while i < len(blocks) and blocks[i].kind == kind:
                items.append(ListItem(Paragraph(inline(blocks[i].text), body), leftIndent=14 + 14 * blocks[i].level))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet" if kind == "bullet" else "1", start=None if kind == "bullet" else 1, bulletFontName=font, leftIndent=14))
            continue
        if b.kind == "heading":
            story.append(Paragraph(inline(b.text), heads[b.level]))
        elif b.kind == "quote":
            story.append(Paragraph(inline(b.text), quote))
        elif b.kind == "code":
            story.append(Preformatted(b.text, code))
        elif b.kind == "table":
            width = max(len(r) for r in b.rows)
            data = [[Paragraph(inline(r[c]) if c < len(r) else "", cell) for c in range(width)] for r in b.rows]
            t = Table(data, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEAE0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            for c in range(width):
                if data[0][c]:
                    data[0][c] = Paragraph(f"<b>{inline(b.rows[0][c]) if c < len(b.rows[0]) else ''}</b>", cell)
            story.extend([t, Spacer(1, 8)])
        elif b.kind == "pagebreak":
            story.append(PageBreak())
        else:
            story.append(Paragraph(inline(b.text), body))
        i += 1
    return story


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output")
    ap.add_argument("--input", "-i", default="-", help="Markdown-lite file, or - for stdin (default)")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--page-size", choices=["a4", "letter"], default="a4")
    ap.add_argument("--margin", type=float, default=2.0, help="Margins in cm (default 2)")
    ap.add_argument("--font", help="Path to a .ttf for body text")
    args = ap.parse_args()
    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    font = register_font(args.font)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        args.output,
        pagesize=A4 if args.page_size == "a4" else letter,
        leftMargin=args.margin * cm, rightMargin=args.margin * cm, topMargin=args.margin * cm, bottomMargin=args.margin * cm,
        title=args.title, author=args.author,
    )
    story = build(parse(text), font)
    page_count = {"n": 0}

    def count(canvas, _doc):
        page_count["n"] = canvas.getPageNumber()

    doc.build(story, onFirstPage=count, onLaterPages=count)
    print(json.dumps({"output": args.output, "pages": page_count["n"], "font": font}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
