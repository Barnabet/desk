#!/usr/bin/env python3
"""See and measure audio: waveform and spectrogram PNGs, silence and speech ranges, loudness (LUFS), peaks and
clipping. Works on audio files and on the audio track of videos."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, input_file, output_path, parser, run_main  # noqa: E402
from _paging import add_paging, check_paging, emit_json_page, page_text  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_audio.py waveform podcast.mp3                    # podcast-waveform.png, look at it with view_image
  python3 scripts/media_audio.py waveform talk.mp4 --start 1:00 --end 1:30 --mark-silence
  python3 scripts/media_audio.py spectrogram song.flac --max-freq 16000
  python3 scripts/media_audio.py stats podcast.mp3                       # LUFS, true peak, RMS, DC, clipping, silence share
  python3 scripts/media_audio.py silence interview.wav --threshold auto --min 1.5
  python3 scripts/media_audio.py speech interview.wav                    # where people talk (offline voice-activity model)

Times: 83.5, 1:23.5, 01:02:03.250, 25%, end. --track picks the audio track of a file with several (1-based).
Results are cached per file content and options: asking again is instant (--no-cache recomputes).
"""


def main() -> int:
    p = parser("See and measure audio: waveform and spectrogram PNGs sized for vision, silence ranges, speech ranges, "
               "EBU R128 loudness (LUFS, LRA, true peak), sample peaks, RMS, DC offset and clipping.", EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{waveform,spectrogram,stats,silence,speech}")

    def common(sp: Any, png: bool = False) -> None:
        sp.add_argument("input")
        sp.add_argument("--start", help="range start")
        sp.add_argument("--end", help="range end")
        sp.add_argument("--track", type=int, help="audio track number, 1-based (default: the main one)")
        if png:
            sp.add_argument("--out", help="output PNG (default <name>-<kind>.png)")
            sp.add_argument("--width", type=int, default=1568, help="image width in px (default 1568)")
            sp.add_argument("--force", action="store_true", help="overwrite an existing output")
        sp.add_argument("--no-cache", action="store_true", help="recompute instead of reusing a cached result")
        add_format(sp)

    w = sub.add_parser("waveform", help="a waveform PNG (peaks and RMS per channel, time axis, clipping marked)", formatter_class=p.formatter_class)
    common(w, png=True)
    w.add_argument("--height", type=int, help="lane height per channel in px (default 220 mono, 170 per channel otherwise)")
    w.add_argument("--mono", action="store_true", help="mix channels into one lane")
    w.add_argument("--log", action="store_true", help="logarithmic (dB) amplitude scale; shows quiet passages better")
    w.add_argument("--mark-silence", action="store_true", help="shade silent ranges (threshold from --threshold)")
    w.add_argument("--threshold", default="auto", help="silence threshold in dBFS or 'auto' (default auto)")

    s = sub.add_parser("spectrogram", help="a spectrogram PNG (frequency over time)", formatter_class=p.formatter_class)
    common(s, png=True)
    s.add_argument("--height", type=int, default=520, help="plot height in px (default 520)")
    s.add_argument("--max-freq", type=float, help="top frequency in Hz (default: half the sample rate, at most 24000; 8000 shows speech in more detail)")
    s.add_argument("--min-freq", type=float, default=30.0, help="bottom frequency on the log axis (default 30)")
    s.add_argument("--linear", action="store_true", help="linear frequency axis (default logarithmic)")
    s.add_argument("--range", type=float, default=90.0, help="dynamic range shown, dB (default 90)")
    s.add_argument("--fft", type=int, default=2048, help="FFT size (default 2048)")

    st = sub.add_parser("stats", help="loudness (LUFS), true peak, sample peak, RMS, DC offset, clipping, silence share", formatter_class=p.formatter_class)
    common(st)
    st.add_argument("--threshold", default="auto", help="silence threshold in dBFS or 'auto' (default auto)")

    si = sub.add_parser("silence", help="silent ranges (and the sounding ranges between them)", formatter_class=p.formatter_class)
    common(si)
    si.add_argument("--threshold", default="auto", help="level in dBFS below which audio counts as silent, or 'auto' (default)")
    si.add_argument("--min", type=float, default=0.5, help="minimum silence length in seconds (default 0.5)")
    si.add_argument("--sounds", action="store_true", help="list the non-silent ranges instead")
    add_paging(si, "ranges")

    sp = sub.add_parser("speech", help="speech ranges from the Silero voice-activity model (offline)", formatter_class=p.formatter_class)
    common(sp)
    sp.add_argument("--sensitivity", type=float, default=0.5, help="speech probability threshold 0-1 (default 0.5; lower finds more)")
    sp.add_argument("--min-silence", type=float, default=0.5, help="gaps shorter than this join segments (default 0.5 s)")
    sp.add_argument("--min-speech", type=float, default=0.25, help="drop speech shorter than this (default 0.25 s)")
    add_paging(sp, "segments")

    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], ("--start", "--end", "--threshold")))
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    if hasattr(a, "offset"):
        check_paging(a)
    return {"waveform": cmd_waveform, "spectrogram": cmd_spectrogram, "stats": cmd_stats, "silence": cmd_silence, "speech": cmd_speech}[a.cmd](a)


def _load(a: Any) -> tuple[dict[str, Any], dict[str, Any], int, float, float | None]:
    """(info, audio stream, track position, start, end) for the common options."""
    from _media import audio_stream_pos, parse_time, probe

    info = probe(input_file(a.input), side_data=False)
    pos = audio_stream_pos(info, a.track)
    auds = [s for s in info["streams"] if s["type"] == "audio"]
    d = info.get("duration")
    s = parse_time(a.start, d) if a.start else 0.0
    e = parse_time(a.end, d) if a.end else None
    if e is not None and d:
        e = min(e, d)
    if e is not None and e <= s:
        raise UsageError("the range is empty (--end must be after --start)")
    return info, auds[pos], pos, s, e


def _span(info: dict[str, Any], s: float, e: float | None) -> float:
    d = info.get("duration") or 0.0
    return max(0.0, (e if e is not None else d) - s)


def _default_out(info: dict[str, Any], kind: str, out: str | None, force: bool) -> Path:
    return output_path(out or f"{Path(info['name']).stem}-{kind}.png", [info["file"]], force)


def _threshold(value: str, levels: Any) -> float:
    from _audio import auto_threshold

    if str(value).lower() == "auto":
        return auto_threshold(levels)
    try:
        v = float(str(value).lower().replace("db", "").replace("fs", ""))
    except ValueError:
        raise UsageError(f"bad threshold '{value}' (use a level like -45 or auto)") from None
    if v > 0:
        v = -v
    return v


def _fmt(t: float) -> str:
    from _media import fmt_time

    return fmt_time(t)


# ── waveform ────────────────────────────────────────────────────────────


def cmd_waveform(a: Any) -> int:
    from _media import cached_product, copy_out, release_product
    from _render import announce

    t0 = time.monotonic()
    info, aud, pos, s, e = _load(a)
    span = _span(info, s, e)
    if span <= 0:
        raise SkillError(f"{info['name']}: the audio has no duration")
    out = _default_out(info, "waveform", a.out, a.force)
    params = {"name": info["name"], "start": s, "end": e, "track": pos, "width": a.width, "height": a.height, "mono": a.mono, "log": a.log,
              "silence": a.mark_silence, "threshold": str(a.threshold)}
    d, data, cached = cached_product(info["file"], "av-waveform", params, "3", lambda tmp: _draw_waveform(a, info, aud, pos, s, e, span, tmp / "waveform.png"))
    try:
        copy_out(d / "waveform.png", out)
    finally:
        release_product(d)
    data = {"file": str(out), **data, "cached": cached, "seconds": round(time.monotonic() - t0, 2)}
    if a.format == "json":
        emit(data, "json")
        return 0
    note = f"{data['width']}x{data['height']}; peak {data['peak_dbfs']:.1f} dBFS, RMS {data['rms_dbfs']:.1f} dBFS"
    if data["clip_times"]:
        note += f"; clipping at {', '.join(_fmt(t) for t in data['clip_times'][:8])}" + (" …" if data["clipped_runs"] > 8 else "")
    if a.mark_silence:
        note += f"; {len(data['silences'])} silent range(s) shaded (below {data['silence_threshold_db']:.0f} dBFS)"
    announce([out], note)
    return 0


def _draw_waveform(a: Any, info: dict[str, Any], aud: dict[str, Any], pos: int, s: float, e: float | None, span: float, png: Path) -> dict[str, Any]:
    import numpy as np

    from _audio import column_stats, db, draw_waveform, levels, silence_ranges
    from _media import fmt_dur, read_audio

    channels = 1 if a.mono else max(1, min(int(aud.get("channels") or 1), 8))
    width = max(300, min(a.width, 8000))
    cols = width - 68
    native = int(aud.get("sample_rate") or 48000)
    # Enough samples per pixel column for true-looking peaks, without decoding hours at full rate.
    sr = native if span * native / cols < 20000 else max(8000, min(native, int(cols * 20000 / span)))
    total = int(span * sr)
    stats = column_stats(read_audio(info["file"], sr=sr, channels=channels, start=s or None, end=e, stream=pos), total, cols, channels)
    silences: list[tuple[float, float]] = []
    thr = None
    if a.mark_silence:
        lv, fs = levels(info["file"], s or None, e, pos)
        thr = _threshold(a.threshold, lv)
        silences = silence_ranges(lv, fs, thr, 0.5, offset=s)
    names = ["mono"] if channels == 1 else (["L", "R"] if channels == 2 else [f"ch{i + 1}" for i in range(channels)])
    lane = a.height or (220 if channels == 1 else 170 if channels == 2 else 110)
    peak_db = db(float(stats["peak"].max()))
    rms_all = float(np.sqrt(np.mean(stats["rms"].astype(np.float64) ** 2)))
    title = f"{info['name']} · {fmt_dur(span)}" + (f" from {_fmt(s)}" if s else "") + f" · {native / 1000:g} kHz · {_layout(aud)} · peak {peak_db:.1f} dBFS · RMS {db(rms_all):.1f} dBFS"
    if stats["clip_runs"]:
        title += f" · {len(stats['clip_runs'])} clipped run(s) (red)"
    W, H = draw_waveform(stats, png, width, lane, title, s, s + span, names, silences, a.log)
    clip_times = [s + i / sr for i in stats["clip_runs"][:50]]
    return {"width": W, "height": H, "duration": span, "peak_dbfs": round(peak_db, 2), "rms_dbfs": round(db(rms_all), 2),
            "clipped_runs": len(stats["clip_runs"]), "clip_times": [round(t, 3) for t in clip_times],
            "silences": [[round(x, 3), round(y, 3)] for x, y in silences], "silence_threshold_db": thr}


def _layout(aud: dict[str, Any]) -> str:
    """'stereo', 'mono', '5.1' …; ffmpeg's '1 channels' for files without a channel layout reads as mono."""
    import re

    lay = str(aud.get("layout") or "")
    ch = int(aud.get("channels") or 0)
    if not lay or re.fullmatch(r"\d+ channels?", lay):
        return {1: "mono", 2: "stereo"}.get(ch, f"{ch} ch")
    return lay


# ── spectrogram ─────────────────────────────────────────────────────────


def cmd_spectrogram(a: Any) -> int:
    from _media import cached_product, copy_out, release_product
    from _render import announce

    t0 = time.monotonic()
    info, aud, pos, s, e = _load(a)
    span = _span(info, s, e)
    if span <= 0:
        raise SkillError(f"{info['name']}: the audio has no duration")
    out = _default_out(info, "spectrogram", a.out, a.force)
    params = {"name": info["name"], "start": s, "end": e, "track": pos, "width": a.width, "height": a.height, "max": a.max_freq, "min": a.min_freq,
              "linear": a.linear, "range": a.range, "fft": a.fft}
    d, data, cached = cached_product(info["file"], "av-spectrogram", params, "2", lambda tmp: _draw_spectrogram(a, info, aud, pos, s, e, span, tmp / "spectrogram.png"))
    try:
        copy_out(d / "spectrogram.png", out)
    finally:
        release_product(d)
    data = {"file": str(out), **data, "cached": cached, "seconds": round(time.monotonic() - t0, 2)}
    if a.format == "json":
        emit(data, "json")
    else:
        announce([out], f"{data['width']}x{data['height']}; strongest average energy near {data['strongest_band_hz']:.0f} Hz. Bright = loud; time runs left to right, low frequencies at the bottom.")
    return 0


def _draw_spectrogram(a: Any, info: dict[str, Any], aud: dict[str, Any], pos: int, s: float, e: float | None, span: float, png: Path) -> dict[str, Any]:
    import numpy as np

    from _audio import draw_spectrogram, spectrogram_data
    from _media import fmt_dur, read_audio

    native = int(aud.get("sample_rate") or 44100)
    fmax = a.max_freq or min(24000.0, native / 2)  # the whole audible band: MP3/AAC cut-offs sit at 15-20 kHz
    if fmax > native / 2:
        fmax = native / 2
    sr = int(min(native, max(2 * fmax, 1000)))
    fft = a.fft if a.fft >= 64 else 2048
    width = max(300, min(a.width, 8000))
    cols = width - 132
    rows = max(64, min(a.height, 2000))
    total = int(span * sr)
    blocks = read_audio(info["file"], sr=sr, channels=1, start=s or None, end=e, stream=pos)
    if total <= 8_000_000:
        chunks = [b[:, 0] for b in blocks]
        x = np.concatenate(chunks) if chunks else np.zeros(fft, dtype=np.float32)
        spec, row_f = spectrogram_data(x, sr, len(x), cols, rows, fft, a.min_freq, fmax, not a.linear)
    else:
        spec, row_f = spectrogram_data(blocks, sr, total, cols, rows, fft, a.min_freq, fmax, not a.linear)
    # The loudest band over time helps describe the picture in words.
    energy = spec.mean(axis=1)
    top_row = int(np.argmax(energy))
    title = f"{info['name']} · {fmt_dur(span)}" + (f" from {_fmt(s)}" if s else "") + f" · {'log' if not a.linear else 'linear'} frequency to {fmax / 1000:g} kHz · {a.range:g} dB range"
    W, H = draw_spectrogram(spec, png, row_f, s, s + span, title, not a.linear, a.range)
    return {"width": W, "height": H, "duration": span, "max_freq": fmax, "sample_rate_used": sr, "strongest_band_hz": round(float(row_f[top_row]), 1)}


# ── stats ───────────────────────────────────────────────────────────────


def cmd_stats(a: Any) -> int:
    from _media import fmt_dur

    info, aud, pos, s, e = _load(a)
    span = _span(info, s, e)
    params = {"start": s, "end": e, "track": pos, "threshold": str(a.threshold)}
    try:
        import _cache

        data = _cache.cached_json(info["file"], "av-stats", params, "3", lambda: _measure(a, info, aud, pos, s, e, span))
    except OSError:
        data = _measure(a, info, aud, pos, s, e, span)
    data = {**data, "file": info["file"]}
    if a.format == "json":
        emit(data, "json")
        return 0

    def f(v: Any, unit: str) -> str:
        return "n/a" if v is None else f"{v:.1f} {unit}"

    clip_times = data["clip_times"]
    silent, nsil, thr = data["silent_seconds"], data["silent_ranges"], data["silence_threshold_dbfs"]
    lines = [f"# {info['name']}" + (f" ({_fmt(s)}–{_fmt(s + span)})" if s or e else ""), "",
             f"- {fmt_dur(span)} · {data['sample_rate'] / 1000:g} kHz · {_layout(aud)} · {aud.get('codec')}",
             f"- **Integrated loudness:** {f(data.get('integrated_lufs'), 'LUFS')} · loudness range {f(data.get('lra_lu'), 'LU')}",
             f"- **True peak:** {f(data.get('true_peak_dbtp'), 'dBTP')} · sample peak {f(data.get('sample_peak_dbfs'), 'dBFS')} · RMS {f(data['rms_dbfs'], 'dBFS')}",
             (f"- **Clipping:** {data['clipped_samples']} full-scale samples in {data['clipped_runs']} run(s), at {', '.join(_fmt(t) for t in clip_times[:6])}"
              if clip_times else f"- **Clipping:** none ({data['clipped_samples']} full-scale samples)"),
             f"- **Silence:** {silent:.1f} s in {nsil} range(s) below {thr:.0f} dBFS ({(100 * silent / span) if span else 0:.0f}%)"]
    if data["per_channel"] and len(data["per_channel"]) > 1:
        from _common import md_table

        lines += ["", md_table(["channel", "peak dBFS", "RMS dBFS", "DC offset", "noise floor dBFS"],
                               [[c["channel"], _n(c["peak_dbfs"]), _n(c["rms_dbfs"]), _n(c["dc_offset"], 4), _n(c["noise_floor_dbfs"])] for c in data["per_channel"]])]
    if data["notes"]:
        lines += ["", "Notes: " + "; ".join(data["notes"]) + "."]
    print("\n".join(lines))
    return 0


def _measure(a: Any, info: dict[str, Any], aud: dict[str, Any], pos: int, s: float, e: float | None, span: float) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    from _audio import column_stats, db, levels, loudness, loudness_verdict, silence_ranges
    from _common import workers_for
    from _media import read_audio

    channels = max(1, min(int(aud.get("channels") or 1), 8))
    native = int(aud.get("sample_rate") or 48000)
    # ffmpeg measures loudness (EBU R128 and astats) while numpy reads the samples for peaks, true peak and clipping;
    # one after the other when DESK_MAX_WORKERS is 1.
    def samples() -> dict[str, Any]:
        return column_stats(read_audio(info["file"], sr=native, channels=channels, start=s or None, end=e, stream=pos), max(1, int(span * native)), 1000, channels,
                            true_peak=True)

    if workers_for(2) >= 2:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(loudness, info["file"], s or None, e, pos, info.get("duration"), False)
            cs = samples()
            lo = fut.result()
    else:
        lo = loudness(info["file"], s or None, e, pos, info.get("duration"), False)
        cs = samples()
    tp = float(cs["true_peak"].max()) if cs["samples"] else 0.0
    lo["true_peak_dbtp"] = round(db(tp), 2) if tp > 0 else None
    lv, fs = levels(info["file"], s or None, e, pos)
    thr = _threshold(a.threshold, lv)
    sil = silence_ranges(lv, fs, thr, 0.5, offset=s)
    silent = sum(y - x for x, y in sil)
    clip_times = [round(s + i / native, 3) for i in cs["clip_runs"][:50]]
    data: dict[str, Any] = {
        "duration": span, "sample_rate": native, "channels": channels, "layout": _layout(aud), "codec": aud.get("codec"),
        **{k: v for k, v in lo.items() if k not in ("channels", "overall")},
        "rms_dbfs": (lo.get("overall") or {}).get("rms_level_db"),
        "dc_offset": (lo.get("overall") or {}).get("dc_offset"),
        "per_channel": [{"channel": c.get("channel"), "peak_dbfs": c.get("peak_level_db"), "rms_dbfs": c.get("rms_level_db"), "dc_offset": c.get("dc_offset"), "noise_floor_dbfs": c.get("noise_floor_db")} for c in lo.get("channels", [])],
        "clipped_samples": cs["clipped_samples"], "clipped_runs": len(cs["clip_runs"]), "clip_times": clip_times,
        "silence_threshold_dbfs": round(thr, 1), "silent_seconds": round(silent, 2), "silent_ranges": len(sil),
        "notes": loudness_verdict(lo),
    }
    if cs["clip_runs"]:
        data["notes"].append(f"{len(cs['clip_runs'])} clipped run(s) of 3+ full-scale samples, first at {_fmt(clip_times[0])}")
    dc = data.get("dc_offset")
    if dc is not None and abs(dc) > 0.01:
        data["notes"].append(f"DC offset {dc:+.3f}: a high-pass filter would remove it")
    if span and silent / span > 0.3:
        data["notes"].append(f"{100 * silent / span:.0f}% of the audio is silent")
    return data


def _n(v: Any, digits: int = 1) -> str:
    if v is None:
        return ""
    out = f"{v:.{digits}f}"
    return out[1:] if out.startswith("-") and float(out) == 0 else out


# ── silence ─────────────────────────────────────────────────────────────


def cmd_silence(a: Any) -> int:
    from _audio import levels, silence_ranges
    from _media import fmt_dur

    info, aud, pos, s, e = _load(a)
    span = _span(info, s, e)
    lv, fs = levels(info["file"], s or None, e, pos)
    thr = _threshold(a.threshold, lv)
    sil = silence_ranges(lv, fs, thr, a.min, offset=s)
    end_t = s + (len(lv) * fs if len(lv) else span)
    sounds: list[tuple[float, float]] = []
    cur = s
    for x, y in sil:
        if x - cur > 1e-6:
            sounds.append((cur, x))
        cur = y
    if end_t - cur > 1e-6:
        sounds.append((cur, end_t))
    ranges = sounds if a.sounds else sil
    kind = "sound" if a.sounds else "silence"
    items = [{"n": i + 1, "start": round(x, 3), "end": round(y, 3), "duration": round(y - x, 3)} for i, (x, y) in enumerate(ranges)]
    page = items[a.offset:][: a.limit] if a.limit else items[a.offset:]
    if a.format == "json":
        data = {"file": info["file"], "threshold_dbfs": round(thr, 1), "min_seconds": a.min, "kind": kind, "total": len(items), "offset": a.offset,
                "ranges": page, "total_silence": round(sum(y - x for x, y in sil), 3), "duration": span}
        if len(json.dumps(data)) > a.max_chars:
            emit_json_page(page, a.offset, len(items), a.max_chars)
        else:
            emit(data, "json")
        return 0
    head = (f"{len(ranges)} {kind} range(s) in {info['name']} ({fmt_dur(span)}), threshold {thr:.1f} dBFS, minimum {a.min:g} s; "
            f"silence totals {sum(y - x for x, y in sil):.1f} s." + ("\n\n| # | start | end | length |\n|---|---|---|---|" if ranges else ""))
    rows = [f"| {r['n']} | {_fmt(r['start'])} | {_fmt(r['end'])} | {r['duration']:.2f}s |" for r in page]
    print(page_text(head, rows, a.offset, len(items), a.max_chars, "ranges"))
    return 0


# ── speech ──────────────────────────────────────────────────────────────


def cmd_speech(a: Any) -> int:
    from _audio import speech_segments
    from _media import fmt_dur

    info, aud, pos, s, e = _load(a)
    span = _span(info, s, e)
    if not 0 < a.sensitivity < 1:
        raise UsageError("--sensitivity must be between 0 and 1")
    params = {"start": s, "end": e, "track": pos, "sens": a.sensitivity, "min_speech": a.min_speech, "min_silence": a.min_silence}

    def compute() -> list[list[float]]:
        return [[round(x, 3), round(y, 3)] for x, y in speech_segments(info["file"], a.sensitivity, a.min_speech, a.min_silence, 0.1, s or None, e, pos)]

    try:
        import _cache

        segs = _cache.cached_json(info["file"], "av-speech", params, "1", compute)
    except OSError:
        segs = compute()
    total = sum(y - x for x, y in segs)
    items = [{"n": i + 1, "start": x, "end": y, "duration": round(y - x, 3)} for i, (x, y) in enumerate(segs)]
    page = items[a.offset:][: a.limit] if a.limit else items[a.offset:]
    if a.format == "json":
        data = {"file": info["file"], "segments": page, "total": len(items), "offset": a.offset, "speech_seconds": round(total, 3), "duration": span,
                "speech_share": round(total / span, 3) if span else None}
        if len(json.dumps(data)) > a.max_chars:
            emit_json_page(page, a.offset, len(items), a.max_chars)
        else:
            emit(data, "json")
        return 0
    head = (f"{len(segs)} speech segment(s), {total:.1f} s of speech in {fmt_dur(span)} ({(100 * total / span) if span else 0:.0f}%)."
            + ("\n\n| # | start | end | length |\n|---|---|---|---|" if segs else ""))
    rows = [f"| {r['n']} | {_fmt(r['start'])} | {_fmt(r['end'])} | {r['duration']:.2f}s |" for r in page]
    print(page_text(head, rows, a.offset, len(items), a.max_chars, "segments"))
    return 0


if __name__ == "__main__":
    run_main(main)
