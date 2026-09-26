"""DrawingML / PresentationML resolution for the presentations skill.

Works on the raw XML (lxml) that python-pptx loads, because rendering and linting need what PowerPoint shows, not
only what a shape states: colours through the theme and colour map, text styles inherited from the layout, master and
presentation defaults, placeholder geometry inherited from the layout and master, group transforms, backgrounds, and
the theme's style matrix (fillRef, lnRef, fontRef). Standard library + python-pptx objects only.
"""

from __future__ import annotations

import colorsys
import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]
C = "{%s}" % NS["c"]

EMU_PER_IN = 914400
EMU_PER_PT = 12700
EMU_PER_CM = 360000

RT_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
RT_TABLE_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles"
RT_DIAGRAM_DRAWING = "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing"


def local(el: Any) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1]


def child(el: Any, name: str) -> Any:
    """First child whose local name is `name` (any namespace)."""
    if el is None:
        return None
    for c in el:
        if isinstance(c.tag, str) and c.tag.rsplit("}", 1)[-1] == name:
            return c
    return None


def children(el: Any, name: str) -> list[Any]:
    if el is None:
        return []
    return [c for c in el if isinstance(c.tag, str) and c.tag.rsplit("}", 1)[-1] == name]


def path(el: Any, *names: str) -> Any:
    for n in names:
        el = child(el, n)
        if el is None:
            return None
    return el


def iattr(el: Any, name: str, default: int | None = None) -> int | None:
    if el is None:
        return default
    v = el.get(name)
    if v is None:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def emu_in(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v / EMU_PER_IN, nd)


# ── lengths given by agents ─────────────────────────────────────────────


def parse_length(value: Any, total_emu: int | None = None) -> int:
    """EMU from 2 / 2.5 (inches), '2in', '5cm', '40mm', '72pt', '120px' (96 dpi), '10%' (of total_emu), '914400emu'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(round(float(value) * EMU_PER_IN))
    s = str(value).strip().lower()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(in|cm|mm|pt|px|emu|%)?", s)
    if not m:
        raise ValueError(f"bad length '{value}' (use inches like 1.5, or '4cm', '72pt', '120px', '10%')")
    n, unit = float(m.group(1)), m.group(2) or "in"
    if unit == "%":
        if total_emu is None:
            raise ValueError(f"'{value}': a percentage is not allowed here")
        return int(round(total_emu * n / 100))
    factor = {"in": EMU_PER_IN, "cm": EMU_PER_CM, "mm": EMU_PER_CM / 10, "pt": EMU_PER_PT, "px": EMU_PER_IN / 96, "emu": 1}[unit]
    return int(round(n * factor))


# ── colours ─────────────────────────────────────────────────────────────


@dataclass
class Color:
    r: int
    g: int
    b: int
    a: float = 1.0

    @property
    def hex(self) -> str:
        return f"{self.r:02X}{self.g:02X}{self.b:02X}"

    def typst(self) -> str:
        if self.a >= 0.999:
            return f'rgb("#{self.hex}")'
        return f'rgb("#{self.hex}{max(0, min(255, round(self.a * 255))):02X}")'

    def luminance(self) -> float:
        def ch(v: int) -> float:
            c = v / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(self.r) + 0.7152 * ch(self.g) + 0.0722 * ch(self.b)

    def over(self, bg: "Color") -> "Color":
        """This colour composited over an opaque background."""
        a = self.a
        return Color(round(self.r * a + bg.r * (1 - a)), round(self.g * a + bg.g * (1 - a)), round(self.b * a + bg.b * (1 - a)))

    @staticmethod
    def from_hex(h: str) -> "Color":
        h = h.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", h):
            raise ValueError(f"bad colour '{h}' (use #RRGGBB)")
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return Color(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def contrast_ratio(fg: Color, bg: Color) -> float:
    l1, l2 = fg.luminance(), bg.luminance()
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


PRESET_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "yellow": "FFFF00",
    "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3", "orange": "FFA500",
    "purple": "800080", "navy": "000080", "teal": "008080", "silver": "C0C0C0", "maroon": "800000", "cyan": "00FFFF",
    "magenta": "FF00FF", "darkBlue": "00008B", "darkRed": "8B0000", "darkGreen": "006400", "ltGray": "D3D3D3",
    "dkGray": "A9A9A9", "gold": "FFD700", "pink": "FFC0CB", "brown": "A52A2A",
}
SYS_COLORS = {"windowText": "000000", "window": "FFFFFF", "btnFace": "F0F0F0", "btnText": "000000", "highlight": "0078D7", "highlightText": "FFFFFF", "grayText": "6D6D6D", "menuText": "000000"}
SCHEME_NAMES = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"]
DEFAULT_CLR_MAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2", "accent1": "accent1", "accent2": "accent2", "accent3": "accent3", "accent4": "accent4", "accent5": "accent5", "accent6": "accent6", "hlink": "hlink", "folHlink": "folHlink"}
OFFICE_SCHEME = {"dk1": "000000", "lt1": "FFFFFF", "dk2": "44546A", "lt2": "E7E6E6", "accent1": "4472C4", "accent2": "ED7D31", "accent3": "A5A5A5", "accent4": "FFC000", "accent5": "5B9BD5", "accent6": "70AD47", "hlink": "0563C1", "folHlink": "954F72"}


def _hsl_mod(rgb: tuple[float, float, float], mods: list[tuple[str, int]]) -> tuple[float, float, float]:
    h, l, s = colorsys.rgb_to_hls(*rgb)
    for name, v in mods:
        f = v / 100000
        if name == "lumMod":
            l *= f
        elif name == "lumOff":
            l += f
        elif name == "satMod":
            s *= f
        elif name == "satOff":
            s += f
        elif name == "hueMod":
            h = (h * f) % 1.0
        elif name == "hueOff":
            h = (h + v / 60000 / 360) % 1.0
        elif name == "hue":
            h = (v / 60000 / 360) % 1.0
        elif name == "sat":
            s = f
        elif name == "lum":
            l = f
    return colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))


class Theme:
    """A parsed theme part: colour scheme, fonts and the style matrix."""

    def __init__(self, xml: Any | None):
        self.el = xml
        self.name = xml.get("name", "") if xml is not None else ""
        self.colors: dict[str, str] = dict(OFFICE_SCHEME)
        self.major = "Calibri Light"
        self.minor = "Calibri"
        self.major_ea = self.minor_ea = ""
        self.fills: list[Any] = []
        self.lines: list[Any] = []
        self.bg_fills: list[Any] = []
        if xml is None:
            return
        te = child(xml, "themeElements")
        cs = child(te, "clrScheme")
        if cs is not None:
            for n in SCHEME_NAMES:
                e = child(cs, n)
                if e is None or not len(e):
                    continue
                c = e[0]
                ln = local(c)
                if ln == "srgbClr":
                    self.colors[n] = (c.get("val") or "000000").upper()
                elif ln == "sysClr":
                    self.colors[n] = (c.get("lastClr") or SYS_COLORS.get(c.get("val", ""), "000000")).upper()
        fs = child(te, "fontScheme")
        if fs is not None:
            mj, mn = child(fs, "majorFont"), child(fs, "minorFont")
            self.major = (child(mj, "latin").get("typeface") if child(mj, "latin") is not None else None) or self.major
            self.minor = (child(mn, "latin").get("typeface") if child(mn, "latin") is not None else None) or self.minor
            self.major_ea = child(mj, "ea").get("typeface", "") if child(mj, "ea") is not None else ""
            self.minor_ea = child(mn, "ea").get("typeface", "") if child(mn, "ea") is not None else ""
        fm = child(te, "fmtScheme")
        if fm is not None:
            for attr, name in (("fills", "fillStyleLst"), ("lines", "lnStyleLst"), ("bg_fills", "bgFillStyleLst")):
                lst = child(fm, name)
                setattr(self, attr, list(lst) if lst is not None else [])

    def font(self, typeface: str | None) -> str | None:
        """Resolves +mj-lt / +mn-lt theme font references."""
        if not typeface:
            return None
        if typeface.startswith("+mj"):
            return self.major_ea if typeface.endswith("-ea") and self.major_ea else self.major
        if typeface.startswith("+mn"):
            return self.minor_ea if typeface.endswith("-ea") and self.minor_ea else self.minor
        return typeface


@dataclass
class ColorCtx:
    theme: Theme
    clr_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLR_MAP))

    def scheme_hex(self, name: str) -> str | None:
        name = self.clr_map.get(name, name)
        return self.theme.colors.get(name)

    def resolve(self, cel: Any, ph: Color | None = None) -> Color | None:
        """A colour element (srgbClr, schemeClr, sysClr, prstClr, scrgbClr, hslClr) → Color, with its modifiers."""
        if cel is None:
            return None
        ln = local(cel)
        base: Color | None = None
        if ln == "srgbClr":
            try:
                base = Color.from_hex(cel.get("val", "000000"))
            except ValueError:
                base = Color(0, 0, 0)
        elif ln == "schemeClr":
            v = cel.get("val", "tx1")
            if v == "phClr":
                base = Color(ph.r, ph.g, ph.b, ph.a) if ph else Color(0, 0, 0)
            else:
                h = self.scheme_hex(v)
                base = Color.from_hex(h) if h else Color(0, 0, 0)
        elif ln == "sysClr":
            base = Color.from_hex(cel.get("lastClr") or SYS_COLORS.get(cel.get("val", ""), "000000"))
        elif ln == "prstClr":
            base = Color.from_hex(PRESET_COLORS.get(cel.get("val", "black"), "000000"))
        elif ln == "scrgbClr":

            def lin(v: str | None) -> int:
                x = max(0.0, min(1.0, int(v or 0) / 100000))
                x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
                return round(x * 255)

            base = Color(lin(cel.get("r")), lin(cel.get("g")), lin(cel.get("b")))
        elif ln == "hslClr":
            r, g, b = colorsys.hls_to_rgb(int(cel.get("hue", 0)) / 60000 / 360, int(cel.get("lum", 0)) / 100000, int(cel.get("sat", 0)) / 100000)
            base = Color(round(r * 255), round(g * 255), round(b * 255))
        else:
            return None
        return apply_mods(base, cel)


def apply_mods(base: Color, cel: Any) -> Color:
    rgb = (base.r / 255, base.g / 255, base.b / 255)
    alpha = base.a
    hsl_mods: list[tuple[str, int]] = []

    def flush(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
        if hsl_mods:
            rgb = _hsl_mod(rgb, hsl_mods)
            hsl_mods.clear()
        return rgb

    for m in cel:
        n = local(m)
        try:
            v = int(m.get("val", "0"))
        except ValueError:
            continue
        if n in ("lumMod", "lumOff", "satMod", "satOff", "hueMod", "hueOff", "hue", "sat", "lum"):
            hsl_mods.append((n, v))
        elif n == "tint":
            # Office applies tint and shade in linear light (scRGB), not on sRGB values.
            rgb = flush(rgb)
            t = v / 100000
            rgb = tuple(_to_srgb(_to_linear(c) * t + (1 - t)) for c in rgb)  # type: ignore[assignment]
        elif n == "shade":
            rgb = flush(rgb)
            s = v / 100000
            rgb = tuple(_to_srgb(_to_linear(c) * s) for c in rgb)  # type: ignore[assignment]
        elif n == "alpha":
            alpha = v / 100000
        elif n == "alphaMod":
            alpha *= v / 100000
        elif n == "alphaOff":
            alpha += v / 100000
        elif n == "inv":
            rgb = flush(rgb)
            rgb = tuple(1 - c for c in rgb)  # type: ignore[assignment]
        elif n == "gray":
            rgb = flush(rgb)
            g = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
            rgb = (g, g, g)
        elif n == "comp":
            rgb = flush(rgb)
            h, l, s = colorsys.rgb_to_hls(*rgb)
            rgb = colorsys.hls_to_rgb((h + 0.5) % 1, l, s)
    rgb = flush(rgb)
    return Color(*(max(0, min(255, round(c * 255))) for c in rgb), a=max(0.0, min(1.0, alpha)))  # type: ignore[misc]


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


COLOR_TAGS = ("srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr")


def color_child(el: Any) -> Any:
    if el is None:
        return None
    for c in el:
        if local(c) in COLOR_TAGS:
            return c
    return None


# ── fills and lines ─────────────────────────────────────────────────────


@dataclass
class Fill:
    kind: str  # none | solid | gradient | image | pattern
    color: Color | None = None
    stops: list[tuple[float, Color]] = field(default_factory=list)
    angle: float = 90.0  # degrees, DrawingML convention: 0 = left→right, 90 = top→bottom
    radial: bool = False
    focus: tuple[float, float] = (0.5, 0.5)  # radial gradients: centre of the fill-to rectangle, as fractions
    rid: str | None = None  # blip for image fills
    part: Any = None  # the part owning rid
    tile: bool = False
    src_rect: tuple[float, float, float, float] = (0, 0, 0, 0)
    tile_scale: tuple[float, float] = (1.0, 1.0)

    def average(self) -> Color | None:
        """A representative solid colour (for contrast checks)."""
        if self.kind == "solid":
            return self.color
        if self.kind in ("gradient", "pattern") and self.stops:
            n = len(self.stops)
            return Color(round(sum(c.r for _, c in self.stops) / n), round(sum(c.g for _, c in self.stops) / n), round(sum(c.b for _, c in self.stops) / n), sum(c.a for _, c in self.stops) / n)
        return None


FILL_TAGS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")


def fill_el(spPr: Any) -> Any:
    if spPr is None:
        return None
    for c in spPr:
        if local(c) in FILL_TAGS:
            return c
    return None


def parse_fill(fe: Any, cc: ColorCtx, ph: Color | None = None, part: Any = None) -> Fill | None:
    """A fill element → Fill; None when it is grpFill or unknown (caller decides)."""
    if fe is None:
        return None
    n = local(fe)
    if n == "noFill":
        return Fill("none")
    if n == "solidFill":
        col = cc.resolve(color_child(fe), ph)
        return Fill("solid", color=col) if col else Fill("none")
    if n == "gradFill":
        stops = []
        for gs in children(child(fe, "gsLst"), "gs"):
            col = cc.resolve(color_child(gs), ph)
            if col:
                stops.append((int(gs.get("pos", "0")) / 100000, col))
        stops.sort(key=lambda s: s[0])
        lin = child(fe, "lin")
        pth = child(fe, "path")
        radial = pth is not None
        focus = (0.5, 0.5)
        if pth is not None:
            ftr = child(pth, "fillToRect")
            if ftr is not None:
                l_, t_, r_, b_ = (int(ftr.get(k, "0")) / 100000 for k in ("l", "t", "r", "b"))
                focus = ((l_ + 1 - r_) / 2, (t_ + 1 - b_) / 2)
        ang = int(lin.get("ang", "0")) / 60000 if lin is not None else 90.0
        if not stops:
            return Fill("none")
        if len(stops) == 1:
            return Fill("solid", color=stops[0][1])
        return Fill("gradient", stops=stops, angle=ang, radial=radial, focus=focus)
    if n == "blipFill":
        blip = child(fe, "blip")
        rid = blip.get(R + "embed") if blip is not None else None
        sr = child(fe, "srcRect")
        rect = tuple(int(sr.get(k, "0")) / 100000 for k in ("l", "t", "r", "b")) if sr is not None else (0, 0, 0, 0)
        tile = child(fe, "tile")
        scale = (int(tile.get("sx", "100000")) / 100000, int(tile.get("sy", "100000")) / 100000) if tile is not None else (1.0, 1.0)
        return Fill("image", rid=rid, part=part, tile=tile is not None, src_rect=rect, tile_scale=scale)  # type: ignore[arg-type]
    if n == "pattFill":
        fg = cc.resolve(color_child(child(fe, "fgClr")), ph) or Color(0, 0, 0)
        bg = cc.resolve(color_child(child(fe, "bgClr")), ph) or Color(255, 255, 255)
        # Drawn as the blend of the two colours, which is what a pattern reads as from a distance.
        return Fill("pattern", color=Color(round(fg.r * 0.35 + bg.r * 0.65), round(fg.g * 0.35 + bg.g * 0.65), round(fg.b * 0.35 + bg.b * 0.65)), stops=[(0, fg), (1, bg)])
    return None


@dataclass
class Line:
    color: Color | None
    width_pt: float = 0.75
    dash: str = "solid"
    head: str | None = None
    tail: str | None = None
    fill: Fill | None = None


def parse_line(ln: Any, cc: ColorCtx, ref_line: Line | None = None, ph: Color | None = None) -> Line | None:
    """spPr/a:ln merged over the theme line style (lnRef); None means no outline."""
    base = ref_line
    if ln is None:
        return base
    fe = fill_el(ln)
    color = base.color if base else None
    lf: Fill | None = base.fill if base else None
    if fe is not None:
        f = parse_fill(fe, cc, ph)
        if f is None or f.kind == "none":
            return None
        lf = f
        color = f.color if f.kind == "solid" else (f.stops[0][1] if f.stops else None)
    if color is None:
        return None
    w = iattr(ln, "w")
    width = w / EMU_PER_PT if w is not None else (base.width_pt if base else 0.75)
    pd = child(ln, "prstDash")
    dash = pd.get("val", "solid") if pd is not None else (base.dash if base else "solid")
    he, te = child(ln, "headEnd"), child(ln, "tailEnd")
    head = he.get("type") if he is not None and he.get("type") not in (None, "none") else (base.head if base else None)
    tail = te.get("type") if te is not None and te.get("type") not in (None, "none") else (base.tail if base else None)
    return Line(color, max(width, 0.25), dash, head, tail, lf)


# ── the slide context ───────────────────────────────────────────────────


def part_xml(part: Any) -> Any:
    """lxml root of a python-pptx part (XmlPart has _element; blob parts are parsed)."""
    el = getattr(part, "_element", None)
    if el is not None:
        return el
    from lxml import etree

    return etree.fromstring(part.blob)


def related(part: Any, reltype: str) -> Any:
    try:
        return part.part_related_by(reltype)
    except KeyError:
        return None


def _clr_map(el: Any) -> dict[str, str] | None:
    if el is None:
        return None
    return {k: v for k, v in el.attrib.items()}


@dataclass
class PresDefaults:
    size: tuple[int, int]
    default_text_style: Any  # p:defaultTextStyle
    table_styles: dict[str, Any]
    default_table_style: str | None


def pres_defaults(prs: Any) -> PresDefaults:
    el = prs.part._element
    dts = child(el, "defaultTextStyle")
    styles: dict[str, Any] = {}
    default = None
    tsp = related(prs.part, RT_TABLE_STYLES)
    if tsp is not None:
        try:
            root = part_xml(tsp)
            default = root.get("def")
            for ts in children(root, "tblStyle"):
                styles[ts.get("styleId", "")] = ts
        except Exception:  # noqa: BLE001 — a damaged styles part only loses table styling
            pass
    return PresDefaults((prs.slide_width or 9144000, prs.slide_height or 6858000), dts, styles, default)


class SlideCtx:
    """Everything needed to resolve what a slide (or layout, or master) looks like."""

    def __init__(self, prs: Any, slide: Any, defaults: PresDefaults, number: int = 0, kind: str = "slide"):
        self.prs = prs
        self.number = number
        self.kind = kind
        self.defaults = defaults
        if kind == "slide":
            self.slide_part = slide.part
            self.layout_part = slide.slide_layout.part
            self.master_part = slide.slide_layout.slide_master.part
        elif kind == "layout":
            self.slide_part = None
            self.layout_part = slide.part
            self.master_part = slide.slide_master.part
        else:
            self.slide_part = None
            self.layout_part = None
            self.master_part = slide.part
        self.slide_el = self.slide_part._element if self.slide_part is not None else None
        self.layout_el = self.layout_part._element if self.layout_part is not None else None
        self.master_el = self.master_part._element
        tp = related(self.master_part, RT_THEME)
        self.theme = _theme_cache(tp)
        cmap = dict(DEFAULT_CLR_MAP)
        cmap.update(_clr_map(child(self.master_el, "clrMap")) or {})
        for el in (self.layout_el, self.slide_el):
            ov = child(child(el, "clrMapOvr"), "overrideClrMapping") if el is not None else None
            if ov is not None:
                cmap.update(_clr_map(ov) or {})
        self.cc = ColorCtx(self.theme, cmap)
        self.width, self.height = defaults.size
        self._ph_cache: dict[tuple[str, str, str], Any] = {}

    # placeholders ------------------------------------------------------

    def layout_ph(self, ph_type: str, idx: str) -> Any:
        return self._find_ph(self.layout_el, ph_type, idx, "layout")

    def master_ph(self, ph_type: str) -> Any:
        return self._find_ph(self.master_el, MASTER_PH_TYPE.get(ph_type, "body"), None, "master")

    def _find_ph(self, root: Any, ph_type: str, idx: str | None, where: str) -> Any:
        if root is None:
            return None
        key = (where, ph_type, idx or "")
        if key in self._ph_cache:
            return self._ph_cache[key]
        tree = path(root, "cSld", "spTree")
        by_idx = by_type = None
        want = _norm_type(ph_type)
        for sp in tree.iter(P + "sp", P + "pic", P + "graphicFrame") if tree is not None else ():
            ph = ph_of(sp)
            if ph is None:
                continue
            t, i = ph
            if idx is not None and i == idx and by_idx is None and not (idx == "0" and _norm_type(t) != want):
                by_idx = sp
            if _norm_type(t) == want and by_type is None:
                by_type = sp
        found = by_idx if by_idx is not None else by_type
        self._ph_cache[key] = found
        return found

    def ph_chain(self, sp: Any, owner: str = "slide") -> list[Any]:
        """[layout placeholder, master placeholder] this placeholder inherits from (skipping missing ones)."""
        ph = ph_of(sp)
        if ph is None:
            return []
        t, i = ph
        out = []
        if owner == "slide":
            lp = self.layout_ph(t, i)
            if lp is not None:
                out.append(lp)
                lt = ph_of(lp)
                t = lt[0] if lt else t
        if owner in ("slide", "layout"):
            mp = self.master_ph(t)
            if mp is not None:
                out.append(mp)
        return out

    # text styles ---------------------------------------------------------

    def master_text_style(self, ph_type: str | None) -> Any:
        tx = child(self.master_el, "txStyles")
        if tx is None:
            return None
        if ph_type is None:
            return None
        mt = MASTER_PH_TYPE.get(ph_type, "body")
        if mt == "title":
            return child(tx, "titleStyle")
        if mt == "body":
            return child(tx, "bodyStyle")
        return child(tx, "otherStyle")

    def image_blob(self, part: Any, rid: str | None) -> tuple[bytes, str] | None:
        if not rid or part is None:
            return None
        try:
            rel = part.rels[rid]
        except KeyError:
            return None
        if getattr(rel, "is_external", False):
            return None
        tp = rel.target_part
        return tp.blob, (getattr(tp, "content_type", "") or "")


_THEMES: dict[int, Theme] = {}


def _theme_cache(tp: Any) -> Theme:
    if tp is None:
        return Theme(None)
    key = id(tp)
    if key not in _THEMES:
        try:
            _THEMES[key] = Theme(part_xml(tp))
        except Exception:  # noqa: BLE001
            _THEMES[key] = Theme(None)
    return _THEMES[key]


MASTER_PH_TYPE = {
    "title": "title", "ctrTitle": "title", "body": "body", "obj": "body", "subTitle": "body", "chart": "body", "tbl": "body",
    "clipArt": "body", "dgm": "body", "media": "body", "pic": "body", "sldImg": "body", "dt": "dt", "ftr": "ftr", "sldNum": "sldNum", "hdr": "hdr",
}


def _norm_type(t: str) -> str:
    return "title" if t in ("title", "ctrTitle") else t


def ph_of(sp: Any) -> tuple[str, str] | None:
    """(type, idx) of a placeholder shape, or None."""
    for nv in sp:
        if local(nv).startswith("nv") and local(nv).endswith("Pr"):
            ph = path(nv, "nvPr", "ph")
            if ph is None:
                return None
            return ph.get("type", "obj"), ph.get("idx", "0")
    return None


def shape_name(sp: Any) -> tuple[str, int]:
    for nv in sp:
        if local(nv).startswith("nv"):
            c = child(nv, "cNvPr")
            if c is not None:
                return c.get("name", ""), int(c.get("id", "0") or 0)
    return "", 0


# ── geometry ────────────────────────────────────────────────────────────


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    rot: float = 0.0
    flip_h: bool = False
    flip_v: bool = False

    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def intersect(self, o: "Box") -> float:
        ix = min(self.x + self.w, o.x + o.w) - max(self.x, o.x)
        iy = min(self.y + self.h, o.y + o.h) - max(self.y, o.y)
        return ix * iy if ix > 0 and iy > 0 else 0.0

    def contains(self, o: "Box", tol: float = 0) -> bool:
        return o.x >= self.x - tol and o.y >= self.y - tol and o.x + o.w <= self.x + self.w + tol and o.y + o.h <= self.y + self.h + tol


@dataclass
class Transform:
    """Maps child coordinates of nested groups to slide EMU."""

    ox: float = 0.0
    oy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0

    def apply(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return self.ox + x * self.sx, self.oy + y * self.sy, w * self.sx, h * self.sy


def xfrm_of(sp: Any) -> Any:
    ln = local(sp)
    if ln == "graphicFrame":
        return child(sp, "xfrm")
    if ln == "grpSp":
        return child(child(sp, "grpSpPr"), "xfrm")
    return child(child(sp, "spPr"), "xfrm")


def read_xfrm(x: Any) -> Box | None:
    if x is None:
        return None
    off, ext = child(x, "off"), child(x, "ext")
    if off is None or ext is None:
        return None
    return Box(float(off.get("x", 0)), float(off.get("y", 0)), float(ext.get("cx", 0)), float(ext.get("cy", 0)), int(x.get("rot", "0") or 0) / 60000, x.get("flipH") in ("1", "true"), x.get("flipV") in ("1", "true"))


def shape_box(ctx: SlideCtx, sp: Any, tf: Transform | None = None, owner: str = "slide") -> Box | None:
    """Absolute box in EMU, inheriting placeholder geometry from the layout and master."""
    b = read_xfrm(xfrm_of(sp))
    if b is None and ph_of(sp) is not None:
        for base in ctx.ph_chain(sp, owner):
            b = read_xfrm(xfrm_of(base))
            if b is not None:
                break
    if b is None:
        return None
    if tf is not None:
        x, y, w, h = tf.apply(b.x, b.y, b.w, b.h)
        b = Box(x, y, w, h, b.rot, b.flip_h, b.flip_v)
    return b


def group_transform(grp: Any, tf: Transform) -> Transform:
    x = child(child(grp, "grpSpPr"), "xfrm")
    if x is None:
        return tf
    off, ext, choff, chext = child(x, "off"), child(x, "ext"), child(x, "chOff"), child(x, "chExt")
    if off is None or ext is None:
        return tf
    ox, oy = float(off.get("x", 0)), float(off.get("y", 0))
    cx, cy = float(ext.get("cx", 0)), float(ext.get("cy", 0))
    chx, chy = (float(choff.get("x", 0)), float(choff.get("y", 0))) if choff is not None else (ox, oy)
    chcx, chcy = (float(chext.get("cx", 0)), float(chext.get("cy", 0))) if chext is not None else (cx, cy)
    sx = cx / chcx if chcx else 1.0
    sy = cy / chcy if chcy else 1.0
    # child point p → group space: ox + (p - chx) * sx → slide: tf.apply
    nox, noy, _, _ = tf.apply(ox - chx * sx, oy - chy * sy, 0, 0)
    return Transform(nox, noy, tf.sx * sx, tf.sy * sy)


SHAPE_TAGS = ("sp", "pic", "graphicFrame", "grpSp", "cxnSp", "contentPart")


def iter_tree(tree: Any, tf: Transform | None = None, prefer: str = "fallback", depth: int = 0) -> Iterator[tuple[Any, Transform, int]]:
    """Yields (shape element, transform, group depth) in z-order, descending into groups and AlternateContent."""
    tf = tf or Transform()
    for el in tree:
        ln = local(el)
        if ln == "AlternateContent":
            pick = _pick_alternate(el, prefer)
            if pick is not None:
                yield from iter_tree(pick, tf, prefer, depth)
            continue
        if ln not in SHAPE_TAGS:
            continue
        yield el, tf, depth
        if ln == "grpSp":
            yield from iter_tree(el, group_transform(el, tf), prefer, depth + 1)


def _pick_alternate(el: Any, prefer: str) -> Any:
    choice = child(el, "Choice")
    fb = child(el, "Fallback")
    if prefer == "fallback" and fb is not None and len(fb):
        return fb
    return choice if choice is not None else fb


def geometry(sp: Any, ctx: SlideCtx | None = None, owner: str = "slide") -> tuple[str, dict[str, int], Any]:
    """(preset name or 'custom', adjust values, custGeom element)."""
    spPr = child(sp, "spPr")
    pg = child(spPr, "prstGeom")
    if pg is not None:
        adj = {}
        for gd in children(child(pg, "avLst"), "gd"):
            m = re.match(r"val\s+(-?\d+)", gd.get("fmla", ""))
            if m:
                adj[gd.get("name", "")] = int(m.group(1))
        return pg.get("prst", "rect"), adj, None
    cg = child(spPr, "custGeom")
    if cg is not None:
        return "custom", {}, cg
    if ctx is not None and ph_of(sp) is not None:
        for base in ctx.ph_chain(sp, owner):
            g = geometry(base)
            if g[0] != "rect" or g[2] is not None:
                return g
    return "rect", {}, None


# ── style resolution ────────────────────────────────────────────────────


def style_refs(sp: Any) -> dict[str, Any]:
    st = child(sp, "style")
    if st is None:
        return {}
    return {local(c): c for c in st}


def shape_fill(ctx: SlideCtx, sp: Any, owner: str = "slide", group_fill: Fill | None = None, part: Any = None) -> Fill | None:
    """The fill a shape shows: its own spPr, a placeholder's inherited spPr, or the theme style (fillRef)."""
    chain = [sp] + (ctx.ph_chain(sp, owner) if ph_of(sp) is not None else [])
    for i, el in enumerate(chain):
        fe = fill_el(child(el, "spPr"))
        if fe is not None:
            if local(fe) == "grpFill":
                return group_fill
            return parse_fill(fe, ctx.cc, part=part if i == 0 else None)
    refs = style_refs(sp)
    fr = refs.get("fillRef")
    if fr is not None:
        idx = int(fr.get("idx", "0") or 0)
        ph = ctx.cc.resolve(color_child(fr))
        if idx == 0:
            return Fill("none")
        lst = ctx.theme.fills if idx < 1000 else ctx.theme.bg_fills
        k = (idx if idx < 1000 else idx - 1000) - 1
        if 0 <= k < len(lst):
            f = parse_fill(lst[k], ctx.cc, ph)
            if f is not None:
                return f
        return Fill("solid", color=ph) if ph else None
    return None


def shape_line(ctx: SlideCtx, sp: Any, owner: str = "slide") -> Line | None:
    refs = style_refs(sp)
    ref_line = None
    lr = refs.get("lnRef")
    if lr is not None:
        idx = int(lr.get("idx", "0") or 0)
        ph = ctx.cc.resolve(color_child(lr))
        if idx > 0 and ph is not None:
            k = idx - 1
            if 0 <= k < len(ctx.theme.lines):
                ref_line = parse_line(ctx.theme.lines[k], ctx.cc, None, ph)
            else:
                ref_line = Line(ph, 0.75)
    chain = [sp] + (ctx.ph_chain(sp, owner) if ph_of(sp) is not None else [])
    for el in chain:
        ln = child(child(el, "spPr"), "ln")
        if ln is not None:
            ph = ctx.cc.resolve(color_child(lr)) if lr is not None else None
            return parse_line(ln, ctx.cc, ref_line, ph)
    return ref_line


def background(ctx: SlideCtx) -> tuple[Fill, Any]:
    """The slide's background fill and the part owning any image (slide → layout → master)."""
    for el, part in ((ctx.slide_el, ctx.slide_part), (ctx.layout_el, ctx.layout_part), (ctx.master_el, ctx.master_part)):
        if el is None:
            continue
        bg = path(el, "cSld", "bg")
        if bg is None:
            continue
        bgpr = child(bg, "bgPr")
        if bgpr is not None:
            f = parse_fill(fill_el(bgpr), ctx.cc, part=part)
            if f is not None:
                return f, part
        ref = child(bg, "bgRef")
        if ref is not None:
            idx = int(ref.get("idx", "0") or 0)
            ph = ctx.cc.resolve(color_child(ref))
            lst = ctx.theme.bg_fills if idx >= 1000 else ctx.theme.fills
            k = (idx - 1001) if idx >= 1000 else idx - 1
            if 0 <= k < len(lst):
                f = parse_fill(lst[k], ctx.cc, ph, part=ctx.master_part)
                if f is not None:
                    return f, ctx.master_part
            if ph is not None:
                return Fill("solid", color=ph), part
    white = ctx.cc.scheme_hex("bg1") or "FFFFFF"
    return Fill("solid", color=Color.from_hex(white)), None


def show_master_shapes(el: Any) -> bool:
    return el is None or el.get("showMasterSp") not in ("0", "false")


# ── text properties ─────────────────────────────────────────────────────


@dataclass
class RunStyle:
    font: str | None = None
    size: float = 18.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    color: Color | None = None
    baseline: float = 0.0
    caps: bool = False
    highlight: Color | None = None
    link: str | None = None
    color_src: str = "default"  # run | list | ref | inherited | default


@dataclass
class ParaStyle:
    level: int = 0
    align: str = "l"
    mar_l: float = 0.0  # pt
    indent: float = 0.0  # pt
    spc_before: float = 0.0  # pt (resolved)
    spc_after: float = 0.0
    line_pct: float | None = 1.0  # multiple of single spacing
    line_pts: float | None = None
    bullet: str | None = None  # the bullet text, resolved per paragraph
    bullet_auto: str | None = None  # autonumber scheme
    bullet_start: int = 1
    bullet_color: Color | None = None
    bullet_font: str | None = None
    bullet_size_pct: float = 1.0
    rtl: bool = False


@dataclass
class Run:
    text: str
    style: RunStyle


@dataclass
class Para:
    runs: list[Run]
    style: ParaStyle
    end_size: float = 18.0

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class BodyProps:
    l: float = 7.2  # pt
    t: float = 3.6
    r: float = 7.2
    b: float = 3.6
    anchor: str = "t"
    anchor_ctr: bool = False
    wrap: bool = True
    vert: str = "horz"
    autofit: str = "none"  # none | norm | shape
    font_scale: float = 1.0
    ln_reduction: float = 0.0
    columns: int = 1
    rot: float = 0.0


@dataclass
class TextFrame:
    paras: list[Para]
    body: BodyProps

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paras)

    def words(self) -> int:
        return len(re.findall(r"\w+", self.text))


class TextResolver:
    """Resolves effective paragraph and run properties for one text body through the inheritance chain."""

    def __init__(self, ctx: SlideCtx, sp: Any | None, txBody: Any, owner: str = "slide", ph_type: str | None = None, extra_lst: list[Any] | None = None, font_ref: Any = None, table_cell: bool = False):
        self.ctx = ctx
        self.txBody = txBody
        cc = ctx.cc
        self.lst_chain: list[Any] = []  # lstStyle-like elements, most specific first
        self.body_chain: list[Any] = []
        own = child(txBody, "lstStyle")
        if own is not None:
            self.lst_chain.append(own)
        self.body_chain.append(child(txBody, "bodyPr"))
        self.font_ref_color: Color | None = None
        self.font_ref_font: str | None = None
        ph = ph_of(sp) if sp is not None else None
        if extra_lst:
            self.lst_chain.extend(e for e in extra_lst if e is not None)
        self.own_count = len(self.lst_chain)
        fr = font_ref if font_ref is not None else (style_refs(sp).get("fontRef") if sp is not None else None)
        if fr is not None:
            self.font_ref_color = cc.resolve(color_child(fr))
            idx = fr.get("idx")
            self.font_ref_font = ctx.theme.major if idx == "major" else ctx.theme.minor if idx == "minor" else None
        self.ph_type = ph_type or (ph[0] if ph else None)
        if ph is not None and sp is not None:
            for base in ctx.ph_chain(sp, owner):
                tb = child(base, "txBody")
                if tb is not None:
                    ls = child(tb, "lstStyle")
                    if ls is not None:
                        self.lst_chain.append(ls)
                    self.body_chain.append(child(tb, "bodyPr"))
            # Title and body placeholders use the master's titleStyle / bodyStyle; dates, footers and numbers otherStyle.
            mts = ctx.master_text_style(self.ph_type)
            if mts is not None:
                self.lst_chain.append(mts)
        # Text boxes, table cells and every other shape fall back to the presentation's defaultTextStyle.
        if ctx.defaults.default_text_style is not None:
            self.lst_chain.append(ctx.defaults.default_text_style)
        self._lvl_cache: dict[int, list[Any]] = {}

    def levels(self, lvl: int) -> list[Any]:
        if lvl not in self._lvl_cache:
            name = f"lvl{lvl + 1}pPr"
            out = []
            for ls in self.lst_chain:
                e = child(ls, name)
                if e is not None:
                    out.append(e)
            self._lvl_cache[lvl] = out
        return self._lvl_cache[lvl]

    def body(self) -> BodyProps:
        bp = BodyProps()

        def first_attr(name: str) -> str | None:
            for b in self.body_chain:
                if b is not None and b.get(name) is not None:
                    return b.get(name)
            return None

        for k, attr in (("l", "lIns"), ("t", "tIns"), ("r", "rIns"), ("b", "bIns")):
            v = first_attr(attr)
            if v is not None:
                setattr(bp, k, int(v) / EMU_PER_PT)
        bp.anchor = first_attr("anchor") or "t"
        bp.anchor_ctr = first_attr("anchorCtr") in ("1", "true")
        bp.wrap = first_attr("wrap") != "none"
        bp.vert = first_attr("vert") or "horz"
        bp.columns = int(first_attr("numCol") or 1)
        bp.rot = int(first_attr("rot") or 0) / 60000
        for b in self.body_chain:
            if b is None:
                continue
            na, sa, no = child(b, "normAutofit"), child(b, "spAutoFit"), child(b, "noAutofit")
            if na is not None:
                bp.autofit = "norm"
                bp.font_scale = int(na.get("fontScale", "100000")) / 100000
                bp.ln_reduction = int(na.get("lnSpcReduction", "0")) / 100000
                break
            if sa is not None:
                bp.autofit = "shape"
                break
            if no is not None:
                break
        return bp

    def _pattr(self, pPr: Any, lvl: int, name: str) -> str | None:
        if pPr is not None and pPr.get(name) is not None:
            return pPr.get(name)
        for e in self.levels(lvl):
            if e.get(name) is not None:
                return e.get(name)
        return None

    def _pchild(self, pPr: Any, lvl: int, names: tuple[str, ...]) -> Any:
        for e in ([pPr] if pPr is not None else []) + self.levels(lvl):
            for c in e:
                if local(c) in names:
                    return c
        return None

    def _rprop(self, rPr: Any, pPr: Any, lvl: int, name: str) -> str | None:
        if rPr is not None and rPr.get(name) is not None:
            return rPr.get(name)
        d = child(pPr, "defRPr") if pPr is not None else None
        if d is not None and d.get(name) is not None:
            return d.get(name)
        for e in self.levels(lvl):
            d = child(e, "defRPr")
            if d is not None and d.get(name) is not None:
                return d.get(name)
        return None

    def _rchild(self, rPr: Any, pPr: Any, lvl: int, names: tuple[str, ...]) -> Any:
        cands = [rPr, child(pPr, "defRPr") if pPr is not None else None] + [child(e, "defRPr") for e in self.levels(lvl)]
        for e in cands:
            if e is None:
                continue
            for c in e:
                if local(c) in names:
                    return c
        return None

    def run_style(self, rPr: Any, pPr: Any, lvl: int, scale: float = 1.0) -> RunStyle:
        cc = self.ctx.cc
        rs = RunStyle()
        sz = self._rprop(rPr, pPr, lvl, "sz")
        rs.size = (int(sz) / 100 if sz else 18.0) * scale
        rs.bold = self._rprop(rPr, pPr, lvl, "b") in ("1", "true")
        rs.italic = self._rprop(rPr, pPr, lvl, "i") in ("1", "true")
        u = self._rprop(rPr, pPr, lvl, "u")
        rs.underline = u not in (None, "none")
        st = self._rprop(rPr, pPr, lvl, "strike")
        rs.strike = st not in (None, "noStrike")
        bl = self._rprop(rPr, pPr, lvl, "baseline")
        rs.baseline = int(bl) / 100000 if bl else 0.0
        rs.caps = self._rprop(rPr, pPr, lvl, "cap") == "all"
        latin = self._rchild(rPr, pPr, lvl, ("latin",))
        font = self.ctx.theme.font(latin.get("typeface")) if latin is not None else None
        if font is None:
            font = self.font_ref_font or self.ctx.theme.minor
        rs.font = font
        col, rs.color_src = self._color(rPr, pPr, lvl)
        rs.color = col or Color.from_hex(cc.scheme_hex("tx1") or "000000")
        hl = child(rPr, "highlight") if rPr is not None else None
        if hl is not None:
            rs.highlight = cc.resolve(color_child(hl))
        link = child(rPr, "hlinkClick") if rPr is not None else None
        if link is not None:
            rs.link = link.get(R + "id") or "#"
            # PowerPoint draws hyperlinks in the theme's hyperlink colour, underlined (unless a14 says "use the text colour").
            if link.find(".//{http://schemas.microsoft.com/office/drawing/2018/hyperlinkcolor}hlinkClr") is None:
                h = cc.scheme_hex("hlink")
                rs.color = Color.from_hex(h) if h else rs.color
                rs.color_src = "link"  # a table style's text colour does not override it
            rs.underline = True
        return rs

    def _color(self, rPr: Any, pPr: Any, lvl: int) -> tuple[Color | None, str]:
        """Run colour and where it came from: the run, the paragraph, the shape's own list style, the shape style's
        fontRef, then inherited styles."""

        def fill_color(e: Any) -> Color | None | bool:
            fe = fill_el(e) if e is not None else None
            if fe is None:
                return False
            f = parse_fill(fe, self.ctx.cc)
            if f is None or f.kind == "none":
                return None
            return f.color if f.kind == "solid" else (f.stops[0][1] if f.stops else None)

        for e in (rPr, child(pPr, "defRPr") if pPr is not None else None):
            c = fill_color(e)
            if c is not False:
                return c, "run"  # type: ignore[return-value]
        name = f"lvl{lvl + 1}pPr"
        for i, ls in enumerate(self.lst_chain):
            if i == self.own_count and self.font_ref_color is not None:
                return self.font_ref_color, "ref"
            e = child(ls, name)
            c = fill_color(child(e, "defRPr")) if e is not None else False
            if c is not False:
                return c, ("list" if i < self.own_count else "inherited")  # type: ignore[return-value]
        return self.font_ref_color, ("ref" if self.font_ref_color is not None else "default")

    def para_style(self, pPr: Any, lvl: int, first_size: float, scale: float, ln_red: float) -> ParaStyle:
        cc = self.ctx.cc
        ps = ParaStyle(level=lvl)
        ps.align = self._pattr(pPr, lvl, "algn") or "l"
        ps.mar_l = int(self._pattr(pPr, lvl, "marL") or 0) / EMU_PER_PT
        ps.indent = int(self._pattr(pPr, lvl, "indent") or 0) / EMU_PER_PT
        ps.rtl = self._pattr(pPr, lvl, "rtl") in ("1", "true")

        def spacing(name: str) -> tuple[float | None, float | None]:
            e = self._pchild(pPr, lvl, (name,))
            if e is None:
                return None, None
            pct, pts = child(e, "spcPct"), child(e, "spcPts")
            if pct is not None:
                return int(pct.get("val", "100000")) / 100000, None
            if pts is not None:
                return None, int(pts.get("val", "0")) / 100
            return None, None

        lp, lpts = spacing("lnSpc")
        if lpts is not None:
            ps.line_pct, ps.line_pts = None, lpts * scale
        else:
            ps.line_pct = max(0.5, (lp if lp is not None else 1.0) - ln_red)
        for attr, name in (("spc_before", "spcBef"), ("spc_after", "spcAft")):
            p_, pts = spacing(name)
            if pts is not None:
                setattr(ps, attr, pts * scale)
            elif p_ is not None:
                setattr(ps, attr, p_ * first_size * 1.2)
        bu = self._pchild(pPr, lvl, ("buNone", "buChar", "buAutoNum", "buBlip"))
        if bu is not None:
            n = local(bu)
            if n == "buChar":
                ps.bullet = bu.get("char", "•")
            elif n == "buAutoNum":
                ps.bullet_auto = bu.get("type", "arabicPeriod")
                ps.bullet_start = int(bu.get("startAt", "1"))
            elif n == "buBlip":
                ps.bullet = "•"
        if ps.bullet or ps.bullet_auto:
            bc = self._pchild(pPr, lvl, ("buClr", "buClrTx"))
            if bc is not None and local(bc) == "buClr":
                ps.bullet_color = cc.resolve(color_child(bc))
            bf = self._pchild(pPr, lvl, ("buFont", "buFontTx"))
            if bf is not None and local(bf) == "buFont":
                ps.bullet_font = self.ctx.theme.font(bf.get("typeface"))
            bs = self._pchild(pPr, lvl, ("buSzPct", "buSzPts", "buSzTx"))
            if bs is not None and local(bs) == "buSzPct":
                ps.bullet_size_pct = int(bs.get("val", "100000")) / 100000
        return ps

    def frame(self, slide_number: int = 0) -> TextFrame:
        body = self.body()
        scale = body.font_scale if body.autofit == "norm" else 1.0
        paras: list[Para] = []
        counters: dict[int, int] = {}
        for p in children(self.txBody, "p"):
            pPr = child(p, "pPr")
            lvl = int(pPr.get("lvl", "0")) if pPr is not None else 0
            runs: list[Run] = []
            for r in p:
                n = local(r)
                if n in ("r", "fld"):
                    rPr = child(r, "rPr")
                    t = child(r, "t")
                    text = t.text if t is not None and t.text else ""
                    if n == "fld" and r.get("type") == "slidenum" and slide_number:
                        text = str(slide_number)
                    runs.append(Run(text, self.run_style(rPr, pPr, lvl, scale)))
                elif n == "br":
                    runs.append(Run("\n", self.run_style(child(r, "rPr"), pPr, lvl, scale)))
                elif n == "AlternateContent" or n == "m":
                    txt = "".join(x for x in r.itertext())
                    if txt.strip():
                        runs.append(Run(txt, self.run_style(None, pPr, lvl, scale)))
            end = child(p, "endParaRPr")
            end_style = self.run_style(end, pPr, lvl, scale)
            first_size = runs[0].style.size if runs else end_style.size
            ps = self.para_style(pPr, lvl, first_size, scale, body.ln_reduction)
            has_text = any(r.text.strip() for r in runs)
            if ps.bullet_auto and has_text:
                for k in list(counters):
                    if k > lvl:
                        del counters[k]
                counters[lvl] = counters.get(lvl, ps.bullet_start - 1) + 1
                ps.bullet = autonum(ps.bullet_auto, counters[lvl])
            elif not has_text:
                ps.bullet = None
            else:
                for k in list(counters):
                    if k >= lvl:
                        del counters[k]
            paras.append(Para(runs, ps, end_style.size))
        return TextFrame(paras, body)


def autonum(scheme: str, n: int) -> str:
    def roman(k: int) -> str:
        vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out = ""
        for v, s in vals:
            while k >= v:
                out += s
                k -= v
        return out

    def alpha(k: int) -> str:
        s = ""
        while k > 0:
            k, rem = divmod(k - 1, 26)
            s = chr(97 + rem) + s
        return s

    s = scheme
    if s.startswith("arabic"):
        core = str(n)
    elif s.startswith("romanUc"):
        core = roman(n).upper()
    elif s.startswith("romanLc"):
        core = roman(n)
    elif s.startswith("alphaUc"):
        core = alpha(n).upper()
    elif s.startswith("alphaLc"):
        core = alpha(n)
    else:
        core = str(n)
    if s.endswith("ParenBoth"):
        return f"({core})"
    if s.endswith("ParenR"):
        return f"{core})"
    if s.endswith("Plain"):
        return core
    if s.endswith("Minus"):
        return f"- {core} -"
    return f"{core}."


def text_resolver(ctx: SlideCtx, sp: Any, owner: str = "slide") -> TextResolver | None:
    tb = child(sp, "txBody")
    if tb is None:
        return None
    return TextResolver(ctx, sp, tb, owner)


# ── pictures ────────────────────────────────────────────────────────────


def picture_blip(pic: Any) -> tuple[str | None, str | None, tuple[float, float, float, float]]:
    """(raster rId, svg rId, crop l,t,r,b fractions) of a p:pic."""
    bf = child(pic, "blipFill")
    blip = child(bf, "blip")
    rid = blip.get(R + "embed") if blip is not None else None
    svg = None
    if blip is not None:
        for e in blip.iter("{%s}svgBlip" % NS["asvg"]):
            svg = e.get(R + "embed")
    sr = child(bf, "srcRect")
    crop = tuple(int(sr.get(k, "0") or 0) / 100000 for k in ("l", "t", "r", "b")) if sr is not None else (0.0, 0.0, 0.0, 0.0)
    return rid, svg, crop  # type: ignore[return-value]


def deep_copy(el: Any) -> Any:
    return copy.deepcopy(el)


def angle_vec(deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    return math.cos(r), math.sin(r)
