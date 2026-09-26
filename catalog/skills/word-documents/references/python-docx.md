# Custom edits with python-docx and lxml

The scripts cover most tasks. For anything else, write a short Python script and run it with this skill's
`python3` (python-docx 1.2, lxml, docxcompose, Pillow and pypdfium2 are installed). Always save to a new path, then
check the result with `docx_read.py` and `docx_render.py`.

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT

doc = Document("in.docx")

# Body blocks in order (paragraphs and tables interleaved), the same order docx_read --indexes numbers.
for block in doc.iter_inner_content():
    print(type(block).__name__, getattr(block, "text", "")[:60])

p = doc.add_paragraph("Intro ", style="Normal")
r = p.add_run("important")
r.bold = True
r.font.color.rgb = RGBColor(0xC4, 0x44, 0x1C)
p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
p.paragraph_format.space_after = Pt(6)

t = doc.add_table(rows=1, cols=3, style="Table Grid")
for cell, text in zip(t.rows[0].cells, ["Item", "Qty", "Price"]):
    cell.text = text

doc.add_picture("chart.png", width=Cm(15))

s = doc.sections[0]
s.orientation = WD_ORIENT.LANDSCAPE
s.page_width, s.page_height = s.page_height, s.page_width

doc.add_comment(p.runs, text="Please confirm this figure", author="Desk", initials="D")
doc.save("out.docx")
```

Note: `docx_read`'s block indexes also count paragraphs inside content controls (`w:sdt`), which
`iter_inner_content()` skips. When you mix the two, use this skill's helper `iter_blocks` (below).

## Reusing this skill's helpers

The scripts' modules can be imported (add the `scripts` folder to `sys.path`). They handle the hard parts.

```python
import sys; sys.path.insert(0, "scripts")
from _docx import load, iter_blocks, para_text, block_text, qn, new_el
from _runs import CharMap, replace_span, isolate, apply_format

doc = load("in.docx")                      # also .odt/.rtf/.doc (converted), keeps every part intact
blocks = doc.blocks()                      # the same indexes as docx_read --indexes
p = blocks[12]                             # an lxml w:p element
cm = CharMap(p)                            # the paragraph's visible text across runs, fields and revisions
i = cm.text.find("30 days")
replace_span(cm, i, i + len("30 days"), "45 days")        # keeps the first run's formatting
for run in isolate(p, 0, 5):               # split runs so that exactly chars 0-5 are separate runs
    apply_format(run, {"bold": True, "color": "C00000"}, doc.styles)
doc.save("out.docx", inputs=["in.docx"])   # refuses to overwrite the input
```

## The XML underneath

A .docx is a zip of XML parts; `word/document.xml` holds the body. The shapes that matter:

- `w:p` paragraph → `w:pPr` (style `w:pStyle`, numbering `w:numPr`, spacing, indents, `w:sectPr` at a section's
  end) and `w:r` runs → `w:rPr` (formatting) and `w:t` text, `w:tab`, `w:br`.
- One visible word is often several runs (spell-check marks, revision ids, formatting). Never match text run by
  run; use `CharMap` (above), or join the runs' text first.
- Fields: `w:fldChar begin` … `w:instrText` (the code, like `PAGE` or `TOC \o "1-3"`) … `w:fldChar separate` …
  cached result runs … `w:fldChar end`. Simple ones are `w:fldSimple`. Word recomputes them on open when
  `w:updateFields` is set in settings (the `update_fields` edit operation).
- Tracked changes: `w:ins` / `w:del` (with `w:delText`) wrap runs; `w:rPrChange` / `w:pPrChange` keep old
  formatting. Accept = unwrap `w:ins`, drop `w:del`.
- Lists: `w:numPr` (`w:ilvl`, `w:numId`) → `numbering.xml` `w:num` → `w:abstractNum` levels (format, text like
  `%1.%2.`, indents). The number itself is not in the text.
- Tables: `w:tbl` → `w:tr` → `w:tc`; merges are `w:gridSpan` (across) and `w:vMerge` (down).
- Pictures: `w:drawing` → `wp:inline` (in the text) or `wp:anchor` (floating) → `a:blip r:embed` → an image part.
- Headers/footers: `w:headerReference` in each `w:sectPr` → `header1.xml`; a section without one inherits the
  previous section's.

## Limits of python-docx

- It does not render, paginate or convert: use `docx_render.py` and `docx_convert.py`.
- It has no API for tracked changes, fields, footnotes, content controls or text boxes; edit the XML (above) or use
  `docx_edit.py`, which handles them.
- `cell.text = …` and `paragraph.text = …` drop the formatting of the runs they replace; `replace_span` keeps it.
