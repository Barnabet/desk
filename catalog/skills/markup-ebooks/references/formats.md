# Formats and options

## Reading: extension → format

| Extensions | Format (`--from`) | Read by |
|---|---|---|
| .md .markdown .mdown .mkd .qmd .rmd .txt | markdown (pandoc Markdown: tables, footnotes, math, YAML front matter) | as written (mk_read), pandoc (convert) |
| .html .htm .xhtml | html | this skill's converter (mk_read, html_extract), pandoc (convert) |
| .rst | rst (Sphinx roles and API directives understood) | pandoc |
| .tex .latex .ltx | latex | pandoc (macros it knows; packages it does not are dropped) |
| .org | org | pandoc |
| .adoc .asciidoc .asc | asciidoc | pandoc |
| .typ | typst | pandoc (to other formats); Typst itself for PDF |
| .textile | textile | pandoc |
| .wiki .mediawiki | mediawiki | pandoc |
| .xml .dbk .docbook | docbook, jats, fb2 or opml, sniffed from the root element | pandoc |
| .epub | epub (2 and 3) | this skill (mk_read, epub_tool), pandoc (convert) |
| .fb2 | fb2 | pandoc |
| .ipynb | ipynb (nbformat 3 and 4) | this skill (mk_read, nb_tool), pandoc (convert) |
| .rtf | rtf | pandoc |
| .docx .odt .pptx | docx, odt, pptx | pandoc (text, tables, images; use word-documents/presentations for editing) |
| .bib .ris .json | bibtex, ris, csljson (bibliographies) | pandoc |

Pass `--from gfm`, `--from commonmark_x` or `--from markdown_strict` for other Markdown dialects. A `.txt` file is
read as Markdown; for text whose line breaks matter, wrap it in a code block or use `--from markdown+hard_line_breaks`.

## Writing: extension → format

| Output | Notes |
|---|---|
| .pdf | pandoc → Typst with Desk's templates (article, report, book) or your own `.typ` template |
| .html | HTML5, standalone and self-contained (CSS and images embedded, math as MathML); `--no-embed` links them instead, `--fragment` gives the body only, `--css` replaces Desk's stylesheet. The `<title>` is the document's title, else its first heading, else the file name |
| .epub | EPUB 3 with navigation, Desk's e-book stylesheet, MathML; `--cover`, `--split-level`, `--toc` for a visible contents page, `-M publisher=… rights=… identifier=…` |
| .docx .odt | Word/ODF; `--reference-doc` copies styles, page size, headers from a document of yours; `--toc`, `-N` |
| .pptx | slides from headings (use the presentations skill for designed decks) |
| .md | pandoc Markdown with pipe tables and YAML front matter; `--to gfm` for GitHub, `--to commonmark`. From EPUB, HTML or office files the reader's scaffolding (section divs, anchor spans, inline SVG, duplicate cover) is dropped and pictures, including images embedded in HTML as data: URIs, are saved to `<output>_media/` |
| .typ | Typst source using Desk's template (markup.typ is copied next to it) |
| .tex .rst .org .adoc .textile .wiki .txt .fb2 .rtf .xml (DocBook 5) .jats .ipynb .opml .json | pandoc writers |

`--to` names any pandoc writer (`python3 scripts/mk_convert.py --list-formats`), plus aliases: md, html, epub,
txt, tex, docbook, adoc, wiki, typ.

## What Desk does before pandoc

- **Only the document's own files**: a stranger's document must not pull the user's files into what Desk reads or
  writes, so includes and pictures come only from the input's folder and `--resource-path` folders.
  - Includes are inlined by Desk when their target is inside those folders: AsciiDoc `include::`, reST
    `.. include::`, `.. literalinclude::` and `:file:` options (raw, csv-table), Org `#+INCLUDE:`, LaTeX `\input`,
    `\include`, `\subfile`, `\import`, `\lstinputlisting`, `\verbatiminput`, `\inputminted`, Typst `#include`.
    Absolute paths, paths leading outside (also through a symlink) and URLs are left out with a warning.
  - pandoc then reads every format but Markdown, HTML and self-contained binary ones with `--sandbox`, into its JSON
    form: an include Desk did not inline (one built by a LaTeX macro, man `.so`, Typst `#read`) is not read.
  - Pictures, stylesheets, covers and bibliographies the document names (Markdown and HTML images, raw `<img>`,
    `url()` in `<style>`, `css:`/`cover-image:`/`bibliography:` metadata) are checked by Desk's filter: files
    inside the folders are embedded, the rest is left out with a warning (`image left out (outside the
    document's folder): …`). A stylesheet from the document that loads other files is left out too.
- **AsciiDoc**: pandoc's reader does not know preprocessor directives, so Desk resolves them first:
  `ifdef`/`ifndef`/`ifeval`/`endif`, `include::file[]` (with `tag=`/`tags=`, `lines=` and `leveloffset=`; a remote
  include becomes a link), `//` comments and `{attribute}` references, the way Asciidoctor's SAFE mode does.
  Unresolved includes are reported.
- **HTML**: `<iframe>`s become links (pandoc would fetch them). `--main` keeps only the article, the same one that
  `html_extract.py` finds. A page nested more than 2 048 elements deep (where the HTML parser stops) is refused, or
  read up to that point with a warning.
- **Markdown**: pandoc's Markdown reader takes exponential time on nested brackets (10 levels take seconds, 16
  never finish), so `[` nested more than 3 deep (1 in a paragraph with over 200 of them) is escaped, and quote
  markers past 32 levels are dropped, each with a warning. Binary data given as a text document is refused.
- **reST (Sphinx)**: roles lose their markup rules (`:func:`~pkg.spam`` → `spam()`, `:class:`!Eggs`` → `Eggs`,
  `:ref:`text <label>`` → text, `:pep:` → PEP 8); API directives (`function`, `method`, `class`, `attribute`,
  `exception`, `data`, `decorator`, …) become a definition list with the signature as code; `versionadded`,
  `versionchanged` and `deprecated` become "New in version 3.2:" notes; `module` keeps its synopsis.
- **Zip-based input** (EPUB, DOCX, ODT, PPTX): declared sizes and compression ratios are checked, and every member
  is inflated in bounded steps and measured before pandoc reads the file, so a member that lies about its size is
  refused instead of filling memory. `DESK_EPUB_MEMBER_MAX_MB` (default 128) caps one member read into memory.
- **Remote images** (`http…` in Markdown, HTML, reST, LaTeX, DocBook, …): outputs that embed images (PDF, DOCX,
  ODT, PPTX, EPUB, self-contained HTML, `--extract-media`) get local copies downloaded first. The limits are 8 s
  per image, 30 s in total, at most 80 images of up to 20 MB each. An image that cannot be fetched stays a link
  and a warning names it. `--offline` (or `DESK_OFFLINE=1`) downloads nothing.
- **PDF (Typst)**: dangling `#links` and empty links keep their text, duplicate ids keep the first element, and
  citations stay as written without `--bibliography`. Images Typst cannot decode (GIF, TIFF, BMP, AVIF, CMYK JPEG,
  wrong extensions) are converted to PNG, and a missing image becomes a small "missing image" box. If Typst still
  fails, math and raw Typst are typeset as source text. Each of these is reported as a warning.
- **Long documents**: `--section ADDR` converts one part. A PDF of more than about 1.5 MB of text is converted in
  parts (see performance.md).

## Options that apply everywhere

| Option | Effect |
|---|---|
| `--toc`, `--toc-depth N` | table of contents (PDF, HTML, DOCX, ODT, EPUB, …) |
| `-N`, `--number-sections` | numbered headings |
| `-M key=value`, `--metadata-file meta.yaml`, `--title`, `--author` (repeat), `--date today`, `--lang fr` | document metadata |
| `--bibliography refs.bib` (repeat), `--csl style.csl` | citations `[@key]`, `@key`, `[-@key]`, `[see @a; @b, p. 3]` and a reference list at the end (or where `::: {#refs} :::` is) |
| `--resource-path DIR` | where to find images and includes besides the input's folder |
| `--shift-heading-level-by -1` | when a document's `##` headings should be the top level |
| `--extract-media DIR` | where images from DOCX/EPUB/ODT/notebook input go (default `<output>_media`) |
| `--wrap auto` | wrap text outputs at 72 columns (default: one line per paragraph) |
| `--highlight-style kate` | code colours in HTML/DOCX/EPUB (pygments, tango, espresso, zenburn, monochrome, breezedark, haddock) |
| `--section ADDR` | only that part: an outline address (`mk_read.py --outline`), a heading, an EPUB TOC entry `tK` or spine document `ch N` (repeatable) |
| `--offline` | no downloads: remote images stay links |
| `--extra=--pandoc-option` | any other pandoc option, e.g. `--extra=--top-level-division=chapter` (see recipes.md) |

## Markdown features worth knowing

- Title block: YAML front matter (`title`, `subtitle`, `author` (list), `date`, `abstract`, `keywords`, `lang`,
  `publisher`, `rights`).
- Tables: pipe tables with `: Caption` below; grid tables for multi-line cells.
- Figures: an image alone in a paragraph becomes a figure with its alt text as the caption; `{width=50%}` sizes it.
- Math: `$inline$` and `$$display$$` (TeX syntax, converted to Typst math, MathML or Word equations).
- Footnotes `[^1]`, definition lists (`Term` then `: definition`), task lists, raw blocks `` ```{=typst} ``.
- Links inside the document: headings get ids (`# Intro {#intro}`, or automatic ones), link with `[see](#intro)`.
- Numbered cross-references (Quarto syntax): `@sec-id` to a heading `{#sec-id}`, `@fig-id` to an image alone in a
  paragraph `![Caption](x.png){#fig-id}`, `@tbl-id` to a table whose caption ends in `{#tbl-id}`; several in one:
  `[@fig-a; @tbl-b]`, with a prefix: `[see @fig-a]`. PDF: Typst's "Figure 1", "Table 2", "Section 3.1" (a heading
  without `-N` shows its title). HTML, DOCX, ODT, EPUB, LaTeX: links named the same way. An id without a target
  stays as written, with a warning. They work with or without `--bibliography`.
- A page break in PDF: a raw Typst block `` ```{=typst}`` / `#pagebreak()` / `` ``` ``.

## Notebooks

`nb_tool.py` handles nbformat 3 and 4. Output types: stream (stdout/stderr), execute_result, display_data
(images: PNG, JPEG, GIF, WebP, SVG; HTML tables become Markdown tables), error (traceback with ANSI colours
removed). Markdown attachments (pasted images) are extracted too. `strip` keeps `tags`, `slideshow` and
`raw_mimetype` cell metadata and the kernelspec/language; everything else goes.
