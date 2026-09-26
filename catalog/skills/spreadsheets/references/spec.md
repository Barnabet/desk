# sheet_create specs

`python3 scripts/sheet_create.py out.xlsx --spec spec.json` (or inline JSON, or `-` for stdin). The workbook is
built, every formula is checked and stored in Excel's file form, then calculated so the file carries its results.
`.xlsm`/`.xltx` outputs work too. `--no-recalc` skips the calculation; `--now` fixes TODAY()/NOW().

## Workbook

```json
{
  "sheets": [ {…}, {…} ],
  "names": {"TaxRate": "Inputs!$B$2", "Months": "=12"},
  "properties": {"title": "Budget 2025", "author": "Finance"},
  "calc": {"iterate": true},
  "active": "Summary",
  "ops": [ {"op": "…"} ]
}
```

`names` may also be a list of `{"name", "ref", "scope"}`. `ops` are any `sheet_edit` operations
(`references/ops.md`), applied after all sheets exist, so they can refer across sheets.

## Sheet

| key | meaning |
|---|---|
| `name` | sheet name (≤ 31 characters, no `[]:*?/\`); duplicates get a number |
| `columns` | list of column specs (below) or plain header strings |
| `rows` | lists (by position) or objects (by header); `null` leaves a cell empty (a column formula fills it) |
| `header` | header strings when `columns` is not given |
| `start` | top-left cell of the block (default `A1`) |
| `table` | `true` or `{"name", "style", "banded_rows", "banded_columns"}`: an Excel table (filter buttons, banding, structured references) |
| `totals` | `true` (sum every numeric column), or `{"Header": "sum" | "average" | "count" | "max" | "min"}`; SUBTOTAL formulas so filtered rows are left out |
| `header_style` | a `style` object for the header row (default: bold white on dark blue, centred, wrapped); `null` or `{}` for none |
| `banded` | `true` or a colour: shade every other row (without a table) |
| `freeze` | `true` (default with a header: below the header), a cell like `"B2"`, or `false` |
| `autofilter` | default true with a header and no table |
| `autofit` | default true: column widths from the displayed values |
| `widths` | `{"A": 30, "C": 12}` explicit widths in characters |
| `cells` | `{"G1": "Tax", "H1": 0.2, "H2": "=SUM(D2:D20)*(1+H1)"}` extra cells anywhere (values, formulas, `{"value", "format"}`, `{"date"}`, `{"value": "=text", "text": true}`) |
| `merge` | `["A1:D1"]` |
| `conditional` | conditional formats: `sheet_edit`'s fields, plus `"column": "Header"` for that column's data cells |
| `validations` | data validations, same `"column"` shortcut |
| `charts` | chart ops; `values`/`categories` may be header names (`"values": ["Revenue", "Cost"], "categories": "Month"`); placed to the right of the data unless `anchor` is given |
| `ops` | `sheet_edit` operations for this sheet (their default sheet) |

## Column

| key | meaning |
|---|---|
| `header` | the header text |
| `format` | Excel number format for the data cells (`#,##0`, `0.0%`, `$#,##0.00`, `yyyy-mm-dd`…) |
| `formula` | a formula for every data row; `{row}` is replaced by the row number: `"=B{row}*C{row}"` |
| `total` | this column's total function (same as `totals`) |
| `width` | width in characters (otherwise fitted) |
| `align` | `left`, `center`, `right`, or an `align` object |
| `style` | a `style` object for the data cells |

Values: numbers, booleans and strings as given; `"=…"` strings are formulas; ISO dates (`"2025-03-31"`) and
date-times become real dates with a date format; `{"value": 0.25, "format": "0%"}` sets a per-cell format.

## Example

```json
{
  "sheets": [
    {
      "name": "Inputs",
      "cells": {"A1": "Tax rate", "B1": 0.2, "A2": "Discount", "B2": 0.05},
      "widths": {"A": 16}
    },
    {
      "name": "Orders",
      "columns": [
        {"header": "Date", "format": "yyyy-mm-dd"},
        {"header": "Customer", "width": 24},
        {"header": "Qty", "format": "#,##0"},
        {"header": "Unit price", "format": "$#,##0.00"},
        {"header": "Net", "formula": "=C{row}*D{row}*(1-Discount)", "format": "$#,##0.00", "total": "sum"},
        {"header": "Gross", "formula": "=E{row}*(1+TaxRate)", "format": "$#,##0.00", "total": "sum"}
      ],
      "rows": [["2025-01-06", "Acme", 12, 9.5], ["2025-01-09", "Globex", 4, 120], ["2025-01-15", "Initech", 30, 2.25]],
      "table": {"name": "Orders"},
      "conditional": [{"column": "Gross", "type": "data_bar"}],
      "validations": [{"column": "Qty", "type": "whole", "operator": ">=", "value": 0, "error": "Quantities are whole numbers"}],
      "charts": [{"type": "column", "categories": "Customer", "values": ["Net", "Gross"], "title": "Orders by customer"}]
    },
    {
      "name": "Summary",
      "cells": {
        "A1": "Customers", "B1": "=ROWS(UNIQUE(Orders[Customer]))",
        "A2": "Gross total", "B2": "=SUM(Orders[Gross])",
        "A4": "Top customers", "A5": "=TAKE(SORTBY(Orders[Customer],Orders[Gross],-1),2)"
      },
      "ops": [{"op": "style", "range": "B2", "number_format": "$#,##0.00"}]
    }
  ],
  "names": {"TaxRate": "Inputs!$B$1", "Discount": "Inputs!$B$2"},
  "active": "Summary"
}
```

After creating, check with `sheet_recalc.py out.xlsx --check` and look at `sheet_render.py out.xlsx --all`.

## From data files

`--from` builds the spec for you, one sheet per file (or per Markdown table):

```bash
python3 scripts/sheet_create.py out.xlsx --from orders.csv --from customers.json --sheet-name Orders --sheet-name Customers
python3 scripts/sheet_create.py out.xlsx --from orders.csv --table --totals --chart line
python3 scripts/sheet_create.py out.xlsx --from notes.md --no-style
```

CSV/TSV: delimiter, encoding (UTF-8, UTF-16, Windows-1252) and decimal commas (`1.234,5` in semicolon files) are
detected; numbers, booleans, ISO dates, percentages (`12%` → 0.12 shown as 12%) and currency (`$9.50`, `€3`) are
typed (also `($150.25)` as a negative), and a column holding codes with leading zeros (`007`) stays text as a
whole. Text starting with `=` is kept as text unless `--allow-formulas` is given (a data file's cell is never run
as a formula). `--chart` plots the numeric columns against the first column on one value axis: percentages next to amounts,
and columns more than 50 times smaller than the largest, are left out and the output says so; the chart is titled
after what it plots ("Units and Revenue by Region"). JSON: a list of records, `{"rows": [...]}`, or
`{"Sheet": [records]}` for several sheets. Markdown: every pipe table, named after the heading above it.
