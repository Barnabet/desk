#!/usr/bin/env python3
"""Compare two tables (any formats this skill reads) by key: added, removed and changed rows with the changed
columns' old → new values, per-column change counts and schema differences. DuckDB does the work, so million-row
tables diff in seconds. Two JSON/YAML/TOML/INI documents are compared structurally instead: every changed, added
and removed value with its path."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, emit, md_table, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_diff.py customers_v1.csv customers_v2.csv --key id
  python3 scripts/data_diff.py old.parquet new.csv --key order_id,line --ignore updated_at
  python3 scripts/data_diff.py jan.xlsx.csv feb.csv                   # key guessed (a unique column like id)
  python3 scripts/data_diff.py a.csv b.csv --key id --tolerance 0.01 --trim --ignore-case
  python3 scripts/data_diff.py a.csv b.csv --key id --out changes.csv   # every change, one line per changed cell
  python3 scripts/data_diff.py a.csv b.csv --key id --limit 50 --offset 50   # next page of changed rows
  python3 scripts/data_diff.py config.yaml config.new.yaml               # documents: every changed path
  python3 scripts/data_diff.py api.json api.v2.json --ignore updated_at  # skip a key wherever it appears
  python3 scripts/data_diff.py dump.json dump2.json --mode table --path data.records --key id

Without a usable key, rows are compared whole (multiset difference: rows only in OLD, rows only in NEW).
The --out file has columns change (added/removed/changed), the key columns, column, old, new.

Documents (--mode auto picks this for JSON objects, YAML, TOML and INI; --mode doc forces it, XML included):
objects compare key by key, arrays of objects match by an id-like key (id, key, name …) when one is unique on
both sides, other arrays align item by item; paths ($.jobs.build.steps[2].run) address the new document, those of
removed values the old one. A JSON array of records (or --path, --key, --mode table) is diffed as a table.
"""


def main() -> int:
    from _duck import add_read_args

    p = parser("Diff two tables by key: added / removed / changed rows, changed cells, schema changes.", EPILOG)
    p.add_argument("old", help="the old / left table")
    p.add_argument("new", help="the new / right table")
    p.add_argument("--key", help="key column(s), comma-separated (default: guessed)")
    p.add_argument("--no-key", action="store_true", help="compare whole rows, ignoring keys")
    p.add_argument("--ignore", help="columns to leave out of the comparison")
    p.add_argument("--columns", help="compare only these columns")
    p.add_argument("--tolerance", type=float, default=0.0, help="numbers closer than this are equal (default 0)")
    p.add_argument("--ignore-case", action="store_true", help="text compares case-insensitively")
    p.add_argument("--trim", action="store_true", help="text compares without leading/trailing spaces")
    p.add_argument("--mode", choices=["auto", "table", "doc"], default="auto", help="doc: structural diff of two documents; table: rows (default auto: doc for JSON objects, YAML, TOML, INI)")
    p.add_argument("--limit", type=int, help="changed/added/removed rows to show (default 20 each; documents: 100 changes)")
    p.add_argument("--offset", type=int, default=0, help="skip this many changed rows (paging)")
    p.add_argument("--out", help="write every change to a file (.csv .parquet .json .jsonl …)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    add_read_args(p)
    a = p.parse_args()
    t0 = time.time()
    docs = pick_docs(a)
    if docs is not None:
        res = doc_diff(a, *docs)
        res["seconds"] = round(time.time() - t0, 2)
        if a.format == "json":
            from _out import jsonable, print_json

            print_json(jsonable(res), a.max_chars, ("changes", "notes"), "Lower --limit, or write every change with --out changes.csv.")
        else:
            print(cap(render_doc(res), a.max_chars, "Lower --limit, or write every change with --out changes.csv."))
        return 0
    if a.limit is None:
        a.limit = 20
    res = diff(a)
    res["seconds"] = round(time.time() - t0, 2)
    if a.format == "json":
        import json

        from _out import jsonable

        from _out import print_json

        print_json(jsonable(res), a.max_chars, ("changed_rows", "notes"), "Lower --limit, or write every change with --out changes.csv.")
    else:
        print(cap(render(res), a.max_chars, "Lower --limit, or write every change with --out changes.csv."))
    return 0


def _cols(s: str | None) -> list[str]:
    return [c.strip() for c in (s or "").split(",") if c.strip()]


# ── documents ───────────────────────────────────────────────────────────

DOC_FORMATS = ("json", "yaml", "toml", "ini")


def pick_docs(a: Any) -> tuple[Any, Any, str, str] | None:
    """(old doc, new doc, old format, new format) when the inputs are compared as documents, else None."""
    if a.mode == "table" or (a.mode == "auto" and (a.key or a.no_key or a.columns or a.json_path or a.record or a.db_table)):
        return None
    from _duck import is_glob
    from _formats import sniff, stream_bytes

    for f in (a.old, a.new):
        if is_glob(f) or not Path(f).is_file():
            if a.mode == "doc":
                raise UsageError(f"--mode doc compares two files; {f} is not one")
            return None
    ia, ib = sniff(Path(a.old)), sniff(Path(a.new))
    allowed = DOC_FORMATS + (("xml",) if a.mode == "doc" else ())
    if ia["format"] not in allowed or ib["format"] not in allowed:
        if a.mode == "doc":
            raise UsageError(f"--mode doc compares JSON, YAML, TOML, INI or XML documents (these are {ia['format']} and {ib['format']})")
        return None
    big = max(ia.get("data_size", ia["size"]), ib.get("data_size", ib["size"]))
    if a.mode == "auto" and big >= stream_bytes():
        return None  # compared as tables of their record arrays; diff() says what was left out
    from _tree import load_doc

    da = load_doc(Path(a.old), ia["format"], a.encoding)
    db = load_doc(Path(a.new), ib["format"], a.encoding)
    if a.mode == "auto" and ia["format"] == ib["format"] == "json" and all(isinstance(d, list) and d and all(isinstance(x, dict) for x in d) for d in (da, db)):
        return None  # two JSON arrays of records: a table diff (keys, per-column counts); YAML streams stay documents
    return da, db, ia["format"], ib["format"]


def doc_diff(a: Any, da: Any, db: Any, fa: str, fb: str) -> dict[str, Any]:
    from _docdiff import Options, diff_docs
    from _tree import fmt_path

    o = Options(tolerance=a.tolerance, ignore_case=a.ignore_case, trim=a.trim, ignore=set(_cols(a.ignore)))
    d = diff_docs(da, db, o)
    changes = [{**c, "path": fmt_path(c["path"])} for c in d["changes"]]
    lim = 100 if a.limit is None else max(0, a.limit)
    res: dict[str, Any] = {"mode": "doc", "old": a.old, "new": a.new, "formats": [fa, fb], "counts": d["counts"], "total": len(changes), "offset": a.offset, "changes": changes[a.offset : a.offset + lim], "notes": d["notes"]}
    if fa != fb:
        res["notes"].insert(0, f"{fa} compared with {fb}: values compare by type (1 and \"1\" differ)")
    if a.ignore:
        res["notes"].append(f"keys left out wherever they appear: {a.ignore}")
    if len(changes) > a.offset + lim:
        from _out import command

        res["next"] = command({"--offset": a.offset + lim, "--limit": lim})
    if a.out:
        res["out"] = _write_doc_changes(a, changes)
    return res


def _write_doc_changes(a: Any, changes: list[dict[str, Any]]) -> dict[str, Any]:
    import csv
    import io
    import json

    from _common import atomic_write
    from _tree import plain

    out = output_path(a.out, [a.old, a.new], a.force)
    ext = out.suffix.lower()

    def text(v: Any) -> str | None:
        return None if v is None else (v if isinstance(v, str) else json.dumps(plain(v), ensure_ascii=False, default=str))

    rows = [{"change": c["change"], "path": c["path"], "old": text(c["old"]), "new": text(c["new"]), "at": c["at"], "note": c.get("note")} for c in changes]
    if ext in (".csv", ".tsv"):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["change", "path", "old", "new", "at", "note"], delimiter="\t" if ext == ".tsv" else ",", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        data = buf.getvalue()
    elif ext == ".json":
        data = json.dumps([{**c, "path": c["path"], "old": plain(c["old"]), "new": plain(c["new"])} for c in changes], ensure_ascii=False, indent=1, default=str)
    elif ext in (".jsonl", ".ndjson"):
        data = "".join(json.dumps({**c, "old": plain(c["old"]), "new": plain(c["new"])}, ensure_ascii=False, default=str) + "\n" for c in changes)
    else:
        raise UsageError("a document diff writes .csv, .tsv, .json or .jsonl")
    atomic_write(out, data.encode("utf-8"))
    return {"path": str(out), "rows": len(changes)}


def render_doc(r: dict[str, Any]) -> str:
    from _tree import preview

    c = r["counts"]
    lines = [f"# Diff: {Path(r['old']).name} → {Path(r['new']).name} (documents)", ""]
    stats = [f"changed {c['changed'] + c['type']:,}", f"added {c['added']:,}", f"removed {c['removed']:,}"]
    lines.append("- " + " · ".join(stats) + f" · {r.get('seconds', 0)}s")
    if c["type"]:
        lines.append(f"- {c['type']} value(s) changed type")
    if c.get("reordered"):
        lines.append(f"- {c['reordered']} array(s) hold the same items in another order")
    for n in r.get("notes", []):
        lines.append(f"- {n}")
    if not r["total"]:
        lines += ["", "No differences: the documents hold the same data (key order, comments and formatting aside)."]
        return "\n".join(lines)
    shown = r["changes"]
    lines += ["", f"## Changes ({len(shown)} shown of {r['total']:,}; paths address the new document, removed ones the old)", ""]
    rows = []
    for ch in shown:
        what = ch["change"] + (f" ({ch['note']})" if ch.get("note") else "")
        rows.append([what, ch["path"], "" if ch["change"] in ("added", "reordered") else preview(ch["old"], 70), "" if ch["change"] in ("removed", "reordered") else preview(ch["new"], 70)])
    lines.append(md_table(["change", "path", "old", "new"], rows))
    if r.get("next"):
        lines += ["", f"[… {r['total'] - r['offset'] - len(shown):,} more changes. Next: {r['next']}]"]
    if r.get("out"):
        lines += ["", f"Wrote {r['out']['rows']:,} changes to {r['out']['path']}."]
    lines += ["", "A value at a path: python3 scripts/data_tree.py get FILE 'PATH'"]
    return "\n".join(lines)


def _kind(t: str) -> str:
    from data_profile import kind_of

    return kind_of(t)


def diff(a: Any) -> dict[str, Any]:
    from _duck import columns_of, connect, ident, load, one_table, parse_inputs, read_opts

    opts = read_opts(a)
    con = connect()
    sa = parse_inputs([a.old])[0]
    sb = parse_inputs([a.new])[0]
    sa.name, sb.name = "__old", "__new"
    la = load(con, sa, opts)
    lb = load(con, sb, opts)
    ta, tb = one_table(con, la, opts), one_table(con, lb, opts)
    partial = [ld for ld in (la, lb) if ld.info.get("record_path") not in (None, "$") or ld.info.get("xml_record")]
    con.execute(f"CREATE TEMP VIEW a AS SELECT * FROM {ta}")
    con.execute(f"CREATE TEMP VIEW b AS SELECT * FROM {tb}")
    ca, cb = columns_of(con, "a"), columns_of(con, "b")
    ta_types, tb_types = dict(ca), dict(cb)
    na = int(con.sql("SELECT count(*) FROM a").fetchone()[0])
    nb = int(con.sql("SELECT count(*) FROM b").fetchone()[0])
    common = [c for c, _ in ca if c in tb_types]
    schema = {
        "only_old": [f"{c} ({t})" for c, t in ca if c not in tb_types],
        "only_new": [f"{c} ({t})" for c, t in cb if c not in ta_types],
        "type_changes": [f"{c}: {ta_types[c]} → {tb_types[c]}" for c in common if ta_types[c] != tb_types[c]],
        "order_changed": [c for c, _ in ca if c in tb_types] != [c for c, _ in cb if c in ta_types],
    }
    res: dict[str, Any] = {"old": a.old, "new": a.new, "rows_old": na, "rows_new": nb, "schema": schema, "notes": list(dict.fromkeys(la.notes + lb.notes))}
    if partial and not a.json_path and not a.record:
        from _out import command

        rec = str(partial[0].info.get("xml_record") or "")
        rp = partial[0].info.get("record_path")
        what = f"the records at {rp}" if rp else f"the <{rec.split('/')[-1]}> elements ({rec})"
        res["notes"].insert(0, f"only {what} were compared; the rest of each document was not. Whole documents: {command({'--mode': 'doc'}, drop=['--key', '--no-key', '--columns'])}")
    if not common:
        raise SkillError("the two tables have no column in common")
    ignore = {c.lower() for c in _cols(a.ignore)}
    every = {x.lower() for x in list(ta_types) + list(tb_types)}
    for c in _cols(a.ignore):
        if c.lower() not in every:
            raise UsageError(f"--ignore: neither table has a column {c!r}")
    keys: list[str] = []
    if not a.no_key:
        if a.key:
            keys = _cols(a.key)
            for k in keys:
                if k not in common:
                    raise UsageError(f"--key {k!r} is not a column of both tables; common columns: {', '.join(common[:30])}")
        else:
            keys = guess_key(con, common, na, nb)
            if keys:
                res["notes"].append(f"key guessed: {', '.join(keys)} (unique in both; pass --key to choose)")
    compare = [c for c in common if c not in keys and c.lower() not in ignore]
    if a.columns:
        want = _cols(a.columns)
        for c in want:
            if c not in compare:
                raise UsageError(f"--columns: {c!r} is not a compared column of both tables")
        compare = want
    res["key"] = keys
    res["compared_columns"] = compare
    if not keys:
        return whole_rows(con, a, res, [c for c in common if c.lower() not in ignore])
    kq = ", ".join(ident(k) for k in keys)
    dup_a = int(con.sql(f"SELECT count(*) FROM (SELECT {kq} FROM a GROUP BY ALL HAVING count(*) > 1)").fetchone()[0])
    dup_b = int(con.sql(f"SELECT count(*) FROM (SELECT {kq} FROM b GROUP BY ALL HAVING count(*) > 1)").fetchone()[0])
    if dup_a or dup_b:
        res["notes"].append(f"duplicate keys: {dup_a} in old, {dup_b} in new; the first row per key is compared")
        con.execute(f"CREATE OR REPLACE TEMP VIEW a AS SELECT * FROM {ta} QUALIFY row_number() OVER (PARTITION BY {kq}) = 1")
        con.execute(f"CREATE OR REPLACE TEMP VIEW b AS SELECT * FROM {tb} QUALIFY row_number() OVER (PARTITION BY {kq}) = 1")
    null_keys = int(con.sql(f"SELECT (SELECT count(*) FROM a WHERE {' OR '.join(ident(k) + ' IS NULL' for k in keys)}) + (SELECT count(*) FROM b WHERE {' OR '.join(ident(k) + ' IS NULL' for k in keys)})").fetchone()[0])
    if null_keys:
        res["notes"].append(f"{null_keys} rows have a null key and match nothing")
    diffs = {c: differs(c, ta_types[c], tb_types[c], a) for c in compare}
    join = " AND ".join(f"a.{ident(k)} = b.{ident(k)}" for k in keys)
    any_diff = " OR ".join(f"({d})" for d in diffs.values()) or "false"
    flags = ", ".join(f"({d}) AS {ident('__d_' + c)}" for c, d in diffs.items()) if diffs else "NULL AS __none"
    keys_a = ", ".join("a." + ident(k) for k in keys)
    con.execute(f"CREATE TEMP TABLE __changed AS SELECT {keys_a}, {flags} FROM a JOIN b ON {join} WHERE {any_diff}")
    changed = int(con.sql("SELECT count(*) FROM __changed").fetchone()[0])
    per_col = {}
    if diffs and changed:
        row = con.sql("SELECT " + ", ".join(f"count(*) FILTER (WHERE {ident('__d_' + c)})" for c in diffs) + " FROM __changed").fetchone()
        per_col = {c: int(n) for c, n in zip(diffs, row) if n}
    matched = int(con.sql(f"SELECT count(*) FROM a JOIN b ON {join}").fetchone()[0])
    added = int(con.sql(f"SELECT count(*) FROM b ANTI JOIN a ON {join}").fetchone()[0])
    removed = int(con.sql(f"SELECT count(*) FROM a ANTI JOIN b ON {join}").fetchone()[0])
    res.update(added=added, removed=removed, changed=changed, unchanged=matched - changed, per_column=per_col)
    # Samples
    lim = max(0, a.limit)
    kcols = ", ".join(f"a.{ident(k)}" for k in keys)
    if lim and changed:
        sel = ", ".join(f"a.{ident(k)}" for k in keys) + ", " + ", ".join(f"c.{ident('__d_' + c)}, a.{ident(c)}, b.{ident(c)}" for c in diffs)
        cjoin = " AND ".join(f"c.{ident(k)} = a.{ident(k)}" for k in keys)
        rows = con.sql(f"SELECT {sel} FROM __changed c JOIN a ON {cjoin} JOIN b ON {join} ORDER BY {kcols} LIMIT {lim} OFFSET {int(a.offset)}").fetchall()
        sample = []
        for r in rows:
            key = list(r[: len(keys)])
            cells = []
            for i, c in enumerate(diffs):
                flag, old, new = r[len(keys) + 3 * i : len(keys) + 3 * i + 3]
                if flag:
                    cells.append({"column": c, "old": old, "new": new})
            sample.append({"key": key, "changes": cells})
        res["changed_rows"] = sample
    if lim and added:
        rel = con.sql(f"SELECT b.* FROM b ANTI JOIN a ON {join} ORDER BY {', '.join('b.' + ident(k) for k in keys)} LIMIT {lim}")
        res["added_rows"] = {"columns": rel.columns, "rows": rel.fetchall()}
    if lim and removed:
        rel = con.sql(f"SELECT a.* FROM a ANTI JOIN b ON {join} ORDER BY {kcols} LIMIT {lim}")
        res["removed_rows"] = {"columns": rel.columns, "rows": rel.fetchall()}
    if changed > a.offset + lim:
        from _out import command

        res["next"] = command({"--offset": a.offset + lim})
    if a.out:
        res["out"] = write_changes(con, a, keys, diffs, join)
    return res


def differs(c: str, ta: str, tb: str, a: Any) -> str:
    from _duck import ident

    x, y = f"a.{ident(c)}", f"b.{ident(c)}"
    ka, kb = _kind(ta), _kind(tb)
    if ka == "number" and kb == "number":
        if a.tolerance:
            return f"(({x} IS NULL) != ({y} IS NULL) OR abs(CAST({x} AS DOUBLE) - CAST({y} AS DOUBLE)) > {a.tolerance!r})"
        return f"CAST({x} AS DOUBLE) IS DISTINCT FROM CAST({y} AS DOUBLE)"
    if ka == "date" and kb == "date":
        return f"CAST({x} AS TIMESTAMP) IS DISTINCT FROM CAST({y} AS TIMESTAMP)"
    if ta == tb and ka not in ("text",) and not (a.trim or a.ignore_case):
        return f"{x} IS DISTINCT FROM {y}"
    ex, ey = f"CAST({x} AS VARCHAR)", f"CAST({y} AS VARCHAR)"
    if a.trim:
        ex, ey = f"trim({ex})", f"trim({ey})"
    if a.ignore_case:
        ex, ey = f"lower({ex})", f"lower({ey})"
    return f"{ex} IS DISTINCT FROM {ey}"


def guess_key(con: Any, common: list[str], na: int, nb: int) -> list[str]:
    from _duck import ident

    if not common or na == 0 or nb == 0:
        return []
    import re

    rel = con.sql("SELECT * FROM a LIMIT 0")
    types = {c: str(t) for c, t in zip(rel.columns, rel.types)}
    # Keys are identifiers: integers, text, dates. A float that happens to be unique is not one.
    usable = [c for c in common if not any(t in types.get(c, "") for t in ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "[]", "STRUCT", "MAP"))]
    ranked = sorted(usable, key=lambda c: (0 if re.fullmatch(r"(?i)(id|key|uuid|guid|code|pk|.*_id|.*id)", c) else 1, common.index(c)))
    for c in ranked[:12]:
        q = ident(c)
        ua = con.sql(f"SELECT count(DISTINCT {q}) = count(*) AND count({q}) = count(*) FROM a").fetchone()[0]
        if not ua:
            continue
        ub = con.sql(f"SELECT count(DISTINCT {q}) = count(*) AND count({q}) = count(*) FROM b").fetchone()[0]
        if ub:
            overlap = con.sql(f"SELECT count(*) FROM a JOIN b USING ({q})").fetchone()[0]
            if overlap >= 0.1 * min(na, nb):
                return [c]
    return []


def whole_rows(con: Any, a: Any, res: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    from _duck import ident

    sel = ", ".join(ident(c) for c in cols)
    con.execute(f"CREATE TEMP TABLE __only_old AS SELECT {sel} FROM a EXCEPT ALL SELECT {sel} FROM b")
    con.execute(f"CREATE TEMP TABLE __only_new AS SELECT {sel} FROM b EXCEPT ALL SELECT {sel} FROM a")
    ro = int(con.sql("SELECT count(*) FROM __only_old").fetchone()[0])
    rn = int(con.sql("SELECT count(*) FROM __only_new").fetchone()[0])
    res.update(removed=ro, added=rn, changed=None, unchanged=res["rows_old"] - ro, per_column={})
    res["notes"].append("no key: rows compared whole (a changed row counts as one removed plus one added); pass --key to see cell changes")
    lim = max(0, a.limit)
    if lim and rn:
        rel = con.sql(f"SELECT * FROM __only_new LIMIT {lim}")
        res["added_rows"] = {"columns": rel.columns, "rows": rel.fetchall()}
    if lim and ro:
        rel = con.sql(f"SELECT * FROM __only_old LIMIT {lim}")
        res["removed_rows"] = {"columns": rel.columns, "rows": rel.fetchall()}
    if a.out:
        from _out import out_format, write_table

        out = output_path(a.out, [a.old, a.new], a.force)
        fmt, comp = out_format(out)
        sql = f"SELECT 'removed' AS change, * FROM __only_old UNION ALL SELECT 'added' AS change, * FROM __only_new"
        r = write_table(con, sql, out, fmt, comp)
        res["out"] = {"path": str(out), "rows": r["rows"]}
    return res


def write_changes(con: Any, a: Any, keys: list[str], diffs: dict[str, str], join: str) -> dict[str, Any]:
    from _duck import ident, sql_str
    from _out import out_format, write_table

    out = output_path(a.out, [a.old, a.new], a.force)
    fmt, comp = out_format(out)
    kq_a = ", ".join(f"a.{ident(k)}" for k in keys)
    kq_b = ", ".join(f"b.{ident(k)}" for k in keys)
    parts = []
    for c, d in diffs.items():
        parts.append(f"SELECT 'changed' AS change, {kq_a}, {sql_str(c)} AS \"column\", CAST(a.{ident(c)} AS VARCHAR) AS old, CAST(b.{ident(c)} AS VARCHAR) AS new FROM a JOIN b ON {join} WHERE {d}")
    parts.append(f"SELECT 'added' AS change, {kq_b}, NULL AS \"column\", NULL AS old, CAST(to_json(b) AS VARCHAR) AS new FROM b ANTI JOIN a ON {join}")
    parts.append(f"SELECT 'removed' AS change, {kq_a}, NULL AS \"column\", CAST(to_json(a) AS VARCHAR) AS old, NULL AS new FROM a ANTI JOIN b ON {join}")
    sql = " UNION ALL ".join(parts)
    order = ", ".join(str(i + 2) for i in range(len(keys)))
    r = write_table(con, f"SELECT * FROM ({sql}) ORDER BY {order}, 1", out, fmt, comp)
    return {"path": str(out), "rows": r["rows"]}


# ── rendering ───────────────────────────────────────────────────────────


def render(r: dict[str, Any]) -> str:
    from _out import cell

    keys = r.get("key") or []
    lines = [f"# Diff: {Path(r['old']).name} → {Path(r['new']).name}" + (f" (key: {', '.join(keys)})" if keys else " (whole rows)"), ""]
    stats = [f"rows {r['rows_old']:,} → {r['rows_new']:,}", f"added {r['added']:,}", f"removed {r['removed']:,}"]
    if r.get("changed") is not None:
        stats += [f"changed {r['changed']:,}", f"unchanged {r['unchanged']:,}"]
    lines.append("- " + " · ".join(stats) + f" · {r.get('seconds', 0)}s")
    s = r["schema"]
    sch = []
    if s["only_new"]:
        sch.append("added columns: " + ", ".join(s["only_new"]))
    if s["only_old"]:
        sch.append("removed columns: " + ", ".join(s["only_old"]))
    if s["type_changes"]:
        sch.append("type changes: " + "; ".join(s["type_changes"]))
    if s["order_changed"]:
        sch.append("column order changed")
    lines.append("- schema: " + ("; ".join(sch) if sch else "same columns and types"))
    for n in r.get("notes", []):
        lines.append(f"- {n}")
    if r.get("per_column"):
        lines += ["", "## Changes per column", ""]
        lines.append(md_table(["column", "rows changed"], sorted(([c, f"{n:,}"] for c, n in r["per_column"].items()), key=lambda x: -int(x[1].replace(",", "")))))
    if r.get("changed_rows"):
        first = 1 + (r.get("offset", 0) or 0)
        lines += ["", f"## Changed rows ({len(r['changed_rows'])} shown of {r['changed']:,})", ""]
        rows = []
        for item in r["changed_rows"]:
            k = ", ".join(cell(v, 40) for v in item["key"])
            for ch in item["changes"]:
                rows.append([k, ch["column"], cell(ch["old"], 60), cell(ch["new"], 60)])
                k = ""
        lines.append(md_table([" / ".join(keys) or "key", "column", "old", "new"], rows))
    for label, key in (("Added rows", "added_rows"), ("Removed rows", "removed_rows")):
        block = r.get(key)
        if block and block["rows"]:
            total = r["added"] if key == "added_rows" else r["removed"]
            lines += ["", f"## {label} ({len(block['rows'])} shown of {total:,})", ""]
            lines.append(md_table(block["columns"], [[cell(v, 40) for v in row] for row in block["rows"]]))
    if r.get("next"):
        lines += ["", f"[… more changed rows. Next: {r['next']}]"]
    if r.get("out"):
        lines += ["", f"Wrote {r['out']['rows']:,} change lines to {r['out']['path']}."]
    elif (r.get("changed") or 0) + r["added"] + r["removed"] > 0:
        lines += ["", "Every change: add --out changes.csv (one line per changed cell, plus added/removed rows)."]
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
