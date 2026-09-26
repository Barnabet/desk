# Coordinates, boxes and regions

Every script in this skill uses one coordinate system, so a box found by one script can be given to another.

## View coordinates

A box is `x0,top,x1,bottom`:

- in **points** (1/72 inch; A4 is 595 × 842, US Letter 612 × 792),
- measured from the **top-left corner of the page as a viewer shows it**: after the page's crop box and its
  `/Rotate` are applied,
- with y growing **downwards**, like image pixels.

| Script | Gives boxes | Takes boxes |
|---|---|---|
| `pdf_text.py --search`, `--words`, `--tables` | `box` of each match, word or table | |
| `pdf_form.py list` | each widget's `box` | |
| `pdf_extract.py images`, `links` | where each image or link sits | |
| `pdf_compare.py` | `regions` that changed | |
| `pdf_render.py` | | `--region x0,top,x1,bottom` |
| `pdf_redact.py` | `--dry-run`: each match's boxes | `--box PAGES:x0,top,x1,bottom` |
| `pdf_pages.py crop` | | `--box x0,top,x1,bottom` |

Values that are all 1 or less are **fractions** of the page: `0,0,1,0.1` is the top tenth, `0.5,0,1,1` the
right half. Use fractions when pages have different sizes.

## From a render to points and back

`pdf_render.py` prints the scale it used: `point = pixel × 72 / dpi`. A render at the default vision size of an
A4 page is about 1109 × 1568 px, so 1 point is about 1.86 px. For a `--region` render, add the region's `x0`
and `top` to the converted pixel position.

To read coordinates straight off an image, render with a grid: lines every 50 points (or a tenth of the region),
labelled in points.

```bash
python3 scripts/pdf_render.py form.pdf --pages 1 --grid
python3 scripts/pdf_render.py form.pdf --pages 1 --region 0,500,300,700 --grid 10
```

## Rotated and shifted pages

PDF content lives in "user space": the origin is usually the bottom-left of the media box and y grows upwards.
The page's crop box can start anywhere (scanners and croppers often leave `[36 36 576 756]` or even negative
origins), and `/Rotate 90` turns the whole page clockwise when displayed. The scripts do this conversion for you;
you only need it for custom code.

With the crop box `(l, b, r, t)` and a point `(x, y)` in user space, the view coordinates are:

| /Rotate | view x | view y | view width × height |
|---|---|---|---|
| 0 | x − l | t − y | (r − l) × (t − b) |
| 90 | y − b | x − l | (t − b) × (r − l) |
| 180 | r − x | y − b | (r − l) × (t − b) |
| 270 | t − y | r − x | (t − b) × (r − l) |

In custom code, `_pdfkit.geometry_of(pypdf_page)` or `_pdfkit.pdfium_geometry(pdfium_page)` returns a
`Geometry` with `to_view(x, y)`, `from_view(vx, vy)`, `rect_to_view(...)`, `rect_from_view(...)` and
`overlay_matrix()` (the matrix that maps a page-sized overlay, drawn upright in view space, onto user space).

```python
import os, sys; sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
from _pdfkit import geometry_of, open_reader

page = open_reader("in.pdf").pages[0]
g = geometry_of(page)
print(g.width, g.height, g.rotation)            # as displayed
x0, y0, x1, y1 = g.rect_from_view(72, 72, 300, 120)   # a view box → user-space rectangle (for annotations)
```

pdfplumber reports `top`/`bottom` from the media box top and does not apply `/Rotate`; pypdfium2's
`get_charbox` and `get_rect` return user space. Convert with the table above when you mix libraries.

## Page numbers and labels

Scripts number pages from 1 in file order. Page labels (i, ii, 1, 2, A-1 …) are what a viewer shows;
`pdf_text.py` prints them next to the page number (`--- page 3 (1) ---`) and `pdf_meta.py labels` lists them. The
`--pages` option always takes page numbers, never labels.
