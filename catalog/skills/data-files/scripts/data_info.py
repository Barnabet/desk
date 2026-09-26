#!/usr/bin/env python3
"""What a data file is and what is in it: format (sniffed from content), encoding, CSV dialect, row and column
counts, schema with types and null shares, sample rows; nested structure for JSON/YAML/TOML/XML; tables, views and
indexes of SQLite/DuckDB files; labels of SPSS/Stata/SAS files; Parquet/Arrow/Avro metadata."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, cap, human_size, input_file, md_table, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_info.py sales.csv
  python3 scripts/data_info.py export.json --depth 4          # structure of a nested document
  python3 scripts/data_info.py shop.sqlite                    # tables, views, indexes, row counts
  python3 scripts/data_info.py survey.sav --format json       # variables, labels, value labels
  python3 scripts/data_info.py data/*.parquet                 # several files at once

Big inputs get a map (schema, null shares, first and last rows with their row numbers) and the commands to read
further. Big CSV/JSON/XML/Avro/SPSS/Stata/SAS inputs are converted once to a cached Parquet copy, so later
data_query/data_profile calls on them are fast.
"""


def main() -> int:
    from _duck import add_read_args

    p = parser("Describe data files: format, encoding, dialect, schema, counts, samples, structure.", EPILOG)
    p.add_argument("files", nargs="+", help="data files")
    p.add_argument("--sample", type=int, default=5, help="sample rows to show (default 5; big tables show first and last rows)")
    p.add_argument("--depth", type=int, default=6, help="nesting depth of document outlines (default 6)")
    p.add_argument("--max-keys", type=int, default=40, help="keys shown per object in outlines (default 40)")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    add_read_args(p)
    a = p.parse_args()
    from _duck import read_opts

    opts = read_opts(a)
    results = []
    files: list[str] = []
    for f in a.files:
        if any(c in f for c in "*?[") and not Path(f).exists():
            import glob

            hits = sorted(h for h in glob.glob(str(Path(f).expanduser()), recursive=True) if Path(h).is_file())
            if not hits:
                raise SkillError(f"no files match {f}")
            files += hits
        else:
            files.append(f)
    for f in files:
        path = input_file(f)
        t0 = time.time()
        try:
            r = describe(path, opts, a)
        except SkillError as e:
            if len(files) == 1:
                raise
            r = {"file": str(path), "error": str(e)}
        r["seconds"] = round(time.time() - t0, 2)
        results.append(r)
    if a.format == "json":
        import json

        from _out import jsonable

        from _out import print_json

        print_json(jsonable(results if len(results) > 1 else results[0]), a.max_chars, ("head", "schema", "column_names"), "Describe fewer files, or lower --depth / --sample.")
        return 0
    text = "\n\n".join(render(r) for r in results)
    print(cap(text, a.max_chars, _more_hint(results, text, a)))
    return 0


def _more_hint(results: list[dict[str, Any]], text: str, a: Any) -> str:
    """The exact commands that show what --max-chars cut."""
    from _out import command

    if len(results) > 1:
        return "Describe one file at a time: python3 scripts/data_info.py FILE"
    r = results[0]
    f = _q(r["file"])
    tips = [f"Everything: {command({'--max-chars': len(text) + 1000})}"]
    if r.get("database"):
        tips.append(f"one table: python3 scripts/data_query.py {f} --sql \"SELECT * FROM <table> LIMIT 20\"")
    elif r.get("structure") and len((r["structure"].get("outline") or [])) > 40:
        tips.append(f"the structure alone: python3 scripts/data_tree.py outline {f} --depth {max(2, a.depth - 2)}")
    if r.get("columns", 0) > 40:
        tips.append(f"fewer sample rows: {command({'--sample': 1})}")
        tips.append(f"the columns as JSON: {command({'--format': 'json', '--sample': 0})}")
    return "; ".join(tips) + "."


# ── describing ──────────────────────────────────────────────────────────


def describe(path: Path, opts: dict[str, Any], a: Any) -> dict[str, Any]:
    from _formats import delimiter_name, outline_bytes, sniff

    info = sniff(path)
    fmt = info["format"]
    r: dict[str, Any] = {"file": str(path), "format": fmt, "size": info["size"], "size_h": human_size(info["size"])}
    if info.get("compression"):
        r["compression"] = info["compression"]
    if info.get("encoding"):
        r["encoding"] = info["encoding"] + (" with BOM" if info.get("bom") else "")
        if info.get("encoding_confidence", 1) < 0.9:
            r["encoding"] += f" (confidence {info['encoding_confidence']})"
    if info.get("hint"):
        r["note"] = info["hint"]
    if fmt in ("json", "yaml", "toml", "xml", "ini") or (fmt == "jsonl" and info.get("data_size", info["size"]) < outline_bytes()):
        r["structure"] = structure(path, fmt, info, a)
    if fmt in ("sqlite", "duckdb"):
        r["database"] = database(path, fmt, opts)
        return r
    if fmt in ("yaml", "toml", "ini") and not opts.get("json_path"):
        rp = r["structure"].get("record_paths") or []
        if not rp and fmt != "ini":
            return r
    if fmt == "json" and not opts.get("json_path") and r["structure"].get("scalar_doc"):
        return r
    table = tabular(path, info, opts, a)
    r.update(table)
    if fmt == "csv" and "dialect" in table:
        d = table["dialect"]
        if d.get("delimiter"):
            d["delimiter_name"] = delimiter_name(d["delimiter"])
    if fmt == "parquet":
        r["parquet"] = parquet_meta(path)
    if fmt == "avro":
        r["avro"] = avro_meta(path)
    if fmt == "arrow":
        r["arrow"] = arrow_meta(path)
    return r


def structure(path: Path, fmt: str, info: dict[str, Any], a: Any) -> dict[str, Any]:
    from _tree import doc_outline, fmt_path, record_paths

    o = doc_outline(path, fmt, info.get("data_size", info["size"]), info.get("compression"), a.depth, a.max_keys)
    out: dict[str, Any] = {"outline": o["lines"], "streamed": o["streamed"]}
    if fmt == "xml":
        out["root"] = o.get("root")
        if o.get("namespaces"):
            out["namespaces"] = o["namespaces"]
    doc = o.get("doc")
    if doc is not None:
        rp = record_paths(doc)
        out["record_paths"] = [{"path": fmt_path(p), "items": n} for p, n in rp[:5]]
        if isinstance(doc, list) and not any(x["path"] == "$" for x in out["record_paths"]):
            out["record_paths"].insert(0, {"path": "$", "items": len(doc)})
        out["scalar_doc"] = not isinstance(doc, list) and not rp
    if o.get("sampled"):
        out["note"] = f"shape from the first 20,000 of {o['records']} records"
    return out


def tabular(path: Path, info: dict[str, Any], opts: dict[str, Any], a: Any) -> dict[str, Any]:
    from _duck import BIG_ROWS, connect, ident, load, parse_inputs
    from _formats import outline_bytes

    con = connect()
    spec = parse_inputs([str(path)])[0]
    ld = load(con, spec, opts)
    t = ident(ld.name)
    rel = con.sql(f"SELECT * FROM {t} LIMIT 0")
    cols = list(rel.columns)
    types = [str(x) for x in rel.types]
    n = int(con.sql(f"SELECT count(*) FROM {t}").fetchone()[0])
    out: dict[str, Any] = {"table": ld.name, "rows": n, "columns": len(cols), "load_seconds": ld.seconds}
    if ld.notes:
        out["notes"] = ld.notes
    for k in ("dialect", "record_path", "xml_record", "stat_meta", "avro_schema"):
        if k in ld.info:
            out[k] = ld.info[k]
    if "dialect" in out:
        out["dialect"] = {k: v for k, v in out["dialect"].items() if v not in (None, "", "(empty)") or k == "header"}
    nulls: list[Any] = []
    if cols:
        aggs = ", ".join(f"count({ident(c)})" for c in cols)
        counts = con.sql(f"SELECT {aggs} FROM {t}").fetchone()
        nulls = [n - c for c in counts]
    big = n > BIG_ROWS or info.get("data_size", info["size"]) > outline_bytes()
    out["big"] = big
    k = max(0, a.sample)
    from _out import display_relation

    shown, _ = display_relation(con.sql(f"SELECT * FROM {t}"))
    head = shown.limit(k).fetchall() if k else []
    tail: list[Any] = []
    if big and k and n > k:
        tail_k = min(3, n - k)
        tail = shown.limit(tail_k, offset=n - tail_k).fetchall()
    example: list[Any] = [None] * len(cols)
    for row in head:
        for i, v in enumerate(row):
            if example[i] is None and v is not None:
                example[i] = v
    sm = ld.info.get("stat_meta") or {}
    labels = sm.get("column_labels", {})
    extra = {"format": sm.get("formats") or {}, "measure": sm.get("measure") or {}, "missing codes": {k: ", ".join(v) for k, v in {**(sm.get("missing_user_values") or {}), **(sm.get("missing_ranges") or {})}.items()}}
    out["schema"] = [{"name": c, "type": ty, "nulls": nulls[i] if nulls else None, "null_share": round(nulls[i] / n, 4) if n and nulls else 0, "example": example[i], **({"label": labels[c]} if c in labels else {}), **{k: m[c] for k, m in extra.items() if m.get(c)}} for i, (c, ty) in enumerate(zip(cols, types))]
    out["head"] = {"first_row": 1, "rows": head}
    if tail:
        out["tail"] = {"first_row": n - len(tail) + 1, "rows": tail}
    out["column_names"] = cols
    return out


def database(path: Path, fmt: str, opts: dict[str, Any]) -> dict[str, Any]:
    if fmt == "sqlite":
        from _duck import sqlite_catalog, sqlite_connect

        cat = sqlite_catalog(path)
        con = sqlite_connect(path)
        try:
            pragmas = {k: con.execute(f"PRAGMA {k}").fetchone()[0] for k in ("page_size", "page_count", "encoding", "user_version", "journal_mode")}
        finally:
            con.close()
        return {"engine": "SQLite", "pragmas": pragmas, "objects": cat}
    from _duck import connect, ident, sql_str

    con = connect()
    try:
        con.execute(f"ATTACH {sql_str(path)} AS db (READ_ONLY)")
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"{path.name}: cannot open the DuckDB file: {e}") from None
    objects: list[dict[str, Any]] = []
    for schema, name, sql, est in con.execute("SELECT schema_name, table_name, sql, estimated_size FROM duckdb_tables() WHERE database_name = 'db' ORDER BY 1, 2").fetchall():
        q = f"db.{ident(schema)}.{ident(name)}"
        cols = con.execute(f"SELECT column_name, data_type, is_nullable FROM duckdb_columns() WHERE database_name = 'db' AND schema_name = {sql_str(schema)} AND table_name = {sql_str(name)} ORDER BY column_index").fetchall()
        rows = con.execute(f"SELECT count(*) FROM {q}").fetchone()[0]
        objects.append({"name": name if schema == "main" else f"{schema}.{name}", "type": "table", "rows": rows, "columns": [{"name": c, "type": t, "notnull": nn == "NO" or nn is False} for c, t, nn in cols], "sql": sql})
    for schema, name, sql in con.execute("SELECT schema_name, view_name, sql FROM duckdb_views() WHERE database_name = 'db' AND NOT internal ORDER BY 1, 2").fetchall():
        q = f"db.{ident(schema)}.{ident(name)}"
        item: dict[str, Any] = {"name": name if schema == "main" else f"{schema}.{name}", "type": "view", "sql": sql}
        try:
            rel = con.sql(f"SELECT * FROM {q} LIMIT 0")
            item["columns"] = [{"name": c, "type": str(t)} for c, t in zip(rel.columns, rel.types)]
            item["rows"] = con.execute(f"SELECT count(*) FROM {q}").fetchone()[0]
        except Exception as e:  # noqa: BLE001 — a broken view
            item["error"] = str(e).splitlines()[0]
        objects.append(item)
    for schema, name, tbl, sql in con.execute("SELECT schema_name, index_name, table_name, sql FROM duckdb_indexes() WHERE database_name = 'db'").fetchall():
        objects.append({"name": name, "type": "index", "table": tbl, "sql": sql})
    return {"engine": "DuckDB", "objects": objects}


def parquet_meta(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    try:
        f = pq.ParquetFile(str(path))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    md = f.metadata
    codecs = set()
    for i in range(min(md.num_row_groups, 20)):
        rg = md.row_group(i)
        for j in range(rg.num_columns):
            codecs.add(rg.column(j).compression)
    kv = {k.decode("utf-8", "replace"): v.decode("utf-8", "replace")[:200] for k, v in (md.metadata or {}).items() if k not in (b"ARROW:schema",)}
    return {"row_groups": md.num_row_groups, "created_by": md.created_by, "format_version": md.format_version, "codecs": sorted(codecs), "metadata_keys": sorted(kv)[:10]}


def avro_meta(path: Path) -> dict[str, Any]:
    import fastavro

    from _formats import avro_codecs

    avro_codecs()

    with open(path, "rb") as f:
        r = fastavro.reader(f)
        return {"codec": r.codec, "schema_name": (r.writer_schema or {}).get("name") if isinstance(r.writer_schema, dict) else None}


def arrow_meta(path: Path) -> dict[str, Any]:
    from _duck import read_arrow

    t = read_arrow(path)
    md = t.schema.metadata or {}
    return {"batches": len(t.to_batches()), "metadata_keys": sorted(k.decode("utf-8", "replace") for k in md)[:10]}


# ── rendering ───────────────────────────────────────────────────────────


def render(r: dict[str, Any]) -> str:
    from _out import cell, command

    name = Path(r["file"]).name
    if "error" in r:
        return f"## {name}\n\nerror: {r['error']}"
    fmt = r["format"]
    lines = [f"## {name}", ""]
    facts = [f"format **{fmt}**", r["size_h"]]
    if r.get("compression"):
        facts.append(f"compressed ({r['compression']})")
    if r.get("encoding"):
        facts.append(f"encoding {r['encoding']}")
    if "rows" in r:
        facts.append(f"**{r['rows']:,} rows × {r['columns']} columns**")
    lines.append(" · ".join(facts))
    if r.get("note"):
        lines.append(f"- note: {r['note']}")
    d = r.get("dialect")
    if d:
        bits = [f"delimiter {d.get('delimiter_name') or repr(d.get('delimiter'))}", f"header {'yes' if d.get('header') else 'no'}"]
        if d.get("quote"):
            bits.append(f"quote {d['quote']}")
        if d.get("escape") and d.get("escape") != d.get("quote"):
            bits.append(f"escape {d['escape']}")
        if d.get("decimal_comma") and not any(str(n).startswith("decimal comma") for n in r.get("notes", [])):
            bits.append("decimal comma")
        if d.get("skip"):
            bits.append(f"skip {d['skip']} lines")
        if d.get("date_format"):
            bits.append(f"dates {d['date_format']}")
        if d.get("timestamp_format"):
            bits.append(f"timestamps {d['timestamp_format']}")
        nl = {"\\n": "LF", "\\r\\n": "CRLF", "\\r": "CR"}.get(str(d.get("newline")), None)
        if nl:
            bits.append(f"line endings {nl}")
        lines.append("- dialect: " + ", ".join(bits))
    for n in r.get("notes", []):
        lines.append(f"- {n}")
    sm = r.get("stat_meta")
    if sm:
        if sm.get("file_label"):
            lines.append(f"- file label: {sm['file_label']}")
        if sm.get("file_encoding"):
            lines.append(f"- file encoding: {sm['file_encoding']}")
    if r.get("parquet"):
        pm = r["parquet"]
        lines.append(f"- parquet: {pm.get('row_groups')} row groups, codecs {', '.join(pm.get('codecs', []))}, written by {pm.get('created_by')}")
    if r.get("avro"):
        lines.append(f"- avro: codec {r['avro'].get('codec')}, record {r['avro'].get('schema_name')}")
    if r.get("arrow"):
        lines.append(f"- arrow IPC: {r['arrow'].get('batches')} record batches")
    st = r.get("structure")
    if st:
        lines += ["", "### Structure" + (" (streamed)" if st.get("streamed") else ""), "", "```"]
        lines += st["outline"][:200]
        if len(st["outline"]) > 200:
            lines.append(f"… {len(st['outline']) - 200} more lines (data_tree.py outline FILE --depth N, or outline a sub-path)")
        lines.append("```")
        if st.get("note"):
            lines.append(st["note"])
        if st.get("namespaces"):
            lines.append("namespaces: " + ", ".join(f"{p or '(default)'}={u}" for p, u in st["namespaces"].items()))
        rp = st.get("record_paths")
        if rp:
            lines.append("record arrays: " + ", ".join(f"{x['path']} ({x['items']})" for x in rp))
    db = r.get("database")
    if db:
        lines += ["", f"### {db['engine']} objects", ""]
        rows = []
        for o in db["objects"]:
            if o["type"] in ("table", "view"):
                cols = ", ".join(f"{c['name']} {c['type']}".strip() + (" PK" if c.get("pk") else "") for c in o.get("columns", []))
                fk = "; FK " + ", ".join(f"{f['from']}→{f['table']}.{f['to']}" for f in o["foreign_keys"]) if o.get("foreign_keys") else ""
                rows.append([o["type"], o["name"], "" if o.get("rows") is None else f"{o['rows']:,}", (cols + fk)[:400]])
            else:
                rows.append([o["type"], o["name"], "", f"on {o.get('table')}: {(o.get('sql') or '')[:200]}"])
        lines.append(md_table(["type", "name", "rows", "columns / definition"], rows))
        lines += ["", "Query: " + f"python3 scripts/data_query.py {_q(r['file'])} --sql \"SELECT * FROM <table> LIMIT 20\""]
        return "\n".join(lines)
    if "schema" in r:
        lines += ["", f"### Columns (table `{r['table']}`)", ""]
        has_label = any("label" in c for c in r["schema"])
        stat_cols = [k for k in ("format", "measure", "missing codes") if any(k in c for c in r["schema"])]
        head = ["#", "column", "type", "nulls", "example"] + (["label"] if has_label else []) + stat_cols
        rows = []
        for i, c in enumerate(r["schema"], 1):
            nul = f"{c['null_share']:.0%}" if c["nulls"] else "0"
            if c["nulls"] and c["null_share"] < 0.01:
                nul = f"{c['nulls']:,}"
            elif c["nulls"] and c["nulls"] < r["rows"] and c["null_share"] >= 0.99:
                nul = f"all but {r['rows'] - c['nulls']:,}"
            rows.append([i, c["name"], c["type"], nul, cell(c["example"], 40)] + ([c.get("label", "")] if has_label else []) + [c.get(k, "") for k in stat_cols])
        lines.append(md_table(head, rows))
        vl = (sm or {}).get("value_labels") or {}
        if vl:
            lines += ["", "Value labels: " + "; ".join(f"{k}: " + ", ".join(f"{kk}={vv}" for kk, vv in list(v.items())[:8]) + (" …" if len(v) > 8 else "") for k, v in list(vl.items())[:15])]
        cols = r["column_names"]
        if r["head"]["rows"]:
            n = r["rows"]
            h = r["head"]["rows"]
            lines += ["", f"### Rows 1-{len(h)}" + (f" and {r['tail']['first_row']}-{n}" if r.get("tail") else ""), ""]
            body = [[cell(v, 60) for v in row] for row in h]
            if r.get("tail"):
                body.append(["…"] * len(cols))
                body += [[cell(v, 60) for v in row] for row in r["tail"]["rows"]]
            if len(cols) > 16:
                lines.append(_csv_rows(cols, h, r.get("tail", {}).get("rows") or [], r.get("tail", {}).get("first_row")))
            else:
                lines.append(_wide_table(cols, body, r["head"]["first_row"], r.get("tail", {}).get("first_row")))
        lines.append("")
        f = _q(r["file"])
        tname = r["table"]
        k = len(r["head"]["rows"])
        lines.append("Next:")
        if r["rows"] > k:
            lines.append(f"- rows: python3 scripts/data_query.py {f} --rows {k + 1}-{min(r['rows'], k + 100)}")
        lines.append(f"- SQL:  python3 scripts/data_query.py {f} --sql \"SELECT … FROM {tname} WHERE …\"")
        lines.append(f"- search every cell: python3 scripts/data_query.py {f} --find TEXT")
        lines.append(f"- stats and data problems: python3 scripts/data_profile.py {f}")
    return "\n".join(lines)


def _csv_rows(cols: list[str], head: list[Any], tail: list[Any], tail_first: int | None) -> str:
    """Sample rows of a wide table as CSV (a Markdown table with dozens of columns is unreadable)."""
    import csv
    import io

    from _out import cell

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["row"] + cols)
    for i, row in enumerate(head, 1):
        w.writerow([i] + [cell(v, 60) for v in row])
    if tail:
        w.writerow(["…"])
        for i, row in enumerate(tail):
            w.writerow([(tail_first or 0) + i] + [cell(v, 60) for v in row])
    return "```csv\n" + buf.getvalue().rstrip("\n") + "\n```"


def _wide_table(cols: list[str], body: list[list[str]], first: int, tail_first: int | None) -> str:
    rows = []
    n_head = len(body) - (0 if tail_first is None else len(body) - body.index(["…"] * len(cols)))
    for i, b in enumerate(body):
        if b == ["…"] * len(cols):
            rows.append(["…"] + b)
            continue
        if tail_first is not None and i > n_head:
            num = tail_first + (i - n_head - 1)
        else:
            num = first + i
        rows.append([num] + b)
    return md_table(["row"] + cols, rows)


def _q(s: str) -> str:
    import shlex

    return shlex.quote(s)


if __name__ == "__main__":
    run_main(main)
