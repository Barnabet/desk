#!/usr/bin/env python3
"""Describe audio and video files: container, duration, streams, chapters, tags; a table for many files."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, cap, emit, md_table, parser, pool_map, run_main  # noqa: E402
from _paging import add_paging, check_paging, emit_json_page, page_text  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_info.py talk.mp4
  python3 scripts/media_info.py talk.mp4 --format json
  python3 scripts/media_info.py music/ --recursive          # one table row per file, with totals
  python3 scripts/media_info.py "clips/*.mov"               # globs are expanded by the script
  python3 scripts/media_info.py talk.mp4 --keyframes        # keyframe times (where a fast trim can cut)
  python3 scripts/media_info.py talk.mp4 --count-frames     # exact frame and packet counts (reads the file)
  python3 scripts/media_info.py lecture.mp4 --map           # long media: sound segments split at pauses, as time addresses
  python3 scripts/media_info.py archive/ -r --offset 200    # a big folder: per-folder totals, then files 201 on

Results are cached per file content: asking again is instant (--no-cache recomputes).
"""


def main() -> int:
    p = parser("Describe audio and video files: container, duration, size, bitrate, every stream (codec, profile, "
               "resolution, fps, pixel format, HDR and colour, rotation, sample rate, channels, subtitle languages), "
               "chapters and tags. Several files give a table.", EPILOG)
    p.add_argument("inputs", nargs="+", help="files, folders or glob patterns")
    p.add_argument("--recursive", "-r", action="store_true", help="look into sub-folders")
    p.add_argument("--keyframes", action="store_true", help="list the main video's keyframe times")
    p.add_argument("--count-frames", action="store_true", help="count frames exactly by reading every packet")
    p.add_argument("--map", action="store_true", help="add a map of the content: sound segments split at pauses (silences), with start-end times "
                                                          "to drill into (decodes the audio once, then cached)")
    p.add_argument("--min-gap", type=float, default=1.0, help="--map: shortest pause that splits segments, seconds (default 1)")
    p.add_argument("--no-cache", action="store_true", help="probe again instead of reusing cached results")
    add_paging(p, "files (or keyframes)", "at most N files in a batch, or N keyframes for --keyframes (default 200)")
    add_format(p)
    a = p.parse_args()
    check_paging(a)
    if a.no_cache:
        import os

        os.environ["DESK_NO_CACHE"] = "1"

    from _media import expand_inputs

    files = expand_inputs(a.inputs, recursive=a.recursive)
    if len(files) == 1:
        info = describe(files[0], a.keyframes, a.count_frames, a.limit or 200)
        if a.map:
            info["map"] = content_map(info, a.min_gap)
        emit(info, a.format, render_one, max_chars=a.max_chars, hint="Use --format json for everything, or --keyframes with --limit.")
        return 0
    if a.map or a.keyframes or a.count_frames:
        raise UsageError("--map, --keyframes and --count-frames take one file")
    results = pool_map(_describe_safe, [str(f) for f in files], threads=True)
    root = _common_root(files)
    for r, f in zip(results, files):
        r["path"] = _rel(f, root)
    total = len(results)
    page = results[a.offset:][: a.limit] if a.limit else results[a.offset:]
    if a.format == "json":
        emit_json_page(page, a.offset, total, a.max_chars)
    else:
        head = render_summary(results, root)
        rows = [_row(r) for r in page]
        print(page_text(head, rows, a.offset, total, a.max_chars, "files"))
    return 1 if all("error" in r for r in results) else 0


def _common_root(files: list[Path]) -> Path:
    import os

    try:
        return Path(os.path.commonpath([str(f.resolve().parent) for f in files]))
    except ValueError:  # different drives on Windows
        return Path()


def _rel(f: Path, root: Path) -> str:
    try:
        return f.resolve().relative_to(root).as_posix() if str(root) not in ("", ".") else str(f)
    except ValueError:
        return str(f)


def _describe_safe(path: str) -> dict[str, Any]:
    try:
        return describe(Path(path), False, False, 0)
    except SkillError as e:
        return {"file": path, "name": Path(path).name, "size": Path(path).stat().st_size if Path(path).exists() else 0, "error": str(e)}


def describe(path: Path, keyframes: bool, count_frames: bool, limit: int) -> dict[str, Any]:
    from _media import probe

    info = probe(path)
    if keyframes or count_frames:
        try:
            import _cache

            scan = _cache.cached_json(path, "av-packets", {"keys": keyframes, "count": count_frames}, "1", lambda: _scan(path, dict(info), keyframes, count_frames))
        except OSError:
            scan = _scan(path, dict(info), keyframes, count_frames)
        for s in info["streams"]:
            n = scan["packets"].get(str(s["index"]))
            if n is not None:
                s["packets"] = n
                if s["type"] == "video":
                    s["frames"] = n
        if keyframes and scan.get("keyframes") is not None:
            kf = scan["keyframes"]
            info["keyframes"] = {**kf, "times": kf["times"][:limit], "listed": min(limit, len(kf["times"]))}
    return info


def _scan(path: Path, info: dict[str, Any], keyframes: bool, count_frames: bool) -> dict[str, Any]:
    """Packet counts per stream and every keyframe time of the main video (JSON-ready, for the cache)."""
    _scan_packets(path, info, keyframes, count_frames, 10**9)
    return {"packets": {str(s["index"]): s["packets"] for s in info["streams"] if "packets" in s}, "keyframes": info.get("keyframes")}


def _scan_packets(path: Path, info: dict[str, Any], keyframes: bool, count_frames: bool, limit: int) -> None:
    from _media import main_video, open_container

    v = main_video(info)
    c = open_container(path)
    try:
        counts: dict[int, int] = {}
        keys: list[float] = []
        nkeys = 0
        vs = c.streams[v["index"]] if v else None
        streams = list(c.streams) if count_frames else ([vs] if vs is not None else [])
        if not streams:
            raise SkillError(f"{path.name} has no video stream (keyframes are a video notion)")
        for pkt in c.demux(*streams):
            if pkt.size == 0:
                continue
            counts[pkt.stream.index] = counts.get(pkt.stream.index, 0) + 1
            if keyframes and vs is not None and pkt.stream.index == vs.index and pkt.is_keyframe and pkt.pts is not None:
                nkeys += 1
                if len(keys) < limit:
                    keys.append(round(float(pkt.pts * pkt.time_base) - (info.get("start") or 0.0), 3))
        for s in info["streams"]:
            if s["index"] in counts:
                s["packets"] = counts[s["index"]]
                if s["type"] == "video":
                    s["frames"] = counts[s["index"]]
        if keyframes and v is not None:
            keys.sort()
            info["keyframes"] = {"count": nkeys, "times": keys, "listed": len(keys)}
            if nkeys > 1 and info.get("duration"):
                info["keyframes"]["average_interval"] = round(info["duration"] / nkeys, 3)
    finally:
        c.close()


# ── map ─────────────────────────────────────────────────────────────────


def content_map(info: dict[str, Any], min_gap: float = 1.0) -> dict[str, Any]:
    """Segments of sound between pauses, as start-end addresses: where to look, listen or transcribe next."""
    from _media import main_audio, require_duration

    d = require_duration(info)
    out: dict[str, Any] = {"duration": d, "segments": [], "method": ""}
    if not main_audio(info):
        n = max(1, min(20, round(d / 60)))
        out["segments"] = [{"start": round(d * i / n, 3), "end": round(d * (i + 1) / n, 3)} for i in range(n)]
        out["method"] = "no audio: equal parts (media_frames.py frames --scenes finds shots)"
        return out
    import numpy as np

    from _audio import auto_threshold, levels, silence_ranges

    lv, fs = levels(info["file"])
    if not lv.size:
        return out
    thr = auto_threshold(lv)
    sil = silence_ranges(lv, fs, thr, max(0.2, min_gap))
    total = len(lv) * fs
    min_len = max(5.0, total / 150)
    want = max(3, min(40, int(total / 20)))
    # The longest pauses split first, as long as every piece stays at least min_len long.
    cuts: list[tuple[float, float]] = []
    for a, b in sorted(sil, key=lambda x: x[0] - x[1]):
        if len(cuts) >= want - 1:
            break
        if a < min_len * 0.5 or total - b < min_len * 0.5:
            continue  # leading or trailing silence: trimmed below instead
        mid = (a + b) / 2
        if all(abs(mid - (x + y) / 2) >= min_len for x, y in cuts):
            cuts.append((a, b))
    cuts.sort()
    lead = next((b for a, b in sil if a <= fs), 0.0)
    tail = next((a for a, b in sil if b >= total - fs), total)
    bounds = [lead] + [x for c in cuts for x in c] + [tail]
    segs = []
    for i in range(0, len(bounds) - 1, 2):
        a, b = bounds[i], bounds[i + 1]
        if b - a < 0.05:
            continue
        seg = lv[int(a / fs):max(int(a / fs) + 1, int(b / fs))]
        loud = seg[seg > thr]
        rms = 10 * np.log10(np.mean(10 ** (loud / 10))) if loud.size else float(seg.max())
        segs.append({"start": round(a, 3), "end": round(b, 3), "rms_dbfs": round(float(rms), 1), "sound_share": round(float(loud.size / max(1, seg.size)), 2)})
    if len(segs) < 2 and total > 120:
        # No usable pauses (music, noise): equal parts cut at the quietest moment near each boundary.
        n = max(2, min(20, round(total / 90)))
        pts = [0.0]
        for i in range(1, n):
            c = int(total * i / n / fs)
            w = int(min(10.0, total / n / 4) / fs)
            lo, hi = max(0, c - w), min(len(lv), c + w + 1)
            pts.append((lo + int(np.argmin(lv[lo:hi]))) * fs)
        pts.append(total)
        segs = [{"start": round(pts[i], 3), "end": round(pts[i + 1], 3)} for i in range(n)]
        out["method"] = "no clear pauses: equal parts, cut at the quietest moment near each boundary"
    else:
        out["method"] = f"split at the longest pauses of at least {min_gap:g} s (below {thr:.0f} dBFS)"
    out["segments"] = segs
    out["pauses"] = len(sil)
    return out


# ── Markdown ────────────────────────────────────────────────────────────


def _br(bps: Any) -> str:
    if not bps:
        return ""
    bps = float(bps)
    return f"{bps / 1_000_000:.2f} Mb/s" if bps >= 1_000_000 else f"{bps / 1000:.0f} kb/s"


def stream_details(s: dict[str, Any]) -> str:
    from _media import display_size

    bits: list[str] = []
    if s["type"] == "video":
        w, h = display_size(s)
        size = f"{s.get('width')}x{s.get('height')}"
        if (w, h) != (s.get("width"), s.get("height")):
            size += f" (shown {w}x{h})"
        bits.append(size)
        if s.get("cover_art"):
            bits.append("cover art")
        elif s.get("fps"):
            bits.append(f"{s['fps']:g} fps" + (f" variable (up to {s['fps_max']:g})" if s.get("vfr") else ""))
        if s.get("pix_fmt"):
            bits.append(f"{s['pix_fmt']} {s.get('bit_depth') or 8}-bit")
        col = s.get("color") or {}
        if col:
            bits.append("/".join(v.split(" ")[0] for k, v in col.items() if k in ("primaries", "transfer", "matrix")) + (f" {col['range'].split(' ')[0]}" if col.get("range") else ""))
        if s.get("hdr"):
            bits.append(f"**{s['hdr']}**")
        if s.get("rotation"):
            bits.append(f"rotation {s['rotation']}°")
        if s.get("interlaced"):
            bits.append(f"interlaced ({s['interlaced']})")
        if s.get("sar"):
            bits.append(f"SAR {s['sar']}")
    elif s["type"] == "audio":
        if s.get("sample_rate"):
            bits.append(f"{s['sample_rate'] / 1000:g} kHz")
        if s.get("layout") or s.get("channels"):
            lay = str(s.get("layout") or "")
            if not lay or lay.endswith(("channel", "channels")):
                lay = {1: "mono", 2: "stereo"}.get(int(s.get("channels") or 0), "")
            bits.append(f"{lay} ({s.get('channels')} ch)".strip())
        if s.get("bit_depth"):
            bits.append(f"{s['bit_depth']}-bit")
    elif s["type"] == "subtitle":
        bits.append("text" if s.get("text_based") else "image-based (bitmaps)")
    elif s["type"] == "attachment":
        bits.append(s.get("filename") or "")
        if s.get("mimetype"):
            bits.append(s["mimetype"])
    elif s.get("tags", {}).get("timecode"):
        bits.append(f"timecode {s['tags']['timecode']}")
    disp = [d for d in s.get("disposition", []) if d not in ("attached_pic",)]
    if disp:
        bits.append("[" + ", ".join(disp) + "]")
    if s.get("title"):
        bits.append(f'"{s["title"]}"')
    if s.get("frames"):
        bits.append(f"{s['frames']} frames")
    return " · ".join(b for b in bits if b)


def render_one(info: dict[str, Any]) -> str:
    from _media import fmt_dur, fmt_size, fmt_stamp, fmt_time

    lines = [f"# {info['name']}", ""]
    dur = fmt_dur(info.get("duration")) + (" (estimated)" if info.get("duration_estimated") else "")
    lines.append(f"- **Container:** {info['container_long']} ({info['container']}) · {dur} · {fmt_size(info['size'])} · {_br(info.get('bit_rate'))}")
    tags = info.get("tags") or {}
    for key in ("title", "artist", "album", "date", "creation_time", "comment", "encoder"):
        for k, v in tags.items():
            if k.lower() == key:
                lines.append(f"- **{k}:** {v}")
    lines += ["", "## Streams", ""]
    rows = []
    for s in info["streams"]:
        codec = s.get("codec") or "?"
        if s.get("profile"):
            codec += f" ({s['profile']})"
        rows.append([s["index"], s["type"], codec, stream_details(s), s.get("language", ""), _br(s.get("bit_rate"))])
    lines.append(md_table(["#", "type", "codec", "details", "lang", "bitrate"], rows))
    for s in info["streams"]:
        extra = {k: s[k] for k in ("dolby_vision", "mastering_display", "content_light_level", "spherical", "stereo3d") if s.get(k)}
        for k, v in extra.items():
            lines.append(f"- stream {s['index']} {k.replace('_', ' ')}: {v}")
    if info.get("chapters"):
        lines += ["", f"## Chapters ({len(info['chapters'])})", ""]
        lines.append(md_table(["#", "start", "end", "title"], [[i + 1, fmt_time(c["start"]), fmt_time(c["end"]), c["title"]] for i, c in enumerate(info["chapters"])]))
    kf = info.get("keyframes")
    if kf:
        lines += ["", f"## Keyframes ({kf['count']}" + (f", every {kf['average_interval']:g}s on average" if kf.get("average_interval") else "") + ")", ""]
        lines.append(", ".join(fmt_time(t) for t in kf["times"]) + (" …" if kf["count"] > kf["listed"] else ""))
    other = {k: v for k, v in tags.items() if k.lower() not in ("title", "artist", "album", "date", "creation_time", "comment", "encoder", "major_brand", "minor_version", "compatible_brands")}
    if other:
        lines += ["", "## Tags", ""]
        lines += [f"- {k}: {v}" for k, v in list(other.items())[:60]]
    stream_tags = [(s["index"], s["tags"]) for s in info["streams"] if s.get("tags")]
    if stream_tags:
        lines += ["", "## Stream tags", ""]
        for idx, t in stream_tags:
            lines.append(f"- stream {idx}: " + ", ".join(f"{k}={v}" for k, v in list(t.items())[:12]))
    mp = info.get("map")
    if mp is not None:
        segs = mp.get("segments") or []
        lines += ["", f"## Map: {len(segs)} segment(s)", "", f"{mp.get('method', '')}. Times are addresses for the drill-down commands below.", ""]
        rows = [[i + 1, fmt_stamp(x["start"]), fmt_stamp(x["end"]), fmt_dur(x["end"] - x["start"]), x.get("rms_dbfs", "")] for i, x in enumerate(segs)]
        lines.append(md_table(["#", "start", "end", "length", "level dBFS"], rows))
        if segs:
            import shlex

            f = shlex.quote(str(info["file"]))
            x = segs[min(1, len(segs) - 1)]
            ab = f"--start {fmt_stamp(x['start'])} --end {fmt_stamp(x['end'])}"
            has_text = any(st.get("type") == "subtitle" and st.get("text_based") for st in info.get("streams", []))
            lines += ["", "Drill down into a segment:",
                      f"- see it: `python3 scripts/media_frames.py sheet {f} {ab}`" if main_video_of(info) else f"- see it: `python3 scripts/media_audio.py waveform {f} {ab}`",
                      f"- hear what is said: `python3 scripts/media_transcribe.py {f} {ab}`",
                      f"- find words: `python3 scripts/subtitles.py find {f} \"…\"` (its subtitle track), or `media_transcribe.py {f} --find \"…\"`"
                      if has_text else f"- find words: `python3 scripts/media_transcribe.py {f} --find \"…\"` (transcribes once, then searches the cached transcript)"]
    elif (info.get("duration") or 0) > 600 and info.get("kind") in ("video", "audio"):
        lines += ["", f"Long media: `--map` lists its segments between pauses with start-end times to drill into; `media_frames.py sheet` shows it."]
    return "\n".join(lines)


def main_video_of(info: dict[str, Any]) -> bool:
    from _media import main_video

    return main_video(info) is not None


def render_summary(results: list[dict[str, Any]], root: Path) -> str:
    """Totals, and per-folder totals when the files span several folders: the map of a big batch."""
    from _media import fmt_dur, fmt_size

    ok = [r for r in results if "error" not in r]
    total_d = sum(r.get("duration") or 0.0 for r in ok)
    total_s = sum(r.get("size") or 0 for r in results)
    kinds: dict[str, int] = {}
    for r in ok:
        kinds[r.get("kind", "other")] = kinds.get(r.get("kind", "other"), 0) + 1
    head = (f"{len(results)} files under {root if str(root) not in ('', '.') else 'the current folder'} · {len(ok)} readable ("
            + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + f") · total {fmt_dur(total_d)} · {fmt_size(total_s)}")
    folders: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        folders.setdefault(str(Path(r["path"]).parent.as_posix()), []).append(r)
    if len(folders) > 1:
        rows = []
        for f, rs in sorted(folders.items()):
            good = [r for r in rs if "error" not in r]
            rows.append([f if f != "." else "(top)", len(rs), len(rs) - len(good), fmt_dur(sum(r.get("duration") or 0.0 for r in good)),
                         fmt_size(sum(r.get("size") or 0 for r in rs))])
        head += "\n\n" + md_table(["folder", "files", "unreadable", "duration", "size"], rows)
    head += "\n\n| file | kind | duration | size | video | audio | subs |\n|---|---|---|---|---|---|---|"
    return head


def _row(r: dict[str, Any]) -> str:
    from _common import md_escape_cell
    from _media import display_size, fmt_dur, fmt_size, main_audio, main_video

    if "error" in r:
        cells = [r["path"], "error", "", fmt_size(r.get("size")), r["error"][:100], "", ""]
    else:
        v = main_video(r)
        a = main_audio(r)
        vtxt = ""
        if v:
            w, h = display_size(v)
            vtxt = f"{v.get('codec')} {w}x{h}" + (f" {v['fps']:g}fps" if v.get("fps") else "") + (f" {v['hdr']}" if v.get("hdr") else "")
        atxt = ""
        if a:
            atxt = f"{a.get('codec')} {(a.get('sample_rate') or 0) / 1000:g}k {a.get('channels')}ch"
            n = sum(1 for s in r["streams"] if s["type"] == "audio")
            if n > 1:
                atxt += f" (+{n - 1})"
        subs = [s.get("language") or s.get("codec") or "?" for s in r["streams"] if s["type"] == "subtitle"]
        cells = [r["path"], r.get("kind", ""), fmt_dur(r.get("duration")), fmt_size(r["size"]), vtxt, atxt, ", ".join(subs)]
    return "| " + " | ".join(md_escape_cell(c) for c in cells) + " |"


if __name__ == "__main__":
    run_main(main)
