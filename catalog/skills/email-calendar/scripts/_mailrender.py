"""Renders an email to PNG pages the way a mail client would show it, approximately: a header panel, then the
HTML body (text styles, colours, links, lists, quotes, data tables, multi-column layout tables, inline cid:
images) or the plain-text body. HTML is translated to Typst markup and compiled with the bundled Typst.

No network: remote images are drawn as labelled placeholders. Scripts, styles sheets and hidden preheaders are
dropped. It is an approximation for looking at a message, not a browser.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Any

from _common import SkillError, human_size

PAGE_W, PAGE_H, MARGIN = 540.0, 760.0, 22.0
FONTS = '("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "Liberation Sans", "DejaVu Sans", "Noto Sans", "PingFang SC", "Hiragino Sans", "Microsoft YaHei", "Noto Sans CJK SC", "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", "Libertinus Serif")'
MONO = '("Menlo", "Consolas", "DejaVu Sans Mono", "Courier New")'
MAX_DEPTH = 7

_NAMED = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000", "blue": "0000ff", "gray": "808080", "grey": "808080",
    "silver": "c0c0c0", "maroon": "800000", "navy": "000080", "orange": "ffa500", "purple": "800080", "teal": "008080",
    "yellow": "ffff00", "lightgray": "d3d3d3", "lightgrey": "d3d3d3", "darkgray": "a9a9a9", "darkgrey": "a9a9a9", "whitesmoke": "f5f5f5",
}
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "center", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "hr", "table", "tr", "td", "th", "tbody", "thead", "tfoot", "dl", "dt", "dd", "figure", "figcaption", "address", "form", "fieldset", "body", "html"}
_TYPST_IMG = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def esc(text: str) -> str:
    out = []
    for ch in text:
        if ch in '\\#*_`$<>@[]~/"\'=-+.:;,!?()&%{}^|':
            out.append("\\" + ch if ch not in "()&%{}^|!?,;:" else ch)
        else:
            out.append(ch)
    return "".join(out)


def tstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'


def css_color(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lower().rstrip(";").replace("!important", "").strip()
    if v in _NAMED:
        return _NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        return "".join(c * 2 for c in m.group(1))
    m = re.fullmatch(r"#([0-9a-f]{6})(?:[0-9a-f]{2})?", v)
    if m:
        return m.group(1)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)", v)
    if m:
        if m.group(4) is not None and float(m.group(4)) < 0.2:
            return None
        return "".join(f"{min(255, int(x)):02x}" for x in m.groups()[:3])
    # A shorthand like "url(x.png) #1e1e1c no-repeat": use its colour token.
    m = re.search(r"#[0-9a-f]{6}\b|#[0-9a-f]{3}\b|rgba?\([^)]*\)", v)
    if m and m.group(0) != v:
        return css_color(m.group(0))
    return None


def bg_color(node: Any, st: dict[str, str]) -> str | None:
    return css_color(st.get("background-color")) or css_color(st.get("background")) or css_color(node.get("bgcolor"))


def _luma(hex6: str) -> float:
    r, g, b = (int(hex6[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _dark_background(node: Any) -> bool:
    """Whether the nearest ancestor with a known background colour is dark (remote background images are unknown)."""
    cur = node
    while cur is not None and getattr(cur, "name", None):
        st = style_map(cur)
        bg = bg_color(cur, st)
        if bg:
            return _luma(bg) < 0.55
        cur = cur.parent
    return False


def style_map(tag: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in str(tag.get("style") or "").split(";"):
        k, sep, v = decl.partition(":")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def _px(v: Any) -> float | None:
    if v is None:
        return None
    m = re.match(r"\s*(\d{1,6}(?:\.\d*)?|\.\d+)\s*(px|pt|%)?", str(v))  # the leading number: "1.2.3" once raised in float()
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "%":
        return None
    return n * (1.0 if m.group(2) == "pt" else 0.75)


class Converter:
    """HTML (a cleaned BeautifulSoup tree) → Typst markup."""

    def __init__(self, root: Path, images: dict[str, Path]) -> None:
        self.root, self.images = root, images
        self.n_img = 0
        self.remote = 0

    # inline content -------------------------------------------------------
    def inline(self, node: Any) -> str:
        from bs4 import NavigableString, Tag

        if isinstance(node, NavigableString):
            if type(node).__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction"):
                return ""
            t = re.sub(r"\s+", " ", str(node))
            return esc(t)
        if not isinstance(node, Tag):
            return ""
        name = node.name.lower()
        if name == "br":
            return " #linebreak() "
        if name == "img":
            return self.image(node)
        inner = "".join(self.inline(c) for c in node.children)
        if not inner.strip():
            return inner
        st = style_map(node)
        if name in ("b", "strong") or st.get("font-weight", "") in ("bold", "700", "800", "900"):
            inner = f"#strong[{inner}]"
        if name in ("i", "em", "cite") or st.get("font-style") == "italic":
            inner = f"#emph[{inner}]"
        if name == "u" or "underline" in st.get("text-decoration", ""):
            inner = f"#underline[{inner}]"
        if name in ("s", "strike", "del"):
            inner = f"#strike[{inner}]"
        if name in ("code", "tt", "kbd", "samp"):
            inner = f"#text(font: {MONO}, size: 0.92em)[{inner}]"
        if name == "sup":
            inner = f"#super[{inner}]"
        if name == "sub":
            inner = f"#sub[{inner}]"
        color = css_color(st.get("color") or (node.get("color") if name == "font" else None))
        if color and _luma(color) > 0.8 and not _dark_background(node):
            color = "333333"  # light text meant for a background image we do not load: keep it readable
        size = _px(st.get("font-size"))
        if color or size:
            args = []
            if color:
                args.append(f'fill: rgb("#{color}")')
            if size:
                args.append(f"size: {max(6.0, min(size, 30.0)):.1f}pt")
            inner = f"#text({', '.join(args)})[{inner}]"
        bg = css_color(st.get("background-color") or st.get("background"))
        if bg and bg not in ("ffffff",) and name in ("span", "mark", "font", "a"):
            inner = f'#highlight(fill: rgb("#{bg}"))[{inner}]'
        if name == "mark" and not bg:
            inner = f"#highlight[{inner}]"
        if name == "a" and node.get("href"):
            href = str(node["href"]).strip()
            if href.lower().startswith(("http://", "https://", "mailto:", "tel:")):
                inner = f'#link({tstr(href)})[#text(fill: rgb("#1a5fb4"))[{inner}]]'
        return inner

    def image(self, node: Any) -> str:
        src = str(node.get("src") or "").strip()
        alt = str(node.get("alt") or "").strip()
        st = style_map(node)
        w = _px(node.get("width")) or _px(st.get("width"))
        h = _px(node.get("height")) or _px(st.get("height"))
        path: Path | None = None
        if src.lower().startswith("cid:"):
            path = self.images.get(src[4:].strip("<>"))
        elif src.lower().startswith("attachment:"):
            path = self.images.get(src[11:])
        elif src.lower().startswith("data:image/"):
            path = self._data_uri(src)
        if path is not None and path.exists():
            max_w = PAGE_W - 2 * MARGIN
            width = f"{min(w, max_w):.1f}pt" if w else None
            args = [tstr(path.relative_to(self.root).as_posix())]
            if width:
                args.append(f"width: {width}")
            elif h:
                args.append(f"height: {min(h, 500):.1f}pt")
            elif self._wide(path):
                args.append("width: 100%")
            return f"#box(image({', '.join(a for a in args if a)}, fit: \"contain\"))"
        # Remote or missing image: a placeholder with its label, at its declared size.
        self.remote += 1
        bw = min(w or 110.0, PAGE_W - 2 * MARGIN)
        bh = min(h or 28.0, 180.0)
        label = alt or (src.rsplit("/", 1)[-1][:40] if src else "image")
        return f'#box(width: {bw:.1f}pt, height: {bh:.1f}pt, fill: luma(236), stroke: 0.5pt + luma(200), inset: 3pt, clip: true)[#text(size: 6.5pt, fill: luma(110))[{esc("image: " + label)}]]'

    def _wide(self, path: Path) -> bool:
        try:
            from PIL import Image

            with Image.open(path) as im:
                return im.size[0] * 0.75 > PAGE_W - 2 * MARGIN
        except Exception:  # noqa: BLE001
            return False

    def _data_uri(self, src: str) -> Path | None:
        m = re.match(r"data:image/([a-z0-9.+-]+);base64,(.*)", src, re.I | re.S)
        if not m:
            return None
        # From a list, never from the message: a 300-character or dotted subtype makes no file name
        ext = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "svg+xml": ".svg", "tiff": ".tif"}.get(m.group(1).lower(), ".img")
        try:
            data = base64.b64decode(re.sub(r"\s+", "", m.group(2)))
        except Exception:  # noqa: BLE001
            return None
        self.n_img += 1
        p = self.root / f"data-{self.n_img}{ext}"
        p.write_bytes(data)
        return ensure_typst_image(p)

    # block content --------------------------------------------------------
    def blocks(self, node: Any, depth: int = 0) -> str:
        """Children of a container as Typst blocks: inline runs become paragraphs."""
        from bs4 import NavigableString, Tag

        out: list[str] = []
        run: list[str] = []

        def flush() -> None:
            text = "".join(run).strip()
            run.clear()
            if text and text.replace("#linebreak()", "").strip():
                out.append(text)

        for child in node.children:
            if isinstance(child, Tag) and child.name and child.name.lower() in _BLOCK_TAGS:
                flush()
                b = self.block(child, depth)
                if b.strip():
                    out.append(b)
            elif isinstance(child, (NavigableString, Tag)):
                run.append(self.inline(child))
        flush()
        return "\n\n".join(out)

    def block(self, node: Any, depth: int) -> str:
        name = node.name.lower()
        st = style_map(node)
        if name == "hr":
            return "#line(length: 100%, stroke: 0.5pt + luma(190))"
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            size = {"h1": 18, "h2": 15, "h3": 13, "h4": 11.5, "h5": 10.5, "h6": 10}[name]
            inner = "".join(self.inline(c) for c in node.children).strip()
            color = css_color(st.get("color"))
            if color and _luma(color) > 0.8 and not _dark_background(node):
                color = None
            fill = f', fill: rgb("#{color}")' if color else ""
            return f"#block(above: 1.5em, below: 0.6em)[#text(size: {size}pt, weight: \"bold\"{fill})[{inner}]]" if inner else ""
        if name == "pre":
            return f"#block(fill: luma(246), inset: 6pt, radius: 2pt, width: 100%)[#text(font: {MONO}, size: 8pt)[#raw({tstr(node.get_text())}, block: true)]]"
        if name in ("ul", "ol"):
            items = [self.blocks(li, depth + 1) or "" for li in node.find_all("li", recursive=False)]
            if not items:
                return self.blocks(node, depth + 1)
            fn = "enum" if name == "ol" else "list"
            return f"#{fn}(" + ", ".join(f"[{i}]" for i in items) + ")"
        if name == "blockquote":
            inner = self.blocks(node, depth + 1)
            return f"#block(stroke: (left: 1.5pt + luma(190)), inset: (left: 8pt, y: 2pt), width: 100%)[#text(fill: luma(90))[{inner}]]" if inner.strip() else ""
        if name == "table":
            return self.table(node, depth)
        if name in ("tbody", "thead", "tfoot", "tr", "td", "th"):
            return self.blocks(node, depth + 1)
        if name in ("dl",):
            return self.blocks(node, depth + 1)
        if name == "dt":
            return f"#strong[{''.join(self.inline(c) for c in node.children).strip()}]"
        inner = self.blocks(node, depth + 1)
        if not inner.strip():
            return ""
        return self.decorate(node, st, inner)

    def decorate(self, node: Any, st: dict[str, str], inner: str) -> str:
        align = (st.get("text-align") or str(node.get("align") or "")).lower()
        if node.name.lower() == "center":
            align = "center"
        color = css_color(st.get("color"))
        if color and _luma(color) > 0.8 and not _dark_background(node):
            color = "333333"
        size = _px(st.get("font-size"))
        if color or size:
            args = ([f'fill: rgb("#{color}")'] if color else []) + ([f"size: {max(6.0, min(size, 30.0)):.1f}pt"] if size else [])
            inner = f"#text({', '.join(args)})[{inner}]"
        if align in ("center", "right"):
            inner = f"#align({align})[{inner}]"
        bg = bg_color(node, st)
        border = "border" in st and "none" not in st.get("border", "") and st.get("border", "").strip() not in ("0", "0px")
        if (bg and bg != "ffffff") or border:
            fill = f'fill: rgb("#{bg}"), ' if bg and bg != "ffffff" else ""
            stroke = "stroke: 0.5pt + luma(200), " if border else ""
            inner = f"#block({fill}{stroke}inset: 6pt, width: 100%, radius: 2pt)[{inner}]"
        return inner

    def table(self, t: Any, depth: int) -> str:
        from _mail import _is_layout_table

        rows = [r for r in t.find_all("tr") if r.find_parent("table") is t]
        if not rows:
            return self.blocks(t, depth + 1)
        grid = [[c for c in r.find_all(["td", "th"], recursive=False)] for r in rows]
        ncols = max((sum(int(c.get("colspan") or 1) if str(c.get("colspan") or "1").isdigit() else 1 for c in r) for r in grid), default=1)
        layout = _is_layout_table(t)
        if ncols <= 1 or depth >= MAX_DEPTH:
            parts = []
            for r in grid:
                for c in r:
                    inner = self.blocks(c, depth + 1)
                    if inner.strip():
                        parts.append(self.decorate(c, style_map(c), inner))
            body = "\n\n".join(parts)
            return self.decorate(t, style_map(t), body) if body.strip() else ""
        cells = []
        for r in grid:
            used = 0
            for c in r:
                span = int(c.get("colspan")) if str(c.get("colspan") or "").isdigit() else 1
                span = max(1, min(span, ncols - used))
                content = self.blocks(c, depth + 1)
                st = style_map(c)
                align = (st.get("text-align") or str(c.get("align") or "")).lower()
                if align in ("center", "right"):
                    content = f"#align({align})[{content}]"
                if c.name == "th" and not layout:
                    content = f"#strong[{content}]"
                opts = []
                if span > 1:
                    opts.append(f"colspan: {span}")
                bg = bg_color(c, st)
                if bg and bg != "ffffff":
                    opts.append(f'fill: rgb("#{bg}")')
                elif c.name == "th" and not layout:
                    opts.append("fill: luma(240)")
                fn = "grid.cell" if layout else "table.cell"
                cells.append(f"{fn}({', '.join(opts)})[{content}]" if opts else f"[{content}]")
                used += span
            cells.extend("[]" for _ in range(ncols - used))
        if layout:
            body = f"#grid(columns: ({', '.join(['auto'] * ncols)},), column-gutter: 8pt, row-gutter: 6pt, {', '.join(cells)})"
        else:
            body = f"#table(columns: {ncols}, stroke: 0.5pt + luma(200), inset: 4pt, {', '.join(cells)})"
        return self.decorate(t, style_map(t), body)


def ensure_typst_image(p: Path) -> Path | None:
    """Typst reads PNG, JPEG, GIF, SVG and WebP; other formats are converted to PNG, broken files are dropped."""
    if p.suffix.lower() in _TYPST_IMG:
        head = p.read_bytes()[:16]
        if head.startswith((b"\x89PNG", b"\xff\xd8", b"GIF8", b"RIFF")) or p.suffix.lower() == ".svg":
            return p
    try:
        from PIL import Image

        with Image.open(p) as im:
            im.load()
            out = p.with_suffix(".conv.png")
            (im.convert("RGBA") if im.mode not in ("RGB", "RGBA", "L") else im).save(out)
            return out
    except Exception:  # noqa: BLE001
        return None


def _header_panel(mail: dict[str, Any]) -> str:
    from _mail import fmt_addrs, fmt_date

    rows = [("From", fmt_addrs(mail.get("from") or [])), ("To", fmt_addrs(mail.get("to") or [])), ("Cc", fmt_addrs(mail.get("cc") or [])), ("Date", fmt_date(mail.get("date"), mail.get("date_raw")))]
    cells = []
    for k, v in rows:
        if v:
            cells.append(f"[#text(fill: luma(110))[{esc(k)}]], [{esc(v[:400])}]")
    subj = esc(mail.get("subject") or "(no subject)")
    atts = [a for a in mail.get("attachments") or [] if a.get("disposition") != "inline" and not a.get("alternative")]
    att_line = ""
    if atts:
        names = ", ".join(f"{a['name']} ({human_size(a['size'])})" for a in atts[:12]) + (f", … {len(atts) - 12} more" if len(atts) > 12 else "")
        att_line = f"\n#v(3pt)\n#text(size: 8.5pt)[📎 {esc(names)}]"
    inv = ""
    for c in mail.get("calendar") or []:
        inv += f"\n#v(3pt)\n#block(fill: rgb(\"#e8f0fe\"), inset: 5pt, radius: 2pt, width: 100%)[#text(size: 8.5pt)[#strong[{esc((c.get('method') or 'Calendar').title())}:] {esc(c.get('summary') or '')} — {esc(c.get('start') or '')}{(' → ' + esc(c['end'])) if c.get('end') else ''}{(' · ' + esc(c['location'])) if c.get('location') else ''}]]"
    return (
        f"#block(fill: luma(246), inset: 8pt, radius: 3pt, width: 100%)[\n#text(size: 13pt, weight: \"bold\")[{subj}]\n#v(4pt)\n"
        f"#text(size: 8.5pt)[#grid(columns: (auto, 1fr), column-gutter: 8pt, row-gutter: 3pt, {', '.join(cells)})]{att_line}{inv}\n]\n#v(8pt)\n"
    )


def _plain_body(text: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        lines = para.split("\n")
        quoted = all(ln.startswith(">") for ln in lines if ln.strip())
        body = " #linebreak()\n".join(esc(ln.lstrip("> ") if quoted else ln) for ln in lines)
        if quoted:
            out.append(f"#block(stroke: (left: 1.5pt + luma(190)), inset: (left: 8pt), width: 100%)[#text(fill: luma(90))[{body}]]")
        else:
            out.append(body)
    return "\n\n".join(out)


def build_typst(mail: dict[str, Any], root: Path, prefer_html: bool = True) -> tuple[str, dict[str, Any]]:
    """Typst source for the message; images are written into root."""
    from _mail import clean_html_soup

    images: dict[str, Path] = {}
    for a in mail.get("attachments") or []:
        if a.get("content_type", "").startswith("image/") and a.get("_data"):
            name = re.sub(r"[^A-Za-z0-9._-]", "_", a["name"])[:60] or f"img{a['index']}"
            p = root / f"att-{a['index']}-{name}"
            p.write_bytes(a["_data"])
            fixed = ensure_typst_image(p)
            if fixed is not None:
                if a.get("content_id"):
                    images[a["content_id"]] = fixed
                images[a["name"]] = fixed
    info = {"source": "none", "remote_images": 0}
    body = ""
    conv = Converter(root, images)
    if mail.get("html") and prefer_html:
        soup = clean_html_soup(mail["html"])
        top = soup.body or soup
        body = conv.blocks(top)
        info["source"] = "html"
    elif mail.get("text"):
        body = _plain_body(mail["text"])
        info["source"] = "text"
    info["remote_images"] = conv.remote
    # Inline images the HTML never referenced (or a text body with pictures): show them after the body.
    shown = set()
    if info["source"] == "html":
        for m in re.finditer(r"(?i)src\s*=\s*[\"']?cid:([^\"'\s>]+)", mail.get("html") or ""):
            shown.add(m.group(1))
    extra = [a for a in mail.get("attachments") or [] if a.get("content_type", "").startswith("image/") and (a.get("content_id") not in shown) and (a.get("content_id") in images or a["name"] in images)]
    gallery = ""
    if extra:
        figs = []
        for a in extra[:12]:
            p = images.get(a.get("content_id") or "") or images.get(a["name"])
            if p is not None:
                figs.append(f"[#image({tstr(p.relative_to(root).as_posix())}, width: 100%, fit: \"contain\") #text(size: 7pt, fill: luma(110))[{esc(a['name'])}]]")
        if figs:
            gallery = f"\n\n#line(length: 100%, stroke: 0.5pt + luma(210))\n#grid(columns: (1fr, 1fr), gutter: 8pt, {', '.join(figs)})"
    src = (
        f"#set page(width: {PAGE_W}pt, height: {PAGE_H}pt, margin: {MARGIN}pt, fill: white, footer: context align(right, text(size: 7pt, fill: luma(150))[#counter(page).display() / #counter(page).final().first()]))\n"
        f"#set text(font: {FONTS}, size: 9.5pt, fallback: true)\n#set par(leading: 0.55em, spacing: 0.9em)\n"
        + _header_panel(mail)
        + (body or "#text(fill: luma(120))[(no body)]")
        + gallery
        + "\n"
    )
    return src, info


def render_mail(mail: dict[str, Any], outdir: Path, max_pages: int = 20, force: bool = False, prefix: str = "mail") -> tuple[list[Path], str]:
    """Writes mail-01.png … into outdir; returns the paths and a note for the agent."""
    import shutil

    from _render import VISION_EDGE, typst_compile

    root = Path(tempfile.mkdtemp(prefix="desk-mailrender-"))
    try:
        src, info = build_typst(mail, root)
        ppi = VISION_EDGE * 72.0 / max(PAGE_W, PAGE_H)
        try:
            pages = typst_compile(src, fmt="png", ppi=ppi, root=root)
        except SkillError:
            # A construct the translation got wrong: fall back to the text version of the body.
            from _mail import body_markdown

            text = body_markdown(mail)[0]
            src2 = src.split(_header_panel(mail), 1)[0] + _header_panel(mail) + _plain_body(text) + "\n"
            pages = typst_compile(src2, fmt="png", ppi=ppi, root=root)
            info["source"] += " (fallback to text)"
            src = src2
        if len(pages) == 1:
            # A short message: trim the empty end of the page (smaller image, same scale).
            try:
                pages = typst_compile(src.replace(f"height: {PAGE_H}pt", "height: auto", 1), fmt="png", ppi=ppi, root=root)
            except SkillError:
                pass
        written = []
        for i, png in enumerate(pages[:max_pages], 1):
            out = outdir / f"{prefix}-{i:02d}.png"
            if out.exists() and not force:
                raise SkillError(f"{out} already exists; choose another folder or pass --force")
            out.write_bytes(png)
            written.append(out)
        note = f"Rendered {len(written)} of {len(pages)} page(s) from the {info['source']} body (approximate mail-client view)."
        if len(pages) > max_pages:
            note += f" {len(pages) - max_pages} more page(s) not written."
        if info["remote_images"]:
            note += f" {info['remote_images']} remote image(s) not loaded (drawn as grey boxes)."
        return written, note
    finally:
        shutil.rmtree(root, ignore_errors=True)


def standalone_html(mail: dict[str, Any]) -> str:
    """The HTML body with cid: images inlined as data URIs (or the text body wrapped in <pre>)."""
    import html as htmllib

    by_cid = {a["content_id"]: a for a in mail.get("attachments") or [] if a.get("content_id")}
    html = mail.get("html")
    if not html:
        return f"<!DOCTYPE html><meta charset=\"utf-8\"><title>{htmllib.escape(mail.get('subject') or '')}</title><pre style=\"white-space:pre-wrap;font-family:sans-serif\">{htmllib.escape(mail.get('text') or '')}</pre>\n"

    def sub(m: re.Match[str]) -> str:
        cid = m.group(2)
        a = by_cid.get(cid)
        if a is None or not a.get("_data"):
            return m.group(0)
        return f"{m.group(1)}data:{a['content_type']};base64,{base64.b64encode(a['_data']).decode()}"

    html = re.sub(r"(?i)(src\s*=\s*[\"']?)cid:([^\"'\s>]+)", sub, html)
    if "<meta" not in html.lower()[:2000]:
        html = '<meta charset="utf-8">\n' + html
    return html
