#!/usr/bin/env python3
"""Read any markup document or e-book as Markdown or plain text, with a map first for big files.

Markdown and text are read as they are; HTML, EPUB and notebooks by this skill's converters; everything else
(reStructuredText, LaTeX, Org, AsciiDoc, Typst, Textile, MediaWiki, DocBook, JATS, FB2, RTF, DOCX, ODT, …) through
the bundled pandoc. Small documents print whole. Documents longer than the budget (--max-chars, default 60 000
characters) print a map instead: the outline with a stable address and size per section (1, 2.3, 2.3.1 by
position; EPUB chapters as ch 12). Then read a section, a chapter or a line range, search with addresses, or page
through everything. Every cut output ends with the exact command that continues it. Big files are converted
once and cached, so later calls are instant.

Examples:
  python3 scripts/mk_read.py notes.md
  python3 scripts/mk_read.py manual.html                     # map if big, else the whole text
  python3 scripts/mk_read.py manual.html --section 4.2       # one section (with its subsections)
  python3 scripts/mk_read.py thesis.tex --outline
  python3 scripts/mk_read.py thesis.tex --find "boundary condition" --context 120
  python3 scripts/mk_read.py novel.epub --chapters 3-4 --text
  python3 scripts/mk_read.py page.html --main               # only the article (like html_extract.py)
  python3 scripts/mk_read.py huge.md --all --offset 60000   # page through everything
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, emit, human_size, input_file, parse_ranges, parser, run_main
from _mk import BIG_BYTES, CACHE_VERSION, budget_cut, continuation, detect_reader, find_hits, find_section, outline, pandoc, reader_spec

MD_READERS = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra"}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("file", help="document to read")
    p.add_argument("--outline", action="store_true", help="only the outline: sections with addresses, lines and words")
    p.add_argument("--section", "-s", action="append", help="a section by address (2.3) or heading text; repeatable")
    p.add_argument("--lines", help="a line range of the Markdown, e.g. 120-340")
    p.add_argument("--chapters", "--chapter", dest="chapters", help="EPUB: chapters by number, e.g. 3, 3-5, last")
    p.add_argument("--find", help="search: matches with section address, line and context")
    p.add_argument("--regex", action="store_true", help="--find is a regular expression")
    p.add_argument("--case", action="store_true", help="--find is case-sensitive")
    p.add_argument("--context", type=int, default=80, help="characters of context around each match (default 80)")
    p.add_argument("--max-hits", type=int, default=50, help="matches to show (default 50)")
    p.add_argument("--all", action="store_true", help="the whole text even when big (paged by --max-chars; continue with --offset)")
    p.add_argument("--depth", type=int, help="outline/map: deepest section level to list")
    p.add_argument("--text", action="store_true", help="plain text instead of Markdown")
    p.add_argument("--main", action="store_true", help="HTML: only the main content (drops navigation and boilerplate)")
    p.add_argument("--from", dest="from_format", help="input format when the extension is unusual")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"output budget (default {DEFAULT_MAX_CHARS}; 0 = no limit)")
    p.add_argument("--offset", type=int, default=0, help="continue a cut output from this character")
    p.add_argument("--no-cache", action="store_true", help="convert again instead of reusing the cached conversion")
    add_format(p)
    return p


# ── loading ─────────────────────────────────────────────────────────────


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", "replace")


def convert_to_markdown(path: Path, reader: str, main: bool) -> dict[str, Any]:
    """Markdown for a non-Markdown document, plus notes (title etc.)."""
    if reader == "html":
        from _html import MarkdownWriter, extract_article, load_html

        if main:
            art = extract_article(path)
            head = [f"# {art['title']}"] if art.get("title") else []
            from _html import byline_text

            byline = byline_text(art)
            if byline:
                head.append(f"*{byline}*")
            return {"markdown": "\n\n".join(head + [art["markdown"]]), "title": art.get("title")}
        root, _ = load_html(path)
        body = next(root.iter("body"), root)
        title = next((" ".join("".join(t.itertext()).split()) for t in root.iter("title")), None)
        return {"markdown": MarkdownWriter().convert(body), "title": title}
    if reader == "ipynb":
        import _nb

        nb = _nb.load(path)
        return {"markdown": _nb.to_markdown(nb, limit=2000), "title": None}
    from _inputs import pandoc_inputs, sandbox_warnings

    pre_warnings: list[str] = []
    src = pandoc_inputs([path], reader, pre_warnings)[0]
    from _mk import reader_filter_args

    # --sandbox: pandoc reads nothing but this file (includes inside the folder were inlined by pandoc_inputs).
    out, warnings = pandoc(["--sandbox", str(src.resolve()), *reader_filter_args(reader), "-f", reader_spec(reader, citations=True) if reader != "markdown" else "markdown", "-t", "gfm-raw_html+pipe_tables" if reader not in ("latex",) else "gfm+pipe_tables", "--wrap=none", "--standalone"])
    text = out.decode("utf-8", "replace")
    return {"markdown": text, "title": None, "warnings": pre_warnings + sandbox_warnings(warnings)}


def load(path: Path, a: Any) -> dict[str, Any]:
    """{markdown, sections, reader, cached} for any non-EPUB document; big conversions are cached."""
    reader = detect_reader(path, a.from_format)
    from _inputs import refuse_binary

    refuse_binary(path, reader)
    if reader in MD_READERS:
        text = read_text_file(path)
        if path.stat().st_size >= BIG_BYTES and not a.no_cache:
            from _cache import cached_json, enabled

            secs = cached_json(path, "mk-outline", {}, CACHE_VERSION, lambda: outline(text)) if enabled() else outline(text)
        else:
            secs = outline(text)
        return {"markdown": text, "sections": secs, "reader": reader, "cached": False}
    from _cache import cached_dir, enabled, lookup, release

    # The folder counts (includes resolve there), and the files the document included are checked on every hit.
    params = {"reader": reader, "main": bool(a.main), "folder": str(path.resolve().parent)}
    use_cache = enabled() and not a.no_cache and path.stat().st_size >= 64 * 1024
    if use_cache:
        from _mk import manifest_ok

        hit = lookup(path, "mk-read", params, CACHE_VERSION)
        if hit is not None and not manifest_ok(hit):
            import shutil

            shutil.rmtree(hit, ignore_errors=True)
            hit = None
        if hit is not None:
            import json

            return {"markdown": (hit / "doc.md").read_text(encoding="utf-8"), "sections": json.loads((hit / "outline.json").read_text(encoding="utf-8")), "reader": reader, "cached": True, **json.loads((hit / "info.json").read_text(encoding="utf-8"))}

    def build(d: Path) -> None:
        import json

        from _mk import resources_manifest

        res = convert_to_markdown(path, reader, a.main)
        (d / "doc.md").write_text(res["markdown"], encoding="utf-8")
        (d / "outline.json").write_text(json.dumps(outline(res["markdown"])), encoding="utf-8")
        (d / "info.json").write_text(json.dumps({k: v for k, v in res.items() if k != "markdown"}), encoding="utf-8")
        (d / "resources.json").write_text(json.dumps(resources_manifest("", [d, path.resolve().parent])), encoding="utf-8")

    if use_cache:
        import json

        d = cached_dir(path, "mk-read", params, CACHE_VERSION, build)
        out = {"markdown": (d / "doc.md").read_text(encoding="utf-8"), "sections": json.loads((d / "outline.json").read_text(encoding="utf-8")), "reader": reader, "cached": False, **json.loads((d / "info.json").read_text(encoding="utf-8"))}
        release(d)
        return out
    res = convert_to_markdown(path, reader, a.main)
    return {**res, "sections": outline(res["markdown"]), "reader": reader, "cached": False}


# ── views ───────────────────────────────────────────────────────────────


def outline_table(secs: list[dict[str, Any]], depth: int | None, with_lines: bool = True) -> str:
    rows = [s for s in secs if depth is None or s["level"] <= depth]
    return table_of(rows)


def table_of(rows: list[dict[str, Any]]) -> str:
    lines = ["| § | Heading | Lines | Words |", "|---|---|---|---|"]
    for s in rows:
        indent = "  " * max(0, s["level"] - 1)
        title = s["title"].replace("|", "\\|")[:90]
        lines.append(f"| {s['address']} | {indent}{title} | {s['line']}-{s['end_line']} | {s.get('total_words', s['words']):,} |")
    return "\n".join(lines)


def map_rows(secs: list[dict[str, Any]], max_rows: int) -> tuple[list[dict[str, Any]], int | None, int]:
    """Rows for the map of a big document: every section down to the deepest level that fits in max_rows; when
    the next level is too long to list, runs of its sections are grouped (1.1–1.40) so the map still covers the
    whole document. Returns (rows, depth, group size)."""
    if not secs:
        return [], None, 1
    maxd = max(s["level"] for s in secs)
    d = 0
    for dd in range(1, maxd + 1):
        if sum(1 for s in secs if s["level"] <= dd) <= max_rows:
            d = dd
        else:
            break
    if d == maxd:
        return list(secs), None, 1
    base = sum(1 for s in secs if s["level"] <= d)
    kids = [s for s in secs if s["level"] == d + 1]
    if base >= max_rows * 0.5 or not kids:
        return [s for s in secs if s["level"] <= d], d, 1
    group = max(1, -(-len(kids) // max(10, max_rows - base)))
    rows: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            rows.append(run[0])
        else:
            a, b = run[0], run[-1]
            row = {"address": f"{a['address']}–{b['address']}", "level": a["level"], "title": f"{a['title'][:40]} … {b['title'][:40]}", "words": sum(x.get("total_words", x["words"]) for x in run), "group": len(run)}
            if "line" in a:
                row.update(line=a["line"], end_line=b["end_line"])
            if "chapter" in a:  # EPUB TOC entries
                row.update(t=a["t"], chapter=a["chapter"], end_chapter=b.get("end_chapter", b["chapter"]))
            rows.append(row)
        run.clear()

    for s in secs:
        if s["level"] <= d:
            flush()
            rows.append(s)
        elif s["level"] == d + 1:
            run.append(s)
            if len(run) >= group:
                flush()
    flush()
    return rows, d + 1, group


def cut(text: str, a: Any) -> tuple[str, str | None]:
    part, note, _ = budget_cut(text, a.offset, a.max_chars, "mk_read.py")
    return part, note


def to_plain(md: str) -> str:
    from _html import markdown_to_text

    return markdown_to_text(md)


def doc_view(path: Path, a: Any) -> dict[str, Any]:
    doc = load(path, a)
    md: str = doc["markdown"]
    secs: list[dict[str, Any]] = doc["sections"]
    lines = md.split("\n")
    size = path.stat().st_size
    words = sum(s.get("words", 0) for s in secs) if secs else 0
    head = {"file": str(path), "format": doc["reader"], "bytes": size, "chars": len(md), "words": words, "sections": len(secs), "cached": doc.get("cached", False)}
    if doc.get("warnings"):
        head["warnings"] = doc["warnings"][:10]
    if a.find:
        hits, total = find_hits(md, a.find, secs, a.regex, a.case, a.context, a.max_hits)
        body = [f"{total} match{'es' if total != 1 else ''} for {a.find!r} in {path.name}" + (f" (showing {len(hits)}; --max-hits for more)" if total > len(hits) else "")]
        for h in hits:
            where = f"§{h['section']} " if "section" in h else ""
            body.append(f"- {where}line {h['line']}: {h['context']}")
        if hits:
            first = hits[0]
            body.append("")
            body.append("Read around a match: " + (f"--section {first['section']}" if "section" in first else f"--lines {max(1, first['line'] - 20)}-{first['line'] + 40}"))
        return {**head, "view": "find", "matches": total, "hits": hits, "text": "\n".join(body)}
    if a.outline:
        return {**head, "view": "outline", "outline": secs, "text": f"# {path.name}: outline ({len(secs)} sections, {words:,} words)\n\n" + outline_table(secs, a.depth)}
    if a.section:
        parts = []
        chosen = []
        for spec in a.section:
            s = find_section(secs, spec)
            chosen.append(s)
            parts.append("\n".join(lines[s["line"] - 1 : s["end_line"]]).strip("\n"))
        text = "\n\n".join(parts) + "\n"
        return {**head, "view": "section", "selected": [{k: s[k] for k in ("address", "title", "line", "end_line", "words")} for s in chosen], "text": to_plain(text) if a.text else text}
    if a.lines:
        m = re.fullmatch(r"\s*(\d+)?\s*-\s*(\d+)?\s*|\s*(\d+)\s*", a.lines)
        if not m:
            raise UsageError("--lines takes A-B, A- or -B")
        if m.group(3):
            lo = hi = int(m.group(3))
        else:
            lo = int(m.group(1) or 1)
            hi = int(m.group(2) or len(lines))
        lo, hi = max(1, lo), min(len(lines), hi)
        if lo > hi:
            raise UsageError(f"--lines {a.lines} is outside 1-{len(lines)}")
        text = "\n".join(lines[lo - 1 : hi]) + "\n"
        return {**head, "view": "lines", "lines": [lo, hi], "text": to_plain(text) if a.text else text}
    budget = a.max_chars or 10**12
    if a.all or len(md) <= budget:
        return {**head, "view": "text", "text": to_plain(md) if a.text else md}
    # The map: too big to print whole.
    if a.depth is not None:
        rows, depth, group = [s for s in secs if s["level"] <= a.depth], a.depth, 1
    else:
        rows, depth, group = map_rows(secs, max(20, min(150, budget // 110)))
    listing = f"Listing {len(rows)} rows" + (f" down to level {depth}" if depth else "") + (f", level-{depth} sections in groups of {group} (read one group with --section FIRST, or list them with --outline --depth {depth})" if group > 1 else "") + "."
    intro = [
        f"# {path.name}: map ({human_size(size)} {doc['reader']}, {len(md):,} characters, {words:,} words, {len(secs)} sections)",
        "",
        "Too long to print whole. Read a part: `--section ADDR` (with its subsections) or `--lines A-B`;",
        "search: `--find TEXT`; everything, page by page: `--all`. Words include subsections. " + listing,
        "",
    ]
    return {**head, "view": "map", "depth": depth, "outline": rows, "text": "\n".join(intro) + table_of(rows)}


def epub_view(path: Path, a: Any) -> dict[str, Any]:
    from _epub import cached_book, section_at, section_chapters, section_text, strip_marks

    book = cached_book(path, a.no_cache)
    chs = book["chapters"]
    secs = book.get("sections") or []
    meta = book["meta"]
    words = sum(c["words"] for c in chs)
    head = {"file": str(path), "format": "epub", "epub_version": book["version"], "title": meta.get("title"), "creators": meta.get("creators"), "chapters": len(chs), "toc_entries": len(secs), "words": words, "bytes": path.stat().st_size}
    fine_toc = len(secs) > len(chs) + 2  # the TOC points inside long documents: address its entries (t1, t2, …)
    if a.lines:
        raise UsageError("for an EPUB, read --chapters N or --section tK (see the map); --lines works on other formats")
    finish = (lambda t: to_plain(re.sub(r"(?m)^<!-- .*? -->$\n?", "", t))) if a.text else strip_marks  # TOC markers are internal
    if a.find:
        hits = []
        total = 0
        for c in chs:
            hs, n = find_hits(c["markdown"], a.find, None, a.regex, a.case, a.context, max(0, a.max_hits - len(hits)))
            total += n
            for h in hs:
                t = section_at(secs, c["n"], h["offset"])
                hit = {"chapter": c["n"], "title": c["title"], **h}
                if t:
                    hit["toc"] = f"t{t['t']}"
                    hit["section"] = t["title"]
                hits.append(hit)
        body = [f"{total} match{'es' if total != 1 else ''} for {a.find!r} in {path.name}" + (f" (showing {len(hits)})" if total > len(hits) else "")]
        for h in hits:
            where = f"ch {h['chapter']}" + (f" / {h['toc']} ({h['section'][:40]})" if h.get("toc") else f" ({h['title'][:40]})")
            body.append(f"- {where} line {h['line']}: {h['context']}")
        if hits:
            body += ["", "Read it: " + (f"--section {hits[0]['toc']}" if hits[0].get("toc") and fine_toc else f"--chapters {hits[0]['chapter']}")]
        return {**head, "view": "find", "matches": total, "hits": hits, "text": "\n".join(body)}
    title_line = f"# {meta.get('title') or path.name}" + (f" — {', '.join(meta.get('creators') or [])}" if meta.get("creators") else "")
    if a.section:
        parts = []
        chosen = []
        for spec in a.section:
            sec = pick_toc(secs, spec)
            chosen.append(sec)
            parts.append(f"<!-- t{sec['t']}: {sec['title']} ({section_chapters(sec)}) -->\n\n" + section_text(book, sec).strip())
        return {**head, "view": "section", "selected": [{k: x.get(k) for k in ("t", "title", "chapter", "end_chapter", "words")} for x in chosen], "text": finish("\n\n".join(parts) + "\n")}
    if a.outline:
        return {**head, "view": "outline", "toc": secs, "spine": [{k: c[k] for k in ("n", "title", "words", "path", "linear")} for c in chs], "text": title_line + f"\n\n{len(chs)} documents, {len(secs)} TOC entries, {words:,} words\n\n" + chapter_table(chs, True) + "\n\nTable of contents:\n\n" + toc_table(secs, a.depth)}
    if a.chapters:
        nums = parse_ranges(a.chapters, len(chs))
        parts = []
        for n in nums:
            c = chs[n - 1]
            parts.append(f"<!-- ch {n}: {c['title']} ({c['path']}) -->\n\n" + c["markdown"].strip())
        return {**head, "view": "chapters", "selected": nums, "text": finish("\n\n".join(parts) + "\n")}
    full_len = sum(c["chars"] for c in chs)
    budget = a.max_chars or 10**12
    if a.all or full_len <= budget:
        text = "\n\n".join(f"<!-- ch {c['n']}: {c['title']} -->\n\n" + c["markdown"].strip() for c in chs) + "\n"
        return {**head, "view": "text", "text": title_line + "\n\n" + finish(text)}
    how = "read a TOC entry with `--section tK` (or part of its title; a part includes its chapters), a whole document with `--chapters N`" if fine_toc else "read chapters with `--chapters N` (or 3-5)"
    intro = [title_line, "", f"EPUB {book['version']}, {len(chs)} documents, {len(secs)} TOC entries, {words:,} words, {human_size(path.stat().st_size)}. Too long to print whole: {how}; search with `--find TEXT`; everything page by page with `--all`. Metadata, cover and checks: epub_tool.py.", ""]
    room = max(800, budget - len("\n".join(intro)) - 200)
    note = ""
    if fine_toc:
        rows = [{**x, "address": f"t{x['t']}"} for x in secs if not a.depth or x["level"] <= a.depth]
        table = toc_table(rows, None)
        if len(table) > room and a.depth is None:
            # Too many entries to list: the deepest levels that fit, with runs of the next level grouped (t5–t44),
            # so the map still covers the whole book.
            fit = max(20, int(len(rows) * room / len(table)) - 2)
            full = rows
            for _ in range(5):  # grouped rows are longer than single ones: shrink until the table fits
                rows, depth, group = map_rows(full, fit)
                table = toc_table(rows, None)
                if len(table) <= room or fit <= 20:
                    break
                fit = max(20, int(fit * room / len(table)) - 1)
            note = f"\nListing {len(rows)} of {len(secs)} entries" + (f" down to level {depth}" if depth else "") + (f", level-{depth} entries in groups of {group} (read a group's first entry, or list them with --outline --depth {depth})" if group > 1 else "") + ". Words include sub-entries.\n"
    else:
        rows = chs
        table = chapter_table(chs, False)
        if len(table) > room:
            group = -(-len(table) // room) + 1
            table = chapter_table(chs, False, group)
            note = f"\nSpine documents in groups of {group} (read them with --chapters A-B).\n"
    return {**head, "view": "map", "spine": [{k: c[k] for k in ("n", "title", "words", "path")} for c in chs], **({"toc": secs} if fine_toc else {}), "text": "\n".join(intro) + note + "\n" + table}


def chapter_table(chs: list[dict[str, Any]], with_path: bool, group: int = 1) -> str:
    rows = ["| ch | Title | Words |" + (" File |" if with_path else ""), "|---|---|---|" + ("---|" if with_path else "")]
    if group > 1:
        for k in range(0, len(chs), group):
            run = chs[k : k + group]
            a, b = run[0], run[-1]
            title = a["title"].replace("|", "/")[:40] + (f" … {b['title'].replace('|', '/')[:40]}" if len(run) > 1 else "")
            rows.append(f"| {a['n']}" + (f"-{b['n']}" if len(run) > 1 else "") + f" | {title} | {sum(c['words'] for c in run):,} |")
        return "\n".join(rows)
    for c in chs:
        rows.append(f"| {c['n']} | {c['title'].replace('|', '/')[:80]}{'' if c['linear'] else ' (non-linear)'} | {c['words']:,} |" + (f" {c['path']} |" if with_path else ""))
    return "\n".join(rows)


def toc_table(secs: list[dict[str, Any]], depth: int | None) -> str:
    rows = ["| § | Title | ch | Words |", "|---|---|---|---|"]
    for x in secs:
        if depth and x["level"] > depth:
            continue
        a, b = x["chapter"], x.get("end_chapter", x["chapter"])
        rows.append(f"| {x.get('address') or 't' + str(x['t'])} | {'  ' * (x['level'] - 1)}{x['title'].replace('|', '/')[:80]} | {a if a == b else f'{a}-{b}'} | {x['words']:,} |")
    return "\n".join(rows)


def pick_toc(secs: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    s = spec.strip()
    m = re.fullmatch(r"t?(\d+)", s, re.I)
    if m and s.lower().startswith("t"):
        k = int(m.group(1))
        for x in secs:
            if x["t"] == k:
                return x
        raise UsageError(f"no TOC entry t{k} (1-{len(secs)})")
    low = s.lower()
    exact = [x for x in secs if x["title"].lower() == low]
    part = exact or [x for x in secs if low in x["title"].lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise UsageError(f"no TOC entry matches '{spec}' (see --outline)")
    raise UsageError(f"'{spec}' matches {len(part)} entries: " + "; ".join(f"t{x['t']} {x['title'][:40]}" for x in part[:8]) + " — pass tK")


def main() -> int:
    a = build_parser().parse_args()
    path = input_file(a.file)
    if path.suffix.lower() == ".pdf":
        raise UsageError(f"{path.name} is a PDF: read it with the pdf-toolkit skill (pdf_text.py)")
    is_epub = path.suffix.lower() == ".epub" or a.from_format == "epub"
    if a.chapters and not is_epub:
        raise UsageError("--chapters is for EPUB files; use --section for other documents")
    res = epub_view(path, a) if is_epub else doc_view(path, a)
    text = res.pop("text")
    part, note = cut(text, a)
    if a.format == "json":
        res["text"] = part
        if note:
            res["truncated"] = True
            res["next_offset"] = a.offset + len(part)
            res["next_command"] = continuation("mk_read.py", a.offset + len(part))
        if res.get("view") in ("map", "outline") and "outline" in res:
            pass
        emit(res, "json", max_chars=None)
        return 0
    sys.stdout.write(part if part.endswith("\n") else part + "\n")
    if note:
        print(note)
    for w in res.get("warnings") or []:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    run_main(main)
