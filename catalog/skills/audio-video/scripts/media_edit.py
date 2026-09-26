#!/usr/bin/env python3
"""Edit audio and video: trim, cut, concatenate, speed, volume, loudness normalisation, fades, crop, scale, rotate,
flip, pad, text and image overlays, blur a region, subtitles (soft track or burned in), replace or mix audio, remux,
metadata, chapters and cover art. Several operations can run in one encode (pipeline)."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, emit, input_file, load_json_arg, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_edit.py trim talk.mp4 clip.mp4 --start 1:05 --end 1:47            # exact (re-encodes)
  python3 scripts/media_edit.py trim talk.mp4 clip.mp4 --start 1:05 --end 1:47 --fast     # keyframe cut, no re-encode
  python3 scripts/media_edit.py cut talk.mp4 short.mp4 --remove 0:00-0:12,14:30-15:10
  python3 scripts/media_edit.py concat intro.mp4 talk.mp4 outro.mp4 -o full.mp4 --crossfade 0.5
  python3 scripts/media_edit.py normalize podcast.wav podcast-norm.wav --preset podcast   # EBU R128, two-pass, -16 LUFS
  python3 scripts/media_edit.py crop talk.mp4 vertical.mp4 --aspect 9:16
  python3 scripts/media_edit.py text talk.mp4 titled.mp4 --text "Q3 review" --position top --start 0 --end 4 --box
  python3 scripts/media_edit.py overlay talk.mp4 logo.png branded.mp4 --position top-right --width 12% --opacity 0.8
  python3 scripts/media_edit.py subtitles talk.mp4 talk.en.srt captioned.mp4 --language eng          # soft track
  python3 scripts/media_edit.py subtitles talk.mp4 talk.en.srt burned.mp4 --burn --size 5%
  python3 scripts/media_edit.py mix-audio video.mp4 music.mp3 out.mp4 --volume 0.25 --duck --loop --fade-out 3
  python3 scripts/media_edit.py pipeline in.mp4 out.mp4 --ops '[{"op":"trim","start":5,"end":65},{"op":"crop","aspect":"1:1"},{"op":"fade","in":0.5,"out":1}]'
  python3 scripts/media_edit.py metadata song.mp3 tagged.mp3 --set title="Intro" --set artist=Desk --cover art.jpg

Times: 83.5, 1:23.5, 01:02:03.250, 25%, end, -10 (from the end). Ranges: 10-15, 1:20-1:25, 2:00-end, 30+5.
The input is never modified; existing outputs need --force. Run with --help after an operation for its options.
"""


def enc_opts(sp: Any) -> None:
    g = sp.add_argument_group("encoding (when streams must be re-encoded)")
    g.add_argument("--crf", type=float, help="video quality (default 20 for H.264, 24 for HEVC, 30 for VP9)")
    g.add_argument("--speed", choices=["fastest", "fast", "medium", "slow", "slowest"], default="medium", help="encoder speed vs size")
    g.add_argument("--vcodec", help="video codec (default: the source's when the container allows it)")
    g.add_argument("--acodec", help="audio codec (default: the source's when the container allows it)")
    g.add_argument("--video-bitrate", help="e.g. 4M instead of --crf")
    g.add_argument("--audio-bitrate", help="e.g. 192k")
    g.add_argument("--track", type=int, dest="audio_track", help="audio track to edit, 1-based (default: the main one)")
    g.add_argument("--reencode", action="store_true", help="re-encode even streams that could be copied")
    sp.add_argument("--force", action="store_true", help="overwrite an existing output")
    add_format(sp)


def main() -> int:
    p = parser("Edit audio and video files. Each operation writes a new file; untouched streams are copied, subtitles and "
               "chapters follow trims, cuts and speed changes. Use 'pipeline' to chain operations in one encode.", EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="OPERATION")
    fc = p.formatter_class

    def io(name: str, help_: str, extra_input: str | None = None) -> Any:
        sp = sub.add_parser(name, help=help_, formatter_class=fc, description=help_)
        sp.add_argument("input")
        if extra_input:
            sp.add_argument(extra_input)
        sp.add_argument("output")
        return sp

    sp = io("trim", "keep one range")
    sp.add_argument("--start", help="start time (default 0)")
    sp.add_argument("--end", help="end time (default the end)")
    sp.add_argument("--duration", help="length instead of --end")
    sp.add_argument("--fast", action="store_true", help="cut at keyframes without re-encoding (instant; start may be early)")
    enc_opts(sp)

    sp = io("cut", "remove ranges (or keep only some) and join the rest")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--remove", help="ranges to remove: 0-12,14:30-15:10")
    g.add_argument("--keep", help="ranges to keep instead: 0-10,20-30")
    sp.add_argument("--fast", action="store_true", help="cut at keyframes without re-encoding")
    enc_opts(sp)

    sp = sub.add_parser("concat", help="join files end to end", formatter_class=fc, description="Join files end to end. Same codecs and sizes are joined without re-encoding; otherwise they are normalised to the first file.")
    sp.add_argument("inputs", nargs="+")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--crossfade", type=float, help="cross-fade seconds between clips (re-encodes)")
    sp.add_argument("--transition", default="fade", help="transition for --crossfade: fade (a smooth cross-dissolve, default), fadeblack, fadewhite, wipeleft, "
                                                         "slideleft, circleopen, smoothleft, zoomin … (ffmpeg xfade names; 'dissolve' means the smooth cross-dissolve, "
                                                         "'pixel-dissolve' ffmpeg's grainy one)")
    sp.add_argument("--size", help="output size WxH (default the first video's)")
    sp.add_argument("--fps", type=float, help="output frame rate (default the first video's)")
    sp.add_argument("--chapters", action="store_true", help="add one chapter per input, titled with its file name")
    enc_opts(sp)

    sp = io("speed", "play faster or slower (audio keeps its pitch)")
    sp.add_argument("--factor", type=float, required=True, help="2 = twice as fast, 0.5 = half speed")
    sp.add_argument("--pitch", action="store_true", help="let the pitch change with the speed (like a tape)")
    enc_opts(sp)

    sp = io("fade", "fade in and/or out (picture and sound)")
    sp.add_argument("--in", dest="fade_in", type=float, help="fade-in seconds")
    sp.add_argument("--out", dest="fade_out", type=float, help="fade-out seconds")
    sp.add_argument("--color", default="black", help="fade colour (default black)")
    sp.add_argument("--only", choices=["audio", "video"], help="fade only one of them")
    enc_opts(sp)

    sp = io("volume", "change the volume (everywhere or in ranges)")
    sp.add_argument("--gain", required=True, help="6dB, -3dB, or a factor like 0.5")
    sp.add_argument("--ranges", help="only in these ranges: 10-12,30-31.5")
    enc_opts(sp)

    sp = io("mute", "silence everything, or ranges (optionally with a beep)")
    sp.add_argument("--ranges", help="ranges to silence (default: remove the audio track)")
    sp.add_argument("--beep", action="store_true", help="put a 1 kHz tone over the silenced ranges")
    enc_opts(sp)

    sp = io("normalize", "loudness normalisation, EBU R128 two-pass loudnorm")
    sp.add_argument("--preset", default="podcast", help="podcast/web -16 LUFS, streaming -14, broadcast -23, voice -19 (default podcast)")
    sp.add_argument("--target", type=float, help="integrated loudness in LUFS (overrides the preset)")
    sp.add_argument("--true-peak", type=float, help="true-peak ceiling in dBTP (default -1.5)")
    sp.add_argument("--lra", type=float, help="loudness range target in LU")
    enc_opts(sp)

    sp = io("denoise", "reduce steady background noise and rumble in speech")
    sp.add_argument("--strength", type=float, default=12, help="noise reduction in dB (default 12)")
    sp.add_argument("--highpass", type=float, default=80, help="cut rumble below this frequency (default 80 Hz)")
    enc_opts(sp)

    sp = io("crop", "crop to a box, an aspect ratio, or remove black borders")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--box", help="x,y,w,h in pixels or percentages")
    g.add_argument("--aspect", help="9:16, 1:1, 4:5, 16:9 … (largest centred box)")
    g.add_argument("--auto", action="store_true", help="detect and remove black borders")
    sp.add_argument("--anchor", default="center", help="with --aspect: center, top, bottom, left, right")
    enc_opts(sp)

    sp = io("scale", "resize")
    sp.add_argument("--size", required=True, help="1280x720, 1280x, x720, 720p, 50%%, fit:1280x720, fill:1080x1920")
    enc_opts(sp)

    sp = io("rotate", "rotate clockwise by 90/180/270 or any angle")
    sp.add_argument("--angle", type=float, required=True, help="degrees clockwise")
    sp.add_argument("--color", default="black", help="background for odd angles")
    sp.add_argument("--metadata-only", action="store_true", help="only change the rotation flag (instant, no re-encode; 90° steps)")
    enc_opts(sp)

    sp = io("flip", "mirror horizontally and/or vertically")
    sp.add_argument("--horizontal", action="store_true")
    sp.add_argument("--vertical", action="store_true")
    enc_opts(sp)

    sp = io("pad", "letterbox/pillarbox to an aspect ratio or size")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--aspect", help="16:9, 9:16, 1:1 …")
    g.add_argument("--size", help="WxH")
    sp.add_argument("--color", default="black", help="bar colour (default black)")
    sp.add_argument("--blur", action="store_true", help="fill the bars with a blurred copy of the picture")
    enc_opts(sp)

    sp = io("adjust", "brightness, contrast, saturation, gamma, grayscale, sharpen, denoise")
    for k, h in (("brightness", "-1..1 (0 = unchanged)"), ("contrast", "0..3 (1 = unchanged)"), ("saturation", "0..3 (1 = unchanged)"), ("gamma", "0.1..10 (1 = unchanged)"), ("sharpen", "amount, e.g. 1.0")):
        sp.add_argument(f"--{k}", type=float, help=h)
    sp.add_argument("--grayscale", action="store_true")
    sp.add_argument("--denoise", action="store_true", help="light video denoise (hqdn3d)")
    sp.add_argument("--start", help="only from this time")
    sp.add_argument("--end", help="only until this time")
    enc_opts(sp)

    sp = io("fps", "change the frame rate")
    sp.add_argument("--fps", type=float, required=True)
    enc_opts(sp)

    sp = io("blur", "blur a region (faces, plates, screens), optionally for a time range")
    sp.add_argument("--box", required=True, help="x,y,w,h in pixels or percentages")
    sp.add_argument("--strength", type=int, default=20, help="blur radius (default 20)")
    sp.add_argument("--start", help="from this time")
    sp.add_argument("--end", help="until this time")
    enc_opts(sp)

    sp = io("text", "draw text (title, caption, label) at a position and time range")
    sp.add_argument("--text", required=True, help="the text; \\n for a new line")
    sp.add_argument("--position", default="bottom", help="top, bottom, center, top-left … bottom-right, or x,y (px or %%)")
    sp.add_argument("--size", help="font size in px or %% of the height (default 6%%)")
    sp.add_argument("--color", default="white", help="text colour (default white)")
    sp.add_argument("--font", help="font file or name (default a bold system sans-serif)")
    sp.add_argument("--regular", action="store_true", help="regular weight instead of bold")
    sp.add_argument("--box", action="store_true", help="draw a translucent box behind the text")
    sp.add_argument("--box-color", default="black@0.55", help="box colour (default black@0.55)")
    sp.add_argument("--no-outline", action="store_true", help="no dark outline around the letters")
    sp.add_argument("--shadow", action="store_true")
    sp.add_argument("--align", default="center", choices=["left", "center", "right"])
    sp.add_argument("--margin", help="distance from the edge, px or %% (default 4%%)")
    sp.add_argument("--start", help="show from")
    sp.add_argument("--end", help="show until")
    sp.add_argument("--fade", type=float, help="fade the text in and out over this many seconds")
    enc_opts(sp)

    sp = io("overlay", "put an image (logo, watermark) over the video", extra_input="image")
    sp.add_argument("--position", default="bottom-right")
    sp.add_argument("--width", help="px or %% of the video width (default 15%%)")
    sp.add_argument("--opacity", type=float, default=1.0)
    sp.add_argument("--margin", help="px or %% (default 3%%)")
    sp.add_argument("--start")
    sp.add_argument("--end")
    enc_opts(sp)

    sp = io("subtitles", "add a subtitle track (soft) or burn subtitles into the picture", extra_input="subs")
    sp.add_argument("--burn", action="store_true", help="draw them into the picture (re-encodes)")
    sp.add_argument("--language", help="soft track: language code (eng, fra, deu …)")
    sp.add_argument("--title", help="soft track: track title")
    sp.add_argument("--default", action="store_true", help="soft track: show by default")
    sp.add_argument("--forced", action="store_true", help="soft track: mark as forced")
    sp.add_argument("--font", help="burn: font file or name")
    sp.add_argument("--size", help="burn: font size px or %% of the height")
    sp.add_argument("--color", help="burn: text colour")
    sp.add_argument("--box", action="store_true", help="burn: boxed style")
    sp.add_argument("--position", help="burn: bottom (default), top or center")
    sp.add_argument("--margin", help="burn: distance from the edge")
    sp.add_argument("--encoding", help="subtitle file encoding (default detected)")
    enc_opts(sp)

    sp = io("extract-audio", "save the audio track (copied when the format allows)")
    enc_opts(sp)

    sp = io("replace-audio", "swap the audio for another file", extra_input="audio")
    sp.add_argument("--offset", help="start the new audio this much later (negative: skip its start)")
    sp.add_argument("--loop", action="store_true", help="loop the new audio to fill the video")
    enc_opts(sp)

    sp = io("mix-audio", "mix another audio file (music, voice-over) under the existing sound", extra_input="audio")
    sp.add_argument("--volume", type=float, default=0.3, help="level of the added audio, 0-1+ (default 0.3)")
    sp.add_argument("--duck", action="store_true", help="lower the added audio while the original has sound (speech)")
    sp.add_argument("--loop", action="store_true", help="loop the added audio to fill the video")
    sp.add_argument("--offset", help="start the added audio at this time")
    sp.add_argument("--fade-out", type=float, help="fade the added audio out over the last N seconds")
    enc_opts(sp)

    sp = io("remux", "change the container without re-encoding (subtitles converted, audio re-encoded only if the container needs it)")
    sp.add_argument("--force", action="store_true")
    add_format(sp)

    sp = io("metadata", "set or clear tags, chapters and cover art (no re-encoding)")
    sp.add_argument("--set", action="append", default=[], help="KEY=VALUE (repeat); KEY= deletes a tag")
    sp.add_argument("--clear", action="store_true", help="drop all existing tags first")
    sp.add_argument("--chapters", help="chapters file: lines like '0:00 Intro' / '1:23:45 Q&A', JSON [{start, title}], or FFMETADATA")
    sp.add_argument("--clear-chapters", action="store_true")
    sp.add_argument("--cover", help="image to embed as cover art (mp3, m4a, mp4, flac, mkv)")
    sp.add_argument("--remove-cover", action="store_true")
    sp.add_argument("--force", action="store_true")
    add_format(sp)

    sp = sub.add_parser("pipeline", help="several operations in one encode (JSON list)", formatter_class=fc,
                        description="Run a list of operations in order, in one encode. Each item is {\"op\": NAME, ...options}. "
                                    "Names: trim, cut, keep, speed, fade, volume, mute, normalize, denoise, crop, scale, rotate, flip, pad, "
                                    "adjust, fps, blur, text, image, subtitles (burn). Options use the flag names with underscores "
                                    "(e.g. {\"op\":\"text\",\"text\":\"Hi\",\"box_color\":\"black@0.5\"}). Times refer to the timeline at that step.")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.add_argument("--ops", required=True, help="JSON list inline, a .json file, or - for stdin")
    enc_opts(sp)

    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], NEGATIVE_OK))
    t0 = time.monotonic()
    res = dispatch(a)
    res.setdefault("seconds", round(time.monotonic() - t0, 2))
    for kind in ("video", "audio"):  # a stream the output does not have was neither copied nor encoded
        if res.get("file") and not res.get("error") and not res.get(kind) and res.get(f"{kind}_action") not in (None, "none"):
            res[f"{kind}_action"] = "none"
    if a.format == "json":
        emit(res, "json")
    else:
        print(render(res))
    return 0


# ── dispatch ────────────────────────────────────────────────────────────

#: Options whose values may start with '-' (argparse would otherwise read them as options).
NEGATIVE_OK = ("--gain", "--offset", "--target", "--true-peak", "--brightness", "--angle", "--start", "--end")

PIPE_OPS = {"trim", "cut", "speed", "fade", "volume", "mute", "normalize", "denoise", "crop", "scale", "rotate", "flip", "pad", "adjust", "fps", "blur", "text", "overlay"}


def _eo(a: Any) -> dict[str, Any]:
    return {k: getattr(a, k, None) for k in ("crf", "speed", "vcodec", "acodec", "video_bitrate", "audio_bitrate", "audio_track", "reencode")}


def _io(a: Any, extra: list[str] | None = None) -> tuple[Path, Path]:
    src = input_file(a.input)
    out = output_path(a.output, [src, *(extra or [])], a.force)
    return src, out


def dispatch(a: Any) -> dict[str, Any]:
    from _edit import run_pipeline

    c = a.cmd
    if c == "trim" and a.fast:
        return fast_trim(a)
    if c == "cut" and a.fast:
        return fast_cut(a)
    if c == "rotate" and a.metadata_only:
        return rotate_flag(a)
    if c in PIPE_OPS:
        extra = [a.image] if c == "overlay" else []
        src, out = _io(a, extra)
        return run_pipeline(src, out, [op_from_args(a)], _eo(a), label=c)
    if c == "pipeline":
        src, out = _io(a)
        ops = load_json_arg(a.ops)
        if isinstance(ops, dict):
            ops = ops.get("ops") or [ops]
        if not isinstance(ops, list) or not all(isinstance(o, dict) and o.get("op") for o in ops):
            raise UsageError("--ops must be a JSON list of objects with an 'op' key")
        ops = [{**o, "op": "image" if str(o["op"]).lower() == "overlay" else o["op"]} for o in ops]
        return run_pipeline(src, out, ops, _eo(a), label="editing")
    if c == "subtitles":
        if a.burn:
            src, out = _io(a, [a.subs])
            op = {"op": "subtitles", "subtitles": a.subs, "font": a.font, "size": a.size, "color": a.color, "box": a.box, "position": a.position, "margin": a.margin, "encoding": a.encoding}
            return run_pipeline(src, out, [op], _eo(a), label="burning subtitles")
        return add_soft_subs(a)
    return {"concat": concat, "extract-audio": extract_audio, "replace-audio": replace_audio, "mix-audio": mix_audio, "remux": remux, "metadata": metadata}[c](a)


def op_from_args(a: Any) -> dict[str, Any]:
    c = a.cmd
    g = lambda k: getattr(a, k, None)  # noqa: E731
    if c == "trim":
        return {"op": "trim", "start": g("start"), "end": g("end"), "duration": g("duration")}
    if c == "cut":
        return {"op": "cut", "remove": g("remove")} if g("remove") else {"op": "keep", "ranges": g("keep")}
    if c == "speed":
        return {"op": "speed", "factor": g("factor"), "keep_pitch": not g("pitch")}
    if c == "fade":
        return {"op": "fade", "in": g("fade_in"), "out": g("fade_out"), "color": g("color"), "only": g("only")}
    if c == "volume":
        return {"op": "volume", "gain": g("gain"), "ranges": g("ranges")}
    if c == "mute":
        return {"op": "mute", "ranges": g("ranges"), "beep": g("beep")}
    if c == "normalize":
        return {"op": "normalize", "preset": g("preset"), "target": g("target"), "true_peak": g("true_peak"), "lra": g("lra")}
    if c == "denoise":
        return {"op": "denoise", "strength": g("strength"), "highpass": g("highpass")}
    if c == "crop":
        return {"op": "crop", "box": g("box"), "aspect": g("aspect"), "auto": g("auto"), "anchor": g("anchor")}
    if c == "scale":
        return {"op": "scale", "size": g("size")}
    if c == "rotate":
        return {"op": "rotate", "angle": g("angle"), "color": g("color")}
    if c == "flip":
        return {"op": "flip", "horizontal": g("horizontal"), "vertical": g("vertical")}
    if c == "pad":
        return {"op": "pad", "aspect": g("aspect"), "size": g("size"), "color": g("color"), "blur": g("blur")}
    if c == "adjust":
        return {"op": "adjust", **{k: g(k) for k in ("brightness", "contrast", "saturation", "gamma", "sharpen", "grayscale", "denoise", "start", "end")}}
    if c == "fps":
        return {"op": "fps", "fps": g("fps")}
    if c == "blur":
        return {"op": "blur", "box": g("box"), "strength": g("strength"), "start": g("start"), "end": g("end")}
    if c == "text":
        return {"op": "text", **{k: g(k) for k in ("text", "position", "size", "color", "font", "regular", "box", "box_color", "no_outline", "shadow", "align", "margin", "start", "end", "fade")}}
    if c == "overlay":
        return {"op": "image", "image": g("image"), **{k: g(k) for k in ("position", "width", "opacity", "margin", "start", "end")}}
    raise UsageError(f"unknown operation {c}")


# ── fast (stream copy) cuts ─────────────────────────────────────────────


def _keyframe_at_or_before(info: dict[str, Any], t: float) -> float | None:
    from _media import main_video, open_container
    from _video import _key_at

    v = main_video(info)
    if v is None or t <= 0:
        return 0.0 if t <= 0 else None
    c = open_container(info["file"])
    try:
        vs = c.streams[v["index"]]
        start = float(vs.start_time * vs.time_base) if vs.start_time is not None else 0.0
        _, kt = _key_at(c, vs, int((t + start) / vs.time_base), True, start, v.get("fps"))
        return kt
    except Exception:  # noqa: BLE001
        return None
    finally:
        c.close()


def fast_trim(a: Any) -> dict[str, Any]:
    from _media import TempOut, parse_time, probe, require_duration, run_ffmpeg, secs_arg, summarize_output
    from _plan import Options, plan_streams

    src, out = _io(a)
    info = probe(src)
    d = require_duration(info)
    s = parse_time(a.start, d) if a.start else 0.0
    e = parse_time(a.end, d) if a.end else (s + parse_time(a.duration, None, "duration") if a.duration else d)
    e = min(e, d)
    if e <= s:
        raise UsageError("the end must be after the start")
    plan = plan_streams(info, out, Options(copy=True), duration=e - s)
    k = _keyframe_at_or_before(info, s)
    with TempOut(out) as tmp:
        run_ffmpeg(["-ss", secs_arg(s), "-i", str(src), "-t", secs_arg(e - s), *plan.args, str(tmp)], duration=e - s, label="trimming")
    res = summarize_output(out)
    notes = list(plan.notes)
    if k is not None and s - k > 0.05:
        notes.append(f"stream copy starts at the keyframe at {k:.3f}s, {s - k:.2f}s before the requested start; players that honour edit lists start at {s:.3f}s. Drop --fast for a frame-exact cut")
    res.update({"input": str(src), "output": str(out), "operations": ["trim (fast)"], "notes": notes, "video_action": "copied", "audio_action": "copied", "expected_duration": round(e - s, 3)})
    return res


def fast_cut(a: Any) -> dict[str, Any]:
    from _media import TempOut, parse_time_ranges, probe, require_duration, run_ffmpeg, summarize_output
    from _plan import Options, plan_streams

    src, out = _io(a)
    info = probe(src)
    d = require_duration(info)
    if a.remove:
        rem = parse_time_ranges(a.remove, d)
        keep = []
        pos = 0.0
        for x, y in rem:
            if x - pos > 0.01:
                keep.append((pos, x))
            pos = max(pos, y)
        if d - pos > 0.01:
            keep.append((pos, d))
    else:
        keep = parse_time_ranges(a.keep, d)
    if not keep:
        raise UsageError("that removes everything")
    tmpd = Path(tempfile.mkdtemp(prefix="desk-cut-"))
    try:
        lst = tmpd / "parts.ffconcat"
        esc = str(src.resolve()).replace("'", "'\\''")
        lines = ["ffconcat version 1.0"]
        for x, y in keep:
            lines += [f"file '{esc}'", f"inpoint {x:.6f}", f"outpoint {y:.6f}"]
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
        plan = plan_streams(info, out, Options(copy=True, no_subs=True), duration=sum(y - x for x, y in keep))
        with TempOut(out) as tmp:
            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), *plan.args, str(tmp)], duration=sum(y - x for x, y in keep), label="cutting")
    finally:
        import shutil

        shutil.rmtree(tmpd, ignore_errors=True)
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["cut (fast)"], "video_action": "copied", "audio_action": "copied",
                "notes": ["cuts land on keyframes, so each kept piece may start a little early; drop --fast for exact cuts", *plan.notes], "expected_duration": round(sum(y - x for x, y in keep), 3)})
    return res


def rotate_flag(a: Any) -> dict[str, Any]:
    from _media import TempOut, main_video, probe, run_ffmpeg, summarize_output
    from _plan import Options, plan_streams

    src, out = _io(a)
    info = probe(src)
    v = main_video(info)
    if v is None:
        raise SkillError("no video stream")
    ang = a.angle % 360
    if ang not in (0, 90, 180, 270):
        raise UsageError("--metadata-only works in 90° steps")
    cur = int(v.get("rotation") or 0)  # counter-clockwise, as ffmpeg reports it
    new = (cur - ang) % 360
    if new > 180:
        new -= 360
    plan = plan_streams(info, out, Options(copy=True))
    with TempOut(out) as tmp:
        run_ffmpeg(["-display_rotation", str(new), "-i", str(src), *plan.args, str(tmp)], label="rotating", progress=False)
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["rotate (flag)"], "video_action": "copied", "audio_action": "copied",
                "notes": [f"rotation flag set to {new}° (players rotate on playback; some web players ignore the flag)"]})
    return res


# ── concat ──────────────────────────────────────────────────────────────


def _params(info: dict[str, Any]) -> tuple[Any, ...]:
    from _media import main_audio, main_video

    v = main_video(info) or {}
    au = main_audio(info) or {}
    return (v.get("codec"), v.get("width"), v.get("height"), v.get("pix_fmt"), round(float(v.get("fps") or 0), 2), v.get("rotation"),
            au.get("codec"), au.get("sample_rate"), au.get("channels"), bool(main_video(info)), bool(main_audio(info)))


def concat(a: Any) -> dict[str, Any]:
    from _edit import encode_args
    from _media import (TempOut, can_copy, container_for, display_size, has_filter, main_audio, main_video, probe, require_duration, run_ffmpeg,
                        summarize_output)

    t0 = time.monotonic()
    if len(a.inputs) < 2:
        raise UsageError("concat needs at least two inputs")
    srcs = [input_file(x) for x in a.inputs]
    out = output_path(a.output, srcs, a.force)
    infos = [probe(s) for s in srcs]
    durs = [require_duration(i) for i in infos]
    cont = container_for(out)
    first_v = next((main_video(i) for i in infos if main_video(i)), None)
    any_a = any(main_audio(i) for i in infos)
    want_v = first_v is not None and not cont.get("audio_only")
    same = len({_params(i) for i in infos}) == 1
    v0 = main_video(infos[0])
    a0 = main_audio(infos[0])
    copyable = (not want_v or can_copy(cont, "video", (v0 or {}).get("codec"))) and (not a0 or can_copy(cont, "audio", a0.get("codec")))
    notes: list[str] = []
    tmpd = Path(tempfile.mkdtemp(prefix="desk-concat-"))
    try:
        if same and copyable and not a.crossfade and not a.size and not a.fps and not a.reencode:
            lst = tmpd / "list.ffconcat"
            lines = ["ffconcat version 1.0"] + [f"file '{str(s.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for s in srcs]
            lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
            maps = (["-map", "0:v:0?"] if want_v else []) + (["-map", "0:a:0?"] if any_a else [])
            extra = ["-movflags", "+faststart"] if cont.get("faststart") else []
            chap_in, chap_opt = _concat_chapters(a, srcs, durs, tmpd, 1)
            with TempOut(out) as tmp:
                run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), *chap_in, *maps, "-c", "copy", *chap_opt, *extra, str(tmp)], duration=sum(durs), label="joining")
            action = "copied (same formats, no re-encode)"
        else:
            if want_v:
                if a.size:
                    from _edit import scaled_size

                    W, H, _ = scaled_size(a.size, *display_size(first_v))
                else:
                    W, H = display_size(first_v)
                W, H = W - W % 2, H - H % 2
                F = float(a.fps or first_v.get("fps") or 30)
                if F > 120:
                    F = 60.0
            inputs: list[str] = []
            chains: list[str] = []
            vl: list[str] = []
            al: list[str] = []
            for k, (s, info, d) in enumerate(zip(srcs, infos, durs)):
                inputs += ["-i", str(s)]
                v = main_video(info)
                au = main_audio(info)
                if want_v:
                    if v:
                        vpos = [x for x in info["streams"] if x["type"] == "video"].index(v)
                        chains.append(f"[{k}:v:{vpos}]fps={F:g},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:({W}-iw)/2:({H}-ih)/2,setsar=1,format=yuv420p,settb=AVTB[v{k}]")
                    else:
                        chains.append(f"color=c=black:s={W}x{H}:r={F:g}:d={d:.3f},format=yuv420p,settb=AVTB[v{k}]")
                    vl.append(f"[v{k}]")
                if any_a:
                    if au:
                        apos = [x for x in info["streams"] if x["type"] == "audio"].index(au)
                        chains.append(f"[{k}:a:{apos}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={d:.3f}[a{k}]" if want_v else f"[{k}:a:{apos}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
                    else:
                        chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.3f}[a{k}]")
                    al.append(f"[a{k}]")
            X = float(a.crossfade or 0)
            if X:
                trans = _transition(a.transition, notes)
                if X >= min(durs) / 2:
                    raise UsageError("--crossfade must be shorter than half the shortest clip")
                if want_v and not has_filter("xfade"):
                    raise SkillError("this ffmpeg has no xfade filter; join without --crossfade")
                acc = durs[0]
                cur_v, cur_a = (vl[0] if vl else None), (al[0] if al else None)
                for k in range(1, len(srcs)):
                    off = acc - X
                    if want_v:
                        chains.append(f"{cur_v}{vl[k]}xfade=transition={trans}:duration={X:g}:offset={off:.3f}[xv{k}]")
                        cur_v = f"[xv{k}]"
                    if any_a:
                        chains.append(f"{cur_a}{al[k]}acrossfade=d={X:g}[xa{k}]")
                        cur_a = f"[xa{k}]"
                    acc = acc + durs[k] - X
                vout, aout = cur_v, cur_a
                total = acc
            else:
                pads = "".join(f"{vl[k] if want_v else ''}{al[k] if any_a else ''}" for k in range(len(srcs)))
                vout = "[vout]" if want_v else None
                aout = "[aout]" if any_a else None
                chains.append(f"{pads}concat=n={len(srcs)}:v={1 if want_v else 0}:a={1 if any_a else 0}{vout or ''}{aout or ''}")
                total = sum(durs)
            eo = {**_eo(a), "_channels": 2, "_mixed": True}  # audio was fitted to 48 kHz stereo from several sources
            ref = infos[0] if main_video(infos[0]) or not want_v else next(i for i in infos if main_video(i))
            enc, _ = encode_args(ref, out, want_v, any_a, eo, vout, aout)
            starts_durs = durs if not X else [d - (X if k < len(durs) - 1 else 0) for k, d in enumerate(durs)]
            chap_in, chap_opt = _concat_chapters(a, srcs, starts_durs, tmpd, len(srcs))
            with TempOut(out) as tmp:
                run_ffmpeg([*inputs, *chap_in, "-filter_complex", ";".join(chains), *enc, *chap_opt, str(tmp)], duration=total, label="joining")
            action = "re-encoded to a common format" + (f" with {X:g}s {trans} transitions" if X else "")
            if not same:
                notes.append(f"inputs differed, so all were fitted to {W}x{H} at {F:g} fps" if want_v else "inputs differed, so the audio was resampled to 48 kHz stereo")
    finally:
        import shutil

        shutil.rmtree(tmpd, ignore_errors=True)
    res = summarize_output(out)
    res.update({"inputs": [str(s) for s in srcs], "output": str(out), "operations": ["concat"], "video_action": action if want_v else "none",
                "audio_action": action if any_a else "none", "notes": notes, "seconds": round(time.monotonic() - t0, 2)})
    return res


XFADE = {"fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown", "circlecrop", "rectcrop", "distance",
         "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright", "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose",
         "horzopen", "horzclose", "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice", "hblur",
         "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast", "fadeslow", "hlwind", "hrwind", "vuwind",
         "vdwind", "coverleft", "coverright", "coverup", "coverdown", "revealleft", "revealright", "revealup", "revealdown"}
_TRANSITION_ALIASES = {"dissolve": "fade", "cross-dissolve": "fade", "crossdissolve": "fade", "crossfade": "fade", "cross-fade": "fade", "mix": "fade",
                       "pixel-dissolve": "dissolve", "noise": "dissolve", "black": "fadeblack", "dip-to-black": "fadeblack", "white": "fadewhite"}


def _transition(name: str, notes: list[str]) -> str:
    """The xfade transition for a name: editors' words (dissolve = a smooth cross-dissolve) mapped to ffmpeg's."""
    n = (name or "fade").strip().lower()
    t = _TRANSITION_ALIASES.get(n, n)
    if t not in XFADE:
        raise UsageError(f"unknown transition '{name}'; use fade, fadeblack, fadewhite, wipeleft, slideleft, circleopen, smoothleft, zoomin … "
                         f"(any of: {', '.join(sorted(XFADE))})")
    if n == "dissolve":
        notes.append("'dissolve' was done as a smooth cross-dissolve (xfade fade); ffmpeg's own grainy dissolve is --transition pixel-dissolve")
    return t


def _concat_chapters(a: Any, srcs: list[Path], durs: list[float], tmpd: Path, index: int) -> tuple[list[str], list[str]]:
    """With --chapters, one chapter per input (an FFMETADATA input at `index`); otherwise no chapters at all."""
    import re

    if not a.chapters:
        return [], ["-map_chapters", "-1", "-map_metadata", "-1"]
    lines = [";FFMETADATA1"]
    t = 0.0
    for s, d in zip(srcs, durs):
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t * 1000)}", f"END={int((t + d) * 1000)}", "title=" + re.sub(r"([=;#\\])", r"\\\1", s.stem)]
        t += d
    f = tmpd / "chapters.txt"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ["-i", str(f)], ["-map_chapters", str(index), "-map_metadata", str(index)]


# ── audio tracks ────────────────────────────────────────────────────────


def extract_audio(a: Any) -> dict[str, Any]:
    from _media import TempOut, container_for, probe, run_ffmpeg, summarize_output
    from _plan import Options, plan_streams

    src, out = _io(a)
    info = probe(src)
    cont = container_for(out)
    if not cont.get("audio_only"):
        raise UsageError(f"use an audio extension for the output (.m4a .mp3 .wav .flac .opus .ogg …), not {out.suffix}")
    plan = plan_streams(info, out, Options(no_video=True, audio_track=a.audio_track, acodec=a.acodec, abitrate=a.audio_bitrate, reencode=a.reencode))
    with TempOut(out) as tmp:
        run_ffmpeg(["-i", str(src), *plan.args, str(tmp)], duration=info.get("duration"), label="extracting audio")
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["extract-audio"], "audio_action": "copied" if plan.audio == "copy" else f"encoded ({plan.audio})", "video_action": "none", "notes": plan.notes})
    return res


def replace_audio(a: Any) -> dict[str, Any]:
    from _edit import encode_args
    from _media import TempOut, brand_cleanup, can_copy, container_for, main_video, parse_time, probe, require_duration, run_ffmpeg, secs_arg, summarize_output

    src, out = _io(a, [a.audio])
    aud = input_file(a.audio)
    info = probe(src)
    ainfo = probe(aud, side_data=False)
    v = main_video(info)
    if v is None:
        raise SkillError(f"{info['name']} has no video")
    if not any(s["type"] == "audio" for s in ainfo["streams"]):
        raise SkillError(f"{ainfo['name']} has no audio")
    D = require_duration(info)
    ad = ainfo.get("duration") or 0.0
    off = 0.0
    if a.offset:
        s = a.offset.strip()
        off = -parse_time(s[1:], None, "offset") if s.startswith("-") else parse_time(s.lstrip("+"), None, "offset")
    cont = container_for(out)
    vpos = [s for s in info["streams"] if s["type"] == "video"].index(v)
    ain: list[str] = []
    if a.loop:
        ain += ["-stream_loop", "-1"]
    if off < 0:
        ain += ["-ss", secs_arg(-off)]
    ain += ["-i", str(aud)]
    filters = []
    if off > 0:
        filters.append(f"adelay=delays={int(off * 1000)}:all=1")
    covered = (ad - max(0.0, -off) + max(0.0, off)) if not a.loop else float("inf")
    if covered < D - 0.05:
        filters.append(f"apad=whole_dur={D:.3f}")
    args = ["-i", str(src), *ain]
    vcopy = can_copy(cont, "video", v.get("codec")) and not a.reencode
    if vcopy:
        args += ["-map", f"0:v:{vpos}", "-c:v", "copy"]
    notes = []
    from _media import main_audio

    na = main_audio(ainfo) or {}
    acopy = not filters and can_copy(cont, "audio", na.get("codec")) and not a.reencode and not a.acodec
    if filters:
        args += ["-filter_complex", f"[1:a:0]{','.join(filters)}[na]"]
        alabel = "[na]"
    else:
        alabel = "1:a:0"
    if acopy:
        args += ["-map", alabel, "-c:a", "copy"]
        enc, _ = encode_args(info, out, not vcopy, False, _eo(a), None if vcopy else f"0:v:{vpos}", None)
    else:
        enc, _ = encode_args(ainfo if na.get("codec") else info, out, False, True, _eo(a), None, alabel)
        if not vcopy:
            enc2, _ = encode_args(info, out, True, False, _eo(a), f"0:v:{vpos}", None)
            enc = enc2 + enc
    args += enc + ["-map_metadata", "0", *brand_cleanup(out), "-t", secs_arg(D)]
    if cont.get("faststart") and "-movflags" not in args:
        args += ["-movflags", "+faststart"]
    with TempOut(out) as tmp:
        run_ffmpeg([*args, str(tmp)], duration=D, label="replacing audio")
    if covered < D - 0.05:
        notes.append(f"the new audio is shorter than the video; the last {D - covered:.1f}s are silent (use --loop to repeat it)")
    elif ad and not a.loop and ad > D + 0.5:
        notes.append(f"the new audio was cut at the end of the video ({D:.1f}s)")
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["replace-audio"], "video_action": "copied" if vcopy else "re-encoded", "audio_action": "copied" if acopy else "encoded", "notes": notes})
    return res


def mix_audio(a: Any) -> dict[str, Any]:
    from _edit import encode_args
    from _media import (TempOut, brand_cleanup, can_copy, container_for, main_audio, main_video, parse_time, probe, require_duration, run_ffmpeg, secs_arg,
                        summarize_output)

    src, out = _io(a, [a.audio])
    aud = input_file(a.audio)
    info = probe(src)
    ainfo = probe(aud, side_data=False)
    if not main_audio(ainfo):
        raise SkillError(f"{ainfo['name']} has no audio")
    D = require_duration(info)
    v = main_video(info)
    base = main_audio(info)
    cont = container_for(out)
    off = parse_time(a.offset, D) if a.offset else 0.0
    mf = [f"volume={a.volume:g}"]
    if off > 0:
        mf.append(f"adelay=delays={int(off * 1000)}:all=1")
    if a.fade_out:
        mf.append(f"afade=t=out:st={max(0.0, D - a.fade_out):.3f}:d={a.fade_out:g}")
    mf.append(f"atrim=duration={D:.3f}")
    chains = [f"[1:a:0]aresample=48000,aformat=channel_layouts=stereo,{','.join(mf)}[m]"]
    if base:
        apos = [s for s in info["streams"] if s["type"] == "audio"].index(base)
        chains.append(f"[0:a:{apos}]aresample=48000,aformat=channel_layouts=stereo[o]")
        if a.duck:
            chains.append("[o]asplit[o1][sc]")
            chains.append("[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400:makeup=1[md]")
            chains.append("[o1][md]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            chains.append("[o][m]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        chains.append("[m]anull[mix]")
    ain = (["-stream_loop", "-1"] if a.loop else []) + ["-i", str(aud)]
    args = ["-i", str(src), *ain, "-filter_complex", ";".join(chains)]
    vcopy = False
    eo = {**_eo(a), "_channels": 2, "_mixed": True}  # the mix is 48 kHz stereo
    if v and not cont.get("audio_only"):
        vpos = [s for s in info["streams"] if s["type"] == "video"].index(v)
        vcopy = can_copy(cont, "video", v.get("codec")) and not a.reencode
        if vcopy:
            args += ["-map", f"0:v:{vpos}", "-c:v", "copy"]
            enc, _ = encode_args(info, out, False, True, eo, None, "[mix]")
        else:
            enc, _ = encode_args(info, out, True, True, eo, f"0:v:{vpos}", "[mix]")
    else:
        enc, _ = encode_args(info, out, False, True, eo, None, "[mix]")
    args += enc + ["-map_metadata", "0", *brand_cleanup(out), "-t", secs_arg(D)]
    with TempOut(out) as tmp:
        run_ffmpeg([*args, str(tmp)], duration=D, label="mixing audio")
    notes = []
    if ainfo.get("duration") and ainfo["duration"] + off < D - 0.5 and not a.loop:
        notes.append(f"the added audio ends at {ainfo['duration'] + off:.1f}s, before the video ({D:.1f}s); --loop repeats it")
    if a.duck:
        notes.append("the added audio ducks under the original sound")
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["mix-audio"], "video_action": ("copied" if vcopy else "re-encoded") if v else "none", "audio_action": "mixed", "notes": notes})
    return res


# ── subtitles, remux, metadata ──────────────────────────────────────────


def add_soft_subs(a: Any) -> dict[str, Any]:
    from _media import TempOut, container_for, probe, run_ffmpeg, summarize_output
    from _plan import Options, plan_streams
    from _subs import load, save

    src, out = _io(a, [a.subs])
    subs = input_file(a.subs)
    info = probe(src)
    cont = container_for(out)
    if not cont.get("s"):
        raise SkillError(f"{out.suffix} cannot hold subtitle tracks; use .mp4, .mov, .mkv or .webm (or --burn)")
    doc = load(subs, a.encoding)
    if not doc.cues:
        raise SkillError(f"{subs.name} has no cues")
    tmpd = Path(tempfile.mkdtemp(prefix="desk-subs-"))
    try:
        # Hand ffmpeg a clean UTF-8 file in a format it reads (ASS keeps its styling in MKV).
        keep_ass = doc.fmt == "ass" and cont.get("s") == "copy"
        clean = tmpd / ("in.ass" if keep_ass else "in.srt")
        if keep_ass:
            clean.write_bytes(subs.read_bytes())
        else:
            save(doc, clean, "srt")
        plan = plan_streams(info, out, Options(reencode=a.reencode, prefer_copy=True))
        n_existing = sum(1 for i in range(len(plan.args) - 1) if plan.args[i] == "-map" and ":s:" in plan.args[i + 1])
        k = n_existing
        codec = cont["s"] if cont["s"] != "copy" else ("ass" if keep_ass else "srt")
        args = ["-i", str(src), "-i", str(clean), *plan.args, "-map", "1:0", f"-c:s:{k}", codec]
        if a.language:
            args += [f"-metadata:s:s:{k}", f"language={a.language}"]
        if a.title:
            args += [f"-metadata:s:s:{k}", f"title={a.title}"]
        disp = [x for x, on in (("default", a.default), ("forced", a.forced)) if on]
        if disp:
            args += [f"-disposition:s:{k}", "+".join(disp)]
        with TempOut(out) as tmp:
            run_ffmpeg([*args, str(tmp)], duration=info.get("duration"), label="adding subtitles")
    finally:
        import shutil

        shutil.rmtree(tmpd, ignore_errors=True)
    notes = list(plan.notes)
    if out.suffix.lower() in (".mp4", ".m4v", ".mov"):
        notes.append("MP4 subtitles are mov_text: styling is dropped, and many web players ignore soft tracks (use --burn to be sure they show)")
    if info.get("duration") and doc.cues and doc.cues[-1].end > info["duration"] + 1:
        notes.append(f"some cues run past the end of the video ({doc.cues[-1].end:.1f}s > {info['duration']:.1f}s)")
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["subtitles (soft track)"], "video_action": "copied" if plan.video == "copy" else str(plan.video),
                "audio_action": "copied" if plan.audio == "copy" else str(plan.audio), "notes": notes, "cues": len(doc.cues)})
    return res


def remux(a: Any) -> dict[str, Any]:
    from _media import TempOut, main_audio, main_video, probe, run_ffmpeg, summarize_output
    from _plan import Options, plan_streams

    src, out = _io(a)
    info = probe(src)
    plan = plan_streams(info, out, Options(prefer_copy=True))
    if plan.video not in (None, "copy"):
        v = main_video(info) or {}
        raise SkillError(f"{v.get('codec')} video cannot go into {out.suffix} as-is; use media_convert.py to re-encode it (or remux to .mkv)")
    with TempOut(out) as tmp:
        run_ffmpeg(["-i", str(src), *plan.args, str(tmp)], duration=info.get("duration"), label="remuxing")
    au = main_audio(info) or {}
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["remux"], "video_action": "copied" if plan.video else "none",
                "audio_action": ("copied" if plan.audio == "copy" else f"re-encoded ({au.get('codec')} → {plan.audio}) because {out.suffix} cannot hold {au.get('codec')}") if plan.audio else "none",
                "notes": plan.notes})
    return res


def parse_chapters(path: Path, duration: float | None) -> list[tuple[float, float, str]]:
    import json
    import re

    from _media import parse_time

    text = path.read_text(encoding="utf-8-sig")
    items: list[tuple[float, str]] = []
    if text.lstrip().startswith(";FFMETADATA"):
        raise ValueError("ffmetadata")
    if text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("chapters", [])
        out = []
        for i, c in enumerate(data):
            s = parse_time(c["start"], duration)
            e = parse_time(c["end"], duration) if c.get("end") is not None else None
            out.append((s, e, str(c.get("title") or f"Chapter {i + 1}")))
        items2 = sorted(out, key=lambda x: x[0])
        res = []
        for i, (s, e, t) in enumerate(items2):
            nxt = items2[i + 1][0] if i + 1 < len(items2) else (duration or s + 1)
            res.append((s, e if e is not None else nxt, t))
        return res
    for line in text.splitlines():
        m = re.match(r"\s*\[?((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\]?\s*[-–—:]?\s*(.*)", line)
        if m:
            items.append((parse_time(m.group(1), duration), m.group(2).strip() or "Chapter"))
    if not items:
        raise UsageError(f"{path.name}: no chapters found (lines like '0:00 Intro' or '1:02:03 Q&A')")
    items.sort()
    res = []
    for i, (s, t) in enumerate(items):
        e = items[i + 1][0] if i + 1 < len(items) else (duration or s + 1)
        res.append((s, e, t))
    return res


def metadata(a: Any) -> dict[str, Any]:
    import mimetypes
    import re

    from _media import TempOut, brand_cleanup, container_for, probe, run_ffmpeg, summarize_output

    src, out = _io(a, [x for x in (a.chapters, a.cover) if x])
    info = probe(src)
    cont = container_for(out)
    if out.suffix.lower() != src.suffix.lower() and not (out.suffix.lower() in (".mp4", ".m4v", ".mov", ".m4a") and src.suffix.lower() in (".mp4", ".m4v", ".mov", ".m4a")):
        raise UsageError("metadata keeps the container: give the output the same extension as the input (use remux to change it)")
    tmpd = Path(tempfile.mkdtemp(prefix="desk-meta-"))
    notes: list[str] = []
    try:
        inputs = ["-i", str(src)]
        maps: list[str] = []
        covers = [s for s in info["streams"] if s["type"] == "video" and s.get("cover_art")]
        vids = [s for s in info["streams"] if s["type"] == "video"]
        for k, s in enumerate(vids):
            if s.get("cover_art") and (a.remove_cover or a.cover):
                continue
            maps += ["-map", f"0:v:{k}"]
        maps += ["-map", "0:a?", "-map", "0:s?"]
        if cont.get("muxer") == "matroska":
            maps += ["-map", "0:t?"]
        opts = ["-c", "copy"]
        next_in = 1
        if a.clear:
            # An empty metadata input clears the tags but, unlike -map_metadata -1, keeps chapter titles.
            empty = tmpd / "empty.txt"
            empty.write_text(";FFMETADATA1\n", encoding="utf-8")
            inputs += ["-i", str(empty)]
            opts += ["-map_metadata", str(next_in)]
            next_in += 1
        else:
            opts += ["-map_metadata", "0"]
        if a.chapters:
            cp = input_file(a.chapters)
            try:
                chs = parse_chapters(cp, info.get("duration"))
                meta = tmpd / "chapters.txt"
                lines = [";FFMETADATA1"]
                for s, e, t in chs:
                    lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(s * 1000)}", f"END={int(e * 1000)}", "title=" + re.sub(r"([=;#\\])", r"\\\1", t)]
                meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except ValueError:
                meta = cp
                chs = []
            inputs += ["-i", str(meta)]
            opts += ["-map_chapters", str(next_in)]
            next_in += 1
            notes.append(f"{len(chs)} chapter(s) written" if chs else "chapters taken from the FFMETADATA file")
        elif a.clear_chapters:
            opts += ["-map_chapters", "-1"]
        if a.cover:
            cv = input_file(a.cover)
            mime = mimetypes.guess_type(cv.name)[0] or "image/jpeg"
            if cont.get("muxer") == "matroska":
                opts += ["-attach", str(cv), "-metadata:s:t:0", f"mimetype={mime}", "-metadata:s:t:0", "filename=cover" + cv.suffix.lower()]
            elif out.suffix.lower() in (".mp3", ".m4a", ".m4b", ".mp4", ".m4v", ".mov", ".flac"):
                inputs += ["-i", str(cv)]
                n_v = len([m for m in maps if m.startswith("0:v:")])
                maps += ["-map", f"{next_in}:0"]
                opts += [f"-c:v:{n_v}", "mjpeg" if mime == "image/jpeg" and cv.suffix.lower() not in (".jpg", ".jpeg") else "copy", f"-disposition:v:{n_v}", "attached_pic"]
                next_in += 1
                if out.suffix.lower() == ".mp3":
                    opts += ["-id3v2_version", "3"]
            else:
                raise SkillError(f"cover art is not supported in {out.suffix} by ffmpeg (use mp3, m4a, mp4, flac or mkv)")
        opts += brand_cleanup(out)  # MP4 brand fields that an earlier conversion turned into ordinary tags
        for kv in a.set:
            if "=" not in kv:
                raise UsageError(f"--set needs KEY=VALUE, got '{kv}'")
            k, v = kv.split("=", 1)
            opts += ["-metadata", f"{k.strip()}={v}"]
        if cont.get("faststart"):
            opts += ["-movflags", "+faststart"]
        if out.suffix.lower() in (".mp4", ".m4v", ".mov", ".m4a"):
            opts += ["-movflags", "+use_metadata_tags"] if any(k.split("=", 1)[0].strip().lower() not in _MP4_TAGS for k in a.set) else []
        with TempOut(out) as tmp:
            run_ffmpeg([*inputs, *maps, *opts, str(tmp)], label="writing metadata", progress=False)
    finally:
        import shutil

        shutil.rmtree(tmpd, ignore_errors=True)
    after = probe(out, side_data=False)
    res = summarize_output(out)
    res.update({"input": str(src), "output": str(out), "operations": ["metadata"], "video_action": "copied", "audio_action": "copied", "notes": notes,
                "tags": after.get("tags"), "chapters": after.get("chapters"), "cover_art": any(s.get("cover_art") for s in after["streams"]) or any(s["type"] == "attachment" for s in after["streams"])})
    return res


_MP4_TAGS = {"title", "artist", "album_artist", "album", "date", "comment", "genre", "copyright", "composer", "track", "disc", "description", "synopsis", "show", "episode_id", "network", "lyrics", "grouping", "encoder", "year", "author"}


def render(r: dict[str, Any]) -> str:
    from _media import describe_output, fmt_dur

    lines = [f"wrote {describe_output(r)}"]
    ops = ", ".join(r.get("operations", []))
    acts = []
    if r.get("video_action") and r["video_action"] != "none":
        acts.append(f"video {r['video_action']}")
    if r.get("audio_action") and r["audio_action"] != "none":
        acts.append(f"audio {r['audio_action']}")
    lines.append(f"{ops}: " + "; ".join(acts) + f" ({r.get('seconds', 0):.1f}s)")
    exp = r.get("expected_duration")
    if exp and r.get("duration") and abs(r["duration"] - exp) > max(0.5, 0.02 * exp):
        lines.append(f"check: the output lasts {fmt_dur(r['duration'])}, expected about {fmt_dur(exp)}")
    if r.get("tags"):
        lines.append("tags: " + ", ".join(f"{k}={v}" for k, v in list(r["tags"].items())[:12]))
    if r.get("chapters"):
        lines.append(f"chapters: {len(r['chapters'])}")
    for n in r.get("notes", []):
        lines.append(f"note: {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
