"""Text and subtitle images for the audio-video skill, drawn with Pillow so they look the same on every platform
(ffmpeg's drawtext and libass depend on how ffmpeg was built). Also font discovery and colour parsing."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from _common import IS_WINDOWS, SkillError, UsageError

# ── fonts ───────────────────────────────────────────────────────────────


def font_dirs() -> list[Path]:
    home = Path.home()
    if IS_WINDOWS:
        dirs = [Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"]
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts", home / ".fonts"]


_REGULAR = ["Arial", "Helvetica", "SegoeUI", "segoeui", "DejaVuSans", "LiberationSans-Regular", "NotoSans-Regular", "Roboto-Regular", "Verdana"]
_BOLD = ["Arial Bold", "arialbd", "SegoeUIBold", "segoeuib", "DejaVuSans-Bold", "LiberationSans-Bold", "NotoSans-Bold", "Roboto-Bold", "Verdana Bold"]


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.lower())


def _index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for d in font_dirs():
        if not d.is_dir():
            continue
        try:
            for f in d.rglob("*"):
                if f.suffix.lower() in (".ttf", ".otf", ".ttc") and f.is_file():
                    idx.setdefault(_norm(f.stem), f)
        except OSError:
            continue
    return idx


_IDX: dict[str, Path] | None = None


def find_font(name: str | None = None, bold: bool = False) -> Path | None:
    """A font file: a path, a font name found in the system font folders, or a sensible sans-serif default."""
    global _IDX
    if name:
        p = Path(name).expanduser()
        if p.is_file():
            return p
    if _IDX is None:
        _IDX = _index()
    if name:
        key = _norm(Path(name).stem)
        want = [key + "bold", key + "bd", key] if bold else [key, key + "regular"]
        for k in want:
            if k in _IDX:
                return _IDX[k]
        hits = sorted((k for k in _IDX if k.startswith(key)), key=len)
        if hits:
            return _IDX[hits[0]]
        raise UsageError(f"font '{name}' not found; pass a .ttf/.otf path")
    for cand in (_BOLD if bold else _REGULAR):
        k = _norm(cand)
        if k in _IDX:
            return _IDX[k]
    return None


# Fonts with wide coverage (CJK, Korean, Arabic, Hebrew, Indic, symbols), tried when the chosen font lacks glyphs.
_FALLBACK = ["Arial Unicode", "ArialUnicodeMS", "Hiragino Sans GB", "AppleSDGothicNeo", "Apple Symbols", "msyh", "YuGothM", "malgun",
             "Nirmala", "seguisym", "NotoSansCJK-Regular", "NotoSansCJKsc-Regular", "NotoSansCJKjp-Regular", "NotoSans-Regular",
             "DejaVuSans", "Arial", "Tahoma"]
_SIGS: dict[tuple[str, str], bytes] = {}
UNDRAWN: set[str] = set()  # characters no available font could draw (callers report them)


def _glyph(path: Path | None, ch: str) -> bytes:
    """A small rendering of `ch`, to compare with the font's 'missing glyph' box."""
    key = (str(path), ch)
    if key not in _SIGS:
        from PIL import Image, ImageDraw

        im = Image.new("L", (48, 48))
        try:
            ImageDraw.Draw(im).text((8, 4), ch, font=load_font(path, 24), fill=255)
        except Exception:  # noqa: BLE001 - an unusable font counts as missing everything
            pass
        _SIGS[key] = im.tobytes()
    return _SIGS[key]


def missing_glyphs(path: Path | None, text: str) -> set[str]:
    """Characters of `text` that the font draws as its 'missing glyph' box."""
    import unicodedata

    chars = {c for c in set(text) if not c.isspace() and unicodedata.category(c)[0] not in "CZ" and unicodedata.category(c) not in ("Mn", "Me")}
    if not chars:
        return set()
    box = _glyph(path, "\U0010fffd")
    return {c for c in chars if _glyph(path, c) == box}


def font_for(text: str, name: str | None = None, bold: bool = False) -> tuple[Path | None, set[str]]:
    """The font to draw `text` with: the requested (or default) font when it has every glyph, otherwise the first
    system font that does (CJK, Arabic, … in a Latin font). Returns (font file or None for Pillow's font, characters
    that stay undrawable)."""
    first = find_font(name, bold)
    lost = missing_glyphs(first, text)
    if not lost:
        return first, set()
    best, best_lost = first, lost
    assert _IDX is not None
    for cand in _FALLBACK:
        path = _IDX.get(_norm(cand))
        if path is None or path == first:
            continue
        left = missing_glyphs(path, "".join(best_lost))
        if len(left) < len(best_lost):
            left = missing_glyphs(path, text)
            if len(left) < len(best_lost):
                best, best_lost = path, left
        if not best_lost:
            break
    return best, best_lost


def ui_font(size: int, text: str = "", bold: bool = False) -> Any:
    """A font for labels on sheets and plots: a system sans-serif that can draw `text` (file names in any script),
    or Pillow's built-in font when there is none."""
    try:
        path, _ = font_for(text, None, bold)
    except Exception:  # noqa: BLE001 - labels must never fail a render
        path = None
    try:
        return load_font(path, size)
    except SkillError:
        return load_font(None, size)


def load_font(path: Path | None, size: int) -> Any:
    from PIL import ImageFont

    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError as e:
            raise SkillError(f"cannot load font {path}: {e}") from None
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ── colours ─────────────────────────────────────────────────────────────


def parse_color(value: str, default_alpha: int = 255) -> tuple[int, int, int, int]:
    """'white', '#fff', '#RRGGBB', '#RRGGBBAA', 'black@0.5' → RGBA."""
    from PIL import ImageColor

    v = value.strip()
    alpha = None
    if "@" in v:
        v, a = v.split("@", 1)
        try:
            alpha = int(round(max(0.0, min(1.0, float(a))) * 255))
        except ValueError:
            raise UsageError(f"bad colour '{value}'") from None
    if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", v):
        v = "#" + v
    try:
        c = ImageColor.getcolor(v, "RGBA")
    except ValueError:
        raise UsageError(f"bad colour '{value}' (use a name, #RRGGBB, #RRGGBBAA or name@0.5)") from None
    r, g, b, a2 = c  # type: ignore[misc]
    if alpha is None:
        alpha = a2 if len(v.lstrip("#")) == 8 else default_alpha
    return (r, g, b, alpha)


# ── text images ─────────────────────────────────────────────────────────


def wrap_to_width(text: str, font: Any, max_w: int) -> str:
    out_lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            cand = f"{line} {w}".strip() if line else w
            if line and font.getlength(cand) > max_w:
                out_lines.append(line)
                line = w
            else:
                line = cand
        out_lines.append(line)
    return "\n".join(out_lines)


def render_text(text: str, size: int, color: str = "white", font: str | None = None, bold: bool = True, stroke: float | None = None, stroke_color: str = "black",
                box: bool = False, box_color: str = "black@0.55", max_width: int | None = None, align: str = "center", shadow: bool = False) -> Any:
    """Text as an RGBA image, tightly cropped (with padding when there is a box)."""
    from PIL import Image, ImageDraw

    path, lost = font_for(text, font, bold)
    UNDRAWN.update(lost)
    fnt = load_font(path, max(6, size))
    if max_width:
        text = wrap_to_width(text, fnt, max_width)
    sw = int(round(stroke if stroke is not None else (0 if box else max(1, size / 14))))
    spacing = max(2, size // 5)
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    l, t, r, b = probe.multiline_textbbox((0, 0), text, font=fnt, spacing=spacing, align=align, stroke_width=sw)
    l, t, r, b = int(l), int(t), int(r + 0.999), int(b + 0.999)  # Pillow may return fractional boxes
    pad = int(size * 0.35) if box else sw + 2
    w, h = (r - l) + 2 * pad, (b - t) + 2 * pad
    img = Image.new("RGBA", (max(2, w), max(2, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if box:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=max(2, size // 6), fill=parse_color(box_color))
    if shadow:
        off = max(1, size // 18)
        d.multiline_text((pad - l + off, pad - t + off), text, font=fnt, fill=(0, 0, 0, 160), spacing=spacing, align=align)
    d.multiline_text((pad - l, pad - t), text, font=fnt, fill=parse_color(color), spacing=spacing, align=align,
                     stroke_width=sw, stroke_fill=parse_color(stroke_color) if sw else None)
    return img


def place(position: str, W: int, H: int, w: int, h: int, margin: int) -> tuple[int, int]:
    """Top-left corner for an overlay of w×h on a W×H frame at a named position or 'x,y' (pixels or %)."""
    p = position.strip().lower().replace("_", "-").replace(" ", "-")
    named = {
        "top-left": (margin, margin), "top": ((W - w) // 2, margin), "top-right": (W - w - margin, margin),
        "left": (margin, (H - h) // 2), "center": ((W - w) // 2, (H - h) // 2), "centre": ((W - w) // 2, (H - h) // 2), "right": (W - w - margin, (H - h) // 2),
        "bottom-left": (margin, H - h - margin), "bottom": ((W - w) // 2, H - h - margin), "bottom-right": (W - w - margin, H - h - margin),
    }
    if p in named:
        return named[p]
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(%?)\s*[,x:]\s*(-?\d+(?:\.\d+)?)(%?)", p)
    if not m:
        raise UsageError(f"bad position '{position}' (use top, bottom, center, top-left … bottom-right, or x,y in px or %)")
    x = float(m.group(1)) * (W / 100 if m.group(2) else 1)
    y = float(m.group(3)) * (H / 100 if m.group(4) else 1)
    return int(round(x)), int(round(y))


def size_px(value: Any, ref: int, default: int) -> int:
    """'48', 48, '5%' (of ref) → pixels."""
    if value is None or value == "":
        return default
    s = str(value).strip()
    try:
        if s.endswith("%"):
            return max(1, int(round(float(s[:-1]) / 100 * ref)))
        return max(1, int(round(float(s))))
    except ValueError:
        raise UsageError(f"bad size '{value}' (pixels like 48, or a percentage like 5%)") from None


# ── subtitle overlay stream (when ffmpeg has no libass) ─────────────────


def cue_images(cues: Sequence[Any], W: int, H: int, outdir: Path, size: int | None = None, color: str = "white", font: str | None = None,
               box: bool = False, margin: int | None = None, position: str = "bottom", stroke_color: str = "black") -> Path:
    """Renders each cue on a transparent W×H canvas and writes an ffconcat list timing them; returns the list path."""
    from PIL import Image

    from _subs import ALIGN_NAMES, strip_tags

    outdir.mkdir(parents=True, exist_ok=True)
    fs = size or max(14, round(H * 0.055))
    mg = margin if margin is not None else max(8, round(H * 0.05))
    blank = outdir / "blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    entries: list[tuple[Path, float]] = []
    t = 0.0
    for i, c in enumerate(sorted(cues, key=lambda c: c.start)):
        text = strip_tags(c.text).strip()
        if not text or c.end <= t:
            continue
        start = max(c.start, t)
        if start > t + 1e-4:
            entries.append((blank, start - t))
        img = render_text(text, fs, color=color, font=font, bold=True, box=box, max_width=int(W * 0.9), stroke_color=stroke_color)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        where = ALIGN_NAMES.get(getattr(c, "align", None) or 2) if getattr(c, "align", None) not in (None, 2) else position  # {\an8} and the like
        x, y = place(where, W, H, img.size[0], img.size[1], mg)
        canvas.alpha_composite(img, (max(0, x), max(0, y)))
        f = outdir / f"cue{i:05d}.png"
        canvas.save(f, compress_level=3)
        entries.append((f, c.end - start))
        t = c.end
    entries.append((blank, 1.0))
    lst = outdir / "cues.ffconcat"
    lines = ["ffconcat version 1.0"]
    for f, d in entries:
        lines.append(f"file '{f.name}'")
        lines.append(f"duration {max(d, 0.001):.3f}")
    lines.append(f"file '{blank.name}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst
