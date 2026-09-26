"""Media helpers for the audio-video skill: ffmpeg discovery and capabilities, probing with PyAV, time parsing,
codec choices per container, and an ffmpeg runner that reports progress.

Heavy imports (av, numpy, PIL, imageio_ffmpeg) happen inside functions so every script's --help stays fast.
Works on Windows, macOS and Linux: no shell, pathlib everywhere, temp files through tempfile.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError, UsageError

#: Bumped when probe() output changes, so cached probes of older code are not reused.
PROBE_VERSION = "2"

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".aifc", ".wma", ".caf", ".m4b", ".ac3", ".alac", ".amr", ".mka", ".wv", ".ape"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".flv", ".3gp", ".ogv", ".vob", ".mxf", ".gif", ".asf", ".divx", ".f4v"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sbv", ".lrc", ".sub"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# ── time values ─────────────────────────────────────────────────────────


def parse_time(value: Any, duration: float | None = None, what: str = "time") -> float:
    """Seconds from '83.5', '1:23.5', '01:02:03,250', '1h2m3s', '90s', '500ms', '25%', 'end', or '-10' (10 s before the end)."""
    if isinstance(value, (int, float)):
        v = float(value)
        if v < 0:
            if duration is None:
                raise UsageError(f"a negative {what} counts from the end, but the duration is unknown")
            return max(0.0, duration + v)
        return v
    s = str(value).strip().lower()
    if not s:
        raise UsageError(f"empty {what}")
    if s in ("start", "begin", "0"):
        return 0.0
    if s == "end":
        if duration is None:
            raise UsageError(f"'{value}' needs a known duration")
        return duration
    if s.endswith("%"):
        if duration is None:
            raise UsageError(f"'{value}' needs a known duration")
        try:
            pct = float(s[:-1])
        except ValueError:
            raise UsageError(f"bad {what} '{value}'") from None
        return max(0.0, min(duration, duration * pct / 100.0))
    neg = s.startswith("-")
    body = s.lstrip("+-").strip()
    secs = _parse_positive(body)
    if secs is None:
        raise UsageError(f"bad {what} '{value}' (use seconds like 83.5, a clock like 1:23.5 or 01:02:03.250, 1h2m3s, 25% or end)")
    if neg:
        if duration is None:
            raise UsageError(f"'{value}' counts from the end, but the duration is unknown")
        return max(0.0, duration - secs)
    return secs


def _parse_positive(s: str) -> float | None:
    if re.fullmatch(r"\d+(\.\d*)?|\.\d+", s):
        return float(s)
    if ":" in s:
        parts = s.replace(",", ".").split(":")
        if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d*)?", p) for p in parts):
            return None
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m(?!s))?\s*(?:(\d+(?:\.\d+)?)\s*s)?\s*(?:(\d+(?:\.\d+)?)\s*ms)?", re.sub(r"\s+", " ", s))  # one space per gap: the adjacent \s* runs backtrack on long ones
    if m and any(m.groups()):
        h, mi, se, ms = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + se + ms / 1000
    return None


def parse_time_list(spec: str, duration: float | None = None) -> list[float]:
    """'10, 1:30, 50%, end' → seconds (commas or semicolons separate values)."""
    out = []
    for tok in re.split(r"[;,]\s*(?=[\d.+\-]|end|start)|;|\s+", spec.strip()):
        if tok.strip():
            out.append(parse_time(tok, duration))
    if not out:
        raise UsageError("no times given")
    return out


def parse_time_ranges(spec: str, duration: float | None = None) -> list[tuple[float, float]]:
    """'10-15, 1:20-1:25.5, 2:00-end, 30+5' → sorted, merged (start, end) pairs in seconds."""
    ranges: list[tuple[float, float]] = []
    for part in re.split(r"[;,]", spec):
        p = part.strip()
        if not p:
            continue
        m = re.fullmatch(r"(.+?)\s*\+\s*(.+)", p)
        if m:
            a = parse_time(m.group(1), duration)
            b = a + parse_time(m.group(2), duration, "length")
        else:
            m = re.fullmatch(r"(.*?[^\s-])?\s*(?:-|\.\.|→|to)\s*(.+)?", p)
            if not m or (m.group(1) is None and m.group(2) is None):
                raise UsageError(f"bad range '{p}' (use start-end like 10-15 or 1:20-1:25, or start+length like 30+5)")
            a = parse_time(m.group(1), duration) if m.group(1) else 0.0
            b = parse_time(m.group(2), duration) if m.group(2) else (duration if duration is not None else float("inf"))
        if duration is not None:
            b = min(b, duration)
        if b <= a:
            raise UsageError(f"range '{p}' is empty or runs backwards")
        ranges.append((a, b))
    if not ranges:
        raise UsageError("no ranges given")
    ranges.sort()
    merged = [ranges[0]]
    for a, b in ranges[1:]:
        if a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def fmt_time(t: float | None, ms: bool = True) -> str:
    """1:23.500, or 1:02:03.500 past an hour."""
    if t is None:
        return "?"
    neg = t < 0
    t = abs(t)
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    frac = f".{msec:03d}" if ms else ""
    body = f"{h}:{m:02d}:{s:02d}{frac}" if h else f"{m}:{s:02d}{frac}"
    return ("-" if neg else "") + body


def fmt_stamp(t: float) -> str:
    """A short timestamp for listings: 1:23.5, 12:30, 1:02:03.25 (no trailing zeros)."""
    x = fmt_time(t)
    return x[:-4] if x.endswith(".000") else x.rstrip("0")


def fmt_dur(t: float | None) -> str:
    """A compact duration: 45.2s, 3:05, 1:02:03."""
    if t is None:
        return "?"
    if t < 60:
        return f"{t:.1f}s"
    return fmt_time(t, ms=False)


def secs_arg(t: float) -> str:
    """A time for ffmpeg's command line."""
    return f"{max(0.0, t):.6f}".rstrip("0").rstrip(".") or "0"


def fmt_size(n: float | None) -> str:
    """A file size in decimal units (1 MB = 1,000,000 bytes, as --target-mb, Finder and mail limits count)."""
    if not n:
        return "0 B"
    for unit, k in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(n) >= k:
            v = n / k
            return f"{v:.2f} {unit}" if v < 10 else f"{v:.1f} {unit}"
    return f"{int(n)} B"


# ── cache switches ──────────────────────────────────────────────────────


def add_no_cache(p: Any) -> None:
    p.add_argument("--no-cache", action="store_true", help="recompute instead of reusing cached results (probes, frames, sheets, audio analysis, transcripts)")


def apply_no_cache(a: Any) -> None:
    """--no-cache: turn the shared file cache off for this process (and the workers it starts)."""
    if getattr(a, "no_cache", False):
        os.environ["DESK_NO_CACHE"] = "1"


# ── ffmpeg ──────────────────────────────────────────────────────────────

_FFMPEG: str | None = None


def ffmpeg() -> str:
    global _FFMPEG
    if _FFMPEG is None:
        from _render import ffmpeg_path

        _FFMPEG = ffmpeg_path()
    return _FFMPEG


_CAPS: dict[str, set[str]] | None = None


def caps() -> dict[str, set[str]]:
    """The ffmpeg binary's encoders, decoders, filters and muxers, cached in the temp dir per binary."""
    global _CAPS
    if _CAPS is not None:
        return _CAPS
    exe = Path(ffmpeg())
    try:
        st = exe.stat()
        key = f"{exe}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = str(exe)
    cache = Path(tempfile.gettempdir()) / "desk-ffmpeg-caps.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("key") == key:
            _CAPS = {k: set(v) for k, v in data["caps"].items()}
            return _CAPS
    except (OSError, ValueError, KeyError):
        pass
    out: dict[str, set[str]] = {}
    for kind, flag in (("encoders", "-encoders"), ("filters", "-filters"), ("muxers", "-muxers")):
        r = subprocess.run([str(exe), "-hide_banner", flag], capture_output=True, timeout=60)
        names = set()
        started = kind == "filters"
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if not started:
                if line.strip().startswith("--"):
                    started = True
                continue
            parts = line.split()
            if kind == "filters":
                if len(parts) >= 3 and "->" in parts[2]:
                    names.add(parts[1])
            elif len(parts) >= 2:
                names.add(parts[1])
        out[kind] = names
    _CAPS = out
    try:
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "caps": {k: sorted(v) for k, v in out.items()}}), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return out


def _hidden() -> set[str]:
    """Names listed in DESK_FFMPEG_HIDE (comma-separated) are treated as missing: tests the fallbacks."""
    return {x.strip() for x in os.environ.get("DESK_FFMPEG_HIDE", "").split(",") if x.strip()}


def has_encoder(name: str) -> bool:
    return name in caps()["encoders"] and name not in _hidden()


def has_filter(name: str) -> bool:
    return name in caps()["filters"] and name not in _hidden()


class Progress:
    """Prints sparse progress lines on stderr (about every 10% or 15 s) so long jobs show they are alive."""

    def __init__(self, total: float | None, label: str = "working") -> None:
        self.total = total if total and total > 0 else None
        self.label = label
        self.t0 = time.monotonic()
        self.last_pct = -100.0
        self.last_t = self.t0
        self.quiet = bool(os.environ.get("DESK_QUIET"))

    def update(self, done: float) -> None:
        if self.quiet:
            return
        now = time.monotonic()
        if self.total:
            pct = max(0.0, min(100.0, 100.0 * done / self.total))
            if (pct - self.last_pct >= 10 and now - self.t0 > 2) or now - self.last_t >= 15:
                speed = done / max(now - self.t0, 1e-6)
                print(f"{self.label}: {pct:3.0f}% ({fmt_dur(done)} of {fmt_dur(self.total)}, {speed:.1f}x)", file=sys.stderr, flush=True)
                self.last_pct, self.last_t = pct, now
        elif now - self.last_t >= 15:
            print(f"{self.label}: {fmt_dur(done)} done", file=sys.stderr, flush=True)
            self.last_t = now


def run_ffmpeg(args: Sequence[Any], duration: float | None = None, label: str = "ffmpeg", timeout: float | None = None, loglevel: str = "error", progress: bool = True, cwd: str | os.PathLike[str] | None = None) -> str:
    """Runs ffmpeg with `args` (inputs, filters, outputs) and returns its stderr text.

    Progress comes from `-progress pipe:1`; stderr is drained on a thread (no deadlock, works on Windows).
    Raises SkillError with the useful tail of ffmpeg's messages when it fails.
    """
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel]
    if progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += [str(a) for a in args]
    if os.environ.get("DESK_DEBUG"):
        print("+ " + " ".join(_quote(c) for c in cmd), file=sys.stderr)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if progress else subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=cwd)
    except FileNotFoundError as e:
        raise SkillError("ffmpeg is not available in this skill's runtime") from e
    err_lines: deque[str] = deque(maxlen=4000)

    def drain() -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            err_lines.append(raw.decode("utf-8", "replace").rstrip("\r\n"))

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    prog = Progress(duration, label) if progress else None
    deadline = time.monotonic() + timeout if timeout else None
    try:
        if progress and proc.stdout is not None:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("ascii", "replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                        if prog is not None and us > 0:
                            prog.update(us / 1e6)
                    except ValueError:
                        pass
                if deadline and time.monotonic() > deadline:
                    proc.kill()
                    raise SkillError(f"{label} timed out after {timeout:.0f}s")
        rc = proc.wait(timeout=max(1.0, deadline - time.monotonic()) if deadline else None)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SkillError(f"{label} timed out after {timeout:.0f}s") from None
    except KeyboardInterrupt:
        proc.kill()
        raise
    t.join(timeout=10)
    text = "\n".join(err_lines)
    if rc != 0:
        raise SkillError(f"ffmpeg failed ({label}): {ffmpeg_error(text)}")
    return text


def ffmpeg_error(text: str) -> str:
    """The informative part of ffmpeg's error output."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    noise = ("Conversion failed", "Error opening output file", "Error opening output files", "Nothing was written", "Terminating thread")
    useful = [ln for ln in lines if not any(ln.startswith(n) for n in noise)]
    tail = (useful or lines)[-6:]
    msg = " | ".join(tail) if tail else "unknown error"
    return msg[:1500]


def _quote(s: str) -> str:
    return s if re.fullmatch(r"[\w./:=,+@%-]+", s) else "'" + s.replace("'", "'\\''") + "'"


def ffmpeg_pipe(args: Sequence[Any], loglevel: str = "error") -> subprocess.Popen[bytes]:
    """Starts ffmpeg writing raw data to stdout (the caller reads proc.stdout and then calls finish_pipe)."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", loglevel, *[str(a) for a in args]]
    if os.environ.get("DESK_DEBUG"):
        print("+ " + " ".join(_quote(c) for c in cmd), file=sys.stderr)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines: deque[str] = deque(maxlen=4000)

    def drain() -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            lines.append(raw.decode("utf-8", "replace").rstrip("\r\n"))

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    proc._desk_err = lines  # type: ignore[attr-defined]
    proc._desk_thread = th  # type: ignore[attr-defined]
    return proc


def finish_pipe(proc: subprocess.Popen[bytes], label: str = "ffmpeg", allow_fail: bool = False) -> str:
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass
    rc = proc.wait()
    proc._desk_thread.join(timeout=10)  # type: ignore[attr-defined]
    text = "\n".join(proc._desk_err)  # type: ignore[attr-defined]
    if rc != 0 and not allow_fail:
        raise SkillError(f"ffmpeg failed ({label}): {ffmpeg_error(text)}")
    return text


def read_audio(path: Path | str, sr: int = 16000, channels: int | None = 1, start: float | None = None, end: float | None = None, stream: int | None = None, block_seconds: float = 10.0) -> Iterable[Any]:
    """Streams decoded audio as float32 numpy blocks shaped (n, channels); resampled to `sr` (None keeps the rate)."""
    import numpy as np

    args: list[Any] = []
    if start:
        args += ["-ss", secs_arg(start)]
    args += ["-i", str(path)]
    if end is not None:
        args += ["-t", secs_arg(end - (start or 0.0))]
    args += ["-map", f"0:a:{stream or 0}", "-vn", "-sn", "-dn"]
    if channels:
        args += ["-ac", str(channels)]
    if sr:
        args += ["-ar", str(sr)]
    args += ["-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
    proc = ffmpeg_pipe(args)
    ch = channels or 1
    block = int((sr or 48000) * block_seconds) * ch * 4
    assert proc.stdout is not None
    leftover = b""
    try:
        while True:
            data = proc.stdout.read(block)
            if not data:
                break
            data = leftover + data
            usable = len(data) - (len(data) % (4 * ch))
            leftover = data[usable:]
            if usable:
                yield np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, ch)
    finally:
        finish_pipe(proc, "decoding audio")


# ── probing ─────────────────────────────────────────────────────────────

_PRIMARIES = {1: "bt709", 4: "bt470m", 5: "bt470bg", 6: "smpte170m", 7: "smpte240m", 8: "film", 9: "bt2020", 10: "smpte428", 11: "smpte431 (DCI-P3)", 12: "smpte432 (Display P3)", 22: "ebu3213"}
_TRC = {1: "bt709", 4: "gamma22", 5: "gamma28", 6: "smpte170m", 7: "smpte240m", 8: "linear", 11: "iec61966-2-4", 13: "srgb", 14: "bt2020-10", 15: "bt2020-12", 16: "smpte2084 (PQ)", 17: "smpte428", 18: "arib-std-b67 (HLG)"}
_MATRIX = {0: "rgb", 1: "bt709", 4: "fcc", 5: "bt470bg", 6: "smpte170m", 7: "smpte240m", 8: "ycgco", 9: "bt2020nc", 10: "bt2020c", 14: "ictcp"}
_RANGE = {1: "limited (tv)", 2: "full (pc)"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext"}
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "jacosub", "sami", "realtext", "pjs", "mpl2", "vplayer", "stl", "eia_608", "ttml"}
_NOISY_TAGS = {"handler_name", "vendor_id", "encoder", "duration", "_statistics_tags", "_statistics_writing_app", "_statistics_writing_date_utc", "number_of_frames", "number_of_bytes", "bps"}


def _frac(x: Any) -> float | None:
    try:
        if x is None:
            return None
        f = float(x)
        return f if f == f and f > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _clean_tags(md: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in (md or {}).items():
        lk = k.lower()
        if lk in _NOISY_TAGS or lk.startswith("_statistics") or lk.startswith("number_of_") or lk.startswith("bps"):
            continue
        if v is None or str(v).strip() == "":
            continue
        out[k] = str(v)[:500]
    return out


def open_container(path: Path | str) -> Any:
    import av

    try:
        return av.open(str(path), metadata_errors="ignore")
    except av.error.FileNotFoundError:
        raise SkillError(f"{path} does not exist") from None
    except (av.error.InvalidDataError, av.error.PermissionError) as e:
        raise SkillError(diagnose(Path(path), str(e.strerror or e))) from None
    except av.FFmpegError as e:
        raise SkillError(f"{Path(path).name}: {e.strerror or e}") from None


def diagnose(path: Path, reason: str) -> str:
    """Why ffmpeg cannot open a file, in words: empty, truncated MP4/MOV (no index), text, or not media at all."""
    name = path.name
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return f"{name}: not a media file ffmpeg can read ({reason})"
    if size == 0:
        return f"{name} is empty (0 bytes): the recording, copy or download did not finish"
    if head[4:8] in (b"ftyp", b"wide", b"mdat", b"free", b"skip", b"moov"):
        boxes = _mp4_top_boxes(path, size)
        if "moov" not in boxes:
            return (f"{name} is an MP4/MOV without its index (no 'moov' box; top-level boxes: {', '.join(boxes) or 'none'}): the recording was "
                    "interrupted or the file is truncated, so players and ffmpeg cannot read it. Recover it from the original device or app")
        return f"{name}: the MP4/MOV structure is damaged ({reason})"
    if head[:4] in (b"RIFF", b"FORM", b"fLaC", b"OggS", b"\x1a\x45\xdf\xa3") or head[:3] == b"ID3":
        return f"{name}: the file starts like media but cannot be read; it is probably truncated or damaged ({reason})"
    sample = head[:512]
    if sample and all(b in b"\t\n\r" or 32 <= b < 127 or b >= 128 for b in sample) and b"\x00" not in sample:
        first = sample.decode("utf-8", "replace").strip().splitlines()[0][:80] if sample.strip() else ""
        return f"{name} is a text file, not a media file (it starts with {first!r})"
    return f"{name}: not a media file ffmpeg can read ({reason})"


def _mp4_top_boxes(path: Path, size: int, limit: int = 64) -> list[str]:
    """Names of the top-level ISO-BMFF boxes (stops at a box that runs past the end of the file)."""
    import struct

    names: list[str] = []
    pos = 0
    try:
        with open(path, "rb") as f:
            while pos + 8 <= size and len(names) < limit:
                f.seek(pos)
                hdr = f.read(16)
                n, kind = struct.unpack(">I4s", hdr[:8])
                if n == 1 and len(hdr) >= 16:
                    n = struct.unpack(">Q", hdr[8:16])[0]
                elif n == 0:
                    n = size - pos
                name = kind.decode("latin-1")
                if n < 8 or not name.isprintable():
                    break
                names.append(name + ("(cut)" if pos + n > size else ""))
                pos += n
    except (OSError, struct.error):
        pass
    return names


def probe(path: Path | str, side_data: bool = True, exact_duration: bool = False, cache: bool = True) -> dict[str, Any]:
    """Container, streams, chapters and tags of a media file (PyAV), plus rotation and HDR side data (ffmpeg -i).

    Cached per file content (DESK_NO_CACHE / --no-cache turn it off), so repeated calls do not even import PyAV.
    """
    path = Path(path)
    if not cache:
        return _probe(path, side_data, exact_duration)
    try:
        import _cache

        info = None
        for sd in ((True, False) if not side_data else (True,)):
            hit = _cache.lookup(path, "av-probe", {"side": sd, "exact": exact_duration}, PROBE_VERSION)
            if hit is not None:
                try:
                    info = json.loads((hit / "value.json").read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    info = None
        if info is None:
            info = _cache.cached_json(path, "av-probe", {"side": side_data, "exact": exact_duration}, PROBE_VERSION, lambda: _probe(path, side_data, exact_duration))
    except OSError:
        return _probe(path, side_data, exact_duration)
    # Copies share an entry: the path and name are this file's.
    info = dict(info)
    info["file"], info["name"] = str(path), path.name
    return info


def _probe(path: Path, side_data: bool = True, exact_duration: bool = False) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise SkillError(f"{path} does not exist")
    c = open_container(path)
    try:
        fmt = c.format
        info: dict[str, Any] = {
            "file": str(path),
            "name": path.name,
            "size": path.stat().st_size,
            "container": fmt.name,
            "container_long": fmt.long_name,
            "duration": (c.duration / 1_000_000) if c.duration else None,
            "start": (c.start_time / 1_000_000) if c.start_time else 0.0,
            "bit_rate": c.bit_rate or None,
            "tags": _clean_tags(dict(c.metadata)),
            "chapters": [],
            "streams": [],
        }
        try:
            for ch in c.chapters():
                tb = ch.get("time_base") or Fraction(1, 1000)
                info["chapters"].append({"start": float(ch["start"] * tb), "end": float(ch["end"] * tb), "title": (ch.get("metadata") or {}).get("title", "")})
        except Exception:  # noqa: BLE001 — older containers without chapter support
            pass
        for s in c.streams:
            info["streams"].append(_stream_info(s))
        if info["duration"] is None or exact_duration:
            d = _demux_duration(c)
            if d:
                info["duration"] = d
                info["duration_estimated"] = not exact_duration
        if info["bit_rate"] is None and info["duration"]:
            info["bit_rate"] = int(info["size"] * 8 / info["duration"])
    finally:
        c.close()
    if side_data and any((s["type"] == "video" and not s.get("cover_art")) or (s["type"] in ("data", "attachment") and not s.get("codec")) for s in info["streams"]):
        _add_side_data(path, info)
    v = main_video(info)
    a = main_audio(info)
    info["kind"] = "video" if v else ("audio" if a else ("subtitles" if any(s["type"] == "subtitle" for s in info["streams"]) else "other"))
    return info


def _stream_info(s: Any) -> dict[str, Any]:
    cc = s.codec_context
    tb = s.time_base
    d: dict[str, Any] = {"index": s.index, "type": s.type}
    try:
        d["codec"] = (getattr(cc.codec, "canonical_name", None) or cc.name) if cc is not None else None
        d["codec_long"] = cc.codec.long_name if cc is not None and cc.codec is not None else None
    except Exception:  # noqa: BLE001 — unknown codecs have no descriptor
        d["codec"] = d.get("codec") or "unknown"
    prof = getattr(s, "profile", None) or (getattr(cc, "profile", None) if cc is not None else None)
    if prof:
        d["profile"] = str(prof)
    br = getattr(cc, "bit_rate", None) if cc is not None else None
    if not br:
        md = {k.lower(): v for k, v in (s.metadata or {}).items()}
        try:
            br = int(md.get("bps") or md.get("bps-eng") or 0) or None
        except ValueError:
            br = None
    if br:
        d["bit_rate"] = int(br)
    lang = s.language if getattr(s, "language", None) not in (None, "und") else None
    if lang:
        d["language"] = lang
    md = dict(s.metadata or {})
    title = md.get("title") or md.get("TITLE")
    if title:
        d["title"] = title
    try:
        disp = s.disposition
        flags = [name for name, member in type(disp).__members__.items() if member and (disp & member) == member]
    except Exception:  # noqa: BLE001
        flags = []
    if flags:
        d["disposition"] = flags
    if s.duration and tb:
        d["duration"] = float(s.duration * tb)
    if s.start_time and tb:
        d["start"] = float(s.start_time * tb)
    if getattr(s, "frames", 0):
        d["frames"] = int(s.frames)
    tags = _clean_tags(md)
    tags.pop("title", None)
    tags.pop("language", None)
    tags.pop("TITLE", None)
    if tags:
        d["tags"] = tags
    if s.type == "video" and cc is not None:
        d["width"], d["height"] = cc.width, cc.height
        d["pix_fmt"] = cc.pix_fmt
        d["bit_depth"] = _bit_depth(cc.pix_fmt)
        sar = s.sample_aspect_ratio or cc.sample_aspect_ratio
        if sar and sar != 1 and float(sar) > 0:
            d["sar"] = f"{sar.numerator}:{sar.denominator}"
            d["display_width"] = int(round(cc.width * float(sar) / 2) * 2)
        dar = s.display_aspect_ratio
        if dar:
            d["dar"] = f"{dar.numerator}:{dar.denominator}"
        avg = _frac(s.average_rate)
        base = _frac(s.base_rate)
        fps = avg or base or _frac(s.guessed_rate)
        if fps:
            d["fps"] = round(fps, 3)
        if avg and base and abs(avg - base) / base > 0.02:
            d["vfr"] = True
            d["fps_max"] = round(base, 3)
        colors = {
            "range": _RANGE.get(cc.color_range),
            "primaries": _PRIMARIES.get(cc.color_primaries),
            "transfer": _TRC.get(cc.color_trc),
            "matrix": _MATRIX.get(cc.colorspace),
        }
        colors = {k: v for k, v in colors.items() if v}
        if colors:
            d["color"] = colors
        if cc.color_trc == 16:
            d["hdr"] = "HDR10 (PQ)"
        elif cc.color_trc == 18:
            d["hdr"] = "HLG"
        try:
            fo = str(cc.field_order) if cc.field_order is not None else ""
            if fo and fo.lower() not in ("progressive", "unknown", "0", "1"):
                d["interlaced"] = fo
        except Exception:  # noqa: BLE001
            pass
        if "attached_pic" in flags:
            d["cover_art"] = True
    elif s.type == "audio" and cc is not None:
        d["sample_rate"] = cc.sample_rate
        d["channels"] = cc.channels
        try:
            d["layout"] = cc.layout.name
        except Exception:  # noqa: BLE001
            pass
        try:
            d["sample_fmt"] = cc.format.name
        except Exception:  # noqa: BLE001
            pass
        bits = getattr(cc, "bits_per_coded_sample", 0) or 0
        name = d.get("codec") or ""
        if name.startswith("pcm_"):
            m = re.search(r"(\d+)", name)
            if m:
                bits = int(m.group(1))
        elif name in ("flac", "alac", "wavpack", "ape", "tta", "truehd", "mlp"):
            fmtname = d.get("sample_fmt") or ""
            bits = 16 if fmtname.startswith("s16") else (24 if fmtname.startswith("s32") else bits)
        if bits and (name.startswith("pcm_") or name in ("flac", "alac", "wavpack", "ape", "tta")):
            d["bit_depth"] = bits
    elif s.type == "subtitle":
        d["text_based"] = (d.get("codec") or "") in TEXT_SUB_CODECS
    elif s.type == "attachment":
        fn = md.get("filename")
        if fn:
            d["filename"] = fn
        mt = md.get("mimetype")
        if mt:
            d["mimetype"] = mt
    return d


def _bit_depth(pix_fmt: str | None) -> int | None:
    if not pix_fmt:
        return None
    m = re.search(r"p(\d{2})(le|be)?$", pix_fmt)
    if m:
        return int(m.group(1))
    if pix_fmt in ("rgb48le", "rgb48be", "rgba64le", "gray16le", "gray16be"):
        return 16
    return 8


def _demux_duration(c: Any) -> float | None:
    """The duration from packet timestamps, or summed packet durations (for containers that store neither)."""
    end = 0.0
    start: float | None = None
    sums: dict[int, float] = {}
    try:
        for pkt in c.demux():
            if pkt.time_base is None:
                continue
            dur = float(pkt.duration * pkt.time_base) if pkt.duration else 0.0
            sums[pkt.stream.index] = sums.get(pkt.stream.index, 0.0) + dur
            if pkt.pts is None:
                continue
            t0 = float(pkt.pts * pkt.time_base)
            if start is None or t0 < start:
                start = t0
            end = max(end, t0 + dur)
    except Exception:  # noqa: BLE001 — damaged tail: keep what we read
        pass
    if start is not None and end > start:
        return end - start
    return max(sums.values(), default=0.0) or None


_SIDE_RE = {
    "rotation": re.compile(r"displaymatrix: rotation of (-?[\d.]+) degrees"),
    "dovi": re.compile(r"DOVI configuration record: (.*)"),
    "mastering": re.compile(r"Mastering Display Metadata, (.*)"),
    "cll": re.compile(r"Content Light Level Metadata, (.*)"),
    "spherical": re.compile(r"spherical: (.*)"),
    "stereo3d": re.compile(r"stereo3d: (.*)"),
}


def _add_side_data(path: Path, info: dict[str, Any]) -> None:
    try:
        r = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired, SkillError):
        return
    text = r.stderr.decode("utf-8", "replace")
    by_index = {s["index"]: s for s in info["streams"]}
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        m = re.match(r"\s*Stream #\d+:(\d+)", line)
        if m:
            current = by_index.get(int(m.group(1)))
            if current is not None and current["type"] in ("data", "attachment") and not current.get("codec"):
                mm = re.search(r": (?:Data|Attachment): (\w+)(?: \((\w+))?", line)
                if mm:
                    current["codec"] = mm.group(2) if mm.group(1) == "none" and mm.group(2) else mm.group(1)
            continue
        if current is None or current["type"] != "video":
            continue
        for key, rx in _SIDE_RE.items():
            mm = rx.search(line)
            if not mm:
                continue
            if key == "rotation":
                rot = float(mm.group(1))
                rot = int(round(rot)) % 360
                if rot > 180:
                    rot -= 360
                if rot:
                    current["rotation"] = rot
            elif key == "dovi":
                current["dolby_vision"] = mm.group(1).strip()
                prof = re.search(r"profile: (\d+)", mm.group(1))
                current["hdr"] = f"Dolby Vision{(' profile ' + prof.group(1)) if prof else ''}" + (f" + {current['hdr']}" if current.get("hdr") and "Dolby" not in current["hdr"] else "")
            elif key == "mastering":
                current["mastering_display"] = mm.group(1).strip()[:200]
            elif key == "cll":
                current["content_light_level"] = mm.group(1).strip()
            else:
                current[key] = mm.group(1).strip()


def main_video(info: dict[str, Any]) -> dict[str, Any] | None:
    """The main video stream: the default non-cover-art one, else the first."""
    vids = [s for s in info["streams"] if s["type"] == "video" and not s.get("cover_art")]
    if not vids:
        return None
    for s in vids:
        if "default" in s.get("disposition", []):
            return s
    return vids[0]


def main_audio(info: dict[str, Any]) -> dict[str, Any] | None:
    auds = [s for s in info["streams"] if s["type"] == "audio"]
    if not auds:
        return None
    for s in auds:
        if "default" in s.get("disposition", []):
            return s
    return auds[0]


def display_size(v: dict[str, Any]) -> tuple[int, int]:
    """The size a player shows: sample aspect ratio and rotation applied."""
    w = v.get("display_width") or v.get("width") or 0
    h = v.get("height") or 0
    if abs(v.get("rotation", 0)) == 90:
        w, h = h, w
    return int(w), int(h)


def require_duration(info: dict[str, Any]) -> float:
    d = info.get("duration")
    if not d:
        raise SkillError(f"{info['name']}: the duration is unknown (a live stream or a damaged file)")
    return float(d)


def audio_stream_pos(info: dict[str, Any], track: int | None) -> int:
    """The position among audio streams (for -map 0:a:N) of --track N (1-based), or of the main audio."""
    auds = [s for s in info["streams"] if s["type"] == "audio"]
    if not auds:
        raise SkillError(f"{info['name']} has no audio stream")
    if track is not None:
        if track < 1 or track > len(auds):
            raise UsageError(f"--track {track}: {info['name']} has {len(auds)} audio track(s)")
        return track - 1
    ma = main_audio(info)
    return auds.index(ma) if ma in auds else 0


def video_stream_pos(info: dict[str, Any]) -> int:
    vids = [s for s in info["streams"] if s["type"] == "video"]
    mv = main_video(info)
    if mv is None:
        raise SkillError(f"{info['name']} has no video stream")
    return vids.index(mv)


# ── containers and codecs ───────────────────────────────────────────────

#: Output container per extension: ffmpeg muxer, default video/audio/subtitle codec, codecs it can hold without re-encoding.
CONTAINERS: dict[str, dict[str, Any]] = {
    # vplay/aplay: codecs copied by default (they play in QuickTime, browsers, phones and TVs); the wider vcopy/acopy
    # sets are copied only on request (--copy, --vcodec copy, remux), with a note.
    ".mp4": {"muxer": "mp4", "v": "h264", "a": "aac", "s": "mov_text", "faststart": True,
             "vcopy": {"h264", "hevc", "av1", "mpeg4", "vp9", "mjpeg", "mpeg2video"}, "acopy": {"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac"}, "scopy": {"mov_text"},
             "vplay": {"h264", "hevc", "mpeg4"}, "aplay": {"aac", "mp3", "alac", "ac3", "eac3"}},
    ".m4v": {"muxer": "mp4", "v": "h264", "a": "aac", "s": "mov_text", "faststart": True,
             "vcopy": {"h264", "hevc", "mpeg4"}, "acopy": {"aac", "ac3", "eac3", "alac"}, "scopy": {"mov_text"}},
    ".mov": {"muxer": "mov", "v": "h264", "a": "aac", "s": "mov_text", "faststart": True,
             "vcopy": {"h264", "hevc", "prores", "mjpeg", "mpeg4", "av1", "png", "qtrle", "dnxhd"}, "acopy": {"aac", "alac", "mp3", "ac3", "pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be", "pcm_f32le"}, "scopy": {"mov_text"},
             "vplay": {"h264", "hevc", "prores", "mjpeg", "mpeg4", "png", "qtrle", "dnxhd"}},
    ".mkv": {"muxer": "matroska", "v": "h264", "a": "aac", "s": "copy", "vcopy": "*", "acopy": "*", "scopy": "*"},
    ".mka": {"muxer": "matroska", "v": None, "a": "opus", "s": "copy", "acopy": "*", "scopy": "*", "audio_only": True},
    ".webm": {"muxer": "webm", "v": "vp9", "a": "opus", "s": "webvtt", "vcopy": {"vp8", "vp9", "av1"}, "acopy": {"opus", "vorbis"}, "scopy": {"webvtt"}},
    ".avi": {"muxer": "avi", "v": "mpeg4", "a": "mp3", "s": None, "vcopy": {"mpeg4", "h264", "mjpeg", "msmpeg4v3"}, "acopy": {"mp3", "ac3", "pcm_s16le", "mp2"}},
    ".wmv": {"muxer": "asf", "v": "wmv2", "a": "wmav2", "s": None, "vcopy": {"wmv1", "wmv2", "wmv3", "vc1"}, "acopy": {"wmav2", "wmav1", "wmapro"}},
    ".asf": {"muxer": "asf", "v": "wmv2", "a": "wmav2", "s": None, "vcopy": {"wmv1", "wmv2", "wmv3", "vc1"}, "acopy": {"wmav2", "wmav1", "wmapro"}},
    ".mpg": {"muxer": "vob", "v": "mpeg2video", "a": "mp2", "s": None, "vcopy": {"mpeg1video", "mpeg2video"}, "acopy": {"mp2", "mp3", "ac3"}},
    ".mpeg": {"muxer": "vob", "v": "mpeg2video", "a": "mp2", "s": None, "vcopy": {"mpeg1video", "mpeg2video"}, "acopy": {"mp2", "mp3", "ac3"}},
    ".ts": {"muxer": "mpegts", "v": "h264", "a": "aac", "s": None, "vcopy": {"h264", "hevc", "mpeg2video", "mpeg1video"}, "acopy": {"aac", "mp3", "mp2", "ac3", "eac3", "opus"}},
    ".m2ts": {"muxer": "mpegts", "v": "h264", "a": "aac", "s": None, "vcopy": {"h264", "hevc", "mpeg2video"}, "acopy": {"aac", "ac3", "eac3", "mp2"}},
    ".mts": {"muxer": "mpegts", "v": "h264", "a": "aac", "s": None, "vcopy": {"h264", "hevc", "mpeg2video"}, "acopy": {"aac", "ac3", "eac3", "mp2"}},
    ".flv": {"muxer": "flv", "v": "h264", "a": "aac", "s": None, "vcopy": {"h264", "flv1"}, "acopy": {"aac", "mp3"}},
    ".ogv": {"muxer": "ogg", "v": "theora", "a": "vorbis", "s": None, "vcopy": {"theora", "vp8"}, "acopy": {"vorbis", "opus", "flac"}},
    ".3gp": {"muxer": "3gp", "v": "h264", "a": "aac", "s": "mov_text", "vcopy": {"h264", "h263", "mpeg4"}, "acopy": {"aac", "amr_nb", "amr_wb"}, "scopy": {"mov_text"}},
    ".gif": {"muxer": "gif", "v": "gif", "a": None, "s": None, "vcopy": {"gif"}, "video_only": True},
    # audio
    ".mp3": {"muxer": "mp3", "v": None, "a": "mp3", "acopy": {"mp3"}, "audio_only": True, "cover": True},
    ".m4a": {"muxer": "ipod", "v": None, "a": "aac", "acopy": {"aac", "alac"}, "audio_only": True, "cover": True, "faststart": True},
    ".m4b": {"muxer": "ipod", "v": None, "a": "aac", "acopy": {"aac", "alac"}, "audio_only": True, "cover": True, "faststart": True},
    ".aac": {"muxer": "adts", "v": None, "a": "aac", "acopy": {"aac"}, "audio_only": True},
    ".opus": {"muxer": "opus", "v": None, "a": "opus", "acopy": {"opus"}, "audio_only": True},
    ".ogg": {"muxer": "ogg", "v": None, "a": "vorbis", "acopy": {"vorbis", "opus", "flac"}, "audio_only": True},
    ".oga": {"muxer": "ogg", "v": None, "a": "vorbis", "acopy": {"vorbis", "opus", "flac"}, "audio_only": True},
    ".flac": {"muxer": "flac", "v": None, "a": "flac", "acopy": {"flac"}, "audio_only": True, "cover": True},
    ".wav": {"muxer": "wav", "v": None, "a": "pcm", "acopy": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8", "pcm_f64le"}, "audio_only": True},
    ".aif": {"muxer": "aiff", "v": None, "a": "pcm_be", "acopy": {"pcm_s16be", "pcm_s24be", "pcm_s32be"}, "audio_only": True},
    ".aiff": {"muxer": "aiff", "v": None, "a": "pcm_be", "acopy": {"pcm_s16be", "pcm_s24be", "pcm_s32be"}, "audio_only": True},
    ".wma": {"muxer": "asf", "v": None, "a": "wmav2", "acopy": {"wmav2", "wmav1"}, "audio_only": True},
    ".ac3": {"muxer": "ac3", "v": None, "a": "ac3", "acopy": {"ac3"}, "audio_only": True},
    ".caf": {"muxer": "caf", "v": None, "a": "alac", "acopy": {"alac", "aac", "pcm_s16le", "pcm_s24le", "opus"}, "audio_only": True},
}

#: Logical codec → ffmpeg encoders to try, in order.
ENCODERS: dict[str, list[str]] = {
    "h264": ["libx264", "h264_videotoolbox", "h264_mf", "h264_nvenc", "h264_qsv"],
    "hevc": ["libx265", "hevc_videotoolbox", "hevc_mf", "hevc_nvenc"],
    "vp9": ["libvpx-vp9"],
    "vp8": ["libvpx"],
    "av1": ["libsvtav1", "libaom-av1", "librav1e"],
    "mpeg4": ["mpeg4"],
    "mpeg2video": ["mpeg2video"],
    "prores": ["prores_ks", "prores"],
    "theora": ["libtheora"],
    "wmv2": ["wmv2"],
    "mjpeg": ["mjpeg"],
    "gif": ["gif"],
    "aac": ["aac", "aac_at", "libfdk_aac"],
    "mp3": ["libmp3lame"],
    "opus": ["libopus", "opus"],
    "vorbis": ["libvorbis", "vorbis"],
    "flac": ["flac"],
    "alac": ["alac"],
    "wmav2": ["wmav2"],
    "mp2": ["mp2", "mp2fixed"],
    "ac3": ["ac3"],
    "pcm": ["pcm_s16le"],
    "pcm_be": ["pcm_s16be"],
}
VIDEO_CODECS = {"h264", "hevc", "vp9", "vp8", "av1", "mpeg4", "mpeg2video", "prores", "theora", "wmv2", "mjpeg", "gif"}
AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac", "alac", "wmav2", "mp2", "ac3", "pcm", "pcm_be", "pcm_s16le", "pcm_s24le", "pcm_f32le"}
_CODEC_ALIASES = {"x264": "h264", "avc": "h264", "h.264": "h264", "x265": "hevc", "h265": "hevc", "h.265": "hevc", "vp09": "vp9", "libopus": "opus", "lame": "mp3", "libmp3lame": "mp3", "wav": "pcm", "ogg": "vorbis", "wma": "wmav2", "mpeg2": "mpeg2video"}


def container_for(path: Path | str) -> dict[str, Any]:
    ext = Path(path).suffix.lower()
    c = CONTAINERS.get(ext)
    if c is None:
        known = " ".join(sorted(CONTAINERS))
        raise UsageError(f"unsupported output extension '{ext or '(none)'}'; use one of: {known}")
    return c


def norm_codec(name: str) -> str:
    n = name.strip().lower()
    return _CODEC_ALIASES.get(n, n)


def encoder_for(codec: str, pcm_bits: int | None = None, big_endian: bool = False) -> str:
    """The ffmpeg encoder for a logical codec (libx264 for h264, …), or a clear error when the binary lacks it."""
    codec = norm_codec(codec)
    if codec in ("pcm", "pcm_be"):
        bits = pcm_bits if pcm_bits in (16, 24, 32) else 16
        return f"pcm_s{bits}{'be' if (big_endian or codec == 'pcm_be') else 'le'}"
    if codec.startswith("pcm_"):
        return codec
    for enc in ENCODERS.get(codec, [codec]):
        if has_encoder(enc):
            return enc
    raise SkillError(f"this ffmpeg build has no encoder for {codec}")


def can_copy(container: dict[str, Any], kind: str, codec: str | None, default: bool = False) -> bool:
    """Whether a stream of `codec` can go into the container as-is; `default` asks the narrower question of whether it
    should be copied without being asked (it also plays well there: no VP9 or Opus in an MP4 unless requested)."""
    key = {"video": "vcopy", "audio": "acopy", "subtitle": "scopy"}[kind]
    allowed = container.get(key)
    if not allowed or not codec:
        return False
    if not (allowed == "*" or codec in allowed):
        return False
    if default and kind in ("video", "audio"):
        plays = container.get("vplay" if kind == "video" else "aplay")
        if plays is not None:
            return codec in plays
    return True


#: Subtitle codecs Matroska stores as they are; other text codecs are converted to SRT for .mkv.
MKV_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
#: Text subtitle codecs ffmpeg can read and convert (TTML can only be written).
CONVERTIBLE_SUBS = TEXT_SUB_CODECS - {"ttml"}


def sub_target(container: dict[str, Any], s: dict[str, Any]) -> str | None:
    """How a subtitle stream goes into the container: "copy", an encoder (srt, mov_text, webvtt), or None (dropped)."""
    target = container.get("s")
    codec = s.get("codec") or ""
    if not target:
        return None
    if target == "copy":  # Matroska
        if codec in MKV_SUBS:
            return "copy"
        return "srt" if codec in CONVERTIBLE_SUBS else None
    if not s.get("text_based") or codec not in CONVERTIBLE_SUBS:
        return None
    return "copy" if codec == target else target


def brand_cleanup(out: Path | str) -> list[str]:
    """Options that stop MP4 brand fields (major_brand …) from becoming ordinary tags in other containers."""
    if Path(out).suffix.lower() in (".mp4", ".m4v", ".mov", ".m4a", ".m4b", ".3gp"):
        return []
    return ["-metadata", "major_brand=", "-metadata", "minor_version=", "-metadata", "compatible_brands="]


def video_encoder_args(enc: str, crf: float | None = None, bitrate: str | None = None, speed: str = "medium", bit_depth: int = 8, maxrate: str | None = None, bufsize: str | None = None) -> list[str]:
    """Quality and speed options for a video encoder (CRF when no bitrate is given)."""
    a: list[str] = ["-c:v", enc]
    x_presets = {"fastest": "ultrafast", "fast": "veryfast", "medium": "medium", "slow": "slow", "slowest": "veryslow"}
    if enc in ("libx264", "libx265"):
        a += ["-preset", x_presets.get(speed, speed)]
        if bitrate:
            a += ["-b:v", bitrate]
        else:
            a += ["-crf", f"{crf if crf is not None else (23 if enc == 'libx264' else 26):g}"]
        if enc == "libx264":
            a += ["-pix_fmt", "yuv420p"]
        else:
            a += ["-pix_fmt", "yuv420p10le" if bit_depth > 8 else "yuv420p", "-tag:v", "hvc1", "-x265-params", "log-level=error"]
    elif enc == "libvpx-vp9":
        cpu = {"fastest": "8", "fast": "6", "medium": "4", "slow": "2", "slowest": "1"}.get(speed, "4")
        a += ["-deadline", "realtime" if speed == "fastest" else "good", "-cpu-used", cpu, "-row-mt", "1", "-pix_fmt", "yuv420p"]
        if bitrate:
            a += ["-b:v", bitrate]
        else:
            a += ["-crf", f"{crf if crf is not None else 32:g}", "-b:v", "0"]
    elif enc == "libvpx":
        a += ["-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p"]
        a += ["-b:v", bitrate] if bitrate else ["-crf", f"{crf if crf is not None else 10:g}", "-b:v", "2M"]
    elif enc == "libsvtav1":
        pr = {"fastest": "12", "fast": "10", "medium": "8", "slow": "5", "slowest": "3"}.get(speed, "8")
        a += ["-preset", pr, "-pix_fmt", "yuv420p10le" if bit_depth > 8 else "yuv420p"]
        a += ["-b:v", bitrate] if bitrate else ["-crf", f"{crf if crf is not None else 35:g}"]
    elif enc == "libaom-av1":
        a += ["-cpu-used", "6", "-row-mt", "1", "-pix_fmt", "yuv420p"]
        a += ["-b:v", bitrate] if bitrate else ["-crf", f"{crf if crf is not None else 32:g}", "-b:v", "0"]
    elif enc.endswith("_videotoolbox") or enc.endswith("_mf") or enc.endswith("_nvenc") or enc.endswith("_qsv"):
        a += ["-b:v", bitrate or "5M", "-pix_fmt", "yuv420p"]
        if enc.startswith("hevc"):
            a += ["-tag:v", "hvc1"]
    elif enc in ("prores_ks", "prores"):
        a += ["-profile:v", "2", "-pix_fmt", "yuv422p10le"]
    elif enc == "gif":
        pass
    elif enc in ("mpeg4", "mpeg2video", "wmv2", "mjpeg", "libtheora"):
        if bitrate:
            a += ["-b:v", bitrate]
        else:
            q = crf if crf is not None and crf <= 31 else 3
            a += ["-q:v", f"{q:g}"]
        if enc in ("mpeg4", "mpeg2video", "wmv2", "libtheora"):
            a += ["-pix_fmt", "yuv420p"]
    if maxrate:
        a += ["-maxrate", maxrate, "-bufsize", bufsize or maxrate]
    return a


def audio_encoder_args(enc: str, bitrate: str | None = None, quality: float | None = None) -> list[str]:
    a = ["-c:a", enc]
    if enc == "libmp3lame":
        a += ["-b:a", bitrate] if bitrate else ["-q:a", f"{quality if quality is not None else 2:g}"]
    elif enc in ("aac", "aac_at", "libfdk_aac"):
        a += ["-b:a", bitrate or "160k"]
    elif enc in ("libopus", "opus"):
        a += ["-b:a", bitrate or "96k"]
        if enc == "opus":
            a += ["-strict", "-2"]
    elif enc in ("libvorbis", "vorbis"):
        a += ["-b:a", bitrate] if bitrate else ["-q:a", f"{quality if quality is not None else 5:g}"]
        if enc == "vorbis":
            a += ["-strict", "-2"]
    elif enc == "flac":
        a += ["-compression_level", "5"]
    elif enc in ("wmav2", "mp2", "ac3"):
        a += ["-b:a", bitrate or ("192k" if enc != "ac3" else "384k")]
    return a


def parse_bitrate(value: str | None) -> str | None:
    """'128k', '2.5M', '128000' → an ffmpeg bitrate string."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("bps", "").replace("b/s", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", s)
    if not m:
        raise UsageError(f"bad bitrate '{value}' (use 128k, 2.5M or 800000)")
    num, unit = float(m.group(1)), m.group(2)
    bps = num * {"": 1, "k": 1000, "m": 1_000_000}[unit]
    if bps < 1000:
        bps *= 1000  # '128' means 128k
    return f"{int(bps)}"


def bitrate_value(value: str | None) -> int:
    return int(parse_bitrate(value) or 0)


# Default stereo bitrates (kb/s) of the constant-bitrate encoders, and their ceilings per file.
_CBR_STEREO = {"aac": 160, "aac_at": 160, "libfdk_aac": 160, "libopus": 96, "opus": 96, "wmav2": 192, "mp2": 192, "ac3": 192}
_CBR_MAX = {"libopus": 510, "opus": 510, "wmav2": 320, "mp2": 384, "ac3": 640}
_LOSSLESS = ("pcm_", "flac", "alac", "wavpack", "tta", "ape", "truehd", "mlp")


def auto_audio_bitrate(enc: str, stereo: str | None, channels: int | None, src: dict[str, Any] | None = None) -> str | None:
    """Bitrate for a constant-bitrate encoder when the user gave none: `stereo` (a preset's value, or the encoder's
    default) scaled to the output channel count, and never much above a lossy source's bitrate (re-encoding a
    96 kb/s voice memo at 192 kb/s only makes it bigger). Other encoders (VBR MP3, Vorbis, lossless) get `stereo`."""
    if enc in ("libmp3lame", "libvorbis") and stereo is None:
        # VBR by default (about 190 kb/s stereo); for a low-bitrate lossy source, a matching bitrate instead, so that
        # re-encoding a 48 kb/s podcast does not make it 50% bigger.
        codec = str((src or {}).get("codec") or "")
        sbr = (src or {}).get("bit_rate")
        if sbr and codec and not codec.startswith(_LOSSLESS) and int(sbr) < 150_000:
            k = max(int(sbr) / 1000 * 1.2, 32 * min(max(1, int(channels or 2)), 2))
            return f"{max(32, int(round(k / 8)) * 8) * 1000}"
        return None
    if enc not in _CBR_STEREO:
        return stereo
    k = bitrate_value(stereo) / 1000 if stereo else _CBR_STEREO[enc]
    ch = max(1, int(channels or 2))
    if ch == 1:
        k = max(k * 0.6, 48)
    elif ch > 2:
        k = k * ch / 2 * 0.75
    codec = str((src or {}).get("codec") or "")
    sbr = (src or {}).get("bit_rate")
    if sbr and codec and not codec.startswith(_LOSSLESS):
        k = min(k, max(int(sbr) / 1000 * 1.25, 32 * min(ch, 2)))
    k = min(k, _CBR_MAX.get(enc, 640))
    return f"{max(16, int(round(k / 8)) * 8) * 1000}"


# ── scaling specs ───────────────────────────────────────────────────────

_NAMED_HEIGHTS = {"2160p": 2160, "4k": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "540p": 540, "480p": 480, "360p": 360, "240p": 240, "144p": 144}


def scale_filter(spec: str, src_w: int | None = None, src_h: int | None = None) -> str:
    """An ffmpeg scale filter for '1280x720', '1280x' / 'x720' / '1280' (keep aspect), '720p', '50%', or 'fit:1280x720'."""
    s = spec.strip().lower()
    if s in _NAMED_HEIGHTS:
        h = _NAMED_HEIGHTS[s]
        # For portrait video the named size limits the short side.
        if src_w and src_h and src_h > src_w:
            return f"scale={h}:-2:flags=lanczos"
        return f"scale=-2:{h}:flags=lanczos"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", s)
    if m:
        f = float(m.group(1)) / 100
        return f"scale=trunc(iw*{f:g}/2)*2:trunc(ih*{f:g}/2)*2:flags=lanczos"
    m = re.fullmatch(r"(?:(fit|fill):)?(\d+)?\s*[x:×]\s*(\d+)?", s)
    if m and (m.group(2) or m.group(3)):
        mode, w, h = m.group(1), m.group(2), m.group(3)
        if mode == "fit" and w and h:
            return f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2"
        if mode == "fill" and w and h:
            return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
        return f"scale={w or -2}:{h or -2}:flags=lanczos"
    if re.fullmatch(r"\d+", s):
        return f"scale={s}:-2:flags=lanczos"
    raise UsageError(f"bad size '{spec}' (use 1280x720, 1280x, x720, 720p, 50%, fit:1280x720 or fill:1080x1920)")


def cap_filter(max_edge: int | None, max_fps: float | None, v: dict[str, Any], max_short: int | None = None) -> list[str]:
    """Filters that bring a video within a long-edge (and short-edge) and frame-rate cap (never upscales)."""
    out = []
    w, h = display_size(v)
    if w and h:
        r = 1.0
        if max_edge:
            r = min(r, max_edge / max(w, h))
        if max_short:
            r = min(r, max_short / min(w, h))
        if r < 0.999:
            out.append(f"scale={max(2, int(round(w * r / 2)) * 2)}:{max(2, int(round(h * r / 2)) * 2)}:flags=lanczos,setsar=1")
    fps = v.get("fps_max") or v.get("fps")
    if max_fps and fps and fps > max_fps + 0.01:
        out.append(f"fps={max_fps:g}")
    return out


#: Lowest bits per pixel per frame that still looks decent, per codec (H.264 needs about twice what AV1 does).
MIN_BPP = {"h264": 0.045, "hevc": 0.03, "vp9": 0.03, "av1": 0.024, "vp8": 0.05, "mpeg4": 0.07, "mpeg2video": 0.09, "wmv2": 0.08, "theora": 0.07}
_LADDER = (2160, 1440, 1080, 900, 720, 540, 480, 360, 270, 240)


def fit_to_bitrate(v: dict[str, Any], bps: float, codec: str, fixed_size: bool = False, fixed_fps: float | None = None) -> tuple[int, int, float, float]:
    """The largest picture and frame rate a video bitrate can carry at a decent quality: (width, height, fps, bits per
    pixel). Frame rate goes down first (to 30, then 24), then the size down a ladder of heights (1080, 720 … 240)."""
    w, h = display_size(v)
    w, h = max(2, w), max(2, h)
    fps = float(fixed_fps or v.get("fps_max") or v.get("fps") or 30.0)
    need = MIN_BPP.get(codec, 0.05)

    def bpp(W: int, H: int, F: float) -> float:
        return bps / max(1.0, W * H * F)

    if not fixed_fps and fps > 30.5 and bpp(w, h, fps) < need:
        fps = 30.0 if fps not in (50.0, 100.0) and abs(fps - 50) > 0.5 else 25.0
    W, H = w, h
    if not fixed_size and bpp(W, H, fps) < need:
        short = min(w, h)
        for s in _LADDER:
            if s >= short:
                continue
            r = s / short
            W, H = max(2, int(round(w * r / 2)) * 2), max(2, int(round(h * r / 2)) * 2)
            if bpp(W, H, fps) >= need:
                break
    if not fixed_fps and bpp(W, H, fps) < need and fps > 24.5:
        fps = 24.0
    return W, H, fps, bpp(W, H, fps)


def fps_rational(f: float) -> str:
    """A frame rate for ffmpeg, exact for the NTSC rates probes round (29.97 → 30000/1001)."""
    k = f * 1.001
    if abs(k - round(k)) < 0.01 and abs(f - round(f)) > 0.01:
        return f"{int(round(k)) * 1000}/1001"
    return f"{f:g}"


def even_filter() -> str:
    return "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def is_hdr(v: dict[str, Any] | None) -> bool:
    return bool(v and (v.get("hdr") or (v.get("color", {}).get("transfer") or "").startswith(("smpte2084", "arib"))))


def tonemap_filter() -> str | None:
    """HDR (PQ/HLG) → SDR BT.709 with zscale + tonemap, when this ffmpeg has them."""
    if has_filter("zscale") and has_filter("tonemap"):
        return "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    return None


# ── outputs ─────────────────────────────────────────────────────────────


def temp_output(out: Path) -> Path:
    """A temp path next to `out` with the same extension (ffmpeg picks the format from it)."""
    fd, name = tempfile.mkstemp(prefix=f".{out.stem}.", suffix=out.suffix, dir=str(out.parent))
    os.close(fd)
    return Path(name)


def finalize(tmp: Path, out: Path) -> Path:
    if not tmp.exists() or tmp.stat().st_size == 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SkillError(f"ffmpeg wrote nothing for {out.name}")
    try:  # mkstemp files are private (0600); give the output normal permissions
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(tmp, 0o666 & ~mask)
    except OSError:
        pass
    os.replace(tmp, out)
    return out


class TempOut:
    """Context manager: yields a temp path beside the output; renames it into place on success, deletes it on failure."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.tmp = temp_output(out)

    def __enter__(self) -> Path:
        return self.tmp

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            finalize(self.tmp, self.out)
        else:
            try:
                self.tmp.unlink()
            except OSError:
                pass


def join_negative_values(argv: list[str], names: Iterable[str]) -> list[str]:
    """Lets `--gain -6dB` or `--by -0:01.5` through argparse, which would take the value for an option."""
    names = set(names)
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in names and i + 1 < len(argv) and argv[i + 1].startswith("-") and not argv[i + 1].startswith("--") and len(argv[i + 1]) > 1:
            out.append(f"{a}={argv[i + 1]}")
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def summarize_output(path: Path) -> dict[str, Any]:
    """Short facts about a produced file (for the report)."""
    try:
        info = probe(path, side_data=True, cache=False)  # outputs are often intermediate: keep them out of the cache
    except SkillError as e:
        return {"file": str(path), "size": path.stat().st_size if path.exists() else 0, "error": str(e)}
    v = main_video(info)
    a = main_audio(info)
    out: dict[str, Any] = {"file": str(path), "size": info["size"], "duration": info.get("duration"), "container": info["container"]}
    if v:
        w, h = display_size(v)
        out["video"] = f"{v.get('codec')} {w}x{h}" + (f" {v['fps']:g}fps" if v.get("fps") else "")
    if a:
        out["audio"] = f"{a.get('codec')} {a.get('sample_rate')}Hz {a.get('channels')}ch"
    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    if subs:
        out["subtitles"] = [f"{s.get('codec')}" + (f" ({s['language']})" if s.get("language") else "") for s in subs]
    return out


def describe_output(o: dict[str, Any]) -> str:
    bits = [f"{o['file']}", fmt_size(o.get("size", 0))]
    if o.get("duration"):
        bits.append(fmt_dur(o["duration"]))
    if o.get("video"):
        bits.append(o["video"])
    if o.get("audio"):
        bits.append(o["audio"])
    if o.get("subtitles"):
        bits.append("subs: " + ", ".join(o["subtitles"]))
    return " · ".join(bits)


def expand_inputs(items: Iterable[str], exts: set[str] | None = MEDIA_EXTS, recursive: bool = False) -> list[Path]:
    """Files from paths, folders (media files inside) and glob patterns (expanded here, since Windows shells do not)."""
    import glob

    from _common import input_file

    out: list[Path] = []
    for it in items:
        p = Path(it).expanduser()
        if p.is_dir():
            it_files = p.rglob("*") if recursive else p.glob("*")
            out.extend(sorted(f for f in it_files if f.is_file() and not f.name.startswith(".") and (exts is None or f.suffix.lower() in exts)))
        elif not p.exists() and any(ch in it for ch in "*?["):
            matches = sorted(Path(m) for m in glob.glob(str(p), recursive=True) if Path(m).is_file())
            if not matches:
                raise SkillError(f"no files match {it}")
            out.extend(matches)
        else:
            out.append(input_file(p))
    seen: set[str] = set()
    uniq = []
    for f in out:
        k = str(f.resolve())
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    if not uniq:
        raise SkillError("no media files found")
    return uniq


# ── cached products (renders, analyses) ─────────────────────────────────


def cached_product(src: Path | str, kind: str, params: dict[str, Any], version: str, build: Any) -> tuple[Path, dict[str, Any], bool]:
    """Runs `build(folder) -> meta` at most once per (file content, kind, params, version): (folder, meta, was cached).

    `build` writes its files (PNGs …) into the folder and returns JSON-ready facts about them. The caller copies what it
    needs out of the folder, then calls release_product(folder) (a no-op for cache entries, deletes transient ones).
    """
    import _cache

    hit = _cache.lookup(src, kind, params, version)
    if hit is not None:
        try:
            return hit, json.loads((hit / "meta.json").read_text(encoding="utf-8")), True
        except (OSError, ValueError):
            pass
    box: dict[str, Any] = {}

    def run(tmp: Path) -> None:
        box["meta"] = build(tmp)
        (tmp / "meta.json").write_text(json.dumps(box["meta"], ensure_ascii=False, default=str), encoding="utf-8")

    try:
        d = _cache.cached_dir(src, kind, params, version, run)
    except OSError:
        if "meta" in box:  # built, but the cache could not keep it
            raise
        d = Path(tempfile.mkdtemp(prefix="desk-av-"))
        run(d)
    meta = box.get("meta")
    if meta is None:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return d, meta, False


def release_product(d: Path) -> None:
    import _cache

    _cache.release(d)


def copy_out(src: Path, dest: Path) -> Path:
    """Copies a cached file to its output path (atomically: a temp file beside it, then a rename)."""
    import shutil

    tmp = temp_output(dest)
    try:
        shutil.copyfile(src, tmp)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return finalize(tmp, dest)
