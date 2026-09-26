"""Images → PDF, one image per page, written directly (no re-encoding of JPEG sources, lossless Flate for graphics).

Pillow's own PDF writer re-encodes every page as a JPEG at quality 75, which blurs screenshots and text; this
writer keeps graphics lossless (Flate with PNG predictors) and photos as JPEG at the requested quality, and can place
images on paper sizes with margins.
"""

from __future__ import annotations

import io
import zlib
from pathlib import Path
from typing import Any, Sequence

from _common import SkillError, UsageError

PAPER_PT = {
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
}


def looks_like_photo(img: Any) -> bool:
    """Many distinct colours in a full-resolution sample → a photo (JPEG is fine); few → graphics (keep lossless)."""
    w, h = img.size
    s = 512
    box = (max(0, w // 2 - s // 2), max(0, h // 2 - s // 2), min(w, w // 2 + s // 2), min(h, h // 2 + s // 2))
    sample = img.crop(box).convert("RGB")
    return sample.getcolors(maxcolors=12000) is None


def _flate_with_predictor(img: Any) -> tuple[bytes, int, int]:
    """Raw pixels compressed with the PNG 'Up' predictor; returns (data, colours, columns)."""
    import numpy as np

    a = np.asarray(img, dtype=np.uint8)
    if a.ndim == 2:
        a = a[:, :, None]
    h, w, c = a.shape
    rows = a.reshape(h, w * c).astype(np.int16)
    up = np.empty_like(rows)
    up[0] = rows[0]
    up[1:] = rows[1:] - rows[:-1]
    filtered = np.empty((h, w * c + 1), dtype=np.uint8)
    filtered[:, 0] = 2  # PNG filter type 2 (Up) on every row
    filtered[:, 1:] = (up % 256).astype(np.uint8)
    return zlib.compress(filtered.tobytes(), 6), c, w


def page_box(img_size: tuple[int, int], dpi: float, page: str, margin: float) -> tuple[float, float, float, float, float, float]:
    """(page_w, page_h, x, y, draw_w, draw_h) in points."""
    iw, ih = img_size
    natural_w, natural_h = iw * 72.0 / dpi, ih * 72.0 / dpi
    key = page.lower().strip()
    if key in ("fit", "image", "auto"):
        return natural_w + 2 * margin, natural_h + 2 * margin, margin, margin, natural_w, natural_h
    landscape = None
    if key.endswith("-landscape"):
        key, landscape = key[: -len("-landscape")], True
    elif key.endswith("-portrait"):
        key, landscape = key[: -len("-portrait")], False
    if key not in PAPER_PT:
        raise UsageError(f"bad page size '{page}' (fit, {', '.join(PAPER_PT)}, optionally -landscape or -portrait)")
    pw, ph = PAPER_PT[key]
    if landscape is None:
        landscape = iw > ih
    if landscape:
        pw, ph = ph, pw
    aw, ah = pw - 2 * margin, ph - 2 * margin
    if aw <= 0 or ah <= 0:
        raise UsageError("the margin leaves no room on the page")
    # Never enlarge beyond the image's natural size at its DPI; shrink to fit.
    r = min(aw / natural_w, ah / natural_h, 1.0)
    dw, dh = natural_w * r, natural_h * r
    return pw, ph, (pw - dw) / 2, (ph - dh) / 2, dw, dh


def images_to_pdf(
    frames: Sequence[Any],
    out: Path,
    dpi: Any = None,
    quality: int | None = None,
    page: str = "fit",
    margin: float | None = None,
    lossless: bool = False,
    jpeg_sources: Sequence[bytes | None] | None = None,
    title: str | None = None,
) -> None:
    """Writes one page per image (L or RGB). `jpeg_sources[i]` embeds original JPEG bytes unchanged for frame i."""
    if not frames:
        raise SkillError("no pages to write")
    margin_pt = float(margin) if margin is not None else (0.0 if page.lower() in ("fit", "image", "auto") else 36.0)
    objs: list[bytes] = []

    def add(obj: bytes) -> int:
        objs.append(obj)
        return len(objs)

    catalog = add(b"")  # placeholders filled below
    pages_id = add(b"")
    kids: list[int] = []
    for i, img in enumerate(frames):
        d = _dpi_of(img, dpi)
        if img.mode not in ("L", "RGB"):
            from _img import flatten

            img = flatten(img, "white")
        pw, ph, x, y, dw, dh = page_box(img.size, d, page, margin_pt)
        src = jpeg_sources[i] if jpeg_sources and i < len(jpeg_sources) else None
        cs = b"/DeviceGray" if img.mode == "L" else b"/DeviceRGB"
        if src:
            data, filt, parms = src, b"/DCTDecode", b""
        elif lossless or not looks_like_photo(img):
            data, colors, cols = _flate_with_predictor(img)
            filt = b"/FlateDecode"
            parms = b"/DecodeParms << /Predictor 15 /Colors %d /BitsPerComponent 8 /Columns %d >>" % (colors, cols)
        else:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=int(quality or 90), optimize=True, subsampling=0 if int(quality or 90) >= 90 else 2)
            data, filt, parms = buf.getvalue(), b"/DCTDecode", b""
        w, h = img.size
        xobj = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 /Filter %s %s /Length %d >>\nstream\n"
            % (w, h, cs, filt, parms, len(data))
            + data
            + b"\nendstream"
        )
        content = b"q %.4f 0 0 %.4f %.4f %.4f cm /Im0 Do Q" % (dw, dh, x, y)
        cid = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        pid = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.4f %.4f] /Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, pw, ph, xobj, cid)
        )
        kids.append(pid)
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objs[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    info = add(b"<< /Producer (Desk images skill)" + (b" /Title " + _pdf_text(title) if title else b"") + b" >>")
    buf = io.BytesIO()
    buf.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        buf.write(b"%010d 00000 n \n" % off)
    buf.write(b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, info, xref))
    tmp = out.with_name(f".{out.name}.part")
    tmp.write_bytes(buf.getvalue())
    import os

    os.replace(tmp, out)


def _dpi_of(img: Any, dpi: Any) -> float:
    if dpi:
        v = dpi[0] if isinstance(dpi, (tuple, list)) else dpi
        return float(v)
    d = img.info.get("dpi")
    if d:
        try:
            v = float(d[0])
            if 50 <= v <= 2400:
                return v
        except (TypeError, ValueError, IndexError):
            pass
    return 150.0


def _pdf_text(s: str) -> bytes:
    try:
        raw = s.encode("latin-1")
        return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"
    except UnicodeEncodeError:
        return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"
