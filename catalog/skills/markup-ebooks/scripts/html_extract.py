#!/usr/bin/env python3
"""Extract the readable content of a saved web page: the article as clean Markdown with its title, byline and date,
without navigation, ads, cookie banners, share bars and other boilerplate. Headings, lists, tables, code, figures
and links are kept; images and links get absolute URLs (from --url, <base> or the canonical link).

Modes: the article (default), --all (the whole visible page), --text (plain text), --links, --images, --tables
(Markdown for small tables, CSV for bigger ones; --csv DIR writes one file per table), --meta (metadata only).
Scoring is local (readability-style: text density, commas, link density, class names, semantic tags), so no
network is needed. Big pages are parsed once and cached; --links/--images on huge pages stream the file.

Examples:
  python3 scripts/html_extract.py saved-page.html
  python3 scripts/html_extract.py saved-page.html --url https://example.com/post/42 -o article.md
  python3 scripts/html_extract.py saved-page.html --text
  python3 scripts/html_extract.py saved-page.html --links --all --format json
  python3 scripts/html_extract.py report.html --tables --csv tables/
  python3 scripts/html_extract.py report.html --tables --table 3
  python3 scripts/html_extract.py saved-page.html --meta
"""

from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, emit, human_size, input_file, md_table, output_dir, output_path, parser, run_main
from _mk import budget_cut

STREAM_BYTES = int(float(os.environ.get("DESK_HTML_STREAM_MB", "8")) * 1024 * 1024)  # --all --links/--images stream above this
CACHE_BYTES = 512 * 1024
CACHE_VERSION = "3"
MD_TABLE_ROWS = 40


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("file", help="a saved web page (.html, .htm, .xhtml, .mhtml is not supported)")
    p.add_argument("--url", help="the page's address: relative links and images become absolute")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--links", action="store_true", help="list links (text, URL, internal/external)")
    m.add_argument("--images", action="store_true", help="list images (URL, alt, caption, size; local copies of saved pages)")
    m.add_argument("--tables", action="store_true", help="data tables (Markdown or CSV)")
    m.add_argument("--meta", action="store_true", help="metadata only: title, byline, date, site, language, canonical URL, description")
    p.add_argument("--all", action="store_true", help="the whole page instead of the main content (with --links/--images/--text too)")
    p.add_argument("--text", action="store_true", help="plain text instead of Markdown")
    p.add_argument("--table", type=int, help="with --tables: only table N")
    p.add_argument("--csv", metavar="DIR", help="with --tables: write each table to DIR/table-N.csv")
    p.add_argument("--table-format", choices=["auto", "md", "csv"], default="auto", help=f"with --tables: Markdown up to {MD_TABLE_ROWS} rows, else CSV (auto)")
    p.add_argument("-o", "--out", help="write the result to this file instead of printing it")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"printed output budget (default {DEFAULT_MAX_CHARS}; 0 = no limit)")
    p.add_argument("--offset", type=int, default=0, help="continue a cut output from this character")
    p.add_argument("--no-cache", action="store_true", help="parse again instead of reusing the cached extraction")
    p.add_argument("--force", action="store_true", help="replace --out if it exists")
    add_format(p)
    return p


def _cached(path: Path, kind: str, params: dict[str, Any], a: Any, compute: Any) -> Any:
    from _cache import cached_json, enabled

    if a.no_cache or not enabled() or path.stat().st_size < CACHE_BYTES:
        return compute()
    return cached_json(path, kind, params, CACHE_VERSION, compute)


def article(path: Path, a: Any) -> dict[str, Any]:
    from _html import extract_article

    return _cached(path, "html-article", {"url": a.url}, a, lambda: extract_article(path, a.url))


def whole_page(path: Path, a: Any) -> dict[str, Any]:
    def compute() -> dict[str, Any]:
        from lxml import etree

        from _html import MarkdownWriter, json_ld, load_html, page_metadata

        root, _ = load_html(path, keep_scripts=True)
        ld = json_ld(root)
        etree.strip_elements(root, "script", "style", "noscript", "template", "svg", "canvas", with_tail=False)
        meta = page_metadata(root, ld, a.url)
        meta.pop("_ld_body", None)
        body = next(root.iter("body"), root)
        w = MarkdownWriter(base=meta.get("base"))
        md = w.convert(body)
        import re

        words = len(re.findall(r"\w+", md))
        return {**meta, "method": "all", "words": words, "markdown": md, "images": w.image_refs}

    return _cached(path, "html-all", {"url": a.url}, a, compute)


def header_lines(info: dict[str, Any]) -> list[str]:
    out = []
    if info.get("title"):
        out.append(f"# {info['title']}")
    from _html import byline_text

    line = byline_text(info)
    if line:
        out.append(f"*{line}*")
    if info.get("url"):
        out.append(f"Source: <{info['url']}>")
    return out


def table_text(t: dict[str, Any], fmt: str) -> str:
    use_csv = fmt == "csv" or (fmt == "auto" and t["rows"] > MD_TABLE_ROWS)
    title = f"Table {t['index']}" + (f": {t['caption']}" if t.get("caption") else "") + (f" (under “{t['heading']}”)" if t.get("heading") else "") + f" · {t['rows']} rows × {t['columns']} columns"
    if use_csv:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(t["header"])
        w.writerows(t["data"])
        return f"## {title}\n\n```csv\n{buf.getvalue()}```"
    return f"## {title}\n\n" + md_table(t["header"], t["data"])


def tables(path: Path, a: Any) -> list[dict[str, Any]]:
    def compute() -> list[dict[str, Any]]:
        from _html import collect_tables, head_metadata, load_html

        base = head_metadata(path, a.url).get("base")
        root, _ = load_html(path)
        body = next(root.iter("body"), root)
        return [t for t in collect_tables(body, base) if not t["layout"]]

    return _cached(path, "html-tables", {"url": a.url}, a, compute)


def links_images(path: Path, a: Any, want: str) -> dict[str, Any]:
    from _html import collect_images, collect_links, extract_main, head_metadata, load_html, stream_links_images

    meta = head_metadata(path, a.url)
    base, page_url = meta.get("base"), meta.get("url")
    if a.all and path.stat().st_size >= STREAM_BYTES:
        res = stream_links_images(path, base, page_url, links=want == "links", images=want == "images")
        return {"meta": meta, "items": res[want], "scope": "whole page (streamed)"}
    root, _ = load_html(path)
    scope_el = next(root.iter("body"), root)
    scope = "whole page"
    if not a.all:
        scope_el, _ = extract_main(root, meta.get("title"))
        scope = "main content"
    if want == "links":
        items = collect_links(scope_el, base, page_url)
    else:
        items = collect_images(scope_el, base, path.parent)
    return {"meta": meta, "items": items, "scope": scope}


def render_links(res: dict[str, Any]) -> str:
    items = res["items"]
    kinds: dict[str, int] = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    head = f"{len(items)} links in the {res['scope']} (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ")"
    rows = [[it["text"][:80], it["url"], it["kind"] + (f" ×{it['count']}" if it["count"] > 1 else "")] for it in items]
    return head + "\n\n" + md_table(["Text", "URL", "Kind"], rows)


def render_images(res: dict[str, Any]) -> str:
    items = res["items"]
    rows = []
    for it in items:
        size = f"{it['width']}×{it['height']}" if it.get("width") and it.get("height") else ""
        local = "" if "local" not in it else (it["local"] or "missing")
        rows.append([it["url"], it.get("alt", "")[:60], it.get("caption", "")[:60], size] + ([local] if any("local" in x for x in items) else []))
    hdr = ["URL", "Alt", "Caption", "Size"] + (["Local file"] if any("local" in x for x in items) else [])
    return f"{len(items)} images in the {res['scope']}\n\n" + md_table(hdr, rows)


def main() -> int:
    a = build_parser().parse_args()
    path = input_file(a.file, {".html", ".htm", ".xhtml", ".shtml", ".xht"})
    if path.suffix.lower() == ".mhtml" or path.suffix.lower() == ".mht":
        raise UsageError("MHTML archives are not supported: save the page as 'Web Page, Complete' or 'HTML only'")
    if (a.table or a.csv) and not a.tables:
        raise UsageError("--table and --csv go with --tables")
    data: dict[str, Any]
    if a.meta:
        from _html import head_metadata

        info = article(path, a) if path.stat().st_size < STREAM_BYTES else head_metadata(path, a.url)
        data = {k: v for k, v in info.items() if k not in ("markdown", "images", "diagnostics", "method", "base")}
        data["bytes"] = path.stat().st_size
        emit(data, a.format, lambda d: "\n".join(f"- **{k}**: {v}" for k, v in d.items() if v not in (None, "", [])), max_chars=None)
        return 0
    if a.links or a.images:
        want = "links" if a.links else "images"
        res = links_images(path, a, want)
        if a.format == "json":
            emit({"scope": res["scope"], want: res["items"], "count": len(res["items"])}, "json", max_chars=None if a.out else a.max_chars)
            return 0
        text = render_links(res) if want == "links" else render_images(res)
        return deliver(text, a)
    if a.tables:
        ts = tables(path, a)
        if a.table is not None:
            if not 1 <= a.table <= len(ts):
                raise UsageError(f"--table {a.table}: the page has {len(ts)} data table(s)")
            ts = [ts[a.table - 1]]
        if a.csv:
            folder = output_dir(a.csv)
            written = []
            for t in ts:
                dest = output_path(folder / f"table-{t['index']}.csv", [path], a.force)
                with open(dest, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(t["header"])
                    w.writerows(t["data"])
                written.append({"table": t["index"], "file": str(dest), "rows": t["rows"], "columns": t["columns"], "caption": t.get("caption"), "heading": t.get("heading")})
            emit({"tables": written}, a.format, lambda d: f"wrote {len(d['tables'])} CSV file(s):\n" + "\n".join(f"- {x['file']}: {x['rows']} rows × {x['columns']} columns" + (f" ({x['caption'] or x['heading']})" if x.get("caption") or x.get("heading") else "") for x in d["tables"]), max_chars=None)
            return 0
        if a.format == "json":
            emit({"count": len(ts), "tables": ts}, "json", max_chars=None if a.out else a.max_chars)
            return 0
        if not ts:
            print("no data tables found (layout tables are skipped)")
            return 0
        text = f"{len(ts)} data table(s)\n\n" + "\n\n".join(table_text(t, a.table_format) for t in ts)
        return deliver(text, a)
    info = whole_page(path, a) if a.all else article(path, a)
    md = info["markdown"]
    if a.text:
        from _html import markdown_to_text

        body = markdown_to_text(md)
        text = "\n\n".join(x.lstrip("# ").strip("*") for x in header_lines(info)) + "\n\n" + body
    else:
        text = "\n\n".join(header_lines(info)) + "\n\n" + md
    if a.format == "json":
        out = {k: v for k, v in info.items() if k not in ("markdown",)}
        out["text" if a.text else "markdown"] = text if a.out else budget_cut(text, a.offset, a.max_chars, "html_extract.py")[0]
        if a.out:
            dest = output_path(a.out, [path], a.force)
            dest.write_text(text, encoding="utf-8")
            out["output"] = str(dest)
            out.pop("markdown", None)
            out.pop("text", None)
        emit(out, "json", max_chars=None)
        return 0
    if a.out:
        note = f"{info.get('words', 0):,} words" + (f", method {info.get('method')}" if info.get("method") else "")
        return deliver(text, a, note)
    return deliver(text, a)


def deliver(text: str, a: Any, note: str | None = None) -> int:
    if a.out:
        dest = output_path(a.out, [a.file], a.force)
        dest.write_text(text, encoding="utf-8")
        print(f"wrote {dest} ({human_size(len(text.encode('utf-8')))}{', ' + note if note else ''})")
        return 0
    part, cut_note, _ = budget_cut(text, a.offset, a.max_chars, "html_extract.py")
    sys.stdout.write(part if part.endswith("\n") else part + "\n")
    if cut_note:
        print(cut_note)
    return 0


if __name__ == "__main__":
    run_main(main)
