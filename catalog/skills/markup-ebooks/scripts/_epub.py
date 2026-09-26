"""EPUB internals for markup-ebooks: container and OPF parsing, spine and TOC (EPUB 3 nav or EPUB 2 NCX), cover,
chapters as Markdown (cached for big books), validity checks, and a safe repack. Standard zipfile + lxml.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from _common import SkillError

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}
XHTML_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml", "image/svg+xml"}
EXT_TYPES = {
    ".xhtml": "application/xhtml+xml", ".html": "application/xhtml+xml", ".htm": "application/xhtml+xml",
    ".css": "text/css", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".ncx": "application/x-dtbncx+xml", ".opf": "application/oebps-package+xml",
    ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2", ".js": "application/javascript",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".m4a": "audio/mp4", ".smil": "application/smil+xml", ".xml": "application/xml",
}
FONT_TYPES = {"font/ttf", "font/otf", "font/woff", "font/woff2", "application/font-woff", "application/vnd.ms-opentype", "application/x-font-ttf", "application/x-font-otf", "application/font-sfnt", "application/x-font-truetype", "application/x-font-opentype"}
JUNK = re.compile(r"(^|/)(\.DS_Store|Thumbs\.db|desktop\.ini)$|^__MACOSX/")


def _xml(data: bytes) -> Any:
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=False)
    return etree.fromstring(data, parser)


def _xml_lenient(data: bytes) -> Any:
    from lxml import etree

    try:
        return _xml(data)
    except etree.XMLSyntaxError:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True)
        root = etree.fromstring(data, parser)
        if root is None:
            raise
        return root


def _local(tag: Any) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and tag.startswith("{") else str(tag)


class Epub:
    """A read-only view of an EPUB file."""

    def __init__(self, path: str | Path) -> None:
        from _common import check_zip

        self.path = Path(path)
        check_zip(self.path)  # a zip bomb is refused before any member is read
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise SkillError(f"{self.path.name} is not an EPUB (not a zip file)") from e
        self.names = self.zip.namelist()
        self._lower = {n.lower(): n for n in self.names}
        self.opf_path = self._find_opf()
        self.opf_dir = posixpath.dirname(self.opf_path)
        self.opf = _xml_lenient(self.read(self.opf_path))
        self.version = self.opf.get("version") or "?"
        self.manifest: dict[str, dict[str, Any]] = {}
        self.by_href: dict[str, dict[str, Any]] = {}
        man = self._first(self.opf, "manifest")
        for it in (man if man is not None else []):
            if _local(it.tag) != "item":
                continue
            href = it.get("href") or ""
            full = self.resolve(self.opf_dir, href)
            item = {"id": it.get("id"), "href": href, "path": full, "type": (it.get("media-type") or "").strip().lower(), "properties": (it.get("properties") or "").split(), "fallback": it.get("fallback")}
            if item["id"]:
                self.manifest[item["id"]] = item
            self.by_href[full] = item
        spine = self._first(self.opf, "spine")
        self.spine_toc = spine.get("toc") if spine is not None else None
        self.spine: list[dict[str, Any]] = []
        for ref in (spine if spine is not None else []):
            if _local(ref.tag) != "itemref":
                continue
            item = self.manifest.get(ref.get("idref") or "")
            self.spine.append({"idref": ref.get("idref"), "item": item, "linear": (ref.get("linear") or "yes") != "no"})

    def close(self) -> None:
        self.zip.close()

    def __enter__(self) -> "Epub":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # helpers
    @staticmethod
    def _first(el: Any, name: str) -> Any:
        for ch in el:
            if _local(ch.tag) == name:
                return ch
        return None

    @staticmethod
    def resolve(base_dir: str, href: str) -> str:
        href = unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(base_dir, href)) if href else ""

    def exists(self, name: str) -> bool:
        return name in self._lower.values() or name.lower() in self._lower

    def real(self, name: str) -> str | None:
        if name in self.names:
            return name
        return self._lower.get(name.lower())

    def read(self, name: str, limit: int | None = None) -> bytes:
        """A member's bytes, read in bounded steps (a member that lies about its size is refused, not inflated)."""
        from _zipsafe import read_member

        real = self.real(name)
        if real is None:
            raise SkillError(f"{name} is missing from {self.path.name}")
        return read_member(self.zip, real, self.path.name, limit)

    def copy_to(self, name: str, dest: Any) -> int:
        """Streams a member into an open binary file; returns the bytes written."""
        from _zipsafe import copy_member

        real = self.real(name)
        if real is None:
            raise SkillError(f"{name} is missing from {self.path.name}")
        return copy_member(self.zip, real, dest, self.path.name)

    def _find_opf(self) -> str:
        try:
            root = _xml(self.read("META-INF/container.xml"))
            rf = root.find(".//c:rootfile", NS)
            if rf is not None and rf.get("full-path"):
                return unquote(rf.get("full-path"))
        except Exception:  # noqa: BLE001 — fall back to looking for an .opf
            pass
        opfs = [n for n in self.names if n.lower().endswith(".opf")]
        if not opfs:
            raise SkillError(f"{self.path.name} has no package document (.opf): not an EPUB, or badly damaged")
        return opfs[0]

    # metadata
    def metadata(self) -> dict[str, Any]:
        md = self._first(self.opf, "metadata")
        out: dict[str, Any] = {}
        if md is None:
            return out
        refines: dict[str, dict[str, str]] = {}
        for m in md.iter():
            if _local(m.tag) == "meta" and m.get("refines"):
                refines.setdefault(m.get("refines").lstrip("#"), {})[m.get("property") or ""] = (m.text or "").strip()
        for el in md:
            if not isinstance(el.tag, str):
                continue
            tag = _local(el.tag)
            text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
            if el.tag.startswith("{" + NS["dc"]):
                key = tag
                if key == "creator" or key == "contributor":
                    role = el.get("{http://www.idpf.org/2007/opf}role") or refines.get(el.get("id") or "", {}).get("role")
                    entry = text + (f" ({role})" if role and role not in ("aut",) else "")
                    out.setdefault(key + "s", []).append(entry)
                elif key in ("subject",):
                    out.setdefault("subjects", []).append(text)
                elif key == "identifier":
                    scheme = el.get("{http://www.idpf.org/2007/opf}scheme") or ""
                    out.setdefault("identifiers", []).append(f"{scheme + ':' if scheme and not text.lower().startswith(scheme.lower()) else ''}{text}")
                elif key == "date":
                    ev = el.get("{http://www.idpf.org/2007/opf}event")
                    out.setdefault("date", text) if not ev or ev == "publication" else out.setdefault(f"date_{ev}", text)
                else:
                    out.setdefault(key, text)
            elif tag == "meta":
                prop = el.get("property")
                if prop == "dcterms:modified":
                    out["modified"] = text
                elif prop in ("rendition:layout",):
                    out["layout"] = text
                elif prop == "belongs-to-collection":
                    out["series"] = text
                elif el.get("name") == "calibre:series":
                    out["series"] = el.get("content")
                elif el.get("name") == "calibre:series_index":
                    out["series_index"] = el.get("content")
        return out

    def cover(self) -> dict[str, Any] | None:
        for it in self.manifest.values():
            if "cover-image" in it["properties"]:
                return it
        md = self._first(self.opf, "metadata")
        if md is not None:
            for m in md.iter():
                if _local(m.tag) == "meta" and m.get("name") == "cover":
                    it = self.manifest.get(m.get("content") or "")
                    if it and it["type"].startswith("image/"):
                        return it
        guide = self._first(self.opf, "guide")
        if guide is not None:
            for ref in guide:
                if (ref.get("type") or "").lower() == "cover":
                    target = self.resolve(self.opf_dir, ref.get("href") or "")
                    doc = self.by_href.get(target)
                    if doc and doc["type"].startswith("image/"):
                        return doc
                    if doc and self.exists(target):
                        img = self._first_image(target)
                        if img:
                            return img
        for it in self.manifest.values():
            if it["type"].startswith("image/") and "cover" in (it["id"] or "").lower() + it["href"].lower():
                return it
        # The first image of the first spine document.
        if self.spine and self.spine[0]["item"]:
            return self._first_image(self.spine[0]["item"]["path"])
        return None

    def _first_image(self, doc_path: str) -> dict[str, Any] | None:
        try:
            data = self.read(doc_path)
        except SkillError:
            return None
        m = re.search(rb"<(?:img|image)\b[^>]*?(?:src|xlink:href|href)\s*=\s*[\"']([^\"']+)", data, re.I)
        if not m:
            return None
        target = self.resolve(posixpath.dirname(doc_path), m.group(1).decode("utf-8", "replace"))
        return self.by_href.get(target) or ({"id": None, "href": target, "path": target, "type": EXT_TYPES.get(posixpath.splitext(target)[1].lower(), ""), "properties": []} if self.exists(target) else None)

    # navigation
    def toc(self) -> list[dict[str, Any]]:
        """TOC entries [{level, title, path, fragment}] from the EPUB 3 nav, else the NCX."""
        nav = next((it for it in self.manifest.values() if "nav" in it["properties"]), None)
        if nav and self.exists(nav["path"]):
            entries = self._toc_nav(nav["path"])
            if entries:
                return entries
        ncx = self.manifest.get(self.spine_toc or "") or next((it for it in self.manifest.values() if it["type"] == "application/x-dtbncx+xml"), None)
        if ncx and self.exists(ncx["path"]):
            return self._toc_ncx(ncx["path"])
        return []

    def _toc_nav(self, path: str) -> list[dict[str, Any]]:
        root = _xml_lenient(self.read(path))
        base = posixpath.dirname(path)
        navs = [n for n in root.iter() if _local(n.tag) == "nav"]
        toc_nav = next((n for n in navs if "toc" in (n.get("{%s}type" % NS["epub"]) or n.get("epub:type") or n.get("role") or "")), navs[0] if navs else None)
        if toc_nav is None:
            return []
        out: list[dict[str, Any]] = []

        def walk(ol: Any, level: int) -> None:
            for li in ol:
                if _local(li.tag) != "li":
                    continue
                label = next((c for c in li if _local(c.tag) in ("a", "span")), None)
                if label is not None:
                    href = label.get("href") or ""
                    title = re.sub(r"\s+", " ", "".join(label.itertext())).strip()
                    out.append({"level": level, "title": title, "path": self.resolve(base, href) if href else "", "fragment": href.split("#", 1)[1] if "#" in href else None})
                for sub in li:
                    if _local(sub.tag) == "ol":
                        walk(sub, level + 1)

        for ol in toc_nav:
            if _local(ol.tag) == "ol":
                walk(ol, 1)
        return out

    def _toc_ncx(self, path: str) -> list[dict[str, Any]]:
        root = _xml_lenient(self.read(path))
        base = posixpath.dirname(path)
        out: list[dict[str, Any]] = []
        nav_map = next((e for e in root.iter() if _local(e.tag) == "navMap"), None)

        def walk(el: Any, level: int) -> None:
            for np_ in el:
                if _local(np_.tag) != "navPoint":
                    continue
                label = next((t for t in np_.iter() if _local(t.tag) == "text"), None)
                content = next((c for c in np_ if _local(c.tag) == "content"), None)
                src = content.get("src") if content is not None else ""
                out.append({"level": level, "title": re.sub(r"\s+", " ", (label.text or "") if label is not None else "").strip(), "path": self.resolve(base, src or ""), "fragment": src.split("#", 1)[1] if src and "#" in src else None})
                walk(np_, level + 1)

        if nav_map is not None:
            walk(nav_map, 1)
        return out

    def spine_docs(self, include_nonlinear: bool = True) -> list[dict[str, Any]]:
        docs = []
        for ref in self.spine:
            it = ref["item"]
            if not it or (not include_nonlinear and not ref["linear"]):
                continue
            docs.append({**it, "linear": ref["linear"]})
        return docs


# ── chapters as Markdown ───────────────────────────────────────────────


TOC_MARK = "<!-- toc t{} -->"
_TOC_MARK_RE = re.compile(r"^<!-- toc t(\d+) -->$", re.M)


def _chapter_job(job: tuple[str, list[tuple[int, str, str, list[tuple[int, str]]]]]) -> list[dict[str, Any]]:
    from _zipsafe import read_member

    path, items = job
    out = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        lower = {n.lower(): n for n in names}
        for n, doc_path, media, marks in items:
            real = doc_path if doc_path in names else lower.get(doc_path.lower())
            if real is None:
                out.append({"n": n, "path": doc_path, "markdown": "", "missing": True})
                continue
            out.append({"n": n, "path": doc_path, **chapter_markdown(read_member(z, real, Path(path).name), doc_path, media, marks)})
    return out


def chapter_markdown(data: bytes, doc_path: str, media_type: str = "application/xhtml+xml", marks: list[tuple[int, str]] | None = None) -> dict[str, Any]:
    """A content document as Markdown. `marks` are (TOC index, fragment id): a `<!-- toc tK -->` line is placed
    where each target starts, so TOC entries inside one long document can be read on their own."""
    from _html import BLOCK, HEADINGS, MarkdownWriter, parse_html_string, tag_of, text_of

    if media_type == "image/svg+xml":
        return {"markdown": "[SVG page]\n", "heading": None, "images": 0}
    root = parse_html_string(data)
    body = next(root.iter("body"), root)
    base_dir = posixpath.dirname(doc_path)
    if marks:
        ids = {}
        for el in body.iter():
            if isinstance(el.tag, str):
                i = el.get("id") or (el.get("name") if tag_of(el) == "a" else None)
                if i:
                    ids.setdefault(i, el)
        for k, frag in marks:
            el = ids.get(frag)
            if el is None:
                continue
            # Mark the enclosing block (an anchor usually sits inside a heading or paragraph).
            blk = el
            while blk is not None and blk is not body and tag_of(blk) not in BLOCK and tag_of(blk) not in HEADINGS:
                blk = blk.getparent()
            if blk is None or blk is body:
                blk = el
            prev = blk.get("data-desk-toc")
            blk.set("data-desk-toc", (prev + " " if prev else "") + str(k))

    class W(MarkdownWriter):
        def url(self, href: str | None) -> str | None:  # zip-internal paths for images and cross-chapter links
            if href is None:
                return None
            href = href.strip()
            if not href or href.lower().startswith(("javascript:",)):
                return None
            if href.startswith("#") or urlparse(href).scheme:
                return href
            frag = ("#" + href.split("#", 1)[1]) if "#" in href else ""
            return Epub.resolve(base_dir, href) + frag

        def block(self, el: Any, out: list[str]) -> None:
            tk = el.get("data-desk-toc")
            if tk:
                for k in tk.split():
                    out.append(TOC_MARK.format(k))
            super().block(el, out)

    w = W()
    md = w.convert(body)
    heading = next((text_of(h) for h in body.iter("h1", "h2", "h3") if text_of(h)), None)
    title_el = next(root.iter("title"), None)
    return {"markdown": md, "heading": heading, "doc_title": text_of(title_el) if title_el is not None else None, "images": len(w.image_refs)}


def _words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text))


def strip_marks(md: str) -> str:
    return _TOC_MARK_RE.sub("", md).replace("\n\n\n", "\n\n")


def book_chapters(path: Path, workers: int | None = None) -> dict[str, Any]:
    """Every spine document as Markdown with titles from the TOC, plus TOC sections (entries inside documents).

    Returns {meta, version, toc, chapters: [{n, path, title, words, chars, markdown, …}], sections: [{t, level,
    title, chapter, words, start, end}]} where start/end are character offsets into that chapter's Markdown."""
    from _common import pool_map

    with Epub(path) as ep:
        docs = ep.spine_docs()
        toc = ep.toc()
        meta = ep.metadata()
        version = ep.version
    first_title: dict[str, str] = {}
    marks_by_doc: dict[str, list[tuple[int, str]]] = {}
    for k, t in enumerate(toc, 1):
        if t["path"] and t["path"] not in first_title and t["title"]:
            first_title[t["path"]] = t["title"]
        if t["path"] and t.get("fragment"):
            marks_by_doc.setdefault(t["path"], []).append((k, t["fragment"]))
    items = [(i + 1, d["path"], d["type"], marks_by_doc.get(d["path"], [])) for i, d in enumerate(docs)]
    total = len(items)
    parallel = total >= 24 and path.stat().st_size >= 1_500_000
    size = max(1, (total + 7) // 8) if parallel else max(1, total)
    chunks = [(str(path), items[k : k + size]) for k in range(0, total, size)]
    results = pool_map(_chapter_job, chunks, workers=workers if parallel else 1)
    chapters = []
    for batch in results:
        for c in batch:
            d = docs[c["n"] - 1]
            md = c.get("markdown", "")
            title = first_title.get(d["path"]) or c.get("heading") or c.get("doc_title") or posixpath.basename(d["path"])
            chapters.append({"n": c["n"], "path": d["path"], "title": title, "linear": d["linear"], "words": _words(strip_marks(md)), "chars": len(md), "images": c.get("images", 0), "markdown": md, **({"missing": True} if c.get("missing") else {})})
    chapters.sort(key=lambda c: c["n"])
    # TOC sections: from an entry's marker (or the document start) to the marker of the next entry at the same or a
    # higher level in the same document, so an entry includes its sub-entries (like a Markdown section).
    ch_by_path = {c["path"]: c for c in chapters}
    per_doc: dict[str, list[dict[str, Any]]] = {}
    for k, t in enumerate(toc, 1):
        c = ch_by_path.get(t["path"])
        if c is None:
            continue
        per_doc.setdefault(c["path"], []).append({"t": k, "level": t["level"], "title": t["title"], "chapter": c["n"], "fragment": t.get("fragment")})
    sections = []
    for doc_path, entries in per_doc.items():
        md = ch_by_path[doc_path]["markdown"]
        pos = {int(m.group(1)): m.start() for m in _TOC_MARK_RE.finditer(md)}
        for e in entries:
            e["start"] = pos.get(e["t"], 0) if e.pop("fragment") else 0
        # In reading order, an entry ends where the next entry at its level or higher begins (a monotonic stack).
        order = sorted(entries, key=lambda e: (e["start"], e["t"]))
        stack: list[dict[str, Any]] = []
        for e in order:
            while stack and stack[-1]["level"] >= e["level"] and e["start"] > stack[-1]["start"]:
                stack.pop()["end"] = e["start"]
            stack.append(e)
        for e in stack:
            e.setdefault("end", len(md))
        for e in order:
            e.setdefault("end", len(md))
            e["end_chapter"] = e["chapter"]
            e["words"] = _words(strip_marks(md[e["start"] : e["end"]]))
        sections.extend(entries)
    sections.sort(key=lambda e: e["t"])
    _span_documents(sections, chapters)
    return {"meta": meta, "version": version, "toc": toc, "chapters": chapters, "sections": sections}


def _span_documents(sections: list[dict[str, Any]], chapters: list[dict[str, Any]]) -> None:
    """A TOC entry whose sub-entries continue in later spine documents (a Part whose chapters are separate files)
    spans through the end of its last sub-entry: end_chapter/end move there and its words are the whole range's."""
    by_n = {c["n"]: c for c in chapters}
    for i in range(len(sections) - 1, -1, -1):  # sub-entries first, so their own spans are final
        e = sections[i]
        end = (e["end_chapter"], e["end"])
        j = i + 1
        while j < len(sections) and sections[j]["level"] > e["level"]:
            d = sections[j]
            end = max(end, (d["end_chapter"], d["end"]))
            j += 1
        if end <= (e["end_chapter"], e["end"]):
            continue
        e["end_chapter"], e["end"] = end
        first, last = by_n[e["chapter"]], by_n[e["end_chapter"]]
        words = _words(strip_marks(first["markdown"][e["start"] :])) + _words(strip_marks(last["markdown"][: e["end"]]))
        words += sum(by_n[n]["words"] for n in range(e["chapter"] + 1, e["end_chapter"]) if n in by_n)
        e["words"] = words


def section_text(book: dict[str, Any], sec: dict[str, Any]) -> str:
    """The Markdown of a TOC section, across spine documents when it spans several."""
    chs = book["chapters"]
    a, b = sec["chapter"], sec.get("end_chapter", sec["chapter"])
    if a == b:
        return chs[a - 1]["markdown"][sec["start"] : sec["end"]]
    parts = [chs[a - 1]["markdown"][sec["start"] :]] + [chs[n - 1]["markdown"] for n in range(a + 1, b)] + [chs[b - 1]["markdown"][: sec["end"]]]
    return "\n\n".join(x.strip("\n") for x in parts if x.strip())


def section_at(secs: list[dict[str, Any]], chapter: int, offset: int) -> dict[str, Any] | None:
    """The innermost TOC section that holds a position (chapter, character offset)."""
    best = None
    pos = (chapter, offset)
    for x in secs:
        if (x["chapter"], x["start"]) <= pos < (x.get("end_chapter", x["chapter"]), x["end"]):
            if best is None or (x["chapter"], x["start"], x["level"]) >= (best["chapter"], best["start"], best["level"]):
                best = x
    return best


def section_chapters(sec: dict[str, Any]) -> str:
    """'ch 3' or 'ch 3-41' for a section."""
    a, b = sec["chapter"], sec.get("end_chapter", sec["chapter"])
    return f"ch {a}" if a == b else f"ch {a}-{b}"


def cached_book(path: Path, no_cache: bool = False) -> dict[str, Any]:
    from _cache import cached_json, enabled

    if no_cache or not enabled() or path.stat().st_size < 64 * 1024:
        return book_chapters(path)
    return cached_json(path, "epub-chapters", {}, "5", lambda: book_chapters(path))


# ── checks ──────────────────────────────────────────────────────────────


def check_epub(path: Path, deep: bool = True) -> dict[str, Any]:
    """Structural validation. Returns {problems: [{level, where, message}], stats}."""
    from lxml import etree

    from _common import check_zip
    from _zipsafe import read_member, safe_member_name, verify_zip

    probs: list[dict[str, Any]] = []

    def add(level: str, where: str, msg: str) -> None:
        if len(probs) < 500:
            probs.append({"level": level, "where": where, "message": msg})

    check_zip(path)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return {"problems": [{"level": "error", "where": path.name, "message": "not a zip file"}], "stats": {}}
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if not infos:
            add("error", "zip", "the archive is empty")
            return {"problems": probs, "stats": {}}
        first = infos[0]
        if first.filename != "mimetype":
            add("error", "mimetype", f"the first file must be 'mimetype' (it is '{first.filename}'); fix with: epub_tool.py repack")
        elif first.compress_type != zipfile.ZIP_STORED:
            add("error", "mimetype", "'mimetype' must be stored uncompressed; fix with: epub_tool.py repack")
        elif first.extra:
            add("warning", "mimetype", "'mimetype' has a zip extra field (some readers reject it); fix with: epub_tool.py repack")
        if "mimetype" in names:
            content = read_member(z, "mimetype", path.name, 4096) if z.getinfo("mimetype").file_size <= 4096 else b"(too long)"
            if content.strip() != b"application/epub+zip" or content != content.strip():
                add("error", "mimetype", f"'mimetype' must contain exactly application/epub+zip (has {content[:40]!r})")
        else:
            add("error", "mimetype", "no 'mimetype' file")
        for n in names:
            if not safe_member_name(n):
                add("error", n, "unsafe path in the archive (absolute, a drive letter, .. or backslashes): it can escape the folder it is extracted into; drop it with repack")
            if JUNK.search(n):
                add("warning", n, "junk file (remove with repack)")
        encrypted = [i.filename for i in infos if i.flag_bits & 0x1]
        if encrypted:
            add("error", encrypted[0], f"{len(encrypted)} zip-encrypted member(s)")
        if "META-INF/encryption.xml" in names:
            data = read_member(z, "META-INF/encryption.xml", path.name, 16 * 1024 * 1024)
            algos = set(re.findall(rb'Algorithm="([^"]+)"', data))
            font_only = all(b"font" in a.lower() or b"obfuscation" in a.lower() or a.endswith(b"xmldsig#sha1") for a in algos) if algos else True
            if font_only:
                add("info", "META-INF/encryption.xml", "fonts are obfuscated (normal for embedded fonts)")
            else:
                add("error", "META-INF/encryption.xml", "the book is encrypted (DRM): its content cannot be read")
        if "META-INF/rights.xml" in names:
            add("error", "META-INF/rights.xml", "Adobe DRM rights file: the book is probably encrypted")
        if "META-INF/container.xml" not in names:
            add("error", "META-INF/container.xml", "missing: readers cannot find the package document")
    try:
        verify_zip(path)
    except SkillError as e:
        add("error", "zip", str(e))
    try:
        ep = Epub(path)
    except SkillError as e:
        add("error", "package", str(e))
        return {"problems": probs, "stats": {}}
    except etree.XMLSyntaxError as e:
        add("error", "package", f"the package document is not well-formed XML: {e}")
        return {"problems": probs, "stats": {}}
    with ep:
        opf = ep.opf_path
        if not ep.exists(opf):
            add("error", "META-INF/container.xml", f"points to {opf}, which is missing")
        v = ep.version
        if v not in ("2.0", "3.0", "3.1", "3.2", "3.3"):
            add("warning", opf, f"unusual package version '{v}'")
        md = ep.metadata()
        if not md.get("title"):
            add("error", opf, "no dc:title")
        if not md.get("language"):
            add("error", opf, "no dc:language")
        if not md.get("identifiers"):
            add("error", opf, "no dc:identifier")
        uid = ep.opf.get("unique-identifier")
        meta_el = Epub._first(ep.opf, "metadata")
        ids = {el.get("id") for el in (meta_el.iter() if meta_el is not None else []) if el.get("id")}
        if not uid:
            add("error", opf, "the package has no unique-identifier attribute")
        elif uid not in ids:
            add("error", opf, f"unique-identifier '{uid}' does not match any dc:identifier id")
        if v.startswith("3") and not md.get("modified"):
            add("error", opf, "EPUB 3 needs <meta property=\"dcterms:modified\">")
        # manifest
        seen_ids: set[str] = set()
        seen_paths: dict[str, str] = {}
        man_el = Epub._first(ep.opf, "manifest")
        raw_items = [it for it in (man_el if man_el is not None else []) if _local(it.tag) == "item"]
        for it in raw_items:
            iid = it.get("id")
            if not iid:
                add("error", opf, f"manifest item without id ({it.get('href')})")
            elif iid in seen_ids:
                add("error", opf, f"duplicate manifest id '{iid}'")
            seen_ids.add(iid or "")
            if not it.get("href"):
                add("error", opf, f"manifest item '{iid}' has no href")
            if not it.get("media-type"):
                add("error", opf, f"manifest item '{iid}' has no media-type")
        for item in ep.manifest.values():
            href = item["href"]
            if urlparse(href).scheme in ("http", "https"):
                if not any("remote-resources" in i["properties"] for i in ep.manifest.values()):
                    add("warning", opf, f"remote resource {href} (needs the remote-resources property)")
                continue
            p = item["path"]
            if p in seen_paths:
                add("error", opf, f"'{href}' is listed twice in the manifest ({seen_paths[p]}, {item['id']})")
            seen_paths[p] = item["id"] or ""
            if not ep.exists(p):
                add("error", p, f"listed in the manifest (id '{item['id']}') but missing from the archive")
            elif ep.real(p) != p:
                add("warning", p, "the manifest's letter case differs from the file name (fails on case-sensitive readers)")
            ext = posixpath.splitext(p)[1].lower()
            expected = EXT_TYPES.get(ext)
            if expected and item["type"] and expected != item["type"] and not (expected.startswith("font/") and item["type"] in FONT_TYPES) and not (ext in (".html", ".htm") and item["type"] == "text/html") and not (ext == ".xml"):
                add("warning", p, f"media-type {item['type']} does not match the extension ({expected})")
        listed = set(seen_paths)
        for n in ep.names:
            if not safe_member_name(n) or n.endswith("/") or n == "mimetype" or n.startswith("META-INF/") or n == opf or JUNK.search(n):
                continue
            if n not in listed and n.lower() not in {x.lower() for x in listed}:
                add("warning", n, "in the archive but not in the manifest")
        # spine
        if not ep.spine:
            add("error", opf, "the spine is empty: there is nothing to read")
        spine_seen: set[str] = set()
        for ref in ep.spine:
            if ref["item"] is None:
                add("error", opf, f"spine itemref '{ref['idref']}' is not in the manifest")
                continue
            if ref["idref"] in spine_seen:
                add("warning", opf, f"spine lists '{ref['idref']}' twice")
            spine_seen.add(ref["idref"])
            t = ref["item"]["type"]
            if t not in XHTML_TYPES and not ref["item"].get("fallback"):
                add("error", ref["item"]["path"], f"spine item of type {t} without a fallback")
        if ep.spine and not any(r["linear"] for r in ep.spine):
            add("error", opf, "every spine item is non-linear")
        # navigation
        navs = [it for it in ep.manifest.values() if "nav" in it["properties"]]
        if v.startswith("3"):
            if not navs:
                add("error", opf, "EPUB 3 needs a navigation document (manifest item with properties=\"nav\")")
            elif len(navs) > 1:
                add("error", opf, "more than one navigation document")
        ncx = ep.manifest.get(ep.spine_toc or "")
        if v.startswith("2"):
            if not ep.spine_toc:
                add("error", opf, "EPUB 2 spine needs toc=\"<ncx id>\"")
            elif ncx is None:
                add("error", opf, f"spine toc '{ep.spine_toc}' is not in the manifest")
        toc = []
        try:
            toc = ep.toc()
        except Exception as e:  # noqa: BLE001
            add("error", "toc", f"cannot read the table of contents: {e}")
        if not toc:
            add("warning", "toc", "the table of contents is empty")
        for t in toc:
            if t["path"] and not ep.exists(t["path"]):
                add("error", "toc", f"TOC entry '{t['title'][:50]}' points to missing {t['path']}")
        cover = ep.cover()
        if cover is None:
            add("info", opf, "no cover image declared")
        # content documents
        stats = {"documents": 0, "images": sum(1 for i in ep.manifest.values() if i["type"].startswith("image/")), "spine": len(ep.spine), "toc_entries": len(toc)}
        if deep:
            doc_ids: dict[str, set[str]] = {}
            links: list[tuple[str, str, str]] = []
            for item in ep.manifest.values():
                if item["type"] not in ("application/xhtml+xml", "image/svg+xml") or not ep.exists(item["path"]):
                    continue
                stats["documents"] += 1
                try:
                    data = ep.read(item["path"])
                except SkillError as e:
                    add("error", item["path"], str(e))
                    continue
                try:
                    root = _xml(data)
                except etree.XMLSyntaxError as e:
                    add("error", item["path"], f"not well-formed XHTML: {str(e)[:160]}")
                    try:
                        root = _xml_lenient(data)
                    except Exception:  # noqa: BLE001
                        continue
                ids_here = set()
                base = posixpath.dirname(item["path"])
                for el in root.iter():
                    if not isinstance(el.tag, str):
                        continue
                    i = el.get("id")
                    if i:
                        ids_here.add(i)
                    tag = _local(el.tag)
                    for attr in ("href", "src", "{http://www.w3.org/1999/xlink}href", "poster"):
                        val = el.get(attr)
                        if not val or tag in ("html",):
                            continue
                        if tag == "a" or tag in ("img", "image", "link", "source", "video", "audio", "script", "object"):
                            links.append((item["path"], tag, val.strip()))
                doc_ids[item["path"]] = ids_here
            for doc, tag, val in links:
                u = urlparse(val)
                if u.scheme:
                    if u.scheme in ("http", "https") and tag in ("img", "image", "source", "video", "audio", "script"):
                        add("warning", doc, f"remote resource {val[:80]}")
                    continue
                target = Epub.resolve(posixpath.dirname(doc), val) if not val.startswith("#") else doc
                frag = val.split("#", 1)[1] if "#" in val else None
                if target and not ep.exists(target):
                    add("error" if tag != "a" else "warning", doc, f"{'image' if tag in ('img', 'image') else 'link'} to missing {target}")
                    continue
                if frag and target in doc_ids and frag not in doc_ids[target] and not frag.startswith(("page", "epubcfi")):
                    add("warning", doc, f"link to missing anchor {posixpath.basename(target)}#{frag}")
        return {"problems": probs, "stats": stats}


def repack(src: Path, dest: Path) -> dict[str, Any]:
    """Rewrites an EPUB with 'mimetype' first and stored, no junk files, everything else deflated."""
    import shutil

    from _common import check_zip
    from _zipsafe import safe_member_name, verify_zip

    check_zip(src)
    verify_zip(src)
    fixed: list[str] = []
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        if not infos or infos[0].filename != "mimetype":
            fixed.append("moved mimetype to the front")
        elif infos[0].compress_type != zipfile.ZIP_STORED:
            fixed.append("stored mimetype uncompressed")
        with zipfile.ZipFile(dest, "w") as zout:
            zi = zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED
            zout.writestr(zi, b"application/epub+zip")
            dropped = 0
            for info in infos:
                n = info.filename
                if n == "mimetype" or n.endswith("/"):
                    continue
                if JUNK.search(n):
                    dropped += 1
                    continue
                if not safe_member_name(n):
                    dropped += 1
                    continue
                out = zipfile.ZipInfo(n, date_time=info.date_time)
                out.compress_type = zipfile.ZIP_DEFLATED
                out.external_attr = info.external_attr
                out.file_size = info.file_size
                with zin.open(info) as fin, zout.open(out, "w", force_zip64=info.file_size > 2**31) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)  # streamed: members never sit in memory whole
            if dropped:
                fixed.append(f"dropped {dropped} junk or unsafe file(s)")
    return {"output": str(dest), "fixed": fixed}
