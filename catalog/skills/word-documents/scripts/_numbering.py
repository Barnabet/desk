"""List numbering: which paragraphs are list items, and the label Word shows for each ('3.', 'b)', '1.2.4', '•')."""

from __future__ import annotations

from typing import Any

from _docx import NS, PPR, qn, sym_char, wattr
from _styles import Styles, flatten

_W = "{" + NS["w"] + "}"

BULLET_MAP = {"": "•", "": "▪", "": "➢", "": "✓", "": "❖", "": "◆", "": "◦", "": "→", "": "○", "": "♦", "": "-", "": "•", "": "➞", "": "⇨"}
ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
ORD_WORDS = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


class Level:
    __slots__ = ("fmt", "text", "start", "ppr", "rpr", "suff", "jc", "is_lgl", "restart", "pstyle")

    def __init__(self, el: Any) -> None:
        def val(tag: str, default: str | None = None) -> str | None:
            c = el.find(qn(tag))
            return c.get(_W + "val", default) if c is not None else default

        self.fmt = val("w:numFmt", "decimal") or "decimal"
        self.text = val("w:lvlText", "") or ""
        try:
            self.start = int(val("w:start", "1") or 1)
        except ValueError:
            self.start = 1
        self.ppr = flatten(el.find(qn("w:pPr")))
        self.rpr = flatten(el.find(qn("w:rPr")))
        self.suff = val("w:suff", "tab") or "tab"
        self.jc = val("w:lvlJc", "left") or "left"
        self.is_lgl = el.find(qn("w:isLgl")) is not None
        r = val("w:lvlRestart")
        self.restart = int(r) if r and r.isdigit() else None
        self.pstyle = val("w:pStyle")


class Numbering:
    """numbering.xml: abstract definitions, instances and level overrides."""

    def __init__(self, root: Any, styles: Styles) -> None:
        self.styles = styles
        self.abstract: dict[str, dict[int, Level]] = {}
        self.abstract_link: dict[str, str] = {}
        self.nums: dict[str, tuple[str, dict[int, Level], dict[int, int]]] = {}
        if root is None:
            return
        for an in root.findall(qn("w:abstractNum")):
            aid = an.get(_W + "abstractNumId") or ""
            self.abstract[aid] = {int(lv.get(_W + "ilvl") or 0): Level(lv) for lv in an.findall(qn("w:lvl"))}
            link = an.find(qn("w:numStyleLink"))
            if link is not None:
                self.abstract_link[aid] = link.get(_W + "val") or ""
        for num in root.findall(qn("w:num")):
            nid = num.get(_W + "numId") or ""
            a = num.find(qn("w:abstractNumId"))
            aid = a.get(_W + "val") if a is not None else ""
            lvls: dict[int, Level] = {}
            starts: dict[int, int] = {}
            for ov in num.findall(qn("w:lvlOverride")):
                il = int(ov.get(_W + "ilvl") or 0)
                so = ov.find(qn("w:startOverride"))
                if so is not None:
                    try:
                        starts[il] = int(so.get(_W + "val") or 0)
                    except ValueError:
                        pass
                lv = ov.find(qn("w:lvl"))
                if lv is not None:
                    lvls[il] = Level(lv)
            self.nums[nid] = (aid or "", lvls, starts)

    def _abstract_levels(self, aid: str, depth: int = 0) -> dict[int, Level]:
        if aid in self.abstract_link and depth < 5:
            # numStyleLink: the definition lives in the numbering style's num.
            sid = self.styles.find(self.abstract_link[aid])
            num_id, _ = self.styles.num_pr(sid) if sid else (None, None)
            if num_id and num_id in self.nums:
                return self._abstract_levels(self.nums[num_id][0], depth + 1)
        return self.abstract.get(aid, {})

    def level(self, num_id: str, ilvl: int) -> Level | None:
        entry = self.nums.get(num_id)
        if entry is None:
            return None
        aid, over, _ = entry
        if ilvl in over:
            return over[ilvl]
        return self._abstract_levels(aid).get(ilvl)

    def abstract_id(self, num_id: str) -> str:
        entry = self.nums.get(num_id)
        return entry[0] if entry else ""

    def num_props(self, p: Any) -> tuple[str, int] | None:
        """(numId, ilvl) for a list paragraph (direct numbering, else its style's), or None."""
        ppr = p.find(PPR)
        num_id = ilvl = None
        sid = None
        if ppr is not None:
            ps = ppr.find(qn("w:pStyle"))
            sid = ps.get(_W + "val") if ps is not None else None
            np_ = ppr.find(qn("w:numPr"))
            if np_ is not None:
                n = np_.find(qn("w:numId"))
                i = np_.find(qn("w:ilvl"))
                num_id = n.get(_W + "val") if n is not None else None
                ilvl = i.get(_W + "val") if i is not None else None
        if num_id is None or ilvl is None:
            s_num, s_lvl = self.styles.num_pr(sid)
            if num_id is None:
                num_id = s_num
            if ilvl is None and s_lvl is not None:
                ilvl = s_lvl
            if ilvl is None and num_id is not None and sid:
                # A style linked from a numbering level (w:pStyle in w:lvl) picks that level.
                for lvl_i, lv in self._levels_of(num_id).items():
                    if lv.pstyle == sid:
                        ilvl = str(lvl_i)
                        break
        if not num_id or num_id == "0" or num_id not in self.nums:
            return None
        try:
            lvl = max(0, min(8, int(ilvl or 0)))
        except ValueError:
            lvl = 0
        return num_id, lvl

    def _levels_of(self, num_id: str) -> dict[int, Level]:
        entry = self.nums.get(num_id)
        if not entry:
            return {}
        levels = dict(self._abstract_levels(entry[0]))
        levels.update(entry[1])
        return levels


class Counter:
    """Walks list paragraphs in document order and produces Word's labels."""

    def __init__(self, numbering: Numbering) -> None:
        self.n = numbering
        self.counts: dict[str, list[int | None]] = {}
        self.seen_nums: set[str] = set()

    def label(self, num_id: str, ilvl: int) -> tuple[str, Level | None]:
        lv = self.n.level(num_id, ilvl)
        if lv is None:
            return "", None
        key = self.n.abstract_id(num_id) or num_id
        counts = self.counts.setdefault(key, [None] * 9)
        starts = self.n.nums.get(num_id, ("", {}, {}))[2]
        if num_id not in self.seen_nums:
            self.seen_nums.add(num_id)
            # An instance with start overrides restarts its list.
            for il, st in starts.items():
                if 0 <= il < 9:
                    counts[il] = st - 1
                    for deeper in range(il + 1, 9):
                        counts[deeper] = None
        if counts[ilvl] is None:
            counts[ilvl] = self._start(num_id, ilvl)
        else:
            counts[ilvl] = counts[ilvl] + 1  # type: ignore[operator]
        for deeper in range(ilvl + 1, 9):
            dl = self.n.level(num_id, deeper)
            if dl is None or dl.restart is None or dl.restart <= ilvl + 1 or dl.restart == 0:
                counts[deeper] = None
        if lv.fmt == "bullet":
            return bullet_text(lv), lv
        text = lv.text
        for i in range(9):
            token = f"%{i + 1}"
            if token not in text:
                continue
            li = self.n.level(num_id, i)
            val = counts[i] if counts[i] is not None else (li.start if li else 1)
            fmt = "decimal" if (lv.is_lgl and i < ilvl) else (li.fmt if li else "decimal")
            text = text.replace(token, format_number(int(val or 0), fmt))
        return text, lv

    def _start(self, num_id: str, ilvl: int) -> int:
        lv = self.n.level(num_id, ilvl)
        return lv.start if lv else 1


def bullet_text(lv: Level) -> str:
    t = lv.text or "•"
    out = []
    for ch in t:
        if ch in BULLET_MAP:
            out.append(BULLET_MAP[ch])
        elif 0xF000 <= ord(ch) <= 0xF0FF:
            out.append("•")
        else:
            out.append(ch)
    s = "".join(out).strip()
    font = ((lv.rpr.get("rFonts") or {}).get("ascii") or "").lower()
    if s == "o" and "courier" in font:
        return "◦"
    if s == "§" and "wingdings" in font:
        return "▪"
    return s or "•"


def to_roman(n: int) -> str:
    if n <= 0:
        return str(n)
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for v, s in vals:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def to_letters(n: int) -> str:
    """Word's letter numbering: A..Z, then AA..ZZ, AAA… (repeated letters, not base 26)."""
    if n <= 0:
        return str(n)
    letter = chr(ord("A") + (n - 1) % 26)
    return letter * ((n - 1) // 26 + 1)


def cardinal(n: int) -> str:
    if n < 20:
        return ONES[n] if n > 0 else "zero"
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" " + cardinal(n % 100) if n % 100 else "")
    return str(n)


def ordinal_word(n: int) -> str:
    words = cardinal(n).split("-")
    last = words[-1].split(" ")
    w = last[-1]
    if w in ORD_WORDS:
        w = ORD_WORDS[w]
    elif w.endswith("y"):
        w = w[:-1] + "ieth"
    else:
        w += "th"
    last[-1] = w
    words[-1] = " ".join(last)
    return "-".join(words)


def format_number(n: int, fmt: str) -> str:
    if fmt in ("decimal", "decimalHalfWidth", "decimalFullWidth", "decimalFullWidth2"):
        return str(n)
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "upperRoman":
        return to_roman(n)
    if fmt == "lowerRoman":
        return to_roman(n).lower()
    if fmt == "upperLetter":
        return to_letters(n)
    if fmt == "lowerLetter":
        return to_letters(n).lower()
    if fmt == "ordinal":
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"
    if fmt == "cardinalText":
        return cardinal(n).capitalize()
    if fmt == "ordinalText":
        return ordinal_word(n).capitalize()
    if fmt == "none":
        return ""
    if fmt == "decimalEnclosedCircle" and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if fmt == "decimalEnclosedParen":
        return f"({n})"
    if fmt == "decimalEnclosedFullstop":
        return f"{n}."
    if fmt == "numberInDash":
        return f"- {n} -"
    if fmt == "chicago":
        marks = ["*", "†", "‡", "§"]
        return marks[(n - 1) % 4] * ((n - 1) // 4 + 1)
    if fmt == "hex":
        return format(n, "X")
    return str(n)


def symbol_text(el: Any) -> str:
    return sym_char(el) if el is not None and wattr(el, "char") else ""
