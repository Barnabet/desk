---
name: markup-ebooks
description: Read, convert, typeset, render, check and extract markup documents and e-books. Markdown (.md), HTML (.html .htm), reStructuredText (.rst), LaTeX (.tex), Org (.org), AsciiDoc (.adoc), Typst (.typ), Textile, MediaWiki, DocBook and JATS XML, EPUB (.epub), FB2, Jupyter notebooks (.ipynb), RTF and plain text, with .bib/.csl citations. Use to convert between any of these and to PDF (typeset with Typst in article, report or book style with TOC, numbering, math, code, citations), DOCX, ODT, EPUB or standalone HTML; to render pages as PNG and look at them; to extract the main article from a saved web page as clean Markdown (links, tables, images); to lint Markdown (broken links and anchors, headings, tables, front matter) and build a TOC; to inspect, read, build, check, repair or convert EPUB books; to outline notebooks, save their plots, find errors, strip outputs, merge them or convert them to Markdown, Python, HTML or PDF. Big files open as a map with section addresses; long ones typeset by section.
license: MIT
---

# Markup and e-books

Scripts in `scripts/` use the bundled pandoc (conversions), Typst (PDF typesetting), lxml and pypdfium2. Desk sets
up their Python environment, so run them with `python3`. Every script has `--help`, prints Markdown by default and
JSON with `--format json`, and never changes its input: outputs go to a new path, and an existing file is only
replaced with `--force`. Nothing here needs LibreOffice. The network is used only to download remote (`http`)
images a document shows, within a time limit; `--offline` skips that.

Work in a loop: **look** at the document, **act** on it, then **check** the result by rendering it and looking.

## Look

```bash
python3 scripts/mk_read.py notes.md                    # the whole text, or a map when it is long
python3 scripts/mk_read.py thesis.tex --outline        # sections with addresses (1, 2.3, 2.3.1), lines, words
python3 scripts/mk_read.py manual.html --section 4.2   # one section with its subsections
python3 scripts/mk_read.py manual.html --find "rate limit"         # matches with section address and line
python3 scripts/mk_render.py report.md --pages 1-3     # typeset pages as PNGs
python3 scripts/mk_render.py book.epub --section t12   # a long document: one part, typeset alone
```

`mk_read.py` reads every format here as Markdown (`--text` for plain text): Markdown as written, HTML and EPUB with
this skill's converter, notebooks cell by cell, everything else through pandoc. Line numbers refer to that
Markdown. `--lines 120-340` reads a range. For an EPUB, `--chapters 5` reads a spine document and `--section t12`
(or part of a TOC title) one table-of-contents entry with its sub-entries: a part brings all its chapters.

Then look at rendered pages with view_image: layout, tables, math, code and pictures show there, not in text.

## Act

### Convert and typeset

```bash
python3 scripts/mk_convert.py notes.md notes.pdf --toc --number-sections
python3 scripts/mk_convert.py report.md report.pdf --template report --bibliography refs.bib --csl apa.csl
python3 scripts/mk_convert.py ch*.md book.pdf --template book --title "Field Notes" --author "A. Lee" --toc
python3 scripts/mk_convert.py README.rst README.html          # one self-contained file, clean stylesheet
python3 scripts/mk_convert.py paper.tex paper.docx --reference-doc house-style.docx
python3 scripts/mk_convert.py book.epub book.md               # clean Markdown; images go to book_media/
python3 scripts/mk_convert.py saved.html article.pdf --main   # only the page's article, with its byline
python3 scripts/mk_convert.py manual.md setup.docx --section 3.2      # one section (address from --outline)
python3 scripts/mk_convert.py docs/*.md --to html --out-dir site/     # batch, in parallel
```

- The output format comes from the extension or `--to` (md, gfm, html, pdf, docx, odt, pptx, epub, rst, tex,
  typ, org, adoc, txt, …; `--list-formats`). Several inputs are joined in order into one document.
- PDF goes pandoc → Typst with Desk's templates: `article` (default: title block, running header, page numbers),
  `report` (title page, contents on its own pages, each top-level section on a new page, "n / N" footer; for a
  short report `article` wastes less paper) and `book` (A5, cover with `--cover`, copyright page when there is a
  date, publisher or rights, "Chapter N" openers with `-N`, mirrored running heads, roman-numbered front matter,
  page 1 on a right-hand page). With `report` and `book` the top heading level becomes the chapters: headings
  that start at `##`, or a lone `#` title above `##` chapters, move up one level.
- Layout: `--paper a4|letter|a5|6x9in`, `--margin`, `--columns 2`, `--font`, `--heading-font`, `--font-size`,
  `--line-spacing`, `--accent teal`, `--header`/`--footer` with `{page}`, `{pages}`, `{title}`, `{section}`,
  `--lof`/`--lot`, `--lang` (hyphenation, quotes, labels). YAML front matter sets title, subtitle, author, date,
  abstract, keywords, publisher and rights; `-M key=value` and `--title`/`--author`/`--date` override it.
- Citations: `--bibliography refs.bib` (or CSL JSON, YAML, RIS) resolves `[@key]` in every output format;
  `--csl style.csl` picks the style (default Chicago author-date).
- Cross-references: give the target an id (`# Results {#sec-results}`, `![Latency](lat.png){#fig-lat}`, a table
  caption `: Uptime {#tbl-up}`) and write `@sec-results`, `@fig-lat`, `@tbl-up` or `[see @fig-lat; @tbl-up]`. PDF
  shows "Figure 1", "Table 2" and, with `-N`, "Section 3" (without numbering, the heading's title); HTML, DOCX
  and EPUB get links named the same way. `[text](#id)` links work everywhere.
- DOCX and ODT `--toc` is a field that Word fills in when the file is opened (it asks to update fields);
  `mk_read.py` shows it empty. For a TOC that is there at once, make a PDF or HTML.
- `--typ-out doc.typ` keeps the generated Typst to fine-tune it; compile it again with `mk_convert.py doc.typ
  doc.pdf`. Your own Typst template: `--template my.typ` (see `references/templates.md`).
- Before pandoc, Desk resolves AsciiDoc `ifdef`/`ifeval`/`include::` and `{attributes}`, turns HTML iframes into
  links and downloads remote images (8 s each, 30 s in all; failures stay links). For PDF, a Lua filter and
  image checks fix what Typst would refuse: dangling `#links` keep their text, GIF/TIFF/CMYK images become PNG.
- The script prints what pandoc and Typst warned about: missing images (replaced by a placeholder or their
  description), unresolved citations, unknown fonts, links kept as text. Report those to the user or fix them.

### Web pages: extract the article

```bash
python3 scripts/html_extract.py saved.html --url https://site.example/post/42    # clean Markdown article
python3 scripts/html_extract.py saved.html --text          # plain text
python3 scripts/html_extract.py saved.html --links         # links of the article (--all: whole page)
python3 scripts/html_extract.py saved.html --tables --csv tables/     # data tables as CSV files
python3 scripts/html_extract.py saved.html --images        # image URLs, alt text, captions, local copies
python3 scripts/html_extract.py saved.html --meta          # title, byline, date, site, language, canonical URL
```

The article comes with its title, byline, date and source, without navigation, cookie banners, share bars,
sidebars or footers. Headings, lists, tables, code (with its language), figures and links stay; relative URLs
become absolute with `--url` (or the page's own `<base>` or canonical link). `--all` converts the whole page.
If an important part is missing, compare with `--all`. A long article prints page by page (the output ends with
the `--offset` command for the next part); for a map of it with section addresses use `mk_read.py page.html
--main`. For fetching live pages use the web-research skill.

### Check Markdown

```bash
python3 scripts/md_check.py README.md                   # problems with line numbers, words, reading time
python3 scripts/md_check.py docs/                       # a folder, with links and #anchors across files
python3 scripts/md_check.py README.md --toc             # a TOC with GitHub anchors
python3 scripts/md_check.py README.md --fix README.fixed.md --add-toc
```

It finds broken relative links and images, `#anchors` that match no heading (with a suggestion), empty links,
images without alt text, undefined references and footnotes, invalid YAML/TOML front matter, heading jumps and
duplicates, `#heading` without a space, table rows with the wrong number of cells, unclosed code fences and a
stale TOC between `<!-- toc -->` and `<!-- tocstop -->`. `[text][Heading]` references that only pandoc resolves
(implicit header references) are notes, not errors. `--fix` writes a new file with the safe fixes (anchors
that only differ in spelling, heading spaces, short table rows padded, fences closed, whitespace, the TOC). Remote
URLs are not fetched. `--strict` exits 1 when there are errors.

### E-books

```bash
python3 scripts/epub_tool.py info book.epub             # metadata, version, cover, sizes, words, DRM
python3 scripts/epub_tool.py toc book.epub
python3 scripts/epub_tool.py read book.epub --section t12
python3 scripts/epub_tool.py extract book.epub --cover cover.jpg --images imgs/ --sheet
python3 scripts/epub_tool.py build ch1.md ch2.md -o book.epub --title "Tides" --author "M. Rao" --cover cover.png --lang en
python3 scripts/epub_tool.py check book.epub            # mimetype, container, OPF, manifest, spine, nav, links
python3 scripts/epub_tool.py repack broken.epub fixed.epub
python3 scripts/epub_tool.py convert book.epub book.pdf --template book --toc
```

`build` makes EPUB 3 (a chapter per level-1 heading, navigation TOC, e-book stylesheet, MathML, cover, metadata
with `-M publisher=…`) and checks the result. `repack` fixes zip-level problems only (mimetype first and stored,
junk files, paths that escape the folder); `check` says what else is wrong. DRM-encrypted books cannot be read;
`info` says so. To read a book as Markdown, `mk_read.py book.epub` is lighter than converting it (chapter markers,
no image files); `mk_convert.py book.epub book.md` writes a Markdown file with the pictures saved beside it.

### Notebooks

```bash
python3 scripts/nb_tool.py outline analysis.ipynb        # cells, execution order, outputs, errors
python3 scripts/nb_tool.py read analysis.ipynb --cells 5-12
python3 scripts/nb_tool.py images analysis.ipynb --out-dir plots --sheet      # plots as PNGs to look at
python3 scripts/nb_tool.py errors analysis.ipynb
python3 scripts/nb_tool.py convert analysis.ipynb report.pdf --title "Analysis"   # also .md .py .html .docx
python3 scripts/nb_tool.py strip analysis.ipynb -o clean.ipynb
python3 scripts/nb_tool.py merge part1.ipynb part2.ipynb -o all.ipynb
python3 scripts/nb_tool.py create script.py -o script.ipynb      # from # %% cells (or from Markdown)
```

Notebooks are never executed: outputs are what was saved. `images` turns PNG, JPEG and SVG outputs into PNGs sized
for vision; look at plots rather than guessing from `<Figure …>` text. `.py` output uses `# %%` cells and comments
out IPython magics so the file stays valid Python.

## Check

1. Render what you made and look at it: `python3 scripts/mk_render.py out.md --template report --sheet`, then
   view_image the sheet and the pages that matter. Pass the same layout flags you used for the PDF. Look for
   overflowing tables and code, missing pictures, wrong fonts, bad breaks and empty placeholders.
2. Read the result back (`mk_read.py out.docx`, `epub_tool.py check book.epub`, `md_check.py out.md`) and compare
   it with what was asked. Fix and repeat.
3. Tell the user where the file is, which template and options you used, and every warning you could not fix.

`mk_render.py` accepts everything `mk_convert.py` reads (and PDFs): `--pages 1-3,last`, `--section "Results"`
(pages of that heading; an address like `2.3` or `t12` typesets that part alone), `--find "Table 2"` (pages where
it appears), `--sheet`, `--dpi`, `--pdf keep.pdf`. HTML is typeset as a document there, not drawn like a browser.

## Big files

Long documents never print whole by default. Above the budget (`--max-chars`, default 60 000 characters)
`mk_read.py` prints a **map**: every section with its address, line span and words, runs of sections grouped
(`1.31–1.60`) so the whole document stays covered; EPUBs list chapters or TOC entries (`t12`). Then drill down:

- `--section 2.3` or `--section "Pricing"`, `--lines A-B`, `--chapters 5`, `--find TEXT` (hits carry addresses).
- Every cut output ends with `[… cut at character N. Continue: python3 scripts/… --offset N]`: run exactly that.
- `html_extract.py --tables` gives CSV for tables over 40 rows; `--links/--images --all` stream huge pages.
- Conversions, maps, extractions and typeset PDFs of big files are cached by content, so the next call (another
  section, other pages) is instant. `--no-cache` forces a fresh run.

Typesetting has limits too: pandoc holds a whole document in memory (about 200 MB per MB of text), and a book is
hundreds of pages. For a document over about 600 KB of text, `mk_render.py` shows the beginning unless you ask for
more. `--section 2.3` (or `t12`, `ch 5`) and `--find TEXT` typeset only that part, in a second or two. `--pages`
and `--full` typeset everything, which can take a minute the first time and is cached after that. A PDF of more
than about 1.5 MB of text is converted in parts of about 1 MB and joined, with its TOC, numbering and links
intact. For other outputs of such a document, convert it by `--section`. Measured timings are in
`references/performance.md`.

## Rules

- Never modify the user's file. Write outputs in your workspace, or where the user asked, and say where they are.
- Render and look before you hand over a PDF, EPUB or HTML you made. Say which template you used.
- Report what the scripts warned about (missing images, unresolved citations, EPUB problems) instead of hiding it.
- Prefer the user's own template, reference document or stylesheet when they have one (`--template`,
  `--reference-doc`, `--css`).
- Keep extracted web content attributed: give the source URL, title, byline and date with it.

## Limits

- PDF output is typeset by Typst, not LaTeX: raw LaTeX commands in Markdown are dropped (pandoc warns); LaTeX input
  is converted by pandoc, so complex macros and packages may be lost. Tikz and other LaTeX-only graphics do not
  render.
- Fonts: Libertinus Serif, New Computer Modern and DejaVu Sans Mono are built in; other fonts must be installed or
  given with `--font file.ttf`/`--font-path`. Headings use a system sans-serif, so they vary slightly by computer.
- A document only uses files from its own folder and `--resource-path` folders: includes (AsciiDoc,
  reStructuredText, Org, LaTeX, Typst) and pictures there work; absolute paths, paths leading outside, `file:`
  URLs and links pointing out are left out with a warning, so converting a stranger's document never ships the
  user's files. Includes made by other means (LaTeX macros, man `.so`) are not read. Remote pictures are
  downloaded by the skill with limits (`--offline` keeps them as links); pandoc never goes to the network itself.
- `html_extract.py` works on saved HTML; it does not run JavaScript, so pages that build their content in the
  browser come out empty (use the web-research skill's browser). MHTML archives are not supported.
- EPUB: DRM-protected books cannot be read; fixed-layout books convert poorly to other formats.
- Notebooks are not executed; `.ipynb` widgets and interactive outputs (Plotly, Bokeh) show as their text form.
- One pandoc run holds about 4 MB of text for DOCX, ODT or EPUB output and a little more for HTML (2 GB limit);
  inputs over 8 MB are refused at once. Such documents go to PDF in parts, but other outputs have to be made
  section by section (`--section`). A PDF made in parts from HTML, an EPUB or a notebook is typeset from this
  skill's Markdown conversion, so it looks slightly different from pandoc's.
- Sphinx reST: roles (`:func:`, `:class:` …), API directives (function, class and method signatures) and version
  notes are kept; autodoc, `toctree` and references to other documents are not resolved (their text stays).
- Hostile or broken input is refused with a message rather than processed: zip bombs and members that lie about
  their size, XML entity bombs, AsciiDoc attribute bombs and include loops, binary data named `.md`, HTML nested
  thousands of levels deep. Markdown with absurdly nested brackets or quotes is tamed (and says so).
- Pandoc ships as a prebuilt binary; on Apple Silicon Macs it runs through Rosetta 2 (installed on first use by
  macOS), and its first run after installation can take a while.

## Beyond the scripts

For anything else, write a small Python script with lxml, pypdfium2 or Typst (`import typst`) and run it with
the same `python3`, or call pandoc directly with the recipes in `references/recipes.md`.

Neighbouring skills: `word-documents` for editing .docx with tracked changes, `pdf-toolkit` for reading and editing
PDFs, `presentations` for .pptx decks, `images` for editing pictures, `web-research` for fetching web pages, and
`data-files` for JSON, XML data, CSV and YAML.

References: `references/formats.md` (formats, extensions, options per output), `references/templates.md` (the
Typst templates, their variables and your own), `references/recipes.md` (pandoc and Typst recipes, custom
scripts), `references/performance.md` (big-file behaviour and measured timings).
