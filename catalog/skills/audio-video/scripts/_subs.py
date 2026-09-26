"""Subtitle model, readers and writers for the audio-video skill: SRT, WebVTT, ASS/SSA, SBV, LRC, JSON (including
Whisper-style segments with words), TSV and plain text; plus timing operations, cleaning, reflow and checks.
Standard library only."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError, UsageError

FORMATS = ("srt", "vtt", "ass", "ssa", "sbv", "lrc", "json", "tsv", "txt", "md")


@dataclass
class Cue:
    start: float
    end: float
    text: str  # lines joined with "\n"; may hold <i>, <b>, <u> tags
    style: str | None = None
    speaker: str | None = None
    settings: str | None = None  # WebVTT cue settings, kept when writing VTT
    words: list[dict[str, Any]] | None = None
    align: int | None = None  # position on screen as a numpad digit (7 8 9 top, 4 5 6 middle, 1 2 3 bottom); None = default

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SubDoc:
    cues: list[Cue]
    fmt: str
    styles: list[str] = field(default_factory=list)  # raw ASS style lines
    style_format: str | None = None
    play_res: tuple[int, int] | None = None
    header: dict[str, str] = field(default_factory=dict)
    language: str | None = None
    skipped: int = 0  # malformed blocks left out when reading
    skipped_at: list[int] = field(default_factory=list)  # their line numbers (first few)


# ── time formats ────────────────────────────────────────────────────────

_TS = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,:](\d{1,3}))?"


def _ts(h: str | None, m: str, s: str, frac: str | None) -> float:
    ms = int((frac or "0").ljust(3, "0")[:3])
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + ms / 1000


def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(t: float) -> str:
    return srt_time(t).replace(",", ".")


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def sbv_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms:03d}"


def lrc_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{m:02d}:{s:02d}.{cs:02d}"


# ── positions ({\an8}) ────────────────────────────────────────────────

#: Legacy SSA \a values (1-3 bottom, 9-11 middle, 5-7 top) → numpad \\an values.
_SSA_ALIGN = {1: 1, 2: 2, 3: 3, 9: 4, 10: 5, 11: 6, 5: 7, 6: 8, 7: 9}
ALIGN_NAMES = {7: "top-left", 8: "top", 9: "top-right", 4: "left", 5: "center", 6: "right", 1: "bottom-left", 2: "bottom", 3: "bottom-right"}


def take_overrides(text: str) -> tuple[str, int | None]:
    """Removes ASS-style override blocks from SRT-like text ({\\an8} positions, {\\i1} italics …).

    Returns the text (italic/bold/underline kept as <i>/<b>/<u>) and the position as a numpad digit, if one was set.
    Braces without a backslash ("{laughs}") are ordinary text and stay.
    """
    align: list[int] = []

    def block(m: re.Match[str]) -> str:
        body = m.group(0)
        an = re.search(r"\\an([1-9])", body)
        if an:
            align.append(int(an.group(1)))
        else:
            a = re.search(r"\\a(\d{1,2})(?!\w)", body)
            if a and int(a.group(1)) in _SSA_ALIGN:
                align.append(_SSA_ALIGN[int(a.group(1))])
        out = []
        for code, on in re.findall(r"\\([ibu])([01])(?![0-9])", body):
            out.append(f"<{code}>" if on == "1" else f"</{code}>")
        return "".join(out)

    if "{\\" not in text:
        return text, None
    text = re.sub(r"\{\\[^{}]*\}", block, text)
    return text, (align[0] if align else None)


def vtt_align(settings: str | None) -> int | None:
    """A numpad position from WebVTT cue settings (line: for the row, align: for the column)."""
    if not settings:
        return None
    row, col = 1, 1  # bottom, centre
    m = re.search(r"\bline:(-?\d+(?:\.\d+)?)(%)?", settings)
    have = False
    if m:
        v = float(m.group(1))
        if m.group(2):
            row = 3 if v < 34 else (2 if v < 67 else 1)
        else:
            row = 3 if 0 <= v <= 2 else (1 if v < 0 else 2)
        have = True
    m = re.search(r"\balign:(start|left|end|right|center|middle)", settings)
    if m and m.group(1) in ("start", "left", "end", "right"):
        col = 0 if m.group(1) in ("start", "left") else 2
        have = True
    if not have:
        return None
    return (1 if row == 1 else 4 if row == 2 else 7) + col


def vtt_settings(align: int | None) -> str | None:
    """WebVTT cue settings for a numpad position (None for the default bottom centre)."""
    if not align or align == 2:
        return None
    parts = []
    if align >= 7:
        parts.append("line:0")
    elif align >= 4:
        parts.append("line:50%")
    if align in (1, 4, 7):
        parts.append("align:left")
    elif align in (3, 6, 9):
        parts.append("align:right")
    return " ".join(parts) or None


# ── reading ─────────────────────────────────────────────────────────────


# Legacy 8-bit code pages, guessed when a file is not UTF-8. Each decoding is scored per language by how many of
# its non-ASCII letters are typical for that language (untypical letters count against it). Latin letters only
# count in words that also have ASCII letters or are short ("Ïðèâåò" is not French); letters of other scripts
# only count outside Latin words ("dйjа" is not Russian), and their most common words add weight.
_CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґў"
_GREEK = "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰς"
_HEBREW = "אבגדהוזחטיכךלמםנןסעפףצץקרשת"
_ARABIC = "ابتثجحخدذرزسشصضطظعغفقكلمنهويىةءآأؤإئ"
_LANGS: list[tuple[tuple[str, ...], str, str]] = [
    # (code pages, typical non-ASCII letters, common words (non-Latin scripts only))
    (("cp1252",), "àâäçéèêëîïôöùûüÿœæ", ""),  # French, German
    (("cp1252",), "áéíóúñüàèìòçï", ""),  # Spanish, Italian, Catalan
    (("cp1252",), "áàâãçéêíóôõúü", ""),  # Portuguese
    (("cp1252",), "äöüßåæøéáëïó", ""),  # German, Nordic, Dutch
    (("cp1250", "iso8859_2"), "ąćęłńóśźż", ""),  # Polish
    (("cp1250", "iso8859_2"), "áčďéěíňóřšťúůýž", ""),  # Czech
    (("cp1250", "iso8859_2"), "áäčďéíĺľňóôŕšťúýž", ""),  # Slovak
    (("cp1250", "iso8859_2"), "áéíóöőúüű", ""),  # Hungarian
    (("cp1250", "iso8859_2"), "čćđšž", ""),  # Croatian, Slovene, Bosnian
    (("cp1250", "iso8859_2"), "ăâîșțşţ", ""),  # Romanian
    (("cp1254", "iso8859_9"), "çğıİöşüâîû", ""),  # Turkish
    (("cp1257", "iso8859_13"), "ąčęėįšųūžāēģīķļņõäöü", ""),  # Baltic
    (("cp1251", "koi8_r", "iso8859_5"), _CYR,
     "и в не на я что он с как а то это все она так его но да ты к у же вы за бы по мне вот от меня нет из мы тебя кто се е ще ти ми си той тя като і що з як та це"),
    (("cp1253", "iso8859_7"), _GREEK, "και το να του η την της με για δεν θα ο τα που σε στο στην είναι τι μου σου αυτό από οι τον μας"),
    (("cp1255", "iso8859_8"), _HEBREW, "של את לא על זה הוא אני מה גם כי אתה עם יש היא אבל כל רק אם לי לך הם"),
    (("cp1256", "iso8859_6"), _ARABIC, "في من على أن إلى لا ما هذا عن مع هل كان أنا أنت هو هي لم قد التي الذي كل لقد"),
]
_LATIN_CODECS = {"cp1252", "cp1250", "iso8859_2", "cp1254", "iso8859_9", "cp1257", "iso8859_13"}
LAST_ENCODING: dict[str, str] = {}  # file name → encoding used (for reports)


def _guess_legacy(raw: bytes) -> tuple[str, str]:
    from collections import Counter

    sample = raw[:300_000]
    codecs = list(dict.fromkeys(c for cs, _, _ in _LANGS for c in cs))
    best: tuple[float, str] | None = None
    for codec in codecs:
        try:
            text = sample.decode(codec)
        except UnicodeDecodeError:
            continue
        latin = codec in _LATIN_CODECS
        chars: Counter[str] = Counter()  # plausible positions only
        misplaced = 0
        words: Counter[str] = Counter()
        for w in re.findall(r"[^\W\d_]+", text):
            if w.isascii():
                continue
            has_ascii = any(c < "\x80" for c in w)
            high = [c for c in w if c >= "\x80"]
            if (latin and not has_ascii and len(w) > 2) or (not latin and has_ascii):
                misplaced += len(high)
                continue
            chars.update(c if c == "İ" else c.lower() for c in high)
            if not latin:
                words[w.lower()] += 1
        total = sum(chars.values())
        for cs, letters, common in _LANGS:
            if codec not in cs:
                continue
            good = sum(n for c, n in chars.items() if c in letters)
            score = good - 0.5 * (total - good) - 0.5 * misplaced
            if common:
                score += 3 * sum(words[x] for x in common.split())
            if best is None or score > best[0]:
                best = (score, codec)
    codec = best[1] if best else "cp1252"
    return raw.decode(codec, "replace"), codec


def read_text(path: Path, encoding: str | None = None) -> str:
    """Subtitle text: the given encoding, a BOM (UTF-8/16/32), UTF-8, UTF-16 without BOM, or a guessed legacy code
    page (Western, Central European, Turkish, Baltic, Cyrillic, Greek, Hebrew, Arabic)."""
    raw = path.read_bytes()
    if encoding:
        try:
            text, used = raw.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError) as e:
            raise SkillError(f"{path.name}: cannot decode as {encoding}: {e}") from None
    elif raw.startswith(b"\xef\xbb\xbf"):
        text, used = raw[3:].decode("utf-8", "replace"), "utf-8"
    elif raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text, used = raw.decode("utf-32", "replace"), "utf-32"
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, used = raw.decode("utf-16", "replace"), "utf-16"
    elif len(raw) > 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        text, used = raw.decode("utf-16-le", "replace"), "utf-16-le"
    elif len(raw) > 4 and raw[0:1] == b"\x00" and raw[2:3] == b"\x00":
        text, used = raw.decode("utf-16-be", "replace"), "utf-16-be"
    else:
        try:
            text, used = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, used = _guess_legacy(raw)
    LAST_ENCODING[path.name] = used
    return text.lstrip("\ufeff")


def detect_format(path: Path, text: str) -> str:
    ext = path.suffix.lower().lstrip(".")
    head = text.lstrip()[:400]
    if head.startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in head or re.search(r"^\[Events\]", text, re.M):
        return "ass"
    if ext in ("json",) or head.startswith(("{", "[")) and ext != "lrc":
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
    if ext in FORMATS and ext not in ("txt", "md"):
        return "ass" if ext == "ssa" else ext
    if re.search(r"-->\s*", head):
        return "srt"
    if re.match(r"\s*\d+:\d{2}:\d{2}\.\d{3},\d+:\d{2}:\d{2}\.\d{3}", head):
        return "sbv"
    if re.match(r"\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", head) or re.match(r"\s*\[(ar|ti|al|by|offset):", head):
        return "lrc"
    if ext == "tsv" or re.match(r"start\tend\ttext", head):
        return "tsv"
    raise SkillError(f"{path.name}: not a subtitle format this skill reads (srt, vtt, ass/ssa, sbv, lrc, json, tsv)")


def load(path: str | Path, encoding: str | None = None, fmt: str | None = None) -> SubDoc:
    p = Path(path)
    if not p.is_file():
        raise SkillError(f"{p} does not exist")
    text = read_text(p, encoding).replace("\r\n", "\n").replace("\r", "\n")
    f = (fmt or detect_format(p, text)).lower()
    if f == "ssa":
        f = "ass"
    reader = {"srt": parse_srt, "vtt": parse_vtt, "ass": parse_ass, "sbv": parse_sbv, "lrc": parse_lrc, "json": parse_json, "tsv": parse_tsv}.get(f)
    if reader is None:
        raise UsageError(f"cannot read '{f}' subtitles")
    doc = reader(text)
    doc.fmt = f
    doc.cues.sort(key=lambda c: (c.start, c.end))
    return doc


_TIMING = re.compile(r"^\s*" + _TS + r"\s*-->\s*" + _TS + r"(.*)$")


def _valid_timing(g: Sequence[str | None]) -> bool:
    """Minutes and seconds under 60, and the end not before the start (99:99:99,999 is not a time)."""
    for mi, se in ((g[1], g[2]), (g[5], g[6])):
        if int(mi or 0) >= 60 or int(se or 0) >= 60:
            return False
    return _ts(*g[4:8]) >= _ts(*g[0:4])


def parse_srt(text: str) -> SubDoc:
    """SRT, tolerant of missing blank lines and indexes; malformed blocks (bad times, stray text between cues) are
    skipped and counted instead of being glued onto the previous cue."""
    lines = text.split("\n")
    cues: list[Cue] = []
    cur: Cue | None = None
    buf: list[str] = []
    orphans: list[str] = []
    orphan_line = 0
    state = "none"  # "text": reading a cue's text; "bad": skipping a malformed cue; "none": between cues
    skipped = 0
    skipped_at: list[int] = []

    def skip(line_no: int) -> None:
        nonlocal skipped
        skipped += 1
        if len(skipped_at) < 5:
            skipped_at.append(line_no)

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            while buf and not buf[-1].strip():
                buf.pop()
            body, align = take_overrides("\n".join(x.rstrip() for x in buf).strip("\n"))
            cur.text = body
            cur.align = align
            cues.append(cur)
        cur = None
        buf.clear()

    for i, line in enumerate(lines):
        m = _TIMING.match(line)
        if m:
            # A bare number just before a timing line is the next cue's index.
            if state == "text" and buf and re.fullmatch(r"\s*\d+\s*", buf[-1] or ""):
                buf.pop()
            if orphans and re.fullmatch(r"\s*\d+\s*", orphans[-1]):
                orphans.pop()
            if any(o.strip() for o in orphans):
                skip(orphan_line)
            orphans = []
            flush()
            g = m.groups()
            if not _valid_timing(g):
                skip(i + 1)
                state = "bad"
                continue
            cur = Cue(_ts(*g[0:4]), _ts(*g[4:8]), "")
            extra = (g[8] or "").strip()
            if extra and not re.match(r"X1:", extra):
                cur.settings = extra
            state = "text"
            continue
        if not line.strip():
            if state == "text":
                flush()
            state = "none"
            continue
        if state == "text":
            buf.append(line)
        elif state == "none":
            if not orphans:
                orphan_line = i + 1
            orphans.append(line)
    flush()
    if orphans and not all(re.fullmatch(r"\s*\d*\s*", o) for o in orphans):
        skip(orphan_line)
    doc = SubDoc(cues, "srt")
    doc.skipped, doc.skipped_at = skipped, skipped_at
    return doc


def parse_vtt(text: str) -> SubDoc:
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []
    header: dict[str, str] = {}
    first = blocks[0] if blocks else ""
    for line in first.split("\n")[1:]:
        if ":" in line and "-->" not in line:
            k, v = line.split(":", 1)
            header[k.strip()] = v.strip()
    for b in blocks:
        lines = b.split("\n")
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) and "-->" not in lines[0]:
            if not any("-->" in ln for ln in lines):
                continue
        idx = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        m = _TIMING.match(lines[idx])
        if not m:
            continue
        g = m.groups()
        body = "\n".join(lines[idx + 1:]).strip("\n")
        speaker = None
        vm = re.match(r"^<v(?:\.[\w.]+)?\s+([^<>]+)>", body)
        if vm:
            speaker = vm.group(1).strip()
        settings = (g[8] or "").strip() or None
        body, align = take_overrides(body)
        cue = Cue(_ts(*g[0:4]), _ts(*g[4:8]), body, speaker=speaker, settings=settings, align=align or vtt_align(settings))
        cues.append(cue)
    doc = SubDoc(cues, "vtt", header=header)
    doc.language = header.get("Language")
    return doc


def parse_ass(text: str) -> SubDoc:
    section = ""
    fmt: list[str] = []
    styles: list[str] = []
    style_format: str | None = None
    header: dict[str, str] = {}
    cues: list[Cue] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        if section == "[script info]" and ":" in line:
            k, v = line.split(":", 1)
            header[k.strip()] = v.strip()
        elif section in ("[v4+ styles]", "[v4 styles]"):
            if line.lower().startswith("format:"):
                style_format = line
            elif line.lower().startswith("style:"):
                styles.append(line)
        elif section == "[events]":
            if line.lower().startswith("format:"):
                fmt = [x.strip().lower() for x in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("dialogue:"):
                if not fmt:
                    fmt = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
                parts = line.split(":", 1)[1].lstrip().split(",", len(fmt) - 1)
                if len(parts) < len(fmt):
                    continue
                rec = dict(zip(fmt, parts))
                try:
                    st = _ass_ts(rec.get("start", "0:00:00.00"))
                    en = _ass_ts(rec.get("end", "0:00:00.00"))
                except ValueError:
                    continue
                raw = rec.get("text", "")
                cues.append(Cue(st, en, ass_to_text(raw), style=(rec.get("style") or "").strip() or None, speaker=(rec.get("name") or rec.get("actor") or "").strip() or None,
                                align=take_overrides(raw)[1]))
    pr = None
    try:
        if header.get("PlayResX") and header.get("PlayResY"):
            pr = (int(header["PlayResX"]), int(header["PlayResY"]))
    except ValueError:
        pr = None
    return SubDoc(cues, "ass", styles=styles, style_format=style_format, play_res=pr, header=header)


def _ass_ts(s: str) -> float:
    m = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\s*", s)
    if not m:
        raise ValueError(s)
    h, mi, se, frac = m.groups()
    frac = (frac or "0")
    val = int(frac) / (10 ** len(frac))
    return int(h) * 3600 + int(mi) * 60 + int(se) + val


def ass_to_text(t: str) -> str:
    """ASS dialogue text → our text: line breaks, italics/bold/underline as tags, other overrides dropped."""
    def tag(m: re.Match[str]) -> str:
        out = []
        for code in re.findall(r"\\([ibu])([01])", m.group(0)):
            out.append(f"<{code[0]}>" if code[1] == "1" else f"</{code[0]}>")
        return "".join(out)

    t = re.sub(r"\{[^{}]*\}", tag, t)  # [^{}]: an override block never holds "{", and "{{{…" stays linear
    t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return balance_tags(t.strip())


def balance_tags(t: str) -> str:
    """Closes <i>/<b>/<u> tags left open and drops stray closing ones."""
    out = []
    open_: list[str] = []
    for part in re.split(r"(</?[ibu]>)", t):
        m = re.fullmatch(r"<(/?)([ibu])>", part)
        if not m:
            out.append(part)
            continue
        if m.group(1):
            if m.group(2) in open_:
                while open_:
                    x = open_.pop()
                    out.append(f"</{x}>")
                    if x == m.group(2):
                        break
        elif m.group(2) not in open_:
            open_.append(m.group(2))
            out.append(part)
    while open_:
        out.append(f"</{open_.pop()}>")
    return re.sub(r"<([ibu])></\1>", "", "".join(out))


def parse_sbv(text: str) -> SubDoc:
    cues = []
    for b in re.split(r"\n\s*\n", text.strip()):
        lines = b.split("\n")
        m = re.match(r"\s*" + _TS + r"\s*,\s*" + _TS, lines[0])
        if not m:
            continue
        g = m.groups()
        body, align = take_overrides("\n".join(lines[1:]).strip())
        cues.append(Cue(_ts(*g[0:4]), _ts(*g[4:8]), body, align=align))
    return SubDoc(cues, "sbv")


def parse_lrc(text: str) -> SubDoc:
    entries: list[tuple[float, str]] = []
    offset = 0.0
    header: dict[str, str] = {}
    for line in text.split("\n"):
        tags = re.findall(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]", line)
        if not tags:
            m = re.match(r"\s*\[(\w+):([^\]]*)\]", line)
            if m:
                header[m.group(1)] = m.group(2).strip()
                if m.group(1).lower() == "offset":
                    try:
                        offset = int(m.group(2)) / 1000
                    except ValueError:
                        pass
            continue
        body = re.sub(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", "", line)
        body = re.sub(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>", "", body).strip()
        for mi, se, frac in tags:
            fr = frac or "0"
            entries.append((int(mi) * 60 + int(se) + int(fr) / (10 ** len(fr)) - offset, body))
    entries.sort()
    cues = []
    for i, (t, body) in enumerate(entries):
        nxt = entries[i + 1][0] if i + 1 < len(entries) else t + max(2.0, min(6.0, 0.08 * len(body) + 1))
        if body:
            cues.append(Cue(max(0.0, t), max(t + 0.01, nxt), body))
    return SubDoc(cues, "lrc", header=header)


def parse_json(text: str) -> SubDoc:
    data = json.loads(text)
    items: Any = data
    lang = None
    if isinstance(data, dict):
        lang = data.get("language")
        items = data.get("segments") or data.get("cues") or data.get("events") or []
    if not isinstance(items, list):
        raise SkillError("JSON subtitles must be a list of {start, end, text} or an object with 'segments' or 'cues'")
    cues = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            st, en = float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            raise SkillError("each JSON cue needs numeric 'start' and 'end' (seconds) and 'text'") from None
        words = it.get("words")
        body, align = take_overrides(str(it.get("text", "")).strip())
        try:
            al = int(it["align"]) if it.get("align") is not None else align
        except (TypeError, ValueError):
            al = align
        cues.append(Cue(st, en, body, speaker=it.get("speaker"), style=it.get("style"), words=words if isinstance(words, list) else None, align=al if al in ALIGN_NAMES else None))
    doc = SubDoc(cues, "json")
    doc.language = lang
    return doc


def parse_tsv(text: str) -> SubDoc:
    cues = []
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if lines and lines[0].lower().startswith("start\t"):
        lines = lines[1:]
    for ln in lines:
        parts = ln.split("\t", 2)
        if len(parts) < 3:
            continue
        try:
            a, b = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        # Whisper TSV uses integer milliseconds.
        if re.fullmatch(r"\d+", parts[0].strip()) and re.fullmatch(r"\d+", parts[1].strip()):
            a, b = a / 1000, b / 1000
        body, align = take_overrides(parts[2].strip().replace("\\n", "\n"))
        cues.append(Cue(a, b, body, align=align))
    return SubDoc(cues, "tsv")


# ── writing ─────────────────────────────────────────────────────────────


def strip_tags(t: str) -> str:
    t = re.sub(r"</?(?:[ibu]|v(?:\.[\w.]+)?(?:\s[^<>]*)?|c(?:\.[\w.]+)?|lang[^<>]*|ruby|rt|font[^<>]*|span[^<>]*)>", "", t)
    t = re.sub(r"<\d{1,2}:\d{2}(?::\d{2})?\.\d{3}>", "", t)
    return html.unescape(t)


def srt_text(t: str) -> str:
    """Text safe for SRT: keeps i/b/u, drops WebVTT-only tags."""
    t = re.sub(r"<v(?:\.[\w.]+)?\s+[^<>]*>|</v>|</?c(?:\.[\w.]+)?>|</?lang[^<>]*>|</?ruby>|</?rt>", "", t)
    t = re.sub(r"<\d{1,2}:\d{2}(?::\d{2})?\.\d{3}>", "", t)
    return html.unescape(t)


def vtt_text(c: Cue) -> str:
    t = c.text
    t = re.sub(r"<\d{1,2}:\d{2}(?::\d{2})?\.\d{3}>", "", t)
    # escape stray markup characters, keep the tags WebVTT knows
    parts = re.split(r"(</?(?:[ibu]|v(?:\.[\w.]+)?(?:\s[^<>]*)?|c(?:\.[\w.]+)?|lang[^<>]*|ruby|rt)>)", t)
    out = []
    for k, p in enumerate(parts):
        if k % 2 == 1:
            out.append(p)
        else:
            out.append(html.escape(html.unescape(p), quote=False))
    text = "".join(out)
    if c.speaker and not text.startswith("<v"):
        text = f"<v {c.speaker}>{text}"
    return text


def text_to_ass(t: str) -> str:
    t = srt_text(t)
    t = t.replace("{", "(").replace("}", ")")
    t = re.sub(r"<([ibu])>", r"{\\\g<1>1}", t)
    t = re.sub(r"</([ibu])>", r"{\\\g<1>0}", t)
    t = re.sub(r"</?[a-z][^<>]*>", "", t)
    return t.replace("\n", "\\N")


DEFAULT_ASS_STYLE = {
    "font": "Arial", "size": 0, "color": "&H00FFFFFF", "outline_color": "&H00000000", "back_color": "&H80000000",
    "bold": 0, "outline": 2.5, "shadow": 0.5, "margin_v": 0, "alignment": 2, "border_style": 1,
}


def ass_color(value: str) -> str:
    """'#RRGGBB', 'RRGGBB', 'white', or '#RRGGBBAA' → ASS &HAABBGGRR."""
    names = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00", "red": "FF0000", "green": "00FF00", "blue": "0000FF", "cyan": "00FFFF", "magenta": "FF00FF", "gray": "808080", "grey": "808080"}
    v = value.strip()
    if v.upper().startswith("&H"):
        return v.upper()
    v = names.get(v.lower(), v).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", v):
        raise UsageError(f"bad colour '{value}' (use #RRGGBB, #RRGGBBAA or a name like white)")
    r, g, b = v[0:2], v[2:4], v[4:6]
    a = 255 - int(v[6:8], 16) if len(v) == 8 else 0
    return f"&H{a:02X}{b}{g}{r}".upper()


def write(doc_or_cues: SubDoc | Sequence[Cue], fmt: str, play_res: tuple[int, int] | None = None, style: dict[str, Any] | None = None, language: str | None = None) -> str:
    cues = doc_or_cues.cues if isinstance(doc_or_cues, SubDoc) else list(doc_or_cues)
    doc = doc_or_cues if isinstance(doc_or_cues, SubDoc) else None
    f = fmt.lower()
    if f == "srt":
        return "".join(f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{an_prefix(c)}{srt_text(c.text)}\n\n" for i, c in enumerate(cues, 1))
    if f == "vtt":
        head = "WEBVTT\n"
        lang = language or (doc.language if doc else None)
        if lang:
            head += f"Language: {lang}\n"
        body = "".join(f"\n{vtt_time(c.start)} --> {vtt_time(c.end)}{(' ' + st) if (st := _cue_settings(c)) else ''}\n{vtt_text(c)}\n" for c in cues)
        return head + body
    if f in ("ass", "ssa"):
        return write_ass(cues, doc, play_res, style)
    if f == "sbv":
        return "".join(f"{sbv_time(c.start)},{sbv_time(c.end)}\n{strip_tags(c.text)}\n\n" for c in cues)
    if f == "lrc":
        return "".join(f"[{lrc_time(c.start)}]{strip_tags(c.text).replace(chr(10), ' ')}\n" for c in cues)
    if f == "json":
        out = []
        for c in cues:
            d: dict[str, Any] = {"start": round(c.start, 3), "end": round(c.end, 3), "text": c.text}
            if c.speaker:
                d["speaker"] = c.speaker
            if c.style:
                d["style"] = c.style
            if c.words:
                d["words"] = c.words
            if c.align and c.align != 2:
                d["align"] = c.align
            out.append(d)
        lang = language or (doc.language if doc else None)
        return json.dumps({"language": lang, "cues": out} if lang else out, ensure_ascii=False, indent=2) + "\n"
    if f == "tsv":
        return "start\tend\ttext\n" + "".join(f"{int(round(c.start * 1000))}\t{int(round(c.end * 1000))}\t{strip_tags(c.text).replace(chr(10), ' ')}\n" for c in cues)
    if f == "txt":
        return plain_text(cues) + "\n"
    if f == "md":
        from _media import fmt_time

        return "".join(f"**[{fmt_time(c.start, ms=False)}]** " + (f"**{c.speaker}:** " if c.speaker else "") + strip_tags(c.text).replace("\n", " ") + "\n\n" for c in cues)
    raise UsageError(f"cannot write '{fmt}' (choose from {', '.join(FORMATS)})")


def an_prefix(c: Cue) -> str:
    """The {\\anN} tag SRT players understand, for a cue placed anywhere but bottom centre."""
    return f"{{\\an{c.align}}}" if c.align and c.align != 2 else ""


def _cue_settings(c: Cue) -> str | None:
    if c.settings and "-->" not in c.settings:
        return c.settings
    return vtt_settings(c.align)


def _overlap(prev: str, cur: str) -> int:
    """Words at the start of `cur` that repeat the end of `prev` (rolling captions repeat the previous line)."""
    pw, cw = prev.split(), cur.split()
    for n in range(min(len(pw), len(cw)), 1, -1):
        if pw[-n:] == cw[:n]:
            return n
    return 0


def plain_text(cues: Sequence[Cue], para_gap: float = 2.0) -> str:
    """Readable prose: cue texts joined, a new paragraph after a pause of para_gap seconds or a change of speaker
    (named when the subtitles name them). Text repeated from the previous cue (rolling captions) is dropped."""
    out: list[str] = []
    prev_end = None
    prev_text = ""
    speaker = None
    para: list[str] = []
    for c in cues:
        t = strip_tags(c.text).replace("\n", " ").strip()
        if not t:
            continue
        n = _overlap(prev_text, t)
        prev_text = t
        t = " ".join(t.split()[n:])
        if not t:
            prev_end = c.end
            continue
        new_speaker = c.speaker and c.speaker != speaker
        if para and (new_speaker or (prev_end is not None and c.start - prev_end >= para_gap)):
            out.append(" ".join(para))
            para = []
        if new_speaker:
            speaker = c.speaker
            para.append(f"{speaker}:")
        para.append(t)
        prev_end = c.end
    if para:
        out.append(" ".join(para))
    return "\n\n".join(out)


def write_ass(cues: Sequence[Cue], doc: SubDoc | None, play_res: tuple[int, int] | None, style: dict[str, Any] | None) -> str:
    pr = play_res or (doc.play_res if doc and doc.play_res else None) or (1920, 1080)
    st = {**DEFAULT_ASS_STYLE, **(style or {})}
    size = st["size"] or max(16, round(pr[1] * 0.055))
    margin_v = st["margin_v"] or max(10, round(pr[1] * 0.05))
    lines = ["[Script Info]", "; written by Desk's audio-video skill", "ScriptType: v4.00+", f"PlayResX: {pr[0]}", f"PlayResY: {pr[1]}",
             "ScaledBorderAndShadow: yes", "WrapStyle: 0", "", "[V4+ Styles]"]
    if doc and doc.styles and not style:
        lines.append(doc.style_format or "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
        lines += doc.styles
        known = {s.split(":", 1)[1].split(",")[0].strip() for s in doc.styles}
    else:
        lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
        lines.append(f"Style: Default,{st['font']},{size},{st['color']},&H000000FF,{st['outline_color']},{st['back_color']},{-1 if st['bold'] else 0},0,0,0,100,100,0,0,{st['border_style']},{st['outline']:g},{st['shadow']:g},{st['alignment']},{round(pr[0] * 0.05)},{round(pr[0] * 0.05)},{margin_v},1")
        known = {"Default"}
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for c in cues:
        sty = c.style if c.style in known else ("Default" if "Default" in known else sorted(known)[0])
        lines.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},{sty},{(c.speaker or '').replace(',', ' ')},0,0,0,,{an_prefix(c)}{text_to_ass(c.text)}")
    return "\n".join(lines) + "\n"


def save(cues: SubDoc | Sequence[Cue], out: Path, fmt: str | None = None, **kw: Any) -> str:
    f = (fmt or out.suffix.lower().lstrip(".")).lower()
    if f not in FORMATS:
        raise UsageError(f"cannot write '{out.suffix}' subtitles (use one of: .{', .'.join(FORMATS)})")
    text = write(cues, f, **kw)
    out.write_text(text, encoding="utf-8", newline="\n")
    return f


# ── operations ──────────────────────────────────────────────────────────


def shift(cues: Iterable[Cue], offset: float, after: float | None = None) -> list[Cue]:
    out = []
    for c in cues:
        if after is not None and c.start < after:
            out.append(c)
            continue
        s, e = c.start + offset, c.end + offset
        if e <= 0:
            continue
        words = [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in c.words] if c.words else None
        out.append(replace(c, start=max(0.0, s), end=e, words=words))
    return out


def retime(cues: Iterable[Cue], a: float, b: float) -> list[Cue]:
    """t' = a·t + b for every time (frame-rate changes, two-point sync)."""
    out = []
    for c in cues:
        s, e = a * c.start + b, a * c.end + b
        if e <= 0:
            continue
        words = [{**w, "start": a * w["start"] + b, "end": a * w["end"] + b} for w in c.words] if c.words else None
        out.append(replace(c, start=max(0.0, s), end=e, words=words))
    return out


def split_at(cues: Sequence[Cue], t: float) -> tuple[list[Cue], list[Cue]]:
    """Cues before `t`, and cues after `t` re-based to start at 0; a cue spanning `t` is cut in two."""
    a, b = [], []
    for c in cues:
        if c.end <= t:
            a.append(c)
        elif c.start >= t:
            b.append(replace(c, start=c.start - t, end=c.end - t, words=None))
        else:
            a.append(replace(c, end=t, words=None))
            b.append(replace(c, start=0.0, end=c.end - t, words=None))
    return a, b


def merge(a: Sequence[Cue], b: Sequence[Cue], mode: str = "interleave", min_overlap: float = 0.5) -> list[Cue]:
    """interleave: all cues sorted by time; stack: B's text goes under the A cue it overlaps most (bilingual)."""
    if mode == "interleave":
        return sorted([*a, *b], key=lambda c: (c.start, c.end))
    if mode != "stack":
        raise UsageError("merge mode must be interleave or stack")
    out = [replace(c, words=None) for c in a]
    extra = []
    for cb in b:
        best, best_ov = None, 0.0
        for ca in out:
            ov = min(ca.end, cb.end) - max(ca.start, cb.start)
            if ov > best_ov:
                best, best_ov = ca, ov
        if best is not None and best_ov >= min_overlap * min(cb.duration, best.duration):
            best.text = best.text + "\n" + cb.text
        else:
            extra.append(cb)
    return sorted([*out, *extra], key=lambda c: (c.start, c.end))


_SDH = [
    re.compile(r"\[[^\[\]]*\]"),  # [MUSIC], [door slams] (innermost brackets: linear on "[[[…")
    re.compile(r"\([^()]*\)"),  # (laughs)
    # ♪ lyrics ♪ and lines of marks alone, one line at a time: [♪♫#*\s]+ once ran across line breaks (cubic on blank
    # lines, and a line ending in # or * right after a blank line was taken for lyrics)
    re.compile(r"^(?:[♪♫#*]|[^\S\n])[^\n]*[♪♫#*][^\S\n]*$|^[^\S\n]*[♪♫#*]+[^\S\n]*$", re.M),
    re.compile(r"^[^\S\n]*[A-Z][A-Z0-9 .'\-]{1,30}:\s*", re.M),  # SPEAKER: (blank lines before it are dropped later anyway)
]


def wrap(text: str, max_chars: int, max_lines: int = 2) -> str:
    """Re-breaks text into balanced lines of at most max_chars (more lines only when it cannot fit)."""
    plain = " ".join(text.split())
    if len(strip_tags(plain)) <= max_chars:
        return plain
    words = plain.split(" ")
    n_lines = max(2, -(-len(strip_tags(plain)) // max_chars))
    target = len(strip_tags(plain)) / n_lines
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and (len(strip_tags(cand)) > max_chars or (len(strip_tags(cur)) >= target and len(lines) < n_lines - 1)):
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def clean(cues: Sequence[Cue], strip_formatting: bool = False, remove_sdh: bool = False, max_chars: int | None = None,
          min_duration: float = 0.0, min_gap: float = 0.0, fix_overlaps: bool = True, merge_duplicates: bool = True) -> tuple[list[Cue], dict[str, int]]:
    """Tidies cues and reports what changed."""
    stats = {"tags_removed": 0, "sdh_removed": 0, "empty_removed": 0, "duplicates_merged": 0, "overlaps_fixed": 0, "extended": 0, "rewrapped": 0}
    work: list[Cue] = []
    for c in sorted(cues, key=lambda c: (c.start, c.end)):
        t = c.text
        if strip_formatting:
            nt = strip_tags(t)
            if nt != t:
                stats["tags_removed"] += 1
            t = nt
        else:
            t = balance_tags(srt_text(t))
        if remove_sdh:
            nt = t
            for rx in _SDH:
                nt = rx.sub("", nt)
            if nt != t:
                stats["sdh_removed"] += 1
            t = nt
        t = "\n".join(" ".join(line.split()) for line in t.split("\n"))
        t = "\n".join(line for line in t.split("\n") if strip_tags(line).strip(" -–—"))
        if not strip_tags(t).strip():
            stats["empty_removed"] += 1
            continue
        if max_chars:
            longest = max(len(strip_tags(x)) for x in t.split("\n"))
            if longest > max_chars:
                t = wrap(t, max_chars)
                stats["rewrapped"] += 1
        if c.end <= c.start:
            c = replace(c, end=c.start + max(min_duration, 1.0))
            stats["extended"] += 1
        work.append(replace(c, text=t))
    if merge_duplicates:
        merged: list[Cue] = []
        for c in work:
            if merged and strip_tags(merged[-1].text).strip() == strip_tags(c.text).strip() and c.start - merged[-1].end <= 0.25:
                merged[-1].end = max(merged[-1].end, c.end)
                stats["duplicates_merged"] += 1
            else:
                merged.append(c)
        work = merged
    for i, c in enumerate(work):
        nxt = work[i + 1].start if i + 1 < len(work) else None
        if fix_overlaps and nxt is not None and c.end > nxt - min_gap:
            new_end = max(c.start + 0.1, nxt - min_gap)
            if new_end < c.end:
                c.end = new_end
                stats["overlaps_fixed"] += 1
        if min_duration and c.duration < min_duration:
            limit = (nxt - min_gap) if nxt is not None else c.start + min_duration
            new_end = min(c.start + min_duration, max(c.end, limit))
            if new_end > c.end:
                c.end = new_end
                stats["extended"] += 1
    return work, stats


def check(cues: Sequence[Cue], max_cps: float = 20.0, max_chars: int = 42, max_lines: int = 2, min_duration: float = 0.7, max_duration: float = 7.0,
          min_gap: float = 0.083, video_duration: float | None = None) -> list[dict[str, Any]]:
    """Timing and readability problems, one entry per issue with the cue number (1-based)."""
    issues: list[dict[str, Any]] = []
    prev: Cue | None = None
    for i, c in enumerate(cues, 1):
        plain = strip_tags(c.text)
        lines = [x for x in plain.split("\n")]
        chars = len(plain.replace("\n", ""))
        if c.end <= c.start:
            issues.append({"cue": i, "time": c.start, "kind": "non-positive duration", "detail": f"ends at {c.end:.3f} s"})
        elif c.duration < min_duration:
            issues.append({"cue": i, "time": c.start, "kind": "too short", "detail": f"{c.duration:.2f} s < {min_duration:g} s"})
        elif c.duration > max_duration:
            issues.append({"cue": i, "time": c.start, "kind": "too long on screen", "detail": f"{c.duration:.1f} s > {max_duration:g} s"})
        if c.duration > 0 and chars / c.duration > max_cps:
            issues.append({"cue": i, "time": c.start, "kind": "reading speed", "detail": f"{chars / c.duration:.1f} chars/s > {max_cps:g}"})
        if lines and max(len(x) for x in lines) > max_chars:
            issues.append({"cue": i, "time": c.start, "kind": "long line", "detail": f"{max(len(x) for x in lines)} chars > {max_chars}"})
        if len(lines) > max_lines:
            issues.append({"cue": i, "time": c.start, "kind": "too many lines", "detail": f"{len(lines)} > {max_lines}"})
        if not plain.strip():
            issues.append({"cue": i, "time": c.start, "kind": "empty", "detail": ""})
        if prev is not None:
            if c.start < prev.start:
                issues.append({"cue": i, "time": c.start, "kind": "out of order", "detail": f"starts before cue {i - 1}"})
            elif c.start < prev.end - 1e-6:
                issues.append({"cue": i, "time": c.start, "kind": "overlap", "detail": f"overlaps cue {i - 1} by {prev.end - c.start:.2f} s"})
            elif 0 < c.start - prev.end < min_gap:
                issues.append({"cue": i, "time": c.start, "kind": "tiny gap", "detail": f"{(c.start - prev.end) * 1000:.0f} ms after cue {i - 1}"})
        if video_duration is not None and c.end > video_duration + 0.5:
            issues.append({"cue": i, "time": c.start, "kind": "past the end", "detail": f"ends at {c.end:.1f} s, the video lasts {video_duration:.1f} s"})
        prev = c
    return issues


def from_words(words: Sequence[dict[str, Any]], max_chars: int = 42, max_lines: int = 2, max_duration: float = 6.0, gap_split: float = 0.8) -> list[Cue]:
    """Subtitle cues built from word timings (Whisper words): breaks at sentence ends, pauses and size limits."""
    cues: list[Cue] = []
    cur: list[dict[str, Any]] = []
    limit = max_chars * max_lines

    def text_of(ws: Sequence[dict[str, Any]]) -> str:
        return "".join(str(w.get("word", w.get("text", ""))) for w in ws).strip()

    def flush() -> None:
        if cur:
            t = text_of(cur)
            if t:
                cues.append(Cue(float(cur[0]["start"]), float(cur[-1]["end"]), wrap(t, max_chars, max_lines), words=list(cur)))
            cur.clear()

    for w in words:
        if "start" not in w or "end" not in w:
            continue
        if cur:
            gap = float(w["start"]) - float(cur[-1]["end"])
            too_long = len(text_of([*cur, w])) > limit or float(w["end"]) - float(cur[0]["start"]) > max_duration
            if gap >= gap_split or too_long:
                flush()
        cur.append(w)
        tok = str(w.get("word", w.get("text", ""))).strip()
        if tok.endswith((".", "?", "!", "…", "。", "？", "！")) and len(text_of(cur)) >= max_chars * 0.6:
            flush()
    flush()
    return cues


def reflow(cues: Sequence[Cue], max_chars: int = 42, max_lines: int = 2, max_duration: float = 6.0) -> list[Cue]:
    """Splits long cues into subtitle-sized ones: by word timings when present, else proportionally by characters."""
    out: list[Cue] = []
    limit = max_chars * max_lines
    for c in cues:
        if c.words:
            out.extend(from_words(c.words, max_chars, max_lines, max_duration))
            continue
        plain = " ".join(strip_tags(c.text).split())
        if len(plain) <= limit and c.duration <= max_duration:
            out.append(replace(c, text=wrap(plain, max_chars, max_lines), words=None))
            continue
        # sentence-ish chunks, then greedy packing into pieces that fit
        parts = re.split(r"(?<=[.!?…;:,])\s+", plain)
        pieces: list[str] = []
        cur = ""
        for part in parts:
            for w in part.split(" "):
                cand = f"{cur} {w}".strip()
                if len(cand) > limit and cur:
                    pieces.append(cur)
                    cur = w
                else:
                    cur = cand
            if len(cur) >= limit * 0.6:
                pieces.append(cur)
                cur = ""
        if cur:
            pieces.append(cur)
        total = sum(len(p) for p in pieces) or 1
        t = c.start
        for p in pieces:
            d = c.duration * len(p) / total
            out.append(replace(c, start=t, end=t + d, text=wrap(p, max_chars, max_lines), words=None))
            t += d
    return out


# ── search ──────────────────────────────────────────────────────────────


def norm_text(t: str) -> str:
    """Text for loose matching: no accents, case-folded, punctuation as spaces."""
    import unicodedata

    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def find_hits(texts: Sequence[str], query: str, regex: bool = False, exact: bool = False, allowed: set[int] | None = None) -> list[int]:
    """Indexes of the texts that match: a phrase (loose by default), an exact substring, or a regular expression.
    When a loose phrase matches nothing, it may straddle two cues: each text joined with the next is tried too."""
    if regex:
        try:
            rx = re.compile(query, 0 if exact else re.IGNORECASE)
        except re.error as e:
            raise UsageError(f"bad regular expression: {e}") from None
        match = lambda text: bool(rx.search(text))  # noqa: E731
    elif exact:
        match = lambda text: query in text  # noqa: E731
    else:
        q = norm_text(query)
        if not q:
            raise UsageError("the query has no letters or digits")
        normed = [f" {norm_text(t)} " for t in texts]
        hits = [i for i in range(len(texts)) if (allowed is None or i in allowed) and f" {q} " in normed[i]]
        if not hits:
            hits = [i for i in range(len(texts) - 1) if (allowed is None or i in allowed) and f" {q} " in normed[i][:-1] + normed[i + 1]]
        return hits
    return [i for i in range(len(texts)) if (allowed is None or i in allowed) and match(texts[i])]
