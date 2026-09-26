"""Keyless search engines for web-research: one request builder and one parser per engine, isolated so a markup
change breaks one engine rather than search. Parsers are pure functions over the response text (the self-test runs
them on saved results pages); runners add the polite request and classify failures.

Groups: web (Bing, DuckDuckGo, Marginalia, Mwmbl), news (Google News, Bing News), papers (OpenAlex, arXiv, Crossref,
PubMed), code (GitHub, Stack Overflow, Hacker News), forums (Reddit), packages (PyPI, npm, crates.io), wiki
(Wikipedia, Wikidata), books (Open Library), places (OpenStreetMap Nominatim), video (YouTube).
"""

from __future__ import annotations

import base64
import html as htmllib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, quote, quote_plus, urlencode, urlsplit

import _net
from _common import SkillError


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""
    date: str = ""
    engine: str = ""
    rank: int = 0
    group: str = "web"
    extra: dict = field(default_factory=dict)


@dataclass
class Query:
    q: str
    max: int = 10
    site: str | None = None
    filetype: str | None = None
    since: str | None = None  # day | week | month | year | YYYY-MM-DD
    lang: str | None = None
    region: str | None = None
    cache: bool = True

    def since_date(self) -> date | None:
        return since_to_date(self.since)

    def since_days(self) -> int | None:
        d = self.since_date()
        return None if d is None else max(1, (datetime.now(timezone.utc).date() - d).days)

    def web_query(self) -> str:
        s = self.q
        if self.site:
            s += f" site:{self.site}"
        if self.filetype:
            s += f" filetype:{self.filetype.lstrip('.')}"
        return s


class EngineError(Exception):
    """An engine that did not answer usefully; cooldown is how long to leave it alone (seconds)."""

    def __init__(self, reason: str, cooldown: float = 0.0, status: str = "down"):
        super().__init__(reason)
        self.reason = reason
        self.cooldown = cooldown
        self.status = status


@dataclass
class Engine:
    name: str
    group: str
    run: Callable[[Query], list[Hit]]
    weight: float = 1.0
    default: bool = True
    probe: str = "python asyncio tutorial"
    host: str = ""
    note: str = ""


ENGINES: dict[str, Engine] = {}
GROUPS = ["web", "news", "papers", "code", "forums", "packages", "wiki", "books", "places", "video"]


def engine(name: str, group: str, *, weight: float = 1.0, default: bool = True, probe: str | None = None, host: str = "", note: str = ""):
    def deco(fn: Callable[[Query], list[Hit]]):
        ENGINES[name] = Engine(name, group, fn, weight, default, probe or DEFAULT_PROBES.get(group, "python asyncio tutorial"), host, note)
        return fn

    return deco


DEFAULT_PROBES = {
    "web": "python asyncio tutorial",
    "news": "climate policy",
    "papers": "retrieval augmented generation",
    "code": "sqlite full text search",
    "forums": "sqlite fts5",
    "packages": "httpx",
    "wiki": "Marie Curie",
    "books": "dune herbert",
    "places": "Eiffel Tower",
    "video": "python asyncio tutorial",
}


# ── helpers ─────────────────────────────────────────────────────────────


def since_to_date(since: str | None) -> date | None:
    if not since:
        return None
    s = since.strip().lower()
    today = datetime.now(timezone.utc).date()
    rel = {"day": 1, "24h": 1, "today": 1, "week": 7, "month": 31, "year": 366}
    if s in rel:
        return today - timedelta(days=rel[s])
    m = re.fullmatch(r"(\d+)\s*(d|days?|w|weeks?|m|months?|y|years?)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        return today - timedelta(days=n * {"d": 1, "w": 7, "m": 31, "y": 366}[unit])
    try:
        return date.fromisoformat(s[:10]) if len(s) >= 10 else date.fromisoformat(s + "-01-01" if len(s) == 4 else s + "-01")
    except ValueError as e:
        raise SkillError(f"--since takes day, week, month, year, 7d, 3m, or a date like 2026-01-31 (got {since!r})") from e


def parse_date(v: Any) -> date | None:
    """Most date shapes engines return → a date (None when unknown)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        import email.utils

        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4})(?:[ /-](\w{3,9}|\d{1,2}))?(?:[ /-](\d{1,2}))?", s)
    if m:
        y = int(m.group(1))
        mon = m.group(2)
        months = {k: i for i, k in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
        mo = int(mon) if mon and mon.isdigit() else months.get((mon or "")[:3].lower(), 1)
        try:
            return date(y, max(1, min(12, mo)), int(m.group(3) or 1))
        except ValueError:
            return None
    return None


def iso_date(v: Any) -> str:
    d = parse_date(v)
    return d.isoformat() if d else ""


# [^<>]: a tag never spans another "<", so a stray "<" stays text and "<a<a<a…" is not quadratic.
INLINE_TAG = re.compile(r"</?(span|b|i|em|strong|a|sup|sub|mark|u|small|code|font)\b[^<>]*>", re.I)


def strip_tags(s: str) -> str:
    """HTML → text: inline tags vanish, other tags become spaces."""
    s = INLINE_TAG.sub("", s or "")
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^<>]+>", " ", s))).strip()


def short(s: str, n: int = 300) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _t(node) -> str:
    return re.sub(r"\s+", " ", node.text(deep=True) or "").strip() if node is not None else ""


def fetch(engine_name: str, url: str, q: Query, *, browser: bool = False, accept: str | None = None, timeout: float = 8.0, max_bytes: int = 8 * 1024 * 1024) -> _net.Response:
    """A polite engine request with failures classified for the health table."""
    headers = {"User-Agent": _net.BROWSER_UA if browser else _net.API_UA}
    if accept:
        headers["Accept"] = accept
    elif browser:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    if q.lang:
        headers["Accept-Language"] = f"{q.lang},{q.lang.split('-')[0]};q=0.9,en;q=0.5"
    try:
        r = _net.request(url, kind="engine", headers=headers, ttl=_net.SEARCH_TTL, timeout=timeout, retries=0, cache=q.cache, max_bytes=max_bytes, rate_key=f"engine:{engine_name}")
    except _net.NetError as e:
        if e.kind == "timeout":
            raise EngineError(f"timed out after {timeout:.0f}s", 300, "timeout") from None
        raise EngineError(e.reason, 120, "down") from None
    if r.status == 202:
        raise EngineError("throttled (202 anomaly page)", 900, "throttled")
    if r.status == 429:
        ra = _net._retry_after(r.headers.get("retry-after"))
        raise EngineError("rate limited (429)", max(ra or 0, 600), "throttled")
    blocked = r.blocked()
    if blocked:
        raise EngineError(blocked, 1800, "blocked")
    if r.status >= 500:
        raise EngineError(f"HTTP {r.status}", 120, "down")
    if r.status >= 400:
        raise EngineError(f"HTTP {r.status}", 300, "down")
    return r


def _json(r: _net.Response) -> Any:
    try:
        return json.loads(r.body.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError as e:
        raise EngineError(f"unexpected answer (not JSON: {e})", 300, "broken") from None


def _xml(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as e:
        raise EngineError(f"unexpected answer (not XML: {e})", 300, "broken") from None


# ── web: Bing ───────────────────────────────────────────────────────────


#: Words that mark a query as not English (Bing then keeps the market it picks for the IP, as without the default).
_NOT_ENGLISH = re.compile(
    r"\b(le|les|du|une|avec|dans|pour|sur|qui|est|der|das|und|ist|mit|für|ein|eine|nicht|los|las|para|por|una|della|gli|che|não|"
    r"uma|het|een|niet|och|är|att|på)\b",
    re.I,
)


def english_default(q: Query) -> bool:
    """No --lang/--region and an English-looking query: Bing is asked for its US-English results. Anonymous Bing
    otherwise picks the market from the IP address (French results for an English query from France)."""
    return not q.lang and not q.region and q.q.isascii() and not _NOT_ENGLISH.search(q.q)


def bing_params(q: Query, count: int | None = None) -> dict:
    p: dict[str, str] = {"q": q.web_query()}
    if count:
        p["count"] = str(count)
    if english_default(q):
        p.update({"setlang": "en", "cc": "US", "mkt": "en-US"})
    if q.lang:
        p["setlang"] = q.lang
    if q.region:
        p["cc"] = q.region.upper()
        if q.lang:
            p["mkt"] = f"{q.lang.split('-')[0]}-{q.region.upper()}"
    if q.since:
        rel = {"day": "ez1", "24h": "ez1", "today": "ez1", "week": "ez2", "month": "ez3"}.get(q.since.lower())
        if rel:
            p["filters"] = f'ex1:"{rel}"'
        else:
            d = q.since_date()
            if d:
                epoch = date(1970, 1, 1)
                p["filters"] = f'ex1:"ez5_{(d - epoch).days}_{(datetime.now(timezone.utc).date() - epoch).days}"'
    return p


def parse_bing_rss(text: str) -> list[Hit]:
    root = _xml(text)
    out = []
    for i, item in enumerate(root.iter("item"), 1):
        link = (item.findtext("link") or "").strip()
        if not link.startswith("http"):
            continue
        out.append(Hit(title=strip_tags(item.findtext("title") or ""), url=link, snippet=short(strip_tags(item.findtext("description") or "")), rank=i))
    return out


@engine("bing", "web", weight=0.8, host="www.bing.com", note="Bing's RSS results; its HTML page when RSS fails")
def run_bing(q: Query) -> list[Hit]:
    st = _net.store()
    if not st.cooling("bing-rss"):
        try:
            r = fetch("bing", "https://www.bing.com/search?" + urlencode({**bing_params(q), "format": "rss"}), q, browser=True)
            hits = parse_bing_rss(r.text)
            if hits:
                return hits
        except EngineError as e:
            if e.status in ("throttled", "blocked"):
                raise
            st.mark("bing-rss", e.cooldown or 300, e.reason)
    return run_bing_html(q)


def decode_bing_url(href: str) -> str:
    """Bing wraps results as /ck/a?…&u=a1<base64url>; returns the target."""
    if "bing.com/ck/a" not in href:
        return href
    u = (parse_qs(urlsplit(htmllib.unescape(href)).query).get("u") or [""])[0]
    if u.startswith("a1"):
        u = u[2:]
        try:
            return base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            return href
    return href


def parse_bing_html(text: str) -> list[Hit]:
    import _html

    tree = _html.parse(text)
    out = []
    for li in tree.css("li.b_algo"):
        a = li.css_first("h2 a")
        if a is None:
            continue
        url = decode_bing_url(a.attributes.get("href") or "")
        if not url.startswith("http") or "bing.com/" in url.split("?")[0]:
            continue
        p = li.css_first(".b_caption p") or li.css_first("p")
        snippet = _t(p)
        snippet = re.sub(r"\s*Read more$", "", snippet)
        m = re.match(r"^(\d{1,2} \w{3,9} \d{4}|\w{3,9} \d{1,2}, \d{4})\s*[·—-]\s*", snippet)
        d = ""
        if m:
            d = iso_date(m.group(1))
            snippet = snippet[m.end() :]
        out.append(Hit(title=_t(a), url=url, snippet=short(snippet), date=d, rank=len(out) + 1))
    return out


@engine("bing-html", "web", weight=0.8, default=False, host="www.bing.com", note="Bing's HTML results page (bing falls back to it)")
def run_bing_html(q: Query) -> list[Hit]:
    r = fetch("bing", "https://www.bing.com/search?" + urlencode(bing_params(q)), q, browser=True)
    hits = parse_bing_html(r.text)
    if not hits and "b_algo" not in r.text and "b_results" not in r.text:
        raise EngineError("unexpected page (no results list)", 600, "broken")
    return hits


# ── web: DuckDuckGo ─────────────────────────────────────────────────────


def ddg_params(q: Query) -> dict:
    p = {"q": q.web_query()}
    if q.region or q.lang:
        region = (q.region or "us").lower().replace("gb", "uk")
        p["kl"] = f"{region}-{(q.lang or 'en').split('-')[0].lower()}"
    if q.since:
        rel = {"day": "d", "24h": "d", "today": "d", "week": "w", "month": "m", "year": "y"}.get(q.since.lower())
        if rel:
            p["df"] = rel
        else:
            d = q.since_date()
            if d:
                p["df"] = f"{d.isoformat()}..{datetime.now(timezone.utc).date().isoformat()}"
    return p


def decode_ddg_url(href: str) -> str:
    href = htmllib.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        target = (parse_qs(urlsplit(href).query).get("uddg") or [""])[0]
        return target or href
    return href


def _is_ddg_ad(url: str) -> bool:
    h = _net.host(url)
    return h.endswith("duckduckgo.com") or "ad_domain=" in url or "bing.com/aclick" in url


def parse_ddg_html(text: str) -> list[Hit]:
    import _html

    tree = _html.parse(text)
    out = []
    for r in tree.css("div.result"):
        cls = r.attributes.get("class") or ""
        if "result--ad" in cls:
            continue
        a = r.css_first("a.result__a")
        if a is None:
            continue
        url = decode_ddg_url(a.attributes.get("href") or "")
        if not url.startswith("http") or _is_ddg_ad(url):
            continue
        d = ""
        extras = r.css_first(".result__extras__url")
        if extras is not None:
            m = re.search(r"(\d{4}-\d{2}-\d{2})T", _t(extras))
            d = m.group(1) if m else ""
        out.append(Hit(title=_t(a), url=url, snippet=short(_t(r.css_first(".result__snippet"))), date=d, rank=len(out) + 1))
    return out


def parse_ddg_lite(text: str) -> list[Hit]:
    import _html

    tree = _html.parse(text)
    out: list[Hit] = []
    for a in tree.css("a.result-link"):
        url = decode_ddg_url(a.attributes.get("href") or "")
        if not url.startswith("http") or _is_ddg_ad(url):
            continue
        tr = a.parent
        while tr is not None and tr.tag != "tr":
            tr = tr.parent
        snippet, d = "", ""
        node = tr.next if tr is not None else None
        seen = 0
        while node is not None and seen < 6:
            if node.tag == "tr":
                seen += 1
                if node.css_first("a.result-link") is not None:
                    break
                sn = node.css_first("td.result-snippet")
                if sn is not None:
                    snippet = _t(sn)
                ts = node.css_first("span.timestamp")
                if ts is not None:
                    d = _t(ts)[:10]
            node = node.next
        out.append(Hit(title=_t(a), url=url, snippet=short(snippet), date=d if re.match(r"\d{4}-\d{2}-\d{2}", d) else "", rank=len(out) + 1))
    return out


@engine("ddg", "web", weight=1.0, host="html.duckduckgo.com", note="DuckDuckGo's HTML page; its lite page when throttled")
def run_ddg(q: Query) -> list[Hit]:
    st = _net.store()
    first: EngineError | None = None
    if not st.cooling("ddg-html"):
        try:
            return run_ddg_html(q)
        except EngineError as e:
            first = e
            if e.cooldown:
                st.mark("ddg-html", e.cooldown, e.reason)  # later searches go straight to the lite page
    try:
        return run_ddg_lite(q)
    except EngineError as e:
        raise (first or e) from None


@engine("ddg-html", "web", default=False, host="html.duckduckgo.com")
def run_ddg_html(q: Query) -> list[Hit]:
    r = fetch("ddg-html", "https://html.duckduckgo.com/html/?" + urlencode(ddg_params(q)), q, browser=True)
    hits = parse_ddg_html(r.text)
    if not hits and "result__a" not in r.text and "No results" not in r.text and "no-results" not in r.text:
        raise EngineError("unexpected page (no results list)", 600, "broken")
    return hits


@engine("ddg-lite", "web", default=False, host="lite.duckduckgo.com")
def run_ddg_lite(q: Query) -> list[Hit]:
    r = fetch("ddg-lite", "https://lite.duckduckgo.com/lite/?" + urlencode(ddg_params(q)), q, browser=True)
    hits = parse_ddg_lite(r.text)
    if not hits and "result-link" not in r.text and "No results" not in r.text:
        raise EngineError("unexpected page (no results list)", 600, "broken")
    return hits


# ── web: Marginalia and Mwmbl (independent indexes) ─────────────────────


def parse_marginalia(data: Any) -> list[Hit]:
    out = []
    for i, r in enumerate((data or {}).get("results") or [], 1):
        url = r.get("url") or ""
        if url.startswith("http"):
            out.append(Hit(title=strip_tags(r.get("title") or url), url=url, snippet=short(strip_tags(r.get("description") or "")), rank=i, extra={"quality": r.get("quality")}))
    return out


@engine("marginalia", "web", weight=0.5, host="api.marginalia.nu", note="Marginalia's independent index of the small, non-commercial web")
def run_marginalia(q: Query) -> list[Hit]:
    url = f"https://api.marginalia.nu/public/search/{quote(q.web_query(), safe='')}?" + urlencode({"count": min(q.max * 2, 40)})
    return parse_marginalia(_json(fetch("marginalia", url, q, accept="application/json", timeout=6)))


def _runs(v: Any) -> str:
    if isinstance(v, list):
        return "".join(str(x.get("value", "")) if isinstance(x, dict) else str(x) for x in v)
    return str(v or "")


def parse_mwmbl(data: Any) -> list[Hit]:
    out = []
    for i, r in enumerate(data if isinstance(data, list) else [], 1):
        url = r.get("url") or ""
        if url.startswith("http"):
            out.append(Hit(title=short(_runs(r.get("title")), 200) or url, url=url, snippet=short(_runs(r.get("extract"))), rank=i))
    return out


@engine("mwmbl", "web", weight=0.4, host="api.mwmbl.org", note="Mwmbl's open, community-crawled index")
def run_mwmbl(q: Query) -> list[Hit]:
    return parse_mwmbl(_json(fetch("mwmbl", "https://api.mwmbl.org/search?" + urlencode({"s": q.web_query()}), q, accept="application/json", timeout=6)))


# ── news ────────────────────────────────────────────────────────────────


def parse_google_news(text: str) -> list[Hit]:
    root = _xml(text)
    out = []
    for i, item in enumerate(root.iter("item"), 1):
        title = strip_tags(item.findtext("title") or "")
        src = item.find("source")
        source = (src.text or "").strip() if src is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append(Hit(
            title=title,
            url=(item.findtext("link") or "").strip(),
            snippet=f"{source}" if source else "",
            date=iso_date(item.findtext("pubDate")),
            rank=i,
            extra={"source": source, "source_url": src.get("url", "") if src is not None else "", "via": "Google News (the link redirects with JavaScript)"},
        ))
    return out


@engine("google-news", "news", host="news.google.com", note="Google News RSS (links redirect through Google)")
def run_google_news(q: Query) -> list[Hit]:
    query = q.web_query()
    if q.since:
        rel = {"day": "1d", "24h": "1d", "today": "1d", "week": "7d", "month": "30d", "year": "1y"}.get(q.since.lower())
        d = q.since_date()
        query += f" when:{rel}" if rel else (f" after:{d.isoformat()}" if d else "")
    lang = (q.lang or "en").split("-")[0]
    region = (q.region or "US").upper()
    url = "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": f"{lang}-{region}" if lang == "en" else lang, "gl": region, "ceid": f"{region}:{lang}"})
    return parse_google_news(fetch("google-news", url, q, browser=True).text)[: q.max * 2]


def parse_bing_news(text: str) -> list[Hit]:
    root = _xml(text)
    out = []
    for i, item in enumerate(root.iter("item"), 1):
        link = (item.findtext("link") or "").strip()
        if "apiclick.aspx" in link:
            link = (parse_qs(urlsplit(link).query).get("url") or [link])[0]
        source = ""
        for child in item:
            if child.tag.endswith("Source"):
                source = (child.text or "").strip()
        out.append(Hit(title=strip_tags(item.findtext("title") or ""), url=link, snippet=short((f"{source}: " if source else "") + strip_tags(item.findtext("description") or "")), date=iso_date(item.findtext("pubDate")), rank=i, extra={"source": source}))
    return out


@engine("bing-news", "news", host="www.bing.com", note="Bing News RSS (direct article links)")
def run_bing_news(q: Query) -> list[Hit]:
    p = {"q": q.web_query(), "format": "rss"}
    if q.since:
        interval = {"day": "7", "24h": "7", "today": "7", "week": "8", "month": "9"}.get(q.since.lower())
        if interval:
            p["qft"] = f'interval="{interval}"'
    if english_default(q):
        p.update({"setlang": "en", "cc": "US", "mkt": "en-US"})
    if q.lang:
        p["setlang"] = q.lang
    if q.region:
        p["cc"] = q.region.upper()
    return parse_bing_news(fetch("bing-news", "https://www.bing.com/news/search?" + urlencode(p), q, browser=True).text)


# ── papers ──────────────────────────────────────────────────────────────


def openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: list[tuple[int, str]] = [(i, w) for w, idx in inv.items() for i in idx]
    return " ".join(w for _, w in sorted(pos))


def _authors(names: list[str], n: int = 3) -> str:
    names = [x for x in names if x]
    return ", ".join(names[:n]) + (" et al." if len(names) > n else "")


def parse_openalex(data: Any) -> list[Hit]:
    out = []
    for i, w in enumerate((data or {}).get("results") or [], 1):
        doi = w.get("doi") or ""
        loc = w.get("primary_location") or {}
        src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
        oa = w.get("open_access") or {}
        url = doi or (loc.get("landing_page_url") if isinstance(loc, dict) else "") or w.get("id", "")
        authors = [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])]
        venue = src.get("display_name") or ""
        cites = w.get("cited_by_count")
        abstract = openalex_abstract(w.get("abstract_inverted_index"))
        meta = " · ".join(x for x in (_authors(authors), venue, str(w.get("publication_year") or ""), f"cited by {cites}" if cites is not None else "") if x)
        out.append(Hit(
            title=strip_tags(w.get("display_name") or w.get("title") or ""),
            url=url,
            snippet=short(meta + (f" — {abstract}" if abstract else ""), 360),
            date=w.get("publication_date") or str(w.get("publication_year") or ""),
            rank=i,
            extra={"doi": doi.replace("https://doi.org/", ""), "authors": authors[:10], "venue": venue, "cited_by": cites, "open_access_url": oa.get("oa_url") or "", "type": w.get("type") or "", "openalex": w.get("id", "")},
        ))
    return out


@engine("openalex", "papers", host="api.openalex.org", note="OpenAlex: 250M+ scholarly works, citations, open-access links")
def run_openalex(q: Query) -> list[Hit]:
    p = {"search": q.q, "per-page": str(min(q.max, 25)), "select": "id,doi,title,display_name,publication_year,publication_date,authorships,primary_location,cited_by_count,open_access,type,abstract_inverted_index"}
    d = q.since_date()
    if d:
        p["filter"] = f"from_publication_date:{d.isoformat()}"
    return parse_openalex(_json(fetch("openalex", "https://api.openalex.org/works?" + urlencode(p), q, accept="application/json", timeout=10)))


ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def parse_arxiv(text: str) -> list[Hit]:
    root = _xml(text)
    out = []
    for i, e in enumerate(root.iter(f"{ATOM}entry"), 1):
        abs_url = (e.findtext(f"{ATOM}id") or "").strip().replace("http://", "https://")
        pdf = ""
        for link in e.findall(f"{ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf = link.get("href", "")
        authors = [(a.findtext(f"{ATOM}name") or "").strip() for a in e.findall(f"{ATOM}author")]
        cat = e.find(f"{ARXIV}primary_category")
        summary = re.sub(r"\s+", " ", e.findtext(f"{ATOM}summary") or "").strip()
        pub = (e.findtext(f"{ATOM}published") or "")[:10]
        out.append(Hit(
            title=re.sub(r"\s+", " ", e.findtext(f"{ATOM}title") or "").strip(),
            url=abs_url,
            snippet=short(f"{_authors(authors)} · arXiv {cat.get('term') if cat is not None else ''} — {summary}", 360),
            date=pub,
            rank=i,
            extra={"authors": authors[:10], "pdf": pdf.replace("http://", "https://"), "arxiv_id": abs_url.rsplit("/abs/", 1)[-1], "category": cat.get("term") if cat is not None else ""},
        ))
    return out


@engine("arxiv", "papers", host="export.arxiv.org", note="arXiv preprints (physics, maths, CS, biology, finance…)")
def run_arxiv(q: Query) -> list[Hit]:
    words = [w for w in re.findall(r"[\w'-]+", q.q) if len(w) > 1][:8]
    if not words:
        return []
    sq = " AND ".join(f"all:{w}" for w in words)
    d = q.since_date()
    if d:
        sq += f" AND submittedDate:[{d.strftime('%Y%m%d')}0000 TO 209912312359]"
    p = {"search_query": sq, "max_results": str(min(q.max, 25)), "sortBy": "relevance"}
    return parse_arxiv(fetch("arxiv", "https://export.arxiv.org/api/query?" + urlencode(p), q, timeout=12).text)


def parse_crossref(data: Any) -> list[Hit]:
    out = []
    for i, it in enumerate(((data or {}).get("message") or {}).get("items") or [], 1):
        doi = it.get("DOI") or ""
        title = strip_tags(" ".join(it.get("title") or []) or "")
        authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "") for a in (it.get("author") or [])]
        parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        d = "-".join(f"{p:02d}" if j else str(p) for j, p in enumerate(parts) if p) if parts and parts[0] else ""
        venue = " ".join(it.get("container-title") or []) or it.get("publisher", "")
        cites = it.get("is-referenced-by-count")
        abstract = strip_tags(it.get("abstract") or "")
        meta = " · ".join(x for x in (_authors(authors), venue, d[:4], f"cited by {cites}" if cites is not None else "", it.get("type", "")) if x)
        out.append(Hit(title=title, url=f"https://doi.org/{doi}" if doi else it.get("URL", ""), snippet=short(meta + (f" — {abstract}" if abstract else ""), 360), date=d, rank=i, extra={"doi": doi, "authors": authors[:10], "venue": venue, "cited_by": cites, "type": it.get("type", "")}))
    return out


@engine("crossref", "papers", host="api.crossref.org", note="Crossref DOI metadata for journals, books and proceedings")
def run_crossref(q: Query) -> list[Hit]:
    p = {"query": q.q, "rows": str(min(q.max, 25)), "select": "DOI,title,author,issued,container-title,type,URL,is-referenced-by-count,abstract,publisher"}
    d = q.since_date()
    if d:
        p["filter"] = f"from-pub-date:{d.isoformat()}"
    return parse_crossref(_json(fetch("crossref", "https://api.crossref.org/works?" + urlencode(p), q, accept="application/json", timeout=12)))


def parse_pubmed_summary(data: Any, ids: list[str]) -> list[Hit]:
    res = (data or {}).get("result") or {}
    out = []
    for i, pid in enumerate(ids, 1):
        r = res.get(pid)
        if not isinstance(r, dict):
            continue
        authors = [a.get("name", "") for a in r.get("authors") or []]
        doi = next((a.get("value") for a in r.get("articleids") or [] if a.get("idtype") == "doi"), "")
        meta = " · ".join(x for x in (_authors(authors), r.get("fulljournalname") or r.get("source", ""), r.get("pubdate", "")) if x)
        out.append(Hit(title=strip_tags(r.get("title", "")), url=f"https://pubmed.ncbi.nlm.nih.gov/{pid}/", snippet=short(meta, 300), date=iso_date(r.get("sortpubdate") or r.get("pubdate")), rank=i, extra={"pmid": pid, "doi": doi, "authors": authors[:10], "venue": r.get("fulljournalname") or r.get("source", "")}))
    return out


@engine("pubmed", "papers", host="eutils.ncbi.nlm.nih.gov", note="PubMed (biomedical literature) through NCBI E-utilities")
def run_pubmed(q: Query) -> list[Hit]:
    p = {"db": "pubmed", "term": q.q, "retmode": "json", "retmax": str(min(q.max, 20)), "sort": "relevance"}
    d = q.since_date()
    if d:
        p.update({"mindate": d.strftime("%Y/%m/%d"), "maxdate": "3000", "datetype": "pdat"})
    ids = (_json(fetch("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urlencode(p), q, accept="application/json")).get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return []
    s = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
    return parse_pubmed_summary(_json(fetch("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urlencode(s), q, accept="application/json")), ids)


# ── code ────────────────────────────────────────────────────────────────


def parse_github(data: Any) -> list[Hit]:
    out = []
    for i, r in enumerate((data or {}).get("items") or [], 1):
        lic = (r.get("license") or {}).get("spdx_id") or ""
        meta = " · ".join(x for x in (f"★ {r.get('stargazers_count', 0):,}", r.get("language") or "", lic if lic and lic != "NOASSERTION" else "", "archived" if r.get("archived") else "") if x)
        out.append(Hit(title=r.get("full_name", ""), url=r.get("html_url", ""), snippet=short(f"{meta} — {r.get('description') or ''}"), date=(r.get("pushed_at") or r.get("updated_at") or "")[:10], rank=i, extra={"stars": r.get("stargazers_count"), "language": r.get("language"), "license": lic, "topics": r.get("topics") or []}))
    return out


@engine("github", "code", host="api.github.com", note="GitHub repository search (unauthenticated: 10 searches a minute)")
def run_github(q: Query) -> list[Hit]:
    s = q.q
    d = q.since_date()
    if d:
        s += f" pushed:>={d.isoformat()}"
    p = {"q": s, "per_page": str(min(q.max, 20))}
    return parse_github(_json(fetch("github", "https://api.github.com/search/repositories?" + urlencode(p), q, accept="application/vnd.github+json")))


def parse_stackexchange(data: Any) -> list[Hit]:
    out = []
    for i, it in enumerate((data or {}).get("items") or [], 1):
        tags = ", ".join(it.get("tags") or [])
        state = "answered" if it.get("is_answered") else "unanswered"
        meta = f"score {it.get('score', 0)} · {it.get('answer_count', 0)} answers ({state}) · {tags}"
        out.append(Hit(title=strip_tags(it.get("title", "")), url=it.get("link", ""), snippet=short(meta), date=iso_date(it.get("last_activity_date") or it.get("creation_date")), rank=i, extra={"score": it.get("score"), "answers": it.get("answer_count"), "accepted": bool(it.get("accepted_answer_id")), "tags": it.get("tags") or []}))
    return out


@engine("stackoverflow", "code", host="api.stackexchange.com", note="Stack Overflow questions through the Stack Exchange API")
def run_stackoverflow(q: Query) -> list[Hit]:
    p = {"q": q.q, "site": "stackoverflow", "order": "desc", "sort": "relevance", "pagesize": str(min(q.max, 20))}
    d = q.since_date()
    if d:
        p["fromdate"] = str(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()))
    return parse_stackexchange(_json(fetch("stackoverflow", "https://api.stackexchange.com/2.3/search/advanced?" + urlencode(p), q, accept="application/json")))


def parse_hn(data: Any) -> list[Hit]:
    out = []
    for i, h in enumerate((data or {}).get("hits") or [], 1):
        item = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        title = h.get("title") or h.get("story_title") or ""
        if not title:
            continue
        meta = f"{h.get('points') or 0} points · {h.get('num_comments') or 0} comments · by {h.get('author', '')} · discussion: {item}"
        out.append(Hit(title=strip_tags(title), url=h.get("url") or item, snippet=short(meta), date=(h.get("created_at") or "")[:10], rank=i, extra={"points": h.get("points"), "comments": h.get("num_comments"), "discussion": item}))
    return out


@engine("hackernews", "code", host="hn.algolia.com", note="Hacker News stories through Algolia")
def run_hackernews(q: Query) -> list[Hit]:
    p = {"query": q.q, "hitsPerPage": str(min(q.max, 20)), "tags": "story"}
    d = q.since_date()
    if d:
        p["numericFilters"] = f"created_at_i>{int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())}"
    return parse_hn(_json(fetch("hackernews", "https://hn.algolia.com/api/v1/search?" + urlencode(p), q, accept="application/json")))


# ── forums ──────────────────────────────────────────────────────────────


def parse_reddit(text: str) -> list[Hit]:
    root = _xml(text)
    out = []
    for i, e in enumerate(root.iter(f"{ATOM}entry"), 1):
        link = e.find(f"{ATOM}link")
        url = link.get("href", "") if link is not None else ""
        if not url.startswith("http"):
            continue
        cat = e.find(f"{ATOM}category")
        sub = cat.get("label", "") if cat is not None else ""
        author = (e.findtext(f"{ATOM}author/{ATOM}name") or "").strip()
        body = strip_tags(e.findtext(f"{ATOM}content") or "")
        body = re.sub(r"\s*submitted by\s+/u/\S+.*$", "", body).strip()
        date = (e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated") or "")[:10]
        meta = " · ".join(x for x in (sub, author) if x)
        out.append(Hit(title=strip_tags(e.findtext(f"{ATOM}title") or ""), url=url, snippet=short(meta + (f" — {body}" if body else "")), date=date, rank=i, extra={"subreddit": sub}))
    return out


@engine("reddit", "forums", host="www.reddit.com", note="Reddit discussions (search RSS): experiences, opinions, comparisons")
def run_reddit(q: Query) -> list[Hit]:
    p = {"q": q.q + (f" site:{q.site}" if q.site else ""), "limit": str(min(q.max * 2, 25)), "sort": "relevance"}
    if q.since:
        t = {"day": "day", "24h": "day", "today": "day", "week": "week", "month": "month", "year": "year"}.get(q.since.lower())
        if t:
            p["t"] = t
    return parse_reddit(fetch("reddit", "https://www.reddit.com/search.rss?" + urlencode(p), q, accept="application/atom+xml").text)


# ── packages ────────────────────────────────────────────────────────────


def parse_pypi(data: Any) -> Hit | None:
    info = (data or {}).get("info") or {}
    if not info.get("name"):
        return None
    urls = (data or {}).get("urls") or []
    uploaded = (urls[0].get("upload_time_iso_8601") or urls[0].get("upload_time") or "")[:10] if urls else ""
    home = info.get("home_page") or next(iter((info.get("project_urls") or {}).values()), "") or ""
    meta = " · ".join(x for x in (f"v{info.get('version')}", f"Python {info['requires_python']}" if info.get("requires_python") else "", (info.get("license_expression") or info.get("license") or "")[:40], home) if x)
    return Hit(title=info["name"], url=info.get("package_url") or f"https://pypi.org/project/{info['name']}/", snippet=short(f"{info.get('summary') or ''} — {meta}"), date=uploaded, extra={"version": info.get("version"), "home": home, "requires_python": info.get("requires_python")})


def _package_names(query: str) -> list[str]:
    q = query.strip()
    cands = []
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,60}", q):
        cands.append(re.sub(r"[\s_.]+", "-", q.lower()))
        words = q.split()
        if 1 < len(words) <= 3:
            cands += [w.lower() for w in words if len(w) > 2 and w.lower() not in STOP]
    return list(dict.fromkeys(cands))[:3]


@engine("pypi", "packages", host="pypi.org", note="PyPI project pages for names in the query (PyPI has no keyword search API)")
def run_pypi(q: Query) -> list[Hit]:
    out = []
    for name in _package_names(q.q):
        try:
            r = fetch("pypi", f"https://pypi.org/pypi/{quote(name)}/json", q, accept="application/json", max_bytes=12 * 1024 * 1024)
        except EngineError as e:
            if "404" in e.reason:
                continue
            raise
        h = parse_pypi(_json(r))
        if h:
            h.rank = len(out) + 1
            out.append(h)
    return out


def parse_npm(data: Any) -> list[Hit]:
    out = []
    for i, o in enumerate((data or {}).get("objects") or [], 1):
        p = o.get("package") or {}
        dl = (o.get("downloads") or {}).get("monthly")
        meta = " · ".join(x for x in (f"v{p.get('version')}", f"{dl:,} downloads/month" if isinstance(dl, int) else "", (p.get("links") or {}).get("repository", "")) if x)
        out.append(Hit(title=p.get("name", ""), url=(p.get("links") or {}).get("npm") or f"https://www.npmjs.com/package/{p.get('name', '')}", snippet=short(f"{p.get('description') or ''} — {meta}"), date=(p.get("date") or o.get("updated") or "")[:10], rank=i, extra={"version": p.get("version"), "downloads_monthly": dl}))
    return out


@engine("npm", "packages", host="registry.npmjs.org", note="npm registry search")
def run_npm(q: Query) -> list[Hit]:
    return parse_npm(_json(fetch("npm", "https://registry.npmjs.org/-/v1/search?" + urlencode({"text": q.q, "size": str(min(q.max, 20))}), q, accept="application/json")))


def parse_crates(data: Any) -> list[Hit]:
    out = []
    for i, c in enumerate((data or {}).get("crates") or [], 1):
        meta = " · ".join(x for x in (f"v{c.get('max_stable_version') or c.get('max_version')}", f"{c.get('downloads', 0):,} downloads", c.get("repository") or "") if x)
        out.append(Hit(title=c.get("name", ""), url=f"https://crates.io/crates/{c.get('id') or c.get('name')}", snippet=short(f"{c.get('description') or ''} — {meta}"), date=(c.get("updated_at") or "")[:10], rank=i, extra={"downloads": c.get("downloads"), "repository": c.get("repository")}))
    return out


@engine("crates", "packages", host="crates.io", note="crates.io (Rust) search")
def run_crates(q: Query) -> list[Hit]:
    return parse_crates(_json(fetch("crates", "https://crates.io/api/v1/crates?" + urlencode({"q": q.q, "per_page": str(min(q.max, 20))}), q, accept="application/json")))


# ── reference ───────────────────────────────────────────────────────────


def parse_wikipedia(data: Any, lang: str = "en") -> list[Hit]:
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    items = list(pages.values()) if isinstance(pages, dict) else list(pages)
    items.sort(key=lambda p: p.get("index", 0))
    out = []
    for i, p in enumerate(items, 1):
        title = p.get("title", "")
        url = p.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        out.append(Hit(title=title, url=url, snippet=short(p.get("extract") or "", 360), date=(p.get("touched") or "")[:10], rank=i, extra={"pageid": p.get("pageid")}))
    if not out:  # list=search shape
        for i, s in enumerate(((data or {}).get("query") or {}).get("search") or [], 1):
            title = s.get("title", "")
            out.append(Hit(title=title, url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}", snippet=short(strip_tags(s.get("snippet", ""))), date=(s.get("timestamp") or "")[:10], rank=i))
    return out


@engine("wikipedia", "wiki", host="en.wikipedia.org", note="Wikipedia search with each article's opening sentences")
def run_wikipedia(q: Query) -> list[Hit]:
    lang = (q.lang or "en").split("-")[0].lower()
    p = {"action": "query", "format": "json", "formatversion": "2", "generator": "search", "gsrsearch": q.q, "gsrlimit": str(min(q.max, 10)), "prop": "extracts|info", "exintro": "1", "explaintext": "1", "exsentences": "2", "exlimit": "max", "inprop": "url", "utf8": "1"}
    return parse_wikipedia(_json(fetch("wikipedia", f"https://{lang}.wikipedia.org/w/api.php?" + urlencode(p), q, accept="application/json")), lang)


def parse_wikidata(data: Any) -> list[Hit]:
    out = []
    for i, s in enumerate((data or {}).get("search") or [], 1):
        qid = s.get("id", "")
        out.append(Hit(title=f"{s.get('label') or qid} ({qid})", url=f"https://www.wikidata.org/wiki/{qid}", snippet=short(s.get("description") or ""), rank=i, extra={"id": qid}))
    return out


@engine("wikidata", "wiki", host="www.wikidata.org", note="Wikidata entities (structured facts)")
def run_wikidata(q: Query) -> list[Hit]:
    lang = (q.lang or "en").split("-")[0].lower()
    p = {"action": "wbsearchentities", "search": q.q, "language": lang, "uselang": lang, "format": "json", "limit": str(min(q.max, 10))}
    return parse_wikidata(_json(fetch("wikidata", "https://www.wikidata.org/w/api.php?" + urlencode(p), q, accept="application/json")))


def parse_openlibrary(data: Any) -> list[Hit]:
    out = []
    for i, d in enumerate((data or {}).get("docs") or [], 1):
        isbn = (d.get("isbn") or [""])[0]
        meta = " · ".join(x for x in (_authors(d.get("author_name") or []), f"first published {d['first_publish_year']}" if d.get("first_publish_year") else "", f"{d.get('edition_count')} editions" if d.get("edition_count") else "", f"ISBN {isbn}" if isbn else "") if x)
        out.append(Hit(title=d.get("title", ""), url=f"https://openlibrary.org{d.get('key', '')}", snippet=short(meta), date=str(d.get("first_publish_year") or ""), rank=i, extra={"authors": d.get("author_name") or [], "isbn": isbn}))
    return out


@engine("openlibrary", "books", host="openlibrary.org", note="Open Library books (Internet Archive)")
def run_openlibrary(q: Query) -> list[Hit]:
    p = {"q": q.q, "limit": str(min(q.max, 20)), "fields": "key,title,author_name,first_publish_year,isbn,edition_count"}
    return parse_openlibrary(_json(fetch("openlibrary", "https://openlibrary.org/search.json?" + urlencode(p), q, accept="application/json", timeout=12)))


def parse_nominatim(data: Any) -> list[Hit]:
    out = []
    for i, p in enumerate(data if isinstance(data, list) else [], 1):
        name = p.get("name") or (p.get("display_name") or "").split(",")[0]
        url = f"https://www.openstreetmap.org/{p.get('osm_type', 'node')}/{p.get('osm_id', '')}"
        meta = f"{p.get('display_name', '')} · {p.get('category') or p.get('class', '')}/{p.get('type', '')} · lat {p.get('lat')}, lon {p.get('lon')}"
        out.append(Hit(title=name, url=url, snippet=short(meta, 360), rank=i, extra={"lat": p.get("lat"), "lon": p.get("lon"), "type": p.get("type")}))
    return out


@engine("nominatim", "places", host="nominatim.openstreetmap.org", note="OpenStreetMap Nominatim geocoding (1 request a second)")
def run_nominatim(q: Query) -> list[Hit]:
    p = {"q": q.q, "format": "jsonv2", "limit": str(min(q.max, 10))}
    if q.lang:
        p["accept-language"] = q.lang
    if q.region:
        p["countrycodes"] = q.region.lower()
    return parse_nominatim(_json(fetch("nominatim", "https://nominatim.openstreetmap.org/search?" + urlencode(p), q, accept="application/json")))


# ── video ───────────────────────────────────────────────────────────────


def _yt_text(v: Any) -> str:
    if isinstance(v, dict):
        if "simpleText" in v:
            return str(v["simpleText"])
        return "".join(r.get("text", "") for r in v.get("runs") or [])
    return str(v or "")


def _walk(o: Any, key: str, out: list) -> None:
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                out.append(v)
            else:
                _walk(v, key, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, key, out)


def yt_initial_data(text: str) -> Any:
    m = re.search(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.*?\});\s*</script>", text, re.S)
    if not m:
        raise EngineError("unexpected page (no ytInitialData; a consent or error page?)", 900, "broken")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise EngineError(f"unexpected page (bad ytInitialData: {e})", 900, "broken") from None


def parse_youtube(text: str) -> list[Hit]:
    renderers: list[dict] = []
    _walk(yt_initial_data(text), "videoRenderer", renderers)
    out = []
    for v in renderers:
        vid = v.get("videoId")
        if not vid:
            continue
        channel = _yt_text(v.get("ownerText") or v.get("longBylineText"))
        length = _yt_text(v.get("lengthText"))
        views = _yt_text(v.get("viewCountText"))
        age = _yt_text(v.get("publishedTimeText"))
        desc = "".join(_yt_text(s.get("snippetText")) for s in v.get("detailedMetadataSnippets") or [])
        meta = " · ".join(x for x in (channel, length, views, age) if x)
        out.append(Hit(title=_yt_text(v.get("title")), url=f"https://www.youtube.com/watch?v={vid}", snippet=short(meta + (f" — {desc}" if desc else "")), rank=len(out) + 1, extra={"id": vid, "channel": channel, "duration": length, "views": views, "published": age}))
    return out


@engine("youtube", "video", host="www.youtube.com", note="YouTube's search page")
def run_youtube(q: Query) -> list[Hit]:
    p = {"search_query": q.web_query(), "hl": (q.lang or "en"), "gl": (q.region or "US").upper()}
    if q.since:
        sp = {"day": "EgIIAg%3D%3D", "24h": "EgIIAg%3D%3D", "today": "EgIIAg%3D%3D", "week": "EgIIAw%3D%3D", "month": "EgIIBA%3D%3D", "year": "EgIIBQ%3D%3D"}.get(q.since.lower())
        if sp:
            p["sp"] = sp.replace("%3D", "=")
    return parse_youtube(fetch("youtube", "https://www.youtube.com/results?" + urlencode(p), q, browser=True, timeout=10).text)[: q.max * 2]


# ── intent detection ────────────────────────────────────────────────────

STOP = {"the", "and", "for", "with", "how", "what", "why", "who", "best", "use", "using", "from", "into", "vs", "versus", "to", "in", "of", "on", "a", "an", "is", "are", "does", "do"}
INTENTS: list[tuple[str, re.Pattern]] = [
    ("news", re.compile(r"\b(news|latest|today|yesterday|this (week|month)|breaking|announced?|announcement|headlines?|just (released|launched)|current(ly)?|recent(ly)?|update[sd]?|election|outage|earnings|20[2-3]\d)\b", re.I)),
    ("papers", re.compile(r"\b(papers?|study|studies|research|arxiv|doi|peer[- ]reviewed|meta-?analys[ie]s|systematic review|journal|preprint|clinical trial|randomi[sz]ed|cohort|evidence|citations?|survey of|literature)\b", re.I)),
    ("code", re.compile(r"\b(error|exception|traceback|stack ?trace|segfault|bug|compile|compiler|api|sdk|library|framework|function|method|regex|github|repo(sitory)?|python|javascript|typescript|rust|golang|java|c\+\+|c#|kotlin|swift|ruby|php|sql|bash|docker|kubernetes|react|node\.?js|npm|pip|cargo|nginx|apache|linux|ubuntu|debian|homebrew|command[- ]line|cli|ssh|postgres(ql)?|mysql|redis|config(uration)?|how to (implement|parse|fix|install|configure|enable|disable|set ?up|deploy|debug|build|run|migrate|upgrade|compile))\b|\w+\(\)|::|\.(py|js|ts|rs|go|java|cpp)\b", re.I)),
    ("forums", re.compile(r"\b(reddit|forums?|opinions?|experiences?|anyone (use|tried)|worth it|recommend(ations?)?|reviews?|complaints?|vs\.?|versus|people say)\b", re.I)),
    ("packages", re.compile(r"\b(package|library for|libraries for|pip install|npm install|cargo add|crate|module for|pypi|npm package|dependency|alternatives? to)\b", re.I)),
    ("wiki", re.compile(r"^(who|what (is|are|was|were)|when (did|was|were)|where (is|was)|history of|definition of|define|meaning of|biography)\b|\b(born|capital of|population of|founded|designed by|invented by|architect of)\b", re.I)),
    ("books", re.compile(r"\b(books?|novels?|author of|isbn|edition|publisher|memoir|textbook)\b", re.I)),
    ("places", re.compile(r"\b(where is|located|location of|address of|near me|nearby|coordinates of|directions to|map of|how far)\b", re.I)),
    ("video", re.compile(r"\b(videos?|youtube|watch|talk|lecture|keynote|webinar|tutorial video|screencast|conference talk)\b", re.I)),
]


def detect_intents(query: str) -> list[tuple[str, str]]:
    """The specialised groups a query probably wants, with the words that suggested them."""
    out = []
    for group, rx in INTENTS:
        m = rx.search(query)
        if m:
            out.append((group, m.group(0).strip()))
    return out


# ── fusion ──────────────────────────────────────────────────────────────


@dataclass
class Fused:
    title: str
    url: str
    snippet: str
    date: str
    group: str
    score: float
    engines: list[tuple[str, int]]
    extra: dict


def _title_key(title: str) -> str:
    t = re.sub(r"(?<!\s)\s+[-|–—]\s+[^-|–—]{2,60}$", "", title or "")  # drop a trailing " - Outlet"
    return re.sub(r"[\W_]+", "", t.casefold())[:120]


def _redirect_only(url: str) -> bool:
    return _net.host(url) == "news.google.com"


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if len(w) > 2 and w not in STOP}


def fuse(runs: dict[str, list[Hit]], weights: dict[str, float], limit: int, *, k: float = 10.0, per_site: int = 2, site: str | None = None, filetype: str | None = None, since: date | None = None, merge_titles: bool = False, relevance: str | None = None) -> list[Fused]:
    """Reciprocal-rank fusion: score(url) = Σ weight_e / (k + rank_e), deduplicated by canonical URL or DOI (and, for
    news, by headline, so a Google News redirect merges with the same story's direct link), then at most `per_site`
    results per site among the first `limit` (unless --site asks for one site).

    k = 10 (not the usual 60): with a handful of engines, rank must count as much as the engine's weight, so one
    engine's top results interleave with another's instead of following its whole list. `relevance` (the query, for
    papers) scales each score by how many of the query's words the title and abstract contain, so a famous but
    off-topic work does not take a slot."""
    by_key: dict[str, Fused] = {}
    by_title: dict[str, str] = {}
    for name, hits in runs.items():
        w = weights.get(name, 1.0)
        seen: set[str] = set()
        for rank, h in enumerate(hits, 1):
            if not h.url or not h.title:
                continue
            if site and not _on_site(h.url, site):
                continue
            if filetype and not _has_ext(h.url, filetype) and name in ("marginalia", "mwmbl"):
                continue
            if since and h.date:
                d = parse_date(h.date)
                if d and d < since:
                    continue
            doi = str(h.extra.get("doi") or "").lower()
            key = f"doi:{doi}" if doi else _net.url_key(h.url)
            tkey = _title_key(h.title) if merge_titles else ""
            if tkey and tkey in by_title and key not in by_key:
                key = by_title[tkey]
            if key in seen:
                continue
            seen.add(key)
            f = by_key.get(key)
            score = w / (k + rank)
            if f is None:
                by_key[key] = Fused(h.title, _net.canonical(h.url), h.snippet, h.date, h.group, score, [(name, rank)], dict(h.extra))
                if tkey:
                    by_title.setdefault(tkey, key)
            else:
                if (_redirect_only(f.url) and not _redirect_only(h.url)) or (_net.host(h.url) == "doi.org" and _net.host(f.url) != "doi.org"):
                    f.url = _net.canonical(h.url)
                    f.extra.pop("via", None)
                f.score += score
                f.engines.append((name, rank))
                if len(h.snippet) > len(f.snippet):
                    f.snippet = h.snippet
                if not f.date and h.date:
                    f.date = h.date
                if len(f.title) < 8 and len(h.title) > len(f.title):
                    f.title = h.title
                for kk, vv in h.extra.items():
                    f.extra.setdefault(kk, vv)
    want = _terms(relevance or "")
    if len(want) >= 2:
        for f in by_key.values():
            f.score *= 0.3 + 0.7 * len(want & _terms(f"{f.title} {f.snippet}")) / len(want)
    ranked = sorted(by_key.values(), key=lambda f: (-f.score, -len(f.engines)))
    if site or per_site <= 0:
        return ranked[:limit]
    out, later, count = [], [], {}
    for f in ranked:
        s = _net.site_of(f.url)
        if count.get(s, 0) >= per_site:
            later.append(f)
            continue
        count[s] = count.get(s, 0) + 1
        out.append(f)
        if len(out) >= limit:
            break
    if len(out) < limit:
        out += later[: limit - len(out)]
    return out


def _on_site(url: str, site: str) -> bool:
    s = site.lower().strip()
    s = re.sub(r"^https?://", "", s).rstrip("/")
    host_part, _, path = s.partition("/")
    h = _net.host(url)
    if not (h == host_part or h.endswith("." + host_part) or h == "www." + host_part):
        return False
    return not path or urlsplit(url).path.lstrip("/").startswith(path)


def _has_ext(url: str, ext: str) -> bool:
    return urlsplit(url).path.lower().endswith("." + ext.lower().lstrip("."))


def run_engine(e: Engine, q: Query) -> tuple[list[Hit], float]:
    t0 = time.time()
    hits = e.run(q)
    for h in hits:
        h.engine = e.name
        h.group = e.group
    return hits, time.time() - t0
