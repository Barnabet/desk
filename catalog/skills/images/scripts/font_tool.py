#!/usr/bin/env python3
"""Fonts (.ttf .otf .ttc .woff .woff2): inspect, check coverage, subset, convert, look at, find, instantiate.

Subcommands:
  info FONT…            names, version, designer, licence and embedding permissions (fsType), weight/width/style,
                        glyph and character counts, Unicode blocks covered, OpenType features and scripts, variable
                        axes and named instances, colour tables, hinting, metrics
  coverage FONT… --text T   which characters of a text each font has, which are missing (with Unicode names), and
                        with --system which installed fonts cover the missing ones
  subset FONT --out F   keep only the glyphs a text (or Unicode ranges) needs; any output flavour (woff2 for the web)
  convert FONT --out F  TTF/OTF ↔ WOFF/WOFF2, and TrueType ↔ CFF outlines (ttf ↔ otf)
  specimen FONT         a PNG to look at: name, alphabet, waterfall, your text, variable instances; --glyphs for a
                        grid of every character with its code point (icon fonts)
  find NAME             installed fonts matching a name (Windows, macOS, Linux font folders); --covers TEXT for
                        installed fonts that have every character of a text
  instance FONT --axes wght=700 --out F   a static font from a variable font

Examples:
  python3 scripts/font_tool.py info Inter.woff2
  python3 scripts/font_tool.py coverage Brand.otf --text "Crème brûlée — 50 €" --system
  python3 scripts/font_tool.py subset Brand.ttf --text-file page.html --out brand-subset.woff2
  python3 scripts/font_tool.py convert Brand.otf --out Brand.ttf
  python3 scripts/font_tool.py convert NotoSansCJK.otf --out NotoSansCJK.woff2 --fast   # big fonts: seconds, not a minute
  python3 scripts/font_tool.py specimen Brand.otf --text "Quarterly report"
  python3 scripts/font_tool.py specimen icons.ttf --glyphs --page 2
  python3 scripts/font_tool.py find "Helvetica"
  python3 scripts/font_tool.py instance Inter-Variable.ttf --axes wght=700,opsz=32 --out Inter-Bold.ttf
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, md_table, output_path, parser, run_main

BLOCKS = [
    (0x0000, 0x007F, "Basic Latin"), (0x0080, 0x00FF, "Latin-1 Supplement"), (0x0100, 0x017F, "Latin Extended-A"),
    (0x0180, 0x024F, "Latin Extended-B"), (0x0250, 0x02AF, "IPA Extensions"), (0x02B0, 0x02FF, "Spacing Modifier Letters"),
    (0x0300, 0x036F, "Combining Diacritical Marks"), (0x0370, 0x03FF, "Greek and Coptic"), (0x0400, 0x04FF, "Cyrillic"),
    (0x0500, 0x052F, "Cyrillic Supplement"), (0x0530, 0x058F, "Armenian"), (0x0590, 0x05FF, "Hebrew"), (0x0600, 0x06FF, "Arabic"),
    (0x0700, 0x074F, "Syriac"), (0x0780, 0x07BF, "Thaana"), (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"), (0x0A80, 0x0AFF, "Gujarati"), (0x0B00, 0x0B7F, "Oriya"), (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"), (0x0D00, 0x0D7F, "Malayalam"), (0x0D80, 0x0DFF, "Sinhala"),
    (0x0E00, 0x0E7F, "Thai"), (0x0E80, 0x0EFF, "Lao"), (0x0F00, 0x0FFF, "Tibetan"), (0x1000, 0x109F, "Myanmar"),
    (0x10A0, 0x10FF, "Georgian"), (0x1100, 0x11FF, "Hangul Jamo"), (0x1200, 0x137F, "Ethiopic"), (0x13A0, 0x13FF, "Cherokee"),
    (0x1400, 0x167F, "Canadian Syllabics"), (0x1780, 0x17FF, "Khmer"), (0x1800, 0x18AF, "Mongolian"),
    (0x1E00, 0x1EFF, "Latin Extended Additional"), (0x1F00, 0x1FFF, "Greek Extended"), (0x2000, 0x206F, "General Punctuation"),
    (0x2070, 0x209F, "Superscripts and Subscripts"), (0x20A0, 0x20CF, "Currency Symbols"), (0x2100, 0x214F, "Letterlike Symbols"),
    (0x2150, 0x218F, "Number Forms"), (0x2190, 0x21FF, "Arrows"), (0x2200, 0x22FF, "Mathematical Operators"),
    (0x2300, 0x23FF, "Miscellaneous Technical"), (0x2460, 0x24FF, "Enclosed Alphanumerics"), (0x2500, 0x257F, "Box Drawing"),
    (0x2580, 0x259F, "Block Elements"), (0x25A0, 0x25FF, "Geometric Shapes"), (0x2600, 0x26FF, "Miscellaneous Symbols"),
    (0x2700, 0x27BF, "Dingbats"), (0x2C60, 0x2C7F, "Latin Extended-C"), (0x3000, 0x303F, "CJK Symbols and Punctuation"),
    (0x3040, 0x309F, "Hiragana"), (0x30A0, 0x30FF, "Katakana"), (0x3100, 0x312F, "Bopomofo"), (0x3130, 0x318F, "Hangul Compatibility Jamo"),
    (0x3400, 0x4DBF, "CJK Unified Ideographs Extension A"), (0x4E00, 0x9FFF, "CJK Unified Ideographs"), (0xA000, 0xA48F, "Yi Syllables"),
    (0xA720, 0xA7FF, "Latin Extended-D"), (0xAC00, 0xD7AF, "Hangul Syllables"), (0xE000, 0xF8FF, "Private Use Area"),
    (0xF900, 0xFAFF, "CJK Compatibility Ideographs"), (0xFB00, 0xFB4F, "Alphabetic Presentation Forms"),
    (0xFB50, 0xFDFF, "Arabic Presentation Forms-A"), (0xFE70, 0xFEFF, "Arabic Presentation Forms-B"),
    (0xFF00, 0xFFEF, "Halfwidth and Fullwidth Forms"), (0xFFF0, 0xFFFF, "Specials"), (0x1D400, 0x1D7FF, "Mathematical Alphanumeric Symbols"),
    (0x1F300, 0x1F5FF, "Miscellaneous Symbols and Pictographs"), (0x1F600, 0x1F64F, "Emoticons"), (0x1F680, 0x1F6FF, "Transport and Map Symbols"),
    (0x1F900, 0x1F9FF, "Supplemental Symbols and Pictographs"), (0xF0000, 0xFFFFD, "Supplementary Private Use Area-A"),
]
FS_TYPE = {0: "installable (no embedding restrictions)", 2: "restricted licence (must not be embedded)", 4: "preview & print embedding", 8: "editable embedding"}
WEIGHTS = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
WIDTHS = {1: "UltraCondensed", 2: "ExtraCondensed", 3: "Condensed", 4: "SemiCondensed", 5: "Normal", 6: "SemiExpanded", 7: "Expanded", 8: "ExtraExpanded", 9: "UltraExpanded"}
SAMPLE = "The quick brown fox jumps over the lazy dog 0123456789"


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("info")
    i.add_argument("fonts", nargs="+")
    i.add_argument("--index", type=int, help="font number in a .ttc/.otc collection (default: all)")
    i.add_argument("--max-chars", type=int, default=60000, help="text budget (default 60000; 0 = no limit)")
    add_format(i)
    c = sub.add_parser("coverage")
    c.add_argument("fonts", nargs="+", help="one font, or a fallback chain in order")
    c.add_argument("--text")
    c.add_argument("--text-file", help="read the text from a file (UTF-8; HTML/Markdown are fine)")
    c.add_argument("--unicodes", help="code points or ranges: U+0041-005A,U+20AC,0x2192")
    c.add_argument("--index", type=int, default=0)
    c.add_argument("--system", action="store_true", help="suggest installed fonts that cover the missing characters")
    c.add_argument("--max-chars", type=int, default=60000, help="text budget (default 60000; 0 = no limit)")
    add_format(c)
    s = sub.add_parser("subset")
    s.add_argument("font")
    s.add_argument("--out", required=True, help="output font; the extension picks the flavour (.woff2 .woff .ttf .otf)")
    s.add_argument("--text")
    s.add_argument("--text-file")
    s.add_argument("--unicodes", help="code points or ranges, e.g. U+0020-007E,U+00A0-00FF")
    s.add_argument("--glyphs", help="glyph names, comma separated")
    s.add_argument("--features", default="*", help="OpenType features to keep: '*' (default, all) or kern,liga,…")
    s.add_argument("--no-hinting", action="store_true", help="drop hinting (smaller)")
    s.add_argument("--fast", action="store_true", help="faster WOFF2 compression (Brotli 9; about 10%% larger)")
    s.add_argument("--index", type=int, default=0)
    s.add_argument("--force", action="store_true")
    add_format(s)
    v = sub.add_parser("convert")
    v.add_argument("font")
    v.add_argument("--out", required=True, help="output: .woff2 .woff .ttf (TrueType outlines) .otf (CFF outlines)")
    v.add_argument("--fast", action="store_true", help="faster WOFF2 compression for big fonts (Brotli 9; about 10%% larger)")
    v.add_argument("--index", type=int, default=0)
    v.add_argument("--force", action="store_true")
    add_format(v)
    sp = sub.add_parser("specimen")
    sp.add_argument("font")
    sp.add_argument("--out", help="PNG path (default renders/<font>-specimen.png)")
    sp.add_argument("--text", help="your own text, shown large")
    sp.add_argument("--glyphs", action="store_true", help="grid of every character with its code point")
    sp.add_argument("--page", type=int, default=1, help="with --glyphs: page of 120 characters (default 1)")
    sp.add_argument("--index", type=int, default=0)
    add_format(sp)
    f = sub.add_parser("find")
    f.add_argument("name", nargs="?", default="", help="family or font name (part of it is enough)")
    f.add_argument("--covers", help="only fonts that have every character of this text")
    f.add_argument("--limit", type=int, default=40)
    f.add_argument("--refresh", action="store_true", help="rebuild the font index")
    f.add_argument("--max-chars", type=int, default=60000, help="text budget (default 60000; 0 = no limit)")
    add_format(f)
    n = sub.add_parser("instance")
    n.add_argument("font")
    n.add_argument("--axes", help="axis=value pairs, e.g. wght=700,wdth=90 (unset axes keep their default)")
    n.add_argument("--named", help="a named instance, e.g. 'Bold' (see info)")
    n.add_argument("--out", required=True)
    n.add_argument("--force", action="store_true")
    add_format(n)
    return p


# ── helpers ─────────────────────────────────────────────────────────────


def _font_path(s: str) -> Path:
    from _common import input_file

    p = input_file(s, (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"))
    _CURRENT.append(str(p))
    return p


def _codepoints(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")

        def num(t: str) -> int:
            t = t.upper().removeprefix("U+").removeprefix("0X")
            try:
                return int(t, 16)
            except ValueError:
                raise UsageError(f"bad code point '{t}' (use U+0041 or 0x41)") from None

        lo = num(a)
        hi = num(b) if b else lo
        if hi < lo or hi - lo > 0x10FFFF:
            raise UsageError(f"bad range '{part}'")
        out.update(range(lo, hi + 1))
    return out


def _text_arg(args: Any) -> str:
    text = args.text or ""
    if getattr(args, "text_file", None):
        text += Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    return text


def _char_name(cp: int) -> str:
    try:
        return unicodedata.name(chr(cp))
    except ValueError:
        return "(unnamed)"


def _fs_type(v: int) -> list[str]:
    out = []
    usage = v & 0x000F
    if usage == 0:
        out.append(FS_TYPE[0])
    for bit in (2, 4, 8):
        if usage & bit:
            out.append(FS_TYPE[bit])
    if v & 0x0100:
        out.append("no subsetting")
    if v & 0x0200:
        out.append("bitmap embedding only")
    return out


def _blocks(cps: set[int]) -> list[dict[str, Any]]:
    res = []
    for lo, hi, name in BLOCKS:
        n = sum(1 for c in cps if lo <= c <= hi)
        if n:
            size = sum(1 for c in range(lo, hi + 1) if unicodedata.category(chr(c)) not in ("Cn", "Cs")) if hi - lo < 30000 else hi - lo + 1
            res.append({"block": name, "chars": n, "of": size, "pct": round(100 * n / max(1, size), 1)})
    return res


def _ot_tags(tt: Any) -> tuple[list[str], list[str]]:
    feats: set[str] = set()
    scripts: set[str] = set()
    for tag in ("GSUB", "GPOS"):
        if tag in tt:
            t = tt[tag].table
            if getattr(t, "FeatureList", None):
                feats.update(fr.FeatureTag for fr in t.FeatureList.FeatureRecord)
            if getattr(t, "ScriptList", None):
                scripts.update(sr.ScriptTag.strip() for sr in t.ScriptList.ScriptRecord)
    return sorted(feats), sorted(scripts)


def font_info(path: Path, index: int) -> dict[str, Any]:
    from _fonts import collection_size, font_format, name_record, open_font

    tt = open_font(path, index)
    try:
        cmap = tt.getBestCmap() or {}
        r: dict[str, Any] = {"file": str(path), "bytes": path.stat().st_size, "format": font_format(tt, path)}
        n = collection_size(path)
        if n > 1:
            r["collection"] = {"index": index, "fonts": n}
        names = {
            "family": (16, 1), "style": (17, 2), "full_name": (4,), "postscript_name": (6,), "version": (5,), "unique_id": (3,),
            "copyright": (0,), "trademark": (7,), "manufacturer": (8,), "designer": (9,), "description": (10,), "vendor_url": (11,),
            "designer_url": (12,), "license": (13,), "license_url": (14,), "sample_text": (19,),
        }
        for k, ids in names.items():
            v = name_record(tt, *ids)
            if v:
                r[k] = v if len(v) <= 500 else v[:500] + "…"
        if "OS/2" in tt:
            os2 = tt["OS/2"]
            r["weight"] = f"{os2.usWeightClass} ({WEIGHTS.get(round(os2.usWeightClass / 100) * 100, '?')})"
            r["width"] = f"{os2.usWidthClass} ({WIDTHS.get(os2.usWidthClass, '?')})"
            r["italic"] = bool(os2.fsSelection & 1)
            r["embedding"] = _fs_type(int(os2.fsType))
            r["vendor_id"] = str(getattr(os2, "achVendID", "")).strip()
            r["metrics"] = {"units_per_em": tt["head"].unitsPerEm, "ascender": os2.sTypoAscender, "descender": os2.sTypoDescender, "line_gap": os2.sTypoLineGap,
                            "x_height": getattr(os2, "sxHeight", None), "cap_height": getattr(os2, "sCapHeight", None)}
        if "post" in tt:
            r["monospaced"] = bool(tt["post"].isFixedPitch)
            if tt["post"].italicAngle:
                r["italic_angle"] = tt["post"].italicAngle
        r["glyphs"] = tt["maxp"].numGlyphs if "maxp" in tt else None
        r["characters"] = len(cmap)
        r["blocks"] = _blocks(set(cmap))
        feats, scripts = _ot_tags(tt)
        if feats:
            r["features"] = feats
        if scripts:
            r["scripts"] = scripts
        r["kerning"] = "GPOS kern" if "kern" in feats else ("legacy kern table" if "kern" in tt else "none")
        if "fvar" in tt:
            fv = tt["fvar"]
            r["axes"] = [{"tag": a.axisTag, "min": a.minValue, "default": a.defaultValue, "max": a.maxValue, "name": name_record(tt, a.axisNameID) or a.axisTag} for a in fv.axes]
            r["instances"] = [{"name": name_record(tt, inst.subfamilyNameID) or "?", "coords": {k: round(v, 2) for k, v in inst.coordinates.items()}} for inst in fv.instances][:40]
        color = [t for t in ("COLR", "CPAL", "SVG ", "sbix", "CBDT", "CBLC") if t in tt]
        if color:
            r["color_tables"] = [t.strip() for t in color]
        r["hinting"] = "TrueType instructions" if ("fpgm" in tt or "prep" in tt) else ("CFF" if "CFF " in tt or "CFF2" in tt else "none")
        r["tables"] = sorted(t.strip() for t in tt.keys() if t != "GlyphOrder")
        return r
    finally:
        tt.close()


# ── subcommands ─────────────────────────────────────────────────────────


def cmd_info(args: Any) -> int:
    from _fonts import collection_size

    recs = []
    for f in args.fonts:
        p = _font_path(f)
        n = collection_size(p)
        idxs = [args.index] if args.index is not None else list(range(n))
        for i in idxs:
            if i >= n:
                raise UsageError(f"{p.name} has {n} font(s)")
            recs.append(font_info(p, i))
    if args.format == "json":
        emit(recs[0] if len(recs) == 1 else recs)
        return 0

    def render(rs: list[dict[str, Any]]) -> str:
        out = []
        for r in rs:
            lines = [f"## {r.get('full_name') or Path(r['file']).name}", ""]
            lines.append(f"- file: {r['file']} ({human_size(r['bytes'])}), {r['format']}" + (f", font {r['collection']['index']} of {r['collection']['fonts']} in the collection" if r.get("collection") else ""))
            lines.append(f"- family: {r.get('family')} · style: {r.get('style')} · weight {r.get('weight', '?')} · width {r.get('width', '?')}" + (" · italic" if r.get("italic") else "") + (" · monospaced" if r.get("monospaced") else ""))
            for k in ("postscript_name", "version", "designer", "manufacturer", "vendor_url", "copyright", "trademark", "license_url"):
                if r.get(k):
                    lines.append(f"- {k.replace('_', ' ')}: {r[k]}")
            if r.get("license"):
                lines.append(f"- licence: {r['license'][:300]}")
            if r.get("embedding"):
                lines.append(f"- embedding (fsType): {', '.join(r['embedding'])}")
            lines.append(f"- {r['glyphs']} glyphs, {r['characters']} characters; kerning: {r['kerning']}; hinting: {r['hinting']}")
            full = [b for b in r["blocks"] if b["pct"] >= 60]
            part = [b for b in r["blocks"] if b["pct"] < 60]
            if full:
                lines.append("- Unicode blocks (≥60%): " + ", ".join(f"{b['block']} {b['pct']:g}%" for b in full))
            if part:
                lines.append("- partial blocks: " + ", ".join(f"{b['block']} {b['chars']}" for b in part[:20]))
            if r.get("scripts"):
                lines.append(f"- OpenType scripts: {', '.join(r['scripts'])}")
            if r.get("features"):
                lines.append(f"- OpenType features: {', '.join(r['features'])}")
            if r.get("axes"):
                lines.append("- variable axes: " + ", ".join(f"{a['tag']} {a['min']:g}–{a['max']:g} (default {a['default']:g})" for a in r["axes"]))
                if r.get("instances"):
                    lines.append("- named instances: " + ", ".join(i["name"] for i in r["instances"]))
            if r.get("color_tables"):
                lines.append(f"- colour font: {', '.join(r['color_tables'])}")
            if r.get("metrics"):
                m = r["metrics"]
                lines.append(f"- metrics: {m['units_per_em']} units/em, ascender {m['ascender']}, descender {m['descender']}, line gap {m['line_gap']}" + (f", x-height {m['x_height']}, cap height {m['cap_height']}" if m.get("x_height") else ""))
            out.append("\n".join(lines))
        return "\n\n".join(out)

    emit(recs, "md", render, max_chars=args.max_chars or None, hint="Pass one font at a time, or --format json.")
    return 0


def cmd_coverage(args: Any) -> int:
    from _fonts import cmap_of, cover_font

    text = _text_arg(args)
    cps = {ord(ch) for ch in text if not ch.isspace() and unicodedata.category(ch)[0] != "C"}
    if args.unicodes:
        cps |= _codepoints(args.unicodes)
    if not cps:
        raise UsageError("give --text, --text-file or --unicodes")
    fonts = [_font_path(f) for f in args.fonts]
    maps = [cmap_of(str(f), args.index) for f in fonts]
    per_font = []
    owner: dict[int, str | None] = {}
    for f, cm in zip(fonts, maps):
        have = cps & cm
        per_font.append({"font": str(f), "covered": len(have), "of": len(cps), "pct": round(100 * len(have) / len(cps), 2)})
        for c in sorted(have):
            owner.setdefault(c, f.name)
    missing = sorted(c for c in cps if c not in owner)
    res: dict[str, Any] = {"characters": len(cps), "fonts": per_font, "missing": [{"char": chr(c), "code": f"U+{c:04X}", "name": _char_name(c)} for c in missing[:500]]}
    if len(fonts) > 1:
        res["fallback_used"] = {name: sum(1 for v in owner.values() if v == name) for name in {f.name for f in fonts}}
    if args.system and missing:
        sugg = cover_font("".join(chr(c) for c in missing))
        res["system_font_covering_missing"] = {"path": sugg[0], "index": sugg[1]} if sugg else None
    if args.format == "json":
        emit(res)
        return 0
    lines = []
    for pf in per_font:
        lines.append(f"- {Path(pf['font']).name}: {pf['covered']} of {pf['of']} characters ({pf['pct']:g}%)")
    if missing:
        lines.append(f"\nMissing from every font ({len(missing)}):")
        lines.append(md_table(["char", "code", "name"], [[m["char"], m["code"], m["name"]] for m in res["missing"][:200]]))
    else:
        lines.append("\nEvery character is covered.")
    if "system_font_covering_missing" in res:
        s = res["system_font_covering_missing"]
        lines.append(f"\nInstalled font covering the missing characters: {s['path']}" + (f" (#{s['index']})" if s and s["index"] else "") if s else "\nNo installed font covers all the missing characters.")
    from _common import cap

    print(cap("\n".join(lines), args.max_chars or None, "Use --format json for every missing character."))
    return 0


def cmd_subset(args: Any) -> int:
    from fontTools import subset

    from _fonts import open_font, save_font

    src = _font_path(args.font)
    out = output_path(args.out, [src], args.force)
    text = _text_arg(args)
    unicodes = _codepoints(args.unicodes) if args.unicodes else set()
    glyphs = [g.strip() for g in args.glyphs.split(",")] if args.glyphs else []
    if not text and not unicodes and not glyphs:
        raise UsageError("say what to keep: --text, --text-file, --unicodes or --glyphs")
    flavor = {".woff2": "woff2", ".woff": "woff"}.get(out.suffix.lower())
    opts = subset.Options()
    opts.flavor = flavor
    opts.layout_features = ["*"] if args.features.strip() == "*" else [f.strip() for f in args.features.split(",") if f.strip()]
    opts.hinting = not args.no_hinting
    opts.desubroutinize = True
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.font_number = args.index
    font = open_font(src, args.index, lazy=False)
    try:
        before = font["maxp"].numGlyphs
        cmap = font.getBestCmap() or {}
        wanted = {ord(ch) for ch in text if not ch.isspace() or ch == " "} | unicodes
        missing = sorted(c for c in wanted if c not in cmap and c >= 32)
        s = subset.Subsetter(options=opts)
        s.populate(text=text, unicodes=sorted(unicodes), glyphs=glyphs)
        s.subset(font)
        after = len(font.getGlyphOrder())
        save_font(font, out, flavor, args.fast)
    finally:
        font.close()
    res = {"input": str(src), "output": str(out), "flavor": flavor or ("otf" if out.suffix.lower() == ".otf" else "ttf"), "glyphs_before": before, "glyphs_after": after,
           "bytes_before": src.stat().st_size, "bytes_after": out.stat().st_size, "missing": [f"U+{c:04X} {chr(c)}" for c in missing[:100]]}
    if args.format == "json":
        emit(res)
    else:
        print(f"{res['output']}: {after} of {before} glyphs, {human_size(res['bytes_before'])} → {human_size(res['bytes_after'])} ({res['flavor']})")
        if missing:
            print(f"warning: {len(missing)} requested character(s) are not in the font: {' '.join(res['missing'][:20])}")
    return 0


def otf_to_ttf(tt: Any, max_err: float = 1.0) -> None:
    """CFF (cubic) outlines → TrueType (quadratic) with cu2qu, keeping every other table."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    if "CFF " not in tt:
        raise SkillError("the font has no CFF outlines" + ("; CFF2 variable fonts are not supported" if "CFF2" in tt else ""))
    order = tt.getGlyphOrder()
    gs = tt.getGlyphSet()
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(gs)
        gs[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
        glyphs[name] = pen.glyph()
    tt["loca"] = newTable("loca")
    glyf = tt["glyf"] = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del tt["CFF "]
    if "VORG" in tt:
        del tt["VORG"]
    glyf.compile(tt)
    hmtx = tt["hmtx"]
    for name, g in glyf.glyphs.items():
        if hasattr(g, "xMin"):
            hmtx[name] = (hmtx[name][0], g.xMin)
    maxp = tt["maxp"] = newTable("maxp")
    maxp.tableVersion = 0x00010000
    for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, attr, 0)
    maxp.maxZones = 1
    maxp.maxComponentElements = max((len(getattr(g, "components", []) or []) for g in glyphs.values()), default=0)
    maxp.compile(tt)
    post = tt["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    post.glyphOrder = order
    tt.sfntVersion = "\x00\x01\x00\x00"


def ttf_to_otf(tt: Any) -> None:
    """TrueType (quadratic) outlines → CFF (cubic, exact) with T2CharStringPen, keeping every other table."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    from _fonts import name_record

    if "glyf" not in tt:
        raise SkillError("the font has no TrueType outlines")
    if "gvar" in tt:
        raise SkillError("variable TrueType fonts cannot become CFF here; make a static instance first (font_tool.py instance)")
    order = tt.getGlyphOrder()
    gs = tt.getGlyphSet()
    hmtx = tt["hmtx"]
    charstrings = {}
    for name in order:
        pen = T2CharStringPen(width=hmtx[name][0], glyphSet=gs)
        gs[name].draw(pen)
        charstrings[name] = pen.getCharString()
    for t in ("glyf", "loca", "cvt ", "fpgm", "prep", "gasp", "LTSH", "hdmx", "VDMX"):
        if t in tt:
            del tt[t]
    fb = FontBuilder(font=tt, isTTF=False)
    ps = (name_record(tt, 6) or "Font").replace(" ", "")
    info = {"FullName": name_record(tt, 4) or ps, "FamilyName": name_record(tt, 1) or ps, "Weight": name_record(tt, 2) or "Regular", "version": name_record(tt, 5) or "1.0"}
    fb.setupCFF(ps, info, charstrings, {})
    fb.setupMaxp()
    tt["post"].formatType = 3.0
    tt.sfntVersion = "OTTO"


def cmd_convert(args: Any) -> int:
    from _fonts import open_font, save_font

    src = _font_path(args.font)
    out = output_path(args.out, [src], args.force)
    ext = out.suffix.lower()
    if ext not in (".woff2", ".woff", ".ttf", ".otf"):
        raise UsageError("--out must end in .woff2, .woff, .ttf or .otf")
    tt = open_font(src, args.index, lazy=False)
    try:
        before = "CFF" if "CFF " in tt else "CFF2" if "CFF2" in tt else "TrueType"
        did = []
        if ext == ".ttf" and before != "TrueType":
            otf_to_ttf(tt)
            did.append("CFF → TrueType outlines (cu2qu, max error 1 unit)")
        elif ext == ".otf" and before == "TrueType":
            ttf_to_otf(tt)
            did.append("TrueType → CFF outlines")
        flavor = {".woff2": "woff2", ".woff": "woff"}.get(ext)
        if flavor:
            did.append(f"compressed as {flavor.upper()}" + (" (fast)" if args.fast and flavor == "woff2" else ""))
        elif src.suffix.lower() in (".woff", ".woff2"):
            did.append("decompressed")
        took = save_font(tt, out, flavor, args.fast)
    finally:
        tt.close()
    res = {"input": str(src), "output": str(out), "bytes_before": src.stat().st_size, "bytes_after": out.stat().st_size, "changes": did or ["re-saved"]}
    if flavor == "woff2" and not args.fast and took > 8:
        res["note"] = f"WOFF2 compression took {took:.0f}s; --fast is about 20× faster for a file about 10% larger"
    if args.format == "json":
        emit(res)
    else:
        print(f"{res['output']}: {human_size(res['bytes_before'])} → {human_size(res['bytes_after'])}; " + "; ".join(res["changes"]))
        if res.get("note"):
            print(f"note: {res['note']}")
    return 0


def cmd_specimen(args: Any) -> int:
    from PIL import Image, ImageDraw

    from _fonts import _ttf_for_pillow, name_record, open_font
    from _render import VISION_EDGE, _font, announce

    src = _font_path(args.font)
    tt = open_font(src, args.index)
    try:
        cmap = tt.getBestCmap() or {}
        family = name_record(tt, 16, 1) or src.stem
        style = name_record(tt, 17, 2) or ""
        nglyphs = tt["maxp"].numGlyphs
        fvar = [(name_record(tt, i.subfamilyNameID) or "?") for i in tt["fvar"].instances] if "fvar" in tt else []
        # Instances as axis coordinates: FreeType's named-instance lookup crashes on some fonts, coordinates are safe.
        fvar_coords = [[i.coordinates.get(a.axisTag, a.defaultValue) for a in tt["fvar"].axes] for i in tt["fvar"].instances] if "fvar" in tt else []
    finally:
        tt.close()
    path, idx = _ttf_for_pillow(str(src), args.index)
    ui = _font(15)
    ui_big = _font(24)

    from _fonts import load_truetype

    def F(size: int) -> Any:
        return load_truetype(path, size, idx)

    out = Path(args.out) if args.out else Path("renders") / f"{''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in src.stem)}-{'glyphs' if args.glyphs else 'specimen'}{f'-{args.page}' if args.glyphs and args.page > 1 else ''}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    W = 1400
    chars = sorted(c for c in cmap if c >= 32 and unicodedata.category(chr(c))[0] not in ("C", "Z") or 0xE000 <= c <= 0xF8FF or c >= 0xF0000)
    if args.glyphs:
        per = 120
        pages = max(1, -(-len(chars) // per))
        if args.page < 1 or args.page > pages:
            raise UsageError(f"--page is 1-{pages}")
        chunk = chars[(args.page - 1) * per : args.page * per]
        cols, cell = 12, W // 12
        rows = -(-len(chunk) // cols)
        img = Image.new("RGB", (W, 70 + rows * cell), "white")
        d = ImageDraw.Draw(img)
        d.text((16, 16), f"{family} {style} — characters {(args.page - 1) * per + 1}-{(args.page - 1) * per + len(chunk)} of {len(chars)} (page {args.page}/{pages})", fill=(30, 30, 30), font=ui_big)
        gf = F(round(cell * 0.5))
        for k, c in enumerate(chunk):
            r_, c_ = divmod(k, cols)
            x0, y0 = c_ * cell, 70 + r_ * cell
            d.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], outline=(225, 225, 225))
            ch = chr(c)
            try:
                tw = d.textlength(ch, font=gf)
                d.text((x0 + (cell - tw) / 2, y0 + cell * 0.12), ch, fill="black", font=gf, embedded_color=True)
            except Exception:  # noqa: BLE001
                pass
            d.text((x0 + 4, y0 + cell - 18), f"U+{c:04X}", fill=(120, 120, 120), font=_font(11))
    else:
        latin = sum(1 for c in range(0x41, 0x7B) if c in cmap) > 40
        sample = SAMPLE if latin else "".join(chr(c) for c in chars[:40])
        # Rows: (label in the UI font, text in the font under test, font).
        rows: list[tuple[str, str, Any]] = []
        big = F(96)
        rows.append(("", "Aa Bb Cc Gg Qq" if latin else sample[:10], big))
        if latin:
            f32 = F(32)
            rows.append(("", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", f32))
            rows.append(("", "abcdefghijklmnopqrstuvwxyz", f32))
            rows.append(("", "0123456789 !?&@#%$€£ (.,;:) [\"'] {+-=/*}", f32))
        for size in (12, 16, 24, 36, 48):
            rows.append((f"{size} px", sample, F(size)))
        if args.text:
            rows.append(("your text", args.text, F(48)))
        shown_inst = list(zip(fvar, fvar_coords))[:12]
        for name, coords in shown_inst:
            f = F(36)
            try:
                f.set_variation_by_axes(coords)
            except Exception:  # noqa: BLE001 — FreeType without variation support
                break
            rows.append((name[:14], sample[:40], f))
        if len(fvar) > len(shown_inst):
            rows.append(("", f"+{len(fvar) - len(shown_inst)} more named instances: {', '.join(fvar[len(shown_inst):])}", ui))
        probe = ImageDraw.Draw(Image.new("L", (1, 1)))
        label_w = 120
        text_w = W - 24 - label_w - 32
        heights = []
        fitted = []
        for label, text, f in rows:
            t = text
            if probe.textlength(t, font=f) > text_w:
                # Cut at a word and end with an ellipsis, so a long line never runs into the edge.
                words = t.split(" ")
                while len(words) > 1 and probe.textlength(" ".join(words) + " …", font=f) > text_w:
                    words.pop()
                t = " ".join(words)
                while t and probe.textlength(t + " …", font=f) > text_w:
                    t = t[:-1]
                t = t.rstrip() + " …"
            fitted.append(t)
            b = probe.textbbox((0, 0), t or " ", font=f)
            heights.append(max(b[3], int(getattr(f, "size", 16))) + 16)
        head = 86
        img = Image.new("RGB", (W, head + sum(heights) + 16), "white")
        d = ImageDraw.Draw(img)
        d.text((24, 16), f"{family} {style}", fill=(20, 20, 20), font=ui_big)
        sub = f"{src.name} · {nglyphs} glyphs · {len(cmap)} characters" + (f" · variable, {len(fvar)} named instances ({fvar[0]} … {fvar[-1]})" if fvar else "")
        d.text((24, 52), sub, fill=(110, 110, 110), font=ui)
        y = head
        for (label, _text, f), t, h in zip(rows, fitted, heights):
            if label:
                d.text((24, y + max(0, (int(getattr(f, "size", 16)) - 15) // 2)), label, fill=(120, 120, 120), font=ui)
            d.text((24 + label_w, y), t, fill=(0, 0, 0), font=f, embedded_color=True)
            y += h
    if max(img.size) > VISION_EDGE:
        from _render import fit_edge

        img = fit_edge(img)
    img.save(out, "PNG")
    if args.format == "json":
        emit({"output": str(out), "size": list(img.size), "family": family, "style": style, "hint": "Look at them with view_image."})
    else:
        announce([out], f"{family} {style}: {'glyph grid' if args.glyphs else 'specimen'} {img.size[0]}×{img.size[1]}")
    return 0


def cmd_find(args: Any) -> int:
    from _fonts import face_covers, find_faces, font_dirs, font_index

    faces = font_index(refresh=args.refresh)
    hits = find_faces(args.name, faces) if args.name else list(faces)
    if not (args.name or "").startswith("."):
        # Families starting with "." are private to macOS (not selectable in apps).
        hits = [f for f in hits if not f["family"].startswith(".")]
    if args.covers:
        need = {ord(c) for c in args.covers if not c.isspace()}
        hits = [f for f in hits if face_covers(f, need)]
    hits = sorted(hits, key=lambda f: (f["family"].lower(), f["weight"], f["italic"]))[: args.limit]
    for f in hits:
        f.pop("ranges", None)
    if args.format == "json":
        emit({"font_dirs": [str(d) for d in font_dirs()], "faces": hits})
        return 0 if hits else 1
    if not hits:
        print(f"no installed font matches '{args.name}'" + (" and covers the text" if args.covers else "") + f" (searched {', '.join(str(d) for d in font_dirs())})")
        return 1
    from _common import cap

    print(cap(md_table(["family", "style", "weight", "file"], [[f["family"], f["style"], f["weight"], f["path"] + (f" #{f['index']}" if f["index"] else "")] for f in hits]), args.max_chars or None, "Narrow the name, or lower --limit."))
    return 0


def cmd_instance(args: Any) -> int:
    from fontTools.varLib import instancer

    from _fonts import name_record, open_font, save_font

    src = _font_path(args.font)
    out = output_path(args.out, [src], args.force)
    tt = open_font(src, 0, lazy=False)
    try:
        if "fvar" not in tt:
            raise SkillError(f"{src.name} is not a variable font")
        fv = tt["fvar"]
        loc: dict[str, float] = {a.axisTag: a.defaultValue for a in fv.axes}
        if args.named:
            match = [i for i in fv.instances if (name_record(tt, i.subfamilyNameID) or "").lower() == args.named.lower()]
            if not match:
                raise UsageError(f"no named instance '{args.named}'; see font_tool.py info")
            loc.update(match[0].coordinates)
        if args.axes:
            for part in args.axes.split(","):
                k, _, v = part.partition("=")
                k = k.strip()
                if k not in loc:
                    raise UsageError(f"axis '{k}' is not in the font ({', '.join(loc)})")
                loc[k] = float(v)
        for a in fv.axes:
            if not a.minValue <= loc[a.axisTag] <= a.maxValue:
                raise UsageError(f"{a.axisTag}={loc[a.axisTag]:g} is outside {a.minValue:g}–{a.maxValue:g}")
        static = instancer.instantiateVariableFont(tt, loc, inplace=False)
        save_font(static, out, {".woff2": "woff2", ".woff": "woff"}.get(out.suffix.lower()))
    finally:
        tt.close()
    res = {"input": str(src), "output": str(out), "location": loc, "bytes": out.stat().st_size}
    if args.format == "json":
        emit(res)
    else:
        print(f"{out}: static instance at " + ", ".join(f"{k}={v:g}" for k, v in loc.items()) + f" ({human_size(res['bytes'])})")
    return 0


_CURRENT: list[str] = []


def main() -> int:
    import os

    args = build_parser().parse_args()
    fn = {"info": cmd_info, "coverage": cmd_coverage, "subset": cmd_subset, "convert": cmd_convert, "specimen": cmd_specimen, "find": cmd_find, "instance": cmd_instance}[args.cmd]
    try:
        return fn(args)
    except SkillError:
        raise
    except (KeyboardInterrupt, BrokenPipeError):
        raise
    except Exception as e:  # noqa: BLE001 — fontTools reads lazily: a damaged table fails later, name the file
        if os.environ.get("DESK_DEBUG"):
            raise
        name = Path(_CURRENT[-1]).name if _CURRENT else "the font"
        first = next((ln.strip() for ln in str(e).splitlines() if ln.strip()), "")
        raise SkillError(f"{name}: damaged or unsupported font ({type(e).__name__}: {first[:200]})") from None


if __name__ == "__main__":
    run_main(main)
