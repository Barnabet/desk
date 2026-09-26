#!/usr/bin/env python3
"""Extract what a PDF carries besides its text: images (original JPEG/JPEG 2000 bytes when possible, PNG with
transparency otherwise), attached files, fonts (list, or save the embedded font programs) and links.

Commands:
  images PDF [--out DIR]        every distinct image once, with page, pixel size and position
  attachments PDF [--out DIR]   embedded files (document-level and file-attachment annotations)
  fonts PDF [--extract DIR]     fonts per page: embedded, subset, type; optionally save the font files
  links PDF                     link annotations: page, box, target URL or page

Examples:
  python3 scripts/pdf_extract.py images report.pdf --out report-images/ --min-size 64
  python3 scripts/pdf_extract.py images scan.pdf --pages 1-3 --png
  python3 scripts/pdf_extract.py attachments invoice.pdf --out attached/
  python3 scripts/pdf_extract.py fonts report.pdf
  python3 scripts/pdf_extract.py links report.pdf --format json
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

from _common import UsageError, add_format, human_size, md_table, output_dir, parser, run_main
from _pdfkit import emit, fmt_num, geometry_of, open_pdfium, open_reader, parse_pages, pdf_input, pdfium_source, safe_filename, unique_path


def _pdfium_boxes(path: Path, password: str | None, numbers: list[int]) -> dict[int, list[tuple[int, int, list[float]]]]:
    """Per page: (pixel width, pixel height, view box) of each placed image, from pdfium."""
    import pypdfium2.raw as raw

    from _pdfkit import pdfium_geometry

    out: dict[int, list[tuple[int, int, list[float]]]] = {}
    doc = open_pdfium(path, password)
    try:
        for n in numbers:
            page = doc[n - 1]
            geo = pdfium_geometry(page)
            items = []
            for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_IMAGE,), max_depth=8):
                try:
                    w, h = obj.get_px_size()
                    l, b, r, t = obj.get_bounds()
                    items.append((int(w), int(h), [fmt_num(v) for v in geo.rect_to_view(l, b, r, t)]))
                except Exception:  # noqa: BLE001
                    continue
            out[n] = items
            page.close()
    finally:
        doc.close()
    return out


def cmd_images(a: Any) -> dict[str, Any]:
    path = pdf_input(a.pdf)
    reader = open_reader(path, a.password)
    numbers = parse_pages(a.pages, len(reader.pages))
    folder = output_dir(a.out or f"{path.stem}-images")
    boxes = _pdfium_boxes(pdfium_source(path, a.password), a.password, numbers)
    seen: dict[Any, str] = {}
    images: list[dict[str, Any]] = []
    skipped_small = 0
    failed: list[str] = []
    for n in numbers:
        page = reader.pages[n - 1]
        try:
            files = list(page.images)
        except Exception as e:  # noqa: BLE001 — broken image streams
            failed.append(f"page {n}: {e}")
            continue
        page_boxes = list(boxes.get(n, []))
        for k, img in enumerate(files):
            try:
                pil = img.image
                w, h = pil.size if pil is not None else (0, 0)
            except Exception as e:  # noqa: BLE001
                failed.append(f"page {n} image {k + 1}: {e}")
                continue
            box = None
            for i, (bw, bh, vb) in enumerate(page_boxes):
                if (bw, bh) == (w, h):
                    box = vb
                    page_boxes.pop(i)
                    break
            ref = getattr(img, "indirect_reference", None)
            key = ("ref", ref.idnum) if ref is not None else ("hash", hashlib.sha1(img.data).hexdigest())
            if key in seen:
                images.append({"page": n, "file": seen[key], "width": w, "height": h, "box": box, "duplicate": True})
                continue
            if min(w, h) < a.min_size:
                skipped_small += 1
                continue
            ext = Path(img.name).suffix.lower() or ".png"
            data = img.data
            if a.png and ext != ".png":
                buf = io.BytesIO()
                im = pil if pil.mode in ("RGB", "RGBA", "L", "LA", "P", "1") else pil.convert("RGB")
                im.save(buf, "PNG")
                data, ext = buf.getvalue(), ".png"
            if ext in (".jp2", ".jpx") and not a.keep_jp2:
                buf = io.BytesIO()
                pil.convert("RGBA" if "A" in pil.mode else "RGB").save(buf, "PNG")
                data, ext = buf.getvalue(), ".png"
            target = unique_path(folder, f"page{n:03d}-img{k + 1:02d}{ext}")
            target.write_bytes(data)
            seen[key] = str(target)
            images.append({"page": n, "file": str(target), "format": ext.lstrip("."), "width": w, "height": h, "mode": pil.mode, "bytes": len(data), "box": box})
    return {"folder": str(folder), "saved": sum(1 for i in images if not i.get("duplicate")), "placements": len(images), "skipped_small": skipped_small, "failed": failed, "images": images}


def _annotation_attachments(reader: Any) -> list[tuple[str, bytes, int]]:
    out = []
    for n, page in enumerate(reader.pages, 1):
        for ref in page.get("/Annots", []) or []:
            try:
                a = ref.get_object()
                if a.get("/Subtype") != "/FileAttachment":
                    continue
                fs = a["/FS"].get_object()
                name = str(fs.get("/UF") or fs.get("/F") or "attachment")
                ef = fs["/EF"].get_object()
                stream = (ef.get("/F") or ef.get("/UF")).get_object()
                out.append((name, stream.get_data(), n))
            except Exception:  # noqa: BLE001
                continue
    return out


def cmd_attachments(a: Any) -> dict[str, Any]:
    path = pdf_input(a.pdf)
    reader = open_reader(path, a.password)
    items: list[tuple[str, bytes, int | None]] = []
    try:
        for name, datas in reader.attachments.items():
            for d in datas:
                items.append((name, d, None))
    except Exception:  # noqa: BLE001
        pass
    items.extend(_annotation_attachments(reader))
    if a.name:
        items = [i for i in items if i[0] == a.name or Path(i[0]).name == a.name]
        if not items:
            raise UsageError(f"no attachment named {a.name}")
    if a.list:
        return {"attachments": [{"name": n, "bytes": len(d), "page": p} for n, d, p in items]}
    folder = output_dir(a.out or f"{path.stem}-attachments")
    saved = []
    for name, data, page in items:
        target = unique_path(folder, safe_filename(name, "attachment"))
        target.write_bytes(data)
        saved.append({"name": name, "file": str(target), "bytes": len(data), "page": page})
    return {"folder": str(folder), "attachments": saved}


FONT_EXT = {"FontFile": ".pfb", "FontFile2": ".ttf"}


def cmd_fonts(a: Any) -> dict[str, Any]:
    from pdf_info import walk_fonts

    path = pdf_input(a.pdf)
    reader = open_reader(path, a.password)
    numbers = parse_pages(a.pages, len(reader.pages))
    fonts, _, _ = walk_fonts(reader, [n - 1 for n in numbers])
    info: dict[str, Any] = {"fonts": fonts}
    if a.extract:
        folder = output_dir(a.extract)
        saved = []
        done: set[Any] = set()
        for n in numbers:
            res = reader.pages[n - 1].get("/Resources")
            if res is None:
                continue
            fd = res.get_object().get("/Font")
            if fd is None:
                continue
            for _name, ref in fd.get_object().items():
                f = ref.get_object()
                key = getattr(ref, "idnum", id(f))
                if key in done:
                    continue
                done.add(key)
                desc = f
                if f.get("/Subtype") == "/Type0":
                    desc = f["/DescendantFonts"][0].get_object()
                fdesc = desc.get("/FontDescriptor")
                if fdesc is None:
                    continue
                fdesc = fdesc.get_object()
                for k in ("/FontFile", "/FontFile2", "/FontFile3"):
                    if k in fdesc:
                        stream = fdesc[k].get_object()
                        ext = FONT_EXT.get(k[1:])
                        if ext is None:
                            sub = str(stream.get("/Subtype", ""))
                            ext = ".otf" if sub == "/OpenType" else ".cff"
                        base = str(f.get("/BaseFont", "/font"))[1:]
                        target = unique_path(folder, safe_filename(base + ext, "font" + ext))
                        target.write_bytes(stream.get_data())
                        saved.append({"font": base, "file": str(target), "bytes": target.stat().st_size})
                        break
        info["folder"] = str(folder)
        info["saved"] = saved
    return info


def cmd_links(a: Any) -> dict[str, Any]:
    path = pdf_input(a.pdf)
    reader = open_reader(path, a.password)
    numbers = parse_pages(a.pages, len(reader.pages))
    links = []
    for n in numbers:
        page = reader.pages[n - 1]
        geo = geometry_of(page)
        for ref in page.get("/Annots", []) or []:
            try:
                an = ref.get_object()
                if an.get("/Subtype") != "/Link":
                    continue
                x0, y0, x1, y1 = (float(v) for v in an["/Rect"])
                box = [fmt_num(v) for v in geo.rect_to_view(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))]
                target: dict[str, Any] = {}
                act = an.get("/A")
                if act is not None:
                    act = act.get_object()
                    s = str(act.get("/S", ""))
                    if s == "/URI":
                        target = {"url": str(act.get("/URI"))}
                    elif s == "/GoTo":
                        target = {"target_page": _dest_page(reader, act.get("/D"))}
                    elif s == "/GoToR":
                        target = {"file": str(act.get("/F")), "dest": str(act.get("/D"))}
                    elif s == "/Launch":
                        target = {"launch": str(act.get("/F"))}
                    else:
                        target = {"action": s.lstrip("/")}
                elif an.get("/Dest") is not None:
                    target = {"target_page": _dest_page(reader, an.get("/Dest"))}
                links.append({"page": n, "box": box, **target})
            except Exception:  # noqa: BLE001
                continue
    info: dict[str, Any] = {"count": len(links), "links": links}
    budget = a.max_chars or 0
    if budget:
        # Stop on a page boundary and say which command lists the rest.
        used, last = 0, None
        for k, x in enumerate(links):
            used += len(json.dumps(x)) + (12 if a.format == "json" else 0)
            if used > budget and last is not None and x["page"] != last:
                rest = [n for n in numbers if n > last]
                shown = f'"{a.pdf}"' if " " in a.pdf else a.pdf
                info["truncated"] = f"stopped after page {last}: {len(links) - k} more link(s) on later pages. Next: python3 scripts/pdf_extract.py links {shown} --pages {rest[0]}-{rest[-1]}" + (" --format json" if a.format == "json" else "") + (f" --max-chars {a.max_chars}" if a.max_chars != 60_000 else "")
                info["links"] = links[:k]
                info["count_shown"] = k
                break
            last = x["page"]
    return info


def _dest_page(reader: Any, dest: Any) -> int | None:
    try:
        d = dest.get_object() if hasattr(dest, "get_object") else dest
        if isinstance(d, (str, bytes)) or hasattr(d, "original_bytes"):
            named = reader.named_destinations.get(str(d))
            return reader.get_destination_page_number(named) + 1 if named is not None else None
        if isinstance(d, list) and d:
            return reader.get_page_number(d[0].get_object()) + 1
    except Exception:  # noqa: BLE001
        return None
    return None


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sp = sub.add_parser("images", help="extract images")
    sp.add_argument("pdf")
    sp.add_argument("--out", help="folder (default <name>-images)")
    sp.add_argument("--pages")
    sp.add_argument("--min-size", type=int, default=8, help="skip images smaller than this many pixels on a side (default 8)")
    sp.add_argument("--png", action="store_true", help="save everything as PNG")
    sp.add_argument("--keep-jp2", action="store_true", help="keep JPEG 2000 as .jp2 (default: convert to PNG)")
    sp.add_argument("--password")
    add_format(sp)
    sp = sub.add_parser("attachments", help="extract attached files")
    sp.add_argument("pdf")
    sp.add_argument("--out", help="folder (default <name>-attachments)")
    sp.add_argument("--name", help="only this attachment")
    sp.add_argument("--list", action="store_true", help="list without saving")
    sp.add_argument("--password")
    add_format(sp)
    sp = sub.add_parser("fonts", help="list fonts, optionally save embedded font files")
    sp.add_argument("pdf")
    sp.add_argument("--pages")
    sp.add_argument("--extract", metavar="DIR", help="save embedded font programs into DIR")
    sp.add_argument("--password")
    add_format(sp)
    sp = sub.add_parser("links", help="list links")
    sp.add_argument("pdf")
    sp.add_argument("--pages")
    sp.add_argument("--max-chars", type=int, default=60_000, help="output budget (0 = no limit); the output ends with the command for the rest")
    sp.add_argument("--password")
    add_format(sp)
    a = p.parse_args()
    handler = {"images": cmd_images, "attachments": cmd_attachments, "fonts": cmd_fonts, "links": cmd_links}[a.cmd]
    info = handler(a)

    def md(d: dict[str, Any]) -> str:
        if a.cmd == "images":
            rows = [[i["page"], Path(i["file"]).name, f"{i['width']}×{i['height']}", "same as above" if i.get("duplicate") else human_size(i["bytes"]), ", ".join(map(str, i["box"])) if i.get("box") else ""] for i in d["images"]]
            head = f"saved {d['saved']} image(s) to {d['folder']} ({d['placements']} placements"
            head += f"; {d['skipped_small']} tiny images skipped" if d["skipped_small"] else ""
            head += ")"
            tail = ("\nFailed: " + "; ".join(d["failed"])) if d["failed"] else ""
            return head + ("\n\n" + md_table(["page", "file", "pixels", "size", "box (x0, top, x1, bottom)"], rows) if rows else "") + tail
        if a.cmd == "attachments":
            items = d["attachments"]
            if not items:
                return "no attachments"
            if "folder" not in d:
                return md_table(["name", "size", "page"], [[i["name"], human_size(i["bytes"]), i["page"] or ""] for i in items])
            return f"saved {len(items)} attachment(s) to {d['folder']}\n\n" + md_table(["name", "file", "size"], [[i["name"], i["file"], human_size(i["bytes"])] for i in items])
        if a.cmd == "fonts":
            text = md_table(["font", "type", "embedded", "subset", "encoding", "pages"], [[f["name"], f["type"], "yes" if f["embedded"] else "NO", "yes" if f["subset"] else "", f["encoding"] or "", ",".join(map(str, f["pages"][:12])) + (" …" if len(f["pages"]) > 12 else "")] for f in d["fonts"]]) if d["fonts"] else "no fonts (the pages have no text, or it is drawn as images or paths)"
            if "saved" in d:
                text += f"\n\nsaved {len(d['saved'])} font file(s) to {d['folder']}"
            return text
        def target(x: dict[str, Any]) -> str:
            if x.get("url"):
                return x["url"]
            if "target_page" in x:
                return f"page {x['target_page']}" if x["target_page"] else "a page (unresolved)"
            return next((f"{k}: {v}" for k, v in x.items() if k not in ("page", "box")), "")

        rows = [[x["page"], ", ".join(map(str, x["box"])), target(x)] for x in d["links"]]
        more = f"\n\n[… {d['truncated']}]" if d.get("truncated") else ""
        return (f"{d['count']} link(s)\n\n" + md_table(["page", "box", "target"], rows) + more) if rows else "no links"

    emit(info, a.format, md)
    return 0


if __name__ == "__main__":
    run_main(main)
