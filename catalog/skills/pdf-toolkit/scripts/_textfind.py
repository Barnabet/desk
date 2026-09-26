"""Finding text on a PDF page the way a reader sees it, for pdf_text.py --search and pdf_redact.py.

pdfium gives one string character per text-page char index. Its text differs from what a reader sees in three ways
that make a plain regex miss matches:
- a line break is "\\r\\n" (or a generated space), so "notice period" wrapped over two lines is "notice\\r\\nperiod";
- a word hyphenated at the end of a line comes back joined, with U+FFFE (or 0x02) where the hyphen was:
  "termi\\ufffenation", "legal@northwind\\ufffeexample.com";
- runs of spaces, tabs and no-break spaces vary.

A page is therefore searched in up to three *views*, each a normalised string that maps back to char indexes:
- "space": every whitespace run (line breaks included) is one space; a line-end hyphen stays a hyphen;
- "joined": a line-end hyphen is removed, so the hyphenated word reads whole ("termination");
- "hyphen": a real "-" followed by a line break joins without a space ("northwind-" + "example.com").
Matches from every view are merged by char span, so a target is found however the layout broke it.
"""

from __future__ import annotations

import re
import math
from bisect import bisect_right
from functools import lru_cache
from typing import Callable, Iterator

# A soft line-end hyphen (pdfium's U+FFFE / 0x02, or a soft hyphen U+00AD) with the break after it, a real hyphen
# followed by a line break, or any other run of whitespace.
_TOKEN = re.compile(r"[\ufffe\x02\xad](?:[ \t\x00]*(?:\r\n|\r|\n)[\s\x00]*)?|-[ \t\x00]*(?:\r\n|\r|\n)[\s\x00]*|[\s\x00]+")
_SOFT = "\ufffe\x02"


class View:
    """A normalised version of a page's text whose indexes map back to pdfium char indexes."""

    __slots__ = ("text", "vpos", "rpos", "rlen", "copy")

    def __init__(self, raw: str, mode: str) -> None:
        parts: list[str] = []
        vpos: list[int] = []
        rpos: list[int] = []
        rlen: list[int] = []
        copy: list[bool] = []
        v = 0
        last = 0

        def seg(text: str, r: int, n: int, is_copy: bool) -> None:
            nonlocal v
            parts.append(text)
            vpos.append(v)
            rpos.append(r)
            rlen.append(n)
            copy.append(is_copy)
            v += len(text)

        for m in _TOKEN.finditer(raw):
            s, e = m.span()
            if s > last:
                seg(raw[last:s], last, s - last, True)
            tok = m.group(0)
            first = tok[0]
            if first in _SOFT:
                rep = "" if mode == "joined" else "-"
            elif first == "\xad":
                rep = ""
            elif first == "-":
                rep = "-" if mode == "hyphen" else "" if mode == "joined" else "- "
            else:
                rep = " "
            seg(rep, s, e - s, False)
            last = e
        if last < len(raw):
            seg(raw[last:], last, len(raw) - last, True)
        self.text = "".join(parts)
        self.vpos, self.rpos, self.rlen, self.copy = vpos, rpos, rlen, copy

    def raw_span(self, vs: int, ve: int) -> tuple[int, int]:
        """The char-index span [start, end) behind view span [vs, ve)."""
        k = bisect_right(self.vpos, vs) - 1
        rs = self.rpos[k] + (vs - self.vpos[k]) if self.copy[k] else self.rpos[k]
        k2 = bisect_right(self.vpos, ve - 1) - 1
        re_ = self.rpos[k2] + (ve - 1 - self.vpos[k2]) + 1 if self.copy[k2] else self.rpos[k2] + self.rlen[k2]
        return rs, max(rs + 1, re_)


@lru_cache(maxsize=16)
def _view(raw: str, mode: str) -> View:
    """Views are reused while several patterns search the same page."""
    return View(raw, mode)


def view_modes(raw: str) -> list[str]:
    """The views worth building for this text (only "space" when nothing on the page was hyphenated)."""
    modes = ["space"]
    if any(c in raw for c in "\ufffe\x02\xad") or re.search(r"-[ \t]*[\r\n]", raw):
        modes.append("joined")
    if re.search(r"-[ \t]*[\r\n]", raw):
        modes.append("hyphen")
    return modes


_BRK = r"(?:[ \t\x00]*(?:\r\n|\r|\n)[\s\x00]*)"
_Q_SHY = re.compile(r"\xad" + _BRK + "?")
_Q_SOFT = re.compile(r"[\ufffe\x02]" + _BRK + "?")
_Q_HYPH = re.compile(r"-" + _BRK)
_Q_WS = re.compile(r"[\s\x00]+")


_HYPHEN_BREAK = re.compile(r"-[ \t]*[\r\n]")


def quick_views(text: str) -> list[str]:
    """The views' texts without index maps (cheap, C-speed): to skip pages that cannot match.

    `text` may be pdfium's raw text or pdf_text's cached text (which keeps pdfium's line-end hyphen mark).
    """
    base = _Q_SHY.sub("", text) if "\xad" in text else text
    soft = "\ufffe" in base or "\x02" in base
    space = _Q_WS.sub(" ", _Q_SOFT.sub("-", base) if soft else base)
    if not soft and not _HYPHEN_BREAK.search(base):
        return [space]
    joined = _Q_WS.sub(" ", _Q_HYPH.sub("", _Q_SOFT.sub("", base)))
    hyphen = _Q_WS.sub(" ", _Q_HYPH.sub("-", _Q_SOFT.sub("-", base)))
    return [space, joined, hyphen]


def could_match(text: str, rx: re.Pattern[str]) -> bool:
    return any(rx.search(v) for v in quick_views(text))


class Hit:
    """One match: pdfium char span [start, end), the normalised matched text, and its context on demand."""

    __slots__ = ("start", "end", "text", "_view", "_vs", "_ve")

    def __init__(self, start: int, end: int, text: str, view: View, vs: int, ve: int) -> None:
        self.start, self.end, self.text = start, end, text
        self._view, self._vs, self._ve = view, vs, ve

    def context(self, n: int) -> str:
        t = self._view.text
        return t[max(0, self._vs - n) : self._ve + n].strip()


def find_spans(raw: str, rx: re.Pattern[str], validate: Callable[[str], bool] | None = None) -> Iterator[Hit]:
    """Every match on a page, one per char span, in the order the views find them.

    The matched text is the normalised form ("termination", "notice period"); spans found in several views are
    reported once. Leading and trailing whitespace is trimmed from each match.
    """
    if not could_match(raw, rx):  # most pages: one C-speed pass, no index maps
        return
    taken: list[tuple[int, int]] = []
    for mode in view_modes(raw):
        view = _view(raw, mode)
        text = view.text
        for m in rx.finditer(text):
            vs, ve = m.span()
            while vs < ve and text[vs] == " ":
                vs += 1
            while ve > vs and text[ve - 1] == " ":
                ve -= 1
            if ve <= vs:
                continue
            found = text[vs:ve]
            if validate is not None and not validate(found):
                continue
            rs, re_ = view.raw_span(vs, ve)
            if any(rs < e and s < re_ for s, e in taken):
                continue
            taken.append((rs, re_))
            yield Hit(rs, re_, found, view, vs, ve)


# ── matches that run over a page break ──────────────────────────────────

_LINE = re.compile(r"[^\r\n]+")
_PAGENUM = re.compile(r"^\W*(?:page|p\.|seite|pagina|página)?\s*(?:#|[ivxlcdm]+)\s*(?:(?:/|of|von|sur|de|di|-)\s*#)?\W*$", re.I)
WINDOW = 400  # characters on each side of a page break that a match may span
# "12 / 40", "12 of 40", "Page 12" run into the end of the last line (pdfium joins it after a hyphenated word).
_RUN_IN_NUMBER = re.compile(r"(?<=[\ufffe\x02\s])(?:(?:page|p\.|seite)\s*)?\d+\s*(?:/|of|von|sur|de)\s*\d+\s*$|(?<=[\ufffe\x02\s])(?:page|seite)\s+\d+\s*$", re.I)


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line)).strip()


def _edge_spans(raw: str) -> list[tuple[int, int, str]]:
    """The first two and last two non-blank lines of a page: (start, end, normalised text)."""
    spans = [m.span() for m in _LINE.finditer(raw)]
    out: list[tuple[int, int, str]] = []
    idx = list(range(len(spans)))
    head = [i for i in idx if raw[spans[i][0] : spans[i][1]].strip()][:2]
    tail = [i for i in reversed(idx) if raw[spans[i][0] : spans[i][1]].strip()][:2]
    for i in sorted(set(head) | set(tail)):
        a, b = spans[i]
        out.append((a, b, _norm_line(raw[a:b])))
    return out


def edge_lines(texts: dict[int, str]) -> tuple[set[str], set[str]]:
    """Running headers and footers: normalised lines (digits as #) found at the top or bottom of many pages."""
    from collections import Counter

    firsts: Counter[str] = Counter()
    lasts: Counter[str] = Counter()
    for raw in texts.values():
        ls = _edge_spans(raw)
        for x in ls[:2]:
            firsts[x[2]] += 1
        for x in ls[-2:]:
            lasts[x[2]] += 1
    need = max(2, math.ceil(0.3 * len(texts)))
    return {k for k, v in firsts.items() if v >= need}, {k for k, v in lasts.items() if v >= need}


def body_bounds(raws: dict[int, str], edges: tuple[set[str], set[str]] | None = None) -> dict[int, tuple[int, int]]:
    """Per page, the char range [start, end) of its body: without running headers and footers (see edge_lines;
    pass `edges` found on the whole document when `raws` holds only a few pages) and bare page numbers, so text
    can be joined across pages."""
    heads, feet = edges if edges is not None else edge_lines(raws)
    suffixes = [_suffix_rx(f) for f in sorted(feet, key=len, reverse=True) if "#" in f and len(f) >= 3] + [_RUN_IN_NUMBER]
    out: dict[int, tuple[int, int]] = {}
    for n, raw in raws.items():
        ls = _edge_spans(raw)
        start, end = 0, len(raw)
        i = 0
        while i < min(2, len(ls)) and (ls[i][2] in heads or _PAGENUM.match(ls[i][2])):
            start = ls[i][1]
            i += 1
        j = len(ls) - 1
        while j >= i and j >= len(ls) - 2 and (ls[j][2] in feet or _PAGENUM.match(ls[j][2])):
            end = ls[j][0]
            j -= 1
        if j == len(ls) - 1 and j >= i:
            # pdfium sometimes runs the footer into the last line (after a hyphenated word): cut it off the end.
            line = raw[ls[j][0] : ls[j][1]]
            for rx in suffixes:
                m = rx.search(line)
                if m and m.start() > 0:
                    end = ls[j][0] + m.start()
                    break
        out[n] = (start, max(start, end))
    return out


def _suffix_rx(norm: str) -> re.Pattern[str]:
    """A normalised footer line as a regex that finds it at the end of a raw line."""
    body = r"\s*".join(r"\d+" if tok == "#" else re.escape(tok) for tok in re.findall(r"#|[^#\s]+", norm))
    return re.compile(r"(?<![\w])" + body + r"\s*$")


class CrossHit:
    """A match that starts on page `first` (chars [s1, e1)) and ends on page `first + 1` (chars [s2, e2))."""

    __slots__ = ("first", "s1", "e1", "s2", "e2", "text", "_hit")

    def __init__(self, first: int, s1: int, e1: int, s2: int, e2: int, hit: Hit) -> None:
        self.first, self.s1, self.e1, self.s2, self.e2 = first, s1, e1, s2, e2
        self.text, self._hit = hit.text, hit

    def context(self, n: int) -> str:
        return self._hit.context(n)


def cross_page(raws: dict[int, str], rx: re.Pattern[str], validate: Callable[[str], bool] | None = None, bounds: dict[int, tuple[int, int]] | None = None) -> list[CrossHit]:
    """Matches that run from the end of one page's body onto the start of the next page's body (pages in `raws`)."""
    bounds = bounds if bounds is not None else body_bounds(raws)
    out: list[CrossHit] = []
    for n in sorted(raws):
        m = n + 1
        if m not in raws:
            continue
        a0, a1 = bounds[n]
        b0, b1 = bounds[m]
        ts = max(a0, a1 - WINDOW)
        tail = raws[n][ts:a1]
        head = raws[m][b0 : min(b1, b0 + WINDOW)]
        if not tail.strip() or not head.strip():
            continue
        joined = tail + "\n" + head
        # Only when the joined text has more matches than its two halves can one run over the break.
        vt, vh, vj = quick_views(tail), quick_views(head), quick_views(joined)
        if not any(len(rx.findall(j)) > len(rx.findall(vt[min(k, len(vt) - 1)])) + len(rx.findall(vh[min(k, len(vh) - 1)])) for k, j in enumerate(vj)):
            continue
        cut = len(tail)
        for h in find_spans(joined, rx, validate):
            if h.start < cut and h.end > cut + 1:
                out.append(CrossHit(n, ts + h.start, a1, b0, b0 + (h.end - cut - 1), h))
    return out
