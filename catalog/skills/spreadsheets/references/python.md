# Custom scripts

When no script fits, write a short Python script and run it with the same `python3` (Desk's environment has
openpyxl, python-calamine, DuckDB, Pillow and pypdfium2). Add this skill's `scripts/` folder to the path to reuse
its helpers, and write to a new file, never over the input.

```python
import sys
sys.path.insert(0, "/path/to/skill/scripts")   # this skill's scripts folder
```

## Read any spreadsheet

```python
from pathlib import Path
from _book import Workbook, trim_grid, detect_header

with Workbook(Path("export.xls")) as wb:          # .xlsx .xlsm .xls .xlsb .ods .csv .tsv .json, HTML tables
    for meta in wb.sheets():
        grid = wb.grid(meta.name)                 # values (numbers, text, bools, dates, error codes), from row1/col1
        rows = trim_grid(grid.rows)
        print(meta.name, grid.row1, grid.col1, len(rows), "header" if detect_header(rows) else "no header")
```

## Change a workbook with openpyxl, then store the results

openpyxl writes formulas without results; save through the skill so the file carries them (and newer functions
get Excel's prefixes):

```python
import openpyxl
from pathlib import Path
from types import SimpleNamespace
from _bridge import save_and_recalc
from _formula import add_prefixes

wb = openpyxl.load_workbook("in.xlsx")                 # keep_vba=True for .xlsm
ws = wb["Data"]
for r in range(2, ws.max_row + 1):
    ws.cell(r, 6).value = add_prefixes(f'=IFS(E{r}>100,"high",E{r}>50,"mid",TRUE,"low")')
ctx = SimpleNamespace(new_formulas=set(), autofit_pending=[], warnings=[])
report = save_and_recalc(wb, Path("out.xlsx"), ctx)
print(report.get("error_counts"), report.get("unsupported"))
```

`sheet_edit.py` operations are usually shorter and also rewrite references on insert/delete; prefer them.

## Evaluate formulas without Excel

```python
from _xlsx import load_book, recalc_file
from _formula import Engine, evaluate_formula

book, pkg, _extra = load_book("model.xlsx"); pkg.close()
report = Engine(book).recalc()                       # errors, cycles, unsupported functions, spills
print(evaluate_formula(book, '=SUMIFS(Data!D:D,Data!A:A,"North")', "Summary"))
sheet = book.sheet("Summary")
print(sheet.formulas[(2, 3)].value)                  # (row, col) → computed value of C2

recalc_file("model.xlsx", "model-calc.xlsx")         # what sheet_recalc --out does
```

Try "what if" questions on a copy of the book: `book.set_value(book.sheet("Inputs"), 2, 2, 0.07)` then
`Engine(book).recalc()` again, and read the outputs, without writing a file.

## Number formats and cell addresses

```python
from _numfmt import format_value
from _a1 import parse_range, col_letter, col_index

format_value(1234.5, "#,##0.00;[Red](#,##0.00)")   # ('1,234.50', None); negatives give color 'FF0000'
format_value(45351, "d mmm yyyy")                   # ('29 Feb 2024', None)
parse_range("B2:D10")                               # (2, 2, 10, 4)
col_letter(28), col_index("AB")                     # ('AB', 28)
```

## SQL over anything

```python
import duckdb
con = duckdb.connect()
con.execute("CREATE TABLE t AS SELECT * FROM read_csv('big.csv')")
print(con.execute("SELECT region, SUM(amount) FROM t GROUP BY 1").fetchall())
```

For workbooks, `sheet_query.py` loads sheets as typed tables (header detection, dates, percentages) and is
simpler than doing it by hand.

## Pictures of a sheet

```python
from pathlib import Path
from _grid import load_view, paginate, draw_page

view = load_view(Path("report.xlsx"), "Summary", None, 60, 30, 1.0)   # sheet, area, max rows, max cols, zoom
for i, (rows, cols) in enumerate(paginate(view, 1568), 1):
    draw_page(view, rows, cols).save(f"summary-{i}.png")
```

Then look at the PNGs with view_image.

## Big sheets

The scripts keep a typed Parquet copy of each big sheet in Desk's file cache; reuse it instead of loading the
workbook (a 200 000 × 30 sheet loads in 0.1 s from the cache, about 6 s the first time):

```python
from pathlib import Path
from _store import connect, sheet_store

meta = sheet_store(Path("orders.xlsx"), "Orders")      # columns, types, header row, rows left out, "parquet" path
con = connect()                                          # DuckDB set up for Desk's sandbox
df = con.sql(f"SELECT * FROM read_parquet('{meta['parquet']}') WHERE Region = 'West'").fetchall()
# every row has _row = its row number in the sheet
```

Write a large table fast (about 1 µs per cell, memory only for distinct strings) instead of openpyxl:

```python
from _fastxlsx import write_table
write_table("out.xlsx", "Data", ["Date", "Region", "Amount"], rows_iterable, formats=["yyyy-mm-dd", None, "#,##0.00"])
```

Change cells of a big workbook in place (what `sheet_edit` does for set/fill/clear/style), keeping every other
part byte for byte, then refresh the stored results:

```python
from _patch import apply
from _xlsx import recalc_file
plan = apply(Path("big.xlsx"), Path("tmp.xlsx"), [{"op": "set", "cell": "Orders!C150000", "value": "West"}], None)
recalc_file("tmp.xlsx", "big-v2.xlsx")
```
