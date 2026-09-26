#!/usr/bin/env python3
"""Overview of a Word document: pages, words, sections and page setup, styles, headers and footers, comments,
tracked changes, footnotes, images, tables, fields, properties, protection and macros.

The page count comes from the file's own statistics (as last saved by Word) and a layout estimate. --exact renders
the document to count pages: exactly with LibreOffice when installed, otherwise with the built-in renderer
(approximate).

Examples:
  python3 scripts/docx_info.py report.docx
  python3 scripts/docx_info.py report.docx --exact
  python3 scripts/docx_info.py contract.docm --format json
"""

from __future__ import annotations

import math
import re
from collections import Counter as Tally
from pathlib import Path
from typing import Any

from _common import add_format, emit, human_size, parser, run_main

PAPER = {
    (595, 842): "A4", (612, 792): "Letter", (612, 1008): "Legal", (420, 595): "A5", (842, 1191): "A3",
    (522, 756): "Executive", (499, 709): "B5 (JIS)", (516, 729): "B5", (792, 1224): "Tabloid",
}


def paper_name(w: float, h: float) -> str:
    a, b = sorted((round(w), round(h)))
    for (pw, ph), name in PAPER.items():
        if abs(pw - a) <= 3 and abs(ph - b) <= 3:
            return name
    return "custom"


def main() -> int:
    ap = parser(__doc__.split("\n\n")[0], __doc__[__doc__.index("The page count") :])
    ap.add_argument("input", help=".docx/.docm/.dotx/.dotm (or .odt/.rtf/.doc)")
    ap.add_argument("--exact", action="store_true", help="render to count pages (LibreOffice when installed, else the built-in renderer)")
    ap.add_argument("--headings", type=int, default=25, help="how many headings to list (default 25)")
    ap.add_argument("--no-cache", action="store_true", help="parse (and with --exact, lay out) again instead of using cached results")
    add_format(ap)
    args = ap.parse_args()
    if args.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"

    from _cache import cached_json
    from _common import input_file
    from _docx import code_version, converter_key, load

    src = input_file(args.input)
    # The overview is cached by the file's content: asking again (or after a render) costs no parsing.
    info = cached_json(src, "docx-info", {"headings": args.headings, **converter_key(src)}, code_version("docx_info.py", "_docx.py", "_styles.py", "_numbering.py", "_reader.py"), lambda: collect(load(src), args.headings))
    info["file"] = str(args.input)
    if args.exact:
        info["pages"]["rendered"], info["pages"]["engine"] = count_pages(src)
    emit(info, args.format, render)
    return 0


def collect(doc: Any, max_headings: int = 25) -> dict[str, Any]:
    from _docx import DEL, INS, MOVEFROM, MOVETO, NS, OMATH, P, RT_VBA, SDT, TBL, TXBX, app_properties, core_properties, custom_properties, field_type, fields_in, heading_level, iter_blocks, iter_paragraphs, para_text, qn, style_id, table_grid, table_style_id, twips, wattr

    w = "{" + NS["w"] + "}"
    body = doc.body
    styles = doc.styles
    path: Path = doc.path
    info: dict[str, Any] = {"file": str(path), "size": human_size(path.stat().st_size), "format": doc.kind}
    if doc.note:
        info["note"] = doc.note

    # text statistics
    words = chars = 0
    para_count = 0
    style_use: Tally[str] = Tally()
    char_style_use: Tally[str] = Tally()
    headings: list[dict[str, Any]] = []
    blocks = list(iter_blocks(body))
    for i, el in enumerate(blocks):
        if el.tag == P:
            t = para_text(el)
            if t.strip():
                para_count += 1
            style_use[styles.name(style_id(el))] += 1
            lvl = heading_level(el, styles)
            if (lvl or styles.is_title(style_id(el))) and t.strip():
                headings.append({"index": i, "level": lvl or 0, "text": t.strip()[:120]})
    all_paras = list(iter_paragraphs(body))
    for p in all_paras:
        t = para_text(p)
        words += len(re.findall(r"\w+(?:[-'’]\w+)*", t))
        chars += len(t)
    for rs in body.iter(qn("w:rStyle")):
        char_style_use[styles.name(wattr(rs, "val"), "character")] += 1

    tables = []
    for i, el in enumerate(blocks):
        if el.tag == TBL:
            grid = table_grid(el)
            ncols = max((sum(c["colspan"] for c in r) + (r[0]["col"] if r else 0) for r in grid), default=0)
            merged = any(c["colspan"] > 1 or c["rowspan"] > 1 for r in grid for c in r)
            sid = table_style_id(el)
            tables.append({"index": i, "rows": len(grid), "cols": ncols, "merged_cells": merged, "style": styles.name(sid, "table") if sid else ""})
    nested_tables = sum(1 for t in body.iter(TBL)) - len(tables)

    # objects
    a = "{" + NS["a"] + "}"
    wp = "{" + NS["wp"] + "}"
    inline_imgs = sum(1 for x in body.iter(wp + "inline") if x.find(f".//{a}blip") is not None)
    floating_imgs = sum(1 for x in body.iter(wp + "anchor") if x.find(f".//{a}blip") is not None and not _in_fallback(x))
    vml_imgs = sum(1 for x in body.iter("{" + NS["v"] + "}imagedata") if not _in_fallback(x))
    charts = sum(1 for _ in body.iter("{" + NS["c"] + "}chart"))
    diagrams = sum(1 for _ in body.iter("{" + NS["dgm"] + "}relIds"))
    textboxes = sum(1 for x in body.iter(TXBX) if not _in_fallback(x))
    equations = sum(1 for _ in body.iter(OMATH))
    ole = sum(1 for _ in body.iter(qn("w:object")))
    hyperlinks = sum(1 for _ in body.iter(qn("w:hyperlink")))
    bookmarks = sum(1 for b in body.iter(qn("w:bookmarkStart")) if not (wattr(b, "name") or "").startswith("_"))
    sdts = sum(1 for _ in body.iter(SDT))
    alt_chunks = sum(1 for _ in body.iter(qn("w:altChunk")))

    fields = Tally(field_type(f) for f in fields_in(body))
    hf_fields: Tally[str] = Tally()
    for hf in doc.header_footer_parts():
        hf_fields.update(field_type(f) for f in fields_in(doc.xml(hf["part"])))
    hyperlinks += fields.get("HYPERLINK", 0)

    # tracked changes
    changes: dict[str, Any] = {}
    authors: Tally[str] = Tally()
    roots = [body] + [doc.xml(hf["part"]) for hf in doc.header_footer_parts()]
    for root in [r for r in (doc.footnotes, doc.endnotes) if r is not None]:
        roots.append(root)
    for label, tags in (("insertions", (INS,)), ("deletions", (DEL,)), ("moves", (MOVEFROM, MOVETO)), ("formatting", (qn("w:rPrChange"), qn("w:pPrChange"), qn("w:tblPrChange"), qn("w:tcPrChange"), qn("w:sectPrChange")))):
        n = 0
        for root in roots:
            for el in root.iter(*tags):
                # w:ins/w:del inside rPr mark paragraph-mark changes; count them too.
                n += 1
                authors[wattr(el, "author") or "?"] += 1
        if n:
            changes[label] = n
    if changes:
        changes["authors"] = dict(authors.most_common())

    # notes and comments
    def real_notes(root: Any, tag: str) -> int:
        if root is None:
            return 0
        return sum(1 for n in root.findall(qn(tag)) if wattr(n, "type") in (None, "normal"))

    footnotes = real_notes(doc.footnotes, "w:footnote")
    endnotes = real_notes(doc.endnotes, "w:endnote")
    comments: dict[str, Any] = {}
    croot = doc.comments
    if croot is not None:
        cs = croot.findall(qn("w:comment"))
        if cs:
            comments = {"count": len(cs), "authors": dict(Tally(wattr(c, "author") or "?" for c in cs).most_common())}

    # sections
    sections = []
    for sect in doc.section_elements():
        pg = sect.find(qn("w:pgSz"))
        mar = sect.find(qn("w:pgMar"))
        wpt, hpt = twips(wattr(pg, "w"), 612), twips(wattr(pg, "h"), 792)
        orient = wattr(pg, "orient") or ("landscape" if wpt > hpt else "portrait")
        s: dict[str, Any] = {
            "paper": paper_name(wpt, hpt),
            "size_cm": [round(wpt / 72 * 2.54, 2), round(hpt / 72 * 2.54, 2)],
            "orientation": orient,
            "margins_cm": {k: round(twips(wattr(mar, k), 72) / 72 * 2.54, 2) for k in ("top", "right", "bottom", "left")} if mar is not None else None,
            "page_size_set": pg is not None,
        }
        t = sect.find(qn("w:type"))
        s["start"] = wattr(t, "val", "nextPage") if t is not None else "nextPage"
        cols = sect.find(qn("w:cols"))
        if cols is not None and int(wattr(cols, "num", "1") or 1) > 1:
            s["columns"] = int(wattr(cols, "num", "1") or 1)
        if sect.find(qn("w:titlePg")) is not None and wattr(sect.find(qn("w:titlePg")), "val") not in ("0", "false"):
            s["different_first_page"] = True
        refs = [f"{'header' if r.tag == qn('w:headerReference') else 'footer'} {wattr(r, 'type', 'default')}" for r in sect if r.tag in (qn("w:headerReference"), qn("w:footerReference"))]
        if refs:
            s["headers_footers"] = refs
        pn = sect.find(qn("w:pgNumType"))
        if pn is not None and (wattr(pn, "start") or wattr(pn, "fmt")):
            s["page_numbering"] = {k: v for k, v in (("start", wattr(pn, "start")), ("format", wattr(pn, "fmt"))) if v}
        sections.append(s)

    # headers and footers text
    from _reader import Reader

    rd = Reader(doc, comments="none", headers=True, plain=True)
    heads, foots = rd._headers_footers()

    # settings
    settings = doc.settings
    protection: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    compat = None
    template = None
    if settings is not None:
        dp = settings.find(qn("w:documentProtection"))
        if dp is not None:
            protection["edit"] = wattr(dp, "edit") or "none"
            protection["enforced"] = wattr(dp, "enforcement") in ("1", "true", "on")
            protection["password"] = bool(wattr(dp, "hashValue") or wattr(dp, "hash") or wattr(dp, "cryptProviderType"))
        wpr = settings.find(qn("w:writeProtection"))
        if wpr is not None:
            protection["write_protection"] = True
        if settings.find(qn("w:trackRevisions")) is not None and wattr(settings.find(qn("w:trackRevisions")), "val") not in ("0", "false"):
            flags["track_changes_on"] = True
        if settings.find(qn("w:evenAndOddHeaders")) is not None:
            flags["even_odd_headers"] = True
        if settings.find(qn("w:updateFields")) is not None:
            flags["update_fields_on_open"] = True
        for cs in settings.iter(qn("w:compatSetting")):
            if wattr(cs, "name") == "compatibilityMode":
                compat = {"15": "Word 2013+", "14": "Word 2010", "12": "Word 2007", "11": "Word 2003"}.get(wattr(cs, "val") or "", wattr(cs, "val"))
        at = settings.find(qn("w:attachedTemplate"))
        if at is not None:
            template = doc.rels_target(at.get(qn("r:id")), doc.rel_part("http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"))[0]

    macros = None
    for rel in doc.part.rels.values():
        if rel.reltype == RT_VBA and not rel.is_external:
            macros = {"vba_project_bytes": len(rel.target_part.blob)}
    signed = any("digital-signature" in r.reltype for r in doc.package.rels.values())
    font_table = doc.rel_root("http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable")
    embedded_fonts = []
    if font_table is not None:
        for f in font_table.findall(qn("w:font")):
            if any(f.find(qn(t)) is not None for t in ("w:embedRegular", "w:embedBold", "w:embedItalic", "w:embedBoldItalic")):
                embedded_fonts.append(wattr(f, "name"))
    hidden_text = any(wattr(v, "val") not in ("0", "false") for v in body.iter(qn("w:vanish")))
    external = [r.target_ref for r in doc.part.rels.values() if r.is_external and r.reltype != "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"]

    app = app_properties(doc)
    est = estimate_pages(doc, blocks)
    pages: dict[str, Any] = {"estimate": est}
    if app.get("Pages", "").isdigit() and doc.note is None:
        pages["saved"] = int(app["Pages"])
        # Files written by tools (and templates filled later) keep statistics that no longer match the text.
        saved_words = int(app["Words"]) if app.get("Words", "").isdigit() else None
        if saved_words is not None and abs(saved_words - words) > max(50, 0.3 * max(words, saved_words)):
            pages["saved_stale"] = True
    info.update(
        {
            "pages": pages,
            "words": words,
            "characters": chars,
            "paragraphs": para_count,
            "blocks": len(blocks),
            "sections": sections,
            "headings": {"count": len(headings), "by_level": dict(sorted(Tally(h["level"] for h in headings).items())), "first": headings[:max_headings]},
            "styles_used": dict(style_use.most_common()),
            "character_styles_used": dict(char_style_use.most_common()),
            "tables": tables,
            "images": {"inline": inline_imgs, "floating": floating_imgs, "vml": vml_imgs} if (inline_imgs or floating_imgs or vml_imgs) else {},
            "objects": {k: v for k, v in (("charts", charts), ("diagrams", diagrams), ("text_boxes", textboxes), ("equations", equations), ("embedded_objects", ole), ("content_controls", sdts), ("nested_tables", nested_tables), ("alt_chunks", alt_chunks)) if v},
            "hyperlinks": hyperlinks,
            "bookmarks": bookmarks,
            "fields": dict(fields.most_common()),
            "header_footer_fields": dict(hf_fields.most_common()),
            "headers": [{"type": h["type"], "sections": h["sections"], "text": h["text"]} for h in heads],
            "footers": [{"type": f["type"], "sections": f["sections"], "text": f["text"]} for f in foots],
            "footnotes": footnotes,
            "endnotes": endnotes,
            "comments": comments,
            "tracked_changes": changes,
            "properties": core_properties(doc),
            "custom_properties": custom_properties(doc),
            "application": {k: app[k] for k in ("Application", "AppVersion", "Company", "Template", "TotalTime", "Words", "Pages") if k in app},
            "settings": flags,
            "compatibility": compat,
            "attached_template": template,
            "protection": protection,
            "macros": macros,
            "digitally_signed": signed,
            "embedded_fonts": embedded_fonts,
            "hidden_text": hidden_text,
            "external_links": external[:20],
        }
    )
    return info


def _in_fallback(el: Any) -> bool:
    from _docx import in_fallback

    return in_fallback(el)


def estimate_pages(doc: Any, blocks: list[Any]) -> int:
    """A layout estimate from text length, font sizes, spacing and page geometry (no rendering)."""
    from _docx import BR, P, in_fallback, iter_blocks, para_text, qn, style_id, twips, units, wattr
    from _styles import merge

    styles = doc.styles
    sect = doc.body.find(qn("w:sectPr"))
    pg = sect.find(qn("w:pgSz")) if sect is not None else None
    mar = sect.find(qn("w:pgMar")) if sect is not None else None
    width = twips(wattr(pg, "w"), 612) - twips(wattr(mar, "left"), 72) - twips(wattr(mar, "right"), 72)
    height = twips(wattr(pg, "h"), 792) - twips(wattr(mar, "top"), 72) - twips(wattr(mar, "bottom"), 72)
    width, height = max(width, 100), max(height, 100)

    def para_height(p: Any, avail: float) -> float:
        sid = style_id(p)
        rpr = merge(styles.doc_rpr, styles.para_style_rpr(sid))
        ppr = merge(styles.doc_ppr, styles.para_style_ppr(sid))
        size = max(1.0, units((rpr.get("sz") or {}).get("val"), 2, 22.0) / 2)
        sp = ppr.get("spacing") or {}
        line = sp.get("line")
        rule = sp.get("lineRule", "auto")
        lh = size * 1.2 * (units(line, 20, 240.0) / 240 if line and rule == "auto" else 1.0)
        if line and rule in ("exact", "atLeast"):
            lh = max(lh, twips(line)) if rule == "atLeast" else twips(line)
        text = para_text(p)
        chars_per_line = max(10, avail / (size * 0.5))
        lines = sum(max(1, math.ceil(len(seg) / chars_per_line)) for seg in text.split("\n")) if text.strip() else 1
        extra = 0.0
        # Inline pictures take room in the line; floating ones (wp:anchor) do not. mc:Fallback copies are skipped.
        for inl in p.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline"):
            ext = inl.find("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
            if ext is None or in_fallback(inl):
                continue
            try:
                extra += int(ext.get("cy")) / 12700
            except (TypeError, ValueError):
                pass
        return lines * lh + twips(sp.get("before")) + twips(sp.get("after"), 0) + extra

    total = 0.0
    pages = 1
    for el in blocks:
        if el.tag == P:
            ppr = el.find(qn("w:pPr"))
            if ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None:
                pages += 1
                total = 0
            h = para_height(el, width)
            breaks = sum(1 for b in el.iter(BR) if wattr(b, "type") == "page")
            sp = ppr.find(qn("w:sectPr")) if ppr is not None else None
            if sp is not None and doc.break_after(sp) != "continuous":
                breaks += 1
        else:
            h = 0.0
            for tr in el.findall(qn("w:tr")):
                cells = tr.findall(qn("w:tc"))
                n = max(1, len(cells))
                row_h = max((sum(para_height(p, width / n) for p in iter_blocks(tc) if p.tag == P) for tc in cells), default=14.0)
                h += row_h + 2
            breaks = 0
        total += h
        while total > height:
            pages += 1
            total -= height
        if breaks:
            pages += breaks
            total = 0
    return pages


def count_pages(src: Path) -> tuple[int, str]:
    """Pages when laid out (LibreOffice when installed, else the built-in renderer); the layout is cached."""
    from _cache import release
    from _docx import BUILTIN_LABEL, document_pdf
    from _render import pdf_page_count

    pdf, label, _, entry = document_pdf(src)
    try:
        return pdf_page_count(pdf), "built-in renderer, approximate" if label == BUILTIN_LABEL else label
    finally:
        release(entry)


def render(info: dict[str, Any]) -> str:
    L: list[str] = []
    p = info["pages"]
    page_bits = []
    if "rendered" in p:
        page_bits.append(f"{p['rendered']} ({p['engine']})")
    if "saved" in p:
        page_bits.append(f"{p['saved']} as last saved" + (" (out of date)" if p.get("saved_stale") else ""))
    page_bits.append(f"~{p['estimate']} estimated")
    L.append(f"# {Path(info['file']).name}")
    L.append(f"{info['format']} · {info['size']} · pages: {', '.join(page_bits)} · {info['words']:,} words · {info['paragraphs']:,} paragraphs · {info['blocks']:,} blocks")
    if info.get("note"):
        L.append(f"note: {info['note']}")
    pages_now = p.get("rendered") or p["estimate"]
    if pages_now >= 60 or info["blocks"] >= 3000:
        name = Path(info["file"]).name
        L.append(f"A long document: `docx_read.py {name}` prints a map (sections with block ranges); drill down with --section, --blocks or --find, and render the pages you need with `docx_render.py {name} --find TEXT` or `--block N`.")
    props = info["properties"]
    if props:
        L.append("")
        L.append("Properties: " + "; ".join(f"{k}: {v}" for k, v in props.items()))
    if info["custom_properties"]:
        L.append("Custom properties: " + "; ".join(f"{k}: {v}" for k, v in info["custom_properties"].items()))
    if info["application"]:
        L.append("Saved by: " + ", ".join(f"{k} {v}" for k, v in info["application"].items() if k in ("Application", "AppVersion", "Company", "Template")))
    L.append("")
    L.append("## Sections")
    for i, s in enumerate(info["sections"], 1):
        m = s["margins_cm"] or {}
        extras = []
        if s.get("columns"):
            extras.append(f"{s['columns']} columns")
        if s.get("different_first_page"):
            extras.append("different first page")
        if s.get("page_numbering"):
            extras.append("page numbers " + ", ".join(f"{k} {v}" for k, v in s["page_numbering"].items()))
        if s.get("headers_footers"):
            extras.append(", ".join(s["headers_footers"]))
        size = f"{s['paper']} {s['orientation']} ({s['size_cm'][0]}×{s['size_cm'][1]} cm)" if s.get("page_size_set", True) else "page size not set (the application's default, usually Letter or A4)"
        margins = f"margins cm T{m.get('top')} R{m.get('right')} B{m.get('bottom')} L{m.get('left')}" if m else "margins not set"
        L.append(f"{i}. {size}, {margins}, starts {s['start']}" + (f"; {'; '.join(extras)}" if extras else ""))
    for kind in ("headers", "footers"):
        for h in info[kind]:
            if h["text"]:
                L.append(f"{kind[:-1].capitalize()} ({h['type']}, section {', '.join(map(str, h['sections']))}): {h['text'][:200]}")
    hd = info["headings"]
    L.append("")
    L.append(f"## Outline ({hd['count']} headings" + (", by level " + ", ".join(f"{'title' if k == 0 else 'H' + str(k)}: {v}" for k, v in hd["by_level"].items()) if hd["by_level"] else "") + ")")
    for h in hd["first"]:
        L.append(f"{'  ' * max(0, h['level'] - 1)}[{h['index']}] {h['text']}")
    if hd["count"] > len(hd["first"]):
        L.append(f"… {hd['count'] - len(hd['first'])} more (docx_read.py --outline)")
    L.append("")
    L.append("## Content")
    L.append("Paragraph styles: " + ", ".join(f"{k} ({v})" for k, v in list(info["styles_used"].items())[:15]))
    if info["character_styles_used"]:
        L.append("Character styles: " + ", ".join(f"{k} ({v})" for k, v in list(info["character_styles_used"].items())[:10]))
    if info["tables"]:
        L.append(f"Tables: {len(info['tables'])} — " + ", ".join(f"[{t['index']}] {t['rows']}×{t['cols']}{' merged' if t['merged_cells'] else ''}" for t in info["tables"][:12]))
    if info["images"]:
        L.append("Images: " + ", ".join(f"{v} {k}" for k, v in info["images"].items() if v))
    if info["objects"]:
        L.append("Objects: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in info["objects"].items()))
    bits = [f"hyperlinks {info['hyperlinks']}", f"bookmarks {info['bookmarks']}", f"footnotes {info['footnotes']}", f"endnotes {info['endnotes']}"]
    L.append(", ".join(bits).capitalize())
    if info["fields"]:
        L.append("Fields: " + ", ".join(f"{k} ×{v}" for k, v in info["fields"].items()))
    if info["header_footer_fields"]:
        L.append("Header/footer fields: " + ", ".join(f"{k} ×{v}" for k, v in info["header_footer_fields"].items()))
    L.append("")
    L.append("## Review")
    c = info["comments"]
    L.append(f"Comments: {c['count']} by " + ", ".join(f"{k} ({v})" for k, v in c["authors"].items()) if c else "Comments: none")
    tc = info["tracked_changes"]
    if tc:
        L.append("Tracked changes: " + ", ".join(f"{k} {v}" for k, v in tc.items() if k != "authors") + " by " + ", ".join(tc["authors"]))
    else:
        L.append("Tracked changes: none")
    L.append("")
    L.append("## Settings")
    s = []
    if info["settings"]:
        s.extend(k.replace("_", " ") for k in info["settings"])
    if info["compatibility"]:
        s.append(f"compatibility mode {info['compatibility']}")
    if info["protection"]:
        s.append("protection " + ", ".join(f"{k}={v}" for k, v in info["protection"].items()))
    if info["macros"]:
        s.append(f"VBA macros present ({info['macros']['vba_project_bytes']} bytes)")
    if info["digitally_signed"]:
        s.append("digitally signed")
    if info["embedded_fonts"]:
        s.append("embedded fonts: " + ", ".join(info["embedded_fonts"]))
    if info["hidden_text"]:
        s.append("contains hidden text")
    if info["attached_template"]:
        s.append(f"template {info['attached_template']}")
    if info["external_links"]:
        s.append("external links: " + ", ".join(info["external_links"][:5]))
    L.append("; ".join(s) if s else "nothing notable")
    return "\n".join(L)


if __name__ == "__main__":
    run_main(main)
