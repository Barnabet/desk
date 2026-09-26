# Markdown in and out

## Writing Markdown for docx_create.py (and Markdown in docx_edit / JSON specs)

Markdown goes through pandoc, then this skill's styles (or `--template`) are applied. Everything pandoc Markdown
supports works:

| Write | Becomes |
|---|---|
| `# Heading` … `###### Heading` | Heading 1-6 (numbered 1, 1.1 … with `--number-sections`) |
| Paragraphs; `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, `H~2~O`, `x^2^` | Body text with character formatting |
| `- item`, `1. item`, `a. item`, `- [ ] task`; nest by indenting 2-4 spaces | Real Word lists (numbering continues, restarts per list) |
| `> quote` | Block quote |
| Fenced code (three backticks, optional language) | Source Code style, monospace |
| Pipe tables, grid tables; `: Caption` line after a table | Word tables with a header row (repeat on each page) and a caption |
| `![Caption](chart.png){width=60%}` or `{width=8cm}` | Picture with a caption; widths in %, cm, in, px |
| `[text](https://…)`, `<https://…>` | Live hyperlinks |
| `[^1]` and `[^1]: note` | Real footnotes |
| `$E = mc^2$`, `$$\sum_i x_i$$` | Native Word equations |
| `---` front matter with `title`, `subtitle`, `author`, `date`, `abstract` | Title block |
| `::: {custom-style="Quote"}` … `:::` | A paragraph style from the template |
| `[text]{custom-style="Strong"}` | A character style |
| `Term` then `: definition` | Definition list |
| A line with only `\pagebreak` or `\newpage` | Page break (Desk extra) |
| A line with only `[[toc]]` | Table of contents here (Desk extra; `--toc` puts it after the title) |

Smart typography is on: "quotes" become curly, `--` an en dash, `---` an em dash, `...` an ellipsis. Pass
`--no-smart` to keep them as typed.

### Characters that are markup

- **`$` (money).** `$…$` is TeX math only when both signs are on one line (one table cell), hug their content
  (`$x^2$`, not `$ x $`), the closing one is not followed by a digit, and the content is not plain words. So
  `Revenue ($M)`, `$5 to $10` and `$48.2M` stay text. When in doubt, write `\$`: it is always a literal dollar.
- **`|` inside a pipe-table cell** ends the cell: write `\|` for a literal bar. Inside `{{…}}` template tags it is
  escaped for you (`{{total | currency}}` works in a table row).
- **`{{…}}` template tags** are kept exactly as typed: no e-mail links (`{{#each items}}{{@number}}`), no emphasis
  from `_` or `*`, straight quotes. Other text with `@` (`me@example.com`) becomes a `mailto:` link.
- `*`, `_`, `` ` ``, `<`, `[` and `#` at the start of a line keep their Markdown meaning; escape them with `\` for
  the literal character.
- An image alone in its paragraph is a figure: its alt text is the caption. A caption paragraph right after it
  that repeats the alt text (what `docx_read` writes for a captioned picture) is dropped, so a round trip keeps one
  caption.

### Tables in created documents

Tables span the text width. Columns are sized from their content (each column at least as wide as its longest word,
the rest shared by the columns with the longest lines), so a pipe table's dash counts do not matter; a JSON spec's
`widths` keeps exact widths. The paragraph after a table gets a little space above it. Alignment follows the
separator row (`:--`, `--:`, `:-:`).

Images are looked up next to the Markdown file, then in the current folder, then in `--resource-path`, and only
there: pandoc runs with `--sandbox`, and Desk's filter hands it the pictures inside those folders. Pictures named
by an absolute path, a path leading outside (also through a symlink) or a `file:` URL are left out with a
warning, so converting a stranger's document never puts the user's files into it. Includes inside the document's
folder (reStructuredText, Org, LaTeX) are inlined; others are not read. Remote images are downloaded with limits
(8 s each, 30 s in all, 20 MB each; `DESK_OFFLINE=1` downloads nothing); one that cannot be fetched becomes a
link.

### Style presets

`--style report` (default: Calibri body, dark blue headings with a rule under Heading 1, A4, 2.5 cm margins),
`--style classic` (Georgia serif, black headings), `--style modern` (Arial, strong colour). Tune with `--font`,
`--heading-font`, `--font-size`, `--color`, `--line-spacing`, `--size`, `--margins`, `--lang`. With
`--template house.dotx` the user's own styles, page setup, headers and footers are used instead, and the styles a
template lacks fall back to Word's defaults.

## Reading: what docx_read.py prints

| Markdown | Meaning |
|---|---|
| `# …` | Headings (a Title-style paragraph is `#`) |
| `1.`, `a)`, `2.3.1`, `-` | The list labels Word would show, nested by 3 spaces per level |
| `\| … \|` or `<table>` | Tables; HTML (with colspan/rowspan) when cells are merged. `--tables pipe` forces pipe tables. A line break or second paragraph inside a cell is `<br>` |
| `{++inserted++}`, `{--deleted--}` | Tracked changes (CriticMarkup), with authors in `--format json`. `--changes accept` or `reject` shows the text either way |
| `{==text==}{>>Author: comment<<}` | A comment on that text; replies follow with ↳. `--comments end` lists them at the end instead |
| `[^1]`, `[^e1]` | Footnotes and endnotes, with their text at the end |
| `![alt](media/image1.png)` | Pictures (`--extract-media DIR` saves them) |
| `> [text box]` | Text in text boxes and shapes |
| `$…$`, `$$…$$` | Equations, as LaTeX |
| `{PAGE}`, `{NUMPAGES}`, `«Name»` | Page fields; merge fields without a value |
| `<!-- header: … -->`, `<!-- footer: … -->` | Headers and footers (`--no-headers` hides them) |
| `<!-- page break -->`, `<!-- section break (nextPage) -->` | Breaks |
| `- Title … 3` | Table of contents entries with their page |
| `chart: …`, `diagram: …` | Chart data (series and values) and SmartArt text |

`--indexes` prefixes every block with `[n]`, the index docx_edit.py uses. `--format json` gives one object per
block: `index`, `type`, `style`, `text` (plain text as it reads now: tracked insertions in, deletions out),
`heading`, `list` (label, level), `align`, `text_boxes`, table `cells` (each with plain `text`, where line breaks
are `\n`, plus `md` when its Markdown says more, and `colspan`/`rowspan`), and with
`--runs` every run's effective formatting (font, size, bold, colour…, resolved through the style chain, and
`change: ins|del` for revisions). `--format text` is the plain text as it reads now.

## Finding your way in a long document

| Command | Gives |
|---|---|
| `docx_read.py f.docx` | Short documents: the Markdown. Long ones (over `--max-chars`, default 60,000): a map, one line per section: `[first-last block] heading (words)`, then suggested next commands |
| `--outline` | Every heading with its block index (JSON adds each section's last block as `end`) |
| `--section "Results"` | The first heading containing the text (an exact match wins), with everything under it; `--section 143` is the section holding block 143. Other matching headings are listed on stderr |
| `--blocks 120-180` | A block range (`12`, `10-40`, `100-`, lists with commas) |
| `--find TEXT` / `--grep REGEX` | Every match with its address (`[143]`, `[footnote 3]`, `[comment c5]`, `[header (default, section 1)]`), the section path above it and up to three context snippets with the match in `**bold**`; `--context N` sets the width, `--match-case` the case. Text boxes are searched with their paragraph |
| `--full` | The whole text in parts |
| `--format csv --blocks N` | Table N as CSV: merged cells repeat their value over their span; lines inside a cell stay separate lines in one quoted value |

A document without heading styles (a contract whose "ARTICLE 12" lines are just bold text) gets a map built from
its heading-like paragraphs: lines starting "Article N", "Section N", "Chapter N", "Schedule N"…, else paragraphs
that are bold throughout, else short lines in capitals. `--outline` and `--section "Article 12"` use them too
(`Article 1` never matches `Article 12`). If none are found the map cuts the document into even slices; then
`--grep '^ARTICLE'` (or any pattern its titles share) finds the way.

`--grep` takes Python regular expressions; one search that runs over 3 seconds (a pattern like `(a|aa)+$`)
stops with an error instead of hanging.

Every output cut by `--max-chars` ends with `Continue with: <exact command>`. JSON stays valid when cut: it gains
`truncated: {shown_blocks, next}` and keeps only the notes and images the shown blocks refer to. The JSON also
reports `timing` (`cache: hit|miss`, seconds).
