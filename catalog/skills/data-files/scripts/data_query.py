#!/usr/bin/env python3
"""SQL (DuckDB) over any mix of data files: CSV/TSV, JSON/JSONL, Parquet, Arrow/Feather, Avro, XML, YAML/TOML/INI,
SQLite and DuckDB databases, SPSS/Stata/SAS files."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, emit, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_query.py sales.csv                                   # first rows (a pager)
  python3 scripts/data_query.py sales.csv --sql "SELECT region, sum(amount) AS total FROM sales GROUP BY 1 ORDER BY 2 DESC"
  python3 scripts/data_query.py orders.parquet customers.csv --sql "SELECT c.name, count(*) FROM orders o JOIN customers c USING (customer_id) GROUP BY 1"
  python3 scripts/data_query.py 'logs/*.jsonl' --sql "SELECT _file, count(*) FROM logs GROUP BY 1"
  python3 scripts/data_query.py --as a=v1.csv --as b=v2.csv --sql "SELECT * FROM a EXCEPT SELECT * FROM b"
  python3 scripts/data_query.py shop.sqlite --tables
  python3 scripts/data_query.py big.csv --sql "SELECT * FROM big WHERE amount > 1000" --out hits.parquet
  python3 scripts/data_query.py big.csv --sql "SELECT * FROM big ORDER BY amount DESC" --limit 100 --offset 100
  python3 scripts/data_query.py big.csv --rows 10001-10500 --format csv        # a slice by row number
  python3 scripts/data_query.py big.csv --find "ACME" --in customer,notes       # which rows mention it (row numbers)

Tables: each file is a table named after its file name without the extension (sales-2024.csv → sales_2024,
v1.2.parquet → v1_2; with one input also `t`);
--as name=path picks the name; a glob or folder is one table with a _file column. A SQLite or DuckDB file exposes
its tables (with one database, unqualified too: SELECT * FROM albums; otherwise db.albums). DuckDB SQL also reads
files directly: SELECT * FROM 'other.csv'. Big inputs are cached as Parquet, so repeated queries are fast.
Output: Markdown for small results, CSV for bigger ones (--format to choose); --out writes every row to .csv .tsv
.json .jsonl .parquet .arrow .avro .sqlite .duckdb .xml .yaml .md .sav .dta .xpt (.gz/.zst for text).
"""


def main() -> int:
    from _duck import add_read_args

    p = parser("Run DuckDB SQL over data files (any mix of formats), with paging, EXPLAIN and exports.", EPILOG)
    p.add_argument("files", nargs="*", help="input files, globs ('data/*.csv') or folders")
    p.add_argument("--sql", "-q", help="the query (DuckDB SQL); default: SELECT * FROM the single input")
    p.add_argument("--sql-file", help="read the query from a .sql file")
    p.add_argument("--as", dest="aliases", action="append", default=[], metavar="NAME=PATH", help="register a file under a table name (repeatable)")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="bind $NAME in the query (repeatable; JSON values allowed)")
    p.add_argument("--tables", action="store_true", help="list the tables with their columns, types and row counts")
    p.add_argument("--explain", action="store_true", help="show the query plan instead of running it")
    p.add_argument("--analyze", action="store_true", help="run the query and show the plan with timings (EXPLAIN ANALYZE)")
    p.add_argument("--limit", type=int, default=None, help="rows to print (default 50 for Markdown, 1000 for CSV/JSON; --out gets all)")
    p.add_argument("--offset", type=int, default=0, help="skip this many result rows (paging)")
    p.add_argument("--rows", metavar="A-B", help="rows A to B (1-based, e.g. 10001-10500): the same as --offset A-1 --limit B-A+1")
    p.add_argument("--find", metavar="TEXT", help="only rows where some column contains TEXT (case-insensitive); adds their row number (_row) and the matching columns (_match)")
    p.add_argument("--regex", action="store_true", help="--find TEXT is a regular expression (RE2 syntax)")
    p.add_argument("--case-sensitive", action="store_true", help="--find matches case")
    p.add_argument("--in", dest="find_in", metavar="COLS", help="--find only in these columns (comma-separated)")
    p.add_argument("--out", "-o", help="write the whole result to a file (format from the extension)")
    p.add_argument("--to", help="output format when the extension is unusual")
    p.add_argument("--force", action="store_true", help="replace an existing --out file")
    p.add_argument("--format", choices=["auto", "md", "csv", "tsv", "json", "jsonl"], default="auto", help="printed format (default auto: Markdown up to 50 rows, CSV above)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="printed output budget (default 60000)")
    p.add_argument("--max-cell", type=int, default=200, help="truncate longer cell values in printed output (default 200)")
    add_read_args(p)
    a = p.parse_args()
    if not a.files and not a.aliases:
        raise UsageError("give at least one input file (or --as name=path)")
    sql = a.sql
    if a.sql_file:
        sql = Path(a.sql_file).read_text(encoding="utf-8")
    if a.rows:
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", a.rows)
        if not m or int(m.group(1)) < 1 or int(m.group(2)) < int(m.group(1)):
            raise UsageError("--rows takes A-B with 1 <= A <= B, e.g. 10001-10500")
        a.offset, a.limit = int(m.group(1)) - 1, int(m.group(2)) - int(m.group(1)) + 1
    if a.offset < 0 or (a.limit is not None and a.limit < 0):
        raise UsageError("--offset and --limit are 0 or more")
    t0 = time.time()
    return run(a, sql, t0)


def run(a: Any, sql: str | None, t0: float) -> int:
    from _duck import connect, duck_error, guard_sql, ident, load_all, parse_inputs, read_opts

    opts = read_opts(a)
    specs = parse_inputs(a.files, a.aliases)
    con = connect()
    if sql:
        guard_sql(con, sql)
    if a.tables or (not sql and not a.out and _is_database(specs)):
        loaded = load_all(con, specs, opts)
        return show_tables(con, loaded, a)
    loaded = load_all(con, specs, opts, sql)
    notes = [f"{ld.name}: {n}" for ld in loaded for n in ld.notes]
    if not sql:
        tables = [ld for ld in loaded if ld.kind != "database"]
        if len(tables) != 1:
            raise UsageError("give --sql (several inputs); see --tables for their names")
        sql = f"SELECT * FROM {ident(tables[0].name)}"
    if a.param and sql:
        from _duck import bind_params

        sql = bind_params(sql, _params(a.param))
    params: dict[str, Any] = {}
    if a.find:
        a.find_base = sql
        a.find_doc = next((ld for ld in loaded if ld.info.get("record_path")), None)
        sql = find_sql(con, sql, a, params)
        notes.append(f"rows containing {a.find!r}" + (f" in {a.find_in}" if a.find_in else "") + "; _row is the row number in " + ("the query result" if a.sql or a.sql_file else "the file") + ", _match the matching columns")
        rp = next((ld.info.get("record_path") for ld in loaded if ld.info.get("record_path")), None)
        if rp and not (a.sql or a.sql_file) and len(loaded) == 1:
            notes.append(f"row N is the record {rp}[N-1] (data_tree.py get FILE '{rp}[N-1]')")
    if a.explain or a.analyze:
        try:
            rows = con.execute(("EXPLAIN ANALYZE " if a.analyze else "EXPLAIN ") + sql, params or None).fetchall()
        except Exception as e:  # noqa: BLE001
            raise SkillError(_sql_error(con, e, loaded)) from None
        print("\n".join(str(r[-1]) for r in rows))
        return 0
    if a.out:
        from _out import out_format, write_table

        out = output_path(a.out, [p for ld in loaded for p in ld.paths], a.force)
        fmt, comp = out_format(out, a.to)
        try:
            if params:
                con.execute("CREATE TEMP TABLE __result AS " + sql, params)
                res = write_table(con, "SELECT * FROM __result", out, fmt, comp, opts={"table_name": out.stem})
            else:
                res = write_table(con, sql, out, fmt, comp, opts={"table_name": _table_name(out)})
        except (SkillError, UsageError):
            raise
        except Exception as e:  # noqa: BLE001
            raise SkillError(_sql_error(con, e, loaded)) from None
        info = {"output": str(out), "format": fmt, "rows": res["rows"], "seconds": round(time.time() - t0, 2), "notes": notes + res["notes"]}
        emit(info, "json" if a.format == "json" else "md", lambda r: f"Wrote {r['rows']} rows to {r['output']} ({r['format']}) in {r['seconds']}s." + "".join(f"\n- {n}" for n in r["notes"]))
        return 0
    try:
        rel = con.sql(sql, params=params) if params else con.sql(sql)
    except Exception as e:  # noqa: BLE001
        raise SkillError(_sql_error(con, e, loaded)) from None
    if rel is None:
        print("ok (statement executed; nothing to show)")
        return 0
    try:
        page(con, rel, a, notes, t0)
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (SkillError, UsageError)):
            raise
        raise SkillError(_sql_error(con, e, loaded)) from None
    return 0


def find_sql(con: Any, sql: str, a: Any, params: dict[str, Any]) -> str:
    """The rows of `sql` where a column matches --find, with their row number and the matching columns."""
    from _duck import ident, sql_str

    try:
        rel = con.sql(f"SELECT * FROM ({sql}) LIMIT 0", params=params or None)
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"SQL error: {str(e).splitlines()[0]}") from None
    cols = list(rel.columns)
    if a.find_in:
        want = [c.strip() for c in a.find_in.split(",") if c.strip()]
        lower = {c.lower(): c for c in cols}
        missing = [w for w in want if w.lower() not in lower]
        if missing:
            raise UsageError(f"--in: no column {missing[0]!r}; columns: {', '.join(cols[:40])}")
        search = [lower[w.lower()] for w in want]
    else:
        search = cols
    if a.regex:
        try:
            re.compile(a.find)
        except re.error as e:
            raise UsageError(f"bad --find regex: {e}") from None
        opt = "" if a.case_sensitive else ", 'i'"
        conds = [f"regexp_matches(CAST({ident(c)} AS VARCHAR), {sql_str(a.find)}{opt})" for c in search]
    elif a.case_sensitive:
        conds = [f"contains(CAST({ident(c)} AS VARCHAR), {sql_str(a.find)})" for c in search]
    else:
        conds = [f"contains(lower(CAST({ident(c)} AS VARCHAR)), {sql_str(a.find.lower())})" for c in search]
    match = "array_to_string(list_filter([" + ", ".join(f"CASE WHEN {cond} THEN {sql_str(c)} END" for c, cond in zip(search, conds)) + "], x -> x IS NOT NULL), ', ')"
    return f"SELECT __row AS _row, {match} AS _match, * EXCLUDE (__row) FROM (SELECT row_number() OVER () AS __row, * FROM ({sql})) WHERE {' OR '.join(conds)}"


def _q(s: str) -> str:
    from _out import _quote

    return _quote(s)


def _table_name(out: Path) -> str:
    from _duck import sanitize, stem_of

    return sanitize(stem_of(out))


def _is_database(specs: Any) -> bool:
    from _formats import sniff

    return len(specs) == 1 and len(specs[0].paths) == 1 and sniff(specs[0].paths[0])["format"] in ("sqlite", "duckdb")


def _params(items: list[str]) -> dict[str, Any]:
    import json

    out: dict[str, Any] = {}
    for it in items:
        if "=" not in it:
            raise UsageError(f"--param needs NAME=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip().lstrip("$")] = json.loads(v)
        except ValueError:
            out[k.strip().lstrip("$")] = v
    return out


def page(con: Any, rel: Any, a: Any, notes: list[str], t0: float) -> None:
    from _out import command, render_rows

    fmt = a.format
    limit = a.limit
    if limit is None:
        limit = 50 if fmt == "md" else 1000 if fmt != "auto" else 1000
    cols = list(rel.columns)
    from _out import display_relation, parse_json_cells

    shown_rel, json_cols = display_relation(rel)
    rows = shown_rel.limit(limit + 1, offset=a.offset).fetchall() if limit else shown_rel.fetchall()
    more_than_limit = limit and len(rows) > limit
    rows = rows[:limit] if limit else rows
    if fmt == "auto":
        fmt = "md" if len(rows) <= 50 and len(cols) <= 30 else "csv"
    total = None
    if more_than_limit or a.offset:
        try:
            total = int(con.sql(f"SELECT count(*) FROM ({rel.sql_query()})").fetchone()[0])
        except Exception:  # noqa: BLE001 — counting is best effort
            total = None
    else:
        total = a.offset + len(rows)
    text, shown = render_rows(cols, rows, fmt if fmt != "json" else "json", a.max_chars, a.max_cell)
    secs = round(time.time() - t0, 2)
    first, last = a.offset + 1, a.offset + shown
    nxt = None
    if shown < len(rows) or more_than_limit:
        nxt = command({"--offset": a.offset + shown, **({"--limit": a.limit} if a.rows else {})}, drop=["--rows"])
    if a.format == "json":
        import json

        from _out import jsonable

        out = {"columns": cols, "types": [str(t) for t in rel.types], "rows": [parse_json_cells(dict(zip(cols, (jsonable(v) for v in r))), json_cols) for r in rows[:shown]], "offset": a.offset, "shown": shown, "total": total, "seconds": secs, "notes": notes}
        if nxt:
            out["next"] = nxt
        print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
        return
    if rows or not getattr(a, "find_base", None):
        print(text)
    tot = f"{total} rows" if total is not None else "more rows"
    span = f"rows {first}-{last} of {tot}" if shown else f"no rows (of {tot})"
    if not shown and getattr(a, "find_base", None):
        try:
            searched = f"{int(con.sql(f'SELECT count(*) FROM ({a.find_base})').fetchone()[0]):,} rows, "
        except Exception:  # noqa: BLE001 — counting is best effort
            searched = ""
        footer = [f"[no row contains {a.find!r}{' past this offset' if a.offset else ''} (searched {searched}{len(cols) - 2} columns) · {secs}s]"]
    else:
        footer = [f"[{span} × {len(cols)} columns · {secs}s]"]
    doc = getattr(a, "find_doc", None)
    if not shown and doc is not None and doc.info.get("record_path") not in (None, "$"):
        footer.append(f"[only the records at {doc.info['record_path']} were searched; the whole document: python3 scripts/data_tree.py find {_q(str(doc.path))} {_q(a.find)} --values]")
    for n in notes:
        footer.append(f"[{n}]")
    if nxt:
        left = f"{total - last} more rows" if total is not None else "more rows"
        footer.append(f"[… {left}. Next: {nxt}  (or --out FILE for all rows)]")
    print("\n".join(footer))


def show_tables(con: Any, loaded: list[Any], a: Any) -> int:
    from _duck import columns_of, count_rows, ident
    from _out import command

    items = []
    for ld in loaded:
        if ld.kind == "database":
            for t in ld.tables:
                q = f"{ident(ld.name)}." + ".".join(ident(x) for x in t.split(".", 1))
                if ld.fmt == "sqlite":
                    q = f"{ident(ld.name)}.{ident(t)}"
                try:
                    cols = columns_of(con, q)
                    n = count_rows(con, q)
                except Exception as e:  # noqa: BLE001
                    cols, n = [], None
                    ld.notes.append(f"{t}: {e}")
                items.append({"table": f"{ld.name}.{t}" if len(loaded) > 1 else t, "rows": n, "columns": [{"name": c, "type": ty} for c, ty in cols], "source": str(ld.path)})
        else:
            cols = columns_of(con, ident(ld.name))
            items.append({"table": ld.name, "rows": count_rows(con, ident(ld.name)), "columns": [{"name": c, "type": ty} for c, ty in cols], "source": ", ".join(str(p) for p in ld.paths[:3]) + (" …" if len(ld.paths) > 3 else ""), "notes": ld.notes})

    def render(d: dict[str, Any]) -> str:
        lines = []
        for it in d["tables"]:
            cols = ", ".join(f"{c['name']} {c['type']}" for c in it["columns"])
            lines.append(f"- **{it['table']}** ({it['rows']} rows) from {it['source']}: {cols}")
            for n in it.get("notes") or []:
                lines.append(f"  - {n}")
        lines.append("")
        lines.append("Query with: " + command({"--sql": "SELECT …"}, drop=["--tables"]))
        return "\n".join(lines)

    emit({"tables": items}, "json" if a.format == "json" else "md", render, a.max_chars)
    return 0


def _sql_error(con: Any, e: Exception, loaded: list[Any]) -> str:
    from _duck import columns_of, duck_error, ident

    msg = duck_error(e)
    names = []
    for ld in loaded:
        if ld.kind == "database":
            names += [f"{t}" for t in ld.tables[:30]]
            continue
        try:
            cols = [c for c, _ in columns_of(con, ident(ld.name))]
        except Exception:  # noqa: BLE001
            cols = []
        shown = ", ".join(cols[:25]) + (", …" if len(cols) > 25 else "")
        names.append(f"{ld.name}({shown})")
    return f"SQL error: {msg}\ntables: {'; '.join(names) or '(none loaded: name tables after their files, or use --as)'}"


if __name__ == "__main__":
    run_main(main)
