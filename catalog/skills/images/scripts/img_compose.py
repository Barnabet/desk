#!/usr/bin/env python3
"""Compose images: collages, before/after, animations, sprite sheets, icon sets, tiles.

Subcommands:
  grid      collage of many images in a grid, with captions and a title
  compare   images side by side (before/after) with labels, same height
  animate   GIF, WebP, APNG or AVIF from frames (files, a folder, or another animation): fps or per-frame durations,
            loop, boomerang, crossfade, a shared optimised palette for GIF
  sprite    sprite sheet (packed) plus a JSON map of every image's box (and optional CSS)
  icons     icon sets from one image or SVG: favicon.ico + web PNGs + webmanifest, app PNG sizes, macOS .icns and
            iconset, Windows .ico
  tiles     split an image into a grid or fixed-size tiles (with overlap) plus a JSON map of their boxes

Examples:
  python3 scripts/img_compose.py grid shots/*.png --out overview.png --cols 3 --title "Onboarding screens"
  python3 scripts/img_compose.py compare before.jpg after.jpg --out before-after.jpg --labels "Before,After"
  python3 scripts/img_compose.py animate frames/ --out demo.gif --fps 12 --max-edge 640 --boomerang
  python3 scripts/img_compose.py animate a.png b.png c.png --out slides.webp --durations 1500,1500,3000 --crossfade 6
  python3 scripts/img_compose.py sprite icons/*.png --out sprite.png --css sprite.css
  python3 scripts/img_compose.py icons logo.svg --out-dir icons/ --preset all
  python3 scripts/img_compose.py tiles big-scan.tif --out-dir tiles/ --tile 1568x1568 --overlap 64

Add --preview to write a vision-sized PNG of the result to renders/ and print it for view_image.
Outputs never overwrite inputs; existing files need --force.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from _common import UsageError, add_format, emit, human_size, output_dir, output_path, parser, pool_map, run_main


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: Any, out_file: bool = True) -> None:
        if out_file:
            sp.add_argument("--out", required=True, help="output file (extension picks the format)")
        sp.add_argument("--force", action="store_true", help="overwrite existing outputs")
        sp.add_argument("--preview", action="store_true", help="also write a vision-sized PNG of the result and print it")
        sp.add_argument("--quality", type=int, help="lossy quality 1-100")
        add_format(sp)

    g = sub.add_parser("grid", help="collage in a grid", formatter_class=p.formatter_class)
    g.add_argument("inputs", nargs="+")
    g.add_argument("--cols", type=int, help="columns (default: about square)")
    g.add_argument("--cell", default="400x400", help="cell size WxH (default 400x400)")
    g.add_argument("--fit", choices=["contain", "cover"], default="contain", help="contain (whole image) or cover (fill the cell, cropping)")
    g.add_argument("--gap", type=int, default=12, help="space between cells in px (default 12)")
    g.add_argument("--bg", default="white", help="background colour (default white; 'transparent' for PNG/WebP)")
    g.add_argument("--captions", default="auto", help="auto (file names), none, or captions separated by '|'")
    g.add_argument("--caption-size", type=int, default=18, help="caption font size in px (default 18)")
    g.add_argument("--title", help="title above the grid")
    g.add_argument("--font", help="font name or file for captions and title (default sans)")
    g.add_argument("-r", "--recursive", action="store_true")
    common(g)

    c = sub.add_parser("compare", help="side by side with labels", formatter_class=p.formatter_class)
    c.add_argument("inputs", nargs="+", help="two or more images, left to right")
    c.add_argument("--labels", help="labels separated by commas (default: Before,After for two images, else file names)")
    c.add_argument("--vertical", action="store_true", help="stack top to bottom")
    c.add_argument("--height", type=int, help="common height in px (default: the smallest input's height, at most 1200)")
    c.add_argument("--gap", type=int, default=16)
    c.add_argument("--bg", default="white")
    c.add_argument("--font", help="label font")
    common(c)

    a = sub.add_parser("animate", help="animation from frames", formatter_class=p.formatter_class)
    a.add_argument("inputs", nargs="+", help="frame files, a folder, globs, or animations (their frames are used)")
    a.add_argument("--fps", type=float, help="frames per second")
    a.add_argument("--duration", type=int, help="milliseconds per frame (default 100)")
    a.add_argument("--durations", help="per-frame milliseconds, comma separated")
    a.add_argument("--loop", type=int, default=0, help="0 = forever (default), else number of plays")
    a.add_argument("--size", help="frame size WxH (default: the first frame's)")
    a.add_argument("--max-edge", type=int, help="shrink frames so their long edge is at most this")
    a.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain", help="how frames of other sizes fit")
    a.add_argument("--bg", default="white", help="background for contain and for flattening (default white; 'transparent' keeps alpha)")
    a.add_argument("--boomerang", action="store_true", help="play forward then backward")
    a.add_argument("--crossfade", type=int, default=0, help="blended frames inserted between frames")
    a.add_argument("--hold-last", type=int, default=0, help="extra milliseconds on the last frame")
    a.add_argument("--colors", type=int, default=256, help="GIF palette size (default 256)")
    a.add_argument("--no-dither", action="store_true", help="GIF without dithering (flat graphics)")
    a.add_argument("--lossless", action="store_true", help="lossless WebP")
    a.add_argument("-r", "--recursive", action="store_true")
    common(a)

    s = sub.add_parser("sprite", help="sprite sheet + JSON map", formatter_class=p.formatter_class)
    s.add_argument("inputs", nargs="+")
    s.add_argument("--cols", type=int, help="grid columns (same-size images); default packs by rows")
    s.add_argument("--padding", type=int, default=2, help="pixels between sprites (default 2)")
    s.add_argument("--json", help="JSON map path (default: next to --out, .json)")
    s.add_argument("--css", help="also write CSS classes to this file")
    s.add_argument("--prefix", default="sprite", help="CSS class prefix (default sprite)")
    s.add_argument("-r", "--recursive", action="store_true")
    common(s)

    i = sub.add_parser("icons", help="icon sets", formatter_class=p.formatter_class)
    i.add_argument("input", help="source image (square, 1024 px or SVG is best)")
    i.add_argument("--out-dir", required=True)
    i.add_argument("--preset", choices=["web", "app", "macos", "windows", "all"], default="web")
    i.add_argument("--sizes", help="custom PNG sizes, comma separated (overrides the preset's PNG list)")
    i.add_argument("--padding", default="0", help="margin inside the icon, px or %% (default 0)")
    i.add_argument("--bg", default="transparent", help="background (default transparent; apple-touch-icon always gets one)")
    i.add_argument("--name", help="app name for site.webmanifest (default: the source file's name; set the real one)")
    i.add_argument("--short-name", help="short app name for site.webmanifest (default: --name)")
    i.add_argument("--theme-color", default="#ffffff", help="theme and background colour for site.webmanifest (default #ffffff)")
    common(i, out_file=False)

    t = sub.add_parser("tiles", help="split into tiles", formatter_class=p.formatter_class)
    t.add_argument("input")
    t.add_argument("--out-dir", required=True)
    t.add_argument("--grid", help="ROWSxCOLS, e.g. 3x3")
    t.add_argument("--tile", help="tile size WxH, e.g. 512x512 (edge tiles may be smaller)")
    t.add_argument("--overlap", type=int, default=0, help="pixels of overlap between neighbouring tiles")
    t.add_argument("--to", default="auto", help="tile format: auto (jpg for photos from JPEG/HEIC/RAW, else png), png, jpg, webp …")
    common(t, out_file=False)
    return p


# ── helpers ─────────────────────────────────────────────────────────────


def _load_rgba(path: Path, max_edge: int | None = None) -> Any:
    from _img import load, normalize

    ld = load(path, max_edge=max_edge)
    return normalize(ld.img).convert("RGBA")


def _load_job(job: tuple[str, int | None]) -> Any:
    return _load_rgba(Path(job[0]), job[1])


def _load_many(paths: list[Path], max_edge: int | None = None) -> list[Any]:
    jobs = [(str(p), max_edge) for p in paths]
    return pool_map(_load_job, jobs) if len(jobs) >= 8 else [_load_job(j) for j in jobs]


def _cell_fit(img: Any, box: tuple[int, int], fit: str) -> Any:
    from PIL import Image, ImageOps

    if fit == "cover":
        return ImageOps.fit(img, box, Image.LANCZOS)
    if fit == "stretch":
        return img.resize(box, Image.LANCZOS)
    c = img.copy()
    c.thumbnail(box, Image.LANCZOS)
    return c


def _save(img: Any, out: Path, quality: int | None, bg: str = "white") -> None:
    from _img import SaveOpts, out_format, save_image

    fmt, _ = out_format(None, out)
    save_image([img], out, fmt, SaveOpts(quality=quality, strip=True, bg=bg if bg != "transparent" else "white"))


def _preview_of(path: Path) -> str:
    from _img import load, to_view
    from _render import fit_edge

    d = Path("renders")
    d.mkdir(exist_ok=True)
    view, _ = to_view(load(path, orient=False).img, "auto")
    target = d / f"{path.stem}-{path.suffix.lstrip('.').lower()}-preview.png"
    fit_edge(view).save(target, "PNG", compress_level=3)
    return str(target)


def _caption_font(spec: str | None, size: int, text: str = "") -> Any:
    from _fonts import pil_font

    return pil_font(spec, size, text)[0]


def _text_w(draw: Any, text: str, font: Any) -> float:
    return draw.textlength(text, font=font)


def _ellipsize(draw: Any, text: str, font: Any, max_w: float) -> str:
    if _text_w(draw, text, font) <= max_w:
        return text
    while text and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


# ── grid ────────────────────────────────────────────────────────────────


def cmd_grid(args: Any) -> dict[str, Any]:
    from PIL import Image, ImageDraw

    from _img import expand_inputs, parse_color, parse_size

    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    n = len(inputs)
    cw, ch = parse_size(args.cell)
    if not cw or not ch:
        raise UsageError("--cell is WxH, e.g. 400x300")
    cols = args.cols or max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    imgs = _load_many([i.path for i in inputs], max(cw, ch) * 2)
    if args.captions == "auto":
        caps = [i.path.stem for i in inputs]
    elif args.captions == "none":
        caps = None
    else:
        caps = [c.strip() for c in args.captions.split("|")]
        if len(caps) < n:
            caps += [""] * (n - len(caps))
    gap = max(0, args.gap)
    cap_h = round(args.caption_size * 1.7) if caps else 0
    title_h = round(args.caption_size * 2.6) if args.title else 0
    W = gap + cols * (cw + gap)
    H = title_h + gap + rows * (ch + cap_h + gap)
    bg = parse_color(args.bg)
    sheet = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(sheet)
    fg = (20, 20, 20, 255) if sum(bg[:3]) > 380 or bg[3] < 128 else (240, 240, 240, 255)
    font = _caption_font(args.font, args.caption_size, "".join(caps or []))
    if args.title:
        tfont = _caption_font(args.font, round(args.caption_size * 1.5), args.title)
        d.text((gap, round(title_h * 0.25)), args.title, font=tfont, fill=fg)
    for k, im in enumerate(imgs):
        r, c = divmod(k, cols)
        x0 = gap + c * (cw + gap)
        y0 = title_h + gap + r * (ch + cap_h + gap)
        cell = _cell_fit(im, (cw, ch), args.fit)
        sheet.alpha_composite(cell, (x0 + (cw - cell.size[0]) // 2, y0 + (ch - cell.size[1]) // 2))
        if caps:
            text = _ellipsize(d, caps[k], font, cw)
            tw = _text_w(d, text, font)
            d.text((x0 + (cw - tw) / 2, y0 + ch + round(cap_h * 0.18)), text, font=font, fill=fg)
    out = output_path(args.out, [i.path for i in inputs], args.force)
    _save(sheet, out, args.quality, args.bg)
    return {"output": str(out), "size": [W, H], "images": n, "cols": cols, "rows": rows}


# ── compare ─────────────────────────────────────────────────────────────


def cmd_compare(args: Any) -> dict[str, Any]:
    from PIL import Image, ImageDraw

    from _img import expand_inputs, parse_color

    inputs = expand_inputs(args.inputs)
    if len(inputs) < 2:
        raise UsageError("compare needs at least two images")
    imgs = _load_many([i.path for i in inputs])
    if args.labels:
        labels = [s.strip() for s in args.labels.split(",")]
    else:
        labels = ["Before", "After"] if len(imgs) == 2 else [i.path.name for i in inputs]
    labels += [""] * (len(imgs) - len(labels))
    if args.vertical:
        target = args.height or min(min(im.size[0] for im in imgs), 1600)
        scaled = [im.resize((target, max(1, round(im.size[1] * target / im.size[0]))), Image.LANCZOS) if im.size[0] != target else im for im in imgs]
    else:
        target = args.height or min(min(im.size[1] for im in imgs), 1200)
        scaled = [im.resize((max(1, round(im.size[0] * target / im.size[1])), target), Image.LANCZOS) if im.size[1] != target else im for im in imgs]
    fsize = max(16, round((target if not args.vertical else target * 0.6) / 24))
    font = _caption_font(args.font, fsize, "".join(labels))
    lab_h = round(fsize * 1.8) if any(labels) else 0
    gap = args.gap
    if args.vertical:
        W = gap * 2 + max(s.size[0] for s in scaled)
        H = gap + sum(s.size[1] + lab_h + gap for s in scaled)
    else:
        W = gap + sum(s.size[0] + gap for s in scaled)
        H = gap * 2 + lab_h + max(s.size[1] for s in scaled)
    bg = parse_color(args.bg)
    canvas = Image.new("RGBA", (W, H), bg)
    d = ImageDraw.Draw(canvas)
    fg = (20, 20, 20, 255) if sum(bg[:3]) > 380 or bg[3] < 128 else (240, 240, 240, 255)
    x = y = gap
    for s, lab in zip(scaled, labels):
        if lab:
            d.text((x, y + round(lab_h * 0.12)), lab, font=font, fill=fg)
        canvas.alpha_composite(s, (x, y + lab_h))
        if args.vertical:
            y += lab_h + s.size[1] + gap
        else:
            x += s.size[0] + gap
    out = output_path(args.out, [i.path for i in inputs], args.force)
    _save(canvas, out, args.quality, args.bg)
    return {"output": str(out), "size": [W, H], "images": len(imgs), "labels": labels}


# ── animate ─────────────────────────────────────────────────────────────


def cmd_animate(args: Any) -> dict[str, Any]:
    from PIL import Image

    from _img import SaveOpts, expand_inputs, flatten, iter_frames, kind_of, out_format, parse_color, parse_size, save_image, _open, frame_count

    out_p = Path(args.out)
    fmt, _ = out_format(None, out_p)
    if fmt not in ("GIF", "WEBP", "PNG", "AVIF"):
        raise UsageError("animate writes .gif, .webp, .png (APNG) or .avif")
    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    frames: list[Any] = []
    src_durations: list[int] = []
    for inp in inputs:
        n = 1
        if kind_of(inp.path) == "raster":
            im = _open(inp.path)
            try:
                n = frame_count(im)
            finally:
                im.close()
        if n > 1:
            for _i, fr, dur in iter_frames(inp.path):
                frames.append(fr.convert("RGBA"))
                src_durations.append(dur)
        else:
            frames.append(_load_rgba(inp.path))
            src_durations.append(0)
    if not frames:
        raise UsageError("no frames")
    if args.size:
        w, h = parse_size(args.size, frames[0].size)
        size = (w or frames[0].size[0], h or round(frames[0].size[1] * (w or frames[0].size[0]) / frames[0].size[0]))
    else:
        size = frames[0].size
    if args.max_edge and max(size) > args.max_edge:
        r = args.max_edge / max(size)
        size = (max(1, round(size[0] * r)), max(1, round(size[1] * r)))
    bg = parse_color(args.bg)
    fitted = []
    for fr in frames:
        if fr.size != size:
            f2 = _cell_fit(fr, size, args.fit)
            if f2.size != size:
                canvas = Image.new("RGBA", size, bg)
                canvas.alpha_composite(f2, ((size[0] - f2.size[0]) // 2, (size[1] - f2.size[1]) // 2))
                f2 = canvas
            fr = f2
        if bg[3] == 255:
            fr = flatten(fr, args.bg).convert("RGBA")
        fitted.append(fr)
    # Durations.
    if args.durations:
        try:
            durs = [int(x) for x in args.durations.split(",") if x.strip()]
        except ValueError:
            raise UsageError("--durations takes integers in ms, comma separated") from None
        if len(durs) < len(fitted):
            durs += [durs[-1]] * (len(fitted) - len(durs))
        durs = durs[: len(fitted)]
    elif args.fps:
        durs = [max(10, round(1000 / args.fps))] * len(fitted)
    elif args.duration:
        durs = [args.duration] * len(fitted)
    elif any(src_durations):
        durs = [d or 100 for d in src_durations]
    else:
        durs = [100] * len(fitted)
    if args.crossfade > 0 and len(fitted) > 1:
        cf, cd = [], []
        steps = args.crossfade
        for k, fr in enumerate(fitted):
            nxt = fitted[(k + 1) % len(fitted)] if args.loop == 0 or k + 1 < len(fitted) else None
            cf.append(fr)
            hold = durs[k]
            if nxt is None:
                cd.append(hold)
                continue
            fade_ms = max(20, min(hold // 2, 60))
            cd.append(max(20, hold - fade_ms * steps // 2))
            for s_ in range(1, steps + 1):
                cf.append(Image.blend(fr, nxt, s_ / (steps + 1)))
                cd.append(fade_ms)
        fitted, durs = cf, cd
    if args.boomerang and len(fitted) > 2:
        fitted = fitted + fitted[-2:0:-1]
        durs = durs + durs[-2:0:-1]
    if args.hold_last:
        durs[-1] += args.hold_last
    out = output_path(out_p, [i.path for i in inputs], args.force)
    opts = SaveOpts(quality=args.quality, lossless=args.lossless, strip=True, durations=durs, loop=args.loop, colors=args.colors, dither=not args.no_dither)
    save_image(fitted, out, fmt, opts)
    res = {"output": str(out), "format": "APNG" if fmt == "PNG" else fmt, "frames": len(fitted), "size": list(size), "total_ms": sum(durs), "loop": args.loop, "bytes": out.stat().st_size}
    if args.preview:
        res["preview"] = _anim_preview(out, fitted, durs)
    return res


def _anim_preview(out: Path, frames: list[Any], durs: list[int]) -> str:
    import tempfile

    from _img import to_view
    from _render import VISION_EDGE, contact_sheet

    idx = list(range(len(frames)))
    if len(idx) > 16:
        step = len(idx) / 16
        idx = [idx[int(k * step)] for k in range(16)]
    tmp = Path(tempfile.mkdtemp(prefix="desk-anim-"))
    paths, labels = [], []
    for k in idx:
        p = tmp / f"f{k:05d}.png"
        to_view(frames[k], "auto")[0].save(p)
        paths.append(p)
        labels.append(f"#{k + 1} · {durs[k]} ms · {sum(durs[:k]) / 1000:.2f}s")
    Path("renders").mkdir(exist_ok=True)
    target = Path("renders") / f"{out.stem}-{out.suffix.lstrip('.')}-frames.png"
    cols = min(4, len(paths))
    contact_sheet(paths, target, labels=labels, cols=cols, cell=max(64, min(400, (VISION_EDGE - 12 * (cols + 1)) // cols)), title=f"{out.name}: {len(frames)} frames, {sum(durs) / 1000:.2f}s", max_edge=VISION_EDGE)
    for p in paths:
        p.unlink(missing_ok=True)
    tmp.rmdir()
    return str(target)


# ── sprite ──────────────────────────────────────────────────────────────


def _pack(sizes: list[tuple[int, int]], pad: int, cols: int | None) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Positions for each size: a grid when all sizes match (or cols is given), else shelf packing by height."""
    n = len(sizes)
    if cols or len(set(sizes)) == 1:
        cw = max(w for w, _ in sizes)
        ch = max(h for _, h in sizes)
        cols = cols or max(1, math.ceil(math.sqrt(n)))
        pos = [((k % cols) * (cw + pad), (k // cols) * (ch + pad)) for k in range(n)]
        rows = math.ceil(n / cols)
        return pos, (cols * (cw + pad) - pad, rows * (ch + pad) - pad)
    area = sum((w + pad) * (h + pad) for w, h in sizes)
    width = max(max(w for w, _ in sizes), int(math.sqrt(area) * 1.15))
    order = sorted(range(n), key=lambda k: (-sizes[k][1], -sizes[k][0]))
    pos: list[tuple[int, int]] = [(0, 0)] * n
    x = y = shelf_h = 0
    W = 0
    for k in order:
        w, h = sizes[k]
        if x and x + w > width:
            y += shelf_h + pad
            x, shelf_h = 0, 0
        pos[k] = (x, y)
        x += w + pad
        shelf_h = max(shelf_h, h)
        W = max(W, x - pad)
    return pos, (W, y + shelf_h)


def cmd_sprite(args: Any) -> dict[str, Any]:
    from PIL import Image

    from _img import expand_inputs

    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    imgs = _load_many([i.path for i in inputs])
    pos, size = _pack([im.size for im in imgs], max(0, args.padding), args.cols)
    sheet = Image.new("RGBA", size, (0, 0, 0, 0))
    names: dict[str, int] = {}
    frames: dict[str, Any] = {}
    for inp, im, (x, y) in zip(inputs, imgs, pos):
        name = inp.path.stem
        if name in names:
            names[name] += 1
            name = f"{name}-{names[name]}"
        else:
            names[name] = 1
        sheet.alpha_composite(im, (x, y))
        frames[name] = {"x": x, "y": y, "w": im.size[0], "h": im.size[1], "source": str(inp.path)}
    out = output_path(args.out, [i.path for i in inputs], args.force)
    _save(sheet, out, args.quality, "transparent")
    jpath = output_path(args.json or out.with_suffix(".json"), [i.path for i in inputs], args.force)
    jpath.write_text(json.dumps({"meta": {"image": out.name, "size": {"w": size[0], "h": size[1]}}, "frames": frames}, indent=2), encoding="utf-8")
    res = {"output": str(out), "json": str(jpath), "size": list(size), "sprites": len(frames)}
    if args.css:
        cpath = output_path(args.css, [i.path for i in inputs], args.force)
        lines = [f".{args.prefix} {{ background-image: url('{out.name}'); background-repeat: no-repeat; display: inline-block; }}"]
        for name, f in frames.items():
            cls = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)
            lines.append(f".{args.prefix}-{cls} {{ width: {f['w']}px; height: {f['h']}px; background-position: -{f['x']}px -{f['y']}px; }}")
        cpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        res["css"] = str(cpath)
    return res


# ── icons ───────────────────────────────────────────────────────────────

WEB_PNGS = {"favicon-16x16.png": 16, "favicon-32x32.png": 32, "favicon-48x48.png": 48, "apple-touch-icon.png": 180, "android-chrome-192x192.png": 192, "android-chrome-512x512.png": 512}
APP_SIZES = [16, 20, 24, 29, 32, 40, 48, 58, 60, 64, 72, 76, 80, 87, 96, 120, 128, 144, 152, 167, 180, 192, 256, 512, 1024]
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


class IconSource:
    """Renders the source at any square size: SVG re-rendered from vectors, rasters downscaled (small sizes sharpened)."""

    def __init__(self, path: Path, padding: str, bg: str):
        from _img import kind_of, parse_color

        self.path = path
        self.svg = kind_of(path) == "svg"
        self.padding = padding
        self.bg = parse_color(bg)
        self.cache: dict[int, Any] = {}
        if not self.svg:
            from _img import square_pad

            self.master = square_pad(_load_rgba(path))

    def at(self, size: int, bg: Any = None) -> Any:
        from PIL import Image, ImageFilter

        from _img import parse_len, render_svg, svg_intrinsic_size

        key = size if bg is None else -size
        if key in self.cache:
            return self.cache[key]
        pad = round(parse_len(self.padding, size))
        inner = max(1, size - 2 * pad)
        if self.svg:
            w, h = svg_intrinsic_size(self.path) or (inner, inner)
            r = inner / max(w, h)
            img = render_svg(self.path, width=max(1, round(w * r)), height=max(1, round(h * r))).convert("RGBA")
        else:
            img = self.master.resize((inner, inner), Image.LANCZOS)
            if inner <= 48 and self.master.size[0] > inner * 2:
                img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=1))
        fill = bg if bg is not None else self.bg
        canvas = Image.new("RGBA", (size, size), fill)
        canvas.alpha_composite(img, ((size - img.size[0]) // 2, (size - img.size[1]) // 2))
        self.cache[key] = canvas
        return canvas


def cmd_icons(args: Any) -> dict[str, Any]:
    from _img import input_file_any

    src_path = input_file_any(args.input)
    src = IconSource(src_path, args.padding, args.bg)
    outdir = output_dir(args.out_dir)
    written: list[str] = []
    notes: list[str] = []

    def save_png(name: str, img: Any) -> None:
        o = output_path(outdir / name, [src_path], args.force)
        img.save(o, "PNG", optimize=True)
        written.append(str(o))

    preset = args.preset
    custom = [int(s) for s in args.sizes.split(",")] if args.sizes else None
    if preset in ("web", "all"):
        o = output_path(outdir / "favicon.ico", [src_path], args.force)
        _ico_from_sizes(o, [src.at(s) for s in (16, 32, 48)])
        written.append(str(o))
        for name, size in WEB_PNGS.items():
            img = src.at(size, bg=(255, 255, 255, 255) if name == "apple-touch-icon.png" and src.bg[3] < 255 else None)
            save_png(name, img)
        if src.svg:
            import shutil

            o = output_path(outdir / "icon.svg", [src_path], args.force)
            shutil.copyfile(src_path, o)
            written.append(str(o))
        app_name = args.name or src_path.stem.replace("_", " ").replace("-", " ").strip() or "App"
        if not args.name:
            notes.append(f"site.webmanifest names the app '{app_name}' (from the file name); pass --name to set the real name")
        manifest = {"name": app_name, "short_name": args.short_name or app_name[:12], "icons": [
            {"src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png"}], "theme_color": args.theme_color, "background_color": args.theme_color, "display": "standalone"}
        o = output_path(outdir / "site.webmanifest", [src_path], args.force)
        o.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        written.append(str(o))
    if preset in ("app", "all") or custom:
        for size in custom or APP_SIZES:
            save_png(f"icon-{size}.png", src.at(size))
    if preset in ("macos", "all"):
        iconset = outdir / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for base, scale in ICONSET:
            name = f"icon.iconset/icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            save_png(name, src.at(base * scale))
        from _img import write_icns

        o = output_path(outdir / "icon.icns", [src_path], args.force)
        write_icns(o, src.at)  # every size rendered on its own (16 and 32 px included), sharp from an SVG
        written.append(str(o))
    if preset in ("windows", "all"):
        o = output_path(outdir / "icon.ico", [src_path], args.force)
        _ico_from_sizes(o, [src.at(s) for s in (16, 24, 32, 48, 64, 128, 256)])
        written.append(str(o))
    html = None
    if preset in ("web", "all"):
        html = "\n".join([
            '<link rel="icon" href="/favicon.ico" sizes="48x48">',
            *(['<link rel="icon" href="/icon.svg" type="image/svg+xml">'] if src.svg else []),
            '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">',
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
            '<link rel="manifest" href="/site.webmanifest">',
        ])
    res: dict[str, Any] = {"out_dir": str(outdir), "files": written, "source": "SVG (rendered per size)" if src.svg else f"raster {src.master.size[0]}x{src.master.size[1]}"}
    if not src.svg and src.master.size[0] < 512:
        res["warning"] = f"the source is only {src.master.size[0]} px; large icons are upscaled and look soft (use a 1024 px image or an SVG)"
    if notes:
        res["notes"] = notes
    if html:
        res["html"] = html
    if args.preview:
        from _render import contact_sheet

        shown = [p for p in written if p.endswith(".png")][:24]
        Path("renders").mkdir(exist_ok=True)
        target = Path("renders") / "icons-preview.png"
        contact_sheet(shown, target, labels=[Path(p).name for p in shown], cols=min(6, len(shown)), cell=200, title=f"{len(written)} icon files")
        res["preview"] = str(target)
    return res


def _ico_from_sizes(out: Path, images: list[Any]) -> None:
    """An ICO holding each size rendered separately (sharper than Pillow's downscaling of one image)."""
    import io
    import struct

    entries = []
    blobs = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        blobs.append(buf.getvalue())
        entries.append(im.size)
    offset = 6 + 16 * len(images)
    head = struct.pack("<HHH", 0, 1, len(images))
    dirs = b""
    for (w, h), blob in zip(entries, blobs):
        dirs += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    tmp = out.with_name(f".{out.name}.part")
    tmp.write_bytes(head + dirs + b"".join(blobs))
    import os

    os.replace(tmp, out)


# ── tiles ───────────────────────────────────────────────────────────────


def cmd_tiles(args: Any) -> dict[str, Any]:
    from _img import SaveOpts, input_file_any, load, out_format, parse_size, save_image

    src = input_file_any(args.input)
    ld = load(src)
    img = ld.img
    W, H = img.size
    to = args.to
    if to == "auto":
        to = "jpg" if ld.format in ("JPEG", "HEIF", "AVIF", "RAW", "MPO") and img.mode in ("RGB", "L") else "png"
    fmt, ext = out_format(to, None)
    if bool(args.grid) == bool(args.tile):
        raise UsageError("give --grid ROWSxCOLS or --tile WxH")
    ov = max(0, args.overlap)
    boxes = []
    if args.grid:
        rows, cols = parse_size(args.grid)
        if not rows or not cols:
            raise UsageError("--grid is ROWSxCOLS, e.g. 3x3")
        xs = [round(c * W / cols) for c in range(cols + 1)]
        ys = [round(r * H / rows) for r in range(rows + 1)]
        for r in range(rows):
            for c in range(cols):
                boxes.append((r, c, max(0, xs[c] - ov), max(0, ys[r] - ov), min(W, xs[c + 1] + ov), min(H, ys[r + 1] + ov)))
    else:
        tw, th = parse_size(args.tile)
        tw, th = tw or W, th or tw or H
        if tw <= ov or th <= ov:
            raise UsageError("the tile must be bigger than the overlap")
        step_x, step_y = tw - ov, th - ov
        r = 0
        for y in range(0, H, step_y):
            c = 0
            for x in range(0, W, step_x):
                boxes.append((r, c, x, y, min(W, x + tw), min(H, y + th)))
                c += 1
                if x + tw >= W:
                    break
            r += 1
            if y + th >= H:
                break
    outdir = output_dir(args.out_dir)
    tiles = []
    jobs = []
    for r, c, l, t, rr, b in boxes:
        o = output_path(outdir / f"{src.stem}_r{r + 1:02d}_c{c + 1:02d}{ext}", [src], args.force)
        jobs.append((o, (l, t, rr, b)))
        tiles.append({"file": o.name, "row": r + 1, "col": c + 1, "x": l, "y": t, "w": rr - l, "h": b - t})
    opts = SaveOpts(quality=args.quality or (92 if fmt == "JPEG" else None), strip=True)

    def save(job: tuple[Path, tuple[int, int, int, int]]) -> None:
        save_image([img.crop(job[1])], job[0], fmt, opts)

    from concurrent.futures import ThreadPoolExecutor

    from _common import workers_for

    # Encoders release the GIL: two threads halve the time without copying the image into other processes.
    for k in range(0, len(jobs), 8):
        with ThreadPoolExecutor(max_workers=workers_for(8, 2)) as pool:
            list(pool.map(save, jobs[k : k + 8]))
    manifest = output_path(outdir / f"{src.stem}_tiles.json", [src], args.force)
    manifest.write_text(json.dumps({"source": str(src), "size": [W, H], "overlap": ov, "tiles": tiles}, indent=2), encoding="utf-8")
    return {"out_dir": str(outdir), "tiles": len(tiles), "manifest": str(manifest), "size": [W, H], "first": tiles[0] if tiles else None}


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "quality", None) is not None and not 1 <= args.quality <= 100:
        raise UsageError("--quality must be 1-100")
    fn = {"grid": cmd_grid, "compare": cmd_compare, "animate": cmd_animate, "sprite": cmd_sprite, "icons": cmd_icons, "tiles": cmd_tiles}[args.cmd]
    res = fn(args)
    if args.preview and "preview" not in res and res.get("output"):
        res["preview"] = _preview_of(Path(res["output"]))
    if args.format == "json":
        emit(res)
        return 0
    lines = []
    if args.cmd in ("grid", "compare"):
        lines.append(f"{res['output']}: {res['size'][0]}×{res['size'][1]}, {res['images']} images")
    elif args.cmd == "animate":
        lines.append(f"{res['output']}: {res['format']} {res['size'][0]}×{res['size'][1]}, {res['frames']} frames, {res['total_ms'] / 1000:.2f}s, loop {res['loop'] or 'forever'}, {human_size(res['bytes'])}")
    elif args.cmd == "sprite":
        lines.append(f"{res['output']}: {res['size'][0]}×{res['size'][1]}, {res['sprites']} sprites; map {res['json']}" + (f"; css {res['css']}" if res.get("css") else ""))
    elif args.cmd == "icons":
        lines.append(f"{len(res['files'])} files in {res['out_dir']} (source: {res['source']}):")
        lines += [f"  {f}" for f in res["files"]]
        if res.get("warning"):
            lines.append(f"warning: {res['warning']}")
        for n in res.get("notes", []):
            lines.append(f"note: {n}")
        if res.get("html"):
            lines.append("HTML for the page <head>:\n" + res["html"])
    elif args.cmd == "tiles":
        lines.append(f"{res['tiles']} tiles of {res['size'][0]}×{res['size'][1]} in {res['out_dir']}; boxes in {res['manifest']}")
    print("\n".join(lines))
    if res.get("preview"):
        from _render import announce

        announce([res["preview"]])
    return 0


if __name__ == "__main__":
    run_main(main)
