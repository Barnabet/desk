"""Fast .xlsx/.xlsm reading (values, formulas, styles' number formats, names, tables) and in-place patching of
cached formula values inside the zip, so every reader sees computed numbers. Standard library only.
"""

from __future__ import annotations

import io
import math
import os
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree as ET

from _a1 import col_index, col_letter, parse_range
from _formula import (
    DYNAMIC_FUNCS, ERRORS, Array, Book, FormulaCell, FormulaSyntaxError, SheetData, TableDef, XLError, functions_used,
    parse, translate,
)
from _numfmt import BUILTIN_FORMATS, date_kind, date_to_serial

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_METADATA = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata"
CT_METADATA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    v = el.get(name)
    if v is not None:
        return v
    for k, val in el.attrib.items():
        if _local(k) == name:
            return val
    return None


_CELL_REF = re.compile(r"([A-Z]{1,3})(\d+)")


def _split_ref(ref: str) -> tuple[int, int]:
    m = _CELL_REF.match(ref)
    if not m:
        raise ValueError(f"bad cell reference {ref}")
    return int(m.group(2)), col_index(m.group(1))


def temp_beside(dest: Path, prefix: str = ".desk-xlsx-", suffix: str = ".xlsx") -> Path:
    """A closed, empty temp file in dest's folder (so the final rename stays on one volume)."""
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(Path(dest).parent))
    os.close(fd)
    return Path(name)


def publish(tmp: Path, dest: Path) -> None:
    """Moves a finished temp file into place with ordinary permissions (mkstemp files are private)."""
    os.replace(tmp, dest)
    try:
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(dest, 0o666 & ~mask)
    except OSError:
        pass


class SheetInfo:
    __slots__ = ("name", "sheet_id", "rid", "path", "state", "index")

    def __init__(self, name: str, sheet_id: str, rid: str, path: str, state: str, index: int) -> None:
        self.name, self.sheet_id, self.rid, self.path, self.state, self.index = name, sheet_id, rid, path, state, index


class Package:
    """An opened .xlsx/.xlsm/.xltx/.xltm: parts, sheets, shared strings, number formats, names, tables."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        from _book import guard_zip

        guard_zip(self.path)
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise ValueError(f"{self.path.name} is not an Office Open XML workbook (not a zip file)") from e
        self.names_in_zip = set(self.zip.namelist())
        self.workbook_path = self._find_workbook()
        self.sheets: list[SheetInfo] = []
        self.defined_names: list[tuple[str, int | None, str, bool]] = []
        self.date1904 = False
        self.calc: dict[str, str] = {}
        self.protection = False
        self._rels = self._read_rels(self.workbook_path)
        self._parse_workbook()
        self._shared: list[str] | None = None
        self._xf_numfmt: list[int] | None = None
        self._numfmts: dict[int, str] = {}

    def close(self) -> None:
        self.zip.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def read(self, name: str) -> bytes:
        return self.zip.read(name)

    def has(self, name: str) -> bool:
        return name in self.names_in_zip

    def _find_workbook(self) -> str:
        if self.has("_rels/.rels"):
            root = ET.fromstring(self.read("_rels/.rels"))
            for rel in root:
                if _attr(rel, "Type") and _attr(rel, "Type").endswith("/officeDocument"):  # type: ignore[union-attr]
                    return _attr(rel, "Target").lstrip("/")  # type: ignore[union-attr]
        if self.has("xl/workbook.xml"):
            return "xl/workbook.xml"
        raise ValueError(f"{self.path.name} has no workbook part (is it really a spreadsheet?)")

    def _read_rels(self, part: str) -> dict[str, tuple[str, str]]:
        d, base = posixpath.split(part)
        rels_path = posixpath.join(d, "_rels", base + ".rels")
        out: dict[str, tuple[str, str]] = {}
        if not self.has(rels_path):
            return out
        root = ET.fromstring(self.read(rels_path))
        for rel in root:
            rid, typ, target, mode = _attr(rel, "Id"), _attr(rel, "Type") or "", _attr(rel, "Target") or "", _attr(rel, "TargetMode")
            if rid is None:
                continue
            if mode == "External":
                out[rid] = (typ, target)
                continue
            full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(d, target))
            out[rid] = (typ, full)
        return out

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        return self._read_rels(part)

    def _parse_workbook(self) -> None:
        root = ET.fromstring(self.read(self.workbook_path))
        idx = 0
        for el in root.iter():
            tag = _local(el.tag)
            if tag == "sheet":
                rid = _attr(el, "id") or ""
                typ, target = self._rels.get(rid, ("", ""))
                if typ and not typ.endswith("/worksheet"):
                    # chartsheets and dialog sheets have no cells
                    self.sheets.append(SheetInfo(el.get("name", f"Sheet{idx + 1}"), el.get("sheetId", ""), rid, "", "chart" if "chartsheet" in typ else "other", idx))
                else:
                    self.sheets.append(SheetInfo(el.get("name", f"Sheet{idx + 1}"), el.get("sheetId", ""), rid, target, el.get("state", "visible"), idx))
                idx += 1
            elif tag == "definedName":
                local = el.get("localSheetId")
                self.defined_names.append((el.get("name", ""), int(local) if local is not None and local.isdigit() else None, el.text or "", el.get("hidden") in ("1", "true")))
            elif tag == "workbookPr":
                self.date1904 = el.get("date1904") in ("1", "true")
            elif tag == "calcPr":
                self.calc = dict(el.attrib)
            elif tag == "workbookProtection":
                # an empty element (openpyxl writes one) protects nothing
                self.protection = any(el.get(k) in ("1", "true") for k in ("lockStructure", "lockWindows", "lockRevision")) or any(
                    "password" in _local(k).lower() or "hash" in _local(k).lower() for k in el.attrib
                )

    # shared strings and styles ---------------------------------------------

    @property
    def shared(self) -> list[str]:
        if self._shared is None:
            self._shared = []
            path = next((t for (typ, t) in self._rels.values() if typ.endswith("/sharedStrings")), None)
            if path and self.has(path):
                with self.zip.open(path) as f:
                    for ev, el in ET.iterparse(f, events=("end",)):
                        if _local(el.tag) == "si":
                            self._shared.append(_rich_text(el))
                            el.clear()
        return self._shared

    def _load_styles(self) -> None:
        self._xf_numfmt = []
        path = next((t for (typ, t) in self._rels.values() if typ.endswith("/styles")), None)
        if not path or not self.has(path):
            return
        root = ET.fromstring(self.read(path))
        for el in root:
            tag = _local(el.tag)
            if tag == "numFmts":
                for nf in el:
                    try:
                        self._numfmts[int(nf.get("numFmtId", "0"))] = nf.get("formatCode", "General")
                    except ValueError:
                        pass
            elif tag == "cellXfs":
                for xf in el:
                    try:
                        self._xf_numfmt.append(int(xf.get("numFmtId", "0")))
                    except ValueError:
                        self._xf_numfmt.append(0)

    def number_format(self, style_index: int) -> str:
        if self._xf_numfmt is None:
            self._load_styles()
        assert self._xf_numfmt is not None
        if style_index < 0 or style_index >= len(self._xf_numfmt):
            return "General"
        nid = self._xf_numfmt[style_index]
        return self._numfmts.get(nid) or BUILTIN_FORMATS.get(nid, "General")

    def sheet(self, name_or_index: str | int) -> SheetInfo:
        if isinstance(name_or_index, int):
            return self.sheets[name_or_index]
        for s in self.sheets:
            if s.name == name_or_index:
                return s
        for s in self.sheets:
            if s.name.lower() == str(name_or_index).lower():
                return s
        raise KeyError(name_or_index)

    def tables(self) -> list[tuple[str, TableDef, str]]:
        """(sheet name, table, part path) for every table."""
        out = []
        for s in self.sheets:
            if not s.path:
                continue
            for rid, (typ, target) in self._read_rels(s.path).items():
                if typ.endswith("/table") and self.has(target):
                    root = ET.fromstring(self.read(target))
                    ref = root.get("ref", "A1:A1")
                    try:
                        r1, c1, r2, c2 = parse_range(ref)
                    except ValueError:
                        continue
                    cols = [tc.get("name", "") for tc in root.iter() if _local(tc.tag) == "tableColumn"]
                    name = root.get("displayName") or root.get("name") or f"Table{len(out) + 1}"
                    out.append((s.name, TableDef(name, s.name, (r1, c1, r2, c2), cols, int(root.get("headerRowCount", "1")), int(root.get("totalsRowCount", "0") or 0)), target))
        return out

    # cells -------------------------------------------------------------------

    def iter_cells(self, sheet: SheetInfo, want_formulas: bool = True) -> Iterator[tuple[int, int, Any, dict[str, Any] | None, int]]:
        """(row, col, cached value, formula info or None, style index) for each stored cell, streaming.

        Formula info: {'text', 'type' (normal/shared/array/dataTable), 'ref', 'si', 'cm'}.
        """
        if not sheet.path or not self.has(sheet.path):
            return
        shared = self.shared
        with self.zip.open(sheet.path) as f:
            row_num = 0
            col_num = 0
            for ev, el in ET.iterparse(f, events=("start", "end")):
                tag = _local(el.tag)
                if ev == "start":
                    if tag == "row":
                        r = el.get("r")
                        row_num = int(r) if r else row_num + 1
                        col_num = 0
                    continue
                if tag != "c":
                    if tag == "row":
                        el.clear()
                    continue
                ref = el.get("r")
                if ref:
                    try:
                        row_num, col_num = _split_ref(ref)
                    except ValueError:
                        col_num += 1
                else:
                    col_num += 1
                t = el.get("t", "n")
                s = el.get("s")
                style = int(s) if s and s.isdigit() else 0
                value: Any = None
                formula: dict[str, Any] | None = None
                for child in el:
                    ctag = _local(child.tag)
                    if ctag == "v":
                        value = child.text
                    elif ctag == "f" and want_formulas:
                        formula = {"text": child.text or "", "type": child.get("t", "normal"), "ref": child.get("ref"), "si": child.get("si"), "cm": el.get("cm")}
                    elif ctag == "is":
                        value = _rich_text(child)
                        t = "inlineStr"
                value = _convert(value, t, shared)
                el.clear()
                if value is None and formula is None:
                    if style:
                        yield row_num, col_num, None, None, style
                    continue
                yield row_num, col_num, value, formula, style


def _parse_rows_xml(self: "Package", xml: str, limit: int) -> list[list[Any]]:
    """Values of the first `limit` rows from a fragment of sheetData XML (as kept by the streaming scan)."""
    from _numfmt import date_kind, serial_to_python

    if not xml:
        return []
    doc = ET.fromstring(f'<sheetData xmlns="{NS_MAIN}">' + re.sub(r"<(/?)\w+:", r"<\1", xml) + "</sheetData>")
    rows: list[list[Any]] = []
    first_col = None
    for row in doc:
        if _local(row.tag) != "row":
            continue
        cells: dict[int, Any] = {}
        col = 0
        for c in row:
            if _local(c.tag) != "c":
                continue
            ref = c.get("r")
            col = _split_ref(ref)[1] if ref else col + 1
            t = c.get("t", "n")
            v = None
            for ch in c:
                tag = _local(ch.tag)
                if tag == "v":
                    v = ch.text
                elif tag == "is":
                    v, t = _rich_text(ch), "inlineStr"
            if t == "s":
                try:
                    v = self.shared_at(int(v or 0))
                except ValueError:
                    v = ""
            else:
                v = _convert(v, t, [])
            s = c.get("s")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and s and s.isdigit():
                kind = date_kind(self.number_format(int(s)))
                if kind:
                    try:
                        v = serial_to_python(v, kind, self.date1904)
                    except (ValueError, OverflowError):
                        pass
            if v is not None:
                cells[col] = v
        if cells:
            lo = min(cells)
            first_col = lo if first_col is None else min(first_col, lo)
            rows.append(cells)  # type: ignore[arg-type]
        if len(rows) >= limit:
            break
    if not rows:
        return []
    hi = max(max(r) for r in rows)  # type: ignore[arg-type]
    lo = first_col or 1
    return [[r.get(c) for c in range(lo, hi + 1)] for r in rows]  # type: ignore[union-attr]


def _shared_at(self: "Package", i: int) -> str:
    """One shared string, reading the table only as far as needed."""
    if self._shared is not None:
        return self._shared[i] if 0 <= i < len(self._shared) else ""
    part: list[str] = getattr(self, "_shared_part", None) or []
    if i < len(part):
        return part[i]
    path = next((t for (typ, t) in self._rels.values() if typ.endswith("/sharedStrings")), None)
    if not path or not self.has(path):
        return ""
    part = []
    with self.zip.open(path) as f:
        for ev, el in ET.iterparse(f, events=("end",)):
            if _local(el.tag) == "si":
                part.append(_rich_text(el))
                el.clear()
                if len(part) > i + 1000:
                    break
    self._shared_part = part  # type: ignore[attr-defined]
    return part[i] if i < len(part) else ""


Package.parse_rows_xml = _parse_rows_xml  # type: ignore[attr-defined]
Package.shared_at = _shared_at  # type: ignore[attr-defined]


def _rich_text(el: ET.Element) -> str:
    parts = []
    for node in el.iter():
        tag = _local(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag in ("rPh",):
            # phonetic runs are not part of the displayed text
            pass
    # rPh children also contain <t>; remove them by re-walking without phonetics
    if any(_local(n.tag) == "rPh" for n in el.iter()):
        parts = []
        for child in el:
            ctag = _local(child.tag)
            if ctag == "t" and child.text:
                parts.append(child.text)
            elif ctag == "r":
                for t in child:
                    if _local(t.tag) == "t" and t.text:
                        parts.append(t.text)
    return _unescape_ooxml("".join(parts))


_OOXML_ESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _unescape_ooxml(s: str) -> str:
    if "_x" not in s:
        return s
    return _OOXML_ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def _convert(v: str | None, t: str, shared: list[str]) -> Any:
    if v is None:
        return None
    if t == "s":
        try:
            return shared[int(v)]
        except (ValueError, IndexError):
            return ""
    if t in ("str", "inlineStr"):
        return _unescape_ooxml(v)
    if t == "b":
        return v.strip() in ("1", "true")
    if t == "e":
        return ERRORS.get(v.strip(), ERRORS["#VALUE!"])
    if t == "d":
        import datetime as _dt

        try:
            return date_to_serial(_dt.datetime.fromisoformat(v.strip().rstrip("Z")))
        except ValueError:
            return v
    s = v.strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return s
    if f.is_integer() and abs(f) < 2**53 and "." not in s and "e" not in s.lower():
        return int(f)
    return f


# ── building an engine Book from a package ──────────────────────────────


# package parts openpyxl does not carry over when it rewrites a workbook
_UNKEPT_PARTS = (
    ("customXml/", "custom XML parts"),
    ("xl/slicers/", "slicers"),
    ("xl/slicerCaches/", "slicers"),
    ("xl/timelines/", "timelines"),
    ("xl/timelineCaches/", "timelines"),
    ("xl/connections.xml", "data connections"),
    ("xl/queryTables/", "query tables"),
    ("xl/model/", "the data model (Power Pivot)"),
    ("xl/richData/", "rich data types and pictures in cells"),
    ("xl/webextensions/", "add-ins"),
    ("xl/threadedComments/", "threaded comments (plain notes are kept)"),
    ("xl/activeX/", "ActiveX controls"),
    ("xl/ctrlProps/", "form controls"),
    ("xl/embeddings/", "embedded objects"),
    ("xl/customProperty", "custom sheet properties"),
)


def openpyxl_losses(pkg: "Package", skip_sheet_scan: bool = False) -> set[str]:
    """Features an openpyxl round trip would lose (sheet_edit then prefers its patch mode)."""
    lost: set[str] = set()
    for name in pkg.names_in_zip:
        for prefix, what in _UNKEPT_PARTS:
            if name.startswith(prefix):
                lost.add(what)
        if re.match(r"xl/charts/(style|colors)\d+\.xml$", name):
            lost.add("chart styles and colour sets (charts keep their data and basic look)")
    for si in pkg.sheets:
        if not si.path or not pkg.has(si.path):
            continue
        if not skip_sheet_scan:
            from _scan import scan_cached

            feats = (scan_cached(pkg.path, si.path) or {}).get("features") or {}
            if feats.get("sparklines"):
                lost.add("sparklines")
            if feats.get("x14_validations"):
                lost.add("extended data validations")
            if feats.get("x14_conditional_formats"):
                lost.add("extended conditional formats (icon sets, data bars)")
        for _rid, (typ, target) in pkg.rels(si.path).items():
            low = typ.lower()
            if "slicer" in low:
                lost.add("slicers")
            elif "timeline" in low:
                lost.add("timelines")
            elif "ctrlprop" in low:
                lost.add("form controls")
            elif typ.endswith("/drawing") and pkg.has(target):
                d = pkg.read(target)
                if re.search(rb"<(?:\w+:)?sp\b", d):
                    lost.add("shapes and text boxes")
                if b"chartEx" in d or any("chartex" in t.lower() for t, _ in pkg.rels(target).values()):
                    lost.add("modern charts (waterfall, treemap…)")
    return lost


class GridValues:
    """A sheet's constant values for the engine, backed by calamine's dense rows (not a dict of millions of tuples).

    Behaves like the dict the engine expects ({(row, col): value}): get, in, len, keys, items, pop, item assignment.
    Dates come back from calamine as datetime objects and are turned into serial numbers when first read."""

    __slots__ = ("rows", "r0", "c0", "over", "gone", "n", "date1904")

    def __init__(self, rows: list[list[Any]], r0: int, c0: int, count: int, date1904: bool) -> None:
        self.rows, self.r0, self.c0 = rows, r0, c0
        self.over: dict[tuple[int, int], Any] = {}
        self.gone: set[tuple[int, int]] = set()
        self.n = count
        self.date1904 = date1904

    def _base(self, key: tuple[int, int]) -> Any:
        i, j = key[0] - self.r0, key[1] - self.c0
        if i < 0 or j < 0 or i >= len(self.rows):
            return None
        row = self.rows[i]
        if j >= len(row):
            return None
        v = row[j]
        if v == "" or v is None:
            return None
        t = type(v)
        if t is float:
            if v.is_integer() and abs(v) < 2**53:
                v = int(v)
                row[j] = v
            return v
        if t is int or t is str or t is bool:
            return v
        import datetime as _dt

        if t is _dt.timedelta:
            v = v.total_seconds() / 86400
        elif t in (_dt.datetime, _dt.date, _dt.time):
            v = date_to_serial(v, self.date1904)
            if isinstance(v, float) and v.is_integer():
                v = int(v)
        row[j] = v
        return v

    def get(self, key: tuple[int, int], default: Any = None) -> Any:
        if self.over:
            v = self.over.get(key)
            if v is not None:
                return v
        if self.gone and key in self.gone:
            return default
        v = self._base(key)
        return default if v is None else v

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not None  # type: ignore[arg-type]

    def __getitem__(self, key: tuple[int, int]) -> Any:
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __setitem__(self, key: tuple[int, int], v: Any) -> None:
        if self._base(key) is None and key not in self.over:
            self.n += 1
        self.over[key] = v
        self.gone.discard(key)

    def pop(self, key: tuple[int, int], default: Any = None) -> Any:
        v = self.get(key)
        if v is None:
            return default
        self.over.pop(key, None)
        if self._base(key) is not None:
            self.gone.add(key)
        self.n -= 1
        return v

    def __len__(self) -> int:
        return self.n

    def keys(self) -> Iterator[tuple[int, int]]:
        for i, row in enumerate(self.rows):
            r = self.r0 + i
            for j, v in enumerate(row):
                if v != "" and v is not None and (r, self.c0 + j) not in self.gone:
                    yield (r, self.c0 + j)
        for k in self.over:
            if self._base(k) is None:
                yield k

    def __iter__(self) -> Iterator[tuple[int, int]]:
        return self.keys()

    def items(self) -> Iterator[tuple[tuple[int, int], Any]]:
        for k in self.keys():
            yield k, self.get(k)

    def values(self) -> Iterator[Any]:
        for k in self.keys():
            yield self.get(k)


FAST_LOAD_CELLS = 50_000
_F_OPEN = re.compile(rb"<(?:\w+:)?f\b([^>]*?)(/>|>(.*?)</(?:\w+:)?f>)", re.S)
_ATTR = re.compile(rb'\b(\w+)="([^"]*)"')


def _xml_unescape(b: bytes) -> str:
    import html

    s = b.decode("utf-8", "replace")
    return html.unescape(s) if "&" in s else s


def formula_cells(zf: zipfile.ZipFile, part: str) -> list[tuple[int, int, dict[str, Any]]]:
    """(row, col, {'text', 'type', 'ref', 'si', 'cm'}) of every formula cell of a sheet part, found without parsing
    the other cells (a regex scan of the inflated XML, chunk by chunk)."""
    out: list[tuple[int, int, dict[str, Any]]] = []
    carry = b""
    prefix = b""
    first = True
    with zf.open(part) as fh:
        while True:
            chunk = fh.read(8 << 20)
            buf = carry + chunk if carry else chunk
            if first:
                first = False
                m = re.search(rb"<(\w+:)?worksheet\b", buf[:4096])
                prefix = (m.group(1) or b"") if m else b""
            if not chunk:
                body, carry = buf, b""
            else:
                cut = buf.rfind(b"</" + prefix + b"c>")
                if cut < 0:
                    carry = buf
                    continue
                cut += len(prefix) + 4
                body, carry = buf[:cut], buf[cut:]
            if b"<" + prefix + b"f" not in body:
                if not chunk:
                    break
                continue
            cell_open = b"<" + prefix + b"c "
            for m in _F_OPEN.finditer(body):
                start = body.rfind(cell_open, 0, m.start())
                if start < 0:
                    continue
                tag_end = body.find(b">", start)
                attrs = dict(_ATTR.findall(body[start:tag_end]))
                ref = attrs.get(b"r")
                if not ref:
                    continue
                try:
                    r, c = _split_ref(ref.decode())
                except ValueError:
                    continue
                fattrs = dict(_ATTR.findall(m.group(1)))
                text = _xml_unescape(m.group(3)) if m.group(3) is not None else ""
                out.append((r, c, {"text": text, "type": fattrs.get(b"t", b"normal").decode(), "ref": fattrs[b"ref"].decode() if b"ref" in fattrs else None,
                                   "si": fattrs[b"si"].decode() if b"si" in fattrs else None, "cm": attrs[b"cm"].decode() if b"cm" in attrs else None}))
            if not chunk:
                break
    return out


def _fast_sheet(path: Path, pkg: "Package", si: SheetInfo, sh: Any, book: Book) -> list[tuple[int, int, Any, dict[str, Any]]] | None:
    """Values of a big sheet from calamine (as GridValues) and its formula cells; None when calamine cannot."""
    try:
        import python_calamine as pc

        wb = pc.CalamineWorkbook.from_path(str(path))
        try:
            csh = wb.get_sheet_by_name(si.name)
            start = csh.start
            rows = csh.to_python(skip_empty_area=True) if start is not None else []
        finally:
            wb.close()
    except Exception:  # noqa: BLE001 — newer error values etc.: the XML reader handles them
        return None
    r0, c0 = (start[0] + 1, start[1] + 1) if start is not None else (1, 1)
    count = sum(len(r) - r.count("") for r in rows)
    gv = GridValues(rows, r0, c0, count, book.date1904)
    from _scan import scan_cached

    st = scan_cached(path, si.path) or {}
    for r, c, code in st.get("errors") or []:
        gv[(r, c)] = ERRORS.get(code, ERRORS["#VALUE!"])
    sh.values = gv
    if rows:
        sh.bump(r0 + len(rows) - 1, c0 + max(len(x) for x in rows) - 1)
    with zipfile.ZipFile(path) as z:
        fcells = formula_cells(z, si.path)
    return [(r, c, gv.get((r, c)), f) for r, c, f in fcells]


def load_book(path: str | Path, sheets: list[str] | None = None) -> tuple[Book, Package, dict[str, Any]]:
    """Reads values and formulas into a Book. Returns (book, package, extra) where extra has per-sheet style and
    formula metadata used by the writer (dynamic arrays' previous spill ranges, data tables)."""
    pkg = Package(path)
    book = Book()
    book.date1904 = pkg.date1904
    calc = pkg.calc
    book.iterate = calc.get("iterate") in ("1", "true")
    try:
        book.iterate_count = int(calc.get("iterateCount", "100"))
        book.iterate_delta = float(calc.get("iterateDelta", "0.001"))
    except ValueError:
        pass
    extra: dict[str, Any] = {"old_spills": {}, "data_tables": 0}
    for si in pkg.sheets:
        sh = book.add_sheet(si.name)
        sh.state = si.state
    fast_min = int(os.environ.get("DESK_FAST_LOAD_CELLS", FAST_LOAD_CELLS))
    for si in pkg.sheets:
        if sheets is not None and si.name not in sheets:
            continue
        sh = book.sheets[si.index]
        masters: dict[str, tuple[int, int, str]] = {}
        pending_shared: list[tuple[int, int, str, Any]] = []
        cells_iter: Any = None
        if si.path and pkg.has(si.path):
            from _scan import scan_cached

            st = scan_cached(pkg.path, si.path) or {}
            if st.get("cells", 0) >= fast_min:
                got = _fast_sheet(pkg.path, pkg, si, sh, book)
                if got is not None:
                    cells_iter = ((r, c, v, f, 0) for r, c, v, f in got)
        if cells_iter is None:
            cells_iter = pkg.iter_cells(si)
        for r, c, v, f, _style in cells_iter:
            if f is None:
                if v is not None:
                    sh.values[(r, c)] = v
                    sh.bump(r, c)
                continue
            ftype = f["type"]
            text = f["text"]
            if ftype == "shared":
                si_key = f["si"] or ""
                if text:
                    masters[si_key] = (r, c, text)
                else:
                    pending_shared.append((r, c, si_key, v))
                    continue
            if ftype == "dataTable":
                # What-if data tables are computed by Excel itself; keep their cached values.
                extra["data_tables"] += 1
                if v is not None:
                    sh.values[(r, c)] = v
                    sh.bump(r, c)
                continue
            array_ref = None
            dynamic = False
            if ftype == "array":
                ref = f["ref"] or f"{col_letter(c)}{r}"
                try:
                    a1, b1, a2, b2 = parse_range(ref)
                except ValueError:
                    a1, b1, a2, b2 = r, c, r, c
                array_ref = (a1, b1, a2, b2)
                dynamic = f["cm"] is not None or _uses_dynamic(text)
                if dynamic:
                    extra["old_spills"][(si.index, r, c)] = array_ref
                    array_ref = None
            fc = book.set_formula(sh, r, c, text, array_ref=array_ref, dynamic=dynamic, cached=v)
            if dynamic:
                fc.dynamic = True
        for r, c, key, v in pending_shared:
            m = masters.get(key)
            if m is None:
                if v is not None:
                    sh.values[(r, c)] = v
                    sh.bump(r, c)
                continue
            mr, mc, mtext = m
            try:
                text = translate(mtext, r - mr, c - mc)
            except FormulaSyntaxError:
                text = mtext
            book.set_formula(sh, r, c, text, cached=v)
        # cells inside an array formula's range hold its cached results, not constants: drop them
        for key in list(sh.members.keys()):
            sh.values.pop(key, None)
        for (sidx, ar, ac), (a1, b1, a2, b2) in extra["old_spills"].items():
            if sidx != si.index:
                continue
            for rr in range(a1, a2 + 1):
                for cc in range(b1, b2 + 1):
                    if (rr, cc) != (ar, ac) and (rr, cc) not in sh.formulas:
                        sh.values.pop((rr, cc), None)
    for name, local, formula, hidden in pkg.defined_names:
        if not name or name.startswith("_xlnm."):
            continue
        key = name.upper()
        if key.startswith("_XLPM."):
            continue
        book.names[(local, key)] = formula
    for sheet_name, tbl, _part in pkg.tables():
        book.tables[tbl.name.lower()] = tbl
    return book, pkg, extra


def _uses_dynamic(text: str) -> bool:
    try:
        return bool(functions_used(parse("=" + text.lstrip("="))) & DYNAMIC_FUNCS)
    except FormulaSyntaxError:
        return False


# ── writing cached values back ──────────────────────────────────────────

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _xml_text(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _XML_BAD.sub(lambda m: f"_x{ord(m.group(0)):04X}_", s)


def _xml_attr(s: str) -> str:
    return _xml_text(s).replace('"', "&quot;")


def value_xml(v: Any) -> tuple[str | None, str]:
    """(t attribute or None, <v> text) for a formula result."""
    t = type(v)
    if t is bool:
        return "b", "1" if v else "0"
    if t is XLError:
        return "e", v.code
    if t is str:
        return "str", _xml_text(v)
    if t is int:
        return None, str(v)
    if t is float:
        if math.isnan(v) or math.isinf(v):
            return "e", "#NUM!"
        if v.is_integer() and abs(v) < 1e15:
            return None, str(int(v))
        return None, repr(v)
    if v is None:
        return None, "0"
    return "str", _xml_text(str(v))


_ROW_RE = re.compile(rb"<(?:(\w+):)?row\b([^>]*?)(/>|>(.*?)</(?:\w+:)?row>)", re.S)
_CELL_RE = re.compile(rb"<(?:(\w+):)?c\b([^>]*?)(/>|>(.*?)</(?:\w+:)?c>)", re.S)
_R_ATTR = re.compile(rb'\br="([A-Z]+)(\d+)"')
_ROW_R_ATTR = re.compile(rb'\br="(\d+)"')
_T_ATTR = re.compile(rb'(?<!\s)\s+t="[^"]*"')
_CM_ATTR = re.compile(rb'(?<!\s)\s+cm="[^"]*"')
_V_EL = re.compile(rb"<(?:\w+:)?v\b[^>]*?(?:/>|>.*?</(?:\w+:)?v>)", re.S)
_IS_EL = re.compile(rb"<(?:\w+:)?is\b[^>]*?>.*?</(?:\w+:)?is>", re.S)
_F_EL = re.compile(rb"<(?:(\w+):)?f\b([^>]*?)(/>|>(.*?)</(?:\w+:)?f>)", re.S)


class CellPatch:
    """What to write into one cell: a cached formula result (optionally with new <f> attributes for array/spill
    conversion), a constant (const=True), a new formula (formula text), or nothing (clear=True, style kept).
    restyle maps the cell's current style index to a new one; keep=True changes only the style."""

    __slots__ = ("value", "f_attrs", "clear", "cm", "const", "formula", "restyle", "keep")

    def __init__(self, value: Any = None, f_attrs: bytes | None = None, clear: bool = False, cm: bool | None = None, const: bool = False, formula: str | None = None, restyle: Any = None, keep: bool = False) -> None:
        self.value, self.f_attrs, self.clear, self.cm = value, f_attrs, clear, cm
        self.const, self.formula = const, formula
        self.restyle, self.keep = restyle, keep


_S_ATTR = re.compile(rb'(?<!\s)\s+s="(\d*)"')


def _with_style(attrs: bytes, style: int | None) -> bytes:
    if style is None:
        return attrs
    attrs = _S_ATTR.sub(b"", attrs)
    return attrs + (b' s="' + str(style).encode() + b'"' if style else b"")


def patch_sheet_xml(xml: bytes, patches: dict[tuple[int, int], CellPatch], base_style: Any = None, chunks: bool = False) -> Any:
    """Applies cell patches to a worksheet part, inserting missing cells and rows in order.

    base_style(row, col, row_attrs) gives the style a missing cell starts from (for restyle). With chunks=True the
    result is a list of bytes/memoryview pieces (the part before <sheetData> content first) instead of one bytes
    object, so a 200 MB sheet is not copied again; rewrite_zip writes such a list as it is."""
    if not patches:
        return [xml] if chunks else xml
    m_open = re.search(rb"<(?:(\w+):)?sheetData\b[^>]*?(/?)>", xml)
    if not m_open:
        return [xml] if chunks else xml
    prefix = (m_open.group(1) + b":") if m_open.group(1) else b""
    mv = memoryview(xml)
    if m_open.group(2) == b"/":
        head: Any = xml[: m_open.end() - 2] + b">"
        tail: Any = b"</" + prefix + b"sheetData>" + xml[m_open.end() :]
        start = end = m_open.end()
    else:
        end = xml.find(b"</" + prefix + b"sheetData>", m_open.end())
        if end < 0:
            return [xml] if chunks else xml
        start = m_open.end()
        head = xml[:start]
        tail = mv[end:]
    base = base_style or (lambda r, c, a: 0)
    by_row: dict[int, dict[int, CellPatch]] = {}
    for (r, c), p in patches.items():
        by_row.setdefault(r, {})[c] = p
    out: list[Any] = []
    pending_rows = sorted(by_row)
    done = False
    if end - start > (4 << 20) and len(pending_rows) <= 5000:
        # a big sheet: find each target row by binary search over the (ordered) rows, never walking all of them
        pos = start
        for r in pending_rows:
            at, exists = _locate_row(xml, prefix, r, pos, end)
            if at is None:
                break
            out.append(mv[pos:at])
            if exists:
                m = _ROW_RE.match(xml, at, end)
                assert m is not None
                out.append(_patch_row(m, r, by_row[r], prefix, base))
                pos = m.end()
            else:
                out.append(_new_row(prefix, r, by_row[r], base))
                pos = at
        else:
            out.append(mv[pos:end])
            done = True
        if not done:
            out = []  # an unordered or unusual sheet: fall back to walking every row
    if not done:
        pos = start
        last_row = 0
        pi = 0
        for m in _ROW_RE.finditer(xml, start, end):
            rm = _ROW_R_ATTR.search(m.group(2))
            rnum = int(rm.group(1)) if rm else last_row + 1
            last_row = rnum
            # rows that must be created before this one
            while pi < len(pending_rows) and pending_rows[pi] < rnum:
                out.append(mv[pos : m.start()])
                pos = m.start()
                out.append(_new_row(prefix, pending_rows[pi], by_row[pending_rows[pi]], base))
                pi += 1
            if rnum in by_row:
                out.append(mv[pos : m.start()])
                out.append(_patch_row(m, rnum, by_row[rnum], prefix, base))
                pos = m.end()
                if pi < len(pending_rows) and pending_rows[pi] == rnum:
                    pi += 1
        out.append(mv[pos:end])
        while pi < len(pending_rows):
            out.append(_new_row(prefix, pending_rows[pi], by_row[pending_rows[pi]], base))
            pi += 1
    pieces = [head] + [x for x in out if len(x)] + [tail]
    return pieces if chunks else b"".join(pieces)


def area_cells(xml: bytes, r1: int, r2: int, c1: int = 1, c2: int = 16384) -> Iterator[tuple[int, int, int, bytes, bytes]]:
    """(row, col, style index, cell attributes, cell inner XML) for the stored cells of rows r1..r2 and columns
    c1..c2 of a sheet part. Rows are found by binary search (ordered rows), so reading rows 150000-150010 of a
    200 MB sheet costs milliseconds; unordered sheets fall back to a walk."""
    m_open = re.search(rb"<(?:(\w+):)?sheetData\b[^>]*?(/?)>", xml)
    if not m_open or m_open.group(2) == b"/":
        return
    prefix = (m_open.group(1) + b":") if m_open.group(1) else b""
    start = m_open.end()
    end = xml.find(b"</" + prefix + b"sheetData>", start)
    if end < 0:
        return
    at, _ = _locate_row(xml, prefix, r1, start, end)
    walk_all = at is None
    last = 0
    for m in _ROW_RE.finditer(xml, start if walk_all else at, end):
        rm = _ROW_R_ATTR.search(m.group(2))
        rnum = int(rm.group(1)) if rm else last + 1
        last = rnum
        if rnum > r2 and not walk_all:
            break
        if rnum < r1 or rnum > r2:
            continue
        col = 0
        for cm in _CELL_RE.finditer(m.group(4) or b""):
            ra = _R_ATTR.search(cm.group(2))
            col = col_index(ra.group(1).decode()) if ra else col + 1
            if col < c1:
                continue
            if col > c2:
                break
            sm = _S_ATTR.search(cm.group(2))
            yield rnum, col, int(sm.group(1)) if sm and sm.group(1) else 0, cm.group(2), cm.group(4) or b""


_SHEETDATA_OPEN = re.compile(rb"<(?:(\w+):)?sheetData\b[^>]*?(/?)>")


def _last_row_start(buf: bytes, tag: bytes, end: int | None = None) -> int:
    """Where the last <row> element of buf (before end) starts, or -1."""
    k = buf.rfind(tag, 0, len(buf) if end is None else end)
    while k >= 0:
        if buf[k + len(tag) : k + len(tag) + 1] in (b" ", b">", b"/", b"\t", b"\n", b"\r"):
            return k
        k = buf.rfind(tag, 0, k)
    return -1


def stream_area(pkg: "Package", part: str, r1: int, r2: int, chunk: int = 8 << 20) -> tuple[bytes, bytes]:
    """(head, sheet data) of a worksheet part read as a stream, for rows r1..r2 only: head is the XML before
    <sheetData> (columns, views, dimension); sheet data is a small '<sheetData>…</sheetData>' holding just those
    rows, for area_cells and row_attrs_in. Memory stays at about two chunks plus the rows asked for, however big
    the sheet: chunks wholly before r1 are skipped after reading one row number, and reading stops after r2."""
    head = b""
    prefix = b""
    keep: list[bytes] = []
    last = 0
    buf = b""
    started = False
    row_tag = end_tag = b""
    with pkg.zip.open(part) as fh:
        done = False
        while not done:
            data = fh.read(chunk)
            eof = not data
            buf += data
            if not started:
                m = _SHEETDATA_OPEN.search(buf)
                if m is None:
                    if eof:
                        return buf, b"<sheetData></sheetData>"
                    continue
                prefix = (m.group(1) + b":") if m.group(1) else b""
                head = buf[: m.start()]
                if m.group(2) == b"/":
                    return head, b"<" + prefix + b"sheetData></" + prefix + b"sheetData>"
                buf = buf[m.end() :]
                started = True
                row_tag, end_tag = b"<" + prefix + b"row", b"</" + prefix + b"sheetData>"
            stop = buf.find(end_tag)
            if stop >= 0 or eof:
                seg, buf, done = (buf[:stop] if stop >= 0 else buf), b"", True
            else:
                k = _last_row_start(buf, row_tag)
                if k <= 0:
                    continue  # one row longer than the buffer: read on
                seg, buf = buf[:k], buf[k:]
            lk = _last_row_start(seg, row_tag)
            if lk >= 0:
                lr = _ROW_R_ATTR.search(seg, lk, seg.find(b">", lk) + 1 or len(seg))
                if lr is not None and int(lr.group(1)) < r1:
                    last = int(lr.group(1))
                    continue
            for m in _ROW_RE.finditer(seg):
                rm = _ROW_R_ATTR.search(m.group(2))
                rnum = int(rm.group(1)) if rm else last + 1
                last = rnum
                if rnum > r2:
                    done = True
                    break
                if rnum >= r1:
                    keep.append(m.group(0))
    return head, b"<" + prefix + b"sheetData>" + b"".join(keep) + b"</" + prefix + b"sheetData>"


def shared_subset(pkg: "Package", wanted: set[int]) -> dict[int, str]:
    """The shared strings with these indexes, streaming the table and keeping only them (a huge table is never
    held whole)."""
    if pkg._shared is not None:
        return {i: pkg._shared[i] for i in wanted if 0 <= i < len(pkg._shared)}
    out: dict[int, str] = {}
    path = next((t for (typ, t) in pkg._rels.values() if typ.endswith("/sharedStrings")), None)
    if not wanted or not path or not pkg.has(path):
        return out
    top = max(wanted)
    i = 0
    with pkg.zip.open(path) as f:
        for _ev, el in ET.iterparse(f, events=("end",)):
            if _local(el.tag) != "si":
                continue
            if i in wanted:
                out[i] = _rich_text(el)
            el.clear()
            i += 1
            if i > top:
                break
    return out


def area_values(pkg: "Package", si: "SheetInfo", r1: int, r2: int, c1: int, c2: int) -> dict[tuple[int, int], Any]:
    """{(row, col): value} for an area of a worksheet, read from its XML as a stream (dates typed from the cells'
    number formats, errors as Excel error values). Cheap in time and memory for a few thousand cells of a huge
    sheet."""
    import html

    from _numfmt import date_kind, serial_to_python

    out: dict[tuple[int, int], Any] = {}
    if not si.path or not pkg.has(si.path):
        return out
    raws: list[tuple[int, int, int, str, str]] = []
    for r, c, style, attrs, inner in area_cells(stream_area(pkg, si.path, r1, r2)[1], r1, r2, c1, c2):
        tm = _T_ATTR.search(attrs)
        t = tm.group(0).split(b'"')[1].decode() if tm else "n"
        if t == "inlineStr":
            texts = re.findall(rb"<(?:\w+:)?t\b[^>]*>([^<]*)</", inner)
            raw = html.unescape(b"".join(texts).decode("utf-8", "replace")) if texts else None
        else:
            vm = _V_EL.search(inner)
            raw = None
            if vm is not None and not vm.group(0).endswith(b"/>"):
                raw = html.unescape(re.sub(rb"^<[^>]*>|</[^>]*>$", b"", vm.group(0)).decode("utf-8", "replace"))
        if raw is not None:
            raws.append((r, c, style, t, raw))
    wanted = {int(raw) for _r, _c, _s, t, raw in raws if t == "s" and raw.strip().isdigit()}
    strings = shared_subset(pkg, wanted)
    for r, c, style, t, raw in raws:
        if t == "s":
            v: Any = strings.get(int(raw), "") if raw.strip().isdigit() else ""
        else:
            v = _convert(raw, t, [])
        if v is None:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and style:
            kind = date_kind(pkg.number_format(style))
            if kind:
                try:
                    v = serial_to_python(v, kind, pkg.date1904)
                except (ValueError, OverflowError):
                    pass
        out[(r, c)] = v
    return out


def row_attrs_in(xml: bytes, r1: int, r2: int) -> dict[int, bytes]:
    """Row element attributes for rows r1..r2 (heights, hidden, styles)."""
    m_open = re.search(rb"<(?:(\w+):)?sheetData\b[^>]*?(/?)>", xml)
    if not m_open or m_open.group(2) == b"/":
        return {}
    prefix = (m_open.group(1) + b":") if m_open.group(1) else b""
    start = m_open.end()
    end = xml.find(b"</" + prefix + b"sheetData>", start)
    at, _ = _locate_row(xml, prefix, r1, start, end)
    out = {}
    for m in _ROW_RE.finditer(xml, start if at is None else at, end):
        rm = _ROW_R_ATTR.search(m.group(2))
        if not rm:
            continue
        r = int(rm.group(1))
        if r > r2 and at is not None:
            break
        if r1 <= r <= r2:
            out[r] = m.group(2)
    return out


def join_chunks(data: Any) -> bytes:
    return data if isinstance(data, (bytes, bytearray)) else b"".join(data)


def _row_at(body: bytes, prefix: bytes, pos: int, end: int) -> tuple[int, int] | None:
    """(start, row number) of the first <row> element starting at or after pos (before end); None at the end;
    (-1, 0) when a row has no r attribute (the caller then walks every row)."""
    tag = b"<" + prefix + b"row"
    k = body.find(tag, pos, end)
    while k >= 0:
        nxt = body[k + len(tag) : k + len(tag) + 1]
        if nxt in (b" ", b">", b"/", b"\t", b"\n", b"\r"):
            te = body.find(b">", k)
            m = _ROW_R_ATTR.search(body, k, te)
            if not m:
                return -1, 0
            return k, int(m.group(1))
        k = body.find(tag, k + 1, end)
    return None


def _locate_row(body: bytes, prefix: bytes, r: int, lo: int, end: int) -> tuple[int | None, bool]:
    """Where row r starts (exists=True) or where it belongs (the next row's start or end), searching lo..end."""
    hi = end
    while lo < hi:
        mid = (lo + hi) // 2
        got = _row_at(body, prefix, mid, end)
        if got is not None and got[0] < 0:
            return None, False
        if got is None or got[1] >= r:
            hi = mid
        else:
            lo = got[0] + 1
    got = _row_at(body, prefix, lo, end)
    if got is None:
        return end, False
    if got[0] < 0:
        return None, False
    return got[0], got[1] == r


def _new_row(prefix: bytes, r: int, cells: dict[int, CellPatch], base: Any) -> bytes:
    parts = []
    for c, p in sorted(cells.items()):
        style = p.restyle(base(r, c, None)) if p.restyle else None
        if p.keep:
            if not style:
                continue
            parts.append(b"<" + prefix + b'c r="' + f"{col_letter(c)}{r}".encode() + b'" s="' + str(style).encode() + b'"/>')
            continue
        if p.clear:
            if style:
                parts.append(b"<" + prefix + b'c r="' + f"{col_letter(c)}{r}".encode() + b'" s="' + str(style).encode() + b'"/>')
            continue
        parts.append(_new_cell(prefix, r, c, p, style))
    inner = b"".join(parts)
    if not inner:
        return b""
    return b"<" + prefix + b'row r="' + str(r).encode() + b'">' + inner + b"</" + prefix + b"row>"


def _new_cell(prefix: bytes, r: int, c: int, p: CellPatch, style: int | None = None) -> bytes:
    attrs = b' r="' + f"{col_letter(c)}{r}".encode() + b'"' + (b' s="' + str(style).encode() + b'"' if style else b"")
    if p.keep or (p.clear and not p.formula):
        return b"<" + prefix + b"c" + attrs + b"/>"
    return b"<" + prefix + b"c" + attrs + _content(prefix, p, b"") + b"</" + prefix + b"c>"


def _content(prefix: bytes, p: CellPatch, f_xml: bytes) -> bytes:
    """The attribute tail (t=…) and children of a patched cell, starting with '>'."""
    if p.formula is not None:
        return b"><" + prefix + b"f>" + _xml_text(p.formula).encode("utf-8") + b"</" + prefix + b"f>"
    if p.const and type(p.value) is str:
        return b' t="inlineStr"><' + prefix + b'is><' + prefix + b't xml:space="preserve">' + _xml_text(p.value).encode("utf-8") + b"</" + prefix + b"t></" + prefix + b"is>"
    t, v = value_xml(p.value)
    if p.const and t == "str":
        t = "inlineStr"
    head = (b' t="' + t.encode() + b'"') if t else b""
    return head + b">" + f_xml + b"<" + prefix + b"v>" + v.encode("utf-8") + b"</" + prefix + b"v>"


def _patch_row(m: re.Match[bytes], rnum: int, cells: dict[int, CellPatch], prefix: bytes, base: Any = None) -> bytes:
    rprefix = m.group(1)
    attrs = m.group(2)
    inner = m.group(4) or b""
    todo = dict(cells)
    out: list[bytes] = []
    pos = 0
    col = 0
    base = base or (lambda r, c, a: 0)

    def new(c: int, p: CellPatch) -> bytes:
        style = p.restyle(base(rnum, c, attrs)) if p.restyle else None
        if (p.keep or p.clear) and not style:
            return b""
        return _new_cell(prefix, rnum, c, p, style)

    for cm in _CELL_RE.finditer(inner):
        ra = _R_ATTR.search(cm.group(2))
        col = col_index(ra.group(1).decode()) if ra else col + 1
        for c in sorted(k for k in todo if k < col):
            p = todo.pop(c)
            got = new(c, p)
            if got:
                out.append(inner[pos : cm.start()])
                pos = cm.start()
                out.append(got)
        if col in todo:
            out.append(inner[pos : cm.start()])
            out.append(_patch_cell(cm, todo.pop(col), prefix))
            pos = cm.end()
    out.append(inner[pos:])
    for c in sorted(todo):
        got = new(c, todo[c])
        if got:
            out.append(got)
    rp = (rprefix + b":") if rprefix else b""
    return b"<" + rp + b"row" + attrs + b">" + b"".join(out) + b"</" + rp + b"row>"


def _patch_cell(m: re.Match[bytes], p: CellPatch, prefix: bytes) -> bytes:
    cprefix = m.group(1)
    attrs = m.group(2)
    inner = m.group(4) or b""
    cp = (cprefix + b":") if cprefix else b""
    if p.restyle is not None:
        sm = _S_ATTR.search(attrs)
        old = int(sm.group(1)) if sm and sm.group(1) else 0
        attrs = _with_style(attrs, p.restyle(old))
    if p.keep:
        return b"<" + cp + b"c" + attrs + (b">" + inner + b"</" + cp + b"c>" if inner else b"/>")
    attrs = _T_ATTR.sub(b"", attrs)
    if p.cm is not None:
        attrs = _CM_ATTR.sub(b"", attrs)
        if p.cm:
            attrs += b' cm="1"'
    fm = _F_EL.search(inner)
    if p.clear and (fm is None or p.const):
        # a cleared or stale spilled cell: keep only its style
        return b"<" + cp + b"c" + attrs + b"/>"
    if p.const or p.formula is not None:
        return b"<" + cp + b"c" + attrs + _content(cp, p, b"") + b"</" + cp + b"c>"
    f_xml = b""
    if fm is not None:
        if p.f_attrs is not None:
            text = fm.group(4) or b""
            fp = (fm.group(1) + b":") if fm.group(1) else b""
            f_xml = b"<" + fp + b"f" + p.f_attrs + b">" + text + b"</" + fp + b"f>"
        else:
            f_xml = fm.group(0)
    return b"<" + cp + b"c" + attrs + _content(cp, p, f_xml) + b"</" + cp + b"c>"


_METADATA_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    b'xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray">'
    b'<metadataTypes count="1"><metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1" '
    b'pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" '
    b'coerce="1" cellMeta="1"/></metadataTypes><futureMetadata name="XLDAPR" count="1"><bk><extLst>'
    b'<ext uri="{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}"><xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/>'
    b"</ext></extLst></bk></futureMetadata><cellMetadata count=\"1\"><bk><rc t=\"1\" v=\"0\"/></bk></cellMetadata></metadata>"
)


def build_patches(book: Book, extra: dict[str, Any]) -> dict[int, dict[tuple[int, int], CellPatch]]:
    """Per sheet index: the cell patches that store the engine's results."""
    out: dict[int, dict[tuple[int, int], CellPatch]] = {}
    old_spills = extra.get("old_spills", {})
    for sh in book.sheets:
        patches: dict[tuple[int, int], CellPatch] = {}
        for (r, c), fc in sh.formulas.items():
            if fc.array_ref is not None and fc.array_value is not None:
                r1, c1, r2, c2 = fc.array_ref
                arr = fc.array_value
                for rr in range(r1, r2 + 1):
                    for cc in range(c1, c2 + 1):
                        v = _array_at(arr, rr - r1, cc - c1)
                        patches[(rr, cc)] = CellPatch(v)
                continue
            if fc.dynamic:
                old = old_spills.get((sh.index, r, c))
                if fc.spill is not None and fc.array_value is not None:
                    r1, c1, r2, c2 = fc.spill
                    ref = f"{col_letter(c1)}{r1}:{col_letter(c2)}{r2}"
                    patches[(r, c)] = CellPatch(fc.value, f_attrs=b' t="array" ref="' + ref.encode() + b'"', cm=True)
                    for rr in range(r1, r2 + 1):
                        for cc in range(c1, c2 + 1):
                            if (rr, cc) != (r, c):
                                patches[(rr, cc)] = CellPatch(fc.array_value.rows[rr - r1][cc - c1])
                else:
                    ref = f"{col_letter(c)}{r}"
                    patches[(r, c)] = CellPatch(fc.value, f_attrs=b' t="array" ref="' + ref.encode() + b'"', cm=True)
                if old:
                    a1, b1, a2, b2 = old
                    new = fc.spill or (r, c, r, c)
                    for rr in range(a1, a2 + 1):
                        for cc in range(b1, b2 + 1):
                            if not (new[0] <= rr <= new[2] and new[1] <= cc <= new[3]) and (rr, cc) not in sh.formulas and (rr, cc) not in sh.values:
                                patches[(rr, cc)] = CellPatch(clear=True)
                continue
            patches[(r, c)] = CellPatch(fc.value)
        if patches:
            out[sh.index] = patches
    return out


def _array_at(arr: Array, i: int, j: int) -> Any:
    h, w = arr.height, arr.width
    if h == 1 and w == 1:
        return arr.rows[0][0]
    if h == 1:
        return arr.rows[0][j] if j < w else ERRORS["#N/A"]
    if w == 1:
        return arr.rows[i][0] if i < h else ERRORS["#N/A"]
    if i < h and j < w:
        return arr.rows[i][j]
    return ERRORS["#N/A"]


def write_values(src: str | Path, dest: str | Path, pkg: Package, book: Book, extra: dict[str, Any], full_calc_on_load: bool | None = None) -> dict[str, Any]:
    """Copies the workbook to dest with every formula cell's cached value set (and spills stored as dynamic arrays)."""
    patches = build_patches(book, extra)
    needs_meta = any(p.cm for ps in patches.values() for p in ps.values())
    meta_path = next((t for (typ, t) in pkg._rels.values() if typ == REL_METADATA), None)
    meta_ok = False
    if meta_path and pkg.has(meta_path):
        meta_ok = b"XLDAPR" in pkg.read(meta_path)
        if not meta_ok:
            for ps in patches.values():
                for p in ps.values():
                    if p.cm:
                        p.cm = False
    add_meta = needs_meta and not meta_path
    calc_chain = next((t for (typ, t) in pkg._rels.values() if typ.endswith("/calcChain")), None)
    sheet_parts = {pkg.sheets[i].path: ps for i, ps in patches.items() if pkg.sheets[i].path}
    dest = Path(dest)
    tmp = temp_beside(dest)

    def edit(name: str) -> bytes | None:
        if name in sheet_parts:
            data = patch_sheet_xml(pkg.zip.read(name), sheet_parts[name], chunks=True)
            data[0] = _fix_dimension(bytes(data[0]), book, pkg, name)
            return data
        if name == "[Content_Types].xml" and (calc_chain or add_meta):
            data = pkg.zip.read(name)
            if calc_chain:
                data = re.sub(rb'<Override[^>]*PartName="/' + re.escape(calc_chain.encode()) + rb'"[^>]*/>', b"", data)
            if add_meta and b"sheetMetadata+xml" not in data:
                data = data.replace(b"</Types>", b'<Override PartName="/xl/metadata.xml" ContentType="' + CT_METADATA.encode() + b'"/></Types>')
            return data
        if name == _rels_name(pkg.workbook_path) and (calc_chain or add_meta):
            data = pkg.zip.read(name)
            if calc_chain:
                data = re.sub(rb'<Relationship[^>]*Type="[^"]*/calcChain"[^>]*/>', b"", data)
            if add_meta:
                rid = _free_rid(data)
                data = data.replace(b"</Relationships>", b'<Relationship Id="' + rid + b'" Type="' + REL_METADATA.encode() + b'" Target="metadata.xml"/></Relationships>')
            return data
        if name == pkg.workbook_path and full_calc_on_load is not None:
            return _set_full_calc(pkg.zip.read(name), full_calc_on_load)
        return None

    try:
        rewrite_zip(pkg.zip, tmp, edit, add={"xl/metadata.xml": _METADATA_XML} if add_meta else None, drop={calc_chain} if calc_chain else None)
        publish(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"cells_written": sum(len(p) for p in patches.values()), "dynamic_arrays": needs_meta}


def rewrite_zip(src: zipfile.ZipFile, dest: Path, edit: Any, add: dict[str, bytes] | None = None, drop: set[str] | None = None) -> None:
    """Copies a zip, calling edit(name) for each member: None keeps it byte for byte (its compressed data is copied
    raw, never inflated), bytes replace it. Big replaced parts are compressed at a fast level."""
    import shutil
    import struct

    drop = drop or set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in src.infolist():
            name = info.filename
            if name in drop:
                continue
            data = edit(name)
            if data is None and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and not info.flag_bits & 0x1:
                # raw copy: local header + compressed bytes, no recompression
                src.fp.seek(info.header_offset)  # type: ignore[union-attr]
                head = src.fp.read(30)  # type: ignore[union-attr]
                if head[:4] != b"PK\x03\x04":
                    data = src.read(name)
                else:
                    n, m = struct.unpack("<HH", head[26:30])
                    src.fp.seek(info.header_offset + 30 + n + m)  # type: ignore[union-attr]
                    zi = zipfile.ZipInfo(name, date_time=info.date_time)
                    zi.compress_type = info.compress_type
                    zi.external_attr = info.external_attr
                    zi.create_system = info.create_system
                    zi.CRC = info.CRC
                    zi.compress_size = info.compress_size
                    zi.file_size = info.file_size
                    zi.flag_bits = info.flag_bits & ~0x08 & ~0x800 | (0x800 if not name.isascii() else 0)
                    zi.header_offset = zout.fp.tell()  # type: ignore[union-attr]
                    zip64 = info.file_size > 0xFFFFFFFF or info.compress_size > 0xFFFFFFFF
                    zout.fp.write(zi.FileHeader(zip64))  # type: ignore[union-attr]
                    left = info.compress_size
                    while left > 0:
                        block = src.fp.read(min(left, 4 << 20))  # type: ignore[union-attr]
                        if not block:
                            raise zipfile.BadZipFile(f"{name} is truncated")
                        zout.fp.write(block)  # type: ignore[union-attr]
                        left -= len(block)
                    zout.filelist.append(zi)
                    zout.NameToInfo[name] = zi
                    zout.start_dir = zout.fp.tell()  # type: ignore[union-attr]
                    continue
            if data is None:
                data = src.read(name)
            zi = zipfile.ZipInfo(name, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            if isinstance(data, list):
                size = sum(len(x) for x in data)
                level = 1 if size > (16 << 20) else 6
                for attr in ("compress_level", "_compresslevel"):  # 3.13+ / 3.8-3.12
                    try:
                        setattr(zi, attr, level)
                        break
                    except AttributeError:
                        continue
                with zout.open(zi, "w", force_zip64=size > (1 << 30)) as w:
                    for piece in data:
                        w.write(piece)
                continue
            zout.writestr(zi, data, compresslevel=1 if len(data) > (16 << 20) else 6)
        for name, data in (add or {}).items():
            zout.writestr(name, data)
    del shutil


def _rels_name(part: str) -> str:
    d, base = posixpath.split(part)
    return posixpath.join(d, "_rels", base + ".rels")


def _free_rid(rels: bytes) -> bytes:
    used = {int(m) for m in re.findall(rb'Id="rId(\d+)"', rels)}
    n = 1
    while n in used:
        n += 1
    return f"rId{n}".encode()


def _set_full_calc(data: bytes, on: bool) -> bytes:
    m = re.search(rb"<(?:\w+:)?calcPr\b[^>]*?/?>", data)
    if not m:
        if not on:
            return data
        root = re.search(rb"<(\w+:)?workbook\b", data)
        px = (root.group(1) or b"") if root else b""
        el = b"<" + px + b'calcPr fullCalcOnLoad="1"/>'
        # schema order: calcPr follows definedNames / externalReferences / functionGroups / sheets
        for tag in (b"definedNames", b"externalReferences", b"functionGroups", b"sheets"):
            end = re.search(rb"</(?:\w+:)?" + tag + rb">", data)
            if end:
                return data[: end.end()] + el + data[end.end() :]
        return re.sub(rb"(</(?:\w+:)?workbook>)", el + b"\\1", data, count=1)
    el = m.group(0)
    el2 = re.sub(rb'(?<!\s)\s+fullCalcOnLoad="[^"]*"', b"", el)
    if on:
        el2 = el2.replace(b"calcPr", b'calcPr fullCalcOnLoad="1"', 1)
    return data[: m.start()] + el2 + data[m.end() :]


def _fix_dimension(xml: bytes, book: Book, pkg: Package, part: str) -> bytes:
    idx = next((s.index for s in pkg.sheets if s.path == part), None)
    if idx is None:
        return xml
    sh = book.sheets[idx]
    if sh.max_row < 1 or sh.max_col < 1:
        return xml
    m = re.search(rb'<(?:\w+:)?dimension\b[^>]*\bref="([^"]*)"', xml)
    if not m:
        return xml
    try:
        r1, c1, r2, c2 = parse_range(m.group(1).decode())
    except ValueError:
        return xml
    nr2, nc2 = max(r2, sh.max_row), max(c2, sh.max_col)
    if (nr2, nc2) == (r2, c2):
        return xml
    new = f"{col_letter(c1)}{r1}:{col_letter(nc2)}{nr2}".encode()
    return xml[: m.start(1)] + new + xml[m.end(1) :]


def recalc_file(src: str | Path, dest: str | Path | None = None, now: Any = None, full_calc_on_load: bool | None = None) -> dict[str, Any]:
    """Loads, recalculates and (when dest is given) writes a workbook with fresh cached values. Returns the report."""
    from _formula import Engine

    book, pkg, extra = load_book(src)
    try:
        engine = Engine(book, now=now)
        report = engine.recalc()
        report["data_tables"] = extra.get("data_tables", 0)
        if dest is not None:
            report.update(write_values(src, dest, pkg, book, extra, full_calc_on_load))
        report["_book"] = book
        return report
    finally:
        pkg.close()
