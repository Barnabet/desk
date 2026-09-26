# Typst templates

PDF output and `mk_render.py` go pandoc → Typst → PDF. The styles live in `templates/markup.typ` (a Typst function,
`desk-markup`) and `templates/pandoc.typ` (the pandoc template that calls it with the document's metadata and the
command-line options).

## The three styles

| | article (default) | report | book |
|---|---|---|---|
| Paper | A4 | A4 | A5, inside/outside margins |
| Title | title block on page 1 (title, subtitle, authors · date, rule, abstract) | full title page with an accent bar, abstract box | title page, copyright page (date, publisher, rights), abstract page, optional cover (`--cover`) |
| Contents | after the title block | own pages, numbered i, ii, … | own pages, numbered i, ii, … |
| Level-1 headings | in the flow | start a new page, ruled | start a new page, with "Chapter N" (localised by `--lang`) when `-N` numbers them |
| Running head | title · current section (from page 2) | title · current section | even pages: page · title; odd pages: chapter · page |
| Footer | page number | page / total | chapter openers show the page number |
| Page 1 | page 1 | first page after the title (and contents) | always a right-hand (odd) page; a blank left-hand page before it has no head or foot |
| Paragraphs | spaced, justified | spaced, justified | indented first lines, no spacing, justified |

**Chapters are the top heading level.** With `report` and `book`, a document whose headings start at `##` is
moved up one level (or more), and a lone `#` heading that opens a document with `##` chapters (a README, a
Gutenberg book) becomes the title, unless the front matter has another title. `--shift-heading-level-by` turns
this off and shifts as you say. Running heads name the current level-1 heading that appears in the contents, so a
body page before the first chapter shows only the title, never "Contents".

Common to all: Libertinus Serif body text (built in), sans-serif headings in the accent colour, code blocks on a
light background with Typst's own syntax highlighting, booktabs-like tables with a tinted header row, figure and
table captions ("Figure 1:", "Table 1:"), footnotes with a short rule, New Computer Modern Math, coloured links, PDF
bookmarks for every heading and PDF metadata (title, authors, keywords).

## Options → template variables

| mk_convert/mk_render option | Variable | Default |
|---|---|---|
| `--template article\|report\|book` | `desk-style` | article |
| `--toc`, `--toc-depth` | `desk-toc`, `desk-toc-depth` | off, 3 |
| `--lof`, `--lot` | lists of figures / tables | off |
| `-N` | `desk-number` (numbering "1.1.1") | off |
| `--paper` | `desk-paper` or `desk-page-width/height` | a4 / a5 (book) |
| `--margin 2cm` / `2cm,3cm` / `t,r,b,l` | `desk-margin` | per style |
| `--columns 2` | `desk-columns` (title spans both) | 1 |
| `--font`, `--heading-font`, `--code-font` | families; a .ttf/.otf path registers the file | Libertinus Serif, system sans, DejaVu Sans Mono |
| `--font-size 10.5pt`, `--line-spacing 1.2` | `desk-fontsize`, `desk-linestretch` | 11pt (10.5pt book), 1.0 |
| `--header`, `--footer` | text with `{page}` `{pages}` `{title}` `{section}` `{chapter}` `{author}` `{date}`; `''` removes it | per style |
| `--accent #1f4e79` or a name | `desk-accent` | #1f4e79 |
| `--no-justify` | ragged right | justified |
| `--lang de-CH` | `desk-lang`, `desk-region` (hyphenation, quotes, "Kapitel") | en |
| `--cover img` | `desk-cover` (book: full-page first page) | none |

Pictures: one with a size (`![A](a.png){width=60%}`) keeps it. One without a size is shown as a 110 dpi screen
would show it (a 320 px picture takes 45% of the text width), never wider than the text and never taller than about
11.5 cm; a picture alone in its paragraph (a Wikipedia thumbnail link, a badge) is centred.

Front matter keys used: `title`, `subtitle`, `author` (string, list, or `{name: …}` entries), `date`, `abstract`,
`abstract-title`, `keywords`, `publisher`, `rights`.

## Tweaking the generated Typst

```bash
python3 scripts/mk_convert.py report.md report.pdf --template report --typ-out build/report.typ
# edit build/report.typ (and build/markup.typ), then:
python3 scripts/mk_convert.py build/report.typ report-final.pdf --force
```

The `.typ` file starts with `#show: doc => desk-markup(...)`: change any argument there. Typst syntax you will
need: `#pagebreak()`, `#v(1em)`, `#align(center)[…]`, `#set text(size: 10pt)`, `#table(columns: 3, […], …)`,
`#image("file.png", width: 60%)`, `#figure(…, caption: […])`.

## Your own template

A template is a pandoc Typst template: a `.typ` file containing `$body$` (and optionally `$title$`,
`$for(author)$…$endfor$`, `$if(toc)$…$endif$`). Pass it with `--template mine.typ`; other `.typ` files in its
folder are copied next to it, so it can `#import "my-style.typ": *`. Put `$body$` in it once: a long
document's parts are placed there. To start from Desk's look, copy
`templates/pandoc.typ` and `templates/markup.typ` into a folder and edit them. Typst documents you write by hand can
use the style directly:

```typst
#import "markup.typ": desk-markup
#show: desk-markup.with(style: "report", title: [Annual review], authors: ([Ann Lee],), date: [2026], toc: true)

= Summary
Text…
```

Then `python3 scripts/mk_convert.py review.typ review.pdf` (the folder of the .typ file is Typst's root: images and
imports must be inside it).

Page numbers in `markup.typ` restart where the labels `<desk-front-start>` (roman front matter) and
`<desk-main-start>` (page 1) are placed, and a page between `<desk-gap>` and `<desk-main-start>` is the blank
left-hand page before a book's page 1. A template of yours that restarts numbering can place the same labels, so
the running head of the restarting page shows the new number.
