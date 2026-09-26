"""HTML for markup-ebooks: loading with the right encoding, HTML → Markdown, main-content extraction (a local,
readability-style scorer), page metadata, links, images and tables. lxml only; imported lazily by the scripts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from _common import SkillError

# Elements whose content is never text for a reader.
SKIP = {
    "script", "style", "noscript", "template", "head", "title", "meta", "link", "base", "object", "embed", "param",
    "source", "track", "canvas", "map", "area", "input", "select", "option", "optgroup", "textarea", "button",
    "datalist", "svg", "dialog", "frameset", "frame", "portal", "slot",
}
STRIP_FIRST = ("script", "style", "noscript", "template", "svg", "canvas", "object", "embed", "iframe", "select", "textarea", "button")
BLOCK = {
    "address", "article", "aside", "blockquote", "body", "center", "dd", "details", "dir", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup",
    "hr", "html", "li", "main", "menu", "nav", "ol", "p", "pre", "section", "summary", "table", "ul", "iframe",
    "video", "audio", "caption", "tbody", "thead", "tfoot", "tr", "td", "th", "noframes", "search", "listing", "xmp",
    "plaintext",
}
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
HARD_BREAK = "\ue000"
_WS = re.compile(r"[ \t\n\r\f\v\u00a0\u200b]+")
_LANG = re.compile(r"(?:^|\s)(?:language|lang|highlight-source|highlight|brush:?|sourceCode)[-_:]([A-Za-z0-9+#.-]+)", re.I)
_TRACKING = re.compile(r"(?:pixel|tracking|spacer|blank|1x1|beacon|placeholder|transparent|clear)[\w-]*\.(?:gif|png|svg)|/(?:pixel|track|beacon)[/?]", re.I)


# ── loading ─────────────────────────────────────────────────────────────

_ALIASES = {"iso-8859-1": "windows-1252", "latin-1": "windows-1252", "latin1": "windows-1252", "ascii": "windows-1252", "us-ascii": "windows-1252", "x-sjis": "shift_jis", "utf8": "utf-8"}


def sniff_encoding(head: bytes) -> str:
    """The encoding of an HTML/XML byte string: BOM, then <meta charset>/XML declaration, then UTF-8 if it decodes."""
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be"
    probe = head[:16384]
    m = re.search(rb"<meta[^>]{0,200}?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", probe, re.I) or re.search(rb"<\?xml[^>]{0,100}encoding\s*=\s*[\"']([A-Za-z0-9_.:-]+)", probe[:300], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore").lower()
        enc = _ALIASES.get(enc, enc)
        try:
            "".encode(enc)
            return enc
        except LookupError:
            pass
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        if e.start >= len(head) - 4:  # a character cut at the end of the sample
            return "utf-8"
        return "windows-1252"


def _parser(encoding: str | None) -> Any:
    from lxml import html as lhtml

    return lhtml.HTMLParser(encoding=encoding, remove_comments=True, remove_pis=True, huge_tree=True, default_doctype=False)


def load_html(path: str | Path, keep_scripts: bool = False) -> tuple[Any, str]:
    """Parses an HTML file (streamed from disk by libxml2) and returns (root element, encoding)."""
    from lxml import etree
    from lxml import html as lhtml

    p = Path(path)
    with open(p, "rb") as f:
        head = f.read(1 << 20)
    if not head.strip():
        raise SkillError(f"{p.name} is empty")
    enc = sniff_encoding(head)
    parser = _parser(enc)
    with open(p, "rb") as f:
        try:
            tree = lhtml.parse(f, parser)
        except (etree.ParserError, ValueError) as e:
            raise SkillError(f"cannot parse {p.name} as HTML: {e}") from e
    root = tree.getroot()
    if root is None:
        raise SkillError(f"{p.name} has no HTML content")
    _depth_check(parser, root, p.name)
    if not keep_scripts:
        etree.strip_elements(root, *STRIP_FIRST, with_tail=False)
    return root, enc


_DTD_SUBSET = re.compile(r"<!DOCTYPE\b[^\[>]*\[.*?\]\s*>", re.S | re.I)
_DTD_SUBSET_B = re.compile(rb"<!DOCTYPE\b[^\[>]*\[.*?\]\s*>", re.S | re.I)


def parse_html_string(text: str | bytes, keep_scripts: bool = False) -> Any:
    from lxml import etree
    from lxml import html as lhtml

    # An XHTML internal DTD subset (entity declarations, never expanded) would leave a stray "]>" in the text.
    head = text[:65536]
    if ("[" if isinstance(text, str) else b"[") in head:
        rx = _DTD_SUBSET if isinstance(text, str) else _DTD_SUBSET_B
        m = rx.search(text, 0, 1 << 20)
        if m is not None:
            text = text[: m.start()] + text[m.end() :]

    parser = _parser(sniff_encoding(text) if isinstance(text, bytes) else None)
    root = lhtml.document_fromstring(text, parser=parser) if text.strip() else None
    if root is None:
        root = lhtml.document_fromstring("<html><body></body></html>")
    else:
        _depth_check(parser, root, "a document")
    if not keep_scripts:
        etree.strip_elements(root, *STRIP_FIRST, with_tail=False)
    return root


def _depth_check(parser: Any, root: Any, name: str) -> None:
    """libxml2 stops parsing at 2,048 levels of nesting and keeps only what came before: say so, never return a
    silently truncated (or empty) page."""
    if not any("Excessive depth" in e.message for e in parser.error_log):
        return
    if not any(t.strip() for t in root.itertext()):
        raise SkillError(f"{name} nests its elements more than 2,048 deep, where the HTML parser stops, and no text comes before that point: it cannot be read (a malformed or hostile page)")
    print(f"warning: {name} nests its elements more than 2,048 deep; the HTML parser stopped there, so the text after that point is missing", file=sys.stderr)


class no_gc:
    """Pauses the cyclic garbage collector: walking big lxml trees creates millions of short-lived objects, and
    repeated full collections make conversions of 50 MB pages several times slower."""

    def __enter__(self) -> "no_gc":
        import gc

        self.was = gc.isenabled()
        gc.disable()
        return self

    def __exit__(self, *exc: Any) -> None:
        import gc

        if self.was:
            gc.enable()


_TAG_NAMES: dict[Any, str | None] = {}


def tag_of(el: Any) -> str | None:
    t = el.tag
    try:
        return _TAG_NAMES[t]
    except (KeyError, TypeError):
        pass
    if not isinstance(t, str):
        return None
    name = (t.split("}", 1)[1] if t[:1] == "{" else t).lower()
    _TAG_NAMES[t] = name
    return name


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


def text_of(el: Any) -> str:
    return norm_ws("".join(el.itertext()))


def text_len(el: Any) -> int:
    """Length of an element's text, computed by libxml2 (XPath) so it stays cheap on huge subtrees."""
    try:
        return int(el.xpath("string-length(normalize-space(string(.)))"))
    except Exception:  # noqa: BLE001 — elements outside a document
        return len(norm_ws("".join(el.itertext())))


def has_text(el: Any) -> bool:
    return any(t.strip() for t in el.itertext())


def is_hidden(el: Any) -> bool:
    if el.get("hidden") is not None or el.get("aria-hidden") == "true":
        return True
    st = el.get("style")
    if st and re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", st, re.I):
        return True
    return False


# ── HTML → Markdown ─────────────────────────────────────────────────────


def _escape(s: str) -> str:
    """Escapes Markdown syntax in text while keeping it readable."""
    if not s:
        return s
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
    s = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", s)
    s = s.replace("<", "&lt;") if "<" in s and re.search(r"<[A-Za-z/!]", s) else s
    return s


def _escape_line_start(s: str) -> str:
    if re.match(r"(#{1,6}\s|>|[-+*]\s|\d{1,9}[.)]\s|=+\s*$|-+\s*$|~~~|```)", s):
        return "\\" + s
    return s


def _wrap(inner: str, mark: str) -> str:
    if not inner.strip():
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    return f"{lead}{mark}{inner.strip()}{mark}{trail}"


def _code_span(text: str) -> str:
    text = text.replace("\n", " ")
    if not text:
        return ""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _indent(text: str, prefix: str, first: str | None = None) -> str:
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if i == 0 and first is not None:
            out.append(first + ln)
        else:
            out.append((prefix + ln) if ln.strip() else "")
    return "\n".join(out)


class MarkdownWriter:
    """Converts an lxml HTML element to GitHub-flavoured Markdown.

    base: URL that relative links and images resolve against (None keeps them as written).
    images: 'keep' (Markdown images), 'alt' (alt text only) or 'drop'. links: False keeps only link text.
    """

    def __init__(self, base: str | None = None, images: str = "keep", links: bool = True, drop_hidden: bool = True) -> None:
        self.base = base
        self.images = images
        self.links = links
        self.drop_hidden = drop_hidden
        self.image_refs: list[dict[str, Any]] = []
        self._marks: dict[str, int] = {"*": 0, "**": 0, "~~": 0}  # emphasis already open (nested ones add nothing)

    # public
    def convert(self, el: Any) -> str:
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 20000))
        try:
            out: list[str] = []
            with no_gc():
                if tag_of(el) in BLOCK - {"body", "html"} or tag_of(el) in HEADINGS:
                    self.block(el, out)
                else:
                    self.blocks(el, out)
        finally:
            sys.setrecursionlimit(old)
        text = "\n\n".join(b for b in out if b.strip())
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n" if text.strip() else ""

    def url(self, href: str | None) -> str | None:
        if href is None:
            return None
        href = href.strip()
        if not href or href.lower().startswith(("javascript:", "vbscript:")):
            return None
        if self.base and not href.startswith("#"):
            try:
                return urljoin(self.base, href)
            except ValueError:
                return href
        return href

    # blocks
    def blocks(self, el: Any, out: list[str]) -> None:
        buf: list[str] = []
        if el.text:
            buf.append(el.text)
        for ch in el:
            t = tag_of(ch)
            if t is None:
                pass
            elif t in SKIP or (self.drop_hidden and is_hidden(ch)):
                pass
            elif t in BLOCK or t in HEADINGS:
                self._flush(buf, out)
                self.block(ch, out)
            else:
                buf.append(self.inline(ch))
            if ch.tail:
                buf.append(ch.tail)
        self._flush(buf, out)

    def _flush(self, buf: list[str], out: list[str]) -> None:
        if not buf:
            return
        joined = "".join(buf)
        buf.clear()
        for piece in re.split(rf"(?:[ \t\n\r]*{HARD_BREAK}[ \t\n\r]*){{2,}}", joined):
            s = self._clean_inline(piece)
            if s:
                out.append("\n".join(_escape_line_start(ln) for ln in s.split("\n")))

    @staticmethod
    def _clean_inline(s: str) -> str:
        s = _WS.sub(" ", s)
        s = re.sub(rf" *{HARD_BREAK} *", HARD_BREAK, s).strip(" " + HARD_BREAK)
        return s.replace(HARD_BREAK, "\\\n")

    def block(self, el: Any, out: list[str]) -> None:
        t = tag_of(el)
        if t in HEADINGS:
            text = self._clean_inline(self.inner(el)).replace("\\\n", " ")
            if text:
                out.append("#" * int(t[1]) + " " + text)
        elif t in ("ul", "ol", "menu", "dir"):
            s = self.list_block(el, t == "ol")
            if s:
                out.append(s)
        elif t == "li":
            sub: list[str] = []
            self.blocks(el, sub)
            if sub:
                out.append(_indent("\n\n".join(sub), "  ", "- "))
        elif t in ("pre", "listing", "xmp", "plaintext"):
            out.append(self.pre_block(el))
        elif t == "blockquote":
            sub = []
            self.blocks(el, sub)
            if sub:
                out.append("\n".join(("> " + ln) if ln else ">" for ln in "\n\n".join(sub).split("\n")))
        elif t == "table":
            self.table_block(el, out)
        elif t == "hr":
            out.append("---")
        elif t == "dl":
            self.dl_block(el, out)
        elif t in ("dt", "summary"):
            text = self._clean_inline(self.inner(el))
            if text:
                out.append(f"**{text}**")
        elif t == "dd":
            sub = []
            self.blocks(el, sub)
            if sub:
                out.append(_indent("\n\n".join(sub), "    ", ":   "))
        elif t == "figcaption" or t == "caption":
            text = self._clean_inline(self._marked(el, "*"))
            if text:
                out.append(f"*{text}*")
        elif t == "iframe":
            src = self.url(el.get("src"))
            if src and not src.startswith("about:"):
                title = norm_ws(el.get("title") or "") or "embedded content"
                out.append(f"[{_escape(title)}]({src})")
        elif t in ("video", "audio"):
            src = self.url(el.get("src") or next((s.get("src") for s in el.iter("source") if s.get("src")), None))
            if src:
                out.append(f"[{t}: {Path(urlparse(src).path).name or src}]({src})")
        elif t in ("tr", "td", "th", "tbody", "thead", "tfoot"):
            self.blocks(el, out)
        else:
            self.blocks(el, out)

    def list_block(self, el: Any, ordered: bool) -> str:
        try:
            n = int(el.get("start", "1")) if ordered else 1
        except ValueError:
            n = 1
        items: list[list[str]] = []
        for ch in el:
            t = tag_of(ch)
            if t is None or t in SKIP or (self.drop_hidden and is_hidden(ch)):
                continue
            sub: list[str] = []
            if t == "li":
                self.blocks(ch, sub)
                items.append(sub)
            elif t in ("ul", "ol") and items:  # a list nested directly in a list (invalid, common)
                s = self.list_block(ch, t == "ol")
                if s:
                    items[-1].append(s)
            else:
                self.block(ch, sub) if (t in BLOCK or t in HEADINGS) else self._flush([self.inline(ch)], sub)
                if sub:
                    items.append(sub)
        items = [i for i in items if any(b.strip() for b in i)]
        if not items:
            return ""
        loose = any(len(i) > 1 and not all(b.lstrip().startswith(("-", "1.", "2.", "3.")) for b in i[1:]) for i in items)
        rendered = []
        for i, sub in enumerate(items):
            marker = f"{n + i}." if ordered else "-"
            body = ("\n\n" if loose else "\n").join(sub)
            rendered.append(_indent(body, " " * (len(marker) + 1), marker + " "))
        return ("\n\n" if loose else "\n").join(rendered)

    def pre_block(self, el: Any) -> str:
        lang = ""
        anc = [a for _, a in zip(range(2), el.iterancestors())]
        for node in [el, *list(el.iter("code"))[:2], *anc]:
            m = _LANG.search(node.get("class") or "") or _LANG.search(node.get("data-lang") or "")
            if m and m.group(1).lower() not in ("default", "none", "text", "plain", "notranslate", "plaintext", "nohighlight"):
                lang = m.group(1).lower().strip(".")
                break
            if node.get("data-lang"):
                lang = node.get("data-lang").lower()
                break
        text = self.pre_text(el).strip("\n")
        text = "\n".join(ln.rstrip() for ln in text.split("\n"))
        longest = max((len(m) for m in re.findall(r"^`{3,}", text, re.M)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{lang}\n{text}\n{fence}"

    def pre_text(self, el: Any) -> str:
        parts: list[str] = []

        def walk(e: Any) -> None:
            if e.text:
                parts.append(e.text)
            for ch in e:
                t = tag_of(ch)
                if t == "br":
                    parts.append("\n")
                elif t is not None and t not in ("script", "style"):
                    walk(ch)
                if ch.tail:
                    parts.append(ch.tail)

        walk(el)
        return "".join(parts).replace("\r\n", "\n").replace("\t", "    ")

    def dl_block(self, el: Any, out: list[str]) -> None:
        for ch in el:
            t = tag_of(ch)
            if t == "div":  # <dl><div><dt/><dd/></div></dl>
                self.dl_block(ch, out)
            elif t in ("dt", "dd"):
                self.block(ch, out)

    def table_block(self, el: Any, out: list[str]) -> None:
        rows = table_rows(el)
        caption = next((c for c in el if tag_of(c) == "caption"), None)
        cap = self._clean_inline(self.inner(caption)) if caption is not None else ""
        ncols = max((len(r) for r in rows), default=0)
        nested = any(True for _ in el.iter("table") if _ is not el) if len(el) else False
        if not rows or ncols <= 1 or len(rows) <= 1 and ncols <= 2 or nested and not any(r and r[0][1] for r in rows):
            # A layout table: render its cells as blocks.
            if cap:
                out.append(f"*{cap}*")
            for tr in _rows_of(el):
                for cell in tr:
                    if tag_of(cell) in ("td", "th"):
                        self.blocks(cell, out)
            return
        texts = [[self.cell_text(c) for c, _ in r] + [""] * (ncols - len(r)) for r in rows]
        if cap:
            out.append(f"*{cap}*")
        out.append(pipe_table(texts[0], texts[1:]))

    def cell_text(self, cell: Any) -> str:
        if cell is None:
            return ""
        sub: list[str] = []
        self.blocks(cell, sub)
        s = "<br>".join(b.replace("\n", " ") for b in sub)
        return s.replace("|", "\\|")

    # inline
    def inner(self, el: Any) -> str:
        if el is None:
            return ""
        parts = [el.text or ""]
        for ch in el:
            t = tag_of(ch)
            if t is not None and t not in SKIP and not (self.drop_hidden and is_hidden(ch)):
                parts.append(self.inline(ch))
            if ch.tail:
                parts.append(ch.tail)
        return "".join(parts)

    def inline(self, el: Any) -> str:
        t = tag_of(el)
        if t is None or t in SKIP:
            return ""
        if t == "br":
            return HARD_BREAK
        if t == "img":
            return self.image(el)
        if t in ("code", "kbd", "samp", "tt"):
            return _code_span(norm_ws("".join(el.itertext())) if t != "code" else "".join(el.itertext()).replace("\n", " ").strip())
        if t == "a":
            inner = self.inner(el)
            href = self.url(el.get("href"))
            if href and href.startswith("#") and (any(tag_of(x) in HEADINGS or tag_of(x) == "dt" for x in el.iterancestors()) or "headerlink" in (el.get("class") or "")):
                return "" if not re.search(r"\w", inner) else inner  # a heading's own permalink (¶, #, §)
            text = self._clean_inline(inner).replace("\\\n", " ")
            if not self.links or not href:
                return inner
            if not text:
                if "![" in inner:
                    return inner
                return ""
            title = el.get("title")
            tpart = f' "{title.strip()}"' if title and title.strip() and title.strip() != text else ""
            if text == href and re.match(r"https?://", href):
                return f"<{href}>"
            href = href.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            return f"[{text}]({href}{tpart})"
        if t in ("strong", "b"):
            return self._emphasis(el, "**")
        if t in ("em", "i", "cite", "dfn", "var"):
            return self._emphasis(el, "*")
        if t in ("del", "s", "strike"):
            return self._emphasis(el, "~~")
        if t in ("sup", "sub"):
            kids = [c for c in el if isinstance(c.tag, str)]
            if t == "sup" and len(kids) == 1 and tag_of(kids[0]) == "a" and (kids[0].get("href") or "").startswith("#") and not (el.text or "").strip():
                ref = text_of(kids[0])
                if re.fullmatch(r"\[?[\w\s,–-]{1,12}\]?", ref):
                    return ref if ref.startswith("[") else f"[{ref}]"
            inner = self._clean_inline(self.inner(el))
            return f"<{t}>{inner}</{t}>" if inner else ""
        if t == "q":
            return "“" + self.inner(el) + "”"
        if t == "math":
            ann = next((a for a in el.iter() if tag_of(a) == "annotation" and "tex" in (a.get("encoding") or "")), None)
            tex = (ann.text or "").strip() if ann is not None else (el.get("alttext") or "").strip()
            if tex:
                return f"${tex}$" if el.get("display") != "block" else f"$${tex}$$"
            return norm_ws("".join(el.itertext()))
        if t == "input" and (el.get("type") or "").lower() == "checkbox":
            return "[x] " if el.get("checked") is not None else "[ ] "
        if t in BLOCK or t in HEADINGS:
            if t in ("ul", "ol", "table", "pre", "blockquote"):
                sub: list[str] = []
                self.block(el, sub)
                return " " + " ".join(x.replace("\n", " ") for x in sub) + " "
            return " " + self.inner(el) + " "
        return self.inner(el)

    def _marked(self, el: Any, mark: str) -> str:
        """The inner Markdown of an element written inside `mark` (so nested emphasis of that kind is not doubled)."""
        self._marks[mark] += 1
        try:
            return self.inner(el)
        finally:
            self._marks[mark] -= 1

    def _emphasis(self, el: Any, mark: str) -> str:
        if self._marks[mark]:
            return self.inner(el)  # <i> inside <i> (or an italic caption): already italic
        return _wrap(self._marked(el, mark), mark)

    def image(self, el: Any) -> str:
        if self.images == "drop":
            return ""
        src = best_image_src(el)
        alt = norm_ws(el.get("alt") or el.get("title") or "")
        if self.images == "alt":
            return f"[image: {alt}]" if alt else ""
        if not src or is_tracking_pixel(el, src):
            return ""
        if src.startswith("data:"):
            return f"![{_escape(alt)}](data:…)" if alt else ""
        url = self.url(src) or src
        self.image_refs.append({"src": url, "alt": alt})
        return f"![{_escape(alt)}]({url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')})"


def _rows_of(table: Any) -> list[Any]:
    rows = []
    for ch in table:
        t = tag_of(ch)
        if t == "tr":
            rows.append(ch)
        elif t in ("thead", "tbody", "tfoot"):
            rows.extend(r for r in ch if tag_of(r) == "tr")
    return rows


def table_rows(table: Any) -> list[list[tuple[Any, bool]]]:
    """The table as a grid of (cell element or None, is_header), with row and column spans expanded."""
    grid: list[list[tuple[Any, bool]]] = []
    pending: dict[tuple[int, int], tuple[Any, bool]] = {}
    for r, tr in enumerate(_rows_of(table)):
        in_head = tag_of(tr.getparent()) == "thead"
        row: list[tuple[Any, bool]] = []
        c = 0
        cells = [cell for cell in tr if tag_of(cell) in ("td", "th")]
        for cell in cells:
            while (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
            try:
                cs = max(1, min(int(cell.get("colspan", "1") or 1), 100))
            except ValueError:
                cs = 1
            try:
                rs = max(1, min(int(cell.get("rowspan", "1") or 1), 1000))
            except ValueError:
                rs = 1
            is_head = in_head or tag_of(cell) == "th"
            for k in range(cs):
                row.append((cell if k == 0 else None, is_head))
                for dr in range(1, rs):
                    pending[(r + dr, c + k)] = (None, is_head)
            c += cs
        while (r, c) in pending:
            row.append(pending.pop((r, c)))
            c += 1
        grid.append(row)
    return [r for r in grid if r]


def pipe_table(header: list[str], rows: Iterable[list[str]]) -> str:
    head = [h.strip() or " " for h in header]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        cells = [c.strip() for c in r] + [""] * (len(head) - len(r))
        lines.append("| " + " | ".join(cells[: len(head)]) + " |")
    return "\n".join(lines)


def best_image_src(el: Any) -> str | None:
    """The real image URL, following lazy-loading attributes and srcset."""
    src = el.get("src") or ""
    for attr in ("data-src", "data-original", "data-lazy-src", "data-url", "data-hi-res-src", "data-full-src"):
        v = el.get(attr)
        if v and (not src or src.startswith("data:") or "placeholder" in src or "blank" in src):
            src = v
            break
    srcset = el.get("srcset") or el.get("data-srcset")
    if srcset and (not src or src.startswith("data:")):
        best, best_w = None, -1.0
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = 0.0
            if len(bits) > 1:
                m = re.match(r"([\d.]+)([wx])", bits[1])
                if m:
                    w = float(m.group(1)) * (1 if m.group(2) == "w" else 1000)
            if w > best_w:
                best, best_w = bits[0], w
        if best:
            src = best
    if not src and tag_of(el.getparent()) == "picture":
        for s in el.getparent().iter("source"):
            v = s.get("srcset")
            if v:
                src = v.split(",")[0].strip().split()[0]
                break
    return src.strip() or None


def is_tracking_pixel(el: Any, src: str) -> bool:
    w, h = (el.get("width") or "").strip(), (el.get("height") or "").strip()
    if w.isdigit() and h.isdigit() and int(w) < 16 and int(h) < 16:
        return True
    return bool(_TRACKING.search(src))


def html_to_markdown(el: Any, base: str | None = None, images: str = "keep", links: bool = True) -> str:
    return MarkdownWriter(base=base, images=images, links=links).convert(el)


def html_to_text(el: Any) -> str:
    """Readable plain text: block elements on their own lines, list bullets, table cells tab-separated."""
    md = MarkdownWriter(images="alt", links=False).convert(el)
    return markdown_to_text(md)


def markdown_to_text(md: str) -> str:
    out = []
    in_code = False
    for ln in md.split("\n"):
        if ln.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(ln)
            continue
        if re.match(r"^\|[-|: ]+\|$", ln):
            continue
        if ln.startswith("|") and ln.endswith("|"):
            ln = "\t".join(c.strip() for c in ln.strip("|").split(" | "))
        ln = re.sub(r"^#{1,6} ", "", ln)
        ln = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", ln)
        ln = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", ln)
        ln = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", ln)
        ln = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", ln)
        ln = re.sub(r"`([^`]*)`", r"\1", ln)
        ln = ln.replace("<br>", " ").replace("\\\n", "\n")
        ln = re.sub(r"\\([\\`*_\[\]#>~|-])", r"\1", ln)
        if ln.endswith("\\"):
            ln = ln[:-1]
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


# ── metadata ────────────────────────────────────────────────────────────


def _meta(root: Any, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for m in root.iter("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        if key in wanted:
            v = norm_ws(m.get("content") or "")
            if v:
                return v
    return None


def json_ld(root: Any) -> list[dict[str, Any]]:
    """Schema.org objects from <script type="application/ld+json"> (call before scripts are stripped)."""
    out: list[dict[str, Any]] = []
    for s in root.iter("script"):
        if (s.get("type") or "").lower() != "application/ld+json" or not s.text:
            continue
        try:
            data = json.loads(s.text.strip().rstrip(";"))
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if isinstance(d, dict):
                if "@graph" in d and isinstance(d["@graph"], list):
                    stack.extend(d["@graph"])
                out.append(d)
            elif isinstance(d, list):
                stack.extend(d)
    return out


def _ld_name(v: Any) -> str | None:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        return _ld_name(v.get("name"))
    if isinstance(v, list):
        names = [n for n in (_ld_name(x) for x in v) if n]
        return ", ".join(dict.fromkeys(names)) or None
    return None


ARTICLE_TYPES = {"article", "newsarticle", "blogposting", "report", "scholarlyarticle", "techarticle", "webpage", "reportagenewsarticle", "analysisnewsarticle", "opinionnewsarticle", "howto", "recipe", "creativework", "posting", "socialmediaposting", "discussionforumposting", "qapage"}


def page_metadata(root: Any, ld: list[dict[str, Any]] | None = None, url: str | None = None) -> dict[str, Any]:
    ld = ld if ld is not None else json_ld(root)
    art = next((d for d in ld if str(d.get("@type", "")).lower() in ARTICLE_TYPES or (isinstance(d.get("@type"), list) and any(str(t).lower() in ARTICLE_TYPES for t in d["@type"]))), {})
    title_tag = next((text_of(t) for t in root.iter("title")), "")
    h1 = next((text_of(h) for h in root.iter("h1") if text_of(h)), "")
    site = _meta(root, "og:site_name", "application-name") or _ld_name(art.get("publisher"))
    og = _meta(root, "og:title", "twitter:title", "dc.title", "citation_title")
    headline = _ld_name(art.get("headline"))
    if h1 and len(h1) >= 3 and (h1.lower() in title_tag.lower() or (og and h1.lower() in og.lower()) or (headline and h1.lower() == headline.lower())):
        title = h1
    elif og:
        title = clean_title(og, site, "")
    elif headline:
        title = headline
    else:
        title = clean_title(title_tag, site, h1)
    byline = _meta(root, "author", "article:author", "dc.creator", "citation_author", "parsely-author", "sailthru.author") or _ld_name(art.get("author"))
    if byline and re.match(r"https?://", byline):
        byline = None
    if not byline:
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            if el.get("rel") == "author" or el.get("itemprop") == "author" or re.search(r"\b(byline|author)\b", el.get("class") or "", re.I):
                t = text_of(el)
                if 2 < len(t) < 100:
                    byline = re.sub(r"^(by|par|von|por)\s+", "", t, flags=re.I)
                    break
    date = _meta(root, "article:published_time", "datePublished", "date", "dc.date", "dc.date.issued", "dcterms.created", "pubdate", "publish-date", "publish_date", "citation_publication_date", "sailthru.date", "parsely-pub-date") or _ld_name(art.get("datePublished"))
    if not date:
        t = next((x for x in root.iter("time") if x.get("datetime")), None)
        if t is not None:
            date = t.get("datetime")
    modified = _meta(root, "article:modified_time", "dateModified", "last-modified") or _ld_name(art.get("dateModified"))
    canonical = next((norm_ws(l.get("href") or "") for l in root.iter("link") if "canonical" in (l.get("rel") or "").lower().split()), None) or _meta(root, "og:url")
    base_el = next((b.get("href") for b in root.iter("base") if b.get("href")), None)
    html_el = root if tag_of(root) == "html" else root.getroottree().getroot()
    lang = (html_el.get("lang") or html_el.get("xml:lang") or _meta(root, "og:locale", "content-language") or "") or None
    meta = {
        "title": title or None,
        "byline": byline,
        "date": date,
        "modified": modified,
        "site": site,
        "description": _meta(root, "description", "og:description", "twitter:description") or _ld_name(art.get("description")),
        "lang": lang,
        "url": url or canonical,
        "image": _meta(root, "og:image", "twitter:image"),
        "keywords": _meta(root, "keywords", "news_keywords"),
    }
    meta["base"] = urljoin(url or canonical or "", base_el) if base_el else (url or canonical)
    if art.get("articleBody"):
        meta["_ld_body"] = str(art["articleBody"])
    return meta


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def display_date(value: str | None) -> str | None:
    """'2003-01-15T02:34:42Z' → '15 January 2003'; anything else is returned as it is."""
    if not value:
        return None
    m = _ISO_DATE.match(value.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return value.strip() or None
    return f"{int(m.group(3))} {_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def date_text(meta: dict[str, Any]) -> str | None:
    """The date to print under a title: the publication date, plus the update date when it is another day."""
    d, mod = display_date(meta.get("date")), display_date(meta.get("modified"))
    if d and mod and mod != d:
        return f"{d} (updated {mod})"
    return d or None


def byline_text(meta: dict[str, Any]) -> str:
    """'By Ann Lee · 15 January 2003 · Site' for a page's metadata ('' when there is none)."""
    return " · ".join(x for x in (("By " + meta["byline"]) if meta.get("byline") else None, date_text(meta), meta.get("site")) if x)


def clean_title(title: str, site: str | None, h1: str) -> str:
    if not title:
        return h1
    if h1 and h1 in title and len(h1) > 10:
        return h1
    for sep in (" | ", " – ", " — ", " - ", " :: ", " » ", " · "):
        if sep in title:
            parts = [p.strip() for p in title.split(sep)]
            if site:
                parts = [p for p in parts if p.lower() != site.lower()] or parts
            best = max(parts, key=len)
            if len(best.split()) >= 2 or len(parts) == 1:
                return best
    return title


# ── main-content extraction ─────────────────────────────────────────────

UNLIKELY = re.compile(r"-ad-|ai2html|banner|breadcrumb|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|yom-remote|cookie|consent|newsletter|subscribe|promo|sharing|share-|-share|signup|toolbar|masthead|outbrain|taboola|recommend|navbar|topnav|subnav|site-nav|skip-link|modal|overlay", re.I)
MAYBE = re.compile(r"and|article|body|column|content|main|shadow|post|entry|story|prose|markdown", re.I)
POSITIVE = re.compile(r"article|body|content|entry|hentry|h-entry|main|page|post|text|blog|story|prose|markdown|rich-?text|e-content|post-body|article-body", re.I)
NEGATIVE = re.compile(r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|gdpr|masthead|media|meta|outbrain|promo|related|scroll|share|shoutbox|sidebar|skyscraper|sponsor|shopping|tags|widget|cookie|newsletter|subscribe|social|nav|menu|breadcrumb|author-bio|toolbar", re.I)
EDIT_LINKS = re.compile(r"editsection|edit-section|edit-link|mw-jump|noprint|navbox|printfooter|catlinks|mw-authority|skip-?link|visually-?hidden|sr-only|screen-reader", re.I)
DROP_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alertdialog", "menubar", "menu", "toolbar", "tablist"}
SCORE_TAGS = ("p", "pre", "td", "blockquote", "section", "div", "li", "dd")


def _class_weight(el: Any) -> int:
    w = 0
    for attr in (el.get("class"), el.get("id")):
        if attr:
            if NEGATIVE.search(attr):
                w -= 25
            if POSITIVE.search(attr):
                w += 25
    t = tag_of(el)
    if t in ("article", "main") or el.get("itemprop") == "articleBody" or el.get("role") == "main":
        w += 30
    return w


def _base_score(el: Any) -> float:
    t = tag_of(el)
    s = {"div": 5, "article": 10, "main": 8, "section": 3, "pre": 3, "td": 3, "blockquote": 3,
         "address": -3, "ol": -3, "ul": -3, "dl": -3, "dd": -3, "dt": -3, "li": -3, "form": -3,
         "h1": -5, "h2": -5, "h3": -5, "h4": -5, "h5": -5, "h6": -5, "th": -5}.get(t or "", 0)
    return s + _class_weight(el)


def _drop(el: Any) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail and tail.strip():
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _has_block_child(el: Any) -> bool:
    return any(tag_of(c) in BLOCK or tag_of(c) in HEADINGS for c in el)


def link_density(el: Any, total_len: int | None = None) -> float:
    total = total_len if total_len is not None else text_len(el)
    if total == 0:
        return 0.0
    links = 0
    for a in el.iter("a"):
        href = a.get("href") or ""
        k = 0.3 if href.startswith("#") else 1.0
        links += len("".join(a.itertext()).strip()) * k
    return min(1.0, links / total)


def _holds_main(el: Any) -> bool:
    for x in el.iter("main", "article"):
        return True
    return any(True for x in el.iter() if isinstance(x.tag, str) and (x.get("role") == "main" or x.get("itemprop") == "articleBody"))


def _prune(body: Any, strip_unlikely: bool = True) -> None:
    """Removes navigation, hidden elements and (with strip_unlikely) unlikely candidates by role, tag and class/id.

    Nothing that contains <main>, <article>, role=main or itemprop=articleBody is removed."""
    doomed = []
    for el in body.iter():
        t = tag_of(el)
        if t is None or el is body:
            continue
        if is_hidden(el) or (el.get("role") or "").lower() in DROP_ROLES or t in ("nav", "form") and len(text_of(el)) < 400:
            if not _holds_main(el):
                doomed.append(el)
            continue
        if t in ("aside",) or t in ("header", "footer") and not any(tag_of(a) in ("article", "main") for a in el.iterancestors()):
            if not _holds_main(el):
                doomed.append(el)
            continue
        if not strip_unlikely or t in ("html", "body", "a", "article", "main", "table", "tbody", "tr", "td", "th", "code", "pre", "li", "p") or t in HEADINGS:
            continue
        match = f"{el.get('class') or ''} {el.get('id') or ''}"
        if match.strip() and (UNLIKELY.search(match) and not MAYBE.search(match) or EDIT_LINKS.search(match)):
            if not any(tag_of(a) in ("table", "code", "pre") for a in el.iterancestors()) and not _holds_main(el):
                doomed.append(el)
    for el in doomed:
        if el.getparent() is not None:
            _drop(el)


def _mark_data_tables(root: Any) -> set[Any]:
    data = set()
    for t in root.iter("table"):
        if (t.get("role") or "") == "presentation":
            continue
        if t.get("summary") or any(tag_of(c) in ("caption", "thead", "colgroup", "tfoot") for c in t) or any(True for _ in t.iter("th")):
            data.add(t)
            continue
        rows = _rows_of(t)
        cols = max((len([c for c in r if tag_of(c) in ("td", "th")]) for r in rows), default=0)
        if any(True for x in t.iter("table") if x is not t):
            continue
        if len(rows) >= 10 or cols > 4 or len(rows) * cols > 10:
            data.add(t)
    return data


def _clean_conditionally(el: Any, title: str | None, data_tables: set[Any]) -> None:
    """Drops blocks inside the extracted content that look like boilerplate."""
    for tag in ("form", "fieldset", "footer", "nav", "aside"):
        for x in list(el.iter(tag)):
            if x is not el and x.getparent() is not None:
                _drop(x)
    candidates = [x for x in el.iter("table", "ul", "ol", "div", "section") if x is not el]
    for x in reversed(candidates):  # innermost first
        if x.getparent() is None:
            continue
        if x in data_tables or any(a in data_tables for a in x.iterancestors("table")):
            continue
        if any(tag_of(a) in ("pre", "code") for a in x.iterancestors()):
            continue
        tl = text_len(x)
        if tl >= 3000:
            continue
        weight = _class_weight(x)
        if weight < 0 and tl < 1000:
            _drop(x)
            continue
        text = text_of(x)
        tl = len(text)
        if text.count(",") >= 10:
            continue
        p = sum(1 for _ in x.iter("p"))
        img = sum(1 for _ in x.iter("img"))
        li = sum(1 for _ in x.iter("li")) - 100
        inputs = sum(1 for _ in x.iter("input"))
        headings = sum(len(text_of(h)) for h in x.iter(*HEADINGS))
        ld = link_density(x, tl)
        embeds = sum(1 for _ in x.iter("iframe", "video", "audio"))
        is_list = tag_of(x) in ("ul", "ol")
        has_figure = any(True for _ in x.iter("figure")) or any(tag_of(a) == "figure" for a in x.iterancestors())
        bad = (
            (img > 1 and p / img < 0.5 and not has_figure and tl < 200)
            or (not is_list and li > p)
            or (inputs > p / 3 and inputs > 0)
            or (not is_list and tl < 25 and (img == 0 or img > 2) and headings == 0 and not has_figure)
            or (not is_list and weight < 25 and ld > 0.2 and tl < 600)
            or (weight >= 25 and ld > 0.5)
            or ((embeds == 1 and tl < 75) or embeds > 1) and not any(True for _ in x.iter("iframe"))
            or (is_list and ld > 0.8 and tl < 800 and p == 0)
        )
        if bad and tl < 3000:
            _drop(x)
    # A first heading equal to the title is repeated by the header we print.
    if title:
        first = next((h for h in el.iter("h1", "h2") if text_of(h)), None)
        if first is not None and _similar(text_of(first), title):
            _drop(first)
    # Share bars, "read more" and empty wrappers.
    for x in list(el.iter()):
        if x is el or x.getparent() is None or not isinstance(x.tag, str):
            continue
        t = tag_of(x)
        if t in ("div", "section", "p") and not has_text(x) and not any(True for _ in x.iter("img", "iframe", "video", "audio", "picture", "hr", "br", "math", "pre", "table")):
            if not any(tag_of(a) in ("pre", "code") for a in x.iterancestors()):
                _drop(x)


def _similar(a: str, b: str) -> bool:
    a2, b2 = re.sub(r"\W+", " ", a).strip().lower(), re.sub(r"\W+", " ", b).strip().lower()
    return bool(a2) and (a2 == b2 or (len(a2) > 15 and (a2 in b2 or b2 in a2)))


def _prose_section(el: Any) -> bool:
    """A sibling <section> that reads as article prose: a real paragraph and a low link density."""
    tl = text_len(el)
    if tl < 140 or link_density(el, tl) >= 0.3:
        return False
    return any(len(text_of(p)) >= 80 for p in el.iter("p"))


def extract_main(root: Any, title: str | None = None, strip_unlikely: bool = True, clean: bool = True) -> tuple[Any, dict[str, Any]]:
    """Finds the main content element (readability-style). Returns (element, diagnostics). Mutates the tree."""
    from lxml import etree

    body = next(root.iter("body"), None)
    if body is None:
        body = root
    data_tables = _mark_data_tables(body)
    _prune(body, strip_unlikely)
    # Turn divs that hold only inline content into paragraphs for scoring.
    scores: dict[Any, float] = {}

    def add(el: Any, s: float) -> None:
        if el not in scores:
            scores[el] = _base_score(el)
        scores[el] += s

    for el in body.iter(*SCORE_TAGS):
        t = tag_of(el)
        if t in ("div", "section", "li", "dd") and _has_block_child(el):
            continue
        text = "".join(el.itertext()).strip()
        if len(text) < 25:
            continue
        s = 1 + text.count(",") + text.count("，") + min(len(text) // 100, 3)
        anc = el.getparent()
        level = 0
        while anc is not None and level < 5:
            if not isinstance(anc.tag, str):
                break
            div = 1 if level == 0 else 2 if level == 1 else level * 3
            add(anc, s / div)
            if anc is body:
                break
            anc = anc.getparent()
            level += 1
    diag: dict[str, Any] = {"candidates": len(scores)}
    if not scores:
        return body, {**diag, "method": "body"}
    top_raw = sorted(scores.items(), key=lambda kv: -kv[1])[:12]
    adjusted = []
    for el, s in top_raw:
        adjusted.append((s * (1 - link_density(el)), el))
    adjusted.sort(key=lambda x: -x[0])
    top_score, top = adjusted[0]
    # Several good candidates sharing an ancestor: the ancestor is the article.
    alts = [el for s, el in adjusted[1:6] if top_score > 0 and s / top_score >= 0.75]
    if len(alts) >= 2:
        parent = top.getparent()
        while parent is not None and parent is not body and tag_of(parent) != "html":
            if sum(1 for a in alts if parent in set(a.iterancestors())) >= 2:
                top = parent
                break
            parent = parent.getparent()
    # Climb while the candidate is an only child.
    while top is not body and top.getparent() is not None and tag_of(top.getparent()) not in ("body", "html"):
        siblings = [c for c in top.getparent() if isinstance(c.tag, str) and has_text(c)]
        if len(siblings) == 1:
            top = top.getparent()
        else:
            break
    # Gather qualifying siblings.
    threshold = max(10.0, top_score * 0.2)
    parent = top.getparent()
    if parent is None or top is body:
        content = top
    else:
        content = etree.Element("div")
        for sib in list(parent):
            if not isinstance(sib.tag, str):
                continue
            keep = sib is top
            if not keep:
                bonus = top_score * 0.2 if sib.get("class") and sib.get("class") == top.get("class") else 0
                if scores.get(sib, -1e9) + bonus >= threshold and sib in scores:
                    keep = True
                elif tag_of(sib) == "p" or (tag_of(sib) in ("div", "section") and not _has_block_child(sib)):
                    text = text_of(sib)
                    ld = link_density(sib, len(text))
                    if len(text) > 80 and ld < 0.25 or (0 < len(text) <= 80 and ld == 0 and re.search(r"\.( |$)", text)):
                        keep = True
                elif tag_of(sib) in ("figure", "pre", "table", "blockquote", "ul", "ol") and sib.getprevious() is top:
                    keep = True
                elif tag_of(sib) == tag_of(top) == "section" and _prose_section(sib):
                    # Section-per-heading pages (Wikipedia, many docs sites): a lead or short section scores below
                    # the threshold, yet it is part of the article when it holds real paragraphs and few links.
                    keep = True
            if keep:
                tail = sib.tail
                sib.tail = None
                content.append(sib)
                if tail and sib is not top:
                    pass
        if len(content) == 0:
            content = top
    if clean:
        _clean_conditionally(content, title, data_tables)
    diag.update({"method": "score", "top_tag": tag_of(top), "top_class": top.get("class"), "top_score": round(top_score, 1)})
    return content, diag


def _drop_byline(el: Any, byline: str | None) -> None:
    """Removes a 'By …' line inside the content that repeats the byline printed under the title."""
    want = re.sub(r"\W+", " ", byline or "").strip().lower()
    if not want:
        return
    for x in list(el.iter("p", "div", "span", "address", "section")):
        if x is el or x.getparent() is None:
            continue
        t = text_of(x)
        if not t or len(t) > len(byline or "") + 12:
            continue
        t = re.sub(r"^(by|par|von|por)\s+", "", t, flags=re.I)
        if re.sub(r"\W+", " ", t).strip().lower() == want:
            _drop(x)


def extract_article(path: str | Path, url: str | None = None) -> dict[str, Any]:
    """Main content of a saved web page as Markdown plus metadata.

    Like Mozilla's Readability, a result that is too short is retried with gentler pruning, and the longest
    reasonable result wins; JSON-LD articleBody and the whole body are the last resorts."""
    from lxml import etree

    with no_gc():
        return _extract_article(path, url)


def _extract_article(path: str | Path, url: str | None = None) -> dict[str, Any]:
    from lxml import etree

    root, _ = load_html(path, keep_scripts=True)
    ld = json_ld(root)
    etree.strip_elements(root, *STRIP_FIRST, with_tail=False)
    meta = page_metadata(root, ld, url)
    body0 = next(root.iter("body"), root)
    page_words = text_len(body0) // 6
    ld_body = meta.pop("_ld_body", None)
    best: dict[str, Any] | None = None
    for attempt, (unlikely, clean) in enumerate(((True, True), (False, True), (False, False))):
        if attempt:
            root, _ = load_html(path)
        content, diag = extract_main(root, meta.get("title"), unlikely, clean)
        _drop_byline(content, meta.get("byline"))
        writer = MarkdownWriter(base=meta.get("base"))
        md = writer.convert(content)
        words = len(md.split())
        cand = {"md": md, "words": words, "images": writer.image_refs, "diag": {**diag, "attempt": attempt + 1}, "method": diag.get("method")}
        if best is None or words > best["words"] * 1.3:
            best = cand
        if words >= 400 or words >= 0.35 * page_words:
            break
    assert best is not None
    md, method = best["md"], best["method"]
    if best["words"] < 60 and ld_body and len(ld_body) > len(md):
        md = "\n\n".join(_escape(p.strip()) for p in re.split(r"\n\s*\n|\r\n", ld_body) if p.strip()) + "\n"
        method = "json-ld"
    if len(md) < 120 and page_words > 80:
        root2, _ = load_html(path)
        body = next(root2.iter("body"), root2)
        _prune(body, False)
        writer = MarkdownWriter(base=meta.get("base"))
        md = writer.convert(body)
        best["images"] = writer.image_refs
        method = "body"
    words = len(md.split())
    return {**meta, "method": method, "words": words, "page_words": page_words, "reading_minutes": max(1, round(words / 230)) if words else 0, "markdown": md, "images": best["images"], "diagnostics": best["diag"]}


# ── links, images, tables (whole page or a subtree) ─────────────────────


def link_kind(href: str, page_url: str | None) -> str:
    if href.startswith("#"):
        return "anchor"
    scheme = urlparse(href).scheme.lower()
    if scheme in ("mailto", "tel", "sms"):
        return scheme
    if not scheme:
        return "relative"
    if page_url:
        a, b = urlparse(href).netloc.lower(), urlparse(page_url).netloc.lower()
        if a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)):
            return "internal"
    return "external"


def collect_links(el: Any, base: str | None, page_url: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for a in el.iter("a"):
        raw = a.get("href")
        if not raw or raw.strip().lower().startswith(("javascript:", "vbscript:")):
            continue
        href = urljoin(base, raw.strip()) if base and not raw.strip().startswith("#") else raw.strip()
        text = text_of(a) or norm_ws(a.get("title") or a.get("aria-label") or "") or next((norm_ws(i.get("alt") or "") for i in a.iter("img")), "")
        if href in seen:
            out[seen[href]]["count"] += 1
            continue
        seen[href] = len(out)
        item = {"text": text, "url": href, "kind": link_kind(href, page_url or base), "count": 1}
        rel = a.get("rel")
        if rel:
            item["rel"] = rel
        out.append(item)
    return out


def collect_images(el: Any, base: str | None, folder: Path | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for img in el.iter("img"):
        src = best_image_src(img)
        if not src or is_tracking_pixel(img, src):
            continue
        if src.startswith("data:"):
            url = src[:40] + "…"
        else:
            url = urljoin(base, src) if base else src
        if url in seen:
            continue
        seen.add(url)
        item: dict[str, Any] = {"url": url, "alt": norm_ws(img.get("alt") or "")}
        for k in ("width", "height"):
            v = img.get(k)
            if v and v.isdigit():
                item[k] = int(v)
        fig = next((f for f in img.iterancestors("figure")), None)
        if fig is not None:
            cap = next((c for c in fig.iter("figcaption")), None)
            if cap is not None and text_of(cap):
                item["caption"] = text_of(cap)
        if folder is not None and not src.startswith("data:") and not urlparse(src).scheme:
            local = (folder / src.split("?")[0].split("#")[0]).resolve()
            item["local"] = str(local) if local.exists() else None
        out.append(item)
    return out


def collect_tables(el: Any, base: str | None = None) -> list[dict[str, Any]]:
    """Every data table (not layout tables) as {index, caption, heading, header, rows}."""
    tables: list[dict[str, Any]] = []
    writer = MarkdownWriter(base=base, images="alt")
    for i, t in enumerate(x for x in el.iter("table") if not any(True for _ in x.iterancestors("table"))):
        grid = table_rows(t)
        if not grid:
            continue
        ncols = max(len(r) for r in grid)
        texts = [[writer.cell_text(c).replace("<br>", " ").replace("\\|", "|") for c, _ in r] + [""] * (ncols - len(r)) for r in grid]
        header_rows = 0
        for r in grid:
            if r and all(h for _, h in r):
                header_rows += 1
            else:
                break
        cap_el = next((c for c in t if tag_of(c) == "caption"), None)
        heading = None
        prev = t
        for _ in range(200):
            prev = prev.getprevious() if prev.getprevious() is not None else prev.getparent()
            if prev is None or not isinstance(prev.tag, str):
                break
            if tag_of(prev) in HEADINGS:
                heading = text_of(prev)
                break
            h = next((x for x in reversed(list(prev.iter(*HEADINGS)))), None) if tag_of(prev) not in ("body", "html") and prev is not t.getparent() else None
            if h is not None:
                heading = text_of(h)
                break
        header = texts[0] if header_rows or len(texts) > 1 else [f"col{j + 1}" for j in range(ncols)]
        body = texts[max(header_rows, 1):] if (header_rows or len(texts) > 1) else texts
        if header_rows > 1:  # merge stacked header rows ("Group / Sub")
            merged = []
            for j in range(ncols):
                parts = [texts[k][j] for k in range(header_rows) if texts[k][j]]
                merged.append(" / ".join(dict.fromkeys(parts)))
            header = merged
        tables.append({"index": len(tables) + 1, "caption": text_of(cap_el) if cap_el is not None else None, "heading": heading, "rows": len(body), "columns": ncols, "header": header, "data": body, "layout": ncols <= 1})
    return tables


# ── streaming (huge pages) ──────────────────────────────────────────────


def head_metadata(path: str | Path, url: str | None = None) -> dict[str, Any]:
    """Metadata from the first 512 KB only (the <head>), without parsing the whole page."""
    with open(path, "rb") as f:
        head = f.read(512 * 1024)
    cut_at = head.lower().find(b"</head>")
    if cut_at > 0:
        head = head[: cut_at + 7] + b"<body></body></html>"
    root = parse_html_string(head, keep_scripts=True)
    ld = json_ld(root)
    from lxml import etree

    etree.strip_elements(root, *STRIP_FIRST, with_tail=False)
    meta = page_metadata(root, ld, url)
    meta.pop("_ld_body", None)
    return meta


def stream_links_images(path: str | Path, base: str | None, page_url: str | None, links: bool = True, images: bool = True) -> dict[str, Any]:
    """Links and images of a whole page in one streaming pass (memory stays flat on huge pages)."""
    from lxml import etree

    with open(path, "rb") as f:
        enc = sniff_encoding(f.read(1 << 20))
    out_links: list[dict[str, Any]] = []
    seen_links: dict[str, int] = {}
    out_imgs: list[dict[str, Any]] = []
    seen_imgs: set[str] = set()
    elements = 0
    with open(path, "rb") as f:
        ctx = etree.iterparse(f, events=("end",), html=True, huge_tree=True, encoding=enc, remove_comments=True, recover=True)
        for _, el in ctx:
            elements += 1
            t = el.tag if isinstance(el.tag, str) else None
            if t == "a" and links:
                raw = el.get("href")
                if raw and not raw.strip().lower().startswith(("javascript:", "vbscript:")):
                    href = urljoin(base, raw.strip()) if base and not raw.strip().startswith("#") else raw.strip()
                    if href in seen_links:
                        out_links[seen_links[href]]["count"] += 1
                    else:
                        seen_links[href] = len(out_links)
                        text = norm_ws("".join(el.itertext())) or norm_ws(el.get("title") or el.get("aria-label") or "")
                        out_links.append({"text": text, "url": href, "kind": link_kind(href, page_url or base), "count": 1})
            elif t == "img" and images:
                src = best_image_src(el)
                if src and not is_tracking_pixel(el, src):
                    u = (src[:40] + "…") if src.startswith("data:") else (urljoin(base, src) if base else src)
                    if u not in seen_imgs:
                        seen_imgs.add(u)
                        out_imgs.append({"url": u, "alt": norm_ws(el.get("alt") or "")})
            if t in BLOCK and t not in ("html", "body") or t in ("a", "img"):
                # Free what has been read: this element's children and earlier siblings.
                if t not in ("a",) or el.getparent() is not None and tag_of(el.getparent()) not in ("a",):
                    el.clear(keep_tail=True)
                parent = el.getparent()
                if parent is not None and t not in ("a", "img"):
                    while el.getprevious() is not None:
                        del parent[0]
    return {"links": out_links, "images": out_imgs, "elements": elements}
