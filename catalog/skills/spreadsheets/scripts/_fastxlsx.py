"""A streaming .xlsx writer for big tables: rows go straight to a temp XML file, never into an object model.

openpyxl needs about 25 µs and several hundred bytes per cell, so a 300 000 × 10 CSV took 77 s and 1.6 GB. This
writer needs about 1 µs per cell and memory only for the distinct strings (shared-string table), so the same table
takes a few seconds. It writes what a data export needs: typed values (numbers, text, booleans, dates as serials
with a date format), a styled header (bold white on dark blue, centred, wrapped), per-column number formats,
column widths, a frozen header row, an autofilter or an Excel table, and an optional totals row whose SUBTOTAL
formulas carry their computed values. Standard library only.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from _a1 import col_letter

_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_EPOCH = _dt.datetime(1899, 12, 30)


def _esc(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if _BAD.search(s):
        s = _BAD.sub(lambda m: f"_x{ord(m.group(0)):04X}_", s)
    return s


def serial(v: Any) -> float:
    """Excel 1900-system serial of a date, datetime or time."""
    if isinstance(v, _dt.datetime):
        d = v - _EPOCH
        return d.days + d.seconds / 86400 + d.microseconds / 86_400_000_000
    if isinstance(v, _dt.date):
        return float((v - _EPOCH.date()).days)
    if isinstance(v, _dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6) / 86400
    raise TypeError(v)


class Column:
    """How to write one column: header text, number format and width (characters)."""

    __slots__ = ("header", "number_format", "width", "total")

    def __init__(self, header: str, number_format: str | None = None, width: float | None = None, total: str | None = None) -> None:
        self.header, self.number_format, self.width, self.total = header, number_format, width, total


class SheetWriter:
    """Writes one worksheet's rows to a temp file; call row() for each data row, then the book assembles the zip."""

    def __init__(self, book: "FastBook", name: str, columns: Sequence[Column], header: bool = True, freeze: bool = True, autofilter: bool = True, table: str | None = None, table_style: str = "TableStyleMedium2") -> None:
        self.book, self.name, self.columns = book, name, list(columns)
        self.header, self.freeze, self.autofilter, self.table, self.table_style = header, freeze, autofilter, table, table_style
        fd, tmp = tempfile.mkstemp(prefix="desk-fastxlsx-", suffix=".xml")
        os.close(fd)
        self.path = Path(tmp)
        self.fh = open(self.path, "w", encoding="utf-8", newline="")  # noqa: SIM115 — closed in finish()
        self.r = 0
        self.ncols = len(self.columns)
        self.letters = [col_letter(j + 1) for j in range(max(self.ncols, 1))]
        self.date_style = [self.book.style_for(c.number_format or "yyyy-mm-dd", False) for c in self.columns]
        self.num_style = [self.book.style_for(c.number_format, False) if c.number_format else 0 for c in self.columns]
        self.dt_style = self.book.style_for("yyyy-mm-dd hh:mm", False)
        self.time_style = self.book.style_for("hh:mm:ss", False)
        self.sums: list[float] = [0.0] * self.ncols
        self.counts: list[int] = [0] * self.ncols
        self.mins: list[float] = [math.inf] * self.ncols
        self.maxs: list[float] = [-math.inf] * self.ncols
        self.widths: list[float] = [0.0] * self.ncols
        self.totals_row = 0
        if header:
            hs = self.book.header_style()
            cells = []
            for j, c in enumerate(self.columns):
                cells.append(f'<c r="{self.letters[j]}1" s="{hs}" t="s"><v>{self.book.sst(str(c.header))}</v></c>')
                self.widths[j] = max(self.widths[j], _text_width(str(c.header)) * 1.1 + 3.0)
            self.r = 1
            self.fh.write('<row r="1">' + "".join(cells) + "</row>")

    def row(self, values: Sequence[Any]) -> None:
        self.r += 1
        r = self.r
        out = []
        sst = self.book.sst
        sample = r < 2000
        for j, v in enumerate(values):
            if v is None or v == "":
                continue
            if j >= self.ncols:
                break
            ref = f"{self.letters[j]}{r}"
            t = type(v)
            if t is bool:
                out.append(f'<c r="{ref}" t="b"><v>{1 if v else 0}</v></c>')
            elif t is int or t is float:
                if t is float and (math.isnan(v) or math.isinf(v)):
                    out.append(f'<c r="{ref}" t="e"><v>#NUM!</v></c>')
                    continue
                s = self.num_style[j]
                out.append(f'<c r="{ref}"' + (f' s="{s}"' if s else "") + f"><v>{v!r}</v></c>" if t is float else f'<c r="{ref}"' + (f' s="{s}"' if s else "") + f"><v>{v}</v></c>")
                self.sums[j] += v
                self.counts[j] += 1
                if v < self.mins[j]:
                    self.mins[j] = v
                if v > self.maxs[j]:
                    self.maxs[j] = v
            elif t is _dt.datetime:
                s = self.date_style[j] if self.columns[j].number_format else (self.dt_style if (v.hour or v.minute or v.second) else self.date_style[j])
                out.append(f'<c r="{ref}" s="{s}"><v>{serial(v)!r}</v></c>')
            elif t is _dt.date:
                out.append(f'<c r="{ref}" s="{self.date_style[j]}"><v>{serial(v):.0f}</v></c>')
            elif t is _dt.time:
                out.append(f'<c r="{ref}" s="{self.time_style}"><v>{serial(v)!r}</v></c>')
            else:
                s = str(v)
                if hasattr(v, "code") and s.startswith("#"):
                    out.append(f'<c r="{ref}" t="e"><v>{_esc(s)}</v></c>')
                    continue
                out.append(f'<c r="{ref}" t="s"><v>{sst(s)}</v></c>')
            if sample:
                w = _text_width(_shown(v, self.columns[j].number_format))
                if w > self.widths[j]:
                    self.widths[j] = w
        self.fh.write(f'<row r="{r}">' + "".join(out) + "</row>")

    def add_totals(self, label: str = "Total") -> None:
        """A totals row: SUBTOTAL formulas (with their values) under the columns that have a total."""
        codes = {"sum": 109, "average": 101, "count": 103, "max": 104, "min": 105}
        first, last = 2 if self.header else 1, self.r
        self.r += 1
        r = self.r
        self.totals_row = r
        bold = self.book.style_for(None, True)
        out = []
        labelled = False
        for j, c in enumerate(self.columns):
            ref = f"{self.letters[j]}{r}"
            fn = (c.total or "").lower()
            if fn in codes:
                L = self.letters[j]
                val: float = {"sum": self.sums[j], "average": self.sums[j] / self.counts[j] if self.counts[j] else 0.0, "count": float(self.counts[j]), "max": self.maxs[j] if self.counts[j] else 0.0, "min": self.mins[j] if self.counts[j] else 0.0}[fn]
                s = self.book.style_for(c.number_format, True)
                out.append(f'<c r="{ref}" s="{s}"><f>SUBTOTAL({codes[fn]},{L}{first}:{L}{last})</f><v>{val!r}</v></c>')
            elif not labelled and j == 0:
                out.append(f'<c r="{ref}" s="{bold}" t="s"><v>{self.book.sst(label)}</v></c>')
                labelled = True
        self.fh.write(f'<row r="{r}">' + "".join(out) + "</row>")

    def finish(self) -> None:
        self.fh.close()


def _shown(v: Any, fmt: str | None) -> str:
    if isinstance(v, float):
        if fmt and "%" in fmt:
            return f"{v * 100:.1f}%"
        if fmt and ("#,##0" in fmt or "$" in fmt):
            return f"${v:,.2f}"
        return f"{v:.10g}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}" if fmt and "#,##0" in fmt else str(v)
    if isinstance(v, _dt.datetime):
        return "2024-01-01 00:00" if (v.hour or v.minute) else "2024-01-01"
    if isinstance(v, _dt.date):
        return "2024-01-01"
    return str(v)


def _text_width(s: str) -> float:
    w = 0.0
    for ch in s.split("\n")[0][:200]:
        if ch in "il.,:;|!'`":
            w += 0.5
        elif ch in "mwMW@%":
            w += 1.3
        elif ch.isupper():
            w += 1.15
        elif ord(ch) > 0x2E80:
            w += 2.0
        else:
            w += 1.0
    return w


class FastBook:
    """A workbook assembled from SheetWriters: shared strings, styles, tables, one zip at the end."""

    def __init__(self) -> None:
        self._sst: dict[str, int] = {}
        self._sst_list: list[str] = []
        self._sst_refs = 0
        self._numfmts: dict[str, int] = {}
        self._xfs: list[tuple[int, bool, bool]] = [(0, False, False)]  # (numFmtId, bold, header)
        self._xf_index: dict[tuple[int, bool, bool], int] = {(0, False, False): 0}
        self.sheets: list[SheetWriter] = []

    def sst(self, s: str) -> int:
        self._sst_refs += 1
        i = self._sst.get(s)
        if i is None:
            i = len(self._sst_list)
            self._sst[s] = i
            self._sst_list.append(s)
        return i

    def _numfmt_id(self, code: str | None) -> int:
        builtin = {None: 0, "General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10, "yyyy-mm-dd": 0, "mm-dd-yy": 14, "h:mm": 20, "hh:mm:ss": 0}
        if code in builtin and builtin[code]:
            return builtin[code]
        if not code or code == "General":
            return 0
        if code not in self._numfmts:
            self._numfmts[code] = 164 + len(self._numfmts)
        return self._numfmts[code]

    def style_for(self, number_format: str | None, bold: bool, header: bool = False) -> int:
        key = (self._numfmt_id(number_format), bold, header)
        i = self._xf_index.get(key)
        if i is None:
            i = len(self._xfs)
            self._xfs.append(key)
            self._xf_index[key] = i
        return i

    def header_style(self) -> int:
        return self.style_for(None, True, True)

    def add_sheet(self, name: str, columns: Sequence[Column], **kw: Any) -> SheetWriter:
        sw = SheetWriter(self, name, columns, **kw)
        self.sheets.append(sw)
        return sw

    # ── assembly ──────────────────────────────────────────────────────

    def _styles_xml(self) -> str:
        fmts = "".join(f'<numFmt numFmtId="{i}" formatCode="{_esc(code).replace(chr(34), "&quot;")}"/>' for code, i in self._numfmts.items())
        xfs = []
        for nid, bold, header in self._xfs:
            font = 2 if header else (1 if bold else 0)
            fill = 2 if header else 0
            attrs = f'numFmtId="{nid}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0"'
            if nid:
                attrs += ' applyNumberFormat="1"'
            if font:
                attrs += ' applyFont="1"'
            if header:
                xfs.append(f'<xf {attrs} applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>')
            else:
                xfs.append(f"<xf {attrs}/>")
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + (f'<numFmts count="{len(self._numfmts)}">{fmts}</numFmts>' if self._numfmts else "")
            + '<fonts count="3"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(xfs)}">' + "".join(xfs) + "</cellXfs>"
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
            "</styleSheet>"
        )

    def _sst_xml_to(self, fh: Any) -> None:
        fh.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{self._sst_refs}" uniqueCount="{len(self._sst_list)}">'.encode())
        buf = []
        for s in self._sst_list:
            t = _esc(s)
            sp = ' xml:space="preserve"' if t[:1].isspace() or t[-1:].isspace() or "\n" in t else ""
            buf.append(f"<si><t{sp}>{t}</t></si>")
            if len(buf) >= 4096:
                fh.write("".join(buf).encode("utf-8"))
                buf = []
        fh.write(("".join(buf) + "</sst>").encode("utf-8"))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        for sw in self.sheets:
            if not sw.fh.closed:
                sw.finish()
        n = len(self.sheets)
        tables: list[tuple[int, int, SheetWriter]] = []  # (table id, sheet index, writer)
        for i, sw in enumerate(self.sheets):
            if sw.table and sw.header and sw.r >= 2:
                tables.append((len(tables) + 1, i, sw))
        ct = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        ]
        for i in range(n):
            ct.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        for tid, _i, _sw in tables:
            ct.append(f'<Override PartName="/xl/tables/table{tid}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>')
        ct.append("</Types>")
        sheets_xml = "".join(f'<sheet name="{_esc(sw.name).replace(chr(34), "&quot;")}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, sw in enumerate(self.sheets))
        defined = []
        for i, sw in enumerate(self.sheets):
            if sw.autofilter and sw.header and not sw.table and sw.r >= 1:
                q = "'" + sw.name.replace("'", "''") + "'"
                last = sw.totals_row - 1 if sw.totals_row else sw.r
                defined.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">{_esc(q)}!$A$1:${sw.letters[-1]}${max(last, 1)}</definedName>')
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr/>'
            '<bookViews><workbookView activeTab="0"/></bookViews>'
            f"<sheets>{sheets_xml}</sheets>" + (f"<definedNames>{''.join(defined)}</definedNames>" if defined else "") + '<calcPr calcId="191029"/></workbook>'
        )
        wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        for i in range(n):
            wb_rels.append(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>')
        wb_rels.append(f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        wb_rels.append(f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>')
        wb_rels.append("</Relationships>")
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'
        )
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        core = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:creator>Desk</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'
        )
        app = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Microsoft Excel</Application></Properties>'
        fd, tmp_name = tempfile.mkstemp(prefix=".desk-fastxlsx-", suffix=".xlsx", dir=str(path.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
                z.writestr("[Content_Types].xml", "".join(ct))
                z.writestr("_rels/.rels", root_rels)
                z.writestr("docProps/core.xml", core)
                z.writestr("docProps/app.xml", app)
                z.writestr("xl/workbook.xml", workbook)
                z.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
                z.writestr("xl/styles.xml", self._styles_xml())
                with z.open("xl/sharedStrings.xml", "w", force_zip64=True) as fh:
                    self._sst_xml_to(fh)
                table_of = {i: tid for tid, i, _ in tables}
                for i, sw in enumerate(self.sheets):
                    last_col = sw.letters[-1]
                    dim = f"A1:{last_col}{max(sw.r, 1)}"
                    views = '<sheetViews><sheetView workbookViewId="0"' + (' tabSelected="1"' if i == 0 else "") + ">"
                    if sw.freeze and sw.header and sw.r >= 2:
                        views += '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
                    views += "</sheetView></sheetViews>"
                    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{min(max(c.width or sw.widths[j] + 2, 6.0), 60.0):.1f}" customWidth="1"/>' for j, c in enumerate(sw.columns))
                    head = (
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                        f'<dimension ref="{dim}"/>{views}<sheetFormatPr defaultRowHeight="15"/>' + (f"<cols>{cols}</cols>" if cols else "") + "<sheetData>"
                    )
                    tail = "</sheetData>"
                    last_data = sw.totals_row - 1 if sw.totals_row else sw.r
                    if sw.autofilter and sw.header and not sw.table and sw.r >= 1:
                        tail += f'<autoFilter ref="A1:{last_col}{max(last_data, 1)}"/>'
                    tail += '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
                    if i in table_of:
                        tail += '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>'
                    tail += "</worksheet>"
                    with z.open(f"xl/worksheets/sheet{i + 1}.xml", "w", force_zip64=True) as fh:
                        fh.write(head.encode("utf-8"))
                        with open(sw.path, "rb") as src:
                            while True:
                                block = src.read(4 << 20)
                                if not block:
                                    break
                                fh.write(block)
                        fh.write(tail.encode("utf-8"))
                    if i in table_of:
                        tid = table_of[i]
                        z.writestr(f"xl/worksheets/_rels/sheet{i + 1}.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                   f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/table" Target="../tables/table{tid}.xml"/></Relationships>')
                        z.writestr(f"xl/tables/table{tid}.xml", self._table_xml(tid, sw))
            _publish(tmp, path)
        finally:
            for sw in self.sheets:
                try:
                    sw.path.unlink()
                except OSError:
                    pass
            if tmp.exists():
                tmp.unlink()

    def _table_xml(self, tid: int, sw: SheetWriter) -> str:
        last_col = sw.letters[-1]
        ref = f"A1:{last_col}{sw.r}"
        totals = bool(sw.totals_row)
        names = []
        seen: set[str] = set()
        for j, c in enumerate(sw.columns):
            base = str(c.header).strip() or f"Column{j + 1}"
            n, k = base, 2
            while n.lower() in seen:
                n = f"{base}{k}"
                k += 1
            seen.add(n.lower())
            names.append(n)
        codes = {"sum": "sum", "average": "average", "count": "count", "max": "max", "min": "min"}
        cols = []
        for j, (n, c) in enumerate(zip(names, sw.columns)):
            fn = codes.get((c.total or "").lower())
            extra = f' totalsRowFunction="{fn}"' if totals and fn else (' totalsRowLabel="Total"' if totals and j == 0 else "")
            cols.append(f'<tableColumn id="{j + 1}" name="{_esc(n).replace(chr(34), "&quot;")}"{extra}/>')
        filt_ref = f"A1:{last_col}{sw.r - 1 if totals else sw.r}"
        name = re.sub(r"[^A-Za-z0-9_]", "_", sw.table or f"Table{tid}") or f"Table{tid}"
        if not re.match(r"[A-Za-z_]", name):
            name = "T_" + name
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="{tid}" name="{name}" displayName="{name}" ref="{ref}"'
            + (' totalsRowCount="1"' if totals else "") + ">"
            f'<autoFilter ref="{filt_ref}"/><tableColumns count="{len(cols)}">' + "".join(cols) + "</tableColumns>"
            f'<tableStyleInfo name="{sw.table_style}" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>'
        )


def _publish(tmp: Path, dest: Path) -> None:
    os.replace(tmp, dest)
    try:
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(dest, 0o666 & ~mask)
    except OSError:
        pass


def write_table(path: str | Path, sheet: str, header: Sequence[str], rows: Iterable[Sequence[Any]], formats: Sequence[str | None] | None = None, table: str | None = None, totals: Sequence[str | None] | None = None) -> int:
    """Convenience: one sheet from a header and an iterable of rows. Returns the number of data rows."""
    cols = [Column(h, (formats[j] if formats and j < len(formats) else None), None, (totals[j] if totals and j < len(totals) else None)) for j, h in enumerate(header)]
    book = FastBook()
    sw = book.add_sheet(sheet, cols, table=table)
    n = 0
    try:
        for r in rows:
            sw.row(r)
            n += 1
        if totals and any(totals):
            sw.add_totals()
    except BaseException:
        sw.finish()
        try:
            sw.path.unlink()
        except OSError:
            pass
        raise
    book.save(path)
    return n
