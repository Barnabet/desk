#!/usr/bin/env python3
"""Check Markdown files: heading hierarchy and duplicates, broken relative links, images and #anchors (across
files too), empty links, undefined references and footnotes, YAML/TOML front matter, table column counts,
unclosed code fences and whitespace. Prints problems with line numbers, word counts and reading time. Generates
or refreshes a table of contents between <!-- toc --> and <!-- tocstop --> markers. --fix writes a corrected
copy to a NEW file: heading spaces, anchor links that only differ in spelling, short table rows, unclosed fences,
trailing spaces, extra blank lines, the TOC. Remote URLs are not fetched.

Examples:
  python3 scripts/md_check.py README.md
  python3 scripts/md_check.py docs/                          # every .md below, with cross-file anchors
  python3 scripts/md_check.py README.md --toc                # print a TOC for the file
  python3 scripts/md_check.py README.md --fix README.fixed.md
  python3 scripts/md_check.py README.md --fix README.fixed.md --add-toc --toc-depth 2
  python3 scripts/md_check.py notes.md --stats --format json
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from _common import SkillError, UsageError, add_format, emit, output_path, parser, run_main

MD_EXTS = {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx", ".qmd", ".rmd"}
TOC_MARKERS = [("<!-- toc -->", "<!-- tocstop -->"), ("<!-- TOC -->", "<!-- /TOC -->"), ("<!-- toc -->", "<!-- /toc -->"), ("<!-- START doctoc -->", "<!-- END doctoc -->")]
LEVELS = {"error": 0, "warning": 1, "info": 2}
WPM = 230

INLINE_LINK = re.compile(r"(!?)\[((?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*)\]\(\s*(<[^>\n]*>|(?:[^()\s\\]|\\.|\((?:[^()\s\\]|\\.)*\))*)(?:\s+(\"[^\"\n]*\"|'[^'\n]*'|\([^)\n]*\)))?\s*\)")
REF_LINK = re.compile(r"(!?)\[((?:[^\[\]\\]|\\.)+)\]\[((?:[^\[\]\\]|\\.)*)\]")
REF_DEF = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.)+)\]:\s*(<[^>]*>|\S+)")
REF_DEF_OPEN = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.)+)\]:\s*$")  # the destination is on the next line
FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\](?!:)")
FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:", re.M)
HTML_ATTR = re.compile(r"<(a|img|source|video|audio)\b[^>]*?\s(href|src)\s*=\s*[\"']([^\"']+)[\"']", re.I)
HTML_ID = re.compile(r"<[a-zA-Z][^>]*?\s(?:id|name)\s*=\s*[\"']([^\"']+)[\"']")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
ATX_NOSPACE = re.compile(r"^ {0,3}(#{1,6})([^#\s].*)$")
FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")  # any indent: fences inside list items are code too
HEADER_ATTRS = re.compile(r"\s*\{\s*[#.][^}]*\}\s*$|\s*\{\s*-\s*\}\s*$")  # pandoc header attributes {#id .class k=v}
TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


# ── slugs ───────────────────────────────────────────────────────────────


def gh_slug(text: str) -> str:
    """GitHub's heading anchor: lower case, punctuation removed, spaces to hyphens."""
    t = strip_md(text).lower()
    t = "".join(ch for ch in t if ch in " -_" or unicodedata.category(ch)[0] in "LNM")
    return t.replace(" ", "-")


def pandoc_slug(text: str) -> str:
    t = strip_md(text).lower()
    t = "".join(ch for ch in t if ch in " -_." or unicodedata.category(ch)[0] in "LNM")
    t = re.sub(r"\s+", "-", t.strip())
    t = re.sub(r"^[^a-z\u00c0-\uffff]+", "", t)
    return t or "section"


def strip_md(text: str) -> str:
    t = HEADER_ATTRS.sub("", text)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\*\*|\*|~~|`", "", t)
    t = re.sub(r"(?<![^\W_])_+|_+(?![^\W_])", "", t)  # emphasis underscores only: tex_math keeps its own
    return t.strip()


def norm_label(s: str) -> str:
    """A reference label as CommonMark matches it: case-folded, inner whitespace collapsed."""
    return re.sub(r"\s+", " ", s.strip()).casefold()


def norm_anchor(s: str) -> str:
    return re.sub(r"[^a-z0-9\u00c0-\uffff]+", "", unquote(s).lower())


# ── parsing ─────────────────────────────────────────────────────────────


class Doc:
    """A Markdown file split into lines with the code, front matter and headings located."""

    def __init__(self, path: Path) -> None:
        self.path = path
        raw = path.read_bytes()
        from _inputs import looks_binary

        if looks_binary(raw):
            raise SkillError(f"{path.name} is binary data, not a Markdown file (it holds NUL or control bytes)")
        try:
            self.text = raw.decode("utf-8-sig")
            self.encoding_ok = True
        except UnicodeDecodeError:
            self.text = raw.decode("cp1252", "replace")
            self.encoding_ok = False
        self.crlf = "\r\n" in self.text[:10000]
        self.lines = self.text.replace("\r\n", "\n").split("\n")
        self.front: tuple[int, int, str] | None = None  # (start, end, kind) line indexes
        self.code = [False] * len(self.lines)
        self.fences: list[tuple[int, int | None, str]] = []  # (open, close or None, info)
        self._scan()
        self.headings = self._headings()
        self._anchors: set[str] | None = None

    def _scan(self) -> None:
        lines = self.lines
        start = 0
        if lines and lines[0].rstrip() in ("---", "+++"):
            closer = {"---": ("---", "..."), "+++": ("+++",)}[lines[0].rstrip()]
            for j in range(1, len(lines)):
                if lines[j].rstrip() in closer:
                    self.front = (0, j, "yaml" if lines[0].rstrip() == "---" else "toml")
                    start = j + 1
                    break
        i = start
        n = len(lines)
        while i < n:
            m = FENCE.match(lines[i])
            if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
                fence = m.group(2)
                j = i + 1
                close = None
                while j < n:
                    m2 = FENCE.match(lines[j])
                    if m2 and m2.group(2)[0] == fence[0] and len(m2.group(2)) >= len(fence) and not m2.group(3).strip():
                        close = j
                        break
                    j += 1
                self.fences.append((i, close, m.group(3).strip()))
                end = close if close is not None else n - 1
                for k in range(i, end + 1):
                    self.code[k] = True
                i = end + 1
                continue
            # Indented code (4 spaces) after a blank line, outside lists: treated as code.
            if lines[i].startswith(("    ", "\t")) and i > 0 and not lines[i - 1].strip() and not self._in_list(i):
                k = i
                while k < n and (lines[k].startswith(("    ", "\t")) or not lines[k].strip()):
                    self.code[k] = True
                    k += 1
                i = k
                continue
            i += 1
        if self.front:
            for k in range(self.front[0], self.front[1] + 1):
                self.code[k] = True

    def _in_list(self, i: int) -> bool:
        for k in range(i - 1, max(-1, i - 40), -1):
            s = self.lines[k]
            if re.match(r"^\s*([-*+]|\d+[.)])\s", s):
                return True
            if s.strip() and not s.startswith((" ", "\t")):
                return False
        return False

    def _headings(self) -> list[dict[str, Any]]:
        out = []
        for i, ln in enumerate(self.lines):
            if self.code[i]:
                continue
            m = ATX.match(ln)
            if m:
                text = (m.group(2) or "").strip()
                explicit = re.search(r"\{[^}]*?#([^}\s]+)[^}]*\}\s*$", text)
                out.append({"line": i + 1, "level": len(m.group(1)), "text": strip_md(text), "raw": text, "id": explicit.group(1) if explicit else None})
                continue
            if i > 0 and re.match(r"^ {0,3}(=+|-+)\s*$", ln) and self.lines[i - 1].strip() and not self.code[i - 1]:
                prev = self.lines[i - 1]
                # "Text" followed by === or --- is a setext heading (not after a list item, quote, heading or table row).
                if not re.match(r"^\s*([-*+>|]\s|\d+[.)]\s|#|<)", prev) and "|" not in prev and not ATX.match(prev):
                    out.append({"line": i, "level": 1 if ln.strip()[0] == "=" else 2, "text": strip_md(prev.strip()), "raw": prev.strip(), "id": None, "setext": True})
        out.sort(key=lambda h: h["line"])
        return out

    def anchors(self) -> set[str]:
        if self._anchors is None:
            a: set[str] = set()
            seen: dict[str, int] = {}
            seen_p: dict[str, int] = {}
            for h in self.headings:
                base = gh_slug(h["raw"])
                k = seen.get(base, 0)
                a.add(base if k == 0 else f"{base}-{k}")
                seen[base] = k + 1
                pbase = pandoc_slug(h["raw"])  # pandoc numbers repeated ids the same way (#options, #options-1)
                k = seen_p.get(pbase, 0)
                a.add(pbase if k == 0 else f"{pbase}-{k}")
                seen_p[pbase] = k + 1
                if h["id"]:
                    a.add(h["id"])
            for i, ln in enumerate(self.lines):
                if "id=" in ln or "name=" in ln:
                    for m in HTML_ID.finditer(ln):
                        a.add(m.group(1))
                for m in re.finditer(r"\{(?:[^}]*\s)?#([\w:.-]+)", ln):
                    a.add(m.group(1))
            for m in FOOTNOTE_DEF.finditer(self.text):
                a.add("fn-" + m.group(1))
            self._anchors = a
        return self._anchors

    def prose_lines(self) -> list[tuple[int, str, str]]:
        """(line number, text with inline code blanked to spaces of the same length, original text) for lines
        outside code and front matter; positions in both texts match."""
        out = []
        for i, ln in enumerate(self.lines):
            if self.code[i]:
                continue
            masked = re.sub(r"(`+)(.+?)\1", lambda m: " " * len(m.group(0)), ln) if "`" in ln else ln
            out.append((i + 1, masked, ln))
        return out

    def heading_labels(self) -> set[str]:
        """Heading texts as reference labels: pandoc resolves [Heading text] and [text][Heading text] to headings."""
        return {norm_label(HEADER_ATTRS.sub("", h["raw"])) for h in self.headings}


# ── checks ──────────────────────────────────────────────────────────────


class Checker:
    def __init__(self, docs_by_path: dict[Path, Doc]) -> None:
        self.docs = docs_by_path

    def doc_for(self, p: Path) -> Doc | None:
        p = p.resolve()
        if p in self.docs:
            return self.docs[p]
        if p.suffix.lower() in MD_EXTS and p.is_file():
            try:
                d = Doc(p)
            except OSError:
                return None
            self.docs[p] = d
            return d
        return None

    def check(self, doc: Doc) -> list[dict[str, Any]]:
        probs: list[dict[str, Any]] = []

        def add(line: int, level: str, rule: str, msg: str, fixable: bool = False) -> None:
            probs.append({"line": line, "level": level, "rule": rule, "message": msg, **({"fixable": True} if fixable else {})})

        if not doc.encoding_ok:
            add(1, "warning", "encoding", "not valid UTF-8 (read as Windows-1252)")
        # front matter
        if doc.front:
            s, e, kind = doc.front
            body = "\n".join(doc.lines[s + 1 : e])
            if kind == "yaml":
                import yaml

                try:
                    data = yaml.safe_load(body)
                    if data is not None and not isinstance(data, dict):
                        add(s + 1, "error", "front-matter", "YAML front matter is not a key: value mapping")
                except yaml.YAMLError as ex:
                    mark = getattr(ex, "problem_mark", None)
                    line = s + 2 + (mark.line if mark else 0)
                    add(line, "error", "front-matter", f"invalid YAML: {getattr(ex, 'problem', None) or str(ex).splitlines()[0]}")
            else:
                import tomllib

                try:
                    tomllib.loads(body)
                except tomllib.TOMLDecodeError as ex:
                    add(s + 1, "error", "front-matter", f"invalid TOML: {ex}")
        elif doc.lines and doc.lines[0].rstrip() == "---" and len(doc.lines) > 1 and re.match(r"^\w[\w-]*\s*:", doc.lines[1]):
            add(1, "error", "front-matter", "front matter is not closed by a --- line")
        # fences
        for o, c, info in doc.fences:
            if c is None:
                add(o + 1, "error", "fence-unclosed", "code fence is never closed (the rest of the file is code)", True)
            elif not info:
                add(o + 1, "info", "fence-language", "code block without a language (no syntax highlighting)")
        # headings
        prev_level = 0
        h1s = [h for h in doc.headings if h["level"] == 1]
        if len(h1s) > 1:
            add(h1s[1]["line"], "warning", "multiple-h1", f"{len(h1s)} top-level headings (# …); documents usually have one title")
        parents: list[str] = []
        seen_under: dict[tuple[str, ...], dict[str, int]] = {}
        for h in doc.headings:
            if not h["text"]:
                add(h["line"], "error", "heading-empty", "empty heading")
            if prev_level and h["level"] > prev_level + 1:
                add(h["line"], "warning", "heading-increment", f"heading jumps from level {prev_level} to {h['level']} (h{prev_level} → h{h['level']})")
            prev_level = h["level"]
            parents = parents[: h["level"] - 1]
            key = tuple(parents)
            texts = seen_under.setdefault(key, {})
            low = h["text"].lower()
            if low and low in texts:
                add(h["line"], "warning", "heading-duplicate", f"duplicate heading '{h['text']}' (also line {texts[low]}); its anchor becomes #{gh_slug(h['raw'])}-1")
            texts.setdefault(low, h["line"])
            while len(parents) < h["level"] - 1:
                parents.append("")
            parents.append(low)
        for i, ln in enumerate(doc.lines):
            if doc.code[i]:
                continue
            if ATX_NOSPACE.match(ln) and not re.match(r"^ {0,3}#+\S*#?$", ln.strip() + "x") and not ln.lstrip().startswith("#!"):
                if re.match(r"^ {0,3}#{1,6}[A-Za-z0-9\u00c0-\uffff*_\[`]", ln):
                    add(i + 1, "warning", "heading-space", "no space after # (not a heading in CommonMark)", True)
        # links, images, references, footnotes
        defs: dict[str, int] = {}
        for i, ln in enumerate(doc.lines):
            if doc.code[i]:
                continue
            m = REF_DEF.match(ln) or REF_DEF_OPEN.match(ln)
            if m and not ln.lstrip().startswith("[^"):
                defs.setdefault(norm_label(m.group(1)), i + 1)
        used_defs: set[str] = set()
        fn_defs = {m.group(1): doc.text[: m.start()].count("\n") + 1 for m in FOOTNOTE_DEF.finditer(doc.text)}
        fn_used: set[str] = set()
        heading_labels: set[str] | None = None
        for lineno, ln, orig in doc.prose_lines():
            if REF_DEF.match(ln) and not ln.lstrip().startswith("[^"):
                target = REF_DEF.match(ln).group(2).strip("<>")
                self._check_target(doc, lineno, target, False, add)
                continue
            if REF_DEF_OPEN.match(ln) and not ln.lstrip().startswith("[^"):
                continue
            for m in INLINE_LINK.finditer(ln):
                mo = INLINE_LINK.fullmatch(orig[m.start() : m.end()]) or m  # the text may be a code span
                bang, text, target = mo.group(1), mo.group(2), (mo.group(3) or "").strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1]
                is_img = bang == "!"
                if not target:
                    add(lineno, "error", "link-empty", f"{'image' if is_img else 'link'} with an empty target: {m.group(0)[:60]}")
                    continue
                if not text.strip() and not is_img:
                    add(lineno, "warning", "link-text", f"link without text: {m.group(0)[:60]}")
                if is_img and not text.strip():
                    add(lineno, "warning", "image-alt", f"image without alt text: {target[:60]}")
                self._check_target(doc, lineno, target, is_img, add)
            for m in REF_LINK.finditer(ln):
                # Found on the masked line (brackets in code spans do not count), read from the original (labels may
                # hold code spans).
                mo = REF_LINK.fullmatch(orig[m.start() : m.end()]) or m
                raw = (mo.group(3) or mo.group(2)).strip()
                label = norm_label(raw)
                used_defs.add(label)
                if label not in defs:
                    if heading_labels is None:
                        heading_labels = doc.heading_labels()
                    if label in heading_labels:
                        add(lineno, "info", "ref-heading", f"[{raw}] points at a heading through pandoc's implicit header references (GitHub does not resolve it)")
                    else:
                        add(lineno, "error", "ref-undefined", f"reference [{raw}] is not defined")
            for m in re.finditer(r"(?<![\]!\\])\[((?:[^\[\]\\]|\\.)+)\](?![\[(:])", ln):
                label = norm_label(m.group(1))
                if label in defs:
                    used_defs.add(label)
            for m in FOOTNOTE_REF.finditer(ln):
                fn_used.add(m.group(1))
                if m.group(1) not in fn_defs:
                    add(lineno, "error", "footnote-undefined", f"footnote [^{m.group(1)}] has no definition")
            for m in HTML_ATTR.finditer(ln):
                self._check_target(doc, lineno, m.group(3), m.group(2).lower() == "src", add)
        for label, line in defs.items():
            if label not in used_defs:
                add(line, "info", "ref-unused", f"reference definition [{label}] is never used")
        for label, line in fn_defs.items():
            if label not in fn_used:
                add(line, "warning", "footnote-unused", f"footnote [^{label}] is defined but never referenced")
        # tables
        for start, end in tables(doc):
            header_cols = count_cells(doc.lines[start])
            delim_cols = count_cells(doc.lines[start + 1])
            if delim_cols != header_cols:
                add(start + 2, "error", "table-columns", f"table delimiter row has {delim_cols} columns, the header {header_cols}")
            for k in range(start + 2, end):
                n = count_cells(doc.lines[k])
                if n < header_cols:
                    add(k + 1, "warning", "table-columns", f"table row has {n} cells, the header {header_cols} (missing cells are empty)", True)
                elif n > header_cols:
                    add(k + 1, "error", "table-columns", f"table row has {n} cells, the header {header_cols} (extra cells are dropped when rendered)")
        # whitespace
        blank_run = 0
        for i, ln in enumerate(doc.lines):
            if doc.code[i]:
                blank_run = 0
                continue
            if ln != ln.rstrip() and not (ln.endswith("  ") and not ln.endswith("   ") and ln.strip()):
                add(i + 1, "info", "trailing-space", "trailing whitespace", True)
            if not ln.strip():
                blank_run += 1
                if blank_run == 2:
                    add(i + 1, "info", "blank-lines", "several blank lines in a row", True)
            else:
                blank_run = 0
        if doc.text and not doc.text.endswith("\n"):
            add(len(doc.lines), "info", "final-newline", "the file does not end with a newline", True)
        # TOC
        toc = find_toc(doc)
        if toc:
            s, e, depth_hint = toc
            current = "\n".join(doc.lines[s + 1 : e]).strip()
            wanted = make_toc(doc, 1, depth_hint or 3, skip_first_h1=True).strip()
            if current != wanted:
                add(s + 1, "warning", "toc-stale", "the table of contents between the markers is out of date", True)
        probs.sort(key=lambda p: (p["line"], LEVELS[p["level"]]))
        return probs

    def _check_target(self, doc: Doc, lineno: int, target: str, is_img: bool, add: Any) -> None:
        target = target.strip()
        u = urlparse(target)
        if u.scheme in ("http", "https", "mailto", "tel", "ftp", "data", "javascript") or target.startswith("//"):
            return
        if u.scheme and len(u.scheme) > 1:
            return
        path_part, _, frag = target.partition("#")
        path_part = unquote(path_part.split("?", 1)[0])
        if not path_part:
            if frag and frag not in doc.anchors() and unquote(frag) not in doc.anchors():
                sugg = suggest_anchor(doc, frag)
                near = sugg or close_anchor(doc, frag)
                add(lineno, "error", "anchor-missing", f"#{frag} matches no heading or id in this file" + (f" (did you mean #{near}?)" if near else ""), bool(sugg))
            return
        base = doc.path.parent
        tp = Path(path_part)
        full = (base / tp) if not tp.is_absolute() else tp
        if path_part.startswith("/"):
            # Site-absolute: resolve from the repository/docs root (the top folder given), else skip.
            return
        if not full.exists():
            if is_img:
                add(lineno, "error", "image-missing", f"image {path_part} does not exist")
            else:
                alt = next((full.with_suffix(e) for e in (".md", ".markdown") if full.with_suffix(e).exists()), None) if not full.suffix else None
                add(lineno, "error", "link-broken", f"{path_part} does not exist" + (f" (did you mean {alt.name}?)" if alt else ""))
            return
        if frag and full.suffix.lower() in MD_EXTS:
            other = self.doc_for(full)
            if other is not None and frag not in other.anchors() and unquote(frag) not in other.anchors():
                sugg = suggest_anchor(other, frag)
                near = sugg or close_anchor(other, frag)
                add(lineno, "error", "anchor-missing", f"{path_part}#{frag}: no such heading in {full.name}" + (f" (did you mean #{near}?)" if near else ""), bool(sugg))


def suggest_anchor(doc: Doc, frag: str) -> str | None:
    want = norm_anchor(frag)
    matches = {a for a in doc.anchors() if norm_anchor(a) == want}
    if len(matches) == 1:
        return matches.pop()
    return None


def close_anchor(doc: Doc, frag: str) -> str | None:
    """The most similar anchor (a suggestion only: --fix changes a link only when the spelling alone differs)."""
    import difflib

    near = difflib.get_close_matches(unquote(frag).lower(), sorted(doc.anchors()), n=1, cutoff=0.72)
    return near[0] if near else None


def count_cells(line: str) -> int:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    s = re.sub(r"`[^`]*`", "x", s)
    return len(re.split(r"(?<!\\)\|", s))


def tables(doc: Doc) -> list[tuple[int, int]]:
    out = []
    i = 0
    n = len(doc.lines)
    while i < n - 1:
        if not doc.code[i] and "|" in doc.lines[i] and TABLE_DELIM.match(doc.lines[i + 1]) and "-" in doc.lines[i + 1] and ("|" in doc.lines[i + 1] or doc.lines[i].count("|") >= 1):
            j = i + 2
            while j < n and doc.lines[j].strip() and "|" in doc.lines[j] and not doc.code[j]:
                j += 1
            out.append((i, j))
            i = j
            continue
        i += 1
    return out


def find_toc(doc: Doc) -> tuple[int, int, int | None] | None:
    for i, ln in enumerate(doc.lines):
        s = ln.strip()
        for open_m, close_m in TOC_MARKERS:
            if s.lower() == open_m.lower() or s.lower().startswith(open_m[:-4].lower() + " "):
                for j in range(i + 1, min(len(doc.lines), i + 2000)):
                    if doc.lines[j].strip().lower() == close_m.lower():
                        m = re.search(r"depth[:=]\s*(\d)", s)
                        return i, j, int(m.group(1)) if m else None
    return None


def make_toc(doc: Doc, min_level: int = 1, max_level: int = 3, skip_first_h1: bool = True, bullet: str = "-") -> str:
    hs = [h for h in doc.headings if min_level <= h["level"] <= max_level and h["text"]]
    toc_line = None
    t = find_toc(doc)
    if t:
        toc_line = t[0] + 1
    if skip_first_h1 and hs and hs[0]["level"] == 1 and sum(1 for h in hs if h["level"] == 1) == 1:
        hs = hs[1:]
    hs = [h for h in hs if not (toc_line and h["line"] < toc_line and h["text"].lower() in ("contents", "table of contents", "toc"))]
    if not hs:
        return ""
    top = min(h["level"] for h in hs)
    seen: dict[str, int] = {}
    slugs = {}
    for h in doc.headings:
        base = gh_slug(h["raw"])
        k = seen.get(base, 0)
        slugs[h["line"]] = h["id"] or (base if k == 0 else f"{base}-{k}")
        seen[base] = k + 1
    lines = []
    for h in hs:
        indent = "  " * (h["level"] - top)
        text = h["text"].replace("[", "\\[").replace("]", "\\]")
        lines.append(f"{indent}{bullet} [{text}](#{slugs[h['line']]})")
    return "\n".join(lines)


# ── stats ───────────────────────────────────────────────────────────────


def stats(doc: Doc) -> dict[str, Any]:
    prose = []
    for i, ln in enumerate(doc.lines):
        if not doc.code[i]:
            prose.append(ln)
    text = "\n".join(prose)
    text_nolinks = re.sub(r"\]\([^)]*\)", "]", text)
    words = len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", re.sub(r"<[^>]+>", " ", text_nolinks)))
    code_lines = sum(1 for i, c in enumerate(doc.code) if c) - (doc.front[1] - doc.front[0] + 1 if doc.front else 0)
    prose = doc.prose_lines()
    links = sum(1 for _, ln, _ in prose for m in INLINE_LINK.finditer(ln) if not m.group(1))
    images = sum(1 for _, ln, _ in prose for m in INLINE_LINK.finditer(ln) if m.group(1))
    minutes = words / WPM
    return {
        "words": words, "reading_minutes": round(minutes, 1), "reading_time": f"{max(1, round(minutes))} min" if words else "0 min",
        "headings": len(doc.headings), "links": links, "images": images, "code_blocks": len(doc.fences), "code_lines": max(0, code_lines),
        "tables": len(tables(doc)), "lines": len(doc.lines), "front_matter": doc.front[2] if doc.front else None,
    }


# ── fixes ───────────────────────────────────────────────────────────────


def _after_title_block(doc: Doc) -> int:
    """Where a TOC goes in a file without a Markdown H1: after its front matter and any leading HTML blocks or
    badge lines (a README's centred logo, <h1 align=center>, shields), so the title block stays on top."""
    lines = doc.lines
    i = doc.front[1] + 1 if doc.front else 0
    at = i
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        first = lines[i].lstrip()
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        block = [x.strip() for x in lines[i:j]]
        html = first.startswith("<") and not first.startswith(("<!-- toc", "<!-- TOC"))
        badges = all(re.fullmatch(r"(?:\[?!\[[^\]]*\]\([^)]*\)(?:\]\([^)]*\))?\s*)+", x) for x in block)
        if not (html or badges):
            break
        if html:  # an HTML block runs to its closing tag, which may follow blank lines
            tag = re.match(r"<([A-Za-z][\w-]*)", first)
            if tag and not re.search(rf"</{tag.group(1)}\s*>", "\n".join(block), re.I) and not first.endswith("/>"):
                k = j
                while k < len(lines) and not re.search(rf"</{tag.group(1)}\s*>", lines[k], re.I):
                    k += 1
                j = min(len(lines), k + 1)
        i = at = j
    return at


def apply_fixes(doc: Doc, add_toc: bool, toc_depth: int) -> tuple[str, dict[str, int]]:
    lines = list(doc.lines)
    counts: dict[str, int] = {}

    def bump(k: str, n: int = 1) -> None:
        counts[k] = counts.get(k, 0) + n

    code = list(doc.code)
    # Unclosed fences: close at the end of the file.
    for o, c, _ in doc.fences:
        if c is None:
            fence = FENCE.match(lines[o]).group(2)  # type: ignore[union-attr]
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(fence[0] * len(fence))
            code.append(True)
            bump("closed code fences")
    # Headings without a space after #.
    for i, ln in enumerate(lines):
        if i < len(code) and code[i]:
            continue
        m = ATX_NOSPACE.match(ln)
        if m and re.match(r"^ {0,3}#{1,6}[A-Za-z0-9\u00c0-\uffff*_\[`]", ln) and not ln.lstrip().startswith("#!"):
            lines[i] = f"{m.group(1)} {m.group(2)}"
            bump("heading spaces")
    # Anchor links that only differ in spelling from a real heading anchor.
    anchors = doc.anchors()

    def fix_anchor(m: re.Match[str]) -> str:
        target = m.group(3) or ""
        if target.startswith("#") and target[1:] not in anchors:
            s = suggest_anchor(doc, target[1:])
            if s:
                bump("anchor links")
                return m.group(0).replace("(" + target, "(#" + s, 1).replace("(<" + target + ">", "(#" + s, 1)
        return m.group(0)

    for i, ln in enumerate(lines):
        if i < len(code) and code[i] or "](#" not in ln:
            continue
        lines[i] = INLINE_LINK.sub(fix_anchor, ln)
    # Short table rows: pad with empty cells.
    for start, end in tables(doc):
        want = count_cells(lines[start])
        for k in range(start + 2, end):
            n = count_cells(lines[k])
            if n < want:
                row = lines[k].rstrip()
                closed = row.endswith("|")
                pad = " |" * (want - n) if closed else " | " * (want - n)
                lines[k] = row + pad if closed else row + " |" + " |" * (want - n - 1) + (" |" if not closed else "")
                bump("table rows padded")
    # Trailing whitespace (keeping two-space hard breaks) and runs of blank lines.
    out: list[str] = []
    blank = 0
    for i, ln in enumerate(lines):
        is_code = i < len(code) and code[i]
        if not is_code:
            stripped = ln.rstrip()
            if ln != stripped:
                if ln.endswith("  ") and not ln.endswith("   ") and stripped:
                    pass
                else:
                    ln = stripped
                    bump("trailing whitespace")
            if not ln.strip():
                blank += 1
                if blank > 1:
                    bump("extra blank lines")
                    continue
            else:
                blank = 0
        else:
            blank = 0
        out.append(ln)
    text = "\n".join(out).rstrip("\n") + "\n"
    if not doc.text.endswith("\n"):
        bump("final newline")
    # TOC between markers (refreshed) or added after the title.
    fixed = Doc.__new__(Doc)
    fixed.path, fixed.text, fixed.encoding_ok, fixed.crlf = doc.path, text, True, False
    fixed.lines = text.split("\n")
    fixed.front, fixed.code, fixed.fences, fixed._anchors = None, [False] * len(fixed.lines), [], None
    fixed._scan()
    fixed.headings = fixed._headings()
    toc = find_toc(fixed)
    if toc:
        s, e, depth_hint = toc
        body = make_toc(fixed, 1, depth_hint or toc_depth)
        new_lines = fixed.lines[: s + 1] + ([""] + body.split("\n") + [""] if body else [""]) + fixed.lines[e:]
        if "\n".join(fixed.lines[s + 1 : e]).strip() != body.strip():
            bump("table of contents refreshed")
        text = "\n".join(new_lines)
    elif add_toc:
        body = make_toc(fixed, 1, toc_depth)
        if body:
            h1 = next((h for h in fixed.headings if h["level"] == 1), None)
            at = h1["line"] if h1 else _after_title_block(fixed)
            block = ["", "<!-- toc -->", "", *body.split("\n"), "", "<!-- tocstop -->", ""]
            new_lines = fixed.lines[:at] + block + fixed.lines[at:]
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines))
            bump("table of contents added")
    if doc.crlf:
        text = text.replace("\n", "\r\n")
    return text, counts


# ── main ────────────────────────────────────────────────────────────────


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        pth = Path(p).expanduser()
        if pth.is_dir():
            for f in sorted(pth.rglob("*")):
                if f.suffix.lower() in MD_EXTS and f.is_file() and not any(part.startswith(".") or part in ("node_modules", "_build", "site-packages") for part in f.relative_to(pth).parts[:-1]):
                    files.append(f)
        elif pth.is_file():
            files.append(pth)
        else:
            raise SkillError(f"{pth} does not exist")
    if not files:
        raise SkillError("no Markdown files found")
    return files


def render(res: dict[str, Any]) -> str:
    out = []
    for f in res["files"]:
        st = f.get("stats") or {}
        counts = {k: sum(1 for p in f["problems"] if p["level"] == k) for k in LEVELS}
        head = f"## {f['file']}: " + (", ".join(f"{v} {k}{'s' if v != 1 else ''}" for k, v in counts.items() if v) or "no problems")
        if st:
            head += f" · {st['words']:,} words, {st['reading_time']} read, {st['headings']} headings"
        out.append(head)
        shown = [p for p in f["problems"] if LEVELS[p["level"]] <= LEVELS[res["min_level"]]]
        for p in shown[: res["limit"]]:
            out.append(f"- line {p['line']} · {p['level']} · {p['rule']}: {p['message']}" + (" [fixable]" if p.get("fixable") else ""))
        if len(shown) > res["limit"]:
            out.append(f"- … {len(shown) - res['limit']} more (--limit, or --format json)")
        if f.get("toc"):
            out.append("\nTable of contents:\n\n" + f["toc"])
        out.append("")
    s = res["summary"]
    out.append(f"{s['files']} file(s): {s['error']} error(s), {s['warning']} warning(s), {s['info']} note(s)" + (f"; {s['fixable']} fixable with --fix NEW.md" if s["fixable"] else ""))
    if res.get("fixed"):
        fx = res["fixed"]
        out.append(f"wrote {fx['output']}: " + (", ".join(f"{v} {k}" for k, v in fx["changes"].items()) or "no changes needed"))
    return "\n".join(out)


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("paths", nargs="+", help="Markdown files or folders")
    p.add_argument("--toc", action="store_true", help="print a table of contents (GitHub anchors) for each file")
    p.add_argument("--toc-depth", type=int, default=3, help="deepest heading level in the TOC (default 3)")
    p.add_argument("--stats", action="store_true", help="only statistics (words, reading time, headings, links, code, tables)")
    p.add_argument("--fix", metavar="NEW.md", help="write a fixed copy of the (single) input to this new file")
    p.add_argument("--add-toc", action="store_true", help="with --fix: insert a TOC (between markers) after the title when there is none")
    p.add_argument("--level", choices=list(LEVELS), default="info", help="show problems at this level and above (default info: everything)")
    p.add_argument("--ignore", action="append", default=[], help="rule to skip (e.g. fence-language, trailing-space); repeatable")
    p.add_argument("--limit", type=int, default=200, help="problems listed per file (default 200)")
    p.add_argument("--strict", action="store_true", help="exit with status 1 when there are errors")
    p.add_argument("--force", action="store_true", help="replace --fix output if it exists")
    add_format(p)
    a = p.parse_args()
    files = collect(a.paths)
    if a.fix and len(files) != 1:
        raise UsageError("--fix takes a single input file")
    docs = {f.resolve(): Doc(f) for f in files}
    checker = Checker(docs)
    results = []
    total = {"files": len(files), "error": 0, "warning": 0, "info": 0, "fixable": 0}
    for f in files:
        d = docs[f.resolve()]
        entry: dict[str, Any] = {"file": str(f), "stats": stats(d)}
        if not a.stats:
            probs = [x for x in checker.check(d) if x["rule"] not in set(a.ignore)]
            entry["problems"] = probs
            for x in probs:
                total[x["level"]] += 1
                total["fixable"] += 1 if x.get("fixable") else 0
        else:
            entry["problems"] = []
        if a.toc:
            entry["toc"] = make_toc(d, 1, a.toc_depth)
        results.append(entry)
    res: dict[str, Any] = {"files": results, "summary": total, "min_level": a.level, "limit": a.limit}
    if a.fix:
        src = files[0]
        dest = output_path(a.fix, [src], a.force)
        text, changes = apply_fixes(docs[src.resolve()], a.add_toc, a.toc_depth)
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        res["fixed"] = {"output": str(dest), "changes": changes}
    if a.stats and a.format != "json":
        for r in results:
            s = r["stats"]
            print(f"{r['file']}: {s['words']:,} words, {s['reading_time']} to read ({WPM} wpm), {s['headings']} headings, {s['links']} links, {s['images']} images, {s['code_blocks']} code blocks, {s['tables']} tables, {s['lines']:,} lines" + (f", {s['front_matter']} front matter" if s["front_matter"] else ""))
            if r.get("toc"):
                print("\n" + r["toc"])
        return 0
    emit(res, a.format, render, max_chars=None)
    return 1 if a.strict and total["error"] else 0


if __name__ == "__main__":
    run_main(main)
