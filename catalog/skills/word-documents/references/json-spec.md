# docx_create.py JSON spec

Use a spec when Markdown is not precise enough: coloured runs, exact table widths and shading, merged cells,
numbered lists with a given format or start, landscape sections, images with sizes. Markdown blocks can be mixed in.

```bash
python3 scripts/docx_create.py --spec spec.json out/invoice.docx
python3 scripts/docx_create.py --spec '{"blocks":[{"type":"heading","text":"Hi"}]}' hi.docx --style modern
```

The command-line options still apply: `--style`, `--template`, `--font`, `--size`, `--margins`, `--toc`,
`--header`, `--footer`, `--page-numbers`, `--title` (shown as a title block unless the spec has one).
Relative image paths resolve against the spec file's folder, then the current folder.

## Top level

```json
{
  "properties": {"title": "Invoice 42", "author": "Billing", "subject": "…", "keywords": "…"},
  "page": {"size": "A4", "orientation": "portrait", "margins": "2cm"},
  "header": "ACME Corp||Invoice 42",
  "footer": {"text": "Page {page} of {pages}", "align": "center"},
  "blocks": [ … ]
}
```

A bare list of blocks is accepted too. `header`/`footer` use the syntax of `--header`: `{page}`, `{pages}`,
`{date}`, `{title}`, `{author}` become fields and `"left|center|right"` puts text at three positions (empty parts
allowed: `"||Page {page}"`, `"Company||Page {page} of {pages}"`). A landscape section gets its own copy of the
header and footer with the right-hand part at its own right margin.

`page` applies to the whole document; a `section` block starts from it and changes only what it names.
`properties` are metadata only: the author appears on the page only when a `title` block (or `--author`) says so.

## Blocks

| type | fields |
|---|---|
| `title` | `text`, `subtitle`, `author`, `date` |
| `heading` | `text` or `runs`, `level` (1-9), paragraph format keys |
| `paragraph` | `text` or `runs`, `style` (a paragraph style), and run keys for the whole paragraph (`bold`, `italic`, `color`, `size`, `font`…), paragraph format keys |
| `bullets` | `items`: strings or `{"text"/"runs", "items": [nested…]}` |
| `numbered` | `items` as above, `format` (`decimal`, `lowerLetter`, `upperLetter`, `lowerRoman`, `upperRoman`, `decimalZero`), `start` |
| `table` | `header` (list), `rows` (list of lists), `widths` (`["8cm","3cm","4cm"]`), `align` (per column: `left`, `center`, `right`), `style` (a table style), `caption` (above the table), `shading` (`{"header": "1F3864", "rows": ["FFFFFF", "F2F2F2"]}`), `font_size`, `merge` (`[{"from": [row, col], "to": [row, col]}]`, rows counted without the header), `bold_header` |
| `image` | `path`, `width` / `height` (one keeps the aspect; default natural size capped at the text width), `align` (default center), `caption`, `alt` |
| `quote` | `text` or `runs` |
| `code` | `text` (lines kept, monospace) |
| `markdown` | `text`: any Markdown (see markdown.md), converted with the document's styles |
| `toc` | `depth` (default 3), `title` (default "Contents"; `""` for none) |
| `page_break` | |
| `spacer` | `height` (default `12pt`) |
| `section` | starts a new section: `orientation`, `size`, `margins`, `columns`, `start` (`new_page` default, or `continuous`) |

Table cells are strings, numbers, or cell objects: `{"text": "Total", "bold": true, "fill": "F2F2F2", "align": "right"}`
(a cell object takes run keys such as `color`, `size`, `italic`, or `runs`). `header` entries can be cell objects
too: `{"text": "Amount (€)", "color": "FFE699", "align": "right"}`.

- A column whose values are all numbers (`2`, `"1,200.00"`, `"€ 45"`, `"12%"`, `"(3.5)"`) is right-aligned,
  header included, unless `align` says otherwise.
- Text on a dark fill (`shading.header`, a cell's `fill`) is white unless the cell or run sets a `color`.
- Without `widths` the table spans the text width with columns sized from their content; with `widths` it keeps
  them exactly.

Paragraph format keys: `align` (`left`, `center`, `right`, `justify`), `space_before`, `space_after`,
`line_spacing` (`1.15` or `14pt`), `indent_left`, `indent_right`, `first_line_indent`, `hanging_indent`,
`keep_with_next`, `keep_together`, `page_break_before`. Lengths: `12pt`, `1cm`, `0.5in`, `20mm`.

## Runs

A run is a string or an object:

| key | meaning |
|---|---|
| `text` | the text (`\n` is a line break, `\t` a tab) |
| `bold`, `italic`, `strike`, `caps`, `small_caps`, `superscript`, `subscript` | true/false |
| `underline` | true, or `double`, `dotted`, `wave`… |
| `color` | `RRGGBB` |
| `highlight` | `yellow`, `green`, `cyan`, `magenta`, `blue`, `red`, `darkBlue`, `lightGray`… |
| `shading` | background `RRGGBB` |
| `size` | points |
| `font` | font name |
| `style` | a character style (`Strong`, `Emphasis`, `Hyperlink`…) |
| `code` | monospace (the Verbatim Char style) |
| `link` | URL (or `#bookmark`) |
| `footnote` | footnote text attached after this run |
| `break` | `"page"` for a page break after the run, `"line"` for a line break |

## Example

```json
{
  "properties": {"title": "Invoice 42", "author": "Billing"},
  "page": {"size": "A4", "margins": "2cm"},
  "header": "ACME Corp||Invoice 42",
  "footer": "Page {page} of {pages}",
  "blocks": [
    {"type": "title", "text": "Invoice 42", "subtitle": "September 2026"},
    {"type": "heading", "text": "Summary", "level": 1},
    {"type": "paragraph", "runs": ["Amount due: ", {"text": "1,333.50 EUR", "bold": true, "color": "C00000"},
                                   {"text": " by 30 September.", "footnote": "Late fees apply after 30 days."}]},
    {"type": "bullets", "items": ["Fast", {"text": "Reliable", "items": ["99.9% uptime", "Support"]}]},
    {"type": "numbered", "items": ["First", "Second"], "format": "upperRoman"},
    {"type": "table", "header": ["Item", "Qty", "Amount"],
     "rows": [["Widget", 3, "1,234.50"], ["Gadget", 1, "99.00"], [{"text": "Total", "bold": true}, "", {"text": "1,333.50", "bold": true}]],
     "widths": ["8cm", "3cm", "4cm"], "caption": "Table 1: items", "merge": [{"from": [2, 0], "to": [2, 1]}]},
    {"type": "image", "path": "logo.png", "width": "5cm", "caption": "Figure 1: logo"},
    {"type": "markdown", "text": "Some **Markdown** with $x^2$ and a [link](https://example.com)."},
    {"type": "section", "orientation": "landscape"},
    {"type": "heading", "text": "Annex", "level": 1}
  ]
}
```
