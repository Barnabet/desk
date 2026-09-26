#!/usr/bin/env python3
"""Extract text from a PDF: fast plain text with page markers (pypdfium2), layout-preserving text, words with
bounding boxes, tables (Markdown, CSV or JSON), or a regex search that reports page, box and context.

Long PDFs (over 100 pages, or more text than --max-chars) get a map first: the outline sections (or blocks of
pages) with page ranges and sizes. Output stops at --max-chars on a page boundary and ends with the exact command
that reads on. Text, words and tables are cached by file content, so the second read of a big file is fast.

--search reads the text as a reader does: a space matches any whitespace (line breaks too), a word hyphenated at a
line end matches whole, and a match may run over a page break (running headers, footers and page numbers are
skipped). Pages without a text layer (scans) are named, since nothing on them can be found.

Boxes are [x0, top, x1, bottom] in points from the top-left of the page as displayed, so they line up with
pdf_render.py images (pixel = point × dpi / 72) and with --region / --box options of the other scripts.

Examples:
  python3 scripts/pdf_text.py paper.pdf                           # text with "--- page N ---" markers (a map when long)
  python3 scripts/pdf_text.py book.pdf --map                      # sections, page ranges, sizes, pages without text
  python3 scripts/pdf_text.py book.pdf --pages 40-60              # a range
  python3 scripts/pdf_text.py paper.pdf --layout --pages 3        # keep columns and alignment
  python3 scripts/pdf_text.py invoice.pdf --tables                # tables (Markdown; CSV when long)
  python3 scripts/pdf_text.py invoice.pdf --tables --out tables/  # one CSV file per table
  python3 scripts/pdf_text.py contract.pdf --search "termination|notice period" --ignore-case
  python3 scripts/pdf_text.py form.pdf --words --pages 1 --format json            # words with boxes
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, md_table, output_dir, parser, pool_map, run_main
from _pdfkit import cached_value, emit, fmt_num, open_pdfium, parse_pages, pdf_input, pdfium_geometry, pdfium_source, plural

NO_TEXT = "[no text layer on this page: it is probably scanned or a drawing. Render it with pdf_render.py --pages {n} and look at it with view_image]"
WS = {0x20, 0x09, 0x0A, 0x0D, 0xA0, 0x3000, 0x2002, 0x2003, 0x2009}


# ── pdfium text helpers ─────────────────────────────────────────────────


def page_chars(tp: Any) -> str:
    """The page's characters, one string character per pdfium char index (so match offsets map to boxes)."""
    import ctypes

    import pypdfium2.raw as raw

    n = raw.FPDFText_CountChars(tp)
    if n <= 0:
        return ""
    buf = (ctypes.c_ushort * (n + 1))()
    got = raw.FPDFText_GetText(tp, 0, n, buf)
    units = list(buf[: max(0, got - 1)])
    if len(units) == n and not any(0xD800 <= u <= 0xDFFF for u in units):
        return "".join(map(chr, units))
    return "".join(chr(raw.FPDFText_GetUnicode(tp, i) or 0xFFFD) for i in range(n))


SOFT = "\ufffe"  # pdfium's mark for a hyphen at a line end; kept in cached text, shown as "-"


def clean_text(text: str) -> str:
    """pdfium's page text with plain line breaks; a line-end hyphen stays SOFT (see shown()) so search can join it."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x02", SOFT)
    text = text.replace("\x00", "")
    return re.sub(r"[ \t]+\n", "\n", text).strip("\n")


def shown(text: str) -> str:
    """Text as printed for the agent: a line-end hyphen as '-'."""
    return text.replace(SOFT, "-")


def char_boxes(tp: Any, n: int, chars: str | None = None) -> list[tuple[float, float, float, float]]:
    """(left, bottom, right, top) of each char in user space; whitespace gets (0, 0, 0, 0) when `chars` is given."""
    import ctypes

    import pypdfium2.raw as raw

    l, r, b, t = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
    rl, rr, rb, rt = ctypes.byref(l), ctypes.byref(r), ctypes.byref(b), ctypes.byref(t)
    get = raw.FPDFText_GetCharBox
    handle = getattr(tp, "raw", tp)
    out = []
    append = out.append
    zero = (0.0, 0.0, 0.0, 0.0)
    for i in range(n):
        if chars is not None and chars[i].isspace():
            append(zero)
            continue
        get(handle, i, rl, rr, rb, rt)
        append((l.value, b.value, r.value, t.value))
    return out


def range_rects(tp: Any, start: int, count: int) -> list[tuple[float, float, float, float]]:
    """User-space rectangles (one per line segment) covering chars [start, start+count)."""
    import ctypes

    import pypdfium2.raw as raw

    n = raw.FPDFText_CountRects(tp, start, count)
    l, t, r, b = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
    out = []
    for i in range(max(0, n)):
        raw.FPDFText_GetRect(tp, i, ctypes.byref(l), ctypes.byref(t), ctypes.byref(r), ctypes.byref(b))
        out.append((l.value, b.value, r.value, t.value))
    return out


def view_box(geo: Any, rect: tuple[float, float, float, float]) -> list[float]:
    return [fmt_num(v) for v in geo.rect_to_view(*rect)]


# ── per-page workers (run in processes for big documents) ───────────────


def _plain_chunk(job: tuple[str, str | None, list[int]]) -> list[tuple[int, str]]:
    import pypdfium2 as pdfium

    path, password, indices = job
    doc = pdfium.PdfDocument(path, password=password)
    out = []
    try:
        for i in indices:
            try:
                page = doc[i]
            except pdfium.PdfiumError:
                print(f"note: page {i + 1} cannot be loaded (damaged); it is read as empty", file=sys.stderr)
                out.append((i + 1, ""))
                continue
            tp = page.get_textpage()
            text = tp.get_text_range() if tp.count_chars() else ""
            tp.close()
            page.close()
            out.append((i + 1, clean_text(text)))
    finally:
        doc.close()
    return out


@contextmanager
def open_plumber(path: str, password: str | None) -> Iterator[Any]:
    """pdfplumber.open whose close does not first build every page of the file (pdfplumber's own does)."""
    import logging

    import pdfplumber

    logging.getLogger("pdfminer").setLevel(logging.ERROR)  # font-descriptor warnings are noise for the agent

    try:
        pdf = pdfplumber.open(path, password=password or "")
    except Exception:  # noqa: BLE001 — pdfminer cannot open it (unusual encryption, damage): use pdfium's re-saved copy
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw

        doc = pdfium.PdfDocument(path, password=password)
        buf = io.BytesIO()
        try:
            doc.save(buf, flags=raw.FPDF_REMOVE_SECURITY)
        finally:
            doc.close()
        buf.seek(0)
        pdf = pdfplumber.open(buf)
    try:
        yield pdf
    finally:
        for page in pdf.__dict__.get("_pages") or []:
            page.close()
        if not pdf.stream_is_external:
            pdf.stream.close()


def plumber_pages(pdf: Any, indices: list[int]) -> dict[int, Any]:
    """pdfplumber Page objects for just these page indexes.

    pdfplumber's `pdf.pages` builds every page of the file first (about a second per 1000 pages, in each worker);
    this walks the page tree with /Count and builds only the wanted ones, falling back to `pdf.pages` on odd trees.
    Afterwards `pdf.pages` holds only these pages.
    """
    from pdfminer.pdfpage import LITERAL_PAGE, LITERAL_PAGES, PDFPage
    from pdfminer.pdftypes import PDFObjRef, dict_value, list_value, resolve1
    from pdfplumber.page import Page

    wanted = set(indices)
    found: dict[int, Any] = {}
    try:
        doc = pdf.doc
        root = doc.catalog["Pages"]

        def walk(obj: Any, parent: dict[str, Any], start: int, depth: int) -> None:
            if depth > 40:
                raise ValueError("page tree too deep")
            props = dict_value(obj).copy()
            for k in PDFPage.INHERITABLE_ATTRS:
                if k in parent and k not in props:
                    props[k] = parent[k]
            kind = props.get("Type")
            if kind is LITERAL_PAGES and "Kids" in props:
                kids = list_value(props["Kids"])
                if resolve1(props.get("Count")) == len(kids):
                    # Every kid holds one page (the usual flat tree): index straight into it.
                    for w in sorted(wanted):
                        if start <= w < start + len(kids):
                            kid = kids[w - start]
                            kd = dict_value(kid)
                            if kd.get("Type") is LITERAL_PAGES and resolve1(kd.get("Count")) != 1:
                                raise ValueError("inconsistent /Count")
                            walk(kid, props, w, depth + 1)
                    return
                idx = start
                for kid in kids:
                    kd = dict_value(kid)
                    cnt = int(resolve1(kd.get("Count", 1))) if kd.get("Type") is LITERAL_PAGES else 1
                    if any(idx <= w < idx + cnt for w in wanted):
                        walk(kid, props, idx, depth + 1)
                    idx += cnt
            elif kind is LITERAL_PAGE and start in wanted:
                pageid = obj.objid if isinstance(obj, PDFObjRef) else id(obj)
                found[start] = Page(pdf, PDFPage(doc, pageid, props, None), page_number=start + 1)

        walk(root, doc.catalog, 0, 0)
        if set(found) != wanted:
            raise ValueError("page tree /Count values do not add up")
        pdf._pages = [found[i] for i in sorted(found)]  # pdf.close() would otherwise build every page to close it
        return found
    except Exception:  # noqa: BLE001 — a malformed tree: let pdfplumber build every page
        try:
            pages = pdf.pages
        except Exception:  # noqa: BLE001 — pdfplumber cannot read the page tree at all
            pages = []
        return {i: pages[i] for i in indices if i < len(pages)}  # pages it could not find are left out


def _layout_chunk(job: tuple[str, str | None, list[int]]) -> list[tuple[int, str]]:
    path, password, indices = job
    out = []
    missing: list[int] = []
    with open_plumber(path, password) as pdf:
        pages = plumber_pages(pdf, indices)
        for i in indices:
            page = pages.get(i)
            try:
                text = page.extract_text(layout=True) or "" if page is not None else None
            except Exception:  # noqa: BLE001 — pdfplumber fails on some odd pages
                text = None
            if text is None:
                missing.append(i)
                out.append((i + 1, ""))
                continue
            text = "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
            out.append((i + 1, re.sub(r"\n{4,}", "\n\n\n", text)))
            page.close()
    if missing:
        # pdfplumber could not read these pages: give pdfium's plain text instead of nothing.
        plain = {n: shown(t) for n, t in _plain_chunk((path, password, missing))}
        out = [(n, plain.get(n, t) if n - 1 in missing else t) for n, t in out]
        print(f"note: layout mode could not read page(s) {_ranges([i + 1 for i in missing])}; plain text shown instead", file=sys.stderr)
    return out


def iter_pages(path: str, password: str | None, numbers: list[int], worker: Callable[[Any], list[Any]], per_worker: int) -> Iterator[Any]:
    """Yields per-page results in order, computing batches in parallel processes when the document is big."""
    indices = [n - 1 for n in numbers]
    if len(indices) <= per_worker:
        yield from worker((path, password, indices))
        return
    batch = per_worker * 8
    for s in range(0, len(indices), batch):
        part = indices[s : s + batch]
        chunks = [(path, password, part[i : i + per_worker]) for i in range(0, len(part), per_worker)]
        for res in pool_map(worker, chunks):
            yield from res


# ── modes ───────────────────────────────────────────────────────────────


CACHE_VERSION = "3"
CHUNK = 25  # pages per cached layout/words/tables entry
MAP_PAGES = 100  # above this many pages (and without --pages) the default read is a map


SHOWN: dict[str, str] = {}  # the input path as the caller typed it, for continuation commands


def shell_arg(x: str) -> str:
    """An argument as it must be typed in a shell command (quoted when it is not a plain word)."""
    if re.fullmatch(r"[A-Za-z0-9_./:,=+@%-]+", x):
        return x
    if "'" not in x:
        return f"'{x}'"
    return '"' + re.sub(r'(["\\$`])', r"\\\1", x) + '"'


def cmd_for(path: Path, *args: str) -> str:
    """The exact command that reads the next part (for continuation hints)."""
    return "python3 scripts/pdf_text.py " + " ".join(shell_arg(x) for x in [SHOWN.get("pdf", str(path)), *args])


def all_plain_text(path: Path, password: str | None, count: int) -> list[str]:
    """Every page's plain text, cached by file content (never cached for password-protected files)."""
    def compute() -> list[str]:
        return [t for _, t in iter_pages(str(path), password, list(range(1, count + 1)), _plain_chunk, 120)]

    if password:
        return compute()
    return cached_value(path, "pdf-text-plain", {}, CACHE_VERSION, compute)


def plain_texts(path: Path, password: str | None, count: int, numbers: list[int]) -> dict[int, str]:
    """Plain text of some pages: from the whole-file cache when it exists, else just those pages when few."""
    from _cache import enabled, lookup

    cached = enabled() and not password and lookup(path, "pdf-text-plain", {}, CACHE_VERSION) is not None
    if not cached and len(numbers) * 3 < count:
        return dict(iter_pages(str(path), password, numbers, _plain_chunk, 120))
    texts = all_plain_text(path, password, count)
    return {n: texts[n - 1] for n in numbers}


def chunked(path: Path, password: str | None, numbers: list[int], kind: str, params: dict[str, Any], worker: Callable[[Any], list[Any]], budgeted: bool = True) -> Iterator[tuple[int, Any]]:
    """(page, result) for slow modes, cached in chunks of CHUNK pages; missing chunks are computed in parallel.

    A budgeted read usually stops early, so it computes 1, 2, 4, then 8 chunks at a time instead of 8 at once.
    """
    import json

    from _cache import enabled, lookup

    doc = open_pdfium(path, password)
    total = len(doc)
    doc.close()
    wanted = sorted(set(numbers))
    chunks = sorted({(n - 1) // CHUNK for n in wanted})
    use_cache = enabled() and not password
    batch = 1 if budgeted else 8
    s = 0
    while s < len(chunks):
        part = chunks[s : s + batch]
        s += batch
        batch = min(8, batch * 2)
        merged: dict[int, Any] = {}
        missing = []
        for ck in part:
            hit = lookup(path, kind, {**params, "chunk": ck, "size": CHUNK}, CACHE_VERSION) if use_cache else None
            try:
                rows = json.loads((hit / "value.json").read_text(encoding="utf-8")) if hit else None
            except (OSError, ValueError):
                rows = None
            if rows is None:
                missing.append(ck)
            else:
                merged.update({int(n): v for n, v in rows})
        jobs = [(str(path), password, ck, total, kind, params, worker.__name__, use_cache) for ck in missing]
        if jobs:
            for m in pool_map(_load_chunk, jobs) if len(jobs) > 1 else [_load_chunk(jobs[0])]:
                merged.update(m)
        for n in wanted:
            if (n - 1) // CHUNK in part:
                yield n, merged.get(n)


def _load_chunk(job: tuple[str, str | None, int, int, str, dict[str, Any], str, bool]) -> dict[int, Any]:
    """Computes (and caches) one chunk of pages for a slow mode (runs in worker processes)."""
    path, password, ck, total, kind, params, worker_name, use_cache = job
    worker = globals()[worker_name]
    idx = list(range(ck * CHUNK, min((ck + 1) * CHUNK, total)))

    def compute() -> list[Any]:
        if worker_name == "_tables_chunk":
            tables = worker((path, password, idx, params.get("settings", {})))
            by_page: dict[int, list[Any]] = {i + 1: [] for i in idx}
            for t in tables:
                by_page[t["page"]].append(t)
            return [[n, v] for n, v in by_page.items()]
        return [[n, v] for n, v in worker((path, password, idx))]

    rows = cached_value(path, kind, {**params, "chunk": ck, "size": CHUNK}, CACHE_VERSION, compute) if use_cache else compute()
    return {int(n): v for n, v in rows}


def outline_sections(path: Path, password: str | None, count: int) -> list[tuple[str, int, int, int]]:
    """(title, first page, last page, depth) for the top outline levels, in page order (cached)."""
    if password:
        return _outline_sections(path, password, count)
    return [tuple(x) for x in cached_value(path, "pdf-text-outline", {}, CACHE_VERSION, lambda: _outline_sections(path, None, count))]  # type: ignore[misc]


def _outline_sections(path: Path, password: str | None, count: int) -> list[tuple[str, int, int, int]]:
    try:
        from pdf_meta import outline_tree
        from _pdfkit import open_reader

        tree = outline_tree(open_reader(path, password))
    except Exception:  # noqa: BLE001
        return []
    flat: list[tuple[str, int, int]] = []

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for nd in nodes:
            if nd.get("page"):
                flat.append((nd["title"], nd["page"], depth))
            if depth < 1 and nd.get("children"):
                walk(nd["children"], depth + 1)

    walk(tree, 0)
    if len([f for f in flat if f[2] == 0]) >= 4:
        flat = [f for f in flat if f[2] == 0]
    flat.sort(key=lambda f: f[1])
    out = []
    for k, (title, first, depth) in enumerate(flat):
        nxt = next((f[1] for f in flat[k + 1 :] if f[1] > first), count + 1)
        out.append((title, first, max(first, nxt - 1), depth))
    return out


def mode_map(a: Any, path: Path, count: int, texts: list[str]) -> int:
    total = sum(len(t) for t in texts)
    sections = outline_sections(path, a.password, count)
    no_text = [i + 1 for i, t in enumerate(texts) if not t.strip()]
    rows = []
    if sections and sections[0][1] > 1:
        sections = [("(before the first section)", 1, sections[0][1] - 1, 0), *sections]
    if sections:
        for title, first, last, depth in sections[:120]:
            chars = sum(len(t) for t in texts[first - 1 : last])
            rows.append([("  " * depth) + title[:70], f"{first}-{last}" if last > first else str(first), f"{chars:,}"])
        table = md_table(["section", "pages", "characters"], rows)
    else:
        step = 10 if count <= 300 else 25 if count <= 1000 else 50
        for s in range(0, count, step):
            block = texts[s : s + step]
            first_line = shown(next((ln.strip() for t in block for ln in t.splitlines() if len(ln.strip()) > 3), ""))
            rows.append([f"{s + 1}-{min(count, s + step)}", f"{sum(len(t) for t in block):,}", first_line[:80]])
        table = md_table(["pages", "characters", "starts with"], rows)
    data = {"file": str(path), "pages": count, "characters": total, "sections": [{"title": t, "first_page": f, "last_page": l, "characters": sum(len(x) for x in texts[f - 1 : l])} for t, f, l, _d in sections], "pages_without_text": no_text}
    if a.format == "json":
        emit(data, "json")
        return 0
    why = "" if a.map else " (too much to read at once; --all prints it anyway)"
    lines = [f"# {path.name}: {plural(count, 'page')}, {total:,} characters of text: a map{why}", "", table]
    if no_text:
        lines.append(f"\nPages without text (scanned or drawings; render them to look): {_ranges(no_text)}")
    body = next((sec for sec in sections if not sec[0].startswith("(")), sections[0]) if sections else None
    first_range = (_ranges(list(range(body[1], body[2] + 1))) if body else f"1-{min(count, 10)}") if count > 1 else "1"
    lines.append(f"\nRead a part: {cmd_for(path, '--pages', first_range)}")
    lines.append(f"Search: {cmd_for(path, '--search', 'PATTERN')}")
    print("\n".join(lines))
    return 0


def _ranges(nums: list[int]) -> str:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def mode_text(a: Any, path: Path, count: int, labels: list[str] | None) -> int:
    if a.layout:
        numbers = parse_pages(a.pages, count)
        source: Iterator[Any] = chunked(path, a.password, numbers, "pdf-text-layout", {}, _layout_chunk, bool(a.max_chars))
    elif a.map or (not a.pages and not a.all):
        texts = all_plain_text(path, a.password, count)
        if not any(t.strip() for t in texts):
            print(no_text_message(path, list(range(1, count + 1)), f"{path.name}: {plural(count, 'page')}, no text"))
            return 0
        if a.map or count > MAP_PAGES or sum(len(t) for t in texts) > 4 * (a.max_chars or DEFAULT_MAX_CHARS):
            return mode_map(a, path, count, texts)
        numbers = list(range(1, count + 1))
        source = ((n, shown(texts[n - 1])) for n in numbers)
    else:
        numbers = parse_pages(a.pages, count)
        got = plain_texts(path, a.password, count, numbers)
        source = ((n, shown(got[n])) for n in numbers)
    budget = a.max_chars or 0
    used = 0
    results: list[dict[str, Any]] = []
    stopped_after: int | None = None
    cut_page = ""
    for n, text in source:
        cost = len(text) + 20
        if budget and results and used + cost > budget:
            stopped_after = results[-1]["page"]
            break
        if budget and not results and cost > budget:
            # One page bigger than the whole budget: show its start and say how to get all of it.
            cut_page = f"page {n} has {len(text):,} characters; the first {budget:,} are shown. All of it: {cmd_for(path, '--pages', str(n), '--max-chars', '0', *(['--layout'] if a.layout else []))}"
            text = text[:budget]
            cost = budget
        results.append({"page": n, **({"label": labels[n - 1]} if labels else {}), "text": text})
        used += cost
        if cut_page and n != numbers[-1]:
            stopped_after = n
            break
    rest = numbers[numbers.index(stopped_after) + 1 :] if stopped_after else []
    hint = ""
    if stopped_after and rest:
        hint = f"stopped after page {stopped_after} of {count} to stay under {budget:,} characters. Next: {cmd_for(path, '--pages', _ranges(rest), *(['--layout'] if a.layout else []), *(['--max-chars', str(a.max_chars)] if a.max_chars != DEFAULT_MAX_CHARS else []))}"
    if a.format == "json":
        extra = {**({"truncated": hint} if hint else {}), **({"page_cut": cut_page} if cut_page else {})}
        emit({"file": str(path), "pages": count, "results": results, **extra}, "json", max_chars=None)
        return 0
    if results and not any(r["text"].strip() for r in results):
        print(no_text_message(path, [r["page"] for r in results], "No text"))
        return 0
    out = []
    for r in results:
        body = r["text"] if r["text"].strip() else NO_TEXT.format(n=r["page"])
        if a.no_markers:
            out.append(body)
        else:
            label = f" ({r['label']})" if r.get("label") and r["label"] != str(r["page"]) else ""
            out.append(f"--- page {r['page']}{label} ---\n{body}")
    text = "\n\n".join(out)
    if cut_page:
        text += f"\n\n[… {cut_page}]"
    if hint:
        text += f"\n\n[… {hint}]"
    print(text)
    return 0


def _words_chunk(job: tuple[str, str | None, list[int]]) -> list[tuple[int, list[list[Any]]]]:
    """Words with view boxes, [text, x0, top, x1, bottom], for some pages (worker process)."""
    import pypdfium2 as pdfium

    path, password, indices = job
    doc = pdfium.PdfDocument(path, password=password)
    out = []
    try:
        for i in indices:
            page = doc[i]
            geo = pdfium_geometry(page)
            tp = page.get_textpage()
            chars = page_chars(tp)
            boxes = char_boxes(tp, len(chars), chars)
            words: list[list[Any]] = []
            cur: list[int] = []

            def flush() -> None:
                if not cur:
                    return
                rects = [boxes[k] for k in cur if boxes[k][2] > boxes[k][0] or boxes[k][3] > boxes[k][1]] or [boxes[k] for k in cur]
                box = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
                words.append(["".join(chars[k] for k in cur), *view_box(geo, box)])
                cur.clear()

            prev_mid = None
            for k, ch in enumerate(chars):
                code = ord(ch)
                if code in WS or code in (0x02, 0xFFFE, 0):
                    flush()
                    prev_mid = None
                    continue
                _l, b, _r, t = boxes[k]
                mid = (b + t) / 2
                if prev_mid is not None and cur and abs(mid - prev_mid) > max(1.0, t - b) * 0.7:
                    flush()
                cur.append(k)
                prev_mid = mid
            flush()
            tp.close()
            page.close()
            out.append((i + 1, merge_leaders(words)))
    finally:
        doc.close()
    return out


_LEADER = re.compile(r"[.·•_…\-–—]+")


def merge_leaders(words: list[list[Any]]) -> list[list[Any]]:
    """Joins runs of punctuation-only "words" on one line (dot leaders in a table of contents) into one word."""
    out: list[list[Any]] = []
    run: list[list[Any]] = []

    def flush() -> None:
        if len(run) >= 3:
            out.append([run[0][0][:1] * 3, min(w[1] for w in run), min(w[2] for w in run), max(w[3] for w in run), max(w[4] for w in run)])
        else:
            out.extend(run)
        run.clear()

    for w in words:
        if _LEADER.fullmatch(w[0]) and (not run or (abs(w[2] - run[-1][2]) < 2 and 0 <= w[1] - run[-1][3] < 12)):
            run.append(w)
            continue
        flush()
        if _LEADER.fullmatch(w[0]):
            run.append(w)
        else:
            out.append(w)
    flush()
    return out


def next_hint(path: Path, stopped_after: int, numbers: list[int], budget: int, *mode: str) -> str:
    """'stopped after page N … Next: <command>' for page-budgeted output (the next command covers the rest)."""
    rest = numbers[numbers.index(stopped_after) + 1 :]
    return f"stopped after page {stopped_after} to stay under {budget:,} characters. Next: {cmd_for(path, *mode, '--pages', _ranges(rest))}"


def in_region(box: list[float], region: tuple[float, float, float, float]) -> bool:
    """True when the centre of a view box lies inside a view region."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def page_sizes(path: Path, password: str | None, numbers: list[int]) -> dict[int, tuple[float, float]]:
    doc = open_pdfium(path, password)
    try:
        return {n: (pdfium_geometry(doc[n - 1]).width, pdfium_geometry(doc[n - 1]).height) for n in numbers}
    finally:
        doc.close()


def mode_words(a: Any, path: Path, count: int) -> int:
    from _pdfkit import parse_box

    numbers = parse_pages(a.pages, count)
    budget = max(1000, a.max_chars - 300) if a.max_chars else 0  # room for the heading and the next command

    def word_cost(w: dict[str, Any]) -> int:
        if a.format == "json":
            return len(json.dumps(w, ensure_ascii=False)) + 8
        return len(w["text"]) + len(str(w["page"])) + sum(len(str(v)) for v in w["box"]) + 20  # "| … |" row

    regions = {n: parse_box(a.region, *wh) for n, wh in page_sizes(path, a.password, numbers).items()} if a.region else {}
    words: list[dict[str, Any]] = []
    used, stopped, cut = 0, None, ""
    last = None
    for n, page_words in chunked(path, a.password, numbers, "pdf-text-words", {}, _words_chunk, bool(a.max_chars)):
        items = [{"page": n, "text": w[0], "box": w[1:5]} for w in page_words or [] if not regions or in_region(w[1:5], regions[n])]
        cost = sum(word_cost(w) for w in items)
        if budget and words and used + cost > budget:
            stopped = last
            break
        if budget and not words and cost > budget:
            keep, c = 0, 0
            while keep < len(items) and c + word_cost(items[keep]) <= budget:
                c += word_cost(items[keep])
                keep += 1
            cut = f"page {n} has {len(items):,} words; the first {keep:,} are shown. Narrow with --region x0,top,x1,bottom (points or fractions, e.g. 0,0,1,0.5 for the top half)"
            items = items[:keep]
            cost = c
        words.extend(items)
        used += cost
        last = n
        if cut:
            if n != numbers[-1]:
                stopped = n
            break
    extra = [*(["--region", a.region] if a.region else []), *(["--format", "json"] if a.format == "json" else []), *(["--max-chars", str(a.max_chars)] if a.max_chars != DEFAULT_MAX_CHARS else [])]
    hint = next_hint(path, stopped, numbers, a.max_chars, "--words", *extra) if stopped and numbers.index(stopped) + 1 < len(numbers) else ""
    if a.format == "json":
        emit({"file": str(path), "count": len(words), "words": words, **({"page_cut": cut} if cut else {}), **({"truncated": hint} if hint else {})}, "json", max_chars=None)
    else:
        rows = [[w["page"], w["text"], *w["box"]] for w in words]
        text = f"{len(words)} words (boxes in points from the top-left: x0, top, x1, bottom)\n\n" + md_table(["page", "word", "x0", "top", "x1", "bottom"], rows)
        print(text + (f"\n\n[… {cut}]" if cut else "") + (f"\n\n[… {hint}]" if hint else ""))
    return 0


PREFILTER_PAGES = 30  # above this many pages, search candidate pages in the cached plain text first


def literal_regex(text: str) -> str:
    """A literal phrase as a regex (spaces between words match any whitespace, line breaks included)."""
    return r"\s+".join(re.escape(part) for part in text.split())


def compile_search(a: Any) -> re.Pattern[str]:
    """--search PATTERN as a regex; a space in it also matches a line break (the text is searched normalised)."""
    pattern = literal_regex(a.search) if a.literal else a.search
    try:
        return re.compile(pattern, re.IGNORECASE if a.ignore_case else 0)
    except re.error as e:
        raise UsageError(f"bad regular expression: {e}") from e


def quoted(path: Path) -> str:
    return shell_arg(SHOWN.get("pdf", str(path)))


def no_text_message(path: Path, pages: list[int], what: str) -> str:
    """Tells the agent that these pages have no text layer and how to look at them instead."""
    first = _ranges(pages[:20])
    sheet = f" (or --sheet --pages {_ranges(pages)} for an overview)" if len(pages) > 6 else ""
    return (f"{what}: {'none of the' if len(pages) > 1 else 'the'} {plural(len(pages), 'page')} {'has' if len(pages) == 1 else 'have'} a text layer "
            f"(scanned or drawn), so there is no text to {'search' if 'search' in what else 'extract'}, and no OCR here. Look at them instead: "
            f"python3 scripts/pdf_render.py {quoted(path)} --pages {first}{sheet}, then view_image; zoom on small print with --region.")


def mode_search(a: Any, path: Path, count: int, labels: list[str] | None) -> int:
    from _textfind import body_bounds, cross_page, edge_lines, find_spans, quick_views

    rx = compile_search(a)
    requested = parse_pages(a.pages, count)
    doc = open_pdfium(path, a.password)
    if len(requested) > PREFILTER_PAGES:
        # Skip pages whose (cached) plain text cannot match; only candidate pages get boxes.
        texts = all_plain_text(path, a.password, count)
        sub = {n: texts[n - 1] for n in requested}
    else:
        sub = {}
        for n in requested:
            page = doc[n - 1]
            tp = page.get_textpage()
            sub[n] = page_chars(tp)
            tp.close()
            page.close()
    no_text = [n for n in requested if not sub[n].strip()]
    quick = {n: max(len(rx.findall(v)) for v in quick_views(sub[n])) for n in requested if sub[n].strip()}
    quick = {n: c for n, c in quick.items() if c}
    edges = edge_lines(sub)
    across = {c.first for c in cross_page(sub, rx, None, body_bounds(sub, edges))}  # matches over a page break
    for n in across:
        quick[n] = quick.get(n, 0) + 1
    numbers = sorted(quick)
    hits: list[dict[str, Any]] = []
    sizes: dict[int, tuple[float, float]] = {}
    ctx = a.context
    budget = a.max_chars or 0
    used = 0
    stop_page: int | None = None  # the last page whose matches are all shown, when output stopped early
    why = ""

    def boxes(page: Any, tp: Any, s: int, e: int) -> tuple[list[float] | None, list[list[float]]]:
        geo = pdfium_geometry(page)
        vrects = [view_box(geo, r) for r in range_rects(tp, s, e - s)]
        union = [min(r[0] for r in vrects), min(r[1] for r in vrects), max(r[2] for r in vrects), max(r[3] for r in vrects)] if vrects else None
        return union, vrects

    try:
        for k, n in enumerate(numbers):
            page = doc[n - 1]
            tp = page.get_textpage()
            chars = page_chars(tp)
            page_hits: list[dict[str, Any]] = []
            geo = pdfium_geometry(page)
            sizes[n] = (geo.width, geo.height)
            for h in find_spans(chars, rx):
                union, vrects = boxes(page, tp, h.start, h.end)
                page_hits.append({"page": n, **({"label": labels[n - 1]} if labels else {}), "match": h.text, "box": union, "rects": vrects, "context": h.context(ctx)})
            if n in across and n + 1 <= count:
                page2 = doc[n]
                tp2 = page2.get_textpage()
                pair = {n: chars, n + 1: page_chars(tp2)}
                for c in cross_page(pair, rx, None, body_bounds(pair, edges)):
                    union, vrects = boxes(page, tp, c.s1, c.e1)
                    union2, vrects2 = boxes(page2, tp2, c.s2, c.e2)
                    page_hits.append({"page": n, **({"label": labels[n - 1]} if labels else {}), "match": c.text, "box": union, "rects": vrects, "continues_on": {"page": n + 1, "box": union2, "rects": vrects2}, "context": c.context(ctx)})
                tp2.close()
                page2.close()
            tp.close()
            page.close()
            page_hits.sort(key=lambda x: (0, x["box"][1], x["box"][0]) if x["box"] and "continues_on" not in x else (1, 0, 0))
            cost = sum(len(x["match"]) + len(x["context"]) + (160 if a.format == "json" else 40) for x in page_hits)
            if hits and ((budget and used + cost > budget) or len(hits) + len(page_hits) > a.limit):
                stop_page = numbers[k - 1]
                why = f"--limit {a.limit}" if len(hits) + len(page_hits) > a.limit and not (budget and used + cost > budget) else f"{budget} characters"
                break
            if not hits and len(page_hits) > a.limit:
                page_hits = page_hits[: a.limit]
                why = f"--limit {a.limit} (page {n} has more)"
            hits.extend(page_hits)
            used += cost
    finally:
        doc.close()
    rest = requested[requested.index(stop_page) + 1 :] if stop_page else []
    nxt = ""
    more = ""
    if stop_page:
        args = ["--search", a.search, *(["--literal"] if a.literal else []), *(["-i"] if a.ignore_case else []), *(["--context", str(a.context)] if a.context != 40 else []), "--pages", _ranges(rest)]
        nxt = cmd_for(path, *args, *(["--format", "json"] if a.format == "json" else []))
        later = [n for n in numbers if n > stop_page]
        if quick:
            more = f"; about {sum(quick.get(n, 0) for n in later)} more on {plural(len(later), 'page')} after page {stop_page}"
        else:
            more = f"; pages after {stop_page} not searched yet"
    pages_hit = sorted({h["page"] for h in hits})
    summary = f"{len(hits)} match{'es' if len(hits) != 1 else ''} on {plural(len(pages_hit), 'page')}" + (f" (stopped at {why}{more})" if why else "")
    searched = len(requested)
    no_text = sorted(set(no_text))
    if no_text and len(no_text) == searched:
        note = no_text_message(path, no_text, "Nothing to search")
    elif no_text:
        note = f"Note: {plural(len(no_text), 'page')} of the {searched} searched ha{'s' if len(no_text) == 1 else 've'} no text layer ({_ranges(no_text[:40])}{'…' if len(no_text) > 40 else ''}): matches there cannot be found. Render them and look (pdf_render.py --pages …)."
    else:
        note = ""
    if a.format == "json":
        data = {"file": str(path), "pattern": rx.pattern, "pages_searched": searched, "count": len(hits), "pages": pages_hit, "matches": hits}
        if no_text:
            data["pages_without_text"] = no_text
            data["note"] = note
        if nxt:
            data["truncated"] = f"stopped after page {stop_page} at {why}{more}"
            data["next"] = nxt
        emit(data, "json", max_chars=None)
    else:
        rows = [[f"{h['page']} (→{h['continues_on']['page']})" if h.get("continues_on") else h["page"], h["match"], ", ".join(str(v) for v in h["box"]) if h["box"] else "", h["context"]] for h in hits]
        look = ""
        for h in hits:
            b = h["box"]
            if not b:
                continue
            pw, ph = sizes.get(h["page"], (0.0, 0.0))
            reg = (max(0.0, b[0] - 60), max(0.0, b[1] - 40), min(pw, b[2] + 60), min(ph, b[3] + 40))
            if reg[2] - reg[0] < 2 or reg[3] - reg[1] < 2:
                continue  # a match outside the visible page (off the crop box)
            region = ",".join(str(fmt_num(v)) for v in reg)
            look = f"\n\nSee it: python3 scripts/pdf_render.py {quoted(path)} --pages {h['page']} --region {region}"
            break
        out = summary + ("\n\n" + md_table(["page", "match", "box (x0, top, x1, bottom)", "context"], rows) + look if rows else "")
        if note:
            out = note if not hits and len(no_text) == searched else out + "\n\n" + note
        if nxt:
            out += f"\n\nNext: {nxt}"
        print(cap(out, budget + 10000 if budget else None, "Narrow with --pages or a smaller --context."))
    return 0 if hits or not a.fail_if_none else 1


# ── tables ──────────────────────────────────────────────────────────────


def _num(s: str) -> bool:
    return bool(re.fullmatch(r"[-+(]?[$€£¥]?\s?\d[\d,.\s]*%?\)?", s.strip())) if s and s.strip() else False


def _clean_cell(v: Any) -> str:
    return re.sub(r"\s*\n\s*", " ", str(v)).strip() if v is not None else ""


def detect_header(rows: list[list[str]], bold_first: bool) -> bool:
    if len(rows) < 2:
        return False
    first = rows[0]
    if bold_first and any(first):
        return True
    if not all(first) or any(_num(c) for c in first):
        return False
    return any(_num(c) for r in rows[1:] for c in r) or len(set(first)) == len(first)


def _pages_with_paths(path: str, password: str | None, indices: list[int]) -> set[int]:
    """Pages drawn with enough vector paths to hold a ruled or shaded table (a running header's rule alone is not)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    doc = pdfium.PdfDocument(path, password=password)
    found = set()
    try:
        for i in indices:
            page = doc[i]
            segments = 0
            for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_PATH,), max_depth=6):
                segments += max(1, raw.FPDFPath_CountSegments(obj.raw))
                if segments >= 8:
                    found.add(i)
                    break
            page.close()
    finally:
        doc.close()
    return found


def _cluster(values: list[float], tol: float) -> list[float]:
    """Sorted values with near-duplicates (within tol) merged into their mean."""
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def zebra_edges(page: Any) -> list[dict[str, Any]]:
    """Vertical edges that let the line finder see every row of a table whose rows are shaded alternately.

    Such a table (pdf_create's, and many reports) has cell fills only on every other row and no vertical rules, so
    pdfplumber finds cells in the shaded rows alone and silently drops the others. Shaded cells that line up in a
    column stack give the column boundaries; they are extended over the unshaded rows between them and over one
    unshaded row above or below when a rule closes the table there.
    """
    fills = [r for r in page.rects if r.get("fill") and r["width"] > 3 and 3 < r["height"] < 200]
    if len(fills) < 2:
        return []
    # Rows of touching cells: fills with the same top and bottom that sit side by side.
    rows: dict[tuple[int, int], list[Any]] = {}
    for r in fills:
        rows.setdefault((round(r["top"]), round(r["bottom"])), []).append(r)
    bands = []
    for rs in rows.values():
        rs.sort(key=lambda r: r["x0"])
        group = [rs[0]]
        for r in rs[1:] + [None]:
            if r is not None and abs(r["x0"] - group[-1]["x1"]) <= 1.5:
                group.append(r)
                continue
            if len(group) >= 2:
                xs = _cluster([g["x0"] for g in group] + [g["x1"] for g in group], 1.5)
                bands.append({"top": min(g["top"] for g in group), "bottom": max(g["bottom"] for g in group), "x0": xs[0], "x1": xs[-1], "xs": xs})
            if r is not None:
                group = [r]
    if not bands:
        return []
    bands.sort(key=lambda b: b["top"])
    hedges = [e for e in page.edges if e["orientation"] == "h"]
    edges: list[dict[str, Any]] = []
    used = [False] * len(bands)
    for i, first in enumerate(bands):
        if used[i]:
            continue
        stack = [first]
        used[i] = True
        for j in range(i + 1, len(bands)):
            b, last = bands[j], stack[-1]
            if used[j] or abs(b["x0"] - first["x0"]) > 2 or abs(b["x1"] - first["x1"]) > 2:
                continue
            height = max(b["bottom"] - b["top"], last["bottom"] - last["top"])
            if b["top"] - last["bottom"] > max(3 * height, 40):
                break
            stack.append(b)
            used[j] = True
        xs = _cluster([x for b in stack for x in b["xs"]], 1.5)
        top, bottom = stack[0]["top"], stack[-1]["bottom"]
        row_h = max(b["bottom"] - b["top"] for b in stack)

        def closing_rule(y: float, down: bool) -> float | None:
            spans = [e["top"] for e in hedges if abs(e["x0"] - xs[0]) <= 3 and abs(e["x1"] - xs[-1]) <= 3 and ((y + 1 < e["top"] <= y + 3 * row_h) if down else (y - 3 * row_h <= e["top"] < y - 1))]
            return (min(spans) if down else max(spans)) if spans else None

        below, above = closing_rule(bottom, True), closing_rule(top, False)
        if len(stack) < 2 and below is None and above is None:
            continue  # one shaded row and nothing to extend it to
        top = above if above is not None else top
        bottom = below if below is not None else bottom
        for x in xs:
            edges.append({"object_type": "desk_edge", "orientation": "v", "x0": x, "x1": x, "top": top, "bottom": bottom, "width": 0, "height": bottom - top, "doctop": top})
    return edges


def _tables_chunk(job: tuple[str, str | None, list[int], dict[str, Any]]) -> list[dict[str, Any]]:
    path, password, indices, settings = job
    ruled = "text" not in (settings.get("vertical_strategy"), settings.get("horizontal_strategy"))
    if ruled:
        # Ruled tables need drawn lines: skip pages without any vector paths (pdfium checks that in a millisecond).
        with_paths = _pages_with_paths(path, password, indices)
        indices = [i for i in indices if i in with_paths]
    out = []
    if not indices:
        return out
    with open_plumber(path, password) as pdf:
        pages = plumber_pages(pdf, indices)
        unread = [i + 1 for i in indices if i not in pages]
        if unread:
            print(f"note: the table finder could not read page(s) {_ranges(unread)}; render them and look instead", file=sys.stderr)
        for i in indices:
            page = pages.get(i)
            if page is None:
                continue
            try:
                extra = zebra_edges(page) if settings.get("vertical_strategy", "lines") == "lines" else []
                found = page.find_tables({**settings, "explicit_vertical_lines": extra} if extra else settings)
            except Exception:  # noqa: BLE001 — pdfplumber can fail on odd pages
                found = []
            ox, oy = page.bbox[0], page.bbox[1]
            for k, t in enumerate(found):
                rows = [[_clean_cell(c) for c in r] for r in t.extract()]
                rows = [r for r in rows if any(r)]
                if not rows or (len(rows) < 2 and len(rows[0]) < 2):
                    continue
                bold = False
                try:
                    r0 = t.rows[0].bbox
                    chars = page.crop(r0, strict=False).chars
                    bold = bool(chars) and sum(1 for ch in chars if re.search(r"bold|heavy|black|semibold", ch.get("fontname", ""), re.I)) > len(chars) * 0.6
                except Exception:  # noqa: BLE001
                    pass
                header = detect_header(rows, bold)
                x0, top, x1, bottom = t.bbox
                out.append({
                    "page": i + 1,
                    "index": k + 1,
                    "box": [fmt_num(x0 - ox), fmt_num(top - oy), fmt_num(x1 - ox), fmt_num(bottom - oy)],
                    "columns": max(len(r) for r in rows),
                    "header": rows[0] if header else None,
                    "rows": rows[1:] if header else rows,
                })
            page.close()
    return out


def merge_continued(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Joins a table that continues at the top of the next page (same column count, no new header)."""
    out: list[dict[str, Any]] = []
    for t in tables:
        prev = out[-1] if out else None
        if prev and t["page"] == prev.get("last_page", prev["page"]) + 1 and t["index"] == 1 and t["columns"] == prev["columns"] and (t["header"] is None or t["header"] == prev["header"]):
            prev["rows"].extend(t["rows"])
            prev["last_page"] = t["page"]
            continue
        out.append(dict(t))
    return out


CELL_MAX = 200  # longer cells are cut in printed tables (JSON and --out CSV files keep them whole)
MD_TABLES_MAX = 4000  # printed tables longer than this as Markdown default to CSV (far fewer tokens)


def _cut(rows: list[list[str]]) -> list[list[str]]:
    return [[c if len(c) <= CELL_MAX else c[:CELL_MAX] + f"…[+{len(c) - CELL_MAX} chars]" for c in r] for r in rows]


def table_title(t: dict[str, Any]) -> str:
    pages = f"page {t['page']}" + (f"-{t['last_page']}" if t.get("last_page") else "")
    nrows = len(t["rows"])
    note = "" if t["header"] else ", no header row detected"
    return f"Table {t['page']}.{t['index']} ({pages}, {nrows} row{'s' if nrows != 1 else ''} × {t['columns']} columns{note}; box {', '.join(str(v) for v in t['box'])})"


def table_md(t: dict[str, Any]) -> str:
    header = t["header"] or [f"col{i + 1}" for i in range(t["columns"])]
    return f"### {table_title(t)}\n\n" + md_table(_cut([header])[0], _cut(t["rows"]))


def table_csv(t: dict[str, Any], cut: bool = False) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    if t["header"]:
        wr.writerow(_cut([t["header"]])[0] if cut else t["header"])
    wr.writerows(_cut(t["rows"]) if cut else t["rows"])
    return buf.getvalue()


def mode_tables(a: Any, path: Path, count: int) -> int:
    numbers = parse_pages(a.pages, count)
    settings: dict[str, Any] = {}
    if a.table_strategy == "text":
        settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
    elif a.table_strategy == "mixed":
        settings = {"vertical_strategy": "text", "horizontal_strategy": "lines"}
    budget = 0 if a.out else (a.max_chars or 0)
    tables: list[dict[str, Any]] = []
    used, stopped, last = 0, None, None
    for n, page_tables in chunked(path, a.password, numbers, "pdf-text-tables", {"settings": settings}, _tables_chunk, bool(budget)):
        cost = sum(sum(len(c) + 3 for r in t["rows"] for c in r) + 120 for t in page_tables or [])
        if budget and tables and used + cost > budget:
            stopped = last
            break
        tables.extend(page_tables or [])
        used += cost
        last = n
    if a.merge_tables:
        tables = merge_continued(tables)
    if a.out:
        folder = output_dir(a.out)
        written = []
        for t in tables:
            p = folder / f"{path.stem}-p{t['page']}-t{t['index']}.csv"
            if p.exists() and not a.force:
                raise SkillError(f"{p} already exists; pass --force to replace it")
            p.write_text(table_csv(t), encoding="utf-8", newline="")
            written.append(str(p))
        print(f"{len(written)} table(s) written to {folder}" + ("".join(f"\n{w}" for w in written) if written else ""))
        return 0
    fmt = a.format or "md"
    md_body = "\n\n".join(table_md(t) for t in tables) if fmt == "md" else ""
    auto_csv = a.format is None and len(md_body) > MD_TABLES_MAX
    if auto_csv:
        fmt = "csv"
    extra = ["--table-strategy", a.table_strategy] if a.table_strategy != "lines" else []
    hint = next_hint(path, stopped, numbers, budget, "--tables", *extra, *(["--format", fmt] if fmt != "md" else []), *(["--max-chars", str(a.max_chars)] if a.max_chars != DEFAULT_MAX_CHARS else [])) if stopped else ""
    if fmt == "json":
        emit({"file": str(path), "count": len(tables), "tables": tables, **({"truncated": hint} if hint else {})}, "json", max_chars=None)
        return 0
    if not tables:
        print("no tables found" + ("" if a.table_strategy != "lines" else " with ruled lines or shaded rows; for a table with neither, try --table-strategy text (or mixed) with --pages on its page, and check the cells"))
        return 0
    if fmt == "csv":
        head = f"{len(tables)} table(s) as CSV" + (" (CSV because Markdown would be long; --format md for Markdown)" if auto_csv else "")
        body = "\n".join(f"# {table_title(t)}\n{table_csv(t, cut=True)}" for t in tables)
    else:
        head = f"{len(tables)} table(s)"
        body = md_body
    text = f"{head}\n\n{body}" + (f"\n\n[… {hint}]" if hint else "")
    print(cap(text, budget + 20000 if budget else None, f"Use --pages or --out DIR (one CSV file per table)."))
    return 0


# ── damaged files ───────────────────────────────────────────────────────


def mode_damaged(a: Any, path: Path, err: Exception) -> int:
    """Text, words and search through pdfminer (pdfplumber) for a file pdfium cannot open at all."""
    import logging

    import pdfplumber

    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    try:
        pdf = pdfplumber.open(str(path), password=a.password or "")
        pages = pdf.pages
    except Exception:  # noqa: BLE001 — pdfminer cannot read it either
        pages = []
    if not pages:
        raise SkillError(f"cannot read {path.name}: pdfium, pypdf and pdfminer all fail on it. It is damaged (a truncated download?); ask for another copy")
    if a.tables:
        raise SkillError(f"pdfium cannot open {path.name} (damaged), so its tables cannot be found; its text is readable: pdf_text.py without --tables")
    print(f"note: pdfium cannot open {path.name} (damaged); read with pdfminer instead. Page images and page labels are not available", file=sys.stderr)
    count = len(pages)
    numbers = parse_pages(a.pages, count) if a.pages else list(range(1, count + 1))
    budget = a.max_chars or 0
    results: list[dict[str, Any]] = []
    used = 0
    rx = None
    if a.search:
        try:
            rx = re.compile(literal_regex(a.search) if a.literal else a.search, re.IGNORECASE if a.ignore_case else 0)
        except re.error as e:
            raise UsageError(f"bad regular expression: {e}") from e
    for n in numbers:
        page = pages[n - 1]
        try:
            if a.words:
                items = [{"page": n, "text": w["text"], "box": [fmt_num(w["x0"]), fmt_num(w["top"]), fmt_num(w["x1"]), fmt_num(w["bottom"])]} for w in page.extract_words()]
            elif rx is not None:
                text = page.extract_text() or ""
                items, pos = [], 0
                for h in page.search(rx.pattern, regex=True, case=not a.ignore_case):
                    at = text.find(h["text"], pos)
                    at = at if at >= 0 else text.find(h["text"])
                    pos = at + 1 if at >= 0 else pos
                    ctx = text[max(0, at - a.context) : at + len(h["text"]) + a.context] if at >= 0 else h["text"]
                    items.append({"page": n, "match": re.sub(r"\s+", " ", h["text"]), "box": [fmt_num(h["x0"]), fmt_num(h["top"]), fmt_num(h["x1"]), fmt_num(h["bottom"])], "context": re.sub(r"\s+", " ", ctx).strip()})
            else:
                items = [{"page": n, "text": "\n".join(ln.rstrip() for ln in (page.extract_text(layout=bool(a.layout)) or "").splitlines()).strip("\n")}]
        except Exception as e:  # noqa: BLE001 — a page pdfminer cannot read either
            items = [] if (a.words or rx is not None) else [{"page": n, "text": f"[page {n} could not be read: {type(e).__name__}]"}]
        results.extend(items)
        used += sum(len(json.dumps(i)) for i in items)
        if budget and used > budget and n != numbers[-1]:
            results.append({"page": None, "text": f"[… stopped after page {n} to stay under {budget} characters. Next: {cmd_for(path, '--pages', f'{n + 1}-')}]"})
            break
    pdf.close()
    if a.format == "json":
        emit({"file": str(path), "pages": count, "damaged": str(err), "results": results}, "json", max_chars=None)
    elif a.words:
        print(md_table(["page", "word", "x0", "top", "x1", "bottom"], [[r["page"], r["text"], *r["box"]] for r in results if r["page"]]))
    elif rx is not None:
        print(f"{len(results)} match(es)\n\n" + md_table(["page", "match", "box (x0, top, x1, bottom)", "context"], [[r["page"], r["match"], ", ".join(map(str, r["box"])), r["context"]] for r in results]) if results else "0 matches")
    else:
        print("\n\n".join(r["text"] if r["page"] is None or a.no_markers else f"--- page {r['page']} ---\n{r['text']}" for r in results))
    return 0 if results or not (rx is not None and a.fail_if_none) else 1


# ── main ────────────────────────────────────────────────────────────────


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("pdf")
    p.add_argument("--pages", help="pages like 1-3,7,10- (default: all; output stops at --max-chars and says how to continue)")
    p.add_argument("--password", help="password of an encrypted PDF")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--layout", action="store_true", help="keep the visual layout (columns, alignment); slower")
    mode.add_argument("--words", action="store_true", help="words with bounding boxes")
    mode.add_argument("--tables", action="store_true", help="tables (Markdown, CSV with --format csv, JSON)")
    mode.add_argument("--search", metavar="PATTERN", help="regular expression to find (page, box, context); a space also matches a line break, and words hyphenated at a line end match whole")
    p.add_argument("--region", help="with --words: only words inside x0,top,x1,bottom (points from the top-left, or fractions 0-1)")
    p.add_argument("--literal", action="store_true", help="--search PATTERN is plain text, not a regular expression")
    p.add_argument("--ignore-case", "-i", action="store_true", help="case-insensitive --search")
    p.add_argument("--context", type=int, default=40, help="characters of context around each match (default 40)")
    p.add_argument("--limit", type=int, default=1000, help="stop after this many matches (default 1000)")
    p.add_argument("--fail-if-none", action="store_true", help="exit 1 when --search finds nothing")
    p.add_argument("--table-strategy", choices=["lines", "text", "mixed"], default="lines", help="lines: ruled tables and tables with shaded rows (default); text: tables aligned by whitespace only; mixed: ruled rows, aligned columns")
    p.add_argument("--merge-tables", action="store_true", help="join tables that continue on the next page")
    p.add_argument("--out", help="with --tables: write one CSV per table into this folder")
    p.add_argument("--force", action="store_true", help="replace existing CSV files")
    p.add_argument("--no-markers", action="store_true", help="plain text without '--- page N ---' lines")
    p.add_argument("--map", action="store_true", help="show a map (sections with page ranges and sizes) instead of the text")
    p.add_argument("--all", action="store_true", help=f"print the text even for big files (default: a map above {MAP_PAGES} pages)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"output budget (default {DEFAULT_MAX_CHARS}; 0 = no limit)")
    p.add_argument("-f", "--format", choices=["md", "text", "json", "csv"], help="output format: md (default), json, or csv for --tables (the default for large tables)")
    p.add_argument("--no-cache", action="store_true", help="recompute instead of reusing cached text, words and tables")
    a = p.parse_args()
    if a.format == "text":
        a.format = "md"
    if a.format == "csv" and not a.tables:
        raise UsageError("--format csv only applies to --tables")
    if a.format is None and not a.tables:
        a.format = "md"
    if a.region and not a.words:
        raise UsageError("--region goes with --words (for text in an area, use pdf_render.py --region to look, or --words --region)")
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    path = pdfium_source(pdf_input(a.pdf), a.password)
    SHOWN["pdf"] = a.pdf
    try:
        doc = open_pdfium(path, a.password)
    except SkillError as e:
        if "password" in str(e):
            raise
        return mode_damaged(a, path, e)
    count = len(doc)
    labels = None
    try:
        raw_labels = [doc.get_page_label(i) for i in range(count)] if count <= 5000 else None
        if raw_labels and any(lbl and lbl != str(i + 1) for i, lbl in enumerate(raw_labels)):
            labels = [lbl or str(i + 1) for i, lbl in enumerate(raw_labels)]
    except Exception:  # noqa: BLE001
        labels = None
    doc.close()
    if a.words:
        return mode_words(a, path, count)
    if a.tables:
        return mode_tables(a, path, count)
    if a.search:
        return mode_search(a, path, count, labels)
    return mode_text(a, path, count, labels)


if __name__ == "__main__":
    run_main(main)
