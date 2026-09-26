---
name: spreadsheets
description: Read, analyse, create, edit, recalculate, render and convert spreadsheets (.xlsx .xlsm .xltx .xltm .xls .xlsb .ods .csv .tsv). Use for any Excel or LibreOffice Calc task, and for a CSV wanted as a workbook (to query, clean or convert CSV, use data-files). Summarise a workbook; read ranges with formulas, number formats and styles; query one or more files with SQL. Build a styled workbook with tables, totals, formulas, conditional formats, validations and charts from CSV, JSON, Markdown or a spec. Edit cells, rows, columns, sheets, styles, names, tables and charts while formulas, other sheets and macros stay intact. Recalculate with a built-in Excel-compatible engine (about 400 functions, dynamic arrays, LET) and find errors, cycles and stale values. Render a sheet to PNG to look at. Convert between xlsx, xls, ods, csv, json, Markdown, HTML, Parquet and PDF. Big files (hundreds of thousands of rows) are read as a map, searched by cell address, queried with SQL from a cached copy and edited in place.
license: MIT
---

# Spreadsheets

Scripts in `scripts/` use openpyxl, python-calamine, DuckDB, Pillow and pypdfium2, plus a built-in formula engine.
Desk sets up their Python environment, so run them with `python3`. Every script has `--help`, prints Markdown by
default and JSON with `--format json`, and never changes its input: outputs go to a new path, and an existing file
is only replaced with `--force`. LibreOffice is optional: it adds .xls writing, exact print layout and a second
calculation engine. Everything else works without it.

Work in a loop: **look** at the workbook, **act** on it, then **check** the result by recalculating, reading it back
and rendering it.

## Look

```bash
python3 scripts/sheet_info.py budget.xlsx                    # sheets, used ranges, tables, names, charts, formulas, errors
python3 scripts/sheet_read.py budget.xlsx                     # first sheet as a Markdown grid (row numbers, column letters)
python3 scripts/sheet_read.py budget.xlsx --sheet Q3 --range A1:H40 --formulas   # values plus the formulas behind them
python3 scripts/sheet_render.py budget.xlsx --sheet Q3        # PNG of the sheet as a spreadsheet window shows it
```

Then look at the PNG with view_image: colours, merged headers, charts and layout show there, not in text.

- `sheet_info` lists what matters before editing: macros, external links, pivot tables, frozen panes, formulas
  without stored results, error cells, functions the engine does not know, and features an edit would drop.
- `sheet_read` detects the header row (`--header yes|no` to force), shows values as Excel displays them with
  `--display` ($1,234.50, 12%, dates), and lists fonts, fills, borders and number formats with `--styles`.
  `--formulas` lists every formula cell of the range (the whole sheet by default), even outside the data block.
  Formulas saved without results (files written by libraries) read blank: the output counts them and gives the
  `sheet_recalc.py … --out` command that stores them. `--format csv|json` for data. Output is capped
  (`--max-chars`, default 60 000) at a whole row and ends with the exact command for the next part.
- Big sheets read as a map first; see "Big files" below.

## Big files

Over 5 000 rows, `sheet_read` prints a **map** instead of rows: the data block and header row, each column's type,
empty share and range, the first and last rows, rows left out (totals, notes) and stray cells far from the data.
Work from the map, then **find**, then **drill down**; never page through every row:

```bash
python3 scripts/sheet_read.py orders.xlsx --map                      # every sheet's map (automatic when big)
python3 scripts/sheet_read.py orders.xlsx --find "ACME"              # cell addresses (C150231) with their rows
python3 scripts/sheet_read.py orders.xlsx --grep "^INV-\d{6}$"       # the same with a regular expression
python3 scripts/sheet_read.py orders.xlsx --rows 150200-150260 --format csv   # sheet rows by number
python3 scripts/sheet_query.py orders.xlsx --sql "SELECT Region, SUM(Revenue) FROM orders GROUP BY 1"
```

- The first call on a big file builds a typed copy of each sheet in Desk's file cache (about 6 s for 200 000 rows ×
  30 columns); later reads, finds and queries on the same file take a fraction of a second. Results of
  `sheet_recalc` and `sheet_render` are cached the same way. `--no-cache` recomputes. Timings:
  `references/performance.md`.
- In SQL, `_row` is each record's sheet row: `WHERE _row BETWEEN 1000 AND 1100`, or report addresses from it.
- Cell edits (`set`, `fill`, `clear`, `style`) on a big workbook are patched into the file in about a second plus
  the recalculation. Structural edits (rows, columns, sheets, charts…) reload the whole workbook: on a very big
  one `sheet_edit` stops and states the time and memory first; `--mode openpyxl` goes ahead.

## Act

### Ask questions with SQL

```bash
python3 scripts/sheet_query.py sales.xlsx --tables                                   # table names and column types
python3 scripts/sheet_query.py sales.xlsx --sql "SELECT region, SUM(revenue) AS total FROM orders GROUP BY 1 ORDER BY 2 DESC"
python3 scripts/sheet_query.py q1.xlsx q2.xlsx targets.csv --sql "SELECT * FROM q1_orders UNION ALL SELECT * FROM q2_orders" --out all.xlsx
python3 scripts/sheet_query.py data.xlsx --range "Raw!A3:H500" --sql "SELECT COUNT(*) FROM raw"
```

Each sheet is a DuckDB table named after it (lowercase, `_` for other characters; `"Sheet 1"` in quotes works too),
and so is each Excel table (`SalesTbl` → `salestbl`). With several files the names get the file name as a prefix.
CSV, JSON and Parquet files are tables named after the file. Title rows above the header and Excel table totals
rows are left out (`--tables` says which; `--keep-totals` keeps them); use `--range` for an unusual layout. Quote
column names with spaces, capitals or SQL keywords: `"Unit price"`, `"When"`. `--out` writes csv, tsv, json,
jsonl, parquet or a styled xlsx.

### Create a workbook

```bash
python3 scripts/sheet_create.py sales.xlsx --from sales.csv --table --totals --chart column
python3 scripts/sheet_create.py report.xlsx --from summary.md                  # every Markdown table becomes a sheet
python3 scripts/sheet_create.py model.xlsx --spec model.json                   # full control: see references/spec.md
```

`--from` reads CSV/TSV (delimiter, encoding and decimal commas detected), JSON records and Markdown tables, types the
values (numbers, dates, 12%, $9.50, a column of codes like 007 kept as text) and writes a styled header, frozen
first row, filter and fitted column widths. Text starting with `=` stays text (a data file's cell is never run as
a formula); `--allow-formulas` when the file is trusted. A spec gives columns with formats and `{row}` formulas, totals, Excel tables,
conditional formats, validations, charts, merged cells, defined names and any edit operation:

```json
{"sheets": [{"name": "Q1",
  "columns": [{"header": "Item"}, {"header": "Qty", "format": "#,##0"}, {"header": "Price", "format": "$#,##0.00"},
              {"header": "Total", "formula": "=B{row}*C{row}", "format": "$#,##0.00", "total": "sum"}],
  "rows": [["Pens", 10, 1.5], ["Paper", 4, 6]],
  "conditional": [{"column": "Total", "type": "data_bar"}],
  "charts": [{"type": "column", "categories": "Item", "values": ["Total"], "title": "Totals"}]}]}
```

Every formula is checked, stored the way Excel expects (newer functions get their `_xlfn.` prefixes, `A1#` and `@`
their file forms) and calculated, so the file opens with its numbers in any reader.

### Edit a workbook

Write the operations as a JSON list and apply them to a new file:

```bash
python3 scripts/sheet_edit.py budget.xlsx --out budget-v2.xlsx --ops '[
  {"op": "insert_rows", "sheet": "Data", "at": 5, "count": 2},
  {"op": "set", "range": "Data!A5", "values": [["West", 12, 3.5], ["East", 8, 4]]},
  {"op": "fill", "range": "Data!D2:D40", "formula": "=B2*C2"},
  {"op": "style", "range": "Data!A1:D1", "font": {"bold": true}, "fill": "#DDEBF7", "border": {"bottom": "medium"}},
  {"op": "conditional_format", "range": "Data!D2:D40", "type": "color_scale"},
  {"op": "chart", "sheet": "Data", "type": "line", "values": "D1:D40", "categories": "A2:A40", "anchor": "F2"}]'
python3 scripts/sheet_edit.py model.xlsm --out model-v2.xlsm --ops edits.json          # macros kept
python3 scripts/sheet_edit.py dashboard.xlsx --out d2.xlsx --mode patch --ops '[{"op":"set","cell":"Inputs!C4","value":0.07}]'
```

- Inserting or deleting rows and columns rewrites every reference to them: formulas on all sheets, defined names,
  merged cells, conditional formats, validations, tables, charts, print areas and hyperlinks. Renaming a sheet
  updates every formula that points at it.
- Operations: set, fill, clear, copy_range, move_range, sort, find_replace, insert/delete rows and columns,
  add/rename/copy/delete/move sheets, style, column_width, row_height, autofit, hide, group, merge, freeze,
  autofilter, conditional_format, validation, name, table, chart, image, comment, hyperlink, protect, print,
  properties, calc. Fields and examples: `references/ops.md`.
- Values: `"=…"` strings are formulas (write text that starts with `=` with `"text": true`), ISO strings become dates,
  `"values"` writes a block. `fill` with a formula shifts its relative references like Excel; `fill` alone copies
  each column's top cell down the range (Ctrl+D), or the first cell across a one-row range.
- After the edits the workbook is recalculated and saved with its results; the output lists any errors.
- Two ways to write: `set`, `fill`, `clear` and `style` are patched straight into the sheet XML when the file is
  3 MB or more or holds parts openpyxl would drop (shapes, sparklines, slicers, form controls, x14 rules, chart
  styles), keeping everything else byte for byte. Everything else goes through openpyxl, which drops those parts
  and lists them in its warnings. Pivot tables are kept, not refreshed: renaming their source sheet updates them,
  and the output warns when data now runs past a pivot's source range. Details: `references/ops.md`.

### Recalculate and check formulas

```bash
python3 scripts/sheet_recalc.py model.xlsx --check                   # errors, cycles, unknown functions; writes nothing
python3 scripts/sheet_recalc.py model.xlsx --check --compare         # computed results against the stored ones
python3 scripts/sheet_recalc.py model.xlsx --out model-calc.xlsx     # store fresh results (files written by libraries have none)
python3 scripts/sheet_recalc.py model.xlsx --out out.xlsx --engine libreoffice   # a second opinion, when installed
python3 scripts/sheet_recalc.py model.xlsx --check --engine libreoffice          # LibreOffice's errors; writes nothing
```

The built-in engine follows Excel: implicit intersection for plain formulas, arrays for array and dynamic formulas
(spills, `#SPILL!`), 1900/1904 dates, 15-digit precision, iterative calculation when the workbook enables it, and
about 400 functions (list and semantics: `references/formulas.md`). A function it does not know keeps its stored
result and is reported by name and cell; nothing is guessed. `--now 2025-01-31` fixes TODAY() and NOW() (built-in
engine only). The report names the engine that ran: with `--engine libreoffice` (or `auto` on an unknown
function), LibreOffice recalculates a temporary copy for `--check` as well as for `--out`.

### Convert

```bash
python3 scripts/sheet_convert.py book.xlsx --out data.csv --sheet Orders
python3 scripts/sheet_convert.py book.xlsx --to csv --out-dir csv/            # one file per sheet
python3 scripts/sheet_convert.py book.xlsx --out book.json                    # {sheet: [records]}
python3 scripts/sheet_convert.py book.xlsx --out book.md --display            # Markdown with Excel's number formats
python3 scripts/sheet_convert.py legacy.xls --out legacy.xlsx                 # formulas and formatting with LibreOffice
python3 scripts/sheet_convert.py book.xlsx --out book.ods                     # built-in writer keeps formulas
python3 scripts/sheet_convert.py book.xlsx --out book.pdf --sheet Summary     # print layout (LibreOffice) or rendered pages
```

Without LibreOffice: .xls/.xlsb become .xlsx with values only (the output says so). .ods → .xlsx keeps formulas,
names, number formats, fonts, fills, borders, alignment, sizes and merges, but not conditional formats, charts or
images. .xlsx → .ods keeps values, formulas (table references become ranges), number formats, bold/italic, fills,
merges and widths, and lists what it left out (tables, conditional formats, validations, charts). PDF pages are
A4 pictures drawn by the built-in renderer (no selectable text, no print areas or headers). Details:
`references/formats.md`. Data exports (csv, json, parquet…) leave out totals rows and titles and say so.

## Check

1. Recalculate or check what you made: `sheet_recalc.py out.xlsx --check`. Fix every error it lists unless the
   user expects it (a `#N/A` for a missing lookup can be intended; say so).
2. Read it back: `sheet_read.py out.xlsx --formulas` for the numbers and the formulas behind them; compare them with
   what was asked. For edits, check that totals and cross-sheet references still point where they should.
3. Render and look: `sheet_render.py out.xlsx --sheet Summary`, then view_image. Look for `####` (column too
   narrow), clipped headers, wrong number formats, charts over data, missing colours.
4. Fix and repeat. Then tell the user where the file is, what changed, and anything you could not do.

`sheet_render` draws column letters, row numbers, gridlines, fills, fonts, borders, alignment, wrapped text,
number formats, merges, frozen panes, conditional formats, images and charts (drawn from their data, legend where
the chart puts it), splitting big ranges into several images no larger than 1568 px. A split never cuts a chart
that fits in one image; a bigger one is drawn in part on each image it spans. The output names the image that
holds each chart. PNGs are named after their sheets, cleaned up into safe file names. `--range`, `--max-rows`,
`--all`, `--formulas` (show the formula text), `--engine libreoffice` for the printed page layout.

## Rules

- Never modify the user's file. Write outputs in your workspace, or where the user asked, and say where they are.
- Keep what is there: edit with `sheet_edit` operations instead of rebuilding a workbook from values; keep formulas
  as formulas (do not paste computed numbers over them) unless asked.
- Put inputs in cells and refer to them; name important ones (`name` op) so formulas stay readable.
- Report what could not be done instead of guessing: unknown functions, external links that cannot be refreshed,
  pivot tables that were not recomputed, features an edit would drop.
- Say which engine produced a result or picture when it matters (built-in or LibreOffice).
- Before sharing a workbook, consider hidden sheets, comments and document properties; ask the user.

## Limits

- The engine does not run LAMBDA and its helpers (MAP, REDUCE, SCAN, BYROW, MAKEARRAY…), GROUPBY/PIVOTBY, cube,
  web and bond-pricing functions, or data tables (what-if); their stored results are kept and reported.
  `--engine libreoffice` covers some of them.
- Pivot tables, slicers and external links are kept but not refreshed. Macros (.xlsm) are kept, never run.
- Charts are drawn from their data in a simple style (column, bar, line, area, pie, doughnut, scatter); other chart
  types are listed. Fonts that are not installed are replaced by metric-compatible ones (the output says which).
- .xls and .xlsb are read directly; writing them, and keeping their formulas when converting, needs LibreOffice.
- Password-protected (encrypted) files cannot be opened; ask for an unprotected copy. Sheet protection is kept.
- Zip-based files that would inflate past safe limits (a possible zip bomb) are refused; the error names the
  `DESK_ZIP_*` variable to raise for a trusted file.

## Beyond the scripts

For anything else, write a small Python script with openpyxl and this skill's helpers (formula engine, A1
utilities, number formats; see `references/python.md`) and run it with the same `python3`.

Neighbouring skills: `data-files` for general data wrangling (JSON, XML, Parquet at scale) and for querying, cleaning
or converting a CSV (this skill takes a CSV when the result is a workbook), `pdf-toolkit` for tables inside PDFs,
`word-documents` and `presentations` for reports and decks built from the numbers, `images` for pictures to place in
a sheet, `file-inspector` for unknown or damaged files.

References: `references/ops.md` (every edit operation, patch mode), `references/spec.md` (sheet_create specs),
`references/formulas.md` (the engine and its functions), `references/formats.md` (reading, converting,
rendering), `references/performance.md` (big files, caching, timings), `references/python.md` (custom scripts).
