# pypdf and pdfplumber recipes beyond the scripts

Run these with this skill's `python3`, which has pypdf, pdfplumber and reportlab installed.

```python
# Stamp every page with a watermark page (first page of stamp.pdf)
from pypdf import PdfReader, PdfWriter
stamp = PdfReader("stamp.pdf").pages[0]
w = PdfWriter(clone_from="in.pdf")
for page in w.pages:
    page.merge_page(stamp, over=True)
w.write("stamped.pdf")

# Images embedded in page 1
reader = PdfReader("in.pdf")
for img in reader.pages[0].images:
    open(img.name, "wb").write(img.data)

# Words with positions (e.g. to find where a label sits), then crop a region and read it
import pdfplumber
with pdfplumber.open("in.pdf") as pdf:
    page = pdf.pages[0]
    words = page.extract_words()                   # [{"text", "x0", "top", "x1", "bottom"}, ...]
    total = page.crop((300, 600, page.width, 700)).extract_text()

# Tune table detection when extract_tables misses lines
    tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "lines"})

# Add a bookmark outline to a merged file
w = PdfWriter(clone_from="merged.pdf")
w.add_outline_item("Appendix", 12)                 # 0-based page index
w.write("with-outline.pdf")

# Compress (lossless) content streams
w = PdfWriter(clone_from="big.pdf")
for page in w.pages:
    page.compress_content_streams()
w.write("smaller.pdf")
```

Limits worth knowing:
- There is no OCR. Scanned PDFs have no text layer (`pdf_info.py` reports `"text_layer": false`), so say so rather than guessing their content.
- Editing existing text in place is not reliable with these libraries. Regenerate the document (`pdf_create.py`) or overlay a stamp instead.
- Rendering pages to images needs poppler or a browser, which this skill does not include.
