#!/usr/bin/env python3
"""Draw a sheet (or a range) as PNG images the way a spreadsheet window shows it, sized for view_image."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, input_file, output_dir, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/sheet_render.py book.xlsx                          # first visible sheet from A1 -> book_render/<sheet>.png
  python3 scripts/sheet_render.py book.xlsx --sheet Summary --range A1:H40
  python3 scripts/sheet_render.py book.xlsx --all --max-rows 40      # every visible sheet (chart sheets too)
  python3 scripts/sheet_render.py book.xlsx --formulas --range B2:F20   # show formulas instead of results
  python3 scripts/sheet_render.py book.xlsx --engine libreoffice     # print layout (page setup, print area) via LibreOffice
  python3 scripts/sheet_render.py data.csv --out renders/

The built-in renderer draws column letters, row numbers, gridlines, fills, fonts, borders, alignment, wrapping,
number formats, merged cells, column widths and row heights, frozen-pane lines, conditional formats, images and
charts (drawn from their data). Big ranges are split into several images no larger than 1568 px. Formula results
are the ones stored in the file, or computed by the built-in engine when the file stores none.
"""

LEGACY = {"xls", "xlsb", "ods"}


def main() -> int:
    p = parser("Render sheets or ranges to PNG (spreadsheet-window look, or LibreOffice print layout).", EPILOG)
    p.add_argument("file", help=".xlsx/.xlsm/.xltx/.xltm/.xls/.xlsb/.ods/.csv/.tsv")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--sheet", help="sheet name or 1-based number (default: the first visible sheet)")
    g.add_argument("--all", action="store_true", help="every visible sheet")
    p.add_argument("--range", help="cells to draw, e.g. A1:H40 or 'Sheet 2'!B3:F30 (default: the used area from A1)")
    p.add_argument("--max-rows", type=int, default=60, help="rows to draw per sheet (default 60)")
    p.add_argument("--max-cols", type=int, default=30, help="columns to draw per sheet (default 30)")
    p.add_argument("--zoom", type=float, default=1.0, help="scale, 0.5-2 (default 1)")
    p.add_argument("--formulas", action="store_true", help="show formula text instead of results")
    p.add_argument("--no-headers", action="store_true", help="leave out column letters and row numbers")
    p.add_argument("--out", help="folder for the PNGs (default: <name>_render in the current folder)")
    p.add_argument("--engine", choices=["auto", "builtin", "libreoffice"], default="auto", help="auto = built-in window view (legacy .xls/.xlsb/.ods go through LibreOffice first when installed, for their formatting); libreoffice = print layout pages")
    p.add_argument("--max-pages", type=int, default=12, help="with --engine libreoffice: pages to render (default 12)")
    p.add_argument("--no-cache", action="store_true", help="draw again even if this exact file and view were rendered before")
    add_format(p)
    a = p.parse_args()
    if a.max_rows < 1 or a.max_cols < 1:
        raise UsageError("--max-rows and --max-cols must be at least 1")
    if not 0.25 <= a.zoom <= 3:
        raise UsageError("--zoom must be between 0.25 and 3")
    src = input_file(a.file)
    from _book import area_arg, kind_of

    kind = kind_of(src)
    if kind not in ("xlsx", "xls", "xlsb", "ods", "csv", "html"):
        raise SkillError(f"{src.name} is not a spreadsheet this skill can draw (detected: {kind})")
    sheet_from_range, area = area_arg(a.range)
    sheet = a.sheet or sheet_from_range
    out = output_dir(a.out or (Path.cwd() / f"{_safe(src.stem)}_render"))
    t0 = time.time()
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    result = cached_render(src, kind, out, sheet, area, a)
    result["seconds"] = round(time.time() - t0, 1)
    if a.format == "json":
        emit(result, "json")
        return 0
    from _render import announce

    lines = []
    for s in result.get("sheets", []):
        lines.append(f"{s['sheet']}: {s['range']}" + (f" ({len(s['images'])} images)" if len(s["images"]) > 1 else ""))
        for chart in (s.get("charts") or [])[:20]:
            held = [Path(x).name for x in chart["images"]]
            where = held[0] if len(held) == 1 else ("split across " + ", ".join(held) + " (too big for one image)" if held else "not drawn")
            lines.append(f"- chart '{chart['chart']}' at {chart['anchor']}: {where}")
    if lines:
        print("\n".join(lines))
    note = f"engine: {result['engine']}" + (" · " + "; ".join(result["notes"]) if result["notes"] else "")
    announce(result["images"], note)
    return 0


#: bump when the drawing changes, so cached renders are redrawn
RENDER_VERSION = "3"


def cached_render(src: Path, kind: str, out: Path, sheet: str | None, area: Any, a: Any) -> dict[str, Any]:
    """Renders at most once per file content and options (a big sheet's second render is a file copy), then copies
    the PNGs into the output folder."""
    import json

    import _lo
    from _cache import cached_dir, release

    params = {k: getattr(a, k) for k in ("sheet", "all", "range", "max_rows", "max_cols", "zoom", "formulas", "no_headers", "engine", "max_pages")}
    params["lo"] = a.engine != "builtin" and (kind in LEGACY or a.engine == "libreoffice") and _lo.available()

    def build(tmp: Path) -> None:
        r = render_libreoffice(src, kind, tmp, sheet, a) if a.engine == "libreoffice" else render_builtin(src, kind, tmp, sheet, area, a)
        r["images"] = [Path(x).name for x in r["images"]]
        for sh in r.get("sheets", []):
            sh["images"] = [Path(x).name for x in sh["images"]]
            for chart in sh.get("charts") or []:
                chart["images"] = [Path(x).name for x in chart["images"]]
        (tmp / "render.json").write_text(json.dumps(r, default=str), encoding="utf-8")

    d = cached_dir(src, "sheet-render", params, RENDER_VERSION, build)
    try:
        r = json.loads((d / "render.json").read_text(encoding="utf-8"))
        for name in r["images"]:
            shutil.copyfile(d / name, out / name)
    finally:
        release(d)
    r["images"] = [str(out / n) for n in r["images"]]
    for sh in r.get("sheets", []):
        sh["images"] = [str(out / n) for n in sh["images"]]
        for chart in sh.get("charts") or []:
            chart["images"] = [str(out / n) for n in chart["images"]]
    return r


def _safe(name: str) -> str:
    from _book import file_stem

    return file_stem(name)


def _sheet_names(src: Path, kind: str) -> list[str]:
    if kind == "xlsx":
        from _xlsx import Package

        with Package(src) as pkg:
            return [s.name for s in pkg.sheets if s.state in ("visible", "chart") and (s.path or s.state == "chart")]
    from _book import Workbook

    with Workbook(src) as wb:
        return [m.name for m in wb.sheets() if m.visible == "visible" and m.kind == "worksheet"]


def _ods_to_xlsx(src: Path) -> tuple[Path, Path]:
    """An .xlsx copy of an .ods with its formatting, made by the built-in converter (no LibreOffice)."""
    from types import SimpleNamespace

    from sheet_convert import builtin_to_xlsx

    d = Path(tempfile.mkdtemp(prefix="desk-ods-render-"))
    out = d / "view.xlsx"
    builtin_to_xlsx(src, "ods", out, SimpleNamespace(sheet=None, range=None, header="auto"))
    return out, d


def _lo_to_xlsx(src: Path) -> Path:
    import _lo

    return _lo.convert(src, "xlsx", timeout=240)


def render_builtin(src: Path, kind: str, out: Path, sheet: str | None, area: Any, a: Any) -> dict[str, Any]:
    from _grid import chart_pages, draw_chart, draw_page, load_view, paginate
    from _render import VISION_EDGE

    notes: list[str] = []
    engine = "built-in renderer (window view)"
    work = src
    tmp_dir: Path | None = None
    if kind in LEGACY and a.engine == "auto":
        import _lo

        if _lo.available():
            try:
                work = _lo_to_xlsx(src)
                tmp_dir = work.parent
                kind = "xlsx"
                engine = "built-in renderer (window view), after LibreOffice converted the file to .xlsx"
            except SkillError as e:
                notes.append(f"LibreOffice could not convert the file ({e}); values drawn without formatting")
        elif kind == "ods":
            work, tmp_dir = _ods_to_xlsx(src)
            kind = "xlsx"
            engine = "built-in renderer (window view), after the built-in .ods reader carried its formatting over"
            notes.append("LibreOffice is not installed: fonts, fills, borders, number formats, merges and sizes come from the .ods; conditional formats and charts are not drawn")
        else:
            notes.append(f"{src.suffix} formatting needs LibreOffice (not installed): values drawn with default styling")
    try:
        names = _sheet_names(work, kind) if a.all else [sheet]
        from _book import unique_stem

        stems: set[str] = set()
        images: list[str] = []
        sheets: list[dict[str, Any]] = []
        from _a1 import col_letter

        for name in names:
            try:
                v = load_view(work, name, area, a.max_rows, a.max_cols, a.zoom, a.formulas)
            except (SkillError, UsageError):
                raise
            except Exception as e:  # noqa: BLE001 — openpyxl rejects some malformed workbooks (an invalid sheet name…)
                lines = str(e).strip().splitlines()
                if isinstance(e, ValueError) and len(lines) == 1:
                    raise SkillError(lines[0]) from e
                first = lines[0].replace(str(work), work.name) if lines else type(e).__name__
                raise SkillError(f"cannot draw {src.name}: {first} (sheet_read.py still reads its values)") from e
            base = unique_stem(_safe(v.sheet), stems)  # sheets whose names differ only in punctuation or case
            files: list[str] = []
            if not v.rows or not v.cols:
                if v.charts:
                    for i, ch in enumerate(v.charts, 1):
                        img = draw_chart(ch, round(960 * a.zoom), round(600 * a.zoom), a.zoom)
                        path = out / (f"{base}.png" if len(v.charts) == 1 else f"{base}-chart{i}.png")
                        img.save(path)
                        files.append(str(path))
                    sheets.append({"sheet": v.sheet, "range": "chart sheet", "images": files, "notes": v.notes})
                    images += files
                    notes += [f"{v.sheet}: {n}" for n in v.notes]
                    continue
                notes.append(f"{v.sheet}: empty")
                continue
            pages = paginate(v, VISION_EDGE)
            for i, (rows, cols) in enumerate(pages, 1):
                img = draw_page(v, rows, cols, headers=not a.no_headers)
                path = out / (f"{base}.png" if len(pages) == 1 else f"{base}-p{i}.png")
                img.save(path)
                files.append(str(path))
            rng = f"{col_letter(v.cols[0])}{v.rows[0]}:{col_letter(v.cols[-1])}{v.rows[-1]}"
            charts = []
            for k, (ch, held) in enumerate(zip(v.charts, chart_pages(v, pages)), 1):
                r, c = ch["box"][0], ch["box"][1]
                charts.append({"chart": ch.get("title") or f"chart {k}", "anchor": f"{col_letter(c)}{r}", "images": [files[i] for i in held]})
            entry: dict[str, Any] = {"sheet": v.sheet, "range": rng, "images": files, "charts": charts, "notes": v.notes}
            if len(pages) > 1:
                entry["pages"] = [f"{col_letter(c[0])}{r[0]}:{col_letter(c[-1])}{r[-1]}" for r, c in pages]
            sheets.append(entry)
            images += files
            fresh = [n for n in v.notes if not (n.startswith("no cell formatting") and any("default styling" in x for x in notes))]
            notes += [f"{v.sheet}: {n}" for n in fresh] if len(names) > 1 else fresh
        if not images:
            raise SkillError("nothing to draw: the selected sheets are empty")
        import _fonts

        if _fonts.SUBSTITUTED:
            notes.append("fonts not installed, drawn with substitutes: " + ", ".join(f"{k} → {v}" for k, v in sorted(_fonts.SUBSTITUTED.items())))
        return {"input": str(src), "engine": engine, "images": images, "sheets": sheets, "notes": notes}
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def render_libreoffice(src: Path, kind: str, out: Path, sheet: str | None, a: Any) -> dict[str, Any]:
    import _lo
    from _render import pdf_page_count, pdf_to_pngs

    if not _lo.available():
        raise SkillError("LibreOffice is not installed; use --engine builtin (the default)")
    notes: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="desk-sheets-render-"))
    cleanup: list[Path] = [tmp]
    try:
        work = src
        if kind != "xlsx" and (sheet or a.range):
            work = _lo_to_xlsx(src)
            cleanup.append(work.parent)
            kind = "xlsx"
        if kind == "xlsx" and (sheet or a.range or not a.all):
            work = _print_copy(work, tmp / "print.xlsx", sheet, a.range, a.all)
        elif kind == "xlsx":
            work = _print_copy(work, tmp / "print.xlsx", None, None, True)
        pdf = _lo.convert(work, "pdf", timeout=300)
        cleanup.append(pdf.parent)
        count = pdf_page_count(pdf)
        if count == 0:
            raise SkillError("LibreOffice printed no pages (empty sheet or print area)")
        n = min(count, max(1, a.max_pages))
        if n < count:
            notes.append(f"rendered {n} of {count} pages; use --max-pages for more")
        pngs = pdf_to_pngs(pdf, out, pages=f"1-{n}", prefix=_safe(sheet or src.stem))
        return {"input": str(src), "engine": "LibreOffice (print layout)", "page_count": count, "images": [str(x) for x in pngs], "sheets": [], "notes": notes}
    finally:
        for d in cleanup:
            shutil.rmtree(d, ignore_errors=True)


_DEFINED_NAME = re.compile(r"<(?:\w+:)?definedName\b[^<>]*>")
_DEFINED_NAME_END = re.compile(r"</(?:\w+:)?definedName>")


def drop_print_area(xml: str, sheet_index: int) -> str:
    """workbook.xml without the _xlnm.Print_Area names of one sheet (attributes in any order).

    Linear time: the regex it replaces had three [^>]* in one tag and a lazy body, and a crafted workbook.xml of a
    few hundred KB kept it busy for minutes."""
    sheet = f'localSheetId="{sheet_index}"'
    out: list[str] = []
    pos = 0
    while True:
        m = _DEFINED_NAME.search(xml, pos)
        if not m:
            break
        tag = m.group(0)
        if 'name="_xlnm.Print_Area"' in tag and sheet in tag and not tag.endswith("/>"):
            end = _DEFINED_NAME_END.search(xml, m.end())
            if not end:
                break  # never closed: nothing later closes either
            out.append(xml[pos : m.start()])
            pos = end.end()
        else:
            out.append(xml[pos : m.end()])
            pos = m.end()
    out.append(xml[pos:])
    return "".join(out)


def _print_copy(src: Path, dest: Path, sheet: str | None, rng: str | None, all_sheets: bool) -> Path:
    """A copy of the workbook that prints only the chosen sheet (and range). The input is never touched."""
    import zipfile

    from _a1 import col_letter, parse_range, quote_sheet, split_sheet
    from _grid import _sheet_target
    from _xlsx import Package

    with Package(src) as pkg:
        target = None if all_sheets else _sheet_target(pkg, sheet)
        wb_part = pkg.workbook_path
        xml = pkg.read(wb_part).decode("utf-8")
        if target is not None:
            if not target.path and target.state != "chart":
                raise SkillError(f"'{target.name}' is not a worksheet")
            idx = [0]

            def sheet_el(m: re.Match[str]) -> str:
                el = m.group(0)
                i = idx[0]
                idx[0] += 1
                el = re.sub(r'\sstate="[^"]*"', "", el)
                if i != target.index:
                    el = re.sub(r"(?<!\s)\s*/>$", ' state="hidden"/>', el)
                return el

            xml = re.sub(r"<(?:\w+:)?sheet\b[^>]*/>", sheet_el, xml)
            xml = re.sub(r'(<(?:\w+:)?workbookView\b[^>]*?)\sactiveTab="\d+"', r"\1", xml)
            xml = re.sub(r'(<(?:\w+:)?workbookView\b[^>]*?)\sfirstSheet="\d+"', r"\1", xml)
            xml = re.sub(r"<((?:\w+:)?workbookView)\b", lambda m: f'<{m.group(1)} activeTab="{target.index}"', xml, count=1)
            if rng:
                _sh, rest = split_sheet(rng)
                try:
                    r1, c1, r2, c2 = parse_range(rest)
                except ValueError as e:
                    raise UsageError(str(e)) from e
                r2 = min(r2, 1_048_576)
                c2 = min(c2, 16_384)
                ref = f"{quote_sheet(target.name)}!${col_letter(c1)}${r1}:${col_letter(c2)}${r2}"
                ref = ref.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml = drop_print_area(xml, target.index)
                m = re.search(r"<((?:\w+:)?)definedNames>", xml)
                prefix = re.match(r"<(\w+:)?workbook\b", xml.lstrip().split("?>", 1)[-1].lstrip())
                pfx = (prefix.group(1) or "") if prefix else ""
                new = f'<{pfx}definedName name="_xlnm.Print_Area" localSheetId="{target.index}">{ref}</{pfx}definedName>'
                if m:
                    xml = xml[: m.end()] + new + xml[m.end() :]
                else:
                    end = re.search(r"</(?:\w+:)?sheets>", xml)
                    if end is None:
                        raise SkillError("unexpected workbook.xml (no <sheets>)")
                    xml = xml[: end.end()] + f"<{pfx}definedNames>{new}</{pfx}definedNames>" + xml[end.end() :]
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for info in pkg.zip.infolist():
                data = xml.encode("utf-8") if info.filename == wb_part else pkg.zip.read(info.filename)
                z.writestr(info, data)
    return dest


if __name__ == "__main__":
    run_main(main)
