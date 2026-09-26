#!/usr/bin/env python3
"""True redaction: remove text, image pixels and drawings under the redacted areas (not just cover them), draw
boxes, scrub matching strings from metadata, bookmarks, annotations and form values, then verify the result by
extracting the text of every searched page again. A page that cannot be redacted exactly, or that fails
verification, is rasterised (rendered to an image with the boxes burnt in) so nothing recoverable remains; the
output says which pages.

Targets: --find TEXT (literal, repeatable), --regex PATTERN, --preset (email, phone, ssn, card, iban, ip, url,
date), and --box PAGES:x0,y0,x1,y1 in points from the top-left of the page as displayed (pdf_text.py
--words/--search and pdf_render.py --grid show these coordinates; fractions 0-1 work). Text targets match as the
text reads: over line breaks, through words hyphenated at a line end, and over page breaks.

Examples:
  python3 scripts/pdf_redact.py contract.pdf redacted.pdf --find "Jane Doe" --preset email --preset phone
  python3 scripts/pdf_redact.py in.pdf out.pdf --regex "\\b\\d{3}-\\d{2}-\\d{4}\\b" --dry-run --render preview/
  python3 scripts/pdf_redact.py scan.pdf out.pdf --box "1:320,90,560,140" --box "all:0,0,1,0.08"
  python3 scripts/pdf_redact.py in.pdf out.pdf --find "Project Falcon" --label REDACTED --strip-metadata
  python3 scripts/pdf_redact.py in.pdf out.pdf --find ACME --raster        # rasterise every affected page
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, human_size, md_table, output_dir, output_path, parser, run_main
from _content import Redactor, Unsupported, iter_strings
from _pdfkit import compress_writer, emit, fmt_num, init_forms, new_writer, open_pdfium, open_reader, parse_box, parse_color, parse_pages, pdf_input, pdf_version, pdfium_geometry, pdfium_source, render_pages, writer_bytes

PRESETS = {
    # Also the grouped form of papers: {ann,bob}@example.org (the whole group is redacted).
    "email": r"(?:\{\s*[A-Za-z0-9._%+-]+(?:\s*[,;|]\s*[A-Za-z0-9._%+-]+)*\s*\}|[A-Za-z0-9._%+-]+)@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "phone": r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]\d{2,4}){2,4}(?!\w)",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "card": r"\b(?:\d[ -]?){12,18}\d\b",
    "iban": r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b",
    "ip": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    "url": r"\bhttps?://[^\s<>\"')\]]+",
    "date": r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
}


def luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


VALIDATORS = {"card": luhn, "iban": iban_ok}


def literal_regex(text: str) -> str:
    return r"\s*".join(re.escape(c) for c in text.split(" ")) if " " not in text.strip() else r"\s+".join(re.escape(part) for part in text.split())


def build_patterns(a: Any) -> list[tuple[str, re.Pattern[str], Any]]:
    flags = re.IGNORECASE if a.ignore_case else 0
    pats: list[tuple[str, re.Pattern[str], Any]] = []
    for t in a.find or []:
        if not t.strip():
            raise UsageError("--find needs some text")
        body = literal_regex(t)
        if a.whole_word:
            body = rf"(?<!\w){body}(?!\w)"
        pats.append((f"find:{t}", re.compile(body, flags), None))
    for r in a.regex or []:
        try:
            pats.append((f"regex:{r}", re.compile(r, flags), None))
        except re.error as e:
            raise UsageError(f"bad --regex {r!r}: {e}") from e
    for pr in a.preset or []:
        if pr not in PRESETS:
            raise UsageError(f"unknown preset {pr} ({', '.join(PRESETS)})")
        pats.append((f"preset:{pr}", re.compile(PRESETS[pr], flags if pr not in ("iban",) else 0), VALIDATORS.get(pr)))
    return pats


def page_chars(tp: Any) -> str:
    from pdf_text import page_chars as pc

    return pc(tp)


def page_texts(doc: Any, numbers: list[int]) -> dict[int, str]:
    """pdfium's text of these pages (one string character per char index)."""
    out = {}
    for n in numbers:
        page = doc[n - 1]
        tp = page.get_textpage()
        out[n] = page_chars(tp)
        tp.close()
        page.close()
    return out


def joined_matches(raw: str, found: list[tuple[str, str, int, int, str]], patterns: list[tuple[str, re.Pattern[str], Any]]) -> list[tuple[str, str, int, int, str]]:
    """Targets that only appear once other targets are removed: "notice period <email> is ninety days" reads
    "notice period is ninety days" after the address is redacted, so that phrase is redacted too (as the pieces
    of text around the removed part). Repeats until nothing new appears (at most 3 rounds)."""
    from _textfind import find_spans

    removed = sorted((s, e) for _l, _t, s, e, _n in found)
    extra: list[tuple[str, str, int, int, str]] = []
    for _round in range(3):
        sim: list[str] = []
        back: list[int] = []  # raw index of each simulated char; -1 for the space left by a removed target
        pos = 0
        for s, e in _merge_spans(removed):
            if s > pos:
                sim.append(raw[pos:s])
                back.extend(range(pos, s))
            sim.append(" ")
            back.append(-1)
            pos = max(pos, e)
        sim.append(raw[pos:])
        back.extend(range(pos, len(raw)))
        text = "".join(sim)
        new: list[tuple[str, str, int, int, str]] = []
        for label, rx, validate in patterns:
            for h in find_spans(text, rx, validate):
                idx = back[h.start : h.end]
                if -1 not in idx:
                    continue  # an ordinary match, found already
                pieces: list[list[int]] = []
                for k in idx:
                    if k < 0:
                        continue
                    if pieces and k == pieces[-1][1]:
                        pieces[-1][1] = k + 1
                    else:
                        pieces.append([k, k + 1])
                for a, b in pieces:
                    if raw[a:b].strip():
                        new.append((label, h.text, a, b, "reads as a match once the other targets are removed"))
        if not new:
            break
        extra.extend(new)
        removed = sorted(removed + [(a, b) for _l, _t, a, b, _n in new])
    return extra


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def find_matches(path: Path, password: str | None, numbers: list[int], patterns: list[tuple[str, re.Pattern[str], Any]]) -> dict[int, list[dict[str, Any]]]:
    """Per page: matches with their user-space rectangles (one per line segment).

    Text is matched the way it reads (see _textfind): across line breaks, through words hyphenated at a line end,
    and across page breaks (running headers, footers and page numbers skipped); such a match is redacted on both
    pages.
    """
    from _textfind import body_bounds, cross_page, find_spans
    from pdf_text import range_rects

    doc = open_pdfium(path, password)
    out: dict[int, list[dict[str, Any]]] = {}
    try:
        raws = page_texts(doc, numbers)
        bounds = body_bounds(raws)
        spans: dict[int, list[tuple[str, str, int, int, str]]] = {}
        for label, rx, validate in patterns:
            for n in numbers:
                for h in find_spans(raws[n], rx, validate):
                    spans.setdefault(n, []).append((label, h.text, h.start, h.end, ""))
            for c in cross_page(raws, rx, validate, bounds):
                spans.setdefault(c.first, []).append((label, c.text, c.s1, c.e1, f"continues on page {c.first + 1}"))
                spans.setdefault(c.first + 1, []).append((label, c.text, c.s2, c.e2, f"continued from page {c.first}"))
        for n in list(spans):
            spans[n].extend(joined_matches(raws[n], spans[n], patterns))
        for n in sorted(spans):
            page = doc[n - 1]
            tp = page.get_textpage()
            chars = raws[n]
            geo = pdfium_geometry(page)
            found = []
            for label, text, s, e, note in spans[n]:
                while s < e and chars[s].isspace():
                    s += 1
                while e > s and chars[e - 1].isspace():
                    e -= 1
                rects = [r for r in range_rects(tp, s, e - s) if r[2] > r[0] and r[3] > r[1]]
                if rects:
                    found.append({"target": label, "text": text, "rects": rects, "view": [[fmt_num(v) for v in geo.rect_to_view(*r)] for r in rects], "chars": (s, e), **({"note": note} if note else {})})
            if found:
                out[n] = found
            tp.close()
            page.close()
    finally:
        doc.close()
    return out


def remaining_targets(doc: Any, numbers: list[int], patterns: list[tuple[str, re.Pattern[str], Any]], boxes: dict[int, Any]) -> dict[int, list[str]]:
    """Targets still extractable on pages that were not redacted, or over a page break (the redacted pages
    themselves are checked by verify)."""
    from _textfind import body_bounds, cross_page, find_spans

    raws = page_texts(doc, numbers)
    bounds = body_bounds(raws)
    left: dict[int, list[str]] = {}
    for label, rx, validate in patterns:
        for n in numbers:
            if n in boxes:
                continue
            for h in find_spans(raws[n], rx, validate):
                left.setdefault(n, []).append(f"{label} still found: {h.text!r}")
                break
        for c in cross_page(raws, rx, validate, bounds):
            left.setdefault(c.first, []).append(f"{label} still found over the page break to page {c.first + 1}: {c.text!r}")
    return left


def at_sign_hint(path: Path, password: str | None, numbers: list[int], matches: dict[int, list[dict[str, Any]]]) -> list[str]:
    """When '@' appears on pages where the email preset matched nothing: an address in an unusual form may be missed."""
    doc = open_pdfium(path, password)
    pages = []
    try:
        for n in numbers:
            if any(m["target"] == "preset:email" for m in matches.get(n, [])):
                continue
            page = doc[n - 1]
            tp = page.get_textpage()
            if "@" in page_chars(tp):
                pages.append(n)
            tp.close()
            page.close()
    finally:
        doc.close()
    if not pages:
        return []
    from pdf_text import _ranges

    return [f"'@' appears on page(s) {_ranges(pages[:30])} where no address matched the email pattern: look with pdf_text.py --search '@' and redact any address there with --regex or --box"]


def parse_boxes(specs: list[str], path: Path, password: str | None, count: int) -> dict[int, list[tuple[float, float, float, float]]]:
    """--box PAGES:x0,y0,x1,y1 → user-space rectangles per page."""
    out: dict[int, list[tuple[float, float, float, float]]] = {}
    if not specs:
        return out
    doc = open_pdfium(path, password)
    try:
        for spec in specs:
            if ":" not in spec:
                raise UsageError(f"bad --box '{spec}': use PAGES:x0,y0,x1,y1, e.g. 1:72,72,300,120 or all:0,0,1,0.1")
            pages_part, box_part = spec.split(":", 1)
            for n in parse_pages(pages_part, count):
                page = doc[n - 1]
                geo = pdfium_geometry(page)
                view = parse_box(box_part, geo.width, geo.height)
                out.setdefault(n, []).append(geo.rect_from_view(*view))
                page.close()
    finally:
        doc.close()
    return out


def pad(r: tuple[float, float, float, float], p: float) -> tuple[float, float, float, float]:
    return (r[0] - p, r[1] - p, r[2] + p, r[3] + p)


def chars_with_boxes(doc: Any, n: int, near: list[tuple[float, float, float, float]] | None = None) -> list[tuple[str, float, float]]:
    """(char, centre x, centre y) in user space for every non-space char on the page.

    With `near`, only lines that come within 8 pt of those rectangles get real positions (a char box costs a
    pdfium call); chars of the other lines are listed at (inf, inf), outside any box, which is all the checks need.
    """
    import ctypes

    import pypdfium2.raw as raw

    from pdf_text import char_boxes

    page = doc[n - 1]
    tp = page.get_textpage()
    chars = page_chars(tp)
    skip = "\x00\x02\ufffe"
    if near is None:
        boxes = char_boxes(tp, len(chars), chars)
        out = [(c, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for c, b in zip(chars, boxes) if not c.isspace() and c not in skip]
    else:
        bands = [(r[1] - 8, r[3] + 8) for r in near]
        l, r_, b, t = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        refs = (ctypes.byref(l), ctypes.byref(r_), ctypes.byref(b), ctypes.byref(t))
        handle = getattr(tp, "raw", tp)
        far = float("inf")
        out = []
        for m in re.finditer(r"[^\r\n]+", chars):
            s0, e0 = m.span()
            ys = []
            for k in (s0, e0 - 1):
                raw.FPDFText_GetCharBox(handle, k, *refs)
                ys += [b.value, t.value]
            line = chars[s0:e0]
            y0, y1 = min(ys), max(ys)
            if not any(y1 >= lo and y0 <= hi for lo, hi in bands):
                out.extend((c, far, far) for c in line if not c.isspace() and c not in skip)
                continue
            for k in range(s0, e0):
                c = chars[k]
                if c.isspace() or c in skip:
                    continue
                raw.FPDFText_GetCharBox(handle, k, *refs)
                out.append((c, (l.value + r_.value) / 2, (b.value + t.value) / 2))
    tp.close()
    page.close()
    return out


def split_chars(chars: list[tuple[str, float, float]], rects: list[tuple[float, float, float, float]]) -> tuple[str, Counter[str]]:
    """The chars whose centre lies in a rectangle (as a string) and a count of all the others."""
    if not rects:
        return "", Counter(c for c, _x, _y in chars)
    y0, y1 = min(r[1] for r in rects), max(r[3] for r in rects)
    inside_: list[str] = []
    outside: list[str] = []
    for c, x, y in chars:
        if y < y0 or y > y1:
            outside.append(c)
        elif any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects):
            inside_.append(c)
        else:
            outside.append(c)
    return "".join(inside_), Counter(outside)


def merge_rects(rects: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Merges rectangles that overlap (the same text found by two patterns, touching words)."""
    out: list[list[float]] = []
    for r in sorted(rects):
        for o in out:
            if r[0] < o[2] and o[0] < r[2] and r[1] < o[3] and o[1] < r[3]:
                o[0], o[1], o[2], o[3] = min(o[0], r[0]), min(o[1], r[1]), max(o[2], r[2]), max(o[3], r[3])
                break
        else:
            out.append(list(r))
    return [tuple(o) for o in out]  # type: ignore[misc]


def add_label(writer: Any, page: Any, rects: list[tuple[float, float, float, float]], fill: tuple[float, float, float], label: str) -> None:
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    res = page.get("/Resources")
    res = res.get_object() if res is not None else DictionaryObject()
    new_res = DictionaryObject({k: v for k, v in res.items()})
    fonts = new_res.get("/Font")
    fonts = DictionaryObject({k: v for k, v in (fonts.get_object().items() if fonts is not None else [])})
    helv = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica-Bold"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    fonts[NameObject("/DeskRedactFont")] = writer._add_object(helv)
    new_res[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = new_res
    ops = draw_label_ops(rects, fill, label, "DeskRedactFont")
    s = DecodedStreamObject()
    s.set_data(ops)
    contents = page.get("/Contents")
    arr = ArrayObject()
    if contents is not None:
        c = contents.get_object()
        if isinstance(c, ArrayObject):
            arr.extend(c)
        else:
            arr.append(contents)
    arr.append(writer._add_object(s))
    page[NameObject("/Contents")] = arr


def draw_label_ops(rects: list[tuple[float, float, float, float]], fill: tuple[float, float, float], label: str, font_name: str) -> bytes:
    light = sum(fill) / 3 < 0.5
    parts = ["q", f"{1 if light else 0} g"]
    esc = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    try:
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS

        widths = CORE_FONT_METRICS["Helvetica-Bold"].character_widths
        em = sum(widths.get(c, 611) for c in label) / 1000.0
    except Exception:  # noqa: BLE001
        em = len(label) * 0.7
    for r in rects:
        h, w = r[3] - r[1], r[2] - r[0]
        size = min(h * 0.7, 9.0, (w - 2) / em if em else 9.0)
        text_w = em * size
        if size < 3.5:
            continue
        x = r[0] + (w - text_w) / 2
        y = r[1] + (h - size * 0.7) / 2
        parts.append(f"BT /{font_name} {size:.2f} Tf {x:.2f} {y:.2f} Td ({esc}) Tj ET")
    parts.append("Q")
    return ("\n".join(parts) + "\n").encode("latin-1", "replace")


def draw_boxes_ops(rects: list[tuple[float, float, float, float]], fill: tuple[float, float, float], label: str | None, font_name: str | None) -> bytes:
    parts = ["q", f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg"]
    for r in rects:
        parts.append(f"{r[0]:.2f} {r[1]:.2f} {r[2] - r[0]:.2f} {r[3] - r[1]:.2f} re f")
    if label and font_name:
        light = sum(fill) / 3 < 0.5
        parts.append(f"{1 if light else 0} g")
        for r in rects:
            h = r[3] - r[1]
            w = r[2] - r[0]
            size = min(h * 0.7, 9.0)
            text_w = len(label) * size * 0.55
            if size < 4 or text_w > w:
                continue
            x = r[0] + (w - text_w) / 2
            y = r[1] + (h - size * 0.7) / 2
            esc = label.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            parts.append(f"BT /{font_name} {size:.2f} Tf {x:.2f} {y:.2f} Td ({esc}) Tj ET")
    parts.append("Q")
    return ("\n".join(parts) + "\n").encode("latin-1", "replace")


def add_boxes(writer: Any, page: Any, rects: list[tuple[float, float, float, float]], fill: tuple[float, float, float], label: str | None) -> None:
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    font_name = None
    if label:
        res = page.get("/Resources")
        res = res.get_object() if res is not None else DictionaryObject()
        new_res = DictionaryObject({k: v for k, v in res.items()})
        fonts = new_res.get("/Font")
        fonts = DictionaryObject({k: v for k, v in (fonts.get_object().items() if fonts is not None else [])})
        helv = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica-Bold"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        fonts[NameObject("/DeskRedactFont")] = writer._add_object(helv)
        new_res[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = new_res
        font_name = "DeskRedactFont"
    head = DecodedStreamObject()
    head.set_data(b"q\n")
    tail = DecodedStreamObject()
    tail.set_data(b"\nQ\n" + draw_boxes_ops(rects, fill, label, font_name))
    arr = ArrayObject([writer._add_object(head)])
    contents = page.get("/Contents")
    if contents is not None:
        c = contents.get_object()
        if isinstance(c, ArrayObject):
            arr.extend(c)
        else:
            arr.append(contents)
    arr.append(writer._add_object(tail))
    page[NameObject("/Contents")] = arr


def drop_unused_refs(writer: Any, ids: set[int]) -> int:
    """Removes resource entries that point at replaced images or forms from pages that never draw them.

    A form or image shared by several pages keeps its original (unredacted) content for the pages that use it;
    a page that only lists it in its resources would still carry the text inside the file, so the entry goes.
    """
    from pypdf.generic import DictionaryObject, NameObject

    removed = 0
    for page in writer.pages:
        res = page.get("/Resources")
        if res is None:
            continue
        res_obj = res.get_object()
        xo = res_obj.get("/XObject")
        if xo is None:
            continue
        xo_obj = xo.get_object()
        names = [k for k, v in xo_obj.items() if getattr(v, "idnum", None) in ids]
        if not names:
            continue
        contents = page.get_contents()
        data = contents.get_data() if contents is not None else b""
        unused = [k for k in names if not re.search(re.escape(k.encode("latin-1")) + rb"\s*Do\b", data)]
        if not unused:
            continue
        new_res = DictionaryObject({k: v for k, v in res_obj.items()})
        new_res[NameObject("/XObject")] = DictionaryObject({k: v for k, v in xo_obj.items() if k not in unused})
        page[NameObject("/Resources")] = new_res
        removed += len(unused)
    return removed


def remove_annotations(writer: Any, page: Any, rects: list[tuple[float, float, float, float]]) -> int:
    from pypdf.generic import ArrayObject, NameObject

    annots = page.get("/Annots")
    if annots is None:
        return 0
    keep = ArrayObject()
    removed_refs = set()
    removed = 0
    for ref in annots.get_object():
        a = ref.get_object()
        rect = a.get("/Rect")
        hit = False
        if rect is not None:
            x0, y0, x1, y1 = (float(v) for v in rect)
            box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            hit = any(box[0] < r[2] and r[0] < box[2] and box[1] < r[3] and r[1] < box[3] for r in rects)
        if hit:
            removed += 1
            removed_refs.add(getattr(ref, "idnum", None))
            if a.get("/Subtype") == "/Widget":
                _remove_field(writer, ref, a)
        else:
            keep.append(ref)
    # Popups whose parent went away.
    final = ArrayObject()
    for ref in keep:
        a = ref.get_object()
        parent = a.get("/Parent")
        if a.get("/Subtype") == "/Popup" and parent is not None and getattr(parent, "idnum", None) in removed_refs:
            removed += 1
            continue
        final.append(ref)
    if final:
        page[NameObject("/Annots")] = final
    else:
        del page["/Annots"]
    return removed


def _remove_field(writer: Any, ref: Any, widget: Any) -> None:
    root = writer._root_object
    acro = root.get("/AcroForm")
    if acro is None:
        return
    acro = acro.get_object()
    field_ref = ref if "/T" in widget else widget.get("/Parent")
    target = getattr(field_ref, "idnum", None)
    container = None
    parent = field_ref.get_object().get("/Parent") if field_ref is not None and "/T" in field_ref.get_object() else None
    if parent is not None:
        container = parent.get_object().get("/Kids")
    else:
        container = acro.get("/Fields")
    if container is None:
        return
    container = container.get_object()
    for i in range(len(container) - 1, -1, -1):
        if getattr(container[i], "idnum", None) == target:
            del container[i]
    if field_ref is not None:
        fo = field_ref.get_object()
        for k in ("/V", "/DV"):
            if k in fo:
                del fo[k]


def rasterise_page(writer: Any, index: int, src: Path, password: str | None, rects: list[tuple[float, float, float, float]], fill: tuple[float, float, float], dpi: float) -> None:
    """Replaces a page with an image of the original page, redaction boxes burnt in."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, EncodedStreamObject, FloatObject, NameObject, NumberObject

    doc = pdfium.PdfDocument(str(src), password=password)
    try:
        init_forms(doc)
        page = doc[index]
        geo = pdfium_geometry(page)
        scale = dpi / 72.0
        img = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
        page.close()
    finally:
        doc.close()
    draw = ImageDraw.Draw(img)
    color = tuple(round(c * 255) for c in fill)
    for r in rects:
        x0, y0, x1, y1 = geo.rect_to_view(*r)
        draw.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale], fill=color)
    W, H = geo.width, geo.height
    gray = img.convert("L")
    is_gray = _is_gray(img)
    xo = EncodedStreamObject()
    buf = io.BytesIO()
    (gray if is_gray else img).save(buf, "JPEG", quality=90, optimize=True)
    xo._data = buf.getvalue()  # noqa: SLF001
    xo[NameObject("/Type")] = NameObject("/XObject")
    xo[NameObject("/Subtype")] = NameObject("/Image")
    xo[NameObject("/Width")] = NumberObject(img.size[0])
    xo[NameObject("/Height")] = NumberObject(img.size[1])
    xo[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if is_gray else "/DeviceRGB")
    xo[NameObject("/BitsPerComponent")] = NumberObject(8)
    xo[NameObject("/Filter")] = NameObject("/DCTDecode")
    img_ref = writer._add_object(xo)
    content = DecodedStreamObject()
    content.set_data(f"q {W:.3f} 0 0 {H:.3f} 0 0 cm /DeskRaster Do Q\n".encode())
    p = writer.pages[index]
    for k in ("/Annots", "/CropBox", "/TrimBox", "/BleedBox", "/ArtBox", "/Rotate", "/Group", "/Metadata", "/PieceInfo", "/Thumb", "/StructParents"):
        if k in p:
            del p[k]
    p[NameObject("/MediaBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(round(W, 3)), FloatObject(round(H, 3))])
    p[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/DeskRaster"): img_ref})})
    p[NameObject("/Contents")] = writer._add_object(content)


def _is_gray(img: Any) -> bool:
    from PIL import ImageChops

    small = img.resize((max(1, img.size[0] // 8), max(1, img.size[1] // 8)))
    r, g, b = small.split()
    return ImageChops.difference(r, g).getextrema()[1] < 12 and ImageChops.difference(g, b).getextrema()[1] < 12


def scrub_document(writer: Any, patterns: list[tuple[str, re.Pattern[str], Any]], replacement: str) -> dict[str, int]:
    """Replaces pattern matches in strings outside page content (metadata, outline, annotations, fields…)."""
    from pypdf.generic import StreamObject, TextStringObject

    counts: Counter[str] = Counter()
    info_obj = writer._info.get_object() if writer._info is not None else None

    def sub(text: str) -> str:
        for _label, rx, validate in patterns:
            text = rx.sub(lambda m: replacement if (validate is None or validate(m.group(0))) else m.group(0), text)
        return text

    for i, obj in enumerate(writer._objects):
        if obj is None:
            continue
        if isinstance(obj, StreamObject):
            if obj.get("/Type") == "/Metadata" and obj.get("/Subtype") == "/XML":
                try:
                    xml = obj.get_data().decode("utf-8", "replace")
                    new = sub(xml)
                    if new != xml:
                        obj.set_data(new.encode("utf-8"))
                        counts["xmp"] += 1
                except Exception:  # noqa: BLE001
                    pass
            targets = iter_strings(obj)
        else:
            targets = iter_strings(obj)
        for container, key, text in list(targets):
            new = sub(text)
            if new != text:
                container[key] = TextStringObject(new)
                kind = "info" if obj is info_obj else str(key).lstrip("/").lower() if isinstance(key, str) else "string"
                counts[kind] += 1
    return dict(counts)


def verify(out_bytes: bytes, pages: dict[int, list[tuple[float, float, float, float]]], patterns: list[tuple[str, re.Pattern[str], Any]], before: dict[int, Counter[str]], rasterised: set[int], label: str | None = None, searched: list[int] | None = None) -> dict[int, list[str]]:
    """Problems per page: target text still extractable (on any searched page, not only the redacted ones), text left
    inside a box, or text lost outside the boxes."""
    import pypdfium2 as pdfium

    from _textfind import find_spans

    problems: dict[int, list[str]] = {}
    doc = pdfium.PdfDocument(out_bytes)
    try:
        for n, rects in pages.items():
            issues = []
            page = doc[n - 1]
            tp = page.get_textpage()
            text = page_chars(tp)
            leftovers = []
            if n not in rasterised:
                for tlabel, rx, validate in patterns:
                    for h in find_spans(text, rx, validate):
                        leftovers.append(f"{tlabel} still found: {h.text!r}")
                        break
            tp.close()
            page.close()
            if n in rasterised:
                if text.strip():
                    issues.append("a rasterised page still has text")
            else:
                issues.extend(leftovers)
                inside_, after = split_chars(chars_with_boxes(doc, n, rects), rects)
                if label:
                    inside_ = inside_.replace("".join(label.split()), "")
                if inside_:
                    issues.append(f"text left under a box: {inside_[:40]!r}")
                lost = before.get(n, Counter()) - after
                if sum(lost.values()) > 0:
                    issues.append(f"{sum(lost.values())} character(s) outside the boxes were lost: {''.join(sorted(lost.elements()))[:40]!r}")
            if issues:
                problems[n] = issues
        if patterns and searched:
            for n, v in remaining_targets(doc, searched, patterns, pages).items():
                problems.setdefault(n, []).extend(v)
    finally:
        doc.close()
    return problems


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("input")
    p.add_argument("out", nargs="?", help="output PDF (not needed with --dry-run)")
    p.add_argument("--find", action="append", help="literal text to redact (repeatable)")
    p.add_argument("--regex", action="append", help="regular expression to redact (repeatable)")
    p.add_argument("--preset", action="append", choices=list(PRESETS), help="common personal data patterns (repeatable)")
    p.add_argument("--box", action="append", help="PAGES:x0,y0,x1,y1 area to redact, e.g. 2:72,500,300,540 or all:0,0.9,1,1")
    p.add_argument("--pages", help="only search these pages (default all)")
    p.add_argument("--ignore-case", "-i", action="store_true")
    p.add_argument("--whole-word", action="store_true", help="--find matches whole words only")
    p.add_argument("--padding", type=float, default=1.0, help="grow each box by this many points (default 1)")
    p.add_argument("--fill", default="black", help="box color (default black)")
    p.add_argument("--label", help="text printed inside each box, e.g. REDACTED")
    p.add_argument("--replacement", default="[REDACTED]", help="text that replaces matches in metadata and other strings")
    p.add_argument("--raster", action="store_true", help="rasterise every affected page instead of removing content exactly")
    p.add_argument("--no-fallback", action="store_true", help="fail instead of rasterising pages that cannot be redacted exactly")
    p.add_argument("--dpi", type=float, default=200, help="resolution of rasterised pages (default 200)")
    p.add_argument("--strip-metadata", action="store_true", help="also remove all document metadata (Info and XMP)")
    p.add_argument("--remove-attachments", action="store_true", help="also remove embedded files (their content cannot be checked)")
    p.add_argument("--dry-run", action="store_true", help="only list what would be redacted")
    p.add_argument("--render", metavar="DIR", help="render affected pages to PNGs (with --dry-run: previews with the boxes outlined)")
    p.add_argument("--password")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()

    src = pdf_input(a.input)
    patterns = build_patterns(a)
    if not patterns and not a.box:
        raise UsageError("nothing to redact: pass --find, --regex, --preset or --box")
    reader = open_reader(src, a.password)
    psrc = pdfium_source(src, a.password)  # what pdfium reads: the file, or a repaired copy of a damaged one
    count = len(reader.pages)
    numbers = parse_pages(a.pages, count)
    matches = find_matches(psrc, a.password, numbers, patterns) if patterns else {}
    boxes = parse_boxes(a.box or [], psrc, a.password, count)
    rects: dict[int, list[tuple[float, float, float, float]]] = {}
    for n, ms in matches.items():
        for m in ms:
            rects.setdefault(n, []).extend(pad(r, a.padding) for r in m["rects"])
    for n, bs in boxes.items():
        rects.setdefault(n, []).extend(bs)
    rects = {n: merge_rects(rs) for n, rs in rects.items()}
    total_matches = sum(1 for v in matches.values() for m in v if not m.get("note") or m["note"].startswith("continues"))
    hints = at_sign_hint(psrc, a.password, numbers, matches) if "email" in (a.preset or []) else []

    if a.dry_run or not a.out:
        if not a.dry_run:
            raise UsageError("give an output file, or --dry-run to only list matches")
        listing = [{"page": n, "target": m["target"], "text": m["text"] + (f" ({m['note']})" if m.get("note") else ""), "boxes": m["view"]} for n in sorted(matches) for m in matches[n]]
        info: dict[str, Any] = {"matches": total_matches, "pages": sorted(rects), "items": listing, "boxes": {n: len(b) for n, b in boxes.items()}, **({"hints": hints} if hints else {})}
        if a.render:
            folder = output_dir(a.render)
            doc = open_pdfium(psrc, a.password)
            jobs = []
            for n in sorted(rects)[:20]:
                geo = pdfium_geometry(doc[n - 1])
                jobs.append({"pdf": str(psrc), "index": n - 1, "password": a.password, "dpi": None, "max_edge": 1568, "out": str(folder / f"preview-page-{n:03d}.png"), "boxes": [{"rect": geo.rect_to_view(*r), "fill": True} for r in rects[n]]})
            doc.close()
            info["rendered"] = [r["path"] for r in render_pages(jobs)]

        def md(d: dict[str, Any]) -> str:
            lines = [f"dry run: {d['matches']} match(es) on {len(d['pages'])} page(s); nothing was written"]
            if d["items"]:
                lines.append("")
                lines.append(md_table(["page", "target", "text", "box (x0, top, x1, bottom)"], [[x["page"], x["target"], x["text"], "; ".join(", ".join(map(str, b)) for b in x["boxes"])] for x in d["items"][:300]]))
            lines += [f"note: {h}" for h in d.get("hints", [])]
            if d.get("rendered"):
                lines += d["rendered"] + ["Look at them with view_image (boxes to be redacted are outlined in red)."]
            return "\n".join(lines)

        emit(info, a.format, md)
        return 0

    out = output_path(a.out, [src], a.force)
    if not rects:
        raise SkillError("no matches and no boxes: nothing to redact (check with pdf_text.py --search, or --dry-run)" + "".join(f". {h}" for h in hints))
    fill = parse_color(a.fill)
    from pypdf import PdfWriter

    writer = new_writer(reader)
    # Characters outside the boxes before redaction (to detect collateral loss).
    before: dict[int, Counter[str]] = {}
    doc = open_pdfium(psrc, a.password)
    try:
        for n, rs in rects.items():
            before[n] = split_chars(chars_with_boxes(doc, n, rs), rs)[1]
    finally:
        doc.close()

    def scrub_text(s: str) -> str:
        for _label, rx, validate in patterns:
            s = rx.sub(lambda m: a.replacement if (validate is None or validate(m.group(0))) else m.group(0), s)
        return s

    per_page: dict[int, dict[str, Any]] = {}
    to_raster: dict[int, str] = {}
    red = Redactor(writer, [], scrub_text if patterns else None)
    for n in sorted(rects):
        page = writer.pages[n - 1]
        entry: dict[str, Any] = {"matches": len(matches.get(n, [])), "boxes": len(rects[n])}
        if a.raster:
            to_raster[n] = "requested"
        else:
            red.rects = rects[n]
            red.stats = dict.fromkeys(red.stats, 0)
            try:
                red.redact_page(page)
                entry.update({k: v for k, v in red.stats.items() if v})
            except Unsupported as e:
                to_raster[n] = str(e)
            except Exception as e:  # noqa: BLE001 — never ship a half-redacted page
                to_raster[n] = f"{type(e).__name__}: {e}"
        entry["annotations_removed"] = remove_annotations(writer, page, rects[n])
        if n not in to_raster:
            add_boxes(writer, page, rects[n], fill, None)
        per_page[n] = entry
    unused_refs = drop_unused_refs(writer, red.replaced) if red.replaced else 0
    if a.no_fallback and to_raster and not a.raster:
        raise SkillError("these pages cannot be redacted exactly (and --no-fallback was given): " + "; ".join(f"page {n}: {why}" for n, why in to_raster.items()))
    for n, why in to_raster.items():
        rasterise_page(writer, n - 1, psrc, a.password, rects[n], fill, a.dpi)
        per_page[n]["method"] = "rasterised"
        per_page[n]["why"] = why
    scrubbed = scrub_document(writer, patterns, a.replacement) if patterns else {}
    appearances = scrub_appearances(writer, patterns) if patterns else 0
    if appearances:
        scrubbed["annotation appearances removed"] = appearances
    if a.strip_metadata:
        if writer._info is not None:
            info_obj = writer._info.get_object()
            for k in list(info_obj.keys()):
                del info_obj[k]
        if "/Metadata" in writer._root_object:
            del writer._root_object["/Metadata"]
    attachments_note = None
    try:
        names = list(reader.attachments)
    except Exception:  # noqa: BLE001
        names = []
    if names:
        if a.remove_attachments:
            root_names = writer._root_object.get("/Names")
            if root_names is not None and "/EmbeddedFiles" in root_names.get_object():
                del root_names.get_object()["/EmbeddedFiles"]
            attachments_note = f"removed {len(names)} attachment(s)"
        else:
            attachments_note = f"the file has {len(names)} attachment(s) ({', '.join(names[:5])}) whose content was not checked; use --remove-attachments to drop them"
    compress_writer(writer)
    data = writer_bytes(writer)
    rasterised = set(to_raster)
    problems = verify(data, rects, patterns, before, rasterised, a.label, numbers)
    unfound = {n: v for n, v in problems.items() if n not in rects}
    if unfound:
        raise SkillError("verification failed, nothing was written: targets the search did not locate are still in the text: " + "; ".join(f"page {n}: {', '.join(v)}" for n, v in unfound.items()) + ". Redact them with --box (pdf_text.py --search shows their boxes) or rasterise the pages with --raster")
    retried = {}
    if problems and not a.no_fallback:
        # Pages that failed verification: rasterise them from the original and check again.
        writer2 = PdfWriter(clone_from=io.BytesIO(data))
        writer2._desk_src_version = pdf_version(data[:16])
        for n in problems:
            rasterise_page(writer2, n - 1, psrc, a.password, rects[n], fill, a.dpi)
            per_page[n]["method"] = "rasterised"
            per_page[n]["why"] = "failed verification: " + "; ".join(problems[n])
            retried[n] = problems[n]
            rasterised.add(n)
        compress_writer(writer2)
        data = writer_bytes(writer2)
        problems = verify(data, rects, patterns, before, rasterised, a.label, numbers)
    if problems:
        raise SkillError("verification failed, nothing was written: " + "; ".join(f"page {n}: {', '.join(v)}" for n, v in problems.items()))
    # Raw scan: literal targets must not appear in any decoded content or string of the output.
    leftovers = raw_scan(data, [p for p in patterns if p[0].startswith("find:")]) if not a.pages else []
    if leftovers:
        raise SkillError("verification failed, nothing was written: the text still appears raw in " + ", ".join(leftovers[:5]))
    if a.label:
        # Labels are added after verification (they are text of our own inside the boxes).
        from pypdf import PdfWriter as _W

        w3 = _W(clone_from=io.BytesIO(data))
        w3._desk_src_version = pdf_version(data[:16])
        from _pdfkit import geometry_of

        for n in rects:
            label_rects = rects[n]
            if n in rasterised:
                # The rasterised page is drawn in display space: map the boxes there.
                g = geometry_of(reader.pages[n - 1])
                label_rects = []
                for r in rects[n]:
                    x0, y0, x1, y1 = g.rect_to_view(*r)
                    label_rects.append((x0, g.height - y1, x1, g.height - y0))
            add_label(w3, w3.pages[n - 1], label_rects, fill, a.label)
        data = writer_bytes(w3)
    from _pdfkit import save_bytes

    save_bytes(data, out)
    for n in per_page:
        per_page[n].setdefault("method", "removed")
    info = {
        "output": str(out),
        "bytes": len(data),
        "matches": total_matches,
        "pages": {str(n): v for n, v in sorted(per_page.items())},
        "scrubbed_strings": scrubbed,
        "unused_references_removed": unused_refs,
        "metadata_stripped": bool(a.strip_metadata),
        "verified": True,
        "pages_checked": len(numbers),
    }
    if hints:
        info["hints"] = hints
    if attachments_note:
        info["attachments"] = attachments_note
    if a.render:
        folder = output_dir(a.render)
        jobs = [{"pdf": str(out), "index": n - 1, "dpi": None, "max_edge": 1568, "out": str(folder / f"{out.stem}-page-{n:03d}.png")} for n in sorted(per_page)[:20]]
        info["rendered"] = [r["path"] for r in render_pages(jobs)]

    def md(d: dict[str, Any]) -> str:
        lines = [f"wrote {d['output']} ({human_size(d['bytes'])}): {d['matches']} match(es) redacted on {len(d['pages'])} page(s); verified: no target is extractable on any of the {d['pages_checked']} searched page(s), and no text remains under the boxes"]
        rows = []
        for n, v in d["pages"].items():
            detail = ", ".join(f"{k.replace('_', ' ')} {v[k]}" for k in ("glyphs", "images_changed", "images_removed", "inline_images", "paths", "forms", "annotations_removed", "marked_text") if v.get(k))
            rows.append([n, v["method"], v["matches"], v["boxes"], detail, v.get("why", "")])
        lines.append("")
        lines.append(md_table(["page", "method", "matches", "boxes", "removed", "why rasterised"], rows))
        if d["scrubbed_strings"]:
            lines.append("scrubbed strings: " + ", ".join(f"{k} {v}" for k, v in d["scrubbed_strings"].items()))
        if d.get("attachments"):
            lines.append("attachments: " + d["attachments"])
        lines += [f"note: {h}" for h in d.get("hints", [])]
        if d.get("rendered"):
            lines += d["rendered"] + ["Look at them with view_image."]
        return "\n".join(lines)

    emit(info, a.format, md)
    return 0


STR_RE = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>(?!>)")
TJ_RE = re.compile(rb"\[((?:\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|[^\[\]()<>])*)\]\s*TJ")
_ESC_RE = re.compile(rb"\\(\r\n|[\r\n]|[0-7]{1,3}|.)", re.S)
_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _unescape(m: re.Match[bytes]) -> bytes:
    g = m.group(1)
    if g in (b"\r\n", b"\r", b"\n"):
        return b""  # a line continuation
    if 0x30 <= g[0] <= 0x37:
        return bytes([int(g, 8) & 0xFF])
    return _ESC.get(g, g)


def pdf_string_text(tok: bytes) -> str:
    """A PDF string token from a content stream, (literal) or <hex>, as text (latin-1, or UTF-16 with a BOM)."""
    if tok[:1] == b"<":
        hexs = re.sub(rb"\s+", b"", tok[1:-1])
        raw = bytes.fromhex((hexs + b"0" * (len(hexs) % 2)).decode("ascii"))
    else:
        body = tok[1:-1]
        raw = _ESC_RE.sub(_unescape, body) if b"\\" in body else body
    if raw[:2] in (b"\xfe\xff", b"\xff\xfe"):
        return raw.decode("utf-16", "replace")
    return raw.decode("latin-1")


def shown_strings(data: bytes) -> list[str]:
    """The strings a content stream shows: each TJ array joined, and every other string on its own."""
    spans = [m.span(1) for m in TJ_RE.finditer(data)]
    out: list[str] = []
    joined: list[str] = []
    k = 0
    for m in STR_RE.finditer(data):
        pos = m.start()
        while k < len(spans) and spans[k][1] <= pos:
            if joined:
                out.append("".join(joined))
                joined = []
            k += 1
        text = pdf_string_text(m.group(0))
        if k < len(spans) and spans[k][0] <= pos:
            joined.append(text)
        else:
            out.append(text)
    if joined:
        out.append("".join(joined))
    return out


def is_content_stream(obj: Any) -> bool:
    """Page contents, form XObjects (annotation appearances too) and tiling patterns: streams that draw text."""
    if obj.get("/Subtype") == "/Form" or obj.get("/PatternType") == 1:
        return True
    return set(obj.keys()) <= {"/Length", "/Filter", "/DecodeParms"}


def _matches(texts: list[str], patterns: list[tuple[str, re.Pattern[str], Any]]) -> bool:
    for t in texts:
        for _label, rx, validate in patterns:
            for m in rx.finditer(t):
                if validate is None or validate(m.group(0)):
                    return True
    return False


def scrub_appearances(writer: Any, patterns: list[tuple[str, re.Pattern[str], Any]]) -> int:
    """Removes annotation appearance streams that still show a target (a note's or stamp's drawn text)."""
    from pypdf.generic import BooleanObject, DictionaryObject, NameObject, StreamObject

    removed = 0
    widgets = False

    def streams(ap: Any) -> list[Any]:
        out = []
        for key in ("/N", "/R", "/D"):
            v = ap.get(key)
            v = v.get_object() if v is not None else None
            if isinstance(v, StreamObject):
                out.append(v)
            elif isinstance(v, DictionaryObject):
                out.extend(x.get_object() for x in v.values() if isinstance(x.get_object(), StreamObject))
        return out

    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            try:
                a = ref.get_object()
                ap = a.get("/AP")
                if ap is None:
                    continue
                texts = []
                for st in streams(ap.get_object()):
                    try:
                        texts.extend(shown_strings(st.get_data()))
                    except Exception:  # noqa: BLE001 — an undecodable stream: leave it to the raw scan
                        continue
                if _matches(texts, patterns):
                    del a["/AP"]
                    removed += 1
                    widgets = widgets or a.get("/Subtype") == "/Widget"
            except Exception:  # noqa: BLE001
                continue
    if widgets and "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"].get_object()[NameObject("/NeedAppearances")] = BooleanObject(True)
    return removed


def raw_scan(data: bytes, patterns: list[tuple[str, re.Pattern[str], Any]]) -> list[str]:
    """Objects of the output whose strings or shown text still match a --find target (outside page text)."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    if not patterns:
        return []

    def strings(obj: Any, depth: int = 0) -> list[str]:
        if depth > 40:
            return []
        out: list[str] = []
        items = obj.values() if isinstance(obj, DictionaryObject) else obj if isinstance(obj, ArrayObject) else []
        for v in items:
            if isinstance(v, IndirectObject):
                continue
            kind = type(v).__name__
            if kind == "TextStringObject":
                out.append(str(v))
            elif kind == "ByteStringObject":
                out.append(bytes(v).decode("latin-1"))
            elif isinstance(v, (DictionaryObject, ArrayObject)):
                out.extend(strings(v, depth + 1))
        return out

    found = []
    r = PdfReader(io.BytesIO(data))
    numbers = sorted({n for entries in r.xref.values() for n in entries} | {n for n in getattr(r, "xref_objStm", {})})
    for num in numbers:
        try:
            obj = r.get_object(num)
        except Exception:  # noqa: BLE001
            continue
        if obj is None:
            continue
        texts = strings(obj) if isinstance(obj, (DictionaryObject, ArrayObject)) else []
        try:
            if hasattr(obj, "get_data"):
                if is_content_stream(obj):
                    texts.extend(shown_strings(obj.get_data()))
                elif obj.get("/Type") == "/Metadata":
                    texts.append(re.sub(r"<[^>]+>", " ", obj.get_data().decode("utf-8", "replace")))
        except Exception:  # noqa: BLE001
            pass
        if _matches(texts, patterns):
            kind = "a form or appearance" if obj.get("/Subtype") == "/Form" else "page content" if hasattr(obj, "get_data") and is_content_stream(obj) else "XMP metadata" if obj.get("/Type") == "/Metadata" else "strings"
            found.append(f"object {num} ({kind})")
    return found


if __name__ == "__main__":
    run_main(main)
