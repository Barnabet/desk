# Creating PDFs with pdf_create.py

`pdf_create.py` converts the input with the bundled pandoc to Typst, lays it out with Desk's templates
(`templates/desk.typ`) and compiles it with the bundled Typst. Nothing else needs to be installed, and the default
fonts ship with Typst, so the same input gives the same PDF on macOS and Windows.

Inputs: Markdown (`.md`, `.markdown`, `-` for stdin), HTML, reStructuredText, Org, LaTeX, DOCX, ODT, EPUB, Jupyter
notebooks, and `.typ` files (compiled directly). `--from FORMAT` names the pandoc reader for unusual extensions.

## Office files

`.docx`, `.docm`, `.odt`, `.rtf`, `.doc`, `.pptx`, `.ppt`, `.odp`, `.xlsx`, `.xls`, `.ods` and other formats
LibreOffice opens are handled by `--engine`:

| `--engine` | What happens |
|---|---|
| `auto` (default) | LibreOffice exports the file with its own layout when it is installed; otherwise `pandoc` for .docx, .odt and .rtf, and an error pointing to the `presentations` or `spreadsheets` skill for decks and workbooks |
| `office` | LibreOffice only; an error when it is not installed |
| `pandoc` | the content goes through pandoc and one of the templates below (`--template`, `--toc` and the layout flags apply) |

LibreOffice is found on PATH, in the usual install folders on macOS and Windows, or through `DESK_SOFFICE` (a
path; `none` hides it). The result reports `engine` (`libreoffice`, `pandoc+typst` or `typst`). Layout flags are
ignored by a LibreOffice export, and the output says so. For a Word document without LibreOffice, the
`word-documents` skill (`docx_convert.py`) keeps the page layout closer than pandoc does.

Zip-based files (.docx, .odt, .epub, .pptx, .xlsx …) are checked before LibreOffice or pandoc opens them: a part
that would inflate to an extreme size or ratio is refused as a possible zip bomb. For a trusted file, raise the
limits with the environment variables the error names (`DESK_ZIP_MAX_RATIO`, `DESK_ZIP_MEMBER_MAX_MB`,
`DESK_ZIP_MAX_MB`).

## Templates

| `--template` | Looks like | Uses |
|---|---|---|
| `report` | title page (title, subtitle, authors, date, abstract), optional contents, running header with the title and date, page numbers "n / total", coloured headings | the default when the document has a title |
| `memo` | "Memorandum" heading, To / From / CC / Date / Subject block, then the body | internal notes |
| `letter` | sender block, recipient, place and date, subject line, body, closing and signature | letters |
| `plain` | the body with a simple title block when there is a title; page numbers | everything else |

## Front matter

```markdown
---
title: Field report
subtitle: Site visits, spring 2026
author: [Ada Lovelace, Charles Babbage]
date: today                      # or any text: "March 2026"
abstract: |
  One paragraph that goes on the title page.
# memo
to: All staff
from: Operations
cc: [Legal, Finance]
subject: New badge policy
# letter
sender: [Acme Ltd, 1 Main Street, Springfield]
recipient: [Ms Jane Roe, 2 Side Road, Shelbyville]
place: Springfield
closing: Kind regards,
signature: Ada Lovelace
---
```

Command-line flags override front matter: `--title`, `--subtitle`, `--author` (repeatable), `--date` (`today` is
today's date), `--subject`, `--to`, `--from-name`.

## Layout options

- `--page-size a4|letter|legal|a5|a3|148x210mm`, `--margin 2cm` (or `2cm,3cm`, or `top,right,bottom,left`).
- `--font "Source Serif 4"` (an installed family) or `--font path/to/Font.ttf`; `--font-path DIR` adds a folder of
  fonts. Missing families fall back to Libertinus Serif. `--font-size 10.5pt`.
- `--toc` (with `--toc-depth N`) and `--number-sections` (1, 1.1, 1.1.1).
- `--header` and `--footer` replace the running header and footer; `{page}`, `{pages}` and `{title}` are filled
  in: `--footer "Confidential · {page} of {pages}"`.
- `--accent "#8b0000"` colours headings and rules; `--no-justify` for ragged-right text (titles, headings and
  table cells are never justified); `--lang fr` sets hyphenation and quotation marks.

## What Markdown can do

Pandoc Markdown with smart punctuation: headings, emphasis, lists (nested, numbered, task lists), tables (pipe and
grid tables; long tables break across pages and repeat their header; cells are ragged right and never hyphenated,
columns without an alignment start at the left, `---:` right-aligns numbers; rows are shaded alternately, and
`pdf_text.py --tables` reads every row back), images (paths relative to the Markdown file; remote images are
downloaded, see below), links, footnotes, block quotes, definition lists, fenced code with syntax
highlighting, math (`$E = mc^2$`, `$$ … $$`), and horizontal rules.

Pictures and includes come only from the input's folder. pandoc runs with `--sandbox`, and Desk's filter hands
it the pictures inside the folder, so a stranger's document cannot put the user's files into the PDF. Absolute
paths, paths leading outside (also through a symlink) and `file:` URLs are left out with a warning. Includes
inside the folder (reStructuredText `.. include::`, Org `#+INCLUDE:`, LaTeX `\input`/`\include`) are inlined;
others are not read. Remote images are downloaded by the skill (8 s each, 30 s in all, 20 MB each); one that
cannot be fetched becomes a link with its alt text. `DESK_OFFLINE=1` downloads nothing.

- A page break, or anything else Typst can do, goes in a raw Typst block:

  ````markdown
  ```{=typst}
  #pagebreak()
  ```
  ````

- Citations: `--bibliography refs.bib` (BibTeX, CSL JSON or YAML) turns `[@key]` and `@key` into citations and adds
  the reference list; `--csl style.csl` picks the style. Without a bibliography, `@name` stays plain text (handy
  for e-mail addresses and handles).
- Pandoc warnings (unknown images, unresolved links) are summarised in the output. Read them.

## Your own Typst

`.typ` input is compiled as it is, with the file's folder as the root Typst may read from (`--root` widens it).
To reuse Desk's styles, copy `templates/desk.typ` next to your file:

```typst
#import "desk.typ": desk-doc
#show: desk-doc.with(style: "report", title: [Quarterly report], authors: ([Finance team],), toc: true)

= Summary
Revenue grew by 4 %.
```

`desk-doc` takes the same settings as the flags: `style`, `title`, `subtitle`, `authors`, `date`, `abstract`,
`toc`, `toc-depth`, `number-sections`, `paper` or `page-width`/`page-height`, `margin`, `font`, `fontsize`,
`lang`, `header`, `footer`, `accent`, `justify`, and the memo and letter fields (`to`, `from`, `cc`, `subject`,
`sender`, `recipient`, `place`, `closing`, `signature`).

The quickest way to fine-tune a converted document: `--typ-out src/` saves the generated `.typ` with `desk.typ`
(and any images) in `src/`. Edit it, then compile it with `python3 scripts/pdf_create.py src/report.typ --out
report.pdf`.

## Check the result

`--render DIR` renders the first pages right after creating the PDF. Look at them with view_image: title page,
table widths, image sizes, page breaks before headings, and the header and footer. Then `pdf_text.py` or
`pdf_info.py` for the page count and metadata.
