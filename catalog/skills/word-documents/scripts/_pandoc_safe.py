"""Keeps pandoc inside a document's own folder, for the skills that run pandoc on documents from anywhere.

The source of truth is catalog/shared/_pandoc_safe.py; `pnpm catalog:sync` copies it into skills (edit it there).

pandoc follows include directives (reStructuredText, Org, LaTeX, AsciiDoc, Typst, man …) and embeds any local picture
a document names, from anywhere on disk, into DOCX, EPUB, PDF and self-contained HTML. Unchecked, turning a stranger's
document into a PDF could ship the user's keys or another project's files inside it. So:

* `inline_includes` inlines the includes Desk understands (reStructuredText `.. include::`, `.. literalinclude::` and
  `:file:` options; Org `#+INCLUDE:`; LaTeX `\\input`, `\\include`, `\\subfile`, `\\import`, `\\lstinputlisting`,
  `\\verbatiminput`, `\\inputminted`; Typst `#include`) when the target is a file inside the allowed folders (the
  document's folder and the ones the caller names), and leaves the others out with a warning.
* Callers run pandoc's reader with --sandbox, so it reads nothing else (an include Desk did not inline is not read).
* `jail_args` adds a Lua filter that decides every picture, stylesheet, cover and bibliography the document names:
  files inside the allowed folders reach pandoc through its media bag, the others are left out with a warning, and
  with download=True remote pictures are fetched here with time and size limits (or become links), so pandoc never
  goes to the network itself. The filter runs this file as a script to judge paths, since symlinks can only be
  followed from Python.

Standard library only; the script part runs with `python -I -S`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Sequence

VERSION = "1"  # part of cache keys: bump when the rules change
MAX_DEPTH = 16
MAX_INCLUDES = 5000
MAX_BYTES = int(float(os.environ.get("DESK_INCLUDE_MAX_MB", "64")) * 1024 * 1024)  # all inlined text together
MAX_REFS = 5000
REMOTE_MAX = int(os.environ.get("DESK_REMOTE_MAX", "80"))
REMOTE_MAX_BYTES = int(float(os.environ.get("DESK_REMOTE_MAX_MB", "20")) * 1024 * 1024)
REMOTE_TIMEOUT = float(os.environ.get("DESK_REMOTE_TIMEOUT", "8"))
REMOTE_BUDGET = float(os.environ.get("DESK_REMOTE_BUDGET", "30"))

#: Readers that never open another file: they may run without --sandbox. Every other reader runs sandboxed.
SELF_CONTAINED_READERS = {
    "markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra",
    "markdown_github", "html", "json", "docx", "odt", "pptx", "xlsx", "epub", "ipynb", "fb2", "rtf", "csv", "tsv",
    "bibtex", "biblatex", "ris", "csljson", "endnotexml",
}
INCLUDE_READERS = {"rst", "org", "latex", "typst"}
MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".pdf": "application/pdf",
    ".avif": "image/avif", ".ico": "image/x-icon", ".emf": "image/x-emf", ".wmf": "image/x-wmf", ".eps": "application/postscript",
    ".css": "text/css", ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
}


def base_reader(reader: str) -> str:
    """'rst' for 'rst+smart' and the like."""
    return reader.split("+")[0].split("-")[0].lower()


def resolved(folders: Iterable[str | os.PathLike[str]]) -> list[Path]:
    """The folders, resolved, without duplicates or missing ones."""
    out: list[Path] = []
    for f in folders:
        try:
            p = Path(f).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def inside(path: Path, roots: Sequence[Path]) -> bool:
    """True when `path`, with every symlink followed, lies in one of the (resolved) roots."""
    try:
        real = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return any(real == r or real.is_relative_to(r) for r in roots)


def _absolute(ref: str) -> bool:
    return ref.startswith(("/", "\\", "~")) or PurePosixPath(ref).is_absolute() or bool(PureWindowsPath(ref).drive) or PureWindowsPath(ref).is_absolute()


def _escapes(ref: str) -> bool:
    norm = os.path.normpath(ref.replace("\\", "/")).replace("\\", "/")
    return norm == ".." or norm.startswith("../")


_URI = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):")


def _is_uri(ref: str) -> bool:
    return _URI.match(ref) is not None  # one letter before ':' is a Windows drive, not a scheme


# ── includes ────────────────────────────────────────────────────────────


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", "replace")


class Inliner:
    """Inlines the includes of one document (see the module doc). `included` lists the files it read."""

    def __init__(self, roots: Sequence[Path], warnings: list[str] | None = None) -> None:
        self.roots = list(roots)
        self.top = self.roots[0] if self.roots else Path.cwd().resolve()
        self.warnings = warnings if warnings is not None else []
        self.included: list[Path] = []
        self.total = 0
        self.count = 0

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def find(self, target: str, bases: Sequence[Path], exts: Sequence[str] = ()) -> tuple[Path | None, str]:
        """(the file an include names, or None and why it is left out)."""
        t = target.strip().strip('"').strip()
        if not t or "\x00" in t:
            return None, "an empty target"
        if _is_uri(t):
            return None, "a URL"
        if _absolute(t):
            return None, "an absolute path"
        for base in bases:
            for name in [t, *[t + e for e in exts if not t.lower().endswith(e)]]:
                cand = base / name
                try:
                    if cand.is_file():
                        return (cand, "") if inside(cand, self.roots) else (None, "outside the document's folder")
                except OSError:
                    continue
        if _escapes(t):
            return None, "outside the document's folder"
        return None, "not found"

    def load(self, path: Path, what: str) -> str | None:
        self.count += 1
        if self.count > MAX_INCLUDES:
            raise ValueError(f"the document performs more than {MAX_INCLUDES:,} includes (an include loop?)")
        try:
            size = path.stat().st_size
            if self.total + size > MAX_BYTES:
                raise ValueError(f"the document grows past {MAX_BYTES // (1024 * 1024)} MB once its includes are inlined (an include loop?)")
            text = read_text(path)
        except OSError as e:
            self._warn(f"{what} could not be read ({e.strerror or e}); left out")
            return None
        self.total += size
        if path not in self.included:
            self.included.append(path)
        return text

    def left_out(self, what: str, why: str) -> None:
        self._warn(f"{what} left out ({why})" if why != "not found" else f"{what} not found; left out")

    def run(self, text: str, reader: str, base: Path, depth: int = 0) -> str:
        r = base_reader(reader)
        if depth > MAX_DEPTH:
            self._warn(f"includes nested more than {MAX_DEPTH} deep; the deepest were left out")
            return ""
        if r == "rst":
            return self.rst(text, base, depth)
        if r == "org":
            return self.org(text, base, depth)
        if r == "latex":
            return self.latex(text, base, depth)
        if r == "typst":
            return self.typst(text, base, depth)
        return text

    # reStructuredText ──────────────────────────────────────────────────

    _RST_DIR = re.compile(r"^(?P<ind>[ \t]*)\.\.[ \t]+(?P<name>include|literalinclude|raw|csv-table)::[ \t]*(?P<arg>.*?)[ \t]*$")
    _RST_OPT = re.compile(r"^[ \t]+:(?P<k>[\w-]+):(?:[ \t]+(?P<v>.*?))?[ \t]*$")

    def rst(self, text: str, base: Path, depth: int) -> str:
        if ".. " not in text:
            return text
        lines = text.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            m = self._RST_DIR.match(lines[i])
            if not m:
                out.append(lines[i])
                i += 1
                continue
            ind, name, arg = m.group("ind"), m.group("name"), m.group("arg")
            j = i + 1
            opts: dict[str, str] = {}
            opt_lines: list[str] = []
            while j < len(lines):
                o = self._RST_OPT.match(lines[j])
                if not o or len(lines[j]) - len(lines[j].lstrip()) <= len(ind):
                    break
                opts[o.group("k").lower()] = o.group("v") or ""
                opt_lines.append(lines[j])
                j += 1
            if name in ("raw", "csv-table"):
                out.extend(self._rst_file_option(lines[i], ind, opts, opt_lines, base, name))
                i = j
                continue
            if arg.startswith("<") and arg.endswith(">"):
                out.extend(lines[i:j])  # a docutils standard file (isonum.txt …): pandoc has none, nothing to read
                i = j
                continue
            what = f"{name} {arg}"
            path, why = self.find(arg, [base])
            body = self.load(path, what) if path is not None else None
            if body is None:
                if path is None:
                    self.left_out(what, why)
                i = j
                continue
            body = self._rst_slice(body, opts)
            pad = ind + "   "
            if name == "literalinclude":
                lang = opts.get("language", "")
                out.append(f"{ind}.. code-block:: {lang}".rstrip())
                out.append("")
                out.extend((pad + ln) if ln.strip() else "" for ln in body.split("\n"))
            elif "literal" in opts:
                out.append(f"{ind}::")
                out.append("")
                out.extend((pad + ln) if ln.strip() else "" for ln in body.split("\n"))
            elif "code" in opts:
                out.append(f"{ind}.. code:: {opts['code']}".rstrip())
                out.append("")
                out.extend((pad + ln) if ln.strip() else "" for ln in body.split("\n"))
            else:
                inner = self.run(body, "rst", path.parent, depth + 1)
                out.extend((ind + ln) if ln.strip() else "" for ln in inner.split("\n"))
            out.append("")
            i = j
        return "\n".join(out)

    @staticmethod
    def _rst_slice(body: str, opts: dict[str, str]) -> str:
        lines = body.split("\n")
        try:
            if "start-line" in opts or "end-line" in opts:
                a = int(opts.get("start-line") or 0)
                b = int(opts["end-line"]) if opts.get("end-line") else None
                lines = lines[a:b]
        except ValueError:
            pass
        if opts.get("lines"):  # literalinclude: 1,3,5-10
            keep: list[str] = []
            for part in opts["lines"].split(","):
                lo, _, hi = part.strip().partition("-")
                try:
                    a = int(lo) if lo else 1
                    b = int(hi) if hi else (len(lines) if _ else a)
                except ValueError:
                    continue
                keep += lines[a - 1 : b]
            lines = keep
        body = "\n".join(lines)
        if opts.get("start-after") and opts["start-after"] in body:
            body = body.split(opts["start-after"], 1)[1]
        if opts.get("end-before") and opts["end-before"] in body:
            body = body.split(opts["end-before"], 1)[0]
        return body.strip("\n")

    def _rst_file_option(self, head: str, ind: str, opts: dict[str, str], opt_lines: list[str], base: Path, name: str) -> list[str]:
        """raw and csv-table with :file: (or :url:): the file's content becomes the directive's body."""
        keep = [ln for ln in opt_lines if not re.match(r"^[ \t]+:(file|url|encoding):", ln)]
        if "url" in opts:
            self._warn(f"{name} :url: {opts['url']} left out (documents are never fetched from the network)")
        if "file" not in opts:
            return [head, *keep]
        what = f"{name} :file: {opts['file']}"
        path, why = self.find(opts["file"], [base])
        body = self.load(path, what) if path is not None else None
        if body is None:
            if path is None:
                self.left_out(what, why)
            return [f"{ind}.. {what} left out"]  # an rst comment: the directive itself would read the file
        pad = ind + "   "
        return [head, *keep, "", *[(pad + ln) if ln.strip() else "" for ln in body.strip("\n").split("\n")], ""]

    # Org ───────────────────────────────────────────────────────────────

    _ORG_KW = re.compile(r"^(?P<ind>[ \t]*)#\+(?P<k>include|setupfile):[ \t]*(?P<arg>.*?)[ \t]*$", re.I)
    _ORG_BLOCK = re.compile(r"^[ \t]*#\+(begin|end)_(src|example|export)\b", re.I)

    def org(self, text: str, base: Path, depth: int) -> str:
        if "#+" not in text:
            return text
        out: list[str] = []
        block = False
        for line in text.split("\n"):
            b = self._ORG_BLOCK.match(line)
            if b:
                block = b.group(1).lower() == "begin"
                out.append(line)
                continue
            m = None if block else self._ORG_KW.match(line)
            if not m:
                out.append(line)
                continue
            kw, arg = m.group("k").upper(), m.group("arg")
            toks = re.findall(r'"[^"]*"|\S+', arg)
            target = toks[0].strip('"') if toks else ""
            target = target.split("::", 1)[0]  # a search option (file.org::*Heading): the whole file is included
            what = f"#+{kw}: {target}"
            path, why = self.find(target, [base])
            if kw == "SETUPFILE":
                if path is None:
                    self.left_out(what, why)
                else:
                    out.append(line)  # pandoc does not read setup files; kept as written
                continue
            body = self.load(path, what) if path is not None else None
            if body is None:
                if path is None:
                    self.left_out(what, why)
                continue
            rest = [t for t in toks[1:]]
            opts: dict[str, str] = {}
            words: list[str] = []
            k = 0
            while k < len(rest):
                if rest[k].startswith(":"):
                    opts[rest[k][1:].lower()] = rest[k + 1].strip('"') if k + 1 < len(rest) else ""
                    k += 2
                else:
                    words.append(rest[k])
                    k += 1
            lines = body.split("\n")
            if opts.get("lines"):
                lo, _, hi = opts["lines"].partition("-")
                try:
                    a = int(lo) if lo.strip() else 1
                    b = int(hi) if hi.strip() else len(lines)
                    lines = lines[a - 1 : b]
                except ValueError:
                    pass
            body = "\n".join(lines)
            kind = words[0].lower() if words else ""
            if kind in ("src", "example", "export", "quote", "verse", "center", "comment"):
                extra = " " + " ".join(words[1:]) if words[1:] else ""
                out.append(f"#+BEGIN_{kind.upper()}{extra}")
                out.extend(body.rstrip("\n").split("\n"))
                out.append(f"#+END_{kind.upper()}")
                continue
            inner = self.run(body, "org", path.parent, depth + 1)
            if opts.get("minlevel", "").isdigit():
                levels = [len(h.group(1)) for h in re.finditer(r"^(\*+)\s", inner, re.M)]
                if levels:
                    shift = int(opts["minlevel"]) - min(levels)
                    inner = re.sub(r"^(\*+)(?=\s)", lambda h: "*" * max(1, len(h.group(1)) + shift), inner, flags=re.M)
            out.extend(inner.split("\n"))
        return "\n".join(out)

    # LaTeX ─────────────────────────────────────────────────────────────

    _TEX_CMD = re.compile(
        r"\\(?P<cmd>input|include|subfile|import|subimport|inputfrom|subinputfrom|includefrom|subincludefrom|"
        r"lstinputlisting|verbatiminput|VerbatimInput|BVerbatimInput|LVerbatimInput|inputminted)(?![A-Za-z@])"
    )
    _TEX_VERB = re.compile(r"\\(begin|end)\{(verbatim|Verbatim|lstlisting|minted|comment)\*?\}")
    _TEX_ARG = re.compile(r"\s*\{([^{}]*)\}")
    _TEX_OPT = re.compile(r"\s*\[([^\]]*)\]")
    _TEX_BARE = re.compile(r"[ \t]+([^\s{}\\%]+)")

    def latex(self, text: str, base: Path, depth: int) -> str:
        if "\\" not in text:
            return text
        out: list[str] = []
        verb: str | None = None
        for line in text.split("\n"):
            code, comment = _tex_split_comment(line)
            if verb is not None or self._TEX_VERB.search(code) or not self._TEX_CMD.search(code):
                for v in self._TEX_VERB.finditer(code):
                    verb = v.group(2) if v.group(1) == "begin" else None
                out.append(line)
                continue
            out.append(self._tex_line(code, base, depth) + comment)
        return "\n".join(out)

    def _tex_line(self, code: str, base: Path, depth: int) -> str:
        res: list[str] = []
        pos = 0
        for m in self._TEX_CMD.finditer(code):
            if m.start() < pos or _escaped(code, m.start()):
                continue
            res.append(code[pos : m.start()])
            cmd = m.group("cmd")
            at = m.end()
            opt = None
            if cmd in ("lstinputlisting", "VerbatimInput", "BVerbatimInput", "LVerbatimInput", "inputminted"):
                o = self._TEX_OPT.match(code, at)
                if o:
                    opt, at = o.group(1), o.end()
            args: list[str] = []
            need = 2 if cmd in ("import", "subimport", "inputfrom", "subinputfrom", "includefrom", "subincludefrom", "inputminted") else 1
            for _ in range(need):
                a = self._TEX_ARG.match(code, at)
                if not a:
                    break
                args.append(a.group(1))
                at = a.end()
            if not args and cmd == "input":
                bare = self._TEX_BARE.match(code, at)
                if bare:
                    args, at = [bare.group(1)], bare.end()
            if len(args) != need or any("\\" in x or "#" in x for x in args):
                res.append(code[m.start() : at])  # computed by macros: pandoc reads it sandboxed, which refuses it
                pos = at
                continue
            res.append(self._tex_include(cmd, opt, args, base, depth, code[m.start() : at]))
            pos = at
        res.append(code[pos:])
        return "".join(res)

    def _tex_include(self, cmd: str, opt: str | None, args: list[str], base: Path, depth: int, raw: str) -> str:
        what = raw.strip()
        if cmd in ("import", "subimport", "inputfrom", "subinputfrom", "includefrom", "subincludefrom"):
            folder, name = args[0].strip(), args[1].strip()
            if _absolute(folder) or _is_uri(folder):
                self.left_out(what, "an absolute path")
                return ""
            sub = cmd.startswith("sub")
            bases = [base / folder] if sub else [self.top / folder, base / folder]
            path, why = self.find(name, bases, (".tex",))
        elif cmd == "inputminted":
            path, why = self.find(args[1], [self.top, base])
        else:
            exts = (".tex",) if cmd in ("input", "include", "subfile") else ()
            target = args[0].strip()
            if cmd == "include" and not target.lower().endswith(".tex"):
                target += ".tex"
            path, why = self.find(target, [self.top, base] if base != self.top else [base], exts)
        body = self.load(path, what) if path is not None else None
        if body is None:
            if path is None:
                self.left_out(what, why)
            return ""
        if cmd == "lstinputlisting":
            return "\\begin{lstlisting}" + (f"[{opt}]" if opt else "") + "\n" + body.rstrip("\n") + "\n\\end{lstlisting}"
        if cmd == "verbatiminput":
            return "\\begin{verbatim}\n" + body.rstrip("\n") + "\n\\end{verbatim}"
        if cmd in ("VerbatimInput", "BVerbatimInput", "LVerbatimInput"):
            return "\\begin{Verbatim}" + (f"[{opt}]" if opt else "") + "\n" + body.rstrip("\n") + "\n\\end{Verbatim}"
        if cmd == "inputminted":
            return "\\begin{minted}" + (f"[{opt}]" if opt else "") + "{" + args[0] + "}\n" + body.rstrip("\n") + "\n\\end{minted}"
        return "\n" + self.run(body, "latex", path.parent, depth + 1) + "\n"

    # Typst ─────────────────────────────────────────────────────────────

    _TYP_INC = re.compile(r'^(?P<ind>[ \t]*)#include[ \t]+"(?P<t>[^"\\]*)"[ \t]*$')

    def typst(self, text: str, base: Path, depth: int) -> str:
        if "#include" not in text:
            return text
        out: list[str] = []
        for line in text.split("\n"):
            m = self._TYP_INC.match(line)
            if not m:
                out.append(line)
                continue
            t = m.group("t")
            what = f'#include "{t}"'
            path, why = self.find(t.lstrip("/"), [self.top]) if t.startswith("/") else self.find(t, [base])
            body = self.load(path, what) if path is not None else None
            if body is None:
                if path is None:
                    self.left_out(what, why)
                continue
            out.append(self.run(body, "typst", path.parent, depth + 1))
        return "\n".join(out)


def _escaped(code: str, at: int) -> bool:
    """True when the backslash at `at` is itself escaped (\\\\input is a line break, then the word input)."""
    n = 0
    while at - n - 1 >= 0 and code[at - n - 1] == "\\":
        n += 1
    return n % 2 == 1


def _tex_split_comment(line: str) -> tuple[str, str]:
    """(code, comment) of a LaTeX line: the comment starts at the first % not escaped by a backslash."""
    if "%" not in line:
        return line, ""
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            return line[:i], line[i:]
        i += 1
    return line, ""


_HAS_INCLUDE = {
    "rst": re.compile(r"^[ \t]*\.\.[ \t]+(include|literalinclude|raw|csv-table)::", re.M),
    "org": re.compile(r"^[ \t]*#\+(include|setupfile):", re.M | re.I),
    "latex": re.compile(r"\\(input|include|subfile|import|subimport|inputfrom|subinputfrom|includefrom|subincludefrom|lstinputlisting|verbatiminput|VerbatimInput|BVerbatimInput|LVerbatimInput|inputminted)(?![A-Za-z@])"),
    "typst": re.compile(r"^[ \t]*#include[ \t]", re.M),
}


def has_includes(text: str, reader: str) -> bool:
    rx = _HAS_INCLUDE.get(base_reader(reader))
    return bool(rx and rx.search(text))


def inline_includes(text: str, reader: str, base: Path, roots: Sequence[Path], warnings: list[str] | None = None, included: list[Path] | None = None) -> str:
    """`text` with its includes inlined (see the module doc); `base` is the document's folder, `roots` the folders
    included files may come from (resolved). Raises ValueError for include loops and documents that grow too big."""
    if not has_includes(text, reader):
        return text
    inl = Inliner(roots or [base.resolve()], warnings)
    out = inl.run(text, reader, base.resolve())
    if included is not None:
        included.extend(p for p in inl.included if p not in included)
    return out


# ── the filter ──────────────────────────────────────────────────────────

LUA = r"""-- Desk's jail filter (written by _pandoc_safe.py): pictures, stylesheets, covers and bibliographies only from the
-- allowed folders. _pandoc_safe.py judges every reference; allowed files reach pandoc through the media bag.
local cfg_path = nil
local verdict = {}
local refs, seen, inserted, warned = {}, {}, {}, {}

local function warn(msg)
  if warned[msg] then return end
  warned[msg] = true
  io.stderr:write("[WARNING] " .. msg .. "\n")
end

local function trim(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end

local function skip(ref)
  if ref == nil then return true end
  local r = trim(ref)
  return r == "" or r:match("^[Dd][Aa][Tt][Aa]:") ~= nil or r:sub(1, 1) == "#"
end

local function unescape(s)
  return (s:gsub("&amp;", "&"):gsub("&quot;", '"'):gsub("&#39;", "'"):gsub("&lt;", "<"):gsub("&gt;", ">"))
end

local function want(kind, ref)
  if skip(ref) then return end
  if kind == "image" and pandoc.mediabag.lookup(ref) then return end -- embedded in the input (DOCX, EPUB …)
  local key = kind .. "\n" .. ref
  if seen[key] then return end
  seen[key] = true
  refs[#refs + 1] = { kind = kind, ref = ref }
end

local function get(kind, ref)
  if skip(ref) then return nil end
  return verdict[kind .. "\n" .. ref]
end

local function insert(ref, v)
  if inserted[ref] then return true end
  local f = io.open(v.path, "rb")
  if not f then return false end
  local data = f:read("a")
  f:close()
  pandoc.mediabag.insert(ref, v.mime, data)
  inserted[ref] = true
  if PANDOC_STATE.verbosity == "INFO" and not v.download then
    io.stderr:write("[INFO] Loaded " .. ref .. " from " .. v.path .. "\n")
  end
  return true
end

local function refused(what, ref, v)
  if v.link then warn(v.warn or ("remote " .. what .. " kept as a link: " .. ref))
  else warn(what .. " left out (" .. (v.refuse or "outside the document's folder") .. "): " .. ref) end
end

-- raw HTML: attributes that make pandoc load a file when it embeds resources
local URL_ATTR = { src = true, poster = true, data = true, background = true, lowsrc = true, dynsrc = true, ["xlink:href"] = true }
local HREF_TAGS = { link = "css", image = "image", use = "image", feimage = "image" }

local function attr_kind(tag, name)
  name, tag = name:lower(), tag:lower()
  if name == "href" then return HREF_TAGS[tag] end
  if URL_ATTR[name] then return "image" end
  if name == "srcset" then return "srcset" end
  if name == "style" then return "style" end
  return nil
end

local function each_attr(tag, attrs, fn)
  local out, pos, n = {}, 1, #attrs
  while pos <= n do
    local s, e = attrs:find("^[%s/]+", pos)
    if s then
      out[#out + 1] = attrs:sub(s, e)
      pos = e + 1
    end
    if pos > n then break end
    local s1, e1, name = attrs:find("^([^%s=/>\"']+)", pos)
    if not s1 then
      out[#out + 1] = attrs:sub(pos)
      break
    end
    local s2, e2 = attrs:find("^%s*=%s*", e1 + 1)
    if not s2 then
      out[#out + 1] = name
      pos = e1 + 1
    else
      local q = attrs:sub(e2 + 1, e2 + 1)
      local value, last
      if q == '"' or q == "'" then
        local close = attrs:find(q, e2 + 2, true) or (n + 1)
        value, last = attrs:sub(e2 + 2, close - 1), close
      else
        local s3, e3 = attrs:find("^[^%s>]*", e2 + 1)
        value, last = attrs:sub(s3, e3), e3
      end
      local kind = attr_kind(tag, name)
      local new = nil
      if kind then new = fn(kind, value) end
      if new == false then
        -- the attribute is dropped
      elseif new ~= nil then
        out[#out + 1] = name .. '="' .. new .. '"'
      else
        out[#out + 1] = attrs:sub(s1, last)
      end
      pos = last + 1
    end
  end
  return table.concat(out)
end

local function css_refs(text, fn)
  text = text:gsub("@[Ii][Mm][Pp][Oo][Rr][Tt][^;]*;?", function() return "/* @import left out */" end)
  return (text:gsub("[Uu][Rr][Ll]%(%s*([\"']?)(.-)%1%s*%)", function(q, ref)
    local new = fn("image", unescape(ref))
    if new == false then return "url(data:,)" end
    return nil
  end))
end

local function raw_html(text, fn)
  local out = text:gsub("<([%a][%w:-]*)([^>]*)>", function(tag, attrs)
    return "<" .. tag .. each_attr(tag, attrs, function(kind, value)
      if kind == "style" then
        local new = css_refs(value, fn)
        if new ~= value then return (new:gsub('"', "&quot;")) end
        return nil
      end
      if kind == "srcset" then
        for cand in value:gmatch("[^,]+") do
          local u = cand:match("^%s*(%S+)")
          if u and not skip(u) and fn("image", unescape(u)) == false then return false end
        end
        return nil
      end
      return fn(kind, unescape(value))
    end) .. ">"
  end)
  out = out:gsub("(<[Ss][Tt][Yy][Ll][Ee][^>]*>)(.-)(</[Ss][Tt][Yy][Ll][Ee]%s*>)", function(a, css, b)
    return a .. css_refs(css, fn) .. b
  end)
  return out
end

local function collect_html(kind, ref)
  want(kind, ref)
  return nil
end

local function apply_html(kind, ref)
  local v = get(kind, ref)
  if not v then return nil end
  if v.path then
    if insert(ref, v) then return nil end
    return false
  end
  if v.link or v.refuse then
    refused(kind == "css" and "stylesheet" or "image", ref, v)
    return false
  end
  return nil
end

local BG_ATTRS = { "background-image", "data-background-image", "data-background", "data-background-video", "data-src" }

local function attrs_of(el, fn)
  if not el.attributes then return nil end
  local changed = false
  for _, k in ipairs(BG_ATTRS) do
    local ref = el.attributes[k]
    if ref and fn("image", ref) == false then
      el.attributes[k] = nil
      changed = true
    end
  end
  local style = el.attributes.style
  if style and style:lower():find("url(", 1, true) then
    local new = css_refs(style, fn)
    if new ~= style then
      el.attributes.style = new
      changed = true
    end
  end
  if changed then return el end
  return nil
end

-- metadata naming files: stylesheets and covers matter when the writer embeds them, bibliographies with citeproc
local META_EMBED = { css = "css", stylesheet = "css", ["cover-image"] = "image", ["epub-cover-image"] = "image" }
local META_CITE = { bibliography = "file", csl = "file", ["citation-abbreviations"] = "file" }
local META = {}

local function meta_values(v)
  if pandoc.utils.type(v) == "List" then
    local out = {}
    for i, x in ipairs(v) do out[i] = pandoc.utils.stringify(x) end
    return out, true
  end
  return { pandoc.utils.stringify(v) }, false
end

local function label(el)
  if el.caption and #el.caption > 0 then return el.caption end
  return {}
end

function Pandoc(doc)
  local c = doc.meta["desk-jail"]
  if c == nil then return nil end
  doc.meta["desk-jail"] = nil -- never let the temp path reach the output's metadata
  cfg_path = pandoc.utils.stringify(c)
  local f = io.open(cfg_path, "r")
  if not f then return doc end
  local ok, cfg = pcall(pandoc.json.decode, f:read("a"), false)
  f:close()
  if not ok or type(cfg) ~= "table" then return doc end

  local embed = cfg.embed ~= false -- the writer loads pictures (DOCX, EPUB, PDF, self-contained HTML …)
  if embed then for k, v in pairs(META_EMBED) do META[k] = v end end
  if cfg.citeproc then for k, v in pairs(META_CITE) do META[k] = v end end
  if not embed and not cfg.citeproc then return doc end
  local html_collect = function(el)
    if el.format:match("html") then raw_html(el.text, collect_html) end
  end
  if embed then
    doc:walk({
      Image = function(el) want("image", el.src) end,
      RawInline = html_collect,
      RawBlock = html_collect,
      Div = function(el) attrs_of(el, collect_html) end,
      Span = function(el) attrs_of(el, collect_html) end,
      Header = function(el) attrs_of(el, collect_html) end,
    })
  end
  for key, kind in pairs(META) do
    local v = doc.meta[key]
    if v ~= nil then
      local vals = meta_values(v)
      for _, ref in ipairs(vals) do want(kind == "image" and "cover" or kind, ref) end
    end
  end
  if #refs == 0 then return doc end
  local okp, out = pcall(pandoc.pipe, cfg.python, { "-I", "-S", cfg.helper, cfg_path }, pandoc.json.encode(refs))
  local okv, decoded = false, nil
  if okp then okv, decoded = pcall(pandoc.json.decode, out, false) end
  if okv and type(decoded) == "table" then
    verdict = decoded
  else -- fail closed: nothing the document names is loaded
    warn("Desk could not check the files this document names (" .. tostring(out):sub(1, 200) .. "); they were left out")
    for _, r in ipairs(refs) do verdict[r.kind .. "\n" .. r.ref] = { refuse = "unchecked" } end
  end

  for key, kind in pairs(META) do
    local v = doc.meta[key]
    if v ~= nil then
      local vals, is_list = meta_values(v)
      local keep = {}
      for _, ref in ipairs(vals) do
        local j = get(kind == "image" and "cover" or kind, ref)
        if j and j.path then keep[#keep + 1] = pandoc.MetaString(j.path)
        elseif j and (j.refuse or j.link) then refused(key, ref, j)
        else keep[#keep + 1] = pandoc.MetaString(ref) end
      end
      if #keep == 0 then doc.meta[key] = nil
      elseif is_list then doc.meta[key] = pandoc.MetaList(keep)
      else doc.meta[key] = keep[1] end
    end
  end
  local html_apply = function(el)
    if el.format:match("html") then
      local new = raw_html(el.text, apply_html)
      if new ~= el.text then
        el.text = new
        return el
      end
    end
  end
  if not embed then return doc end
  return doc:walk({
    Image = function(el)
      local v = get("image", el.src)
      if not v then return nil end
      if v.path and insert(el.src, v) then return nil end
      if v.link then
        refused("image", el.src, v)
        local text = label(el)
        if #text == 0 then text = { pandoc.Str("image") } end
        return pandoc.Link(text, el.src)
      end
      if v.refuse then
        refused("image", el.src, v)
        return label(el)
      end
      return nil
    end,
    RawInline = html_apply,
    RawBlock = html_apply,
    Div = function(el) return attrs_of(el, apply_html) end,
    Span = function(el) return attrs_of(el, apply_html) end,
    Header = function(el) return attrs_of(el, apply_html) end,
  })
end
"""


_ARGS_SEQ = [0]


def jail_args(work: Path, roots: Iterable[str | os.PathLike[str]], *, trusted: Iterable[str | os.PathLike[str]] = (), embed: bool = True, citeproc: bool = False, download: bool = False, offline: bool = False) -> list[str]:
    """pandoc arguments for Desk's jail filter (see the module doc). `roots` are the folders a document may use files
    from, in lookup order (its own folder first); `trusted` files the user named on the command line (bibliography,
    citation style). embed=False when the writer loads no pictures (Markdown, plain HTML …); citeproc=True when
    pandoc runs --citeproc, which reads the bibliography and style the document's metadata names. With download,
    remote pictures are fetched with limits instead of being left to other filters. The filter and its settings are
    written into `work`, which must outlive the pandoc run."""
    work.mkdir(parents=True, exist_ok=True)
    lua = work / "desk-jail.lua"
    if not lua.exists():
        lua.write_text(LUA, encoding="utf-8")
    _ARGS_SEQ[0] += 1
    cfg = work / f"desk-jail-{os.getpid()}-{_ARGS_SEQ[0]}.json"
    cfg.write_text(json.dumps({
        "python": sys.executable or "python3",
        "helper": str(Path(__file__).resolve()),
        "roots": [str(r) for r in resolved(roots)],
        "trusted": [str(Path(t).expanduser().resolve()) for t in trusted],
        "embed": embed,
        "citeproc": citeproc,
        "download": download,
        "offline": offline,
        "work": str(work.resolve()),
    }), encoding="utf-8")
    return [f"--lua-filter={lua.resolve()}", "-M", f"desk-jail={cfg.resolve()}"]


# ── judging references (run by the filter) ─────────────────────────────


_CSS_LOADS = re.compile(rb"url\(\s*['\"]?(?!\s*data:)|@import", re.I)


def judge(ref: str, kind: str, roots: Sequence[Path], trusted: set[str]) -> dict[str, Any]:
    """What the filter does with one reference: {'path', 'mime'} to embed that file, {'refuse': why}, {'remote': True},
    {'missing': True} (pandoc reports it), or {} to leave it alone."""
    from urllib.parse import unquote

    r = ref.strip()
    if r.lower().startswith(("http://", "https://")) or r.startswith("//"):
        return {"remote": True}
    if _is_uri(r):
        return {"refuse": "a URL pandoc must not open"}
    plain = r.split("?", 1)[0].split("#", 1)[0]
    variants = [v for v in dict.fromkeys([r, unquote(r), plain, unquote(plain)]) if v and "\x00" not in v]
    for v in variants:
        cands = [Path(v)] if _absolute(v) else [root / v for root in roots]
        for c in cands:
            try:
                if not c.is_file():
                    continue
                real = c.resolve()
            except (OSError, RuntimeError, ValueError):
                continue
            if str(real) not in trusted and not inside(real, roots):
                return {"refuse": "outside the document's folder"}
            if kind == "css":
                try:
                    with open(real, "rb") as f:
                        if _CSS_LOADS.search(f.read(4 * 1024 * 1024)):
                            return {"refuse": "that loads other files"}
                except OSError:
                    return {"missing": True}
            mime = MIME.get(real.suffix.lower())
            return {"path": str(real), **({"mime": mime} if mime else {})}
    if any(_absolute(v) or _escapes(v) for v in variants):
        return {"refuse": "outside the document's folder"}
    return {"missing": True}


def _sniff(data: bytes) -> str | None:
    head = data[:512]
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if b"<svg" in head.lower():
        return ".svg"
    return None


_REMOTE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/tiff": ".tif"}


def _fetch(url: str, dest: Path, deadline: float) -> dict[str, Any]:
    import hashlib
    import time
    import urllib.request

    full = "https:" + url if url.startswith("//") else url
    left = deadline - time.time()
    if left <= 0.5:
        return {"error": "time budget used up"}
    req = urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0 (Desk)", "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=min(REMOTE_TIMEOUT, left)) as resp:  # noqa: S310 — http(s) only
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            chunks: list[bytes] = []
            size = 0
            while True:  # in pieces, so a server that trickles data cannot outlast the budget
                if time.time() > deadline:
                    return {"error": "time budget used up"}
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > REMOTE_MAX_BYTES:
                    return {"error": f"larger than {REMOTE_MAX_BYTES // (1024 * 1024)} MB"}
    except Exception as e:  # noqa: BLE001 — network, TLS, HTTP errors: the picture becomes a link
        return {"error": str(e)[:160] or type(e).__name__}
    data = b"".join(chunks)
    ext = _REMOTE_EXT.get(ctype) or _sniff(data)
    if not ext:
        return {"error": f"not an image ({ctype or 'unknown type'})"}
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / (hashlib.sha256(full.encode()).hexdigest()[:20] + ext)
    out.write_bytes(data)
    mime = MIME.get(ext)
    return {"path": str(out), **({"mime": mime} if mime else {}), "download": True}


def _download(urls: list[str], dest: Path) -> dict[str, dict[str, Any]]:
    """Fetches on daemon threads and stops waiting at the deadline (socket timeouts do not cover name lookups)."""
    import queue
    import threading
    import time

    deadline = time.time() + REMOTE_BUDGET
    todo: "queue.Queue[str]" = queue.Queue()
    for u in urls:
        todo.put(u)
    done: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def worker() -> None:
        while True:
            try:
                u = todo.get_nowait()
            except queue.Empty:
                return
            res = _fetch(u, dest, deadline)
            with lock:
                done[u] = res

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(6, len(urls)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, deadline - time.time()) + 0.5)
    with lock:
        return {u: done.get(u) or {"error": "time budget used up"} for u in urls}


def _serve(cfg_path: str) -> int:
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    items = json.loads(sys.stdin.read() or "[]")[:MAX_REFS]
    roots = [Path(r) for r in cfg.get("roots") or []]
    trusted = set(cfg.get("trusted") or [])
    out: dict[str, dict[str, Any]] = {}
    remote: list[str] = []
    for it in items:
        kind, ref = str(it.get("kind")), str(it.get("ref"))
        v = judge(ref, kind, roots, trusted)
        if v.get("remote"):
            if kind not in ("image", "cover"):
                v = {"refuse": "a remote file pandoc must not fetch"}
            elif cfg.get("download"):
                remote.append(ref)
                continue
            else:
                v = {}  # another filter (markup-ebooks' desk-remote.lua) handles remote pictures
        out[kind + "\n" + ref] = v
    if remote:
        urls = list(dict.fromkeys(remote))
        offline = cfg.get("offline") or os.environ.get("DESK_OFFLINE", "").lower() in ("1", "true", "yes")
        got = {} if offline else _download(urls[:REMOTE_MAX], Path(cfg["work"]) / "remote")
        for it in items:
            kind, ref = str(it.get("kind")), str(it.get("ref"))
            if ref not in urls or (kind + "\n" + ref) in out:
                continue
            res = got.get(ref)
            if res and "path" in res:
                out[kind + "\n" + ref] = res
            else:
                why = "offline" if offline else (res or {}).get("error", f"more than {REMOTE_MAX} remote pictures")
                out[kind + "\n" + ref] = {"link": True, "warn": f"remote image not downloaded: {ref[:120]} ({why}); kept as a link"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(_serve(sys.argv[1]))
