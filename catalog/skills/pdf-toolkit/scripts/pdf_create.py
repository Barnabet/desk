#!/usr/bin/env python3
"""Create a PDF from Markdown (or HTML, reStructuredText, Org, LaTeX, DOCX, ODT, EPUB, notebooks) through the
bundled pandoc and Typst, with Desk's templates: report (title page, optional contents, running header, page
numbers), memo, letter and plain. Also compiles your own .typ files. Math, footnotes, tables, images, code with
syntax highlighting, links and citations (--bibliography) are supported.

Office files (.docx, .odt, .rtf, .doc, .pptx, .xlsx …) are exported with their own layout by LibreOffice when it
is installed (--engine office). Without LibreOffice, .docx, .odt and .rtf go through pandoc and Desk's templates
(the content, not the original layout); the output says which engine it used.

YAML front matter sets title, subtitle, author (a list is fine), date and abstract, and for the other templates:
memo: to, from, cc, subject; letter: sender, recipient (lists give one line each), place, subject, closing,
signature. Command-line flags override front matter.

Examples:
  python3 scripts/pdf_create.py report.md --out report.pdf --toc --number-sections
  python3 scripts/pdf_create.py memo.md --out memo.pdf --template memo --page-size letter
  python3 scripts/pdf_create.py letter.md --out letter.pdf --template letter --font "Georgia" --margin 2.5cm
  python3 scripts/pdf_create.py page.html --out page.pdf --template plain
  python3 scripts/pdf_create.py contract.docx --out contract.pdf      # LibreOffice when installed, else pandoc
  python3 scripts/pdf_create.py notes.docx --out notes.pdf --engine pandoc --template report --toc
  python3 scripts/pdf_create.py poster.typ --out poster.pdf           # compile Typst directly
  python3 scripts/pdf_create.py report.md --out report.pdf --typ-out report.typ   # keep the Typst source to tweak
  python3 scripts/pdf_create.py out.pdf --input report.md --title "Q3"   # older argument order still works
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from _common import SkillError, UsageError, check_zip, human_size, output_dir, output_path, parser, run_main
from _pdfkit import emit, parse_color, render_pages, save_bytes
from _render import announce, find_soffice, typst_compile, typst_string

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
READERS = {
    ".md": "markdown", ".markdown": "markdown", ".txt": "markdown", ".mdown": "markdown", ".mkd": "markdown",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".rst": "rst", ".org": "org", ".tex": "latex", ".latex": "latex",
    ".docx": "docx", ".odt": "odt", ".epub": "epub", ".ipynb": "ipynb", ".textile": "textile",
    ".wiki": "mediawiki", ".adoc": "asciidoc", ".asciidoc": "asciidoc", ".rtf": "rtf", ".fb2": "fb2", ".json": "json",
}
PAPERS = {"a4": "a4", "a3": "a3", "a5": "a5", "a6": "a6", "b5": "iso-b5", "letter": "us-letter", "us-letter": "us-letter", "legal": "us-legal", "us-legal": "us-legal", "executive": "us-executive", "tabloid": "us-tabloid"}
TEMPLATES_ALL = ("report", "memo", "letter", "plain")
#: Files LibreOffice exports with their own layout (pandoc reads only .docx, .odt and .rtf among them).
OFFICE_EXTS = {
    ".docx", ".docm", ".dotx", ".doc", ".dot", ".odt", ".ott", ".rtf", ".wpd", ".wps",
    ".pptx", ".pptm", ".ppsx", ".potx", ".ppt", ".pps", ".odp",
    ".xlsx", ".xlsm", ".xltx", ".xls", ".xlsb", ".ods",
    ".odg", ".vsd", ".vsdx",
}
LAYOUT_FLAGS = ("template", "toc", "number_sections", "page_size", "margin", "font", "font_size", "header", "footer", "accent", "title", "bibliography")


def typst_length(value: str) -> str:
    """A CSS-like length ('2cm', '1in', '72pt', '20mm') as Typst length syntax."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(pt|mm|cm|in)?\s*", value.lower())
    if not m:
        raise UsageError(f"bad length '{value}' (use e.g. 2cm, 1in, 20mm or 72pt)")
    return f"{m.group(1)}{m.group(2) or 'pt'}"


def typst_margin(spec: str) -> str:
    parts = [typst_length(x) for x in re.split(r"[,\s]+", spec.strip()) if x]
    if len(parts) == 1:
        return f"{parts[0]}"
    if len(parts) == 2:
        return f"(y: {parts[0]}, x: {parts[1]})"
    if len(parts) == 4:
        return f"(top: {parts[0]}, right: {parts[1]}, bottom: {parts[2]}, left: {parts[3]})"
    raise UsageError("--margin takes 1, 2 (vertical,horizontal) or 4 (top,right,bottom,left) values")


def font_family(spec: str, font_paths: list[str]) -> str:
    """A family name; a .ttf/.otf path is registered as a font path and its family name returned."""
    p = Path(spec).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.exists():
        font_paths.append(str(p.parent))
        try:
            from PIL import ImageFont

            return ImageFont.truetype(str(p), 12).getname()[0]
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"cannot read the font file {p}: {e}") from e
    return spec


def pandoc_warnings(stderr: bytes) -> list[str]:
    out = []
    for line in stderr.decode("utf-8", "replace").splitlines():
        if line.startswith("[WARNING]"):
            msg = line[len("[WARNING]") :].strip()
            if "Could not fetch resource" in msg:
                msg = re.sub(r":.*", "", msg) + ": the image was replaced by its description"
            m = re.match(r"Could not load include file (.+?)(?: at .*)?$", msg)
            if m:
                msg = f"include {m.group(1)} not read (only includes inside the document's folder are followed)"
            out.append(msg[:200])
    return out


def build_typ_from_pandoc(src: Path | None, text: str | None, fmt: str, build: Path, a: Any) -> tuple[Path, list[str]]:
    """Runs pandoc → Typst with Desk's template into `build`; returns the main .typ and pandoc's warnings.

    pandoc runs with --sandbox and Desk's jail filter (_pandoc_safe.py): includes and pictures come only from the
    input's folder (includes inside it are inlined first), remote pictures are downloaded with time and size limits,
    and anything else the document names is left out with a warning, so a stranger's document cannot pull the
    user's files into the PDF."""
    from _common import run_tool
    from _pandoc_safe import has_includes, inline_includes, jail_args, read_text, resolved
    from _render import pandoc_path

    shutil.copy(TEMPLATES / "desk.typ", build / "desk.typ")
    style = a.template
    roots = resolved([src.resolve().parent if src else Path.cwd()])
    notes: list[str] = []
    source = str(src.resolve()) if src else "-"
    if src is not None and fmt in ("rst", "org", "latex", "typst"):
        raw = read_text(src)
        if has_includes(raw, fmt):
            try:
                raw = inline_includes(raw, fmt, src.resolve().parent, roots, notes)
            except ValueError as e:
                raise SkillError(f"{src.name}: {e}") from None
            inlined = build / ("desk-source" + src.suffix)
            inlined.write_text(raw, encoding="utf-8")
            source = str(inlined)
    trusted = [Path(b).expanduser() for b in [*(a.bibliography or []), *([a.csl] if a.bibliography and a.csl else [])]]
    args = [
        pandoc_path(),
        "--sandbox",
        source,
        *jail_args(build / "jail", roots, trusted=trusted, citeproc=bool(a.bibliography), download=True),
        "-f", fmt + ("+smart" + ("" if a.bibliography else "-citations") if fmt == "markdown" else ""),
        "-t", "typst",
        "--standalone",
        f"--template={TEMPLATES / 'pandoc.typ'}",
        "--wrap=preserve",
        "--extract-media=media",
        f"--resource-path={(src.resolve().parent if src else Path.cwd().resolve())}",
        "-o", "main.typ",
        "-V", f"desk-style={style}",
    ]
    meta = {"title": a.title, "subtitle": a.subtitle, "date": a.date, "subject": a.subject, "to": a.to, "from": a.sender_name}
    for k, v in meta.items():
        if v:
            args += ["-M", f"{k}={v}"]
    for author in a.author or []:
        args += ["-M", f"author={author}"]
    if a.toc:
        args += ["-V", "desk-toc=true", "-V", f"desk-toc-depth={a.toc_depth}"]
    if a.number_sections:
        args += ["-V", "desk-number=true"]
    if a.page_size:
        key = a.page_size.lower()
        if key in PAPERS:
            args += ["-V", f"desk-paper={PAPERS[key]}"]
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(mm|cm|in|pt)?", key)
            if not m:
                raise UsageError(f"unknown --page-size '{a.page_size}' (a4, letter, legal, a5, a3, or WxH like 148x210mm)")
            unit = m.group(3) or "pt"
            args += ["-V", f"desk-page-width={m.group(1)}{unit}", "-V", f"desk-page-height={m.group(2)}{unit}"]
    if a.margin:
        args += ["-V", f"desk-margin={typst_margin(a.margin)}"]
    font_paths: list[str] = list(a.font_path or [])
    if a.font:
        args += ["-V", f"desk-font={typst_string(font_family(a.font, font_paths))}"]
    a._font_paths = font_paths
    if a.font_size:
        args += ["-V", f"desk-fontsize={typst_length(a.font_size)}"]
    if a.lang:
        args += ["-V", f"desk-lang={a.lang}"]
    if a.header:
        args += ["-V", f"desk-header={typst_string(a.header)}"]
    if a.footer:
        args += ["-V", f"desk-footer={typst_string(a.footer)}"]
    if a.accent:
        from _pdfkit import color_hex

        args += ["-V", f"desk-accent={color_hex(parse_color(a.accent))}"]
    if a.no_justify:
        args += ["-V", "desk-nojustify=true"]
    if a.title:
        args += ["-V", f"desk-title-text={typst_string(a.title)}"]
    if a.bibliography:
        for b in a.bibliography:
            args += ["--citeproc", f"--bibliography={Path(b).resolve()}"]
        if a.csl:
            args += [f"--csl={Path(a.csl).resolve()}"]
    r = run_tool(args, cwd=build, input=text.encode("utf-8") if text is not None else None, timeout=300, check=False)
    if r.returncode != 0:
        lines = [ln for ln in r.stderr.decode("utf-8", "replace").splitlines() if ln.strip() and not ln.startswith((" ", "HasCallStack"))]
        raise SkillError("pandoc could not convert the input: " + " ".join(lines[-4:])[:600])
    main = build / "main.typ"
    if not main.exists():
        raise SkillError("pandoc produced no Typst output")
    return main, notes + pandoc_warnings(r.stderr)


def default_style(src: Path | None, text: str | None) -> str:
    """report when the document has a title, otherwise plain."""
    body = text if text is not None else (src.read_text(encoding="utf-8", errors="replace")[:4000] if src and src.suffix.lower() in (".md", ".markdown", ".txt") else "")
    return "report" if re.match(r"\s*---\s*\n(?:.*\n)*?\s*title\s*:", body) else "plain"


def main() -> int:
    p = parser(__doc__.split("\n\n")[0], __doc__.split("\n\n", 1)[1])
    p.add_argument("source", help="input file (.md, .html, .typ, …; - for Markdown on stdin), or the output PDF in the older form with --input")
    p.add_argument("--out", "-o", help="output PDF (default: the input's name with .pdf, in the current folder)")
    p.add_argument("--input", "-i", help="older form: the input file when the first argument is the output")
    p.add_argument("--engine", choices=["auto", "office", "pandoc"], default="auto", help="for Office files (.docx, .odt, .rtf, .doc, .pptx, .xlsx …): office = LibreOffice with the document's own layout; pandoc = the content in Desk's templates; auto (default) = office when LibreOffice is installed")
    p.add_argument("--template", choices=TEMPLATES_ALL, help="report (title page), memo, letter or plain (default: report when the document has a title, else plain)")
    p.add_argument("--from", dest="from_format", help="pandoc input format when the extension is unusual (markdown, html, rst, docx …)")
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--author", action="append", help="author (repeat for several)")
    p.add_argument("--date", help="date text; 'today' for today's date")
    p.add_argument("--subject", help="memo or letter subject")
    p.add_argument("--to", help="memo recipient")
    p.add_argument("--from-name", dest="sender_name", help="memo sender")
    p.add_argument("--toc", action="store_true", help="table of contents")
    p.add_argument("--toc-depth", type=int, default=3)
    p.add_argument("--number-sections", action="store_true", help="numbered headings (1, 1.1, …)")
    p.add_argument("--page-size", help="a4 (default), letter, legal, a5, a3, or WxH like 148x210mm")
    p.add_argument("--margin", help="2.2cm | vertical,horizontal | top,right,bottom,left")
    p.add_argument("--font", help="body font family (installed), or a .ttf/.otf file; falls back to Libertinus Serif")
    p.add_argument("--font-path", action="append", help="folder with extra fonts")
    p.add_argument("--font-size", help="e.g. 11pt")
    p.add_argument("--lang", help="language for hyphenation and quotes, e.g. en, fr, de")
    p.add_argument("--header", help="running header text; {page}, {pages} and {title} are replaced")
    p.add_argument("--footer", help="footer text, e.g. 'Confidential · {page} of {pages}'")
    p.add_argument("--accent", help="accent color for headings and rules (#1f4e79 by default)")
    p.add_argument("--no-justify", action="store_true", help="ragged-right paragraphs")
    p.add_argument("--bibliography", action="append", help="a .bib/.json/.yaml bibliography: [@key] citations become references")
    p.add_argument("--csl", help="citation style (.csl file)")
    p.add_argument("--root", help="for .typ input: the folder Typst may read files from (default: the file's folder)")
    p.add_argument("--typ-out", help="also save the generated Typst source: a .typ path or a folder (with desk.typ and media next to it; edit it and compile it with this script)")
    p.add_argument("--render", metavar="DIR", help="also render the first pages to PNGs in DIR, to check the layout")
    p.add_argument("--force", action="store_true", help="replace an existing output")
    p.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args()

    if a.input:  # older form: pdf_create.py out.pdf --input report.md
        out_arg, src_arg = a.source, a.input
    else:
        src_arg, out_arg = a.source, a.out
    text = None
    src: Path | None = None
    if src_arg == "-":
        text = sys.stdin.read()
    else:
        src = Path(src_arg).expanduser()
        if not src.is_file():
            raise SkillError(f"{src} does not exist")
        check_zip(src)  # .docx, .odt, .epub, .pptx, .xlsx …: refuse a zip bomb before LibreOffice or pandoc unpacks it
    if not out_arg:
        out_arg = (src.stem if src else "document") + ".pdf"
    out = output_path(out_arg, [src] if src else [], a.force)
    if a.date and a.date.lower() == "today":
        today = date.today()
        a.date = f"{today.day} {today.strftime('%B %Y')}"
    warnings: list[str] = []
    notes: list[str] = []
    a._font_paths = list(a.font_path or [])
    suffix = src.suffix.lower() if src else ""
    is_typ = suffix == ".typ"
    engine = "pandoc+typst" if not is_typ else "typst"
    soffice = None
    if suffix in OFFICE_EXTS and a.engine != "pandoc" and not a.from_format:
        soffice = find_soffice()
        if soffice is None:
            if a.engine == "office":
                raise SkillError("LibreOffice is not installed (set DESK_SOFFICE to its soffice path if it is); --engine pandoc converts the content with Desk's templates instead")
            if suffix not in READERS:
                raise SkillError(
                    f"a {suffix} file needs LibreOffice to become a PDF, and LibreOffice is not installed. Install it, or use the "
                    + ("presentations skill (pptx_convert.py or pptx_render.py)" if suffix in (".pptx", ".pptm", ".ppsx", ".potx", ".ppt", ".pps", ".odp") else "spreadsheets skill (sheet_convert.py or sheet_render.py)" if suffix in (".xlsx", ".xlsm", ".xltx", ".xls", ".xlsb", ".ods") else "word-documents skill (docx_convert.py)")
                )
            notes.append("LibreOffice is not installed, so the content went through pandoc and Desk's template: the original page layout is not kept (the word-documents skill's docx_convert.py keeps it closer)")
    elif a.engine == "office":
        raise UsageError("--engine office is for Office files (.docx, .odt, .rtf, .doc, .pptx, .xlsx …)")
    if a.render:
        render_dir = output_dir(a.render)
        clash = sorted(render_dir.glob(f"{out.stem}-page-*.png"))
        if clash and not a.force:
            raise SkillError(f"{clash[0]} already exists; pass --force or choose another --render folder")
    if soffice is not None:
        assert src is not None
        from _render import office_convert

        ignored = [f"--{f.replace('_', '-')}" for f in LAYOUT_FLAGS if getattr(a, f, None)]
        if ignored:
            notes.append(f"{', '.join(ignored)} {'does' if len(ignored) == 1 else 'do'} not apply to a LibreOffice export (it keeps the document's own layout); use --engine pandoc for Desk's templates")
        if a.typ_out:
            raise UsageError("--typ-out needs --engine pandoc (LibreOffice exports the PDF directly)")
        pdf = office_convert(src, "pdf", soffice=soffice).read_bytes()
        engine = "libreoffice"
    else:
        if not is_typ and not a.template:
            a.template = default_style(src, text)
        pdf = compile_with_typst(a, src, text, is_typ, warnings)
    save_bytes(pdf, out)
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(out))
    pages = len(doc)
    doc.close()
    template = None if engine != "pandoc+typst" else a.template
    info: dict[str, Any] = {"output": str(out), "pages": pages, "bytes": len(pdf), "engine": engine, "template": template}
    if warnings:
        info["warnings"] = warnings
    if notes:
        info["notes"] = notes
    if a.typ_out:
        info["typst_source"] = a.typ_out
    if a.render:
        jobs = [{"pdf": str(out), "index": i, "dpi": None, "max_edge": 1568, "out": str(render_dir / f"{out.stem}-page-{i + 1:02d}.png")} for i in range(min(pages, 8))]
        info["rendered"] = [r["path"] for r in render_pages(jobs)]
    if a.format == "json":
        emit(info, "json")
        return 0
    how = {"libreoffice": "exported by LibreOffice", "typst": "compiled with Typst"}.get(engine, f"template {template}")
    print(f"wrote {out}: {pages} page{'s' if pages != 1 else ''}, {human_size(len(pdf))}, {how}")
    for w in warnings:
        print(f"warning: {w}")
    for n in notes:
        print(f"note: {n}")
    if info.get("rendered"):
        announce(info["rendered"], "Rendered the first pages." if pages > 8 else None)
    return 0


def compile_with_typst(a: Any, src: Path | None, text: str | None, is_typ: bool, warnings: list[str]) -> bytes:
    """The PDF bytes of a .typ file, or of any pandoc input laid out with Desk's templates."""
    with tempfile.TemporaryDirectory(prefix="desk-pdf-create-") as tmp:
        build = Path(tmp)
        if is_typ:
            assert src is not None
            root = Path(a.root).resolve() if a.root else src.resolve().parent
            main_typ = src.resolve()
            if not main_typ.is_relative_to(root):
                raise UsageError("--root must contain the .typ file")
            if a.font and Path(a.font).exists():
                a._font_paths.append(str(Path(a.font).parent))
        else:
            fmt = a.from_format or (READERS.get(src.suffix.lower()) if src else "markdown")
            if not fmt:
                raise UsageError(f"unknown input type {src.suffix if src else ''}: pass --from (markdown, html, rst, docx, …)")
            main_typ, found = build_typ_from_pandoc(src, text, fmt, build, a)
            warnings.extend(found)
            root = build
        if a.typ_out:
            if is_typ:
                raise UsageError("--typ-out is for converted inputs; the input is already Typst")
            want = Path(a.typ_out)
            if want.is_dir() or want.suffix.lower() != ".typ":
                want = output_dir(want) / f"{src.stem if src else 'document'}.typ"
            dest = output_path(want, [src] if src else [], a.force)
            a.typ_out = str(dest)
            shutil.copy(main_typ, dest)
            desk_copy = dest.parent / "desk.typ"
            if not desk_copy.exists():
                shutil.copy(TEMPLATES / "desk.typ", desk_copy)
            media = build / "media"
            if media.exists() and not (dest.parent / "media").exists():
                shutil.copytree(media, dest.parent / "media")
        return typst_compile(main_typ, fmt="pdf", root=root, font_paths=a._font_paths)[0]


if __name__ == "__main__":
    run_main(main)
