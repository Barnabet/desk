#!/usr/bin/env python3
"""Overview of a spreadsheet: sheets, used ranges, formulas, names, tables, charts and what needs attention."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import add_format, emit, human_size, input_file, md_table, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/sheet_info.py budget.xlsx
  python3 scripts/sheet_info.py legacy.xls --format json
  python3 scripts/sheet_info.py export.csv
"""


INFO_VERSION = "2"


def main() -> int:
    p = parser("Summarise a workbook (.xlsx .xlsm .xltx .xltm .xls .xlsb .ods) or a .csv/.tsv file.", EPILOG)
    p.add_argument("file", help="the spreadsheet")
    p.add_argument("--peek", type=int, default=3, help="rows to preview per sheet (default 3, 0 for none)")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached summary")
    add_format(p)
    a = p.parse_args()
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    path = input_file(a.file)
    from _book import kind_of
    from _cache import cached_json

    kind = kind_of(path)
    info = cached_json(path, "sheet-info", {"peek": a.peek}, INFO_VERSION, lambda: build_info(path, kind, a.peek))
    info["file"] = str(path)
    info["hints"] = hints(info)
    emit(info, a.format, render)
    return 0


def build_info(path: Path, kind: str, peek: int) -> dict[str, Any]:
    from _book import Workbook, text_value

    info: dict[str, Any] = {"file": str(path), "format": kind, "size": path.stat().st_size}
    with Workbook(path) as wb:
        if kind == "xlsx":
            info.update(xlsx_details(path, wb))
        sheets = []
        by_name = {s["name"]: s for s in info.get("sheets", [])}
        for m in wb.sheets():
            entry = by_name.get(m.name, {"name": m.name, "state": m.visible})
            entry.setdefault("state", m.visible)
            if m.kind != "worksheet":
                entry["kind"] = {"chartsheet": "chart", "dialogsheet": "dialog", "macrosheet": "macro"}.get(m.kind, m.kind)
                entry["state"] = m.visible
                sheets.append(entry)
                continue
            if kind == "csv" and path.stat().st_size > (8 << 20):
                from _store import sheet_store

                st = sheet_store(path, None)
                entry["rows"] = st["rows"] + (1 if st.get("header_row") else 0)
                entry["cols"] = len(st["columns"])
                entry["used_range"] = f"A1:{st['columns'][-1]['letter']}{entry['rows']}" if st["columns"] else ""
                if peek:
                    entry["preview"] = ([[c["name"] for c in st["columns"]]] if st.get("header_row") else []) + [r[1:] for r in st["head"][: max(0, peek - 1)]]
                info["csv"] = st.get("csv") or {}
                sheets.append(entry)
                continue
            scan = wb.scan(m) if kind == "xlsx" else None
            if scan is not None and scan.get("max_row"):
                from _scan import extent_ref, is_sparse

                entry["used_range"] = extent_ref(scan)
                entry["rows"] = scan["max_row"] - scan["min_row"] + 1
                entry["cols"] = scan["max_col"] - scan["min_col"] + 1
                if scan.get("dimension") and scan["dimension"] != entry["used_range"] and scan["dimension"].split(":")[-1] != entry["used_range"].split(":")[-1]:
                    entry["stale_dimension"] = scan["dimension"]
                big = scan["cells"] > 200_000
                if is_sparse(scan):
                    g = wb.grid(m.name)
                    from _a1 import col_letter

                    entry["data_block"] = f"{col_letter(g.col1)}{g.row1}:{col_letter(g.col1 + max(g.total_cols, 1) - 1)}{g.row1 + max(g.total_rows, 1) - 1}" if g.rows else ""
                    entry["outlying_cells"] = g.outlier_count
                    entry["outliers"] = [[addr, text_value(v)[:40]] for addr, v in g.outliers[:10]]
                    if peek:
                        entry["preview"] = [[text_value(v) for v in r] for r in g.rows[:peek]]
                elif peek:
                    if big:
                        entry["preview"] = _head_preview(path, m.name, scan, peek)
                    else:
                        g = wb.grid(m.name, max_rows=max(peek, 1) + 1)
                        entry["preview"] = [[text_value(v) for v in r] for r in g.rows[:peek]]
                sheets.append(entry)
                continue
            g = wb.grid(m.name, max_rows=max(peek, 1) + 1)
            entry["used_range"] = _used(g)
            entry["rows"] = g.total_rows
            entry["cols"] = g.total_cols
            if peek:
                entry["preview"] = [[text_value(v) for v in r] for r in g.rows[:peek]]
            sheets.append(entry)
        info["sheets"] = sheets
        if kind == "csv" and "csv" not in info:
            wb.grid()
            info["csv"] = wb.csv_dialect
    return info


def _head_preview(path: Path, sheet: str, scan: dict[str, Any], peek: int) -> list[list[str]]:
    """The first rows of a big sheet from the XML the scan kept (no full parse)."""
    from _book import text_value
    from _xlsx import Package

    try:
        with Package(path) as pkg:
            rows = pkg.parse_rows_xml(scan.get("head_xml") or "", peek)
    except Exception:  # noqa: BLE001 — a preview is optional
        return []
    return [[text_value(v) for v in r] for r in rows]


def _used(g: Any) -> str:
    from _a1 import col_letter

    if not g.rows or not g.total_rows:
        return ""
    return f"{col_letter(g.col1)}{g.row1}:{col_letter(g.col1 + max(g.total_cols, 1) - 1)}{g.row1 + g.total_rows - 1}"


def xlsx_details(path: Path, wb: Any) -> dict[str, Any]:
    from _book import SheetMeta
    from _xlsx import Package

    out: dict[str, Any] = {}
    with Package(path) as pkg:
        names = pkg.names_in_zip
        out["macros"] = any(n.lower().endswith("vbaproject.bin") for n in names)
        out["date_system"] = 1904 if pkg.date1904 else 1900
        calc = pkg.calc
        out["calculation"] = {"mode": calc.get("calcMode", "auto"), "full_calc_on_load": calc.get("fullCalcOnLoad") in ("1", "true"), "iterative": calc.get("iterate") in ("1", "true")}
        out["external_links"] = sum(1 for n in names if n.startswith("xl/externalLinks/externalLink"))
        out["pivot_tables"] = sum(1 for n in names if re.match(r"xl/pivotTables/pivotTable\d+\.xml$", n))
        out["workbook_protected"] = pkg.protection
        names_out = []
        for name, local, formula, hidden in pkg.defined_names:
            if name.startswith("_xlnm._FilterDatabase"):
                continue
            scope = pkg.sheets[local].name if local is not None and local < len(pkg.sheets) else None
            names_out.append({"name": name.replace("_xlnm.", ""), "refers_to": formula, **({"scope": scope} if scope else {}), **({"hidden": True} if hidden else {})})
        out["names"] = names_out
        out["tables"] = [{"name": t.name, "sheet": sheet, "ref": f"{_cell(t.r1, t.c1)}:{_cell(t.r2, t.c2)}", "columns": t.columns, "totals_row": bool(t.totals_rows)} for sheet, t, _ in pkg.tables()]
        sheets = []
        dropped: set[str] = set()
        funcs: dict[str, int] = {}
        total_formulas = 0
        missing_cache = 0
        for si in pkg.sheets:
            entry: dict[str, Any] = {"name": si.name, "state": si.state}
            if not si.path or not pkg.has(si.path):
                entry["kind"] = si.state if si.state in ("chart", "other") else "chart"
                sheets.append(entry)
                continue
            st = wb.scan(SheetMeta(si.name, si.index)) or {}
            feats = st.get("features") or {}
            entry["cells"] = st.get("cells", 0)
            nf = st.get("formulas", 0)
            entry["formulas"] = nf
            total_formulas += nf
            nov = st.get("formulas_without_values", 0)
            missing_cache += nov
            if nov:
                entry["formulas_without_cached_values"] = nov
            if st.get("error_cells"):
                entry["error_cells"] = st["error_cells"]
            for k, v in (st.get("functions") or {}).items():
                funcs[k] = funcs.get(k, 0) + v
            for k in ("merged", "conditional_formats", "data_validations", "hyperlinks"):
                entry[k] = feats.get(k, 0)
            for k in ("frozen_at", "autofilter"):
                if feats.get(k):
                    entry[k] = feats[k]
            if feats.get("protected"):
                entry["protected"] = True
            hidden_rows = st.get("hidden_rows", 0)
            hidden_cols = feats.get("hidden_cols", 0)
            if hidden_rows or hidden_cols:
                entry["hidden"] = {"rows": hidden_rows, "column_groups": hidden_cols}
            if feats.get("sparklines"):
                dropped.add("sparklines")
            if feats.get("x14_validations"):
                dropped.add("extended data validations")
            if feats.get("x14_conditional_formats"):
                dropped.add("extended conditional formats (icon sets, data bars)")
            charts = images = shapes = comments = 0
            for rid, (typ, target) in pkg.rels(si.path).items():
                if typ.endswith("/drawing") and pkg.has(target):
                    d = pkg.read(target)
                    shapes += len(re.findall(rb"<(?:\w+:)?sp\b", d))
                    if b"mc:AlternateContent" in d or b"a14:" in d:
                        dropped.add("form controls")
                    for rid2, (typ2, target2) in pkg.rels(target).items():
                        if typ2.endswith("/chart"):
                            charts += 1
                            if b"chartex" in typ2.encode():
                                dropped.add("modern charts (chartex)")
                        elif typ2.endswith("/image"):
                            images += 1
                        elif typ2.endswith("/chartEx") or "chartEx" in typ2:
                            charts += 1
                            dropped.add("modern charts (waterfall, treemap…)")
                elif typ.endswith("/comments") and pkg.has(target):
                    comments += len(re.findall(rb"<(?:\w+:)?comment\b", pkg.read(target)))
                elif "slicer" in typ.lower():
                    dropped.add("slicers")
                elif "timeline" in typ.lower():
                    dropped.add("timelines")
            for k, v in (("charts", charts), ("images", images), ("shapes", shapes), ("comments", comments)):
                if v:
                    entry[k] = v
            if shapes:
                dropped.add("shapes and text boxes")
            sheets.append(entry)
        out["sheets"] = sheets
        out["formulas"] = total_formulas
        out["formulas_without_cached_values"] = missing_cache
        out["functions"] = dict(sorted(funcs.items(), key=lambda kv: -kv[1])[:40])
        if funcs:
            try:
                import _functions  # noqa: F401
                from _formula import FUNCS

                known_extra = {"LAMBDA"}
                out["unsupported_functions"] = sorted(k for k in funcs if k not in FUNCS and k not in known_extra and not k.startswith("_"))
            except Exception:  # noqa: BLE001
                pass
        if out["pivot_tables"]:
            dropped.add("pivot table refresh (pivot tables are kept but not recomputed)")
        from _xlsx import openpyxl_losses

        dropped |= openpyxl_losses(pkg, skip_sheet_scan=True)
        out["openpyxl_would_drop"] = sorted(dropped)
        core = "docProps/core.xml"
        if pkg.has(core):
            props = {}
            for tag in ("title", "subject", "creator", "lastModifiedBy", "created", "modified"):
                m = re.search(rb"<[\w:]*" + tag.encode() + rb"\b[^>]*>([^<]*)</", pkg.read(core))
                if m and m.group(1).strip():
                    props[tag] = m.group(1).decode("utf-8", "replace")
            if props:
                out["properties"] = props
    return out


def _cell(r: int, c: int) -> str:
    from _a1 import col_letter

    return f"{col_letter(c)}{r}"


def hints(info: dict[str, Any]) -> list[str]:
    out = []
    big = [s["name"] for s in info.get("sheets", []) if (s.get("rows") or 0) > 5000 and not s.get("outlying_cells")]
    if big:
        out.append(f"Big sheet{'s' if len(big) > 1 else ''} ({', '.join(big[:5])}): sheet_read.py prints a map (columns, types, first and last rows); then --find TEXT for addresses, --rows N-M for pages, sheet_query.py for questions over all rows.")
    for s in info.get("sheets", []):
        if s.get("outlying_cells"):
            out.append(f"{s['name']}: the used range {s['used_range']} is mostly empty; the data is {s.get('data_block')} and {s['outlying_cells']} cell(s) sit far away ({', '.join(a for a, _ in s.get('outliers', [])[:5])}). Readers show the data block and list those cells.")
    if info.get("formulas_without_cached_values"):
        out.append(f"{info['formulas_without_cached_values']} formulas have no cached value (the file was written by a library): run sheet_recalc.py to compute and store them.")
    if any(s.get("error_cells") for s in info.get("sheets", [])):
        out.append("Some cells hold error values: sheet_recalc.py --check lists them with their formulas.")
    if info.get("unsupported_functions"):
        out.append("Functions the built-in engine does not evaluate: " + ", ".join(info["unsupported_functions"]) + " (their cached values are kept; --engine libreoffice recalculates everything).")
    if info.get("openpyxl_would_drop"):
        out.append("Editing with sheet_edit.py keeps cells, styles, charts, images, comments, validations and formats, but not: " + ", ".join(info["openpyxl_would_drop"]) + ". Use sheet_edit.py --mode patch for value/formula changes that must keep everything.")
    if info.get("macros"):
        out.append("The workbook contains VBA macros; they are kept when you edit it as .xlsm, never run.")
    if info.get("external_links"):
        out.append("The workbook links to other files; those references keep their cached values.")
    if info.get("format") in ("xls", "xlsb", "ods"):
        out.append("To edit or recalculate, convert to .xlsx first with sheet_convert.py (with LibreOffice installed formulas and formatting are kept).")
    return out


def render(info: dict[str, Any]) -> str:
    from _book import text_value  # noqa: F401

    lines = [f"# {Path(info['file']).name}", ""]
    fmt = {"xlsx": "Excel workbook (OOXML)", "xls": "Excel 97-2003 workbook", "xlsb": "Excel binary workbook", "ods": "OpenDocument spreadsheet", "csv": "delimited text", "json": "JSON records", "html": "HTML tables (a web export saved as a spreadsheet)"}.get(info["format"], info["format"])
    lines.append(f"- Format: {fmt}, {human_size(info['size'])}" + ("; contains VBA macros" if info.get("macros") else ""))
    if info.get("csv"):
        d = info["csv"]
        lines.append(f"- Delimiter: {'TAB' if d.get('delimiter') == chr(9) else repr(d.get('delimiter'))}; encoding {d.get('encoding')}")
    if info["format"] == "xlsx":
        calc = info.get("calculation", {})
        lines.append(f"- Formulas: {info.get('formulas', 0)}" + (f" ({info['formulas_without_cached_values']} without cached values)" if info.get("formulas_without_cached_values") else "") + f"; date system {info.get('date_system')}; calculation {calc.get('mode')}" + ("; iterative" if calc.get("iterative") else ""))
        extras = []
        for k, label in (("names", "defined names"), ("tables", "tables")):
            if info.get(k):
                extras.append(f"{len(info[k])} {label}")
        for k, label in (("pivot_tables", "pivot tables"), ("external_links", "external links")):
            if info.get(k):
                extras.append(f"{info[k]} {label}")
        if extras:
            lines.append("- " + ", ".join(extras))
        if info.get("properties"):
            lines.append("- Properties: " + "; ".join(f"{k} {v}" for k, v in info["properties"].items()))
    lines.append("")
    rows = []
    for i, s in enumerate(info["sheets"], 1):
        notes = []
        for k in ("merged", "conditional_formats", "data_validations", "hyperlinks", "charts", "images", "shapes", "comments", "error_cells"):
            if s.get(k):
                notes.append(f"{s[k]} {k.replace('_', ' ')}")
        if s.get("frozen_at"):
            notes.append(f"frozen at {s['frozen_at']}")
        if s.get("autofilter"):
            notes.append(f"filter {s['autofilter']}")
        if s.get("protected"):
            notes.append("protected")
        if s.get("hidden"):
            notes.append(f"hidden rows {s['hidden']['rows']}, col groups {s['hidden']['column_groups']}")
        if s.get("kind") and s.get("kind") != "worksheet":
            notes.append(f"{s['kind']} sheet")
        if s.get("outlying_cells"):
            notes.append(f"data {s.get('data_block')} + {s['outlying_cells']} far cell(s)")
        if s.get("stale_dimension"):
            notes.append(f"stored dimension {s['stale_dimension']} is stale")
        rows.append([i, s["name"], s.get("state", "visible"), s.get("used_range", ""), f"{s['rows']:,}" if isinstance(s.get("rows"), int) else "", s.get("cols", ""), s.get("formulas", ""), "; ".join(notes)])
    lines.append(md_table(["#", "Sheet", "State", "Used range", "Rows", "Cols", "Formulas", "Notes"], rows))
    for s in info["sheets"]:
        if s.get("preview"):
            lines.append("")
            lines.append(f"**{s['name']}** first rows: " + " / ".join(" · ".join(v for v in r if v)[:160] for r in s["preview"] if any(r)))
    if info.get("names"):
        lines.append("")
        lines.append("Names: " + "; ".join(f"{n['name']} = {n['refers_to']}" + (f" ({n['scope']})" if n.get("scope") else "") for n in info["names"][:40]))
    if info.get("tables"):
        lines.append("")
        lines.append("Tables: " + "; ".join(f"{t['name']} ({t['sheet']}!{t['ref']}: {', '.join(t['columns'][:8])}{'…' if len(t['columns']) > 8 else ''})" for t in info["tables"]))
    if info.get("functions"):
        lines.append("")
        lines.append("Functions used: " + ", ".join(f"{k}×{v}" for k, v in info["functions"].items()))
    if info.get("hints"):
        lines.append("")
        lines.extend(f"- {h}" for h in info["hints"])
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
