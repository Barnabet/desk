"""OpenDocument spreadsheets without LibreOffice: read formulas from .ods, write .ods (values, formulas translated
to OpenFormula with cached results, number formats, bold/italic/fills, merged cells, column widths). Standard library.
"""

from __future__ import annotations

import datetime as _dt
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from _a1 import col_letter, quote_sheet
from _formula import FormulaSyntaxError, strip_prefixes, tokenize

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
}

# Functions LibreOffice stores with a COM.MICROSOFT. prefix in OpenDocument files.
_MS_PREFIXED = set(
    "IFS SWITCH MAXIFS MINIFS CONCAT TEXTJOIN XLOOKUP XMATCH FILTER SORT SORTBY UNIQUE SEQUENCE RANDARRAY LET "
    "TEXTBEFORE TEXTAFTER TEXTSPLIT VSTACK HSTACK TOCOL TOROW TAKE DROP CHOOSECOLS CHOOSEROWS WRAPROWS WRAPCOLS EXPAND".split()
)

# Functions whose OpenFormula name differs from Excel's (OpenFormula → Excel).
_ODF_TO_XL = {
    "FORMULA": "FORMULATEXT", "USDOLLAR": "DOLLAR", "LEGACY.CHIDIST": "CHIDIST", "LEGACY.CHIINV": "CHIINV",
    "LEGACY.CHITEST": "CHITEST", "LEGACY.FDIST": "FDIST", "LEGACY.FINV": "FINV", "LEGACY.NORMSDIST": "NORMSDIST",
    "LEGACY.NORMSINV": "NORMSINV", "LEGACY.TDIST": "TDIST", "LEGACY.TINV": "TINV", "LEGACY.FTEST": "FTEST",
}
_XL_TO_ODF = {v: k for k, v in _ODF_TO_XL.items()}
_ODF_NAME = re.compile(r"(?i)(?<![\w.])(" + "|".join(re.escape(k) for k in sorted(_ODF_TO_XL, key=len, reverse=True)) + r")(?=\s*\()")


def _rename_functions(text: str) -> str:
    """Applies _ODF_TO_XL outside string literals."""
    parts = re.split(r'("(?:[^"]|"")*")', text)
    return "".join(p if i % 2 else _ODF_NAME.sub(lambda m: _ODF_TO_XL[m.group(1).upper()], p) for i, p in enumerate(parts))


# ── formulas: Excel ↔ OpenFormula ───────────────────────────────────────


def _odf_ref(info: Any) -> str:
    sheet = ""
    if info.sheet is not None:
        sheet = "$" + (info.sheet if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", info.sheet) else "'" + info.sheet.replace("'", "''") + "'")
    def cell(r: int, c: int, ar: bool, ac: bool) -> str:
        return f"{'$' if ac else ''}{col_letter(c)}{'$' if ar else ''}{r}"

    if info.kind == "cell":
        return f"[{sheet}.{cell(info.r1, info.c1, info.ar1, info.ac1)}]"
    if info.kind == "cols":
        return f"[{sheet}.{'$' if info.ac1 else ''}{col_letter(info.c1)}:.{'$' if info.ac2 else ''}{col_letter(info.c2)}]"
    if info.kind == "rows":
        return f"[{sheet}.{'$' if info.ar1 else ''}{info.r1}:.{'$' if info.ar2 else ''}{info.r2}]"
    return f"[{sheet}.{cell(info.r1, info.c1, info.ar1, info.ac1)}:.{cell(info.r2, info.c2, info.ar2, info.ac2)}]"


def excel_to_odf(formula: str) -> str | None:
    """'=SUM(Sheet2!A1:B3;…)' → 'of:=SUM([$Sheet2.A1:.B3];…)'; None when it cannot be translated."""
    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return None
    out = []
    depth_array = 0
    for t in toks:
        k, s = t.kind, t.text
        if k == "ref":
            if t.info.external or t.info.sheet2 is not None or t.info.spill:
                return None
            out.append(_odf_ref(t.info))
        elif k == "func":
            name = strip_prefixes(s).upper()
            name = _XL_TO_ODF.get(name, name)
            out.append(("COM.MICROSOFT." + name) if name in _MS_PREFIXED else name)
        elif k == "punct" and s == "{":
            depth_array += 1
            out.append(s)
        elif k == "punct" and s == "}":
            depth_array -= 1
            out.append(s)
        elif k == "punct" and s == ",":
            out.append(";")
        elif k == "punct" and s == ";" and depth_array:
            out.append("|")
        elif k == "sref":
            return None
        else:
            out.append(s)
    return "of:=" + "".join(out)


_ODF_REF = re.compile(r"\[(\$?(?:'(?:[^']|'')+'|[^.\[\]']*))?\.(\$?[A-Za-z]{1,3}\$?\d*)(?::(\$?(?:'(?:[^']|'')+'|[^.\[\]:']*))?\.(\$?[A-Za-z]{1,3}\$?\d*))?\]")


def odf_to_excel(formula: str) -> str:
    """'of:=SUM([.A1:.B2];[$Sheet2.C3])' → '=SUM(A1:B2,Sheet2!C3)'."""
    f = formula
    for pre in ("of:", "oooc:", "msoxl:"):
        if f.startswith(pre):
            f = f[len(pre):]
            break
    if not f.startswith("="):
        f = "=" + f

    def ref(m: re.Match[str]) -> str:
        sheet, a, sheet2, b = m.group(1), m.group(2), m.group(3), m.group(4)
        s = ""
        if sheet:
            name = sheet.lstrip("$")
            if name.startswith("'"):
                name = name[1:-1].replace("''", "'")
            s = quote_sheet(name) + "!"
        return s + a + (":" + b if b else "")

    # replace references and separators outside strings
    out = []
    i = 0
    in_array = 0
    while i < len(f):
        ch = f[i]
        if ch == '"':
            j = i + 1
            while j < len(f):
                if f[j] == '"':
                    if j + 1 < len(f) and f[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            out.append(f[i : j + 1])
            i = j + 1
            continue
        if ch == "#":
            m = re.match(r"#[A-Za-z0-9/_]+[!?]?", f[i:])
            if m:
                out.append(m.group(0))
                i += len(m.group(0))
                continue
        if ch == "[":
            m = _ODF_REF.match(f, i)
            if m:
                out.append(ref(m))
                i = m.end()
                continue
        if ch == "{":
            in_array += 1
        elif ch == "}":
            in_array -= 1
        if ch == ";":
            out.append(",")
        elif ch == "|" and in_array:
            out.append(";")
        elif ch == "!":
            out.append(" ")
        elif ch == "~":
            out.append(",")
        else:
            out.append(ch)
        i += 1
    text = "".join(out)
    text = re.sub(r"(?i)\bCOM\.MICROSOFT\.", "", text)
    text = re.sub(r"(?i)\bORG\.OPENOFFICE\.", "", text)
    return _rename_functions(text)


def read_names(path: str | Path) -> list[tuple[str, str, str | None]]:
    """Named ranges and expressions of an .ods file: [(name, Excel formula without '=', local sheet or None)]."""
    t = NS["table"]
    out: list[tuple[str, str, str | None]] = []
    stack: list[str | None] = []
    with zipfile.ZipFile(path) as z:
        with z.open("content.xml") as fh:
            for ev, el in ET.iterparse(fh, events=("start", "end")):
                if el.tag == f"{{{t}}}table":
                    if ev == "start":
                        stack.append(el.get(f"{{{t}}}name"))
                    else:
                        stack.pop()
                        el.clear()
                    continue
                if ev != "end":
                    continue
                if el.tag in (f"{{{t}}}named-range", f"{{{t}}}named-expression"):
                    name = el.get(f"{{{t}}}name")
                    addr = el.get(f"{{{t}}}cell-range-address")
                    expr = el.get(f"{{{t}}}expression")
                    formula = None
                    if addr:
                        formula = odf_to_excel("of:=[" + addr.split(" ")[0] + "]")
                    elif expr:
                        formula = odf_to_excel(expr)
                    if name and formula and formula != "=":
                        out.append((name, formula.lstrip("="), stack[-1] if stack else None))
    return out


def read_formulas(path: str | Path) -> dict[str, dict[tuple[int, int], str]]:
    """Formulas of an .ods file by sheet: {(row, col): Excel-syntax formula}."""
    out: dict[str, dict[tuple[int, int], str]] = {}
    t_table, t_row, t_cell, t_cov = (f"{{{NS['table']}}}{n}" for n in ("table", "table-row", "table-cell", "covered-table-cell"))
    a_name, a_rrep, a_crep, a_formula = (f"{{{NS['table']}}}{n}" for n in ("name", "number-rows-repeated", "number-columns-repeated", "formula"))
    with zipfile.ZipFile(path) as z:
        with z.open("content.xml") as fh:
            sheet = None
            row = 0
            col = 0
            row_rep = 1
            for ev, el in ET.iterparse(fh, events=("start", "end")):
                tag = el.tag
                if ev == "start":
                    if tag == t_table:
                        sheet = el.get(a_name) or f"Sheet{len(out) + 1}"
                        out[sheet] = {}
                        row = 0
                    elif tag == t_row:
                        row_rep = int(el.get(a_rrep, "1"))
                        col = 0
                    continue
                if tag in (t_cell, t_cov):
                    rep = int(el.get(a_crep, "1"))
                    f = el.get(a_formula)
                    if f and sheet is not None:
                        for dr in range(min(row_rep, 1000)):
                            for dc in range(min(rep, 1000)):
                                out[sheet][(row + 1 + dr, col + 1 + dc)] = odf_to_excel(f)
                    col += rep
                    el.clear()
                elif tag == t_row:
                    row += row_rep
                    el.clear()
    return out


# ── writing ─────────────────────────────────────────────────────────────


class OdsCell:
    __slots__ = ("value", "formula", "numfmt", "bold", "italic", "fill", "align", "span", "covered")

    def __init__(self, value: Any = None, formula: str | None = None, numfmt: str | None = None, bold: bool = False, italic: bool = False, fill: str | None = None, align: str | None = None) -> None:
        self.value, self.formula, self.numfmt = value, formula, numfmt
        self.bold, self.italic, self.fill, self.align = bold, italic, fill, align
        self.span: tuple[int, int] | None = None
        self.covered = False


class OdsSheet:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cells: dict[tuple[int, int], OdsCell] = {}
        self.widths: dict[int, float] = {}
        self.merges: list[tuple[int, int, int, int]] = []
        self.freeze: tuple[int, int] | None = None


def _num_style(code: str | None, value: Any) -> tuple[str, str] | None:
    """(key, number-style XML body) for common Excel formats; None → General."""
    if isinstance(value, _dt.datetime):
        return "dt", '<number:date-style style:name="{n}"><number:year number:style="long"/><number:text>-</number:text><number:month number:style="long"/><number:text>-</number:text><number:day number:style="long"/><number:text> </number:text><number:hours number:style="long"/><number:text>:</number:text><number:minutes number:style="long"/></number:date-style>'
    if isinstance(value, _dt.date):
        return "d", '<number:date-style style:name="{n}"><number:year number:style="long"/><number:text>-</number:text><number:month number:style="long"/><number:text>-</number:text><number:day number:style="long"/></number:date-style>'
    if isinstance(value, _dt.time):
        return "t", '<number:time-style style:name="{n}"><number:hours number:style="long"/><number:text>:</number:text><number:minutes number:style="long"/><number:text>:</number:text><number:seconds number:style="long"/></number:time-style>'
    if not code or code == "General":
        return None
    c = code.split(";")[0]
    m = re.fullmatch(r'(?:"?([$€£¥])"?)?(#,##)?0(?:\.(0+))?(%?)(?:_\))?', c.replace("\\", ""))
    if m:
        cur, grouping, dec, pct = m.group(1), bool(m.group(2)), len(m.group(3) or ""), m.group(4)
        grp = ' number:grouping="true"' if grouping else ""
        if pct:
            return f"p{dec}", f'<number:percentage-style style:name="{{n}}"><number:number number:decimal-places="{dec}" number:min-integer-digits="1"/><number:text>%</number:text></number:percentage-style>'
        if cur:
            return f"c{cur}{dec}{int(grouping)}", f'<number:number-style style:name="{{n}}"><number:text>{escape(cur)}</number:text><number:number number:decimal-places="{dec}" number:min-integer-digits="1"{grp}/></number:number-style>'
        return f"n{dec}{int(grouping)}", f'<number:number-style style:name="{{n}}"><number:number number:decimal-places="{dec}" number:min-integer-digits="1"{grp}/></number:number-style>'
    return None


def _cell_xml(c: OdsCell, style: str | None) -> str:
    attrs = []
    if style:
        attrs.append(f'table:style-name="{style}"')
    if c.span:
        attrs.append(f'table:number-rows-spanned="{c.span[0]}" table:number-columns-spanned="{c.span[1]}"')
    if c.formula:
        attrs.append(f'table:formula="{escape(c.formula, {chr(34): "&quot;"})}"')
    v = c.value
    text = None
    if v is None:
        if c.formula:
            attrs.append('office:value-type="string" office:string-value=""')
    elif isinstance(v, bool):
        attrs.append(f'office:value-type="boolean" office:boolean-value="{"true" if v else "false"}"')
        text = "TRUE" if v else "FALSE"
    elif isinstance(v, (int, float)):
        pct = c.numfmt is not None and "%" in c.numfmt.split(";")[0]
        attrs.append(f'office:value-type="{"percentage" if pct else "float"}" office:value="{repr(float(v)) if isinstance(v, float) else v}"')
        from _numfmt import format_value

        text = format_value(v, c.numfmt or "General")[0]
    elif isinstance(v, _dt.datetime):
        attrs.append(f'office:value-type="date" office:date-value="{v.replace(microsecond=0).isoformat()}"')
        text = v.strftime("%Y-%m-%d %H:%M")
    elif isinstance(v, _dt.date):
        attrs.append(f'office:value-type="date" office:date-value="{v.isoformat()}"')
        text = v.isoformat()
    elif isinstance(v, _dt.time):
        attrs.append(f'office:value-type="time" office:time-value="PT{v.hour:02d}H{v.minute:02d}M{v.second:02d}S"')
        text = v.strftime("%H:%M:%S")
    elif hasattr(v, "code"):
        # LibreOffice's own encoding of an error result: readers that ignore calcext still see the code as text
        attrs.append('office:value-type="string" calcext:value-type="error"')
        text = v.code
    else:
        s = str(v)
        attrs.append('office:value-type="string"')
        text = s
    body = ""
    if text is not None:
        body = "".join(f"<text:p>{_ods_text(line)}</text:p>" for line in str(text).split("\n"))
    return f"<table:table-cell {' '.join(attrs)}>{body}</table:table-cell>" if body else f"<table:table-cell {' '.join(attrs)}/>"


def _ods_text(s: str) -> str:
    s = escape(s)
    s = re.sub(r"  +", lambda m: " " + f'<text:s text:c="{len(m.group(0)) - 1}"/>', s)
    return s.replace("\t", "<text:tab/>")


def _names_xml(names: Iterable[tuple[str, str]]) -> str:
    items = []
    for name, formula in names:
        f = excel_to_odf("=" + formula.lstrip("="))
        if f is None:
            continue
        body = f[len("of:="):]
        m = re.fullmatch(r"\[([^\[\]]+)\]", body)
        if m and "$" in m.group(1).split(".", 1)[0][:1]:
            addr = m.group(1)
            base = addr.split(":")[0]
            items.append(f'<table:named-range table:name="{escape(name)}" table:base-cell-address="{escape(base)}" table:cell-range-address="{escape(addr)}"/>')
        else:
            items.append(f'<table:named-expression table:name="{escape(name)}" table:base-cell-address="$Sheet1.$A$1" table:expression="{escape(f, {chr(34): "&quot;"})}"/>')
    return "<table:named-expressions>" + "".join(items) + "</table:named-expressions>" if items else ""


def write_ods(path: str | Path, sheets: Iterable[OdsSheet], names: Iterable[tuple[str, str]] = ()) -> None:
    """Writes a minimal .ods. names: global (name, Excel formula) pairs, stored as named ranges/expressions."""
    sheets = list(sheets)
    num_styles: dict[str, str] = {}
    cell_styles: dict[tuple, str] = {}
    auto_xml: list[str] = []
    col_styles: dict[float, str] = {}

    def num_style_name(key: str, body: str) -> str:
        if key not in num_styles:
            n = f"N{len(num_styles) + 1}"
            num_styles[key] = n
            auto_xml.append(body.replace("{n}", n))
        return num_styles[key]

    def cell_style(c: OdsCell) -> str | None:
        ns = _num_style(c.numfmt, c.value)
        nname = num_style_name(*ns) if ns else None
        key = (nname, c.bold, c.italic, c.fill, c.align)
        if key == (None, False, False, None, None):
            return None
        if key not in cell_styles:
            name = f"ce{len(cell_styles) + 1}"
            cell_styles[key] = name
            props = []
            text_props = []
            if c.bold:
                text_props.append('fo:font-weight="bold" style:font-weight-asian="bold" style:font-weight-complex="bold"')
            if c.italic:
                text_props.append('fo:font-style="italic"')
            if c.fill:
                props.append(f'<style:table-cell-properties fo:background-color="{c.fill}"/>')
            if c.align in ("center", "right", "left"):
                props.append(f'<style:paragraph-properties fo:text-align="{ {"left": "start", "right": "end", "center": "center"}[c.align] }"/>')
            if text_props:
                props.append(f"<style:text-properties {' '.join(text_props)}/>")
            dn = f' style:data-style-name="{nname}"' if nname else ""
            auto_xml.append(f'<style:style style:name="{name}" style:family="table-cell" style:parent-style-name="Default"{dn}>{"".join(props)}</style:style>')
        return cell_styles[key]

    def col_style(width_chars: float) -> str:
        w = round(width_chars, 1)
        if w not in col_styles:
            name = f"co{len(col_styles) + 1}"
            col_styles[w] = name
            auto_xml.append(f'<style:style style:name="{name}" style:family="table-column"><style:table-column-properties style:column-width="{w * 0.1905:.3f}cm"/></style:style>')
        return col_styles[w]

    default_col = col_style(8.43)
    tables = []
    for sh in sheets:
        for (r1, c1, r2, c2) in sh.merges:
            top = sh.cells.setdefault((r1, c1), OdsCell())
            top.span = (r2 - r1 + 1, c2 - c1 + 1)
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    if (r, c) != (r1, c1):
                        sh.cells.setdefault((r, c), OdsCell()).covered = True
        max_r = max((k[0] for k in sh.cells), default=0)
        max_c = max((k[1] for k in sh.cells), default=0)
        parts = [f'<table:table table:name="{escape(sh.name, {chr(34): "&quot;"})}">']
        for c in range(1, max(max_c, 1) + 1):
            w = sh.widths.get(c)
            parts.append(f'<table:table-column table:style-name="{col_style(w) if w else default_col}" table:default-cell-style-name="Default"/>')
        by_row: dict[int, dict[int, OdsCell]] = {}
        for (r, c), cell in sh.cells.items():
            by_row.setdefault(r, {})[c] = cell
        r = 1
        while r <= max_r:
            if r not in by_row:
                nxt = min((k for k in by_row if k > r), default=max_r + 1)
                parts.append(f'<table:table-row table:number-rows-repeated="{nxt - r}"><table:table-cell table:number-columns-repeated="{max(max_c, 1)}"/></table:table-row>')
                r = nxt
                continue
            row = by_row[r]
            cells_xml = []
            c = 1
            last = max(row)
            while c <= last:
                cell = row.get(c)
                if cell is None:
                    nxt = min((k for k in row if k > c), default=last + 1)
                    cells_xml.append(f'<table:table-cell table:number-columns-repeated="{nxt - c}"/>' if nxt - c > 1 else "<table:table-cell/>")
                    c = nxt
                    continue
                if cell.covered:
                    cells_xml.append("<table:covered-table-cell/>")
                else:
                    cells_xml.append(_cell_xml(cell, cell_style(cell)))
                c += 1
            parts.append("<table:table-row>" + "".join(cells_xml) + "</table:table-row>")
            r += 1
        parts.append("</table:table>")
        tables.append("".join(parts))
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
        'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" '
        'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0" '
        'office:version="1.3"><office:automatic-styles>' + "".join(auto_xml) + "</office:automatic-styles>"
        "<office:body><office:spreadsheet>" + "".join(tables) + _names_xml(names) + "</office:spreadsheet></office:body></office:document-content>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.3">'
        '<office:styles><style:style style:name="Default" style:family="table-cell"><style:text-properties style:font-name="Liberation Sans" fo:font-size="10pt"/></style:style></office:styles></office:document-styles>'
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/></manifest:manifest>'
    )
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.3"><office:meta><meta:generator>Desk spreadsheets skill</meta:generator></office:meta></office:document-meta>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet", compress_type=zipfile.ZIP_STORED)
        for name, data in (("content.xml", content), ("styles.xml", styles), ("meta.xml", meta), ("META-INF/manifest.xml", manifest)):
            z.writestr(name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


# ── reading formatting (built-in .ods → .xlsx) ──────────────────────────

_FO = NS["fo"]
_STYLE = NS["style"]
_NUM = NS["number"]
_TABLE = NS["table"]
_TEXT = NS["text"]


def _a(el: ET.Element, ns: str, name: str) -> str | None:
    return el.get(f"{{{NS[ns]}}}{name}")


def _length_pt(v: str | None) -> float | None:
    """'0.35cm' / '1pt' / '0.0138in' / '2mm' → points."""
    if not v:
        return None
    m = re.match(r"^\s*(-?[\d.]+)\s*(cm|mm|in|pt|pc|px)?\s*$", v)
    if not m:
        return None
    x = float(m.group(1))
    unit = m.group(2) or "pt"
    return x * {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72.0, "pt": 1.0, "pc": 12.0, "px": 0.75}[unit]


def _color(v: str | None) -> str | None:
    if not v or v in ("transparent", "none"):
        return None
    v = v.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        return v.upper()
    return None


def _border(v: str | None) -> tuple[str, str | None] | None:
    """'0.06pt solid #000000' → ('thin', '#000000'); 'none' → None."""
    if not v or v.strip() in ("none", "hidden"):
        return None
    parts = v.split()
    width = next((_length_pt(p) for p in parts if _length_pt(p) is not None), 0.75) or 0.75
    kind = next((p for p in parts if p in ("solid", "dashed", "dotted", "double", "dot-dash", "dot-dot-dash", "fine-dashed")), "solid")
    color = next((_color(p) for p in parts if p.startswith("#")), None)
    if kind == "double":
        style = "double"
    elif kind in ("dashed", "fine-dashed", "dot-dash", "dot-dot-dash"):
        style = "dashed" if width < 1.5 else "mediumDashed"
    elif kind == "dotted":
        style = "dotted"
    elif width >= 2.0:
        style = "thick"
    elif width >= 1.0:
        style = "medium"
    elif width <= 0.1:
        style = "hair" if width < 0.05 else "thin"
    else:
        style = "thin"
    return style, color


def _quote_lit(s: str) -> str:
    if not s:
        return ""
    if re.fullmatch(r"[ $€£¥%\-+/():!^&'~{}<>=,.]+", s):
        return s
    return '"' + s.replace('"', "") + '"'


def _number_part(el: ET.Element) -> str:
    dec = int(_a(el, "number", "decimal-places") or 0)
    mind = _a(el, "number", "min-decimal-places")
    mind_i = int(mind) if mind is not None else dec
    mint = int(_a(el, "number", "min-integer-digits") or 1)
    grouping = _a(el, "number", "grouping") == "true"
    integer = "#,##0" if grouping else ("0" * mint if mint else "#")
    frac = ""
    if dec:
        frac = "." + "0" * min(mind_i, dec) + "#" * max(0, dec - mind_i)
    return integer + frac


def _numstyle_code(el: ET.Element, by_name: dict[str, ET.Element], depth: int = 0) -> str:
    """An OpenDocument number style element → an Excel number format code."""
    kind = el.tag.split("}")[1]
    if kind == "text-style":
        return "@"
    if kind == "boolean-style":
        return '"TRUE";"TRUE";"FALSE"'
    parts: list[str] = []
    color = None
    for ch in el:
        tag = ch.tag.split("}")[1] if "}" in ch.tag else ch.tag
        ns = ch.tag[1:].split("}")[0] if ch.tag.startswith("{") else ""
        if ns == _STYLE and tag == "text-properties":
            c = _color(ch.get(f"{{{_FO}}}color"))
            if c and c.upper() in ("#FF0000", "#C9211E", "#CC0000", "#E00000", "#FF3333"):
                color = "[Red]"
            continue
        if ns != _NUM:
            continue
        if tag == "number":
            parts.append(_number_part(ch))
        elif tag == "scientific-number":
            dec = int(_a(ch, "number", "decimal-places") or 0)
            parts.append("0" + ("." + "0" * dec if dec else "") + "E+" + "0" * int(_a(ch, "number", "min-exponent-digits") or 2))
        elif tag == "fraction":
            parts.append("# " + "?" * int(_a(ch, "number", "min-numerator-digits") or 1) + "/" + "?" * int(_a(ch, "number", "min-denominator-digits") or 1))
        elif tag == "currency-symbol":
            sym = (ch.text or "").strip() or "$"
            parts.append(sym if sym in ("$", "€", "£", "¥") else f'"{sym}"')
        elif tag == "text":
            parts.append(_quote_lit(ch.text or ""))
        elif tag == "year":
            parts.append("yyyy" if _a(ch, "number", "style") == "long" else "yy")
        elif tag == "month":
            textual = _a(ch, "number", "textual") == "true"
            long = _a(ch, "number", "style") == "long"
            parts.append(("mmmm" if long else "mmm") if textual else ("mm" if long else "m"))
        elif tag == "day":
            parts.append("dd" if _a(ch, "number", "style") == "long" else "d")
        elif tag == "day-of-week":
            parts.append("dddd" if _a(ch, "number", "style") == "long" else "ddd")
        elif tag == "hours":
            parts.append("hh" if _a(ch, "number", "style") == "long" else "h")
        elif tag == "minutes":
            parts.append("mm" if _a(ch, "number", "style") == "long" else "m")
        elif tag == "seconds":
            dec = int(_a(ch, "number", "decimal-places") or 0)
            parts.append(("ss" if _a(ch, "number", "style") == "long" else "s") + ("." + "0" * dec if dec else ""))
        elif tag == "am-pm":
            parts.append("AM/PM")
        elif tag == "text-content":
            parts.append("@")
    code = "".join(parts)
    if kind == "time-style" and _a(el, "number", "truncate-on-overflow") == "false":
        code = re.sub(r"^h+", lambda m: "[" + m.group(0) + "]", code)
    if kind == "percentage-style" and "%" not in code:
        code += "%"
    code = (color or "") + code
    # conditional sections: <style:map style:condition="value()>=0" style:apply-style-name="N104P0"/>
    maps = [(m.get(f"{{{_STYLE}}}condition") or "", m.get(f"{{{_STYLE}}}apply-style-name")) for m in el if m.tag == f"{{{_STYLE}}}map"]
    if maps and depth < 3:
        pos = neg = None
        for cond, name in maps:
            target = by_name.get(name or "")
            if target is None:
                continue
            sub = _numstyle_code(target, by_name, depth + 1)
            c = cond.replace("value()", "").replace(" ", "").replace("&gt;", ">").replace("&lt;", "<")
            if c in (">=0", ">0"):
                pos = sub
            elif c in ("<0", "<=0"):
                neg = sub
        if pos and neg:
            return f"{pos};{neg};{code}"
        if pos:
            return f"{pos};{code}"
        if neg:
            return f"{code};{neg}"
    return code or "General"


class _StyleSheet:
    def __init__(self) -> None:
        self.num: dict[str, ET.Element] = {}
        self.cell: dict[str, ET.Element] = {}
        self.col_w: dict[str, float] = {}
        self.row_h: dict[str, float] = {}
        self.default: ET.Element | None = None
        self._resolved: dict[str, dict[str, Any]] = {}

    def collect(self, root: ET.Element) -> None:
        for el in root.iter():
            tag = el.tag
            if tag.startswith(f"{{{_NUM}}}") and tag.endswith("-style"):
                name = el.get(f"{{{_STYLE}}}name")
                if name:
                    self.num[name] = el
            elif tag == f"{{{_STYLE}}}style":
                fam = el.get(f"{{{_STYLE}}}family")
                name = el.get(f"{{{_STYLE}}}name") or ""
                if fam == "table-cell":
                    self.cell[name] = el
                elif fam == "table-column":
                    for p in el:
                        if p.tag == f"{{{_STYLE}}}table-column-properties":
                            w = _length_pt(p.get(f"{{{_STYLE}}}column-width"))
                            if w:
                                self.col_w[name] = w
                elif fam == "table-row":
                    for p in el:
                        if p.tag == f"{{{_STYLE}}}table-row-properties":
                            h = _length_pt(p.get(f"{{{_STYLE}}}row-height"))
                            if h and p.get(f"{{{_STYLE}}}use-optimal-row-height") != "true":
                                self.row_h[name] = h
            elif tag == f"{{{_STYLE}}}default-style" and el.get(f"{{{_STYLE}}}family") == "table-cell":
                self.default = el

    def resolve(self, name: str | None) -> dict[str, Any]:
        """Effective formatting of a cell style (parents merged): numfmt, bold, italic, underline, color, size,
        font, fill, halign, valign, wrap, borders."""
        key = name or ""
        if key in self._resolved:
            return self._resolved[key]
        chain: list[ET.Element] = []
        seen: set[str] = set()
        cur = self.cell.get(key)
        while cur is not None and len(chain) < 10:
            chain.append(cur)
            parent = cur.get(f"{{{_STYLE}}}parent-style-name")
            if not parent or parent in seen:
                break
            seen.add(parent)
            cur = self.cell.get(parent)
        if self.default is not None:
            chain.append(self.default)
        out: dict[str, Any] = {}
        for el in reversed(chain):
            ds = el.get(f"{{{_STYLE}}}data-style-name")
            if ds and ds in self.num:
                code = _numstyle_code(self.num[ds], self.num)
                if code and code != "General":
                    out["numfmt"] = code
            for p in el:
                if p.tag == f"{{{_STYLE}}}text-properties":
                    fw = p.get(f"{{{_FO}}}font-weight")
                    if fw:
                        out["bold"] = fw in ("bold", "600", "700", "800", "900")
                    fs = p.get(f"{{{_FO}}}font-style")
                    if fs:
                        out["italic"] = fs == "italic"
                    ul = p.get(f"{{{_STYLE}}}text-underline-style")
                    if ul:
                        out["underline"] = ul not in ("none",)
                    c = _color(p.get(f"{{{_FO}}}color"))
                    if c:
                        out["color"] = c
                    size = _length_pt(p.get(f"{{{_FO}}}font-size"))
                    if size:
                        out["size"] = size
                    fn = p.get(f"{{{_STYLE}}}font-name")
                    if fn:
                        out["font"] = fn
                elif p.tag == f"{{{_STYLE}}}table-cell-properties":
                    bg = p.get(f"{{{_FO}}}background-color")
                    if bg is not None:
                        out["fill"] = _color(bg)
                    if p.get(f"{{{_FO}}}wrap-option"):
                        out["wrap"] = p.get(f"{{{_FO}}}wrap-option") == "wrap"
                    va = p.get(f"{{{_STYLE}}}vertical-align")
                    if va:
                        out["valign"] = {"top": "top", "middle": "center", "bottom": "bottom", "automatic": None}.get(va)
                    borders = dict(out.get("borders") or {})
                    all_b = p.get(f"{{{_FO}}}border")
                    if all_b is not None:
                        b = _border(all_b)
                        for side in ("left", "right", "top", "bottom"):
                            borders[side] = b
                    for side in ("left", "right", "top", "bottom"):
                        v = p.get(f"{{{_FO}}}border-{side}")
                        if v is not None:
                            borders[side] = _border(v)
                    if borders:
                        out["borders"] = borders
                elif p.tag == f"{{{_STYLE}}}paragraph-properties":
                    ta = p.get(f"{{{_FO}}}text-align")
                    if ta:
                        out["halign"] = {"start": "left", "left": "left", "center": "center", "end": "right", "right": "right", "justify": "justify"}.get(ta)
        self._resolved[key] = out
        return out


def read_formats(path: str | Path, limits: dict[str, tuple[int, int]] | None = None) -> dict[str, dict[str, Any]]:
    """Formatting of an .ods by sheet: {'cells': {(r, c): fmt}, 'widths': {col: chars}, 'heights': {row: pt},
    'merges': [(r1, c1, r2, c2)], 'hidden_cols': set, 'hidden_rows': set}. limits: sheet → (max row, max col) to
    stop repeated empty styled cells from filling a million rows (default 20 000 × 256)."""
    ss = _StyleSheet()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "styles.xml" in names:
            ss.collect(ET.fromstring(z.read("styles.xml")))
        with z.open("content.xml") as fh:
            out: dict[str, dict[str, Any]] = {}
            cur: dict[str, Any] | None = None
            sheet = None
            row = 0
            col = 0
            row_rep = 1
            row_style_default = None
            col_defaults: list[str | None] = []
            lim_r, lim_c = 1 << 20, 1 << 14
            t_table, t_col, t_row, t_cell, t_cov = (f"{{{_TABLE}}}{n}" for n in ("table", "table-column", "table-row", "table-cell", "covered-table-cell"))
            auto_done = False
            for ev, el in ET.iterparse(fh, events=("start", "end")):
                tag = el.tag
                if ev == "end" and tag == f"{{{NS['office']}}}automatic-styles" and not auto_done:
                    ss.collect(el)
                    auto_done = True
                    el.clear()
                    continue
                if ev == "start":
                    if tag == t_table:
                        sheet = el.get(f"{{{_TABLE}}}name") or f"Sheet{len(out) + 1}"
                        cur = {"cells": {}, "widths": {}, "heights": {}, "merges": [], "hidden_cols": set(), "hidden_rows": set()}
                        out[sheet] = cur
                        row = 0
                        col_defaults = []
                        lim_r, lim_c = (limits or {}).get(sheet, (20_000, 256))
                    elif tag == t_row:
                        row_rep = int(el.get(f"{{{_TABLE}}}number-rows-repeated", "1"))
                        row_style_default = el.get(f"{{{_TABLE}}}default-cell-style-name")
                        col = 0
                        if cur is not None and row < lim_r:
                            h = ss.row_h.get(el.get(f"{{{_TABLE}}}style-name") or "")
                            vis = el.get(f"{{{_TABLE}}}visibility")
                            for dr in range(min(row_rep, max(0, lim_r - row))):
                                if h:
                                    cur["heights"][row + 1 + dr] = h
                                if vis in ("collapse", "filter"):
                                    cur["hidden_rows"].add(row + 1 + dr)
                    continue
                if cur is None:
                    continue
                if tag == t_col:
                    rep = int(el.get(f"{{{_TABLE}}}number-columns-repeated", "1"))
                    w = ss.col_w.get(el.get(f"{{{_TABLE}}}style-name") or "")
                    dcs = el.get(f"{{{_TABLE}}}default-cell-style-name")
                    hidden = el.get(f"{{{_TABLE}}}visibility") in ("collapse", "filter")
                    for k in range(min(rep, max(0, lim_c + 1 - len(col_defaults)))):
                        c = len(col_defaults) + 1
                        col_defaults.append(dcs if dcs and dcs != "Default" else None)
                        if w:
                            cur["widths"][c] = w / 5.25  # points → character widths (Calibri 11: about 7 px per char)
                        if hidden:
                            cur["hidden_cols"].add(c)
                    el.clear()
                elif tag in (t_cell, t_cov):
                    rep = int(el.get(f"{{{_TABLE}}}number-columns-repeated", "1"))
                    style = el.get(f"{{{_TABLE}}}style-name")
                    if style is None:
                        style = row_style_default
                    span_c = int(el.get(f"{{{_TABLE}}}number-columns-spanned", "1"))
                    span_r = int(el.get(f"{{{_TABLE}}}number-rows-spanned", "1"))
                    implied = _implied_format(el) if tag == t_cell else None
                    for dr in range(min(row_rep, max(0, lim_r - row))):
                        for dc in range(min(rep, max(0, lim_c - col))):
                            r, c = row + 1 + dr, col + 1 + dc
                            st = style
                            if st is None and c - 1 < len(col_defaults):
                                st = col_defaults[c - 1]
                            f = ss.resolve(st) if st and st != "Default" and tag == t_cell else None
                            if implied and not (f or {}).get("numfmt"):
                                f = dict(f or {}, numfmt=implied)
                            if f:
                                cur["cells"][(r, c)] = f
                            if tag == t_cell and (span_c > 1 or span_r > 1):
                                cur["merges"].append((r, c, r + span_r - 1, c + span_c - 1))
                    col += rep
                    el.clear()
                elif tag == t_row:
                    row += row_rep
                    el.clear()
    return out


def _implied_format(el: Any) -> str | None:
    """A number format for a percentage or currency cell that carries no data style (some writers rely on the
    value type and the displayed text alone): decimals and currency symbol are read off the displayed text."""
    vt = el.get(f"{{{NS['office']}}}value-type")
    if vt not in ("percentage", "currency"):
        return None
    p = el.find(f"{{{NS['text']}}}p")
    text = "".join(p.itertext()).strip() if p is not None else ""
    m = re.search(r"\d[.,](\d+)", text)
    decimals = len(m.group(1)) if m else 0
    body = "0" + ("." + "0" * decimals if decimals else "")
    if vt == "percentage":
        return body + "%"
    grouped = "#,##" + body
    sym = re.match(r"^-?\s*([^\d\s.,-]+)", text)
    if sym:
        return (sym.group(1) if sym.group(1) == "$" else f'"{sym.group(1)}"') + grouped
    suf = re.search(r"[\d.,]\s*([^\d\s.,]+)\s*$", text)
    if suf:
        return grouped + f' "{suf.group(1)}"'
    return grouped


def fix_lo_arity(formula: str) -> str:
    """LibreOffice accepts ROUND(x), ROUNDUP(x), ROUNDDOWN(x), CEILING(x) and FLOOR(x); Excel needs the second
    argument (0 digits, significance 1). Returns the formula with it added."""
    from _formula import _match_parens, _top_args, tokenize

    try:
        toks = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    pairs = _match_parens(toks)
    inserts: list[tuple[int, str]] = []
    for i, t in enumerate(toks):
        if t.kind != "func":
            continue
        name = strip_prefixes(t.text).upper()
        extra = {"ROUND": "0", "ROUNDUP": "0", "ROUNDDOWN": "0", "CEILING": "1", "FLOOR": "1"}.get(name)
        if extra is None or i + 1 >= len(toks):
            continue
        close = pairs.get(i + 1)
        if close is None:
            continue
        args = _top_args(toks, i + 1, close)
        if len(args) == 1 and args[0][1] > args[0][0]:
            inserts.append((close, "," + extra))
    if not inserts:
        return formula
    lead = "=" if formula.startswith("=") else ""
    texts = [t.text for t in toks]
    for pos, text in sorted(inserts, reverse=True):
        texts.insert(pos, text)
    return lead + "".join(texts)
