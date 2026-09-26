#!/usr/bin/env python3
"""Edit images with a pipeline of operations, on one file or a whole batch (in parallel).

Operations (full parameters in references/edit-ops.md):
  geometry   resize (fit|cover|fill|exact, scale, max_edge), crop (box|insets|aspect|size), trim, rotate, flip,
             pad/canvas, border, round_corners, shadow
  tone       adjust (brightness, contrast, saturation, sharpness, gamma, exposure, hue, temperature), levels,
             auto_contrast, equalize, grayscale, sepia, invert, posterize, solarize, threshold, duotone, replace_color
  filters    blur, box_blur, sharpen, unsharp, median, pixelate, filter (emboss, edges…); `box`/`boxes` limit them to regions
  annotate   draw shapes (rect, ellipse, line, arrow, polygon, highlight, callout) with labels; text; watermark (text
             or image, tiled); composite (overlay another image with opacity and blend modes); redact
  alpha      chroma_key (remove a background colour, connected to the edges), transparent, opacity, flatten
  output     set_dpi, strip_metadata, mode, quantize

Give operations as JSON (--ops, inline, a .json file or - for stdin) and/or one at a time (--op 'name key=value …').
In --op, quote values with spaces (text="Two words"; \\n is a line break inside double quotes) and give lists as JSON
(boxes=["0,0,300,40","0,900,300,40"], or with single quotes inside). Unknown operations and misspelled parameters are
errors that list what the operation takes.
Lengths are pixels or percentages ('25%') of the image at that step. Coordinates are in the upright image as
img_view shows it (EXIF orientation is applied first); `img_view.py --grid` helps you pick them.
Text is 5% of the image height unless size= says otherwise; text wider than the room is shrunk (to 40% at most),
then wrapped, and the output says so (fit=false keeps the size, max_width=80% wraps at a width).

Examples:
  python3 scripts/img_edit.py photo.heic --out photo-web.jpg --op 'resize max_edge=2000' --op 'adjust contrast=1.1 saturation=1.05'
  python3 scripts/img_edit.py shot.png --out shot-annotated.png --preview --ops '[
      {"op": "rect", "box": "120,80,400,60", "label": "Save button"},
      {"op": "arrow", "from": "70%,70%", "to": "52%,40%"},
      {"op": "redact", "box": "10,10,300,40"}]'
  python3 scripts/img_edit.py product.jpg --out product.png --op 'chroma_key color=auto tolerance=12' --op trim
  python3 scripts/img_edit.py scan.png --out scan-blurred.png --op 'blur radius=20 boxes=["0,0,300,40","0,900,300,40"]'
  python3 scripts/img_edit.py chart.png --out chart-captioned.png --op 'text text="Figure 1: Sales by region" position=bottom bg=#000000aa'
  python3 scripts/img_edit.py photos/ --out-dir out/ --op 'resize width=1200' --op 'watermark text="© Acme" opacity=0.4'
  python3 scripts/img_edit.py avatar.jpg --out avatar.png --op 'crop aspect=1:1' --op 'resize width=512' --op 'round_corners radius=50%'

Animated GIF/WebP/APNG inputs are edited frame by frame and stay animated. Metadata is kept (orientation reset)
unless --strip or a redact/strip_metadata operation. Outputs never overwrite inputs; existing files need --force.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, add_format, emit, human_size, load_json_arg, output_dir, output_path, parser, run_main


def build_parser() -> Any:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("inputs", nargs="+", help="image files, folders or globs")
    p.add_argument("--ops", help="JSON list of operations (inline, a .json file, or - for stdin)")
    p.add_argument("--op", action="append", default=[], help="one operation: 'name key=value …' (repeatable, applied after --ops)")
    p.add_argument("--out", help="output file (one input); the extension picks the format")
    p.add_argument("--out-dir", help="output folder for batches (default ./edited), mirroring subfolders")
    p.add_argument("--to", help="output format for batches (default: keep each input's format)")
    p.add_argument("--quality", type=int, help="lossy quality 1-100")
    p.add_argument("--strip", action="store_true", help="drop EXIF (incl. GPS), XMP, IPTC and comments")
    p.add_argument("--no-orient", action="store_true", help="do not apply the EXIF orientation first")
    p.add_argument("--first-frame", action="store_true", help="edit only the first frame of an animation")
    p.add_argument("--preview", action="store_true", help="also write a vision-sized PNG of each result to renders/ and print it")
    p.add_argument("-r", "--recursive", action="store_true", help="include subfolders of folder inputs")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    p.add_argument("--workers", type=int, help="parallel workers")
    p.add_argument("--list-ops", action="store_true", help="list the operation names and exit")
    p.add_argument("--max-chars", type=int, default=60000, help="text budget for the report of a big batch (default 60000; 0 = no limit)")
    add_format(p)
    return p


def _split_op(text: str) -> list[tuple[str, bool]]:
    """Splits 'name key=value …' at spaces outside quotes and brackets. Returns (token, value_was_quoted)."""
    toks: list[tuple[str, bool]] = []
    buf: list[str] = []
    quoted = False
    depth = 0
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(text) and depth == 0:
                nxt = text[i + 1]
                buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
                i += 2
                continue
            if ch == quote:
                quote = ""
                if depth:
                    buf.append(ch)
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
            if depth:
                buf.append(ch)
            else:
                quoted = True
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch.isspace() and not depth:
            if buf or quoted:
                toks.append(("".join(buf), quoted))
            buf, quoted = [], False
        else:
            buf.append(ch)
        i += 1
    if quote:
        raise UsageError(f"--op '{text}': a quote is not closed")
    if buf or quoted:
        toks.append(("".join(buf), quoted))
    return toks


def _value(v: str, quoted: bool) -> Any:
    """A --op value: quoted values stay text; [..] and {..} are JSON (single quotes allowed); numbers and
    true/false are parsed; anything else is text."""
    if quoted and not v[:1] in "[{":
        return v
    if v[:1] in "[{":
        for cand in (v, v.replace("'", '"')):
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                continue
        raise UsageError(f"cannot read the list or object {v!r}: write it as JSON, e.g. boxes='[\"10,10,50,50\",\"80,10,50,50\"]', or use --ops")
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return json.loads(v) if v and (v[0].isdigit() or v[0] in "-.") else v
    except json.JSONDecodeError:
        return v


def parse_op(text: str) -> dict[str, Any]:
    """'resize width=800 mode=cover' or JSON → an operation dict. Values in quotes stay text; lists and objects are
    JSON (boxes=["0,0,10,10","20,20,5,5"] works, inner quotes included); numbers and true/false are parsed."""
    t = text.strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except json.JSONDecodeError as e:
            raise UsageError(f"--op JSON: {e}") from None
        if "op" not in d:
            raise UsageError("--op JSON needs an 'op' key")
        return d
    parts = _split_op(t)
    if not parts:
        raise UsageError("empty --op")
    name = parts[0][0].rstrip(":")
    out: dict[str, Any] = {"op": name}
    for tok, quoted in parts[1:]:
        if "=" not in tok:
            raise UsageError(f"--op '{text}': expected key=value, got '{tok}'")
        k, v = tok.split("=", 1)
        out[k] = _value(v, quoted)
    return out


def edit_one(job: dict[str, Any]) -> dict[str, Any]:
    """Loads, edits (frame by frame for animations) and saves one image. Top-level for worker processes."""
    from _img import SaveOpts, iter_frames, load, meta_from, out_format, save_image
    from _ops import run_ops

    path = Path(job["input"])
    rec: dict[str, Any] = {"input": str(path), "bytes_in": path.stat().st_size}
    try:
        out = Path(job["out"])
        fmt, _ext = out_format(None, out) if out.suffix else out_format(job.get("to"), None)
        ld = load(path, orient=not job["no_orient"])
        rec["size_in"] = list(ld.img.size)
        ctx: dict[str, Any] = {"alpha_output": fmt in ("PNG", "WEBP", "AVIF", "HEIF", "TIFF", "GIF", "ICO", "ICNS")}
        from _img import ANIMATED_FORMATS

        animated = ld.n_frames > 1 and not job["first_frame"] and fmt in ANIMATED_FORMATS and ld.format in ANIMATED_FORMATS and ld.kind == "raster"
        if animated:
            frames, durations = [], []
            for _i, fr, dur in iter_frames(path):
                if not job["no_orient"] and ld.oriented != 1:
                    from _img import apply_orientation

                    fr.info["exif"] = ld.info.get("exif", b"")
                    fr, _ = apply_orientation(fr)
                fctx = dict(ctx)
                frames.append(run_ops(fr, job["ops"], fctx))
                durations.append(dur)
            ctx.update({k: v for k, v in fctx.items() if k != "log"})
            ctx["log"] = fctx.get("log", [])
            result: Any = frames
        else:
            result = run_ops(ld.img, job["ops"], ctx)
            frames, durations = [result], None
        meta = meta_from(ld.info, orientation_applied=ld.oriented != 1)
        strip = job["strip"] or bool(ctx.get("force_strip"))
        dpi = ctx.get("dpi") or meta["dpi"]
        opts = SaveOpts(quality=job["quality"], strip=strip, exif=meta["exif"], xmp=meta["xmp"], icc=meta["icc"], dpi=dpi,
                        durations=durations, loop=int(ld.info.get("loop", 0) or 0))
        o = output_path(out, [path], job["force"])
        save_image(frames, o, fmt, opts)
        rec.update(output=str(o), format=fmt, size=list(frames[0].size), mode=_saved_mode(o, frames[0].mode), bytes_out=o.stat().st_size, ops=[e["op"] for e in ctx.get("log", [])])
        if animated:
            rec["frames"] = len(frames)
        if ctx.get("notes"):
            rec["notes"] = ctx["notes"]
        if ctx.get("fonts"):
            rec["fonts"] = sorted(ctx["fonts"])
        if strip:
            rec["metadata"] = "stripped"
        if job.get("preview"):
            rec["preview"] = _preview(o, job["preview_dir"])
    except SkillError as e:
        rec["error"] = str(e)
    except Exception as e:  # noqa: BLE001 — one bad file must not stop a batch
        if __import__("os").environ.get("DESK_DEBUG"):
            raise
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _saved_mode(path: Path, fallback: str) -> str:
    try:
        from _img import _open

        im = _open(path)
        try:
            return str(im.mode)
        finally:
            im.close()
    except Exception:  # noqa: BLE001 — PDF and other write-only formats
        return fallback


def _preview(out: Path, preview_dir: str) -> str:
    """A vision-sized PNG of the file as written (so it shows what the format kept)."""
    from _img import load, to_view
    from _render import fit_edge

    d = Path(preview_dir)
    d.mkdir(parents=True, exist_ok=True)
    view, _ = to_view(load(out, orient=False).img, "auto")
    view = fit_edge(view)
    target = d / f"{out.stem}-{out.suffix.lstrip('.').lower() or 'img'}-preview.png"
    view.save(target, "PNG", compress_level=3)
    return str(target)


def main() -> int:
    from _img import OUTPUT_FORMATS, RAW_EXTS, expand_inputs, out_format
    from _ops import OPS, SHAPES, validate

    args = build_parser().parse_args()
    if args.list_ops:
        print("\n".join(sorted(set(OPS) | set(SHAPES))))
        return 0
    ops: list[dict[str, Any]] = []
    if args.ops:
        data = load_json_arg(args.ops)
        if isinstance(data, dict):
            data = data.get("ops", [data])
        if not isinstance(data, list) or not all(isinstance(o, dict) for o in data):
            raise UsageError("--ops is a JSON list of objects like {\"op\": \"resize\", \"width\": 800}")
        ops.extend(data)
    ops.extend(parse_op(o) for o in args.op)
    if not ops:
        raise UsageError("give operations with --ops or --op (see --help or --list-ops)")
    for i, o in enumerate(ops, 1):
        validate(o, f"operation {i}")
    if args.quality is not None and not 1 <= args.quality <= 100:
        raise UsageError("--quality must be 1-100")
    inputs = expand_inputs(args.inputs, recursive=args.recursive)
    if args.out and len(inputs) > 1:
        raise UsageError("--out takes one input; use --out-dir for several")
    jobs = []
    base = {"ops": ops, "quality": args.quality, "strip": args.strip, "no_orient": args.no_orient, "first_frame": args.first_frame,
            "force": args.force, "preview": args.preview, "preview_dir": "renders", "to": args.to}
    if args.out:
        out = Path(args.out)
        if not out.suffix:
            fmt_ext = out_format(args.to, None)[1] if args.to else inputs[0].path.suffix
            out = out.with_suffix(fmt_ext)
        jobs.append({**base, "input": str(inputs[0].path), "out": str(out)})
    else:
        root = output_dir(args.out_dir or "edited")
        ext = out_format(args.to, None)[1] if args.to else None
        for inp in inputs:
            rel = inp.rel
            target = root / (rel.with_suffix(ext) if ext else rel)
            if target.suffix.lower().lstrip(".") not in OUTPUT_FORMATS:
                # Formats this skill reads but does not write (SVG, PSD, RAW…): PNG, or JPEG for camera RAW.
                target = target.with_suffix(".jpg" if target.suffix.lower() in RAW_EXTS else ".png")
            jobs.append({**base, "input": str(inp.path), "out": str(target)})
    from _img import batch_map

    recs = batch_map(edit_one, jobs, workers=args.workers)
    if args.format == "json":
        emit(recs[0] if len(recs) == 1 else recs, max_chars=args.max_chars or None)
    else:
        lines = []
        for r in recs:
            if "error" in r:
                name, err = Path(r["input"]).name, str(r["error"])
                lines.append(f"- error: {err}" if err.startswith(name) else f"- error: {r['input']}: {err}")
                continue
            w, h = r["size"]
            lines.append(f"- {r['output']}: {r['format']} {w}×{h} {r['mode']}, {human_size(r['bytes_out'])} ({' → '.join(r['ops'])})"
                         + (f", {r['frames']} frames" if r.get("frames") else "") + (", metadata stripped" if r.get("metadata") else ""))
            for n in r.get("notes", []):
                lines.append(f"  - {n}")
        ok = sum(1 for r in recs if "error" not in r)
        from _common import cap

        print(cap(f"{ok} of {len(recs)} edited.\n" + "\n".join(lines), args.max_chars or None, "The files are all written; --max-chars 0 lists every one."))
        previews = [r["preview"] for r in recs if r.get("preview")]
        if previews:
            from _render import announce

            announce(previews)
    return 0 if all("error" not in r for r in recs) else 1


if __name__ == "__main__":
    run_main(main)
