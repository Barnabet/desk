"""Surgical .xlsx edits: cell values, formulas, fills, clears and styles written straight into the sheet XML.

Only the sheet parts that change and styles.xml are rewritten; every other part of the zip is copied byte for byte
(its compressed data is not even inflated), so slicers, sparklines, x14 extensions, pivot caches, VBA and anything
else the file holds stay exactly as they were. A one-cell edit of a 40 MB workbook takes about a second plus the
recalculation, instead of a full openpyxl load and save.

Styles: a cell's current style (its cellXfs index) is combined with the requested font, fill, border, alignment,
number format or protection into a new xf appended to styles.xml (fonts, fills, borders and number formats are
reused when identical), so everything else about the cell's look is kept.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

from _a1 import MAX_COL, MAX_ROW, col_letter, parse_range, range_name, split_sheet
from _common import SkillError
from _xlsx import NS_MAIN, CellPatch, Package, _rels_name, _set_full_calc, patch_sheet_xml, publish, rewrite_zip, temp_beside

#: ops this module applies (after ALIASES)
PATCH_OPS = {"set", "fill", "clear", "style"}


# ── styles.xml ──────────────────────────────────────────────────────────


class StyleBook:
    """styles.xml as text sections: new fonts, fills, borders, number formats and xfs are appended in place."""

    def __init__(self, xml: bytes) -> None:
        self.xml = xml
        m = re.search(rb"<(\w+:)?styleSheet\b", xml)
        self.px = (m.group(1) or b"") if m else b""
        self.items: dict[str, list[bytes]] = {}
        for tag, child in (("numFmts", "numFmt"), ("fonts", "font"), ("fills", "fill"), ("borders", "border"), ("cellXfs", "xf")):
            self.items[tag] = self._children(tag, child)
        self.added: dict[str, list[bytes]] = {k: [] for k in self.items}
        self._memo: dict[Any, int] = {}
        self._index: dict[str, dict[bytes, int]] = {k: {v: i for i, v in enumerate(vs)} for k, vs in self.items.items()}

    def _section(self, tag: str, xml: bytes | None = None) -> re.Match[bytes] | None:
        px = re.escape(self.px)
        return re.search(rb"<" + px + tag.encode() + rb"\b[^>]*?(?:/>|>(.*?)</" + px + tag.encode() + rb">)", self.xml if xml is None else xml, re.S)

    def _children(self, tag: str, child: str) -> list[bytes]:
        m = self._section(tag)
        if not m or m.group(1) is None:
            return []
        px = re.escape(self.px)
        return re.findall(rb"<" + px + child.encode() + rb"\b(?:[^>]*?/>|[^>]*?>.*?</" + px + child.encode() + rb">)", m.group(1), re.S)

    # element <-> openpyxl object
    def _obj(self, cls: Any, xml: bytes) -> Any:
        body = re.sub(rb"<(/?)" + re.escape(self.px), rb"<\1", xml) if self.px else xml
        body = re.sub(rb"^<(\w+)", lambda m: b"<" + m.group(1) + b' xmlns="' + NS_MAIN.encode() + b'"', body, count=1)
        return cls.from_tree(ET.fromstring(body))

    def _xml(self, obj: Any) -> bytes:
        out = ET.tostring(obj.to_tree(), encoding="utf-8")
        out = re.sub(rb"^<\?xml[^>]*>\s*", b"", out)
        out = out.replace(b" />", b"/>")
        if self.px:
            out = re.sub(rb"<(/?)(\w)", lambda m: b"<" + m.group(1) + self.px + m.group(2), out)
        return out

    def _add(self, tag: str, xml: bytes) -> int:
        idx = self._index[tag].get(xml)
        if idx is not None:
            return idx
        idx = len(self.items[tag]) + len(self.added[tag])
        self.added[tag].append(xml)
        self._index[tag][xml] = idx
        return idx

    def font(self, i: int) -> Any:
        from openpyxl.styles.fonts import Font

        xs = self.items["fonts"]
        return self._obj(Font, xs[i]) if 0 <= i < len(xs) else Font()

    def fill(self, i: int) -> Any:
        from openpyxl.styles.fills import Fill, PatternFill

        xs = self.items["fills"]
        return self._obj(Fill, xs[i]) if 0 <= i < len(xs) else PatternFill()

    def border(self, i: int) -> Any:
        from openpyxl.styles.borders import Border

        xs = self.items["borders"]
        return self._obj(Border, xs[i]) if 0 <= i < len(xs) else Border()

    def xf_attrs(self, i: int) -> tuple[dict[str, str], bytes]:
        xs = self.items["cellXfs"]
        x = xs[i] if 0 <= i < len(xs) else b'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        head = re.match(rb"<(?:\w+:)?xf\b([^>]*?)(/?)>", x)
        attrs = {k.decode(): v.decode() for k, v in re.findall(rb'\b(\w+)="([^"]*)"', head.group(1))} if head else {}
        inner = b"" if not head or head.group(2) == b"/" else x[head.end() : x.rfind(b"</")]
        return attrs, inner

    def numfmt_id(self, code: str) -> int:
        from _numfmt import BUILTIN_FORMATS

        for k, v in BUILTIN_FORMATS.items():
            if v == code:
                return k
        for x in self.items["numFmts"] + self.added["numFmts"]:
            m = re.search(rb'numFmtId="(\d+)"[^>]*formatCode="([^"]*)"', x) or re.search(rb'formatCode="([^"]*)"[^>]*numFmtId="(\d+)"', x)
            if m:
                fid, fc = (m.group(1), m.group(2)) if m.re.pattern.startswith(rb"numFmtId") else (m.group(2), m.group(1))
                import html

                if html.unescape(fc.decode("utf-8", "replace")) == code:
                    return int(fid)
        used = [int(n) for x in self.items["numFmts"] + self.added["numFmts"] for n in re.findall(rb'numFmtId="(\d+)"', x)]
        new = max([163] + used) + 1
        esc = code.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        self.added["numFmts"].append(b"<" + self.px + f'numFmt numFmtId="{new}" formatCode="{esc}"/>'.encode("utf-8"))
        return new

    def number_format(self, xf: int) -> str:
        from _numfmt import BUILTIN_FORMATS

        attrs, _ = self.xf_attrs(xf)
        nid = int(attrs.get("numFmtId", "0") or 0)
        if nid in BUILTIN_FORMATS:
            return BUILTIN_FORMATS[nid]
        for x in self.items["numFmts"] + self.added["numFmts"]:
            if re.search(rb'numFmtId="' + str(nid).encode() + rb'"', x):
                m = re.search(rb'formatCode="([^"]*)"', x)
                if m:
                    import html

                    return html.unescape(m.group(1).decode("utf-8", "replace"))
        return "General"

    def derive(self, xf: int, st: dict[str, Any], border: Any = None) -> int:
        """A new cellXfs index: xf with the style spec applied (font, fill, align, number_format, locked) and border
        (an openpyxl Border already combined for the cell's position)."""
        from _ops import make_alignment, make_fill, make_font

        key = (xf, repr(sorted((k, repr(v)) for k, v in st.items())), repr(border) if border is not None else None)
        if key in self._memo:
            return self._memo[key]
        attrs, inner = self.xf_attrs(xf)
        new = dict(attrs)
        font_spec = dict(st.get("font") or {}) if isinstance(st.get("font"), dict) else ({"bold": True} if st.get("font") is True else {})
        for k in ("bold", "italic", "color", "size"):
            if k in st and k not in font_spec:
                font_spec[k] = st[k]
        if font_spec:
            base = self.font(int(attrs.get("fontId", "0") or 0))
            new["fontId"] = str(self._add("fonts", self._xml(make_font(font_spec, base))))
            new["applyFont"] = "1"
        if "fill" in st or "background" in st:
            new["fillId"] = str(self._add("fills", self._xml(make_fill(st.get("fill", st.get("background"))))))
            new["applyFill"] = "1"
        if border is not None:
            new["borderId"] = str(self._add("borders", self._xml(border)))
            new["applyBorder"] = "1"
        nf = st.get("number_format", st.get("format"))
        if nf is not None:
            new["numFmtId"] = str(self.numfmt_id(str(nf)))
            new["applyNumberFormat"] = "1"
        align_xml = re.search(rb"<(?:\w+:)?alignment\b[^>]*/>", inner)
        prot_xml = re.search(rb"<(?:\w+:)?protection\b[^>]*/>", inner)
        if "align" in st or "alignment" in st or "wrap" in st:
            from openpyxl.styles.alignment import Alignment

            base_al = self._obj(Alignment, align_xml.group(0)) if align_xml else None
            spec = dict(st.get("align") or st.get("alignment") or {})
            if "wrap" in st:
                spec["wrap"] = st["wrap"]
            al = make_alignment(spec, base_al)
            align_bytes = self._xml(al)
            new["applyAlignment"] = "1"
        else:
            align_bytes = align_xml.group(0) if align_xml else b""
        if "locked" in st:
            locked = "1" if st["locked"] else "0"
            prot_bytes = b"<" + self.px + f'protection locked="{locked}"/>'.encode()
            new["applyProtection"] = "1"
        else:
            prot_bytes = prot_xml.group(0) if prot_xml else b""
        order = ["numFmtId", "fontId", "fillId", "borderId", "xfId"]
        keys = [k for k in order if k in new] + [k for k in new if k not in order]
        attr_txt = " ".join(f'{k}="{new[k]}"' for k in keys)
        children = align_bytes + prot_bytes
        xml = b"<" + self.px + b"xf " + attr_txt.encode() + (b">" + children + b"</" + self.px + b"xf>" if children else b"/>")
        idx = self._add("cellXfs", xml)
        self._memo[key] = idx
        return idx

    def result(self) -> bytes:
        """styles.xml with the additions (counts updated; a numFmts section created when needed)."""
        xml = self.xml
        for tag in ("numFmts", "fonts", "fills", "borders", "cellXfs"):
            add = self.added[tag]
            if not add:
                continue
            m = self._section(tag, xml)
            px = self.px
            total = len(self.items[tag]) + len(add)
            if m is None:
                if tag != "numFmts":
                    raise SkillError(f"styles.xml has no {tag} section")
                sect = b"<" + px + b'numFmts count="' + str(total).encode() + b'">' + b"".join(add) + b"</" + px + b"numFmts>"
                root = re.search(rb"<(?:\w+:)?styleSheet\b[^>]*>", xml)
                assert root is not None
                xml = xml[: root.end()] + sect + xml[root.end() :]
                continue
            whole = m.group(0)
            if m.group(1) is None:  # <fonts count="0"/>
                open_tag = re.sub(rb"(?<!\s)\s*/>$", b">", whole)
                new_sect = open_tag + b"".join(add) + b"</" + px + tag.encode() + b">"
            else:
                close = whole.rfind(b"</")
                new_sect = whole[:close] + b"".join(add) + whole[close:]
            new_sect = re.sub(rb'\bcount="\d+"', b'count="' + str(total).encode() + b'"', new_sect, count=1)
            if b"count=" not in new_sect[: new_sect.find(b">")]:
                new_sect = new_sect.replace(b"<" + px + tag.encode(), b"<" + px + tag.encode() + b' count="' + str(total).encode() + b'"', 1)
            xml = xml[: m.start()] + new_sect + xml[m.end() :]
        return xml


# ── planning ────────────────────────────────────────────────────────────


class Plan:
    """Per sheet: {(row, col): CellPatch}, plus the StyleBook and a log."""

    def __init__(self, pkg: Package) -> None:
        self.pkg = pkg
        styles_path = next((t for (typ, t) in pkg._rels.values() if typ.endswith("/styles")), None)
        self.styles_path = styles_path
        self.styles = StyleBook(pkg.read(styles_path)) if styles_path and pkg.has(styles_path) else None
        self.changes: dict[str, dict[tuple[int, int], CellPatch]] = {}
        self.log: list[str] = []
        self.warnings: list[str] = []
        self.values_changed = False
        self._xml_cache: dict[str, bytes] = {}

    def patch(self, sheet: str, r: int, c: int) -> CellPatch:
        cells = self.changes.setdefault(sheet, {})
        p = cells.get((r, c))
        if p is None:
            p = CellPatch(keep=True)
            cells[(r, c)] = p
        return p

    def sheet_xml(self, sheet: str) -> bytes:
        if sheet not in self._xml_cache:
            si = self.pkg.sheet(sheet)
            if not si.path:
                raise SkillError(f"{sheet} is not a worksheet")
            self._xml_cache[sheet] = self.pkg.read(si.path)
        return self._xml_cache[sheet]


def _set_value(plan: Plan, sheet: str, r: int, c: int, v: Any, date1904: bool) -> None:
    from _numfmt import date_kind, date_to_serial
    from _ops import LiteralText

    p = plan.patch(sheet, r, c)
    p.keep = False
    p.clear = False
    p.const = False
    p.formula = None
    p.value = None
    plan.values_changed = True
    if type(v) is LiteralText:
        p.value, p.const = str(v), True
        return
    if isinstance(v, str) and v.startswith("="):
        p.formula = v[1:]
        return
    if v is None:
        p.clear, p.const = True, True
        return
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        code = "yyyy-mm-dd" if type(v) is _dt.date else ("hh:mm:ss" if type(v) is _dt.time else "yyyy-mm-dd hh:mm")
        serial = date_to_serial(v, date1904)
        p.value, p.const = serial, True
        if plan.styles is not None:
            sb = plan.styles
            prev = p.restyle

            def dated(old: int, prev: Any = prev, sb: StyleBook = sb, code: str = code) -> int:
                base = prev(old) if prev else old
                return base if date_kind(sb.number_format(base)) else sb.derive(base, {"number_format": code})

            p.restyle = dated
        return
    p.value, p.const = v, True


def _restyle(plan: Plan, sheet: str, r: int, c: int, fn: Callable[[int], int]) -> None:
    p = plan.patch(sheet, r, c)
    prev = p.restyle
    p.restyle = (lambda old, prev=prev, fn=fn: fn(prev(old))) if prev else fn


def _cell_source(plan: Plan, sheet: str, r: int, c: int) -> tuple[Any, str | None, int]:
    """(cached value, formula text or None, style index) of one cell in the original sheet XML."""
    from _formula import FormulaSyntaxError, translate
    from _xlsx import _CELL_RE, _F_EL, _R_ATTR, _convert, _V_EL

    xml = plan.sheet_xml(sheet)
    k = re.search(rb"<(?:\w+:)?row\b[^>]*?\br=\"" + str(r).encode() + rb"\"[^>]*?(?:/>|>(.*?)</(?:\w+:)?row>)", xml, re.S)
    if not k or not k.group(1):
        return None, None, 0
    want = f"{col_letter(c)}{r}".encode()
    for cm in _CELL_RE.finditer(k.group(1)):
        ra = _R_ATTR.search(cm.group(2))
        if not ra or ra.group(1) + ra.group(2) != want:
            continue
        attrs = cm.group(2)
        sm = re.search(rb'\bs="(\d+)"', attrs)
        style = int(sm.group(1)) if sm else 0
        inner = cm.group(4) or b""
        fm = _F_EL.search(inner)
        formula = None
        if fm is not None:
            import html

            text = html.unescape((fm.group(4) or b"").decode("utf-8", "replace"))
            fattrs = fm.group(2)
            si = re.search(rb'\bsi="(\d+)"', fattrs)
            if not text and si:
                master = re.search(rb'<(?:\w+:)?c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*>\s*<(?:\w+:)?f\b[^>]*?\bsi="' + si.group(1) + rb'"[^>]*>([^<]+)</', xml)
                if master:
                    from _a1 import col_index

                    mr, mc = int(master.group(2)), col_index(master.group(1).decode())
                    try:
                        text = translate("=" + html.unescape(master.group(3).decode()), r - mr, c - mc)[1:]
                    except FormulaSyntaxError:
                        text = ""
            formula = "=" + text if text else None
        tm = re.search(rb'\bt="(\w+)"', attrs)
        vm = _V_EL.search(inner)
        v = None
        if vm is not None:
            raw = re.sub(rb"^<[^>]*>|</[^>]*>$", b"", vm.group(0)).decode("utf-8", "replace")
            v = _convert(raw, tm.group(1).decode() if tm else "n", plan.pkg.shared)
        elif tm and tm.group(1) == b"inlineStr":
            t = re.search(rb"<(?:\w+:)?t\b[^>]*>([^<]*)</", inner)
            v = t.group(1).decode("utf-8", "replace") if t else None
        return v, formula, style
    return None, None, 0


def plan_ops(pkg: Package, ops: list[dict[str, Any]], default_sheet: str | None) -> Plan:
    from _formula import translate
    from _ops import ALIASES, LiteralText, _border_for, make_side, normalize_formula, to_cell_value  # noqa: F401
    from _scan import scan_cached

    plan = Plan(pkg)
    names = [s.name for s in pkg.sheets]
    default = default_sheet or next((s.name for s in pkg.sheets if s.path), names[0])

    def resolve(name: str | None) -> str:
        n = name or default
        for s in names:
            if s.lower() == str(n).lower():
                return s
        raise SkillError(f"no sheet named '{n}'; sheets: {', '.join(names)}")

    def area(o: dict[str, Any], i: int, name: str) -> tuple[str, int, int, int, int]:
        spec = o.get("range") or o.get("cell") or o.get("cells")
        if not spec:
            raise SkillError(f"operation {i} ({name}) needs 'range' or 'cell'")
        s, rest = split_sheet(str(spec))
        sh = resolve(s or o.get("sheet"))
        try:
            r1, c1, r2, c2 = parse_range(rest)
        except ValueError as e:
            raise SkillError(f"operation {i} ({name}): {e}") from e
        if r2 >= MAX_ROW or c2 >= MAX_COL:
            si = pkg.sheet(sh)
            st = scan_cached(pkg.path, si.path) if si.path else {}
            if r2 >= MAX_ROW:
                r2 = max(r1, (st or {}).get("max_row") or r1)
            if c2 >= MAX_COL:
                c2 = max(c1, (st or {}).get("max_col") or c1)
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 2_000_000:
            raise SkillError(f"operation {i} ({name}): {range_name(r1, c1, r2, c2)} is over 2 million cells; narrow it")
        return sh, r1, c1, r2, c2

    for i, o in enumerate(ops, 1):
        if not isinstance(o, dict) or "op" not in o:
            raise SkillError(f"operation {i} must be an object with an 'op' key")
        name = ALIASES.get(str(o["op"]).lower().strip(), str(o["op"]).lower().strip())
        if name == "style" and str(o["op"]).lower() in ("number_format",) and "number_format" not in o and "format" in o:
            o = dict(o, number_format=o["format"])
        raw = bool(o.get("text") or o.get("raw"))
        if name == "set" and "values" in o:
            sh, r1, c1, _r2, _c2 = area(o, i, name)
            vals = o["values"]
            if not isinstance(vals, list):
                raise SkillError(f"operation {i} (set): 'values' must be a list of rows")
            if vals and not isinstance(vals[0], list):
                vals = [vals] if not o.get("down") else [[v] for v in vals]
            n = 0
            for di, row in enumerate(vals):
                for dj, v in enumerate(row):
                    val, fmt = to_cell_value(v, raw)
                    _set_value(plan, sh, r1 + di, c1 + dj, val, pkg.date1904)
                    if (fmt or o.get("number_format")) and plan.styles is not None:
                        code = o.get("number_format") or fmt
                        _restyle(plan, sh, r1 + di, c1 + dj, lambda old, code=code: plan.styles.derive(old, {"number_format": code}))  # type: ignore[union-attr]
                    n += 1
            plan.log.append(f"{i}. set {n} cells from {range_name(r1, c1, r1, c1)} on {sh}")
            if o.get("style"):
                _style_area(plan, sh, r1, c1, r1 + len(vals) - 1, c1 + max(len(x) for x in vals) - 1, dict(o["style"]))
        elif name in ("set", "fill"):
            sh, r1, c1, r2, c2 = area(o, i, name)
            if "formula" in o:
                base: Any = normalize_formula(str(o["formula"]))
                sources = None
            elif "value" in o:
                base = to_cell_value(o["value"], raw)[0]
                sources = None
            elif name == "fill":
                base = None
                down = r2 > r1 or c1 == c2
                sources = [(r1, c) for c in range(c1, c2 + 1)] if down else [(r1, c1)]
            else:
                raise SkillError(f"operation {i} (set) needs 'value', 'formula' or 'values'")
            if sources is None:
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        v = translate(base, r - r1, c - c1) if isinstance(base, str) and base.startswith("=") and type(base) is not LiteralText else base
                        _set_value(plan, sh, r, c, v, pkg.date1904)
                if name == "fill" and o.get("copy_style", True) and plan.styles is not None:
                    _v0, _f0, s0 = _cell_source(plan, sh, r1, c1)
                    for r in range(r1, r2 + 1):
                        for c in range(c1, c2 + 1):
                            if (r, c) != (r1, c1):
                                _restyle(plan, sh, r, c, lambda old, s0=s0: s0)
                if o.get("number_format") and plan.styles is not None:
                    _style_area(plan, sh, r1, c1, r2, c2, {"number_format": o["number_format"]})
                plan.log.append(f"{i}. {name} {range_name(r1, c1, r2, c2)} on {sh}")
            else:
                empty = []
                for (sr, sc) in sources:
                    v0, f0, s0 = _cell_source(plan, sh, sr, sc)
                    src = f0 if f0 is not None else v0
                    if src is None:
                        empty.append(f"{col_letter(sc)}{sr}")
                        continue
                    targets = [(r, sc) for r in range(r1 + 1, r2 + 1)] if down else [(r1, c) for c in range(c1 + 1, c2 + 1)]
                    for r, c in targets:
                        v = translate(src, r - sr, c - sc) if isinstance(src, str) and f0 is not None else src
                        _set_value(plan, sh, r, c, v, pkg.date1904)
                        if o.get("copy_style", True):
                            _restyle(plan, sh, r, c, lambda old, s0=s0: s0)
                if len(empty) == len(sources):
                    raise SkillError(f"operation {i} (fill): {', '.join(empty)} is empty; give 'formula' or 'value'")
                if empty:
                    plan.warnings.append(f"fill {range_name(r1, c1, r2, c2)}: {', '.join(empty[:8])} empty, so those columns were left as they were")
                plan.log.append(f"{i}. filled {range_name(r1, c1, r2, c2)} on {sh} ({'down' if down else 'right'} from the first {'row' if down else 'column'})")
            if o.get("style"):
                _style_area(plan, sh, r1, c1, r2, c2, dict(o["style"]))
        elif name == "clear":
            sh, r1, c1, r2, c2 = area(o, i, name)
            what = o.get("what", "contents")
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    if what in ("contents", "all", "values"):
                        _set_value(plan, sh, r, c, None, pkg.date1904)
                    if what in ("formats", "all", "styles"):
                        _restyle(plan, sh, r, c, lambda old: 0)
            plan.log.append(f"{i}. cleared {what} of {range_name(r1, c1, r2, c2)} on {sh}")
        elif name == "style":
            sh, r1, c1, r2, c2 = area(o, i, name)
            st = {k: v for k, v in o.items() if k not in ("op", "range", "cell", "cells", "sheet")}
            if "format" in st and "number_format" not in st:
                st["number_format"] = st.pop("format")
            if not st:
                raise SkillError(f"operation {i} (style) needs font, fill, border, align or number_format")
            _style_area(plan, sh, r1, c1, r2, c2, dict(st))
            plan.log.append(f"{i}. styled {range_name(r1, c1, r2, c2)} on {sh} ({', '.join(sorted(st))})")
        else:
            raise SkillError(f"operation {i} ({name}) cannot be written in patch mode; use --mode openpyxl")
    return plan


def _style_area(plan: Plan, sheet: str, r1: int, c1: int, r2: int, c2: int, st: dict[str, Any]) -> None:
    from openpyxl.styles.borders import Border

    from _ops import _border_for

    if plan.styles is None:
        raise SkillError("this workbook has no styles part; use --mode openpyxl")
    sb = plan.styles
    border = st.pop("border", None)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            def fn(old: int, r: int = r, c: int = c) -> int:
                b = None
                if border is not None:
                    attrs, _ = sb.xf_attrs(old)
                    cur = sb.border(int(attrs.get("borderId", "0") or 0)) if attrs else Border()
                    b = _border_for(cur, border, r, c, r1, c1, r2, c2)
                return sb.derive(old, st, b)

            _restyle(plan, sheet, r, c, fn)


# ── writing ─────────────────────────────────────────────────────────────


def apply(src: Path, dest: Path, ops: list[dict[str, Any]], default_sheet: str | None, full_calc: bool = False) -> Plan:
    """Plans the ops and writes dest: patched sheets, extended styles.xml, everything else copied raw. full_calc asks
    Excel to recalculate on open (when values changed and no recalculation follows)."""
    with Package(src) as pkg:
        plan = plan_ops(pkg, ops, default_sheet)
        new_xml: dict[str, Any] = {}
        for sheet, cells in plan.changes.items():
            part = pkg.sheet(sheet).path
            data = plan.sheet_xml(sheet)
            _protect_shared(plan, sheet, data, cells)
            pieces = patch_sheet_xml(data, cells, base_style=_base_style(data), chunks=True)
            pieces[0] = _grow_dimension(bytes(pieces[0]), cells)
            new_xml[part] = pieces
        calc_chain = next((t for (typ, t) in pkg._rels.values() if typ.endswith("/calcChain")), None) if plan.values_changed else None
        styles_new = plan.styles.result() if plan.styles is not None and any(plan.styles.added.values()) else None

        def edit(name: str) -> Any:
            if name in new_xml:
                return new_xml[name]
            if full_calc and plan.values_changed and name == pkg.workbook_path:
                return _set_full_calc(pkg.read(name), True)
            if styles_new is not None and name == plan.styles_path:
                return styles_new
            if calc_chain and name == "[Content_Types].xml":
                return re.sub(rb'<Override[^>]*PartName="/' + re.escape(calc_chain.encode()) + rb'"[^>]*/>', b"", pkg.read(name))
            if calc_chain and name == _rels_name(pkg.workbook_path):
                return re.sub(rb'<Relationship[^>]*Type="[^"]*/calcChain"[^>]*/>', b"", pkg.read(name))
            return None

        tmp = temp_beside(dest)
        try:
            rewrite_zip(pkg.zip, tmp, edit, drop={calc_chain} if calc_chain else None)
            publish(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
    return plan


_SHARED_MASTER = re.compile(rb'<(?:\w+:)?c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*>(?:(?!</(?:\w+:)?c>).)*?<(?:\w+:)?f\b([^>]*?\bt="shared"[^>]*)>([^<]+)</', re.S)


def _protect_shared(plan: Plan, sheet: str, xml: bytes, cells: dict[tuple[int, int], CellPatch]) -> None:
    """A cell holding a shared formula's master text is being overwritten: give every other cell of that group its
    own formula, or they would lose theirs."""
    import html

    from _a1 import col_index
    from _formula import FormulaSyntaxError, translate

    over = {k for k, p in cells.items() if not p.keep}
    if not over or b't="shared"' not in xml:
        return
    for m in _SHARED_MASTER.finditer(xml):
        mr, mc = int(m.group(2)), col_index(m.group(1).decode())
        if (mr, mc) not in over:
            continue
        si = re.search(rb'\bsi="(\d+)"', m.group(3))
        ref = re.search(rb'\bref="([^"]+)"', m.group(3))
        if not si:
            continue
        text = "=" + html.unescape(m.group(4).decode("utf-8", "replace"))
        dep = re.compile(rb'<(?:\w+:)?c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*>(?:(?!</(?:\w+:)?c>).)*?<(?:\w+:)?f\b[^>]*?\bsi="' + si.group(1) + rb'"[^>]*/>', re.S)
        lo = xml.rfind(b"<", 0, m.start()) if ref is None else 0
        n = 0
        for d in dep.finditer(xml, max(0, lo)):
            r, c = int(d.group(2)), col_index(d.group(1).decode())
            if (r, c) in cells and not cells[(r, c)].keep:
                continue
            try:
                f = translate(text, r - mr, c - mc)
            except FormulaSyntaxError:
                continue
            p = plan.patch(sheet, r, c)
            p.keep, p.clear, p.const, p.value, p.formula = False, False, False, None, f[1:]
            cells[(r, c)] = p
            n += 1
        if n:
            plan.values_changed = True
            plan.warnings.append(f"{col_letter(mc)}{mr} on {sheet} held the shared formula of {n} other cells; they now carry their own copy of it")


def _grow_dimension(xml: bytes, cells: dict[tuple[int, int], CellPatch]) -> bytes:
    """Widens <dimension ref> to cover cells written outside it."""
    written = [k for k, p in cells.items() if not p.keep and not p.clear]
    if not written:
        return xml
    m = re.search(rb'<(?:\w+:)?dimension\b[^>]*\bref="([^"]*)"', xml[:4096])
    if not m:
        return xml
    try:
        r1, c1, r2, c2 = parse_range(m.group(1).decode())
    except ValueError:
        return xml
    nr1, nc1 = min([r1] + [r for r, _ in written]), min([c1] + [c for _, c in written])
    nr2, nc2 = max([r2] + [r for r, _ in written]), max([c2] + [c for _, c in written])
    if (nr1, nc1, nr2, nc2) == (r1, c1, r2, c2):
        return xml
    return xml[: m.start(1)] + range_name(nr1, nc1, nr2, nc2).encode() + xml[m.end(1) :]


def _base_style(xml: bytes) -> Callable[[int, int, bytes | None], int]:
    """The style a new cell starts from: its row's style (customFormat rows), else its column's, else 0."""
    cols: list[tuple[int, int, int]] = []
    head = xml[: xml.find(b"sheetData")] if b"sheetData" in xml else b""
    for m in re.finditer(rb"<(?:\w+:)?col\b([^>]*)/?>", head):
        a = dict(re.findall(rb'\b(\w+)="([^"]*)"', m.group(1)))
        if b"style" in a:
            try:
                cols.append((int(a.get(b"min", b"1")), int(a.get(b"max", b"1")), int(a[b"style"])))
            except ValueError:
                pass

    def base(r: int, c: int, row_attrs: bytes | None) -> int:
        if row_attrs and b'customFormat="1"' in row_attrs:
            m = re.search(rb'\bs="(\d+)"', row_attrs)
            if m:
                return int(m.group(1))
        for lo, hi, s in cols:
            if lo <= c <= hi:
                return s
        return 0

    return base
