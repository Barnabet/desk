---
name: pdf-toolkit
description: Read, search, render, create, edit and check PDF files (.pdf). Use for any PDF task. Extract text fast (page markers, layout mode, words with boxes, tables as Markdown or CSV, regex search that returns page and box), get a section map of a long PDF, render pages or a zoomed region to PNG and look at them. Merge, split, extract, delete, rotate, reorder, insert, crop, n-up, scale and interleave pages. Fill, list and flatten forms. Create PDFs from Markdown, HTML, DOCX or Typst with report, memo and letter templates. Add watermarks, page numbers, headers, footers, logos and Bates numbers. Redact text, patterns (emails, phones, SSNs, cards, IBANs) or areas for real, with verification. Compress and shrink images, extract images, attachments, fonts and links, edit metadata, outline (bookmarks), page labels and passwords (AES-256), and compare two versions (text diff plus visual diff images). Works on scanned PDFs by rendering them; no OCR.
license: MIT
---

# PDF toolkit

Scripts in `scripts/` use pypdfium2 (fast text and rendering), pypdf (page and document edits), pdfplumber
(layout text and tables) and the bundled pandoc and Typst (creating PDFs). Desk sets up their Python environment,
so run them with `python3`. Every script has `--help` with examples, prints Markdown by default and JSON with
`--format json`, and never changes its input: outputs go to a new path, and an existing file is only replaced
with `--force`. Errors go to stderr with exit code 1 (2 for a wrong command line).

Work in a loop: **look** at the PDF, **act** on it, then **check** the result by rendering it and looking.

## Look

```bash
python3 scripts/pdf_info.py report.pdf                  # pages, sizes, fonts, outline, forms, encryption, scanned pages
python3 scripts/pdf_text.py report.pdf                  # the text with "--- page N ---" markers (a map when long)
python3 scripts/pdf_text.py report.pdf --pages 12-20    # a range
python3 scripts/pdf_text.py report.pdf --search "termination|notice period" -i   # page, box and context of each match
python3 scripts/pdf_text.py invoice.pdf --tables        # tables (CSV instead of Markdown when they are long)
python3 scripts/pdf_render.py report.pdf --pages 1-3    # PNGs sized for vision
python3 scripts/pdf_render.py report.pdf --pages 2 --region 300,400,560,560   # zoom on a part of page 2
```

Then look at the PNGs with view_image. Layout, charts, stamps, signatures, handwriting and scanned pages only show
there: text extraction cannot see them.

- **Search** (`--search`) is a regular expression over each page's text as it reads: a space also matches a line
  break, a word hyphenated at a line end matches whole ("termination"), and a match that runs over a page break is
  found too (shown as `27 (→28)`). `--literal` for plain text, `-i` to ignore case.
- **Scanned pages** have no text layer. `pdf_info.py` lists them, and `pdf_text.py` says so instead of printing
  nothing (a search tells you how many searched pages have no text). There is no OCR: render them and read them
  with view_image, zooming with `--region` on small print.
- **Boxes and regions** are `x0,top,x1,bottom` in points (1/72 inch) from the top-left of the page as displayed,
  the same in every script: a box from `--search` or `--words` can be passed to `pdf_render.py --region` and
  `pdf_redact.py --box`. On a render, point = pixel × 72 / dpi; `--grid` overlays labelled coordinates.
  Values of 1 or less are fractions of the page (`0,0,1,0.1` is the top tenth). See `references/coordinates.md`.
- `--layout` keeps columns and alignment (slower); `--words` gives every word with its box (`--region` keeps the
  words of one area).
- Tables: the default finds ruled tables and tables with shaded rows (every row, shaded or not).
  `--table-strategy text` (or `mixed`) is for tables with no lines or shading at all; use it with `--pages` on the
  table's page and check the cells, since it guesses columns from gaps. `--merge-tables` joins a table that
  continues on the next page; `--out DIR` writes one CSV per table.
- Encrypted files need `--password`; `pdf_info.py` shows the permissions.
- Damaged files: when pdfium or pypdf cannot open a file (a broken page tree too), the scripts work on a copy the
  other one repaired and say so on stderr. When only pdfminer can read it, `pdf_text.py` still gives the text,
  words and search.

## Big files

Above 100 pages, or when the text would not fit the output budget, `pdf_text.py` prints a **map** instead of the
text: the outline sections (or blocks of pages) with page ranges and sizes, and the pages without text. Work in
three steps:

1. **Map**: `python3 scripts/pdf_text.py book.pdf` (or `--map`), and `pdf_info.py` for fonts, forms and scans.
2. **Find**: `python3 scripts/pdf_text.py book.pdf --search "warranty|liability" -i` gives page, box and context.
3. **Drill down**: `--pages 212-230` for the text, `--tables --pages 212`, `pdf_render.py --pages 214`.
   A long outline reads level by level: `pdf_meta.py outline book.pdf --depth 1`.

Every output stops at `--max-chars` (default 60 000) on a page boundary, says what it left out, and ends with the
exact command that reads on (`Next: python3 scripts/pdf_text.py … --pages 41-230`), which covers the rest of what
you asked for. Text, words, tables, `pdf_info.py` and renders are cached by file content, so the second call on a
big file is fast; `--no-cache` recomputes. Files opened with `--password` are never cached. Timings:
`references/performance.md`.

## Act

### Pages

```bash
python3 scripts/pdf_pages.py merge all.pdf cover.pdf report.pdf appendix.pdf:1-4   # one bookmark per file
python3 scripts/pdf_pages.py split book.pdf chapters/ --by-outline                  # or --every 10, --at 5,9, --max-size 9MB
python3 scripts/pdf_pages.py extract in.pdf out.pdf --pages 2-5
python3 scripts/pdf_pages.py delete in.pdf out.pdf --pages 1,last
python3 scripts/pdf_pages.py rotate in.pdf out.pdf --degrees 90 --pages 3
python3 scripts/pdf_pages.py reorder in.pdf out.pdf --order "1,5-8,2-4,9-"
python3 scripts/pdf_pages.py insert in.pdf out.pdf --after 3 --from extra.pdf --from-pages 1-2   # or --blank 1
python3 scripts/pdf_pages.py crop slides.pdf trimmed.pdf --auto --padding 18       # or --margins 1cm, --box …
python3 scripts/pdf_pages.py nup handout.pdf handout-4up.pdf --n 4 --border
python3 scripts/pdf_pages.py scale mixed.pdf a4.pdf --paper A4
python3 scripts/pdf_pages.py interleave fronts.pdf backs.pdf book.pdf --reverse-even
```

Ranges are `1-3,7,10-`, `last` and `last-2`. Links, bookmarks and form fields survive extract, delete, rotate,
reorder and merge. Cropping only hides content (it stays in the file): use `pdf_redact.py` to remove it.

### Forms

```bash
python3 scripts/pdf_form.py list form.pdf                     # names, types, options, current values, page and box
python3 scripts/pdf_form.py fill form.pdf filled.pdf --values '{"name": "Ada Lovelace", "agree": true, "plan": "Pro"}' --render checks/
python3 scripts/pdf_form.py fill form.pdf final.pdf --values values.json --flatten
python3 scripts/pdf_form.py flatten filled.pdf final.pdf
```

Values are checked before anything is written: an unknown field name gets the closest match, a bad choice lists
the valid options. Checkboxes take `true`/`false`, combo boxes one option, multi-select lists a list. A radio
group takes one option, by its export value or by the words printed next to its button: `list` shows both
(`1 (Female), 2 (Male)`). Text that would not fit its box is drawn smaller; `--font-size 9` sets the size. The
fill reports each value read back. A form that also carries an XFA version loses it when filled, so every viewer
shows the new values; XFA-only forms (some government forms) cannot be filled. A file whose widgets lack a form
dictionary gets one.

### Create a PDF

Write Markdown (or use HTML, reStructuredText, LaTeX, EPUB, a notebook, an Office file or a `.typ` file):

```bash
python3 scripts/pdf_create.py report.md --out report.pdf --toc --number-sections
python3 scripts/pdf_create.py memo.md --out memo.pdf --template memo --to "All staff" --from-name "Operations"
python3 scripts/pdf_create.py letter.md --out letter.pdf --template letter --page-size letter
python3 scripts/pdf_create.py contract.docx --out contract.pdf                   # LibreOffice if installed
python3 scripts/pdf_create.py poster.typ --out poster.pdf                        # your own Typst
python3 scripts/pdf_create.py report.md --out report.pdf --typ-out src/          # keep the Typst source to adjust
```

- Templates: `report` (title page, optional contents, running header, page numbers; the default when the front
  matter has a title), `memo`, `letter` and `plain`. YAML front matter sets title, subtitle, author, date,
  abstract, and memo and letter fields; flags override it.
- Tables, images, footnotes, math, code with highlighting, links and citations (`--bibliography refs.bib`) work.
  `--font`, `--font-size`, `--margin`, `--page-size`, `--accent`, `--header`/`--footer` (`{page}`, `{pages}`,
  `{title}`) and `--lang` tune the look. Fonts are embedded; the default families are bundled, so the output is the
  same on every machine. Details: `references/create.md`.
- Office files (.docx, .odt, .rtf, .doc, .pptx, .xlsx …) keep their own layout when LibreOffice is installed.
  Without it, .docx, .odt and .rtf go through pandoc and a template (content, not layout; the output says so), and
  decks and workbooks need the `presentations` or `spreadsheets` skill. `--engine pandoc` forces the template.

### Stamp

```bash
python3 scripts/pdf_stamp.py in.pdf out.pdf --watermark DRAFT --opacity 0.12
python3 scripts/pdf_stamp.py in.pdf out.pdf --page-numbers "Page {n} of {total}" --pages 2- --start 1
python3 scripts/pdf_stamp.py in.pdf out.pdf --header "Acme Corp · {title}" --footer "{date}" --footer-align right
python3 scripts/pdf_stamp.py in.pdf out.pdf --bates ACME --bates-start 1201     # reports the next start number
python3 scripts/pdf_stamp.py in.pdf out.pdf --image logo.png --image-width 90pt --position top-right
```

`{title}` is the document's Title (`--title` sets it; without one it is left out, with a note). Stamps follow each
page's rotation and crop box. `--under` puts a watermark behind the content. The output notes pages where a
header, footer, page number or Bates number lands on text already there (often the document's own page numbers):
render one and move the stamp with `--number-position`, `--footer-align` or `--margin`.

### Redact

```bash
python3 scripts/pdf_redact.py contract.pdf redacted.pdf --find "Jane Doe" --preset email --preset phone --dry-run --render preview/
python3 scripts/pdf_redact.py contract.pdf redacted.pdf --find "Jane Doe" --preset email --preset phone
python3 scripts/pdf_redact.py scan.pdf redacted.pdf --box "1:320,90,560,140"
```

Redaction really removes the text, the image pixels and the vector graphics under each box, then draws the box.
Targets are matched like `--search`: over line breaks, hyphenated line ends and page breaks. The script then
**verifies** the output on every searched page: no target may be extractable anymore, no character may remain
under a box, and no text outside the boxes may be lost; a redacted page that fails is rasterised and checked
again, and anything else stops the run with nothing written. Presets: email (also `{ann,bob}@example.org`), phone,
ssn, card (Luhn-checked), iban (checksum), ip, url, date. Also `--regex`, `--whole-word`, `--ignore-case`,
`--label REDACTED`, `--strip-metadata`, `--remove-attachments`. How it works and its limits:
`references/redaction.md`.

### Other edits

```bash
python3 scripts/pdf_optimize.py big.pdf small.pdf --preset ebook      # screen (96 dpi), ebook (150), print (300), lossless
python3 scripts/pdf_extract.py images report.pdf --out report-images/ # also: attachments, fonts [--extract DIR], links
python3 scripts/pdf_meta.py set in.pdf out.pdf --title "Annual report" --author "Finance"   # Info and XMP together
python3 scripts/pdf_meta.py outline in.pdf --format json > outline.json   # edit, then set-outline … --outline outline.json
python3 scripts/pdf_meta.py encrypt in.pdf locked.pdf --password open-me --allow print    # AES-256
python3 scripts/pdf_meta.py decrypt locked.pdf open.pdf --password open-me
python3 scripts/pdf_meta.py set-labels book.pdf out.pdf --labels "1:roman,9:decimal"
python3 scripts/pdf_compare.py v1.pdf v2.pdf                           # text diff per page + visual diff PNGs
```

`pdf_optimize.py` downsamples only images shown above the target resolution, never makes a file bigger, and says
what it changed. `pdf_compare.py` pairs pages even when pages were inserted or removed, and marks visual changes
in red on side-by-side images (at most `--max-images`, default 20; the output names the changed pages left
without one).

## Check

1. Render what you made or changed and look at it: `python3 scripts/pdf_render.py out.pdf --sheet` for an
   overview, then the pages that matter at full size (`--pages`, `--region` for detail). Look for text running
   off the page, overlapping stamps, missing images, wrong page order or rotation, and empty form fields.
2. Read it back: `pdf_text.py` (or `--search` for what must be there, or gone), `pdf_info.py` for page count,
   sizes, fonts and metadata, `pdf_form.py list` for field values.
3. For edits of an existing PDF, `pdf_compare.py original.pdf edited.pdf` shows every change, in text and pixels.
4. Fix and repeat. Then tell the user where the file is, what changed, and anything you could not do.

## Rules

- Never modify the user's file. Write outputs in your workspace, or where the user asked, and say where they are.
- Look before you conclude: a page with little or no text is probably a scan or a drawing. Render it.
- Use `pdf_redact.py` for anything sensitive. Drawing a black rectangle (a stamp, an annotation, a crop) leaves the
  text in the file. Preview with `--dry-run --render`, then check the verified result, a `--search` for the
  targets on the output (expect none), and the rendered pages.
- For contracts and other documents someone signed, do not change the content: stamp, extract or annotate a copy,
  and remember that any edit invalidates a digital signature (`pdf_info.py` lists signature fields).
- Say when something is only visible in a render (scans, charts, handwriting) and describe what you saw.

## Limits

- No OCR: scanned text is read by looking at renders. Vertical writing and right-to-left scripts come out in the
  order the PDF stores them, which can be visual order. Search and redaction follow that order too: a phrase that
  only reads across two columns, or inside an image, is not found. Look at the renders of what matters.
- Existing text cannot be edited in place. Recreate the document (`pdf_create.py`), or cover and stamp a copy.
- Redaction falls back to rasterising a page (text there is no longer selectable) for vertical fonts, Type3 fonts
  with rotated glyphs, JBIG2 images and content it cannot parse; the report says which pages and why.
- Attachments encrypted with their own crypt filter cannot be extracted.
- XFA forms, signing, linearisation (fast web view) and PDF/A conversion are not supported. Encrypted files open
  with their password; files with an owner password only open without one.
- pdf_create follows pandoc for Markdown and HTML: complex CSS layouts are not reproduced. An Office file keeps
  its layout only through LibreOffice. Zip-based inputs (.docx, .odt, .epub …) that would inflate to extreme
  sizes are refused as possible zip bombs.
- pdf_create takes pictures and includes (reStructuredText, Org, LaTeX) only from the input's own folder, so a
  stranger's document cannot put the user's files into the PDF: absolute paths, paths leading outside, `file:`
  URLs and links pointing out are left out with a warning. Remote pictures are downloaded by the skill (30 s in
  all, 20 MB each; `DESK_OFFLINE=1` skips them) and become links when they cannot be fetched.

## Beyond the scripts

For anything the scripts do not cover, write a short Python script with pypdf, pypdfium2, pdfplumber or Typst and
run it with the same `python3`: `references/recipes.md` has tested snippets (annotations, drawing on pages,
documents laid out in Typst from data, characters with boxes, tables in an area, splitting scanned book spreads,
attachments and page labels, image resolutions).

Neighbouring skills: `word-documents` for .docx (and a Word document's own PDF export), `images` for editing
pictures and image formats, `markup-ebooks` for Markdown, HTML and EPUB conversions, `spreadsheets` for tables that
need analysis after extraction, `file-inspector` for unknown files.

References: `references/coordinates.md` (boxes, regions, rotation), `references/create.md` (templates, front
matter, Typst), `references/redaction.md` (how redaction and verification work), `references/recipes.md` (custom
scripts), `references/performance.md` (timings on big files, caching).
