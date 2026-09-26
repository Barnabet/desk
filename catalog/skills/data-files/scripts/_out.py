"""Output for the data-files skill: cells and rows within a character budget, continuation commands, and writers
for every output format (CSV/TSV, JSON/JSONL, Parquet, Arrow/Feather, Avro, SQLite, DuckDB, XML, YAML, TOML, INI,
Markdown, SPSS/Stata/SAS transport)."""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import os
import re
import shlex
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError, UsageError, md_escape_cell

MAX_CELL = 200

OUT_EXT = {
    ".csv": "csv", ".txt": "csv", ".tsv": "tsv", ".tab": "tsv", ".psv": "psv",
    ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".parquet": "parquet", ".pq": "parquet", ".arrow": "arrow", ".feather": "arrow", ".ipc": "arrow", ".arrows": "arrows",
    ".avro": "avro", ".sqlite": "sqlite", ".sqlite3": "sqlite", ".db": "sqlite", ".duckdb": "duckdb", ".ddb": "duckdb",
    ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".md": "md",
    ".sav": "sav", ".zsav": "zsav", ".dta": "dta", ".xpt": "xpt",
}
OUT_FORMATS = sorted(set(OUT_EXT.values()))
TEXT_TABLE = ("csv", "tsv", "psv")


def out_format(path: Path, explicit: str | None = None) -> tuple[str, str | None]:
    """(format, compression) for an output path like out.csv.gz, or an explicit --to."""
    from _formats import COMPRESSION_EXT

    suffixes = [s.lower() for s in path.suffixes]
    comp = None
    if suffixes and suffixes[-1] in COMPRESSION_EXT:
        comp = COMPRESSION_EXT[suffixes[-1]]
        suffixes = suffixes[:-1]
    if explicit:
        f = explicit.lower()
        if f not in OUT_FORMATS:
            raise UsageError(f"unknown output format {explicit}; one of {', '.join(OUT_FORMATS)}")
        return f, comp
    ext = suffixes[-1] if suffixes else ""
    if ext in (".xlsx", ".xls", ".ods"):
        raise UsageError("writing spreadsheets is the spreadsheets skill's job (sheet_create.py / sheet_convert.py); write .csv or .parquet here")
    if ext not in OUT_EXT:
        raise UsageError(f"cannot tell the output format from {path.name}; use one of {', '.join(sorted(OUT_EXT))} or pass --to")
    if comp and OUT_EXT[ext] not in ("csv", "tsv", "psv", "json", "jsonl", "xml", "yaml", "md"):
        raise UsageError(f"{path.name}: .{comp} compression applies to text formats; Parquet/Arrow/Avro have their own codecs (--codec)")
    if comp in ("bz2", "xz"):
        raise UsageError("text outputs can be compressed with .gz or .zst")
    return OUT_EXT[ext], comp


# ── cells and rows ──────────────────────────────────────────────────────


def cell(v: Any, max_cell: int | None = MAX_CELL) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        s = float_text(v)
    elif isinstance(v, (int, Decimal)):
        s = str(v)
    elif isinstance(v, _dt.datetime):
        s = v.isoformat(sep=" ")
    elif isinstance(v, (_dt.date, _dt.time)):
        s = v.isoformat()
    elif isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        s = "0x" + b[:48].hex() + ("…" if len(b) > 48 else "")
    elif isinstance(v, (dict, list, tuple)):
        s = json.dumps(_tidy(jsonable(v)), ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        s = str(v)
    if max_cell and len(s) > max_cell:
        s = s[:max_cell] + f"…[+{len(s) - max_cell} chars]"
    return s


def _tidy(v: Any) -> Any:
    """Floats inside lists and structs as float_text shows them (for printing only)."""
    if isinstance(v, float):
        return float(float_text(v))
    if isinstance(v, dict):
        return {k: _tidy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_tidy(x) for x in v]
    return v


def float_text(v: float) -> str:
    """repr(), except that binary noise past 15 significant digits is rounded away (0.30000000000000004 → 0.3)."""
    s = repr(v)
    mantissa = s.split("e")[0].lstrip("-").replace(".", "").lstrip("0")
    if len(mantissa) > 15:
        return f"{v:.15g}"
    return s


def jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v) if v != v.to_integral_value() else int(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, _dt.timedelta):
        return str(v)
    return v


def render_rows(cols: Sequence[str], rows: Iterable[Sequence[Any]], fmt: str, max_chars: int | None, max_cell: int | None = MAX_CELL) -> tuple[str, int]:
    """Rows as md/csv/tsv/json/jsonl text, cut at a row boundary to fit max_chars: (text, rows shown)."""
    budget = max_chars or 10**12
    out: list[str] = []
    used = 0
    shown = 0
    if fmt == "md":
        head = "| " + " | ".join(md_escape_cell(c) for c in cols) + " |\n|" + "|".join("---" for _ in cols) + "|"
        out.append(head)
        used = len(head)
        for r in rows:
            line = "| " + " | ".join(md_escape_cell(cell(v, max_cell)) for v in r) + " |"
            if used + len(line) + 1 > budget and shown:
                break
            out.append(line)
            used += len(line) + 1
            shown += 1
        return "\n".join(out), shown
    if fmt in ("csv", "tsv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="," if fmt == "csv" else "\t", lineterminator="\n")
        w.writerow(cols)
        out.append(buf.getvalue())
        used = len(out[0])
        for r in rows:
            buf.seek(0)
            buf.truncate()
            w.writerow([cell(v, max_cell) for v in r])
            line = buf.getvalue()
            if used + len(line) > budget and shown:
                break
            out.append(line)
            used += len(line)
            shown += 1
        return "".join(out).rstrip("\n"), shown
    if fmt in ("json", "jsonl"):
        items = []
        for r in rows:
            obj = {c: _json_cell(v, max_cell) for c, v in zip(cols, r)}
            s = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":") if fmt == "jsonl" else (", ", ": "))
            if used + len(s) + 2 > budget and shown:
                break
            items.append(s)
            used += len(s) + 2
            shown += 1
        if fmt == "jsonl":
            return "\n".join(items), shown
        return "[\n  " + ",\n  ".join(items) + "\n]" if items else "[]", shown
    raise UsageError(f"cannot print {fmt}")


def _json_cell(v: Any, max_cell: int | None) -> Any:
    v = jsonable(v)
    if isinstance(v, str) and max_cell and len(v) > max_cell:
        return v[:max_cell] + f"…[+{len(v) - max_cell} chars]"
    return v


def json_columns(rel: Any) -> list[str]:
    """Columns whose type holds DuckDB JSON values (JSON, JSON[], STRUCT(… JSON …))."""
    return [c for c, t in zip(rel.columns, rel.types) if "JSON" in str(t)]


def display_relation(rel: Any) -> tuple[Any, list[str]]:
    """(relation to print, JSON columns). DuckDB hands JSON values to Python as text, so [-87.3, 35.0] inside a
    JSON[] list would print as ["-87.3","35.0"]: nested columns holding JSON are turned into one JSON text each."""
    cols = json_columns(rel)
    nested = {c for c, t in zip(rel.columns, rel.types) if "JSON" in str(t) and str(t) != "JSON"}
    if not nested:
        return rel, cols

    def q(n: str) -> str:
        return '"' + n.replace('"', '""') + '"'

    exprs = [f"CAST(to_json({q(c)}) AS VARCHAR) AS {q(c)}" if c in nested else q(c) for c in rel.columns]
    return rel.project(", ".join(exprs)), cols


def parse_json_cells(row: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    """For JSON output: JSON text in JSON-typed columns back to values."""
    for c in cols:
        v = row.get(c)
        if isinstance(v, str):
            try:
                row[c] = json.loads(v)
            except ValueError:
                pass
    return row


def print_json(obj: Any, max_chars: int | None, trim: Sequence[str] = (), hint: str = "") -> None:
    """Prints JSON that is never cut mid-way (it would not parse). Over the budget, a list is shortened to the items
    that fit, and in an object the lists named in `trim` are shortened (a `truncated` key says what was left out);
    a stderr note gives the hint. Anything that still does not fit is printed whole."""
    from _common import emit

    def dumps(o: Any) -> str:
        return json.dumps(o, ensure_ascii=False, indent=1, default=str)

    text = dumps(obj)
    if not max_chars or len(text) <= max_chars:
        print(text)
        return
    if isinstance(obj, list):
        emit(obj, "json", max_chars=max_chars, hint=hint)
        return
    if not isinstance(obj, dict):
        print(text)
        return
    obj = dict(obj)
    full = {k: obj[k] for k in trim if isinstance(obj.get(k), list) and len(obj[k]) > 1}
    for k in sorted(full, key=lambda k: -len(dumps(full[k]))):
        lo, hi = 1, len(full[k])
        while lo < hi:  # the longest prefix of this list that fits
            mid = (lo + hi + 1) // 2
            obj[k] = full[k][:mid]
            if len(dumps(obj)) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        obj[k] = full[k][:lo]
        if len(dumps(obj)) <= max_chars:
            break
    cut = {k: {"shown": len(obj[k]), "total": len(v)} for k, v in full.items() if len(obj[k]) < len(v)}
    if cut:
        obj["truncated"] = cut
        print("[… truncated to fit --max-chars " + str(max_chars) + ": " + ", ".join(f"{k} {c['shown']} of {c['total']}" for k, c in cut.items()) + (f". {hint}" if hint else "") + "]", file=sys.stderr)
    print(dumps(obj))


# ── continuation commands ───────────────────────────────────────────────


def command(updates: dict[str, Any] | None = None, drop: Iterable[str] = ()) -> str:
    """This script's command line with some flags replaced, for 'next page' hints."""
    updates = dict(updates or {})
    drop = set(drop) | set(updates)
    args = sys.argv[1:]
    kept: list[str] = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        flag = a.split("=", 1)[0]
        if flag in drop:
            if "=" not in a and i + 1 < len(args) and not _is_flag_only(flag):
                skip_next = True
            continue
        kept.append(a)
    for k, v in updates.items():
        if v is None or v is False:
            continue
        kept.append(k)
        if v is not True:
            kept.append(str(v))
    script = "scripts/" + Path(sys.argv[0]).name
    return "python3 " + " ".join([script] + [_quote(a) for a in kept])


_FLAG_ONLY = {"--force", "--no-cache", "--all", "--explain", "--analyze", "--tables", "--flatten", "--nest", "--dedupe", "--labels", "--all-text", "--map", "--full", "--minify", "--sort-keys", "--keys", "--values", "--regex", "--case-sensitive", "--infer", "--stream", "--user-missing", "--doc"}


def _is_flag_only(flag: str) -> bool:
    return flag in _FLAG_ONLY


def _quote(a: str) -> str:
    if os.name == "nt":
        return a if re.fullmatch(r"[\w@%+=:,./\\-]+", a) else '"' + a.replace('"', '\\"') + '"'
    return shlex.quote(a)


def more_line(omitted: str, cmd: str) -> str:
    return f"[… {omitted}. Next: {cmd}]"


# ── writers ─────────────────────────────────────────────────────────────


def flat_select(con: Any, sql: str, lists: str = "json") -> str:
    """SQL flattening STRUCT columns to dotted columns and LIST/MAP columns to JSON text (for CSV-like outputs)."""
    rel = con.sql(f"SELECT * FROM ({sql}) LIMIT 0")
    exprs: list[str] = []
    changed = False

    def ident(n: str) -> str:
        return '"' + n.replace('"', '""') + '"'

    def walk(expr: str, name: str, t: Any, depth: int) -> None:
        nonlocal changed
        tid = t.id
        if tid == "struct" and depth < 8:
            changed = True
            for child, ct in t.children:
                walk(f"{expr}[{_sq(child)}]", f"{name}.{child}", ct, depth + 1)
        elif tid in ("list", "map", "array", "union") or (tid == "struct"):
            changed = True
            exprs.append(f"CAST(to_json({expr}) AS VARCHAR) AS {ident(name)}")
        else:
            exprs.append(f"{expr} AS {ident(name)}")

    for c, t in zip(rel.columns, rel.types):
        walk(ident(c), c, t, 0)
    if not changed:
        return sql
    return f"SELECT {', '.join(exprs)} FROM ({sql}) AS _flat"


def _sq(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def nest_select(con: Any, sql: str, sep: str = ".") -> str:
    """SQL turning dotted column names (a.b, a.c) back into STRUCT columns (a: {b, c})."""
    rel = con.sql(f"SELECT * FROM ({sql}) LIMIT 0")
    tree: dict[str, Any] = {}
    order: list[str] = []
    for c in rel.columns:
        parts = c.split(sep)
        if len(parts) == 1 or any(not p for p in parts):
            tree[c] = c
            order.append(c)
            continue
        node = tree
        if parts[0] not in tree:
            order.append(parts[0])
        for p in parts[:-1]:
            nxt = node.get(p)
            if not isinstance(nxt, dict):
                nxt = node[p] = {}
            node = nxt
        node[parts[-1]] = c

    def ident(n: str) -> str:
        return '"' + n.replace('"', '""') + '"'

    def build(node: Any) -> str:
        if isinstance(node, str):
            return ident(node)
        return "struct_pack(" + ", ".join(f"{ident(k)} := {build(v)}" for k, v in node.items()) + ")"

    if all(isinstance(tree[k], str) for k in order):
        return sql
    return "SELECT " + ", ".join(f"{build(tree[k])} AS {ident(k)}" for k in order) + f" FROM ({sql}) AS _nest"


def write_table(con: Any, sql: str, out: Path, fmt: str, compression: str | None = None, *, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Writes the rows of `sql` to `out` as `fmt`; returns {'rows', 'notes'}."""
    from _duck import sql_str

    o = opts or {}
    notes: list[str] = []
    if fmt in TEXT_TABLE or fmt == "md":
        sql = flat_select(con, sql)
    if o.get("decimal_comma") and fmt in TEXT_TABLE:
        sql = _decimal_comma_select(con, sql)
    rows = int(con.sql(f"SELECT count(*) FROM ({sql})").fetchone()[0])
    comp_opt = f", COMPRESSION {compression}" if compression in ("gzip", "zstd") else ""
    tmp = out.with_name(f".{out.name}.partial")
    target = tmp
    try:
        if fmt in TEXT_TABLE:
            delim = o.get("delimiter") or {"csv": ",", "tsv": "\t", "psv": "|"}[fmt]
            header = "false" if o.get("no_header") else "true"
            extra = ""
            if o.get("quote_all"):
                extra += ", FORCE_QUOTE *"
            if o.get("date_format"):
                extra += f", DATEFORMAT {sql_str(o['date_format'])}"
            if o.get("timestamp_format"):
                extra += f", TIMESTAMPFORMAT {sql_str(o['timestamp_format'])}"
            if o.get("null_text") is not None:
                extra += f", NULLSTR {sql_str(o['null_text'])}"
            enc = (o.get("out_encoding") or "utf-8").lower()
            post = o.get("bom") or enc not in ("utf-8", "utf8")
            con.execute(f"COPY ({sql}) TO {sql_str(target)} (FORMAT csv, HEADER {header}, DELIMITER {sql_str(delim)}{extra}{'' if post else comp_opt})")
            if post:
                _recode(target, enc, bool(o.get("bom")), compression)
                if enc not in ("utf-8", "utf8"):
                    notes.append(f"encoded as {enc}")
        elif fmt in ("json", "jsonl"):
            arr = "true" if fmt == "json" else "false"
            con.execute(f"COPY ({sql}) TO {sql_str(target)} (FORMAT json, ARRAY {arr}{comp_opt})")
        elif fmt == "parquet":
            codec = (o.get("codec") or "zstd").lower()
            if codec not in ("zstd", "snappy", "gzip", "lz4", "brotli", "uncompressed", "none"):
                raise UsageError("Parquet codecs: zstd (default), snappy, gzip, lz4, brotli, uncompressed")
            codec = "uncompressed" if codec == "none" else codec
            rg = int(o.get("row_group_size") or 122880)
            con.execute(f"COPY ({sql}) TO {sql_str(target)} (FORMAT parquet, COMPRESSION {codec}, ROW_GROUP_SIZE {rg})")
        elif fmt in ("arrow", "arrows"):
            _write_arrow(con, sql, target, fmt == "arrows", o.get("codec"))
        elif fmt == "avro":
            _write_avro(con, sql, target, o.get("codec"))
        elif fmt == "sqlite":
            notes += _write_sqlite(con, sql, target, o.get("table_name") or "data")
        elif fmt == "duckdb":
            _write_duckdb(con, sql, target, o.get("table_name") or "data")
        elif fmt == "xml":
            _write_xml(con, sql, target, o.get("xml_root") or "rows", o.get("xml_row") or "row", compression)
        elif fmt in ("yaml", "toml", "ini", "md"):
            notes += _write_doc(con, sql, target, fmt, o, compression)
        elif fmt in ("sav", "zsav", "dta", "xpt"):
            notes += _write_stat(con, sql, target, fmt, o)
        else:
            raise UsageError(f"cannot write {fmt}")
        os.replace(target, out)
    except BaseException:
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        raise
    return {"rows": rows, "notes": notes}


def _decimal_comma_select(con: Any, sql: str) -> str:
    rel = con.sql(f"SELECT * FROM ({sql}) LIMIT 0")
    reps = [c for c, t in zip(rel.columns, rel.types) if t.id in ("double", "float", "decimal")]
    if not reps:
        return sql
    rep = ", ".join(f"replace(CAST(\"{c.replace(chr(34), chr(34) * 2)}\" AS VARCHAR), '.', ',') AS \"{c.replace(chr(34), chr(34) * 2)}\"" for c in reps)
    return f"SELECT * REPLACE ({rep}) FROM ({sql})"


def _recode(path: Path, enc: str, bom: bool, compression: str | None) -> None:
    """Re-encodes a UTF-8 file in place (streaming), optionally with a BOM, then compresses it."""
    import codecs

    tmp = path.with_name(path.name + ".re")
    dec = codecs.getincrementaldecoder("utf-8")()
    with open(path, "rb") as src, _open_compressed(tmp, compression) as dst:
        if bom:
            if enc in ("utf-8", "utf8"):
                dst.write(codecs.BOM_UTF8)
            elif enc.startswith("utf-16"):
                pass  # the utf-16 codec writes its own BOM
        encoder = codecs.getincrementalencoder(enc)(errors="replace") if enc not in ("utf-8", "utf8") else None
        while True:
            chunk = src.read(4 * 1024 * 1024)
            if not chunk:
                break
            if encoder is None:
                dst.write(chunk)
            else:
                dst.write(encoder.encode(dec.decode(chunk)))
        if encoder is not None:
            dst.write(encoder.encode(dec.decode(b"", final=True), final=True))
    os.replace(tmp, path)


def _open_compressed(path: Path, compression: str | None) -> Any:
    if compression == "gzip":
        import gzip

        return gzip.open(path, "wb")
    if compression == "zstd":
        import pyarrow as pa

        return pa.CompressedOutputStream(str(path), "zstd")
    return open(path, "wb")


def _write_arrow(con: Any, sql: str, out: Path, stream: bool, codec: str | None) -> None:
    import pyarrow as pa
    import pyarrow.ipc as ipc

    reader = con.sql(sql).fetch_record_batch(100_000)
    c = (codec or "").lower() or None
    if c in ("none", "uncompressed"):
        c = None
    if c not in (None, "lz4", "zstd"):
        raise UsageError("Arrow codecs: lz4, zstd or none")
    options = ipc.IpcWriteOptions(compression=c)
    sink = pa.OSFile(str(out), "wb")
    try:
        writer = (ipc.new_stream if stream else ipc.new_file)(sink, reader.schema, options=options)
        for batch in reader:
            writer.write_batch(batch)
        writer.close()
    finally:
        sink.close()


def arrow_to_avro(t: Any, name: str, names: dict[str, int]) -> Any:
    import pyarrow as pa

    def nullable(x: Any) -> Any:
        return ["null", x]

    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "long" if t.bit_width > 32 or (not pa.types.is_signed_integer(t) and t.bit_width >= 32) else "int"
    if pa.types.is_float32(t):
        return "float"
    if pa.types.is_floating(t):
        return "double"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    if pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return "bytes"
    if pa.types.is_date(t):
        return {"type": "int", "logicalType": "date"}
    if pa.types.is_timestamp(t):
        return {"type": "long", "logicalType": "timestamp-micros"}
    if pa.types.is_time(t):
        return {"type": "long", "logicalType": "time-micros"}
    if pa.types.is_decimal(t):
        return {"type": "bytes", "logicalType": "decimal", "precision": t.precision, "scale": t.scale}
    if pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        return {"type": "array", "items": nullable(arrow_to_avro(t.value_type, name + "_item", names))}
    if pa.types.is_map(t):
        return {"type": "map", "values": nullable(arrow_to_avro(t.item_type, name + "_value", names))}
    if pa.types.is_struct(t):
        base = re.sub(r"\W", "_", name) or "record"
        if base[0].isdigit():
            base = "r_" + base
        names[base] = names.get(base, 0) + 1
        rec_name = base if names[base] == 1 else f"{base}_{names[base]}"
        return {"type": "record", "name": rec_name, "fields": [{"name": _avro_field(f.name), "type": nullable(arrow_to_avro(f.type, f.name, names)), "default": None} for f in t]}
    return "string"


def _avro_field(n: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", n)
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "_" + s
    return s


def _write_avro(con: Any, sql: str, out: Path, codec: str | None) -> None:
    import fastavro
    import pyarrow as pa

    reader = con.sql(sql).fetch_record_batch(50_000)
    schema = reader.schema
    fields = []
    rename = {}
    names: dict[str, int] = {}
    for f in schema:
        an = _avro_field(f.name)
        rename[f.name] = an
        fields.append({"name": an, "type": ["null", arrow_to_avro(f.type, f.name, names)], "default": None})
    avro_schema = fastavro.parse_schema({"type": "record", "name": "Row", "fields": fields})
    from _formats import avro_codecs

    avro_codecs()
    c = (codec or "deflate").lower()
    c = {"none": "null", "uncompressed": "null", "zstd": "zstandard"}.get(c, c)
    if c not in ("null", "deflate", "bzip2", "xz", "snappy", "zstandard"):
        raise UsageError("Avro codecs: deflate (default), snappy, zstd, bzip2, xz or none")
    maps = [f.name for f in schema if pa.types.is_map(f.type)]

    def records() -> Any:
        for batch in reader:
            for r in batch.to_pylist():
                if maps:
                    for m in maps:
                        if r.get(m) is not None:
                            r[m] = dict(r[m])
                yield {rename[k]: _avro_value(v) for k, v in r.items()}

    with open(out, "wb") as f:
        fastavro.writer(f, avro_schema, records(), codec=c)


def _avro_value(v: Any) -> Any:
    if isinstance(v, _dt.datetime) and v.tzinfo is None:
        return v.replace(tzinfo=_dt.timezone.utc)
    if isinstance(v, dict):
        return {k: _avro_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_avro_value(x) for x in v]
    return v


def _sqlite_type(t: Any) -> str:
    tid = t.id
    if tid in ("tinyint", "smallint", "integer", "bigint", "utinyint", "usmallint", "uinteger", "ubigint", "hugeint", "boolean"):
        return "INTEGER"
    if tid in ("float", "double", "decimal"):
        return "REAL"
    if tid in ("blob",):
        return "BLOB"
    if tid in ("date",):
        return "DATE"
    if tid.startswith("timestamp"):
        return "TIMESTAMP"
    return "TEXT"


def _sqlite_value(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bytes)):
        return v
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, _dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(jsonable(v), ensure_ascii=False)
    if isinstance(v, _dt.timedelta):
        return str(v)
    return str(v)


def _write_sqlite(con: Any, sql: str, out: Path, table: str) -> list[str]:
    import sqlite3

    rel = con.sql(sql)
    cols = rel.columns
    types = rel.types
    notes = []
    db = sqlite3.connect(str(out))
    try:
        qt = '"' + table.replace('"', '""') + '"'
        coldefs = ", ".join('"' + c.replace('"', '""') + '" ' + _sqlite_type(t) for c, t in zip(cols, types))
        db.execute(f"CREATE TABLE {qt} ({coldefs})")
        ph = ", ".join("?" for _ in cols)
        ins = f"INSERT INTO {qt} VALUES ({ph})"
        needs = [i for i, t in enumerate(types) if _sqlite_type(t) not in ("INTEGER", "REAL", "TEXT", "BLOB") or t.id in ("boolean", "decimal", "struct", "list", "map", "hugeint", "ubigint", "uuid", "interval", "time")]
        cur = con.execute(sql)  # not con.cursor(): a cursor does not see Arrow data registered on con
        while True:
            batch = cur.fetchmany(50_000)
            if not batch:
                break
            if needs:
                batch = [tuple(_sqlite_value(v) for v in r) for r in batch]
            db.executemany(ins, batch)
        db.commit()
        notes.append(f"table {table}")
    finally:
        db.close()
    return notes


def _write_duckdb(con: Any, sql: str, out: Path, table: str, schema: str = "main") -> None:
    from _duck import ident, sql_str

    con.execute(f"ATTACH {sql_str(out)} AS __desk_out")
    try:
        if schema != "main":
            con.execute(f"CREATE SCHEMA IF NOT EXISTS __desk_out.{ident(schema)}")
        con.execute(f"CREATE TABLE __desk_out.{ident(schema)}.{ident(table)} AS {sql}")
        con.execute("CHECKPOINT __desk_out")
    finally:
        con.execute("DETACH __desk_out")
    wal = out.with_name(out.name + ".wal")
    if wal.exists():
        wal.unlink()


def write_database(con: Any, tables: list[tuple[str, str]], out: Path, fmt: str) -> dict[str, int]:
    """Writes several (name, SQL) tables into one new SQLite or DuckDB file; returns rows per table.

    A DuckDB name like sales.orders keeps its schema; SQLite has none, so it becomes sales_orders.
    """
    tmp = out.with_name(f".{out.name}.partial")
    wal = tmp.with_name(tmp.name + ".wal")
    for p in (tmp, wal):
        if p.exists():
            p.unlink()
    rows: dict[str, int] = {}
    try:
        for name, sql in tables:
            rows[name] = int(con.sql(f"SELECT count(*) FROM ({sql})").fetchone()[0])
            if fmt == "sqlite":
                _write_sqlite(con, sql, tmp, name.replace(".", "_"))
            else:
                schema, _, table = name.rpartition(".")
                _write_duckdb(con, sql, tmp, table, schema or "main")
        os.replace(tmp, out)
    except BaseException:
        for p in (tmp, wal):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        raise
    return rows


def _write_xml(con: Any, sql: str, out: Path, root: str, row: str, compression: str | None) -> None:
    from _tree import xml_name

    cur = con.execute(sql)  # not con.cursor(): a cursor does not see Arrow data registered on con
    cols = [d[0] for d in cur.description]
    tags = [xml_name(c.replace(".", "_")) for c in cols]
    with _open_compressed(out, compression) as fh:
        _xml_rows(fh, cur, tags, root, row)
        fh.write(b"\n")


def _xml_rows(fh: Any, cur: Any, tags: list[str], root: str, row: str) -> None:
    from lxml import etree

    from _tree import xml_name

    with etree.xmlfile(fh, encoding="utf-8") as xf:
        xf.write_declaration()
        with xf.element(xml_name(root)):
            xf.write("\n")
            while True:
                batch = cur.fetchmany(10_000)
                if not batch:
                    break
                for r in batch:
                    el = etree.Element(xml_name(row))
                    for tag, v in zip(tags, r):
                        if v is None:
                            continue
                        _xml_value(el, tag, v)
                    xf.write(el, pretty_print=True)


def _xml_value(parent: Any, tag: str, v: Any) -> None:
    from lxml import etree

    from _tree import xml_name

    if isinstance(v, dict):
        child = etree.SubElement(parent, tag)
        for k, x in v.items():
            if x is not None:
                _xml_value(child, xml_name(str(k)), x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            if x is not None:
                _xml_value(parent, tag, x)
    else:
        child = etree.SubElement(parent, tag)
        child.text = repr(v) if isinstance(v, float) and v == v else cell(v, None)


def _write_doc(con: Any, sql: str, out: Path, fmt: str, o: dict[str, Any], compression: str | None) -> list[str]:
    from _tree import dump_doc, plain

    notes: list[str] = []
    cur = con.execute(sql)  # not con.cursor(): a cursor does not see Arrow data registered on con
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if fmt == "md":
        from _common import md_table

        text = md_table(cols, [[cell(v, None) for v in r.values()] for r in rows]) + "\n"
    elif fmt == "yaml":
        text = dump_doc([jsonable_keep_dates(r) for r in rows], "yaml")
    elif fmt == "toml":
        key = o.get("table_name") or "rows"
        text = dump_doc({key: [plain(r) for r in rows]}, "toml", nulls="drop")
        if any(v is None for r in rows for v in r.values()):
            notes.append("null values left out (TOML has no null)")
    else:  # ini
        lower = [c.lower() for c in cols]
        sections: dict[str, dict[str, Any]] = {}
        if {"section", "key", "value"} <= set(lower):
            si, ki, vi = lower.index("section"), lower.index("key"), lower.index("value")
            for r in rows:
                vals = list(r.values())
                sections.setdefault(str(vals[si]), {})[str(vals[ki])] = vals[vi]
        else:
            for i, r in enumerate(rows, 1):
                vals = list(r.items())
                name = str(vals[0][1]) if vals and vals[0][1] is not None else f"row{i}"
                if name in sections:
                    name = f"{name}_{i}"
                sections[name] = {k: v for k, v in vals[1:] if v is not None}
            notes.append(f"one section per row, named by column {cols[0]!r}")
        text = dump_doc(sections, "ini")
    with _open_compressed(out, compression) as f:
        f.write(text.encode("utf-8"))
    return notes


def jsonable_keep_dates(r: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in r.items():
        if isinstance(v, (_dt.date, _dt.datetime)):
            out[k] = v
        else:
            out[k] = jsonable(v)
    return out


def _value_labels(vl: dict[str, dict[str, str]], rename: dict[str, str], df: Any, ints_only: bool) -> dict[str, dict[Any, str]]:
    """Value labels read from an SPSS/Stata input (keys as text) back as numbers, for the columns still present."""
    res: dict[str, dict[Any, str]] = {}
    for col, m in vl.items():
        name = rename.get(col, col)
        if name not in df.columns or not m or str(df[name].dtype) in ("object", "string", "str"):
            continue
        conv: dict[Any, str] | None = {}
        for k, v in m.items():
            try:
                num = float(k)
            except ValueError:
                conv = None
                break
            if ints_only and not num.is_integer():
                conv = None
                break
            conv[int(num) if num.is_integer() else num] = str(v)
        if conv:
            res[name] = conv
    return res


def _write_stat(con: Any, sql: str, out: Path, fmt: str, o: dict[str, Any]) -> list[str]:
    from _formats import allow_pandas

    allow_pandas()
    import pyreadstat

    notes: list[str] = []
    sql = flat_select(con, sql)
    rel = con.sql(sql)
    dates = [c for c, t in zip(rel.columns, rel.types) if str(t) == "DATE"]
    df = rel.df()
    for c in dates:
        # pandas holds DATE as datetime64, which pyreadstat writes as a datetime (%tc, DATETIME); date objects
        # become dates (%td, DATE11).
        df[c] = [None if v is None or v != v else (v.date() if hasattr(v, "date") else v) for v in df[c].tolist()]
    limit = {"sav": 64, "zsav": 64, "dta": 32, "xpt": 8}[fmt]
    rename: dict[str, str] = {}
    used: set[str] = set()
    for c in df.columns:
        n = re.sub(r"[^A-Za-z0-9_]", "_", str(c))
        if not n or not (n[0].isalpha() or (n[0] == "_" and fmt != "sav")):
            n = ("v_" if fmt != "xpt" else "V") + n
        n = n[:limit]
        base, k = n, 1
        while n.lower() in used:
            k += 1
            n = f"{base[: limit - len(str(k)) - 1]}_{k}"
        used.add(n.lower())
        if n != c:
            rename[str(c)] = n
    if rename:
        df = df.rename(columns=rename)
        notes.append("renamed for " + fmt + ": " + ", ".join(f"{a} → {b}" for a, b in list(rename.items())[:12]) + (" …" if len(rename) > 12 else ""))
    for c in df.columns:
        if str(df[c].dtype).startswith("datetime64") and getattr(df[c].dt, "tz", None) is not None:
            df[c] = df[c].dt.tz_convert(None)
    labels = o.get("column_labels") or {}
    labels = {rename.get(k, k): v for k, v in labels.items() if rename.get(k, k) in df.columns}
    values = _value_labels(o.get("value_labels") or {}, rename, df, ints_only=fmt == "dta") if fmt != "xpt" else {}
    missing = {rename.get(k, k): v for k, v in (o.get("missing_ranges") or {}).items() if rename.get(k, k) in df.columns}

    def write(vl: dict[str, Any]) -> None:
        if fmt in ("sav", "zsav"):
            pyreadstat.write_sav(df, str(out), compress=fmt == "zsav", column_labels=labels or None, variable_value_labels=vl or None, missing_ranges=missing or None)
        elif fmt == "dta":
            pyreadstat.write_dta(df, str(out), column_labels=labels or None, version=15, variable_value_labels=vl or None)
        else:
            pyreadstat.write_xport(df, str(out), table_name=(o.get("table_name") or "DATA")[:8].upper(), column_labels=labels or None)

    try:
        try:
            write(values)
            if values:
                notes.append(f"value labels kept for {len(values)} variable(s)")
            if missing:
                notes.append(f"user-missing ranges kept for {len(missing)} variable(s)")
        except Exception as e:  # noqa: BLE001
            if not values:
                raise
            write({})
            notes.append(f"value labels left out ({str(e).splitlines()[0][:120]})")
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"cannot write {fmt}: {e}") from None
    return notes
