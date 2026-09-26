# Custom scripts with pypdf, pypdfium2, pdfplumber and Typst

When no script covers a task, write a short Python script and run it with the same `python3` (the skill's
environment has pypdf, pypdfium2, pdfplumber, Pillow and typst). Put the script in the workspace, not in the skill.
To reuse this skill's helpers, add its `scripts/` folder to the path (`SKILL_DIR` points to the skill during
`skill_run`):

```python
import os, sys; sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
from _pdfkit import geometry_of, open_reader, open_pdfium, save_writer, parse_pages
```

Always write to a new file, then render it (`pdf_render.py`) and look at the result.

## Annotations: highlight, note, link

```python
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText, Highlight, Link, Text
from pypdf.generic import ArrayObject, FloatObject

w = PdfWriter(clone_from=PdfReader("in.pdf"))
h = w.pages[0].mediabox.height
# pypdf wants user-space rectangles (origin bottom-left): convert a view box (x0, top, x1, bottom)
x0, top, x1, bottom = 56, 70, 200, 86
rect = (x0, h - bottom, x1, h - top)
quad = ArrayObject([FloatObject(v) for v in (x0, h - top, x1, h - top, x0, h - bottom, x1, h - bottom)])
w.add_annotation(0, Highlight(rect=rect, quad_points=quad, highlight_color="ffff00"))
w.add_annotation(0, Text(rect=(520, h - 60, 540, h - 40), text="Check this figure", open=False))
w.add_annotation(0, FreeText(text="Reviewed", rect=(400, h - 120, 540, h - 95), font_size="12pt", border_color="ff0000"))
w.add_annotation(0, Link(rect=(56, h - 140, 200, h - 125), url="https://example.org"))
w.write("annotated.pdf")
```

For rotated or shifted pages, convert with `geometry_of(page).rect_from_view(x0, top, x1, bottom)` instead of
`h - y` (see `coordinates.md`). A FreeText annotation has no appearance of its own: viewers draw its text, but
pdfium renders (and some viewers) show only the border. For text that must look the same everywhere, draw it with
a Typst overlay (next recipe) or `pdf_stamp.py`.

## Draw or write anywhere on a page (Typst overlay)

Typst makes a transparent page of the same size; merge it over (or under) the original. This is what
`pdf_stamp.py` does; use it directly for custom layouts (a signature block, a table of numbers, a diagram).

```python
import typst
from pypdf import PdfReader, PdfWriter, Transformation
import os, sys; sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
from _pdfkit import geometry_of

w = PdfWriter(clone_from=PdfReader("in.pdf"))
page = w.pages[0]
g = geometry_of(page)                         # the page as displayed (rotation and crop box applied)
src = f"""
#set page(width: {g.width}pt, height: {g.height}pt, margin: 0pt, fill: none)
#place(top + left, dx: 360pt, dy: 700pt, block(stroke: 0.8pt + rgb("#1f4e79"), inset: 8pt, radius: 3pt)[
  #text(9pt)[Approved by *A. Lovelace*] \\ #text(8pt, fill: luma(90))[25 September 2026]
])
"""
overlay = PdfReader(__import__("io").BytesIO(typst.compile(src.encode()))).pages[0]
page.merge_transformed_page(overlay, Transformation(g.overlay_matrix()), over=True)
w.write("approved.pdf")
```

`dx`/`dy` are view coordinates, so a box from `pdf_text.py --search` or a `--grid` render can be used as is.

## A document laid out in Typst from data

For invoices, certificates, labels and other generated documents, pass the data to Typst and loop in the markup:

```python
import json, typst

data = {"client": "Acme Ltd", "items": [["Design", 12, 90.0], ["Build", 30, 75.5]]}
src = r"""
#let d = json(bytes(sys.inputs.data))
#set page(paper: "a4", margin: 2cm)
#set text(font: "Libertinus Serif", size: 11pt)
= Invoice for #d.client
#table(columns: (1fr, auto, auto), align: (left, right, right), stroke: 0.5pt,
  [*Item*], [*Hours*], [*Amount*],
  ..d.items.map(((name, hours, rate)) => ([#name], [#hours], [#calc.round(hours * rate, digits: 2)])).flatten())
"""
open("invoice.pdf", "wb").write(typst.compile(src.encode(), sys_inputs={"data": json.dumps(data)}, ignore_system_fonts=True))
```

Typst's bundled fonts: Libertinus Serif, New Computer Modern (and Math), DejaVu Sans Mono. Leave out
`ignore_system_fonts=True` to use installed fonts as well (the first run then scans them, which can take seconds).

## Characters with their boxes (pypdfium2)

```python
import pypdfium2 as pdfium

doc = pdfium.PdfDocument("in.pdf")
page = doc[0]
tp = page.get_textpage()
text = tp.get_text_range()
for i in range(min(tp.count_chars(), 20)):
    l, b, r, t = tp.get_charbox(i)            # user space, origin bottom-left
    print(repr(text[i]) if i < len(text) else "?", round(l, 1), round(b, 1))
searcher = tp.search("revenue", match_case=False)
while (hit := searcher.get_next()) is not None:
    start, count = hit
    print("match at char", start, [tp.get_rect(k) for k in range(tp.count_rects(start, count))])
tp.close(); page.close(); doc.close()
```

## Text and tables in one area (pdfplumber)

pdfplumber measures from the top-left of the media box (no `/Rotate`), in points.

```python
import pdfplumber

with pdfplumber.open("in.pdf") as pdf:
    page = pdf.pages[0]
    area = page.crop((50, 140, 300, 240))                  # x0, top, x1, bottom
    print(area.extract_text())
    for table in area.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"}):
        print(table)
    # Tables without rules: align on words, and tell pdfplumber where the columns are
    rows = page.extract_table({"vertical_strategy": "explicit", "explicit_vertical_lines": [56, 110, 150, 200],
                               "horizontal_strategy": "text"})
```

`page.to_image(resolution=100).debug_tablefinder().save("debug.png")` draws what the table finder sees; look at
it with view_image when a table comes out wrong.

## Render one page yourself

```python
import pypdfium2 as pdfium

doc = pdfium.PdfDocument("form.pdf")
doc.init_forms()                              # without this, form field values are not drawn
page = doc[0]
img = page.render(scale=150 / 72, may_draw_forms=True).to_pil()
img.save("page1.png")
page.close(); doc.close()
```

## Split scanned book spreads into single pages

```python
from copy import deepcopy
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

r = PdfReader("spreads.pdf")
w = PdfWriter()
for page in r.pages:
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    mid = (x0 + x1) / 2
    for left, right in ((x0, mid), (mid, x1)):
        half = w.add_page(deepcopy(page))
        half.mediabox = RectangleObject((left, y0, right, y1))
        half.cropbox = RectangleObject((left, y0, right, y1))
w.write("pages.pdf")
```

## Attach a file, custom page labels, document JavaScript check

```python
from pypdf import PdfReader, PdfWriter

w = PdfWriter(clone_from=PdfReader("in.pdf"))
w.add_attachment("data.csv", open("data.csv", "rb").read())
w.set_page_label(0, 1, style="/r")                           # pages 1-2: i, ii
w.set_page_label(2, len(w.pages) - 1, style="/D", start=1)   # then 1, 2, 3 …
w.write("with-extras.pdf")
```

`pdf_info.py` reports JavaScript, launch actions and other risky actions; never run them.

## Images and their effective resolution

```python
from pypdf import PdfReader
import os, sys; sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
from _content import image_placements

r = PdfReader("scan.pdf")
for n, page in enumerate(r.pages, 1):
    shown = image_placements(page, r)          # image object number → longest side as drawn, in points
    for img in page.images:
        ref = img.indirect_reference.idnum if img.indirect_reference else None
        side = shown.get(ref)
        dpi = max(img.image.size) / (side / 72) if side else None
        print(n, img.name, img.image.size, f"{dpi:.0f} dpi" if dpi else "")
```

## Where to look in the libraries

- pypdf: `PdfReader`, `PdfWriter(clone_from=…)`, `page.merge_transformed_page`, `writer.add_annotation`,
  `writer.encrypt`, `writer.set_page_label`, `page.images`, `reader.outline`, `writer.add_outline_item`.
- pypdfium2: `PdfDocument`, `page.render`, `page.get_textpage()` (`get_text_range`, `get_text_bounded`,
  `get_charbox`, `search`), `page.get_objects()`, `doc.get_toc()`, `doc.init_forms()`.
- pdfplumber: `page.chars`, `page.words` via `extract_words()`, `page.lines`, `page.rects`, `find_tables(settings)`,
  `crop`, `within_bbox`, `filter`.
- Typst: <https://typst.app/docs> (tables, `place`, `grid`, `image`, `json`, `csv`, `sys.inputs`).
