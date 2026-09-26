"""Fonts for the images skill: reading font files (fontTools), finding system fonts on Windows, macOS and Linux,
and choosing a Pillow font that covers a given text."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError

FONT_FILE_EXTS = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2")

GENERIC = {
    "sans": ["Helvetica", "Arial", "Segoe UI", "DejaVu Sans", "Noto Sans", "Liberation Sans", "Roboto", "Verdana"],
    "serif": ["Times New Roman", "Times", "Georgia", "DejaVu Serif", "Noto Serif", "Liberation Serif", "Cambria"],
    "mono": ["Menlo", "Consolas", "Courier New", "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "Monaco", "Courier"],
}
GENERIC["sans-serif"] = GENERIC["sans"]
GENERIC["monospace"] = GENERIC["mono"]
#: Broad-coverage fonts tried first when a text has characters the chosen font lacks (CJK, symbols…).
WIDE_COVERAGE = [
    "Arial Unicode MS", "PingFang SC", "Hiragino Sans", "Hiragino Sans GB", "Apple SD Gothic Neo", "Noto Sans CJK SC", "Noto Sans CJK JP",
    "Microsoft YaHei", "Yu Gothic", "Malgun Gothic", "Meiryo", "SimSun", "Segoe UI Symbol", "Nirmala UI", "Noto Sans", "DejaVu Sans",
    "Apple Symbols", "Arial", "Tahoma", "Geeza Pro", "Kohinoor Devanagari", "Thonburi",
]


# ── reading font files ──────────────────────────────────────────────────


def font_max_bytes() -> int:
    """The most a WOFF/WOFF2 font may inflate to (DESK_FONT_MAX_MB, default 256)."""
    try:
        return max(1, int(os.environ.get("DESK_FONT_MAX_MB", "256"))) * 1024 * 1024
    except ValueError:
        return 256 * 1024 * 1024


_CHECKED: set[tuple[str, int, int]] = set()


def check_font(path: Path) -> None:
    """Refuses a compressed font (WOFF zlib tables, the WOFF2 Brotli stream) that would inflate past its declared
    sizes or DESK_FONT_MAX_MB, before fontTools inflates it whole: a 500 KB file must not take gigabytes of memory.
    Inflation is streamed with an output limit, so the check itself stays small. Other fonts return at once."""
    import struct
    import zlib

    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key in _CHECKED:
        return
    limit = font_max_bytes()
    hint = "raise DESK_FONT_MAX_MB if you trust the file"
    with open(path, "rb") as f:
        head = f.read(48)
        sig = head[:4]
        if sig == b"wOFF" and len(head) >= 44:
            n = struct.unpack(">H", head[12:14])[0]
            total = struct.unpack(">I", head[16:20])[0]
            if total > limit:
                raise SkillError(f"{path.name}: the WOFF font declares {total / 1048576:.0f} MB of tables, over the {limit // 1048576} MB limit (a possible zip bomb; {hint})")
            f.seek(44)
            entries = [struct.unpack(">4sIIII", f.read(20)) for _ in range(n)]
            for tag, off, comp, orig, _ck in entries:
                if orig > limit:
                    raise SkillError(f"{path.name}: table {tag.decode('latin-1')} declares {orig / 1048576:.0f} MB (limit {limit // 1048576} MB; {hint})")
                if comp >= orig:
                    continue
                f.seek(off)
                raw = f.read(comp)
                d = zlib.decompressobj()
                got = 0
                buf = raw
                try:
                    while buf and got <= orig:
                        got += len(d.decompress(buf, orig + 1 - got))
                        buf = d.unconsumed_tail
                except zlib.error as e:
                    raise SkillError(f"{path.name}: damaged WOFF table {tag.decode('latin-1')} ({e})") from None
                if got > orig:
                    raise SkillError(f"{path.name}: table {tag.decode('latin-1')} inflates past its declared {orig:,} bytes (a possible zip bomb); refusing the font")
        elif sig == b"wOF2" and len(head) >= 48:
            total = struct.unpack(">I", head[16:20])[0]
            if total > limit:
                raise SkillError(f"{path.name}: the WOFF2 font declares {total / 1048576:.0f} MB of tables, over the {limit // 1048576} MB limit ({hint})")
            comp_size = struct.unpack(">I", head[20:24])[0]
            f.seek(0)
            data = f.read()
            start, expected = _woff2_stream(data)
            if start is None:
                return  # fontTools reports the damage
            try:
                import brotli
            except ImportError:  # pragma: no cover
                return
            dec = brotli.Decompressor()
            got = 0
            cap = min(limit, expected + (1 << 20))
            stream = data[start : start + comp_size]
            pos = 0
            try:
                while True:
                    if dec.can_accept_more_data():
                        if pos >= len(stream):
                            break
                        piece = stream[pos : pos + 65536]
                        pos += len(piece)
                    else:
                        piece = b""  # output is pending: drain it in 1 MB steps
                    got += len(dec.process(piece, output_buffer_limit=1 << 20))
                    if got > cap:
                        raise SkillError(f"{path.name}: the WOFF2 data inflates past its declared {expected:,} bytes (a possible bomb; {hint})")
                    if dec.is_finished():
                        break
            except brotli.error as e:
                raise SkillError(f"{path.name}: damaged WOFF2 data ({e})") from None
    _CHECKED.add(key)


def _woff2_stream(data: bytes) -> tuple[int | None, int]:
    """(offset of the Brotli stream, the size its table directory says it inflates to) of a WOFF2 file."""
    import struct

    def base128(i: int) -> tuple[int, int]:
        v = 0
        for k in range(5):
            b = data[i + k]
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                return v, i + k + 1
        raise ValueError("bad UIntBase128")

    def u255(i: int) -> tuple[int, int]:
        c = data[i]
        if c == 253:
            return struct.unpack(">H", data[i + 1 : i + 3])[0], i + 3
        if c == 254:
            return data[i + 1] + 253 * 2, i + 2
        if c == 255:
            return data[i + 1] + 253, i + 2
        return c, i + 1

    try:
        flavor = data[4:8]
        n = struct.unpack(">H", data[12:14])[0]
        i = 48
        expected = 0
        for _ in range(n):
            flags = data[i]
            i += 1
            tag_idx = flags & 0x3F
            tag = data[i : i + 4] if tag_idx == 0x3F else b""
            if tag_idx == 0x3F:
                i += 4
            orig, i = base128(i)
            version = (flags >> 6) & 3
            known = {10: b"glyf", 11: b"loca"}
            is_gl = tag in (b"glyf", b"loca") or tag_idx in known
            transformed = (version == 0 and is_gl) or (version != 0 and not is_gl)
            if transformed:
                tl, i = base128(i)
                expected += tl
            else:
                expected += orig
        if flavor == b"ttcf":
            i += 4  # version
            nfonts, i = u255(i)
            for _ in range(nfonts):
                nt, i = u255(i)
                i += 4
                for _ in range(nt):
                    _, i = u255(i)
        return i, expected
    except (IndexError, ValueError, struct.error):
        return None, 0


def open_font(path: Path, index: int = 0, lazy: bool = True) -> Any:
    """A fontTools TTFont (collections: font `index`). WOFF and WOFF2 are decompressed transparently, after
    check_font made sure they inflate to what they declare."""
    import logging

    from fontTools.ttLib import TTFont, TTLibError

    logging.getLogger("fontTools").setLevel(logging.ERROR)
    check_font(Path(path))
    try:
        return TTFont(str(path), fontNumber=index, lazy=lazy)
    except TTLibError as e:
        msg = str(e)
        if "specify a font number" in msg or "fontNumber" in msg:
            raise UsageError(f"{path.name} is a font collection; pick one font with --index") from None
        raise SkillError(f"{path.name}: not a font fontTools can read ({e})") from None
    except (OSError, AssertionError, ValueError, KeyError, IndexError) as e:
        raise SkillError(f"{path.name}: not a readable font ({type(e).__name__}: {e})") from None


def collection_size(path: Path) -> int:
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:4] == b"ttcf":
        return int.from_bytes(head[8:12], "big")
    return 1


def name_record(tt: Any, *ids: int) -> str | None:
    if "name" not in tt:
        return None
    name = tt["name"]
    for i in ids:
        rec = name.getDebugName(i)
        if rec:
            return str(rec).strip()
    return None


def font_format(tt: Any, path: Path) -> str:
    flavor = getattr(tt, "flavor", None)
    outline = "CFF2" if "CFF2" in tt else "CFF" if "CFF " in tt else "TrueType" if "glyf" in tt else "bitmap" if ("EBDT" in tt or "CBDT" in tt or "sbix" in tt) else "unknown"
    container = {"woff": "WOFF", "woff2": "WOFF2"}.get(flavor or "", "OTC/TTC collection" if path.suffix.lower() in (".ttc", ".otc") else "OpenType")
    return f"{container} ({outline} outlines)"


def font_summary(path: Path) -> dict[str, Any]:
    """Family, style, format and glyph count, for listings."""
    n = collection_size(path)
    tt = open_font(path, 0)
    try:
        return {
            "format": font_format(tt, path),
            "family": name_record(tt, 16, 1),
            "style": name_record(tt, 17, 2),
            "glyphs": tt["maxp"].numGlyphs if "maxp" in tt else None,
            **({"fonts_in_collection": n} if n > 1 else {}),
        }
    finally:
        tt.close()


# ── system fonts ────────────────────────────────────────────────────────


def font_dirs() -> list[Path]:
    from _img import system_font_dirs

    dirs = [Path(d) for d in system_font_dirs()]
    extra = os.environ.get("DESK_FONT_DIRS")
    if extra:
        dirs = [Path(d) for d in extra.split(os.pathsep) if d] + dirs
    return [d for d in dirs if d.is_dir()]


def system_font_files() -> list[Path]:
    files: list[Path] = []
    for d in font_dirs():
        try:
            for p in d.rglob("*"):
                if p.suffix.lower() in FONT_FILE_EXTS and p.is_file():
                    files.append(p)
        except OSError:
            continue
    return files


def save_font(tt: Any, out: Path, flavor: str | None, fast: bool = False) -> float:
    """Saves atomically (a .part file, then a rename) in `flavor` (None, woff or woff2). Returns the seconds spent.

    fast: WOFF2 with Brotli quality 9 instead of 11. For a 7 MB font that is 1 s instead of 30 s, for a file about 10%
    larger; subsets are small enough that quality 11 is quick.
    """
    import contextlib
    import time

    tt.flavor = flavor
    tmp = out.with_name(f".{out.name}.part")
    t0 = time.time()
    ctx: Any = contextlib.nullcontext()
    if flavor == "woff2" and fast:
        from fontTools.ttLib import woff2

        real = woff2.brotli

        class _Brotli:
            def __getattr__(self, name: str) -> Any:
                return getattr(real, name)

            def compress(self, data: bytes, mode: int = 0, quality: int = 11, **kw: Any) -> bytes:
                return real.compress(data, mode=mode, quality=min(quality, 9), **kw)

        @contextlib.contextmanager
        def patched() -> Any:
            woff2.brotli = _Brotli()
            try:
                yield
            finally:
                woff2.brotli = real

        ctx = patched()
    try:
        with ctx:
            tt.save(str(tmp))
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return time.time() - t0


def cmap_ranges(tt: Any) -> list[int] | None:
    """Covered code points as flat, merged [start, end, start, end, …] ranges, read from the raw cmap table.

    Much faster than fontTools' getBestCmap (no glyph names): the index can store every system font's coverage.
    Returns None when the table has no Unicode format 4 or 12 subtable (the caller falls back to fontTools).
    """
    import struct

    try:
        data = tt.reader["cmap"]
        n = struct.unpack_from(">H", data, 2)[0]
        subs: dict[tuple[int, int], int] = {}
        for i in range(n):
            pid, eid, off = struct.unpack_from(">HHI", data, 4 + 8 * i)
            fmt = struct.unpack_from(">H", data, off)[0]
            if (pid == 0 or (pid == 3 and eid in (1, 10))) and fmt in (4, 12, 13):
                subs.setdefault((fmt, pid), off)
        off12 = subs.get((12, 3), subs.get((12, 0)))
        off4 = subs.get((4, 3), subs.get((4, 0)))
        spans: list[tuple[int, int]] = []
        if off12 is not None:
            groups = struct.unpack_from(">I", data, off12 + 12)[0]
            for g in range(groups):
                a, b, gid = struct.unpack_from(">III", data, off12 + 16 + 12 * g)
                if gid == 0:
                    a += 1
                if a <= b:
                    spans.append((a, b))
        elif off4 is not None:
            seg = struct.unpack_from(">H", data, off4 + 6)[0] // 2
            ends = struct.unpack_from(f">{seg}H", data, off4 + 14)
            starts = struct.unpack_from(f">{seg}H", data, off4 + 16 + 2 * seg)
            deltas = struct.unpack_from(f">{seg}h", data, off4 + 16 + 4 * seg)
            ro_base = off4 + 16 + 6 * seg
            ros = struct.unpack_from(f">{seg}H", data, ro_base)
            for i in range(seg):
                a, b, delta, ro = starts[i], ends[i], deltas[i], ros[i]
                if a > b or a == 0xFFFF:
                    continue
                if ro == 0:
                    zero = (-delta) & 0xFFFF  # the one code point that maps to glyph 0
                    for x, y in ((a, min(b, zero - 1)), (max(a, zero + 1), b)) if a <= zero <= b else ((a, b),):
                        if x <= y:
                            spans.append((x, y))
                    continue
                gids = struct.unpack_from(f">{b - a + 1}H", data, ro_base + 2 * i + ro)
                first = last = -1
                for k, g in enumerate(gids):
                    if g and (g + delta) & 0xFFFF:
                        if first < 0:
                            first = a + k
                        last = a + k
                    elif first >= 0:
                        spans.append((first, last))
                        first = -1
                if first >= 0:
                    spans.append((first, last))
        elif (13, 0) in subs or (13, 3) in subs:
            return []  # a last-resort font (format 13): placeholder glyphs for everything, real coverage of nothing
        else:
            return None
    except (KeyError, struct.error, IndexError, TypeError):
        return None
    spans.sort()
    flat: list[int] = []
    for a, b in spans:
        if flat and a <= flat[-1] + 1:
            flat[-1] = max(flat[-1], b)
        else:
            flat += [a, b]
    return flat


def ranges_cover(flat: list[int], cps: set[int]) -> bool:
    """True when every code point in `cps` falls in the flat [start, end, …] ranges."""
    import bisect

    starts = flat[0::2]
    for cp in cps:
        i = bisect.bisect_right(starts, cp) - 1
        if i < 0 or cp > flat[2 * i + 1]:
            return False
    return True


def face_covers(face: dict[str, Any], cps: set[int]) -> bool:
    """Whether an indexed face has every code point (from its stored ranges; fontTools when there are none)."""
    if face.get("ranges") is not None:
        return ranges_cover(face["ranges"], cps)
    try:
        return cps <= cmap_of(face["path"], int(face["index"]))
    except (SkillError, UsageError):
        return False


def _index_file(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        n = collection_size(path)
    except OSError:
        return out
    for i in range(min(n, 64)):
        try:
            tt = open_font(path, i)
        except (SkillError, UsageError):
            break
        try:
            os2 = tt["OS/2"] if "OS/2" in tt else None
            out.append({
                "path": str(path),
                "index": i,
                "family": name_record(tt, 16, 1) or path.stem,
                "style": name_record(tt, 17, 2) or "Regular",
                "full": name_record(tt, 4) or "",
                "postscript": name_record(tt, 6) or "",
                "weight": int(getattr(os2, "usWeightClass", 400)) if os2 is not None else 400,
                "italic": bool(os2 is not None and (os2.fsSelection & 1)),
                "ranges": cmap_ranges(tt),
            })
        except Exception:  # noqa: BLE001 — a damaged font must not break the index
            pass
        finally:
            tt.close()
    return out


def font_index(refresh: bool = False) -> list[dict[str, Any]]:
    """Every system font face (cached in the temp folder, rebuilt when a font folder changes)."""
    dirs = font_dirs()
    from _cache import root

    sig_src = "index-v2|" + "|".join(f"{d}:{int(d.stat().st_mtime)}" for d in dirs)
    sig = hashlib.sha1(sig_src.encode()).hexdigest()[:16]
    folder = root() / "font-index"
    cache = folder / f"{sig}.json"
    if cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    from _common import pool_map

    files = system_font_files()
    faces: list[dict[str, Any]] = []
    for chunk in pool_map(_index_file, files, threads=True, workers=8):
        faces.extend(chunk)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(faces), encoding="utf-8")
        os.replace(tmp, cache)
        # Installing a font changes the signature: keep only the few most recent indexes (about 4 MB each).
        old = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[3:]
        for p in old:
            p.unlink()
    except OSError:
        pass
    return faces


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_faces(query: str, faces: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Faces whose family, full name or PostScript name matches `query` (exact matches first)."""
    faces = faces if faces is not None else font_index()
    q = _norm(query)
    exact = [f for f in faces if q in (_norm(f["family"]), _norm(f["full"]), _norm(f["postscript"]))]
    if exact:
        return exact
    return [f for f in faces if q in _norm(f["family"]) or q in _norm(f["full"])]


def pick_face(candidates: list[dict[str, Any]], bold: bool = False, italic: bool = False, weight: int | None = None) -> dict[str, Any] | None:
    if not candidates:
        return None
    want_w = weight or (700 if bold else 400)

    def score(f: dict[str, Any]) -> tuple[float, int]:
        s = abs(f["weight"] - want_w) + (0 if f["italic"] == italic else 1000)
        style = f["style"].lower()
        if not bold and not italic and style in ("regular", "normal", "roman", "book"):
            s -= 50
        return (s, len(f["full"]))

    return sorted(candidates, key=score)[0]


def resolve_font(spec: str | None, bold: bool = False, italic: bool = False) -> tuple[str, int] | None:
    """A font file and collection index from a path, a family name or a generic name (sans, serif, mono)."""
    if spec:
        p = Path(spec).expanduser()
        if p.suffix.lower() in FONT_FILE_EXTS or p.exists():
            if not p.is_file():
                raise SkillError(f"font file {spec} does not exist")
            return str(p), 0
    key = (spec or "sans").strip().lower()
    names = GENERIC.get(key, [spec] if spec else GENERIC["sans"])
    faces = font_index()
    for name in names:
        face = pick_face(find_faces(name, faces), bold, italic)
        if face:
            return face["path"], int(face["index"])
    if spec and key not in GENERIC:
        raise SkillError(f"no installed font matches '{spec}'; run font_tool.py find '{spec}' or pass a font file")
    return None


def cmap_of(path: str, index: int = 0) -> set[int]:
    tt = open_font(Path(path), index)
    try:
        cmap = tt.getBestCmap() or {}
        return set(cmap)
    finally:
        tt.close()


def missing_chars(text: str, cps: set[int]) -> list[str]:
    return sorted({ch for ch in text if not ch.isspace() and ord(ch) not in cps and ord(ch) >= 32})


def _ttf_for_pillow(path: str, index: int) -> tuple[str | bytes, int]:
    """Pillow's FreeType may not read WOFF2: hand it the decompressed font as bytes (no temp file to clean up)."""
    if Path(path).suffix.lower() not in (".woff", ".woff2"):
        return path, index
    import io

    tt = open_font(Path(path), index, lazy=False)
    try:
        tt.flavor = None
        buf = io.BytesIO()
        tt.save(buf)
    finally:
        tt.close()
    return buf.getvalue(), 0


def pil_font(spec: str | None, size: float, text: str = "", bold: bool = False, italic: bool = False) -> tuple[Any, str, list[str]]:
    """A Pillow font for `text`: the requested font, else a system font that covers every character, else Pillow's
    built-in font. Returns (font, description, characters no font covers)."""
    from PIL import ImageFont

    size = max(1.0, float(size))
    chosen = resolve_font(spec, bold, italic)
    missing: list[str] = []
    if chosen:
        cps = cmap_of(*chosen) if text else set()
        missing = missing_chars(text, cps) if text else []
        if missing:
            alt = cover_font(text, bold, italic)
            if alt:
                chosen, missing = alt, []
    elif text and any(ord(ch) > 0x24F for ch in text):
        alt = cover_font(text, bold, italic)
        if alt:
            chosen = alt
    if chosen:
        path, index = _ttf_for_pillow(*chosen)
        try:
            font = load_truetype(path, size, index)
            return font, f"{Path(chosen[0]).name}" + (f"#{chosen[1]}" if chosen[1] else ""), missing
        except OSError as e:
            if spec and Path(spec).exists():
                raise SkillError(f"cannot load font {spec}: {e}") from None
    try:
        return ImageFont.load_default(size=size), "Pillow built-in font", missing_chars(text, set(range(32, 0x250)))
    except TypeError:  # pragma: no cover — very old Pillow
        return ImageFont.load_default(), "Pillow built-in bitmap font", []


#: Pixel sizes of the bitmap strikes colour-emoji fonts usually carry (sbix, CBDT).
BITMAP_STRIKES = (160, 137, 128, 109, 96, 80, 72, 64, 52, 48, 40, 32, 26, 24, 20, 16)


def load_truetype(path: str | bytes, size: float, index: int = 0) -> Any:
    """ImageFont.truetype (from a path, or font bytes), falling back to the nearest bitmap strike for bitmap-only
    (colour emoji) fonts. The returned font's .size tells which size it really has; callers scale the rendering by
    size / font.size."""
    import io

    from PIL import ImageFont

    def src() -> Any:
        return io.BytesIO(path) if isinstance(path, bytes) else path

    try:
        return ImageFont.truetype(src(), size=size, index=index, layout_engine=ImageFont.Layout.BASIC)
    except OSError as e:
        if "pixel size" not in str(e):
            raise
    order = sorted(BITMAP_STRIKES, key=lambda s: (s < size, abs(s - size)))
    for s in order:
        try:
            return ImageFont.truetype(src(), size=s, index=index, layout_engine=ImageFont.Layout.BASIC)
        except OSError:
            continue
    raise OSError(("this font" if isinstance(path, bytes) else Path(path).name) + " has no usable size")


def cover_font(text: str, bold: bool = False, italic: bool = False, budget: int = 400) -> tuple[str, int] | None:
    """A system font whose cmap covers every non-space character of `text`."""
    need = {ord(ch) for ch in text if not ch.isspace() and ord(ch) >= 32}
    if not need:
        return None
    faces = font_index()
    tried: set[tuple[str, int]] = set()
    order: list[dict[str, Any]] = []
    for name in WIDE_COVERAGE:
        f = pick_face(find_faces(name, faces), bold, italic)
        if f:
            order.append(f)
    order += sorted(faces, key=lambda f: (f["italic"] != italic, abs(f["weight"] - (700 if bold else 400))))
    for f in order[:budget]:
        key = (f["path"], int(f["index"]))
        if key in tried:
            continue
        tried.add(key)
        if face_covers(f, need):
            return key
    return None
