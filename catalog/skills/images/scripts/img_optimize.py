#!/usr/bin/env python3
"""Shrink images for the web or email: pick the format, hit a size or quality target, resize, strip metadata.

For each image the script tries the candidate formats, searches the quality (binary search) that meets the target,
and keeps the smallest result. PNGs are palette-quantised when that is invisible and always recompressed with
oxipng. Every result reports its size, the saving and an SSIM score against the original (1.0 = identical), so you
can judge the quality without guessing. If nothing beats the original, the original bytes are copied unchanged.

Targets (combine with --max-edge / --max-width / --max-height):
  --target-kb N     the best quality that fits in N kilobytes (downscales too if even low quality is too big)
  --min-ssim S      the smallest file whose SSIM is at least S (0.95 is visually very close, 0.98 near-transparent)
  --quality Q       a fixed quality (default: jpg 82, webp 80, avif 55)

Formats (--to): auto (web: WebP or AVIF vs JPEG/PNG, smallest wins), email (JPEG or PNG only), keep (same format),
or webp, avif, jpg, png. Animated GIFs become animated WebP with auto (much smaller) or stay GIF with keep.

Examples:
  python3 scripts/img_optimize.py hero.png --out hero.webp --max-edge 1920 --min-ssim 0.97
  python3 scripts/img_optimize.py photos/ -r --to email --target-kb 300 --max-edge 1600 --out-dir for-email/
  python3 scripts/img_optimize.py screenshot.png --to png --out screenshot-small.png
  python3 scripts/img_optimize.py banner.gif --to auto --out-dir web/

Metadata (EXIF, GPS, XMP) is stripped unless --keep-metadata; colours are converted to sRGB. Inputs are never touched.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, md_table, output_dir, output_path, parser, run_main

DEFAULT_Q = {"JPEG": 82, "WEBP": 80, "AVIF": 55}
EXT = {"JPEG": ".jpg", "WEBP": ".webp", "AVIF": ".avif", "PNG": ".png", "GIF": ".gif"}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="+", help="image files, folders or globs")
    p.add_argument("--out", help="output file (one input)")
    p.add_argument("--out-dir", help="output folder (default ./optimized), mirroring subfolders")
    p.add_argument("--to", default="auto", help="auto, email, keep, webp, avif, jpg or png (default auto)")
    p.add_argument("--target-kb", type=float, help="largest acceptable file size in KB")
    p.add_argument("--min-ssim", type=float, help="lowest acceptable SSIM (0-1) against the original")
    p.add_argument("--quality", type=int, help="fixed quality 1-100")
    p.add_argument("--max-edge", type=int, help="shrink so the long edge is at most this")
    p.add_argument("--max-width", type=int)
    p.add_argument("--max-height", type=int)
    p.add_argument("--colors", type=int, help="PNG palette size (default: 256 when quantising is invisible)")
    p.add_argument("--lossless", action="store_true", help="lossless only (PNG with oxipng, lossless WebP)")
    p.add_argument("--keep-metadata", action="store_true", help="keep EXIF/XMP (default: strip, incl. GPS)")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    p.add_argument("--workers", type=int)
    p.add_argument("--max-chars", type=int, default=60000, help="text budget for the report of a big batch (default 60000; 0 = no limit)")
    add_format(p)
    return p


# ── encoding ────────────────────────────────────────────────────────────


def oxipng_level(pixels: int) -> int:
    """oxipng effort by image size: thorough for normal images, light for big ones, none for huge ones."""
    return 2 if pixels <= 4_000_000 else 1 if pixels <= 16_000_000 else 0


def png_optimize(data: bytes, pixels: int, keep_meta: bool = False) -> bytes:
    level = oxipng_level(pixels)
    if level == 0:
        return data
    try:
        import oxipng

        out = oxipng.optimize_from_memory(data, level=level, strip=oxipng.StripChunks.none() if keep_meta else oxipng.StripChunks.safe())
        return out if len(out) < len(data) else data
    except Exception:  # noqa: BLE001 — oxipng is an optimisation, never a requirement
        return data


def encode(img: Any, fmt: str, q: int | None, *, lossless: bool = False, colors: int | None = None, exif: bytes | None = None, frames: list[Any] | None = None, durations: list[int] | None = None, loop: int = 0, png_opt: bool = True) -> bytes:
    """Encodes to bytes (frames → animated WebP/GIF/AVIF)."""
    from _img import gif_frames, has_alpha

    buf = io.BytesIO()
    kw: dict[str, Any] = {}
    if exif:
        kw["exif"] = exif
    if frames and len(frames) > 1:
        if fmt == "GIF":
            fr = gif_frames(frames, colors or 256)
            extra = {"transparency": fr[0].info["transparency"], "disposal": 2} if "transparency" in fr[0].info else {}
            fr[0].save(buf, "GIF", save_all=True, append_images=fr[1:], duration=durations, loop=loop, optimize=True, **extra)
        else:
            mode = "RGBA" if any(has_alpha(f) for f in frames) else "RGB"
            fr = [f.convert(mode) for f in frames]
            if fmt == "WEBP":
                fr[0].save(buf, "WEBP", save_all=True, append_images=fr[1:], duration=durations, loop=loop, quality=q or 80, lossless=lossless, method=4, minimize_size=True)
            else:
                fr[0].save(buf, fmt, save_all=True, append_images=fr[1:], duration=durations, loop=loop, quality=q or 55)
        return buf.getvalue()
    if fmt == "JPEG":
        from _img import flatten

        src = img if img.mode in ("RGB", "L") else flatten(img, "white")
        src.save(buf, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0 if q >= 90 else 2, **kw)
    elif fmt == "WEBP":
        big = img.size[0] * img.size[1] > 12_000_000
        img.save(buf, "WEBP", quality=q if not lossless else 100, lossless=lossless, method=4 if big else 5, **kw)
    elif fmt == "AVIF":
        from _img import encoder_threads

        if encoder_threads():
            kw["max_threads"] = encoder_threads()
        # Speed 6 is 10× faster than 5 for about the same size at equal SSIM; 8 for very large images.
        img.save(buf, "AVIF", quality=q, speed=6 if img.size[0] * img.size[1] <= 12_000_000 else 8, **kw)
    elif fmt == "PNG":
        src = img
        if colors:
            from PIL import Image

            if has_alpha(img):
                # Palette with one transparent entry (median cut on the colours); only used for on/off transparency.
                src = gif_frames([img], colors)[0]
            else:
                src = img.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        src.save(buf, "PNG", compress_level=6, **kw)
        data = buf.getvalue()
        return png_optimize(data, img.size[0] * img.size[1], bool(exif)) if png_opt else data
    elif fmt == "GIF":
        from _img import gif_frames

        fr = gif_frames([img], colors or 256)[0]
        fr.save(buf, "GIF", optimize=True, **({"transparency": fr.info["transparency"]} if "transparency" in fr.info else {}))
    else:
        raise UsageError(f"cannot optimise to {fmt}")
    return buf.getvalue()


def strip_jpeg(data: bytes, orientation: int = 1) -> bytes | None:
    """The same JPEG without its metadata, losslessly: EXIF (GPS, camera serial), XMP, IPTC, comments and maker
    segments are dropped; JFIF, the ICC profile and Adobe colour info are kept. A minimal EXIF holding only the
    orientation is written back when the picture needs one to show upright. None if the file is not a clean JPEG."""
    import struct

    if data[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    if orientation not in (0, 1):
        from _img import pil

        ex = pil().Exif()
        ex[0x0112] = orientation
        body = b"Exif\x00\x00" + ex.tobytes()
        out += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out += data[i : i + 2]
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        seg = data[i : i + 2 + length]
        if marker == 0xDA:  # start of scan: image data up to the end-of-image marker (FF D9 cannot occur inside it)
            end = data.find(b"\xff\xd9", i)
            # Anything after it (a second MPO picture, a motion-photo video) goes too: it can carry its own EXIF.
            out += data[i : end + 2] if end > 0 else data[i:]
            return bytes(out)
        keep = not (0xE1 <= marker <= 0xEF or marker == 0xFE) or marker in (0xE2, 0xEE)
        if marker == 0xE2 and not seg[4:16].startswith(b"ICC_PROFILE"):
            keep = False
        if keep:
            out += seg
        i += 2 + length
    return None


def reference(img: Any) -> Any:
    """What results are scored against: the image at ≤1024 px, transparency composited over mid-grey (so alpha errors
    count), as a float array."""
    import numpy as np

    from _img import flatten

    from _img import shrink

    ref = shrink(flatten(img, "#808080"), 1024)  # a new image: never thumbnail() the caller's in place
    return np.asarray(ref.convert("RGB"), dtype=np.float64)


def score(ref: Any, data: bytes) -> float:
    """SSIM of an encoded result against the original: the lowest of the R, G and B channels, so colour banding and
    lost transparency count as much as blur."""
    import numpy as np
    from PIL import Image

    from _img import flatten
    from _metrics import ssim

    im = Image.open(io.BytesIO(data))
    im.load()
    im = flatten(im, "#808080").convert("RGB")
    h, w = ref.shape[:2]
    if im.size != (w, h):
        im = im.resize((w, h), Image.BILINEAR)
    b = np.asarray(im, dtype=np.float64)
    return min(ssim(ref[..., c], b[..., c])[0] for c in range(3))


def search(img: Any, fmt: str, ref: Any, target_bytes: int | None, min_ssim: float | None, q_fixed: int | None, exif: bytes | None) -> tuple[bytes, int, float]:
    """(data, quality, ssim) for a lossy format meeting the targets (binary search over quality)."""
    if q_fixed:
        d = encode(img, fmt, q_fixed, exif=exif)
        return d, q_fixed, score(ref, d)
    lo, hi = 30, 95
    if target_bytes is None and min_ssim is None:
        q = DEFAULT_Q[fmt]
        d = encode(img, fmt, q, exif=exif)
        return d, q, score(ref, d)
    best: tuple[bytes, int, float] | None = None
    if min_ssim is not None and target_bytes is None:
        # Smallest file with SSIM ≥ min_ssim: the lowest passing quality.
        while lo <= hi:
            mid = (lo + hi) // 2
            d = encode(img, fmt, mid, exif=exif)
            s = score(ref, d)
            if s >= min_ssim:
                best, hi = (d, mid, s), mid - 1
            else:
                lo = mid + 1
        if best is None:
            d = encode(img, fmt, 95, exif=exif)
            best = (d, 95, score(ref, d))
        return best
    # Best quality within the size budget (and above min_ssim when both are given).
    floor_checked = False
    while lo <= hi:
        mid = (lo + hi) // 2
        d = encode(img, fmt, mid, exif=exif)
        if len(d) <= target_bytes:  # type: ignore[operator]
            best, lo = (d, mid, -1.0), mid + 1
        else:
            hi = mid - 1
            if not floor_checked and best is None and lo < hi:
                # After a first miss, try the lowest quality once: when even that is too big, stop searching
                # (the caller downscales) instead of encoding five more times.
                floor_checked = True
                d = encode(img, fmt, lo, exif=exif)
                if len(d) > target_bytes:  # type: ignore[operator]
                    return d, lo, score(ref, d)
                best, lo = (d, lo, -1.0), lo + 1
    if best is None:
        d = encode(img, fmt, 30, exif=exif)
        return d, 30, score(ref, d)
    return best[0], best[1], score(ref, best[0])


def optimize_one(job: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    from _img import RawOpts, fit_within, has_alpha, iter_frames, load, meta_from, normalize, to_srgb, uses_alpha

    path = Path(job["input"])
    rec: dict[str, Any] = {"input": str(path), "bytes_in": path.stat().st_size}
    try:
        # A big JPEG decodes at reduced scale and huge images shrink early; RAW files are developed, not previewed.
        ld = load(path, max_edge=job["max_edge"] or None, raw=RawOpts(use_preview=False))
        img = normalize(ld.img)
        img, _ = to_srgb(img, ld.info.get("icc_profile"))
        rec["from"] = ld.format
        rec["size_in"] = list(img.size)
        frames = None
        durations = None
        if ld.n_frames > 1 and ld.format in ("GIF", "WEBP", "PNG", "AVIF"):
            frames, durations = [], []
            for _i, fr, dur in iter_frames(path):
                frames.append(fr)
                durations.append(dur or 100)
        w, h = img.size
        new = fit_within((w, h), job["max_width"] or job["max_edge"], job["max_height"] or job["max_edge"])
        if job["max_edge"]:
            new = fit_within(new, job["max_edge"], job["max_edge"])
        if new != (w, h):
            img = img.resize(new, Image.LANCZOS)
            if frames:
                frames = [f.resize(new, Image.LANCZOS) for f in frames]
        alpha = uses_alpha(img) if has_alpha(img) else False
        if not alpha and img.mode == "RGBA":
            img = img.convert("RGB")
        exif = meta_from(ld.info, ld.oriented != 1)["exif"] if job["keep_metadata"] else None
        ref = reference(img)
        graphics = _is_graphics(img)
        cands = _candidates(job["to"], ld.format, alpha, graphics, bool(frames), job["lossless"])
        target = int(job["target_kb"] * 1024) if job["target_kb"] else None
        if target and rec["bytes_in"] <= target and ld.format in cands and not frames:
            # The original already fits: a result in its own format never needs to be bigger than it was.
            target = rec["bytes_in"]
        loop = int(ld.info.get("loop", 0) or 0)
        if target and not frames and not job["lossless"] and any(c in ("JPEG", "WEBP", "AVIF") for c in cands):
            # Shrink first when even a JPEG at quality 60 cannot fit: searching every format at a size that cannot
            # fit wastes most of the time on large photos (a 24 MP photo to 300 KB took a minute).
            for _ in range(5):
                probe = len(encode(img, "JPEG", 60))
                if probe <= target or min(img.size) <= 32:
                    break
                factor = max(0.25, min(0.95, (target / probe) ** 0.5 * 0.97))
                img = img.resize((max(1, round(img.size[0] * factor)), max(1, round(img.size[1] * factor))), Image.LANCZOS)
            if img.size != new:
                ref = reference(img)
        # Candidate formats encode in parallel threads (the encoders release the GIL).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, len(cands))) as pool:
            results = list(pool.map(lambda fmt: _try(fmt, img, ref, frames, durations, loop, job, target, exif, graphics), cands))
        original = None
        if ld.format == "JPEG" and "JPEG" in cands and img.size == (w, h) and not frames and not job["keep_metadata"] and not job["lossless"]:
            # The original pixels with the metadata removed losslessly: a photo already small enough never grows.
            data = strip_jpeg(path.read_bytes(), ld.oriented)
            if data:
                original = {"format": "JPEG", "data": data, "bytes": len(data), "quality": "original pixels", "ssim": 1.0, "original": True}
                results.append(original)
        # Downscale when nothing fits the budget.
        scale_steps = 0
        while target and all(r["bytes"] > target for r in results) and scale_steps < 6 and min(img.size) > 32:
            factor = max(0.5, min(0.92, (target / min(r["bytes"] for r in results)) ** 0.5 * 0.97))
            img = img.resize((max(1, round(img.size[0] * factor)), max(1, round(img.size[1] * factor))), Image.LANCZOS)
            if frames:
                frames = [f.resize(img.size, Image.LANCZOS) for f in frames]
            ref2 = reference(img)
            with ThreadPoolExecutor(max_workers=max(1, len(cands))) as pool:
                results = list(pool.map(lambda fmt: _try(fmt, img, ref2, frames, durations, loop, job, target, exif, graphics), cands))
            if original:
                results.append(original)
            scale_steps += 1
        ok = [r for r in results if (not target or r["bytes"] <= target) and (job["min_ssim"] is None or r["ssim"] >= job["min_ssim"] - 1e-9)]
        if target and job["min_ssim"] is None and ok:
            # --target-kb asks for the best quality that fits: the highest SSIM within the budget (then the smaller).
            best = max(ok, key=lambda r: (round(r["ssim"], 3), -r["bytes"]))
        else:
            best = min(ok or results, key=lambda r: r["bytes"])
        out = Path(job["out"])
        if out.suffix.lower() != EXT[best["format"]] and not job["explicit_out"]:
            out = out.with_suffix(EXT[best["format"]])
        out = output_path(out, [path], job["force"])
        same_format = best["format"] == {"JPEG": "JPEG", "PNG": "PNG", "WEBP": "WEBP", "GIF": "GIF", "AVIF": "AVIF"}.get(ld.format)
        has_meta = bool(ld.info.get("exif") or ld.info.get("xmp") or ld.info.get("XML:com.adobe.xmp"))
        if same_format and best["bytes"] >= rec["bytes_in"] and new == (w, h) and not target and (job["keep_metadata"] or not has_meta):
            out.write_bytes(path.read_bytes())
            rec.update(output=str(out), format=best["format"], bytes_out=rec["bytes_in"], size=list(img.size), note="already optimal: copied unchanged", ssim=1.0)
        else:
            out.write_bytes(best["data"])
            rec.update(output=str(out), format=best["format"], bytes_out=best["bytes"], size=list(img.size), quality=best.get("quality"), ssim=round(best["ssim"], 4))
            if best.get("colors"):
                rec["colors"] = best["colors"]
            if best.get("original"):
                rec["note"] = "already small enough: the original pixels, with the metadata (EXIF, GPS, XMP) removed losslessly"
        rec["saved_pct"] = round(100 * (1 - rec["bytes_out"] / rec["bytes_in"]), 1) if rec["bytes_in"] else 0.0
        rec["tried"] = [{"format": r["format"], "bytes": r["bytes"], "quality": r.get("quality"), "ssim": round(r["ssim"], 4)} for r in results]
        if rec["bytes_out"] > rec["bytes_in"]:
            rec["warning"] = f"the result is larger than the original ({best['format']} was needed for --to {job['to']}); a lower --min-ssim or --quality makes it smaller"
        if target and rec["bytes_out"] > target:
            rec["warning"] = f"could not reach {min(job['target_kb'], target / 1024):g} KB"
        if job["min_ssim"] is not None and rec.get("ssim", 1) < job["min_ssim"]:
            rec["warning"] = f"SSIM {rec['ssim']} is below {job['min_ssim']}"
        if frames:
            rec["frames"] = len(frames)
    except SkillError as e:
        rec["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _is_graphics(img: Any) -> bool:
    """Screenshots, logos and diagrams: few distinct colours in full-resolution samples (smooth photos and gradients
    have thousands)."""
    w, h = img.size
    s = 384
    for cx, cy in ((w // 2, h // 2), (w // 4, h // 4), (3 * w // 4, 3 * h // 4)):
        box = (max(0, cx - s // 2), max(0, cy - s // 2), min(w, cx + s // 2), min(h, cy + s // 2))
        if img.crop(box).convert("RGB").getcolors(maxcolors=3000) is None:
            return False
    return True


def _candidates(to: str, src_fmt: str, alpha: bool, graphics: bool, animated: bool, lossless: bool) -> list[str]:
    to = to.lower()
    explicit = {"jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "avif": "AVIF", "png": "PNG", "gif": "GIF"}
    if to in explicit:
        return [explicit[to]]
    if to == "keep":
        return [src_fmt if src_fmt in ("JPEG", "PNG", "WEBP", "GIF", "AVIF") else "PNG"]
    if animated:
        return ["WEBP", "GIF"] if to == "auto" else ["GIF"]
    if lossless:
        return ["PNG", "WEBP"] if to == "auto" else ["PNG"]
    if to == "email":
        return ["PNG"] if alpha else (["PNG", "JPEG"] if graphics else ["JPEG"])
    if to != "auto":
        raise UsageError("--to is auto, email, keep, webp, avif, jpg or png")
    if graphics:
        return ["PNG", "WEBP"]
    return ["WEBP", "AVIF", "JPEG"] if not alpha else ["WEBP", "AVIF", "PNG"]


def _try(fmt: str, img: Any, ref: Any, frames: Any, durations: Any, loop: int, job: dict[str, Any], target: int | None, exif: bytes | None, graphics: bool) -> dict[str, Any]:
    # Candidates run in parallel threads, and Pillow keeps save() options on the image object (encoderinfo): each
    # thread needs its own image, or a JPEG's subsampling=2 can leak into a concurrent AVIF save.
    img = img.copy()
    frames = [f.copy() for f in frames] if frames else frames
    if frames:
        q = job["quality"] or 80
        if fmt == "WEBP" and target and not job["lossless"]:
            lo, hi, best = 30, 95, None
            while lo <= hi:
                mid = (lo + hi) // 2
                d = encode(img, fmt, mid, frames=frames, durations=durations, loop=loop)
                if len(d) <= target:
                    best, lo = (d, mid), mid + 1
                else:
                    hi = mid - 1
            d, q = best or (encode(img, fmt, 30, frames=frames, durations=durations, loop=loop), 30)
        else:
            d = encode(img, fmt, q, lossless=job["lossless"], frames=frames, durations=durations, loop=loop, colors=job["colors"])
        return {"format": fmt, "data": d, "bytes": len(d), "quality": q if fmt != "GIF" else None, "ssim": score(ref, d)}
    if fmt == "PNG" or (fmt == "WEBP" and job["lossless"]):
        if fmt == "WEBP":
            d = encode(img, "WEBP", None, lossless=True, exif=exif)
            return {"format": fmt, "data": d, "bytes": len(d), "quality": "lossless", "ssim": score(ref, d)}
        # Compare candidates with plain zlib, then run oxipng once on the winner.
        pixels = img.size[0] * img.size[1]
        lossless_png = encode(img, "PNG", None, exif=exif, png_opt=False)
        best = {"format": "PNG", "data": lossless_png, "bytes": len(lossless_png), "quality": "lossless", "ssim": 1.0}
        soft_alpha = False
        if img.mode in ("RGBA", "LA"):
            lo_hi = img.getchannel("A").getextrema()
            hist = img.getchannel("A").histogram()
            soft_alpha = sum(hist[1:255]) > 0 and lo_hi[0] < 255
        if not job["lossless"] and not soft_alpha:
            tries = [job["colors"]] if job["colors"] else ([256, 128, 64] if pixels <= 8_000_000 else [256])
            floor = job["min_ssim"] if job["min_ssim"] is not None else (0.97 if graphics else 0.985)
            for n in tries:
                d = encode(img, "PNG", None, colors=n, exif=exif, png_opt=False)
                s = score(ref, d)
                if (s >= floor or job["colors"]) and len(d) < best["bytes"]:
                    best = {"format": "PNG", "data": d, "bytes": len(d), "quality": f"{n} colours", "ssim": s, "colors": n}
                if target and best["bytes"] <= target:
                    break
        best["data"] = png_optimize(best["data"], pixels, bool(exif))
        best["bytes"] = len(best["data"])
        if best["quality"] == "lossless":
            best["ssim"] = score(ref, best["data"])
        return best
    if fmt == "GIF":
        d = encode(img, "GIF", None, colors=job["colors"] or 256)
        return {"format": fmt, "data": d, "bytes": len(d), "quality": None, "ssim": score(ref, d)}
    d, q, s = search(img, fmt, ref, target, job["min_ssim"], job["quality"], exif)
    return {"format": fmt, "data": d, "bytes": len(d), "quality": q, "ssim": s}


def main() -> int:
    from _img import expand_inputs

    args = build_parser().parse_args()
    if args.quality is not None and not 1 <= args.quality <= 100:
        raise UsageError("--quality must be 1-100")
    if args.min_ssim is not None and not 0 < args.min_ssim <= 1:
        raise UsageError("--min-ssim is between 0 and 1")
    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    if args.out and len(inputs) > 1:
        raise UsageError("--out takes one input; use --out-dir")
    base = {"to": args.to, "target_kb": args.target_kb, "min_ssim": args.min_ssim, "quality": args.quality, "max_edge": args.max_edge,
            "max_width": args.max_width, "max_height": args.max_height, "colors": args.colors, "lossless": args.lossless,
            "keep_metadata": args.keep_metadata, "force": args.force}
    jobs = []
    if args.out:
        jobs.append({**base, "input": str(inputs[0].path), "out": args.out, "explicit_out": True})
        explicit = Path(args.out).suffix.lower().lstrip(".")
        if explicit and args.to == "auto" and explicit in ("jpg", "jpeg", "webp", "avif", "png", "gif"):
            jobs[0]["to"] = explicit
    else:
        root = output_dir(args.out_dir or "optimized")
        stems: dict[str, int] = {}
        for inp in inputs:
            key = str(inp.rel.with_suffix("")).lower()
            stems[key] = stems.get(key, 0) + 1
        for inp in inputs:
            rel = inp.rel
            if stems[str(rel.with_suffix("")).lower()] > 1:
                # photo.png and photo.jpg would both become photo.webp: keep the source extension in the name.
                rel = rel.with_name(f"{rel.stem}-{rel.suffix.lstrip('.').lower()}{rel.suffix}")
            jobs.append({**base, "input": str(inp.path), "out": str(root / rel), "explicit_out": False})
    from _img import batch_map

    recs = batch_map(optimize_one, jobs, workers=args.workers)
    for r in recs:
        for t in r.get("tried", []):
            t.pop("data", None)
    if args.format == "json":
        emit(recs[0] if len(recs) == 1 else recs, max_chars=args.max_chars or None)
    else:
        rows = []
        for r in recs:
            if "error" in r:
                rows.append([r["input"], "error: " + r["error"], "", "", "", ""])
                continue
            q = r.get("quality")
            rows.append([r["input"], r["output"], f"{r['size'][0]}×{r['size'][1]}", f"{human_size(r['bytes_in'])} → {human_size(r['bytes_out'])} ({-r['saved_pct']:+.0f}%)",
                         f"{r['format']}" + (f" q{q}" if isinstance(q, int) else f" {q}" if q else ""), f"{r.get('ssim', '')}" + (f" · {r['warning']}" if r.get("warning") else "") + (f" · {r['note']}" if r.get("note") else "")])
        total_in = sum(r["bytes_in"] for r in recs if "error" not in r)
        total_out = sum(r["bytes_out"] for r in recs if "error" not in r)
        emit(recs, "md", lambda _: f"Total {human_size(total_in)} → {human_size(total_out)} for {len(recs)} file(s).\n\n" + md_table(["input", "output", "size", "bytes", "format", "SSIM"], rows), max_chars=args.max_chars or None, hint="The files are all written; --max-chars 0 lists every one.")
    return 0 if all("error" not in r for r in recs) else 1


if __name__ == "__main__":
    run_main(main)
