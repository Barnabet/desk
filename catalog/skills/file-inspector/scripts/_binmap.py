"""Per-block entropy and byte-class profiles of a binary file, and the map PNG that shows them.

Histograms come from Pillow (C speed); blocks of big files are sampled (64 KB per block) so a multi-GB file is
profiled in seconds. The profile is cached by content fingerprint.
"""

from __future__ import annotations

import math
import os
from typing import Any

VERSION = "1"
SAMPLE = 64 * 1024
CLASSES = ("zero", "text", "control", "high", "ff")
COLORS = {"zero": (20, 20, 20), "text": (52, 120, 246), "control": (46, 170, 90), "high": (214, 69, 69), "ff": (240, 240, 240)}


def histogram(data: bytes) -> list[int]:
    if not data:
        return [0] * 256
    try:
        from PIL import Image

        w = min(len(data), 16384)
        h = -(-len(data) // w)
        pad = w * h - len(data)
        img = Image.frombytes("L", (w, h), data + b"\x00" * pad)
        hist = img.histogram()
        hist[0] -= pad
        return hist
    except ImportError:
        counts = [0] * 256
        for b in data:
            counts[b] += 1
        return counts


def entropy_of(hist: list[int]) -> float:
    n = sum(hist)
    if not n:
        return 0.0
    return -sum(c / n * math.log2(c / n) for c in hist if c)


def classes_of(hist: list[int]) -> dict[str, float]:
    n = sum(hist) or 1
    text = sum(hist[32:127]) + hist[9] + hist[10] + hist[13]
    zero = hist[0]
    ff = hist[255]
    control = sum(hist[1:32]) - hist[9] - hist[10] - hist[13] + hist[127]
    high = sum(hist[128:255])
    return {"zero": zero / n, "text": text / n, "control": control / n, "high": high / n, "ff": ff / n}


def category(ent: float, cls: dict[str, float]) -> str:
    if cls["zero"] > 0.9 or cls["ff"] > 0.9:
        return "padding (zeros)" if cls["zero"] >= cls["ff"] else "padding (0xFF)"
    if cls["text"] > 0.85 and ent < 6.0:
        return "text"
    if ent > 7.5:
        return "compressed or encrypted"
    if ent < 4.0:
        return "sparse data"
    return "code or structured data"


def profile(path: str, blocks: int = 512, use_cache: bool = True) -> dict[str, Any]:
    """{'size', 'block', 'sampled', 'entropy' (whole file, from samples when big), 'blocks': [[offset, entropy, classes…]]}."""

    def build() -> dict[str, Any]:
        size = os.path.getsize(path)
        n = max(1, min(blocks, -(-size // 256)))
        block = max(256, -(-size // n))
        sampled = block > SAMPLE * 2
        rows = []
        total = [0] * 256
        with open(path, "rb") as f:
            for i in range(n):
                off = i * block
                if off >= size:
                    break
                f.seek(off)
                data = f.read(min(block, SAMPLE) if sampled else block)
                h = histogram(data)
                for j, c in enumerate(h):
                    total[j] += c
                cls = classes_of(h)
                rows.append([off, round(entropy_of(h), 3)] + [round(cls[c], 3) for c in CLASSES])
        return {"size": size, "block": block, "sampled": sampled, "entropy": round(entropy_of(total), 3), "classes": {k: round(v, 3) for k, v in classes_of(total).items()}, "blocks": rows}

    size = os.path.getsize(path)
    if use_cache and size >= 16 * 1024 * 1024:
        from _cache import cached_json

        return cached_json(path, "fi-binprofile", {"blocks": blocks}, VERSION, build)
    return build()


def regions(prof: dict[str, Any]) -> list[dict[str, Any]]:
    """Adjacent blocks of the same category merged into regions."""
    out: list[dict[str, Any]] = []
    size = prof["size"]
    for row in prof["blocks"]:
        off, ent = row[0], row[1]
        cls = dict(zip(CLASSES, row[2:]))
        cat = category(ent, cls)
        if out and out[-1]["kind"] == cat:
            r = out[-1]
            r["end"] = min(size, off + prof["block"])
            r["_e"].append(ent)
        else:
            out.append({"start": off, "end": min(size, off + prof["block"]), "kind": cat, "_e": [ent]})
    for r in out:
        es = r.pop("_e")
        r["entropy"] = round(sum(es) / len(es), 2)
        r["size"] = r["end"] - r["start"]
    return out


def render_map(path: str, prof: dict[str, Any], out: Any, title: str, marks: list[tuple[int, str]] | None = None) -> Any:
    from PIL import Image, ImageDraw

    from _common import human_size
    from _render import _font

    W, H = 1500, 690
    L, R = 90, 30
    top_t, top_h = 90, 330
    strip_t, strip_h = top_t + top_h + 50, 110
    size = prof["size"] or 1
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    dr.text((L, 16), title[:150], fill="#111111", font=_font(22, title[:150]))
    sub = f"{size:,} bytes; {len(prof['blocks'])} blocks of {prof['block']:,} bytes" + (" (sampled 64 KB each)" if prof["sampled"] else "") + f"; overall entropy {prof['entropy']:.2f} bits/byte"
    dr.text((L, 48), sub, fill="#444444", font=_font(15))
    pw = W - L - R
    # entropy panel
    for e in (0, 2, 4, 6, 8):
        y = top_t + top_h - top_h * e / 8
        dr.line([(L, y), (W - R, y)], fill="#e8e8e8")
        dr.text((L - 30, y - 8), str(e), fill="#555555", font=_font(13))
    dr.text((8, top_t - 26), "entropy (bits/byte)", fill="#333333", font=_font(14))
    dr.rectangle([L, top_t + top_h - top_h * 8 / 8, W - R, top_t + top_h - top_h * 7.5 / 8], fill="#fbe9e9")
    pts = []
    for row in prof["blocks"]:
        x = L + pw * (row[0] + prof["block"] / 2) / size
        y = top_t + top_h - top_h * row[1] / 8
        pts.append((x, y))
    if len(pts) == 1:
        pts.append((pts[0][0] + 1, pts[0][1]))
    dr.line(pts, fill="#1f5fbf", width=2)
    # byte-class strip
    dr.text((8, strip_t - 26), "byte classes", fill="#333333", font=_font(14))
    for row in prof["blocks"]:
        x0 = L + pw * row[0] / size
        x1 = max(x0 + 1, L + pw * min(size, row[0] + prof["block"]) / size)
        mix = dict(zip(CLASSES, row[2:]))
        col = tuple(int(sum(COLORS[c][k] * mix[c] for c in CLASSES) / max(1e-9, sum(mix.values()))) for k in range(3))
        dr.rectangle([x0, strip_t, x1, strip_t + strip_h], fill=col)
    dr.rectangle([L, strip_t, W - R, strip_t + strip_h], outline="#888888")
    # signature marks
    if marks:
        for i, (off, label) in enumerate(marks[:40]):
            x = L + pw * off / size
            dr.line([(x, top_t), (x, strip_t + strip_h)], fill="#ff8c00", width=1)
            dr.text((x + 3, top_t + 4 + 16 * (i % 6)), label[:18], fill="#b35c00", font=_font(12))
    # axis
    f13 = _font(13)
    f12 = _font(12)
    # round ticks: a power of two giving 4 to 8 steps, labelled in hex with the size under it; the file's end last
    step = 1
    while size / step > 8:
        step *= 2
    ticks = list(range(0, size, step))
    right = -1e9
    for off in ticks + [size]:
        x = L + pw * off / size
        lab = f"0x{off:X}"
        sub = human_size(off) if off else ""
        tw = max(dr.textlength(lab, font=f13), dr.textlength(sub, font=f12))
        tx = x if off == 0 else min(x - tw / 2, W - R - tw)  # the end label ends at the edge: never clipped
        if tx < right + 14:
            continue  # too close to the previous label (the end right after a tick)
        dr.line([(x, strip_t + strip_h), (x, strip_t + strip_h + 6)], fill="#333333")
        dr.text((tx, strip_t + strip_h + 9), lab, fill="#333333", font=f13)
        if sub:
            dr.text((tx, strip_t + strip_h + 25), sub, fill="#777777", font=f12)
        right = tx + tw
    lx = L
    ly = H - 44
    # uniform random bytes (compressed or encrypted data) blend the classes into one colour: show it in the legend
    uni = {"zero": 1 / 256, "text": 98 / 256, "control": 30 / 256, "high": 127 / 256, "ff": 0.0}
    random_col = tuple(int(sum(COLORS[c][k] * uni[c] for c in CLASSES)) for k in range(3))
    f14 = _font(14)
    for col, label in ((COLORS["zero"], "0x00"), (COLORS["text"], "printable text"), (COLORS["control"], "control bytes"), (COLORS["high"], "bytes >= 0x80"), (COLORS["ff"], "0xFF"), (random_col, "random mix (compressed/encrypted)")):
        dr.rectangle([lx, ly, lx + 18, ly + 16], fill=col, outline="#888888")
        dr.text((lx + 24, ly - 1), label, fill="#333333", font=f14)
        lx += int(24 + dr.textlength(label, font=f14) + 34)
    if marks:
        dr.line([(lx, ly + 8), (lx + 18, ly + 8)], fill="#ff8c00", width=2)
        dr.text((lx + 24, ly - 1), "embedded file signature", fill="#333333", font=_font(14))
    img.save(out)
    return out
