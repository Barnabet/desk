#!/usr/bin/env python3
"""Self-test for the markup-ebooks skill: builds fixtures in a temp folder and runs every script as an agent would
(python3 scripts/<name>.py …), asserting on real outputs: conversions and round trips, typeset PDFs and rendered
PNG sizes, extraction quality, lint findings and fixes, EPUB structure, notebook outputs, big-file maps, budgets,
continuation commands and cache hits. No network. Prints "ok: N checks in S s" and exits 1 on any failure.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
CHECKS = 0
FAILS: list[str] = []
T0 = time.time()


def check(cond: bool, what: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{what}{': ' + detail[:600] if detail else ''}")
        print(f"FAIL {what} {detail[:600]}", file=sys.stderr)


def run(script: str, *args: str, env: dict[str, str] | None = None, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(ENV)
    if env:
        e.update(env)
    return subprocess.run([PY, str(HERE / script), *args], capture_output=True, text=True, env=e, input=stdin, cwd=str(cwd or WORK), timeout=240)


def ok(r: subprocess.CompletedProcess[str], what: str) -> str:
    check(r.returncode == 0, what, f"exit {r.returncode}: {r.stderr.strip()[-500:]}")
    return r.stdout


def timed(script: str, *args: str) -> tuple[float, subprocess.CompletedProcess[str]]:
    t = time.time()
    r = run(script, *args)
    return time.time() - t, r


def png_size(p: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(p) as im:
        return im.size


def make_png(path: Path, w: int = 320, h: int = 200, color: tuple[int, int, int] = (40, 90, 160)) -> None:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, w - 10, h - 10], outline=color, width=6)
    d.line([0, 0, w, h], fill=color, width=4)
    im.save(path)


def pdf_facts(path: Path) -> tuple[int, str, list[str]]:
    """(pages, text, bookmark titles) of a PDF, closing everything it opens."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        parts = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            parts.append(tp.get_text_range())
            tp.close()
            page.close()
        titles = [(bm.get_title() if hasattr(bm, "get_title") else bm.title) or "" for bm in pdf.get_toc()]
        return len(pdf), "\n".join(parts), titles
    finally:
        pdf.close()


def pdf_size(path: Path, index: int = 0) -> tuple[float, float]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[index]
        size = page.get_size()
        page.close()
        return size
    finally:
        pdf.close()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── fixtures ────────────────────────────────────────────────────────────

DOC_MD = """---
title: Field Notes
subtitle: A test document
author: [Ann Lee, Bo Chen]
date: 2026-09-25
abstract: Short abstract for the test.
---

# Introduction

Some *emphasis*, **strong**, `code`, a [link](https://typst.app) and a footnote.[^1]
As @knuth84 showed, literate programming matters [@knuth84, p. 99]. Missing [@nobody].

[^1]: The footnote text.

## Math

Inline $E = mc^2$ and display:

$$\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$

## Code

```python
def hello(name: str) -> str:
    return f"Hello {name}"
```

## Data

| Fruit | Qty | Price |
|:------|----:|------:|
| Apple | 3 | 1.20 |
| Pear | 10 | 0.80 |

: Fruit prices

![A chart](chart.png)

![Gone](missing.png)

# Conclusion

Done with FINALWORD.

# References
"""

BIB = """@article{knuth84,
  author = {Donald E. Knuth},
  title = {Literate Programming},
  journal = {The Computer Journal},
  year = {1984},
  volume = {27},
  number = {2},
  pages = {97--111}
}
"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>How Tides Work | Ocean Blog</title>
<meta property="og:title" content="How Tides Work">
<meta property="og:site_name" content="Ocean Blog">
<meta name="author" content="Mira Sol">
<meta property="article:published_time" content="2026-03-02T10:00:00Z">
<meta name="description" content="Why the sea rises and falls twice a day.">
<link rel="canonical" href="https://ocean.example/posts/tides">
<script type="application/ld+json">{"@type": "BlogPosting", "headline": "How Tides Work", "author": {"name": "Mira Sol"}}</script>
<script>var tracking = "NAVSCRIPT";</script><style>.x{color:red}</style>
</head><body>
<header class="site-header"><nav class="menu"><a href="/">Home</a> <a href="/about">About us</a> <a href="/shop">Shop</a></nav></header>
<div id="cookie-banner" class="cookie-consent">We use cookies COOKIETEXT. <button>Accept</button></div>
<div class="layout">
<aside class="sidebar"><h3>Popular</h3><ul><li><a href="/p/1">SIDEBARLINK one</a></li><li><a href="/p/2">Another popular post</a></li></ul></aside>
<main><article class="post">
<h1>How Tides Work</h1>
<p class="byline">By Mira Sol</p>
<p>The ocean rises and falls twice each day, and the reason is gravity, mostly the Moon's, with the Sun adding its share. This article explains the forces, the bulges, and why some coasts see enormous ranges while others barely notice.</p>
<h2>The Moon's pull</h2>
<p>The Moon pulls on the near side of the Earth more strongly than on the far side, which stretches the oceans into two bulges, one facing the Moon and one opposite it, as the planet turns beneath them.</p>
<p>See <a href="/posts/gravity">our gravity primer</a> and <a href="https://en.wikipedia.org/wiki/Tide">the encyclopedia</a> for more, including historic measurements, older theories and the long story of tide prediction machines.</p>
<figure><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="/img/bulge.png" alt="Two tidal bulges"><figcaption>The two bulges</figcaption></figure>
<h2>Spring and neap tides</h2>
<p>When the Sun and Moon line up, their pulls add together, producing larger spring tides; at right angles they partly cancel, giving smaller neap tides, a rhythm that repeats roughly every two weeks.</p>
<table><caption>Tidal ranges</caption><thead><tr><th>Place</th><th>Range (m)</th></tr></thead>
<tbody><tr><td>Bay of Fundy</td><td>16.3</td></tr><tr><td>Bristol Channel</td><td>14.5</td></tr><tr><td>Mediterranean</td><td>0.3</td></tr></tbody></table>
<pre><code class="language-python">def tide(t):
    return amplitude * cos(omega * t)
</code></pre>
<ul><li>High water happens about every 12 hours 25 minutes, give or take local effects.</li><li>Coastal shape can amplify the range, sometimes dramatically.</li></ul>
<div class="share-buttons social"><a href="https://twitter.com/share">SHARETEXT Twitter</a> <a href="https://facebook.com/share">Facebook</a></div>
</article></main></div>
<footer class="site-footer"><p>FOOTERTEXT Copyright Ocean Blog</p><a href="/privacy">Privacy</a></footer>
</body></html>
"""

NOTEBOOK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><rect x="10" y="10" width="180" height="100" fill="#3a7"/><text x="20" y="60">SVG</text></svg>'


def make_notebook(path: Path, png: bytes) -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Analysis\n", "Intro text about NBMARK."]},
            {"cell_type": "code", "execution_count": 1, "metadata": {"scrolled": True}, "source": ["import math\n", "print('hello nb')"], "outputs": [{"output_type": "stream", "name": "stdout", "text": ["hello nb\n"]}]},
            {"cell_type": "code", "execution_count": 2, "metadata": {}, "source": "plot()", "outputs": [{"output_type": "display_data", "metadata": {}, "data": {"image/png": base64.b64encode(png).decode(), "text/plain": ["<Figure size 640x480 with 1 Axes>"]}}]},
            {"cell_type": "code", "execution_count": 3, "metadata": {}, "source": "svg()", "outputs": [{"output_type": "execute_result", "execution_count": 3, "metadata": {}, "data": {"image/svg+xml": NOTEBOOK_SVG, "text/plain": ["<svg>"]}}]},
            {"cell_type": "code", "execution_count": 4, "metadata": {}, "source": "1/0", "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": ["\u001b[0;31mZeroDivisionError\u001b[0m Traceback", "----> 1 1/0", "ZeroDivisionError: division by zero"]}]},
            {"cell_type": "markdown", "metadata": {}, "source": "## Results\n\nThe end."},
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python"}, "widgets": {"state": {}}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb), encoding="utf-8")


def big_html(path: Path, sections: int) -> None:
    r = random.Random(3)
    words = "river mountain archive ledger quantum harbor lantern meadow copper signal lorem ipsum dolor sit amet".split()
    parts = ['<html><head><meta charset="utf-8"><title>Big Page</title></head><body><nav class="menu">' + "".join(f'<a href="/n{i}">nav {i}</a>' for i in range(50)) + "</nav><main><article><h1>Big Page</h1>"]
    for s in range(1, sections + 1):
        parts.append(f'<h2 id="s{s}">Section {s} {r.choice(words)}</h2>')
        for sub in (1, 2):
            parts.append(f"<h3>Part {s}.{sub}</h3>")
            for _ in range(4):
                parts.append("<p>" + " ".join(r.choice(words) for _ in range(110)) + f' see <a href="/x/{s}">link {s}</a> MARK{s}.</p>')
        parts.append("<script>" + "var d='" + "x" * 3000 + "';</script>")
    parts.append("</article></main></body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def broken_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("mimetype", "application/epub+zip")  # wrong place and compressed
        z.writestr("OPS/book.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x1</dc:identifier><dc:title>Broken</dc:title><dc:language>en</dc:language></metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/><itemref idref="ghost"/></spine></package>')
        z.writestr("OPS/c1.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title></head><body><h1>One</h1><p>Text <a href="c3.xhtml">bad link</a> <img src="nope.png" alt=""/></p></body></html>')
        z.writestr("OPS/nav.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li></ol></nav></body></html>')
        z.writestr(".DS_Store", "junk")


# ── regression fixtures (defects found by acceptance testing) ──────────


def zip_epub(path: Path, docs: dict[str, str], nav: str, extra: dict[str, bytes] | None = None, title: str = "Book") -> None:
    """A minimal EPUB 3: docs {file name: body html} in spine order, a nav <ol>, extra raw members."""
    items = "".join(f'<item id="d{k}" href="{n}" media-type="application/xhtml+xml"/>' for k, n in enumerate(docs))
    spine = "".join(f'<itemref idref="d{k}"/>' for k in range(len(docs)))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier><dc:title>{title}</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{items}</manifest><spine>{spine}</spine></package>')
        z.writestr("OEBPS/nav.xhtml", '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>nav</title></head><body><nav epub:type="toc"><ol>' + nav + "</ol></nav></body></html>")
        for n, body in docs.items():
            z.writestr(f"OEBPS/{n}", body if body.startswith("<?xml") else f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{n}</title></head><body>{body}</body></html>')
        for n, data in (extra or {}).items():
            z.writestr(n, data)


def parts_epub(path: Path, parts: int = 2, chapters: int = 3) -> None:
    """Parts whose chapters are separate files (the TOC nests chapters and scenes under each part)."""
    rnd = random.Random(4)
    words = "tide moon harbor current shoal estuary delta reef swell basin".split()
    docs: dict[str, str] = {}
    nav = []
    n = 0
    for p in range(1, parts + 1):
        lis = []
        for _ in range(chapters):
            n += 1
            name = f"ch{n:02d}.xhtml"
            body = (f"<h1>Part {p}</h1>" if not lis else "") + f"<h2>Chapter {n}</h2>"
            for sc in (1, 2):
                body += f'<h3 id="s{sc}">Scene {sc}</h3>' + "".join("<p>" + " ".join(rnd.choice(words) for _ in range(40)) + f" CHWORD{n}.</p>" for _ in range(2))
            docs[name] = body
            lis.append(f'<li><a href="{name}">Chapter {n}</a><ol><li><a href="{name}#s1">Scene 1</a></li><li><a href="{name}#s2">Scene 2</a></li></ol></li>')
        nav.append(f'<li><a href="ch{(p - 1) * chapters + 1:02d}.xhtml">Part {p}</a><ol>{"".join(lis)}</ol></li>')
    zip_epub(path, docs, "".join(nav), title="Saga")


def liar_epub(path: Path) -> None:
    """A chapter whose zip headers declare 2,000 bytes for 3 MB of text (sizes patched after writing)."""
    import struct

    big = "<p>" + "B" * (3 << 20) + "</p>"
    zip_epub(path, {"ch1.xhtml": big}, '<li><a href="ch1.xhtml">One</a></li>')
    data = bytearray(path.read_bytes())
    real = len(f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch1.xhtml</title></head><body>{big}</body></html>'.encode())
    name = b"OEBPS/ch1.xhtml"
    i = data.find(b"PK\x01\x02")
    while i != -1:
        n = struct.unpack("<H", data[i + 28 : i + 30])[0]
        if data[i + 46 : i + 46 + n] == name:
            struct.pack_into("<I", data, i + 24, 2000)
            struct.pack_into("<I", data, struct.unpack("<I", data[i + 42 : i + 46])[0] + 22, 2000)
        i = data.find(b"PK\x01\x02", i + 4)
    j = data.find(b"PK\x07\x08")
    while j != -1:
        if struct.unpack("<I", data[j + 12 : j + 16])[0] == real:
            struct.pack_into("<I", data, j + 12, 2000)
        j = data.find(b"PK\x07\x08", j + 4)
    path.write_bytes(bytes(data))


def page_lines(path: Path, top: bool = True) -> list[str]:
    """The running head (top) or foot of every page of a PDF."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            tp = page.get_textpage()
            text = tp.get_text_bounded(0, h - 50, w, h) if top else tp.get_text_bounded(0, 0, w, 45)
            out.append(" ".join(text.split()))
            tp.close()
            page.close()
        return out
    finally:
        pdf.close()


WIKI_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Typecasting - Wikipedia</title>
<meta property="og:title" content="Typecasting - Wikipedia"><meta property="article:published_time" content="2003-01-15T02:34:42Z">
<meta property="article:modified_time" content="2026-09-20T10:00:00Z"></head><body>
<nav class="vector-menu"><a href="/wiki/Main">Main page</a> <a href="/wiki/Random">Random</a></nav>
<main><h1>Typecasting</h1><div class="mw-content-ltr mw-parser-output">
<section data-mw-section-id="0"><p><b>Typecasting</b> is LEADWORD the composition of text for publication, display or distribution by arranging physical type or digital glyphs, a craft with a long history.</p></section>
<section data-mw-section-id="1"><h2>History</h2>""" + "".join(f"<p>Paragraph {i} of the history, with commas, clauses, more clauses, and details about compositors, cases, sorts and presses that make it long enough to score.</p>" for i in range(8)) + """</section>
<section data-mw-section-id="2"><h2>Modern era</h2>""" + "".join(f"<p>Modern paragraph {i}, about phototypesetting, desktop publishing, fonts, rendering, and the tools, programs and printers of today.</p>" for i in range(6)) + """</section>
<section data-mw-section-id="3"><h2>References</h2><ul>""" + "".join(f'<li><a href="/wiki/R{i}">Reference {i}</a></li>' for i in range(20)) + """</ul></section>
</div></main></body></html>"""


# ── tests ───────────────────────────────────────────────────────────────


def test_help() -> None:
    slowest = 0.0
    for s in ("mk_convert.py", "mk_render.py", "mk_read.py", "html_extract.py", "md_check.py", "epub_tool.py", "nb_tool.py"):
        t = time.time()
        r = run(s, "--help")
        dt = time.time() - t
        slowest = max(slowest, dt)
        check(r.returncode == 0 and "Examples" in r.stdout and "python3 scripts/" in r.stdout, f"{s} --help has examples", r.stderr)
    check(slowest < 3.0, "--help is fast", f"slowest {slowest:.2f}s")
    r = run("mk_convert.py", "nothing-here.md", "x.pdf")
    check(r.returncode == 1 and r.stderr.startswith("error:") and "Traceback" not in r.stderr, "missing input is one error line", r.stderr)
    r = run("mk_convert.py", "doc.md")
    check(r.returncode == 2, "usage error exits 2", r.stderr)


def test_convert() -> None:
    out = ok(run("mk_convert.py", "doc.md", "doc.pdf", "--toc", "-N", "--bibliography", "refs.bib"), "md → pdf")
    check("wrote doc.pdf" in out and "pages" in out, "pdf summary", out)
    check("unresolved citation: @nobody" in out, "reports unresolved citations", out)
    check("missing image" in out and "missing.png" in out, "reports missing images", out)
    n, text, toc = pdf_facts(WORK / "doc.pdf")
    check(n >= 2 and "Introduction" in text and "Knuth" in text and "FINALWORD" in text, "pdf content (headings, citation, body)", text[:300])
    check("Contents" in text and "1.1" in text, "pdf TOC and numbering", text[:400])
    check("Introduction" in " ".join(toc), "pdf bookmarks from headings", str(toc))
    r = run("mk_convert.py", "doc.md", "doc.pdf")
    check(r.returncode == 1 and "already exists" in r.stderr, "refuses to overwrite without --force", r.stderr)
    for style in ("report", "book"):
        ok(run("mk_convert.py", "doc.md", f"doc-{style}.pdf", "--template", style, "--toc"), f"{style} template")
    n1, _, _ = pdf_facts(WORK / "doc-report.pdf")
    n2, _, _ = pdf_facts(WORK / "doc-book.pdf")
    check(n2 > n1 >= 3, "report/book layouts differ (title page, chapters)", f"{n1} {n2}")
    w, h = pdf_size(WORK / "doc-book.pdf")
    check(abs(w - 419.5) < 2 and abs(h - 595.3) < 2, "book is A5", f"{w}x{h}")
    ok(run("mk_convert.py", "doc.md", "letter.pdf", "--paper", "letter", "--columns", "2", "--footer", "Draft {page}/{pages}", "--accent", "teal"), "layout options")
    _, ltxt, _ = pdf_facts(WORK / "letter.pdf")
    check(abs(pdf_size(WORK / "letter.pdf")[0] - 612) < 2 and "Draft 1/" in ltxt, "paper size and footer placeholders", ltxt[-200:])
    out = ok(run("mk_convert.py", "doc.md", "doc.html"), "md → html")
    html = (WORK / "doc.html").read_text()
    check("<style" in html and "data:image/png;base64" in html and "<math" in html, "self-contained html with css, embedded image, MathML")
    ok(run("mk_convert.py", "doc.md", "doc.docx"), "md → docx")
    ok(run("mk_convert.py", "doc.docx", "back.md"), "docx → md")
    back = (WORK / "back.md").read_text()
    check("Introduction" in back and "| Apple" in back.replace("  ", " ") and (WORK / "back_media").exists(), "docx round trip keeps headings, table, images", back[:300])
    ok(run("mk_convert.py", "doc.md", "doc.epub", "--cover", "chart.png"), "md → epub")
    ok(run("mk_convert.py", "doc.md", "doc.typ", "--template", "report"), "md → typst source")
    check((WORK / "markup.typ").exists() and "desk-markup" in (WORK / "doc.typ").read_text(), "typst source uses Desk's template")
    ok(run("mk_convert.py", "doc.typ", "fromtyp.pdf"), "compile a .typ file")
    ok(run("mk_convert.py", "doc.md", "keep.pdf", "--typ-out", "src/keep.typ", "--render", "checkpng"), "--typ-out and --render")
    check((WORK / "src" / "keep.typ").exists() and (WORK / "src" / "markup.typ").exists() and any((WORK / "checkpng").glob("*.png")), "typst source and check renders kept")
    # other readers
    samples = {
        "s.rst": "Title\n=====\n\nSome *rst* text RSTWORD.\n\n- item\n",
        "s.tex": "\\documentclass{article}\\begin{document}\\section{Intro}TEXWORD $x^2$\\end{document}\n",
        "s.org": "* Heading\nORGWORD text\n",
        "s.adoc": "= Title\n\n== Section\n\nADOCWORD text.\n",
        "s.textile": "h1. Title\n\nTEXTILEWORD text.\n",
        "s.wiki": "== Heading ==\nWIKIWORD text.\n",
        "s.typ": "= Heading\nTYPWORD text.\n",
        "db.xml": '<?xml version="1.0"?><article xmlns="http://docbook.org/ns/docbook" version="5.0"><title>T</title><section><title>S</title><para>DOCBOOKWORD</para></section></article>',
        "jats.xml": '<?xml version="1.0"?><!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.2 20190208//EN" "JATS-journalpublishing1.dtd"><article article-type="research-article"><front><article-meta><title-group><article-title>T</article-title></title-group></article-meta></front><body><sec><title>S</title><p>JATSWORD</p></sec></body></article>',
    }
    for name, content in samples.items():
        (WORK / name).write_text(content, encoding="utf-8")
        word = re.search(r"[A-Z]{3,}WORD", content).group(0)  # type: ignore[union-attr]
        r = run("mk_convert.py", name, "-o", "-", "--to", "md")
        check(r.returncode == 0 and word in r.stdout, f"reads {name}", r.stderr[-300:] + r.stdout[:200])
    ok(run("mk_convert.py", "doc.md", "doc.fb2"), "md → fb2")
    ok(run("mk_convert.py", "doc.md", "doc.rtf"), "md → rtf")
    for name in ("doc.fb2", "doc.rtf"):
        r = run("mk_convert.py", name, "-o", "-", "--to", "md")
        check(r.returncode == 0 and "FINALWORD" in r.stdout, f"reads back {name}", r.stderr[-300:])
    out = ok(run("mk_convert.py", "--list-formats"), "list formats")
    check("asciidoc" in out and "typst" in out and "pdf (through Typst)" in out, "readers and writers listed")
    (WORK / "a.md").write_text("# A\n\nalpha\n")
    (WORK / "b.md").write_text("# B\n\nbeta\n")
    out = ok(run("mk_convert.py", "a.md", "b.md", "--to", "html", "--out-dir", "site"), "batch conversion")
    check((WORK / "site" / "a.html").exists() and (WORK / "site" / "b.html").exists(), "batch outputs", out)
    ok(run("mk_convert.py", "a.md", "b.md", "joined.md", "--to", "gfm"), "join inputs")
    j = (WORK / "joined.md").read_text()
    check("alpha" in j and "beta" in j, "joined document has both inputs")
    r = run("mk_convert.py", "-", "-o", "-", "--to", "html", "--fragment", stdin="Hello *stdin*\n")
    check(r.returncode == 0 and "<em>stdin</em>" in r.stdout, "stdin to stdout", r.stderr)
    # the same result without LibreOffice (this skill never needs it)
    r = run("mk_convert.py", "a.md", "nolo.pdf", env={"DESK_SOFFICE": "none"})
    check(r.returncode == 0, "works with LibreOffice hidden", r.stderr)


def test_cache() -> None:
    big = WORK / "long.md"
    paras = []
    rnd = random.Random(5)
    words = "alpha beta gamma delta epsilon zeta theta kappa lambda sigma omega".split()
    for s in range(1, 121):
        paras.append(f"## Part {s}\n")
        for _ in range(12):
            paras.append(" ".join(rnd.choice(words) for _ in range(90)) + ".\n")
    big.write_text("# Long\n\n" + "\n".join(paras), encoding="utf-8")
    t1, r1 = timed("mk_render.py", "long.md", "--pages", "1", "--out", "longpng")
    t2, r2 = timed("mk_render.py", "long.md", "--pages", "2", "--out", "longpng")
    check(r1.returncode == 0 and r2.returncode == 0 and "cached PDF" in r2.stdout, "second render reuses the cached PDF", r2.stdout + r2.stderr)
    check(t1 >= 5 * t2 or t1 - t2 > 1.5, "cached render is much faster", f"cold {t1:.2f}s, cached {t2:.2f}s")
    TIMES["render cold/cached"] = (t1, t2)
    (WORK / "chart2.png").write_bytes((WORK / "chart.png").read_bytes())
    big2 = WORK / "long2.md"
    big2.write_text(big.read_text() + "\n![pic](chart2.png)\n", encoding="utf-8")
    ok(run("mk_convert.py", "long2.md", "l2a.pdf"), "cache: first conversion with an image")
    make_png(WORK / "chart2.png", 300, 180, (200, 30, 30))
    os.utime(WORK / "chart2.png", (time.time() + 5, time.time() + 5))
    out = ok(run("mk_convert.py", "long2.md", "l2b.pdf"), "cache: image changed")
    check("from cache" not in out, "a changed image invalidates the cached PDF", out)


def test_render() -> None:
    out = ok(run("mk_render.py", "doc.md", "--sheet", "--toc"), "render doc")
    pngs = sorted((WORK / "doc-pages").glob("doc-*.png"))
    pages = [p for p in pngs if "sheet" not in p.name]
    check(len(pages) >= 2 and "Look at them with view_image." in out, "one PNG per page + view hint", out)
    if pages:
        w, h = png_size(pages[0])
        check(max(w, h) == 1568, "pages sized for vision (1568 px)", f"{w}x{h}")
    check((WORK / "doc-pages" / "doc-sheet.png").exists(), "contact sheet")
    out = ok(run("mk_render.py", "doc.md", "--section", "Conclusion", "--out", "sec"), "render a section")
    check("rendered page" in out, "section pages", out)
    out = ok(run("mk_render.py", "doc.md", "--find", "Fruit prices", "--out", "find", "--format", "json"), "render where a phrase is")
    d = json.loads(out)
    check(d["page_numbers"] and all(max(s) == 1568 for s in d["sizes"]), "find pages + sizes", out[:300])
    ok(run("mk_render.py", "doc.typ", "--out", "typ", "--dpi", "72"), "render .typ")
    ok(run("mk_render.py", "book.epub", "--pages", "1-2", "--template", "book", "--out", "bookpng"), "render epub")
    check(len(list((WORK / "bookpng").glob("*.png"))) == 2, "epub pages rendered")
    ok(run("mk_render.py", "doc.pdf", "--pages", "last", "--out", "pdfpng"), "render a pdf")


def test_read() -> None:
    out = ok(run("mk_read.py", "doc.md"), "read md")
    check("FINALWORD" in out and "title: Field Notes" in out, "small md printed whole")
    out = ok(run("mk_read.py", "doc.md", "--outline"), "outline")
    check("| 1.1 |" in out and "Math" in out and "| 2 |" in out, "outline addresses", out)
    out = ok(run("mk_read.py", "doc.md", "--section", "Data"), "section by heading")
    check("| Apple" in out and "FINALWORD" not in out, "section text only", out)
    out = ok(run("mk_read.py", "doc.md", "--lines", "10-12"), "line range")
    check(out.split("\n")[:3] == DOC_MD.split("\n")[9:12], "exactly lines 10-12", out)
    out = ok(run("mk_read.py", "s.rst", "--text"), "read rst as text")
    check("RSTWORD" in out and "*" not in out, "plain text", out)
    out = ok(run("mk_read.py", "nb.ipynb"), "read notebook")
    check("cell 5" in out and "ZeroDivisionError" in out, "notebook cells with addresses", out[:300])
    # big: map first, sections by address, search with addresses, continuation, cache
    t1, r1 = timed("mk_read.py", "big.html")
    out = ok(r1, "big html map")
    check(": map (" in out and "--section ADDR" in out and "| 1.1" in out, "map instead of a dump", out[:600])
    check(len(out) < 60_000, "map fits the budget", str(len(out)))
    t2, r2 = timed("mk_read.py", "big.html")
    check(r2.returncode == 0 and (t1 >= 5 * t2 or t1 - t2 > 1.5), "cached map is much faster", f"cold {t1:.2f}s, cached {t2:.2f}s")
    TIMES["read big html cold/cached"] = (t1, t2)
    out = ok(run("mk_read.py", "big.html", "--section", "1.7"), "big section")
    check(out.startswith("## Section 7") and "MARK7." in out and "MARK8." not in out, "exact section", out[:200])
    out = ok(run("mk_read.py", "big.html", "--find", "MARK33.", "--max-hits", "2"), "big find")
    check("§1.33" in out and "line" in out, "hits carry section addresses", out[:400])
    out = ok(run("mk_read.py", "big.html", "--all", "--max-chars", "5000"), "paged dump")
    m = re.search(r"Continue: (python3 scripts/mk_read\.py .*--offset (\d+))\]", out)
    check(bool(m), "cut output names the continuation command", out[-300:])
    if m:
        out2 = ok(run("mk_read.py", "big.html", "--all", "--max-chars", "5000", "--offset", m.group(2)), "continue")
        check(len(out2) > 1000 and out2[:200] != out[:200], "continuation shows the next part")
    out = ok(run("mk_read.py", "book.epub"), "read epub")
    check("Chapter Two" in out or "ch" in out, "epub text or map", out[:300])
    out = ok(run("mk_read.py", "book.epub", "--find", "THIRDCHAPTER"), "epub find")
    check(re.search(r"- ch \d+ / t\d+ \(Chapter Three\) line \d+", out) is not None, "epub hit with chapter and TOC address", out)
    hit = json.loads(ok(run("mk_read.py", "book.epub", "--find", "SECONDCHAPTER", "--format", "json"), "epub find json"))["hits"][0]
    out = ok(run("mk_read.py", "book.epub", "--chapters", str(hit["chapter"]), "--text"), "epub chapter by the hit's address")
    check("SECONDCHAPTER" in out and "FIRSTCHAPTER" not in out and "<!--" not in out, "epub chapter text", out[:300])
    out = ok(run("mk_read.py", "book.epub", "--section", hit["toc"]), "epub TOC entry by address")
    check("SECONDCHAPTER" in out and "THIRDCHAPTER" not in out and "<!-- toc" not in out, "TOC entry text (no internal markers)", out[:300])


def test_extract() -> None:
    out = ok(run("html_extract.py", "page.html", "--url", "https://ocean.example/posts/tides"), "extract article")
    check(out.startswith("# How Tides Work") and "*By Mira Sol · 2 March 2026 · Ocean Blog*" in out, "title, byline, readable date", out[:300])
    for junk in ("NAVSCRIPT", "COOKIETEXT", "SIDEBARLINK", "SHARETEXT", "FOOTERTEXT", "About us"):
        check(junk not in out, f"boilerplate removed: {junk}", out)
    check("## The Moon's pull" in out and "| Bay of Fundy | 16.3 |" in out and "```python" in out, "headings, table, code kept", out)
    check("https://ocean.example/img/bulge.png" in out and "(https://ocean.example/posts/gravity)" in out, "absolute image (lazy data-src) and link URLs", out)
    check(out.count("How Tides Work") == 1, "title not repeated")
    out = ok(run("html_extract.py", "page.html", "--text"), "extract text")
    check("Moon pulls" in out and "](" not in out and "**" not in out, "plain text", out[:300])
    out = ok(run("html_extract.py", "page.html", "--links", "--format", "json"), "links json")
    d = json.loads(out)
    urls = [x["url"] for x in d["links"]]
    check("https://en.wikipedia.org/wiki/Tide" in urls and not any("/about" in u for u in urls), "main-content links only", str(urls))
    out = ok(run("html_extract.py", "page.html", "--links", "--all", "--format", "json"), "all links")
    check(any(x["url"].endswith("/about") for x in json.loads(out)["links"]), "--all includes navigation links")
    r = run("html_extract.py", "big.html", "--links", "--all", env={"DESK_HTML_STREAM_MB": "0.5"})
    check(r.returncode == 0 and "(streamed)" in r.stdout and "/x/40" in r.stdout, "huge pages stream links", r.stdout[:300] + r.stderr)
    out = ok(run("html_extract.py", "page.html", "--tables"), "tables")
    check("Tidal ranges" in out and "| Bristol Channel | 14.5 |" in out, "table markdown with caption", out)
    ok(run("html_extract.py", "page.html", "--tables", "--csv", "csv"), "tables csv")
    csvf = WORK / "csv" / "table-1.csv"
    check(csvf.exists() and "Bay of Fundy,16.3" in csvf.read_text(), "csv file")
    out = ok(run("html_extract.py", "page.html", "--images", "--url", "https://ocean.example/posts/tides"), "images")
    check("https://ocean.example/img/bulge.png" in out and "The two bulges" in out, "image list with caption", out)
    out = ok(run("html_extract.py", "page.html", "--meta", "--format", "json"), "meta")
    m = json.loads(out)
    check(m.get("site") == "Ocean Blog" and m.get("lang") == "en" and m.get("url") == "https://ocean.example/posts/tides", "metadata", out)
    ok(run("html_extract.py", "page.html", "-o", "article.md"), "write article to file")
    check((WORK / "article.md").read_text().startswith("# How Tides Work"), "article file")
    t1, r1 = timed("html_extract.py", "big.html")
    t2, r2 = timed("html_extract.py", "big.html")
    check(r1.returncode == 0 and r1.stdout == r2.stdout and (t1 >= 5 * t2 or t1 - t2 > 1.5), "cached extraction is much faster", f"cold {t1:.2f}s, cached {t2:.2f}s")
    TIMES["extract big html cold/cached"] = (t1, t2)


BAD_MD = """---
title: Test
tags: [a, b
---

# Title

<!-- toc -->
- [Old](#old)
<!-- tocstop -->

## Intro

See [setup](./setup.md#install), [missing anchor](setup.md#nope), [the intro](#Intro), [nowhere](#nowhere), [gone](missing.md) and [empty]().
![](pic.png) ![logo](logo.png)

#### Deep jump

##Bad heading

## Intro

Uses [ref][r1] and [missing][r9]. Note[^1] and [^2].

[r1]: https://example.com

[^1]: A note.

| a | b | c |
|---|---|---|
| 1 | 2 |



```python
x = 1
"""


def test_md_check() -> None:
    (WORK / "docs").mkdir()
    (WORK / "docs" / "bad.md").write_text(BAD_MD, encoding="utf-8")
    (WORK / "docs" / "setup.md").write_text("# Setup\n\n## Install\n\nSteps.\n", encoding="utf-8")
    make_png(WORK / "docs" / "logo.png", 40, 40)
    before = sha(WORK / "docs" / "bad.md")
    out = run("md_check.py", "docs/bad.md", "--format", "json").stdout
    d = json.loads(out)
    rules = {p["rule"] for p in d["files"][0]["problems"]}
    for rule in ("front-matter", "anchor-missing", "link-broken", "link-empty", "image-missing", "image-alt", "heading-increment", "heading-space", "heading-duplicate", "ref-undefined", "footnote-undefined", "table-columns", "fence-unclosed", "toc-stale"):
        check(rule in rules, f"md_check finds {rule}", str(sorted(rules)))
    msgs = " ".join(p["message"] for p in d["files"][0]["problems"])
    check("setup.md#nope" in msgs and "#install" not in msgs.replace("#install?", ""), "cross-file anchors checked", msgs)
    check("did you mean #intro" in msgs, "anchor suggestion", msgs)
    out = ok(run("md_check.py", "docs/bad.md", "--fix", "fixed.md"), "md_check --fix")
    check("wrote fixed.md" in out and "heading spaces" in out and "table of contents refreshed" in out, "fix summary", out)
    fixed = (WORK / "fixed.md").read_text()
    check("## Bad heading" in fixed and "(#intro)" in fixed and fixed.rstrip().endswith("```") and "| 1 | 2 | |" in fixed, "safe fixes applied", fixed)
    check("- [Bad heading](#bad-heading)" in fixed, "toc regenerated", fixed[:500])
    check(sha(WORK / "docs" / "bad.md") == before, "input unchanged")
    out = ok(run("md_check.py", "doc.md", "--stats", "--format", "json"), "stats")
    st = json.loads(out)["files"][0]["stats"]
    check(st["words"] > 40 and st["tables"] == 1 and st["code_blocks"] == 1 and st["reading_time"] == "1 min", "stats values", str(st))
    out = ok(run("md_check.py", "docs"), "folder check")
    check("docs/bad.md" in out and "docs/setup.md" in out, "every file in the folder", out[-300:])
    out = ok(run("md_check.py", "doc.md", "--toc"), "toc")
    check("- [Introduction](#introduction)" in out and "  - [Math](#math)" in out, "toc links", out)
    ok(run("md_check.py", "fixed.md", "--fix", "fixed2.md", "--add-toc"), "fix idempotent")
    r = run("md_check.py", "docs/bad.md", "--strict")
    check(r.returncode == 1, "--strict fails on errors")


def test_epub() -> None:
    chapters = []
    for i, name in enumerate(("One", "Two", "Three"), 1):
        f = WORK / f"ch{i}.md"
        marker = ["FIRSTCHAPTER", "SECONDCHAPTER", "THIRDCHAPTER"][i - 1]
        f.write_text(f"# Chapter {name}\n\nText of chapter {name.lower()} {marker}.\n\n## Scene\n\nMore text.\n", encoding="utf-8")
        chapters.append(f.name)
    out = ok(run("epub_tool.py", "build", *chapters, "-o", "book.epub", "--title", "Tides Book", "--author", "M. Rao", "--cover", "chart.png", "--lang", "en", "-M", "publisher=Desk Press"), "build epub")
    check("wrote book.epub" in out and "epub check" not in out, "built epub passes its check", out)
    out = ok(run("epub_tool.py", "info", "book.epub", "--format", "json"), "epub info")
    d = json.loads(out)
    check(d["metadata"].get("title") == "Tides Book" and "M. Rao" in d["metadata"].get("creators", []) and d["cover"] and d["version"].startswith("3"), "metadata and cover", out[:500])
    check(d["metadata"].get("publisher") == "Desk Press" and d["words"] > 20, "publisher and words")
    out = ok(run("epub_tool.py", "toc", "book.epub"), "toc")
    check("Chapter Two" in out and "ch " in out, "toc with chapter numbers", out)
    out = ok(run("epub_tool.py", "check", "book.epub"), "check good epub")
    check("no problems" in out or " error" not in out, "no errors", out)
    out = ok(run("epub_tool.py", "read", "book.epub", "--section", "Chapter Two"), "read via epub_tool")
    check("SECONDCHAPTER" in out and "FIRSTCHAPTER" not in out, "chapter text by TOC title", out[:300])
    out = ok(run("epub_tool.py", "extract", "book.epub", "--cover", "cover-out.png", "--images", "imgs", "--sheet", "--all", "unz"), "extract")
    check((WORK / "cover-out.png").exists() and png_size(WORK / "cover-out.png") == png_size(WORK / "chart.png"), "cover extracted intact")
    check((WORK / "unz" / "mimetype").exists() and "Look at them with view_image." in out, "all files extracted + view hint", out)
    broken_epub(WORK / "broken.epub")
    out = run("epub_tool.py", "check", "broken.epub", "--format", "json").stdout
    msgs = " ".join(p["message"] for p in json.loads(out)["problems"])
    for frag in ("first file must be 'mimetype'", "missing from the archive", "not in the manifest", "dcterms:modified", "link to missing", "image to missing", "junk file"):
        check(frag in msgs, f"epub check: {frag}", msgs)
    out = ok(run("epub_tool.py", "repack", "broken.epub", "fixed.epub"), "repack")
    with zipfile.ZipFile(WORK / "fixed.epub") as z:
        first = z.infolist()[0]
        check(first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED and ".DS_Store" not in z.namelist(), "mimetype first and stored, junk gone")
    check("still wrong" in out, "repack reports what it cannot fix", out)
    ok(run("epub_tool.py", "convert", "book.epub", "book.md"), "epub → md")
    check("THIRDCHAPTER" in (WORK / "book.md").read_text(), "converted text")


def test_notebook() -> None:
    before = sha(WORK / "nb.ipynb")
    out = ok(run("nb_tool.py", "outline", "nb.ipynb"), "nb outline")
    check("6 cells (4 code, 2 markdown)" in out and "1 error(s)" in out and "2 image output(s)" in out, "outline stats", out[:400])
    out = ok(run("nb_tool.py", "read", "nb.ipynb", "--cells", "2-3"), "nb read")
    check("hello nb" in out and "png 320×200" in out and "Analysis" not in out, "cells and image placeholder", out)
    out = ok(run("nb_tool.py", "images", "nb.ipynb", "--out-dir", "plots", "--sheet"), "nb images")
    files = sorted((WORK / "plots").glob("cell-*.png"))
    check(len(files) == 2 and "view_image" in out, "png and svg outputs saved", out)
    if len(files) == 2:
        check(png_size(files[0]) == (320, 200) and png_size(files[1])[0] > 100, "image sizes", f"{png_size(files[0])} {png_size(files[1])}")
    out = ok(run("nb_tool.py", "errors", "nb.ipynb"), "nb errors")
    check("cell 5" in out and "ZeroDivisionError" in out and "1/0" in out and "\x1b" not in out, "error with source, ANSI stripped", out)
    ok(run("nb_tool.py", "convert", "nb.ipynb", "nb.md"), "nb → md")
    md = (WORK / "nb.md").read_text()
    check("```python" in md and "![](nb_files/cell-003-1.png)" in md and (WORK / "nb_files" / "cell-003-1.png").exists(), "markdown with image files", md)
    ok(run("nb_tool.py", "convert", "nb.ipynb", "nb.py"), "nb → py")
    py = (WORK / "nb.py").read_text()
    check(py.count("# %%") == 6 and "# # Analysis" in py, "percent format", py)
    compile(py, "nb.py", "exec")
    CHECKS_INC()
    ok(run("nb_tool.py", "convert", "nb.ipynb", "nb.html"), "nb → html")
    check("data:image/png;base64" in (WORK / "nb.html").read_text(), "html embeds plots")
    out = ok(run("nb_tool.py", "convert", "nb.ipynb", "nb.pdf", "--title", "Analysis report"), "nb → pdf")
    check("pages" in out, "pdf written", out)
    ok(run("nb_tool.py", "strip", "nb.ipynb", "-o", "clean.ipynb"), "strip")
    clean = json.loads((WORK / "clean.ipynb").read_text())
    check(all(not c.get("outputs") and c.get("execution_count") is None for c in clean["cells"] if c["cell_type"] == "code") and "widgets" not in clean["metadata"], "outputs, counts, metadata removed")
    check(sha(WORK / "nb.ipynb") == before, "notebook unchanged")
    ok(run("nb_tool.py", "merge", "nb.ipynb", "clean.ipynb", "-o", "merged.ipynb", "--headings"), "merge")
    check(len(json.loads((WORK / "merged.ipynb").read_text())["cells"]) == 14, "merged cell count")
    ok(run("nb_tool.py", "create", "nb.py", "-o", "fromscript.ipynb"), "create from script")
    made = json.loads((WORK / "fromscript.ipynb").read_text())
    check(len(made["cells"]) == 6 and made["cells"][0]["cell_type"] == "markdown", "cells from # %% markers", str(len(made["cells"])))
    out = ok(run("nb_tool.py", "find", "nb.ipynb", "division", "--outputs"), "find")
    check("cell 5" in out, "find in outputs", out)


def test_inputs() -> None:
    """Remote images offline, AsciiDoc directives, HTML that Typst would refuse, iframes, --main."""
    (WORK / "remote.md").write_text("# Remote\n\n![far away](https://example.invalid/pic.png)\n\nREMOTEWORD\n", encoding="utf-8")
    out = ok(run("mk_convert.py", "remote.md", "remote.pdf", "--offline"), "remote image with --offline")
    check("not downloaded (--offline)" in out and "https://example.invalid/pic.png" in out, "offline: remote image kept as a link", out)
    _, text, _ = pdf_facts(WORK / "remote.pdf")
    check("REMOTEWORD" in text and "far away" in text, "offline pdf keeps the text and the image's description", text[:200])
    (WORK / "inc.adoc").write_text("// tag::part[]\nINCLUDEDWORD here.\n// end::part[]\n\nNOTTAGGED\n", encoding="utf-8")
    (WORK / "cond.adoc").write_text("= Doc\n:flag:\n:product: Tidekit\n\n== Section\n\nifdef::flag[]\nFLAGWORD for {product}.\nendif::[]\n\nifndef::flag[]\nHIDDENWORD\nendif::[]\n\ninclude::inc.adoc[tag=part]\n", encoding="utf-8")
    r = run("mk_convert.py", "cond.adoc", "-o", "-", "--to", "md")
    check(r.returncode == 0 and "FLAGWORD for Tidekit" in r.stdout and "HIDDENWORD" not in r.stdout and "INCLUDEDWORD" in r.stdout and "NOTTAGGED" not in r.stdout, "AsciiDoc ifdef, attributes and tagged include", r.stderr[-300:] + r.stdout)
    html = '<html><body><h1 id="top">Top<br>line</h1><p><a href="#nowhere">dangling</a> <a href="">empty</a> <span id="a1"></span><span id="a2">anchored</span> <a href="#a2">to a2</a></p><h2 id="top">Dup</h2><iframe src="https://example.invalid/embed" title="Embedded map"></iframe><p>HTMLWORD</p></body></html>'
    (WORK / "links.html").write_text(html, encoding="utf-8")
    out = ok(run("mk_convert.py", "links.html", "links.pdf"), "html with dangling, empty and duplicate anchors → pdf")
    check("missing anchor #nowhere" in out, "dangling link reported", out)
    _, text, _ = pdf_facts(WORK / "links.pdf")
    check("HTMLWORD" in text and "dangling" in text and "anchored" in text and "Top line" in text, "all text typeset", text[:300])
    r = run("mk_convert.py", "links.html", "-o", "-", "--to", "md")
    check("[Embedded map](https://example.invalid/embed)" in r.stdout, "an iframe becomes a link (never fetched)", r.stdout)
    ok(run("mk_convert.py", "page.html", "main.pdf", "--main"), "--main → pdf")
    _, text, _ = pdf_facts(WORK / "main.pdf")
    check("How Tides Work" in text and "Bay of Fundy" in text and "COOKIETEXT" not in text and "SIDEBARLINK" not in text, "--main keeps the article only", text[:300])
    (WORK / "refh.md").write_text("# Guide\n\n## Setup\n\nSee [the setup][Setup] and [missing][nothing].\n", encoding="utf-8")
    d = json.loads(run("md_check.py", "refh.md", "--format", "json").stdout)
    rules = [x["rule"] for x in d["files"][0]["problems"]]
    check("ref-heading" in rules and rules.count("ref-undefined") == 1, "implicit heading references are not errors", str(rules))


def test_long() -> None:
    """Long documents: converted in parts (links between parts kept), excerpts by section, --section conversions."""
    rnd = random.Random(9)
    words = "tide moon harbor current shoal estuary delta reef swell basin".split()
    body = ["# Atlas", "", "See [the last part](#part-6), [the second notes](#notes-1) and a note.[^n]", "", "[^n]: A footnote in part one."]
    for s in range(1, 7):
        body += [f"## Part {s}", "", f"PARTWORD{s} text.", ""]
        body += [" ".join(rnd.choice(words) for _ in range(120)) + "." for _ in range(40)]
        if s in (2, 5):
            body += ["", "### Notes", "", f"NOTESWORD{s}", ""]
    (WORK / "atlas.md").write_text("\n\n".join(body) + "\n", encoding="utf-8")
    small = {"DESK_MK_PARTS_FROM": "20000", "DESK_MK_PART_CHARS": "40000"}
    out = ok(run("mk_convert.py", "atlas.md", "atlas.pdf", "--typ-out", "atlas-src/atlas.typ", env=small), "long document in parts → pdf")
    check("converted in" in out and "parts" in out and "missing anchor" not in out, "parts mode, every link resolved", out)
    typ = (WORK / "atlas-src" / "atlas.typ").read_text(encoding="utf-8")
    check("desk-xref" not in typ and re.search(r"#link\(<p\d+-part-6>\)", typ) is not None and re.search(r"#link\(<p5-notes>\)", typ) is not None, "links between parts point at the right labels", re.findall(r"#link\(<[^>]*>\)", typ)[:6].__repr__())
    n, text, toc = pdf_facts(WORK / "atlas.pdf")
    check("PARTWORD1" in text and "PARTWORD6" in text and "A footnote in part one" in text and len([t for t in toc if t.startswith("Part")]) == 6, "one document with every part", str(toc))
    for name, word in (("nb.ipynb", "hello nb"), ("book.epub", "THIRDCHAPTER"), ("page.html", "Bay of Fundy")):
        out = ok(run("mk_convert.py", name, f"parts-{Path(name).stem}.pdf", env={"DESK_MK_PARTS_FROM": "100"}), f"{name} through the parts path")
        _, text, _ = pdf_facts(WORK / f"parts-{Path(name).stem}.pdf")
        check("converted in" in out and word in text, f"{name} typeset from its Markdown", out[-200:] + text[:100])
    # excerpts of a long document (over 600 KB of text): the beginning by default, a section alone on request
    paras = ["# Chronicle", ""]
    for s in range(1, 81):
        paras += [f"## Year {s}", ""] + [" ".join(rnd.choice(words) for _ in range(100)) + f" YEARWORD{s}." for _ in range(14)] + [""]
    (WORK / "chronicle.md").write_text("\n\n".join(paras), encoding="utf-8")
    d = json.loads(ok(run("mk_render.py", "chronicle.md", "--out", "chron", "--format", "json"), "render a long document"))
    check("beginning" in d.get("excerpt", "") and d["pages"] < 80 and len(d["rendered"]) == 8, "the beginning, typeset alone", str({k: d.get(k) for k in ("excerpt", "pages")}))
    d = json.loads(ok(run("mk_render.py", "chronicle.md", "--section", "1.50", "--out", "chron50", "--pdf", "y50.pdf", "--format", "json"), "render one section of a long document"))
    _, text, _ = pdf_facts(WORK / "y50.pdf")
    check("1.50" in d.get("excerpt", "") and "YEARWORD50." in text and "YEARWORD51." not in text and "YEARWORD49." not in text, "exactly that section", d.get("excerpt", ""))
    d = json.loads(ok(run("mk_render.py", "chronicle.md", "--find", "YEARWORD77.", "--out", "chron77", "--format", "json"), "render where a phrase is (long)"))
    check("1.77" in d.get("excerpt", "") and d["page_numbers"], "--find typesets the section holding the phrase", str(d.get("excerpt")))
    out = ok(run("mk_convert.py", "doc.md", "data.md", "--section", "Data"), "convert one section")
    data = (WORK / "data.md").read_text(encoding="utf-8")
    check("| Apple" in data and "FINALWORD" not in data and "title: Field Notes" in data, "section with the document's metadata", data[:300])
    ok(run("mk_convert.py", "book.epub", "two.md", "--section", "Chapter Two"), "convert one EPUB TOC entry")
    two = (WORK / "two.md").read_text(encoding="utf-8")
    check("SECONDCHAPTER" in two and "THIRDCHAPTER" not in two and "<!--" not in two, "that entry only", two[:300])


def test_regressions() -> None:
    """One check per defect found in acceptance testing (see the skill's fix list)."""
    # html_extract: a lead section before the first heading (section-per-heading pages such as Wikipedia).
    (WORK / "wiki.html").write_text(WIKI_HTML, encoding="utf-8")
    out = ok(run("html_extract.py", "wiki.html", "--no-cache"), "extract a section-per-heading page")
    check("LEADWORD" in out and "## History" in out and "Reference 7" not in out, "lead section kept, reference list dropped", out[:400])
    check("15 January 2003 (updated 20 September 2026)" in out and "T02:34" not in out, "readable dates with the update", out[:200])
    out = ok(run("html_extract.py", "page.html"), "extract (byline)")
    check(out.count("Mira Sol") == 1, "the byline paragraph is not repeated under the title", out[:400])
    # Remote images: a link around a picture is not a picture (no false 'not downloaded' warning for the link).
    (WORK / "linked.md").write_text("# L\n\n[![pic](https://example.invalid/t.png)](https://example.invalid/wiki/File:T.svg)\n", encoding="utf-8")
    out = ok(run("mk_convert.py", "linked.md", "linked.pdf", "--offline"), "linked picture offline")
    check("1 remote image(s) not downloaded" in out, "only the picture counts as a remote image", out)
    # HTML <title>: the front matter's title, else the first heading.
    ok(run("mk_convert.py", "doc.md", "titled.html"), "html with front-matter title")
    check("<title>Field Notes</title>" in (WORK / "titled.html").read_text(encoding="utf-8"), "front-matter title is the page title")
    (WORK / "untitled.md").write_text("# First Heading\n\nText.\n", encoding="utf-8")
    ok(run("mk_convert.py", "untitled.md", "untitled.html"), "html without title")
    check("<title>First Heading</title>" in (WORK / "untitled.html").read_text(encoding="utf-8"), "first heading is the page title")
    # EPUB parts: a TOC entry whose sub-entries are later documents covers all of them.
    parts_epub(WORK / "saga.epub")
    out = ok(run("mk_read.py", "saga.epub", "--section", "t1", "--max-chars", "0"), "read a part")
    check(all(f"CHWORD{n}." in out for n in (1, 2, 3)) and "CHWORD4." not in out and "(ch 1-3)" in out, "a part holds its chapters", out[:200])
    out = ok(run("epub_tool.py", "toc", "saga.epub"), "toc of parts")
    check("t1 Part 1 · ch 1-3" in out and "t2 Chapter 1 · ch 1 ·" in out, "part spans chapters in the toc", out[:400])
    ok(run("mk_convert.py", "saga.epub", "part2.md", "--section", "Part 2"), "convert a part")
    p2 = (WORK / "part2.md").read_text(encoding="utf-8")
    check(all(f"CHWORD{n}." in p2 for n in (4, 5, 6)) and "CHWORD3." not in p2, "converted part has its chapters", p2[:200])
    parts_epub(WORK / "saga3.epub", parts=3, chapters=4)
    out = ok(run("mk_read.py", "saga3.epub", "--max-chars", "1400"), "epub map under a small budget")
    check("Listing" in out and "Part 3" in out and "cut at character" not in out, "the map fits the budget and still covers the whole book", out[-700:])
    # Zip members that lie about their size are refused before they are inflated.
    liar_epub(WORK / "liar.epub")
    for script, args in (("mk_read.py", ["liar.epub"]), ("mk_convert.py", ["liar.epub", "liar.md"])):
        r = run(script, *args)
        check(r.returncode == 1 and "lies about its size" in r.stderr, f"{script}: lying member refused", r.stderr[-300:])
    out = run("epub_tool.py", "check", "liar.epub").stdout
    check("lies about its size" in out, "check reports the lying member", out[:400])
    # Backslash paths escape on Windows: check flags them and repack drops them.
    zip_epub(WORK / "trav.epub", {"c.xhtml": "<h1>T</h1><p>x</p>"}, '<li><a href="c.xhtml">T</a></li>', {"OEBPS/..\\..\\evil.txt": b"x"})
    out = run("epub_tool.py", "check", "trav.epub").stdout
    check("unsafe path" in out and "evil.txt" in out, "backslash traversal flagged", out[:400])
    ok(run("epub_tool.py", "repack", "trav.epub", "trav-fixed.epub"), "repack traversal")
    with zipfile.ZipFile(WORK / "trav-fixed.epub") as z:
        check(not any("evil" in n for n in z.namelist()), "repack drops the escaping member")
    # An internal DTD subset leaves no stray ']>' line.
    dtd = '<?xml version="1.0"?><!DOCTYPE html [<!ENTITY e "x">]><html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body><h1>Laughs</h1><p>DTDWORD</p></body></html>'
    zip_epub(WORK / "dtd.epub", {"c.xhtml": dtd}, '<li><a href="c.xhtml">L</a></li>')
    out = ok(run("mk_read.py", "dtd.epub"), "epub with a DTD subset")
    check("DTDWORD" in out and "]>" not in out, "no stray ]>", out)
    # AsciiDoc attribute bombs and include loops fail fast with a message.
    (WORK / "loop.adoc").write_text("= Loop\n\nHello\n\ninclude::loop.adoc[]\n\n:x: {x}{x}{x}{x}{x}{x}{x}{x}{x}{x}\n\n{x}\n", encoding="utf-8")
    t = time.time()
    r = run("mk_read.py", "loop.adoc")
    check(r.returncode == 1 and "attribute bomb" in r.stderr and time.time() - t < 20, "AsciiDoc attribute bomb refused", r.stderr[-300:])
    # Pathological and binary Markdown.
    (WORK / "brackets.md").write_text("# B\n\n" + "[" * 40 + "x" + "]" * 40 + "\n\nBRACKETWORD\n", encoding="utf-8")
    t = time.time()
    out = ok(run("mk_convert.py", "brackets.md", "brackets.html"), "deeply nested brackets")
    check(time.time() - t < 30 and "escaped" in out, "nested brackets escaped, conversion fast", out)
    (WORK / "noise.md").write_bytes(random.Random(1).randbytes(20000))
    r = run("mk_convert.py", "noise.md", "noise.html")
    check(r.returncode == 1 and "binary data" in r.stderr, "binary 'Markdown' refused", r.stderr[-200:])
    r = run("md_check.py", "noise.md")
    check(r.returncode == 1 and "binary data" in r.stderr, "md_check refuses binary", r.stderr[-200:])
    # HTML nested past the parser's depth limit: an error, not an empty result.
    (WORK / "deep.html").write_text("<html><body>" + "<div>" * 3000 + "<p>deep</p>" + "</div>" * 3000 + "</body></html>", encoding="utf-8")
    r = run("html_extract.py", "deep.html")
    check(r.returncode == 1 and "2,048 deep" in r.stderr, "deep HTML reported", r.stderr[-200:])
    # Notebooks: one damaged image does not stop the rest; a malformed notebook is named as such.
    nb = {"cells": [{"cell_type": "code", "source": "x", "metadata": {}, "execution_count": 1, "outputs": [{"output_type": "display_data", "metadata": {}, "data": {"image/png": "!!!notbase64!!!"}}, {"output_type": "display_data", "metadata": {}, "data": {"image/svg+xml": NOTEBOOK_SVG}}]}], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (WORK / "badimg.ipynb").write_text(json.dumps(nb), encoding="utf-8")
    out = ok(run("nb_tool.py", "images", "badimg.ipynb", "--out-dir", "badimg"), "images with a damaged one")
    check("1 image(s)" in out and "not valid base64" in out and (WORK / "badimg" / "cell-001-2.png").exists(), "good image saved, bad one reported", out)
    out = ok(run("nb_tool.py", "convert", "badimg.ipynb", "badimg.pdf"), "convert with a damaged image")
    check("left out" in out, "damaged image left out of the PDF", out)
    (WORK / "weird.ipynb").write_text('{"cells": "notalist", "nbformat": 4}', encoding="utf-8")
    r = run("nb_tool.py", "outline", "weird.ipynb")
    check(r.returncode == 1 and "not a valid notebook" in r.stderr, "malformed notebook named", r.stderr)
    # md_check: a TOC goes after a README's HTML title block; near-miss anchors get a suggestion.
    (WORK / "readme.md").write_text('<h1 align="center">\n  <img alt="Logo" src="logo.png">\n</h1>\n\n<p align="center">\n  <a href="https://x.example"><img src="badge.svg"></a>\n</p>\n\nIntro text.\n\n## Install\n\nRun it.\n\n## Running the server\n\nSee [run](#run-the-server).\n', encoding="utf-8")
    ok(run("md_check.py", "readme.md", "--fix", "readme.fixed.md", "--add-toc"), "add a toc")
    fixed = (WORK / "readme.fixed.md").read_text(encoding="utf-8")
    check(fixed.index("</p>") < fixed.index("<!-- toc -->") < fixed.index("Intro text"), "toc after the title block", fixed[:300])
    out = run("md_check.py", "readme.md").stdout
    check("did you mean #running-the-server?" in out, "near-miss anchor suggestion", out)
    # Clean Markdown from an EPUB and from self-contained HTML.
    md = (WORK / "book.md").read_text(encoding="utf-8")
    check("<div" not in md and "<span" not in md and "<svg" not in md, "EPUB → Markdown without scaffolding", md[:300])
    png64 = base64.b64encode((WORK / "chart.png").read_bytes()).decode()
    (WORK / "inline.html").write_text(f'<html><body><h1>Inline</h1><p><img src="data:image/png;base64,{png64}" alt="chart" style="width:50%"></p></body></html>', encoding="utf-8")
    out = ok(run("mk_convert.py", "inline.html", "inline.md"), "self-contained HTML → md")
    md = (WORK / "inline.md").read_text(encoding="utf-8")
    check("data:image" not in md and "![chart](inline_media/" in md and any((WORK / "inline_media").iterdir()), "embedded image saved as a file", md)
    # Quarto-style cross-references.
    (WORK / "xref.md").write_text("# Intro {#sec-intro}\n\nSee @sec-data, @fig-chart and [@tbl-nums].\n\n# Data {#sec-data}\n\n![A chart](chart.png){#fig-chart}\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n: Numbers {#tbl-nums}\n", encoding="utf-8")
    out = ok(run("mk_convert.py", "xref.md", "xref.pdf", "-N"), "cross-references to PDF")
    _, text, _ = pdf_facts(WORK / "xref.pdf")
    flat = " ".join(text.split())
    check("Section 2" in flat and "Figure 1" in flat and "Table 1" in flat and "sec-data" not in flat and "unresolved" not in out, "numbered references", flat[:200])
    ok(run("mk_convert.py", "xref.md", "xref.html"), "cross-references to HTML")
    html = (WORK / "xref.html").read_text(encoding="utf-8")
    check('href="#fig-chart">Figure' in html and 'href="#sec-data">Data</a>' in html, "linked references in HTML", re.findall(r"See .{0,300}", html)[:1].__repr__())
    # Sphinx reST: roles and API signatures survive.
    (WORK / "api.rst").write_text("API\n===\n\nCall :func:`~pkg.mod.spam` or :class:`!Eggs`.\n\n.. function:: spam(eggs, *, ham=None)\n\n   Cook SPAMWORD.\n\n   .. versionadded:: 3.2\n", encoding="utf-8")
    r = run("mk_convert.py", "api.rst", "-o", "-", "--to", "md")
    check(r.returncode == 0 and "`spam()`" in r.stdout and "`Eggs`" in r.stdout and "`spam(eggs, *, ham=None)`" in r.stdout and "New in version 3.2" in r.stdout, "Sphinx roles and signatures", r.stdout + r.stderr[-200:])
    # Pictures without a size are shown at a screen-like resolution (not Typst's 72 dpi) and centred when alone.
    (WORK / "pics.md").write_text("# Pics\n\n[![thumb](chart.png)](https://example.invalid/wiki/File:Chart.png)\n\n![A chart](chart.png)\n\n![Wide](chart.png){width=90%}\n", encoding="utf-8")
    ok(run("mk_convert.py", "pics.md", "pics.pdf", "--typ-out", "pics-src/pics.typ"), "pictures without a size")
    typ = (WORK / "pics-src" / "pics.typ").read_text(encoding="utf-8")
    check(len(re.findall(r'image\("[^"]+", width: 45(?:\.\d)?%\)', typ)) == 2 and re.search(r"width: 90(?:\.0)?%", typ) is not None and "#align(center)[\n#link(" in typ, "natural picture size, explicit sizes kept, lone link picture centred", "\n".join(re.findall(r"image\([^)]*\)", typ)))
    # Nested emphasis in HTML does not double the Markdown markers.
    (WORK / "nest.html").write_text("<html><body><figure><img src='a.png' alt='a'><figcaption>From the <i>Cyclopaedia</i></figcaption></figure><p><i>a <i>b</i> c</i></p></body></html>", encoding="utf-8")
    out = ok(run("html_extract.py", "nest.html", "--all"), "nested emphasis")
    check("*From the Cyclopaedia*" in out and "*a b c*" in out and "**" not in out, "no doubled emphasis markers", out)
    # Book and report running heads and page numbers.
    rnd = random.Random(2)
    words = "harbor lantern ledger copper meadow signal archive river".split()
    chap = lambda n: f"## Chapter {n}\n\n" + "\n\n".join(" ".join(rnd.choice(words) for _ in range(90)) + "." for _ in range(9)) + "\n\n"
    (WORK / "novel.md").write_text("---\ntitle: Novel\nauthor: A. Writer\n---\n\n" + chap(1) + chap(2), encoding="utf-8")
    ok(run("mk_convert.py", "novel.md", "novel.pdf", "--template", "book"), "book whose chapters are level 2")
    heads, feet = page_lines(WORK / "novel.pdf"), page_lines(WORK / "novel.pdf", top=False)
    first = next((i for i, f in enumerate(feet) if f == "1"), None)
    nums = []
    for h, f in zip(heads[first:] if first is not None else [], feet[first:] if first is not None else []):
        m = re.match(r"(\d+)\b", h) or re.search(r"\b(\d+)$", h) or re.fullmatch(r"(\d+)", f)
        nums.append(int(m.group(1)) if m else None)
    check(first is not None and first % 2 == 0 and nums == list(range(1, len(nums) + 1)), "page 1 is a right-hand page and numbers run 1, 2, 3 …", f"{heads} {feet}")
    check(any(h.startswith("Chapter 1") or h.startswith("Chapter 2") for h in heads) or any(re.match(r"Chapter \d+ \d+$", h) for h in heads), "odd pages name the chapter", str(heads))
    (WORK / "rep.md").write_text("---\ntitle: Rep\n---\n\n" + "\n\n".join(" ".join(rnd.choice(words) for _ in range(90)) + "." for _ in range(14)) + "\n\n# One\n\nText.\n", encoding="utf-8")
    ok(run("mk_convert.py", "rep.md", "rep.pdf", "--template", "report", "--toc"), "report with text before the first chapter")
    heads = page_lines(WORK / "rep.pdf")
    check(not any("Contents" in h for h in heads[2:]), "running heads never say Contents on body pages", str(heads))


def test_jail() -> None:
    """A stranger's document never pulls files from outside its folder into what Desk reads or writes: includes
    (AsciiDoc, reStructuredText, Org, LaTeX), pictures (Markdown, HTML, file: URIs, symlinks) and :file: options are
    left out, while includes and pictures inside the folder still work. Also: the read cache follows included files,
    and a batch never writes two inputs to one output."""
    doc, far = WORK / "jail", WORK / "jail-secret"
    (doc / "sub").mkdir(parents=True)
    far.mkdir()
    marker = "SECRETMARK-7Q"
    secret = far / "secret.txt"
    secret.write_text(marker + "\n", encoding="utf-8")
    (far / "secret.tex").write_text(marker + "\n", encoding="utf-8")
    make_png(far / "outside.png", 40, 30, (200, 0, 0))
    make_png(doc / "pic.png", 40, 30, (0, 0, 200))
    s_abs, s_tex = str(secret), secret.with_suffix("").as_posix()
    link = ""
    try:
        (doc / "link.png").symlink_to(far / "outside.png")
        link = "\n\n![l](link.png)"
    except OSError:
        pass  # no symlinks here (Windows without the privilege)
    files = {
        "a.adoc": f"= A\n\ninclude::{s_abs}[]\n\ninclude::../jail-secret/secret.txt[]\n\ninclude::/etc/hosts[]\n\ninclude::part.adoc[]\n",
        "part.adoc": "ADOCPART\n",
        "c.rst": f"Title\n=====\n\n.. include:: {s_abs}\n\n.. include:: /etc/hosts\n\n.. include:: part.rst\n\n.. raw:: html\n   :file: ../jail-secret/secret.txt\n\n.. csv-table:: T\n   :file: ../jail-secret/secret.txt\n",
        "part.rst": "RSTPART\n\n.. include:: ../jail-secret/secret.txt\n",
        "o.org": f'* H\n#+INCLUDE: "{s_abs}"\n#+INCLUDE: "/etc/hosts"\n#+INCLUDE: "sub/p.org"\n',
        "sub/p.org": 'ORGPART\n#+INCLUDE: "../../jail-secret/secret.txt"\n',
        "l.tex": "\\documentclass{article}\n\\begin{document}\n\\include{" + s_tex + "}\n\\include{/etc/hosts}\n\\input{sub/ch}\n\\def\\p{../jail-secret/secret}\\input{\\p}\n\\end{document}\n",
        "sub/ch.tex": "TEXPART\n",
        "m.md": f"# M\n\nMDPART\n\n![x]({s_abs})\n\n![h](/etc/hosts)\n\n![y](../jail-secret/outside.png)\n\n![ok](pic.png){link}\n\n<img src=\"file://{secret.as_posix()}\"> <img src=\"../jail-secret/outside.png\">\n\n<div style=\"background:url(../jail-secret/secret.txt)\">bg</div>\n",
        "h.html": f'<html><body><p>HTMLPART</p><img src="file://{secret.as_posix()}"><img src="file:///etc/hosts"><img src="../jail-secret/outside.png"><img src="pic.png"></body></html>',
    }
    for name, text in files.items():
        (doc / name).write_text(text, encoding="utf-8")
    far_png = (far / "outside.png").read_bytes()
    bad = [marker.encode(), base64.b64encode((marker + "\n").encode()), base64.b64encode(far_png)[:60], far_png[:200]]

    def leaks(path: Path) -> bool:
        data = path.read_bytes()
        if path.suffix in (".docx", ".epub"):
            with zipfile.ZipFile(path) as z:
                data = b"".join(z.read(n) for n in z.namelist())
        return any(x in data for x in bad)

    for name, part in (("a.adoc", "ADOCPART"), ("c.rst", "RSTPART"), ("o.org", "ORGPART"), ("l.tex", "TEXPART"), ("m.md", "MDPART"), ("h.html", "HTMLPART")):
        r = run("mk_read.py", f"jail/{name}")
        check(r.returncode == 0 and marker not in r.stdout and marker not in r.stderr and part in r.stdout, f"read {name}: nothing from outside its folder, its own include kept", r.stderr[-300:] + r.stdout[:300])
        for ext in ("html", "docx"):
            dest = WORK / f"jail-{Path(name).stem}-{Path(name).suffix[1:]}.{ext}"
            r = run("mk_convert.py", f"jail/{name}", str(dest))
            check(r.returncode == 0 and not leaks(dest) and marker not in r.stdout, f"convert {name} to {ext}: nothing from outside its folder", r.stderr[-300:] + r.stdout[-300:])
            if name in ("c.rst", "l.tex", "a.adoc") and ext == "html":
                check(part in dest.read_text(encoding="utf-8"), f"convert {name}: the include inside the folder is kept")
    out = ok(run("mk_convert.py", "jail/m.md", "jail-m.pdf"), "markdown with outside pictures to pdf")
    check("left out (outside the document's folder)" in out and "left out (a URL pandoc must not open)" in out, "refused pictures are reported", out)
    check(not leaks(WORK / "jail-m.pdf"), "pdf has nothing from outside the folder")
    html = (WORK / "jail-m-md.html").read_text(encoding="utf-8")
    check(html.count("data:image/png;base64,") == 1, "the picture inside the folder is still embedded (and only it)", str(html.count("data:image/png")))
    with zipfile.ZipFile(WORK / "jail-m-md.docx") as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    check(len(media) == 1, "docx embeds only the picture inside the folder", str(media))
    r = run("nb_tool.py", "create", "jail/m.md", "-o", str(WORK / "jail-m.ipynb"))
    check(r.returncode == 0 and not leaks(WORK / "jail-m.ipynb"), "notebook from markdown: nothing from outside the folder", r.stderr[-300:])

    # mk_read's cache (documents of 64 KB and more) follows included files and the document's folder.
    for d, word in (("book1", "FIRSTVERSION"), ("book2", "OTHERFOLDER")):
        (WORK / d).mkdir()
        (WORK / d / "book.adoc").write_text("= Book\n\n" + "".join(f"== Part {k}\n\n" + "filler text " * 60 + "\n\n" for k in range(100)) + "include::chap.adoc[]\n", encoding="utf-8")
        (WORK / d / "chap.adoc").write_text(f"== Chapter\n\n{word} here.\n", encoding="utf-8")
    first = run("mk_read.py", "book1/book.adoc", "--find", "here")
    (WORK / "book1" / "chap.adoc").write_text("== Chapter\n\nSECONDVERSION here.\n", encoding="utf-8")
    again = run("mk_read.py", "book1/book.adoc", "--find", "here")
    other = run("mk_read.py", "book2/book.adoc", "--find", "here")
    check("FIRSTVERSION" in first.stdout and "SECONDVERSION" in again.stdout and "FIRSTVERSION" not in again.stdout, "an edited include shows on the next (cached) read", first.stdout[-200:] + again.stdout[-200:])
    check("OTHERFOLDER" in other.stdout, "the same document in another folder reads its own includes", other.stdout[-200:])

    # Batch: two inputs with one name (case aside) get two outputs.
    for d, name, word in (("ba", "notes.md", "ALPHAONE"), ("bb", "Notes.md", "BETATWO")):
        (WORK / d).mkdir()
        (WORK / d / name).write_text(f"# N\n\n{word}\n", encoding="utf-8")
    ok(run("mk_convert.py", "ba/notes.md", "bb/Notes.md", "--to", "html", "--out-dir", "same"), "batch with one name twice")
    outs = sorted((WORK / "same").glob("*.html"))
    texts = " ".join(p.read_text(encoding="utf-8") for p in outs)
    check(len(outs) == 2 and "ALPHAONE" in texts and "BETATWO" in texts and any(p.stem.endswith("-2") for p in outs), "same-name inputs get name and name-2", str([p.name for p in outs]))


def CHECKS_INC() -> None:
    check(True, "percent-format script compiles")


TIMES: dict[str, tuple[float, float]] = {}


def main() -> int:
    global WORK, ENV
    if {"-h", "--help"} & set(sys.argv[1:]):
        print((__doc__ or "").strip() + "\n\nUsage: python3 scripts/selftest.py [test_name …]  (default: every test)")
        return 0
    with tempfile.TemporaryDirectory(prefix="desk-mk-selftest-") as tmp:
        WORK = Path(tmp) / "w"
        WORK.mkdir()
        ENV = {k: v for k, v in os.environ.items()}
        ENV["DESK_FILE_CACHE"] = str(Path(tmp) / "cache")
        ENV.pop("DESK_NO_CACHE", None)
        ENV["PYTHONDONTWRITEBYTECODE"] = "1"
        (WORK / "doc.md").write_text(DOC_MD, encoding="utf-8")
        (WORK / "refs.bib").write_text(BIB, encoding="utf-8")
        make_png(WORK / "chart.png")
        (WORK / "page.html").write_text(PAGE_HTML, encoding="utf-8")
        make_notebook(WORK / "nb.ipynb", (WORK / "chart.png").read_bytes())
        big_html(WORK / "big.html", 800)
        inputs = {p.name: sha(p) for p in WORK.iterdir() if p.is_file()}
        tests = [test_help, test_convert, test_cache, test_md_check, test_epub, test_render, test_read, test_extract, test_notebook, test_inputs, test_long, test_regressions, test_jail]
        only = set(sys.argv[1:])
        for t in tests:
            if only and t.__name__ not in only:
                continue
            t0 = time.time()
            try:
                t()
            except Exception as e:  # noqa: BLE001 — a crash is a failure of that test, keep going
                check(False, f"{t.__name__} crashed", f"{type(e).__name__}: {e}")
            print(f"  {t.__name__}: {time.time() - t0:.1f}s", file=sys.stderr)
        for name, h in inputs.items():
            check(sha(WORK / name) == h, f"input {name} unchanged")
    for k, (a, b) in TIMES.items():
        print(f"  {k}: {a:.2f}s / {b:.2f}s", file=sys.stderr)
    dt = time.time() - T0
    if FAILS:
        print(f"FAILED: {len(FAILS)} of {CHECKS} checks in {dt:.1f}s")
        for f in FAILS:
            print(f" - {f}")
        return 1
    print(f"ok: {CHECKS} checks in {dt:.1f}s")
    return 0


WORK = Path(".")
ENV: dict[str, str] = {}

if __name__ == "__main__":
    sys.exit(main())
