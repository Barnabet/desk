"""Shared machinery for markup-ebooks: formats, pandoc runs and warnings, the pandoc → Typst → PDF pipeline with
Desk's templates, Markdown outlines with stable section addresses, text budgets, and the per-file cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from _common import SkillError, UsageError, human_size, run_tool

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
CACHE_VERSION = "6"
CACHE_MIN_BYTES = 64 * 1024  # smaller inputs convert faster than a cache round trip is worth
BIG_BYTES = 2 * 1024 * 1024  # above this, reads start with a map

# Extension → pandoc reader.
READERS: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown", ".mdown": "markdown", ".mkd": "markdown", ".mkdn": "markdown",
    ".mdwn": "markdown", ".qmd": "markdown", ".rmd": "markdown", ".txt": "markdown", ".text": "markdown",
    ".html": "html", ".htm": "html", ".xhtml": "html", ".shtml": "html", ".mhtml": "html",
    ".rst": "rst", ".rest": "rst", ".tex": "latex", ".latex": "latex", ".ltx": "latex",
    ".org": "org", ".adoc": "asciidoc", ".asciidoc": "asciidoc", ".asc": "asciidoc", ".typ": "typst",
    ".textile": "textile", ".mediawiki": "mediawiki", ".wiki": "mediawiki", ".dbk": "docbook", ".docbook": "docbook",
    ".jats": "jats", ".epub": "epub", ".fb2": "fb2", ".ipynb": "ipynb", ".rtf": "rtf", ".docx": "docx",
    ".odt": "odt", ".opml": "opml", ".muse": "muse", ".t2t": "t2t", ".dj": "djot", ".djot": "djot",
    ".bib": "bibtex", ".bibtex": "bibtex", ".ris": "ris", ".csv": "csv", ".tsv": "tsv", ".json": "json",
    ".pod": "pod", ".creole": "creole", ".man": "man", ".1": "man", ".vimwiki": "vimwiki", ".xml": "xml?",
    ".pptx": "pptx", ".xlsx": "xlsx",
}
# Extension → pandoc writer (PDF goes through Typst).
WRITERS: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown", ".gfm": "gfm", ".html": "html5", ".htm": "html5", ".xhtml": "html5",
    ".rst": "rst", ".tex": "latex", ".latex": "latex", ".org": "org", ".adoc": "asciidoc", ".asciidoc": "asciidoc",
    ".typ": "typst", ".textile": "textile", ".mediawiki": "mediawiki", ".wiki": "mediawiki", ".dbk": "docbook5",
    ".docbook": "docbook5", ".xml": "docbook5", ".jats": "jats", ".epub": "epub3", ".fb2": "fb2", ".ipynb": "ipynb",
    ".rtf": "rtf", ".docx": "docx", ".odt": "odt", ".pptx": "pptx", ".txt": "plain", ".text": "plain", ".pdf": "pdf",
    ".json": "json", ".opml": "opml", ".icml": "icml", ".ms": "ms", ".man": "man", ".1": "man", ".texi": "texinfo",
    ".dj": "djot", ".djot": "djot", ".bib": "biblatex", ".muse": "muse", ".jira": "jira", ".xwiki": "xwiki",
    ".zim": "zimwiki", ".dokuwiki": "dokuwiki", ".tei": "tei", ".bbcode": "bbcode", ".ctx": "context",
}
WRITER_ALIASES = {"md": "markdown", "html": "html5", "epub": "epub3", "text": "plain", "txt": "plain", "tex": "latex", "docbook": "docbook5", "adoc": "asciidoc", "wiki": "mediawiki", "typ": "typst", "py": "py"}
BINARY_WRITERS = {"docx", "odt", "pptx", "epub", "epub2", "epub3", "fb2", "pdf"}
EXT_FOR_WRITER = {"markdown": ".md", "gfm": ".md", "commonmark": ".md", "commonmark_x": ".md", "markdown_strict": ".md", "html5": ".html", "html": ".html", "html4": ".html", "chunkedhtml": ".zip", "epub3": ".epub", "epub2": ".epub", "epub": ".epub", "plain": ".txt", "latex": ".tex", "beamer": ".tex", "docbook5": ".xml", "docbook4": ".xml", "docbook": ".xml", "jats": ".xml", "typst": ".typ", "asciidoc": ".adoc", "asciidoctor": ".adoc", "mediawiki": ".wiki", "revealjs": ".html", "slidy": ".html", "dzslides": ".html", "s5": ".html", "texinfo": ".texi", "context": ".tex"}

PAPERS = {"a4": "a4", "a3": "a3", "a5": "a5", "a6": "a6", "b5": "iso-b5", "letter": "us-letter", "us-letter": "us-letter", "legal": "us-legal", "us-legal": "us-legal", "executive": "us-executive", "tabloid": "us-tabloid", "trade": None}
STYLES = ("article", "report", "book")


def pandoc_exe() -> str:
    from _render import pandoc_path

    return pandoc_path()


def sniff_xml(path: Path) -> str | None:
    """Which XML vocabulary a .xml file is: docbook, jats, fb2, html, opml, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None
    head = re.sub(r"<!--.*?-->", "", head, flags=re.S)
    m = re.search(r"<(?!\?|!)([A-Za-z_][\w:.-]*)", head)
    root = (m.group(1).split(":")[-1].lower() if m else "")
    if root == "fictionbook":
        return "fb2"
    if root == "html":
        return "html"
    if root == "opml":
        return "opml"
    if "docbook" in head.lower() or root in ("book", "chapter", "section", "sect1", "refentry", "set", "part") or ("article" == root and "jats" not in head.lower() and "<front" not in head):
        return "docbook"
    if "jats" in head.lower() or "journalpublishing" in head.lower() or (root == "article" and ("<front" in head or "article-type" in head)):
        return "jats"
    return None


ZIP_READERS = {"epub": False, "docx": True, "odt": True, "pptx": True, "xlsx": True}  # reader → Office package (no DTDs)


def detect_reader(path: Path | None, override: str | None = None) -> str:
    """The pandoc reader for an input; zip-based inputs (EPUB, DOCX, ODT, …) are checked for zip bombs first."""
    fmt = override or _detect_reader(path)
    if path is not None and fmt.split("+")[0].split("-")[0] in ZIP_READERS:
        from _common import check_zip

        check_zip(path, refuse_dtd=ZIP_READERS[fmt.split("+")[0].split("-")[0]])
        from _zipsafe import verify_zip

        verify_zip(path)  # members that lie about their size are refused before pandoc inflates them
    return fmt


def _detect_reader(path: Path | None) -> str:
    if path is None:
        return "markdown"
    ext = path.suffix.lower()
    fmt = READERS.get(ext)
    if fmt == "xml?" or (fmt is None and ext in (".xml", "")):
        sniffed = sniff_xml(path)
        if sniffed:
            return sniffed
        raise UsageError(f"cannot tell which XML vocabulary {path.name} is (DocBook, JATS, FB2, OPML): pass --from docbook|jats|fb2 (generic XML data belongs to the data-files skill)")
    if fmt is None:
        raise UsageError(f"unknown input type '{ext or path.name}': pass --from (markdown, html, rst, latex, org, asciidoc, typst, docbook, jats, …; see --list-formats)")
    return fmt


def writer_for(out: Path | None, to: str | None) -> str:
    if to:
        w = WRITER_ALIASES.get(to.lower(), to.lower())
        return w
    if out is None:
        raise UsageError("give an output path or --to FORMAT")
    ext = out.suffix.lower()
    w = WRITERS.get(ext)
    if not w:
        raise UsageError(f"cannot tell the output format from '{out.name}': pass --to (md, html, pdf, docx, epub, odt, rst, tex, typ, …)")
    return w


def ext_for(writer: str) -> str:
    base = writer.split("+")[0].split("-")[0]
    if base in EXT_FOR_WRITER:
        return EXT_FOR_WRITER[base]
    for ext, w in WRITERS.items():
        if w == base:
            return ext
    return "." + base


# ── pandoc ──────────────────────────────────────────────────────────────


def pandoc_warnings(stderr: bytes | str) -> list[str]:
    """pandoc's [WARNING] lines, shortened and deduplicated."""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    out: list[str] = []
    cur: list[str] = []

    def done() -> None:
        if cur:
            msg = " ".join(s.strip() for s in cur)
            msg = re.sub(r"^Scripting warning at .*? line \d+ column \d+:\s*", "", msg)  # Desk's own Lua filter
            m = re.match(r"Could not fetch resource (\S+?)(?::|\s|$)", msg)
            if m:
                msg = f"missing image or resource: {m.group(1).strip()} (replaced by its description)"
            m = re.match(r"Citeproc: citation (\S+) not found", msg)
            if m:
                msg = f"unresolved citation: @{m.group(1)} (not in the bibliography)"
            if "Duplicate identifier" in msg or "Duplicate note reference" in msg:
                msg = re.sub(r"\s+", " ", msg)
            from _inputs import original_names

            msg = original_names(msg)
            if msg not in out:
                out.append(msg[:300])
            cur.clear()

    for line in text.splitlines():
        if line.startswith("[WARNING]"):
            done()
            cur.append(line[len("[WARNING]") :].strip())
        elif line.startswith("["):
            done()
        elif cur and line.startswith(" "):
            cur.append(line)
        else:
            done()
    done()
    return out


def pandoc_error(stderr: bytes) -> str:
    from _inputs import original_names

    lines = [ln for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip() and not ln.startswith(("[WARNING]", "[INFO]", "HasCallStack", "  "))]
    return original_names(" ".join(lines[-4:]))[:700] or "unknown error"


_LAST_LOG: list[str] = [""]


def pandoc(args: Sequence[str], cwd: Path | None = None, input: bytes | None = None, timeout: float = 600, verbose: bool = False) -> tuple[bytes, list[str]]:
    """Runs the bundled pandoc; returns (stdout, warnings). Raises SkillError with pandoc's message on failure.

    With verbose, pandoc's full log is kept in last_log() (it names every resource it loaded). The timeout shrinks
    with the input (2 minutes plus 90 s per MB), so a small but pathological document fails fast."""
    size = len(input or b"")
    for x in args:
        if not x.startswith("-"):
            f = Path(x) if cwd is None or Path(x).is_absolute() else Path(cwd) / x
            try:
                size += f.stat().st_size if f.is_file() else 0
            except OSError:
                pass
    timeout = min(timeout, 120 + 90 * size / (1024 * 1024))
    r = run_tool([pandoc_exe(), *(["--verbose"] if verbose else []), *args], cwd=cwd, input=input, timeout=timeout, check=False)
    _LAST_LOG[0] = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise SkillError("pandoc failed: " + pandoc_error(r.stderr))
    return r.stdout, pandoc_warnings(r.stderr)


def last_log() -> str:
    return _LAST_LOG[0]


def reader_spec(fmt: str, smart: bool = True, citations: bool = False) -> str:
    """The -f value: Markdown gets smart quotes and, without a bibliography, no citation parsing (keeps @handles)."""
    if fmt == "markdown":
        return "markdown" + ("+smart" if smart else "") + ("" if citations else "-citations")
    return fmt


XREF_RE = re.compile(r"(?<![\w@])@(?:sec|fig|tbl)-\w")


def has_xrefs(inputs: Sequence[Path], stdin_text: str | None = None) -> bool:
    """True when Markdown inputs use @sec-/@fig-/@tbl- cross-references (they need pandoc's citation syntax)."""
    if stdin_text is not None and XREF_RE.search(stdin_text):
        return True
    for p in inputs:
        try:
            with open(p, "rb") as f:
                if XREF_RE.search(f.read(16 * 1024 * 1024).decode("utf-8", "replace")):
                    return True
        except OSError:
            continue
    return False


def list_formats() -> dict[str, list[str]]:
    exe = pandoc_exe()
    ins = run_tool([exe, "--list-input-formats"]).stdout.decode().split()
    outs = run_tool([exe, "--list-output-formats"]).stdout.decode().split()
    return {"readers": ins, "writers": outs + ["pdf (through Typst)"]}


# ── Typst ───────────────────────────────────────────────────────────────


def typst_length(value: str) -> str:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(pt|mm|cm|in|em)?\s*", value.lower())
    if not m:
        raise UsageError(f"bad length '{value}' (use e.g. 2cm, 1in, 20mm or 11pt)")
    return f"{m.group(1)}{m.group(2) or 'pt'}"


def typst_margin(spec: str) -> str:
    parts = [typst_length(x) for x in re.split(r"[,\s]+", spec.strip()) if x]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"(y: {parts[0]}, x: {parts[1]})"
    if len(parts) == 4:
        return f"(top: {parts[0]}, right: {parts[1]}, bottom: {parts[2]}, left: {parts[3]})"
    raise UsageError("--margin takes 1, 2 (vertical,horizontal) or 4 (top,right,bottom,left) values")


def typst_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def color_hex(spec: str) -> str:
    named = {"blue": "1f4e79", "navy": "1b2a4a", "teal": "0f6e6e", "green": "2e6b30", "red": "9b1c1c", "purple": "5b2a86", "orange": "b35900", "gray": "444444", "grey": "444444", "black": "111111"}
    s = spec.strip().lower().lstrip("#")
    s = named.get(s, s)
    if re.fullmatch(r"[0-9a-f]{3}", s):
        s = "".join(c * 2 for c in s)
    if not re.fullmatch(r"[0-9a-f]{6}", s):
        raise UsageError(f"bad colour '{spec}' (use #1f4e79 or a name like teal)")
    return "#" + s


def add_doc_args(p: Any, pdf: bool = True) -> None:
    """Options shared by mk_convert and mk_render: metadata, TOC, numbering, citations, and PDF layout."""
    g = p.add_argument_group("document")
    g.add_argument("--from", dest="from_format", help="input format when the extension is unusual (markdown, gfm, html, rst, latex, org, asciidoc, typst, docbook, jats, textile, mediawiki, …)")
    g.add_argument("--toc", action="store_true", help="table of contents")
    g.add_argument("--toc-depth", type=int, default=3, help="heading levels in the TOC (default 3)")
    g.add_argument("--number-sections", "-N", action="store_true", help="number headings 1, 1.1, …")
    g.add_argument("--metadata", "-M", action="append", default=[], metavar="K=V", help="document metadata (title=…, author=…, date=…, lang=…); repeatable")
    g.add_argument("--metadata-file", help="YAML or JSON file with metadata")
    g.add_argument("--title", help="shortcut for --metadata title=…")
    g.add_argument("--author", action="append", help="author (repeat for several)")
    g.add_argument("--date", help="date text ('today' for today)")
    g.add_argument("--lang", help="document language (en, fr, de-CH, …): hyphenation, quotes, labels")
    g.add_argument("--bibliography", action="append", help=".bib, .json (CSL JSON), .yaml or .ris file: [@key] citations are resolved (citeproc)")
    g.add_argument("--csl", help="citation style (.csl file), e.g. apa.csl, ieee.csl; default Chicago author-date")
    g.add_argument("--resource-path", action="append", help="extra folder to find images and includes in (the input's folder is always searched)")
    g.add_argument("--shift-heading-level-by", type=int, help="shift heading levels (e.g. -1 makes ## the top level)")
    if pdf:
        t = p.add_argument_group("PDF and rendering (Typst)")
        t.add_argument("--template", help="article (default), report or book; or your own Typst template (.typ with $body$) — see references/templates.md")
        t.add_argument("--paper", help="a4 (article/report default), a5 (book default), letter, legal, a3, or WxH like 6x9in")
        t.add_argument("--margin", help="2.2cm | vertical,horizontal | top,right,bottom,left")
        t.add_argument("--columns", type=int, help="text columns (article)")
        t.add_argument("--font", help="body font family (installed), or a .ttf/.otf file; falls back to Libertinus Serif")
        t.add_argument("--heading-font", help="heading font family")
        t.add_argument("--code-font", help="monospace font family")
        t.add_argument("--font-size", help="body size, e.g. 10.5pt")
        t.add_argument("--line-spacing", type=float, help="line spacing factor (1.0 default)")
        t.add_argument("--font-path", action="append", help="folder with extra fonts")
        t.add_argument("--header", help="running header; {page} {pages} {title} {section} {author} {date} are filled in; '' for none")
        t.add_argument("--footer", help="footer, e.g. 'Draft · {page} of {pages}'; '' for none")
        t.add_argument("--accent", help="accent colour for headings and links (#1f4e79, or blue, teal, green, red, purple, gray)")
        t.add_argument("--no-justify", action="store_true", help="ragged-right paragraphs")
        t.add_argument("--cover", help="book: a cover image as the first page")
        t.add_argument("--lof", action="store_true", help="list of figures after the TOC")
        t.add_argument("--lot", action="store_true", help="list of tables after the TOC")
    g.add_argument("--main", action="store_true", help="HTML input: only the main content (the article html_extract.py finds, with its title, byline and date), not navigation and page chrome")
    g.add_argument("--offline", action="store_true", help="do not download remote (http) images; they become links (default: downloaded with a 30 s budget)")


def metadata_args(a: Any) -> list[str]:
    args: list[str] = []
    for kv in a.metadata or []:
        if "=" not in kv and ":" not in kv:
            raise UsageError(f"--metadata takes KEY=VALUE, got '{kv}'")
        args += ["-M", kv.replace(":", "=", 1) if "=" not in kv else kv]
    if a.metadata_file:
        mf = Path(a.metadata_file).expanduser()
        if not mf.is_file():
            raise SkillError(f"{mf} does not exist")
        args += [f"--metadata-file={mf.resolve()}"]
    if a.title:
        args += ["-M", f"title={a.title}"]
    for au in a.author or []:
        args += ["-M", f"author={au}"]
    if a.date:
        d = a.date
        if d.lower() == "today":
            from datetime import date

            t = date.today()
            d = f"{t.day} {t.strftime('%B %Y')}"
        args += ["-M", f"date={d}"]
    if a.lang:
        args += ["-M", f"lang={a.lang}"]
    return args


def citation_args(a: Any) -> list[str]:
    args: list[str] = []
    for b in a.bibliography or []:
        bp = Path(b).expanduser()
        if not bp.is_file():
            raise SkillError(f"bibliography {bp} does not exist")
        args += [f"--bibliography={bp.resolve()}"]
    if a.csl:
        cp = Path(a.csl).expanduser()
        if not cp.is_file():
            raise SkillError(f"citation style {cp} does not exist (download a .csl file from the Zotero style repository, or omit --csl)")
        args += [f"--csl={cp.resolve()}"]
    if a.bibliography or a.csl:
        args += ["--citeproc"]
    return args


def extra_args(a: Any) -> list[str]:
    """--extra pandoc arguments; a relative file in `--opt=FILE` becomes absolute (pandoc runs in a build folder)."""
    out: list[str] = []
    for x in getattr(a, "extra", None) or []:
        if x.startswith("--") and "=" in x:
            k, v = x.split("=", 1)
            f = Path(v).expanduser()
            if v and not f.is_absolute() and f.is_file():
                x = f"{k}={f.resolve()}"
        out.append(x)
    return out


def extra_key(a: Any) -> list[str]:
    """--extra for a cache key: files named in it by content, so editing a Lua filter rebuilds."""
    from _cache import fingerprint

    out = []
    for x in extra_args(a):
        v = x.split("=", 1)[1] if x.startswith("--") and "=" in x else ""
        out.append(x.split("=", 1)[0] + "=" + fingerprint(v) if v and Path(v).is_file() else x)
    return out


def resource_path(inputs: Sequence[Path], extra: Iterable[str] | None) -> str:
    dirs: list[str] = []
    for i in inputs:
        d = str(i.resolve().parent)
        if d not in dirs:
            dirs.append(d)
    for e in extra or []:
        dirs.append(str(Path(e).expanduser().resolve()))
    dirs.append(".")
    return os.pathsep.join(dirs)


def style_of(a: Any) -> tuple[str, Path | None]:
    """(built-in style, custom template path or None)."""
    t = getattr(a, "template", None)
    if not t:
        return "article", None
    if t.lower() in STYLES:
        return t.lower(), None
    tp = Path(t).expanduser()
    if not tp.is_file():
        raise UsageError(f"--template: '{t}' is neither article, report, book nor an existing template file")
    return "article", tp.resolve()


def typst_vars(a: Any, style: str, font_paths: list[str]) -> list[str]:
    v = ["-V", f"desk-style={style}"]
    if a.toc:
        v += ["-V", "desk-toc=true", "-V", f"desk-toc-depth={a.toc_depth}"]
    if getattr(a, "lof", False):
        v += ["-V", "desk-lof=true"]
    if getattr(a, "lot", False):
        v += ["-V", "desk-lot=true"]
    if a.number_sections:
        v += ["-V", "desk-number=true"]
    if a.paper:
        key = a.paper.lower().strip()
        if key in PAPERS and PAPERS[key]:
            v += ["-V", f"desk-paper={PAPERS[key]}"]
        else:
            if key == "trade":
                key = "6x9in"
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(mm|cm|in|pt)?", key)
            if not m:
                raise UsageError(f"unknown --paper '{a.paper}' (a4, a5, letter, legal, a3, or WxH like 6x9in or 148x210mm)")
            unit = m.group(3) or "mm"
            v += ["-V", f"desk-page-width={m.group(1)}{unit}", "-V", f"desk-page-height={m.group(2)}{unit}"]
    if a.margin:
        v += ["-V", f"desk-margin={typst_margin(a.margin)}"]
    if a.columns and a.columns > 1:
        v += ["-V", f"desk-columns={min(a.columns, 4)}"]
    if a.font:
        v += ["-V", f"desk-font={typst_str(font_family(a.font, font_paths))}"]
    if a.heading_font:
        v += ["-V", f"desk-heading-font={typst_str(font_family(a.heading_font, font_paths))}"]
    if a.code_font:
        v += ["-V", f"desk-code-font={typst_str(font_family(a.code_font, font_paths))}"]
    if a.font_size:
        v += ["-V", f"desk-fontsize={typst_length(a.font_size)}"]
    if a.line_spacing:
        v += ["-V", f"desk-linestretch={max(0.6, min(a.line_spacing, 3.0))}"]
    if a.lang:
        lang, _, region = a.lang.replace("_", "-").partition("-")
        v += ["-V", f"desk-lang={lang.lower()}"]
        if region:
            v += ["-V", f"desk-region={region.upper()}"]
    if a.header is not None:
        v += ["-V", f"desk-header={typst_str(a.header)}"]
    if a.footer is not None:
        v += ["-V", f"desk-footer={typst_str(a.footer)}"]
    if a.accent:
        v += ["-V", f"desk-accent={color_hex(a.accent)}"]
    if a.no_justify:
        v += ["-V", "desk-nojustify=true"]
    return v


def font_family(spec: str, font_paths: list[str]) -> str:
    p = Path(spec).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.exists():
        font_paths.append(str(p.resolve().parent))
        try:
            from PIL import ImageFont

            return ImageFont.truetype(str(p), 12).getname()[0]
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"cannot read the font file {p}: {e}") from e
    return spec


TYPST_IMAGE_OK = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}


_IMAGE_CALL = re.compile(r'(image\()"((?:[^"\\]|\\.)*)"')
_MISSING_DEF = "#let _desk_missing_image(..args) = box(width: 6cm, height: 2cm, stroke: 0.5pt + luma(150), inset: 6pt, align(center + horizon, text(size: 8pt, fill: luma(110))[missing image]))\n"
TYPST_RASTER = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def _image_ref(build: Path, ref: str) -> Path:
    raw = ref.replace('\\"', '"').replace("\\\\", "\\")
    return (build / raw) if not Path(raw).is_absolute() else Path(raw)


def _raw_block_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of the fenced raw blocks of a Typst source."""
    if "```" not in text:
        return []
    return [(m.start(), m.end()) for m in re.finditer(r"^[ \t]*(`{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", text, re.M | re.S)]


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    import bisect

    k = bisect.bisect_right([a for a, _ in spans], pos) - 1
    return k >= 0 and spans[k][0] <= pos < spans[k][1]


IMAGE_DPI = 110  # a picture without a size is shown as if it had this resolution (Typst assumes 72: too big)
_TEXT_WIDTH_PT = 465.0  # A4 with Desk's margins; percentages keep the same proportion on other page sizes
_MAX_IMAGE_PT = 330.0  # the tallest a picture without a size may be (about half a page of text)


def _image_width(w: int, h: int) -> str:
    """', width: N%' for a raster picture with no size of its own: its size at IMAGE_DPI, no wider than the text and
    no taller than about half a page. '' when it would fill the width anyway."""
    if w <= 0 or h <= 0:
        return ""
    natural = w * 72 / IMAGE_DPI
    width = min(natural, _TEXT_WIDTH_PT, _MAX_IMAGE_PT * w / h)
    pct = round(100 * width / _TEXT_WIDTH_PT, 1)
    return "" if pct >= 99 else f", width: {max(pct, 0.5):g}%"


def fix_typst_images(main: Path, build: Path) -> list[str]:
    """Makes every image Typst will load one it can decode: GIF, TIFF, BMP, ICO, AVIF, CMYK JPEG and files whose
    content does not match their extension become PNG; missing or unreadable ones become a visible placeholder.
    Returns warnings."""
    text = main.read_text(encoding="utf-8")
    warnings: list[str] = []
    changed = False
    done: dict[str, str | None] = {}
    sizes: dict[str, tuple[int, int]] = {}

    def convert(p: Path) -> Path | None:
        try:
            from PIL import Image

            out = p.with_name(p.name + ".desk.png")
            with Image.open(p) as im:
                im.seek(0)
                im.convert("RGBA" if im.mode in ("RGBA", "LA", "P", "PA") or "transparency" in im.info else "RGB").save(out)
            return out
        except Exception:  # noqa: BLE001 — unreadable: the caller shows a placeholder
            return None

    raw_spans = _raw_block_spans(text)

    def repl(m: re.Match[str]) -> str:
        nonlocal changed
        ref = m.group(2)
        if raw_spans and _inside(raw_spans, m.start()):
            return m.group(0)  # Typst code shown in a code block, not an image
        if ref in done:
            new_ref = done[ref]
        else:
            p = _image_ref(build, ref)
            ext = p.suffix.lower()
            new_ref = ref
            if not p.is_file():
                warnings.append(f"missing image: {ref}")
                new_ref = None
            elif ext in (".svg", ".pdf"):
                pass  # checked one by one only if the compile fails (see quarantine_images)
            else:
                fmt, mode = None, None
                try:
                    from PIL import Image

                    with Image.open(p) as im:
                        fmt, mode = im.format, im.mode
                        sizes[ref] = im.size
                except Exception:  # noqa: BLE001
                    fmt = None
                if fmt in TYPST_RASTER and not (fmt == "JPEG" and mode == "CMYK") and TYPST_RASTER[fmt] == (".jpg" if ext == ".jpeg" else ext):
                    pass
                else:
                    out = convert(p)
                    if out is None:
                        warnings.append(f"image Typst cannot show: {ref} (left out)")
                        new_ref = None
                    else:
                        new_ref = typst_str(out.relative_to(build).as_posix() if out.is_relative_to(build) else str(out))[1:-1]
            done[ref] = new_ref
        size = ""
        if new_ref is not None and ref in sizes and text[m.end() : m.end() + 1] == ")":
            size = _image_width(*sizes[ref])
        if new_ref == ref and not size:
            return m.group(0)
        changed = True
        if new_ref is None:
            return '_desk_missing_image("__desk_missing__"'
        return f'{m.group(1)}"{new_ref}"{size}'

    new = _IMAGE_CALL.sub(repl, text)
    if changed:
        new = (_MISSING_DEF if "_desk_missing_image(" in new and _MISSING_DEF not in new else "") + new
        main.write_text(new, encoding="utf-8")
    return warnings


def quarantine_images(main: Path, build: Path, font_paths: Sequence[str] = ()) -> list[str]:
    """After an image error, compiles every image of the document on its own and replaces those Typst rejects (a
    malformed SVG, a damaged file) with the placeholder. Returns the refs replaced."""
    text = main.read_text(encoding="utf-8")
    spans = _raw_block_spans(text)
    refs = list(dict.fromkeys(m.group(2) for m in _IMAGE_CALL.finditer(text) if m.group(2) != "__desk_missing__" and not _inside(spans, m.start())))
    bad: list[str] = []
    probe = build / ".desk-image-probe.typ"
    for ref in refs[:400]:
        probe.write_text(f'#image("{ref}")\n', encoding="utf-8")
        try:
            typst_compile_file(probe, build, font_paths)
        except SkillError:
            bad.append(ref)
    probe.unlink(missing_ok=True)
    if bad:
        badset = set(bad)
        new = _IMAGE_CALL.sub(lambda m: '_desk_missing_image("__desk_missing__"' if m.group(2) in badset and not _inside(spans, m.start()) else m.group(0), text)
        if _MISSING_DEF not in new:
            new = _MISSING_DEF + new
        main.write_text(new, encoding="utf-8")
    return bad


TYPST_NOISE = ("no whitespace before raw text", "content labelled multiple times")  # about generated markup, nothing the agent can act on


def typst_warnings(ws: Iterable[Any], user_fonts: Iterable[str] = ()) -> list[str]:
    wanted = {f.lower() for f in user_fonts}
    out = []
    for w in ws:
        msg = str(getattr(w, "message", w)).strip()
        if any(n in msg for n in TYPST_NOISE):
            continue
        m = re.search(r"unknown font family: (.+)", msg)
        if m:
            fam = m.group(1).strip().lower()
            if fam in wanted:
                out.append(f"font '{m.group(1).strip()}' is not installed; used the fallback")
            continue
        msg = msg.splitlines()[0][:300]
        if msg not in out:
            out.append(msg)
    return out


def typst_compile_file(main: Path, root: Path, font_paths: Sequence[str] = (), fmt: str = "pdf", ppi: float = 144.0) -> tuple[list[bytes], list[Any]]:
    import typst

    kwargs: dict[str, Any] = {"format": fmt, "root": str(root)}
    if fmt == "png":
        kwargs["ppi"] = float(ppi)
    if font_paths:
        kwargs["font_paths"] = [str(f) for f in font_paths]
    try:
        result, warnings = typst.compile_with_warnings(str(main), **kwargs)
    except Exception as e:  # noqa: BLE001 — typst.TypstError carries the diagnostics
        msg = str(e).strip()
        hint = "" if main.name != "main.typ" else "\n(keep the generated source with --typ-out to inspect it)"
        raise SkillError(f"Typst could not typeset the document: {msg[:900]}{hint}") from e
    pages = [bytes(result)] if isinstance(result, (bytes, bytearray)) else [bytes(p) for p in result]
    return pages, list(warnings or [])


def reader_filter_args(reader: str) -> list[str]:
    """Filters for a reader's dialect: Sphinx roles and API directives in reStructuredText (desk-sphinx.lua)."""
    if reader.split("+")[0].split("-")[0] == "rst":
        return [f"--lua-filter={TEMPLATES / 'desk-sphinx.lua'}"]
    return []


def xref_args(a: Any, typst: bool = True) -> list[str]:
    """templates/desk-xref.lua: Quarto-style @sec-/@fig-/@tbl- cross-references (before --citeproc sees them)."""
    args = [f"--lua-filter={TEMPLATES / 'desk-xref.lua'}"]
    if typst:
        args += ["-M", "desk-xref-typst=true"]
    if getattr(a, "number_sections", False):
        args += ["-M", "desk-xref-numbered=true"]
    return args


def promote_args(a: Any, style: str) -> list[str]:
    """Report and book: the top heading level becomes chapters (see desk-typst.lua), unless the user shifts levels."""
    if style in ("report", "book") and not getattr(a, "shift_heading_level_by", None):
        return ["-M", "desk-promote=true"]
    return []


def typst_filter_args(a: Any, safe: bool = False) -> list[str]:
    """Desk's Lua filter for Typst output (templates/desk-typst.lua): drops empty and dangling links, duplicate labels
    and bibliography-less #cite calls; with safe, math and raw Typst become literal text (the retry after a failure)."""
    args = [f"--lua-filter={TEMPLATES / 'desk-typst.lua'}"]
    if getattr(a, "bibliography", None) or getattr(a, "csl", None):
        args += ["-M", "desk-citeproc=true"]
    if safe:
        args += ["-M", "desk-safe=true"]
    return args


def main_content_inputs(inputs: Sequence[Path], reader: str, a: Any) -> tuple[list[Path], str]:
    """With --main, HTML inputs become Markdown files of their main content (html_extract.py's article, with title,
    byline, date and absolute URLs); relative images still resolve from the page's folder (added to --resource-path)."""
    if not getattr(a, "main", False) or reader.split("+")[0] != "html":
        return list(inputs), reader
    import atexit

    from _html import date_text, extract_article

    from _inputs import register_work_dir

    work = register_work_dir(Path(tempfile.mkdtemp(prefix="desk-main-")))
    atexit.register(shutil.rmtree, work, True)
    out: list[Path] = []
    extra = list(getattr(a, "resource_path", None) or [])
    for k, src in enumerate(inputs):
        art = extract_article(src)
        meta = {"title": art.get("title"), "author": art.get("byline"), "date": date_text(art)}
        front = ["---"] + [f"{k2}: {json.dumps(v, ensure_ascii=False)}" for k2, v in meta.items() if v] + ["---", ""] if any(meta.values()) else []
        body = art["markdown"] + (f"\n\nSource: <{art['url']}>\n" if art.get("url") else "")
        dest = work / f"{k}-{src.stem}.md"
        dest.write_text("\n".join(front) + body, encoding="utf-8")
        out.append(dest)
        extra.append(str(src.resolve().parent))
    a.resource_path = extra
    return out, "markdown"


def build_typst_source(inputs: Sequence[Path], reader: str, a: Any, build: Path, stdin_text: str | None = None, safe: bool = False) -> tuple[Path, list[str], list[str]]:
    """pandoc → Typst with Desk's template into `build`. Returns (main .typ, warnings, font paths)."""
    shutil.copy(TEMPLATES / "markup.typ", build / "markup.typ")
    style, custom = style_of(a)
    template = custom or (TEMPLATES / "pandoc.typ")
    if custom is not None:
        # A custom template may import markup.typ or its own files next to it.
        for f in custom.parent.glob("*.typ"):
            if f != custom and not (build / f.name).exists():
                shutil.copy(f, build / f.name)
    font_paths: list[str] = list(a.font_path or [])
    from _inputs import jail_roots, prepare
    from _pandoc_safe import jail_args

    pre_warnings: list[str] = []
    roots = jail_roots(inputs, a.resource_path)
    paths, read_as = prepare(inputs, reader, pre_warnings, roots, stdin_text)
    args = [str(i.resolve()) for i in paths] or ["-"]
    # Desk's jail filter first: pictures, covers and bibliographies only from the document's folder (see _pandoc_safe).
    trusted = [Path(x).expanduser() for x in [*(a.bibliography or []), *([a.csl] if a.csl else [])]]
    args += jail_args(build / "jail", [*roots, build], trusted=trusted, citeproc=bool(a.bibliography or a.csl))
    from _remote import filter_args

    remote_args, remote_warnings = filter_args(inputs, reader, build / "remote", getattr(a, "offline", False), stdin_text, prefix="remote")
    pre_warnings += remote_warnings
    args += remote_args
    args += [
        "-f", "json" if read_as == "json" else reader_spec(reader, citations=bool(a.bibliography) or has_xrefs(inputs, stdin_text)),
        "-t", "typst",
        "--standalone",
        f"--template={template}",
        "--wrap=preserve",
        "--extract-media=media",
        f"--resource-path={resource_path(inputs, a.resource_path)}",
        "-o", "main.typ",
    ]
    if a.shift_heading_level_by:
        args += [f"--shift-heading-level-by={a.shift_heading_level_by}"]
    args += reader_filter_args(reader) + metadata_args(a) + xref_args(a) + citation_args(a) + typst_filter_args(a, safe) + promote_args(a, style) + typst_vars(a, style, font_paths) + extra_args(a)
    cover = getattr(a, "cover", None)
    if cover:
        cp = Path(cover).expanduser()
        if not cp.is_file():
            raise SkillError(f"cover image {cp} does not exist")
        shutil.copy(cp, build / ("cover" + cp.suffix.lower()))
        args += ["-V", f"desk-cover=cover{cp.suffix.lower()}"]
    _, warnings = pandoc(args, cwd=build, input=stdin_text.encode("utf-8") if stdin_text is not None else None, verbose=True)
    warnings = pre_warnings + warnings
    main = build / "main.typ"
    if not main.exists():
        raise SkillError("pandoc produced no Typst output")
    warnings += fix_typst_images(main, build)
    return main, warnings, font_paths


def pdf_params(a: Any, reader: str, inputs: Sequence[Path]) -> dict[str, Any]:
    """Everything that changes the PDF, for the cache key (other inputs and side files by content)."""
    keys = ("toc", "toc_depth", "lof", "lot", "number_sections", "metadata", "title", "author", "date", "lang", "shift_heading_level_by", "template", "paper", "margin", "columns", "font", "heading_font", "code_font", "font_size", "line_spacing", "header", "footer", "accent", "no_justify", "resource_path", "font_path", "offline")
    params: dict[str, Any] = {k: getattr(a, k, None) for k in keys}
    params["reader"] = reader
    params["extra"] = extra_key(a)
    from _inputs import work_dirs

    derived = [d.resolve() for d in work_dirs()]
    params["resource_path"] = [x for x in (params["resource_path"] or []) if not any(Path(x).expanduser().resolve().is_relative_to(d) for d in derived)]
    params["origin"] = getattr(a, "_origin_fp", None)  # an excerpt: the document it comes from (its pictures)
    from _bigdoc import PART_CHARS, PARTS_FROM_CHARS

    params["parts"] = [PART_CHARS, PARTS_FROM_CHARS]
    from _cache import fingerprint

    side = [*inputs[1:]]
    for k in ("bibliography",):
        side += [Path(x).expanduser() for x in (getattr(a, k, None) or [])]
    for k in ("csl", "cover", "metadata_file"):
        v = getattr(a, k, None)
        if v:
            side.append(Path(v).expanduser())
    _, custom = style_of(a)
    if custom:
        side.append(custom)
    params["side"] = [fingerprint(p) if p.is_file() else str(p) for p in side]
    params["templates"] = hashlib.sha256(b"".join((TEMPLATES / n).read_bytes() for n in ("markup.typ", "pandoc.typ", "desk-typst.lua", "desk-remote.lua")) + (SKILL_DIR / "scripts" / "_pandoc_safe.py").read_bytes()).hexdigest()[:12]
    return params


def resources_manifest(log: str, dirs: Sequence[Path]) -> list[list[Any]]:
    """Local files pandoc loaded besides the inputs (from its --verbose "Loaded X from Y" lines) and AsciiDoc
    includes, as (path, size, mtime), so a changed picture or include invalidates a cached result. Files in temp
    folders Desk made (downloads, prepared copies, the build: dirs[0]) are left out: they are derived, and gone
    afterwards."""
    from _inputs import included_files, work_dirs

    found: list[list[Any]] = []
    seen: set[str] = set()
    derived = [d.resolve() for d in [*dirs[:1], *work_dirs()]]

    def add(c: Path) -> bool:
        try:
            if not c.is_file():
                return False
            r = c.resolve()
            key = str(r)
            if key not in seen and not any(r.is_relative_to(d) for d in derived):
                seen.add(key)
                st = r.stat()
                found.append([key, st.st_size, st.st_mtime_ns])
            return True
        except OSError:
            return False

    for m in re.finditer(r"^\[INFO\] Loaded (.+?) from (.+?)\s*$", log, re.M):
        src = m.group(2).strip()
        if re.match(r"[a-z][a-z0-9+.-]*://", src, re.I):
            continue
        for c in [Path(src)] if Path(src).is_absolute() else [d / src for d in dirs]:
            if add(c):
                break
    for f in included_files():
        add(f)
    return found


def manifest_ok(entry: Path) -> bool:
    mf = entry / "resources.json"
    if not mf.exists():
        return True
    try:
        for path, size, mtime in json.loads(mf.read_text(encoding="utf-8")):
            st = Path(path).stat()
            if st.st_size != size or st.st_mtime_ns != mtime:
                return False
    except (OSError, ValueError):
        return False
    return True


def cache_wanted(inputs: Sequence[Path], a: Any) -> bool:
    from _cache import enabled

    if getattr(a, "no_cache", False) or not enabled() or not inputs:
        return False
    return sum(i.stat().st_size for i in inputs) >= CACHE_MIN_BYTES


def recover_compile(first: SkillError, main: Path, build: Path, font_paths: list[str], warnings: list[str], rebuild_safe: Any, typ_out: Path | None) -> tuple[list[bytes], list[Any]]:
    """Second chances after a failed compile: images Typst rejects become placeholders; then the document is converted
    again with math and raw Typst as literal text. Raises the first error when nothing helps."""
    if typ_out is not None:
        save_typ_source(build, main, typ_out)  # what failed, to inspect
    reason = str(first).replace("Typst could not typeset the document: ", "").split("\n")[0][:200]
    if re.search(r"image|svg|decode|png|jpe?g|gif", reason, re.I):
        bad = quarantine_images(main, build, font_paths)
        if bad:
            try:
                pages, tw = typst_compile_file(main, build, font_paths)
                warnings.append(f"{len(bad)} image(s) Typst could not decode were left out: " + ", ".join(bad[:5]) + ("…" if len(bad) > 5 else ""))
                return pages, tw
            except SkillError:
                pass
    main2, warnings2, fp2 = rebuild_safe()  # images already fixed by build_typst_source
    warns: list[str] = []
    try:
        pages, tw = typst_compile_file(main2, build, fp2)
    except SkillError:
        bad = quarantine_images(main2, build, fp2)
        if not bad:
            raise first from None
        try:
            pages, tw = typst_compile_file(main2, build, fp2)
        except SkillError:
            raise first from None
        warns.append(f"{len(bad)} image(s) Typst could not decode were left out")
    warnings[:] = warnings2 + warns
    warnings.append(f"Typst could not typeset the document as converted ({reason}); math and raw Typst are shown as source text")
    return pages, tw


def make_pdf(inputs: Sequence[Path], reader: str, a: Any, stdin_text: str | None = None, typ_out: Path | None = None) -> dict[str, Any]:
    """Converts inputs to PDF through Typst (or compiles a .typ file). Returns {pdf: bytes, warnings, cached, pages}.

    Big inputs are cached by content and options, so a second render or conversion is instant.
    """
    if reader == "typst" and len(inputs) == 1 and not typ_out and not getattr(a, "_convert_typst", False):
        return compile_typst_input(inputs[0], a)

    def build_into(dest: Path) -> dict[str, Any]:
        from _bigdoc import parts_log, typeset_parts, wants_parts

        big = wants_parts(inputs, reader, a) if stdin_text is None else None  # too long for one pandoc run

        def source(build: Path, safe: bool = False) -> tuple[Path, list[str], list[str]]:
            if big is not None:
                return typeset_parts(big, a, build, safe=safe)
            return build_typst_source(inputs, reader, a, build, stdin_text, safe=safe)

        with tempfile.TemporaryDirectory(prefix="desk-mk-") as tmp:
            build = Path(tmp)
            main, warnings, font_paths = source(build)
            try:
                pages, tw = typst_compile_file(main, build, font_paths)
            except SkillError as first:
                pages, tw = recover_compile(first, main, build, font_paths, warnings, lambda: source(build, safe=True), typ_out)
            warnings += typst_warnings(tw, [x for x in (a.font, a.heading_font, a.code_font) if x])
            (dest / "out.pdf").write_bytes(pages[0])
            if typ_out is not None:
                save_typ_source(build, main, typ_out)
            res = resources_manifest(parts_log() if big is not None else last_log(), [build, *[i.resolve().parent for i in inputs], *[Path(x).expanduser().resolve() for x in (a.resource_path or [])]])
            (dest / "resources.json").write_text(json.dumps(res), encoding="utf-8")
            info = {"warnings": warnings}
            (dest / "info.json").write_text(json.dumps(info), encoding="utf-8")
            return info

    if typ_out is None and cache_wanted(inputs, a):
        from _cache import cached_dir, lookup

        params = pdf_params(a, reader, inputs)
        hit = lookup(inputs[0], "mk-pdf", params, CACHE_VERSION)
        if hit is not None and not manifest_ok(hit):
            shutil.rmtree(hit, ignore_errors=True)
            hit = None
        if hit is not None:
            info = json.loads((hit / "info.json").read_text(encoding="utf-8"))
            return {"pdf": (hit / "out.pdf").read_bytes(), "warnings": info.get("warnings", []), "cached": True, "cache_entry": str(hit)}
        d = cached_dir(inputs[0], "mk-pdf", params, CACHE_VERSION, lambda tmp: build_into(tmp) and None)
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
        data = (d / "out.pdf").read_bytes()
        from _cache import release

        release(d)
        return {"pdf": data, "warnings": info.get("warnings", []), "cached": False}
    with tempfile.TemporaryDirectory(prefix="desk-mk-out-") as tmp:
        info = build_into(Path(tmp))
        return {"pdf": (Path(tmp) / "out.pdf").read_bytes(), "warnings": info["warnings"], "cached": False}


def compile_typst_input(src: Path, a: Any) -> dict[str, Any]:
    """A .typ file compiled as is (its folder is the root, so its own imports and images work)."""
    font_paths = list(getattr(a, "font_path", None) or [])
    root = src.resolve().parent

    def compute(dest: Path) -> None:
        pages, tw = typst_compile_file(src.resolve(), root, font_paths)
        (dest / "out.pdf").write_bytes(pages[0])
        (dest / "info.json").write_text(json.dumps({"warnings": typst_warnings(tw)}), encoding="utf-8")
        (dest / "resources.json").write_text(json.dumps(folder_manifest(root, src)), encoding="utf-8")

    from _cache import cached_dir, lookup, release

    if cache_wanted([src], a) and len(folder_manifest(root, src)) < 500:
        params = {"fonts": font_paths}
        hit = lookup(src, "mk-typst", params, CACHE_VERSION)
        if hit is not None and not manifest_ok(hit):
            shutil.rmtree(hit, ignore_errors=True)
        d = cached_dir(src, "mk-typst", params, CACHE_VERSION, compute)
        cached = hit is not None and d == hit
    else:
        d = Path(tempfile.mkdtemp(prefix="desk-mk-typ-"))
        compute(d)
        cached = False
    info = json.loads((d / "info.json").read_text(encoding="utf-8"))
    data = (d / "out.pdf").read_bytes()
    release(d)
    return {"pdf": data, "warnings": info["warnings"], "cached": cached}


def folder_manifest(root: Path, main: Path) -> list[list[Any]]:
    """(path, size, mtime) of the files a .typ document may use: everything under its folder (up to 500)."""
    out: list[list[Any]] = []
    try:
        for f in root.rglob("*"):
            if f.is_file() and f.resolve() != main.resolve() and not f.name.startswith("."):
                st = f.stat()
                out.append([str(f), st.st_size, st.st_mtime_ns])
                if len(out) >= 500:
                    break
    except OSError:
        pass
    return out


def save_typ_source(build: Path, main: Path, dest: Path) -> None:
    """Keeps the generated Typst: dest.typ plus markup.typ and media/ next to it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(main, dest)
    if not (dest.parent / "markup.typ").exists():
        shutil.copy(build / "markup.typ", dest.parent / "markup.typ")
    for sub in ("media",):
        s = build / sub
        if s.exists():
            shutil.copytree(s, dest.parent / sub, dirs_exist_ok=True)
    for f in build.glob("cover.*"):
        shutil.copy(f, dest.parent / f.name)


def pdf_outline(pdf: Any, max_depth: int = 10) -> list[tuple[int, str, int | None, float | None]]:
    """(level from 0, title, page index or None, distance from the page top as a fraction or None) for every bookmark
    of an open pypdfium2 document, across pypdfium2 versions (5.x yields PdfBookmark objects with getters; older
    ones had title/page_index attributes)."""
    out: list[tuple[int, str, int | None, float | None]] = []
    for bm in pdf.get_toc(max_depth=max_depth):
        top = None
        if hasattr(bm, "get_title"):
            title = bm.get_title() or ""
            dest = bm.get_dest()
            index = dest.get_index() if dest is not None else None
            if dest is not None and index is not None:
                try:
                    mode, pos = dest.get_view()
                    if mode == 1 and len(pos) >= 2:  # XYZ: left, top, zoom (PDF points from the bottom)
                        h = pdf[index].get_height()
                        top = max(0.0, min(1.0, (h - pos[1]) / h)) if h else None
                except Exception:  # noqa: BLE001 — the position is only a refinement
                    top = None
        else:
            title, index = getattr(bm, "title", "") or "", getattr(bm, "page_index", None)
        out.append((int(getattr(bm, "level", 0)), title, index, top))
    return out


def pdf_page_count_bytes(data: bytes) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        return len(doc)
    finally:
        doc.close()


# ── Markdown outlines, sections and budgets ─────────────────────────────

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")


def headings(lines: Sequence[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) of ATX and setext headings outside code fences and front matter."""
    out: list[tuple[int, int, str]] = []
    fence: str | None = None
    start = 0
    if lines and lines[0].strip() in ("---", "+++"):
        closer = lines[0].strip()
        for j in range(1, min(len(lines), 2000)):
            if lines[j].strip() in (closer, "...") :
                start = j + 1
                break
    prev_text = False
    for i in range(start, len(lines)):
        ln = lines[i]
        m = _FENCE.match(ln)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not ln.strip()[len(m.group(1)) :].strip():
                fence = None
            prev_text = False
            continue
        if m:
            fence = m.group(1)
            prev_text = False
            continue
        if not ln.strip():
            prev_text = False
            continue
        if ln[0] == "#" or (ln[:4].strip()[:1] == "#"):
            h = _ATX.match(ln)
            if h:
                text = (h.group(2) or "").strip()
                text = re.sub(r"\s*\{\s*[#.-][^}]*\}\s*$", "", text)
                out.append((i, len(h.group(1)), text))
                prev_text = False
                continue
        if prev_text and (ln[:1] in "=-" or ln[:4].strip()[:1] in "=-"):
            s = _SETEXT.match(ln)
            if s and i > 0 and not lines[i - 1].lstrip().startswith(("-", "*", "+", ">", "|")):
                out.append((i - 1, 1 if s.group(1)[0] == "=" else 2, lines[i - 1].strip()))
                prev_text = False
                continue
        prev_text = not ln.lstrip().startswith(("|", ">", "<"))
    return out


def outline(text: str) -> list[dict[str, Any]]:
    """Sections of a Markdown text with stable addresses (1, 1.2, 1.2.3 by position), line spans and sizes."""
    lines = text.split("\n")
    hs = headings(lines)
    secs: list[dict[str, Any]] = []
    # Line offsets for fast char counts.
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln) + 1)
    if hs and hs[0][0] > 0 and text[: offs[hs[0][0]]].strip():
        secs.append({"address": "0", "level": 0, "title": "(before the first heading)", "line": 1, "_i": 0})
    elif not hs:
        return [{"address": "0", "level": 0, "title": "(no headings)", "line": 1, "end_line": len(lines), "chars": len(text), "words": _words(text), "own_chars": len(text)}]
    stack: list[tuple[int, int]] = []  # (level, count)
    counters: list[int] = []
    for i, level, title in hs:
        while stack and stack[-1][0] >= level:
            if stack[-1][0] == level:
                break
            stack.pop()
            counters.pop()
        if stack and stack[-1][0] == level:
            counters[-1] += 1
        else:
            stack.append((level, 0))
            counters.append(1)
        stack[-1] = (level, counters[-1])
        secs.append({"address": ".".join(str(c) for c in counters), "level": len(counters), "md_level": level, "title": _plain_heading(title), "line": i + 1, "_i": i})
    # Spans: a section runs to the next heading of the same or a higher level (subsections included).
    n = len(lines)
    # Next section at the same or a higher level, for every section, in one backwards pass (a stack).
    next_idx: list[int] = [len(secs)] * len(secs)
    stack: list[int] = []
    for k in range(len(secs) - 1, -1, -1):
        lvl = secs[k]["level"]
        while stack and secs[stack[-1]]["level"] > lvl:
            stack.pop()
        next_idx[k] = stack[-1] if stack else len(secs)
        stack.append(k)
    for k, s in enumerate(secs):
        own_end = secs[k + 1]["_i"] if k + 1 < len(secs) else n
        end = own_end if s["level"] == 0 else (secs[next_idx[k]]["_i"] if next_idx[k] < len(secs) else n)
        s["end_line"] = end
        s["chars"] = offs[end] - offs[s["_i"]]
        s["own_chars"] = offs[own_end] - offs[s["_i"]]
        s["words"] = _words("\n".join(lines[s["_i"] : own_end])) if s["own_chars"] < 2_000_000 else s["own_chars"] // 6
    # Words including subsections.
    prefix = [0]
    for s in secs:
        prefix.append(prefix[-1] + s["words"])
    for k, s in enumerate(secs):
        s["total_words"] = s["words"] if s["level"] == 0 else prefix[next_idx[k]] - prefix[k]
    for s in secs:
        s.pop("_i", None)
    return secs


def _plain_heading(t: str) -> str:
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_`]{1,3}", "", t)
    return t.replace("\\", "").strip()


def _words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", text))


def find_section(secs: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    spec = spec.strip()
    for s in secs:
        if s["address"] == spec:
            return s
    low = spec.lower()
    exact = [s for s in secs if s["title"].lower() == low]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [s for s in secs if low in s["title"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise UsageError(f"no section '{spec}': use an address from the outline (--outline) or part of a heading")
    listing = "; ".join(f"{s['address']} {s['title'][:40]}" for s in partial[:8])
    raise UsageError(f"'{spec}' matches {len(partial)} sections ({listing}); pass the address")


def slice_budget(text: str, offset: int, max_chars: int) -> tuple[str, int | None]:
    """text[offset:] cut at a line boundary within max_chars; returns (part, next offset or None)."""
    if offset >= len(text):
        return "", None
    if max_chars <= 0 or len(text) - offset <= max_chars:
        return text[offset:], None
    end = offset + max_chars
    nl = text.rfind("\n", offset + max_chars // 2, end)
    if nl > offset:
        end = nl + 1
    return text[offset:end], end


def continuation(script: str, next_offset: int) -> str:
    """The command that continues a cut output: this run's arguments with --offset NEXT."""
    args = sys.argv[1:]
    out: list[str] = []
    skip = False
    for x in args:
        if skip:
            skip = False
            continue
        if x == "--offset":
            skip = True
            continue
        if x.startswith("--offset="):
            continue
        out.append(x)
    return f"python3 scripts/{script} " + " ".join(shell_quote(x) for x in out + ["--offset", str(next_offset)])


def budget_cut(text: str, offset: int, max_chars: int, script: str) -> tuple[str, str | None, int | None]:
    """(part, note with the continuation command or None, next offset or None)."""
    part, nxt = slice_budget(text, offset, max_chars)
    if offset and not part:
        raise UsageError(f"--offset {offset} is past the end ({len(text)} characters)")
    if nxt is None:
        return part, None, None
    return part, f"[… cut at character {nxt:,} of {len(text):,}. Continue: {continuation(script, nxt)}]", nxt


def shell_quote(s: str) -> str:
    if re.fullmatch(r"[\w@%+=:,./-]+", s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def command(script: str, *args: str) -> str:
    return "python3 scripts/" + script + " " + " ".join(shell_quote(x) for x in args if x is not None)


def find_hits(text: str, pattern: str, secs: list[dict[str, Any]] | None, regex: bool = False, case: bool = False, context: int = 80, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
    """Matches with line numbers, section addresses and context; returns (hits, total)."""
    flags = 0 if case else re.I
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise UsageError(f"bad regular expression: {e}") from e
    hits: list[dict[str, Any]] = []
    total = 0
    starts = [s["line"] for s in secs] if secs else []
    import bisect

    line_no = 1
    last = 0
    for m in rx.finditer(text):
        total += 1
        if len(hits) >= limit:
            continue
        line_no += text.count("\n", last, m.start())
        last = m.start()
        a = max(0, m.start() - context)
        b = min(len(text), m.end() + context)
        snippet = text[a:b].replace("\n", " ")
        hit = {"line": line_no, "offset": m.start(), "match": m.group(0)[:200], "context": ("…" if a > 0 else "") + snippet + ("…" if b < len(text) else "")}
        if secs:
            k = bisect.bisect_right(starts, line_no) - 1
            if k >= 0:
                # The innermost section containing the line.
                hit["section"] = secs[k]["address"]
                hit["heading"] = secs[k]["title"]
        hits.append(hit)
    return hits, total


def size_note(n: int) -> str:
    return human_size(n)


def is_big(path: Path) -> bool:
    try:
        return path.stat().st_size >= BIG_BYTES
    except OSError:
        return False


def warning_lines(ws: Sequence[str], limit: int = 12) -> list[str]:
    """`warning: …` lines, at most `limit` (the rest are counted; --format json lists them all)."""
    out = [f"warning: {w}" for w in ws[:limit]]
    if len(ws) > limit:
        out.append(f"warning: … {len(ws) - limit} more (--format json lists every warning)")
    return out


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
