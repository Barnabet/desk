# docx_edit.py operations

`docx_edit.py IN OUT --ops OPS` applies a JSON list of operations in order and writes a new file. `OPS` is inline
JSON, a `.json` file, or `-` for stdin. Nothing is written when any operation fails; the error names the operation
(`op 3 (replace): … Nothing was written.`).

```bash
python3 scripts/docx_edit.py contract.docx contract-v2.docx --ops edits.json
python3 scripts/docx_edit.py contract.docx contract-v2.docx --ops edits.json --track --author "Desk"
python3 scripts/docx_edit.py contract.docx --ops edits.json --dry-run        # counts only
```

## Addressing

- **Block indexes** (`index`, `indexes`, `range`, `after`, `before`, `to`) are the numbers `docx_read.py --indexes`
  or `--format json` prints for the **original** file. They stay valid whatever earlier operations inserted,
  deleted or moved, so read once, then write every operation against those numbers. A block is a top-level paragraph
  or table (paragraphs inside content controls count as blocks too).
- `range`: `"12"`, `"10-20"`, `"30-"` (0-based, inclusive).
- `find` (literal text) or `regex` (Python syntax) instead of an index: the operation applies to the blocks whose
  text contains it. `"occurrence": 2` picks the second such block; `"first": true` the first one.
- Literal `find` tolerates curly vs straight quotes, non-breaking spaces and hyphen variants. `match_case` (default
  true) and `whole_word` (default false) refine it.
- Tables: `"table": 0` is the first top-level table (reading order); `"index": 14` also works when block 14 is a
  table. Rows and columns count from 0 and include the header row. Merged cells address the top-left cell.
- Every operation reports a count. An operation that changes nothing fails, unless it has `"optional": true`.
- `"track": true` on one operation records it as a tracked change (the same as `--track` for all of them).

## Text

| op | fields | notes |
|---|---|---|
| `replace` | `find` or `regex`, `replace` (`""` deletes), `match_case`, `whole_word`, `first`, `count`, `scope`, `blocks` | Works across runs. The replacement takes the formatting of the first character matched. With `regex`, `$1`, `\1` or `${name}` insert groups. |
| `format` | `find`/`regex` (just the matched text) or `index`/`indexes`/`range` (whole blocks); `bold`, `italic`, `underline` (true or `double`, `wavy`…), `strike`, `color` (`RRGGBB`), `highlight` (`yellow`, `green`, `cyan`…), `size` (pt), `font`, `caps`, `small_caps`, `superscript`, `subscript`, `hidden`, `shading` (`RRGGBB`), `style` (a character style) | Splits runs at the match edges so only the match changes. |
| `set_text` | `index` (or `find`), `text` or `markdown` | Replaces the whole paragraph's text, keeping its style and the first run's formatting. `markdown` swaps the paragraph for new Markdown blocks. |
| `add_comment` | `find`/`regex` (anchor on the match; `occurrence`: n or `"all"`) or `index`; `text`, `author`, `initials` | A real Word comment anchored on that text. |
| `remove_comments` | optional `author` | Removes comments and their anchors. |

`scope` for `replace` and `format`: `body`, `tables`, `textboxes`, `headers`, `footers`, `footnotes`, `endnotes`,
`comments`, `all`, or a list. The default is everything except comments. `blocks: "10-20"` limits the operation to
those body blocks.

## Blocks

| op | fields | notes |
|---|---|---|
| `insert_after` / `insert_before` | anchor: `after`/`before`/`index` (block) or `find`; content: `markdown`, or `text` (paragraphs split on blank lines) with optional `style`, or `docx` (a file whose body is inserted); `like` | Several inserts after the same block keep their order. See "How inserted text looks" below. |
| `append` / `prepend` | `markdown`, `text`+`style`, or `docx` | At the end (before the final section properties) or at the start. |
| `delete` | `index`, `indexes`, `range` or `find` | Deleting a paragraph that ends a section keeps the section break. Tracked with `--track`. |
| `move` | `index`, `to`, `position` (`after` default, or `before`) | Tracked as a deletion here and an insertion there. |
| `set_style` | `index`/`indexes`/`range`/`find`; `style` (paragraph style name or id; a table style for tables) and paragraph format keys | Format keys: `align` (`left`, `center`, `right`, `justify`), `space_before`, `space_after`, `line_spacing` (a multiple like `1.15`, or `14pt` exact), `indent_left`, `indent_right`, `first_line_indent`, `hanging_indent`, `keep_with_next`, `keep_together`, `page_break_before`. Lengths: `12pt`, `1cm`, `0.5in`, `20mm`. |
| `page_break` | `after` or `before` (block), or `find` | |

### How inserted text looks

- `text` copies the anchor paragraph: its style, paragraph settings and the first run's formatting (font, size,
  colour). `style` picks a paragraph style instead.
- `markdown` body paragraphs also take the anchor's look when the anchor is ordinary body text: its paragraph style
  and settings, and its font, size and colour wherever the Markdown did not set them (`**bold**` and `*italic*`
  stay). Headings, lists, tables, quotes and code keep the document's own styles (`Heading 2`, `List Paragraph`…).
  `"like": false` turns this off: the Markdown then uses the document's Normal style. Tables inserted from Markdown
  span the section's text width.

## Tables

| op | fields |
|---|---|
| `table_set_cell` | `table`, `row`, `col`, `text` (or `value`); optional `bold`, `italic`, `color`, `highlight`, `size`, `font`, `fill` (cell shading `RRGGBB`) |
| `table_add_row` | `table`, `cells` (list of texts), `after` (row, default last) or `before`, `copy_from` (row whose formatting the new row copies) |
| `table_delete_row` | `table`, `row` or `rows` |
| `table_add_column` | `table`, `cells` (one text per row, top to bottom), `after` (column, default last), `width` |
| `table_delete_column` | `table`, `col` |
| `table_merge` | `table`, `from: [row, col]`, `to: [row, col]` (a rectangle) |
| `table_style` | `table`, `style` (a table style in the document), `align` (`left`, `center`, `right`), `borders` (`all`, `outer`, `horizontal`, `none`, `style`), `header_row` (true: repeat row 0 on each page) |

A new row copies the formatting of its neighbour (or `copy_from`), without header marks or vertical merges.

## Images

| op | fields |
|---|---|
| `insert_image` | `path`; where: `after`/`before`/`index` (a new centred paragraph), or `find` (the matched placeholder text is replaced by the picture, inline); `width`/`height` (one keeps the aspect ratio; default: natural size, capped at the text width), `align`, `caption`, `alt` |
| `replace_image` | `image` (1-based, reading order of body pictures), `path`, `keep` (`width` default: the new picture keeps the old width, height follows its aspect; `size`: keep both) |

## Tracked changes, headers, layout, properties

| op | fields | notes |
|---|---|---|
| `accept_changes` / `reject_changes` | optional `author` | Insertions, deletions, moves, formatting and paragraph-mark changes, table rows. |
| `track_changes` | `on` (default true) | Turns Word's Track Changes on for later editing. |
| `set_header` / `set_footer` | `text`, `align`, `section` (1-based, list, or `all`), `kind` (`default`, `first`, `even`) | `{page}`, `{pages}`, `{date}`, `{title}`, `{author}`, `{section}`, `{filename}` become fields; `"left|center|right"` places three parts on tab stops (only the parts with text get a tab: `"Company||Page {page}"`). Without `section`, section 1 holds the text and the others inherit it, except sections of another width (landscape), which get their own copy with tab stops at their own margins. |
| `page_setup` | `size` (`A4`, `Letter`, `Legal`, `A3`, `A5`, `21x29.7cm`), `orientation`, `margins` (`2cm`, `2cm,3cm`, or top,right,bottom,left), `columns`, `header_distance`, `footer_distance`, `section` | |
| `properties` | `title`, `subject`, `author`, `keywords`, `comments`, `category`, `last_modified_by`, `content_status`, `language`, `version`, `identifier`, `custom` (object of custom properties) | |
| `remove_personal_info` | optional `author` (placeholder, default `Author`) | Clears author and last-modified-by, renames revision and comment authors, removes rsids and company/manager, and sets Word's "remove personal information on save". |
| `update_fields` | | Asks Word to refresh the TOC, page numbers and cross-references when the file is next opened. |
| `append_docx` | `path`, `page_break` (default true) | Appends another document with its styles, numbering, images and footnotes (docxcompose). |

Aliases: `accept_all`, `reject_all`, `set_cell`, `comment`, `header`, `footer`, `insert`, `append_document`,
`merge_cells`.

## Tracked mode

With `--track` (or `"track": true`), edits become real revisions Word can accept or reject, and the report marks
each operation `(tracked)` or `(not tracked: …)`.

- **Recorded as revisions:** `replace`, `set_text` (old text deleted, new inserted); `insert_after`,
  `insert_before`, `append`, `prepend`, `insert_image`, `page_break`, `append_docx`, `table_add_row` (marked
  inserted); `delete`, `table_delete_row` (marked deleted); `move` (deleted here, inserted there);
  `table_set_cell`; `format` (old run properties kept); `set_style` (old paragraph properties kept);
  `set_header` / `set_footer` (old header text deleted, new inserted, inside the header).
- **Not revisions** (Word cannot record them; `reject_changes` does not undo them): `page_setup`, `properties`,
  `table_add_column`, `table_delete_column`, `table_merge`, `table_style`, `replace_image`. Do these in a separate
  pass without `--track` if the reviewer must see only revisions, and say so to the user.
- Comments, `accept_changes`, `reject_changes`, `remove_personal_info` and `update_fields` are not edits of the text.

After accepting or rejecting, a table whose rows were all removed is removed too (Word calls an empty table
unreadable content). Check the result with `docx_read.py` (CriticMarkup `{--old--}{++new++}`) and
`docx_render.py` (Word-like markup).

`regex` uses Python syntax; a search that runs over 3 seconds on one paragraph (a pattern like `(a|aa)+$`) stops
the edit with an error and nothing is written.

## Worked example

```json
[
  {"op": "replace", "find": "ACME Ltd", "replace": "Acme Limited"},
  {"op": "replace", "regex": "(\\d+) days", "replace": "$1 business days", "scope": "body"},
  {"op": "format", "find": "Acme Limited", "bold": true, "first": true},
  {"op": "insert_after", "index": 12, "markdown": "### 4.2 Review\n\nThe parties review the terms **annually**."},
  {"op": "delete", "range": "30-32"},
  {"op": "table_add_row", "table": 0, "cells": ["Support", "12", "1,200.00"]},
  {"op": "set_footer", "text": "Acme Limited||Page {page} of {pages}"},
  {"op": "add_comment", "find": "annually", "text": "Confirm with Legal", "author": "Desk"},
  {"op": "properties", "title": "Services agreement v2"},
  {"op": "replace", "find": "DRAFT", "replace": "", "optional": true}
]
```
