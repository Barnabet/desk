"""Typed columnar copies of sheets (Parquet, cached by file content) and the workbook map built from them.

Every skill_run is a new process: without this, each read, search or query of a 200 000-row sheet would parse the
whole workbook again. The first call streams the sheet once (calamine, or DuckDB's CSV reader for big CSV files),
types each column, writes `data.parquet` plus `meta.json` (header row, column types, empty share, min/max, head
and tail samples, rows left out) into the file cache (_cache.py), and later calls read those in milliseconds.

Rows keep their sheet address: every Parquet copy has a `_row` column (the sheet row number; for CSV files the
record number, counting the header as row 1), so SQL results, searches and pages can name `Sheet!B12`.

Rows that are not data are left out and listed in meta["excluded"]: totals rows of Excel tables, and a last row
that is labelled Total/Grand total. Rows above the header (titles, notes) are kept in meta["preamble"].
"""

from __future__ import annotations

import atexit
import csv
import datetime as _dt
import json
import os
import re
import shutil
import tempfile
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterator

from _a1 import MAX_COL, MAX_ROW, col_letter
from _common import SkillError

STORE_VERSION = "3"
#: Sheets with more data rows than this get a map (not a dump) by default.
BIG_ROWS = 5000
#: CSV files bigger than this are typed by DuckDB instead of Python's csv module.
BIG_CSV_BYTES = 8 << 20
CHUNK_ROWS = 4096

_RELEASE: list[Path] = []
_TEMPS: list[Path] = []


def _cleanup() -> None:
    from _cache import release

    for d in _RELEASE:
        release(d)
    for d in _TEMPS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def temp_dir(prefix: str = "desk-sheets-") -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMPS.append(d)
    return d


def connect() -> Any:
    """An in-memory DuckDB connection that writes only to temp dirs and never downloads extensions."""
    import duckdb

    work = temp_dir("desk-duck-")
    threads = max(1, min(int(os.environ.get("DESK_MAX_WORKERS", "4") or 4) * 2, os.cpu_count() or 2, 8))
    con = duckdb.connect(":memory:", config={
        "autoinstall_known_extensions": False, "autoload_known_extensions": False,
        "extension_directory": str(work / "ext"), "temp_directory": str(work / "spill"),
        "threads": threads, "memory_limit": os.environ.get("DESK_DUCKDB_MEMORY", "1GB"),
    })
    con.execute("SET enable_progress_bar = false")
    return con


def sql_str(s: str | os.PathLike[str]) -> str:
    text = str(s)
    if os.name == "nt":
        text = text.replace("\\", "/")
    return "'" + text.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# ── header detection ────────────────────────────────────────────────────


def find_header_row(rows: list[list[Any]], mode: str = "auto") -> int | None:
    """Index of the header row among the first rows of a block: 0 normally; later when title or note lines sit
    above a full-width header (Report title / blank / Region, Units, …). None when there is no header."""
    from _book import detect_header

    if mode == "no" or not rows:
        return None
    if mode == "yes":
        return 0
    n = min(len(rows), 30)
    widths = [sum(1 for v in r if v is not None and v != "") for r in rows[:n]]
    typical = sorted(widths)[len(widths) // 2] if widths else 0
    for i in range(min(10, n - 1)):
        if widths[i] == 0:
            continue
        if typical >= 2 and widths[i] * 2 <= typical and i + 1 < n:
            continue  # a title or note line above the table
        return i if detect_header(rows[i:]) else None
    return None


_TOTAL_LABEL = re.compile(r"^\s*(grand\s+)?(sub)?totals?\s*:?\s*$|^\s*(total|sum)\b.{0,20}$", re.I)


# ── typing ──────────────────────────────────────────────────────────────


class _ColStats:
    __slots__ = ("types", "nulls", "errors", "integral", "has_time")

    def __init__(self) -> None:
        self.types: Counter[type] = Counter()
        self.nulls = 0
        self.errors = 0
        self.integral = True
        self.has_time = False


def _sql_type(st: _ColStats) -> str:
    from _book import ErrorText

    kinds = {t for t, n in st.types.items() if n and t is not type(None) and not issubclass(t, ErrorText)}
    if not kinds:
        return "VARCHAR"
    norm = set()
    for k in kinds:
        if k is bool:
            norm.add("bool")
        elif issubclass(k, bool):
            norm.add("bool")
        elif issubclass(k, int):
            norm.add("int")
        elif issubclass(k, float):
            norm.add("float")
        elif k is _dt.datetime:
            norm.add("datetime")
        elif k is _dt.date:
            norm.add("date")
        elif k is _dt.time:
            norm.add("time")
        else:
            return "VARCHAR"
    if norm <= {"int", "float"}:
        return "BIGINT" if st.integral else "DOUBLE"
    if norm == {"bool"}:
        return "BOOLEAN"
    if norm == {"date"}:
        return "DATE"
    if norm <= {"date", "datetime"}:
        return "TIMESTAMP" if st.has_time else "DATE"
    if norm == {"time"}:
        return "TIME"
    return "VARCHAR"


def _cell_text(v: Any) -> Any:
    """What the typing CSV holds for a value (None → empty)."""
    t = type(v)
    if v is None or t is str:
        return v
    if t is float:
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if t is int:
        return str(v)
    if t is bool:
        return "true" if v else "false"
    if t is _dt.datetime:
        return v.isoformat(sep=" ")
    if t is _dt.date or t is _dt.time:
        return v.isoformat()
    if t is _dt.timedelta:
        from _book import text_value

        return text_value(v)
    if hasattr(v, "code"):
        return str(v)
    if isinstance(v, float):
        return repr(float(v))
    return str(v)


def _short(v: Any, n: int = 60) -> str:
    from _book import text_value

    s = text_value(v)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ── building a store ────────────────────────────────────────────────────


def _row_source(path: Path, sheet: str | None, area: tuple[int, int, int, int] | None) -> tuple[dict[str, Any], Iterator[tuple[int, list[Any]]]]:
    """(info, iterator of (sheet row number, values)) for a sheet, streaming where the format allows."""
    from _book import ErrorText, Workbook, norm, trim_grid

    wb = Workbook(path)
    info: dict[str, Any] = {"kind": wb.kind}
    if wb.kind in ("csv", "json", "html"):
        g = wb.grid(sheet, area)
        rows = g.rows if area else trim_grid(g.rows)
        info.update(sheet=g.sheet, first_row=g.row1, first_col=g.col1, width=max((len(r) for r in rows), default=0), merged=g.merged)
        if wb.kind == "csv":
            info["csv"] = wb.csv_dialect
        wb.close()
        return info, ((g.row1 + i, r) for i, r in enumerate(rows))
    meta = wb.resolve_sheet(sheet)
    info["sheet"] = meta.name
    if meta.kind != "worksheet":
        wb.close()
        raise SkillError(f"'{meta.name}' is a {meta.kind}, not a worksheet")
    st = wb.scan(meta) if wb.kind == "xlsx" else None
    info["scan"] = {k: st.get(k) for k in ("cells", "formulas", "dimension", "features")} if st else None
    from _scan import is_sparse

    if area is not None or (st and is_sparse(st)):
        g = wb.grid(meta.name, area)
        rows = g.rows
        if area is None:
            rows = trim_grid(rows)
        info.update(first_row=g.row1, first_col=g.col1, width=max((len(r) for r in rows), default=0), merged=g.merged,
                    outliers=[[a, _short(v)] for a, v in g.outliers], outlier_count=g.outlier_count, extent=g.extent)
        wb.close()
        return info, ((g.row1 + i, r) for i, r in enumerate(rows))
    try:
        sh = wb._cal.get_sheet_by_name(meta.name)
        start = sh.start
    except Exception:  # noqa: BLE001 — fall back to the XML reader through grid()
        g = wb.grid(meta.name)
        rows = trim_grid(g.rows)
        info.update(first_row=g.row1, first_col=g.col1, width=max((len(r) for r in rows), default=0), merged=g.merged)
        wb.close()
        return info, ((g.row1 + i, r) for i, r in enumerate(rows))
    if start is None:
        info.update(first_row=1, first_col=1, width=0, merged=[])
        wb.close()
        return info, iter(())
    r0, c0 = start[0] + 1, start[1] + 1
    try:
        merged = [f"{col_letter(a[1] + 1)}{a[0] + 1}:{col_letter(b[1] + 1)}{b[0] + 1}" for a, b in (sh.merged_cell_ranges or [])]
    except Exception:  # noqa: BLE001
        merged = []
    errors: dict[int, list[tuple[int, str]]] = {}
    for r, c, code in (st or {}).get("errors") or []:
        errors.setdefault(r, []).append((c, code))
    info.update(first_row=r0, first_col=c0, width=sh.width, merged=merged)

    def gen() -> Iterator[tuple[int, list[Any]]]:
        try:
            for i, raw in enumerate(sh.iter_rows()):
                r = r0 + i
                row = [None if v == "" else v for v in raw]
                if r in errors:
                    for c, code in errors[r]:
                        j = c - c0
                        if 0 <= j < len(row):
                            row[j] = ErrorText(code)
                yield r, row
        finally:
            wb.close()

    return info, gen()


def _build(tmp: Path, path: Path, sheet: str | None, header: str, area: tuple[int, int, int, int] | None, keep_totals: bool) -> None:
    from _book import detect_header, text_value, unique_headers  # noqa: F401

    info, source = _row_source(path, sheet, area)
    width = info.get("width") or 0
    c0 = info.get("first_col") or 1
    # header detection on the first rows
    first: list[tuple[int, list[Any]]] = []
    for item in source:
        first.append(item)
        if len(first) >= 30:
            break
    rows30 = [r for _, r in first]
    width = max(width, max((len(r) for r in rows30), default=0))
    h = find_header_row(rows30, header)
    preamble = [[rn, [_short(v) for v in r]] for rn, r in first[: h or 0]] if h else []
    if h is not None:
        header_row = first[h][0]
        head_vals = rows30[h] + [None] * (width - len(rows30[h]))
        names = unique_headers([" ".join(text_value(v).split()) if v is not None else None for v in head_vals])
        data_first = first[h + 1 :]
    else:
        header_row = None
        names = [col_letter(c0 + j) for j in range(width)]
        data_first = first
    # rows to leave out: totals rows of Excel tables on this sheet
    excluded: dict[int, str] = {}
    tables_here: list[dict[str, Any]] = []
    if info["kind"] == "xlsx":
        try:
            from _xlsx import Package

            with Package(path) as pkg:
                for sname, t, _part in pkg.tables():
                    if sname != info["sheet"]:
                        continue
                    r1, tc1, r2, tc2 = t.r1, t.c1, t.r2, t.c2
                    tables_here.append({"name": t.name, "ref": f"{col_letter(tc1)}{r1}:{col_letter(tc2)}{r2}", "totals_row": bool(t.totals_rows), "header_rows": t.header_rows, "columns": t.columns})
                    if t.totals_rows and not keep_totals:
                        for rr in range(r2 - t.totals_rows + 1, r2 + 1):
                            excluded[rr] = f"totals row of table {t.name}"
        except Exception:  # noqa: BLE001 — tables are an extra
            pass
    names_used = {n.lower() for n in names}
    row_col = "_row" if "_row" not in names_used else "_row_number"
    # stream the data rows into a typing CSV
    work = temp_dir("desk-store-")
    csv_path = work / "data.csv"
    stats = [_ColStats() for _ in range(width)]
    head: list[list[Any]] = []
    tail: deque[list[Any]] = deque(maxlen=6)
    n = 0
    first_written = 0
    last_row_seen = header_row or 0
    pending: list[list[Any]] = []
    fixups: dict[int, str] = {}  # column index → 'td' (timedelta) or 'mixed' (numbers or dates inside text)
    NoneType = type(None)

    def all_rows() -> Iterator[tuple[int, list[Any]]]:
        yield from data_first
        yield from source

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(names + [row_col])
        chunk: list[list[Any]] = []  # rows with their sheet row number appended

        def flush() -> None:
            if not chunk:
                return
            cols = list(zip(*chunk))
            for j in range(width):
                col = cols[j]
                s = stats[j]
                cnt = Counter(map(type, col))
                empty = col.count("") if str in cnt else 0
                if empty:
                    cnt[str] -= empty
                    if not cnt[str]:
                        del cnt[str]
                s.nulls += cnt.pop(NoneType, 0) + empty
                s.types.update(cnt)
                if not cnt:
                    continue
                for t in cnt:
                    if t is not str and hasattr(t, "code"):
                        s.errors += cnt[t]
                if s.integral and any(t is float or (t is not bool and issubclass(t, float)) for t in cnt):
                    vals = col if len(cnt) == 1 and float in cnt and not empty else [v for v in col if isinstance(v, float)]
                    try:
                        s.integral = all(map(float.is_integer, vals)) and max(vals) < 2**53 and min(vals) > -(2**53)
                    except (TypeError, ValueError):
                        s.integral = False
                if _dt.datetime in cnt and not s.has_time:
                    s.has_time = any(v.hour or v.minute or v.second or v.microsecond for v in col if type(v) is _dt.datetime)
                if _dt.timedelta in cnt:
                    fixups[j] = "td"
            if fixups:
                from _book import text_value

                for r in chunk:
                    for j in fixups:
                        v = r[j]
                        if type(v) is _dt.timedelta:
                            r[j] = text_value(v)
            w.writerows(chunk)
            chunk.clear()

        for rn, row in all_rows():
            if len(row) != width:
                row = list(row[:width]) + [None] * (width - len(row)) if len(row) < width else list(row[:width])
            if rn in excluded:
                continue
            if row.count("") + row.count(None) >= width:
                pending.append([*row, rn])
                continue
            if pending:
                chunk.extend(pending)
                n += len(pending)
                pending = []
            row.append(rn)
            chunk.append(row)
            n += 1
            if not first_written:
                first_written = rn
            last_row_seen = rn
            if len(head) < 5:
                head.append([rn] + [_short(v) for v in row[:width]])
            tail.append(row)
            if len(chunk) >= CHUNK_ROWS:
                flush()
        # a last row labelled "Total" under numbers is a totals row, not data
        if tail and not keep_totals and header_row is not None:
            last = tail[-1]
            rn, vals = last[width], last[:width]
            label = next((v for v in vals if v is not None and v != ""), None)
            if isinstance(label, str) and _TOTAL_LABEL.match(label) and n > 2 and any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                if chunk and chunk[-1] is last:
                    chunk.pop()
                    n -= 1
                    excluded[rn] = f"row labelled {label.strip()!r} (a totals row)"
                    tail.pop()
                    last_row_seen = tail[-1][width] if tail else (header_row or 0)
                    if head and head[-1][0] == rn:
                        head.pop()
        flush()
    types = [_sql_type(s) for s in stats]
    # DuckDB: typed Parquet with the sheet row number
    con = connect()
    cols_sql = ", ".join(f"{sql_str(nm)}: 'VARCHAR'" for nm in names) + (", " if names else "") + f"{sql_str(row_col)}: 'BIGINT'"
    sel = []
    for j, (nm, t) in enumerate(zip(names, types)):
        q = ident(nm)
        if t != "VARCHAR":
            sel.append(f"TRY_CAST({q} AS {t}) AS {q}")
        elif {float, _dt.datetime} & set(stats[j].types):
            # text columns holding numbers or dates: 12 not 12.0, 2024-01-31 not 2024-01-31 00:00:00
            sel.append(f"regexp_replace(regexp_replace({q}, '^(-?\\d+)\\.0$', '\\1'), '^(\\d{{4}}-\\d{{2}}-\\d{{2}}) 00:00:00$', '\\1') AS {q}")
        else:
            sel.append(q)
    sel.append(ident(row_col))
    src = f"read_csv({sql_str(csv_path)}, header=true, auto_detect=false, delim=',', quote='\"', escape='\"', columns={{{cols_sql}}}, null_padding=true)"
    out = tmp / "data.parquet"
    con.execute(f"COPY (SELECT {', '.join(sel)} FROM {src}) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)")
    shutil.rmtree(work, ignore_errors=True)
    # column statistics in one pass
    colinfo = []
    aggs = []
    for nm, t in zip(names, types):
        q = ident(nm)
        if t in ("BIGINT", "DOUBLE", "DATE", "TIMESTAMP", "TIME"):
            aggs.append(f"CAST(min({q}) AS VARCHAR), CAST(max({q}) AS VARCHAR)")
        else:
            aggs.append(f"NULL, NULL")
        aggs.append(f"approx_count_distinct({q})")
    row_stats = con.execute(f"SELECT {', '.join(aggs)} FROM read_parquet({sql_str(out)})").fetchone() if names else ()
    for j, (nm, t) in enumerate(zip(names, types)):
        s = stats[j]
        lo, hi, distinct = (row_stats[3 * j], row_stats[3 * j + 1], row_stats[3 * j + 2]) if row_stats else (None, None, None)
        ci = {"name": nm, "letter": col_letter(c0 + j), "type": t, "empty": s.nulls, "errors": s.errors, "min": lo, "max": hi, "distinct": distinct}
        names_of = {k.__name__ for k in s.types}
        if t in ("DOUBLE", "BIGINT") and "Percent" in names_of:
            ci["format"] = "0.0%"
        elif t in ("DOUBLE", "BIGINT") and "Currency" in names_of:
            ci["format"] = "$#,##0.00"
        colinfo.append(ci)
    con.close()
    meta = {
        "version": STORE_VERSION, "source": str(path), "kind": info["kind"], "sheet": info.get("sheet"),
        "header_row": header_row, "header_detected": h is not None, "first_col": c0, "last_col": c0 + max(width, 1) - 1,
        "first_row": first_written if n else None, "last_row": last_row_seen if n else None,
        "rows": n, "columns": colinfo, "row_column": row_col, "preamble": preamble, "head": head,
        "tail": [[r[width]] + [_short(v) for v in r[:width]] for r in list(tail)[-5:]],
        "excluded": [{"row": r, "why": why} for r, why in sorted(excluded.items()) if (header_row or 0) < r],
        "merged": (info.get("merged") or [])[:50], "merged_count": len(info.get("merged") or []),
        "outliers": info.get("outliers") or [], "outlier_count": info.get("outlier_count") or 0, "extent": info.get("extent") or "",
        "tables": tables_here, "csv": info.get("csv"), "area": list(area) if area else None,
    }
    if info.get("scan"):
        meta["features"] = (info["scan"] or {}).get("features") or {}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")


def _build_big_csv(tmp: Path, path: Path, header: str) -> None:
    """A big CSV typed by DuckDB: read as text, then every column is checked against the same rules as the Python
    reader (integers without leading zeros, decimals, dates, booleans, 12%, $1,234.50, European 1.234,5)."""
    from _book import _DEC_COMMA, decode_text, detect_header, infer_value

    with open(path, "rb") as fh:
        sample = fh.read(1 << 20)
    text, enc = decode_text(sample)
    encoding = "utf-16" if enc.startswith("utf-16") else ("latin-1" if enc in ("cp1252", "latin-1") else "utf-8")
    if path.suffix.lower() in (".tsv", ".tab"):
        delim = "\t"
    else:
        try:
            delim = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    lines = list(csv.reader(text.splitlines()[:60], delimiter=delim))
    decimal = "," if delim != "," and any(_DEC_COMMA.match(x.strip()) for r in lines[1:] for x in r) else "."
    typed = [[infer_value(x, decimal) for x in r] for r in lines]
    has_header = header == "yes" or (header == "auto" and detect_header(typed))
    con = connect()
    src = (f"read_csv({sql_str(path)}, delim={sql_str(delim)}, header={'true' if has_header else 'false'}, all_varchar=true, "
           f"encoding={sql_str(encoding)}, null_padding=true)")
    # a table keeps the file's order: its rowid is the record index
    con.execute(f"CREATE TABLE raw AS SELECT * FROM {src}")
    cols = [r[0] for r in con.execute("DESCRIBE raw").fetchall()]
    from _book import unique_headers

    names = unique_headers(cols) if has_header else [col_letter(j + 1) for j in range(len(cols))]
    # per-column pattern counts, one scan
    pats = {
        "int": r"^[+-]?(0|[1-9]\d{0,17})$",
        "float": r"^[+-]?((0|[1-9]\d*)(\.\d*)?|\.\d+)([eE][+-]?\d+)?$",
        "date": r"^\d{4}-\d{2}-\d{2}$",
        "ts": r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$",
        "bool": r"^(?i:true|false)$",
        "pct": r"^[+-]?\d+(\.\d+)?\s?%$" if decimal == "." else r"^[+-]?\d+(,\d+)?\s?%$",
        "money": r"^-?[$€£¥]\s?-?\d{1,3}(,\d{3})*(\.\d+)?$|^-?[$€£¥]\s?-?\d+(\.\d+)?$",
        "thousands": r"^-?\d{1,3}(,\d{3})+(\.\d+)?$",
        "eu": r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$",
    }
    aggs = []
    for c in cols:
        q = ident(c)
        aggs.append(f"count({q})")
        for k, rx in pats.items():
            aggs.append(f"count(*) FILTER (WHERE regexp_full_match(trim({q}), {sql_str(rx)}))")
    counts = con.execute(f"SELECT {', '.join(aggs)} FROM raw").fetchone()
    k = len(pats) + 1
    exprs = []
    types = []
    formats: list[str | None] = []
    for j, c in enumerate(cols):
        q = f"trim({ident(c)})"
        got = dict(zip(["nn", *pats], counts[j * k : (j + 1) * k]))
        nn = got["nn"]
        if nn == 0:
            t, e = "VARCHAR", ident(c)
        elif decimal == "," and got["eu"] == nn and got["int"] < nn:
            t, e = "DOUBLE", f"TRY_CAST(replace(replace({q}, '.', ''), ',', '.') AS DOUBLE)"
        elif got["int"] == nn:
            t, e = "BIGINT", f"TRY_CAST({q} AS BIGINT)"
        elif got["int"] + got["float"] == nn:
            t, e = "DOUBLE", f"TRY_CAST({q} AS DOUBLE)"
        elif got["date"] == nn:
            t, e = "DATE", f"TRY_CAST({q} AS DATE)"
        elif got["date"] + got["ts"] == nn:
            t, e = "TIMESTAMP", f"TRY_CAST(replace({q}, 'T', ' ') AS TIMESTAMP)"
        elif got["bool"] == nn:
            t, e = "BOOLEAN", f"lower({q}) = 'true'"
        elif got["pct"] == nn:
            t, e = "DOUBLE", f"TRY_CAST(replace(replace(replace({q}, '%', ''), ' ', ''), ',', '.') AS DOUBLE) / 100"
        elif got["money"] and got["money"] + got["int"] + got["float"] + got["thousands"] == nn:
            t, e = "DOUBLE", f"TRY_CAST(regexp_replace({q}, '[$€£¥,\\s]', '', 'g') AS DOUBLE)"
        elif got["thousands"] and got["thousands"] + got["int"] + got["float"] == nn:
            t, e = "DOUBLE", f"TRY_CAST(replace({q}, ',', '') AS DOUBLE)"
        else:
            t, e = "VARCHAR", ident(c)
        types.append(t)
        formats.append("0.0%" if got["pct"] == nn and nn else ("$#,##0.00" if got["money"] and t == "DOUBLE" else ("#,##0.##" if got["thousands"] and t == "DOUBLE" else None)))
        exprs.append(f"{e} AS {ident(names[j])}")
    row_col = "_row" if "_row" not in {nm.lower() for nm in names} else "_row_number"
    out = tmp / "data.parquet"
    rowexpr = "rowid + 2" if has_header else "rowid + 1"
    con.execute(f"COPY (SELECT {', '.join(exprs)}, CAST({rowexpr} AS BIGINT) AS {ident(row_col)} FROM raw ORDER BY rowid) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)")
    con.execute("DROP TABLE raw")
    aggs = []
    for nm, t in zip(names, types):
        q = ident(nm)
        mm = f"CAST(min({q}) AS VARCHAR), CAST(max({q}) AS VARCHAR)" if t != "VARCHAR" and t != "BOOLEAN" else "NULL, NULL"
        aggs.append(f"{mm}, count(*) - count({q}), approx_count_distinct({q})")
    st = con.execute(f"SELECT count(*), {', '.join(aggs)} FROM read_parquet({sql_str(out)})").fetchone()
    nrows = st[0]
    colinfo = []
    for j, (nm, t) in enumerate(zip(names, types)):
        lo, hi, empty, distinct = st[1 + 4 * j : 5 + 4 * j]
        ci = {"name": nm, "letter": col_letter(j + 1), "type": t, "empty": empty, "errors": 0, "min": lo, "max": hi, "distinct": distinct}
        if formats[j]:
            ci["format"] = formats[j]
        colinfo.append(ci)
    headrows = con.execute(f"SELECT * FROM read_parquet({sql_str(out)}) ORDER BY {ident(row_col)} LIMIT 5").fetchall()
    tailrows = con.execute(f"SELECT * FROM (SELECT * FROM read_parquet({sql_str(out)}) ORDER BY {ident(row_col)} DESC LIMIT 5) ORDER BY {ident(row_col)}").fetchall()
    con.close()
    first_row = 2 if has_header else 1
    meta = {
        "version": STORE_VERSION, "source": str(path), "kind": "csv", "sheet": path.stem,
        "header_row": 1 if has_header else None, "header_detected": has_header, "first_col": 1, "last_col": max(len(cols), 1),
        "first_row": first_row if nrows else None, "last_row": (first_row + nrows - 1) if nrows else None, "rows": nrows,
        "columns": colinfo, "row_column": row_col, "preamble": [], "head": [[r[-1]] + [_short(v) for v in r[:-1]] for r in headrows],
        "tail": [[r[-1]] + [_short(v) for v in r[:-1]] for r in tailrows], "excluded": [], "merged": [], "merged_count": 0,
        "outliers": [], "outlier_count": 0, "extent": "", "tables": [],
        "csv": {"delimiter": delim, "encoding": enc, "decimal": decimal}, "area": None, "typed_by": "duckdb",
    }
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")


def sheet_store(path: Path, sheet: str | None = None, header: str = "auto", area: tuple[int, int, int, int] | None = None, keep_totals: bool = False) -> dict[str, Any]:
    """The cached store of one sheet (building it the first time): meta.json's content plus 'parquet' and 'dir'."""
    from _book import kind_of
    from _cache import cached_dir

    kind = kind_of(path)
    big_csv = kind == "csv" and area is None and path.stat().st_size > BIG_CSV_BYTES
    params = {"sheet": sheet, "header": header, "area": list(area) if area else None, "totals": keep_totals, "big_csv": big_csv}

    def build(tmp: Path) -> None:
        if big_csv:
            try:
                _build_big_csv(tmp, path, header)
                return
            except Exception as e:  # noqa: BLE001 — odd dialects: the Python reader handles them
                for f in tmp.iterdir():
                    f.unlink()
                if os.environ.get("DESK_DEBUG"):
                    raise
                del e
        _build(tmp, path, sheet, header, area, keep_totals)

    d = cached_dir(path, "sheet-store", params, STORE_VERSION, build)
    _RELEASE.append(d)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["parquet"] = str(d / "data.parquet")
    meta["dir"] = str(d)
    return meta


def worksheet_names(path: Path) -> list[str | None]:
    from _book import Workbook

    with Workbook(path) as wb:
        if wb.kind in ("csv", "json"):
            return [None]
        return [m.name for m in wb.sheets() if m.kind == "worksheet"]


# ── reading back ────────────────────────────────────────────────────────


def page(con: Any, meta: dict[str, Any], r1: int, r2: int, cols: list[int] | None = None) -> list[tuple[int, list[Any]]]:
    """Data rows r1..r2 (sheet row numbers) as (row, values) from the Parquet copy."""
    names = [c["name"] for c in meta["columns"]]
    if cols is not None:
        names = [names[j] for j in cols if 0 <= j < len(names)]
    rc = meta["row_column"]
    sel = ", ".join(ident(n) for n in names) or "NULL"
    rows = con.execute(f"SELECT {ident(rc)}, {sel} FROM read_parquet({sql_str(meta['parquet'])}) WHERE {ident(rc)} BETWEEN ? AND ? ORDER BY {ident(rc)}", [r1, r2]).fetchall()
    return [(r[0], list(r[1:])) for r in rows]


def find(con: Any, meta: dict[str, Any], needle: str, regex: bool = False, limit: int = 100, case: bool = False) -> tuple[list[dict[str, Any]], int]:
    """Cells whose text contains needle (or matches the regex): [{'cell', 'row', 'col', 'value'}], total count."""
    cols = meta["columns"]
    if not cols:
        return [], 0
    rc = ident(meta["row_column"])
    parts = []
    for j, c in enumerate(cols):
        parts.append(f"SELECT {rc} AS r, {j} AS j, CAST({ident(c['name'])} AS VARCHAR) AS v FROM read_parquet({sql_str(meta['parquet'])})")
    union = " UNION ALL ".join(parts)
    if regex:
        cond = "regexp_matches(v, ?)" if case else "regexp_matches(v, ?, 'i')"
    else:
        cond = "contains(v, ?)" if case else "contains(lower(v), lower(?))"
    total = con.execute(f"SELECT count(*) FROM ({union}) WHERE v IS NOT NULL AND {cond}", [needle]).fetchone()[0]
    hits = con.execute(f"SELECT r, j, v FROM ({union}) WHERE v IS NOT NULL AND {cond} ORDER BY r, j LIMIT {int(limit)}", [needle]).fetchall()
    out = []
    for r, j, v in hits:
        letter = cols[j]["letter"]
        out.append({"cell": f"{letter}{r}", "row": r, "col": j, "column": cols[j]["name"], "value": v})
    return out, total


def fmt_count(n: int) -> str:
    return f"{n:,}"


def column_line(c: dict[str, Any], rows: int) -> list[str]:
    empty = c.get("empty") or 0
    share = f"{100 * empty / rows:.0f}%" if rows else "-"
    if rows and 0 < empty < rows and share == "0%":
        share = "<1%"
    rng = ""
    if c.get("min") is not None and c.get("max") is not None:
        lo, hi = str(c["min"]), str(c["max"])
        if c["type"] == "DOUBLE":
            try:
                lo, hi = f"{float(lo):.6g}", f"{float(hi):.6g}"
            except ValueError:
                pass
        if c["type"] == "TIMESTAMP":
            lo, hi = lo.replace(" 00:00:00", ""), hi.replace(" 00:00:00", "")
        rng = f"{lo} … {hi}"
    kind = {"BIGINT": "integer", "DOUBLE": "number", "VARCHAR": "text", "DATE": "date", "TIMESTAMP": "date-time", "BOOLEAN": "true/false", "TIME": "time"}.get(c["type"], c["type"])
    extra = f"{c['errors']} errors" if c.get("errors") else ""
    distinct = f"≈{c['distinct']:,}" if isinstance(c.get("distinct"), int) else ""
    return [c["letter"], c["name"], kind, share, distinct, rng or "", extra]


def render_map(path: Path, metas: list[dict[str, Any]], why: str = "") -> str:
    """The workbook map: per sheet its data block, header, columns (type, empty share, distinct, range), head and
    tail rows, rows left out, and the commands to go further."""
    from _common import md_table

    name = path.name
    lines = [f"# {name}: map" + (f" ({why})" if why else "")]
    for m in metas:
        sheet = m.get("sheet") or path.stem
        ncols = len(m["columns"])
        c1, c2 = m["first_col"], m["last_col"]
        rows = m["rows"]
        block = ""
        if rows:
            top = m["header_row"] or m["first_row"]
            block = f"{col_letter(c1)}{top}:{col_letter(c2)}{m['last_row']}"
        lines.append("")
        lines.append(f"## {sheet}" + (f": {block}" if block else ": empty") + f", {fmt_count(rows)} data rows × {ncols} columns")
        facts = []
        if m.get("header_row"):
            facts.append(f"header in row {m['header_row']}; data from row {m['first_row']}")
        elif rows:
            facts.append("no header row detected: columns are named by letter (--header yes to force)")
        feats = m.get("features") or {}
        if feats.get("frozen_at"):
            facts.append(f"frozen at {feats['frozen_at']}")
        if feats.get("autofilter"):
            facts.append(f"filter {feats['autofilter']}")
        if m.get("merged_count"):
            facts.append(f"{m['merged_count']} merged range{'s' if m['merged_count'] != 1 else ''}")
        if facts:
            text = "; ".join(facts)
            lines.append(text[:1].upper() + text[1:] + ".")
        for t in m.get("tables") or []:
            lines.append(f"Excel table {t['name']} {t['ref']}" + (" with a totals row" if t.get("totals_row") else "") + f" (SQL table {sanitize(t['name'])}).")
        if m.get("preamble"):
            pre = [(r, " · ".join(v for v in vals if v)[:120]) for r, vals in m["preamble"][:5]]
            pre = [(r, t) for r, t in pre if t]
            if pre:
                lines.append("Above the header: " + " / ".join(f"row {r}: {t}" for r, t in pre))
        if m.get("excluded"):
            lines.append("Left out of the data: " + "; ".join(f"row {e['row']} ({e['why']})" for e in m["excluded"][:10]))
        if m.get("outlier_count"):
            ol = ", ".join(f"{a} = {v}" for a, v in m["outliers"][:10])
            lines.append(f"Used range {m.get('extent')}, but {m['outlier_count']} cell(s) sit far outside the data block: {ol}" + (" …" if m["outlier_count"] > 10 else "") + ". Read them with --range.")
        if ncols:
            lines.append("")
            cl = [column_line(c, rows) for c in m["columns"]]
            hdr = ["col", "name", "type", "empty", "distinct", "range", "notes"]
            if not any(r[-1] for r in cl):
                hdr, cl = hdr[:-1], [r[:-1] for r in cl]
            lines.append(md_table(hdr, cl))
        if m.get("head"):
            show = min(ncols, 12)
            letters = [f"{c['letter']}: {c['name']}" for c in m["columns"][:show]]
            lines.append("")
            lines.append("First rows:" if rows > 5 else "Rows:")
            lines.append(md_table(["row"] + letters, [[str(r[0])] + r[1 : show + 1] for r in m["head"]]))
            if rows > 5 and m.get("tail"):
                lines.append("Last rows:")
                lines.append(md_table(["row"] + letters, [[str(r[0])] + r[1 : show + 1] for r in m["tail"] if r[0] not in {h[0] for h in m["head"]}]))
            if ncols > show:
                lines.append(f"({ncols - show} more columns: " + ", ".join(c["name"] for c in m["columns"][show:])[:400] + ")")
    return "\n".join(lines)


def sanitize(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]+", "_", str(name).strip()).strip("_").lower()
    if not s:
        s = "t"
    if s[0].isdigit():
        s = "t_" + s
    return s


def area_columns(meta: dict[str, Any], c1: int, c2: int) -> list[int]:
    """Column indexes of the store that fall in sheet columns c1..c2."""
    base = meta["first_col"]
    return [j for j in range(len(meta["columns"])) if c1 <= base + j <= c2]


def covers(meta: dict[str, Any], area: tuple[int, int, int, int]) -> bool:
    """True when an area lies inside the store's data block (rows and columns), so it can be served from Parquet."""
    if not meta.get("rows"):
        return False
    r1, c1, r2, c2 = area
    return r1 >= (meta["first_row"] or 1) and (r2 <= meta["last_row"] or r2 >= MAX_ROW) and c1 >= meta["first_col"] and (c2 <= meta["last_col"] or c2 >= MAX_COL)
