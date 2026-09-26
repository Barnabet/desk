"""Image similarity for the images skill: pixel metrics (MAE, RMSE, PSNR, SSIM), changed regions, and perceptual
hashes (aHash, dHash, pHash) implemented with NumPy."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def as_array(img: Any, mode: str = "RGBA") -> np.ndarray:
    return np.asarray(img.convert(mode))


def gray(img: Any, max_edge: int | None = None) -> np.ndarray:
    from PIL import Image

    g = img.convert("L")
    if max_edge and max(g.size) > max_edge:
        g = g.copy()
        g.thumbnail((max_edge, max_edge), Image.BILINEAR)
    return np.asarray(g, dtype=np.float64)


def _box_mean(x: np.ndarray, k: int) -> np.ndarray:
    """Mean over k×k windows (valid region) with integral images."""
    c = np.cumsum(np.cumsum(np.pad(x, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def ssim(a: np.ndarray, b: np.ndarray, k: int = 7) -> tuple[float, np.ndarray]:
    """Mean SSIM of two greyscale float arrays (7×7 uniform windows) and the SSIM map."""
    if a.shape != b.shape:
        raise ValueError("ssim needs same-size images")
    if min(a.shape) < k:
        k = max(1, min(a.shape))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = _box_mean(a, k), _box_mean(b, k)
    aa, bb, ab = _box_mean(a * a, k), _box_mean(b * b, k), _box_mean(a * b, k)
    n = k * k
    cov_norm = n / (n - 1) if n > 1 else 1.0
    va = (aa - mu_a**2) * cov_norm
    vb = (bb - mu_b**2) * cov_norm
    cab = (ab - mu_a * mu_b) * cov_norm
    m = ((2 * mu_a * mu_b + c1) * (2 * cab + c2)) / ((mu_a**2 + mu_b**2 + c1) * (va + vb + c2))
    return float(m.mean()), m


def pixel_metrics(a: Any, b: Any, threshold: int = 10) -> dict[str, Any]:
    """Pixel metrics between two same-size RGBA images: changed %, MAE, RMSE, PSNR (all 0-255 scale).

    Computed in strips of rows, so two big images never need full-size 16-bit copies. PSNR is None for identical
    pixels (infinite; JSON has no infinity).
    """
    W, H = a.size
    alpha = a.getchannel("A").getextrema()[0] < 255 or b.getchannel("A").getextrema()[0] < 255
    ch = 4 if alpha else 3
    dmax = np.empty((H, W), dtype=np.uint8)
    total_abs = 0.0
    total_sq = 0.0
    rows = max(1, 2_000_000 // max(1, W))
    for y in range(0, H, rows):
        box = (0, y, W, min(H, y + rows))
        x = np.asarray(a.crop(box), dtype=np.int16)[..., :ch]
        z = np.asarray(b.crop(box), dtype=np.int16)[..., :ch]
        d = np.abs(x - z)
        dmax[y : y + rows] = d.max(axis=2)
        total_abs += float(d.sum(dtype=np.int64))
        total_sq += float((d.astype(np.int32) ** 2).sum(dtype=np.int64))
    n = max(1, W * H * ch)
    mse = total_sq / n
    changed = dmax > threshold
    return {
        "changed_pct": round(float(changed.mean() * 100), 4),
        "changed_pixels": int(changed.sum()),
        "mae": round(total_abs / n, 4),
        "rmse": round(math.sqrt(mse), 4),
        "psnr_db": round(10 * math.log10(255**2 / mse), 2) if mse > 0 else None,
        "max_diff": int(dmax.max()) if dmax.size else 0,
        "_mask": changed,
        "_dmax": dmax,
    }


def regions(mask: np.ndarray, max_regions: int = 30) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x, y, w, h) of changed areas: the mask on a block grid, dilated, then 8-connected groups."""
    h, w = mask.shape
    if not mask.any():
        return []
    block = max(4, int(max(h, w) / 200))
    gh, gw = math.ceil(h / block), math.ceil(w / block)
    pad = np.zeros((gh * block, gw * block), dtype=bool)
    pad[:h, :w] = mask
    grid = pad.reshape(gh, block, gw, block).any(axis=(1, 3))
    # Join changes that are a block apart.
    dil = grid.copy()
    dil[1:, :] |= grid[:-1, :]
    dil[:-1, :] |= grid[1:, :]
    dil[:, 1:] |= grid[:, :-1]
    dil[:, :-1] |= grid[:, 1:]
    labels = np.zeros(dil.shape, dtype=np.int32)
    boxes = []
    cur = 0
    ys, xs = np.nonzero(dil)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if labels[sy, sx]:
            continue
        cur += 1
        stack = [(sy, sx)]
        labels[sy, sx] = cur
        y0 = y1 = sy
        x0 = x1 = sx
        while stack:
            cy, cx = stack.pop()
            y0, y1, x0, x1 = min(y0, cy), max(y1, cy), min(x0, cx), max(x1, cx)
            for ny in (cy - 1, cy, cy + 1):
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= ny < gh and 0 <= nx < gw and dil[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = cur
                        stack.append((ny, nx))
        # Tighten the box to the real changed pixels inside it.
        sub = mask[y0 * block : (y1 + 1) * block, x0 * block : (x1 + 1) * block]
        yy, xx = np.nonzero(sub)
        if len(xx) == 0:
            continue
        bx, by = x0 * block + int(xx.min()), y0 * block + int(yy.min())
        boxes.append((bx, by, int(xx.max() - xx.min()) + 1, int(yy.max() - yy.min()) + 1))
    boxes.sort(key=lambda b: -b[2] * b[3])
    return boxes[:max_regions]


# ── perceptual hashes ───────────────────────────────────────────────────

_DCT_CACHE: dict[int, np.ndarray] = {}


def _dct_matrix(n: int) -> np.ndarray:
    if n not in _DCT_CACHE:
        k = np.arange(n)[:, None]
        i = np.arange(n)[None, :]
        m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * math.sqrt(2 / n)
        m[0, :] = 1 / math.sqrt(n)
        _DCT_CACHE[n] = m
    return _DCT_CACHE[n]


def _bits_to_int(bits: np.ndarray) -> int:
    v = 0
    for bit in bits.ravel():
        v = (v << 1) | int(bool(bit))
    return v


def _prep(img: Any) -> Any:
    from _img import flatten

    return flatten(img, "white").convert("L")


def ahash(img: Any) -> int:
    from PIL import Image

    a = np.asarray(_prep(img).resize((8, 8), Image.BOX), dtype=np.float64)
    return _bits_to_int(a > a.mean())


def dhash(img: Any) -> int:
    from PIL import Image

    a = np.asarray(_prep(img).resize((9, 8), Image.BOX), dtype=np.float64)
    return _bits_to_int(a[:, 1:] > a[:, :-1])


def phash(img: Any) -> int:
    """DCT hash: 32×32 greyscale → 2-D DCT → the 8×8 lowest frequencies against their median."""
    from PIL import Image

    a = np.asarray(_prep(img).resize((32, 32), Image.LANCZOS), dtype=np.float64)
    m = _dct_matrix(32)
    d = m @ a @ m.T
    low = d[:8, :8]
    return _bits_to_int(low > np.median(low))


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def pairwise_close(hashes: np.ndarray, limit: int) -> list[tuple[int, int, int]]:
    """(i, j, distance) for every pair of 64-bit hashes within `limit` bits, vectorised in chunks."""
    n = len(hashes)
    out: list[tuple[int, int, int]] = []
    h = hashes.astype(np.uint64)
    chunk = 512
    for s in range(0, n, chunk):
        blk = h[s : s + chunk]
        x = blk[:, None] ^ h[None, :]
        dist = np.bitwise_count(x) if hasattr(np, "bitwise_count") else _popcount(x)
        ii, jj = np.nonzero(dist <= limit)
        for i, j in zip(ii.tolist(), jj.tolist()):
            gi = s + i
            if j > gi:
                out.append((gi, j, int(dist[i, j])))
    return out


def _popcount(x: np.ndarray) -> np.ndarray:  # pragma: no cover — NumPy < 2.0
    x = x.copy()
    c = np.zeros(x.shape, dtype=np.uint8)
    while x.any():
        c += (x & np.uint64(1)).astype(np.uint8)
        x >>= np.uint64(1)
    return c
