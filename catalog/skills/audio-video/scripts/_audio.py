"""Audio analysis and drawing for the audio-video skill: waveform and spectrogram PNGs, silence, loudness, clipping
and speech detection. Decoding streams through ffmpeg (bounded memory); numpy does the maths; Pillow draws."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError

# ── decoding helpers ────────────────────────────────────────────────────


_TP_FILTER: Any = None


def _tp_filter() -> Any:
    """Weights (3 phases × 17 taps) that interpolate the points 1/4, 2/4 and 3/4 of the way to the next sample:
    a Kaiser-windowed sinc for 4x oversampling, the method BS.1770 uses for true peak."""
    global _TP_FILTER
    if _TP_FILTER is None:
        import numpy as np

        k = np.arange(17)
        rows = []
        win = np.kaiser(71, 7.0)
        for ph in (1, 2, 3):
            t = 32 - 4 * k + ph  # position of each input sample relative to the output point, in quarter samples
            w = np.sinc(t / 4.0) * win[np.clip(t + 35, 0, 70)] * (np.abs(t) <= 35)
            rows.append(w / w.sum())
        _TP_FILTER = np.array(rows, dtype=np.float64)  # (3, 17)
    return _TP_FILTER


def true_peak_block(ext: Any, thr: Any) -> Any:
    """Highest 4x-oversampled level per channel in `ext` (n, ch), looking only near samples at or above `thr`
    (the true peak can only exceed the sample peak next to large samples). The first and last 8 rows are context."""
    import numpy as np

    h = _tp_filter()
    n, ch = ext.shape
    out = np.zeros(ch)
    if n < 18:
        return out
    offs = np.arange(-8, 9)
    for c in range(ch):
        x = ext[:, c].astype(np.float64)
        big = np.flatnonzero(np.abs(x[8:n - 9]) >= thr[c]) + 8
        if not big.size:
            continue
        cand = np.unique(np.concatenate([big, big - 1]))
        cand = cand[(cand >= 8) & (cand <= n - 10)]
        if not cand.size:
            continue
        for part in range(0, cand.size, 200_000):  # bounded memory for dense loud audio
            idx = cand[part:part + 200_000]
            win = x[idx[:, None] + offs[None, :]]  # (k, 17)
            out[c] = max(out[c], float(np.abs(win @ h.T).max()))
    return out


def column_stats(blocks: Iterable[Any], total: int, cols: int, channels: int, true_peak: bool = False) -> dict[str, Any]:
    """Per-column min, max and mean-square for each channel, streamed; plus clipping runs, sample peaks and (with
    true_peak) the 4x-oversampled true peak, computed near the loud samples only."""
    import numpy as np

    mn = np.full((cols, channels), np.inf, dtype=np.float32)
    mx = np.full((cols, channels), -np.inf, dtype=np.float32)
    ss = np.zeros((cols, channels), dtype=np.float64)
    cnt = np.zeros(cols, dtype=np.int64)
    clip_cols = np.zeros(cols, dtype=bool)
    clipped_samples = 0
    clip_runs: list[int] = []  # sample index where each run of 3+ clipped samples starts
    prev_run = np.zeros(channels, dtype=np.int64)
    pos = 0
    peak = np.zeros(channels, dtype=np.float64)
    tp = np.zeros(channels, dtype=np.float64)
    tail = np.zeros((0, channels), dtype=np.float32)
    total = max(total, 1)
    for b in blocks:
        n = b.shape[0]
        if n == 0:
            continue
        if true_peak:
            ext = np.concatenate([tail, b]) if tail.size else np.concatenate([np.zeros((8, channels), dtype=np.float32), b])
            bpeak = np.abs(b).max(axis=0)
            # Inter-sample overshoot rarely exceeds 4 dB: only samples within 4.4 dB of the loudest so far can matter.
            thr = np.maximum(np.maximum(tp, peak), bpeak) * 0.6
            tp = np.maximum(tp, true_peak_block(ext, thr))
            tail = ext[-18:]
        idx = np.arange(pos, pos + n, dtype=np.int64)
        col = np.minimum((idx * cols) // total, cols - 1)
        starts = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
        ucols = col[starts]
        bmin = np.minimum.reduceat(b, starts, axis=0)
        bmax = np.maximum.reduceat(b, starts, axis=0)
        bss = np.add.reduceat(b.astype(np.float64) ** 2, starts, axis=0)
        bcnt = np.diff(np.r_[starts, n])
        mn[ucols] = np.minimum(mn[ucols], bmin)
        mx[ucols] = np.maximum(mx[ucols], bmax)
        ss[ucols] += bss
        cnt[ucols] += bcnt
        absb = np.abs(b)
        peak = np.maximum(peak, absb.max(axis=0))
        hot = absb >= 0.999
        if hot.any():
            clipped_samples += int(hot.sum())
            for ch in range(channels):
                h = hot[:, ch].astype(np.int8)
                if not h.any():
                    prev_run[ch] = 0
                    continue
                # run starts and lengths, joining a run that continues from the previous block
                edges = np.diff(np.r_[0, h, 0])
                rs = np.flatnonzero(edges == 1)
                re_ = np.flatnonzero(edges == -1)
                lens = re_ - rs
                if rs.size and rs[0] == 0 and prev_run[ch]:
                    lens[0] += prev_run[ch]
                for s0, ln in zip(rs, lens):
                    if ln >= 3 and (s0 != 0 or prev_run[ch] < 3):
                        clip_runs.append(pos + int(s0))
                        clip_cols[min(cols - 1, (pos + int(s0)) * cols // total)] = True
                prev_run[ch] = lens[-1] if (re_.size and re_[-1] == n) else 0
        else:
            prev_run[:] = 0
        pos += n
    empty = cnt == 0
    mn[empty] = 0
    mx[empty] = 0
    rms = np.sqrt(ss / np.maximum(cnt, 1)[:, None]).astype(np.float32)
    if true_peak and tail.size:
        # The last samples: pad with silence so they get their interpolation too.
        tp = np.maximum(tp, true_peak_block(np.concatenate([tail, np.zeros((9, channels), dtype=np.float32)]), np.maximum(tp, peak) * 0.6))
    return {"min": mn, "max": mx, "rms": rms, "samples": pos, "peak": peak, "true_peak": np.maximum(tp, peak), "clipped_samples": clipped_samples,
            "clip_runs": sorted(set(clip_runs)), "clip_cols": clip_cols}


def db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-10 else -200.0


def frame_rms_db(path: str, sr: int = 8000, frame: float = 0.02, start: float | None = None, end: float | None = None, stream: int | None = None) -> tuple[Any, float]:
    """RMS level in dBFS of consecutive frames (default 20 ms) of the mono mix; returns (levels, frame seconds)."""
    import numpy as np

    from _media import read_audio

    n = max(1, int(sr * frame))
    levels: list[Any] = []
    rest = np.zeros(0, dtype=np.float32)
    for b in read_audio(path, sr=sr, channels=1, start=start, end=end, stream=stream):
        x = np.concatenate([rest, b[:, 0]])
        k = len(x) // n
        if k:
            fr = x[: k * n].reshape(k, n)
            levels.append(10 * np.log10(np.mean(fr.astype(np.float64) ** 2, axis=1) + 1e-12))
        rest = x[k * n:]
    if rest.size:
        levels.append(np.array([10 * math.log10(float(np.mean(rest.astype(np.float64) ** 2)) + 1e-12)]))
    arr = np.concatenate(levels) if levels else np.zeros(0)
    return arr, n / sr


def levels(path: str, start: float | None = None, end: float | None = None, stream: int | None = None) -> tuple[Any, float]:
    """frame_rms_db of the file, computed once per file content and range (cached as .npy), then read back in
    milliseconds: silence, speech maps, waveform shading and stats all start from it."""
    import numpy as np

    from _media import cached_product, release_product

    def build(tmp: Any) -> dict[str, Any]:
        lv, fs = frame_rms_db(path, start=start, end=end, stream=stream)
        np.save(tmp / "levels.npy", lv.astype(np.float32))
        return {"frame": fs, "frames": int(lv.size)}

    d, meta, _ = cached_product(path, "av-levels", {"start": start, "end": end, "track": stream or 0, "sr": 8000, "frame": 0.02}, "1", build)
    try:
        lv = np.load(d / "levels.npy").astype(np.float64)
    finally:
        release_product(d)
    return lv, float(meta["frame"])


def silence_ranges(levels: Any, frame_s: float, threshold_db: float, min_s: float, offset: float = 0.0) -> list[tuple[float, float]]:
    """Runs of frames quieter than threshold_db lasting at least min_s."""
    import numpy as np

    quiet = levels < threshold_db
    if not quiet.any():
        return []
    edges = np.diff(np.r_[0, quiet.astype(np.int8), 0])
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    out = []
    for s, e in zip(starts, ends):
        if (e - s) * frame_s >= min_s:
            out.append((offset + s * frame_s, offset + e * frame_s))
    return out


def auto_threshold(levels: Any) -> float:
    """A silence threshold from the level distribution: above the noise floor, below the programme."""
    import numpy as np

    if levels.size == 0:
        return -50.0
    real = levels[levels > -110]
    if real.size == 0:
        return -60.0
    if real.size < levels.size * 0.98:
        # Digital silence (edited or generated audio): the pauses are exact zeros, so anything clearly above them and
        # below the quietest programme counts as silence; quiet passages of the programme stay sound.
        quiet = float(np.percentile(real, 5))
        return float(max(-70.0, min(-35.0, quiet - 12.0)))
    floor = float(np.percentile(real, 10))
    loud = float(np.percentile(real, 90))
    thr = floor + max(6.0, min(15.0, (loud - floor) * 0.35))
    return float(max(-70.0, min(-20.0, thr)))


# ── loudness (ffmpeg ebur128 + astats) ──────────────────────────────────


def loudness(path: str, start: float | None = None, end: float | None = None, stream: int | None = None, duration: float | None = None, true_peak: bool = True) -> dict[str, Any]:
    """EBU R128 integrated loudness and range (and true peak unless true_peak=False: ffmpeg's oversampling is the slow
    part, column_stats measures it faster), plus per-channel sample peak, RMS and DC offset."""
    from _media import run_ffmpeg, secs_arg

    args: list[Any] = []
    if start:
        args += ["-ss", secs_arg(start)]
    args += ["-i", path]
    if end is not None:
        args += ["-t", secs_arg(end - (start or 0.0))]
    args += ["-map", f"0:a:{stream or 0}", "-vn", "-sn", "-dn", "-af", f"ebur128=peak={'true+sample' if true_peak else 'sample'}:framelog=quiet,astats=measure_perchannel=Peak_level+RMS_level+DC_offset+Flat_factor+Peak_count+Noise_floor:measure_overall=Peak_level+RMS_level+DC_offset", "-f", "null", "-"]
    text = run_ffmpeg(args, duration=(end or duration or 0) - (start or 0.0) or None, label="measuring loudness", loglevel="info")
    return parse_loudness(text)


def parse_loudness(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""

    def grab(label: str, src: str) -> float | None:
        m = re.search(label + r":\s*(-?[\d.]+|-inf|nan)", src)
        if not m:
            return None
        try:
            v = float(m.group(1))
            return v if math.isfinite(v) else None
        except ValueError:
            return None

    if summary:
        out["integrated_lufs"] = grab(r"\bI", summary)
        out["threshold_lufs"] = grab(r"Threshold", summary)
        out["lra_lu"] = grab(r"LRA", summary)
        tp = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)", summary)
        sp = re.search(r"Sample peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)", summary)
        out["true_peak_dbtp"] = float(tp.group(1)) if tp and tp.group(1) != "-inf" else None
        out["sample_peak_dbfs"] = float(sp.group(1)) if sp and sp.group(1) != "-inf" else None
    channels: list[dict[str, Any]] = []
    overall: dict[str, Any] = {}
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if "Parsed_astats" not in line:
            continue
        body = line.split("]", 1)[-1].strip()
        m = re.match(r"Channel:\s*(\d+)", body)
        if m:
            cur = {"channel": int(m.group(1))}
            channels.append(cur)
            continue
        if body.startswith("Overall"):
            cur = overall
            continue
        m = re.match(r"([A-Za-z][A-Za-z _]+?):\s*(-?[\d.]+|-?inf|nan)", body)
        if m and cur is not None:
            key = m.group(1).strip().lower().replace(" ", "_")
            try:
                val = float(m.group(2))
            except ValueError:
                continue
            cur[key] = val if math.isfinite(val) else None
    if channels:
        out["channels"] = channels
    if overall:
        out["overall"] = overall
    return out


def loudness_verdict(l: dict[str, Any]) -> list[str]:
    """Plain-language notes about levels, for the agent to relay."""
    notes = []
    i = l.get("integrated_lufs")
    tp = l.get("true_peak_dbtp")
    if i is not None:
        if i < -30:
            notes.append(f"very quiet ({i:.1f} LUFS)")
        elif i < -20:
            notes.append(f"quiet for web or podcasts ({i:.1f} LUFS; -16 is typical, -23 is broadcast)")
        elif i > -9:
            notes.append(f"very loud ({i:.1f} LUFS; streaming services turn it down to about -14)")
        else:
            notes.append(f"loudness {i:.1f} LUFS")
    if tp is not None:
        if tp > -0.1:
            notes.append(f"true peak {tp:+.1f} dBTP: at or over full scale, likely clipping")
        elif tp > -1.0:
            notes.append(f"true peak {tp:+.1f} dBTP: little headroom (-1 dBTP is the usual ceiling)")
    return notes


# ── speech (Silero VAD bundled with faster-whisper) ─────────────────────


def import_onnxruntime_quietly() -> None:
    """Imports onnxruntime (used by faster-whisper's voice-activity model) with native stderr muted: on import it
    prints a telemetry warning when it cannot save a device id, which is always the case inside Desk's sandbox."""
    import os
    import sys

    if "onnxruntime" in sys.modules:
        return
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    saved = None
    if fd is not None:
        sys.stderr.flush()
        saved = os.dup(fd)
        with open(os.devnull, "w", encoding="utf-8") as null:
            os.dup2(null.fileno(), fd)
    try:
        import onnxruntime

        onnxruntime.set_default_logger_severity(3)
    except ImportError:
        pass
    finally:
        if saved is not None and fd is not None:
            os.dup2(saved, fd)
            os.close(saved)


def speech_segments(path: str, threshold: float = 0.5, min_speech: float = 0.25, min_silence: float = 0.5, pad: float = 0.1, start: float | None = None, end: float | None = None, stream: int | None = None) -> list[tuple[float, float]]:
    """Speech ranges found by the Silero voice-activity model (runs offline, CPU), processed in 10-minute chunks."""
    import numpy as np

    import_onnxruntime_quietly()
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError as e:
        raise SkillError(f"speech detection needs faster-whisper in the runtime ({e})") from None
    from _media import read_audio

    sr = 16000
    opts = VadOptions(threshold=threshold, min_speech_duration_ms=int(min_speech * 1000), min_silence_duration_ms=int(min_silence * 1000), speech_pad_ms=int(pad * 1000))
    chunk = sr * 600
    buf: list[Any] = []
    have = 0
    offset = start or 0.0
    out: list[tuple[float, float]] = []

    def run(audio: Any, off: float) -> None:
        for seg in get_speech_timestamps(audio, opts):
            a, b = off + seg["start"] / sr, off + seg["end"] / sr
            if out and a - out[-1][1] < min_silence:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))

    for b in read_audio(path, sr=sr, channels=1, start=start, end=end, stream=stream):
        buf.append(b[:, 0])
        have += b.shape[0]
        if have >= chunk:
            audio = np.concatenate(buf)
            run(audio[:chunk], offset)
            rest = audio[chunk:]
            buf, have = [rest], rest.shape[0]
            offset += chunk / sr
    if have:
        run(np.concatenate(buf), offset)
    return out


# ── drawing ─────────────────────────────────────────────────────────────

_MAGMA = [(0.0, (0, 0, 4)), (0.13, (28, 16, 68)), (0.25, (79, 18, 123)), (0.38, (129, 37, 129)), (0.5, (181, 54, 122)),
          (0.63, (229, 80, 100)), (0.75, (251, 135, 97)), (0.88, (254, 194, 135)), (1.0, (252, 253, 191))]


def magma_lut() -> Any:
    import numpy as np

    xs = np.array([p for p, _ in _MAGMA])
    cols = np.array([c for _, c in _MAGMA], dtype=np.float32)
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, xs, cols[:, k]) for k in range(3)], axis=1).astype(np.uint8)


def font(size: int, text: str = "") -> Any:
    from _overlay import ui_font

    return ui_font(size, text)


def nice_step(span: float, target: int = 10) -> float:
    steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
    for s in steps:
        if span / s <= target:
            return s
    return steps[-1]


def tlabel(t: float, step: float) -> str:
    if step < 1:
        dec = 2 if step < 0.1 else 1
        m, s = divmod(t, 60)
        return f"{int(m)}:{s:0{3 + dec}.{dec}f}"
    t = int(round(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def draw_time_axis(d: Any, x0: int, x1: int, y: int, t0: float, t1: float, color: str = "#444444") -> None:
    span = max(t1 - t0, 1e-6)
    step = nice_step(span, max(4, (x1 - x0) // 130))
    f = font(13)
    first = math.ceil(t0 / step) * step
    t = first
    right = d.im.size[0] - 2 if hasattr(d, "im") else x1 + 60
    while t <= t1 + 1e-9:
        x = x0 + (t - t0) / span * (x1 - x0)
        d.line([(x, y), (x, y + 5)], fill=color)
        label = tlabel(t, step)
        w = d.textlength(label, font=f)
        # Labels near the right edge end at their tick instead of starting there, so they are never cut off.
        d.text((x + 2, y + 5) if x + 2 + w <= right else (x - 2 - w, y + 5), label, fill=color, font=f)
        t += step


def draw_waveform(stats: dict[str, Any], out: str | Path, width: int, lane_h: int, title: str, t0: float, t1: float, names: Sequence[str], silences: Sequence[tuple[float, float]] = (), log_scale: bool = False) -> tuple[int, int]:
    import numpy as np
    from PIL import Image, ImageDraw

    ch = stats["min"].shape[1]
    left, right, top, bottom, gap = 56, 12, 34, 30, 8
    cols = stats["min"].shape[0]
    W = left + cols + right
    H = top + ch * lane_h + (ch - 1) * gap + bottom
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 8), title, fill="black", font=font(16, title))
    span = max(t1 - t0, 1e-9)
    small = font(12)

    def amp_to_y(v: Any, y0: int) -> Any:
        half = lane_h / 2
        if log_scale:
            sgn = np.sign(v)
            mag = np.clip((20 * np.log10(np.maximum(np.abs(v), 1e-6)) + 48) / 48, 0, 1)
            v = sgn * mag
        return y0 + half - np.clip(v, -1, 1) * (half - 2)

    for c in range(ch):
        y0 = top + c * (lane_h + gap)
        d.rectangle([left, y0, left + cols - 1, y0 + lane_h - 1], fill="#f7f8fa", outline="#d0d4da")
        for a, b in silences:
            xa = left + int((a - t0) / span * cols)
            xb = left + int((b - t0) / span * cols)
            if xb > left and xa < left + cols:
                d.rectangle([max(left, xa), y0 + 1, min(left + cols - 1, xb), y0 + lane_h - 2], fill="#f8e2a0")
        for frac, lab in ((0.5, "-6"), (0.25, "-12")):
            v = frac if not log_scale else (1 - (-20 * math.log10(frac)) / 48)
            for sgn in (1, -1):
                y = y0 + lane_h / 2 - sgn * v * (lane_h / 2 - 2)
                d.line([(left, y), (left + cols - 1, y)], fill="#e3e6ea")
            d.text((left - 4, y0 + lane_h / 2 - v * (lane_h / 2 - 2)), lab, fill="#888888", font=small, anchor="rm")
        d.line([(left, y0 + lane_h / 2), (left + cols - 1, y0 + lane_h / 2)], fill="#c5cad1")
        ymin = amp_to_y(stats["min"][:, c], y0)
        ymax = amp_to_y(stats["max"][:, c], y0)
        yr1 = amp_to_y(stats["rms"][:, c], y0)
        yr2 = amp_to_y(-stats["rms"][:, c], y0)
        for x in range(cols):
            d.line([(left + x, ymax[x]), (left + x, ymin[x])], fill="#5b8fd0")
            d.line([(left + x, yr1[x]), (left + x, yr2[x])], fill="#1f4e8c")
        d.text((8, y0 + lane_h / 2 - 8), names[c] if c < len(names) else f"ch{c + 1}", fill="#333333", font=font(14))
        d.text((left - 4, y0 + 1), "0 dB", fill="#888888", font=small, anchor="ra")
    clip_cols = np.flatnonzero(stats["clip_cols"])
    for x in clip_cols:
        d.line([(left + x, top - 6), (left + x, top + ch * (lane_h + gap) - gap)], fill="#e03131")
    draw_time_axis(d, left, left + cols, top + ch * (lane_h + gap) - gap + 2, t0, t1)
    img.save(out)
    return img.size


def draw_spectrogram(spec_db: Any, out: str | Path, freqs: Any, t0: float, t1: float, title: str, log_freq: bool, dyn: float) -> tuple[int, int]:
    """spec_db: (rows, cols) dB values relative to the maximum (0 = loudest), row 0 = highest frequency."""
    import numpy as np
    from PIL import Image, ImageDraw

    rows, cols = spec_db.shape
    left, right, top, bottom = 62, 70, 34, 30
    W, H = left + cols + right, top + rows + bottom
    img = Image.new("RGB", (W, H), "white")
    lut = magma_lut()
    norm = np.clip((spec_db + dyn) / dyn, 0, 1)
    rgb = lut[(norm * 255).astype(np.uint8)]
    img.paste(Image.fromarray(rgb, "RGB"), (left, top))
    d = ImageDraw.Draw(img)
    d.text((10, 8), title, fill="black", font=font(16, title))
    f = font(12)
    fmin, fmax = float(freqs[-1]), float(freqs[0])
    ticks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000] if log_freq else None
    if not log_freq:
        step = nice_step(fmax, 8)
        ticks = [k * step for k in range(int(fmax // step) + 1)]
    for fr in ticks:
        if fr < fmin or fr > fmax:
            continue
        if log_freq:
            y = top + (math.log(fmax) - math.log(fr)) / (math.log(fmax) - math.log(fmin)) * (rows - 1)
        else:
            y = top + (fmax - fr) / (fmax - fmin) * (rows - 1)
        d.line([(left - 5, y), (left, y)], fill="#444444")
        lab = f"{fr / 1000:g}k" if fr >= 1000 else f"{fr:g}"
        d.text((left - 8, y), lab + " Hz" if fr < 1000 else lab, fill="#444444", font=f, anchor="rm")
    d.rectangle([left - 1, top - 1, left + cols, top + rows], outline="#999999")
    draw_time_axis(d, left, left + cols, top + rows + 2, t0, t1)
    # colour bar
    bx = left + cols + 18
    bar = np.linspace(1, 0, rows)
    bar_rgb = lut[(bar * 255).astype(np.uint8)][:, None, :].repeat(14, axis=1)
    img.paste(Image.fromarray(bar_rgb, "RGB"), (bx, top))
    for k in range(0, int(dyn) + 1, 20 if dyn > 60 else 10):
        y = top + k / dyn * (rows - 1)
        d.text((bx + 17, y - 7), f"-{k}" if k else "0 dB", fill="#444444", font=f)
    img.save(out)
    return img.size


def spectrogram_data(blocks_or_array: Any, sr: int, total: int, cols: int, rows: int, n_fft: int, fmin: float, fmax: float, log_freq: bool) -> tuple[Any, Any]:
    """Power spectrogram at exactly `cols` columns and `rows` frequency rows (dB, 0 = the loudest cell)."""
    import numpy as np

    win = np.hanning(n_fft).astype(np.float32)
    bin_f = np.fft.rfftfreq(n_fft, 1 / sr)
    if log_freq:
        row_f = np.geomspace(fmax, max(fmin, bin_f[1]), rows)
    else:
        row_f = np.linspace(fmax, fmin, rows)
    out = np.zeros((rows, cols), dtype=np.float32)
    span = total / cols

    def column_power(seg: Any) -> Any:
        """Mean power spectrum over up to 6 windows inside the segment."""
        if seg.shape[0] < n_fft:
            seg = np.pad(seg, (0, n_fft - seg.shape[0]))
        k = int(min(6, max(1, (seg.shape[0] - n_fft) // (n_fft // 2) + 1)))
        starts = np.linspace(0, seg.shape[0] - n_fft, k).astype(int)
        frames = np.stack([seg[s:s + n_fft] for s in starts]) * win
        return (np.abs(np.fft.rfft(frames, axis=1)) ** 2).mean(axis=0)

    # Each row averages the FFT bins inside its band (rows near the top of a log axis span many bins).
    if rows > 1:
        mids = np.sqrt(row_f[:-1] * row_f[1:]) if log_freq else (row_f[:-1] + row_f[1:]) / 2
        band_edges = np.r_[row_f[0] + (row_f[0] - mids[0]), mids, max(0.0, row_f[-1] - (mids[-1] - row_f[-1]))]
    else:
        band_edges = np.array([fmax, fmin])
    hi_idx = np.searchsorted(bin_f, band_edges[:-1])
    lo_idx = np.searchsorted(bin_f, band_edges[1:])
    wide = hi_idx - lo_idx >= 2

    def put(c: int, p: Any) -> None:
        col = np.interp(row_f, bin_f, p)
        if wide.any():
            cs = np.r_[0.0, np.cumsum(p)]
            col[wide] = (cs[hi_idx[wide]] - cs[lo_idx[wide]]) / (hi_idx[wide] - lo_idx[wide])
        out[:, c] = col

    if isinstance(blocks_or_array, np.ndarray):
        x = blocks_or_array
        for c in range(cols):
            centre = int((c + 0.5) * span)
            a = max(0, min(len(x) - n_fft, centre - n_fft // 2)) if span < n_fft else int(c * span)
            b = a + max(n_fft, int(span))
            put(c, column_power(x[a:b]))
    else:
        buf = np.zeros(0, dtype=np.float32)
        consumed = 0
        c = 0
        for blk in blocks_or_array:
            buf = np.concatenate([buf, blk[:, 0]])
            while c < cols:
                a = int(c * span) - consumed
                b = int((c + 1) * span) - consumed
                if b > buf.shape[0]:
                    break
                put(c, column_power(buf[a:b]))
                c += 1
            drop = int(c * span) - consumed
            if drop > 0:
                buf = buf[drop:]
                consumed += drop
        while c < cols:
            a = int(c * span) - consumed
            put(c, column_power(buf[max(0, a):]) if buf.shape[0] > max(0, a) else np.zeros_like(bin_f))
            c += 1
    spec = 10 * np.log10(out + 1e-12)
    spec -= spec.max()
    return spec, row_f
