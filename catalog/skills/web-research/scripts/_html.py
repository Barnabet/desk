"""HTML helpers for web-research (selectolax/lexbor): page metadata, links, tables, images, feed links, a
JavaScript-needed heuristic, and a plain HTML → Markdown converter used when main-content extraction is not wanted
(--full) or finds nothing."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from _net import resolve

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed", "head", "select", "button", "input", "textarea", "option", "dialog", "map", "audio", "video", "source", "track", "link", "meta"}
BOILERPLATE = {"nav", "header", "footer", "aside", "form"}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "center", "dd", "details", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section",
    "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "caption", "hgroup", "menu",
}
WS = re.compile(r"\s+")


def parse(html: str):
    from selectolax.lexbor import LexborHTMLParser

    return LexborHTMLParser(html)


# The opening tag, then a search for its closing tag: one regex with [^>]*? and .*?</\1> rescanned the rest of the page
# from every "<a" (quadratic on a hostile page). [^<>] ends a tag at the next "<"; a tag that is never closed is
# remembered, so later ones are not searched for again.
CF_OPEN = re.compile(r"<(a|span)\b[^<>]*?\bdata-cfemail=[\"']([0-9a-fA-F]+)[\"'][^<>]*>", re.I)
CF_CLOSE = {t: re.compile(rf"</{t}\s*>", re.I) for t in ("a", "span")}
CF_HREF = re.compile(r"(href=[\"'])(?:https?://[^\"'#]*)?/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", re.I)


def _cf_decode(hexs: str) -> str:
    try:
        b = bytes.fromhex(hexs)
    except ValueError:
        return ""
    return bytes(x ^ b[0] for x in b[1:]).decode("utf-8", "replace") if len(b) > 1 else ""


def decode_cf_emails(html: str) -> str:
    """Undoes Cloudflare's e-mail obfuscation ('[email protected]' + data-cfemail), which the browser decodes with a
    script: without it, addresses and git@host URLs in docs would read as '[email protected]'."""
    if "cfemail" not in html and "email-protection#" not in html:
        return html
    import html as htmllib

    out, pos, unclosed = [], 0, set()
    for m in CF_OPEN.finditer(html):
        tag = m.group(1).lower()
        if m.start() < pos or tag in unclosed:
            continue
        end = CF_CLOSE[tag].search(html, m.end())
        if end is None:
            unclosed.add(tag)
            continue
        out += [html[pos : m.start()], htmllib.escape(_cf_decode(m.group(2))) or html[m.start() : end.end()]]
        pos = end.end()
    html = "".join(out) + html[pos:]
    return CF_HREF.sub(lambda m: m.group(1) + "mailto:" + _cf_decode(m.group(2)), html)


def clean(s: str | None) -> str:
    return WS.sub(" ", s or "").strip()


def tidy(s: str) -> str:
    """Collapses whitespace and the padding inline markup leaves before punctuation."""
    return re.sub(r"\s+([.,;:!?)\]])(?=\s|$)", r"\1", clean(s)).replace("( ", "(")


def text_of(node) -> str:
    return clean(node.text(deep=True)) if node is not None else ""


def _attr(node, name: str) -> str:
    v = node.attributes.get(name) if node is not None else None
    return (v or "").strip()


# ── metadata ────────────────────────────────────────────────────────────


def _meta(tree, *names: str) -> str:
    for n in names:
        for sel in (f'meta[property="{n}"]', f'meta[name="{n}"]', f'meta[itemprop="{n}"]'):
            node = tree.css_first(sel)
            if node is not None and _attr(node, "content"):
                return clean(_attr(node, "content"))
    return ""


def _jsonld(tree) -> list[dict]:
    out: list[dict] = []
    for s in tree.css('script[type="application/ld+json"]'):
        raw = s.text(deep=True) or ""
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                if "@graph" in d:
                    stack.extend(d["@graph"] if isinstance(d["@graph"], list) else [d["@graph"]])
                out.append(d)
    return out


ORG_TYPES = re.compile(r"Organi[sz]ation|Corporation|NewsMedia|NGO|GovernmentOrganization|EducationalOrganization|LocalBusiness|Brand|WebSite", re.I)


def _is_org_node(v: Any) -> bool:
    """True when a JSON-LD author is an organisation rather than a person."""
    items = v if isinstance(v, list) else [v]
    types = [" ".join(t) if isinstance(t, list) else str(t or "") for t in (x.get("@type") for x in items if isinstance(x, dict))]
    return bool(types) and all(ORG_TYPES.search(t) for t in types)


def _names(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [str(v.get("name"))] if v.get("name") else []
    if isinstance(v, list):
        return [n for x in v for n in _names(x)]
    return []


ARTICLE_TYPES = re.compile(r"Article|BlogPosting|Report|ScholarlyArticle|WebPage|Posting|Review|Recipe|HowTo|Question|Book|Dataset|VideoObject", re.I)
_CONNECTORS = {"of", "and", "the", "for", "to", "in", "on", "at", "de", "da", "del", "der", "di", "du", "la", "le", "van", "von", "y", "e", "&", "den", "des", "af", "zu", "bin", "al", "el", "ter", "ten"}
DOI_IN = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>?#]+)")


ROLE = re.compile(
    r"\b(editor(-in-chief)?|chief|writer|reporter|correspondent|contributor|columnist|producer|director|manager|staff|senior|"
    r"lead|head of|founder|ceo|cto|coo|cfo|president|analyst|intern|freelancer?|professor|journalist|host|engineer|developer|"
    r"designer|scientist|researcher|fellow|officer|vp|evangelist|advocate|specialist|consultant|photographer|critic|anchor|"
    r"md|phd|msc|mph|rn)\b",
    re.I,
)


def plausible_author(name: str) -> str:
    """The name when it can be a byline ('Joe Doe, Senior Editor, Example.com' → 'Joe Doe'); '' for headings,
    sentences, URLs and other junk picked up as an author."""
    s = clean(re.sub(r"^(by|par|von|por|di)\s+", "", clean(name), flags=re.I)).strip(" ,;|·-")
    parts = [p.strip() for p in s.split(",")]
    for i, p in enumerate(parts[1:], 1):
        if ROLE.search(p) or re.search(r"\.[a-z]{2,6}$", p, re.I):  # a job title or a site name after the name
            s = ", ".join(parts[:i])
            break
    if not s or len(s) > 120 or re.search(r"[?!<>{}]|https?://|www\.|\.(com|org|net)\b|@", s):
        return ""
    words = s.replace(",", " ").split()
    lower = [w for w in words if w[:1].islower() and w.lower() not in _CONNECTORS]
    if len(words) >= 3 and len(lower) >= 2 and len(lower) * 2 >= len(words):
        return ""  # 'Will I be charged', 'click here to read more' …
    if sum(ch.isdigit() for ch in s) > 4:
        return ""
    return s


def plausible_site(site: str, url: str) -> str:
    """The site name, unless it is some other host's name (a CDN picked up from an image URL, say)."""
    from _net import site_of

    s = clean(site)
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+", s) and site_of("https://" + s.lower()) != site_of(url):
        return ""
    return s


def norm_doi(value: str) -> str:
    """'doi:10.1/x', 'https://doi.org/10.1/x' → '10.1/x'; '' when there is no DOI in it."""
    m = DOI_IN.search(value or "")
    return m.group(1).rstrip(".,;") if m else ""


def page_meta(tree, url: str) -> dict:
    """Title, description, canonical URL, language, author, dates, site name, feeds, robots and paywall flags."""
    title_node = tree.css_first("title")
    m: dict[str, Any] = {
        "title": _meta(tree, "og:title", "twitter:title") or text_of(title_node),
        "page_title": text_of(title_node),
        "description": _meta(tree, "description", "og:description", "twitter:description"),
        "site": _meta(tree, "og:site_name", "application-name"),
        "author": _meta(tree, "author", "article:author", "parsely-author", "dc.creator", "DC.creator", "citation_author", "sailthru.author"),
        "author_org": False,
        "published": _meta(tree, "article:published_time", "datePublished", "citation_publication_date", "citation_date", "dc.date", "DC.date", "pubdate", "date", "parsely-pub-date", "og:published_time"),
        "modified": _meta(tree, "article:modified_time", "dateModified", "og:updated_time", "last-modified"),
        "type": _meta(tree, "og:type"),
        "image": _meta(tree, "og:image", "twitter:image"),
        "doi": _meta(tree, "citation_doi", "dc.identifier", "prism.doi"),
        "lang": "",
        "canonical": "",
        "feeds": [],
        "noindex": False,
        "nofollow": False,
        "paywalled": False,
    }
    html_node = tree.css_first("html")
    if html_node is not None:
        m["lang"] = _attr(html_node, "lang") or _attr(html_node, "xml:lang")
    if not m["lang"]:
        m["lang"] = _meta(tree, "og:locale", "language", "content-language")
    link = tree.css_first('link[rel="canonical"]')
    if link is not None:
        m["canonical"] = resolve(url, _attr(link, "href")) or ""
    robots = (_meta(tree, "robots") + " " + _meta(tree, "Desk")).lower()
    m["noindex"] = "noindex" in robots or "none" in robots.split(",")
    m["nofollow"] = "nofollow" in robots or "none" in robots.split(",")
    m["feeds"] = feed_links(tree, url)
    publisher = ""
    for d in _jsonld(tree):
        t = d.get("@type")
        t = " ".join(t) if isinstance(t, list) else str(t or "")
        if not ARTICLE_TYPES.search(t):
            if t in ("Organization", "WebSite") and not m["site"]:
                m["site"] = clean(str(d.get("name") or ""))
            continue
        who = d.get("author") or d.get("creator")
        if not m["author"]:
            m["author"] = ", ".join(dict.fromkeys(_names(who)))
            m["author_org"] = bool(m["author"]) and _is_org_node(who)
        elif who and _is_org_node(who) and clean(m["author"]).lower() in {n.lower() for n in _names(who)}:
            m["author_org"] = True
        if not m["published"] and d.get("datePublished"):
            m["published"] = str(d["datePublished"])
        if not m["modified"] and d.get("dateModified"):
            m["modified"] = str(d["dateModified"])
        if not publisher and d.get("publisher"):
            publisher = ", ".join(_names(d["publisher"]))
        if d.get("headline") and not _meta(tree, "og:title"):
            m["title"] = clean(str(d["headline"]))
        free = d.get("isAccessibleForFree")
        if free is False or str(free).lower() == "false":
            m["paywalled"] = True
    if not m["author"]:
        node = tree.css_first('[rel="author"], .author-name, .byline__name, [itemprop="author"] [itemprop="name"], .byline')
        if node is not None:
            a = text_of(node)
            if 0 < len(a) < 80:
                m["author"] = a
    m["author"] = plausible_author(m["author"])
    m["author_org"] = bool(m["author"]) and m["author_org"]
    if not m["published"]:
        node = tree.css_first("time[datetime]")
        if node is not None:
            m["published"] = _attr(node, "datetime")
    m["site"] = plausible_site(m["site"] or publisher, url)
    m["doi"] = norm_doi(m["doi"])
    if _meta(tree, "generator").lower().startswith("mediawiki"):
        # A wiki page: written by its contributors, current as of its last revision, published by the wiki.
        search = tree.css_first('link[rel="search"][title]')
        wiki = re.sub(r"(?<!\s)\s*\([\w-]+\)$", "", _attr(search, "title")) if search is not None else ""
        m["site"] = wiki or m["site"]
        if re.match(r"contributors to\b", m["author"], re.I):
            m["author"] = ""
        if m["modified"]:
            m["published"] = m["modified"]
    m["title"], carried = split_title(m["title"], m["site"], strip_marks(text_of(tree.css_first("h1"))), url)
    if not m["site"] and carried:
        m["site"] = plausible_site(carried, url)
    return m


def strip_marks(s: str) -> str:
    """Text without trailing blanks and heading anchors (¶ # 🔗); not re.sub(r"[\\s¶#🔗]+$"), quadratic on '###…x'."""
    end = len(s)
    while end and (s[end - 1].isspace() or s[end - 1] in "¶#🔗"):
        end -= 1
    return s[:end]


TITLE_SEP = re.compile(r"(?<!\s)\s+(?:\||-|–|—|·|::|»)\s+")  # starts only where a blank run does: linear


def clean_title(title: str, site: str, h1: str, url: str) -> str:
    """'Tidal Power Report | Harbour News' → 'Tidal Power Report' when the other part is the site's name."""
    return split_title(title, site, h1, url)[0]


def split_title(title: str, site: str, h1: str, url: str) -> tuple[str, str]:
    """(title, the site name it carried or ''): 'Pricing | Backblaze' → ('Pricing', 'Backblaze'); a short last part
    after ' | ' ('The auth CLI | uv') is taken for the site's name too."""
    parts = [p for p in TITLE_SEP.split(title) if p]
    if len(parts) < 2:
        return title, ""
    from _net import domain

    names = {x.lower() for x in (site, domain(url), domain(url).split(".")[0]) if x}
    if h1 and any(p.lower() == h1.lower() for p in parts):
        rest = [p for p in parts if p.lower() != h1.lower()]
        return h1, (rest[-1] if len(rest) == 1 and len(rest[0]) <= 40 else "")
    keep = [p for p in parts if p.lower() not in names and p.lower().replace(" ", "") not in names]
    if 0 < len(keep) < len(parts):
        sep = TITLE_SEP.search(title)
        dropped = [p for p in parts if p not in keep]
        return (sep.group(0) if sep else " - ").join(keep), dropped[-1] if dropped else ""
    last = parts[-1]
    if len(parts) == 2 and " | " in title and len(last) <= 20 and len(last.split()) <= 2 and len(parts[0].split()) >= 2:
        return parts[0], last
    return title, ""


def feed_links(tree, url: str) -> list[dict]:
    out, seen = [], set()
    for node in tree.css('link[rel~="alternate"], link[rel="feed"]'):
        t = _attr(node, "type").lower()
        if not any(k in t for k in ("rss", "atom", "feed+json", "rdf")) and _attr(node, "rel") != "feed":
            continue
        u = resolve(url, _attr(node, "href"))
        if u and u not in seen:
            seen.add(u)
            out.append({"url": u, "title": _attr(node, "title"), "type": t or "feed"})
    return out


# ── links, tables, images ──────────────────────────────────────────────


def links(tree, base: str) -> list[dict]:
    """Every link as {url, text, rel}; deduplicated by URL, first anchor text wins."""
    out: dict[str, dict] = {}
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        base = resolve(base, _attr(base_node, "href")) or base
    for a in tree.css("a[href]"):
        u = resolve(base, _attr(a, "href"))
        if not u:
            continue
        text = text_of(a) or _attr(a, "title") or _attr(a, "aria-label")
        if u not in out:
            out[u] = {"url": u, "text": text[:200], "rel": _attr(a, "rel")}
        elif not out[u]["text"] and text:
            out[u]["text"] = text[:200]
    return list(out.values())


def tables(tree) -> list[dict]:
    """Data tables as {caption, rows}; colspans are repeated, layout tables (nested or single-cell) skipped."""
    out = []
    for t in tree.css("table"):
        if len(t.css("table")) > 1:  # css() includes the node itself
            continue
        rows: list[list[str]] = []
        for tr in t.css("tr"):
            row: list[str] = []
            for cell in tr.iter():
                if cell.tag not in ("td", "th"):
                    continue
                txt = text_of(cell)
                try:
                    span = max(1, min(20, int(_attr(cell, "colspan") or 1)))
                except ValueError:
                    span = 1
                row.extend([txt] + [""] * (span - 1))
            if any(c for c in row):
                rows.append(row)
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        cap = t.css_first("caption")
        out.append({"caption": text_of(cap), "rows": rows})
    return out


def images(tree, base: str) -> list[dict]:
    out, seen = [], set()
    for img in tree.css("img"):
        src = _attr(img, "src") or _attr(img, "data-src") or _attr(img, "data-original")
        if not src or src.startswith("data:"):
            srcset = _attr(img, "srcset") or _attr(img, "data-srcset")
            if srcset:
                src = srcset.split(",")[-1].strip().split(" ")[0]
        u = resolve(base, src)
        if not u or u in seen:
            continue
        seen.add(u)
        w, h = _attr(img, "width"), _attr(img, "height")
        out.append({"url": u, "alt": clean(_attr(img, "alt"))[:200], "width": int(w) if w.isdigit() else None, "height": int(h) if h.isdigit() else None})
    for node in tree.css('meta[property="og:image"]'):
        u = resolve(base, _attr(node, "content"))
        if u and u not in seen:
            seen.add(u)
            out.append({"url": u, "alt": "og:image", "width": None, "height": None})
    return out


def visible_text_len(node) -> int:
    """Characters of text in a tree or element, not counting scripts, styles and templates."""
    root = getattr(node, "body", node)
    if root is None:
        return 0
    txt = root.text(deep=True, separator=" ") or ""
    hidden = sum(len(clean(s.text(deep=True, separator=" "))) for s in root.css("script, style, noscript, template"))
    return max(0, len(clean(txt)) - hidden)


BOILER_HEADING = re.compile(
    r"^(about the authors?|(the )?latest( news| stories)?|related( articles| stories| posts| content)?|read (more|next)|more from\b.*|"
    r"you (may|might) also like|recommended( for you)?|popular|trending|most read|share( this)?|comments?|leave a (comment|reply)|"
    r"newsletter|subscribe.*|sign up.*|advertisement|tags|topics|follow us.*|table of contents|contents|on this page|in this article|"
    r"must[- ]reads?( stories)?|more stories( by .*)?|(top|recommended|featured|related|latest) (stories|news|videos|posts|articles)|"
    r"editor'?s picks|up next|what to read next|trending now|don'?t miss|partner content|sponsored( content)?|read more about.*|"
    r"(no |\d+ )?comments?|submit a comment|join the (conversation|discussion)|sidebar|share (this|on).*|more (in|on) .*)[:.]?$",
    re.I,
)


FURNITURE = re.compile(r"(^|[\s_-])(comments?|related|sidebar|newsletter|promo|share|sharing|social|author-?(bio|box|info|card)|recommend(ed|ations)?|popular|trending|outbrain|taboola|more-stories|read-?more|read-?next|subscribe|advert|ad-slot)([\s_-]|$)", re.I)


def headings(node) -> list[str]:
    """The h2–h4 content headings of an element: not in navigation, headers, footers or asides, not links to other
    pages (teasers for other articles), and not the usual 'Related articles', 'About the author', 'Newsletter'
    furniture. (h1 is the page title, which the readers handle separately.)"""
    out = []
    for h in node.css("h2, h3, h4"):
        p, skip = h.parent, False
        while p is not None and p != node:  # only the containers inside the element count (selectolax: == not is)
            site_header = p.tag == "header" and p.parent is not None and p.parent.tag in ("body", "html")
            if p.tag in ("nav", "footer", "aside", "a") or site_header or FURNITURE.search(f"{_attr(p, 'id')} {_attr(p, 'class')}"):
                skip = True
                break
            p = p.parent
        link = h.css_first("a[href]")
        if link is not None and not _attr(link, "href").startswith("#") and len(text_of(link)) >= 0.8 * len(text_of(h)):
            skip = True
        t = strip_marks(text_of(h))
        if t and not skip and not BOILER_HEADING.match(t):
            out.append(t)
    return out


def text_outside_links(node) -> str:
    """An element's text without links (teasers for other pages), scripts and styles."""
    if node is None:
        return ""
    tree = parse(node.html or "")
    for sel in ("a", "script", "style", "noscript", "template", "nav", "aside", "footer"):
        for n in tree.css(sel):
            n.decompose()
    return clean((tree.body or tree.root).text(deep=True, separator=" "))


def needs_js(html: str, extracted_chars: int) -> bool:
    """True when the page looks like an app shell that only renders with JavaScript."""
    if extracted_chars > 600:
        return False
    low = html[:200_000].lower()
    hints = ("enable javascript", "requires javascript", "you need to enable javascript", "javascript is disabled", "please turn on javascript", "this app works best with javascript")
    if any(h in low for h in hints):
        return True
    scripts = low.count("<script")
    shell = re.search(r'<div id="(root|app|__next|__nuxt|svelte|main-app)"[^>]*>\s*</div>', low) is not None
    return shell or (scripts >= 3 and extracted_chars < 200)


# ── HTML → Markdown (fallback and --full) ──────────────────────────────


def main_node(tree):
    """<main>, the biggest <article>, [role=main], a #content-like block, else <body>."""
    for sel in ("main", '[role="main"]'):
        node = tree.css_first(sel)
        if node is not None and len(text_of(node)) > 200:
            return node
    arts = tree.css("article")
    if arts:
        best = max(arts, key=lambda a: len(a.text(deep=True) or ""))
        if len(text_of(best)) > 200:
            return best
    for sel in ("#content", "#main", ".content", "#main-content", ".post", ".entry-content", ".article-body"):
        node = tree.css_first(sel)
        if node is not None and len(text_of(node)) > 200:
            return node
    return tree.body or tree.root


def to_markdown(node, base: str, inline_links: bool = False, drop_boilerplate: bool = True) -> str:
    """A readable Markdown rendering of an element: headings, paragraphs, lists, code, quotes, tables, links."""
    if node is None:
        return ""
    conv = _Converter(base, inline_links, drop_boilerplate)
    conv.block(node, 0)
    conv.flush()
    text = "\n\n".join(b for b in conv.blocks if b.strip())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _Converter:
    def __init__(self, base: str, inline_links: bool, drop_boilerplate: bool):
        self.base = base
        self.inline_links = inline_links
        self.drop = drop_boilerplate
        self.blocks: list[str] = []
        self.buf: list[str] = []

    def flush(self) -> None:
        s = tidy("".join(self.buf))
        self.buf = []
        if s:
            self.blocks.append(s)

    def _skip(self, n) -> bool:
        tag = n.tag
        if tag in SKIP_TAGS:
            return True
        if self.drop and tag in BOILERPLATE:
            return True
        if _attr(n, "hidden") or _attr(n, "aria-hidden") == "true":
            return True
        style = _attr(n, "style").replace(" ", "").lower()
        return "display:none" in style or "visibility:hidden" in style

    def inline(self, n) -> str:
        parts: list[str] = []
        for c in n.iter(include_text=True):
            tag = c.tag
            if tag == "-text":
                parts.append(c.text_content or "")
                continue
            if self._skip(c):
                continue
            if tag == "br":
                parts.append("  \n")
            elif tag in ("b", "strong"):
                s = self.inline(c).strip()
                parts.append(f" **{s}** " if s else "")
            elif tag in ("i", "em", "cite"):
                s = self.inline(c).strip()
                parts.append(f" *{s}* " if s else "")
            elif tag in ("code", "kbd", "samp", "tt"):
                s = clean(c.text(deep=True))
                parts.append(f" `{s}` " if s else "")
            elif tag == "a":
                s = self.inline(c).strip()
                href = resolve(self.base, _attr(c, "href"))
                parts.append(f" [{s}]({href}) " if (self.inline_links and href and s) else f" {s} ")
            elif tag == "img":
                alt = clean(_attr(c, "alt"))
                if alt:
                    parts.append(f" [image: {alt}] ")
            elif tag in ("sup", "sub"):
                s = clean(self.inline(c))
                if not re.fullmatch(r"\[(\d{1,4}|[a-z]{1,2}|(note|nb|n)\s*\d{1,3})\]", s, re.I):  # not a citation marker
                    parts.append(("^" if tag == "sup" and re.fullmatch(r"[-+−]?\d{1,3}|n", s) else "") + s)
            else:
                parts.append(self.inline(c))
        return "".join(parts)

    def block(self, n, depth: int) -> None:
        for c in n.iter(include_text=True):
            tag = c.tag
            if tag == "-text":
                self.buf.append(c.text_content or "")
                continue
            if tag == "-comment" or self._skip(c):
                continue
            if tag not in BLOCK_TAGS:
                if tag in ("b", "strong", "i", "em", "code", "a", "span", "img", "br", "abbr", "small", "sup", "sub", "mark", "time", "q", "s", "u", "del", "ins", "label", "cite", "kbd", "var", "font"):
                    self.buf.append(self.inline(_Wrap(c)))
                else:
                    self.block(c, depth)
                continue
            self.flush()
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                s = tidy(self.inline(c))
                if s:
                    self.blocks.append("#" * int(tag[1]) + " " + s)
            elif tag == "p":
                s = tidy(self.inline(c))
                if s:
                    self.blocks.append(s)
            elif tag in ("ul", "ol", "menu"):
                self.blocks.append(self.list_md(c, depth, tag == "ol"))
            elif tag == "pre":
                code = (c.text(deep=True) or "").strip("\n")
                lang = ""
                cn = c.css_first("code")
                cls = " ".join(_attr(x, "class") for x in (cn, c, c.parent, c.parent.parent if c.parent is not None else None) if x is not None)
                mm = re.search(r"(?:language|lang|highlight(?:-source)?)-([a-z][\w+-]*)", cls)
                if mm:
                    lang = mm.group(1)
                self.blocks.append(f"```{lang}\n{code}\n```")
            elif tag == "blockquote":
                sub = _Converter(self.base, self.inline_links, self.drop)
                sub.block(c, depth)
                sub.flush()
                if sub.blocks and all(b.startswith("```") for b in sub.blocks):
                    self.blocks.extend(sub.blocks)  # a quoted example that is only code: keep it a plain code block
                else:
                    inner = "\n\n".join(sub.blocks)
                    self.blocks.append("\n".join("> " + ln if ln else ">" for ln in inner.splitlines()))
            elif tag == "table":
                self.blocks.append(self.table_md(c))
            elif tag == "hr":
                self.blocks.append("---")
            elif tag == "dt":
                s = clean(self.inline(c))
                if s:
                    self.blocks.append(f"**{s}**")
            elif tag == "dd":
                self.block(c, depth)
                self.flush()
            elif tag in ("figcaption", "caption", "summary"):
                s = clean(self.inline(c))
                if s:
                    self.blocks.append(f"*{s}*")
            else:
                self.block(c, depth)
                self.flush()

    def list_md(self, node, depth: int, ordered: bool) -> str:
        """A list whose items may hold paragraphs, code blocks and nested lists (indented under the marker)."""
        items = []
        i = 0
        for li in node.iter():
            if li.tag != "li":
                continue
            i += 1
            marker = f"{i}." if ordered else "-"
            sub = _Converter(self.base, self.inline_links, self.drop)
            sub.block(li, depth + 1)
            sub.flush()
            blocks = [b for b in sub.blocks if b.strip()] or [""]
            pad = " " * (len(marker) + 1)
            first = blocks[0].splitlines() or [""]
            out = [f"{marker} {first[0]}"] + [pad + ln if ln else "" for ln in first[1:]]
            for b in blocks[1:]:
                out += [pad + ln if ln else "" for ln in b.splitlines()]
            items.append("\n".join(out))
        return "\n".join(items)

    def table_md(self, node) -> str:
        rows = []
        for tr in node.css("tr"):
            cells = [clean(self.inline(td)).replace("|", "\\|") for td in tr.iter() if td.tag in ("td", "th")]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head, *body = rows
        out = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
        out += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(out)


class _Wrap:
    """Lets inline() treat a single element as a container of itself."""

    def __init__(self, node):
        self.node = node

    def iter(self, include_text: bool = True) -> Iterator[Any]:
        yield self.node
