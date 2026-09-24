---
name: pdf-toolkit
description: Read, create and change PDF files. Use for any PDF task, such as extracting text and tables, getting page count and metadata, merging, splitting, extracting, deleting or rotating pages, filling form fields, encrypting or decrypting, or writing a new PDF report from Markdown.
license: MIT
---

# PDF toolkit

Scripts in `scripts/` use pypdf (BSD), pdfplumber (MIT) and reportlab (BSD). Desk sets up their Python environment, so
run them with `python3`. Every script has `--help` and prints JSON or text. Pages are 1-based, and ranges look like
`1-3,7,10-`.

## Look before acting

```bash
python3 scripts/pdf_info.py in.pdf        # pages, size, encryption, metadata, outline, form fields, text layer
```

If `text_layer` is false, the PDF is scanned. Its text needs OCR, which this skill cannot do, so tell the user.

## Read

```bash
python3 scripts/pdf_text.py in.pdf --pages 1-5            # text, with page markers
python3 scripts/pdf_text.py in.pdf --layout               # keep columns and alignment
python3 scripts/pdf_text.py in.pdf --tables               # tables as Markdown
python3 scripts/pdf_text.py in.pdf --tables --format json # [{page, text, tables}]
```

Quote what the document says and cite the page. Table extraction is heuristic, so check totals and headers against the text.

## Change pages

```bash
python3 scripts/pdf_pages.py merge out.pdf a.pdf b.pdf
python3 scripts/pdf_pages.py extract in.pdf out.pdf --pages 2-4
python3 scripts/pdf_pages.py delete in.pdf out.pdf --pages 1
python3 scripts/pdf_pages.py rotate in.pdf out.pdf --degrees 90 --pages 3
python3 scripts/pdf_pages.py split in.pdf parts/ --every 1
python3 scripts/pdf_pages.py encrypt in.pdf locked.pdf --password '…'   # decrypt works the same way
python3 scripts/pdf_pages.py metadata in.pdf out.pdf --title "…" --author "…"
```

## Forms

```bash
python3 scripts/pdf_form.py list form.pdf                               # names, types, current values, options
python3 scripts/pdf_form.py fill form.pdf filled.pdf --values values.json [--flatten]
```

Use field names exactly as listed. Checkboxes take one of their listed options (often `/Yes`) or `/Off`. Flatten only
when the user wants a final, non-editable copy.

## Create

```bash
python3 scripts/pdf_create.py out/report.pdf --input report.md --title "…" [--page-size letter] [--margin 2.5]
```

The Markdown-lite input supports headings, paragraphs, bullet and numbered lists, `>` quotes, code blocks, pipe
tables, `\pagebreak`, and inline **bold**, *italic* and `code`.

## Rules

- Never modify the user's file in place. Write a new file and say where it is.
- After a change, check the result with `pdf_info.py` or `pdf_text.py`: the page count, the fields filled, the text present.
- For stamping, images, word positions and outlines, see `references/recipes.md`.
