#!/usr/bin/env python3
"""Summarise a presentation: size, slides, layouts, theme, fonts, media, notes, macros."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import add_format, emit, human_size, md_table, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/pptx_info.py deck.pptx
  python3 scripts/pptx_info.py template.potx --format json

Start here with an unfamiliar deck: it names the layouts (for pptx_create --template and pptx_edit add_slide), the
theme colours and fonts, and which slides have tables, charts, pictures, notes or are hidden.
"""


INFO_VERSION = "2"


def main() -> int:
    ap = parser("Overview of a .pptx/.pptm/.potx/.ppsx (or .ppt/.odp via LibreOffice).", EPILOG)
    ap.add_argument("file")
    ap.add_argument("--max-chars", type=int, default=60000, help="cap on Markdown output (default 60000; 0 = no cap)")
    ap.add_argument("--no-cache", action="store_true", help="do not use or fill the file cache")
    add_format(ap)
    a = ap.parse_args()
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    from _cache import cached_json

    from _deck import ooxml_source, open_deck

    shown = Path(a.file)
    path = ooxml_source(shown)
    info = cached_json(path, "pptx-info", {}, INFO_VERSION, lambda: deck_info(open_deck(path, checked=True).prs, path))
    st = shown.stat()
    info.update({"file": str(shown), "bytes": st.st_size, "size": human_size(st.st_size), "format": shown.suffix.lower().lstrip(".") or "pptx"})
    if path != shown:
        info["converted_with"] = "LibreOffice"
    cmd = f"python3 scripts/pptx_read.py {a.file}"
    emit(info, a.format, render_md, max_chars=a.max_chars or None, hint=f"Page through the slides with: {cmd} --map --slides 101-200")
    return 0


KIND_LABELS = {"picture": "pictures", "table": "tables", "chart": "charts", "smartart": "SmartArt", "media": "video/audio", "ole": "embedded objects", "group": "groups"}


def deck_info(prs: Any, path: Path) -> dict[str, Any]:
    from _deck import RT_VBA, is_hidden, notes_text, shape_kind, slide_title

    from _ooxml import RT_THEME, Theme, emu_in, part_xml, related

    w, h = prs.slide_width or 9144000, prs.slide_height or 6858000
    ratio = w / h if h else 0
    aspect = next((name for name, r in (("16:9", 16 / 9), ("4:3", 4 / 3), ("16:10", 1.6), ("A4", 297 / 210), ("Letter", 11 / 8.5)) if abs(ratio - r) < 0.02), f"{ratio:.2f}:1")
    cp = prs.core_properties
    layout_use: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    fonts: Counter[str] = Counter()
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        kinds: Counter[str] = Counter()
        words = 0

        def visit(shapes: Any) -> None:
            nonlocal words
            for sh in shapes:
                k = shape_kind(sh)
                kinds[k] += 1
                if sh.has_text_frame:
                    words += len(sh.text_frame.text.split())
                if k == "group":
                    visit(sh.shapes)

        visit(slide.shapes)
        for k, v in kinds.items():
            totals[k] += v
        layout_use[slide.slide_layout.name] += 1
        for lat in slide._element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}latin"):
            tf = lat.get("typeface")
            if tf:
                fonts[tf] += 1
        n = notes_text(slide)
        slides.append({
            "number": i,
            "title": slide_title(slide),
            "layout": slide.slide_layout.name,
            "shapes": sum(kinds.values()),
            "words": words,
            "has_notes": bool(n),
            "hidden": is_hidden(slide),
            **{KIND_LABELS[k]: v for k, v in kinds.items() if k in KIND_LABELS},
        })
    masters = []
    theme_info: dict[str, Any] = {}
    for mi, m in enumerate(prs.slide_masters):
        tp = related(m.part, RT_THEME)
        th = Theme(part_xml(tp)) if tp is not None else Theme(None)
        if mi == 0:
            theme_info = {"name": th.name, "heading_font": th.major, "body_font": th.minor, "colors": {k: "#" + v for k, v in th.colors.items()}}
        masters.append({"number": mi + 1, "theme": th.name, "layouts": [{"name": lay.name, "used": layout_use.get(lay.name, 0), "placeholders": len(lay.placeholders)} for lay in m.slide_layouts]})
    resolved_fonts: Counter[str] = Counter()
    for f, c in fonts.items():
        if f.startswith("+mj"):
            resolved_fonts[theme_info.get("heading_font") or f] += c
        elif f.startswith("+mn"):
            resolved_fonts[theme_info.get("body_font") or f] += c
        else:
            resolved_fonts[f] += c
    has_macros = any(rel.reltype == RT_VBA for rel in prs.part.rels.values())
    embedded_fonts = prs.part._element.find("{*}embeddedFontLst") is not None
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "size": human_size(path.stat().st_size),
        "format": path.suffix.lower().lstrip(".") or "pptx",
        "slide_count": len(slides),
        "hidden_slides": [s["number"] for s in slides if s["hidden"]],
        "slide_size_in": [emu_in(w), emu_in(h)],
        "aspect": aspect,
        "properties": {k: v for k, v in {
            "title": cp.title, "subject": cp.subject, "author": cp.author, "keywords": cp.keywords,
            "last_modified_by": cp.last_modified_by, "created": cp.created, "modified": cp.modified, "revision": cp.revision,
        }.items() if v},
        "theme": theme_info,
        "masters": masters,
        "fonts_used": dict(resolved_fonts.most_common()),
        "content": {KIND_LABELS.get(k, k): v for k, v in totals.items()},
        "slides_with_notes": sum(1 for s in slides if s["has_notes"]),
        "macros": has_macros,
        "embedded_fonts": embedded_fonts,
        "slides": slides,
    }


def render_md(d: dict[str, Any]) -> str:
    L = [f"# {Path(d['file']).name}"]
    L.append(f"- {d['format']} file, {d['size']}; {d['slide_count']} slides ({d['aspect']}, {d['slide_size_in'][0]} × {d['slide_size_in'][1]} in)")
    if d["hidden_slides"]:
        L.append(f"- Hidden slides: {', '.join(map(str, d['hidden_slides']))}")
    if d.get("converted_with"):
        L.append(f"- Read through a {d['converted_with']} conversion")
    props = d["properties"]
    if props:
        L.append("- Properties: " + "; ".join(f"{k} {v}" for k, v in props.items()))
    th = d["theme"]
    if th:
        cols = ", ".join(f"{k} {v}" for k, v in th["colors"].items())
        L.append(f"- Theme \"{th['name']}\": headings {th['heading_font']}, body {th['body_font']}; colours {cols}")
    if d["fonts_used"]:
        L.append("- Fonts set on runs: " + ", ".join(f"{k} ({v})" for k, v in list(d["fonts_used"].items())[:12]))
    content = ", ".join(f"{v} {k}" for k, v in d["content"].items() if k in KIND_LABELS.values())
    L.append(f"- Content: {content or 'text only'}; {d['slides_with_notes']} slides with speaker notes")
    if d["macros"]:
        L.append("- Contains VBA macros")
    if d["embedded_fonts"]:
        L.append("- Has embedded fonts")
    for m in d["masters"]:
        lay = ", ".join(f"{x['name']}" + (f" (used {x['used']}×)" if x["used"] else "") for x in m["layouts"])
        L.append(f"- Master {m['number']} layouts: {lay}")
    if d["slides"]:
        L.append("")
        rows = []
        for s in d["slides"]:
            extra = ", ".join(f"{s[k]} {k}" for k in KIND_LABELS.values() if s.get(k))
            flags = " ".join(x for x in ("hidden" if s["hidden"] else "", "notes" if s["has_notes"] else "") if x)
            rows.append([s["number"], s["title"][:70], s["layout"], s["shapes"], s["words"], extra, flags])
        L.append(md_table(["#", "Title", "Layout", "Shapes", "Words", "Objects", "Flags"], rows))
    return "\n".join(L)


if __name__ == "__main__":
    run_main(main)
