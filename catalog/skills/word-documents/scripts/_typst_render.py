"""The built-in (approximate) renderer: a Word document drawn with Typst, for machines without LibreOffice.

It respects page size and margins, sections, headers and footers (first-page and even variants), fonts, sizes,
bold/italic/underline/strike/colour/highlight, caps, super/subscript, paragraph alignment, spacing, line spacing,
indents, borders and shading, tab stops, list labels, tables (grid widths, borders, shading, merged cells, table
style conditional formats, header rows), images (inline, cropped, floating), text boxes, footnotes and endnotes,
page and TOC fields, page and column breaks, equations, and tracked changes (markup, accepted or rejected).
Layout is Typst's, so line and page breaks can differ from Word's.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from lxml import etree

from _common import SkillError
from _docx import ALTCONTENT, BR, CR, DEL, DELTEXT, FLDCHAR, FLDSIMPLE, HYPERLINK, INS, INSTR, MC_CHOICE, MOVEFROM, MOVETO, NBH, NS, OMATH, OMATHPARA, P, PPR, R, RPR, SDT, SDTCONTENT, SECTPR, SYM, T, TAB, TBL, TBLPR, TCPR, TR, TRPR, TXBX, Doc, field_type, iter_blocks, qn, style_id, sym_char, table_grid, twips, units, wattr
from _numbering import Counter
from _render import typst_string
from _styles import HIGHLIGHT, flag, flatten, merge

_W = "{" + NS["w"] + "}"
_R = "{" + NS["r"] + "}"
_A = "{" + NS["a"] + "}"
_WP = "{" + NS["wp"] + "}"
DRAWING, PICT, OBJECT = qn("w:drawing"), qn("w:pict"), qn("w:object")
FOOTREF, ENDREF = qn("w:footnoteReference"), qn("w:endnoteReference")
EMU = 12700.0

SERIF_HINTS = ("times", "georgia", "cambria", "garamond", "book antiqua", "palatino", "baskerville", "century", "bookman", "constantia", "minion", "serif", "caslon", "didot", "bodoni", "charter", "liberation serif", "tinos", "caladea", "libertinus", "computer modern", "sabon", "perpetua", "rockwell", "goudy")
MONO_HINTS = ("courier", "consolas", "menlo", "monaco", "mono", "lucida console", "source code", "inconsolata", "fira code", "cousine")
METRIC_TWINS = {"calibri": ["Carlito"], "cambria": ["Caladea"], "arial": ["Liberation Sans", "Arimo", "Helvetica"], "times new roman": ["Liberation Serif", "Tinos", "Times"], "courier new": ["Liberation Mono", "Cousine", "Courier"], "helvetica": ["Arial", "Liberation Sans"], "aptos": ["Calibri", "Carlito"], "calibri light": ["Calibri", "Carlito"], "segoe ui": ["Helvetica Neue", "Arial"]}
SANS = ["Calibri", "Carlito", "Arial", "Helvetica Neue", "Helvetica", "Liberation Sans", "DejaVu Sans", "Noto Sans", "Segoe UI"]
SERIF = ["Times New Roman", "Liberation Serif", "Times", "Georgia", "Cambria", "Caladea", "Libertinus Serif"]
MONO = ["Consolas", "Menlo", "Courier New", "Liberation Mono", "DejaVu Sans Mono"]
TRACK_COLOR = "C00000"

_FONT_FAMILIES: set[str] | None = None


def available_fonts() -> set[str]:
    global _FONT_FAMILIES
    if _FONT_FAMILIES is None:
        try:
            import typst

            _FONT_FAMILIES = {f.lower() for f in typst.Fonts().families()}
        except Exception:  # noqa: BLE001 — font listing is an optimisation
            _FONT_FAMILIES = set()
    return _FONT_FAMILIES


def font_stack(name: str | None, subs: dict[str, str]) -> list[str]:
    """Fallback font list for a Word font, keeping only fonts Typst can find (embedded ones always last)."""
    name = (name or "Calibri").strip()
    low = name.lower()
    cls = MONO if any(h in low for h in MONO_HINTS) else SERIF if any(h in low for h in SERIF_HINTS) else SANS
    cand = [name] + METRIC_TWINS.get(low, []) + cls
    avail = available_fonts()
    out: list[str] = []
    for c in cand:
        if (not avail or c.lower() in avail) and c not in out:
            out.append(c)
    last = "DejaVu Sans Mono" if cls is MONO else "Libertinus Serif"
    if last not in out:
        out.append(last)
    if avail and low not in avail:
        subs[name] = out[0]
    return out


NUMPAGES_CODE = "#context locate(<desk-end>).page()"


def esc(text: str) -> str:
    """Typst markup for literal text: specials escaped, repeated and leading spaces kept."""
    out = []
    for ch in text:
        # '(' too: right after an embedded call like #text(..)[..], a '(' or '[' would continue the call.
        if ch in '\\#*_`$<>@[]()~/"\'=-+.':
            out.append("\\" + ch)
        elif ch == "\u00a0":
            out.append("~")
        elif ord(ch) < 32:
            continue
        else:
            out.append(ch)
    s = "".join(out)
    s = re.sub(r" {2,}", lambda m: " " + "~" * (len(m.group(0)) - 1), s)
    return s


def pt(v: float) -> str:
    return f"{v:.2f}pt"


def rgb(hex6: str) -> str:
    return f'rgb("#{hex6}")'


class Geometry:
    def __init__(self, sect: Any) -> None:
        pg = sect.find(qn("w:pgSz")) if sect is not None else None
        mar = sect.find(qn("w:pgMar")) if sect is not None else None
        self.w = twips(wattr(pg, "w"), 612) or 612
        self.h = twips(wattr(pg, "h"), 792) or 792
        self.top = abs(twips(wattr(mar, "top"), 72))
        self.bottom = abs(twips(wattr(mar, "bottom"), 72))
        self.left = twips(wattr(mar, "left"), 72)
        self.right = twips(wattr(mar, "right"), 72)
        self.header = twips(wattr(mar, "header"), 36)
        self.footer = twips(wattr(mar, "footer"), 36)
        self.gutter = twips(wattr(mar, "gutter"), 0)
        self.left += self.gutter
        cols = sect.find(qn("w:cols")) if sect is not None else None
        self.cols = int(wattr(cols, "num", "1") or 1) if cols is not None else 1
        self.col_space = twips(wattr(cols, "space"), 36) if cols is not None else 36
        # Columns of different widths (w:equalWidth="0" with one w:col each): (width, space after) in points.
        self.col_list: list[tuple[float, float]] = []
        if cols is not None and wattr(cols, "equalWidth") in ("0", "false", "off"):
            self.col_list = [(twips(wattr(c, "w"), 0), twips(wattr(c, "space"), 0)) for c in cols.findall(qn("w:col"))]
            if len(self.col_list) > 1:
                self.cols = len(self.col_list)
        t = sect.find(qn("w:type")) if sect is not None else None
        self.start = wattr(t, "val", "nextPage") if t is not None else "nextPage"
        pn = sect.find(qn("w:pgNumType")) if sect is not None else None
        self.pg_start = wattr(pn, "start") if pn is not None else None
        self.pg_fmt = {"lowerRoman": "i", "upperRoman": "I", "lowerLetter": "a", "upperLetter": "A"}.get(wattr(pn, "fmt") or "", "1") if pn is not None else "1"
        tp = sect.find(qn("w:titlePg")) if sect is not None else None
        self.title_pg = tp is not None and wattr(tp, "val") not in ("0", "false")
        self.text_w = max(36.0, self.w - self.left - self.right)
        self.text_h = max(36.0, self.h - self.top - self.bottom)

    def same_paper(self, o: "Geometry | None") -> bool:
        return o is not None and (round(self.w), round(self.h)) == (round(o.w), round(o.h))

    def same_page(self, o: "Geometry") -> bool:
        return (round(self.w), round(self.h), round(self.top), round(self.bottom), round(self.left), round(self.right)) == (round(o.w), round(o.h), round(o.top), round(o.bottom), round(o.left), round(o.right))


class Renderer:
    def __init__(self, doc: Doc, workdir: Path, changes: str = "markup", safe: bool = False) -> None:
        self.doc = doc
        self.styles = doc.styles
        self.numbering = doc.numbering
        self.counter = Counter(self.numbering)
        self.workdir = workdir
        self.changes = changes
        self.safe = safe
        self.subs: dict[str, str] = {}
        self.notes: list[str] = []
        self.images: dict[str, str] = {}
        self.endnotes: list[str] = []
        self.labels: dict[int, str] = {}
        self.bookmark_labels: dict[str, str] = {}
        self.fields: list[dict[str, Any]] = []
        self.geom = Geometry(None)
        self.pg_fmt = "1"
        settings = doc.settings
        dts = settings.find(qn("w:defaultTabStop")) if settings is not None else None
        self.default_tab = twips(wattr(dts, "val"), 36) or 36
        self.even_odd = settings is not None and settings.find(qn("w:evenAndOddHeaders")) is not None
        self._footnotes = {wattr(n, "id"): n for n in (doc.footnotes.findall(qn("w:footnote")) if doc.footnotes is not None else [])}
        self._endnotes = {wattr(n, "id"): n for n in (doc.endnotes.findall(qn("w:endnote")) if doc.endnotes is not None else [])}
        self.fn_part = doc.rel_part("http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes")
        self.en_part = doc.rel_part("http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes")
        self.headings: list[tuple[int, int, str]] = []
        self._heading_by_id: dict[int, tuple[int, str]] = {}
        self._img_n = 0
        self._label_n = 0

    # ── document ────────────────────────────────────────────────────────
    def build(self) -> str:
        body = self.doc.body
        blocks = list(iter_blocks(body))
        self._prepass(blocks)
        sections = self.doc.section_elements() or [None]
        # split blocks by section
        groups: list[list[Any]] = [[]]
        for el in blocks:
            groups[-1].append(el)
            if el.tag == P:
                ppr = el.find(PPR)
                if ppr is not None and ppr.find(SECTPR) is not None and len(groups) < len(sections):
                    groups.append([])
        while len(groups) < len(sections):
            groups.append([])
        out: list[str] = [self._preamble()]
        hf_refs: dict[str, Any] = {}
        prev_geom: Geometry | None = None
        page_geom: Geometry | None = None
        for si, (sect, group) in enumerate(zip(sections, groups)):
            g = Geometry(sect)
            self.geom = g
            self.pg_fmt = g.pg_fmt
            if sect is not None:
                for ref in sect:
                    if ref.tag in (qn("w:headerReference"), qn("w:footerReference")):
                        kind = "header" if ref.tag == qn("w:headerReference") else "footer"
                        hf_refs[f"{kind}:{wattr(ref, 'type', 'default')}"] = ref.get(_R + "id")
            label = f"desk-sec-{si}"
            # Like Word: a continuous section stays on the page unless the paper size changes; its own top and
            # bottom margins wait for the next page, and different side margins indent its text.
            new_page = prev_geom is None or g.start != "continuous" or not g.same_paper(page_geom)
            if new_page:
                if prev_geom is not None and g.start in ("oddPage", "evenPage"):
                    out.append(f'#pagebreak(to: "{"odd" if g.start == "oddPage" else "even"}")')
                out.append(self._page_setup(g, hf_refs, label))
                page_geom = g
            out.append(f"#metadata(none) <{label}>")
            if g.pg_start is not None:
                out.append(f"#counter(page).update({int(g.pg_start)})")
            content = self.flow(group)
            chunks = content.split("\n#colbreak()\n") if len(g.col_list) > 1 else []
            if chunks and len(chunks) <= g.cols:
                # Typst's columns are all alike; columns of different widths filled up to their column breaks
                # (a résumé's side column, say) become a grid.
                chunks += [""] * (g.cols - len(chunks))
                widths = ", ".join(pt(w) for w, _ in g.col_list)
                gaps = ", ".join(pt(sp) for _, sp in g.col_list[:-1])
                cells = ", ".join(f"[\n{c}\n]" for c in chunks)
                content = f"#grid(columns: ({widths},), column-gutter: ({gaps},), {cells})"
            elif g.cols > 1 and (not new_page or len(g.col_list) > 1):
                content = f"#columns({g.cols}, gutter: {pt(g.col_space)})[\n{content}\n]"
            if not new_page and (abs(g.left - page_geom.left) > 0.5 or abs(g.right - page_geom.right) > 0.5):
                content = f"#pad(left: {pt(g.left - page_geom.left)}, right: {pt(g.right - page_geom.right)})[\n{content}\n]"
            out.append(content)
            prev_geom = g
        if self.endnotes:
            out.append("#v(18pt)\n#line(length: 30%, stroke: 0.5pt)\n" + "\n".join(self.endnotes))
        out.append("#metadata(none) <desk-end>")
        return "\n".join(out) + "\n"

    def _prepass(self, blocks: list[Any]) -> None:
        """Labels for bookmarked paragraphs (PAGEREF targets) and headings (auto TOC)."""
        from _docx import heading_level, para_text

        pending: list[str] = []
        for el in blocks:
            if el.tag != P:
                pending = []
                continue
            names = [wattr(b, "name") for b in el.iter(qn("w:bookmarkStart"))]
            prev = el.getprevious()
            while prev is not None and prev.tag == qn("w:bookmarkStart"):
                names.append(wattr(prev, "name"))
                prev = prev.getprevious()
            lvl = heading_level(el, self.styles)
            if names or lvl:
                lab = self._new_label()
                self.labels[id(el)] = lab
                for n in names:
                    if n:
                        self.bookmark_labels.setdefault(n, lab)
                if lvl and lvl <= 9:
                    text = para_text(el).strip()
                    if text:
                        self.headings.append((lvl, id(el), text))
                        self._heading_by_id[id(el)] = (lvl, " ".join(text.split()))

    def _new_label(self) -> str:
        self._label_n += 1
        return f"desk-l{self._label_n}"

    def _preamble(self) -> str:
        st = self.styles
        rpr = merge(st.doc_rpr, st.para_style_rpr(None))
        size = self._size(rpr)
        font = font_stack(st.font_name(rpr), self.subs)
        lang = ((rpr.get("lang") or {}).get("val") or "en").split("-")[0].lower()
        if not re.fullmatch(r"[a-z]{2,3}", lang):
            lang = "en"
        return "\n".join(
            [
                f"#set text(font: ({', '.join(typst_string(f) for f in font)},), size: {pt(size)}, lang: {typst_string(lang)}, hyphenate: false, top-edge: 0.92em, bottom-edge: -0.25em, fallback: true)",
                "#set par(spacing: 0pt, leading: 0em, justify: false)",
                "#set block(spacing: 0pt, above: 0pt, below: 0pt)",
                "#set footnote.entry(separator: line(length: 30%, stroke: 0.5pt), gap: 0.4em)",
                "#show footnote.entry: set text(size: 0.85em)",
                "#let desk-tab(stops, left, dflt, seg) = context { let x = here().position().x - left; let s = stops.find(s => s.at(0) > x + 0.5pt); if s == none { let n = (calc.floor(x / dflt) + 1) * dflt; h(calc.max(n - x, 1pt)) } else { let w = if seg == none or s.at(1) == \"left\" { 0pt } else if s.at(1) == \"center\" { measure(seg).width / 2 } else { measure(seg).width }; let gap = calc.max(s.at(0) - x - w - (if w > 0pt { 0.3pt } else { 0pt }), 1pt); if s.at(2) == none { h(gap) } else { box(width: gap, repeat(s.at(2))) } } }",
            ]
        )

    def _page_setup(self, g: Geometry, refs: dict[str, Any], label: str) -> str:
        header = self._hf_code("header", g, refs, label)
        footer = self._hf_code("footer", g, refs, label)
        parts = [f"width: {pt(g.w)}", f"height: {pt(g.h)}", f"margin: (top: {pt(g.top)}, bottom: {pt(g.bottom)}, left: {pt(g.left)}, right: {pt(g.right)})", "header-ascent: 0pt", "footer-descent: 0pt"]
        if g.cols > 1 and len(g.col_list) < 2:
            parts.append(f"columns: {g.cols}")
        parts.append(f"header: {header}")
        parts.append(f"footer: {footer}")
        return "#set page(" + ", ".join(parts) + ")\n" + (f"#set columns(gutter: {pt(g.col_space)})" if g.cols > 1 else "")

    def _hf_code(self, kind: str, g: Geometry, refs: dict[str, Any], label: str) -> str:
        variants = {}
        for v in ("default", "first", "even"):
            rid = refs.get(f"{kind}:{v}")
            if rid:
                variants[v] = self._hf_part_code(kind, rid, g)
        if not variants:
            return "none"
        default = variants.get("default", "[]")
        first = variants.get("first", "[]") if g.title_pg else default
        even = variants.get("even", default) if self.even_odd else default
        if first == default and even == default:
            return default
        return f"context {{ let p = here().page(); let s = locate(<{label}>).page(); if p == s {{ {first} }} else if calc.even(p) {{ {even} }} else {{ {default} }} }}"

    def _hf_part_code(self, kind: str, rid: str, g: Geometry) -> str:
        rel = self.doc.part.rels.get(rid)
        if rel is None or rel.is_external:
            return "[]"
        part = rel.target_part
        root = self.doc.xml(part)
        saved_fields = self.fields
        self.fields = []
        inner = self.flow(list(iter_blocks(root)), part=part, story=kind)
        self.fields = saved_fields
        if kind == "header":
            return f"block(width: 100%, height: {pt(g.top)}, inset: (top: {pt(min(g.header, g.top))}))[#align(top)[#block(width: 100%)[\n{inner}\n]]]"
        return f"block(width: 100%, height: {pt(g.bottom)}, inset: (bottom: {pt(min(g.footer, g.bottom))}))[#align(bottom)[#block(width: 100%)[\n{inner}\n]]]"

    # ── flow of blocks ──────────────────────────────────────────────────
    def flow(self, blocks: list[Any], part: Any = None, story: str = "body", tbl: dict[str, Any] | None = None, width: float | None = None) -> str:
        out: list[str] = []
        prev: dict[str, Any] | None = None
        for el in blocks:
            if el.tag == P:
                code, info = self.paragraph(el, part=part, story=story, tbl=tbl, width=width)
                same = prev is not None and prev.get("style") == info.get("style")
                before = 0.0 if (info.get("contextual") and same) else info["before"]
                if prev is None:
                    gap = before
                else:
                    gap = (0.0 if (prev.get("contextual") and same) else prev["after"]) + before
                if gap > 0:
                    out.append(f"#v({pt(gap)}, weak: {'false' if (tbl is not None and prev is None) else 'true'})")
                out.append(code)
                prev = info
            elif el.tag == TBL:
                if prev is not None and prev["after"] > 0:
                    out.append(f"#v({pt(prev['after'])}, weak: true)")
                out.append(self.table(el, part=part, story=story, width=width))
                prev = {"before": 0.0, "after": 0.0, "style": None}
        if prev is not None and prev["after"] > 0 and (tbl is not None or story == "body"):
            out.append(f"#v({pt(prev['after'])}, weak: {'false' if tbl is not None else 'true'})")
        return "\n".join(out)

    # ── paragraphs ──────────────────────────────────────────────────────
    def para_props(self, p: Any, tbl: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        st = self.styles
        sid = style_id(p)
        ppr = dict(st.doc_ppr)
        rpr = dict(st.doc_rpr)
        if tbl is not None:
            ppr = merge(ppr, tbl.get("ppr", {}))
            rpr = merge(rpr, tbl.get("rpr", {}))
        ppr = merge(ppr, st.para_style_ppr(sid))
        rpr = merge(rpr, st.para_style_rpr(sid))
        if tbl is not None:
            # Conditional table formats (header row bold, banding) win over the paragraph style's run properties.
            rpr = merge(rpr, tbl.get("cond_rpr", {}))
            ppr = merge(ppr, tbl.get("cond_ppr", {}))
        return ppr, rpr, sid

    def paragraph(self, p: Any, part: Any = None, story: str = "body", tbl: dict[str, Any] | None = None, width: float | None = None) -> tuple[str, dict[str, Any]]:
        part = part or self.doc.part
        saved_anchors = self._pending_anchors
        self._pending_anchors = []
        ppr, base_rpr, sid = self.para_props(p, tbl)
        direct_ppr = flatten(p.find(PPR))
        num = self.numbering.num_props(p)
        label = ""
        lvl = None
        if num:
            label, lvl = self.counter.label(*num)
            if lvl is not None:
                ppr = merge(ppr, lvl.ppr)
        ppr = merge(ppr, direct_ppr)
        mark_rpr = flatten(p.find(PPR).find(RPR)) if p.find(PPR) is not None and p.find(PPR).find(RPR) is not None else {}
        size = self._size(base_rpr)
        # spacing
        sp = ppr.get("spacing") or {}
        before = twips(sp.get("before"), 0.0)
        after = twips(sp.get("after"), 0.0)
        if sp.get("beforeAutospacing") in ("1", "true", "on"):
            before = 14.0
        if sp.get("afterAutospacing") in ("1", "true", "on"):
            after = 14.0
        line = sp.get("line")
        rule = sp.get("lineRule", "auto")
        natural = 1.17  # the line box (top-edge 0.92em + bottom-edge 0.25em) is Word's single line
        if line and rule == "auto":
            mult = max(0.5, units(line, 20, 240) / 240)
            leading = f"{max(0.0, natural * (mult - 1)):.3f}em"
            top_pad = size * natural * (mult - 1) if mult > 1 else 0.0
        elif line and rule == "exact":
            pitch = twips(line)
            leading = pt(max(0.0, pitch - size * natural))
            top_pad = 0.0
        elif line and rule == "atLeast":
            pitch = max(twips(line), size * natural)
            leading = pt(max(0.0, pitch - size * natural))
            top_pad = 0.0
        else:
            leading = "0em"
            top_pad = 0.0
        ind = ppr.get("ind") or {}
        left = twips(ind.get("left") or ind.get("start"), 0.0)
        right = twips(ind.get("right") or ind.get("end"), 0.0)
        first = twips(ind.get("firstLine"), 0.0)
        hanging = twips(ind.get("hanging"), 0.0)
        jc = (ppr.get("jc") or {}).get("val", "left")
        align = {"center": "center", "right": "right", "end": "right", "both": "left", "distribute": "left", "left": "left", "start": "left"}.get(jc, "left")
        justify = jc in ("both", "distribute")
        info = {"before": before, "after": after, "style": sid, "contextual": "contextualSpacing" in ppr and flag(ppr, "contextualSpacing")}
        # content
        items = self.inline(p, base_rpr, part, story)
        has_text = any(k == "text" and v.strip() for k, v in items)
        # Like Word, an empty list paragraph still shows its bullet or number.
        body_segments = self._segments(items, ppr, left if not hanging else left - hanging)
        lab_code = ""
        if label:
            lrpr = merge(base_rpr, lvl.rpr) if lvl is not None else base_rpr
            lrpr = merge(lrpr, {k: v for k, v in mark_rpr.items() if k in ("b", "i", "color", "sz", "rFonts")})
            ltext = self._run_code(label, lrpr, base_rpr)
            suff = lvl.suff if lvl is not None else "tab"
            if suff == "tab":
                hang = hanging if hanging > 0 else 18.0
                if self.safe:
                    lab_code = f"#box(width: {pt(hang)})[{ltext}]"
                else:
                    # Like Word: the text starts at the hanging indent, or at the next tab stop when the label is wider.
                    lab_code = f"#context {{ let l = [{ltext}]; let w = measure(l).width + 2pt; box(width: if w > {pt(hang)} {{ {pt(hang)} + calc.ceil((w - {pt(hang)}) / {pt(self.default_tab)}) * {pt(self.default_tab)} }} else {{ {pt(hang)} }})[#l] }}"
                if not hanging:
                    left_eff = left
                    first = 0.0
            elif suff == "space":
                lab_code = ltext + " "
            else:
                lab_code = ltext
        # indentation: the first line starts at left - hanging (or left + firstLine)
        inset_left = left - hanging if hanging else left
        par_args = [f"leading: {leading}", f"justify: {'true' if justify else 'false'}"]
        if hanging:
            par_args.append(f"hanging-indent: {pt(hanging)}")
        elif first:
            par_args.append(f"first-line-indent: (amount: {pt(first)}, all: true)")
        text_rpr = self._text_args(base_rpr, None)
        block_args = ["width: 100%", "breakable: true"]
        pads = []
        if inset_left:
            pads.append(f"left: {pt(inset_left)}")
        if right:
            pads.append(f"right: {pt(right)}")
        bdr = ppr.get("pBdr") or {}
        stroke = self._borders(bdr)
        insets = [f"top: {pt(top_pad)}"] if top_pad else []
        if stroke:
            block_args.append(f"stroke: {stroke}")
            outs = []
            for side in ("top", "bottom", "left", "right"):
                b = bdr.get(side)
                if b and b.get("val") not in ("nil", "none"):
                    try:
                        sp_ = float(b.get("space", "1") or 1)
                        wd = units(b.get("sz"), 8, 4.0) / 8
                    except ValueError:
                        sp_, wd = 1.0, 0.5
                    if side in ("top", "bottom"):
                        # Word puts top and bottom borders inside the paragraph's height.
                        insets = [x for x in insets if not x.startswith(side)] + [f"{side}: {pt(sp_ + wd + (top_pad if side == 'top' else 0))}"]
                    else:
                        outs.append(f"{side}: {pt(sp_ + wd)}")
            if outs:
                block_args.append("outset: (" + ", ".join(outs) + ")")
        if insets:
            block_args.append("inset: (" + ", ".join(insets) + ")")
        shd = self.styles.color(ppr.get("shd"), "fill")
        if shd:
            block_args.append(f"fill: {rgb(shd)}")
        pieces = []
        brk = ""
        if flag(ppr, "pageBreakBefore") and story == "body" and tbl is None:
            brk = "#pagebreak(weak: true)\n"
        empty_size = self._size(merge(base_rpr, mark_rpr)) if not has_text else size
        hd = self._heading_by_id.get(id(p)) if story == "body" and tbl is None and not self.safe else None
        # An invisible heading in a zero-size box: the PDF gets a bookmark (outline entry) at this paragraph, which
        # PDF viewers show as a navigation tree and docx_create reads back for the TOC's page numbers.
        bookmark = f"#box(width: 0pt, height: 0pt, hide(heading(level: {hd[0]}, outlined: false, numbering: none, bookmarked: true)[{esc(hd[1][:200])}]))" if hd else ""
        for si, seg in enumerate(body_segments):
            if seg == "\x00PAGE":
                if story == "body" and tbl is None:
                    pieces.append("#pagebreak()")
                continue
            if seg == "\x00COL":
                if story == "body" and tbl is None:
                    pieces.append("#colbreak()")
                continue
            content = (lab_code if si == 0 else "") + seg
            if not content.strip():
                # Like Word, an empty paragraph is one line of its mark's font size tall.
                content = f"#text(size: {pt(empty_size)})[#sym.zws]"
            if bookmark:
                content = bookmark + content
                bookmark = ""
            code = f"block({', '.join(block_args)}, {{ set text({text_rpr}); set align({align}); par({', '.join(par_args)})[{content}] }})"
            code = f"#pad({', '.join(pads)})[#{code}]" if pads else "#" + code
            pieces.append(code)
        lab = self.labels.get(id(p))
        if lab and pieces:
            for i, pc in enumerate(pieces):
                if not pc.startswith("#pagebreak") and not pc.startswith("#colbreak"):
                    pieces[i] = pc + f"<{lab}>"
                    break
        anchors = self._pending_anchors
        self._pending_anchors = saved_anchors
        return brk + "\n".join(anchors + pieces), info

    _pending_anchors: list[str] = []

    def _segments(self, items: list[tuple[str, str]], ppr: dict[str, Any], left_edge: float) -> list[str]:
        """Joins inline items into markup, splitting at page and column breaks. Tabs go to the paragraph's tab
        stops: left stops start the text there, center and right stops centre or end the following text there."""
        stops = []
        for t in ppr.get("tabs") or []:
            try:
                pos = twips(t.get("pos"))
            except (TypeError, ValueError):
                continue
            kind = {"end": "right", "decimal": "right", "start": "left", "num": "left", "bar": "left"}.get(t.get("val", "left"), t.get("val", "left"))
            if kind == "clear":
                continue
            stops.append((pos, kind, t.get("leader")))
        stops.sort()
        segs: list[str] = []
        cur: list[str] = []
        last_tab = max((i for i, (k, _) in enumerate(items) if k == "tab"), default=-1)
        leaders = {"dot": ".", "middleDot": "·", "hyphen": "-", "underscore": "_", "heavy": "_"}
        # The last tab of a line going to a right stop at the margin ('left||right' footers, TOC entries): fill
        # the rest of the line, which needs no measuring and so never wraps the text before it.
        to_margin = bool(stops) and stops[-1][1] == "right" and stops[-1][0] >= self.geom.text_w - 3 and not self.safe
        for i, (kind, val) in enumerate(items):
            if kind == "text" or kind == "obj":
                cur.append(val)
            elif kind == "tab" and i == last_tab and to_margin:
                ld = leaders.get(stops[-1][2] or "")
                cur.append(f"#box(width: 1fr, repeat[{esc(ld)}])#h(2pt)" if ld else "#h(1fr)")
            elif kind == "tab":
                # The text this tab positions: up to the next tab or break.
                follow = []
                for k2, v2 in items[i + 1 :]:
                    if k2 not in ("text", "obj"):
                        break
                    follow.append(v2)
                cur.append(self._tab_code(stops, "".join(follow)))
            elif kind == "br":
                cur.append("#linebreak()")
            elif kind in ("page", "col"):
                segs.append("".join(cur))
                segs.append("\x00PAGE" if kind == "page" else "\x00COL")
                cur = []
        segs.append("".join(cur))
        if len(segs) > 1:
            # A paragraph holding a break: keep the break and the non-empty text around it.
            segs = [s for s in segs if s.startswith("\x00") or s.strip()]
        return segs

    def _tab_code(self, stops: list[tuple[float, str, str | None]], follow: str) -> str:
        if self.safe:
            return f"#h({pt(self.default_tab)})"
        leaders = {"dot": ".", "middleDot": "·", "hyphen": "-", "underscore": "_", "heavy": "_"}
        # Word lets text at a right tab past the margin run into the margin; here it stops at the margin instead.
        limit = self.geom.text_w
        stops = [(min(p, limit) if k != "left" else p, k, ld) for p, k, ld in stops]
        arr = "(" + "".join(f"({pt(p)}, {typst_string(k)}, {typst_string(leaders[ld]) if ld in leaders else 'none'})," for p, k, ld in stops) + ")"
        # Measuring the following text lays it out a second time: notes and placed objects must not be duplicated.
        measurable = follow and "#footnote" not in follow and "#metadata" not in follow and "#place" not in follow
        seg = f"[{follow}]" if measurable and any(k != "left" for _, k, _ in stops) else "none"
        return f"#desk-tab({arr}, {pt(self.geom.left)}, {pt(self.default_tab)}, {seg})"

    # ── inline content ──────────────────────────────────────────────────
    def inline(self, p: Any, base_rpr: dict[str, Any], part: Any, story: str) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        self._walk(p, items, base_rpr, part, None, None, story)
        # merge adjacent text items
        merged: list[tuple[str, str]] = []
        for k, v in items:
            if merged and k == "text" and merged[-1][0] == "text":
                merged[-1] = ("text", merged[-1][1] + v)
            else:
                merged.append((k, v))
        return merged

    def _walk(self, el: Any, items: list[tuple[str, str]], base: dict[str, Any], part: Any, change: str | None, link: str | None, story: str) -> None:
        for child in el:
            tag = child.tag
            if tag == R:
                self._run(child, items, base, part, change, link, story)
            elif tag == HYPERLINK:
                rid = child.get(_R + "id")
                target = self.doc.rels_target(rid, part)[0] if rid else None
                sub: list[tuple[str, str]] = []
                self._walk(child, sub, base, part, change, target, story)
                if target and not self.safe and re.match(r"^(https?|mailto|ftp|file):", target):
                    inner = "".join(v for k, v in sub if k in ("text", "obj"))
                    others = [(k, v) for k, v in sub if k not in ("text", "obj")]
                    if inner:
                        items.append(("text", f"#link({typst_string(target)})[{inner}]"))
                    items.extend(others)
                else:
                    items.extend(sub)
            elif tag in (INS, MOVETO):
                if self.changes == "reject":
                    continue
                self._walk(child, items, base, part, "ins" if self.changes == "markup" else change, link, story)
            elif tag in (DEL, MOVEFROM):
                if self.changes == "accept":
                    continue
                self._walk(child, items, base, part, "del" if self.changes == "markup" else "deltext", link, story)
            elif tag == FLDSIMPLE:
                instr = (child.get(_W + "instr") or "").strip()
                self.fields.append({"instr": [instr], "phase": "instr"})
                self._field_separate(items, story)
                self._walk(child, items, base, part, change, link, story)
                self._field_end(items)
            elif tag == SDT:
                content = child.find(SDTCONTENT)
                if content is not None:
                    self._walk(content, items, base, part, change, link, story)
            elif tag in (qn("w:smartTag"), qn("w:customXml"), qn("w:dir"), qn("w:bdo")):
                self._walk(child, items, base, part, change, link, story)
            elif tag == OMATH:
                items.append(("obj", "$" + omml_to_typst(child) + "$"))
            elif tag == OMATHPARA:
                eqs = [omml_to_typst(m) for m in child.iter(OMATH)]
                items.append(("obj", "#box(width: 100%)[#align(center)[" + "#linebreak()".join(f"$display({e})$" for e in eqs) + "]]"))

    def _run(self, r: Any, items: list[tuple[str, str]], base: dict[str, Any], part: Any, change: str | None, link: str | None, story: str) -> None:
        direct = flatten(r.find(RPR))
        rs = (direct.get("rStyle") or {}).get("val")
        props = merge(merge(base, self.styles.char_style_rpr(rs)), direct) if rs else merge(base, direct)
        hidden = flag(props, "vanish") and not flag(props, "specVanish")
        deleted = change in ("del", "deltext")
        buf: list[str] = []

        def flush() -> None:
            if buf:
                text = "".join(buf)
                buf.clear()
                items.append(("text", self._run_code(text, props, base, change)))

        for c in r:
            tag = c.tag
            if tag == FLDCHAR:
                flush()
                t = c.get(_W + "fldCharType")
                # Field results (page numbers…) take the formatting of the run that holds them.
                def wrap(code: str) -> str:
                    styled = self._run_code("DESKFIELDX", props, base, change)
                    return styled.replace("DESKFIELDX", code) if "DESKFIELDX" in styled else code

                if t == "begin":
                    self.fields.append({"instr": [], "phase": "instr"})
                elif t == "separate":
                    self._field_separate(items, story, wrap)
                elif t == "end":
                    self._field_end(items, wrap)
                continue
            if tag in (INSTR, qn("w:delInstrText")):
                if self.fields and self.fields[-1]["phase"] == "instr":
                    self.fields[-1]["instr"].append(c.text or "")
                continue
            if self.fields and (self.fields[-1]["phase"] == "instr" or self.fields[-1].get("suppress")):
                continue
            if hidden:
                continue
            if tag == T or (tag == DELTEXT and deleted):
                buf.append(c.text or "")
            elif tag == TAB or tag == qn("w:ptab"):
                flush()
                items.append(("tab", ""))
            elif tag == BR:
                flush()
                bt = c.get(_W + "type")
                items.append(("page", "") if bt == "page" else ("col", "") if bt == "column" else ("br", ""))
            elif tag == CR:
                flush()
                items.append(("br", ""))
            elif tag == NBH:
                buf.append("\u2011")
            elif tag == SYM:
                buf.append(sym_char(c))
            elif tag == FOOTREF:
                flush()
                items.append(("obj", self._footnote(c.get(_W + "id"))))
            elif tag == ENDREF:
                flush()
                items.append(("obj", self._endnote(c.get(_W + "id"))))
            elif tag == qn("w:footnoteRef") or tag == qn("w:endnoteRef"):
                continue
            elif tag in (DRAWING, PICT, OBJECT):
                flush()
                self._drawing(c, items, part, story)
            elif tag == ALTCONTENT:
                flush()
                choice = c.find(MC_CHOICE)
                target = choice if choice is not None else c.find(qn("mc:Fallback"))
                if target is not None:
                    for x in target:
                        if x.tag in (DRAWING, PICT, OBJECT):
                            self._drawing(x, items, part, story)
        flush()

    def _field_separate(self, items: list[tuple[str, str]], story: str, wrap: Any = lambda c: c) -> None:
        if not self.fields:
            return
        f = self.fields[-1]
        f["phase"] = "result"
        instr = "".join(f["instr"]).strip()
        ftype = field_type(instr) if instr else ""
        f["type"] = ftype
        if ftype in ("PAGE", "SECTIONPAGES", "NUMPAGES") and not self.safe:
            if ftype == "PAGE":
                items.append(("obj", wrap(f"#context counter(page).display({typst_string(self.pg_fmt)})")))
            elif ftype == "NUMPAGES":
                # Word counts the pages of the file, whatever page numbers the sections restart at.
                items.append(("obj", wrap(NUMPAGES_CODE)))
            else:
                items.append(("obj", wrap("#context counter(page).final().first()")))
            f["suppress"] = True
        elif ftype == "PAGEREF" and not self.safe:
            m = re.match(r"PAGEREF\s+(\S+)", instr)
            lab = self.bookmark_labels.get(m.group(1)) if m else None
            if lab:
                items.append(("obj", wrap(f"#context counter(page).at(<{lab}>).first()")))
                f["suppress"] = True
        elif ftype == "TOC" and not self.safe:
            f["toc"] = instr
            f["toc_mark"] = len(items)
            f["toc_items"] = id(items)

    def _field_end(self, items: list[tuple[str, str]], wrap: Any = lambda c: c) -> None:
        if not self.fields:
            return
        f = self.fields.pop()
        if f.get("type") is None:
            instr = "".join(f["instr"]).strip()
            ftype = field_type(instr) if instr else ""
            if ftype == "PAGE" and not self.safe:
                items.append(("obj", wrap(f"#context counter(page).display({typst_string(self.pg_fmt)})")))
            elif ftype == "NUMPAGES" and not self.safe:
                items.append(("obj", wrap(NUMPAGES_CODE)))
            elif ftype == "TOC" and not self.safe:
                items.append(("obj", self._auto_toc(instr)))
        elif f.get("toc") and f.get("toc_items") == id(items) and not any(k == "text" and v.strip() for k, v in items[f.get("toc_mark", 0) :]):
            items.append(("obj", self._auto_toc(f["toc"])))

    def _auto_toc(self, instr: str) -> str:
        """TOC entries with page numbers from this layout, for a TOC field that was never calculated."""
        m = re.search(r'\\o\s+"(\d)-(\d)"', instr)
        depth = int(m.group(2)) if m else 3
        rows = []
        for lvl, eid, text in self.headings:
            if lvl > depth:
                continue
            lab = self.labels.get(eid)
            num = f"#context counter(page).at(<{lab}>).first()" if lab else ""
            rows.append(f"#block(inset: (left: {pt((lvl - 1) * 12)}))[{esc(text)}#box(width: 1fr, repeat[\\.])#h(2pt){num}]")
        return "#linebreak()".join(rows) if rows else ""

    _wrap_width: float | None = None

    def _run_code(self, text: str, props: dict[str, Any], base: dict[str, Any], change: str | None = None) -> str:
        if flag(props, "caps"):
            text = text.upper()
        if self._wrap_width:
            # Like Word, a word wider than its table cell breaks inside the word instead of running over the
            # next cell: zero-width spaces let Typst break it (only in such words).
            limit = self._wrap_width / (0.5 * self._size(props))
            if len(text) > limit:
                text = re.sub(r"\S{%d,}" % max(2, int(limit)), lambda m: "\u200b".join(m.group(0)), text)
        code = esc(text)
        if not code:
            return ""
        args = self._text_args(props, base)
        if flag(props, "smallCaps"):
            code = f"#smallcaps[{code}]"
        va = (props.get("vertAlign") or {}).get("val")
        if va == "superscript":
            code = f"#super(typographic: false)[{code}]"
        elif va == "subscript":
            code = f"#sub(typographic: false)[{code}]"
        u = (props.get("u") or {}).get("val")
        if u and u not in ("none", "0") or change == "ins":
            ucol = self.styles.color({"val": (props.get("u") or {}).get("color")}) if props.get("u") else None
            stroke = f", stroke: {rgb(ucol)}" if ucol else ""
            code = f"#underline(offset: 1.5pt{stroke})[{code}]"
        if flag(props, "strike") or flag(props, "dstrike") or change == "del":
            code = f"#strike[{code}]"
        hl = (props.get("highlight") or {}).get("val")
        fill = HIGHLIGHT.get(hl or "") or self.styles.color(props.get("shd"), "fill")
        if fill:
            code = f"#highlight(fill: {rgb(fill)}, extent: 0.5pt)[{code}]"
        if change in ("ins", "del"):
            # Revisions take the reviewer colour instead of the run's own (one fill argument only).
            args = self._text_args(props, base, fill=rgb(TRACK_COLOR))
        if args:
            code = f"#text({args})[{code}]"
        return code

    def _text_args(self, props: dict[str, Any], base: dict[str, Any] | None, fill: str | None = None) -> str:
        """text() arguments for props (only those that differ from base when base is given); `fill` forces a colour."""
        out = []

        def differs(key: str) -> bool:
            return base is None or (props.get(key) != base.get(key))

        font = self.styles.font_name(props)
        if base is None or font != self.styles.font_name(base):
            out.append(f"font: ({', '.join(typst_string(f) for f in font_stack(font, self.subs))},)")
        if base is None or self._size(props) != self._size(base):
            out.append(f"size: {pt(self._size(props))}")
        b = flag(props, "b")
        if base is None or b != flag(base, "b"):
            out.append(f'weight: "{"bold" if b else "regular"}"')
        i = flag(props, "i")
        if base is None or i != flag(base, "i"):
            out.append(f'style: "{"italic" if i else "normal"}"')
        col = self.styles.color(props.get("color"))
        if fill:
            out.append(f"fill: {fill}")
        elif base is None or col != self.styles.color(base.get("color")):
            out.append(f"fill: {rgb(col) if col else 'black'}")
        if differs("spacing") and props.get("spacing"):
            out.append(f"tracking: {pt(units(props['spacing'].get('val'), 20, 0.0) / 20)}")
        return ", ".join(out)

    def _size(self, rpr: dict[str, Any]) -> float:
        return max(1.0, units((rpr.get("sz") or {}).get("val"), 2, 22.0) / 2)

    # ── notes ───────────────────────────────────────────────────────────
    def _note_body(self, note: Any, part: Any) -> str:
        saved = self.fields
        self.fields = []
        code = self.flow(list(iter_blocks(note)), part=part, story="note")
        self.fields = saved
        return code

    def _note_inline(self, note: Any, part: Any) -> str:
        """A note's paragraphs as inline content (so the number and the text share a line)."""
        saved = self.fields
        self.fields = []
        out = []
        for p in iter_blocks(note):
            if p.tag != P:
                out.append(self.table(p, part=part, story="note"))
                continue
            ppr, base, _ = self.para_props(p, None)
            items = self.inline(p, base, part, "note")
            text = "".join(s for s in self._segments(items, ppr, 0.0) if not s.startswith("\x00")).strip()
            if text:
                out.append(f"#text({self._text_args(base, None)})[{text}]")
        self.fields = saved
        return "#linebreak()".join(out)

    def _footnote(self, nid: str | None) -> str:
        note = self._footnotes.get(nid)
        if note is None or self.safe:
            return ""
        return f"#footnote[{self._note_inline(note, self.fn_part)}]"

    def _endnote(self, nid: str | None) -> str:
        note = self._endnotes.get(nid)
        if note is None:
            return ""
        n = len(self.endnotes) + 1
        from _numbering import to_roman

        mark = to_roman(n).lower()
        self.endnotes.append(f"#block[#super[{mark}] {self._note_inline(note, self.en_part)}]")
        return f"#super[{mark}]"

    # ── drawings ────────────────────────────────────────────────────────
    def _drawing(self, el: Any, items: list[tuple[str, str]], part: Any, story: str) -> None:
        inline = el.find(f"{_WP}inline")
        anchor = el.find(f"{_WP}anchor")
        host = inline if inline is not None else anchor
        if el.tag == PICT or el.tag == OBJECT:
            code = self._vml(el, part, story)
            if code:
                items.append(("obj", code))
            return
        if host is None:
            return
        ext = host.find(f"{_WP}extent")
        try:
            w = int(ext.get("cx")) / EMU if ext is not None else 72.0
            h = int(ext.get("cy")) / EMU if ext is not None else 72.0
        except (TypeError, ValueError):
            w, h = 72.0, 72.0
        content = self._graphic(host, w, h, part, story)
        if not content:
            return
        if anchor is None:
            items.append(("obj", f"#box({content})" if not content.startswith("box(") else "#" + content))
            return
        self._anchor(anchor, content, w, h, items, story)

    def _graphic(self, host: Any, w: float, h: float, part: Any, story: str) -> str:
        """Typst code (a function call without '#') drawing a drawing's graphic at w×h points."""
        blip = host.find(f".//{_A}blip")
        if blip is not None:
            rid = blip.get(_R + "embed") or blip.get(_R + "link")
            src = host.find(f".//{_A}srcRect")
            return self._image(rid, part, w, h, src)
        txbx = host.find(f".//{TXBX}")
        if txbx is not None:
            sppr = host.find(f".//{{{NS['wps']}}}spPr")
            fill, stroke = self._shape_style(sppr)
            body_pr = host.find(f".//{{{NS['wps']}}}bodyPr")
            ins = [7.2, 3.6, 7.2, 3.6]
            if body_pr is not None:
                for i, k in enumerate(("lIns", "tIns", "rIns", "bIns")):
                    if body_pr.get(k):
                        try:
                            ins[i] = int(body_pr.get(k)) / EMU
                        except ValueError:
                            pass
            auto = body_pr is not None and body_pr.find(f"{_A}spAutoFit") is not None
            inner = self.flow([x for x in iter_blocks(txbx)], part=part, story=story, width=w)
            hpart = "" if auto else f", height: {pt(h)}"
            return f"block(width: {pt(w)}{hpart}, inset: (left: {pt(ins[0])}, top: {pt(ins[1])}, right: {pt(ins[2])}, bottom: {pt(ins[3])}), fill: {fill}, stroke: {stroke}, breakable: false)[{inner}]"
        chart = host.find(f".//{{{NS['c']}}}chart")
        if chart is not None:
            from _reader import chart_summary

            label = chart_summary(self.doc, chart.get(_R + "id"), part)
            return self._placeholder(w, h, label)
        dgm = host.find(f".//{{{NS['dgm']}}}relIds")
        if dgm is not None:
            from _reader import diagram_summary

            return self._placeholder(w, h, diagram_summary(self.doc, dgm.get(_R + "dm"), part))
        sppr = host.find(f".//{{{NS['wps']}}}spPr")
        if sppr is not None:
            fill, stroke = self._shape_style(sppr)
            geom = sppr.find(f"{_A}prstGeom")
            kind = geom.get("prst") if geom is not None else "rect"
            if kind in ("line", "straightConnector1"):
                return f"box(width: {pt(w)}, height: {pt(max(h, 1))})[#line(length: {pt(max(w, 1))}, stroke: {stroke if stroke != 'none' else '0.75pt'})]"
            if kind == "ellipse":
                return f"ellipse(width: {pt(w)}, height: {pt(h)}, fill: {fill}, stroke: {stroke})"
            if fill == "none" and stroke == "none":
                return ""
            return f"rect(width: {pt(w)}, height: {pt(h)}, fill: {fill}, stroke: {stroke})"
        return ""

    def _shape_style(self, sppr: Any) -> tuple[str, str]:
        fill, stroke = "none", "none"
        if sppr is None:
            return fill, stroke
        sf = sppr.find(f"{_A}solidFill")
        if sf is not None:
            c = self._drawing_color(sf)
            if c:
                fill = rgb(c)
        ln = sppr.find(f"{_A}ln")
        if ln is not None and ln.find(f"{_A}noFill") is None:
            c = self._drawing_color(ln.find(f"{_A}solidFill")) or "000000"
            try:
                width = int(ln.get("w", "9525")) / EMU
            except ValueError:
                width = 0.75
            stroke = f"{pt(width)} + {rgb(c)}"
        return fill, stroke

    def _drawing_color(self, el: Any) -> str | None:
        if el is None:
            return None
        s = el.find(f"{_A}srgbClr")
        if s is not None and s.get("val"):
            return s.get("val").upper()
        sc = el.find(f"{_A}schemeClr")
        if sc is not None:
            name = sc.get("val")
            name = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}.get(name, name)
            return self.doc.theme["colors"].get(name)
        return None

    def _placeholder(self, w: float, h: float, label: str) -> str:
        return f"block(width: {pt(w)}, height: {pt(h)}, fill: luma(245), stroke: 0.5pt + luma(180), inset: 6pt, clip: true)[#set text(size: 8pt, fill: luma(90)); {esc(label[:300])}]"

    def _image(self, rid: str | None, part: Any, w: float, h: float, src: Any = None) -> str:
        rel = part.rels.get(rid) if rid else None
        if rel is None or rel.is_external:
            return self._placeholder(w, h, "linked image")
        ipart = rel.target_part
        key = str(ipart.partname)
        name = self.images.get(key)
        if name is None:
            name = self._save_image(ipart)
            self.images[key] = name
        if name.startswith("\x00"):
            return self._placeholder(w, h, name[1:])
        crop = None
        if src is not None and any(src.get(k) for k in ("l", "t", "r", "b")):
            try:
                l, t, r, b = (int(src.get(k, "0")) / 100000 for k in ("l", "t", "r", "b"))
                if 0 <= l + r < 0.98 and 0 <= t + b < 0.98:
                    crop = (l, t, r, b)
            except ValueError:
                crop = None
        if crop:
            l, t, r, b = crop
            fw, fh = w / (1 - l - r), h / (1 - t - b)
            return f'box(width: {pt(w)}, height: {pt(h)}, clip: true)[#move(dx: {pt(-l * fw)}, dy: {pt(-t * fh)})[#image("{name}", width: {pt(fw)}, height: {pt(fh)}, fit: "stretch")]]'
        return f'box(image("{name}", width: {pt(w)}, height: {pt(h)}, fit: "stretch"))'

    def _save_image(self, ipart: Any) -> str:
        blob = ipart.blob
        ext = Path(str(ipart.partname)).suffix.lower()
        self._img_n += 1
        base = f"img{self._img_n}"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
            if ext == ".svg" or self._pillow_ok(blob):
                path = self.workdir / (base + ext)
                path.write_bytes(blob)
                return path.name
            return "\x00unreadable image"
        if ext in (".emf", ".wmf", ".emz", ".wmz"):
            return f"\x00{ext[1:].upper()} image (vector format the built-in renderer cannot draw)"
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(blob)) as im:
                im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
                path = self.workdir / (base + ".png")
                im.save(path)
                return path.name
        except Exception:  # noqa: BLE001
            return f"\x00{ext[1:].upper() or 'unknown'} image"

    def _pillow_ok(self, blob: bytes) -> bool:
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(blob)) as im:
                im.verify()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _anchor(self, anchor: Any, content: str, w: float, h: float, items: list[tuple[str, str]], story: str) -> None:
        g = self.geom
        behind = anchor.get("behindDoc") == "1"
        wrap = next((etree.QName(c).localname for c in anchor if etree.QName(c).localname.startswith("wrap")), "wrapNone")
        ph = anchor.find(f"{_WP}positionH")
        pv = anchor.find(f"{_WP}positionV")

        def offset(pos: Any) -> tuple[str, str | None, float]:
            if pos is None:
                return "column", None, 0.0
            rel = pos.get("relativeFrom", "column")
            al = pos.find(f"{_WP}align")
            po = pos.find(f"{_WP}posOffset")
            if al is not None and al.text:
                return rel, al.text.strip(), 0.0
            try:
                return rel, None, int((po.text or "0").strip()) / EMU if po is not None else 0.0
            except ValueError:
                return rel, None, 0.0

        hrel, hal, hoff = offset(ph)
        vrel, val_, voff = offset(pv)
        # horizontal position relative to the text area's left edge
        if hrel == "page":
            area_l, area_w = -g.left, g.w
        elif hrel in ("leftMargin", "insideMargin"):
            area_l, area_w = -g.left, g.left
        elif hrel in ("rightMargin", "outsideMargin"):
            area_l, area_w = g.text_w, g.right
        else:
            area_l, area_w = 0.0, g.text_w
        if hal:
            x = area_l + {"left": 0.0, "inside": 0.0, "center": (area_w - w) / 2, "right": area_w - w, "outside": area_w - w}.get(hal, 0.0)
        else:
            x = area_l + hoff
        in_flow = (wrap in ("wrapTopAndBottom", "wrapSquare", "wrapTight", "wrapThrough")) and not behind and story == "body"
        if in_flow:
            align = "center" if hal == "center" else "right" if hal in ("right", "outside") else "left"
            pad = "" if hal else f"#pad(left: {pt(max(0.0, min(x, g.text_w - w)))})"
            code = f"#align({align})[{pad}[#{content}]]" if pad else f"#align({align})[#{content}]"
            self._pending_anchors.append(f"#block(width: 100%, above: 4pt, below: 4pt)[{code}]")
            return
        if vrel in ("page",):
            y = voff if not val_ else {"top": 0.0, "center": (g.h - h) / 2, "bottom": g.h - h}.get(val_, 0.0)
            y_rel_top = y - (g.top if story == "body" else 0.0 if story == "header" else g.h - g.bottom)
            valign = "top"
        elif vrel in ("margin", "topMargin"):
            base = 0.0 if vrel == "margin" else -g.top
            y = (voff if not val_ else {"top": 0.0, "center": (g.text_h - h) / 2, "bottom": g.text_h - h}.get(val_, 0.0)) + base
            y_rel_top = y if story == "body" else y + g.top if story == "header" else y + g.top - (g.h - g.bottom)
            valign = "top"
        else:
            y_rel_top = voff
            valign = None
        align = f"top + left" if valign else "left"
        placed = f"#place({align}, dx: {pt(x)}, dy: {pt(y_rel_top)})[#{content}]"
        self._pending_anchors.append(placed)

    def _vml(self, el: Any, part: Any, story: str) -> str:
        v = "{" + NS["v"] + "}"
        shape = el.find(f".//{v}shape") or el.find(f".//{v}rect") or el.find(f".//{v}roundrect")
        if shape is None:
            return ""
        style = shape.get("style", "")
        dims = dict(re.findall(r"(width|height):\s*([\d.]+)pt", style))
        w = float(dims.get("width", 72))
        h = float(dims.get("height", 72))
        img = shape.find(f"{v}imagedata")
        if img is not None:
            return "#" + self._image(img.get(_R + "id"), part, w, h)
        tb = shape.find(f".//{TXBX}")
        if tb is not None:
            inner = self.flow(list(iter_blocks(tb)), part=part, story=story, width=w)
            return f"#block(width: {pt(w)}, inset: 4pt, stroke: 0.5pt + luma(120))[{inner}]"
        return ""

    def _borders(self, bdr: dict[str, Any]) -> str:
        sides = []
        for side in ("top", "bottom", "left", "right"):
            s = self._stroke(bdr.get(side))
            if s:
                sides.append(f"{side}: {s}")
        return "(" + ", ".join(sides) + ")" if sides else ""

    def _stroke(self, b: dict[str, Any] | None) -> str | None:
        if not b:
            return None
        val = b.get("val", "single")
        if val in ("nil", "none"):
            return "none"
        try:
            width = max(0.25, units(b.get("sz"), 8, 4.0) / 8)
        except ValueError:
            width = 0.5
        if val in ("double", "thinThickSmallGap", "thickThinSmallGap", "triple"):
            width *= 2
        color = self.styles.color({"val": b.get("color"), "themeColor": b.get("themeColor")}) or "000000"
        dash = ""
        if val in ("dotted", "dotDash", "dotDotDash"):
            dash = ', dash: "dotted"'
        elif val in ("dashed", "dashSmallGap", "dashDotStroked"):
            dash = ', dash: "dashed"'
        return f"(paint: {rgb(color)}, thickness: {pt(width)}{dash})"

    # ── tables ──────────────────────────────────────────────────────────
    def table(self, tbl: Any, part: Any = None, story: str = "body", width: float | None = None) -> str:
        part = part or self.doc.part
        st = self.styles
        tpr_el = tbl.find(TBLPR)
        direct = flatten(tpr_el)
        sid = (direct.get("tblStyle") or {}).get("val") or st.defaults.get("table")
        ts = st.table_style(sid)
        tblpr = merge(ts["tblpr"], direct)
        look = tblpr.get("tblLook") or {}

        def look_on(name: str, bit: int) -> bool:
            if name in look:
                return look[name] in ("1", "true", "on")
            v = look.get("val")
            try:
                return bool(int(v, 16) & bit) if v else name in ("firstRow", "firstColumn")
            except ValueError:
                return False

        first_row, last_row = look_on("firstRow", 0x20), look_on("lastRow", 0x40)
        first_col, last_col = look_on("firstColumn", 0x80), look_on("lastColumn", 0x100)
        band_h = not look_on("noHBand", 0x200)
        band_v = not look_on("noVBand", 0x400)
        grid = table_grid(tbl)
        gcols = [twips(wattr(gc, "w"), 0.0) for gc in tbl.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")]
        ncols = max([len(gcols)] + [sum(c["colspan"] for c in r) + (r[0]["col"] if r else 0) for r in grid] + [1])
        avail = width if width is not None else self.geom.text_w
        if len(gcols) < ncols or sum(gcols) <= 0:
            tw = tblpr.get("tblW") or {}
            total = avail
            if tw.get("type") == "dxa" and tw.get("w"):
                total = twips(tw["w"]) or avail
            elif tw.get("type") == "pct" and tw.get("w"):
                wv = tw["w"]
                total = avail * (float(wv[:-1]) / 100 if wv.endswith("%") else units(wv, 1, 5000) / 5000)
            gcols = [total / ncols] * ncols
        total = sum(gcols)
        limit = avail + self.geom.right * 0.8 if width is None else avail
        if total > limit and total > 0:
            gcols = [c * limit / total for c in gcols]
        mar = tblpr.get("tblCellMar") or {}
        pad_l = twips((mar.get("left") or mar.get("start") or {}).get("w"), 5.4)
        pad_r = twips((mar.get("right") or mar.get("end") or {}).get("w"), 5.4)
        pad_t = twips((mar.get("top") or {}).get("w"), 0.0)
        pad_b = twips((mar.get("bottom") or {}).get("w"), 0.0)
        tb = tblpr.get("tblBorders") or {}
        header_rows = 0
        for tr in tbl.findall(TR):
            trpr = flatten(tr.find(TRPR))
            if "tblHeader" in trpr and flag(trpr, "tblHeader"):
                header_rows += 1
            else:
                break
        nrows = len(grid)
        row_heights = []
        cells_code: list[str] = []
        data_row = 0
        trs = tbl.findall(TR)
        for ri, row in enumerate(grid):
            tr = trs[ri]
            trpr = flatten(tr.find(TRPR))
            hgt = trpr.get("trHeight") or {}
            rh = "auto"
            if hgt.get("val") and hgt.get("hRule") == "exact":
                rh = pt(twips(hgt["val"]))
            row_heights.append(rh)
            is_header = ri < max(1, header_rows) and first_row
            is_last = ri == nrows - 1 and last_row
            if row and row[0]["col"] > 0:
                cells_code.append(f"table.cell(colspan: {row[0]['col']}, stroke: none)[]")
            used = row[0]["col"] if row else 0
            for cell in row:
                if cell["hidden"]:
                    continue
                c0 = cell["col"]
                conds = ["wholeTable"]
                if band_v and not (first_col and c0 == 0):
                    conds.append("band1Vert" if ((c0 - (1 if first_col else 0)) % 2 == 0) else "band2Vert")
                if band_h and not is_header and not is_last:
                    conds.append("band1Horz" if data_row % 2 == 0 else "band2Horz")
                if first_col and c0 == 0:
                    conds.append("firstCol")
                if last_col and c0 + cell["colspan"] >= ncols:
                    conds.append("lastCol")
                if is_header:
                    conds.append("firstRow")
                if is_last:
                    conds.append("lastRow")
                if is_header and first_col and c0 == 0:
                    conds.append("nwCell")
                if is_header and last_col and c0 + cell["colspan"] >= ncols:
                    conds.append("neCell")
                if is_last and first_col and c0 == 0:
                    conds.append("swCell")
                if is_last and last_col and c0 + cell["colspan"] >= ncols:
                    conds.append("seCell")
                tcpr = dict(ts["tcpr"])
                cppr: dict[str, Any] = {}
                crpr: dict[str, Any] = {}
                cborders = dict(tb)
                for cnd in conds:
                    cd = ts["cond"].get(cnd)
                    if cd:
                        tcpr = merge(tcpr, cd.get("tcpr", {}))
                        cppr = merge(cppr, cd.get("ppr", {}))
                        crpr = merge(crpr, cd.get("rpr", {}))
                        if cd.get("tblpr", {}).get("tblBorders"):
                            cborders.update(cd["tblpr"]["tblBorders"])
                tcpr = merge(tcpr, flatten(cell["tc"].find(TCPR)))
                cb = tcpr.get("tcBorders") or {}
                rspan = cell["rowspan"]
                cspan = cell["colspan"]

                def side(name: str, edge: bool, inner: str) -> str | None:
                    if name in cb:
                        return self._stroke(cb[name])
                    alt = {"left": "start", "right": "end"}.get(name)
                    if alt and alt in cb:
                        return self._stroke(cb[alt])
                    key = name if edge else inner
                    b = cborders.get(key) or (cborders.get({"left": "start", "right": "end"}.get(key, "")) if key in ("left", "right") else None)
                    return self._stroke(b)

                strokes = {
                    "top": side("top", ri == 0, "insideH"),
                    "bottom": side("bottom", ri + rspan >= nrows, "insideH"),
                    "left": side("left", c0 == 0, "insideV"),
                    "right": side("right", c0 + cspan >= ncols, "insideV"),
                }
                stroke = ", ".join(f"{k}: {v}" for k, v in strokes.items() if v)
                fill = st.color(tcpr.get("shd"), "fill")
                va = (tcpr.get("vAlign") or {}).get("val", "top")
                valign = {"center": "horizon", "bottom": "bottom"}.get(va, "top")
                cw = sum(gcols[c0 : c0 + cspan]) - pad_l - pad_r
                tmar = tcpr.get("tcMar") or {}
                inset = f"(left: {pt(twips((tmar.get('left') or tmar.get('start') or {}).get('w'), pad_l))}, right: {pt(twips((tmar.get('right') or tmar.get('end') or {}).get('w'), pad_r))}, top: {pt(twips((tmar.get('top') or {}).get('w'), pad_t))}, bottom: {pt(twips((tmar.get('bottom') or {}).get('w'), pad_b))})"
                ctx = {"ppr": merge(ts["ppr"], {}), "rpr": ts["rpr"], "cond_rpr": crpr, "cond_ppr": cppr}
                saved_wrap = self._wrap_width
                self._wrap_width = max(cw, 10.0)
                inner = self.flow(list(iter_blocks(cell["tc"])), part=part, story=story, tbl=ctx, width=max(cw, 10.0))
                self._wrap_width = saved_wrap
                args = [f"colspan: {cspan}"] if cspan > 1 else []
                if rspan > 1:
                    args.append(f"rowspan: {rspan}")
                if fill:
                    args.append(f"fill: {rgb(fill)}")
                args.append(f"stroke: ({stroke})" if stroke else "stroke: none")
                args.append(f"align: {valign}")
                args.append(f"inset: {inset}")
                cells_code.append(f"table.cell({', '.join(args)})[{inner}]")
                used = c0 + cspan
            if used < ncols:
                cells_code.append(f"table.cell(colspan: {ncols - used}, stroke: none)[]")
            if not is_header:
                data_row += 1
        cols = "(" + ", ".join(pt(max(c, 4.0)) for c in gcols[:ncols]) + ("," if ncols == 1 else "") + ")"
        rows = "(" + ", ".join(row_heights) + ("," if len(row_heights) == 1 else "") + ")"
        if header_rows and not self.safe:
            # table.header needs whole rows; count the cells that belong to header rows
            hcount = 0
            for row in grid[:header_rows]:
                hcount += sum(1 for c in row if not c["hidden"]) + (1 if row and row[0]["col"] > 0 else 0)
                used = (row[0]["col"] if row else 0) + sum(c["colspan"] for c in row if not c["hidden"])
                if used < ncols:
                    hcount += 1
            spans = any(c["rowspan"] > 1 for row in grid[:header_rows] for c in row if c["rowspan"] > 1 and grid.index(row) + c["rowspan"] > header_rows)
            if not spans and hcount <= len(cells_code):
                cells_code = [f"table.header(repeat: true, {', '.join(cells_code[:hcount])})"] + cells_code[hcount:]
        code = f"#table(columns: {cols}, rows: {rows}, stroke: none, inset: 0pt, align: left, {', '.join(cells_code)})"
        jc = (tblpr.get("jc") or {}).get("val")
        ind = twips((tblpr.get("tblInd") or {}).get("w"), 0.0) if (tblpr.get("tblInd") or {}).get("type", "dxa") == "dxa" else 0.0
        if jc in ("center", "right", "end"):
            code = f"#align({'center' if jc == 'center' else 'right'})[{code}]"
        elif ind:
            code = f"#pad(left: {pt(ind)})[{code}]"
        return f"#block(width: 100%)[{code}]"


# ── math ────────────────────────────────────────────────────────────────

_M = "{" + NS["m"] + "}"
NARY_T = {"∑": "sum", "∏": "product", "∫": "integral", "∬": "integral.double", "∭": "integral.triple", "∮": "integral.cont", "⋃": "union.big", "⋂": "sect.big"}


def _math_text(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalpha() and ch.isascii():
            out.append(f" {ch} ")
        elif ch.isdigit() or ch in "+=<>.!|":
            out.append(ch)
        elif ch == " ":
            out.append(" ")
        elif ch == "-" or ch == "−":
            out.append(" - ")
        elif ch.isalpha():
            out.append(f" {ch} ")
        else:
            out.append(f'"{ch}"' if ch not in '"\\' else f'"\\{ch}"')
    return "".join(out)


def omml_to_typst(el: Any) -> str:
    """Office Math -> Typst math markup."""

    def kids(e: Any) -> str:
        return " ".join(conv(c) for c in e) if e is not None else ""

    def arg(e: Any, name: str) -> str:
        return kids(e.find(_M + name))

    def grp(s: str) -> str:
        s = s.strip()
        return f"({s})" if s else '""'

    def base(s: str) -> str:
        s = s.strip()
        return s if s else '""'

    def conv(e: Any) -> str:
        if not isinstance(e.tag, str):
            return ""
        q = etree.QName(e)
        tag, ns = q.localname, q.namespace
        if ns != NS["m"]:
            if tag == "r":
                return _math_text("".join(t.text or "" for t in e.iter(T)))
            return ""
        if tag == "r":
            return _math_text("".join(t.text or "" for t in e.iter(_M + "t")))
        if tag in ("e", "num", "den", "sub", "sup", "deg", "lim", "fName", "oMath"):
            return kids(e)
        if tag == "f":
            return f"frac({arg(e, 'num') or chr(34) * 2}, {arg(e, 'den') or chr(34) * 2})"
        if tag == "sSup":
            return f"attach({base(arg(e, 'e'))}, t: {base(arg(e, 'sup'))})"
        if tag == "sSub":
            return f"attach({base(arg(e, 'e'))}, b: {base(arg(e, 'sub'))})"
        if tag == "sSubSup":
            return f"attach({base(arg(e, 'e'))}, t: {base(arg(e, 'sup'))}, b: {base(arg(e, 'sub'))})"
        if tag == "sPre":
            return f"attach({base(arg(e, 'e'))}, tl: {base(arg(e, 'sup'))}, bl: {base(arg(e, 'sub'))})"
        if tag == "rad":
            deg = arg(e, "deg").strip()
            return f"root({deg}, {arg(e, 'e') or chr(34) * 2})" if deg else f"sqrt({arg(e, 'e') or chr(34) * 2})"
        if tag == "nary":
            pr = e.find(_M + "naryPr")
            ch = pr.find(_M + "chr") if pr is not None else None
            sym = ch.get(_M + "val") if ch is not None else "∫"
            op = NARY_T.get(sym, f'"{sym}"')
            sub, sup = arg(e, "sub").strip(), arg(e, "sup").strip()
            s = op + (f"_{grp(sub)}" if sub else "") + (f"^{grp(sup)}" if sup else "")
            return f"{s} {arg(e, 'e')}"
        if tag == "d":
            pr = e.find(_M + "dPr")
            beg, end = "(", ")"
            if pr is not None:
                b = pr.find(_M + "begChr")
                en = pr.find(_M + "endChr")
                beg = b.get(_M + "val", "") if b is not None else beg
                end = en.get(_M + "val", "") if en is not None else end
            inner = ", ".join(kids(x) for x in e.findall(_M + "e"))
            q = lambda c: f'"{c}"' if c else ""  # noqa: E731
            return f"lr({q(beg)} {inner} {q(end)})" if beg or end else inner
        if tag == "func":
            return f'upright("{"".join(t.text or "" for t in e.find(_M + "fName").iter(_M + "t")) if e.find(_M + "fName") is not None else ""}") {arg(e, "e")}'
        if tag == "limLow":
            return f"limits({base(arg(e, 'e'))})_{grp(arg(e, 'lim'))}"
        if tag == "limUpp":
            return f"limits({base(arg(e, 'e'))})^{grp(arg(e, 'lim'))}"
        if tag == "acc":
            return f"hat({arg(e, 'e') or chr(34) * 2})"
        if tag == "bar":
            return f"overline({arg(e, 'e') or chr(34) * 2})"
        if tag == "m":
            rows = ["; ".join(", ".join(kids(c) or '""' for c in mr.findall(_M + "e")) for mr in [r]) for r in e.findall(_M + "mr")]
            return "mat(" + "; ".join(rows) + ")"
        if tag == "eqArr":
            return " \\ ".join(kids(x) for x in e.findall(_M + "e"))
        if tag in ("box", "borderBox", "groupChr", "phant"):
            return arg(e, "e")
        if tag.endswith("Pr"):
            return ""
        return kids(e)

    s = conv(el).strip()
    return s or '""'


# ── entry points ────────────────────────────────────────────────────────


def typst_source(doc: Doc, workdir: Path, changes: str = "markup", safe: bool = False) -> tuple[str, Renderer]:
    r = Renderer(doc, workdir, changes=changes, safe=safe)
    r._pending_anchors = []
    return r.build(), r


def render_pdf(doc: Doc, out_pdf: Path, changes: str = "markup") -> dict[str, Any]:
    """Draws the document to a PDF with Typst. Returns notes (font substitutions, fallbacks)."""
    from _render import typst_compile

    work = Path(tempfile.mkdtemp(prefix="desk-typ-"))
    try:
        notes: list[str] = []
        try:
            src, r = typst_source(doc, work, changes)
            dump = os.environ.get("DESK_TYPST_DUMP")
            if dump:
                Path(dump).write_text(src, encoding="utf-8")
            pdf = typst_compile(src, "pdf", root=work)[0]
        except SkillError as first:
            # Retry in a simpler mode rather than failing on an unusual construct.
            src, r = typst_source(doc, work, changes, safe=True)
            try:
                pdf = typst_compile(src, "pdf", root=work)[0]
            except SkillError:
                raise first from None
            notes.append("rendered in safe mode (simplified tabs, fields and footnotes) after: " + str(first)[:300])
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        out_pdf.write_bytes(pdf)
        if r.subs:
            notes.append("fonts substituted: " + ", ".join(f"{k} → {v}" for k, v in sorted(r.subs.items())))
        return {"notes": notes}
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
