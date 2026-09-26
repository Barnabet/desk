# Performance on big PDFs

Measured on 25 September 2026 on an Apple M2 (8 cores, 8 GB), inside Desk's sandbox with `DESK_MAX_WORKERS=2`
(at most two helper processes), while another agent was working, so an idle machine with more workers is faster.
Each operation started with an empty cache ("cold"), then ran again ("cached"). Times include starting Python.

## A. A 1000-page Typst document (4.1 MB)

Chapters and sections in its outline, about 2 400 characters of text per page, a ruled table every tenth page.

| Operation | Cold | Cached |
|---|---|---|
| `pdf_info.py` summary | 0.73 s | 0.04 s |
| `pdf_text.py` default read (a map: chapters with page ranges and sizes) | 0.66 s | 0.08 s |
| `pdf_text.py --all --max-chars 0` (all 1000 pages, 2.4 M characters) | 0.56 s | 0.10 s |
| `pdf_text.py --pages 500-510` | 0.08 s | 0.08 s |
| `pdf_text.py --search` over 1000 pages (page, box, context; page breaks too) | 0.71 s | 0.24 s |
| `pdf_text.py --layout --pages 1-100` | 2.17 s | 0.08 s |
| `pdf_text.py --words --pages 1-100 --format json` (40 000 words) | 0.71 s | 0.37 s |
| `pdf_text.py --tables` over 1000 pages (100 tables) | 3.57 s | 0.08 s |
| `pdf_render.py --pages 1-20` (vision size) | 0.36 s | 0.13 s |
| `pdf_render.py --sheet` (30 contact sheets of 12 pages) | 1.98 s | 0.93 s |
| `pdf_pages.py split --every 100` | 0.33 s | |
| `pdf_pages.py nup --n 4` | 0.30 s | |
| `pdf_pages.py merge` (2 × 1000 pages) | 0.54 s | |
| `pdf_stamp.py --page-numbers` (1000 pages, with the overlap check) | 2.68 s | |
| `pdf_redact.py --find` (1 match; every page verified, raw scan) | 6.06 s | |
| `pdf_optimize.py --preset lossless` | 3.70 s | |
| `pdf_compare.py --text-only` (1000 vs 1000 pages) | 1.69 s | |
| `pdf_compare.py` text and visual diff, 100 changed pages | 4.94 s | |

## B. A 1000-page contract made with `pdf_create.py` (6.7 MB)

The report template (title page, 18 pages of contents, running header and "n / total" footer, justified and
hyphenated text), 230 annexes in the outline, 300 zebra-striped tables (785 rows in each 421-page copy), 1 800
e-mail addresses with hyphenated domains (some split by a line end or a page break) and 2 500 copies of a phrase.

| Operation | Cold | Cached |
|---|---|---|
| `pdf_text.py` default read (a map of 230 sections) | 1.47 s | 0.08 s |
| `pdf_text.py --search 'contact-\d+@\S+' --format json --max-chars 0` (1 800 matches with boxes) | 2.91 s | 1.81 s |
| `pdf_text.py --search PHRASE` (default budget: stops after about 400 matches with a Next command) | 1.92 s | 0.84 s |
| `pdf_text.py --tables` over every page (300 zebra tables, every row) | 13.18 s | 0.09 s |
| `pdf_text.py --words` (default budget, stops with a Next command) | 0.58 s | 0.08 s |
| `pdf_redact.py --preset email --find PHRASE` (4 300 targets on 950 pages, verified) | 41.97 s | |
| `pdf_pages.py split --by-outline` | 0.99 s | |

Every address and phrase was found and redacted, including the ones hyphenated at a line end or split by a page
break; the output is the size of the input (5.4 MB for a 421-page copy), since rewritten streams are compressed.

## Targets and self test

On a 200-page version of document A: all text well under 1 s (target < 3 s), 20 pages rendered in about 0.4 s
(target < 5 s). The self test checks both on a 220-page file, plus a cache hit at least 5 times faster than the
first call for tables and layout text, budgets that hold (`--max-chars` on text, words and search) and
continuation commands that read on to the end.

Real files: every script ran on 55 public sample PDFs (the py-pdf sample-files and pdf.js test corpora: forms,
XFA, Type3 and CID fonts, CMYK and CCITT images, AES-256 files, damaged cross-reference tables, up to 5.3 MB and
104 pages) with the cache off; no call took more than 20 s and none crashed.

## What is cached

Results are stored by file **content** (a fingerprint: the full SHA-256 up to 8 MB, otherwise size, date and
samples), so an edited file is read again and a copy reuses the entries. Entries live in Desk's file cache
(`DESK_FILE_CACHE`, default `desk-files` under `XDG_CACHE_HOME` or the temp folder), capped at
`DESK_FILE_CACHE_MB` (1024 MB), least recently used first out.

| Kind | Cached as |
|---|---|
| plain text | every page at once, the first time the whole file is read (map, `--all`, search) |
| layout text, words, tables | chunks of 25 pages, per table strategy |
| outline sections for the map | once per file |
| `pdf_info.py` | the whole report, per `--pages`/`--fonts` |
| page renders | one PNG per page and settings (dpi, size, region, grid, boxes, forms) |

`--no-cache` (on `pdf_text.py`, `pdf_render.py`, `pdf_info.py`) or `DESK_NO_CACHE=1` recomputes without
storing. Files opened with `--password` are never cached, so their decrypted text is not written to disk.

## Why it is fast

- Text, search and rendering use pdfium (C++) page by page; big files are split into batches that run in
  parallel processes.
- A budgeted read (`--max-chars`) computes 25 pages first, then 50, 100 and 200 at a time, and stops as soon as
  the budget is full, so the first answer on a huge file comes quickly.
- Search first filters pages with the cached plain text (C-speed regular expressions over normalised copies of
  each page) and computes character boxes only on pages that match; a page break is checked only when the two
  page ends together hold more matches than each alone.
- Table detection skips pages without enough vector paths to draw a table (a running header's rule is not enough;
  pdfium answers in about a millisecond per page) and opens only the pages it needs in pdfplumber, instead of
  building every page of the file.
- Redaction checks the characters around each box only on the lines near it.
- n-up and scale wrap page content in Form XObjects or `q … Q` streams instead of parsing and rewriting it.

## Slow paths to know

- `--layout` and `--tables` use pdfplumber (pure Python): about 40 ms per page for layout and 40 to 60 ms per
  page that holds a table, cold. Read big files with the map, then `--pages`.
- `pdf_redact.py` rewrites every page that has a target (pypdf parses its content, about 15 ms per dense page) and
  then reads the whole output again to verify it: about 6 s for 1000 pages with one target, 40 s when almost every
  page has several. `pdf_optimize.py` rewrites the whole file (4 s per 1000 pages).
- `pdf_compare.py` renders both versions of every page pair (about 50 ms per changed pair): narrow big
  comparisons with `--pages`, or use `--text-only` first. At most `--max-images` (20) diff images are written.
- `pdf_render.py --sheet` composes thumbnails in Python; contact sheets stop at 30 sheets (360 pages): pass
  `--pages` for the rest.
- pdfium has no decompression limit: a small file whose content stream inflates to hundreds of MB costs that much
  memory while it is read.
