#!/usr/bin/env python3
"""Recalculate every formula and store the results as cached values, or just check for errors."""

from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, input_file, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/sheet_recalc.py model.xlsx --check                   # errors, cycles, unsupported functions; writes nothing
  python3 scripts/sheet_recalc.py model.xlsx --out model-calc.xlsx     # store computed values so every reader sees numbers
  python3 scripts/sheet_recalc.py model.xlsx --check --compare         # computed vs cached values (stale caches, engine gaps)
  python3 scripts/sheet_recalc.py model.xlsx --out out.xlsx --engine libreoffice   # full LibreOffice recalculation
  python3 scripts/sheet_recalc.py model.xlsx --check --engine libreoffice          # LibreOffice's errors, on a temporary copy
  python3 scripts/sheet_recalc.py plan.xlsx --check --now 2025-01-31   # fix TODAY()/NOW() for reproducible results

The built-in engine covers about 400 Excel functions, dynamic arrays (spills), structured table references, defined
names, iterative calculation and the 1900/1904 date systems. Functions it does not know keep their cached values and
are listed by name and cell; nothing is guessed.
"""


def main() -> int:
    p = parser("Recalculate an .xlsx/.xlsm workbook with the built-in engine (or LibreOffice).", EPILOG)
    p.add_argument("file")
    p.add_argument("--out", help="write the recalculated workbook here (the input is never modified)")
    p.add_argument("--check", action="store_true", help="only report; write nothing")
    p.add_argument("--engine", choices=["builtin", "libreoffice", "auto"], default="builtin", help="auto = LibreOffice when the built-in engine meets unsupported functions and LibreOffice is installed; with --check LibreOffice recalculates a temporary copy")
    p.add_argument("--compare", action="store_true", help="compare computed values with the values cached in the file")
    p.add_argument("--now", help="date/time for TODAY() and NOW(), e.g. 2025-01-31 or 2025-01-31T09:30")
    p.add_argument("--full-calc-on-load", choices=["keep", "on", "off"], default="keep", help="ask Excel to recalculate when it opens the file (default: keep the file's setting)")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    p.add_argument("--max-errors", type=int, default=50, help="error cells to list (default 50)")
    p.add_argument("--no-cache", action="store_true", help="recompute even if this exact file was recalculated before")
    add_format(p)
    a = p.parse_args()
    src = input_file(a.file, {".xlsx", ".xlsm", ".xltx", ".xltm"})
    from _book import kind_of

    if kind_of(src) != "xlsx":
        raise SkillError(f"{src.name} is not an .xlsx-family workbook; convert it first with sheet_convert.py (e.g. --to xlsx)")
    if not a.out and not a.check:
        raise UsageError("pass --out <file> to write the recalculated workbook, or --check to only report")
    out = output_path(a.out, [src], a.force) if a.out and not a.check else None
    now = None
    if a.now:
        try:
            now = _dt.datetime.fromisoformat(a.now)
        except ValueError as e:
            raise UsageError(f"--now: {e}") from e
    fcol = None if a.full_calc_on_load == "keep" else a.full_calc_on_load == "on"
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    t0 = time.time()
    report = cached_recalc(src, out, a, now, fcol)
    report["seconds"] = round(time.time() - t0, 2)
    if out is not None:
        report["output"] = str(out)
    if now is not None:
        report["now"] = now.isoformat()
    emit(report, a.format, lambda r: render(r, a.max_errors))
    return 0


#: bump when the engine or the report changes, so cached reports and workbooks are rebuilt
CACHE_VERSION = "2"


def cached_recalc(src: Path, out: Path | None, a: Any, now: Any, fcol: bool | None) -> dict[str, Any]:
    """The report (and the recalculated workbook for --out), computed at most once per file content and options:
    a second --check or --out on an unchanged 40 MB workbook takes well under a second instead of ten."""
    import json
    import shutil

    from _cache import cached_dir, cached_json, release

    params = {"engine": a.engine, "compare": a.compare, "now": a.now or f"today {_dt.date.today().isoformat()}", "max": a.max_errors, "fcol": a.full_calc_on_load}
    if a.engine != "builtin":
        import _lo

        params["lo"] = _lo.available()  # a report made without LibreOffice is not reused once it is installed
    ran = []

    def jsonable(r: dict[str, Any]) -> dict[str, Any]:
        r.pop("_book", None)
        return json.loads(json.dumps(r, default=str))

    if out is None:
        def check() -> dict[str, Any]:
            ran.append(1)
            return jsonable(recalc(src, None, a, now, fcol))

        report = cached_json(src, "recalc-check", params, CACHE_VERSION, check)
    else:
        name = "out" + out.suffix.lower()

        def build(tmp: Path) -> None:
            ran.append(1)
            r = jsonable(recalc(src, tmp / name, a, now, fcol))
            (tmp / "report.json").write_text(json.dumps(r), encoding="utf-8")

        d = cached_dir(src, "recalc-out", params, CACHE_VERSION, build)
        try:
            shutil.copyfile(d / name, out)
            report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        finally:
            release(d)
    if not ran:
        report.setdefault("notes", []).append("Reused the result computed earlier for this exact file (--no-cache recomputes).")
    return report


def recalc(src: Path, out: Path | None, a: Any, now: Any, fcol: bool | None) -> dict[str, Any]:
    from _formula import Engine
    from _xlsx import load_book, write_values

    engine_used = "builtin"
    book, pkg, extra = load_book(src)
    try:
        eng = Engine(book, now=now)
        report = eng.recalc()
        report["data_tables"] = extra.get("data_tables", 0)
        if a.compare:
            report["compare"] = compare(book, a.max_errors)
        want_lo = a.engine == "libreoffice" or (a.engine == "auto" and report.get("unsupported"))
        if want_lo:
            import _lo

            if not _lo.available():
                if a.engine == "libreoffice":
                    raise SkillError("LibreOffice is not installed; use --engine builtin")
                report.setdefault("notes", []).append("LibreOffice is not installed, so unsupported functions kept their cached values.")
            else:
                engine_used = "libreoffice"
        if out is not None and engine_used == "builtin":
            report.update(write_values(src, out, pkg, book, extra, fcol))
    finally:
        pkg.close()
    if engine_used == "libreoffice":
        # LibreOffice runs for --check too (on a temporary copy): the report never names an engine that did not run
        unknown = sorted(report.get("unsupported") or {})
        report = libreoffice_recalc(src, out, a.max_errors, book if a.compare else None)
        if unknown and a.engine == "auto":
            report["notes"].insert(0, f"The built-in engine does not evaluate {', '.join(k.split(': ', 1)[-1] for k in unknown)}, so LibreOffice recalculated the workbook.")
        if now is not None:
            report["notes"].append("--now applies to the built-in engine only: LibreOffice used today's date for TODAY() and NOW().")
    report["engine"] = engine_used
    return report


def compare(book: Any, limit: int) -> dict[str, Any]:
    """Cells whose computed value differs from the value cached in the file."""
    from _a1 import col_letter
    from _formula import XLError, strip_prefixes

    diffs = []
    checked = 0
    for sh in book.sheets:
        for (r, c), fc in sorted(sh.formulas.items()):
            if fc.cached is None or fc.problem:
                continue
            checked += 1
            if not _same(fc.value, fc.cached):
                if len(diffs) < limit:
                    diffs.append({"cell": f"{sh.name}!{col_letter(c)}{r}", "formula": "=" + strip_prefixes(fc.text.lstrip("=")), "cached": _show(fc.cached), "computed": _show(fc.value)})
                else:
                    diffs.append(None)
    real = [d for d in diffs if d is not None]
    return {"checked": checked, "differences": len(diffs), "cells": real}


def _show(v: Any) -> Any:
    if hasattr(v, "code"):
        return v.code
    return v


def _same(a: Any, b: Any) -> bool:
    ta, tb = type(a), type(b)
    if hasattr(a, "code") or hasattr(b, "code"):
        return getattr(a, "code", None) == getattr(b, "code", None)
    if ta in (int, float) and tb in (int, float) and ta is not bool and tb is not bool:
        if a == b:
            return True
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if ta is str and tb is str:
        return a == b
    if ta is bool and tb is bool:
        return a == b
    if a == 0 and b in (None, ""):
        return True
    return a == b


def libreoffice_recalc(src: Path, out: Path | None, limit: int, cached_book: Any = None) -> dict[str, Any]:
    """Recalculates with LibreOffice and reports its results. out=None (--check) recalculates a temporary copy and
    writes nothing. cached_book (the file as read, for --compare) gives the results the file had stored."""
    import shutil

    import _lo
    from _a1 import col_letter
    from _formula import XLError, strip_prefixes
    from _xlsx import load_book

    produced = _lo.convert(src, "xlsx")
    try:
        if out is not None:
            shutil.move(str(produced), str(out))
            result = out
        else:
            result = produced
        book, pkg, _ = load_book(result)
        pkg.close()
    finally:
        shutil.rmtree(produced.parent, ignore_errors=True)
    errors = []
    counts: dict[str, int] = {}
    total = 0
    diffs: list[dict[str, Any] | None] = []
    checked = 0
    before = {sh.name: sh for sh in cached_book.sheets} if cached_book is not None else {}
    for sh in book.sheets:
        old = before.get(sh.name)
        for (r, c), fc in sorted(sh.formulas.items()):
            total += 1
            v = fc.cached
            if type(v) is XLError:
                counts[v.code] = counts.get(v.code, 0) + 1
                if len(errors) < limit:
                    errors.append({"sheet": sh.name, "cell": f"{col_letter(c)}{r}", "error": v.code, "formula": "=" + strip_prefixes(fc.text.lstrip("="))})
            if cached_book is not None:
                ofc = old.formulas.get((r, c)) if old is not None else None
                if ofc is None or ofc.cached is None:
                    continue
                checked += 1
                if not _same(v, ofc.cached):
                    diffs.append({"cell": f"{sh.name}!{col_letter(c)}{r}", "formula": "=" + strip_prefixes(fc.text.lstrip("=")), "cached": _show(ofc.cached), "computed": _show(v)} if len(diffs) < limit else None)
    what = "re-saved the whole workbook; check formatting and newer Excel features with sheet_render.py." if out is not None else "checked a temporary copy; nothing was written."
    report: dict[str, Any] = {"formulas": total, "error_counts": counts, "errors": errors, "cycles": [], "unsupported": {}, "spills": [], "notes": [f"LibreOffice recalculated every formula and {what}"]}
    if cached_book is not None:
        report["compare"] = {"checked": checked, "differences": len(diffs), "cells": [d for d in diffs if d is not None]}
    return report


def render(r: dict[str, Any], limit: int) -> str:
    lines = []
    head = f"Recalculated {r.get('formulas', 0)} formulas with the {r.get('engine')} engine in {r.get('seconds')}s"
    if not r.get("output"):
        head += " (a check: nothing written)"
    if r.get("output"):
        head += f" → {r['output']}"
    lines.append(head + ".")
    counts = r.get("error_counts") or {}
    if counts:
        lines.append("Errors: " + ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    else:
        lines.append("No formula returns an error.")
    errs = [e for e in r.get("errors", []) if e.get("error")]
    notes = [e for e in r.get("errors", []) if not e.get("error")]
    for e in errs[:limit]:
        why = f" — {e['why']}" if e.get("why") else ""
        lines.append(f"- {e['sheet']}!{e['cell']} {e['error']}: {e['formula']}{why}")
    if len(errs) > limit:
        lines.append(f"- … {len(errs) - limit} more (--max-errors)")
    if r.get("cycles"):
        lines.append("Circular references" + (" (iterative calculation is on; values converged)" if r.get("iterative") else " (Excel shows 0 in these cells):"))
        for cyc in r["cycles"][:10]:
            lines.append("- " + " → ".join(cyc))
    if r.get("unsupported"):
        lines.append("Not evaluated by the built-in engine (cached values kept; try --engine libreoffice):")
        for k, cells in r["unsupported"].items():
            lines.append(f"- {k}: {', '.join(cells[:8])}{' …' if len(cells) > 8 else ''}")
    other = [e for e in notes if "circular" not in (e.get("why") or "")]
    for e in other[:20]:
        lines.append(f"- {e['sheet']}!{e['cell']}: {e['why']}")
    if r.get("spills"):
        lines.append("Dynamic arrays: " + ", ".join(f"{s['sheet']}!{s['cell']} → {s['range']}" for s in r["spills"][:20]))
    if r.get("data_tables"):
        lines.append(f"{r['data_tables']} what-if data table cells keep Excel's cached values.")
    if "compare" in r:
        c = r["compare"]
        lines.append(f"Compared with cached values: {c['checked']} checked, {c['differences']} differ.")
        for d in c["cells"][:limit]:
            lines.append(f"- {d['cell']}: cached {d['cached']!r}, computed {d['computed']!r}  ({d['formula']})")
    for n in r.get("notes", []):
        lines.append(n)
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
