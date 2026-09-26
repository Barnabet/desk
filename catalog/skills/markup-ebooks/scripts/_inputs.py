"""Input preparation for markup-ebooks: what pandoc reads instead of a file it would mishandle.

* AsciiDoc with preprocessor directives (ifdef::, ifeval::, include:: with tags or leveloffset), which pandoc's reader
  does not evaluate: evaluated the way Asciidoctor does (_adoc.py).
* HTML with <iframe src=…>: pandoc's reader downloads and parses every frame while reading, with no timeout. Frames
  become links, so reading never touches the network.
* Markdown that nests brackets or quote markers absurdly deep: pandoc's Markdown reader takes exponential time on
  nested '[' (10 levels take seconds, 16 never finish), so brackets past MAX_BRACKETS levels are escaped and quote
  markers past MAX_QUOTES are dropped, with a warning.
* Binary data given as a text document is refused before pandoc spends minutes on it.
* Includes (reStructuredText, Org, LaTeX, Typst, AsciiDoc) are inlined here when their target lies inside the
  document's folder or an explicit --resource-path folder, and left out otherwise (_pandoc_safe.py, _adoc.py): a
  stranger's document must never pull the user's own files into what Desk reads or writes.

The prepared copy goes to a temp folder under the same name; callers keep the originals' folders on pandoc's resource
path, so relative images still resolve. Inputs that need nothing are returned as they are. `prepare` then reads
formats that can name other files (everything but Markdown, HTML and self-contained binary formats) with pandoc's
--sandbox into its JSON form, so the conversion itself never follows an include Desk did not inline.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from _common import SkillError

_IFRAME = re.compile(r"<iframe\b([^>]*)>(.*?)</iframe\s*>|<iframe\b([^>]*?)/?>", re.I | re.S)
_ATTR = re.compile(r"""\b(src|title)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_WORK: list[Path] = []
_INCLUDED: list[Path] = []


_DERIVED: list[Path] = []


def register_work_dir(d: Path) -> Path:
    """Marks a temp folder of derived files (left out of cache manifests)."""
    _DERIVED.append(d)
    return d


def work_dirs() -> list[Path]:
    return [*_WORK, *_DERIVED]


def included_files() -> list[Path]:
    """Files AsciiDoc include:: directives pulled in during this run (for cache manifests)."""
    return list(_INCLUDED)


def _work() -> Path:
    if not _WORK:
        import atexit
        import shutil

        d = Path(tempfile.mkdtemp(prefix="desk-inputs-"))
        atexit.register(shutil.rmtree, d, True)
        _WORK.append(d)
    return _WORK[0]


def _frame_link(m: re.Match[str]) -> str:
    attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(m.group(1) or m.group(3) or "")}
    src = attrs.get("src", "")
    if not src or src.startswith(("about:", "javascript:")):
        return ""
    title = attrs.get("title") or "embedded content"
    return f'<p><a href="{src}">{title}</a></p>'


def neutralize_iframes(text: str) -> str:
    return _IFRAME.sub(_frame_link, text)


MAX_BRACKETS = 3
MAX_QUOTES = 32
MD_READERS = {"markdown", "gfm", "commonmark", "commonmark_x", "markdown_strict", "markdown_mmd", "markdown_phpextra", "markdown_github"}
BINARY_READERS = {"docx", "odt", "pptx", "xlsx", "epub", "ipynb"}
_CONTROL = bytes(range(0, 9)) + bytes(range(14, 27)) + bytes(range(28, 32))


def looks_binary(data: bytes) -> bool:
    """True for bytes that are not text: NUL bytes (outside UTF-16) or more than 2% control characters."""
    sample = data[:65536]
    if not sample:
        return False
    if sample[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False
    if b"\x00" in sample:
        return True
    return sum(sample.count(bytes([c])) for c in _CONTROL) > len(sample) * 0.02


def refuse_binary(path: Path, reader: str) -> None:
    """Raises when a text-format input is binary data (read from its first 64 KB)."""
    if reader.split("+")[0].split("-")[0] in BINARY_READERS:
        return
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError:
        return
    if looks_binary(head):
        raise SkillError(f"{path.name} is binary data, not a {reader.split('+')[0]} document (it holds NUL or control bytes); check the file, or read it with the file-inspector skill")


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_QUOTES = re.compile(r"^((?: {0,3}>){%d,})" % (MAX_QUOTES + 1))


def tame_markdown(text: str) -> tuple[str, list[str]]:
    """Escapes '[' nested more than MAX_BRACKETS deep (1 in a paragraph holding more than 200 of them) and drops
    quote markers past MAX_QUOTES (see the module doc). Fenced code is left alone. Returns (text, warnings); the text
    is unchanged when nothing needed taming."""
    if text.count("[") <= MAX_BRACKETS and ">" * (MAX_QUOTES + 1) not in text.replace(" ", ""):
        return text, []
    lines = text.split("\n")
    escaped = trimmed = 0
    fence: str | None = None
    para: list[int] = []  # indexes of the current paragraph's lines

    def flush() -> None:
        nonlocal escaped
        if not para:
            return
        n = sum(lines[i].count("[") for i in para)
        limit = 1 if n > 200 else MAX_BRACKETS
        if n > limit:
            depth = 0
            for i in para:
                line = lines[i]
                if "[" not in line and "]" not in line:
                    continue
                out = []
                code = False
                esc = False
                for ch in line:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == "`":
                        code = not code
                    elif not code and ch == "[":
                        depth += 1
                        if depth > limit:
                            out.append("\\")
                            escaped += 1
                    elif not code and ch == "]" and depth:
                        depth -= 1
                    out.append(ch)
                lines[i] = "".join(out)
        para.clear()

    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            flush()
            fence = m.group(1)
            continue
        if not line.strip():
            flush()  # brackets never span paragraphs
            continue
        q = _QUOTES.match(line)
        if q:
            lines[i] = ">" * MAX_QUOTES + " " + line[q.end() :].lstrip()
            trimmed += 1
        para.append(i)
    flush()
    warnings = []
    if escaped:
        warnings.append(f"{escaped} '[' nested too deep were escaped (pandoc's Markdown reader cannot parse such nesting in reasonable time)")
    if trimmed:
        warnings.append(f"{trimmed} line(s) quoted more than {MAX_QUOTES} levels deep were cut to {MAX_QUOTES} levels")
    return ("\n".join(lines), warnings) if escaped or trimmed else (text, [])


def jail_roots(inputs: Sequence[Path], extra: Iterable[str] | None = None) -> list[Path]:
    """The folders a document may take includes and pictures from: its own folder(s) and the --resource-path folders
    the caller named (the current folder for stdin)."""
    from _pandoc_safe import resolved

    return resolved([*(i.resolve().parent for i in inputs), *(Path(e).expanduser() for e in extra or [])] or [Path.cwd()])


def pandoc_inputs(inputs: Sequence[Path], reader: str, warnings: list[str] | None = None, roots: Sequence[Path] | None = None) -> list[Path]:
    """The files pandoc should read for these inputs (see the module doc). Includes may only come from `roots`
    (default: the inputs' own folders)."""
    base = reader.split("+")[0].split("-")[0]
    for src in inputs:
        refuse_binary(src, reader)
    from _pandoc_safe import INCLUDE_READERS

    if base not in ("asciidoc", "asciidoctor", "html") and base not in MD_READERS and base not in INCLUDE_READERS:
        return list(inputs)
    if roots is None:
        roots = jail_roots(inputs)
    out: list[Path] = []
    for k, src in enumerate(inputs):
        try:
            raw = src.read_bytes()
        except OSError:
            out.append(src)
            continue
        new: str | bytes | None = None
        if base in MD_READERS:
            if raw.count(b"[") > MAX_BRACKETS or b">" * (MAX_QUOTES + 1) in raw.replace(b" ", b""):
                from mk_read import read_text_file

                text = read_text_file(src)
                tamed, warns = tame_markdown(text)
                if warns:
                    new = tamed.encode("utf-8")
                    if warnings is not None:
                        warnings.extend(warns)
        elif base == "html":
            if re.search(rb"<iframe\b", raw, re.I):
                from _html import sniff_encoding

                enc = sniff_encoding(raw[: 1 << 20])
                text = raw.decode(enc, "replace")
                new = neutralize_iframes(text).encode(enc, "xmlcharrefreplace")
        elif base in INCLUDE_READERS:
            from _pandoc_safe import has_includes, inline_includes, read_text

            text = read_text(src)
            if has_includes(text, base):
                warns: list[str] = []
                try:
                    body = inline_includes(text, base, src.resolve().parent, roots, warns, _INCLUDED)
                except ValueError as e:
                    raise SkillError(f"{src.name}: {e}; refusing it") from None
                new = body.encode("utf-8")
                if warnings is not None:
                    warnings.extend(warns)
        else:
            from _adoc import DIRECTIVE, preprocess

            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", "replace")
            if DIRECTIVE.search(text):
                body, warns, included = preprocess(text, src.resolve().parent, roots=roots)
                _INCLUDED.extend(included)
                new = body.encode("utf-8")
                if warnings is not None:
                    warnings.extend(warns)
        if new is None:
            out.append(src)
            continue
        dest = _work() / str(k) / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new)
        _ORIGINALS[str(dest.resolve())] = str(src)
        out.append(dest)
    return out


_ORIGINALS: dict[str, str] = {}


def prepare(inputs: Sequence[Path], reader: str, warnings: list[str], roots: Sequence[Path], stdin_text: str | None = None) -> tuple[list[Path], str]:
    """(the files pandoc should convert, the -f reader for them). Formats that can name other files are read here with
    --sandbox into pandoc's JSON (after pandoc_inputs inlined the includes allowed), so the conversion that follows
    reads only the pictures Desk's jail filter hands it. Returns ([], reader) for stdin that needs nothing."""
    from _pandoc_safe import SELF_CONTAINED_READERS, base_reader

    paths = pandoc_inputs(inputs, reader, warnings, roots)
    if base_reader(reader) in SELF_CONTAINED_READERS:
        return paths, reader
    from _mk import pandoc

    work = _work()
    _SEQ[0] += 1
    dest = work / f"sandboxed-{_SEQ[0]}.json"
    try:
        _, warns = pandoc(["--sandbox", *([str(p.resolve()) for p in paths] or ["-"]), "-f", reader, "-t", "json", "-o", str(dest)], input=stdin_text.encode("utf-8") if stdin_text is not None and not paths else None)
    except SkillError as e:
        msg = str(e)
        if "not found in resource path" in msg or "Could not" in msg:
            msg += " (Desk reads documents sandboxed: only includes inside the document's folder are followed, and Desk inlines them for reStructuredText, Org, LaTeX, Typst and AsciiDoc)"
        raise SkillError(msg) from None
    warnings.extend(sandbox_warnings(warns))
    if inputs:
        _ORIGINALS[str(dest.resolve())] = str(inputs[0])
    return [dest], "json"


_SEQ = [0]


def sandbox_warnings(warns: Sequence[str]) -> list[str]:
    """pandoc's warnings from a --sandbox read, with includes it refused said plainly."""
    out = []
    for w in warns:
        m = re.match(r"Could not load include file (.+?)(?: at .*)?$", w)
        out.append(f"include {m.group(1)} not read (only includes inside the document's folder are followed)" if m else w)
    return out


def original_names(text: str) -> str:
    """pandoc's messages name the prepared copies: put the user's file names back."""
    for tmp, orig in _ORIGINALS.items():
        if tmp in text:
            text = text.replace(tmp, orig)
    return text
