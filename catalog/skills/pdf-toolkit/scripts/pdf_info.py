#!/usr/bin/env python3
"""Summarise a PDF: pages and sizes, encryption and permissions, metadata (Info and XMP), outline, form fields,
attachments, JavaScript and other actions, fonts (embedded or not), images, links, and which pages have a text
layer. Pages without text (scanned) are named: render them with pdf_render.py and look at them (no OCR here).

Examples:
  python3 scripts/pdf_info.py report.pdf
  python3 scripts/pdf_info.py report.pdf --format json
  python3 scripts/pdf_info.py big.pdf --pages 1-40 --fonts      # per-page table for pages 1-40, full font list
  python3 scripts/pdf_info.py locked.pdf --password secret
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _common import add_format, human_size, md_table, parser, pool_map, run_main
from _pdfkit import cached_value, emit, fmt_num, info_strings, open_reader, page_labels, paper_name, parse_pages, pdf_input, pdfium_source, plural

PERMS = {"print": 4, "modify": 8, "copy": 16, "annotate": 32, "fill-forms": 256, "accessibility": 512, "assemble": 1024, "print-high": 2048}
TABLE_LIMIT = 60


def pdf_date(value: Any) -> str | None:
    """'D:20240501103000+02'00'' → '2024-05-01T10:30:00+02:00' (best effort)."""
    if value is None:
        return None
    s = str(value)
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?", s)
    if not m:
        return s
    y, mo, d, hh, mi, ss, tz, tzh, tzm = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}"
    if hh:
        out += f"T{hh}:{mi or '00'}:{ss or '00'}"
        if tz in ("Z", "z"):
            out += "Z"
        elif tz in ("+", "-") and tzh:
            out += f"{tz}{tzh}:{tzm or '00'}"
    return out


def encryption_info(reader: Any, password: str | None) -> dict[str, Any] | None:
    enc = getattr(reader, "_desk_encryption", None) or getattr(reader, "_encryption", None)
    if enc is None:
        return None
    algo = "unknown"
    if enc.V >= 5:
        algo = "AES-256"
    elif enc.V == 4:
        cfm = ""
        try:
            cf = enc.entry.get("/CF", {}).get(str(enc.StmF), {})
            cfm = str(cf.get("/CFM", ""))
        except Exception:  # noqa: BLE001
            pass
        algo = "AES-128" if "AESV2" in cfm else "RC4-128"
    elif enc.V in (1, 2):
        algo = f"RC4-{enc.Length or 40}"
    allowed = [k for k, bit in PERMS.items() if enc.P & bit]
    return {
        "algorithm": algo,
        "revision": enc.R,
        "opened_with": "password" if password else "empty user password (anyone can open it; the owner password only restricts permissions)",
        "permissions": allowed,
        "denied": [k for k in PERMS if k not in allowed],
    }


def xmp_info(reader: Any) -> dict[str, Any] | None:
    root = reader.root_object
    meta = root.get("/Metadata")
    if meta is None:
        return None
    try:
        xml = meta.get_object().get_data().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return {"present": True, "readable": False}
    out: dict[str, Any] = {"present": True}

    def tag(name: str) -> str | None:
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", xml, re.S)
        if not m:
            m2 = re.search(rf'{name}="([^"]*)"', xml)
            return m2.group(1).strip() if m2 else None
        inner = re.sub(r"<[^>]+>", " ", m.group(1))
        return re.sub(r"\s+", " ", inner).strip() or None

    for key, name in (("title", "dc:title"), ("creator", "dc:creator"), ("description", "dc:description"), ("create_date", "xmp:CreateDate"), ("modify_date", "xmp:ModifyDate"), ("creator_tool", "xmp:CreatorTool"), ("producer", "pdf:Producer"), ("keywords", "pdf:Keywords")):
        v = tag(name)
        if v:
            out[key] = v
    part, conf = tag("pdfaid:part"), tag("pdfaid:conformance")
    if part:
        out["pdfa"] = f"PDF/A-{part}{(conf or '').lower()}"
    ua = tag("pdfuaid:part")
    if ua:
        out["pdfua"] = f"PDF/UA-{ua}"
    return out


def outline_info(reader: Any) -> dict[str, Any]:
    entries: list[tuple[int, str]] = []

    def walk(items: Any, depth: int) -> None:
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
            else:
                try:
                    entries.append((depth, str(it.title)))
                except Exception:  # noqa: BLE001
                    pass
            if len(entries) > 5000:
                return

    try:
        walk(reader.outline, 0)
    except Exception:  # noqa: BLE001 — broken outline
        pass
    return {"entries": len(entries), "levels": (max(d for d, _ in entries) + 1) if entries else 0, "top": [t for d, t in entries if d == 0][:25]}


FIELD_TYPES = {"/Tx": "text", "/Btn": "button", "/Ch": "choice", "/Sig": "signature"}


def forms_info(reader: Any) -> dict[str, Any] | None:
    root = reader.root_object
    acro = root.get("/AcroForm")
    if acro is None:
        return None
    acro = acro.get_object()
    try:
        fields = reader.get_fields() or {}
    except Exception:  # noqa: BLE001
        fields = {}
    types: dict[str, int] = {}
    signed = 0
    for f in fields.values():
        ft = f.get("/FT")
        kind = FIELD_TYPES.get(str(ft), str(ft) if ft else "group")
        if kind == "button":
            flags = int(f.get("/Ff", 0) or 0)
            kind = "pushbutton" if flags & (1 << 16) else "radio" if flags & (1 << 15) else "checkbox"
        if kind == "group":
            continue
        types[kind] = types.get(kind, 0) + 1
        if kind == "signature" and f.get("/V") is not None:
            signed += 1
    return {
        "fields": sum(types.values()),
        "types": types,
        "xfa": "/XFA" in acro,
        "need_appearances": bool(acro.get("/NeedAppearances", False)),
        "signatures": types.get("signature", 0),
        "signed": signed,
    }


def action_kind(action: Any) -> str | None:
    try:
        a = action.get_object()
        s = a.get("/S")
        return str(s)[1:] if s else None
    except Exception:  # noqa: BLE001
        return None


def actions_info(reader: Any, pages: list[Any]) -> dict[str, Any]:
    root = reader.root_object
    out: dict[str, Any] = {"document_javascript": [], "open_action": None, "counts": {}}
    try:
        names = root.get("/Names")
        js = names.get_object().get("/JavaScript") if names else None
        if js is not None:
            arr = js.get_object().get("/Names", [])
            out["document_javascript"] = [str(arr[i]) for i in range(0, len(arr), 2)][:20]
            # Name trees can be nested in /Kids.
            if not arr and "/Kids" in js.get_object():
                out["document_javascript"] = ["(name tree)"]
    except Exception:  # noqa: BLE001
        pass
    oa = root.get("/OpenAction")
    if oa is not None:
        obj = oa.get_object()
        out["open_action"] = action_kind(obj) if hasattr(obj, "get") and obj.get("/S") else "GoTo (page)"
    counts: dict[str, int] = {}

    def count(kind: str | None) -> None:
        if kind:
            counts[kind] = counts.get(kind, 0) + 1

    if "/AA" in root:
        for _k, v in root["/AA"].get_object().items():
            count(action_kind(v))
    for page in pages:
        try:
            if "/AA" in page:
                for _k, v in page["/AA"].get_object().items():
                    count(action_kind(v))
            for annot in page.get("/Annots", []) or []:
                a = annot.get_object()
                if "/A" in a:
                    count(action_kind(a["/A"]))
                if "/AA" in a:
                    for _k, v in a["/AA"].get_object().items():
                        count(action_kind(v))
        except Exception:  # noqa: BLE001
            continue
    out["counts"] = counts
    risky = {k: v for k, v in counts.items() if k in ("JavaScript", "Launch", "SubmitForm", "ImportData")}
    if out["document_javascript"] or out["open_action"] == "JavaScript":
        risky["JavaScript"] = risky.get("JavaScript", 0) + len(out["document_javascript"]) + (1 if out["open_action"] == "JavaScript" else 0)
    out["risky"] = risky
    return out


def font_record(fobj: Any) -> dict[str, Any]:
    sub = str(fobj.get("/Subtype", "/?"))[1:]
    base = fobj.get("/BaseFont")
    name = str(base)[1:] if base else "(unnamed)"
    fd = fobj.get("/FontDescriptor")
    kind = sub
    if sub == "Type0":
        try:
            desc = fobj["/DescendantFonts"][0].get_object()
            fd = desc.get("/FontDescriptor")
            kind = "Type0 (" + ("CFF" if str(desc.get("/Subtype")) == "/CIDFontType0" else "TrueType") + ")"
        except Exception:  # noqa: BLE001
            fd = None
    embedded = sub == "Type3"
    file_kind = None
    if fd is not None:
        try:
            fdo = fd.get_object()
            for k in ("/FontFile", "/FontFile2", "/FontFile3"):
                if k in fdo:
                    embedded = True
                    file_kind = k[1:]
                    break
        except Exception:  # noqa: BLE001
            pass
    enc = fobj.get("/Encoding")
    enc_name = str(enc)[1:] if enc is not None and str(enc).startswith("/") else ("custom" if enc is not None else None)
    return {
        "name": re.sub(r"^[A-Z]{6}\+", "", name),
        "type": kind,
        "embedded": embedded,
        "subset": bool(re.match(r"^[A-Z]{6}\+", name)),
        "encoding": enc_name,
        "font_file": file_kind,
    }


def walk_fonts(reader: Any, indices: list[int]) -> tuple[list[dict[str, Any]], int, list[int]]:
    """Fonts used by the pages (including inside form XObjects), plus the image XObject count and pages with images."""
    fonts: dict[Any, dict[str, Any]] = {}
    seen_forms: set[Any] = set()
    images: set[Any] = set()
    image_pages: list[int] = []

    def key_of(ref: Any, obj: Any) -> Any:
        return getattr(ref, "idnum", None) or id(obj)

    def walk(res: Any, pno: int, depth: int, had_image: list[bool]) -> None:
        if res is None or depth > 12:
            return
        try:
            res = res.get_object()
        except Exception:  # noqa: BLE001
            return
        fd = res.get("/Font")
        if fd is not None:
            for _n, ref in fd.get_object().items():
                try:
                    obj = ref.get_object()
                    k = key_of(ref, obj)
                    if k not in fonts:
                        fonts[k] = {**font_record(obj), "pages": []}
                    pages = fonts[k]["pages"]
                    if not pages or pages[-1] != pno:
                        pages.append(pno)
                except Exception:  # noqa: BLE001
                    continue
        xd = res.get("/XObject")
        if xd is not None:
            for _n, ref in xd.get_object().items():
                try:
                    obj = ref.get_object()
                    st = obj.get("/Subtype")
                    k = key_of(ref, obj)
                    if st == "/Image":
                        images.add(k)
                        had_image[0] = True
                    elif st == "/Form" and (k, pno) not in seen_forms:
                        seen_forms.add((k, pno))
                        walk(obj.get("/Resources"), pno, depth + 1, had_image)
                except Exception:  # noqa: BLE001
                    continue

    for i in indices:
        page = reader.pages[i]
        had = [False]
        try:
            walk(page.get("/Resources"), i + 1, 0, had)
        except Exception:  # noqa: BLE001
            pass
        if had[0]:
            image_pages.append(i + 1)
    return list(fonts.values()), len(images), image_pages


def _analyze_chunk(job: tuple[str, str | None, list[int]]) -> list[dict[str, Any]]:
    """Per-page text and image statistics with pypdfium2 (runs in worker processes for big files)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    path, password, indices = job
    doc = pdfium.PdfDocument(path, password=password)
    out = []
    try:
        for i in indices:
            page = doc[i]
            w, h = page.get_size()
            l, b, r, t = page.get_cropbox()
            area = max(1.0, (r - l) * (t - b))
            tp = page.get_textpage()
            n = tp.count_chars()
            text = tp.get_text_range() if n else ""
            tp.close()
            chars = sum(1 for c in text if not c.isspace())
            imgs, cover = 0, 0.0
            for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_IMAGE,), max_depth=4):
                imgs += 1
                try:
                    x0, y0, x1, y1 = obj.get_bounds()
                    iw, ih = max(0.0, min(x1, r) - max(x0, l)), max(0.0, min(y1, t) - max(y0, b))
                    cover = max(cover, iw * ih / area)
                except Exception:  # noqa: BLE001
                    pass
            objects = raw.FPDFPage_CountObjects(page)
            if chars >= 10 and cover >= 0.8:
                kind = "image+text"  # a scan with an (often invisible) OCR text layer
            elif imgs and cover >= 0.4 and chars < 10:
                kind = "scanned"  # a page image, perhaps with a stamped page number
            elif chars:
                kind = "text"
            elif imgs:
                kind = "images"
            elif objects:
                kind = "vector"
            else:
                kind = "blank"
            out.append({"page": i + 1, "width": fmt_num(w), "height": fmt_num(h), "rotation": page.get_rotation(), "chars": chars, "images": imgs, "image_cover": round(cover, 2), "kind": kind})
            page.close()
    finally:
        doc.close()
    return out


def analyze_pages(path: str, password: str | None, count: int) -> list[dict[str, Any]]:
    indices = list(range(count))
    if count <= 150:
        return _analyze_chunk((path, password, indices))
    n = 8
    size = (count + n - 1) // n
    chunks = [(path, password, indices[i : i + size]) for i in range(0, count, size)]
    return [p for part in pool_map(_analyze_chunk, chunks) for p in part]


def collect(path: Path, password: str | None, pages_spec: str | None, want_fonts: bool) -> dict[str, Any]:
    reader = open_reader(path, password)
    count = len(reader.pages)
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(1024)
    version = None
    m = re.search(rb"%PDF-(\d\.\d)", head)
    if m:
        version = m.group(1).decode()
    root = reader.root_object
    if "/Version" in root:
        version = str(root["/Version"])[1:]
    revisions = 0
    with open(path, "rb") as f:
        tail = b""
        while chunk := f.read(1 << 20):
            revisions += (tail + chunk).count(b"%%EOF") - tail.count(b"%%EOF")
            tail = chunk[-8:]
    meta = {}
    for k, v in info_strings(reader).items():
        key = k.lstrip("/")
        val = v.strip()
        if val:
            meta[key] = pdf_date(val) if key in ("CreationDate", "ModDate") else val
    per_page = analyze_pages(str(pdfium_source(path, password)), password, count)
    try:
        labels = page_labels(reader) if "/PageLabels" in root else None
    except Exception:  # noqa: BLE001
        labels = None
    if labels:
        for p in per_page:
            p["label"] = labels[p["page"] - 1]
    pages = list(reader.pages)
    fonts, image_objects, image_pages = walk_fonts(reader, list(range(count)))
    annots: dict[str, int] = {}
    links = {"internal": 0, "external": 0}
    for page in pages:
        try:
            for a in page.get("/Annots", []) or []:
                ao = a.get_object()
                st = str(ao.get("/Subtype", "/?"))[1:]
                annots[st] = annots.get(st, 0) + 1
                if st == "Link":
                    act = ao.get("/A")
                    kind = action_kind(act) if act is not None else None
                    links["external" if kind in ("URI", "Launch", "GoToR") else "internal"] += 1
        except Exception:  # noqa: BLE001
            continue
    try:
        attachments = [{"name": n, "size": sum(len(d) for d in data)} for n, data in reader.attachments.items()]
    except Exception:  # noqa: BLE001
        attachments = []
    sizes: dict[str, int] = {}
    for p in per_page:
        key = f"{p['width']}x{p['height']}"
        sizes[key] = sizes.get(key, 0) + 1
    size_summary = []
    for key, n in sorted(sizes.items(), key=lambda kv: -kv[1]):
        w, h = (float(x) for x in key.split("x"))
        size_summary.append({"width": fmt_num(w), "height": fmt_num(h), "paper": paper_name(w, h), "pages": n})
    ocp = root.get("/OCProperties")
    layers = 0
    if ocp is not None:
        try:
            layers = len(ocp.get_object().get("/OCGs", []))
        except Exception:  # noqa: BLE001
            layers = 0
    no_text = [p["page"] for p in per_page if p["kind"] in ("scanned", "images", "vector", "blank")]
    info: dict[str, Any] = {
        "file": str(path),
        "bytes": size,
        "version": version,
        "pages": count,
        "page_sizes": size_summary,
        "rotated_pages": [p["page"] for p in per_page if p["rotation"]],
        "encryption": encryption_info(reader, password),
        "metadata": meta,
        "xmp": xmp_info(reader),
        "repaired": getattr(reader, "_desk_repaired", None),
        "tagged": bool(root.get("/MarkInfo", {}).get("/Marked", False)) if root.get("/MarkInfo") is not None else False,
        "structure_tree": "/StructTreeRoot" in root,
        "linearized": b"/Linearized" in head,
        "revisions": max(1, revisions),
        "layers": layers,
        "page_labels": labels is not None,
        "outline": outline_info(reader),
        "forms": forms_info(reader),
        "attachments": attachments,
        "actions": actions_info(reader, pages),
        "fonts": sorted(fonts, key=lambda f: (f["embedded"], f["name"])),
        "images": {"objects": image_objects, "pages": len(image_pages), "placed": sum(p["images"] for p in per_page)},
        "links": links,
        "annotations": annots,
        "text": {
            "pages_with_text": sum(1 for p in per_page if p["kind"] in ("text", "image+text")),
            "scanned_pages": [p["page"] for p in per_page if p["kind"] == "scanned"],
            "ocr_layer_pages": [p["page"] for p in per_page if p["kind"] == "image+text"],
            "pages_without_text": no_text,
        },
    }
    table_pages = parse_pages(pages_spec, count) if pages_spec else list(range(1, min(count, TABLE_LIMIT) + 1))
    info["per_page"] = [per_page[n - 1] for n in table_pages]
    info["_want_fonts"] = want_fonts
    return info


def _short(ranges: str, limit: int = 40) -> str:
    if len(ranges) <= limit:
        return ranges
    return ranges[: ranges.rfind(",", 0, limit)] + " …"


def compress_ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:  # type: ignore[list-item]
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ",".join(out)


def render_md(info: dict[str, Any]) -> str:
    L: list[str] = []
    name = Path(info["file"]).name
    L.append(f"# {name}")
    sizes = info["page_sizes"]
    size_text = ""
    if len(sizes) == 1:
        s = sizes[0]
        size_text = f" · {s['paper'] + ' ' if s['paper'] != 'custom' else ''}({s['width']} × {s['height']} pt)"
    L.append(f"{plural(info['pages'], 'page')} · PDF {info['version'] or '?'} · {human_size(info['bytes'])}{size_text}")
    if len(sizes) > 1:
        L.append("Page sizes vary: " + "; ".join(f"{s['width']} × {s['height']} pt ({s['paper']}) × {s['pages']}" for s in sizes[:6]))
    if info["rotated_pages"]:
        L.append(f"Rotated pages: {compress_ranges(info['rotated_pages'])}")
    L.append("")
    L.append("## Document")
    meta = info["metadata"]
    for key in ("Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate"):
        if meta.get(key):
            L.append(f"- {key}: {meta[key]}")
    extra = [k for k in meta if k not in ("Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate")]
    if extra:
        L.append(f"- Other Info keys: {', '.join(extra[:10])}")
    if not meta:
        L.append("- No Info metadata")
    xmp = info["xmp"]
    if xmp:
        bits = [xmp.get("pdfa"), xmp.get("pdfua")]
        L.append("- XMP metadata: yes" + (f" ({', '.join(b for b in bits if b)})" if any(bits) else "") + (f"; dc:title “{xmp['title']}”" if xmp.get("title") and xmp.get("title") != meta.get("Title") else ""))
    enc = info["encryption"]
    if enc:
        L.append(f"- Encryption: {enc['algorithm']} (R{enc['revision']}), opened with {enc['opened_with']}")
        L.append(f"  - Allowed: {', '.join(enc['permissions']) or 'nothing'}; denied: {', '.join(enc['denied']) or 'nothing'}")
    else:
        L.append("- Encryption: none")
    flags = [f"tagged: {'yes' if info['tagged'] else 'no'}", f"revisions: {info['revisions']}"]
    if info["linearized"]:
        flags.append("linearized")
    if info["layers"]:
        flags.append(f"layers: {info['layers']}")
    if info["page_labels"]:
        flags.append("page labels: yes")
    L.append("- " + " · ".join(flags))
    if info.get("repaired"):
        L.append(f"- Damaged structure: read through a repaired copy ({info['repaired'][:120]})")
    L.append("")
    L.append("## Content")
    t = info["text"]
    L.append(f"- Text layer: {t['pages_with_text']} of {plural(info['pages'], 'page')}")
    if t["scanned_pages"]:
        L.append(f"- Scanned pages (images, no text): {compress_ranges(t['scanned_pages'])}. Render them with pdf_render.py and look at them with view_image; this skill does no OCR.")
    other = [p for p in t["pages_without_text"] if p not in t["scanned_pages"]]
    if other:
        L.append(f"- Other pages without text (drawings, images or blank): {compress_ranges(other)}. Render them to see what they hold.")
    if t["ocr_layer_pages"]:
        L.append(f"- Image pages with a text layer (probably OCR, may be inaccurate): {compress_ranges(t['ocr_layer_pages'])}")
    im = info["images"]
    L.append(f"- Images: {im['objects']} image objects on {plural(im['pages'], 'page')}")
    fonts = info["fonts"]
    not_emb = [f["name"] for f in fonts if not f["embedded"]]
    L.append(f"- Fonts: {len(fonts)} ({len(fonts) - len(not_emb)} embedded" + (f"; not embedded: {', '.join(sorted(set(not_emb))[:8])}" if not_emb else "") + ")")
    ol = info["outline"]
    if ol["entries"]:
        L.append(f"- Outline (bookmarks): {ol['entries']} entries, {ol['levels']} level(s): " + "; ".join(f"“{x}”" for x in ol["top"][:8]) + (" …" if len(ol["top"]) > 8 else ""))
    else:
        L.append("- Outline (bookmarks): none")
    fm = info["forms"]
    if fm and fm["fields"]:
        L.append(f"- Form: {fm['fields']} fields (" + ", ".join(f"{k} {v}" for k, v in fm["types"].items()) + ")" + (" · XFA present (dynamic form; only the AcroForm part is editable here)" if fm["xfa"] else "") + ". List them with pdf_form.py list.")
        if fm["signatures"]:
            L.append(f"- Signature fields: {fm['signatures']} ({fm['signed']} signed). Any change to the file invalidates existing signatures.")
    if info["attachments"]:
        L.append("- Attachments: " + ", ".join(f"{a['name']} ({human_size(a['size'])})" for a in info["attachments"][:10]) + ". Extract with pdf_extract.py attachments.")
    ln = info["links"]
    if ln["internal"] or ln["external"]:
        L.append(f"- Links: {ln['external']} external, {ln['internal']} internal")
    other_annots = {k: v for k, v in info["annotations"].items() if k not in ("Link", "Widget")}
    if other_annots:
        L.append("- Annotations: " + ", ".join(f"{k} {v}" for k, v in sorted(other_annots.items())))
    act = info["actions"]
    if act["risky"] or act["document_javascript"] or act["open_action"]:
        parts = []
        if act["document_javascript"]:
            parts.append(f"document JavaScript: {', '.join(act['document_javascript'][:5])}")
        if act["open_action"]:
            parts.append(f"open action: {act['open_action']}")
        for k, v in act["risky"].items():
            if k != "JavaScript" or not act["document_javascript"]:
                parts.append(f"{k} actions: {v}")
        L.append("- Actions: " + "; ".join(parts))
    else:
        L.append("- JavaScript and risky actions: none")
    if info.get("_want_fonts") or (fonts and len(fonts) <= 12):
        if fonts:
            L.append("")
            L.append("## Fonts")
            L.append(md_table(["font", "type", "embedded", "subset", "pages"], [[f["name"], f["type"], "yes" if f["embedded"] else "NO", "yes" if f["subset"] else "", _short(compress_ranges(f["pages"]))] for f in fonts]))
    pp = info["per_page"]
    if pp:
        L.append("")
        shown = f"pages {compress_ranges([p['page'] for p in pp])}" if len(pp) < info["pages"] else "all pages"
        L.append(f"## Pages ({shown})")
        has_labels = any("label" in p for p in pp)
        headers = ["page"] + (["label"] if has_labels else []) + ["size (pt)", "rot", "chars", "images", "kind"]
        rows = []
        for p in pp:
            rows.append([p["page"]] + ([p.get("label", "")] if has_labels else []) + [f"{p['width']} × {p['height']}", p["rotation"] or "", p["chars"], p["images"] or "", p["kind"]])
        L.append(md_table(headers, rows))
        if len(pp) < info["pages"]:
            L.append(f"(Use --pages to see other pages; {info['pages']} in total.)")
    return "\n".join(L)


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], "Examples:\n" + __doc__.split("Examples:\n", 1)[1])
    p.add_argument("pdf", help="the PDF to inspect")
    p.add_argument("--password", help="password of an encrypted PDF")
    p.add_argument("--pages", help=f"pages for the per-page table, like 1-5,9 (default: the first {TABLE_LIMIT})")
    p.add_argument("--fonts", action="store_true", help="always list every font (Markdown)")
    p.add_argument("--no-cache", action="store_true", help="inspect again instead of reusing the cached result")
    add_format(p)
    a = p.parse_args()
    path = pdf_input(a.pdf)
    if a.password or a.no_cache:
        info = collect(path, a.password, a.pages, a.fonts)
    else:
        info = cached_value(path, "pdf-info", {"pages": a.pages, "fonts": a.fonts}, "4", lambda: collect(path, None, a.pages, a.fonts))
        info["file"] = str(path)
    if a.format == "json":
        info.pop("_want_fonts", None)
    emit(info, a.format, render_md, hint="Narrow the per-page details with --pages (e.g. --pages 1-50).")
    return 0


if __name__ == "__main__":
    run_main(main)
