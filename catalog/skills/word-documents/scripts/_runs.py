"""Text inside paragraphs as Word stores it: split across runs. Maps characters to the XML nodes holding them, so
text can be found, replaced, formatted or commented across run boundaries while keeping the formatting of the
first run of each match."""

from __future__ import annotations

import copy
import re
from typing import Any, Iterator

from _docx import BR, CR, CUSTOMXML, DIR, BDO, FLDSIMPLE, HYPERLINK, INS, MOVETO, NBH, P, R, RPR, SDT, SDTCONTENT, SMARTTAG, SYM, T, TAB, TXBX, new_el, qn, set_text, sym_char

_CONTAINERS = {HYPERLINK, SMARTTAG, CUSTOMXML, FLDSIMPLE, DIR, BDO, INS, MOVETO}


class Atom:
    """One piece of visible text: part of a w:t, or a single tab/break/symbol character."""

    __slots__ = ("run", "node", "start", "length", "kind", "offset")

    def __init__(self, run: Any, node: Any, start: int, length: int, kind: str, offset: int = 0) -> None:
        self.run = run
        self.node = node
        self.start = start  # position in the paragraph text
        self.length = length
        self.kind = kind  # 't', 'tab', 'br', 'sym', 'nbh'
        self.offset = offset  # offset inside node.text for 't'


def paragraph_runs(p: Any) -> Iterator[Any]:
    """Visible runs of a paragraph in order (tracked deletions and field codes excluded, insertions included)."""

    def walk(el: Any) -> Iterator[Any]:
        for c in el:
            tag = c.tag
            if tag == R:
                yield c
            elif tag in _CONTAINERS:
                yield from walk(c)
            elif tag == SDT:
                content = c.find(SDTCONTENT)
                if content is not None:
                    yield from walk(content)

    yield from walk(p)


class CharMap:
    """The visible text of a paragraph and the atoms it is made of."""

    def __init__(self, p: Any) -> None:
        self.p = p
        self.atoms: list[Atom] = []
        parts: list[str] = []
        pos = 0
        field_depth = 0
        in_code: list[bool] = []
        for r in paragraph_runs(p):
            for c in r:
                tag = c.tag
                if tag == qn("w:fldChar"):
                    t = c.get(qn("w:fldCharType"))
                    if t == "begin":
                        in_code.append(True)
                    elif t == "separate" and in_code:
                        in_code[-1] = False
                    elif t == "end" and in_code:
                        in_code.pop()
                    continue
                if in_code and in_code[-1]:
                    continue
                if tag == T:
                    text = c.text or ""
                    if text:
                        self.atoms.append(Atom(r, c, pos, len(text), "t"))
                        parts.append(text)
                        pos += len(text)
                elif tag in (TAB, BR, CR, NBH, SYM):
                    ch = {TAB: "\t", BR: "\n", CR: "\n", NBH: "\u2011"}.get(tag) or sym_char(c) or "\ufffc"
                    self.atoms.append(Atom(r, c, pos, 1, {TAB: "tab", BR: "br", CR: "br", NBH: "nbh"}.get(tag, "sym")))
                    parts.append(ch)
                    pos += 1
        self.text = "".join(parts)

    def atoms_in(self, start: int, end: int) -> list[Atom]:
        return [a for a in self.atoms if a.start < end and a.start + a.length > start]


def iter_story_paragraphs(root: Any, scope: str) -> Iterator[Any]:
    """Paragraphs of a story, limited to one scope for the body ('body' = outside tables and text boxes)."""
    for p in root.iter(P):
        if scope == "all_in_story":
            yield p
            continue
        in_tbl = _has_ancestor(p, qn("w:tbl"), root)
        in_box = _has_ancestor(p, TXBX, root)
        if scope == "body" and not in_tbl and not in_box:
            yield p
        elif scope == "tables" and in_tbl and not in_box:
            yield p
        elif scope == "textboxes" and in_box:
            yield p


def _has_ancestor(el: Any, tag: str, stop: Any) -> bool:
    a = el.getparent()
    while a is not None and a is not stop:
        if a.tag == tag:
            return True
        a = a.getparent()
    return False


# ── matching ────────────────────────────────────────────────────────────


#: Seconds one search may take. Python's re cannot be interrupted, so a pathological pattern like (a+)+$ would
#: hang the tool; the regex module (the same syntax) stops it.
REGEX_TIMEOUT = 3.0


class SafePattern:
    """A compiled pattern whose searches give up after REGEX_TIMEOUT seconds with a clear error."""

    def __init__(self, pat: Any, source: str) -> None:
        self._pat = pat
        self.pattern = source

    def _fail(self) -> None:
        from _common import SkillError

        raise SkillError(f"the pattern {self.pattern!r} took over {REGEX_TIMEOUT:.0f} s on one paragraph (catastrophic backtracking, as in (a+)+); simplify it, e.g. drop nested repeats")

    def finditer(self, text: str) -> list[Any]:
        try:
            return list(self._pat.finditer(text, timeout=REGEX_TIMEOUT))
        except TimeoutError:
            self._fail()
            return []

    def search(self, text: str) -> Any:
        try:
            return self._pat.search(text, timeout=REGEX_TIMEOUT)
        except TimeoutError:
            self._fail()
            return None


def safe_regex(pattern: str, flags: int = 0, what: str = "regex") -> SafePattern:
    """Compiles a user's regular expression (Python re syntax) for searching with a time limit."""
    import regex

    from _common import UsageError

    rflags = regex.V0 | (regex.IGNORECASE if flags & re.IGNORECASE else 0) | (regex.MULTILINE if flags & re.MULTILINE else 0) | (regex.DOTALL if flags & re.DOTALL else 0)
    try:
        re.compile(pattern, flags)  # the syntax agents know; also catches what regex would read differently
        return SafePattern(regex.compile(pattern, rflags), pattern)
    except (re.error, regex.error) as e:
        raise UsageError(f"bad {what} {pattern!r}: {e}") from e


def compile_pattern(find: str | None, regex: str | None, match_case: bool = True, whole_word: bool = False) -> SafePattern:
    if regex is not None:
        pat = regex
    elif find is not None:
        pat = re.escape(find)
        # Straight and curly quotes, and the various spaces and hyphens, match each other.
        pat = pat.replace("'", "['’‘]").replace('"', '["“”]').replace("\\ ", "[ \u00a0]").replace("\\-", "[-\u2011\u2013]")
    else:
        from _common import UsageError

        raise UsageError("give 'find' (literal text) or 'regex'")
    if whole_word:
        pat = rf"(?<!\w)(?:{pat})(?!\w)"
    return safe_regex(pat, 0 if match_case else re.IGNORECASE)


def expand_template(m: re.Match[str], template: str, is_regex: bool) -> str:
    if not is_regex:
        return template
    t = re.sub(r"\$(\d+)", r"\\g<\1>", template)
    t = re.sub(r"\$\{(\w+)\}", r"\\g<\1>", t)
    return m.expand(t)


# ── editing ─────────────────────────────────────────────────────────────


def _text_nodes(text: str) -> list[Any]:
    """New run children for text: w:t, with w:tab for tabs and w:br for line breaks."""
    out = []
    for part in re.split(r"(\t|\n)", text):
        if part == "":
            continue
        if part == "\t":
            out.append(new_el("w:tab"))
        elif part == "\n":
            out.append(new_el("w:br"))
        else:
            t = new_el("w:t")
            set_text(t, part)
            out.append(t)
    return out


def replace_span(cm: CharMap, start: int, end: int, replacement: str) -> None:
    """Replaces text[start:end] in place. The replacement takes the formatting of the run holding the first character."""
    atoms = cm.atoms_in(start, end)
    if not atoms:
        return
    first = atoms[0]
    # insertion point: after the prefix of the first atom
    new_nodes = _text_nodes(replacement)
    if first.kind == "t":
        node = first.node
        text = node.text or ""
        cut = start - first.start
        head = text[:cut]
        tail_in_first = text[end - first.start :] if end < first.start + first.length else ""
        set_text(node, head)
        anchor = node
        for n in new_nodes:
            anchor.addnext(n)
            anchor = n
        if tail_in_first:
            t = new_el("w:t")
            set_text(t, tail_in_first)
            anchor.addnext(t)
        if not head:
            node.getparent().remove(node)
    else:
        anchor = first.node
        for n in new_nodes:
            anchor.addprevious(n)
        if first.start >= start and first.start + first.length <= end:
            anchor.getparent().remove(anchor)
    for a in atoms[1:]:
        if a.kind == "t":
            node = a.node
            text = node.text or ""
            keep = text[max(0, end - a.start) :] if a.start + a.length > end else ""
            if keep:
                set_text(node, keep)
            else:
                node.getparent().remove(node)
        else:
            if a.node.getparent() is not None:
                a.node.getparent().remove(a.node)
    for a in atoms:
        _drop_if_empty(a.run)


def _drop_if_empty(r: Any) -> None:
    if r.getparent() is None:
        return
    if all(c.tag == RPR for c in r):
        parent = r.getparent()
        parent.remove(r)
        if parent.tag in (HYPERLINK, INS, SMARTTAG) and len(parent) == 0 and parent.getparent() is not None:
            parent.getparent().remove(parent)


def split_run(r: Any, offset: int) -> Any:
    """Splits a run at a character offset of its visible text; returns the new second run (or None)."""
    pos = 0
    children = [c for c in r if c.tag != RPR]
    for i, c in enumerate(children):
        if c.tag == T:
            n = len(c.text or "")
        elif c.tag in (TAB, BR, CR, NBH, SYM):
            n = 1
        else:
            n = 0
        if pos == offset and n > 0:
            idx = i
            break
        if pos < offset < pos + n and c.tag == T:
            text = c.text or ""
            k = offset - pos
            set_text(c, text[:k])
            t2 = new_el("w:t")
            set_text(t2, text[k:])
            c.addnext(t2)
            children = [x for x in r if x.tag != RPR]
            idx = children.index(t2)
            break
        pos += n
    else:
        return None
    if idx == 0:
        return None
    new = new_el("w:r")
    rpr = r.find(RPR)
    if rpr is not None:
        new.append(copy.deepcopy(rpr))
    for c in children[idx:]:
        new.append(c)
    r.addnext(new)
    return new


def isolate(p: Any, start: int, end: int) -> list[Any]:
    """Splits runs so that text[start:end] is exactly covered by whole runs; returns those runs in order."""
    for pos in (end, start):
        cm = CharMap(p)
        for a in cm.atoms:
            if a.start < pos < a.start + a.length or (a.start == pos and a.run is not None):
                run_atoms = [x for x in cm.atoms if x.run is a.run]
                run_start = run_atoms[0].start
                if pos > run_start:
                    split_run(a.run, pos - run_start)
                break
    cm = CharMap(p)
    runs: list[Any] = []
    for a in cm.atoms_in(start, end):
        if a.run not in runs:
            runs.append(a.run)
    return runs


RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"]
HIGHLIGHT_NAMES = {"yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen", "darkMagenta", "darkRed", "darkYellow", "darkGray", "lightGray", "black", "white", "none"}


def set_rpr(rpr: Any, name: str, attrs: dict[str, str] | None) -> None:
    """Sets (attrs dict) or removes (None) a run property, keeping the schema order."""
    tag = qn("w:" + name)
    for old in rpr.findall(tag):
        rpr.remove(old)
    if attrs is None:
        return
    el = new_el("w:" + name)
    for k, v in attrs.items():
        el.set(qn("w:" + k), str(v))
    idx = RPR_ORDER.index(name) if name in RPR_ORDER else len(RPR_ORDER)
    for i, c in enumerate(rpr):
        cname = c.tag.split("}")[-1]
        if cname in RPR_ORDER and RPR_ORDER.index(cname) > idx or cname == "rPrChange":
            c.addprevious(el)
            return
    rpr.append(el)


def apply_format(r: Any, fmt: dict[str, Any], styles: Any = None) -> None:
    """Applies bold/italic/underline/strike/color/highlight/size/font/style/caps/superscript/subscript to a run."""
    from _docx import rpr_of

    rpr = rpr_of(r)
    for key, prop in (("bold", "b"), ("italic", "i"), ("strike", "strike"), ("caps", "caps"), ("small_caps", "smallCaps"), ("hidden", "vanish")):
        if key in fmt:
            set_rpr(rpr, prop, {} if fmt[key] else {"val": "0"})
            if prop in ("b", "i"):
                set_rpr(rpr, prop + "Cs", {} if fmt[key] else {"val": "0"})
    if "underline" in fmt:
        u = fmt["underline"]
        set_rpr(rpr, "u", {"val": "single" if u is True else ("none" if not u else str(u))})
    if "color" in fmt:
        c = str(fmt["color"]).lstrip("#")
        set_rpr(rpr, "color", {"val": c.upper()} if c else None)
    if "highlight" in fmt:
        h = fmt["highlight"]
        if h and h not in HIGHLIGHT_NAMES:
            from _common import UsageError

            raise UsageError(f"highlight must be one of {', '.join(sorted(HIGHLIGHT_NAMES))}")
        set_rpr(rpr, "highlight", {"val": h} if h else None)
    if "size" in fmt:
        v = str(int(round(float(fmt["size"]) * 2)))
        set_rpr(rpr, "sz", {"val": v})
        set_rpr(rpr, "szCs", {"val": v})
    if "font" in fmt:
        f = fmt["font"]
        set_rpr(rpr, "rFonts", {"ascii": f, "hAnsi": f, "eastAsia": f, "cs": f})
    if fmt.get("superscript"):
        set_rpr(rpr, "vertAlign", {"val": "superscript"})
    if fmt.get("subscript"):
        set_rpr(rpr, "vertAlign", {"val": "subscript"})
    if "style" in fmt and styles is not None:
        sid = styles.find(fmt["style"], "character")
        if not sid:
            from _common import SkillError

            raise SkillError(f"character style '{fmt['style']}' is not defined in this document")
        set_rpr(rpr, "rStyle", {"val": sid})
    if "shading" in fmt:
        c = str(fmt["shading"]).lstrip("#")
        set_rpr(rpr, "shd", {"val": "clear", "color": "auto", "fill": c.upper()} if c else None)
