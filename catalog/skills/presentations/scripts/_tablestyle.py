"""Table styles for drawing tables: the deck's own tblStyle definitions, else built-in approximations by GUID.

PowerPoint ships its ~74 table styles inside the application, and files usually carry only the style's GUID. The
most common ones (Medium Style 2 in all accents, which python-pptx and PowerPoint use by default, and the two
"No Style" styles) are defined here in the same XML form, so every style goes through one code path.
"""

from __future__ import annotations

from typing import Any

from _ooxml import Line, SlideCtx, child, color_child, fill_el, local, parse_fill, parse_line

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _medium2(clr: str) -> str:
    ln = lambda w: f'<a:ln w="{w}" cmpd="sng"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln>'  # noqa: E731
    return f"""<a:tblStyle xmlns:a="{A_NS}" styleId="x" styleName="Medium Style 2">
<a:wholeTbl><a:tcTxStyle><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="dk1"/></a:tcTxStyle>
<a:tcStyle><a:tcBdr><a:left>{ln(12700)}</a:left><a:right>{ln(12700)}</a:right><a:top>{ln(12700)}</a:top><a:bottom>{ln(12700)}</a:bottom><a:insideH>{ln(12700)}</a:insideH><a:insideV>{ln(12700)}</a:insideV></a:tcBdr>
<a:fill><a:solidFill><a:schemeClr val="{clr}"><a:tint val="20000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:wholeTbl>
<a:band1H><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{clr}"><a:tint val="40000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:band1H>
<a:band1V><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{clr}"><a:tint val="40000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:band1V>
<a:lastCol><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill></a:fill></a:tcStyle></a:lastCol>
<a:firstCol><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill></a:fill></a:tcStyle></a:firstCol>
<a:lastRow><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr><a:top>{ln(38100)}</a:top></a:tcBdr><a:fill><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill></a:fill></a:tcStyle></a:lastRow>
<a:firstRow><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr><a:bottom>{ln(38100)}</a:bottom></a:tcBdr><a:fill><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill></a:fill></a:tcStyle></a:firstRow>
</a:tblStyle>"""


def _grid() -> str:
    ln = '<a:ln w="12700" cmpd="sng"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:ln>'
    return f"""<a:tblStyle xmlns:a="{A_NS}" styleId="x" styleName="No Style, Table Grid"><a:wholeTbl><a:tcTxStyle><a:schemeClr val="tx1"/></a:tcTxStyle>
<a:tcStyle><a:tcBdr><a:left>{ln}</a:left><a:right>{ln}</a:right><a:top>{ln}</a:top><a:bottom>{ln}</a:bottom><a:insideH>{ln}</a:insideH><a:insideV>{ln}</a:insideV></a:tcBdr><a:fill><a:noFill/></a:fill></a:tcStyle></a:wholeTbl></a:tblStyle>"""


def _light1(clr: str) -> str:
    ln = f'<a:ln w="12700" cmpd="sng"><a:solidFill><a:schemeClr val="{clr}"/></a:solidFill></a:ln>'
    return f"""<a:tblStyle xmlns:a="{A_NS}" styleId="x" styleName="Light Style 1"><a:wholeTbl><a:tcTxStyle><a:schemeClr val="tx1"/></a:tcTxStyle>
<a:tcStyle><a:tcBdr><a:top>{ln}</a:top><a:bottom>{ln}</a:bottom></a:tcBdr><a:fill><a:noFill/></a:fill></a:tcStyle></a:wholeTbl>
<a:band1H><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{clr}"><a:alpha val="20000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:band1H>
<a:firstRow><a:tcTxStyle b="on"/><a:tcStyle><a:tcBdr><a:bottom>{ln}</a:bottom></a:tcBdr><a:fill><a:noFill/></a:fill></a:tcStyle></a:firstRow>
<a:lastRow><a:tcTxStyle b="on"/><a:tcStyle><a:tcBdr><a:top>{ln}</a:top></a:tcBdr><a:fill><a:noFill/></a:fill></a:tcStyle></a:lastRow></a:tblStyle>"""


BUILTIN = {
    "{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}": lambda: _medium2("dk1"),
    "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}": lambda: _medium2("accent1"),
    "{21E4AEA4-8DFA-4A89-87EB-49C32662AFE8}": lambda: _medium2("accent2"),
    "{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}": lambda: _medium2("accent3"),
    "{00A15C55-8517-42AA-B614-E9B94910E393}": lambda: _medium2("accent4"),
    "{7DF18680-E054-41AD-8BC1-D1AEF772440D}": lambda: _medium2("accent5"),
    "{93296810-A885-4BE3-A3E7-6D5BEEA58F35}": lambda: _medium2("accent6"),
    "{5940675A-B579-460E-94D1-54222C63F5DA}": _grid,
    "{9D7B26C5-4107-4FEC-AEDC-1716B250EE53}": lambda: _light1("tx1"),
    "{3B4B98B0-60AC-42C2-AFA5-B58CD77FA1E5}": lambda: _light1("accent1"),
}
NO_STYLE = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
_PARSED: dict[str, Any] = {}


class TableStyle:
    def __init__(self, ctx: SlideCtx, el: Any | None):
        self.ctx = ctx
        self.parts: dict[str, Any] = {}
        if el is not None:
            for c in el:
                self.parts[local(c)] = c

    def text_props(self, parts: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {"color": None, "bold": None, "italic": None}
        for name in parts:  # lowest priority first
            tx = child(self.parts.get(name), "tcTxStyle")
            if tx is None:
                continue
            if tx.get("b") in ("on", "off"):
                out["bold"] = tx.get("b") == "on"
            if tx.get("i") in ("on", "off"):
                out["italic"] = tx.get("i") == "on"
            cel = color_child(tx)
            if cel is None:
                fr = child(tx, "fontRef")
                cel = color_child(fr) if fr is not None else None
            if cel is not None:
                out["color"] = self.ctx.cc.resolve(cel)
        return out

    def fill(self, parts: list[str]) -> Any:
        f = None
        for name in parts:
            ts = child(self.parts.get(name), "tcStyle")
            if ts is None:
                continue
            fe = child(ts, "fill")
            if fe is not None:
                ff = fill_el(fe)
                if ff is not None:
                    f = parse_fill(ff, self.ctx.cc)
            fr = child(ts, "fillRef")
            if fr is not None:
                col = self.ctx.cc.resolve(color_child(fr))
                if col is not None:
                    from _ooxml import Fill

                    f = Fill("solid", color=col)
        return f

    def border(self, parts: list[str], side: str, ri: int, ci: int, nr: int, nc: int, rs: int, gs: int) -> Line | None:
        for name in reversed(parts):  # highest priority first
            ts = child(self.parts.get(name), "tcStyle")
            bdr = child(ts, "tcBdr") if ts is not None else None
            if bdr is None:
                continue
            key = _side_key(name, side, ri, ci, nr, nc, rs, gs)
            e = child(bdr, key)
            if e is None:
                continue
            ln = child(e, "ln")
            if ln is not None:
                return parse_line(ln, self.ctx.cc)
            lr = child(e, "lnRef")
            if lr is not None:
                col = self.ctx.cc.resolve(color_child(lr))
                return Line(col, 1.0) if col is not None else None
        return None


def _side_key(part: str, side: str, ri: int, ci: int, nr: int, nc: int, rs: int, gs: int) -> str:
    first_c, last_c = ci == 0, ci + gs >= nc
    first_r, last_r = ri == 0, ri + rs >= nr
    if part in ("firstRow", "lastRow", "band1H", "band2H"):
        return {"lnT": "top", "lnB": "bottom", "lnL": "left" if first_c else "insideV", "lnR": "right" if last_c else "insideV"}[side]
    if part in ("firstCol", "lastCol", "band1V", "band2V"):
        return {"lnL": "left", "lnR": "right", "lnT": "top" if first_r else "insideH", "lnB": "bottom" if last_r else "insideH"}[side]
    return {"lnL": "left" if first_c else "insideV", "lnR": "right" if last_c else "insideV", "lnT": "top" if first_r else "insideH", "lnB": "bottom" if last_r else "insideH"}[side]


def table_style(ctx: SlideCtx, tblPr: Any, defaults: Any) -> TableStyle:
    sid = None
    if tblPr is not None:
        el = child(tblPr, "tableStyleId")
        sid = (el.text or "").strip() if el is not None else None
        inline = child(tblPr, "tableStyle")
        if inline is not None:
            return TableStyle(ctx, inline)
    if not sid or sid == NO_STYLE:
        return TableStyle(ctx, None)
    if sid in defaults.table_styles:
        return TableStyle(ctx, defaults.table_styles[sid])
    gen = BUILTIN.get(sid.upper()) or BUILTIN["{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"]
    key = sid.upper() if sid.upper() in BUILTIN else "default"
    if key not in _PARSED:
        from lxml import etree

        _PARSED[key] = etree.fromstring(gen())
    return TableStyle(ctx, _PARSED[key])

