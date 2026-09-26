# Big files: behaviour and measured timings

## What happens with a big file

- **A map first.** `mk_read.py`, `epub_tool.py read` and `nb_tool.py outline` print a map (sections with stable
  addresses `1.2.3`, line spans and words; EPUB TOC entries `t12` and spine documents `ch 5`; notebook cells)
  instead of the whole text once it passes `--max-chars` (default 60 000 characters). Addresses depend only on the
  document, so they stay valid between calls.
- **Drill down by address.** `--section`, `--lines`, `--chapters`, `--cells`; `--find` (`--regex` for a regular
  expression) returns hits with those addresses.
- **Budgets end with the exact next command.** A cut output ends with
  `[… cut at character N of M. Continue: python3 scripts/… --offset N]`.
- **Tables as CSV.** `html_extract.py --tables` prints tables over 40 rows as CSV (`--csv DIR` writes files).
- **Streaming.** `html_extract.py --links/--images --all` stream a huge page with `lxml.iterparse`; memory stays
  flat.
- **Cache.** Conversions to Markdown, maps, extractions, EPUB chapter models, typeset PDFs and pandoc outputs are
  cached by content (the file's fingerprint, the options and the code version) in Desk's file cache. Pictures and
  includes a document loaded are part of the entry, so changing one of them rebuilds it. `--no-cache` skips the
  cache; `DESK_NO_CACHE=1` turns it off everywhere.
- **Typesetting long documents.** pandoc keeps a whole document in memory, about 200 MB per MB of Markdown (DOCX
  output about 270 MB), so one run holds roughly 4 MB of text for DOCX, ODT or EPUB and a little more for HTML
  within the 2 GB a tool may use (`DESK_TOOL_MAX_MB`). A text input larger than `DESK_TOOL_MAX_MB` / 256 (8 MB)
  is refused at once for those outputs instead of failing after seconds of work. Desk works around the limit in
  two ways:
  - `mk_render.py` does not typeset a document over about 600 KB of text whole. By default it shows the beginning
    (whole sections, about 150 KB). `--section ADDR` or `--find TEXT` typesets that section alone, and `--pages`
    or `--full` typesets the whole document.
  - A PDF of more than about 1.5 MB of Markdown, HTML, EPUB or notebook text is converted in parts of about 1 MB.
    Each part is one pandoc run, cut at a heading. The parts are joined into one Typst document, so the TOC,
    numbering and page numbers cover everything, and links between parts are resolved afterwards. A notebook over
    8 MB always goes this way, because pandoc's notebook reader holds every embedded image as JSON text.
  - For other outputs (HTML, DOCX, EPUB, …) of a document that long, convert parts with `mk_convert.py --section`.
- **EPUB parts.** A TOC entry whose sub-entries live in later spine documents (a part whose chapters are separate
  files) covers all of them in `--section tK`, in its word count and in the map (`ch 1-40`). A map with more
  entries than the budget holds shows the top levels and groups the next one (`t2–t14`), so it still covers the
  whole book.
- **Bounded zip reads.** EPUB members are read 1 MB at a time with a byte limit, and before pandoc reads an EPUB,
  DOCX, ODT or PPTX every member is inflated in bounded steps and measured (cached per file), so a member that lies
  about its size is refused at a few tens of MB of memory instead of being inflated whole.

## Measured timings

The measurements were taken on a MacBook Air (Apple M2, 8 GB), with pandoc 3.9 (x86_64 under Rosetta 2) and Typst
0.15. Each command ran twice: "cold" is the first run on an empty cache and "cached" is the second. The fixtures
were generated: a 5 MB article page, a 10 MB Markdown file, a 35 MB EPUB (120 chapters, 60 photos and a cover), a
300 KB EPUB in parts (8 parts, 320 chapter files, 1 288 nested TOC entries) and a 63 MB notebook (600 code cells,
130 PNG plots); the 50 MB notebook and 30 MB EPUB of the brief were scaled up slightly rather than down. Times are
wall clock and include Python start-up (about 0.05 s). The machine was shared with other work, so single runs vary
by up to about 2×. The HTML, EPUB and notebook tables were measured again on 2026-09-25 after the acceptance fixes;
the 10 MB Markdown table is from the first build (those code paths did not change).

### 5 MB HTML page (2 810 sections, 670 000 words)

| Command | Cold | Cached | Peak memory |
|---|---|---|---|
| `html_extract.py page.html` (the article) | 0.91 s | 0.06 s | 113 MB |
| `html_extract.py page.html --links --all` (streamed) | 0.10 s | 0.10 s | 63 MB |
| `html_extract.py page.html --tables` (140 tables, CSV) | 0.19 s | 0.04 s | 64 MB |
| `mk_read.py page.html` (map) | 0.65 s | 0.05 s | 82 MB |
| `mk_read.py page.html --section 1.200` | 0.05 s | 0.05 s | 47 MB |
| `mk_read.py page.html --find KEY500X2` | 0.08 s | 0.07 s | 47 MB |
| `mk_render.py page.html` (the beginning, 41 pages) | 2.5 s | 0.45 s | 153 MB |
| `mk_render.py page.html --section 1.300` | 0.57 s | 0.58 s | 143 MB |
| `mk_render.py page.html --find KEY500X2` | 0.46 s | 0.45 s | 105 MB |
| `mk_render.py page.html --pages 1-2` (whole page in 5 parts, 1 298 pages) | 29 s | 0.24 s | 1.2 GB |
| `mk_convert.py page2.html page2.docx` (the page twice, 9.6 MB) | refused in 0.04 s: too big for one run | | |

### 10 MB Markdown (2 222 chapters, 8 890 sections, 1.4 million words)

| Command | Cold | Cached | Peak memory |
|---|---|---|---|
| `mk_read.py big.md` (map) | 0.35 s | 0.07 s | 80 MB |
| `mk_read.py big.md --section 1.1500` | 0.07 s | 0.06 s | 73 MB |
| `mk_read.py big.md --find FINDME2000` | 0.11 s | 0.13 s | 73 MB |
| `md_check.py big.md` | 1.4 s | 1.4 s | 149 MB |
| `mk_convert.py big.md out.html --section 1.700` | 0.51 s | 0.46 s | 103 MB |
| `mk_convert.py big.md out.html` (whole) | stopped at 2 GB after 20 s, with advice to use `--section` or PDF | | |
| `mk_render.py big.md` (the beginning, 44 pages) | 1.8 s | 0.63 s | 178 MB |
| `mk_render.py big.md --section 1.1500` | 0.64 s | 0.64 s | 135 MB |
| `mk_convert.py big.md big.pdf` (11 parts, 2 921 pages) | 46 s | 0.22 s | 1.3 GB |

### 35 MB EPUB (120 chapters, 480 TOC entries, 60 photos)

| Command | Cold | Cached | Peak memory |
|---|---|---|---|
| `epub_tool.py info` | 0.17 s | 0.12 s | 44 MB |
| `epub_tool.py toc` | 0.39 s | 0.04 s | 47 MB |
| `epub_tool.py check` (with the streamed member check) | 0.07 s | 0.07 s | 35 MB |
| `epub_tool.py read` (map), `--section t200`, `--find` | 0.04 s | 0.04 s | 37 MB |
| `epub_tool.py extract --images DIR --sheet` | 0.87 s | | 115 MB |
| `mk_render.py book.epub` (the beginning, 8 chapters) | 2.3 s | 0.29 s | 152 MB |
| `mk_render.py book.epub --section t200` | 0.36 s | 0.36 s | 106 MB |
| `mk_render.py book.epub --pages 1-3` (whole book) | 12 s | 0.25 s | 757 MB |
| `mk_convert.py book.epub book.md` (clean Markdown, images saved) | 7.8 s | 0.25 s | 573 MB |

A real 25 MB EPUB (Gutenberg's Pride and Prejudice with its illustrations): `epub_tool.py check` 0.11 s cold, 0.07 s
cached; `mk_convert.py pride.epub pride.html` 5.4 s.

### EPUB in parts (8 parts × 40 chapter files, 1 288 TOC entries, 178 000 words)

| Command | Cold | Cached |
|---|---|---|
| `epub_tool.py toc` | 0.33 s | 0.04 s |
| `mk_read.py saga.epub --section t1` (a part: 40 chapters) | 0.04 s | 0.04 s |
| `mk_read.py saga.epub --max-chars 6000` (88 rows: parts and chapter groups, whole book) | 0.05 s | 0.05 s |
| `mk_render.py saga.epub --section t1` (the part typeset alone) | 1.6 s | |
| `mk_convert.py saga.epub part1.md --section t1` | 0.42 s | |

### 63 MB notebook (600 code cells, 130 plots)

| Command | Cold | Cached | Peak memory |
|---|---|---|---|
| `nb_tool.py outline` | 0.38 s | 0.27 s | 225 MB |
| `nb_tool.py read --cells 100-110` | 0.17 s | 0.19 s | 227 MB |
| `nb_tool.py errors`, `find` | 0.11 s | 0.12 s | 222 MB |
| `nb_tool.py images --cells 1-40 --sheet` | 0.76 s | | 248 MB |
| `nb_tool.py strip` | 0.14 s | 0.13 s | 223 MB |
| `mk_read.py nb.ipynb` (map) | 0.17 s | 0.04 s | 227 MB |
| `mk_render.py nb.ipynb --section 40` | 1.1 s | 0.56 s | 299 MB |
| `mk_render.py nb.ipynb` (the beginning), first build | 1.5 s | 0.68 s | 486 MB |
| `mk_convert.py nb.ipynb nb.pdf` (whole notebook), first build | 4.6 s | 0.28 s | 608 MB |

Notebook commands read the whole JSON on every call: about 0.1 s and 4 MB of memory per MB of notebook. Their
results are small, so they are not cached.

### Small documents

A one-page Markdown file typesets to PDF in about 1.3 s (mostly pandoc's start-up), and pandoc's 113-page manual
in 2.4 s. `--help` answers in 0.05 s to 0.2 s. The self-test (312 checks) runs in 35 s to 60 s.

### Hostile and broken input

| Input | Result |
|---|---|
| 1.5 MB EPUB whose chapter inflates to 1.5 GB | refused by the declared-size check, 31 MB of memory |
| 300 KB EPUB whose chapter declares 2 KB and inflates to 300 MB | refused as "lies about its size" in 0.08 s, 36 MB |
| 250 KB of Markdown nesting 50 000 brackets and quotes | converted in 7.4 s with warnings (pandoc alone: over 7 minutes) |
| 200 KB of random bytes named `.md` | refused as binary in 0.06 s |
| 2.2 MB HTML nesting 200 000 divs | refused in 0.1 s (the parser stops at 2 048 levels) |
| 60-byte AsciiDoc including itself with a growing attribute | refused as an attribute bomb in 0.08 s |

## Tuning

| Variable | Effect |
|---|---|
| `DESK_TOOL_MAX_MB` | memory limit for pandoc and other tools (default 2048; 0 = none) |
| `DESK_FILE_CACHE`, `DESK_FILE_CACHE_MB`, `DESK_NO_CACHE` | cache folder, size cap (default 1 GB), off |
| `DESK_MK_PART_CHARS`, `DESK_MK_PARTS_FROM` | part size (default 1 000 000 characters) and the length from which a PDF is converted in parts (default 1 500 000) |
| `DESK_MK_NO_PARTS=1` | always convert in one pandoc run |
| `DESK_EPUB_MEMBER_MAX_MB` | largest EPUB member read into memory (default 128) |
| `DESK_ADOC_MAX_MB` | largest AsciiDoc document once includes and attributes are expanded (default 64) |
| `DESK_ZIP_MAX_MB`, `DESK_ZIP_MEMBER_MAX_MB`, `DESK_ZIP_MAX_RATIO`, `DESK_ZIP_MAX_MEMBERS` | zip-bomb limits for EPUB, DOCX, ODT and PPTX input |
| `DESK_REMOTE_TIMEOUT`, `DESK_REMOTE_BUDGET`, `DESK_REMOTE_MAX`, `DESK_REMOTE_MAX_MB`, `DESK_OFFLINE=1` | remote images: timeout per image (8 s), total budget (30 s), count (80), size per image (20 MB), no downloads |
