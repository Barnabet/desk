# Writing your own script (python-pptx recipes)

When the scripts do not cover a task, write a short script and run it with the same `python3`: the skill's runtime
has python-pptx 1.0.2, lxml, Pillow, typst and pypdfium2. Save to a new file and never overwrite the input. Then
render and lint the result like any other deck.

The skill's helper modules can be imported when you add `scripts/` to `sys.path`:
- `_deck.open_deck` opens .potx/.ppsx/.pptm, and .ppt/.odp through LibreOffice.
- `_deck.save_deck` writes the content type matching the extension.
- `_ooxml.parse_length` parses lengths like "4cm".

```python
import sys
sys.path.insert(0, "scripts")
from _deck import open_deck, save_deck
from pathlib import Path

prs = open_deck("deck.pptx").prs
# ... changes ...
save_deck(prs, Path("out/deck-edited.pptx"))
```

## Basics

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE

prs = Presentation("deck.pptx")
for i, slide in enumerate(prs.slides, 1):
    for shape in slide.shapes:                     # z-order; groups have .shapes
        print(i, shape.shape_id, shape.name, shape.shape_type, shape.left, shape.top, shape.width, shape.height)
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                print("  ", p.level, "".join(r.text for r in p.runs))
```

Units are EMU: 914400 per inch and 12700 per point. Use `Inches()` and `Pt()`.

## Text with formatting

```python
tb = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(1.5))
tf = tb.text_frame
tf.word_wrap = True
tf.auto_size = MSO_AUTO_SIZE.NONE
p = tf.paragraphs[0]
r = p.add_run(); r.text = "Revenue "; r.font.size = Pt(24)
r = p.add_run(); r.text = "+38%"; r.font.bold = True
r.font.color.theme_color = MSO_THEME_COLOR.ACCENT_1       # follows the theme
p.alignment = PP_ALIGN.LEFT
r.hyperlink.address = "https://example.com"
```

## Placeholders and layouts

```python
layout = next(l for l in prs.slide_layouts if l.name == "Title and Content")
s = prs.slides.add_slide(layout)
s.shapes.title.text = "Agenda"
body = s.placeholders[1]                             # idx 1 is usually the body
body.text_frame.text = "First point"
p = body.text_frame.add_paragraph(); p.text = "Detail"; p.level = 1
```

## Pictures, crop

```python
pic = slide.shapes.add_picture("photo.jpg", Inches(0), Inches(0), width=Inches(6))
pic.crop_left = 0.1; pic.crop_right = 0.1          # fractions of the image
```

## Tables

```python
gf = slide.shapes.add_table(4, 3, Inches(1), Inches(2), Inches(8), Inches(2))
t = gf.table
t.columns[0].width = Inches(3)
cell = t.cell(0, 0); cell.text = "Region"
cell.fill.solid(); cell.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_1
t.cell(1, 0).merge(t.cell(1, 1))
```

## Charts

```python
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
cd = CategoryChartData(); cd.categories = ["Q1", "Q2"]; cd.add_series("2026", (1.8, 2.0))
gf = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(8), Inches(4), cd)
chart = gf.chart
chart.has_legend = True; chart.legend.position = XL_LEGEND_POSITION.BOTTOM; chart.legend.include_in_layout = False
chart.plots[0].series[0].format.fill.solid()
chart.plots[0].series[0].format.fill.fore_color.rgb = RGBColor(0x25, 0x63, 0xEB)
chart.replace_data(cd)                               # update data later, keeps formatting
```

## Speaker notes, hidden slides, order

```python
slide.notes_slide.notes_text_frame.text = "Say this."
slide._element.set("show", "0")                      # hide
lst = prs.slides._sldIdLst                           # reorder: move the 3rd slide first
el = lst[2]; lst.remove(el); lst.insert(0, el)
```

## Deleting a slide

```python
lst = prs.slides._sldIdLst
el = lst[4]; rid = el.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
lst.remove(el); prs.part.drop_rel(rid)
```

## Raw XML when the API stops

Every python-pptx object has `._element` (lxml). Examples:

- **Transition:** add `<p:transition><p:fade/></p:transition>` after `p:clrMapOvr` in `slide._element`.
- **Line spacing:** use `p.line_spacing = 0.9`, or `a:lnSpc/a:spcPct` in the paragraph's `a:pPr`.
- **Theme:** the theme part is `prs.slide_master.part.part_related_by(RT.THEME)`. Its XML is `.blob`. Edit it with
  lxml and assign the result back to `._blob` (see `_build.themed_presentation`).

## Merging decks

python-pptx cannot import slides from another file directly. To do it by hand:
1. Add a slide with a matching layout.
2. Deep-copy the source slide's `spTree` children.
3. Relate the new slide to the same image parts, and clone chart parts.

`pptx_edit.duplicate_slide` shows how to clone parts and remap relationship ids within one deck. For
cross-deck merges, rebuilding the slides with `pptx_create` is usually cleaner.

## Rendering what you made

```bash
python3 scripts/pptx_render.py out/deck-edited.pptx --out-dir renders --sheet --force   # then view_image
python3 scripts/pptx_lint.py out/deck-edited.pptx
```
