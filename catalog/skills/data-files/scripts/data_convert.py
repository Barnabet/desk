#!/usr/bin/env python3
"""Convert data files between formats, reshaping on the way: select, rename, cast, filter, sort, dedupe, flatten
or nest, re-encode, recompress and split. Documents (JSON, YAML, TOML, XML, INI) convert structure for structure."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, emit, human_size, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_convert.py sales.csv sales.parquet
  python3 scripts/data_convert.py export.json export.csv                     # nested objects → dotted columns
  python3 scripts/data_convert.py flat.csv nested.json --nest                # a.b, a.c columns → {"a": {"b", "c"}}
  python3 scripts/data_convert.py legacy.csv clean.csv --encoding cp1252 --bom --out-delimiter ';' --decimal-comma
  python3 scripts/data_convert.py orders.csv fr.csv --where "country = 'FR'" --select id,amount,day --sort "day DESC"
  python3 scripts/data_convert.py raw.csv typed.parquet --cast amount=DECIMAL(12,2) --cast 'day=DATE:%d/%m/%Y|%Y-%m-%d'
  python3 scripts/data_convert.py people.csv unique.csv --dedupe-on email --rename email=mail
  python3 scripts/data_convert.py big.csv parts/big.csv --split-rows 100000      # parts/big-0001.csv, …
  python3 scripts/data_convert.py sales.csv by_region/sales.csv --split-by region # by_region/sales-North.csv, …
  python3 scripts/data_convert.py config.yaml config.toml                        # document conversion
  python3 scripts/data_convert.py 'logs/*.jsonl' logs.parquet                    # many files → one table
  python3 scripts/data_convert.py *.csv --to parquet --out-dir parquet/          # batch, in parallel
  python3 scripts/data_convert.py shop.sqlite orders.csv --table orders
  python3 scripts/data_convert.py shop.sqlite shop.duckdb                        # every table
  python3 scripts/data_convert.py orders.csv people.parquet 'logs/*.jsonl' all.duckdb   # one table per input

Outputs: .csv .tsv .psv .json .jsonl .parquet .arrow/.feather .avro .sqlite .duckdb .xml .yaml .toml .ini .md
.sav .zsav .dta .xpt (add .gz or .zst to text formats). All column options name the input's columns.
Order of operations: --where, --cast, --select/--exclude, --explode, --dedupe, --sort, --limit, --rename.
"""

DOC_FORMATS = ("json", "yaml", "toml", "xml", "ini")


def main() -> int:
    from _duck import add_read_args

    p = parser("Convert and reshape data files: any of CSV/TSV, JSON/JSONL, Parquet, Arrow, Avro, SQLite, DuckDB, XML, YAML, TOML, INI, SPSS/Stata/SAS.", EPILOG)
    p.add_argument("paths", nargs="+", help="INPUT OUTPUT, or several inputs with --out-dir")
    p.add_argument("--to", help="output format (default: from the output extension)")
    p.add_argument("--out-dir", help="batch mode: convert every input into this folder (needs --to)")
    p.add_argument("--force", action="store_true", help="replace existing outputs")
    p.add_argument("--mode", choices=["auto", "table", "doc"], default="auto", help="doc: JSON/YAML/TOML/XML/INI structure for structure; table: rows (default auto)")
    g = p.add_argument_group("reshaping (column names are the input's)")
    g.add_argument("--select", help="keep these columns, in this order (comma-separated)")
    g.add_argument("--exclude", help="drop these columns")
    g.add_argument("--rename", action="append", default=[], metavar="OLD=NEW", help="rename a column (repeatable)")
    g.add_argument("--cast", action="append", default=[], metavar="COL=TYPE", help="cast: INTEGER, DOUBLE, DECIMAL(12,2), VARCHAR, BOOLEAN, DATE, TIMESTAMP, or DATE:%%d/%%m/%%Y (formats separated by |)")
    g.add_argument("--lenient", action="store_true", help="values that do not cast become null (counted) instead of failing")
    g.add_argument("--where", help="keep rows matching this SQL condition")
    g.add_argument("--sort", help="ORDER BY clause, e.g. 'day DESC, id'")
    g.add_argument("--dedupe", action="store_true", help="drop exact duplicate rows (keeps the first)")
    g.add_argument("--dedupe-on", help="keep the first row per value of these columns")
    g.add_argument("--limit", type=int, help="keep the first N rows")
    g.add_argument("--sql", help="full custom query instead of the options above (the input is table t and its stem)")
    g.add_argument("--flatten", action="store_true", help="nested objects → dotted columns, lists → JSON text (automatic for CSV/TSV)")
    g.add_argument("--nest", action="store_true", help="dotted columns (a.b) → nested objects")
    g.add_argument("--explode", help="one row per element of this list column")
    o = p.add_argument_group("output options")
    o.add_argument("--out-delimiter", help="CSV output separator (default , for .csv, tab for .tsv)")
    o.add_argument("--no-header", action="store_true", help="CSV output without a header row")
    o.add_argument("--bom", action="store_true", help="start CSV output with a UTF-8 BOM (Excel opens it as UTF-8)")
    o.add_argument("--out-encoding", help="CSV output encoding, e.g. cp1252 (default utf-8)")
    o.add_argument("--decimal-comma", action="store_true", help="CSV output numbers with a decimal comma (use with --out-delimiter ';')")
    o.add_argument("--quote-all", action="store_true", help="quote every CSV field")
    o.add_argument("--null-text", help="CSV text for null (default empty)")
    o.add_argument("--date-format", help="CSV date format, e.g. %%d/%%m/%%Y")
    o.add_argument("--timestamp-format", help="CSV timestamp format")
    o.add_argument("--codec", help="Parquet: zstd (default), snappy, gzip, lz4, brotli, none; Arrow: lz4, zstd, none; Avro: deflate (default), snappy, zstd, bzip2, xz, none")
    o.add_argument("--row-group-size", type=int, help="Parquet rows per row group (default 122880)")
    o.add_argument("--table-name", help="table name in SQLite/DuckDB/XPT outputs, key for TOML (default: output stem)")
    o.add_argument("--xml-root", help="XML root element (default rows / root)")
    o.add_argument("--xml-row", help="XML row element (default row)")
    o.add_argument("--indent", type=int, default=2, help="documents: indentation (default 2)")
    o.add_argument("--minify", action="store_true", help="documents: compact JSON/XML")
    o.add_argument("--sort-keys", action="store_true", help="documents: sort object keys")
    o.add_argument("--drop-nulls", action="store_true", help="documents to TOML: leave out nulls (TOML has none)")
    s = p.add_argument_group("splitting (outputs become NAME-<part>.EXT next to OUTPUT; {n} or {value} in the name to choose)")
    s.add_argument("--split-rows", type=int, help="at most this many rows per file")
    s.add_argument("--split-size", help="about this size per file, e.g. 50MB")
    s.add_argument("--split-by", help="one file per value of this column")
    p.add_argument("--format", choices=["md", "json"], default="md", help="report format")
    add_read_args(p)
    a = p.parse_args()
    t0 = time.time()
    if a.out_dir:
        if not a.to:
            raise UsageError("--out-dir needs --to FORMAT")
        return batch(a, t0)
    if len(a.paths) < 2:
        raise UsageError("give INPUT OUTPUT (or several inputs with --out-dir DIR --to FORMAT, or several inputs and one .duckdb/.sqlite output)")
    if len(a.paths) > 2:
        res = pack(a, a.paths[:-1], a.paths[-1])
    else:
        res = convert(a, a.paths[0], a.paths[1])
    res["seconds"] = round(time.time() - t0, 2)
    emit(res, a.format, render)
    return 0


def render(r: dict[str, Any]) -> str:
    if r.get("mode") == "pack":
        lines = [f"Wrote {len(r['tables'])} tables to {r['output']} ({r['to']}, {r['size']}) in {r['seconds']}s:"]
        lines += [f"- {t}: {n:,} rows" for t, n in r["tables"].items()]
        lines += [f"- {n}" for n in r.get("notes", [])]
        lines.append(f"Check: python3 scripts/data_info.py {r['output']}")
        return "\n".join(lines)
    if r.get("mode") == "doc":
        lines = [f"Converted {r['input']} ({r['from']}) → {r['output']} ({r['to']}, {r['size']}) in {r['seconds']}s."]
    elif r.get("parts"):
        lines = [f"Wrote {r['rows']:,} rows into {len(r['parts'])} files ({r['to']}) in {r['seconds']}s:"]
        for part in r["parts"][:30]:
            lines.append(f"- {part['path']}: {part['rows']:,} rows" + (f" ({part['value']})" if "value" in part else ""))
        if len(r["parts"]) > 30:
            lines.append(f"- … {len(r['parts']) - 30} more")
    else:
        lines = [f"Wrote {r['rows']:,} rows × {r['columns']} columns to {r['output']} ({r['to']}, {r['size']}) in {r['seconds']}s."]
        if r.get("input_rows") is not None and r["input_rows"] != r["rows"]:
            lines.append(f"- input had {r['input_rows']:,} rows")
    for n in r.get("notes", []):
        lines.append(f"- {n}")
    target = r.get("output") or (r["parts"][0]["path"] if r.get("parts") else None)
    if target:
        lines.append(f"Check: python3 scripts/data_info.py {target}")
    return "\n".join(lines)


def batch(a: Any, t0: float) -> int:
    from _common import pool_map
    from _duck import is_glob

    import glob

    inputs: list[str] = []
    for pth in a.paths:
        inputs += sorted(glob.glob(pth)) if is_glob(pth) and not Path(pth).exists() else [pth]
    outdir = output_dir(a.out_dir)
    from _out import OUT_EXT

    ext = next((e for e, f in OUT_EXT.items() if f == a.to.lower()), "." + a.to.lower())
    jobs = [(vars(a), i, str(outdir / (Path(i).name.split(".")[0] + ext))) for i in inputs]
    results = pool_map(_batch_one, jobs)
    ok = [r for r in results if "error" not in r]
    lines = [f"Converted {len(ok)} of {len(results)} files to {a.to} in {round(time.time() - t0, 2)}s ({outdir}):"]
    for r in results:
        lines.append(f"- {r['input']} → " + (f"{r['output']} ({r.get('rows', '?'):,} rows)" if "error" not in r else f"error: {r['error']}"))
    print("\n".join(lines))
    return 0 if len(ok) == len(results) else 1


def _batch_one(job: tuple[dict[str, Any], str, str]) -> dict[str, Any]:
    import argparse

    args, src, dst = job
    a = argparse.Namespace(**args)
    try:
        return convert(a, src, dst)
    except (SkillError, UsageError) as e:
        return {"input": src, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"input": src, "error": f"{type(e).__name__}: {e}"}


# ── one conversion ──────────────────────────────────────────────────────


def _table_opts(a: Any) -> bool:
    return any([a.select, a.exclude, a.rename, a.cast, a.where, a.sort, a.dedupe, a.dedupe_on, a.limit, a.sql, a.flatten, a.nest, a.explode, a.split_rows, a.split_size, a.split_by])


def pack(a: Any, inputs: list[str], dst: str) -> dict[str, Any]:
    """Several inputs (or every table of a database) into one SQLite or DuckDB file, one table each."""
    from _duck import connect, ident, load, parse_inputs, read_opts, sql_str
    from _out import out_format, write_database

    if _table_opts(a):
        raise UsageError("reshaping options apply to one table: convert the inputs one at a time, or build the table with data_query --out")
    opts = read_opts(a)
    out = Path(dst).expanduser()
    fmt, _ = out_format(out, a.to)
    if fmt not in ("sqlite", "duckdb"):
        raise UsageError("several inputs go into one .duckdb or .sqlite file (a table each); for separate files use --out-dir DIR --to FORMAT")
    out = output_path(out, [p for p in inputs if Path(p).exists()], a.force)
    con = connect()
    tables: list[tuple[str, str]] = []
    notes: list[str] = []
    used: set[str] = set()
    for spec in parse_inputs(inputs):
        ld = load(con, spec, opts)
        if ld.kind == "database":
            want = [t for t in ld.tables if not opts.get("db_table") or t.lower() == opts["db_table"].lower()]
            views = {o["name"] for o in ld.info.get("catalog", []) if o.get("type") == "view"}
            if ld.fmt == "duckdb":
                views |= {r[0] if r[1] == "main" else f"{r[1]}.{r[0]}" for r in con.execute(f"SELECT view_name, schema_name FROM duckdb_views() WHERE database_name = {sql_str(ld.name)} AND NOT internal").fetchall()}
            for t in want:
                src = f"{ident(ld.name)}." + ".".join(ident(x) for x in t.split(".", 1))
                name = t if len(inputs) == 1 else f"{ld.name}_{t.replace('.', '_')}"
                tables.append((_unique(name, used), f"SELECT * FROM {src}"))
            if views & set(want):
                notes.append(f"{ld.path.name}: views copied as tables ({', '.join(sorted(views & set(want)))})")
            notes.append(f"{ld.path.name}: data copied; indexes, keys and constraints are not")
        else:
            tables.append((_unique(ld.name, used), f"SELECT * FROM {ident(ld.name)}"))
        notes += [f"{ld.name}: {n}" for n in ld.notes]
    if not tables:
        raise SkillError("no tables to write")
    rows = write_database(con, tables, out, fmt)
    return {"mode": "pack", "output": str(out), "to": fmt, "tables": rows, "size": human_size(out.stat().st_size), "notes": notes}


def _unique(name: str, used: set[str]) -> str:
    base, k = name, 2
    while name.lower() in used:
        name = f"{base}_{k}"
        k += 1
    used.add(name.lower())
    return name


def convert(a: Any, src: str, dst: str) -> dict[str, Any]:
    from _duck import is_glob, read_opts
    from _formats import sniff
    from _out import out_format

    opts = read_opts(a)
    split = a.split_rows or a.split_size or a.split_by
    out = Path(dst).expanduser()
    fmt, comp = out_format(out, a.to)
    single = not is_glob(src) and Path(src).is_file()
    in_fmt = sniff(Path(src))["format"] if single else None
    if in_fmt in ("sqlite", "duckdb") and fmt in ("sqlite", "duckdb") and not opts.get("db_table") and not _table_opts(a):
        return pack(a, [src], dst)
    if single and not split:
        out = output_path(out, [src], a.force)
    if in_fmt == "sav" and fmt in ("sav", "zsav"):
        opts = {**opts, "user_missing": True}  # SPSS to SPSS: the codes and their missing ranges both survive
    mode = a.mode
    if mode == "auto":
        mode = "doc" if in_fmt in DOC_FORMATS and fmt in DOC_FORMATS and not _table_opts(a) and not opts.get("json_path") and not opts.get("record") else "table"
    if mode == "doc":
        if in_fmt not in DOC_FORMATS + ("jsonl",):
            raise UsageError(f"--mode doc needs a JSON, YAML, TOML, XML or INI input (this is {in_fmt})")
        if fmt not in DOC_FORMATS + ("jsonl",):
            raise UsageError(f"--mode doc writes JSON, YAML, TOML, XML or INI (not {fmt})")
        return convert_doc(a, Path(src), in_fmt, out, fmt, comp)
    return convert_table(a, src, out, fmt, comp, opts)


def convert_doc(a: Any, src: Path, in_fmt: str, out: Path, fmt: str, comp: str | None) -> dict[str, Any]:
    from _tree import dump_doc, load_doc

    doc = load_doc(src, in_fmt, a.encoding)
    notes = []
    if fmt == "toml" and not isinstance(doc, dict):
        notes.append(f"TOML needs a table at the top: the {type(doc).__name__} is under key '{a.table_name or 'items'}'")
    text = dump_doc(doc, fmt, indent=None if a.minify else a.indent, sort=a.sort_keys, xml_root=a.xml_root or (a.table_name if fmt == "toml" else None), xml_item=a.xml_row or "item", nulls="drop" if a.drop_nulls else "error")
    from _out import _open_compressed

    with _open_compressed(out, comp) as f:
        f.write(text.encode("utf-8"))
    if in_fmt == "xml":
        notes.append("XML attributes are @keys and mixed text is #text (see references/formats.md)")
    if fmt == "xml" and in_fmt != "xml":
        bad = sorted(_bad_xml_keys(doc))
        if bad:
            notes.append("keys that are not XML names were adjusted (" + ", ".join(repr(k) for k in bad[:6]) + (" …" if len(bad) > 6 else "") + "; spaces and other characters → _)")
    return {"mode": "doc", "input": str(src), "from": in_fmt, "output": str(out), "to": fmt, "size": human_size(out.stat().st_size), "notes": notes}


def _bad_xml_keys(obj: Any, out: set[str] | None = None) -> set[str]:
    """Object keys that xml_name has to change (not @attr / #text markers)."""
    from _tree import xml_name

    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            name = key[1:] if key.startswith("@") else key
            if key not in ("#text",) and not key.startswith("@xmlns") and xml_name(name) != name:
                out.add(key)
            _bad_xml_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _bad_xml_keys(v, out)
    return out


def _cols(spec: str | None) -> list[str]:
    return [c.strip() for c in (spec or "").split(",") if c.strip()]


def _check_cols(names: list[str], have: list[str], what: str) -> None:
    low = {h.lower() for h in have}
    for n in names:
        if n.lower() not in low:
            raise UsageError(f"{what}: no column {n!r}; columns: {', '.join(have[:40])}")


def _cast_expr(col: str, spec: str, lenient: bool) -> str:
    from _duck import ident, sql_str

    q = ident(col)
    m = re.fullmatch(r"(?i)(DATE|TIMESTAMP)\s*:\s*(.+)", spec.strip())
    if m:
        kind, fmts = m.group(1).upper(), [f for f in m.group(2).split("|") if f]
        lst = "[" + ", ".join(sql_str(f) for f in fmts) + "]"
        fn = "try_strptime" if lenient or len(fmts) > 1 else "strptime"
        expr = f"{fn}(CAST({q} AS VARCHAR), {lst})"
        return f"CAST({expr} AS {kind})"
    typ = spec.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?(\[\])?", typ):
        raise UsageError(f"bad type {spec!r} for --cast {col}")
    if typ.upper() in ("BOOL", "BOOLEAN"):
        return f"CASE WHEN lower(trim(CAST({q} AS VARCHAR))) IN ('true','t','yes','y','1','oui','ja','si') THEN true WHEN lower(trim(CAST({q} AS VARCHAR))) IN ('false','f','no','n','0','non','nein') THEN false ELSE {'NULL' if lenient else f'CAST({q} AS BOOLEAN)'} END"
    return f"{'TRY_CAST' if lenient else 'CAST'}({q} AS {typ})"


def build_sql(con: Any, a: Any, table: str) -> tuple[str, list[str]]:
    from _duck import columns_of, guard_sql, ident

    notes: list[str] = []
    if a.sql:
        guard_sql(con, a.sql, "the OUTPUT argument")
        return a.sql, notes
    have = [c for c, _ in columns_of(con, table)]
    sql = f"SELECT * FROM {table}"
    if a.where:
        sql += f" WHERE {a.where}"
    casts = []
    for c in a.cast:
        if "=" not in c:
            raise UsageError(f"--cast needs COL=TYPE, got {c!r}")
        col, typ = c.split("=", 1)
        _check_cols([col.strip()], have, "--cast")
        casts.append((col.strip(), typ))
    if casts:
        reps = ", ".join(f"{_cast_expr(col, typ, a.lenient)} AS {ident(col)}" for col, typ in casts)
        base_sql = sql
        sql = f"SELECT * REPLACE ({reps}) FROM ({sql})"
        ints = [(col, typ) for col, typ in casts if re.fullmatch(r"(?i)\s*(U?(TINY|SMALL|BIG|HUGE)?INT(EGER)?|INT[1248]|LONG|SHORT)\s*", typ)]
        if ints:
            # DuckDB rounds text such as '7.5' to 8 when casting to an integer: say so, with an example.
            checks = ", ".join(f"count(*) FILTER (WHERE TRY_CAST(CAST({ident(c)} AS VARCHAR) AS DOUBLE) != trunc(TRY_CAST(CAST({ident(c)} AS VARCHAR) AS DOUBLE))), min(CAST({ident(c)} AS VARCHAR)) FILTER (WHERE TRY_CAST(CAST({ident(c)} AS VARCHAR) AS DOUBLE) != trunc(TRY_CAST(CAST({ident(c)} AS VARCHAR) AS DOUBLE)))" for c, _ in ints)
            row = con.sql(f"SELECT {checks} FROM ({base_sql})").fetchone()
            for i, (c, typ) in enumerate(ints):
                n, ex = row[2 * i], row[2 * i + 1]
                if n:
                    notes.append(f"{n:,} value{'s' if n != 1 else ''} of {c} had decimals and {'were' if n != 1 else 'was'} rounded to whole numbers (e.g. {ex}); cast to DECIMAL or DOUBLE to keep them")
        if a.lenient:
            checks = ", ".join(f"count(*) FILTER (WHERE {ident(col)} IS NOT NULL AND {_cast_expr(col, typ, True)} IS NULL)" for col, typ in casts)
            base = f"SELECT * FROM {table}" + (f" WHERE {a.where}" if a.where else "")
            failed = con.sql(f"SELECT {checks} FROM ({base})").fetchone()
            for (col, typ), n in zip(casts, failed):
                if n:
                    notes.append(f"{n:,} values of {col} did not fit {typ} and became null")
    if a.select:
        cols = _cols(a.select)
        _check_cols(cols, have, "--select")
        sql = f"SELECT {', '.join(ident(c) for c in cols)} FROM ({sql})"
    if a.exclude:
        cols = _cols(a.exclude)
        _check_cols(cols, have, "--exclude")
        sql = f"SELECT * EXCLUDE ({', '.join(ident(c) for c in cols)}) FROM ({sql})"
    if a.explode:
        _check_cols([a.explode], have, "--explode")
        sql = f"SELECT * REPLACE (unnest({ident(a.explode)}) AS {ident(a.explode)}) FROM ({sql})"
    if a.dedupe or a.dedupe_on:
        cur = columns_of(con, f"({sql})")
        keys = _cols(a.dedupe_on) if a.dedupe_on else [c for c, _ in cur]
        if a.dedupe_on:
            _check_cols(keys, [c for c, _ in cur], "--dedupe-on")
        part = ", ".join(ident(k) for k in keys)
        order = f"ORDER BY {a.sort}" if a.sort and a.dedupe_on else "ORDER BY __rn"
        sql = f"SELECT * EXCLUDE (__rn) FROM (SELECT *, row_number() OVER () AS __rn FROM ({sql})) QUALIFY row_number() OVER (PARTITION BY {part} {order}) = 1 ORDER BY __rn"
    if a.sort:
        sql = f"SELECT * FROM ({sql}) ORDER BY {a.sort}"
    if a.limit is not None:
        sql = f"SELECT * FROM ({sql}) LIMIT {int(a.limit)}"
    if a.rename:
        pairs = []
        for r in a.rename:
            if "=" not in r:
                raise UsageError(f"--rename needs OLD=NEW, got {r!r}")
            old, new = r.split("=", 1)
            _check_cols([old.strip()], have, "--rename")
            pairs.append(f"{ident(old.strip())} AS {ident(new.strip())}")
        sql = f"SELECT * RENAME ({', '.join(pairs)}) FROM ({sql})"
    return sql, notes


def convert_table(a: Any, src: str, out: Path, fmt: str, comp: str | None, opts: dict[str, Any]) -> dict[str, Any]:
    from _duck import connect, duck_error, load_all, one_table, parse_inputs, sanitize, stem_of
    from _out import flat_select, nest_select, write_table

    con = connect()
    specs = parse_inputs([src])
    loaded = load_all(con, specs, opts)
    ld = loaded[0]
    table = one_table(con, ld, opts)
    notes = list(ld.notes)
    try:
        sql, more = build_sql(con, a, table)
        notes += more
        if a.flatten:
            sql = flat_select(con, sql)
        if a.nest:
            sql = nest_select(con, sql)
        con.sql(f"SELECT * FROM ({sql}) LIMIT 0")
    except UsageError:
        raise
    except Exception as e:  # noqa: BLE001
        from _duck import columns_of

        cols = ", ".join(c for c, _ in columns_of(con, table))
        raise SkillError(f"SQL error: {duck_error(e)}\ncolumns: {cols[:1500]}") from None
    wopts = {
        "delimiter": _delim(a.out_delimiter), "no_header": a.no_header, "bom": a.bom, "out_encoding": a.out_encoding,
        "decimal_comma": a.decimal_comma, "quote_all": a.quote_all, "null_text": a.null_text, "date_format": a.date_format,
        "timestamp_format": a.timestamp_format, "codec": a.codec, "row_group_size": a.row_group_size,
        "table_name": a.table_name or sanitize(stem_of(out)), "xml_root": a.xml_root, "xml_row": a.xml_row,
    }
    sm = ld.info.get("stat_meta") or {}
    if sm.get("column_labels") and fmt in ("sav", "zsav", "dta", "xpt"):
        wopts["column_labels"] = sm["column_labels"]
    if sm.get("value_labels") and fmt in ("sav", "zsav", "dta") and not opts.get("labels"):
        wopts["value_labels"] = sm["value_labels"]
    if sm.get("missing_raw") and fmt in ("sav", "zsav") and opts.get("user_missing"):
        wopts["missing_ranges"] = sm["missing_raw"]
    elif sm.get("missing_raw") and opts.get("user_missing"):
        notes.append(f"{fmt} has no user-missing ranges: the SPSS missing codes are ordinary values in {out.name}")
    if fmt in ("csv", "tsv", "psv") and _has_nested(con, sql):
        notes.append("nested columns flattened: objects → dotted columns, lists → JSON text")
    input_rows = None
    try:
        input_rows = int(con.sql(f"SELECT count(*) FROM {table}").fetchone()[0])
    except Exception:  # noqa: BLE001
        pass
    if a.split_rows or a.split_size or a.split_by:
        return split(con, a, sql, out, fmt, comp, wopts, notes, src)
    try:
        res = write_table(con, sql, out, fmt, comp, opts=wopts)
    except (SkillError, UsageError):
        raise
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"conversion failed: {duck_error(e)}") from None
    ncols = len(con.sql(f"SELECT * FROM ({sql}) LIMIT 0").columns)
    return {"mode": "table", "input": src, "output": str(out), "to": fmt + (f"+{comp}" if comp else ""), "rows": res["rows"], "columns": ncols, "input_rows": input_rows, "size": human_size(out.stat().st_size), "notes": notes + res["notes"]}


def _delim(d: str | None) -> str | None:
    if not d:
        return None
    return {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}.get(d.lower(), d)


def _has_nested(con: Any, sql: str) -> bool:
    rel = con.sql(f"SELECT * FROM ({sql}) LIMIT 0")
    return any(t.id in ("struct", "list", "map", "array", "union") for t in rel.types)


def _size_bytes(s: str) -> int:
    m = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*(b|kb|k|mb|m|gb|g)?\s*", s)
    if not m:
        raise UsageError(f"bad size {s!r}; e.g. 50MB")
    mult = {"b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3}[(m.group(2) or "b").lower()]
    return int(float(m.group(1)) * mult)


def _part_path(out: Path, token: str, placeholder: str) -> Path:
    name = out.name
    if "{" + placeholder + "}" in name:
        return out.with_name(name.replace("{" + placeholder + "}", token))
    suffixes = "".join(out.suffixes[-2:]) if len(out.suffixes) > 1 and out.suffixes[-1].lower() in (".gz", ".zst") else out.suffix
    stem = name[: len(name) - len(suffixes)] if suffixes else name
    return out.with_name(f"{stem}-{token}{suffixes}")


def _check_part(path: Path, src: str, force: bool) -> None:
    from _common import same_file

    if Path(src).exists() and same_file(path, Path(src)):
        raise SkillError(f"refusing to overwrite the input {src} with a part; choose another output name")
    if path.exists() and not force:
        raise SkillError(f"{path} already exists; pass --force")


def split(con: Any, a: Any, sql: str, out: Path, fmt: str, comp: str | None, wopts: dict[str, Any], notes: list[str], src: str) -> dict[str, Any]:
    from _duck import ident
    from _out import write_table

    con.execute(f"CREATE TEMP TABLE __conv AS SELECT row_number() OVER () AS __rn, * FROM ({sql})")
    n = int(con.sql("SELECT count(*) FROM __conv").fetchone()[0])
    parts: list[dict[str, Any]] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.split_by:
        col = a.split_by
        have = con.sql("SELECT * FROM __conv LIMIT 0").columns
        if col not in have:
            match = [c for c in have if c.lower() == col.lower()]
            if not match:
                raise UsageError(f"--split-by: no column {col!r}")
            col = match[0]
        con.execute(f"CREATE TEMP TABLE __groups AS SELECT *, dense_rank() OVER (ORDER BY {ident(col)} NULLS LAST) AS __grp FROM __conv")
        groups = con.sql(f"SELECT DISTINCT __grp, {ident(col)} FROM __groups ORDER BY 1").fetchall()
        if len(groups) > 1000:
            raise SkillError(f"{col} has {len(groups):,} distinct values; split by a column with at most 1000")
        used: set[str] = set()
        for grp, v in groups:
            token = "null" if v is None else re.sub(r"[^\w.\-]+", "_", str(v)).strip("._") or "blank"
            base, k = token, 2
            while token.lower() in used:
                token = f"{base}_{k}"
                k += 1
            used.add(token.lower())
            path = _part_path(out, token, "value")
            _check_part(path, src, a.force)
            res = write_table(con, f"SELECT * EXCLUDE (__rn, __grp) FROM __groups WHERE __grp = {int(grp)} ORDER BY __rn", path, fmt, comp, opts=wopts)
            parts.append({"path": str(path), "rows": res["rows"], "value": v})
    else:
        per = a.split_rows
        if not per:
            target = _size_bytes(a.split_size)
            probe_n = min(n, 20000)
            import tempfile

            probe = Path(tempfile.mkdtemp(prefix="desk-split-")) / f"probe{out.suffix or '.bin'}"
            write_table(con, f"SELECT * EXCLUDE (__rn) FROM __conv ORDER BY __rn LIMIT {probe_n}", probe, fmt, comp, opts=wopts)
            per_row = max(1.0, probe.stat().st_size / max(1, probe_n))
            probe.unlink()
            per = max(1, int(target / per_row * 0.95))
            notes.append(f"about {per:,} rows per file for {a.split_size} (sizes are estimates)")
        count = max(1, (n + per - 1) // per)
        width = max(4, len(str(count)))
        for i in range(count):
            path = _part_path(out, str(i + 1).zfill(width), "n")
            _check_part(path, src, a.force)
            res = write_table(con, f"SELECT * EXCLUDE (__rn) FROM __conv WHERE __rn > {i * per} AND __rn <= {(i + 1) * per} ORDER BY __rn", path, fmt, comp, opts=wopts)
            parts.append({"path": str(path), "rows": res["rows"]})
    return {"mode": "table", "input": src, "to": fmt, "rows": n, "parts": parts, "notes": notes}


if __name__ == "__main__":
    run_main(main)
