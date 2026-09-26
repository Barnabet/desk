#!/usr/bin/env python3
"""Page operations. Every command writes a new PDF (never the input) and reports what it did.

Commands:
  merge OUT IN [IN…]        join PDFs (IN may be file.pdf:2-5); one outline entry per source file
  split IN OUTDIR           --every N (default 1), --at 4,10, --by-outline [--level 1] or --max-size 5MB
  extract IN OUT --pages R  keep only these pages (outline, links and form fields are kept)
  delete IN OUT --pages R   remove these pages
  rotate IN OUT --degrees D rotate pages by 90, 180, 270 or -90 (all pages, or --pages)
  reorder IN OUT --order O  e.g. "3,1,2,4-" (pages may repeat)
  reverse IN OUT            last page first
  insert IN OUT --after N   pages from --from other.pdf [--from-pages R], or --blank K pages (N=0: at the start)
  crop IN OUT               --margins 36 | 1cm,2cm | t,r,b,l ; --box x0,y0,x1,y1 ; or --auto (trim white margins)
  nup IN OUT --n 2|4|6|8|9|16   several pages per sheet [--paper A4] [--border]
  scale IN OUT --paper A4   fit every page (or --pages) onto a paper size, centered, keeping proportions
  interleave ODD EVEN OUT   merge duplex scans (fronts + backs); --reverse-even when backs were scanned last-first
  encrypt / decrypt / metadata   older names for pdf_meta.py commands (still work)

Pages are 1-based: 1-3,7,10- ; "last" and "last-1" work too. Boxes are points from the top-left of the displayed page.

Examples:
  python3 scripts/pdf_pages.py merge all.pdf cover.pdf report.pdf appendix.pdf:1-4
  python3 scripts/pdf_pages.py split book.pdf chapters/ --by-outline
  python3 scripts/pdf_pages.py split scan.pdf parts/ --max-size 9MB
  python3 scripts/pdf_pages.py reorder in.pdf out.pdf --order "1,5-8,2-4,9-"
  python3 scripts/pdf_pages.py insert in.pdf out.pdf --after 3 --from extra.pdf --from-pages 1-2
  python3 scripts/pdf_pages.py crop slides.pdf trimmed.pdf --auto --padding 18
  python3 scripts/pdf_pages.py nup handout.pdf handout-4up.pdf --n 4 --border
  python3 scripts/pdf_pages.py interleave fronts.pdf backs.pdf book.pdf --reverse-even
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Sequence

from _common import SkillError, UsageError, add_format, human_size, output_dir, output_path, parser, run_main
from _pdfkit import (
    Geometry,
    bbox_of_points,
    emit,
    fmt_num,
    geometry_of,
    info_strings,
    mat_apply,
    mat_invert,
    mat_mul,
    new_writer,
    open_reader,
    parse_box,
    parse_length,
    parse_pages,
    parse_paper,
    pdf_input,
    pdf_version,
    pdfium_source,
    plural,
    safe_filename,
    save_writer,
    split_page_suffix,
)


# ── helpers ─────────────────────────────────────────────────────────────


def parse_order(spec: str, count: int) -> list[int]:
    """'3,1,2,4-' → 1-based page numbers in that order; repeats allowed."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            out.extend(parse_pages(part, count))
    if not out:
        raise UsageError("--order is empty")
    return out


def blank_writer() -> Any:
    from pypdf import PdfWriter

    return PdfWriter()


def copy_pages(reader: Any, numbers: list[int]) -> Any:
    """A new writer holding these pages (1-based, in order); keeps outline entries, links and fields when possible."""
    w = blank_writer()
    w._desk_src_version = pdf_version(getattr(reader, "pdf_header", ""))
    if len(set(numbers)) == len(numbers):
        w.append(reader, pages=[n - 1 for n in numbers])
        _keep_metadata(w, reader)
        return w
    added: set[int] = set()
    for n in numbers:
        if n not in added:
            w.add_page(reader.pages[n - 1])
            added.add(n)
        else:
            _add_page_copy(w, reader.pages[n - 1])
    _keep_metadata(w, reader)
    return w


def _add_page_copy(w: Any, page: Any) -> None:
    """Adds a second, independent page object that shares the first copy's content and resources."""
    from pypdf.generic import DictionaryObject, NameObject

    first = page.clone(w)
    dup = DictionaryObject({k: v for k, v in first.items() if k not in ("/Annots", "/Parent", "/StructParents")})
    dup[NameObject("/Type")] = NameObject("/Page")
    from pypdf import PageObject

    po = PageObject(w)
    po.update(dup)
    w.add_page(po)


def _keep_metadata(w: Any, reader: Any) -> None:
    meta = info_strings(reader)
    if meta:
        w.add_metadata(meta)


def write(w: Any, out: Path, a: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    size = save_writer(w, out)
    info = {"output": str(out), "pages": len(w.pages), "bytes": size, **(extra or {})}
    return info


def report(a: Any, info: dict[str, Any]) -> None:
    if a.format == "json":
        emit(info, "json")
        return
    if "outputs" in info:
        lines = [f"wrote {len(info['outputs'])} files to {info['folder']}:"]
        for o in info["outputs"][:60]:
            lines.append(f"- {Path(o['output']).name}: {o['pages']} page{'s' if o['pages'] != 1 else ''}, {human_size(o['bytes'])}" + (f" ({o['title']})" if o.get("title") else "") + (" (over the size limit: a single page this big cannot be split further)" if o.get("over_limit") else ""))
        if len(info["outputs"]) > 60:
            lines.append(f"… and {len(info['outputs']) - 60} more")
        print("\n".join(lines))
        return
    detail = "; ".join(f"{k.replace('_', ' ')}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in info.items() if k not in ("output", "pages", "bytes") and v not in (None, [], ""))
    print(f"wrote {info['output']}: {plural(info['pages'], 'page')}, {human_size(info['bytes'])}" + (f" ({detail})" if detail else ""))


# ── commands ────────────────────────────────────────────────────────────


def cmd_merge(a: Any) -> dict[str, Any]:
    if len(a.inputs) < 1:
        raise UsageError("merge needs at least one input")
    specs = [split_page_suffix(i) for i in a.inputs]
    paths = [pdf_input(p) for p, _ in specs]
    out = output_path(a.out, paths, a.force)
    w = blank_writer()
    sources = []
    for (p, rng), path in zip(specs, paths):
        reader = open_reader(path, a.password)
        numbers = parse_pages(rng, len(reader.pages)) if rng else list(range(1, len(reader.pages) + 1))
        title = None if a.no_outline else path.stem
        start = len(w.pages)
        w.append(reader, outline_item=title, pages=[n - 1 for n in numbers], import_outline=not a.no_outline)
        sources.append({"file": path.name, "pages": len(numbers), "starts_at": start + 1})
    return write(w, out, a, {"sources": sources if a.format == "json" else len(sources)})


def outline_starts(reader: Any, level: int) -> list[tuple[int, str]]:
    """(0-based page, title) for outline entries at `level` (1 = top), sorted by page."""
    found: list[tuple[int, str]] = []

    def walk(items: Any, depth: int) -> None:
        for it in items:
            if isinstance(it, list):
                if depth + 1 <= level:
                    walk(it, depth + 1)
                continue
            if depth == level:
                try:
                    pg = reader.get_destination_page_number(it)
                except Exception:  # noqa: BLE001
                    pg = None
                if pg is not None and pg >= 0:
                    found.append((pg, str(it.title)))

    walk(reader.outline, 1)
    found.sort(key=lambda x: x[0])
    dedup: list[tuple[int, str]] = []
    for pg, title in found:
        if dedup and dedup[-1][0] == pg:
            continue
        dedup.append((pg, title))
    return dedup


def _chunk_size(reader: Any, numbers: list[int]) -> int:
    w = copy_pages(reader, numbers)
    buf = io.BytesIO()
    w.write(buf)
    return buf.tell()


def cmd_split(a: Any) -> dict[str, Any]:
    path = pdf_input(a.input)
    reader = open_reader(path, a.password)
    count = len(reader.pages)
    folder = output_dir(a.outdir)
    stem = safe_filename(a.prefix or path.stem, "part")
    groups: list[tuple[list[int], str | None]] = []
    if a.by_outline:
        starts = outline_starts(reader, a.level)
        if not starts:
            raise SkillError(f"{path.name} has no outline entries at level {a.level}; use --every or --at")
        if starts[0][0] > 0:
            starts.insert(0, (0, "front matter"))
        for i, (pg, title) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else count
            if end > pg:
                groups.append((list(range(pg + 1, end + 1)), title))
    elif a.at:
        cuts = sorted({int(x) for x in re.split(r"[,\s]+", a.at.strip()) if x})
        if any(c < 2 or c > count for c in cuts):
            raise UsageError(f"--at pages must be between 2 and {count} (each starts a new part)")
        bounds = [1, *cuts, count + 1]
        groups = [(list(range(bounds[i], bounds[i + 1])), None) for i in range(len(bounds) - 1)]
    elif a.max_size:
        limit = _parse_size(a.max_size)
        s = 1
        while s <= count:
            lo, hi = s, s  # find the largest e with size(s..e) <= limit
            if _chunk_size(reader, [s]) > limit:
                groups.append(([s], None))
                s += 1
                continue
            step = 1
            while hi < count:
                nxt = min(count, hi + step)
                if _chunk_size(reader, list(range(s, nxt + 1))) <= limit:
                    lo = hi = nxt
                    step *= 2
                else:
                    break
            if hi < count:
                bad = min(count, hi + step)
                while bad - lo > 1:
                    mid = (lo + bad) // 2
                    if _chunk_size(reader, list(range(s, mid + 1))) <= limit:
                        lo = mid
                    else:
                        bad = mid
            groups.append((list(range(s, lo + 1)), None))
            s = lo + 1
    else:
        every = max(1, a.every)
        groups = [(list(range(s, min(s + every, count + 1))), None) for s in range(1, count + 1, every)]
    limit_bytes = _parse_size(a.max_size) if a.max_size else None
    width = max(2, len(str(len(groups))))
    outputs = []
    for i, (numbers, title) in enumerate(groups, 1):
        name = f"{stem}-{i:0{width}d}"
        if title:
            slug = re.sub(r"[^\w-]+", "-", title.lower()).strip("-")[:40]
            name += f"-{slug}" if slug else ""
        out = output_path(folder / f"{name}.pdf", [path], a.force)
        w = copy_pages(reader, numbers)
        size = save_writer(w, out)
        outputs.append({"output": str(out), "pages": len(numbers), "first_page": numbers[0], "bytes": size, **({"title": title} if title else {}), **({"over_limit": True} if limit_bytes and size > limit_bytes else {})})
    return {"folder": str(folder), "outputs": outputs}


def _parse_size(s: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg]?)b?\s*", s.lower())
    if not m:
        raise UsageError(f"bad size '{s}' (use e.g. 5MB, 800KB)")
    return int(float(m.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2)])


def cmd_extract(a: Any, delete: bool = False) -> dict[str, Any]:
    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    count = len(reader.pages)
    chosen = parse_pages(a.pages, count)
    numbers = [n for n in range(1, count + 1) if n not in set(chosen)] if delete else chosen
    if not numbers:
        raise UsageError("that would leave no pages")
    w = copy_pages(reader, numbers)
    return write(w, out, a, {"removed" if delete else "kept": _ranges_text(chosen)})


def _ranges_text(nums: list[int]) -> str:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def cmd_rotate(a: Any) -> dict[str, Any]:
    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    from pypdf import PdfWriter

    w = new_writer(reader)
    targets = parse_pages(a.pages, len(w.pages))
    deg = a.degrees % 360
    for n in targets:
        w.pages[n - 1].rotate(deg)
    return write(w, out, a, {"rotated": f"{_ranges_text(targets)} by {a.degrees}°"})


def cmd_reorder(a: Any, order: list[int] | None = None) -> dict[str, Any]:
    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    count = len(reader.pages)
    numbers = order(count) if callable(order) else parse_order(a.order, count)
    missing = [n for n in range(1, count + 1) if n not in set(numbers)]
    w = copy_pages(reader, numbers)
    return write(w, out, a, {"order": _ranges_text(numbers) if len(numbers) < 200 else f"{len(numbers)} pages", "left_out": _ranges_text(missing)})


def cmd_insert(a: Any) -> dict[str, Any]:
    path = pdf_input(a.input)
    inputs = [path]
    if a.from_pdf:
        inputs.append(pdf_input(a.from_pdf))
    out = output_path(a.out, inputs, a.force)
    reader = open_reader(path, a.password)
    from pypdf import PdfWriter

    w = new_writer(reader)
    count = len(w.pages)
    if a.after in ("end", "last"):
        after = count
    elif re.fullmatch(r"\d+", str(a.after).strip()):
        after = int(a.after)
    else:
        raise UsageError(f"--after takes a page number (0 = before the first page) or 'end', not '{a.after}'")
    if not 0 <= after <= count:
        raise UsageError(f"--after must be between 0 and {count} (or 'end')")
    inserted = 0
    if a.from_pdf:
        other = open_reader(inputs[1], a.from_password or a.password)
        numbers = parse_pages(a.from_pages, len(other.pages))
        for k, n in enumerate(numbers):
            w.insert_page(other.pages[n - 1], after + k)
        inserted = len(numbers)
    elif a.blank:
        ref = w.pages[max(0, min(after, count) - 1)] if count else None
        if a.size:
            pw, ph = parse_paper(a.size)
        elif ref is not None:
            g = geometry_of(ref)
            pw, ph = g.width, g.height
        else:
            pw, ph = parse_paper("a4")
        for k in range(a.blank):
            w.insert_blank_page(pw, ph, after + k)
        inserted = a.blank
    else:
        raise UsageError("insert needs --from other.pdf or --blank N")
    return write(w, out, a, {"inserted": f"{inserted} page(s) after page {after}"})


def parse_margins(spec: str) -> tuple[float, float, float, float]:
    """CSS-like: '36' | 'v,h' | 't,r,b,l' (units allowed) → (top, right, bottom, left) points."""
    parts = [parse_length(x) for x in re.split(r"[,\s]+", spec.strip()) if x]
    if len(parts) == 1:
        return parts[0], parts[0], parts[0], parts[0]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0], parts[1]
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    raise UsageError("--margins takes 1, 2 or 4 values: all | vertical,horizontal | top,right,bottom,left")


def _auto_box(job: tuple[str, str | None, int, float]) -> tuple[float, float, float, float] | None:
    """The view-space box of non-white content on a page (rendered at 72-100 dpi)."""
    import pypdfium2 as pdfium

    path, password, index, threshold = job
    doc = pdfium.PdfDocument(path, password=password)
    try:
        page = doc[index]
        vw, vh = page.get_size()
        scale = min(1.5, 1400 / max(vw, vh))
        img = page.render(scale=scale, may_draw_forms=False).to_pil().convert("L")
        mask = img.point(lambda v: 255 if v < threshold else 0)
        box = mask.getbbox()
        if not box:
            return None
        x0, y0, x1, y1 = box
        return x0 / scale, y0 / scale, x1 / scale, y1 / scale
    finally:
        doc.close()


def _set_box(page: Any, rect: tuple[float, float, float, float]) -> None:
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    arr = ArrayObject([FloatObject(round(v, 3)) for v in rect])
    page[NameObject("/MediaBox")] = arr
    page[NameObject("/CropBox")] = ArrayObject(list(arr))
    for k in ("/TrimBox", "/BleedBox", "/ArtBox"):
        if k in page:
            del page[k]


def cmd_crop(a: Any) -> dict[str, Any]:
    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    from pypdf import PdfWriter

    w = new_writer(reader)
    targets = parse_pages(a.pages, len(w.pages))
    modes = sum(bool(x) for x in (a.margins, a.box, a.auto))
    if modes != 1:
        raise UsageError("crop needs exactly one of --margins, --box or --auto")
    auto_boxes: dict[int, Any] = {}
    if a.auto:
        from _common import pool_map

        jobs = [(str(pdfium_source(path, a.password)), a.password, n - 1, 245.0) for n in targets]
        for n, box in zip(targets, pool_map(_auto_box, jobs, workers=None if len(jobs) >= 6 else 1)):
            auto_boxes[n] = box
    changed: list[int] = []
    skipped: list[int] = []
    for n in targets:
        page = w.pages[n - 1]
        g = geometry_of(page)
        if a.margins:
            t, r, b, l = parse_margins(a.margins)
            view = (l, t, g.width - r, g.height - b)
        elif a.box:
            view = parse_box(a.box, g.width, g.height)
        else:
            box = auto_boxes.get(n)
            if box is None:
                skipped.append(n)  # a blank page
                continue
            pad = parse_length(a.padding)
            view = (max(0, box[0] - pad), max(0, box[1] - pad), min(g.width, box[2] + pad), min(g.height, box[3] + pad))
        if view[2] - view[0] < 10 or view[3] - view[1] < 10:
            if a.auto:
                skipped.append(n)  # tiny content (or a tiny page): leave the page as it is
                continue
            raise UsageError(f"page {n}: the crop leaves less than 10 points")
        _set_box(page, g.rect_from_view(*view))
        changed.append(n)
    extra = {"cropped": _ranges_text(changed), "left_as_they_were": _ranges_text(skipped), "note": "cropping hides content outside the box; it is still in the file (use pdf_redact.py to remove content)"}
    return write(w, out, a, extra)


NUP_GRID = {2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 9: (3, 3), 16: (4, 4)}


def _view_matrix(g: Geometry) -> tuple[float, float, float, float, float, float]:
    """User space → displayed page space with the origin at the bottom-left."""
    inv = mat_invert(g.overlay_matrix())
    assert inv is not None
    return inv


def _page_form(w: Any, page: Any, g: Geometry) -> Any:
    """The page as a Form XObject (its content bytes untouched, clipped to its crop box), so n-up sheets draw pages
    without parsing their content streams."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, EncodedStreamObject, FloatObject, NameObject

    contents = page.get("/Contents")
    obj = contents.get_object() if contents is not None else None
    if obj is not None and not isinstance(obj, ArrayObject) and "/Filter" in obj:
        form: Any = EncodedStreamObject()
        form._data = obj._data  # the encoded bytes as they are
        for key in ("/Filter", "/DecodeParms"):
            if key in obj:
                form[NameObject(key)] = obj[key].clone(w)
    else:
        parts = [obj] if obj is not None and not isinstance(obj, ArrayObject) else list(obj or [])
        form = DecodedStreamObject()
        form.set_data(b"\n".join(p.get_object().get_data() for p in parts))
        form = form.flate_encode()
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject([FloatObject(v) for v in (g.l, g.b, g.r, g.t)])
    res = page.get("/Resources")
    if res is not None:
        clone = res.get_object().clone(w)
        ref = getattr(clone, "indirect_reference", None)
        form[NameObject("/Resources")] = ref if ref is not None and ref.pdf is w else clone
    if "/Group" in page:
        form[NameObject("/Group")] = page["/Group"].get_object().clone(w)
    return w._add_object(form)


def _moved_links(w: Any, page: Any, m: Sequence[float]) -> list[Any]:
    """Web links of a page, cloned with their rectangles moved by the matrix m (links to pages are dropped)."""
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    out = []
    for annot in page.get("/Annots", []) or []:
        try:
            ao = annot.get_object()
            action = ao.get("/A")
            if ao.get("/Subtype") != "/Link" or action is None or action.get_object().get("/S") != "/URI":
                continue
            x0, y0, x1, y1 = (float(v) for v in ao["/Rect"])
            pts = [mat_apply(m, x, y) for x, y in ((x0, y0), (x1, y1), (x0, y1), (x1, y0))]
            copy = ao.clone(w, ignore_fields=("/P", "/Parent"))
            copy = copy.get_object() if hasattr(copy, "get_object") else copy
            copy[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in bbox_of_points(pts)])
            out.append(copy.indirect_reference if getattr(copy, "indirect_reference", None) is not None else w._add_object(copy))
        except Exception:  # noqa: BLE001 — a malformed annotation is simply not carried over
            continue
    return out


def cmd_nup(a: Any) -> dict[str, Any]:
    from pypdf import PageObject
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    if a.n not in NUP_GRID:
        raise UsageError(f"--n must be one of {', '.join(map(str, NUP_GRID))}")
    big, small = NUP_GRID[a.n]
    first = geometry_of(reader.pages[0])
    pw, ph = (first.width, first.height) if a.paper == "same" else parse_paper(a.paper)
    portrait = (min(pw, ph), max(pw, ph), small, big)  # sheet w, h, cols, rows
    landscape = (max(pw, ph), min(pw, ph), big, small)

    def cell_fit(o: tuple[float, float, int, int]) -> float:
        return min(o[0] / o[2] / first.width, o[1] / o[3] / first.height)

    if a.orientation == "portrait":
        choice = portrait
    elif a.orientation == "landscape":
        choice = landscape
    else:
        choice = portrait if cell_fit(portrait) > cell_fit(landscape) + 1e-9 else landscape
    sw, sh, cols, rows = choice
    margin = parse_length(a.margin)
    gap = parse_length(a.gap)
    cw = (sw - 2 * margin - (cols - 1) * gap) / cols
    chh = (sh - 2 * margin - (rows - 1) * gap) / rows
    from pypdf import PdfWriter

    w = PdfWriter()
    w._desk_src_version = pdf_version(getattr(reader, "pdf_header", ""))
    pages = list(reader.pages)
    sheets = 0
    for s in range(0, len(pages), a.n):
        sheet = PageObject.create_blank_page(w, sw, sh)
        w.add_page(sheet)
        sheet = w.pages[-1]
        xobjects = DictionaryObject()
        ops, border_ops, annots = [], [], ArrayObject()
        for k, src in enumerate(pages[s : s + a.n]):
            r, c = (divmod(k, cols) if a.order == "across" else (k % rows, k // rows))
            g = geometry_of(src)
            scale = min(cw / g.width, chh / g.height)
            dw, dh = g.width * scale, g.height * scale
            x = margin + c * (cw + gap) + (cw - dw) / 2
            y = sh - margin - r * (chh + gap) - chh + (chh - dh) / 2
            m = mat_mul(mat_mul(_view_matrix(g), (scale, 0, 0, scale, 0, 0)), (1, 0, 0, 1, x, y))
            name = f"/P{k}"
            xobjects[NameObject(name)] = _page_form(w, src, g)
            ops.append(f"q {' '.join(f'{v:.6f}' for v in m)} cm {name} Do Q")
            annots.extend(_moved_links(w, src, m))
            if a.border:
                border_ops.append(f"{x:.2f} {y:.2f} {dw:.2f} {dh:.2f} re")
        if border_ops:
            ops.append("q 0.6 G 0.5 w " + " ".join(border_ops) + " S Q")
        sheet[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode())
        sheet[NameObject("/Contents")] = w._add_object(stream.flate_encode())
        if annots:
            sheet[NameObject("/Annots")] = annots
        sheet[NameObject("/MediaBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(sw), FloatObject(sh)])
        sheets += 1
    _keep_metadata(w, reader)
    return write(w, out, a, {"layout": f"{a.n} per sheet ({cols}×{rows}) on {fmt_num(sw)}×{fmt_num(sh)} pt", "source_pages": len(pages)})


def _append_content(w: Any, page: Any, data: bytes) -> None:
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

    s = DecodedStreamObject()
    s.set_data(data)
    ref = w._add_object(s)
    contents = page.get("/Contents")
    if contents is None:
        page[NameObject("/Contents")] = ref
        return
    obj = contents.get_object()
    if isinstance(obj, ArrayObject):
        obj.append(ref)
    else:
        page[NameObject("/Contents")] = ArrayObject([contents, ref])


def _wrap_contents(w: Any, page: Any, before: bytes, after: bytes) -> None:
    """Puts content before and after the page's own content streams without parsing them (fast on big pages)."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

    def ref(data: bytes) -> Any:
        s = DecodedStreamObject()
        s.set_data(data)
        return w._add_object(s)

    contents = page.get("/Contents")
    parts: list[Any] = []
    if contents is not None:
        obj = contents.get_object()
        parts = list(obj) if isinstance(obj, ArrayObject) else [contents]
    page[NameObject("/Contents")] = ArrayObject([ref(before), *parts, ref(after)])


def cmd_scale(a: Any) -> dict[str, Any]:
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    path = pdf_input(a.input)
    out = output_path(a.out, [path], a.force)
    reader = open_reader(path, a.password)
    w = new_writer(reader)
    tw, th = parse_paper(a.paper)
    targets = parse_pages(a.pages, len(w.pages))
    for n in targets:
        page = w.pages[n - 1]
        g = geometry_of(page)
        if a.orientation == "auto":
            W, H = (max(tw, th), min(tw, th)) if g.width > g.height else (min(tw, th), max(tw, th))
        elif a.orientation == "landscape":
            W, H = max(tw, th), min(tw, th)
        else:
            W, H = min(tw, th), max(tw, th)
        s = min(W / g.width, H / g.height)
        if a.shrink_only:
            s = min(s, 1.0)
        dx, dy = (W - g.width * s) / 2, (H - g.height * s) / 2
        m = mat_mul(mat_mul(_view_matrix(g), (s, 0, 0, s, 0, 0)), (1, 0, 0, 1, dx, dy))
        _wrap_contents(w, page, f"q {' '.join(f'{v:.6f}' for v in m)} cm\n".encode(), b"\nQ\n")
        for annot in page.get("/Annots", []) or []:
            ao = annot.get_object()
            rect = ao.get("/Rect")
            if rect is None:
                continue
            x0, y0, x1, y1 = (float(v) for v in rect)
            pts = [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in ((x0, y0), (x1, y1), (x0, y1), (x1, y0))]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            ao[NameObject("/Rect")] = ArrayObject([FloatObject(min(xs)), FloatObject(min(ys)), FloatObject(max(xs)), FloatObject(max(ys))])
        if "/Rotate" in page:
            del page["/Rotate"]
        _set_box(page, (0, 0, W, H))
    return write(w, out, a, {"scaled": f"{_ranges_text(targets)} to {fmt_num(tw)}×{fmt_num(th)} pt"})


def cmd_interleave(a: Any) -> dict[str, Any]:
    odd_p, even_p = pdf_input(a.odd), pdf_input(a.even)
    out = output_path(a.out, [odd_p, even_p], a.force)
    odd = open_reader(odd_p, a.password)
    even = open_reader(even_p, a.password)
    evens = list(even.pages)
    if a.reverse_even:
        evens.reverse()
    w = blank_writer()
    odds = list(odd.pages)
    for i in range(max(len(odds), len(evens))):
        if i < len(odds):
            w.add_page(odds[i])
        if i < len(evens):
            w.add_page(evens[i])
    note = "" if len(odds) == len(evens) else f"page counts differ ({len(odds)} vs {len(evens)}); the extra pages were appended"
    return write(w, out, a, {"note": note})


def cmd_legacy(a: Any) -> dict[str, Any]:
    import pdf_meta

    if a.cmd == "encrypt":
        return pdf_meta.do_encrypt(a.input, a.out, a.password, None, None, "AES-256", a.force, None)
    if a.cmd == "decrypt":
        return pdf_meta.do_decrypt(a.input, a.out, a.password, a.force)
    fields = {k: getattr(a, k) for k in ("title", "author", "subject", "keywords") if getattr(a, k)}
    return pdf_meta.do_set(a.input, a.out, fields, a.password, a.force)


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def common(sp: Any, pages: bool = False, pages_required: bool = False) -> None:
        if pages:
            sp.add_argument("--pages", required=pages_required, help="pages like 1-3,7,10- (default: all)" if not pages_required else "pages like 1-3,7,10-")
        sp.add_argument("--password", help="password of an encrypted input")
        sp.add_argument("--force", action="store_true", help="replace an existing output")
        add_format(sp)

    sp = sub.add_parser("merge", help="join PDFs")
    sp.add_argument("out")
    sp.add_argument("inputs", nargs="+", help="PDFs, optionally with pages: file.pdf:2-5")
    sp.add_argument("--no-outline", action="store_true", help="no outline entry per file (and drop the inputs' outlines)")
    common(sp)

    sp = sub.add_parser("split", help="split into several files")
    sp.add_argument("input")
    sp.add_argument("outdir")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--every", type=int, default=1, help="pages per part (default 1)")
    g.add_argument("--at", help="page numbers that start a new part, e.g. 5,12")
    g.add_argument("--by-outline", action="store_true", help="one part per outline (bookmark) entry")
    g.add_argument("--max-size", help="largest part size, e.g. 9MB")
    sp.add_argument("--level", type=int, default=1, help="outline level for --by-outline (default 1)")
    sp.add_argument("--prefix", help="file name prefix (default: the input's name)")
    common(sp)

    for name, helptext in (("extract", "keep only some pages"), ("delete", "remove pages")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("input")
        sp.add_argument("out")
        common(sp, pages=True, pages_required=True)

    sp = sub.add_parser("rotate", help="rotate pages")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--degrees", type=int, choices=[90, 180, 270, -90, -180, -270], required=True)
    common(sp, pages=True)

    sp = sub.add_parser("reorder", help="put pages in a new order")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--order", required=True, help='e.g. "3,1,2,4-" (pages may repeat; left-out pages are dropped)')
    common(sp)

    sp = sub.add_parser("reverse", help="reverse the page order")
    sp.add_argument("input")
    sp.add_argument("out")
    common(sp)

    sp = sub.add_parser("insert", help="insert pages from another PDF, or blank pages")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--after", default="end", help="insert after this page (0 = before the first page; default: end)")
    sp.add_argument("--from", dest="from_pdf", help="PDF to take pages from")
    sp.add_argument("--from-pages", help="pages of --from to insert (default: all)")
    sp.add_argument("--from-password", help="password of --from")
    sp.add_argument("--blank", type=int, help="insert this many blank pages")
    sp.add_argument("--size", help="blank page size (default: like the neighbouring page), e.g. A4, Letter")
    common(sp)

    sp = sub.add_parser("crop", help="crop pages")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--margins", help="cut this much from the edges: 36 | 1cm,2cm | top,right,bottom,left")
    sp.add_argument("--box", help="keep only x0,y0,x1,y1 (points from the top-left, or fractions)")
    sp.add_argument("--auto", action="store_true", help="trim white margins around the content")
    sp.add_argument("--padding", default="12", help="space kept around content with --auto (default 12pt)")
    common(sp, pages=True)

    sp = sub.add_parser("nup", help="several pages per sheet")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--n", type=int, default=2, help="pages per sheet: 2, 4, 6, 8, 9 or 16 (default 2)")
    sp.add_argument("--paper", default="same", help="sheet size: same (as page 1), A4, Letter, A3 … (default same)")
    sp.add_argument("--orientation", choices=["auto", "portrait", "landscape"], default="auto")
    sp.add_argument("--order", choices=["across", "down"], default="across", help="fill rows first (across) or columns first")
    sp.add_argument("--margin", default="18", help="sheet margin (default 18pt)")
    sp.add_argument("--gap", default="10", help="space between pages (default 10pt)")
    sp.add_argument("--border", action="store_true", help="draw a thin frame around each page")
    common(sp)

    sp = sub.add_parser("scale", help="fit pages onto a paper size")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--paper", required=True, help="A4, Letter, Legal, A3, A5 or WxH like 210x297mm")
    sp.add_argument("--orientation", choices=["auto", "portrait", "landscape"], default="auto", help="auto keeps each page's orientation")
    sp.add_argument("--shrink-only", action="store_true", help="never enlarge small pages")
    common(sp, pages=True)

    sp = sub.add_parser("interleave", help="combine fronts and backs of a duplex scan")
    sp.add_argument("odd")
    sp.add_argument("even")
    sp.add_argument("out")
    sp.add_argument("--reverse-even", action="store_true", help="the backs are in reverse order (scanned last page first)")
    common(sp)

    for name in ("encrypt", "decrypt"):
        sp = sub.add_parser(name, help=f"(old name) same as pdf_meta.py {name}")
        sp.add_argument("input")
        sp.add_argument("out")
        sp.add_argument("--password", required=True)
        sp.add_argument("--force", action="store_true")
        add_format(sp)
    sp = sub.add_parser("metadata", help="(old name) same as pdf_meta.py set")
    sp.add_argument("input")
    sp.add_argument("out")
    for key in ("title", "author", "subject", "keywords"):
        sp.add_argument(f"--{key}")
    common(sp)

    a = p.parse_args()
    handlers = {
        "merge": cmd_merge,
        "split": cmd_split,
        "extract": cmd_extract,
        "delete": lambda x: cmd_extract(x, delete=True),
        "rotate": cmd_rotate,
        "reorder": cmd_reorder,
        "reverse": lambda x: cmd_reorder(x, order=lambda n: list(range(n, 0, -1))),
        "insert": cmd_insert,
        "crop": cmd_crop,
        "nup": cmd_nup,
        "scale": cmd_scale,
        "interleave": cmd_interleave,
        "encrypt": cmd_legacy,
        "decrypt": cmd_legacy,
        "metadata": cmd_legacy,
    }
    info = handlers[a.cmd](a)
    report(a, info)
    return 0


if __name__ == "__main__":
    run_main(main)
