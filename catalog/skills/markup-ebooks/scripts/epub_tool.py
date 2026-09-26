#!/usr/bin/env python3
"""EPUB e-books: inspect, read, extract, build, check, repair and convert (EPUB 2 and 3).

Commands:
  info     metadata, version, spine, TOC size, cover, sizes by kind, layout, DRM, word count
  toc      the table of contents (nav or NCX) with chapter numbers and words per chapter
  read     chapters as Markdown or text (same as mk_read.py; a map first for big books)
  extract  the cover, every image (with a contact sheet to look at), or all files (safely)
  build    an EPUB 3 from Markdown (or other) chapter files + metadata + cover, through pandoc
  check    structure: mimetype, container, OPF metadata, manifest vs files, spine, nav/NCX, broken links, XHTML
  repack   a copy with the zip layout fixed (mimetype first and stored, junk files removed)
  convert  to Markdown, HTML, PDF (Typst), DOCX, text … (same options as mk_convert.py)

Examples:
  python3 scripts/epub_tool.py info book.epub
  python3 scripts/epub_tool.py toc book.epub
  python3 scripts/epub_tool.py read book.epub --chapters 5
  python3 scripts/epub_tool.py extract book.epub --cover cover.jpg
  python3 scripts/epub_tool.py extract book.epub --images images/ --sheet
  python3 scripts/epub_tool.py build ch1.md ch2.md ch3.md -o book.epub --title "Tides" --author "M. Rao" --cover cover.png --lang en
  python3 scripts/epub_tool.py check book.epub
  python3 scripts/epub_tool.py repack broken.epub fixed.epub
  python3 scripts/epub_tool.py convert book.epub book.pdf --template book --toc
"""

from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, input_file, md_table, output_dir, output_path, parser, run_main

SAFE_TOTAL = 4 * 1024**3


def cmd_info(a: Any) -> int:
    from _epub import Epub

    path = input_file(a.file, {".epub"})
    with Epub(path) as ep:
        md = ep.metadata()
        toc = ep.toc()
        cover = ep.cover()
        groups: dict[str, list[int]] = {}
        infos = {i.filename: i for i in ep.zip.infolist()}
        for it in ep.manifest.values():
            t = it["type"]
            kind = "text" if t in ("application/xhtml+xml", "text/html") else "images" if t.startswith("image/") else "css" if t == "text/css" else "fonts" if "font" in t or t in ("application/vnd.ms-opentype",) else "audio/video" if t.startswith(("audio/", "video/")) else "other"
            info = infos.get(ep.real(it["path"]) or "")
            g = groups.setdefault(kind, [0, 0])
            g[0] += 1
            g[1] += info.file_size if info else 0
        words = 0
        unreadable: list[str] = []
        for d in ep.spine_docs():
            try:
                raw = ep.read(d["path"]).decode("utf-8", "replace")
            except SkillError as e:
                unreadable.append(f"{d['path']}: {str(e).split(': ', 1)[-1]}" if ep.exists(d["path"]) else f"{d['path']}: missing")
                continue
            body = raw.split("<body", 1)[-1]
            body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
            words += len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", re.sub(r"<[^>]+>|&[a-z]+;|&#\d+;", " ", body)))
        encrypted = "META-INF/encryption.xml" in ep.names
        drm = None
        if encrypted:
            data = ep.read("META-INF/encryption.xml", 16 * 1024 * 1024)
            algos = set(re.findall(rb'Algorithm="([^"]+)"', data))
            drm = "fonts obfuscated" if all(b"font" in x.lower() or b"obfuscation" in x.lower() or x.endswith(b"xmldsig#sha1") for x in algos) else "encrypted (DRM)"
        if "META-INF/rights.xml" in ep.names:
            drm = "Adobe DRM"
        cover_info = None
        if cover:
            cover_info = {"path": cover["path"], "type": cover["type"]}
            try:
                data = ep.read(cover["path"])
                cover_info["bytes"] = len(data)
                if cover["type"].startswith("image/") and cover["type"] != "image/svg+xml":
                    import io

                    from PIL import Image

                    with Image.open(io.BytesIO(data)) as im:
                        cover_info["size"] = f"{im.width}×{im.height}"
            except Exception:  # noqa: BLE001
                pass
        res = {
            "file": str(path), "bytes": path.stat().st_size, "version": ep.version, "opf": ep.opf_path, "metadata": md,
            "spine": len(ep.spine), "linear": sum(1 for s in ep.spine if s["linear"]), "toc_entries": len(toc),
            "toc_source": "nav" if any("nav" in it["properties"] for it in ep.manifest.values()) else "ncx" if toc else None,
            "cover": cover_info, "content": {k: {"files": v[0], "bytes": v[1]} for k, v in groups.items()},
            "layout": md.get("layout", "reflowable"), "drm": drm, "words": words, "reading_hours": round(words / 230 / 60, 1),
            "unreadable": unreadable,
        }

    def render(r: dict[str, Any]) -> str:
        m = r["metadata"]
        lines = [f"# {m.get('title') or Path(r['file']).name}" + (f" — {', '.join(m.get('creators', []))}" if m.get("creators") else "")]
        lines.append(f"EPUB {r['version']} · {r['spine']} spine documents · {r['toc_entries']} TOC entries ({r['toc_source'] or 'none'}) · {r['words']:,} words (~{r['reading_hours']} h) · {human_size(r['bytes'])}")
        lines.append("")
        for k in ("language", "publisher", "date", "modified", "series", "series_index", "rights", "source"):
            if m.get(k):
                lines.append(f"- {k}: {m[k]}")
        if m.get("identifiers"):
            lines.append(f"- identifiers: {', '.join(m['identifiers'])}")
        if m.get("contributors"):
            lines.append(f"- contributors: {', '.join(m['contributors'])}")
        if m.get("subjects"):
            lines.append(f"- subjects: {', '.join(m['subjects'][:12])}")
        if m.get("description"):
            d = re.sub(r"<[^>]+>", "", m["description"])
            lines.append(f"- description: {d[:400]}{'…' if len(d) > 400 else ''}")
        c = r["cover"]
        lines.append(f"- cover: {c['path']} ({c.get('size', c['type'])}, {human_size(c.get('bytes', 0))}) — extract with: epub_tool.py extract FILE --cover cover.jpg" if c else "- cover: none")
        lines.append("- content: " + ", ".join(f"{v['files']} {k} ({human_size(v['bytes'])})" for k, v in sorted(r["content"].items(), key=lambda kv: -kv[1]["bytes"])))
        lines.append(f"- layout: {r['layout']}" + (f" · {r['drm']}" if r["drm"] else " · no DRM"))
        if r["unreadable"]:
            lines.append(f"- unreadable: {len(r['unreadable'])} spine document(s), not counted in the words: " + "; ".join(r["unreadable"][:3]) + " (details: epub_tool.py check FILE)")
        return "\n".join(lines)

    emit(res, a.format, render, max_chars=None)
    return 0


def cmd_toc(a: Any) -> int:
    from _epub import cached_book

    path = input_file(a.file, {".epub"})
    book = cached_book(path, a.no_cache)
    chs = book["chapters"]
    by_path = {c["path"]: c for c in chs}
    sec_words = {x["t"]: x["words"] for x in book.get("sections") or []}
    sec_end = {x["t"]: x.get("end_chapter", x["chapter"]) for x in book.get("sections") or []}
    rows = []
    for k, t in enumerate(book["toc"], 1):
        c = by_path.get(t["path"])
        rows.append({"t": k, "level": t["level"], "title": t["title"], "chapter": c["n"] if c else None, "end_chapter": sec_end.get(k), "words": sec_words.get(k), "target": t["path"] + (f"#{t['fragment']}" if t.get("fragment") else "")})
    if not rows:
        rows = [{"t": None, "level": 1, "title": c["title"], "chapter": c["n"], "words": c["words"], "target": c["path"]} for c in chs]

    def render(rs: list[dict[str, Any]]) -> str:
        out = [f"# {book['meta'].get('title') or path.name}: contents ({len(rs)} entries, {len(chs)} chapters)", ""]
        for r in rs:
            if a.depth and r["level"] > a.depth:
                continue
            out.append("  " * (r["level"] - 1) + "- " + (f"t{r['t']} " if r["t"] else "") + r["title"] + (f" · ch {r['chapter']}" + (f"-{r['end_chapter']}" if r.get("end_chapter") and r["end_chapter"] != r["chapter"] else "") if r["chapter"] else "") + (f" · {r['words']:,} words" if r["words"] is not None else ""))
        out += ["", "Read an entry with its sub-entries (a part with all its chapters): python3 scripts/epub_tool.py read FILE --section tK · a whole spine document: --chapters N"]
        return "\n".join(out)

    emit(rows, a.format, render)
    return 0


def cmd_read(a: Any, rest: list[str]) -> int:
    import mk_read

    sys.argv = ["mk_read.py", a.file, *rest]
    return mk_read.main()


def cmd_extract(a: Any) -> int:
    from _epub import Epub

    path = input_file(a.file, {".epub"})
    if not (a.cover or a.images or a.all):
        raise UsageError("say what to extract: --cover FILE, --images DIR or --all DIR")
    done: dict[str, Any] = {}
    with Epub(path) as ep:
        if a.cover:
            cov = ep.cover()
            if cov is None:
                raise SkillError("the book declares no cover image")
            ext = posixpath.splitext(cov["path"])[1].lower() or ".img"
            dest = Path(a.cover)
            if dest.suffix.lower() != ext and dest.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg") and not (dest.suffix.lower() in (".jpg", ".jpeg") and ext in (".jpg", ".jpeg")):
                dest = dest.with_suffix(ext)
            dest = output_path(dest, [path], a.force)
            with open(dest, "wb") as fh:
                n = ep.copy_to(cov["path"], fh)
            done["cover"] = {"file": str(dest), "bytes": n, "source": cov["path"]}
            preview = view_preview(dest)
            if preview:
                done["cover"]["preview"] = str(preview)
        if a.images:
            folder = output_dir(a.images)
            written = []
            for it in ep.manifest.values():
                if not it["type"].startswith("image/") or not ep.exists(it["path"]):
                    continue
                zinfo = ep.zip.getinfo(ep.real(it["path"]) or it["path"])
                if zinfo.file_size < a.min_bytes:
                    continue
                rel = it["path"][len(ep.opf_dir) + 1 :] if ep.opf_dir and it["path"].startswith(ep.opf_dir + "/") else it["path"]
                dest = safe_join(folder, rel)
                if dest.exists() and not a.force:
                    raise SkillError(f"{dest} already exists; pass --force")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as fh:
                    ep.copy_to(it["path"], fh)
                written.append(str(dest))
            done["images"] = {"folder": str(folder), "count": len(written), "files": written}
            if a.sheet and written:
                from _render import contact_sheet

                raster = [w for w in written if Path(w).suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")]
                sheets = []
                for k in range(0, len(raster), 30):
                    batch = raster[k : k + 30]
                    sheets.append(str(contact_sheet(batch, folder / f"_sheet-{k // 30 + 1}.png", labels=[Path(b).name for b in batch], title=f"{path.name}: images {k + 1}-{k + len(batch)} of {len(raster)}")))
                done["images"]["sheets"] = sheets
        if a.all:
            folder = output_dir(a.all)
            total = 0
            count = 0
            skipped = []
            for info in ep.zip.infolist():
                if info.is_dir():
                    continue
                try:
                    dest = safe_join(folder, info.filename)
                except SkillError:
                    skipped.append(info.filename)
                    continue
                total += info.file_size
                if total > SAFE_TOTAL:
                    raise SkillError("the archive expands to more than 4 GB; stopped (a zip bomb?)")
                if dest.exists() and not a.force:
                    raise SkillError(f"{dest} already exists; pass --force")
                dest.parent.mkdir(parents=True, exist_ok=True)
                from _zipsafe import copy_member

                with open(dest, "wb") as out:
                    copy_member(ep.zip, info, out, path.name)
                count += 1
            done["all"] = {"folder": str(folder), "files": count, "bytes": total, "skipped_unsafe": skipped}

    def render(d: dict[str, Any]) -> str:
        out = []
        if "cover" in d:
            out.append(f"cover: {d['cover']['file']} ({human_size(d['cover']['bytes'])}, from {d['cover']['source']})")
        if "images" in d:
            out.append(f"images: {d['images']['count']} file(s) in {d['images']['folder']}")
        if "all" in d:
            out.append(f"all files: {d['all']['files']} ({human_size(d['all']['bytes'])}) in {d['all']['folder']}" + (f"; skipped {len(d['all']['skipped_unsafe'])} unsafe path(s)" if d["all"]["skipped_unsafe"] else ""))
        return "\n".join(out)

    emit(done, a.format, render, max_chars=None)
    if a.format != "json":
        from _render import announce

        views = []
        if "cover" in done:
            views.append(done["cover"].get("preview") or done["cover"]["file"])
        views += done.get("images", {}).get("sheets", [])
        if views:
            announce(views)
    return 0


def view_preview(img: Path) -> Path | None:
    """A vision-sized PNG when the image is too big or not a PNG/JPEG (SVG covers are typeset with Typst)."""
    from _render import VISION_EDGE, fit_edge

    ext = img.suffix.lower()
    if ext == ".svg":
        from _mk import typst_compile_file

        tmp = img.with_name(img.stem + "-preview.typ")
        tmp.write_text(f'#set page(width: auto, height: auto, margin: 0pt)\n#image("{img.name}", width: 600pt)\n', encoding="utf-8")
        try:
            pages, _ = typst_compile_file(tmp, img.parent, fmt="png", ppi=144)
        finally:
            tmp.unlink(missing_ok=True)
        out = img.with_name(img.stem + "-preview.png")
        out.write_bytes(pages[0])
        return out
    try:
        from PIL import Image

        with Image.open(img) as im:
            if max(im.size) <= VISION_EDGE and ext in (".png", ".jpg", ".jpeg"):
                return None
            small = fit_edge(im.convert("RGB"))
            out = img.with_name(img.stem + "-preview.png")
            small.save(out)
            return out
    except Exception:  # noqa: BLE001
        return None


def safe_join(folder: Path, name: str) -> Path:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or re.match(r"^[A-Za-z]:", parts[0]):
        raise SkillError(f"unsafe path in the archive: {name}")
    dest = folder.joinpath(*parts)
    if not dest.resolve().is_relative_to(folder.resolve()):
        raise SkillError(f"unsafe path in the archive: {name}")
    return dest


def cmd_check(a: Any) -> int:
    from _epub import check_epub

    path = input_file(a.file, {".epub"})
    rep = check_epub(path, deep=not a.quick)
    probs = rep["problems"]
    counts = {k: sum(1 for p in probs if p["level"] == k) for k in ("error", "warning", "info")}

    def render(r: dict[str, Any]) -> str:
        st = r.get("stats") or {}
        out = [f"{path.name}: " + (", ".join(f"{v} {k}{'s' if v != 1 else ''}" for k, v in counts.items() if v) or "no problems") + (f" · {st.get('documents', 0)} documents, {st.get('images', 0)} images, {st.get('spine', 0)} spine items, {st.get('toc_entries', 0)} TOC entries" if st else "")]
        for p in r["problems"][: a.limit]:
            out.append(f"- {p['level']} · {p['where']}: {p['message']}")
        if len(r["problems"]) > a.limit:
            out.append(f"- … {len(r['problems']) - a.limit} more (--limit or --format json)")
        if any("repack" in p["message"] for p in r["problems"]):
            out.append("\nZip-level problems are fixed by: python3 scripts/epub_tool.py repack IN.epub OUT.epub")
        return "\n".join(out)

    emit({**rep, "counts": counts}, a.format, render, max_chars=None)
    return 1 if a.strict and counts["error"] else 0


def cmd_repack(a: Any) -> int:
    from _epub import check_epub, repack

    src = input_file(a.file, {".epub"})
    dest = output_path(a.output, [src], a.force)
    res = repack(src, dest)
    rep = check_epub(dest, deep=False)
    res["remaining_errors"] = [p for p in rep["problems"] if p["level"] == "error"]
    emit(res, a.format, lambda r: f"wrote {r['output']}: " + ("; ".join(r["fixed"]) or "layout was already correct") + (f"\nstill wrong (not a zip-level problem): " + "; ".join(f"{p['where']}: {p['message']}" for p in r["remaining_errors"][:8]) if r["remaining_errors"] else ""), max_chars=None)
    return 0


def run_mk_convert(argv: list[str]) -> int:
    import mk_convert

    sys.argv = ["mk_convert.py", *argv]
    return mk_convert.main()


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    s = sub.add_parser("info", help="metadata, structure, cover, sizes, words")
    s.add_argument("file")
    add_format(s)
    s = sub.add_parser("toc", help="table of contents with chapter numbers")
    s.add_argument("file")
    s.add_argument("--depth", type=int, help="deepest TOC level to list")
    s.add_argument("--no-cache", action="store_true")
    add_format(s)
    s = sub.add_parser("read", help="chapters as Markdown/text (see mk_read.py --help)", add_help=False)
    s.add_argument("file")
    s = sub.add_parser("extract", help="cover, images or all files")
    s.add_argument("file")
    s.add_argument("--cover", metavar="FILE", help="write the cover image here (the extension follows the image)")
    s.add_argument("--images", metavar="DIR", help="write every image into DIR")
    s.add_argument("--sheet", action="store_true", help="with --images: contact sheets of the images to look at")
    s.add_argument("--min-bytes", type=int, default=0, help="with --images: skip images smaller than this")
    s.add_argument("--all", metavar="DIR", help="unzip every file into DIR (unsafe paths are skipped)")
    s.add_argument("--force", action="store_true")
    add_format(s)
    s = sub.add_parser("build", help="EPUB 3 from chapter files (pandoc); options as mk_convert.py", add_help=False)
    s = sub.add_parser("check", help="structural validation")
    s.add_argument("file")
    s.add_argument("--quick", action="store_true", help="skip parsing every XHTML document and link")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--strict", action="store_true", help="exit 1 when there are errors")
    add_format(s)
    s = sub.add_parser("repack", help="fix the zip layout into a new file")
    s.add_argument("file")
    s.add_argument("output")
    s.add_argument("--force", action="store_true")
    add_format(s)
    sub.add_parser("convert", help="to md/html/pdf/docx/txt… (options as mk_convert.py)", add_help=False)
    argv = sys.argv[1:]
    if argv and argv[0] in ("build", "convert"):
        rest = argv[1:]
        if not rest or rest[0] in ("-h", "--help"):
            print(f"usage: epub_tool.py {argv[0]} INPUT… OUTPUT [mk_convert.py options]\n")
            if argv[0] == "build":
                print("Builds an EPUB 3 with pandoc: chapters from the inputs (each level-1 heading starts a chapter; --split-level),\n"
                      "a navigation TOC, Desk's e-book stylesheet (--css to replace), MathML, --cover IMG, --title, --author,\n"
                      "--lang, --date, --metadata publisher=… rights=… identifier=…, --toc for a visible contents page.\n"
                      "The result is checked with epub_tool.py check.\n\n"
                      "  python3 scripts/epub_tool.py build ch1.md ch2.md -o book.epub --title \"Tides\" --author \"M. Rao\" --cover cover.png\n")
            else:
                print("Converts an EPUB with mk_convert.py (pandoc; PDF through Typst):\n\n"
                      "  python3 scripts/epub_tool.py convert book.epub book.md        # images to book_media/\n"
                      "  python3 scripts/epub_tool.py convert book.epub book.pdf --template book --toc\n"
                      "  python3 scripts/epub_tool.py convert book.epub book.docx\n")
            return 0
        if argv[0] == "build":
            if not any(x in ("-o", "--out") or x.startswith(("--out=", "-o")) for x in rest) and not rest[-1].lower().endswith(".epub"):
                raise UsageError("give the output: -o book.epub")
            if any(x.startswith("--to") for x in rest):
                raise UsageError("build always writes EPUB 3; use convert or mk_convert.py for other formats")
            return run_mk_convert(rest + (["--to", "epub3"] if not any(x.endswith(".epub") for x in rest) else []))
        return run_mk_convert(rest)
    if argv and argv[0] == "read":
        if len(argv) < 2 or argv[1] in ("-h", "--help"):
            import mk_read

            mk_read.build_parser().print_help()
            return 0
        a = argparse_ns(file=argv[1])
        return cmd_read(a, argv[2:])
    a = p.parse_args()
    return {"info": cmd_info, "toc": cmd_toc, "extract": cmd_extract, "check": cmd_check, "repack": cmd_repack}[a.cmd](a)


def argparse_ns(**kw: Any) -> Any:
    import argparse

    return argparse.Namespace(**kw)


if __name__ == "__main__":
    run_main(main)
