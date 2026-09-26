#!/usr/bin/env python3
"""Profile a table: per-column types, nulls, distinct values, ranges, quantiles, top values, text lengths and
patterns, outliers and suspicious values; duplicate rows, candidate keys and correlations. Cached per file."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, input_file, md_table, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_profile.py orders.csv
  python3 scripts/data_profile.py orders.parquet --columns amount,region,email
  python3 scripts/data_profile.py shop.sqlite --table orders --format json
  python3 scripts/data_profile.py big.csv --sample 200000 --out profile.md
  python3 scripts/data_profile.py a.csv --sql "SELECT * FROM a WHERE country = 'FR'"

Issues are listed first, most important first: all-null or constant columns, duplicate rows, numbers or dates
stored as text, mixed date formats, values differing only by case or spaces, placeholder nulls ('NA', '-'),
stray spaces, outliers (1.5 × IQR), values that break a column's dominant pattern (emails, URLs, dates, IDs).
Results are cached per file content: profiling the same file again is instant.
"""

PATTERNS = [
    ("email", r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)*\.[^@\s.\d]{2,}$"),
    ("url", r"^(https?|ftp)://[^\s/$.?#].[^\s]*$"),
    ("uuid", r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    ("iso datetime", r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$"),
    ("iso date", r"^\d{4}-\d{2}-\d{2}$"),
    ("d/m/y or m/d/y date", r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    ("d.m.y date", r"^\d{1,2}\.\d{1,2}\.\d{2,4}$"),
    ("y/m/d date", r"^\d{4}/\d{1,2}/\d{1,2}$"),
    ("text date", r"^\d{1,2}[ -][A-Za-z]{3,9}\.?[ -]\d{2,4}$|^[A-Za-z]{3,9}\.? \d{1,2},? \d{4}$"),
    ("phone", r"^\+?[\d][\d ().\-]{6,}\d$"),
    ("ipv4", r"^(\d{1,3}\.){3}\d{1,3}$"),
    ("zero-padded id", r"^0\d+$"),
    ("integer text", r"^[+-]?\d+$"),
    ("decimal text", r"^[+-]?\d*[.,]\d+$"),
    ("code (letters+digits)", r"^[A-Z]{1,5}[-_]?\d+$"),
]
DATE_PATTERNS = ("iso date", "iso datetime", "d/m/y or m/d/y date", "d.m.y date", "y/m/d date", "text date")
PLACEHOLDERS = ("na", "n/a", "null", "none", "nan", "nil", "-", "--", "?", "missing", "unknown", "#n/a", "undefined")
PLACEHOLDER_SPELLINGS = sorted({v for x in PLACEHOLDERS for v in (x, x.upper(), x.capitalize())})
NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "REAL")


def main() -> int:
    from _duck import add_read_args

    p = parser("Profile a table: stats per column, data-quality issues, duplicates, keys, correlations.", EPILOG)
    p.add_argument("file", help="data file (any format this skill reads), or a glob")
    p.add_argument("--sql", help="profile the result of this query instead (the file is table t and its stem)")
    p.add_argument("--columns", help="only these columns (comma-separated)")
    p.add_argument("--sample", type=int, help="profile a random sample of this many rows (reproducible)")
    p.add_argument("--top", type=int, default=5, help="top values per column (default 5)")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--out", help="also write the report to a .md or .json file")
    p.add_argument("--force", action="store_true")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    add_read_args(p)
    a = p.parse_args()
    from _duck import read_opts

    opts = read_opts(a)
    t0 = time.time()
    src = Path(a.file)
    params = {"columns": a.columns, "sample": a.sample, "top": a.top, "sql": a.sql, **{k: v for k, v in opts.items()}}
    import _cache

    if src.exists() and src.is_file():
        input_file(src)
        hit = _cache.lookup(src, "profile", params, "3")
        prof = _cache.cached_json(src, "profile", params, "3", lambda: compute(a, opts))
        prof["cached"] = hit is not None
    else:
        prof = compute(a, opts)
        prof["cached"] = False
    prof["seconds"] = round(time.time() - t0, 2)
    if a.format == "json":
        import json

        text = json.dumps(prof, ensure_ascii=False, indent=1, default=str)
    else:
        text = render(prof)
    if a.out:
        out = output_path(a.out, [src] if src.exists() else [], a.force)
        body = text if out.suffix.lower() != ".json" or a.format == "json" else __import__("json").dumps(prof, ensure_ascii=False, indent=1, default=str)
        out.write_text(body if out.suffix.lower() == ".json" else render(prof), encoding="utf-8")
    if a.format == "json":
        from _out import print_json

        print_json(prof, a.max_chars, ("profiles", "correlations", "issues"), "Profile fewer columns with --columns, or write the full report with --out report.json.")
    else:
        print(cap(text, a.max_chars, "Profile fewer columns with --columns, or write the full report with --out report.md."))
    if a.out:
        print(f"\nFull report: {a.out}")
    return 0


# ── computing ───────────────────────────────────────────────────────────


def compute(a: Any, opts: dict[str, Any]) -> dict[str, Any]:
    from _duck import connect, guard_sql, ident, load_all, one_table, parse_inputs

    con = connect()
    if a.sql:
        guard_sql(con, a.sql, "data_query.py --out")
    specs = parse_inputs([a.file])
    loaded = load_all(con, specs, opts)
    ld = loaded[0]
    table = one_table(con, ld, opts)
    notes = list(ld.notes)
    if a.sql:
        try:
            con.execute(f"CREATE TEMP TABLE __src AS {a.sql}")
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"SQL error: {str(e).splitlines()[0]}") from None
        table = "__src"
    total = int(con.sql(f"SELECT count(*) FROM {table}").fetchone()[0])
    if a.sample and a.sample < total:
        con.execute(f"CREATE TEMP TABLE __sample AS SELECT * FROM {table} USING SAMPLE reservoir({int(a.sample)} ROWS) REPEATABLE (42)")
        table = "__sample"
        notes.append(f"profiled a random sample of {a.sample:,} of {total:,} rows")
    rel = con.sql(f"SELECT * FROM {table} LIMIT 0")
    cols = list(zip(rel.columns, [str(t) for t in rel.types]))
    if a.columns:
        want = [c.strip() for c in a.columns.split(",") if c.strip()]
        lower = {c.lower(): (c, t) for c, t in cols}
        missing = [w for w in want if w.lower() not in lower]
        if missing:
            raise UsageError(f"no column {missing[0]!r}; columns: {', '.join(c for c, _ in cols)}")
        cols = [lower[w.lower()] for w in want]
    n = int(con.sql(f"SELECT count(*) FROM {table}").fetchone()[0])
    exact = n <= 2_000_000
    shown = table.split(".")[-1].strip('"') if ld.kind == "database" else ld.name
    out: dict[str, Any] = {"file": a.file, "table": shown, "rows": n, "total_rows": total, "columns": len(cols), "notes": notes, "exact_distinct": exact}
    profiles = [{"name": c, "type": t, "kind": kind_of(t)} for c, t in cols]
    if n == 0:
        out["profiles"] = profiles
        out["issues"] = [{"column": None, "severity": 3, "issue": "the table has no rows"}]
        return out
    basic(con, table, profiles, n, exact)
    numeric(con, table, [p for p in profiles if p["kind"] == "number"])
    text_checks(con, table, [p for p in profiles if p["kind"] == "text"], n)
    for p in profiles:
        top_values(con, table, p, a.top)
    out["duplicates"] = duplicates(con, table, [p for p in profiles], n) if not a.columns else None
    out["candidate_keys"] = candidate_keys(con, table, profiles, n)
    out["correlations"] = correlations(con, table, [p for p in profiles if p["kind"] == "number" and p.get("distinct", 0) > 2])
    out["profiles"] = profiles
    out["issues"] = issues(profiles, out, n)
    return out


def kind_of(t: str) -> str:
    base = t.split("(")[0].upper()
    if base in NUMERIC or base.startswith("DECIMAL"):
        return "number"
    if base in ("VARCHAR", "TEXT", "STRING") or base.startswith("ENUM"):
        return "text"
    if base in ("DATE",) or base.startswith("TIMESTAMP") or base == "TIME":
        return "date"
    if base == "BOOLEAN":
        return "bool"
    if t.endswith("[]") or base.startswith(("STRUCT", "MAP", "UNION")) or "[" in t:
        return "nested"
    return "other"


def basic(con: Any, table: str, profiles: list[dict[str, Any]], n: int, exact: bool) -> None:
    from _duck import ident

    aggs = []
    for p in profiles:
        q = ident(p["name"])
        aggs.append(f"count({q})")
        if p["kind"] == "nested":
            aggs.append(f"approx_count_distinct(CAST({q} AS VARCHAR))")
        else:
            aggs.append(f"count(DISTINCT {q})" if exact else f"approx_count_distinct({q})")
        if p["kind"] == "number":
            aggs += [f"min({q})", f"max({q})"]
        elif p["kind"] in ("date", "text"):
            aggs += [f"CAST(min({q}) AS VARCHAR)", f"CAST(max({q}) AS VARCHAR)"]
        elif p["kind"] == "bool":
            aggs += [f"CAST(count(*) FILTER (WHERE {q}) AS VARCHAR)", "NULL"]
        else:
            aggs += ["NULL", "NULL"]
    row = con.sql(f"SELECT {', '.join(aggs)} FROM {table}").fetchone()
    for i, p in enumerate(profiles):
        nn, d, mn, mx = row[4 * i : 4 * i + 4]
        p["non_null"] = int(nn)
        p["nulls"] = n - int(nn)
        p["null_share"] = round((n - int(nn)) / n, 4)
        p["distinct"] = int(d)
        if p["kind"] == "bool":
            p["true"] = int(mn) if mn is not None else 0
        elif p["kind"] == "number":
            p["min"], p["max"] = _num(mn), _num(mx)
        else:
            p["min"], p["max"] = _short(mn), _short(mx)
        p["distinct"] = min(p["distinct"], p["non_null"])
    if not exact:
        # Approximate counts are ±2%: make near-unique columns exact, so keys and "all distinct" are right.
        near = [p for p in profiles if p["kind"] != "nested" and p["non_null"] and p["distinct"] >= 0.75 * p["non_null"]]
        if near:
            row = con.sql("SELECT " + ", ".join(f"count(DISTINCT {ident(p['name'])})" for p in near) + f" FROM {table}").fetchone()
            for p, d in zip(near, row):
                p["distinct"] = int(d)
                p["distinct_exact"] = True


def _num(v: Any) -> Any:
    """Decimals become floats (JSON and Markdown friendly); integers stay exact."""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v) if v != v.to_integral_value() else int(v)
    if isinstance(v, float) and v == v and abs(v) < 1e15:
        return float(f"{v:.12g}")  # the value, without float noise (0.1 + 0.2)
    return v


def _short(v: Any, n: int = 80) -> Any:
    if isinstance(v, str) and len(v) > n:
        return v[:n] + "…"
    return v


def numeric(con: Any, table: str, profiles: list[dict[str, Any]]) -> None:
    from _duck import ident

    if not profiles:
        return
    aggs = []
    for p in profiles:
        q = f"CAST({ident(p['name'])} AS DOUBLE)"
        aggs += [f"avg({q})", f"stddev_samp({q})", f"quantile_cont({q}, [0.01, 0.25, 0.5, 0.75, 0.99])", f"count(*) FILTER (WHERE {q} = 0)", f"count(*) FILTER (WHERE {q} < 0)", f"sum({q})"]
    row = con.sql(f"SELECT {', '.join(aggs)} FROM {table}").fetchone()
    out_aggs = []
    for i, p in enumerate(profiles):
        avg, std, qs, zeros, neg, total = row[6 * i : 6 * i + 6]
        p.update(mean=_r(avg), std=_r(std), zeros=zeros, negatives=neg, sum=_r(total))
        if qs:
            p.update(p01=_r(qs[0]), q25=_r(qs[1]), median=_r(qs[2]), q75=_r(qs[3]), p99=_r(qs[4]))
            iqr = qs[3] - qs[1]
            if iqr <= 0:
                # Half the values or more are one number (counts, flags): every other value would be an "outlier".
                out_aggs += ["0", "[]"]
                continue
            lo, hi = qs[1] - 1.5 * iqr, qs[3] + 1.5 * iqr
            p["outlier_bounds"] = [_r(lo), _r(hi)]
            q = f"CAST({ident(p['name'])} AS DOUBLE)"
            out_aggs += [f"count(*) FILTER (WHERE {q} < {lo!r} OR {q} > {hi!r})", f"list(DISTINCT {q}) FILTER (WHERE {q} < {lo!r} OR {q} > {hi!r})[1:5]"]
        else:
            out_aggs += ["0", "[]"]
    row = con.sql(f"SELECT {', '.join(out_aggs)} FROM {table}").fetchone()
    for i, p in enumerate(profiles):
        p["outliers"] = int(row[2 * i] or 0)
        p["outlier_examples"] = [_r(x) for x in (row[2 * i + 1] or [])][:5]


def _r(v: Any) -> Any:
    if isinstance(v, float):
        if v != v:
            return None
        return float(f"{v:.6g}") if abs(v) < 1e15 else v
    return v


def text_checks(con: Any, table: str, profiles: list[dict[str, Any]], n: int) -> None:
    from _duck import ident

    if not profiles:
        return
    aggs = []
    ph = ", ".join("'" + x.replace("'", "''") + "'" for x in PLACEHOLDER_SPELLINGS)
    for p in profiles:
        q = ident(p["name"])
        exact_distinct = p.get("distinct_exact") or n <= 2_000_000
        check_case = exact_distinct and 1 < p.get("distinct", 0) <= 200_000
        aggs += [
            f"min(length({q}))", f"avg(length({q}))", f"max(length({q}))",
            f"count(*) FILTER (WHERE {q} = '')",
            f"count(*) FILTER (WHERE {q} != trim({q}) OR contains({q}, '  '))",
            f"count(*) FILTER (WHERE {q} IN ({ph}))",
            f"count(DISTINCT lower(trim({q})))" if check_case else "NULL",
        ]
    row = con.sql(f"SELECT {', '.join(aggs)} FROM {table}").fetchone()
    for i, p in enumerate(profiles):
        lmin, lavg, lmax, empty, spaces, placeholders, dist_norm = row[7 * i : 7 * i + 7]
        p.update(len_min=lmin, len_avg=_r(lavg), len_max=lmax, empty=empty, stray_spaces=spaces, placeholders=placeholders)
        # Extra spellings that differ only by case or surrounding spaces (exact distinct counts).
        p["case_space_variants"] = max(0, p["distinct"] - int(dist_norm)) if dist_norm is not None else 0
    # Patterns on a sample, over distinct values weighted by their counts (regexes are the costly part).
    sample = table
    if n > 50_000:
        cols = ", ".join(ident(p["name"]) for p in profiles)
        con.execute(f"CREATE OR REPLACE TEMP TABLE __pat_sample AS SELECT {cols} FROM {table} USING SAMPLE reservoir(50000 ROWS) REPEATABLE (7)")
        sample = "__pat_sample"
    for p in profiles:
        v = ident(p["name"])
        aggs = ["sum(c)", "sum(c) FILTER (WHERE TRY_CAST(replace(v, ',', '.') AS DOUBLE) IS NOT NULL)"] + [f"sum(c) FILTER (WHERE regexp_full_match(v, '{rx}'))" for _, rx in PATTERNS]
        vals = con.sql(f"SELECT {', '.join(aggs)} FROM (SELECT trim({v}) AS v, count(*) AS c FROM {sample} WHERE {v} IS NOT NULL AND trim({v}) != '' GROUP BY 1)").fetchone()
        base = vals[0] or 0
        p["numeric_share"] = round((vals[1] or 0) / base, 4) if base else 0.0
        shares = {name: round(c / base, 4) for (name, _), c in zip(PATTERNS, vals[2:]) if base and c}
        p["patterns"] = shares
        p["pattern_sample"] = n > 50_000
        if shares:
            best = max(shares.items(), key=lambda kv: kv[1])
            p["pattern"] = best[0] if best[1] >= 0.5 else None
    from _duck import guess_date_formats

    for p in profiles:
        pats = p.get("patterns") or {}
        if sum(v for k, v in pats.items() if k in DATE_PATTERNS) >= 0.5:
            g = guess_date_formats(con, table, p["name"])
            if g:
                p["date_guess"] = g
                from _duck import date_cast_sql

                lo, hi = con.sql(f"SELECT CAST(min(d) AS VARCHAR), CAST(max(d) AS VARCHAR) FROM (SELECT {date_cast_sql(p['name'], g)} AS d FROM {table})").fetchone()
                if lo is not None:
                    p["min"], p["max"] = lo, hi  # as dates, not in text order
    for p in profiles:
        q = ident(p["name"])
        if p.get("case_space_variants"):
            rows = con.sql(f"SELECT list(DISTINCT {q})[1:4] FROM {table} WHERE {q} IS NOT NULL GROUP BY lower(trim({q})) HAVING count(DISTINCT {q}) > 1 ORDER BY count(*) DESC LIMIT 3").fetchall()
            p["case_space_examples"] = [r[0] for r in rows]
        pat = p.get("pattern")
        if pat and p["patterns"][pat] < 1.0:
            rx = dict(PATTERNS)[pat]
            if pat in DATE_PATTERNS:
                cond = " AND ".join(f"NOT regexp_full_match(trim({q}), '{dict(PATTERNS)[d]}')" for d in DATE_PATTERNS)
            else:
                cond = f"NOT regexp_full_match(trim({q}), '{rx}')"
            bad = con.sql(f"SELECT count(*), list(DISTINCT {q})[1:5] FROM {table} WHERE {q} IS NOT NULL AND trim({q}) != '' AND {cond}").fetchone()
            p["pattern_violations"] = int(bad[0])
            p["pattern_violation_examples"] = [_short(x, 60) for x in (bad[1] or [])]


def top_values(con: Any, table: str, p: dict[str, Any], k: int) -> None:
    from _duck import ident

    if k <= 0 or p["kind"] == "nested" or p.get("distinct", 0) == 0:
        return
    if p.get("distinct", 0) >= p.get("non_null", 0) and p.get("non_null", 0) > 1:
        p["all_distinct"] = True
        return
    if p["kind"] == "number" and p.get("distinct", 0) > 0.5 * max(1, p.get("non_null", 1)) and p["non_null"] > 50:
        return  # continuous: quantiles say more
    q = ident(p["name"])
    rows = con.sql(f"SELECT {q}, count(*) AS n FROM {table} WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {k}").fetchall()
    p["top"] = [[_short(v, 60), c] for v, c in rows]


def duplicates(con: Any, table: str, profiles: list[dict[str, Any]], n: int) -> dict[str, Any]:
    try:
        distinct = int(con.sql(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {table})").fetchone()[0])
    except Exception:  # noqa: BLE001 — types DISTINCT cannot compare
        return {"rows": None}
    dup = n - distinct
    out: dict[str, Any] = {"rows": dup}
    if dup:
        try:
            cols = con.sql(f"SELECT * FROM {table} LIMIT 0").columns
            rows = con.sql(f"SELECT *, count(*) AS __n FROM {table} GROUP BY ALL HAVING count(*) > 1 ORDER BY __n DESC LIMIT 3").fetchall()
            out["examples"] = [{"times": r[-1], "row": dict(zip(cols, (_short(v, 40) for v in r[:-1])))} for r in rows]
        except Exception:  # noqa: BLE001
            pass
    return out


def candidate_keys(con: Any, table: str, profiles: list[dict[str, Any]], n: int) -> list[str]:
    from _duck import ident

    def keyable(p: dict[str, Any]) -> bool:  # measured floats are unique by accident, not keys
        return p["kind"] != "nested" and str(p.get("type", "")).upper() not in ("DOUBLE", "FLOAT", "REAL")

    singles = [p["name"] for p in profiles if p.get("distinct") == n and p.get("nulls") == 0 and keyable(p) and (p.get("distinct_exact") or n <= 2_000_000)]
    if singles or n < 2:
        return singles[:10]
    cands = sorted((p for p in profiles if p.get("nulls") == 0 and keyable(p) and 1 < p.get("distinct", 0) < n and p.get("len_avg", 0) < 64), key=lambda p: -p.get("distinct", 0))[:7]
    keys = []
    checks = 0
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i]["name"], cands[j]["name"]
            if cands[i]["distinct"] * cands[j]["distinct"] < n:
                continue
            checks += 1
            if checks > 15:
                break
            d = con.sql(f"SELECT count(*) FROM (SELECT DISTINCT {ident(a)}, {ident(b)} FROM {table})").fetchone()[0]
            if d == n:
                keys.append(f"{a} + {b}")
    return keys[:5]


def correlations(con: Any, table: str, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from _duck import ident

    nums = profiles[:25]
    if len(nums) < 2:
        return []
    pairs = [(a["name"], b["name"]) for i, a in enumerate(nums) for b in nums[i + 1 :]]
    aggs = ", ".join(f"corr(CAST({ident(x)} AS DOUBLE), CAST({ident(y)} AS DOUBLE))" for x, y in pairs)
    row = con.sql(f"SELECT {aggs} FROM {table}").fetchone()
    out = [{"a": x, "b": y, "r": round(r, 3)} for (x, y), r in zip(pairs, row) if r is not None and r == r]
    out.sort(key=lambda d: -abs(d["r"]))
    return out


def issues(profiles: list[dict[str, Any]], prof: dict[str, Any], n: int) -> list[dict[str, Any]]:
    """Data-quality findings, most important first (severity 3 = likely wrong, 1 = worth a look)."""
    out: list[dict[str, Any]] = []

    def add(col: str | None, sev: int, text: str) -> None:
        out.append({"column": col, "severity": sev, "issue": text})

    dup = (prof.get("duplicates") or {}).get("rows")
    if dup:
        add(None, 3, f"{dup:,} duplicate rows (identical in every column)")
    for p in profiles:
        c = p["name"]
        nn = p.get("non_null", 0)
        if nn == 0:
            add(c, 3, "every value is null")
            continue
        if p.get("distinct") == 1 and n > 1:
            add(c, 1, f"constant: every non-null value is {p.get('min')!r}")
        if p.get("null_share", 0) >= 0.5:
            add(c, 2, f"{p['null_share']:.0%} nulls")
        if p["kind"] == "text":
            share = p.get("numeric_share", 0.0)
            pats = p.get("patterns") or {}
            date_share = sum(v for k, v in pats.items() if k in DATE_PATTERNS)
            approx = "about " if p.get("pattern_sample") else ""
            if share >= 0.95 and pats.get("zero-padded id", 0) < 0.05:
                add(c, 2, f"numbers stored as text ({approx}{share:.0%} of values parse as numbers)")
            elif 0.05 < share < 0.95 and date_share < 0.5:
                add(c, 2, f"mixed types: {approx}{share:.0%} of values are numbers, the rest text")
            if date_share >= 0.8:
                used = {k: v for k, v in pats.items() if k in DATE_PATTERNS and v >= 0.01}
                g = p.get("date_guess")
                how = ""
                if g:
                    how = f"; read them with data_convert --cast '{c}={g['kind']}:{'|'.join(g['formats'])}' or try_strptime({c}, {g['formats']}) in SQL"
                    if g.get("ambiguous"):
                        how += f" (day/month order is ambiguous: {g['ambiguous']} reads every value too; check with the user)"
                if len(used) > 1 or (g and len(g["formats"]) > 1):
                    add(c, 3, "mixed date formats: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])) + how)
                else:
                    add(c, 1, f"dates stored as text ({(g or {}).get('formats', [next(iter(used), 'dates')])[0]})" + (how or "; cast with strptime in data_query"))
            if p.get("case_space_variants"):
                ex = "; ".join(" / ".join(repr(x) for x in grp) for grp in (p.get("case_space_examples") or [])[:2])
                add(c, 2, f"values that differ only by case or spaces: {p['case_space_variants']:,} extra spellings" + (f" (e.g. {ex})" if ex else ""))
            if p.get("placeholders"):
                add(c, 2, f"{p['placeholders']:,} placeholder values that probably mean null ('NA', 'N/A', '-', 'null' …)")
            if p.get("stray_spaces"):
                add(c, 1, f"{p['stray_spaces']:,} values with leading, trailing or double spaces")
            if p.get("empty"):
                add(c, 1, f"{p['empty']:,} empty strings (not null)")
            if p.get("pattern_violations"):
                ex = ", ".join(repr(x) for x in p.get("pattern_violation_examples", [])[:3])
                add(c, 2, f"{p['pattern_violations']:,} values are not {p['pattern']} like the rest" + (f" (e.g. {ex})" if ex else ""))
        if p["kind"] == "number" and p.get("outliers"):
            share = p["outliers"] / nn
            ex = ", ".join(str(x) for x in p.get("outlier_examples", [])[:4])
            add(c, 2 if share < 0.01 else 1, f"{p['outliers']:,} outliers outside {p['outlier_bounds'][0]} … {p['outlier_bounds'][1]} (1.5 × IQR)" + (f", e.g. {ex}" if ex else ""))
    out.sort(key=lambda d: -d["severity"])
    return out


# ── rendering ───────────────────────────────────────────────────────────


def render(prof: dict[str, Any]) -> str:
    from _out import cell

    n = prof["rows"]
    lines = [f"# Profile: {Path(prof['file']).name} (table `{prof['table']}`, {n:,} rows × {prof['columns']} columns)", ""]
    for note in prof.get("notes", []):
        lines.append(f"- {note}")
    dup = prof.get("duplicates") or {}
    facts = []
    if dup.get("rows") is not None:
        facts.append(f"duplicate rows: {dup['rows']:,}")
    keys = prof.get("candidate_keys") or []
    facts.append("candidate keys: " + (", ".join(keys) if keys else "none"))
    if not prof.get("exact_distinct"):
        facts.append("distinct counts are approximate (≈2%)")
    if prof.get("cached"):
        facts.append("from cache")
    lines.append("- " + " · ".join(facts))
    iss = prof.get("issues") or []
    lines += ["", f"## Issues ({len(iss)})", ""]
    if not iss:
        lines.append("No data-quality issues found.")
    for i in iss:
        mark = {3: "**!!**", 2: "**!**", 1: "·"}[i["severity"]]
        lines.append(f"- {mark} " + (f"`{i['column']}`: " if i["column"] else "") + i["issue"])
    profs = prof.get("profiles", [])
    lines += ["", "## Columns", ""]
    rows = []
    for p in profs:
        nul = "0" if not p.get("nulls") else (f"{p['null_share']:.1%}" if p["null_share"] >= 0.001 else f"{p['nulls']:,}")
        rng = ""
        if p.get("min") is not None:
            rng = f"{cell(p['min'], 24)} … {cell(p['max'], 24)}"
        elif p["kind"] == "bool":
            rng = f"true {p.get('true', 0):,}"
        top = "all distinct" if p.get("all_distinct") else ", ".join(f"{cell(v, 24)} ({c:,})" for v, c in (p.get("top") or [])[:3])
        rows.append([p["name"], p["type"], nul, f"{p.get('distinct', 0):,}", rng, top])
    lines.append(md_table(["column", "type", "nulls", "distinct", "min … max", "top values"], rows))
    nums = [p for p in profs if p["kind"] == "number" and p.get("non_null")]
    if nums:
        lines += ["", "## Numbers", ""]
        lines.append(md_table(["column", "mean", "std", "p01", "q25", "median", "q75", "p99", "zeros", "neg", "outliers"], [[p["name"], p.get("mean"), p.get("std"), p.get("p01"), p.get("q25"), p.get("median"), p.get("q75"), p.get("p99"), f"{p.get('zeros', 0):,}", f"{p.get('negatives', 0):,}", f"{p.get('outliers', 0):,}"] for p in nums]))
    texts = [p for p in profs if p["kind"] == "text" and p.get("non_null")]
    if texts:
        lines += ["", "## Text", ""]
        trs = []
        for p in texts:
            pats = p.get("patterns") or {}
            pat = ", ".join(f"{k} {v:.0%}" for k, v in sorted(pats.items(), key=lambda kv: -kv[1])[:2])
            trs.append([p["name"], f"{p.get('len_min')}/{p.get('len_avg')}/{p.get('len_max')}", pat or "free text", f"{p.get('empty', 0):,}", f"{p.get('stray_spaces', 0):,}", f"{p.get('case_space_variants', 0):,}"])
        lines.append(md_table(["column", "length min/avg/max", "patterns", "empty", "stray spaces", "case variants"], trs))
        if any(p.get("pattern_sample") for p in texts):
            lines.append("(patterns measured on a 50,000-row random sample)")
    corr = [c for c in prof.get("correlations") or [] if abs(c["r"]) >= 0.5]
    if corr:
        lines += ["", "## Correlations (|r| ≥ 0.5)", ""]
        lines.append(md_table(["a", "b", "r"], [[c["a"], c["b"], c["r"]] for c in corr[:20]]))
    if dup.get("examples"):
        lines += ["", "## Duplicate examples", ""]
        for e in dup["examples"]:
            lines.append(f"- ×{e['times']}: " + ", ".join(f"{k}={cell(v, 30)}" for k, v in list(e["row"].items())[:8]))
    lines += ["", f"[{prof.get('seconds', 0)}s]"]
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
