#!/usr/bin/env python3
"""Compare images: a visual diff with changed regions boxed and metrics, or perceptual hashes to find duplicates.

Subcommands:
  diff A B      (default) metrics — % of pixels changed, mean absolute error, RMSE, PSNR, SSIM — the changed regions
                as boxes, and a diff PNG (A | B | heat map, regions numbered) to look at with view_image
  dupes DIR…    exact duplicates (same bytes) and near-duplicates (resized, recompressed, lightly edited) by pHash and
                dHash, grouped around the best copy to keep (the largest): every member is within --threshold of it,
                never chained through other members; --sheet renders each group for a visual check. Big folders are
                paged (--max-chars, --offset); the output ends with the command for the next groups
  similar Q DIR…  images most like Q, ranked by perceptual distance
  hash FILE…    aHash, dHash and pHash (64-bit hex) of each image

Examples:
  python3 scripts/img_compare.py before.png after.png
  python3 scripts/img_compare.py expected.png actual.png --threshold 0 --out diff.png --format json
  python3 scripts/img_compare.py dupes ~/Pictures/trip -r --sheet
  python3 scripts/img_compare.py similar query.jpg photos/ --top 5
  python3 scripts/img_compare.py hash a.jpg b.jpg

Images of different sizes are compared after scaling B to A's size (reported). Distances: 0 = identical hash; up to
about 8 of 64 bits is the same picture resized or recompressed; above 16 is a different picture.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from _common import SkillError, add_format, emit, human_size, md_table, parser, run_main

SUBCOMMANDS = {"diff", "dupes", "similar", "hash"}


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="visual diff and metrics of two images")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--out", help="diff PNG path (default renders/<a>-vs-<b>-diff.png)")
    d.add_argument("--threshold", type=int, default=10, help="per-channel difference (0-255) that counts as changed (default 10; 0 = exact)")
    d.add_argument("--max-regions", type=int, default=20, help="changed regions to list (default 20)")
    d.add_argument("--no-image", action="store_true", help="metrics only, no diff PNG")
    d.add_argument("--no-orient", action="store_true", help="ignore EXIF orientation")
    add_format(d)
    for name, help_ in (("dupes", "group duplicates and near-duplicates"), ("similar", "rank images by similarity to a query")):
        s = sub.add_parser(name, help=help_)
        if name == "similar":
            s.add_argument("query")
        s.add_argument("inputs", nargs="+", help="files, folders or globs")
        s.add_argument("-r", "--recursive", action="store_true")
        s.add_argument("--threshold", type=int, default=8, help="max pHash distance for near-duplicates (default 8 of 64)")
        s.add_argument("--hash", choices=["phash", "dhash", "ahash", "both"], default="both", help="both = pHash and dHash must agree (default)")
        s.add_argument("--workers", type=int)
        s.add_argument("--no-cache", action="store_true", help="hash every file again")
        if name == "dupes":
            s.add_argument("--exact-only", action="store_true", help="only byte-identical files")
            s.add_argument("--sheet", action="store_true", help="render a contact sheet per group shown (up to 12 groups)")
            from _paging import add_paging

            add_paging(s, "groups")
        else:
            s.add_argument("--top", type=int, default=10)
            s.add_argument("--max-chars", type=int, default=60000, help="text budget (default 60000; 0 = no limit)")
        add_format(s)
    h = sub.add_parser("hash", help="perceptual hashes")
    h.add_argument("inputs", nargs="+")
    h.add_argument("-r", "--recursive", action="store_true")
    h.add_argument("--workers", type=int)
    h.add_argument("--no-cache", action="store_true", help="hash every file again")
    h.add_argument("--max-chars", type=int, default=60000, help="text budget (default 60000; 0 = no limit)")
    add_format(h)
    return p


# ── diff ────────────────────────────────────────────────────────────────


def cmd_diff(args: Any) -> int:
    from PIL import Image

    from _common import input_file
    from _img import load
    from _metrics import gray, pixel_metrics, regions, ssim

    pa, pb = input_file(args.a), input_file(args.b)
    A = load(pa, orient=not args.no_orient).img
    B = load(pb, orient=not args.no_orient).img
    from _img import normalize

    A, B = normalize(A).convert("RGBA"), normalize(B).convert("RGBA")
    notes = []
    if A.size != B.size:
        ra, rb = A.size[0] / A.size[1], B.size[0] / B.size[1]
        notes.append(f"sizes differ ({A.size[0]}×{A.size[1]} vs {B.size[0]}×{B.size[1]}); B was scaled to A's size" + ("; the aspect ratios differ too" if abs(ra - rb) / ra > 0.01 else ""))
        B = B.resize(A.size, Image.LANCZOS)
    m = pixel_metrics(A, B, max(0, args.threshold))
    edge = 1024
    s, smap = ssim(gray(A, edge), gray(B, edge))
    boxes = regions(m["_mask"], args.max_regions)
    res: dict[str, Any] = {
        "a": str(pa), "b": str(pb), "size": list(A.size),
        "identical": m["max_diff"] == 0,
        "changed_pct": m["changed_pct"], "changed_pixels": m["changed_pixels"], "threshold": args.threshold,
        "mae": m["mae"], "rmse": m["rmse"], "psnr_db": m["psnr_db"], "ssim": round(s, 5), "max_diff": m["max_diff"],
        "regions": [{"n": i + 1, "x": x, "y": y, "w": w, "h": h} for i, (x, y, w, h) in enumerate(boxes)],
    }
    if notes:
        res["notes"] = notes
    if not args.no_image:
        out = Path(args.out) if args.out else Path("renders") / f"{_stem(pa)}-vs-{_stem(pb)}-diff.png"
        from _common import same_file

        if same_file(out, pa) or same_file(out, pb):
            raise SkillError("refusing to overwrite an input with the diff image")
        out.parent.mkdir(parents=True, exist_ok=True)
        diff_image(A, B, m["_dmax"], boxes, pa.name, pb.name).save(out, "PNG", compress_level=3)
        res["diff_image"] = str(out)
    if args.format == "json":
        emit(res)
    else:
        pct, npx = res["changed_pct"], res["changed_pixels"]
        if res["identical"]:
            verdict = "identical pixels"
        elif npx == 0:
            verdict = f"no pixel differs by more than {args.threshold}"
        elif pct >= 0.01:
            verdict = f"{pct:.3g}% of pixels differ by more than {args.threshold}"
        else:
            verdict = f"{npx} pixel{'s' if npx != 1 else ''} (under 0.01%) differ{'s' if npx == 1 else ''} by more than {args.threshold}"
        lines = [f"{pa.name} vs {pb.name} ({A.size[0]}×{A.size[1]}): {verdict}"]
        psnr = "∞ (identical)" if res["psnr_db"] is None else f"{res['psnr_db']} dB"
        lines.append(f"- SSIM {res['ssim']:.4f} (1 = same structure), PSNR {psnr}, mean abs error {res['mae']}, max diff {res['max_diff']}")
        for n in notes:
            lines.append(f"- note: {n}")
        if boxes:
            lines.append("- changed regions (x,y,w,h; largest first): " + "; ".join(f"{r['n']}: {r['x']},{r['y']},{r['w']},{r['h']}" for r in res["regions"]))
        print("\n".join(lines))
        if res.get("diff_image"):
            from _render import announce

            announce([res["diff_image"]], "Panels: A | B | difference heat map; numbered boxes match the regions.")
    return 0


def _stem(p: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in p.stem)[:40] or "img"


def diff_image(A: Any, B: Any, dmax: Any, boxes: list[tuple[int, int, int, int]], name_a: str, name_b: str) -> Any:
    """A | B | heat map (changed pixels in red over a faded A), regions boxed and numbered, fitted for vision."""
    import numpy as np
    from PIL import Image, ImageDraw

    from _img import flatten
    from _render import VISION_EDGE, _font

    w, h = A.size
    gap, head = 10, 26
    horiz_scale = min((VISION_EDGE - 4 * gap) / (3 * w), (VISION_EDGE - head - 2 * gap) / h)
    vert_scale = min((VISION_EDGE - 2 * gap) / w, (VISION_EDGE - 3 * head - 4 * gap) / (3 * h))
    horizontal = horiz_scale >= vert_scale
    sc = min(1.0, horiz_scale if horizontal else vert_scale)
    pw, ph = max(1, round(w * sc)), max(1, round(h * sc))
    sa, sb = A.resize((pw, ph), Image.LANCZOS), B.resize((pw, ph), Image.LANCZOS)
    fa, fb = flatten(sa, "checker"), flatten(sb, "checker")
    faded = np.asarray(flatten(sa, "white").convert("L"), dtype=np.float32) * 0.35 + 165
    # Max-pool before scaling down, so a change of a few pixels in a large image still shows.
    f = max(1, int(np.ceil(max(w / pw, h / ph))))
    pooled = dmax
    if f > 1:
        hh, ww = dmax.shape[0], dmax.shape[1]
        pad = np.zeros((-(-hh // f) * f, -(-ww // f) * f), dtype=dmax.dtype)
        pad[:hh, :ww] = dmax
        pooled = pad.reshape(pad.shape[0] // f, f, pad.shape[1] // f, f).max(axis=(1, 3))
    heat = np.asarray(Image.fromarray(pooled.clip(0, 255).astype(np.uint8)).resize((pw, ph), Image.BILINEAR), dtype=np.float32)
    # Any change shows clearly: scale so small differences are visible.
    k = np.clip(heat / max(1.0, min(64.0, float(heat.max()))), 0, 1)
    rgb = np.stack([faded * (1 - k) + 230 * k, faded * (1 - k) + 20 * k, faded * (1 - k) + 60 * k], axis=2)
    fh = Image.fromarray(rgb.clip(0, 255).astype(np.uint8), "RGB")
    panels = [(fa, f"A: {name_a}"), (fb, f"B: {name_b}"), (fh, "difference")]
    if horizontal:
        W, H = 4 * gap + 3 * pw, head + 2 * gap + ph
    else:
        W, H = 2 * gap + pw, 3 * (head + ph) + 4 * gap
    canvas = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(canvas)
    font = _font(14)
    for i, (panel, label) in enumerate(panels):
        x = gap + i * (pw + gap) if horizontal else gap
        y = gap if horizontal else gap + i * (head + ph + gap)
        d.text((x, y + 4), label[:60], fill=(30, 30, 30), font=font)
        canvas.paste(panel, (x, y + head))
        for n, (bx, by, bw, bh) in enumerate(boxes, 1):
            x0, y0 = x + bx * sc, y + head + by * sc
            x1, y1 = x0 + max(3, bw * sc), y0 + max(3, bh * sc)
            d.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], outline=(255, 0, 90), width=2)
            d.text((x0, max(y + head, y0 - 16)), str(n), fill=(255, 0, 90), font=font)
    return canvas


# ── hashes, dupes, similar ──────────────────────────────────────────────


def peek_hash(path: str) -> dict[str, Any] | None:
    from _img import cache_peek, code_version

    rec = cache_peek(path, "img-hash", {}, code_version("img_compare.py", "_img.py", "_metrics.py"))
    return None if rec is None else {**rec, "file": path}


def hash_job(path: str) -> dict[str, Any]:
    """Hashes of one file, from the file cache when this file content was hashed before (errors are not cached)."""
    from _img import Uncached, cached_value, code_version

    def compute() -> dict[str, Any]:
        rec = compute_hashes(path)
        if "error" in rec:
            raise Uncached(rec)
        return rec

    try:
        rec = dict(cached_value(path, "img-hash", {}, code_version("img_compare.py", "_img.py", "_metrics.py"), compute))
    except Uncached as e:
        rec = dict(e.rec)
    except OSError as e:
        return {"file": path, "error": f"cannot read: {e}"}
    rec["file"] = path
    return rec


def compute_hashes(path: str) -> dict[str, Any]:
    from _img import load
    from _metrics import ahash, dhash, phash

    p = Path(path)
    rec: dict[str, Any] = {"file": path}
    try:
        data = p.read_bytes()
        rec["sha256"] = hashlib.sha256(data).hexdigest()
        rec["bytes"] = len(data)
        ld = load(p, max_edge=256)
        w, h = ld.native_size
        if ld.oriented in (5, 6, 7, 8):
            w, h = h, w
        rec.update(width=w, height=h, format=ld.format)
        rec["ahash"], rec["dhash"], rec["phash"] = f"{ahash(ld.img):016x}", f"{dhash(ld.img):016x}", f"{phash(ld.img):016x}"
    except SkillError as e:
        rec["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _hash_all(paths: list[str], workers: int | None) -> list[dict[str, Any]]:
    from _img import cached_map

    return cached_map(hash_job, paths, peek_hash, workers=workers, min_pool=6)


def _dist(a: dict[str, Any], b: dict[str, Any], kind: str) -> int:
    from _metrics import hamming

    if kind == "both":
        return max(hamming(int(a["phash"], 16), int(b["phash"], 16)), hamming(int(a["dhash"], 16), int(b["dhash"], 16)) - 4)
    return hamming(int(a[kind], 16), int(b[kind], 16))


def cmd_hash(args: Any) -> int:
    from _img import expand_inputs

    recs = _hash_all([str(i.path) for i in expand_inputs(args.inputs, recursive=args.recursive)], args.workers)
    if args.format == "json":
        emit(recs, max_chars=args.max_chars or None)
    else:
        rows = [[Path(r["file"]).name, r.get("ahash", ""), r.get("dhash", ""), r.get("phash", ""), f"{r.get('width', '')}×{r.get('height', '')}" if "width" in r else "error: " + str(r.get("error", ""))] for r in recs]
        from _common import cap

        print(cap(md_table(["file", "aHash", "dHash", "pHash", "size"], rows), args.max_chars or None, "Use --format json (never cut mid-item), or hash fewer files."))
    return 0 if all("error" not in r for r in recs) else 1


def cmd_dupes(args: Any) -> int:
    import numpy as np

    from _img import expand_inputs
    from _metrics import pairwise_close

    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    recs = _hash_all([str(i.path) for i in inputs], args.workers)
    one_base = len({str(i.base) for i in inputs}) == 1
    shown_name = {str(i.path): (str(i.rel).replace("\\", "/") if one_base else str(i.path)) for i in inputs}
    ok = [r for r in recs if "error" not in r]
    errors = [r for r in recs if "error" in r]
    # Units of byte-identical files, then groups built around a keeper: the best copy (most pixels, then bytes)
    # takes every remaining unit within the threshold of IT. A chain of look-alikes (A near B near C…) never joins
    # pictures that are far from the keeper, so every distance listed is at most the threshold.
    by_sha: dict[str, list[int]] = {}
    for i, r in enumerate(ok):
        by_sha.setdefault(r["sha256"], []).append(i)
    units = list(by_sha.values())
    rep = [u[0] for u in units]
    near: dict[int, dict[int, int]] = {}
    if not args.exact_only and len(units) > 1:
        kind = "phash" if args.hash == "both" else args.hash
        hashes = np.array([int(ok[i][kind], 16) for i in rep], dtype=np.uint64)
        for i, j, dist in pairwise_close(hashes, args.threshold):
            dd = _dist(ok[rep[i]], ok[rep[j]], args.hash) if args.hash == "both" else dist
            if dd <= args.threshold:
                near.setdefault(i, {})[j] = dd
                near.setdefault(j, {})[i] = dd
    order = sorted(range(len(units)), key=lambda k: (-(ok[rep[k]]["width"] * ok[rep[k]]["height"]), -ok[rep[k]]["bytes"], ok[rep[k]]["file"]))
    taken: set[int] = set()
    out_groups = []
    for k in order:
        if k in taken:
            continue
        taken.add(k)
        mates = sorted((d, j) for j, d in near.get(k, {}).items() if j not in taken)
        for _d, j in mates:
            taken.add(j)
        members = [(i, 0) for i in units[k]] + [(i, d) for d, j in mates for i in units[j]]
        if len(members) < 2:
            continue
        keep = ok[members[0][0]]
        items = []
        for pos, (i, d) in enumerate(members):
            r = ok[i]
            same = r["sha256"] == keep["sha256"]
            items.append({"file": r["file"], "width": r["width"], "height": r["height"], "bytes": r["bytes"], "role": "keep" if pos == 0 else "same bytes" if same else "near",
                          "same_bytes_as_keep": same, "distance": 0 if same else d})
        exact = all(it["same_bytes_as_keep"] for it in items)
        out_groups.append({"kind": "exact" if exact else "near", "keep": keep["file"], "items": items})
    out_groups.sort(key=lambda g: (g["kind"] != "exact", -len(g["items"])))
    res: dict[str, Any] = {"files": len(recs), "groups": out_groups, "duplicates": sum(len(g["items"]) - 1 for g in out_groups),
                           "reclaimable_bytes": sum(it["bytes"] for g in out_groups for it in g["items"][1:])}
    if errors:
        res["errors"] = [{"file": e["file"], "error": e["error"]} for e in errors]
    from _paging import check_paging, next_command, page_text

    check_paging(args)
    shown = out_groups[args.offset : args.offset + args.limit if args.limit else None]
    if args.sheet and shown:
        res["sheets"] = _group_sheets(shown[:12], args.offset)
    if args.format == "json":
        if args.offset or args.limit:
            res["groups"] = shown
            res["offset"] = args.offset
            end = args.offset + len(shown)
            if end < len(out_groups):
                res["next"] = next_command(end)
        emit(res)
    else:
        if out_groups:
            head = (f"{res['files']} images: {len(out_groups)} group(s) of duplicates, {res['duplicates']} extra copies, {human_size(res['reclaimable_bytes'])} reclaimable."
                    + f" Every copy is within pHash distance {args.threshold} of its group's keeper (0 = same picture; look at the groups before deleting anything).")
        else:
            head = f"{res['files']} images: no duplicates (no two images within pHash distance {args.threshold}; --threshold 12 is looser)."
        blocks = []
        for gi, g in enumerate(shown, args.offset + 1):
            rows = [[_short(shown_name.get(it["file"], it["file"])), f"{it['width']}×{it['height']}", human_size(it["bytes"]), "keep (best copy)" if it["role"] == "keep" else "same bytes" if it["same_bytes_as_keep"] else f"distance {it['distance']}"] for it in g["items"]]
            blocks.append(f"\n### Group {gi} ({g['kind']}) — keep {_short(shown_name.get(g['keep'], g['keep']))}\n" + md_table(["file", "size", "bytes", "vs keep"], rows))
        tail = "\n".join(f"- could not read {e['file']}: {e['error']}" for e in res.get("errors", []))
        print(page_text(head, blocks, args.offset, len(out_groups), args.max_chars, "groups", tail=tail))
        if res.get("sheets"):
            from _render import announce

            announce(res["sheets"], "One contact sheet per group (first image = the one to keep).")
    return 0


def _short(path: str) -> str:
    """A path short enough for a table: the last three parts when it is long."""
    parts = Path(path).parts
    return path if len(path) <= 70 or len(parts) <= 3 else str(Path("…", *parts[-3:]))


def _group_sheets(groups: list[dict[str, Any]], first: int = 0) -> list[str]:
    import tempfile

    from _img import load, to_view
    from _render import contact_sheet

    out = []
    Path("renders").mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="desk-dupes-"))
    for gi, g in enumerate(groups, first + 1):
        paths, labels = [], []
        for k, it in enumerate(g["items"][:12]):
            v, _ = to_view(load(Path(it["file"]), max_edge=640).img)
            p = tmp / f"g{gi}-{k}.png"
            v.thumbnail((360, 360))
            v.save(p)
            paths.append(p)
            labels.append(f"{'KEEP ' if k == 0 else ''}{Path(it['file']).name} {it['width']}×{it['height']}")
        target = Path("renders") / f"dupes-group-{gi}.png"
        contact_sheet(paths, target, labels=labels, cols=min(4, len(paths)), cell=360, title=f"Group {gi} ({g['kind']})")
        out.append(str(target))
        for p in paths:
            p.unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass
    return out


def cmd_similar(args: Any) -> int:
    from _common import input_file
    from _img import expand_inputs

    q = hash_job(str(input_file(args.query)))
    if "error" in q:
        raise SkillError(q["error"])
    inputs = [i for i in expand_inputs(args.inputs, recursive=args.recursive) if Path(i.path).resolve() != Path(args.query).resolve()]
    recs = [r for r in _hash_all([str(i.path) for i in inputs], args.workers) if "error" not in r]
    kind = args.hash
    for r in recs:
        r["distance"] = _dist(q, r, kind) if kind != "both" else _dist(q, r, "phash")
        r["dhash_distance"] = _dist(q, r, "dhash")
    recs.sort(key=lambda r: (r["distance"], r["dhash_distance"]))
    top = recs[: max(1, args.top)]
    if args.format == "json":
        emit({"query": q["file"], "results": top})
    else:
        rows = [[r["file"], r["distance"], r["dhash_distance"], f"{r['width']}×{r['height']}", "near-duplicate" if r["distance"] <= args.threshold else "similar" if r["distance"] <= 16 else "different"] for r in top]
        print(f"Most similar to {q['file']} (pHash distance, 0-64):\n" + md_table(["file", "pHash dist", "dHash dist", "size", "verdict"], rows))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["diff", *argv]
    args = build_parser().parse_args(argv)
    if getattr(args, "no_cache", False):
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    return {"diff": cmd_diff, "dupes": cmd_dupes, "similar": cmd_similar, "hash": cmd_hash}[args.cmd](args)


if __name__ == "__main__":
    run_main(main)
