#!/usr/bin/env python3
"""Convert audio and video between formats with presets (web MP4, WebM, HEVC, AV1, email-sized, GIF, MP3, M4A, Opus,
WAV, FLAC …) or explicit codec, quality, size and rate options. Streams that already fit are copied, not re-encoded."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, cap, emit, md_table, output_dir, output_path, parser, pool_map, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_convert.py talk.mov talk.mp4                       # H.264/AAC MP4 (streams that fit are copied)
  python3 scripts/media_convert.py talk.mov talk.mp4 --preset web          # web-safe: within 1920x1080, 60 fps max, fast start
  python3 scripts/media_convert.py talk.mov small.mp4 --preset email       # ~20 MB max, within 1280x720, for attachments
  python3 scripts/media_convert.py talk.mov talk.mp4 --target-mb 8         # two-pass encode under 8 MB (size and fps lowered to fit)
  python3 scripts/media_convert.py clip.mp4 clip.webm --crf 30 --size 720p
  python3 scripts/media_convert.py clip.mp4 clip.gif --start 5 --end 9 --size 480x --fps 12
  python3 scripts/media_convert.py song.flac song.mp3                       # VBR ~190 kb/s, tags and cover kept
  python3 scripts/media_convert.py "recordings/*.wav" --to mp3 --out-dir mp3/   # batch, in parallel
  python3 scripts/media_convert.py memos/ -r --to m4a --out-dir m4a/        # a folder tree; sub-folders kept in --out-dir
  python3 scripts/media_convert.py talk.mp4 talk.m4a                        # audio only (copied when already AAC)
  python3 scripts/media_convert.py --presets                                # list the presets

Output format comes from the extension (.mp4 .mov .mkv .webm .avi .wmv .mpg .ts .gif .mp3 .m4a .aac .opus .ogg .wav
.flac .aiff .wma). The input is never modified; existing outputs need --force. Sizes are decimal: 1 MB = 1,000,000 bytes.
In a batch, inputs from different folders keep their sub-folders under --out-dir, and two inputs that would get the same
output name get -2, -3 … instead of overwriting each other.
"""


def main() -> int:
    p = parser("Convert audio and video between formats, with presets or explicit codec, quality (CRF or bitrate), "
               "size, frame rate, channels and sample rate; --target-mb sizes the output. Keeps metadata, chapters, "
               "subtitles (converted to what the container supports), cover art and rotation where possible.", EPILOG)
    p.add_argument("paths", nargs="*", help="INPUT OUTPUT, or several inputs with --out-dir and --to/--preset")
    p.add_argument("--presets", action="store_true", help="list the presets and exit")
    p.add_argument("--preset", "-p", help="web, webm, hevc, av1, email, edit, gif, mp3, m4a, opus, ogg, wav, flac, aiff, alac, wma, voice")
    p.add_argument("--out-dir", help="batch mode: folder for the outputs")
    p.add_argument("--to", help="batch mode: output extension (mp3, mp4, …) when no preset gives one")
    p.add_argument("--recursive", "-r", action="store_true", help="batch mode: convert the media files in sub-folders too (the tree is kept under --out-dir)")
    g = p.add_argument_group("video")
    g.add_argument("--vcodec", help="h264, hevc, vp9, av1, mpeg4, prores, gif … or copy")
    g.add_argument("--crf", type=float, help="constant quality (lower = better/larger; h264 ~18-28, vp9 ~24-40)")
    g.add_argument("--video-bitrate", help="e.g. 2500k or 4M (instead of --crf)")
    g.add_argument("--size", help="1280x720, 1280x (keep aspect), x720, 720p, 50%%, fit:1280x720, fill:1080x1920")
    g.add_argument("--fps", type=float, help="frame rate")
    g.add_argument("--speed", default="medium", choices=["fastest", "fast", "medium", "slow", "slowest"], help="encoder speed vs size (default medium)")
    g.add_argument("--no-video", action="store_true", help="drop video")
    g.add_argument("--tonemap", choices=["auto", "on", "off"], default="auto", help="HDR → SDR tone mapping (auto: when the target is 8-bit)")
    g.add_argument("--deinterlace", choices=["auto", "on", "off"], default="auto")
    g = p.add_argument_group("audio")
    g.add_argument("--acodec", help="aac, mp3, opus, vorbis, flac, alac, pcm, wmav2 … or copy")
    g.add_argument("--audio-bitrate", help="e.g. 128k")
    g.add_argument("--channels", type=int, help="1 = mono, 2 = stereo")
    g.add_argument("--sample-rate", type=int, help="e.g. 44100 or 48000")
    g.add_argument("--audio-track", type=int, help="keep only this audio track (1-based)")
    g.add_argument("--no-audio", action="store_true", help="drop audio")
    g = p.add_argument_group("general")
    g.add_argument("--start", help="convert from this time")
    g.add_argument("--end", help="convert up to this time")
    g.add_argument("--target-mb", type=float, help="stay under this size in MB (1,000,000 bytes): two-pass for video, lowering the size and frame rate the bitrate cannot carry")
    g.add_argument("--copy", action="store_true", help="never re-encode (remux only); fails if a stream does not fit")
    g.add_argument("--reencode", action="store_true", help="always re-encode, even when a stream could be copied")
    g.add_argument("--no-subs", action="store_true", help="drop subtitle tracks")
    g.add_argument("--strip-metadata", action="store_true", help="drop tags, chapters and other metadata")
    g.add_argument("--workers", type=int, help="batch mode: parallel jobs (default: audio in parallel, video one at a time)")
    g.add_argument("--force", action="store_true", help="overwrite existing outputs")
    add_format(p)
    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], ("--start", "--end")))

    from _plan import PRESETS

    if a.presets:
        rows = [[k, v["ext"], v["about"]] for k, v in PRESETS.items() if k not in ("web-mp4", "mp4")]
        print(md_table(["preset", "extension", "what"], rows))
        return 0
    if not a.paths:
        raise UsageError("give INPUT OUTPUT, or inputs with --out-dir")
    from _media import expand_inputs

    if a.out_dir:
        inputs = expand_inputs(a.paths, recursive=a.recursive)
        ext = _batch_ext(a)
        outdir = output_dir(a.out_dir)
        jobs, renamed = _batch_jobs(inputs, outdir, ext)
        for src, dst in jobs:
            output_path(dst, [src], a.force)
        if len(jobs) > 1:
            import os

            os.environ["DESK_QUIET"] = "1"  # parallel progress lines would interleave
        audio_only = ext in (".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac", ".aiff", ".aif", ".wma", ".oga")
        workers = a.workers or (None if audio_only else 1)
        results = pool_map(_convert_safe, [(src, dst, vars(a)) for src, dst in jobs], workers=workers, threads=True)
        root = outdir.resolve()
        for r in results:
            r["output_rel"] = Path(r["output"]).resolve().relative_to(root).as_posix() if Path(r["output"]).resolve().is_relative_to(root) else r["output"]
        ok = [r for r in results if "error" not in r]
        if a.format == "json":
            emit(results, "json", hint=f"All outputs are in {outdir}: media_info.py {outdir} -r lists them.")
        else:
            from _media import fmt_size

            head = (f"{len(ok)} of {len(results)} converted into {outdir} · {fmt_size(sum(r.get('input_size', 0) for r in ok))} → "
                    f"{fmt_size(sum(r.get('size', 0) for r in ok))}")
            if renamed:
                head += f"\n{renamed} output name(s) were taken by another input and got a -2, -3 … suffix (see the table)"
            bad = [r for r in results if "error" in r]
            rows = [[r["input"], r.get("output_rel", "") if "error" not in r else "", fmt_size(r["size"]) if "size" in r else "",
                     r.get("video_action", ""), r.get("audio_action", ""), r.get("error", "; ".join(r.get("notes", [])))] for r in bad + ok]
            print(cap(head + "\n\n" + md_table(["input", "output", "size", "video", "audio", "note"], rows),
                      hint=f"Failures are listed first; every output is in {outdir} (media_info.py {outdir} -r lists them)."))
        return 0 if all("error" not in r for r in results) else 1
    if len(a.paths) != 2:
        raise UsageError("give exactly INPUT OUTPUT (or use --out-dir for several inputs)")
    src, dst = a.paths
    if a.preset and not Path(dst).suffix:
        dst = dst + PRESETS[a.preset.lower()]["ext"] if a.preset.lower() in PRESETS else dst
    res = convert(src, dst, vars(a))
    if a.format == "json":
        emit(res, "json")
    else:
        print(render(res))
    return 0


def _batch_jobs(inputs: list[Path], outdir: Path, ext: str) -> tuple[list[tuple[str, str]], int]:
    """(input, output) pairs: sub-folders relative to the inputs' common folder are kept, and names two inputs would
    share get -2, -3 … (never a silent overwrite inside one batch). Also how many were renamed."""
    import os

    parents = [str(f.resolve().parent) for f in inputs]
    try:
        root = Path(os.path.commonpath(parents))
    except ValueError:  # different drives on Windows
        root = None
    taken: set[str] = set()
    jobs: list[tuple[str, str]] = []
    renamed = 0
    for f in inputs:
        rel = f.resolve().parent.relative_to(root) if root is not None else Path()
        base = outdir / rel / (f.stem + ext)
        dst = base
        k = 2
        while str(dst).lower() in taken:
            dst = base.with_name(f"{base.stem}-{k}{base.suffix}")
            k += 1
        if dst != base:
            renamed += 1
        taken.add(str(dst).lower())
        jobs.append((str(f), str(dst)))
    return jobs, renamed


def _batch_ext(a: Any) -> str:
    from _plan import preset

    if a.to:
        e = a.to if a.to.startswith(".") else "." + a.to
        return e.lower()
    pr = preset(a.preset)
    if pr:
        return pr["ext"]
    raise UsageError("batch mode needs --to EXT or --preset")


def _convert_safe(job: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    src, dst, opts = job
    try:
        return convert(src, dst, opts)
    except SkillError as e:
        return {"input": src, "output": dst, "error": str(e)}


def convert(src: str, dst: str, a: dict[str, Any]) -> dict[str, Any]:
    from _common import input_file
    from _media import TempOut, parse_time, probe, run_ffmpeg, secs_arg, summarize_output
    from _plan import Options, plan_streams, preset

    t0 = time.monotonic()
    inp = input_file(src)
    out = output_path(dst, [inp], a.get("force", False))
    info = probe(inp)
    d = info.get("duration")
    pre = a.get("preset")
    if not pre and out.suffix.lower() == ".gif":
        pre = "gif"
    pr = preset(pre)
    if pr and pr.get("ext") and out.suffix.lower() != pr["ext"] and not (pr["ext"] in (".mp4",) and out.suffix.lower() in (".mp4", ".m4v", ".mov", ".mkv")) and not (pr["ext"] == ".m4a" and out.suffix.lower() in (".m4a", ".m4b", ".aac", ".mp4")):
        raise UsageError(f"preset {pre} writes {pr['ext']} files, but the output is {out.suffix}")
    s = parse_time(a["start"], d) if a.get("start") else 0.0
    e = parse_time(a["end"], d) if a.get("end") else None
    if e is not None and e <= s:
        raise UsageError("--end must be after --start")
    span = (e if e is not None else (d or 0.0)) - s if d or e is not None else None
    o = Options(preset=pre, vcodec=a.get("vcodec"), acodec=a.get("acodec"), crf=a.get("crf"), vbitrate=a.get("video_bitrate"),
                abitrate=a.get("audio_bitrate"), size=a.get("size"), fps=a.get("fps"), channels=a.get("channels"), sample_rate=a.get("sample_rate"),
                speed=a.get("speed") or "medium", target_mb=a.get("target_mb"), audio_track=a.get("audio_track"), no_audio=bool(a.get("no_audio")),
                no_video=bool(a.get("no_video")), no_subs=bool(a.get("no_subs")), copy=bool(a.get("copy")), reencode=bool(a.get("reencode")),
                strip_metadata=bool(a.get("strip_metadata")), tonemap=a.get("tonemap") or "auto", deinterlace=a.get("deinterlace") or "auto")
    inputs: list[Any] = []
    if s:
        inputs += ["-ss", secs_arg(s)]
    inputs += ["-i", str(inp)]
    if e is not None:
        inputs += ["-t", secs_arg(e - s)]
    plan = plan_streams(info, out, o, duration=span)
    notes: list[str] = []
    passlog = Path(tempfile.mkdtemp(prefix="desk-2pass-")) / "pass"

    def encode(pl: Any, again: bool = False) -> None:
        with TempOut(out) as tmp:
            if pl.two_pass:
                run_ffmpeg([*inputs, *_strip_audio(pl.args), "-an", "-sn", "-pass", "1", "-passlogfile", str(passlog), "-f", "null", "-"], duration=span,
                           label="re-analysing" if again else "analysing (pass 1 of 2)")
                run_ffmpeg([*inputs, *pl.args, "-pass", "2", "-passlogfile", str(passlog), str(tmp)], duration=span,
                           label="re-encoding smaller" if again else "encoding (pass 2 of 2)")
            else:
                run_ffmpeg([*inputs, *pl.args, str(tmp)], duration=span, label="re-encoding smaller" if again else "converting")

    target = o.target_mb if o.target_mb is not None else (pr.get("target_mb") if pr else None)
    try:
        encode(plan)
        # Container overhead and rate-control drift can overshoot: up to two corrective encodes, scaled by the miss.
        tries = 0
        goal = target
        while target and out.stat().st_size > target * 1_000_000 and plan.video and plan.video != "copy" and span and tries < 2:
            tries += 1
            goal = goal * target * 1_000_000 * (0.97 - 0.03 * tries) / out.stat().st_size
            plan = plan_streams(info, out, Options(**{**o.__dict__, "target_mb": goal}), duration=span)
            encode(plan, again=True)
        notes += plan.notes
        if (s or e is not None) and plan.video == "copy":
            notes.append("the video was copied, so the cut starts at the keyframe before --start (add --reencode for an exact cut)")
        if tries:
            got = out.stat().st_size / 1e6
            notes.append(f"re-encoded {tries} more time(s) to get under {target:g} MB ({got:.2f} MB)" if got <= target
                         else f"still {got:.2f} MB after {tries} more encode(s), over the {target:g} MB target: lower --size or --fps, or shorten it")
    finally:
        import shutil

        shutil.rmtree(passlog.parent, ignore_errors=True)
    res = summarize_output(out)
    res.update({"input": str(inp), "output": str(out), "input_size": info["size"], "video_action": _action(plan.video, info, "video"), "audio_action": _action(plan.audio, info, "audio"),
                "notes": notes, "seconds": round(time.monotonic() - t0, 2)})
    if target:
        res["target_mb"] = target
    if span and res.get("duration") and abs(res["duration"] - span) > max(1.0, 0.02 * span) and not o.fps:
        res["notes"].append(f"output lasts {res['duration']:.1f} s, expected about {span:.1f} s")
    return res


def _strip_audio(args: list[str]) -> list[str]:
    """Pass 1 of a two-pass encode needs the video settings only."""
    out: list[str] = []
    skip = 0
    for i, x in enumerate(args):
        if skip:
            skip -= 1
            continue
        if x == "-map" and i + 1 < len(args) and (":a:" in args[i + 1] or ":s:" in args[i + 1]):
            skip = 1
            continue
        if x.startswith(("-c:a", "-b:a", "-q:a", "-ac:a", "-ar:a", "-filter:a", "-c:s")) or x in ("-compression_level", "-disposition:v:0", "-id3v2_version", "-movflags", "-map_metadata", "-strict"):
            skip = 1
            continue
        out.append(x)
    return out


def _action(choice: str | None, info: dict[str, Any], kind: str) -> str:
    from _media import main_audio, main_video

    s = main_video(info) if kind == "video" else main_audio(info)
    if choice is None:
        return "dropped" if s else ""
    src = (s or {}).get("codec", "?")
    return f"{src} copied" if choice == "copy" else f"{src} → {choice}"


def render(r: dict[str, Any]) -> str:
    from _media import describe_output

    from _media import fmt_size

    lines = [f"wrote {describe_output(r)}",
             f"from {r['input']} ({fmt_size(r['input_size'])}) in {r['seconds']:.1f}s"]
    acts = [f"video: {r['video_action']}" if r.get("video_action") else "", f"audio: {r['audio_action']}" if r.get("audio_action") else ""]
    acts = [x for x in acts if x]
    if acts:
        lines.append(" · ".join(acts))
    if r.get("target_mb"):
        lines.append(f"size target {r['target_mb']:g} MB: got {r['size'] / 1_000_000:.2f} MB")
    for n in r.get("notes", []):
        lines.append(f"note: {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    run_main(main)
