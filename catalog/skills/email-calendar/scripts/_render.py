"""Shared rendering and converter helpers for Desk's first-party file skills.

The source of truth is catalog/shared/_render.py; `pnpm catalog:sync` copies it into skills (edit it there). Heavy
libraries (pypdfium2, Pillow, typst, pypandoc, imageio_ffmpeg) are imported lazily inside the functions that need
them, so a skill only needs the packages of the helpers it calls. Works on Windows, macOS and Linux.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import IS_WINDOWS, SkillError, find_tool, parse_ranges, pool_map, run_tool

#: Long edge, in pixels, of images meant for a model to look at (larger ones are downscaled by the model API anyway).
VISION_EDGE = 1568
VIEW_HINT = "Look at them with view_image."


# ── LibreOffice (optional, exact Office rendering) ──────────────────────


def _soffice_candidates() -> list[str]:
    if IS_WINDOWS:
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)")]
        return [str(Path(r) / "LibreOffice" / "program" / "soffice.exe") for r in roots if r]
    if sys.platform == "darwin":
        return ["/Applications/LibreOffice.app/Contents/MacOS/soffice", str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice")]
    return ["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice", "/snap/bin/libreoffice"]  # portable-ok: Linux only


def find_soffice() -> str | None:
    """LibreOffice's soffice, if installed. DESK_SOFFICE overrides it; DESK_SOFFICE=none disables it."""
    return find_tool(["soffice", "libreoffice"], env_var="DESK_SOFFICE", candidates=_soffice_candidates())


def office_convert(src: str | os.PathLike[str], fmt: str = "pdf", timeout: float = 240, soffice: str | None = None) -> Path:
    """Converts a document with headless LibreOffice into a fresh temp folder; returns the produced file.

    `fmt` is a LibreOffice --convert-to target such as 'pdf', 'docx', 'xlsx', 'pptx', 'odt' or 'pdf:writer_pdf_Export'.
    The caller moves or copies the result where it belongs: the temp folder is deleted when the process exits.
    Raises SkillError when LibreOffice is missing or fails.

    Calls from several threads run one at a time: each soffice takes hundreds of MB. The shared profile is released
    after each call, and a private profile (another process held the shared one) is deleted.
    """
    exe = soffice or find_soffice()
    if not exe:
        raise SkillError("LibreOffice is not installed (set DESK_SOFFICE to its soffice path if it is)")
    src = Path(src).resolve()
    outdir = Path(tempfile.mkdtemp(prefix="desk-lo-out-"))
    atexit.register(shutil.rmtree, outdir, True)
    with _LO_RUN:
        profile, lock = _lo_profile()
        args = [
            exe,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--convert-to",
            fmt,
            "--outdir",
            str(outdir),
            str(src),
        ]
        try:
            run_tool(args, timeout=timeout)
        finally:
            _release_profile(profile, lock)
    ext = fmt.split(":", 1)[0]
    produced = sorted(outdir.glob(f"*.{ext}"))
    if not produced:
        raise SkillError(f"LibreOffice produced no .{ext} from {src.name} (the file may be damaged or password-protected)")
    return produced[0]


#: One LibreOffice conversion at a time in this process (threads wait their turn).
_LO_RUN = threading.Lock()


def _lo_profile() -> tuple[Path, Path | None]:
    """(profile, lock) for one conversion. The shared profile in the temp area is reused when free (fast starts) and
    comes with the lock file this call holds; when another process holds it, a private profile comes with None."""
    base = Path(tempfile.gettempdir()) / "desk-lo-profile"
    lock = base.with_suffix(".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        _PROFILE_LOCKS.append(lock)
        return base, lock
    except FileExistsError:
        try:
            # A lock older than 10 minutes is stale (a crashed run).
            if time.time() - lock.stat().st_mtime > 600:
                lock.unlink(missing_ok=True)
                return _lo_profile()
        except OSError:
            pass
        return Path(tempfile.mkdtemp(prefix="desk-lo-profile-")), None


def _release_profile(profile: Path, lock: Path | None) -> None:
    """Frees the shared profile's lock, or deletes a private profile (it also lists the documents converted)."""
    if lock is None:
        shutil.rmtree(profile, ignore_errors=True)
        return
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass
    if lock in _PROFILE_LOCKS:
        _PROFILE_LOCKS.remove(lock)


_PROFILE_LOCKS: list[Path] = []


def _release_profile_locks() -> None:
    """At exit: frees a lock a conversion still holds (one interrupted without its finally block)."""
    for lock in _PROFILE_LOCKS:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_release_profile_locks)


# ── PDF → PNG (pypdfium2) ───────────────────────────────────────────────


def _render_page(job: tuple[str, int, float, str, str | None, int]) -> str:
    import pypdfium2 as pdfium

    pdf_path, index, scale, out, password, max_edge = job
    pdf = pdfium.PdfDocument(pdf_path, password=password)
    try:
        with warnings.catch_warnings():  # pypdfium2 warns about documents without forms: noise for the agent
            warnings.simplefilter("ignore")
            pdf.init_forms()  # without this, form fields and their values are not drawn
        page = pdf[index]
        w, h = page.get_size()
        if max_edge:
            scale = min(scale, max_edge / max(w, h)) if scale else max_edge / max(w, h)
        img = page.render(scale=scale, may_draw_forms=True).to_pil()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, optimize=False)
        return out
    finally:
        pdf.close()


def pdf_page_count(pdf: str | os.PathLike[str], password: str | None = None) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf), password=password)
    try:
        return len(doc)
    finally:
        doc.close()


def pdf_to_pngs(
    pdf: str | os.PathLike[str],
    outdir: str | os.PathLike[str],
    pages: str | Sequence[int] | None = None,
    dpi: float | None = None,
    max_edge: int | None = VISION_EDGE,
    prefix: str = "page",
    password: str | None = None,
    workers: int | None = None,
) -> list[Path]:
    """Renders PDF pages (1-based spec like '1-3,7') to PNG files; parallel for many pages.

    With `dpi`, pages render at that resolution, still capped at `max_edge` pixels on the long side (pass
    max_edge=0 for no cap). Without `dpi`, pages fill `max_edge`.
    """
    pdf = Path(pdf)
    count = pdf_page_count(pdf, password)
    numbers = parse_ranges(pages, count) if (pages is None or isinstance(pages, str)) else list(pages)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    width = max(len(str(count)), 2)
    scale = (dpi / 72.0) if dpi else 0.0
    jobs = [(str(pdf), n - 1, scale, str(out / f"{prefix}-{n:0{width}d}.png"), password, max_edge or 0) for n in numbers]
    many = len(jobs) >= 6
    results = pool_map(_render_page, jobs, workers=workers if many else 1)
    return [Path(r) for r in results]


# ── images for a model to look at ───────────────────────────────────────


def fit_edge(img: Any, max_edge: int = VISION_EDGE) -> Any:
    """A copy of a PIL image whose long side is at most max_edge (never upscaled)."""
    from PIL import Image

    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    ratio = max_edge / max(w, h)
    return img.resize((max(1, round(w * ratio)), max(1, round(h * ratio))), Image.LANCZOS)


# Fonts with wide Unicode coverage (CJK, Cyrillic, Greek, Arabic, Indic), tried in order for non-ASCII labels.
_UNICODE_FONTS = [
    "Arial Unicode.ttf", "Hiragino Sans GB.ttc", "PingFang.ttc", "AppleSDGothicNeo.ttc",  # macOS
    "msyh.ttc", "YuGothM.ttc", "malgun.ttf", "Nirmala.ttf", "seguisym.ttf", "arial.ttf",  # Windows
    "NotoSansCJK-Regular.ttc", "NotoSans-Regular.ttf", "DejaVuSans.ttf",  # Linux
]
_FONT_CACHE: dict[tuple[int, bool], Any] = {}


def _font_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        win = Path(os.environ.get("WINDIR") or "C:\\Windows")
        local = os.environ.get("LOCALAPPDATA")
        return [win / "Fonts"] + ([Path(local) / "Microsoft" / "Windows" / "Fonts"] if local else [])
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts", home / ".fonts"]


def _unicode_font_file() -> Path | None:
    for d in _font_dirs():
        if not d.is_dir():
            continue
        for name in _UNICODE_FONTS:
            direct = d / name
            if direct.is_file():
                return direct
            if sys.platform.startswith("linux"):
                hit = next(d.rglob(name), None)
                if hit:
                    return hit
    return None


def unicode_font_file() -> Path | None:
    """A system font file with wide Unicode coverage (CJK, Cyrillic, Greek, Arabic, Indic), or None: for matplotlib or
    Pillow text that is not plain ASCII."""
    return _unicode_font_file()


def _font(size: int, text: str | None = None) -> Any:
    """Pillow's built-in font, or a system font with wide Unicode coverage when `text` is not plain ASCII."""
    from PIL import ImageFont

    wide = bool(text) and not text.isascii()
    key = (size, wide)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    if wide:
        f = _unicode_font_file()
        if f:
            try:
                font = ImageFont.truetype(str(f), size)
            except OSError:
                font = None
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _fit_text(draw: Any, text: str, font: Any, width: int) -> str:
    """`text` shortened with an ellipsis so it fits in `width` pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def contact_sheet(
    images: Sequence[str | os.PathLike[str]],
    out: str | os.PathLike[str],
    labels: Sequence[str] | None = None,
    cols: int | None = None,
    cell: int = 360,
    title: str | None = None,
    max_edge: int = VISION_EDGE * 2,
    compress_level: int = 6,
) -> Path:
    """A labelled grid of thumbnails (pages, slides, frames), saved as PNG (`compress_level` 1 saves faster, larger).

    `cell` is the long edge of a thumbnail. Cells take the images' typical shape (tall for pages, wide for slides and
    frames), so the sheet wastes no space; thumbnails are decoded at reduced size where the format allows (JPEG).
    """
    from PIL import Image, ImageDraw

    if not images:
        raise SkillError("no images for the contact sheet")
    n = len(images)
    cols = cols or min(n, 4 if n > 6 else 3 if n > 2 else n)
    rows = (n + cols - 1) // cols
    pad, label_h = 12, 22 if labels is not None else 0
    title_h = 34 if title else 0
    thumbs = []
    for p in images:
        with Image.open(p) as im:
            im.draft("RGB", (cell, cell))  # JPEG: decode at 1/2, 1/4 or 1/8 scale; a no-op for other formats
            im = im.convert("RGB")
            if max(im.size) > cell:  # already small enough (e.g. pre-rendered thumbnails): no resampling
                im.thumbnail((cell, cell), Image.LANCZOS, reducing_gap=2.0)
            thumbs.append(im if im.mode == "RGB" else im.convert("RGB"))
    aspects = sorted(t.size[0] / max(1, t.size[1]) for t in thumbs)
    aspect = aspects[len(aspects) // 2]
    cell_w = cell if aspect >= 1 else max(1, max(t.size[0] for t in thumbs))
    cell_h = max(t.size[1] for t in thumbs)
    # Text is drawn larger when the sheet is bigger than what a model sees (VISION_EDGE on the long side; model APIs
    # scale larger images down), so labels still read at about 14 px there.
    size = lambda: (pad + cols * (cell_w + pad), title_h + pad + rows * (cell_h + label_h + pad))  # noqa: E731
    grow = min(4.0, max(1.0, max(size()) / VISION_EDGE))
    label_px, title_px = round(14 * grow), round(20 * grow)
    if labels is not None:
        label_h = round(22 * grow)
    if title:
        title_h = round(34 * grow)
    sheet = Image.new("RGB", size(), "white")
    draw = ImageDraw.Draw(sheet)
    if title:
        tf = _font(title_px, title)
        draw.text((pad, round(8 * grow)), _fit_text(draw, title, tf, sheet.size[0] - 2 * pad), fill="black", font=tf)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad) + (cell_w - t.size[0]) // 2
        y = title_h + pad + r * (cell_h + label_h + pad)
        sheet.paste(t, (x, y))
        draw.rectangle([x - 1, y - 1, x + t.size[0], y + t.size[1]], outline="#bbbbbb")
        if labels is not None and i < len(labels):
            label = str(labels[i])
            lf = _font(label_px, label)
            draw.text((pad + c * (cell_w + pad), y + cell_h + 4), _fit_text(draw, label, lf, cell_w), fill="#333333", font=lf)
    sheet = fit_edge(sheet, max_edge)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, compress_level=compress_level)
    return out


def announce(paths: Iterable[str | os.PathLike[str]], note: str | None = None) -> None:
    """Prints rendered image paths for the agent, with the view_image hint."""
    paths = [str(p) for p in paths]
    for p in paths:
        print(p)
    if note:
        print(note)
    if paths:
        print(VIEW_HINT)


# ── Typst (bundled compiler) ────────────────────────────────────────────


def typst_compile(source: str | os.PathLike[str], fmt: str = "pdf", ppi: float = 144.0, root: str | os.PathLike[str] | None = None, font_paths: Sequence[str] = (), sys_inputs: dict[str, str] | None = None) -> list[bytes]:
    """Compiles a .typ file (or Typst markup given as a string) to PDF, PNG or SVG; returns one bytes per output page.

    PDF output is a single item. Raises SkillError with Typst's message when compilation fails.
    """
    import typst

    tmp: Path | None = None
    tmpdir: Path | None = None
    is_file = isinstance(source, os.PathLike) or (isinstance(source, str) and "\n" not in source and source.endswith(".typ") and Path(source).exists())
    if is_file:
        src = Path(source)
    else:
        if root:
            d = Path(root)
        else:
            d = tmpdir = Path(tempfile.mkdtemp(prefix="desk-typst-"))
        tmp = d / f".desk-{os.getpid()}-{time.time_ns()}.typ"
        tmp.write_text(str(source), encoding="utf-8")
        src = tmp
    kwargs: dict[str, Any] = {"format": fmt}
    if fmt == "png":
        kwargs["ppi"] = float(ppi)
    if root:
        kwargs["root"] = str(root)
    if font_paths:
        kwargs["font_paths"] = [str(f) for f in font_paths]
    if sys_inputs:
        kwargs["sys_inputs"] = sys_inputs
    try:
        result = typst.compile(str(src), **kwargs)
    except Exception as e:  # typst.TypstError carries the diagnostics
        raise SkillError(f"Typst could not compile the document: {e}") from e
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
    if isinstance(result, (bytes, bytearray)):
        return [bytes(result)]
    return [bytes(p) for p in result]


def typst_string(text: str) -> str:
    """A Typst string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def typst_escape(text: str) -> str:
    """Escapes text for Typst markup mode."""
    out = []
    for ch in text:
        if ch in '\\#*_`$<>@[]~/"\'=-+.':
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


# ── bundled binaries ────────────────────────────────────────────────────


def pandoc_path() -> str:
    """The pandoc binary shipped in the pypandoc-binary wheel (a pinned version), else DESK_PANDOC or PATH."""
    override = os.environ.get("DESK_PANDOC")
    if override and Path(override).exists():
        return override
    try:
        import pypandoc

        bundled = Path(pypandoc.__file__).parent / "files" / ("pandoc.exe" if IS_WINDOWS else "pandoc")
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    found = find_tool("pandoc")
    if not found:
        raise SkillError("pandoc is not available in this skill's runtime")
    return found


def run_pandoc(args: Sequence[str], input: bytes | None = None, timeout: float = 300, cwd: str | os.PathLike[str] | None = None) -> bytes:
    """Runs the bundled pandoc; returns stdout."""
    return run_tool([pandoc_path(), *args], input=input, timeout=timeout, cwd=cwd).stdout


def ffmpeg_path() -> str:
    """The ffmpeg binary from DESK_FFMPEG, else the imageio-ffmpeg wheel (a pinned static build), else PATH."""
    override = os.environ.get("DESK_FFMPEG")
    if override and Path(override).exists():
        return override
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — fall back to a system ffmpeg
        found = find_tool("ffmpeg")
        if not found:
            raise SkillError("ffmpeg is not available in this skill's runtime") from None
        return found


def tool_version(exe: str) -> str:
    try:
        r = run_tool([exe, "--version"], timeout=30, check=False)
        return (r.stdout or r.stderr).decode("utf-8", "replace").splitlines()[0].strip()
    except SkillError:
        return "unknown"


def copy_to(src: str | os.PathLike[str], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest
