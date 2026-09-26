#!/usr/bin/env python3
"""SQL (DuckDB) over the sheets of one or more workbooks and CSV/TSV/JSON/Parquet files."""

from __future__ import annotations

import csv
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, cap, emit, input_file, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/sheet_query.py sales.xlsx --tables                         # table names, columns and types
  python3 scripts/sheet_query.py sales.xlsx --sql "SELECT region, SUM(revenue) AS total FROM orders GROUP BY 1 ORDER BY 2 DESC"
  python3 scripts/sheet_query.py sales.xlsx --sql "SELECT _row, rep, revenue FROM orders WHERE rep ILIKE '%zelda%'"   # _row = sheet row
  python3 scripts/sheet_query.py report.xlsx --sql "SELECT * FROM salestbl"   # an Excel table by its name (totals row left out)
  python3 scripts/sheet_query.py a.xlsx b.csv --sql "SELECT * FROM a_orders o JOIN b USING (id)"
  python3 scripts/sheet_query.py book.xlsx --sql "SELECT * EXCLUDE (_row) FROM \\"Sheet 1\\" WHERE amount > 100" --out big.xlsx
  python3 scripts/sheet_query.py data.xlsx --range "Raw!A3:H500" --sql "SELECT COUNT(*) FROM raw"

Each worksheet is a table named after the sheet (lowercase, non-alphanumerics → _; the exact sheet name also works
in double quotes). Excel tables (ListObjects) are tables named after themselves. With several workbooks, names are
prefixed with the file name: <file>_<sheet>. CSV, TSV, JSON, JSONL and Parquet files are tables named after the file.
Every sheet and CSV table has a _row column: the row number in the sheet (CSV: the record number, header = 1), so
results carry addresses. Totals rows (of Excel tables, or a last row labelled Total) are left out and listed by
--tables. Columns are typed like sheet_read: numbers, dates, 12%, $1,234.50 and 1.234,50 become numbers; codes with
leading zeros stay text. Sheets are read once into a cached Parquet copy, so repeated queries on big files are fast.
"""


def main() -> int:
    p = parser("Run DuckDB SQL over spreadsheets (.xlsx .xlsm .xls .xlsb .ods) and data files (.csv .tsv .json .parquet).", EPILOG)
    p.add_argument("files", nargs="+", help="workbooks and data files")
    p.add_argument("--sql", "-q", help="the query (DuckDB dialect)")
    p.add_argument("--tables", action="store_true", help="list the tables and their columns")
    p.add_argument("--header", choices=["auto", "yes", "no"], default="auto", help="first row holds column names (default auto)")
    p.add_argument("--range", action="append", default=[], help="load only this area of a sheet: 'Sheet!A3:H500' (repeatable)")
    p.add_argument("--keep-totals", action="store_true", help="keep totals rows as data rows")
    p.add_argument("--limit", type=int, default=200, help="rows to print (default 200; --out gets all)")
    p.add_argument("--offset", type=int, default=0, help="skip this many result rows (paging)")
    p.add_argument("--out", help="write the result to .csv/.tsv/.json/.jsonl/.parquet/.xlsx")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="cap on printed output; it ends with the command for the next part (default 60000)")
    p.add_argument("--no-cache", action="store_true", help="do not use or store cached Parquet copies of the sheets")
    p.add_argument("--force", action="store_true")
    add_format(p, ("md", "csv", "json"))
    a = p.parse_args()
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    if not a.sql and not a.tables:
        raise UsageError("give --sql or --tables")
    if a.offset < 0 or (a.limit is not None and a.limit < 0):
        raise UsageError("--offset and --limit must be 0 or more")
    paths = [input_file(f) for f in a.files]
    out = output_path(a.out, paths, a.force) if a.out else None
    import duckdb

    from _store import connect

    t0 = time.time()
    con = connect()
    keywords = {r[0].lower() for r in con.execute("SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category <> 'unreserved'").fetchall()}
    catalog = build_catalog(paths, a.range, keywords)
    wanted = select_tables(catalog, a.sql) if a.sql and not a.tables else catalog
    loaded = []
    for t in _with_bases(wanted, catalog):
        load_table(con, t, a.header, a.keep_totals)
        loaded.append(t)
    if a.tables:
        info = [describe(con, t) for t in loaded if t in wanted]
        emit({"tables": info}, a.format, render_tables, max_chars=a.max_chars, hint="Name the files one at a time.")
        return 0
    try:
        rel = con.sql(a.sql)
    except duckdb.Error as e:
        names = ", ".join(t["name"] for t in catalog)
        first = str(e).splitlines()[0]
        hint = ""
        near = re.search(r'at or near "([^"]+)"', first)
        if near and re.fullmatch(r"\w+", near.group(1)):
            hint = f'\n{near.group(1)} is an SQL keyword: write it in double quotes ("{near.group(1)}") when it names a column or table.'
        elif "does not have a column" in first or "not found in FROM clause" in first:
            hint = "\nColumn names with spaces or capitals need double quotes: \"Unit price\"."
        raise SkillError(f"SQL error: {first}{hint}\ntables: {names} (see --tables)") from None
    if rel is None:
        print("ok (statement executed)")
        return 0
    cols = rel.columns
    if out is not None:
        n = write_result(con, rel, out)
        emit({"output": str(out), "rows": n, "columns": cols, "seconds": round(time.time() - t0, 2)}, a.format, lambda r: f"Wrote {r['rows']:,} rows × {len(r['columns'])} columns to {r['output']} in {r['seconds']}s.")
        return 0
    total = rel.count("*").fetchone()[0]
    page = rel.limit(a.limit, offset=a.offset) if a.limit else rel.limit(10**12, offset=a.offset)
    rows = page.fetchall()
    emit_rows(cols, rows, total, a)
    return 0


# ── tables ──────────────────────────────────────────────────────────────


def sanitize(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip()).strip("_").lower()
    if not s:
        s = "t"
    if s[0].isdigit():
        s = "t_" + s
    return s


def build_catalog(paths: list[Path], ranges: list[str], keywords: set[str] = frozenset()) -> list[dict[str, Any]]:  # type: ignore[assignment]
    from _a1 import parse_range, split_sheet
    from _book import Workbook, kind_of

    area_by_sheet: dict[str, tuple[int, int, int, int]] = {}
    for spec in ranges:
        sheet, rest = split_sheet(spec)
        if not sheet:
            raise UsageError("--range needs a sheet: 'Sheet!A3:H500'")
        try:
            area_by_sheet[sheet.lower()] = parse_range(rest)
        except ValueError as e:
            raise UsageError(f"--range {spec}: {e}") from e
    kinds = {p: kind_of(p) for p in paths}
    books = [p for p in paths if kinds[p] in ("xlsx", "xls", "xlsb", "ods", "html")]
    multi = len(books) > 1 or len(paths) > 1
    catalog: list[dict[str, Any]] = []
    used: set[str] = set()

    def unique(n: str) -> str:
        if n in keywords:  # 'semi', 'order', 'select'… would need quotes in every query
            n += "_data"
        base, k = n, 2
        while n in used:
            n = f"{base}_{k}"
            k += 1
        used.add(n)
        return n

    for p in paths:
        kind = kinds[p]
        ext = p.suffix.lower()
        if kind in ("xlsx", "xls", "xlsb", "ods", "html"):
            with Workbook(p) as wb:
                sheet_entries = {}
                for m in wb.sheets():
                    if m.kind != "worksheet":
                        continue
                    name = unique(sanitize(f"{p.stem}_{m.name}") if multi else sanitize(m.name))
                    entry = {"name": name, "sheet": m.name, "path": p, "kind": "sheet", "area": area_by_sheet.get(m.name.lower()), "aliases": [m.name] if m.name != name else []}
                    catalog.append(entry)
                    sheet_entries[m.name] = entry
            if kind == "xlsx":
                from _xlsx import Package

                try:
                    with Package(p) as pkg:
                        tables = pkg.tables()
                except Exception:  # noqa: BLE001 — tables are an extra
                    tables = []
                for sname, t, _part in tables:
                    tname = unique(sanitize(f"{p.stem}_{t.name}") if multi else sanitize(t.name))
                    catalog.append({"name": tname, "sheet": sname, "path": p, "kind": "xltable", "table": t, "base": sheet_entries.get(sname), "aliases": [t.name] if t.name.lower() != tname else []})
        elif kind == "parquet" or ext == ".parquet":
            catalog.append({"name": unique(sanitize(p.stem)), "path": p, "kind": "parquet", "aliases": []})
        elif kind == "json":
            catalog.append({"name": unique(sanitize(p.stem)), "path": p, "kind": "json", "aliases": []})
        else:
            name = unique(sanitize(p.stem))
            catalog.append({"name": name, "path": p, "kind": "csv", "aliases": [p.stem] if name != p.stem else []})
    return catalog


def select_tables(catalog: list[dict[str, Any]], sql: str) -> list[dict[str, Any]]:
    low = sql.lower()
    picked = []
    for t in catalog:
        names = [t["name"]] + t.get("aliases", [])
        if any(re.search(r'(?<![\w"])' + re.escape(n.lower()) + r'(?![\w"])', low) or f'"{n.lower()}"' in low for n in names):
            picked.append(t)
    return picked or [t for t in catalog if t["kind"] != "xltable"]


def _with_bases(wanted: list[dict[str, Any]], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Excel tables are views over their sheet's copy when they line up with it: load the sheet first."""
    out: list[dict[str, Any]] = []
    for t in wanted:
        if t["kind"] == "xltable" and t.get("base") is not None and t["base"] not in out:
            out.append(t["base"])
        if t not in out:
            out.append(t)
    return out


def _lit(text: str) -> str:
    """A SQL string literal."""
    return "'" + text.replace("'", "''") + "'"


def load_table(con: Any, t: dict[str, Any], header_mode: str, keep_totals: bool = False) -> None:
    from _store import ident, sheet_store, sql_str

    name = t["name"]
    q = ident(name)
    if t.get("loaded"):
        return
    if t["kind"] == "parquet":
        con.execute(f"CREATE VIEW {q} AS SELECT * FROM read_parquet({sql_str(t['path'])})")
    elif t["kind"] == "xltable":
        tb = t["table"]
        base = t.get("base")
        meta = base.get("meta") if base else None
        cols = tb.columns
        data_top = tb.r1 + tb.header_rows
        data_bottom = tb.r2 - (0 if keep_totals else tb.totals_rows)
        aligned = (
            meta is not None and meta.get("header_row") == tb.r1 and tb.header_rows == 1
            and [c["name"] for c in meta["columns"]][tb.c1 - meta["first_col"] : tb.c2 - meta["first_col"] + 1] == [c for c in _unique(cols)]
        )
        if aligned:
            sel = ", ".join(ident(c) for c in _unique(cols))
            rc = meta["row_column"]
            con.execute(f"CREATE VIEW {q} AS SELECT {sel}, {ident(rc)} FROM {ident(base['name'])} WHERE {ident(rc)} BETWEEN {int(data_top)} AND {int(data_bottom)}")
            t["meta"] = {**meta, "rows": None, "excluded": [e for e in meta.get("excluded", []) if tb.r1 <= e["row"] <= tb.r2]}
        else:
            meta = sheet_store(t["path"], t["sheet"], "yes", (tb.r1, tb.c1, tb.r2, tb.c2), keep_totals)
            t["meta"] = meta
            con.execute(f"CREATE VIEW {q} AS SELECT * FROM read_parquet({sql_str(meta['parquet'])})")
    else:
        sheet = t.get("sheet") if t["kind"] == "sheet" else None
        meta = sheet_store(t["path"], sheet, header_mode, t.get("area"), keep_totals)
        t["meta"] = meta
        con.execute(f"CREATE VIEW {q} AS SELECT * FROM read_parquet({sql_str(meta['parquet'])})")
    t["loaded"] = True
    for alias in t.get("aliases", []):
        try:
            con.execute(f"CREATE VIEW {ident(alias)} AS SELECT * FROM {q}")
        except Exception:  # noqa: BLE001 — alias clashes (DuckDB names are case-insensitive) are harmless
            pass


def _unique(names: list[str]) -> list[str]:
    from _book import unique_headers

    return unique_headers([" ".join(str(n).split()) if n is not None else None for n in names])


def _load_rows(con: Any, name: str, rows: list[list[Any]], tmp: Path, header_mode: str) -> None:
    """Rows already in memory → a typed table (used by sheet_convert for small parquet exports)."""
    from _a1 import col_letter
    from _book import detect_header, text_value, unique_headers

    q = '"' + name.replace('"', '""') + '"'
    if not rows:
        con.execute(f"CREATE TABLE {q} (empty VARCHAR)")
        return
    width = max(len(r) for r in rows)
    has_header = header_mode == "yes" or (header_mode == "auto" and detect_header(rows))
    if has_header:
        heads = unique_headers([" ".join(text_value(h).split()) if h is not None else None for h in rows[0]] + [None] * (width - len(rows[0])))
        body = rows[1:]
    else:
        heads = [col_letter(i + 1) for i in range(width)]
        body = rows
    types = [_column_type([r[i] if i < len(r) else None for r in body]) for i in range(width)]
    f = tmp / f"{sanitize(name)}.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(heads)
        for r in body:
            w.writerow([_csv_value(r[i] if i < len(r) else None, types[i]) for i in range(width)])
    cols = ", ".join(f"'{h.replace(chr(39), chr(39) * 2)}': '{t}'" for h, t in zip(heads, types))
    con.execute(
        f"CREATE TABLE {q} AS SELECT * FROM read_csv(?, header=true, auto_detect=false, delim=',', quote='\"', escape='\"', columns={{{cols}}})",
        [str(f)],
    )


def _column_type(values: list[Any]) -> str:
    """DuckDB type for a column of Python cell values (mixed columns stay text)."""
    import datetime as dt

    kinds = set()
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            kinds.add("float")
        elif isinstance(v, dt.datetime):
            kinds.add("datetime")
        elif isinstance(v, dt.date):
            kinds.add("date")
        elif isinstance(v, dt.time):
            kinds.add("time")
        else:
            return "VARCHAR"
    if not kinds:
        return "VARCHAR"
    if kinds == {"int"}:
        big = max(abs(v) for v in values if isinstance(v, int) and not isinstance(v, bool))
        return "BIGINT" if big < 2**63 else "DOUBLE"
    if kinds <= {"int", "float"}:
        return "DOUBLE"
    if kinds == {"bool"}:
        return "BOOLEAN"
    if kinds == {"date"}:
        return "DATE"
    if kinds <= {"date", "datetime"}:
        return "TIMESTAMP"
    if kinds == {"time"}:
        return "TIME"
    return "VARCHAR"


def _csv_value(v: Any, typ: str) -> Any:
    from _book import text_value

    if v is None or (v == "" and typ != "VARCHAR"):
        return None
    if typ == "BOOLEAN":
        return "true" if v else "false"
    if typ in ("BIGINT", "DOUBLE"):
        return repr(v) if isinstance(v, float) else str(v)
    if typ in ("DATE", "TIMESTAMP", "TIME"):
        return v.isoformat(sep=" ") if hasattr(v, "hour") and hasattr(v, "day") else v.isoformat()
    return text_value(v)


def describe(con: Any, t: dict[str, Any]) -> dict[str, Any]:
    q = '"' + t["name"].replace('"', '""') + '"'
    cols = con.execute(f"DESCRIBE {q}").fetchall()
    n = con.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    src = f"{t['path'].name}" + (f" / {t['sheet']}" if t.get("sheet") and t["kind"] == "sheet" else "")
    if t["kind"] == "xltable":
        tb = t["table"]
        from _a1 import col_letter

        src = f"{t['path'].name} / Excel table {tb.name} on {t['sheet']}!{col_letter(tb.c1)}{tb.r1}:{col_letter(tb.c2)}{tb.r2}"
    d: dict[str, Any] = {"name": t["name"], "source": src, "rows": n, "columns": [{"name": c[0], "type": c[1]} for c in cols]}
    if t.get("aliases"):
        d["aliases"] = t["aliases"]
    meta = t.get("meta") or {}
    if meta.get("excluded"):
        d["left_out"] = meta["excluded"]
    if meta.get("header_row") and meta.get("header_row") != 1 and t["kind"] == "sheet":
        d["header_row"] = meta["header_row"]
    if meta.get("outlier_count"):
        d["outlying_cells"] = meta["outlier_count"]
    return d


def render_tables(d: dict[str, Any]) -> str:
    lines = []
    for t in d["tables"]:
        alias = f" (also \"{t['aliases'][0]}\")" if t.get("aliases") else ""
        lines.append(f"- {t['name']}{alias}: {t['rows']:,} rows from {t['source']}" + (f"; header in row {t['header_row']}" if t.get("header_row") else ""))
        lines.append("  " + ", ".join(f"{c['name']} {c['type']}" for c in t["columns"]))
        if t.get("left_out"):
            lines.append("  left out (not data): " + "; ".join(f"row {e['row']} ({e['why']})" for e in t["left_out"][:5]) + " — --keep-totals keeps them")
        if t.get("outlying_cells"):
            lines.append(f"  {t['outlying_cells']} cell(s) far outside the data block are not in the table (sheet_read.py shows them)")
    return "\n".join(lines)


# ── output ──────────────────────────────────────────────────────────────


def _py(v: Any) -> Any:
    import datetime as _dt
    import decimal

    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (list, dict, tuple)):
        import json

        return json.dumps(v, default=str, ensure_ascii=False)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, _dt.timedelta):
        return str(v)
    return v


def _again(a: Any, offset: int) -> str:
    """This command with a new --offset."""
    parts = ["python3", "scripts/sheet_query.py", *a.files]
    if a.sql:
        parts += ["--sql", a.sql]
    for r in a.range:
        parts += ["--range", r]
    if a.header != "auto":
        parts += ["--header", a.header]
    if a.format != "md":
        parts += ["--format", a.format]
    if a.limit != 200:
        parts += ["--limit", str(a.limit)]
    parts += ["--offset", str(offset)]
    return " ".join(shlex.quote(str(x)) for x in parts)


def emit_rows(cols: list[str], rows: list[tuple], total: int, a: Any) -> None:
    import json

    from _book import csv_text, json_value, text_value
    from _common import md_escape_cell

    rows = [[_py(v) for v in r] for r in rows]
    budget = a.max_chars or 0
    if a.format == "json":
        recs = [[json_value(v) for v in r] for r in rows]
        payload: dict[str, Any] = {"columns": cols, "rows": recs, "total_rows": total, "offset": a.offset}
        text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        if budget and len(text) > budget:
            lo, hi = 0, len(recs)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                payload["rows"] = recs[:mid]
                if len(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))) + 400 <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            payload["rows"] = recs[:lo]
            payload["truncated"] = f"{len(recs) - lo:,} more rows of this page did not fit in --max-chars {budget:,}"
            payload["next"] = _again(a, a.offset + lo)
            text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        elif a.offset + len(rows) < total:
            payload["next"] = _again(a, a.offset + len(rows))
            text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        print(text)
        return
    if a.format == "csv":
        head = csv_text([cols]).rstrip("\n")
        lines = [csv_text([r]).rstrip("\n") for r in rows]
    else:
        head = "\n".join(["| " + " | ".join(md_escape_cell(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"])
        lines = ["| " + " | ".join(md_escape_cell(text_value(v)) for v in r) + " |" for r in rows]
    room = budget - len(head) - 300 if budget else None
    kept = len(lines)
    if room is not None:
        acc = 0
        for i, ln in enumerate(lines):
            acc += len(ln) + 1
            if acc > room:
                kept = max(i, 1)
                break
    out = head + ("\n" + "\n".join(lines[:kept]) if kept else "")
    end = a.offset + kept
    if kept < len(lines):
        out += f"\n[… {len(lines) - kept:,} more rows of this page did not fit in --max-chars {budget:,}. Next: {_again(a, end)}]"
    elif end < total:
        msg = f"{kept:,} of {total:,} rows shown (rows {a.offset + 1:,}-{end:,}). Next: {_again(a, end)} — or --out file.csv for all of them."
        out += ("\n\n" if a.format == "md" else "\n# ") + msg
    elif a.format == "md":
        out += f"\n\n{total:,} row{'s' if total != 1 else ''}."
    print(out)


def write_result(con: Any, rel: Any, out: Path) -> int:
    ext = out.suffix.lower()
    n = rel.count("*").fetchone()[0]
    if ext in (".csv", ".tsv"):
        rel.write_csv(str(out), sep="\t" if ext == ".tsv" else ",", header=True)
    elif ext == ".parquet":
        rel.write_parquet(str(out))
    elif ext in (".json", ".jsonl", ".ndjson"):
        import json

        from _book import json_value

        cols = rel.columns
        rows = rel.fetchall()
        with open(out, "w", encoding="utf-8") as f:
            if ext == ".json":
                json.dump([{c: json_value(_py(v)) for c, v in zip(cols, r)} for r in rows], f, ensure_ascii=False, indent=1, default=str)
            else:
                for r in rows:
                    f.write(json.dumps({c: json_value(_py(v)) for c, v in zip(cols, r)}, ensure_ascii=False, default=str) + "\n")
    elif ext in (".xlsx", ".xlsm"):
        from _fastxlsx import write_table

        cols = list(rel.columns)
        types = [str(t).upper() for t in rel.types]
        fmts = []
        for t in types:
            if t.startswith("TIMESTAMP"):
                fmts.append("yyyy-mm-dd hh:mm")
            elif t == "DATE":
                fmts.append("yyyy-mm-dd")
            else:
                fmts.append(None)

        def gen() -> Any:
            while True:
                batch = rel.fetchmany(10000)
                if not batch:
                    break
                for r in batch:
                    yield [_py(v) for v in r]

        write_table(out, "Result", cols, gen(), fmts)
    else:
        raise UsageError("--out must end in .csv, .tsv, .json, .jsonl, .parquet or .xlsx")
    return n


if __name__ == "__main__":
    run_main(main)
