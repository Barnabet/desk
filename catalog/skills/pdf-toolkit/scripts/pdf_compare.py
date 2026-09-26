#!/usr/bin/env python3
"""Compare two PDFs: pages are aligned by content (so an inserted or deleted page does not make every later page
"different"), then each pair gets a text diff and a visual diff. Visual diffs are PNGs with the old page on the
left and the new page on the right, changed regions outlined in red; look at them with view_image.

Examples:
  python3 scripts/pdf_compare.py v1.pdf v2.pdf                       # text + visual, diff images in v1-vs-v2/
  python3 scripts/pdf_compare.py v1.pdf v2.pdf --text-only --context 2
  python3 scripts/pdf_compare.py v1.pdf v2.pdf --visual-only --dpi 120 --out diffs/
  python3 scripts/pdf_compare.py v1.pdf v2.pdf --format json
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from _common import DEFAULT_MAX_CHARS, SkillError, cap, output_dir, parser, pool_map, run_main
from _pdfkit import emit, fmt_num, init_forms, open_pdfium, parse_pages, pdf_input, pdfium_source, plural
from _render import VISION_EDGE, announce


def page_texts(path: Path, password: str | None) -> list[str]:
    from pdf_text import _plain_chunk, shown

    doc = open_pdfium(path, password)
    n = len(doc)
    doc.close()
    return [shown(t) for _, t in _plain_chunk((str(path), password, list(range(n))))]


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def align(a: list[str], b: list[str]) -> list[tuple[int | None, int | None]]:
    """Pairs of 0-based page indexes (None for a page only in one file)."""
    sa, sb = [norm(x) for x in a], [norm(x) for x in b]
    sm = difflib.SequenceMatcher(None, sa, sb, autojunk=False)
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs += list(zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            # Pair the most similar pages in order; leftovers are removed or added pages.
            ai, bj = list(range(i1, i2)), list(range(j1, j2))
            if len(ai) == len(bj):
                pairs += list(zip(ai, bj))
            else:
                block: list[tuple[int | None, int | None]] = []
                used_b: set[int] = set()
                for i in ai:
                    best, score = None, 0.35
                    for j in bj:
                        if j in used_b:
                            continue
                        s = difflib.SequenceMatcher(None, sa[i][:3000], sb[j][:3000], autojunk=False).quick_ratio()
                        if s > score:
                            best, score = j, s
                    if best is None:
                        block.append((i, None))
                    else:
                        used_b.add(best)
                        block.append((i, best))
                for j in bj:
                    if j not in used_b:
                        block.append((None, j))
                # Keep document order: by new page, removed pages right after their old neighbour.
                block.sort(key=lambda p: (p[1] if p[1] is not None else j1 + (p[0] or 0) - i1 + 0.5))
                pairs += block
        elif tag == "delete":
            pairs += [(i, None) for i in range(i1, i2)]
        else:
            pairs += [(None, j) for j in range(j1, j2)]
    return pairs


def text_diff(a: str, b: str, context: int) -> list[str]:
    la, lb = a.splitlines(), b.splitlines()
    out = []
    for line in difflib.unified_diff(la, lb, lineterm="", n=context):
        if line.startswith(("---", "+++")):
            continue
        out.append(line)
    return out


def _render_pair(job: dict[str, Any]) -> dict[str, Any]:
    """Renders both pages, finds changed regions and writes the side-by-side PNG (worker process)."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    def render(path: str, pw: str | None, idx: int) -> Any:
        doc = pdfium.PdfDocument(path, password=pw)
        try:
            init_forms(doc)
            page = doc[idx]
            img = page.render(scale=job["dpi"] / 72.0, may_draw_forms=True).to_pil().convert("RGB")
            page.close()
            return img
        finally:
            doc.close()

    ia = render(job["a"], job["pa"], job["ia"])
    ib = render(job["b"], job["pb"], job["ib"])
    w, h = max(ia.size[0], ib.size[0]), max(ia.size[1], ib.size[1])
    ca = Image.new("RGB", (w, h), "white")
    ca.paste(ia, (0, 0))
    cb = Image.new("RGB", (w, h), "white")
    cb.paste(ib, (0, 0))
    diff = ImageChops.difference(ca, cb).convert("L").point(lambda v: 255 if v > job["threshold"] else 0)
    total = diff.size[0] * diff.size[1]
    hist = diff.histogram()
    changed = hist[255]
    regions: list[tuple[int, int, int, int]] = []
    if changed:
        cell = max(4, round(job["dpi"] / 12))
        # Downsample first (a cell with any changed pixel stays on), then grow by one cell to join neighbours:
        # the same regions as growing the full-size mask, at a fraction of the cost.
        small = diff.resize((max(1, w // cell), max(1, h // cell)), Image.BOX).point(lambda v: 255 if v > 0 else 0)
        small = small.filter(ImageFilter.MaxFilter(3))
        sw, sh = small.size
        px = small.load()
        seen = set()
        for y in range(sh):
            for x in range(sw):
                if px[x, y] and (x, y) not in seen:
                    stack = [(x, y)]
                    seen.add((x, y))
                    x0 = x1 = x
                    y0 = y1 = y
                    while stack:
                        cx, cy = stack.pop()
                        x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
                        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1), (cx + 1, cy + 1), (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1)):
                            if 0 <= nx < sw and 0 <= ny < sh and px[nx, ny] and (nx, ny) not in seen:
                                seen.add((nx, ny))
                                stack.append((nx, ny))
                    regions.append((x0 * cell, y0 * cell, min(w, (x1 + 1) * cell), min(h, (y1 + 1) * cell)))
        regions = _merge(regions, gap=cell * 2)
    out = None
    if changed and job["out"]:
        gap = 16
        from _render import _font

        sheet = Image.new("RGB", (w * 2 + gap * 3, h + gap * 2 + 24), (235, 235, 235))
        draw = ImageDraw.Draw(sheet)
        font = _font(18)
        for k, (img, label) in enumerate(((ca, job["label_a"]), (cb, job["label_b"]))):
            x = gap + k * (w + gap)
            sheet.paste(img, (x, gap + 24))
            draw.text((x, 4), label, fill=(40, 40, 40), font=font)
            for r in regions:
                draw.rectangle([x + r[0] - 2, gap + 24 + r[1] - 2, x + r[2] + 2, gap + 24 + r[3] + 2], outline=(220, 20, 20), width=3)
        from _render import fit_edge

        sheet = fit_edge(sheet, job["max_edge"])
        sheet.save(job["out"])
        out = job["out"]
    scale = 72.0 / job["dpi"]
    return {"changed_percent": round(100 * changed / total, 3) if total else 0, "regions": [[fmt_num(v * scale) for v in r] for r in regions], "image": out}


def _merge(boxes: list[tuple[int, int, int, int]], gap: int) -> list[tuple[int, int, int, int]]:
    boxes = sorted(boxes)
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for b in boxes:
            for o in out:
                if b[0] <= o[2] + gap and o[0] <= b[2] + gap and b[1] <= o[3] + gap and o[1] <= b[3] + gap:
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    changed = True
                    break
            else:
                out.append(list(b))
        boxes = [tuple(o) for o in out]  # type: ignore[misc]
    return boxes  # type: ignore[return-value]


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--pages", help="pages of the OLD file to compare (default all)")
    p.add_argument("--text-only", action="store_true")
    p.add_argument("--visual-only", action="store_true")
    p.add_argument("--dpi", type=float, default=100, help="resolution for the visual comparison (default 100)")
    p.add_argument("--threshold", type=int, default=40, help="pixel difference 0-255 that counts as a change (default 40)")
    p.add_argument("--context", type=int, default=1, help="lines of context around text changes (default 1)")
    p.add_argument("--out", help="folder for the diff images (default <old>-vs-<new>)")
    p.add_argument("--max-images", type=int, default=20, help="write at most this many diff images (default 20)")
    p.add_argument("--force", action="store_true", help="replace diff images left by an earlier comparison")
    p.add_argument("--password-old")
    p.add_argument("--password-new")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()
    pa, pb = pdf_input(a.old), pdf_input(a.new)
    sa, sb = pdfium_source(pa, a.password_old), pdfium_source(pb, a.password_new)
    ta, tb = page_texts(sa, a.password_old), page_texts(sb, a.password_new)
    pairs = align(ta, tb)
    if a.pages:
        keep = set(parse_pages(a.pages, len(ta)))
        pairs = [pr for pr in pairs if (pr[0] is not None and pr[0] + 1 in keep) or (pr[0] is None)]
    results: list[dict[str, Any]] = []
    for i, j in pairs:
        r: dict[str, Any] = {"old_page": i + 1 if i is not None else None, "new_page": j + 1 if j is not None else None}
        if i is None:
            r["status"] = "added"
        elif j is None:
            r["status"] = "removed"
        else:
            same = norm(ta[i]) == norm(tb[j])
            r["status"] = "same text" if same else "text changed"
            if not same and not a.visual_only:
                r["diff"] = text_diff(ta[i], tb[j], a.context)
        results.append(r)
    folder = None
    if not a.text_only:
        target = Path(a.out or f"{pa.stem}-vs-{pb.stem}")
        existed = target.exists()
        folder = output_dir(target)
        jobs = []
        both = [r for r in results if r["old_page"] and r["new_page"]]
        for r in both:
            name = f"diff-p{r['old_page']:03d}-p{r['new_page']:03d}.png"
            jobs.append({"a": str(sa), "b": str(sb), "pa": a.password_old, "pb": a.password_new, "ia": r["old_page"] - 1, "ib": r["new_page"] - 1, "dpi": a.dpi, "threshold": a.threshold, "out": str(folder / name), "label_a": f"{pa.name} p. {r['old_page']}", "label_b": f"{pb.name} p. {r['new_page']}", "max_edge": VISION_EDGE})
        clash = [j["out"] for j in jobs if Path(j["out"]).exists()]
        if clash and not a.force:
            raise SkillError(f"{clash[0]} already exists; pass --force or choose another --out")
        visual = pool_map(_render_pair, jobs, workers=None if len(jobs) >= 4 else 1)
        written = 0
        skipped: list[int] = []
        for r, v in zip(both, visual):
            r["visual_changed_percent"] = v["changed_percent"]
            r["regions"] = v["regions"]
            if v["image"]:
                if written < a.max_images:
                    r["image"] = v["image"]
                    written += 1
                else:
                    Path(v["image"]).unlink(missing_ok=True)
                    skipped.append(r["old_page"])
            if r["status"] == "same text" and v["changed_percent"] > 0:
                r["status"] = "looks different"
        if not written and not existed:
            try:
                folder.rmdir()  # no visual differences: do not leave an empty folder behind
            except OSError:
                pass
            folder = None
    changed = [r for r in results if r["status"] not in ("same text",)]
    summary = {"old": str(pa), "new": str(pb), "old_pages": len(ta), "new_pages": len(tb), "identical": not changed, "changed": len(changed), "pages": results}
    if folder:
        summary["images_folder"] = str(folder)
    capped = ""
    if not a.text_only and skipped:
        from pdf_text import _ranges

        capped = (f"Diff images were written for the first {a.max_images} changed pages only; {plural(len(skipped), 'more changed page')} "
                  f"(old pages {_ranges(skipped[:60])}{'…' if len(skipped) > 60 else ''}) got none. For those: --pages {_ranges(skipped[:a.max_images])} --out another folder, or raise --max-images.")
        summary["images_skipped_pages"] = skipped
        summary["images_note"] = capped
    if a.format == "json":
        emit(summary, "json", max_chars=a.max_chars)
        return 0
    L = [f"# {pa.name} ({plural(len(ta), 'page')}) vs {pb.name} ({plural(len(tb), 'page')})"]
    if not changed:
        L.append("No differences found" + (" in the text." if a.text_only else " in text or appearance."))
    else:
        counts: dict[str, int] = {}
        for r in changed:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        L.append(", ".join(f"{v} page(s) {k}" for k, v in counts.items()) + ".")
    budget = a.max_chars or 0
    used = sum(len(x) + 1 for x in L)
    for k, r in enumerate(changed):
        where = f"old p. {r['old_page']}" if r["old_page"] else ""
        where += (" → " if r["old_page"] and r["new_page"] else "") + (f"new p. {r['new_page']}" if r["new_page"] else "")
        extra = f"; {r['visual_changed_percent']}% of the page changed" if r.get("visual_changed_percent") else ""
        regs = r.get("regions") or []
        if regs:
            extra += "; regions (points, top-left origin): " + "; ".join(", ".join(map(str, g)) for g in regs[:6]) + (" …" if len(regs) > 6 else "")
        part = [f"\n## {where}: {r['status']}{extra}"]
        if r.get("diff"):
            part.append("```diff")
            part.extend(r["diff"][:200])
            if len(r["diff"]) > 200:
                part.append(f"… {len(r['diff']) - 200} more diff lines (use --pages {r['old_page']})")
            part.append("```")
        elif r["status"] == "added":
            part.append(f"(only in {pb.name}) " + norm(tb[r["new_page"] - 1])[:300])
        elif r["status"] == "removed":
            part.append(f"(only in {pa.name}) " + norm(ta[r["old_page"] - 1])[:300])
        cost = sum(len(x) + 1 for x in part)
        if budget and k and used + cost > budget:
            rest = [x["old_page"] for x in changed[k:] if x["old_page"]]
            if rest:
                from pdf_text import shell_arg

                nxt = f"python3 scripts/pdf_compare.py {shell_arg(a.old)} {shell_arg(a.new)} --pages {rest[0]}- --text-only" + (f" --max-chars {a.max_chars}" if a.max_chars != DEFAULT_MAX_CHARS else "")
                L.append(f"\n[… {len(changed) - k} more changed page(s) not shown, to stay under {budget:,} characters. Next: {nxt}]")
            else:
                L.append(f"\n[… {len(changed) - k} more changed page(s) not shown; see --format json]")
            break
        L.extend(part)
        used += cost
    text = "\n".join(L)
    print(cap(text, budget + 5000 if budget else None, "Use --pages to narrow the comparison."))
    if capped:
        print(f"\nnote: {capped}")
    images = [r["image"] for r in results if r.get("image")]
    if images:
        announce(images, "Diff images: the old page is on the left, the new one on the right; changed regions are outlined in red.")
    return 0


if __name__ == "__main__":
    run_main(main)
