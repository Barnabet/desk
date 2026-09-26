#!/usr/bin/env python3
"""Describe images: format, size, mode, bit depth, DPI, transparency, animation, colour profile, EXIF (camera, lens,
exposure, date, orientation, GPS as latitude/longitude), XMP and IPTC basics, dominant colours, brightness, sharpness.

One file gives a detailed report; several files (or folders and globs) give a table, computed in parallel.
Also reads SVG (size, viewBox, text, fonts, external references), camera RAW and PSD (layers), and names fonts.

Examples:
  python3 scripts/img_info.py photo.jpg
  python3 scripts/img_info.py IMG_0042.HEIC --exif all          # every EXIF tag
  python3 scripts/img_info.py photos/ -r                         # a map of the folder, then a table of its files
  python3 scripts/img_info.py photos/ -r --find "Nikon|GPS"      # which files match, with the field and context
  python3 scripts/img_info.py photos/ -r --offset 400            # the next part of a long listing
  python3 scripts/img_info.py "shots/*.png" --stats --format json

Pixel statistics (palette, brightness, sharpness) are on by default for one file, and with --stats for many (then
only for the rows shown, 300 at most per call). Big folders start with a map (totals, formats, pixel sizes, dates,
cameras, GPS, one row per subfolder); rows show paths relative to the folder given, and a listing longer than
--max-chars ends with the exact command for the next part.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _common import SkillError, add_format, emit, human_size, md_table, parser, run_main

EXIF_IFD, GPS_IFD, INTEROP_IFD = 0x8769, 0x8825, 0xA005

CURATED_IFD0 = {0x010F: "make", 0x0110: "model", 0x0131: "software", 0x0132: "modified", 0x013B: "artist", 0x8298: "copyright", 0x010E: "description", 0x0112: "orientation"}
CURATED_EXIF = {
    0x829A: "exposure_time", 0x829D: "f_number", 0x8827: "iso", 0x9003: "taken", 0x9011: "time_offset", 0x920A: "focal_length_mm",
    0xA405: "focal_length_35mm", 0xA434: "lens", 0xA433: "lens_make", 0x9209: "flash", 0x8822: "exposure_program", 0x9204: "exposure_bias_ev",
    0x9207: "metering", 0xA403: "white_balance", 0xA431: "body_serial", 0xA001: "color_space", 0x9291: "subsec", 0xA420: "image_unique_id",
}
EXPOSURE_PROGRAMS = {0: "not defined", 1: "manual", 2: "program", 3: "aperture priority", 4: "shutter priority", 5: "creative", 6: "action", 7: "portrait", 8: "landscape"}
METERING = {0: "unknown", 1: "average", 2: "center-weighted", 3: "spot", 4: "multi-spot", 5: "pattern", 6: "partial"}
IPTC_FIELDS = {(2, 5): "title", (2, 25): "keywords", (2, 80): "byline", (2, 85): "byline_title", (2, 90): "city", (2, 95): "state", (2, 101): "country", (2, 105): "headline", (2, 110): "credit", (2, 115): "source", (2, 116): "copyright", (2, 120): "caption", (2, 55): "date_created", (2, 40): "instructions"}
XMP_SKIP_PREFIXES = ("crs:", "xmpMM:", "stEvt:", "stRef:", "photoshop:DocumentAncestors", "xmpNote:", "aux:", "GCamera:", "hdrgm:", "Container", "Item:")

#: Named colours (sRGB) matched in CIELAB, so olive, khaki, tan and steel blue are named as a person would.
COLOR_NAMES = {
    "red": (205, 35, 35), "dark red": (125, 20, 25), "pink": (240, 150, 180), "hot pink": (230, 60, 140), "salmon": (240, 128, 114),
    "coral": (250, 110, 80), "orange": (245, 140, 20), "brown": (125, 75, 35), "dark brown": (75, 45, 25), "tan": (200, 150, 95),
    "beige": (225, 205, 170), "cream": (245, 238, 205), "khaki": (190, 175, 105), "gold": (225, 175, 30), "mustard": (195, 155, 40),
    "yellow": (250, 222, 35), "pale yellow": (250, 242, 160), "olive": (125, 125, 45), "dark olive": (80, 85, 35), "yellow-green": (160, 200, 50),
    "lime": (60, 215, 60), "green": (45, 160, 65), "dark green": (15, 90, 35), "light green": (150, 220, 140), "mint": (170, 230, 200),
    "teal": (0, 128, 128), "dark teal": (10, 80, 85), "turquoise": (60, 200, 190), "cyan": (0, 200, 225), "sky blue": (125, 190, 235),
    "light blue": (175, 210, 240), "steel blue": (75, 125, 170), "blue": (40, 90, 210), "navy": (20, 35, 100), "indigo": (75, 40, 130),
    "purple": (120, 50, 160), "violet": (170, 120, 220), "lavender": (205, 190, 235), "magenta": (210, 20, 190), "plum": (140, 70, 120),
    "peach": (245, 195, 160), "sand": (215, 190, 140), "rust": (170, 75, 30), "slate": (95, 110, 130),
}
_LAB_NAMES: dict[str, tuple[float, float, float]] = {}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="+", help="image files, folders or globs")
    p.add_argument("-r", "--recursive", action="store_true", help="include subfolders of folder inputs")
    p.add_argument("--exif", choices=["curated", "all", "none"], default="curated", help="EXIF detail (default curated)")
    p.add_argument("--stats", action="store_true", help="pixel statistics for every file (palette, brightness, sharpness)")
    p.add_argument("--no-stats", action="store_true", help="skip pixel statistics (header only, fastest)")
    p.add_argument("--colors", type=int, default=6, help="dominant colours to report (default 6)")
    p.add_argument("--detail", action="store_true", help="a detailed section per file even for many files")
    p.add_argument("--workers", type=int, help="parallel workers")
    p.add_argument("--find", metavar="REGEX", help="search file names and metadata (EXIF, XMP, IPTC, comments, SVG text, font names); lists file, field and context")
    p.add_argument("--no-cache", action="store_true", help="read the files again instead of reusing cached results")
    from _paging import add_paging

    add_paging(p, "files")
    add_format(p)
    return p


# ── EXIF / XMP / IPTC ───────────────────────────────────────────────────


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _text(v: Any) -> Any:
    if isinstance(v, bytes):
        v = v.rstrip(b"\x00")
        try:
            return v.decode("utf-8").strip()
        except UnicodeDecodeError:
            return v.decode("latin-1").strip()
    if isinstance(v, str):
        return v.rstrip("\x00").strip()
    return v


def _dms(v: Any) -> float | None:
    try:
        d, m, s = (float(x) for x in v)
        return d + m / 60 + s / 3600
    except (TypeError, ValueError, ZeroDivisionError):
        return _num(v)


def decode_exif(ex: Any, mode: str = "curated") -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(EXIF fields, GPS) from a PIL Exif object."""
    from PIL import ExifTags

    out: dict[str, Any] = {}
    gps: dict[str, Any] | None = None
    if ex is None or not len(ex) and not ex.get_ifd(EXIF_IFD):
        return out, None
    exif_ifd = ex.get_ifd(EXIF_IFD) or {}
    gps_ifd = ex.get_ifd(GPS_IFD) or {}
    if mode == "all":
        for tag, v in list(ex.items()) + list(exif_ifd.items()):
            if tag in (EXIF_IFD, GPS_IFD, INTEROP_IFD, 0x927C, 0x02BC, 0x8773, 0x83BB):  # sub-IFDs, MakerNote, XMP, ICC, IPTC
                continue
            name = ExifTags.TAGS.get(tag, f"0x{tag:04X}")
            v = _text(v)
            if isinstance(v, bytes):
                v = f"<{len(v)} bytes>"
            elif isinstance(v, tuple):
                v = [(_num(x) if hasattr(x, "numerator") else _text(x)) for x in v][:32]
            elif hasattr(v, "numerator"):
                v = _num(v)
            out[name] = v
    else:
        for tag, name in CURATED_IFD0.items():
            if tag in ex:
                out[name] = _text(ex[tag])
        for tag, name in CURATED_EXIF.items():
            if tag in exif_ifd:
                out[name] = _text(exif_ifd[tag])
        et = _num(out.get("exposure_time"))
        if et:
            out["exposure_time"] = f"1/{round(1 / et)} s" if et < 0.5 else f"{et:g} s"
        for k in ("f_number", "focal_length_mm", "exposure_bias_ev"):
            if k in out:
                n = _num(out[k])
                out[k] = round(n, 2) if n is not None else out[k]
        if "f_number" in out and isinstance(out["f_number"], float):
            out["f_number"] = f"f/{out['f_number']:g}"
        if "iso" in out and isinstance(out["iso"], tuple):
            out["iso"] = out["iso"][0] if out["iso"] else None
        if "flash" in out and isinstance(out["flash"], int):
            out["flash"] = "fired" if out["flash"] & 1 else "did not fire"
        if "exposure_program" in out:
            out["exposure_program"] = EXPOSURE_PROGRAMS.get(out["exposure_program"], out["exposure_program"])
        if "metering" in out:
            out["metering"] = METERING.get(out["metering"], out["metering"])
        if "white_balance" in out:
            out["white_balance"] = {0: "auto", 1: "manual"}.get(out["white_balance"], out["white_balance"])
        if "color_space" in out:
            out["color_space"] = {1: "sRGB", 0xFFFF: "uncalibrated"}.get(out["color_space"], out["color_space"])
        if "orientation" in out:
            from _img import ORIENTATION_NAMES

            o = out["orientation"]
            out["orientation"] = f"{o} ({ORIENTATION_NAMES.get(o, 'unknown')})"
        if out.get("taken") and out.get("subsec"):
            out["taken"] = f"{out['taken']}.{out['subsec']}"
        out.pop("subsec", None)
        if out.get("taken") and out.get("time_offset"):
            out["taken"] = f"{out['taken']} {out['time_offset']}"
        out.pop("time_offset", None)
    if gps_ifd:
        lat, lon = _dms(gps_ifd.get(2)), _dms(gps_ifd.get(4))
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            if str(_text(gps_ifd.get(1, "N"))).upper().startswith("S"):
                lat = -lat
            if str(_text(gps_ifd.get(3, "E"))).upper().startswith("W"):
                lon = -lon
            gps = {"lat": round(lat, 6), "lon": round(lon, 6)}
            alt = _num(gps_ifd.get(6))
            if alt is not None:
                gps["alt_m"] = round(-alt if gps_ifd.get(5) in (1, b"\x01") else alt, 1)
            if gps_ifd.get(29):
                gps["date"] = _text(gps_ifd.get(29))
            direction = _num(gps_ifd.get(17))
            if direction is not None:
                gps["direction_deg"] = round(direction, 1)
    return {k: v for k, v in out.items() if v not in (None, "", b"")}, gps


def xmp_bytes(im: Any) -> bytes | None:
    for key in ("xmp", "XML:com.adobe.xmp"):
        v = im.info.get(key)
        if v:
            return v.encode("utf-8") if isinstance(v, str) else bytes(v)
    tag = getattr(im, "tag_v2", None)
    if tag is not None and 700 in tag:
        v = tag[700]
        return v if isinstance(v, bytes) else str(v).encode("utf-8")
    return None


def parse_xmp(data: bytes, everything: bool = False) -> dict[str, Any]:
    """Simple XMP properties as {'prefix:Name': value}; lists for rdf:Bag/Seq/Alt."""
    import xml.etree.ElementTree as ET

    start = data.find(b"<x:xmpmeta")
    if start < 0:
        start = data.find(b"<rdf:RDF")
    end = data.rfind(b"</x:xmpmeta>")
    blob = data[start : end + len(b"</x:xmpmeta>")] if start >= 0 and end > 0 else data
    ns_prefix: dict[str, str] = {}
    try:
        for _ev, (prefix, uri) in ET.iterparse(__import__("io").BytesIO(blob), events=("start-ns",)):
            ns_prefix.setdefault(uri, prefix)
        root = ET.fromstring(blob)
    except ET.ParseError:
        return {}

    def qname(tag: str) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            return f"{ns_prefix.get(uri, 'ns')}:{local}"
        return tag

    RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    out: dict[str, Any] = {}
    for desc in root.iter(f"{RDF}Description"):
        for k, v in desc.attrib.items():
            name = qname(k)
            if name.startswith("rdf:") or name.startswith("xml:"):
                continue
            out.setdefault(name, v)
        for child in desc:
            name = qname(child.tag)
            items = [li.text.strip() for li in child.iter(f"{RDF}li") if li.text and li.text.strip()]
            if items:
                out.setdefault(name, items if len(items) > 1 or child.find(f"{RDF}Alt") is None else items[0])
            elif child.text and child.text.strip():
                out.setdefault(name, child.text.strip())
    if not everything:
        out = {k: v for k, v in out.items() if not k.startswith(XMP_SKIP_PREFIXES)}
    trimmed = {}
    for k, v in list(out.items())[:60]:
        if isinstance(v, str) and len(v) > 300:
            v = v[:300] + "…"
        trimmed[k] = v
    return trimmed


def iptc(im: Any) -> dict[str, Any]:
    try:
        from PIL import IptcImagePlugin

        data = IptcImagePlugin.getiptcinfo(im)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    for key, name in IPTC_FIELDS.items():
        if data and key in data:
            v = data[key]
            out[name] = [_text(x) for x in v] if isinstance(v, list) else _text(v)
    return out


# ── pixel statistics ────────────────────────────────────────────────────


def _lab(rgb: tuple[int, ...]) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(v)) for v in rgb[:3])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_name(rgb: tuple[int, ...]) -> str:
    """A plain name for a colour: greys by lightness (warm or cool when tinted), others by the nearest named colour
    in CIELAB (lightness weighted less, since 'dark' and 'light' variants have their own names)."""
    import math

    L, a, b = _lab(rgb)
    chroma = math.hypot(a, b)
    if chroma < 9:
        grey = "black" if L < 14 else "dark grey" if L < 38 else "grey" if L < 62 else "light grey" if L < 88 else "white"
        if chroma >= 5 and grey not in ("black", "white"):
            hue = math.degrees(math.atan2(b, a)) % 360
            grey = ("warm " if 20 <= hue < 110 else "cool " if 180 <= hue < 300 else "") + grey
        return grey
    if not _LAB_NAMES:
        _LAB_NAMES.update({n: _lab(c) for n, c in COLOR_NAMES.items()})
    best, bd = "", 1e18
    for name, (l2, a2, b2) in _LAB_NAMES.items():
        d = ((L - l2) * 0.6) ** 2 + (a - a2) ** 2 + (b - b2) ** 2
        if d < bd:
            best, bd = name, d
    return best


def pixel_stats(img: Any, ncolors: int = 6) -> dict[str, Any]:
    """Dominant colours, brightness, contrast, colourfulness and a sharpness score from a decoded image."""
    import numpy as np
    from PIL import Image

    from _img import normalize, shrink

    img = shrink(normalize(img), 1024)  # never convert a full-size image: stats need 1024 px at most
    rgba = img.convert("RGBA")
    rgba.thumbnail((400, 400), Image.BILINEAR)
    a = np.asarray(rgba)
    mask = a[..., 3] >= 128
    px = a[mask][:, :3]
    stats: dict[str, Any] = {}
    if px.size == 0:
        return {"note": "fully transparent"}
    if len(px) > 60000:
        px = px[:: len(px) // 60000 + 1]
    q = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8), "RGB").quantize(colors=max(2, ncolors * 2), method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    palette = []
    for c, idx in counts:
        rgb = tuple(pal[idx * 3 : idx * 3 + 3])
        # Merge near-duplicates median cut sometimes splits.
        for p in palette:
            if sum(abs(int(x) - int(y)) for x, y in zip(p["rgb"], rgb)) < 24:
                p["count"] += c
                break
        else:
            palette.append({"rgb": rgb, "count": c})
    palette.sort(key=lambda p: -p["count"])
    stats["palette"] = [{"hex": "#%02x%02x%02x" % p["rgb"], "pct": round(100 * p["count"] / total, 1), "name": color_name(p["rgb"])} for p in palette[:ncolors]]
    f = px.astype(np.float32)
    luma = 0.299 * f[:, 0] + 0.587 * f[:, 1] + 0.114 * f[:, 2]
    stats["brightness_pct"] = round(float(luma.mean()) / 2.55, 1)
    stats["contrast_pct"] = round(float(luma.std()) / 1.275, 1)
    rg = f[:, 0] - f[:, 1]
    yb = 0.5 * (f[:, 0] + f[:, 1]) - f[:, 2]
    colorful = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    stats["colorfulness"] = round(colorful, 1)
    stats["grayscale"] = bool(np.percentile(np.abs(f - f.mean(axis=1, keepdims=True)).max(axis=1), 98) < 4)
    # Sharpness: variance of the Laplacian on a 1024 px grey version.
    g = img.convert("L")
    g.thumbnail((1024, 1024), Image.BILINEAR)
    ga = np.asarray(g, dtype=np.float32)
    if ga.shape[0] > 2 and ga.shape[1] > 2:
        lap = ga[1:-1, 2:] + ga[1:-1, :-2] + ga[2:, 1:-1] + ga[:-2, 1:-1] - 4 * ga[1:-1, 1:-1]
        stats["sharpness"] = round(float(lap.var()), 1)
    stats["mean_rgb"] = [round(float(x)) for x in f.mean(axis=0)]
    return stats


def brightness_word(p: float) -> str:
    return "very dark" if p < 15 else "dark" if p < 35 else "bright" if p > 70 else "very bright" if p > 88 else "medium"


# ── per-kind readers ────────────────────────────────────────────────────


def info_one(job: tuple[str, str, bool, int]) -> dict[str, Any]:
    """The record for one file, from the file cache when this file content was described before (errors are not cached)."""
    from _img import Uncached, cached_value, code_version

    path_s, exif_mode, want_stats, ncolors = job

    def compute() -> dict[str, Any]:
        rec = describe(job)
        if "error" in rec:
            raise Uncached(rec)
        return rec

    try:
        rec = cached_value(path_s, "img-info", _info_params(job), code_version("img_info.py", "_img.py", "_fonts.py"), compute)
    except Uncached as e:
        rec = e.rec
    except OSError as e:
        return {"file": path_s, "error": f"cannot read: {e}"}
    rec = dict(rec)
    rec["file"] = path_s
    return rec


def _info_params(job: tuple[str, str, bool, int]) -> dict[str, Any]:
    return {"exif": job[1], "stats": job[2], "colors": job[3]}


def peek_info(job: tuple[str, str, bool, int]) -> dict[str, Any] | None:
    """The cached record, or None (checked in the main process, so a cached batch starts no workers)."""
    from _img import cache_peek, code_version

    rec = cache_peek(job[0], "img-info", _info_params(job), code_version("img_info.py", "_img.py", "_fonts.py"))
    if rec is not None:
        rec = {**rec, "file": job[0]}
    return rec


def describe(job: tuple[str, str, bool, int]) -> dict[str, Any]:
    path_s, exif_mode, want_stats, ncolors = job
    path = Path(path_s)
    rec: dict[str, Any] = {"file": str(path)}
    try:
        from _img import kind_of

        rec["bytes"] = path.stat().st_size
        kind = kind_of(path)
        rec["kind"] = kind
        if kind == "font":
            from _fonts import font_summary

            rec.update(font_summary(path))
        elif kind == "svg":
            rec.update(svg_info(path, want_stats, ncolors))
        elif kind == "raw":
            rec.update(raw_info(path, exif_mode, want_stats, ncolors))
        else:
            rec.update(raster_info(path, exif_mode, want_stats, ncolors))
    except SkillError as e:
        rec["error"] = str(e)
    except Exception as e:  # noqa: BLE001 — one bad file must not stop a batch
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


MODE_BITS = {"1": 1, "L": 8, "P": 8, "RGB": 8, "RGBA": 8, "CMYK": 8, "LA": 8, "PA": 8, "YCbCr": 8, "LAB": 8, "HSV": 8, "I;16": 16, "I;16B": 16, "I;16L": 16, "I;16N": 16, "I": 32, "F": 32, "RGBX": 8, "RGBa": 8}
MODE_CHANNELS = {"1": 1, "L": 1, "P": 1, "I": 1, "F": 1, "I;16": 1, "I;16B": 1, "I;16L": 1, "I;16N": 1, "LA": 2, "PA": 2, "RGB": 3, "YCbCr": 3, "LAB": 3, "HSV": 3, "RGBA": 4, "CMYK": 4, "RGBX": 4, "RGBa": 4}


def raster_info(path: Path, exif_mode: str, want_stats: bool, ncolors: int) -> dict[str, Any]:
    from _img import SKIPPED_TEXT, _open, frame_count, has_alpha, icc_description, load, uses_alpha

    SKIPPED_TEXT.clear()
    im = _open(path)
    try:
        r: dict[str, Any] = {"format": im.format, "format_name": getattr(im, "format_description", None) or im.format}
        w, h = im.size
        r["mode"] = im.mode
        bits = MODE_BITS.get(im.mode)
        if im.info.get("bit_depth"):
            bits = int(im.info["bit_depth"])
        r["bit_depth"] = bits
        r["channels"] = MODE_CHANNELS.get(im.mode)
        dpi = im.info.get("dpi")
        if dpi:
            try:
                r["dpi"] = [round(float(dpi[0]), 1), round(float(dpi[1]), 1)]
            except (TypeError, ValueError):
                pass
        r["icc_profile"] = icc_description(im.info.get("icc_profile"))
        r["alpha"] = has_alpha(im)
        n = frame_count(im)
        if im.format == "PSD":
            layers = getattr(im, "layers", None) or []
            r["layers"] = [{"name": _text(l[0]), "box": list(l[2])} for l in layers][:100]
            n = 1
        if n > 1:
            r["frames"] = n
            if im.format in ("GIF", "PNG", "WEBP", "AVIF"):
                from _img import animation_timing

                durs, loop = animation_timing(path, im)
                r["animation"] = {"frames": n, "total_ms": sum(durs), "durations_ms": durs[:60] + (["…"] if n > 60 else []), "loop": loop}
            else:
                sizes = []
                for i in range(min(n, 50)):
                    im.seek(i)
                    sizes.append(list(im.size))
                im.seek(0)
                r["pages"] = n
                r["page_sizes"] = sizes
                if im.format == "TIFF":
                    from _img import largest_resolution

                    if largest_resolution(im):
                        r["multi_resolution"] = True
        if im.format in ("ICO", "ICNS"):
            sizes = im.info.get("sizes") or []
            r["icon_sizes"] = sorted({f"{s[0]}x{s[1]}" + (f"@{s[2]}x" if len(s) > 2 and s[2] != 1 else "") for s in sizes}, key=lambda x: int(x.split("x")[0]))
        if im.format == "TIFF":
            r["compression"] = im.info.get("compression")
        if im.format == "JPEG":
            r["progressive"] = bool(im.info.get("progressive") or im.info.get("progression"))
            try:
                from PIL import JpegImagePlugin

                sub = JpegImagePlugin.get_sampling(im)
                r["subsampling"] = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(sub, None)
                q = _jpeg_quality(im)
                if q:
                    r["estimated_quality"] = q
            except Exception:  # noqa: BLE001
                pass
        if im.info.get("comment"):
            r["comment"] = _text(im.info["comment"])[:300]
        if im.format == "PNG":
            text = {k: _text(v)[:300] for k, v in im.info.items() if isinstance(v, (str, bytes)) and k not in ("icc_profile", "exif", "xmp", "XML:com.adobe.xmp", "transparency") and k.lower() not in ("dpi",)}
            if text:
                r["png_text"] = dict(list(text.items())[:20])
        ex = None
        try:
            # A PNG's getexif() decodes every pixel to look past them; only an eXIf chunk before them is read.
            ex = None if (im.format == "PNG" and "exif" not in im.info) else im.getexif()
        except Exception:  # noqa: BLE001
            ex = None
        orientation = int(ex.get(0x0112, 1) or 1) if ex else 1
        if exif_mode != "none" and ex is not None:
            fields, gps = decode_exif(ex, exif_mode)
            if fields:
                r["exif"] = fields
            if gps:
                r["gps"] = gps
        xb = xmp_bytes(im)
        if xb:
            xm = parse_xmp(xb, everything=exif_mode == "all")
            if xm:
                r["xmp"] = xm
        ip = iptc(im) if im.format in ("JPEG", "TIFF") else {}
        if ip:
            r["iptc"] = ip
        if im.format == "MPO":
            r["note"] = "multi-picture JPEG (several images; frames are the extra pictures)"
        if im.format in ("HEIF", "AVIF") and im.info.get("depth_images"):
            r["depth_maps"] = len(im.info["depth_images"])

    finally:
        try:
            im.close()
        except Exception:  # noqa: BLE001
            pass
    r["width"], r["height"] = (h, w) if orientation in (5, 6, 7, 8) else (w, h)
    if orientation in (5, 6, 7, 8):
        r["stored_size"] = [w, h]
    r["megapixels"] = round(w * h / 1e6, 2)
    if want_stats:
        ld = load(path, max_edge=512)
        r["alpha_used"] = uses_alpha(ld.img) if r["alpha"] else False
        r["stats"] = pixel_stats(ld.img, ncolors)
        if ld.info.get("_warning"):
            r["warning"] = ld.info["_warning"]
    if SKIPPED_TEXT:
        r["warning"] = f"a text chunk that inflates past Pillow's limit (a possible text bomb, {human_size(max(SKIPPED_TEXT))} compressed) was skipped; the pixels are fine"
    return r


def _jpeg_quality(im: Any) -> int | None:
    """Estimates the JPEG quality (1-100) from the luminance quantisation table (libjpeg scaling)."""
    q = getattr(im, "quantization", None)
    if not q or 0 not in q:
        return None
    base = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
            18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
    table = list(q[0])
    if len(table) != 64:
        return None
    # Pillow stores tables in zig-zag order; compare sums, which are order-independent.
    ratio = sum(table) / sum(base) * 100
    quality = (200 - ratio) / 2 if ratio <= 100 else 5000 / ratio
    return max(1, min(100, round(quality)))


def raw_info(path: Path, exif_mode: str, want_stats: bool, ncolors: int) -> dict[str, Any]:
    import io

    import rawpy

    from _img import _raw_thumb, load, pil

    Image = pil()
    data = path.read_bytes()
    r: dict[str, Any] = {"format": "RAW", "format_name": f"camera RAW ({path.suffix.lstrip('.').upper()})"}
    try:
        raw = rawpy.imread(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"{path.name}: LibRaw cannot read this file ({type(e).__name__})") from None
    with raw:
        s = raw.sizes
        flip = int(getattr(s, "flip", 0) or 0)
        w, h = int(s.width), int(s.height)
        r["width"], r["height"] = (h, w) if flip in (5, 6) else (w, h)
        r["raw_size"] = [int(s.raw_width), int(s.raw_height)]
        r["megapixels"] = round(w * h / 1e6, 2)
        r["mode"] = "RAW"
        r["bit_depth"] = max(1, int(raw.white_level).bit_length()) if getattr(raw, "white_level", None) else None
        r["channels"] = int(raw.num_colors)
        try:
            r["color_filter"] = raw.color_desc.decode("ascii", "replace")
            r["raw_type"] = str(raw.raw_type).split(".")[-1].lower()
            r["black_level"] = [int(x) for x in raw.black_level_per_channel]
            r["white_level"] = int(raw.white_level)
            r["camera_wb"] = [round(float(x), 3) for x in raw.camera_whitebalance]
        except Exception:  # noqa: BLE001
            pass
        thumb = _raw_thumb(raw, rawpy)
        if thumb is not None:
            r["embedded_preview"] = f"{thumb.size[0]}x{thumb.size[1]}"
    ex_bytes = None
    try:
        from _img import raw_exif

        ex_bytes = raw_exif(path, data)
    except Exception:  # noqa: BLE001
        pass
    if ex_bytes and exif_mode != "none":
        ex = Image.Exif()
        ex.load(ex_bytes)
        fields, gps = decode_exif(ex, exif_mode)
        if fields:
            r["exif"] = fields
        if gps:
            r["gps"] = gps
    if want_stats:
        ld = load(path, max_edge=512)
        r["stats"] = pixel_stats(ld.img, ncolors)
    return r


def svg_info(path: Path, want_stats: bool, ncolors: int) -> dict[str, Any]:
    import io
    import re
    import xml.etree.ElementTree as ET
    from collections import Counter

    from _img import _svg_bytes, load, svg_intrinsic_size, svg_root

    r: dict[str, Any] = {"format": "SVG", "format_name": "Scalable Vector Graphics"}
    tag, attrs = svg_root(path)
    size = svg_intrinsic_size(path)
    if size:
        r["width"], r["height"] = (int(v) if float(v).is_integer() else round(v, 2) for v in size)
    for k in ("width", "height", "viewBox"):
        if attrs.get(k):
            r[f"attr_{k}"] = attrs[k]
    counts: Counter[str] = Counter()
    texts: list[str] = []
    fonts: set[str] = set()
    external: list[str] = []
    embedded = 0
    scripts = 0
    try:
        root = ET.parse(io.BytesIO(_svg_bytes(path))).getroot()
    except ET.ParseError as e:
        raise SkillError(f"{path.name}: not a valid SVG ({e})") from None
    for el in root.iter():
        name = el.tag.split("}")[-1] if isinstance(el.tag, str) else "?"
        counts[name] += 1
        if name in ("text", "tspan", "textPath", "title", "desc") and el.text and el.text.strip():
            if name != "tspan" or not texts or texts[-1] != el.text.strip():
                texts.append(el.text.strip())
        fam = el.attrib.get("font-family")
        if fam:
            fonts.add(fam.strip())
        style = el.attrib.get("style", "")
        m = re.search(r"font-family\s*:\s*([^;]+)", style)
        if m:
            fonts.add(m.group(1).strip())
        if name == "style" and el.text:
            fonts.update(f.strip() for f in re.findall(r"font-family\s*:\s*([^;}]+)", el.text))
        for k, v in el.attrib.items():
            if k.split("}")[-1] == "href":
                if v.startswith("data:"):
                    embedded += 1
                elif not v.startswith("#"):
                    external.append(v)
        if name == "script":
            scripts += 1
        if name == "foreignObject":
            r["foreign_objects"] = r.get("foreign_objects", 0) + 1
    r["elements"] = dict(counts.most_common(15))
    if texts:
        joined = " | ".join(texts)
        r["text"] = joined[:2000] + ("…" if len(joined) > 2000 else "")
    if fonts:
        r["fonts"] = sorted(fonts)[:20]
    if embedded:
        r["embedded_images"] = embedded
    if external:
        r["external_refs"] = external[:20]
    if scripts:
        r["scripts"] = scripts
    if counts.get("animate") or counts.get("animateTransform") or counts.get("animateMotion"):
        r["animated"] = True
    if want_stats:
        ld = load(path, max_edge=512)
        r["stats"] = pixel_stats(ld.img, ncolors)
    return r


# ── output ──────────────────────────────────────────────────────────────


def render_one(r: dict[str, Any]) -> str:
    if "error" in r:
        return f"## {r['file']}\n\nerror: {r['error']}"
    lines = [f"## {Path(r['file']).name}", ""]
    if r.get("kind") == "font":
        lines.append(f"- font: {r.get('family')} {r.get('style', '')} ({r.get('format')}, {r.get('glyphs')} glyphs) — use font_tool.py info for details")
        return "\n".join(lines)
    dims = f"{r.get('width')}×{r.get('height')}"
    lines.append(f"- format: {r.get('format_name') or r.get('format')} · {dims} px" + (f" ({r['megapixels']} MP)" if r.get("megapixels") else "") + f" · {human_size(r.get('bytes', 0))}")
    if r.get("stored_size"):
        lines.append(f"- stored as {r['stored_size'][0]}×{r['stored_size'][1]}; the EXIF orientation turns it upright")
    if r.get("mode"):
        lines.append(f"- mode: {r['mode']}" + (f", {r['bit_depth']}-bit per channel" if r.get("bit_depth") else "") + (f", {r['channels']} channel(s)" if r.get("channels") else ""))
    extras = []
    if r.get("dpi"):
        extras.append(f"DPI {r['dpi'][0]:g}×{r['dpi'][1]:g}")
    if r.get("icc_profile"):
        extras.append(f"profile: {r['icc_profile']}")
    if r.get("alpha"):
        extras.append("alpha channel" + ("" if "alpha_used" not in r else (" (used)" if r["alpha_used"] else " (fully opaque)")))
    if r.get("progressive"):
        extras.append("progressive")
    if r.get("subsampling"):
        extras.append(f"chroma {r['subsampling']}")
    if r.get("estimated_quality"):
        extras.append(f"quality ≈{r['estimated_quality']}")
    if r.get("compression"):
        extras.append(f"compression {r['compression']}")
    if extras:
        lines.append("- " + ", ".join(extras))
    if r.get("animation"):
        a = r["animation"]
        uniq = sorted({d for d in a["durations_ms"] if d != "…"})
        lines.append(f"- animation: {a['frames']} frames, {a['total_ms'] / 1000:.2f}s, frame durations {', '.join(str(u) for u in uniq[:8])} ms, loop {a['loop'] if a['loop'] is not None else 'once'}" + (" (0 = forever)" if a["loop"] == 0 else ""))
    if r.get("multi_resolution"):
        sizes = ", ".join(f"{w}×{h}" for w, h in r.get("page_sizes", []))
        lines.append(f"- multi-resolution: the same image at {sizes} (the scripts use the largest)")
    elif r.get("pages"):
        lines.append(f"- pages: {r['pages']}" + (f" ({', '.join(f'{w}×{h}' for w, h in r['page_sizes'][:6])}{', …' if r['pages'] > 6 else ''})" if len({tuple(x) for x in r.get('page_sizes', [])}) > 1 else ""))
    if r.get("icon_sizes"):
        lines.append(f"- icon sizes: {', '.join(r['icon_sizes'])}")
    if r.get("layers"):
        lines.append(f"- PSD layers ({len(r['layers'])}): " + ", ".join(str(l["name"]) for l in r["layers"][:20]))
    if r.get("raw_size"):
        lines.append(f"- RAW: {r.get('raw_type', '')} {r.get('color_filter', '')} sensor {r['raw_size'][0]}×{r['raw_size'][1]}" + (f", white level {r['white_level']}" if r.get("white_level") else "") + (f", embedded preview {r['embedded_preview']}" if r.get("embedded_preview") else ", no embedded preview"))
    if r.get("exif"):
        lines.append("- EXIF: " + "; ".join(f"{k}: {v}" for k, v in r["exif"].items()))
    if r.get("gps"):
        g = r["gps"]
        lines.append(f"- GPS: {g['lat']}, {g['lon']}" + (f", altitude {g['alt_m']} m" if "alt_m" in g else "") + " (location metadata; img_convert.py --strip removes it)")
    if r.get("xmp"):
        lines.append("- XMP: " + "; ".join(f"{k}: {v}" for k, v in list(r["xmp"].items())[:25]))
    if r.get("iptc"):
        lines.append("- IPTC: " + "; ".join(f"{k}: {v}" for k, v in r["iptc"].items()))
    if r.get("comment"):
        lines.append(f"- comment: {r['comment']}")
    if r.get("png_text"):
        lines.append("- PNG text: " + "; ".join(f"{k}: {v}" for k, v in r["png_text"].items()))
    for k in ("elements", "fonts", "external_refs", "embedded_images", "scripts", "foreign_objects", "animated"):
        if r.get(k):
            v = r[k]
            if isinstance(v, dict):
                v = ", ".join(f"{a} {b}" for a, b in v.items())
            elif isinstance(v, list):
                v = ", ".join(v)
            lines.append(f"- {k.replace('_', ' ')}: {v}")
    if r.get("text"):
        lines.append(f"- text in the SVG: {r['text']}")
    if r.get("note"):
        lines.append(f"- note: {r['note']}")
    if r.get("warning"):
        lines.append(f"- warning: {r['warning']}")
    st = r.get("stats")
    if st and "palette" in st:
        lines.append("- dominant colours: " + ", ".join(f"{p['hex']} {p['name']} {p['pct']}%" for p in st["palette"]))
        lines.append(f"- brightness {st['brightness_pct']}% ({brightness_word(st['brightness_pct'])}), contrast {st['contrast_pct']}%, colourfulness {st['colorfulness']}" + (" (greyscale)" if st.get("grayscale") else "") + (f", sharpness {st['sharpness']}" if "sharpness" in st else ""))
    return "\n".join(lines)


def _row(r: dict[str, Any]) -> list[str]:
    name = r.get("rel") or Path(r["file"]).name
    if "error" in r:
        return [name, "error", "", "", human_size(r.get("bytes", 0)), "", "", r["error"][:80]]
    if r.get("kind") == "font":
        return [name, r.get("format", "font"), "", "", human_size(r.get("bytes", 0)), "", "", f"{r.get('family', '')} {r.get('style', '')}"]
    ex = r.get("exif", {})
    notes = []
    if r.get("animation"):
        notes.append(f"{r['animation']['frames']} frames")
    if r.get("pages"):
        notes.append(f"{r['pages']} pages")
    if r.get("gps"):
        notes.append("GPS")
    if r.get("alpha"):
        notes.append("alpha")
    if r.get("icc_profile") and "srgb" not in str(r["icc_profile"]).lower():
        notes.append(str(r["icc_profile"])[:24])
    st = r.get("stats") or {}
    if st.get("palette"):
        notes.append(f"{st['palette'][0]['name']}, {brightness_word(st['brightness_pct'])}")
    if r.get("warning"):
        notes.append("warning: " + r["warning"][:60])
    camera = " ".join(str(x) for x in (ex.get("make"), ex.get("model")) if x)
    return [name, r.get("format", ""), f"{r.get('width')}×{r.get('height')}", r.get("mode", ""), human_size(r.get("bytes", 0)), str(ex.get("taken", ""))[:19], camera[:30], ", ".join(notes)]


TABLE_HEAD = ["file", "format", "size", "mode", "bytes", "taken", "camera", "notes"]


def render_table(recs: list[dict[str, Any]]) -> str:
    total = sum(r.get("bytes", 0) for r in recs)
    return md_table(TABLE_HEAD, [_row(r) for r in recs]) + f"\n\n{len(recs)} file(s), {human_size(total)}."


def folder_map(recs: list[dict[str, Any]], where: str) -> str:
    """The map of a big listing: totals, formats, sizes, dates, cameras, GPS, and one row per subfolder."""
    from collections import Counter

    ok = [r for r in recs if "error" not in r]
    imgs = [r for r in ok if r.get("kind") != "font" and r.get("width")]
    total = sum(r.get("bytes", 0) for r in recs)
    folders: dict[str, list[dict[str, Any]]] = {}
    for r in recs:
        rel = str(r.get("rel") or Path(r["file"]).name).replace("\\", "/")
        folders.setdefault(rel.rsplit("/", 1)[0] + "/" if "/" in rel else "./", []).append(r)
    lines = [f"{len(recs)} file(s) in {where}" + (f" ({len(folders)} folders)" if len(folders) > 1 else "") + f", {human_size(total)}"]
    fmts = Counter(str(r.get("format", "?")) for r in ok)
    lines.append("- formats: " + ", ".join(f"{k} {v}" for k, v in fmts.most_common(12)))
    if imgs:
        by_px = sorted(imgs, key=lambda r: r["width"] * r["height"])
        lo, med, hi = by_px[0], by_px[len(by_px) // 2], by_px[-1]
        lines.append(f"- pixel sizes: {lo['width']}×{lo['height']} to {hi['width']}×{hi['height']} (median {med['width']}×{med['height']})")
        dates = sorted(str(r["exif"]["taken"])[:19] for r in imgs if (r.get("exif") or {}).get("taken"))
        if dates:
            lines.append(f"- taken: {dates[0]} … {dates[-1]} ({len(dates)} with a date)")
        cams = Counter(" ".join(str(x) for x in ((r.get("exif") or {}).get("make"), (r.get("exif") or {}).get("model")) if x) for r in imgs)
        cams.pop("", None)
        if cams:
            lines.append("- cameras: " + ", ".join(f"{k} ({v})" for k, v in cams.most_common(6)) + (f", {len(cams) - 6} more" if len(cams) > 6 else ""))
        flags = [(sum(1 for r in imgs if r.get("gps")), "with GPS"), (sum(1 for r in imgs if r.get("alpha")), "with alpha"),
                 (sum(1 for r in imgs if r.get("animation")), "animated"), (sum(1 for r in imgs if r.get("pages")), "multi-page")]
        lines.append("- " + ", ".join(f"{n} {what}" for n, what in flags))
    fonts = [r for r in ok if r.get("kind") == "font"]
    if fonts:
        lines.append(f"- fonts: {len(fonts)}")
    errs = len(recs) - len(ok)
    if errs:
        lines.append(f"- errors: {errs} file(s) could not be read (listed in the rows)")
    if len(folders) > 1:
        rows = []
        for name, rs in list(folders.items())[:40]:
            f = Counter(str(r.get("format", "?")) for r in rs if "error" not in r)
            rows.append([name, len(rs), human_size(sum(r.get("bytes", 0) for r in rs)), ", ".join(f"{k} {v}" for k, v in f.most_common(3))])
        lines.append("")
        lines.append(md_table(["folder", "files", "bytes", "formats"], rows) + (f"\n({len(folders) - 40} more folders)" if len(folders) > 40 else ""))
    return "\n".join(lines)


def _search_fields(r: dict[str, Any]) -> list[tuple[str, str]]:
    """(field, text) pairs --find searches: the path, format, EXIF, GPS, XMP, IPTC, comments, PNG and SVG text, font names."""
    out = [("file", str(r.get("rel") or r["file"])), ("format", str(r.get("format_name") or r.get("format") or ""))]
    for group in ("exif", "xmp", "iptc", "png_text"):
        for k, v in (r.get(group) or {}).items():
            out.append((f"{group}.{k}", ", ".join(map(str, v)) if isinstance(v, list) else str(v)))
    if r.get("gps"):
        out.append(("gps", f"GPS {r['gps'].get('lat')}, {r['gps'].get('lon')}"))
    for k in ("comment", "text", "family", "style", "icc_profile", "note", "warning", "error"):
        if r.get(k):
            out.append((k, str(r[k])))
    for k in ("fonts", "external_refs"):
        if r.get(k):
            out.append((k, ", ".join(map(str, r[k]))))
    return out


def find_matches(recs: list[dict[str, Any]], pattern: str) -> list[dict[str, Any]]:
    import re

    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits = []
    for r in recs:
        for field, text in _search_fields(r):
            m = rx.search(text)
            if m:
                a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
                ctx = ("…" if a else "") + text[a:m.start()] + "«" + m.group(0) + "»" + text[m.end():b] + ("…" if b < len(text) else "")
                hits.append({"file": r["file"], "rel": r.get("rel"), "field": field, "match": ctx})
    return hits


def main() -> int:
    from _img import FONT_EXTS, IMAGE_EXTS, cached_map, expand_inputs
    from _paging import check_paging, emit_json_page, page_text

    args = build_parser().parse_args()
    check_paging(args)
    if args.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    inputs = expand_inputs(args.inputs, recursive=args.recursive, exts=IMAGE_EXTS | FONT_EXTS, what="images or fonts")
    single = len(inputs) == 1 and not args.find
    bases = {str(i.base) for i in inputs}
    if single:
        rec = cached_map(info_one, [(str(inputs[0].path), args.exif, not args.no_stats, args.colors)], peek_info)[0]
        if args.format == "json":
            emit(rec, "json")
        else:
            emit([rec], "md", lambda rs: "\n\n".join(render_one(r) for r in rs), max_chars=args.max_chars)
        return 1 if "error" in rec else 0
    # Every file's header record (cheap and cached) gives the map, --find, and the rows; pixel statistics are
    # computed only for the rows shown.
    names = [str(i.rel) if len(bases) == 1 and inputs[0].base != Path(".") else str(i.path) for i in inputs]
    jobs = [(str(i.path), args.exif, False, args.colors) for i in inputs]
    recs = cached_map(info_one, jobs, peek_info, workers=args.workers, min_pool=8)
    for r, n in zip(recs, names):
        r["rel"] = n.replace("\\", "/")
    total = len(recs)
    if args.find:
        hits = find_matches(recs, args.find)
        page = hits[args.offset : args.offset + args.limit if args.limit else None]
        if args.format == "json":
            emit_json_page(page, args.offset, len(hits), args.max_chars)
        else:
            files = len({h["file"] for h in hits})
            head = f"{len(hits)} match(es) for '{args.find}' in {files} of {total} file(s)"
            rows = [md_table(["file", "field", "match"], [[h["rel"], h["field"], h["match"]] for h in page])] if page else []
            lines = rows[0].split("\n") if rows else []
            print(page_text("\n".join([head, ""] + lines[:2]) if lines else head, lines[2:], args.offset, len(hits), args.max_chars, "matches"))
        return 0
    want_stats = args.stats and not args.no_stats
    limit = args.limit or (300 if want_stats else None)
    page = recs[args.offset : args.offset + limit if limit else None]
    if want_stats and page:
        full = cached_map(info_one, [(r["file"], args.exif, True, args.colors) for r in page], peek_info, workers=args.workers, min_pool=8)
        for r, f in zip(page, full):
            f["rel"] = r["rel"]
        page = full
    shown_all = args.offset == 0 and len(page) == total
    if args.format == "json":
        emit_json_page(page, args.offset, total, args.max_chars)
    elif args.detail:
        blocks = [render_one(r) for r in page]
        print(page_text("", blocks, args.offset, total, args.max_chars, "files"))
    else:
        where = ", ".join(str(a) for a in args.inputs[:3]) + (" …" if len(args.inputs) > 3 else "")
        head_parts = []
        if total > 30 or len({n.rsplit('/', 1)[0] for n in names if '/' in n}) > 1:
            head_parts.append(folder_map(recs, where) + ("\n" if not shown_all else ""))
        table = md_table(TABLE_HEAD, [_row(r) for r in page]).split("\n")
        head_parts.append("\n".join(table[:2]))
        tail = "" if (len(head_parts) > 1 or not shown_all) else f"\n{total} file(s), {human_size(sum(r.get('bytes', 0) for r in recs))}."
        print(page_text("\n".join(head_parts), table[2:], args.offset, total, args.max_chars, "files", tail=tail))
    return 1 if all("error" in r for r in recs) else 0


if __name__ == "__main__":
    run_main(main)
