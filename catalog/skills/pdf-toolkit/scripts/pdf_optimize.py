#!/usr/bin/env python3
"""Make a PDF smaller: compress content streams, merge duplicate objects, drop unused objects, thumbnails and
application data, and downsample and recompress images to a target resolution (measured from how large each image
is drawn on the page). Reports the size before and after; the output is never larger than the input.

Presets (image resolution / JPEG quality): screen 96 dpi / 60, ebook 150 dpi / 75 (default), print 300 dpi / 90,
lossless (no image changes).

Examples:
  python3 scripts/pdf_optimize.py big.pdf small.pdf
  python3 scripts/pdf_optimize.py scan.pdf email.pdf --preset screen
  python3 scripts/pdf_optimize.py report.pdf out.pdf --preset lossless --strip-metadata
  python3 scripts/pdf_optimize.py photos.pdf out.pdf --dpi 200 --quality 85 --grayscale
"""

from __future__ import annotations

import io
from typing import Any

from _common import UsageError, human_size, output_path, parser, pool_map, run_main
from _pdfkit import compress_writer, emit, new_writer, open_reader, pdf_input, save_bytes, writer_bytes

PRESETS = {"screen": (96, 60), "ebook": (150, 75), "print": (300, 90), "lossless": (None, None)}
SIMPLE_SPACES = {"/DeviceRGB": "RGB", "/DeviceGray": "L"}


def _space_mode(xo: Any) -> str | None:
    cs = xo.get("/ColorSpace")
    if cs is None:
        return None
    cs = cs.get_object()
    if isinstance(cs, str) and str(cs) in SIMPLE_SPACES:
        return SIMPLE_SPACES[str(cs)]
    if isinstance(cs, list) and cs and str(cs[0]) == "/ICCBased":
        try:
            n = int(cs[1].get_object().get("/N", 0))
        except Exception:  # noqa: BLE001
            return None
        return {1: "L", 3: "RGB"}.get(n)
    return None


def _recompress(job: dict[str, Any]) -> dict[str, Any] | None:
    """Decodes, downsamples and re-encodes one image (runs in a thread); None when nothing is gained."""
    from PIL import Image

    img: Image.Image = job["image"]
    mode = job["mode"]
    target = job["target_px"]
    was_jpeg = job["was_jpeg"]
    im = img.convert("L" if job["gray"] else mode)
    resized = False
    if target and max(im.size) > target:
        ratio = target / max(im.size)
        im = im.resize((max(1, round(im.size[0] * ratio)), max(1, round(im.size[1] * ratio))), Image.LANCZOS)
        resized = True
    buf = io.BytesIO()
    photo = was_jpeg or _is_photo(im)
    if photo and job["quality"]:
        im.save(buf, "JPEG", quality=job["quality"], optimize=True, progressive=False)
        filt = "/DCTDecode"
        data = buf.getvalue()
    else:
        import zlib

        data = zlib.compress(im.tobytes(), 9)
        filt = "/FlateDecode"
    if not resized and len(data) >= job["old_bytes"] * 0.9 and not job["gray"]:
        return None
    smask = None
    if job.get("smask") is not None:
        import zlib

        m = job["smask"].convert("L")
        if m.size != im.size:
            m = m.resize(im.size, Image.LANCZOS)
        smask = (zlib.compress(m.tobytes(), 9), m.size)
    return {"id": job["id"], "data": data, "filter": filt, "size": im.size, "mode": im.mode, "smask": smask}


def _is_photo(im: Any) -> bool:
    """Many distinct colours → photographic (JPEG); few → line art (lossless)."""
    small = im.copy()
    small.thumbnail((96, 96))
    colors = small.getcolors(maxcolors=4096)
    return colors is None or len(colors) > 256


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("input")
    p.add_argument("out")
    p.add_argument("--preset", choices=list(PRESETS), default="ebook")
    p.add_argument("--dpi", type=float, help="target image resolution (overrides the preset)")
    p.add_argument("--quality", type=int, help="JPEG quality 1-95 (overrides the preset)")
    p.add_argument("--grayscale", action="store_true", help="convert images to grayscale")
    p.add_argument("--strip-metadata", action="store_true", help="remove document metadata (Info and XMP)")
    p.add_argument("--keep-thumbnails", action="store_true", help="keep embedded page thumbnails")
    p.add_argument("--password")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()
    src = pdf_input(a.input)
    out = output_path(a.out, [src], a.force)
    dpi, quality = PRESETS[a.preset]
    if a.dpi:
        dpi = a.dpi
    if a.quality:
        if not 1 <= a.quality <= 95:
            raise UsageError("--quality must be between 1 and 95")
        quality = a.quality
    before = src.stat().st_size
    reader = open_reader(src, a.password)
    from pypdf import PdfWriter
    from pypdf.generic import EncodedStreamObject, NameObject, NumberObject

    from _content import image_placements

    writer = new_writer(reader)
    removed = {"thumbnails": 0, "piece_info": 0}
    for page in writer.pages:
        if not a.keep_thumbnails and "/Thumb" in page:
            del page["/Thumb"]
            removed["thumbnails"] += 1
        if "/PieceInfo" in page:
            del page["/PieceInfo"]
            removed["piece_info"] += 1
    if "/PieceInfo" in writer._root_object:
        del writer._root_object["/PieceInfo"]
        removed["piece_info"] += 1
    images_done: list[dict[str, Any]] = []
    image_bytes_before = image_bytes_after = 0
    skipped: dict[str, int] = {}
    if dpi or quality or a.grayscale:
        placements: dict[Any, float] = {}
        for page in writer.pages:
            try:
                for k, v in image_placements(page, writer).items():
                    placements[k] = max(placements.get(k, 0.0), v)
            except Exception:  # noqa: BLE001 — unusual content: leave its images alone
                continue
        from pypdf.generic._image_xobject import _xobj_to_image

        jobs = []
        for idnum, shown_pt in placements.items():
            if not isinstance(idnum, int):
                continue
            xo = writer.get_object(idnum)
            if xo is None or xo.get("/Subtype") != "/Image" or xo.get("/ImageMask"):
                continue
            filters = xo.get("/Filter")
            flist = [str(f) for f in (filters if isinstance(filters, list) else [filters] if filters else [])]
            mode = _space_mode(xo)
            bpc = int(xo.get("/BitsPerComponent", 8) or 8)
            why = None
            if mode is None:
                why = "colour space"
            elif bpc != 8:
                why = "bit depth"
            elif any(f in ("/JBIG2Decode", "/CCITTFaxDecode", "/JPXDecode") for f in flist):
                why = "codec"
            elif "/Decode" in xo:
                why = "decode array"
            if why:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            w, h = int(xo["/Width"]), int(xo["/Height"])
            eff = max(w, h) / max(shown_pt / 72.0, 1e-6)
            target_px = round(max(w, h) * dpi / eff) if dpi and eff > dpi * 1.15 else None
            old_bytes = len(xo._data or b"") if hasattr(xo, "_data") else 0  # noqa: SLF001
            is_jpeg = "/DCTDecode" in flist
            worth = bool(target_px) or a.grayscale or (quality is not None and ((is_jpeg and quality < 85) or (not is_jpeg and old_bytes > 50_000)))
            if not worth:
                continue
            smask_ref = xo.get("/SMask")
            try:
                if smask_ref is not None:
                    del xo["/SMask"]
                _e, _r, img = _xobj_to_image(xo)
            except Exception:  # noqa: BLE001
                skipped["undecodable"] = skipped.get("undecodable", 0) + 1
                continue
            finally:
                if smask_ref is not None:
                    xo[NameObject("/SMask")] = smask_ref
            smask_img = None
            if smask_ref is not None:
                try:
                    smask_img = _xobj_to_image(smask_ref.get_object())[2]
                except Exception:  # noqa: BLE001
                    skipped["soft mask"] = skipped.get("soft mask", 0) + 1
                    continue
            jobs.append({"id": idnum, "image": img, "mode": mode, "target_px": target_px, "quality": quality, "was_jpeg": "/DCTDecode" in flist, "old_bytes": old_bytes, "gray": a.grayscale, "smask": smask_img, "eff_dpi": round(eff)})
        results = pool_map(_recompress, jobs, threads=True)
        for job, res in zip(jobs, results):
            if res is None:
                continue
            xo = writer.get_object(job["id"])
            new = EncodedStreamObject()
            for k, v in xo.items():
                if k not in ("/Filter", "/DecodeParms", "/Length", "/Width", "/Height", "/ColorSpace", "/BitsPerComponent", "/SMask"):
                    new[NameObject(k)] = v
            new[NameObject("/Width")] = NumberObject(res["size"][0])
            new[NameObject("/Height")] = NumberObject(res["size"][1])
            new[NameObject("/BitsPerComponent")] = NumberObject(8)
            new[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if res["mode"] == "L" else "/DeviceRGB")
            new[NameObject("/Filter")] = NameObject(res["filter"])
            new._data = res["data"]  # noqa: SLF001
            if res["smask"] is not None:
                sm = EncodedStreamObject()
                sm[NameObject("/Type")] = NameObject("/XObject")
                sm[NameObject("/Subtype")] = NameObject("/Image")
                sm[NameObject("/Width")] = NumberObject(res["smask"][1][0])
                sm[NameObject("/Height")] = NumberObject(res["smask"][1][1])
                sm[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
                sm[NameObject("/BitsPerComponent")] = NumberObject(8)
                sm[NameObject("/Filter")] = NameObject("/FlateDecode")
                sm._data = res["smask"][0]  # noqa: SLF001
                new[NameObject("/SMask")] = writer._add_object(sm)
            ref = writer.get_object(job["id"]).indirect_reference
            writer._replace_object(ref, new)
            image_bytes_before += job["old_bytes"]
            image_bytes_after += len(res["data"])
            images_done.append({"object": job["id"], "from": list(job["image"].size), "to": list(res["size"]), "effective_dpi": job["eff_dpi"], "bytes_before": job["old_bytes"], "bytes_after": len(res["data"])})
    for page in writer.pages:
        try:
            page.compress_content_streams(level=9)
        except Exception:  # noqa: BLE001
            pass
    if a.strip_metadata:
        if writer._info is not None:
            info = writer._info.get_object()
            for k in list(info.keys()):
                del info[k]
        if "/Metadata" in writer._root_object:
            del writer._root_object["/Metadata"]
    compress_writer(writer)
    data = writer_bytes(writer)
    kept_original = False
    if len(data) >= before and not a.strip_metadata:
        data = src.read_bytes()
        kept_original = True
    save_bytes(data, out)
    after = len(data)
    info: dict[str, Any] = {
        "output": str(out),
        "before": before,
        "after": after,
        "saved_percent": round(100 * (before - after) / before, 1) if before else 0,
        "preset": a.preset,
        "target_dpi": dpi,
        "jpeg_quality": quality,
        "images_recompressed": len(images_done),
        "image_bytes": [image_bytes_before, image_bytes_after],
        "images_skipped": skipped,
        "removed": {k: v for k, v in removed.items() if v},
        "kept_original": kept_original,
    }
    if a.format == "json":
        info["images"] = images_done
        emit(info, "json")
        return 0
    line = f"wrote {out}: {human_size(before)} → {human_size(after)} ({info['saved_percent']}% smaller)"
    if kept_original:
        line = f"wrote {out}: no gain possible ({human_size(before)}); the output is a copy of the input"
    print(line)
    if images_done:
        print(f"images: {len(images_done)} recompressed ({human_size(image_bytes_before)} → {human_size(image_bytes_after)}), target {dpi or 'unchanged'} dpi, JPEG quality {quality}")
    if skipped:
        print("images left as they were: " + ", ".join(f"{v} ({k})" for k, v in skipped.items()))
    if info["removed"]:
        print("removed: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in info["removed"].items()))
    print("Check the result: render a page with images (pdf_render.py) and look at it with view_image.")
    return 0


if __name__ == "__main__":
    run_main(main)
