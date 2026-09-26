"""Text-or-binary decisions and encoding detection, shared by file_identify, file_survey and text_tool.

UTF-8/ASCII and BOMs are decided with the standard library; other encodings go through charset-normalizer
(imported lazily). Standard library only otherwise.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field

BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]

# Bytes that never appear in real text (NUL is handled separately).
_CTRL = bytes(b for b in range(32) if b not in (9, 10, 11, 12, 13, 27)) + b"\x7f"


@dataclass
class Encoding:
    name: str  # a Python codec name: 'utf-8', 'ascii', 'utf-16-le', 'cp1252', …
    confidence: float
    bom: str | None = None  # the BOM's encoding when the data starts with one
    method: str = "utf-8 check"
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    language: str | None = None

    @property
    def bom_len(self) -> int:
        if not self.bom:
            return 0
        for b, n in BOMS:
            if n == self.bom:
                return len(b)
        return 0

    @property
    def label(self) -> str:
        base = self.name
        if self.name == "ascii":
            base = "ascii (UTF-8 compatible)"
        return f"{base} with BOM" if self.bom else base


def bom_of(data: bytes) -> str | None:
    for b, name in BOMS:
        if data.startswith(b):
            return name
    return None


def _utf16_guess(data: bytes) -> str | None:
    """UTF-16 without a BOM: mostly-ASCII text leaves NULs in every other byte."""
    n = min(len(data), 4096) & ~1
    if n < 8:
        return None
    even = data[0:n:2].count(0)
    odd = data[1:n:2].count(0)
    half = n // 2
    if odd > 0.4 * half and even < 0.05 * half:
        return "utf-16-le"
    if even > 0.4 * half and odd < 0.05 * half:
        return "utf-16-be"
    return None


def _wide_decodes(data: bytes, bom: str, complete: bool) -> bool:
    """True when the bytes after a UTF-16/32 BOM decode strictly (random data breaks on a lone surrogate or a code
    point past U+10FFFF within a few hundred bytes) and hold no NUL characters."""
    unit = 4 if bom.startswith("utf-32") else 2
    body = data[unit if unit == 4 else 2 :]
    n = len(body) - len(body) % unit
    if not complete:
        n = max(0, n - 2 * unit)  # a surrogate pair may be cut at the end of the sample
    try:
        text = body[:n].decode(bom)
    except UnicodeDecodeError:
        return False
    return text.count("\x00") <= max(2, len(text) // 1000)


def looks_binary(data: bytes) -> bool:
    """True when a sample is clearly not text in any 8-bit or UTF encoding."""
    if not data:
        return False
    if bom_of(data) or _utf16_guess(data):
        return False
    sample = data[:65536]
    nul = sample.count(0)
    if nul > max(2, len(sample) // 1000):
        return True
    ctrl = len(sample) - len(sample.translate(None, _CTRL))
    return ctrl > max(4, len(sample) * 0.03)


def decode_prefix(data: bytes, encoding: str, complete: bool) -> str:
    """Decodes a sample, dropping an incomplete trailing character when the sample was cut."""
    dec = codecs.getincrementaldecoder(encoding)(errors="replace")
    return dec.decode(data, final=complete)


def detect(data: bytes, complete: bool = False, use_normalizer: bool = True) -> Encoding | None:
    """The encoding of a text sample, or None when it looks binary."""
    bom = bom_of(data)
    if bom:
        if bom != "utf-8" and not _wide_decodes(data, bom, complete):
            return None  # random bytes that happen to start with FE FF (1 file in 30,000): not UTF-16 text
        return Encoding(bom, 1.0, bom=bom, method="BOM")
    guess16 = _utf16_guess(data)
    if guess16:
        try:
            decode_prefix(data[: len(data) & ~1], guess16, True)
            return Encoding(guess16, 0.85, method="NUL pattern")
        except UnicodeDecodeError:
            pass
    if looks_binary(data):
        return None
    try:
        if data.isascii():
            return Encoding("ascii", 1.0, method="ascii check")
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(data, final=complete)
        return Encoding("utf-8", 0.99, method="utf-8 check")
    except UnicodeDecodeError:
        pass
    if not use_normalizer:
        return Encoding("cp1252", 0.4, method="fallback (not UTF-8)")
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return Encoding("cp1252", 0.4, method="fallback (not UTF-8)")
    matches = from_bytes(data[:65536], steps=5, chunk_size=512, threshold=0.25)
    best = matches.best()
    if best is None:
        return _fallback(data[:16384])
    # Among near-equal candidates, prefer the encodings real files use (charset-normalizer happily says cp1257
    # or cp1250 for Western text that decodes identically, or almost, as cp1252).
    sample = data[:16384]
    cands = [
        {"enc": m.encoding, "chaos": m.chaos, "coh": m.coherence, "lang": m.language, "names": list(m.could_be_from_charset or [m.encoding])}
        for m in list(matches)[:8]
        if m.chaos <= best.chaos + 0.15
    ]
    # charset-normalizer sometimes drops the right code page on short texts (Finnish "ää" without cp1252, a short French
    # CSV read as cp932 or mac_latin2, Hebrew as koi8_r): offer the common code pages as rescue candidates. They rank
    # below charset-normalizer's own candidates unless their decoding is more plausible (see rank()).
    have = {n for c in cands for n in c["names"]}
    try:
        single_byte = len(data[:4096].decode(best.encoding, "replace")) == len(data[:4096])
    except LookupError:
        single_byte = False
    high = sum(1 for b in sample if b >= 0x80)
    sparse = high <= 0.3 * len(sample)  # mostly ASCII: a multi-byte CJK reading is doubtful
    for extra in RESCUE if (single_byte or sparse) else ():
        if extra not in have:
            try:
                data[: len(data) if complete else max(0, len(data) - 4)].decode(extra)
            except UnicodeDecodeError:
                continue
            cands.append({"enc": extra, "chaos": best.chaos, "coh": 0.0, "lang": None, "names": [extra]})

    def rank(c: dict) -> tuple[float, int, float, int, float]:
        try:
            plaus = _plausibility(sample.decode(c["enc"], "replace"))
        except LookupError:
            plaus = 0.0
        pref = min((PREFERRED.index(n) for n in c["names"] if n in PREFERRED), default=len(PREFERRED))
        # Equally plausible decodings: a code page files really use beats a rare one (a short French cp1252 CSV also
        # reads as mac_latin2 "Cafť", and charset-normalizer's language coherence sometimes prefers that).
        return -round(plaus, 2), min(_tier(n) for n in c["names"]), -int(c["coh"] * 5), pref, c["chaos"]  # coherence in steps of 0.2: closer is a tie

    ranked = sorted(cands, key=rank)
    pick = ranked[0]
    name = _preferred(pick["names"])
    lang = pick["lang"] if pick["lang"] and pick["lang"] != "Unknown" else None
    conf = round(max(0.05, min(0.98, (1 - pick["chaos"]) * (0.7 + 0.3 * max(pick["coh"], 0.3)))), 2)
    # Alternatives: other candidates, best first, that decode this sample differently (identical decodings are not a
    # choice), each with a confidence below the pick's.
    ref = sample.decode(name, "replace")
    alts: list[tuple[str, float]] = []
    seen = {name}
    # common code pages first (stable: in rank order within a tier)
    others = sorted(ranked[1:], key=lambda c: min(_tier(n) for n in c["names"])) + [{"enc": m.encoding, "names": [m.encoding]} for m in list(matches)[:6]]
    for c in others:
        n = _preferred(c["names"])
        if n in seen:
            continue
        seen.add(n)
        try:
            if sample.decode(n, "replace") == ref:
                continue
        except LookupError:
            continue
        alts.append((n, round(max(0.05, conf - 0.05 * (len(alts) + 1)), 2)))
    return Encoding(name, conf, method="charset-normalizer", alternatives=alts[:4], language=lang)


RESCUE = ("cp1252", "cp1250", "cp1251", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "koi8_r")


def _fallback(sample: bytes) -> Encoding:
    """charset-normalizer found nothing (short or odd text): the most plausible common code page, with a low
    confidence; latin-1 (which decodes anything) when none fits."""
    scored = []
    for enc in RESCUE:
        try:
            text = sample.decode(enc)
        except UnicodeDecodeError:
            continue
        scored.append((-round(_plausibility(text), 2), PREFERRED.index(enc) if enc in PREFERRED else len(PREFERRED), enc))
    scored.sort()
    if not scored or -scored[0][0] < 0.5:
        return Encoding("latin-1", 0.3, method="fallback (undecidable)")
    ref = sample.decode(scored[0][2])
    alts = [(e, round(max(0.05, 0.45 - 0.05 * i), 2)) for i, (_p, _r, e) in enumerate(x for x in scored[1:] if sample.decode(x[2]) != ref)][:4]
    return Encoding(scored[0][2], round(0.5 * -scored[0][0], 2), method="letter check (charset-normalizer undecided)", alternatives=alts)


def _preferred(names: list[str]) -> str:
    return min(names, key=lambda n: PREFERRED.index(n) if n in PREFERRED else len(PREFERRED))


_RARE_PREFIXES = ("mac_", "hp_", "ptcp", "kz1048")
_RARE = {"cp037", "cp273", "cp424", "cp500", "cp875", "cp1026", "cp1140", "cp720", "cp737", "cp775", "cp855", "cp856", "cp857", "cp858",
         "cp860", "cp861", "cp862", "cp863", "cp864", "cp865", "cp869", "cp1006", "cp1125", "iso8859_10", "iso8859_14", "iso8859_16", "iso8859_3", "iso8859_4"}
_DOS = {"cp437", "cp850", "cp852", "cp866"}


def _tier(name: str) -> int:
    """0 for code pages real files use, 1 for DOS code pages, 2 for rare ones (Mac, EBCDIC, minor DOS/ISO pages)."""
    if name in _DOS:
        return 1
    if name in _RARE or name.startswith(_RARE_PREFIXES):
        return 2
    return 0


def sample_words(text: str, k: int = 6) -> list[str]:
    """Up to k distinct words holding non-ASCII letters: what to eyeball to confirm an encoding (Café, not Cafť)."""
    import re

    out: list[str] = []
    # Starts only where a run of letters starts: otherwise a long ASCII word (a base64 line) is rescanned from each letter.
    for w in re.findall(r"(?<![^\W\d_])[^\W\d_]*[^\x00-\x7f][^\W\d_]*", text):
        w = w.strip()
        if w and w not in out and any(ord(c) > 127 for c in w):
            out.append(w[:30])
            if len(out) >= k:
                break
    return out


_WESTERN = set("àáâãäåæçèéêëìíîïñòóôõöøùúûüÿßœšžƒÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜŸŒŠŽªº")
_CENTRAL = set("ąćęłńóśźżčďěňřšťůžáéíóúýäôĺľŕőűăâîșțşţéöüĄĆĘŁŃÓŚŹŻČĎĚŇŘŠŤŮŽÁÉÍÓÚÝÄÔĹĽŔŐŰĂÂÎȘȚŞŢÉÖÜ")
_BALTIC = set("āčēģīķļņšūžąęėįųõäöüĀČĒĢĪĶĻŅŠŪŽĄĘĖĮŲÕÄÖÜ")
_TURKISH = set("çğıöşüâîûÇĞİÖŞÜÂÎÛ")


def _script(text: str) -> str | None:
    """The script most non-ASCII letters of a text belong to (LATIN, CYRILLIC, ARABIC, ...), or None."""
    import unicodedata

    counts: dict[str, int] = {}
    for c in text:
        if ord(c) > 127 and c.isalpha():
            k = unicodedata.name(c, "?").split(" ")[0]
            counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.__getitem__) if counts else None


_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_ASCII_LOWER = re.compile(r"[a-z]")
_ASCII_UPPER = re.compile(r"[A-Z]")


def _plausibility(text: str) -> float:
    """How much a decoding looks like real text: non-ASCII characters that are letters of one coherent alphabet.
    Only the non-ASCII positions are visited (found at C speed), so a mostly-ASCII 16 KB sample costs little."""
    import unicodedata

    pos = [m.start() for m in _NON_ASCII.finditer(text) if text[m.start()] != "�"]
    if not pos:
        return 1.0
    chars = [text[i] for i in pos]
    letters = [c for c in chars if c.isalpha()]
    common_punct = sum(1 for c in chars if not c.isalpha() and (c in "€–—‘’“”…•«»°©®™£§·¿¡،؛؟٫٬־׳״" or "　" <= c <= "〿" or "＀" <= c <= "￯"))
    base = (len(letters) + common_punct) / len(chars)
    if not letters:
        return base * 0.5
    n = len(text)
    # a capital right after a small letter ("CafŽ", "моЯ") is what a wrong code page produces, not what people write
    midcaps = sum(1 for i in pos if i and text[i].isupper() and text[i - 1].islower())
    base *= max(0.0, 1 - 2 * midcaps / len(letters))
    # Letters of another script glued to ASCII letters ("reЗu", "ﾉlan", "re輹") are Western accents read in the wrong
    # code page: Cyrillic, Greek, Arabic or Hebrew words never hold Latin letters, and CJK characters come in runs.

    def ascii_letter(i: int) -> bool:
        return 0 <= i < n and text[i].isascii() and text[i].isalpha()

    def plain(i: int) -> bool:
        return not 0 <= i < n or text[i].isascii()

    foreign = glued = 0
    for i in pos:
        c = text[i]
        if ord(c) <= 0x24F or not c.isalpha():
            continue
        foreign += 1
        if ord(c) >= 0x2E80:  # CJK: alone among ASCII characters and touching a letter
            glued += plain(i - 1) and plain(i + 1) and (ascii_letter(i - 1) or ascii_letter(i + 1))
        else:
            glued += ascii_letter(i - 1) or ascii_letter(i + 1)
    if foreign:
        base *= max(0.0, 1 - 2 * glued / foreign)
    latin = [c for c in letters if "LATIN" in unicodedata.name(c, "")]
    if len(letters) >= 8 and len(latin) < 0.5 * len(letters):
        # Cyrillic or Greek written all in capitals while the ASCII around it is lower case: Hebrew or Arabic read in
        # koi8_r or cp1253 looks like that; people write in lower case
        upper = sum(1 for c in letters if c.isupper())
        if upper > 0.8 * len(letters) and len(_ASCII_LOWER.findall(text)) >= len(_ASCII_UPPER.findall(text)):
            base *= 0.6
    if len(latin) >= 0.5 * len(letters):
        fit = max(sum(1 for c in latin if c in S) for S in (_WESTERN, _CENTRAL, _BALTIC, _TURKISH)) / len(latin)
        return base * fit
    scripts: dict[str, int] = {}
    for c in letters:
        k = unicodedata.name(c, "?").split(" ")[0]
        if k in ("HIRAGANA", "KATAKANA", "HANGUL", "IDEOGRAPHIC", "FULLWIDTH", "HALFWIDTH", "BOPOMOFO"):
            k = "CJK"
        scripts[k] = scripts.get(k, 0) + 1
    top = max(scripts, key=scripts.__getitem__)
    if top == "GREEK":
        base *= _greek_fit(text)
    elif top == "CYRILLIC" and scripts[top] >= 12:
        # Hebrew read as cp1251 turns its common letters into rare ones: tav into "ъ" and shin into "щ"
        # (together under 4% of Bulgarian letters, under 1% of Russian ones)
        low = text.lower()
        if low.count("ъ") + low.count("щ") > 0.08 * scripts[top]:
            base *= 0.7
    return base * scripts[top] / len(letters)


def _greek_fit(text: str) -> float:
    """Hebrew read as cp1253 gives lower-case Greek-looking words, but with a final sigma inside words and almost no
    accented vowel, which modern Greek puts on most words."""
    import re

    words = re.findall("[\u03ac-\u03ce\u0390]+", text.lower())
    if len(words) < 3:
        return 1.0
    inner_sigma = sum(1 for w in words if "\u03c2" in w[:-1] or "\u03b0" in w)
    long_words = [w for w in words if len(w) >= 4]
    accented = sum(1 for w in long_words if any(c in "\u03ac\u03ad\u03ae\u03af\u03cc\u03cd\u03ce" for c in w))
    fit = max(0.0, 1 - 2 * inner_sigma / len(words))
    if len(long_words) >= 3 and accented < 0.15 * len(long_words):
        fit *= 0.7
    return fit


PREFERRED = [
    "utf_8", "cp1252", "cp1251", "cp1250", "cp932", "shift_jis", "gb18030", "gbk", "gb2312", "big5", "euc_kr", "cp949", "euc_jp",
    "cp1253", "cp1254", "cp1256", "cp1255", "cp1257", "koi8_r", "koi8_u", "iso8859_15", "latin_1", "iso8859_2", "mac_roman", "cp437", "cp850",
]


def newline_style(text: str) -> tuple[str, dict[str, int]]:
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    counts = {"LF": lf, "CRLF": crlf, "CR": cr}
    kinds = [k for k, v in counts.items() if v]
    if not kinds:
        return "none", counts
    if len(kinds) == 1:
        return kinds[0], counts
    return "mixed (" + ", ".join(f"{k} {counts[k]}" for k in kinds) + ")", counts
