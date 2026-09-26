# Performance and big files

Measured on an Apple M2 (8 GB) with Python 3.12, one process at a time. "Cold" is the first call on a file (empty
cache); "cached" is any later call on the same file content. Peak memory is the process's maximum resident size.

## A 200 000-row workbook

`orders200k.xlsx`: 40 MB, sheet Orders 200 000 rows × 30 columns (dates, text, numbers, percentages), a Calc sheet
with 60 000 formulas; 6 million cells, 223 MB of sheet XML.

| call | cold | cached | peak memory |
|---|---|---|---|
| `sheet_info` | 1.6 s | 0.05 s | 133 MB |
| `sheet_read` (the map: columns, types, head, tail) | 5.6 s | 0.14 s | 474 MB cold, 33 MB cached |
| `sheet_read --find Country49` | 1.0 s once the map exists | | 97 MB |
| `sheet_read --rows 150001-150500 --format csv` | 0.24 s | | 77 MB |
| `sheet_read --range A150000:F150010` (read from the XML) | 0.19 s | | 61 MB |
| `sheet_read --range A1:F3 --styles` (streamed, see below) | 0.45 s | | 89 MB |
| `sheet_query` GROUP BY over every row | 0.18 s once the map exists | | 63 MB |
| `sheet_recalc --check` (60 000 formulas) | 9.5 s | 0.05 s | 824 MB |
| `sheet_render` (first 60 rows) | 3.1 s | 0.05 s | 730 MB |
| `sheet_edit` one cell + a header style, `--no-recalc` (patch) | 1.6 s | | 500 MB |
| `sheet_edit` one cell, recalculated (patch + engine) | 14 s | | 714 MB |
| `sheet_edit` insert_rows (openpyxl), `--no-recalc` | 116 s | | 2.1 GB |
| `sheet_convert --sheet Orders --out orders.csv` | 0.75 s | | 272 MB |

### Styles and display formats of a huge sheet

`--styles` and `--display` used to inflate the whole sheet part into memory (twice: once for the values, once for
the styles) and, without `--range`, load every cell through calamine. They now stream the part in 8 MB chunks,
skip the chunks before the first row asked for, stop after the last, and keep only those rows, plus the styles
part and the shared strings those rows use. Measured on a regenerated workbook of the same shape (200 000 × 30,
32 MB file, 248 MB of sheet XML; the scan already cached), one process at a time:

| call | before | after |
|---|---|---|
| `sheet_read --range A1:F3 --styles` | 0.84-1.17 s, 553 MB | 0.45 s, 89 MB |
| `sheet_read --range A150000:F150010 --styles` | 0.84 s, 553 MB | 0.64 s, 128 MB |
| `sheet_read --range A199990:AD200001 --styles` (the last rows) | 0.82 s, 553 MB | 0.81 s, 135 MB |
| `sheet_read --rows 150001-150003 --styles` | 1.03 s, 552 MB | 0.65 s, 128 MB |
| `sheet_read --styles` (no range: the first 500 rows, then `--rows`) | 12 s, 1.18 GB | 0.42 s, 91 MB |
| `sheet_read --range A150000:F150010 --display` | 0.55 s, 547 MB | 0.52 s, 121 MB |

Memory no longer grows with the sheet: it is the chunk size, the rows asked for and the styles part.

## A 1 000 000-row CSV

`orders1m.csv`: 96 MB, 1 000 000 rows × 12 columns.

| call | cold | cached | peak memory |
|---|---|---|---|
| `sheet_info` (builds the typed copy) | 2.2 s | 0.04 s | 724 MB |
| `sheet_read` (the map) | 0.05 s after sheet_info | 0.05 s | 31 MB |
| `sheet_query` GROUP BY over every row | 0.10 s | | 69 MB |
| `sheet_read --find "Rep Q42"` | 0.67 s | | 86 MB |
| `sheet_convert --out orders.xlsx` (streaming writer) | 16 s | | 244 MB |
| `sheet_convert --out orders.parquet` | 0.32 s | | 421 MB |

A 300 000-row CSV converts to a styled .xlsx in 5.5 s and 390 MB.

## What is cached

Desk's file cache keys every entry by the file's content, the options and the code version, so an edited file is
never served stale, and copies of a file share entries. `--no-cache` (on sheet_read, sheet_query, sheet_info,
sheet_recalc, sheet_render) recomputes; `DESK_NO_CACHE=1` turns the cache off for everything.

- the per-sheet scan of each worksheet's XML (cells, formulas, errors, used range, stray cells, features);
- a typed Parquet copy of each sheet that sheet_read and sheet_query use (columns typed as integer, number, date,
  date-time, text; `_row` holds the sheet row; Excel table totals rows and notes below the data left out);
- `sheet_recalc` reports and recalculated workbooks, `sheet_render` images.

## How the big-file paths work

- **Map first.** Over 5 000 rows (`DESK_SHEET_BIG_ROWS`) sheet_read prints a map instead of a dump. Every output is
  capped by `--max-chars` at a whole row and ends with the exact next command.
- **Find, then drill down.** `--find`/`--grep` search the typed copy and return cell addresses (`G9876`) with their
  rows; `--rows A-B` or `--range` read just that part; sheet_query answers questions over every row.
- **Streaming reads.** Sheet XML is scanned in 8 MB chunks; a small `--range` or `--rows` of a sheet part over
  16 MB (`DESK_AREA_XML_MB`) streams the part, skipping whole chunks before its first row and stopping after its
  last, so its memory does not grow with the sheet.
- **Surgical edits.** set/fill/clear/style on files of 3 MB or more (`DESK_EDIT_PATCH_MB`) patch only the rows they
  touch inside the affected sheet parts, append styles to styles.xml and copy every other zip member raw. Structural
  operations need openpyxl: on workbooks over 2 million cells (`DESK_EDIT_SLOW_CELLS`) sheet_edit stops first and
  states the time and memory it would take; `--mode openpyxl` goes ahead.
- **Recalculation.** The engine loads big workbooks through a streaming loader (over 50 000 cells), then evaluates
  every formula (9.5 s for the 6-million-cell workbook above with its 60 000 formulas), so a patch that changes
  values of a big model spends most of its time here. `--no-recalc` skips it (Excel then recalculates on open).

## Scaling

Memory grows with the cells actually read: the typed copy is built 4 096 rows at a time, DuckDB is capped
at 1 GB and spills to a temp folder, and the patch path holds the edited sheet part once. The costly paths are the
ones that need every cell as a Python object: recalculation (about 140 bytes per formula-or-cell) and the openpyxl
path (about 340 bytes per cell). Keep those for workbooks up to a few million cells.
