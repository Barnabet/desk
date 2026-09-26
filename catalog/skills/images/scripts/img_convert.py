#!/usr/bin/env python3
"""Convert images between formats, one file or whole folders in parallel.

Reads everything img_view reads (HEIC, AVIF, RAW, SVG, PSD, TIFF pages, animations…). Writes png, jpg, webp, avif,
heic, tif, gif, bmp, ico (multi-size), icns, pdf (one page per image, lossless for graphics), apng, jp2, tga, qoi.
Photos are turned upright and their EXIF orientation reset. Metadata (EXIF, XMP, colour profile) is kept unless
--strip, which also converts wide-gamut pixels to sRGB so colours stay right. Animations stay animated when the
target can hold them (gif, webp, apng, avif); multi-page TIFFs stay multi-page in tif and pdf.

Examples:
  python3 scripts/img_convert.py IMG_0042.HEIC --to jpg --out IMG_0042.jpg
  python3 scripts/img_convert.py photos/ -r --to webp --quality 80 --max-edge 2000 --strip --out-dir web/
  python3 scripts/img_convert.py logo.svg --to png --width 1024 --out logo-1024.png
  python3 scripts/img_convert.py logo.png --to ico --sizes 16,32,48,256 --out favicon.ico
  python3 scripts/img_convert.py scan1.jpg scan2.png --out scans.pdf --pdf-page a4
  python3 scripts/img_convert.py DSC_0001.NEF --to tif --raw-wb auto --out DSC_0001.tif
  python3 scripts/img_convert.py pages.tif --to png --split --out-dir pages/

Outputs: --out FILE for one input (or many inputs combined into one pdf/tif), else --out-dir (default ./converted),
mirroring subfolders. Existing files are refused unless --force; inputs are never overwritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, md_table, output_dir, output_path, parser, run_main


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="+", help="image files, folders or globs")
    p.add_argument("--to", help="target format (png, jpg, webp, avif, heic, tif, gif, bmp, ico, icns, pdf, apng, jp2, tga, qoi); default from --out")
    p.add_argument("--out", help="output file (one input, or several combined into one pdf/tif)")
    p.add_argument("--out-dir", help="output folder for batches (default ./converted)")
    p.add_argument("--quality", type=int, help="lossy quality 1-100 (defaults: jpg 88, webp 85, avif 65, heic 80)")
    p.add_argument("--lossless", action="store_true", help="exact pixels in webp and heic (and pdf pages); avif gets near-lossless 4:4:4 at quality 100")
    p.add_argument("--strip", action="store_true", help="drop EXIF (incl. GPS), XMP, IPTC and comments; convert to sRGB")
    p.add_argument("--srgb", action="store_true", help="convert pixels from their colour profile to sRGB")
    p.add_argument("--width", type=int, help="resize to this width (keeps aspect); for SVG, render at this width")
    p.add_argument("--height", type=int, help="resize to this height (keeps aspect); for SVG, render at this height")
    p.add_argument("--max-edge", type=int, help="shrink so the long edge is at most this")
    p.add_argument("--scale", type=float, help="scale factor (SVG: render zoom, e.g. 2 for @2x)")
    p.add_argument("--upscale", action="store_true", help="allow --width/--height to enlarge raster images")
    p.add_argument("--dpi", type=float, help="DPI to record in the output (SVG: render at this DPI; pdf: page size)")
    p.add_argument("--bg", default="white", help="colour behind transparency for formats without alpha (default white)")
    p.add_argument("--page", type=int, help="convert only this frame/page (1-based)")
    p.add_argument("--split", action="store_true", help="write every frame/page as its own file (name-001.png …)")
    p.add_argument("--sizes", default="16,24,32,48,64,128,256", help="ico sizes (default 16,24,32,48,64,128,256)")
    p.add_argument("--colors", type=int, default=256, help="gif palette size (default 256)")
    p.add_argument("--effort", type=int, help="webp method 0-6 / avif speed 0-10 (smaller files are slower)")
    p.add_argument("--tiff-compression", choices=["lzw", "deflate", "jpeg", "none"], default="lzw", help="tif compression (default lzw)")
    p.add_argument("--pdf-page", default="fit", help="pdf page: fit (image size), a4, letter, a3, a5, legal, tabloid (+ -landscape/-portrait)")
    p.add_argument("--pdf-margin", type=float, help="pdf margin in points (default 36 on paper sizes, 0 for fit)")
    p.add_argument("--raw-wb", choices=["camera", "auto", "daylight"], default="camera", help="RAW white balance (default camera)")
    p.add_argument("--raw-bright", type=float, default=1.0, help="RAW brightness multiplier (default 1.0)")
    p.add_argument("--raw-half", action="store_true", help="develop RAW at half size (4x faster)")
    p.add_argument("--no-orient", action="store_true", help="keep the stored orientation (do not apply EXIF orientation)")
    p.add_argument("-r", "--recursive", action="store_true", help="include subfolders of folder inputs")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    p.add_argument("--workers", type=int, help="parallel workers")
    p.add_argument("--max-chars", type=int, default=60000, help="text budget for the report of a big batch (default 60000; 0 = no limit)")
    add_format(p)
    return p


TIFF_COMP = {"lzw": "tiff_lzw", "deflate": "tiff_adobe_deflate", "jpeg": "jpeg", "none": "raw"}
NO_ICC_FORMATS = {"GIF", "BMP", "ICO", "ICNS", "TGA", "QOI", "PPM", "PDF"}


def load_frames(path: Path, job: dict[str, Any], want_all: bool) -> tuple[list[Any], list[int], Any]:
    """The frames to write (one unless the target keeps several), their durations, and the Loaded record."""
    from _img import RawOpts, SvgOpts, iter_frames, kind_of, load

    kind = kind_of(path)
    raw = RawOpts(wb=job["raw_wb"], bright=job["raw_bright"], half=job["raw_half"], use_preview=False)
    svg = SvgOpts(width=job["width"], height=job["height"], scale=job["scale"], dpi=job["dpi"])
    if kind == "svg" and job["fmt"] in ("ICO", "ICNS") and not (job["width"] or job["height"] or job["scale"]):
        svg.width = svg.height = 1024 if job["fmt"] == "ICNS" else 256
    page = (job["page"] or 1) - 1
    # --max-edge lets a big JPEG decode at 1/2-1/8 scale (DCT scaling) and other huge images shrink right after
    # decoding; the final LANCZOS resize still makes the exact size.
    hint = job["max_edge"] if job["max_edge"] and not job["scale"] and kind != "svg" else None
    ld = load(path, frame=page, orient=not job["no_orient"], raw=raw, svg=svg, max_edge=hint)
    frames, durations = [ld.img], [int(ld.info.get("duration", 0) or 0)]
    if want_all and ld.n_frames > 1 and kind == "raster" and job["page"] is None:
        frames, durations = [], []
        from _img import apply_orientation

        for _i, fr, dur in iter_frames(path):
            if not job["no_orient"] and ld.oriented != 1:
                fr.info["exif"] = ld.info.get("exif", b"")
                fr, _ = apply_orientation(fr)
            frames.append(fr)
            durations.append(dur)
    return frames, durations, ld


def convert_one(job: dict[str, Any]) -> dict[str, Any]:
    """Converts one input to one output (or several with split). Top-level for worker processes."""
    from _img import SaveOpts, fit_within, meta_from, pil, save_image, to_srgb

    Image = pil()
    path = Path(job["input"])
    fmt = job["fmt"]
    rec: dict[str, Any] = {"input": str(path), "bytes_in": path.stat().st_size}
    try:
        from _img import ANIMATED_FORMATS, frame_count, _open, kind_of

        src_fmt, n_src = "", 1
        if kind_of(path) == "raster":
            probe = _open(path)
            try:
                src_fmt, n_src = str(probe.format), frame_count(probe)
            finally:
                probe.close()
        animated_src = src_fmt in ANIMATED_FORMATS and n_src > 1
        # Animations stay animations; pages (TIFF) stay pages in TIFF and PDF only.
        keeps_frames = (fmt in ("GIF", "WEBP", "AVIF") and animated_src) or fmt in ("TIFF", "PDF") or (job["apng"] and animated_src) or job["split"]
        frames, durations, ld = load_frames(path, job, keeps_frames)
        rec["from"] = ld.format
        rec["frames"] = ld.n_frames
        src_info = dict(ld.info)
        meta = meta_from(src_info, orientation_applied=ld.oriented != 1)
        icc = meta["icc"]
        converted_colour = False
        need_srgb = job["srgb"] or job["strip"] or fmt in NO_ICC_FORMATS
        out_frames = []
        for fr in frames:
            if need_srgb and icc:
                fr2, did = to_srgb(fr, icc)
                converted_colour = converted_colour or did
                fr = fr2
            if ld.kind != "svg" and (job["width"] or job["height"] or job["max_edge"] or job["scale"]):
                w, h = fr.size
                tw, th = job["width"], job["height"]
                if job["scale"]:
                    tw, th = round(w * job["scale"]), round(h * job["scale"])
                    new = (max(1, tw), max(1, th))
                else:
                    new = fit_within((w, h), tw, th, upscale=job["upscale"])
                if job["max_edge"]:
                    new = fit_within(new, job["max_edge"], job["max_edge"])
                if new != (w, h):
                    fr = fr.resize(new, Image.LANCZOS)
            out_frames.append(fr)
        if converted_colour or job["strip"]:
            icc = None if (converted_colour or job["strip"]) else icc
        dpi = (job["dpi"], job["dpi"]) if job["dpi"] and ld.kind != "svg" else meta["dpi"]
        if job["dpi"] and ld.kind == "svg":
            dpi = (job["dpi"], job["dpi"])
        opts = SaveOpts(
            quality=job["quality"], lossless=job["lossless"], strip=job["strip"], exif=meta["exif"], xmp=meta["xmp"], icc=icc,
            dpi=dpi, bg=job["bg"], ico_sizes=job["sizes"], durations=durations if len(out_frames) > 1 else None,
            loop=int(src_info.get("loop", 0) or 0), effort=job["effort"], colors=job["colors"],
            tiff_compression=TIFF_COMP[job["tiff_compression"]], pdf_page=job["pdf_page"], pdf_margin=job["pdf_margin"],
        )
        outs = []
        if job["split"] and len(out_frames) > 1:
            base = Path(job["out"])
            width = max(3, len(str(len(out_frames))))
            for i, fr in enumerate(out_frames, 1):
                o = base.with_name(f"{base.stem}-{i:0{width}d}{base.suffix}")
                o = output_path(o, [path], job["force"])
                save_image([fr], o, fmt, opts)
                outs.append(o)
        else:
            if not keeps_frames or (fmt == "PNG" and not job["apng"]):
                out_frames = out_frames[:1]
                if ld.note:
                    rec["note"] = ld.note
                elif ld.n_frames > 1 and job["page"] is None:
                    what = "frames" if animated_src else "pages"
                    rec["note"] = f"{ld.n_frames} {what}: wrote the first (" + ("--to apng, " if animated_src else "--to tif or pdf, ") + "--split or --page N for others)"
            o = output_path(job["out"], [path], job["force"])
            unchanged_jpeg = (
                fmt == "PDF" and ld.format == "JPEG" and len(out_frames) == 1 and ld.oriented == 1 and not converted_colour
                and out_frames[0] is frames[0] and out_frames[0].mode in ("RGB", "L")
            )
            if unchanged_jpeg:
                from _pdfimg import images_to_pdf

                images_to_pdf(out_frames, o, dpi=dpi, page=job["pdf_page"], margin=job["pdf_margin"], jpeg_sources=[path.read_bytes()])
                rec["note"] = "JPEG embedded as is (no re-encoding)"
            else:
                save_image(out_frames, o, fmt, opts)
            outs.append(o)
        rec["outputs"] = [str(o) for o in outs]
        rec["output"] = str(outs[0])
        rec["bytes_out"] = sum(o.stat().st_size for o in outs)
        rec["size"] = list(out_frames[0].size)
        rec["written_frames"] = len(out_frames) if not job["split"] else 1
        rec["to"] = fmt
        if job["lossless"] and fmt == "AVIF":
            rec["note"] = "; ".join(x for x in (rec.get("note"), "AVIF --lossless is near-lossless here (4:4:4, quality 100: values may differ by a few levels); webp, png or heic keep exact pixels") if x)
        if converted_colour:
            rec["colour"] = "converted to sRGB"
    except SkillError as e:
        rec["error"] = str(e)
    except Exception as e:  # noqa: BLE001 — one bad file must not stop a batch
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def combine(inputs: list[Any], out: Path, fmt: str, job: dict[str, Any]) -> dict[str, Any]:
    """Several inputs → one multi-page pdf or tif (every page of multi-page inputs included)."""
    from _img import SaveOpts, save_image, to_srgb

    pages, sources = [], []
    for inp in inputs:
        frames, _d, ld = load_frames(inp.path, {**job, "page": None}, True)
        for fr in frames:
            icc = ld.info.get("icc_profile")
            if icc:
                fr, _ = to_srgb(fr, icc)
            pages.append(fr)
            passthrough = fmt == "PDF" and ld.format == "JPEG" and ld.oriented == 1 and len(frames) == 1 and fr.mode in ("RGB", "L") and not icc
            sources.append(inp.path.read_bytes() if passthrough else None)
    o = output_path(out, [i.path for i in inputs], job["force"])
    if fmt == "PDF":
        from _pdfimg import images_to_pdf

        images_to_pdf(pages, o, dpi=(job["dpi"], job["dpi"]) if job["dpi"] else None, quality=job["quality"], page=job["pdf_page"], margin=job["pdf_margin"], lossless=job["lossless"], jpeg_sources=sources)
    else:
        save_image(pages, o, fmt, SaveOpts(quality=job["quality"], strip=True, tiff_compression=TIFF_COMP[job["tiff_compression"]], dpi=(job["dpi"], job["dpi"]) if job["dpi"] else None))
    return {"inputs": [str(i.path) for i in inputs], "output": str(o), "pages": len(pages), "bytes_out": o.stat().st_size, "to": fmt}


def main() -> int:
    from _img import expand_inputs, out_format

    args = build_parser().parse_args()
    if args.quality is not None and not 1 <= args.quality <= 100:
        raise UsageError("--quality must be 1-100")
    if not args.to and not args.out:
        raise UsageError("say the target format with --to (or give --out with an extension)")
    to_key = (args.to or Path(args.out).suffix).lower().lstrip(".")
    fmt, ext = out_format(to_key, None)
    apng = to_key == "apng"
    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    try:
        sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    except ValueError:
        raise UsageError("--sizes takes numbers like 16,32,48") from None
    job_base = {
        "fmt": fmt, "apng": apng, "quality": args.quality, "lossless": args.lossless, "strip": args.strip, "srgb": args.srgb,
        "width": args.width, "height": args.height, "max_edge": args.max_edge, "scale": args.scale, "upscale": args.upscale,
        "dpi": args.dpi, "bg": args.bg, "page": args.page, "split": args.split, "sizes": sizes, "colors": args.colors,
        "effort": args.effort, "tiff_compression": args.tiff_compression, "pdf_page": args.pdf_page, "pdf_margin": args.pdf_margin,
        "raw_wb": args.raw_wb, "raw_bright": args.raw_bright, "raw_half": args.raw_half, "no_orient": args.no_orient, "force": args.force,
    }
    if args.out and len(inputs) > 1:
        if fmt not in ("PDF", "TIFF"):
            raise UsageError("several inputs go into one file only for pdf or tif; use --out-dir (animations: img_compose.py animate)")
        rec = combine(inputs, Path(args.out), fmt, job_base)
        if args.format == "json":
            emit(rec)
        else:
            print(f"{rec['output']}: {rec['pages']} page(s) from {len(inputs)} file(s), {human_size(rec['bytes_out'])}")
        return 0
    jobs = []
    taken: dict[str, str] = {}
    out_root = None if args.out else output_dir(args.out_dir or "converted")
    for inp in inputs:
        if args.out:
            out = Path(args.out)
            if out.suffix == "":
                out = out.with_suffix(ext)
        else:
            assert out_root is not None
            rel = inp.rel
            out = out_root / rel.with_suffix(ext if not apng else ".png")
            key = str(out).lower()
            if key in taken:
                out = out_root / rel.with_name(f"{rel.stem}-{rel.suffix.lstrip('.').lower()}{ext}")
                key = str(out).lower()
            taken[key] = str(inp.path)
        jobs.append({**job_base, "input": str(inp.path), "out": str(out)})
    from _img import batch_map

    recs = batch_map(convert_one, jobs, workers=args.workers)
    if args.format == "json":
        emit(recs[0] if len(recs) == 1 else recs, max_chars=args.max_chars or None)
    else:
        rows = []
        for r in recs:
            if "error" in r:
                rows.append([r["input"], "error: " + r["error"], "", ""])
                continue
            extra = "; ".join(x for x in (r.get("note"), r.get("colour")) if x)
            out_s = r["output"] if len(r["outputs"]) == 1 else f"{r['outputs'][0]} … ({len(r['outputs'])} files)"
            rows.append([r["input"], out_s, f"{r['size'][0]}×{r['size'][1]}", f"{human_size(r['bytes_in'])} → {human_size(r['bytes_out'])}" + (f" ({extra})" if extra else "")])
        ok = sum(1 for r in recs if "error" not in r)
        emit(recs, "md", lambda _: f"{ok} of {len(recs)} converted to {fmt}.\n\n" + md_table(["input", "output", "size", "bytes"], rows), max_chars=args.max_chars or None, hint="The files are all written; --max-chars 0 lists every one.")
    return 0 if all("error" not in r for r in recs) else 1


if __name__ == "__main__":
    run_main(main)
