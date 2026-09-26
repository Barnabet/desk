---
name: word-documents
description: Create, read, edit, fill, compare, render and convert Word documents (.docx .docm .dotx .dotm, plus .odt .rtf .doc). Use for any Word or word-processing task. Build a styled report, letter or memo from Markdown or a JSON spec, with a table of contents, page numbers, headers and footers, or the user's own template. Read text with real list numbers, tables, footnotes, comments and tracked changes; search and navigate long documents (1,000+ pages) by section. Replace text across runs while keeping formatting, insert Markdown, delete or move paragraphs, edit tables, images, headers, page setup and properties, as tracked changes when asked. Accept or reject revisions, add comments, remove personal info. Fill {{placeholder}} templates with loops and conditions (mail merge). Compare two versions into a change report and a redline .docx. Render pages to PNG and look at them. Convert to PDF, HTML, Markdown, text, ODT, RTF or EPUB and back.
license: MIT
---

# Word documents

Scripts in `scripts/` use python-docx (MIT), pandoc, Typst and pypdfium2. Desk sets up their Python environment,
so run them with `python3`. Every script has `--help`, prints Markdown by default and JSON with `--format json`,
and never changes its input: outputs go to a new path, and an existing file is only replaced with `--force`.
Legacy .doc files and exact rendering need LibreOffice; everything else works without it.

Work in a loop: **look** at the document, **act** on it, then **check** the result by rendering it and looking.

## Look

```bash
python3 scripts/docx_info.py contract.docx               # pages, words, sections, styles, comments, revisions, fields…
python3 scripts/docx_read.py contract.docx               # Markdown: headings, real list labels, tables, notes, comments
python3 scripts/docx_read.py contract.docx --outline     # headings only, with block indexes
python3 scripts/docx_read.py contract.docx --find "Termination"     # every match: block address, section, context
python3 scripts/docx_render.py contract.docx --pages 1-3 --sheet    # PNG per page + a contact sheet
```

Then look at the PNGs with view_image: layout, letterheads, tables and pictures show there, not in text.

- `docx_read` shows tracked changes as CriticMarkup (`{++added++}`, `{--removed--}`), comments as
  `{==text==}{>>Author: comment<<}`, footnotes as `[^1]`, text boxes, equations, headers and footers. Use
  `--changes accept|reject` and `--comments end|none` to change that, `--extract-media DIR` to save the pictures.
- `--indexes` (or `--format json`) gives each block's index: edits address blocks by these numbers.
- Tables come out as Markdown; `--blocks 57 --format csv` gives a table's data as CSV (merged cells repeated,
  lines inside a cell kept as lines). In JSON, a cell's `text` is plain text.
- `docx_info --exact` counts pages by rendering. Without it the page count is Word's last saved count, or an estimate.
- Rendering uses LibreOffice when installed (exact) and otherwise a built-in Typst renderer (close: fonts, line and
  page breaks can differ). The output names the engine; say so when it matters.

### Big files

Long documents (about 20 pages and more) never print whole. Work map → find → drill down:

```bash
python3 scripts/docx_read.py thesis.docx                          # the map: sections with block ranges and sizes
python3 scripts/docx_read.py thesis.docx --grep "Table \d+\.\d"    # matches: [block] (section path) …context…
python3 scripts/docx_read.py thesis.docx --section "4.2 Results"  # one section; or --blocks 5190-5240
python3 scripts/docx_render.py thesis.docx --block 5201           # the page where that block is; or --find "Figure 7"
```

- Block numbers are stable addresses in a given file: docx_read, docx_render and docx_edit all use them (an edited
  copy has its own numbering; read it again before addressing it).
- A document without heading styles (a contract whose "ARTICLE 12" lines are only bold) is mapped by its
  heading-like lines, and `--section "Article 12"` works on them. If the map shows even slices instead, navigate
  with `--grep '^ARTICLE'` (whatever its titles share).
- Output is capped by `--max-chars` (default 60,000). A cut output says what it left out and ends with the exact
  command for the next part; run it rather than raising the cap.
- The parsed document and the page layout are cached by the file's content (`--no-cache` skips it), so the second
  call on a 1,000-page file takes a fraction of a second; rendering more pages reuses the layout. Without
  `--pages`, docx_render draws at most the first 20. Timings: `references/performance.md`.

## Act

### Create a document

Write Markdown, then build. Headings, lists, tables, images, links, footnotes, math and code all map to real Word
styles; `\pagebreak` starts a page and `[[toc]]` places a table of contents (see `references/markdown.md`).
Money is safe: `Revenue ($M)` and `$5 to $10` stay text, `$E=mc^2$` is an equation; write `\$` when in doubt,
and `\|` for a literal bar inside a table cell.

```bash
python3 scripts/docx_create.py report.md out/report.docx --title "Q3 Report" --author "Finance" --toc --page-numbers
python3 scripts/docx_create.py letter.md out/letter.docx --template letterhead.dotx      # the user's own styles
python3 scripts/docx_create.py --spec invoice.json out/invoice.docx                   # exact control, JSON
```

- Presets: `--style report` (default), `classic` or `modern`; tune with `--font`, `--font-size`, `--color`,
  `--size A4|Letter`, `--landscape`, `--margins 2cm`, `--lang fr-FR`.
- `--header` / `--footer` take `{page}`, `{pages}`, `{date}`, `{title}` as live fields and `"left|center|right"`
  for three positions: `--footer "Acme||Page {page} of {pages}"`.
- `--toc` writes the real page numbers: it lays the result out once (LibreOffice, else the built-in renderer) and
  reads where each heading landed (`--no-toc-pages` leaves them to Word).
- Tables span the text width with columns sized from their content.
- A JSON spec (`references/json-spec.md`) gives exact runs, colours, table widths, merged cells, shading (text on
  a dark fill turns white), list formats and landscape sections (their headers and footers follow the new width),
  and can mix in Markdown blocks.
- When the user has a house template (.dotx or a previous document), use `--template`: it keeps their styles,
  page setup, headers and footers.

### Edit a document

Read with `--indexes` first, then write the operations as a JSON list and apply them to a new file:

```bash
cat > edits.json <<'JSON'
[
  {"op": "replace", "find": "ACME Ltd", "replace": "Acme Limited"},
  {"op": "insert_after", "index": 12, "markdown": "The parties review the terms **annually**."},
  {"op": "delete", "range": "30-32"},
  {"op": "table_add_row", "table": 0, "cells": ["Support", "12", "1,200.00"]},
  {"op": "set_footer", "text": "Acme Limited||Page {page} of {pages}"}
]
JSON
python3 scripts/docx_edit.py contract.docx contract-v2.docx --ops edits.json
python3 scripts/docx_edit.py contract.docx contract-v2.docx --ops edits.json --track --author "Desk"
python3 scripts/docx_edit.py draft.docx final.docx --ops '[{"op":"accept_changes"},{"op":"remove_comments"}]'
```

- Every index refers to the **original** document, whatever earlier operations did, so read once and write all
  operations against those numbers.
- `replace` finds text across runs (Word splits words into pieces), keeps the formatting of the match's first
  character, and covers the body, tables, text boxes, headers, footers and notes. `regex` with `$1` groups works.
- Each operation reports its count. One that matches nothing fails the whole edit (nothing is written) unless it
  has `"optional": true`. Check the counts against what you expected.
- `--track` records edits as real tracked changes the user can accept or reject in Word; each operation in the
  report says `(tracked)` or `(not tracked)`. Page setup, properties, column and merge operations cannot be
  revisions: `reject_changes` does not undo them, so tell the user. `--dry-run` shows counts without writing.
- Inserted Markdown takes the neighbouring paragraph's look (style, font, size) when that is body text; headings,
  lists and tables use the document's own styles. `"text"` copies the anchor paragraph exactly.
- All operations and their fields (formatting, styles, tables, images, comments, revisions, headers, page setup,
  properties, appending documents, removing personal info): `references/edit-ops.md`.

### Fill a template

```bash
python3 scripts/docx_template.py invoice-template.docx --fields                 # what it expects
python3 scripts/docx_template.py invoice-template.docx out/invoice-42.docx --data invoice.json
```

`{{client.name}}`, `{{total | currency:"€"}}`, `{{due | date:long}}`, `{{#each items}}` loops (paragraphs, table
rows or inline), `{{#if paid}} … {{else}} … {{/if}}`, Word merge fields and content controls. Placeholders keep
their formatting even when Word split them across runs. Unfilled ones are reported: fix the data. A template can
be written in Markdown with `docx_create.py`: `{{…}}` tags stay exactly as typed, also in table rows. See
`references/templates.md`.

### Compare versions

```bash
python3 scripts/docx_compare.py v1.docx v2.docx                                   # change report
python3 scripts/docx_compare.py v1.docx v2.docx --redline v1-v2-redline.docx --author "Legal"
```

The report lists added, deleted and changed paragraphs, and changed tables row by row (a row keeps its label, so
`Total` 4,668.50 → 9,900.00 is one changed row), with word-level edits, style changes and header/footer changes,
each with its block numbers (`[old → new]`). A long report ends with the exact command for the next part
(`--offset`). The redline is the new version with the differences as tracked changes: accepting them all gives
v2's text, rejecting them all v1's (check with `docx_compare.py v2.docx accepted.docx`). Pictures inside
deleted text are not carried into the redline.

### Convert

```bash
python3 scripts/docx_convert.py report.docx report.pdf
python3 scripts/docx_convert.py legacy.doc legacy.docx                  # needs LibreOffice
python3 scripts/docx_convert.py *.docx --to pdf --out-dir pdf/          # batch, in parallel
```

The output format follows the extension: .pdf, .html, .md, .txt, .odt, .rtf, .epub, .doc, or .docx from .md, .html,
.odt, .rtf and .doc. PDF uses LibreOffice when installed, otherwise the built-in renderer (it says which). HTML is
clean semantic HTML (mammoth; `--media-dir` saves the pictures). Markdown and text come from this skill's reader.
.odt and .rtf use LibreOffice or pandoc; .doc needs LibreOffice. To .docx, `--template house.dotx` applies the
user's styles. Every script also reads .odt, .rtf and .doc directly by converting a temporary copy first.

## Check

1. Render what you made or changed and look at it: `python3 scripts/docx_render.py out.docx --sheet`, then
   view_image the sheet and the pages that matter. Look for overflowing tables, orphaned headings, wrong fonts,
   missing pictures and empty placeholders.
2. Read it back (`docx_read.py`) and compare it with what was asked; for edits, compare the counts, or run
   `docx_compare.py original.docx edited.docx` to see every change.
3. What a render cannot show: comments (read them with `docx_read`), and the page numbers stored in the file. A
   render recomputes TOC and page fields from its own layout, so to check the TOC as saved, read its blocks
   (`docx_read.py out.docx --blocks 5-20`).
4. Fix and repeat. Then tell the user where the file is, what changed, and anything you could not do.

## Rules

- Never modify the user's file. Write outputs in your workspace, or where the user asked, and say where they are.
- Keep the user's formatting: prefer `docx_edit` operations and `--template` over rebuilding a document from text.
- Use tracked changes (`--track`, `--redline`) when the document belongs to someone who will review your edits.
- Before sharing a document outside, consider `remove_personal_info` and accepting or rejecting open revisions and
  comments; ask the user which they want.
- Say which renderer produced a preview or PDF when LibreOffice is not installed.
- Report what could not be done instead of guessing: macros, embedded OLE objects, SmartArt and charts are kept
  but not edited; editing restrictions and passwords are left as they are.

## Limits

- Exact page layout (page count, where lines break) needs LibreOffice. The built-in renderer is close for ordinary
  documents; complex floating layouts, WordArt, EMF/WMF pictures (grey boxes) and some fonts differ.
- Legacy .doc, .wpd, .wps and .pages need LibreOffice. Without it, ask the user for a .docx or PDF copy.
- Without LibreOffice, .odt and .rtf go through pandoc: text, lists, tables, links, pictures and basic formatting,
  not layout (columns, text boxes, exact spacing); writing .rtf keeps the title block, header and footer but not
  footnotes. Say so when fidelity matters.
- Password-protected (encrypted) files cannot be opened; ask for an unprotected copy. Damaged packages fail with
  one clear message; a package missing a part (a picture) is read without it, and with LibreOffice installed a
  broken one is first repaired (the scripts say so). Zip bombs, and XML with a DTD (which Word refuses too), are
  refused before anything is parsed.
- ISO Strict .docx files are read as ordinary (Transitional) Word files; saved copies are Transitional.
- Markdown, HTML, reStructuredText, LaTeX and Org inputs take pictures and includes only from their own folder,
  the current folder and `--resource-path`, so a stranger's document cannot put the user's files into the .docx:
  anything else (absolute paths, paths leading outside, `file:` URLs) is left out with a warning. Remote pictures
  are downloaded with limits (30 s, 20 MB each; `DESK_OFFLINE=1` skips them) and become links otherwise.
- Charts and SmartArt are read (data and text) but not edited. Macros (.docm) are kept, never run.
- Tables of contents, page numbers and cross-references are Word fields: they are filled where possible and Word
  refreshes them on open (`update_fields`).

## Beyond the scripts

For anything the scripts do not cover, write a small Python script with python-docx and lxml, or import this skill's
helpers (`references/python-docx.md`), and run it with the same `python3`.

Neighbouring skills (use them when installed): `pdf-toolkit` for PDFs (forms, merging, stamping, redaction),
`spreadsheets` for .xlsx, `presentations` for .pptx, `markup-ebooks` for EPUB and HTML books, `images` for editing
pictures before inserting them, `file-inspector` when a file's type is unclear.

References: `references/markdown.md` (Markdown in and out), `references/json-spec.md` (docx_create specs),
`references/edit-ops.md` (every edit operation), `references/templates.md` (template syntax),
`references/python-docx.md` (custom scripts and the XML underneath), `references/performance.md` (timings on
a 1,000-page document).
