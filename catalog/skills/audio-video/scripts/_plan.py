"""Output planning for the audio-video skill: presets, which streams to keep, copy-or-encode decisions per stream,
codec options, subtitles and cover art per container. Shared by media_convert and media_edit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError

#: Named presets: output extension plus codec and quality choices. `caps` keep web outputs within a size and rate.
PRESETS: dict[str, dict[str, Any]] = {
    "web": {"ext": ".mp4", "v": "h264", "a": "aac", "crf": 23, "ab": "128k", "max_edge": 1920, "max_short": 1080, "max_fps": 60, "copy_ok": "web",
            "about": "MP4, H.264 + AAC, fast start, within 1920x1080 (1080x1920 portrait) and 60 fps: plays everywhere"},
    "webm": {"ext": ".webm", "v": "vp9", "a": "opus", "crf": 32, "ab": "96k", "max_edge": 1920, "max_short": 1080, "max_fps": 60,
             "about": "WebM, VP9 + Opus: smaller than H.264 at the same quality, for browsers"},
    "hevc": {"ext": ".mp4", "v": "hevc", "a": "aac", "crf": 26, "ab": "128k",
             "about": "MP4, H.265/HEVC + AAC: about half the size of H.264; keeps 10-bit"},
    "av1": {"ext": ".mp4", "v": "av1", "a": "opus", "crf": 35, "ab": "96k",
            "about": "MP4, AV1 + Opus: the smallest files, slower to encode, newer players only"},
    "email": {"ext": ".mp4", "v": "h264", "a": "aac", "crf": 28, "ab": "96k", "max_edge": 1280, "max_short": 720, "max_fps": 30, "target_mb": 20, "copy_ok": "email",
              "about": "small MP4 for attachments: within 1280x720 and 30 fps, capped at about 20 MB (change with --target-mb)"},
    "edit": {"ext": ".mov", "v": "prores", "a": "pcm", "about": "MOV, ProRes 422 + PCM: an intermediate for video editors (large)"},
    "gif": {"ext": ".gif", "v": "gif", "fps": 12, "width": 480, "about": "animated GIF with an optimised palette (480 px, 12 fps)"},
    "mp3": {"ext": ".mp3", "a": "mp3", "aq": 2, "about": "MP3, VBR about 190 kb/s (or --audio-bitrate)"},
    "m4a": {"ext": ".m4a", "a": "aac", "ab": "192k", "about": "M4A, AAC 192 kb/s"},
    "aac": {"ext": ".m4a", "a": "aac", "ab": "192k", "about": "same as m4a"},
    "opus": {"ext": ".opus", "a": "opus", "ab": "96k", "about": "Opus 96 kb/s: the best quality per bit"},
    "ogg": {"ext": ".ogg", "a": "vorbis", "about": "Ogg Vorbis, quality 5 (about 160 kb/s)"},
    "wav": {"ext": ".wav", "a": "pcm", "about": "WAV PCM (16-bit, or 24-bit when the source has more)"},
    "flac": {"ext": ".flac", "a": "flac", "about": "FLAC, lossless"},
    "aiff": {"ext": ".aiff", "a": "pcm_be", "about": "AIFF PCM"},
    "alac": {"ext": ".m4a", "a": "alac", "about": "Apple Lossless in M4A"},
    "wma": {"ext": ".wma", "a": "wmav2", "ab": "192k", "about": "Windows Media Audio"},
    "voice": {"ext": ".m4a", "a": "aac", "ab": "64k", "channels": 1, "sample_rate": 24000, "about": "speech: mono AAC 64 kb/s, 24 kHz"},
}
PRESETS["web-mp4"] = PRESETS["web"]
PRESETS["mp4"] = PRESETS["web"]


@dataclass
class Options:
    """What the caller asked for (all optional)."""

    preset: str | None = None
    vcodec: str | None = None
    acodec: str | None = None
    crf: float | None = None
    vbitrate: str | None = None
    abitrate: str | None = None
    size: str | None = None
    fps: float | None = None
    channels: int | None = None
    sample_rate: int | None = None
    speed: str = "medium"
    target_mb: float | None = None
    audio_track: int | None = None
    no_audio: bool = False
    no_video: bool = False
    no_subs: bool = False
    copy: bool = False
    reencode: bool = False
    strip_metadata: bool = False
    prefer_copy: bool = False  # copy every stream the container can hold (remux), not only those that play well there
    tonemap: str = "auto"  # auto | on | off
    deinterlace: str = "auto"
    extra_vf: list[str] = field(default_factory=list)
    extra_af: list[str] = field(default_factory=list)


@dataclass
class Plan:
    args: list[str]
    notes: list[str]
    video: str | None  # "copy", an encoder name, or None
    audio: str | None
    two_pass: bool = False
    video_kbps: int | None = None
    audio_kbps: int | None = None


def preset(name: str | None) -> dict[str, Any]:
    if not name:
        return {}
    p = PRESETS.get(name.lower())
    if p is None:
        raise UsageError(f"unknown preset '{name}'; choose from: {', '.join(sorted(PRESETS))}")
    return p


def _web_compatible(v: dict[str, Any], pr: dict[str, Any]) -> bool:
    """A video stream that can be copied as-is into a web MP4 (H.264 8-bit 4:2:0 within the caps, not HDR)."""
    from _media import display_size, is_hdr

    if v.get("codec") != "h264" or v.get("pix_fmt") not in ("yuv420p", "yuvj420p") or is_hdr(v):
        return False
    w, h = display_size(v)
    if pr.get("max_edge") and max(w, h) > pr["max_edge"]:
        return False
    if pr.get("max_short") and min(w, h) > pr["max_short"]:
        return False
    fps = v.get("fps_max") or v.get("fps") or 0
    if pr.get("max_fps") and fps > pr["max_fps"] + 0.01:
        return False
    return not v.get("interlaced")


_PLAYS_NOTE = {"vp9", "vp8", "av1", "opus", "flac", "mpeg2video", "mjpeg"}


def plan_streams(info: dict[str, Any], out: Path, o: Options, duration: float | None = None, video_filters: list[str] | None = None, audio_filters: list[str] | None = None, input_index: int = 0) -> Plan:
    """Mapping and codec arguments to write `out` from input `input_index` (an ffmpeg input already on the command line).

    `video_filters` / `audio_filters` are extra filters the caller needs (they force re-encoding of that stream).
    Streams are copied when nothing asks for a change and the container holds them well (`o.prefer_copy`: whenever
    the container can hold them at all, as remux does).
    """
    from _media import (AUDIO_CODECS, VIDEO_CODECS, audio_encoder_args, audio_stream_pos, auto_audio_bitrate, bitrate_value, brand_cleanup, can_copy,
                        cap_filter, container_for, encoder_for, fit_to_bitrate, is_hdr, main_video, norm_codec, parse_bitrate, scale_filter,
                        sub_target, tonemap_filter, video_encoder_args)

    cont = container_for(out)
    pr = preset(o.preset)
    notes: list[str] = []
    args: list[str] = []
    vf = list(video_filters or []) + list(o.extra_vf)
    af = list(audio_filters or []) + list(o.extra_af)
    ii = input_index
    ext = out.suffix.lower()

    v = main_video(info)
    auds = [s for s in info["streams"] if s["type"] == "audio"]
    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    covers = [s for s in info["streams"] if s["type"] == "video" and s.get("cover_art")]
    want_v = bool(v) and not o.no_video and not cont.get("audio_only")
    want_a = bool(auds) and not o.no_audio and not cont.get("video_only")
    if not want_v and not want_a:
        if cont.get("audio_only") and not auds:
            raise SkillError(f"{info['name']} has no audio to put in a {out.suffix} file")
        if cont.get("video_only") and not v:
            raise SkillError(f"{info['name']} has no video")
        raise SkillError("nothing to write: both audio and video are excluded")

    # Which audio tracks, and (for a size target) the exact audio bitrate, which the video budget depends on.
    chosen: list[int] = []
    acodec = norm_codec(o.acodec) if o.acodec else (pr.get("a") or cont.get("a"))
    if want_a:
        if cont.get("audio_only") or o.audio_track is not None or ext in (".avi", ".wmv", ".asf", ".mpg", ".mpeg", ".flv", ".ogv"):
            chosen = [audio_stream_pos(info, o.audio_track)]
        else:
            chosen = list(range(len(auds)))
    target_mb = o.target_mb if o.target_mb is not None else pr.get("target_mb")
    size_target_av = target_mb is not None and want_v and not (o.preset and pr.get("crf") is not None and o.target_mb is None)
    target_abr: int | None = None
    if target_mb is not None and want_v and want_a:
        want = bitrate_value(o.abitrate or pr.get("ab") or "128k")
        ch_out = int(o.channels or pr.get("channels") or 0) or max(int(auds[p].get("channels") or 2) for p in chosen)
        target_abr = min(want, int(bitrate_value(auto_audio_bitrate("aac", str(want), min(ch_out, 2), None) or want)))
        src_br = max((int(auds[p].get("bit_rate") or 0) for p in chosen), default=0)
        if src_br and src_br < target_abr:
            target_abr = max(32_000, int(src_br * 1.1 // 8000 * 8000))

    # ── video ──
    vchoice: str | None = None
    two_pass = False
    vkbps: int | None = None
    akbps: int | None = None
    if want_v:
        assert v is not None
        vcodec = norm_codec(o.vcodec) if o.vcodec else (pr.get("v") or cont.get("v"))
        if vcodec and vcodec not in VIDEO_CODECS and vcodec != "copy":
            raise UsageError(f"unknown video codec '{o.vcodec}'; use one of {', '.join(sorted(VIDEO_CODECS))} or copy")
        filters: list[str] = []
        deint = o.deinterlace == "on" or (o.deinterlace == "auto" and v.get("interlaced"))
        if deint:
            filters.append("bwdif=mode=send_frame")
        # A size target: the video bitrate left after the audio, and the picture that bitrate can carry.
        fit_fps: float | None = None
        fit_size: tuple[int, int] | None = None
        if target_mb is not None:
            d = duration or info.get("duration")
            if not d:
                raise SkillError("--target-mb needs a known duration")
            budget = target_mb * 8_000_000 * 0.965 / d - (target_abr or 0) * len(chosen)
            if budget < 50_000:
                raise SkillError(f"{target_mb:g} MB is too small for {d:.0f} s of video (it leaves {max(0, budget) / 1000:.0f} kb/s for the picture); "
                                 "shorten it (--start/--end) or choose a bigger target")
            vkbps = int(budget / 1000)
            if size_target_av and vcodec not in (None, "copy", "gif"):
                W0, H0 = cap_size(v, pr, o)
                if o.size:
                    from _edit import scaled_size

                    W0, H0, _ = scaled_size(o.size, W0, H0)
                vv = {**v, "display_width": W0, "width": W0, "height": H0, "rotation": 0, "sar": None}
                cap_fps = o.fps or (pr.get("max_fps") if (v.get("fps_max") or v.get("fps") or 0) > (pr.get("max_fps") or 1e9) else None)
                W, H, F, bpp = fit_to_bitrate(vv, budget, vcodec, fixed_size=bool(o.size), fixed_fps=cap_fps)
                src_fps = float(v.get("fps_max") or v.get("fps") or 0)
                if (W, H) != (W0, H0):
                    fit_size = (W, H)
                if not cap_fps and src_fps and F < src_fps - 0.01:
                    fit_fps = F
                if fit_size or fit_fps:
                    notes.append(f"to fit {target_mb:.3g} MB at {vkbps} kb/s the picture was reduced to {W}x{H} at {F:g} fps "
                                 f"({bpp:.3f} bits per pixel)" + ("" if o.size and o.fps else "; pass --size and --fps to choose them yourself"))
                elif bpp < MIN_OK_BPP:
                    notes.append(f"only {bpp:.3f} bits per pixel at {vkbps} kb/s: expect blocky pictures; a smaller --size or lower --fps looks better")
        sizing: list[str] = []
        if o.size:
            sizing.append(scale_filter(o.size, v.get("width"), v.get("height")))
        elif fit_size:
            sizing.append(f"scale={fit_size[0]}:{fit_size[1]}:flags=lanczos,setsar=1")
        elif pr.get("width") and vcodec == "gif":
            sizing.append(f"scale={pr['width']}:-2:flags=lanczos")
        else:
            sizing += cap_filter(pr.get("max_edge"), None, v, pr.get("max_short"))
        fps = o.fps or fit_fps or (pr.get("fps") if vcodec == "gif" else None)
        rate = [f"fps={fps:g}"] if fps else cap_filter(None, pr.get("max_fps"), v)
        src_fps = float(v.get("fps_max") or v.get("fps") or 0)
        # Dropping frames before scaling saves work (240 fps 4K → 30 fps 720p scales 8x fewer frames).
        filters += rate + sizing if rate and (not fps or not src_fps or fps < src_fps) else sizing + rate
        filters += vf
        hdr_src = is_hdr(v)
        wants_8bit = vcodec in ("h264", "vp9", "vp8", "mpeg4", "mpeg2video", "wmv2", "gif", "mjpeg", "theora")
        if hdr_src and wants_8bit and o.tonemap != "off":
            tm = tonemap_filter()
            if tm:
                filters.insert(0, tm)
                notes.append("HDR converted to SDR (tone-mapped) so colours look right on normal screens")
            else:
                notes.append("the source is HDR and this ffmpeg cannot tone-map it; colours may look washed out")
        elif o.tonemap == "on" and hdr_src:
            tm = tonemap_filter()
            if tm:
                filters.insert(0, tm)
        src_codec = v.get("codec")
        can = can_copy(cont, "video", src_codec)
        plays = can_copy(cont, "video", src_codec, default=True)
        explicit_q = any(x is not None for x in (o.crf, o.vbitrate)) or target_mb is not None
        same_codec = vcodec in (None, "copy") or norm_codec(src_codec or "") == vcodec
        implicit = not pr and not o.vcodec
        copy_ok = not o.reencode and not filters and can and not explicit_q and (
            vcodec == "copy" or (implicit and (plays or o.prefer_copy)) or (not pr and same_codec and bool(o.vcodec))
            or (pr.get("copy_ok") and _web_compatible(v, pr) and same_codec))
        if vcodec == "copy" or o.copy:
            if not can:
                raise SkillError(f"{src_codec} video cannot go into {out.suffix} without re-encoding (drop --copy, or pick .mkv)")
            if filters:
                raise UsageError("--copy cannot be combined with changes to the picture (size, fps, filters)")
            copy_ok = True
        if implicit and can and not plays and not copy_ok and not o.reencode and not filters and not explicit_q:
            notes.append(f"{src_codec} video was re-encoded to {cont.get('v')} because many players cannot play {src_codec} in {out.suffix}; "
                         "--vcodec copy keeps it as it is")
        vpos = [s for s in info["streams"] if s["type"] == "video"].index(v)
        if copy_ok:
            vchoice = "copy"
            args += ["-map", f"{ii}:v:{vpos}", "-c:v", "copy"]
            if ext in (".mp4", ".mov", ".m4v") and src_codec == "hevc":
                args += ["-tag:v", "hvc1"]
            if ext in (".mp4", ".mov", ".m4v") and src_codec in _PLAYS_NOTE:
                notes.append(f"{src_codec} video copied into {out.suffix}: browsers and VLC play it, QuickTime and many TVs do not")
        else:
            if not vcodec or vcodec == "copy":
                vcodec = cont.get("v") or "h264"
            if vcodec == "gif":
                vchoice = "gif"
                graph = ",".join(filters) if filters else "null"
                src = f"[{ii}:v:{vpos}]"
                args += ["-filter_complex", f"{src}{graph},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle[gif]", "-map", "[gif]", "-loop", "0"]
                return Plan(args, notes, vchoice, None)
            args += ["-map", f"{ii}:v:{vpos}"]
            enc = encoder_for(vcodec)
            if enc.endswith(("_videotoolbox", "_mf", "_nvenc", "_qsv")):
                notes.append(f"using the {enc} encoder (no software {vcodec} encoder in this ffmpeg)")
            crf = o.crf if o.crf is not None else pr.get("crf")
            vbr = parse_bitrate(o.vbitrate)
            maxrate = None
            if vkbps is not None:
                if not size_target_av:
                    # A preset's own cap (email): quality-driven CRF, never above the size budget.
                    maxrate = f"{vkbps}k"
                else:
                    vbr = f"{vkbps}k"
                    two_pass = enc in ("libx264", "libvpx-vp9", "libvpx")
                    crf = None
            bit_depth = int(v.get("bit_depth") or 8)
            args += video_encoder_args(enc, crf if not vbr else None, vbr, o.speed, bit_depth, maxrate, f"{2 * vkbps}k" if maxrate and vkbps else None)
            if vcodec in ("hevc", "av1") and hdr_src and bit_depth > 8:
                col = v.get("color") or {}
                trc = "smpte2084" if (col.get("transfer") or "").startswith("smpte2084") else "arib-std-b67"
                filters.append(f"setparams=color_primaries=bt2020:color_trc={trc}:colorspace=bt2020nc")
                args += ["-color_primaries", "bt2020", "-colorspace", "bt2020nc", "-color_trc", trc]
                notes.append("kept HDR (10-bit BT.2020 tags); mastering metadata is not carried over")
            if filters:
                args += ["-vf", ",".join(filters)]
            elif enc in ("libx264", "mpeg4", "mpeg2video", "wmv2") and (int(v.get("width") or 0) % 2 or int(v.get("height") or 0) % 2):
                args += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
            vchoice = enc
    # ── audio ──
    achoice: str | None = None
    if want_a:
        if acodec and acodec not in AUDIO_CODECS and acodec != "copy":
            raise UsageError(f"unknown audio codec '{o.acodec}'; use one of {', '.join(sorted(AUDIO_CODECS))} or copy")
        ch = o.channels or pr.get("channels")
        sr = o.sample_rate or pr.get("sample_rate")
        abr = parse_bitrate(o.abitrate) or (f"{bitrate_value(pr['ab'])}" if pr.get("ab") else None)
        if target_abr:
            abr = str(target_abr)
            akbps = target_abr // 1000
        if target_mb is not None and not want_v:
            d = duration or info.get("duration")
            if not d:
                raise SkillError("--target-mb needs a known duration")
            kbps = int(target_mb * 8_000 * 0.98 / d)
            if kbps < 16:
                raise SkillError(f"{target_mb:g} MB is too small for {d:.0f} s of audio")
            limit = {"mp3": 320, "aac": 320, "opus": 256, "vorbis": 320, "wmav2": 320}.get(acodec or "", 320)
            if acodec in ("pcm", "pcm_be", "flac", "alac"):
                raise UsageError(f"--target-mb does not apply to lossless {acodec}; choose mp3, m4a or opus")
            kbps = min(kbps, limit)
            abr = f"{kbps * 1000}"
            akbps = kbps
        implicit_a = not o.acodec and not pr.get("a")
        for k, pos in enumerate(chosen):
            a = auds[pos]
            src_codec = a.get("codec") or ""
            can = can_copy(cont, "audio", src_codec)
            plays = can_copy(cont, "audio", src_codec, default=True)
            same = acodec in (None, "copy") or norm_codec(src_codec) == acodec or (acodec == "pcm" and src_codec.startswith("pcm_") and src_codec.endswith("le"))
            changes = bool(af) or ch or sr or o.abitrate or akbps
            args += ["-map", f"{ii}:a:{pos}"]
            if acodec == "copy" or o.copy:
                if not can:
                    raise SkillError(f"{src_codec} audio cannot go into {out.suffix} without re-encoding")
                if af:
                    raise UsageError("--copy cannot be combined with audio changes")
                args += [f"-c:a:{k}", "copy"]
                achoice = "copy"
                continue
            if not o.reencode and not changes and can and ((implicit_a and (plays or o.prefer_copy)) or (same and not (pr and pr.get("a") in ("mp3", "aac", "opus", "vorbis") and src_codec != pr.get("a")))):
                args += [f"-c:a:{k}", "copy"]
                achoice = "copy"
                if ext in (".mp4", ".mov", ".m4v") and src_codec in _PLAYS_NOTE:
                    notes.append(f"{src_codec} audio copied into {out.suffix}: browsers and VLC play it, QuickTime and many TVs do not")
                continue
            codec = acodec if acodec and acodec != "copy" else (cont.get("a") or "aac")
            bits = int(a.get("bit_depth") or 16)
            enc = encoder_for(codec, pcm_bits=24 if bits > 16 else 16)
            out_ch = int(ch) if ch else int(a.get("channels") or 2)
            if not ch and (enc in ("libmp3lame", "wmav2", "mp2") or (enc in ("libopus", "opus") and a.get("layout") not in ("5.1", "5.1(side)", "7.1", "quad", "stereo", "mono"))) and out_ch > 2:
                out_ch = 2
            sbr = abr if (o.abitrate or target_mb is not None) else auto_audio_bitrate(enc, abr, out_ch, a)
            aargs = audio_encoder_args(enc, sbr, pr.get("aq"))
            args += [x.replace("-c:a", f"-c:a:{k}").replace("-b:a", f"-b:a:{k}") if x in ("-c:a", "-b:a") else x for x in aargs]
            if ch or out_ch != int(a.get("channels") or 2):
                args += [f"-ac:a:{k}", str(out_ch)]
            if sr:
                args += [f"-ar:a:{k}", str(sr)]
            elif enc in ("libopus", "opus") and int(a.get("sample_rate") or 48000) not in (8000, 12000, 16000, 24000, 48000):
                args += [f"-ar:a:{k}", "48000"]
            elif enc in ("libmp3lame",) and int(a.get("sample_rate") or 44100) > 48000:
                args += [f"-ar:a:{k}", "48000"]
            elif enc == "wmav2" and int(a.get("sample_rate") or 44100) > 48000:
                args += [f"-ar:a:{k}", "48000"]
            if af:
                args += [f"-filter:a:{k}", ",".join(af)]
            achoice = enc
    # ── subtitles ──
    if want_v and subs and not o.no_subs:
        args += subtitle_args(info, cont, out, ii, notes)
    # ── cover art ──
    if covers and cont.get("cover") and not o.no_video:
        cpos = [s for s in info["streams"] if s["type"] == "video"].index(covers[0])
        args += ["-map", f"{ii}:v:{cpos}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        if ext == ".mp3":
            args += ["-id3v2_version", "3"]
    # ── metadata and container flags ──
    if o.strip_metadata:
        args += ["-map_metadata", "-1", "-map_chapters", "-1"]
    else:
        args += ["-map_metadata", str(ii), *brand_cleanup(out)]
    if cont.get("faststart"):
        args += ["-movflags", "+faststart"]
    if two_pass and vkbps:
        notes.append(f"two-pass encode at {vkbps} kb/s to hit about {target_mb:.3g} MB")
    return Plan(args, notes, vchoice, achoice, two_pass, vkbps, akbps)


#: Below this many bits per pixel even a good encoder shows blocks.
MIN_OK_BPP = 0.02


def cap_size(v: dict[str, Any], pr: dict[str, Any], o: Options) -> tuple[int, int]:
    """The picture size after a preset's caps (before any size-target reduction)."""
    from _media import display_size

    w, h = display_size(v)
    r = 1.0
    if pr.get("max_edge"):
        r = min(r, pr["max_edge"] / max(w, h, 1))
    if pr.get("max_short"):
        r = min(r, pr["max_short"] / max(1, min(w, h)))
    return max(2, int(round(w * r / 2)) * 2), max(2, int(round(h * r / 2)) * 2)


def subtitle_args(info: dict[str, Any], cont: dict[str, Any], out: Path, ii: int, notes: list[str], first_out: int = 0) -> list[str]:
    """-map and per-stream -c:s options for the input's subtitle tracks, converted to what the container holds."""
    from _media import sub_target

    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    if not subs:
        return []
    if not cont.get("s"):
        notes.append(f"dropped {len(subs)} subtitle track(s): {out.suffix} has no subtitle support (use .mp4 or .mkv)")
        return []
    args: list[str] = []
    k = first_out
    dropped: list[str] = []
    converted: list[str] = []
    for pos, s in enumerate(subs):
        t = sub_target(cont, s)
        if t is None:
            dropped.append(f"{s.get('codec')}" + ("" if s.get("text_based") else " image-based"))
            continue
        args += ["-map", f"{ii}:s:{pos}", f"-c:s:{k}", t]
        if t != "copy":
            converted.append(f"{s.get('codec')} → {t}")
        k += 1
    if dropped:
        notes.append(f"dropped {len(dropped)} subtitle track(s) ({', '.join(sorted(set(dropped)))}): {out.suffix} cannot hold them"
                     + (" (use .mkv)" if cont.get("s") != "copy" else ""))
    if converted:
        notes.append("subtitles converted: " + ", ".join(sorted(set(converted))))
    return args
