# sheet_edit operations

`python3 scripts/sheet_edit.py in.xlsx --out out.xlsx --ops OPS` where OPS is a JSON list (inline, a `.json` file,
or `-` for stdin). Operations run in order; each one sees the result of the previous ones. Every operation has
`"op"`; most take a place:

- `"range": "B2:D9"` (or `"cell": "B2"`), optionally with a sheet: `"range": "'Q1 2025'!B2:D9"`.
- `"sheet": "Data"` when the place has no sheet. Without either, `--sheet` or the active sheet is used.
- Whole columns and rows work where it makes sense: `"A:C"`, `"5:9"`.

Colours are `#RRGGBB` or a name (red, green, blue, yellow, orange, gray, lightgray, lightblue, lightgreen,
lightred, lightyellow, darkblue, navy, purple, black, white). Aliases are accepted (`insert_row`, `set_formula`,
`add_chart`, `freeze_panes`, `data_validation`…), and an unknown op fails the whole edit with the list of known ones.

After the operations the workbook is saved and **recalculated**: every formula gets a stored result, new formulas
that only work as arrays in Excel 365 are stored as array formulas, and the report lists errors by cell.
`--no-recalc` skips that (Excel then recalculates on open). `--now` fixes TODAY()/NOW().

## Cells

| op | fields | notes |
|---|---|---|
| `set` | `range`/`cell`, one of `value`, `formula`, `values` (2-D list; a flat list is a row, or a column with `"down": true`), `number_format`, `style`, `text` | Strings starting with `=` are formulas; ISO dates (`2025-03-31`, `2025-03-31 14:30`) become dates with a date format; `{"date": "2025-03-31", "format": "d mmm yyyy"}` or `{"value": 0.2, "format": "0%"}` for per-cell formats; `"text": true` writes strings as they are (text starting with `=`). A single `value`/`formula` over a range fills it, shifting relative references. |
| `fill` | `range`, then `formula`, `value`, `series` `{"start": 1, "step": 1}`, or nothing (copies the first cell); `copy_style` (default true) | Like dragging the fill handle: `=B2*C2` becomes `=B3*C3`… `$` references stay. |
| `clear` | `range`, `what`: `contents` (default), `formats`, `all` | |
| `copy_range` | `from`, `to` (top-left cell, may name another sheet), `styles` (default true) | Formulas are translated to the new place. |
| `move_range` | `range`, `rows`, `cols` | References to the moved cells follow them. |
| `sort` | `range`, `by`: column letter, or `[{"column": "C", "order": "desc"}, …]`, `header` (true: first row stays) | Formulas move with their rows; blanks sort last like Excel. |
| `find_replace` | `find`, `replace`, `range` or `sheet` (default all sheets), `in`: `values` (default), `formulas`, `all`; `match_case`, `whole_cell`, `regex` (with `\1`) | Reports how many cells changed. |

## Rows, columns and sheets

| op | fields | notes |
|---|---|---|
| `insert_rows` / `delete_rows` | `sheet`, `at` + `count`, or `rows: "5:9"` | Every reference is rewritten: formulas on all sheets, defined names, merged cells, conditional formats and their formulas, validations, tables (they grow or shrink), charts, images, print areas, hyperlinks, autofilters, row heights and column widths. References to deleted cells become `#REF!` like Excel. |
| `insert_cols` / `delete_cols` | `sheet`, `at` (letter or number) + `count`, or `columns: "C:E"` | Same rewriting; tables gain or lose columns. |
| `add_sheet` | `name`, `index` (1-based) | |
| `rename_sheet` | `sheet`, `to` | Every formula, name, chart and validation that refers to the sheet is updated (quotes added when needed). |
| `copy_sheet` | `sheet`, `to` (new name), `index` | Values, styles, merges, validations and formats are copied; charts are not (add them with `chart`). |
| `delete_sheet` | `sheet` | Formulas that used it become `#REF!` and are reported. |
| `move_sheet` | `sheet`, `index` | |
| `reorder` | `order`: [names] | Sheets not listed keep their order after these. |
| `sheet_state` | `sheet`, `state`: `visible`, `hidden`, `veryHidden` | |
| `tab_color` | `sheet`, `color` | |

## Formatting

| op | fields | notes |
|---|---|---|
| `style` | `range`, any of `font` `{bold, italic, underline, strike, size, name, color}`, shortcuts `bold`/`italic`/`color`/`size`, `fill` (colour or `none`), `border`, `align` `{horizontal, vertical, wrap, indent, rotation, shrink}`, `wrap`, `number_format` (or `format`), `locked` | `border`: a style for every edge (`"thin"`), or `{"all": …, "outline": …, "inner": …, "top"/"bottom"/"left"/"right": …}`; each value is `thin`, `medium`, `thick`, `dashed`, `dotted`, `double`, `hair`… optionally with a colour: `"medium #1F4E78"`. |
| `column_width` | `columns` (`"A"`, `"B:D"`), `width` (characters) or `widths` [list], `hidden` | |
| `row_height` | `rows` (`"1"`, `"2:10"`), `height` (points) | |
| `autofit` | `columns` (default all), `min`, `max` | Widths from the displayed text (numbers as formatted, formulas after calculation). |
| `hide_columns` / `hide_rows` | `columns` / `rows`, `hidden` (default true) | |
| `group` | `rows` or `columns`, `level`, `collapsed` | Outline groups. |
| `merge` / `unmerge` | `range`; merge also `horizontal`, `vertical` (default centered) | Only the top-left value is kept by Excel: put the text there. |
| `freeze` / `unfreeze` | `cell` (`"B2"` freezes row 1 and column A) | |
| `autofilter` | `range` | |

Number formats are Excel codes: `#,##0.00`, `0%`, `0.0%`, `$#,##0;[Red]-$#,##0`, `€#,##0.00`, `yyyy-mm-dd`,
`d mmm yyyy`, `hh:mm`, `[h]:mm`, `0.00E+00`, `# ?/?`, `@` (text), `"Q"0` (literal text).

## Rules, names and tables

| op | fields | notes |
|---|---|---|
| `conditional_format` | `range`, `type`, style (`fill`, `color`, `bold`, `font`; default light red fill, dark red text), `stop` | Types: `cell` (`operator` `>`, `>=`, `<`, `<=`, `=`, `<>`, `between`, `notBetween`; `value` or `values`; a value starting with `=` is a formula), `formula` (`"formula": "$C2>100"`, relative to the range's first cell), `color_scale` (`colors`: 2 or 3), `data_bar` (`color`), `icon_set` (`icons`: `3TrafficLights1`, `3Arrows`, `5Rating`…, `reverse`), `top`/`bottom` (`rank`, `percent`), `above_average`/`below_average`, `duplicates`, `unique`, `text` (`text`: contains), `blanks`, `errors`. |
| `validation` | `range`, `type`: `list` (`values` [..] or `source` `"$H$1:$H$9"`), `whole`, `decimal`, `date`, `time`, `text_length` (`operator` + `value`, or `min`/`max` for between), `custom` (`formula`); `prompt`, `prompt_title`, `error`, `error_title`, `error_style` (`stop`, `warning`, `information`), `allow_blank` | |
| `name` | `name`, `ref` (`"Inputs!$B$2"`, a range, or a formula like `"=Inputs!$B$2*12"`), `scope` (a sheet, for a local name) | Formulas can then use the name: `=Price*Qty`. |
| `delete_name` | `name` | |
| `table` | `range` (header row included), `name`, `style` (default `TableStyleMedium2`), `banded_rows`, `banded_columns`, `totals` (`{"Revenue": "sum", "Qty": "average"}`: sum, average, count, max, min, stdev, var) | Blank or duplicate headers are fixed. Formulas can use structured references: `=SUM(Sales[Revenue])`, `=[@Qty]*[@Price]`. |

## Objects

| op | fields | notes |
|---|---|---|
| `chart` | `type` (`column`, `bar`, `line`, `area`, `pie`, `doughnut`, `radar`, `scatter`), `values` (a range with the header cell first, or a list of ranges), `categories` (labels, or the x values for scatter), `anchor` (top-left cell), `title`, `x_title`, `y_title`, `legend` (`right`, `bottom`, `top`, `left`, `none`), `data_labels` (true or `"percent"` for pies), `stacked` (true or `"percent"`), `width`/`height` (cm, default 16×8), `marker`, `lines` (scatter), `style`, `data_area` (then `values`/`categories` may be header names) | The chart reads the cells, so it follows later edits. |
| `image` | `cell`, `path` (PNG/JPEG), `width` and/or `height` (pixels, aspect kept) | |
| `comment` | `cell`, `text` (empty removes), `author`, `width`, `height` | |
| `hyperlink` | `cell`, `url` or `location` (`"'Q1'!A1"`), `text`, `tooltip` | |

## Workbook

| op | fields | notes |
|---|---|---|
| `protect` / `unprotect` | `sheet`, `password`, `allow` [`sort`, `autoFilter`, `formatCells`, `insertRows`…], `unlock` (a range left editable) | Sheet protection guards against accidental edits, not attackers. |
| `print` | `sheet`, `orientation` (`portrait`/`landscape`), `paper` (`a4`, `letter`, `legal`, `a3`), `fit_width`/`fit_height` (pages), `print_area`, `repeat_rows` (`"1:2"`), `gridlines`, `header`, `footer` | Check with `sheet_render.py --engine libreoffice`. |
| `properties` | `title`, `subject`, `author`, `keywords`, `description`, `category` | |
| `calc` | `iterate`, `iterate_count`, `iterate_delta`, `full_calc_on_load`, `mode` (`auto`, `manual`) | Iteration lets intentional circular models converge. |

## Patch mode (surgical edits)

`set`, `fill`, `clear` and `style` can be written straight into the sheet XML: only the sheet parts that change
and `styles.xml` are rewritten, every other part of the zip is copied byte for byte (not even recompressed), so
shapes, sparklines, slicers, form controls, x14 extensions, pivot caches, custom XML and VBA are kept exactly.

- `--mode auto` (default) patches when every operation is one of those four and the file is 3 MB or bigger, or
  holds parts openpyxl would drop. A one-cell edit of a 40 MB, 200 000-row workbook takes about 1.3 s plus the
  recalculation (about 10 s there), instead of 45 s through openpyxl.
- Any other operation (insert/delete rows, sheets, charts, tables, rules…) loads and saves the whole workbook with
  openpyxl. On a big file the output says so, with an estimate ("the full openpyxl path: … about N s here"); group
  the cell edits in a separate call when you can.
- Styles: each cell's current style is combined with the requested font, fill, border, alignment, number format
  or protection into a new entry appended to `styles.xml` (identical fonts, fills, borders and formats are reused),
  so the rest of the cell's look stays. Dates get a date format when the cell has none.
- Text is written as inline strings, so the shared-string table never needs rewriting. A cell that carries the
  text of a shared formula gives the other cells of the group their own copy before it is overwritten.
- The calculation chain is dropped when values change (Excel rebuilds it), and the workbook is recalculated unless
  `--no-recalc` (then Excel recalculates on open).
- `--mode patch` forces it on a small file; it refuses other operations.

What the openpyxl mode drops, and says so in its warnings: shapes and text boxes, sparklines, slicers, timelines,
form controls, x14 conditional formats (newer data bars, icon sets) and data validations, modern charts (waterfall,
treemap…), chart style and colour parts. `sheet_info` lists them under "an edit would drop".

## Fill semantics

- `{"op": "fill", "range": "D2:D40", "formula": "=B2*C2"}` writes the formula in D2 and shifts its relative
  references down the range (`=B3*C3`…); `$` parts stay.
- Without `formula`/`value`, fill copies from the range's first row downwards, each column from its own top cell,
  like selecting the block and pressing Ctrl+D: `C2:D40` fills C from C2 and D from D2. A one-row range fills to
  the right from its first cell (Ctrl+R). Styles are copied with the values (`"copy_style": false` to keep the
  targets' styles). An empty top cell leaves its column alone and the output says so.

## Examples

```json
[
  {"op": "add_sheet", "name": "Summary", "index": 1},
  {"op": "set", "range": "Summary!A1", "values": [["Region", "Revenue"], ["North", "=SUMIFS(Data!D:D,Data!A:A,A2)"]]},
  {"op": "fill", "range": "Summary!B2:B5", "formula": "=SUMIFS(Data!$D:$D,Data!$A:$A,A2)"},
  {"op": "style", "range": "Summary!B2:B5", "number_format": "$#,##0"},
  {"op": "name", "name": "TaxRate", "ref": "Inputs!$B$2"},
  {"op": "set", "cell": "Summary!C2", "formula": "=B2*(1+TaxRate)"},
  {"op": "table", "range": "Data!A1:D200", "name": "Sales", "totals": {"Revenue": "sum"}},
  {"op": "conditional_format", "range": "Data!D2:D200", "type": "top", "rank": 10, "fill": "#C6EFCE"},
  {"op": "validation", "range": "Data!B2:B200", "type": "list", "values": ["Open", "Won", "Lost"]},
  {"op": "chart", "sheet": "Summary", "type": "pie", "values": "B1:B5", "categories": "A2:A5", "anchor": "E2", "data_labels": "percent"},
  {"op": "freeze", "sheet": "Data", "cell": "A2"},
  {"op": "print", "sheet": "Summary", "orientation": "landscape", "fit_width": 1}
]
```
