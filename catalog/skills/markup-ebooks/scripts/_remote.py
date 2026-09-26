"""Remote images for markup-ebooks: find the http(s) images a document uses and download them with strict limits.

pandoc fetches remote images itself when it writes PDF (through Typst), DOCX, ODT, EPUB or self-contained HTML, and
it has no timeout: one slow server hangs the conversion for minutes. So Desk downloads them first (in parallel,
with a time budget, size caps and image types only) and hands pandoc local copies through templates/desk-remote.lua;
anything that could not be downloaded becomes a link with a warning, and pandoc never goes to the network.
Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Sequence

MAX_IMAGES = int(os.environ.get("DESK_REMOTE_MAX", "80"))
MAX_BYTES = int(float(os.environ.get("DESK_REMOTE_MAX_MB", "20")) * 1024 * 1024)
TIMEOUT = float(os.environ.get("DESK_REMOTE_TIMEOUT", "8"))
BUDGET = float(os.environ.get("DESK_REMOTE_BUDGET", "30"))
TEXT_READERS = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "html", "rst", "org", "asciidoc", "asciidoctor", "textile", "mediawiki", "latex", "docbook", "jats", "typst", "djot", "muse", "creole", "dokuwiki", "vimwiki", "t2t", "fb2", "opml"}
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/tiff": ".tif", "image/avif": ".avif", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico"}
_URL = r"((?:https?:)?//[^\s\"'<>()\[\]]+)"
_IMG_REF = re.compile(r"!\[[^\]]*\]\[([^\]]*)\]")
_REF_DEF = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*<?((?:https?:)?//[^\s>]+)", re.M)
PATTERNS = [
    re.compile(r"!\[[^\]]*\]\(\s*<?" + _URL, re.I),  # Markdown ![alt](url)
    re.compile(r"<img\b[^>]*?\s(?:src|data-src|data-original)\s*=\s*[\"']?" + _URL, re.I),  # HTML (also raw in Markdown)
    re.compile(r"\b(?:image|figure)::?\s*" + _URL, re.I),  # reST .. image:: url, AsciiDoc image::url[]
    re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{" + _URL, re.I),  # LaTeX
    re.compile(r"(?:xlink:href|fileref|url)\s*=\s*[\"']" + _URL, re.I),  # DocBook, JATS, FB2
]
# Any URL that ends like an image (Textile !url!, Org [[url]], bare URLs). A weak hit: it may be a link target, such as
# Wikipedia's File:…svg pages around thumbnails, so it is skipped after href= or a Markdown link's ]( and a failed
# download is not reported (pandoc's filter still warns if the URL really is an image in the document).
_ANY_IMAGE = re.compile(r"(https?://[^\s\"'<>()\[\]|!]+\.(?:png|jpe?g|gif|svg|webp|bmp|tiff?|avif)(?:\?[^\s\"'<>()\[\]|!]*)?)", re.I)
_LINK_BEFORE = re.compile(r"(?:\]\(\s*<?|href\s*=\s*[\"']?|<)$", re.I)


def find_remote_images(inputs: Sequence[Path], reader: str, stdin_text: str | None = None, limit_bytes: int = 64 * 1024 * 1024, weak: set[str] | None = None) -> list[str]:
    """http(s) image URLs in the inputs, in order of appearance, without duplicates. URLs found only by the loose
    'ends like an image' rule are also added to `weak`."""
    base = reader.split("+")[0].split("-")[0]
    if base not in TEXT_READERS:
        return []
    texts: list[str] = []
    if stdin_text is not None:
        texts.append(stdin_text)
    for p in inputs:
        try:
            with open(p, "rb") as f:
                texts.append(f.read(limit_bytes).decode("utf-8", "replace"))
        except OSError:
            continue
    seen: dict[str, None] = {}
    for text in texts:
        if "//" not in text:
            continue
        hits: list[tuple[int, str]] = []
        for rx in PATTERNS:
            for m in rx.finditer(text):
                hits.append((m.start(1), m.group(1)))
        strong = {u.rstrip(".,;:").replace("&amp;", "&") for _, u in hits}
        for m in _ANY_IMAGE.finditer(text):
            url = m.group(1).rstrip(".,;:").replace("&amp;", "&")
            if url in strong or _LINK_BEFORE.search(text[max(0, m.start(1) - 16) : m.start(1)]):
                continue
            hits.append((m.start(1), m.group(1)))
            if weak is not None:
                weak.add(url)
        labels = {x.strip().lower() for x in _IMG_REF.findall(text) if x.strip()}
        if labels:  # reference-style images: ![alt][badge] … [badge]: https://img.shields.io/…
            for m in _REF_DEF.finditer(text):
                if m.group(1).strip().lower() in labels:
                    hits.append((m.start(2), m.group(2)))
        for _, url in sorted(hits):
            url = url.rstrip(".,;:")
            seen.setdefault(url.replace("&amp;", "&"), None)
    return list(seen)


def _fetch(url: str, dest: Path, deadline: float) -> dict[str, Any]:
    import urllib.request

    full = "https:" + url if url.startswith("//") else url
    left = deadline - time.time()
    if left <= 0.5:
        return {"url": url, "error": "time budget used up"}
    req = urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0 (Desk markup-ebooks)", "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=min(TIMEOUT, left)) as r:  # noqa: S310 — http(s) only, checked above
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            chunks: list[bytes] = []
            size = 0
            while True:  # in pieces, so a server that trickles data cannot outlast the budget
                if time.time() > deadline:
                    return {"url": url, "error": "time budget used up"}
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            data = b"".join(chunks)
    except Exception as e:  # noqa: BLE001 — network, TLS, HTTP errors: the image becomes a link
        return {"url": url, "error": str(e)[:160] or type(e).__name__}
    if len(data) > MAX_BYTES:
        return {"url": url, "error": f"larger than {MAX_BYTES // (1024 * 1024)} MB"}
    ext = EXT.get(ctype) or _sniff(data)
    if not ext:
        return {"url": url, "error": f"not an image ({ctype or 'unknown type'})"}
    name = hashlib.sha256(full.encode()).hexdigest()[:20] + ext
    (dest / name).write_bytes(data)
    return {"url": url, "file": name, "bytes": len(data)}


def _sniff(data: bytes) -> str | None:
    head = data[:512]
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if b"<svg" in head.lower():
        return ".svg"
    return None


def _run_all(urls: list[str], dest: Path, deadline: float) -> list[dict[str, Any]]:
    """Fetches on daemon threads and stops waiting at the deadline: a request stuck in DNS or a silent server can
    neither hold up the conversion nor keep the process alive (socket timeouts do not cover name lookups)."""
    import queue
    import threading

    todo: "queue.Queue[str]" = queue.Queue()
    for u in urls:
        todo.put(u)
    done: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def worker() -> None:
        while True:
            try:
                u = todo.get_nowait()
            except queue.Empty:
                return
            r = _fetch(u, dest, deadline)
            with lock:
                done[u] = r

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(6, len(urls)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, deadline - time.time()) + 0.5)
    with lock:
        return [done.get(u) or {"url": u, "error": "time budget used up"} for u in urls]


def download(urls: Sequence[str], dest: Path, offline: bool = False, weak: set[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Downloads the images into dest; returns ({url: file name in dest}, warnings). Failures of `weak` URLs (maybe
    links, not images) are not reported."""
    if not urls:
        return {}, []
    warnings: list[str] = []
    if offline or os.environ.get("DESK_OFFLINE", "").lower() in ("1", "true", "yes"):
        n = sum(1 for u in urls if not (weak and u in weak))
        if n:
            warnings.append(f"{n} remote image(s) not downloaded (--offline); they are links in the output")
        return {}, warnings
    wanted = list(urls)[:MAX_IMAGES]
    if len(urls) > MAX_IMAGES:
        warnings.append(f"{len(urls) - MAX_IMAGES} remote image(s) beyond the first {MAX_IMAGES} were not downloaded")
    dest.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + BUDGET
    results = _run_all(wanted, dest, deadline)
    mapping: dict[str, str] = {}
    failed = []
    for r in results:
        if "file" in r:
            mapping[r["url"]] = r["file"]
            if r["url"].startswith("//"):
                mapping["https:" + r["url"]] = r["file"]
        elif not (weak and r["url"] in weak):
            failed.append(r)
    for r in failed[:8]:
        warnings.append(f"remote image not downloaded: {r['url'][:120]} ({r['error']}); kept as a link")
    if len(failed) > 8:
        warnings.append(f"{len(failed) - 8} more remote image(s) not downloaded; kept as links")
    return mapping, warnings


def filter_args(inputs: Sequence[Path], reader: str, work: Path, offline: bool = False, stdin_text: str | None = None, prefix: str | None = None) -> tuple[list[str], list[str]]:
    """pandoc arguments that keep pandoc off the network: local copies of the remote images (downloaded into work,
    referenced as prefix/name or absolute paths) and templates/desk-remote.lua. Returns (args, warnings)."""
    from _mk import TEMPLATES

    map_file = work / "remote-map.json"
    if map_file.exists():  # a second pandoc run in the same build (the Typst retry): reuse the downloads
        return [f"--lua-filter={TEMPLATES / 'desk-remote.lua'}", "-M", f"desk-remote-map={map_file.resolve()}"], []
    weak: set[str] = set()
    urls = find_remote_images(inputs, reader, stdin_text, weak=weak)
    mapping, warnings = download(urls, work, offline, weak)
    if prefix is not None:
        refs = {u: f"{prefix}/{name}" for u, name in mapping.items()}
    else:
        refs = {u: str((work / name).resolve()) for u, name in mapping.items()}
    work.mkdir(parents=True, exist_ok=True)
    map_file.write_text(json.dumps(refs), encoding="utf-8")
    return [f"--lua-filter={TEMPLATES / 'desk-remote.lua'}", "-M", f"desk-remote-map={map_file.resolve()}"], warnings
