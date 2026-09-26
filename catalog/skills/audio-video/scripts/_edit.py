"""The edit pipeline of the audio-video skill: a list of operations (trim, cut, speed, fades, volume, normalise,
crop, scale, rotate, pad, text, image, blur, subtitles …) compiled into one ffmpeg filter graph and one encode.
Streams nobody touched are copied; subtitles and chapters follow trims, cuts and speed changes."""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from _common import SkillError, UsageError

# ── timeline: where source times end up ────────────────────────────────


@dataclass
class Timeline:
    """Pieces of the source that survive, in output order: (src_a, src_b, out_a, rate) → out = out_a + (t - src_a) / rate."""

    segs: list[tuple[float, float, float, float]]
    src_d: float = 0.0

    @classmethod
    def identity(cls, d: float) -> "Timeline":
        return cls([(0.0, d, 0.0, 1.0)], d)

    def keep(self, ranges: list[tuple[float, float]]) -> None:
        """Keeps these output-time ranges (sorted) and closes the gaps."""
        new = []
        pos = 0.0
        for x, y in ranges:
            for a, b, o, r in self.segs:
                o2 = o + (b - a) / r
                lo, hi = max(x, o), min(y, o2)
                if hi - lo > 1e-9:
                    sa = a + (lo - o) * r
                    sb = a + (hi - o) * r
                    new.append((sa, sb, pos + (lo - x), r))
            pos += y - x
        self.segs = new

    def speed(self, f: float) -> None:
        self.segs = [(a, b, o / f, r * f) for a, b, o, r in self.segs]

    def map_range(self, s: float, e: float) -> list[tuple[float, float]]:
        out = []
        for a, b, o, r in self.segs:
            lo, hi = max(s, a), min(e, b)
            if hi - lo > 1e-9:
                out.append((o + (lo - a) / r, o + (hi - a) / r))
        merged: list[tuple[float, float]] = []
        for x, y in sorted(out):
            if merged and x - merged[-1][1] < 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], y))
            else:
                merged.append((x, y))
        return merged

    @property
    def changed(self) -> bool:
        a, b, o, r = self.segs[0] if len(self.segs) == 1 else (0.0, 0.0, 1.0, 0.0)
        return not (a == 0 and o == 0 and r == 1 and b >= self.src_d - 1e-3)


# ── graph ───────────────────────────────────────────────────────────────


class Graph:
    """A filter_complex under construction: current video/audio labels, extra inputs, chains."""

    def __init__(self, src: str, vpos: int | None, apos: int | None) -> None:
        self.inputs: list[list[str]] = [["-i", src]]
        self.chains: list[str] = []
        self.v0 = f"[0:v:{vpos}]" if vpos is not None else None
        self.a0 = f"[0:a:{apos}]" if apos is not None else None
        self.v = self.v0
        self.a = self.a0
        self.n = 0

    def _label(self, kind: str) -> str:
        self.n += 1
        return f"[{kind}{self.n}]"

    def vf(self, *filters: str) -> None:
        if self.v is None:
            raise SkillError("this operation needs a video stream")
        out = self._label("v")
        self.chains.append(f"{self.v}{','.join(filters)}{out}")
        self.v = out

    def af(self, *filters: str) -> None:
        if self.a is None:
            raise SkillError("this operation needs an audio stream")
        out = self._label("a")
        self.chains.append(f"{self.a}{','.join(filters)}{out}")
        self.a = out

    def add_input(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def text(self) -> str:
        return ";".join(self.chains)

    def input_args(self) -> list[str]:
        return [x for grp in self.inputs for x in grp]


@dataclass
class State:
    D: float
    W: int
    H: int
    fps: float
    sr: int
    timeline: Timeline
    tmp: Path
    info: dict[str, Any]
    cwd: Path | None = None
    notes: list[str] = field(default_factory=list)
    drop_audio: bool = False
    done: list[str] = field(default_factory=list)
    lossy_out: bool = True  # the output's audio codec is lossy (MP3, AAC, Opus …)
    tp_target: float | None = None  # normalize into a lossy codec: the true peak promised, checked on the output


def _t(p: dict[str, Any], key: str, st: State, default: float | None = None) -> float | None:
    from _media import parse_time

    v = p.get(key)
    if v is None or v == "":
        return default
    return parse_time(v, st.D, key)


def _enable(start: float | None, end: float | None) -> str:
    if start is None and end is None:
        return ""
    s = start or 0.0
    return f":enable='between(t,{s:.3f},{end:.3f})'" if end is not None else f":enable='gte(t,{s:.3f})'"


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _ranges(p: dict[str, Any], key: str, st: State) -> list[tuple[float, float]]:
    from _media import parse_time_ranges

    v = p.get(key)
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        v = ",".join(f"{x[0]}-{x[1]}" if isinstance(x, (list, tuple)) else str(x) for x in v)
    return parse_time_ranges(str(v), st.D)


# ── operations ──────────────────────────────────────────────────────────


def op_trim(g: Graph, st: State, p: dict[str, Any]) -> None:
    s = _t(p, "start", st, 0.0) or 0.0
    e = _t(p, "end", st)
    if e is None and p.get("duration") is not None:
        from _media import parse_time

        e = s + parse_time(p["duration"], None, "duration")
    e = min(e if e is not None else st.D, st.D)
    if e <= s:
        raise UsageError("trim: the end must be after the start")
    if g.v:
        g.vf(f"trim=start={s:.6f}:end={e:.6f}", "setpts=PTS-STARTPTS")
    if g.a:
        g.af(f"atrim=start={s:.6f}:end={e:.6f}", "asetpts=PTS-STARTPTS")
    st.timeline.keep([(s, e)])
    st.D = e - s


def _keep_segments(g: Graph, st: State, keep: list[tuple[float, float]]) -> None:
    k = len(keep)
    if k == 1:
        s, e = keep[0]
        op_trim(g, st, {"start": s, "end": e})
        return
    pads = []
    if g.v:
        outs = [f"[vs{g.n}_{i}]" for i in range(k)]
        g.chains.append(f"{g.v}split={k}{''.join(outs)}")
    if g.a:
        aouts = [f"[as{g.n}_{i}]" for i in range(k)]
        g.chains.append(f"{g.a}asplit={k}{''.join(aouts)}")
    base = g.n
    for i, (s, e) in enumerate(keep):
        if g.v:
            g.chains.append(f"[vs{base}_{i}]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[vk{base}_{i}]")
            pads.append(f"[vk{base}_{i}]")
        if g.a:
            g.chains.append(f"[as{base}_{i}]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[ak{base}_{i}]")
            pads.append(f"[ak{base}_{i}]")
    g.n += 1
    vo = f"[vc{g.n}]" if g.v else ""
    ao = f"[ac{g.n}]" if g.a else ""
    g.chains.append(f"{''.join(pads)}concat=n={k}:v={1 if g.v else 0}:a={1 if g.a else 0}{vo}{ao}")
    if g.v:
        g.v = vo
    if g.a:
        g.a = ao
    st.timeline.keep(keep)
    st.D = sum(e - s for s, e in keep)


def op_cut(g: Graph, st: State, p: dict[str, Any]) -> None:
    remove = _ranges(p, "remove", st)
    if not remove:
        raise UsageError("cut: give the ranges to remove, e.g. 10-15,1:20-1:25")
    keep = []
    pos = 0.0
    for a, b in remove:
        if a - pos > 0.01:
            keep.append((pos, a))
        pos = max(pos, b)
    if st.D - pos > 0.01:
        keep.append((pos, st.D))
    if not keep:
        raise UsageError("cut: that removes everything")
    _keep_segments(g, st, keep)


def op_keep(g: Graph, st: State, p: dict[str, Any]) -> None:
    keep = _ranges(p, "ranges", st)
    if not keep:
        raise UsageError("keep: give the ranges to keep, e.g. 0-10,20-30")
    _keep_segments(g, st, keep)


def atempo_chain(f: float) -> list[str]:
    parts = []
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.6f}")
    return parts


def op_speed(g: Graph, st: State, p: dict[str, Any]) -> None:
    try:
        f = float(p.get("factor"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise UsageError("speed: give --factor, e.g. 2 (twice as fast) or 0.5 (half speed)") from None
    if not 0.05 <= f <= 100:
        raise UsageError("speed: the factor must be between 0.05 and 100")
    if g.v:
        g.vf(f"setpts=PTS/{f:.6f}", f"fps={st.fps:g}")
    if g.a:
        if p.get("keep_pitch", True):
            g.af(*atempo_chain(f))
        else:
            g.af(f"asetrate={int(st.sr * f)}", f"aresample={st.sr}")
    st.timeline.speed(f)
    st.D = st.D / f


def op_fade(g: Graph, st: State, p: dict[str, Any]) -> None:
    fin = float(p.get("in") or 0)
    fout = float(p.get("out") or 0)
    if not fin and not fout:
        raise UsageError("fade: give --in and/or --out seconds")
    if fin + fout > st.D + 1e-6:
        raise UsageError("fade: the fades are longer than the clip")
    color = p.get("color") or "black"
    only = p.get("only")
    if g.v and only != "audio":
        fs = []
        if fin:
            fs.append(f"fade=t=in:st=0:d={fin:.3f}:color={color}")
        if fout:
            fs.append(f"fade=t=out:st={st.D - fout:.3f}:d={fout:.3f}:color={color}")
        g.vf(*fs)
    if g.a and only != "video":
        fs = []
        if fin:
            fs.append(f"afade=t=in:st=0:d={fin:.3f}")
        if fout:
            fs.append(f"afade=t=out:st={st.D - fout:.3f}:d={fout:.3f}")
        g.af(*fs)


def _gain(v: Any) -> str:
    s = str(v).strip().lower().replace(" ", "")
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)db", s)
    if m:
        return f"{float(m.group(1)):g}dB"
    try:
        f = float(s)
    except ValueError:
        raise UsageError(f"bad gain '{v}' (use 6dB, -3dB or a factor like 0.5)") from None
    if f < 0:
        raise UsageError("a volume factor cannot be negative (use dB for cuts, e.g. -6dB)")
    return f"{f:g}"


def op_volume(g: Graph, st: State, p: dict[str, Any]) -> None:
    if p.get("gain") is None:
        raise UsageError("volume: give --gain, e.g. 6dB, -3dB or 0.5")
    rng = _ranges(p, "ranges", st)
    en = ""
    if rng:
        en = ":enable='" + "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in rng) + "'"
    g.af(f"volume={_gain(p['gain'])}{en}")


def op_mute(g: Graph, st: State, p: dict[str, Any]) -> None:
    rng = _ranges(p, "ranges", st)
    if not rng:
        st.drop_audio = True
        return
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in rng)
    g.af(f"volume=0:enable='{expr}'")
    if p.get("beep"):
        g.n += 1
        tone = f"[beep{g.n}]"
        g.chains.append(f"sine=frequency={float(p.get('beep_freq') or 1000):g}:sample_rate={st.sr}:duration={st.D:.3f},volume='0.25*({expr})':eval=frame{tone}")
        out = f"[am{g.n}]"
        g.chains.append(f"{g.a}{tone}amix=inputs=2:duration=first:normalize=0{out}")
        g.a = out


def op_normalize(g: Graph, st: State, p: dict[str, Any], analyse: Callable[[Graph, str], str]) -> None:
    presets = {"podcast": (-16.0, -1.5, 11.0), "web": (-16.0, -1.0, 11.0), "streaming": (-14.0, -1.0, 11.0), "broadcast": (-23.0, -1.0, 15.0), "voice": (-19.0, -1.5, 9.0)}
    base = presets.get(str(p.get("preset") or "podcast").lower())
    if base is None:
        raise UsageError(f"normalize: preset must be one of {', '.join(presets)}")
    i = float(p.get("target") if p.get("target") is not None else base[0])
    tp = float(p.get("true_peak") if p.get("true_peak") is not None else base[1])
    lra = float(p.get("lra") if p.get("lra") is not None else base[2])
    if not -70 <= i <= -5:
        raise UsageError("normalize: the target must be between -70 and -5 LUFS")
    # MP3/AAC/Opus encoders overshoot the peaks of what they are given (about 1 dB, more at low bitrates): aim lower
    # for them. run_pipeline measures the result and, if the encoder still went over, encodes once more lower still.
    margin = 0.0
    if st.lossy_out:
        from _media import main_audio

        sbr = int((main_audio(st.info) or {}).get("bit_rate") or 0)
        margin = 1.5 if 0 < sbr < 96_000 else 1.0  # low-bitrate MP3/AAC overshoots more
    tp_run = float(p["_tp_run"]) if p.get("_tp_run") is not None else tp - margin
    if st.lossy_out:
        st.tp_target = tp
    first = f"loudnorm=I={i:g}:TP={tp_run:g}:LRA={lra:g}:print_format=json"
    text = analyse(g, first)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        raise SkillError("loudness analysis failed (no measurement from ffmpeg)")
    meas = json.loads(m[-1])
    try:
        mi = float(meas["input_i"])
    except (KeyError, ValueError):
        mi = float("-inf")
    if not math.isfinite(mi) or mi < -69:
        raise SkillError("the audio is silent; there is nothing to normalise")
    second = (f"loudnorm=I={i:g}:TP={tp_run:g}:LRA={lra:g}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:measured_LRA={meas['input_lra']}"
              f":measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true:print_format=summary")
    g.af(second, f"aresample={st.sr}")
    try:
        peaks = float(meas["input_tp"]) + (i - mi) > tp_run
        wide = float(meas["input_lra"]) > lra
    except (KeyError, ValueError):
        peaks = wide = False
    why = []
    if peaks:
        why.append(f"reaching {i:g} LUFS by gain alone would push the peaks over {tp_run:g} dBTP")
    if wide:
        why.append(f"the loudness range ({float(meas['input_lra']):.1f} LU) is wider than {lra:g} LU")
    st.notes.append(f"loudness {mi:.1f} → {'about ' if why else ''}{i:g} LUFS, true peak ≤ {tp:g} dBTP"
                    + (f" (limited at {tp_run:g} dBTP: lossy encoders overshoot peaks)" if st.lossy_out else "")
                    + (f"; {' and '.join(why)}, so loudnorm evened the levels dynamically (on very peaky audio that can land 1-3 LU under the target: "
                       f"measure the result with media_audio.py stats)" if why else ""))


def op_denoise(g: Graph, st: State, p: dict[str, Any]) -> None:
    amount = float(p.get("strength") or 12)
    hp = float(p.get("highpass") or 80)
    g.af(f"highpass=f={hp:g}", f"afftdn=nr={amount:g}:nf=-40:tn=1")


def _box(p: dict[str, Any], key: str, W: int, H: int) -> tuple[int, int, int, int]:
    v = p.get(key)
    if isinstance(v, (list, tuple)):
        parts = [str(x) for x in v]
    else:
        parts = [x.strip() for x in str(v or "").replace(":", ",").split(",")]
    if len(parts) != 4:
        raise UsageError(f"{key} must be x,y,w,h (pixels or percentages, e.g. 10%,10%,50%,30%)")
    vals = []
    for k, x in enumerate(parts):
        ref = W if k in (0, 2) else H
        try:
            vals.append(int(round(float(x[:-1]) / 100 * ref)) if x.endswith("%") else int(round(float(x))))
        except ValueError:
            raise UsageError(f"bad number '{x}' in {key}") from None
    x, y, w, h = vals
    x, y = max(0, x), max(0, y)
    w, h = min(w, W - x), min(h, H - y)
    if w < 2 or h < 2:
        raise UsageError(f"{key} is outside the picture ({W}x{H})")
    return x, y, w, h


def _ratio(v: Any) -> float:
    s = str(v).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)) / float(m.group(2))
    try:
        return float(s)
    except ValueError:
        raise UsageError(f"bad aspect ratio '{v}' (use 16:9, 9:16, 1:1, 4:5 or 1.85)") from None


def op_crop(g: Graph, st: State, p: dict[str, Any], detect: Callable[[], tuple[int, int, int, int] | None] | None = None) -> None:
    W, H = st.W, st.H
    if p.get("box"):
        x, y, w, h = _box(p, "box", W, H)
    elif p.get("aspect"):
        r = _ratio(p["aspect"])
        if W / H > r:
            h = H
            w = _even(H * r)
        else:
            w = W
            h = _even(W / r)
        w, h = min(w, W), min(h, H)
        anchor = str(p.get("anchor") or "center").lower()
        x = (W - w) // 2
        y = (H - h) // 2
        if "left" in anchor:
            x = 0
        if "right" in anchor:
            x = W - w
        if "top" in anchor:
            y = 0
        if "bottom" in anchor:
            y = H - h
    elif p.get("auto"):
        if "crop" in st.done or "scale" in st.done or "rotate" in st.done or "pad" in st.done:
            raise UsageError("crop --auto must come before other size changes")
        found = detect() if detect else None
        if not found:
            st.notes.append("no black borders found; nothing cropped")
            return
        w, h, x, y = found
        if w >= W - 4 and h >= H - 4:
            st.notes.append("no black borders found; nothing cropped")
            return
        st.notes.append(f"black borders removed: {W}x{H} → {w}x{h}")
    else:
        raise UsageError("crop: give --box x,y,w,h, --aspect 9:16 or --auto")
    w, h = w - w % 2, h - h % 2
    g.vf(f"crop={w}:{h}:{x}:{y}")
    st.W, st.H = w, h


def scaled_size(spec: str, W: int, H: int) -> tuple[int, int, str]:
    """(w, h, filter) for a size spec, computed here so later operations know the new size."""
    s = str(spec).strip().lower()
    named = {"2160p": 2160, "4k": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "540p": 540, "480p": 480, "360p": 360, "240p": 240}
    if s in named:
        short = named[s]
        if W >= H:
            w, h = _even(W * short / H), short
        else:
            w, h = short, _even(H * short / W)
        return w, h, f"scale={w}:{h}:flags=lanczos,setsar=1"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", s)
    if m:
        f = float(m.group(1)) / 100
        w, h = _even(W * f), _even(H * f)
        return w, h, f"scale={w}:{h}:flags=lanczos,setsar=1"
    m = re.fullmatch(r"(fit|fill):(\d+)\s*[x:×]\s*(\d+)", s)
    if m:
        mode, tw, th = m.group(1), int(m.group(2)), int(m.group(3))
        if mode == "fit":
            r = min(tw / W, th / H)
            w, h = _even(W * r), _even(H * r)
            return w, h, f"scale={w}:{h}:flags=lanczos,setsar=1"
        r = max(tw / W, th / H)
        w1, h1 = _even(W * r), _even(H * r)
        return tw, th, f"scale={w1}:{h1}:flags=lanczos,setsar=1,crop={tw}:{th}"
    m = re.fullmatch(r"(\d+)?\s*[x:×]\s*(\d+)?", s)
    if m and (m.group(1) or m.group(2)):
        if m.group(1) and m.group(2):
            w, h = int(m.group(1)), int(m.group(2))
        elif m.group(1):
            w = int(m.group(1))
            h = _even(H * w / W)
        else:
            h = int(m.group(2))
            w = _even(W * h / H)
        return w, h, f"scale={w}:{h}:flags=lanczos,setsar=1"
    if re.fullmatch(r"\d+", s):
        w = int(s)
        h = _even(H * w / W)
        return w, h, f"scale={w}:{h}:flags=lanczos,setsar=1"
    raise UsageError(f"bad size '{spec}' (use 1280x720, 1280x, x720, 720p, 50%, fit:1280x720 or fill:1080x1920)")


def op_scale(g: Graph, st: State, p: dict[str, Any]) -> None:
    if not p.get("size"):
        raise UsageError("scale: give --size, e.g. 1280x720, 720p or 50%")
    w, h, f = scaled_size(p["size"], st.W, st.H)
    g.vf(f)
    st.W, st.H = w, h


def op_rotate(g: Graph, st: State, p: dict[str, Any]) -> None:
    try:
        ang = float(p.get("angle"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise UsageError("rotate: give --angle in degrees clockwise (90, 180, 270, or any)") from None
    a = ang % 360
    if abs(a - 90) < 1e-6:
        g.vf("transpose=1")
        st.W, st.H = st.H, st.W
    elif abs(a - 270) < 1e-6:
        g.vf("transpose=2")
        st.W, st.H = st.H, st.W
    elif abs(a - 180) < 1e-6:
        g.vf("hflip", "vflip")
    elif a > 1e-6:
        rad = math.radians(a)
        c = p.get("color") or "black"
        w = _even(abs(st.W * math.cos(rad)) + abs(st.H * math.sin(rad)))
        h = _even(abs(st.W * math.sin(rad)) + abs(st.H * math.cos(rad)))
        g.vf(f"rotate={rad:.6f}:ow={w}:oh={h}:c={c}")
        st.W, st.H = w, h


def op_flip(g: Graph, st: State, p: dict[str, Any]) -> None:
    fs = []
    if p.get("horizontal"):
        fs.append("hflip")
    if p.get("vertical"):
        fs.append("vflip")
    if not fs:
        raise UsageError("flip: give --horizontal and/or --vertical")
    g.vf(*fs)


def op_pad(g: Graph, st: State, p: dict[str, Any]) -> None:
    W, H = st.W, st.H
    if p.get("aspect"):
        r = _ratio(p["aspect"])
        if W / H < r:
            w, h = _even(H * r), H
        else:
            w, h = W, _even(W / r)
    elif p.get("size"):
        m = re.fullmatch(r"(\d+)\s*[x:×]\s*(\d+)", str(p["size"]).strip().lower())
        if not m:
            raise UsageError("pad: --size must be WxH")
        w, h = int(m.group(1)), int(m.group(2))
    else:
        raise UsageError("pad: give --aspect 16:9 or --size 1920x1080")
    if w < W or h < H:
        # Fit the picture inside the new frame first.
        r = min(w / W, h / H)
        fw, fh = _even(W * r), _even(H * r)
        g.vf(f"scale={fw}:{fh}:flags=lanczos,setsar=1")
        W, H = fw, fh
    color = p.get("color") or "black"
    if p.get("blur"):
        g.n += 1
        n = g.n
        g.chains.append(f"{g.v}split[pa{n}][pb{n}]")
        sw, sh = _even(w / 8), _even(h / 8)
        lr = max(1, min(8, min(sw, sh) // 2 - 1))
        cr = max(0, min(lr, min(sw, sh) // 4 - 1))
        g.chains.append(f"[pa{n}]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
                        f"boxblur=luma_radius={lr}:luma_power=2:chroma_radius={cr}:chroma_power=2,scale={w}:{h},setsar=1[pbg{n}]")
        g.chains.append(f"[pbg{n}][pb{n}]overlay=({w}-w)/2:({h}-h)/2[pv{n}]")
        g.v = f"[pv{n}]"
    else:
        g.vf(f"pad={w}:{h}:({w}-iw)/2:({h}-ih)/2:color={color}", "setsar=1")
    st.W, st.H = w, h


def op_adjust(g: Graph, st: State, p: dict[str, Any]) -> None:
    parts = []
    for key, lo, hi in (("brightness", -1.0, 1.0), ("contrast", 0.0, 3.0), ("saturation", 0.0, 3.0), ("gamma", 0.1, 10.0)):
        if p.get(key) is not None:
            v = float(p[key])
            if not lo <= v <= hi:
                raise UsageError(f"adjust: {key} must be between {lo:g} and {hi:g}")
            parts.append(f"{key}={v:g}")
    fs = []
    if parts:
        fs.append("eq=" + ":".join(parts))
    if p.get("grayscale"):
        fs.append("hue=s=0")
    if p.get("sharpen"):
        fs.append(f"unsharp=5:5:{float(p['sharpen']):g}")
    if p.get("denoise"):
        fs.append("hqdn3d")
    if not fs:
        raise UsageError("adjust: give --brightness, --contrast, --saturation, --gamma, --grayscale, --sharpen or --denoise")
    s = _t(p, "start", st)
    e = _t(p, "end", st)
    if s is not None or e is not None:
        fs = [f + _enable(s, e) for f in fs]
    g.vf(*fs)


def op_fps(g: Graph, st: State, p: dict[str, Any]) -> None:
    f = float(p.get("fps") or 0)
    if f <= 0:
        raise UsageError("fps: give --fps")
    g.vf(f"fps={f:g}")
    st.fps = f


def op_blur(g: Graph, st: State, p: dict[str, Any]) -> None:
    x, y, w, h = _box(p, "box", st.W, st.H)
    strength = max(2, int(p.get("strength") or 20))
    strength = min(strength, max(1, min(w, h) // 2 - 1))
    chroma = max(0, min(strength, min(w, h) // 4 - 1))
    s = _t(p, "start", st)
    e = _t(p, "end", st)
    g.n += 1
    n = g.n
    g.chains.append(f"{g.v}split[bm{n}][bc{n}]")
    g.chains.append(f"[bc{n}]crop={w}:{h}:{x}:{y},boxblur=luma_radius={strength}:luma_power=3:chroma_radius={chroma}:chroma_power=3[bb{n}]")
    g.chains.append(f"[bm{n}][bb{n}]overlay={x}:{y}{_enable(s, e)}[bo{n}]")
    g.v = f"[bo{n}]"


def _loop_input(g: Graph, st: State, path: Path) -> int:
    return g.add_input(["-loop", "1", "-framerate", f"{st.fps:g}", "-t", f"{st.D:.3f}", "-i", str(path)])


# Scripts that need shaping or right-to-left layout (Hebrew, Arabic, Indic, Thai, Lao, Tibetan, Myanmar, Khmer):
# Pillow here has no shaping engine, libass has one.
COMPLEX_SCRIPT = re.compile("[\u0590-\u08ff\u0900-\u0dff\u0e00-\u0fff\u1000-\u109f\u1780-\u17ff\ufb1d-\ufdff\ufe70-\ufeff]")


def _ass_rgba(value: str) -> str:
    from _overlay import parse_color

    r, g_, b, a = parse_color(value)
    return f"&H{255 - a:02X}{b:02X}{g_:02X}{r:02X}"


def _text_libass(g: Graph, st: State, p: dict[str, Any], text: str, size: int, margin: int, s: float | None, e: float | None) -> None:
    """The text op through libass, which shapes Arabic, Hebrew and Indic scripts correctly."""
    from _overlay import find_font, place
    from _subs import ass_time

    bold = not p.get("regular")
    font_path = find_font(p.get("font"), bold)
    family = _font_family(font_path) if font_path else "Arial"
    pos = str(p.get("position") or "bottom").strip().lower().replace("_", "-").replace(" ", "-")
    an = {"top-left": 7, "top": 8, "top-right": 9, "left": 4, "center": 5, "centre": 5, "middle": 5, "right": 6,
          "bottom-left": 1, "bottom": 2, "bottom-right": 3}.get(pos)
    tags = ""
    if an is None:
        x, y = place(pos, st.W, st.H, 0, 0, margin)
        tags += f"\\an7\\pos({x},{y})"
    fade = int(float(p.get("fade") or 0) * 1000)
    if fade:
        tags += f"\\fad({fade},{fade})"
    box = bool(p.get("box"))
    outline = 0 if p.get("no_outline") and not box else (round(size * 0.3) if box else max(1, round(size / 14)))
    back = _ass_rgba(p.get("box_color") or "black@0.55")
    style = (f"Style: T,{family},{size},{_ass_rgba(p.get('color') or 'white')},&H000000FF,{back if box else '&H00000000'},{back},"
             f"{-1 if bold else 0},0,0,0,100,100,0,0,{3 if box else 1},{outline},{max(1, size // 18) if p.get('shadow') else 0},"
             f"{an or 7},{margin},{margin},{margin},1")
    body = text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
    d = st.tmp / "subs"
    d.mkdir(exist_ok=True)
    g.n += 1
    name = f"text{g.n}.ass"
    (d / name).write_text("\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {st.W}", f"PlayResY: {st.H}", "ScaledBorderAndShadow: yes", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{ass_time(s or 0.0)},{ass_time(e if e is not None else st.D + 1)},T,,0,0,0,,{{{tags}}}{body}" if tags else
        f"Dialogue: 0,{ass_time(s or 0.0)},{ass_time(e if e is not None else st.D + 1)},T,,0,0,0,,{body}", ""]), encoding="utf-8")
    opts = f"subtitles=filename={name}"
    if p.get("font") and font_path:
        fd = d / "fonts"
        fd.mkdir(exist_ok=True)
        shutil.copyfile(font_path, fd / font_path.name)
        opts += ":fontsdir=fonts"
    g.vf(opts)
    st.cwd = d
    st.notes.append("text drawn with libass (it shapes right-to-left and complex scripts)")


def op_text(g: Graph, st: State, p: dict[str, Any]) -> None:
    from _media import has_filter
    from _overlay import UNDRAWN, place, render_text, size_px

    text = p.get("text")
    if not text:
        raise UsageError("text: give --text")
    text = str(text).replace("\\n", "\n")
    size = size_px(p.get("size"), st.H, max(16, round(st.H * 0.06)))
    if COMPLEX_SCRIPT.search(text):
        if has_filter("subtitles"):
            margin = size_px(p.get("margin"), min(st.W, st.H), max(8, round(min(st.W, st.H) * 0.04)))
            _text_libass(g, st, p, text, size, margin, _t(p, "start", st), _t(p, "end", st))
            return
        st.notes.append("this ffmpeg has no libass, so right-to-left or complex-script text is drawn without shaping (letters unjoined); check it")
    UNDRAWN.clear()
    img = render_text(text, size, color=p.get("color") or "white", font=p.get("font"), bold=not p.get("regular"),
                      box=bool(p.get("box")), box_color=p.get("box_color") or "black@0.55", max_width=int(st.W * 0.92),
                      align=p.get("align") or "center", shadow=bool(p.get("shadow")), stroke=None if not p.get("no_outline") else 0)
    if UNDRAWN:
        st.notes.append(f"no available font can draw {' '.join(sorted(UNDRAWN))}; those characters show as boxes (pass --font)")
    if img.size[0] > st.W or img.size[1] > st.H:
        img.thumbnail((st.W, st.H))
    margin = size_px(p.get("margin"), min(st.W, st.H), max(8, round(min(st.W, st.H) * 0.04)))
    x, y = place(str(p.get("position") or "bottom"), st.W, st.H, img.size[0], img.size[1], margin)
    f = st.tmp / f"text{g.n + 1}.png"
    img.save(f)
    idx = _loop_input(g, st, f)
    s = _t(p, "start", st)
    e = _t(p, "end", st)
    fade = float(p.get("fade") or 0)
    pre = "format=rgba"
    if fade:
        fs = s or 0.0
        fe = e if e is not None else st.D
        pre += f",fade=t=in:st={fs:.3f}:d={fade:.3f}:alpha=1,fade=t=out:st={max(fs, fe - fade):.3f}:d={fade:.3f}:alpha=1"
    g.n += 1
    n = g.n
    g.chains.append(f"[{idx}:v]{pre}[tx{n}]")
    g.chains.append(f"{g.v}[tx{n}]overlay={x}:{y}:eof_action=pass{_enable(s, e)}[to{n}]")
    g.v = f"[to{n}]"


def op_image(g: Graph, st: State, p: dict[str, Any]) -> None:
    from PIL import Image

    from _common import input_file
    from _overlay import place, size_px

    src = input_file(str(p.get("image") or "")).resolve()
    try:
        with Image.open(src) as im:
            iw, ih = im.size
    except Exception as e:  # noqa: BLE001
        raise SkillError(f"cannot read the image {src.name}: {e}") from None
    w = size_px(p.get("width"), st.W, max(16, round(st.W * 0.15)))
    w = min(w, st.W)
    h = max(2, round(ih * w / iw))
    margin = size_px(p.get("margin"), min(st.W, st.H), max(8, round(min(st.W, st.H) * 0.03)))
    x, y = place(str(p.get("position") or "bottom-right"), st.W, st.H, w, h, margin)
    op = float(p.get("opacity") if p.get("opacity") is not None else 1.0)
    if not 0 <= op <= 1:
        raise UsageError("image: --opacity must be between 0 and 1")
    idx = _loop_input(g, st, src)
    s = _t(p, "start", st)
    e = _t(p, "end", st)
    g.n += 1
    n = g.n
    g.chains.append(f"[{idx}:v]scale={w}:{h}:flags=lanczos,format=rgba,colorchannelmixer=aa={op:g}[im{n}]")
    g.chains.append(f"{g.v}[im{n}]overlay={x}:{y}:eof_action=pass{_enable(s, e)}[io{n}]")
    g.v = f"[io{n}]"


def op_subtitles(g: Graph, st: State, p: dict[str, Any]) -> None:
    """Burns subtitles into the picture: libass when this ffmpeg has it, else cue images drawn with Pillow."""
    from _common import input_file
    from _media import has_filter
    from _subs import ass_color, load, save

    src = input_file(str(p.get("subtitles") or p.get("file") or ""))
    doc = load(src, p.get("encoding"))
    if not doc.cues:
        raise SkillError(f"{src.name} has no cues")
    styled = any(p.get(k) for k in ("font", "size", "color", "box", "position", "margin"))
    use_libass = has_filter("subtitles") and not p.get("pillow")
    if use_libass:
        d = st.tmp / "subs"
        d.mkdir(exist_ok=True)
        target = d / "subs.ass"
        if doc.fmt == "ass" and not styled:
            shutil.copyfile(src, target)
        else:
            from _overlay import find_font, size_px

            style: dict[str, Any] = {}
            font_path = None
            if p.get("font"):
                font_path = find_font(p["font"], bold=False)
                style["font"] = _font_family(font_path) if font_path else p["font"]
            if p.get("size"):
                style["size"] = size_px(p["size"], st.H, 0)
            if p.get("color"):
                style["color"] = ass_color(p["color"])
            if p.get("box"):
                # In a boxed style (BorderStyle 3) the outline width is the padding around the text.
                fs = style.get("size") or max(16, round(st.H * 0.055))
                style["border_style"] = 3
                style["outline"] = max(4, round(fs * 0.28))
                style["shadow"] = 0
                style["outline_color"] = "&H80000000"
                style["back_color"] = "&H80000000"
            pos = str(p.get("position") or "bottom").lower()
            style["alignment"] = 8 if pos.startswith("top") else (5 if pos in ("center", "centre", "middle") else 2)
            if p.get("margin"):
                style["margin_v"] = size_px(p["margin"], st.H, 0)
            save(doc, target, "ass", play_res=(st.W, st.H), style=style)
            if font_path:
                fd = d / "fonts"
                fd.mkdir(exist_ok=True)
                shutil.copyfile(font_path, fd / font_path.name)
        opts = "subtitles=filename=subs.ass"
        if (d / "fonts").is_dir():
            opts += ":fontsdir=fonts"
        g.vf(opts)
        st.cwd = d
        st.notes.append("subtitles burned in with libass")
    else:
        from _overlay import UNDRAWN, cue_images, size_px

        UNDRAWN.clear()
        lst = cue_images(doc.cues, st.W, st.H, st.tmp / "cues", size=size_px(p.get("size"), st.H, 0) or None, color=p.get("color") or "white",
                         font=p.get("font"), box=bool(p.get("box")), position=str(p.get("position") or "bottom"),
                         margin=size_px(p.get("margin"), st.H, 0) or None)
        idx = g.add_input(["-f", "concat", "-safe", "0", "-i", str(lst)])
        g.n += 1
        n = g.n
        g.chains.append(f"[{idx}:v]format=rgba[sb{n}]")
        g.chains.append(f"{g.v}[sb{n}]overlay=0:0:eof_action=pass:repeatlast=1[so{n}]")
        g.v = f"[so{n}]"
        st.notes.append("subtitles burned in (drawn by the skill; this ffmpeg has no libass)")
        if any(COMPLEX_SCRIPT.search(c.text) for c in doc.cues):
            st.notes.append("right-to-left or complex-script lines are drawn without shaping (letters unjoined); check them")
        if UNDRAWN:
            st.notes.append(f"no available font can draw {' '.join(sorted(UNDRAWN))}; those characters show as boxes")


def _font_family(path: Path) -> str:
    try:
        from PIL import ImageFont

        return ImageFont.truetype(str(path), 12).getname()[0]
    except Exception:  # noqa: BLE001
        return path.stem


OPS: dict[str, Callable[..., None]] = {
    "trim": op_trim, "cut": op_cut, "keep": op_keep, "speed": op_speed, "fade": op_fade, "volume": op_volume, "mute": op_mute,
    "normalize": op_normalize, "denoise": op_denoise, "crop": op_crop, "scale": op_scale, "rotate": op_rotate, "flip": op_flip,
    "pad": op_pad, "adjust": op_adjust, "fps": op_fps, "blur": op_blur, "text": op_text, "image": op_image, "subtitles": op_subtitles,
}
VIDEO_OPS = {"crop", "scale", "rotate", "flip", "pad", "adjust", "fps", "blur", "text", "image", "subtitles"}
AUDIO_OPS = {"volume", "mute", "normalize", "denoise"}


# ── running a pipeline ──────────────────────────────────────────────────


def encode_args(info: dict[str, Any], out: Path, want_v: bool, want_a: bool, eo: dict[str, Any], vlabel: str | None, alabel: str | None) -> tuple[list[str], list[str]]:
    """Mapping and encoder options for edited streams; the source's codec is kept when the container allows it."""
    from _media import (ENCODERS, audio_encoder_args, auto_audio_bitrate, can_copy, container_for, encoder_for, has_encoder, main_audio,
                        main_video, norm_codec, parse_bitrate, video_encoder_args)

    cont = container_for(out)
    args: list[str] = []
    notes: list[str] = []
    if want_v and vlabel:
        v = main_video(info) or {}
        src = norm_codec(v.get("codec") or "")
        if eo.get("vcodec"):
            codec = norm_codec(eo["vcodec"])
        elif src in ENCODERS and can_copy(cont, "video", src) and any(has_encoder(e) for e in ENCODERS[src]) and src not in ("mjpeg", "gif", "png"):
            codec = src
        else:
            codec = cont.get("v") or "h264"
        enc = encoder_for(codec)
        crf = eo.get("crf")
        if crf is None:
            crf = {"libx264": 20, "libx265": 24, "libvpx-vp9": 30, "libsvtav1": 32, "libaom-av1": 30}.get(enc)
        args += ["-map", vlabel, *video_encoder_args(enc, crf, parse_bitrate(eo.get("video_bitrate")), eo.get("speed") or "medium", int(v.get("bit_depth") or 8))]
    if want_a and alabel:
        a = main_audio(info) or {}
        src = norm_codec(a.get("codec") or "")
        if eo.get("acodec"):
            codec = norm_codec(eo["acodec"])
        elif src.startswith("pcm_") and can_copy(cont, "audio", src):
            codec = src
        elif src in ENCODERS and can_copy(cont, "audio", src) and any(has_encoder(e) for e in ENCODERS[src]):
            codec = src
        else:
            codec = cont.get("a") or "aac"
        bits = int(a.get("bit_depth") or 16)
        enc = encoder_for(codec, pcm_bits=24 if bits > 16 else 16, big_endian=out.suffix.lower() in (".aif", ".aiff"))
        abr = parse_bitrate(eo.get("audio_bitrate")) or auto_audio_bitrate(enc, {"aac": "192000", "libopus": "128000", "wmav2": "192000"}.get(enc),
                                                                        eo.get("_channels") or a.get("channels"), None if eo.get("_mixed") else a)
        args += ["-map", alabel, *audio_encoder_args(enc, abr)]
        if enc in ("libopus", "opus"):
            args += ["-ar", "48000"]
    if cont.get("faststart"):
        args += ["-movflags", "+faststart"]
    return args, notes


def _lossy_audio_out(info: dict[str, Any], out: Path, eo: dict[str, Any]) -> bool:
    """Whether encode_args will write the audio with a lossy codec."""
    from _media import ENCODERS, can_copy, container_for, main_audio, norm_codec

    cont = container_for(out)
    src = norm_codec((main_audio(info) or {}).get("codec") or "")
    codec = norm_codec(eo["acodec"]) if eo.get("acodec") else (src if (src.startswith("pcm_") or src in ENCODERS) and can_copy(cont, "audio", src) else (cont.get("a") or "aac"))
    return not (codec.startswith("pcm") or codec in ("flac", "alac", "wavpack"))


def run_pipeline(src: Path, out: Path, ops: list[dict[str, Any]], eo: dict[str, Any], label: str = "editing") -> dict[str, Any]:
    """Applies `ops` to `src` in one encode and writes `out`. Returns a report."""
    import time

    from _media import TempOut, audio_stream_pos, brand_cleanup, display_size, fps_rational, main_audio, main_video, probe, run_ffmpeg, secs_arg, summarize_output

    t0 = time.monotonic()
    # Absolute paths: burning subtitles runs ffmpeg from a temp folder.
    shown_src, shown_out = src, out
    src, out = Path(src).resolve(), Path(out).resolve()
    info = probe(src)
    v = main_video(info)
    a = main_audio(info)
    if not v and not a:
        raise SkillError(f"{info['name']} has no audio or video stream")
    D = info.get("duration")
    if not D:
        raise SkillError(f"{info['name']}: the duration is unknown, so it cannot be edited safely")
    names = [str(o.get("op", "")).lower() for o in ops]
    for n in names:
        if n not in OPS:
            raise UsageError(f"unknown operation '{n}'; known: {', '.join(sorted(OPS))}")
    if not v and any(n in VIDEO_OPS for n in names):
        bad = next(n for n in names if n in VIDEO_OPS)
        raise SkillError(f"'{bad}' needs video, and {info['name']} is audio only")
    if not a and any(n in AUDIO_OPS for n in names):
        bad = next(n for n in names if n in AUDIO_OPS)
        raise SkillError(f"'{bad}' needs audio, and {info['name']} has none")
    vpos = [s for s in info["streams"] if s["type"] == "video"].index(v) if v else None
    apos = audio_stream_pos(info, eo.get("audio_track")) if a else None
    W, H = display_size(v) if v else (0, 0)
    fps = float((v or {}).get("fps") or 25.0)
    if fps > 120:
        fps = 60.0
    sr = int((a or {}).get("sample_rate") or 48000)
    tmp = Path(tempfile.mkdtemp(prefix="desk-edit-"))
    st = State(D=float(D), W=W, H=H, fps=fps, sr=sr, timeline=Timeline.identity(float(D)), tmp=tmp, info=info, lossy_out=_lossy_audio_out(info, out, eo))
    g = Graph(str(src), vpos, apos)
    try:
        # A leading trim becomes an input seek: fast, and exact because the stream is re-encoded.
        rest = list(ops)
        if rest and str(rest[0].get("op")).lower() == "trim":
            p = rest.pop(0)
            s = _t(p, "start", st, 0.0) or 0.0
            e = _t(p, "end", st)
            if e is None and p.get("duration") is not None:
                from _media import parse_time

                e = s + parse_time(p["duration"], None, "duration")
            e = min(e if e is not None else st.D, st.D)
            if e <= s:
                raise UsageError("trim: the end must be after the start")
            g.inputs[0] = ["-ss", secs_arg(s), "-t", secs_arg(e - s), "-i", str(src)]
            st.timeline.keep([(s, e)])
            st.D = e - s
            st.done.append("trim")

        def analyse(graph: Graph, audio_filter: str) -> str:
            chains = list(graph.chains)
            chains.append(f"{graph.a}{audio_filter}[meas]")
            if graph.v and graph.v != graph.v0:
                chains.append(f"{graph.v}nullsink")
            return run_ffmpeg([*graph.input_args(), "-filter_complex", ";".join(chains), "-map", "[meas]", "-f", "null", "-"],
                              duration=st.D, label="measuring loudness", loglevel="info", cwd=st.cwd)

        def detect() -> tuple[int, int, int, int] | None:
            return detect_crop(str(src), float(D), vpos or 0)

        for p in rest:
            n = str(p.get("op")).lower()
            if n == "normalize":
                op_normalize(g, st, p, analyse)
            elif n == "crop":
                op_crop(g, st, p, detect)
            else:
                OPS[n](g, st, p)
            st.done.append(n)

        want_v = v is not None and not eo.get("no_video")
        want_a = a is not None and not st.drop_audio and not eo.get("no_audio")
        changed = st.timeline.changed
        from _media import can_copy, container_for

        cont = container_for(out)
        if cont.get("audio_only"):
            want_v = False
        if cont.get("video_only"):
            want_a = False
        graph = g.text()
        if graph:
            # Branches nobody maps must still be consumed.
            extra = []
            if g.v and g.v != g.v0 and not want_v:
                extra.append(f"{g.v}nullsink")
            if g.a and g.a != g.a0 and not want_a:
                extra.append(f"{g.a}anullsink")
            graph = ";".join([graph, *extra])
        vlabel = (g.v if g.v != g.v0 else f"0:v:{vpos}") if want_v else None
        alabel = (g.a if g.a != g.a0 else f"0:a:{apos}") if want_a else None
        copy_v = want_v and g.v == g.v0 and not changed and not eo.get("reencode") and can_copy(cont, "video", (v or {}).get("codec"))
        copy_a = want_a and g.a == g.a0 and not changed and not eo.get("reencode") and can_copy(cont, "audio", (a or {}).get("codec"))
        inputs = g.input_args()
        n_in = len(g.inputs)
        sub_inputs, sub_out, sub_notes = _carry_subtitles(info, out, st, changed, n_in)
        n_in += sub_inputs.count("-i")
        chap_args: list[str] = []
        if changed:
            meta = _chapters_file(info, st)
            if meta is not None:
                sub_inputs += ["-i", str(meta)]
                chap_args = ["-map_chapters", str(n_in)]
                n_in += 1
            else:
                chap_args = ["-map_chapters", "-1"]
        args: list[str] = [*inputs, *sub_inputs]
        if graph:
            args += ["-filter_complex", graph]
        if copy_v:
            args += ["-map", vlabel or "", "-c:v", "copy"]
        if copy_a:
            args += ["-map", alabel or "", "-c:a", "copy"]
        enc_args, enc_notes = encode_args(info, out, want_v and not copy_v, want_a and not copy_a, eo, None if copy_v else vlabel, None if copy_a else alabel)
        args += enc_args + sub_out + ["-map_metadata", "0", *brand_cleanup(out), *chap_args]
        st.notes += enc_notes + sub_notes
        if want_v and not copy_v and v and not v.get("vfr") and not cont.get("video_only"):
            # Constant frame rate out: trims and joins otherwise leave the last frame without a duration (60.13 fps).
            args += ["-fps_mode:v", "cfr", "-r:v", fps_rational(st.fps)]
        if len(g.inputs) > 1:
            args += ["-t", secs_arg(st.D)]
        if want_v and not copy_v and v and v.get("rotation"):
            st.notes.append(f"the {v['rotation']}° rotation flag was applied to the pixels")
        with TempOut(out) as tmpout:
            run_ffmpeg([*args, str(tmpout)], duration=st.D, label=label, cwd=st.cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if st.tp_target is not None and want_a and not copy_a:
        tp_out = _output_true_peak(out)
        k = names.index("normalize") if "normalize" in names else -1
        if tp_out is not None and tp_out > st.tp_target + 0.05:
            cheap = (not want_v or copy_v) and st.D <= 1800  # audio only and under 30 min: one more encode costs little
            if cheap and not eo.get("_tp_retry") and k >= 0:
                # One corrective encode, aimed lower by the overshoot just measured.
                prev = float(ops[k].get("_tp_run") if ops[k].get("_tp_run") is not None else st.tp_target - 1.0)
                fixed = [dict(o) for o in ops]
                fixed[k]["_tp_run"] = round(prev - (tp_out - st.tp_target) - 0.3, 2)
                res = run_pipeline(shown_src, shown_out, fixed, {**eo, "_tp_retry": tp_out}, label)
                res["seconds"] = round(time.monotonic() - t0, 2)
                return res
            st.notes.append(f"{'even after a second encode, ' if eo.get('_tp_retry') is not None else ''}"
                            f"the encoder pushed the true peak to {tp_out:.1f} dBTP, over the {st.tp_target:g} target; "
                            "a higher --audio-bitrate or a lower --true-peak leaves it more room")
        elif tp_out is not None and eo.get("_tp_retry") is not None:
            st.notes.append(f"the first encode peaked at {float(eo['_tp_retry']):.1f} dBTP, so it was encoded once more with a lower limit "
                            f"(true peak now {tp_out:.1f} dBTP)")
    res = summarize_output(out)
    res["file"] = str(shown_out)
    res.update({"input": str(shown_src), "output": str(shown_out), "operations": names, "notes": list(dict.fromkeys(st.notes)), "seconds": round(time.monotonic() - t0, 2),
                "video_action": ("copied" if copy_v else "re-encoded") if want_v else "none", "audio_action": ("copied" if copy_a else "re-encoded") if want_a else "none",
                "expected_duration": round(st.D, 3)})
    return res


def _output_true_peak(path: Path) -> float | None:
    """The written file's true peak (dBTP, 4x oversampled), or None when it cannot be read."""
    from _audio import column_stats, db
    from _media import main_audio, probe, read_audio

    try:
        info = probe(path, side_data=False, cache=False)
        a = main_audio(info) or {}
        ch = max(1, min(int(a.get("channels") or 1), 8))
        sr = int(a.get("sample_rate") or 48000)
        cs = column_stats(read_audio(path, sr=sr, channels=ch), max(1, int((info.get("duration") or 1) * sr)), 10, ch, true_peak=True)
        tp = float(cs["true_peak"].max()) if cs["samples"] else 0.0
    except Exception:  # noqa: BLE001 — a check, not a step: the output is already written
        return None
    return round(db(tp), 2) if tp > 0 else None


def _carry_subtitles(info: dict[str, Any], out: Path, st: State, changed: bool, first_input: int) -> tuple[list[str], list[str], list[str]]:
    """(extra inputs, output options, notes) that keep the source's subtitle tracks, converted to what the container
    holds (mov_text becomes SRT in .mkv, WebVTT in .webm); re-timed when the timeline changed."""
    from _media import container_for, run_ffmpeg, sub_target
    from _subs import Cue, load, save

    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    cont = container_for(out)
    if not subs or cont.get("audio_only") or cont.get("video_only"):
        return [], [], []
    if not cont.get("s"):
        return [], [], [f"dropped {len(subs)} subtitle track(s): {out.suffix} cannot hold subtitles"]
    notes: list[str] = []
    inputs: list[str] = []
    maps: list[str] = []
    k_in = first_input
    n_out = 0
    for pos, s in enumerate(subs):
        target = sub_target(cont, s)
        if target is None:
            notes.append(f"dropped subtitle track {pos + 1} ({s.get('codec')}): {out.suffix} cannot hold it")
            continue
        if not changed:
            maps += ["-map", f"0:s:{pos}", f"-c:s:{n_out}", target]
            n_out += 1
            continue
        if not s.get("text_based"):
            notes.append(f"dropped image subtitle track {pos + 1} ({s.get('codec')}): it cannot be re-timed")
            continue
        f = st.tmp / f"sub{pos}.srt"
        try:
            run_ffmpeg(["-i", info["file"], "-map", f"0:s:{pos}", "-c:s", "srt", str(f)], label="reading subtitles", progress=False)
            doc = load(f)
        except SkillError:
            notes.append(f"dropped subtitle track {pos + 1} (could not re-time it)")
            continue
        cues = []
        for c in doc.cues:
            for x, y in st.timeline.map_range(c.start, c.end):
                if y - x >= 0.15:
                    cues.append(Cue(x, y, c.text, align=c.align))
        if not cues:
            continue
        doc.cues = cues
        g = st.tmp / f"sub{pos}.retimed.srt"
        save(doc, g, "srt")
        inputs += ["-i", str(g)]
        maps += ["-map", f"{k_in}:0", f"-c:s:{n_out}", cont["s"] if cont["s"] != "copy" else "srt"]
        if s.get("language"):
            maps += [f"-metadata:s:s:{n_out}", f"language={s['language']}"]
        n_out += 1
        k_in += 1
        if s.get("codec") in ("ass", "ssa"):
            notes.append(f"subtitle track {pos + 1} was re-timed as plain text (ASS styling dropped)")
    if inputs:
        notes.append("subtitles re-timed to follow the edit")
    return inputs, maps, notes


def _chapters_file(info: dict[str, Any], st: State) -> Path | None:
    chs = info.get("chapters") or []
    if not chs:
        return None
    lines = [";FFMETADATA1"]
    kept = 0
    for c in chs:
        rng = st.timeline.map_range(c["start"], c["end"])
        if not rng:
            continue
        a, b = rng[0][0], rng[-1][1]
        if b - a < 0.5:
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(a * 1000)}", f"END={int(b * 1000)}", f"title={_ffmeta_escape(c.get('title') or '')}"]
        kept += 1
    if not kept:
        return None
    f = st.tmp / "chapters.txt"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _ffmeta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def detect_crop(path: str, duration: float, vpos: int) -> tuple[int, int, int, int] | None:
    """Black borders from cropdetect on a few spread-out samples; the union of what they keep."""
    from _common import pool_map
    from _media import run_ffmpeg, secs_arg

    points = [duration * f for f in (0.15, 0.35, 0.55, 0.75, 0.9)] if duration > 12 else [0.0]
    length = min(2.0, duration)

    def one(t: float) -> tuple[int, int, int, int] | None:
        text = run_ffmpeg(["-ss", secs_arg(t), "-i", path, "-t", secs_arg(length), "-map", f"0:v:{vpos}", "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                          label="finding black borders", loglevel="info", progress=False)
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text)
        if not found:
            return None
        w, h, x, y = (int(v) for v in found[-1])
        return w, h, x, y

    boxes = [b for b in pool_map(one, points, threads=True) if b]
    if not boxes:
        return None
    x0 = min(b[2] for b in boxes)
    y0 = min(b[3] for b in boxes)
    x1 = max(b[2] + b[0] for b in boxes)
    y1 = max(b[3] + b[1] for b in boxes)
    w, h = x1 - x0, y1 - y0
    return w - w % 2, h - h % 2, x0, y0
