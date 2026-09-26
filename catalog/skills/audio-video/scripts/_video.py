"""Frame grabbing for the audio-video skill: fast PyAV seeking, exact frames, HDR tone mapping, rotation and
pixel aspect handled, parallel across processes. Also layout helpers for timestamped contact sheets."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Sequence

from _common import SkillError, pool_map, workers_for

# ── colour ──────────────────────────────────────────────────────────────

_M2020_TO_709 = (
    (1.6605, -0.5876, -0.0728),
    (-0.1246, 1.1329, -0.0083),
    (-0.0182, -0.1006, 1.1187),
)


def _hable(x: Any) -> Any:
    a, b, c, d, e, f = 0.15, 0.50, 0.10, 0.20, 0.02, 0.30
    return ((x * (a * x + c * b) + d * e) / (x * (a * x + b) + d * f)) - e / f


def tonemap_to_sdr(rgb16: Any, transfer: str) -> Any:
    """BT.2020 PQ or HLG RGB (uint16, full range) → BT.709 SDR RGB uint8, with the Hable curve (like ffmpeg's tonemap)."""
    import numpy as np

    x = rgb16.astype(np.float32) / 65535.0
    if transfer == "pq":
        m1, m2 = 2610 / 16384, 2523 / 4096 * 128
        c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
        p = np.power(np.clip(x, 0, 1), 1 / m2)
        lin = np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1) * 10000.0  # nits
    else:  # HLG: inverse OETF, then the reference OOTF for a 1000-nit display
        a, b, c = 0.17883277, 0.28466892, 0.55991073
        e = np.where(x <= 0.5, (x * x) / 3.0, (np.exp((x - c) / a) + b) / 12.0)
        y = 0.2627 * e[..., 0] + 0.6780 * e[..., 1] + 0.0593 * e[..., 2]
        lin = 1000.0 * e * np.power(np.maximum(y, 1e-6), 0.2)[..., None]
    rel = lin / 100.0  # 1.0 = SDR reference white
    m = np.array(_M2020_TO_709, dtype=np.float32)
    rel = np.maximum(rel @ m.T, 0.0)
    peak = 10.0
    out = _hable(rel) / _hable(np.float32(peak))
    out = np.clip(out, 0, 1)
    out = np.where(out < 0.018, 4.5 * out, 1.099 * np.power(out, 0.45) - 0.099)
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def hdr_kind(v: dict[str, Any] | None) -> str | None:
    if not v:
        return None
    t = (v.get("color") or {}).get("transfer") or ""
    if t.startswith("smpte2084"):
        return "pq"
    if t.startswith("arib"):
        return "hlg"
    return None


def target_size(w: int, h: int, sar: float, rotation: int, max_edge: int | None) -> tuple[int, int]:
    """Decoded frame size to ask swscale for: SAR applied, long edge (after rotation) within max_edge, never upscaled."""
    dw = max(2, int(round(w * sar)))
    dh = h
    if max_edge and max(dw, dh) > max_edge:
        r = max_edge / max(dw, dh)
        dw, dh = max(2, int(round(dw * r))), max(2, int(round(dh * r)))
    return dw, dh


def frame_to_image(frame: Any, tw: int, th: int, rotation: int, hdr: str | None) -> Any:
    """A decoded PyAV frame → a PIL RGB image at (tw, th), tone-mapped if HDR, rotated for display."""
    from PIL import Image

    kw: dict[str, Any] = {"interpolation": "AREA" if (tw < frame.width or th < frame.height) else "BICUBIC"}
    try:
        if frame.color_range == 2:
            kw["src_color_range"] = "JPEG"
            kw["dst_color_range"] = "JPEG"
    except AttributeError:
        pass
    if hdr:
        f16 = frame.reformat(width=tw, height=th, format="rgb48le", src_colorspace="BT2020", **kw)
        arr = f16.to_ndarray()
        img = Image.fromarray(tonemap_to_sdr(arr, hdr), "RGB")
    else:
        try:
            cs = frame.colorspace
        except AttributeError:
            cs = None
        if not cs or cs == 2:  # unspecified: HD sizes are BT.709 by convention
            kw["src_colorspace"] = "ITU709" if frame.height >= 720 else "ITU601"
        img = frame.reformat(width=tw, height=th, format="rgb24", **kw).to_image()
    if rotation:
        img = img.rotate(rotation, expand=True)
    return img


# ── grabbing ────────────────────────────────────────────────────────────


def _open_video(path: str, stream_index: int, threads: int) -> tuple[Any, Any]:
    import av

    c = av.open(path, metadata_errors="ignore")
    vs = c.streams[stream_index]
    vs.thread_type = "AUTO"
    vs.codec_context.thread_count = max(1, threads)
    return c, vs


def decode_tolerant(c: Any, vs: Any, max_errors: int = 200) -> Any:
    """Decoded frames of `vs`, skipping packets the decoder rejects (damaged data) instead of stopping at the first
    one, as ffmpeg's error concealment does. The frames after a damaged keyframe can still be grey or smeared."""
    import av

    errors = 0
    for pkt in c.demux(vs):
        try:
            frames = pkt.decode()
        except av.FFmpegError:
            errors += 1
            if errors > max_errors:
                return
            continue
        for f in frames:
            yield f


def _frame_time(frame: Any, vs: Any, start: float, index: int, fps: float | None) -> float:
    if frame.pts is not None and frame.time_base is not None:
        return float(frame.pts * frame.time_base) - start
    if frame.time is not None:
        return float(frame.time) - start
    return index / fps if fps else 0.0


def grab_job(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Worker: opens the file once and grabs frames at sorted times. Top-level so it runs in a process pool."""
    import av

    path, idx = job["path"], job["stream"]
    times: list[float] = job["times"]
    c, vs = _open_video(path, idx, job["threads"])
    start = float(vs.start_time * vs.time_base) if vs.start_time is not None else 0.0
    results: list[dict[str, Any]] = []
    used: set[float] = set()
    try:
        seekable = True
        for i, t in enumerate(times):
            out = job["outs"][i]
            frame = None
            actual = None
            if seekable:
                try:
                    frame, actual = _seek_grab(c, vs, t, start, job, used)
                except av.error.PermissionError:
                    seekable = False
                except av.FFmpegError:
                    frame = None
            if not seekable:
                c.close()
                c, vs = _open_video(path, idx, job["threads"])
                frame, actual = _linear_grab(c, vs, t, start, job.get("fps"))
            if frame is None:
                results.append({"time": t, "error": "no frame decoded at this time"})
                continue
            if actual is not None:
                used.add(round(actual, 3))
            img = frame_to_image(frame, job["tw"], job["th"], job["rotation"], job["hdr"])
            img.save(out, compress_level=job.get("compress", 6))
            results.append({"time": t, "actual": round(actual if actual is not None else t, 3), "file": out, "width": img.size[0], "height": img.size[1], "keyframe": bool(frame.key_frame)})
    finally:
        c.close()
    return results


def _key_at(c: Any, vs: Any, ts: int, backward: bool, start: float, fps: float | None) -> tuple[Any, float | None]:
    """The keyframe at or before (backward) / at or after `ts`, decoding keyframes only."""
    cc = vs.codec_context
    cc.skip_frame = "NONKEY"
    try:
        c.seek(ts, stream=vs, backward=backward, any_frame=False)
        cc.flush_buffers()
        for n, frame in enumerate(decode_tolerant(c, vs, 20)):
            return frame, _frame_time(frame, vs, start, n, fps)
        return None, None
    finally:
        cc.skip_frame = "DEFAULT"


def _seek_grab(c: Any, vs: Any, t: float, start: float, job: dict[str, Any], used: set[float]) -> tuple[Any, float | None]:
    fps = job.get("fps")
    target = int((t + start) / vs.time_base)
    if not job["exact"]:
        tol = job["tol"]
        k1, t1 = _key_at(c, vs, target, True, start, fps)
        if k1 is not None and t1 is not None and abs(t1 - t) <= tol / 2 and round(t1, 3) not in used:
            return k1, t1
        # The next keyframe within reach: the keyframe at or before t + tol (more reliable than a forward seek).
        k2, t2 = _key_at(c, vs, int((t + tol + start) / vs.time_base), True, start, fps)
        if t2 is not None and t1 is not None and t2 <= t1 + 1e-6:
            k2, t2 = None, None
        cands = [(abs(tt - t), f, tt) for f, tt in ((k1, t1), (k2, t2)) if f is not None and tt is not None and round(tt, 3) not in used]
        near = [x for x in cands if x[0] <= tol]
        if near:
            _, f, tt = min(near, key=lambda x: x[0])
            return f, tt
        # Exact decoding from the previous keyframe, when it is affordable; else the closest keyframe.
        dist = t - (t1 if t1 is not None and t1 <= t else 0.0)
        cost = dist * (fps or 30.0) * job["pixels"] / (1920 * 1080)
        if cost > job.get("budget", 400):
            if cands:
                _, f, tt = min(cands, key=lambda x: x[0])
                return f, tt
            if k1 is not None:
                return k1, t1
    # exact: decode forward from the keyframe before t to the first frame at or after t
    cc = vs.codec_context
    c.seek(target, stream=vs, backward=True, any_frame=False)
    cc.flush_buffers()
    half = 0.5 / fps if fps else 0.02
    last = None
    last_t = None
    for n, frame in enumerate(decode_tolerant(c, vs)):
        ft = _frame_time(frame, vs, start, n, fps)
        if ft >= t - half:
            if last is not None and last_t is not None and abs(last_t - t) < abs(ft - t):
                return last, last_t
            return frame, ft
        last, last_t = frame, ft
    return last, last_t


def _linear_grab(c: Any, vs: Any, t: float, start: float, fps: float | None) -> tuple[Any, float | None]:
    """For streams that cannot seek: decode from the start."""
    last = None
    last_t = None
    for n, frame in enumerate(decode_tolerant(c, vs)):
        ft = _frame_time(frame, vs, start, n, fps)
        if ft >= t:
            return frame, ft
        last, last_t = frame, ft
    return last, last_t


def grab_frames(info: dict[str, Any], times: Sequence[float], outs: Sequence[str], exact: bool = False, max_edge: int | None = 1568, workers: int | None = None, tol: float | None = None, compress: int = 6) -> list[dict[str, Any]]:
    """Frames at `times` (seconds from the start) saved as PNGs at `outs`; parallel over processes for many frames."""
    from _media import main_video

    v = main_video(info)
    if v is None:
        raise SkillError(f"{info['name']} has no video stream (for audio, use media_audio.py waveform)")
    if not times:
        return []
    sar = 1.0
    if v.get("sar"):
        n, d = v["sar"].split(":")
        sar = float(n) / float(d) if float(d) else 1.0
    rot = int(v.get("rotation") or 0)
    tw, th = target_size(v["width"], v["height"], sar, rot, max_edge)
    order = sorted(range(len(times)), key=lambda i: times[i])
    st = [float(times[i]) for i in order]
    so = [str(outs[i]) for i in order]
    spacing = (st[-1] - st[0]) / max(1, len(st) - 1) if len(st) > 1 else (info.get("duration") or 10.0)
    # Each time gets its own keyframe slot: a keyframe counts as "near" within just under half the spacing.
    tol = tol if tol is not None else 0.45 * spacing
    cpu = os.cpu_count() or 2
    n_workers = workers if workers is not None else workers_for(max(1, len(st) // 2))  # honours DESK_MAX_WORKERS
    per = math.ceil(len(st) / n_workers)
    jobs = []
    for k in range(0, len(st), per):
        jobs.append({
            "path": info["file"], "stream": v["index"], "times": st[k:k + per], "outs": so[k:k + per], "exact": exact, "tol": tol,
            "threads": max(1, cpu // max(1, n_workers)), "fps": v.get("fps"), "tw": tw, "th": th, "rotation": rot,
            "hdr": hdr_kind(v), "compress": compress, "pixels": int(v["width"]) * int(v["height"]),
        })
    chunks = pool_map(grab_job, jobs, workers=len(jobs)) if len(jobs) > 1 else [grab_job(jobs[0])]
    flat = [r for ch in chunks for r in ch]
    back: list[Any] = [None] * len(flat)
    for pos, i in enumerate(order):
        back[i] = flat[pos]
    failed = [i for i, r in enumerate(back) if not r or "file" not in r]
    if failed:
        # Damaged files: ffmpeg's own decoder conceals errors (and resyncs at later keyframes) where PyAV gave up.
        vpos = [s for s in info["streams"] if s["type"] == "video"].index(v)
        sw, sh = (th, tw) if abs(rot) == 90 else (tw, th)
        for i in failed:
            r = _ffmpeg_grab(info["file"], vpos, float(times[i]), str(outs[i]), sw, sh, bool(hdr_kind(v)))
            if r:
                back[i] = r
    return back


def _ffmpeg_grab(path: str, vpos: int, t: float, out: str, w: int, h: int, hdr: bool) -> dict[str, Any] | None:
    """One frame through the ffmpeg command line (rotation applied by ffmpeg, errors concealed)."""
    from _media import run_ffmpeg, secs_arg, tonemap_filter

    vf = [f"scale={w}:{h}:flags=area"]
    tm = tonemap_filter() if hdr else None
    if tm:
        vf.insert(0, tm)
    try:
        run_ffmpeg(["-ss", secs_arg(t), "-i", path, "-map", f"0:v:{vpos}", "-frames:v", "1", "-vf", ",".join(vf), "-update", "1", out],
                   label="decoding a frame", progress=False, timeout=120)
    except Exception:  # noqa: BLE001 — nothing decodable here either
        return None
    p = Path(out)
    if not p.exists() or p.stat().st_size == 0:
        return None
    from PIL import Image

    with Image.open(p) as im:
        size = im.size
    return {"time": t, "actual": round(t, 3), "file": out, "width": size[0], "height": size[1], "keyframe": False, "concealed": True}


def spread_times(duration: float, count: int, start: float = 0.0, end: float | None = None) -> list[float]:
    """`count` times at the centres of equal slices of [start, end]."""
    end = duration if end is None else min(end, duration)
    span = max(0.0, end - start)
    if count <= 0 or span <= 0:
        return [start]
    return [start + (i + 0.5) * span / count for i in range(count)]


def sheet_layout(n: int, frame_w: int, frame_h: int, max_edge: int, cols: int | None = None) -> tuple[int, int, int]:
    """Columns and thumbnail size (w, h) for a contact sheet whose long edge stays within max_edge."""
    aspect = frame_w / max(1, frame_h)
    if cols is None:
        best = None
        for c in range(1, min(n, 12) + 1):
            rows = math.ceil(n / c)
            sheet_aspect = (c * aspect) / (rows * (1 + 0.08 * (1 / aspect)))
            score = abs(math.log(sheet_aspect / 1.4)) + 0.05 * (c * rows - n)
            if best is None or score < best[0]:
                best = (score, c)
        cols = best[1] if best else 1
    cols = max(1, min(cols, n))
    rows = math.ceil(n / cols)
    w = (max_edge - SHEET_PAD * (cols + 1)) / cols
    h = w / aspect
    max_h = (max_edge - SHEET_TITLE - SHEET_PAD * (rows + 1) - rows * SHEET_LABEL) / rows
    if h > max_h:
        h = max_h
        w = h * aspect
    return cols, max(16, int(w)), max(16, int(h))


SHEET_PAD, SHEET_LABEL, SHEET_TITLE = 10, 22, 32


def draw_sheet(files: Sequence[str], labels: Sequence[str], cols: int, out: str | os.PathLike[str], title: str, max_edge: int, cell: tuple[int, int] | None = None) -> tuple[int, int]:
    """A contact sheet with cells shaped like the frames, labels under each. `cell` (from sheet_layout) is the
    thumbnail size: full-size frames are shrunk to it first, so labels keep their size when the sheet is fitted."""
    from PIL import Image, ImageDraw

    from _render import fit_edge

    thumbs = []
    for f in files:
        with Image.open(f) as im:
            im = im.convert("RGB")
            if cell and (im.size[0] > cell[0] or im.size[1] > cell[1]):
                im.thumbnail(cell, Image.LANCZOS)
            thumbs.append(im)
    cw = max(t.size[0] for t in thumbs)
    ch = max(t.size[1] for t in thumbs)
    rows = math.ceil(len(thumbs) / cols)
    W = SHEET_PAD + cols * (cw + SHEET_PAD)
    H = SHEET_TITLE + SHEET_PAD + rows * (ch + SHEET_LABEL + SHEET_PAD)
    sheet = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(sheet)

    from _overlay import ui_font

    d.text((SHEET_PAD, 7), title, fill="black", font=ui_font(18, title))
    lf = ui_font(15, "".join(labels))
    for i, t in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = SHEET_PAD + c * (cw + SHEET_PAD) + (cw - t.size[0]) // 2
        y = SHEET_TITLE + SHEET_PAD + r * (ch + SHEET_LABEL + SHEET_PAD) + (ch - t.size[1]) // 2
        sheet.paste(t, (x, y))
        d.rectangle([x - 1, y - 1, x + t.size[0], y + t.size[1]], outline="#b0b0b0")
        if i < len(labels):
            ly = SHEET_TITLE + SHEET_PAD + r * (ch + SHEET_LABEL + SHEET_PAD) + ch + 3
            d.text((SHEET_PAD + c * (cw + SHEET_PAD), ly), str(labels[i]), fill="#222222", font=lf)
    sheet = fit_edge(sheet, max_edge)
    sheet.save(out)
    return sheet.size
