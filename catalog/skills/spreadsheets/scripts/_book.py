"""Loading any spreadsheet-like file as grids of typed values, and formatting values for output.

xlsx/xlsm/xltx/xltm/xls/xlsb/ods are read with python-calamine (fast, typed, dates decoded); error cells in the
xlsx family are recovered from the sheet XML (calamine blanks them). CSV/TSV are sniffed (delimiter, encoding).

Every zip-based input passes _common.check_zip first (zip bombs), encrypted Office files and random bytes are
refused with a clear message, and .xlsx sheets whose cells are few but far apart (a stray value at XFD1048576) are
read cell by cell instead of as a dense rectangle (see _scan.py).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import math
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from _a1 import MAX_COL, MAX_ROW, col_index, col_letter, parse_range, split_sheet
from _common import SkillError, UsageError, check_zip, md_escape_cell
from _numfmt import general

XLSX_EXTS = {".xlsx", ".xlsm", ".xltx", ".xltm"}
CALAMINE_EXTS = XLSX_EXTS | {".xls", ".xlsb", ".xla", ".xlam", ".ods", ".fods"}
TEXT_EXTS = {".csv", ".tsv", ".tab", ".txt"}
ALL_EXTS = CALAMINE_EXTS | TEXT_EXTS | {".json", ".jsonl", ".ndjson"}


_ZIP_CHECKED: set[str] = set()


def guard_zip(path: Path) -> None:
    """check_zip once per file and process: refuses zip bombs before any parser inflates a part."""
    try:
        key = str(Path(path).resolve())
    except OSError:
        key = str(path)
    if key in _ZIP_CHECKED:
        return
    check_zip(path)
    _refuse_dtd(Path(path))
    _ZIP_CHECKED.add(key)


def _refuse_dtd(path: Path) -> None:
    """Office and OpenDocument XML never declares a DOCTYPE; one with entities is a billion-laughs or external-entity
    attack. The declaration must precede the root element, so the first KB of each XML part is enough."""
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist()[:5000]:
                if not info.filename.lower().endswith((".xml", ".rels", ".vml")) or info.file_size == 0:
                    continue
                with z.open(info) as fh:
                    head = fh.read(1024)
                if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
                    raise SkillError(f"{path.name}: part {info.filename} declares a DOCTYPE with entities, which Office files never do "
                                     "(the pattern of billion-laughs and external-entity attacks): refusing to parse it")
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
        return  # damaged or encrypted zips are reported by the readers


def _ole_has(path: Path, streams: tuple[str, ...]) -> bool:
    """True when an OLE2 file's directory names one of these streams (UTF-16 names; the first 64 MB are searched)."""
    want = [n.encode("utf-16-le") + b"\0\0" for n in streams]
    try:
        with open(path, "rb") as f:
            tail = b""
            for _ in range(64):
                block = f.read(1 << 20)
                if not block:
                    break
                buf = tail + block
                if any(w in buf for w in want):
                    return True
                tail = buf[-80:]
    except OSError:
        return False
    return False


def ole_encrypted(path: Path) -> bool:
    """True for an encrypted Office file: a CFB container holding EncryptionInfo and EncryptedPackage streams."""
    want = ("EncryptedPackage".encode("utf-16-le"), "EncryptionInfo".encode("utf-16-le"))
    found = [False, False]
    try:
        with open(path, "rb") as f:
            tail = b""
            read = 0
            while read < 256 << 20:
                block = f.read(1 << 20)
                if not block:
                    break
                read += len(block)
                buf = tail + block
                for i, w in enumerate(want):
                    if not found[i] and w in buf:
                        found[i] = True
                if all(found):
                    return True
                tail = buf[-64:]
    except OSError:
        return False
    return False


def _looks_binary(head: bytes) -> bool:
    """Random or binary bytes (not text in any usual encoding)."""
    if not head:
        return False
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False
    if b"\x00" in head:
        return True
    ctrl = sum(1 for b in head if b < 9 or 13 < b < 32)
    return ctrl > max(2, len(head) // 100)


_EXTS_OF = {"xlsx": (".xlsx", ".xlsm", ".xltx", ".xltm", ".xlam"), "xls": (".xls", ".xlt", ".xla"), "xlsb": (".xlsb",), "ods": (".ods", ".ots")}


def kind_of(path: Path) -> str:
    """'xlsx', 'xls', 'xlsb', 'ods', 'csv', 'json', 'html', 'fods', 'xml2003' — by content first, then extension.

    Refuses zip bombs (check_zip), encrypted Office files, and files whose content is no spreadsheet at all."""
    ext = path.suffix.lower()
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError as e:
        raise SkillError(f"cannot read {path}: {e}") from e
    if head.startswith(b"PK"):
        guard_zip(path)
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "mimetype" in names and b"opendocument.spreadsheet" in z.read("mimetype"):
                    return "ods"
                if any(n.startswith("xl/") for n in names):
                    if "xl/workbook.bin" in names:
                        return "xlsb"
                    return "xlsx"
                if "mimetype" in names:
                    mt = z.read("mimetype").decode("ascii", "replace")
                    raise SkillError(f"{path.name} is an OpenDocument file of type {mt}, not a spreadsheet")
                if "word/document.xml" in names:
                    raise SkillError(f"{path.name} is a Word document, not a spreadsheet (use the word-documents skill)")
                if "ppt/presentation.xml" in names:
                    raise SkillError(f"{path.name} is a PowerPoint deck, not a spreadsheet (use the presentations skill)")
        except zipfile.BadZipFile:
            pass
        return "xlsx" if ext in XLSX_EXTS else "zip"
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        if ole_encrypted(path):
            raise SkillError(f"{path.name} is password-protected (an encrypted Office file). Ask the user for an unprotected copy, or to open it in Excel or LibreOffice and save it without a password")
        if not _ole_has(path, ("Workbook", "Book")):
            what = "a Word 97-2003 document" if _ole_has(path, ("WordDocument",)) else "a PowerPoint 97-2003 deck" if _ole_has(path, ("PowerPoint Document",)) else "damaged, or another kind of OLE2 file"
            raise SkillError(f"{path.name} is an OLE2 (Office 97-2003) container without a workbook stream: {what}, not a spreadsheet (file-inspector can identify it)")
        return "xls"
    if ext == ".fods":
        return "fods"
    if ext in (".json", ".jsonl", ".ndjson"):
        return "json"
    if not head:
        if ext in (".csv", ".tsv", ".tab", ".txt"):
            return "csv"
        raise SkillError(f"{path.name} is empty (0 bytes): it is not a workbook")
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if ext in (".html", ".htm") or ext in (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb", ".ods"):
        # an Excel extension on a text file: usually an HTML table or CSV exported by a web app
        if start.startswith((b"<!doctype html", b"<html", b"<table", b"<?xml")) or b"<table" in start or b"<html" in start:
            if b"urn:schemas-microsoft-com:office:spreadsheet" in start or b"<workbook" in start:
                return "xml2003"
            return "html"
        if _looks_binary(head):
            raise SkillError(f"{path.name} is not a spreadsheet: its content is neither a zip-based workbook (xlsx, ods), a legacy Excel file nor text. It may be damaged; file-inspector can identify it")
        return "csv"
    if _looks_binary(head):
        raise SkillError(f"{path.name} looks binary, not delimited text; file-inspector can identify it")
    return "csv"


# ── values ──────────────────────────────────────────────────────────────


def norm(v: Any) -> Any:
    """Numbers come back from calamine as floats: integral ones become ints."""
    if type(v) is float and v.is_integer() and abs(v) < 2**53:
        return int(v)
    if v == "":
        return None
    return v


def text_value(v: Any) -> str:
    """A value as plain text for Markdown/CSV output (dates ISO, numbers without float noise)."""
    if v is None:
        return ""
    t = type(v)
    if t is bool:
        return "TRUE" if v else "FALSE"
    if t is int:
        return str(v)
    if t is float or t is Percent or t is Currency:
        if math.isnan(v) or math.isinf(v):
            return "#NUM!"
        if t is Percent:
            return general(v * 100, None) + "%"
        if t is Currency:
            sym = getattr(v, "symbol", "$")
            return ("-" if v < 0 else "") + sym + f"{abs(v):,.2f}"
        return general(v, None)
    if t is _dt.datetime:
        if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
            return v.date().isoformat()
        return v.isoformat(sep=" ", timespec="seconds" if not v.microsecond else "milliseconds")
    if t is _dt.date:
        return v.isoformat()
    if t is _dt.time:
        return v.isoformat(timespec="seconds")
    if t is _dt.timedelta:
        total = v.total_seconds()
        h, rem = divmod(int(total), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
    if hasattr(v, "code"):
        return v.code
    return str(v)


def json_value(v: Any) -> Any:
    t = type(v)
    if v is None or t in (bool, int, str):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "#NUM!"
        return float(general(v, None)) if abs(v) < 1e300 else v
    return text_value(v)


# ── grids ───────────────────────────────────────────────────────────────


class Grid:
    """A rectangular block of a sheet: rows of values starting at (row1, col1), 1-based."""

    def __init__(self, sheet: str, rows: list[list[Any]], row1: int = 1, col1: int = 1, total_rows: int | None = None, total_cols: int | None = None) -> None:
        self.sheet = sheet
        self.rows = rows
        self.row1 = row1
        self.col1 = col1
        self.total_rows = total_rows if total_rows is not None else len(rows)
        self.total_cols = total_cols if total_cols is not None else (max((len(r) for r in rows), default=0))
        self.merged: list[str] = []
        self.outliers: list[tuple[str, Any]] = []  # cells far outside the main block (sparse sheets)
        self.outlier_count = 0
        self.extent = ""  # the full used range when it differs from the main block

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def ref(self) -> str:
        if not self.rows or not self.width:
            return ""
        return f"{col_letter(self.col1)}{self.row1}:{col_letter(self.col1 + self.width - 1)}{self.row1 + len(self.rows) - 1}"


class SheetMeta:
    def __init__(self, name: str, index: int, visible: str = "visible", kind: str = "worksheet") -> None:
        self.name, self.index, self.visible, self.kind = name, index, visible, kind


class Workbook:
    """Read access to any supported file: sheet list and grids."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.kind = kind_of(self.path)
        self._cal: Any = None
        self._csv_rows: list[list[Any]] | None = None
        self.csv_dialect: dict[str, Any] = {}
        self._parts: dict[str, str] | None = None
        self._scans: dict[str, dict[str, Any] | None] = {}
        if self.kind in ("xlsx", "xls", "xlsb", "ods"):
            try:
                import python_calamine as pc
            except ImportError as e:  # pragma: no cover
                raise SkillError("python-calamine is missing from this skill's runtime") from e
            try:
                if self.path.suffix.lower() in _EXTS_OF.get(self.kind, ()):
                    self._cal = pc.CalamineWorkbook.from_path(str(self.path))
                else:  # content and extension disagree (an .xls saved as .xlsx): calamine goes by the extension
                    with open(self.path, "rb") as fh:
                        self._cal = pc.CalamineWorkbook.from_filelike(fh)
            except Exception as e:  # noqa: BLE001 — calamine raises several error types
                msg = str(e)
                if "password" in msg.lower() or "encrypt" in msg.lower() or type(e).__name__ == "PasswordError":
                    raise SkillError(f"{self.path.name} is password-protected; it must be decrypted first (LibreOffice or Excel)") from e
                raise SkillError(f"cannot read {self.path.name}: {msg}") from e
        elif self.kind == "zip":
            raise SkillError(f"{self.path.name} is a zip archive, not a spreadsheet")
        elif self.kind == "parquet":
            raise SkillError(f"{self.path.name} is a Parquet file: query it with sheet_query.py (or the data-files skill)")
        elif self.kind == "fods":
            raise SkillError(f"{self.path.name} is a flat OpenDocument spreadsheet: convert it with LibreOffice first (sheet_convert.py {self.path.name} --out {self.path.stem}.xlsx)")
        elif self.kind == "xml2003":
            raise SkillError(f"{self.path.name} is an Excel 2003 XML spreadsheet: convert it with LibreOffice first (sheet_convert.py {self.path.name} --out {self.path.stem}.xlsx)")
        elif self.kind == "html":
            self._html = read_html_tables(self.path)
            if not self._html:
                raise SkillError(f"{self.path.name} is HTML without a table")

    # ── xlsx sheet statistics (streaming scan, cached) ──────────────────

    def sheet_part(self, meta: SheetMeta) -> str | None:
        """The zip part of an .xlsx worksheet."""
        if self.kind != "xlsx":
            return None
        if self._parts is None:
            from _xlsx import Package

            try:
                with Package(self.path) as pkg:
                    self._parts = {si.name: si.path for si in pkg.sheets}
            except Exception:  # noqa: BLE001 — calamine read it; the scan is an optimisation
                self._parts = {}
        return self._parts.get(meta.name) or None

    def scan(self, meta: SheetMeta) -> dict[str, Any] | None:
        """_scan.scan_part for an .xlsx worksheet (cached by file content), or None."""
        part = self.sheet_part(meta)
        if not part:
            return None
        if part in self._scans:
            return self._scans[part]
        from _scan import scan_cached

        try:
            st = scan_cached(self.path, part)
        except (zipfile.BadZipFile, OSError, ValueError):
            st = {}
        self._scans[part] = st or None
        return self._scans[part]

    def close(self) -> None:
        if self._cal is not None:
            try:
                self._cal.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "Workbook":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def sheets(self) -> list[SheetMeta]:
        if self._cal is not None:
            out = []
            for i, m in enumerate(self._cal.sheets_metadata):
                vis = str(m.visible).rsplit(".", 1)[-1].lower()
                vis = {"visible": "visible", "hidden": "hidden", "veryhidden": "very hidden"}.get(vis, vis)
                kind = str(m.typ).rsplit(".", 1)[-1].lower()
                out.append(SheetMeta(m.name, i, vis, kind))
            return out
        if self.kind == "html":
            return [SheetMeta(name, i) for i, (name, _rows, _merged) in enumerate(self._html)]
        return [SheetMeta(self.path.stem, 0)]

    def resolve_sheet(self, sheet: str | int | None) -> SheetMeta:
        metas = self.sheets()
        if not metas:
            raise SkillError(f"{self.path.name} has no sheets")
        if sheet is None or sheet == "":
            for m in metas:
                if m.visible == "visible" and m.kind == "worksheet":
                    return m
            return metas[0]
        if isinstance(sheet, int) or (isinstance(sheet, str) and sheet.isdigit() and not any(m.name == sheet for m in metas)):
            i = int(sheet) - 1
            if 0 <= i < len(metas):
                return metas[i]
            raise SkillError(f"sheet number {sheet} is out of range (1-{len(metas)})")
        for m in metas:
            if m.name == sheet:
                return m
        for m in metas:
            if m.name.lower() == str(sheet).lower():
                return m
        raise SkillError(f"no sheet named '{sheet}'; sheets: {', '.join(m.name for m in metas)}")

    def grid(self, sheet: str | int | None = None, area: tuple[int, int, int, int] | None = None, max_rows: int | None = None) -> Grid:
        """Values of a sheet (or an area of it), trimmed to the used range."""
        if self.kind in ("csv",):
            rows = self._csv()
            name = self.path.stem
            return _slice(Grid(name, rows, 1, 1), area, max_rows)
        if self.kind == "json":
            rows = json_rows(self.path)
            return _slice(Grid(self.path.stem, rows, 1, 1), area, max_rows)
        meta = self.resolve_sheet(sheet)
        if self.kind == "html":
            name, rows, merged = self._html[meta.index]
            g = Grid(name, rows, 1, 1)
            g.merged = list(merged)
            return _slice(g, area, max_rows)
        if meta.kind != "worksheet":
            return Grid(meta.name, [], 1, 1)
        st = self.scan(meta) if self.kind == "xlsx" else None
        if st:
            from _scan import is_sparse

            if is_sparse(st):
                return self._sparse_grid(meta, st, area, max_rows)
            if area is None and max_rows is not None and (st.get("bytes") or 0) > area_xml_bytes() and st.get("max_row"):
                # the first rows of a huge sheet: streamed from its XML, not loaded whole
                r0, c0 = st.get("min_row") or 1, st.get("min_col") or 1
                box = (r0, c0, min(st["max_row"], r0 + max_rows - 1), st.get("max_col") or c0)
                if (box[2] - box[0] + 1) * (box[3] - box[1] + 1) <= 200_000:
                    return self._area_grid(meta, st, box)
            if area is not None and (st.get("bytes") or 0) > area_xml_bytes() and st.get("max_row"):
                a1, b1, a2, b2 = area
                a2 = min(a2, st["max_row"]) if a2 >= MAX_ROW else a2
                b2 = min(b2, st.get("max_col") or b2) if b2 >= MAX_COL else b2
                if max_rows is not None:
                    a2 = min(a2, a1 + max_rows - 1)
                if a2 >= a1 and b2 >= b1 and (a2 - a1 + 1) * (b2 - b1 + 1) <= 200_000:
                    return self._area_grid(meta, st, (a1, b1, a2, b2))
        try:
            sh = self._cal.get_sheet_by_name(meta.name)
            start = sh.start
            if start is not None:
                sh.to_python(skip_empty_area=True, nrows=1)
        except Exception as e:  # noqa: BLE001 — calamine rejects some newer error values (#SPILL!, #CALC!…)
            if self.kind == "xlsx":
                return _slice(_grid_from_xml(self.path, meta), area, max_rows)
            raise SkillError(f"cannot read sheet '{meta.name}': {e}") from e
        if start is None:
            g = Grid(meta.name, [], 1, 1, 0, 0)
            return g
        r0, c0 = start[0] + 1, start[1] + 1
        want_rows = None
        if area is not None:
            want_rows = area[2] - r0 + 1 if area[2] < MAX_ROW else None
        if max_rows is not None:
            limit = (area[0] - r0 + max_rows) if area is not None else max_rows
            want_rows = min(want_rows, limit) if want_rows is not None else limit
        try:
            data = sh.to_python(skip_empty_area=True, nrows=max(0, want_rows) if want_rows is not None else None)
        except Exception as e:  # noqa: BLE001
            if self.kind == "xlsx":
                return _slice(_grid_from_xml(self.path, meta), area, max_rows)
            raise SkillError(f"cannot read sheet '{meta.name}': {e}") from e
        rows = [[norm(v) for v in r] for r in data]
        g = Grid(meta.name, rows, r0, c0, total_rows=sh.height, total_cols=sh.width)
        if self.kind == "xlsx":
            if st is not None:
                for r, c, code in st.get("errors") or []:
                    i, j = r - g.row1, c - g.col1
                    if 0 <= i < len(g.rows) and 0 <= j < len(g.rows[i]):
                        g.rows[i][j] = ErrorText(code)
            else:
                _restore_errors(self.path, meta, g)
        try:
            g.merged = [f"{col_letter(a[1] + 1)}{a[0] + 1}:{col_letter(b[1] + 1)}{b[0] + 1}" for a, b in (sh.merged_cell_ranges or [])]
        except Exception:  # noqa: BLE001 — not available for every format
            g.merged = []
        total_rows, total_cols = g.total_rows, g.total_cols
        out = _slice(g, area, max_rows)
        out.total_rows, out.total_cols = total_rows, total_cols
        return out

    def _area_grid(self, meta: SheetMeta, st: dict[str, Any], box: tuple[int, int, int, int]) -> Grid:
        """A small area of a huge sheet, read from the sheet XML by binary search over its rows (no full load)."""
        from _xlsx import Package, area_values

        a1, b1, a2, b2 = box
        with Package(self.path) as pkg:
            cells = area_values(pkg, pkg.sheets[meta.index], a1, a2, b1, b2)
        rows = [[_area_cell(cells.get((r, c))) for c in range(b1, b2 + 1)] for r in range(a1, a2 + 1)]
        g = Grid(meta.name, rows, a1, b1, (st.get("max_row") or a2) - (st.get("min_row") or 1) + 1, (st.get("max_col") or b2) - (st.get("min_col") or 1) + 1)
        g.merged = _merged_in((st.get("features") or {}).get("merges") or [], box)
        return g

    def _sparse_grid(self, meta: SheetMeta, st: dict[str, Any], area: tuple[int, int, int, int] | None, max_rows: int | None) -> Grid:
        """A sheet whose few cells are far apart: read cell by cell, keep the main block, list the outliers."""
        from _scan import blocks, extent_ref

        full = _grid_from_xml(self.path, meta, sparse=True)
        cells: dict[tuple[int, int], Any] = full.cells  # type: ignore[attr-defined]
        if area is not None:
            a1, b1, a2, b2 = area
            a2 = min(a2, st.get("max_row") or a2)
            b2 = min(b2, st.get("max_col") or b2)
            if max_rows is not None:
                a2 = min(a2, a1 + max_rows - 1)
            if (a2 - a1 + 1) * (b2 - b1 + 1) > 5_000_000:
                raise SkillError(f"{col_letter(b1)}{a1}:{col_letter(b2)}{a2} is too large to read at once; narrow --range")
            rows = [[cells.get((r, c)) for c in range(b1, b2 + 1)] for r in range(a1, a2 + 1)]
            g = Grid(meta.name, rows, a1, b1, a2 - a1 + 1, b2 - b1 + 1)
            g.merged = _merged_in(full.merged, (a1, b1, a2, b2))
            return g
        (r1, c1, r2, c2), outside = blocks(cells)
        last = r2 if max_rows is None else min(r2, r1 + max_rows - 1)
        rows = [[cells.get((r, c)) for c in range(c1, c2 + 1)] for r in range(r1, last + 1)]
        g = Grid(meta.name, rows, r1, c1, r2 - r1 + 1, c2 - c1 + 1)
        g.merged = _merged_in(full.merged, (r1, c1, r2, c2))
        g.outlier_count = len(outside)
        g.outliers = [(f"{col_letter(c)}{r}", cells[(r, c)]) for r, c in outside[:50]]
        g.extent = extent_ref(st)
        return g

    def _csv(self) -> list[list[Any]]:
        if self._csv_rows is None:
            rows, dialect = read_csv(self.path)
            self._csv_rows = rows
            self.csv_dialect = dialect
        return self._csv_rows


def read_html_tables(path: Path) -> list[tuple[str, list[list[Any]], list[str]]]:
    """Every <table> of an HTML file (web exports saved as .xls, saved pages): [(name, rows, merged ranges)].
    Cell text is typed like CSV; colspan/rowspan become merged ranges."""
    from html.parser import HTMLParser

    text, _enc = decode_text(path.read_bytes())

    class Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.tables: list[tuple[str, list[list[Any]], list[str]]] = []
            self.stack: list[dict[str, Any]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            a = dict(attrs)
            if tag == "table":
                self.stack.append({"rows": [], "row": None, "cell": None, "pending": {}, "caption": None, "merged": [], "in_caption": False})
                return
            if not self.stack:
                return
            t = self.stack[-1]
            if tag == "caption":
                t["in_caption"] = True
                t["caption"] = ""
            elif tag == "tr":
                t["row"] = []
            elif tag in ("td", "th"):
                if t["row"] is None:
                    t["row"] = []
                t["cell"] = []
                try:
                    t["span"] = (max(1, int(a.get("rowspan") or 1)), max(1, int(a.get("colspan") or 1)))
                except ValueError:
                    t["span"] = (1, 1)
            elif tag == "br" and t["cell"] is not None:
                t["cell"].append("\n")

        def handle_data(self, data: str) -> None:
            if not self.stack:
                return
            t = self.stack[-1]
            if t["cell"] is not None:
                t["cell"].append(data)
            elif t["in_caption"]:
                t["caption"] += data

        def _place_pending(self, t: dict[str, Any]) -> None:
            row = t["row"]
            while len(row) in t["pending"]:
                left = t["pending"].pop(len(row))
                row.append(None)
                if left > 1:
                    t["pending"][len(row) - 1] = left - 1

        def handle_endtag(self, tag: str) -> None:
            if not self.stack:
                return
            t = self.stack[-1]
            if tag == "caption":
                t["in_caption"] = False
            elif tag in ("td", "th") and t["cell"] is not None:
                raw = "".join(t["cell"])
                val = "\n".join(" ".join(part.split()) for part in raw.split("\n")).strip()
                t["cell"] = None
                self._place_pending(t)
                rs, cs = t.get("span", (1, 1))
                r = len(t["rows"]) + 1
                c = len(t["row"]) + 1
                t["row"].append(infer_value(val) if val else None)
                for k in range(1, cs):
                    t["row"].append(None)
                if rs > 1:
                    for k in range(cs):
                        t["pending"][c - 1 + k] = rs - 1
                if rs > 1 or cs > 1:
                    t["merged"].append(f"{col_letter(c)}{r}:{col_letter(c + cs - 1)}{r + rs - 1}")
            elif tag == "tr" and t["row"] is not None:
                self._place_pending(t)
                t["rows"].append(t["row"])
                t["row"] = None
            elif tag == "table":
                self.stack.pop()
                if t["row"]:
                    t["rows"].append(t["row"])
                rows = [r for r in t["rows"] if any(v is not None for v in r)]
                if rows:
                    name = " ".join((t["caption"] or "").split())[:31] or f"Table{len(self.tables) + 1}"
                    self.tables.append((name, rows, t["merged"]))

    parser = Parser()
    parser.feed(text)
    parser.close()
    used: set[str] = set()
    out = []
    for name, rows, merged in parser.tables:
        base, k = name, 2
        while name.lower() in used:
            name = f"{base[:28]} {k}"
            k += 1
        used.add(name.lower())
        out.append((name, rows, merged))
    return out


def _grid_from_xml(path: Path, meta: SheetMeta, sparse: bool = False) -> Grid:
    """Reads a worksheet with the standard-library XML reader (used when calamine cannot, and for sparse sheets:
    with sparse=True the grid is empty and .cells holds {(row, col): value})."""
    from _numfmt import date_kind, serial_to_python
    from _xlsx import Package

    cells: dict[tuple[int, int], Any] = {}
    merged: list[str] = []
    with Package(path) as pkg:
        si = pkg.sheets[meta.index]
        for r, c, v, _f, style in pkg.iter_cells(si, want_formulas=False):
            if v is None:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) and style:
                kind = date_kind(pkg.number_format(style))
                if kind:
                    try:
                        v = serial_to_python(v, kind, pkg.date1904)
                    except (ValueError, OverflowError):
                        pass
            if hasattr(v, "code"):
                v = ErrorText(v.code)
            cells[(r, c)] = v
        if si.path:
            merged = [m.decode() for m in re.findall(rb'<(?:\w+:)?mergeCell\b[^>]*\bref="([^"]+)"', pkg.read(si.path))]
    if sparse:
        g = Grid(meta.name, [], 1, 1, 0, 0)
        g.cells = cells  # type: ignore[attr-defined]
        g.merged = merged
        return g
    if not cells:
        return Grid(meta.name, [], 1, 1, 0, 0)
    r1 = min(k[0] for k in cells)
    c1 = min(k[1] for k in cells)
    r2 = max(k[0] for k in cells)
    c2 = max(k[1] for k in cells)
    rows = [[cells.get((r, c)) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
    g = Grid(meta.name, rows, r1, c1, r2 - r1 + 1, c2 - c1 + 1)
    g.merged = merged
    return g


def area_xml_bytes() -> int:
    """Sheet parts bigger than this (uncompressed; DESK_AREA_XML_MB, default 16) serve --range reads straight from
    their XML instead of loading the whole sheet."""
    try:
        return int(float(os.environ.get("DESK_AREA_XML_MB", "16")) * (1 << 20))
    except ValueError:
        return 16 << 20


def _area_cell(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "code"):
        return ErrorText(v.code)
    return norm(v)


def _merged_in(merged: list[str], box: tuple[int, int, int, int]) -> list[str]:
    a1, b1, a2, b2 = box
    keep = []
    for m in merged:
        try:
            x1, y1, x2, y2 = parse_range(m)
        except ValueError:
            continue
        if not (x2 < a1 or x1 > a2 or y2 < b1 or y1 > b2):
            keep.append(m)
    return keep


def _slice(g: Grid, area: tuple[int, int, int, int] | None, max_rows: int | None) -> Grid:
    rows = g.rows
    r1, c1 = g.row1, g.col1
    if area is not None:
        a1, b1, a2, b2 = area
        a2 = min(a2, r1 + len(rows) - 1) if a2 >= MAX_ROW else a2
        b2 = min(b2, c1 + g.width - 1) if b2 >= MAX_COL else b2
        out = []
        for r in range(a1, a2 + 1):
            i = r - r1
            src = rows[i] if 0 <= i < len(rows) else []
            out.append([src[c - c1] if 0 <= c - c1 < len(src) else None for c in range(b1, b2 + 1)])
        rows, r1, c1 = out, a1, b1
    if max_rows is not None and len(rows) > max_rows:
        rows = rows[:max_rows]
    ng = Grid(g.sheet, rows, r1, c1, g.total_rows, g.total_cols)
    if area is None:
        ng.merged = g.merged
    else:
        a1, b1, a2, b2 = area
        keep = []
        for m in g.merged:
            x1, y1, x2, y2 = parse_range(m)
            if not (x2 < a1 or x1 > a2 or y2 < b1 or y1 > b2):
                keep.append(m)
        ng.merged = keep
    return ng


_ERR_CELL = re.compile(rb'<(?:\w+:)?c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*?\bt="e"[^>]*>.*?<(?:\w+:)?v>([^<]*)</(?:\w+:)?v>', re.S)
_ERR_CELL2 = re.compile(rb'<(?:\w+:)?c\b[^>]*?\bt="e"[^>]*?\br="([A-Z]+)(\d+)"[^>]*>.*?<(?:\w+:)?v>([^<]*)</(?:\w+:)?v>', re.S)


def _restore_errors(path: Path, meta: SheetMeta, g: Grid) -> None:
    """calamine returns error cells as blanks; put the error codes back from the sheet XML."""
    try:
        from _xlsx import Package

        with Package(path) as pkg:
            si = pkg.sheets[meta.index] if meta.index < len(pkg.sheets) else None
            if si is None or not si.path:
                return
            data = pkg.read(si.path)
    except Exception:  # noqa: BLE001 — best effort
        return
    if b't="e"' not in data:
        return
    for rx in (_ERR_CELL, _ERR_CELL2):
        for m in rx.finditer(data):
            r, c = int(m.group(2)), col_index(m.group(1).decode())
            i, j = r - g.row1, c - g.col1
            if 0 <= i < len(g.rows) and 0 <= j < len(g.rows[i]):
                g.rows[i][j] = ErrorText(m.group(3).decode())


class ErrorText(str):
    """An error value read from a file (#N/A, #DIV/0!…)."""

    @property
    def code(self) -> str:
        return str(self)


# ── CSV / TSV / JSON ────────────────────────────────────────────────────


def decode_text(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-8-sig":
            enc = "utf-8 with BOM" if data[:3] == b"\xef\xbb\xbf" else "utf-8"
        return text, enc
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def read_csv(path: Path, delimiter: str | None = None, infer: bool = True) -> tuple[list[list[Any]], dict[str, Any]]:
    data = path.read_bytes()
    text, enc = decode_text(data)
    if delimiter is None:
        if path.suffix.lower() in (".tsv", ".tab"):
            delimiter = "\t"
        else:
            sample = text[:65536]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    raw = list(reader)
    decimal = "."
    if delimiter != "," and infer:
        # European exports (semicolon-separated) write decimal commas: 1,5 and 1.234,50
        probe = (x.strip() for r in raw[:500] for x in r)
        if any(_DEC_COMMA.match(x) for x in probe):
            decimal = ","
    rows: list[list[Any]] = []
    for r in raw:
        rows.append([infer_value(x, decimal) if infer else (x if x != "" else None) for x in r])
    if infer:
        keep_codes_as_text(rows, raw)
    while rows and all(v is None for v in rows[-1]):
        rows.pop()
    return rows, {"delimiter": delimiter, "encoding": enc, "decimal": decimal}


_LEAD_ZERO = re.compile(r"^[+-]?0\d+$")


def keep_codes_as_text(rows: list[list[Any]], raw: list[list[str]]) -> int:
    """A column where some numbers are written with leading zeros (ZIP codes, IDs: 01234) is a column of codes:
    every plain integer in it stays text too (90210 → "90210"), so the column has one type. Returns cells changed."""
    width = max((len(r) for r in rows), default=0)
    changed = 0
    for j in range(width):
        if not any(type(r[j]) is str and _LEAD_ZERO.match(r[j].strip()) for r in rows if j < len(r)):
            continue
        for i, r in enumerate(rows):
            if j < len(r) and type(r[j]) is int and i < len(raw) and j < len(raw[i]):
                r[j] = raw[i][j].strip()
                changed += 1
    return changed


_DEC_COMMA = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+),\d+%?$")
_EU_NUMBER = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")


_INT = re.compile(r"^[+-]?\d{1,15}$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


def infer_value(s: str, decimal: str = ".") -> Any:
    """Text from CSV/Markdown as a typed value: int, float, bool, ISO date/datetime, percent, else the text.

    decimal="," reads European numbers (1.234,5 and 12,5%)."""
    if s is None:
        return None
    t = s.strip()
    if t == "":
        return None
    if decimal == ",":
        core = t[:-1].strip() if t.endswith("%") else t
        if _EU_NUMBER.match(core) and not (len(core.lstrip("+-")) > 1 and core.lstrip("+-")[0] == "0" and "," not in core):
            num = core.replace(".", "").replace(",", ".")
            if t.endswith("%"):
                return Percent(float(num) / 100)
            return float(num) if "." in num else int(num)
    if _INT.match(t):
        if len(t.lstrip("+-")) > 1 and t.lstrip("+-").startswith("0"):
            return s  # keep leading zeros (IDs, zip codes)
        return int(t)
    if _FLOAT.match(t):
        try:
            return float(t)
        except ValueError:
            return s
    up = t.upper()
    if up in ("TRUE", "FALSE"):
        return up == "TRUE"
    if _ISO_DATE.match(t):
        try:
            return _dt.date.fromisoformat(t)
        except ValueError:
            return s
    if _ISO_DT.match(t):
        try:
            return _dt.datetime.fromisoformat(t.replace(" ", "T"))
        except ValueError:
            return s
    if t.endswith("%") and _FLOAT.match(t[:-1].strip()):
        return Percent(float(t[:-1]) / 100)
    neg = False
    core = t
    if core.startswith("(") and core.endswith(")"):  # accounting negatives: ($150.25)
        neg, core = True, core[1:-1].strip()
    elif core.startswith("-") and core[1:2] in "$€£¥":
        neg, core = True, core[1:]
    m = re.match(r"^([$€£¥])\s?(-?[\d,]*\.?\d+)$", core)
    if m and re.fullmatch(r"-?\d{1,3}(,\d{3})*(\.\d+)?|-?\d+(\.\d+)?", m.group(2)):
        v = float(m.group(2).replace(",", ""))
        return Currency(-v if neg else v, m.group(1))
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", t):
        return float(t.replace(",", "")) if "." in t else int(t.replace(",", ""))
    return s


class Percent(float):
    """A number that came in as '12%' (written with a percent format)."""


class Currency(float):
    """A number that came in as '$12.50' (written with a currency format)."""

    def __new__(cls, v: float, symbol: str = "$") -> "Currency":
        obj = super().__new__(cls, v)
        obj.symbol = symbol  # type: ignore[attr-defined]
        return obj


def json_rows(path: Path) -> list[list[Any]]:
    """JSON records (list of objects), a list of lists, {"rows": [...]}, or JSON Lines → header + rows."""
    text, _ = decode_text(path.read_bytes())
    stripped = text.strip()
    data: Any
    if path.suffix.lower() in (".jsonl", ".ndjson") or (stripped.startswith("{") and "\n{" in stripped):
        data = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    else:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise SkillError(f"{path.name}: invalid JSON: {e}") from e
    return records_to_rows(data)


def json_blocks(path: Path) -> list[tuple[str, list[list[Any]]]]:
    """Like json_rows, but {"Sheet A": [records], "Sheet B": [records]} gives one block per key."""
    text, _ = decode_text(path.read_bytes())
    stripped = text.strip()
    if stripped.startswith("{") and "\n{" not in stripped and path.suffix.lower() not in (".jsonl", ".ndjson"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise SkillError(f"{path.name}: invalid JSON: {e}") from e
        if isinstance(data, dict) and data and not any(k in data for k in ("rows", "data", "records", "items", "values")) and all(isinstance(v, list) for v in data.values()):
            return [(str(k), records_to_rows(v)) for k, v in data.items()]
    return [(path.stem, json_rows(path))]


def records_to_rows(data: Any) -> list[list[Any]]:
    if isinstance(data, dict):
        for key in ("rows", "data", "records", "items", "values"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise SkillError("JSON must be a list of records or of rows")
    if not data:
        return []
    if all(isinstance(r, list) for r in data):
        return [[_json_cell(v) for v in r] for r in data]
    keys: list[str] = []
    seen: set[str] = set()
    for r in data:
        if isinstance(r, dict):
            for k in r:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
    rows: list[list[Any]] = [list(keys)]
    for r in data:
        if isinstance(r, dict):
            rows.append([_json_cell(r.get(k)) for k in keys])
        else:
            rows.append([_json_cell(r)])
    return rows


def _json_cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, str):
        if _ISO_DATE.match(v):
            try:
                return _dt.date.fromisoformat(v)
            except ValueError:
                return v
        if _ISO_DT.match(v):
            try:
                return _dt.datetime.fromisoformat(v.replace(" ", "T"))
            except ValueError:
                return v
    return v


# A Markdown table's rule line (|---|:--:|). No two runs of blanks touch, so a line of spaces is not rescanned from
# every position (the "\s*\|?\s*" form was quadratic in the line length).
_MD_RULE = re.compile(r"^[^\S\n]*(?:\|[^\S\n]*)?:?-{2,}:?(?:[^\S\n]*\|[^\S\n]*:?-{2,}:?)*[^\S\n]*(?:\|[^\S\n]*)?$")


def markdown_tables(text: str) -> list[tuple[str | None, list[list[Any]]]]:
    """Pipe tables in Markdown → [(preceding heading or None, rows)] with typed cells."""
    lines = text.splitlines()
    out: list[tuple[str | None, list[list[Any]]]] = []
    heading: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip() or heading
        if line.startswith("|") and i + 1 < len(lines) and _MD_RULE.match(lines[i + 1]):
            block = [line]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j].strip())
                j += 1
            rows = []
            for k, b in enumerate(block):
                cells = _split_md_row(b)
                rows.append([c if k == 0 else infer_value(c) for c in cells])
            w = max(len(r) for r in rows)
            rows = [r + [None] * (w - len(r)) for r in rows]
            out.append((heading, rows))
            i = j
            continue
        i += 1
    return out


def _split_md_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, esc = [], [], False
    for ch in s:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    return [c.replace("<br>", "\n") for c in cells]


# ── header detection and table output ───────────────────────────────────


def detect_header(rows: list[list[Any]]) -> bool:
    """True when the first row looks like column names (all text, unique) above data."""
    if len(rows) < 2:
        return False
    first = [v for v in rows[0] if v is not None]
    if not first or not all(isinstance(v, str) for v in first):
        return False
    if len({str(v).strip().lower() for v in first}) != len(first):
        return False
    # at least one column's data is not text, or the texts look like labels (short)
    body = rows[1 : min(len(rows), 50)]
    for j in range(len(rows[0])):
        col = [r[j] for r in body if j < len(r) and r[j] is not None]
        if col and any(not isinstance(v, str) for v in col):
            return True
    return all(len(str(v)) <= 40 for v in first) and len(rows) > 2


def trim_grid(rows: list[list[Any]]) -> list[list[Any]]:
    """Drops trailing empty rows and columns."""
    while rows and all(v is None for v in rows[-1]):
        rows = rows[:-1]
    if not rows:
        return rows
    w = 0
    for r in rows:
        for j in range(len(r) - 1, -1, -1):
            if r[j] is not None:
                w = max(w, j + 1)
                break
    return [r[:w] + [None] * (w - len(r[:w])) for r in rows]


def area_arg(spec: str | None) -> tuple[str | None, tuple[int, int, int, int] | None]:
    """'Sheet1!A1:C9' → ('Sheet1', area); 'B2:D5' → (None, area)."""
    if not spec:
        return None, None
    sheet, rest = split_sheet(spec)
    try:
        return sheet, parse_range(rest)
    except ValueError as e:
        raise UsageError(str(e)) from e


_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def file_stem(name: str | None, fallback: str = "sheet") -> str:
    """A safe file name stem for a sheet or file name, used for every file written per sheet: letters, digits, '.',
    '-' and '_' only (so never a path: '../../x' gives 'x'), no leading dot (never hidden), at most 60 characters,
    and never a Windows device name (CON, NUL, COM1…)."""
    s = re.sub(r"[^\w.-]+", "_", name or "", flags=re.UNICODE).strip("._")[:60].rstrip("._")
    if not s:
        s = fallback
    if s.split(".")[0].upper() in _DEVICE_NAMES:
        s = "_" + s
    return s


def unique_stem(stem: str, used: set[str]) -> str:
    """stem, or stem-2, stem-3… when an earlier sheet took it (compared without case, as macOS and Windows do)."""
    out, k = stem, 1
    while out.lower() in used:
        k += 1
        out = f"{stem}-{k}"
    used.add(out.lower())
    return out


def unique_headers(names: Sequence[Any]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, n in enumerate(names):
        base = text_value(n).strip() or f"column_{i + 1}"
        k = base.lower()
        if k in seen:
            seen[k] += 1
            base = f"{base}_{seen[k]}"
        else:
            seen[k] = 1
        out.append(base)
    return out


def md_grid(g: Grid, header: bool, show_coords: bool = True) -> str:
    """A Markdown table of a grid; with coords, a 'row' column and column letters so cells can be addressed."""
    rows = g.rows
    if not rows:
        return "_(empty)_"
    w = g.width
    letters = [col_letter(g.col1 + j) for j in range(w)]
    if header:
        names = [text_value(v) for v in (rows[0] + [None] * (w - len(rows[0])))]
        heads = [f"{letters[j]}: {names[j]}" if show_coords and names[j] else (letters[j] if show_coords else names[j]) for j in range(w)]
        body = rows[1:]
        first_row = g.row1 + 1
    else:
        heads = letters
        body = rows
        first_row = g.row1
    if show_coords:
        heads = ["row"] + heads
    lines = ["| " + " | ".join(md_escape_cell(h) for h in heads) + " |", "|" + "|".join("---" for _ in heads) + "|"]
    for i, r in enumerate(body):
        cells = [md_escape_cell(text_value(v)) for v in r] + [""] * (w - len(r))
        if show_coords:
            cells = [str(first_row + i)] + cells
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def csv_text(rows: Iterable[Sequence[Any]], delimiter: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    for r in rows:
        w.writerow([text_value(v) for v in r])
    return buf.getvalue()
