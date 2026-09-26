"""Helpers shared by the pdf-toolkit scripts: opening PDFs, page geometry, units, colors, rendering and writing.

Coordinates shown to the agent are *view points*: PDF points (1/72 in) measured from the top-left corner of the
page as it is displayed (crop box, rotation applied), so they match rendered PNGs (pixel = point * dpi / 72).
Raw PDF user space (bottom-left origin, unrotated) stays internal. Heavy libraries are imported lazily.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError, UsageError, input_file

PDF_EXTS = {".pdf"}

#: Paper sizes in points (portrait).
PAPER: dict[str, tuple[float, float]] = {
    "a0": (2383.94, 3370.39),
    "a1": (1683.78, 2383.94),
    "a2": (1190.55, 1683.78),
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "a6": (297.64, 419.53),
    "b4": (708.66, 1000.63),
    "b5": (498.90, 708.66),
    "letter": (612.0, 792.0),
    "us-letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "us-legal": (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
    "ledger": (1224.0, 792.0),
    "executive": (521.86, 756.0),
}

_UNITS = {"pt": 1.0, "in": 72.0, "inch": 72.0, "mm": 72.0 / 25.4, "cm": 72.0 / 2.54, "px": 0.75}

COLORS = {
    "black": (0, 0, 0),
    "white": (1, 1, 1),
    "red": (0.8, 0.1, 0.1),
    "green": (0.1, 0.55, 0.2),
    "blue": (0.1, 0.3, 0.8),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
    "lightgray": (0.8, 0.8, 0.8),
    "yellow": (1.0, 0.85, 0.0),
    "orange": (0.95, 0.5, 0.1),
    "navy": (0.1, 0.15, 0.4),
}


# ── output ──────────────────────────────────────────────────────────────


def emit(data: Any, fmt: str = "json", render: Any = None, max_chars: int | None = 60_000, hint: str = "") -> None:
    """Like _common.emit, but JSON is never cut (a cut JSON cannot be parsed; scripts whose JSON can grow page
    their items themselves) and text is cut at the end of a line, saying how much was left out."""
    import json

    from _common import _json_default

    if fmt == "json" or render is None:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))
        return
    print(cut_lines(render(data), max_chars, hint))


def cut_lines(text: str, max_chars: int | None, hint: str = "") -> str:
    """`text` cut at the last line end before max_chars, with a note on what was left out."""
    if not max_chars or len(text) <= max_chars:
        return text
    k = text.rfind("\n", 0, max_chars)
    if k < max_chars // 2:
        k = max_chars
    rest = text[k:]
    return f"{text[:k]}\n[… truncated: {rest.count(chr(10)) + 1} more lines, {len(rest):,} characters.{' ' + hint if hint else ''}]"


# ── units, sizes, colors ────────────────────────────────────────────────


def plural(n: int, word: str) -> str:
    """'1 page', '3 pages'."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def parse_length(value: str | float, default_unit: str = "pt") -> float:
    """'36', '36pt', '0.5in', '2cm', '15mm' → points."""
    if isinstance(value, (int, float)):
        return float(value) * _UNITS[default_unit]
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*([a-z]*)\s*", str(value).lower())
    if not m:
        raise UsageError(f"bad length '{value}' (use e.g. 36pt, 0.5in, 12mm, 2cm)")
    unit = m.group(2) or default_unit
    if unit not in _UNITS:
        raise UsageError(f"unknown unit '{unit}' in '{value}' (pt, in, mm, cm)")
    return float(m.group(1)) * _UNITS[unit]


def parse_paper(value: str) -> tuple[float, float]:
    """A paper name (A4, Letter, Legal, A3, …) or 'WxH' with a unit ('210x297mm', '8.5x11in', '600x800')."""
    key = value.strip().lower()
    if key in PAPER:
        return PAPER[key]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*([a-z]*)", key)
    if not m:
        raise UsageError(f"unknown paper size '{value}' (A4, Letter, Legal, A3, A5, or WxH like 210x297mm)")
    unit = m.group(3) or "pt"
    return parse_length(m.group(1) + unit), parse_length(m.group(2) + unit)


def parse_color(value: str) -> tuple[float, float, float]:
    """'black', '#c00', '#cc0000' or 'r,g,b' (0-255) → RGB floats 0..1."""
    v = value.strip().lower()
    if v in COLORS:
        return COLORS[v]
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    parts = [p for p in re.split(r"[,\s]+", v) if p]
    if len(parts) == 3:
        try:
            return tuple(min(255, max(0, float(p))) / 255 for p in parts)  # type: ignore[return-value]
        except ValueError:
            pass
    raise UsageError(f"bad color '{value}' (a name like black or red, #rrggbb, or r,g,b)")


def color_hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def fmt_num(x: float) -> float | int:
    """A tidy number for output: integers stay integers, others get two decimals."""
    r = round(float(x), 2)
    return int(r) if r == int(r) else r


def paper_name(w: float, h: float) -> str:
    """'A4 portrait', 'Letter landscape' or 'custom' for a size in points."""
    for name in ("a4", "letter", "legal", "a3", "a5", "a6", "b5", "tabloid", "executive"):
        pw, ph = PAPER[name]
        label = name.upper() if name.startswith(("a", "b")) else name.capitalize()
        if abs(w - pw) < 2.5 and abs(h - ph) < 2.5:
            return f"{label} portrait"
        if abs(w - ph) < 2.5 and abs(h - pw) < 2.5:
            return f"{label} landscape"
    return "custom"


# ── geometry ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Geometry:
    """Maps between PDF user space and view points for one page (crop box l,b,r,t and /Rotate)."""

    l: float
    b: float
    r: float
    t: float
    rotation: int = 0

    @property
    def width(self) -> float:
        """Displayed width in points."""
        return (self.t - self.b) if self.rotation in (90, 270) else (self.r - self.l)

    @property
    def height(self) -> float:
        return (self.r - self.l) if self.rotation in (90, 270) else (self.t - self.b)

    def to_view(self, x: float, y: float) -> tuple[float, float]:
        rot = self.rotation
        if rot == 90:
            return y - self.b, x - self.l
        if rot == 180:
            return self.r - x, y - self.b
        if rot == 270:
            return self.t - y, self.r - x
        return x - self.l, self.t - y

    def from_view(self, vx: float, vy: float) -> tuple[float, float]:
        rot = self.rotation
        if rot == 90:
            return self.l + vy, self.b + vx
        if rot == 180:
            return self.r - vx, self.b + vy
        if rot == 270:
            return self.r - vy, self.t - vx
        return self.l + vx, self.t - vy

    def rect_to_view(self, l: float, b: float, r: float, t: float) -> tuple[float, float, float, float]:
        """A user-space rectangle → view [x0, top, x1, bottom]."""
        pts = [self.to_view(x, y) for x, y in ((l, b), (r, t))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def rect_from_view(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        """A view rectangle → user-space (l, b, r, t)."""
        pts = [self.from_view(x, y) for x, y in ((x0, y0), (x1, y1))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def overlay_matrix(self) -> tuple[float, float, float, float, float, float]:
        """The matrix that places a page of the displayed size (origin bottom-left) onto this page's user space."""
        rot = self.rotation
        if rot == 90:
            return (0, 1, -1, 0, self.r, self.b)
        if rot == 180:
            return (-1, 0, 0, -1, self.r, self.t)
        if rot == 270:
            return (0, -1, 1, 0, self.l, self.t)
        return (1, 0, 0, 1, self.l, self.b)


def geometry_of(page: Any) -> Geometry:
    """Geometry of a pypdf page (crop box, falling back to the media box)."""
    box = page.cropbox
    l, b, r, t = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    rot = int(page.get("/Rotate", 0) or 0) % 360
    if rot not in (0, 90, 180, 270):
        rot = 0
    return Geometry(min(l, r), min(b, t), max(l, r), max(b, t), rot)


def pdfium_geometry(page: Any) -> Geometry:
    """Geometry of a pypdfium2 page."""
    l, b, r, t = page.get_cropbox()
    return Geometry(min(l, r), min(b, t), max(l, r), max(b, t), int(page.get_rotation()) % 360)


def parse_box(spec: str, width: float, height: float) -> tuple[float, float, float, float]:
    """'x0,y0,x1,y1' in view points (or fractions of the page when every value is at most 1) → view rect."""
    try:
        vals = [float(v) for v in re.split(r"[,\s]+", spec.strip()) if v]
    except ValueError:
        raise UsageError(f"bad box '{spec}': use x0,y0,x1,y1 in points from the top-left, or fractions 0-1") from None
    if len(vals) != 4:
        raise UsageError(f"bad box '{spec}': need four numbers x0,y0,x1,y1")
    if all(0 <= v <= 1 for v in vals):
        vals = [vals[0] * width, vals[1] * height, vals[2] * width, vals[3] * height]
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        raise UsageError(f"bad box '{spec}': x1 must be greater than x0 and y1 greater than y0")
    box = max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1)
    if box[2] - box[0] < 0.5 or box[3] - box[1] < 0.5:
        raise UsageError(f"box '{spec}' is outside the page ({fmt_num(width)} × {fmt_num(height)} pt)")
    return box


def rects_intersect(a: Sequence[float], b: Sequence[float], pad: float = 0.0) -> bool:
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


def mat_mul(m: Sequence[float], n: Sequence[float]) -> tuple[float, float, float, float, float, float]:
    """m × n for PDF matrices [a b c d e f] (apply m first, then n)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def mat_apply(m: Sequence[float], x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def mat_invert(m: Sequence[float]) -> tuple[float, float, float, float, float, float] | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))


def bbox_of_points(pts: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


# ── opening and saving ──────────────────────────────────────────────────


def parse_pages(spec: str | None, count: int) -> list[int]:
    """Like _common.parse_ranges, but a range that runs past the last page stops there ('1-3' on a 1-page file)."""
    import sys

    from _common import parse_ranges

    try:
        return parse_ranges(spec, count)
    except UsageError as e:
        if not spec or "out of range" not in str(e):
            raise
        parts = []
        for part in spec.split(","):
            m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", part)
            if m and int(m.group(1)) <= count < int(m.group(2)):
                part = f"{m.group(1)}-{count}" if int(m.group(1)) < count else str(count)
            parts.append(part)
        pages = parse_ranges(",".join(parts), count)  # still fails for pages wholly past the end
        print(f"note: the PDF has {count} page{'s' if count != 1 else ''}; using pages {','.join(parts)}", file=sys.stderr)
        return pages


_MAGIC = [
    (b"\x89PNG", "a PNG image"), (b"\xff\xd8\xff", "a JPEG image"), (b"GIF8", "a GIF image"), (b"II*\x00", "a TIFF image"),
    (b"MM\x00*", "a TIFF image"), (b"PK\x03\x04", "a ZIP archive (or a .docx, .xlsx or .pptx)"), (b"\xd0\xcf\x11\xe0", "an old Office file (.doc, .xls, .ppt)"),
    (b"{\\rtf", "an RTF document"), (b"%!PS", "a PostScript file"), (b"AT&TFORM", "a DjVu document"), (b"\x1f\x8b", "a gzip archive"),
]


def pdf_input(path: str) -> Path:
    """An existing input that looks like a PDF (a "%PDF" header in its first 64 KB); a clear error otherwise."""
    p = input_file(path, PDF_EXTS)
    with open(p, "rb") as f:
        head = f.read(65536)
    if b"%PDF" not in head:
        if not head:
            raise SkillError(f"{p.name} is empty (0 bytes)")
        kind = next((what for magic, what in _MAGIC if head.startswith(magic)), None)
        if kind is None:
            text = head[:512].lstrip().lower()
            kind = "an HTML page" if text.startswith((b"<!doctype html", b"<html")) else "not a PDF (no %PDF header)"
        raise SkillError(f"{p.name} is {kind}, not a PDF" if kind.startswith("a") else f"{p.name} is {kind}; the file-inspector skill can identify it")
    return p


def open_reader(path: str | os.PathLike[str], password: str | None = None) -> Any:
    """A pypdf PdfReader, decrypted when needed; clear errors for damaged or locked files.

    When pypdf cannot parse the file (a damaged cross-reference table or page tree) or cannot decrypt it (an
    unusual security handler) but pdfium can open it, the reader holds a copy pdfium re-saved (repaired, and
    decrypted with the given password); a note on stderr says so. `reader._desk_repaired` then gives the reason
    and `reader._desk_was_encrypted` whether the original was encrypted.
    """
    import logging
    import sys

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # repair notes are noise for the agent

    name = Path(path).name
    problem = None
    encrypted = False
    reader = None
    try:
        reader = PdfReader(str(path))
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            try:
                ok = reader.decrypt(password or "")
            except Exception as e:  # noqa: BLE001 — unsupported crypto, bad data
                ok, problem = 0, f"cannot decrypt it ({e})"
            if not ok:
                problem = problem or "wrong password"
        if problem is None:
            if len(reader.pages) == 0:  # also parses the page tree now, so damage shows here and not halfway through
                raise SkillError(f"{name} has no pages")
            return reader
    except (PdfReadError, ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError) as e:
        problem = f"{type(e).__name__}: {e}"
    copy = _pdfium_copy(path, password)
    if copy is None and problem != "wrong password":
        rebuilt = rebuild_page_tree(path, password)
        if rebuilt is not None:
            from pypdf import PdfReader as _Reader

            copy = _Reader(io.BytesIO(rebuilt[0]))
            print(f"note: {name} has a broken page tree ({problem[:120]}); working on a copy rebuilt from the {plural(rebuilt[1], 'page')} that can be reached", file=sys.stderr)
            copy._desk_repaired = problem
            copy._desk_was_encrypted = encrypted
            return copy
    if copy is None:
        if problem == "wrong password":
            raise SkillError(f"{name} is encrypted: pass --password" if not password else f"wrong password for {name}")
        raise SkillError(f"cannot read {name} as a PDF ({problem}); it may be damaged. pdf_text.py can still try to read its text")
    if problem == "wrong password":  # pypdf's decryption failed where pdfium's worked
        print(f"note: pypdf could not decrypt {name}; working on a copy decrypted by pdfium", file=sys.stderr)
        copy._desk_repaired = None
    else:
        print(f"note: pypdf could not read {name} ({problem[:160]}); working on a copy repaired by pdfium", file=sys.stderr)
        copy._desk_repaired = problem
    copy._desk_was_encrypted = encrypted
    if reader is not None and getattr(reader, "_encryption", None) is not None:
        copy._desk_encryption = reader._encryption
    return copy


def _pdfium_copy(path: str | os.PathLike[str], password: str | None) -> Any:
    """The document re-saved by pdfium (repaired, without encryption) as a pypdf reader, or None."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from pypdf import PdfReader

    try:
        doc = pdfium.PdfDocument(str(path), password=password)
    except pdfium.PdfiumError:
        return None
    buf = io.BytesIO()
    try:
        doc.save(buf, flags=raw.FPDF_REMOVE_SECURITY)
    except Exception:  # noqa: BLE001
        return None
    finally:
        doc.close()
    try:
        reader = PdfReader(io.BytesIO(buf.getvalue()))
        len(reader.pages)
        return reader
    except Exception:  # noqa: BLE001
        return None


def rebuild_page_tree(path: str | os.PathLike[str], password: str | None = None) -> tuple[bytes, int, int] | None:
    """A copy whose page tree is rebuilt from the pages that can actually be reached (cycles and bad /Count values
    skipped), as (bytes, pages found, pages claimed); None when that is not possible either."""
    import logging

    from pypdf import PdfReader, PdfWriter
    from pypdf._page import PageObject
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, IndirectObject, NameObject

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        r = PdfReader(str(path), strict=False)
        if r.is_encrypted and not r.decrypt(password or ""):
            return None
        root = r.trailer["/Root"].get_object()
        top = root["/Pages"]
        claimed = int(top.get_object().get("/Count", 0) or 0)
        found: list[tuple[Any, Any, dict[str, Any]]] = []
        seen: set[int] = set()
        inheritable = ("/Resources", "/MediaBox", "/CropBox", "/Rotate")

        def walk(ref: Any, inherited: dict[str, Any], depth: int) -> None:
            if depth > 64 or len(found) > 200_000:
                return
            if isinstance(ref, IndirectObject):
                if ref.idnum in seen:
                    return
                seen.add(ref.idnum)
            node = ref.get_object()
            if not isinstance(node, DictionaryObject):
                return
            inh = {**inherited, **{k: node[k] for k in inheritable if k in node}}
            kids = node.get("/Kids")
            if node.get("/Type") != "/Page" and kids is not None:
                for kid in kids.get_object():
                    walk(kid, inh, depth + 1)
            elif node.get("/Type") == "/Page" or "/Contents" in node:
                found.append((ref, node, inh))

        walk(top, {}, 0)
        if not found:
            return None
        w = PdfWriter()
        for ref, node, inh in found:
            page = PageObject(r, ref if isinstance(ref, IndirectObject) else None)
            page.update({k: v for k, v in node.items() if k != "/Parent"})
            for k, v in inh.items():
                if k not in page:
                    page[NameObject(k)] = v
            if "/MediaBox" not in page:
                page[NameObject("/MediaBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(612), FloatObject(792)])
            w.add_page(page)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue(), len(found), claimed
    except Exception:  # noqa: BLE001 — too damaged to rebuild
        return None


def open_pdfium(path: str | os.PathLike[str], password: str | None = None) -> Any:
    """A pypdfium2 PdfDocument; clear errors for damaged or locked files."""
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(str(path), password=password)
    except pdfium.PdfiumError as e:
        msg = str(e)
        if "password" in msg.lower():
            raise SkillError(f"{Path(path).name} is encrypted: pass --password" if not password else f"wrong password for {Path(path).name}") from e
        raise SkillError(f"cannot open {Path(path).name} as a PDF ({msg}); it may be damaged or not a PDF. pdf_text.py can still try to read its text") from e


def init_forms(doc: Any) -> None:
    """Lets pdfium draw form fields; quiet about XFA forms (this pdfium draws only their AcroForm part)."""
    import logging

    logging.getLogger("pypdfium2").setLevel(logging.ERROR)
    try:
        doc.init_forms()
    except Exception:  # noqa: BLE001 — no forms support for this file
        pass


_SOURCES: dict[str, Path] = {}


def pdfium_source(path: str | os.PathLike[str], password: str | None = None) -> Path:
    """`path` when pdfium can open it; otherwise a copy re-saved by pypdf that pdfium opens (a note on stderr).

    pdfium gives up on a few damaged files that pypdf reads (a trailer without /Root, for example). Callers use
    the returned path for pdfium work and keep the original for messages and output checks. A file whose first or
    last page pdfium cannot load (a cyclic or miscounted page tree) is replaced by a copy with a rebuilt tree.
    """
    import sys

    import pypdfium2 as pdfium

    key = str(Path(path).resolve())
    if key in _SOURCES:
        return _SOURCES[key]
    try:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            n = len(doc)
            bad = None
            for i in sorted({0, n - 1}) if n else []:
                try:
                    doc[i].close()
                except pdfium.PdfiumError:
                    bad = i + 1
        finally:
            doc.close()
        if bad is None:
            _SOURCES[key] = Path(path)
            return Path(path)
        rebuilt = rebuild_page_tree(path, password)
        if rebuilt is None:
            _SOURCES[key] = Path(path)
            return Path(path)  # the page that fails is reported by the caller
        data = rebuilt[0]
        pdfium.PdfDocument(data).close()
        _SOURCES[key] = _temp_copy(Path(path).name, data)
        print(f"note: pdfium cannot load page {bad} of {Path(path).name} (a broken page tree: it claims {plural(rebuilt[2], 'page')}); working on a copy rebuilt from the {plural(rebuilt[1], 'page')} that can be reached", file=sys.stderr)
        return _SOURCES[key]
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            return Path(path)  # the caller reports it
    try:
        import logging

        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(password or ""):
            return Path(path)
        if len(reader.pages) == 0:
            raise SkillError(f"{Path(path).name} has no pages")
        w = new_writer(reader)
        data = writer_bytes(w)
        pdfium.PdfDocument(data).close()
    except SkillError:
        raise
    except Exception:  # noqa: BLE001 — pypdf cannot read it either: the caller reports pdfium's error
        return Path(path)
    copy = _temp_copy(Path(path).name, data)
    print(f"note: pdfium could not open {Path(path).name} (damaged); working on a copy repaired by pypdf", file=sys.stderr)
    _SOURCES[key] = copy
    return copy


def _temp_copy(name: str, data: bytes) -> Path:
    """Writes a repaired copy into a temp folder removed at exit."""
    import atexit
    import shutil

    folder = Path(tempfile.mkdtemp(prefix="desk-pdf-repaired-"))
    atexit.register(shutil.rmtree, folder, True)
    copy = folder / name
    copy.write_bytes(data)
    return copy


def new_writer(reader: Any) -> Any:
    """A pypdf PdfWriter holding a copy of the whole document (with a usable /Info)."""
    from pypdf import PdfWriter

    w = PdfWriter(clone_from=reader)
    fix_info(w)
    w._desk_src_version = pdf_version(getattr(reader, "pdf_header", ""))
    return w


def pdf_version(header: Any) -> str | None:
    """'1.5' from a header like '%PDF-1.5' (str or bytes); None when there is none."""
    if isinstance(header, bytes):
        header = header.decode("latin-1", "replace")
    m = re.search(r"%PDF-(\d\.\d)", str(header or ""))
    return m.group(1) if m else None


def fix_header(writer: Any) -> None:
    """Gives the output an honest version header: the source's version (1.7 when unknown), raised to what the
    features used need (1.4 for transparency, 1.7 with Adobe extension level 8 for AES-256). pypdf writes 1.3."""
    from pypdf.generic import DictionaryObject, NameObject, NumberObject

    version = getattr(writer, "_desk_src_version", None) or "1.7"
    need = "1.4"
    enc = getattr(writer, "_encryption", None)
    v = int(getattr(enc, "V", 0) or 0) if enc is not None else 0
    if v >= 5:
        need = "1.7"
    elif v == 4:
        need = "1.6"
    version = max(version, need, key=float)
    try:
        writer.pdf_header = f"%PDF-{version}".encode()
        if v >= 5 and version == "1.7":
            root = writer._root_object
            ext = DictionaryObject({NameObject("/ADBE"): DictionaryObject({NameObject("/BaseVersion"): NameObject("/1.7"), NameObject("/ExtensionLevel"): NumberObject(8)})})
            root[NameObject("/Extensions")] = ext
    except Exception:  # noqa: BLE001 — keep pypdf's header
        pass


def compress_streams(writer: Any, minimum: int = 128) -> int:
    """Flate-compresses every stream the writer holds unfiltered (content rewritten by pypdf: merged stamps,
    redacted pages, new forms), which pypdf would otherwise write raw, often 10x bigger. Returns how many."""
    import zlib

    from pypdf.generic import EncodedStreamObject, NameObject, StreamObject

    done = 0
    for i, o in enumerate(writer._objects):
        if not isinstance(o, StreamObject) or "/Filter" in o or o.get("/Type") == "/Metadata":
            continue  # XMP stays readable as it is (PDF/A requires it unfiltered)
        try:
            data = o.get_data()
        except Exception:  # noqa: BLE001 — leave an unreadable stream as it is
            continue
        if len(data) < minimum:
            continue
        enc = EncodedStreamObject()
        enc.update({k: v for k, v in o.items() if k not in ("/Length", "/Filter", "/DecodeParms")})
        enc[NameObject("/Filter")] = NameObject("/FlateDecode")
        enc._data = zlib.compress(data, 6)
        enc.indirect_reference = o.indirect_reference
        writer._objects[i] = enc
        done += 1
    return done


def fix_mode(path: Path) -> None:
    """Gives a written file the permissions a normal new file gets (temp files are created private, 0600)."""
    try:
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(path, 0o666 & ~mask)
    except OSError:
        pass


def fix_info(writer: Any) -> bool:
    """Replaces a broken /Info with an empty one; True when it did.

    Some files point /Info at a missing object or at a page-tree node, which makes pypdf fail when writing.
    """
    from pypdf.generic import DictionaryObject

    try:
        info = writer._info
        ok = info is None or (isinstance(info, DictionaryObject) and not _structural(info))
    except Exception:  # noqa: BLE001 — a dangling reference
        ok = False
    if not ok:
        writer._info_obj = writer._add_object(DictionaryObject())
    return not ok


def _structural(d: Any) -> bool:
    """True for a dictionary that is part of the document structure (a page-tree node or the catalog), not metadata."""
    return any(k in d for k in ("/Kids", "/Pages", "/Parent")) or str(d.get("/Type", "")) in ("/Pages", "/Page", "/Catalog")


def info_strings(reader: Any) -> dict[str, str]:
    """The document information entries that are plain strings (safe to copy into another file)."""
    out: dict[str, str] = {}
    try:
        meta = reader.metadata or {}
        if _structural(meta):
            return out
        for k, v in meta.items():
            v = v.get_object() if hasattr(v, "get_object") else v
            if isinstance(k, str) and isinstance(v, str):
                out[k] = str(v)
    except Exception:  # noqa: BLE001 — unreadable metadata
        pass
    return out


def fix_foreign_refs(writer: Any) -> int:
    """Pulls objects still referenced from the source file into the writer (pypdf leaves /Info values behind when
    cloning) and nulls references to objects that do not exist. Returns how many references it changed."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NullObject

    count = len(writer._objects)
    changed = 0

    def fix(obj: Any, depth: int) -> None:
        nonlocal changed
        if depth > 60:
            return
        items = list(obj.items()) if isinstance(obj, DictionaryObject) else list(enumerate(obj))
        for k, v in items:
            if isinstance(v, IndirectObject):
                if v.pdf is writer:
                    if not 1 <= v.idnum <= count or writer._objects[v.idnum - 1] is None:
                        obj[k] = NullObject()
                        changed += 1
                    continue
                try:
                    target = v.get_object()
                    clone = target.clone(writer) if target is not None else NullObject()
                    ref = getattr(clone, "indirect_reference", None)
                    obj[k] = ref if ref is not None and ref.pdf is writer else clone
                except Exception:  # noqa: BLE001 — unreadable object in the source
                    obj[k] = NullObject()
                changed += 1
            elif isinstance(v, (DictionaryObject, ArrayObject)):
                fix(v, depth + 1)

    for o in list(writer._objects):
        if isinstance(o, (DictionaryObject, ArrayObject)):
            fix(o, 0)
    return changed


def compress_writer(writer: Any) -> bool:
    """Merges duplicate objects and drops every object the document no longer reaches; False when pypdf could not
    (the file is still valid)."""
    try:
        fix_foreign_refs(writer)
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
        drop_unreachable(writer)
        return True
    except Exception:  # noqa: BLE001 — pypdf fails on some damaged object graphs; skip the optimisation
        return False


def drop_unreachable(writer: Any) -> int:
    """Removes objects that cannot be reached from the catalog or /Info; returns how many.

    pypdf's own clean-up keeps an orphan's children (a removed widget's appearance streams, an image only used by a
    replaced form), so content taken out of the pages could stay in the file.
    """
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    objs = writer._objects
    n = len(objs)
    seen = bytearray(n)
    stack: list[int] = [writer._root_object.indirect_reference.idnum]
    for ref in (getattr(writer, "_info_obj", None), getattr(writer, "_ID", None)):
        if isinstance(ref, IndirectObject):
            stack.append(ref.idnum)
        elif getattr(ref, "indirect_reference", None) is not None:
            stack.append(ref.indirect_reference.idnum)
    while stack:
        i = stack.pop()
        if not 1 <= i <= n or seen[i - 1]:
            continue
        seen[i - 1] = 1
        todo = [objs[i - 1]]
        while todo:
            o = todo.pop()
            if isinstance(o, DictionaryObject):
                vals = o.values()
            elif isinstance(o, ArrayObject):
                vals = o
            else:
                continue
            for v in vals:
                if isinstance(v, IndirectObject):
                    if v.pdf is writer and 1 <= v.idnum <= n and not seen[v.idnum - 1]:
                        stack.append(v.idnum)
                elif isinstance(v, (DictionaryObject, ArrayObject)):
                    todo.append(v)
    dropped = 0
    for i in range(n):
        if not seen[i] and objs[i] is not None:
            objs[i] = None
            dropped += 1
    return dropped


def save_writer(writer: Any, out: Path) -> int:
    """Writes a pypdf writer to `out` through a temp file in the same folder; returns the size in bytes."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fix_info(writer)
    fix_header(writer)
    compress_streams(writer)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
        os.replace(tmp, out)
        fix_mode(out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return out.stat().st_size


def save_bytes(data: bytes, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, out)
        fix_mode(out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def writer_bytes(writer: Any) -> bytes:
    fix_info(writer)
    fix_header(writer)
    compress_streams(writer)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def page_labels(reader: Any) -> list[str]:
    """Every page's label from /PageLabels, in one linear pass (pypdf's own is quadratic on big files)."""
    count = len(reader.pages)
    plain = [str(i + 1) for i in range(count)]
    try:
        tree = reader.root_object.get("/PageLabels")
        if tree is None:
            return plain
        entries: list[tuple[int, Any]] = []

        def walk(node: Any, depth: int) -> None:
            node = node.get_object()
            nums = node.get("/Nums")
            if nums is not None:
                nums = nums.get_object()
                for j in range(0, len(nums) - 1, 2):
                    entries.append((int(nums[j]), nums[j + 1].get_object()))
            if depth < 20:
                for kid in node.get("/Kids", []) or []:
                    walk(kid, depth + 1)

        walk(tree, 0)
        entries.sort(key=lambda e: e[0])
        labels = list(plain)
        for k, (start, spec) in enumerate(entries):
            end = entries[k + 1][0] if k + 1 < len(entries) else count
            style = str(spec.get("/S", ""))
            prefix = str(spec.get("/P", ""))
            first = int(spec.get("/St", 1))
            for i in range(max(0, start), min(end, count)):
                n = first + i - start
                if style == "/D":
                    body = str(n)
                elif style in ("/R", "/r"):
                    body = _roman(n) if n > 0 else str(n)
                    body = body.upper() if style == "/R" else body
                elif style in ("/A", "/a"):
                    body = chr(65 + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""
                    body = body if style == "/A" else body.lower()
                else:
                    body = ""
                labels[i] = prefix + body
        return labels
    except Exception:  # noqa: BLE001 — malformed /PageLabels
        return plain


def split_page_suffix(arg: str) -> tuple[str, str | None]:
    """'file.pdf:2-5' → ('file.pdf', '2-5'); a path that exists as given wins (Windows drive colons)."""
    if Path(arg).exists():
        return arg, None
    m = re.fullmatch(r"(.+?):((?:\d+|last)(?:-(?:\d+|last)?)?(?:,(?:\d+|last)?(?:-(?:\d+|last)?)?)*)", arg)
    if m and Path(m.group(1)).exists():
        return m.group(1), m.group(2)
    return arg, None


_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|conin\$|conout\$|clock\$|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3])")


def safe_filename(name: str, fallback: str = "file") -> str:
    """A file name without folders, traversal or characters Windows rejects. Windows opens a device for any name
    whose part before the first dot is reserved (con.tar.gz, nul.report.pdf), so those get a leading underscore;
    trailing dots and spaces, which Windows drops, are removed, also after the name is cut to 180 characters."""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in (".", ".."):
        name = fallback
    if _RESERVED.fullmatch(name.split(".", 1)[0].rstrip(" ")):
        name = "_" + name
    return name[:180].rstrip(" .") or fallback


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while (folder / f"{stem}-{i}{suffix}").exists():
        i += 1
    return folder / f"{stem}-{i}{suffix}"


# ── rendering ───────────────────────────────────────────────────────────


RENDER_CACHE_VERSION = "1"


def cached_value(path: str | os.PathLike[str], kind: str, params: dict[str, Any], version: str, compute: Any) -> Any:
    """compute()'s JSON result, cached by file content (skips the cache cleanly when it is disabled or full)."""
    import json
    import shutil

    from _cache import cached_dir, enabled, lookup, release

    if not enabled():
        return compute()
    hit = lookup(path, kind, params, version)
    if hit is not None:
        try:
            return json.loads((hit / "value.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            shutil.rmtree(hit, ignore_errors=True)
    box: dict[str, Any] = {}

    def build(tmp: Path) -> None:
        box["v"] = compute()
        (tmp / "value.json").write_text(json.dumps(box["v"], ensure_ascii=False), encoding="utf-8")

    d = cached_dir(path, kind, params, version, build)
    value = box["v"] if "v" in box else json.loads((d / "value.json").read_text(encoding="utf-8"))
    release(d)
    return value


def _render_job(job: dict[str, Any]) -> dict[str, Any]:
    """Renders one page, reusing a cached render of the same page and settings when there is one."""
    from _cache import enabled

    if job.get("password") or job.get("no_cache") or not enabled():
        return _render_now(job, job["out"])
    import json
    import shutil

    from _cache import cached_dir, release

    out = job["out"]
    ext = ".jpg" if out.lower().endswith((".jpg", ".jpeg")) else ".png"
    params = {k: job.get(k) for k in ("index", "dpi", "max_edge", "region", "forms", "boxes", "grid")}
    params["ext"] = ext

    def build(tmp: Path) -> None:
        meta = _render_now(job, str(tmp / f"page{ext}"))
        (tmp / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    d = cached_dir(job["pdf"], "pdf-render", params, RENDER_CACHE_VERSION, build)
    try:
        shutil.copyfile(d / f"page{ext}", out)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    finally:
        release(d)
    return {**meta, "path": out}


def _render_now(job: dict[str, Any], out: str) -> dict[str, Any]:
    """Renders one page (optionally a region, with a coordinate grid and boxes); used in worker processes."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(job["pdf"], password=job.get("password"))
    try:
        if job.get("forms", True):
            init_forms(doc)
        page = doc[job["index"]]
        vw, vh = page.get_size()
        region = job.get("region")
        rw, rh = (region[2] - region[0], region[3] - region[1]) if region else (vw, vh)
        scale = job["dpi"] / 72.0 if job.get("dpi") else 0.0
        max_edge = job.get("max_edge") or 0
        if max_edge:
            fit = max_edge / max(rw, rh)
            scale = min(scale, fit) if scale else fit
        crop = (region[0], vh - region[3], vw - region[2], region[1]) if region else (0, 0, 0, 0)
        bitmap = page.render(scale=scale, crop=crop, may_draw_forms=True, draw_annots=True)
        img = bitmap.to_pil()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        ox, oy = (region[0], region[1]) if region else (0.0, 0.0)
        if job.get("boxes"):
            _draw_boxes(img, job["boxes"], scale, ox, oy)
        if job.get("grid"):
            img = _draw_grid(img, float(job["grid"]), scale, ox, oy)
        if out.lower().endswith((".jpg", ".jpeg")):
            img.convert("RGB").save(out, quality=88)
        else:
            img.save(out)
        page.close()
        return {"path": out, "page": job["index"] + 1, "width": img.size[0], "height": img.size[1], "scale": scale}
    finally:
        doc.close()


def _draw_boxes(img: Any, boxes: list[dict[str, Any]], scale: float, ox: float, oy: float) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img, "RGBA")
    for box in boxes:
        x0, y0, x1, y1 = box["rect"]
        xy = [(x0 - ox) * scale, (y0 - oy) * scale, (x1 - ox) * scale, (y1 - oy) * scale]
        color = box.get("color", (220, 30, 30))
        if box.get("fill"):
            draw.rectangle(xy, fill=tuple(color) + (box.get("alpha", 90),), outline=tuple(color) + (255,), width=2)
        else:
            draw.rectangle(xy, outline=tuple(color) + (255,), width=max(2, round(scale)))


def _draw_grid(img: Any, step: float, scale: float, ox: float, oy: float) -> Any:
    """Overlays light grid lines every `step` view points, labelled in points, so coordinates can be read off."""
    from PIL import Image, ImageDraw

    from _render import _font

    base = img.convert("RGB")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(max(10, min(16, round(step * scale / 5))))
    w, h = base.size
    first_x = (int(ox // step) + 1) * step if ox % step else ox
    x = first_x
    while (x - ox) * scale < w:
        px = (x - ox) * scale
        major = round(x) % round(step * 2) == 0
        draw.line([(px, 0), (px, h)], fill=(0, 120, 255, 110 if major else 60), width=1)
        draw.text((px + 2, 2), f"{x:g}", fill=(0, 70, 200, 230), font=font)
        x += step
    first_y = (int(oy // step) + 1) * step if oy % step else oy
    y = first_y
    while (y - oy) * scale < h:
        py = (y - oy) * scale
        major = round(y) % round(step * 2) == 0
        draw.line([(0, py), (w, py)], fill=(0, 120, 255, 110 if major else 60), width=1)
        draw.text((2, py + 2), f"{y:g}", fill=(0, 70, 200, 230), font=font)
        y += step
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def render_pages(jobs: list[dict[str, Any]], workers: int | None = None) -> list[dict[str, Any]]:
    """Runs render jobs, in parallel processes when there are many."""
    from _common import pool_map

    return pool_map(_render_job, jobs, workers=workers if len(jobs) >= 6 else 1)
