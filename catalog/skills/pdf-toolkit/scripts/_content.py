"""Content-stream engine for pdf-toolkit: glyph-level text removal (true redaction) and image placement scans.

The interpreter follows the PDF graphics and text state (q/Q, cm, BT/ET, Tf, Tc, Tw, Tz, TL, Ts, Td, TD, Tm, T*,
Tj, TJ, ', "), computes every glyph's position from the font's widths, and can drop the glyphs whose centre lies in
a redaction rectangle. A removed glyph is replaced by a TJ spacing number of the same advance, so the rest of the
line stays exactly where it was. Images under a rectangle get their pixels painted over (new image objects, so
shared images elsewhere are untouched), covered paths and inline images are dropped, and form XObjects are
processed recursively (copied first when modified). Anything it cannot do exactly raises Unsupported, and the
caller falls back to rasterising that page.
"""

from __future__ import annotations

import io
import math
from typing import Any, Callable, Iterable, Sequence

from _pdfkit import mat_apply, mat_invert, mat_mul

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
PAINT_OPS = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}
PATH_OPS = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}


class Unsupported(Exception):
    """This page cannot be redacted exactly (rasterise it instead)."""


# ── fonts ───────────────────────────────────────────────────────────────


class FontMetrics:
    """Splits a shown string into character codes and gives each code's advance (text space units, size 1)."""

    def __init__(self, font: Any) -> None:
        from pypdf._font import Font

        self.subtype = str(font.get("/Subtype", ""))
        self.two_byte = False
        self.trie: dict[int, Any] | None = None
        self.scale = 0.001
        self.vertical = False
        try:
            f = Font.from_font_resource(font)
        except Exception as e:  # noqa: BLE001
            raise Unsupported(f"font {font.get('/BaseFont')}: {e}") from e
        self.widths = f.character_widths
        self.default = float(self.widths.get("default", 500) or 500)
        asc = float(getattr(f.font_descriptor, "ascent", 700) or 700)
        desc = float(getattr(f.font_descriptor, "descent", -200) or -200)
        if self.subtype == "/Type3":
            fm = [float(v) for v in font.get("/FontMatrix", [0.001, 0, 0, 0.001, 0, 0])]
            if abs(fm[1]) > 1e-9 or abs(fm[2]) > 1e-9:
                raise Unsupported("a Type3 font with a rotated or skewed font matrix")
            self.scale = fm[0]
            bbox = [float(v) for v in font.get("/FontBBox", [0, -200, 1000, 800])]
            asc, desc = bbox[3] * fm[3] / 0.001, bbox[1] * fm[3] / 0.001
        if asc - desc < 100:
            asc, desc = 750.0, -250.0
        self.ascent = asc / 1000.0
        self.descent = desc / 1000.0
        if self.subtype == "/Type0":
            enc = font.get("/Encoding")
            enc_obj = enc.get_object() if enc is not None else None
            name = str(enc_obj) if enc_obj is not None and not hasattr(enc_obj, "get_data") else ""
            if name in ("/Identity-H", "/Identity-V"):
                self.two_byte = True
                self.vertical = name.endswith("-V")
            elif name.startswith("/"):
                from pdfminer.cmapdb import CMapDB

                try:
                    cmap = CMapDB.get_cmap(name[1:])
                except Exception as e:  # noqa: BLE001
                    raise Unsupported(f"unknown CMap {name}") from e
                self.trie = getattr(cmap, "code2cid", None)
                self.vertical = bool(getattr(cmap, "is_vertical", lambda: False)())
                if not self.trie:
                    raise Unsupported(f"CMap {name} has no code map")
            elif enc_obj is not None and hasattr(enc_obj, "get_data"):
                from pdfminer.cmapdb import CMap, CMapParser

                cmap = CMap()
                try:
                    CMapParser(cmap, io.BytesIO(enc_obj.get_data())).run()
                except Exception as e:  # noqa: BLE001
                    raise Unsupported(f"unreadable embedded CMap: {e}") from e
                if not cmap.code2cid:
                    raise Unsupported("empty embedded CMap")
                self.trie = cmap.code2cid
                self.vertical = bool(enc_obj.get("/WMode", 0))
            else:
                raise Unsupported("a composite font without a usable encoding")
            if self.vertical:
                raise Unsupported("vertical writing")

    def split(self, data: bytes) -> list[tuple[bytes, int]]:
        """(code bytes, width key) for each character code in the string."""
        if self.subtype != "/Type0":
            return [(bytes([b]), b) for b in data]
        if self.two_byte:
            if len(data) % 2:
                raise Unsupported("an odd-length string in a two-byte font")
            return [(data[i : i + 2], (data[i] << 8) | data[i + 1]) for i in range(0, len(data), 2)]
        out = []
        i = 0
        trie = self.trie or {}
        while i < len(data):
            node: Any = trie
            j = i
            while j < len(data) and isinstance(node, dict) and data[j] in node:
                node = node[data[j]]
                j += 1
                if not isinstance(node, dict):
                    break
            if isinstance(node, dict) or j == i:
                j = i + 1  # unmapped byte: treat as a one-byte code with the default width
                cid = 0
            else:
                cid = int(node)
            out.append((data[i:j], cid))
            i = j
        return out

    def width(self, key: int) -> float:
        w = self.widths.get(chr(key)) if 0 <= key < 0x110000 else None
        if w is None:
            w = self.default
        return float(w) * self.scale

    def single_space(self, code: bytes) -> bool:
        return len(code) == 1 and code == b" "


# ── helpers ─────────────────────────────────────────────────────────────


def raw_bytes(s: Any) -> bytes:
    ob = getattr(s, "original_bytes", None)
    if ob is not None:
        return bytes(ob)
    if isinstance(s, bytes):
        return bytes(s)
    return str(s).encode("latin-1", "replace")


def point_in(rects: Sequence[Sequence[float]], x: float, y: float) -> bool:
    for r in rects:
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return True
    return False


def bbox_of(m: Sequence[float], x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    pts = [mat_apply(m, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside(a: Sequence[float], b: Sequence[float], tol: float = 0.5) -> bool:
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] <= b[3] + tol


def resolve(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


# ── the redactor ────────────────────────────────────────────────────────


class Redactor:
    """Removes content under user-space rectangles on one page of a pypdf PdfWriter."""

    def __init__(self, writer: Any, rects: list[tuple[float, float, float, float]], scrub: Callable[[str], str] | None = None) -> None:
        self.w = writer
        self.rects = rects
        self.scrub = scrub
        self.fonts: dict[int, FontMetrics] = {}
        self.stats = {"glyphs": 0, "images_changed": 0, "images_removed": 0, "inline_images": 0, "paths": 0, "forms": 0, "marked_text": 0}
        self.counter = 0
        #: Object numbers of images and forms this redactor replaced or dropped somewhere (the originals may
        #: still be listed in other pages' resources; pdf_redact removes those unused references).
        self.replaced: set[int] = set()

    # -- entry point --

    def redact_page(self, page: Any) -> bool:
        from pypdf.generic import ContentStream, DictionaryObject, NameObject

        contents = page.get_contents()
        if contents is None:
            return False
        res = page.get("/Resources")
        if res is None:
            res = page.get_inherited("/Resources", None) if hasattr(page, "get_inherited") else None
        res = resolve(res) if res is not None else DictionaryObject()
        holder = {"res": res, "own": False, "set": lambda new: page.__setitem__(NameObject("/Resources"), new)}
        ops = contents.operations if isinstance(contents, ContentStream) else ContentStream(contents, self.w).operations
        new_ops, changed = self.run(ops, holder, IDENTITY)
        if changed:
            cs = ContentStream(None, self.w)
            cs.operations = new_ops
            page.replace_contents(cs)
        return changed

    # -- resources --

    def _own(self, holder: dict[str, Any]) -> Any:
        """Makes the resources dict private to its owner before a change (it may be shared)."""
        from pypdf.generic import DictionaryObject, NameObject

        if not holder["own"]:
            new = DictionaryObject({k: v for k, v in holder["res"].items()})
            xo = new.get("/XObject")
            new[NameObject("/XObject")] = DictionaryObject({k: v for k, v in (resolve(xo).items() if xo is not None else [])})
            holder["set"](new)
            holder["res"] = new
            holder["own"] = True
        return holder["res"]

    def _font(self, res: Any, name: Any) -> FontMetrics:
        fonts = res.get("/Font")
        ref = resolve(fonts).get(name) if fonts is not None else None
        if ref is None:
            raise Unsupported(f"font {name} is missing from the resources")
        key = getattr(ref, "idnum", None) or id(resolve(ref))
        fm = self.fonts.get(key)
        if fm is None:
            fm = self.fonts[key] = FontMetrics(resolve(ref))
        return fm

    # -- interpreter --

    def run(self, ops: list[tuple[Any, bytes]], holder: dict[str, Any], ctm0: Sequence[float]) -> tuple[list[tuple[Any, bytes]], bool]:
        from pypdf.generic import ArrayObject, ByteStringObject, DictionaryObject, FloatObject, NameObject, TextStringObject

        res = holder["res"]
        ctm = tuple(ctm0)
        ts = {"Tc": 0.0, "Tw": 0.0, "Th": 1.0, "TL": 0.0, "font": None, "fs": 0.0, "Ts": 0.0}
        stack: list[tuple[Any, dict[str, Any]]] = []
        tm = tlm = IDENTITY
        out: list[tuple[Any, bytes]] = []
        changed = False
        path_start: int | None = None
        path_pts: list[tuple[float, float]] = []
        path_clip = False
        rects = self.rects

        def num(x: Any) -> float:
            return float(x)

        def show(data: bytes) -> tuple[list[Any], bool]:
            """Advances the text matrix over `data`; returns TJ items without removed glyphs, and whether any were removed."""
            nonlocal tm
            fm: FontMetrics | None = ts["font"]
            if fm is None:
                raise Unsupported("text shown without a font")
            fs, th, tc, tw, rise = ts["fs"], ts["Th"], ts["Tc"], ts["Tw"], ts["Ts"]
            items: list[Any] = []
            buf = b""
            removed = False
            cy = (fm.ascent + fm.descent) / 2
            for code, key in fm.split(data):
                w0 = fm.width(key)
                trm = mat_mul((fs * th, 0, 0, fs, 0, rise), mat_mul(tm, ctm))
                cx, cyy = mat_apply(trm, w0 / 2, cy)
                adv = (w0 * fs + tc + (tw if fm.single_space(code) else 0.0)) * th
                if point_in(rects, cx, cyy):
                    if fs == 0 or th == 0:
                        raise Unsupported("a zero font size or scale")
                    if buf:
                        items.append(buf)
                        buf = b""
                    items.append(-adv * 1000.0 / (fs * th))
                    removed = True
                    self.stats["glyphs"] += 1
                else:
                    buf += code
                tm = mat_mul((1, 0, 0, 1, adv, 0), tm)
            if buf:
                items.append(buf)
            return items, removed

        def tj_op(items: list[Any]) -> tuple[Any, bytes]:
            arr = ArrayObject()
            for it in items:
                if isinstance(it, bytes):
                    arr.append(ByteStringObject(it))
                else:
                    if arr and isinstance(arr[-1], FloatObject):
                        arr[-1] = FloatObject(round(float(arr[-1]) + it, 3))
                    else:
                        arr.append(FloatObject(round(it, 3)))
            return ([arr], b"TJ")

        def next_line() -> None:
            nonlocal tm, tlm
            tlm = mat_mul((1, 0, 0, 1, 0, -ts["TL"]), tlm)
            tm = tlm

        for operands, op in ops:
            if op in PATH_OPS:
                if path_start is None:
                    path_start = len(out)
                    path_pts = []
                    path_clip = False
                o = [num(x) for x in operands]
                if op == b"re" and len(o) == 4:
                    x, y, w_, h_ = o
                    path_pts += [mat_apply(ctm, x, y), mat_apply(ctm, x + w_, y + h_), mat_apply(ctm, x + w_, y), mat_apply(ctm, x, y + h_)]
                else:
                    for i in range(0, len(o) - 1, 2):
                        path_pts.append(mat_apply(ctm, o[i], o[i + 1]))
                out.append((operands, op))
                continue
            if op in (b"W", b"W*"):
                path_clip = True
                out.append((operands, op))
                continue
            if op in PAINT_OPS:
                if path_start is not None and op != b"n" and not path_clip and path_pts:
                    xs, ys = [p[0] for p in path_pts], [p[1] for p in path_pts]
                    pb = (min(xs), min(ys), max(xs), max(ys))
                    if any(inside(pb, r) for r in rects):
                        del out[path_start:]
                        self.stats["paths"] += 1
                        changed = True
                        path_start = None
                        continue
                path_start = None
                out.append((operands, op))
                continue
            path_start = None if op not in (b"W", b"W*") else path_start

            if op == b"q":
                stack.append((ctm, dict(ts)))
            elif op == b"Q":
                if stack:
                    ctm, ts = stack.pop()
            elif op == b"cm" and len(operands) == 6:
                ctm = mat_mul([num(x) for x in operands], ctm)
            elif op == b"BT":
                tm = tlm = IDENTITY
            elif op == b"Tc":
                ts["Tc"] = num(operands[0])
            elif op == b"Tw":
                ts["Tw"] = num(operands[0])
            elif op == b"Tz":
                ts["Th"] = num(operands[0]) / 100.0
            elif op == b"TL":
                ts["TL"] = num(operands[0])
            elif op == b"Ts":
                ts["Ts"] = num(operands[0])
            elif op == b"Tf":
                ts["font"] = self._font(res, operands[0])
                ts["fs"] = num(operands[1])
            elif op in (b"Td", b"TD"):
                tx, ty = num(operands[0]), num(operands[1])
                if op == b"TD":
                    ts["TL"] = -ty
                tlm = mat_mul((1, 0, 0, 1, tx, ty), tlm)
                tm = tlm
            elif op == b"Tm":
                tm = tlm = tuple(num(x) for x in operands)  # type: ignore[assignment]
            elif op == b"T*":
                next_line()
            elif op in (b"Tj", b"'", b'"'):
                if op == b'"':
                    ts["Tw"], ts["Tc"] = num(operands[0]), num(operands[1])
                    data = raw_bytes(operands[2])
                else:
                    data = raw_bytes(operands[0])
                if op != b"Tj":
                    next_line()
                items, removed = show(data)
                if removed:
                    changed = True
                    if op == b'"':
                        out.append(([FloatObject(ts["Tw"])], b"Tw"))
                        out.append(([FloatObject(ts["Tc"])], b"Tc"))
                    if op != b"Tj":
                        out.append(([], b"T*"))
                    out.append(tj_op(items))
                    continue
            elif op == b"TJ":
                arr = operands[0] if operands else []
                new_items: list[Any] = []
                removed_any = False
                fs, th = ts["fs"], ts["Th"]
                for el in arr:
                    el = resolve(el)
                    if isinstance(el, (int, float)) or type(el).__name__ in ("FloatObject", "NumberObject"):
                        n = float(el)
                        tm = mat_mul((1, 0, 0, 1, -n / 1000.0 * fs * th, 0), tm)
                        new_items.append(n)  # spacing numbers are kept as they are
                    else:
                        items, removed = show(raw_bytes(el))
                        removed_any = removed_any or removed
                        new_items.extend(items)
                if removed_any:
                    changed = True
                    out.append(tj_op(new_items))
                    continue
            elif op == b"Do":
                name = operands[0]
                xobjs = res.get("/XObject")
                ref = resolve(xobjs).get(name) if xobjs is not None else None
                if ref is not None:
                    xo = resolve(ref)
                    st = xo.get("/Subtype")
                    if st == "/Image":
                        box = bbox_of(ctm, 0, 0, 1, 1)
                        hit = [r for r in rects if overlaps(box, r)]
                        if hit:
                            changed = True
                            if getattr(ref, "idnum", None):
                                self.replaced.add(ref.idnum)
                            if any(inside(box, r, 0.5) for r in hit):
                                self.stats["images_removed"] += 1
                                continue
                            new_ref = self._paint_image(xo, ctm, hit)
                            new_name = self._register(holder, new_ref, "DeskImg")
                            out.append(([new_name], b"Do"))
                            self.stats["images_changed"] += 1
                            continue
                    elif st == "/Form":
                        fm_ = [num(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                        fctm = mat_mul(fm_, ctm)
                        bb = [num(x) for x in xo.get("/BBox", [0, 0, 0, 0])]
                        box = bbox_of(fctm, *bb) if len(bb) == 4 else None
                        if box is not None and any(overlaps(box, r) for r in rects):
                            if getattr(ref, "idnum", None):
                                self.replaced.add(ref.idnum)
                            if any(inside(box, r, 0.5) for r in rects):
                                changed = True
                                self.stats["forms"] += 1
                                continue
                            new_ref = self._redact_form(xo, fctm, res)
                            if new_ref is not None:
                                changed = True
                                new_name = self._register(holder, new_ref, "DeskForm")
                                out.append(([new_name], b"Do"))
                                self.stats["forms"] += 1
                                continue
            elif op == b"INLINE IMAGE":
                box = bbox_of(ctm, 0, 0, 1, 1)
                if any(overlaps(box, r) for r in rects):
                    changed = True
                    self.stats["inline_images"] += 1
                    continue
            elif op in (b"BDC", b"DP") and self.scrub is not None and len(operands) == 2:
                props = resolve(operands[1])
                if isinstance(props, DictionaryObject):
                    for key in ("/ActualText", "/Alt", "/E"):
                        if key in props:
                            val = str(props[key])
                            new_val = self.scrub(val)
                            if new_val != val:
                                props[NameObject(key)] = TextStringObject(new_val)
                                self.stats["marked_text"] += 1
                                changed = True
            out.append((operands, op))
        return out, changed

    # -- forms and images --

    def _register(self, holder: dict[str, Any], ref: Any, prefix: str) -> Any:
        from pypdf.generic import DictionaryObject, NameObject

        res = self._own(holder)
        xo = res.get("/XObject")
        if xo is None:
            xo = DictionaryObject()
            res[NameObject("/XObject")] = xo
        xo = resolve(xo)
        while True:
            self.counter += 1
            name = NameObject(f"/{prefix}{self.counter}")
            if name not in xo:
                break
        xo[name] = ref
        return name

    def _redact_form(self, xo: Any, fctm: Sequence[float], parent_res: Any) -> Any:
        from pypdf.generic import ContentStream, DecodedStreamObject, NameObject

        res = xo.get("/Resources")
        res = resolve(res) if res is not None else parent_res
        new = DecodedStreamObject()
        for k, v in xo.items():
            if k not in ("/Filter", "/DecodeParms", "/Length"):
                new[NameObject(k)] = v
        holder = {"res": res, "own": False, "set": lambda r: new.__setitem__(NameObject("/Resources"), r)}
        ops = ContentStream(xo, self.w).operations
        new_ops, changed = self.run(ops, holder, fctm)
        if not changed:
            return None
        cs = ContentStream(None, self.w)
        cs.operations = new_ops
        new.set_data(cs.get_data())
        return self.w._add_object(new)

    def _paint_image(self, xo: Any, ctm: Sequence[float], hit: list[Sequence[float]]) -> Any:
        """A copy of the image with the covered pixels painted over (and made opaque in its soft mask)."""
        from PIL import Image, ImageDraw
        from pypdf.generic import DecodedStreamObject, NameObject, NumberObject
        from pypdf.generic._image_xobject import _xobj_to_image

        w = int(xo.get("/Width", 0))
        h = int(xo.get("/Height", 0))
        inv = mat_invert(ctm)
        if inv is None or w <= 0 or h <= 0:
            raise Unsupported("a degenerate image placement")
        pixel_boxes = []
        for r in hit:
            pts = [mat_apply(inv, x, y) for x, y in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3]))]
            us, vs = [p[0] for p in pts], [p[1] for p in pts]
            x0, x1 = max(0.0, min(us)) * w, min(1.0, max(us)) * w
            y0, y1 = (1 - min(1.0, max(vs))) * h, (1 - max(0.0, min(vs))) * h
            if x1 > x0 and y1 > y0:
                pixel_boxes.append((math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)))
        is_mask = bool(xo.get("/ImageMask", False))
        filters = xo.get("/Filter")
        flist = [str(f) for f in (filters if isinstance(filters, list) else [filters] if filters else [])]
        if any(f in ("/JBIG2Decode",) for f in flist):
            raise Unsupported("a JBIG2 image")
        cs_obj = resolve(xo.get("/ColorSpace")) if xo.get("/ColorSpace") is not None else None
        if not is_mask and isinstance(cs_obj, list) and cs_obj and str(cs_obj[0]) == "/Indexed" and int(xo.get("/BitsPerComponent", 8) or 8) == 8 and not any(f in ("/DCTDecode", "/JPXDecode") for f in flist):
            return self._paint_indexed(xo, cs_obj, w, h, pixel_boxes)
        smask_ref = xo.get("/SMask")
        try:
            if smask_ref is not None:
                del xo["/SMask"]  # decode the base image alone; restored below
            _ext, _raw, img = _xobj_to_image(xo)
        except Exception as e:  # noqa: BLE001
            raise Unsupported(f"an image that cannot be decoded ({e})") from e
        finally:
            if smask_ref is not None:
                xo[NameObject("/SMask")] = smask_ref
        if img is None:
            raise Unsupported("an image that cannot be decoded")
        if is_mask:
            img = img.convert("1")
            decode = [float(v) for v in xo.get("/Decode", [0, 1])]
            unpainted = 1 if decode[0] == 0 else 0  # sample value that leaves the page alone
            draw = ImageDraw.Draw(img)
            for b in pixel_boxes:
                draw.rectangle([b[0], b[1], b[2] - 1, b[3] - 1], fill=unpainted)
            data = img.tobytes()
            new = DecodedStreamObject()
            for k, v in xo.items():
                if k not in ("/Filter", "/DecodeParms", "/Length"):
                    new[NameObject(k)] = v
            new.set_data(data)
            new = new.flate_encode()
            return self.w._add_object(new)
        mode = "L" if img.mode in ("L", "LA", "1") else "CMYK" if img.mode == "CMYK" else "RGB"
        base = img.convert(mode)
        draw = ImageDraw.Draw(base)
        black = 0 if mode == "L" else (0, 0, 0, 255) if mode == "CMYK" else (0, 0, 0)
        for b in pixel_boxes:
            draw.rectangle([b[0], b[1], b[2] - 1, b[3] - 1], fill=black)
        new = DecodedStreamObject()
        new[NameObject("/Type")] = NameObject("/XObject")
        new[NameObject("/Subtype")] = NameObject("/Image")
        new[NameObject("/Width")] = NumberObject(base.size[0])
        new[NameObject("/Height")] = NumberObject(base.size[1])
        new[NameObject("/BitsPerComponent")] = NumberObject(8)
        new[NameObject("/ColorSpace")] = _same_space(xo, mode) or NameObject({"L": "/DeviceGray", "CMYK": "/DeviceCMYK", "RGB": "/DeviceRGB"}[mode])
        if "/Interpolate" in xo:
            new[NameObject("/Interpolate")] = xo["/Interpolate"]
        if "/DCTDecode" in flist and mode != "CMYK":
            buf = io.BytesIO()
            base.save(buf, "JPEG", quality=92)
            new._data = buf.getvalue()  # noqa: SLF001
            new[NameObject("/Filter")] = NameObject("/DCTDecode")
            from pypdf.generic import EncodedStreamObject

            enc = EncodedStreamObject()
            for k, v in new.items():
                enc[NameObject(k)] = v
            enc._data = buf.getvalue()  # noqa: SLF001
            new = enc
        else:
            new.set_data(base.tobytes())
            new = new.flate_encode()
        self._paint_smask(xo, new, pixel_boxes, base.size)
        return self.w._add_object(new)


    def _paint_smask(self, xo: Any, new: Any, pixel_boxes: list[tuple[int, int, int, int]], size: tuple[int, int]) -> None:
        """Gives `new` a copy of xo's soft mask made opaque under the boxes (so the painted pixels show)."""
        from PIL import ImageDraw
        from pypdf.generic import DecodedStreamObject, NameObject, NumberObject
        from pypdf.generic._image_xobject import _xobj_to_image

        smask = xo.get("/SMask")
        if smask is None:
            return
        try:
            mobj = resolve(smask)
            _e, _r, mimg = _xobj_to_image(mobj)
            mimg = mimg.convert("L")
            sx, sy = mimg.size[0] / size[0], mimg.size[1] / size[1]
            md = ImageDraw.Draw(mimg)
            for b in pixel_boxes:
                md.rectangle([b[0] * sx, b[1] * sy, b[2] * sx - 1, b[3] * sy - 1], fill=255)
            ms = DecodedStreamObject()
            for k, v in mobj.items():
                if k not in ("/Filter", "/DecodeParms", "/Length", "/Decode"):
                    ms[NameObject(k)] = v
            ms[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
            ms[NameObject("/BitsPerComponent")] = NumberObject(8)
            ms[NameObject("/Width")] = NumberObject(mimg.size[0])
            ms[NameObject("/Height")] = NumberObject(mimg.size[1])
            ms.set_data(mimg.tobytes())
            new[NameObject("/SMask")] = self.w._add_object(ms.flate_encode())
        except Unsupported:
            raise
        except Exception as e:  # noqa: BLE001
            raise Unsupported(f"an image soft mask that cannot be decoded ({e})") from e

    def _paint_indexed(self, xo: Any, cs: Any, w: int, h: int, pixel_boxes: list[tuple[int, int, int, int]]) -> Any:
        """An 8-bit palette image with the covered pixels set to its darkest colour (palette and colours kept)."""
        from pypdf.generic import DecodedStreamObject, NameObject

        try:
            raw = bytearray(xo.get_data())
        except Exception as e:  # noqa: BLE001
            raise Unsupported(f"an image that cannot be decoded ({e})") from e
        if len(raw) < w * h:
            raise Unsupported("a palette image with too little data")
        dark = _darkest_index(cs)
        for x0, y0, x1, y1 in pixel_boxes:
            x0, x1 = max(0, x0), min(w, x1)
            if x1 <= x0:
                continue
            run = bytes([dark]) * (x1 - x0)
            for y in range(max(0, y0), min(h, y1)):
                raw[y * w + x0 : y * w + x1] = run
        new = DecodedStreamObject()
        for k, v in xo.items():
            if k not in ("/Filter", "/DecodeParms", "/Length", "/SMask"):
                new[NameObject(k)] = v
        new.set_data(bytes(raw))
        new = new.flate_encode()
        self._paint_smask(xo, new, pixel_boxes, (w, h))
        return self.w._add_object(new)

def _darkest_index(cs: list[Any]) -> int:
    """The palette index of the darkest colour of an /Indexed colour space (0 when the palette is unreadable)."""
    try:
        base = resolve(cs[1])
        hival = int(cs[2])
        lookup = resolve(cs[3])
        table = lookup.get_data() if hasattr(lookup, "get_data") else raw_bytes(lookup)
        name = str(base[0] if isinstance(base, list) else base)
        n = {"/DeviceGray": 1, "/CalGray": 1, "/DeviceRGB": 3, "/CalRGB": 3, "/Lab": 3, "/DeviceCMYK": 4}.get(name)
        if n is None and name == "/ICCBased":
            n = int(resolve(base[1]).get("/N", 3))
        if n is None:
            return 0
        best, best_dark = 0, -1.0
        for i in range(min(hival + 1, len(table) // n)):
            c = table[i * n : (i + 1) * n]
            dark = (255 - c[0]) if n == 1 else (765 - sum(c)) / 3 if n == 3 else min(255, max(c[3], (c[0] + c[1] + c[2]) / 3 + c[3]))
            if dark > best_dark:
                best, best_dark = i, dark
        return best
    except Exception:  # noqa: BLE001
        return 0


def _same_space(xo: Any, mode: str) -> Any:
    """The image's own ICC-based colour space when it fits the decoded pixels (keeps colours exact), else None."""
    cs = xo.get("/ColorSpace")
    try:
        obj = resolve(cs) if cs is not None else None
        if isinstance(obj, list) and len(obj) == 2 and str(obj[0]) == "/ICCBased":
            n = int(resolve(obj[1]).get("/N", 0))
            if n == {"L": 1, "RGB": 3, "CMYK": 4}[mode]:
                return cs
    except Exception:  # noqa: BLE001 — an odd colour space: fall back to the device space
        return None
    return None


# ── image placements (for pdf_optimize) ─────────────────────────────────


def image_placements(page: Any, pdf: Any) -> dict[Any, float]:
    """For each image XObject (by object id) drawn on the page: the largest displayed size in points (max side)."""
    from pypdf.generic import ContentStream

    out: dict[Any, float] = {}

    def walk(stream: Any, res: Any, ctm: Sequence[float], depth: int) -> None:
        if depth > 8 or stream is None:
            return
        try:
            ops = stream.operations if isinstance(stream, ContentStream) else ContentStream(stream, pdf).operations
        except Exception:  # noqa: BLE001
            return
        stack: list[Any] = []
        cur = tuple(ctm)
        xobjs = resolve(res.get("/XObject")) if res is not None and res.get("/XObject") is not None else {}
        for operands, op in ops:
            if op == b"q":
                stack.append(cur)
            elif op == b"Q":
                if stack:
                    cur = stack.pop()
            elif op == b"cm" and len(operands) == 6:
                cur = mat_mul([float(x) for x in operands], cur)
            elif op == b"Do":
                ref = xobjs.get(operands[0]) if xobjs else None
                if ref is None:
                    continue
                xo = resolve(ref)
                st = xo.get("/Subtype")
                key = getattr(ref, "idnum", None) or id(xo)
                if st == "/Image":
                    b = bbox_of(cur, 0, 0, 1, 1)
                    size = max(b[2] - b[0], b[3] - b[1])
                    out[key] = max(out.get(key, 0.0), size)
                elif st == "/Form":
                    m = [float(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                    sub_res = xo.get("/Resources")
                    walk(xo, resolve(sub_res) if sub_res is not None else res, mat_mul(m, cur), depth + 1)

    res = page.get("/Resources")
    walk(page.get_contents(), resolve(res) if res is not None else None, IDENTITY, 0)
    return out


def iter_strings(obj: Any, depth: int = 0) -> Iterable[tuple[Any, Any, str]]:
    """(container, key or index, text) for every string inside a direct object tree (not following references)."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    if depth > 40:
        return
    if isinstance(obj, DictionaryObject):
        for k, v in list(obj.items()):
            if isinstance(v, IndirectObject):
                continue
            if isinstance(v, str) and type(v).__name__ in ("TextStringObject", "ByteStringObject"):
                yield obj, k, str(v)
            elif type(v).__name__ == "ByteStringObject":
                try:
                    yield obj, k, bytes(v).decode("latin-1")
                except Exception:  # noqa: BLE001
                    pass
            else:
                yield from iter_strings(v, depth + 1)
    elif isinstance(obj, ArrayObject):
        for i, v in enumerate(list(obj)):
            if isinstance(v, IndirectObject):
                continue
            if type(v).__name__ in ("TextStringObject", "ByteStringObject"):
                try:
                    yield obj, i, str(v) if type(v).__name__ == "TextStringObject" else bytes(v).decode("latin-1")
                except Exception:  # noqa: BLE001
                    pass
            else:
                yield from iter_strings(v, depth + 1)
