"""Huge images for the images skill: a tile map with stable addresses (r2c3), and a cached tile store so zooming
into a 200-megapixel image decodes it once, not once per look.

- `map_grid(size)`: the tiles the map shows. Each is about one vision-sized render (1568 px), so `--zoom r2c3` shows
  a tile at actual pixels; very large images get bigger tiles so there are at most 100.
- `parse_tile_region(spec, size)`: 'r2c3' or 'r2c3:r3c5' (a block of tiles) → a pixel box.
- The tile store (`store_lookup`, `store_build`, `store_region`): the upright image cut into 1024-px tiles at full
  resolution and at 1/4 scale, in the file cache (content-addressed like every cached result). PNG tiles for graphics,
  JPEG quality 92 without chroma subsampling for photos (the store of a 200 MP photo is about 100 MB).
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from _common import UsageError

STORE_TILE = 1024
STORE_LEVELS = (1, 4)


def big_pixels() -> int:
    """Images with at least this many pixels get a tile store when zoomed (DESK_TILE_MIN_MP, default 50)."""
    try:
        return int(float(os.environ.get("DESK_TILE_MIN_MP", "50")) * 1_000_000)
    except ValueError:
        return 50_000_000


# ── the map grid ────────────────────────────────────────────────────────


def map_grid(size: tuple[int, int], tile: int | None = None, max_tiles: int = 100) -> tuple[int, int, int, int]:
    """(tile width, tile height, cols, rows) of the map of an image of `size`."""
    from _render import VISION_EDGE

    W, H = size
    t = float(tile or VISION_EDGE)
    if t < 16:
        raise UsageError("--tile must be at least 16 px")
    cols, rows = max(1, math.ceil(W / t)), max(1, math.ceil(H / t))
    while not tile and cols * rows > max_tiles:
        t *= 1.15
        cols, rows = max(1, math.ceil(W / t)), max(1, math.ceil(H / t))
    return math.ceil(W / cols), math.ceil(H / rows), cols, rows


_ADDR = re.compile(r"\s*r(\d+)\s*c(\d+)\s*(?::\s*r(\d+)\s*c(\d+)\s*)?", re.I)


def is_tile_address(spec: Any) -> bool:
    return isinstance(spec, str) and _ADDR.fullmatch(spec) is not None


def parse_tile_region(spec: str, size: tuple[int, int], tile: int | None = None) -> tuple[int, int, int, int]:
    """'r2c3' or 'r2c3:r3c5' → (left, top, right, bottom) in pixels, on the map grid of `size`."""
    m = _ADDR.fullmatch(spec)
    if not m:
        raise UsageError(f"bad tile address '{spec}' (r2c3, or r2c3:r3c5 for a block)")
    tw, th, cols, rows = map_grid(size, tile)
    r0, c0 = int(m.group(1)), int(m.group(2))
    r1, c1 = (int(m.group(3)), int(m.group(4))) if m.group(3) else (r0, c0)
    r0, r1 = sorted((r0, r1))
    c0, c1 = sorted((c0, c1))
    if not (1 <= r0 and r1 <= rows and 1 <= c0 and c1 <= cols):
        raise UsageError(f"tile {spec} is outside the map ({rows} rows × {cols} columns, r1c1 to r{rows}c{cols})")
    W, H = size
    return (c0 - 1) * tw, (r0 - 1) * th, min(W, c1 * tw), min(H, r1 * th)


def tiles_of(size: tuple[int, int], tile: int | None = None) -> list[dict[str, Any]]:
    tw, th, cols, rows = map_grid(size, tile)
    W, H = size
    out = []
    for r in range(rows):
        for c in range(cols):
            x, y = c * tw, r * th
            out.append({"tile": f"r{r + 1}c{c + 1}", "x": x, "y": y, "w": min(tw, W - x), "h": min(th, H - y)})
    return out


# ── the tile store ──────────────────────────────────────────────────────


def _version() -> str:
    from _img import code_version

    return code_version("_big.py", "_img.py")


def store_lookup(path: Path, params: dict[str, Any]) -> Path | None:
    from _cache import lookup

    try:
        return lookup(path, "img-tiles", params, _version())
    except OSError:
        return None


def _looks_photographic(img: Any) -> bool:
    w, h = img.size
    s = 256
    for cx, cy in ((w // 2, h // 2), (w // 4, h // 3), (3 * w // 4, 2 * h // 3)):
        box = (max(0, cx - s // 2), max(0, cy - s // 2), min(w, cx + s // 2), min(h, cy + s // 2))
        if img.crop(box).getcolors(maxcolors=4096) is None:
            return True
    return False


def store_build(path: Path, img: Any, params: dict[str, Any]) -> Path | None:
    """Cuts the decoded, upright `img` into the tile store (in the file cache). None when the cache is off or full."""
    from concurrent.futures import ThreadPoolExecutor

    from _cache import cached_dir, enabled, transient

    if not enabled():
        return None
    from _img import normalize

    src = normalize(img)
    if src.mode not in ("L", "RGB", "RGBA", "LA"):
        src = src.convert("RGB")
    photo = src.mode in ("RGB", "L") and _looks_photographic(src)
    ext = ".jpg" if photo else ".png"

    def save(job: tuple[Any, Path]) -> None:
        tile, target = job
        if photo:
            tile.save(target, "JPEG", quality=92, subsampling=0)
        else:
            tile.save(target, "PNG", compress_level=1)

    def build(tmp: Path) -> None:
        W, H = src.size
        levels = []
        for f in STORE_LEVELS:
            lvl = src if f == 1 else src.reduce(f)
            d = tmp / f"L{f}"
            d.mkdir()
            jobs = []
            for y in range(0, lvl.size[1], STORE_TILE):
                for x in range(0, lvl.size[0], STORE_TILE):
                    jobs.append((lvl.crop((x, y, min(lvl.size[0], x + STORE_TILE), min(lvl.size[1], y + STORE_TILE))), d / f"{y // STORE_TILE}_{x // STORE_TILE}{ext}"))
                if len(jobs) >= 16:  # bounded memory: encode in small batches (the encoders release the GIL)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(save, jobs))
                    jobs = []
            if jobs:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(save, jobs))
            levels.append({"factor": f, "size": list(lvl.size)})
        (tmp / "meta.json").write_text(json.dumps({"size": [W, H], "mode": src.mode, "tile": STORE_TILE, "ext": ext, "levels": levels}), encoding="utf-8")

    try:
        d = cached_dir(path, "img-tiles", params, _version(), build)
    except OSError:
        return None
    if transient(d):
        import shutil

        shutil.rmtree(d, ignore_errors=True)
        return None
    return d


def store_meta(store: Path) -> dict[str, Any]:
    return json.loads((store / "meta.json").read_text(encoding="utf-8"))


def store_region(store: Path, box: tuple[int, int, int, int], scale: float) -> tuple[Any, float]:
    """The pixels of `box` (full-resolution coordinates) from the store, at the coarsest level that still has
    `scale` (output px per original px). Returns (image, its px per original px)."""
    from _img import pil

    Image = pil()
    meta = store_meta(store)
    f = 1
    for lv in meta["levels"]:
        if lv["factor"] > f and 1 / lv["factor"] >= scale:
            f = lv["factor"]
    lw, lh = next(lv["size"] for lv in meta["levels"] if lv["factor"] == f)
    t = meta["tile"]
    l, top, r, b = (box[0] // f, box[1] // f, min(lw, math.ceil(box[2] / f)), min(lh, math.ceil(box[3] / f)))
    r, b = max(r, l + 1), max(b, top + 1)
    canvas = Image.new(meta["mode"], (r - l, b - top))
    for ty in range(top // t, (b - 1) // t + 1):
        for tx in range(l // t, (r - 1) // t + 1):
            p = store / f"L{f}" / f"{ty}_{tx}{meta['ext']}"
            with Image.open(p) as im:
                im.load()
                tile = im if im.mode == meta["mode"] else im.convert(meta["mode"])
                canvas.paste(tile, (tx * t - l, ty * t - top))
    return canvas, 1.0 / f
