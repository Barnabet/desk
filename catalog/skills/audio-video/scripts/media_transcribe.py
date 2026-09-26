#!/usr/bin/env python3
"""Transcribe or translate speech in audio and video files with faster-whisper (offline once the model is downloaded):
text, SRT, WebVTT, JSON with word timings, TSV or Markdown. Long files are processed in chunks with bounded memory."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, add_format, emit, input_file, md_table, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/media_transcribe.py interview.mp3                          # transcript on stdout (model 'base')
  python3 scripts/media_transcribe.py talk.mp4 --out talk.srt --out talk.txt # subtitles and plain text
  python3 scripts/media_transcribe.py talk.mp4 --out talk.json --words       # segments with word timings
  python3 scripts/media_transcribe.py lecture.m4a --model small --language fr --out lecture.vtt
  python3 scripts/media_transcribe.py clip.mp4 --task translate --out clip.en.srt   # any language → English
  python3 scripts/media_transcribe.py meeting.wav --start 10:00 --end 25:00 --prompt "Desk, Kubernetes, Anaïs"
  python3 scripts/media_transcribe.py talk.mp4 --find "budget"              # where it is said: timestamps with context
  python3 scripts/media_transcribe.py talk.mp4 --offset 120                 # the transcript from segment 120 on
  python3 scripts/media_transcribe.py --list-models

Transcripts are cached per file content and options: asking again (another page, --find, another --out format) is
instant and needs no model.

The first use of a model downloads it into the Hugging Face cache (tiny 75 MB, base 145 MB, small 484 MB,
medium 1.5 GB, large-v3 3.1 GB, turbo 1.6 GB); later runs work offline. --model-dir uses a local CTranslate2
Whisper model folder instead (no download).
"""

MODEL_MB = {"tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145, "small": 484, "small.en": 484, "medium": 1530, "medium.en": 1530,
            "large-v1": 3090, "large-v2": 3090, "large-v3": 3090, "large": 3090, "distil-large-v2": 1510, "distil-large-v3": 1510,
            "distil-large-v3.5": 1510, "distil-medium.en": 789, "distil-small.en": 336, "large-v3-turbo": 1620, "turbo": 1620}
ALLOW = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


def main() -> int:
    p = parser("Transcribe (or translate to English) the speech in an audio or video file with faster-whisper: "
               "language detection, voice-activity filtering, optional word timestamps; writes txt, srt, vtt, json, tsv or md.", EPILOG)
    p.add_argument("input", nargs="?", help="audio or video file")
    p.add_argument("--out", action="append", default=[], help="output file; the extension picks the format (.txt .srt .vtt .json .tsv .md); repeatable")
    p.add_argument("--model", default="base", help="tiny, base (default), small, medium, large-v3, turbo, distil-large-v3, *.en variants, or a Hugging Face repo id")
    p.add_argument("--model-dir", help="a local CTranslate2 Whisper model folder (offline, no download)")
    p.add_argument("--language", default="auto", help="spoken language code (en, fr, de, es, ja …) or auto (default)")
    p.add_argument("--task", choices=["transcribe", "translate"], default="transcribe", help="translate = English text from any language")
    p.add_argument("--words", action="store_true", help="word-level timestamps (better subtitle timing; a little slower)")
    p.add_argument("--no-vad", action="store_true", help="do not skip silence with the voice-activity filter")
    p.add_argument("--beam-size", type=int, default=5, help="beam search width (default 5; 1 is fastest)")
    p.add_argument("--prompt", help="initial prompt: names, jargon or style to expect")
    p.add_argument("--hotwords", help="words to favour (comma-separated)")
    p.add_argument("--start", help="transcribe from this time")
    p.add_argument("--end", help="transcribe up to this time")
    p.add_argument("--track", type=int, help="audio track, 1-based")
    p.add_argument("--line-chars", "--max-line-chars", dest="line_chars", type=int, default=42, help="subtitle line length for .srt/.vtt (default 42)")
    p.add_argument("--max-lines", type=int, default=2, help="lines per subtitle (default 2)")
    p.add_argument("--find", "--grep", dest="find", help="print only the segments that say this (a phrase; accents, case and punctuation ignored), with their times")
    p.add_argument("--regex", action="store_true", help="--find takes a Python regular expression")
    p.add_argument("--context", type=int, default=1, help="segments shown around each --find hit (default 1)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"printed transcript budget (default {DEFAULT_MAX_CHARS}); a longer one ends with the command for the next part")
    p.add_argument("--offset", type=int, default=0, help="print from this segment on (the next-part command sets it)")
    p.add_argument("--no-cache", action="store_true", help="transcribe again instead of reusing a cached transcript")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto", help="cuda needs an NVIDIA GPU with CUDA libraries")
    p.add_argument("--compute-type", default=None, help="int8 (CPU default), float16 (GPU default), int8_float16, float32")
    p.add_argument("--threads", type=int, help="CPU threads (default: all but one, up to 8)")
    p.add_argument("--chunk-minutes", type=float, default=30.0, help="process long audio in pieces of about this length (default 30)")
    p.add_argument("--offline", action="store_true", help="never download; fail if the model is not cached")
    p.add_argument("--list-models", action="store_true", help="list models, sizes and which are downloaded")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    add_format(p)
    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], ("--start", "--end")))
    if a.no_cache:
        os.environ["DESK_NO_CACHE"] = "1"
    if a.offset < 0 or a.context < 0:
        raise UsageError("--offset and --context are 0 or more")
    if a.max_chars is not None and a.max_chars < 2000:
        raise UsageError("--max-chars is at least 2000")
    if a.list_models:
        return list_models(a)
    if not a.input:
        raise UsageError("give an input file (or --list-models)")
    return transcribe(a)


# ── models ──────────────────────────────────────────────────────────────


def _repo(name: str) -> str:
    from faster_whisper.utils import _MODELS

    if "/" in name:
        return name
    repo = _MODELS.get(name)
    if repo is None:
        raise UsageError(f"unknown model '{name}'; choose from: {', '.join(_MODELS)}")
    return repo


def _cached(repo: str) -> str | None:
    """A downloaded copy: in this runtime's cache, or in the user's usual Hugging Face cache (read only)."""
    import huggingface_hub

    dirs: list[str | None] = [None]
    home = Path.home() / ".cache" / "huggingface" / "hub"
    if home.is_dir() and home.resolve() != Path(cache_dir()).resolve():
        dirs.append(str(home))
    for d in dirs:
        try:
            return huggingface_hub.snapshot_download(repo, local_files_only=True, allow_patterns=ALLOW, cache_dir=d)
        except Exception:  # noqa: BLE001 — not in this cache
            continue
    return None


def cache_dir() -> str:
    try:
        from huggingface_hub import constants

        return str(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        return "the Hugging Face cache"


def list_models(a: Any) -> int:
    from faster_whisper.utils import _MODELS

    rows = []
    data = []
    for name, repo in _MODELS.items():
        path = _cached(repo)
        rows.append([name, f"{MODEL_MB.get(name, 0):,} MB" if name in MODEL_MB else "?", "yes" if path else "", repo])
        data.append({"model": name, "repo": repo, "size_mb": MODEL_MB.get(name), "downloaded": bool(path), "path": path})
    if a.format == "json":
        emit({"cache": cache_dir(), "models": data}, "json")
    else:
        print(f"Models download to {cache_dir()} on first use.\n")
        print(md_table(["model", "size", "downloaded", "repository"], rows))
        print("\ntiny/base are fast and fine for clear speech; small/medium/turbo are more accurate; .en models are English-only.")
    return 0


def resolve_model(a: Any) -> tuple[str, str]:
    """(path to a local model folder, label); downloads on first use with progress on stderr."""
    if a.model_dir:
        d = Path(a.model_dir).expanduser()
        if not (d / "model.bin").is_file():
            raise SkillError(f"{d} is not a CTranslate2 Whisper model folder (no model.bin); convert one with ctranslate2's converter or download a faster-whisper model")
        return str(d), d.name
    repo = _repo(a.model)
    path = _cached(repo)
    if path:
        return path, a.model
    if a.offline or os.environ.get("HF_HUB_OFFLINE") in ("1", "true", "True"):
        raise SkillError(f"model '{a.model}' is not downloaded and downloads are off. Run once with network access "
                         f"(it goes to {cache_dir()}), or pass --model-dir with a local model folder")
    return download(repo, a.model), a.model


def download(repo: str, name: str) -> str:
    import huggingface_hub

    size = MODEL_MB.get(name)
    print(f"downloading Whisper model '{name}'" + (f" (~{size} MB)" if size else "") + f" to {cache_dir()} (first use only)…", file=sys.stderr, flush=True)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["path"] = huggingface_hub.snapshot_download(repo, allow_patterns=ALLOW)
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    folder = Path(cache_dir()) / ("models--" + repo.replace("/", "--"))
    last = 0.0
    t0 = time.monotonic()
    while th.is_alive():
        th.join(timeout=2.0)
        if time.monotonic() - last >= 10 and th.is_alive():
            got = _folder_mb(folder)
            print(f"  {got:,.0f}" + (f" / {size:,} MB" if size else " MB") + f" after {time.monotonic() - t0:.0f}s", file=sys.stderr, flush=True)
            last = time.monotonic()
    if "error" in result:
        e = result["error"]
        raise SkillError(f"could not download model '{name}' ({type(e).__name__}: {str(e)[:200]}). Check the network, or pass --model-dir with a local model folder") from None
    print(f"  done in {time.monotonic() - t0:.0f}s", file=sys.stderr, flush=True)
    return str(result["path"])


def _folder_mb(folder: Path) -> float:
    total = 0
    try:
        for f in folder.rglob("*"):
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
    except OSError:
        pass
    xet = Path(cache_dir()).parent / "xet"
    return total / 1e6 if total else _dir_mb(xet)


def _dir_mb(d: Path) -> float:
    total = 0
    try:
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total / 1e6


# ── transcription ───────────────────────────────────────────────────────


def _chunks(path: str, start: float, end: float | None, track: int | None, chunk_s: float) -> Any:
    """Yields (offset seconds, 16 kHz mono float32 array); pieces are cut at the quietest moment near each boundary."""
    import numpy as np

    from _media import read_audio

    sr = 16000
    buf: list[Any] = []
    have = 0
    offset = start
    limit = int(chunk_s * sr)
    look = min(int(30 * sr), limit // 2)
    for b in read_audio(path, sr=sr, channels=1, start=start or None, end=end, stream=track):
        buf.append(b[:, 0])
        have += b.shape[0]
        if have >= limit + look:
            audio = np.concatenate(buf)
            cut = _quiet_point(audio, limit - look, limit + look, sr)
            yield offset, audio[:cut]
            rest = audio[cut:]
            buf, have = [rest], rest.shape[0]
            offset += cut / sr
    if have:
        yield offset, np.concatenate(buf)


def _quiet_point(x: Any, lo: int, hi: int, sr: int) -> int:
    import numpy as np

    lo, hi = max(0, lo), min(len(x), hi)
    win = sr // 2
    seg = x[lo:hi]
    if len(seg) < win * 2:
        return hi
    n = len(seg) // win
    e = (seg[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1)
    return lo + int(np.argmin(e)) * win + win // 2


def _cache_params(a: Any, s: float, e: float | None, pos: int) -> dict[str, Any]:
    """Everything that changes the transcript: the model, language, task and decoding options, and the range."""
    if a.model_dir:
        d = Path(a.model_dir).expanduser().resolve()
        try:
            st = (d / "model.bin").stat()
            model = f"dir:{d}:{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            model = f"dir:{d}"
    else:
        model = f"hf:{a.model}"
    return {"model": model, "language": (a.language or "auto").lower(), "task": a.task, "words": bool(a.words), "vad": not a.no_vad,
            "beam": a.beam_size, "prompt": a.prompt or "", "hotwords": a.hotwords or "", "start": round(s, 3), "end": None if e is None else round(e, 3),
            "track": pos, "chunk": a.chunk_minutes, "device": a.device, "compute": a.compute_type or ""}


def transcribe(a: Any) -> int:
    from _media import audio_stream_pos, fmt_time, parse_time, probe

    t0 = time.monotonic()
    src = input_file(a.input)
    info = probe(src, side_data=False)
    pos = audio_stream_pos(info, a.track)
    d = info.get("duration")
    s = parse_time(a.start, d) if a.start else 0.0
    e = parse_time(a.end, d) if a.end else None
    if e is not None and e <= s:
        raise UsageError("--end must be after --start")
    span = ((e if e is not None else d) or 0.0) - s
    outs = []
    for o in a.out:
        op = output_path(o, [src], a.force)
        fmt = op.suffix.lower().lstrip(".")
        if fmt not in ("txt", "srt", "vtt", "json", "tsv", "md", "ass"):
            raise UsageError(f"--out {o}: use .txt, .srt, .vtt, .json, .tsv, .md or .ass")
        outs.append((op, fmt))
    if not 1 <= a.beam_size <= 20:
        raise UsageError("--beam-size must be 1-20")
    if a.model.endswith(".en") and a.task == "translate":
        raise UsageError("English-only (.en) models cannot translate; use a multilingual model")

    import _cache

    params = _cache_params(a, s, e, pos)
    cached = True
    hit = _cache.lookup(src, "av-transcript", params, TRANSCRIPT_VERSION)
    result: dict[str, Any] | None = None
    if hit is not None:
        try:
            result = json.loads((hit / "value.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
    if result is None:
        cached = False
        if a.find:
            print(f"no cached transcript of {src.name} with these options yet: transcribing it first ({fmt_time(span, ms=False)} of audio)…", file=sys.stderr, flush=True)
        try:
            result = _cache.cached_json(src, "av-transcript", params, TRANSCRIPT_VERSION, lambda: run_model(a, src, s, e, pos, span))
        except OSError:
            result = run_model(a, src, s, e, pos, span)
    from _subs import Cue

    cues = [Cue(float(c["start"]), float(c["end"]), c["text"], words=c.get("words")) for c in result["segments"]]
    detected, prob = result.get("language"), result.get("language_probability")
    write_outputs(a, outs, cues, result, span)
    elapsed = time.monotonic() - t0
    n_words = sum(len(c.words or []) for c in cues)
    summary = {"file": str(src), "model": result.get("model"), "device": result.get("device"), "compute_type": result.get("compute_type"), "language": detected,
               "language_probability": prob, "task": a.task, "duration": round(span, 3), "segments": len(cues), "words": n_words,
               "seconds": round(elapsed, 2), "cached": cached, "transcribe_seconds": result.get("seconds"),
               "speed": result.get("speed"), "outputs": [str(o) for o, _ in outs]}
    if a.format == "json":
        rows = list(range(len(cues)))
        if a.find:
            rows = _hits(a, cues)
        page, more = _fit_json(cues, rows, a)
        items = [_item(cues, i, a) for i in page]
        emit({**summary, "query": a.find, "hits" if a.find else "cues": items, "total": len(rows), "offset": a.offset,
              **({"next": _next_cmd(a.offset + len(page))} if more else {})}, "json", max_chars=None)
        return 0
    how = ("from the cache (no model needed)" if cached else f"in {result.get('seconds', 0):.1f}s ({result.get('speed')}x real time)")
    head = (f"{src.name}: {fmt_time(span, ms=False)} of audio, language {detected} ({(prob or 0) * 100:.0f}%), model {result.get('model')}, "
            f"{len(cues)} segments {how}")
    print(head)
    for o, _ in outs:
        print(f"wrote {o}")
    if not cues:
        print("\nNo speech was recognised (silence, music, or the wrong --language).")
        return 0
    print()
    if a.find:
        hits = _hits(a, cues)
        blocks = []
        for i in hits[a.offset:]:
            lines = [f"    [{_st(cues[j].start)}] {cues[j].text}" for j in range(max(0, i - a.context), i)]
            lines.append(f"[{_st(cues[i].start)}–{_st(cues[i].end)}] » {cues[i].text}")
            lines += [f"    [{_st(cues[j].start)}] {cues[j].text}" for j in range(i + 1, min(len(cues), i + 1 + a.context))]
            blocks.append("\n".join(lines))
        print(f"{len(hits)} hit(s) for {a.find!r}" + (":" if hits else "."))
        _print_page(blocks, a.offset, len(hits), a.max_chars, "hits")
        if hits:
            print("Times are addresses: media_frames.py frames --at TIME to look, media_edit.py trim --start/--end to cut.")
        return 0
    rows = [f"[{_st(c.start)}] {c.text}" for c in cues[a.offset:]]
    _print_page(rows, a.offset, len(cues), a.max_chars, "segments")
    return 0


TRANSCRIPT_VERSION = "1"


def run_model(a: Any, src: Path, s: float, e: float | None, pos: int, span: float) -> dict[str, Any]:
    """Transcribes with faster-whisper (the model is found or downloaded first); a JSON-ready result."""
    from _media import Progress

    t0 = time.monotonic()
    model_path, label = resolve_model(a)
    from _audio import import_onnxruntime_quietly

    import_onnxruntime_quietly()
    from faster_whisper import WhisperModel

    device = a.device
    if device == "auto":
        device = "cuda" if _has_cuda() else "cpu"
    ctype = a.compute_type or ("float16" if device == "cuda" else "int8")
    threads = a.threads or max(1, min((os.cpu_count() or 2) - 1, 8))
    try:
        model = WhisperModel(model_path, device=device, compute_type=ctype, cpu_threads=threads)
    except Exception as ex:  # noqa: BLE001
        raise SkillError(f"could not load the model ({type(ex).__name__}: {str(ex)[:300]})") from None

    lang = None if a.language in (None, "", "auto") else a.language.lower()
    if lang:
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        if lang not in _LANGUAGE_CODES:
            raise UsageError(f"unknown language '{a.language}'; use a code like en, fr, de, es, it, pt, nl, ja, zh, ko, ru, ar, hi")
    prog = Progress(span, "transcribing")
    segments_out: list[dict[str, Any]] = []
    detected = None
    prob = None
    prompt = a.prompt
    for offset, audio in _chunks(str(src), s, e, pos, max(0.05, a.chunk_minutes) * 60):
        if audio.size < 1600:
            continue
        segments, tinfo = model.transcribe(
            audio, language=lang or detected, task=a.task, beam_size=a.beam_size, vad_filter=not a.no_vad,
            vad_parameters={"min_silence_duration_ms": 500}, word_timestamps=a.words, initial_prompt=prompt,
            hotwords=a.hotwords, condition_on_previous_text=True,
        )
        if detected is None:
            detected, prob = tinfo.language, tinfo.language_probability
        last_text = ""
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            item: dict[str, Any] = {"start": round(offset + seg.start, 3), "end": round(offset + seg.end, 3), "text": text}
            if seg.words:
                item["words"] = [{"start": round(offset + w.start, 3), "end": round(offset + w.end, 3), "word": w.word, "probability": round(w.probability, 3)} for w in seg.words]
            segments_out.append(item)
            last_text = text
            prog.update(offset + seg.end - s)
        # Carry context into the next chunk, like Whisper does between windows.
        prompt = ((a.prompt + " ") if a.prompt else "") + last_text[-200:] if last_text else a.prompt
    elapsed = time.monotonic() - t0
    return {"language": detected, "language_probability": round(prob or 0, 3), "model": label, "device": device, "compute_type": ctype,
            "task": a.task, "duration": round(span, 3), "seconds": round(elapsed, 2), "speed": round(span / elapsed, 1) if elapsed else None,
            "segments": segments_out}


def write_outputs(a: Any, outs: list[tuple[Path, str]], cues: list[Any], result: dict[str, Any], span: float) -> None:
    from _subs import from_words, reflow, save

    detected = result.get("language")
    for op, fmt in outs:
        if fmt in ("srt", "vtt", "ass"):
            sub = [c for cue in cues for c in (from_words(cue.words, a.line_chars, a.max_lines) if cue.words else reflow([cue], a.line_chars, a.max_lines))]
            save(sub, op, fmt, language=detected)
        elif fmt == "json":
            data = {"language": detected, "language_probability": result.get("language_probability"), "duration": round(span, 3), "model": result.get("model"),
                    "task": a.task, "segments": [{"start": c.start, "end": c.end, "text": c.text, **({"words": c.words} if c.words else {})} for c in cues]}
            op.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            save(cues, op, fmt)


def _st(t: float) -> str:
    from _media import fmt_stamp

    return fmt_stamp(t)


def _hits(a: Any, cues: list[Any]) -> list[int]:
    from _subs import find_hits

    return find_hits([c.text for c in cues], a.find, regex=a.regex)


def _item(cues: list[Any], i: int, a: Any) -> dict[str, Any]:
    c = cues[i]
    d: dict[str, Any] = {"n": i + 1, "start": c.start, "end": c.end, "text": c.text}
    if a.find:
        d["before"] = [cues[j].text for j in range(max(0, i - a.context), i)]
        d["after"] = [cues[j].text for j in range(i + 1, min(len(cues), i + 1 + a.context))]
    return d


def _fit_json(cues: list[Any], rows: list[int], a: Any) -> tuple[list[int], bool]:
    """The rows from --offset that fit in --max-chars as JSON items; and whether more remain."""
    budget = (a.max_chars or 10**12) - 800
    used = 0
    page: list[int] = []
    for i in rows[a.offset:]:
        n = len(json.dumps(_item(cues, i, a), ensure_ascii=False)) + 8
        if page and used + n > budget:
            break
        page.append(i)
        used += n
    return page, a.offset + len(page) < len(rows)


def _next_cmd(offset: int) -> str:
    """This command line for the next part: same input and options, no outputs to write again, --offset moved."""
    import shlex

    argv = sys.argv[1:]
    out: list[str] = []
    skip = False
    for x in argv:
        if skip:
            skip = False
            continue
        if x in ("--out", "--offset"):
            skip = True
            continue
        if x.startswith(("--out=", "--offset=")) or x == "--force":
            continue
        out.append(x)
    return shlex.join(["python3", "scripts/media_transcribe.py", *out, "--offset", str(offset)])


def _print_page(rows: list[str], offset: int, total: int, max_chars: int | None, what: str) -> None:
    from _paging import fit_rows

    if not rows:
        print(f"[{what}: --offset {offset} is past the end ({total})]" if total else f"[no {what}]")
        return
    n = fit_rows("", rows, (max_chars or 10**12) - 400)
    print("\n".join(rows[:n]))
    end = offset + n
    if end < total:
        print(f"\n[{what} {offset + 1}-{end} of {total}; {total - end} more. Next part: {_next_cmd(end)}]")
    elif offset and n:
        print(f"\n[{what} {offset + 1}-{end} of {total}: the last part]")


def _has_cuda() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    run_main(main)
