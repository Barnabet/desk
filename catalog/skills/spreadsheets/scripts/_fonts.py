"""Finding TrueType fonts for rendering on Windows, macOS and Linux (system folders only; Pillow's built-in font last)."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

# family → candidates, metric-compatible substitutes first
_FAMILIES = {
    "calibri": ["calibri", "carlito", "arial", "liberation sans", "helvetica", "dejavu sans"],
    "arial": ["arial", "liberation sans", "helvetica", "arimo", "dejavu sans"],
    "helvetica": ["helvetica", "arial", "liberation sans", "dejavu sans"],
    "aptos": ["aptos", "calibri", "carlito", "arial", "liberation sans", "dejavu sans"],
    "segoe ui": ["segoe ui", "arial", "liberation sans", "dejavu sans"],
    "verdana": ["verdana", "dejavu sans", "arial"],
    "tahoma": ["tahoma", "verdana", "dejavu sans", "arial"],
    "times new roman": ["times new roman", "liberation serif", "tinos", "times", "dejavu serif"],
    "cambria": ["cambria", "caladea", "times new roman", "liberation serif", "dejavu serif"],
    "georgia": ["georgia", "times new roman", "liberation serif", "dejavu serif"],
    "courier new": ["courier new", "liberation mono", "cousine", "courier", "dejavu sans mono"],
    "consolas": ["consolas", "courier new", "liberation mono", "menlo", "dejavu sans mono"],
}
_SERIF_HINTS = ("times", "serif", "cambria", "georgia", "garamond", "book")
_MONO_HINTS = ("courier", "mono", "consolas", "menlo")

# filename stems for (family, bold, italic)
_STEMS = {
    "arial": ("arial", "arialbd", "ariali", "arialbi"),
    "calibri": ("calibri", "calibrib", "calibrii", "calibriz"),
    "times new roman": ("times", "timesbd", "timesi", "timesbi"),
    "courier new": ("cour", "courbd", "couri", "courbi"),
    "verdana": ("verdana", "verdanab", "verdanai", "verdanaz"),
    "tahoma": ("tahoma", "tahomabd", "tahoma", "tahomabd"),
    "georgia": ("georgia", "georgiab", "georgiai", "georgiaz"),
    "cambria": ("cambria", "cambriab", "cambriai", "cambriaz"),
    "consolas": ("consola", "consolab", "consolai", "consolaz"),
    "segoe ui": ("segoeui", "segoeuib", "segoeuii", "segoeuiz"),
    "carlito": ("carlito-regular", "carlito-bold", "carlito-italic", "carlito-bolditalic"),
    "caladea": ("caladea-regular", "caladea-bold", "caladea-italic", "caladea-bolditalic"),
    "liberation sans": ("liberationsans-regular", "liberationsans-bold", "liberationsans-italic", "liberationsans-bolditalic"),
    "liberation serif": ("liberationserif-regular", "liberationserif-bold", "liberationserif-italic", "liberationserif-bolditalic"),
    "liberation mono": ("liberationmono-regular", "liberationmono-bold", "liberationmono-italic", "liberationmono-bolditalic"),
    "dejavu sans": ("dejavusans", "dejavusans-bold", "dejavusans-oblique", "dejavusans-boldoblique"),
    "dejavu serif": ("dejavuserif", "dejavuserif-bold", "dejavuserif-italic", "dejavuserif-bolditalic"),
    "dejavu sans mono": ("dejavusansmono", "dejavusansmono-bold", "dejavusansmono-oblique", "dejavusansmono-boldoblique"),
}


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:\\Windows"
        dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts", Path.home() / ".local" / "share" / "fonts"]
    extra = os.environ.get("DESK_FONT_DIRS")
    if extra:
        dirs = [Path(p) for p in extra.split(os.pathsep) if p] + dirs
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Lowercase file stem (and 'family style' for macOS names) → path."""
    idx: dict[str, str] = {}
    budget = 6000
    for d in _font_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                low = f.lower()
                if not low.endswith((".ttf", ".otf", ".ttc")):
                    continue
                stem = low.rsplit(".", 1)[0]
                idx.setdefault(stem, os.path.join(root, f))
                budget -= 1
                if budget <= 0:
                    return idx
    return idx


def _find(family: str | None, bold: bool, italic: bool) -> tuple[str | None, str | None]:
    """(font file, candidate family it belongs to)."""
    fam = (family or "calibri").strip().lower()
    cands = list(_FAMILIES.get(fam, [fam]))
    if fam not in _FAMILIES:
        if any(h in fam for h in _MONO_HINTS):
            cands += _FAMILIES["courier new"]
        elif any(h in fam for h in _SERIF_HINTS):
            cands += _FAMILIES["times new roman"]
        else:
            cands += _FAMILIES["calibri"]
    idx = _index()
    k = (1 if bold else 0) + (2 if italic else 0)
    style_word = ["", " bold", " italic", " bold italic"][k]
    for c in cands:
        # macOS style names first ("Times New Roman Bold.ttf"), then Windows/Linux file stems ("timesbd.ttf")
        name = (c + style_word).strip()
        if name in idx:
            return idx[name], c
        if k == 0 and c in idx:
            return idx[c], c
        stems = _STEMS.get(c)
        if stems and stems[k] in idx:
            return idx[stems[k]], c
    # regular face of any candidate (bold is then synthesised)
    for c in cands:
        stems = _STEMS.get(c)
        if stems and stems[0] in idx:
            return idx[stems[0]], c
        if c in idx:
            return idx[c], c
    return None, None


def find_font(family: str | None, bold: bool = False, italic: bool = False) -> str | None:
    """Path of the best available font file for a family and style (a metric-compatible substitute if needed)."""
    return _find(family, bold, italic)[0]


# digit advance width (em) of common Office families, used to size substitutes so text keeps its width
_DIGIT = {"calibri": 0.507, "calibri light": 0.507, "aptos": 0.54, "aptos narrow": 0.45, "arial narrow": 0.456, "cambria": 0.556, "segoe ui": 0.559, "candara": 0.5, "corbel": 0.5, "consolas": 0.55, "tahoma": 0.546, "verdana": 0.636}
SUBSTITUTED: dict[str, str] = {}


@lru_cache(maxsize=256)
def load(family: str | None, px: int, bold: bool = False, italic: bool = False) -> tuple[Any, bool]:
    """(PIL font, needs synthetic bold). Falls back to Pillow's built-in scalable font.

    When a family is missing, a substitute is used (sized to keep roughly the same text widths) and recorded in
    SUBSTITUTED so renderers can say so."""
    from PIL import ImageFont

    path, cand = _find(family, bold, italic)
    fake_bold = False
    fam = (family or "calibri").strip().lower()
    if path is not None:
        substitute = cand != fam and not (fam == "calibri" and cand == "carlito")
        try:
            font = ImageFont.truetype(path, max(6, px))
            if substitute:
                SUBSTITUTED[family or "Calibri"] = (cand or Path(path).stem).title()
                target = _DIGIT.get(fam)
                if target is not None:
                    want = int(target * px) + 0.25
                    size = max(6, px)
                    while size > max(6, px * 0.7) and font.getlength("0") > want:
                        size -= 1
                        font = ImageFont.truetype(path, size)
            if bold:
                name = Path(path).name.lower()
                fake_bold = not ("bold" in name or name.rsplit(".", 1)[0].endswith(("bd", "b", "z", "bi")))
            return font, fake_bold
        except OSError:
            pass
    SUBSTITUTED[family or "Calibri"] = "Pillow's built-in font"
    try:
        return ImageFont.load_default(size=max(6, px)), bold
    except TypeError:  # very old Pillow
        return ImageFont.load_default(), bold
