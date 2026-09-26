"""DuckDB plumbing for the data-files skill: a sandbox-safe connection and loading any supported file as a table.

Every input becomes a table or view named after its file stem (or --as name=path). Small files are read into
memory; big ones go through a cached Parquet copy (catalog/shared/_cache.py), so the second query on a 100 MB CSV
reads 14 MB of Parquet instead of re-parsing text. Formats DuckDB reads natively (CSV, JSON, Parquet) use its
readers; the rest are read in Python (stdlib sqlite3, fastavro, pyreadstat, pyarrow, lxml) and handed over as Arrow.
DuckDB extensions are never downloaded: autoloading is off and the extension folder is a temp dir.
"""

from __future__ import annotations

import atexit
import glob as _glob
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from _common import SkillError, UsageError, human_size
from _formats import big_bytes

CACHE_VERSION = "3"
MB = 1024 * 1024
#: Tables with more rows than this get a map by default instead of a dump.
BIG_ROWS = 5000

_TEMP_DIRS: list[Path] = []


def _cleanup() -> None:
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def temp_dir(prefix: str = "desk-data-") -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(d)
    return d


def sql_str(s: str | os.PathLike[str]) -> str:
    """A single-quoted SQL string literal (paths use forward slashes, which DuckDB accepts on Windows too)."""
    text = str(s)
    if isinstance(s, Path) or os.sep in text:
        text = text.replace("\\", "/") if os.name == "nt" else text
    return "'" + text.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sql_literal(v: Any) -> str:
    """A SQL literal for a --param value (text, number, boolean, null, list or object)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if v == v and v not in (float("inf"), float("-inf")) else f"'{v}'::DOUBLE"
    if isinstance(v, list):
        return "[" + ", ".join(sql_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"'{str(k).replace(chr(39), chr(39) * 2)}': {sql_literal(x)}" for k, x in v.items()) + "}"
    return "'" + str(v).replace("'", "''") + "'"


def bind_params(sql: str, params: dict[str, Any]) -> str:
    """Replaces $name by its value's literal outside strings, quoted names and comments.

    DuckDB's own parameter binding imports pandas (a second or more per call without bytecode caches)."""
    if not params:
        return sql
    import duckdb

    lower = {k.lower(): k for k in params}
    toks = duckdb.tokenize(sql)
    parts: list[str] = []
    last = 0
    for i, (pos, typ) in enumerate(toks):
        if sql[pos : pos + 1] != "$" or "operator" not in str(typ).lower():
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", sql[pos + 1 :])
        if nxt is None or nxt[0] != pos + 1 or not m:
            continue
        name = m.group(0)
        key = name if name in params else lower.get(name.lower())
        if key is None:
            raise UsageError(f"${name} has no value: add --param {name}=VALUE")
        parts += [sql[last:pos], sql_literal(params[key])]
        last = pos + 1 + len(name)
    parts.append(sql[last:])
    return "".join(parts)


def connect(threads: int | None = None) -> Any:
    """An in-memory DuckDB connection that never writes outside temp dirs and never downloads extensions."""
    import duckdb

    work = temp_dir("desk-duck-")
    config = {
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "extension_directory": str(work / "ext"),
        "temp_directory": str(work / "spill"),
    }
    if threads:
        config["threads"] = threads
    config["allow_community_extensions"] = False
    # Several agents share the machine: DuckDB spills to temp files past this instead of taking 80% of the RAM.
    mem = os.environ.get("DESK_DATA_MEMORY", "2GB").strip()
    config["memory_limit"] = mem if re.fullmatch(r"\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)", mem, re.IGNORECASE) else "2GB"
    con = duckdb.connect(":memory:", config=config)
    con.execute("SET enable_progress_bar = false")
    con.execute("SET lock_configuration = true")  # user SQL cannot turn extension downloads or other settings back on
    return con


#: Statements user SQL may not run: they write files (inputs included) or fetch and load extensions.
_WRITE_STATEMENTS = ("COPY", "COPY_DATABASE", "EXPORT", "LOAD", "ATTACH")


def guard_sql(con: Any, sql: str, out_flag: str = "--out") -> None:
    """Refuses COPY … TO, EXPORT DATABASE, INSTALL/LOAD and writable ATTACH in SQL given on the command line."""
    try:
        statements = con.extract_statements(sql)
    except Exception:  # noqa: BLE001 — the query itself reports syntax errors
        return
    for st in statements:
        kind = getattr(st.type, "name", str(st.type))
        if kind not in _WRITE_STATEMENTS:
            continue
        if kind == "ATTACH" and re.search(r"\bREAD_ONLY\b", st.query, re.IGNORECASE):
            continue
        what = {"LOAD": "INSTALL/LOAD (extensions are not downloaded)", "ATTACH": "ATTACH without (READ_ONLY)"}.get(kind, kind.replace("_", " ") + " statements")
        verb = "are" if what.endswith("statements") else "is"
        raise UsageError(f"the SQL here only reads: {what} {verb} refused; write results with {out_flag} FILE (it never replaces an input)")


def duck_error(e: Exception) -> str:
    msg = str(e).strip()
    first = msg.splitlines()[0] if msg else type(e).__name__
    return first[:600]


# ── read options (shared CLI flags) ─────────────────────────────────────


def add_read_args(p: Any) -> None:
    g = p.add_argument_group("reading inputs")
    g.add_argument("--delimiter", "--delim", dest="delimiter", help="CSV field separator (default: sniffed; 'tab' for TSV, 'whitespace' for columns aligned with runs of spaces, 'lines' for one `line` column per line)")
    g.add_argument("--header", choices=["auto", "yes", "no"], default="auto", help="CSV first row is a header (default auto)")
    g.add_argument("--encoding", help="text encoding (default: detected, e.g. utf-8, cp1252, utf-16)")
    g.add_argument("--skip", type=int, default=0, help="CSV lines to skip before the header")
    g.add_argument("--null", action="append", default=[], metavar="TEXT", help="CSV text meaning null, e.g. NA (repeatable)")
    g.add_argument("--all-text", action="store_true", help="read every CSV column as text (no type sniffing)")
    g.add_argument("--path", dest="json_path", help="JSON/YAML/TOML: the record array to use as the table, e.g. data.items (default: the biggest array of objects)")
    g.add_argument("--record", help="XML: the element that is one row, e.g. book or catalog/book (default: the most repeated element)")
    g.add_argument("--table", dest="db_table", help="SQLite/DuckDB file: the table to use when one table is needed")
    g.add_argument("--labels", action="store_true", help="SPSS/Stata/SAS: replace coded values by their value labels")
    g.add_argument("--user-missing", action="store_true", help="SPSS: keep user-missing codes (e.g. -1, 99) as values instead of null")
    g.add_argument("--no-cache", action="store_true", help="do not use or store cached Parquet copies of big inputs")


def read_opts(a: Any) -> dict[str, Any]:
    if getattr(a, "no_cache", False):
        os.environ["DESK_NO_CACHE"] = "1"
    d = getattr(a, "delimiter", None)
    if d:
        d = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";", "pipe": "|", "space": " ", "whitespace": WHITESPACE, "ws": WHITESPACE, "spaces": WHITESPACE, "lines": LINES, "line": LINES, "none": LINES}.get(d.lower(), d)
    return {
        "delimiter": d,
        "header": getattr(a, "header", "auto"),
        "encoding": getattr(a, "encoding", None),
        "skip": getattr(a, "skip", 0) or 0,
        "nulls": list(getattr(a, "null", []) or []),
        "all_text": bool(getattr(a, "all_text", False)),
        "json_path": getattr(a, "json_path", None),
        "record": getattr(a, "record", None),
        "db_table": getattr(a, "db_table", None),
        "labels": bool(getattr(a, "labels", False)),
        "user_missing": bool(getattr(a, "user_missing", False)),
    }


# ── names ───────────────────────────────────────────────────────────────


def sanitize(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip()).strip("_")
    if not s:
        s = "t"
    if s[0].isdigit():
        s = "t_" + s
    return s


def stem_of(path: Path) -> str:
    """The file name without its extension (and compression suffix): v0.7.1.parquet → v0.7.1, data.csv.gz → data."""
    from _formats import COMPRESSION_EXT

    name = path.name
    low = name.lower()
    for ext in COMPRESSION_EXT:
        if low.endswith(ext) and len(name) > len(ext):
            name = name[: -len(ext)]
            break
    dot = name.rfind(".")
    if dot > 0 and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,9}", name[dot + 1 :]):
        name = name[:dot]
    return name or path.name


def glob_name(pattern: str) -> str:
    base = Path(pattern).name
    base = re.sub(r"\.[^.*?\[\]]*$", "", base)  # extension
    base = re.sub(r"[*?\[\]].*$", "", base).rstrip("_-. ") or Path(pattern).parent.name or "files"
    return sanitize(base)


def is_glob(s: str) -> bool:
    return any(c in s for c in "*?[")


# ── inputs ──────────────────────────────────────────────────────────────


@dataclass
class Loaded:
    name: str
    paths: list[Path]
    fmt: str
    kind: str = "table"  # table | view | database
    notes: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    tables: list[str] = field(default_factory=list)  # databases: the table names inside
    keep: list[Any] = field(default_factory=list)  # Arrow tables DuckDB scans (kept alive)
    backing: str = "memory"  # memory | parquet-cache | file | arrow | attached
    seconds: float = 0.0

    @property
    def path(self) -> Path:
        return self.paths[0]


@dataclass
class Spec:
    name: str
    paths: list[Path]
    pattern: str | None = None


def parse_inputs(files: Iterable[str], aliases: Iterable[str] = ()) -> list[Spec]:
    """Files, globs and --as name=path into named specs (names unique, SQL-safe)."""
    specs: list[Spec] = []
    used: set[str] = set()

    def unique(n: str) -> str:
        base, k = n, 2
        while n.lower() in used:
            n = f"{base}_{k}"
            k += 1
        used.add(n.lower())
        return n

    def expand(s: str) -> tuple[list[Path], str | None]:
        if is_glob(s) and not Path(s).exists():
            hits = sorted(Path(p) for p in _glob.glob(os.path.expanduser(s), recursive=True) if Path(p).is_file())
            if not hits:
                raise SkillError(f"no files match {s}")
            return hits, s
        p = Path(s).expanduser()
        if not p.exists():
            raise SkillError(f"{p} does not exist")
        if p.is_dir():
            hits = sorted(x for x in p.iterdir() if x.is_file() and not x.name.startswith("."))
            if not hits:
                raise SkillError(f"{p} is an empty folder")
            return hits, str(p / "*")
        return [p], None

    for a in aliases:
        if "=" not in a:
            raise UsageError(f"--as needs name=path, got {a!r}")
        n, path = a.split("=", 1)
        n = n.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n):
            raise UsageError(f"--as name {n!r} must be a plain SQL identifier (letters, digits, _)")
        paths, pat = expand(path.strip())
        specs.append(Spec(unique(n), paths, pat))
    for f in files:
        paths, pat = expand(f)
        name = glob_name(pat) if pat else sanitize(stem_of(paths[0]))
        specs.append(Spec(unique(name), paths, pat))
    return specs


def mentioned(sql: str, name: str) -> bool:
    return re.search(r'(?<![\w."])' + re.escape(name) + r'(?![\w"])', sql, re.IGNORECASE) is not None or f'"{name}"'.lower() in sql.lower()


# ── the loader ──────────────────────────────────────────────────────────


def load(con: Any, spec: Spec, opts: dict[str, Any]) -> Loaded:
    """Creates a table/view named spec.name for the input (or a schema of tables for a database file)."""
    from _formats import sniff

    t0 = time.time()
    if len(spec.paths) > 1:
        ld = _load_many(con, spec, opts)
    else:
        path = spec.paths[0]
        info = sniff(path)
        fmt = info["format"]
        fn = _LOADERS.get(fmt)
        if fn is None:
            raise SkillError(f"{path.name}: {fmt} is not a table format this skill reads")
        ld = fn(con, spec.name, path, info, opts)
        ld.info.setdefault("sniff", info)
        if info.get("hint"):
            ld.notes.append(info["hint"])
    ld.seconds = round(time.time() - t0, 3)
    return ld


def _load_many(con: Any, spec: Spec, opts: dict[str, Any]) -> Loaded:
    from _formats import sniff

    infos = [sniff(p) for p in spec.paths]
    fmts = {i["format"] for i in infos}
    native = fmts <= {"csv"} or fmts <= {"parquet"} or fmts <= {"json", "jsonl"}
    plain_text = all(i.get("compression") in (None, "gzip", "zstd") and (i.get("encoding") or "utf-8").lower() in ("utf-8", "ascii") and not i.get("bom") for i in infos)
    ld = Loaded(spec.name, spec.paths, infos[0]["format"] if len(fmts) == 1 else "mixed")
    ld.info["files"] = len(spec.paths)
    lst = "[" + ", ".join(sql_str(p) for p in spec.paths) + "]"
    if native and plain_text and not opts.get("json_path") and opts.get("delimiter") not in (WHITESPACE, LINES):
        f = next(iter(fmts))
        if f == "csv":
            args = _csv_args(opts, infos[0], spec.paths[0])
            src = f"read_csv({lst}, union_by_name=true, filename='_file'{args})"
        elif f == "parquet":
            src = f"read_parquet({lst}, union_by_name=true, filename='_file')"
        else:
            from _formats import check_json_depth

            for p, i in zip(spec.paths, infos):
                check_json_depth(p, i.get("compression"))
            src = f"read_json({lst}, union_by_name=true, filename='_file', format='auto', maximum_object_size=268435456)"
        con.execute(f"CREATE VIEW {ident(spec.name)} AS SELECT * FROM {src}")
        ld.kind, ld.backing = "view", "file"
        ld.notes.append(f"{len(spec.paths)} files read as one table; column _file says which file a row came from")
        return ld
    parts = []
    for i, p in enumerate(spec.paths):
        sub = f"__part_{sanitize(spec.name)}_{i}"
        one = load(con, Spec(sub, [p]), opts)
        ld.keep.extend(one.keep)
        parts.append(f"SELECT *, {sql_str(str(p))} AS _file FROM {ident(sub)}")
    con.execute(f"CREATE VIEW {ident(spec.name)} AS " + " UNION ALL BY NAME ".join(parts))
    ld.kind, ld.backing = "view", "memory"
    ld.notes.append(f"{len(spec.paths)} files ({', '.join(sorted(fmts))}) combined by column name; column _file says which file a row came from")
    return ld


# ── CSV ─────────────────────────────────────────────────────────────────


def _csv_args(opts: dict[str, Any], info: dict[str, Any], path: Path, text_sample: str | None = None) -> str:
    from _formats import EXT_DELIM, split_ext

    args = []
    delim = opts.get("delimiter") or EXT_DELIM.get(split_ext(path)[0])
    if delim:
        args.append(f"delim={sql_str(delim)}")
    if opts.get("header") == "yes":
        args.append("header=true")
    elif opts.get("header") == "no":
        args.append("header=false")
    if opts.get("skip"):
        args.append(f"skip={int(opts['skip'])}")
    if opts.get("nulls"):
        args.append("nullstr=[" + ", ".join(sql_str(n) for n in opts["nulls"]) + "]")
    if opts.get("all_text"):
        args.append("all_varchar=true")
    if "quote" in opts:
        args.append(f"quote={sql_str(opts['quote'])}, escape={sql_str(opts['quote'])}")
    if opts.get("names"):
        args.append("names=[" + ", ".join(sql_str(n) for n in opts["names"]) + "]")
    if opts.get("types") and not opts.get("all_text"):
        args.append("types={" + ", ".join(f"{sql_str(k)}: {sql_str(v)}" for k, v in opts["types"].items()) + "}")
    # "" is an empty string, an unquoted empty field is null (DuckDB's default reads both as null).
    args.append("allow_quoted_nulls=false")
    comp = info.get("compression")
    if comp in ("gzip", "zstd"):
        args.append(f"compression={sql_str(comp)}")
    if info.get("decimal_comma"):
        args.append("decimal_separator=','")
    return (", " + ", ".join(args)) if args else ""


def prepare_text(path: Path, info: dict[str, Any], opts: dict[str, Any], workdir: Path) -> tuple[Path, list[str]]:
    """A UTF-8 file DuckDB can read: the input itself, or a transcoded / decompressed copy in workdir."""
    from _formats import is_utf8, open_decompressed

    notes: list[str] = []
    enc = opts.get("encoding") or info.get("encoding") or "utf-8"
    comp = info.get("compression")
    need_transcode = not is_utf8(enc)
    need_decompress = comp in ("bz2", "xz") or (comp and need_transcode)
    if not need_transcode and not need_decompress:
        return path, notes
    out = workdir / "input.txt"
    import codecs

    need = int(info.get("data_size") or info["size"]) * (2 if need_transcode else 1)
    free = shutil.disk_usage(workdir).free
    if need > free - 512 * MB:
        raise SkillError(f"{path.name}: a decoded copy needs about {human_size(need)} of temp space and only {human_size(free)} is free")

    dec = codecs.getincrementaldecoder(enc)(errors="replace") if need_transcode else None
    with open_decompressed(path, comp) as src, open(out, "wb") as dst:
        first = True
        while True:
            chunk = src.read(4 * MB)
            if not chunk:
                break
            if dec is not None:
                text = dec.decode(chunk)
                if first and text.startswith("\ufeff"):
                    text = text[1:]
                dst.write(text.encode("utf-8"))
            else:
                dst.write(chunk)
            first = False
        if dec is not None:
            dst.write(dec.decode(b"", final=True).encode("utf-8"))
    if need_transcode:
        notes.append(f"decoded from {enc}")
    if need_decompress:
        notes.append(f"decompressed ({comp})")
    return out, notes


def _dialect(con: Any, src: Path, compression: str | None, opts: dict[str, Any], orig: Path | None = None) -> dict[str, Any]:
    """DuckDB's sniffer plus our decimal-comma check (src is UTF-8, possibly gzip/zstd)."""
    from _formats import decimal_comma, read_head

    args = _csv_args(opts, {"compression": compression}, orig or src)
    d: dict[str, Any] = {}
    try:
        row = con.execute(f"SELECT Delimiter, Quote, Escape, NewLineDelimiter, SkipRows, HasHeader, DateFormat, TimestampFormat, len(Columns), Columns[1].name, list_distinct([c.type FOR c IN Columns]), [c.name FOR c IN Columns IF c.type = 'BOOLEAN'] FROM sniff_csv({sql_str(src)}{args}, sample_size=20480)").fetchone()
        d = dict(zip(["delimiter", "quote", "escape", "newline", "skip", "header", "date_format", "timestamp_format", "ncols", "first_col", "types", "bool_cols"], row))
        d = {k: ("" if v == "(empty)" else v) for k, v in d.items()}
    except Exception as e:  # noqa: BLE001 — sniffing is best effort
        d = {"sniff_error": duck_error(e)}
    try:
        head = read_head(src, compression, 256 * 1024).decode("utf-8", "replace")
    except OSError:
        head = ""
    if d.get("ncols") == 1 and not opts.get("delimiter"):
        forced = _forced_delimiter(head, int(d.get("skip") or 0))
        if forced:
            # DuckDB found no delimiter because some rows are ragged or a quote is left open: use the one the lines share.
            d["forced_delimiter"], d["header_names"] = forced
        elif d.get("header") and opts.get("header", "auto") == "auto" and len(str(d.get("first_col") or "")) > 30:
            # One column whose "header" is a long line (a log, a list of sentences): every line is data.
            d["header"] = False
            d["lines"] = True
    if d.get("header") and not d.get("lines") and not d.get("forced_delimiter") and opts.get("header", "auto") == "auto" and d.get("types") == ["VARCHAR"]:
        # Every column is text, so DuckDB cannot tell a header from data; a first line shaped like the others
        # (same digits in the same places: timestamps, ids) is data.
        if _first_line_is_data(head, int(d.get("skip") or 0)):
            d["header"] = False
            d["no_header"] = True
    d.pop("ncols", None)
    d.pop("first_col", None)
    d.pop("types", None)
    if not d.get("bool_cols"):
        d.pop("bool_cols", None)
    delim = opts.get("delimiter") or d.get("forced_delimiter") or d.get("delimiter") or ","
    d["decimal_comma"] = decimal_comma(head, delim, d.get("quote") or '"')
    return d


def _shape(line: str) -> str:
    """A line's character-class outline: digits → 9, letters → a, runs collapsed ('[2024-01-01] x 7' → '[9-9-9] a 9')."""
    out = re.sub(r"\d+", "9", line.strip())
    return re.sub(r"[^\W\d_]+", "a", out)


def _first_line_is_data(head: str, skip: int) -> bool:
    from collections import Counter

    lines = [ln for ln in head.splitlines()[skip : skip + 60] if ln.strip()]
    if len(lines) < 3:
        return False
    common, n = Counter(_shape(ln) for ln in lines[1:]).most_common(1)[0]
    return "9" in common and n >= 0.6 * (len(lines) - 1) and _shape(lines[0]) == common


def _forced_delimiter(head: str, skip: int) -> tuple[str, list[str]] | None:
    """(delimiter, header names) when most lines split on the same , ; tab or | into short header-like fields."""
    import csv

    lines = [ln for ln in head.splitlines()[skip : skip + 200] if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2:
        return None
    best: tuple[str, list[str]] | None = None
    for cand in (",", ";", "\t", "|"):
        try:
            names = next(csv.reader([lines[0]], delimiter=cand, quotechar='"'))
        except (csv.Error, StopIteration):
            continue
        names = [n.strip() for n in names]
        if len(names) < 2 or any(len(n) > 40 for n in names) or sum(1 for n in names if n) < len(names) * 0.8:
            continue
        with_delim = sum(1 for ln in lines[1:51] if cand in ln)
        if with_delim < 0.8 * min(50, len(lines) - 1):
            continue
        if best is None or len(names) > len(best[1]):
            best = (cand, names)
    if best is None:
        return None
    seen: dict[str, int] = {}
    names = []
    for i, n in enumerate(best[1]):
        n = n or f"column{i}"
        k = n.lower()
        seen[k] = seen.get(k, 0) + 1
        names.append(n if seen[k] == 1 else f"{n}_{seen[k] - 1}")
    return best[0], names


WHITESPACE = "whitespace"
#: --delimiter lines: no splitting at all, one `line` column (fixed-width files, logs, text lists).
LINES = "lines"


def whitespace_table(text: str) -> bool:
    """True for text laid out in columns separated by runs of spaces (no , ; tab or | anywhere in the header)."""
    lines = [ln for ln in text.splitlines()[:200] if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2 or any(d in lines[0] for d in ",;\t|"):
        return False
    from collections import Counter

    counts = Counter(len(ln.split()) for ln in lines)
    width, n = counts.most_common(1)[0]
    if width < 2 or n < 0.9 * len(lines):
        return False
    aligned = sum(1 for ln in lines if "  " in ln.strip())
    tokens = [t for ln in lines[1:] for t in ln.split()]
    numeric = sum(1 for t in tokens if re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", t))
    return aligned >= 0.8 * len(lines) or (tokens and numeric >= 0.8 * len(tokens))


def _to_tsv(src: Path, compression: str | None, workdir: Path) -> Path:
    """Rewrites whitespace-separated text as tab-separated (streamed)."""
    import io

    from _formats import open_decompressed

    out = workdir / "whitespace.tsv"
    with open_decompressed(src, compression) as raw, open(out, "w", encoding="utf-8", newline="\n") as dst:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""):
            parts = line.split()
            if parts:
                dst.write("\t".join(parts) + "\n")
    return out


def csv_source(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], workdir: Path) -> tuple[str, dict[str, Any], list[str]]:
    """(read_csv(...) SQL, dialect, notes) for a CSV input, transcoding into workdir if needed."""
    from _formats import read_head

    src, notes = prepare_text(path, info, opts, workdir)
    comp = info.get("compression") if src == path else None
    lines = opts.get("delimiter") == LINES
    if lines:
        opts = {**opts, "delimiter": None}
    ws = opts.get("delimiter") == WHITESPACE
    if not ws and not lines and not opts.get("delimiter"):
        try:
            ws = whitespace_table(read_head(src, comp, 64 * 1024).decode("utf-8", "replace"))
        except OSError:
            ws = False
    if ws:
        src, comp = _to_tsv(src, comp, workdir), None
        opts = {**opts, "delimiter": "\t"}
        notes.append("columns separated by runs of spaces (read as whitespace-separated; --delimiter to override)")
    dialect = _dialect(con, src, comp, opts, path)
    if ws:
        dialect["delimiter"] = WHITESPACE
    if lines:
        for k in ("forced_delimiter", "header_names", "no_header", "decimal_comma"):
            dialect.pop(k, None)
        dialect["lines"] = True
    forced = dialect.pop("forced_delimiter", None)
    names = dialect.pop("header_names", None)
    if forced:
        from _formats import delimiter_name

        opts = {**opts, "delimiter": forced, "header": "yes", "quote": '"'}
        dialect.update(delimiter=forced, header=True, quote='"')
        notes.append(f"rows are ragged or a quote is left open, so the delimiter ({delimiter_name(forced)}) was taken from the lines themselves")
        cols = "{" + ", ".join(f"{sql_str(n)}: 'VARCHAR'" for n in names or []) + "}"
        c = f", compression={sql_str(comp)}" if comp in ("gzip", "zstd") else ""
        dialect["_last_resort"] = f"read_csv({sql_str(src)}, auto_detect=false, header=true, delim={sql_str(forced)}, quote='\"', escape='\"', columns={cols}, null_padding=true, strict_mode=false, ignore_errors=true, store_rejects=true{c})"
    if dialect.pop("no_header", False):
        opts = {**opts, "header": "no"}
        notes.append("the first line is shaped like the others (no header): columns are named column0, column1, …; pass --header yes if it is one")
    if dialect.pop("lines", False):
        # A delimiter and quote that never occur, so commas and quotes inside a line stay in it.
        header = "yes" if lines and opts.get("header") == "yes" else "no"
        opts = {**opts, "header": header, "names": ["line"], "all_text": True, "delimiter": "\x1f", "quote": ""}
        dialect.update(delimiter="lines", quote="", escape="", header=header == "yes")
        notes.append(("every line is a row (--delimiter lines)" if lines else "one column of long lines (a log or text list): every line is a row") + ", column `line`; cut fields with substr(line, start, length) or regexp_extract(line, pattern, group)")
    bools = dialect.pop("bool_cols", None)
    if bools and not opts.get("all_text") and not dialect.get("lines"):
        text_cols, note = _text_booleans(con, src, opts, comp, src if ws else path, bools)
        if text_cols:
            opts = {**opts, "types": {**(opts.get("types") or {}), **{c: "VARCHAR" for c in text_cols}}}
        if note:
            notes.append(note)
    args = _csv_args(opts, {"compression": comp, "decimal_comma": dialect.get("decimal_comma")}, src if ws else path)
    return f"read_csv({sql_str(src)}{args})", dialect, notes


def _text_booleans(con: Any, src: Path, opts: dict[str, Any], comp: str | None, orig: Path, cols: list[str]) -> tuple[list[str], str | None]:
    """Columns DuckDB would read as BOOLEAN that hold yes/no, y/n, t/f or 0/1 rather than true/false: those stay text,
    so a CSV → CSV copy never rewrites yes as true. true/false in another case (True, FALSE) stays BOOLEAN, with a note."""
    args = _csv_args({**opts, "all_text": True}, {"compression": comp}, orig)
    sel = ", ".join(f"list_distinct(list({ident(c)}))[1:20]" for c in cols)
    try:
        row = con.execute(f"SELECT {sel} FROM (SELECT * FROM read_csv({sql_str(src)}{args}) LIMIT 20480)").fetchone()
    except Exception:  # noqa: BLE001 — the real read reports problems
        return [], None
    text, recased = [], []
    for c, vals in zip(cols, row):
        seen = {str(v).strip() for v in (vals or []) if v is not None}
        if any(v.lower() not in ("true", "false") for v in seen):
            text.append(c)
        elif any(v not in ("true", "false") for v in seen):
            recased.append(f"{c} ({'/'.join(sorted(seen, key=str.lower, reverse=True))})")
    note = None
    if recased:
        note = f"read as BOOLEAN (text outputs write true/false; --all-text keeps the original text): {', '.join(recased[:8])}"
    return text, note


_FALLBACKS = [
    ("", None),
    (", sample_size=-1", "types sniffed from the whole file"),
    (", sample_size=-1, strict_mode=false, null_padding=true", "lenient parsing (short rows padded with nulls; extra fields kept in extra columns)"),
    (", all_varchar=true, strict_mode=false, null_padding=true", "every column read as text (the file has values that do not fit one type)"),
    (", all_varchar=true, strict_mode=false, null_padding=true, ignore_errors=true, store_rejects=true", "unreadable rows skipped"),
]


def _with_fallbacks(src_sql: str, run: Any, con: Any = None, last_resort: str | None = None) -> str | None:
    """Runs run(read_csv_sql) with progressively more lenient reader options; returns the note of the one that worked.

    `last_resort` is a complete read_csv call (explicit columns, no sniffing) tried when every other one failed.
    """
    base = src_sql[:-1]  # without the closing parenthesis
    first_err: Exception | None = None
    attempts = [(base + extra + ")", note) for extra, note in _FALLBACKS]
    if last_resort:
        attempts.append((last_resort, "malformed quoting: every column read as text and unreadable rows skipped"))
    for sql, note in attempts:
        try:
            run(sql)
        except Exception as e:  # noqa: BLE001
            if "Invalid unicode" in str(e) or "invalid unicode" in str(e):
                raise
            if first_err is None:
                first_err = e
            continue
        if note and "skipped" in note and con is not None:
            note += _rejects(con)
        if note and first_err is not None:
            return f"{note} after: {duck_error(first_err)}"
        return note
    raise SkillError(f"cannot read CSV: {duck_error(first_err) if first_err else 'unknown error'}")


def _rejects(con: Any) -> str:
    """How many lines the CSV reader rejected, and which (from DuckDB's reject_errors table)."""
    try:
        n, lines = con.sql("SELECT count(DISTINCT line), list(DISTINCT line ORDER BY line)[1:10] FROM reject_errors").fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not n:
        return ""
    return f" ({n} line{'s' if n != 1 else ''} rejected, near line {', '.join(str(x) for x in lines)}{' …' if n > 10 else ''})"


def _eu_numbers(con: Any, rel_sql: str) -> list[str]:
    """Text columns holding decimal-comma numbers with thousands dots (1.234,56)."""
    cols = con.sql(f"SELECT * FROM {rel_sql} LIMIT 0")
    cand = [c for c, t in zip(cols.columns, cols.types) if str(t) == "VARCHAR"]
    if not cand:
        return []
    pat = r"^\s*[-+]?\d{1,3}(\.\d{3})*(,\d+)?\s*$"
    aggs = ", ".join(f"count({ident(c)}) FILTER (WHERE NOT regexp_full_match({ident(c)}, '{pat}')), count({ident(c)})" for c in cand)
    row = con.sql(f"SELECT {aggs} FROM {rel_sql}").fetchone()
    return [c for i, c in enumerate(cand) if row[2 * i] == 0 and row[2 * i + 1] > 0]


def _eu_select(cols: list[str]) -> str:
    if not cols:
        return "*"
    rep = ", ".join(f"TRY_CAST(replace(replace(trim({ident(c)}), '.', ''), ',', '.') AS DOUBLE) AS {ident(c)}" for c in cols)
    return f"* REPLACE ({rep})"


def _load_csv(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    ld = Loaded(name, [path], "csv")
    big = info.get("data_size", info["size"]) >= big_bytes("csv")
    if big:
        return _parquet_backed(con, ld, path, info, opts, _build_csv_parquet)
    work = temp_dir()
    src_sql, dialect, notes = csv_source(con, path, info, opts, work)
    ld.notes += notes
    last = dialect.pop("_last_resort", None)
    ld.info["dialect"] = dialect
    try:
        note = _with_fallbacks(src_sql, lambda s: con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT * FROM {s}"), con, last)
    except Exception as e:
        if "nvalid unicode" not in str(e):
            raise
        src_sql, dialect, notes = csv_source(con, path, {**info, "encoding": _redetect(path, info)}, {**opts, "encoding": None}, work)
        ld.notes += notes
        last = dialect.pop("_last_resort", None)
        ld.info["dialect"] = dialect
        note = _with_fallbacks(src_sql, lambda s: con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT * FROM {s}"), con, last)
    if note:
        ld.notes.append(note)
    if dialect.get("decimal_comma"):
        eu = _eu_numbers(con, ident(name))
        if eu:
            con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT {_eu_select(eu)} FROM {ident(name)}")
        ld.notes.append("decimal comma" + (f"; numbers with thousands separators (1.234,56) read in: {', '.join(eu)}" if eu else ""))
    shutil.rmtree(work, ignore_errors=True)
    return ld


def _redetect(path: Path, info: dict[str, Any]) -> str:
    """The encoding of a file that is not valid UTF-8 somewhere past the sniffed head."""
    from charset_normalizer import from_bytes

    from _formats import open_decompressed

    with open_decompressed(path, info.get("compression")) as f:
        data = f.read(16 * MB)
    best = from_bytes(data, cp_exclusion=["utf_8", "ascii"]).best()
    enc = (best.encoding if best else "cp1252").replace("_", "-")
    return "cp1252" if enc in ("latin-1", "iso8859-1") else enc


def _build_csv_parquet(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], out: Path) -> dict[str, Any]:
    work = out.parent / "work"
    work.mkdir(exist_ok=True)
    meta: dict[str, Any] = {"notes": []}
    src_sql, dialect, notes = csv_source(con, path, info, opts, work)

    def copy(s: str) -> None:
        con.execute(f"COPY (SELECT * FROM {s}) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd)")

    try:
        note = _with_fallbacks(src_sql, copy, con, dialect.pop("_last_resort", None))
    except Exception as e:
        if "nvalid unicode" not in str(e):
            raise
        src_sql, dialect, notes2 = csv_source(con, path, {**info, "encoding": _redetect(path, info)}, {**opts, "encoding": None}, work)
        notes += notes2
        note = _with_fallbacks(src_sql, copy, con, dialect.pop("_last_resort", None))
    meta["notes"] = notes + ([note] if note else [])
    if dialect.get("decimal_comma"):
        eu = _eu_numbers(con, f"read_parquet({sql_str(out)})")
        if eu:
            fixed = out.parent / "fixed.parquet"
            con.execute(f"COPY (SELECT {_eu_select(eu)} FROM read_parquet({sql_str(out)})) TO {sql_str(fixed)} (FORMAT parquet, COMPRESSION zstd)")
            os.replace(fixed, out)
        meta["notes"].append("decimal comma" + (f"; numbers with thousands separators (1.234,56) read in: {', '.join(eu)}" if eu else ""))
    meta["dialect"] = dialect
    shutil.rmtree(work, ignore_errors=True)
    return meta


# ── cached Parquet copies ───────────────────────────────────────────────


def _parquet_backed(con: Any, ld: Loaded, path: Path, info: dict[str, Any], opts: dict[str, Any], builder: Any, extra: dict[str, Any] | None = None) -> Loaded:
    """Views the cached Parquet copy of `path` (built by builder(con, path, info, opts, out) on a miss)."""
    import _cache

    params = {k: opts.get(k) for k in ("delimiter", "header", "encoding", "skip", "nulls", "all_text", "json_path", "record", "labels", "user_missing")}
    params["fmt"] = info["format"]
    if extra:
        params.update(extra)
    box: dict[str, Any] = {}

    def build(tmp: Path) -> None:
        t0 = time.time()
        meta = builder(con, path, info, opts, tmp / "data.parquet") or {}
        meta["build_seconds"] = round(time.time() - t0, 2)
        (tmp / "meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")
        box["built"] = True

    hit = _cache.lookup(path, "data-parquet", params, CACHE_VERSION)
    entry = hit or _cache.cached_dir(path, "data-parquet", params, CACHE_VERSION, build)
    if _cache.transient(entry):
        _TEMP_DIRS.append(entry)
    meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    pq = entry / "data.parquet"
    con.execute(f"CREATE OR REPLACE VIEW {ident(ld.name)} AS SELECT * FROM read_parquet({sql_str(pq)})")
    ld.kind = "view"
    ld.backing = "parquet-cache"
    ld.notes += meta.get("notes", [])
    for k in ("dialect", "record_path", "xml_record", "stat_meta", "avro_schema"):
        if k in meta:
            ld.info[k] = meta[k]
    size = pq.stat().st_size
    if hit:
        ld.notes.append(f"read from the cached Parquet copy ({human_size(size)})")
    elif _cache.transient(entry):
        ld.notes.append(f"big input: converted to a temporary Parquet copy in {meta.get('build_seconds')}s (cache disabled)")
    else:
        ld.notes.append(f"big input: cached as Parquet ({human_size(size)}) in {meta.get('build_seconds')}s; later calls reuse it")
    ld.info["parquet"] = str(pq)
    return ld


# ── JSON / JSONL ────────────────────────────────────────────────────────


def _json_top(path: Path, info: dict[str, Any]) -> str:
    from _formats import decode_head, read_head

    head = read_head(path, info.get("compression"), 4096)
    s = decode_head(head, info.get("encoding") or "utf-8").lstrip()
    return s[:1]


def _load_json(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    from _formats import is_utf8

    fmt = info["format"]
    ld = Loaded(name, [path], fmt)
    top = "[" if fmt == "jsonl" else _json_top(path, info)
    jp = opts.get("json_path")
    big = info.get("data_size", info["size"]) >= big_bytes("json")
    native = is_utf8(info.get("encoding")) and info.get("compression") in (None, "gzip", "zstd") and not jp and (fmt == "jsonl" or top == "[")
    if big:
        return _parquet_backed(con, ld, path, info, opts, _build_json_parquet)
    if native:
        src_path, src_info = _bom_free(path, info, temp_dir())
        src = _json_reader(src_path, fmt, src_info)
        try:
            note = _json_fallbacks(con, src, lambda s: con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT * FROM {s}"))
        except SkillError:
            if fmt != "json":
                raise
            note = None  # JSON with comments or trailing commas (JSONC): the Python reader below takes it
        else:
            if note:
                ld.notes.append(note)
            return ld
    from _tree import load_doc, records_at

    doc = load_doc(path, "json" if fmt == "json" else fmt, opts.get("encoding"))
    records, where = records_at(doc, jp)
    ld.info["record_path"] = where
    if where != "$" or jp:
        ld.notes.append(f"rows are the {len(records)} items at {where}")
    _records_table(con, name, records, temp_dir())
    return ld


def _json_reader(path: Path, fmt: str, info: dict[str, Any]) -> str:
    """read_json(...) for JSONL or a top-level JSON array (the only JSON the native reader gets)."""
    from _formats import check_json_depth

    comp = info.get("compression")
    check_json_depth(path, comp)
    c = f", compression={sql_str(comp)}" if comp in ("gzip", "zstd") else ""
    form = "newline_delimited" if fmt == "jsonl" else "array"
    return f"read_json({sql_str(path)}, format={sql_str(form)}, maximum_object_size=268435456{c})"


def _bom_free(path: Path, info: dict[str, Any], workdir: Path) -> tuple[Path, dict[str, Any]]:
    """(path, info) DuckDB's JSON reader accepts: it refuses a byte order mark, so a file with one is copied
    (streamed, decompressed) into workdir without it."""
    if not info.get("bom"):
        return path, info
    import codecs

    from _formats import open_decompressed

    out = workdir / "no-bom.json"
    with open_decompressed(path, info.get("compression")) as src, open(out, "wb") as dst:
        head = src.read(len(codecs.BOM_UTF8))
        if head != codecs.BOM_UTF8:
            dst.write(head)
        shutil.copyfileobj(src, dst, 4 * MB)
    return out, {**info, "compression": None, "bom": False}


def _json_fallbacks(con: Any, src: str, run: Any) -> str | None:
    base = src[:-1]
    first: Exception | None = None
    for extra, note in (("", None), (", sample_size=-1", "types sniffed from every record"), (", sample_size=-1, union_by_name=true, ignore_errors=true", "records that did not fit were skipped")):
        try:
            run(base + extra + ")")
            return f"{note} after: {duck_error(first)}" if note and first else note
        except Exception as e:  # noqa: BLE001
            first = first or e
    # Last resort: one JSON column per record.
    try:
        run(base.replace("read_json(", "read_json_objects(", 1).split(", maximum_object_size")[0] + ")")
        return f"records kept as raw JSON in column 'json' after: {duck_error(first)}"
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"cannot read JSON: {duck_error(first or e)}") from None


def _records_table(con: Any, name: str, records: list[Any], work: Path, target: Path | None = None) -> None:
    """Loads Python records (dicts, or scalars) through an NDJSON temp file, so DuckDB infers types and nesting."""
    from _tree import plain

    nd = work / f"{sanitize(name)}.ndjson"
    with open(nd, "w", encoding="utf-8") as f:
        for r in records:
            if not isinstance(r, dict):
                r = {"value": r}
            f.write(json.dumps(plain(r), ensure_ascii=False, default=str))
            f.write("\n")
    if not records:
        con.execute(f"CREATE OR REPLACE TABLE {ident(name)} (value VARCHAR)")
        return
    src = f"read_json({sql_str(nd)}, format='newline_delimited', maximum_object_size=268435456)"
    if target is not None:
        _json_fallbacks(con, src, lambda s: con.execute(f"COPY (SELECT * FROM {s}) TO {sql_str(target)} (FORMAT parquet, COMPRESSION zstd)"))
    else:
        _json_fallbacks(con, src, lambda s: con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT * FROM {s}"))
    nd.unlink(missing_ok=True)


def _build_json_parquet(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], out: Path) -> dict[str, Any]:
    from _formats import is_utf8

    fmt = info["format"]
    meta: dict[str, Any] = {"notes": []}
    jp = opts.get("json_path")
    top = "[" if fmt == "jsonl" else _json_top(path, info)
    if is_utf8(info.get("encoding")) and info.get("compression") in (None, "gzip", "zstd") and not jp and (fmt == "jsonl" or top == "["):
        src_path, src_info = _bom_free(path, info, out.parent)
        src = _json_reader(src_path, fmt, src_info)
        note = _json_fallbacks(con, src, lambda s: con.execute(f"COPY (SELECT * FROM {s}) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd)"))
        if src_path != path:
            src_path.unlink(missing_ok=True)
        if note:
            meta["notes"].append(note)
        return meta
    # A document with the records somewhere inside (or an odd encoding): stream the records with ijson.
    from _tree import parse_path, stream_record_path, stream_records, fmt_pattern

    if jp:
        steps = parse_path(jp)
        where = fmt_pattern(steps)
    else:
        steps, where = stream_record_path(path, info.get("compression"))
    nd = out.parent / "records.ndjson"
    n = 0
    encode = json.JSONEncoder(ensure_ascii=False, default=str, separators=(",", ":")).encode  # built once: 2x faster than dumps
    batch: list[str] = []
    with open(nd, "w", encoding="utf-8") as f:
        for rec in stream_records(path, steps, info.get("compression")):
            batch.append(encode(rec if isinstance(rec, dict) else {"value": rec}))
            if len(batch) >= 10_000:
                f.write("\n".join(batch))
                f.write("\n")
                n += len(batch)
                batch = []
        if batch:
            f.write("\n".join(batch))
            f.write("\n")
            n += len(batch)
    meta["record_path"] = where
    meta["notes"].append(f"rows are the {n} items at {where} (streamed)")
    if n == 0:
        raise SkillError(f"no records at {where}; pick the array with --path (see data_tree.py outline)")
    src = f"read_json({sql_str(nd)}, format='newline_delimited', maximum_object_size=268435456)"
    _json_fallbacks(con, src, lambda s: con.execute(f"COPY (SELECT * FROM {s}) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd)"))
    nd.unlink(missing_ok=True)
    return meta


# ── Parquet / Arrow ─────────────────────────────────────────────────────


def _load_parquet(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    con.execute(f"CREATE OR REPLACE VIEW {ident(name)} AS SELECT * FROM read_parquet({sql_str(path)})")
    return Loaded(name, [path], "parquet", kind="view", backing="file")


def read_arrow(path: Path) -> Any:
    import pyarrow as pa
    import pyarrow.ipc as ipc

    try:
        source = pa.memory_map(str(path), "r")
        try:
            return ipc.open_file(source).read_all()
        except pa.ArrowInvalid:
            source.seek(0)
            try:
                return ipc.open_stream(source).read_all()
            except pa.ArrowInvalid:
                import pyarrow.feather as feather

                return feather.read_table(str(path))
    except (pa.ArrowInvalid, OSError) as e:
        raise SkillError(f"{path.name}: not a readable Arrow/Feather file: {e}") from None


def _load_arrow(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    table = read_arrow(path)
    reg = f"__arrow_{sanitize(name)}"
    con.register(reg, table)
    con.execute(f"CREATE OR REPLACE VIEW {ident(name)} AS SELECT * FROM {ident(reg)}")
    return Loaded(name, [path], "arrow", kind="view", keep=[table], backing="arrow")


def _register_arrow(con: Any, name: str, table: Any, ld: Loaded) -> None:
    reg = f"__arrow_{sanitize(name)}"
    con.register(reg, table)
    con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT * FROM {ident(reg)}")
    con.unregister(reg)


# ── Avro ────────────────────────────────────────────────────────────────


def avro_to_arrow_type(t: Any, named: dict[str, Any] | None = None) -> Any:
    """Maps an Avro schema to an Arrow type (nullable unions → the inner type; other unions → JSON text)."""
    import pyarrow as pa

    named = named if named is not None else {}
    if isinstance(t, list):
        inner = [x for x in t if x != "null"]
        if len(inner) == 1:
            return avro_to_arrow_type(inner[0], named)
        return pa.string()
    if isinstance(t, str):
        prim = {"null": pa.null(), "boolean": pa.bool_(), "int": pa.int32(), "long": pa.int64(), "float": pa.float32(), "double": pa.float64(), "bytes": pa.binary(), "string": pa.string()}
        if t in prim:
            return prim[t]
        if t in named:
            return named[t]
        return pa.string()
    if isinstance(t, dict):
        lt = t.get("logicalType")
        base = t.get("type")
        if lt == "date":
            return pa.date32()
        if lt == "timestamp-millis":
            return pa.timestamp("ms", tz="UTC")
        if lt == "timestamp-micros":
            return pa.timestamp("us", tz="UTC")
        if lt in ("local-timestamp-millis", "local-timestamp-micros"):
            return pa.timestamp("ms" if lt.endswith("millis") else "us")
        if lt == "time-millis":
            return pa.time32("ms")
        if lt == "time-micros":
            return pa.time64("us")
        if lt == "decimal":
            return pa.decimal128(int(t.get("precision", 38)), int(t.get("scale", 0)))
        if lt == "uuid":
            return pa.string()
        if base == "record":
            fields = [pa.field(f["name"], avro_to_arrow_type(f["type"], named)) for f in t.get("fields", [])]
            st = pa.struct(fields) if fields else pa.string()
            named[t.get("name", "")] = st
            if t.get("namespace"):
                named[f"{t['namespace']}.{t.get('name')}"] = st
            return st
        if base == "enum":
            named[t.get("name", "")] = pa.string()
            return pa.string()
        if base == "array":
            return pa.list_(avro_to_arrow_type(t.get("items"), named))
        if base == "map":
            return pa.map_(pa.string(), avro_to_arrow_type(t.get("values"), named))
        if base == "fixed":
            named[t.get("name", "")] = pa.binary()
            return pa.binary()
        return avro_to_arrow_type(base, named)
    return pa.string()


def _avro_fix(value: Any, arrow_type: Any) -> Any:
    import pyarrow as pa

    if value is None:
        return None
    if pa.types.is_string(arrow_type) and not isinstance(value, str):
        return json.dumps(value, default=str, ensure_ascii=False)
    if pa.types.is_map(arrow_type) and isinstance(value, dict):
        return list(value.items())
    return value


def _avro_batches(path: Path, batch: int = 65536) -> Any:
    import fastavro
    import pyarrow as pa

    from _formats import avro_codecs

    avro_codecs()

    with open(path, "rb") as f:
        reader = fastavro.reader(f)
        schema = reader.writer_schema
        arrow_type = avro_to_arrow_type(schema)
        if pa.types.is_struct(arrow_type):
            arrow_schema = pa.schema(list(arrow_type))
        else:
            arrow_schema = pa.schema([pa.field("value", arrow_type)])
        yield arrow_schema, schema
        rows: list[dict[str, Any]] = []
        needs_fix = [f.name for f in arrow_schema if pa.types.is_string(f.type) or pa.types.is_map(f.type)]
        for rec in reader:
            if not isinstance(rec, dict) or not pa.types.is_struct(arrow_type):
                rec = {"value": rec}
            if needs_fix:
                for k in needs_fix:
                    if k in rec:
                        rec[k] = _avro_fix(rec[k], arrow_schema.field(k).type)
            rows.append(rec)
            if len(rows) >= batch:
                yield pa.Table.from_pylist(rows, schema=arrow_schema)
                rows = []
        if rows:
            yield pa.Table.from_pylist(rows, schema=arrow_schema)


def _load_avro(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    ld = Loaded(name, [path], "avro")
    if info["size"] >= big_bytes("py"):
        return _parquet_backed(con, ld, path, info, opts, _build_avro_parquet)
    import pyarrow as pa

    gen = _avro_batches(path)
    arrow_schema, avro_schema = next(gen)
    parts = list(gen)
    table = pa.concat_tables(parts) if parts else arrow_schema.empty_table()
    ld.info["avro_schema"] = avro_schema
    _register_arrow(con, name, table, ld)
    return ld


def _build_avro_parquet(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], out: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    gen = _avro_batches(path)
    arrow_schema, avro_schema = next(gen)
    with pq.ParquetWriter(str(out), arrow_schema, compression="zstd") as w:
        n = 0
        for t in gen:
            w.write_table(t)
            n += t.num_rows
        if n == 0:
            w.write_table(arrow_schema.empty_table())
    return {"notes": [], "avro_schema": avro_schema}


# ── SPSS / Stata / SAS (pyreadstat) ─────────────────────────────────────


def _stat_reader(fmt: str) -> Any:
    import pyreadstat

    return {"sav": pyreadstat.read_sav, "por": pyreadstat.read_por, "dta": pyreadstat.read_dta, "sas7bdat": pyreadstat.read_sas7bdat, "xpt": pyreadstat.read_xport}[fmt]


def stat_meta(path: Path, fmt: str, encoding: str | None = None) -> dict[str, Any]:
    reader = _stat_reader(fmt)
    kw: dict[str, Any] = {"encoding": encoding} if encoding and fmt != "por" else {}
    if fmt in ("sav", "dta", "sas7bdat"):
        kw["user_missing"] = True  # only then does pyreadstat report the user-missing ranges / codes
    try:
        _, m = reader(str(path), metadataonly=True, output_format="dict", **kw)
    except Exception as e:  # noqa: BLE001 — pyreadstat raises its own error types
        raise SkillError(f"{path.name}: cannot read {fmt}: {_first_line(e)}") from None
    labels = dict(zip(m.column_names, m.column_labels or [None] * len(m.column_names)))
    return {
        "rows": m.number_rows,
        "columns": m.number_columns,
        "file_label": getattr(m, "file_label", None),
        "file_encoding": getattr(m, "file_encoding", None),
        "table_name": getattr(m, "table_name", None),
        "created": str(getattr(m, "creation_time", "") or "") or None,
        "modified": str(getattr(m, "modification_time", "") or "") or None,
        "column_labels": {k: v for k, v in labels.items() if v},
        "formats": dict(getattr(m, "original_variable_types", {}) or {}),
        "value_labels": {k: {_label_key(kk): vv for kk, vv in v.items()} for k, v in (getattr(m, "variable_value_labels", {}) or {}).items()},
        "missing_ranges": {k: [_range_text(r) for r in v] for k, v in (getattr(m, "missing_ranges", {}) or {}).items() if v},
        "missing_raw": {k: [dict(r) if isinstance(r, dict) else r for r in v] for k, v in (getattr(m, "missing_ranges", {}) or {}).items() if v},
        "missing_user_values": {k: [str(x) for x in v] for k, v in (getattr(m, "missing_user_values", {}) or {}).items() if v},
        "measure": {k: v for k, v in (getattr(m, "variable_measure", {}) or {}).items() if v and v != "unknown"},
    }


def _range_text(r: Any) -> str:
    """An SPSS missing range as text: -1, or 2000 to 3000."""
    if isinstance(r, dict):
        lo, hi = r.get("lo"), r.get("hi")
        lo_t, hi_t = _label_key(lo), _label_key(hi)
        return lo_t if lo_t == hi_t else f"{lo_t} to {hi_t}"
    return _label_key(r)


def missing_note(sm: dict[str, Any], kept: bool) -> str | None:
    """The note for SPSS user-missing codes (read as null unless --user-missing)."""
    mr = sm.get("missing_ranges") or {}
    if not mr:
        return None
    items = "; ".join(f"{k} ({', '.join(v)})" for k, v in list(mr.items())[:8]) + (" …" if len(mr) > 8 else "")
    if kept:
        return f"SPSS user-missing codes kept as values (--user-missing): {items}"
    return f"SPSS user-missing codes read as null: {items}; --user-missing keeps the coded values"


def _first_line(e: Exception) -> str:
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
    return lines[0] if lines else type(e).__name__


def _label_key(k: Any) -> str:
    if isinstance(k, float) and k.is_integer():
        return str(int(k))
    return str(k)


def _stat_table(data: dict[str, Any], meta: Any, labels: bool) -> Any:
    """An Arrow table from pyreadstat's dict output (numpy arrays; NaN means missing), value labels applied on request."""
    import pyarrow as pa

    value_labels = (getattr(meta, "variable_value_labels", {}) or {}) if labels else {}
    storage = getattr(meta, "readstat_variable_types", {}) or {}
    names = list(data)
    arrays = []
    for name in names:
        col = data[name]
        if storage.get(name) == "float" and not value_labels.get(name):
            # A 4-byte float (Stata `float`): 3.58 arrives as 3.5799999237060547; keep the decimal it was.
            try:
                import numpy as np

                arrays.append(pa.array(np.asarray(col, dtype=np.float32), from_pandas=True).cast(pa.string()).cast(pa.float64()))
                continue
            except (TypeError, ValueError, pa.ArrowInvalid):
                pass
        vl = value_labels.get(name)
        if vl:
            out = []
            for v in (col.tolist() if hasattr(col, "tolist") else list(col)):
                if v is None or (isinstance(v, float) and v != v):
                    out.append(None)
                else:
                    lab = vl.get(v)
                    if lab is None and isinstance(v, float) and v.is_integer():
                        lab = vl.get(int(v))
                    out.append(str(lab if lab is not None else (int(v) if isinstance(v, float) and v.is_integer() else v)))
            arrays.append(pa.array(out, type=pa.string()))
            continue
        try:
            arr = pa.array(col, from_pandas=True)
            if pa.types.is_float64(arr.type) and _float32_noise(col):
                arr = arr.cast(pa.float32()).cast(pa.string()).cast(pa.float64())
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
            vals = col.tolist() if hasattr(col, "tolist") else list(col)
            arr = pa.array([None if (v is None or (isinstance(v, float) and v != v)) else str(v) for v in vals], type=pa.string())
        arrays.append(arr)
    return pa.Table.from_arrays(arrays, names=names) if names else pa.table({})


def _float32_noise(col: Any) -> bool:
    """True for doubles that are all 4-byte floats written as 8 bytes (3.58 stored as 3.5799999237060547, as
    exports from float32 data do): each value survives a float32 round trip, and some print with float noise."""
    try:
        import numpy as np

        a = np.asarray(col, dtype=np.float64)
        a = a[~np.isnan(a)]
        if not a.size:
            return False
        if not np.array_equal(a.astype(np.float32).astype(np.float64), a):
            return False
        sample = a[: 2000]
        return any(len(repr(float(v))) >= 16 and len(str(np.float32(v))) <= 10 for v in sample)
    except (TypeError, ValueError):
        return False


def _stat_frames(path: Path, fmt: str, opts: dict[str, Any], chunk: int | None = None) -> Any:
    import pyreadstat

    reader = _stat_reader(fmt)
    kw: dict[str, Any] = {"output_format": "dict"}
    if opts.get("encoding") and fmt != "por":
        kw["encoding"] = opts["encoding"]
    if opts.get("user_missing") and fmt == "sav":
        kw["user_missing"] = True
    try:
        if chunk:
            it = pyreadstat.read_file_in_chunks(reader, str(path), chunksize=chunk, **kw)
        else:
            it = [reader(str(path), **kw)]
        for data, meta in it:
            yield _stat_table(data, meta, bool(opts.get("labels")))
    except SkillError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"{path.name}: cannot read {fmt}: {_first_line(e)}") from None


def conform(t: Any, schema: Any) -> Any:
    """Casts a chunk to the schema of the first chunk (a column that is all missing in one chunk comes back typed
    differently); values that cannot be cast become text-typed nulls only when the column is entirely null."""
    import pyarrow as pa

    cols = []
    for f in schema:
        if f.name not in t.column_names:
            cols.append(pa.nulls(t.num_rows, type=f.type))
            continue
        c = t.column(f.name)
        if c.type == f.type:
            cols.append(c)
        elif c.null_count == len(c):
            cols.append(pa.nulls(len(c), type=f.type))
        else:
            try:
                cols.append(c.cast(f.type, safe=False))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
                raise SkillError(f"column {f.name} changes type between parts of the file ({f.type} then {c.type}): {e}") from None
    return pa.Table.from_arrays(cols, schema=schema)


def _integral_select(con: Any, src: str) -> str:
    """SELECT list turning DOUBLE columns that only hold whole numbers into BIGINT (SPSS/Stata/SAS store all numbers as doubles)."""
    rel = con.sql(f"SELECT * FROM {src} LIMIT 0")
    cols = [c for c, t in zip(rel.columns, rel.types) if str(t) == "DOUBLE"]
    if not cols:
        return "*"
    aggs = ", ".join(f"count({ident(c)}) FILTER (WHERE {ident(c)} != trunc({ident(c)}) OR abs({ident(c)}) > 9007199254740992 OR isnan({ident(c)}))" for c in cols)
    row = con.sql(f"SELECT {aggs} FROM {src}").fetchone()
    reps = [f"CAST({ident(c)} AS BIGINT) AS {ident(c)}" for c, bad in zip(cols, row) if bad == 0]
    return f"* REPLACE ({', '.join(reps)})" if reps else "*"


def _load_stat(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    fmt = info["format"]
    ld = Loaded(name, [path], fmt)
    meta = stat_meta(path, fmt, opts.get("encoding"))
    # .zsav and compressed .sas7bdat can hold far more than their size: judge by rows × columns (8 bytes a cell).
    cells = (meta.get("rows") or 0) * (meta.get("columns") or 0)
    from _formats import max_inflate_bytes

    if cells * 8 > max_inflate_bytes():
        raise SkillError(f"{path.name} declares {meta.get('rows'):,} rows × {meta.get('columns'):,} columns, over {human_size(max_inflate_bytes())} in memory (DESK_DATA_MAX_INFLATE_MB): refusing a possible decompression bomb")
    if info["size"] >= big_bytes("py") or cells * 8 >= 256 * MB:
        ld = _parquet_backed(con, ld, path, info, opts, _build_stat_parquet)
    else:
        import pyarrow as pa

        tables = list(_stat_frames(path, fmt, opts))
        _register_arrow(con, name, pa.concat_tables(tables) if len(tables) > 1 else tables[0], ld)
        sel = _integral_select(con, ident(name))
        if sel != "*":
            con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT {sel} FROM {ident(name)}")
    ld.info["stat_meta"] = meta
    if opts.get("labels"):
        ld.notes.append("coded values replaced by their value labels")
    note = missing_note(ld.info["stat_meta"], bool(opts.get("user_missing")) and fmt == "sav")
    if note:
        ld.notes.append(note)
    return ld


def _build_stat_parquet(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], out: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    writer = None
    try:
        for t in _stat_frames(path, info["format"], opts, chunk=200_000):
            if writer is None:
                writer = pq.ParquetWriter(str(out), t.schema, compression="zstd")
            elif t.schema != writer.schema:
                t = conform(t, writer.schema)
            writer.write_table(t)
    finally:
        if writer is not None:
            writer.close()
    sel = _integral_select(con, f"read_parquet({sql_str(out)})")
    if sel != "*":
        fixed = out.parent / "fixed.parquet"
        con.execute(f"COPY (SELECT {sel} FROM read_parquet({sql_str(out)})) TO {sql_str(fixed)} (FORMAT parquet, COMPRESSION zstd)")
        os.replace(fixed, out)
    return {"notes": []}


# ── SQLite and DuckDB files ─────────────────────────────────────────────


def sqlite_connect(path: Path) -> Any:
    import sqlite3

    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.DatabaseError as e:
        raise SkillError(f"{path.name}: cannot open as SQLite: {e}") from None


def sqlite_catalog(path: Path) -> list[dict[str, Any]]:
    con = sqlite_connect(path)
    try:
        out = []
        for name, typ, tbl, sql in con.execute("SELECT name, type, tbl_name, sql FROM sqlite_master WHERE type IN ('table','view','index','trigger') AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 WHEN 'index' THEN 2 ELSE 3 END, name"):
            item: dict[str, Any] = {"name": name, "type": typ, "table": tbl, "sql": sql}
            if typ in ("table", "view"):
                cols = con.execute(f"PRAGMA table_xinfo({ident(name)})").fetchall()
                item["columns"] = [{"name": c[1], "type": c[2] or "", "notnull": bool(c[3]), "pk": int(c[5]), "hidden": int(c[6]) if len(c) > 6 else 0} for c in cols]
                try:
                    item["rows"] = con.execute(f"SELECT count(*) FROM {ident(name)}").fetchone()[0]
                except Exception as e:  # noqa: BLE001 — broken views
                    item["rows"] = None
                    item["error"] = str(e)
                if typ == "table":
                    fks = con.execute(f"PRAGMA foreign_key_list({ident(name)})").fetchall()
                    if fks:
                        item["foreign_keys"] = [{"from": f[3], "table": f[2], "to": f[4]} for f in fks]
            out.append(item)
        return out
    finally:
        con.close()


def _sqlite_arrow(path: Path, table: str, batch: int = 100_000) -> Any:
    """Yields Arrow tables for a SQLite table, typed from the declared column types (text when values disagree)."""
    import pyarrow as pa

    con = sqlite_connect(path)
    try:
        decl = {c[1]: (c[2] or "").upper() for c in con.execute(f"PRAGMA table_xinfo({ident(table)})").fetchall()}
        special = _sqlite_special_types(con, table, decl)
        cur = con.execute(f"SELECT * FROM {ident(table)}")
        names = [d[0] for d in cur.description]
        types: list[Any] = []
        for n in names:
            t = decl.get(n, "")
            if n in special:
                types.append(special[n][0])
            elif "INT" in t:
                types.append(pa.int64())
            elif any(x in t for x in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
                types.append(pa.float64())
            elif "BLOB" in t:
                types.append(pa.binary())
            elif "BOOL" in t:
                types.append(pa.bool_())
            elif "CHAR" in t or "TEXT" in t or "CLOB" in t or "DATE" in t or "TIME" in t:
                types.append(pa.string())
            else:
                types.append(None)
        final: list[Any] = [None] * len(names)
        first = True
        while True:
            rows = cur.fetchmany(batch)
            if not rows and not first:
                break
            first = False
            cols = list(zip(*rows)) if rows else [()] * len(names)
            arrays = []
            for i, col in enumerate(cols):
                want = final[i] or types[i]
                sp = special.get(names[i])
                arrays.append(_to_array(sp[1](col) if sp else list(col), want))
                if final[i] is None:
                    final[i] = arrays[-1].type if len(col) else types[i] or pa.string()
                elif arrays[-1].type != final[i]:
                    arrays[-1] = _to_array(list(col), final[i])
            yield pa.Table.from_arrays(arrays, names=_unique_names(names))
            if not rows:
                break
    finally:
        con.close()


def _sqlite_special_types(con: Any, table: str, decl: dict[str, str]) -> dict[str, tuple[Any, Any]]:
    """Columns declared DECIMAL/NUMERIC(p,s), DATE or DATETIME/TIMESTAMP whose every value fits that type (checked
    in SQLite first, so no batch can fail later): {name: (arrow type, converter for a batch of values)}."""
    import datetime as dt
    from decimal import Decimal

    import pyarrow as pa

    out: dict[str, tuple[Any, Any]] = {}
    for name, t in decl.items():
        q = ident(name)
        m = re.fullmatch(r"\s*(?:NUMERIC|DECIMAL)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*", t)
        try:
            if m and 0 < int(m.group(2)) <= int(m.group(1)) <= 38:
                p, s = int(m.group(1)), int(m.group(2))
                bad = con.execute(f"SELECT count(*) FROM {ident(table)} WHERE {q} IS NOT NULL AND (typeof({q}) NOT IN ('integer', 'real') OR round({q}, {s}) != {q} OR abs({q}) >= {10 ** (p - s)})").fetchone()[0]
                if not bad:
                    unit = Decimal(1).scaleb(-s)
                    out[name] = (pa.decimal128(p, s), lambda col, unit=unit: [None if v is None else Decimal(repr(v) if isinstance(v, float) else v).quantize(unit) for v in col])
            elif re.search(r"DATETIME|TIMESTAMP", t) or re.fullmatch(r"\s*DATE\s*", t):
                is_date = "TIME" not in t
                shape = "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]"
                cond = f"length({q}) = 10 AND {q} GLOB '{shape}' AND date({q}) IS NOT NULL" if is_date else f"{q} GLOB '{shape}*' AND datetime({q}) IS NOT NULL AND substr({q}, 11) NOT GLOB '*[-+Zz]*'"
                row = con.execute(f"SELECT count(*) FILTER (WHERE {q} IS NOT NULL AND (typeof({q}) != 'text' OR NOT ({cond}))), count({q}) FROM {ident(table)}").fetchone()
                if row[1] and not row[0]:
                    if is_date:
                        out[name] = (pa.date32(), lambda col: [None if v is None else dt.date.fromisoformat(v) for v in col])
                    else:
                        out[name] = (pa.timestamp("us"), lambda col: [None if v is None else dt.datetime.fromisoformat(v) for v in col])
        except Exception:  # noqa: BLE001 — odd declarations stay with the generic mapping
            continue
    return out


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        k = n.lower()
        if k in seen:
            seen[k] += 1
            n = f"{n}_{seen[k]}"
        else:
            seen[k] = 1
        out.append(n)
    return out


def _to_array(values: list[Any], want: Any) -> Any:
    import pyarrow as pa

    if want is not None:
        try:
            if pa.types.is_integer(want) and any(isinstance(v, float) for v in values):
                raise pa.ArrowInvalid("floats in an integer column")
            if pa.types.is_boolean(want):
                values = [None if v is None else bool(v) for v in values]
            return pa.array(values, type=want)
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError):
            if pa.types.is_integer(want):
                try:
                    return pa.array(values, type=pa.float64())
                except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
                    pass
    else:
        try:
            return pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError):
            pass
    return pa.array([None if v is None else (v.hex() if isinstance(v, bytes) else str(v)) for v in values], type=pa.string())


def _load_sqlite(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any], only: set[str] | None = None) -> Loaded:
    """Each table and view of the database becomes a table in schema `name` (and in main when unambiguous)."""
    import pyarrow as pa

    ld = Loaded(name, [path], "sqlite", kind="database")
    cat = [c for c in sqlite_catalog(path) if c["type"] in ("table", "view")]
    ld.info["catalog"] = cat
    ld.tables = [c["name"] for c in cat]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {ident(name)}")
    big = info["size"] >= big_bytes("sqlite")
    for c in cat:
        t = c["name"]
        if only is not None and t.lower() not in only and f"{name}.{t}".lower() not in only:
            continue
        target = f"{ident(name)}.{ident(t)}"
        if big:
            sub = Loaded(t, [path], "sqlite")

            def builder(con_: Any, p: Path, i: dict[str, Any], o: dict[str, Any], out: Path, _t: str = t) -> dict[str, Any]:
                import pyarrow.parquet as pq

                w = None
                for tb in _sqlite_arrow(p, _t):
                    if w is None:
                        w = pq.ParquetWriter(str(out), tb.schema, compression="zstd")
                    w.write_table(tb.cast(w.schema) if tb.schema != w.schema else tb)
                if w is not None:
                    w.close()
                return {"notes": []}

            sub.name = f"__sq_{sanitize(name)}_{sanitize(t)}"
            _parquet_backed(con, sub, path, info, {**opts, "record": t}, builder, {"table": t})
            con.execute(f"CREATE OR REPLACE VIEW {target} AS SELECT * FROM {ident(sub.name)}")
            ld.backing = "parquet-cache"
            for n in sub.notes:
                if n not in ld.notes and "cached" in n:
                    ld.notes.append(f"{t}: {n}")
        else:
            parts = list(_sqlite_arrow(path, t))
            table = pa.concat_tables(parts) if len(parts) > 1 else parts[0]
            reg = f"__sq_{sanitize(t)}"
            con.register(reg, table)
            con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {ident(reg)}")
            con.unregister(reg)
    return ld


def _load_duckdb(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    ld = Loaded(name, [path], "duckdb", kind="database", backing="attached")
    try:
        con.execute(f"ATTACH {sql_str(path)} AS {ident(name)} (READ_ONLY)")
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"{path.name}: cannot open the DuckDB file: {duck_error(e)}") from None
    rows = con.execute(f"SELECT schema_name, table_name, estimated_size FROM duckdb_tables() WHERE database_name = {sql_str(name)} ORDER BY 1, 2").fetchall()
    views = con.execute(f"SELECT schema_name, view_name FROM duckdb_views() WHERE database_name = {sql_str(name)} AND NOT internal ORDER BY 1, 2").fetchall()
    ld.tables = [t if s == "main" else f"{s}.{t}" for s, t, _ in rows] + [v if s == "main" else f"{s}.{v}" for s, v in views]
    return ld


def attach_main_views(con: Any, ld: Loaded) -> None:
    """For a single database input, exposes its tables unqualified too (SELECT * FROM albums)."""
    for t in ld.tables:
        if "." in t:
            # A DuckDB table in another schema (sales.orders): the same schema in memory, so sales.orders works.
            if ld.fmt == "duckdb":
                sch, tbl = t.split(".", 1)
                try:
                    con.execute(f"CREATE SCHEMA IF NOT EXISTS memory.{ident(sch)}")
                    con.execute(f"CREATE VIEW IF NOT EXISTS memory.{ident(sch)}.{ident(tbl)} AS SELECT * FROM {ident(ld.name)}.{ident(sch)}.{ident(tbl)}")
                except Exception:  # noqa: BLE001 — a clash with another input's name
                    pass
            continue
        try:
            if ld.fmt == "duckdb":
                con.execute(f"CREATE VIEW IF NOT EXISTS main.{ident(t)} AS SELECT * FROM {ident(ld.name)}.main.{ident(t)}")
            else:
                exists = con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE schema_name = {sql_str(ld.name)} AND table_name = {sql_str(t)}").fetchone()[0]
                exists = exists or con.execute(f"SELECT count(*) FROM duckdb_views() WHERE schema_name = {sql_str(ld.name)} AND view_name = {sql_str(t)}").fetchone()[0]
                if exists:
                    con.execute(f"CREATE VIEW IF NOT EXISTS main.{ident(t)} AS SELECT * FROM {ident(ld.name)}.{ident(t)}")
        except Exception:  # noqa: BLE001 — a clash with another input's name
            pass


# ── XML ─────────────────────────────────────────────────────────────────


_LOCAL_NAMES: dict[str, str] = {}


def _local(tag: Any) -> str:
    """The tag without its {namespace} (cached: a big XML asks for the same few names millions of times)."""
    if not isinstance(tag, str):
        return ""
    name = _LOCAL_NAMES.get(tag)
    if name is None:
        name = _LOCAL_NAMES[tag] = tag.rsplit("}", 1)[-1]
    return name


def xml_record_tag(path: Path, max_elements: int = 200_000) -> tuple[str, int]:
    """The element path (like catalog/book) that repeats most: one row each."""
    from lxml import etree

    from _formats import open_decompressed, split_ext, magic_compression

    with open(path, "rb") as f:
        comp = magic_compression(f.read(8)) or split_ext(path)[1]
    counts: dict[str, int] = {}
    stack: list[str] = []
    with open_decompressed(path, comp) as f:
        ctx = etree.iterparse(f, events=("start", "end"), resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)
        n = 0
        try:
            for ev, el in ctx:
                if ev == "start":
                    stack.append(_local(el.tag))
                    if len(stack) >= 2:
                        key = "/".join(stack)
                        counts[key] = counts.get(key, 0) + 1
                    n += 1
                    if n >= max_elements:
                        break
                else:
                    stack.pop()
                    if stack:
                        el.clear(keep_tail=True)
                        parent = el.getparent()
                        if parent is not None:
                            while el.getprevious() is not None:
                                del parent[0]
        except etree.XMLSyntaxError as e:
            if not counts:
                raise SkillError(f"{path.name}: invalid XML: {e}") from None
    if not counts:
        return "", 0
    # Rows repeat; prefer the shallowest element path among the most repeated ones.
    top = max(counts.values())
    best = min((k for k, v in counts.items() if v >= max(2, top * 0.2)), key=lambda k: (k.count("/"), -counts[k]), default=None)
    if best is None:
        best = max(counts, key=lambda k: counts[k])
    return best, counts[best]


def xml_records(path: Path, record: str) -> Any:
    """Streams flat dicts for each record element: attributes, child text, nested children as dotted keys."""
    from lxml import etree

    from _formats import open_decompressed, split_ext, magic_compression

    parts = [p for p in record.strip("/").split("/") if p]
    target = parts[-1]
    with open(path, "rb") as f:
        comp = magic_compression(f.read(8)) or split_ext(path)[1]
    with open_decompressed(path, comp) as f:
        stack: list[str] = []
        ctx = etree.iterparse(f, events=("start", "end"), resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)
        depth_match = None
        for ev, el in ctx:
            if ev == "start":
                stack.append(_local(el.tag))
                continue
            here = stack
            is_rec = here[-1] == target and (len(parts) == 1 or here[-len(parts):] == parts)
            if is_rec and depth_match in (None, len(here)):
                depth_match = len(here)
                yield _flat_element(el)
                el.clear()
                parent = el.getparent()
                if parent is not None:
                    while el.getprevious() is not None:
                        del parent[0]
            stack.pop()


def _flat_element(el: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def put(key: str, value: Any) -> None:
        if key in out:
            prev = out[key]
            if isinstance(prev, list):
                prev.append(value)
            else:
                out[key] = [prev, value]
        else:
            out[key] = value

    def go(e: Any, prefix: str) -> None:
        for k, v in e.attrib.items():
            put(f"{prefix}{_local(k)}" if prefix else _local(k), v)
        kids = [c for c in e if isinstance(c.tag, str)]
        text = (e.text or "").strip()
        if not kids:
            if prefix and (text or not e.attrib):  # <link href="…"/> is its attributes only, no empty text column
                put(prefix.rstrip("."), text if text else None)
            elif text:
                put("text", text)
            return
        if text and prefix:
            put(prefix + "text", text)
        for c in kids:
            go(c, f"{prefix}{_local(c.tag)}.")

    go(el, "")
    return out


def _xml_auto_types(con: Any, src: str) -> str:
    """SELECT list casting text columns whose every value is an integer, number, date, timestamp or boolean."""
    rel = con.sql(f"SELECT * FROM {src} LIMIT 0")
    cols = [c for c, t in zip(rel.columns, rel.types) if str(t) == "VARCHAR"]
    json_cols = [c for c, t in zip(rel.columns, rel.types) if str(t) == "JSON"]
    empty: list[str] = []
    if json_cols:
        row = con.sql("SELECT " + ", ".join(f"count({ident(c)})" for c in json_cols) + f" FROM {src}").fetchone()
        empty = [c for c, n in zip(json_cols, row) if not n]
    if not cols and not empty:
        return "*"
    if not cols:
        return "* REPLACE (" + ", ".join(f"CAST(NULL AS VARCHAR) AS {ident(c)}" for c in empty) + ")"
    aggs = []
    for c in cols:
        q = ident(c)
        aggs += [
            f"count({q})",
            f"count(TRY_CAST({q} AS BIGINT)) FILTER (WHERE regexp_full_match({q}, '\\s*[+-]?[0-9]+\\s*') AND NOT regexp_matches({q}, '^\\s*[+-]?0[0-9]'))",
            f"count(TRY_CAST({q} AS DOUBLE)) FILTER (WHERE regexp_matches({q}, '^\\s*[+-]?(\\d+\\.?\\d*|\\.\\d+)([eE][+-]?\\d+)?\\s*$'))",
            f"count(TRY_CAST({q} AS DATE)) FILTER (WHERE regexp_matches({q}, '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'))",
            f"count(TRY_CAST({q} AS TIMESTAMP)) FILTER (WHERE regexp_matches({q}, '^\\d{{4}}-\\d{{2}}-\\d{{2}}[T ]\\d'))",
            f"count(*) FILTER (WHERE lower({q}) IN ('true', 'false'))",
        ]
    row = con.sql(f"SELECT {', '.join(aggs)} FROM {src}").fetchone()
    reps = []
    for i, c in enumerate(cols):
        n, ints, nums, dates, ts, bools = row[6 * i : 6 * i + 6]
        if not n:
            continue
        q = ident(c)
        if ints == n:
            reps.append(f"CAST({q} AS BIGINT) AS {q}")
        elif nums == n:
            reps.append(f"CAST({q} AS DOUBLE) AS {q}")
        elif dates == n:
            reps.append(f"CAST({q} AS DATE) AS {q}")
        elif ts == n:
            reps.append(f"CAST({q} AS TIMESTAMP) AS {q}")
        elif bools == n:
            reps.append(f"(lower({q}) = 'true') AS {q}")
    reps += [f"CAST(NULL AS VARCHAR) AS {ident(c)}" for c in empty]
    return f"* REPLACE ({', '.join(reps)})" if reps else "*"


def _xml_to(con: Any, path: Path, opts: dict[str, Any], nd: Path) -> tuple[str, int]:
    record = opts.get("record")
    if not record:
        record, _ = xml_record_tag(path)
        if not record:
            raise SkillError(f"{path.name}: no repeated elements to use as rows; pick one with --record")
    n = 0
    encode = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).encode
    with open(nd, "w", encoding="utf-8") as f:
        for rec in xml_records(path, record):
            f.write(encode(rec))
            f.write("\n")
            n += 1
    if n == 0:
        raise SkillError(f"{path.name}: no <{record}> elements; pick the row element with --record (see data_tree.py outline)")
    return record, n


def _xml_src(nd: Path) -> str:
    return f"read_json({sql_str(nd)}, format='newline_delimited', maximum_object_size=268435456, sample_size=-1)"


def _load_xml(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    ld = Loaded(name, [path], "xml")
    if info["size"] >= big_bytes("py"):
        return _parquet_backed(con, ld, path, info, opts, _build_xml_parquet)
    work = temp_dir()
    nd = work / "records.ndjson"
    record, n = _xml_to(con, path, opts, nd)
    # Parse the records once (reading JSON infers the schema from every line), then type the text columns.
    con.execute(f"CREATE OR REPLACE TEMP TABLE __xml_raw AS SELECT * FROM {_xml_src(nd)}")
    sel = _xml_auto_types(con, "__xml_raw")
    con.execute(f"CREATE OR REPLACE TABLE {ident(name)} AS SELECT {sel} FROM __xml_raw")
    con.execute("DROP TABLE __xml_raw")
    ld.info["xml_record"] = record
    ld.notes.append(f"rows are the {n} <{record.split('/')[-1]}> elements ({record}); pick another with --record")
    shutil.rmtree(work, ignore_errors=True)
    return ld


def _build_xml_parquet(con: Any, path: Path, info: dict[str, Any], opts: dict[str, Any], out: Path) -> dict[str, Any]:
    nd = out.parent / "records.ndjson"
    record, n = _xml_to(con, path, opts, nd)
    # Parse the records once into an untyped Parquet file, then type the text columns from it.
    raw = out.parent / "records.parquet"
    con.execute(f"COPY (SELECT * FROM {_xml_src(nd)}) TO {sql_str(raw)} (FORMAT parquet, COMPRESSION zstd)")
    nd.unlink(missing_ok=True)
    src = f"read_parquet({sql_str(raw)})"
    sel = _xml_auto_types(con, src)
    con.execute(f"COPY (SELECT {sel} FROM {src}) TO {sql_str(out)} (FORMAT parquet, COMPRESSION zstd)")
    raw.unlink(missing_ok=True)
    return {"notes": [f"rows are the {n} <{record.split('/')[-1]}> elements ({record}); pick another with --record"], "xml_record": record}


# ── YAML / TOML / INI ───────────────────────────────────────────────────


def _load_doc_table(con: Any, name: str, path: Path, info: dict[str, Any], opts: dict[str, Any]) -> Loaded:
    from _tree import flatten_record, load_doc, records_at

    fmt = info["format"]
    ld = Loaded(name, [path], fmt)
    doc = load_doc(path, fmt, opts.get("encoding"))
    if fmt == "ini" and not opts.get("json_path"):
        records = [{"section": s, "key": k, "value": v} for s, kv in doc.items() if isinstance(kv, dict) for k, v in kv.items()]
        ld.notes.append("rows are (section, key, value)")
    else:
        records, where = records_at(doc, opts.get("json_path"))
        if where == "$" and len(records) == 1 and isinstance(records[0], dict):
            records = [flatten_record(records[0])]
            ld.notes.append("one row: the whole document with nested keys as dotted columns (pick an array with --path)")
        else:
            ld.notes.append(f"rows are the {len(records)} items at {where}")
        ld.info["record_path"] = where
    _records_table(con, name, records, temp_dir())
    return ld


_LOADERS = {
    "csv": _load_csv,
    "json": _load_json,
    "jsonl": _load_json,
    "parquet": _load_parquet,
    "arrow": _load_arrow,
    "avro": _load_avro,
    "sav": _load_stat,
    "por": _load_stat,
    "dta": _load_stat,
    "sas7bdat": _load_stat,
    "xpt": _load_stat,
    "sqlite": _load_sqlite,
    "duckdb": _load_duckdb,
    "xml": _load_xml,
    "yaml": _load_doc_table,
    "toml": _load_doc_table,
    "ini": _load_doc_table,
}


def load_all(con: Any, specs: list[Spec], opts: dict[str, Any], sql: str | None = None) -> list[Loaded]:
    """Loads the inputs a query needs (all of them without SQL); a single input is also available as `t`."""
    from _formats import sniff

    loaded: list[Loaded] = []
    for spec in specs:
        if sql is not None and len(specs) > 1:
            fmt = sniff(spec.paths[0])["format"] if len(spec.paths) == 1 else None
            if fmt not in ("sqlite", "duckdb") and not mentioned(sql, spec.name):
                continue
        only = None
        if sql is not None and len(spec.paths) == 1:
            info = sniff(spec.paths[0])
            if info["format"] == "sqlite":
                names = {c["name"] for c in sqlite_catalog(spec.paths[0]) if c["type"] in ("table", "view")}
                only = {n.lower() for n in names if mentioned(sql, n) or re.search(r'\.\s*"?' + re.escape(n) + r'(?![\w"])', sql, re.IGNORECASE)}
                if opts.get("db_table"):
                    only.add(opts["db_table"].lower())
                ld = _load_sqlite(con, spec.name, spec.paths[0], info, opts, only)
                ld.info.setdefault("sniff", info)
                loaded.append(ld)
                continue
        loaded.append(load(con, spec, opts))
    dbs = [ld for ld in loaded if ld.kind == "database"]
    if len(dbs) == 1:
        attach_main_views(con, dbs[0])
    if len(specs) == 1 and loaded and loaded[0].kind != "database" and loaded[0].name.lower() != "t":
        try:
            con.execute(f"CREATE VIEW IF NOT EXISTS t AS SELECT * FROM {ident(loaded[0].name)}")
        except Exception:  # noqa: BLE001
            pass
    return loaded


def one_table(con: Any, ld: Loaded, opts: dict[str, Any]) -> str:
    """The SQL name of the single table an operation works on (a database needs --table unless it has one)."""
    if ld.kind != "database":
        return ident(ld.name)
    tables = ld.tables
    want = opts.get("db_table")
    if want:
        match = [t for t in tables if t.lower() == want.lower()]
        if not match:
            raise UsageError(f"{ld.path.name} has no table {want!r}; tables: {', '.join(tables) or '(none)'}")
        t = match[0]
    elif len(tables) == 1:
        t = tables[0]
    else:
        raise UsageError(f"{ld.path.name} has {len(tables)} tables; pick one with --table ({', '.join(tables[:20])})")
    if ld.fmt == "sqlite" and con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE schema_name = {sql_str(ld.name)} AND table_name = {sql_str(t)}").fetchone()[0] == 0 and con.execute(f"SELECT count(*) FROM duckdb_views() WHERE schema_name = {sql_str(ld.name)} AND view_name = {sql_str(t)}").fetchone()[0] == 0:
        info = ld.info.get("sniff") or {"format": "sqlite", "size": ld.path.stat().st_size}
        _load_sqlite(con, ld.name, ld.path, info, opts, {t.lower()})
    if "." in t:
        s, n = t.split(".", 1)
        return f"{ident(ld.name)}.{ident(s)}.{ident(n)}"
    return f"{ident(ld.name)}.{ident(t)}"


def open_table(path_or_spec: str, opts: dict[str, Any], con: Any = None, name: str | None = None) -> tuple[Any, Loaded, str]:
    """Convenience: (connection, Loaded, SQL table name) for one input file or glob."""
    con = con or connect()
    specs = parse_inputs([path_or_spec])
    if name:
        specs[0].name = name
    ld = load(con, specs[0], opts)
    return con, ld, one_table(con, ld, opts)


def columns_of(con: Any, table_sql: str) -> list[tuple[str, str]]:
    rel = con.sql(f"SELECT * FROM {table_sql} LIMIT 0")
    return [(c, str(t)) for c, t in zip(rel.columns, rel.types)]


def count_rows(con: Any, table_sql: str) -> int:
    return int(con.sql(f"SELECT count(*) FROM {table_sql}").fetchone()[0])


# ── dates stored as text ────────────────────────────────────────────────

#: strptime formats tried on text columns that look like dates (DuckDB and Python share these codes).
DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y", "%d/%m/%y", "%m/%d/%y",
    "%d.%m.%y", "%d-%b-%y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%d %b %y",
    "%Y%m%d", "%Y-%m", "%b %Y", "%B %Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p",
)


def guess_date_formats(con: Any, table_sql: str, col: str, sample: int = 2000) -> dict[str, Any] | None:
    """How a text column's dates are written: {'formats', 'kind' (DATE or TIMESTAMP), 'share', 'ambiguous'}, or None.

    Looks at up to `sample` distinct values; picks the fewest formats (at most 3) covering at least 95% of them.
    'ambiguous' names the day/month alternative when every value also reads the other way round.
    """
    q = f"trim(CAST({ident(col)} AS VARCHAR))"
    try:
        vals = [r[0] for r in con.sql(f"SELECT DISTINCT {q} AS v FROM {table_sql} WHERE {ident(col)} IS NOT NULL AND {q} != '' LIMIT {int(sample)}").fetchall()]
    except Exception:  # noqa: BLE001
        return None
    if not vals or not any(ch.isdigit() for ch in "".join(vals[:50])):
        return None
    con.execute("CREATE OR REPLACE TEMP TABLE __date_vals (v VARCHAR)")
    con.executemany("INSERT INTO __date_vals VALUES (?)", [(v,) for v in vals])
    checks = ", ".join(f"try_strptime(v, {sql_str(f)}) IS NOT NULL" for f in DATE_FORMATS)
    rows = con.sql(f"SELECT {checks} FROM __date_vals").fetchall()
    con.execute("DROP TABLE __date_vals")
    n = len(rows)
    left = set(range(n))
    chosen: list[int] = []
    while left and len(chosen) < 3:
        best = max(range(len(DATE_FORMATS)), key=lambda j: sum(1 for i in left if rows[i][j]))
        hit = {i for i in left if rows[i][best]}
        if not hit:
            break
        chosen.append(best)
        left -= hit
        if len(left) <= 0.05 * n:
            break
    covered = n - len(left)
    if not chosen or covered < 0.95 * n:
        return None
    fmts = [DATE_FORMATS[j] for j in chosen]
    swap = {"%d/%m/%Y": "%m/%d/%Y", "%m/%d/%Y": "%d/%m/%Y", "%d/%m/%y": "%m/%d/%y", "%m/%d/%y": "%d/%m/%y", "%d-%m-%Y": "%m-%d-%Y", "%m-%d-%Y": "%d-%m-%Y",
            "%d/%m/%Y %H:%M": "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M": "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S": "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S": "%d/%m/%Y %H:%M:%S"}
    ambiguous = None
    for f in fmts:
        other = swap.get(f)
        if other and other in DATE_FORMATS:
            j, k = DATE_FORMATS.index(f), DATE_FORMATS.index(other)
            if all(r[k] for r in rows if r[j]):
                ambiguous = other
    kind = "TIMESTAMP" if any("%H" in f or "%I" in f for f in fmts) else "DATE"
    return {"formats": fmts, "kind": kind, "share": round(covered / n, 4), "ambiguous": ambiguous}


def date_cast_sql(col: str, guess: dict[str, Any]) -> str:
    """SQL turning the text column into DATE/TIMESTAMP with the guessed formats (unparseable values become null)."""
    fmts = "[" + ", ".join(sql_str(f) for f in guess["formats"]) + "]"
    return f"CAST(try_strptime(trim(CAST({ident(col)} AS VARCHAR)), {fmts}) AS {guess['kind']})"
