# Performance

Measured on 2026-09-25 on the development Mac (Apple silicon, 8 GB, Python 3.12, LibreOffice 26.2), inside Desk's
sandbox, with another agent running, so treat the numbers as upper bounds. "Cold" is the
first call on a file; "cached" is a later call on the same file. Parsed reads, overviews, page layouts and page
texts are kept in Desk's file cache, keyed by the file's content, so an edited file is parsed again and a copy of a
file shares its entries. `--no-cache` (or `DESK_NO_CACHE=1`) turns the cache off.

## A 1,000-page document

`big.docx`: 0.9 MB, 507,385 words, 8,403 blocks (120 chapters, 960 sections, 120 tables, 240 footnotes, bullet and
numbered lists), built from 4.3 MB of Markdown with a 1,080-entry table of contents. The built-in renderer lays it
out on 1,277 pages, LibreOffice on 1,231. Measured 2026-09-25 (second round, after the acceptance fixes).

| Command | Cold | Cached |
|---|---|---|
| `docx_create.py big.md big.docx --toc` without LibreOffice (pandoc, then a built-in layout for the TOC page numbers) | 15.9 s | |
| the same with LibreOffice installed (the layout pass is LibreOffice's) | 23.2 s | |
| `docx_info.py big.docx` | 0.65 s | 0.08 s |
| `docx_read.py big.docx` (the map: sections with block ranges and word counts) | 1.5 s | 0.45 s |
| `docx_read.py big.docx --find "needle clause"` (address, section path, context) | | 0.16 s |
| `docx_read.py big.docx --grep 'Table 7\d\.4'` | | 0.15 s |
| `docx_read.py big.docx --section "Chapter 75"` | | 0.11 s |
| `docx_read.py big.docx --blocks 6000-6050 --format json` | | 0.12 s |
| `docx_read.py big.docx --outline` | | 0.11 s |
| `docx_read.py big.docx --blocks 100-120 --format json --runs` (its own cache entry) | 1.4 s | |
| `docx_read.py big.docx --no-cache` | 1.5 s | |
| `docx_render.py big.docx --pages 1-3`, built-in renderer (lays out all 1,277 pages) | 4.6 s | 0.18 s for any other page |
| `docx_render.py big.docx --pages 1-3` with LibreOffice (1,231 pages) | 12.0 s | 0.18 s for any other page |
| `docx_render.py big.docx --find "needle clause"` (extracts every page's text once) | 1.6-1.8 s | |
| `docx_render.py big.docx --block 6001` | | 0.27 s |
| `docx_info.py big.docx --exact` after a render | | 0.10 s (built-in) / 0.65 s (LibreOffice) |
| `docx_convert.py big.docx out.pdf` after a render | | 0.10 s |
| `docx_convert.py big.docx out.md` | 1.6 s | |
| `docx_convert.py big.docx out.html` (mammoth) | 2.2 s | |
| `docx_convert.py big.docx out.txt` | 0.8 s | |
| `docx_edit.py --track` (9,319 tracked replacements across runs, a regex, a Markdown insert, 13 deletions, a format, a table row) | 2.8 s | |
| `docx_compare.py big.docx edited.docx --redline redline.docx` (4,269 changed paragraphs, 18,489 revisions written) | 4.3 s | |
| `docx_template.py big.docx --fields` | 0.21 s | |

The TOC's page numbers are exact for the layout that filled them: Chapter 1 on page 27, 56.3 on 602 and Chapter 120
on 1,266 with the built-in renderer (28, 582 and 1,221 with LibreOffice), read from the PDF's heading bookmarks.
Every script answers `--help` in about 35 ms: heavy libraries load only when a command needs them. Safety checks
on open (zip-bomb limits, no DTDs, missing parts) read only the zip directory, the relationship parts and the first
2 KB of each XML part: a few milliseconds.

A 500-page document takes about half of each cold figure. Documents of a few dozen pages answer every read in
well under a second, and their renders take 1 to 3 seconds (LibreOffice) or under a second (built-in).

## Checked by the selftest

`scripts/selftest.py` builds a scaled-down long document (30 parts, over 20 pages) and checks: the map and its
suggested commands, `--blocks`, `--section`, `--find` with the section path, `--full` paging with an exact continue
command, a cached read at least 5 times faster than the first (the scripts time the parse themselves; interpreter
start-up is noise on a loaded machine), a second render that reuses the cached layout at least 5 times faster,
`render --find` and `render --block` landing on the same page, and the 20-page default when `--pages` is not given.
It also checks that a zip bomb (24 MB of XML packed into a few kilobytes) is refused in under 5 seconds by
docx_read, docx_info and docx_render, that `--grep '(a+)+$'` answers at once and a pattern that does backtrack
stops after 3 seconds, and that `--toc` page numbers match the pages the headings land on.

## What makes it fast

- Reads and overviews parse the document once; later calls (another `--blocks` range, `--section`, `--find`,
  `--outline`, JSON) load the cached result instead of the .docx.
- Rendering lays out the whole document once (LibreOffice or Typst) and caches the PDF. Later renders of other
  pages, `docx_info --exact` and `docx_convert` to PDF reuse it; `--find` and `--block` search its cached page texts.
- `docx_read` on a long document prints a map (sections with block ranges and word counts) instead of 60,000
  characters of text; every cut output ends with the exact command for the next part.
- `docx_compare` matches paragraphs by word overlap, diffs only the changed middle of each paragraph, and pages a
  long report with `--offset`.
- `docx_convert` runs batches in parallel (`--workers`).
