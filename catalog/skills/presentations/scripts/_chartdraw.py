"""Draws a DrawingML chart (c:chartSpace) with Typst primitives, from its cached data.

Covers bar/column (clustered, stacked, 100%), line, area, pie, doughnut, scatter and radar (drawn as lines), with
series colours from the chart or the theme accents, a title, a legend, value gridlines, category labels and data
labels. It is an approximation meant for looking at a slide, not a chart engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from _ooxml import Color, ColorCtx, child, fill_el, local, parse_fill
from _typtext import pt, tstr

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


@dataclass
class Series:
    name: str
    values: list[float | None]
    x: list[float | None] | None = None
    color: Color | None = None
    point_colors: dict[int, Color] = field(default_factory=dict)
    line: bool = True
    marker: bool = True


@dataclass
class Plot:
    kind: str  # bar | line | area | pie | doughnut | scatter | radar
    horizontal: bool = False
    grouping: str = "clustered"
    series: list[Series] = field(default_factory=list)
    gap: float = 1.5
    overlap: float = 0.0
    label_size: float | None = None
    label_color: Color | None = None
    label_bold: bool = False
    hole: float = 0.5
    labels: bool = False
    percent_labels: bool = False
    vary: bool = False


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _cache(el: Any, numeric: bool) -> list[Any]:
    if el is None:
        return []
    cache = el.find(".//" + C + "numCache")
    if cache is None:
        cache = el.find(".//" + C + "strCache")
    if cache is None:
        cache = el.find(".//" + C + "lvl")
    if cache is None:
        lit = el.find(".//" + C + "numLit")
        cache = lit if lit is not None else el.find(".//" + C + "strLit")
    if cache is None:
        return []
    pc = cache.find(C + "ptCount")
    n = int(pc.get("val", "0")) if pc is not None else 0
    out: list[Any] = [None] * n
    for p in cache.findall(C + "pt"):
        i = int(p.get("idx", "0"))
        v = p.findtext(C + "v")
        if i >= len(out):
            out.extend([None] * (i + 1 - len(out)))
        out[i] = _num(v) if numeric else (v or "")
    return out


def _series_color(ser: Any, cc: ColorCtx) -> Color | None:
    spPr = child(ser, "spPr")
    fe = fill_el(spPr)
    if fe is not None and local(fe) != "noFill":
        f = parse_fill(fe, cc)
        if f is not None:
            return f.color if f.kind == "solid" else (f.stops[0][1] if f.stops else None)
    ln = child(spPr, "ln") if spPr is not None else None
    if ln is not None:
        f = parse_fill(fill_el(ln), cc)
        if f is not None and f.kind == "solid":
            return f.color
    return None


def parse_chart(root: Any, cc: ColorCtx) -> dict[str, Any]:
    chart = root.find(C + "chart")
    out: dict[str, Any] = {"plots": [], "title": None, "legend": None, "categories": [], "gridlines": False, "text_color": None, "font_size": 10.0}
    if chart is None:
        return out
    txpr = root.find(C + "txPr")
    if txpr is not None:
        d = txpr.find(".//{*}defRPr")
        if d is not None and d.get("sz"):
            out["font_size"] = int(d.get("sz")) / 100
        if d is not None:
            fe = fill_el(d)
            f = parse_fill(fe, cc) if fe is not None else None
            if f is not None and f.kind == "solid":
                out["text_color"] = f.color
    pa = chart.find(C + "plotArea")
    n_series = 0
    for plot in (pa if pa is not None else []):
        name = local(plot)
        if not name.endswith("Chart"):
            continue
        k = name[:-5].replace("3D", "")
        kind = {"bar": "bar", "line": "line", "area": "area", "pie": "pie", "ofPie": "pie", "doughnut": "doughnut", "scatter": "scatter", "radar": "radar", "bubble": "scatter", "stock": "line", "surface": "area"}.get(k, "bar")
        pl = Plot(kind)
        bd = plot.find(C + "barDir")
        pl.horizontal = bd is not None and bd.get("val") == "bar"
        g = plot.find(C + "grouping")
        pl.grouping = g.get("val", "clustered") if g is not None else "clustered"
        gw = plot.find(C + "gapWidth")
        pl.gap = int(gw.get("val", "150")) / 100 if gw is not None else 1.5
        ov = plot.find(C + "overlap")
        pl.overlap = max(-1.0, min(1.0, int(ov.get("val", "0")) / 100)) if ov is not None else 0.0
        hs = plot.find(C + "holeSize")
        pl.hole = int(hs.get("val", "50")) / 100 if hs is not None else 0.5
        vc = plot.find(C + "varyColors")
        pl.vary = vc is not None and vc.get("val") in ("1", "true", None)
        dl = plot.find(C + "dLbls")
        if dl is None:
            dl = plot.find(C + "ser/" + C + "dLbls")
        if dl is not None:
            tp = dl.find(C + "txPr")
            dr = tp.find(".//{*}defRPr") if tp is not None else None
            if dr is not None:
                pl.label_size = int(dr.get("sz")) / 100 if dr.get("sz") else None
                pl.label_bold = dr.get("b") in ("1", "true")
                fe = fill_el(dr)
                lf = parse_fill(fe, cc) if fe is not None else None
                pl.label_color = lf.color if lf is not None and lf.kind == "solid" else None
            sv, sp = dl.find(C + "showVal"), dl.find(C + "showPercent")
            pl.labels = sv is not None and sv.get("val") in ("1", "true")
            pl.percent_labels = sp is not None and sp.get("val") in ("1", "true")
        scatter_style = plot.find(C + "scatterStyle")
        for ser in plot.findall(C + "ser"):
            tx = ser.find(C + "tx")
            sname = ""
            if tx is not None:
                v = tx.find(C + "v")
                sname = v.text if v is not None and v.text else " ".join(str(x) for x in _cache(tx, False) if x)
            cat = ser.find(C + "cat")
            xv = ser.find(C + "xVal")
            vals = _cache(ser.find(C + "val") if ser.find(C + "val") is not None else ser.find(C + "yVal"), True)
            s = Series(sname or f"Series {n_series + 1}", vals)
            if xv is not None:
                s.x = _cache(xv, True)
                if not any(v is not None for v in s.x):
                    s.x = list(range(1, len(vals) + 1))  # text x values: PowerPoint plots them as 1..n
            cats = _cache(cat if cat is not None else xv, False)
            if cats and not out["categories"]:
                out["categories"] = [str(c) if c is not None else "" for c in cats]
            s.color = _series_color(ser, cc)
            for dpt in ser.findall(C + "dPt"):
                idx = dpt.find(C + "idx")
                col = _series_color(dpt, cc)
                if idx is not None and col is not None:
                    s.point_colors[int(idx.get("val", "0"))] = col
            mk = ser.find(C + "marker/" + C + "symbol")
            s.marker = not (mk is not None and mk.get("val") == "none")
            if kind == "scatter" and scatter_style is not None and scatter_style.get("val") in ("marker", "none"):
                ln = ser.find(C + "spPr/{*}ln")
                s.line = ln is not None and child(ln, "noFill") is None
            sln = ser.find(C + "spPr/{*}ln")
            if kind in ("line", "scatter") and sln is not None and child(sln, "noFill") is not None:
                s.line = False
            dls = ser.find(C + "dLbls")
            if dls is not None:
                sv = dls.find(C + "showVal")
                if sv is not None and sv.get("val") in ("1", "true"):
                    pl.labels = True
                sp = dls.find(C + "showPercent")
                if sp is not None and sp.get("val") in ("1", "true"):
                    pl.percent_labels = True
            pl.series.append(s)
            n_series += 1
        out["plots"].append(pl)
    out["cat_reversed"] = False
    for ax in (pa if pa is not None else []):
        if local(ax) in ("catAx", "dateAx"):
            o = ax.find(C + "scaling/" + C + "orientation")
            out["cat_reversed"] = o is not None and o.get("val") == "maxMin"
        if local(ax) == "valAx" and ax.find(C + "majorGridlines") is not None:
            delete = ax.find(C + "delete")
            if not (delete is not None and delete.get("val") in ("1", "true")):
                out["gridlines"] = True
    title = chart.find(C + "title")
    atd = chart.find(C + "autoTitleDeleted")
    if title is not None:
        t = " ".join(x.text or "" for x in title.iter("{*}t")).strip()
        if not t:
            t = " ".join(str(x) for x in _cache(title, False) if x).strip()
        if not t and n_series == 1:
            t = out["plots"][0].series[0].name if out["plots"] and out["plots"][0].series else ""
        out["title"] = t or "Chart Title"
    elif n_series == 1 and not (atd is not None and atd.get("val") in ("1", "true")) and atd is not None:
        out["title"] = out["plots"][0].series[0].name if out["plots"] and out["plots"][0].series else None
    lg = chart.find(C + "legend")
    if lg is not None:
        pos = lg.find(C + "legendPos")
        out["legend"] = pos.get("val", "r") if pos is not None else "r"
        out["legend_size"] = _text_size(lg)
    # tick label sizes of each axis (scatter charts have two value axes: the one at the bottom is x)
    for ax in (pa if pa is not None else []):
        nm = local(ax)
        if nm not in ("catAx", "dateAx", "valAx"):
            continue
        pos = ax.find(C + "axPos")
        is_x = nm != "valAx" or (pos is not None and pos.get("val") in ("b", "t") and any(p.kind == "scatter" for p in out["plots"]))
        out["cat_size" if is_x else "val_size"] = _text_size(ax)
    return out


def _text_size(el: Any) -> float | None:
    """The font size an element's own c:txPr sets, in points."""
    tp = el.find(C + "txPr")
    d = tp.find(".//{*}defRPr") if tp is not None else None
    return int(d.get("sz")) / 100 if d is not None and d.get("sz") else None


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw * 0.999)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5 and len(ticks) < 50:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def _fmt(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v / 1e6:g}M"
    if abs(v) >= 1e4:
        return f"{v / 1e3:g}K"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.3g}"


def draw_chart(root: Any, cc: ColorCtx, w: float, h: float, fonts: Any) -> str:
    """Typst markup drawing the chart into a w×h pt box (origin top-left)."""
    d = parse_chart(root, cc)
    accents = [Color.from_hex(cc.scheme_hex(f"accent{i}") or "4472C4") for i in range(1, 7)]
    text_col = d["text_color"] or Color.from_hex(cc.scheme_hex("tx1") or "000000")
    grid_col = Color(text_col.r, text_col.g, text_col.b, 0.18)
    fam = fonts.resolve(fonts_minor(cc))[0]
    fs = max(6.0, min(d["font_size"], h / 12))
    vfs, cfs = (max(6.0, min(d[k], h / 12)) if d.get(k) else fs for k in ("val_size", "cat_size"))
    items: list[str] = []

    def label(x: float, y: float, s: str, size: float = fs, anchor: str = "center", weight: str = "regular", width: float | None = None, color: Color | None = None) -> None:
        wbox = width if width is not None else 200
        ax = {"center": "center", "left": "left", "right": "right"}[anchor]
        dx = x - (wbox / 2 if anchor == "center" else wbox if anchor == "right" else 0)
        items.append(f"#place(top + left, dx: {pt(dx)}, dy: {pt(y)}, box(width: {pt(wbox)}, align({ax}, text(font: {fam}, size: {pt(size)}, weight: \"{weight}\", fill: {(color or text_col).typst()}, {tstr(s)}))))")

    def color_for(i: int) -> Color:
        base = accents[i % 6]
        if i >= 6:  # darker variants after the six accents, as Office does
            f = 0.6 if (i // 6) % 2 else 0.8
            return Color(round(base.r * f), round(base.g * f), round(base.b * f))
        return base

    top = 6.0
    if d["title"]:
        tsize = fs * 1.4
        label(w / 2, top, d["title"], tsize, width=w)
        top += tsize * 1.5
    plots: list[Plot] = d["plots"]
    if not plots or not any(p.series for p in plots):
        items.append(f"#place(top + left, dx: 0pt, dy: {pt(top)}, box(width: {pt(w)}, align(center, text(size: {pt(fs)}, fill: {text_col.typst()}, \"(chart)\"))))")
        return "\n".join(items)
    cats: list[str] = d["categories"]
    first = plots[0]
    radial = first.kind in ("pie", "doughnut")
    # legend entries
    entries: list[tuple[str, Color]] = []
    line_keys: set[int] = set()
    if radial or (first.vary and len(first.series) == 1 and first.kind not in ("line", "radar", "scatter")):
        s0 = first.series[0]
        for i, _ in enumerate(s0.values):
            entries.append((cats[i] if i < len(cats) else str(i + 1), s0.point_colors.get(i) or color_for(i)))
    else:
        k = 0
        for p in plots:
            for s in p.series:
                if p.kind in ("line", "radar") or (p.kind == "scatter" and s.line):
                    line_keys.add(len(entries))
                entries.append((s.name, s.color or color_for(k)))
                k += 1

    def key(x: float, y: float, size: float, col: Color, i: int) -> str:
        if i in line_keys:
            return f"#place(top + left, line(start: ({pt(x - size * 0.2)}, {pt(y + size * 0.6)}), end: ({pt(x + size)}, {pt(y + size * 0.6)}), stroke: 2pt + {col.typst()}))"
        return f"#place(top + left, dx: {pt(x)}, dy: {pt(y + size * 0.2)}, rect(width: {pt(size * 0.8)}, height: {pt(size * 0.8)}, fill: {col.typst()}))"
    left, right, bottom = 6.0, w - 6.0, h - 6.0
    legend = d["legend"]
    if legend and entries:
        lfs = max(6.0, min(d["legend_size"], h / 12)) if d.get("legend_size") else fs * 0.95
        if legend in ("r", "tr", "l"):
            if first.kind == "bar" and first.horizontal and not d.get("cat_reversed") and not radial and len(entries) > 1:
                # horizontal bars stack the first series at the bottom: a vertical legend lists them bottom-up too
                n_e = len(entries)
                entries = entries[::-1]
                line_keys = {n_e - 1 - i for i in line_keys}
            lw = min(w * 0.3, max(len(e[0]) for e in entries) * lfs * 0.55 + lfs * 2)
            ly = max(top, (h - len(entries) * lfs * 1.5) / 2)
            lx = right - lw if legend != "l" else left
            for i, (name, col) in enumerate(entries):
                y = ly + i * lfs * 1.5
                items.append(key(lx, y, lfs, col, i))
                label(lx + lfs * 1.2, y, name, lfs, anchor="left", width=lw - lfs * 1.2)
            if legend == "l":
                left += lw + 6
            else:
                right -= lw + 6
        else:
            widths = [len(e[0]) * lfs * 0.55 + lfs * 2 for e in entries]
            total = sum(widths)
            x = max(left, (w - total) / 2)
            y = bottom - lfs * 1.3 if legend == "b" else top
            for i, ((name, col), wd) in enumerate(zip(entries, widths)):
                items.append(key(x, y, lfs, col, i))
                label(x + lfs * 1.1, y, name, lfs, anchor="left", width=wd)
                x += wd
            if legend == "b":
                bottom -= lfs * 1.8
            else:
                top += lfs * 1.8
    if radial:
        _draw_pie(items, first, cats, left, top, right, bottom, color_for, label, fs, text_col)
        return "\n".join(items)
    if first.kind == "radar":
        _draw_radar(items, plots, cats, left, top, right, bottom, color_for, label, fs, grid_col, text_col)
        return "\n".join(items)
    # value range over all non-radial plots
    vals: list[float] = []
    for p in plots:
        if p.kind == "bar" and p.grouping in ("stacked", "percentStacked") or p.kind == "area" and p.grouping in ("stacked", "percentStacked"):
            n = max(len(s.values) for s in p.series)
            if p.grouping == "percentStacked":
                vals += [0, 1]
            else:
                pos = [sum(max(0.0, s.values[i] or 0) for s in p.series if i < len(s.values)) for i in range(n)]
                neg = [sum(min(0.0, s.values[i] or 0) for s in p.series if i < len(s.values)) for i in range(n)]
                vals += pos + neg
        else:
            for s in p.series:
                vals += [v for v in s.values if v is not None]
    if not vals:
        vals = [0, 1]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    if not any(p.grouping == "percentStacked" for p in plots):
        # like PowerPoint's automatic bounds: keep ~5% headroom beyond the data before the last tick
        span = hi - lo
        hi, lo = (hi + span / 20 if hi > 0 else hi), (lo - span / 20 if lo < 0 else lo)
    horizontal = first.kind == "bar" and first.horizontal
    # about ten intervals, as PowerPoint picks them, fewer when the labels would crowd a short axis
    room = w * 0.75 / (vfs * (0.55 * len(_fmt(float(math.ceil(hi)))) + 0.6)) if horizontal else h * 0.75 / (vfs * 1.25)
    ticks = _nice_ticks(lo, hi, max(3, min(10, int(room))))
    vmin, vmax = ticks[0], ticks[-1]
    pct_axis = any(p.grouping == "percentStacked" for p in plots)
    scatter = first.kind == "scatter"
    tick_lbl = [(f"{t * 100:.0f}%" if pct_axis else _fmt(t)) for t in ticks]
    lab_w = max(len(s) for s in tick_lbl) * vfs * 0.6 + 6
    cat_lbl_h = (vfs if horizontal else cfs) * 1.6
    if horizontal:
        cat_w = min(w * 0.3, max((len(c) for c in cats), default=4) * cfs * 0.55 + 8)
        px0, px1, py0, py1 = left + cat_w, right - 8, top + 4, bottom - cat_lbl_h
    else:
        px0, px1, py0, py1 = left + lab_w, right - 8, top + 4, bottom - cat_lbl_h
    pw, ph = max(10.0, px1 - px0), max(10.0, py1 - py0)

    def vpos(v: float) -> float:
        f = (v - vmin) / (vmax - vmin) if vmax != vmin else 0
        return (px0 + f * pw) if horizontal else (py1 - f * ph)

    # gridlines and value labels
    for t, s in zip(ticks, tick_lbl):
        p_ = vpos(t)
        if horizontal:
            if d["gridlines"]:
                items.append(f"#place(top + left, line(start: ({pt(p_)}, {pt(py0)}), end: ({pt(p_)}, {pt(py1)}), stroke: 0.5pt + {grid_col.typst()}))")
            label(p_, py1 + 3, s, vfs, width=lab_w * 2)
        else:
            if d["gridlines"]:
                items.append(f"#place(top + left, line(start: ({pt(px0)}, {pt(p_)}), end: ({pt(px1)}, {pt(p_)}), stroke: 0.5pt + {grid_col.typst()}))")
            label(px0 - 4, p_ - vfs * 0.6, s, vfs, anchor="right", width=lab_w)
    axis_col = Color(text_col.r, text_col.g, text_col.b, 0.45)
    zero = vpos(0.0)
    if horizontal:
        items.append(f"#place(top + left, line(start: ({pt(zero)}, {pt(py0)}), end: ({pt(zero)}, {pt(py1)}), stroke: 0.75pt + {axis_col.typst()}))")
    else:
        items.append(f"#place(top + left, line(start: ({pt(px0)}, {pt(zero)}), end: ({pt(px1)}, {pt(zero)}), stroke: 0.75pt + {axis_col.typst()}))")
    if scatter:
        xs = [x for p in plots for s in p.series for x in (s.x or []) if x is not None] or [0, 1]
        xt = _nice_ticks(min(xs), max(xs))
        x0v, x1v = xt[0], xt[-1]

        def xpos(v: float) -> float:
            return px0 + (v - x0v) / (x1v - x0v) * pw if x1v != x0v else px0

        for t in xt:
            label(xpos(t), py1 + 3, _fmt(t), cfs, width=60)
        k = 0
        for p in plots:
            for s in p.series:
                col = s.color or color_for(k)
                pts = [(xpos(x), vpos(y)) for x, y in zip(s.x or [], s.values) if x is not None and y is not None]
                if s.line and len(pts) > 1:
                    items.append(_polyline(pts, col, 2.0))
                for x, y in pts:
                    items.append(f"#place(top + left, dx: {pt(x - 3)}, dy: {pt(y - 3)}, circle(radius: 3pt, fill: {col.typst()}))")
                k += 1
        return "\n".join(items)
    n = max((len(s.values) for p in plots for s in p.series), default=0) or 1
    slot = (ph if horizontal else pw) / n
    rev = d.get("cat_reversed", False)

    def row(i: int) -> int:
        # Bar charts list the first category at the bottom unless the axis is reversed.
        return i if rev else n - 1 - i

    for i in range(n):
        c = cats[i] if i < len(cats) else str(i + 1)
        if horizontal:
            label(px0 - 4, py0 + slot * (row(i) + 0.5) - cfs * 0.6, c, cfs, anchor="right", width=px0 - left - 4)
        else:
            label(px0 + slot * (i + 0.5), py1 + 3, c, cfs, width=max(slot, 30))
    k = 0
    for p in plots:
        if p.kind == "bar":
            ns = len(p.series)
            stacked = p.grouping in ("stacked", "percentStacked")
            # gap width and overlap are fractions of one bar's width (PowerPoint's model)
            ov = 1.0 if stacked else p.overlap
            nb = 1 if stacked else max(1, ns)
            bw = slot / (nb - (nb - 1) * ov + p.gap)
            group = bw * (nb - (nb - 1) * ov)
            step = bw * (1 - ov)
            base_pos = [0.0] * n
            base_neg = [0.0] * n
            totals = [sum(abs(s.values[i] or 0) for s in p.series if i < len(s.values)) or 1 for i in range(n)]
            for si, s in enumerate(p.series):
                col = s.color or color_for(k + si)
                for i, v in enumerate(s.values):
                    if v is None:
                        continue
                    if p.grouping == "percentStacked":
                        v = v / totals[i]
                    if stacked:
                        b0 = base_pos[i] if v >= 0 else base_neg[i]
                        b1 = b0 + v
                        if v >= 0:
                            base_pos[i] = b1
                        else:
                            base_neg[i] = b1
                    else:
                        b0, b1 = 0.0, v
                    off = (slot - group) / 2 + (0 if stacked else si * step)
                    a, b = sorted((vpos(b0), vpos(b1)))
                    pc = s.point_colors.get(i) or (color_for(i) if p.vary and ns == 1 else col)
                    if horizontal:
                        off_h = (slot - group) / 2 + (0 if stacked else (ns - 1 - si) * step)
                        yy = py0 + slot * row(i) + off_h
                        items.append(f"#place(top + left, dx: {pt(a)}, dy: {pt(yy)}, rect(width: {pt(max(0.5, b - a))}, height: {pt(bw)}, fill: {pc.typst()}))")
                        if p.labels:
                            label(b + 4, yy + bw / 2 - fs * 0.6, _fmt(s.values[i] or 0), fs * 0.9, anchor="left", width=60)
                    else:
                        items.append(f"#place(top + left, dx: {pt(px0 + slot * i + off)}, dy: {pt(a)}, rect(width: {pt(bw)}, height: {pt(max(0.5, b - a))}, fill: {pc.typst()}))")
                        if p.labels:
                            label(px0 + slot * i + off + bw / 2, a - fs * 1.3, _fmt(s.values[i] or 0), fs * 0.9, width=max(bw, 40))
            k += ns
        elif p.kind in ("line", "radar"):
            for s in p.series:
                col = s.color or color_for(k)
                pts = [(px0 + slot * (i + 0.5), vpos(v)) for i, v in enumerate(s.values) if v is not None]
                if len(pts) > 1 and s.line:
                    items.append(_polyline(pts, col, 2.25))
                if s.marker:
                    for x, y in pts:
                        items.append(f"#place(top + left, dx: {pt(x - 2.5)}, dy: {pt(y - 2.5)}, circle(radius: 2.5pt, fill: {col.typst()}))")
                if p.labels:
                    for (x, y), v in zip(pts, [v for v in s.values if v is not None]):
                        label(x, y - fs * 1.4, _fmt(v), fs * 0.9, width=50)
                k += 1
        elif p.kind == "area":
            stacked = p.grouping in ("stacked", "percentStacked")
            base = [0.0] * n
            totals = [sum(abs(s.values[i] or 0) for s in p.series if i < len(s.values)) or 1 for i in range(n)]
            for s in p.series:
                col = s.color or color_for(k)
                top_pts, bot_pts = [], []
                for i in range(n):
                    v = (s.values[i] if i < len(s.values) else 0) or 0
                    if p.grouping == "percentStacked":
                        v = v / totals[i]
                    b0 = base[i] if stacked else 0.0
                    b1 = b0 + v
                    if stacked:
                        base[i] = b1
                    x = px0 + (pw * i / (n - 1) if n > 1 else pw / 2)
                    top_pts.append((x, vpos(b1)))
                    bot_pts.append((x, vpos(b0)))
                poly = top_pts + bot_pts[::-1]
                fillc = Color(col.r, col.g, col.b, 0.85 if stacked else 0.6)
                items.append("#place(top + left, polygon(fill: " + fillc.typst() + ", stroke: none, " + ", ".join(f"({pt(x)}, {pt(y)})" for x, y in poly) + "))")
                k += 1
    return "\n".join(items)


def _polyline(pts: list[tuple[float, float]], col: Color, width: float) -> str:
    segs = [f"curve.move(({pt(pts[0][0])}, {pt(pts[0][1])}))"] + [f"curve.line(({pt(x)}, {pt(y)}))" for x, y in pts[1:]]
    return f"#place(top + left, curve(stroke: (paint: {col.typst()}, thickness: {pt(width)}, join: \"round\", cap: \"round\"), {', '.join(segs)}))"


def _arc_points(cx: float, cy: float, r: float, a0: float, a1: float) -> list[tuple[float, float]]:
    steps = max(2, int(abs(a1 - a0) / (math.pi / 36)) + 1)
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / steps), cy + r * math.sin(a0 + (a1 - a0) * i / steps)) for i in range(steps + 1)]


def _draw_pie(items: list[str], p: Plot, cats: list[str], left: float, top: float, right: float, bottom: float, color_for: Any, label: Any, fs: float, text_col: Color) -> None:
    s = p.series[0]
    vals = [max(0.0, v or 0.0) for v in s.values]
    total = sum(vals) or 1.0
    cx, cy = (left + right) / 2, (top + bottom) / 2
    r = max(5.0, min(right - left, bottom - top) / 2 - fs * 0.6)
    lsize = min(p.label_size, fs * 1.6) if p.label_size else fs
    a = -math.pi / 2
    for i, v in enumerate(vals):
        if v <= 0:
            continue
        sweep = v / total * 2 * math.pi
        col = s.point_colors.get(i) or color_for(i)
        outer = _arc_points(cx, cy, r, a, a + sweep)
        if p.kind == "doughnut":
            inner = _arc_points(cx, cy, r * p.hole, a + sweep, a)
            poly = outer + inner
        else:
            poly = [(cx, cy)] + outer
        items.append("#place(top + left, polygon(fill: " + col.typst() + ", stroke: 1pt + white, " + ", ".join(f"({pt(x)}, {pt(y)})" for x, y in poly) + "))")
        if p.labels or p.percent_labels:
            mid = a + sweep / 2
            rr = r * (0.75 if p.kind == "doughnut" else 0.62)
            txt = f"{v / total * 100:.0f}%" if p.percent_labels else _fmt(v)
            weight = "bold" if p.label_bold else "regular"
            label(cx + rr * math.cos(mid), cy + rr * math.sin(mid) - lsize * 0.6, txt, lsize, width=60, weight=weight, color=p.label_color or text_col)
        a += sweep


def _draw_radar(items: list[str], plots: list[Plot], cats: list[str], left: float, top: float, right: float, bottom: float, color_for: Any, label: Any, fs: float, grid_col: Color, text_col: Color) -> None:
    series = [s for p in plots for s in p.series]
    n = max((len(s.values) for s in series), default=0)
    if n < 3:
        return
    vals = [v for s in series for v in s.values if v is not None] or [0, 1]
    cx, cy = (left + right) / 2, (top + bottom) / 2 + fs * 0.3
    r = max(5.0, min(right - left, bottom - top) / 2 - fs * 1.6)
    ticks = _nice_ticks(min(0.0, min(vals)), max(vals), max(2, min(5, int(r / (fs * 1.8)))))
    lo, hi = ticks[0], ticks[-1]

    def at(i: int, frac: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + r * frac * math.cos(ang), cy + r * frac * math.sin(ang)

    for t in ticks[1:]:
        ring = [at(i, (t - lo) / (hi - lo)) for i in range(n)]
        items.append("#place(top + left, polygon(fill: none, stroke: 0.5pt + " + grid_col.typst() + ", " + ", ".join(f"({pt(x)}, {pt(y)})" for x, y in ring) + "))")
    for i in range(n):
        x, y = at(i, 1.0)
        items.append(f"#place(top + left, line(start: ({pt(cx)}, {pt(cy)}), end: ({pt(x)}, {pt(y)}), stroke: 0.5pt + {grid_col.typst()}))")
        lx, ly = at(i, 1.12)
        label(lx, ly - fs * 0.6, cats[i] if i < len(cats) else str(i + 1), fs, width=90)
    for t in ticks:
        x, y = at(0, (t - lo) / (hi - lo))
        label(x + 3, y - fs * 0.6, _fmt(t), fs * 0.85, anchor="left", width=40, color=text_col)
    for k, s in enumerate(series):
        col = s.color or color_for(k)
        pts = [at(i, ((v or 0) - lo) / (hi - lo)) for i, v in enumerate(s.values)]
        items.append("#place(top + left, polygon(fill: none, stroke: 2pt + " + col.typst() + ", " + ", ".join(f"({pt(x)}, {pt(y)})" for x, y in pts) + "))")


def fonts_minor(cc: ColorCtx) -> str:
    return cc.theme.minor or "Arial"
