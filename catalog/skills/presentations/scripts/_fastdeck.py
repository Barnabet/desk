"""A fast, streaming map of a deck read straight from the zip (lxml, no python-pptx), cached per file content.

It answers "what is in this deck and where" for decks of any size in well under a second, and instantly on a cache
hit: per slide the number, stable address, title, layout, hidden flag, word and shape counts, notes, charts, tables,
pictures, media and SmartArt, plus a text index (shape by shape, table cell by cell, notes, chart labels) that
--find/--grep search without opening the deck again. Every part is parsed on its own, so memory stays small.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

MAP_VERSION = "2"
BIG_DECK = 50  # above this many slides pptx_read shows the map first

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
RT_SLIDE = "/slide"
MINOR_PH = ("dt", "ftr", "sldNum", "hdr")
MINOR_NAMES = ("footer", "slide number", "date")


def _parser() -> Any:
    from lxml import etree

    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)


def _xml(z: zipfile.ZipFile, name: str) -> Any:
    from lxml import etree

    try:
        data = z.read(name)
    except KeyError:
        return None
    try:
        return etree.fromstring(data, _parser())
    except etree.XMLSyntaxError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str, bool]]:
    """{rId: (type, resolved target, external)} for a part."""
    d, f = posixpath.split(part)
    root = _xml(z, posixpath.join(d, "_rels", f + ".rels"))
    out: dict[str, tuple[str, str, bool]] = {}
    if root is None:
        return out
    for rel in root.iter(PR + "Relationship"):
        rid, typ, tgt = rel.get("Id", ""), rel.get("Type", ""), rel.get("Target", "")
        ext = rel.get("TargetMode") == "External"
        if not ext:
            tgt = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath(posixpath.join(d, tgt))
        out[rid] = (typ, tgt, ext)
    return out


def _text_of(tx: Any) -> str:
    """Paragraph texts of a txBody, joined by newlines."""
    paras = []
    for p in tx.iter(A + "p"):
        parts = []
        for r in p:
            tag = r.tag if isinstance(r.tag, str) else ""
            if tag in (A + "r", A + "fld"):
                t = r.find(A + "t")
                if t is not None and t.text:
                    parts.append(t.text)
            elif tag == A + "br":
                parts.append(" ")
        paras.append("".join(parts))
    return "\n".join(paras).strip()


def _ph(el: Any) -> tuple[str, str] | None:
    for nv in el:
        tag = nv.tag if isinstance(nv.tag, str) else ""
        if tag.startswith(P + "nv"):
            ph = nv.find(P + "nvPr/" + P + "ph")
            if ph is None:
                return None
            return ph.get("type", "obj"), ph.get("idx", "0")
    return None


def _name_id(el: Any) -> tuple[str, int]:
    for nv in el:
        tag = nv.tag if isinstance(nv.tag, str) else ""
        if tag.startswith(P + "nv"):
            c = nv.find(P + "cNvPr")
            if c is not None:
                try:
                    return c.get("name", ""), int(c.get("id", "0") or 0)
                except ValueError:
                    return c.get("name", ""), 0
    return "", 0


def _descr(el: Any) -> str:
    for nv in el:
        tag = nv.tag if isinstance(nv.tag, str) else ""
        if tag.startswith(P + "nv"):
            c = nv.find(P + "cNvPr")
            if c is not None:
                return (c.get("descr") or "").strip()
    return ""


def _shapes(tree: Any) -> list[Any]:
    """Shapes in z-order, descending into groups and taking the preferred branch of AlternateContent."""
    out: list[Any] = []
    for el in tree:
        tag = el.tag if isinstance(el.tag, str) else ""
        local = tag.rsplit("}", 1)[-1]
        if local == "AlternateContent":
            ch = next((c for c in el if isinstance(c.tag, str) and c.tag.endswith("}Choice")), None)
            if ch is None:
                ch = next((c for c in el if isinstance(c.tag, str) and c.tag.endswith("}Fallback")), None)
            if ch is not None:
                out.extend(_shapes(ch))
            continue
        if local in ("sp", "pic", "graphicFrame", "cxnSp", "contentPart"):
            out.append(el)
        elif local == "grpSp":
            out.append(el)
            out.extend(_shapes(el))
    return out


def deck_map(path: str | Path, use_cache: bool = True) -> dict[str, Any]:
    """The cached map of a deck (see the module docstring)."""
    if use_cache:
        from _cache import cached_json

        return cached_json(path, "pptx-map", {}, MAP_VERSION, lambda: build_map(Path(path)))
    return build_map(Path(path))


def build_map(path: Path) -> dict[str, Any]:
    from _common import SkillError, check_zip
    from _deck import chart_data

    check_zip(path, refuse_dtd=True)  # zip bombs, billion laughs and XXE are refused before any part is parsed
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise SkillError(f"{path.name} is not a PowerPoint file (not a ZIP package)") from e
    with z:
        main = "ppt/presentation.xml"
        root_rels = _rels(z, "")
        for typ, tgt, _ext in root_rels.values():
            if typ.endswith("/officeDocument"):
                main = tgt
        pres = _xml(z, main)
        if pres is None:
            raise SkillError(f"{path.name} has no readable presentation part ({main}); it may be damaged")
        prels = _rels(z, main)
        sz = pres.find(P + "sldSz")
        size = [int(sz.get("cx", "9144000")), int(sz.get("cy", "6858000"))] if sz is not None else [9144000, 6858000]
        slide_parts: list[tuple[int, str]] = []
        lst = pres.find(P + "sldIdLst")
        for sid in (lst if lst is not None else []):
            rid = sid.get(R + "id")
            if rid in prels:
                slide_parts.append((int(sid.get("id", "0") or 0), prels[rid][1]))
        layout_names: dict[str, str] = {}
        slides = []
        for n, (sid, part) in enumerate(slide_parts, 1):
            slides.append(_slide_map(z, n, sid, part, layout_names, chart_data))
    return {"file": str(path), "slide_count": len(slides), "slide_size_in": [round(size[0] / 914400, 2), round(size[1] / 914400, 2)], "slides": slides}


def _slide_map(z: zipfile.ZipFile, n: int, sid: int, part: str, layout_names: dict[str, str], chart_data: Any) -> dict[str, Any]:
    root = _xml(z, part)
    rels = _rels(z, part)
    item: dict[str, Any] = {"number": n, "address": f"slide {n}", "slide_id": sid, "title": "", "layout": "", "hidden": False,
                            "words": 0, "shapes": 0, "notes": False, "charts": 0, "tables": 0, "pictures": 0, "media": 0, "smartart": 0}
    texts: list[dict[str, Any]] = []
    item["texts"] = texts
    for typ, tgt, ext in rels.values():
        if typ.endswith("/slideLayout") and not ext:
            if tgt not in layout_names:
                lroot = _xml(z, tgt)
                csld = lroot.find(P + "cSld") if lroot is not None else None
                layout_names[tgt] = csld.get("name", "") if csld is not None else ""
            item["layout"] = layout_names[tgt]
        elif typ.endswith("/notesSlide") and not ext:
            nroot = _xml(z, tgt)
            if nroot is not None:
                for sp in nroot.iter(P + "sp"):
                    ph = _ph(sp)
                    tx = sp.find(P + "txBody")
                    if ph and ph[0] == "body" and tx is not None:
                        t = _text_of(tx)
                        if t:
                            item["notes"] = True
                            item["notes_words"] = len(re.findall(r"\w+", t))
                            texts.append({"where": "notes", "text": t})
                        break
    if root is None:
        item["error"] = "the slide XML could not be read"
        return item
    item["hidden"] = root.get("show") in ("0", "false")
    tree = root.find(P + "cSld/" + P + "spTree")
    if tree is None:
        return item
    words = 0
    count = 0
    for el in _shapes(tree):
        local = el.tag.rsplit("}", 1)[-1]
        name, shid = _name_id(el)
        ph = _ph(el)
        if local != "grpSp":
            count += 1
        base = {"shape": name, "id": shid}
        if local == "sp":
            tx = el.find(P + "txBody")
            if tx is None:
                continue
            t = _text_of(tx)
            if not t:
                continue
            minor = (ph is not None and ph[0] in MINOR_PH) or name.lower().startswith(MINOR_NAMES)
            if ph is not None and ph[0] in ("title", "ctrTitle") and not item["title"]:
                item["title"] = " ".join(t.split())
            if not minor:
                words += len(re.findall(r"\w+", t))
                texts.append({"where": "title" if ph is not None and ph[0] in ("title", "ctrTitle") else "shape", **base, "text": t})
        elif local == "pic":
            if el.find(".//" + P + "videoFile") is not None or el.find(".//" + A + "videoFile") is not None or el.find(".//" + A + "audioFile") is not None:
                item["media"] += 1
            else:
                item["pictures"] += 1
            d = _descr(el)
            if d:
                texts.append({"where": "alt text", **base, "text": d})
        elif local == "graphicFrame":
            gd = el.find(A + "graphic/" + A + "graphicData")
            uri = gd.get("uri", "") if gd is not None else ""
            if uri.endswith("/table"):
                item["tables"] += 1
                tbl = gd.find(A + "tbl")
                for ri, tr in enumerate(tbl.findall(A + "tr") if tbl is not None else [], 1):
                    cells = [_text_of(tc.find(A + "txBody")) if tc.find(A + "txBody") is not None else "" for tc in tr.findall(A + "tc")]
                    row = " | ".join(cells).strip(" |")
                    if row:
                        texts.append({"where": "table", **base, "row": ri, "text": row})
            elif uri.endswith("/chart") or "chartex" in uri:
                item["charts"] += 1
                cref = gd.find(".//" + C + "chart")
                rid = cref.get(R + "id") if cref is not None else None
                if rid and rid in rels:
                    croot = _xml(z, rels[rid][1])
                    if croot is not None:
                        try:
                            cd = chart_data(croot)
                        except Exception:  # noqa: BLE001 — a broken chart part only loses its labels
                            cd = {}
                        bits = [cd.get("title") or ""] + [str(s.get("name") or "") for s in cd.get("series", [])] + [str(c) for c in cd.get("categories", []) if c is not None][:200]
                        t = " | ".join(b for b in bits if b)
                        texts.append({"where": "chart", **base, "kind": ", ".join(cd.get("types", [])), "text": t})
            elif uri.endswith("/diagram"):
                item["smartart"] += 1
                ids = gd.find(".//{http://schemas.openxmlformats.org/drawingml/2006/diagram}relIds")
                dm = ids.get(R + "dm") if ids is not None else None
                if dm and dm in rels:
                    droot = _xml(z, rels[dm][1])
                    if droot is not None:
                        t = " | ".join(x.text for x in droot.iter(A + "t") if x.text and x.text.strip())
                        if t:
                            words += len(re.findall(r"\w+", t))
                            texts.append({"where": "smartart", **base, "text": t})
    item["words"] = words
    item["shapes"] = count
    return item


# ── search ──────────────────────────────────────────────────────────────


def address(slide: int, hit: dict[str, Any]) -> str:
    w = hit["where"]
    if w == "notes":
        return f"slide {slide} / notes"
    name = hit.get("shape", "")
    if w == "table":
        return f'slide {slide} / table "{name}" row {hit.get("row")}'
    if w in ("chart", "smartart"):
        return f'slide {slide} / {w} "{name}"'
    return f'slide {slide} / shape "{name}"'


def search(m: dict[str, Any], pattern: Any, numbers: list[int] | None = None, context: int = 60, limit: int = 200) -> dict[str, Any]:
    """Hits of a compiled regex over the text index: address, where, shape, context. `limit` caps the list."""
    hits: list[dict[str, Any]] = []
    total = 0
    wanted = set(numbers) if numbers else None
    for s in m["slides"]:
        if wanted is not None and s["number"] not in wanted:
            continue
        for t in s.get("texts", []):
            text = t["text"]
            for mt in pattern.finditer(text):
                total += 1
                if len(hits) >= limit:
                    continue
                a, b = mt.start(), mt.end()
                lo, hi = max(0, a - context), min(len(text), b + context)
                snippet = ("…" if lo else "") + text[lo:a] + "«" + text[a:b] + "»" + text[b:hi] + ("…" if hi < len(text) else "")
                h = {"slide": s["number"], "title": s["title"], "address": address(s["number"], t), "where": t["where"], "match": text[a:b], "context": " ".join(snippet.split())}
                if t.get("shape"):
                    h["shape"] = t["shape"]
                    h["shape_id"] = t.get("id")
                if t.get("row"):
                    h["row"] = t["row"]
                hits.append(h)
                if mt.end() == mt.start():
                    break
    return {"hits": hits, "total": total, "shown": len(hits)}
