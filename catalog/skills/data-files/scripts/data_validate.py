#!/usr/bin/env python3
"""Validate data: JSON Schema (draft 2020-12 and older) for JSON, JSONL, YAML, TOML and table rows, listing every
error by path; infer a schema from data; table rules (required columns, types, not-null, unique, ranges, regex,
allowed values, lengths) for CSV and any other table, with the violating row numbers."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, input_file, md_table, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_validate.py order.json --schema order.schema.json
  python3 scripts/data_validate.py events.jsonl --schema event.schema.json        # every line is checked
  python3 scripts/data_validate.py config.yaml --schema config.schema.json --path server
  python3 scripts/data_validate.py orders.json --infer --out order.schema.json    # a schema from the data
  python3 scripts/data_validate.py people.csv --rules rules.yaml
  python3 scripts/data_validate.py people.csv --rules '{"columns": {"id": {"type": "integer", "unique": true}}}'

Rules file (YAML or JSON):
  columns:
    id:      {type: integer, required: true, not_null: true, unique: true}
    email:   {regex: '^[^@\\s]+@[^@\\s]+\\.[a-z]+$', not_null: true}
    age:     {type: integer, min: 0, max: 120}
    country: {allowed: [FR, DE, US]}
    name:    {min_length: 1, max_length: 80}
    day:     {type: date, format: '%d/%m/%Y'}       # types: integer number boolean date datetime string
  unique: [[order_id, line]]                        # composite keys
  row_count: {min: 1}
  no_extra_columns: true

Exit code 0 when valid, 1 when not (the report is on stdout either way), 2 for bad arguments.
"""


def main() -> int:
    from _duck import add_read_args

    p = parser("Validate data against a JSON Schema or table rules, or infer a JSON Schema.", EPILOG)
    p.add_argument("file", help="the data file")
    p.add_argument("--schema", help="JSON Schema: a .json/.yaml file or inline JSON")
    p.add_argument("--rules", help="table rules: a .yaml/.json file or inline JSON")
    p.add_argument("--infer", action="store_true", help="infer a JSON Schema from the data instead of validating")
    p.add_argument("--strict", action="store_true", help="with --infer: add enums for small value sets, number ranges and additionalProperties: false")
    p.add_argument("--sample", type=int, help="with --infer: shape from at most N records (tables: a reproducible random sample, default 50000, while required columns, ranges and enums still come from every row; JSONL: the first N lines, default all)")
    p.add_argument("--each", action="store_true", help="validate each element of the array (at --path) separately (automatic for JSONL and tables)")
    p.add_argument("--max-errors", type=int, default=50, help="errors to list one by one (default 50; all are counted and grouped by kind)")
    p.add_argument("--out", help="write the inferred schema (or the full error report as .json/.csv) here")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    add_read_args(p)
    a = p.parse_args()
    if sum(bool(x) for x in (a.schema, a.rules, a.infer)) != 1:
        raise UsageError("give exactly one of --schema, --rules or --infer")
    path = input_file(a.file)
    from _duck import read_opts

    opts = read_opts(a)
    t0 = time.time()
    if a.infer:
        return infer(a, path, opts)
    if a.schema:
        report = validate_schema(a, path, opts)
    else:
        report = validate_rules(a, path, opts)
    report["seconds"] = round(time.time() - t0, 2)
    if a.out:
        out = output_path(a.out, [path], a.force)
        write_report(report, out)
        report["report_file"] = str(out)
    if a.format == "json":
        from _out import print_json

        print_json(report, a.max_chars, ("errors", "error_kinds", "results"), "Write every error with --out errors.json.")
    else:
        print(cap(render(report), a.max_chars, "Write every error with --out errors.csv, or lower --max-errors."))
    return 0 if report["valid"] else 1


def _load_spec(value: str, what: str) -> Any:
    s = value.strip()
    if s[:1] in "{[":
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise UsageError(f"{what}: invalid inline JSON: {e}") from None
    p = Path(value).expanduser()
    if not p.exists():
        raise UsageError(f"{what}: {value} is neither inline JSON nor an existing file")
    from _formats import sniff
    from _tree import load_doc

    fmt = sniff(p)["format"]
    if fmt not in ("json", "yaml", "toml"):
        fmt = "yaml" if p.suffix.lower() in (".yaml", ".yml") else "json"
    return load_doc(p, fmt)


# ── JSON Schema ─────────────────────────────────────────────────────────


def _format_checker(cls: Any) -> Any:
    import datetime as dt
    from urllib.parse import urlparse

    checker = cls.FORMAT_CHECKER

    @checker.checks("date-time", raises=ValueError)
    def _dt(v: Any) -> bool:
        if not isinstance(v, str):
            return True
        if not re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$", v):
            raise ValueError("not an RFC 3339 date-time")
        dt.datetime.fromisoformat(v.replace("z", "Z").replace("Z", "+00:00"))
        return True

    @checker.checks("time", raises=ValueError)
    def _time(v: Any) -> bool:
        if not isinstance(v, str):
            return True
        if not re.match(r"^\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})?$", v):
            raise ValueError("not an RFC 3339 time")
        return True

    @checker.checks("uri", raises=ValueError)
    def _uri(v: Any) -> bool:
        if not isinstance(v, str):
            return True
        u = urlparse(v)
        if not u.scheme or not re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*$", u.scheme) or " " in v:
            raise ValueError("not an absolute URI")
        return True

    @checker.checks("uri-reference", raises=ValueError)
    def _uriref(v: Any) -> bool:
        if isinstance(v, str) and (" " in v or "\n" in v):
            raise ValueError("not a URI reference")
        return True

    return checker


def _validator(schema: Any) -> Any:
    from jsonschema import exceptions, validators

    cls = validators.validator_for(schema, default=validators.Draft202012Validator)
    try:
        cls.check_schema(schema)
    except exceptions.SchemaError as e:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        raise SkillError(f"the schema itself is invalid at {loc}: {e.message}") from None
    return cls(schema, format_checker=_format_checker(cls)), cls.__name__.replace("Validator", "")


def _instances(path: Path, opts: dict[str, Any], each: bool) -> Iterator[tuple[str, Any]]:
    """(location label, instance) pairs: the document, its elements, JSONL lines or table rows."""
    from _formats import sniff, stream_bytes
    from _tree import load_doc, parse_path, select, stream_records

    info = sniff(path)
    fmt = info["format"]
    jp = opts.get("json_path")
    if fmt == "jsonl":
        from _formats import open_decompressed

        with open_decompressed(path, info.get("compression")) as f:
            for i, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    yield f"line {i}", json.loads(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw)
                except ValueError as e:
                    yield f"line {i}", _BadJSON(str(e))
        return
    if fmt in ("json", "yaml", "toml", "xml", "ini"):
        big = fmt == "json" and info.get("data_size", info["size"]) >= stream_bytes()
        if big and each:
            steps = parse_path(jp) if jp else []
            for i, item in enumerate(stream_records(path, steps, info.get("compression"))):
                yield f"item {i}", item
            return
        doc = load_doc(path, fmt, opts.get("encoding"))
        if jp:
            hits = list(select(doc, parse_path(jp)))
            if not hits:
                raise SkillError(f"nothing at {jp}")
            doc = hits[0][1]
        from _tree import fmt_pattern

        base = fmt_pattern(parse_path(jp)) if jp else "$"
        if each:
            if not isinstance(doc, list):
                raise UsageError(f"--each needs an array at {base}; it is {type(doc).__name__}")
            for i, item in enumerate(doc):
                yield f"{base}[{i}]", item
        else:
            yield base, doc
        return
    # Tables: each row is an object.
    from _duck import connect, ident, load, one_table, parse_inputs
    from _out import jsonable

    con = connect()
    spec = parse_inputs([str(path)])[0]
    ld = load(con, spec, opts)
    t = one_table(con, ld, opts)
    cur = con.execute(f"SELECT * FROM {t}")  # not con.cursor(): it does not see Arrow data registered on con
    cols = [d[0] for d in cur.description]
    n = 0
    while True:
        batch = cur.fetchmany(10000)
        if not batch:
            break
        for r in batch:
            n += 1
            yield f"row {n}", {c: jsonable(v) for c, v in zip(cols, r) if v is not None}


class _BadJSON:
    def __init__(self, msg: str) -> None:
        self.msg = msg


def validate_schema(a: Any, path: Path, opts: dict[str, Any]) -> dict[str, Any]:
    schema = _load_spec(a.schema, "--schema")
    validator, draft = _validator(schema)
    keep = 100_000 if a.out else a.max_errors
    from _formats import sniff

    info = sniff(path)
    if info["format"] == "jsonl" and not info.get("compression") and info["size"] >= _parallel_bytes():
        from _common import workers_for

        if workers_for(8) > 1:
            return _validate_jsonl_parallel(path, info["size"], schema, draft, keep, a.max_errors)
    col = _Collector(keep)
    for label, inst in _instances(path, opts, a.each):
        col.check(validator, label, inst)
    return col.report(path, draft, a.max_errors)


class _Collector:
    """Counts records and errors, keeps the first `keep` errors, and groups every error by kind (the path with
    indexes as [*], and the rule) with its first locations, so a million identical errors read as one line."""

    def __init__(self, keep: int) -> None:
        self.keep = keep
        self.errors: list[dict[str, Any]] = []
        self.total = self.checked = self.invalid = 0
        self.groups: dict[str, list[Any]] = {}

    def add(self, label: Any, item: dict[str, Any]) -> None:
        self.total += 1
        if len(self.errors) < self.keep:
            self.errors.append({**item, "where": label} if isinstance(label, str) else {**item, "_rel": label})
        key = re.sub(r"\[\d+\]", "[*]", item.get("path") or "$") + "\t" + str(item.get("keyword"))
        g = self.groups.get(key)
        if g is None:
            if len(self.groups) >= 200:
                return
            g = self.groups[key] = [0, [], item.get("message", "")]
        g[0] += 1
        if len(g[1]) < 5:
            g[1].append(label)

    def check(self, validator: Any, label: Any, inst: Any) -> None:
        self.checked += 1
        if isinstance(inst, _BadJSON):
            self.invalid += 1
            self.add(label, {"where": "", "path": "", "message": f"invalid JSON: {inst.msg}", "keyword": "json"})
            return
        errs = sorted(validator.iter_errors(inst), key=lambda e: (list(map(str, e.absolute_path)), e.validator))
        if errs:
            self.invalid += 1
        for e in errs:
            self.add(label, _error("", e))

    def state(self) -> dict[str, Any]:
        return {"errors": self.errors, "total": self.total, "checked": self.checked, "invalid": self.invalid, "groups": self.groups}

    def report(self, path: Path, draft: str, show: int) -> dict[str, Any]:
        groups = sorted(({"path": k.split("\t")[0], "keyword": k.split("\t")[1], "count": v[0], "first": v[1], "message": v[2]} for k, v in self.groups.items()), key=lambda g: -g["count"])
        return {"file": str(path), "mode": "schema", "draft": draft, "valid": self.total == 0, "records": self.checked, "invalid_records": self.invalid, "errors_total": self.total, "error_kinds": groups, "errors": self.errors, "show": show}


def _validate_jsonl_parallel(path: Path, size: int, schema: Any, draft: str, keep: int, show: int) -> dict[str, Any]:
    """A big JSONL file validated in parallel byte ranges (cut at line ends), with exact line numbers."""
    from _common import pool_map, workers_for

    n = workers_for(64)
    jobs = [(str(path), a, b, schema, keep) for a, b in _line_ranges(path, size, max(n * 4, size // (16 * 1024 * 1024) + 1))]
    results = pool_map(_validate_range, jobs, workers=n)
    col = _Collector(keep)
    first_line = 1

    def where(rel: int) -> str:
        return f"line {first_line + rel}"

    for r in results:
        for item in r["errors"]:
            if len(col.errors) < keep:
                rel = item.pop("_rel")
                col.errors.append({**item, "where": where(rel)})
        for key, (count, firsts, msg) in r["groups"].items():
            g = col.groups.get(key)
            if g is None:
                if len(col.groups) >= 200:
                    continue
                g = col.groups[key] = [0, [], msg]
            g[0] += count
            g[1].extend(where(x) for x in firsts[: 5 - len(g[1])])
        col.total += r["total"]
        col.checked += r["checked"]
        col.invalid += r["invalid"]
        first_line += r["lines"]
    rep = col.report(path, draft, show)
    rep["workers"] = n
    return rep


def _validate_range(job: tuple[str, int, int, Any, int]) -> dict[str, Any]:
    """Validates the lines in [start, end) of a JSONL file; locations are 0-based line offsets in the range."""
    path, start, end, schema, keep = job
    validator, _ = _validator(schema)
    col = _Collector(keep)
    rel = -1
    with open(path, "rb") as f:
        f.seek(start)
        pos = start
        while pos < end:
            raw = f.readline()
            if not raw:
                break
            pos += len(raw)
            rel += 1
            if not raw.strip():
                continue
            try:
                inst: Any = json.loads(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw)
            except ValueError as e:
                inst = _BadJSON(str(e))
            col.check(validator, rel, inst)
    out = col.state()
    out["lines"] = rel + 1
    return out


def _error(label: str, e: Any) -> dict[str, Any]:
    from _tree import fmt_path

    msg = e.message
    if len(msg) > 300:
        msg = msg[:300] + "…"
    item = {"where": label, "path": fmt_path(tuple(e.absolute_path)), "message": msg, "keyword": e.validator, "schema_path": "/".join(str(x) for x in e.absolute_schema_path)}
    if e.context:
        subs = sorted({c.message[:120] for c in e.context})[:4]
        item["because"] = subs
    return item


# ── schema inference ────────────────────────────────────────────────────


def infer(a: Any, path: Path, opts: dict[str, Any]) -> int:
    from _formats import sniff
    from _tree import Shape, infer_schema

    info = sniff(path)
    fmt = info["format"]
    sh = Shape()
    n = 0
    each = a.each or fmt == "jsonl" or fmt not in ("json", "yaml", "toml", "xml", "ini")
    table = fmt not in ("json", "jsonl", "yaml", "toml", "xml", "ini")
    if a.sample is not None and a.sample < 1:
        raise UsageError("--sample is 1 or more")
    stats: dict[str, Any] = {}
    if table:
        instances, stats = _table_sample(path, opts, a.sample or 50_000)
    elif fmt == "jsonl" and not a.sample and not info.get("compression") and info["size"] >= _parallel_bytes():
        sh, n = _jsonl_shape_parallel(path, info["size"])
        instances = iter(())
    else:
        instances = (inst for _, inst in _instances(path, opts, each))
    for inst in instances:
        if isinstance(inst, _BadJSON):
            continue
        sh.add(inst)
        n += 1
        if fmt == "jsonl" and a.sample and n >= a.sample:
            stats["note"] = f"shape of the first {n:,} lines (--sample)"
            break
    schema = infer_schema(sh)
    if a.strict:
        _strict(schema, sh)
    if stats.get("columns"):
        _exact_columns(schema, stats, a.strict)
    total = stats.get("rows", n)
    title = f"Inferred from {path.name}" + (f" ({total:,} records, one record per instance)" if each else "")
    if stats.get("note"):
        title += f"; {stats['note']}"
    schema = {"$schema": schema.pop("$schema"), "title": title, **schema}
    text = json.dumps(schema, ensure_ascii=False, indent=2)
    if a.out:
        out = output_path(a.out, [path], a.force)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote a JSON Schema ({'each record' if each else 'the whole document'}, {total:,} instance{'s' if total != 1 else ''}) to {out}." + (f" Note: {stats['note']}." if stats.get("note") else ""))
        extra = (" --each" if each and fmt in ("json", "yaml", "toml") else "") + (f" --path {opts['json_path']}" if opts.get("json_path") else "")
        print(f"Check: python3 scripts/data_validate.py {a.file} --schema {out}{extra}")
    elif a.max_chars and len(text) > a.max_chars:
        props = list((schema.get("properties") or {}))
        print(f"The inferred schema is {len(text):,} characters, over --max-chars {a.max_chars}: write it to a file with --out schema.json.")
        if props:
            print(f"Top-level properties ({len(props)}): " + ", ".join(props[:200]))
    else:
        print(text)
    return 0


def _parallel_bytes() -> int:
    """JSONL files from this size are checked in parallel (8 MB; half of DESK_DATA_BIG_MB when set)."""
    from _formats import big_bytes

    return big_bytes("jsonl") // 2


def _line_ranges(path: Path, size: int, parts: int) -> list[tuple[int, int]]:
    """About `parts` byte ranges of a text file, each starting at a line start."""
    cuts = [0]
    with open(path, "rb") as f:
        for i in range(1, parts):
            f.seek(size * i // parts)
            f.readline()
            pos = f.tell()
            if cuts[-1] < pos < size:
                cuts.append(pos)
    cuts.append(size)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


def _jsonl_shape_parallel(path: Path, size: int) -> tuple[Any, int]:
    """The merged shape of every line of a big JSONL file, built in parallel byte ranges."""
    from _common import pool_map, workers_for
    from _tree import Shape

    n = workers_for(64)
    ranges = _line_ranges(path, size, max(n * 4, size // (16 * 1024 * 1024) + 1))
    sh, count = Shape(), 0
    for part, k in pool_map(_shape_range, [(str(path), a, b) for a, b in ranges], workers=n):
        sh.merge(part)
        count += k
    return sh, count


def _shape_range(job: tuple[str, int, int]) -> tuple[Any, int]:
    from _tree import Shape

    path, start, end = job
    sh, k = Shape(), 0
    with open(path, "rb") as f:
        f.seek(start)
        pos = start
        while pos < end:
            raw = f.readline()
            if not raw:
                break
            pos += len(raw)
            if raw.strip():
                try:
                    sh.add(json.loads(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw))
                    k += 1
                except ValueError:
                    pass
    return sh, k


def _table_sample(path: Path, opts: dict[str, Any], sample: int | None) -> tuple[Iterator[Any], dict[str, Any]]:
    """Rows of a table as objects (a reproducible random sample past `sample` rows), and per-column facts from
    every row: nulls, min/max of numbers, and the values of columns with at most 10 of them."""
    from _duck import columns_of, connect, ident, load, one_table, parse_inputs
    from _out import jsonable

    con = connect()
    ld = load(con, parse_inputs([str(path)])[0], opts)
    t = one_table(con, ld, opts)
    cols = columns_of(con, t)
    n = int(con.sql(f"SELECT count(*) FROM {t}").fetchone()[0])
    stats: dict[str, Any] = {"rows": n, "columns": {}}
    if cols:
        aggs = []
        for c, ty in cols:
            q = ident(c)
            numeric = any(k in ty for k in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL"))
            aggs += [f"count({q})", f"min({q})" if numeric else "NULL", f"max({q})" if numeric else "NULL", f"approx_count_distinct({q})"]
        row = con.sql(f"SELECT {', '.join(aggs)} FROM {t}").fetchone()
        for i, (c, ty) in enumerate(cols):
            nn, lo, hi, dist = row[4 * i : 4 * i + 4]
            info: dict[str, Any] = {"nulls": n - int(nn), "min": jsonable(lo), "max": jsonable(hi)}
            if dist is not None and dist <= 12 and ("VARCHAR" in ty or "INT" in ty or ty == "BOOLEAN"):
                vals = [r[0] for r in con.sql(f"SELECT DISTINCT {ident(c)} FROM {t} WHERE {ident(c)} IS NOT NULL ORDER BY 1 LIMIT 11").fetchall()]
                if len(vals) <= 10:
                    info["values"] = [jsonable(v) for v in vals]
            stats["columns"][c] = info
    src = t
    if sample and n > sample:
        src = f"(SELECT * FROM {t} USING SAMPLE reservoir({int(sample)} ROWS) REPEATABLE (42))"
        stats["note"] = f"shape from a random sample of {sample:,} rows; required columns, ranges and enums from all {n:,}"

    def rows() -> Iterator[Any]:
        cur = con.execute(f"SELECT * FROM {src}")  # not con.cursor(): it does not see Arrow data registered on con
        names = [d[0] for d in cur.description]
        while True:
            batch = cur.fetchmany(10000)
            if not batch:
                return
            for r in batch:
                yield {c: jsonable(v) for c, v in zip(names, r) if v is not None}

    return rows(), stats


def _exact_columns(schema: dict[str, Any], stats: dict[str, Any], strict: bool) -> None:
    """Required columns (never null in any row) and, with --strict, ranges and enums from every row of the table."""
    props = schema.get("properties") or {}
    cols = stats["columns"]
    req = [c for c in props if c in cols and cols[c]["nulls"] == 0]
    if req:
        schema["required"] = req
    else:
        schema.pop("required", None)
    if not strict:
        return
    for c, sub in props.items():
        info = cols.get(c) or {}
        t = sub.get("type")
        if t in ("integer", "number") and info.get("min") is not None:
            sub["minimum"], sub["maximum"] = info["min"], info["max"]
        if t == "string" and "enum" in sub and info.get("values"):
            sub["enum"] = info["values"]
        elif t == "string" and "enum" in sub:
            sub.pop("enum")  # more distinct values in the whole table than in the sample


def _strict(schema: dict[str, Any], sh: Any) -> None:
    """Adds enums (≤ 10 values seen at least twice each on average), ranges and closed objects, in place."""
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        for k, sub in (schema.get("properties") or {}).items():
            if k in sh.keys:
                _strict(sub, sh.keys[k])
    elif t == "array" and "items" in schema and sh.items is not None:
        _strict(schema["items"], sh.items)
    elif t in ("integer", "number") and sh.num_min is not None:
        schema["minimum"] = int(sh.num_min) if t == "integer" else sh.num_min
        schema["maximum"] = int(sh.num_max) if t == "integer" else sh.num_max
    elif t == "string" and sh.enum and len(sh.enum) <= 10 and sum(sh.enum.values()) >= 2 * len(sh.enum):
        schema["enum"] = sorted(sh.enum)


# ── table rules ─────────────────────────────────────────────────────────

TYPE_CHECK = {
    "integer": "TRY_CAST(CAST({c} AS VARCHAR) AS BIGINT) IS NULL OR (TRY_CAST(CAST({c} AS VARCHAR) AS DOUBLE) != TRY_CAST(CAST({c} AS VARCHAR) AS BIGINT))",
    "number": "TRY_CAST(replace(CAST({c} AS VARCHAR), ',', '.') AS DOUBLE) IS NULL",
    "boolean": "lower(CAST({c} AS VARCHAR)) NOT IN ('true', 'false', '1', '0', 'yes', 'no', 't', 'f', 'y', 'n')",
    "date": "TRY_CAST(CAST({c} AS VARCHAR) AS DATE) IS NULL",
    "datetime": "TRY_CAST(CAST({c} AS VARCHAR) AS TIMESTAMP) IS NULL",
    "string": "false",
}


def validate_rules(a: Any, path: Path, opts: dict[str, Any]) -> dict[str, Any]:
    from _duck import connect, ident, load, one_table, parse_inputs, sql_str

    rules = _load_spec(a.rules, "--rules")
    if not isinstance(rules, dict):
        raise UsageError("rules must be an object with 'columns' (and optionally unique, row_count, no_extra_columns)")
    cols_rules = rules.get("columns") or {}
    if isinstance(cols_rules, list):
        cols_rules = {c["name"]: {k: v for k, v in c.items() if k != "name"} for c in cols_rules}
    unknown = set(rules) - {"columns", "unique", "row_count", "no_extra_columns", "empty_is_null"}
    if unknown:
        raise UsageError(f"unknown rule keys: {', '.join(sorted(unknown))}")
    con = connect()
    spec = parse_inputs([str(path)])[0]
    ld = load(con, spec, opts)
    table = one_table(con, ld, opts)
    con.execute(f"CREATE TEMP TABLE __v AS SELECT row_number() OVER () AS __row, * FROM {table}")
    have = [c for c in con.sql("SELECT * FROM __v LIMIT 0").columns if c != "__row"]
    lower = {c.lower(): c for c in have}
    n = int(con.sql("SELECT count(*) FROM __v").fetchone()[0])
    empty_null = rules.get("empty_is_null", True)
    results: list[dict[str, Any]] = []
    k = 100_000 if a.out else max(1, min(a.max_errors, 50))

    def check(rule: str, column: str | None, cond: str, show: str | None = None) -> None:
        rows = con.sql(f"SELECT count(*), list(__row ORDER BY __row)[1:{k}], list(DISTINCT {show or 'NULL'})[1:5] FROM __v WHERE {cond}").fetchone()
        results.append({"rule": rule, "column": column, "violations": int(rows[0]), "rows": rows[1] or [], "examples": [x for x in (rows[2] or []) if x is not None]})

    for name, r in cols_rules.items():
        if not isinstance(r, dict):
            raise UsageError(f"rules for column {name!r} must be an object")
        bad_keys = set(r) - {"type", "required", "not_null", "unique", "min", "max", "regex", "pattern", "allowed", "min_length", "max_length", "format"}
        if bad_keys:
            raise UsageError(f"column {name}: unknown rule(s) {', '.join(sorted(bad_keys))}")
        col = lower.get(str(name).lower())
        if col is None:
            results.append({"rule": "required column", "column": name, "violations": 1 if r.get("required", True) else 0, "rows": [], "examples": [], "note": "column missing"})
            continue
        c = ident(col)
        txt = f"CAST({c} AS VARCHAR)"
        present = f"{c} IS NOT NULL" + (f" AND trim({txt}) != ''" if empty_null else "")
        if r.get("not_null"):
            check("not null", col, f"NOT ({present})")
        t = r.get("type")
        if t:
            if t not in TYPE_CHECK:
                raise UsageError(f"column {col}: type must be one of {', '.join(TYPE_CHECK)}")
            if t in ("date", "datetime") and r.get("format"):
                bad = f"try_strptime({txt}, {sql_str(r['format'])}) IS NULL"
            else:
                bad = TYPE_CHECK[t].format(c=c)
            check(f"type {t}" + (f" ({r['format']})" if r.get("format") else ""), col, f"{present} AND ({bad})", txt)
        if r.get("unique"):
            dup = con.sql(f"SELECT count(*), list(rows ORDER BY rows[1])[1:5] FROM (SELECT list(__row ORDER BY __row) AS rows FROM __v WHERE {present} GROUP BY {c} HAVING count(*) > 1)").fetchone()
            groups = int(dup[0])
            results.append({"rule": "unique", "column": col, "violations": groups, "rows": [x for g in (dup[1] or []) for x in g][:k], "examples": [], "note": f"{groups} repeated values" if groups else ""})
        for key, op in (("min", "<"), ("max", ">")):
            if key in r:
                v = r[key]
                if isinstance(v, (int, float)):
                    cmp = f"TRY_CAST(replace({txt}, ',', '.') AS DOUBLE) {op} {float(v)!r}"
                else:
                    cmp = f"{txt} {op} {sql_str(str(v))}" if t not in ("date", "datetime") else f"TRY_CAST({txt} AS TIMESTAMP) {op} TRY_CAST({sql_str(str(v))} AS TIMESTAMP)"
                check(f"{key} {v}", col, f"{present} AND {cmp}", txt)
        rx = r.get("regex") or r.get("pattern")
        if rx:
            try:
                re.compile(rx)
            except re.error as e:
                raise UsageError(f"column {col}: bad regex: {e}") from None
            check(f"regex {rx}", col, f"{present} AND NOT regexp_full_match({txt}, {sql_str(rx)})", txt)
        if "allowed" in r:
            vals = r["allowed"]
            if not isinstance(vals, list):
                raise UsageError(f"column {col}: allowed must be a list")
            lst = ", ".join(sql_str(_scalar(v)) for v in vals)
            check(f"allowed {len(vals)} values", col, f"{present} AND {txt} NOT IN ({lst})", txt)
        if "min_length" in r:
            check(f"min_length {r['min_length']}", col, f"{present} AND length({txt}) < {int(r['min_length'])}", txt)
        if "max_length" in r:
            check(f"max_length {r['max_length']}", col, f"{c} IS NOT NULL AND length({txt}) > {int(r['max_length'])}", txt)
    for combo in rules.get("unique") or []:
        if isinstance(combo, str):
            combo = [combo]
        cs = [lower.get(str(x).lower()) for x in combo]
        if None in cs:
            results.append({"rule": "unique " + "+".join(map(str, combo)), "column": None, "violations": 1, "rows": [], "examples": [], "note": "column missing"})
            continue
        keys = ", ".join(ident(x) for x in cs)
        dup = con.sql(f"SELECT count(*), list(rows ORDER BY rows[1])[1:5] FROM (SELECT list(__row ORDER BY __row) AS rows FROM __v GROUP BY {keys} HAVING count(*) > 1)").fetchone()
        results.append({"rule": "unique " + " + ".join(cs), "column": None, "violations": int(dup[0]), "rows": [x for g in (dup[1] or []) for x in g][:50], "examples": []})
    rc = rules.get("row_count")
    if rc:
        bad = (rc.get("min") is not None and n < rc["min"]) or (rc.get("max") is not None and n > rc["max"])
        bounds = " and ".join(f"{'at least' if k == 'min' else 'at most'} {rc[k]:,}" for k in ("min", "max") if isinstance(rc, dict) and rc.get(k) is not None)
        results.append({"rule": f"row count {bounds}", "column": None, "violations": int(bool(bad)), "rows": [], "examples": [], "note": f"{n} rows"})
    if rules.get("no_extra_columns"):
        declared = {str(x).lower() for x in cols_rules}
        extra = [c for c in have if c.lower() not in declared]
        results.append({"rule": "no extra columns", "column": None, "violations": len(extra), "rows": [], "examples": extra[:10]})
    total = sum(r["violations"] for r in results)
    return {"file": str(path), "mode": "rules", "valid": total == 0, "rows": n, "violations_total": total, "results": results, "header_lines": 1 if ld.fmt == "csv" else 0}


def _scalar(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


# ── output ──────────────────────────────────────────────────────────────


def render(r: dict[str, Any]) -> str:
    name = Path(r["file"]).name
    if r["mode"] == "schema":
        head = f"**{'VALID' if r['valid'] else 'INVALID'}**: {name} against the schema ({r['draft']})"
        lines = [head, f"- {r['records']:,} instance(s) checked; {r['invalid_records']:,} invalid; {r['errors_total']:,} error(s) · {r['seconds']}s"]
        kinds = r.get("error_kinds") or []
        if any(k["count"] > 1 for k in kinds) or r["errors_total"] > len(r["errors"]):
            lines += ["", "Errors by kind:", ""]
            rows = [[k["path"], k["keyword"], f"{k['count']:,}", ", ".join(str(x) for x in k["first"]) + (" …" if k["count"] > len(k["first"]) else ""), k["message"][:80]] for k in kinds[:20]]
            lines.append(md_table(["path", "rule", "errors", "first at", "e.g."], rows))
            if len(kinds) > 20:
                lines.append(f"… {len(kinds) - 20} more kinds")
        if r["errors"]:
            lines += ["", "First errors:" if r["errors_total"] > 1 else "Error:"]
            for e in r["errors"][: r.get("show", 200)]:
                if e["where"].startswith("$"):
                    where = e["where"] + (e["path"][1:] if e["path"].startswith("$") else "")
                else:
                    where = e["where"] + ("" if e["path"] in ("$", "") else f" {e['path']}")
                lines.append(f"- {where}: {e['message']}  [{e['keyword']}]")
                for b in e.get("because", []):
                    lines.append(f"    - {b}")
            shown = min(len(r["errors"]), r.get("show", 200))
            if r["errors_total"] > shown:
                lines.append(f"- … {r['errors_total'] - shown:,} more (raise --max-errors, or --out errors.csv)")
        return "\n".join(lines)
    lines = [f"**{'VALID' if r['valid'] else 'INVALID'}**: {name} ({r['rows']:,} rows) against the rules · {r['violations_total']:,} violation(s) · {r['seconds']}s", ""]
    for x in r["results"]:
        ok = x["violations"] == 0
        col = f"`{x['column']}` " if x["column"] else ""
        line = f"- {'ok ' if ok else 'FAIL'} {col}{x['rule']}"
        if not ok:
            line += f": {x['violations']:,}"
            if x["rows"]:
                line += " — rows " + ", ".join(str(v) for v in x["rows"][:12]) + (" …" if x["violations"] > len(x["rows"][:12]) else "")
            if x["examples"]:
                line += " — e.g. " + ", ".join(repr(v)[:40] for v in x["examples"][:4])
        if x.get("note"):
            line += f" ({x['note']})"
        lines.append(line)
    if r.get("header_lines"):
        lines += ["", "Row numbers count data rows from 1 (file line = row + 1 for the header)."]
    return "\n".join(lines)


def write_report(r: dict[str, Any], out: Path) -> None:
    if out.suffix.lower() == ".json":
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return
    import csv

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if r["mode"] == "schema":
            w.writerow(["where", "path", "keyword", "message"])
            for e in r["errors"]:
                w.writerow([e["where"], e["path"], e["keyword"], e["message"]])
        else:
            w.writerow(["rule", "column", "violations", "rows", "examples"])
            for x in r["results"]:
                w.writerow([x["rule"], x["column"] or "", x["violations"], " ".join(map(str, x["rows"])), json.dumps(x["examples"], default=str)])


if __name__ == "__main__":
    run_main(main)
