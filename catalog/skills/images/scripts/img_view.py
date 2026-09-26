#!/usr/bin/env python3
"""See any image: write PNGs sized for vision (long edge 1568 px), then look at them with view_image.

Reads PNG, JPEG, GIF, WebP, TIFF, BMP, ICO, ICNS, HEIC/HEIF, AVIF, PSD (composite), JPEG 2000, camera RAW (CR2, CR3,
NEF, ARW, DNG, ORF, RW2, RAF…) and SVG. Photos are turned upright (EXIF orientation), wide-gamut profiles are
converted to sRGB, 16-bit data is scaled, and transparency shows as a grey checkerboard.

Modes (combine --zoom and --grid):
  preview (default)   one PNG per input
  --zoom REGION       crop at full resolution, then fit: x,y,w,h in pixels or % (10%,20%,30%,30%), WxH+X+Y, or a
                      tile of the map (r2c3, or r2c3:r3c4 for a block of tiles)
  --grid [STEP]       overlay a labelled coordinate grid (original-image pixels, or --grid-units pct)
  --map               for big images: an overview with rulers and a grid of named tiles (r1c1 …), each about one
                      render at actual pixels, plus the tile table; then --zoom r2c3 shows one tile in full detail
  --sheet             contact sheet of many images with numbered file names (folders, globs; -r recurses)
  --frames            frames of an animation or pages of a multi-page TIFF, with timings, as a sheet

Examples:
  python3 scripts/img_view.py photo.heic
  python3 scripts/img_view.py screenshot.png --grid
  python3 scripts/img_view.py screenshot.png --zoom 60%,0,40%,25% --grid
  python3 scripts/img_view.py panorama.tif --map
  python3 scripts/img_view.py panorama.tif --zoom r2c3 --zoom r4c1:r4c2
  python3 scripts/img_view.py photos/ --sheet -r
  python3 scripts/img_view.py photos/ -r --offset 150          # the next sheets of a big folder
  python3 scripts/img_view.py loading.gif --frames --max-frames 12
  python3 scripts/img_view.py drawing.svg --max-edge 800 --out-dir renders

Outputs go to --out-dir (default ./renders) and replace earlier renders of the same name; inputs are never touched.
Renders are cached by file content. Zooming into an image of 50 MP or more decodes it once into a cached tile store,
so later zooms read only the tiles they need. A folder gives at most --limit images per call (default 150, five
sheets); the output ends with the command for the next ones.
"""

from __future__ import annotations

import math
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, output_dir, parse_ranges, parser, run_main, same_file

MODES = {"preview", "zoom", "grid", "sheet", "frames", "map"}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n", 2)[2])
    p.add_argument("inputs", nargs="+", help="image files, folders or globs")
    p.add_argument("--out-dir", default="renders", help="folder for the PNGs (default ./renders)")
    p.add_argument("--out", help="exact output file (one input, one render)")
    p.add_argument("--max-edge", type=int, default=None, help="long edge of each PNG (default 1568)")
    p.add_argument("--zoom", action="append", metavar="REGION", help="crop x,y,w,h (px or %%), WxH+X+Y or a map tile (r2c3, r2c3:r3c4) at full resolution; repeatable")
    p.add_argument("--map", action="store_true", help="overview with rulers and named tiles (r1c1 …) to zoom into; for big images")
    p.add_argument("--tile", type=int, help="map tile size in px (default: about one vision render, at most 100 tiles)")
    p.add_argument("--grid", nargs="?", const="auto", metavar="STEP", help="overlay a coordinate grid (STEP in pixels, or auto)")
    p.add_argument("--grid-units", choices=["px", "pct"], default="px", help="grid labels in original pixels (default) or percent")
    p.add_argument("--sheet", action="store_true", help="one contact sheet for all inputs")
    p.add_argument("--frames", action="store_true", help="frames/pages of animations and multi-page files, as a sheet")
    p.add_argument("--pages", help="with --frames: which frames, 1-based ('1-10,15', 'last')")
    p.add_argument("--max-frames", type=int, default=24, help="with --frames: sample at most this many (default 24)")
    p.add_argument("--page", type=int, default=1, help="frame or page to preview (1-based)")
    p.add_argument("--cols", type=int, help="contact-sheet columns")
    p.add_argument("--per-sheet", type=int, default=30, help="images per contact sheet (default 30)")
    p.add_argument("--bg", default="auto", help="background behind transparency: auto (checkerboard when transparent), white, black, checker, #rrggbb")
    p.add_argument("--no-orient", action="store_true", help="ignore the EXIF orientation")
    p.add_argument("--stretch", action="store_true", help="stretch 16-bit / float data to its min..max range")
    p.add_argument("--raw-develop", action="store_true", help="develop RAW files with LibRaw instead of using the embedded camera preview")
    p.add_argument("-r", "--recursive", action="store_true", help="include subfolders of folder inputs")
    p.add_argument("--workers", type=int, help="parallel workers for many inputs")
    p.add_argument("--no-cache", action="store_true", help="render again instead of reusing cached renders")
    from _paging import add_paging

    add_paging(p, "images", "with --sheet: at most N images per call (default 150 = 5 sheets of 30)")
    add_format(p)
    return p


def normalize_argv(argv: list[str]) -> list[str]:
    """Accepts a leading mode word: `img_view.py grid shot.png` = `img_view.py shot.png --grid`."""
    if argv and argv[0] in MODES:
        mode, rest = argv[0], argv[1:]
        if mode in ("grid", "sheet", "frames", "map"):
            rest = rest + [f"--{mode}"]
        return rest
    return argv


# ── rendering jobs (top-level so worker processes can run them) ─────────


def _unique(out_dir: Path, stem: str, taken: set[str], suffix: str = ".png") -> Path:
    name = f"{stem}{suffix}"
    n = 2
    while name.lower() in taken:
        name = f"{stem}-{n}{suffix}"
        n += 1
    taken.add(name.lower())
    return out_dir / name


VIEW_KEYS = ("max_edge", "zoom", "grid", "grid_units", "page", "bg", "no_orient", "stretch", "raw_develop", "map", "tile")


def peek_view(job: dict[str, Any]) -> dict[str, Any] | None:
    """view_one when its render is cached (cheap: a copy), else None."""
    from _cache import lookup
    from _img import code_version

    try:
        hit = lookup(job["input"], "img-view", {k: job.get(k) for k in VIEW_KEYS}, code_version("img_view.py", "_img.py", "_render.py", "_big.py"))
    except OSError:
        return None
    return view_one(job) if hit is not None else None


def view_one(job: dict[str, Any]) -> dict[str, Any]:
    """One render, from the file cache when this exact render of this file content was made before."""
    import json
    import shutil

    from _cache import cached_dir, release
    from _img import Uncached, code_version

    path = Path(job["input"])
    out = Path(job["out"])

    built: list[bool] = []

    def build(tmp: Path) -> None:
        built.append(True)
        rec = render_view({**job, "out": str(tmp / "view.png")})
        if "error" in rec:
            shutil.rmtree(tmp, ignore_errors=True)  # also when the cache is off, where nobody else removes it
            raise Uncached(rec)
        (tmp / "rec.json").write_text(json.dumps(rec), encoding="utf-8")

    try:
        d = cached_dir(path, "img-view", {k: job.get(k) for k in VIEW_KEYS}, code_version("img_view.py", "_img.py", "_render.py", "_big.py"), build)
    except Uncached as e:
        e.rec["input"] = str(path)
        return e.rec
    except OSError as e:
        return {"input": str(path), "error": f"{path.name}: cannot read it ({e})"}
    rec = json.loads((d / "rec.json").read_text(encoding="utf-8"))
    shutil.copyfile(d / "view.png", out)
    release(d)
    rec.update(input=str(path), output=str(out), cached=not built)
    return rec


#: The last full-resolution decode in this process, reused by further zooms into the same image when no tile store
#: can be kept (cache off): zooming three regions of a huge scan decodes it once.
_FULL: dict[tuple[Any, ...], Any] = {}


def _full_image(path: Path, job: dict[str, Any], raw: Any) -> Any:
    from _img import load

    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns, job["page"], job["no_orient"], job["raw_develop"])
    if key not in _FULL:
        _FULL.clear()
        _FULL[key] = load(path, frame=job["page"] - 1, orient=not job["no_orient"], max_edge=None, raw=raw)
    return _FULL[key]


def _zoom_box(zoom: str, full: tuple[int, int], tile: int | None) -> tuple[int, int, int, int]:
    from _big import is_tile_address, parse_tile_region
    from _img import parse_box

    if is_tile_address(zoom):
        return parse_tile_region(zoom, full, tile)
    return parse_box(zoom, full, "zoom region")


def _zoom_raster(path: Path, job: dict[str, Any], fit: int) -> tuple[Any, dict[str, Any], tuple[int, int, int, int]]:
    """The zoom region of a raster or RAW image, as few pixels as possible: from the tile store of a big image,
    from a reduced JPEG decode when the region is large, else from a full decode. Returns (image of the region,
    facts about the file, box)."""
    from _big import big_pixels, store_build, store_lookup, store_region
    from _img import RawOpts, load, probe

    pr = probe(path, job["page"] - 1, not job["no_orient"])
    full = pr["size"]
    box = _zoom_box(job["zoom"], full, job.get("tile"))
    rw, rh = box[2] - box[0], box[3] - box[1]
    need = min(1.0, fit / max(rw, rh))  # output px per original px
    facts = {"format": pr["format"], "size": full, "frames": pr["frames"], "kind": pr["kind"], "oriented": pr["oriented"]}
    raw = RawOpts(use_preview=False)
    pixels = full[0] * full[1]
    big = pixels >= big_pixels() or (pr["kind"] == "raw" and pixels >= 16_000_000)
    params = {"page": job["page"], "orient": not job["no_orient"], "raw": pr["kind"] == "raw"}
    if big:
        st = store_lookup(path, params)
        if st is not None:
            img, k = store_region(st, box, need)
            facts["note"] = "from the cached tile store"
            return img, facts, box
        ld = _full_image(path, job, raw)
        facts["note_ld"] = ld
        if store_build(path, ld.img, params) is not None:
            facts["note"] = "decoded once; later zooms read the cached tile store"
        return ld.img.crop(box), facts, box
    if need <= 0.5 and pr["kind"] == "raster":
        # A large region shown small: decode at reduced size (JPEG DCT scaling, or a strip reduce).
        ld = load(path, frame=job["page"] - 1, orient=not job["no_orient"], max_edge=max(64, int(max(full) * need) + 1))
        k = ld.img.size[0] / full[0]
        facts["note_ld"] = ld
        return ld.img.crop((round(box[0] * k), round(box[1] * k), round(box[2] * k), round(box[3] * k))), facts, box
    ld = _full_image(path, job, raw)
    facts["note_ld"] = ld
    return ld.img.crop(box), facts, box


def render_view(job: dict[str, Any]) -> dict[str, Any]:
    """Loads, orients, crops (zoom), fits and saves one render. Returns a record for the report."""
    from _img import RawOpts, kind_of, load, pil, shrink, to_view

    Image = pil()
    path = Path(job["input"])
    max_edge = job["max_edge"]
    zoom = job.get("zoom")
    rec: dict[str, Any] = {"input": str(path)}
    try:
        grid = job.get("grid")
        band = 30 if (grid or job.get("map")) else 0
        fit = max_edge - band
        origin = (0.0, 0.0)
        if zoom and kind_of(path) == "svg":
            from _big import is_tile_address
            from _img import probe

            if is_tile_address(zoom):
                l, t, r, b = _zoom_box(zoom, probe(path)["size"], job.get("tile"))
                zoom = f"{l},{t},{r - l},{b - t}"
            ld = _load_svg_for_zoom(path, zoom, max_edge)
            full = _display_size(ld)
            l, t, r, b = _zoom_box(zoom, full, job.get("tile"))
            img = ld.img
            if not ld.info.get("_svg_region"):
                k = ld.info.get("_svg_zoom", 1.0)
                img = img.crop((round(l * k), round(t * k), round(r * k), round(b * k)))
            facts: dict[str, Any] = {"format": "SVG", "size": full, "frames": 1, "kind": "svg", "oriented": 1}
            box = (l, t, r, b)
        elif zoom:
            img, facts, box = _zoom_raster(path, job, fit)
            full = facts["size"]
        else:
            raw = RawOpts(use_preview=not job["raw_develop"])
            ld = load(path, frame=job["page"] - 1, orient=not job["no_orient"], max_edge=max_edge, raw=raw)
            img = ld.img
            full = _display_size(ld)
            facts = {"format": ld.format, "size": full, "frames": ld.n_frames, "kind": ld.kind, "oriented": ld.oriented, "note_ld": ld}
            box = (0, 0, full[0], full[1])
        ld = facts.pop("note_ld", None)
        rec.update(format=facts["format"], size=list(full), frames=facts["frames"], kind=facts["kind"])
        if facts.get("oriented", 1) != 1:
            rec["orientation_applied"] = facts["oriented"]
        note = (ld.note if ld is not None else "") or facts.get("note", "")
        if note:
            rec["note"] = note
        if ld is not None and ld.info.get("_warning"):
            rec["warning"] = ld.info["_warning"]
        region = (box[0], box[1], box[2] - box[0], box[3] - box[1])
        if zoom:
            origin = (box[0], box[1])
            rec["region"] = list(region)
            from _big import is_tile_address

            if is_tile_address(job.get("zoom")):
                rec["tile"] = str(job["zoom"]).strip()
        if max(img.size) > fit and not job["stretch"]:
            # Shrink before colour conversion: an ICC transform of a 36 MP photo costs a second, of 2 MP nothing.
            img = shrink(img, fit)
        view, alpha_note = to_view(img, job["bg"], job["stretch"])
        if max(view.size) > fit:
            view = shrink(view, fit)
        elif zoom and max(view.size) < 600:
            f = max(1, min(8, fit // max(view.size)))
            if f > 1:
                view = view.resize((view.size[0] * f, view.size[1] * f), Image.NEAREST)
        scale = view.size[0] / region[2]
        if job.get("map"):
            from _big import map_grid

            view = draw_grid(view, origin, region[2:], scale, "auto", job["grid_units"], full)
            view = draw_tiles(view, scale, full, job.get("tile"))
            tw, th, cols, rows = map_grid(full, job.get("tile"))
            rec["map"] = {"cols": cols, "rows": rows, "tile_w": tw, "tile_h": th}
        elif grid:
            view = draw_grid(view, origin, region[2:], scale, grid, job["grid_units"], full)
        out = Path(job["out"])
        view.save(out, "PNG", compress_level=3)
        rec.update(output=str(out), shown=list(view.size), scale=round(scale, 4))
        if alpha_note:
            rec["transparency"] = alpha_note
    except SkillError as e:
        rec["error"] = str(e)
    except MemoryError:
        rec["error"] = f"{path.name}: not enough memory to decode it; lower DESK_MAX_PIXELS or free memory"
    except Exception as e:  # noqa: BLE001 — one bad file must not stop a batch
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def draw_tiles(view: Any, scale: float, full: tuple[int, int], tile: int | None) -> Any:
    """Tile boundaries of the map (thick blue lines) with each tile's name in its top-left corner."""
    from PIL import Image, ImageDraw

    from _big import tiles_of
    from _render import _font

    band = 30
    over = Image.new("RGBA", view.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    tiles = tiles_of(full, tile)
    fsize = 13
    font = _font(fsize)
    for t in tiles:
        x0, y0 = band + t["x"] * scale, band + t["y"] * scale
        x1, y1 = band + (t["x"] + t["w"]) * scale, band + (t["y"] + t["h"]) * scale
        d.rectangle([x0, y0, x1, y1], outline=(0, 90, 255, 200), width=2)
        if x1 - x0 >= 34 and y1 - y0 >= 18:
            label = t["tile"]
            w = d.textlength(label, font=font)
            d.rectangle([x0 + 2, y0 + 2, x0 + 6 + w, y0 + 4 + fsize + 2], fill=(0, 90, 255, 215))
            d.text((x0 + 4, y0 + 3), label, fill=(255, 255, 255, 255), font=font)
    return Image.alpha_composite(view.convert("RGBA"), over).convert("RGB")


def _huge_inputs(paths: set[str]) -> set[str]:
    """The inputs big enough for the tile store (their zooms are scheduled one after the other)."""
    from _big import big_pixels
    from _img import probe

    out = set()
    for ps in paths:
        try:
            pr = probe(Path(ps))
        except Exception:  # noqa: BLE001 — the render reports the problem
            continue
        px = pr["size"][0] * pr["size"][1]
        if px >= big_pixels() or (pr["kind"] == "raw" and px >= 16_000_000):
            out.add(ps)
    return out


def _display_size(ld: Any) -> tuple[int, int]:
    """Full-resolution size in display orientation."""
    w, h = ld.native_size
    return (h, w) if ld.oriented in (5, 6, 7, 8) else (w, h)


def _load_svg_for_zoom(path: Path, zoom: str, max_edge: int) -> Any:
    """Renders only the zoom region of an SVG, sharp at max_edge.

    The SVG is placed as an <image> in a wrapper whose viewBox is the region, so resvg draws just that part at any
    magnification. If that draws nothing (an unusual file name or SVG), the whole SVG is rendered large (≤ 64 MP)
    and cropped instead.
    """
    from xml.sax.saxutils import quoteattr

    from _img import Loaded, parse_box, render_svg, svg_intrinsic_size

    size = svg_intrinsic_size(path)
    if not size:
        img = render_svg(path)
        size = img.size
    fw, fh = float(size[0]), float(size[1])
    full = (max(1, round(fw)), max(1, round(fh)))
    l, t, r, b = parse_box(zoom, full, "zoom region")
    z = max(max_edge / max(r - l, b - t), 1e-3)
    ow, oh = max(1, round((r - l) * z)), max(1, round((b - t) * z))

    def wrapper(box: tuple[float, float, float, float], w: int, h: int) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" '
            f'viewBox="{box[0]} {box[1]} {box[2]} {box[3]}"><image x="0" y="0" width="{fw}" height="{fh}" '
            f'preserveAspectRatio="none" xlink:href={quoteattr(path.name)}/></svg>'
        )

    def drawn(im: Any) -> bool:
        return im.mode != "RGBA" or im.getchannel("A").getbbox() is not None

    try:
        img = render_svg(path, svg_string=wrapper((l, t, r - l, b - t), ow, oh))
        # A blank region is fine when the wrapper draws the whole SVG (the region is simply empty).
        if drawn(img) or drawn(render_svg(path, svg_string=wrapper((0, 0, fw, fh), 128, max(1, round(128 * fh / fw))))):
            return Loaded(img, "svg", "SVG", full, 1, {"_svg_region": True}, 1)
    except SkillError:
        pass
    z = min(z, math.sqrt(64e6 / (full[0] * full[1])))
    img = render_svg(path, zoom=z)
    return Loaded(img, "svg", "SVG", full, 1, {"_svg_zoom": img.size[0] / full[0]}, 1)


def _nice_step(span: float, target_lines: int = 10) -> float:
    raw = span / target_lines
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def draw_grid(view: Any, origin: tuple[float, float], region: tuple[int, int], scale: float, step_spec: str, units: str, full: tuple[int, int]) -> Any:
    """Adds rulers (top and left) and grid lines labelled in original-image coordinates."""
    from PIL import Image, ImageDraw

    from _render import _font

    band = 30
    vw, vh = view.size
    ox, oy = origin
    rw, rh = region
    if units == "pct":
        # Steps in percent of the full image.
        span_pct = max(rw / full[0], rh / full[1]) * 100
        step_pct = float(step_spec) if step_spec not in (None, "auto") else _nice_step(span_pct, 10)
        sx, sy = step_pct * full[0] / 100, step_pct * full[1] / 100
        fmt = (lambda v, total: f"{v / total * 100:g}%")
    else:
        step = float(step_spec) if step_spec not in (None, "auto") else _nice_step(max(rw, rh), 10)
        if step <= 0:
            raise UsageError("grid step must be positive")
        sx = sy = step
        fmt = (lambda v, total: f"{v:g}")
    canvas = Image.new("RGB", (vw + band, vh + band), (238, 238, 238))
    canvas.paste(view, (band, band))
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = _font(11)
    line = (255, 0, 140, 150)
    minor = (255, 0, 140, 70)

    def ticks(start: float, length: float, step: float) -> list[float]:
        first = math.ceil(start / step) * step
        out = []
        v = first
        while v <= start + length + 1e-9:
            out.append(v)
            v += step
        return out

    xs = ticks(ox, rw, sx)
    ys = ticks(oy, rh, sy)
    label_every_x = max(1, math.ceil(len(xs) / max(1, vw // 46)))
    label_every_y = max(1, math.ceil(len(ys) / max(1, vh // 18)))
    for i, v in enumerate(xs):
        x = band + (v - ox) * scale
        if x > band + vw:
            continue
        labelled = i % label_every_x == 0
        d.line([(x, band), (x, band + vh)], fill=line if labelled else minor, width=1)
        d.line([(x, band - 6), (x, band)], fill=(60, 60, 60, 255), width=1)
        if labelled:
            text = fmt(v, full[0])
            tw = d.textlength(text, font=font)
            tx = min(max(band, x - tw / 2), band + vw - tw)
            d.text((tx, 8), text, fill=(20, 20, 20, 255), font=font)
    for i, v in enumerate(ys):
        y = band + (v - oy) * scale
        if y > band + vh:
            continue
        labelled = i % label_every_y == 0
        d.line([(band, y), (band + vw, y)], fill=line if labelled else minor, width=1)
        d.line([(band - 6, y), (band, y)], fill=(60, 60, 60, 255), width=1)
        if labelled:
            text = fmt(v, full[1])
            # Vertical labels do not fit a 30 px band when long; write them small, right-aligned.
            tw = d.textlength(text, font=font)
            if tw > band - 4:
                text = text if len(text) <= 5 else f"{v / 1000:g}k"
                tw = d.textlength(text, font=font)
            ty = min(max(band, y - 6), band + vh - 12)
            d.text((max(1, band - 4 - tw), ty), text, fill=(20, 20, 20, 255), font=font)
    unit = "%" if units == "pct" else "px"
    d.text((2, 2), unit, fill=(90, 90, 90, 255), font=font)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    return canvas


def peek_thumb(job: tuple[str, int, str, str]) -> dict[str, Any] | None:
    from _cache import lookup
    from _img import code_version

    try:
        hit = lookup(job[0], "img-thumb", {"cell": job[1], "bg": job[2]}, code_version("img_view.py", "_img.py", "_render.py", "_big.py"))
    except OSError:
        return None
    return thumb_job(job) if hit is not None else None


def thumb_job(job: tuple[str, int, str, str]) -> dict[str, Any]:
    """A contact-sheet thumbnail, from the file cache when possible."""
    import json
    import shutil

    from _cache import cached_dir, release
    from _img import Uncached, code_version

    path, cell, bg, out = job

    def build(tmp: Path) -> None:
        rec = make_thumb((path, cell, bg, str(tmp / "thumb.png")))
        if "error" in rec:
            shutil.rmtree(tmp, ignore_errors=True)
            raise Uncached(rec)
        (tmp / "rec.json").write_text(json.dumps(rec), encoding="utf-8")

    try:
        d = cached_dir(path, "img-thumb", {"cell": cell, "bg": bg}, code_version("img_view.py", "_img.py", "_render.py", "_big.py"), build)
    except Uncached:
        # Unreadable files get an uncached placeholder tile.
        return make_thumb(job)
    except OSError:
        return make_thumb(job)
    rec = json.loads((d / "rec.json").read_text(encoding="utf-8"))
    shutil.copyfile(d / "thumb.png", out)
    release(d)
    rec.update(input=path, thumb=out)
    return rec


def make_thumb(job: tuple[str, int, str, str]) -> dict[str, Any]:
    """A thumbnail PNG for a contact sheet (or a placeholder tile when the file cannot be read)."""
    from PIL import Image, ImageDraw

    from _img import load, shrink, to_view

    path, cell, bg, out = job
    rec: dict[str, Any] = {"input": path, "thumb": out}
    try:
        ld = load(Path(path), max_edge=cell * 2)
        v, _ = to_view(shrink(ld.img, cell), bg)
        w, h = ld.native_size
        if ld.oriented in (5, 6, 7, 8):
            w, h = h, w
        rec.update(size=[w, h], format=ld.format, frames=ld.n_frames)
    except Exception as e:  # noqa: BLE001
        v = Image.new("RGB", (cell, cell * 3 // 4), (250, 235, 235))
        d = ImageDraw.Draw(v)
        d.text((10, 10), "cannot read", fill=(160, 0, 0))
        rec["error"] = str(e) if isinstance(e, SkillError) else f"{type(e).__name__}: {e}"
    v.save(out, "PNG")
    return rec


# ── modes ───────────────────────────────────────────────────────────────


def run_sheet(inputs: list[Any], args: Any, out_dir: Path, max_edge: int) -> dict[str, Any]:
    """Contact sheets. The whole result is cached by the content of every input, so looking again is instant."""
    import json
    import shutil

    from _cache import cached_dir, enabled, fingerprint, lookup, release
    from _img import code_version

    n = len(inputs)
    per = max(1, args.per_sheet)
    cols_default = args.cols or (n if n <= 4 else 3 if n <= 9 else 4 if n <= 16 else 5 if n <= 25 else 6)
    cols = max(1, min(cols_default, per))
    cell = max(64, min(400, (max_edge - 12 * (cols + 1)) // cols))
    base = _sheet_name(inputs, args)
    try:
        key: dict[str, Any] | None = {"files": [[str(inp.path), fingerprint(inp.path)] for inp in inputs], "per": per, "cols": cols,
                                      "cell": cell, "bg": args.bg, "max_edge": max_edge, "first": getattr(args, "_first", 0), "total": getattr(args, "_total", n)}
    except OSError:
        key = None  # an unreadable input: render without the sheet cache
    version = code_version("img_view.py", "_img.py", "_render.py", "_big.py")
    anchor = inputs[0].path
    hit = lookup(anchor, "img-sheet", key, version) if key else None
    if hit is not None:
        try:
            result = json.loads((hit / "result.json").read_text(encoding="utf-8"))
            names = result.pop("files")
            stems = result.pop("stems")
            sheets = []
            taken: set[str] = set()
            for name, stem in zip(names, stems):
                out = _unique(out_dir, stem, taken)
                shutil.copyfile(hit / name, out)
                sheets.append(str(out))
            result["sheets"] = sheets
            result["cached"] = True
            return result
        except (OSError, ValueError, KeyError):
            pass
    result = _make_sheets(inputs, args, out_dir, max_edge, per, cols, cell, base)
    if key and enabled() and not any("error" in r for r in result["images"]):

        def store(tmp: Path) -> None:
            names = []
            for i, sp in enumerate(result["sheets"]):
                names.append(f"sheet-{i + 1}.png")
                shutil.copyfile(sp, tmp / names[-1])
            (tmp / "result.json").write_text(json.dumps({**result, "sheets": [], "files": names, "stems": [Path(x).stem for x in result["sheets"]]}), encoding="utf-8")

        try:
            release(cached_dir(anchor, "img-sheet", key, version, store))
        except OSError:
            pass
    return result


def _make_sheets(inputs: list[Any], args: Any, out_dir: Path, max_edge: int, per: int, cols: int, cell: int, base: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="desk-sheet-", ignore_cleanup_errors=True) as tmp_s:
        return _make_sheets_in(Path(tmp_s), inputs, args, out_dir, max_edge, per, cols, cell, base)


def _make_sheets_in(tmp: Path, inputs: list[Any], args: Any, out_dir: Path, max_edge: int, per: int, cols: int, cell: int, base: str) -> dict[str, Any]:
    from _render import contact_sheet

    n = len(inputs)
    jobs = [(str(inp.path), cell, args.bg, str(tmp / f"t{i:05d}.png")) for i, inp in enumerate(inputs)]
    from _img import cached_map

    recs = cached_map(thumb_job, jobs, peek_thumb, workers=args.workers)
    sheets = []
    taken: set[str] = set()
    first = getattr(args, "_first", 0)
    total = getattr(args, "_total", n)
    names = getattr(args, "_names", None) or [Path(r["input"]).name for r in recs]
    pages = math.ceil(n / per)
    all_pages = math.ceil(total / per)
    for pi in range(pages):
        chunk = list(range(pi * per, min(n, (pi + 1) * per)))
        g = (first + chunk[0]) // per + 1
        labels = [f"{first + i + 1}. {names[i]}" for i in chunk]
        title = f"{total} images" + (f" — sheet {g} of {all_pages} (images {first + chunk[0] + 1}-{first + chunk[-1] + 1})" if all_pages > 1 else "")
        out = _unique(out_dir, f"{base}-sheet" + (f"-{g}" if all_pages > 1 else ""), taken)
        contact_sheet([recs[i]["thumb"] for i in chunk], out, labels=labels, cols=cols, cell=cell, title=title, max_edge=max_edge)
        sheets.append(str(out))
    for r in recs:
        r.pop("thumb", None)
    items = [{"n": first + i + 1, "name": names[i], **{k: v for k, v in r.items()}} for i, r in enumerate(recs)]
    return {"mode": "sheet", "sheets": sheets, "images": items}


def _sheet_name(inputs: list[Any], args: Any) -> str:
    if len(args.inputs) == 1:
        p = Path(args.inputs[0])
        name = p.name if p.is_dir() else p.stem
        name = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-.") or "images"
        return name
    return "images"


def run_frames(inputs: list[Any], args: Any, out_dir: Path, max_edge: int) -> dict[str, Any]:
    from _img import iter_frames, kind_of, shrink, to_view, _open, frame_count
    from _render import contact_sheet

    results = []
    taken: set[str] = set()
    for inp in inputs:
        path = inp.path
        rec: dict[str, Any] = {"input": str(path)}
        try:
            if kind_of(path) != "raster":
                raise SkillError(f"{path.name} has a single image; use preview")
            im = _open(path)
            try:
                n = frame_count(im)
                fmt = im.format
            finally:
                im.close()
            wanted = [i - 1 for i in parse_ranges(args.pages, n)]
            if len(wanted) > args.max_frames > 0:
                step = len(wanted) / args.max_frames
                wanted = [wanted[int(i * step)] for i in range(args.max_frames)]
            stem = _safe_stem(path)
            fdir = out_dir / f"{stem}-frames"
            fdir.mkdir(parents=True, exist_ok=True)
            frames, labels, times = [], [], []
            animated = fmt in ("GIF", "PNG", "WEBP", "AVIF")
            # Every frame's duration from the container (WebP ANMF headers, GIF/APNG frame headers): the total and
            # the start times are right however few frames are shown.
            from _img import animation_timing

            all_durs = animation_timing(path)[0] if animated else [0] * n
            width = max(3, len(str(n)))
            for i, fr, dur in iter_frames(path, wanted):
                dur = all_durs[i] if i < len(all_durs) and all_durs[i] else dur
                v, _ = to_view(shrink(fr, max_edge), args.bg)
                out = fdir / f"frame-{i + 1:0{width}d}.png"
                v.save(out, "PNG", compress_level=3)
                frames.append(str(out))
                start = sum(all_durs[:i]) / 1000
                labels.append(f"#{i + 1} · {dur} ms · {start:.2f}s" if animated else f"page {i + 1}")
                times.append({"frame": i + 1, "duration_ms": dur, "start_s": round(start, 3), "path": str(out)})
            total = sum(all_durs)
            sheet = _unique(out_dir, f"{stem}-frames-sheet", taken)
            cols = args.cols or (len(frames) if len(frames) <= 4 else 4 if len(frames) <= 16 else 6)
            cell = max(64, min(400, (max_edge - 12 * (cols + 1)) // cols))
            title = f"{path.name}: {n} {'frames' if fmt in ('GIF', 'PNG', 'WEBP', 'AVIF') else 'pages'}" + (f", {total / 1000:.2f}s" if total else "") + (f" (showing {len(frames)})" if len(frames) < n else "")
            contact_sheet(frames, sheet, labels=labels, cols=cols, cell=cell, title=title, max_edge=max_edge)
            rec.update(format=fmt, frames=n, shown=len(frames), total_ms=total, sheet=str(sheet), frame_dir=str(fdir), items=times)
        except SkillError as e:
            rec["error"] = str(e)
        results.append(rec)
    return {"mode": "frames", "results": results}


def _safe_stem(path: Path) -> str:
    s = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in path.stem).strip("-.")
    return s or "image"


def main() -> int:
    from _img import expand_inputs
    from _render import VISION_EDGE, announce

    args = build_parser().parse_args(normalize_argv(sys.argv[1:]))
    if args.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    max_edge = args.max_edge or VISION_EDGE
    if max_edge < 64 or max_edge > 8000:
        raise UsageError("--max-edge must be between 64 and 8000")
    if args.sheet and args.frames:
        raise UsageError("choose --sheet or --frames, not both")
    if args.page < 1:
        raise UsageError("--page is 1-based")
    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    out_dir = output_dir(args.out_dir)
    for inp in inputs:
        if same_file(out_dir, inp.path):
            raise UsageError("--out-dir is an input file")

    if args.sheet or (len(inputs) > 12 and not args.zoom and not args.grid and not args.frames and not args.out and not args.map):
        from _paging import check_paging, next_command, page_text

        check_paging(args)
        total = len(inputs)
        limit = args.limit or 150
        if args.offset >= total:
            raise UsageError(f"--offset {args.offset} is past the last image ({total})")
        chunk = inputs[args.offset : args.offset + limit]
        one_base = len({str(i.base) for i in inputs}) == 1
        args._first, args._total = args.offset, total
        args._names = [str(i.rel).replace("\\", "/") if one_base else str(i.path) for i in chunk]
        data = run_sheet(chunk, args, out_dir, max_edge)
        end = args.offset + len(chunk)
        if args.format == "json":
            res = {**data, "total": total, "offset": args.offset, "hint": "Look at them with view_image."}
            if end < total:
                res["next"] = next_command(end)
            emit(res)
        else:
            head = f"Contact sheet{'s' if len(data['sheets']) > 1 else ''} of images {args.offset + 1}-{end} of {total} (numbers match the labels):"
            lines = []
            for it in data["images"]:
                extra = f"{it['size'][0]}×{it['size'][1]} {it.get('format', '')}" if "size" in it else f"error: {it.get('error')}"
                lines.append(f"  {it['n']}. {it['name']} — {extra}")
            text = page_text(head, lines, args.offset, total, args.max_chars, "images")
            if end < total and "Next part" not in text:
                text += f"\n\n[images {args.offset + 1}-{end} of {total}; {total - end} more. Next sheets: {next_command(end)}]"
            announce(data["sheets"], text)
        return 0 if all("error" not in i for i in data["images"]) else 1

    if args.frames:
        data = run_frames(inputs, args, out_dir, max_edge)
        if args.format == "json":
            emit({**data, "hint": "Look at them with view_image."})
        else:
            notes, sheets = [], []
            for r in data["results"]:
                if "error" in r:
                    notes.append(f"{r['input']}: error: {r['error']}")
                    continue
                sheets.append(r["sheet"])
                notes.append(f"{Path(r['input']).name}: {r['format']}, {r['frames']} frame(s)" + (f", {r['total_ms'] / 1000:.2f}s total" if r["total_ms"] else "") + f"; showing {r['shown']}. Single frames: {r['frame_dir']}/frame-NNN.png")
            if sheets:
                announce(sheets, "\n".join(notes))
            else:
                print("\n".join("error: " + n.replace(": error: ", ": ", 1) for n in notes), file=sys.stderr)
        return 0 if all("error" not in r for r in data["results"]) else 1

    zooms = args.zoom or [None]
    if args.out and (len(inputs) > 1 or len(zooms) > 1):
        raise UsageError("--out takes one input and one render; use --out-dir")
    taken: set[str] = set()
    jobs = []
    for inp in inputs:
        for zi, z in enumerate(zooms):
            stem = _safe_stem(inp.path)
            if z:
                stem += "-zoom" + (f"{zi + 1}" if len(zooms) > 1 else "")
            if args.grid:
                stem += "-grid"
            if args.map and not z:
                stem += "-map"
            if args.out:
                out = Path(args.out)
                if out.suffix.lower() != ".png":
                    raise UsageError("--out must end in .png")
                if same_file(out, inp.path):
                    raise SkillError("refusing to overwrite the input")
                out.parent.mkdir(parents=True, exist_ok=True)
            else:
                out = _unique(out_dir, stem, taken)
            if same_file(out, inp.path):
                raise SkillError(f"refusing to overwrite the input {inp.path}")
            jobs.append({
                "input": str(inp.path), "out": str(out), "max_edge": max_edge, "zoom": z, "grid": args.grid,
                "grid_units": args.grid_units, "page": args.page, "bg": args.bg, "no_orient": args.no_orient,
                "stretch": args.stretch, "raw_develop": args.raw_develop, "map": bool(args.map and not z), "tile": args.tile,
            })
    from _img import cached_map

    # Zooms into a huge image run here, one after the other: the first decodes it (once) and stores its tiles, the
    # others read tiles; in parallel workers each would decode the whole image at the same time.
    huge = _huge_inputs({j["input"] for j in jobs if j["zoom"]})
    recs: list[Any] = [None] * len(jobs)
    rest = [i for i, j in enumerate(jobs) if not (j["zoom"] and j["input"] in huge)]
    for i, j in enumerate(jobs):
        if j["zoom"] and j["input"] in huge:
            recs[i] = view_one(j)
    for i, r in zip(rest, cached_map(view_one, [jobs[i] for i in rest], peek_view, workers=args.workers)):
        recs[i] = r
    if args.format == "json":
        emit({"mode": "preview", "images": recs, "hint": "Look at them with view_image."})
        return 0 if all("error" not in r for r in recs) else 1
    notes, outs, errors = [], [], []
    for r in recs:
        name = Path(r["input"]).name
        if "error" in r:
            err = str(r["error"])
            errors.append(err if name in err[: len(name) + 40] else f"{name}: {err}")
            continue
        outs.append(r["output"])
        w, h = r["size"]
        bits = [f"{name}: {r['format']} {w}×{h}"]
        if r.get("frames", 1) > 1 and not r.get("note", "").startswith("multi-resolution"):
            bits.append(f"{r['frames']} frames (showing {args.page}; --frames for all)")
        if r.get("orientation_applied"):
            bits.append(f"EXIF orientation {r['orientation_applied']} applied")
        if r.get("note"):
            bits.append(r["note"])
        if r.get("region"):
            x, y, rw, rh = r["region"]
            bits.append(f"region x={x} y={y} w={rw} h={rh}")
        s = r["scale"]
        if abs(s - 1) <= 1e-3:
            how = "actual pixels"
        elif s < 1:
            how = f"scaled to {s * 100:.3g}%: 1 px here ≈ {1 / s:.3g} px of the original"
        else:
            how = f"enlarged {s:.3g}×" + ("; vector, rendered sharp" if r.get("kind") == "svg" else "")
        bits.append(f"shown {r['shown'][0]}×{r['shown'][1]} ({how})")
        if r.get("transparency"):
            bits.append(r["transparency"])
        if r.get("warning"):
            bits.append("warning: " + r["warning"])
        if r.get("tile"):
            bits.insert(1, f"tile {r['tile']}")
        if s < 0.35 and not r.get("region") and not r.get("map"):
            bits.append("a big image: --map names tiles to zoom into (then --zoom r2c3), or --zoom x,y,w,h")
        notes.append(", ".join(bits))
        if r.get("map"):
            m = r["map"]
            cmd = f"python3 scripts/img_view.py {shlex.quote(r['input'])}"
            tile = f" --tile {args.tile}" if args.tile else ""
            notes.append(f"  map: {m['rows']} rows × {m['cols']} columns of {m['tile_w']}×{m['tile_h']} px tiles, r1c1 (top left) to r{m['rows']}c{m['cols']} (bottom right); "
                         f"tile rRcC covers x = (C-1)×{m['tile_w']}, y = (R-1)×{m['tile_h']}. Blue labels name them on the overview; the rulers are in original pixels.")
            notes.append(f"  zoom into a tile at full resolution: {cmd}{tile} --zoom r1c1 (a block: --zoom r1c1:r2c2; repeat --zoom for several)")
    if outs:
        announce(outs, "\n".join(notes + [f"error: {e}" for e in errors]))
    else:
        print("\n".join(f"error: {e}" for e in errors), file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    run_main(main)
