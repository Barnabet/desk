"""Documents for web-research: a response (HTML, PDF, feed, JSON, text) becomes Markdown plus metadata, and is then
read economically: outline, sections, grep, character paging, links, tables and images.

fetch.py, browse.py, archive.py, crawl.py and sources.py share it, so every reader behaves the same way.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _net
from _common import SkillError, UsageError, md_table

CODE_VERSION = "8"
DEFAULT_READ = 30_000
BIG_OUTLINE = 60
VIEW_HINT = "Look at them with view_image."


@dataclass
class Doc:
    url: str
    final_url: str
    kind: str = "html"  # html | pdf | feed | json | text | image | binary
    title: str = ""
    author: str = ""
    author_org: bool = False  # the author is an organisation (JSON-LD says so): never inverted in citations
    published: str = ""
    modified: str = ""
    site: str = ""
    lang: str = ""
    canonical: str = ""
    description: str = ""
    doi: str = ""
    content_type: str = ""
    status: int = 200
    fetched: str = ""
    markdown: str = ""
    extractor: str = ""
    bytes: int = 0
    pages: int = 0
    archived: dict | None = None
    notes: list[str] = field(default_factory=list)
    feeds: list[dict] = field(default_factory=list)
    date_guessed: bool = False
    paywalled: bool = False
    needs_js: bool = False
    noindex: bool = False
    nofollow: bool = False

    @property
    def cite_url(self) -> str:
        return self.url if self.archived else (self.canonical or self.final_url or self.url)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Doc":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _ymd(y: str, mo: int | None = None, d: str | None = None) -> str:
    if not mo or not 1 <= mo <= 12:
        return y
    if d and 1 <= int(d) <= 31:
        return f"{y}-{mo:02d}-{int(d):02d}"
    return f"{y}-{mo:02d}"


def short_date(value: str) -> str:
    """A date as YYYY-MM-DD (or YYYY-MM, YYYY): '2026-09-01T10:00+02:00', '2021/12/01', '02 Feb 1999', 'February 2,
    1999', 'Mon, 01 Sep 2026 …' and '20260901' all work; anything else is kept as it is."""
    v = (value or "").strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?!\d)", v)
    if m:
        return _ymd(m.group(1), int(m.group(2)), m.group(3))
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?!\d)", v)
    if m:
        return _ymd(m.group(1), int(m.group(2)), m.group(3))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", v)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _ymd(m.group(3), _MONTHS[m.group(2)[:3].lower()], m.group(1))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", v)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _ymd(m.group(3), _MONTHS[m.group(1)[:3].lower()], m.group(2))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b", v)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _ymd(m.group(2), _MONTHS[m.group(1)[:3].lower()])
    m = re.fullmatch(r"(\d{4})", v)
    if m:
        return m.group(1)
    return v[:40]


# ── sniffing and extraction ─────────────────────────────────────────────


def sniff(body: bytes, content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    head = body[:2048].lstrip()
    low = head[:600].lower()
    if head.startswith(b"%PDF") or ct == "application/pdf":
        return "pdf"
    if ct.startswith("image/") or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff" or head[:6] in (b"GIF87a", b"GIF89a") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if any(k in ct for k in ("rss", "atom")) or re.search(rb"<(rss|rdf:rdf)[\s>]", low) or (b"<feed" in low and b"http://www.w3.org/2005/atom" in body[:3000].lower()):
        return "feed"
    if ct in ("application/json", "application/ld+json", "text/json") or ct.endswith("+json"):
        if b'"https://jsonfeed.org/version' in body[:400]:
            return "feed"
        return "json"
    if ct in ("text/html", "application/xhtml+xml") or low.startswith((b"<!doctype html", b"<html")) or b"<body" in body[:5000].lower():
        return "html"
    if ct.startswith("text/") or ct in ("application/xml", "text/xml", "application/javascript", "application/x-yaml") or not ct:
        try:
            body[:4096].decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            return "text" if ct.startswith("text/") else "binary"
    return "binary"


def extract(body: bytes, content_type: str, url: str, final_url: str | None = None, *, full: bool = False, inline_links: bool = False) -> Doc:
    """Bytes of a response → a Doc with Markdown and metadata."""
    final_url = final_url or url
    kind = sniff(body, content_type, final_url)
    doc = Doc(url=url, final_url=final_url, kind=kind, content_type=content_type.split(";")[0].strip(), bytes=len(body))
    if kind == "html":
        _html_doc(doc, _net.decode(body, content_type), full, inline_links)
        doc.doi = doc.doi or doi_of(url) or doi_of(final_url)
    elif kind == "pdf":
        _pdf_doc(doc, body)
    elif kind == "feed":
        _feed_doc(doc, body)
    elif kind == "json":
        _json_doc(doc, body)
    elif kind == "text":
        text = _net.decode(body, content_type)
        doc.markdown = text
        doc.extractor = "text"
        first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
        doc.title = first[:120]
    elif kind == "image":
        doc.extractor = "image"
        doc.markdown = _image_summary(body)
        doc.notes.append("This is an image: save it with --save DIR (or --images DIR) and look at it with view_image.")
    else:
        doc.extractor = "none"
        doc.markdown = f"Binary content ({doc.content_type or 'unknown type'}, {len(body):,} bytes)."
        doc.notes.append("Binary file: save it with --save DIR and open it with the matching file skill.")
    return doc


def _image_summary(body: bytes) -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(body)) as im:
            return f"Image: {im.format} {im.width}×{im.height}, {len(body):,} bytes."
    except Exception:  # noqa: BLE001 — any unreadable image just gets a size line
        return f"Image, {len(body):,} bytes."


def _html_doc(doc: Doc, html: str, full: bool, inline_links: bool) -> None:
    import _html

    html = _html.decode_cf_emails(html)
    tree = _html.parse(html)
    meta = _html.page_meta(tree, doc.final_url)
    for k in ("title", "author", "author_org", "published", "modified", "site", "lang", "canonical", "description", "doi", "feeds", "paywalled", "noindex", "nofollow"):
        setattr(doc, k, meta.get(k) or getattr(doc, k))
    md = ""
    if full:
        md = _html.to_markdown(tree.body or tree.root, doc.final_url, inline_links, drop_boilerplate=False)
        doc.extractor = "full"
    else:
        md = _trafilatura(html, doc, inline_links)
        main = _html.main_node(tree)
        visible = _html.visible_text_len(tree)
        main_text = _html.visible_text_len(main) if main is not None else 0
        # trafilatura's main-content extraction sometimes drops what matters most on docs and product pages: code
        # blocks (a lone one, or those nested in lists), headings, price cards. The structural rendering of the main
        # element keeps them, so it is compared with trafilatura's output and used when trafilatura lost something.
        alt = _html.to_markdown(main, doc.final_url, inline_links) if main is not None else ""
        flat = _flat(md)
        pres = [p for p in (main.css("pre") if main is not None else []) if len(_html.text_of(p)) >= 3]
        lost_code = bool(pres) and md.count("```") // 2 < len(pres) * 0.7
        heads = _html.headings(main) if main is not None else []
        missing_heads = [h for h in heads if _flat(h) not in flat]
        lost_heads = len(heads) >= 3 and len(missing_heads) >= max(2, len(heads) * 0.2)
        figures = {re.sub(r"\s+", "", f) for f in MONEY.findall(_html.text_outside_links(main))}
        packed = flat.replace(" ", "")
        missing_figures = sorted(f for f in figures if f.casefold() not in packed)
        lost_figures = bool(missing_figures) and len(missing_figures) >= len(figures) * 0.3
        thin = not md or (len(md) < 250 and visible > 1200) or (len(md) < visible * 0.15 and visible > 4000)
        lost = lost_code or lost_heads or lost_figures
        if thin or lost:
            if alt and len(alt) > len(md or "") * (0.6 if lost else 1.0):
                md = alt
                doc.extractor = "structure"
            elif lost:
                what = [f"{len(missing_heads)} headings (e.g. '{missing_heads[0][:40]}')" if lost_heads else "", f"prices such as {', '.join(missing_figures[:3])}" if lost_figures else "", "code blocks" if lost_code else ""]
                doc.notes.append(f"The main-content extraction left out {' and '.join(w for w in what if w)} shown on the page: read it again with --full.")
        if not md:
            md = _html.to_markdown(tree.body or tree.root, doc.final_url, inline_links, drop_boilerplate=False)
            doc.extractor = "full"
        elif doc.extractor == "trafilatura" and main_text > 2500 and len(md) < main_text * 0.45:
            doc.notes.append(f"The main-content extraction kept {_k(len(md))} of the {_k(main_text)} on the page: if something seems missing (prices, cards, tables, code), read it again with --full.")
    doc.markdown = tidy_markdown(md)
    if not doc.title:
        m = re.search(r"^#\s+(.+)$", doc.markdown, re.M)
        doc.title = m.group(1).strip() if m else ""
    if doc.title and doc.markdown and not re.match(r"#\s", doc.markdown):
        doc.markdown = f"# {doc.title}\n\n{doc.markdown}"
    doc.needs_js = _html.needs_js(html, len(doc.markdown))
    if doc.needs_js:
        doc.notes.append("This page seems to need JavaScript to show its content: try browse.py render URL.")
    elif len(doc.markdown) < 80:
        doc.notes.append("The page has almost no readable text: it may be drawn by JavaScript (browse.py render URL) or be an interstitial (archive.py read URL shows an archived copy).")
    if doc.paywalled:
        doc.notes.append("The page marks itself as paywalled: only the free part is shown. Don't try to get around it; look for another source.")
    if _net.domain(doc.final_url) in ("youtube.com", "youtu.be", "m.youtube.com"):
        doc.notes.append("For what the video says, read its transcript: video.py captions URL (video.py info for chapters).")
    if _net.host(doc.final_url) == "news.google.com":
        doc.notes.append("Google News links redirect with JavaScript (and, in some regions, through a consent page Desk leaves alone): search the headline with --site OUTLET to get the direct link.")


MONEY = re.compile(r"(?:US\$|[$€£¥])\s?\d+(?:[.,]\d+)*")


def _flat(text: str) -> str:
    """Text without Markdown marks, case or spacing, for 'is this on the page' comparisons."""
    return " ".join(re.sub(r"[*`#|>\\]+", " ", text or "").casefold().split())


PERMALINK = re.compile(r"(?<![ \t])[ \t]*[¶🔗][ \t]*$|(?<=\S)¶", re.M)  # starts only where a blank run does: linear
CITE_SUP = re.compile(r"<sup>\s*\[(?:\d{1,4}|[a-z]{1,2}|(?:note|nb|n)\s*\d{1,3})\]\s*</sup>", re.I)
SUP_SUB = re.compile(r"<(sup|sub)>\s*([^<>]{1,40}?)\s*</\1>", re.I)
EDIT_LINK = re.compile(r"^(#{1,6}\s.*?)\s*\[\s*edit(?: source)?\s*\]\s*$", re.I | re.M)


def tidy_markdown(md: str) -> str:
    """Drops what costs tokens without informing: permalink signs, Wikipedia citation markers and [edit] links, raw
    <sup>/<sub> tags (x^2, CO2), and blockquotes that only wrap a code block."""
    md = PERMALINK.sub("", md)
    md = CITE_SUP.sub("", md)
    md = SUP_SUB.sub(lambda m: ("^" if m.group(1).lower() == "sup" and re.fullmatch(r"[-+−]?\d{1,3}|n", m.group(2)) else "") + m.group(2), md)
    md = EDIT_LINK.sub(r"\1", md)
    if "> ```" in md or ">```" in md:
        md = unquote_code(md)
    return md.strip()


def unquote_code(md: str) -> str:
    """'> ```…> ```' (a quoted example block, as nginx's docs have) → a plain fenced block."""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(">"):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].startswith(">"):
            j += 1
        body = "\n".join(re.sub(r"^> ?", "", ln) for ln in lines[i:j]).strip()
        out.extend(body.split("\n") if body.startswith("```") and body.endswith("```") and body.count("```") == 2 else lines[i:j])
        i = j
    return "\n".join(out)


class _LazyDateDataParser:
    """Stands in for dateparser.DateDataParser while trafilatura imports (htmldate builds one at import time, which
    costs about 0.15 s per run); the real parser loads on first use, which is rare."""

    def __init__(self, *args, **kwargs):
        self._args, self._kwargs, self._real = args, kwargs, None

    def __getattr__(self, name: str):
        if self._real is None:
            import sys

            if getattr(sys.modules.get("dateparser"), "__desk_stub__", False):
                del sys.modules["dateparser"]
            import dateparser

            self._real = dateparser.DateDataParser(*self._args, **self._kwargs)
        return getattr(self._real, name)


def import_trafilatura():
    import sys

    if "trafilatura" not in sys.modules and "dateparser" not in sys.modules:
        import types

        stub = types.ModuleType("dateparser")
        stub.DateDataParser = _LazyDateDataParser  # type: ignore[attr-defined]
        stub.__desk_stub__ = True  # type: ignore[attr-defined]
        sys.modules["dateparser"] = stub
        try:
            import trafilatura
        finally:
            if getattr(sys.modules.get("dateparser"), "__desk_stub__", False):
                del sys.modules["dateparser"]
        return trafilatura
    import trafilatura

    return trafilatura


def _trafilatura(html: str, doc: Doc, inline_links: bool) -> str:
    try:
        trafilatura = import_trafilatura()
    except ImportError:
        return ""
    try:
        md = trafilatura.extract(
            html,
            url=doc.final_url,
            output_format="markdown",
            include_tables=True,
            include_links=inline_links,
            include_images=False,
            include_formatting=True,
            include_comments=False,
            favor_recall=True,
            deduplicate=False,
            with_metadata=False,
        )
    except Exception:  # noqa: BLE001 — extractor bugs on odd markup must not kill the read
        md = None
    doc.extractor = "trafilatura"
    if not (doc.author and doc.published and doc.site):
        try:
            meta = trafilatura.extract_metadata(html, default_url=doc.final_url)
        except Exception:  # noqa: BLE001
            meta = None
        if meta is not None:
            import _html

            doc.author = doc.author or _html.plausible_author(meta.author or "")
            if not doc.published and meta.date:
                doc.published = meta.date
                doc.date_guessed = True
            doc.site = doc.site or _html.plausible_site(meta.sitename or "", doc.final_url)
            doc.title = doc.title or (meta.title or "")
    return (md or "").strip()


def _pdf_doc(doc: Doc, body: bytes) -> None:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise SkillError("pypdfium2 is not installed") from e
    try:
        pdf = pdfium.PdfDocument(body)
    except Exception as e:  # noqa: BLE001
        doc.markdown = f"A PDF that could not be opened ({e})."
        doc.extractor = "pdfium"
        return
    parts = []
    try:
        meta = pdf.get_metadata_dict() or {}
        doc.title = (meta.get("Title") or "").strip()
        doc.author = (meta.get("Author") or "").strip()
        doc.published = _pdf_date(meta.get("CreationDate") or "")
        doc.pages = len(pdf)
        empty = 0
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            text = (tp.get_text_bounded() or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            tp.close()
            page.close()
            if not text:
                empty += 1
            parts.append(f"## Page {i + 1}\n\n{text or '[no text layer on this page]'}")
    finally:
        pdf.close()
    if not doc.title:
        doc.title = _pdf_title(parts[0] if parts else "")
    if doc.published:
        doc.date_guessed = True  # the PDF's creation date, not necessarily its publication date
    doc.markdown = "\n\n".join(parts)
    doc.extractor = "pdfium"
    if doc.pages and empty == doc.pages:
        doc.notes.append("This PDF has no text layer (scanned): save it with --save DIR and render its pages with pdf-toolkit, then view_image.")
    doc.notes.append("PDF text via pypdfium2; for tables, layout and page renders use the pdf-toolkit skill on a saved copy (--save DIR).")


def _pdf_title(page_md: str) -> str:
    """The first line of page 1 that reads like a title (not a page number, an arXiv stamp or a running header)."""
    for ln in page_md.splitlines()[2:40]:
        t = ln.strip()
        if len(t) < 8 or len(t.split()) < 2 or re.match(r"^(arXiv:|doi:|https?://|page \d|\d+$|preprint|vol\.|volume )", t, re.I):
            continue
        return t[:160]
    return ""


def _pdf_date(v: str) -> str:
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", v or "")
    if not m:
        return ""
    return "-".join(x for x in m.groups() if x)


def _feed_doc(doc: Doc, body: bytes) -> None:
    feed = parse_feed(body, doc.final_url)
    doc.title = feed["title"]
    doc.description = feed.get("description", "")
    doc.extractor = "feedparser"
    lines = [f"# {feed['title'] or 'Feed'}", ""]
    if feed.get("description"):
        lines += [feed["description"], ""]
    for it in feed["items"]:
        lines.append(f"## {it['title'] or '(untitled)'}")
        bits = [b for b in (it.get("date", "")[:10], it.get("author", ""), it.get("link", "")) if b]
        if bits:
            lines.append(" · ".join(bits))
        if it.get("summary"):
            lines += ["", it["summary"]]
        lines.append("")
    doc.markdown = "\n".join(lines).strip()
    doc.notes.append("This is a feed: feeds.py read/watch lists its items with dates and tracks new ones.")


def parse_feed(body: bytes | str, url: str = "") -> dict:
    """RSS, Atom, RDF or JSON Feed → {title, link, description, items: [{id, title, link, date, author, summary}]}."""
    import feedparser

    if isinstance(body, bytes) and b'"https://jsonfeed.org/version' in body[:400]:
        d = json.loads(body.decode("utf-8", "replace"))
        items = []
        for it in d.get("items", []):
            items.append({
                "id": str(it.get("id") or it.get("url") or ""),
                "title": (it.get("title") or "").strip(),
                "link": it.get("url") or it.get("external_url") or "",
                "date": it.get("date_published") or it.get("date_modified") or "",
                "author": ", ".join(a.get("name", "") for a in (it.get("authors") or ([it["author"]] if it.get("author") else [])) if isinstance(a, dict)),
                "summary": _summary(it.get("summary") or it.get("content_text") or it.get("content_html") or ""),
            })
        return {"title": d.get("title", ""), "link": d.get("home_page_url", ""), "description": d.get("description", ""), "items": items, "format": "json feed"}
    f = feedparser.parse(body)
    if f.bozo and not f.entries and not f.feed:
        raise SkillError(f"{url or 'input'} is not a readable feed ({getattr(f, 'bozo_exception', 'parse error')})")
    items = []
    for e in f.entries:
        date = ""
        for k in ("published_parsed", "updated_parsed", "created_parsed"):
            t = e.get(k)
            if t:
                date = time.strftime("%Y-%m-%dT%H:%M:%SZ", t)
                break
        items.append({
            "id": str(e.get("id") or e.get("link") or e.get("title") or ""),
            "title": _clean_text(e.get("title", "")),
            "link": e.get("link", ""),
            "date": date or e.get("published", "") or e.get("updated", ""),
            "author": e.get("author", ""),
            "summary": _summary(e.get("summary", "") or (e.get("content") or [{}])[0].get("value", "")),
        })
    feed = f.feed
    return {"title": _clean_text(feed.get("title", "")), "link": feed.get("link", ""), "description": _summary(feed.get("subtitle", "") or feed.get("description", "")), "items": items, "format": f.version or "feed"}


def _md_rule(line: str) -> bool:
    """A Markdown table rule line like |---|:--:|. Not re.fullmatch(r"\\s*\\|[\\s:|-]+\\|\\s*"), which is quadratic on a long line."""
    t = line.strip()
    return len(t) >= 3 and t[0] == "|" == t[-1] and all(c in ":|-" or c.isspace() for c in t[1:-1])


def _clean_text(s: str) -> str:
    import html as htmllib

    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^<>]+>", " ", s or ""))).strip()


def _summary(s: str, limit: int = 400) -> str:
    t = _clean_text(s)
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def _json_doc(doc: Doc, body: bytes) -> None:
    doc.extractor = "json"
    data: Any = None
    try:
        data = json.loads(body.decode("utf-8-sig", "replace"))
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        text = body.decode("utf-8", "replace")
    doc.markdown = f"```json\n{text}\n```"
    if isinstance(data, dict):
        doc.title = str(data.get("title") or data.get("name") or "")[:120]


# ── loading with the archive fallback ───────────────────────────────────

ARCHIVE_STATUSES = {403, 404, 410, 451, 429}
REASONS = {403: "403 Forbidden", 404: "404 Not Found", 410: "410 Gone", 451: "451 Unavailable For Legal Reasons", 429: "429 Too Many Requests"}


@dataclass
class Loaded:
    doc: Doc
    body: bytes | None
    content_type: str
    response: Any = None


def load(url: str, *, refresh: bool = False, archive: bool = True, full: bool = False, inline_links: bool = False, timeout: float = 25.0, need_body: bool = False) -> Loaded:
    """Fetches and extracts one URL (or reads a local file). When the live page is gone, blocked or unreachable, it
    tries, in order: the official API copy (PubMed, PMC), the open-access copy of a DOI (OpenAlex), then the nearest
    Wayback snapshot (of the page a DOI leads to first), and labels the result. Extracted documents are cached for a
    day next to the responses, so --outline, then --section, then --grep cost one fetch."""
    local = Path(url).expanduser()
    if "://" not in url and local.is_file():
        body = local.read_bytes()
        doc = extract(body, _guess_type(local), str(local), str(local), full=full, inline_links=inline_links)
        doc.fetched = iso(local.stat().st_mtime)
        return Loaded(doc, body, _guess_type(local))
    url = _net.normalize_input_url(url)
    memo_key = [CODE_VERSION, url, full, inline_links]
    memo = None if refresh else _net.memo_get("doc", memo_key)
    if memo and not need_body:
        return Loaded(Doc.from_dict(memo), None, memo.get("content_type", ""))
    reason = ""
    final = url
    try:
        r = _net.request(url, kind="page", cache=not refresh, timeout=timeout)
        final = r.final_url or url
        wall = _net.consent_wall(r)
        if wall:
            raise SkillError(consent_message(url, wall))
        blocked = r.blocked()
        if r.ok and not blocked:
            if memo and r.from_cache:
                doc = Doc.from_dict(memo)
            else:
                doc = extract(r.body, r.headers.get("content-type", ""), url, r.final_url, full=full, inline_links=inline_links)
                doc.status = r.status
                doc.fetched = iso(r.fetched_at)
                if r.truncated:
                    doc.notes.append(f"The response was cut at {_net.MAX_BYTES // (1024 * 1024)} MB.")
                _net.memo_put("doc", memo_key, doc.to_dict(), _net.PAGE_TTL)
            return Loaded(doc, r.body, r.headers.get("content-type", ""), r)
        if blocked:
            reason = blocked
        elif r.status in ARCHIVE_STATUSES:
            reason = REASONS.get(r.status, str(r.status))
        else:
            raise SkillError(f"{url}: HTTP {r.status}" + (" (a server error: try again later, or archive.py read URL)" if r.status >= 500 else ""))
    except _net.NetError as e:
        reason = e.reason
    for fallback in (load_api_copy, load_open_access):
        got = fallback(url, reason, full=full, inline_links=inline_links)
        if got is not None:
            return got
    if not archive:
        raise SkillError(f"{url}: {reason}")
    global ARCHIVE_BLOCKED
    ARCHIVE_BLOCKED = ""
    for target in archive_targets(url, final):
        arch = load_archived(target, reason=reason, cite_as=url, full=full, inline_links=inline_links)
        if arch is not None:
            return arch
    hint = " The site blocks automated access: don't try to get around it; use other sources." if ("blocked" in reason or "403" in reason) else ""
    if ARCHIVE_BLOCKED:
        why = f"the Wayback Machine's copy is itself a block page ({ARCHIVE_BLOCKED})"
    elif _net.LAST_ARCHIVE_ERROR:
        why = f"the Wayback Machine could not be reached either ({_net.LAST_ARCHIVE_ERROR})"
    else:
        why = "the Wayback Machine has no copy"
    raise SkillError(f"{url}: {reason}, and {why}.{hint}")


def consent_message(url: str, wall: str) -> str:
    if _net.host(url) == "news.google.com":
        hint = ' For a Google News link, search the headline with search.py "HEADLINE" --site OUTLET (or use the Bing News result) to get the direct link.'
    else:
        hint = " Look for the same content elsewhere, or read an archived copy with archive.py read URL."
    return f"{url}: redirected to a cookie-consent page ({wall}) instead of the content; Desk doesn't click through consent pages.{hint}"


def archive_targets(url: str, final: str) -> list[str]:
    """Which URLs to look up in the archive: for a DOI, the publisher page it leads to first (doi.org links are
    archived as redirects); otherwise the URL, then where it redirected (unless that is a home or error page)."""
    out = [url]
    doi = doi_of(url)
    landing = final if _net.url_key(final) != _net.url_key(url) else ""
    if doi and not landing and _net.host(url) in ("doi.org", "dx.doi.org"):
        landing = doi_landing(doi)
    if landing and _net.url_key(landing) != _net.url_key(url):
        path = _net.urlsplit(landing).path
        if doi:
            out.insert(0, landing)
        elif path.strip("/") and not re.search(r"404|not[-_]?found|error|login|signin", path, re.I):
            out.append(landing)
    return out


def doi_landing(doi: str) -> str:
    """Where a DOI points, from the DOI handle API (no request to the publisher)."""
    try:
        data = _net.get_json(f"https://doi.org/api/handles/{doi}?type=URL", ttl=7 * 86400, timeout=15)
    except SkillError:
        return ""
    for v in (data or {}).get("values") or []:
        if v.get("type") == "URL":
            u = str((v.get("data") or {}).get("value") or "")
            return u if _net.is_http(u) else ""
    return ""


def _guess_type(p: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(p.name)[0] or ""


DOI_RX = re.compile(r"(10\.\d{4,9}/[^\s?#]+)")


def doi_of(url: str) -> str:
    """The DOI in a doi.org link or a publisher's /doi/ URL."""
    h = _net.host(url)
    path = _net.urlsplit(url).path
    if h in ("doi.org", "dx.doi.org") or "/doi/" in path:
        from urllib.parse import unquote

        m = DOI_RX.search(unquote(path))
        if not m:
            return ""
        doi = m.group(1).rstrip(".")
        return re.sub(r"/(full|abs|pdf|epdf|html|summary)$", "", doi, flags=re.I) if h not in ("doi.org", "dx.doi.org") else doi
    return ""


def load_open_access(url: str, reason: str, *, full: bool = False, inline_links: bool = False) -> Loaded | None:
    """For a blocked or paywalled DOI link: the open-access copy OpenAlex knows about (a repository or the
    publisher's free version), if there is one. PubMed and PMC copies are read through their API."""
    doi = doi_of(url)
    if not doi:
        return None
    try:
        data = _net.get_json(f"https://api.openalex.org/works/doi:{doi}", ttl=7 * 86400, timeout=15)
    except SkillError:
        return None
    best = data.get("best_oa_location") or {}
    cands = [best.get("pdf_url"), best.get("landing_page_url"), (data.get("open_access") or {}).get("oa_url")]
    for loc in data.get("locations") or []:
        if isinstance(loc, dict) and loc.get("is_oa"):
            cands += [loc.get("pdf_url"), loc.get("landing_page_url")]
    blocked_host = _net.site_of(url)
    tried = 0
    for cand in [c for c in dict.fromkeys(cands) if c and _net.is_http(c) and _net.url_key(c) != _net.url_key(url)]:
        if tried >= 4:
            break
        tried += 1
        api = load_api_copy(cand, "", full=full, inline_links=inline_links)
        if api is not None:
            loaded = api
            r = None
        else:
            if _net.host(cand) in ("doi.org", "dx.doi.org") or (_net.site_of(cand) == blocked_host and "blocked" in reason):
                continue  # the same blocked publisher again
            try:
                r = _net.request(cand, kind="page", timeout=30)
            except _net.NetError:
                continue
            if not r.ok or r.blocked() or _net.consent_wall(r):
                continue
            loaded = Loaded(extract(r.body, r.headers.get("content-type", ""), url, r.final_url, full=full, inline_links=inline_links), r.body, r.headers.get("content-type", ""), r)
            if archived_block(r, loaded.doc):  # a challenge page that answered 200 ('Checking your browser')
                continue
        doc = loaded.doc
        doc.url = url
        doc.doi = doi
        doc.title = doc.title if (doc.title and doc.kind != "pdf") else (data.get("display_name") or doc.title)
        authors = [((a.get("author") or {}).get("display_name") or "") for a in data.get("authorships") or []]
        doc.author = ", ".join(a for a in authors[:12] if a) or doc.author
        doc.canonical = f"https://doi.org/{doi}"  # cite the DOI; the header shows where the copy came from
        if data.get("publication_date"):
            doc.published, doc.date_guessed = data["publication_date"], False
        venue = ((data.get("primary_location") or {}).get("source") or {}).get("display_name")
        doc.site = venue or ((best.get("source") or {}).get("display_name")) or doc.site or _net.domain(doc.final_url)
        if r is not None:
            doc.fetched = iso(r.fetched_at)
            doc.notes.insert(0, f"Open-access copy of doi:{doi} from {_net.domain(r.final_url)} (found through OpenAlex): the publisher's page was unavailable ({reason}).")
        else:
            doc.notes.insert(0, f"Open-access copy of doi:{doi} (found through OpenAlex): the publisher's page was unavailable ({reason}).")
        return loaded
    return None


# ── official API copies (sites that refuse scripted readers but publish an API) ─

PUBMED_URL = re.compile(r"^https?://(?:www\.)?(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d+)", re.I)
PMC_URL = re.compile(r"^https?://(?:pmc\.ncbi\.nlm\.nih\.gov/articles/|(?:www\.)?ncbi\.nlm\.nih\.gov/pmc/articles/|europepmc\.org/articles?/)(?:PMC)?(\d+)", re.I)
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"


def load_api_copy(url: str, reason: str, *, full: bool = False, inline_links: bool = False) -> Loaded | None:
    """PubMed and PMC pages refuse scripted readers: the same record, from NCBI's E-utilities API."""
    m = PUBMED_URL.match(url)
    if m:
        return _ncbi_copy(url, "pubmed", m.group(1), reason)
    m = PMC_URL.match(url)
    if m:
        return _ncbi_copy(url, "pmc", m.group(1), reason)
    return None


def _ncbi_copy(url: str, db: str, ident: str, reason: str) -> Loaded | None:
    import xml.etree.ElementTree as ET
    from urllib.parse import urlencode

    try:
        r = _net.request(EUTILS + urlencode({"db": db, "id": ident, "retmode": "xml"}), kind="api", ttl=7 * 86400, timeout=25)
    except _net.NetError:
        return None
    if not r.ok:
        return None
    try:
        root = ET.fromstring(r.body)
    except ET.ParseError:
        return None
    rec = _pubmed_record(root) if db == "pubmed" else _jats_record(root)
    if not rec or not rec.get("title"):
        return None
    doc = Doc(url=url, final_url=url, kind="html", title=rec["title"], author="; ".join(rec["authors"][:30]), published=rec.get("date", ""), site=rec.get("journal", "") or ("PubMed" if db == "pubmed" else "PubMed Central"), doi=rec.get("doi", ""), content_type="application/xml", status=200, fetched=iso(r.fetched_at), markdown=rec["markdown"], extractor=f"ncbi-{db}", bytes=len(r.body))
    what = "the PubMed record (abstract)" if db == "pubmed" else ("the full text" if rec.get("has_body") else "the article's metadata and abstract (NCBI has no open full text for it)")
    doc.notes.append((f"{_net.domain(url)} refused the page ({reason}): this is " if reason else "This is ") + f"{what} from NCBI's E-utilities API.")
    if rec.get("pmc") and db == "pubmed":
        doc.notes.append(f"Free full text: fetch.py https://pmc.ncbi.nlm.nih.gov/articles/{rec['pmc']}/")
    elif rec.get("doi") and db == "pubmed":
        doc.notes.append(f"Full text: fetch.py https://doi.org/{rec['doi']} (the publisher, its open-access copy or an archived copy).")
    return Loaded(doc, None, "application/xml", r)


def _itx(el) -> str:
    """Text of an XML element without citation cross-references and footnotes."""
    parts = [el.text or ""]
    for ch in el:
        if not ((ch.tag == "xref" and ch.get("ref-type") in ("bibr", "fn")) or ch.tag in ("fn", "table-wrap", "fig", "ref-list")):
            parts.append(_itx(ch))
        parts.append(ch.tail or "")
    s = " ".join("".join(parts).split())
    return re.sub(r"\s*[\[(]\s*[,;–\-\s]*[\])]", "", s)


def _pubmed_record(root) -> dict | None:
    art = root.find(".//PubmedArticle")
    if art is None:
        return None
    title = _itx(art.find(".//ArticleTitle")) if art.find(".//ArticleTitle") is not None else ""
    authors = []
    for a in art.findall(".//AuthorList/Author"):
        if a.findtext("CollectiveName"):
            authors.append(a.findtext("CollectiveName") or "")
        elif a.findtext("LastName"):
            authors.append(f"{a.findtext('LastName')}, {a.findtext('ForeName') or a.findtext('Initials') or ''}".rstrip(", "))
    journal = art.findtext(".//Journal/Title") or ""
    pd = art.find(".//ArticleDate") if art.find(".//ArticleDate") is not None else art.find(".//JournalIssue/PubDate")
    date = ""
    if pd is not None:
        y, mo, d = pd.findtext("Year"), pd.findtext("Month"), pd.findtext("Day")
        if y and mo and not mo.isdigit():
            date = short_date(" ".join(x for x in (d, mo, y) if x))
        elif y:
            date = "-".join(x for x in (y, mo.zfill(2) if mo else "", d.zfill(2) if d and mo else "") if x)
        else:
            date = short_date(pd.findtext("MedlineDate") or "")
    ids = {i.get("IdType"): (i.text or "") for i in art.findall(".//PubmedData/ArticleIdList/ArticleId")}
    doi = ids.get("doi") or next((e.text or "" for e in art.findall(".//ELocationID") if e.get("EIdType") == "doi"), "")
    paras = []
    for t in art.findall(".//Abstract/AbstractText"):
        label = t.get("Label")
        text = _itx(t)
        paras.append(f"**{label.title()}:** {text}" if label else text)
    head = " · ".join(x for x in ("; ".join(authors[:8]) + (" et al." if len(authors) > 8 else ""), f"*{journal}*" if journal else "", date, f"doi:{doi}" if doi else "", f"PMID {ids.get('pubmed', '')}".strip()) if x)
    md = f"# {title}\n\n{head}\n\n## Abstract\n\n" + ("\n\n".join(paras) if paras else "(PubMed has no abstract for this record.)")
    return {"title": title, "authors": authors, "journal": journal, "date": date, "doi": doi, "pmc": ids.get("pmc", ""), "markdown": md}


def _jats_record(root) -> dict | None:
    art = root if root.tag == "article" else root.find(".//article")
    if art is None:
        return None
    meta = art.find("front/article-meta")
    if meta is None:
        return None
    tnode = meta.find("title-group/article-title")
    title = _itx(tnode) if tnode is not None else ""
    authors = []
    for c in meta.findall(".//contrib-group/contrib"):
        if c.get("contrib-type", "author") != "author":
            continue
        n = c.find("name")
        if n is not None and n.findtext("surname"):
            authors.append(f"{n.findtext('surname')}, {n.findtext('given-names') or ''}".rstrip(", "))
        elif c.findtext("collab"):
            authors.append(_itx(c.find("collab")))
    journal = art.findtext("front/journal-meta/journal-title-group/journal-title") or art.findtext("front/journal-meta/journal-title") or ""
    date = ""
    for pd in meta.findall("pub-date"):
        y, mo, d = pd.findtext("year"), pd.findtext("month"), pd.findtext("day")
        if y:
            date = "-".join(x for x in (y, (mo or "").zfill(2) if mo else "", (d or "").zfill(2) if d and mo else "") if x)
            if pd.get("pub-type") in ("epub", "ppub") or pd.get("date-type") == "pub":
                break
    ids = {i.get("pub-id-type"): (i.text or "") for i in meta.findall("article-id")}
    out: list[str] = [f"# {title}", " · ".join(x for x in ("; ".join(authors[:8]) + (" et al." if len(authors) > 8 else ""), f"*{journal}*" if journal else "", date, f"doi:{ids['doi']}" if ids.get("doi") else "") if x)]
    abstract = meta.find("abstract")
    if abstract is not None:
        out.append("## Abstract")
        _jats_blocks(abstract, 3, out)
    body = art.find("body")
    if body is not None:
        _jats_blocks(body, 2, out)
    return {"title": title, "authors": authors, "journal": journal, "date": date, "doi": ids.get("doi", ""), "pmc": "", "markdown": "\n\n".join(x for x in out if x), "has_body": body is not None}


def _jats_blocks(el, level: int, out: list[str]) -> None:
    for ch in el:
        tag = ch.tag
        if tag == "sec":
            t = ch.find("title")
            if t is not None and _itx(t):
                out.append("#" * min(6, level) + " " + _itx(t))
            _jats_blocks(ch, level + 1, out)
        elif tag == "p":
            s = _itx(ch)
            if s:
                out.append(s)
        elif tag == "list":
            items = [_itx(li) for li in ch.findall("list-item")]
            out.append("\n".join(f"- {i}" for i in items if i))
        elif tag in ("fig", "table-wrap"):
            cap = " ".join(x for x in (_itx(ch.find("label")) if ch.find("label") is not None else "", _itx(ch.find("caption")) if ch.find("caption") is not None else "") if x)
            if cap:
                out.append(f"*{cap}*")
        elif tag in ("boxed-text", "statement", "def-list", "disp-quote"):
            _jats_blocks(ch, level, out)


# ── archives ────────────────────────────────────────────────────────────

ARCHIVE_BLOCKED = ""  # why the last archived copies were refused ('' when none was)
BLOCK_PAGE = re.compile(
    r"cookies (must|need to) be enabled|enable cookies|please enable javascript|javascript is (disabled|required)|access denied|"
    r"verify (that )?you are (a )?human|are you a robot|unusual traffic|request (was )?blocked|has been blocked|captcha|"
    r"checking your browser|just a moment|attention required",
    re.I,
)


def archived_block(r, doc: Doc) -> str:
    """Why an archived copy is a block page rather than the content ('' when it looks like the content)."""
    reason = _net.looks_blocked(_net.Response(r.url, r.final_url, 200, r.headers, r.body))
    if reason:
        return reason
    if len(doc.markdown) < 1500:
        m = BLOCK_PAGE.search(doc.markdown + " " + doc.title)
        if m:
            return f"'{m.group(0)}'"
    return ""


def load_archived(url: str, *, when: str | None = None, reason: str = "", cite_as: str | None = None, full: bool = False, inline_links: bool = False) -> Loaded | None:
    """The nearest Wayback snapshot of url as a document (cited as cite_as, default url). A snapshot that is itself a
    block page (the archive's crawler was refused too) is skipped for the next nearest, twice at most."""
    global ARCHIVE_BLOCKED
    tried: list[str] = []
    while len(tried) < 3:
        snap = _net.wayback_nearest(url, when, skip=tried)
        if not snap:
            return None
        raw = _net.wayback_raw(snap["url"])
        try:
            r = _net.PLAYBACK.pop(raw, None) or _net.request(raw, kind="page", timeout=40, ttl=7 * 86400)
        except _net.NetError:
            return None
        if not r.ok:
            return None
        doc = extract(r.body, r.headers.get("content-type", ""), cite_as or url, snap["url"], full=full, inline_links=inline_links)
        block = archived_block(r, doc)
        if block:
            ARCHIVE_BLOCKED = f"the capture from {snap['date']} shows {block}"
            tried.append(snap["timestamp"])
            continue
        if not doc.site or "archive.org" in doc.site:
            doc.site = _net.domain(url)
        if cite_as and not doc.doi:
            doc.doi = doi_of(cite_as)
        doc.archived = {"source": "wayback", "date": snap["date"], "timestamp": snap["timestamp"], "snapshot": snap["url"], "reason": reason, "of": url}
        doc.fetched = iso(r.fetched_at)
        where = "" if not cite_as or _net.url_key(cite_as) == _net.url_key(url) else f" of {url}"
        if reason:
            doc.notes.insert(0, _net.archive_note(snap["date"], reason).replace("(the Wayback Machine)", f"(the Wayback Machine{where})"))
        else:
            doc.notes.insert(0, f"Archived copy from {snap['date']} (the Wayback Machine{where}).")
        return Loaded(doc, r.body, r.headers.get("content-type", ""), r)
    return None


# ── sections, outline, grep, paging ────────────────────────────────────


@dataclass
class Section:
    id: int
    level: int
    title: str
    start: int
    end: int  # including subsections
    own_end: int  # up to the next heading


HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def sections(md: str) -> list[Section]:
    heads: list[tuple[int, int, str]] = []
    pos = 0
    fence = False
    for line in md.splitlines(keepends=True):
        s = line.rstrip("\n")
        if s.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence:
            m = HEADING.match(s)
            if m:
                heads.append((pos, len(m.group(1)), m.group(2).strip()))
        pos += len(line)
    out: list[Section] = []
    first = heads[0][0] if heads else len(md)
    if md[:first].strip():
        out.append(Section(0, 0, "(before the first heading)", 0, first, first))
    for i, (start, level, title) in enumerate(heads):
        own_end = heads[i + 1][0] if i + 1 < len(heads) else len(md)
        end = len(md)
        for s2, l2, _ in heads[i + 1 :]:
            if l2 <= level:
                end = s2
                break
        out.append(Section(i + 1, level, title, start, end, own_end))
    return out


def outline(md: str, limit: int | None = None) -> str:
    secs = sections(md)
    lines = []
    for s in secs[: limit or len(secs)]:
        indent = "  " * max(0, s.level - 1)
        size = s.own_end - s.start
        lines.append(f"{indent}§{s.id} {s.title} ({_k(size)})")
    if limit and len(secs) > limit:
        lines.append(f"… {len(secs) - limit} more sections (--outline shows all)")
    return "\n".join(lines)


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k chars" if n >= 1000 else f"{n} chars"


def pick_sections(md: str, spec: str) -> list[Section]:
    secs = sections(md)
    if not secs:
        raise UsageError("this page has no sections; use --grep or --offset")
    by_id = {s.id: s for s in secs}
    chosen: list[Section] = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        m = re.fullmatch(r"§?(\d+)(?:\s*-\s*§?(\d+))?", part)
        if m:
            a = int(m.group(1))
            b = int(m.group(2) or a)
            for i in range(a, b + 1):
                if i not in by_id:
                    raise UsageError(f"no section §{i} (there are {secs[-1].id}); see --outline")
                chosen.append(by_id[i])
            continue
        low = part.lower()
        hits = [s for s in secs if low in s.title.lower()]
        if not hits:
            raise UsageError(f"no section title contains '{part}'; see --outline")
        chosen.append(hits[0])
    seen: set[int] = set()
    return [s for s in chosen if not (s.id in seen or seen.add(s.id))]


def section_text(md: str, secs: list[Section]) -> str:
    return "\n\n".join(md[s.start : s.end].strip() for s in secs)


def grep(md: str, pattern: str, context: int = 1, case: bool = False, max_matches: int = 40) -> tuple[str, int]:
    """Lines matching a regex (or a literal when the regex is invalid), with context and their section ids."""
    flags = 0 if case else re.I
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)
    lines = md.splitlines()
    secs = sections(md)
    offsets = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    def sec_at(off: int) -> Section | None:
        cur = None
        for s in secs:
            if s.start <= off:
                cur = s
            else:
                break
        return cur

    hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
    total = len(hits)
    hits = hits[:max_matches]
    blocks: list[str] = []
    last_end = -1
    group: list[str] = []
    shown: set[int] = set()  # table header rows already printed (once per table, not once per match)
    for i in hits:
        a, b = max(0, i - context), min(len(lines), i + context + 1)
        if a <= last_end and group:
            a = last_end
        else:
            if group:
                blocks.append("\n".join(group))
            s = sec_at(offsets[i])
            where = f"§{s.id} {s.title}" if s else "top"
            group = [f"── {where} · line {i + 1}"]
        if lines[i].lstrip().startswith("|"):  # a table row: show the table's header row so the columns have names
            t0 = i
            while t0 > 0 and lines[t0 - 1].lstrip().startswith("|"):
                t0 -= 1
            if t0 < a and t0 not in shown:
                shown.add(t0)
                group.append(f"  {lines[t0]}")
                if t0 + 1 < a and _md_rule(lines[t0 + 1]):
                    group.append(f"  {lines[t0 + 1]}")
        for j in range(a, b):
            if j < last_end:
                continue
            mark = ">" if j == i or rx.search(lines[j]) else " "
            group.append(f"{mark} {lines[j]}")
        last_end = b
    if group:
        blocks.append("\n".join(group))
    return "\n".join(blocks), total


def page(text: str, offset: int, max_chars: int) -> tuple[str, int | None]:
    """A slice of text that ends on a line break when one is near, and the next offset (None at the end)."""
    if offset < 0:
        raise UsageError("--offset must be ≥ 0")
    if offset >= len(text):
        return "", None
    end = offset + max_chars
    if end >= len(text):
        return text[offset:], None
    cut = text.rfind("\n", offset + max_chars // 2, end)
    if cut > offset:
        end = cut + 1
    return text[offset:end], end


# ── CLI glue shared by the readers ─────────────────────────────────────


def add_read_args(p: argparse.ArgumentParser, default_max: int = DEFAULT_READ) -> None:
    g = p.add_argument_group("reading")
    g.add_argument("--outline", action="store_true", help="list the sections (§id, title, size) instead of the text")
    g.add_argument("--section", metavar="ID|TITLE", help="print sections by id (3, 3-5, 2,7) or by title words")
    g.add_argument("--grep", metavar="REGEX", help="print matching lines with their section and context")
    g.add_argument("--context", type=int, default=2, help="lines of context around --grep matches (default 2)")
    g.add_argument("--case-sensitive", action="store_true", help="--grep is case-insensitive unless this is given")
    g.add_argument("--max-chars", type=int, default=default_max, help=f"text budget (default {default_max:,})")
    g.add_argument("--offset", type=int, default=0, help="start at this character (paging through long pages)")
    g.add_argument("--meta", action="store_true", help="only the metadata (title, author, dates, size, outline size)")
    g.add_argument("--full", action="store_true", help="the whole page (navigation included) instead of the main content")
    g.add_argument("--inline-links", action="store_true", help="keep links inline in the Markdown ([text](url))")


def header_lines(doc: Doc) -> list[str]:
    lines = []
    if doc.title:
        lines.append(f"Title: {doc.title}")
    lines.append(f"URL: {doc.final_url if not doc.archived else doc.url}")
    if doc.archived:
        lines.append(f"Archived snapshot: {doc.archived.get('snapshot', '')}")
    if doc.canonical and _net.url_key(doc.canonical) != _net.url_key(doc.final_url) and not doc.archived:
        lines.append(f"Canonical: {doc.canonical}")
    bits = []
    if doc.site:
        bits.append(f"site: {doc.site}")
    if doc.author:
        bits.append(f"author: {doc.author}")
    if doc.published:
        bits.append(f"date: {short_date(doc.published)} (guessed from the page)" if doc.date_guessed else f"published: {short_date(doc.published)}")
    if doc.modified and short_date(doc.modified) != short_date(doc.published):
        bits.append(f"updated: {short_date(doc.modified)}")
    if doc.lang:
        bits.append(f"lang: {doc.lang}")
    if doc.doi:
        bits.append(f"doi: {doc.doi}")
    if bits:
        lines.append(" · ".join(bits))
    n_sec = len(sections(doc.markdown))
    size = f"{doc.kind}" + (f", {doc.pages} pages" if doc.pages else "") + f" · {len(doc.markdown):,} chars" + (f" in {n_sec} sections" if n_sec > 1 else "")
    lines.append(f"Size: {size} · fetched {doc.fetched or iso()}")
    for n in doc.notes:
        lines.append(f"Note: {n}")
    return lines


def render(doc: Doc, args: argparse.Namespace, fmt: str = "md") -> str:
    """The reader output for one document, honouring --outline/--section/--grep/--meta/--offset/--max-chars."""
    md = doc.markdown
    footer = ""
    body = ""
    mode = "text"
    matches = None
    if getattr(args, "meta", False):
        mode = "meta"
    elif getattr(args, "outline", False):
        mode = "outline"
        body = outline(md) or "(no headings: use --grep or --offset to move through the text)"
    elif getattr(args, "section", None):
        mode = "section"
        secs = pick_sections(md, args.section)
        text = section_text(md, secs)
        body, nxt = page(text, args.offset, args.max_chars)
        if nxt is not None:
            footer = f"[section text continues: {len(text) - nxt:,} more chars. Next: --section {args.section} --offset {nxt}]"
    elif getattr(args, "grep", None):
        mode = "grep"
        body, matches = grep(md, args.grep, args.context, args.case_sensitive)
        body = body or f"(no line matches {args.grep!r})"
        body, nxt = page(body, 0, args.max_chars)
        if matches > 40:
            footer = f"[{matches} matching lines; the first 40 are shown. Narrow the pattern.]"
    else:
        big = len(md) > args.max_chars and args.offset == 0
        if big and len(sections(md)) > 2:
            ol = outline(md, BIG_OUTLINE)
            budget = max(2000, args.max_chars - len(ol) - 200)
            chunk, nxt = page(md, 0, budget)
            body = f"Outline (read parts with --section ID):\n{ol}\n\n────────\n\n{chunk}"
        else:
            body, nxt = page(md, args.offset, args.max_chars)
        if nxt is not None:
            footer = f"[showing chars {args.offset:,}–{nxt:,} of {len(md):,}. Next: --offset {nxt} (or --outline, --section, --grep)]"
        elif args.offset:
            footer = f"[end of text: chars {args.offset:,}–{len(md):,}]"
    if fmt == "json":
        out = doc.to_dict()
        out.pop("markdown", None)
        out.update({"mode": mode, "chars": len(md), "untrusted": _net.UNTRUSTED})
        if mode == "outline":
            out["outline"] = [asdict(s) for s in sections(md)]
        elif mode != "meta":
            out["text"] = body
        if matches is not None:
            out["matches"] = matches
        if footer:
            out["continue"] = footer
        return json.dumps(out, ensure_ascii=False, indent=2)
    parts = header_lines(doc)
    if mode == "meta":
        parts.append(f"Sections: {len(sections(md))}")
        return _net.framed(doc.cite_url, "\n".join(parts))
    text = "\n".join(parts) + "\n\n" + body.strip()
    out = _net.framed(doc.cite_url, text)
    return out + (f"\n{footer}" if footer else "")


# ── links, tables, images, saving ───────────────────────────────────────


def render_links(url: str, body: bytes, content_type: str, fmt: str, max_links: int = 400) -> str:
    import _html

    tree = _html.parse(_net.decode(body, content_type))
    items = _html.links(tree, url)
    here = _net.site_of(url)
    for it in items:
        it["internal"] = _net.site_of(it["url"]) == here
    if fmt == "json":
        return json.dumps({"url": url, "count": len(items), "links": items[:max_links], "untrusted": _net.UNTRUSTED}, ensure_ascii=False, indent=2)
    inside = [i for i in items if i["internal"]]
    outside = [i for i in items if not i["internal"]]
    lines = [f"{len(items)} links ({len(inside)} on {here}, {len(outside)} elsewhere)"]
    for label, group in ((f"On {here}", inside), ("Elsewhere", outside)):
        if group:
            lines.append(f"\n{label}:")
            lines += [f"- {i['text'] or '(no text)'} — {i['url']}" for i in group[:max_links]]
    if len(items) > max_links:
        lines.append(f"[… {len(items) - max_links} more links not shown]")
    return _net.framed(url, "\n".join(lines))


def html_tables(body: bytes, content_type: str) -> list[dict]:
    import _html

    return _html.tables(_html.parse(_net.decode(body, content_type)))


def render_tables(url: str, tables: list[dict], style: str, fmt: str) -> str:
    if fmt == "json":
        return json.dumps({"url": url, "tables": tables, "untrusted": _net.UNTRUSTED}, ensure_ascii=False, indent=2)
    if not tables:
        return _net.framed(url, "No data tables on this page (a PDF's tables: use pdf-toolkit on a saved copy).")
    out = []
    for i, t in enumerate(tables, 1):
        cap = f": {t['caption']}" if t.get("caption") else ""
        out.append(f"Table {i}{cap} ({len(t['rows']) - 1} rows × {len(t['rows'][0])} columns)")
        if style == "csv":
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(t["rows"])
            out.append(buf.getvalue().rstrip())
        else:
            out.append(md_table(t["rows"][0], t["rows"][1:]))
        out.append("")
    return _net.framed(url, "\n".join(out).rstrip())


def write_tables_csv(tables: list[dict], folder: Path, stem: str) -> list[Path]:
    paths = []
    for i, t in enumerate(tables, 1):
        p = folder / f"{stem}-table{i}.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(t["rows"])
        paths.append(p)
    return paths


def slug(url: str, limit: int = 90) -> str:
    p = _net.urlsplit(url)
    s = (p.hostname or "page") + (p.path or "")
    if p.query:
        s += "-" + p.query
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")
    s = re.sub(r"\.(html?|php|aspx?)$", "", s, flags=re.I)
    return (s[:limit].rstrip("-._") or "page").lower()


EXT = {"html": ".html", "pdf": ".pdf", "feed": ".xml", "json": ".json", "text": ".txt"}


def save(doc: Doc, body: bytes | None, folder: Path, force: bool = False) -> list[Path]:
    """Writes the raw response (.html/.pdf/…) and the Markdown (.md, with a metadata header) into folder."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = slug(doc.url)
    out = []
    if body is not None:
        ext = EXT.get(doc.kind) or _image_ext(body) or ".bin"
        raw = folder / f"{stem}{ext}"
        if raw.exists() and not force:
            raw = _unique(raw)
        raw.write_bytes(body)
        out.append(raw)
    md = folder / f"{stem}.md"
    if md.exists() and not force:
        md = _unique(md)
    head = ["---", f"url: {doc.url}", f"title: {json.dumps(doc.title, ensure_ascii=False)}", f"fetched: {doc.fetched}"]
    for k in ("author", "published", "site", "lang"):
        if getattr(doc, k):
            head.append(f"{k}: {json.dumps(getattr(doc, k), ensure_ascii=False)}")
    if doc.archived:
        head.append(f"archived: {doc.archived.get('snapshot')}")
    head += ["---", _net.frame_open(doc.cite_url), ""]
    md.write_text("\n".join(head) + doc.markdown + "\n", encoding="utf-8")
    out.append(md)
    return out


def _unique(p: Path) -> Path:
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not q.exists():
            return q
    raise SkillError(f"too many files named like {p}")


def _image_ext(body: bytes) -> str | None:
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    return None


VISION_EDGE = 1568
MAX_VIEW_BYTES = 3_700_000
SKIP_IMG = re.compile(r"(sprite|spacer|pixel|tracking|1x1|blank\.gif|/ads?/|doubleclick|favicon|gravatar\.com/avatar)", re.I)


def download_images(url: str, body: bytes, content_type: str, folder: Path, limit: int = 12, min_side: int = 64) -> list[dict]:
    """Downloads a page's images (politely), converting and downscaling them so view_image can show them."""
    import _html

    tree = _html.parse(_net.decode(body, content_type))
    main = _html.main_node(tree)
    cands = _html.images(main, url) if main is not None else []
    for extra in _html.images(tree, url):
        if all(extra["url"] != c["url"] for c in cands):
            cands.append(extra)
    cands = [c for c in cands if not SKIP_IMG.search(c["url"]) and not ((c["width"] or 999) < min_side or (c["height"] or 999) < min_side)]
    folder.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for i, c in enumerate(cands):
        if len(out) >= limit:
            break
        try:
            r = _net.request(c["url"], kind="page", headers={"Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5", "Referer": url}, max_bytes=20 * 1024 * 1024, timeout=20)
        except _net.NetError:
            continue
        if not r.ok:
            continue
        saved = save_image(r.body, folder / f"img-{len(out) + 1:02d}", min_side)
        if saved:
            saved.update({"source": c["url"], "alt": c["alt"]})
            out.append(saved)
    return out


def save_image(data: bytes, stem: Path, min_side: int = 1) -> dict | None:
    """Writes an image view_image can show (PNG/JPEG/GIF/WebP ≤ 3.7 MB, long edge ≤ 1568 px); None if not an image."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001 — SVG, AVIF without a plugin, HTML error pages …
        return None
    if im.width < min_side or im.height < min_side:
        return None
    fmt = (im.format or "").upper()
    ok_fmt = fmt in ("PNG", "JPEG", "GIF", "WEBP")
    if ok_fmt and len(data) <= MAX_VIEW_BYTES and max(im.size) <= VISION_EDGE:
        ext = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}[fmt]
        path = stem.with_suffix(ext)
        path.write_bytes(data)
        return {"path": str(path), "width": im.width, "height": im.height, "format": fmt}
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
    im.thumbnail((VISION_EDGE, VISION_EDGE))
    if im.mode == "RGBA":
        path = stem.with_suffix(".png")
        im.save(path, "PNG", optimize=True)
    else:
        path = stem.with_suffix(".jpg")
        im.save(path, "JPEG", quality=88)
    return {"path": str(path), "width": im.width, "height": im.height, "format": path.suffix[1:].upper()}
