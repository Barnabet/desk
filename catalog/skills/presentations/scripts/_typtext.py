"""Typst markup for PowerPoint text, shared by the built-in renderer and the text measurer.

The same function lays out text for drawing and for measuring, so pptx_create's auto-fit, pptx_lint's overflow
check and pptx_render's pictures agree. Line pitch follows PowerPoint: a font's single spacing times the paragraph's
line-spacing percentage; the space before the first paragraph is ignored, as PowerPoint does.
"""

from __future__ import annotations

import json
from typing import Any

from _fonts import FontEnv, bullet_char
from _ooxml import Color, Para, TextFrame

PREAMBLE = """#set page(margin: 0pt)
#set par(spacing: 0pt, justify: false, linebreaks: "simple")
#set block(spacing: 0pt)
#set text(hyphenate: false, kerning: true, ligatures: false, overhang: false)
#let bw(avail, whole, broken) = context { if measure(whole).width > avail { broken } else { whole } }
"""


def tstr(s: str) -> str:
    """A Typst string literal."""
    return json.dumps(s, ensure_ascii=False)


def pt(v: float) -> str:
    return f"{v:.2f}pt"


def _run_markup(text: str, st: Any, fonts: FontEnv, default_color: Color | None, avail: float | None = None) -> str:
    """One run as Typst markup. With `avail` (the line width in pt), a word wider than the line may break anywhere,
    as PowerPoint breaks it (Typst would let it run past the box)."""
    if not text:
        return ""
    fam, tracking, used = fonts.resolve(st.font)
    size = max(1.0, st.size)
    args = [f"font: {fam}", f"size: {pt(size)}"]
    wt = fonts.weight(st.font)
    if wt is not None:
        args.append(f"weight: {max(wt, 700) if st.bold else wt}")
    elif st.bold:
        args.append('weight: "bold"')
    if st.italic:
        args.append('style: "italic"')
    col = st.color or default_color
    if col is not None:
        args.append(f"fill: {col.typst()}")
    if tracking:
        args.append(f"tracking: {tracking}em")
    body = text.replace("\t", "    ").replace("\r", "")
    if st.caps:
        body = body.upper()

    def wrap(lit: str) -> str:
        inner = f"text({', '.join(args)}, {lit})"
        if st.baseline:
            inner = f"text(baseline: {-st.baseline * 0.8:.2f}em, size: 0.66em, {inner})" if abs(st.baseline) > 0.001 else inner
        if st.underline:
            inner = f"underline(offset: 0.12em, {inner})"
        if st.strike:
            inner = f"strike({inner})"
        if st.highlight is not None:
            inner = f"highlight(fill: {st.highlight.typst()}, {inner})"
        return inner

    if avail and avail > 0:
        import re

        parts = re.findall(r"\S+|\s+", body)
        # a rough upper bound of a word's width (0.62 em per character): only such words can be too wide
        if any(not p.isspace() and len(p) * size * 0.62 > avail for p in parts):
            out = []
            plain = ""
            for p in parts:
                if not p.isspace() and len(p) * size * 0.62 > avail:
                    if plain:
                        out.append("#" + wrap(tstr(plain)))
                        plain = ""
                    broken = "\u200b".join(p)
                    out.append(f"#bw({pt(avail)}, {wrap(tstr(p))}, {wrap(tstr(broken))})")
                else:
                    plain += p
            if plain:
                out.append("#" + wrap(tstr(plain)))
            return "".join(out)
    return "#" + wrap(tstr(body))


def para_markup(p: Para, fonts: FontEnv, first: bool, wrap: bool = True, default_color: Color | None = None, avail: float | None = None) -> str:
    """One paragraph as a Typst block: spacing, indents, bullet, runs, line pitch. `avail`: the frame's line width,
    for breaking words wider than it (drawing only)."""
    ps = p.style
    runs = [r for r in p.runs if r.text]
    size = runs[0].style.size if runs else p.end_size
    font = runs[0].style.font if runs else None
    lf = fonts.line_factor(font)
    if ps.line_pts is not None:
        pitch = ps.line_pts
        lead = f"{pt(max(-size, ps.line_pts - size * lf))}"
    else:
        pct = ps.line_pct or 1.0
        pitch = size * lf * pct
        lead = f"{(pct - 1) * lf:.3f}em"
    top = 0.8 * lf
    bottom = 0.2 * lf
    out = []
    if ps.spc_before and not first:
        out.append(f"#v({pt(ps.spc_before)})")
    line_w = (avail - max(0.0, ps.mar_l)) if (avail and wrap) else None
    body = "".join(_run_markup(r.text, r.style, fonts, default_color, line_w) for r in runs)
    edges = f"top-edge: {top:.3f}em, bottom-edge: {-bottom:.3f}em"
    align = {"ctr": "center", "r": "right", "just": "left", "dist": "left", "justLow": "left", "thaiDist": "left"}.get(ps.align, "left")
    justify = "true" if ps.align in ("just", "dist") else "false"
    if not body:
        # An empty paragraph still takes one line.
        out.append(f"#block(height: {pt(pitch)})[]")
    else:
        indent_l = max(0.0, ps.mar_l + min(0.0, ps.indent))
        hang = -ps.indent if ps.indent < 0 else 0.0
        bul = ""
        if ps.bullet:
            b = bullet_char(ps.bullet, ps.bullet_font) if not ps.bullet_auto else ps.bullet
            bst = runs[0].style
            bcol = ps.bullet_color or bst.color or default_color
            bsize = size * (ps.bullet_size_pct or 1.0)
            bfam = fonts.resolve(ps.bullet_font if ps.bullet_font and "wingdings" not in ps.bullet_font.lower() and ps.bullet_font.lower() != "symbol" else bst.font)[0]
            bargs = [f"font: {bfam}", f"size: {pt(bsize)}"]
            if bcol is not None:
                bargs.append(f"fill: {bcol.typst()}")
            if ps.bullet_auto and bst.bold:
                bargs.append('weight: "bold"')
            bul = f"#text({', '.join(bargs)}, {tstr(b)})"
        inner = f"#par(leading: {lead}, justify: {justify})[{body}]"
        if not wrap:
            inner = f"#box({inner[1:]})"
        if bul:
            gap = hang if hang > 0 else size * 0.6
            content = f"#grid(columns: ({pt(gap)}, 1fr), column-gutter: 0pt, align: (left + top, {align} + top), [{bul}], [{inner}])"
            wrapper = f"#pad(left: {pt(indent_l)})[{content}]"
        else:
            extra = f", hanging-indent: {pt(hang)}" if hang > 0 else ""
            if ps.indent > 0:
                extra = f", first-line-indent: (amount: {pt(ps.indent)}, all: true)"
            inner = f"#par(leading: {lead}, justify: {justify}{extra})[{body}]"
            if not wrap:
                inner = f"#box({inner[1:]})"
            left = max(0.0, ps.mar_l) if ps.indent > 0 else indent_l
            wrapper = f"#pad(left: {pt(left)})[#align({align})[{inner}]]"
        out.append(f"#block(width: 100%)[#set text({edges}, size: {pt(size)})\n{wrapper}]")
    if ps.spc_after:
        out.append(f"#v({pt(ps.spc_after)})")
    return "\n".join(out)


def frame_body(tf: TextFrame, fonts: FontEnv, default_color: Color | None = None, avail: float | None = None) -> str:
    """The paragraphs of a text frame (no box, insets or anchoring) as Typst markup."""
    parts = []
    first = True
    for p in tf.paras:
        parts.append(para_markup(p, fonts, first, tf.body.wrap, default_color, avail))
        first = False
    return "\n".join(parts)


def frame_box(tf: TextFrame, w_pt: float, h_pt: float, fonts: FontEnv, default_color: Color | None = None) -> str:
    """A text frame laid out in its shape's box (insets, vertical anchor, vertical text), as a Typst expression."""
    b = tf.body
    vert0 = b.vert in ("vert", "eaVert", "vert270", "wordArtVert", "mongolianVert", "wordArtVertRtl")
    iw0 = max(1.0, (h_pt if vert0 else w_pt) - b.l - b.r)
    body = frame_body(tf, fonts, default_color, iw0)
    anchor = {"t": "top", "ctr": "horizon", "b": "bottom", "just": "top", "dist": "top"}.get(b.anchor, "top")
    vert = b.vert in ("vert", "eaVert", "vert270", "wordArtVert", "mongolianVert", "wordArtVertRtl")
    bw, bh = (h_pt, w_pt) if vert else (w_pt, h_pt)
    iw = max(1.0, bw - b.l - b.r)
    ih = max(1.0, bh - b.t - b.b)
    width = f"{pt(iw)}" if b.wrap else "auto"
    inner = f"block(width: {width}, height: {pt(ih)})[#align({anchor})[#block(width: {'100%' if b.wrap else 'auto'})[\n{body}\n]]]"
    boxed = f"box(width: {pt(bw)}, height: {pt(bh)}, inset: (left: {pt(b.l)}, right: {pt(b.r)}, top: {pt(b.t)}, bottom: {pt(b.b)}))[#{inner}]"
    if vert:
        ang = -90 if b.vert == "vert270" else 90
        boxed = f"box(width: {pt(w_pt)}, height: {pt(h_pt)})[#place(center + horizon, rotate({ang}deg, reflow: false, {boxed}))]"
    return boxed


# ── measuring ───────────────────────────────────────────────────────────


class Measurer:
    """Measures many text blocks with one Typst compile: add() them, then run() → {key: (width_pt, height_pt)}."""

    def __init__(self, fonts: FontEnv | None = None):
        self.fonts = fonts or FontEnv()
        self.items: list[str] = []
        self.word_meta: dict[str, list[tuple[str, float, str]]] = {}

    def add_frame(self, key: str, tf: TextFrame, width_pt: float | None, default_color: Color | None = None) -> None:
        self.add_markup(key, frame_body(tf, self.fonts, default_color), width_pt)

    def add_words(self, key: str, tf: TextFrame, top: int = 3) -> None:
        """Measures a frame's longest unbreakable words (PowerPoint breaks a word wider than its box mid-word)."""
        cands: list[tuple[float, str, Any, float]] = []
        for p in tf.paras:
            indent = max(0.0, p.style.mar_l)
            for r in p.runs:
                for w in r.text.split():
                    cands.append((len(w) * r.style.size, w, r.style, indent))
        cands.sort(key=lambda c: -c[0])
        seen: set[tuple[Any, ...]] = set()
        meta: list[tuple[str, float, str]] = []
        for _est, w, st, indent in cands:
            sig = (w, st.size, st.font, st.bold, st.italic)
            if sig in seen:
                continue
            seen.add(sig)
            k = f"{key}{len(meta)}"
            self.add_markup(k, _run_markup(w, st, self.fonts, None), None)
            meta.append((k, indent, w))
            if len(meta) >= top:
                break
        self.word_meta[key] = meta

    def word_ratio(self, sizes: dict[str, tuple[float, float]], key: str, width_pt: float) -> tuple[float, str]:
        """(widest word ÷ the width it has, that word) for a frame measured with add_words; 0 when it has none."""
        best, word = 0.0, ""
        for k, indent, w in self.word_meta.get(key, []):
            got = sizes.get(k)
            if got:
                r = got[0] / max(1.0, width_pt - indent)
                if r > best:
                    best, word = r, w
        return best, word

    def add_markup(self, key: str, markup: str, width_pt: float | None) -> None:
        w = "none" if width_pt is None else pt(max(1.0, width_pt))
        self.items.append(f"#m({tstr(key)}, {w})[\n{markup}\n]")

    def run(self) -> dict[str, tuple[float, float]]:
        if not self.items:
            return {}
        src = PREAMBLE + "#set page(width: auto, height: auto)\n" + (
            "#let m(id, w, body) = context { let s = if w == none { measure(body) } else { measure(block(width: w, body)) }; "
            "[#metadata((id: id, w: s.width.pt(), h: s.height.pt())) <m>] }\n"
        ) + "\n".join(self.items)
        raw = _query(src)
        return {d["id"]: (float(d["w"]), float(d["h"])) for d in json.loads(raw)}


_COMPILER: Any = None
_WORKDIR: Any = None
_BATCH = 0


def _query(src: str) -> str:
    """Compiles a measurement document and queries its metadata, with one Typst compiler per process (fonts are
    scanned once); each batch goes to a new file so nothing is served from Typst's cache."""
    global _COMPILER, _WORKDIR, _BATCH
    import atexit
    import shutil
    import tempfile
    from pathlib import Path

    import typst

    from _common import SkillError

    if _COMPILER is None:
        _WORKDIR = Path(tempfile.mkdtemp(prefix="desk-typst-measure-"))
        atexit.register(shutil.rmtree, str(_WORKDIR), True)
        _COMPILER = typst.Compiler(root=str(_WORKDIR))
    _BATCH += 1
    f = _WORKDIR / f"m{_BATCH}.typ"
    f.write_text(src, encoding="utf-8")
    try:
        _COMPILER.compile(input=str(f), format="svg")
        return _COMPILER.query("<m>", field="value")
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"text measurement failed: {str(e)[:400]}") from e
    finally:
        try:
            f.unlink()
        except OSError:
            pass
