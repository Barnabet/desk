# Formats: reading, converting, rendering

## What each format keeps

| format | read | write | notes |
|---|---|---|---|
| .xlsx .xlsm .xltx .xltm | values, formulas, styles, names, tables, charts, comments, validations, conditional formats | everything this skill makes; edits keep the rest | .xlsm keeps its VBA project (never run). Templates (.xltx) are written with the template flag. |
| .xls (Excel 97-2003) | values, sheet names, merged cells (python-calamine) | only with LibreOffice | Formulas and formatting of .xls need LibreOffice to convert to .xlsx first; without it `sheet_convert` writes values and says so. |
| .xlsb (binary) | values (python-calamine) | no | Same as .xls: LibreOffice converts it with formulas. |
| .ods (LibreOffice Calc) | values (calamine), formulas and named ranges (OpenFormula, translated to Excel syntax), cell formatting (number formats, fonts, fills, borders, alignment, widths, heights, merges) | built-in writer: values, formulas with results (structured references rewritten as ranges), number formats, bold/italic, fills, alignment, merges, widths, names | Without LibreOffice, conditional formats, charts, images and Excel tables are not carried across, and the output lists what was left out. LibreOffice, when installed, converts everything it supports. |
| .csv .tsv .txt | delimiter (`,` `;` TAB `|`), encoding (UTF-8 with or without BOM, UTF-16, Windows-1252), decimal commas, types | UTF-8 (`--bom` for Excel on Windows), any `--delimiter` | A column with a code like `007` stays text as a whole (so `123` in it is text too); `12%`, `$9.50`, `($150.25)`, ISO dates are typed, the same way in `sheet_read` and `sheet_query`. Text starting with `=` is written to workbooks as text, never as a formula (formula injection); `sheet_create --allow-formulas` when the file is trusted. |
| .json .jsonl | records → table | records per sheet (`{sheet: [records]}` for several) | Header detection picks the keys (`--header yes|no`). |
| .md | pipe tables | one table per sheet | `--display` writes numbers as formatted in Excel. |
| .html | — | one table per sheet | |
| .parquet | via `sheet_query` | yes | |
| .pdf | — | LibreOffice print layout, or A4 pages drawn by the built-in renderer | Use `--sheet`/`--range` for one area. See below for the built-in pages. |

Encrypted (password-protected) workbooks cannot be read; the error says so. Files with a wrong extension are
detected by their content (an .xls that is really HTML or CSV, a Parquet file, an .xlsx that is really a zip of
something else); an empty or binary file with a spreadsheet extension is refused instead of being read as text.

Zip-based files (.xlsx family, .ods) are checked before anything inflates them: at most 4096 MB in all
(`DESK_ZIP_MAX_MB`), 2048 MB per part (`DESK_ZIP_MEMBER_MAX_MB`), a compression ratio of 250 for parts over 16 MB
(`DESK_ZIP_MAX_RATIO`) and 100 000 parts (`DESK_ZIP_MAX_MEMBERS`). A file over a limit is refused as a possible zip
bomb; raise the variable only for a file you trust.

## sheet_convert

```
sheet_convert.py FILE --out OUT [--sheet S] [--range A1:F200] [--header auto|yes|no] [--display]
                 [--engine auto|builtin|libreoffice] [--delimiter ;] [--bom] [--force]
sheet_convert.py FILE --to csv --out-dir DIR        # one file per sheet
```

- The output extension picks the format; `--to` overrides it.
- `--engine auto` (default) uses LibreOffice for workbook→workbook conversions and PDF when it is installed, except
  .xlsx → .ods (the built-in writer is exact for what it supports and keeps the workbook's own results).
- Workbook → text formats read the stored values (run `sheet_recalc` first if the file has formulas without
  results; `sheet_info` tells). `--display` applies each cell's number format.
- CSV/TSV/Parquet hold one sheet: pick it with `--sheet`, or use `--out-dir` for all. Data exports leave out Excel
  table totals rows and title rows above the header, and say so (`--keep-totals`, `--range`).
- CSV → .xlsx streams: a 300 000-row CSV becomes a styled workbook (typed columns, header, frozen row, filter,
  widths) in about 5 s and 400 MB, whatever its size.
- .xlsx → .xls with LibreOffice: Excel 97 cannot store newer functions (SUMIFS inside tables, XLOOKUP, SORTBY…),
  tables or structured references; the output warns before converting, and converting such an .xls back lists the
  formulas that became `=NA()`.
- LibreOffice's one-argument `ROUND(x)` (and ROUNDUP/ROUNDDOWN) gets Excel's second argument (`ROUND(x,0)`) when
  an .ods is converted.

### Built-in PDF (no LibreOffice)

Each sheet is drawn by the renderer (cells, number formats, fonts, fills, borders, merges, conditional formats,
images and charts; no gridlines or headings) on A4 pages at 150 dpi: portrait, or landscape for wide sheets; up to
2.5 pages wide the sheet is shrunk to one page wide, page breaks move above a chart instead of cutting it, and
empty pages are skipped. The pages are pictures: text cannot be selected or searched, and print areas, page
breaks, headers and footers are not applied. LibreOffice gives Excel's print layout with real text.

## sheet_read details

- The header row is detected when the first row is text over typed data; `--header yes|no` forces it. Header
  names appear as column labels (`A: Region`).
- Merged cells are listed; their value sits in the top-left cell.
- Errors show as `#DIV/0!` etc. (from the stored results).
- `--formulas` lists formulas under the grid, grouping filled-down runs (`D2:D40: =B2*C2 (filled down)`), with the
  stored result of single formulas and flags for array and dynamic-array formulas. Every formula cell of the range
  (the whole sheet without `--range`) is listed once across the pages, including ones outside the data block: the
  formula cells come from the sheet XML, and the grid grows to show them when they sit near the data. A formula
  saved without a result (openpyxl and other libraries write none) is marked `(no stored result)`; the output
  counts them (`formulas_without_values` in JSON) and gives the `sheet_recalc.py … --out` command that stores them.
- `--styles` lists fonts, fills, borders, alignment, number formats, column widths and merges for the range.
- Big sheets (over 5 000 rows, `DESK_SHEET_BIG_ROWS`) print a map first: the data block, header row, column types,
  empty shares, ranges, first and last rows, rows left out (totals, notes) and cells far outside the block.
  `--map` asks for it on any file (every sheet); `--rows 10001-10500` reads sheet rows by number; `--find TEXT`
  and `--grep REGEX` return cell addresses with their rows. Their data comes from a cached typed copy of the
  sheet (Parquet), so a read of 500 rows from a 200 000-row sheet takes a fraction of a second.
- Every output is capped by `--max-chars` (default 60 000) on a whole-row boundary and ends with the exact
  command for the next part; JSON too (`truncated`, `next`).
- A small `--range` (or `--rows`) of a very big sheet (part over 16 MB, `DESK_AREA_XML_MB`) is read straight from
  the sheet XML as a stream: chunks before the first row are skipped, reading stops after the last, and only those
  rows are kept, with just the shared strings they use. `--styles` and `--display` read styles the same way, with
  the styles part, so memory stays near 100 MB whatever the sheet's size. Without `--range` they read the first
  500 rows of a big sheet that way and continue with `--rows`.

## sheet_render details

The built-in renderer (Pillow) draws what a spreadsheet window shows:

- column letters and row numbers (`--no-headers` to hide), gridlines unless the sheet hides them;
- column widths and row heights from the file (automatic heights for wrapped text and large fonts), hidden rows
  and columns skipped, frozen-pane lines;
- fills (theme colours with tints, indexed colours, gradients as their first colour), fonts (family, size, bold,
  italic, underline, strike-through, colour), borders (thin to thick, dashed, dotted, double), alignment,
  indentation, wrapping, rotation, shrink-to-fit, text overflowing into empty neighbours, `####` for numbers
  that do not fit;
- number formats including `[Red]` sections, dates and percentages; formula results (stored, or computed by the
  engine when the file has none);
- merged cells, comments (red corner), Excel table styles (header and banding colours), conditional formats
  (cell rules, formulas, colour scales, data bars, icon sets as coloured dots, top/bottom, averages, duplicates,
  text, blanks, errors);
- images, and charts drawn from their data (column, bar, stacked, line, area, pie, doughnut, scatter, radar as
  lines) at their anchors, with the legend where the chart puts it (right, top right, left, top, bottom; none
  when the chart has no legend); chart sheets as one picture.

Images are at most 1568 px on each side; larger ranges become several images (`Sheet-p1.png`, `Sheet-p2.png`…) and
the JSON output gives each image's range (`pages`). A split is moved before a chart (or picture) it would cut when
the chart fits in one image; a chart too big for one image is drawn in part on every image it spans, each part
cut at the image edge so the parts line up. The output lists each chart with its anchor and the image or images
that hold it (`charts` in JSON). PNG names come from sheet names cleaned up into safe file names, the same way
`sheet_convert --out-dir` names its files: letters, digits, `.`, `-` and `_` only, no leading dot (a sheet named
`../../x` gives `x.png`), Windows device names avoided (`CON` gives `_CON.png`), and `-2`, `-3`… when two sheets
clean up alike. Sheet names that openpyxl refuses (with `/ \ ? * : [ ]`, which some programs write) are drawn
too. Fonts come from the system; missing ones (Calibri on a Mac without
Office) are replaced by metric-compatible fonts sized to keep text widths, and the output says so.

`--engine libreoffice` prints the sheet through LibreOffice instead (page setup, print area, headers and footers,
page breaks) and renders the PDF pages: use it to check printing, not cell layout. `.xls`, `.xlsb` and `.ods` are
converted to .xlsx through LibreOffice first when it is installed, so their formatting shows; without it an .ods
is drawn with the formatting the built-in reader carries over (not its conditional formats or charts), and
.xls/.xlsb from values with default styling.

Pivot charts point at pivot-table fields, not cells: they are drawn from the values the file caches with the
chart, with its own series colours. Renders are cached per file content and options (`--no-cache` redraws).

## LibreOffice

Found automatically (standard install locations on Windows, macOS and Linux, or `DESK_SOFFICE`); scripts use a
private profile so they never touch the user's LibreOffice settings, and force a full recalculation when files
load. `DESK_SOFFICE=none` hides it.
