#!/usr/bin/env python3
"""See a video: timestamped contact sheets, frames at times / intervals / scene changes, a preview GIF, a thumbnail,
and embedded cover art. Images are sized for vision; look at them with view_image."""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, input_file, md_table, output_dir, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_frames.py sheet talk.mp4                         # 16 timestamped frames on one PNG
  python3 scripts/media_frames.py sheet talk.mp4 --start 10:00 --end 12:00 --count 12   # zoom into two minutes
  python3 scripts/media_frames.py frames talk.mp4 --at 0:05,1:30,50%     # exact frames at those times (1568 px; --max-edge 0 = full size)
  python3 scripts/media_frames.py frames talk.mp4 --every 30 --out-dir frames/
  python3 scripts/media_frames.py frames talk.mp4 --scenes --sheet       # one frame per shot, plus a sheet
  python3 scripts/media_frames.py gif talk.mp4 preview.gif --clips 6     # six 1.5 s moments as a looping GIF
  python3 scripts/media_frames.py thumb talk.mp4 poster.jpg              # the most representative frame
  python3 scripts/media_frames.py cover song.mp3 cover.jpg               # embedded album art

Times: 83.5, 1:23.5, 01:02:03.250, 1h2m, 25%, end, -10 (10 s before the end).
Sheets, frames, previews and thumbnails are cached per file content and options: asking again is instant.
"""


def main() -> int:
    p = parser("See what is in a video: timestamped contact sheets, frames at exact times, every N seconds, N evenly "
               "spaced or at scene changes, a short preview GIF, a representative thumbnail, and embedded cover art. "
               "PNGs are sized for vision (long edge 1568 px by default).", EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{sheet,frames,gif,thumb,cover}")

    s = sub.add_parser("sheet", help="a grid of timestamped frames: the quickest way to watch a video", formatter_class=p.formatter_class,
                       description="A contact sheet: N frames spread over the video (or a time range), each labelled with its time.")
    s.add_argument("input")
    s.add_argument("--out", help="output PNG (default <name>-sheet.png; more pages add -2, -3)")
    s.add_argument("--count", type=int, help="frames (default: 9 up to 1 min, 16 up to 20 min, else 25)")
    s.add_argument("--every", help="one frame every this many seconds instead of --count")
    s.add_argument("--start", help="range start")
    s.add_argument("--end", help="range end")
    s.add_argument("--cols", type=int, help="columns (default: chosen from the frame shape)")
    s.add_argument("--per-sheet", type=int, default=30, help="frames per sheet before starting another page (default 30)")
    s.add_argument("--exact", action="store_true", help="exact frames at each time (slower); default uses the nearest keyframe")
    s.add_argument("--max-edge", type=int, default=None, help="sheet long edge in px (default 1568)")
    s.add_argument("--force", action="store_true", help="overwrite existing outputs")
    s.add_argument("--no-cache", action="store_true", help="decode again instead of reusing a cached sheet")
    add_format(s)

    f = sub.add_parser("frames", help="frames as PNGs at times, intervals, counts or scene changes", formatter_class=p.formatter_class,
                       description="Frames as separate PNGs. Choose one of --at, --every, --count or --scenes.")
    f.add_argument("input")
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("--at", help="comma-separated times: 5,1:30,50%%,end")
    g.add_argument("--every", help="one frame every this many seconds")
    g.add_argument("--count", type=int, help="this many frames spread evenly")
    g.add_argument("--scenes", action="store_true", help="the first frame of each shot (scene-change detection)")
    f.add_argument("--threshold", type=float, default=0.3, help="scene-change sensitivity 0-1, lower finds more cuts (default 0.3)")
    f.add_argument("--start", help="range start")
    f.add_argument("--end", help="range end")
    f.add_argument("--out-dir", help="folder for the PNGs (default <name>-frames/)")
    f.add_argument("--fast", action="store_true", help="nearest keyframes instead of exact frames (much faster on long videos)")
    f.add_argument("--max-edge", type=int, default=None, help="long edge in px (default 1568; 0 = full resolution)")
    f.add_argument("--max", type=int, default=200, help="refuse to write more frames than this (default 200)")
    f.add_argument("--sheet", action="store_true", help="also write a contact sheet of the frames")
    f.add_argument("--jpg", action="store_true", help="write JPEG instead of PNG (smaller)")
    f.add_argument("--force", action="store_true", help="overwrite existing frames")
    f.add_argument("--no-cache", action="store_true", help="decode again instead of reusing cached frames")
    add_format(f)

    gi = sub.add_parser("gif", help="a short looping preview GIF", formatter_class=p.formatter_class,
                        description="A preview GIF: either several short clips spread over the video, or one continuous range.")
    gi.add_argument("input")
    gi.add_argument("output", nargs="?", help="output .gif (or .webp for an animated WebP when supported)")
    gi.add_argument("--out", dest="out_opt", metavar="OUTPUT", help="same as the OUTPUT argument")
    gi.add_argument("--clips", type=int, default=6, help="number of clips spread over the video (default 6)")
    gi.add_argument("--clip-length", type=float, default=1.5, help="seconds per clip (default 1.5)")
    gi.add_argument("--start", help="one continuous range instead of clips: start")
    gi.add_argument("--end", help="end of that range")
    gi.add_argument("--duration", help="length of that range (default 5 s)")
    gi.add_argument("--width", type=int, default=480, help="width in px (default 480)")
    gi.add_argument("--fps", type=float, default=10, help="frames per second (default 10)")
    gi.add_argument("--colors", type=int, default=128, help="palette size 2-256 (default 128)")
    gi.add_argument("--force", action="store_true")
    gi.add_argument("--no-cache", action="store_true", help="encode again instead of reusing a cached preview")
    add_format(gi)

    t = sub.add_parser("thumb", help="one representative frame as an image", formatter_class=p.formatter_class,
                       description="A thumbnail: the frame at --at, or the sharpest, best-exposed of many candidates.")
    t.add_argument("input")
    t.add_argument("output", nargs="?", help="output .jpg, .png or .webp (default: <name>-thumb.jpg)")
    t.add_argument("--out", dest="out_opt", metavar="OUTPUT", help="same as the OUTPUT argument")
    t.add_argument("--at", help="time of the frame (default: pick the best candidate)")
    t.add_argument("--size", type=int, default=1280, help="long edge in px (default 1280)")
    t.add_argument("--candidates", type=int, default=24, help="frames to consider when picking (default 24)")
    t.add_argument("--force", action="store_true")
    t.add_argument("--no-cache", action="store_true", help="pick again instead of reusing a cached thumbnail")
    add_format(t)

    c = sub.add_parser("cover", help="extract embedded cover art (album art, poster)", formatter_class=p.formatter_class)
    c.add_argument("input")
    c.add_argument("output", nargs="?", help="output image; the extension is corrected to the real format if needed (default: <name>-cover)")
    c.add_argument("--out", dest="out_opt", metavar="OUTPUT", help="same as the OUTPUT argument")
    c.add_argument("--force", action="store_true")
    add_format(c)

    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], ("--start", "--end", "--at")))
    if getattr(a, "no_cache", False):
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    return {"sheet": cmd_sheet, "frames": cmd_frames, "gif": cmd_gif, "thumb": cmd_thumb, "cover": cmd_cover}[a.cmd](a)


def _load(path: str) -> dict[str, Any]:
    from _media import probe

    return probe(input_file(path))


def _range(info: dict[str, Any], start: str | None, end: str | None) -> tuple[float, float]:
    from _media import parse_time, require_duration

    d = require_duration(info)
    s = parse_time(start, d) if start else 0.0
    e = parse_time(end, d) if end else d
    e = min(e, d)
    if e <= s:
        raise UsageError("the range is empty (--end must be after --start)")
    return s, e


def _label(t: float) -> str:
    from _media import fmt_time

    s = fmt_time(t)
    return s[:-4] if s.endswith(".000") else s.rstrip("0")


def _stamp(t: float) -> str:
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}h{m:02d}m{s:02d}.{ms:03d}s" if h else f"{m:02d}m{s:02d}.{ms:03d}s"


# ── sheet ───────────────────────────────────────────────────────────────


def cmd_sheet(a: Any) -> int:
    from _media import cached_product, copy_out, display_size, fmt_dur, main_video, parse_time, release_product
    from _render import VISION_EDGE, announce
    from _video import spread_times

    t0 = time.monotonic()
    info = _load(a.input)
    v = main_video(info)
    if v is None:
        raise SkillError(f"{info['name']} has no video stream; for audio use media_audio.py waveform")
    s, e = _range(info, a.start, a.end)
    span = e - s
    if a.every:
        step = parse_time(a.every, what="interval")
        if step <= 0:
            raise UsageError("--every must be positive")
        times = [s + step * i for i in range(int(span / step) + 1) if s + step * i < e]
    else:
        n = a.count or (9 if span <= 60 else 16 if span <= 1200 else 25)
        if n < 1:
            raise UsageError("--count must be at least 1")
        times = spread_times(info["duration"], n, s, e)
    if len(times) > 400:
        raise UsageError(f"{len(times)} frames is too many for sheets; use a larger --every or a smaller range")
    max_edge = a.max_edge or VISION_EDGE
    base = Path(a.out) if a.out else Path(f"{Path(info['name']).stem}-sheet.png")
    if base.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        base = base.with_suffix(".png")
    per = max(1, a.per_sheet)
    pages = [times[i:i + per] for i in range(0, len(times), per)]
    outs = [base if i == 0 else base.with_name(f"{base.stem}-{i + 1}{base.suffix}") for i in range(len(pages))]
    outs = [output_path(o, [info["file"]], a.force) for o in outs]
    w, h = display_size(v)

    def build(tmp: Path) -> dict[str, Any]:
        work = tmp / "work"
        work.mkdir()
        results = []
        for pi, page in enumerate(pages):
            res = _sheet_page(info, page, tmp / f"sheet-{pi + 1}.png", work, a, max_edge, (w, h), pi + 1, len(pages), s, e)
            res["file"] = f"sheet-{pi + 1}.png"
            results.append(res)
        shutil.rmtree(work, ignore_errors=True)
        return {"sheets": results}

    params = {"name": info["name"], "times": [round(t, 3) for t in times], "exact": a.exact, "max_edge": max_edge, "cols": a.cols, "per": per}
    d, meta, cached = cached_product(info["file"], "av-sheet", params, "3", build)
    results = []
    try:
        for res, out in zip(meta["sheets"], outs):
            copy_out(d / res["file"], out)
            results.append({**res, "file": str(out)})
    finally:
        release_product(d)
    data = {"file": info["file"], "duration": info["duration"], "range": [round(s, 3), round(e, 3)], "sheets": results, "cached": cached,
            "seconds": round(time.monotonic() - t0, 2)}
    if a.format == "json":
        emit(data, "json")
    else:
        missing = sum(r["missing"] for r in results)
        frames = sum(r["frames"] for r in results)
        note = (f"{info['name']}: {fmt_dur(info['duration'])}, {w}x{h}" + (f", {v['fps']:g} fps" if v.get("fps") else "")
                + f"; {frames} frames from {_label(results[0]['first'])} to {_label(results[-1]['last'])}"
                + ("" if a.exact else " (nearest keyframes; labels show their real times)") + ".")
        if missing:
            note += f" {missing} frame(s) could not be decoded."
        if any(r.get("concealed") for r in results):
            note += (" The file is damaged: some frames were decoded with ffmpeg's error concealment (smears or grey blocks are damage, "
                     "not content); media_convert.py --reencode can salvage a playable copy.")
        announce([r["file"] for r in results], note)
    return 0


def _sheet_page(info: dict[str, Any], times: list[float], out: Path, tmp: Path, a: Any, max_edge: int, size: tuple[int, int], page: int, pages: int, s: float, e: float) -> dict[str, Any]:
    from _media import fmt_dur
    from _video import draw_sheet, grab_frames, sheet_layout

    w, h = size
    cols, tw, th = sheet_layout(len(times), w, h, max_edge, a.cols)
    outs = [str(tmp / f"p{page}-{i:04d}.png") for i in range(len(times))]
    res = grab_frames(info, times, outs, exact=a.exact, max_edge=max(tw, th), compress=1)
    ok = _dedupe([r for r in res if r and "file" in r])
    if not ok:
        raise SkillError(f"no frames could be decoded from {info['name']} (not even with error concealment); media_convert.py --reencode may salvage it")
    title = f"{info['name']} · {fmt_dur(info['duration'])} · {w}x{h}"
    if (s, e) != (0.0, info["duration"]):
        title += f" · {_label(s)}–{_label(e)}"
    if pages > 1:
        title += f" · sheet {page}/{pages}"
    ow, oh = draw_sheet([r["file"] for r in ok], [_label(r["actual"]) for r in ok], cols, out, title, max_edge, cell=(tw, th))
    return {"file": str(out), "frames": len(ok), "missing": len(res) - len(ok), "first": ok[0]["actual"], "last": ok[-1]["actual"], "times": [r["actual"] for r in ok],
            "width": ow, "height": oh, "concealed": any(r.get("concealed") for r in ok)}


def _dedupe(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drops frames that landed on the same keyframe as the previous one."""
    out: list[dict[str, Any]] = []
    for r in frames:
        if out and abs(out[-1]["actual"] - r["actual"]) < 1e-3:
            continue
        out.append(r)
    return out


# ── frames ──────────────────────────────────────────────────────────────


def cmd_frames(a: Any) -> int:
    from _media import cached_product, copy_out, fmt_time, main_video, parse_time, parse_time_list, release_product, require_duration
    from _render import VISION_EDGE, announce
    from _video import spread_times

    t0 = time.monotonic()
    info = _load(a.input)
    v = main_video(info)
    if v is None:
        raise SkillError(f"{info['name']} has no video stream; for audio use media_audio.py waveform")
    d = require_duration(info)
    s, e = _range(info, a.start, a.end)
    scores: dict[float, float] = {}
    if a.at:
        times = parse_time_list(a.at, d)
        bad = [t for t in times if t > d + 0.001]
        if bad:
            raise UsageError(f"time {fmt_time(bad[0])} is past the end ({fmt_time(d)})")
        times = [min(t, max(0.0, d - 0.001)) for t in times]
    elif a.every:
        step = parse_time(a.every, what="interval")
        if step <= 0:
            raise UsageError("--every must be positive")
        times = [s + step * i for i in range(int((e - s) / step) + 1) if s + step * i < e]
    elif a.count:
        times = spread_times(d, a.count, s, e)
    else:
        if not 0 < a.threshold < 1:
            raise UsageError("--threshold must be between 0 and 1")
        found = scenes_cached(info, s, e, a.threshold, a.fast)
        times = [t for t, _ in found]
        scores = {round(t, 3): sc for t, sc in found}
    if len(times) > a.max:
        raise UsageError(f"that is {len(times)} frames; raise --max or choose fewer (use sheet for an overview)")
    outdir = output_dir(a.out_dir or f"{Path(info['name']).stem}-frames")
    ext = ".jpg" if a.jpg else ".png"
    names = [f"frame-{i + 1:04d}-{_stamp(t)}{ext}" for i, t in enumerate(times)]
    for n in names:
        output_path(outdir / n, [info["file"]], a.force)
    max_edge = VISION_EDGE if a.max_edge is None else (a.max_edge or None)
    exact = not a.fast

    def build(tmp: Path) -> dict[str, Any]:
        return _grab_into(info, v, times, names, tmp, exact, max_edge, a)

    params = {"name": info["name"], "times": [round(t, 3) for t in times], "exact": exact, "max_edge": max_edge, "jpg": a.jpg, "sheet": a.sheet}
    dd, meta, cached = cached_product(info["file"], "av-frames", params, "2", build)
    frames = []
    try:
        for i, r in enumerate(meta["frames"]):
            if r is None:
                continue
            dest = outdir / r["file"]
            if dest.exists() and not a.force:
                dest = outdir / names[i]  # the name that was checked to be free
            copy_out(dd / r["file"], dest)
            frames.append({**r, "file": str(dest)})
        sheet = None
        if meta.get("sheet"):
            sheet = output_path(outdir / "sheet.png", [info["file"]], True)
            copy_out(dd / meta["sheet"], sheet)
    finally:
        release_product(dd)
    for r in frames:
        if round(r["time"], 3) in scores:
            r["scene_score"] = round(scores[round(r["time"], 3)], 3)
    data = {"file": info["file"], "frames": frames, "missing": meta["missing"], "sheet": str(sheet) if sheet else None, "cached": cached,
            "seconds": round(time.monotonic() - t0, 2)}
    if a.format == "json":
        emit(data, "json")
        return 0
    if a.scenes:
        print(f"{len(frames)} scene(s) found (threshold {a.threshold:g}):")
    rows = [[i + 1, _label(r["actual"]), f"{r['width']}x{r['height']}", r.get("scene_score", ""), r["file"]] for i, r in enumerate(frames)]
    print(md_table(["#", "time", "size", "scene score", "file"], rows) if a.scenes else md_table(["#", "time", "size", "file"], [[x[0], x[1], x[2], x[4]] for x in rows]))
    if data["missing"]:
        print(f"could not decode frames at: {', '.join(_label(t) for t in data['missing'])}")
    note = f"{len(frames)} frame(s) in {outdir}" + ("" if exact else " (nearest keyframes)")
    if any(r.get("concealed") for r in frames):
        note += ("; the file is damaged, so some frames were decoded with ffmpeg's error concealment (smears or grey blocks are damage); "
                 "media_convert.py --reencode can salvage a playable copy")
    announce([sheet] if sheet else [r["file"] for r in frames[:8]], note)
    return 0


def _grab_into(info: dict[str, Any], v: dict[str, Any], times: list[float], names: list[str], tmp: Path, exact: bool, max_edge: int | None, a: Any) -> dict[str, Any]:
    """Grabs the frames into `tmp` under their final names (renamed to the real time when it differs); the facts as JSON."""
    from _media import display_size
    from _render import VISION_EDGE
    from _video import draw_sheet, grab_frames, sheet_layout

    tmp_names = [str(tmp / Path(n).with_suffix(".png").name) for n in names]
    res = grab_frames(info, times, tmp_names, exact=exact, max_edge=max_edge)
    frames: list[dict[str, Any] | None] = []
    for i, r in enumerate(res):
        if not r or "file" not in r:
            frames.append(None)
            continue
        src = Path(r["file"])
        name = names[i]
        if abs(r["actual"] - r["time"]) >= 0.0005:
            name = f"frame-{i + 1:04d}-{_stamp(r['actual'])}{Path(name).suffix}"
        dst = tmp / name
        if a.jpg:
            from PIL import Image

            with Image.open(src) as im:
                im.convert("RGB").save(dst, quality=90)
            src.unlink()
        elif dst != src:
            src.replace(dst)
        frames.append({**r, "file": name})
    ok = [f for f in frames if f]
    sheet = None
    if a.sheet and ok:
        w, h = display_size(v)
        cols, tw, th = sheet_layout(len(ok), w, h, VISION_EDGE)
        sheet = "sheet.png"
        draw_sheet([str(tmp / f["file"]) for f in ok], [_label(f["actual"]) for f in ok], cols, tmp / sheet, f"{info['name']} · {len(ok)} frames", VISION_EDGE, cell=(tw, th))
    return {"frames": frames, "missing": [t for t, f in zip(times, frames) if f is None], "sheet": sheet}


def scenes_cached(info: dict[str, Any], start: float, end: float, threshold: float, fast: bool) -> list[tuple[float, float]]:
    """detect_scenes, computed once per file content and options."""
    try:
        import _cache

        found = _cache.cached_json(info["file"], "av-scenes", {"start": round(start, 3), "end": round(end, 3), "threshold": threshold, "fast": fast}, "1",
                                   lambda: [[t, sc] for t, sc in detect_scenes(info, start, end, threshold, fast)])
    except OSError:
        found = detect_scenes(info, start, end, threshold, fast)
    return [(float(t), float(sc)) for t, sc in found]


def detect_scenes(info: dict[str, Any], start: float, end: float, threshold: float, fast: bool) -> list[tuple[float, float]]:
    """Scene cuts as (time, score); the range start always counts as the first scene."""
    from _media import main_video, run_ffmpeg, secs_arg, video_stream_pos

    if not 0 < threshold < 1:
        raise UsageError("--threshold must be between 0 and 1")
    v = main_video(info)
    assert v is not None
    args: list[Any] = []
    if fast:
        args += ["-skip_frame", "nokey"]
    if start > 0:
        args += ["-ss", secs_arg(start)]
    args += ["-i", info["file"], "-t", secs_arg(end - start), "-map", f"0:v:{video_stream_pos(info)}", "-an", "-sn", "-dn",
             "-vf", f"scale=192:-2:flags=fast_bilinear,select='gt(scene\\,{threshold})',metadata=print:key=lavfi.scene_score", "-f", "null", "-"]
    text = run_ffmpeg(args, duration=end - start, label="finding scene changes", loglevel="info")
    cuts: list[tuple[float, float]] = [(start, 1.0)]
    pending_t: float | None = None
    for line in text.splitlines():
        m = re.search(r"pts_time:\s*(-?[\d.]+)", line)
        if m:
            pending_t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and pending_t is not None:
            t = start + pending_t
            if t - cuts[-1][0] > 0.2:
                cuts.append((t, float(m.group(1))))
            pending_t = None
    return cuts


# ── gif ─────────────────────────────────────────────────────────────────


def cmd_gif(a: Any) -> int:
    from _media import cached_product, copy_out, fmt_size, has_encoder, parse_time, release_product, require_duration, run_ffmpeg, secs_arg, video_stream_pos

    info = _load(a.input)
    d = require_duration(info)
    target = a.output or a.out_opt
    if not target:
        raise UsageError("give the output file (e.g. preview.gif)")
    out = output_path(target, [info["file"]], a.force)
    ext = out.suffix.lower()
    if ext not in (".gif", ".webp"):
        raise UsageError("the output must be .gif or .webp")
    if ext == ".webp" and not has_encoder("libwebp_anim"):
        raise SkillError("this ffmpeg build cannot write animated WebP; use .gif")
    if not 2 <= a.colors <= 256:
        raise UsageError("--colors must be 2-256")
    vpos = video_stream_pos(info)
    inputs: list[Any] = []
    if a.start is not None or a.duration is not None or a.end is not None:
        s = parse_time(a.start, d) if a.start else 0.0
        if a.end is not None:
            length = parse_time(a.end, d, "end") - s
        else:
            length = parse_time(a.duration, d, "duration") if a.duration else 5.0
        if length <= 0 or s >= d:
            raise UsageError("the GIF range is empty (check --start, --end and --duration)")
        segs = [(s, min(length, d - s))]
    else:
        n = max(1, a.clips)
        L = min(a.clip_length, d / n)
        segs = [(max(0.0, (i + 0.5) * d / n - L / 2), L) for i in range(n)]
    for s, L in segs:
        inputs += ["-ss", secs_arg(s), "-t", secs_arg(L), "-i", info["file"]]
    chain = "".join(f"[{i}:v:{vpos}]" for i in range(len(segs)))
    pre = f"{chain}concat=n={len(segs)}:v=1:a=0," if len(segs) > 1 else f"[0:v:{vpos}]"
    scale = f"fps={a.fps:g},scale={a.width}:-2:flags=lanczos"
    if ext == ".gif":
        graph = f"{pre}{scale},split[a][b];[a]palettegen=max_colors={a.colors}:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
        codec = []
    else:
        graph = f"{pre}{scale}"
        codec = ["-c:v", "libwebp_anim", "-q:v", "70", "-loop", "0"]

    def build(tmp: Path) -> dict[str, Any]:
        from PIL import Image

        f = tmp / f"preview{ext}"
        run_ffmpeg([*inputs, "-filter_complex", graph, "-an", *codec, "-loop", "0", str(f)], duration=sum(L for _, L in segs), label="making the preview")
        with Image.open(f) as im:
            return {"width": im.size[0], "height": im.size[1], "frames": getattr(im, "n_frames", 1)}

    params = {"segs": [[round(x, 3), round(y, 3)] for x, y in segs], "width": a.width, "fps": a.fps, "colors": a.colors, "ext": ext}
    dd, meta, cached = cached_product(info["file"], "av-gif", params, "1", build)
    try:
        copy_out(dd / f"preview{ext}", out)
    finally:
        release_product(dd)
    data = {"file": str(out), "size": out.stat().st_size, **meta, "segments": params["segs"], "cached": cached}
    if a.format == "json":
        emit(data, "json")
    else:
        print(f"{out}: {data['width']}x{data['height']}, {data['frames']} frames, {fmt_size(data['size'])}, from {len(segs)} segment(s)")
        print("view_image shows only the first frame of an animation; to check the content, use media_frames.py sheet.")
    return 0


# ── thumbnail ───────────────────────────────────────────────────────────


def cmd_thumb(a: Any) -> int:
    from _media import cached_product, copy_out, parse_time, release_product, require_duration
    from _render import announce

    info = _load(a.input)
    d = require_duration(info)
    out = output_path(a.output or a.out_opt or f"{Path(info['file']).stem}-thumb.jpg", [info["file"]], a.force)
    ext = out.suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise UsageError("the output must be .jpg, .png or .webp")
    at = min(parse_time(a.at, d), max(0.0, d - 0.001)) if a.at else None

    def build(tmp: Path) -> dict[str, Any]:
        return _pick_thumb(info, d, at, a, tmp, tmp / f"thumb{ext}")

    params = {"at": None if at is None else round(at, 3), "size": a.size, "candidates": a.candidates, "ext": ext}
    dd, meta, cached = cached_product(info["file"], "av-thumb", params, "1", build)
    try:
        copy_out(dd / f"thumb{ext}", out)
    finally:
        release_product(dd)
    data = {"file": str(out), **meta, "cached": cached}
    if a.format == "json":
        emit(data, "json")
    else:
        announce([out], f"frame at {_label(data['time'])}, {data['width']}x{data['height']}" + ("" if a.at else " (picked for sharpness, exposure and colour)"))
    return 0


def _pick_thumb(info: dict[str, Any], d: float, at: float | None, a: Any, tmp: Path, out: Path) -> dict[str, Any]:
    from PIL import Image

    from _video import grab_frames, spread_times

    if at is not None:
        res = grab_frames(info, [at], [str(tmp / "f.png")], exact=True, max_edge=a.size)
        best = res[0]
        if not best or "file" not in best:
            raise SkillError(f"no frame at {a.at}")
        score = None
    else:
        times = spread_times(d, max(3, a.candidates), d * 0.05, d * 0.95)
        res = grab_frames(info, times, [str(tmp / f"c{i}.png") for i in range(len(times))], exact=False, max_edge=a.size)
        cands = [r for r in res if r and "file" in r]
        if not cands:
            raise SkillError(f"no frames could be decoded from {info['name']}")
        scored = [(frame_score(r["file"]), r) for r in cands]
        score, best = max(scored, key=lambda x: x[0])
    with Image.open(best["file"]) as im:
        im = im.convert("RGB")
        if out.suffix.lower() in (".jpg", ".jpeg"):
            im.save(out, quality=90, optimize=True)
        else:
            im.save(out)
        size = im.size
    for f in tmp.glob("*.png"):
        if f != out:
            f.unlink()
    return {"time": best["actual"], "width": size[0], "height": size[1], "score": round(score, 3) if score is not None else None}


def frame_score(path: str) -> float:
    """Higher for sharp, well-exposed, colourful frames; near-black, blown-out or flat frames score low."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((320, 320))
        rgb = np.asarray(im, dtype=np.float32)
    gray = rgb.mean(axis=2)
    lap = np.abs(4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:])
    sharp = float(np.log1p(lap.var()))
    mean = float(gray.mean())
    exposure = 1.0 - abs(mean - 118) / 118
    contrast = float(gray.std()) / 64
    rg = rgb[..., 0] - rgb[..., 1]
    yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
    colour = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 60
    if mean < 18 or mean > 240:
        return -10 + mean / 100
    return sharp * 0.5 + max(exposure, 0) * 2 + min(contrast, 1.5) + min(colour, 1.5)


# ── cover art ───────────────────────────────────────────────────────────


def cmd_cover(a: Any) -> int:
    from _media import fmt_size, open_container

    src = input_file(a.input)
    c = open_container(src)
    try:
        pics = [s for s in c.streams if s.type == "video" and (int(s.disposition) & 0x400)]
        if not pics:
            raise SkillError(f"{src.name} has no embedded cover art")
        s = pics[0]
        codec = s.codec_context.name
        data = b""
        for pkt in c.demux(s):
            if pkt.size:
                data = bytes(pkt)
                break
    finally:
        c.close()
    if not data:
        raise SkillError("the cover art stream is empty")
    real = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if data[:3] == b"\xff\xd8\xff" else (".bmp" if data[:2] == b"BM" else f".{codec}")
    out = Path(a.output or a.out_opt or f"{Path(src).stem}-cover{real}")
    if out.suffix.lower() not in (real, ".jpeg" if real == ".jpg" else real):
        out = out.with_suffix(real)
    out = output_path(out, [src], a.force)
    out.write_bytes(data)
    from PIL import Image

    try:
        with Image.open(out) as im:
            size = im.size
    except Exception:  # noqa: BLE001
        size = (0, 0)
    info = {"file": str(out), "bytes": len(data), "width": size[0], "height": size[1], "format": real.lstrip(".")}
    if a.format == "json":
        emit(info, "json")
    else:
        print(f"{out}: {size[0]}x{size[1]} {real.lstrip('.').upper()}, {fmt_size(len(data))}")
        print("Look at it with view_image.")
    return 0


if __name__ == "__main__":
    run_main(main)
