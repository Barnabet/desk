#!/usr/bin/env python3
"""Subtitles: convert between SRT, WebVTT, ASS/SSA, SBV, LRC, JSON, TSV and text; shift and re-time; merge and split;
clean; check timing and readability; extract a video's subtitle tracks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, add_format, cap, emit, input_file, md_table, output_dir, output_path, parser, run_main  # noqa: E402
from _paging import add_paging, check_paging, emit_json_page, page_text  # noqa: E402

EPILOG = """examples:
  python3 scripts/subtitles.py info movie.srt
  python3 scripts/subtitles.py read movie.srt                          # timestamped lines, paged (--offset for the next part)
  python3 scripts/subtitles.py read movie.mkv --start 20:00 --end 25:00   # a video's subtitle track, one time window
  python3 scripts/subtitles.py find movie.srt "quarterly budget"       # where it is said: cue numbers and times, with context
  python3 scripts/subtitles.py find talk.json "budget|forecast" --regex   # also media_transcribe JSON
  python3 scripts/subtitles.py convert movie.srt movie.vtt
  python3 scripts/subtitles.py convert transcript.json captions.srt --reflow --line-chars 42   # from media_transcribe JSON
  python3 scripts/subtitles.py convert movie.srt -                     # plain prose to stdout (no times)
  python3 scripts/subtitles.py shift movie.srt fixed.srt --by -1.5     # 1.5 s earlier
  python3 scripts/subtitles.py sync movie.srt fixed.srt --fps 25:23.976
  python3 scripts/subtitles.py sync movie.srt fixed.srt --map 0:01:02.5=0:01:04 --map 1:40:00=1:40:03.2
  python3 scripts/subtitles.py merge en.srt fr.srt bilingual.srt --mode stack
  python3 scripts/subtitles.py split movie.srt --at 52:10 --out-dir parts/
  python3 scripts/subtitles.py clean raw.srt clean.srt --remove-sdh --line-chars 42 --min-duration 1
  python3 scripts/subtitles.py check movie.srt --video movie.mp4
  python3 scripts/subtitles.py extract movie.mkv --list
  python3 scripts/subtitles.py extract movie.mkv movie.en.srt --language eng

Formats come from the extension: .srt .vtt .ass .ssa .sbv .lrc .json .tsv .txt .md. Times: 83.5, 1:23.5, 01:02:03,250.
read, find, info and check also take a video (its text subtitle track; --track or --language picks one).
"""


def main() -> int:
    p = parser("Work with subtitle files: convert formats, shift or re-time, merge, split, clean, check timing and "
               "readability, and extract text subtitle tracks from videos.", EPILOG)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{info,read,find,convert,shift,sync,merge,split,clean,check,extract}")

    def enc(sp: Any) -> None:
        sp.add_argument("--encoding", help="input text encoding, e.g. cp1251 or latin-1 (default: detected: a BOM, UTF-8, UTF-16, or a guessed legacy code page)")

    def track(sp: Any) -> None:
        sp.add_argument("--track", type=int, help="for a video input: its subtitle track number, 1-based (default the first text track)")
        sp.add_argument("--language", dest="lang", help="for a video input: the track with this language code (eng, fra …)")
        sp.add_argument("--no-cache", action="store_true", help="extract a video's track again instead of reusing the cached copy")

    i = sub.add_parser("info", help="format, cue count, time span, styles and a timing summary", formatter_class=p.formatter_class)
    i.add_argument("input")
    i.add_argument("--show", type=int, default=10, help="cues to list (default 10; 0 for none)")
    enc(i)
    track(i)
    add_format(i)

    r = sub.add_parser("read", help="the cues as timestamped lines, paged (#cue [time] text)", formatter_class=p.formatter_class,
                       description="Timestamped text of every cue ('#12 [1:23.5] text'), within --max-chars; the last line gives the "
                                   "command for the next part. Inputs: subtitle files, media_transcribe JSON, or a video's text track.")
    r.add_argument("input")
    r.add_argument("--start", help="only cues from this time")
    r.add_argument("--end", help="only cues until this time")
    add_paging(r, "cues")
    enc(r)
    track(r)
    add_format(r)

    fd = sub.add_parser("find", aliases=["grep"], help="where words are said: matching cues with times and context", formatter_class=p.formatter_class,
                        description="Search the cue text (case-insensitive; accents and punctuation are ignored unless --exact). Each hit "
                                    "gives the cue number and time (the address for media_frames, media_edit trim …) with the cues around it.")
    fd.add_argument("input")
    fd.add_argument("query", help="words to find (all of them, in order, as a phrase), or a regular expression with --regex")
    fd.add_argument("--regex", action="store_true", help="the query is a Python regular expression")
    fd.add_argument("--exact", action="store_true", help="match case, accents and punctuation exactly")
    fd.add_argument("--context", type=int, default=1, help="cues shown before and after each hit (default 1)")
    fd.add_argument("--start", help="only search from this time")
    fd.add_argument("--end", help="only search until this time")
    add_paging(fd, "hits")
    enc(fd)
    track(fd)
    add_format(fd)

    c = sub.add_parser("convert", help="convert to another format (by extension); '-' writes plain text to stdout", formatter_class=p.formatter_class)
    c.add_argument("input")
    c.add_argument("output")
    c.add_argument("--to", help="output format when the extension does not say it")
    c.add_argument("--reflow", action="store_true", help="re-split long cues into subtitle-sized ones (uses word timings when present)")
    c.add_argument("--line-chars", "--max-chars", dest="line_chars", type=int, default=42, help="line length for --reflow (default 42)")
    c.add_argument("--max-lines", type=int, default=2, help="lines per cue for --reflow (default 2)")
    c.add_argument("--font", help="ASS output: font name (default Arial)")
    c.add_argument("--font-size", type=int, help="ASS output: font size in script pixels (default 5.5%% of the height)")
    c.add_argument("--color", help="ASS output: text colour, #RRGGBB or a name")
    c.add_argument("--play-res", help="ASS output: script resolution WxH (default 1920x1080, or --video's)")
    c.add_argument("--video", help="ASS output: take the script resolution from this video")
    c.add_argument("--force", action="store_true")
    enc(c)

    s = sub.add_parser("shift", help="move all cues (or those after a time) earlier or later", formatter_class=p.formatter_class)
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--by", required=True, help="offset, e.g. 2.5, -1.2, +0:01.5, -500ms")
    s.add_argument("--after", help="only shift cues starting at or after this time")
    s.add_argument("--force", action="store_true")
    enc(s)

    y = sub.add_parser("sync", help="re-time linearly: frame-rate change, factor, or two reference points", formatter_class=p.formatter_class)
    y.add_argument("input")
    y.add_argument("output")
    g = y.add_mutually_exclusive_group(required=True)
    g.add_argument("--fps", help="FROM:TO frame rates, e.g. 25:23.976 (subtitles made for 25 fps, video at 23.976)")
    g.add_argument("--factor", type=float, help="multiply every time by this")
    g.add_argument("--map", action="append", help="OLD=NEW time pair; give two (first and last line) for drift + offset")
    y.add_argument("--force", action="store_true")
    enc(y)

    m = sub.add_parser("merge", help="combine two subtitle files", formatter_class=p.formatter_class)
    m.add_argument("first")
    m.add_argument("second")
    m.add_argument("output")
    m.add_argument("--mode", choices=["interleave", "stack"], default="interleave", help="interleave all cues, or stack the second under the first (bilingual)")
    m.add_argument("--offset", default="0", help="shift the second file by this first")
    m.add_argument("--force", action="store_true")
    enc(m)

    sp = sub.add_parser("split", help="cut into parts at times (later parts re-based to 0)", formatter_class=p.formatter_class)
    sp.add_argument("input")
    sp.add_argument("--at", required=True, help="comma-separated cut times")
    sp.add_argument("--out-dir", default=".", help="folder for NAME-1.EXT, NAME-2.EXT … (default here)")
    sp.add_argument("--force", action="store_true")
    enc(sp)

    cl = sub.add_parser("clean", help="tidy: tags, hearing-impaired notes, empty and duplicate cues, overlaps, line length", formatter_class=p.formatter_class)
    cl.add_argument("input")
    cl.add_argument("output")
    cl.add_argument("--strip-formatting", action="store_true", help="remove italics/bold and all other tags")
    cl.add_argument("--remove-sdh", action="store_true", help="remove [sounds], (laughs), ♪ lyrics ♪ and SPEAKER: labels")
    cl.add_argument("--line-chars", "--max-chars", dest="line_chars", type=int, help="re-wrap lines longer than this many characters")
    cl.add_argument("--min-duration", type=float, default=0.0, help="extend shorter cues (without overlapping the next)")
    cl.add_argument("--min-gap", type=float, default=0.0, help="keep at least this gap between cues (s)")
    cl.add_argument("--keep-overlaps", action="store_true", help="do not trim overlapping cues")
    cl.add_argument("--no-merge", action="store_true", help="do not merge consecutive identical cues")
    cl.add_argument("--force", action="store_true")
    enc(cl)
    add_format(cl)

    ck = sub.add_parser("check", help="timing and readability problems (overlaps, speed, long lines …)", formatter_class=p.formatter_class)
    ck.add_argument("input")
    ck.add_argument("--video", help="also check against this video's duration")
    ck.add_argument("--max-cps", type=float, default=20.0, help="max reading speed, characters per second (default 20)")
    ck.add_argument("--line-chars", type=int, default=42, help="max characters per line (default 42)")
    ck.add_argument("--max-lines", type=int, default=2)
    ck.add_argument("--min-duration", type=float, default=0.7)
    ck.add_argument("--max-duration", type=float, default=7.0)
    add_paging(ck, "issues")
    enc(ck)
    track(ck)
    add_format(ck)

    x = sub.add_parser("extract", help="save a video's text subtitle track (or --list them)", formatter_class=p.formatter_class)
    x.add_argument("input")
    x.add_argument("output", nargs="?", help="output file (.srt, .vtt, .ass, .txt, .json …), or - to print timestamped lines (paged like read)")
    x.add_argument("--list", action="store_true", help="list subtitle tracks")
    x.add_argument("--track", type=int, help="subtitle track number, 1-based (see --list)")
    x.add_argument("--language", help="pick the track with this language code (eng, fra, …)")
    x.add_argument("--all", action="store_true", help="extract every text track into --out-dir")
    x.add_argument("--out-dir", help="folder for --all (default here)")
    x.add_argument("--force", action="store_true")
    x.add_argument("--no-cache", action="store_true", help="extract again instead of reusing the cached copy")
    add_paging(x, "cues")
    add_format(x)

    from _media import join_negative_values

    a = p.parse_args(join_negative_values(sys.argv[1:], ("--by", "--after", "--offset")))
    if getattr(a, "no_cache", False):
        import os

        os.environ["DESK_NO_CACHE"] = "1"
    if hasattr(a, "max_chars") and hasattr(a, "offset") and a.cmd in ("read", "find", "grep", "check", "extract"):
        check_paging(a)
    return {"info": cmd_info, "read": cmd_read, "find": cmd_find, "grep": cmd_find, "convert": cmd_convert, "shift": cmd_shift, "sync": cmd_sync, "merge": cmd_merge, "split": cmd_split,
            "clean": cmd_clean, "check": cmd_check, "extract": cmd_extract}[a.cmd](a)


def _load(path: str, encoding: str | None = None) -> Any:
    from _subs import load

    return load(input_file(path), encoding)


def _is_media(path: Path) -> bool:
    from _media import MEDIA_EXTS

    return path.suffix.lower() in MEDIA_EXTS


def _load_any(a: Any, path: str | None = None) -> Any:
    """A subtitle file, transcript JSON, or a video's text subtitle track (extracted once, then cached)."""
    src = input_file(path or a.input)
    if not _is_media(src):
        return _load(str(src), getattr(a, "encoding", None))
    from _media import probe

    info = probe(src, side_data=False)
    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    if not subs:
        raise SkillError(f"{src.name} has no subtitle tracks (transcribe the speech with media_transcribe.py)")
    k = _pick_track(subs, getattr(a, "track", None), getattr(a, "lang", None) or getattr(a, "language", None))
    return _extract_doc(info, k, subs[k])


def _pick_track(subs: list[dict[str, Any]], track: int | None, language: str | None) -> int:
    if track is not None:
        if not 1 <= track <= len(subs):
            raise UsageError(f"--track {track}: there are {len(subs)} subtitle track(s)")
        return track - 1
    if language:
        hits = [k for k, s in enumerate(subs) if (s.get("language") or "").lower().startswith(language.lower()[:2])]
        if not hits:
            raise SkillError(f"no subtitle track in language '{language}' (see extract --list)")
        return hits[0]
    text = [k for k, s in enumerate(subs) if s.get("text_based")]
    return text[0] if text else 0


def _extract_doc(info: dict[str, Any], k: int, s: dict[str, Any]) -> Any:
    """Track k of a video as a SubDoc: extracted to SRT with ffmpeg once per file content, then read from the cache."""
    from _media import run_ffmpeg
    from _subs import load

    if not s.get("text_based"):
        raise SkillError(f"track {k + 1} is image-based ({s.get('codec')}): its text cannot be read without OCR; look at frames instead")

    def build(target: Path) -> None:
        run_ffmpeg(["-i", info["file"], "-map", f"0:s:{k}", "-c:s", "srt", "-f", "srt", str(target)], label="extracting subtitles", progress=False)

    from _cache import cached_file, release

    try:
        f = cached_file(info["file"], "av-subtrack", {"track": k}, "1", build, "track.srt")
    except OSError:  # an unusable cache folder: extract into a temp folder (release() deletes it)
        import tempfile

        f = Path(tempfile.mkdtemp(prefix="desk-subx-")) / "track.srt"
        build(f)
    doc = load(f, fmt="srt")
    doc.language = s.get("language")
    release(f.parent)
    return doc


def _window(a: Any, cues: list[Any]) -> list[tuple[int, Any]]:
    """(cue number, cue) inside --start/--end."""
    s = _time(a.start) if getattr(a, "start", None) else None
    e = _time(a.end) if getattr(a, "end", None) else None
    return [(i, c) for i, c in enumerate(cues, 1) if (s is None or c.end > s) and (e is None or c.start < e)]


def _stamp(t: float) -> str:
    from _media import fmt_stamp

    return fmt_stamp(t)


def _row(n: int, c: Any) -> str:
    from _subs import ALIGN_NAMES, strip_tags

    who = f"{c.speaker}: " if c.speaker else ""
    where = f" ({ALIGN_NAMES[c.align]})" if c.align and c.align != 2 else ""
    return f"#{n} [{_stamp(c.start)}] {who}{' / '.join(x.strip() for x in strip_tags(c.text).splitlines() if x.strip())}{where}"


def cmd_read(a: Any) -> int:
    doc = _load_any(a)
    rows = _window(a, doc.cues)
    total = len(rows)
    page = rows[a.offset:][: a.limit] if a.limit else rows[a.offset:]
    if a.format == "json":
        emit_json_page([{"n": n, "start": round(c.start, 3), "end": round(c.end, 3), "text": c.text, **({"speaker": c.speaker} if c.speaker else {}),
                         **({"align": c.align} if c.align and c.align != 2 else {})} for n, c in page], a.offset, total, a.max_chars)
        return 0
    span = f"{_stamp(rows[0][1].start)}–{_stamp(rows[-1][1].end)}" if rows else "no cues"
    head = f"{Path(a.input).name}: {total} cue(s) {span}" + (f" (window {a.start or '0'}–{a.end or 'end'})" if a.start or a.end else "") + ". #cue [start] text:"
    print(page_text(head, [_row(n, c) for n, c in page], a.offset, total, a.max_chars, "cues"))
    return 0


def cmd_find(a: Any) -> int:
    from _subs import find_hits, strip_tags

    if a.context < 0:
        raise UsageError("--context must be 0 or more")
    doc = _load_any(a)
    cues = doc.cues
    inside = {n - 1 for n, _ in _window(a, cues)}
    texts = [" ".join(strip_tags(c.text).split()) for c in cues]
    hits = find_hits(texts, a.query, a.regex, a.exact, inside)
    total = len(hits)
    page = hits[a.offset:][: a.limit] if a.limit else hits[a.offset:]
    if a.format == "json":
        items = [{"n": i + 1, "start": round(cues[i].start, 3), "end": round(cues[i].end, 3), "text": texts[i],
                  "before": [texts[j] for j in range(max(0, i - a.context), i)], "after": [texts[j] for j in range(i + 1, min(len(cues), i + 1 + a.context))]}
                 for i in page]
        emit_json_page(items, a.offset, total, a.max_chars)
        return 0
    rows = []
    for i in page:
        block = [f"#{i + 1} [{_stamp(cues[i].start)}–{_stamp(cues[i].end)}] » {texts[i]}"]
        for j in range(max(0, i - a.context), i):
            block.insert(len(block) - 1, f"    #{j + 1} [{_stamp(cues[j].start)}] {texts[j]}")
        for j in range(i + 1, min(len(cues), i + 1 + a.context)):
            block.append(f"    #{j + 1} [{_stamp(cues[j].start)}] {texts[j]}")
        rows.append("\n".join(block))
    head = f"{total} hit(s) for {a.query!r} in {Path(a.input).name} ({len(cues)} cues)" + (":" if total else ".")
    print(page_text(head, rows, a.offset, total, a.max_chars, "hits"))
    if total:
        print("Times are addresses: look with media_frames.py frames --at TIME, or cut with media_edit.py trim --start/--end.")
    return 0


def _out(path: str, inputs: list[str], force: bool) -> Path:
    return output_path(path, inputs, force)


def _saved(out: Path, n: int, fmt: str) -> None:
    print(f"wrote {out} ({n} cues, {fmt})")


def _time(v: str, what: str = "time") -> float:
    from _media import parse_time

    return parse_time(v, None, what)


def _signed(v: str) -> float:
    s = v.strip()
    neg = s.startswith("-")
    t = _time(s.lstrip("+-"), "offset")
    return -t if neg else t


def cmd_info(a: Any) -> int:
    from _media import fmt_time
    from _subs import LAST_ENCODING, check, strip_tags

    doc = _load_any(a)
    enc_used = LAST_ENCODING.get(Path(a.input).name) if not _is_media(Path(a.input)) else None
    cues = doc.cues
    chars = sum(len(strip_tags(c.text)) for c in cues)
    talk = sum(max(0.0, c.duration) for c in cues)
    issues = check(cues)
    kinds: dict[str, int] = {}
    for it in issues:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    data = {"file": a.input, "format": doc.fmt, "cues": len(cues), "first": cues[0].start if cues else None, "last": cues[-1].end if cues else None,
            "characters": chars, "average_cps": round(chars / talk, 1) if talk else None, "language": doc.language, "encoding": enc_used,
            "styles": [s.split(":", 1)[1].split(",")[0].strip() for s in doc.styles], "speakers": sorted({c.speaker for c in cues if c.speaker}),
            "issues": kinds, "sample": [{"n": i + 1, "start": c.start, "end": c.end, "text": c.text} for i, c in enumerate(cues[: max(0, a.show)])],
            "positioned": sum(1 for c in cues if c.align and c.align != 2), "malformed_skipped": doc.skipped, "malformed_lines": doc.skipped_at}
    if a.format == "json":
        emit(data, "json")
        return 0
    lines = [f"# {Path(a.input).name}", "", f"- {doc.fmt.upper()}{' track of the video' if _is_media(Path(a.input)) else ''} · {len(cues)} cues" + (f" · {fmt_time(data['first'])} → {fmt_time(data['last'])}" if cues else "")
             + (f" · language {doc.language}" if doc.language else "")
             + (f" · encoding {enc_used}" + (" (guessed; pass --encoding if the letters look wrong)" if enc_used.startswith("cp") and not a.encoding else "")
                if enc_used and enc_used != "utf-8" else ""),
             f"- {chars} characters · average {data['average_cps']} chars/s while on screen" if talk else "- no timed text"]
    if data["styles"]:
        lines.append(f"- ASS styles: {', '.join(data['styles'])}")
    if data["speakers"]:
        lines.append(f"- speakers: {', '.join(data['speakers'][:20])}")
    if data["positioned"]:
        lines.append(f"- {data['positioned']} cue(s) placed away from the bottom centre ({{\\an8}} tags or cue settings; kept when converting)")
    if doc.skipped:
        lines.append(f"- {doc.skipped} malformed block(s) skipped (bad times or stray text), near line(s) {', '.join(map(str, doc.skipped_at))}")
    lines.append("- issues: " + (", ".join(f"{v} {k}" for k, v in kinds.items()) if kinds else "none") + ("  (details: subtitles.py check)" if kinds else ""))
    if data["sample"]:
        lines += ["", md_table(["#", "start", "end", "text"], [[s["n"], fmt_time(s["start"]), fmt_time(s["end"]), s["text"]] for s in data["sample"]])]
    print("\n".join(lines))
    return 0


def cmd_convert(a: Any) -> int:
    from _subs import FORMATS, ass_color, plain_text, reflow, save

    doc = _load(a.input, a.encoding)
    cues = doc.cues
    if a.reflow:
        cues = reflow(cues, a.line_chars, a.max_lines)
        doc.cues = cues
    if a.output == "-":
        print(cap(plain_text(cues), hint=f"Read it in parts with times: python3 scripts/subtitles.py read {a.input}"))
        return 0
    out = _out(a.output, [a.input], a.force)
    fmt = (a.to or out.suffix.lstrip(".")).lower()
    if fmt not in FORMATS:
        raise UsageError(f"unknown output format '{fmt}' (use --to with one of {', '.join(FORMATS)})")
    kw: dict[str, Any] = {}
    if fmt in ("ass", "ssa"):
        style: dict[str, Any] = {}
        if a.font:
            style["font"] = a.font
        if a.font_size:
            style["size"] = a.font_size
        if a.color:
            style["color"] = ass_color(a.color)
        if style:
            kw["style"] = style
        if a.play_res:
            try:
                w, h = (int(x) for x in a.play_res.lower().split("x"))
            except ValueError:
                raise UsageError("--play-res must look like 1920x1080") from None
            kw["play_res"] = (w, h)
        elif a.video:
            from _media import display_size, main_video, probe

            v = main_video(probe(input_file(a.video), side_data=True))
            if v:
                kw["play_res"] = display_size(v)
    save(doc, out, fmt, **kw)
    _saved(out, len(cues), fmt)
    return 0


def cmd_shift(a: Any) -> int:
    from _subs import save, shift

    doc = _load(a.input, a.encoding)
    off = _signed(a.by)
    after = _time(a.after) if a.after else None
    before = len(doc.cues)
    doc.cues = shift(doc.cues, off, after)
    out = _out(a.output, [a.input], a.force)
    fmt = save(doc, out)
    _saved(out, len(doc.cues), fmt)
    print(f"shifted by {off:+.3f} s" + (f" from {a.after}" if a.after else "") + (f"; dropped {before - len(doc.cues)} cue(s) that moved before 0" if before > len(doc.cues) else ""))
    return 0


def cmd_sync(a: Any) -> int:
    from _subs import retime, save

    doc = _load(a.input, a.encoding)
    if a.fps:
        try:
            f_from, f_to = (float(eval_ratio(x)) for x in a.fps.split(":"))
        except ValueError:
            raise UsageError("--fps must look like 25:23.976") from None
        k, b = f_from / f_to, 0.0
    elif a.factor:
        k, b = a.factor, 0.0
    else:
        pairs = []
        for mp in a.map or []:
            if "=" not in mp:
                raise UsageError("--map must look like OLD=NEW, e.g. 1:02.5=1:04")
            o, n = mp.split("=", 1)
            pairs.append((_time(o), _time(n)))
        if len(pairs) == 1:
            k, b = 1.0, pairs[0][1] - pairs[0][0]
        elif len(pairs) == 2:
            (o1, n1), (o2, n2) = pairs
            if abs(o2 - o1) < 1e-6:
                raise UsageError("the two --map points need different times")
            k = (n2 - n1) / (o2 - o1)
            b = n1 - k * o1
        else:
            raise UsageError("give one --map (offset) or two (offset and drift)")
    doc.cues = retime(doc.cues, k, b)
    out = _out(a.output, [a.input], a.force)
    fmt = save(doc, out)
    _saved(out, len(doc.cues), fmt)
    print(f"new time = {k:.6f} × old {b:+.3f} s")
    return 0


def eval_ratio(x: str) -> float:
    x = x.strip()
    if "/" in x:
        n, d = x.split("/", 1)
        return float(n) / float(d)
    return float(x)


def cmd_merge(a: Any) -> int:
    from _subs import merge, save, shift

    d1 = _load(a.first, a.encoding)
    d2 = _load(a.second, a.encoding)
    off = _signed(a.offset) if a.offset and a.offset != "0" else 0.0
    second = shift(d2.cues, off) if off else d2.cues
    d1.cues = merge(d1.cues, second, a.mode)
    out = _out(a.output, [a.first, a.second], a.force)
    fmt = save(d1, out)
    _saved(out, len(d1.cues), fmt)
    return 0


def cmd_split(a: Any) -> int:
    from _subs import save, split_at

    doc = _load(a.input, a.encoding)
    cuts = sorted(_time(t) for t in a.at.split(","))
    outdir = output_dir(a.out_dir)
    src = Path(a.input)
    parts = []
    rest = doc.cues
    base = 0.0
    for t in cuts:
        before, rest = split_at(rest, t - base)
        parts.append(before)
        base = t
    parts.append(rest)
    written = []
    for k, cues in enumerate(parts, 1):
        out = _out(str(outdir / f"{src.stem}-{k}{src.suffix}"), [a.input], a.force)
        doc.cues = cues
        save(doc, out)
        written.append((out, len(cues)))
    for out, n in written:
        print(f"wrote {out} ({n} cues)")
    return 0


def cmd_clean(a: Any) -> int:
    from _subs import clean, save

    doc = _load(a.input, a.encoding)
    before = len(doc.cues)
    doc.cues, stats = clean(doc.cues, a.strip_formatting, a.remove_sdh, a.line_chars, a.min_duration, a.min_gap, not a.keep_overlaps, not a.no_merge)
    out = _out(a.output, [a.input], a.force)
    fmt = save(doc, out)
    data = {"file": str(out), "cues_before": before, "cues_after": len(doc.cues), **stats}
    if a.format == "json":
        emit(data, "json")
    else:
        _saved(out, len(doc.cues), fmt)
        changed = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in stats.items() if v)
        print(f"{before} → {len(doc.cues)} cues" + (f"; {changed}" if changed else "; nothing needed fixing"))
    return 0


def cmd_check(a: Any) -> int:
    from _media import fmt_time
    from _subs import check

    doc = _load_any(a)
    vd = None
    if a.video:
        from _media import probe

        vd = probe(input_file(a.video), side_data=False).get("duration")
    elif _is_media(Path(a.input)):
        from _media import probe

        vd = probe(input_file(a.input), side_data=False).get("duration")
    issues = check(doc.cues, a.max_cps, a.line_chars, a.max_lines, a.min_duration, a.max_duration, video_duration=vd)
    kinds: dict[str, int] = {}
    for it in issues:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    total = len(issues)
    page = issues[a.offset:][: a.limit] if a.limit else issues[a.offset:]
    if a.format == "json":
        emit({"file": a.input, "cues": len(doc.cues), "summary": kinds, "total": total, "offset": a.offset, "issues": page}, "json")
        return 0
    if not issues:
        print(f"{Path(a.input).name}: {len(doc.cues)} cues, no problems found." + (f" ({doc.skipped} malformed block(s) were skipped when reading.)" if doc.skipped else ""))
        return 0
    head = (f"{Path(a.input).name}: {len(doc.cues)} cues, {total} issue(s): " + ", ".join(f"{v} {k}" for k, v in kinds.items())
            + (f"; {doc.skipped} malformed block(s) skipped when reading" if doc.skipped else "") + "\n\n| cue | time | issue | detail |\n|---|---|---|---|")
    rows = [f"| {it['cue']} | {fmt_time(it['time'])} | {it['kind']} | {it['detail']} |" for it in page]
    print(page_text(head, rows, a.offset, total, a.max_chars, "issues",
                    tail="\nsubtitles.py clean fixes overlaps, short cues and long lines; reading speed needs shorter text or longer cues."))
    return 0


def cmd_extract(a: Any) -> int:
    from _media import probe, run_ffmpeg

    info = probe(input_file(a.input), side_data=False)
    subs = [s for s in info["streams"] if s["type"] == "subtitle"]
    if a.list or (not a.output and not a.all):
        rows = [[k + 1, s.get("codec"), s.get("language", ""), s.get("title", ""), "text" if s.get("text_based") else "image (no text)", ", ".join(s.get("disposition", []))] for k, s in enumerate(subs)]
        if a.format == "json":
            emit({"file": info["file"], "tracks": [{**s, "track": k + 1} for k, s in enumerate(subs)]}, "json")
        elif not subs:
            print(f"{info['name']} has no subtitle tracks.")
        else:
            print(md_table(["track", "codec", "lang", "title", "kind", "flags"], rows))
        return 0
    if not subs:
        raise SkillError(f"{info['name']} has no subtitle tracks")

    def pick() -> list[int]:
        if a.all:
            return [k for k, s in enumerate(subs) if s.get("text_based")]
        if a.track is not None:
            if not 1 <= a.track <= len(subs):
                raise UsageError(f"--track {a.track}: there are {len(subs)} subtitle track(s)")
            return [a.track - 1]
        if a.language:
            hits = [k for k, s in enumerate(subs) if (s.get("language") or "").lower().startswith(a.language.lower()[:2])]
            if not hits:
                raise SkillError(f"no subtitle track in language '{a.language}' (see --list)")
            return [hits[0]]
        return [0]

    chosen = pick()
    if not chosen:
        raise SkillError("no text subtitle tracks to extract (image-based tracks need OCR, which this skill does not do)")
    outs: list[tuple[int, Path]] = []
    if a.all:
        d = output_dir(a.out_dir or ".")
        for k in chosen:
            lang = subs[k].get("language") or f"track{k + 1}"
            ext = ".ass" if subs[k].get("codec") in ("ass", "ssa") else ".srt"
            outs.append((k, output_path(d / f"{Path(info['name']).stem}.{k + 1}.{lang}{ext}", [info["file"]], a.force)))
    elif a.output == "-":
        doc = _extract_doc(info, chosen[0], subs[chosen[0]])
        rows = list(enumerate(doc.cues, 1))
        total = len(rows)
        page = rows[a.offset:][: a.limit] if a.limit else rows[a.offset:]
        if a.format == "json":
            emit_json_page([{"n": n, "start": round(c.start, 3), "end": round(c.end, 3), "text": c.text} for n, c in page], a.offset, total, a.max_chars)
        else:
            head = f"{info['name']} subtitle track {chosen[0] + 1}: {total} cue(s). #cue [start] text:"
            print(page_text(head, [_row(n, c) for n, c in page], a.offset, total, a.max_chars, "cues"))
        return 0
    else:
        if not a.output:
            raise UsageError("give an output file (or --list / --all)")
        outs.append((chosen[0], output_path(a.output, [info["file"]], a.force)))
    import tempfile

    from _subs import load, save

    results = []
    for k, out in outs:
        s = subs[k]
        if not s.get("text_based"):
            raise SkillError(f"track {k + 1} is image-based ({s.get('codec')}): its text cannot be extracted without OCR. "
                             "Look at it instead: burn it into frames with media_edit.py and view them, or remux it with media_convert.py --copy to .mkv")
        ext = out.suffix.lower()
        direct = {".srt": "srt", ".vtt": "webvtt", ".ass": "ass", ".ssa": "ass"}
        if ext in direct:
            run_ffmpeg(["-i", info["file"], "-map", f"0:s:{k}", "-c:s", direct[ext], str(out)], label="extracting subtitles", progress=False)
        else:
            tmpd = Path(tempfile.mkdtemp(prefix="desk-subx-"))
            try:
                tmp = tmpd / "x.srt"
                run_ffmpeg(["-i", info["file"], "-map", f"0:s:{k}", "-c:s", "srt", str(tmp)], label="extracting subtitles", progress=False)
                doc = load(tmp)
                doc.language = s.get("language")
                save(doc, out)
            finally:
                import shutil

                shutil.rmtree(tmpd, ignore_errors=True)
        n = len(load(out).cues) if out.suffix.lower() not in (".txt", ".md") else None
        results.append({"track": k + 1, "language": s.get("language"), "codec": s.get("codec"), "file": str(out), "cues": n})
    if a.format == "json":
        emit(results, "json")
    else:
        for r in results:
            print(f"wrote {r['file']} (track {r['track']}, {r['codec']}" + (f", {r['language']}" if r["language"] else "") + (f", {r['cues']} cues" if r["cues"] is not None else "") + ")")
    return 0


if __name__ == "__main__":
    run_main(main)
