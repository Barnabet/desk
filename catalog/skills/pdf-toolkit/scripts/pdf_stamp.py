#!/usr/bin/env python3
"""Stamp pages: a text or image watermark, page numbers ("Page {n} of {total}"), header and footer text, and
Bates numbering. Stamps are drawn with Typst on transparent overlay pages and merged onto the pages, so the
original content is untouched (rotated pages and odd page boxes are handled). Several stamps combine in one run.

Placeholders in --page-numbers, --header, --footer: {n} number, {total} last number, {page} physical page,
{pages} page count, {label} page label, {date} today, {file} file name, {title} the document title (--title, else the
PDF's Title; empty with a note when it has none).
Positions: top-left, top, top-right, left, center, right, bottom-left, bottom, bottom-right.

Examples:
  python3 scripts/pdf_stamp.py in.pdf out.pdf --watermark DRAFT
  python3 scripts/pdf_stamp.py in.pdf out.pdf --watermark "CONFIDENTIAL" --opacity 0.12 --rotation 30 --color red
  python3 scripts/pdf_stamp.py in.pdf out.pdf --page-numbers "Page {n} of {total}" --pages 2- --start 1
  python3 scripts/pdf_stamp.py in.pdf out.pdf --header "Acme Corp · {title}" --footer "{date}" --footer-align right
  python3 scripts/pdf_stamp.py in.pdf out.pdf --bates ACME --bates-start 1201 --bates-digits 6
  python3 scripts/pdf_stamp.py in.pdf out.pdf --image logo.png --image-width 90pt --position top-right --opacity 0.9
"""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from _common import UsageError, human_size, input_file, output_dir, output_path, parser, run_main
from _pdfkit import color_hex, emit, geometry_of, info_strings, new_writer, open_reader, page_labels, parse_color, parse_length, parse_pages, pdf_input, pdfium_source, plural, render_pages, save_writer
from _render import announce, typst_compile, typst_string

POSITIONS = {
    "top-left": "top + left", "top": "top + center", "top-right": "top + right",
    "left": "horizon + left", "center": "horizon + center", "right": "horizon + right",
    "bottom-left": "bottom + left", "bottom": "bottom + center", "bottom-right": "bottom + right",
}
SANS = '("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "Liberation Sans", "Libertinus Serif")'
MONO = '("DejaVu Sans Mono",)'


def fill(template: str, ctx: dict[str, str]) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", v)
    return out


def typst_color(rgb: tuple[float, float, float], opacity: float = 1.0) -> str:
    c = f'rgb("{color_hex(rgb)}")'
    if opacity < 1:
        c += f".transparentize({round((1 - opacity) * 100, 1)}%)"
    return c


def placed(position: str, body: str, margin: float) -> str:
    align = POSITIONS[position]
    return f"place(top + left, box(width: 100%, height: 100%, inset: {margin:.2f}pt, place({align}, {body})))"


def build_overlay(a: Any, targets: list[dict[str, Any]], build: Path) -> bytes:
    lines = ["#set text(size: %.2fpt)" % a.font_size]
    font = f'("{a.font}",) + {SANS}' if a.font else SANS
    lines.append(f"#set text(font: {font})")
    image_ref = None
    if a.image:
        from PIL import Image

        src = input_file(a.image)
        with Image.open(src) as im:
            im = im.convert("RGBA")
            if a.opacity is not None and a.opacity < 1:
                alpha = im.getchannel("A").point(lambda v: round(v * a.opacity))
                im.putalpha(alpha)
            im.save(build / "stamp.png")
        image_ref = "stamp.png"
    for t in targets:
        parts: list[str] = []
        W, H = t["width"], t["height"]
        if a.watermark:
            op = a.opacity if a.opacity is not None else 0.15
            col = typst_color(parse_color(a.color or "gray"), op)
            txt = typst_string(fill(a.watermark, t["ctx"]))
            size_expr = f"{a.size:.2f}pt" if a.size else "base * fit"
            rot = -(a.rotation if a.rotation is not None else 45)
            wm = (
                f"context {{ let base = 100pt; let m = measure(text(size: base, weight: \"bold\", {txt})); "
                f"let diag = calc.sqrt(calc.pow({W:.2f}, 2) + calc.pow({H:.2f}, 2)); "
                f"let fit = calc.min(0.7 * diag / calc.max(m.width / 1pt, 1.0), 0.45 * {min(W, H):.2f} / calc.max(m.height / 1pt, 1.0)); "
                f"rotate({rot}deg, reflow: false, text(size: {size_expr}, weight: \"bold\", fill: {col}, {txt})) }}"
            )
            parts.append(placed(a.position if a.position else "center", wm, 0))
        if image_ref:
            width = a.image_width or "25%"
            width_expr = width if width.endswith("%") else f"{parse_length(width):.2f}pt"
            body = f'rotate({-(a.rotation or 0)}deg, reflow: true, image("{image_ref}", width: {width_expr}))'
            parts.append(placed(a.position or "center", body, a.margin))
        if a.header:
            parts.append(placed(f"top-{a.header_align}" if a.header_align != "center" else "top", f"text({typst_string(fill(a.header, t['ctx']))})", a.margin))
        if a.footer:
            parts.append(placed(f"bottom-{a.footer_align}" if a.footer_align != "center" else "bottom", f"text({typst_string(fill(a.footer, t['ctx']))})", a.margin))
        if a.page_numbers:
            parts.append(placed(a.number_position, f"text({typst_string(fill(a.page_numbers, t['ctx']))})", a.margin))
        if a.bates:
            box = f"box(fill: white.transparentize(10%), inset: 2pt, text(font: {MONO}, weight: \"bold\", {typst_string(t['bates'])}))"
            parts.append(placed(a.bates_position, box, a.margin))
        body = "\n  ".join(f"#{p}" for p in parts)
        lines.append(f"#page(width: {W:.3f}pt, height: {H:.3f}pt, margin: 0pt)[\n  {body}\n]")
    (build / "overlay.typ").write_text("\n".join(lines), encoding="utf-8")
    return typst_compile(build / "overlay.typ", fmt="pdf", root=build)[0]


def stamp_box(position: str, text: str, W: float, H: float, margin: float, size: float) -> tuple[float, float, float, float]:
    """The approximate view box a short stamp text takes at a position (for the overlap check)."""
    w, h = min(W - 2 * margin, len(text) * size * 0.6 + 4), size * 1.4
    vert, _, horiz = position.partition("-") if "-" in position else (position, "", "center")
    if position in ("left", "right"):
        vert, horiz = "horizon", position
    elif position in ("top", "bottom"):
        vert, horiz = position, "center"
    elif position == "center":
        vert, horiz = "horizon", "center"
    x0 = margin if horiz == "left" else W - margin - w if horiz == "right" else (W - w) / 2
    y0 = margin if vert == "top" else H - margin - h if vert == "bottom" else (H - h) / 2
    return max(0.0, x0 - 2), max(0.0, y0 - 2), min(W, x0 + w + 2), min(H, y0 + h + 2)


def overlaps(src: Path, password: str | None, targets: list[dict[str, Any]], a: Any) -> dict[str, list[int]]:
    """Pages where a header, footer, page number or Bates number would sit on text already on the page."""
    from _pdfkit import open_pdfium, pdfium_geometry

    found: dict[str, list[int]] = {}
    doc = open_pdfium(src, password)
    try:
        for t in targets:
            elems = []
            if a.header:
                elems.append(("header", f"top-{a.header_align}" if a.header_align != "center" else "top", fill(a.header, t["ctx"])))
            if a.footer:
                elems.append(("footer", f"bottom-{a.footer_align}" if a.footer_align != "center" else "bottom", fill(a.footer, t["ctx"])))
            if a.page_numbers:
                elems.append(("page numbers", a.number_position, fill(a.page_numbers, t["ctx"])))
            if a.bates:
                elems.append(("Bates numbers", a.bates_position, t["bates"]))
            if not elems:
                break
            page = doc[t["page"] - 1]
            geo = pdfium_geometry(page)
            tp = page.get_textpage()
            for name, pos, text in elems:
                l, b, r, top = geo.rect_from_view(*stamp_box(pos, text, geo.width, geo.height, a.margin, a.font_size))
                if tp.get_text_bounded(left=l, bottom=b, right=r, top=top).strip():
                    found.setdefault(name, []).append(t["page"])
            tp.close()
            page.close()
    finally:
        doc.close()
    return found


def ranges(nums: list[int]) -> str:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("input")
    p.add_argument("out")
    p.add_argument("--pages", help="pages to stamp, like 2- (default: all)")
    g = p.add_argument_group("watermark")
    g.add_argument("--watermark", "--text", dest="watermark", help="watermark text (placeholders allowed)")
    g.add_argument("--image", help="image watermark or logo (PNG, JPEG, …)")
    g.add_argument("--image-width", help="image width: 25%% of the page (default), or a length like 90pt, 3cm")
    g.add_argument("--opacity", type=float, help="0-1 (text watermark default 0.15, image 1)")
    g.add_argument("--rotation", type=float, help="degrees counter-clockwise (text watermark default 45)")
    g.add_argument("--size", type=float, help="watermark font size in points (default: fit the page diagonal)")
    g.add_argument("--color", help="watermark color: a name, #rrggbb or r,g,b (default gray)")
    g.add_argument("--position", choices=list(POSITIONS), help="watermark or image position (default center)")
    g.add_argument("--under", action="store_true", help="put the watermark under the page content")
    g = p.add_argument_group("page numbers, header, footer, Bates")
    g.add_argument("--page-numbers", nargs="?", const="{n} / {total}", help='format, default "{n} / {total}"')
    g.add_argument("--number-position", choices=list(POSITIONS), default="bottom", help="default bottom (center)")
    g.add_argument("--start", type=int, default=1, help="number of the first stamped page (default 1)")
    g.add_argument("--header", help="header text")
    g.add_argument("--header-align", choices=["left", "center", "right"], default="center")
    g.add_argument("--footer", help="footer text")
    g.add_argument("--footer-align", choices=["left", "center", "right"], default="center")
    g.add_argument("--bates", metavar="PREFIX", help="Bates numbers: PREFIX followed by a zero-padded counter")
    g.add_argument("--bates-start", type=int, default=1)
    g.add_argument("--bates-digits", type=int, default=6)
    g.add_argument("--bates-position", choices=list(POSITIONS), default="bottom-right")
    g.add_argument("--title", dest="title_text", help="text for {title} (default: the PDF's own Title)")
    g.add_argument("--font", help="font family for stamps (default Helvetica/Arial, falling back to the bundled serif)")
    g.add_argument("--font-size", type=float, default=10, help="size of header, footer, numbers and Bates text (default 10)")
    g.add_argument("--margin", default="24pt", help="distance from the page edge (default 24pt)")
    p.add_argument("--render", metavar="DIR", help="render the first stamped pages to PNGs in DIR, to check them")
    p.add_argument("--password")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()
    if not any((a.watermark, a.image, a.page_numbers, a.header, a.footer, a.bates)):
        raise UsageError("nothing to stamp: pass --watermark, --image, --page-numbers, --header, --footer or --bates")
    if a.opacity is not None and not 0 <= a.opacity <= 1:
        raise UsageError("--opacity must be between 0 and 1")
    a.margin = parse_length(a.margin)
    src = pdf_input(a.input)
    inputs = [src] + ([Path(a.image)] if a.image else [])
    out = output_path(a.out, inputs, a.force)
    reader = open_reader(src, a.password)
    from pypdf import PdfWriter, Transformation

    writer = new_writer(reader)
    count = len(writer.pages)
    numbers = parse_pages(a.pages, count)
    labels = page_labels(reader)
    title = a.title_text if a.title_text is not None else info_strings(reader).get("/Title", "").strip()
    uses_title = any("{title}" in (v or "") for v in (a.watermark, a.page_numbers, a.header, a.footer))
    if uses_title and not title:
        # No title: drop the placeholder with the separator next to it ("Review copy · {title}" → "Review copy").
        for key in ("watermark", "page_numbers", "header", "footer"):
            v = getattr(a, key)
            if v:
                setattr(a, key, re.sub(r"\s*[·•|:—–-]\s*\{title\}|\{title\}\s*[·•|:—–-]\s*|\{title\}", "", v).strip() or v.replace("{title}", ""))
    total = a.start + len(numbers) - 1
    targets = []
    for k, n in enumerate(numbers):
        geo = geometry_of(writer.pages[n - 1])
        bates = f"{a.bates}{a.bates_start + k:0{a.bates_digits}d}" if a.bates else ""
        ctx = {"n": str(a.start + k), "total": str(total), "page": str(n), "pages": str(count), "label": labels[n - 1] if n - 1 < len(labels) else str(n), "date": date.today().isoformat(), "file": src.name, "title": title, "bates": bates}
        targets.append({"page": n, "geo": geo, "width": geo.width, "height": geo.height, "ctx": ctx, "bates": bates})
    with tempfile.TemporaryDirectory(prefix="desk-stamp-") as tmp:
        overlay_pdf = build_overlay(a, targets, Path(tmp))
        from io import BytesIO

        from pypdf import PdfReader

        overlay = PdfReader(BytesIO(overlay_pdf))
        if len(overlay.pages) != len(targets):
            raise UsageError("the overlay has the wrong number of pages")
        under = bool(a.under and (a.watermark or a.image))
        for t, op in zip(targets, overlay.pages):
            page = writer.pages[t["page"] - 1]
            page.merge_transformed_page(op, Transformation(t["geo"].overlay_matrix()), over=not under, expand=False)
    size = save_writer(writer, out)
    info: dict[str, Any] = {"output": str(out), "pages": count, "stamped": len(numbers), "bytes": size}
    done = []
    if a.watermark:
        done.append(f"watermark “{a.watermark}”" + (" under the content" if a.under else ""))
    if a.image:
        done.append(f"image {Path(a.image).name}")
    if a.page_numbers:
        done.append(f"page numbers {a.start}-{total}")
    if a.header:
        done.append("header")
    if a.footer:
        done.append("footer")
    if a.bates:
        info["bates_first"] = targets[0]["bates"]
        info["bates_last"] = targets[-1]["bates"]
        info["bates_next_start"] = a.bates_start + len(targets)
        done.append(f"Bates {targets[0]['bates']}–{targets[-1]['bates']} (next start {a.bates_start + len(targets)})")
    info["applied"] = done
    if uses_title and not title:
        info["notes"] = ["the PDF has no Title, so {title} was left empty: pass --title \"…\", or set the Title first with pdf_meta.py set --title"]
    clashes = overlaps(pdfium_source(src, a.password), a.password, targets, a)
    if clashes:
        info["overlaps_existing_text"] = {k: ranges(v) for k, v in clashes.items()}
    if a.render:
        folder = output_dir(a.render)
        jobs = [{"pdf": str(out), "index": n - 1, "dpi": None, "max_edge": 1568, "out": str(folder / f"{out.stem}-page-{n:02d}.png")} for n in numbers[:4]]
        info["rendered"] = [r["path"] for r in render_pages(jobs)]
    if a.format == "json":
        emit(info, "json")
        return 0
    print(f"wrote {out}: stamped {len(numbers)} of {plural(count, 'page')} ({'; '.join(done)}), {human_size(size)}")
    for n in info.get("notes", []):
        print(f"note: {n}")
    for name, pages in info.get("overlaps_existing_text", {}).items():
        print(f"note: the {name} overlap text already on page(s) {pages} (the document's own numbers or footer?). Look at a render; move them with the --*-position, --*-align or --margin options.")
    if info.get("rendered"):
        announce(info["rendered"])
    return 0


if __name__ == "__main__":
    run_main(main)
