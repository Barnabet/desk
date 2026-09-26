#!/usr/bin/env python3
"""Self-test for the images skill: builds fixtures in a temp folder, runs every script exactly as an agent would
(python3 scripts/<name>.py …, in parallel groups), and checks the real outputs: sizes, pixels, metadata, frames,
round trips, rendered PNG dimensions, cache hits, and that no input was modified.

Usage: python3 scripts/selftest.py [-k NAME] [-v]
Needs no network. None of these scripts uses LibreOffice; the children run with DESK_SOFFICE=none anyway.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
PY = sys.executable
VIEW_HINT = "Look at them with view_image."
LOCK = threading.Lock()
RESULTS = {"checks": 0, "failures": []}
VERBOSE = False
ROOT: Path
FX: Path
CACHE: Path


# ── harness ─────────────────────────────────────────────────────────────


def ok(cond: Any, name: str, detail: Any = "") -> bool:
    with LOCK:
        RESULTS["checks"] += 1
        if not cond:
            RESULTS["failures"].append(f"{name}: {str(detail)[:600]}")
        elif VERBOSE:
            print(f"  ok {name}")
    return bool(cond)


def run(script: str, *args: Any, cwd: Path | None = None, expect: int | None = 0, env: dict[str, str] | None = None, timeout: float = 120) -> tuple[int, str, str]:
    e = dict(os.environ)
    e.update({"DESK_SOFFICE": "none", "PYTHONDONTWRITEBYTECODE": "1", "DESK_FILE_CACHE": str(CACHE), "PYTHONIOENCODING": "utf-8"})
    if env:
        e.update(env)
    cmd = [PY, str(HERE / script), *[str(a) for a in args]]
    t = time.time()
    r = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, env=e, timeout=timeout)
    out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    if VERBOSE:
        print(f"  $ {script} {' '.join(str(a) for a in args)[:120]}  ({time.time() - t:.2f}s, exit {r.returncode})")
    if expect is not None:
        ok(r.returncode == expect, f"{script} {' '.join(str(a) for a in args)[:80]} exits {expect}", f"exit {r.returncode}; stderr: {err[-500:]} stdout: {out[-300:]}")
    return r.returncode, out, err


def jrun(script: str, *args: Any, cwd: Path | None = None, expect: int | None = 0, env: dict[str, str] | None = None) -> Any:
    code, out, err = run(script, *args, "--format", "json", cwd=cwd, expect=expect, env=env)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        ok(False, f"{script} {args[:3]} prints JSON", out[:300] + err[-300:])
        return {}


def img(path: Path) -> Any:
    from PIL import Image

    im = Image.open(path)
    im.load()
    return im


def workdir(name: str) -> Path:
    d = ROOT / "work" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── fixtures ────────────────────────────────────────────────────────────


def photo_array(w: int, h: int, seed: int = 1) -> Any:
    import numpy as np

    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 128 + 100 * np.sin(x / (w / 9) + seed)
    g = 128 + 90 * np.cos(y / (h / 8))
    b = 128 + 60 * np.sin((x + y) / (w / 6))
    a = np.stack([r, g, b], 2) + rng.normal(0, 10, (h, w, 3))
    return np.clip(a, 0, 255).astype(np.uint8)


def make_fixtures() -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    import pillow_heif

    pillow_heif.register_heif_opener()
    FX.mkdir(parents=True, exist_ok=True)
    # A photo with camera EXIF, GPS, orientation 6 (stored landscape, shown portrait) and an XMP title.
    ph = Image.fromarray(photo_array(1600, 1200))
    d = ImageDraw.Draw(ph)
    d.rectangle([100, 100, 300, 250], fill=(250, 250, 250))  # a white patch at the stored top-left
    ex = Image.Exif()
    ex[0x010F], ex[0x0110], ex[0x0112], ex[0x0131] = "Canon", "EOS R5", 6, "Desk selftest"
    ex[0x8769] = {0x829A: 1 / 250, 0x829D: 2.8, 0x8827: 400, 0x9003: "2026:07:14 18:30:05", 0xA434: "RF24-70mm F2.8 L IS USM", 0x920A: 50.0}
    ex[0x8825] = {1: "N", 2: (48.0, 51.0, 29.5), 3: "E", 4: (2.0, 17.0, 40.2), 5: b"\x00", 6: 35.0}
    xmp = (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="4">'
           b'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Eiffel sunset</rdf:li></rdf:Alt></dc:title>'
           b'<dc:subject><rdf:Bag><rdf:li>paris</rdf:li><rdf:li>tower</rdf:li></rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    ph.save(FX / "photo.jpg", quality=90, exif=ex.tobytes(), xmp=xmp)
    ph.save(FX / "photo.heic", quality=70, exif=ex.tobytes())
    Image.fromarray(photo_array(640, 480, 3)).save(FX / "photo.avif", quality=60)
    # A transparent logo.
    logo = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse([40, 40, 360, 360], fill=(220, 30, 30, 255))
    logo.save(FX / "logo.png")
    # A screenshot-like image.
    shot = Image.new("RGB", (1200, 800), (240, 240, 245))
    d = ImageDraw.Draw(shot)
    d.rectangle([0, 0, 1200, 70], fill=(40, 60, 120))
    d.rectangle([100, 150, 400, 210], fill=(30, 144, 255))
    d.rectangle([100, 260, 1000, 320], fill="white", outline="gray")
    d.text((120, 280), "email: jane@example.com", fill="black")
    shot.save(FX / "shot.png")
    # A green screen with a subject that has a green square inside.
    g = Image.new("RGB", (600, 400), (0, 200, 0))
    dg = ImageDraw.Draw(g)
    dg.ellipse([150, 50, 450, 350], fill=(200, 60, 40))
    dg.rectangle([270, 170, 330, 230], fill=(0, 200, 0))
    g.save(FX / "green.png")
    # A bordered image for trim.
    b = Image.new("RGB", (500, 400), "white")
    ImageDraw.Draw(b).rectangle([100, 80, 299, 279], fill=(10, 80, 200))
    b.save(FX / "bordered.png")
    # An animated GIF with 12 frames and varying durations.
    frames = [Image.new("RGB", (160, 120), (i * 20, 40, 255 - i * 20)) for i in range(12)]
    for i, f in enumerate(frames):
        ImageDraw.Draw(f).rectangle([i * 10, 40, i * 10 + 30, 80], fill="white")
    frames[0].save(FX / "anim.gif", save_all=True, append_images=frames[1:], duration=[80 if i % 2 else 120 for i in range(12)], loop=0)
    # A multi-page TIFF.
    pages = [Image.new("RGB", (300, 400), c) for c in ("red", "green", "blue")]
    pages[0].save(FX / "pages.tif", save_all=True, append_images=pages[1:], compression="tiff_lzw")
    # SVG and SVGZ with text.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150" viewBox="0 0 300 150">'
           '<rect width="300" height="150" fill="#eef"/><circle cx="75" cy="75" r="60" fill="orange"/>'
           '<rect x="200" y="20" width="80" height="110" fill="#1565c0"/><text x="10" y="140" font-family="sans-serif" font-size="14">Hello SVG</text></svg>')
    (FX / "drawing.svg").write_text(svg, encoding="utf-8")
    import gzip

    (FX / "drawing.svgz").write_bytes(gzip.compress(svg.encode()))
    # 16-bit greyscale gradient and a CMYK JPEG.
    grad = (np.tile(np.linspace(0, 65535, 256), (64, 1))).astype(np.uint16)
    Image.fromarray(grad).save(FX / "gray16.png")
    Image.new("CMYK", (200, 100), (0, 255, 255, 0)).save(FX / "cmyk.jpg")
    # A PSD (composite image only), a DNG and icons.
    write_psd(FX / "comp.psd", photo_array(120, 80, 5))
    write_dng(FX / "camera.dng")
    logo.save(FX / "icon.ico", sizes=[(16, 16), (32, 32), (256, 256)])
    logo.resize((1024, 1024)).save(FX / "icon.icns")
    # Fonts.
    (FX / "fonts").mkdir(exist_ok=True)
    build_font(FX / "fonts" / "test.ttf")
    build_font(FX / "fonts" / "test.otf", cff=True)
    build_font(FX / "fonts" / "testvar.ttf", variable=True, family="Desk Var")
    # Duplicates: original, same bytes, resized, recompressed, and a different picture.
    dd = ROOT / "dupes"
    (dd / "sub").mkdir(parents=True, exist_ok=True)
    a = Image.fromarray(photo_array(800, 600, 7))
    ImageDraw.Draw(a).ellipse([200, 150, 500, 450], fill=(250, 240, 30))
    a.save(dd / "a.jpg", quality=92)
    shutil.copyfile(dd / "a.jpg", dd / "a-copy.jpg")
    a.resize((400, 300)).save(dd / "sub" / "a-small.jpg", quality=85)
    a.save(dd / "a-q40.jpg", quality=40)
    other = Image.fromarray(photo_array(800, 600, 11)[:, ::-1].copy())
    ImageDraw.Draw(other).rectangle([50, 50, 300, 500], fill=(20, 20, 20))
    other.save(dd / "b.jpg", quality=90)
    # A batch of 50 photos for the performance check (10 in a subfolder).
    bd = ROOT / "batch"
    (bd / "sub").mkdir(parents=True, exist_ok=True)
    base = photo_array(640, 480, 9)
    for i in range(50):
        Image.fromarray(np.roll(base, i * 7, axis=1)).save((bd / "sub" if i >= 40 else bd) / f"img{i:02d}.jpg", quality=85)


def write_psd(path: Path, rgb: Any) -> None:
    """A minimal Photoshop file: header, empty sections, raw planar composite image."""
    h, w, _ = rgb.shape
    data = b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, h, w, 8, 3)
    data += struct.pack(">I", 0) + struct.pack(">I", 0) + struct.pack(">I", 0)
    data += struct.pack(">H", 0) + b"".join(rgb[:, :, c].tobytes() for c in range(3))
    path.write_bytes(data)


def write_dng(path: Path, w: int = 320, h: int = 240) -> None:
    """A minimal DNG (RGGB Bayer mosaic of a colour gradient, 12-bit in 16-bit samples) that LibRaw develops."""
    import numpy as np

    y, x = np.mgrid[0:h, 0:w]
    r = (x / w * 3000 + 400).astype(np.uint16)
    g = (y / h * 3000 + 400).astype(np.uint16)
    b = ((1 - x / w) * 3000 + 400).astype(np.uint16)
    cfa = np.empty((h, w), np.uint16)
    cfa[0::2, 0::2], cfa[0::2, 1::2], cfa[1::2, 0::2], cfa[1::2, 1::2] = r[0::2, 0::2], g[0::2, 1::2], g[1::2, 0::2], b[1::2, 1::2]
    pix = cfa.astype("<u2").tobytes()
    entries: list[tuple[int, int, int, bytes]] = []

    def add(tag: int, typ: int, vals: Any) -> None:
        if typ == 2:
            raw = vals.encode() + b"\0"
            entries.append((tag, typ, len(raw), raw))
            return
        fmt = {1: "B", 3: "H", 4: "I", 5: "II", 10: "ii"}[typ]
        raw = b"".join(struct.pack("<" + fmt, *v) for v in vals) if typ in (5, 10) else struct.pack("<" + fmt * len(vals), *vals)
        entries.append((tag, typ, len(vals), raw))

    add(254, 4, [0]); add(256, 4, [w]); add(257, 4, [h]); add(258, 3, [16]); add(259, 3, [1]); add(262, 3, [32803])  # noqa: E702
    add(271, 2, "Desk"); add(272, 2, "Selftest DNG"); add(273, 4, [0]); add(274, 3, [1]); add(277, 3, [1])  # noqa: E702
    add(278, 4, [h]); add(279, 4, [len(pix)]); add(284, 3, [1]); add(33421, 3, [2, 2]); add(33422, 1, [0, 1, 1, 2])  # noqa: E702
    add(50706, 1, [1, 4, 0, 0]); add(50708, 2, "Desk Selftest DNG"); add(50714, 4, [0]); add(50717, 4, [4095])  # noqa: E702
    add(50721, 10, [(10000, 10000), (0, 10000), (0, 10000), (0, 10000), (10000, 10000), (0, 10000), (0, 10000), (0, 10000), (10000, 10000)])
    add(50778, 3, [21]); add(50728, 5, [(1, 1), (1, 1), (1, 1)])  # noqa: E702
    entries.sort()
    extra_off = 8 + 2 + len(entries) * 12 + 4
    extra = b""
    fields = []
    for tag, typ, count, raw in entries:
        if len(raw) <= 4:
            fields.append((tag, typ, count, raw.ljust(4, b"\0")))
        else:
            fields.append((tag, typ, count, struct.pack("<I", extra_off + len(extra))))
            extra += raw + (b"\0" if len(raw) % 2 else b"")
    data_off = extra_off + len(extra)
    out = b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(entries))
    for tag, typ, count, v in fields:
        out += struct.pack("<HHI", tag, typ, count) + (struct.pack("<I", data_off) if tag == 273 else v)
    path.write_bytes(out + struct.pack("<I", 0) + extra + pix)


def build_font(path: Path, cff: bool = False, variable: bool = False, family: str = "Desk Test") -> None:
    """A small font (printable ASCII, é, €) with box glyphs; CFF or TrueType; optionally variable (wght 100-900)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.tables.TupleVariation import TupleVariation

    def shape(pen: Any, i: int) -> None:
        hgt = 500 + (i * 37) % 250
        pen.moveTo((60, 0)); pen.lineTo((60, hgt)); pen.lineTo((440, hgt)); pen.lineTo((440, 0)); pen.closePath()  # noqa: E702
        t = 120 + (i * 13) % 60
        pen.moveTo((t, 80)); pen.lineTo((500 - t, 80)); pen.lineTo((500 - t, hgt - 80)); pen.lineTo((t, hgt - 80)); pen.closePath()  # noqa: E702

    chars = [chr(c) for c in range(0x21, 0x7F)] + ["é", "€"]
    names = [".notdef", "space"] + [f"uni{ord(c):04X}" for c in chars]
    fb = FontBuilder(1000, isTTF=not cff)
    fb.setupGlyphOrder(names)
    cmap = {ord(c): f"uni{ord(c):04X}" for c in chars}
    cmap[0x20] = "space"
    fb.setupCharacterMap(cmap)
    if cff:
        cs = {}
        for i, n in enumerate(names):
            pen = T2CharStringPen(600 if n != "space" else 250, None)
            if n != "space":
                shape(pen, i)
            cs[n] = pen.getCharString()
        fb.setupCFF(family.replace(" ", "") + "-Regular", {"FullName": family + " Regular"}, cs, {})
    else:
        glyphs = {}
        for i, n in enumerate(names):
            pen = TTGlyphPen(None)
            if n != "space":
                shape(pen, i)
            glyphs[n] = pen.glyph()
        fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (600 if n != "space" else 250, 60 if n != "space" else 0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular", "designer": "Desk selftest", "licenseDescription": "MIT test font"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200, fsType=0, usWeightClass=400)
    fb.setupPost()
    if variable:
        fb.setupFvar(axes=[("wght", 100, 400, 900, "Weight")], instances=[{"location": {"wght": 100}, "stylename": "Thin"}, {"location": {"wght": 900}, "stylename": "Black"}])
        deltas = [(-20, 0), (-20, 0), (20, 0), (20, 0), (30, 30), (-30, 30), (-30, -30), (30, -30), (0, 0), (0, 0), (0, 0), (0, 0)]
        var = {n: ([] if n == "space" else [TupleVariation({"wght": (0, 1.0, 1.0)}, deltas), TupleVariation({"wght": (-1.0, -1.0, 0)}, [(-dx // 2, -dy // 2) for dx, dy in deltas])]) for n in names}
        fb.setupGvar(var)
    fb.save(str(path))


def snapshot(dirs: list[Path]) -> dict[str, str]:
    out = {}
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ── tests ───────────────────────────────────────────────────────────────


def t_help() -> None:
    scripts = ["img_info.py", "img_view.py", "img_convert.py", "img_edit.py", "img_compose.py", "img_compare.py", "img_optimize.py", "font_tool.py"]
    slowest = 0.0
    for s in scripts:
        t = time.time()
        code, out, _ = run(s, "--help")
        dt = time.time() - t
        slowest = max(slowest, dt)
        ok("python3 scripts/" in out and "usage" in out.lower(), f"{s} --help shows usage and examples", out[:200])
    ok(slowest < 1.0, "--help answers quickly", f"slowest {slowest:.2f}s")
    with LOCK:
        RESULTS["help_s"] = round(slowest, 3)
    # Timing is noisy on a busy machine; the cause of slow help is not: no heavy library may load for --help.
    probe = (
        "import runpy, sys\n"
        "sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]\n"
        "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit:\n    pass\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & {'PIL', 'numpy', 'fontTools', 'rawpy', 'resvg_py', 'pillow_heif', 'pyoxipng'})\n"
        "print('HEAVY', ','.join(heavy), file=sys.stderr)\n"
    )
    for sc in scripts:
        r = subprocess.run([PY, "-c", probe, str(HERE), str(HERE / sc), "--help"], capture_output=True, text=True, timeout=60)
        heavy = [line for line in r.stderr.splitlines() if line.startswith("HEAVY")]
        ok(heavy == ["HEAVY "], f"{sc} --help imports no heavy library", heavy or r.stderr[-300:])


def t_info() -> None:
    w = workdir("info")
    r = jrun("img_info.py", FX / "photo.jpg", cwd=w)
    ok(r.get("width") == 1200 and r.get("height") == 1600 and r.get("stored_size") == [1600, 1200], "info: EXIF orientation swaps the shown size", r.get("width"))
    ex = r.get("exif", {})
    ok(ex.get("make") == "Canon" and ex.get("model") == "EOS R5" and "1/250" in str(ex.get("exposure_time")) and ex.get("f_number") == "f/2.8" and ex.get("iso") == 400, "info: camera EXIF decoded", ex)
    ok(str(ex.get("taken", "")).startswith("2026:07:14 18:30:05"), "info: date taken", ex.get("taken"))
    gps = r.get("gps") or {}
    ok(abs(gps.get("lat", 0) - 48.8582) < 0.001 and abs(gps.get("lon", 0) - 2.2945) < 0.001 and gps.get("alt_m") == 35.0, "info: GPS decoded to lat/lon", gps)
    ok((r.get("xmp") or {}).get("dc:title") == "Eiffel sunset" and (r.get("xmp") or {}).get("dc:subject") == ["paris", "tower"], "info: XMP title and keywords", r.get("xmp"))
    ok(r.get("estimated_quality") in range(86, 95), "info: JPEG quality estimate", r.get("estimated_quality"))
    st = r.get("stats") or {}
    ok(len(st.get("palette", [])) >= 3 and all(p["hex"].startswith("#") for p in st["palette"]) and 0 < st.get("brightness_pct", 0) < 100, "info: palette and brightness", st)
    code, out, _ = run("img_info.py", FX / "photo.jpg", cwd=w)
    ok("GPS: 48.85" in out and "dominant colours" in out and "Canon" in out, "info: Markdown report", out[:400])
    r = jrun("img_info.py", FX / "anim.gif", cwd=w)
    ok((r.get("animation") or {}).get("frames") == 12 and r["animation"]["total_ms"] == 1200 and r["animation"]["loop"] == 0, "info: GIF animation frames and timing", r.get("animation"))
    r = jrun("img_info.py", FX / "pages.tif", cwd=w)
    ok(r.get("pages") == 3, "info: TIFF pages", r.get("pages"))
    r = jrun("img_info.py", FX / "drawing.svg", cwd=w)
    ok(r.get("format") == "SVG" and r.get("width") == 300 and "Hello SVG" in r.get("text", "") and "sans-serif" in r.get("fonts", []), "info: SVG size, text and fonts", r)
    r = jrun("img_info.py", FX / "camera.dng", cwd=w)
    ok(r.get("format") == "RAW" and r.get("width") == 320 and (r.get("exif") or {}).get("make") == "Desk", "info: DNG camera RAW", r)
    r = jrun("img_info.py", FX / "photo.heic", cwd=w)
    ok(r.get("format") == "HEIF" and (r.get("exif") or {}).get("model") == "EOS R5", "info: HEIC with EXIF", r.get("format"))
    r = jrun("img_info.py", FX / "comp.psd", cwd=w)
    ok(r.get("format") == "PSD" and r.get("width") == 120, "info: PSD", r)
    r = jrun("img_info.py", FX / "icon.ico", cwd=w)
    ok(set(r.get("icon_sizes", [])) >= {"16x16", "32x32", "256x256"}, "info: ICO sizes", r.get("icon_sizes"))
    r = jrun("img_info.py", FX / "gray16.png", cwd=w)
    ok(r.get("bit_depth") == 16, "info: 16-bit PNG", r.get("bit_depth"))
    r = jrun("img_info.py", FX / "fonts" / "test.ttf", cwd=w)
    ok(r.get("family") == "Desk Test", "info: font files are named", r)
    rs = jrun("img_info.py", FX, "-r", cwd=w)
    ok(isinstance(rs, list) and len(rs) >= 18 and not [x for x in rs if "error" in x], "info: folder batch without errors", [x for x in rs if "error" in x][:3] if isinstance(rs, list) else rs)
    code, out, _ = run("img_info.py", FX, cwd=w)
    ok(out.startswith("| file |") and "photo.jpg" in out, "info: batch Markdown table", out[:200])
    code, _, err = run("img_info.py", w / "missing.png", cwd=w, expect=1)
    ok(err.strip().startswith("error:") and len(err.strip().splitlines()) == 1, "info: one-line error for a missing file", err)


def t_view() -> None:
    w = workdir("view")
    code, out, _ = run("img_view.py", FX / "photo.jpg", FX / "logo.png", FX / "drawing.svg", FX / "photo.heic", FX / "camera.dng", FX / "comp.psd", FX / "gray16.png", FX / "cmyk.jpg", FX / "photo.avif", FX / "icon.icns", cwd=w)
    ok(out.rstrip().endswith(VIEW_HINT), "view: ends with the view_image hint", out[-200:])
    for name, size in (("photo.png", (1176, 1568)), ("logo.png", (400, 400)), ("drawing.png", (1568, 784)), ("camera.png", (320, 240)), ("comp.png", (120, 80)), ("gray16.png", (256, 64)), ("cmyk.png", (200, 100)), ("icon.png", (1024, 1024))):
        p = w / "renders" / name
        ok(p.exists() and img(p).size == size, f"view: {name} rendered at {size}", img(p).size if p.exists() else "missing")
    heic = img(w / "renders" / "photo-2.png") if (w / "renders" / "photo-2.png").exists() else None
    ok(heic is not None and heic.size == (1176, 1568), "view: HEIC rendered upright", heic.size if heic else "missing")
    pv = img(w / "renders" / "photo.png").convert("RGB")
    # The white patch was at the stored top-left; after a 90° clockwise turn it is at the top-right.
    ok(pv.getpixel((pv.size[0] - 170 * pv.size[0] // 1200, 200 * pv.size[1] // 1600))[0] > 235, "view: orientation applied (patch moved top-right)", pv.getpixel((pv.size[0] - 170 * pv.size[0] // 1200, 200 * pv.size[1] // 1600)))
    lg = img(w / "renders" / "logo.png").convert("RGB")
    ok(lg.getpixel((2, 2)) in ((255, 255, 255), (214, 214, 214)) and lg.getpixel((200, 200))[0] > 200, "view: transparency as checkerboard", lg.getpixel((2, 2)))
    g16 = img(w / "renders" / "gray16.png").convert("L")
    ok(g16.getpixel((0, 10)) < 10 and g16.getpixel((255, 10)) > 245, "view: 16-bit data scaled to 8-bit", (g16.getpixel((0, 10)), g16.getpixel((255, 10))))
    cm = img(w / "renders" / "cmyk.png").convert("RGB").getpixel((5, 5))
    ok(cm[0] > 200 and cm[1] < 80 and cm[2] < 80, "view: CMYK converted to RGB (red)", cm)
    r = jrun("img_view.py", FX / "shot.png", "--zoom", "100,150,300,60", "--grid", cwd=w)
    im0 = (r.get("images") or [{}])[0]
    ok(im0.get("region") == [100, 150, 300, 60] and im0.get("output", "").endswith("shot-zoom-grid.png"), "view: zoom region in pixels", im0)
    z = img(w / im0["output"]) if im0.get("output") else None
    ok(z is not None and z.size[0] == 30 + 300 * 5 and z.convert("RGB").getpixel((5, 5)) == (238, 238, 238), "view: zoom enlarged ×5 (nearest) with a grid ruler band", z.size if z else None)
    ok(z is not None and z.convert("RGB").getpixel((30 + 150, 30 + 150))[:3][2] > 200, "view: zoomed pixels are the blue button", z.convert("RGB").getpixel((180, 180)) if z else None)
    r = jrun("img_view.py", FX / "shot.png", "--zoom", "50%,50%,50%,50%", cwd=w)
    ok((r.get("images") or [{}])[0].get("region") == [600, 400, 600, 400], "view: zoom region in percent", r)
    code, out, _ = run("img_view.py", "grid", FX / "photo.jpg", "--grid-units", "pct", cwd=w)
    ok("photo-grid.png" in out and img(w / "renders" / "photo-grid.png").size[1] == 1568, "view: mode word 'grid' and percent units", out[:200])
    r = jrun("img_view.py", FX / "drawing.svg", "--zoom", "200,20,80,110", cwd=w)
    im0 = (r.get("images") or [{}])[0]
    zs = img(w / im0["output"]).convert("RGB") if im0.get("output") else None
    ok(zs is not None and max(zs.size) >= 1400 and zs.getpixel((zs.size[0] // 2, zs.size[1] // 2))[2] > 150, "view: SVG zoom re-rendered sharp", zs.size if zs else r)
    code, out, _ = run("img_view.py", FX / "anim.gif", "--frames", cwd=w)
    fr = sorted((w / "renders" / "anim-frames").glob("frame-*.png"))
    ok(len(fr) == 12 and (w / "renders" / "anim-frames-sheet.png").exists() and "1.20s" in out, "view: GIF frames and sheet with timings", out[:300])
    code, out, _ = run("img_view.py", FX / "pages.tif", "--frames", "--pages", "2-3", cwd=w)
    fr = sorted((w / "renders" / "pages-frames").glob("frame-*.png"))
    ok(len(fr) == 2 and img(fr[0]).convert("RGB").getpixel((5, 5))[1] > 100, "view: TIFF pages 2-3", [f.name for f in fr])
    r = jrun("img_view.py", FX, "--sheet", "-r", cwd=w)
    ok(r.get("sheets") and len(r.get("images", [])) == 17 and all(Path(w / s).exists() for s in r["sheets"]), "view: contact sheet of a folder", r.get("sheets"))
    sh = img(w / r["sheets"][0]) if r.get("sheets") else None
    ok(sh is not None and max(sh.size) <= 1568, "view: sheet fits the vision size", sh.size if sh else None)
    code, out, err = run("img_view.py", FX / "photo.jpg", "--zoom", "5000,5000,10,10", cwd=w, expect=1)
    ok("outside" in (out + err), "view: a region outside the image is an error", out + err)


def t_cache() -> None:
    import numpy as np
    from PIL import Image

    w = workdir("cache")
    big = w / "noise.png"
    # 12 MP: big enough that decoding dominates the cold run even on a loaded machine (the cached run is process
    # start-up plus a copy, whatever the size).
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (3000, 4000, 3), dtype=np.uint8)).save(big, compress_level=1)
    t = time.time()
    run("img_view.py", big, "--out-dir", "r1", cwd=w)
    cold = time.time() - t
    warm, hits = 1e9, []
    for _ in range(3):  # the best of three: a busy machine only ever adds time
        t = time.time()
        r = jrun("img_view.py", big, "--out-dir", "r2", cwd=w)
        warm = min(warm, time.time() - t)
        hits.append(((r.get("images") or [{}])[0]).get("cached"))
    ok((w / "r2" / "noise.png").exists() and img(w / "r2" / "noise.png").size == (1568, 1176), "cache: the cached render is copied out", r)
    ok(hits == [True, True, True], "cache: the second preview is a cache hit", hits)
    # The cached run is mostly process start-up, so the ratio is modest when the cold decode is fast (a warm runtime).
    ok(cold / max(warm, 1e-3) >= 3, "cache: second preview at least 3× faster", f"cold {cold:.2f}s warm {warm:.2f}s")
    with LOCK:
        RESULTS["cache"] = f"preview cold {cold:.2f}s, cached {warm:.2f}s"
    t = time.time()
    run("img_view.py", big, "--out-dir", "r3", "--no-cache", cwd=w)
    ok(time.time() - t > warm * 2, "cache: --no-cache renders again", time.time() - t)
    big.unlink()
    # Contact sheets are cached whole (keyed by every input's content), and info/hash batches answer cache hits
    # without starting worker processes.
    t = time.time()
    run("img_view.py", ROOT / "batch", "-r", "--sheet", "--out-dir", "s1", cwd=w)
    cold = time.time() - t
    warm = 1e9
    for _ in range(2):
        t = time.time()
        code, out, _ = run("img_view.py", ROOT / "batch", "-r", "--sheet", "--out-dir", "s2", cwd=w)
        warm = min(warm, time.time() - t)
    s1, s2 = sorted((w / "s1").glob("*.png")), sorted((w / "s2").glob("*.png"))
    ok(len(s2) == len(s1) >= 1 and s1[0].read_bytes() == s2[0].read_bytes() and out.rstrip().endswith(VIEW_HINT), "cache: the cached sheet is the same", out[-200:])
    ok(jrun("img_view.py", ROOT / "batch", "-r", "--sheet", "--out-dir", "s3", cwd=w).get("cached") is True, "cache: the second contact sheet is a cache hit")
    ok(cold / max(warm, 1e-3) >= 3, "cache: second contact sheet at least 3× faster", f"cold {cold:.2f}s warm {warm:.2f}s")
    with LOCK:
        RESULTS["cache"] += f", sheet of 50 cold {cold:.2f}s, cached {warm:.2f}s"
    r1 = jrun("img_info.py", ROOT / "batch", "-r", cwd=w)
    r2 = jrun("img_info.py", ROOT / "batch", "-r", cwd=w)
    ok(r1 == r2 and len(r1) == 50, "cache: cached info records are identical", len(r2))


def t_convert() -> None:
    w = workdir("convert")
    r = jrun("img_convert.py", FX / "photo.heic", "--to", "jpg", "--out", "photo.jpg", cwd=w)
    o = img(w / "photo.jpg")
    ok(o.size == (1200, 1600) and o.getexif().get(0x0112, 1) == 1 and o.getexif().get(0x0110) == "EOS R5", "convert: HEIC → JPEG upright, EXIF kept, orientation reset", (o.size, dict(o.getexif())))
    jrun("img_convert.py", FX / "logo.png", "--to", "jpg", "--out", "logo.jpg", "--bg", "#00ff00", cwd=w)
    px = img(w / "logo.jpg").convert("RGB").getpixel((3, 3))
    ok(px[1] > 230 and px[0] < 30, "convert: alpha flattened on --bg for JPEG", px)
    jrun("img_convert.py", FX / "drawing.svg", "--to", "png", "--width", "900", "--out", "drawing.png", cwd=w)
    ok(img(w / "drawing.png").size == (900, 450) and img(w / "drawing.png").mode == "RGBA", "convert: SVG → PNG at a width, transparent-capable", img(w / "drawing.png").size)
    jrun("img_convert.py", FX / "drawing.svg", "--to", "png", "--scale", "2", "--out", "drawing2x.png", cwd=w)
    ok(img(w / "drawing2x.png").size == (600, 300), "convert: SVG --scale 2", img(w / "drawing2x.png").size)
    jrun("img_convert.py", FX / "logo.png", "--to", "ico", "--sizes", "16,32,48", "--out", "fav.ico", cwd=w)
    ok(img(w / "fav.ico").info.get("sizes") == {(16, 16), (32, 32), (48, 48)}, "convert: multi-size ICO", img(w / "fav.ico").info.get("sizes"))
    jrun("img_convert.py", FX / "drawing.svg", "--to", "icns", "--out", "app.icns", cwd=w)
    ok(img(w / "app.icns").size == (1024, 1024), "convert: SVG → ICNS", img(w / "app.icns").size)
    rs = jrun("img_convert.py", FX / "anim.gif", "--to", "webp", "--out", "anim.webp", cwd=w)
    a = img(w / "anim.webp")
    ok(getattr(a, "n_frames", 1) == 12, "convert: GIF → animated WebP keeps frames", getattr(a, "n_frames", 1))
    jrun("img_convert.py", FX / "anim.gif", "--to", "apng", "--out", "anim.png", cwd=w)
    ok(getattr(img(w / "anim.png"), "n_frames", 1) == 12, "convert: GIF → APNG", getattr(img(w / "anim.png"), "n_frames", 1))
    r = jrun("img_convert.py", FX / "anim.gif", "--to", "png", "--out", "first.png", cwd=w)
    ok("first" in str(r.get("note", "")) and getattr(img(w / "first.png"), "n_frames", 1) == 1, "convert: GIF → PNG writes the first frame and says so", r.get("note"))
    r = jrun("img_convert.py", FX / "pages.tif", "--to", "png", "--split", "--out-dir", "split", cwd=w)
    ok(sorted(p.name for p in (w / "split").glob("*.png")) == ["pages-001.png", "pages-002.png", "pages-003.png"], "convert: --split writes each page", list((w / "split").glob("*")))
    jrun("img_convert.py", FX / "pages.tif", "--to", "pdf", "--out", "pages.pdf", cwd=w)
    check_pdf(w / "pages.pdf", 3, "convert: multi-page TIFF → 3-page PDF")
    code, out, _ = run("img_convert.py", FX / "photo.jpg", FX / "logo.png", FX / "shot.png", "--out", "combined.pdf", "--pdf-page", "a4", cwd=w)
    check_pdf(w / "combined.pdf", 3, "convert: three images → one A4 PDF", a4=True)
    jrun("img_convert.py", FX / "cmyk.jpg", "--to", "pdf", "--out", "cmyk.pdf", cwd=w)
    check_pdf(w / "cmyk.pdf", 1, "convert: CMYK JPEG → PDF")
    rs = jrun("img_convert.py", ROOT / "dupes" / "a.jpg", "--to", "pdf", "--out", "a.pdf", cwd=w)
    ok((ROOT / "dupes" / "a.jpg").read_bytes() in (w / "a.pdf").read_bytes(), "convert: an unchanged JPEG is embedded in the PDF as is", rs)
    r = jrun("img_convert.py", FX / "camera.dng", "--to", "tif", "--out", "camera.tif", cwd=w)
    t = img(w / "camera.tif")
    ok(t.size == (320, 240) and t.mode == "RGB", "convert: DNG → TIFF", (t.size, t.mode))
    r = jrun("img_convert.py", FX / "photo.jpg", "--to", "webp", "--strip", "--max-edge", "800", "--out", "stripped.webp", cwd=w)
    s = img(w / "stripped.webp")
    ok(max(s.size) == 800 and not len(s.getexif()), "convert: --strip drops EXIF (and GPS); --max-edge", (s.size, dict(s.getexif())))
    for fmt in ("avif", "heic", "bmp", "gif", "tif", "jp2", "qoi", "tga"):
        rr = jrun("img_convert.py", FX / "logo.png", "--to", fmt, "--out", f"logo.{fmt}", cwd=w)
        ok((w / f"logo.{fmt}").exists() and img(w / f"logo.{fmt}").size == (400, 400), f"convert: PNG → {fmt}", rr)
    code, out, err = run("img_convert.py", FX / "logo.png", "--to", "png", "--out", FX / "logo.png", cwd=w, expect=1)
    ok("refusing to overwrite the input" in (out + err), "convert: never overwrites an input", out + err)
    code, out, err = run("img_convert.py", FX / "logo.png", "--to", "avif", "--out", "logo.avif", cwd=w, expect=1)
    ok("already exists" in (out + err), "convert: refuses an existing output without --force", out + err)
    run("img_convert.py", FX / "logo.png", "--to", "avif", "--out", "logo.avif", "--force", cwd=w)
    code, _, err = run("img_convert.py", FX / "logo.png", "--to", "xyz", cwd=w, expect=2)
    ok("cannot write" in err, "convert: unknown format is a usage error", err)
    # Performance: 50 images → WebP in parallel, mirroring the subfolder.
    t = time.time()
    rs = jrun("img_convert.py", ROOT / "batch", "-r", "--to", "webp", "--out-dir", "web", cwd=w)
    dt = time.time() - t
    ok(isinstance(rs, list) and len(rs) == 50 and len(list((w / "web" / "sub").glob("*.webp"))) == 10, "convert: batch of 50 mirrors subfolders", len(rs) if isinstance(rs, list) else rs)
    ok(dt < 10, "convert: 50 images in under 10 s", f"{dt:.1f}s")
    with LOCK:
        RESULTS["convert50_s"] = round(dt, 2)


def check_pdf(path: Path, pages: int, name: str, a4: bool = False) -> None:
    """Structural PDF check: xref offsets point at objects, page count, image streams decode."""
    import re
    import zlib

    if not ok(path.exists(), name + " (exists)", path):
        return
    data = path.read_bytes()
    n = len(re.findall(rb"/Type /Page\b", data))
    ok(n == pages, name + f" ({pages} pages)", n)
    xref = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    rows = data[xref:].split(b"\n")[3:]
    good = all(data[int(r[:10]) :].startswith(b"%d 0 obj" % (i + 1)) for i, r in enumerate(rows) if r.endswith(b" n ") or r.endswith(b" n"))
    ok(data[xref:].startswith(b"xref") and good, name + " (valid xref)", data[xref : xref + 40])
    if a4:
        ok(b"/MediaBox [0 0 595.2800 841.8900]" in data or b"/MediaBox [0 0 841.8900 595.2800]" in data, name + " (A4 pages)", re.findall(rb"/MediaBox \[[^\]]+\]", data)[:3])
    for m in re.finditer(rb"/Filter /FlateDecode /DecodeParms[^>]*>> /Length (\d+) >>\nstream\n", data):
        raw = data[m.end() : m.end() + int(m.group(1))]
        try:
            zlib.decompress(raw)
            ok(True, name + " (Flate stream decodes)")
        except zlib.error as e:
            ok(False, name + " (Flate stream decodes)", e)
        break


def t_edit() -> None:
    import numpy as np

    w = workdir("edit")
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "geo.png", "--op", "resize width=800", "--op", "crop aspect=1:1", "--op", "rotate angle=90", "--op", "pad all=10 bg=#ff0000", "--op", "border width=5 color=black", cwd=w)
    ok(r.get("size") == [830, 830] and r.get("ops") == ["resize", "crop", "rotate", "pad", "border"], "edit: resize → crop → rotate → pad → border sizes", r)
    g = img(w / "geo.png").convert("RGB")
    ok(g.getpixel((2, 2)) == (0, 0, 0) and g.getpixel((10, 10)) == (255, 0, 0), "edit: border and pad colours", (g.getpixel((2, 2)), g.getpixel((10, 10))))
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "cover.jpg", "--op", "resize width=300 height=200 mode=cover", cwd=w)
    ok(r.get("size") == [300, 200], "edit: resize cover gives the exact box", r.get("size"))
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "fill.png", "--op", "resize width=300 height=300 mode=fill bg=transparent", cwd=w)
    f = img(w / "fill.png")
    ok(f.size == (300, 300) and f.mode == "RGBA" and f.getpixel((2, 150))[3] == 0, "edit: resize fill letterboxes with transparency", (f.size, f.mode))
    r = jrun("img_edit.py", FX / "bordered.png", "--out", "trim.png", "--op", "trim", cwd=w)
    ok(r.get("size") == [200, 200], "edit: trim removes uniform borders", r.get("size"))
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "rot.png", "--op", "rotate angle=30", cwd=w)
    ok(r.get("size", [0])[0] > 1200 and img(w / "rot.png").mode == "RGBA", "edit: free rotation expands with a transparent background", r)
    tones = [("grayscale", "L"), ("sepia", "RGB"), ("invert", "RGB"), ("posterize bits=2", "RGB"), ("solarize", "RGB"), ("threshold", "L"), ("duotone black=#001040 white=#ffe0a0", "RGB"),
             ("levels black=20 white=230 gamma=1.2", "RGB"), ("auto_contrast", "RGB"), ("equalize", "RGB"), ("adjust brightness=1.2 contrast=0.9 saturation=1.3 gamma=0.8 hue=40 temperature=30 sharpness=1.5", "RGB"),
             ("blur radius=3", "RGB"), ("sharpen", "RGB"), ("unsharp", "RGB"), ("median size=3", "RGB"), ("filter name=emboss", "RGB"), ("pixelate size=16 box=0,0,50%,50%", "RGB"),
             ("quantize colors=8", "P"), ("mode mode=L", "L"), ("replace_color from=#ffffff to=#00ff00 tolerance=40", "RGB"), ("set_dpi dpi=300", "RGB")]
    for i, (spec, mode) in enumerate(tones):
        r = jrun("img_edit.py", FX / "shot.png", "--out", f"tone{i}.png", "--op", spec, cwd=w)
        ok(r.get("mode") == mode and (w / f"tone{i}.png").exists(), f"edit: {spec.split()[0]} → mode {mode}", r)
    inv = np.asarray(img(w / "tone2.png").convert("RGB"), dtype=np.int16)
    src = np.asarray(img(FX / "shot.png").convert("RGB"), dtype=np.int16)
    ok(np.abs(inv + src - 255).max() == 0, "edit: invert is exact", np.abs(inv + src - 255).max())
    th = np.unique(np.asarray(img(w / "tone5.png")))
    ok(set(th.tolist()) <= {0, 255}, "edit: threshold gives pure black and white", th[:5])
    ok(len(img(w / "tone17.png").getcolors(256) or []) <= 8, "edit: quantize to 8 colours", len(img(w / "tone17.png").getcolors(256) or []))
    ok(round(img(w / "tone20.png").info.get("dpi", (0, 0))[0]) == 300, "edit: set_dpi recorded", img(w / "tone20.png").info.get("dpi"))
    pix = img(w / "tone16.png").convert("RGB")
    ok(pix.getpixel((1, 1)) == pix.getpixel((14, 14)) and pix.getpixel((700, 500)) == img(FX / "shot.png").convert("RGB").getpixel((700, 500)), "edit: pixelate only inside the box", "")
    ops = [
        {"op": "rect", "box": "100,150,300,60", "label": "Save button", "color": "#ff0000"},
        {"op": "arrow", "from": "80%,80%", "to": "40%,30%", "color": "#0000ff", "width": 6},
        {"op": "highlight", "box": "100,260,900,60"},
        {"op": "ellipse", "box": "900,400,200,120", "color": "#00aa00", "width": 4},
        {"op": "callout", "at": "95%,5%", "number": 3},
        {"op": "text", "text": "Figure 1 — settings", "position": "bottom", "size": "5%", "bg": "#000000cc", "color": "white"},
        {"op": "watermark", "text": "DRAFT", "tile": True, "opacity": 0.2},
        {"op": "redact", "box": "110,270,200,30"},
    ]
    r = jrun("img_edit.py", FX / "shot.png", "--out", "annot.png", "--ops", json.dumps(ops), "--preview", cwd=w)
    a = img(w / "annot.png").convert("RGB")
    ok(a.getpixel((101, 180))[0] > 200 and a.getpixel((101, 180))[1] < 60, "edit: rect outline drawn", a.getpixel((101, 180)))
    ok(a.getpixel((150, 285)) == (0, 0, 0), "edit: redact fills black", a.getpixel((150, 285)))
    hl = a.getpixel((600, 300))
    ok(hl[2] < 200 and hl[0] > 200, "edit: highlight tints yellow", hl)
    ok(r.get("metadata") == "stripped" and r.get("preview") and (w / r["preview"]).exists(), "edit: redact strips metadata; --preview writes a PNG", r)
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "redacted.jpg", "--op", "redact box=0,0,100,100", cwd=w)
    ok(not len(img(w / "redacted.jpg").getexif()), "edit: redacted output has no EXIF/GPS", dict(img(w / "redacted.jpg").getexif()))
    r = jrun("img_edit.py", FX / "photo.jpg", "--out", "keep.jpg", "--op", "resize width=600", cwd=w)
    k = img(w / "keep.jpg")
    ok(k.getexif().get(0x010F) == "Canon" and k.getexif().get(0x0112, 1) == 1 and k.size == (600, 800), "edit: metadata kept, orientation reset", (k.size, dict(k.getexif())))
    r = jrun("img_edit.py", FX / "green.png", "--out", "cut.png", "--op", "chroma_key color=#00c800 tolerance=15", cwd=w)
    c = img(w / "cut.png")
    ok(c.mode == "RGBA" and c.getpixel((5, 5))[3] == 0 and c.getpixel((200, 200))[3] == 255 and c.getpixel((300, 200))[3] == 255, "edit: chroma key removes the connected background only", (c.getpixel((5, 5)), c.getpixel((300, 200))))
    r = jrun("img_edit.py", FX / "green.png", "--out", "cut2.png", "--op", "chroma_key color=auto connected=false", "--op", "trim", cwd=w)
    c2 = img(w / "cut2.png")
    ok(c2.getpixel((c2.size[0] // 2, c2.size[1] // 2))[3] == 0 and abs(c2.size[0] - 300) <= 4, "edit: chroma key everywhere, then trim to the subject", (c2.size, c2.getpixel((c2.size[0] // 2, c2.size[1] // 2))))
    r = jrun("img_edit.py", FX / "shot.png", "--out", "comp.png", "--op", f"composite image={FX / 'logo.png'} width=200 position=top-right margin=0", cwd=w)
    cp = img(w / "comp.png").convert("RGB").getpixel((1100, 100))
    ok(cp[0] > 200 and cp[1] < 60, "edit: composite places the logo top-right", cp)
    r = jrun("img_edit.py", FX / "shot.png", "--out", "mult.png", "--op", f"composite image={FX / 'logo.png'} width=400 x=0 y=0 blend=multiply", cwd=w)
    ok((w / "mult.png").exists(), "edit: composite with a blend mode", r)
    for spec, check in (("round_corners radius=50%", lambda im: im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0), ("shadow", lambda im: im.size[0] > 1200 and im.mode == "RGBA"),
                        ("opacity value=0.5", lambda im: im.getpixel((600, 400))[3] in (127, 128)), ("transparent color=#f0f0f5", lambda im: im.getpixel((600, 500))[3] == 0)):
        r = jrun("img_edit.py", FX / "shot.png", "--out", f"{spec.split()[0]}.png", "--op", spec, cwd=w)
        im = img(w / f"{spec.split()[0]}.png")
        ok(check(im), f"edit: {spec}", (im.size, im.mode))
    r = jrun("img_edit.py", FX / "logo.png", "--out", "flat.jpg", "--op", "flatten color=#0000ff", cwd=w)
    ok(img(w / "flat.jpg").convert("RGB").getpixel((3, 3))[2] > 230, "edit: flatten onto a colour", img(w / "flat.jpg").getpixel((3, 3)))
    r = jrun("img_edit.py", FX / "anim.gif", "--out", "anim-small.gif", "--op", "resize width=80", "--op", "text text=Hi size=30%", cwd=w)
    a = img(w / "anim-small.gif")
    ok(getattr(a, "n_frames", 1) == 12 and a.size == (80, 60), "edit: animations are edited frame by frame", (getattr(a, "n_frames", 1), a.size))
    rs = jrun("img_edit.py", ROOT / "dupes", "-r", "--out-dir", "batch", "--op", "resize width=200", "--to", "png", cwd=w)
    ok(isinstance(rs, list) and len(rs) == 5 and (w / "batch" / "sub" / "a-small.png").exists(), "edit: batch with --out-dir mirrors folders", rs if not isinstance(rs, list) else len(rs))
    r = jrun("img_edit.py", FX / "drawing.svg", "--out", "svg-edit.png", "--op", "border width=10 color=red", cwd=w)
    ok(r.get("size") == [320, 170], "edit: SVG input is rendered, then edited", r.get("size"))
    code, out, err = run("img_edit.py", FX / "shot.png", "--out", "x.png", "--op", "sparkle", cwd=w, expect=2)
    ok("unknown operation" in err, "edit: unknown operation is a usage error", err)
    code, out, err = run("img_edit.py", "--list-ops", FX / "shot.png", cwd=w)
    ok("chroma_key" in out and "watermark" in out, "edit: --list-ops", out[:100])
    code, out, err = run("img_edit.py", FX / "shot.png", "--out", "x.png", "--op", "crop box=5000,5000,10,10", cwd=w, expect=1)
    ok("outside" in (out + err), "edit: a crop outside the image is reported", out + err)


def t_compose() -> None:
    w = workdir("compose")
    r = jrun("img_compose.py", "grid", FX / "photo.jpg", FX / "logo.png", FX / "shot.png", FX / "drawing.svg", "--out", "grid.png", "--cols", "2", "--cell", "300x200", "--title", "Four", cwd=w)
    ok(r.get("size") == [12 + 2 * 312, round(18 * 2.6) + 12 + 2 * (200 + round(18 * 1.7) + 12)] and img(w / "grid.png").size == tuple(r["size"]), "compose: grid size with captions and title", r)
    r = jrun("img_compose.py", "compare", FX / "shot.png", FX / "photo.jpg", "--out", "ba.jpg", "--height", "400", cwd=w)
    ok(img(w / "ba.jpg").size[1] > 400 and r.get("labels") == ["Before", "After"], "compose: before/after", r)
    r = jrun("img_compose.py", "animate", FX / "logo.png", FX / "shot.png", FX / "photo.jpg", "--out", "slides.gif", "--durations", "500,700,900", "--max-edge", "300", cwd=w)
    a = img(w / "slides.gif")
    durs = []
    for i in range(getattr(a, "n_frames", 1)):
        a.seek(i)
        durs.append(a.info.get("duration"))
    ok(r.get("frames") == 3 and durs == [500, 700, 900] and max(a.size) == 300, "compose: GIF from frames with durations", (r, durs))
    r = jrun("img_compose.py", "animate", FX / "anim.gif", "--out", "boom.webp", "--boomerang", "--fps", "20", cwd=w)
    ok(r.get("frames") == 22 and getattr(img(w / "boom.webp"), "n_frames", 1) == 22, "compose: boomerang WebP from an animation", r)
    r = jrun("img_compose.py", "animate", FX / "logo.png", FX / "shot.png", "--out", "fade.png", "--crossfade", "3", "--size", "200x150", "--bg", "transparent", "--preview", cwd=w)
    ok(r.get("frames") == 8 and getattr(img(w / "fade.png"), "n_frames", 1) == 8 and r.get("preview"), "compose: crossfade APNG with a frames preview", r)
    r = jrun("img_compose.py", "sprite", FX / "logo.png", FX / "anim.gif", FX / "drawing.svg", FX / "bordered.png", "--out", "sprite.png", "--css", "sprite.css", cwd=w)
    m = json.loads((w / "sprite.json").read_text())
    sheet = img(w / "sprite.png").convert("RGBA")
    f = m["frames"]["logo"]
    tile = sheet.crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]))
    ok(tile.getpixel((200, 200)) == (220, 30, 30, 255) and tile.getpixel((5, 5))[3] == 0 and ".sprite-logo" in (w / "sprite.css").read_text(), "compose: sprite map boxes match the pixels", f)
    r = jrun("img_compose.py", "icons", FX / "drawing.svg", "--out-dir", "icons", "--preset", "all", cwd=w)
    names = {Path(p).name for p in r.get("files", [])}
    ok({"favicon.ico", "apple-touch-icon.png", "android-chrome-512x512.png", "site.webmanifest", "icon.icns", "icon.ico", "icon-1024.png", "icon_16x16@2x.png"} <= names, "compose: icon set files", sorted(names)[:12])
    fav = img(w / "icons" / "favicon.ico")
    ok(fav.info.get("sizes") == {(16, 16), (32, 32), (48, 48)} and img(w / "icons" / "icon.ico").info.get("sizes") and (256, 256) in img(w / "icons" / "icon.ico").info["sizes"], "compose: ICO sizes", fav.info.get("sizes"))
    ok(img(w / "icons" / "icon.iconset" / "icon_512x512@2x.png").size == (1024, 1024) and img(w / "icons" / "apple-touch-icon.png").getpixel((0, 0))[3] == 255, "compose: iconset sizes; apple-touch-icon opaque", "")
    r = jrun("img_compose.py", "tiles", FX / "shot.png", "--out-dir", "tiles", "--tile", "500x500", "--overlap", "50", cwd=w)
    man = json.loads((w / "tiles" / "shot_tiles.json").read_text())
    covered = max(t["x"] + t["w"] for t in man["tiles"]) == 1200 and max(t["y"] + t["h"] for t in man["tiles"]) == 800
    ok(r.get("tiles") == 6 and covered and man["tiles"][1]["x"] == 450, "compose: tiles with overlap cover the image", r)
    r = jrun("img_compose.py", "tiles", FX / "photo.jpg", "--out-dir", "tiles3", "--grid", "3x2", cwd=w)
    ok(r.get("tiles") == 6 and img(w / "tiles3" / "photo_r01_c01.jpg").size == (600, 533), "compose: 3×2 grid tiles of the upright photo (JPEG tiles for a JPEG photo)", r)
    # The web manifest is named after the source unless --name says otherwise, and the .icns has every size.
    man = json.loads((w / "icons" / "site.webmanifest").read_text())
    ok(man.get("name") == "drawing", "compose: webmanifest named after the source file", man.get("name"))
    r = jrun("img_info.py", w / "icons" / "icon.icns", cwd=w)
    ok({"16x16", "32x32", "16x16@2x", "512x512@2x"} <= set(r.get("icon_sizes", [])), "compose: .icns holds 16x16 and 32x32 at 1x too", r.get("icon_sizes"))
    r = jrun("img_compose.py", "icons", FX / "logo.png", "--out-dir", "icons2", "--preset", "web", "--name", "Acme Portal", cwd=w)
    ok(json.loads((w / "icons2" / "site.webmanifest").read_text()).get("name") == "Acme Portal" and not r.get("notes"), "compose: --name sets the manifest name", r.get("notes"))


def t_compare() -> None:
    from PIL import Image, ImageDraw

    w = workdir("compare")
    r = jrun("img_compare.py", FX / "shot.png", FX / "shot.png", cwd=w)
    ok(r.get("identical") is True and r.get("ssim") == 1.0 and r.get("changed_pct") == 0, "compare: identical images", r)
    m = img(FX / "shot.png").convert("RGB")
    ImageDraw.Draw(m).rectangle([700, 500, 799, 579], fill=(255, 0, 0))
    m.save(w / "shot-mod.png")
    r = jrun("img_compare.py", FX / "shot.png", w / "shot-mod.png", cwd=w)
    reg = (r.get("regions") or [{}])[0]
    ok(reg.get("x") == 700 and reg.get("y") == 500 and reg.get("w") == 100 and reg.get("h") == 80, "compare: the changed region is boxed exactly", r.get("regions"))
    ok(abs(r.get("changed_pct", 0) - 100 * 8000 / (1200 * 800)) < 0.01 and r.get("ssim", 1) < 1 and r.get("diff_image"), "compare: changed %, SSIM and a diff image", r)
    d = img(w / r["diff_image"])
    ok(max(d.size) <= 1568, "compare: diff image fits the vision size", d.size)
    code, out, _ = run("img_compare.py", "diff", FX / "shot.png", w / "shot-mod.png", "--no-image", cwd=w)
    ok("SSIM" in out and "700,500,100,80" in out, "compare: Markdown summary", out)
    Image.open(FX / "shot.png").resize((600, 400)).save(w / "small.png")
    r = jrun("img_compare.py", FX / "shot.png", w / "small.png", "--no-image", cwd=w)
    ok(r.get("notes") and r.get("ssim", 0) > 0.8, "compare: different sizes are scaled and noted", r.get("notes"))
    r = jrun("img_compare.py", "dupes", ROOT / "dupes", "-r", "--sheet", cwd=w)
    groups = r.get("groups", [])
    members = sorted(Path(it["file"]).name for g in groups for it in g["items"])
    ok(len(groups) == 1 and members == ["a-copy.jpg", "a-q40.jpg", "a-small.jpg", "a.jpg"], "compare: dupes groups the copies, not the other picture", groups)
    ok(groups and Path(groups[0]["keep"]).name in ("a.jpg", "a-copy.jpg") and r.get("sheets") and (w / r["sheets"][0]).exists(), "compare: keeps the biggest copy; group sheet", r.get("sheets"))
    r = jrun("img_compare.py", "dupes", ROOT / "dupes", "-r", "--exact-only", cwd=w)
    ok(len(r.get("groups", [])) == 1 and len(r["groups"][0]["items"]) == 2 and r["groups"][0]["kind"] == "exact", "compare: --exact-only", r.get("groups"))
    r = jrun("img_compare.py", "similar", ROOT / "dupes" / "a.jpg", ROOT / "dupes", "-r", "--top", "5", cwd=w)
    res = r.get("results", [])
    ok(res and Path(res[-1]["file"]).name == "b.jpg" and res[0]["distance"] == 0 and res[-1]["distance"] > 16, "compare: similar ranks the other picture last", [(Path(x["file"]).name, x["distance"]) for x in res])
    rs = jrun("img_compare.py", "hash", FX / "photo.jpg", FX / "logo.png", cwd=w)
    ok(isinstance(rs, list) and all(len(x.get("phash", "")) == 16 for x in rs), "compare: hashes are 64-bit hex", rs)


def t_optimize() -> None:
    w = workdir("optimize")
    r = jrun("img_optimize.py", ROOT / "dupes" / "a.jpg", "--out", "a-80k.jpg", "--target-kb", "40", cwd=w)
    ok(r.get("bytes_out", 1e9) <= 40 * 1024 and r.get("format") == "JPEG" and 30 <= (r.get("quality") or 0) <= 95, "optimize: JPEG within a size budget", r)
    r = jrun("img_optimize.py", ROOT / "dupes" / "a.jpg", "--out", "a-ssim.webp", "--min-ssim", "0.95", cwd=w)
    ok(r.get("ssim", 0) >= 0.95 and r.get("bytes_out", 1e9) < r.get("bytes_in", 0) and (r.get("quality") or 99) < 95, "optimize: smallest WebP with SSIM ≥ 0.95", r)
    r2 = jrun("img_optimize.py", ROOT / "dupes" / "a.jpg", "--out", "a-ssim2.webp", "--min-ssim", "0.98", cwd=w)
    ok((r2.get("ssim", 0) >= 0.98 or "below" in r2.get("warning", "")) and (r2.get("quality") or 0) > (r.get("quality") or 99), "optimize: a higher SSIM target costs more quality (or says it was missed)", (r.get("quality"), r2))
    r = jrun("img_optimize.py", FX / "shot.png", "--to", "png", "--out", "shot.png", cwd=w)
    ok(r.get("bytes_out", 1e9) < r.get("bytes_in", 0) and img(w / "shot.png").size == (1200, 800) and r.get("ssim", 0) >= 0.97, "optimize: screenshot PNG gets smaller, same size", r)
    import numpy as np
    from PIL import Image

    a = np.zeros((300, 300, 4), np.uint8)
    a[..., 0], a[..., 2] = np.linspace(0, 255, 300)[None, :], 200
    a[..., 3] = np.linspace(0, 255, 300)[:, None]
    Image.fromarray(a, "RGBA").save(w / "soft.png")
    r = jrun("img_optimize.py", w / "soft.png", "--to", "png", "--out", "soft-opt.png", cwd=w)
    o = img(w / "soft-opt.png").convert("RGBA")
    ok(o.getpixel((150, 150))[3] in range(120, 136) and (r.get("quality") == "lossless" or "already optimal" in r.get("note", "")), "optimize: soft transparency is never palette-quantised", r)
    r = jrun("img_optimize.py", FX / "anim.gif", "--out-dir", "o", cwd=w)
    ok(r.get("format") == "WEBP" and getattr(img(w / r["output"]), "n_frames", 1) == 12, "optimize: animated GIF → animated WebP", r)
    r = jrun("img_optimize.py", FX / "photo.jpg", "--to", "email", "--max-edge", "800", "--out-dir", "mail", cwd=w)
    o = img(w / r["output"]) if r.get("output") else None
    ok(o is not None and o.format == "JPEG" and max(o.size) == 800 and not len(o.getexif()), "optimize: email JPEG, resized, metadata stripped", r)
    r = jrun("img_optimize.py", FX / "photo.jpg", "--to", "jpg", "--keep-metadata", "--out", "meta.jpg", cwd=w)
    ok(img(w / "meta.jpg").getexif().get(0x010F) == "Canon", "optimize: --keep-metadata", r)
    rs = jrun("img_optimize.py", ROOT / "dupes", "-r", "--out-dir", "all", cwd=w)
    ok(isinstance(rs, list) and len(rs) == 5 and all("error" not in x for x in rs), "optimize: batch", rs if not isinstance(rs, list) else [x.get("error") for x in rs])


def t_fonts() -> None:
    w = workdir("fonts")
    F = FX / "fonts"
    r = jrun("font_tool.py", "info", F / "testvar.ttf", cwd=w)
    ok(r.get("family") == "Desk Var" and r.get("glyphs") == 98 and [a["tag"] for a in r.get("axes", [])] == ["wght"] and [i["name"] for i in r.get("instances", [])] == ["Thin", "Black"], "fonts: info of a variable font", r)
    ok(r.get("embedding") == ["installable (no embedding restrictions)"] and r.get("blocks", [{}])[0]["block"] == "Basic Latin", "fonts: embedding bits and Unicode blocks", r.get("embedding"))
    code, out, _ = run("font_tool.py", "info", F / "test.otf", cwd=w)
    ok("CFF outlines" in out and "Desk Test" in out, "fonts: Markdown info", out[:200])
    r = jrun("font_tool.py", "coverage", F / "test.ttf", "--text", "Crème €5", cwd=w)
    ok([m["code"] for m in r.get("missing", [])] == ["U+00E8"] and r["fonts"][0]["covered"] == 6, "fonts: coverage names the missing characters", r)
    r = jrun("font_tool.py", "subset", F / "test.ttf", "--text", "Hello", "--out", "sub.woff2", cwd=w)
    ok(r.get("glyphs_after", 99) <= 6 and r.get("bytes_after", 1e9) < r.get("bytes_before", 0) and (w / "sub.woff2").read_bytes()[:4] == b"wOF2", "fonts: subset to WOFF2", r)
    r = jrun("font_tool.py", "convert", F / "test.ttf", "--out", "conv.otf", cwd=w)
    jrun("font_tool.py", "convert", w / "conv.otf", "--out", "back.ttf", cwd=w)
    i1 = jrun("font_tool.py", "info", w / "conv.otf", cwd=w)
    i2 = jrun("font_tool.py", "info", w / "back.ttf", cwd=w)
    ok("CFF" in i1.get("format", "") and "TrueType" in i2.get("format", "") and i1.get("glyphs") == i2.get("glyphs") == 98, "fonts: TTF → OTF → TTF round trip", (i1.get("format"), i2.get("format")))
    r = jrun("font_tool.py", "convert", F / "test.otf", "--out", "t.woff", cwd=w)
    ok((w / "t.woff").read_bytes()[:4] == b"wOFF", "fonts: OTF → WOFF", r)
    r = jrun("font_tool.py", "convert", F / "test.ttf", "--out", "fast.woff2", "--fast", cwd=w)
    back = jrun("font_tool.py", "info", w / "fast.woff2", cwd=w)
    ok((w / "fast.woff2").read_bytes()[:4] == b"wOF2" and back.get("glyphs") == 98 and not list(w.glob(".*.part")), "fonts: --fast WOFF2 is a valid font; no .part left", (r, back.get("glyphs")))
    r = jrun("font_tool.py", "instance", F / "testvar.ttf", "--axes", "wght=900", "--out", "black.ttf", cwd=w)
    ib = jrun("font_tool.py", "info", w / "black.ttf", cwd=w)
    ok("axes" not in ib and ib.get("glyphs") == 98, "fonts: static instance of a variable font", ib.get("tables"))
    code, out, _ = run("font_tool.py", "specimen", F / "testvar.ttf", "--text", "Hello", cwd=w)
    sp = w / "renders" / "testvar-specimen.png"
    ok(out.rstrip().endswith(VIEW_HINT) and sp.exists() and img(sp).size[0] == 1400, "fonts: specimen PNG", out)
    code, out, _ = run("font_tool.py", "specimen", w / "sub.woff2", "--glyphs", cwd=w)
    ok((w / "renders" / "sub-glyphs.png").exists(), "fonts: glyph grid of a WOFF2", out)
    code, out, _ = run("font_tool.py", "find", "zzzz-no-such-font", cwd=w, expect=1)
    ok("no installed font" in out, "fonts: find reports no match", out)
    code, out, _ = run("font_tool.py", "find", "", "--limit", "3", cwd=w, expect=None)
    ok(code in (0, 1), "fonts: find lists installed fonts", out[:200])
    # Coverage comes from the font index (raw cmap ranges), which must agree with fontTools.
    env = {"DESK_FONT_DIRS": str(F)}
    r = jrun("font_tool.py", "find", "Desk Test", "--covers", "Hello", "--format", "json", cwd=w, env=env)
    ours = [f for f in r.get("faces", []) if Path(f["path"]).parent == F]
    ok(len(ours) >= 2 and all("ranges" not in f for f in ours), "fonts: find --covers finds the fonts that have the text", r)
    r = jrun("font_tool.py", "find", "Desk Test", "--covers", "Crème", "--format", "json", cwd=w, env=env, expect=None)
    ok(not [f for f in r.get("faces", []) if Path(f["path"]).parent == F], "fonts: find --covers skips fonts missing a character", r)
    sys.dont_write_bytecode = True  # never leave __pycache__ in the skill folder
    sys.path.insert(0, str(HERE))
    from _fonts import cmap_ranges, open_font

    for name in ("test.ttf", "test.otf", "testvar.ttf"):
        tt = open_font(F / name, 0)
        try:
            flat = cmap_ranges(tt) or []
            got = {c for a, b in zip(flat[0::2], flat[1::2]) for c in range(a, b + 1)}
            ok(got == set(tt.getBestCmap() or {}), f"fonts: raw cmap ranges match fontTools ({name})", len(got))
        finally:
            tt.close()


def t_text_fonts() -> None:
    """Text on images with an explicit font file (works everywhere, no system fonts needed)."""
    w = workdir("textfont")
    r = jrun("img_edit.py", FX / "shot.png", "--out", "t.png", "--op", f"text text=ABC font={FX / 'fonts' / 'test.ttf'} size=100 x=10 y=100 color=#000000", cwd=w)
    ok(r.get("fonts") == ["test.ttf"], "text: uses the given font file", r)
    t = img(w / "t.png").convert("L")
    dark = sum(1 for x in range(10, 200) for y in range(100, 200) if t.getpixel((x, y)) < 50)
    ok(dark > 1000, "text: glyphs drawn", dark)


def t_robust() -> None:
    """Files seen in the wild: macOS multi-resolution TIFFs, pages that are not animations, unreadable files."""
    from PIL import Image, ImageDraw

    w = workdir("robust")
    # A macOS "HiDPI" TIFF (tiffutil -cathidpicheck): the same picture at 1× and 2× as two pages.
    big = Image.new("RGBA", (256, 192), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse([16, 16, 240, 176], fill=(30, 136, 229, 255))
    small = big.resize((128, 96))
    src = w / "src"
    src.mkdir()
    small.save(src / "icon.tiff", save_all=True, append_images=[big], compression="tiff_lzw")
    r = jrun("img_info.py", src / "icon.tiff", cwd=w)
    ok(r.get("multi_resolution") is True and r.get("page_sizes") == [[128, 96], [256, 192]], "robust: multi-resolution TIFF detected", r)
    r = jrun("img_convert.py", src / "icon.tiff", "--to", "webp", "--out", "icon.webp", cwd=w)
    o = img(w / "icon.webp")
    ok(o.size == (256, 192) and getattr(o, "n_frames", 1) == 1 and "largest" in str(r.get("note")), "robust: multi-resolution TIFF → WebP uses the largest version", (o.size, r))
    r = jrun("img_edit.py", src / "icon.tiff", "--out", "icon.png", "--op", "border width=4", cwd=w)
    ok(img(w / "icon.png").size == (264, 200), "robust: edits start from the largest version", img(w / "icon.png").size)
    code, out, _ = run("img_view.py", src / "icon.tiff", cwd=w)
    ok("largest version" in out and "frames (showing" not in out and img(w / "renders" / "icon.png").size == (256, 192), "robust: view shows the largest version", out[:300])
    # Pages of a document TIFF are not an animation: a GIF or WebP gets the first page, and says so.
    r = jrun("img_convert.py", FX / "pages.tif", "--to", "gif", "--out", "page1.gif", cwd=w)
    ok(getattr(img(w / "page1.gif"), "n_frames", 1) == 1 and "3 pages" in str(r.get("note")), "robust: TIFF pages → GIF writes the first page", r.get("note"))
    r = jrun("img_edit.py", FX / "pages.tif", "--out", "pages-edited.webp", "--op", "grayscale", cwd=w)
    ok(getattr(img(w / "pages-edited.webp"), "n_frames", 1) == 1, "robust: editing a TIFF to WebP does not animate its pages", r)
    # Unreadable files: one clear error line, and the error is not cached (a fixed skill must not repeat it).
    (src / "bad.png").write_bytes(b"not an image at all")
    code, out, err = run("img_view.py", src / "bad.png", cwd=w, expect=1)
    ok(err.strip().startswith("error: bad.png:") and "bad.png: bad.png" not in err and len(err.strip().splitlines()) == 1, "robust: one error line for an unreadable file", err)
    code, out, err = run("img_view.py", src / "bad.png", src / "icon.tiff", "--out-dir", "mixed", cwd=w, expect=1)
    ok("error: bad.png:" in out and out.rstrip().endswith(VIEW_HINT) and (w / "mixed" / "icon.png").exists(), "robust: a bad file does not stop the others", out[-300:])
    run("img_info.py", src / "bad.png", cwd=w, expect=1)
    run("img_compare.py", "hash", src / "bad.png", cwd=w, expect=None)
    stored = [v for v in CACHE.rglob("value.json") if "bad.png" in v.read_text(encoding="utf-8", errors="replace")]
    ok(not stored, "robust: errors are not cached", [str(v) for v in stored][:3])
    # SVG sizes: a malformed viewBox is no size (it raised ValueError), and a padded width is read in linear time (an unknown unit counts as px).
    sys.path.insert(0, str(HERE))
    from _img import svg_intrinsic_size

    ns = 'xmlns="http://www.w3.org/2000/svg"'
    (src / "vb.svg").write_text(f'<svg {ns} viewBox="0 0 abc 10"/>', encoding="utf-8")
    (src / "pad.svg").write_text(f'<svg {ns} width="1{" " * 60000}x" height=" 20 px "/>', encoding="utf-8")
    (src / "unit.svg").write_text(f'<svg {ns} width=" 2in " height="30"/>', encoding="utf-8")
    t = time.time()
    sizes = [svg_intrinsic_size(src / n) for n in ("vb.svg", "pad.svg", "unit.svg")]
    ok(sizes == [None, (1.0, 20.0), (192.0, 30.0)] and time.time() - t < 2, "robust: odd SVG width, height and viewBox values", (sizes, round(time.time() - t, 2)))
    # With the cache off, nothing may be left in the temp folder (good files, bad files, sheets).
    private = w / "tmp"
    private.mkdir()
    env = {"DESK_NO_CACHE": "1", "TMPDIR": str(private), "TMP": str(private), "TEMP": str(private)}
    run("img_view.py", src / "bad.png", src / "icon.tiff", "--out-dir", "nc", cwd=w, env=env, expect=1)
    run("img_view.py", src, FX / "logo.png", "--sheet", "--out-dir", "nc", cwd=w, env=env, expect=None)
    run("img_info.py", src / "icon.tiff", src / "bad.png", cwd=w, env=env, expect=None)
    run("img_compare.py", "hash", src / "icon.tiff", src / "bad.png", cwd=w, env=env, expect=1)
    left = [p.name for p in private.iterdir()]
    ok(not left, "robust: no temp files left behind with the cache off", left)


def _strict_json(text: str) -> Any:
    def bad(c: str) -> Any:
        raise ValueError(f"non-standard JSON constant {c}")

    return json.loads(text, parse_constant=bad)


def _png_with_ztxt(path: Path, inflated: int) -> None:
    """A small PNG with a zTXt chunk that inflates to `inflated` bytes (Pillow refuses more than 1 MB)."""
    import zlib

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(buf, "PNG")
    png = buf.getvalue()
    body = b"Comment\x00\x00" + zlib.compress(b"A" * inflated, 9)
    chunk = struct.pack(">I", len(body)) + b"zTXt" + body + struct.pack(">I", zlib.crc32(b"zTXt" + body) & 0xFFFFFFFF)
    i = png.index(b"IDAT") - 4
    path.write_bytes(png[:i] + chunk + png[i:])


def _woff_bombs(d: Path) -> None:
    """From the test font: a WOFF whose name table declares 2 MB (over a 1 MB limit), one that declares 5000 bytes
    but inflates to 2 MB, and a WOFF2 whose Brotli stream inflates far past its table directory."""
    import zlib

    from fontTools.ttLib import TTFont

    f = TTFont(str(FX / "fonts" / "test.ttf"))
    f.flavor = "woff"
    f.save(str(d / "ok.woff"))
    f = TTFont(str(FX / "fonts" / "test.ttf"))
    f.flavor = "woff2"
    f.save(str(d / "ok.woff2"))
    data = (d / "ok.woff").read_bytes()
    n = struct.unpack(">H", data[12:14])[0]
    blob = zlib.compress(b"\0" * (2 << 20), 9)
    for name, declared in (("bomb.woff", 2 << 20), ("sneaky.woff", 5000)):
        out = bytearray(data)
        off = len(out)
        out += blob
        for i in range(n):
            e = list(struct.unpack(">4sIIII", out[44 + 20 * i : 64 + 20 * i]))
            if e[0] == b"name":
                e[1], e[2], e[3] = off, len(blob), declared
                out[44 + 20 * i : 64 + 20 * i] = struct.pack(">4sIIII", *e)
        struct.pack_into(">I", out, 8, len(out))
        (d / name).write_bytes(bytes(out))
    import brotli

    sys.path.insert(0, str(HERE))
    from _fonts import _woff2_stream

    w2 = (d / "ok.woff2").read_bytes()
    start, _expected = _woff2_stream(w2)
    stream = brotli.compress(b"\0" * (3 << 20), quality=5)
    out = bytearray(w2[:start]) + stream
    struct.pack_into(">I", out, 20, len(stream))
    struct.pack_into(">I", out, 8, len(out))
    (d / "bomb.woff2").write_bytes(bytes(out))


def t_regress() -> None:
    """Regression checks for the defects an acceptance test found (one check or more per defect)."""
    import numpy as np
    from PIL import Image, ImageDraw

    w = workdir("regress")
    src = np.asarray(img(FX / "shot.png").convert("RGB"), dtype=np.int16)
    # Multi-line text of every kind draws instead of crashing: \n, max_width wrapping (px and %), labels, watermarks.
    ops = [{"op": "text", "text": "Line one\nLine two", "size": 40, "color": "#000000", "x": 100, "y": 300},
           {"op": "text", "text": "one two three four five six seven eight nine ten", "max_width": 300, "size": 30, "x": 700, "y": 90, "color": "#000000"},
           {"op": "text", "text": "wrap me at ninety percent of the width please, all of it", "max_width": "90%", "position": "top"},
           {"op": "rect", "box": "100,450,300,100", "label": "Two\nlines"},
           {"op": "watermark", "text": "CONFIDENTIAL\nDraft", "tile": True}]
    r = jrun("img_edit.py", FX / "shot.png", "--out", "multiline.png", "--ops", json.dumps(ops), cwd=w)
    ok(r.get("ops") == ["text", "text", "text", "rect", "watermark"] and "error" not in r, "regress: multi-line text, wrapped text, labels and watermarks draw", r)
    if (w / "multiline.png").exists():
        a = np.asarray(img(w / "multiline.png").convert("L"))[300:460, 100:600]
        rows = np.nonzero((a < 60).any(axis=1))[0]
        ok(len(rows) and rows.max() - rows.min() > 55, "regress: '\\n' gives two lines of text", (rows.min(), rows.max()) if len(rows) else "no text")
        b = np.asarray(img(w / "multiline.png").convert("L"))[90:400, 700:1010]
        rows = np.nonzero((b < 60).any(axis=1))[0]
        ok(len(rows) and rows.max() - rows.min() > 60, "regress: max_width wraps onto several lines", (rows.min(), rows.max()) if len(rows) else "no text")
    # A caption longer than the image shrinks to fit inside the margins (and says so); fit=false keeps it and warns.
    cap = "Figure 1: Example screen with every setting and several more words that are much too long"
    r = jrun("img_edit.py", FX / "shot.png", "--out", "caption.png", "--op", f'text text="{cap}" position=bottom bg=#000000aa', cwd=w)
    c = np.asarray(img(w / "caption.png").convert("RGB"), dtype=np.int16)
    ok(any("shrunk" in n for n in r.get("notes", [])) and np.abs(c[:, :8] - src[:, :8]).max() == 0 and np.abs(c[:, -8:] - src[:, -8:]).max() == 0,
       "regress: a long caption shrinks to fit between the margins", r.get("notes"))
    r = jrun("img_edit.py", FX / "shot.png", "--out", "caption2.png", "--op", f'text text="{cap}" position=bottom fit=false', cwd=w)
    ok(any("cut off" in n for n in r.get("notes", [])), "regress: text that overflows with fit=false is reported", r.get("notes"))
    # Misspelled parameters are usage errors that name the right one.
    code, out, err = run("img_edit.py", FX / "shot.png", "--out", "typo.png", "--op", "rect box=10,10,50,50 colour=blue lable=Hi", cwd=w, expect=2)
    ok("unknown parameter 'colour'" in err and "did you mean 'color'" in err and not (w / "typo.png").exists(), "regress: unknown op parameters are refused with a suggestion", err)
    # --op list values keep their inner quotes: several blur boxes.
    r = jrun("img_edit.py", FX / "shot.png", "--out", "boxes.png", "--op", 'blur radius=6 boxes=["80,130,340,100","80,250,300,90"]', cwd=w)
    bx = np.asarray(img(w / "boxes.png").convert("RGB"), dtype=np.int16)
    ok(r.get("ops") == ["blur"] and np.abs(bx[130:230, 80:420] - src[130:230, 80:420]).max() > 0 and np.abs(bx[250:340, 80:380] - src[250:340, 80:380]).max() > 0
       and np.abs(bx[400:, :] - src[400:, :]).max() == 0,
       "regress: --op boxes=[\"…\",\"…\"] blurs each box and nothing else", r)
    # HEIC --lossless is exact.
    jrun("img_convert.py", FX / "photo.jpg", "--to", "png", "--max-edge", "320", "--out", "small.png", cwd=w)
    jrun("img_convert.py", w / "small.png", "--to", "heic", "--lossless", "--out", "small.heic", cwd=w)
    jrun("img_convert.py", w / "small.heic", "--to", "png", "--out", "small-rt.png", cwd=w)
    code, out, _ = run("img_compare.py", w / "small.png", w / "small-rt.png", "--threshold", "0", "--no-image", "--format", "json", cwd=w)
    res = _strict_json(out) if out.strip() else {}
    ok(res.get("identical") is True and res.get("psnr_db") is None, "regress: HEIC --lossless round trip is pixel-identical; identical images give PSNR null (strict JSON)", res)
    # Animated WebP frame durations (Pillow reports them only after decoding each frame).
    jrun("img_convert.py", FX / "anim.gif", "--to", "webp", "--out", "anim.webp", cwd=w)
    r = jrun("img_info.py", w / "anim.webp", cwd=w)
    an = r.get("animation") or {}
    ok(an.get("total_ms") == 1200 and set(an.get("durations_ms", [])) == {80, 120}, "regress: animated WebP durations are read", an)
    code, out, _ = run("img_view.py", FX / "anim.gif", "--frames", "--max-frames", "3", "--out-dir", "fr", cwd=w)
    ok("1.20s total" in out, "regress: --frames total covers every frame, however few are shown", out[-300:])
    # A PNG with an oversized zTXt chunk still shows its pixels, with a warning.
    _png_with_ztxt(w / "ztxt.png", 3 << 20)
    code, out, err = run("img_view.py", w / "ztxt.png", "--out-dir", "zt", cwd=w)
    ok(code == 0 and "text chunk" in out and (w / "zt" / "ztxt.png").exists(), "regress: a PNG text bomb is skipped, the image renders", out + err)
    r = jrun("img_info.py", w / "ztxt.png", cwd=w)
    ok("text chunk" in r.get("warning", "") and r.get("width") == 32, "regress: img_info reports the skipped text chunk", r.get("warning"))
    # .svgz is inflated with a limit: a gzip bomb is refused before it takes memory.
    import gzip

    (w / "bomb.svgz").write_bytes(gzip.compress(b'<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9"><!--' + b" " * (3 << 20) + b"--></svg>"))
    for sc in ("img_info.py", "img_view.py"):
        code, out, err = run(sc, w / "bomb.svgz", cwd=w, expect=1, env={"DESK_SVG_MAX_MB": "1"})
        ok("gzip bomb" in err + out, f"regress: {sc} refuses a .svgz gzip bomb", err + out)
    # WOFF/WOFF2 tables that inflate past their limits or declared sizes are refused.
    fd = w / "fonts"
    fd.mkdir(exist_ok=True)
    _woff_bombs(fd)
    r = jrun("font_tool.py", "info", fd / "ok.woff", cwd=w)
    ok(r.get("glyphs") == 98, "regress: a normal WOFF still reads", r.get("glyphs"))
    r = jrun("font_tool.py", "info", fd / "ok.woff2", cwd=w)
    ok(r.get("glyphs") == 98, "regress: a normal WOFF2 still reads", r.get("glyphs"))
    for name, words in (("bomb.woff", "declares"), ("sneaky.woff", "inflates past"), ("bomb.woff2", "inflates past")):
        code, out, err = run("font_tool.py", "info", fd / name, cwd=w, expect=1, env={"DESK_FONT_MAX_MB": "1"})
        ok(words in err and name in err, f"regress: {name} is refused", err)
    # A damaged font's error names the file.
    (fd / "trunc.ttf").write_bytes((FX / "fonts" / "test.ttf").read_bytes()[:1500])
    code, out, err = run("font_tool.py", "info", fd / "trunc.ttf", cwd=w, expect=1)
    ok(err.startswith("error: trunc.ttf") and len(err.strip().splitlines()) == 1, "regress: font errors name the file", err)
    # img_view errors name the file once and point to the right skill.
    (w / "album.pdf").write_bytes(b"%PDF-1.4\n%junk\n")
    code, out, err = run("img_view.py", w / "album.pdf", cwd=w, expect=1)
    ok(err.count("album.pdf") == 1 and "pdf-toolkit" in err, "regress: a PDF is named once and sent to pdf-toolkit", err)
    # Dominant colours are named like a person would.
    for rgb, name in (((128, 128, 0), "olive"), ((70, 130, 180), "steel blue"), ((190, 175, 105), "khaki")):
        Image.new("RGB", (64, 64), rgb).save(w / f"c{name.replace(' ', '')}.png")
        r = jrun("img_info.py", w / f"c{name.replace(' ', '')}.png", cwd=w)
        got = ((r.get("stats") or {}).get("palette") or [{}])[0].get("name")
        ok(got == name, f"regress: colour {rgb} is named {name}", got)
    # chroma_key keeps a pale subject opaque next to a white backdrop (only its outline is softened).
    ck = Image.new("RGB", (300, 200), "white")
    dd = ImageDraw.Draw(ck)
    dd.ellipse([40, 40, 260, 160], fill=(229, 221, 160))
    dd.ellipse([110, 80, 190, 120], fill=(190, 205, 60))
    ck.save(w / "pale.png")
    jrun("img_edit.py", w / "pale.png", "--out", "pale-cut.png", "--op", "chroma_key color=auto tolerance=15", cwd=w)
    pc = img(w / "pale-cut.png")
    ok(pc.getpixel((5, 5))[3] == 0 and pc.getpixel((70, 100))[3] == 255 and pc.getpixel((150, 100))[3] == 255, "regress: chroma_key keeps a pale subject opaque", (pc.getpixel((70, 100)), pc.getpixel((5, 5))))
    # img_optimize never grows a small photo: the original pixels with the metadata stripped (orientation kept).
    r = jrun("img_optimize.py", FX / "photo.jpg", "--to", "email", "--target-kb", "20000", "--out", "small-email.jpg", cwd=w)
    o = img(w / "small-email.jpg")
    ex = o.getexif()
    ok(r.get("bytes_out", 1e12) <= r.get("bytes_in", 0) and 0x8825 not in ex and ex.get(0x010F) is None and ex.get(0x0112) == 6 and "original pixels" in r.get("note", ""),
       "regress: an under-target JPEG keeps its pixels, loses EXIF/GPS, keeps its orientation", (r.get("bytes_in"), r.get("bytes_out"), dict(ex)))
    code, out, _ = run("img_compare.py", FX / "photo.jpg", w / "small-email.jpg", "--no-orient", "--no-image", "--threshold", "0", "--format", "json", cwd=w)
    ok(_strict_json(out).get("identical") is True if out.strip() else False, "regress: … and its pixels are identical", out[:200])
    # Specimens keep a right margin (long lines end with an ellipsis instead of touching the edge).
    run("font_tool.py", "specimen", FX / "fonts" / "testvar.ttf", "--text", "a very long line of text " * 6, "--out", "spec.png", cwd=w)
    sp = np.asarray(img(w / "spec.png").convert("L"))
    ok(sp[:, -16:].min() >= 250, "regress: specimen lines stop before the right edge", int(sp[:, -16:].min()))


def t_dupes_chain() -> None:
    """Near-duplicate groups never chain: every member is within the threshold of its keeper, checked on hashes."""
    import numpy as np
    from PIL import Image, ImageDraw

    w = workdir("chain")
    d = w / "crops"
    d.mkdir()
    base = Image.fromarray(photo_array(1600, 900, 21))
    dr = ImageDraw.Draw(base)
    rng = np.random.default_rng(3)
    for _ in range(40):
        x, y = int(rng.integers(0, 1500)), int(rng.integers(0, 800))
        dr.ellipse([x, y, x + int(rng.integers(40, 200)), y + int(rng.integers(40, 200))], fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    for i in range(12):
        base.crop((i * 60, 0, i * 60 + 800, 600)).resize((400, 300)).save(d / f"crop{i:02d}.jpg", quality=88)
    res = jrun("img_compare.py", "dupes", d, "--threshold", "8", cwd=w)
    hashes = {h["file"]: h for h in jrun("img_compare.py", "hash", d, cwd=w)}

    def dist(a: str, b: str) -> int:
        pa, pb = int(hashes[a]["phash"], 16), int(hashes[b]["phash"], 16)
        return bin(pa ^ pb).count("1")

    worst = max((dist(g["keep"], it["file"]) for g in res.get("groups", []) for it in g["items"]), default=0)
    ok(res.get("groups") is not None and worst <= 8 and all(it["distance"] <= 8 for g in res["groups"] for it in g["items"]), "dupes: every member is within the threshold of its keeper (no chaining)", worst)
    code, out, _ = run("img_compare.py", "dupes", d, cwd=w)
    ok("keep (best copy)" in out if res.get("groups") else "no duplicates" in out, "dupes: the keeper's row says so (or that there are none)", out[:300])


def t_big() -> None:
    """The big-file contract, scaled down: a map with named tiles, zoom by tile address, the cached tile store,
    early reduction of huge decodes, paged listings with the exact next command, and --find."""
    import numpy as np
    from PIL import Image, ImageDraw

    w = workdir("big")
    big = Image.new("RGB", (6400, 4800), "white")
    d = ImageDraw.Draw(big)
    for x in range(0, 6400, 200):
        d.line([(x, 0), (x, 4800)], fill=(190, 200, 230), width=2)
    for y in range(0, 4800, 200):
        d.line([(0, y), (6400, y)], fill=(190, 200, 230), width=2)
    for k in range(120):
        x, y = 100 + (k % 12) * 520, 100 + (k // 12) * 470
        d.rectangle([x, y, x + 200, y + 60], outline=(20, 60, 160), width=3)
        d.text((x + 10, y + 20), f"ROOM {k:02d}", fill=(0, 0, 0))
    big.save(w / "plan.png")
    env = {"DESK_TILE_MIN_MP": "20"}
    t = time.time()
    r = jrun("img_view.py", w / "plan.png", "--map", cwd=w, env=env)
    cold = time.time() - t
    m = (r.get("images") or [{}])[0]
    ok((m.get("map") or {}).get("cols") == 5 and m["map"].get("rows") == 4 and m.get("output", "").endswith("plan-map.png"), "big: --map names a 5×4 tile grid", m.get("map"))
    mp = img(w / m["output"]) if m.get("output") else None
    ok(mp is not None and max(mp.size) <= 1568 and mp.convert("RGB").getpixel((5, 5)) == (238, 238, 238), "big: the map fits the vision size, with rulers", mp.size if mp else None)
    warm, hits = 1e9, []
    for _ in range(3):  # the best of three: a busy machine only ever adds time
        t = time.time()
        r2 = jrun("img_view.py", w / "plan.png", "--map", cwd=w, env=env)
        warm = min(warm, time.time() - t)
        hits.append(((r2.get("images") or [{}])[0]).get("cached"))
    ok(m.get("cached") is False and hits == [True, True, True], "big: the map is built once, then comes from the cache", (m.get("cached"), hits))
    # Mostly process start-up once cached, so the ratio is modest when the cold build is quick.
    ok(cold / max(warm, 1e-3) >= 3, "big: the cached map is at least 3× faster", f"cold {cold:.2f}s cached {warm:.2f}s")
    code, out, _ = run("img_view.py", w / "plan.png", "--map", cwd=w, env=env)
    ok("--zoom r1c1" in out and "tile rRcC covers" in out, "big: the map text gives the zoom command and the tile formula", out[-400:])
    r = jrun("img_view.py", w / "plan.png", "--zoom", "r2c3", cwd=w, env=env)
    z = (r.get("images") or [{}])[0]
    ok(z.get("region") == [2560, 1200, 1280, 1200] and "decoded once" in z.get("note", ""), "big: --zoom r2c3 is the tile's box; the first zoom builds the tile store", z)
    r = jrun("img_view.py", w / "plan.png", "--zoom", "r1c1", "--zoom", "r1c1:r2c2", cwd=w, env=env)
    zs = r.get("images") or [{}, {}]
    ok("tile store" in zs[0].get("note", "") and zs[1].get("region") == [0, 0, 2560, 2400], "big: later zooms read the tile store; blocks of tiles work", [x.get("note") for x in zs])
    if zs[0].get("output"):
        got = np.asarray(img(w / zs[0]["output"]).convert("RGB"))
        want = np.asarray(big.crop((0, 0, 1280, 1200)))
        ok(got.shape == want.shape and np.array_equal(got, want), "big: a tile zoom from the store is the exact pixels", got.shape)
    code, out, err = run("img_view.py", w / "plan.png", "--zoom", "r9c9", cwd=w, env=env, expect=1)
    ok("outside the map" in err + out, "big: a tile outside the map is an error", err + out)
    # A 1-bit image far larger than its preview is reduced strip by strip (no full-size RGBA copy).
    Image.new("1", (6000, 6000), 1).save(w / "bits.png")
    r = jrun("img_info.py", w / "bits.png", cwd=w)
    ok(r.get("stats", {}).get("brightness_pct", 0) > 99, "big: stats of a big 1-bit image", r.get("stats"))
    # Paged listings: every page ends with the exact command for the next; together they list every file once.
    seen: list[str] = []
    cmd = [str(ROOT / "batch"), "-r", "--max-chars", "2500"]
    for _ in range(30):
        code, out, _ = run("img_info.py", *cmd, cwd=w)
        seen += [line.split("|")[1].strip() for line in out.splitlines() if line.startswith("| ") and ".jpg" in line]
        import re as _re

        m2 = _re.search(r"Next part: python3 scripts/img_info\.py (.*)\]", out)
        if not m2:
            break
        import shlex as _shlex

        cmd = _shlex.split(m2.group(1))
    ok(sorted(seen) == sorted(set(seen)) and len(seen) == 50 and any("sub/" in x for x in seen), "big: img_info pages cover all 50 files once, with subfolder paths", (len(seen), len(set(seen))))
    code, out, err = run("img_info.py", ROOT / "batch", "-r", "--format", "json", "--max-chars", "3000", cwd=w)
    ok(isinstance(json.loads(out), list) and "Next part" in err, "big: JSON pages are valid JSON with the next command on stderr", err[-200:])
    code, out, _ = run("img_info.py", FX, "-r", "--find", "canon|eiffel", cwd=w)
    ok("exif.make" in out and "«Canon»" in out and "xmp.dc:title" in out, "big: --find returns file, field and context", out[:400])
    r = jrun("img_view.py", ROOT / "batch", "-r", "--limit", "20", "--out-dir", "pg", cwd=w)
    r2 = jrun("img_view.py", ROOT / "batch", "-r", "--limit", "20", "--offset", "20", "--out-dir", "pg", cwd=w)
    ok(r.get("next", "").endswith("--offset 20") and (r2.get("images") or [{}])[0].get("n") == 21 and r2.get("total") == 50, "big: contact sheets page through a folder", (r.get("next"), (r2.get("images") or [{}])[0].get("n")))
    with LOCK:
        RESULTS["big"] = f"map cold {cold:.2f}s, cached {warm:.2f}s"


TESTS: dict[str, Callable[[], None]] = {
    "help": t_help, "info": t_info, "view": t_view, "cache": t_cache, "convert": t_convert, "edit": t_edit,
    "compose": t_compose, "compare": t_compare, "optimize": t_optimize, "fonts": t_fonts, "text": t_text_fonts,
    "robust": t_robust, "regress": t_regress, "chain": t_dupes_chain, "big": t_big,
}


def main() -> int:
    global ROOT, FX, CACHE, VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-k", help="run only tests whose name contains this")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = ap.parse_args()
    VERBOSE = args.verbose
    t0 = time.time()
    ROOT = Path(tempfile.mkdtemp(prefix="desk-images-selftest-"))
    FX = ROOT / "fx"
    CACHE = ROOT / "cache"
    try:
        make_fixtures()
        before = snapshot([FX, ROOT / "dupes", ROOT / "batch"])
        names = [n for n in TESTS if not args.k or args.k in n]
        # The cache test times a cold render: run it alone first so other tests do not skew it.
        if "cache" in names:
            names.remove("cache")
            TESTS["cache"]()

        def guarded(n: str) -> None:
            try:
                TESTS[n]()
            except Exception:  # noqa: BLE001 — a crashing test is a failure, not an abort
                ok(False, f"{n} test crashed", traceback.format_exc()[-1500:])

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(guarded, names))
        after = snapshot([FX, ROOT / "dupes", ROOT / "batch"])
        ok(before == after, "no input file was modified", [k for k in before if before[k] != after.get(k)][:5])
    finally:
        if not args.keep:
            shutil.rmtree(ROOT, ignore_errors=True)
        else:
            print(f"kept {ROOT}")
    dt = time.time() - t0
    for f in RESULTS["failures"]:
        print(f"FAIL {f}")
    extra = ", ".join(f"{k} {v}" for k, v in RESULTS.items() if k not in ("checks", "failures"))
    if RESULTS["failures"]:
        print(f"failed: {len(RESULTS['failures'])} of {RESULTS['checks']} checks in {dt:.1f}s ({extra})")
        return 1
    print(f"ok: {RESULTS['checks']} checks in {dt:.1f}s ({extra})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
