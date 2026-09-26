#!/usr/bin/env python3
"""Design QA for a deck: overflowing text, off-slide or overlapping shapes, tiny fonts, low contrast, and more."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import add_format, emit, parse_ranges, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_lint.py deck.pptx
  python3 scripts/pptx_lint.py deck.pptx --slides 3-5 --format json
  python3 scripts/pptx_lint.py deck.pptx --fail-on warning      # exit 1 when anything but notes is found

Rules: overflow (text measured against its box), off-slide, overlap, font-size (body text under --min-size, any
text under 9 pt), fonts (more than 3 families), contrast (WCAG: 4.5:1, 3:1 for large text), empty-placeholder,
title (missing or duplicated), words (more than --max-words), image-resolution (upscaled when shown at 1920 px
wide), alt-text, leftover (template or placeholder text), hidden. Each issue names the slide and shape and says how
to fix it. Run it after pptx_create or pptx_edit, together with pptx_render + view_image.
"""

LINT_VERSION = "3"
LEFTOVER = re.compile(r"click to (add|edit)|lorem ipsum|\{\{[^{}]*\}\}|\[insert[^\[\]]*\]|\bTBD\b|\bXX+%?|title here|your (text|title) here|sample text", re.I)
MINOR_PH = ("date", "footer", "slide_number", "header")


def main() -> int:
    ap = parser("Check a .pptx for layout, readability and accessibility problems.", EPILOG)
    ap.add_argument("file")
    ap.add_argument("--slides", help="slide numbers or ranges (default all)")
    ap.add_argument("--min-size", type=float, default=12.0, help="smallest body text size in pt (default 12)")
    ap.add_argument("--max-words", type=int, default=90, help="most words on one slide (default 90)")
    ap.add_argument("--projector-width", type=int, default=1920, help="pixels across the screen, for image resolution (default 1920)")
    ap.add_argument("--fail-on", choices=["error", "warning", "never"], default="never", help="exit 1 when issues of this severity (or worse) are found")
    ap.add_argument("--no-cache", action="store_true", help="do not use or fill the file cache")
    add_format(ap)
    a = ap.parse_args()
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    from _cache import cached_json

    from _deck import ooxml_source, open_deck

    path = ooxml_source(a.file)
    params = {"slides": a.slides, "min_size": a.min_size, "max_words": a.max_words, "projector_width": a.projector_width}
    report = cached_json(path, "pptx-lint", params, LINT_VERSION, lambda: lint(open_deck(path, checked=True).prs, a.slides, a.min_size, a.max_words, a.projector_width))
    report["file"] = str(a.file)
    emit(report, a.format, render_md, max_chars=60000, hint="Lint fewer slides with --slides.")
    c = report["counts"]
    if a.fail_on == "error" and c["error"]:
        return 1
    if a.fail_on == "warning" and (c["error"] or c["warning"]):
        return 1
    return 0


def lint(prs: Any, slides_spec: str | None = None, min_size: float = 12.0, max_words: int = 90, proj_w: int = 1920) -> dict[str, Any]:
    from _deck import image_info, is_hidden, slide_ctxs, walk

    from _ooxml import EMU_PER_PT, TextResolver, child, picture_blip, ph_of
    from _typtext import Measurer

    defaults, ctxs = slide_ctxs(prs)
    slides = list(prs.slides)
    numbers = parse_ranges(slides_spec, len(slides))
    W, H = defaults.size
    issues: list[dict[str, Any]] = []
    meas = Measurer()
    pending: list[tuple[str, int, Any, Any, float, float, Any]] = []
    fonts_used: Counter[str] = Counter()
    font_where: dict[str, set[int]] = {}
    titles: dict[str, list[int]] = {}

    def add(n: int, rec: Any, rule: str, sev: str, msg: str, fix: str) -> None:
        issues.append({"slide": n, "shape": rec.name if rec is not None else None, "shape_id": rec.id if rec is not None else None, "rule": rule, "severity": sev, "message": msg, "fix": fix})

    for n in numbers:
        slide, ctx = slides[n - 1], ctxs[n - 1]
        recs = walk(ctx, slide, prefer="fallback")
        under = _underlay(ctx)
        words = 0
        title_text = None
        has_title_ph = False
        if is_hidden(slide):
            add(n, None, "hidden", "note", "the slide is hidden (it is skipped in the slide show)", "unhide it with pptx_edit hide_slide {\"hidden\": false} if it should be shown")
        for rec in recs:
            el = rec.el
            ph = ph_of(el)
            ph_type = ph[0] if ph else None
            if ph_type in ("title", "ctrTitle"):
                has_title_ph = True
            tb = child(el, "txBody")
            box = rec.box
            if box is not None and rec.kind != "group":
                _geometry_checks(n, rec, box, W, H, add)
            if rec.kind in ("picture",) and box is not None:
                _image_checks(n, rec, slide, box, W, proj_w, add, image_info, picture_blip)
            if rec.kind == "chart":
                _chart_checks(n, rec, slide, ctx, add)
            if tb is None or box is None:
                continue
            text = "".join(t.text or "" for t in tb.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
            if ph is not None and not text.strip() and rec.kind == "placeholder":
                if (ph_type or "") not in ("dt", "ftr", "sldNum", "hdr"):
                    add(n, rec, "empty-placeholder", "warning", f"empty {ph_type or 'content'} placeholder (shows 'Click to add…' while editing, nothing in the show)", "fill it, or delete it with pptx_edit delete_shape")
                continue
            if not text.strip():
                continue
            if ph_type in ("title", "ctrTitle"):
                title_text = " ".join(text.split())
            res = TextResolver(ctx, el, tb)
            frame = res.frame(n)
            minor = ph_type in ("dt", "ftr", "sldNum", "hdr") or rec.name.lower().startswith(("footer", "slide number", "date"))
            if not minor:
                words += frame.words()
            # font sizes and families
            small = [r for p in frame.paras for r in p.runs if r.text.strip() and r.style.size < min_size]
            tiny = [r for r in small if r.style.size < 9]
            nwords = len(text.split())
            if tiny and not minor:
                add(n, rec, "font-size", "warning", f"text at {min(r.style.size for r in tiny):g} pt is too small to read on a screen", f"use at least {min_size:g} pt (pptx_edit set_shape font_size), or cut the text")
            elif small and not minor and nwords > 6:
                add(n, rec, "font-size", "warning", f"body text at {min(r.style.size for r in small):g} pt (under {min_size:g} pt)", f"use at least {min_size:g} pt, or move detail to the speaker notes")
            for p in frame.paras:
                for r in p.runs:
                    if r.text.strip() and r.style.font:
                        fonts_used[r.style.font] += len(r.text)
                        font_where.setdefault(r.style.font, set()).add(n)
            found = []
            for m in LEFTOVER.finditer(text):
                if m.group(0) not in found:
                    found.append(m.group(0))
            if found:
                add(n, rec, "leftover", "warning", "looks like leftover placeholder text: " + ", ".join(f"\"{x}\"" for x in found[:8]), "replace it with real content (pptx_edit replace or set_shape)")
            # contrast
            bg = _background_under(ctx, recs, rec, under)
            if bg is not None:
                worst = None
                for p in frame.paras:
                    for r in p.runs:
                        if not r.text.strip() or r.style.color is None:
                            continue
                        from _ooxml import contrast_ratio

                        fg = r.style.color.over(bg) if r.style.color.a < 1 else r.style.color
                        ratio = contrast_ratio(fg, bg)
                        large = r.style.size >= 18 or (r.style.bold and r.style.size >= 14)
                        need = 3.0 if large else 4.5
                        if ratio < need and (worst is None or ratio < worst[0]):
                            worst = (ratio, need, fg, r.style.size)
                if worst:
                    sev = "error" if worst[0] < 2.0 else "warning"
                    add(n, rec, "contrast", sev, f"text #{worst[2].hex} on #{bg.hex} has contrast {worst[0]:.1f}:1 (needs {worst[1]:g}:1 at {worst[3]:g} pt)", "darken or lighten the text or its background (pptx_edit set_shape color / fill)")
            # overflow: measured later in one batch
            body = frame.body
            iw = box.w / EMU_PER_PT - body.l - body.r
            ih = box.h / EMU_PER_PT - body.t - body.b
            if body.vert not in ("horz", None) and body.vert:
                iw, ih = ih, iw
            key = f"{n}:{rec.z}"
            if body.wrap:
                meas.add_frame(key, frame, iw)
                meas.add_words(key + ":w", frame, top=2)
            else:
                meas.add_frame(key, frame, None)
            pending.append((key, n, rec, frame, iw, ih, box))
        _overlap_checks(n, recs, add)
        if not has_title_ph and not any(ph_of(r.el) and ph_of(r.el)[0] in ("title", "ctrTitle") for r in recs):
            add(n, None, "title", "note", "the slide has no title placeholder (the outline, navigation and screen readers use titles)", "fine for a picture or quote slide; otherwise add one with pptx_edit set_title")
        elif not title_text:
            add(n, None, "title", "warning", "the title is empty", "set it with pptx_edit set_title")
        else:
            titles.setdefault(title_text.lower(), []).append(n)
        if words > max_words:
            add(n, None, "words", "warning", f"{words} words on one slide (more than {max_words})", "cut to the key points, split the slide, or move detail into the speaker notes")
    sizes = meas.run() if pending else {}
    for key, n, rec, frame, iw, ih, box in pending:
        got = sizes.get(key)
        if not got:
            continue
        w, h = got
        body = frame.body
        if body.autofit == "shape":
            bottom = box.y / EMU_PER_PT + h + body.t + body.b
            if bottom > H / EMU_PER_PT + 2:
                add(n, rec, "overflow", "error", f"the box grows with its text and runs {(bottom - H / EMU_PER_PT) / 72:.2f} in past the bottom of the slide", "shorten the text or move the box up")
            continue
        if not body.wrap and w > iw + 2:
            add(n, rec, "overflow", "error", f"text is {w / 72:.2f} in wide but its box has {iw / 72:.2f} in (it does not wrap)", "shorten it, turn on wrapping, or widen the box")
        if body.wrap:
            ratio, word = meas.word_ratio(sizes, key + ":w", iw)
            if ratio > 1.02:
                add(n, rec, "overflow", "error", f"the word \"{word[:40]}\" is {ratio:.1f}× as wide as its box ({iw / 72:.2f} in): PowerPoint breaks it mid-word", "reduce the font size (pptx_edit set_shape font_size), widen the box, or shorten the word")
        if h > ih + 2:
            over = h - ih
            sev = "error" if over > 6 else "warning"
            add(n, rec, "overflow", sev, f"text needs {h / 72:.2f} in of height but the box has {ih / 72:.2f} in", "shorten the text, reduce its size (pptx_edit set_shape font_size), enlarge the box, or split the slide")
    for t, ns in titles.items():
        if len(ns) > 1 and len(ns) <= 6 and not re.search(r"\((cont\.?|continued|\d+/\d+)\)$", t):
            add(ns[1], None, "title", "note", f"the title \"{t[:60]}\" is also used on slide(s) {', '.join(map(str, ns[:1] + ns[2:]))}", "distinct titles help navigation (use '(cont.)' for continuations)")
    from _fonts import generic_of

    families = [f for f in fonts_used if generic_of(f) != "mono"]  # code fonts are expected
    if len(families) > 3:
        top = ", ".join(f"{f} (slides {_ranges(font_where[f])})" for f, _ in fonts_used.most_common(8))
        issues.append({"slide": None, "shape": None, "shape_id": None, "rule": "fonts", "severity": "warning", "message": f"{len(families)} font families (not counting code fonts): {top}", "fix": "use one heading and one body font (theme fonts); change stray runs with pptx_edit set_shape font"})
    sev_order = {"error": 0, "warning": 1, "note": 2}
    issues.sort(key=lambda i: (i["slide"] if i["slide"] is not None else 10**6, sev_order[i["severity"]]))
    counts = Counter(i["severity"] for i in issues)
    return {"slides_checked": len(numbers), "counts": {"error": counts.get("error", 0), "warning": counts.get("warning", 0), "note": counts.get("note", 0)}, "fonts": dict(fonts_used.most_common()), "issues": issues}


def _overlap_checks(n: int, recs: list[Any], add: Any) -> None:
    """Content shapes that partly cover each other, or text drawn over other text."""
    from _ooxml import EMU_PER_IN, child

    def text_of(r: Any) -> str:
        tb = child(r.el, "txBody")
        return "".join(t.text or "" for t in tb.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t")).strip() if tb is not None else ""

    content = []
    for r in recs:
        if r.box is None or r.kind in ("group", "line") or r.box.w <= 0 or r.box.h <= 0:
            continue
        has_text = bool(text_of(r))
        if has_text or r.kind in ("picture", "table", "chart", "smartart", "media"):
            content.append((r, has_text))
    tol = 0.03 * EMU_PER_IN
    reported = 0
    for i in range(len(content)):
        a, ta = content[i]
        for j in range(i + 1, len(content)):
            b, tb_ = content[j]
            if a.parent is not None and a.parent == b.parent:
                continue
            inter = a.box.intersect(b.box)
            small = min(a.box.area(), b.box.area())
            if small <= 0 or inter / small < 0.08:
                continue
            contained = a.box.contains(b.box, tol) or b.box.contains(a.box, tol)
            if ta and tb_:
                msg = f"its text overlaps the text of \"{b.name}\""
            elif contained:
                continue
            else:
                msg = f"it partly covers \"{b.name}\" ({inter / small:.0%} of the smaller one)"
            add(n, a, "overlap", "warning", msg, "move or resize one of them (pptx_edit set_shape x/y/w/h); render and look to confirm")
            reported += 1
            if reported >= 8:
                return


def _chart_checks(n: int, rec: Any, slide: Any, ctx: Any, add: Any) -> None:
    """A chart showing the literal 'Chart Title', or series that share one colour."""
    from _chartdraw import _series_color

    from _ooxml import part_xml

    cref = rec.el.find(".//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart")
    rid = cref.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if cref is not None else None
    if not rid:
        return
    try:
        root = part_xml(slide.part.related_part(rid))
    except Exception:  # noqa: BLE001 — unreadable charts are reported by the renderer
        return
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    chart = root.find(C + "chart")
    if chart is None:
        return
    sers = [s for plot in (chart.find(C + "plotArea") if chart.find(C + "plotArea") is not None else []) if plot.tag.endswith("Chart") for s in plot.findall(C + "ser")]
    title = chart.find(C + "title")
    if title is not None and len(sers) > 1:
        has_text = any((t.text or "").strip() for t in title.iter("{*}t")) or title.find(".//" + C + "strRef") is not None
        if not has_text:
            add(n, rec, "chart-title", "warning", "the chart has an automatic title, which PowerPoint shows as the placeholder 'Chart Title' when there are several series", "set a title or remove it: pptx_edit update_chart with \"title\": \"…\" or \"title\": null")
    colors: dict[str, str] = {}
    for s in sers:
        c = _series_color(s, ctx.cc)
        if c is None:
            continue
        tx = s.find(C + "tx")
        name = (tx.findtext(".//" + C + "v") or "") if tx is not None else ""
        if c.hex in colors:
            add(n, rec, "chart-colors", "warning", f"series \"{colors[c.hex]}\" and \"{name}\" have the same colour (#{c.hex}); they cannot be told apart", "give each series its own colour: pptx_edit update_chart with \"colors\": [\"accent1\", \"accent2\", …]")
            break
        colors[c.hex] = name


def _ranges(ns: set[int]) -> str:
    s = sorted(ns)
    return ", ".join(map(str, s[:6])) + ("…" if len(s) > 6 else "")


def _geometry_checks(n: int, rec: Any, box: Any, W: int, H: int, add: Any) -> None:
    from _ooxml import EMU_PER_IN

    tol = 0.02 * EMU_PER_IN
    left, top, right, bottom = box.x, box.y, box.x + box.w, box.y + box.h
    if box.rot % 180 in (90,):
        cx, cy = box.x + box.w / 2, box.y + box.h / 2
        left, right, top, bottom = cx - box.h / 2, cx + box.h / 2, cy - box.w / 2, cy + box.w / 2
    if right <= 0 or bottom <= 0 or left >= W or top >= H:
        add(n, rec, "off-slide", "warning", "the shape is entirely outside the slide (invisible in the show)", "move it onto the slide or delete it")
        return
    bleed = box.w >= W * 0.9 or box.h >= H * 0.9 or rec.kind in ("picture",) and (box.w >= W * 0.4 or box.h >= H * 0.6)
    if (left < -tol or top < -tol or right > W + tol or bottom > H + tol) and not bleed:
        out = max(-left, -top, right - W, bottom - H) / EMU_PER_IN
        add(n, rec, "off-slide", "warning", f"the shape sticks out of the slide by {out:.2f} in", "move or resize it to sit inside the slide (pptx_edit set_shape x/y/w/h)")


def _image_checks(n: int, rec: Any, slide: Any, box: Any, W: int, proj_w: int, add: Any, image_info: Any, picture_blip: Any) -> None:
    rid, svg, crop = picture_blip(rec.el)
    if svg:
        return
    info = image_info(slide.part, rid)
    px = info.get("pixels")
    nv = rec.el.find(".//{*}cNvPr")
    if nv is not None and not (nv.get("descr") or "").strip() and not (nv.get("title") or "").strip():
        add(n, rec, "alt-text", "note", "the picture has no alt text", "describe it for screen readers (pptx_edit set_shape alt_text)")
    if not px:
        return
    l, t, r, b = crop
    native_w = px[0] * max(0.01, 1 - l - r)
    native_h = px[1] * max(0.01, 1 - t - b)
    shown_w = box.w / W * proj_w
    shown_h = box.h / W * proj_w
    up = max(shown_w / native_w, shown_h / native_h)
    if up > 1.5:
        add(n, rec, "image-resolution", "warning", f"the picture is upscaled {up:.1f}× on a {proj_w} px wide screen ({int(native_w)}×{int(native_h)} px shown at {int(shown_w)}×{int(shown_h)} px); it will look soft", "use a larger image, or show it smaller")


def _underlay(ctx: Any) -> list[tuple[Any, Any]]:
    """Filled master/layout shapes drawn under the slide's own shapes: [(box, fill colour)]."""
    from _ooxml import iter_tree, path, ph_of, shape_box, shape_fill, show_master_shapes

    out = []
    if not show_master_shapes(ctx.slide_el):
        return out
    trees = []
    if show_master_shapes(ctx.layout_el):
        trees.append((path(ctx.master_el, "cSld", "spTree"), "master"))
    trees.append((path(ctx.layout_el, "cSld", "spTree"), "layout"))
    for tree, owner in trees:
        if tree is None:
            continue
        for el, tf, _ in iter_tree(tree):
            if ph_of(el) is not None or not el.tag.endswith("}sp"):
                continue
            box = shape_box(ctx, el, tf, owner)
            f = shape_fill(ctx, el, owner)
            if box is not None and f is not None and f.kind in ("solid", "gradient", "pattern"):
                c = f.average()
                if c is not None:
                    out.append((box, c))
    return out


def _background_under(ctx: Any, recs: list[Any], rec: Any, under: list[tuple[Any, Any]]) -> Any:
    """The colour behind a shape's text: its own fill, else the nearest filled shape below that covers it."""
    from _ooxml import Box, Color, background, shape_fill

    own = shape_fill(ctx, rec.el) if rec.el.tag.endswith("}sp") else None
    base, _ = background(ctx)
    bgc = base.average() if base.kind != "image" else None
    stack_bg = bgc
    b = rec.box
    cx, cy = b.x + b.w / 2, b.y + b.h / 2
    probe = Box(cx - b.w * 0.25, cy - b.h * 0.25, b.w * 0.5, b.h * 0.5)
    for ubox, col in under:
        if ubox.contains(probe):
            stack_bg = col.over(stack_bg) if stack_bg is not None and col.a < 1 else col
    for other in recs:
        if other.z >= rec.z:
            break
        if other.box is None or other.kind in ("group",):
            continue
        if other.box.contains(probe):
            if other.kind == "picture":
                stack_bg = None
                continue
            f = shape_fill(ctx, other.el) if other.el.tag.endswith("}sp") else None
            if f is not None and f.kind != "none":
                c = f.average()
                if c is not None:
                    stack_bg = c.over(stack_bg) if stack_bg is not None and c.a < 1 else Color(c.r, c.g, c.b)
    if own is not None and own.kind in ("solid", "gradient", "pattern"):
        c = own.average()
        if c is not None:
            return c.over(stack_bg) if stack_bg is not None and c.a < 1 else Color(c.r, c.g, c.b)
    if own is not None and own.kind == "image":
        return None
    return stack_bg


def render_md(r: dict[str, Any]) -> str:
    c = r["counts"]
    L = [f"# Lint: {Path(r.get('file', 'deck')).name}: {c['error']} errors, {c['warning']} warnings, {c['note']} notes ({r['slides_checked']} slides)"]
    if not r["issues"]:
        L.append("No problems found. Still render it and look (pptx_render.py + view_image).")
        return "\n".join(L)
    cur = object()
    for i in r["issues"]:
        if i["slide"] != cur:
            cur = i["slide"]
            L.append("")
            L.append(f"## Slide {cur}" if cur is not None else "## Whole deck")
        shape = f" \"{i['shape']}\"" if i["shape"] else ""
        L.append(f"- {i['severity']} {i['rule']}{shape}: {i['message']}. Fix: {i['fix']}.")
    return "\n".join(L)


if __name__ == "__main__":
    run_main(main)
