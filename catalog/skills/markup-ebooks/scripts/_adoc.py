"""AsciiDoc preprocessor directives for markup-ebooks: conditionals and includes, the way Asciidoctor does them.

pandoc's AsciiDoc reader does not know ifdef::, ifndef::, ifeval:: and endif:: (it fails with 'Unexpected "[]"' or
prints the directives as text), ignores the tag=, lines= and leveloffset= options of include::, and stops on a missing
include. Real READMEs and books use all of these. Asciidoctor evaluates them line by line before parsing, so this
does the same into a copy that pandoc reads. Attributes come from the document's own `:name: value` lines (in order)
plus the defaults of a plain HTML conversion. Includes follow Asciidoctor's SAFE mode: only files inside the
document's folder (or folders the caller allows); absolute paths, paths that lead outside and URLs are left out.
Standard library only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from _common import SkillError

DIRECTIVE = re.compile(r"^(ifdef|ifndef|ifeval|endif|include)::", re.M)
_IFDEF = re.compile(r"^(ifdef|ifndef)::([^\[\s]*)\[(.*)\]\s*$")
_IFEVAL = re.compile(r"^ifeval::\[(.*)\]\s*$")
_ENDIF = re.compile(r"^endif::[^\[\s]*\[\]\s*$")
_INCLUDE = re.compile(r"^include::(\S+?)\[(.*)\]\s*$")
_ATTR = re.compile(r"^:(!?)([A-Za-z0-9_][\w-]*)(!?):(?:[ \t]+(.*?))?\s*$")
_REF = re.compile(r"\{([A-Za-z0-9_][\w-]*)\}")
_CMP = re.compile(r"^\s*(.+?)\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*$")
_REF_ESC = re.compile(r"(\\)?\{([A-Za-z0-9_][\w-]*)\}")
_DELIM = re.compile(r"^(-{4,}|\.{4,}|\+{4,}|/{4,}|`{3,})$")
_TAG_MARK = re.compile(r"\b(tag|end)::([\w*!-]+)\[\]")
_HEADING = re.compile(r"^(=+)(\s+\S.*)$")
MAX_DEPTH = 16
MAX_ATTR_CHARS = 64 * 1024  # one attribute value after its references are expanded
MAX_LINE_CHARS = 1024 * 1024  # one line after expansion
MAX_INCLUDES = 5000
MAX_MB = float(os.environ.get("DESK_ADOC_MAX_MB", "64"))  # the whole preprocessed document

# What Asciidoctor defines for an HTML conversion in SAFE mode, the mode this preprocessor enforces: includes are
# followed only inside the document's folder (and the folders the caller allows), never from absolute paths or URLs.
DEFAULTS = {"backend": "html5", "basebackend": "html", "doctype": "article", "safe-mode-level": "10", "safe-mode-name": "safe", "safe-mode-safe": "", "embedded": "", "outfilesuffix": ".html", "filetype": "html", "asciidoctor": "", "empty": "", "sp": " "}


def _defined(names: str, attrs: dict[str, str]) -> bool:
    if "+" in names:
        return all(n.strip() in attrs for n in names.split("+") if n.strip())
    return any(n.strip() in attrs for n in names.split(",") if n.strip())


def _value(tok: str) -> float | str | bool:
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    if t in ("true", "false"):
        return t == "true"
    try:
        return float(t)
    except ValueError:
        return t


def evaluate(expr: str, attrs: dict[str, str]) -> bool:
    """An ifeval expression such as `{safe-mode-level} < 20` or `"{backend}" == "html5"`; unknown forms are false."""
    text = _REF.sub(lambda m: attrs.get(m.group(1), m.group(0)), expr)  # a missing attribute stays as written
    m = _CMP.match(text)
    if not m:
        return False
    a, op, b = _value(m.group(1)), m.group(2), _value(m.group(3))
    if type(a) is not type(b):
        a, b = str(a), str(b)
    try:
        return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]  # type: ignore[operator]
    except TypeError:
        return False


def _options(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in re.findall(r'(\w+)=("[^"]*"|[^,]*)', spec):
        out[part[0]] = part[1].strip('"')
    return out


def _select_tags(lines: list[str], tags: str) -> list[str]:
    """Lines between `tag::name[]` and `end::name[]` markers for the wanted tags (`*`, `**` and `!name` work too)."""
    wanted = [t.strip() for t in re.split(r"[;,]", tags) if t.strip()]
    include = {t for t in wanted if not t.startswith("!") and t not in ("*", "**")}
    exclude = {t[1:] for t in wanted if t.startswith("!")}
    star, globstar = "*" in wanted, "**" in wanted
    untagged = globstar or (not include and not star and bool(exclude))  # lines outside any tag
    out: list[str] = []
    open_tags: list[str] = []
    for ln in lines:
        m = _TAG_MARK.search(ln)
        if m:
            kind, name = m.groups()
            if kind == "tag":
                open_tags.append(name)
            elif name in open_tags:
                open_tags.remove(name)
            continue
        if any(t in exclude for t in open_tags):
            continue
        if (open_tags and (star or globstar or untagged or any(t in include for t in open_tags))) or (not open_tags and untagged):
            out.append(ln)
    return out


def _select_lines(lines: list[str], spec: str) -> list[str]:
    keep: set[int] = set()
    for part in re.split(r"[;,]", spec):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("..")
        try:
            lo = int(a)
            hi = int(b) if b and b != "-1" else (len(lines) if b else lo)
        except ValueError:
            continue
        keep.update(range(lo, hi + 1))
    return [ln for i, ln in enumerate(lines, 1) if i in keep]


def _shift(lines: list[str], offset: int) -> list[str]:
    if not offset:
        return lines
    out = []
    fence = False
    for ln in lines:
        if ln.startswith(("----", "....", "```")):
            fence = not fence
        m = None if fence else _HEADING.match(ln)
        out.append(("=" * max(1, min(6, len(m.group(1)) + offset)) + m.group(2)) if m else ln)
    return out


class Preprocessor:
    def __init__(self, attrs: dict[str, str] | None = None, roots: list[Path] | None = None) -> None:
        self.env = {**DEFAULTS, **(attrs or {})}
        self.roots = roots  # the folders includes may come from (resolved); None: the top document's folder
        self.warnings: list[str] = []
        self.included: list[Path] = []
        self.total = 0
        self.includes = 0
        self.max_total = int(MAX_MB * 1024 * 1024)

    def _expanded(self, text: str, rx: "re.Pattern[str]", group: int) -> int:
        """The length `text` has once its attribute references are expanded (without building it)."""
        n = len(text)
        env = self.env
        for r in rx.finditer(text):
            if rx is _REF_ESC and r.group(1):
                continue
            v = env.get(r.group(group))
            if v is not None:
                n += len(v) - len(r.group(0))
        return n

    def _grow(self, n: int) -> None:
        self.total += n
        if self.total > self.max_total:
            raise SkillError(f"the AsciiDoc document grows past {MAX_MB:g} MB once its includes and attributes are expanded (an include loop or attribute bomb?); refusing it. If the file is trusted, raise DESK_ADOC_MAX_MB")

    def run(self, text: str, base: Path | None, depth: int = 0) -> list[str]:
        out: list[str] = []
        stack: list[bool] = []
        active = True
        env = self.env
        verbatim: str | None = None  # the delimiter of the listing/literal/passthrough block we are in
        for line in text.split("\n"):
            if active:
                delim = line.rstrip()
                if verbatim is not None:
                    if delim == verbatim:
                        verbatim = None
                    if verbatim != "////":
                        self._grow(len(line) + 1)
                        out.append(line)
                    continue
                if _DELIM.match(delim):
                    verbatim = delim
                    if delim != "////":
                        self._grow(len(line) + 1)
                        out.append(line)
                    continue
                if line.startswith("//") and not line.startswith("///"):
                    continue  # a line comment: pandoc's reader trips over them in the header
            head = line[:2]
            if head in ("if", "en", "in"):
                m = _IFDEF.match(line)
                if m:
                    kind, names, content = m.groups()
                    ok = _defined(names, env) == (kind == "ifdef")
                    if content:  # the single-line form: ifdef::name[text]
                        if active and ok:
                            self._grow(len(content) + 1)
                            out.append(content)
                        continue
                    stack.append(active)
                    active = active and ok
                    continue
                m = _IFEVAL.match(line)
                if m:
                    stack.append(active)
                    active = active and evaluate(m.group(1), env)
                    continue
                if _ENDIF.match(line):
                    active = stack.pop() if stack else True
                    continue
                m = _INCLUDE.match(line) if active else None
                if m:
                    out.extend(self.include(m.group(1), m.group(2), base, depth))
                    continue
            if not active:
                continue
            if line[:1] == ":":
                m = _ATTR.match(line)
                if m:
                    name = m.group(2)
                    if m.group(1) or m.group(3):
                        env.pop(name, None)
                    else:
                        value = m.group(4) or ""
                        if "{" in value and self._expanded(value, _REF, 1) > MAX_ATTR_CHARS:
                            raise SkillError(f"AsciiDoc attribute '{name}' expands to more than {MAX_ATTR_CHARS // 1024} KB (an attribute bomb?); refusing the document")
                        env[name] = _REF.sub(lambda r: env.get(r.group(1), r.group(0)), value)
                    self._grow(len(line) + 1)
                    out.append(line)
                    continue
            if "{" in line:
                # Attribute references, which pandoc leaves as written in links, images and text ({url-docs}/x[..]).
                if self._expanded(line, _REF_ESC, 2) > MAX_LINE_CHARS:
                    raise SkillError(f"an AsciiDoc line expands to more than {MAX_LINE_CHARS // (1024 * 1024)} MB through its attribute references (an attribute bomb?); refusing the document")
                line = _REF_ESC.sub(lambda r: r.group(0) if r.group(1) else env.get(r.group(2), r.group(0)), line)
            self._grow(len(line) + 1)
            out.append(line)
        return out

    def include(self, target: str, opts: str, base: Path | None, depth: int) -> list[str]:
        target = _REF.sub(lambda r: self.env.get(r.group(1), r.group(0)), target)
        o = _options(opts)
        if re.match(r"[a-z][a-z0-9+.-]*://", target, re.I):
            return [f"link:{target}[{target}]"]  # remote includes are never fetched
        if self.roots is None:
            self.roots = [(base or Path.cwd()).resolve()]
        from _pandoc_safe import _absolute, _is_uri, inside

        if _absolute(target) or _is_uri(target):
            self.warnings.append(f"include::{target}[] left out (an absolute path; only files inside the document's folder are included)")
            return []
        path = (base or Path.cwd()) / target
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if exists and not inside(path, self.roots):
            self.warnings.append(f"include::{target}[] left out (outside the document's folder)")
            return []
        if depth >= MAX_DEPTH:
            self.warnings.append(f"include::{target}[] nested more than {MAX_DEPTH} deep; left out")
            return []
        self.includes += 1
        if self.includes > MAX_INCLUDES:
            raise SkillError(f"the AsciiDoc document performs more than {MAX_INCLUDES:,} includes (an include loop?); refusing it")
        try:
            if path.stat().st_size > self.max_total:
                raise SkillError(f"include::{target}[] is larger than {MAX_MB:g} MB; refusing it. If the file is trusted, raise DESK_ADOC_MAX_MB")
            text = path.read_text(encoding="utf-8-sig")
            self.included.append(path)
        except (OSError, UnicodeDecodeError):
            self.warnings.append(f"include::{target}[] not found (relative to {base or '.'}); left out")
            return [f"Unresolved directive - include::{target}[]"]
        lines = text.split("\n")
        if o.get("tag") or o.get("tags"):
            lines = _select_tags(lines, o.get("tag") or o.get("tags") or "")
        elif o.get("lines"):
            lines = _select_lines(lines, o["lines"])
        else:
            lines = [ln for ln in lines if not (_TAG_MARK.search(ln) and ln.lstrip().startswith(("//", "#", "--", "<!--", "/*", ";")))]
        if path.suffix.lower() in (".adoc", ".asciidoc", ".asc", ".ad", ".txt", ""):
            lines = self.run("\n".join(lines), path.parent, depth + 1)
        lo = o.get("leveloffset", "")
        if lo:
            try:
                lines = _shift(lines, int(lo))
            except ValueError:
                pass
        return lines


def preprocess(text: str, base: Path | None = None, attrs: dict[str, str] | None = None, roots: list[Path] | None = None) -> tuple[str, list[str], list[Path]]:
    """(text with conditionals evaluated and includes inlined, warnings, included files). Includes come only from
    `roots` (resolved folders; default: `base`)."""
    pp = Preprocessor(attrs, [r.resolve() for r in roots] if roots else None)
    return "\n".join(pp.run(text, base)), pp.warnings, pp.included
