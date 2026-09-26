#!/usr/bin/env python3
"""Self-test for the audio-video skill: builds small fixtures with the bundled ffmpeg, runs every script the way an
agent does (python3 scripts/<name>.py …), and checks real outputs: durations, sizes, pixels, levels, cue times.

No network: transcription runs against a stub model unless DESK_SELFTEST_NETWORK=1 (which downloads 'tiny').
Prints "ok: N checks in S s" and exits 0, or lists the failures and exits 1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
PY = sys.executable
T0 = time.monotonic()
LOCK = threading.Lock()
RESULTS: list[tuple[str, bool, str]] = []
WORK = Path(tempfile.mkdtemp(prefix="desk-av-selftest-"))
FX = WORK / "fx"
CACHE = WORK / "cache"  # a private file cache: no stale entries from other runs, nothing left behind


def check(name: str, cond: Any, detail: Any = "") -> bool:
    ok = bool(cond)
    with LOCK:
        RESULTS.append((name, ok, "" if ok else str(detail)[:600]))
    return ok


def run(script: str, *args: Any, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: float = 240) -> subprocess.CompletedProcess[str]:
    e = {**os.environ, "DESK_QUIET": "1", "PYTHONIOENCODING": "utf-8", "DESK_FILE_CACHE": str(CACHE), **(env or {})}
    return subprocess.run([PY, str(HERE / script), *[str(a) for a in args]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=e, cwd=str(cwd or WORK), timeout=timeout)


def ok(name: str, r: subprocess.CompletedProcess[str], *contains: str) -> bool:
    good = r.returncode == 0 and all(c in r.stdout for c in contains)
    return check(name, good, f"exit {r.returncode}; stdout: {r.stdout[-400:]}; stderr: {r.stderr[-400:]}")


def fails(name: str, r: subprocess.CompletedProcess[str], code: int, *contains: str) -> bool:
    good = r.returncode == code and r.stderr.startswith("error:") and all(c in r.stderr for c in contains) and "Traceback" not in r.stderr
    return check(name, good, f"exit {r.returncode}; stderr: {r.stderr[-400:]}")


def js(r: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def info(path: Path) -> dict[str, Any]:
    return js(run("media_info.py", path, "--format", "json"))


def near(a: Any, b: float, tol: float) -> bool:
    try:
        return abs(float(a) - b) <= tol
    except (TypeError, ValueError):
        return False


def streams(i: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [s for s in i.get("streams", []) if s.get("type") == kind]


def pix(path: Path) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.float32)


# ── fixtures ────────────────────────────────────────────────────────────

FF = ""
HAVE: dict[str, bool] = {}


def ff(*args: Any) -> bool:
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *[str(a) for a in args]], capture_output=True, timeout=120)
    if r.returncode != 0:
        print(f"fixture command failed: {' '.join(str(a) for a in args)}\n{r.stderr.decode('utf-8', 'replace')[-400:]}", file=sys.stderr)
    return r.returncode == 0


def fixtures() -> None:
    global FF
    import imageio_ffmpeg
    from PIL import Image, ImageDraw

    FF = imageio_ffmpeg.get_ffmpeg_exe()
    FX.mkdir(parents=True)
    (FX / "subs.srt").write_text("1\n00:00:01,000 --> 00:00:02,500\nFirst cue\n\n2\n00:00:03,500 --> 00:00:05,000\nSecond cue\n", encoding="utf-8")
    (FX / "meta.txt").write_text(";FFMETADATA1\ntitle=Fixture\nartist=Selftest\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=Part one\n"
                                 "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=6000\ntitle=Part two\n", encoding="utf-8")
    assert ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=6", "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=6", "-i", FX / "subs.srt", "-i", FX / "meta.txt",
              "-map", "0", "-map", "1", "-map", "2", "-map_metadata", "3", "-map_chapters", "3", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-ac", "2", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", FX / "v.mp4"), "cannot build the main fixture"
    ff("-i", FX / "v.mp4", "-map", "0:v", "-map", "0:a", "-map", "0:s", "-c", "copy", "-c:s", "srt", FX / "v.mkv")
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=1.5", "-f", "lavfi", "-i", "smptebars=s=320x180:r=25:d=1.5", "-f", "lavfi", "-i", "mandelbrot=s=320x180:r=25,trim=duration=1.5",
       "-f", "lavfi", "-i", "rgbtestsrc=s=320x180:r=25:d=1.5", "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "250", FX / "scenes.mp4")
    ff("-f", "lavfi", "-i", "smptebars=s=320x180:r=25:d=3", "-vf", "pad=320:240:0:30:black", "-c:v", "libx264", "-preset", "ultrafast", FX / "letterbox.mp4")
    HAVE["rot"] = ff("-display_rotation", "90", "-i", FX / "v.mp4", "-map", "0:v", "-map", "0:a", "-c", "copy", FX / "rot.mp4")
    tone = "0.5*sin(2*PI*1000*t)*lt(t\\,1)+0.5*sin(2*PI*440*t)*gte(t\\,2)"
    ff("-f", "lavfi", "-i", f"aevalsrc={tone}|{tone}:s=44100:d=3", "-c:a", "pcm_s16le", FX / "tone.wav")
    ff("-f", "lavfi", "-i", "aevalsrc=1.6*sin(2*PI*220*t):s=44100:d=1", "-c:a", "pcm_s16le", FX / "clip.wav")
    ff("-f", "lavfi", "-i", "aevalsrc=0.25*sin(2*PI*330*t):s=44100:d=4", "-c:a", "pcm_s16le", FX / "quiet.wav")
    img = Image.new("RGB", (64, 64), (200, 30, 30))
    ImageDraw.Draw(img).rectangle([16, 16, 48, 48], fill=(255, 255, 255))
    img.save(FX / "cover.png")
    logo = Image.new("RGBA", (80, 40), (0, 255, 0, 255))
    logo.save(FX / "logo.png")
    ff("-i", FX / "tone.wav", "-i", FX / "cover.png", "-map", "0", "-map", "1", "-c:a", "flac", "-c:v", "png", "-disposition:v", "attached_pic",
       "-metadata", "title=Song", "-metadata", "artist=Tester", FX / "song.flac")
    ff("-i", FX / "tone.wav", "-i", FX / "cover.png", "-map", "0", "-map", "1", "-c:a", "libmp3lame", "-q:a", "4", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
       "-id3v2_version", "3", "-metadata", "title=Song", FX / "song.mp3")
    ff("-f", "lavfi", "-i", "sine=f=660:sample_rate=44100:d=2", "-c:a", "aac", FX / "voice.m4a")
    HAVE["hdr"] = ff("-f", "lavfi", "-i", "testsrc2=s=160x120:r=25:d=1", "-vf", "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
                     "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error", "-tag:v", "hvc1", FX / "hdr.mp4")
    (FX / "notmedia.mp4").write_text("this is not a video", encoding="utf-8")
    (FX / "a.srt").write_text("\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\n<i>Hello</i> world, this is a rather long subtitle line that goes on\r\n\r\n"
                              "2\r\n00:00:03,000 --> 00:00:04,000\r\n[MUSIC PLAYING]\r\n\r\n3\r\n00:00:05,000 --> 00:00:05,300\r\nJOHN: Quick!\r\n\r\n"
                              "4\r\n00:00:05,300 --> 00:00:06,000\r\nQuick!\r\n\r\n5\r\n00:00:10,000 --> 00:00:12,000\r\n42\r\n", encoding="utf-8")
    (FX / "b.vtt").write_text("WEBVTT\nLanguage: fr\n\nNOTE comment\n\nintro\n00:01.000 --> 00:03.000 align:start\n<v Marie>Bonjour <b>tout</b> le monde &amp; bienvenue\n\n"
                              "00:00:04.000 --> 00:00:06.000\n<c.yellow>Deuxième</c> ligne\n", encoding="utf-8")
    (FX / "c.ass").write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                              "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                              "MarginR, MarginV, Encoding\nStyle: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,30,1\n\n"
                              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                              "Dialogue: 0,0:00:01.00,0:00:03.50,Default,Bob,0,0,0,,{\\i1}Hi{\\i0}, there,\\Nhow are you?\n"
                              "Dialogue: 0,0:00:04.00,0:00:05.00,Default,,0,0,0,,{\\pos(100,100)}EXIT\n", encoding="utf-8")
    words = [{"start": 0.5 + 0.4 * k, "end": 0.8 + 0.4 * k, "word": f" word{k}" + ("." if k % 9 == 8 else ""), "probability": 0.9} for k in range(30)]
    (FX / "words.json").write_text(json.dumps({"language": "en", "segments": [{"start": 0.5, "end": 12.4, "text": " ".join(w["word"].strip() for w in words), "words": words}]}), encoding="utf-8")
    (FX / "chapters.txt").write_text("0:00 Opening\n0:02 Middle part\n0:04.5 Ending\n", encoding="utf-8")


# ── groups ──────────────────────────────────────────────────────────────


def g_info() -> None:
    r = run("media_info.py", FX / "v.mp4")
    ok("info: markdown", r, "h264", "aac", "mov_text", "Part one")
    i = info(FX / "v.mp4")
    check("info: duration", near(i.get("duration"), 6.0, 0.1), i.get("duration"))
    check("info: streams", [s["type"] for s in i.get("streams", [])][:3] == ["video", "audio", "subtitle"], i.get("streams"))
    v = streams(i, "video")[0] if streams(i, "video") else {}
    check("info: video fields", v.get("width") == 320 and v.get("height") == 240 and near(v.get("fps"), 25, 0.01) and v.get("pix_fmt") == "yuv420p", v)
    au = streams(i, "audio")[0] if streams(i, "audio") else {}
    check("info: audio fields", au.get("sample_rate") == 48000 and au.get("channels") == 2, au)
    check("info: subtitle language", (streams(i, "subtitle") or [{}])[0].get("language") == "eng", streams(i, "subtitle"))
    check("info: chapters and tags", len(i.get("chapters", [])) == 2 and i.get("tags", {}).get("title") == "Fixture", (i.get("chapters"), i.get("tags")))
    if HAVE.get("rot"):
        ri = info(FX / "rot.mp4")
        rv = streams(ri, "video")[0]
        check("info: rotation", rv.get("rotation") == 90, rv)
    if HAVE.get("hdr"):
        hv = streams(info(FX / "hdr.mp4"), "video")[0]
        check("info: HDR10 detected", "PQ" in str(hv.get("hdr")) and hv.get("bit_depth") == 10, hv)
    r = run("media_info.py", FX, "--format", "json")
    rows = js(r)
    check("info: folder batch", isinstance(rows, list) and len(rows) >= 8 and any("error" in x for x in rows), f"{len(rows) if isinstance(rows, list) else rows}")
    r = run("media_info.py", FX / "v.mp4", FX / "tone.wav", FX / "song.mp3")
    ok("info: batch table", r, "| file |", "tone.wav", "3 files")
    r = run("media_info.py", FX / "scenes.mp4", "--keyframes", "--count-frames", "--format", "json")
    k = js(r)
    check("info: keyframes and frame count", k.get("keyframes", {}).get("count", 0) >= 1 and 145 <= (streams(k, "video")[0].get("frames") or 0) <= 152, (k.get("keyframes"), streams(k, "video")))
    fails("info: not media", run("media_info.py", FX / "notmedia.mp4"), 1, "not a media file")
    (FX / "empty.mp3").write_bytes(b"")
    fails("info: empty file diagnosed", run("media_info.py", FX / "empty.mp3"), 1, "is empty (0 bytes)")
    data = (FX / "v.mp4").read_bytes()
    (FX / "trunc.mp4").write_bytes(data[: len(data) * 2 // 3])
    fails("info: truncated MP4 diagnosed", run("media_info.py", FX / "trunc.mp4"), 1, "without its index")
    r = run("media_info.py", FX / "quiet.wav")
    ok("info: mono layout named", r, "mono (1 ch)")
    fails("info: missing file", run("media_info.py", FX / "nope.mp4"), 1, "does not exist")
    si = info(FX / "song.flac")
    check("info: cover art flagged", any(s.get("cover_art") for s in si.get("streams", [])) and si.get("kind") == "audio", si.get("streams"))


def g_convert() -> None:
    d = WORK / "convert"
    d.mkdir()
    r = run("media_convert.py", FX / "v.mkv", d / "remux.mp4", "--format", "json")
    j = js(r)
    check("convert: mkv→mp4 copies streams", r.returncode == 0 and "copied" in j.get("video_action", "") and "copied" in j.get("audio_action", ""), r.stderr or j)
    check("convert: srt → mov_text", "mov_text" in str(j.get("subtitles")), j.get("subtitles"))
    r = run("media_convert.py", FX / "v.mp4", d / "web.mp4", "--preset", "web", "--reencode")
    ok("convert: web preset", r, "libx264")
    head = (d / "web.mp4").read_bytes()[:200_000] if (d / "web.mp4").exists() else b""
    check("convert: faststart (moov before mdat)", b"moov" in head and head.find(b"moov") < head.find(b"mdat"), (head.find(b"moov"), head.find(b"mdat")))
    r = run("media_convert.py", FX / "v.mp4", d / "small.webm", "--size", "160x", "--speed", "fastest")
    ok("convert: webm", r, "vp9", "opus")
    wv = streams(info(d / "small.webm"), "video")
    check("convert: webm size", wv and wv[0].get("width") == 160 and wv[0].get("height") == 120, wv)
    r = run("media_convert.py", FX / "v.mp4", d / "target.mp4", "--target-mb", "0.25", "--audio-bitrate", "48k", "--format", "json")
    size = (d / "target.mp4").stat().st_size if (d / "target.mp4").exists() else 0
    check("convert: --target-mb", r.returncode == 0 and 0.15e6 < size <= 0.26e6, f"{size} bytes; {r.stderr[-300:]}")
    r = run("media_convert.py", FX / "v.mp4", d / "anim.gif", "--start", "1", "--end", "3", "--size", "160x")
    gi = streams(info(d / "anim.gif"), "video")
    check("convert: gif", r.returncode == 0 and gi and gi[0].get("width") == 160, r.stderr or gi)
    r = run("media_convert.py", FX / "song.flac", d / "song.mp3", "--format", "json")
    mi = info(d / "song.mp3")
    check("convert: flac→mp3 keeps tags and cover", r.returncode == 0 and mi.get("tags", {}).get("title") == "Song" and any(s.get("cover_art") for s in mi.get("streams", [])), (r.stderr, mi.get("tags")))
    check("convert: mp3 duration", near(mi.get("duration"), 3.0, 0.1), mi.get("duration"))
    r = run("media_convert.py", FX / "tone.wav", d / "voice.m4a", "--preset", "voice", "--format", "json")
    va = streams(info(d / "voice.m4a"), "audio")
    check("convert: voice preset mono 24k", r.returncode == 0 and va and va[0].get("channels") == 1 and va[0].get("sample_rate") == 24000, va)
    run("media_convert.py", FX / "tone.wav", d / "mono64.mp3", "--channels", "1", "--audio-bitrate", "64k")
    r = run("media_convert.py", d / "mono64.mp3", d / "mono.m4a")
    ma = streams(info(d / "mono.m4a"), "audio")
    check("convert: mono 64k source is not inflated", r.returncode == 0 and ma and 0 < int(ma[0].get("bit_rate") or 0) <= 90_000, r.stderr or ma)
    r = run("media_convert.py", FX / "tone.wav", FX / "quiet.wav", "--to", "opus", "--out-dir", d / "batch", "--format", "json")
    b = js(r)
    check("convert: batch", r.returncode == 0 and isinstance(b, list) and len(b) == 2 and all((d / "batch" / n).exists() for n in ("tone.opus", "quiet.opus")), r.stderr or b)
    r = run("media_convert.py", FX / "tone.wav", d / "short.mp3", "--target-mb", "0.02", "--format", "json")
    s2 = (d / "short.mp3").stat().st_size if (d / "short.mp3").exists() else 0
    check("convert: audio --target-mb", r.returncode == 0 and s2 <= 0.024e6, s2)
    fails("convert: refuses to overwrite the input", run("media_convert.py", FX / "tone.wav", FX / "tone.wav"), 1, "refusing to overwrite the input")
    fails("convert: existing output needs --force", run("media_convert.py", FX / "v.mp4", d / "web.mp4"), 1, "already exists")
    fails("convert: --copy impossible", run("media_convert.py", FX / "v.mp4", d / "x.webm", "--copy"), 1, "cannot go into")
    fails("convert: bad extension", run("media_convert.py", FX / "v.mp4", d / "x.xyz"), 2, "unsupported output extension")
    ok("convert: preset list", run("media_convert.py", "--presets"), "email", "webm")


def dur(p: Path) -> float:
    return float(info(p).get("duration") or 0)


def g_edit_time() -> None:
    d = WORK / "edit1"
    d.mkdir()
    r = run("media_edit.py", "trim", FX / "v.mp4", d / "trim.mp4", "--start", "1.5", "--end", "4.5", "--format", "json")
    check("edit: trim exact", r.returncode == 0 and near(dur(d / "trim.mp4"), 3.0, 0.08), r.stderr or dur(d / "trim.mp4"))
    r = run("subtitles.py", "extract", d / "trim.mp4", d / "trim.srt")
    txt = (d / "trim.srt").read_text(encoding="utf-8") if (d / "trim.srt").exists() else ""
    check("edit: trim re-times subtitles", "00:00:00,000 --> 00:00:01,000" in txt and "00:00:02,000 --> 00:00:03,000" in txt, txt)
    ch = info(d / "trim.mp4").get("chapters", [])
    check("edit: trim re-times chapters", len(ch) == 2 and near(ch[1]["start"], 1.5, 0.05), ch)
    r = run("media_edit.py", "trim", FX / "v.mp4", d / "head.mp4", "--end", "2.5", "--format", "json")
    check("edit: trim from 0 is exact too", r.returncode == 0 and near(dur(d / "head.mp4"), 2.5, 0.05) and len(info(d / "head.mp4").get("chapters", [])) <= 1, r.stderr or dur(d / "head.mp4"))
    r = run("media_edit.py", "trim", FX / "v.mp4", d / "fast.mp4", "--start", "2", "--duration", "2", "--fast", "--format", "json")
    check("edit: trim --fast copies", r.returncode == 0 and js(r).get("video_action") == "copied" and near(dur(d / "fast.mp4"), 2.0, 0.3), r.stderr or js(r))
    r = run("media_edit.py", "cut", FX / "v.mp4", d / "cut.mp4", "--remove", "1-2,4-5")
    check("edit: cut", r.returncode == 0 and near(dur(d / "cut.mp4"), 4.0, 0.08), r.stderr or dur(d / "cut.mp4"))
    r = run("media_edit.py", "cut", FX / "v.mp4", d / "keep.mp4", "--keep", "0-1,3-4.5", "--fast")
    check("edit: cut --keep --fast", r.returncode == 0 and 2.0 <= dur(d / "keep.mp4") <= 4.2, r.stderr or dur(d / "keep.mp4"))
    r = run("media_edit.py", "speed", FX / "v.mp4", d / "fast2.mp4", "--factor", "2")
    check("edit: speed x2", r.returncode == 0 and near(dur(d / "fast2.mp4"), 3.0, 0.08), r.stderr or dur(d / "fast2.mp4"))
    r = run("media_edit.py", "speed", FX / "tone.wav", d / "slow.wav", "--factor", "0.5")
    check("edit: speed audio 0.5", r.returncode == 0 and near(dur(d / "slow.wav"), 6.0, 0.08), r.stderr or dur(d / "slow.wav"))
    r = run("media_edit.py", "concat", FX / "v.mp4", FX / "v.mp4", "-o", d / "cat.mp4", "--chapters", "--format", "json")
    ci = info(d / "cat.mp4")
    check("edit: concat same formats copies", r.returncode == 0 and "copied" in js(r).get("video_action", "") and near(ci.get("duration"), 12.0, 0.15), r.stderr or js(r))
    check("edit: concat chapters per clip", [c.get("title") for c in ci.get("chapters", [])] == ["v", "v"], ci.get("chapters"))
    r = run("media_edit.py", "concat", FX / "v.mp4", FX / "scenes.mp4", "-o", d / "xf.mp4", "--crossfade", "1", "--speed", "fastest")
    check("edit: concat with crossfade", r.returncode == 0 and near(dur(d / "xf.mp4"), 6 + 6 - 1, 0.15), r.stderr or dur(d / "xf.mp4"))
    r = run("media_edit.py", "concat", FX / "tone.wav", FX / "quiet.wav", "-o", d / "cat.wav")
    check("edit: concat audio", r.returncode == 0 and near(dur(d / "cat.wav"), 7.0, 0.05), r.stderr or dur(d / "cat.wav"))
    r = run("media_edit.py", "pipeline", FX / "v.mp4", d / "pipe.mp4", "--ops",
            json.dumps([{"op": "trim", "start": 1, "end": 5}, {"op": "crop", "aspect": "1:1"}, {"op": "text", "text": "Square", "box": True}, {"op": "fade", "in": 0.5, "out": 0.5}, {"op": "normalize"}]),
            "--format", "json")
    pi = info(d / "pipe.mp4")
    pv = streams(pi, "video")
    check("edit: pipeline", r.returncode == 0 and pv and pv[0].get("width") == pv[0].get("height") == 240 and near(pi.get("duration"), 4.0, 0.1), r.stderr or pv)
    fails("edit: pipeline bad op", run("media_edit.py", "pipeline", FX / "v.mp4", d / "bad.mp4", "--ops", '[{"op":"explode"}]'), 2, "unknown operation")
    fails("edit: video op on audio", run("media_edit.py", "crop", FX / "tone.wav", d / "bad.wav", "--aspect", "1:1"), 1, "needs video")


def lufs(p: Path) -> float | None:
    j = js(run("media_audio.py", "stats", p, "--format", "json"))
    return j.get("integrated_lufs")


def g_edit_audio() -> None:
    d = WORK / "edit2"
    d.mkdir()
    base = lufs(FX / "quiet.wav")
    r = run("media_edit.py", "volume", FX / "quiet.wav", d / "q6.wav", "--gain", "-6dB")
    after = lufs(d / "q6.wav")
    check("edit: volume -6dB", r.returncode == 0 and base is not None and after is not None and near(after, base - 6, 0.5), (base, after, r.stderr))
    r = run("media_edit.py", "normalize", FX / "quiet.wav", d / "norm.wav", "--target", "-16")
    n = lufs(d / "norm.wav")
    check("edit: normalize to -16 LUFS", r.returncode == 0 and n is not None and near(n, -16, 1.0), (n, r.stderr))
    check("edit: normalize keeps the sample rate", streams(info(d / "norm.wav"), "audio")[0].get("sample_rate") == 44100, info(d / "norm.wav"))
    r = run("media_edit.py", "normalize", FX / "v.mp4", d / "norm.mp4", "--preset", "broadcast", "--format", "json")
    j = js(r)
    check("edit: normalize video copies the picture", r.returncode == 0 and j.get("video_action") == "copied" and j.get("audio_action") == "re-encoded", r.stderr or j)
    r = run("media_edit.py", "fade", FX / "tone.wav", d / "fade.wav", "--in", "0.5", "--out", "0.5")
    ok("edit: fade audio", r)
    r = run("media_edit.py", "mute", FX / "v.mp4", d / "mute.mp4", "--ranges", "1-2", "--beep")
    ok("edit: mute range with beep", r)
    r = run("media_edit.py", "mute", FX / "v.mp4", d / "silent.mp4", "--format", "json")
    check("edit: mute all drops audio", r.returncode == 0 and not streams(info(d / "silent.mp4"), "audio"), r.stderr)
    r = run("media_edit.py", "denoise", FX / "tone.wav", d / "dn.wav")
    ok("edit: denoise", r)
    r = run("media_edit.py", "extract-audio", FX / "v.mp4", d / "a.m4a", "--format", "json")
    check("edit: extract-audio copies aac", r.returncode == 0 and js(r).get("audio_action") == "copied", r.stderr or js(r))
    r = run("media_edit.py", "extract-audio", FX / "v.mp4", d / "a.flac")
    check("edit: extract-audio to flac", r.returncode == 0 and streams(info(d / "a.flac"), "audio")[0].get("codec") == "flac", r.stderr)
    r = run("media_edit.py", "replace-audio", FX / "v.mp4", FX / "voice.m4a", d / "rep.mp4", "--format", "json")
    ri = info(d / "rep.mp4")
    check("edit: replace-audio (padded to the video)", r.returncode == 0 and near(ri.get("duration"), 6.0, 0.1) and streams(ri, "audio")[0].get("sample_rate") == 44100, r.stderr or ri)
    r = run("media_edit.py", "replace-audio", FX / "v.mp4", FX / "voice.m4a", d / "rep2.mp4", "--loop", "--format", "json")
    check("edit: replace-audio --loop copies", r.returncode == 0 and js(r).get("audio_action") == "copied" and near(dur(d / "rep2.mp4"), 6.0, 0.1), r.stderr or js(r))
    r = run("media_edit.py", "mix-audio", FX / "v.mp4", FX / "voice.m4a", d / "mix.mp4", "--duck", "--loop", "--fade-out", "1")
    check("edit: mix-audio", r.returncode == 0 and near(dur(d / "mix.mp4"), 6.0, 0.1), r.stderr)


def region_diff(a: Path, b: Path, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    A, B = pix(a), pix(b)
    return float(abs(A[y0:y1, x0:x1] - B[y0:y1, x0:x1]).mean())


def frame(p: Path, t: float, out: Path) -> Path:
    r = run("media_frames.py", "frames", p, "--at", str(t), "--out-dir", out, "--force", "--format", "json")
    fr = js(r).get("frames") or [{}]
    return Path(fr[0].get("file", out / "missing.png"))


def g_edit_video() -> None:
    d = WORK / "edit3"
    d.mkdir()
    ref = frame(FX / "v.mp4", 2.0, d / "f_ref")
    r = run("media_edit.py", "crop", FX / "v.mp4", d / "crop.mp4", "--aspect", "9:16")
    cv = streams(info(d / "crop.mp4"), "video")
    check("edit: crop 9:16", r.returncode == 0 and cv and cv[0]["width"] == 136 and cv[0]["height"] == 240, r.stderr or cv)
    r = run("media_edit.py", "crop", FX / "letterbox.mp4", d / "auto.mp4", "--auto")
    av = streams(info(d / "auto.mp4"), "video")
    check("edit: crop --auto removes bars", r.returncode == 0 and av and av[0]["height"] == 180 and av[0]["width"] == 320, r.stderr or av)
    r = run("media_edit.py", "scale", FX / "v.mp4", d / "half.mp4", "--size", "50%")
    hv = streams(info(d / "half.mp4"), "video")
    check("edit: scale 50%", r.returncode == 0 and hv and hv[0]["width"] == 160, r.stderr or hv)
    r = run("media_edit.py", "rotate", FX / "v.mp4", d / "rot.mp4", "--angle", "90")
    rv = streams(info(d / "rot.mp4"), "video")
    check("edit: rotate 90", r.returncode == 0 and rv and rv[0]["width"] == 240 and rv[0]["height"] == 320, r.stderr or rv)
    r = run("media_edit.py", "rotate", FX / "v.mp4", d / "rotflag.mp4", "--angle", "90", "--metadata-only", "--format", "json")
    fv = streams(info(d / "rotflag.mp4"), "video")
    check("edit: rotate --metadata-only", r.returncode == 0 and fv and fv[0].get("rotation") == -90 and js(r).get("video_action") == "copied", r.stderr or fv)
    rf = frame(d / "rotflag.mp4", 1.0, d / "f_rotflag")
    check("frames: rotation flag applied to frames", rf.exists() and pix(rf).shape[:2] == (320, 240), rf)
    r = run("media_edit.py", "flip", FX / "v.mp4", d / "flip.mp4", "--horizontal")
    ok("edit: flip", r)
    r = run("media_edit.py", "pad", FX / "v.mp4", d / "pad.mp4", "--aspect", "16:9", "--blur")
    pv = streams(info(d / "pad.mp4"), "video")
    check("edit: pad 16:9", r.returncode == 0 and pv and pv[0]["width"] == 426 and pv[0]["height"] == 240, r.stderr or pv)
    r = run("media_edit.py", "text", FX / "v.mp4", d / "text.mp4", "--text", "HELLO", "--position", "center", "--size", "40", "--box", "--start", "1", "--end", "3")
    t_in = frame(d / "text.mp4", 2.0, d / "f_text_in")
    t_out = frame(d / "text.mp4", 4.0, d / "f_text_out")
    ref4 = frame(FX / "v.mp4", 4.0, d / "f_ref4")
    box = (110, 95, 210, 145)
    check("edit: text drawn inside its time range", r.returncode == 0 and t_in.exists() and region_diff(t_in, ref, box) > 20, r.stderr)
    check("edit: text absent outside its time range", t_out.exists() and region_diff(t_out, ref4, box) < 6, region_diff(t_out, ref4, box) if t_out.exists() else "no frame")
    for hide, want in (("", "libass"), ("subtitles", "without shaping")):
        r = run("media_edit.py", "text", FX / "v.mp4", d / f"rtl{len(hide)}.mp4", "--text", "مرحبا", "--position", "center",
                "--size", "40", "--end", "3", "--format", "json", env={"DESK_FFMPEG_HIDE": hide} if hide else None)
        rf2 = frame(d / f"rtl{len(hide)}.mp4", 2.0, d / f"f_rtl{len(hide)}")
        if not hide and "libass" not in json.dumps(js(r)):
            want = "without shaping"  # this ffmpeg has no libass
        check(f"edit: right-to-left text ({want})", r.returncode == 0 and want in json.dumps(js(r), ensure_ascii=False) and rf2.exists() and region_diff(rf2, ref, box) > 5,
              (r.stderr, js(r).get("notes")))
    r = run("media_edit.py", "overlay", FX / "v.mp4", FX / "logo.png", d / "logo.mp4", "--position", "top-left", "--width", "80", "--margin", "0")
    lf = frame(d / "logo.mp4", 2.0, d / "f_logo")
    g = pix(lf)[5:35, 5:75].mean(axis=(0, 1)) if lf.exists() else [0, 0, 0]
    check("edit: image overlay", r.returncode == 0 and g[1] > 200 and g[0] < 60 and g[2] < 60, (r.stderr, list(g)))
    r = run("media_edit.py", "blur", FX / "v.mp4", d / "blur.mp4", "--box", "0,0,50%,50%")
    bf = frame(d / "blur.mp4", 2.0, d / "f_blur")
    if bf.exists() and ref.exists():
        import numpy as np

        va, vb = float(np.var(np.diff(pix(bf)[:100, :140], axis=1))), float(np.var(np.diff(pix(ref)[:100, :140], axis=1)))
        check("edit: blur region", r.returncode == 0 and va < vb * 0.5, (va, vb))
    else:
        check("edit: blur region", False, r.stderr)
    r = run("media_edit.py", "adjust", FX / "v.mp4", d / "gray.mp4", "--grayscale")
    gf = frame(d / "gray.mp4", 2.0, d / "f_gray")
    if gf.exists():
        a = pix(gf)
        check("edit: grayscale", r.returncode == 0 and float(abs(a[..., 0] - a[..., 2]).mean()) < 3, float(abs(a[..., 0] - a[..., 2]).mean()))
    else:
        check("edit: grayscale", False, r.stderr)


def g_edit_subs() -> None:
    d = WORK / "edit4"
    d.mkdir()
    r = run("media_edit.py", "subtitles", FX / "scenes.mp4", FX / "a.srt", d / "soft.mp4", "--language", "fra", "--default")
    s = streams(info(d / "soft.mp4"), "subtitle")
    check("edit: soft subtitles mp4", r.returncode == 0 and s and s[0].get("codec") == "mov_text" and s[0].get("language") == "fra", r.stderr or s)
    r = run("media_edit.py", "subtitles", FX / "scenes.mp4", FX / "c.ass", d / "soft.mkv", "--language", "eng")
    s = streams(info(d / "soft.mkv"), "subtitle")
    check("edit: soft ASS subtitles mkv", r.returncode == 0 and s and s[0].get("codec") == "ass", r.stderr or s)
    ref = frame(FX / "v.mp4", 1.5, d / "f_ref")
    box = (0, 180, 320, 240)
    r = run("media_edit.py", "subtitles", FX / "v.mp4", FX / "subs.srt", d / "burn.mp4", "--burn", "--format", "json")
    bf = frame(d / "burn.mp4", 1.5, d / "f_burn")
    check("edit: burn subtitles", r.returncode == 0 and bf.exists() and region_diff(bf, ref, box) > 3, (r.stderr, js(r).get("notes")))
    r = run("media_edit.py", "subtitles", FX / "v.mp4", FX / "subs.srt", d / "burn2.mp4", "--burn", "--box", "--format", "json", env={"DESK_FFMPEG_HIDE": "subtitles"})
    bf2 = frame(d / "burn2.mp4", 1.5, d / "f_burn2")
    nf = frame(d / "burn2.mp4", 3.0, d / "f_burn2b")
    ref3 = frame(FX / "v.mp4", 3.0, d / "f_ref3")
    check("edit: burn subtitles without libass (Pillow)", r.returncode == 0 and "drawn by the skill" in json.dumps(js(r)) and bf2.exists() and region_diff(bf2, ref, box) > 3, (r.stderr, js(r).get("notes")))
    check("edit: Pillow subtitles only during cues", nf.exists() and region_diff(nf, ref3, box) < 6, region_diff(nf, ref3, box) if nf.exists() else "no frame")
    r = run("media_edit.py", "remux", FX / "v.mkv", d / "remux.mp4")
    ok("edit: remux", r, "copied")
    fails("edit: remux impossible", run("media_edit.py", "remux", FX / "v.mp4", d / "x.webm"), 1, "media_convert")
    r = run("media_edit.py", "metadata", FX / "song.mp3", d / "tagged.mp3", "--set", "title=New", "--set", "album=Album", "--cover", FX / "cover.png")
    ti = info(d / "tagged.mp3")
    check("edit: metadata tags and cover", r.returncode == 0 and ti.get("tags", {}).get("title") == "New" and ti.get("tags", {}).get("album") == "Album"
          and sum(1 for s in ti.get("streams", []) if s.get("cover_art")) == 1, (r.stderr, ti.get("tags")))
    r = run("media_edit.py", "metadata", FX / "v.mp4", d / "chap.mp4", "--chapters", FX / "chapters.txt", "--clear")
    ci = info(d / "chap.mp4")
    check("edit: chapters from text", r.returncode == 0 and [c["title"] for c in ci.get("chapters", [])] == ["Opening", "Middle part", "Ending"] and not ci.get("tags", {}).get("title"), (r.stderr, ci.get("chapters")))


def g_frames() -> None:
    d = WORK / "frames"
    d.mkdir()
    t0 = time.monotonic()
    r = run("media_frames.py", "sheet", FX / "v.mp4", "--out", d / "sheet.png", "--format", "json")
    j = js(r)
    sh = (j.get("sheets") or [{}])[0]
    check("frames: sheet", r.returncode == 0 and sh.get("frames") == 9 and max(sh.get("width", 0), sh.get("height", 0)) <= 1568, r.stderr or j)
    check("frames: sheet speed", time.monotonic() - t0 < 20, time.monotonic() - t0)
    r = run("media_frames.py", "sheet", FX / "v.mp4", "--out", d / "sheet2.png", "--start", "2", "--end", "4", "--count", "4", "--exact", "--format", "json")
    sh = (js(r).get("sheets") or [{}])[0]
    check("frames: sheet range exact", r.returncode == 0 and sh.get("frames") == 4 and all(2 <= t <= 4 for t in sh.get("times", [])), r.stderr or sh)
    r = run("media_frames.py", "sheet", FX / "v.mp4", "--out", d / "sheet3.png")
    ok("frames: sheet text output", r, "sheet3.png", "view_image")
    r = run("media_frames.py", "frames", FX / "v.mp4", "--at", "0.5,50%,end", "--out-dir", d / "at", "--format", "json")
    fr = js(r).get("frames", [])
    check("frames: --at exact times", r.returncode == 0 and len(fr) == 3 and near(fr[0]["actual"], 0.5, 0.021) and near(fr[1]["actual"], 3.0, 0.03) and fr[2]["actual"] > 5.8, r.stderr or fr)
    check("frames: png size", fr and Path(fr[0]["file"]).exists() and fr[0]["width"] == 320, fr[:1])
    r = run("media_frames.py", "frames", FX / "v.mp4", "--every", "2", "--out-dir", d / "every", "--jpg", "--format", "json")
    fr = js(r).get("frames", [])
    check("frames: --every", r.returncode == 0 and len(fr) == 3 and all(f["file"].endswith(".jpg") for f in fr), r.stderr or fr)
    r = run("media_frames.py", "frames", FX / "v.mp4", "--count", "5", "--fast", "--out-dir", d / "count", "--format", "json")
    check("frames: --count --fast", r.returncode == 0 and len(js(r).get("frames", [])) == 5, r.stderr)
    r = run("media_frames.py", "frames", FX / "scenes.mp4", "--scenes", "--sheet", "--out-dir", d / "scenes", "--format", "json")
    j = js(r)
    times = [f["actual"] for f in j.get("frames", [])]
    check("frames: scene detection", r.returncode == 0 and len(times) == 4 and all(near(t, e, 0.1) for t, e in zip(times, [0, 1.5, 3.0, 4.5])) and j.get("sheet"), r.stderr or times)
    if HAVE.get("rot"):
        r = run("media_frames.py", "frames", FX / "rot.mp4", "--at", "1", "--out-dir", d / "rot", "--format", "json")
        fr = js(r).get("frames", [{}])
        check("frames: rotation applied", r.returncode == 0 and fr[0].get("width") == 240 and fr[0].get("height") == 320, fr)
    if HAVE.get("hdr"):
        r = run("media_frames.py", "frames", FX / "hdr.mp4", "--at", "0.5", "--out-dir", d / "hdr", "--format", "json")
        fr = js(r).get("frames", [{}])
        ok_img = False
        if r.returncode == 0 and fr and Path(fr[0].get("file", "")).exists():
            a = pix(Path(fr[0]["file"]))
            ok_img = 20 < float(a.mean()) < 235 and float(a.std()) > 20
        check("frames: HDR frame tone-mapped", ok_img, r.stderr or fr)
    r = run("media_frames.py", "gif", FX / "v.mp4", d / "p.gif", "--clips", "3", "--clip-length", "0.5", "--width", "160", "--format", "json")
    g = js(r)
    check("frames: preview gif", r.returncode == 0 and g.get("frames", 0) > 5 and g.get("width") == 160, r.stderr or g)
    r = run("media_frames.py", "gif", FX / "v.mp4", "--out", d / "range.gif", "--start", "1", "--end", "3", "--width", "160", "--fps", "10", "--format", "json")
    g = js(r)
    check("frames: gif of a range (--end, --out)", r.returncode == 0 and 18 <= g.get("frames", 0) <= 22, r.stderr or g)
    r = run("media_frames.py", "thumb", FX / "scenes.mp4", d / "thumb.jpg", "--format", "json")
    check("frames: thumbnail", r.returncode == 0 and (d / "thumb.jpg").exists() and js(r).get("width") == 320, r.stderr)
    r = run("media_frames.py", "cover", FX / "song.mp3", d / "cover.png", "--format", "json")
    c = js(r)
    check("frames: cover art", r.returncode == 0 and c.get("width") == 64 and c.get("format") == "jpg" and Path(c.get("file", "")).suffix == ".jpg", r.stderr or c)
    fails("frames: audio has no frames", run("media_frames.py", "sheet", FX / "tone.wav"), 1, "media_audio.py")
    fails("frames: time past the end", run("media_frames.py", "frames", FX / "v.mp4", "--at", "30"), 2, "past the end")


def g_audio() -> None:
    d = WORK / "audio"
    d.mkdir()
    r = run("media_audio.py", "waveform", FX / "tone.wav", "--out", d / "w.png", "--mark-silence", "--format", "json")
    j = js(r)
    check("audio: waveform", r.returncode == 0 and j.get("width") == 1568 and (d / "w.png").exists() and len(j.get("silences", [])) == 1, r.stderr or j)
    sil = (j.get("silences") or [[0, 0]])[0]
    check("audio: waveform silence range", near(sil[0], 1.0, 0.06) and near(sil[1], 2.0, 0.06), sil)
    check("audio: waveform peak", near(j.get("peak_dbfs"), -6.0, 0.3), j.get("peak_dbfs"))
    r = run("media_audio.py", "waveform", FX / "clip.wav", "--out", d / "c.png", "--format", "json")
    check("audio: clipping detected", r.returncode == 0 and js(r).get("clipped_runs", 0) > 10, r.stderr or js(r))
    r = run("media_audio.py", "waveform", FX / "v.mp4", "--out", d / "v.png", "--start", "1", "--end", "2", "--log")
    ok("audio: waveform of a video range", r, "view_image")
    r = run("media_audio.py", "spectrogram", FX / "tone.wav", "--out", d / "s.png", "--format", "json")
    j = js(r)
    check("audio: spectrogram", r.returncode == 0 and (d / "s.png").exists() and (near(j.get("strongest_band_hz"), 1000, 60) or near(j.get("strongest_band_hz"), 440, 30)), r.stderr or j)
    r = run("media_audio.py", "spectrogram", FX / "tone.wav", "--out", d / "s2.png", "--linear", "--max-freq", "4000")
    ok("audio: linear spectrogram", r, "s2.png")
    r = run("media_audio.py", "stats", FX / "tone.wav", "--format", "json")
    j = js(r)
    check("audio: stats", r.returncode == 0 and j.get("integrated_lufs") is not None and near(j.get("sample_peak_dbfs"), -6.0, 0.3) and j.get("clipped_runs") == 0, r.stderr or j)
    check("audio: stats silence share", near(j.get("silent_seconds"), 1.0, 0.1), j.get("silent_seconds"))
    r = run("media_audio.py", "stats", FX / "clip.wav")
    ok("audio: stats markdown with clipping note", r, "Integrated loudness", "clipped run")
    r = run("media_audio.py", "silence", FX / "tone.wav", "--threshold", "-40", "--format", "json")
    rg = js(r).get("ranges", [])
    check("audio: silence", r.returncode == 0 and len(rg) == 1 and near(rg[0]["start"], 1.0, 0.05) and near(rg[0]["end"], 2.0, 0.05), r.stderr or rg)
    r = run("media_audio.py", "silence", FX / "tone.wav", "--sounds", "--format", "json")
    check("audio: sound ranges", r.returncode == 0 and len(js(r).get("ranges", [])) == 2, r.stderr or js(r))
    r = run("media_audio.py", "speech", FX / "tone.wav", "--format", "json")
    check("audio: speech detection runs (tones are not speech)", r.returncode == 0 and js(r).get("speech_seconds", 99) < 1.0, r.stderr or js(r))
    fails("audio: no audio stream", run("media_audio.py", "stats", FX / "scenes.mp4"), 1, "no audio")


STUB = r'''
import runpy, sys
sys.path.insert(0, SCRIPTS)
import faster_whisper

class Word:
    def __init__(self, s, e, w): self.start, self.end, self.word, self.probability = s, e, w, 0.9
class Seg:
    def __init__(self, s, e, t, words): self.start, self.end, self.text, self.words = s, e, t, words
class Info:
    language, language_probability = "en", 0.97
class Fake:
    calls = 0
    def __init__(self, *a, **k):
        assert k.get("compute_type") == "int8" and k.get("device") == "cpu", k
    def transcribe(self, audio, **kw):
        Fake.calls += 1
        assert audio.dtype.name == "float32" and audio.ndim == 1
        n = len(audio) / 16000.0
        segs = []
        k = 0
        while k + 0.9 <= n:
            ws = [Word(k + 0.1 * j, k + 0.1 * j + 0.08, f" w{Fake.calls}_{int(k)}_{j}") for j in range(6)]
            segs.append(Seg(k, k + 0.6, "".join(w.word for w in ws), ws if kw.get("word_timestamps") else None))
            k += 1.0
        return iter(segs), Info()
faster_whisper.WhisperModel = Fake
sys.argv = ARGV
runpy.run_path(SCRIPTS + "/media_transcribe.py", run_name="__main__")
'''


def g_transcribe() -> None:
    d = WORK / "transcribe"
    d.mkdir()
    r = run("media_transcribe.py", "--list-models")
    ok("transcribe: list models", r, "base", "large-v3", "turbo")
    empty_cache = {"XDG_CACHE_HOME": str(d / "cache"), "HF_HOME": str(d / "cache" / "hf"), "HF_HUB_OFFLINE": "1"}
    fails("transcribe: offline without a model", run("media_transcribe.py", FX / "tone.wav", env=empty_cache), 1, "not downloaded")
    fails("transcribe: bad model folder", run("media_transcribe.py", FX / "tone.wav", "--model-dir", d), 1, "model.bin")
    md = d / "model"
    md.mkdir()
    (md / "model.bin").write_bytes(b"stub")
    argv = ["media_transcribe.py", str(FX / "v.mp4"), "--model-dir", str(md), "--words", "--start", "0.5", "--chunk-minutes", "0.05",
            "--out", str(d / "t.srt"), "--out", str(d / "t.vtt"), "--out", str(d / "t.json"), "--out", str(d / "t.txt"), "--out", str(d / "t.tsv"), "--format", "json"]
    code = STUB.replace("SCRIPTS", repr(str(HERE))).replace("ARGV", repr(argv))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(d), timeout=120,
                       env={**os.environ, "DESK_QUIET": "1", "DESK_FILE_CACHE": str(CACHE)})
    j = js(r)
    check("transcribe: stub model run", r.returncode == 0 and j.get("language") == "en" and j.get("segments", 0) >= 4, (r.stderr[-800:], j))
    data = json.loads((d / "t.json").read_text(encoding="utf-8")) if (d / "t.json").exists() else {}
    segs = data.get("segments", [])
    starts = [s["start"] for s in segs]
    check("transcribe: chunked, offsets restored", len({s["text"].split("_")[0] for s in segs}) >= 2 and starts == sorted(starts) and starts and starts[0] >= 0.5 and segs[-1]["end"] <= 6.05,
          starts)
    check("transcribe: words kept in JSON", segs and all(w["start"] >= s["start"] - 1e-6 for s in segs for w in s.get("words", [])) and len(segs[0].get("words", [])) == 6, segs[:1])
    srt = (d / "t.srt").read_text(encoding="utf-8") if (d / "t.srt").exists() else ""
    vtt = (d / "t.vtt").read_text(encoding="utf-8") if (d / "t.vtt").exists() else ""
    check("transcribe: srt and vtt written", "-->" in srt and vtt.startswith("WEBVTT") and "Language: en" in vtt, (srt[:200], vtt[:200]))
    check("transcribe: txt and tsv written", (d / "t.txt").exists() and "w1_" in (d / "t.txt").read_text(encoding="utf-8") and (d / "t.tsv").read_text(encoding="utf-8").startswith("start\tend\ttext"), "")
    r = run("subtitles.py", "check", d / "t.srt", "--format", "json")
    check("transcribe: subtitles have no overlaps", r.returncode == 0 and "overlap" not in js(r).get("summary", {}), js(r).get("summary"))
    transcript_cache_checks(d, md)
    # A real run with the tiny model when it is already on this machine (or downloads are allowed): never a surprise download.
    net = os.environ.get("DESK_SELFTEST_NETWORK") == "1"
    r = run("media_transcribe.py", FX / "tone.wav", "--model", "tiny", "--out", d / "real.json", "--format", "json", timeout=600, env=None if net else {"HF_HUB_OFFLINE": "1"})
    if r.returncode == 0 or "not downloaded" not in r.stderr:
        check("transcribe: real tiny model", r.returncode == 0 and (d / "real.json").exists() and js(r).get("cached") is False, r.stderr[-500:])
        r = run("media_transcribe.py", FX / "tone.wav", "--model", "tiny", "--format", "json", env={"HF_HUB_OFFLINE": "1"})
        check("transcribe: real transcript cached", r.returncode == 0 and js(r).get("cached") is True, r.stderr[-300:])


def g_subtitles() -> None:
    d = WORK / "subs"
    d.mkdir()
    r = run("subtitles.py", "info", FX / "a.srt", "--format", "json")
    j = js(r)
    check("subs: info srt", r.returncode == 0 and j.get("cues") == 5 and j.get("format") == "srt" and j.get("issues", {}).get("overlap") == 1, j)
    r = run("subtitles.py", "info", FX / "b.vtt", "--format", "json")
    j = js(r)
    check("subs: info vtt", r.returncode == 0 and j.get("cues") == 2 and j.get("language") == "fr" and j.get("speakers") == ["Marie"], j)
    r = run("subtitles.py", "info", FX / "c.ass")
    ok("subs: info ass", r, "ASS", "Bob")
    (d / "roll.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:02.000\n<v Ann>hello there my friend\n\n00:02.000 --> 00:04.000\n<v Ann>hello there my friend\nhow are you\n\n"
                                "00:04.000 --> 00:06.000\n<v Bob>fine thanks\n", encoding="utf-8")
    r = run("subtitles.py", "convert", d / "roll.vtt", "-")
    check("subs: text names speakers and drops rolling repeats", r.returncode == 0 and r.stdout.strip() == "Ann: hello there my friend how are you\n\nBob: fine thanks", r.stdout or r.stderr)
    legacy = {  # text, encoding it is saved in, encoding the guess must find
        "ru": ("Я не знаю, что это такое.\nМы идём домой, и все ждут нас.", "cp1251", "cp1251"),
        "tr": ("Benim adım Abaddon, şimdi gidiyoruz.\nÇok güzel bir gün değil mi?", "cp1254", "cp1254"),
        "cs": ("Příliš žluťoučký kůň úpěl ďábelské ódy.\nTo je všechno, děkuji.", "cp1250", "cp1250"),
        "fr": ("Où est la gare ? Je ne sais pas, désolé.\nÇa va très bien, merci à vous.", "cp1252", "cp1252"),
        "u32": ("Grüße aus Köln", "utf-32", "utf-32"),
    }
    for k, (txt, enc, want) in legacy.items():
        body = "".join(f"{i + 1}\n00:00:0{i},000 --> 00:00:0{i},900\n{line}\n\n" for i, line in enumerate(txt.split("\n")))
        (d / f"legacy-{k}.srt").write_bytes(body.encode(enc))
        r = run("subtitles.py", "info", d / f"legacy-{k}.srt", "--format", "json", "--show", "3")
        j = js(r)
        got = [c["text"] for c in j.get("sample", [])]
        check(f"subs: reads {enc} ({k})", r.returncode == 0 and j.get("encoding") == want and got == txt.split("\n"), (j.get("encoding"), got))
    run("subtitles.py", "convert", FX / "a.srt", d / "a.vtt")
    run("subtitles.py", "convert", d / "a.vtt", d / "back.srt")
    x = js(run("subtitles.py", "info", d / "back.srt", "--format", "json"))
    y = js(run("subtitles.py", "info", FX / "a.srt", "--format", "json"))
    check("subs: srt→vtt→srt round trip", x.get("cues") == y.get("cues") and x.get("first") == y.get("first") and x.get("last") == y.get("last"), (x, y))
    run("subtitles.py", "convert", FX / "c.ass", d / "c.srt")
    cs = (d / "c.srt").read_text(encoding="utf-8") if (d / "c.srt").exists() else ""
    check("subs: ass→srt keeps italics and line breaks", "<i>Hi</i>, there,\nhow are you?" in cs and "pos(" not in cs, cs)
    r = run("subtitles.py", "convert", FX / "a.srt", d / "a.ass", "--video", FX / "v.mp4", "--color", "yellow")
    at = (d / "a.ass").read_text(encoding="utf-8") if (d / "a.ass").exists() else ""
    check("subs: srt→ass with video resolution", r.returncode == 0 and "PlayResX: 320" in at and "&H0000FFFF" in at, at[:300])
    for fmt in ("sbv", "lrc", "json", "tsv", "txt", "md"):
        r = run("subtitles.py", "convert", FX / "b.vtt", d / f"b.{fmt}")
        check(f"subs: vtt→{fmt}", r.returncode == 0 and (d / f"b.{fmt}").stat().st_size > 10, r.stderr)
    r = run("subtitles.py", "info", d / "b.sbv", "--format", "json")
    check("subs: sbv read back", js(r).get("cues") == 2, js(r))
    r = run("subtitles.py", "convert", FX / "words.json", d / "w.srt", "--reflow", "--max-chars", "20")
    wi = js(run("subtitles.py", "check", d / "w.srt", "--line-chars", "20", "--format", "json"))
    check("subs: reflow from word timings", r.returncode == 0 and wi.get("cues", 0) >= 5 and "long line" not in wi.get("summary", {}) and "overlap" not in wi.get("summary", {}), wi)
    r = run("subtitles.py", "convert", FX / "a.srt", "-")
    ok("subs: plain text to stdout", r, "Hello world", "Quick!")
    r = run("subtitles.py", "shift", FX / "subs.srt", d / "shift.srt", "--by", "-0.5")
    check("subs: shift", r.returncode == 0 and "00:00:00,500 --> 00:00:02,000" in (d / "shift.srt").read_text(encoding="utf-8"), r.stderr)
    r = run("subtitles.py", "shift", FX / "subs.srt", d / "shift2.srt", "--by", "+1.5", "--after", "3")
    check("subs: shift after a time", r.returncode == 0 and "00:00:01,000 --> 00:00:02,500" in (d / "shift2.srt").read_text(encoding="utf-8") and "00:00:05,000 --> 00:00:06,500" in (d / "shift2.srt").read_text(encoding="utf-8"), r.stderr)
    r = run("subtitles.py", "sync", FX / "subs.srt", d / "sync.srt", "--map", "1=2", "--map", "3.5=5.5")
    check("subs: two-point sync", r.returncode == 0 and "00:00:02,000 --> 00:00:04,100" in (d / "sync.srt").read_text(encoding="utf-8"), (d / "sync.srt").read_text(encoding="utf-8") if (d / "sync.srt").exists() else r.stderr)
    r = run("subtitles.py", "sync", FX / "subs.srt", d / "fps.srt", "--fps", "25:24")
    check("subs: frame-rate sync", r.returncode == 0 and "00:00:01,042 -->" in (d / "fps.srt").read_text(encoding="utf-8"), r.stderr)
    r = run("subtitles.py", "merge", FX / "subs.srt", FX / "b.vtt", d / "m.srt", "--mode", "stack")
    ms = (d / "m.srt").read_text(encoding="utf-8") if (d / "m.srt").exists() else ""
    check("subs: merge stack", r.returncode == 0 and "First cue\nBonjour" in ms, ms[:300])
    r = run("subtitles.py", "split", FX / "subs.srt", "--at", "3", "--out-dir", d / "parts")
    p2 = (d / "parts" / "subs-2.srt").read_text(encoding="utf-8") if (d / "parts" / "subs-2.srt").exists() else ""
    check("subs: split re-bases", r.returncode == 0 and "00:00:00,500 --> 00:00:02,000" in p2, p2)
    r = run("subtitles.py", "clean", FX / "a.srt", d / "clean.srt", "--remove-sdh", "--max-chars", "30", "--min-duration", "1", "--format", "json")
    j = js(r)
    ci = js(run("subtitles.py", "check", d / "clean.srt", "--format", "json"))
    check("subs: clean", r.returncode == 0 and j.get("cues_after") == 3 and j.get("sdh_removed", 0) >= 2 and "overlap" not in ci.get("summary", {}), (j, ci.get("summary")))
    r = run("subtitles.py", "check", FX / "a.srt", "--video", FX / "v.mp4", "--format", "json")
    ck = js(r).get("summary", {})
    check("subs: check finds problems", r.returncode == 0 and ck.get("overlap") == 1 and ck.get("past the end") == 1 and ck.get("too short") == 1, ck)
    r = run("subtitles.py", "extract", FX / "v.mkv", "--list")
    ok("subs: extract --list", r, "subrip", "eng")
    r = run("subtitles.py", "extract", FX / "v.mp4", d / "x.vtt", "--language", "eng")
    xv = (d / "x.vtt").read_text(encoding="utf-8") if (d / "x.vtt").exists() else ""
    check("subs: extract to vtt", r.returncode == 0 and xv.startswith("WEBVTT") and "Second cue" in xv, r.stderr or xv)
    r = run("subtitles.py", "extract", FX / "v.mkv", d / "x.txt")
    check("subs: extract to txt", r.returncode == 0 and "First cue" in (d / "x.txt").read_text(encoding="utf-8"), r.stderr)
    fails("subs: no subtitle tracks", run("subtitles.py", "extract", FX / "scenes.mp4", d / "none.srt"), 1, "no subtitle tracks")
    enc = d / "latin1.srt"
    enc.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCafé crème\n".encode("cp1252"))
    r = run("subtitles.py", "convert", enc, d / "utf8.vtt")
    check("subs: cp1252 input decoded", r.returncode == 0 and "Café crème" in (d / "utf8.vtt").read_text(encoding="utf-8"), r.stderr)
    fails("subs: not subtitles", run("subtitles.py", "info", FX / "chapters.txt"), 1, "not a subtitle format")
    # Cue text and time specs from files: markup regexes once rescanned the rest of a cue from every unclosed "{", "<v"
    # or "[" (a 1 MB cue: hours); the time parser's adjacent blank runs backtracked on long gaps.
    sys.path.insert(0, str(HERE))
    import _media
    import _subs

    same = (_subs.ass_to_text(r"{\i1}Hello{\i0} there\Nnext"), _subs.strip_tags("<v Bob>hi <c.red>x</c></v>"), _subs.text_to_ass("<i>a</i> <font color=red>b</font>"), _media._parse_positive("1h 30m"))
    t = time.monotonic()
    _subs.ass_to_text("{" * 200000), _subs.strip_tags("<v " * 100000), _subs.srt_text("<lang" * 100000), _subs.text_to_ass("<a" * 100000)
    [(rx.sub("", "[" * 200000), rx.sub("", "(" * 200000), rx.sub("", "\n" * 100000 + "x")) for rx in _subs._SDH]
    lyrics = [rx for rx in _subs._SDH if "♪" in rx.pattern][0]
    kept = lyrics.sub("", "♪ la la ♪\nC#\n\nC#\n  ♪\nplain")
    _media._parse_positive("1h" + " " * 30000 + "x")
    check("subs: markup and time parsing are linear on hostile text", same == ("<i>Hello</i> there\nnext", "hi x", r"{\i1}a{\i0} b", 5400.0) and kept == "\nC#\n\nC#\n\nplain" and time.monotonic() - t < 2, (same, round(time.monotonic() - t, 2)))


# ── regressions from the acceptance review ─────────────────────────────


def corrupt_packet(src: Path, dst: Path, which: int = 3) -> bool:
    """Copies src with one video packet overwritten by 0xFF bytes: PyAV's plain decode stops there, ffmpeg conceals it."""
    import av

    with av.open(str(src)) as c:
        vs = c.streams.video[0]
        pk = [(p.pos, p.size) for p in c.demux(vs) if p.size and p.pos is not None]
    if len(pk) <= which:
        return False
    data = bytearray(src.read_bytes())
    pos, size = pk[which]
    data[pos:pos + size] = b"\xff" * size
    dst.write_bytes(bytes(data))
    return True


def g_regress_convert() -> None:
    d = WORK / "regress1"
    d.mkdir()
    # Batch: two inputs named take.* must not overwrite each other; -r keeps the tree.
    for sub, secs, codec in (("a", 3, "aac"), ("b", 5, "alac")):
        (d / "in" / sub).mkdir(parents=True)
        ff("-f", "lavfi", "-i", f"sine=f=500:d={secs}", "-c:a", codec, d / "in" / sub / "take.m4a")
    ff("-f", "lavfi", "-i", "sine=f=700:d=2", d / "in" / "a" / "take.wav")
    r = run("media_convert.py", d / "in" / "a" / "*", d / "in" / "b" / "*.m4a", "--to", "mp3", "--out-dir", d / "mp3", "--format", "json")
    outs = sorted(str(x.relative_to(d / "mp3").as_posix()) for x in (d / "mp3").rglob("*.mp3"))
    check("convert: batch never overwrites same-named outputs", r.returncode == 0 and outs == ["a/take-2.mp3", "a/take.mp3", "b/take.mp3"], (outs, r.stderr[-300:]))
    durs = sorted(round(dur(x)) for x in (d / "mp3").rglob("*.mp3"))
    check("convert: batch outputs are the right files", durs == [2, 3, 5], durs)
    r = run("media_convert.py", d / "in", "-r", "--to", "opus", "--out-dir", d / "opus")
    got = sorted(str(x.relative_to(d / "opus").as_posix()) for x in (d / "opus").rglob("*.opus"))
    check("convert: --recursive keeps sub-folders", r.returncode == 0 and got == ["a/take-2.opus", "a/take.opus", "b/take.opus"] and "-2" in r.stdout, (got, r.stdout[-300:]))
    # --target-mb on a heavy source lowers size and frame rate and lands under the target.
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=60:d=4", "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", d / "heavy.mp4")
    r = run("media_convert.py", d / "heavy.mp4", d / "small.mp4", "--target-mb", "0.2", "--speed", "fast", "--format", "json")
    j = js(r)
    sv = streams(info(d / "small.mp4"), "video")
    size = (d / "small.mp4").stat().st_size if (d / "small.mp4").exists() else 0
    check("convert: --target-mb lowers size and fps to fit", r.returncode == 0 and 0 < size <= 200_000 and sv and sv[0]["height"] < 720 and (sv[0].get("fps") or 99) <= 30.01
          and "reduced to" in " ".join(j.get("notes", [])), (size, sv[:1], j.get("notes"), r.stderr[-300:]))
    # MP4 with a mov_text track → MKV (converted to SRT), and WebM → MKV keeps Opus.
    r = run("media_convert.py", FX / "v.mp4", d / "v.mkv", "--format", "json")
    check("convert: mov_text MP4 → MKV", r.returncode == 0 and [s.get("codec") for s in streams(info(d / "v.mkv"), "subtitle")] == ["subrip"], r.stderr[-400:])
    r = run("media_edit.py", "remux", FX / "v.mp4", d / "vr.mkv")
    ok("edit: remux mov_text MP4 → MKV", r, "mov_text → srt")
    check("convert: no MP4 brand tags in MKV", not any(k.lower() in ("major_brand", "compatible_brands", "minor_version") for k in info(d / "vr.mkv").get("tags", {})), info(d / "vr.mkv").get("tags"))
    run("media_convert.py", FX / "v.mp4", d / "v.webm", "--speed", "fastest")
    r = run("media_edit.py", "remux", d / "v.webm", d / "w.mkv", "--format", "json")
    check("edit: WebM → MKV remux keeps Opus", r.returncode == 0 and js(r).get("audio_action") == "copied" and streams(info(d / "w.mkv"), "audio")[0].get("codec") == "opus", r.stderr or js(r))
    r = run("media_convert.py", d / "v.webm", d / "w.mp4", "--format", "json")
    check("convert: WebM → MP4 re-encodes VP9 to H.264", r.returncode == 0 and streams(info(d / "w.mp4"), "video")[0].get("codec") == "h264", r.stderr or js(r))
    # The web preset fits 1920x1080 (a 1920x1200 source becomes 1728x1080).
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1200:r=25:d=1", "-c:v", "libx264", "-preset", "ultrafast", d / "wide.mp4")
    run("media_convert.py", d / "wide.mp4", d / "web.mp4", "--preset", "web", "--speed", "fastest")
    wv = streams(info(d / "web.mp4"), "video")
    check("convert: web preset within 1920x1080", wv and (wv[0]["width"], wv[0]["height"]) == (1728, 1080), wv)


def g_regress_edit() -> None:
    d = WORK / "regress2"
    d.mkdir()
    # {\an8}: kept through conversions, shown at the top when burned (libass and the Pillow fallback).
    (d / "an8.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\n{\\an8}Top line\n\n2\n00:00:03,500 --> 00:00:05,000\nBottom line\n", encoding="utf-8")
    run("subtitles.py", "convert", d / "an8.srt", d / "an8.vtt")
    run("subtitles.py", "convert", d / "an8.vtt", d / "an8.ass")
    run("subtitles.py", "convert", d / "an8.ass", d / "back.srt")
    vtt = (d / "an8.vtt").read_text(encoding="utf-8") if (d / "an8.vtt").exists() else ""
    ass = (d / "an8.ass").read_text(encoding="utf-8") if (d / "an8.ass").exists() else ""
    back = (d / "back.srt").read_text(encoding="utf-8") if (d / "back.srt").exists() else ""
    check("subs: {\\an8} → VTT line:0, ASS override, back to SRT", "line:0" in vtt and "{\\an8}Top line" in ass and "{\\an8}Top line" in back and "(\\an8)" not in back + ass + vtt,
          (vtt[-120:], ass[-160:], back[:80]))
    ck = js(run("subtitles.py", "check", d / "an8.srt", "--format", "json"))
    check("subs: {\\an8} is not a problem", ck.get("summary") == {}, ck)
    ref = frame(FX / "v.mp4", 2.0, d / "f_ref")
    top, bottom = (0, 0, 320, 70), (0, 180, 320, 240)
    for hide in ("", "subtitles"):
        out = d / f"an8{len(hide)}.mp4"
        r = run("media_edit.py", "subtitles", FX / "v.mp4", d / "an8.srt", out, "--burn", env={"DESK_FFMPEG_HIDE": hide} if hide else None)
        f2 = frame(out, 2.0, d / f"f_an8{len(hide)}")
        ok_img = r.returncode == 0 and f2.exists() and ref.exists() and region_diff(f2, ref, top) > 2 and region_diff(f2, ref, bottom) < 1.5
        check(f"edit: {{\\an8}} burned at the top ({'Pillow' if hide else 'libass'})", ok_img,
              (r.stderr[-300:], region_diff(f2, ref, top) if f2.exists() else None, region_diff(f2, ref, bottom) if f2.exists() else None))
    # A malformed SRT: bad times and stray lines are skipped, not glued onto the previous cue.
    (d / "junk.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n2\n99:99:99,999 --> 99:99:99,999\nbad time\n\nstray words\n\n3\n00:00:03,000 --> 00:00:04,100\n"
                                "a line of forty three characters in total!!\n4\n00:00:05,000 --> 00:00:06,000\nlast\n", encoding="utf-8")
    j = js(run("subtitles.py", "info", d / "junk.srt", "--format", "json"))
    texts = [c["text"] for c in j.get("sample", [])]
    check("subs: malformed blocks skipped and counted", texts == ["first", "a line of forty three characters in total!!", "last"] and j.get("malformed_skipped") == 2, (texts, j.get("malformed_skipped")))
    ck = js(run("subtitles.py", "check", d / "junk.srt", "--format", "json"))
    check("subs: reading speed shown with a decimal", any(i["kind"] == "reading speed" and "39.1 chars/s" in i["detail"] for i in ck.get("issues", [])), ck.get("issues"))
    # A cut in a pipeline keeps a constant frame rate (no 25.4 fps from the last frame's missing duration).
    run("media_edit.py", "pipeline", FX / "v.mp4", d / "cut.mp4", "--ops", '[{"op":"cut","remove":"2-3"}]', "--speed", "fastest")
    cv = streams(info(d / "cut.mp4"), "video")
    check("edit: pipeline cut keeps 25 fps", cv and near(cv[0].get("fps"), 25.0, 0.001), cv)
    # 'dissolve' is the smooth cross-dissolve; ffmpeg's grainy one is pixel-dissolve.
    r = run("media_edit.py", "concat", FX / "v.mp4", FX / "scenes.mp4", "-o", d / "xd.mp4", "--crossfade", "0.5", "--transition", "dissolve", "--speed", "fastest", "--format", "json")
    check("edit: --transition dissolve is a cross-dissolve", r.returncode == 0 and "smooth cross-dissolve" in " ".join(js(r).get("notes", [])), r.stderr or js(r).get("notes"))
    fails("edit: unknown transition", run("media_edit.py", "concat", FX / "v.mp4", FX / "v.mp4", "-o", d / "x.mp4", "--crossfade", "0.5", "--transition", "sparkle"), 2, "unknown transition")
    # Normalising into MP3 leaves room for the encoder's overshoot, and a 48 kb/s source stays small.
    ff("-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:a=0.3:seed=1,lowpass=f=3500,volume=0.3*(0.5+0.5*sin(2*PI*0.7*t))*gt(sin(2*PI*0.25*t)\\,-0.6):eval=frame",
       "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "48k", d / "pod.mp3")
    r = run("media_edit.py", "normalize", d / "pod.mp3", d / "podn.mp3", "--preset", "podcast", "--format", "json")
    st = js(run("media_audio.py", "stats", d / "podn.mp3", "--format", "json"))
    na = streams(info(d / "podn.mp3"), "audio")
    check("edit: normalize to MP3 keeps the true peak under -1.5 dBTP", r.returncode == 0 and st.get("true_peak_dbtp") is not None and st["true_peak_dbtp"] <= -1.5, (st.get("true_peak_dbtp"), r.stderr[-300:]))
    check("edit: normalize does not inflate a 48 kb/s MP3", na and int(na[0].get("bit_rate") or 0) <= 64_000, na)
    # A damaged video: frames after a broken packet still decode.
    if corrupt_packet(FX / "v.mp4", d / "damaged.mp4"):
        r = run("media_frames.py", "frames", d / "damaged.mp4", "--at", "0.5,2,4", "--out-dir", d / "dmg", "--format", "json")
        check("frames: damaged video still gives frames", r.returncode == 0 and len(js(r).get("frames", [])) == 3, r.stderr or js(r))
    # frames --sheet shrinks big frames first, so the labels stay readable (at least 9 px tall).
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=10:d=3", "-c:v", "libx264", "-preset", "ultrafast", d / "hd.mp4")
    r = run("media_frames.py", "frames", d / "hd.mp4", "--at", "0.5,1.5,2.5", "--sheet", "--out-dir", d / "hdf", "--format", "json")
    sheet = js(r).get("sheet")
    tall = 0
    if sheet and Path(sheet).exists():
        a = pix(Path(sheet))
        # Label text is near-black and grey (the frames here are saturated colour): its rows under the first cell.
        text = (a.max(axis=2) < 110) & (a.max(axis=2) - a.min(axis=2) < 30)
        H = a.shape[0]
        band = [y for y in range(int(H * 0.3), int(H * 0.6)) if text[y, 5:120].any()]
        tall = (max(band) - min(band) + 1) if band else 0
    check("frames: --sheet labels readable", r.returncode == 0 and tall >= 9, (tall, r.stderr[-200:]))
    # The waveform's last time label is never cut at the right edge.
    r = run("media_audio.py", "waveform", FX / "tone.wav", "--out", d / "w.png", "--format", "json")
    if (d / "w.png").exists():
        a = pix(d / "w.png")
        edge = a[-26:, -3:, :]
        check("audio: last time label inside the picture", float(edge.min()) > 200, float(edge.min()))
    # onnxruntime's telemetry warning does not reach stderr.
    r = run("media_audio.py", "speech", FX / "tone.wav", "--no-cache")
    check("audio: speech detection keeps stderr clean", r.returncode == 0 and "onnxruntime" not in r.stderr and "telemetry" not in r.stderr, r.stderr[-300:])


# ── big files (scaled down): maps, budgets, search, cache ──────────────


def g_big() -> None:
    d = WORK / "big"
    d.mkdir()
    cues = []
    for i in range(360):
        t = 2 * i
        stamp = lambda x: f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{int(x % 60):02d},{int(round(x % 1 * 1000)):03d}"  # noqa: E731
        text = "we should revisit the quarterly budget" if i == 247 else f"line {i + 1} of the talk"
        cues.append(f"{i + 1}\n{stamp(t)} --> {stamp(t + 1.5)}\n{text}\n")
    (d / "talk.srt").write_text("\n".join(cues), encoding="utf-8")
    meta = [";FFMETADATA1"] + [x for k in range(12) for x in ("[CHAPTER]", "TIMEBASE=1/1000", f"START={k * 60000}", f"END={(k + 1) * 60000}", f"title=Part {k + 1}")]
    (d / "meta.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    # 12 minutes, sound with a 4 s pause every 2 minutes.
    built = ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=720", "-f", "lavfi", "-i", "aevalsrc=0.3*sin(2*PI*300*t)*gt(mod(t\\,120)\\,4):s=16000:d=720",
               "-i", d / "talk.srt", "-i", d / "meta.txt", "-map", "0", "-map", "1", "-map", "2", "-map_chapters", "3", "-c:v", "libx264", "-preset", "ultrafast",
               "-g", "50", "-c:a", "aac", "-b:a", "32k", "-c:s", "srt", d / "long.mkv")
    if not check("big: fixture", built, "ffmpeg failed"):
        return
    long = d / "long.mkv"
    r = run("media_info.py", long, "--map", "--format", "json")
    segs = (js(r).get("map") or {}).get("segments", [])
    check("big: --map splits at the pauses", r.returncode == 0 and len(segs) == 6 and all(near(x["start"] % 120, 4.0, 0.2) for x in segs), segs)
    r = run("media_info.py", long, "--map")
    ok("big: map lists drill-down commands", r, "## Map: 6 segment(s)", "media_frames.py sheet", "--start")
    t0 = time.monotonic()
    r = run("media_frames.py", "sheet", long, "--out", d / "sheet.png", "--format", "json")
    cold = time.monotonic() - t0
    sh = (js(r).get("sheets") or [{}])[0]
    check("big: 12-minute sheet", r.returncode == 0 and sh.get("frames") == 16 and sh.get("last", 0) > 600 and cold < 20, (cold, sh, r.stderr[-200:]))
    t0 = time.monotonic()
    r = run("media_frames.py", "sheet", long, "--out", d / "sheet2.png", "--format", "json")
    check("big: repeated sheet comes from the cache", r.returncode == 0 and js(r).get("cached") is True and (d / "sheet2.png").stat().st_size == (d / "sheet.png").stat().st_size,
          (js(r).get("cached"), time.monotonic() - t0))
    # Where is it said? Subtitle search returns the cue and its time, with context.
    r = run("subtitles.py", "find", long, "Quarterly  Budget!", "--format", "json")
    hit = (js(r) or [{}])[0] if isinstance(js(r), list) else {}
    check("big: subtitles find gives the address", r.returncode == 0 and hit.get("n") == 248 and near(hit.get("start"), 494.0, 0.2) and len(hit.get("before", [])) == 1, (hit, r.stderr[-200:]))
    ok("big: grep is find", run("subtitles.py", "grep", long, "quarterly budget"), "#248 [8:14", "» we should revisit")
    # Paging: read stops within the budget and names the exact command for the next part; running it continues.
    r = run("subtitles.py", "read", long, "--max-chars", "2000")
    nxt = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    check("big: read pages within --max-chars", r.returncode == 0 and len(r.stdout) <= 2100 and "Next part: python3 scripts/subtitles.py read" in nxt, (len(r.stdout), nxt))
    import re
    import shlex

    m = re.search(r"Next part: (.*)\]$", nxt)
    if m:
        argv = shlex.split(m.group(1))
        r2 = run("subtitles.py", *argv[2:])
        first = next((ln for ln in r2.stdout.splitlines() if ln.startswith("#")), "")
        want = re.search(r"--offset (\d+)", m.group(1))
        check("big: the next-part command continues", r2.returncode == 0 and want and first.startswith(f"#{int(want.group(1)) + 1} "), (first, m.group(1)))
    else:
        check("big: the next-part command continues", False, nxt)
    r = run("subtitles.py", "extract", long, "-", "--max-chars", "2000")
    ok("big: extract - is timestamped and paged", r, "#1 [0:00", "Next part:")
    r = run("media_audio.py", "silence", long, "--min", "2", "--format", "json")
    check("big: silence ranges at the pauses", r.returncode == 0 and js(r).get("total") == 6, js(r).get("total"))
    # The cache: a repeated analysis is at least 5x faster.
    t0 = time.monotonic()
    r = run("media_audio.py", "stats", long, "--format", "json")
    cold = time.monotonic() - t0
    t0 = time.monotonic()
    r2 = run("media_audio.py", "stats", long, "--format", "json")
    warm = time.monotonic() - t0
    check("big: cached stats at least 5x faster", r.returncode == 0 and r2.returncode == 0 and js(r) == js(r2) and cold >= 5 * warm, (round(cold, 3), round(warm, 3)))
    r3 = run("media_audio.py", "stats", long, "--format", "json", "--no-cache")
    check("big: --no-cache recomputes the same", r3.returncode == 0 and js(r3).get("integrated_lufs") == js(r).get("integrated_lufs"), r3.stderr[-200:])
    # A folder: totals per folder first, then the files in pages; rows name their folder.
    lib = d / "lib"
    for sub in ("x", "y", "z"):
        (lib / sub).mkdir(parents=True)
        for k in range(6):
            ff("-f", "lavfi", "-i", f"sine=f={300 + 50 * k}:d=0.5", "-c:a", "pcm_s16le", lib / sub / f"memo{k}.wav")
    r = run("media_info.py", lib, "-r", "--max-chars", "2000")
    check("big: folder map and paged rows", r.returncode == 0 and "| x | 6 |" in r.stdout and "x/memo0.wav" in r.stdout and "Next part: python3 scripts/media_info.py" in r.stdout,
          r.stdout[-600:])
    r = run("media_info.py", lib, "-r", "--format", "json", "--max-chars", "4000")
    rows = js(r)
    check("big: JSON pages without cutting an item", r.returncode == 0 and isinstance(rows, list) and 0 < len(rows) < 18 and "Next part:" in r.stderr, (len(rows) if isinstance(rows, list) else rows, r.stderr[-200:]))


RAISING_STUB = r"""
import runpy, sys
sys.path.insert(0, SCRIPTS)
import faster_whisper
class Boom:
    def __init__(self, *a, **k):
        raise RuntimeError("the model must not be loaded: the transcript is cached")
faster_whisper.WhisperModel = Boom
sys.argv = ARGV
runpy.run_path(SCRIPTS + "/media_transcribe.py", run_name="__main__")
"""


def transcript_cache_checks(d: Path, md: Path) -> None:
    """After the stub run: the same transcript again needs no model (a stub that refuses to load proves it), --find
    returns times with context, and --offset/--max-chars page it."""
    base = ["media_transcribe.py", str(FX / "v.mp4"), "--model-dir", str(md), "--words", "--start", "0.5", "--chunk-minutes", "0.05"]

    def stub(*extra: str) -> subprocess.CompletedProcess[str]:
        code = RAISING_STUB.replace("SCRIPTS", repr(str(HERE))).replace("ARGV", repr([*base, *extra]))
        return subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(d), timeout=120,
                              env={**os.environ, "DESK_QUIET": "1", "DESK_FILE_CACHE": str(CACHE)})

    r = stub("--format", "json")
    j = js(r)
    check("transcribe: cached transcript needs no model", r.returncode == 0 and j.get("cached") is True and j.get("segments", 0) >= 4, (r.stderr[-400:], j.get("cached")))
    r = stub("--find", "w1_1_3")
    check("transcribe: --find gives times and context", r.returncode == 0 and "1 hit(s)" in r.stdout and "» w1_1_0" in r.stdout and "[0:" in r.stdout, r.stdout[-400:] or r.stderr[-300:])
    r = stub("--grep", "W1_1_3", "--format", "json")
    hits = js(r).get("hits") or [{}]
    check("transcribe: --grep JSON hit with its time", r.returncode == 0 and js(r).get("total") == 1 and near(hits[0].get("start"), 1.5, 0.01) and hits[0].get("before"), (hits, r.stderr[-300:]))
    r = stub("--out", str(d / "again.srt"))
    check("transcribe: outputs written from the cache", r.returncode == 0 and (d / "again.srt").exists() and "from the cache" in r.stdout, (r.stdout[:300], r.stderr[-200:]))
    # Paging: 90 s of audio (one stub segment per second) printed within 2000 characters, then the next part from the cache.
    import re
    import shlex

    ff("-f", "lavfi", "-i", "sine=f=300:d=90", "-ac", "1", "-ar", "16000", d / "talk.wav")
    argv = ["media_transcribe.py", str(d / "talk.wav"), "--model-dir", str(md), "--chunk-minutes", "0.5", "--max-chars", "2000"]
    code = STUB.replace("SCRIPTS", repr(str(HERE))).replace("ARGV", repr(argv))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(d), timeout=120,
                       env={**os.environ, "DESK_QUIET": "1", "DESK_FILE_CACHE": str(CACHE)})
    m = re.search(r"Next part: (.*)\]$", r.stdout.strip())
    check("transcribe: long transcript paged", r.returncode == 0 and len(r.stdout) <= 2100 and m, (len(r.stdout), r.stdout[-300:], r.stderr[-300:]))
    if m:
        nxt = shlex.split(m.group(1))
        off = int(nxt[nxt.index("--offset") + 1])
        code = RAISING_STUB.replace("SCRIPTS", repr(str(HERE))).replace("ARGV", repr(["media_transcribe.py", *nxt[2:]]))
        r2 = subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(d), timeout=120,
                            env={**os.environ, "DESK_QUIET": "1", "DESK_FILE_CACHE": str(CACHE)})
        first = re.search(r"^\[(\d+):(\d+)", r2.stdout, re.M)
        check("transcribe: the next part continues from the cache", r2.returncode == 0 and first and int(first.group(1)) * 60 + int(first.group(2)) == off,
              (off, r2.stdout[:300], r2.stderr[-300:]))


# ── main ────────────────────────────────────────────────────────────────


def guarded(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 — a crash in a group is a failure, not the end of the run
        check(f"{fn.__name__} crashed", False, traceback.format_exc()[-1500:])


def main() -> int:
    try:
        fixtures()
    except Exception:  # noqa: BLE001
        print("error: could not build the fixtures\n" + traceback.format_exc()[-1500:], file=sys.stderr)
        return 1
    groups = [g_big, g_regress_convert, g_edit_video, g_regress_edit, g_edit_time, g_edit_subs, g_edit_audio, g_convert, g_frames, g_audio, g_subtitles, g_transcribe, g_info]
    try:
        cap = max(1, int(os.environ.get("DESK_MAX_WORKERS", "5")))
    except ValueError:
        cap = 5
    workers = max(1, min(5, cap, os.cpu_count() or 2))  # DESK_MAX_WORKERS bounds the ffmpeg processes running at once
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(guarded, groups))
    failed = [r for r in RESULTS if not r[1]]
    secs = time.monotonic() - T0
    if not os.environ.get("DESK_SELFTEST_KEEP"):
        shutil.rmtree(WORK, ignore_errors=True)
    else:
        print(f"kept {WORK}")
    if failed:
        for name, _, detail in failed:
            print(f"FAIL {name}: {detail}")
        print(f"failed: {len(failed)} of {len(RESULTS)} checks in {secs:.1f}s")
        return 1
    print(f"ok: {len(RESULTS)} checks in {secs:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
