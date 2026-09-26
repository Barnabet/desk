"""Fonts for drawing and measuring slides with Typst, on any OS.

PowerPoint files name fonts (Calibri, Aptos, Segoe UI…) that a machine may not have. For each requested family this
module picks what Typst should use: the family itself when installed, else a metric-compatible or look-alike
substitute, else a generic sans/serif/mono stack. When the substitute is wider or narrower than the requested font,
a small tracking correction keeps line breaks close to PowerPoint's. Line pitch per font follows the font's own
metrics (PowerPoint's "single" spacing), from a table of common families.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

SANS = ["Arial", "Helvetica", "Liberation Sans", "Arimo", "Helvetica Neue", "DejaVu Sans", "Noto Sans", "Segoe UI"]
SERIF = ["Times New Roman", "Times", "Liberation Serif", "Tinos", "Georgia", "DejaVu Serif", "Libertinus Serif"]
MONO = ["Courier New", "Consolas", "Menlo", "Liberation Mono", "Cousine", "DejaVu Sans Mono"]
# Appended to every stack so CJK, symbols and emoji still draw (Typst falls back per character).
EXTRA = [
    "PingFang SC", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Microsoft YaHei", "Yu Gothic", "Meiryo", "Malgun Gothic",
    "Apple SD Gothic Neo", "Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC", "Arial Unicode MS",
    "Apple Symbols", "Segoe UI Symbol", "Noto Sans Symbols", "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji",
    "Noto Sans Hebrew", "Arial Hebrew", "Geeza Pro", "Noto Sans Arabic", "Kohinoor Devanagari", "Noto Sans Devanagari",
]

SUBSTITUTES: dict[str, list[str]] = {
    "calibri": ["Carlito"], "calibri light": ["Carlito"], "cambria": ["Caladea"], "arial": ["Liberation Sans", "Arimo", "Helvetica"],
    "helvetica": ["Arial", "Liberation Sans"], "helvetica neue": ["Helvetica", "Arial"], "times new roman": ["Liberation Serif", "Tinos", "Times"],
    "times": ["Times New Roman", "Liberation Serif"], "courier new": ["Liberation Mono", "Cousine", "Courier"], "courier": ["Courier New", "Liberation Mono"],
    "segoe ui": ["Helvetica Neue", "Arial"], "segoe ui light": ["Helvetica Neue", "Arial"], "segoe ui semibold": ["Helvetica Neue", "Arial"],
    "aptos": ["Helvetica Neue", "Arial"], "aptos display": ["Helvetica Neue", "Arial"], "aptos narrow": ["Arial Narrow", "Helvetica Neue"],
    "century gothic": ["Avenir", "Futura", "Verdana"], "gill sans mt": ["Gill Sans", "Helvetica Neue"], "franklin gothic book": ["Helvetica Neue", "Arial"],
    "franklin gothic medium": ["Helvetica Neue", "Arial"], "tw cen mt": ["Futura", "Avenir"], "consolas": ["Menlo", "Courier New"],
    "lucida console": ["Menlo", "Courier New"], "garamond": ["EB Garamond", "Georgia"], "book antiqua": ["Palatino", "Georgia"],
    "palatino linotype": ["Palatino", "Georgia"], "constantia": ["Georgia"], "candara": ["Trebuchet MS", "Helvetica Neue"],
    "corbel": ["Helvetica Neue", "Arial"], "open sans": ["Helvetica Neue", "Arial"], "roboto": ["Helvetica Neue", "Arial"],
    "lato": ["Helvetica Neue", "Arial"], "montserrat": ["Avenir Next", "Verdana"], "source sans pro": ["Helvetica Neue", "Arial"],
    "arial narrow": ["Helvetica Neue", "Arial"], "meiryo": ["Hiragino Sans"], "ms gothic": ["Hiragino Sans"],
    "ms pgothic": ["Hiragino Sans"], "ms mincho": ["Hiragino Mincho ProN"], "yu gothic": ["Hiragino Sans"], "microsoft yahei": ["PingFang SC"],
    "simsun": ["Songti SC", "PingFang SC"], "malgun gothic": ["Apple SD Gothic Neo"], "wingdings": ["Apple Symbols", "Segoe UI Symbol"],
    "symbol": ["Apple Symbols", "Segoe UI Symbol"],
}
SERIF_HINTS = ("times", "georgia", "cambria", "garamond", "palatino", "antiqua", "serif", "constantia", "baskerville", "caslon", "bodoni", "didot", "minion", "rockwell", "mincho", "songti", "libertinus", "charter", "merriweather", "playfair")
MONO_HINTS = ("mono", "courier", "consolas", "menlo", "monaco", "code", "lucida console", "inconsolata")

# Average advance width of text (em) — for tracking corrections when a font is substituted.
AVG_WIDTH = {
    "arial": 0.50, "helvetica": 0.50, "helvetica neue": 0.51, "liberation sans": 0.50, "arimo": 0.50, "calibri": 0.455, "calibri light": 0.45,
    "carlito": 0.455, "aptos": 0.48, "aptos display": 0.47, "segoe ui": 0.49, "segoe ui light": 0.48, "verdana": 0.58, "tahoma": 0.49,
    "trebuchet ms": 0.48, "century gothic": 0.56, "gill sans": 0.46, "gill sans mt": 0.46, "futura": 0.50, "avenir": 0.51, "avenir next": 0.52,
    "candara": 0.46, "corbel": 0.45, "franklin gothic book": 0.47, "franklin gothic medium": 0.48, "arial narrow": 0.41, "arial black": 0.62,
    "open sans": 0.54, "roboto": 0.49, "lato": 0.48, "montserrat": 0.56, "source sans pro": 0.46, "impact": 0.46,
    "times new roman": 0.44, "times": 0.44, "liberation serif": 0.44, "georgia": 0.50, "cambria": 0.47, "caladea": 0.47, "garamond": 0.42,
    "palatino": 0.48, "book antiqua": 0.48, "palatino linotype": 0.48, "constantia": 0.49, "libertinus serif": 0.44,
    "courier new": 0.60, "courier": 0.60, "consolas": 0.55, "menlo": 0.60, "liberation mono": 0.60, "dejavu sans": 0.55, "dejavu sans mono": 0.60,
}
# PowerPoint "single" line pitch as a multiple of the font size (hhea ascender + descender + line gap).
LINE_FACTOR = {
    "arial": 1.15, "helvetica": 1.15, "helvetica neue": 1.19, "calibri": 1.22, "calibri light": 1.22, "carlito": 1.22, "aptos": 1.2, "aptos display": 1.2,
    "segoe ui": 1.33, "verdana": 1.215, "tahoma": 1.207, "trebuchet ms": 1.16, "century gothic": 1.227, "georgia": 1.136, "cambria": 1.172,
    "times new roman": 1.15, "times": 1.15, "courier new": 1.133, "consolas": 1.17, "menlo": 1.16, "arial black": 1.41, "impact": 1.22,
    "garamond": 1.12, "gill sans mt": 1.15, "franklin gothic book": 1.13, "corbel": 1.22, "candara": 1.22, "constantia": 1.22,
}


def _font_dirs() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        win = os.environ.get("WINDIR", "C:\\Windows")
        dirs = [Path(win) / "Fonts"]
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"), home / "Library" / "Fonts"]  # portable-ok: macOS only
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local" / "share" / "fonts", home / ".fonts"]  # portable-ok: Linux only


_FAMILIES: set[str] | None = None


def font_signature() -> str:
    """Changes when fonts are installed or removed (it keys cached built-in renders)."""
    sig = hashlib.sha1()
    for d in _font_dirs():
        try:
            sig.update(f"{d}:{d.stat().st_mtime_ns}".encode())
        except OSError:
            pass
    return sig.hexdigest()[:16]


def families() -> set[str]:
    """Lower-cased font families Typst can use (system + embedded), cached on disk per font-folder state."""
    global _FAMILIES
    if _FAMILIES is not None:
        return _FAMILIES
    cache = Path(tempfile.gettempdir()) / f"desk-typst-fonts-{font_signature()}.json"
    try:
        _FAMILIES = set(json.loads(cache.read_text(encoding="utf-8")))
        return _FAMILIES
    except (OSError, ValueError):
        pass
    import typst

    fams = sorted({f.lower() for f in typst.Fonts().families()})
    _FAMILIES = set(fams)
    try:
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(fams), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return _FAMILIES


def generic_of(name: str) -> str:
    n = name.lower()
    if any(h in n for h in MONO_HINTS):
        return "mono"
    if any(h in n for h in SERIF_HINTS):
        return "serif"
    return "sans"


WEIGHT_WORDS = [("extra bold", 800), ("ultra bold", 800), ("extrabold", 800), ("semibold", 600), ("semi bold", 600), ("demibold", 600), ("demi", 600),
                ("black", 900), ("heavy", 900), ("bold", 700), ("medium", 500), ("extralight", 200), ("ultralight", 200), ("thin", 200), ("light", 300)]


def split_weight(name: str) -> tuple[str, int | None]:
    """'Arial Black' → ('Arial', 900); 'Segoe UI Semibold' → ('Segoe UI', 600); others unchanged."""
    low = name.lower()
    for word, w in WEIGHT_WORDS:
        if low.endswith(" " + word):
            return name[: -len(word) - 1].strip(), w
    return name, None


class FontEnv:
    """Resolves requested families to Typst font stacks, with a width correction, memoised."""

    def __init__(self) -> None:
        self.have = families()
        self._memo: dict[str, tuple[str, float, str]] = {}
        self._weights: dict[str, int | None] = {}

    def resolve(self, name: str | None) -> tuple[str, float, str]:
        """(Typst font tuple literal, tracking in em, family actually used) for a requested family."""
        name = (name or "Arial").strip() or "Arial"
        if name in self._memo:
            return self._memo[name]
        low = name.lower()
        stack: list[str] = []
        weight = None
        if low in self.have:
            stack.append(name)
        else:
            base, weight = split_weight(name)
            if weight is not None and base.lower() in self.have and low not in SUBSTITUTES:
                stack.append(base)
            elif weight is not None:
                weight = None
        for s in SUBSTITUTES.get(low, []):
            if s.lower() in self.have:
                stack.append(s)
        g = generic_of(name)
        for s in {"sans": SANS, "serif": SERIF, "mono": MONO}[g]:
            if s.lower() in self.have and s not in stack:
                stack.append(s)
        used = stack[0] if stack else "Libertinus Serif"
        tracking = 0.0
        self._weights[name] = weight
        if weight is not None:
            pass  # same family at another weight: its width is close enough
        elif used.lower() != low:
            want, got = AVG_WIDTH.get(low), AVG_WIDTH.get(used.lower())
            if want and got:
                tracking = max(-0.08, min(0.08, want - got))
        for s in EXTRA:
            if s.lower() in self.have and s not in stack:
                stack.append(s)
        lit = "(" + ", ".join('"' + s.replace('"', "") + '"' for s in stack) + ("," if len(stack) == 1 else "") + ")" if stack else '("Libertinus Serif",)'
        self._memo[name] = (lit, round(tracking, 3), used)
        return self._memo[name]

    def weight(self, name: str | None) -> int | None:
        """A weight to apply when a weight-named family ('Arial Black') was mapped to its base family."""
        self.resolve(name)
        return self._weights.get((name or "Arial").strip() or "Arial")

    def line_factor(self, name: str | None) -> float:
        low = (name or "").lower()
        if low in LINE_FACTOR:
            return LINE_FACTOR[low]
        return LINE_FACTOR.get(self.resolve(name)[2].lower(), 1.2)


WINGDINGS = {"l": "●", "n": "■", "q": "❑", "u": "◆", "v": "❖", "Ø": "➢", "ü": "✓", "§": "▪", "o": "□", "p": "◻", "Ü": "✓", "à": "➔", "è": "➜", "ð": "⇨", "ú": "▪", "w": "⬥", "¡": "○", "¤": "◉", "Ÿ": "•", "Ö": "➢", "Þ": "➔"}
SYMBOL = {"·": "•", "-": "–", "Þ": "⇒", "®": "→"}


def bullet_char(ch: str, font: str | None) -> str:
    f = (font or "").lower()
    if "wingdings" in f:
        return WINGDINGS.get(ch, "•")
    if f == "symbol":
        return SYMBOL.get(ch, ch if ch.isprintable() and ord(ch) < 0xF000 else "•")
    if ch and 0xF000 <= ord(ch[0]) <= 0xF0FF:  # private-use symbol-font codes
        return WINGDINGS.get(chr(ord(ch[0]) - 0xF000), "•")
    return ch or "•"
