"""Built-in slide renderer: python-pptx → Typst (absolute placement) → PNG or PDF, with no LibreOffice.

Draws, per slide: the background (solid, gradient, picture) from the slide, layout or master; the master's and
layout's own shapes (when shown); then every shape in z-order: preset and custom geometry with fill and outline,
lines and connectors with arrowheads, text frames with inherited styles (fonts, sizes, colours, bullets, alignment,
anchoring, autofit), pictures with crop, tables with their table style, charts from their data, SmartArt from its
cached drawing, and groups. Rotation and flips apply. What it cannot draw (EMF/WMF pictures, OLE objects without a
preview) becomes a labelled grey box, and the caller reports it.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _fonts import FontEnv
from _ooxml import (
    EMU_PER_PT,
    Box,
    Color,
    Fill,
    Line,
    SlideCtx,
    TextResolver,
    Transform,
    background,
    child,
    children,
    fill_el,
    geometry,
    iter_tree,
    local,
    parse_fill,
    parse_line,
    part_xml,
    path,
    ph_of,
    picture_blip,
    pres_defaults,
    shape_box,
    shape_fill,
    shape_line,
    show_master_shapes,
)
from _typtext import PREAMBLE, frame_box, pt, tstr

RT_CHART = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
DASHES = {"dash": "dashed", "sysDash": "densely-dashed", "dot": "dotted", "sysDot": "densely-dotted", "dashDot": "dash-dotted", "sysDashDot": "dash-dotted", "lgDash": "loosely-dashed", "lgDashDot": "loosely-dash-dotted", "lgDashDotDot": "loosely-dash-dotted", "sysDashDotDot": "dash-dotted"}


@dataclass
class RenderStats:
    slides: int = 0
    shapes: int = 0
    placeholders_drawn: list[str] = field(default_factory=list)  # things drawn as grey boxes
    by_slide: dict[int, list[str]] = field(default_factory=dict)  # the same, per slide number
    current: int = 0

    def note(self, what: str) -> None:
        if what not in self.placeholders_drawn:
            self.placeholders_drawn.append(what)
        lst = self.by_slide.setdefault(self.current, [])
        if what not in lst:
            lst.append(what)


class Renderer:
    def __init__(self, prs: Any, workdir: Path):
        self.prs = prs
        self.defaults = pres_defaults(prs)
        self.fonts = FontEnv()
        self.root = workdir
        (workdir / "img").mkdir(parents=True, exist_ok=True)
        self.images: dict[int, str | None] = {}
        self.image_px: dict[str, tuple[int, int]] = {}
        self.stats = RenderStats()
        self.W = self.defaults.size[0] / EMU_PER_PT
        self.H = self.defaults.size[1] / EMU_PER_PT

    # ── document ────────────────────────────────────────────────────────

    def document(self, numbers: list[int]) -> str:
        slides = list(self.prs.slides)
        pages = []
        for n in numbers:
            self.stats.current = n
            ctx = SlideCtx(self.prs, slides[n - 1], self.defaults, n)
            pages.append(self.slide_markup(ctx))
            self.stats.slides += 1
        head = PREAMBLE + f"#set page(width: {pt(self.W)}, height: {pt(self.H)})\n"
        return head + "\n#pagebreak()\n".join(pages)

    def slide_markup(self, ctx: SlideCtx) -> str:
        out: list[str] = []
        bg, bg_part = background(ctx)
        out.append(self.background_markup(bg, bg_part))
        if show_master_shapes(ctx.slide_el):
            if show_master_shapes(ctx.layout_el):
                out.extend(self.tree_markup(ctx, path(ctx.master_el, "cSld", "spTree"), "master", ctx.master_part))
            out.extend(self.tree_markup(ctx, path(ctx.layout_el, "cSld", "spTree"), "layout", ctx.layout_part))
        out.extend(self.tree_markup(ctx, path(ctx.slide_el, "cSld", "spTree"), "slide", ctx.slide_part))
        return "\n".join(x for x in out if x)

    def background_markup(self, bg: Fill, part: Any) -> str:
        if bg.kind == "image":
            til = self.image_tiling(bg.part or part, bg, self.W, self.H)
            if til:
                return f"#place(top + left, rect(width: {pt(self.W)}, height: {pt(self.H)}, fill: {til}, stroke: none))"
            return ""
        paint = self.paint(bg)
        if paint and paint != "none":
            return f"#place(top + left, rect(width: {pt(self.W)}, height: {pt(self.H)}, fill: {paint}, stroke: none))"
        return ""

    # ── shapes ──────────────────────────────────────────────────────────

    def tree_markup(self, ctx: SlideCtx, tree: Any, owner: str, part: Any, tf: Transform | None = None) -> list[str]:
        if tree is None:
            return []
        out: list[str] = []
        group_fills: dict[int, Fill | None] = {}
        hidden_groups: set[int] = set()
        for el, etf, _depth in iter_tree(tree, tf, prefer="fallback"):
            anc = _group_ancestor(el)
            if anc is not None and id(anc) in hidden_groups:
                if local(el) == "grpSp":
                    hidden_groups.add(id(el))
                continue
            nv = _cnvpr(el)
            if nv is not None and nv.get("hidden") in ("1", "true"):
                if local(el) == "grpSp":
                    hidden_groups.add(id(el))
                continue
            if owner != "slide" and ph_of(el) is not None:
                continue  # layout/master placeholders are prompts, not content
            try:
                kind = local(el)
                if kind == "grpSp":
                    fe = fill_el(child(el, "grpSpPr"))
                    group_fills[id(el)] = parse_fill(fe, ctx.cc, part=part) if fe is not None else (group_fills.get(id(anc)) if anc is not None else None)
                    continue
                gfill = group_fills.get(id(anc)) if anc is not None else None
                m = self.shape_markup(ctx, el, etf, owner, part, gfill)
                if m:
                    out.append(m)
                    self.stats.shapes += 1
            except Exception as e:  # noqa: BLE001 — one odd shape must not stop the slide
                if os.environ.get("DESK_DEBUG"):
                    raise
                self.stats.note(f"a shape that could not be drawn ({type(e).__name__})")
        return out

    def shape_markup(self, ctx: SlideCtx, el: Any, tf: Transform, owner: str, part: Any, gfill: Fill | None) -> str:
        kind = local(el)
        box = shape_box(ctx, el, tf, owner)
        if box is None:
            return ""
        x, y, w, h = box.x / EMU_PER_PT, box.y / EMU_PER_PT, box.w / EMU_PER_PT, box.h / EMU_PER_PT
        if kind in ("sp", "cxnSp"):
            return self.sp_markup(ctx, el, box, x, y, w, h, owner, part, gfill)
        if kind == "pic":
            return self.pic_markup(ctx, el, box, x, y, w, h, owner, part)
        if kind == "graphicFrame":
            return self.frame_markup(ctx, el, box, x, y, w, h, part)
        if kind == "contentPart":
            self.stats.note("ink")
        return ""

    def _place(self, x: float, y: float, w: float, h: float, body: str, rot: float = 0.0, flip_h: bool = False, flip_v: bool = False) -> str:
        if flip_h or flip_v:
            body = f"scale(x: {-100 if flip_h else 100}%, y: {-100 if flip_v else 100}%, origin: center + horizon, reflow: false, {body})"
        if rot:
            body = f"rotate({rot:.2f}deg, origin: center + horizon, reflow: false, {body})"
        return f"#place(top + left, dx: {pt(x)}, dy: {pt(y)}, {body})"

    def sp_markup(self, ctx: SlideCtx, el: Any, box: Box, x: float, y: float, w: float, h: float, owner: str, part: Any, gfill: Fill | None) -> str:
        prst, adj, cust = geometry(el, ctx, owner)
        fill = shape_fill(ctx, el, owner, gfill, part)
        line = shape_line(ctx, el, owner)
        is_line = prst in LINE_PRESETS or local(el) == "cxnSp" and prst not in CLOSED_PRESETS
        out = []
        if is_line:
            if line is not None:
                out.append(self._place(x, y, w, h, self.line_geom(prst, adj, w, h, line, box.flip_h, box.flip_v), box.rot))
        else:
            g = self.geom(prst, adj, cust, w, h, fill, line, part)
            if g:
                out.append(self._place(x, y, w, h, g, box.rot, box.flip_h, box.flip_v))
        tb = child(el, "txBody")
        if tb is not None and _has_text(tb):
            res = TextResolver(ctx, el, tb, owner)
            frame = res.frame(ctx.number)
            tx, ty, tw, th = x, y, w, h
            txx = child(el, "txXfrm")  # SmartArt drawings place text separately
            if txx is not None and self._dsp_origin is not None:
                tbx = _read_box(txx)
                if tbx is not None:
                    ox, oy = self._dsp_origin
                    tx, ty, tw, th = (ox + tbx.x) / EMU_PER_PT, (oy + tbx.y) / EMU_PER_PT, tbx.w / EMU_PER_PT, tbx.h / EMU_PER_PT
            tl, tt, tr_, tb_ = text_rect(prst, tw, th)
            text = frame_box(frame, tr_ - tl, tb_ - tt, self.fonts)
            rot = box.rot + frame.body.rot
            if box.flip_v and not frame.body.vert.startswith("vert"):
                rot += 180
            out.append(self._place(tx + tl, ty + tt, tr_ - tl, tb_ - tt, text, rot % 360 if rot else 0.0))
        return "\n".join(out)

    _dsp_origin: tuple[float, float] | None = None

    def pic_markup(self, ctx: SlideCtx, el: Any, box: Box, x: float, y: float, w: float, h: float, owner: str, part: Any) -> str:
        rid, svg, crop = picture_blip(el)
        src = self.image_path(part, svg) if svg else None
        if src is None:
            src = self.image_path(part, rid)
        prst, adj, _ = geometry(el, ctx, owner)
        line = shape_line(ctx, el, owner)
        if src is None:
            what = self._image_kind(part, rid)
            self.stats.note(what)
            body = f"rect(width: {pt(w)}, height: {pt(h)}, fill: luma(225), stroke: 0.5pt + luma(160))[#align(center + horizon, text(size: {pt(min(12, max(6, h / 6)))}, fill: luma(90), {tstr(what)}))]"
            return self._place(x, y, w, h, body, box.rot, box.flip_h, box.flip_v)
        l, t, r, b = crop
        fw = w / max(0.01, 1 - l - r)
        fh = h / max(0.01, 1 - t - b)
        if prst not in ("rect", "roundRect", "ellipse"):
            img = f"image({tstr(src)}, width: {pt(fw)}, height: {pt(fh)}, fit: \"stretch\")"
            paint = f"tiling(size: ({pt(max(w, 1))}, {pt(max(h, 1))}), box(width: {pt(max(w, 1))}, height: {pt(max(h, 1))}, clip: true, place(top + left, dx: {pt(-l * fw)}, dy: {pt(-t * fh)}, {img})))"
            return self._place(x, y, w, h, preset_geom(prst, adj, w, h, paint, self.stroke(line)), box.rot, box.flip_h, box.flip_v)
        radius = ""
        if prst == "roundRect":
            radius = f", radius: {pt(min(w, h) * adj.get('adj', 16667) / 100000)}"
        elif prst == "ellipse":
            radius = f", radius: {pt(min(w, h) / 2)}"
        stroke = f", stroke: {self.stroke(line)}" if line is not None else ""
        img = f"image({tstr(src)}, width: {pt(fw)}, height: {pt(fh)}, fit: \"stretch\")"
        body = f"box(width: {pt(w)}, height: {pt(h)}, clip: true{radius}{stroke})[#place(top + left, dx: {pt(-l * fw)}, dy: {pt(-t * fh)}, {img})]"
        return self._place(x, y, w, h, body, box.rot, box.flip_h, box.flip_v)

    def frame_markup(self, ctx: SlideCtx, el: Any, box: Box, x: float, y: float, w: float, h: float, part: Any) -> str:
        gd = path(el, "graphic", "graphicData")
        uri = gd.get("uri", "") if gd is not None else ""
        if uri.endswith("/table"):
            tbl = child(gd, "tbl")
            return self.table_markup(ctx, tbl, x, y, w, h) if tbl is not None else ""
        if uri.endswith("/chart"):
            c = child(gd, "chart")
            rid = c.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if c is not None else None
            try:
                root = part_xml(part.related_part(rid))
            except Exception:  # noqa: BLE001
                self.stats.note("a chart whose data is missing")
                return self._grey(x, y, w, h, "chart")
            from _chartdraw import draw_chart

            fill = None
            sp = root.find("{http://schemas.openxmlformats.org/drawingml/2006/chart}spPr")
            if sp is not None:
                fill = parse_fill(fill_el(sp), ctx.cc)
            bg = f"#place(top + left, rect(width: {pt(w)}, height: {pt(h)}, fill: {self.paint(fill)}, stroke: none))" if fill and fill.kind != "none" else ""
            inner = draw_chart(root, ctx.cc, w, h, self.fonts)
            return self._place(x, y, w, h, f"box(width: {pt(w)}, height: {pt(h)})[{bg}\n{inner}]", box.rot)
        if uri.endswith("/diagram"):
            return self.smartart_markup(ctx, el, gd, box, part)
        # OLE objects and others: a preview picture if there is one
        pic = el.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}pic")
        if pic is not None:
            return self.pic_markup(ctx, pic, box, x, y, w, h, "slide", part)
        self.stats.note("an embedded object")
        return self._grey(x, y, w, h, "embedded object")

    def _grey(self, x: float, y: float, w: float, h: float, label: str) -> str:
        return self._place(x, y, w, h, f"rect(width: {pt(w)}, height: {pt(h)}, fill: luma(232), stroke: 0.5pt + luma(170))[#align(center + horizon, text(size: 10pt, fill: luma(100), {tstr(label)}))]")

    def smartart_markup(self, ctx: SlideCtx, el: Any, gd: Any, box: Box, part: Any) -> str:
        rel_ids = child(gd, "relIds")
        try:
            dm_rid = rel_ids.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}dm")
            data = part_xml(part.related_part(dm_rid))
            ext = data.find(".//{http://schemas.microsoft.com/office/drawing/2008/diagram}dataModelExt")
            drawing_part = part.related_part(ext.get("relId"))
            droot = part_xml(drawing_part)
        except Exception:  # noqa: BLE001
            self.stats.note("SmartArt without a cached drawing")
            return self._grey(box.x / EMU_PER_PT, box.y / EMU_PER_PT, box.w / EMU_PER_PT, box.h / EMU_PER_PT, "SmartArt")
        tree = child(droot, "spTree")
        prev = self._dsp_origin
        self._dsp_origin = (box.x, box.y)
        try:
            items = self.tree_markup(ctx, tree, "slide", drawing_part, Transform(box.x, box.y, 1.0, 1.0))
        finally:
            self._dsp_origin = prev
        return "\n".join(items)

    # ── tables ──────────────────────────────────────────────────────────

    def table_markup(self, ctx: SlideCtx, tbl: Any, x: float, y: float, w: float, h: float) -> str:
        from _tablestyle import table_style

        cols = [int(g.get("w", "0")) / EMU_PER_PT for g in children(child(tbl, "tblGrid"), "gridCol")]
        rows = children(tbl, "tr")
        if not cols or not rows:
            return ""
        tblPr = child(tbl, "tblPr")
        style = table_style(ctx, tblPr, self.defaults)
        flags = {k: (tblPr.get(k) in ("1", "true")) if tblPr is not None else False for k in ("firstRow", "lastRow", "firstCol", "lastCol", "bandRow", "bandCol")}
        # measure row heights: at least the stated height, grown to fit the text
        cells: list[tuple[int, int, int, int, Any]] = []
        heights = [int(r.get("h", "0")) / EMU_PER_PT for r in rows]
        from _typtext import Measurer

        meas = Measurer(self.fonts)
        resolved: dict[tuple[int, int], Any] = {}
        for ri, tr in enumerate(rows):
            ci = 0
            for tc in children(tr, "tc"):
                if ci >= len(cols):
                    break
                gs = int(tc.get("gridSpan", "1"))
                rs = int(tc.get("rowSpan", "1"))
                if tc.get("hMerge") in ("1", "true") or tc.get("vMerge") in ("1", "true"):
                    ci += 1
                    continue
                cells.append((ri, ci, rs, gs, tc))
                part_names = _cell_parts(ri, ci, len(rows), len(cols), flags)
                txt = style.text_props(part_names)
                tb = child(tc, "txBody")
                if tb is not None:
                    frame = TextResolver(ctx, None, tb, "slide", table_cell=True).frame(ctx.number)
                    for p in frame.paras:
                        for r in p.runs:
                            _apply_cell_text(r, txt)
                    tcPr = child(tc, "tcPr")
                    ins = [int(tcPr.get(k)) / EMU_PER_PT if tcPr is not None and tcPr.get(k) else d for k, d in (("marL", 7.2), ("marR", 7.2), ("marT", 3.6), ("marB", 3.6))]
                    frame.body.l, frame.body.r, frame.body.t, frame.body.b = ins
                    frame.body.anchor = tcPr.get("anchor", "t") if tcPr is not None else "t"
                    resolved[(ri, ci)] = frame
                    if rs == 1:
                        cw = sum(cols[ci:ci + gs])
                        meas.add_frame(f"{ri},{ci}", frame, cw - ins[0] - ins[1])
                ci += gs
        sizes = meas.run() if meas.items else {}
        for (ri, ci), frame in resolved.items():
            m = sizes.get(f"{ri},{ci}")
            if m:
                heights[ri] = max(heights[ri], m[1] + frame.body.t + frame.body.b)
        ys = [0.0]
        for hh in heights:
            ys.append(ys[-1] + hh)
        xs = [0.0]
        for cw in cols:
            xs.append(xs[-1] + cw)
        out = []
        for ri, ci, rs, gs, tc in cells:
            cx, cy = xs[ci], ys[ri]
            cw = xs[min(len(cols), ci + gs)] - cx
            ch = ys[min(len(rows), ri + rs)] - cy
            part_names = _cell_parts(ri, ci, len(rows), len(cols), flags)
            tcPr = child(tc, "tcPr")
            fe = fill_el(tcPr)
            fill = parse_fill(fe, ctx.cc) if fe is not None else style.fill(part_names)
            paint = self.paint(fill) if fill else "none"
            out.append(f"#place(top + left, dx: {pt(x + cx)}, dy: {pt(y + cy)}, rect(width: {pt(cw)}, height: {pt(ch)}, fill: {paint}, stroke: none))")
            for side, (x1, y1, x2, y2) in (("lnL", (cx, cy, cx, cy + ch)), ("lnR", (cx + cw, cy, cx + cw, cy + ch)), ("lnT", (cx, cy, cx + cw, cy)), ("lnB", (cx, cy + ch, cx + cw, cy + ch))):
                ln_el = child(tcPr, side) if tcPr is not None else None
                ln = parse_line(ln_el, ctx.cc) if ln_el is not None else style.border(part_names, side, ri, ci, len(rows), len(cols), rs, gs)
                if ln is not None and ln.color is not None:
                    out.append(f"#place(top + left, line(start: ({pt(x + x1)}, {pt(y + y1)}), end: ({pt(x + x2)}, {pt(y + y2)}), stroke: {self.stroke(ln)}))")
            frame = resolved.get((ri, ci))
            if frame is not None and frame.text.strip():
                out.append(f"#place(top + left, dx: {pt(x + cx)}, dy: {pt(y + cy)}, {frame_box(frame, cw, ch, self.fonts)})")
        return "\n".join(out)

    # ── geometry ────────────────────────────────────────────────────────

    def paint(self, fill: Fill | None) -> str:
        if fill is None or fill.kind == "none":
            return "none"
        if fill.kind in ("solid", "pattern") and fill.color is not None:
            return fill.color.typst()
        if fill.kind == "gradient" and fill.stops:
            st = list(fill.stops)
            if st[0][0] > 0:
                st.insert(0, (0.0, st[0][1]))
            if st[-1][0] < 1:
                st.append((1.0, st[-1][1]))
            stops = ", ".join(f"({c.typst()}, {min(100.0, max(0.0, p * 100)):.1f}%)" for p, c in st)
            if fill.radial:
                fx, fy = fill.focus
                rad = math.hypot(max(fx, 1 - fx), max(fy, 1 - fy)) * 100
                return f"gradient.radial({stops}, center: ({fx * 100:.1f}%, {fy * 100:.1f}%), radius: {rad:.1f}%)"
            return f"gradient.linear({stops}, angle: {fill.angle:.1f}deg)"
        if fill.kind == "image":
            return "luma(200)"
        return "none"

    def stroke(self, line: Line | None) -> str:
        if line is None or line.color is None:
            return "none"
        paint = line.color.typst()
        if line.fill is not None and line.fill.kind == "gradient":
            paint = self.paint(line.fill)
        dash = DASHES.get(line.dash)
        d = f", dash: \"{dash}\"" if dash else ""
        return f"(paint: {paint}, thickness: {pt(line.width_pt)}{d})"

    def geom(self, prst: str, adj: dict[str, int], cust: Any, w: float, h: float, fill: Fill | None, line: Line | None, part: Any) -> str:
        paint = self.paint(fill)
        stroke = self.stroke(line)
        if paint == "none" and stroke == "none":
            return ""
        if fill is not None and fill.kind == "image":
            tiling = self.image_tiling(fill.part or part, fill, w, h)
            paint = tiling or "luma(200)"
        if cust is not None:
            return self.custom_geom(cust, w, h, paint, stroke)
        return preset_geom(prst, adj, w, h, paint, stroke)

    def image_tiling(self, part: Any, fill: Fill, w: float, h: float) -> str | None:
        """A Typst tiling paint showing an image fill (stretched with its crop, or tiled), for any geometry."""
        src = self.image_path(part, fill.rid)
        if not src:
            return None
        if fill.tile:
            px = self.image_px.get(src, (96, 96))
            tw = max(2.0, px[0] * 0.75 * fill.tile_scale[0])
            th = max(2.0, px[1] * 0.75 * fill.tile_scale[1])
            return f"tiling(size: ({pt(tw)}, {pt(th)}), image({tstr(src)}, width: {pt(tw)}, height: {pt(th)}, fit: \"stretch\"))"
        l, t, r, b = fill.src_rect
        fw = w / max(0.01, 1 - l - r)
        fh = h / max(0.01, 1 - t - b)
        img = f"image({tstr(src)}, width: {pt(fw)}, height: {pt(fh)}, fit: \"stretch\")"
        return f"tiling(size: ({pt(max(w, 1))}, {pt(max(h, 1))}), box(width: {pt(max(w, 1))}, height: {pt(max(h, 1))}, clip: true, place(top + left, dx: {pt(-l * fw)}, dy: {pt(-t * fh)}, {img})))"

    def line_geom(self, prst: str, adj: dict[str, int], w: float, h: float, line: Line, flip_h: bool, flip_v: bool) -> str:
        x1, y1, x2, y2 = 0.0, 0.0, w, h
        if flip_h:
            x1, x2 = x2, x1
        if flip_v:
            y1, y2 = y2, y1
        stroke = self.stroke(line)
        pts = [(x1, y1), (x2, y2)]
        if prst.startswith("bentConnector") and prst != "bentConnector2":
            a = adj.get("adj1", 50000) / 100000
            mx = x1 + (x2 - x1) * a
            pts = [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
        elif prst == "bentConnector2":
            pts = [(x1, y1), (x2, y1), (x2, y2)]
        segs = [f"curve.move(({pt(pts[0][0])}, {pt(pts[0][1])}))"] + [f"curve.line(({pt(px)}, {pt(py)}))" for px, py in pts[1:]]
        items = [f"#place(top + left, curve(stroke: {stroke}, {', '.join(segs)}))"]
        size = max(4.0, line.width_pt * 3)
        if line.tail and line.tail != "none":
            items.append(_arrowhead(pts[-2], pts[-1], size, line.color, line.tail))
        if line.head and line.head != "none":
            items.append(_arrowhead(pts[1], pts[0], size, line.color, line.head))
        return f"box(width: {pt(max(w, 0.1))}, height: {pt(max(h, 0.1))})[{''.join(items)}]"

    def custom_geom(self, cust: Any, w: float, h: float, paint: str, stroke: str) -> str:
        out = []
        for pth in children(child(cust, "pathLst"), "path"):
            pw = float(pth.get("w", "0") or 0) or None
            ph = float(pth.get("h", "0") or 0) or None
            sx = w / pw if pw else 1.0 / EMU_PER_PT
            sy = h / ph if ph else 1.0 / EMU_PER_PT
            fill_none = pth.get("fill") == "none"
            no_stroke = pth.get("stroke") in ("0", "false")
            segs = []
            cur = (0.0, 0.0)
            start = (0.0, 0.0)
            for cmd in pth:
                n = local(cmd)
                pts = [(float(p.get("x", 0)) * sx, float(p.get("y", 0)) * sy) for p in children(cmd, "pt")]
                if n == "moveTo" and pts:
                    segs.append(f"curve.move(({pt(pts[0][0])}, {pt(pts[0][1])}))")
                    cur = start = pts[0]
                elif n == "lnTo" and pts:
                    segs.append(f"curve.line(({pt(pts[0][0])}, {pt(pts[0][1])}))")
                    cur = pts[0]
                elif n == "cubicBezTo" and len(pts) >= 3:
                    segs.append(f"curve.cubic(({pt(pts[0][0])}, {pt(pts[0][1])}), ({pt(pts[1][0])}, {pt(pts[1][1])}), ({pt(pts[2][0])}, {pt(pts[2][1])}))")
                    cur = pts[2]
                elif n == "quadBezTo" and len(pts) >= 2:
                    segs.append(f"curve.quad(({pt(pts[0][0])}, {pt(pts[0][1])}), ({pt(pts[1][0])}, {pt(pts[1][1])}))")
                    cur = pts[1]
                elif n == "arcTo":
                    wr = float(cmd.get("wR", 0)) * sx
                    hr = float(cmd.get("hR", 0)) * sy
                    st = float(cmd.get("stAng", 0)) / 60000
                    sw = float(cmd.get("swAng", 0)) / 60000
                    for c1, c2, e in _arc_beziers(cur, wr, hr, st, sw):
                        segs.append(f"curve.cubic(({pt(c1[0])}, {pt(c1[1])}), ({pt(c2[0])}, {pt(c2[1])}), ({pt(e[0])}, {pt(e[1])}))")
                        cur = e
                elif n == "close":
                    segs.append("curve.close()")
                    cur = start
            if not segs:
                continue
            f = "none" if fill_none else paint
            s = "none" if no_stroke else stroke
            out.append(f"#place(top + left, curve(fill: {f}, stroke: {s}, {', '.join(segs)}))")
        return f"box(width: {pt(max(w, 0.1))}, height: {pt(max(h, 0.1))})[{''.join(out)}]"

    # ── images ──────────────────────────────────────────────────────────

    def _image_kind(self, part: Any, rid: str | None) -> str:
        try:
            ct = part.rels[rid].target_part.content_type if rid else ""
        except Exception:  # noqa: BLE001
            ct = ""
        if "emf" in ct or "wmf" in ct:
            return "EMF/WMF picture"
        if rid is None:
            return "picture without image data"
        return "picture"

    def image_path(self, part: Any, rid: str | None) -> str | None:
        """Writes an image part into the Typst root (converted to PNG when needed); returns its root-relative path."""
        if not rid or part is None:
            return None
        try:
            rel = part.rels[rid]
        except (KeyError, AttributeError):
            return None
        if rel.is_external:
            return None
        ip = rel.target_part
        key = id(ip)
        if key in self.images:
            return self.images[key]
        blob = ip.blob
        ct = (getattr(ip, "content_type", "") or "").lower()
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif", "image/svg+xml": "svg"}.get(ct)
        if ext is None:
            sniff = blob[:12]
            if sniff.startswith(b"\x89PNG"):
                ext = "png"
            elif sniff.startswith(b"\xff\xd8"):
                ext = "jpg"
            elif sniff.startswith(b"GIF8"):
                ext = "gif"
            elif b"<svg" in blob[:512]:
                ext = "svg"
        if ext is None:
            try:
                from PIL import Image

                with Image.open(io.BytesIO(blob)) as im:
                    im.load()
                    if im.format in ("WMF", "EMF"):
                        raise ValueError("vector metafile")
                    im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA", "L", "LA") else im
                    buf = io.BytesIO()
                    im.save(buf, "PNG")
                    blob, ext = buf.getvalue(), "png"
            except Exception:  # noqa: BLE001 — EMF/WMF and odd formats become grey boxes
                self.images[key] = None
                return None
        if ext == "jpg":
            blob = _jpeg_fix(blob)
        name = f"img/{hashlib.sha1(blob).hexdigest()[:16]}.{ext}"
        (self.root / name).write_bytes(blob)
        self.images[key] = "/" + name
        if ext != "svg":
            try:
                from PIL import Image

                with Image.open(io.BytesIO(blob)) as im:
                    self.image_px["/" + name] = im.size
            except Exception:  # noqa: BLE001
                pass
        return self.images[key]


# ── helpers ─────────────────────────────────────────────────────────────


LINE_PRESETS = {"line", "straightConnector1", "bentConnector2", "bentConnector3", "bentConnector4", "bentConnector5", "curvedConnector2", "curvedConnector3", "curvedConnector4", "curvedConnector5", "lineInv"}
CLOSED_PRESETS = {"rect", "roundRect", "ellipse"}


def _jpeg_fix(blob: bytes) -> bytes:
    """Re-encodes CMYK/odd JPEGs that Typst cannot decode."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as im:
            if im.mode in ("RGB", "L"):
                return blob
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=92)
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return blob


def _group_ancestor(el: Any) -> Any:
    p = el.getparent()
    while p is not None:
        n = local(p)
        if n == "grpSp":
            return p
        if n in ("spTree", "cSld"):
            return None
        p = p.getparent()
    return None


def _cnvpr(el: Any) -> Any:
    for nv in el:
        if local(nv).startswith("nv"):
            return child(nv, "cNvPr")
    return None


def _has_text(tb: Any) -> bool:
    for t in tb.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
        if t.text and t.text.strip():
            return True
    return any(True for _ in tb.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}fld"))


def _read_box(x: Any) -> Box | None:
    from _ooxml import read_xfrm

    return read_xfrm(x)


def text_rect(prst: str, w: float, h: float) -> tuple[float, float, float, float]:
    """The text rectangle of a preset shape (l, t, r, b) inside its box, as PowerPoint defines for common ones."""
    if prst in ("ellipse", "flowChartConnector"):
        k = (1 - math.sqrt(0.5)) / 2
        return w * k, h * k, w * (1 - k), h * (1 - k)
    if prst in ("triangle", "flowChartExtract"):
        return w * 0.25, h * 0.5, w * 0.75, h
    if prst in ("diamond", "flowChartDecision"):
        return w * 0.25, h * 0.25, w * 0.75, h * 0.75
    if prst in ("rightArrow", "leftArrow"):
        return 0.0, h * 0.25, w, h * 0.75
    if prst in ("homePlate", "chevron"):
        return 0.0, 0.0, w * 0.85, h
    return 0.0, 0.0, w, h


def _arrowhead(p0: tuple[float, float], p1: tuple[float, float], size: float, color: Color | None, kind: str) -> str:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    bx, by = p1[0] - ux * size, p1[1] - uy * size
    nx, ny = -uy * size * 0.5, ux * size * 0.5
    col = (color or Color(0, 0, 0)).typst()
    if kind == "oval":
        return f"#place(top + left, dx: {pt(p1[0] - size / 2)}, dy: {pt(p1[1] - size / 2)}, circle(radius: {pt(size / 2)}, fill: {col}))"
    if kind == "arrow":
        return f"#place(top + left, curve(stroke: (paint: {col}, thickness: {pt(max(0.75, size / 5))}), curve.move(({pt(bx + nx)}, {pt(by + ny)})), curve.line(({pt(p1[0])}, {pt(p1[1])})), curve.line(({pt(bx - nx)}, {pt(by - ny)}))))"
    return f"#place(top + left, polygon(fill: {col}, stroke: none, ({pt(p1[0])}, {pt(p1[1])}), ({pt(bx + nx)}, {pt(by + ny)}), ({pt(bx - nx)}, {pt(by - ny)})))"


def _arc_beziers(cur: tuple[float, float], wr: float, hr: float, st_deg: float, sw_deg: float) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """DrawingML arcTo from the current point → cubic Bézier segments."""
    if wr <= 0 or hr <= 0 or sw_deg == 0:
        return []
    st = math.radians(st_deg)
    # centre such that the point at angle st on the ellipse is the current point
    cx = cur[0] - wr * math.cos(st)
    cy = cur[1] - hr * math.sin(st)
    segs = []
    n = max(1, int(math.ceil(abs(sw_deg) / 90)))
    step = math.radians(sw_deg) / n
    a = st
    for _ in range(n):
        b = a + step
        k = 4 / 3 * math.tan((b - a) / 4)
        p0 = (cx + wr * math.cos(a), cy + hr * math.sin(a))
        p3 = (cx + wr * math.cos(b), cy + hr * math.sin(b))
        c1 = (p0[0] - k * wr * math.sin(a), p0[1] + k * hr * math.cos(a))
        c2 = (p3[0] + k * wr * math.sin(b), p3[1] - k * hr * math.cos(b))
        segs.append((c1, c2, p3))
        a = b
    return segs


def _poly(points: list[tuple[float, float]], paint: str, stroke: str) -> str:
    return f"polygon(fill: {paint}, stroke: {stroke}, " + ", ".join(f"({pt(x)}, {pt(y)})" for x, y in points) + ")"


def preset_geom(prst: str, adj: dict[str, int], w: float, h: float, paint: str, stroke: str) -> str:
    """Typst for a preset shape in a w×h box (common presets exactly, others approximated)."""
    ss = min(w, h)

    def a(name: str, default: int) -> float:
        return adj.get(name, default) / 100000

    fs = f"fill: {paint}, stroke: {stroke}"
    if prst in ("ellipse", "flowChartConnector", "donut", "cloud", "heart", "teardrop", "flowChartOnlineStorage", "pie", "chord", "smileyFace"):
        return f"ellipse(width: {pt(w)}, height: {pt(h)}, {fs})"
    if prst in ("roundRect", "flowChartAlternateProcess", "round2SameRect", "round1Rect", "snipRoundRect", "flowChartTerminator"):
        r = ss * a("adj", 16667) if prst != "flowChartTerminator" else ss / 2
        if prst == "round2SameRect":
            r = ss * a("adj1", 16667)
            return f"rect(width: {pt(w)}, height: {pt(h)}, radius: (top-left: {pt(r)}, top-right: {pt(r)}), {fs})"
        if prst == "round1Rect":
            return f"rect(width: {pt(w)}, height: {pt(h)}, radius: (top-right: {pt(r)}), {fs})"
        return f"rect(width: {pt(w)}, height: {pt(h)}, radius: {pt(r)}, {fs})"
    if prst in ("triangle", "flowChartExtract", "flowChartMerge"):
        ax = a("adj", 50000) * w
        pts = [(ax, 0), (w, h), (0, h)] if prst != "flowChartMerge" else [(0, 0), (w, 0), (w / 2, h)]
        return _poly(pts, paint, stroke)
    if prst == "rtTriangle":
        return _poly([(0, 0), (w, h), (0, h)], paint, stroke)
    if prst in ("diamond", "flowChartDecision"):
        return _poly([(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)], paint, stroke)
    if prst in ("parallelogram", "flowChartInputOutput"):
        o = ss * a("adj", 25000) if prst == "parallelogram" else w * 0.2
        return _poly([(o, 0), (w, 0), (w - o, h), (0, h)], paint, stroke)
    if prst in ("trapezoid", "flowChartManualOperation"):
        o = ss * a("adj", 25000) if prst == "trapezoid" else w * 0.2
        return _poly([(o, 0), (w - o, 0), (w, h), (0, h)] if prst == "trapezoid" else [(0, 0), (w, 0), (w - o, h), (o, h)], paint, stroke)
    if prst in ("pentagon", "hexagon", "octagon", "heptagon", "decagon", "dodecagon"):
        if prst == "hexagon":
            o = ss * a("adj", 25000)
            return _poly([(o, 0), (w - o, 0), (w, h / 2), (w - o, h), (o, h), (0, h / 2)], paint, stroke)
        if prst == "octagon":
            o = ss * a("adj", 29289)
            return _poly([(o, 0), (w - o, 0), (w, o), (w, h - o), (w - o, h), (o, h), (0, h - o), (0, o)], paint, stroke)
        n = {"pentagon": 5, "heptagon": 7, "decagon": 10, "dodecagon": 12}[prst]
        pts = [(w / 2 + w / 2 * math.sin(2 * math.pi * i / n), h / 2 - h / 2 * math.cos(2 * math.pi * i / n)) for i in range(n)]
        return _poly(pts, paint, stroke)
    if prst in ("homePlate", "chevron"):
        o = ss * a("adj", 50000)
        if prst == "homePlate":
            return _poly([(0, 0), (w - o, 0), (w, h / 2), (w - o, h), (0, h)], paint, stroke)
        return _poly([(0, 0), (w - o, 0), (w, h / 2), (w - o, h), (0, h), (o, h / 2)], paint, stroke)
    if prst in ("rightArrow", "leftArrow", "upArrow", "downArrow", "leftRightArrow", "notchedRightArrow", "stripedRightArrow"):
        t = a("adj1", 50000)
        hl = ss * a("adj2", 50000)
        if prst in ("rightArrow", "notchedRightArrow", "stripedRightArrow"):
            y0, y1 = h / 2 - h * t / 2, h / 2 + h * t / 2
            return _poly([(0, y0), (w - hl, y0), (w - hl, 0), (w, h / 2), (w - hl, h), (w - hl, y1), (0, y1)], paint, stroke)
        if prst == "leftArrow":
            y0, y1 = h / 2 - h * t / 2, h / 2 + h * t / 2
            return _poly([(w, y0), (hl, y0), (hl, 0), (0, h / 2), (hl, h), (hl, y1), (w, y1)], paint, stroke)
        if prst == "leftRightArrow":
            y0, y1 = h / 2 - h * t / 2, h / 2 + h * t / 2
            return _poly([(0, h / 2), (hl, 0), (hl, y0), (w - hl, y0), (w - hl, 0), (w, h / 2), (w - hl, h), (w - hl, y1), (hl, y1), (hl, h)], paint, stroke)
        x0, x1 = w / 2 - w * t / 2, w / 2 + w * t / 2
        if prst == "upArrow":
            return _poly([(x0, h), (x0, hl), (0, hl), (w / 2, 0), (w, hl), (x1, hl), (x1, h)], paint, stroke)
        return _poly([(x0, 0), (x1, 0), (x1, h - hl), (w, h - hl), (w / 2, h), (0, h - hl), (x0, h - hl)], paint, stroke)
    if prst in ("star5", "star4", "star6", "star8", "star7", "star10", "star12", "star16", "star24", "star32", "irregularSeal1", "irregularSeal2"):
        n = int(prst[4:]) if prst.startswith("star") else 8
        inner = {4: 0.38, 5: 0.38, 6: 0.5, 7: 0.55}.get(n, 0.7)
        pts = []
        for i in range(2 * n):
            r = 1.0 if i % 2 == 0 else inner
            ang = math.pi * i / n - math.pi / 2
            pts.append((w / 2 + w / 2 * r * math.cos(ang), h / 2 + h / 2 * r * math.sin(ang)))
        return _poly(pts, paint, stroke)
    if prst in ("plus", "mathPlus", "flowChartSummingJunction"):
        o = ss * a("adj", 25000)
        return _poly([(o, 0), (w - o, 0), (w - o, o), (w, o), (w, h - o), (w - o, h - o), (w - o, h), (o, h), (o, h - o), (0, h - o), (0, o), (o, o)], paint, stroke)
    if prst in ("snip1Rect", "snip2SameRect", "snip2DiagRect"):
        o = ss * a("adj", 16667) if prst == "snip1Rect" else ss * a("adj1", 16667)
        if prst == "snip1Rect":
            return _poly([(0, 0), (w - o, 0), (w, o), (w, h), (0, h)], paint, stroke)
        return _poly([(o, 0), (w - o, 0), (w, o), (w, h), (0, h), (0, o)], paint, stroke)
    if prst in ("wedgeRectCallout", "wedgeRoundRectCallout", "wedgeEllipseCallout", "cloudCallout"):
        tx = w / 2 + w * a("adj1", -20833)
        ty = h / 2 + h * a("adj2", 62500)
        base = f"rect(width: {pt(w)}, height: {pt(h)}, radius: {pt(ss * 0.1 if 'Round' in prst else 0)}, {fs})" if "Ellipse" not in prst and "cloud" not in prst else f"ellipse(width: {pt(w)}, height: {pt(h)}, {fs})"
        tail = _poly([(w * 0.4, h * 0.9), (tx, ty), (w * 0.55, h * 0.9)], paint, stroke)
        return f"box(width: {pt(w)}, height: {pt(h)})[#place(top + left, {tail})#place(top + left, {base})]"
    if prst in ("can", "flowChartMagneticDisk"):
        e = h * 0.15
        return f"box(width: {pt(w)}, height: {pt(h)})[#place(top + left, dy: {pt(e / 2)}, rect(width: {pt(w)}, height: {pt(h - e)}, {fs}))#place(top + left, dy: {pt(h - e)}, ellipse(width: {pt(w)}, height: {pt(e)}, {fs}))#place(top + left, ellipse(width: {pt(w)}, height: {pt(e)}, {fs}))]"
    if prst in ("frame", "bevel"):
        o = ss * a("adj1" if prst == "frame" else "adj", 12500)
        return f"box(width: {pt(w)}, height: {pt(h)})[#place(top + left, rect(width: {pt(w)}, height: {pt(h)}, {fs}))#place(top + left, dx: {pt(o)}, dy: {pt(o)}, rect(width: {pt(max(0, w - 2 * o))}, height: {pt(max(0, h - 2 * o))}, fill: none, stroke: {stroke}))]"
    if prst == "sun":
        k = a("adj", 25000)
        rays = []
        for i in range(8):
            ang = math.pi * i / 4
            c, s_ = math.cos(ang), math.sin(ang)
            tipx, tipy = w / 2 + w / 2 * c, h / 2 + h / 2 * s_
            bx, by = w / 2 + w * 0.36 * c, h / 2 + h * 0.36 * s_
            nx, ny = -s_ * w * 0.07, c * h * 0.07
            rays.append("#place(top + left, " + _poly([(tipx, tipy), (bx + nx, by + ny), (bx - nx, by - ny)], paint, stroke) + ")")
        d = 1 - 2 * max(0.12, min(0.4, k + 0.05))
        core = f"#place(top + left, dx: {pt(w * (1 - d) / 2)}, dy: {pt(h * (1 - d) / 2)}, ellipse(width: {pt(w * d)}, height: {pt(h * d)}, {fs}))"
        return f"box(width: {pt(w)}, height: {pt(h)})[{''.join(rays)}{core}]"
    if prst == "moon":
        k = a("adj", 50000)
        return _poly([(w, 0)] + [(w - w * math.sin(math.pi * i / 16), h / 2 - h / 2 * math.cos(math.pi * i / 16)) for i in range(1, 16)] + [(w, h)] + [(w - w * k * math.sin(math.pi * i / 16), h / 2 + h / 2 * math.cos(math.pi * i / 16)) for i in range(1, 16)], paint, stroke)
    if prst in ("arc", "blockArc"):
        return f"ellipse(width: {pt(w)}, height: {pt(h)}, fill: none, stroke: {stroke})"
    if prst in ("flowChartDocument", "wave", "doubleWave"):
        return _poly([(0, 0), (w, 0), (w, h * 0.85), (w * 0.75, h * 0.78), (w * 0.5, h * 0.88), (w * 0.25, h), (0, h * 0.9)], paint, stroke)
    return f"rect(width: {pt(w)}, height: {pt(h)}, {fs})"


def _cell_parts(ri: int, ci: int, nr: int, nc: int, flags: dict[str, bool]) -> list[str]:
    """Table style parts that apply to a cell, lowest priority first."""
    parts = ["wholeTbl"]
    data_r = ri - (1 if flags["firstRow"] else 0)
    data_c = ci - (1 if flags["firstCol"] else 0)
    if flags["bandRow"] and not (flags["firstRow"] and ri == 0) and not (flags["lastRow"] and ri == nr - 1):
        parts.append("band1H" if data_r % 2 == 0 else "band2H")
    if flags["bandCol"] and not (flags["firstCol"] and ci == 0) and not (flags["lastCol"] and ci == nc - 1):
        parts.append("band1V" if data_c % 2 == 0 else "band2V")
    if flags["lastCol"] and ci == nc - 1:
        parts.append("lastCol")
    if flags["firstCol"] and ci == 0:
        parts.append("firstCol")
    if flags["lastRow"] and ri == nr - 1:
        parts.append("lastRow")
    if flags["firstRow"] and ri == 0:
        parts.append("firstRow")
    return parts


def _apply_cell_text(run: Any, txt: dict[str, Any]) -> None:
    st = run.style
    if txt.get("color") is not None and st.color_src in ("inherited", "default"):
        st.color = txt["color"]
    if txt.get("bold") is not None:
        st.bold = txt["bold"] or st.bold
    if txt.get("italic"):
        st.italic = True


# ── entry points ────────────────────────────────────────────────────────


def _render_chunk(job: tuple[str, list[int], float]) -> tuple[list[bytes], dict[int, list[str]]]:
    """Worker: opens the deck itself and renders some slides to PNG (for process-parallel rendering)."""
    from _deck import open_deck

    path, numbers, ppi = job
    prs = open_deck(path, allow_convert=False, checked=True).prs
    pages, stats = compile_slides(prs, numbers, "png", ppi)
    return pages, stats.by_slide


def render_pngs(prs: Any, source: str, numbers: list[int], ppi: float) -> tuple[list[bytes], dict[int, list[str]]]:
    """PNG bytes per slide, and what each slide drew as grey boxes; many slides are split across processes (Typst
    rasterises one document at a time)."""
    from _common import pool_map, workers_for

    n = len(numbers)
    workers = workers_for(n) if n >= 12 else 1
    if workers <= 1:
        pages, stats = compile_slides(prs, numbers, "png", ppi)
        return pages, stats.by_slide
    size = -(-n // workers)
    chunks = [numbers[i:i + size] for i in range(0, n, size)]
    try:
        results = pool_map(_render_chunk, [(source, c, ppi) for c in chunks], workers=len(chunks))
    except Exception:  # noqa: BLE001 — no process pool here (sandbox, frozen app): render in this process
        pages, stats = compile_slides(prs, numbers, "png", ppi)
        return pages, stats.by_slide
    pages_all: list[bytes] = []
    notes: dict[int, list[str]] = {}
    for pages, drawn in results:
        pages_all.extend(pages)
        notes.update(drawn)
    return pages_all, notes


def compile_slides(prs: Any, numbers: list[int], fmt: str, ppi: float = 144.0) -> tuple[list[bytes], RenderStats]:
    """Renders slides with Typst: PNG bytes per slide, or one PDF."""
    import typst

    work = Path(tempfile.mkdtemp(prefix="desk-pptx-render-"))
    try:
        r = Renderer(prs, work)
        src = r.document(numbers)
        (work / "slides.typ").write_text(src, encoding="utf-8")
        if os.environ.get("DESK_DEBUG"):
            dbg = Path(tempfile.gettempdir()) / "desk-pptx-last.typ"
            dbg.write_text(src, encoding="utf-8")
        try:
            kw: dict[str, Any] = {"format": fmt, "root": str(work)}
            if fmt == "png":
                kw["ppi"] = ppi
            res = typst.compile(str(work / "slides.typ"), **kw)
        except Exception as e:  # noqa: BLE001
            from _common import SkillError

            raise SkillError(f"the built-in renderer failed: {str(e)[:600]}") from e
        pages = [bytes(res)] if isinstance(res, (bytes, bytearray)) else [bytes(p) for p in res]
        return pages, r.stats
    finally:
        shutil.rmtree(work, ignore_errors=True)
