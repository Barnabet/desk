# python-docx notes for custom edits

The scripts cover most tasks. For anything else, write a short Python script run with this skill's `python3`
(python-docx is installed in it).

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT

doc = Document("in.docx")              # or Document() for a blank document

# Paragraphs and runs: a paragraph is a list of runs sharing one style; each run has its own character formatting.
p = doc.add_paragraph("Intro ", style="Normal")
r = p.add_run("important")
r.bold = True
r.font.color.rgb = RGBColor(0xC4, 0x44, 0x1C)
p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
p.paragraph_format.space_after = Pt(6)

# Headings, page breaks
doc.add_heading("Results", level=1)
doc.add_page_break()

# Tables
t = doc.add_table(rows=1, cols=3, style="Table Grid")
for cell, text in zip(t.rows[0].cells, ["Item", "Qty", "Price"]):
    cell.text = text
row = t.add_row().cells
row[0].text = "Widget"

# Images (PNG/JPEG), sized by width
doc.add_picture("chart.png", width=Cm(15))

# Sections: orientation, margins, headers and footers
s = doc.sections[0]
s.orientation = WD_ORIENT.LANDSCAPE
s.page_width, s.page_height = s.page_height, s.page_width
s.left_margin = s.right_margin = Cm(2)
s.header.paragraphs[0].text = "Confidential"

# Styles: change the Normal font for the whole document
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)

# Comments (python-docx ≥ 1.2): attach to a run range
doc.add_comment(p.runs, text="Please confirm this figure", author="Desk", initials="D")

doc.save("out.docx")
```

Walking the body in order (paragraphs and tables interleaved): `for block in doc.iter_inner_content(): ...`.

Limits worth knowing:
- python-docx does not render or convert. For PDF output, write a PDF with the pdf-toolkit skill instead of converting.
- Tracked changes are not supported. To show edits, add comments, or produce a changelog next to the document.
- Fields (tables of contents, page numbers) are stored as field codes and update when the file is opened in Word.
