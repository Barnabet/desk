"""Style resolution for Word documents: style chains, headings, and effective paragraph and run properties.

Properties are flattened into plain dicts, `{'b': {}, 'sz': {'val': '24'}, 'spacing': {'after': '160'}, …}`, so
the style chain, numbering and direct formatting merge the way Word layers them.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from _docx import NS, qn

_W = "{" + NS["w"] + "}"
MERGE_ATTRS = {"spacing", "ind", "rFonts", "lang", "shd", "framePr"}
MERGE_CHILDREN = {"pBdr", "tblBorders", "tcBorders", "tblCellMar", "tcMar", "numPr", "bdr"}
SKIP = {"rPrChange", "pPrChange", "sectPr", "tblPrChange", "tcPrChange", "trPrChange", "tblPrExChange", "ins", "del", "moveFrom", "moveTo"}

BUILTIN_NAMES = {"toc heading": "TOC Heading", "html preformatted": "HTML Preformatted"}


def attrs(el: Any) -> dict[str, str]:
    return {etree.QName(k).localname: v for k, v in el.attrib.items()}


def flatten(el: Any) -> dict[str, Any]:
    """An rPr/pPr/tblPr/tcPr/trPr element as a dict of property name -> attributes (or children for borders)."""
    out: dict[str, Any] = {}
    if el is None:
        return out
    for c in el:
        if not isinstance(c.tag, str):
            continue
        name = etree.QName(c).localname
        if name in SKIP or (name == "rPr" and el.tag == qn("w:pPr")):
            continue
        if name in MERGE_CHILDREN:
            out[name] = {etree.QName(x).localname: attrs(x) for x in c if isinstance(x.tag, str)}
        elif name == "tabs":
            out[name] = [attrs(x) for x in c if isinstance(x.tag, str)]
        else:
            out[name] = attrs(c)
    return out


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Layers `over` on `base` the way Word does (attribute-level for spacing, indents, fonts and borders)."""
    if not over:
        return dict(base)
    out = dict(base)
    for k, v in over.items():
        if k in out and (k in MERGE_ATTRS or k in MERGE_CHILDREN):
            merged = dict(out[k])
            merged.update(v)
            if k == "ind":
                # A hanging indent cancels an inherited first-line indent and vice versa.
                if "hanging" in v and "firstLine" not in v:
                    merged.pop("firstLine", None)
                if "firstLine" in v and "hanging" not in v:
                    merged.pop("hanging", None)
            out[k] = merged
        elif k == "tabs" and k in out:
            stops = {t.get("pos"): t for t in out[k]}
            for t in v:
                if t.get("val") == "clear":
                    stops.pop(t.get("pos"), None)
                else:
                    stops[t.get("pos")] = t
            out[k] = sorted(stops.values(), key=lambda t: _pos(t.get("pos")))
        else:
            out[k] = v
    return out


def _pos(v: Any) -> float:
    from _docx import units

    return units(v, 20, 0.0)


def flag(props: dict[str, Any], name: str) -> bool:
    """A toggle property's effective value."""
    v = props.get(name)
    if v is None:
        return False
    val = v.get("val")
    return val is None or val.lower() not in ("0", "false", "off", "none")


class Style:
    __slots__ = ("id", "name", "type", "based_on", "ppr", "rpr", "tblpr", "trpr", "tcpr", "cond", "link", "default", "num")

    def __init__(self, el: Any) -> None:
        w = _W
        self.id = el.get(w + "styleId") or ""
        n = el.find(qn("w:name"))
        self.name = n.get(w + "val") if n is not None else self.id
        self.type = el.get(w + "type") or "paragraph"
        b = el.find(qn("w:basedOn"))
        self.based_on = b.get(w + "val") if b is not None else None
        ln = el.find(qn("w:link"))
        self.link = ln.get(w + "val") if ln is not None else None
        self.default = on_off_attr(el.get(w + "default"))
        self.ppr = flatten(el.find(qn("w:pPr")))
        self.rpr = flatten(el.find(qn("w:rPr")))
        self.tblpr = flatten(el.find(qn("w:tblPr")))
        self.trpr = flatten(el.find(qn("w:trPr")))
        self.tcpr = flatten(el.find(qn("w:tcPr")))
        self.cond: dict[str, dict[str, dict[str, Any]]] = {}
        for c in el.findall(qn("w:tblStylePr")):
            self.cond[c.get(w + "type") or ""] = {
                "ppr": flatten(c.find(qn("w:pPr"))),
                "rpr": flatten(c.find(qn("w:rPr"))),
                "tcpr": flatten(c.find(qn("w:tcPr"))),
                "tblpr": flatten(c.find(qn("w:tblPr"))),
                "trpr": flatten(c.find(qn("w:trPr"))),
            }
        num = self.ppr.get("numPr") or {}
        self.num = (num.get("numId", {}).get("val"), num.get("ilvl", {}).get("val"))


def on_off_attr(v: str | None) -> bool:
    return v is not None and v.lower() in ("1", "true", "on")


def display_name(name: str) -> str:
    """Word's UI name for a style ('heading 1' -> 'Heading 1')."""
    low = name.lower()
    if low in BUILTIN_NAMES:
        return BUILTIN_NAMES[low]
    if re.fullmatch(r"toc \d", low):
        return "TOC " + low[-1]
    if name and name == low:
        return " ".join(w[:1].upper() + w[1:] for w in name.split(" "))
    return name


class Styles:
    """The styles part of a document, resolved."""

    def __init__(self, root: Any, theme: dict[str, Any] | None = None) -> None:
        self.theme = theme or {"major": None, "minor": None, "colors": {}}
        self.by_id: dict[str, Style] = {}
        self.by_name: dict[str, str] = {}
        self.defaults: dict[str, str] = {}
        self.doc_rpr: dict[str, Any] = {}
        self.doc_ppr: dict[str, Any] = {}
        self._chain_cache: dict[str, list[Style]] = {}
        self._pcache: dict[tuple[str, str | None], dict[str, Any]] = {}
        if root is None:
            return
        dd = root.find(qn("w:docDefaults"))
        if dd is not None:
            self.doc_rpr = flatten(dd.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}"))
            self.doc_ppr = flatten(dd.find(f"{qn('w:pPrDefault')}/{qn('w:pPr')}"))
        for el in root.findall(qn("w:style")):
            s = Style(el)
            self.by_id[s.id] = s
            self.by_name.setdefault(s.name.lower(), s.id)
            if s.default:
                self.defaults.setdefault(s.type, s.id)

    def get(self, sid: str | None) -> Style | None:
        return self.by_id.get(sid) if sid else None

    def name(self, sid: str | None, kind: str = "paragraph") -> str:
        s = self.get(sid) or self.get(self.defaults.get(kind))
        return display_name(s.name) if s else ("Normal" if kind == "paragraph" else "")

    def find(self, name_or_id: str, kind: str | None = None) -> str | None:
        """A style id from a UI name ('Heading 1'), an XML name ('heading 1') or an id ('Heading1')."""
        if name_or_id in self.by_id and (kind is None or self.by_id[name_or_id].type == kind):
            return name_or_id
        sid = self.by_name.get(name_or_id.lower())
        if sid and (kind is None or self.by_id[sid].type == kind):
            return sid
        squashed = re.sub(r"\s+", "", name_or_id).lower()
        for s in self.by_id.values():
            if (s.id.lower() == squashed or re.sub(r"\s+", "", s.name).lower() == squashed) and (kind is None or s.type == kind):
                return s.id
        return None

    def chain(self, sid: str | None, kind: str = "paragraph") -> list[Style]:
        """The style and its ancestors, base first."""
        if not sid:
            sid = self.defaults.get(kind)
        if not sid:
            return []
        key = f"{kind}:{sid}"
        if key in self._chain_cache:
            return self._chain_cache[key]
        out: list[Style] = []
        seen: set[str] = set()
        cur = self.get(sid)
        while cur is not None and cur.id not in seen and len(out) < 25:
            out.append(cur)
            seen.add(cur.id)
            cur = self.get(cur.based_on)
        out.reverse()
        self._chain_cache[key] = out
        return out

    def heading_level(self, sid: str | None) -> int | None:
        for s in reversed(self.chain(sid)):
            ol = s.ppr.get("outlineLvl")
            if ol is not None:
                try:
                    v = int(ol.get("val", "9"))
                except ValueError:
                    return None
                return v + 1 if v < 9 else None
        s = self.get(sid)
        if s is not None:
            m = re.fullmatch(r"heading\s*(\d)", s.name.lower())
            if m:
                return int(m.group(1))
        return None

    def is_title(self, sid: str | None) -> bool:
        s = self.get(sid)
        return s is not None and s.name.lower() == "title"

    def para_style_ppr(self, sid: str | None) -> dict[str, Any]:
        key = ("ppr", sid)
        if key not in self._pcache:
            out: dict[str, Any] = {}
            for s in self.chain(sid, "paragraph"):
                out = merge(out, s.ppr)
            self._pcache[key] = out
        return self._pcache[key]

    def para_style_rpr(self, sid: str | None) -> dict[str, Any]:
        key = ("prpr", sid)
        if key not in self._pcache:
            out: dict[str, Any] = {}
            for s in self.chain(sid, "paragraph"):
                out = merge(out, s.rpr)
            self._pcache[key] = out
        return self._pcache[key]

    def char_style_rpr(self, sid: str | None) -> dict[str, Any]:
        if not sid:
            return {}
        key = ("crpr", sid)
        if key not in self._pcache:
            out: dict[str, Any] = {}
            for s in self.chain(sid, "character"):
                out = merge(out, s.rpr)
            self._pcache[key] = out
        return self._pcache[key]

    def table_style(self, sid: str | None) -> dict[str, Any]:
        """Merged table style: tblpr, trpr, tcpr, ppr, rpr and conditional formats along the chain."""
        key = ("tbl", sid)
        if key not in self._pcache:
            res: dict[str, Any] = {"tblpr": {}, "trpr": {}, "tcpr": {}, "ppr": {}, "rpr": {}, "cond": {}}
            for s in self.chain(sid, "table"):
                res["tblpr"] = merge(res["tblpr"], s.tblpr)
                res["trpr"] = merge(res["trpr"], s.trpr)
                res["tcpr"] = merge(res["tcpr"], s.tcpr)
                res["ppr"] = merge(res["ppr"], s.ppr)
                res["rpr"] = merge(res["rpr"], s.rpr)
                for t, c in s.cond.items():
                    prev = res["cond"].get(t, {})
                    res["cond"][t] = {k: merge(prev.get(k, {}), v) for k, v in c.items()}
            self._pcache[key] = res
        return self._pcache[key]

    def num_pr(self, sid: str | None) -> tuple[str | None, str | None]:
        num_id = ilvl = None
        for s in self.chain(sid, "paragraph"):
            if s.num[0] is not None:
                num_id = s.num[0]
            if s.num[1] is not None:
                ilvl = s.num[1]
        return num_id, ilvl

    # fonts and colours
    def font_name(self, rpr: dict[str, Any]) -> str | None:
        f = rpr.get("rFonts") or {}
        for key in ("ascii", "hAnsi"):
            if f.get(key):
                return f[key]
        for key in ("asciiTheme", "hAnsiTheme"):
            t = f.get(key)
            if t:
                return self.theme.get("major" if t.startswith("major") else "minor")
        return self.theme.get("minor")

    def color(self, spec: dict[str, Any] | None, attr: str = "val") -> str | None:
        """An RRGGBB colour from a w:color / w:shd attribute set (theme colours resolved), or None for auto."""
        if not spec:
            return None
        theme_attr = "themeColor" if attr == "val" else "themeFill" if attr == "fill" else None
        if theme_attr and spec.get(theme_attr):
            base = self.theme["colors"].get(spec[theme_attr])
            if base:
                return adjust(base, spec.get("themeTint" if attr == "val" else "themeFillTint"), spec.get("themeShade" if attr == "val" else "themeFillShade"))
        v = spec.get(attr)
        if not v or v.lower() == "auto" or not re.fullmatch(r"[0-9A-Fa-f]{6}", v):
            return None
        return v.upper()


def adjust(hex6: str, tint: str | None, shade: str | None) -> str:
    try:
        r, g, b = (int(hex6[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex6
    if tint:
        f = int(tint, 16) / 255
        r, g, b = (round(c * f + 255 * (1 - f)) for c in (r, g, b))
    if shade:
        f = int(shade, 16) / 255
        r, g, b = (round(c * f) for c in (r, g, b))
    return f"{r:02X}{g:02X}{b:02X}"


HIGHLIGHT = {
    "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF", "blue": "0000FF", "red": "FF0000",
    "darkBlue": "000080", "darkCyan": "008080", "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000",
    "darkYellow": "808000", "darkGray": "808080", "lightGray": "C0C0C0", "black": "000000", "white": "FFFFFF",
}
