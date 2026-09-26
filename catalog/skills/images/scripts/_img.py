"""Loading, normalising and saving images for the images skill.

Pillow reads most raster formats; pillow-heif adds HEIC/HEIF, rawpy develops camera RAW files, resvg renders SVG.
Every loader returns a decoded PIL image. Heavy imports stay inside functions so `--help` stays fast.
Works on Windows, macOS and Linux.
"""

from __future__ import annotations

import glob as _glob
import io
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from _common import SkillError, UsageError

RAW_EXTS = frozenset(
    ".3fr .ari .arw .bay .cr2 .cr3 .crw .dcr .dcs .dng .erf .fff .iiq .k25 .kdc .mef .mos .mrw .nef .nrw .orf .pef "
    ".ptx .raf .raw .rw2 .rwl .sr2 .srf .srw .x3f".split()
)
SVG_EXTS = frozenset({".svg", ".svgz"})
FONT_EXTS = frozenset(".ttf .otf .woff .woff2 .ttc .otc".split())
RASTER_EXTS = frozenset(
    ".png .apng .jpg .jpeg .jpe .jfif .gif .webp .tif .tiff .bmp .dib .ico .cur .icns .heic .heif .hif .avif .avifs "
    ".psd .jp2 .j2k .jpx .jpf .tga .ppm .pgm .pbm .pnm .pcx .dds .sgi .qoi .mpo .xbm .blp .msp".split()
)
IMAGE_EXTS = RASTER_EXTS | RAW_EXTS | SVG_EXTS

#: Output formats by name or extension → (Pillow format, default extension).
OUTPUT_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("PNG", ".png"),
    "apng": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "jpeg": ("JPEG", ".jpg"),
    "jpe": ("JPEG", ".jpg"),
    "jfif": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "avif": ("AVIF", ".avif"),
    "heic": ("HEIF", ".heic"),
    "heif": ("HEIF", ".heic"),
    "hif": ("HEIF", ".heic"),
    "tif": ("TIFF", ".tif"),
    "tiff": ("TIFF", ".tif"),
    "gif": ("GIF", ".gif"),
    "bmp": ("BMP", ".bmp"),
    "ico": ("ICO", ".ico"),
    "icns": ("ICNS", ".icns"),
    "pdf": ("PDF", ".pdf"),
    "jp2": ("JPEG2000", ".jp2"),
    "tga": ("TGA", ".tga"),
    "qoi": ("QOI", ".qoi"),
    "ppm": ("PPM", ".ppm"),
}
#: Formats that can hold several frames or pages.
MULTI_FRAME = {"GIF", "PNG", "WEBP", "AVIF", "HEIF", "TIFF", "PDF"}
#: Formats that keep an alpha channel.
ALPHA_FORMATS = {"PNG", "WEBP", "AVIF", "HEIF", "TIFF", "ICO", "ICNS", "TGA", "QOI", "JPEG2000", "GIF"}
#: Lossy formats and their default quality.
DEFAULT_QUALITY = {"JPEG": 88, "WEBP": 85, "AVIF": 65, "HEIF": 80}

ORIENTATION_NAMES = {
    1: "normal",
    2: "mirrored horizontally",
    3: "rotated 180°",
    4: "mirrored vertically",
    5: "mirrored horizontally, rotated 90° counter-clockwise",
    6: "rotated 90° clockwise",
    7: "mirrored horizontally, rotated 90° clockwise",
    8: "rotated 90° counter-clockwise",
}

_READY = False
#: Text chunks (PNG zTXt/iTXt) skipped because they inflate past Pillow's limit; the pixels still load.
SKIPPED_TEXT: list[int] = []


def pil() -> Any:
    """PIL.Image with HEIF registered and a generous but finite pixel limit (DESK_MAX_PIXELS)."""
    global _READY
    from PIL import Image

    if not _READY:
        import warnings

        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001 — HEIC support is optional
            pass
        Image.MAX_IMAGE_PIXELS = int(os.environ.get("DESK_MAX_PIXELS", "600000000"))
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        _skip_huge_png_text()
        _READY = True
    return Image


def _skip_huge_png_text() -> None:
    """A PNG whose zTXt/iTXt chunk inflates past Pillow's limit (a text bomb) still opens: the chunk is skipped.

    Pillow refuses the whole file with "Decompressed data too large"; its limit stays in force (nothing big is
    inflated), only the refusal becomes a skipped chunk, which the scripts report.
    """
    try:
        from PIL import PngImagePlugin
    except ImportError:  # pragma: no cover
        return
    orig = getattr(PngImagePlugin, "_safe_zlib_decompress", None)
    if orig is None or getattr(orig, "_desk", False):
        return

    def safe(s: bytes) -> bytes:
        try:
            return orig(s)
        except ValueError:
            SKIPPED_TEXT.append(len(s))
            return b""

    safe._desk = True  # type: ignore[attr-defined]
    PngImagePlugin._safe_zlib_decompress = safe


# ── cache helpers ───────────────────────────────────────────────────────

_CODE_VERSIONS: dict[tuple[str, ...], str] = {}


def code_version(*files: str) -> str:
    """A short hash of these modules' sources (next to this one), so updating the skill invalidates cached results."""
    import hashlib

    if files not in _CODE_VERSIONS:
        h = hashlib.sha256()
        here = Path(__file__).resolve().parent
        for name in files:
            try:
                h.update((here / name).read_bytes())
            except OSError:
                h.update(name.encode())
        _CODE_VERSIONS[files] = h.hexdigest()[:12]
    return _CODE_VERSIONS[files]


def cached_value(path: Any, kind: str, params: dict[str, Any] | None, version: str, compute: Any) -> Any:
    """Like _cache.cached_json, but leaves no temp folder behind when the cache is off or the disk is full."""
    import json

    from _cache import cached_dir, enabled, release

    if not enabled():
        return compute()
    hit = cache_peek(path, kind, params, version)
    if hit is not None:
        return hit
    box: dict[str, Any] = {}

    def build(tmp: Path) -> None:
        try:
            box["value"] = compute()
        except BaseException:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            raise
        (tmp / "value.json").write_text(json.dumps(box["value"], ensure_ascii=False, default=str), encoding="utf-8")

    d = cached_dir(path, kind, params, version, build)
    value = box["value"] if "value" in box else json.loads((d / "value.json").read_text(encoding="utf-8"))
    release(d)
    return value


def cache_peek(path: Any, kind: str, params: dict[str, Any] | None, version: str) -> Any:
    """The cached JSON value (from _cache.cached_json) for this file, or None, without computing anything."""
    import json

    from _cache import lookup

    try:
        d = lookup(path, kind, params, version)
        return None if d is None else json.loads((d / "value.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def batch_map(fn: Any, jobs: Sequence[Any], workers: int | None = None, min_pool: int = 3) -> list[Any]:
    """fn over jobs in worker processes (for batches of at least `min_pool`), in order.

    Multi-threaded encoders (AVIF) get cores / workers threads each, so a batch does not run 7 × 8 threads on
    8 cores (DESK_IMG_THREADS, read by encoder_threads()).
    """
    from _common import pool_map, workers_for

    if len(jobs) < min_pool:
        return [fn(j) for j in jobs]
    n = workers_for(len(jobs), workers)
    if n > 1:
        os.environ["DESK_IMG_THREADS"] = str(max(1, (os.cpu_count() or 2) // n))
    return pool_map(fn, jobs, workers=n)


def encoder_threads() -> int | None:
    """Threads an encoder may use in this process: set for batch workers, else None (the library decides)."""
    try:
        n = int(os.environ.get("DESK_IMG_THREADS", "0"))
    except ValueError:
        return None
    return n if n > 0 else None


def cached_map(fn: Any, jobs: Sequence[Any], peek: Any, workers: int | None = None, min_pool: int = 4) -> list[Any]:
    """fn over jobs in parallel, answering cache hits in this process first: a fully cached batch starts no workers."""
    out = [peek(j) for j in jobs]
    miss = [i for i, r in enumerate(out) if r is None]
    todo = [jobs[i] for i in miss]
    done = batch_map(fn, todo, workers=workers, min_pool=min_pool)
    for i, r in zip(miss, done):
        out[i] = r
    return out


class Uncached(Exception):
    """Raised inside a cache build to hand back a result, usually an error record, without storing it."""

    def __init__(self, rec: dict[str, Any]):
        super().__init__("not cached")
        self.rec = rec


# ── inputs ──────────────────────────────────────────────────────────────


def natural_key(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


@dataclass
class Input:
    path: Path
    base: Path  # the folder outputs mirror from (the dir given, the glob's fixed prefix, or the file's parent)

    @property
    def rel(self) -> Path:
        try:
            return self.path.relative_to(self.base)
        except ValueError:
            return Path(self.path.name)


def expand_inputs(items: Sequence[str], recursive: bool = False, exts: Iterable[str] = IMAGE_EXTS, what: str = "images") -> list[Input]:
    """Files, folders and globs → existing files (folders filtered by extension, natural order, no duplicates)."""
    exts = {e.lower() for e in exts}
    out: list[Input] = []
    seen: set[str] = set()

    def add(p: Path, base: Path) -> None:
        key = os.path.normcase(str(p.resolve()))
        if key not in seen:
            seen.add(key)
            out.append(Input(p, base))

    for item in items:
        p = Path(item).expanduser()
        if p.is_file():
            add(p, p.parent)
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            files = sorted((f for f in it if f.is_file() and f.suffix.lower() in exts and not f.name.startswith(".")), key=lambda f: natural_key(str(f.relative_to(p))))
            if not files:
                raise SkillError(f"no {what} in {p}{'' if recursive else ' (use -r to include subfolders)'}")
            for f in files:
                add(f, p)
        elif _glob.has_magic(item):
            matches = sorted(_glob.glob(str(p), recursive=True), key=natural_key)
            files = [Path(m) for m in matches if Path(m).is_file() and (Path(m).suffix.lower() in exts or not Path(m).name.startswith("."))]
            files = [f for f in files if f.suffix.lower() in exts] or files
            if not files:
                raise SkillError(f"no files match {item}")
            base = _glob_base(str(p))
            for f in files:
                add(f, base)
        else:
            raise SkillError(f"{item} does not exist")
    if not out:
        raise UsageError(f"no input {what}")
    return out


def input_file_any(path: str) -> Path:
    """One existing input image (any extension; the content decides)."""
    from _common import input_file

    return input_file(path)


def _glob_base(pattern: str) -> Path:
    parts = Path(pattern).parts
    fixed: list[str] = []
    for part in parts:
        if _glob.has_magic(part):
            break
        fixed.append(part)
    return Path(*fixed) if fixed else Path(".")


# ── kinds and loading ───────────────────────────────────────────────────

_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"wOFF", b"wOF2", b"ttcf", b"true")


def kind_of(path: Path) -> str:
    """'raster', 'raw', 'svg' or 'font', by extension and then by content."""
    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        return "raw"
    if ext in SVG_EXTS:
        return "svg"
    if ext in FONT_EXTS:
        return "font"
    if ext in RASTER_EXTS:
        return "raster"
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError as e:
        raise SkillError(f"cannot read {path}: {e}") from e
    if head[:4] in _FONT_MAGIC:
        return "font"
    low = head.lower()
    if b"<svg" in low and (low.lstrip().startswith(b"<") or low.startswith(b"\xef\xbb\xbf")):
        return "svg"
    return "raster"


@dataclass
class Loaded:
    img: Any  # a decoded PIL image
    kind: str  # raster | raw | svg
    format: str  # JPEG, PNG, HEIF, SVG, RAW, …
    native_size: tuple[int, int]  # full-resolution size as stored (before orientation)
    n_frames: int = 1
    info: dict[str, Any] = field(default_factory=dict)  # the source's info: exif, icc_profile, dpi, xmp, duration…
    oriented: int = 1  # EXIF orientation that was applied (1 = none)
    note: str = ""  # e.g. "embedded RAW preview"


@dataclass
class RawOpts:
    wb: str = "camera"  # camera | auto | daylight
    bright: float = 1.0
    auto_bright: bool = True
    half: bool = False
    use_preview: bool = True  # allow the embedded JPEG when it is big enough for the requested size


@dataclass
class SvgOpts:
    width: int | None = None
    height: int | None = None
    scale: float | None = None
    dpi: float | None = None
    background: str | None = None
    font_dirs: list[str] = field(default_factory=list)


def load(
    path: Path,
    frame: int = 0,
    orient: bool = True,
    max_edge: int | None = None,
    raw: RawOpts | None = None,
    svg: SvgOpts | None = None,
) -> Loaded:
    """Decodes one frame of any supported image. `max_edge` is a hint that allows faster, smaller decodes
    (JPEG DCT scaling, the embedded RAW preview, SVG rendered to size); the caller still fits the result."""
    path = Path(path)
    kind = kind_of(path)
    if kind == "font":
        raise SkillError(f"{path.name} is a font; use font_tool.py (info, specimen) instead")
    if kind == "svg":
        return _load_svg(path, max_edge, svg or SvgOpts())
    if kind == "raw":
        try:
            return _load_raw(path, orient, max_edge, raw or RawOpts())
        except SkillError:
            # Some "RAW" extensions are plain TIFFs (e.g. .tif-like .dng previews); let Pillow try.
            pass
    return _load_raster(path, frame, orient, max_edge)


def probe(path: Path, frame: int = 0, orient: bool = True) -> dict[str, Any]:
    """Size and kind from the header only (no pixels decoded): 'size' is the upright size, 'native' as stored."""
    kind = kind_of(path)
    if kind == "svg":
        s = svg_intrinsic_size(path)
        if not s:
            s = render_svg(path).size
        size = (max(1, round(s[0])), max(1, round(s[1])))
        return {"kind": "svg", "format": "SVG", "size": size, "native": size, "frames": 1, "oriented": 1}
    if kind == "raw":
        try:
            import rawpy

            with rawpy.imread(io.BytesIO(path.read_bytes())) as raw:
                s = raw.sizes
                native = (int(s.width), int(s.height))
                flip = int(getattr(s, "flip", 0) or 0)
            o = _FLIP_TO_EXIF.get(flip, 1) if orient else 1
            return {"kind": "raw", "format": "RAW", "size": (native[1], native[0]) if o in (5, 6, 7, 8) else native, "native": native, "frames": 1, "oriented": o}
        except Exception:  # noqa: BLE001 — a RAW extension on a TIFF-like file: Pillow below
            pass
    im = _open(path, header=True)
    try:
        n = frame_count(im)
        fmt = im.format or ""
        if frame and 0 < frame < n:
            im.seek(frame)
        elif n > 1 and fmt == "TIFF":
            best = largest_resolution(im)
            if best:
                im.seek(best)
        native = im.size
        o = 1
        if orient:
            o = header_orientation(im)
    finally:
        im.close()
    return {"kind": "raster", "format": fmt, "size": (native[1], native[0]) if o in (5, 6, 7, 8) else native, "native": native, "frames": n, "oriented": o}


def header_orientation(im: Any) -> int:
    """The EXIF orientation of an opened image without decoding it. (Pillow's PNG getexif() decodes the whole image
    to look for an eXIf chunk after the pixels: 600 MB for a 200 MP PNG; only a chunk before them is read here.)"""
    if im.format == "PNG" and "exif" not in im.info:
        return 1
    try:
        o = int(im.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001
        return 1
    return o if 1 <= o <= 8 else 1


def _open(path: Path, header: bool = False) -> Any:
    """Opens an image from memory (small files) so no OS file handle stays open, which matters on Windows.
    `header`: open from the file (only the header is read); the caller closes it."""
    Image = pil()
    from PIL import UnidentifiedImageError

    try:
        size = path.stat().st_size
        src: Any = io.BytesIO(path.read_bytes()) if size <= 256 * 1024 * 1024 and not header else path
        return Image.open(src)
    except UnidentifiedImageError:
        raise SkillError(f"{path.name}: not an image format this skill can read{elsewhere(path)}") from None
    except Image.DecompressionBombError as e:
        raise SkillError(f"{path.name}: {e}; set DESK_MAX_PIXELS higher if you trust the file") from None
    except OSError as e:
        raise SkillError(f"{path.name}: {e}") from None


#: Where files that are not images belong (neighbouring skills, which may not be installed).
_ELSEWHERE = {
    ".pdf": "a PDF: render its pages with the pdf-toolkit skill (pdf_render.py), then look at the PNGs",
    ".docx": "a Word document: see the word-documents skill", ".doc": "a Word document: see the word-documents skill",
    ".odt": "a text document: see the word-documents skill", ".rtf": "a text document: see the word-documents skill",
    ".pptx": "a presentation: see the presentations skill", ".ppt": "a presentation: see the presentations skill", ".key": "a Keynote file",
    ".xlsx": "a spreadsheet: see the spreadsheets skill", ".xls": "a spreadsheet: see the spreadsheets skill", ".csv": "a table: see the spreadsheets or data-files skill",
    ".mp4": "a video: the audio-video skill takes frames (media_frames.py)", ".mov": "a video: the audio-video skill takes frames (media_frames.py)",
    ".mkv": "a video: see the audio-video skill", ".webm": "a video: see the audio-video skill", ".avi": "a video: see the audio-video skill",
    ".zip": "an archive: see the archives skill", ".eps": "EPS (PostScript) is not supported; convert it to PDF or SVG first",
    ".jxl": "JPEG XL is not supported by this runtime", ".ai": "an Illustrator file: open it as a PDF with pdf-toolkit",
}


def elsewhere(path: Path) -> str:
    hint = _ELSEWHERE.get(path.suffix.lower())
    return f" ({hint})" if hint else " (the file-inspector skill's file_identify.py can tell what it is)"


def n_frames_safe(im: Any) -> int:
    try:
        return frame_count(im)
    except Exception:  # noqa: BLE001
        return 1


def frame_count(im: Any) -> int:
    """Frames of an animation or pages of a multi-page file (a PSD's layers are not frames: its composite is)."""
    if getattr(im, "format", None) == "PSD":
        return 1
    try:
        return int(getattr(im, "n_frames", 1) or 1)
    except Exception:  # noqa: BLE001
        return 1


def _load_raster(path: Path, frame: int, orient: bool, max_edge: int | None) -> Loaded:
    SKIPPED_TEXT.clear()
    im = _open(path)
    try:
        fmt = im.format or path.suffix.lstrip(".").upper()
        n = frame_count(im)
        note = ""
        if frame:
            if frame >= n or frame < 0:
                raise UsageError(f"{path.name} has {n} frame(s); frame {frame + 1} does not exist")
            im.seek(frame)
        elif n > 1 and fmt == "TIFF":
            best = largest_resolution(im)
            if best:
                im.seek(best)
                note = f"multi-resolution TIFF: showing the largest version (page {best + 1} of {n})"
        native = im.size
        info = dict(im.info)
        if max_edge and fmt == "JPEG" and max(native) > 2 * max_edge:
            r = max_edge / max(native)
            im.draft(im.mode if im.mode in ("RGB", "L", "CMYK") else "RGB", (max(1, int(native[0] * r)), max(1, int(native[1] * r))))
        try:
            im.load()
        except OSError as e:
            if "truncated" in str(e).lower():
                from PIL import ImageFile

                ImageFile.LOAD_TRUNCATED_IMAGES = True
                im.load()
                info["_warning"] = "the file is truncated; the missing part is grey"
            else:
                raise SkillError(f"{path.name}: {e}") from None
        if SKIPPED_TEXT:
            info["_warning"] = "a PNG text chunk too large to inflate (a possible text bomb) was skipped; the pixels are intact"
        img = im.copy() if n > 1 else im
        if n > 1:
            img.info = dict(im.info)
    finally:
        if n_frames_safe(im) > 1:
            im.close()
    if max_edge and max(img.size) >= 4 * max_edge and img.mode not in ("I", "F"):
        # A huge PNG/TIFF/WebP/HEIC decodes at full size; reduce it strip by strip right away, so no full-size
        # conversion (1-bit → L → RGBA of 400 MP is gigabytes) ever happens. Callers still fit the result.
        img = reduce_strips(img, int(max(img.size) // (2 * max_edge)))
        img.info = dict(info)
    orientation = 1
    if orient:
        img, orientation = apply_orientation(img)
    return Loaded(img, "raster", fmt, native, n, info, orientation, note)


def reduce_strips(img: Any, factor: int) -> Any:
    """`img` scaled down by an integer factor (box average), converted to 8-bit L/LA/RGB/RGBA a strip at a time,
    so memory stays near the decoded image's own size."""
    import math

    Image = pil()
    if factor < 2:
        return normalize(img)
    W, H = img.size
    rows = max(factor, (max(1, 16_000_000 // max(1, W)) // factor) * factor)
    out = None
    for y in range(0, H, rows):
        part = normalize(img.crop((0, y, W, min(H, y + rows))))
        small = part.reduce(factor)
        if out is None:
            out = Image.new(small.mode, (math.ceil(W / factor), math.ceil(H / factor)))
        out.paste(small, (0, y // factor))
    return out


#: Formats whose frames are an animation (TIFF, ICO, ICNS and MPO frames are pages or variants instead).
ANIMATED_FORMATS = {"GIF", "PNG", "WEBP", "AVIF"}


def largest_resolution(im: Any) -> int:
    """The index of the biggest page when every page is the same picture at another size (macOS @2x TIFFs), else 0."""
    try:
        n = frame_count(im)
        sizes = []
        for i in range(min(n, 16)):
            im.seek(i)
            sizes.append(im.size)
        im.seek(0)
    except Exception:  # noqa: BLE001
        return 0
    if len(set(sizes)) < 2:
        return 0
    ratios = [w / h for w, h in sizes if h]
    if max(ratios) - min(ratios) > 0.01 * max(ratios):
        return 0
    return max(range(len(sizes)), key=lambda i: sizes[i][0] * sizes[i][1])


def apply_orientation(img: Any) -> tuple[Any, int]:
    """Applies the EXIF orientation (the transposed copy's EXIF says 1). Returns (image, orientation applied)."""
    from PIL import ImageOps

    try:
        o = int(img.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 — broken EXIF
        return img, 1
    if o in (0, 1) or o > 8:
        return img, 1
    try:
        out = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        return img, 1
    return (out if out is not None else img), o


def _load_svg(path: Path, max_edge: int | None, opts: SvgOpts) -> Loaded:
    intrinsic = svg_intrinsic_size(path)
    w, h = opts.width, opts.height
    zoom = opts.scale
    if opts.dpi and not (w or h or zoom):
        zoom = opts.dpi / 96.0
    if not (w or h or zoom) and max_edge and intrinsic:
        iw, ih = intrinsic
        zoom = max_edge / max(iw, ih)
    img = render_svg(path, width=w, height=h, zoom=zoom, background=opts.background, font_dirs=opts.font_dirs)
    native = (round(intrinsic[0]), round(intrinsic[1])) if intrinsic else img.size
    return Loaded(img, "svg", "SVG", native, 1, {}, 1)


_UNITS = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "em": 16.0, "ex": 8.0, "q": 96 / 101.6}


def svg_max_bytes() -> int:
    """The largest SVG (after gunzip for .svgz) this skill parses: DESK_SVG_MAX_MB, default 64 MB."""
    try:
        return max(1, int(os.environ.get("DESK_SVG_MAX_MB", "64"))) * 1024 * 1024
    except ValueError:
        return 64 * 1024 * 1024


_SVG_MEMO: dict[tuple[str, int, int], bytes] = {}


def _svg_bytes(path: Path) -> bytes:
    """The SVG's markup: .svgz is inflated in a stream and refused past DESK_SVG_MAX_MB (a gzip bomb never loads)."""
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key in _SVG_MEMO:
        return _SVG_MEMO[key]
    limit = svg_max_bytes()
    hint = "raise DESK_SVG_MAX_MB if you trust the file"
    with open(path, "rb") as f:
        head = f.read(2)
    if head != b"\x1f\x8b":
        if st.st_size > limit:
            raise SkillError(f"{path.name}: the SVG is {st.st_size / 1048576:.0f} MB, over the {limit // 1048576} MB limit ({hint})")
        data = path.read_bytes()
    else:
        import zlib

        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        parts: list[bytes] = []
        total = 0
        with open(path, "rb") as f:
            try:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    buf = chunk
                    while buf:
                        out = d.decompress(buf, limit + 1 - total)
                        total += len(out)
                        parts.append(out)
                        if total > limit:
                            raise SkillError(f"{path.name}: the .svgz inflates to more than {limit // 1048576} MB (a possible gzip bomb; {hint})")
                        buf = d.unconsumed_tail
                        if d.eof:
                            break
                    if d.eof:
                        break
            except zlib.error as e:
                raise SkillError(f"{path.name}: damaged .svgz ({e})") from None
        data = b"".join(parts)
    _SVG_MEMO.clear()
    _SVG_MEMO[key] = data
    return data


def _svg_text(path: Path) -> str:
    data = _svg_bytes(path)
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def svg_root(path: Path) -> tuple[str, dict[str, str]]:
    """The root element's tag and attributes (namespace stripped)."""
    import xml.etree.ElementTree as ET

    try:
        for _event, el in ET.iterparse(io.BytesIO(_svg_bytes(path)), events=("start",)):
            return el.tag.split("}")[-1], {k.split("}")[-1]: v for k, v in el.attrib.items()}
    except ET.ParseError as e:
        raise SkillError(f"{path.name}: not a valid SVG ({e})") from None
    raise SkillError(f"{path.name}: empty SVG")


def svg_intrinsic_size(path: Path) -> tuple[float, float] | None:
    """The SVG's size in CSS pixels, from width/height (with units) or the viewBox."""
    try:
        tag, a = svg_root(path)
    except SkillError:
        return None
    if tag != "svg":
        return None
    vb = None
    if a.get("viewBox"):
        try:
            nums = [float(x) for x in re.split(r"[\s,]+", a["viewBox"].strip()) if x]
        except ValueError:  # viewBox="0 0 abc 10": no size from it
            nums = []
        if len(nums) == 4 and nums[2] > 0 and nums[3] > 0:
            vb = (nums[2], nums[3])

    def length(v: str | None) -> float | None:
        if not v:
            return None
        m = re.fullmatch(r"([0-9.eE+-]+)\s*([a-zA-Z%]*)", v.strip())  # stripped: blank runs on both sides of an empty unit were quadratic
        if not m or m.group(2) == "%":
            return None
        try:
            return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1.0)
        except ValueError:
            return None

    w, h = length(a.get("width")), length(a.get("height"))
    if w and h:
        return w, h
    if vb:
        if w:
            return w, w * vb[1] / vb[0]
        if h:
            return h * vb[0] / vb[1], h
        return vb
    return None


def system_font_dirs() -> list[str]:
    """Font folders on this machine (Windows, macOS, Linux), for SVG text and font lookups."""
    home = Path.home()
    if os.name == "nt":
        cands = [Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            cands.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        cands = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    else:
        cands = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts", home / ".fonts"]
    return [str(c) for c in cands if c.is_dir()]


def render_svg(path: Path, width: int | None = None, height: int | None = None, zoom: float | None = None, background: str | None = None, font_dirs: Sequence[str] = (), svg_string: str | None = None) -> Any:
    """Renders an SVG with resvg (system fonts plus `font_dirs`); returns an RGBA PIL image.

    With `svg_string`, renders that markup instead, resolving relative links next to `path`.
    """
    import resvg_py

    Image = pil()
    # dpi must be explicit: resvg-py passes 0 through, which collapses pt/mm/in sizes to nothing.
    kwargs: dict[str, Any] = {"resources_dir": str(path.resolve().parent), "dpi": 96.0}
    # Always hand resvg the markup read here: the size limit (and the capped .svgz inflation) then applies to it too.
    kwargs["svg_string"] = svg_string if svg_string is not None else _svg_text(path)
    dirs = [d for d in font_dirs if d and Path(d).is_dir()]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        user = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
        if user.is_dir():
            dirs.append(str(user))
    if dirs:
        kwargs["font_dirs"] = dirs
    if width and height:
        kwargs["width"], kwargs["height"] = int(width), int(height)
    elif width:
        kwargs["width"] = int(width)
    elif height:
        kwargs["height"] = int(height)
    elif zoom:
        kwargs["zoom"] = float(zoom)
    if background:
        kwargs["background"] = background
    try:
        data = resvg_py.svg_to_bytes(**kwargs)
    except BaseException as e:  # resvg raises ValueError, or a Rust panic
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise SkillError(f"{path.name}: cannot render this SVG ({e})") from None
    img = Image.open(io.BytesIO(bytes(data)))
    img.load()
    return img


def _load_raw(path: Path, orient: bool, max_edge: int | None, opts: RawOpts) -> Loaded:
    try:
        import rawpy
    except ImportError as e:  # pragma: no cover
        raise SkillError("rawpy is not available in this runtime") from e
    import numpy as np

    Image = pil()
    data = path.read_bytes()
    try:
        raw = rawpy.imread(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 — LibRawFileUnsupportedError, LibRawIOError…
        raise SkillError(f"{path.name}: not a camera RAW file LibRaw can read ({type(e).__name__})") from None
    with raw:
        s = raw.sizes
        native = (int(s.width), int(s.height))
        flip = int(getattr(s, "flip", 0) or 0)
        exif = raw_exif(path, data)
        note = ""
        if max_edge and opts.use_preview:
            thumb = _raw_thumb(raw, rawpy)
            if thumb is not None:
                t_edge = max(thumb.size)
                if t_edge >= min(max_edge, max(native) * 0.9) or (t_edge >= 1000 and t_edge >= max_edge * 0.6):
                    applied = 1
                    if orient:
                        thumb, applied = apply_orientation(thumb)
                        if applied == 1 and flip in _FLIP_TO_EXIF:
                            thumb = _flip_raw(thumb, flip)
                            applied = _FLIP_TO_EXIF[flip]
                    info = {"exif": exif} if exif else {}
                    return Loaded(thumb, "raw", "RAW", native, 1, info, applied, "embedded camera preview")
        wb = opts.wb.lower()
        kw: dict[str, Any] = {
            "use_camera_wb": wb == "camera",
            "use_auto_wb": wb == "auto",
            "half_size": bool(opts.half or (max_edge and max(native) > 2 * max_edge)),
            "no_auto_bright": not opts.auto_bright,
            "bright": float(opts.bright),
            "output_bps": 8,
            "user_flip": None if orient else 0,
        }
        if wb in ("daylight", "none"):
            kw["use_camera_wb"] = False
            kw["use_auto_wb"] = False
        try:
            rgb = raw.postprocess(**kw)
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"{path.name}: LibRaw could not develop this file ({type(e).__name__}: {e})") from None
        img = Image.fromarray(np.ascontiguousarray(rgb))
        note = "developed with LibRaw" + (" at half size" if kw["half_size"] else "")
    info = {"exif": exif} if exif else {}
    return Loaded(img, "raw", "RAW", native, 1, info, _FLIP_TO_EXIF.get(flip, 1) if orient else 1, note)


#: LibRaw's flip codes as the equivalent EXIF orientation.
_FLIP_TO_EXIF = {3: 3, 5: 8, 6: 6}


def _raw_thumb(raw: Any, rawpy: Any) -> Any:
    Image = pil()
    try:
        th = raw.extract_thumb()
    except Exception:  # noqa: BLE001 — no or unsupported thumbnail
        return None
    try:
        if th.format == rawpy.ThumbFormat.JPEG:
            im = Image.open(io.BytesIO(th.data))
            im.load()
            return im
        if th.format == rawpy.ThumbFormat.BITMAP:
            return Image.fromarray(th.data)
    except Exception:  # noqa: BLE001
        return None
    return None


def _flip_raw(img: Any, flip: int) -> Any:
    from PIL import Image as I

    if flip == 3:
        return img.transpose(I.Transpose.ROTATE_180)
    if flip == 5:
        return img.transpose(I.Transpose.ROTATE_90)
    if flip == 6:
        return img.transpose(I.Transpose.ROTATE_270)
    return img


def raw_exif(path: Path, data: bytes | None = None) -> bytes | None:
    """Clean EXIF of a camera RAW file (camera, lens, exposure, date, GPS), read from its TIFF structure (CR2, NEF,
    ARW, DNG, ORF, RW2, PEF…), from the CMT boxes of a CR3, or else from the embedded preview JPEG."""
    data = data if data is not None else path.read_bytes()
    ex = _tiff_exif(data) or _cr3_exif(data)
    if ex is None:
        try:
            import rawpy

            with rawpy.imread(io.BytesIO(data)) as raw:
                th = _raw_thumb(raw, rawpy)
                if th is not None and len(th.getexif()):
                    ex = th.getexif()
        except Exception:  # noqa: BLE001
            ex = None
    return _clean_exif(ex) if ex is not None else None


_EXIF_KEEP_IFD0 = (0x010E, 0x010F, 0x0110, 0x0112, 0x0131, 0x0132, 0x013B, 0x8298)


def _tiff_exif(data: bytes) -> Any:
    head = data[:4]
    if head[:2] == b"II":
        fixed = b"II*\x00" + data[4:]
    elif head[:2] == b"MM":
        fixed = b"MM\x00*" + data[4:]
    else:
        return None
    try:
        ex = pil().Exif()
        ex.load(fixed)
        return ex if (len(ex) and (0x010F in ex or 0x8769 in ex)) else None
    except Exception:  # noqa: BLE001
        return None


def _cr3_exif(data: bytes) -> Any:
    """Canon CR3 keeps IFD0 in a CMT1 box, the EXIF IFD in CMT2 and GPS in CMT4 (each a small TIFF)."""
    if data[4:12] != b"ftypcrx ":
        return None
    boxes = {}
    head = data[: 1 << 20]
    for tag in (b"CMT1", b"CMT2", b"CMT4"):
        i = head.find(tag)
        if i >= 4:
            size = int.from_bytes(head[i - 4 : i], "big")
            boxes[tag] = data[i + 4 : i - 4 + size]
    if b"CMT1" not in boxes:
        return None
    try:
        Image = pil()
        ex = Image.Exif()
        ex.load(boxes[b"CMT1"])
        for tag, ifd in ((b"CMT2", 0x8769), (b"CMT4", 0x8825)):
            if tag in boxes:
                sub = Image.Exif()
                sub.load(boxes[tag])
                ex[ifd] = {k: v for k, v in sub.items()}
        return ex
    except Exception:  # noqa: BLE001
        return None


def _clean_exif(ex: Any) -> bytes | None:
    """Only the descriptive tags (no strip offsets, sub-IFD pointers or maker notes of the RAW container)."""
    try:
        Image = pil()
        out = Image.Exif()
        for tag in _EXIF_KEEP_IFD0:
            if tag in ex:
                out[tag] = ex[tag]
        sub = ex.get_ifd(0x8769) if 0x8769 in ex else {}
        sub = {k: v for k, v in dict(sub).items() if k not in (0x927C, 0xA005, 0x8769, 0x8825, 0x9286) and not isinstance(v, dict)}
        if sub:
            out[0x8769] = sub
        gps = ex.get_ifd(0x8825) if 0x8825 in ex else {}
        if gps:
            out[0x8825] = dict(gps)
        return out.tobytes() if len(out) else None
    except Exception:  # noqa: BLE001
        return None


def webp_animation(path: Path) -> tuple[list[int], int | None] | None:
    """(frame durations in ms, loop count) of an animated WebP read from its RIFF chunks, or None.

    Pillow reports a WebP frame's duration only after decoding it; the ANMF chunk headers have them all at once.
    """
    import struct

    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
                return None
            durs: list[int] = []
            loop: int | None = None
            while True:
                ch = f.read(8)
                if len(ch) < 8:
                    break
                tag, size = ch[:4], struct.unpack("<I", ch[4:])[0]
                start = f.tell()
                if tag == b"ANIM":
                    body = f.read(6)
                    if len(body) == 6:
                        loop = struct.unpack("<H", body[4:6])[0]
                elif tag == b"ANMF":
                    body = f.read(16)
                    if len(body) < 16:
                        break
                    durs.append(int.from_bytes(body[12:15], "little"))
                f.seek(start + size + (size & 1))
    except OSError:
        return None
    return (durs, loop) if durs else None


def animation_timing(path: Path, im: Any = None) -> tuple[list[int], Any]:
    """Every frame's duration (ms) and the loop count of an animation (GIF, APNG, WebP, AVIF), without decoding
    frames where the container allows it."""
    wp = webp_animation(path)
    if wp is not None:
        return wp
    own = im is None
    im = im if im is not None else _open(path)
    try:
        n = frame_count(im)
        durs = []
        for i in range(n):
            im.seek(i)
            d = im.info.get("duration")
            if d is None and im.format in ("WEBP", "AVIF"):
                im.load()
                d = im.info.get("duration")
            durs.append(int(d or 0))
        im.seek(0)
        return durs, im.info.get("loop")
    finally:
        if own:
            im.close()


def iter_frames(path: Path, indexes: Sequence[int] | None = None) -> Iterator[tuple[int, Any, int]]:
    """Yields (index, RGBA-or-RGB frame copy, duration ms) for animations and multi-page files."""
    im = _open(path)
    try:
        n = frame_count(im)
        wanted = list(range(n)) if indexes is None else list(indexes)
        for i in wanted:
            if i < 0 or i >= n:
                raise UsageError(f"{path.name} has {n} frame(s)")
            im.seek(i)
            im.load()
            dur = int(im.info.get("duration", 0) or 0)
            fr = normalize(im.copy())
            yield i, fr, dur
    finally:
        im.close()


# ── modes, colour, alpha ────────────────────────────────────────────────


def has_alpha(img: Any) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (img.mode == "P" and "transparency" in img.info)


def uses_alpha(img: Any) -> bool:
    """True when some pixel is not fully opaque."""
    if not has_alpha(img):
        return False
    a = img.convert("RGBA").getchannel("A") if img.mode not in ("RGBA", "LA") else img.getchannel("A")
    lo, _hi = a.getextrema()
    return lo < 255


def normalize(img: Any, stretch: bool = False) -> Any:
    """8-bit L, LA, RGB or RGBA. High bit-depth data is scaled by its range (stretched to min..max with stretch)."""
    m = img.mode
    if m in ("RGB", "RGBA", "L", "LA"):
        return img
    if m == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if m == "PA":
        return img.convert("RGBA")
    if m == "1":
        return img.convert("L")
    if m in ("RGBa", "La"):
        return img.convert("RGBA" if m == "RGBa" else "LA")
    if m == "CMYK":
        return cmyk_to_rgb(img)
    if m.startswith("I") or m == "F":
        import numpy as np

        Image = pil()
        a = np.asarray(img).astype(np.float64)
        if a.ndim == 3:
            a = a[..., 0]
        lo, hi = (float(a.min()), float(a.max())) if a.size else (0.0, 1.0)
        if not stretch:
            if m.startswith("I;16") or (m == "I" and lo >= 0 and hi <= 65535):
                top = 65535.0
                for bits in (8, 10, 12, 14):
                    if hi <= (1 << bits) - 1 and m == "I":
                        top = float((1 << bits) - 1)
                        break
                lo, hi = 0.0, top if hi > 255 or m.startswith("I;16") else 255.0
            elif m == "F" and lo >= 0 and hi <= 1.0:
                lo, hi = 0.0, 1.0
        span = (hi - lo) or 1.0
        out = np.clip((a - lo) * (255.0 / span), 0, 255).astype(np.uint8)
        return Image.fromarray(out, "L")
    return img.convert("RGBA" if has_alpha(img) else "RGB")


def cmyk_to_rgb(img: Any) -> Any:
    icc = img.info.get("icc_profile")
    if icc:
        try:
            from PIL import ImageCms

            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            out = ImageCms.profileToProfile(img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
            if out is not None:
                return out
        except Exception:  # noqa: BLE001
            pass
    return img.convert("RGB")


def icc_description(icc: bytes | None) -> str | None:
    if not icc:
        return None
    try:
        from PIL import ImageCms

        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() or "unnamed"
    except Exception:  # noqa: BLE001
        return "unreadable profile"


def is_srgb_profile(icc: bytes | None) -> bool:
    d = (icc_description(icc) or "").lower()
    return not icc or "srgb" in d or "iec61966-2" in d or "iec 61966-2" in d


def to_srgb(img: Any, icc: bytes | None = None) -> tuple[Any, bool]:
    """Converts pixels from their ICC profile to sRGB. Returns (image, converted?)."""
    icc = icc if icc is not None else img.info.get("icc_profile")
    if not icc or is_srgb_profile(icc):
        return img, False
    try:
        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        if img.mode == "CMYK":
            out = ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
        elif img.mode in ("RGB", "RGBA"):
            alpha = img.getchannel("A") if img.mode == "RGBA" else None
            out = ImageCms.profileToProfile(img.convert("RGB"), src, dst, outputMode="RGB")
            if out is not None and alpha is not None:
                out.putalpha(alpha)
        else:
            return img, False
    except Exception:  # noqa: BLE001 — a profile LittleCMS rejects: keep the pixels as they are
        return img, False
    if out is None:
        return img, False
    out.info = {k: v for k, v in img.info.items() if k != "icc_profile"}
    return out, True


def parse_color(value: str | Sequence[int] | None, default: Any = None) -> Any:
    """'#ff8800', '#ff880080', 'red', 'rgb(1,2,3)', 'transparent', or [r,g,b(,a)] → an RGBA tuple."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        vals = [int(v) for v in value]
        if len(vals) == 3:
            vals.append(255)
        if len(vals) != 4:
            raise UsageError(f"bad colour {value}")
        return tuple(vals)
    s = str(value).strip()
    if s.lower() in ("transparent", "none", "clear"):
        return (0, 0, 0, 0)
    from PIL import ImageColor

    if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", s):
        s = "#" + s
    try:
        c = ImageColor.getrgb(s)
    except ValueError:
        raise UsageError(f"bad colour '{value}' (use a name, #rrggbb, #rrggbbaa or rgb(r,g,b))") from None
    return tuple(c) if len(c) == 4 else (*c, 255)


def hex_color(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb[:3])


def checkerboard(size: tuple[int, int], square: int = 12, light: tuple[int, int, int] = (255, 255, 255), dark: tuple[int, int, int] = (214, 214, 214)) -> Any:
    Image = pil()
    w, h = size
    tile = Image.new("RGB", (square * 2, square * 2), light)
    from PIL import ImageDraw

    d = ImageDraw.Draw(tile)
    d.rectangle([square, 0, square * 2 - 1, square - 1], fill=dark)
    d.rectangle([0, square, square - 1, square * 2 - 1], fill=dark)
    bg = Image.new("RGB", (max(w, 1), max(h, 1)))
    bg.paste(tile, (0, 0))
    # Double the filled area each step: log(n) pastes instead of one per tile (80 000 for a 24 MP image).
    cw, ch = square * 2, square * 2
    while cw < w:
        bg.paste(bg.crop((0, 0, cw, ch)), (cw, 0))
        cw *= 2
    while ch < h:
        bg.paste(bg.crop((0, 0, w, ch)), (0, ch))
        ch *= 2
    return bg


def flatten(img: Any, bg: str | Sequence[int] = "white") -> Any:
    """RGB image with alpha composited over a colour or a checkerboard ('checker')."""
    Image = pil()
    img = normalize(img)
    if not has_alpha(img):
        return img.convert("RGB") if img.mode != "RGB" else img
    rgba = img.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] == 255:
        return rgba.convert("RGB")  # an alpha channel that is fully opaque
    if isinstance(bg, str) and bg.lower() in ("checker", "checkerboard"):
        base = checkerboard(rgba.size)
    else:
        c = parse_color(bg, (255, 255, 255, 255))
        base = Image.new("RGB", rgba.size, c[:3])
    base.paste(rgba, (0, 0), rgba)
    return base


def shrink(img: Any, max_edge: int) -> Any:
    """Downscales so the long edge is at most max_edge (never enlarges); fast for huge images (reducing_gap)."""
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    r = max_edge / max(w, h)
    Image = pil()
    src = img if img.mode not in ("P", "1", "I;16", "I;16B", "I;16L", "I;16N", "I", "F") else normalize(img)
    return src.resize((max(1, round(w * r)), max(1, round(h * r))), Image.LANCZOS, reducing_gap=3.0)


def to_view(img: Any, bg: str = "auto", stretch: bool = False) -> tuple[Any, str]:
    """An RGB image ready for a model to look at; also returns how alpha was shown ('' when opaque)."""
    img = normalize(img, stretch=stretch)
    img, _ = to_srgb(img)
    if not has_alpha(img):
        return (img.convert("RGB") if img.mode != "RGB" else img), ""
    if bg == "auto":
        if uses_alpha(img):
            return flatten(img, "checker"), "transparent areas are shown as a grey checkerboard"
        return flatten(img, "white"), ""
    return flatten(img, bg), f"transparent areas are shown on {bg}"


# ── geometry ────────────────────────────────────────────────────────────


def parse_len(v: Any, total: float, name: str = "value") -> float:
    """A length in pixels, or a percentage of `total` ('25%')."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    try:
        if s.endswith("%"):
            return float(s[:-1]) * total / 100.0
        if s.endswith("px"):
            s = s[:-2]
        return float(s)
    except ValueError:
        raise UsageError(f"bad {name} '{v}' (pixels like 120, or a percentage like 25%)") from None


def parse_box(spec: Any, size: tuple[int, int], name: str = "box") -> tuple[int, int, int, int]:
    """'x,y,w,h' (pixels or %), 'WxH+X+Y', or [x,y,w,h] / {x,y,w,h} → (left, top, right, bottom), clamped."""
    W, H = size
    if isinstance(spec, dict):
        vals = [spec.get("x", 0), spec.get("y", 0), spec.get("w", spec.get("width")), spec.get("h", spec.get("height"))]
        if vals[2] is None or vals[3] is None:
            raise UsageError(f"{name} needs x, y, w and h")
    elif isinstance(spec, (list, tuple)):
        vals = list(spec)
    else:
        s = str(spec).strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?%?)x(\d+(?:\.\d+)?%?)([+-]\d+(?:\.\d+)?%?)([+-]\d+(?:\.\d+)?%?)", s)
        if m:
            vals = [m.group(3).lstrip("+"), m.group(4).lstrip("+"), m.group(1), m.group(2)]
        else:
            vals = [t for t in re.split(r"[,\s]+", s) if t]
    if len(vals) != 4:
        raise UsageError(f"bad {name} '{spec}': give x,y,w,h (pixels or %) or WxH+X+Y")
    x = parse_len(vals[0], W, name)
    y = parse_len(vals[1], H, name)
    w = parse_len(vals[2], W, name)
    h = parse_len(vals[3], H, name)
    if w <= 0 or h <= 0:
        raise UsageError(f"{name} '{spec}' has no area")
    l, t = max(0, round(x)), max(0, round(y))
    r, b = min(W, round(x + w)), min(H, round(y + h))
    if r <= l or b <= t:
        raise UsageError(f"{name} '{spec}' lies outside the {W}x{H} image")
    return l, t, r, b


def parse_point(spec: Any, size: tuple[int, int], name: str = "point") -> tuple[float, float]:
    W, H = size
    if isinstance(spec, dict):
        return parse_len(spec.get("x", 0), W, name), parse_len(spec.get("y", 0), H, name)
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        return parse_len(spec[0], W, name), parse_len(spec[1], H, name)
    parts = [t for t in re.split(r"[,\s]+", str(spec).strip()) if t]
    if len(parts) != 2:
        raise UsageError(f"bad {name} '{spec}' (x,y)")
    return parse_len(parts[0], W, name), parse_len(parts[1], H, name)


ANCHORS = {
    "top-left": (0.0, 0.0), "tl": (0.0, 0.0), "nw": (0.0, 0.0), "northwest": (0.0, 0.0),
    "top": (0.5, 0.0), "t": (0.5, 0.0), "n": (0.5, 0.0), "north": (0.5, 0.0), "top-center": (0.5, 0.0),
    "top-right": (1.0, 0.0), "tr": (1.0, 0.0), "ne": (1.0, 0.0), "northeast": (1.0, 0.0),
    "left": (0.0, 0.5), "l": (0.0, 0.5), "w": (0.0, 0.5), "west": (0.0, 0.5),
    "center": (0.5, 0.5), "centre": (0.5, 0.5), "c": (0.5, 0.5), "middle": (0.5, 0.5),
    "right": (1.0, 0.5), "r": (1.0, 0.5), "e": (1.0, 0.5), "east": (1.0, 0.5),
    "bottom-left": (0.0, 1.0), "bl": (0.0, 1.0), "sw": (0.0, 1.0), "southwest": (0.0, 1.0),
    "bottom": (0.5, 1.0), "b": (0.5, 1.0), "s": (0.5, 1.0), "south": (0.5, 1.0), "bottom-center": (0.5, 1.0),
    "bottom-right": (1.0, 1.0), "br": (1.0, 1.0), "se": (1.0, 1.0), "southeast": (1.0, 1.0),
}


def anchor_frac(name: str) -> tuple[float, float]:
    key = str(name).strip().lower().replace("_", "-").replace(" ", "-")
    if key not in ANCHORS:
        raise UsageError(f"bad position '{name}' (top-left, top, top-right, left, center, right, bottom-left, bottom, bottom-right)")
    return ANCHORS[key]


def place(anchor: str, outer: tuple[int, int], inner: tuple[int, int], margin: float = 0) -> tuple[int, int]:
    """Top-left corner that puts `inner` at `anchor` inside `outer`, `margin` pixels from the edges."""
    fx, fy = anchor_frac(anchor)
    x = margin + fx * (outer[0] - inner[0] - 2 * margin)
    y = margin + fy * (outer[1] - inner[1] - 2 * margin)
    return round(x), round(y)


def parse_size(spec: str, size: tuple[int, int] | None = None) -> tuple[int | None, int | None]:
    """'800x600', '800x', 'x600', '800' (width), '50%' (of `size`) → (w, h)."""
    s = str(spec).strip().lower()
    if s.endswith("%") and size:
        f = float(s[:-1]) / 100
        return max(1, round(size[0] * f)), max(1, round(size[1] * f))
    m = re.fullmatch(r"(\d+)?\s*[x×]\s*(\d+)?", s)
    if m and (m.group(1) or m.group(2)):
        return (int(m.group(1)) if m.group(1) else None, int(m.group(2)) if m.group(2) else None)
    if s.isdigit():
        return int(s), None
    raise UsageError(f"bad size '{spec}' (WxH, Wx, xH, W or N%)")


def fit_within(size: tuple[int, int], w: int | None, h: int | None, upscale: bool = False) -> tuple[int, int]:
    W, H = size
    r = min((w / W) if w else float("inf"), (h / H) if h else float("inf"))
    if r == float("inf"):
        return W, H
    if not upscale:
        r = min(r, 1.0)
    return max(1, round(W * r)), max(1, round(H * r))


# ── saving ──────────────────────────────────────────────────────────────


def out_format(fmt: str | None, out: Path | None) -> tuple[str, str]:
    """(Pillow format, extension) from a format name or the output's extension."""
    key = (fmt or (out.suffix if out else "")).lower().lstrip(".")
    if key not in OUTPUT_FORMATS:
        names = ", ".join(sorted({k for k in OUTPUT_FORMATS if k not in ("jpe", "jfif", "hif", "heif", "tiff", "jpeg")}))
        raise UsageError(f"cannot write '{key or '?'}' images; choose one of: {names}")
    return OUTPUT_FORMATS[key]


@dataclass
class SaveOpts:
    quality: int | None = None
    lossless: bool = False
    strip: bool = False
    exif: bytes | None = None
    xmp: bytes | None = None
    icc: bytes | None = None
    dpi: tuple[float, float] | None = None
    bg: Any = "white"
    ico_sizes: Sequence[int] = (16, 24, 32, 48, 64, 128, 256)
    durations: Sequence[int] | int | None = None
    loop: int = 0
    effort: int | None = None  # webp method 0-6 / avif speed 0-10 (lower = smaller, slower)
    progressive: bool = True
    compress_level: int = 6
    colors: int = 256
    dither: bool = True
    tiff_compression: str = "tiff_lzw"
    pdf_page: str = "fit"
    pdf_margin: float = 0.0


def meta_from(info: dict[str, Any], orientation_applied: bool = True) -> dict[str, Any]:
    """EXIF, XMP and ICC from an image's info, with the orientation reset when it was applied to the pixels."""
    exif = info.get("exif")
    if isinstance(exif, str):
        exif = exif.encode("latin-1", "replace")
    if exif and orientation_applied:
        exif = reset_orientation(exif)
    xmp = info.get("xmp") or info.get("XML:com.adobe.xmp")
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8")
    if xmp and orientation_applied:
        xmp = re.sub(rb'(tiff:Orientation(?:="|>))\s*[2-8]', rb"\g<1>1", xmp)
    return {"exif": exif or None, "xmp": xmp or None, "icc": info.get("icc_profile") or None, "dpi": info.get("dpi")}


def reset_orientation(exif: bytes) -> bytes:
    Image = pil()
    try:
        ex = Image.Exif()
        ex.load(exif)
        if ex.get(0x0112, 1) != 1:
            ex[0x0112] = 1
        return ex.tobytes()
    except Exception:  # noqa: BLE001
        return exif


def _exif_bytes_for(exif: bytes | None, size: tuple[int, int]) -> bytes | None:
    """EXIF bytes with the pixel dimensions updated (and no stale embedded thumbnail)."""
    if not exif:
        return None
    Image = pil()
    try:
        ex = Image.Exif()
        ex.load(exif)
        sub = ex.get_ifd(0x8769)
        if sub:
            if 0xA002 in sub:
                sub[0xA002] = size[0]
            if 0xA003 in sub:
                sub[0xA003] = size[1]
        return ex.tobytes()
    except Exception:  # noqa: BLE001
        return exif


def square_pad(img: Any, bg: Any = (0, 0, 0, 0)) -> Any:
    Image = pil()
    w, h = img.size
    if w == h:
        return img
    s = max(w, h)
    base = Image.new("RGBA", (s, s), parse_color(bg, (0, 0, 0, 0)))
    src = img.convert("RGBA")
    base.paste(src, ((s - w) // 2, (s - h) // 2), src)
    return base


def prepare_mode(img: Any, fmt: str, bg: Any = "white") -> Any:
    """Converts a frame to a mode the output format stores (flattening alpha where the format has none)."""
    img = img if img.mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16", "CMYK") else normalize(img)
    if fmt in ("JPEG", "PDF"):
        if img.mode == "CMYK" and fmt == "JPEG":
            return img
        if img.mode == "I;16":
            img = normalize(img)
        return img if img.mode in ("L", "RGB") and not has_alpha(img) else flatten(img, bg)
    if fmt in ("BMP", "PPM"):
        if img.mode in ("1", "L", "RGB"):
            return img
        return flatten(img, bg) if has_alpha(img) else normalize(img).convert("RGB")
    if fmt in ("WEBP", "AVIF", "HEIF", "ICNS", "QOI", "TGA"):
        img = normalize(img)
        return img.convert("RGBA" if has_alpha(img) else "RGB")
    if fmt == "JPEG2000":
        img = normalize(img)
        return img if img.mode in ("L", "LA", "RGB", "RGBA") else img.convert("RGB")
    if fmt == "ICO":
        return square_pad(normalize(img).convert("RGBA"))
    if fmt == "GIF":
        return img if img.mode in ("P", "L", "1") else normalize(img)
    if fmt == "PNG":
        return img if img.mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16") else normalize(img)
    if fmt == "TIFF":
        return img if img.mode in ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16") else normalize(img)
    return normalize(img)


def save_image(frames: Any, out: Path, fmt: str, o: SaveOpts | None = None) -> None:
    """Saves one image or a list of frames/pages in `fmt`, honouring quality, metadata and animation options."""
    o = o or SaveOpts()
    pil()
    frames = list(frames) if isinstance(frames, (list, tuple)) else [frames]
    if not frames:
        raise SkillError("nothing to save")
    if fmt == "PDF":
        from _pdfimg import images_to_pdf

        images_to_pdf([prepare_mode(f, "PDF", o.bg) for f in frames], out, dpi=o.dpi, quality=o.quality, page=o.pdf_page, margin=o.pdf_margin, lossless=o.lossless)
        return
    if fmt == "ICNS":
        src = square_pad(normalize(frames[0]).convert("RGBA"))
        Image = pil()
        write_icns(out, lambda px: src if src.size[0] == px else src.resize((px, px), Image.LANCZOS))
        return
    if fmt == "ICO":
        frames = frames[:1]
    if fmt not in MULTI_FRAME:
        frames = frames[:1]
    prepared = [prepare_mode(f, fmt, o.bg) for f in frames]
    if len(prepared) > 1 and len({f.mode for f in prepared}) > 1 and fmt != "GIF":
        # Animation frames often mix P, RGB and RGBA; encoders need one mode.
        alpha = any(has_alpha(f) for f in prepared)
        target = "RGBA" if alpha and fmt in ALPHA_FORMATS else "RGB"
        prepared = [f if f.mode == target else (flatten(f, o.bg) if target == "RGB" and has_alpha(f) else normalize(f).convert(target)) for f in prepared]
    if fmt == "GIF":
        prepared = gif_frames(prepared, o.colors, o.dither)
    first, rest = prepared[0], prepared[1:]
    kw: dict[str, Any] = {}
    q = o.quality if o.quality is not None else DEFAULT_QUALITY.get(fmt)
    keep = not o.strip
    exif = _exif_bytes_for(o.exif, first.size) if keep and o.exif else None
    if fmt == "JPEG":
        # Progressive and optimised Huffman coding keep every DCT coefficient in memory (about 1 GB for 200 MP):
        # huge images are written baseline, in one streaming pass.
        small = first.size[0] * first.size[1] <= 50_000_000
        kw.update(quality=int(q), optimize=small, progressive=o.progressive and small, subsampling=0 if int(q) >= 90 else 2)
    elif fmt == "WEBP":
        kw.update(quality=int(q), method=4 if o.effort is None else int(o.effort), lossless=o.lossless)
        if o.lossless:
            kw["exact"] = False
    elif fmt == "AVIF":
        # Pillow's libavif path has no identity (RGB) matrix, so 4:4:4 at quality 100 is as close as it gets:
        # near-lossless (a few levels off at most). The caller says so.
        kw.update(quality=100 if o.lossless else int(q), speed=7 if o.effort is None else int(o.effort))
        if o.lossless:
            kw["subsampling"] = "4:4:4"
        if encoder_threads():
            kw["max_threads"] = encoder_threads()
    elif fmt == "HEIF":
        if o.lossless:
            # Truly lossless HEIC needs full chroma and the identity matrix (RGB stored as is), not just quality -1.
            kw.update(quality=-1, chroma=444, matrix_coefficients=0)
        else:
            kw.update(quality=int(q))
    elif fmt == "PNG":
        kw.update(compress_level=o.compress_level, optimize=False)
    elif fmt == "TIFF":
        comp = o.tiff_compression
        if comp == "jpeg" and first.mode not in ("RGB", "L"):
            comp = "tiff_lzw"
        kw["compression"] = comp
        if comp == "jpeg":
            kw["quality"] = int(q or 88)
    elif fmt == "ICO":
        s = max(first.size)
        sizes = sorted({int(x) for x in o.ico_sizes if int(x) <= 256})
        if not sizes:
            raise UsageError("ICO sizes must be 256 or less")
        kw["sizes"] = [(x, x) for x in sizes]
        if s < max(sizes):
            first = first.resize((max(sizes), max(sizes)), pil().LANCZOS)
    elif fmt == "JPEG2000":
        if o.quality is not None and not o.lossless:
            kw.update(quality_mode="dB", quality_layers=[20 + 0.3 * int(o.quality)], irreversible=True)
    if keep:
        if exif and fmt == "TIFF":
            info = _tiff_basic_tags(exif)
            if info is not None:
                kw["tiffinfo"] = info
        elif exif and fmt in ("JPEG", "PNG", "WEBP", "AVIF", "HEIF", "JPEG2000"):
            kw["exif"] = exif
        if o.xmp and fmt in ("JPEG", "PNG", "WEBP", "AVIF", "HEIF", "TIFF"):
            kw["xmp"] = o.xmp
    if o.icc and fmt in ("JPEG", "PNG", "WEBP", "AVIF", "HEIF", "TIFF", "JPEG2000") and not (o.strip and is_srgb_profile(o.icc)):
        if first.mode in ("RGB", "RGBA", "CMYK") or (first.mode in ("L", "LA") and _icc_is_gray(o.icc)):
            kw["icc_profile"] = o.icc
    if o.dpi and fmt in ("JPEG", "PNG", "TIFF", "BMP", "WEBP"):
        kw["dpi"] = (float(o.dpi[0]), float(o.dpi[1]))
    if rest:
        kw["save_all"] = True
        kw["append_images"] = rest
        if fmt in ("GIF", "PNG", "WEBP", "AVIF"):
            d = o.durations
            if d is not None:
                kw["duration"] = list(d) if isinstance(d, (list, tuple)) else int(d)
            kw["loop"] = int(o.loop)
            if fmt == "GIF":
                kw["disposal"] = 2
                kw["optimize"] = False
            if fmt == "PNG":
                kw["disposal"] = 1
                kw["blend"] = 0
    if fmt == "GIF" and "transparency" in first.info:
        kw["transparency"] = first.info["transparency"]
    tmp = out.with_name(f".{out.name}.part")
    try:
        with open(tmp, "w+b") as fh:  # multi-page TIFF reads back what it wrote
            first.save(fh, fmt, **kw)
        os.replace(tmp, out)
    except Exception as e:  # noqa: BLE001 — encoders raise OSError, ValueError, RuntimeError…
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SkillError(f"could not write {out.name} as {fmt}: {e}") from None


#: ICNS entries written: every size macOS asks for, 16 and 32 px at 1x in the classic RLE form (readable by every
#: macOS version), the rest as PNG. (Pillow's own writer leaves out 16x16 and 32x32 at 1x.)
ICNS_ENTRIES = (("is32", 16), ("il32", 32), ("ic11", 32), ("ic12", 64), ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512), ("ic10", 1024))


def _icns_rle(channel: bytes) -> bytes:
    """Apple's packbits variant, literal runs only (valid and simple: a header byte n means n+1 bytes follow)."""
    out = bytearray()
    for i in range(0, len(channel), 128):
        chunk = channel[i : i + 128]
        out.append(len(chunk) - 1)
        out += chunk
    return bytes(out)


def write_icns(out: Path, at: Any) -> None:
    """Writes a macOS .icns from `at(px)`, which returns a square RGBA image of px pixels (rendered per size)."""
    import struct

    blobs: list[bytes] = []
    cache: dict[int, Any] = {}
    for typ, px in ICNS_ENTRIES:
        if px not in cache:
            im = at(px)
            cache[px] = (im if im.mode == "RGBA" else im.convert("RGBA")) if im.size == (px, px) else im.convert("RGBA").resize((px, px), pil().LANCZOS)
        im = cache[px]
        if typ in ("is32", "il32"):
            r, g, b, a = im.split()
            body = b"".join(_icns_rle(c.tobytes()) for c in (r, g, b))
            blobs.append(typ.encode() + struct.pack(">I", 8 + len(body)) + body)
            mask = a.tobytes()
            blobs.append((b"s8mk" if typ == "is32" else b"l8mk") + struct.pack(">I", 8 + len(mask)) + mask)
        else:
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=px <= 256)
            body = buf.getvalue()
            blobs.append(typ.encode() + struct.pack(">I", 8 + len(body)) + body)
    data = b"".join(blobs)
    tmp = out.with_name(f".{out.name}.part")
    tmp.write_bytes(b"icns" + struct.pack(">I", 8 + len(data)) + data)
    os.replace(tmp, out)


#: Baseline TIFF tags worth carrying over from EXIF (libtiff rejects Pillow's EXIF sub-IFD pointers).
_TIFF_KEEP = (0x010E, 0x010F, 0x0110, 0x0131, 0x0132, 0x013B, 0x8298)


def _tiff_basic_tags(exif: bytes) -> Any:
    try:
        from PIL import TiffImagePlugin

        ex = pil().Exif()
        ex.load(exif)
        info = TiffImagePlugin.ImageFileDirectory_v2()
        for tag in _TIFF_KEEP:
            v = ex.get(tag)
            if isinstance(v, str) and v:
                info[tag] = v
        return info if len(info) else None
    except Exception:  # noqa: BLE001
        return None


def _icc_is_gray(icc: bytes) -> bool:
    return len(icc) > 20 and icc[16:20] == b"GRAY"


def gif_frames(frames: Sequence[Any], colors: int = 256, dither: bool = True) -> list[Any]:
    """Palette frames for GIF: one shared palette built from all frames (no flicker), 1-bit transparency kept."""
    Image = pil()
    colors = max(2, min(256, int(colors)))
    if all(f.mode == "P" for f in frames) and len(frames) == 1:
        return list(frames)
    rgba = [normalize(f).convert("RGBA") for f in frames]
    transparent = any(uses_alpha(f) for f in rgba)
    ncol = colors - 1 if transparent else colors
    # A palette from a mosaic of downscaled frames covers the colours of the whole animation.
    sample = []
    for f in rgba[:: max(1, len(rgba) // 24)]:
        t = f.convert("RGB")
        t.thumbnail((160, 160))
        sample.append(t)
    mw = sum(s.size[0] for s in sample)
    mh = max(s.size[1] for s in sample)
    mosaic = Image.new("RGB", (mw, mh))
    x = 0
    for s in sample:
        mosaic.paste(s, (x, 0))
        x += s.size[0]
    if len(rgba) == 1:
        mosaic = rgba[0].convert("RGB")
    pal = mosaic.quantize(colors=ncol, method=Image.Quantize.MEDIANCUT)
    dmode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    out = []
    for f in rgba:
        q = f.convert("RGB").quantize(palette=pal, dither=dmode)
        if transparent:
            # Move every index up by one and use index 0 for transparent pixels.
            import numpy as np

            idx = np.asarray(q, dtype=np.uint16) + 1
            alpha = np.asarray(f.getchannel("A"))
            idx[alpha < 128] = 0
            p = Image.fromarray(idx.astype(np.uint8), "P")
            palette = [0, 0, 0] + (q.getpalette() or [])[: 3 * ncol]
            p.putpalette(palette)
            p.info["transparency"] = 0
            q = p
        out.append(q)
    return out
