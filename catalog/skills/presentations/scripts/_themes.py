"""Built-in deck themes for pptx_create: palette, fonts, type scale and spacing grid.

Fonts are ones every PowerPoint, Keynote, Google Slides and LibreOffice install can show (Arial, Georgia, Trebuchet
MS, Courier New; LibreOffice maps them to metric-compatible faces), so decks look the same on Windows and macOS.
Colours are written into the theme part, so the deck stays re-themable in PowerPoint, and every shape refers to them
by role: bg1 background, tx1 text, bg2 surface (cards, table bands), tx2 muted text, accent1-6.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class Theme:
    name: str
    label: str
    dark: bool
    bg: str
    surface: str
    text: str
    muted: str
    accents: list[str]
    heading_font: str
    body_font: str
    mono_font: str = "Courier New"
    heading_bold: bool = True
    decor: str = "bar"  # bar | rule | band
    section_bg: str = "surface"  # surface | accent | text
    positive: str = "15803D"
    negative: str = "B91C1C"
    hlink: str = ""
    sizes: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SIZES))

    def color(self, role: str) -> str:
        """Hex for a role name: bg, surface, text, muted, accent1..6, positive, negative, or a #hex."""
        r = role.lower().lstrip("#")
        if r in ("bg", "background", "bg1"):
            return self.bg
        if r in ("surface", "bg2"):
            return self.surface
        if r in ("text", "tx1", "fg"):
            return self.text
        if r in ("muted", "tx2", "secondary"):
            return self.muted
        if r.startswith("accent") and r[6:].isdigit() and 1 <= int(r[6:]) <= 6:
            return self.accents[int(r[6:]) - 1]
        if r in ("positive", "good", "up"):
            return self.positive
        if r in ("negative", "bad", "down"):
            return self.negative
        return role.lstrip("#").upper()


DEFAULT_SIZES = {
    "cover": 44, "section": 40, "title": 30, "subtitle": 20, "body": 20, "body2": 18, "body3": 16,
    "small": 14, "caption": 12, "footer": 10, "kpi": 54, "quote": 30, "min_body": 14, "min_title": 22,
}

THEMES: dict[str, Theme] = {
    "clean": Theme(
        "clean", "Clean: white, near-black text, blue accent (default)", False,
        bg="FFFFFF", surface="F1F4F9", text="1B2430", muted="5B6675",
        accents=["2563EB", "0EA5E9", "10B981", "F59E0B", "EF4444", "8B5CF6"],
        heading_font="Arial", body_font="Arial", decor="bar", section_bg="surface",
    ),
    "midnight": Theme(
        "midnight", "Midnight: dark navy background, light text, sky and violet accents", True,
        bg="0F172A", surface="1E293B", text="F1F5F9", muted="94A3B8",
        accents=["38BDF8", "A78BFA", "34D399", "FBBF24", "F87171", "F472B6"],
        heading_font="Trebuchet MS", body_font="Arial", decor="bar", section_bg="surface", positive="4ADE80", negative="F87171",
    ),
    "paper": Theme(
        "paper", "Paper: warm off-white, serif headings, terracotta and teal (editorial)", False,
        bg="FBF8F3", surface="F1EBE1", text="2B2622", muted="75695F",
        accents=["B4532A", "2F6F73", "C08A2E", "6B4E71", "4F7942", "8C2F39"],
        heading_font="Georgia", body_font="Arial", heading_bold=False, decor="rule", section_bg="surface",
    ),
    "vivid": Theme(
        "vivid", "Vivid: white with bold violet section slides and bright accents (pitches)", False,
        bg="FFFFFF", surface="F4F1FE", text="1E1B4B", muted="5F5B7A",
        accents=["7C3AED", "EC4899", "F97316", "14B8A6", "EAB308", "3B82F6"],
        heading_font="Trebuchet MS", body_font="Arial", decor="band", section_bg="accent",
    ),
    "slate": Theme(
        "slate", "Slate: cool grey, teal accent, dark section slides (corporate reports)", False,
        bg="F8FAFC", surface="E7ECF2", text="0F172A", muted="5A6B7F",
        accents=["0F766E", "1D4ED8", "B45309", "7E22CE", "BE123C", "15803D"],
        heading_font="Arial", body_font="Arial", decor="rule", section_bg="text",
    ),
}


def get_theme(spec: Any) -> Theme:
    """A theme from a name, or {"base": name, "accent": "#hex", "accents": [...], "fonts": {...}, ...} overrides."""
    from _common import UsageError

    if spec is None:
        return THEMES["clean"]
    if isinstance(spec, str):
        if spec not in THEMES:
            raise UsageError(f"unknown theme '{spec}' (choose from {', '.join(THEMES)})")
        return THEMES[spec]
    if not isinstance(spec, dict):
        raise UsageError("theme must be a name or an object")
    base = get_theme(spec.get("base", "clean"))
    changes: dict[str, Any] = {}
    for k in ("bg", "surface", "text", "muted", "positive", "negative"):
        if k in spec:
            changes[k] = _hex(spec[k])
    accents = list(base.accents)
    if "accents" in spec:
        for i, c in enumerate(spec["accents"][:6]):
            accents[i] = _hex(c)
    if "accent" in spec:
        accents[0] = _hex(spec["accent"])
    changes["accents"] = accents
    fonts = spec.get("fonts") or {}
    if "heading" in fonts:
        changes["heading_font"] = str(fonts["heading"])
    if "body" in fonts:
        changes["body_font"] = str(fonts["body"])
    if "mono" in fonts:
        changes["mono_font"] = str(fonts["mono"])
    if "dark" in spec:
        changes["dark"] = bool(spec["dark"])
    if "heading_bold" in spec:
        changes["heading_bold"] = bool(spec["heading_bold"])
    if spec.get("decor") in ("bar", "rule", "band"):
        changes["decor"] = spec["decor"]
    if spec.get("section_bg") in ("surface", "accent", "text"):
        changes["section_bg"] = spec["section_bg"]
    if isinstance(spec.get("sizes"), dict):
        sizes = dict(base.sizes)
        sizes.update({k: float(v) for k, v in spec["sizes"].items() if k in sizes})
        changes["sizes"] = sizes
    return replace(base, name=str(spec.get("name", base.name)), **changes)


def _hex(v: Any) -> str:
    from _common import UsageError

    s = str(v).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise UsageError(f"bad colour '{v}' (use #RRGGBB)")
    return s.upper()


def theme_list() -> str:
    return "\n".join(f"  {t.name:9} {t.label}; fonts {t.heading_font} / {t.body_font}" for t in THEMES.values())
